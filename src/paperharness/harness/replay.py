"""按冻结修复清单安全还原材料，并计算可复跑的依赖摘要。"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path

from paperharness.contracts.bundle import ChangeKind, FileChange, RepairManifest

_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class ReplayMaterializationError(ValueError):
    """修复清单无法安全、完整地还原到目标工作区。"""


def _is_reparse_point(info: os.stat_result) -> bool:
    return bool(getattr(info, "st_file_attributes", 0) & _REPARSE_POINT)


def _check_directory(path: Path) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ReplayMaterializationError(f"无法检查目录 {path}: {exc}") from exc
    if stat.S_ISLNK(info.st_mode) or _is_reparse_point(info) or not stat.S_ISDIR(info.st_mode):
        raise ReplayMaterializationError(f"目录包含符号链接或重解析点: {path}")


def _check_regular_file(path: Path) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ReplayMaterializationError(f"无法检查文件 {path}: {exc}") from exc
    if (
        stat.S_ISLNK(info.st_mode)
        or _is_reparse_point(info)
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
    ):
        raise ReplayMaterializationError(f"文件不是安全的单链接普通文件: {path}")
    return info


def _validate_workspace_root(workspace: Path, *, must_exist: bool) -> Path:
    absolute = Path(os.path.abspath(workspace))
    for component in reversed([absolute, *absolute.parents]):
        try:
            component.lstat()
        except FileNotFoundError as exc:
            if must_exist:
                raise ReplayMaterializationError(f"工作区根目录不存在: {workspace}") from exc
            return absolute
        except OSError as exc:
            raise ReplayMaterializationError(f"无法检查工作区组件 {component}: {exc}") from exc
        _check_directory(component)
    return absolute


def _validate_parent_components(workspace: Path, parts: tuple[str, ...]) -> None:
    current = workspace
    for part in parts:
        current = current / part
        try:
            current.lstat()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise ReplayMaterializationError(f"无法检查父目录 {current}: {exc}") from exc
        _check_directory(current)


def _manifest_parts(manifest: RepairManifest) -> list[tuple[FileChange, tuple[str, ...]]]:
    changes: list[tuple[FileChange, tuple[str, ...]]] = []
    seen: set[str] = set()
    for change in manifest.files:
        raw = change.path
        if not raw or raw.startswith("/") or "\\" in raw or ":" in raw or "\0" in raw:
            raise ReplayMaterializationError(f"非法材料路径: {raw!r}")
        parts = tuple(raw.split("/"))
        if any(part in ("", ".", "..") or part.casefold() == ".git" for part in parts):
            raise ReplayMaterializationError(f"非法材料路径: {raw!r}")
        key = os.path.normcase(raw)
        if key in seen:
            raise ReplayMaterializationError(f"材料路径重复: {raw!r}")
        seen.add(key)
        changes.append((change, parts))
    return changes


def _read_declared_file(path: Path, change: FileChange) -> bytes:
    info = _check_regular_file(path)
    if info.st_size != change.size:
        raise ReplayMaterializationError(f"材料文件大小与清单不符: {change.path}")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as stream:
            opened_info = os.fstat(stream.fileno())
            if (
                stat.S_ISLNK(opened_info.st_mode)
                or _is_reparse_point(opened_info)
                or not stat.S_ISREG(opened_info.st_mode)
                or opened_info.st_nlink != 1
            ):
                raise ReplayMaterializationError(f"材料文件不是安全的单链接普通文件: {change.path}")
            raw = stream.read()
    except OSError as exc:
        raise ReplayMaterializationError(f"无法读取材料文件 {change.path}: {exc}") from exc
    if len(raw) != change.size or hashlib.sha256(raw).hexdigest() != change.sha256:
        raise ReplayMaterializationError(f"材料文件哈希或大小与清单不符: {change.path}")
    return raw


def _atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(raw)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def apply_repair_manifest(
    source_workspace: Path,
    target_workspace: Path,
    manifest: RepairManifest,
) -> None:
    """完整预校验冻结材料后，再原子写入新增/修改文件并执行删除。"""
    if manifest.excluded:
        raise ReplayMaterializationError("冻结材料清单不完整，拒绝部分还原")
    changes = _manifest_parts(manifest)
    source = _validate_workspace_root(source_workspace, must_exist=True)
    target = _validate_workspace_root(target_workspace, must_exist=False)

    preflighted: list[tuple[FileChange, Path, bytes | None]] = []
    for change, parts in changes:
        source_path = source.joinpath(*parts)
        target_path = target.joinpath(*parts)
        raw: bytes | None = None
        if change.change is not ChangeKind.DELETED:
            _validate_parent_components(source, parts[:-1])
            raw = _read_declared_file(source_path, change)
        _validate_parent_components(target, parts[:-1])
        try:
            target_path.lstat()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise ReplayMaterializationError(f"无法检查目标文件 {target_path}: {exc}") from exc
        else:
            _check_regular_file(target_path)
        preflighted.append((change, target_path, raw))

    try:
        for change, target_path, raw in preflighted:
            if change.change is ChangeKind.DELETED:
                target_path.unlink(missing_ok=True)
            else:
                assert raw is not None
                _atomic_write(target_path, raw)
    except OSError as exc:
        raise ReplayMaterializationError(f"还原修复材料失败: {exc}") from exc


def dependency_digest(manifest: RepairManifest) -> str:
    """对 Python 版本与去重排序后的依赖生成确定性 JSON SHA-256。"""
    payload = {
        "dependencies": sorted(set(manifest.dependencies)),
        "python_version": manifest.python_version,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
