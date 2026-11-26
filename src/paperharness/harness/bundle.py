"""修复材料清单构建与导出包打包（设计评审稿 7.4 / 9.4 / 12.2）。

只读宿主工作区，不在容器内执行任何命令。清单来自 git 状态 +
文件内容哈希，导出包只在完整性为 complete 时才允许下载。
"""

from __future__ import annotations

import fnmatch
import hashlib
import io
import json
import os
import stat
import subprocess
import zipfile
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Literal

from paperharness.aci.paper_aci import is_safe_environment_value
from paperharness.contracts.bundle import (
    BundleEntry,
    BundleItem,
    BundleMissing,
    BundleResult,
    ChangeKind,
    ExcludedChange,
    ExclusionReason,
    FileChange,
    RepairManifest,
)

MAX_FILE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 4 * 1024 * 1024
_GIT_TIMEOUT_SECONDS = 30
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

# 可能携带凭据/密钥的文件名模式；命中即阻止导出，而不是打包后过滤。
_SENSITIVE_PATTERNS = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "credentials",
    "credentials.json",
    "secrets.json",
    "*.token",
    "*_token",
    "*.secret",
    "*_secret",
    ".npmrc",
    ".pypirc",
)

_REASON_TEXT = {
    ExclusionReason.BINARY_FILE: "二进制文件，本轮只导出文本变更。",
    ExclusionReason.OVERSIZED_FILE: f"超过单文件 {MAX_FILE_BYTES // 1024} KiB 上限。",
    ExclusionReason.UNREADABLE_FILE: "文件读取失败或不是合法 UTF-8 文本。",
    ExclusionReason.SENSITIVE_NAME: "文件名命中敏感模式（可能含凭据），禁止导出。",
    ExclusionReason.OVERSIZED_BUNDLE: f"材料总大小超过 {MAX_TOTAL_BYTES // 1024 // 1024} MiB 上限。",
}

_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


class ManifestBuildError(ValueError):
    """工作区无法审计（非 git 仓库/ git 不可用），不能生成可信清单。"""


def _is_sensitive(path: PurePosixPath) -> bool:
    name = path.name.lower()
    return any(fnmatch.fnmatch(name, pattern) for pattern in _SENSITIVE_PATTERNS)


def _is_reparse_point(info: os.stat_result) -> bool:
    return bool(getattr(info, "st_file_attributes", 0) & _REPARSE_POINT)


def _safe_relpath(raw: str) -> PurePosixPath | None:
    # git 已给相对路径，但仍按不可信输入复核：拒绝越界、链接与 Windows 路径表示。
    if not raw or "\\" in raw or ":" in raw or "\0" in raw:
        return None
    parts = raw.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return None
    if any(part.casefold() == ".git" for part in parts):
        return None
    return PurePosixPath(*parts)


def _safe_workspace_target(workspace: Path, rel: PurePosixPath) -> Path | None:
    try:
        root_info = workspace.lstat()
        if (stat.S_ISLNK(root_info.st_mode) or _is_reparse_point(root_info)
                or not stat.S_ISDIR(root_info.st_mode)):
            return None
        current = workspace
        for part in rel.parts[:-1]:
            current = current / part
            info = current.lstat()
            if (stat.S_ISLNK(info.st_mode) or _is_reparse_point(info)
                    or not stat.S_ISDIR(info.st_mode)):
                return None
    except OSError:
        return None
    return workspace.joinpath(*rel.parts)


def _git_status(workspace: Path) -> list[tuple[ChangeKind, str]]:
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
    try:
        proc = subprocess.run(
            [
                "git", "-C", str(workspace),
                "status", "--porcelain=v1", "--no-renames",
                "--untracked-files=all", "-z",
            ],
            capture_output=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            env=env,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ManifestBuildError(f"无法审计工作区 git 状态: {exc}") from exc
    if proc.returncode != 0:
        raise ManifestBuildError("工作区不是可审计的 git 仓库。")
    out = proc.stdout.decode("utf-8", errors="surrogateescape")
    changes: list[tuple[ChangeKind, str]] = []
    for record in out.split("\0"):
        if len(record) < 4:
            continue
        status, raw_path = record[:2], record[3:]
        if status == "??":
            kind = ChangeKind.ADDED
        elif "D" in status:
            kind = ChangeKind.DELETED
        else:
            kind = ChangeKind.MODIFIED
        changes.append((kind, raw_path))
    return changes


def _classify(workspace: Path, kind: ChangeKind, rel: PurePosixPath) -> FileChange | ExcludedChange:
    path_text = rel.as_posix()
    if kind is ChangeKind.DELETED:
        return FileChange(path=path_text, change=kind, sha256=_EMPTY_SHA256, size=0)
    if _is_sensitive(rel):
        return ExcludedChange(path=path_text, change=kind, reason=ExclusionReason.SENSITIVE_NAME)
    target = _safe_workspace_target(workspace, rel)
    if target is None:
        return ExcludedChange(
            path=path_text, change=kind, reason=ExclusionReason.UNREADABLE_FILE
        )
    try:
        info = target.lstat()
        if (stat.S_ISLNK(info.st_mode) or _is_reparse_point(info)
                or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
            return ExcludedChange(
                path=path_text, change=kind, reason=ExclusionReason.UNREADABLE_FILE
            )
        if info.st_size > MAX_FILE_BYTES:
            return ExcludedChange(
                path=path_text, change=kind, reason=ExclusionReason.OVERSIZED_FILE
            )
        raw = target.read_bytes()
        if b"\0" in raw[:8192]:
            return ExcludedChange(path=path_text, change=kind, reason=ExclusionReason.BINARY_FILE)
        raw.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return ExcludedChange(path=path_text, change=kind, reason=ExclusionReason.UNREADABLE_FILE)
    return FileChange(
        path=path_text, change=kind, sha256=hashlib.sha256(raw).hexdigest(), size=len(raw)
    )


def build_repair_manifest(
    *,
    run_id: str,
    workspace: Path,
    base_revision: str,
    repository_url: str | None = None,
    command: list[str],
    python_version: str,
    dependencies: Iterable[str] = (),
    environment: dict[str, str] | None = None,
    expected_artifacts: Iterable[str] = (),
) -> RepairManifest:
    """以 git 状态为基准枚举变更；预期产物与源码变更分开记录。"""
    artifacts = sorted({PurePosixPath(p).as_posix() for p in expected_artifacts})
    artifact_set = set(artifacts)
    files: list[FileChange] = []
    excluded: list[ExcludedChange] = []
    for kind, raw_path in _git_status(workspace):
        rel = _safe_relpath(raw_path)
        if rel is None:
            excluded.append(ExcludedChange(
                path=raw_path[:200], change=kind, reason=ExclusionReason.UNREADABLE_FILE,
            ))
            continue
        if rel.as_posix() in artifact_set:
            continue  # 产物单独记录，不算源码变更
        change = _classify(workspace, kind, rel)
        (files if isinstance(change, FileChange) else excluded).append(change)
    return RepairManifest(
        run_id=run_id,
        repository_url=repository_url,
        base_revision=base_revision,
        command=list(command),
        python_version=python_version,
        files=sorted(files, key=lambda item: item.path),
        dependencies=sorted(set(dependencies)),
        # 契约层只保留 ACI 白名单内的非敏感值；缺失或非白名单值留空以阻止复跑。
        environment={
            name: value if is_safe_environment_value(name, value) else ""
            for name, value in (environment or {}).items()
        },
        artifacts=artifacts,
        excluded=sorted(excluded, key=lambda item: item.path),
        frozen_at=datetime.now(UTC),
    )


def manifest_digest(manifest: RepairManifest) -> str:
    # 与导出包内 repair_manifest.json 的字节完全一致，保证哈希可复验。
    return hashlib.sha256(
        manifest.model_dump_json(indent=2).encode("utf-8")
    ).hexdigest()


def _redact_environment_metadata(content: str | None) -> tuple[str | None, bool]:
    if content is None:
        return None, False
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return None, True
    if not isinstance(data, dict):
        return None, True
    if "environment" not in data:
        return content, False
    if not isinstance(data["environment"], dict):
        return None, True
    environment = data["environment"]
    safe = {
        name: value if is_safe_environment_value(name, value) else ""
        for name, value in environment.items()
    }
    redacted = safe != environment
    data["environment"] = safe
    return json.dumps(data, ensure_ascii=False, indent=2), redacted


def pack_bundle(
    run_dir: Path,
    workspace: Path,
    manifest: RepairManifest,
    metadata: dict[str, str | None],
) -> BundleResult:
    """把冻结清单与材料打成 reproduce.zip；完整性不是 complete 时不落盘。

    metadata: run 目录里应进入材料包的文本（patch.diff / run_plan.json /
    environment.json / verification.json 等）；None 表示缺失，记为 missing。
    """
    items: list[BundleItem] = []
    entries: dict[str, bytes] = {}

    def _add(kind: str, zip_path: str, raw: bytes | None, reason: str | None = None) -> None:
        if raw is None:
            items.append(BundleItem(
                path=zip_path,
                kind=kind,
                status="missing",
                reason=reason or "材料缺失或内容与冻结清单不一致。",
            ))
            return
        items.append(BundleItem(
            path=zip_path, kind=kind, status="included",
            size=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
        ))
        entries[zip_path] = raw

    for change in manifest.files:
        if change.change is ChangeKind.DELETED:
            continue  # 删除只列在清单里，压缩包不需要载荷
        _add("source", f"files/{change.path}", _read_workspace_file(workspace, change))
    for artifact in manifest.artifacts:
        _add(
            "artifact",
            f"artifacts/{artifact}",
            _read_bounded_file(workspace, artifact),
            reason="预期产物在收尾时缺失。",
        )
    manifest_json = manifest.model_dump_json(indent=2).encode("utf-8")
    _add("metadata", "repair_manifest.json", manifest_json)
    environment_metadata_redacted = False
    environment_metadata_excluded = False
    for name, content in sorted(metadata.items()):
        if name == "environment.json":
            original_content = content
            content, redacted = _redact_environment_metadata(content)
            environment_metadata_redacted = environment_metadata_redacted or redacted
            environment_metadata_excluded = (
                environment_metadata_excluded
                or (original_content is not None and content is None)
            )
        _add("metadata", name, content.encode("utf-8") if content is not None else None,
             reason="运行记录缺少该材料。")

    total = sum(len(raw) for raw in entries.values())
    for change in manifest.excluded:
        blocked = change.reason is ExclusionReason.SENSITIVE_NAME
        items.append(BundleItem(
            path=change.path,
            kind="source",
            status="blocked" if blocked else "missing",
            reason=_REASON_TEXT[change.reason],
        ))

    sorted_items = sorted(items, key=lambda item: item.path)
    integrity: Literal["complete", "incomplete", "blocked"] = "complete"
    if any(item.status == "blocked" for item in sorted_items):
        integrity = "blocked"
    elif any(item.status == "missing" for item in sorted_items):
        integrity = "incomplete"
    elif total > MAX_TOTAL_BYTES:
        integrity = "incomplete"
        sorted_items.append(BundleItem(
            path="reproduce.zip", kind="metadata", status="missing",
            reason=_REASON_TEXT[ExclusionReason.OVERSIZED_BUNDLE],
        ))
        sorted_items.sort(key=lambda item: item.path)

    bundle_entries: list[BundleEntry] = []
    missing: list[BundleMissing] = []
    limitations = ["仅包含 UTF-8 文本变更；二进制、超限或不可读内容见缺项清单"]
    if environment_metadata_redacted and not environment_metadata_excluded:
        limitations.append("环境变量元数据中的非白名单或非法值已脱敏")
    if environment_metadata_excluded:
        limitations.append("环境变量元数据格式无效，已从材料包剔除")
    if manifest.environment:
        if any(not value for value in manifest.environment.values()):
            limitations.append("环境变量仅记录名称，需按名称补填非敏感值")
        else:
            limitations.append("环境变量仅包含 ACI 白名单内的非敏感值")
    for item in sorted_items:
        if item.status == "included":
            assert item.sha256 is not None
            bundle_entries.append(BundleEntry(
                path=item.path, sha256=item.sha256, size=item.size,
            ))
        elif item.status == "missing":
            missing.append(BundleMissing(path=item.path, reason=item.reason or "材料缺失。"))
        elif item.reason:
            limitations.append(item.reason)

    zip_sha256: str | None = None
    if integrity == "complete":
        sums = "".join(
            f"{entry.sha256}  {entry.path}\n" for entry in bundle_entries
        ).encode("utf-8")
        entries["SHA256SUMS"] = sums
        zip_sha256 = _write_zip(run_dir / "reproduce.zip", entries)
    return BundleResult(
        integrity=integrity,
        items=sorted_items,
        total_bytes=total,
        url=f"/api/runs/{manifest.run_id}/bundle" if integrity == "complete" else None,
        sha256=zip_sha256,
        manifest_digest=manifest_digest(manifest),
        entries=bundle_entries,
        missing=missing,
        external_inputs=(
            [f"{manifest.repository_url}@{manifest.base_revision}"]
            if manifest.repository_url is not None
            else []
        ),
        limitations=limitations,
    )


def _read_bounded_file(workspace: Path, path_text: str) -> bytes | None:
    rel = _safe_relpath(path_text)
    if rel is None:
        return None
    target = _safe_workspace_target(workspace, rel)
    if target is None:
        return None
    try:
        info = target.lstat()
        if (stat.S_ISLNK(info.st_mode) or _is_reparse_point(info)
                or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_size > MAX_FILE_BYTES):
            return None
        return target.read_bytes()
    except OSError:
        return None


def _read_workspace_file(workspace: Path, change: FileChange) -> bytes | None:
    raw = _read_bounded_file(workspace, change.path)
    if raw is None:
        return None
    if len(raw) != change.size or hashlib.sha256(raw).hexdigest() != change.sha256:
        return None  # 清单冻结后内容又变了：如实记缺失，不打包陈旧内容
    return raw


def _write_zip(path: Path, entries: dict[str, bytes]) -> str:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(entries):
            archive.writestr(name, entries[name])
    tmp = path.with_suffix(".zip.tmp")
    tmp.write_bytes(buffer.getvalue())
    os.replace(tmp, path)
    return hashlib.sha256(path.read_bytes()).hexdigest()
