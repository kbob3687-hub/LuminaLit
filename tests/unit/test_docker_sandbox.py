"""DockerSandboxSession 纯逻辑单元测试，不依赖 Docker。

集成安全测试（队友负责）在 tests/integration/test_docker_sandbox.py。
"""

from pathlib import Path

import pytest

from paperharness.sandbox import docker_sandbox as ds
from paperharness.sandbox.base import SandboxUnavailableError
from paperharness.sandbox.docker_sandbox import DockerSandboxSession, _tail


def make_session(tmp_path: Path) -> DockerSandboxSession:
    session = DockerSandboxSession.__new__(DockerSandboxSession)
    session.run_dir = tmp_path
    session.logs_dir = tmp_path / "logs"
    session.logs_dir.mkdir(parents=True, exist_ok=True)
    session._environment = {}
    session._exec_seq = 0
    session._container = FakeContainer()
    return session


def make_exec_session(tmp_path: Path, chunks, exit_code=0):
    session = make_session(tmp_path)
    api = FakeExecAPI(chunks, exit_code=exit_code)
    session._client = FakeClient(api)
    return session, api


class FakeExecAPI:
    """模拟 docker SDK 的 client.api：exec_create/exec_start/exec_inspect。"""

    def __init__(self, chunks, exit_code=0):
        self._chunks = chunks
        self._exit_code = exit_code
        self.create_kwargs = None

    def exec_create(self, **kwargs):
        self.create_kwargs = kwargs
        return {"Id": "fake-exec"}

    def exec_start(self, exec_id, stream=True, demux=True):
        yield from self._chunks

    def exec_inspect(self, exec_id):
        return {"ExitCode": self._exit_code}


class FakeClient:
    def __init__(self, api):
        self.api = api


class FakeContainer:
    id = "fake-container-id"


class FakeDockerErrors:
    class DockerException(Exception):
        pass

    class NotFound(DockerException):
        pass


class FakeDockerModule:
    """`_load_docker_module()` 的替身：只需要 `errors`。"""

    errors = FakeDockerErrors


class FakeNetwork:
    def __init__(self, name, fail_on_remove=False):
        self.name = name
        self.removed = False
        self._fail_on_remove = fail_on_remove

    def remove(self):
        if self._fail_on_remove:
            raise FakeDockerErrors.DockerException("network busy")
        self.removed = True


class FakeRuntimeContainer:
    def __init__(self, status="running"):
        self.id = "fake-runtime-container"
        self.status = status
        self.removed = 0

    def reload(self):
        pass

    def remove(self, force=False):
        self.removed += 1


class FakeContainersAPI:
    def __init__(self, status="running"):
        self.status = status
        self.run_calls = 0
        self.created: list[FakeRuntimeContainer] = []

    def list(self, all=True, filters=None):
        return []

    def run(self, *args, **kwargs):
        self.run_calls += 1
        container = FakeRuntimeContainer(status=self.status)
        self.created.append(container)
        return container


class FakeNetworksAPI:
    def __init__(self, fail_on_remove=False):
        self.created: list[FakeNetwork] = []
        self._fail_on_remove = fail_on_remove

    def get(self, name):
        raise FakeDockerErrors.NotFound("no such network")

    def create(self, name, driver=None, labels=None):
        network = FakeNetwork(name, fail_on_remove=self._fail_on_remove)
        self.created.append(network)
        return network

    def list(self, filters=None):
        return []


class FakeImagesAPI:
    def __init__(self, exists=True):
        self.exists = exists

    def get(self, name):
        if not self.exists:
            raise FakeDockerErrors.NotFound("no such image")
        return object()


class FakeSetupClient:
    """构造期用的 docker client 替身，不连接任何真实引擎。"""

    def __init__(self, image_exists=True, status="running", network_remove_fails=False):
        self.images = FakeImagesAPI(exists=image_exists)
        self.containers = FakeContainersAPI(status=status)
        self.networks = FakeNetworksAPI(fail_on_remove=network_remove_fails)


def build_session(tmp_path: Path, client, monkeypatch, name="run-setup"):
    monkeypatch.setattr(ds, "_load_docker_module", lambda: FakeDockerModule)
    return DockerSandboxSession(
        run_dir=tmp_path / name, python_version="3.10", client=client
    )


def test_constructor_failure_removes_created_network(tmp_path, monkeypatch):
    """镜像不存在 → 容器没建成，但已建的网络必须回收（异常路径清理）。"""
    client = FakeSetupClient(image_exists=False)

    with pytest.raises(SandboxUnavailableError, match="not found"):
        build_session(tmp_path, client, monkeypatch)

    assert client.containers.run_calls == 0
    assert [network.removed for network in client.networks.created] == [True]


def test_constructor_failure_removes_half_created_container(tmp_path, monkeypatch):
    """容器起来了但没跑住 → 容器和网络都要回收，不留同 label 的残留。"""
    client = FakeSetupClient(image_exists=True, status="exited")

    with pytest.raises(SandboxUnavailableError, match="did not stay running"):
        build_session(tmp_path, client, monkeypatch)

    assert [container.removed for container in client.containers.created] == [1]
    assert client.networks.created[0].removed is True


def test_constructor_network_setup_failure_has_nothing_to_clean(tmp_path, monkeypatch):
    """网络都没建成时，回收逻辑不能自己再抛一次。"""
    client = FakeSetupClient(image_exists=True)
    monkeypatch.setattr(
        client.networks,
        "create",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            FakeDockerErrors.DockerException("bridge driver missing")
        ),
    )

    with pytest.raises(SandboxUnavailableError, match="network setup failed"):
        build_session(tmp_path, client, monkeypatch)


def test_constructor_cleanup_failure_does_not_mask_original_error(tmp_path, monkeypatch):
    """回收失败不能让原始错误变成清理错误。"""
    client = FakeSetupClient(image_exists=False, network_remove_fails=True)

    with pytest.raises(SandboxUnavailableError, match="not found"):
        build_session(tmp_path, client, monkeypatch)



def test_set_environment_stores_and_overrides():
    session = DockerSandboxSession.__new__(DockerSandboxSession)
    session._environment = {}
    session.set_environment("OMP_NUM_THREADS", "4")
    session.set_environment("OMP_NUM_THREADS", "1")
    assert session._environment == {"OMP_NUM_THREADS": "1"}


@pytest.mark.parametrize("bad", ["CUDA\nX", "CUDA\rX", "CUDA_X\n", "\r"])
def test_set_environment_rejects_newlines(bad):
    session = DockerSandboxSession.__new__(DockerSandboxSession)
    session._environment = {}
    with pytest.raises(ValueError):
        session.set_environment(bad, "-1")
    with pytest.raises(ValueError):
        session.set_environment("CUDA_VISIBLE_DEVICES", "0\n1")


def test_tail_keeps_last_2000_chars():
    assert _tail("a" * 3000) == "a" * 2000
    assert _tail("short") == "short"


def test_exec_streaming_writes_logs_and_tails(tmp_path):
    session, api = make_exec_session(
        tmp_path,
        [(b"hello stdout\n", None), (None, b"hello stderr\n"), (b"tail", None)],
        exit_code=3,
    )
    out_path = session.logs_dir / "exec-001.out"
    err_path = session.logs_dir / "exec-001.err"
    holder: dict = {}
    session._exec_streaming(["python", "x.py"], out_path, err_path, holder)

    assert out_path.read_bytes() == b"hello stdout\ntail"
    assert err_path.read_bytes() == b"hello stderr\n"
    assert holder["exit_code"] == 3
    assert holder["stdout_tail"] == "hello stdout\ntail"
    assert holder["stderr_tail"] == "hello stderr\n"
    # exec 参数走 argv 数组 + 固定 workdir/用户/env
    kwargs = api.create_kwargs
    assert kwargs["cmd"] == ["python", "x.py"]
    assert kwargs["workdir"] == ds.WORKSPACE_MOUNT
    assert kwargs["user"] == ds.CONTAINER_USER


def test_exec_streaming_truncates_oversized_output(tmp_path, monkeypatch):
    monkeypatch.setattr(ds, "MAX_LOG_BYTES", 10)
    session, _ = make_exec_session(
        tmp_path, [(b"0123456789", None), (b"OVERFLOW-BYTES", None)]
    )
    out_path = session.logs_dir / "exec-001.out"
    err_path = session.logs_dir / "exec-001.err"
    holder: dict = {}
    session._exec_streaming(["x"], out_path, err_path, holder)

    content = out_path.read_bytes()
    assert content.startswith(b"0123456789")
    assert b"OVERFLOW-BYTES" not in content  # 超限字节不落盘
    assert b"truncated at" in content       # 但打了截断标记
    assert err_path.read_bytes() == b""


def test_tail_window_keeps_only_last_bytes(tmp_path):
    session, _ = make_exec_session(tmp_path, [(b"x" * 30000, None)])
    out_path = session.logs_dir / "exec-001.out"
    err_path = session.logs_dir / "exec-001.err"
    holder: dict = {}
    session._exec_streaming(["x"], out_path, err_path, holder)

    assert out_path.stat().st_size == 30000  # 文件是全量
    assert len(holder["stdout_tail"]) == 2000  # 内存只留尾部
    assert holder["stdout_tail"] == "x" * 2000


def test_execute_timeout_preserves_partial_tails(tmp_path):
    import threading

    released = threading.Event()

    class HangingStreamAPI:
        def exec_create(self, **kwargs):
            return {"Id": "hang-exec"}

        def exec_start(self, exec_id, stream=True, demux=True):
            yield (b"partial stdout\n", None)
            yield (None, b"partial stderr\n")
            released.wait(timeout=5)

        def exec_inspect(self, exec_id):
            return {"ExitCode": None}

    class KillableContainer:
        id = "killable-id"

        def kill(self):
            released.set()

    session = make_session(tmp_path)
    session._closed = False
    session._docker = FakeDockerModule
    session._container = KillableContainer()
    session._client = FakeClient(HangingStreamAPI())

    result = session.execute(["python", "slow.py"], timeout_seconds=0.1)

    assert result.timed_out is True
    assert result.exit_code is None
    assert "partial stdout" in result.stdout_tail
    assert "partial stderr" in result.stderr_tail
    assert session._closed is True


def test_export_diff_uses_safe_git_flags_and_truncates(tmp_path, monkeypatch):
    monkeypatch.setattr(ds, "MAX_DIFF_BYTES", 10)
    commands: list[list[str]] = []

    class DiffAPI:
        def exec_create(self, **kwargs):
            commands.append(list(kwargs["cmd"]))
            return {"Id": f"exec-{len(commands)}"}

        def exec_start(self, exec_id, stream=True, demux=True):
            if exec_id == "exec-1":
                yield (b"", None)
            else:
                yield (b"diff --git a/x b/x\n+1234567890", None)

        def exec_inspect(self, exec_id):
            return {"ExitCode": 0}

    session = make_session(tmp_path)
    session._closed = False
    session._docker = FakeDockerModule
    session._client = FakeClient(DiffAPI())

    diff = session.export_diff()

    assert len(commands) == 2
    assert "core.hooksPath=/dev/null" in commands[0]
    assert "--no-ext-diff" in commands[1]
    assert "core.hooksPath=/dev/null" in commands[1]
    assert diff.startswith("diff --git")
    assert "truncated at 10 bytes" in diff
