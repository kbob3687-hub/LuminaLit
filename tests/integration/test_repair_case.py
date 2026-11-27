"""Read-only case projections and integrity-checked handoff downloads (API fixtures)."""

import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from paperharness.app.api import create_app
from paperharness.app.repair_case import REVISION
from paperharness.contracts.run import ReplayResult, RunRequest, RunResult, RunState
from paperharness.contracts.verification import VerificationResult
from paperharness.harness.runner import HarnessRunner
from paperharness.harness.store import RunStore


class NoModel:
    def __getattr__(self, name):
        raise AssertionError(f"Reading a case must not invoke a model: {name}")


@pytest.fixture
def case(tmp_path):
    store = RunStore(tmp_path)
    run_id = store.create_run(RunRequest(
        repo_url="https://github.com/dynamicslab/pysindy", revision=REVISION,
    ))
    store.write_result(run_id, RunResult(
        run_id=run_id, outcome="succeeded", verified=True, attempts=2,
        resolved_revision=REVISION, report_url="/report",
        verification=VerificationResult(status="passed", level="target"),
        replay=ReplayResult(status="passed"),
    ))
    store.write_status(run_id, RunState.SUCCEEDED, {})
    store.write_text(run_id, "repair_acceptance.json", json.dumps({
        "source_revision": REVISION, "model": "API fixture", "successful_model_json_decisions": 2,
    }))
    store.append_event(run_id, RunState.TESTING, "test", "failed", {
        "exit_code": 1, "stderr_tail": "AttributeError: missing private method",
    })
    store.append_event(run_id, RunState.REPAIRING, "replace_exact", "replaced", {
        "ok": True, "path": "pysindy/optimizers/base.py",
    })
    store.append_event(run_id, RunState.REPAIRING, "replace_exact", "replace_not_found", {
        "ok": False, "path": "pysindy/optimizers/base.py",
    })
    def no_sandbox(*_args):
        raise AssertionError("Reading a case must not create a sandbox")

    app, _ = create_app(store, HarnessRunner(
        store=store, llm=NoModel(), sandbox_factory=no_sandbox,
    ))
    with TestClient(app) as client:
        yield store, run_id, client


def test_case_reads_actual_run_and_keeps_failed_actions_and_pending_teammate(case):
    store, run_id, client = case
    assert client.get("/api/cases/lorenz-repair").json()["run_id"] == run_id
    response = client.get(f"/api/runs/{run_id}/repair-evidence")
    assert response.status_code == 200
    data = response.json()
    assert data["original_error"] == "AttributeError: missing private method"
    assert [a["ok"] for a in data["actions"]] == [True, False]
    assert data["diff"] is None  # No source available: do not fabricate a patch.
    assert data["handoff"] == {
        "status": "pending", "available": False, "size_bytes": None,
        "sha256": None, "download_url": None,
    }
    assert len(list(store.root.glob("*/request.json"))) == 1


def test_zero_model_decisions_do_not_qualify_as_real_repair_history(case):
    store, run_id, client = case
    store.write_text(run_id, "repair_acceptance.json", json.dumps({
        "source_revision": REVISION, "successful_model_json_decisions": 0,
    }))
    assert client.get("/api/cases/lorenz-repair").status_code == 404


def test_handoff_requires_publication_and_rejects_same_size_tampering(case):
    store, run_id, client = case
    url = f"/api/runs/{run_id}/handoff"
    assert client.get(url).status_code == 404
    raw = b"ZIP API fixture"
    path = store.run_dir(run_id) / "handoff.zip"
    path.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    store.write_text(run_id, "handoff_package.json", json.dumps({
        "run_id": run_id, "size_bytes": len(raw), "sha256": digest,
    }))
    response = client.get(url)
    assert response.status_code == 200 and response.content == raw
    assert response.headers["X-Bundle-SHA256"] == digest
    assert client.get(f"/api/runs/{run_id}/repair-evidence").json()["handoff"]["status"] == "pending"
    path.write_bytes(b"X" * len(raw))
    response = client.get(url)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "handoff_corrupted"


def test_invalid_run_id_has_no_host_file_access(case):
    _, _, client = case
    assert client.get("/api/runs/not-a-run/repair-evidence").status_code == 404
