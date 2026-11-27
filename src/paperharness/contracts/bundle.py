"""修复材料清单与导出包契约（设计评审稿 7.4 / 12.2）。

RepairManifest 是复跑与材料导出共同引用的冻结清单：
本轮受限实现只收录 UTF-8 文本变更；二进制、超限、敏感命名、
不可读文件一律记入 excluded，不静默丢弃，也不进入导出包。
"""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ChangeKind(StrEnum):
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"


class ExclusionReason(StrEnum):
    # 内容类缺失：材料不完整，但可解释
    BINARY_FILE = "binary_file"
    OVERSIZED_FILE = "oversized_file"
    UNREADABLE_FILE = "unreadable_file"
    OVERSIZED_BUNDLE = "oversized_bundle"
    # 安全/许可类：材料被阻止，不得打包
    SENSITIVE_NAME = "sensitive_name"


class FileChange(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str  # workspace 相对路径，posix 风格，禁止绝对路径与 ..
    change: ChangeKind
    sha256: str  # 文件内容哈希；deleted 记空内容哈希
    size: int  # 字节数；deleted 记 0


class ExcludedChange(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    change: ChangeKind
    reason: ExclusionReason


class RepairManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    run_id: str
    repository_url: str | None = None
    base_revision: str  # 原始检出 commit，复跑还原基准
    command: list[str]  # 冻结执行命令（参数数组，非 shell 字符串）
    python_version: str
    files: list[FileChange] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)  # 修复期显式 install_requirement
    # 只冻结 ACI 白名单内的非敏感环境变量值；缺失值留空并阻止复跑
    environment: dict[str, str] = Field(default_factory=dict)
    artifacts: list[str] = Field(default_factory=list)  # 预期产物，与源码变更分开记录
    excluded: list[ExcludedChange] = Field(default_factory=list)
    frozen_at: datetime


class BundleItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str  # 压缩包内路径
    kind: Literal["source", "artifact", "metadata"]
    status: Literal["included", "missing", "blocked"]
    reason: str | None = None  # 未包含原因（中文，可解释）
    size: int = 0
    sha256: str | None = None


class BundleEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    sha256: str
    size: int


class BundleMissing(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    reason: str


class BundleResult(BaseModel):
    integrity: Literal["complete", "incomplete", "blocked"] = "blocked"
    items: list[BundleItem] = Field(default_factory=list)
    total_bytes: int = 0
    url: str | None = None
    sha256: str | None = None
    manifest_digest: str | None = None
    entries: list[BundleEntry] = Field(default_factory=list)
    missing: list[BundleMissing] = Field(default_factory=list)
    external_inputs: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
