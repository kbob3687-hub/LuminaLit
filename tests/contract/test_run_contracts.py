import pytest
from pydantic import ValidationError

from paperharness.contracts.run import RunRequest, RunState, validate_repo_url


def test_run_request_defaults():
    request = RunRequest(repo_url="https://github.com/example/repo")
    assert request.max_repairs == 3
    assert request.command is None


def test_run_request_rejects_bad_repair_budget():
    with pytest.raises(ValidationError):
        RunRequest(repo_url="https://github.com/example/repo", max_repairs=9)


@pytest.mark.parametrize(
    "bad_url",
    [
        "C:/Users/xxx/.ssh",           # Windows 本地路径
        "/etc/passwd",                 # POSIX 本地路径
        "../relative/repo",            # 相对路径
        "file:///e:/java/LuminaLit",   # file 协议
        "ssh://git@github.com/o/r",    # ssh 协议
        "git@github.com:owner/repo",   # scp 语法
        "https://user:pass@github.com/o/r",  # 内嵌凭据
        "https://",                    # 空 host
        "https://github.com",          # 裸 host，无仓库路径
        "https://github.com/o/r?x=1",  # query
        "http://github.com/owner/repo",  # 非 https
        "https://github.com/owner/repo\nx",  # 控制字符
    ],
)
def test_run_request_rejects_non_https_repo_url(bad_url):
    with pytest.raises(ValidationError):
        RunRequest(repo_url=bad_url)


def test_validate_repo_url_accepts_public_https():
    assert validate_repo_url("https://github.com/owner/repo") == "https://github.com/owner/repo"
    assert validate_repo_url("https://gitlab.example.com:8443/group/repo.git")


def test_run_state_values():
    assert RunState.SUCCEEDED.value == "succeeded"
