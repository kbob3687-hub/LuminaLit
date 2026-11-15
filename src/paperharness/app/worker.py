import queue
import threading

from paperharness.contracts.run import RunState
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
            finally:
                self.queue.task_done()
