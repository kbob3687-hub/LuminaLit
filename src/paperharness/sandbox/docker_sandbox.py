"""Docker implementation of SandboxSession.

契约见 docs/interface-contract.md 第 1 节：命名、label、资源限制、错误语义均已写死，
改动需两人 review。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from paperharness.contracts.run import ExecResult, RunRequest
from paperharness.sandbox.base import SandboxClosedError, SandboxUnavailableError

TAIL_LIMIT = 2000
TAIL_BYTES = 16384  # 尾部滚动窗口字节数，覆盖 2000 字符的最坏 UTF-8 编码
MAX_LOG_BYTES = 50 * 1024 * 1024  # 单条流上限：超出截断并在日志文件里打标
CONTAINER_USER = "1000:1000"
WORKSPACE_MOUNT = "/workspace"
MEMORY_LIMIT = "2g"
NANO_CPUS = 1_500_000_000
PIDS_LIMIT = 128
DEFAULT_PYTHON_VERSION = "3.10"


def _load_docker_module() -> Any:
    try:
        import docker
    except ImportError as exc:  # pragma: no cover - environment specific
        raise SandboxUnavailableError(
            "docker SDK not installed; run pip install 'paperharness[docker]'"
        ) from exc
    return docker


def create_docker_sandbox(request: RunRequest, run_dir: Path) -> DockerSandboxSession:
    """SandboxFactory 实现：Harness 只通过工厂拿 session，不直接 new。"""
    python_version = request.python_version or DEFAULT_PYTHON_VERSION
    return DockerSandboxSession(run_dir=run_dir, python_version=python_version)


class DockerSandboxSession:
    def __init__(self, run_dir: Path, python_version: str, client: Any | None = None):
        self.run_dir = run_dir
        self.run_id = run_dir.name
        self.workspace = run_dir / "workspace"
        self.logs_dir = run_dir / "logs"
        self._environment: dict[str, str] = {}
        self._exec_seq = 0
        self._network_enabled = True
        self._closed = False
        # close() 清理失败不抛异常，但必须记录在这里，由 Harness 写事件和清单。
        self.cleanup_errors: list[str] = []

        docker = _load_docker_module()
        self._docker = docker
        self._client = client if client is not None else docker.from_env()
        self._container_name = f"paperharness-{self.run_id}"
        self._network_name = f"paperharness-net-{self.run_id}"
        self._labels = {
            "paperharness.run_id": self.run_id,
            "paperharness.role": "sandbox",
        }
        self._image = f"paperharness/base-py{python_version}"

        self.workspace.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        # 构造是两步（先建网络、再建容器）。第二步失败时对象还没构造完，
        # close() 永远不会被调用，已经建好的资源必须在这里就地回收
        # （契约 §1.2：异常路径也要清理）。
        self._network: Any | None = None
        self._container: Any | None = None
        try:
            self._network = self._create_network()
            self._container = self._create_container()
        except BaseException:
            self._cleanup_partial_setup()
            raise

    # ------------------------------------------------------------------ setup

    def _create_network(self) -> Any:
        try:
            self._remove_stale_containers()
            self._remove_stale_network()
            return self._client.networks.create(
                self._network_name, driver="bridge", labels=self._labels
            )
        except self._docker.errors.DockerException as exc:
            raise SandboxUnavailableError(f"network setup failed: {exc}") from exc

    def _remove_stale_containers(self) -> None:
        stale = self._client.containers.list(
            all=True, filters={"label": f"paperharness.run_id={self.run_id}"}
        )
        for container in stale:
            container.remove(force=True)

    def _remove_stale_network(self) -> None:
        try:
            self._client.networks.get(self._network_name).remove()
        except self._docker.errors.NotFound:
            pass

    def _create_container(self) -> Any:
        try:
            self._client.images.get(self._image)
        except self._docker.errors.NotFound as exc:
            raise SandboxUnavailableError(
                f"image {self._image} not found; pull or build it before running"
            ) from exc
        except self._docker.errors.DockerException as exc:
            raise SandboxUnavailableError(f"docker engine unavailable: {exc}") from exc
        container: Any | None = None
        try:
            container = self._client.containers.run(
                self._image,
                name=self._container_name,
                command=["sleep", "infinity"],
                detach=True,
                user=CONTAINER_USER,
                working_dir=WORKSPACE_MOUNT,
                mem_limit=MEMORY_LIMIT,
                memswap_limit=MEMORY_LIMIT,
                nano_cpus=NANO_CPUS,
                pids_limit=PIDS_LIMIT,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                volumes={
                    str(self.workspace.resolve()): {
                        "bind": WORKSPACE_MOUNT,
                        "mode": "rw",
                    }
                },
                network=self._network.name,
                labels=self._labels,
            )
            container.reload()
            if container.status != "running":
                raise SandboxUnavailableError(
                    f"container {self._container_name} did not stay running"
                )
            return container
        except SandboxUnavailableError:
            # 容器已建但没跑住（状态不是 running）：不回收就会留下同 label 的残留。
            self._remove_container_quietly(container)
            raise
        except self._docker.errors.DockerException as exc:
            self._remove_container_quietly(container)
            raise SandboxUnavailableError(f"container creation failed: {exc}") from exc

    def _remove_container_quietly(self, container: Any | None) -> None:
        """构造期回收：尽力而为，失败只记录，绝不盖掉原始异常。"""
        if container is None:
            return
        try:
            container.remove(force=True)
        except self._docker.errors.NotFound:
            pass
        except self._docker.errors.DockerException as exc:
            self._record_cleanup_failure(f"container remove failed during setup: {exc}")

    def _cleanup_partial_setup(self) -> None:
        """构造期失败时的回收顺序：先容器、后网络（网络还挂着容器时删不掉）。"""
        self._remove_container_quietly(self._container)
        if self._network is None:
            return
        try:
            self._network.remove()
        except self._docker.errors.NotFound:
            pass
        except self._docker.errors.DockerException as exc:
            self._record_cleanup_failure(f"network remove failed during setup: {exc}")

    # --------------------------------------------------------------- protocol

    def execute(self, command: list[str], timeout_seconds: int) -> ExecResult:
        if self._closed:
            raise SandboxClosedError("session is closed")
        started = time.monotonic()
        self._exec_seq += 1
        seq = f"{self._exec_seq:03d}"
        out_path = self.logs_dir / f"exec-{seq}.out"
        err_path = self.logs_dir / f"exec-{seq}.err"
        stdout_path = f"logs/{out_path.name}"
        stderr_path = f"logs/{err_path.name}"
        # 先建空文件：超时提前返回时日志路径也一定存在（契约：完整输出只进日志文件）
        out_path.touch()
        err_path.touch()
        holder: dict[str, Any] = {}

        def _run() -> None:
            try:
                self._exec_streaming(command, out_path, err_path, holder)
            except self._docker.errors.DockerException as exc:
                holder["error"] = exc

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        thread.join(timeout_seconds)
        duration_ms = int((time.monotonic() - started) * 1000)

        if thread.is_alive():
            self._kill_quietly()
            self._closed = True
            return ExecResult(
                exit_code=None,
                timed_out=True,
                duration_ms=duration_ms,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
            )
        if "error" in holder:
            raise SandboxUnavailableError(f"exec failed: {holder['error']}")

        return ExecResult(
            exit_code=holder.get("exit_code"),
            timed_out=False,
            duration_ms=duration_ms,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            stdout_tail=holder.get("stdout_tail", ""),
            stderr_tail=holder.get("stderr_tail", ""),
        )

    def _exec_streaming(
        self,
        command: list[str],
        out_path: Path,
        err_path: Path,
        holder: dict[str, Any],
    ) -> None:
        """流式执行：输出边读边写日志文件，不整段进内存。

        单条流超过 MAX_LOG_BYTES 后停止写文件（继续排空管道避免 exec 卡死），
        并在日志里打截断标记；内存只保留尾部滚动窗口。
        """
        created = self._client.api.exec_create(
            container=self._container.id,
            cmd=list(command),
            stdout=True,
            stderr=True,
            user=CONTAINER_USER,
            environment=dict(self._environment),
            workdir=WORKSPACE_MOUNT,
        )
        exec_id = created["Id"]
        written = {"stdout": 0, "stderr": 0}
        truncated = {"stdout": False, "stderr": False}
        tails = {"stdout": b"", "stderr": b""}
        handles = {
            "stdout": out_path.open("ab"),
            "stderr": err_path.open("ab"),
        }
        try:
            stream = self._client.api.exec_start(exec_id, stream=True, demux=True)
            for chunk in stream:
                if not isinstance(chunk, tuple) or len(chunk) != 2:
                    continue
                for name, data in zip(("stdout", "stderr"), chunk):
                    if not data:
                        continue
                    written[name] += len(data)
                    if written[name] > MAX_LOG_BYTES:
                        if not truncated[name]:
                            truncated[name] = True
                            handles[name].write(
                                f"\n[paperharness] {name} truncated at "
                                f"{MAX_LOG_BYTES} bytes\n".encode()
                            )
                        continue  # 丢弃超额字节，但继续读，防止管道写满阻塞 exec
                    handles[name].write(data)
                    tails[name] = (tails[name] + data)[-TAIL_BYTES:]
        finally:
            for handle in handles.values():
                handle.close()
        inspect = self._client.api.exec_inspect(exec_id)
        holder["exit_code"] = inspect.get("ExitCode")
        holder["stdout_tail"] = _tail(tails["stdout"].decode(errors="replace"))
        holder["stderr_tail"] = _tail(tails["stderr"].decode(errors="replace"))

    def set_network(self, enabled: bool) -> None:
        if self._closed:
            raise SandboxClosedError("session is closed")
        if enabled == self._network_enabled:
            return
        try:
            if enabled:
                self._network.connect(self._container)
            else:
                self._network.disconnect(self._container)
        except self._docker.errors.DockerException as exc:
            raise SandboxUnavailableError(f"network switch failed: {exc}") from exc
        self._network_enabled = enabled

    def set_environment(self, name: str, value: str) -> None:
        if "\n" in name or "\r" in name or "\n" in value or "\r" in value:
            raise ValueError("environment name and value must not contain newlines")
        self._environment[name] = value

    def export_diff(self) -> str:
        if self._closed:
            raise SandboxClosedError("session is closed")
        try:
            stage = self._container.exec_run(
                ["git", "add", "-A"], workdir=WORKSPACE_MOUNT, user=CONTAINER_USER, demux=True
            )
            diff = self._container.exec_run(
                ["git", "diff", "--cached"],
                workdir=WORKSPACE_MOUNT,
                user=CONTAINER_USER,
                demux=True,
            )
        except self._docker.errors.DockerException as exc:
            raise SandboxUnavailableError(f"export_diff failed: {exc}") from exc
        if stage.exit_code != 0 or diff.exit_code != 0:
            raise SandboxUnavailableError(
                f"git diff failed (add={stage.exit_code}, diff={diff.exit_code})"
            )
        stdout, _ = diff.output or (b"", b"")
        return (stdout or b"").decode(errors="replace")

    def close(self) -> None:
        """幂等清理；失败不抛异常（不能盖掉 run 的真实结果），但必须记入 cleanup_errors。"""
        if self._closed:
            return
        self._closed = True
        try:
            self._container.reload()
            if self._container.status == "running":
                self._container.kill()
        except self._docker.errors.NotFound:
            pass  # 容器已不存在，无需 kill
        except self._docker.errors.DockerException as exc:
            self._record_cleanup_failure(f"kill failed: {exc}")
        try:
            self._container.remove(force=True)
        except self._docker.errors.NotFound:
            pass
        except self._docker.errors.DockerException as exc:
            self._record_cleanup_failure(f"container remove failed: {exc}")
        try:
            self._network.remove()
        except self._docker.errors.NotFound:
            pass
        except self._docker.errors.DockerException as exc:
            self._record_cleanup_failure(f"network remove failed: {exc}")
        self._verify_no_residue()

    # --------------------------------------------------------------- cleanup

    def _kill_quietly(self) -> None:
        """超时强杀：尽力而为，不记录（close 后的残留核对才是权威判定）。"""
        try:
            self._container.kill()
        except self._docker.errors.DockerException:
            pass

    def _verify_no_residue(self) -> None:
        """close 后按 label 核对：安全测试清单第 3 节的判据，也兜底捕获静默残留。"""
        try:
            leftovers = self._client.containers.list(
                all=True, filters={"label": f"paperharness.run_id={self.run_id}"}
            )
            if leftovers:
                self._record_cleanup_failure(
                    f"{len(leftovers)} container(s) still present after close"
                )
            networks = self._client.networks.list(
                filters={"label": f"paperharness.run_id={self.run_id}"}
            )
            if networks:
                self._record_cleanup_failure(
                    f"{len(networks)} network(s) still present after close"
                )
        except self._docker.errors.DockerException as exc:
            self._record_cleanup_failure(f"post-close residue check failed: {exc}")

    def _record_cleanup_failure(self, message: str) -> None:
        self.cleanup_errors.append(message)

    # ------------------------------------------------------------------ logs


def _tail(text: str) -> str:
    return text[-TAIL_LIMIT:]
