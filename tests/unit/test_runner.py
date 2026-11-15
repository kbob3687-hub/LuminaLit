from paperharness.agent.fake import FakeLLM
from paperharness.contracts.run import ExecResult, RunRequest, RunState
from paperharness.harness.runner import HarnessRunner
from paperharness.harness.store import RunStore
from paperharness.sandbox.fake import FakeSandboxSession


def make_runner(tmp_path, results=None, artifact_path=None):
    store = RunStore(tmp_path)

    def factory(request, run_dir):
        return FakeSandboxSession(
            results, artifact_path=artifact_path, workspace=run_dir / "workspace"
        )

    runner = HarnessRunner(store=store, llm=FakeLLM(), sandbox_factory=factory)
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
