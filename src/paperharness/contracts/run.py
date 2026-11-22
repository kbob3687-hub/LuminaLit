from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, JsonValue, field_validator

from paperharness.contracts.verification import VerificationResult

# 已知不可能是 Git 仓库的 host：收单后必然 clone_failed，在 API 边界直接拒掉。
# 只收录有明确证据的（用户实测会贴上来的），不猜。
_NON_GIT_HOSTS = frozenset({"arxiv.org", "www.arxiv.org"})


def validate_repo_url(url: str) -> str:
    """安全契约：repo_url 只允许 https:// 公开 Git 地址。

    拒绝本地路径（git clone 支持本地目录）、file://、ssh://、
    内嵌凭据、空 host、裸 host、query/fragment——防止把宿主机
    任意目录 clone 进 workspace 再挂进容器。
    """
    if not url or any(ord(ch) < 0x20 or ch.isspace() for ch in url):
        raise ValueError("repo_url must be a non-empty https:// URL without whitespace")
    parsed = urlsplit(url)
    if parsed.scheme != "https":
        raise ValueError(
            "repo_url must start with https:// "
            "(local paths, file:// and ssh:// are not allowed)"
        )
    if parsed.username or parsed.password:
        raise ValueError("repo_url must not embed credentials")
    if not parsed.hostname:
        raise ValueError("repo_url must include a host")
    if not parsed.path or parsed.path == "/":
        raise ValueError("repo_url must point to a repository path, not a bare host")
    if parsed.hostname.lower() in _NON_GIT_HOSTS:
        raise ValueError(
            f"{parsed.hostname} is not a Git repository host "
            "(e.g. an arXiv page); submit the paper's code repository instead"
        )
    if parsed.query or parsed.fragment:
        raise ValueError("repo_url must not include query or fragment")
    return url


class RunRequest(BaseModel):
    repo_url: str
    revision: str | None = None
    command: list[str] | None = None
    expected_artifact: str | None = None
    python_version: Literal["3.10", "3.11"] | None = None
    timeout_seconds: int = Field(default=300, ge=30, le=900)
    max_repairs: int = Field(default=3, ge=0, le=5)

    @field_validator("repo_url")
    @classmethod
    def _validate_repo_url(cls, value: str) -> str:
        return validate_repo_url(value)


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
    VERIFYING = "verifying"
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


class EventExplanation(BaseModel):
    title: str
    detail: str
    tone: Literal["neutral", "success", "warning", "error"]


class RunGuidance(EventExplanation):
    next_steps: list[str]
    repair_summary: str


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
    verification: VerificationResult | None = None


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
