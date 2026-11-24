"""ACI 执行权限策略（评审稿 §4.1 / R06 / §13）。

`TargetManifest.allowed_actions` 过去只在加载时校验「名字合法」，ACI 执行
路径从不读它：任何靶场都能执行全部 7 种动作，也能改写服务端注入的
`check.py` 和预期产物 —— 等于让考生自己判卷。本模块把允许范围变成
**执行前必须过的一道门**：

* `ActionPolicy.for_plan()`：无需后端改动即可生效的**默认文件保护**（验收链）。
* `ActionPolicy.for_manifest()`：按受维护任务声明的动作与产物构造。
* `build_aci_factory()`：符合 `HarnessRunner.aci_factory` 签名的工厂。

**当前的接线边界**：`app/main.py` 仍传 `aci_factory=PaperACI`，且 `RunPlan`
没有 `target_id`，`build_aci_factory()` 无法可靠匹配 manifest。因此在队长完成
接线（见 Issue #40）之前，只有 `for_plan()` 的默认文件保护在生产路径生效；
**按靶场 `allowed_actions` 限制尚未生效**，不能作为已完成能力宣称。

只做「先验后做」的拒绝，不新增宿主 shell 工具，不改沙箱行为，不改契约。
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from paperharness.aci import errors

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

DEFAULT_ENV_NAMES = frozenset(
    {"CUDA_VISIBLE_DEVICES", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "TOKENIZERS_PARALLELISM"}
)

#: 会产生副作用、因而必须按 manifest 授权的动作。
#: `run_smoke_test` 会执行 `plan.command`——它会写产物、跑脚本、可能触发网络，
#: 不是「只读观察」；不纳入授权就等于给了一个绕过 allowed_actions 的入口。
#: 纯观察动作（read_file / search_text）和仅结束本轮决策的 finish_repair
#: 始终允许：模型必须先能看报错，才谈得上「修复越界」。
SIDE_EFFECTS = frozenset(
    {"replace_exact", "install_requirement", "set_environment", "run_smoke_test"}
)


def _norm(path: str) -> str:
    """把 `./check.py`、`sub/../check.py`、`a\\b` 归一，用于受保护路径比较。

    越界（`..` 逃出工作区）不在这里判，仍由既有的 `path_guard` 负责。
    """
    parts: list[str] = []
    for part in path.replace("\\", "/").split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            else:
                return path  # 交给 path_guard 判 path_escape
        else:
            parts.append(part)
    return "/".join(parts)


@dataclass(frozen=True)
class ActionPolicy:
    allowed_actions: frozenset[str] = ACTION_NAMES
    #: 非 None 时，副作用动作必须落在 manifest 声明的集合内（修复越界判定）。
    restricted_side_effects: frozenset[str] | None = None
    allowed_env_names: frozenset[str] = DEFAULT_ENV_NAMES
    protected_paths: frozenset[str] = frozenset()
    #: 非 None 时，install_requirement 只能装这里声明的依赖。
    allowed_requirements: frozenset[str] | None = None
    #: 非 None 时，写操作只能落在这些相对路径前缀下。
    writable_prefixes: tuple[str, ...] | None = None

    @classmethod
    def for_plan(cls, plan) -> ActionPolicy:
        """默认策略：合法动作都放行，但验收链文件一律不可写。

        这里**只**落实默认文件保护，不读取 manifest 的 `allowed_actions`；
        按靶场限制须队长完成工厂接线后才生效（见 Issue #40）。

        `expected_artifact` 是验收判据的目标产物；允许模型直接写它，就能
        用伪造产物换取「验证通过」。`check.py` 是服务端注入的断言脚本。
        """
        protected = {"check.py"}
        artifact = getattr(plan, "expected_artifact", None)
        if artifact:
            protected.add(_norm(artifact))
        return cls(protected_paths=frozenset(protected))

    @classmethod
    def for_manifest(cls, manifest, plan=None) -> ActionPolicy:
        """按受维护任务构造：声明的动作集合约束副作用，产物纳入保护。

        `allowed_actions` 为空表示「不额外声明修复限制」（例如一次通过的
        靶场），不是「禁止一切动作」—— 否则模型连读日志都做不到。
        """
        declared = frozenset(getattr(manifest, "allowed_actions", []) or [])
        protected = {"check.py"}
        for candidate in (getattr(manifest, "expected_artifact", None),
                          getattr(plan, "expected_artifact", None)):
            if candidate:
                protected.add(_norm(candidate))
        return cls(
            restricted_side_effects=declared or None,
            protected_paths=frozenset(protected),
        )

    def _protected_real_paths(self, workspace: Path) -> frozenset[str]:
        """把受保护的相对路径解析成真实文件路径，用于防符号链接别名绕过。

        `path_guard.resolve()` 会把 `alias/check.py`（alias -> .）解析到
        `workspace/check.py`；只做词法归一化是比不出来的。Windows 下用
        normcase 抵消大小写差异。
        """
        base = Path(workspace).resolve(strict=False)
        return frozenset(
            os.path.normcase(str((base / rel).resolve(strict=False)))
            for rel in self.protected_paths
        )

    def rejection(
        self,
        action,
        *,
        resolved: Path | None = None,
        workspace: Path | None = None,
    ) -> str | None:
        """返回写死的失败 summary；None 表示允许执行。必须在副作用之前调用。

        `resolved` / `workspace` 由 PaperACI 传入（即 `path_guard.resolve` 的
        结果）。给了就按**真实路径**比较受保护文件，堵住符号链接别名；不给时
        退回词法比较，便于策略本身被独立单测直接调用。
        """
        name = getattr(action, "action", "")
        if name not in self.allowed_actions:
            return errors.action_not_allowed(name)
        if (
            name in SIDE_EFFECTS
            and self.restricted_side_effects is not None
            and name not in self.restricted_side_effects
        ):
            return errors.action_not_allowed(name)

        if name == "replace_exact":
            rel = _norm(getattr(action, "path", ""))
            if resolved is not None and workspace is not None:
                protected_hit = os.path.normcase(str(resolved)) in self._protected_real_paths(workspace)
            else:
                protected_hit = rel in self.protected_paths
            if protected_hit:
                return errors.protected_file(rel)
            if self.writable_prefixes is not None and not any(
                rel == p or rel.startswith(p.rstrip("/") + "/") for p in self.writable_prefixes
            ):
                return errors.change_not_allowed(rel)
        elif name == "install_requirement":
            requirement = getattr(action, "requirement", "")
            if self.allowed_requirements is not None and requirement not in self.allowed_requirements:
                return errors.change_not_allowed(requirement)
        elif name == "set_environment" and getattr(action, "name", "") not in self.allowed_env_names:
            return errors.env_not_allowed(getattr(action, "name", ""))
        return None


def build_aci_factory(policy: ActionPolicy | None = None, targets: Iterable | None = None):
    """返回 `(session, workspace, plan, timeout_seconds) -> ACI` 的工厂。

    `targets` 预留给按靶场选择策略：当前 `HarnessRunner` 的工厂签名不带
    target_id，所以这里只记录映射、不做不可靠的猜测；接入点见 Issue #40。
    不传 policy 时用 `for_plan(plan)`，**只有默认验收链文件保护立即生效**，
    按靶场 `allowed_actions` 限制仍需队长完成接线后才能宣称。
    """
    by_manifest = {getattr(m, "id", None): m for m in (targets or [])}

    def factory(session, workspace, plan, timeout_seconds):
        # 延迟导入，避免 aci/__init__.py 的循环导入。
        from paperharness.aci.paper_aci import PaperACI

        chosen = policy
        if chosen is None:
            manifest = by_manifest.get(getattr(plan, "target_id", None))
            chosen = ActionPolicy.for_manifest(manifest, plan) if manifest else ActionPolicy.for_plan(plan)
        return PaperACI(session, workspace, plan, timeout_seconds, policy=chosen)

    return factory
