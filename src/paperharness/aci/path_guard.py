"""路径校验：把模型给的相对路径钉死在 workspace 内。

模型是不可信输入源，可能写出 `../../etc/passwd`。本模块是 `read_file` /
`search_text` / `replace_exact` 的共同前置，**先验后做**：任何校验失败都在
产生副作用之前抛出，不留下半截写入。
"""

import os
from pathlib import Path, PurePosixPath, PureWindowsPath


class PathEscape(Exception):
    """请求的路径落到了 workspace 之外（或本身就是非法路径）。"""

    def __init__(self, raw: str):
        super().__init__(raw)
        self.raw = raw


def _is_absolute(raw: str) -> bool:
    """同时按 Windows 与 POSIX 两种口味判断绝对路径。

    不能只用当前平台的 `os.path.isabs`：`C:\\Windows\\...` 在 Linux 上会被
    判成相对路径，`/etc/passwd` 在 Windows 上同理。两条都认才安全。
    """
    return (
        PureWindowsPath(raw).is_absolute()
        or PurePosixPath(raw).is_absolute()
        or raw.startswith("~")
    )


def resolve(workspace: Path, raw: str) -> Path:
    """返回 workspace 内的绝对路径；越界抛 `PathEscape`。

    `workspace` 必须已经是 `resolve()` 过的绝对路径（由调用方缓存一次），
    否则符号链接会让边界判断漂移。
    """
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        raise PathEscape(raw if isinstance(raw, str) else "")

    # 先拒绝对路径再 resolve：`workspace / "C:\\Windows\\..."` 会被拼成
    # 奇怪路径，早拦比晚拦清楚。
    if _is_absolute(raw):
        raise PathEscape(raw)

    target = (workspace / raw).resolve(strict=False)
    if not _is_inside(workspace, target):
        raise PathEscape(raw)
    return target


def _is_inside(workspace: Path, target: Path) -> bool:
    """大小写不敏感比较（Windows 下 `C:\\Run\\WS` 与 `c:\\run\\ws` 是同一个目录）。"""
    base = os.path.normcase(str(workspace))
    candidate = os.path.normcase(str(target))
    if candidate == base:
        return True
    return candidate.startswith(base + os.sep)
