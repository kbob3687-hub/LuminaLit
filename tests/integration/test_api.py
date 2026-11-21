import time

import pytest
from fastapi.testclient import TestClient

from paperharness.agent.fake import FakeLLM
from paperharness.app.api import create_app
from paperharness.contracts.run import (
    EventExplanation,
    RunGuidance,
    RunRequest,
    RunResult,
    RunState,
)
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
        info = client.get(f"/api/runs/{run_id}").json()
        assert info["guidance"]["tone"] == "success"
        assert info["last_event_sequence"] == events[-1]["sequence"]
        assert "不等于完整复现论文" in info["guidance"]["detail"]
        kinds = [event["kind"] for event in events]
        assert kinds.index("plan") < kinds.index("sandbox_start") < kinds.index("sandbox_ready")
        assert kinds.index("sandbox_ready") < kinds.index("install")
        for event in events:
            EventExplanation.model_validate(event["explanation"])
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
        assert run_info["guidance"]["title"] == "任务已取消"
        assert run_info["last_event_sequence"] == 1
        assert client.get(f"/api/runs/{run_id}/events").json()[0]["explanation"]["tone"] == "warning"
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


def unstarted_app(store):
    runner = HarnessRunner(
        store=store, llm=FakeLLM(),
        sandbox_factory=lambda req, run_dir: FakeSandboxSession(workspace=run_dir / "workspace"),
        cloner=lambda req, run_dir: "fake-commit",
    )
    return create_app(store, runner)


def test_historical_events_are_projected_without_rewriting_disk(tmp_path):
    store = RunStore(tmp_path)
    run_id = store.create_run(RunRequest(repo_url="https://github.com/a/b"))
    store.append_event(run_id, RunState.DIAGNOSING, "diagnosis", "raw classification", {
        "failure_class": "missing_dependency", "attempt": 1,
    })
    store.append_event(run_id, RunState.REPAIRING, "read_file", "read source", {
        "ok": True, "request": {"path": "main.py"}, "stdout_tail": "raw source snippet",
    }, log_path="logs/action.log")
    store.append_event(run_id, RunState.REPAIRING, "install_requirement", "raw install", {
        "ok": True, "exit_code": 0, "request": {"requirement": "PyYAML"},
    })
    store.append_event(run_id, RunState.SUCCEEDED, "final", "run succeeded", {"verified": True})
    store.write_status(run_id, RunState.SUCCEEDED, {})
    result = RunResult(
        run_id=run_id, outcome="succeeded", verified=True, attempts=2,
        failure_category="missing_dependency", report_url=f"/api/runs/{run_id}/report",
    )
    store.write_result(run_id, result)
    store.write_text(run_id, "report.md", "raw report")
    store.write_text(run_id, "patch.diff", "raw patch")
    originals = {path: path.read_bytes() for path in store.run_dir(run_id).iterdir() if path.is_file()}
    raw_events = [event.model_dump(mode="json") for event in store.read_events(run_id)]

    # 使用全新的 Store，确保不是仅对内存中新事件生效。
    app, _ = unstarted_app(RunStore(tmp_path))
    client = TestClient(app)
    info = client.get(f"/api/runs/{run_id}").json()
    assert info["result"] == result.model_dump(mode="json")
    assert info["last_event_sequence"] == 4
    guidance = RunGuidance.model_validate(info["guidance"])
    assert guidance.tone == "success"
    assert "1 轮原因分析" in guidance.repair_summary
    assert "1 个修复动作" in guidance.repair_summary
    projected = client.get(f"/api/runs/{run_id}/events").json()
    assert "main.py" in projected[1]["explanation"]["title"]
    assert "PyYAML" in projected[2]["explanation"]["title"]
    for raw, projected_event in zip(raw_events, projected, strict=True):
        EventExplanation.model_validate(projected_event.pop("explanation"))
        assert projected_event == raw
    after = client.get(f"/api/runs/{run_id}/events?after=2").json()
    assert [event["sequence"] for event in after] == [3, 4]
    assert client.get(f"/api/runs/{run_id}/events?after=4").json() == []
    assert all(path.read_bytes() == contents for path, contents in originals.items())


@pytest.mark.parametrize("state, title", [
    (RunState.QUEUED, "等待开始"),
    (RunState.FAILED, "正在整理结果"),
    (RunState.SUCCEEDED, "正在整理结果"),
    (RunState.CANCELLED, "任务已取消"),
])
def test_guidance_without_result_or_events(tmp_path, state, title):
    store = RunStore(tmp_path)
    run_id = store.create_run(RunRequest(repo_url="https://github.com/a/b"))
    store.write_status(run_id, state, {})
    app, _ = unstarted_app(store)
    info = TestClient(app).get(f"/api/runs/{run_id}").json()
    assert info["result"] is None
    assert info["guidance"]["title"] == title
    assert info["last_event_sequence"] == 0
    assert isinstance(info["last_event_sequence"], int)


def test_get_run_reads_events_after_result(tmp_path, monkeypatch):
    store = RunStore(tmp_path)
    run_id = store.create_run(RunRequest(repo_url="https://github.com/a/b"))
    store.write_status(run_id, RunState.SUCCEEDED, {})
    store.write_result(run_id, RunResult(
        run_id=run_id, outcome="succeeded", verified=True, attempts=1, report_url="/report",
    ))
    read_result = store.read_result
    read_events = store.read_events
    calls = []

    def completing_read(run_id):
        calls.append("result")
        # 模拟读结果的时刻已有一个最终事件落盘。
        store.append_event(run_id, RunState.SUCCEEDED, "final", "run succeeded")
        return read_result(run_id)

    def tracking_read(run_id, after=0):
        calls.append("events")
        return read_events(run_id, after)

    # 避免 append_event 首次加载 sequence 产生额外 read_events 调用。
    store.append_event(run_id, RunState.PLANNING, "plan", "run plan resolved")
    monkeypatch.setattr(store, "read_result", completing_read)
    monkeypatch.setattr(store, "read_events", tracking_read)
    app, _ = unstarted_app(store)
    info = TestClient(app).get(f"/api/runs/{run_id}").json()
    assert calls == ["result", "events"]
    assert info["last_event_sequence"] == 2


def test_api_recovery_does_not_reuse_old_docker_failure(tmp_path):
    store = RunStore(tmp_path)
    run_id = store.create_run(RunRequest(repo_url="https://github.com/a/b"))
    store.append_event(run_id, RunState.FAILED, "final", "old failure", {
        "failure_category": "service_interrupted",
        "message": "sandbox unavailable: image paperharness/base-py3.10 not found",
    })
    store.write_status(run_id, RunState.TESTING, {})
    app, _ = unstarted_app(store)
    with TestClient(app) as client:
        info = client.get(f"/api/runs/{run_id}").json()
        assert info["result"]["failure_category"] == "service_interrupted"
        assert info["guidance"]["title"] == "运行服务中断"
        assert info["last_event_sequence"] == 2
        events = client.get(f"/api/runs/{run_id}/events").json()
        assert events[-1]["explanation"]["title"] == "运行服务中断"


@pytest.mark.parametrize("message, expected_title", [
    ("Error while fetching server API version: CreateFile failed", "无法连接 Docker 服务"),
    ("unrecognized worker failure", "运行服务发生异常"),
])
def test_worker_exception_has_api_guidance(tmp_path, message, expected_title):
    class DockerException(Exception):
        pass

    def broken_factory(request, run_dir):
        raise DockerException(message)

    store = RunStore(tmp_path)
    runner = HarnessRunner(
        store=store, llm=FakeLLM(), sandbox_factory=broken_factory,
        cloner=lambda request, run_dir: "fake-commit",
    )
    app, worker = create_app(store, runner)
    with TestClient(app) as client:
        run_id = client.post("/api/runs", json={
            "repo_url": "https://github.com/a/b", "command": ["python", "main.py"],
        }).json()["run_id"]
        worker.queue.join()
        info = client.get(f"/api/runs/{run_id}").json()
        assert info["result"]["failure_category"] == "internal_error"
        assert info["guidance"]["title"] == expected_title
        events = client.get(f"/api/runs/{run_id}/events").json()
        assert events[-1]["explanation"]["title"] == expected_title
        assert info["last_event_sequence"] == events[-1]["sequence"]
        assert "sandbox_start" in [event["kind"] for event in events]
        assert "sandbox_ready" not in [event["kind"] for event in events]
