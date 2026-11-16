import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse

from paperharness.app.worker import RunWorker
from paperharness.contracts.run import RunRequest, RunState
from paperharness.harness.runner import HarnessRunner
from paperharness.harness.store import RunStore

TARGETS = [
    {
        "id": "clean-example",
        "repo_url": "https://github.com/paperharness-demo/clean-example",
        "command": ["python", "main.py"],
        "expected_artifact": "out.txt",
    }
]


def create_app(
    store: RunStore,
    runner: HarnessRunner,
    targets: list[dict[str, object]] | None = None,
) -> tuple[FastAPI, RunWorker]:
    worker = RunWorker(store=store, runner=runner)

    @asynccontextmanager
    async def lifespan(_app):
        # 重启恢复：上次崩溃遗留的非终态 run 一律标 failed/service_interrupted，
        # 不留永远"运行中"的僵尸状态（设计 6.3 / DoD）。
        store.recover_interrupted()
        worker.start()
        yield
        worker.stop()

    app = FastAPI(title="PaperHarness API", lifespan=lifespan)
    target_list = targets if targets is not None else TARGETS

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/targets")
    def targets():
        return target_list

    @app.post("/api/runs", status_code=202)
    def create_run(request: RunRequest):
        if not worker.can_accept():
            raise HTTPException(status_code=429, detail={"code": "queue_full"})
        run_id = store.create_run(request)
        if not worker.submit(run_id):
            raise HTTPException(status_code=429, detail={"code": "queue_full"})
        return {"run_id": run_id, "state": "queued"}

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str):
        status = store.read_status(run_id)
        if status is None:
            raise HTTPException(status_code=404, detail={"code": "run_not_found"})
        result = store.read_result(run_id)
        return {
            "run_id": run_id,
            "state": status.get("state"),
            "result": result.model_dump(mode="json") if result else None,
        }

    @app.get("/api/runs/{run_id}/events")
    def get_events(run_id: str, after: int = 0):
        return [event.model_dump(mode="json") for event in store.read_events(run_id, after=after)]

    @app.post("/api/runs/{run_id}/cancel")
    def cancel_run(run_id: str):
        status = store.read_status(run_id)
        if status is None:
            raise HTTPException(status_code=404, detail={"code": "run_not_found"})
        state = status.get("state")
        if state == RunState.QUEUED.value:
            store.write_status(run_id, RunState.CANCELLED, {})
            store.append_event(run_id, RunState.CANCELLED, "cancel", "queued run cancelled")
            return {"run_id": run_id, "state": RunState.CANCELLED.value}
        if state in {
            RunState.SUCCEEDED.value,
            RunState.FAILED.value,
            RunState.CANCELLED.value,
        }:
            # 重复取消返回当前终态，不产生第二个任务（契约 13.6）
            return {"run_id": run_id, "state": state}
        # 运行中：终止当前容器，工作线程在最近阶段边界收敛到 cancelled
        worker.request_cancel(run_id)
        return {"run_id": run_id, "state": state}

    @app.get("/api/runs/{run_id}/report", response_class=PlainTextResponse)
    def get_report(run_id: str):
        report = store.read_text(run_id, "report.md")
        if report is None:
            raise HTTPException(status_code=404, detail={"code": "report_not_ready"})
        return report

    @app.get("/api/runs/{run_id}/patch", response_class=PlainTextResponse)
    def get_patch(run_id: str):
        return store.read_text(run_id, "patch.diff") or ""

    @app.get("/api/runs/{run_id}/environment")
    def get_environment(run_id: str):
        environment = store.read_text(run_id, "environment.json")
        if environment is None:
            raise HTTPException(status_code=404, detail={"code": "environment_not_ready"})
        return json.loads(environment)

    return app, worker
