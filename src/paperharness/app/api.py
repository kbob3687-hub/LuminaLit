import json
import os
import shutil
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from paperharness.app.worker import RunWorker
from paperharness.contracts.run import RunRequest, RunResult, RunState
from paperharness.harness.runner import HarnessRunner
from paperharness.harness.store import RunStore

DEFAULT_CORS_ORIGINS = (
    "http://localhost:5173,http://127.0.0.1:5173,"
    "http://localhost:4173,http://127.0.0.1:4173"
)

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
    cors_origins = [
        origin.strip()
        for origin in os.environ.get("PAPERHARNESS_CORS_ORIGINS", DEFAULT_CORS_ORIGINS).split(",")
        if origin.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )
    target_list = targets if targets is not None else TARGETS

    def _read_status_or_404(run_id: str) -> dict:
        try:
            status = store.read_status(run_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail={"code": "run_not_found"}) from exc
        if status is None:
            raise HTTPException(status_code=404, detail={"code": "run_not_found"})
        return status

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
            shutil.rmtree(store.run_dir(run_id), ignore_errors=True)
            raise HTTPException(status_code=429, detail={"code": "queue_full"})
        return {"run_id": run_id, "state": "queued"}

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str):
        status = _read_status_or_404(run_id)
        result = store.read_result(run_id)
        return {
            "run_id": run_id,
            "state": status.get("state"),
            "result": result.model_dump(mode="json") if result else None,
        }

    @app.get("/api/runs/{run_id}/events")
    def get_events(run_id: str, after: int = 0):
        try:
            events = store.read_events(run_id, after=after)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail={"code": "run_not_found"}) from exc
        return [event.model_dump(mode="json") for event in events]

    @app.post("/api/runs/{run_id}/cancel")
    def cancel_run(run_id: str):
        status = _read_status_or_404(run_id)
        state = status.get("state")
        if state == RunState.QUEUED.value:
            worker.request_cancel(run_id)
            store.write_status(run_id, RunState.CANCELLED, {})
            store.append_event(run_id, RunState.CANCELLED, "cancel", "queued run cancelled")
            store.write_text(run_id, "patch.diff", "")
            store.write_text(run_id, "environment.json", "{}")
            store.write_text(
                run_id, "report.md", "# Report\n\noutcome=cancelled\nverified=False\n"
            )
            store.write_result(
                run_id,
                RunResult(
                    run_id=run_id,
                    outcome="cancelled",
                    verified=False,
                    attempts=0,
                    patch_url=f"/api/runs/{run_id}/patch",
                    environment_url=f"/api/runs/{run_id}/environment",
                    report_url=f"/api/runs/{run_id}/report",
                ),
            )
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
        try:
            report = store.read_text(run_id, "report.md")
        except ValueError as exc:
            raise HTTPException(status_code=404, detail={"code": "run_not_found"}) from exc
        if report is None:
            raise HTTPException(status_code=404, detail={"code": "report_not_ready"})
        return report

    @app.get("/api/runs/{run_id}/patch", response_class=PlainTextResponse)
    def get_patch(run_id: str):
        try:
            patch = store.read_text(run_id, "patch.diff")
        except ValueError as exc:
            raise HTTPException(status_code=404, detail={"code": "run_not_found"}) from exc
        return patch or ""

    @app.get("/api/runs/{run_id}/environment")
    def get_environment(run_id: str):
        try:
            environment = store.read_text(run_id, "environment.json")
        except ValueError as exc:
            raise HTTPException(status_code=404, detail={"code": "run_not_found"}) from exc
        if environment is None:
            raise HTTPException(status_code=404, detail={"code": "environment_not_ready"})
        return json.loads(environment)

    return app, worker
