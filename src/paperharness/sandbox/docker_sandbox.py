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
        self._network = self._create_network()
        self._container = self._create_container()

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
            raise
        except self._docker.errors.DockerException as exc:
            raise SandboxUnavailableError(f"container creation failed: {exc}") from exc

    # --------------------------------------------------------------- protocol

    def execute(self, command: list[str], timeout_seconds: int) -> ExecResult:
        if self._closed:
            raise SandboxClosedError("session is closed")
        started = time.monotonic()
        self._exec_seq += 1
        seq = f"{self._exec_seq:03d}"
        holder: dict[str, Any] = {}

        def _run() -> None:
            try:
                holder["exec"] = self._container.exec_run(
                    cmd=list(command),
                    workdir=WORKSPACE_MOUNT,
                    user=CONTAINER_USER,
                    environment=dict(self._environment),
                    demux=True,
                )
            except self._docker.errors.DockerException as exc:
                holder["error"] = exc

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        thread.join(timeout_seconds)
        duration_ms = int((time.monotonic() - started) * 1000)
        stdout_path, stderr_path = self._write_logs(seq, b"", b"")

        if thread.is_alive():
            self._kill()
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

        exit_code, output = holder["exec"].exit_code, holder["exec"].output
        stdout, stderr = output[0] or b"", output[1] or b""
        stdout_path, stderr_path = self._write_logs(seq, stdout, stderr)
        return ExecResult(
            exit_code=exit_code,
            timed_out=False,
            duration_ms=duration_ms,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            stdout_tail=_tail(stdout.decode(errors="replace")),
            stderr_tail=_tail(stderr.decode(errors="replace")),
        )

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
        if self._closed:
            return
        self._closed = True
        self._kill()
        self._remove_container()
        self._remove_network()

    # --------------------------------------------------------------- cleanup

    def _kill(self) -> None:
        try:
            self._container.kill()
        except self._docker.errors.DockerException:
            pass

    def _remove_container(self) -> None:
        try:
            self._container.remove(force=True)
        except self._docker.errors.DockerException:
            pass

    def _remove_network(self) -> None:
        try:
            self._network.remove()
        except self._docker.errors.DockerException:
            pass

    # ------------------------------------------------------------------ logs

    def _write_logs(self, seq: str, stdout: bytes, stderr: bytes) -> tuple[str, str]:
        out_path = self.logs_dir / f"exec-{seq}.out"
        err_path = self.logs_dir / f"exec-{seq}.err"
        out_path.write_bytes(stdout)
        err_path.write_bytes(stderr)
        return f"logs/{out_path.name}", f"logs/{err_path.name}"


def _tail(text: str) -> str:
    return text[-TAIL_LIMIT:]
