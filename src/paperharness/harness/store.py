import json
import os
import re
import tempfile
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

from paperharness.contracts.run import RunEvent, RunRequest, RunResult, RunState

_VALID_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class RunStore:
    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._sequences: dict[str, int] = {}

    def run_dir(self, run_id: str) -> Path:
        if not isinstance(run_id, str) or not _VALID_RUN_ID.fullmatch(run_id) or ".." in run_id:
            raise ValueError(f"invalid run_id: {run_id!r}")
        return self.root / run_id

    def create_run(self, request: RunRequest) -> str:
        run_id = uuid.uuid4().hex
        run_dir = self.run_dir(run_id)
        run_dir.mkdir(parents=True)
        (run_dir / "logs").mkdir()
        (run_dir / "workspace").mkdir()
        self._write_json(run_dir / "request.json", request.model_dump(mode="json"))
        self.write_status(run_id, RunState.QUEUED, {})
        return run_id

    def append_event(
        self,
        run_id: str,
        state: RunState,
        kind: str,
        message: str,
        data: dict | None = None,
        log_path: str | None = None,
    ) -> RunEvent:
        with self._lock:
            last_seq = self._sequences.get(run_id)
            if last_seq is None:
                events = self.read_events(run_id, after=0)
                last_seq = events[-1].sequence if events else 0
            sequence = last_seq + 1
            event = RunEvent(
                run_id=run_id,
                sequence=sequence,
                timestamp=datetime.now(UTC),
                state=state,
                kind=kind,
                message=message,
                data=data or {},
                log_path=log_path,
            )
            path = self.run_dir(run_id) / "events.jsonl"
            with path.open("a", encoding="utf-8") as handle:
                handle.write(event.model_dump_json() + "\n")
            self._sequences[run_id] = sequence
            return event

    def read_events(self, run_id: str, after: int = 0) -> list[RunEvent]:
        with self._lock:
            path = self.run_dir(run_id) / "events.jsonl"
            if not path.exists():
                return []
            events = []
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                event = RunEvent.model_validate_json(line)
                if event.sequence > after:
                    events.append(event)
            return events

    def write_status(self, run_id: str, state: RunState, payload: dict) -> None:
        with self._lock:
            self._write_json(
                self.run_dir(run_id) / "status.json", {"state": state.value, **payload}
            )

    def read_status(self, run_id: str) -> dict | None:
        with self._lock:
            path = self.run_dir(run_id) / "status.json"
            if not path.exists():
                return None
            return self._read_json(path)

    def read_request(self, run_id: str) -> RunRequest:
        with self._lock:
            return RunRequest.model_validate(
                self._read_json(self.run_dir(run_id) / "request.json")
            )

    def recover_interrupted(self) -> list[str]:
        """服务启动时调用（设计 6.3）：非终态 run 一律标 failed/service_interrupted。

        不自动续跑、不假装任务仍在执行；幂等，终态 run 不动。
        """
        terminal = {
            RunState.SUCCEEDED.value,
            RunState.FAILED.value,
            RunState.CANCELLED.value,
        }
        recovered: list[str] = []
        if not self.root.exists():
            return recovered
        for run_dir in sorted(path for path in self.root.iterdir() if path.is_dir()):
            run_id = run_dir.name
            status = self.read_status(run_id)
            if not status or status.get("state") in terminal:
                continue
            self.write_status(
                run_id, RunState.FAILED, {"failure_category": "service_interrupted"}
            )
            self.append_event(
                run_id,
                RunState.FAILED,
                "final",
                "run failed: service_interrupted",
                {"failure_category": "service_interrupted"},
            )
            self.write_result(
                run_id,
                RunResult(
                    run_id=run_id,
                    outcome="failed",
                    verified=False,
                    attempts=1,
                    failure_category="service_interrupted",
                    report_url=f"/api/runs/{run_id}/report",
                ),
            )
            recovered.append(run_id)
        return recovered

    def write_result(self, run_id: str, result: RunResult) -> None:
        with self._lock:
            self._write_json(
                self.run_dir(run_id) / "result.json", result.model_dump(mode="json")
            )

    def read_result(self, run_id: str) -> RunResult | None:
        with self._lock:
            path = self.run_dir(run_id) / "result.json"
            if not path.exists():
                return None
            return RunResult.model_validate(self._read_json(path))

    def write_text(self, run_id: str, name: str, content: str) -> None:
        with self._lock:
            self.run_dir(run_id).joinpath(name).write_text(content, encoding="utf-8")

    def read_text(self, run_id: str, name: str) -> str | None:
        with self._lock:
            path = self.run_dir(run_id) / name
            if not path.exists():
                return None
            return path.read_text(encoding="utf-8")

    def _write_json(self, path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        os.replace(tmp_name, path)

    def _read_json(self, path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))
