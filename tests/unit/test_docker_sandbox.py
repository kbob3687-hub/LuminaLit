"""DockerSandboxSession 纯逻辑单元测试，不依赖 Docker。

集成安全测试（队友负责）在 tests/integration/test_docker_sandbox.py。
"""

from pathlib import Path

import pytest

from paperharness.sandbox.docker_sandbox import DockerSandboxSession, _tail


def make_session(tmp_path: Path) -> DockerSandboxSession:
    session = DockerSandboxSession.__new__(DockerSandboxSession)
    session.run_dir = tmp_path
    session.logs_dir = tmp_path / "logs"
    session.logs_dir.mkdir(parents=True, exist_ok=True)
    session._environment = {}
    session._exec_seq = 0
    return session


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


def test_write_logs_names_and_relative_paths(tmp_path):
    session = make_session(tmp_path)
    out, err = session._write_logs("001", b"hello", b"oops")
    assert out == "logs/exec-001.out"
    assert err == "logs/exec-001.err"
    assert (tmp_path / "logs" / "exec-001.out").read_bytes() == b"hello"
    assert (tmp_path / "logs" / "exec-001.err").read_bytes() == b"oops"
