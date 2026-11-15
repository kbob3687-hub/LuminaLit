from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

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
    outcome: Literal["succeeded", "failed", "budget_exceeded", "unrepairable", "cancelled"]
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
    ReadFileAction | SearchTextAction | ReplaceExactAction | InstallRequirementAction | SetEnvironmentAction | RunSmokeTestAction | FinishRepairAction,
    Field(discriminator="action"),
]
