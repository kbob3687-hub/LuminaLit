from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class VerificationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_id: Literal["pysindy-linear-v1"] = "pysindy-linear-v1"
    validator_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact: Literal["coefficients.json"] = "coefficients.json"
    threshold: Literal[0.0001] = 0.0001
    code_under_test: str = "已安装的 PySINDy 包，不是检出的仓库源码"
    dataset: str = "解析合成数据：x(t)=exp(-2t)、y(t)=exp(t)，t∈[0,1]，1001 点"
    source: str = "维护者定义的最小线性系统任务；系数参考值来自解析方程，不是论文实验指标"


class VerificationCheck(BaseModel):
    name: str
    passed: bool
    detail: str


class MetricResult(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    name: str
    observed: float
    threshold: float
    comparison: Literal["lt"] = "lt"
    passed: bool


class VerificationDiagnostic(BaseModel):
    phase: Literal[
        "rule", "client", "image", "create", "start", "wait", "response", "cleanup", "client_cleanup"
    ]
    code: Literal[
        "api_timeout", "environment_error", "cancelled", "deadline_exceeded", "rule_changed"
    ]


class VerificationResult(BaseModel):
    level: Literal["none", "execution", "artifact", "target"] = "none"
    status: Literal["not_run", "passed", "failed", "blocked"] = "not_run"
    summary: str = "尚未完成验证"
    checks: list[VerificationCheck] = Field(default_factory=list)
    metrics: list[MetricResult] = Field(default_factory=list)
    rule_id: str | None = None
    validator_digest: str | None = None
    artifact_sha256: str | None = None
    verifier_image_id: str | None = None
    checked_at: datetime | None = None
    code_under_test: str | None = None
    dataset: str | None = None
    source: str | None = None
    failure_category: str | None = None
    evidence_url: str | None = None
    limitations: list[str] = Field(default_factory=list)
    diagnostics: list[VerificationDiagnostic] = Field(default_factory=list, max_length=6)
