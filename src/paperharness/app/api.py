import hashlib
import json
import os
import shutil
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, Response

from paperharness.app.guidance import build_run_guidance, explain_event
from paperharness.app.repair_case import featured_case, handoff_file, repair_evidence
from paperharness.app.worker import RunWorker
from paperharness.contracts.bundle import BundleResult
from paperharness.contracts.preflight import (
    ConfirmedRunRequest,
    DirectRunRequest,
    PreflightRequest,
    PreflightResult,
)
from paperharness.contracts.run import RunResult, RunState
from paperharness.harness.preflight import PreflightError, PreflightService
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
    preflights = PreflightService(store, runner.targets)
    admission_lock = threading.Lock()

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

    @app.get("/api/cases/lorenz-repair")
    def recorded_repair_case():
        return featured_case(store)

    @app.get("/api/runs/{run_id}/repair-evidence")
    def get_repair_evidence(run_id: str):
        return repair_evidence(store, run_id)

    @app.get("/api/runs/{run_id}/handoff")
    def get_handoff(run_id: str):
        path, digest = handoff_file(store, run_id)
        return FileResponse(path, media_type="application/zip", filename="lorenz-repair-handoff.zip",
                            headers={"X-Bundle-SHA256": digest})

    @app.post("/api/preflights", response_model=PreflightResult, status_code=201)
    def create_preflight(request: PreflightRequest):
        try:
            return preflights.create(request).view
        except PreflightError as exc:
            raise HTTPException(
                status_code=exc.status_code, detail={"code": exc.code, "message": exc.message}
            ) from exc

    @app.get("/api/preflights/{preflight_id}", response_model=PreflightResult)
    def get_preflight(preflight_id: str):
        try:
            return preflights.read(preflight_id).view
        except PreflightError as exc:
            raise HTTPException(
                status_code=exc.status_code, detail={"code": exc.code, "message": exc.message}
            ) from exc

    @app.post("/api/runs", status_code=202)
    def create_run(request: ConfirmedRunRequest | DirectRunRequest):
        with admission_lock:
            record = None
            try:
                if isinstance(request, ConfirmedRunRequest):
                    record = preflights.read(request.preflight_id)
                    if not record.check_digest(request.confirmation_digest):
                        raise PreflightError("confirmation_mismatch", "确认摘要不匹配，请重新体检。")
                    if record.view.run_id:
                        status = store.read_status(record.view.run_id)
                        if status is not None:
                            return {"run_id": record.view.run_id, "state": status["state"]}
                    elif datetime.now(UTC) >= record.view.expires_at:
                        raise PreflightError("preflight_expired", "体检记录已过期，请重新体检后确认。")
                if not worker.can_accept():
                    raise HTTPException(status_code=429, detail={"code": "queue_full"})
                if record is not None:
                    if record.view.run_id is None:
                        # 先原子预留身份；后续写盘失败只能补齐该 ID，不能创建另一任务。
                        record.view.run_id = uuid.uuid4().hex
                        preflights.save(record)
                    run_id = store.create_run(
                        record.request,
                        run_id=record.view.run_id,
                        confirmation=record.model_dump(mode="json"),
                    )
                else:
                    run_id = store.create_run(request)
                if not worker.submit(run_id):
                    try:
                        if record is not None:
                            record.view.run_id = None
                            preflights.save(record)
                    finally:
                        # 清绑定失败也不能留下一个从未提交的 queued 完成标记。
                        shutil.rmtree(store.run_dir(run_id), ignore_errors=True)
                    raise HTTPException(status_code=429, detail={"code": "queue_full"})
                return {"run_id": run_id, "state": "queued"}
            except PreflightError as exc:
                raise HTTPException(
                    status_code=exc.status_code,
                    detail={"code": exc.code, "message": exc.message},
                ) from exc
            except OSError as exc:
                message = "运行记录读写暂不可用，无法确认本次提交结果，请稍后重试。"
                if isinstance(request, ConfirmedRunRequest):
                    message = (
                        "运行记录读写暂不可用，无法确认本次提交结果；"
                        "请使用同一体检记录和确认摘要重试，不要重新体检。"
                    )
                raise HTTPException(
                    status_code=503, detail={"code": "storage_unavailable", "message": message}
                ) from exc

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str):
        status = _read_status_or_404(run_id)
        result = store.read_result(run_id)
        # 先读结果再读事件，让前端在收到终态结果后核对已消费的事件序号。
        events = store.read_events(run_id)
        return {
            "run_id": run_id,
            "state": status.get("state"),
            "result": result.model_dump(mode="json") if result else None,
            "guidance": build_run_guidance(
                status.get("state"), result, events
            ).model_dump(mode="json"),
            "last_event_sequence": max((event.sequence for event in events), default=0),
        }

    @app.get("/api/runs/{run_id}/events")
    def get_events(run_id: str, after: int = 0):
        try:
            events = store.read_events(run_id, after=after)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail={"code": "run_not_found"}) from exc
        result = store.read_result(run_id)
        return [
            {
                **event.model_dump(mode="json"),
                "explanation": explain_event(event, result).model_dump(mode="json"),
            }
            for event in events
        ]

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

    @app.get("/api/runs/{run_id}/verification")
    def get_verification(run_id: str):
        _read_status_or_404(run_id)
        result = store.read_result(run_id)
        if result is None or result.verification is None:
            raise HTTPException(status_code=404, detail={"code": "verification_not_ready"})
        return result.verification

    @app.get("/api/runs/{run_id}/verification/input")
    def get_verification_input(run_id: str):
        _read_status_or_404(run_id)
        result = store.read_result(run_id)
        verification = result.verification if result else None
        if verification is None or verification.evidence_url is None:
            raise HTTPException(status_code=404, detail={"code": "verification_evidence_not_ready"})
        path = store.run_dir(run_id) / "verification-input.json"
        if not path.is_file() or path.is_symlink():
            raise HTTPException(status_code=404, detail={"code": "verification_evidence_not_ready"})
        with path.open("rb") as handle:
            raw = handle.read(32 * 1024 + 1)
        if len(raw) > 32 * 1024 or hashlib.sha256(raw).hexdigest() != verification.artifact_sha256:
            raise HTTPException(status_code=409, detail={"code": "verification_evidence_mismatch"})
        return Response(raw, media_type="application/octet-stream", headers={
            "Content-Disposition": 'attachment; filename="verification-input.json"',
            "X-Content-Type-Options": "nosniff",
        })

    @app.get("/api/runs/{run_id}/bundle")
    def get_bundle(run_id: str):
        _read_status_or_404(run_id)
        raw = store.read_text(run_id, "bundle.json")
        if raw is None:
            raise HTTPException(status_code=404, detail={"code": "bundle_not_ready"})
        bundle = BundleResult.model_validate_json(raw)
        if bundle.integrity != "complete":
            # 材料不完整/被阻止：只给缺项清单，不提供误导性的部分下载。
            raise HTTPException(
                status_code=409,
                detail={
                    "code": f"bundle_{bundle.integrity}",
                    "items": [item.model_dump(mode="json") for item in bundle.items
                              if item.status != "included"],
                },
            )
        path = store.run_dir(run_id) / "reproduce.zip"
        if not path.is_file() or path.is_symlink():
            raise HTTPException(status_code=409, detail={"code": "bundle_incomplete"})
        raw = path.read_bytes()
        # 落盘后仍可能被替换或截断：只提供与 bundle.json 记录一致的原始字节。
        # total_bytes 是材料字节合计，不是压缩包大小，完整性以压缩包哈希为准。
        if bundle.sha256 is None or hashlib.sha256(raw).hexdigest() != bundle.sha256:
            raise HTTPException(status_code=409, detail={"code": "bundle_corrupted"})
        return Response(
            raw,
            media_type="application/zip",
            headers={
                "Content-Disposition": 'attachment; filename="reproduce.zip"',
                "X-Content-Type-Options": "nosniff",
                "X-Bundle-Integrity": "complete",
                "X-Bundle-SHA256": bundle.sha256,
            },
        )

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
