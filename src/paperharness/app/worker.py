import queue
import threading

from paperharness.contracts.run import RunResult, RunState
from paperharness.harness.runner import HarnessRunner
from paperharness.harness.store import RunStore


class RunWorker:
    def __init__(self, store: RunStore, runner: HarnessRunner, max_queue: int = 5):
        self.store = store
        self.runner = runner
        self.max_queue = max_queue
        self.queue: queue.Queue[str] = queue.Queue(maxsize=max_queue)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def can_accept(self) -> bool:
        return self.queue.qsize() < self.max_queue

    def submit(self, run_id: str) -> bool:
        try:
            self.queue.put_nowait(run_id)
            return True
        except queue.Full:
            return False

    def request_cancel(self, run_id: str) -> bool:
        """运行中取消：转给 runner.abort（置事件 + 终止当前容器）。"""
        return self.runner.abort(run_id)

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                run_id = self.queue.get(timeout=0.05)
            except queue.Empty:
                continue
            try:
                status = self.store.read_status(run_id)
                if status and status.get("state") != RunState.CANCELLED.value:
                    self.runner.run(run_id, self.store.read_request(run_id))
            except Exception as exc:  # noqa: BLE001 - 唯一工作线程绝不能被杀死（设计 18.5）
                self._mark_internal_error(run_id, exc)
            finally:
                self.queue.task_done()

    def _mark_internal_error(self, run_id: str, exc: Exception) -> None:
        """兜底：runner 未分类的异常 → failed/internal_error，不留永远"运行中"。

        只记异常类型和短消息，不带 traceback、不泄露内部路径（契约 13.10）。
        """
        message = f"{type(exc).__name__}: {exc}"[:2000]
        try:
            self.store.write_status(
                run_id, RunState.FAILED, {"failure_category": "internal_error"}
            )
            self.store.append_event(
                run_id,
                RunState.FAILED,
                "internal_error",
                message,
                {"failure_category": "internal_error"},
            )
            self.store.write_result(
                run_id,
                RunResult(
                    run_id=run_id,
                    outcome="failed",
                    verified=False,
                    attempts=1,
                    failure_category="internal_error",
                    patch_url=f"/api/runs/{run_id}/patch",
                    report_url=f"/api/runs/{run_id}/report",
                ),
            )
            self.store.write_text(
                run_id,
                "report.md",
                "# Report\n\n"
                f"outcome=failed\nverified=False\n"
                f"failure_category=internal_error\n\n{message}\n",
            )
        except Exception:  # noqa: BLE001, S110 - 存储本身故障时只能放弃
            pass
