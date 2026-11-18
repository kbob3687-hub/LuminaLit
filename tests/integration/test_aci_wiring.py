"""ACI 与 Harness 的接缝测试。

单元测试（tests/unit/test_aci.py）验证的是「门岗内部的规则」，这里验证的是
「门岗真的被装在门口」——即 `HarnessRunner` 的自愈循环确实把动作交给 ACI，
而不是绕过它直接碰沙箱。

这条缝在 main 上曾经是断的：`main.py` 构造 `HarnessRunner` 时没传
`aci_factory`，导致 `runner.py` 里的 `if self.aci_factory is not None` 恒为假，
整个 REPAIRING 分支走不到动作执行。所以这里必须有一条测试钉住它。
"""

import json

import pytest

from paperharness.aci import PaperACI
from paperharness.agent.fake import FakeLLM
from paperharness.contracts.run import (
    ExecResult,
    FinishRepairAction,
    ReadFileAction,
    RunRequest,
)
from paperharness.harness.runner import HarnessRunner
from paperharness.harness.store import RunStore
from paperharness.sandbox.fake import FakeSandboxSession


def failing(exit_code: int = 1, stderr: str = "ModuleNotFoundError: numpy") -> ExecResult:
    return ExecResult(
        exit_code=exit_code,
        timed_out=False,
        duration_ms=1,
        stdout_path="logs/out.log",
        stderr_path="logs/err.log",
        stderr_tail=stderr,
    )


def make_cloner(request, run_dir):
    workspace = run_dir / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "main.py").write_text("import numpy\n", encoding="utf-8")
    return "d" * 40


@pytest.fixture
def store(tmp_path):
    return RunStore(tmp_path / "runs")


def test_harness_runner_requires_aci_factory_to_self_heal(store):
    """没有 aci_factory 时，失败后不会进入修复循环——这是 main 上的旧行为。"""

    def sandbox(request, run_dir):
        return FakeSandboxSession(
            results=[failing() for _ in range(5)], workspace=run_dir / "workspace"
        )

    runner = HarnessRunner(
        store=store, llm=FakeLLM(), sandbox_factory=sandbox, cloner=make_cloner
    )
    result = runner.run(
        "no-aci",
        RunRequest(repo_url="https://github.com/example/repo", timeout_seconds=60, max_repairs=1),
    )

    assert runner.aci_factory is None
    assert result.outcome == "failed"
    assert result.attempts == 1


def test_self_heal_routes_actions_through_aci(store):
    """接上 ACI 后，模型提的每个动作都必须经过 `PaperACI.execute`。"""

    seen: list[str] = []

    class ProbeACI(PaperACI):
        def execute(self, action):
            seen.append(action.action)
            return super().execute(action)

    def sandbox(request, run_dir):
        return FakeSandboxSession(
            results=[failing() for _ in range(5)], workspace=run_dir / "workspace"
        )

    llm = FakeLLM(
        actions=[
            ReadFileAction(action="read_file", path="main.py"),
            FinishRepairAction(action="finish_repair", summary="看过依赖了"),
        ]
    )
    runner = HarnessRunner(
        store=store,
        llm=llm,
        sandbox_factory=sandbox,
        cloner=make_cloner,
        aci_factory=ProbeACI,
    )
    runner.run(
        "with-aci",
        RunRequest(repo_url="https://github.com/example/repo", timeout_seconds=60, max_repairs=1),
    )

    assert seen == ["read_file", "finish_repair"]


def test_aci_observation_lands_in_event_stream(store):
    """ACI 的 summary 要能出现在事件流里——前端靠这条链路读失败原因。"""

    def sandbox(request, run_dir):
        return FakeSandboxSession(
            results=[failing() for _ in range(5)], workspace=run_dir / "workspace"
        )

    llm = FakeLLM(
        actions=[
            ReadFileAction(action="read_file", path="main.py"),
            FinishRepairAction(action="finish_repair", summary="看过依赖了"),
        ]
    )
    runner = HarnessRunner(
        store=store,
        llm=llm,
        sandbox_factory=sandbox,
        cloner=make_cloner,
        aci_factory=PaperACI,
    )
    runner.run(
        "events",
        RunRequest(repo_url="https://github.com/example/repo", timeout_seconds=60, max_repairs=1),
    )

    events_path = store.run_dir("events") / "events.jsonl"
    events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
    repairing = [(e["kind"], e["message"]) for e in events if e["state"] == "repairing"]

    assert ("read_file", "read 1 lines from main.py") in repairing
    assert ("finish_repair", "看过依赖了") in repairing


def test_aci_rejection_reaches_model_as_observation(store):
    """门岗拒绝的动作，模型能收到 `ok=False` 和逐字 summary，而不是抛异常。"""

    observed: list[object] = []
    workspace_root = {}

    def sandbox(request, run_dir):
        workspace_root["ws"] = run_dir / "workspace"
        return FakeSandboxSession(
            results=[failing() for _ in range(5)], workspace=run_dir / "workspace"
        )

    class RecordingACI(PaperACI):
        def execute(self, action):
            observation = super().execute(action)
            observed.append(observation)
            return observation

    llm = FakeLLM(
        actions=[
            ReadFileAction(action="read_file", path="../../etc/passwd"),
            FinishRepairAction(action="finish_repair", summary="放弃"),
        ]
    )
    runner = HarnessRunner(
        store=store,
        llm=llm,
        sandbox_factory=sandbox,
        cloner=make_cloner,
        aci_factory=RecordingACI,
    )
    runner.run(
        "reject",
        RunRequest(repo_url="https://github.com/example/repo", timeout_seconds=60, max_repairs=1),
    )

    first = observed[0]
    assert first.ok is False
    assert first.summary == "path_escape:../../etc/passwd"
