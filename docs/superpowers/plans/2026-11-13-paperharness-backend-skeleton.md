# PaperHarness Backend Skeleton Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a testable PaperHarness backend skeleton with contracts, event storage, fake sandbox/LLM, no-repair harness runner, and public API endpoints.

**Architecture:** A single FastAPI process owns a bounded in-memory queue and one worker thread. The worker drives a deterministic HarnessRunner that consumes SandboxSession and LLMPort protocols. All run evidence is written to a filesystem run directory; no database is used.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, pytest, ruff, Docker Python SDK later, official OpenAI SDK later.

**Spec:** `docs/superpowers/specs/2026-11-13-paperharness-design.md`

## Global Constraints

- Python 3.11 is the backend language.
- Do not use a database; run evidence lives under `runs/`.
- Do not use WebSocket; clients poll events by sequence.
- Uvicorn must run with one process and one worker thread for execution.
- `command` fields are argument lists, never shell strings.
- Default `max_repairs` is 3; skeleton does not implement repair yet.
- No LangChain, AutoGen, CrewAI, Celery, Redis, Kafka, or Kubernetes.
- Do not create git commits unless the user explicitly asks; leave skeleton changes uncommitted.
- Tests must run with `pytest`; lint with `ruff`.
- Do not expose API keys, tracebacks, or host absolute paths in API responses.

---

### Task 1: Project Packaging and Test Tooling

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `src/paperharness/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/test_packaging.py`

**Interfaces:**
- Consumes: none.
- Produces: importable package root `paperharness`, pytest and ruff configuration.

- [ ] **Step 1: Write the failing test**

```python
import importlib.metadata


def test_package_metadata_exists():
    version = importlib.metadata.version("paperharness")
    assert version == "0.1.0"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_packaging.py -v`
Expected: FAIL because package metadata is not installed.

- [ ] **Step 3: Write minimal implementation**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "paperharness"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "fastapi>=0.110",
  "pydantic>=2.6",
]

[project.optional-dependencies]
dev = [
  "pytest>=8",
  "ruff>=0.4",
  "httpx>=0.27",
]

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.ruff]
line-length = 100
```

```text
__pycache__/
*.pyc
.pytest_cache/
.ruff_cache/
runs/
.env
```

```python
__version__ = "0.1.0"
```

- [ ] **Step 4: Install editable package and run test**

Run: `pip install -e ".[dev]"`
Run: `pytest tests/test_packaging.py -v`
Expected: PASS.

- [ ] **Step 5: Leave changes uncommitted**

No git commit unless the user explicitly asks.

---

### Task 2: Core Contracts and Validation

**Files:**
- Create: `src/paperharness/contracts/__init__.py`
- Create: `src/paperharness/contracts/run.py`
- Test: `tests/contract/test_run_contracts.py`

**Interfaces:**
- Consumes: none.
- Produces: `RunRequest`, `RunPlan`, `RunState`, `RunEvent`, `ExecResult`, `Observation`, `RunResult`, `ToolAction`.

- [ ] **Step 1: Write the failing test**

```python
import pytest
from pydantic import ValidationError

from paperharness.contracts.run import RunRequest, RunState


def test_run_request_defaults():
    request = RunRequest(repo_url="https://github.com/example/repo")
    assert request.max_repairs == 3
    assert request.command is None


def test_run_request_rejects_bad_repair_budget():
    with pytest.raises(ValidationError):
        RunRequest(repo_url="https://github.com/example/repo", max_repairs=9)


def test_run_state_values():
    assert RunState.SUCCEEDED.value == "succeeded"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/contract/test_run_contracts.py -v`
Expected: FAIL with import error.

- [ ] **Step 3: Write minimal implementation**

```python
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, JsonValue


class RunRequest(BaseModel):
    repo_url: str
    revision: str | None = None
    command: list[str] | None = None
    expected_artifact: str | None = None
    python_version: Literal["3.10", "3.11"] | None = None
    timeout_seconds: int = Field(default=300, ge=30, le=900)
    max_repairs: int = Field(default=3, ge=0, le=5)


class RunPlan(BaseModel):
    resolved_revision: str
    python_version: Literal["3.10", "3.11"]
    command: list[str]
    expected_artifact: str | None = None
    source: Literal["user", "target_manifest", "repository_analysis"]


class RunState(StrEnum):
    QUEUED = "queued"
    PREPARING = "preparing"
    PLANNING = "planning"
    INSTALLING = "installing"
    TESTING = "testing"
    DIAGNOSING = "diagnosing"
    REPAIRING = "repairing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunEvent(BaseModel):
    run_id: str
    sequence: int
    timestamp: datetime
    state: RunState
    kind: str
    message: str
    data: dict[str, JsonValue] = Field(default_factory=dict)
    log_path: str | None = None


class ExecResult(BaseModel):
    exit_code: int | None
    timed_out: bool
    duration_ms: int
    stdout_path: str
    stderr_path: str
    stdout_tail: str = ""
    stderr_tail: str = ""


class Observation(BaseModel):
    ok: bool
    action: str
    summary: str
    exit_code: int | None = None
    timed_out: bool = False
    stdout_tail: str = ""
    stderr_tail: str = ""
    log_path: str | None = None


class RunResult(BaseModel):
    run_id: str
    outcome: Literal[
        "succeeded", "failed", "budget_exceeded", "unrepairable", "cancelled"
    ]
    verified: bool
    attempts: int
    failure_category: str | None = None
    resolved_revision: str | None = None
    patch_url: str | None = None
    environment_url: str | None = None
    report_url: str


class ReadFileAction(BaseModel):
    action: Literal["read_file"]
    path: str
    start_line: int = 1
    line_count: int = Field(default=100, le=200)


class SearchTextAction(BaseModel):
    action: Literal["search_text"]
    query: str
    path: str = "."
    max_results: int = Field(default=20, le=50)


class ReplaceExactAction(BaseModel):
    action: Literal["replace_exact"]
    path: str
    old: str
    new: str


class InstallRequirementAction(BaseModel):
    action: Literal["install_requirement"]
    requirement: str


class SetEnvironmentAction(BaseModel):
    action: Literal["set_environment"]
    name: str
    value: str


class RunSmokeTestAction(BaseModel):
    action: Literal["run_smoke_test"]


class FinishRepairAction(BaseModel):
    action: Literal["finish_repair"]
    summary: str


ToolAction = Annotated[
    Union[
        ReadFileAction,
        SearchTextAction,
        ReplaceExactAction,
        InstallRequirementAction,
        SetEnvironmentAction,
        RunSmokeTestAction,
        FinishRepairAction,
    ],
    Field(discriminator="action"),
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/contract/test_run_contracts.py -v`
Expected: PASS.

- [ ] **Step 5: Leave changes uncommitted**

No git commit unless the user explicitly asks.

---

### Task 3: Run Event and Artifact Store

**Files:**
- Create: `src/paperharness/harness/__init__.py`
- Create: `src/paperharness/harness/store.py`
- Test: `tests/unit/test_store.py`

**Interfaces:**
- Consumes: `RunEvent`, `RunRequest`, `RunResult`, `RunState`.
- Produces: `RunStore.create_run`, `append_event`, `read_events`, `write_status`, `read_status`, `write_result`, `read_result`, `write_text`, `read_text`.

- [ ] **Step 1: Write the failing test**

```python
from paperharness.contracts.run import RunRequest, RunState
from paperharness.harness.store import RunStore


def test_store_appends_and_reads_events(tmp_path):
    store = RunStore(tmp_path)
    run_id = store.create_run(RunRequest(repo_url="https://github.com/a/b"))
    store.append_event(run_id, RunState.QUEUED, "queued", "task queued")
    store.append_event(run_id, RunState.PREPARING, "preparing", "workspace ready")

    events = store.read_events(run_id, after=0)
    assert [event.sequence for event in events] == [1, 2]

    later = store.read_events(run_id, after=1)
    assert [event.sequence for event in later] == [2]


def test_store_status_is_atomic_and_readable(tmp_path):
    store = RunStore(tmp_path)
    run_id = store.create_run(RunRequest(repo_url="https://github.com/a/b"))
    store.write_status(run_id, RunState.TESTING, {"attempts": 1})
    status = store.read_status(run_id)
    assert status["state"] == "testing"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_store.py -v`
Expected: FAIL with import error.

- [ ] **Step 3: Write minimal implementation**

```python
import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from paperharness.contracts.run import RunEvent, RunRequest, RunResult, RunState


class RunStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def run_dir(self, run_id: str) -> Path:
        return self.root / run_id

    def create_run(self, request: RunRequest) -> str:
        run_id = uuid.uuid4().hex
        run_dir = self.run_dir(run_id)
        run_dir.mkdir(parents=True)
        (run_dir / "logs").mkdir()
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
        events = self.read_events(run_id, after=0)
        sequence = (events[-1].sequence if events else 0) + 1
        event = RunEvent(
            run_id=run_id,
            sequence=sequence,
            timestamp=datetime.now(timezone.utc),
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
        data = {"state": state.value, **payload}
        self._write_json(self.run_dir(run_id) / "status.json", data)

    def read_status(self, run_id: str) -> dict:
        return self._read_json(self.run_dir(run_id) / "status.json")

    def write_result(self, run_id: str, result: RunResult) -> None:
        self._write_json(
            self.run_dir(run_id) / "result.json", result.model_dump(mode="json")
        )

    def read_result(self, run_id: str) -> RunResult | None:
        path = self.run_dir(run_id) / "result.json"
        if not path.exists():
            return None
        return RunResult.model_validate(self._read_json(path))

    def write_text(self, run_id: str, name: str, content: str) -> None:
        path = self.run_dir(run_id) / name
        path.write_text(content, encoding="utf-8")

    def read_text(self, run_id: str, name: str) -> str | None:
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_store.py -v`
Expected: PASS.

- [ ] **Step 5: Leave changes uncommitted**

No git commit unless the user explicitly asks.

---

### Task 4: Sandbox Protocol and Fake Sandbox

**Files:**
- Create: `src/paperharness/sandbox/__init__.py`
- Create: `src/paperharness/sandbox/base.py`
- Create: `src/paperharness/sandbox/fake.py`
- Test: `tests/unit/test_fake_sandbox.py`

**Interfaces:**
- Consumes: `ExecResult`.
- Produces: `SandboxSession`, `FakeSandboxSession`.

- [ ] **Step 1: Write the failing test**

```python
from paperharness.contracts.run import ExecResult
from paperharness.sandbox.fake import FakeSandboxSession


def test_fake_sandbox_returns_scripted_results(tmp_path):
    first = ExecResult(
        exit_code=1,
        timed_out=False,
        duration_ms=10,
        stdout_path="logs/a.log",
        stderr_path="logs/a.err",
        stderr_tail="ModuleNotFoundError",
    )
    session = FakeSandboxSession([first], artifact_path="out.txt")
    result = session.execute(["python", "main.py"], timeout_seconds=30)
    assert result.exit_code == 1
    assert session.network_enabled is False
    assert session.closed is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_fake_sandbox.py -v`
Expected: FAIL with import error.

- [ ] **Step 3: Write minimal implementation**

```python
from typing import Protocol

from paperharness.contracts.run import ExecResult


class SandboxSession(Protocol):
    def execute(self, command: list[str], timeout_seconds: int) -> ExecResult: ...

    def set_network(self, enabled: bool) -> None: ...

    def export_diff(self) -> str: ...

    def close(self) -> None: ...
```

```python
from pathlib import Path

from paperharness.contracts.run import ExecResult


class FakeSandboxSession:
    def __init__(
        self,
        results: list[ExecResult] | None = None,
        artifact_path: str | None = None,
        workspace: Path | None = None,
    ):
        self.results = list(results or [])
        self.artifact_path = artifact_path
        self.workspace = workspace or Path(".")
        self.network_enabled = True
        self.closed = False
        self.commands: list[list[str]] = []

    def execute(self, command: list[str], timeout_seconds: int) -> ExecResult:
        self.commands.append(command)
        if self.results:
            result = self.results.pop(0)
        else:
            result = ExecResult(
                exit_code=0,
                timed_out=False,
                duration_ms=1,
                stdout_path="logs/fake.log",
                stderr_path="logs/fake.err",
            )
        if result.exit_code == 0 and self.artifact_path:
            artifact = self.workspace / self.artifact_path
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text("fake artifact", encoding="utf-8")
        return result

    def set_network(self, enabled: bool) -> None:
        self.network_enabled = enabled

    def export_diff(self) -> str:
        return ""

    def close(self) -> None:
        self.closed = True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_fake_sandbox.py -v`
Expected: PASS.

- [ ] **Step 5: Leave changes uncommitted**

No git commit unless the user explicitly asks.

---

### Task 5: No-Repair Harness Runner

**Files:**
- Create: `src/paperharness/agent/__init__.py`
- Create: `src/paperharness/agent/base.py`
- Create: `src/paperharness/agent/fake.py`
- Create: `src/paperharness/harness/runner.py`
- Test: `tests/unit/test_runner.py`

**Interfaces:**
- Consumes: `RunStore`, `SandboxSession`, contracts.
- Produces: `LLMPort`, `FakeLLM`, `HarnessRunner.run`.

- [ ] **Step 1: Write the failing test**

```python
from paperharness.agent.fake import FakeLLM
from paperharness.contracts.run import ExecResult, RunRequest, RunState
from paperharness.harness.runner import HarnessRunner
from paperharness.harness.store import RunStore
from paperharness.sandbox.fake import FakeSandboxSession


def make_runner(tmp_path, session):
    store = RunStore(tmp_path)
    return HarnessRunner(store=store, llm=FakeLLM(), sandbox_factory=lambda request: session), store


def test_runner_success_verifies_artifact(tmp_path):
    session = FakeSandboxSession(artifact_path="out.txt")
    runner, store = make_runner(tmp_path, session)
    request = RunRequest(
        repo_url="https://github.com/a/b",
        command=["python", "main.py"],
        expected_artifact="out.txt",
    )
    run_id = store.create_run(request)
    result = runner.run(run_id, request)
    assert result.verified is True
    assert result.outcome == "succeeded"
    assert session.network_enabled is False
    assert session.closed is True


def test_runner_failure_is_not_verified(tmp_path):
    failing = ExecResult(
        exit_code=1,
        timed_out=False,
        duration_ms=5,
        stdout_path="logs/t.log",
        stderr_path="logs/t.err",
        stderr_tail="boom",
    )
    session = FakeSandboxSession([failing])
    runner, store = make_runner(tmp_path, session)
    request = RunRequest(repo_url="https://github.com/a/b", command=["python", "main.py"])
    run_id = store.create_run(request)
    result = runner.run(run_id, request)
    assert result.verified is False
    assert result.outcome == "failed"
    assert store.read_status(run_id)["state"] == RunState.FAILED.value
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_runner.py -v`
Expected: FAIL with import error.

- [ ] **Step 3: Write minimal implementation**

```python
from typing import Protocol

from paperharness.contracts.run import RunPlan, RunRequest


class LLMPort(Protocol):
    def propose_run_plan(self, request: RunRequest) -> RunPlan: ...
```

```python
from paperharness.contracts.run import RunPlan, RunRequest


class FakeLLM:
    def propose_run_plan(self, request: RunRequest) -> RunPlan:
        command = request.command or ["python", "main.py"]
        return RunPlan(
            resolved_revision=request.revision or "fake-commit",
            python_version=request.python_version or "3.11",
            command=command,
            expected_artifact=request.expected_artifact,
            source="user" if request.command else "repository_analysis",
        )
```

```python
from collections.abc import Callable

from paperharness.contracts.run import RunRequest, RunResult, RunState
from paperharness.harness.store import RunStore
from paperharness.sandbox.base import SandboxSession

INSTALL_COMMAND = ["python", "-m", "pip", "install", "-r", "requirements.txt"]


class HarnessRunner:
    def __init__(
        self,
        store: RunStore,
        llm,
        sandbox_factory: Callable[[RunRequest], SandboxSession],
    ):
        self.store = store
        self.llm = llm
        self.sandbox_factory = sandbox_factory

    def run(self, run_id: str, request: RunRequest) -> RunResult:
        session = self.sandbox_factory(request)
        try:
            self.store.append_event(run_id, RunState.PREPARING, "preparing", "workspace prepared")
            self.store.write_status(run_id, RunState.PREPARING, {})

            self.store.write_status(run_id, RunState.PLANNING, {})
            plan = self.llm.propose_run_plan(request)
            self.store.write_text(run_id, "run_plan.json", plan.model_dump_json(indent=2))
            self.store.append_event(run_id, RunState.PLANNING, "plan", "run plan resolved", plan.model_dump(mode="json"))

            self.store.write_status(run_id, RunState.INSTALLING, {})
            session.set_network(True)
            install = session.execute(INSTALL_COMMAND, timeout_seconds=request.timeout_seconds)
            self.store.append_event(
                run_id,
                RunState.INSTALLING,
                "install",
                "dependency install finished",
                {"exit_code": install.exit_code},
            )

            self.store.write_status(run_id, RunState.TESTING, {})
            session.set_network(False)
            test = session.execute(plan.command, timeout_seconds=request.timeout_seconds)
            self.store.append_event(
                run_id,
                RunState.TESTING,
                "test",
                "smoke test finished",
                {"exit_code": test.exit_code, "stderr_tail": test.stderr_tail},
            )

            verified = test.exit_code == 0 and not test.timed_out
            if verified and plan.expected_artifact:
                verified = (session.workspace / plan.expected_artifact).exists()

            outcome = "succeeded" if verified else "failed"
            state = RunState.SUCCEEDED if verified else RunState.FAILED
            failure_category = None if verified else "smoke_test_failed"

            patch = session.export_diff()
            self.store.write_text(run_id, "patch.diff", patch)
            self.store.write_text(run_id, "report.md", f"# Report\n\nverified={verified}\n")
            self.store.write_text(
                run_id,
                "environment.json",
                f'{{"resolved_revision": "{plan.resolved_revision}"}}',
            )

            result = RunResult(
                run_id=run_id,
                outcome=outcome,
                verified=verified,
                attempts=1,
                failure_category=failure_category,
                resolved_revision=plan.resolved_revision,
                patch_url=f"/api/runs/{run_id}/patch",
                environment_url=f"/api/runs/{run_id}/environment",
                report_url=f"/api/runs/{run_id}/report",
            )
            self.store.write_status(run_id, state, {"verified": verified})
            self.store.append_event(run_id, state, "final", f"run {outcome}", {"verified": verified})
            self.store.write_result(run_id, result)
            return result
        finally:
            session.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_runner.py -v`
Expected: PASS.

- [ ] **Step 5: Leave changes uncommitted**

No git commit unless the user explicitly asks.

---

### Task 6: API Queue, Worker, and Endpoints

**Files:**
- Create: `src/paperharness/app/__init__.py`
- Create: `src/paperharness/app/worker.py`
- Create: `src/paperharness/app/api.py`
- Test: `tests/integration/test_api.py`

**Interfaces:**
- Consumes: `RunStore`, `HarnessRunner`, contracts.
- Produces: `RunWorker`, `create_app`.

- [ ] **Step 1: Write the failing test**

```python
import time

from fastapi.testclient import TestClient

from paperharness.agent.fake import FakeLLM
from paperharness.app.api import create_app
from paperharness.contracts.run import ExecResult
from paperharness.harness.runner import HarnessRunner
from paperharness.harness.store import RunStore
from paperharness.sandbox.fake import FakeSandboxSession


def make_client(tmp_path):
    store = RunStore(tmp_path)
    runner = HarnessRunner(
        store=store,
        llm=FakeLLM(),
        sandbox_factory=lambda request: FakeSandboxSession(artifact_path="out.txt"),
    )
    app, worker = create_app(store=store, runner=runner)
    worker.start()
    return TestClient(app), worker


def test_health_and_targets():
    client, worker = make_client(__import__("pathlib").Path("runs-test"))
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_api.py -v`
Expected: FAIL with import error.

- [ ] **Step 3: Write minimal implementation**

```python
import queue
import threading

from paperharness.contracts.run import RunRequest
from paperharness.harness.runner import HarnessRunner
from paperharness.harness.store import RunStore


class RunWorker:
    def __init__(self, store: RunStore, runner: HarnessRunner, max_queue: int = 5):
        self.store = store
        self.runner = runner
        self.queue: queue.Queue[str] = queue.Queue(maxsize=max_queue)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

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
            request = RunRequest.model_validate(self.store.read_status_request(run_id))
            self.runner.run(run_id, request)
            self.queue.task_done()
```

```python
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse

from paperharness.app.worker import RunWorker
from paperharness.contracts.run import RunRequest
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


def create_app(store: RunStore, runner: HarnessRunner) -> tuple[FastAPI, RunWorker]:
    app = FastAPI(title="PaperHarness API")
    worker = RunWorker(store=store, runner=runner)

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/targets")
    def targets():
        return TARGETS

    @app.post("/api/runs", status_code=202)
    def create_run(request: RunRequest):
        run_id = store.create_run(request)
        if not worker.submit(run_id):
            raise HTTPException(status_code=429, detail={"code": "queue_full"})
        return {"run_id": run_id, "state": "queued"}

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str):
        status = store.read_status(run_id)
        if not status:
            raise HTTPException(status_code=404, detail={"code": "run_not_found"})
        result = store.read_result(run_id)
        return {
            "run_id": run_id,
            "state": status.get("state"),
            "result": result.model_dump(mode="json") if result else None,
        }

    @app.get("/api/runs/{run_id}/events")
    def get_events(run_id: str, after: int = 0):
        return [
            event.model_dump(mode="json")
            for event in store.read_events(run_id, after=after)
        ]

    @app.post("/api/runs/{run_id}/cancel")
    def cancel_run(run_id: str):
        status = store.read_status(run_id)
        if not status:
            raise HTTPException(status_code=404, detail={"code": "run_not_found"})
        return {"run_id": run_id, "state": status.get("state")}

    @app.get("/api/runs/{run_id}/report", response_class=PlainTextResponse)
    def get_report(run_id: str):
        report = store.read_text(run_id, "report.md")
        if report is None:
            raise HTTPException(status_code=404, detail={"code": "report_not_ready"})
        return report

    @app.get("/api/runs/{run_id}/patch", response_class=PlainTextResponse)
    def get_patch(run_id: str):
        patch = store.read_text(run_id, "patch.diff")
        return patch if patch is not None else ""

    @app.get("/api/runs/{run_id}/environment")
    def get_environment(run_id: str):
        environment = store.read_text(run_id, "environment.json")
        if environment is None:
            raise HTTPException(status_code=404, detail={"code": "environment_not_ready"})
        import json

        return json.loads(environment)

    return app, worker
```

Add to `RunStore`:

```python
    def read_status_request(self, run_id: str) -> dict:
        return self._read_json(self.run_dir(run_id) / "request.json")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/integration/test_api.py -v`
Expected: PASS.

- [ ] **Step 5: Leave changes uncommitted**

No git commit unless the user explicitly asks.

---

### Task 7: Skeleton Documentation and Full Test Gate

**Files:**
- Modify: `README.md`
- Test: all tests.

**Interfaces:**
- Consumes: all previous tasks.
- Produces: developer startup instructions and verified skeleton.

- [ ] **Step 1: Write README skeleton section**

```markdown
## 本地开发

1. 安装 Python 3.11。
2. 运行 `pip install -e ".[dev]"`。
3. 运行 `pytest`。
4. 运行 `ruff check .`。

当前骨架不包含 Docker、LLM 和前端，只验证契约、事件存储、假沙箱、无自愈 Harness 和 API。
```

- [ ] **Step 2: Run full test suite**

Run: `pytest -q`
Expected: all tests PASS.

- [ ] **Step 3: Run lint**

Run: `ruff check .`
Expected: no errors.

- [ ] **Step 4: Leave changes uncommitted**

No git commit unless the user explicitly asks.
