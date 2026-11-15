"""自愈循环状态机测试：假模型 + 假 ACI + 假沙箱，不依赖 Docker 和真实 LLM。"""

from paperharness.agent.classifier import FailureContext, classify_by_rules
from paperharness.agent.fake import FakeLLM
from paperharness.contracts.run import (
    ExecResult,
    FinishRepairAction,
    InstallRequirementAction,
    Observation,
    RunRequest,
)
from paperharness.harness.runner import HarnessRunner
from paperharness.harness.store import RunStore
from paperharness.sandbox.fake import FakeSandboxSession


class FakeACI:
    """契约第 2 节协议的最小假实现：记录动作并全部回 ok。"""

    def __init__(self, session, workspace, plan, timeout_seconds):
        self.actions = []

    def execute(self, action):
        self.actions.append(action)
        return Observation(
            ok=True, action=action.action, summary=f"did {action.action}", exit_code=0
        )


def make_result(exit_code, tail=""):
    return ExecResult(
        exit_code=exit_code,
        timed_out=False,
        duration_ms=5,
        stdout_path="logs/x.out",
        stderr_path="logs/x.err",
        stderr_tail=tail,
    )


def make_heal_runner(tmp_path, results, actions, max_repairs=3):
    store = RunStore(tmp_path)
    runner = HarnessRunner(
        store=store,
        llm=FakeLLM(actions=actions),
        sandbox_factory=lambda request, run_dir: FakeSandboxSession(
            results, workspace=run_dir / "workspace"
        ),
        cloner=lambda request, run_dir: "fake-commit",
        aci_factory=lambda session, workspace, plan, timeout: FakeACI(
            session, workspace, plan, timeout
        ),
    )
    return runner, store


def test_heal_success_after_one_repair(tmp_path):
    failing = make_result(1, "ModuleNotFoundError: No module named 'yaml'")
    passing = make_result(0)
    runner, store = make_heal_runner(
        tmp_path,
        results=[make_result(0), failing, passing],
        actions=[
            InstallRequirementAction(action="install_requirement", requirement="PyYAML"),
            FinishRepairAction(action="finish_repair", summary="installed missing dependency"),
        ],
    )
    request = RunRequest(
        repo_url="https://github.com/a/b",
        command=["python", "main.py"],
        max_repairs=3,
    )
    run_id = store.create_run(request)
    result = runner.run(run_id, request)

    assert result.verified is True
    assert result.outcome == "succeeded"
    assert result.attempts == 2
    assert result.failure_category == "missing_dependency"
    states = [e.state for e in store.read_events(run_id)]
    assert "diagnosing" in states and "repairing" in states


def test_heal_budget_exceeded(tmp_path):
    runner, store = make_heal_runner(
        tmp_path,
        results=[make_result(0), make_result(1, "boom"), make_result(1, "boom")],
        actions=[FinishRepairAction(action="finish_repair", summary="no idea")],
        max_repairs=1,
    )
    request = RunRequest(
        repo_url="https://github.com/a/b", command=["python", "main.py"], max_repairs=1
    )
    run_id = store.create_run(request)
    result = runner.run(run_id, request)

    assert result.outcome == "budget_exceeded"
    assert result.verified is False
    assert result.attempts == 2


def test_unrepairable_short_circuits_without_actions(tmp_path):
    """requires_gpu 不可修复：分类后直接认输，不进 REPAIRING、不烧重验。"""
    runner, store = make_heal_runner(
        tmp_path,
        results=[make_result(0), make_result(1, "RuntimeError: requires GPU device")],
        actions=[
            InstallRequirementAction(action="install_requirement", requirement="torch")
        ],
        max_repairs=3,
    )
    request = RunRequest(
        repo_url="https://github.com/a/b", command=["python", "main.py"], max_repairs=3
    )
    run_id = store.create_run(request)
    result = runner.run(run_id, request)

    assert result.outcome == "unrepairable"
    assert result.failure_category == "requires_gpu"
    assert result.attempts == 1
    states = [e.state for e in store.read_events(run_id)]
    assert "repairing" not in states


def test_no_healing_without_aci_factory(tmp_path):
    """没接 ACI 时保持 no_repair 行为：失败就是 failed，不进循环。"""
    store = RunStore(tmp_path)
    runner = HarnessRunner(
        store=store,
        llm=FakeLLM(
            actions=[
                InstallRequirementAction(action="install_requirement", requirement="PyYAML")
            ]
        ),
        sandbox_factory=lambda request, run_dir: FakeSandboxSession(
            [make_result(0), make_result(1, "ModuleNotFoundError: No module named 'yaml'")],
            workspace=run_dir / "workspace",
        ),
        cloner=lambda request, run_dir: "fake-commit",
    )
    request = RunRequest(
        repo_url="https://github.com/a/b", command=["python", "main.py"], max_repairs=3
    )
    run_id = store.create_run(request)
    result = runner.run(run_id, request)

    assert result.outcome == "failed"
    assert result.attempts == 1
    states = [e.state for e in store.read_events(run_id)]
    assert "diagnosing" not in states


def test_classify_rules_hit_order():
    context = FailureContext(
        exit_code=1,
        timed_out=False,
        stderr_tail="ModuleNotFoundError: No module named 'yaml'",
    )
    assert classify_by_rules(context) == "missing_dependency"

    context = FailureContext(
        exit_code=1,
        timed_out=False,
        stderr_tail="FileNotFoundError: /home/xxx/dataset.bin not found",
    )
    assert classify_by_rules(context) == "missing_private_data"

    context = FailureContext(
        exit_code=1,
        timed_out=False,
        stderr_tail="RuntimeError: CUDA error: no kernel image",
    )
    assert classify_by_rules(context) == "cuda_only"

    context = FailureContext(exit_code=1, timed_out=False, stderr_tail="完全看不懂的报错")
    assert classify_by_rules(context) is None
