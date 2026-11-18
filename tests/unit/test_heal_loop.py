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

    def factory(request, run_dir):
        (run_dir / "workspace").mkdir(parents=True, exist_ok=True)
        (run_dir / "workspace" / "requirements.txt").write_text("", encoding="utf-8")
        return FakeSandboxSession(results, workspace=run_dir / "workspace")

    runner = HarnessRunner(
        store=store,
        llm=FakeLLM(actions=actions),
        sandbox_factory=factory,
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

    def factory(request, run_dir):
        (run_dir / "workspace").mkdir(parents=True, exist_ok=True)
        (run_dir / "workspace" / "requirements.txt").write_text("", encoding="utf-8")
        return FakeSandboxSession(
            [make_result(0), make_result(1, "ModuleNotFoundError: No module named 'yaml'")],
            workspace=run_dir / "workspace",
        )

    runner = HarnessRunner(
        store=store,
        llm=FakeLLM(
            actions=[
                InstallRequirementAction(action="install_requirement", requirement="PyYAML")
            ]
        ),
        sandbox_factory=factory,
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


def test_install_failure_enters_heal_loop(tmp_path):
    """安装阶段失败不再被忽略：诊断 → 修复 → 重装 → 重测。"""
    install_fail = make_result(1, "ERROR: No matching distribution found for numpy==9.9")
    runner, store = make_heal_runner(
        tmp_path,
        results=[install_fail, make_result(0), make_result(0)],
        actions=[
            InstallRequirementAction(action="install_requirement", requirement="numpy==1.26.4"),
            FinishRepairAction(action="finish_repair", summary="pinned numpy"),
        ],
        max_repairs=2,
    )
    request = RunRequest(
        repo_url="https://github.com/a/b", command=["python", "main.py"], max_repairs=2
    )
    run_id = store.create_run(request)
    result = runner.run(run_id, request)

    assert result.verified is True
    assert result.attempts == 1
    states = [e.state for e in store.read_events(run_id)]
    assert "diagnosing" in states


def test_install_failure_without_aci_fails_run(tmp_path):
    """没接 ACI 时安装失败要直接判失败，而不是继续跑测试。"""
    store = RunStore(tmp_path)

    def factory(request, run_dir):
        (run_dir / "workspace").mkdir(parents=True, exist_ok=True)
        (run_dir / "workspace" / "requirements.txt").write_text("", encoding="utf-8")
        return FakeSandboxSession(
            [make_result(1, "ERROR: Could not find a version that satisfies the requirement x")],
            workspace=run_dir / "workspace",
        )

    runner = HarnessRunner(
        store=store,
        llm=FakeLLM(),
        sandbox_factory=factory,
        cloner=lambda request, run_dir: "fake-commit",
    )
    request = RunRequest(repo_url="https://github.com/a/b", command=["python", "main.py"])
    run_id = store.create_run(request)
    result = runner.run(run_id, request)

    assert result.outcome == "failed"
    assert result.failure_category == "install_failed"
    assert result.attempts == 1


def test_time_budget_exhausted_maps_to_budget_exceeded():
    runner = HarnessRunner(
        store=RunStore.__new__(RunStore), llm=None, sandbox_factory=lambda *a: None
    )
    request = RunRequest(repo_url="https://github.com/a/b")
    import pytest

    with pytest.raises(Exception, match="budget"):
        runner._step_timeout(request, deadline=0.0)


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


def test_classify_rules_gpu_path_version_and_deprecated():
    assert (
        classify_by_rules(
            FailureContext(exit_code=1, timed_out=False, stderr_tail="No CUDA-capable device is detected")
        )
        == "requires_gpu"
    )
    assert (
        classify_by_rules(
            FailureContext(exit_code=1, timed_out=False, stderr_tail="Traceback /home/runner/x.py line 3: ValueError")
        )
        is None
    )
    assert (
        classify_by_rules(
            FailureContext(
                exit_code=1,
                timed_out=False,
                stderr_tail="FileNotFoundError: [Errno 2] No such file or directory: '/data/in.csv'",
            )
        )
        == "hardcoded_path"
    )
    assert (
        classify_by_rules(
            FailureContext(
                exit_code=1,
                timed_out=False,
                stderr_tail="AttributeError: `np.float_` was removed in the NumPy 2.0 release.",
            )
        )
        == "version_conflict"
    )
    assert (
        classify_by_rules(
            FailureContext(
                exit_code=1,
                timed_out=False,
                stderr_tail="TypeError: load() got an unexpected keyword argument 'verbose'",
            )
        )
        == "deprecated_api"
    )


def test_heal_seeds_failure_context_into_propose_action_history(tmp_path):
    """每轮 REPAIRING 必须把当轮失败的 stderr_tail、类别和命令喂进 propose_action(history)。"""
    seen_histories: list[list[Observation]] = []

    class InspectingLLM(FakeLLM):
        def propose_action(self, history):
            seen_histories.append(list(history))
            return FinishRepairAction(action="finish_repair", summary="inspected")

    store = RunStore(tmp_path)

    def factory(request, run_dir):
        (run_dir / "workspace").mkdir(parents=True, exist_ok=True)
        (run_dir / "workspace" / "requirements.txt").write_text("", encoding="utf-8")
        return FakeSandboxSession(
            [
                make_result(0),
                make_result(1, "ERR_ROUND_1"),
                make_result(1, "ERR_ROUND_2"),
                make_result(0),
            ],
            workspace=run_dir / "workspace",
        )

    runner = HarnessRunner(
        store=store,
        llm=InspectingLLM(),
        sandbox_factory=factory,
        cloner=lambda req, run_dir: "fake-commit",
        aci_factory=FakeACI,
    )
    request = RunRequest(
        repo_url="https://github.com/a/b", command=["python", "main.py"], max_repairs=2
    )
    run_id = store.create_run(request)
    result = runner.run(run_id, request)

    assert result.verified is True
    assert len(seen_histories) == 2
    assert any("ERR_ROUND_1" in obs.stderr_tail for obs in seen_histories[0])
    assert any("ERR_ROUND_2" in obs.stderr_tail for obs in seen_histories[1])


def test_heal_breaks_early_on_repeated_identical_action(tmp_path):
    from paperharness.contracts.run import ReadFileAction

    calls = 0

    class RepeatingLLM(FakeLLM):
        def propose_action(self, history):
            nonlocal calls
            calls += 1
            return ReadFileAction(action="read_file", path="main.py")

    store = RunStore(tmp_path)

    def factory(request, run_dir):
        (run_dir / "workspace").mkdir(parents=True, exist_ok=True)
        (run_dir / "workspace" / "requirements.txt").write_text("", encoding="utf-8")
        return FakeSandboxSession(
            [make_result(0), make_result(1, "boom"), make_result(1, "boom")],
            workspace=run_dir / "workspace",
        )

    runner = HarnessRunner(
        store=store,
        llm=RepeatingLLM(),
        sandbox_factory=factory,
        cloner=lambda req, run_dir: "fake-commit",
        aci_factory=FakeACI,
    )
    request = RunRequest(
        repo_url="https://github.com/a/b", command=["python", "main.py"], max_repairs=1
    )
    run_id = store.create_run(request)
    result = runner.run(run_id, request)

    assert result.outcome == "budget_exceeded"
    assert calls == 2  # 第二次给出完全相同动作即熔断，不会空转 20 次
    loop_events = [e for e in store.read_events(run_id) if e.kind == "loop_stopped"]
    assert len(loop_events) == 1


def test_heal_inner_loop_respects_wall_clock_deadline(tmp_path):
    """内层动作循环必须受总墙钟 deadline 约束，超时收敛 budget_exceeded。"""
    from paperharness.harness import runner as runner_mod

    store = RunStore(tmp_path)

    def factory(request, run_dir):
        (run_dir / "workspace").mkdir(parents=True, exist_ok=True)
        (run_dir / "workspace" / "requirements.txt").write_text("", encoding="utf-8")
        return FakeSandboxSession(
            [make_result(0), make_result(1, "boom")],
            workspace=run_dir / "workspace",
        )

    class SlowLLM(FakeLLM):
        def classify_failure(self, context):
            return "unknown"

    runner = HarnessRunner(
        store=store,
        llm=SlowLLM(),
        sandbox_factory=factory,
        cloner=lambda req, run_dir: "fake-commit",
        aci_factory=FakeACI,
    )
    request = RunRequest(
        repo_url="https://github.com/a/b",
        command=["python", "main.py"],
        timeout_seconds=30,
        max_repairs=2,
    )
    run_id = store.create_run(request)
    # 强行让墙钟预算在首轮 REPAIRING 内耗尽
    orig_factor = runner_mod.TOTAL_BUDGET_FACTOR
    orig_grace = runner_mod.TOTAL_BUDGET_GRACE_SECONDS
    runner_mod.TOTAL_BUDGET_FACTOR = 0
    runner_mod.TOTAL_BUDGET_GRACE_SECONDS = 0
    try:
        result = runner.run(run_id, request)
    finally:
        runner_mod.TOTAL_BUDGET_FACTOR = orig_factor
        runner_mod.TOTAL_BUDGET_GRACE_SECONDS = orig_grace

    assert result.outcome == "budget_exceeded"
    assert result.failure_category == "time_budget_exceeded"


def test_llm_agent_includes_stderr_and_skips_llm_when_command_given(tmp_path, monkeypatch):
    from paperharness.agent.llm_agent import OpenAICompatibleLLM

    llm = OpenAICompatibleLLM(
        base_url="https://example.invalid/v1",
        api_key="sk-test",
        model="test-model",
    )
    captured_users: list[str] = []

    def fake_chat_json(system, user):
        captured_users.append(user)
        return {"action": "finish_repair", "summary": "ok"}

    monkeypatch.setattr(llm, "_chat_json", fake_chat_json)
    action = llm.propose_action(
        [
            Observation(
                ok=False,
                action="test_failed",
                summary="failed",
                exit_code=1,
                stdout_tail="OUT_INFO",
                stderr_tail="TRACEBACK_STDERR",
            )
        ]
    )
    assert action.action == "finish_repair"
    assert "OUT_INFO" in captured_users[0]
    assert "TRACEBACK_STDERR" in captured_users[0]

    def should_not_call(*args, **kwargs):
        raise AssertionError("LLM should not be called when command is already provided")

    monkeypatch.setattr(llm, "_chat_json", should_not_call)
    plan = llm.propose_run_plan_with_workspace(
        RunRequest(repo_url="https://github.com/a/b", command=["python", "demo.py"]),
        tmp_path,
    )
    assert plan.source == "user"
    assert plan.command == ["python", "demo.py"]
