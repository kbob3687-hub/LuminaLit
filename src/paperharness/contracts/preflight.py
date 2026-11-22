from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from paperharness.contracts.run import RunPlan, RunRequest
from paperharness.contracts.verification import VerificationSpec


class PreflightRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_id: str = Field(min_length=1, max_length=128)
    timeout_seconds: int = Field(default=300, ge=30, le=900)
    max_repairs: int = Field(default=3, ge=0, le=5)


class ConfirmedRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preflight_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    confirmation_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class DirectRunRequest(RunRequest):
    model_config = ConfigDict(extra="forbid")


class PreflightCheck(BaseModel):
    name: str
    status: Literal["passed", "warning", "not_checked"]
    detail: str
    source: str


class PreflightResult(BaseModel):
    preflight_id: str
    target_id: str
    repo_url: str
    created_at: datetime
    expires_at: datetime
    confirmation_digest: str
    plan: RunPlan
    timeout_seconds: int
    max_repairs: int
    checks: list[PreflightCheck]
    limitations: list[str]
    run_id: str | None = None
    verification: VerificationSpec | None = None
