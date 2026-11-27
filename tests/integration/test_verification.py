import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from paperharness.agent.fake import FakeLLM
from paperharness.app.api import create_app
from paperharness.contracts.preflight import PreflightRequest
from paperharness.contracts.run import ExecResult, RunRequest, RunState
from paperharness.contracts.verification import MetricResult, VerificationResult
from paperharness.harness.preflight import PreflightService
from paperharness.harness.runner import HarnessRunner
from paperharness.harness.store import RunStore
from paperharness.harness.verification import verification_spec
from paperharness.sandbox.fake import FakeSandboxSession
from paperharness.targets import load_targets

ROOT = Path(__file__).resolve().parents[2]
GOOD = json.dumps({
    "schema_version": 1, "coefficients": [[0, -2, 0, 0, 0, 0], [0, 0, 1, 0, 0, 0]],
}).encode()
_GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
    "PATH": os.environ.get("PATH", ""),
}


def _init_repo(workspace: Path) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "main.py").write_bytes(b"print('base')\n")
    (workspace / "requirements.txt").write_bytes(b"numpy\n")
    for args in (
        ("init", "-q"), ("add", "-A"),
        ("-c", "core.autocrlf=false", "commit", "-q", "-m", "base"),
    ):
        subprocess.run(
            ["git", "-C", str(workspace), *args], check=True,
            capture_output=True, text=True, timeout=30, env=_GIT_ENV,
        )


def make_run(tmp_path, *, output=GOOD, result_status="passed", stale=False, cleanup_error=False):
    store = RunStore(tmp_path / "runs")
    target = next(t for t in load_targets(ROOT / "targets") if t.id == "paper-pysindy")
    calls = []
    sessions = []

    class Producer(FakeSandboxSession):
        def execute(self, command, timeout_seconds):
            if command == target.command and output is not None:
                (self.workspace / "coefficients.json").write_bytes(output)
            return ExecResult(
                exit_code=0, timed_out=False, duration_ms=1,
                stdout_path="logs/test.log", stderr_path="logs/test.err",
                stdout_tail="PASS! all metrics perfect!",
            )

        def close(self):
            super().close()
            if cleanup_error:
                self.cleanup_errors = ["producer cleanup failed"]

    def factory(request, run_dir):
        session = Producer(workspace=run_dir / "workspace")
        sessions.append(session)
        return session

    def cloner(request, run_dir):
        _init_repo(run_dir / "workspace")
        if stale:
            (run_dir / "workspace" / "coefficients.json").write_bytes(GOOD)
        return target.revision

    def verifier(raw, spec, *, timeout_seconds, cancelled):
        assert sessions[-1].closed
        assert not cancelled()
        assert 0 < timeout_seconds <= 20
        calls.append(raw)
        passed = result_status == "passed"
        return VerificationResult(
            level="target", status=result_status,
            summary="固定系数指标达标" if passed else "系数指标未达标",
            metrics=[MetricResult(name="系数误差", observed=0 if passed else 1,
                                  threshold=0.0001, passed=passed)],
            rule_id=spec.rule_id, validator_digest=spec.validator_digest,
            code_under_test=spec.code_under_test, dataset=spec.dataset, source=spec.source,
            checked_at=datetime.now(UTC),
            failure_category=None if passed else "metric_not_met",
            limitations=["未进行干净环境复跑。"],
        )

    runner = HarnessRunner(
        store=store, llm=FakeLLM(), sandbox_factory=factory, cloner=cloner,
        targets=[target], verifier=verifier,
    )
    preflights = PreflightService(store, [target])
    record = preflights.create(PreflightRequest(target_id=target.id, max_repairs=0))
    run_id = store.create_run(record.request, confirmation=record.model_dump(mode="json"))
    return runner, store, run_id, record, calls, sessions


def test_confirmed_task_verifies_only_after_producer_stops(tmp_path):
    runner, store, run_id, record, calls, sessions = make_run(tmp_path)
    result = runner.run(run_id, record.request)
    assert result.verified and result.verification.level == "target"
    assert result.verification.status == "passed"
    assert result.replay is not None and result.replay.status == "passed"
    assert calls == [GOOD, GOOD]
    assert len(sessions) == 2 and all(session.closed for session in sessions)
    assert result.verification.artifact_sha256 == hashlib.sha256(GOOD).hexdigest()
    assert result.verification.validator_digest == record.view.verification.validator_digest
    assert store.read_status(run_id)["state"] == "succeeded"
    events = store.read_events(run_id)
    kinds = [event.kind for event in events]
    assert kinds[-2:] == ["bundle_ready", "final"]
    assert kinds.index("verification") < kinds.index("replay_started")
    assert kinds.index("replay_started") < kinds.index("replay_finished")
    assert kinds.index("replay_finished") < kinds.index("bundle_ready")
    assert next(e for e in events if e.kind == "verification").state == RunState.VERIFYING
    assert json.loads(store.read_text(run_id, "verification.json")) == result.verification.model_dump(mode="json")
    assert "target/passed" in store.read_text(run_id, "report.md")
    assert record.view.verification.source.startswith("维护者")


def test_exit_zero_and_happy_log_do_not_override_metric_failure(tmp_path):
    runner, store, run_id, record, calls, _ = make_run(tmp_path, result_status="failed")
    runner.llm = Mock()
    result = runner.run(run_id, record.request)
    assert result.outcome == "failed" and not result.verified
    assert result.failure_category == "metric_not_met"
    assert result.verification.level == "target" and result.verification.status == "failed"
    assert len(calls) == 1
    runner.llm.propose_action.assert_not_called()
    runner.llm.classify_failure.assert_not_called()
    assert not any(e.state == RunState.REPAIRING for e in store.read_events(run_id))


@pytest.mark.parametrize("stale", [False, True])
def test_missing_or_stale_output_cannot_pass(tmp_path, stale):
    runner, store, run_id, record, calls, _ = make_run(tmp_path, output=None, stale=stale)
    result = runner.run(run_id, record.request)
    assert result.outcome == "failed" and not result.verified
    assert result.failure_category == "artifact_invalid"
    assert calls == []
    assert not (store.run_dir(run_id) / "workspace" / "coefficients.json").exists()


def test_failed_producer_cleanup_blocks_verification(tmp_path):
    runner, _, run_id, record, calls, _ = make_run(tmp_path, cleanup_error=True)
    result = runner.run(run_id, record.request)
    assert not result.verified
    assert result.verification.status == "blocked"
    assert result.failure_category == "verification_error"
    assert calls == []


def test_unsafe_artifact_directory_is_not_removed_or_executed(tmp_path):
    runner, store, run_id, record, calls, _ = make_run(tmp_path)
    unsafe = store.run_dir(run_id) / "workspace" / "coefficients.json"
    unsafe.mkdir()
    (unsafe / "keep.txt").write_text("keep")
    result = runner.run(run_id, record.request)
    assert not result.verified and result.failure_category == "artifact_invalid"
    assert (unsafe / "keep.txt").read_text() == "keep"
    assert calls == []


def test_confirmed_rule_change_rejected_before_clone(tmp_path):
    runner, store, run_id, record, calls, _ = make_run(tmp_path)
    record.view.verification = record.view.verification.model_copy(update={"validator_digest": "0" * 64})
    record.view.confirmation_digest = record.digest()
    store.write_text(run_id, "preflight.json", record.model_dump_json())
    runner.cloner = Mock(side_effect=AssertionError("must not clone"))
    result = runner.run(run_id, record.request)
    assert result.failure_category == "confirmation_mismatch"
    assert not result.verified and calls == []
    runner.cloner.assert_not_called()


def test_confirmed_missing_rule_rejected(tmp_path):
    runner, store, run_id, record, calls, _ = make_run(tmp_path)
    record.view.verification = None
    record.view.confirmation_digest = record.digest()
    store.write_text(run_id, "preflight.json", record.model_dump_json())
    result = runner.run(run_id, record.request)
    assert result.failure_category == "confirmation_mismatch" and not calls


def test_verifier_error_is_not_metric_failure(tmp_path):
    runner, _, run_id, record, _, _ = make_run(tmp_path)
    runner.verifier = lambda *a, **kw: VerificationResult(
        level="target", status="blocked", summary="无法启动可信验证器",
        failure_category="verification_error",
    )
    result = runner.run(run_id, record.request)
    assert result.outcome == "failed" and not result.verified
    assert result.verification.status == "blocked"
    assert result.failure_category == "verification_error"


def test_cancel_during_verification_preserves_blocked_evidence(tmp_path):
    runner, store, run_id, record, _, _ = make_run(tmp_path)

    def cancel(*a, **kw):
        runner.abort(run_id)
        assert kw["cancelled"]()
        return VerificationResult(
            level="target", status="blocked", summary="验收已取消",
            failure_category="verification_cancelled",
        )

    runner.verifier = cancel
    result = runner.run(run_id, record.request)
    assert result.outcome == "cancelled" and not result.verified
    assert result.verification.failure_category == "verification_cancelled"
    assert store.read_status(run_id)["state"] == "cancelled"


def test_evidence_api_returns_snapshot_not_mutable_workspace(tmp_path):
    runner, store, run_id, record, _, _ = make_run(tmp_path)
    result = runner.run(run_id, record.request)
    (store.run_dir(run_id) / "workspace" / "coefficients.json").write_text("tampered")
    app, _ = create_app(store, runner)
    client = TestClient(app)
    response = client.get(f"/api/runs/{run_id}/verification")
    assert response.json() == result.verification.model_dump(mode="json")
    response = client.get(result.verification.evidence_url)
    assert response.content == GOOD
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "attachment" in response.headers["content-disposition"]
    status = client.get(f"/api/runs/{run_id}").json()
    assert status["guidance"]["title"] == "指定等级通过，干净复跑通过"
    (store.run_dir(run_id) / "verification-input.json").write_text("corrupted")
    assert client.get(result.verification.evidence_url).status_code == 409
    assert client.get("/api/runs/missing/verification").status_code == 404


def test_exploration_success_only_has_execution_level(tmp_path):
    store = RunStore(tmp_path)
    runner = HarnessRunner(
        store, FakeLLM(),
        lambda req, run_dir: FakeSandboxSession(workspace=run_dir / "workspace"),
        cloner=lambda req, run_dir: "a" * 40,
    )
    request = RunRequest(repo_url="https://github.com/a/b", command=["python", "main.py"])
    run_id = store.create_run(request)
    result = runner.run(run_id, request)
    assert result.verified
    assert result.verification.level == "execution" and result.verification.metrics == []
    assert result.verification.rule_id is None
    app, _ = create_app(store, runner)
    client = TestClient(app)
    assert client.get(f"/api/runs/{run_id}").json()["guidance"]["title"] == "程序运行通过"
    assert client.get(f"/api/runs/{run_id}/verification/input").status_code == 404


def test_custom_command_on_known_repository_cannot_claim_target_level(tmp_path):
    runner, store, _, record, calls, _ = make_run(tmp_path)
    request = RunRequest(
        repo_url=record.request.repo_url, command=["python", "custom.py"],
        expected_artifact="custom.txt", max_repairs=0,
    )
    run_id = store.create_run(request)
    result = runner.run(run_id, request)
    assert result.verification.level == "execution"
    assert not result.verified and calls == []


def test_verification_deadline_failure_is_blocked_not_metric_failure(tmp_path):
    runner, _, run_id, record, _, _ = make_run(tmp_path)
    runner.verifier = lambda *a, **kw: VerificationResult(
        level="target", status="blocked", summary="验收时间已用完",
        failure_category="verification_timeout",
    )
    result = runner.run(run_id, record.request)
    assert result.outcome == "failed" and not result.verified
    assert result.verification.status == "blocked"
    assert result.failure_category == "verification_timeout"


def test_static_preflight_binds_fixed_verifier_not_a_claim_of_success(tmp_path):
    _, _, _, record, _, _ = make_run(tmp_path)
    assert record.view.verification == verification_spec()
    original = record.digest()
    record.view.verification = record.view.verification.model_copy(update={"validator_digest": "f" * 64})
    assert record.digest() != original
    assert not record.check_digest(original)
