"""PaperACI：模型动作的唯一收口。

执行流水线（每次 execute 都走这条，顺序不可换）：

1. 动作分派：不认识的 action → `unknown_action` 兜底
2. 公共前置校验：路径类动作先过 `path_guard`，失败即返回
3. 动作专属校验：依赖字符串 / 环境变量白名单
4. 执行副作用
5. 组装 Observation

第 2、3 步的关键是**顺序**：先验后做。任何校验失败都必须在产生副作用之前
返回，否则「用一次非法写入换一次合法读」就成了绕过通道。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from paperharness.aci import errors, path_guard, requirement_guard
from paperharness.contracts.run import (
    ExecResult,
    Observation,
    RunPlan,
    RunSmokeTestAction,
    ToolAction,
)
from paperharness.sandbox.base import SandboxSession

# 单次 Observation 的文本上限（契约 §7）：超了保留尾部，模型最需要看最后几行。
MAX_TAIL_CHARS = 4000
# search_text 单条命中的内容截断长度。
MAX_MATCH_CHARS = 200
# search_text 单文件扫描上限，防止在仓库同目录树里读爆内存。
MAX_SCAN_BYTES = 2 * 1024 * 1024
# set_environment 白名单：只有这四个。
ALLOWED_ENV_NAMES = frozenset(
    {
        "CUDA_VISIBLE_DEVICES",
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "TOKENIZERS_PARALLELISM",
    }
)
# pip 装包通常比跑一次 smoke test 慢，单独给下限（设计稿 §5.4 提案 4）。
INSTALL_TIMEOUT_FLOOR = 120


class PaperACI:
    def __init__(
        self,
        session: SandboxSession,
        workspace: Path,
        plan: RunPlan,
        timeout_seconds: int,
    ):
        self.session = session
        # workspace 先 resolve 一次并缓存：符号链接会让边界判断漂移。
        self.workspace = Path(workspace).resolve(strict=False)
        self.plan = plan
        self.timeout_seconds = timeout_seconds
        # 记录 ACI 进场时的网络状态，保证每轮动作结束后回到这个状态。
        self._network_at_entry: bool | None = None

    # ------------------------------------------------------------------ 入口

    def execute(self, action: ToolAction) -> Observation:
        handler = getattr(self, f"_do_{getattr(action, 'action', '')}", None)
        if handler is None:
            return self._fail(getattr(action, "action", "unknown"), errors.UNKNOWN_ACTION)
        return handler(action)

    # ------------------------------------------------------------- 动作实现

    def _do_read_file(self, action) -> Observation:
        if action.start_line < 1:
            return self._fail(action.action, errors.invalid_argument("start_line"))
        if not 1 <= action.line_count <= 200:
            return self._fail(action.action, errors.invalid_argument("line_count"))

        target = self._guard(action)
        if isinstance(target, Observation):
            return target
        if not target.is_file():
            return self._fail(action.action, errors.FILE_NOT_FOUND)

        lines = self._read_text(target)
        if lines is None:
            return self._fail(action.action, errors.FILE_NOT_FOUND)

        start = action.start_line - 1
        chunk = lines.splitlines()[start : start + action.line_count]
        body = "\n".join(
            f"{start + offset + 1}: {line}" for offset, line in enumerate(chunk)
        )
        return Observation(
            ok=True,
            action=action.action,
            summary=f"read {len(chunk)} lines from {action.path}",
            stdout_tail=self._tail(body),
        )

    def _do_search_text(self, action) -> Observation:
        target = self._guard(action)
        if isinstance(target, Observation):
            return target
        if not target.exists():
            return self._fail(action.action, errors.FILE_NOT_FOUND)

        files = [target] if target.is_file() else sorted(self._walk_files(target))
        matches: list[str] = []
        for path in files:
            if len(matches) >= action.max_results:
                break
            text = self._read_text(path)
            if text is None:
                continue
            rel = self._relative(path)
            for line_no, line in enumerate(text.splitlines(), start=1):
                if action.query in line:
                    content = line.strip()[:MAX_MATCH_CHARS]
                    matches.append(f"{rel}:{line_no}:{content}")
                    if len(matches) >= action.max_results:
                        break

        summary = "0 matches" if not matches else f"{len(matches)} matches"
        return Observation(
            ok=True,
            action=action.action,
            summary=summary,
            stdout_tail=self._tail("\n".join(matches)),
        )

    def _do_replace_exact(self, action) -> Observation:
        target = self._guard(action)
        if isinstance(target, Observation):
            return target
        if not target.is_file():
            return self._fail(action.action, errors.FILE_NOT_FOUND)

        raw = self._read_bytes(target)
        if raw is None:
            return self._fail(action.action, errors.FILE_NOT_FOUND)

        old = action.old.encode("utf-8")
        occurrences = raw.count(old) if old else raw.count(b"") - 1
        if occurrences == 0:
            return self._fail(action.action, errors.REPLACE_NOT_FOUND)
        if occurrences > 1:
            return self._fail(action.action, errors.REPLACE_NOT_UNIQUE)

        updated = raw.replace(old, action.new.encode("utf-8"), 1)
        self._atomic_write(target, updated)
        return Observation(
            ok=True,
            action=action.action,
            summary=f"replaced 1 occurrence in {action.path}",
        )

    def _do_install_requirement(self, action) -> Observation:
        try:
            requirement = requirement_guard.validate(action.requirement)
        except requirement_guard.RequirementNotAllowed:
            # 校验失败**不能开网**：这是「拒绝时无副作用」的落点之一。
            return self._fail(action.action, errors.REQUIREMENT_NOT_ALLOWED)

        timeout = max(self.timeout_seconds, INSTALL_TIMEOUT_FLOOR)
        with self._network(True):
            result = self.session.execute(
                ["python", "-m", "pip", "install", requirement], timeout
            )
        return self._from_exec(action.action, result, f"install {requirement}")

    def _do_set_environment(self, action) -> Observation:
        if action.name not in ALLOWED_ENV_NAMES:
            return self._fail(action.action, errors.env_not_allowed(action.name))
        self.session.set_environment(action.name, action.value)
        return Observation(
            ok=True,
            action=action.action,
            summary=f"set {action.name}={action.value}",
        )

    def _do_run_smoke_test(self, action: RunSmokeTestAction) -> Observation:
        # 命令来自 plan，模型换不了。
        self.session.set_network(False)
        result = self.session.execute(list(self.plan.command), self.timeout_seconds)
        return self._from_exec(action.action, result, "smoke test")

    def _do_finish_repair(self, action) -> Observation:
        # 只结束本轮决策，不代表成功——成功与否由 Harness 的验证阶段判。
        return Observation(ok=True, action=action.action, summary=action.summary)

    # ------------------------------------------------------------- 内部工具

    def _guard(self, action) -> Path | Observation:
        try:
            return path_guard.resolve(self.workspace, action.path)
        except path_guard.PathEscape as exc:
            return self._fail(action.action, errors.path_escape(exc.raw))

    def _fail(self, action: str, summary: str) -> Observation:
        return Observation(ok=False, action=action, summary=summary)

    def _from_exec(self, action: str, result: ExecResult, label: str) -> Observation:
        ok = result.exit_code == 0 and not result.timed_out
        return Observation(
            ok=ok,
            action=action,
            summary=f"{label} exited with {result.exit_code}",
            exit_code=result.exit_code,
            timed_out=result.timed_out,
            stdout_tail=self._tail(result.stdout_tail),
            stderr_tail=self._tail(result.stderr_tail),
            log_path=result.stdout_path,
        )

    @contextmanager
    def _network(self, enabled: bool) -> Iterator[None]:
        """临时切网络，`finally` 恢复调用前的状态。

        `finally` 是硬要求：装包中途抛异常（超时、容器已关）也必须把网络关
        回去，否则后面所有动作都在联网状态下跑，「断网」这条隔离承诺当场失效。
        """
        previous = self._current_network()
        self.session.set_network(enabled)
        try:
            yield
        finally:
            self.session.set_network(previous)

    def _current_network(self) -> bool:
        if self._network_at_entry is None:
            value = getattr(self.session, "network_enabled", True)
            self._network_at_entry = bool(value)
        return self._network_at_entry

    def _relative(self, path: Path) -> str:
        try:
            return path.relative_to(self.workspace).as_posix()
        except ValueError:  # pragma: no cover - path_guard 已保证在内
            return path.name

    def _walk_files(self, root: Path) -> Iterator[Path]:
        if not root.is_dir():
            return
        for current, dirs, files in os.walk(root):
            # 不跟随 workspace 内的目录符号链接：避免把宿主目录整棵读进来。
            dirs[:] = [
                name for name in dirs if not (Path(current) / name).is_symlink()
            ]
            for name in files:
                candidate = Path(current) / name
                if candidate.is_file() and not candidate.is_symlink():
                    yield candidate

    @staticmethod
    def _read_bytes(path: Path) -> bytes | None:
        try:
            if path.stat().st_size > MAX_SCAN_BYTES:
                return None
            return path.read_bytes()
        except OSError:
            return None

    @classmethod
    def _read_text(cls, path: Path) -> str | None:
        raw = cls._read_bytes(path)
        if raw is None or b"\x00" in raw[:1024]:
            return None
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return None

    @classmethod
    def _read_lines(cls, path: Path) -> list[str] | None:
        text = cls._read_text(path)
        if text is None:
            return None
        return text.splitlines(keepends=True)

    @staticmethod
    def _atomic_write(path: Path, payload: bytes) -> None:
        """同目录临时文件 + `os.replace`，避免半写留下坏文件。"""
        tmp = path.with_name(f".{path.name}.aci-tmp")
        tmp.write_bytes(payload)
        os.replace(tmp, path)

    @staticmethod
    def _tail(text: str) -> str:
        if not text:
            return ""
        return text[-MAX_TAIL_CHARS:]
