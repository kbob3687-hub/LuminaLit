from paperharness.agent.fake import FakeLLM
from paperharness.contracts.run import ExecResult, RunRequest, RunState
from paperharness.harness.runner import HarnessRunner
from paperharness.harness.store import RunStore
from paperharness.sandbox.fake import FakeSandboxSession


def make_runner(tmp_path, results=None, artifact_path=None, cloner=None, factory=None):
    store = RunStore(tmp_path)

    def default_factory(request, run_dir):
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
