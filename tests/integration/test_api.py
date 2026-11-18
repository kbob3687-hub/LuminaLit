import time

from fastapi.testclient import TestClient

from paperharness.agent.fake import FakeLLM
from paperharness.app.api import create_app
from paperharness.harness.runner import HarnessRunner
from paperharness.harness.store import RunStore
from paperharness.sandbox.fake import FakeSandboxSession


def make_client(tmp_path):
    store = RunStore(tmp_path)

    def factory(request, run_dir):
        (run_dir / "workspace").mkdir(parents=True, exist_ok=True)
        (run_dir / "workspace" / "requirements.txt").write_text("", encoding="utf-8")
        return FakeSandboxSession(
            artifact_path="out.txt", workspace=run_dir / "workspace"
        )

    runner = HarnessRunner(
        store=store,
        llm=FakeLLM(),
        sandbox_factory=factory,
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


def test_cancel_queued_run(tmp_path):
    # worker 不启动（不进 TestClient 的 with），run 停留在 queued 后取消
    store = RunStore(tmp_path)

    def factory(request, run_dir):
        return FakeSandboxSession(workspace=run_dir / "workspace")

    runner = HarnessRunner(
        store=store, llm=FakeLLM(), sandbox_factory=factory,
        cloner=lambda request, run_dir: "fake-commit",
    )
    app, worker = create_app(store=store, runner=runner)
    client = TestClient(app)
    try:
        response = client.post("/api/runs", json={"repo_url": "https://github.com/a/b"})
        assert response.status_code == 202
        run_id = response.json()["run_id"]
        assert client.get(f"/api/runs/{run_id}").json()["state"] == "queued"

        cancelled = client.post(f"/api/runs/{run_id}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["state"] == "cancelled"
        assert client.get(f"/api/runs/{run_id}").json()["state"] == "cancelled"
    finally:
        worker.stop()


def test_cancel_running_run_converges_to_cancelled(tmp_path):
    store = RunStore(tmp_path)

    class SlowSandbox(FakeSandboxSession):
        def execute(self, command, timeout_seconds):
            time.sleep(0.5)
            return super().execute(command, timeout_seconds)

    def factory(request, run_dir):
        (run_dir / "workspace").mkdir(parents=True, exist_ok=True)
        (run_dir / "workspace" / "requirements.txt").write_text("", encoding="utf-8")
        return SlowSandbox(artifact_path="out.txt", workspace=run_dir / "workspace")

    runner = HarnessRunner(
        store=store, llm=FakeLLM(), sandbox_factory=factory,
        cloner=lambda request, run_dir: "fake-commit",
    )
    app, _worker = create_app(store=store, runner=runner)
    with TestClient(app) as client:
        response = client.post(
            "/api/runs",
            json={
                "repo_url": "https://github.com/a/b",
                "command": ["python", "main.py"],
            },
        )
        run_id = response.json()["run_id"]

        state = "queued"
        deadline = time.time() + 10
        while time.time() < deadline:
            state = client.get(f"/api/runs/{run_id}").json()["state"]
            if state in {"preparing", "planning", "installing", "testing"}:
                break
            time.sleep(0.02)
        assert state != "queued", f"run never started, state={state}"

        cancelled = client.post(f"/api/runs/{run_id}/cancel")
        assert cancelled.status_code == 200

        deadline = time.time() + 10
        while time.time() < deadline:
            state = client.get(f"/api/runs/{run_id}").json()["state"]
            if state == "cancelled":
                break
            time.sleep(0.05)
        assert state == "cancelled", f"expected cancelled, got {state}"

        result = client.get(f"/api/runs/{run_id}").json()["result"]
        assert result["outcome"] == "cancelled"


def test_cors_preflight_allows_configured_origin(tmp_path):
    client, worker = make_client(tmp_path)
    try:
        resp = client.options(
            "/api/runs",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert resp.status_code in (200, 204)
        assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"
    finally:
        worker.stop()


def test_cancel_queued_run_writes_complete_artifacts(tmp_path):
    store = RunStore(tmp_path)
    runner = HarnessRunner(
        store=store,
        llm=FakeLLM(),
        sandbox_factory=lambda req, run_dir: FakeSandboxSession(workspace=run_dir / "workspace"),
        cloner=lambda req, run_dir: "fake-commit",
    )
    app, worker = create_app(store=store, runner=runner)
    client = TestClient(app)
    try:
        run_id = client.post("/api/runs", json={"repo_url": "https://github.com/a/b"}).json()["run_id"]
        cancelled = client.post(f"/api/runs/{run_id}/cancel")
        assert cancelled.status_code == 200

        run_info = client.get(f"/api/runs/{run_id}").json()
        assert run_info["state"] == "cancelled"
        assert run_info["result"]["outcome"] == "cancelled"
        assert client.get(f"/api/runs/{run_id}/report").status_code == 200
        assert client.get(f"/api/runs/{run_id}/environment").status_code == 200
        assert client.get(f"/api/runs/{run_id}/patch").status_code == 200
    finally:
        worker.stop()


def test_invalid_run_id_traversal_returns_404(tmp_path):
    client, worker = make_client(tmp_path)
    try:
        assert client.get("/api/runs/..").status_code == 404
        assert client.get("/api/runs/bad..id").status_code == 404
        assert client.get("/api/runs/bad..id/events").status_code == 404
        assert client.get("/api/runs/bad..id/report").status_code == 404
        assert client.get("/api/runs/bad..id/patch").status_code == 404
        assert client.get("/api/runs/bad..id/environment").status_code == 404
    finally:
        worker.stop()
