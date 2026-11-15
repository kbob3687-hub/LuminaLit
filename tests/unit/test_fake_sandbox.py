from paperharness.contracts.run import ExecResult
from paperharness.sandbox.fake import FakeSandboxSession


def test_fake_sandbox_returns_scripted_results(tmp_path):
    first = ExecResult(
        exit_code=1,
        timed_out=False,
        duration_ms=10,
        stdout_path="logs/a.log",
        stderr_path="logs/a.err",
        stderr_tail="ModuleNotFoundError",
    )
    session = FakeSandboxSession([first], artifact_path="out.txt", workspace=tmp_path)
    result = session.execute(["python", "main.py"], timeout_seconds=30)
    assert result.exit_code == 1
    assert session.network_enabled is True
    session.set_network(False)
    assert session.network_enabled is False
    session.close()
    assert session.closed is True
