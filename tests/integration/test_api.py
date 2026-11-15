import time

from fastapi.testclient import TestClient

from paperharness.agent.fake import FakeLLM
from paperharness.app.api import create_app
from paperharness.harness.runner import HarnessRunner
from paperharness.harness.store import RunStore
from paperharness.sandbox.fake import FakeSandboxSession


def make_client(tmp_path):
    store = RunStore(tmp_path)
    runner = HarnessRunner(
        store=store,
        llm=FakeLLM(),
        sandbox_factory=lambda request, run_dir: FakeSandboxSession(
            artifact_path="out.txt", workspace=run_dir / "workspace"
        ),
        cloner=lambda request, run_dir: "fake-commit",
    )
    app, worker = create_app(store=store, runner=runner)
    worker.start()
    return TestClient(app), worker


def test_health_and_targets(tmp_path):
    client, worker = make_client(tmp_path)
    try:
        assert client.get("/api/health").status_code == 200
        targets = client.get("/api/targets").json()
        assert isinstance(targets, list)
    finally:
        worker.stop()


def test_run_lifecycle(tmp_path):
    client, worker = make_client(tmp_path)
    try:
        response = client.post(
            "/api/runs",
            json={
                "repo_url": "https://github.com/a/b",
                "command": ["python", "main.py"],
                "expected_artifact": "out.txt",
            },
        )
        assert response.status_code == 202
        run_id = response.json()["run_id"]

        deadline = time.time() + 5
        state = "queued"
        while time.time() < deadline and state not in {"succeeded", "failed"}:
            state = client.get(f"/api/runs/{run_id}").json()["state"]
            time.sleep(0.05)
        assert state == "succeeded"

        events = client.get(f"/api/runs/{run_id}/events?after=0").json()
        assert events[0]["sequence"] == 1
        assert client.get(f"/api/runs/{run_id}/report").status_code == 200
        assert client.get(f"/api/runs/{run_id}/patch").status_code == 200
        assert client.get(f"/api/runs/{run_id}/environment").status_code == 200
    finally:
        worker.stop()
