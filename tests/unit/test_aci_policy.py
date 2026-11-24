"""ACI 执行权限与验收链保护（评审稿 §4.1 / R06 / §13）。

全部用 FakeSandboxSession，不依赖 Docker。重点断言两件事：
  1. 拒绝发生在任何副作用之前（不写文件、不开网、不改环境变量）；
  2. 观察类动作（读、搜、跑测试）不被「修复越界」误伤。
"""

import os
import subprocess
from types import SimpleNamespace

import pytest

from paperharness.aci import ActionPolicy, PaperACI, build_aci_factory
from paperharness.aci.policy import SIDE_EFFECTS
from paperharness.contracts.run import (
    InstallRequirementAction,
    ReadFileAction,
    ReplaceExactAction,
    RunPlan,
    SetEnvironmentAction,
)
from paperharness.sandbox.fake import FakeSandboxSession


def plan(artifact="reports/out.txt", source="target_manifest") -> RunPlan:
    return RunPlan(
        resolved_revision="0" * 40,
        python_version="3.10",
        command=["python", "-I", "check.py"],
        expected_artifact=artifact,
        source=source,
    )


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "workspace"
    (root / "src").mkdir(parents=True)
    (root / "src" / "main.py").write_text("print('hello')\n", encoding="utf-8")
    (root / "check.py").write_text("# server-owned validator\n", encoding="utf-8")
    (root / "reports").mkdir()
    (root / "reports" / "out.txt").write_text("old", encoding="utf-8")
    return root


@pytest.fixture
def session(workspace):
    return FakeSandboxSession(workspace=workspace)


def make_aci(session, workspace, p=None, policy=None):
    return PaperACI(session, workspace, p or plan(), timeout_seconds=300, policy=policy)


@pytest.mark.parametrize("path", ["check.py", "./check.py", "src/../check.py", "src\\..\\check.py"])
def test_check_py_is_protected_and_unchanged(session, workspace, path):
    aci = make_aci(session, workspace)
    before = (workspace / "check.py").read_bytes()
    obs = aci.execute(ReplaceExactAction(action="replace_exact", path=path, old="#", new="X"))
    assert obs.ok is False
    assert obs.summary.startswith("protected_file:")
    assert (workspace / "check.py").read_bytes() == before


def test_expected_artifact_is_protected(session, workspace):
    aci = make_aci(session, workspace)
    obs = aci.execute(ReplaceExactAction(action="replace_exact", path="reports/out.txt", old="old", new="faked"))
    assert obs.ok is False and obs.summary == "protected_file:reports/out.txt"
    assert (workspace / "reports" / "out.txt").read_text(encoding="utf-8") == "old"


def test_reading_validator_is_allowed(session, workspace):
    aci = make_aci(session, workspace)
    obs = aci.execute(ReadFileAction(action="read_file", path="check.py", start_line=1, line_count=5))
    assert obs.ok is True


def test_normal_source_file_is_writable(session, workspace):
    aci = make_aci(session, workspace)
    obs = aci.execute(ReplaceExactAction(action="replace_exact", path="src/main.py", old="hello", new="world"))
    assert obs.ok is True
    assert "world" in (workspace / "src" / "main.py").read_text(encoding="utf-8")


def test_path_escape_still_wins(session, workspace):
    """归一化不能把越界路径吞掉；仍由既有 path_guard 判 path_escape。"""
    aci = make_aci(session, workspace)
    obs = aci.execute(ReplaceExactAction(action="replace_exact", path="../../etc/passwd", old="a", new="b"))
    assert obs.ok is False and obs.summary.startswith("path_escape:")


def manifest(actions, artifact="reports/out.txt"):
    return SimpleNamespace(id="t", allowed_actions=actions, expected_artifact=artifact)


def test_manifest_restricts_side_effects_without_touching_network(session, workspace, monkeypatch):
    calls = []
    monkeypatch.setattr(session, "set_network", lambda v: calls.append(("network", v)))
    monkeypatch.setattr(session, "execute", lambda *a, **k: calls.append(("execute", a)))
    policy = ActionPolicy.for_manifest(manifest(["replace_exact"]), plan())
    aci = make_aci(session, workspace, policy=policy)
    obs = aci.execute(InstallRequirementAction(action="install_requirement", requirement="numpy==1.26"))
    assert obs.ok is False and obs.summary == "action_not_allowed:install_requirement"
    assert calls == [], "被拒的动作不得开网或执行命令"


def test_manifest_allows_declared_action(session, workspace):
    policy = ActionPolicy.for_manifest(manifest(["replace_exact"]), plan())
    aci = make_aci(session, workspace, policy=policy)
    obs = aci.execute(ReplaceExactAction(action="replace_exact", path="src/main.py", old="hello", new="ok"))
    assert obs.ok is True


def test_empty_allowed_actions_means_no_extra_restriction(session, workspace):
    """一次通过的靶场声明空列表，不表示禁止模型读日志或跑测试。"""
    policy = ActionPolicy.for_manifest(manifest([]), plan())
    aci = make_aci(session, workspace, policy=policy)
    assert aci.execute(ReadFileAction(action="read_file", path="check.py")).ok is True
    assert aci.execute(ReplaceExactAction(action="replace_exact", path="src/main.py", old="hello", new="ok")).ok is True


def test_env_and_requirement_scope(session, workspace):
    env_policy = ActionPolicy(allowed_env_names=frozenset({"OMP_NUM_THREADS"}))
    aci = make_aci(session, workspace, policy=env_policy)
    assert aci.execute(SetEnvironmentAction(action="set_environment", name="PATH", value="/tmp")).summary == "env_not_allowed:PATH"

    req_policy = ActionPolicy(allowed_requirements=frozenset({"numpy==1.26.4"}))
    aci = make_aci(session, workspace, policy=req_policy)
    obs = aci.execute(InstallRequirementAction(action="install_requirement", requirement="requests"))
    assert obs.ok is False and obs.summary == "change_not_allowed:requests"


def test_writable_prefixes(session, workspace):
    policy = ActionPolicy(writable_prefixes=("src",))
    aci = make_aci(session, workspace, policy=policy)
    obs = aci.execute(ReplaceExactAction(action="replace_exact", path="notes.txt", old="a", new="b"))
    assert obs.ok is False and obs.summary == "change_not_allowed:notes.txt"


def test_build_aci_factory_keeps_runner_signature(session, workspace):
    factory = build_aci_factory()
    aci = factory(session, workspace, plan(), 300)
    assert isinstance(aci, PaperACI)
    assert aci.execute(ReplaceExactAction(action="replace_exact", path="check.py", old="#", new="X")).ok is False


def test_unknown_action_still_uses_its_own_tag(session, workspace):
    aci = make_aci(session, workspace)
    obs = aci.execute(SimpleNamespace(action="rm_rf", path="/"))
    assert obs.ok is False and obs.summary == "unknown_action"


# ---------------------------------------------------------------------------
# 队长 review（PR #37）：符号链接别名、run_smoke_test 越权、生产路径接线边界
# ---------------------------------------------------------------------------


def _make_alias(workspace):
    """在 workspace 里造一个指向 workspace 自身的目录别名。

    Linux 用符号链接；Windows 无创建符号链接权限时退回 `mklink /J`（目录
    联接，不需管理员），两者都会被 `Path.resolve()` 解析到同一个真实文件。
    """
    alias = workspace / "alias"
    try:
        alias.symlink_to(".", target_is_directory=True)
        return alias
    except (OSError, NotImplementedError):
        pass
    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(alias), str(workspace)],
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            return alias
    pytest.skip("当前环境既不能创建符号链接，也不能创建目录联接")


def test_symlink_alias_cannot_bypass_protected_file(session, workspace):
    """仓库预置 `alias -> .` 后，alias/check.py 与 check.py 是同一个真实文件。

    旧实现只比词法归一化后的相对路径，别名会被当成普通源码放行。这里断言
    文件内容不变，且拒绝发生在任何命令执行之前。
    """
    _make_alias(workspace)

    aci = make_aci(session, workspace)
    before = (workspace / "check.py").read_bytes()
    obs = aci.execute(
        ReplaceExactAction(action="replace_exact", path="alias/check.py", old="#", new="X")
    )
    assert obs.ok is False
    assert obs.summary.startswith("protected_file:"), obs.summary
    assert (workspace / "check.py").read_bytes() == before, "符号链接别名改写了受保护文件"
    assert session.commands == [], "拒绝必须发生在任何命令执行之前"


def test_rejection_compares_resolved_real_path(workspace):
    """策略本身按调用方解析出的真实路径判断，不依赖词法形态。"""
    policy = ActionPolicy.for_plan(plan())
    action = ReplaceExactAction(action="replace_exact", path="alias/check.py", old="#", new="X")
    real = (workspace / "check.py").resolve(strict=False)

    assert policy.rejection(action, resolved=real, workspace=workspace).startswith("protected_file:")
    # 只给词法路径的旧调用方式看不出别名——这正是必须从 PaperACI 传入
    # path_guard.resolve 结果的原因；生产路径不会走这条分支。
    assert policy.rejection(action) is None


def test_run_smoke_test_requires_manifest_authorization(session, workspace):
    """run_smoke_test 会执行 plan.command，不是无副作用的观察动作。"""
    from paperharness.contracts.run import RunSmokeTestAction

    policy = ActionPolicy.for_manifest(manifest(["replace_exact"]), plan())
    aci = make_aci(session, workspace, policy=policy)
    obs = aci.execute(RunSmokeTestAction(action="run_smoke_test"))
    assert obs.ok is False and obs.summary == "action_not_allowed:run_smoke_test"
    assert session.commands == [], "被拒的冒烟测试不得执行 plan.command"


def test_run_smoke_test_allowed_when_declared(session, workspace):
    from paperharness.contracts.run import RunSmokeTestAction

    policy = ActionPolicy.for_manifest(manifest(["run_smoke_test"]), plan())
    aci = make_aci(session, workspace, policy=policy)
    obs = aci.execute(RunSmokeTestAction(action="run_smoke_test"))
    assert obs.ok is True
    assert session.commands == [["python", "-I", "check.py"]]


def test_for_plan_only_protects_files_not_manifest_scope():
    """默认策略只做文件保护，不宣称按靶场 allowed_actions 已经生效。

    app/main.py 仍是 aci_factory=PaperACI、RunPlan 没有 target_id，接线完成前
    不能把 for_plan() 当成 manifest 权限的实现（见 Issue #40）。
    """
    policy = ActionPolicy.for_plan(plan())
    assert policy.restricted_side_effects is None
    assert "check.py" in policy.protected_paths
    assert "reports/out.txt" in policy.protected_paths
    assert "run_smoke_test" in SIDE_EFFECTS


def test_production_factory_path_blocks_protected_write(session, workspace):
    """模拟 app/main.py 当前使用的四参工厂路径：默认保护必须照常生效。"""
    factory = build_aci_factory()
    aci = factory(session, workspace, plan(), 300)
    assert isinstance(aci, PaperACI)
    obs = aci.execute(ReplaceExactAction(action="replace_exact", path="check.py", old="#", new="X"))
    assert obs.ok is False and obs.summary.startswith("protected_file:")
    assert (workspace / "check.py").read_text(encoding="utf-8").startswith("# server-owned validator")
    assert session.commands == []
