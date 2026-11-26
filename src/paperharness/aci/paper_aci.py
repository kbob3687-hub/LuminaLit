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
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from paperharness.aci import errors, path_guard, requirement_guard
from paperharness.aci.policy import ActionPolicy
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
_ENV_VALUE_PATTERNS = {
    "CUDA_VISIBLE_DEVICES": re.compile(r"(?:-1|[0-9]{1,3}(?:,[0-9]{1,3})*)"),
    "OMP_NUM_THREADS": re.compile(r"[1-9][0-9]{0,3}"),
    "MKL_NUM_THREADS": re.compile(r"[1-9][0-9]{0,3}"),
    "TOKENIZERS_PARALLELISM": re.compile(r"(?:true|false|0|1)"),
}


def is_safe_environment_value(name: str, value: object) -> bool:
    pattern = _ENV_VALUE_PATTERNS.get(name)
    return (
        pattern is not None
        and isinstance(value, str)
        and len(value) <= 64
        and pattern.fullmatch(value) is not None
    )


# install 不单独设超时下限。runner 每个动作前会把 aci.timeout_seconds 覆写成
# step_timeout（剩余墙钟预算），它是 TimeBudgetExceeded 收敛的唯一抓手；
# 若在这里用 max(step_timeout, 120) 把预算抬回去，临近 deadline 时一次 pip
# install 可跑满 120s 并绕过总墙钟。与 run_smoke_test 保持同一口径。
# 设计稿 §5.4「提案 4」的下限仍标着「待确认」，在确认前不落地。


class PaperACI:
    def __init__(
        self,
        session: SandboxSession,
        workspace: Path,
        plan: RunPlan,
        timeout_seconds: int,
        policy: ActionPolicy | None = None,
    ):
        self.session = session
        # workspace 先 resolve 一次并缓存：符号链接会让边界判断漂移。
        self.workspace = Path(workspace).resolve(strict=False)
        self.plan = plan
        self.timeout_seconds = timeout_seconds
        # runner 显式传入冻结任务策略；直接构造时也保护 check.py 与预期产物。
        self.policy = policy if policy is not None else ActionPolicy.for_plan(plan)
        # ACI 自己维护网络状态。**不向会话查询**：契约 §1.2 写死沙箱不暴露状态
        # 查询接口，`getattr(session, "network_enabled", True)` 在真沙箱上永远拿到
        # 默认值 True，会让 install 之后把本该断开的网永久打开。
        # 修复期基线约定为断网：runner 在跑测试前会 set_network(False)，自愈发生在
        # 这个断网上下文里，所以 install 结束后必须回到断开状态。
        self._network_state = False

    # ------------------------------------------------------------------ 入口

    def execute(self, action: ToolAction) -> Observation:
        name = getattr(action, "action", "")
        handler = getattr(self, f"_do_{name}", None)
        if handler is None:
            return self._fail(name, errors.UNKNOWN_ACTION)
        # 执行权限是整条流水线的第 0 步：任何拒绝都必须发生在读文件、
        # 写文件、开网、改环境变量之前，不能「用一次非法写入换一次合法读」。
        # replace_exact 先把路径解析到真实文件再交给策略：只比词法路径时，
        # `alias/check.py`（alias -> .）能绕过 check.py 的保护。
        resolved: Path | None = None
        if name == "replace_exact":
            raw = getattr(action, "path", "")
            if isinstance(raw, str):
                try:
                    resolved = path_guard.resolve(self.workspace, raw)
                except path_guard.PathEscape:
                    # 越界仍由动作自己的 _guard 返回 path_escape，保持原标签。
                    resolved = None
        rejected = self.policy.rejection(action, resolved=resolved, workspace=self.workspace)
        if rejected is not None:
            return self._fail(name, rejected)
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
        body = "\n".join(f"{start + offset + 1}: {line}" for offset, line in enumerate(chunk))
        return Observation(
            ok=True,
            action=action.action,
            summary=f"read {len(chunk)} lines from {action.path}",
            stdout_tail=self._tail(body),
        )

    def _do_search_text(self, action) -> Observation:
        # 契约 §2.2「最多 50 条」：Pydantic 只挡上界（le=50），下界和绕过
        # schema 的构造都靠这里兜住；否则 max_results=0 会恒返回 "0 matches"。
        if not 1 <= action.max_results <= 50:
            return self._fail(action.action, errors.invalid_argument("max_results"))

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

        # 空 old 一律拒绝，不做特殊处理。不能用 `raw.count(b"") - 1` 推断：
        # 那个值等于文件长度，长度恰好为 1 时会被判成"唯一命中"而真写入
        # （b"x" + old="" + new="INJECTED" → b"INJECTEDx"）。设计 §10.6 要求
        # 空 old 直接回 replace_not_unique。
        if not action.old:
            return self._fail(action.action, errors.REPLACE_NOT_UNIQUE)

        old = action.old.encode("utf-8")
        occurrences = raw.count(old)
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

        with self._network(True):
            result = self.session.execute(
                ["python", "-m", "pip", "install", requirement], self.timeout_seconds
            )
        return self._from_exec(action.action, result, f"install {requirement}")

    def _do_set_environment(self, action) -> Observation:
        # 白名单与策略共用同一份判定（rejection 已先跑过一次，这里不重复开洞）。
        if action.name not in self.policy.allowed_env_names:
            return self._fail(action.action, errors.env_not_allowed(action.name))
        if not is_safe_environment_value(action.name, action.value):
            return self._fail(action.action, errors.invalid_argument("environment_value"))
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
        """临时切网络，`finally` 恢复到**ACI 自己记录的状态**。

        为什么自己不查询会话：契约 §1.2 明确沙箱不暴露状态查询接口，调用方要
        自己记录。真沙箱 `DockerSandboxSession` 只有私有 `_network_enabled`，
        任何 `getattr(..., True)` 的兜底都会拿到默认值 True——装一次包就把断网
        永久打开，隔离承诺当场失效。替身有公开属性，会让单测假绿。

        `finally` 是硬要求：装包中途抛异常（超时、容器已关）也必须把网络关回去。
        """
        previous = self._network_state
        self.session.set_network(enabled)
        self._network_state = enabled
        try:
            yield
        finally:
            self.session.set_network(previous)
            self._network_state = previous

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
            dirs[:] = [name for name in dirs if not (Path(current) / name).is_symlink()]
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
