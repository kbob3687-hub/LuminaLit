import json
import os
import tempfile
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

from paperharness.contracts.run import RunEvent, RunRequest, RunResult, RunState


class RunStore:
    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def run_dir(self, run_id: str) -> Path:
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
            events = self.read_events(run_id, after=0)
            sequence = (events[-1].sequence if events else 0) + 1
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
