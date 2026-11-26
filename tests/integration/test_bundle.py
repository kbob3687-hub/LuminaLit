import json
import os
import shutil
import subprocess
import time
import zipfile
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from paperharness.agent.fake import FakeLLM
from paperharness.app.api import create_app
from paperharness.contracts.bundle import ChangeKind, RepairManifest
from paperharness.contracts.preflight import PreflightRequest
from paperharness.contracts.run import ExecResult, RunRequest, RunState
from paperharness.contracts.verification import MetricResult, VerificationResult
from paperharness.harness.bundle import manifest_digest
from paperharness.harness.preflight import PreflightService
from paperharness.harness.runner import HarnessRunner, TimeBudgetExceeded
from paperharness.harness.store import RunStore
from paperharness.sandbox.fake import FakeSandboxSession
from paperharness.targets import load_targets

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")

ROOT = Path(__file__).resolve().parents[2]
GOOD = json.dumps({
    "schema_version": 1, "coefficients": [[0, -2, 0, 0, 0, 0], [0, 0, 1, 0, 0, 0]],
}).encode()

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
    "PATH": os.environ.get("PATH", ""),
}


def _git(workspace: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(workspace), *args],
        check=True, capture_output=True, text=True, timeout=30, env=_GIT_ENV,
    )
    return proc.stdout.strip()


def _init_repo(workspace: Path) -> str:
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "main.py").write_bytes(b"print('base')\n")
    (workspace / "requirements.txt").write_bytes(b"numpy\n")
    _git(workspace, "init", "-q")
    _git(workspace, "add", "-A")
    _git(workspace, "-c", "core.autocrlf=false", "commit", "-q", "-m", "base")
    return _git(workspace, "rev-parse", "HEAD")


INSTALL_COMMAND = ["python", "-m", "pip", "install", "-r", "requirements.txt"]


def make_run(
    tmp_path, *, real_git=True, repair_dependency=None, repair_environment=None,
    replay_creates_artifact=True, replay_artifact_bytes=GOOD,
    replay_verification_status="passed", replay_verifier_error=False,
    replay_install_failure=False, replay_command_failure=False, replay_clone_error=False,
    replay_budget_exceeded=False, cancel_during_replay=False,
    cancel_after_current_verification=False,
    expire_budget_after_current_verification=False, budget_flag=None,
    max_repairs=0, llm=None, aci_factory=None,
):
    store = RunStore(tmp_path / "runs")
    target = next(t for t in load_targets(ROOT / "targets") if t.id == "paper-pysindy")

    clones = []
    sessions = []

    class Producer(FakeSandboxSession):
        def execute(self, command, timeout_seconds):
            self.commands.append(command)
            replay = len(sessions) > 1
            if replay and replay_budget_exceeded and command == INSTALL_COMMAND:
                raise TimeBudgetExceeded("total wall-clock budget exhausted")
            if replay and replay_install_failure and command == INSTALL_COMMAND:
                return ExecResult(
                    exit_code=1, timed_out=False, duration_ms=1,
                    stdout_path="logs/i.out", stderr_path="logs/i.err",
                    stderr_tail="replay install failed",
                )
            if command == target.command:
                # 模拟修复动作：改源码 + 产出指标产物
                (self.workspace / "main.py").write_bytes(b"print('fixed')\n")
                if replay and replay_command_failure:
                    return ExecResult(
                        exit_code=1, timed_out=False, duration_ms=1,
                        stdout_path="logs/t.out", stderr_path="logs/t.err",
                        stderr_tail="replay command failed",
                    )
                artifact = replay_artifact_bytes if replay else GOOD
                if len(sessions) == 1 or replay_creates_artifact:
                    (self.workspace / "coefficients.json").write_bytes(artifact)
            return ExecResult(
                exit_code=0, timed_out=False, duration_ms=1,
                stdout_path="logs/test.log", stderr_path="logs/test.err",
            )

        def export_diff(self):
            return "diff --git a/main.py b/main.py\n+print('fixed')\n"

    def factory(request, run_dir):
        session = Producer(workspace=run_dir / "workspace")
        sessions.append(session)
        if cancel_during_replay and len(sessions) == 2:
            runner.abort(run_id)
        return session

    def cloner(request, run_dir):
        clones.append(run_dir)
        if replay_clone_error and len(clones) == 2:
            raise RuntimeError("replay clone unavailable")
        if real_git:
            # 真实 git 工作区供清单审计；返回 target.revision 以匹配确认计划。
            _init_repo(run_dir / "workspace")
        else:
            (run_dir / "workspace").mkdir(parents=True, exist_ok=True)
        return target.revision

    verifier_calls = 0

    def verifier(raw, spec, *, timeout_seconds, cancelled):
        nonlocal verifier_calls
        verifier_calls += 1
        if verifier_calls == 1:
            if cancel_after_current_verification:
                runner.abort(run_id)
            if expire_budget_after_current_verification and budget_flag is not None:
                budget_flag["expired"] = True
        if replay_verifier_error and verifier_calls == 2:
            raise RuntimeError("replay verifier unavailable")
        status = "passed" if verifier_calls == 1 else replay_verification_status
        return VerificationResult(
            level="target", status=status, summary="固定系数指标达标" if status == "passed" else "复跑指标未达标",
            metrics=[MetricResult(
                name="系数误差", observed=0 if status == "passed" else 1,
                threshold=0.0001, passed=status == "passed",
            )],
            rule_id=spec.rule_id, validator_digest=spec.validator_digest,
            code_under_test=spec.code_under_test, dataset=spec.dataset, source=spec.source,
            failure_category=None if status == "passed" else "metric_failed",
            checked_at=datetime.now(UTC),
        )

    runner = HarnessRunner(
        store=store, llm=llm or FakeLLM(), sandbox_factory=factory, cloner=cloner,
        targets=[target], verifier=verifier, aci_factory=aci_factory,
    )
    preflights = PreflightService(store, [target])
    record = preflights.create(
        PreflightRequest(target_id=target.id, max_repairs=max_repairs)
    )
    run_id = store.create_run(record.request, confirmation=record.model_dump(mode="json"))
    if repair_dependency is not None:
        store.append_event(
            run_id, RunState.REPAIRING, "install_requirement",
            "repair dependency installed",
            {"ok": True, "requirement": repair_dependency},
        )
    if repair_environment is not None:
        store.append_event(
            run_id, RunState.REPAIRING, "set_environment",
            "repair environment installed", repair_environment,
        )
    return runner, store, run_id, record, clones, sessions


def test_run_finalizes_complete_bundle_and_serves_zip(tmp_path):
    runner, store, run_id, record, clones, sessions = make_run(
        tmp_path, real_git=True, repair_dependency="repair-dependency==1.0",
        repair_environment={"ok": True, "env_name": "OMP_NUM_THREADS", "env_value": "1"},
    )
    result = runner.run(run_id, record.request)

    assert result.verified
    assert result.replay is not None and result.replay.status == "passed"
    assert len(clones) == 2 and clones[0] != clones[1]
    assert clones[0].name == run_id
    assert clones[1].name == f"replay-{run_id}"
    assert result.replay.session_id == f"replay-{run_id}"
    assert len(sessions) == 2 and sessions[0] is not sessions[1]
    assert sessions[0].workspace != sessions[1].workspace
    assert sessions[1].commands == [
        ["python", "-m", "pip", "install", "-r", "requirements.txt"],
        ["python", "-m", "pip", "install", "repair-dependency==1.0"],
        record.view.plan.command,
    ]
    assert sessions[1].environment == {"OMP_NUM_THREADS": "1"}
    assert all(session.closed for session in sessions)
    assert result.replay.repair_manifest_digest == result.bundle.manifest_digest
    assert result.replay.verification is not None
    assert result.replay.verification.status == "passed"
    assert result.bundle is not None and result.bundle.integrity == "complete"
    assert result.bundle_url == f"/api/runs/{run_id}/bundle"
    # 清单落盘且产物与源码变更分开
    manifest = json.loads(store.read_text(run_id, "repair_manifest.json"))
    assert json.loads(store.read_text(run_id, "verification.json")) == result.verification.model_dump(mode="json")
    assert json.loads(store.read_text(run_id, "replay_result.json")) == result.replay.model_dump(mode="json")
    assert json.loads(store.read_text(run_id, "repair_manifest.json")) == manifest
    assert manifest["base_revision"] != ""
    assert manifest["repository_url"] == record.request.repo_url
    assert manifest["environment"] == {"OMP_NUM_THREADS": "1"}
    paths = {change["path"] for change in manifest["files"]}
    assert "main.py" in paths and "coefficients.json" not in paths
    assert manifest["artifacts"] == ["coefficients.json"]
    change = next(c for c in manifest["files"] if c["path"] == "main.py")
    assert change["change"] == ChangeKind.MODIFIED.value
    # bundle_ready 先于 final，材料状态属于已落盘证据
    kinds = [event.kind for event in store.read_events(run_id)]
    assert kinds[-2:] == ["bundle_ready", "final"]
    assert kinds.index("replay_started") < kinds.index("replay_finished") < kinds.index("bundle_ready")
    ready = next(event for event in store.read_events(run_id) if event.kind == "bundle_ready")
    assert ready.data["manifest_digest"] == result.bundle.manifest_digest

    app, _worker = create_app(store, runner)
    client = TestClient(app)
    response = client.get(f"/api/runs/{run_id}/bundle")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        names = set(archive.namelist())
        assert "replay_result.json" in names
        assert {"files/main.py", "artifacts/coefficients.json",
                "repair_manifest.json", "patch.diff", "SHA256SUMS"} <= names
        assert archive.read("artifacts/coefficients.json") == GOOD


@pytest.mark.parametrize(
    "repair_environment",
    [
        {"ok": True, "env_name": "OMP_NUM_THREADS"},
        {"ok": True, "env_name": "OMP_NUM_THREADS", "env_value": ""},
        {"ok": True, "env_name": "OMP_NUM_THREADS", "env_value": "1\nSECRET=1"},
        {"ok": True, "env_name": "PATH", "env_value": "/tmp/bin"},
    ],
)
def test_invalid_replay_environment_value_blocks_replay(tmp_path, repair_environment):
    runner, _store, run_id, record, _, sessions = make_run(
        tmp_path, real_git=True, repair_environment=repair_environment,
    )

    result = runner.run(run_id, record.request)

    assert result.verified
    assert result.outcome == "failed"
    assert result.replay is not None and result.replay.status == "blocked"
    assert result.replay.reason == "replay_material_incomplete"
    assert sessions[1].environment == {}
    assert any(repair_environment["env_name"] in limitation for limitation in result.replay.limitations)


def test_missing_replay_artifact_is_not_reported_as_hash_difference(tmp_path):
    runner, store, run_id, record, _, _ = make_run(
        tmp_path, real_git=True, replay_creates_artifact=False,
    )

    result = runner.run(run_id, record.request)

    assert result.verified
    assert result.replay is not None and result.replay.status == "failed"
    assert result.replay.reason == "artifact_invalid"
    assert result.replay.verification is not None
    assert result.replay.verification.artifact_sha256 is None
    assert result.replay.differences == []
    assert json.loads(store.read_text(run_id, "replay_result.json"))["differences"] == []


def test_unauditable_workspace_blocks_bundle(tmp_path):
    runner, store, run_id, record, _, _ = make_run(tmp_path, real_git=False)
    result = runner.run(run_id, record.request)

    assert result.verified
    assert result.bundle is not None and result.bundle.integrity == "blocked"
    assert (store.run_dir(run_id) / "reproduce.zip").exists() is False

    app, _worker = create_app(store, runner)
    client = TestClient(app)
    response = client.get(f"/api/runs/{run_id}/bundle")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "bundle_blocked"


DIFFERENT_BYTES = json.dumps({
    "schema_version": 1, "coefficients": [[0, -2, 0, 0, 0, 0], [0, 0, 1, 0, 0, 0]],
}, indent=2).encode()


def test_replay_metric_failure_keeps_current_verification_but_fails_run(tmp_path):
    runner, store, run_id, record, _, _ = make_run(
        tmp_path, replay_verification_status="failed",
    )

    result = runner.run(run_id, record.request)

    assert result.verified is True
    assert result.outcome == "failed"
    assert result.failure_category == "replay_failed"
    assert result.replay is not None
    assert result.replay.status == "failed"
    assert result.replay.reason == "metric_failed"
    assert result.replay.verification is not None
    assert result.replay.verification.status == "failed"
    assert json.loads(store.read_text(run_id, "verification.json"))["status"] == "passed"
    assert store.read_status(run_id)["state"] == "failed"


def test_replay_install_failure_does_not_start_second_repair_loop(tmp_path):
    aci_calls = []

    def aci_factory(*args, **kwargs):
        aci_calls.append(args)
        raise AssertionError("干净复跑不得再次进入修复循环")

    runner, _store, run_id, record, _, sessions = make_run(
        tmp_path, max_repairs=2, replay_install_failure=True, aci_factory=aci_factory,
    )

    result = runner.run(run_id, record.request)

    assert result.verified is True
    assert result.outcome == "failed"
    assert result.failure_category == "replay_failed"
    assert result.replay is not None and result.replay.status == "failed"
    assert result.replay.reason == "replay_install_failed"
    assert aci_calls == []
    assert len(sessions) == 2
    assert sessions[1].commands == [INSTALL_COMMAND]


def test_replay_command_failure_marks_failed(tmp_path):
    runner, _store, run_id, record, _, sessions = make_run(
        tmp_path, replay_command_failure=True,
    )

    result = runner.run(run_id, record.request)

    assert result.verified is True
    assert result.outcome == "failed"
    assert result.failure_category == "replay_failed"
    assert result.replay is not None and result.replay.status == "failed"
    assert result.replay.reason == "replay_command_failed"
    assert sessions[1].commands[-1] == record.view.plan.command


def test_replay_clone_failure_is_blocked_and_keeps_frozen_manifest(tmp_path):
    runner, store, run_id, record, clones, sessions = make_run(
        tmp_path, replay_clone_error=True,
    )

    result = runner.run(run_id, record.request)

    assert result.verified is True
    assert result.outcome == "failed"
    assert result.failure_category == "replay_blocked"
    assert result.replay is not None and result.replay.status == "blocked"
    assert result.replay.reason == "replay_clone_failed"
    assert len(clones) == 2 and len(sessions) == 1
    stored = json.loads(store.read_text(run_id, "replay_result.json"))
    assert stored["status"] == "blocked" and stored["reason"] == "replay_clone_failed"
    assert json.loads(store.read_text(run_id, "verification.json"))["status"] == "passed"
    manifest = RepairManifest.model_validate_json(
        store.read_text(run_id, "repair_manifest.json")
    )
    assert manifest_digest(manifest) == result.replay.repair_manifest_digest
    assert result.bundle is not None
    assert result.bundle.manifest_digest == result.replay.repair_manifest_digest


def test_replay_verifier_error_is_blocked_not_failed(tmp_path):
    runner, store, run_id, record, _, sessions = make_run(
        tmp_path, replay_verifier_error=True,
    )

    result = runner.run(run_id, record.request)

    assert result.verified is True
    assert result.outcome == "failed"
    assert result.failure_category == "replay_blocked"
    assert result.replay is not None and result.replay.status == "blocked"
    assert result.replay.reason == "replay_verifier_error"
    assert result.replay.verification is None
    assert sessions[1].closed
    assert json.loads(store.read_text(run_id, "verification.json"))["status"] == "passed"


def test_replay_artifact_hash_difference_is_recorded_not_fatal(tmp_path):
    runner, _store, run_id, record, _, _ = make_run(
        tmp_path, replay_artifact_bytes=DIFFERENT_BYTES,
    )

    result = runner.run(run_id, record.request)

    assert result.verified is True
    assert result.outcome == "succeeded"
    assert result.replay is not None and result.replay.status == "passed"
    assert result.replay.differences
    assert "SHA-256" in result.replay.differences[0]
    assert result.bundle is not None and result.bundle.integrity == "complete"


def test_cancel_during_replay_blocks_replay_and_keeps_evidence(tmp_path):
    runner, store, run_id, record, _, sessions = make_run(
        tmp_path, cancel_during_replay=True,
    )

    result = runner.run(run_id, record.request)

    assert result.outcome == "cancelled"
    assert result.verified is True
    assert result.replay is not None and result.replay.status == "blocked"
    assert result.replay.reason == "cancelled"
    assert store.read_status(run_id)["state"] == "cancelled"
    assert json.loads(store.read_text(run_id, "replay_result.json"))["reason"] == "cancelled"
    assert json.loads(store.read_text(run_id, "verification.json"))["status"] == "passed"
    assert all(session.closed for session in sessions)


def test_budget_exhausted_during_replay_blocks_replay(tmp_path):
    runner, store, run_id, record, _, _ = make_run(
        tmp_path, replay_budget_exceeded=True,
    )

    result = runner.run(run_id, record.request)

    assert result.outcome == "budget_exceeded"
    assert result.verified is True
    assert result.replay is not None and result.replay.status == "blocked"
    assert result.replay.reason == "budget_exceeded"
    assert store.read_status(run_id)["state"] == "failed"
    stored = json.loads(store.read_text(run_id, "replay_result.json"))
    assert stored["status"] == "blocked" and stored["reason"] == "budget_exceeded"
    assert json.loads(store.read_text(run_id, "verification.json"))["status"] == "passed"


def test_cancel_before_replay_records_not_started(tmp_path):
    runner, store, run_id, record, clones, sessions = make_run(
        tmp_path, cancel_after_current_verification=True,
    )

    result = runner.run(run_id, record.request)

    assert result.outcome == "cancelled"
    assert result.verified is True
    assert result.replay is not None and result.replay.status == "blocked"
    assert result.replay.reason == "not_started_before_termination"
    assert len(clones) == 1 and len(sessions) == 1
    stored = json.loads(store.read_text(run_id, "replay_result.json"))
    assert stored["status"] == "blocked"
    assert stored["reason"] == "not_started_before_termination"


def test_budget_before_replay_records_not_started(tmp_path, monkeypatch):
    budget_flag = {"expired": False}
    real_monotonic = time.monotonic

    def fake_monotonic():
        return 10**9 if budget_flag["expired"] else real_monotonic()

    monkeypatch.setattr(time, "monotonic", fake_monotonic)
    runner, store, run_id, record, clones, sessions = make_run(
        tmp_path, expire_budget_after_current_verification=True, budget_flag=budget_flag,
    )

    result = runner.run(run_id, record.request)

    assert result.outcome == "budget_exceeded"
    assert result.verified is True
    assert result.replay is not None and result.replay.status == "blocked"
    assert result.replay.reason == "not_started_before_termination"
    assert len(clones) == 1 and len(sessions) == 1
    stored = json.loads(store.read_text(run_id, "replay_result.json"))
    assert stored["status"] == "blocked"
    assert stored["reason"] == "not_started_before_termination"


def test_bundle_endpoint_rejects_tampered_zip(tmp_path):
    runner, store, run_id, record, _, _ = make_run(tmp_path)
    result = runner.run(run_id, record.request)
    assert result.bundle is not None and result.bundle.integrity == "complete"
    zip_path = store.run_dir(run_id) / "reproduce.zip"
    zip_path.write_bytes(zip_path.read_bytes() + b"\x00tamper")

    app, _worker = create_app(store, runner)
    response = TestClient(app).get(f"/api/runs/{run_id}/bundle")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "bundle_corrupted"


def test_bundle_endpoint_404_before_finalized(tmp_path):
    store = RunStore(tmp_path / "runs")
    runner = HarnessRunner(
        store=store, llm=FakeLLM(),
        sandbox_factory=lambda request, run_dir: FakeSandboxSession(workspace=run_dir / "workspace"),
        cloner=lambda request, run_dir: "a" * 40,
    )
    run_id = store.create_run(RunRequest(
        repo_url="https://github.com/paperharness-demo/clean-example"))
    app, _worker = create_app(store, runner)
    client = TestClient(app)
    response = client.get(f"/api/runs/{run_id}/bundle")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "bundle_not_ready"
