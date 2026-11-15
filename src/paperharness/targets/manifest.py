"""靶场 manifest 契约（interface-contract.md 第 5 节）。

字段规则：
- revision 必须是 40 位完整 commit
- failure_class 取值八种
- repairable=false 时 expected_final 必须是 failed（对称地，repairable=true 必须 succeeded，
  否则评测预期自相矛盾）
- allowed_actions 只能取 contracts/run.py 冻结的动作名，它是评测判定修复越界的依据
- GET /api/targets 只暴露 public_view 的四个字段，答案字段不出网
"""

import json
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator


class FailureClass(StrEnum):
    NONE = "none"
    MISSING_DEPENDENCY = "missing_dependency"
    VERSION_CONFLICT = "version_conflict"
    HARDCODED_PATH = "hardcoded_path"
    CUDA_ONLY = "cuda_only"
    DEPRECATED_API = "deprecated_api"
    MISSING_PRIVATE_DATA = "missing_private_data"
    REQUIRES_GPU = "requires_gpu"


ACTION_NAMES = frozenset(
    {
        "read_file",
        "search_text",
        "replace_exact",
        "install_requirement",
        "set_environment",
        "run_smoke_test",
        "finish_repair",
    }
)


class TargetManifest(BaseModel):
    id: str
    repo_url: str
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    python_version: Literal["3.10", "3.11"]
    command: list[str]
    expected_artifact: str | None = None
    failure_class: FailureClass
    repairable: bool
    allowed_actions: list[str]
    expected_final: Literal["succeeded", "failed"]
    notes: str | None = None

    @model_validator(mode="after")
    def _check_rules(self) -> Self:
        expected = "succeeded" if self.repairable else "failed"
        if self.expected_final != expected:
            raise ValueError(f"repairable={self.repairable} 要求 expected_final={expected}")
        unknown = set(self.allowed_actions) - ACTION_NAMES
        if unknown:
            raise ValueError(f"unknown actions: {sorted(unknown)}")
        return self


def load_targets(root: Path) -> list[TargetManifest]:
    """扫描 targets/<id>/manifest.json，id 必须与目录名一致。"""
    root = Path(root)
    manifests: list[TargetManifest] = []
    for manifest_path in sorted(root.glob("*/manifest.json")):
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = TargetManifest.model_validate(data)
        if manifest.id != manifest_path.parent.name:
            raise ValueError(
                f"manifest id {manifest.id!r} 与目录名 {manifest_path.parent.name!r} 不一致"
            )
        manifests.append(manifest)
    return manifests


def public_view(manifests: list[TargetManifest]) -> list[dict[str, object]]:
    """GET /api/targets 的展示字段，不含 failure_class 等答案字段。"""
    return [
        {
            "id": m.id,
            "repo_url": m.repo_url,
            "command": m.command,
            "expected_artifact": m.expected_artifact,
        }
        for m in manifests
    ]
