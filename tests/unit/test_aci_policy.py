"""ACI 执行权限与验收链保护（评审稿 §4.1 / R06 / §13）。

全部用 FakeSandboxSession，不依赖 Docker。重点断言两件事：
  1. 拒绝发生在任何副作用之前（不写文件、不开网、不改环境变量）；
  2. 观察类动作（读、搜、跑测试）不被「修复越界」误伤。
"""

from types import SimpleNamespace

import pytest

from paperharness.aci import ActionPolicy, PaperACI, build_aci_factory
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
