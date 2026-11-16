"""DockerSandboxSession 纯逻辑单元测试，不依赖 Docker。

集成安全测试（队友负责）在 tests/integration/test_docker_sandbox.py。
"""

from pathlib import Path

import pytest

from paperharness.sandbox import docker_sandbox as ds
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
