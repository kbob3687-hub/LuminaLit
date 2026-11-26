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
    ReplaceExactAction,
    RunRequest,
)
from paperharness.harness.runner import HarnessRunner
from paperharness.harness.store import RunStore
from paperharness.sandbox.fake import FakeSandboxSession
from paperharness.targets.manifest import TargetManifest


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


@pytest.mark.parametrize("allowed,live_allowed,can_write", [
    ([], ["replace_exact"], False),
    (["install_requirement"], ["replace_exact"], False),
    (["replace_exact"], [], True),
])
def test_app_executes_confirmed_permissions_after_live_manifest_changes(
    tmp_path, monkeypatch, allowed, live_allowed, can_write,
):
    """从真实组装入口经体检/确认到执行边界，不能只测策略构造。"""
    from fastapi.testclient import TestClient

    from paperharness.app import main

    source = tmp_path / "target"
    source.mkdir()
    (source / "check.py").write_text("print('check')\n", encoding="utf-8")
    target = TargetManifest(
        id="policy-target", repo_url="https://github.com/example/repo", revision="d" * 40,
        python_version="3.10", command=["python", "-I", "check.py"],
        failure_class="missing_dependency", repairable=True,
        allowed_actions=allowed, expected_final="succeeded", source_dir=source,
    )
    histories = []

    class RecordingModel(FakeLLM):
        def propose_action(self, history):
            histories.append(list(history))
            return super().propose_action(history)

    model = RecordingModel(actions=[
        ReadFileAction(action="read_file", path="main.py"),
        ReplaceExactAction(
            action="replace_exact", path="main.py", old="import numpy", new="import math",
        ),
        FinishRepairAction(action="finish_repair", summary="done"),
    ])
    monkeypatch.setattr(main, "load_targets", lambda root: [target])
    monkeypatch.setattr(main, "_build_llm", lambda: model)
    monkeypatch.setenv("PAPERHARNESS_TARGETS", str(source))
    app, worker = main.build_app(tmp_path / "runs", backend="fake")
    runner = worker.runner
    runner.cloner = make_cloner
    sessions = []

    def sandbox(request, run_dir):
        session = FakeSandboxSession(
            results=[failing(), failing()], workspace=run_dir / "workspace",
        )
        sessions.append(session)
        return session

    runner.sandbox_factory = sandbox
    with TestClient(app) as client:
        view_response = client.post("/api/preflights", json={
            "target_id": target.id, "max_repairs": 1,
        })
        assert view_response.status_code == 201, view_response.text
        view = view_response.json()
        # 改动发生在确认之前；运行仍必须使用体检时冻结的权限。
        target.allowed_actions[:] = live_allowed
        confirmed = client.post("/api/runs", json={
            key: view[key] for key in ("preflight_id", "confirmation_digest")
        })
        assert confirmed.status_code == 202, confirmed.text
        run_id = confirmed.json()["run_id"]
        worker.queue.join()
        result = worker.store.read_result(run_id)
        assert result is not None and result.outcome != "internal_error"
        workspace = worker.store.run_dir(run_id) / "workspace"
        assert (workspace / "main.py").read_text(encoding="utf-8") == (
            "import math\n" if can_write else "import numpy\n"
        )
        events = [json.loads(row) for row in (
            worker.store.run_dir(run_id) / "events.jsonl"
        ).read_text(encoding="utf-8").splitlines()]
        action_event = next(row for row in events if row["kind"] == "replace_exact")
        assert action_event["data"]["ok"] is can_write
        if not can_write:
            assert action_event["message"] == "action_not_allowed:replace_exact"
            assert any(
                not obs.ok and obs.summary == "action_not_allowed:replace_exact"
                for history in histories for obs in history
            )
        assert [row["kind"] for row in events if row["state"] == "repairing"][:3] == [
            "read_file", "replace_exact", "finish_repair",
        ]
        # 只允许 Harness 初试/重验；ACI 不得偷偷增加命令、网络或环境副作用。
        assert sessions[0].commands == [target.command, target.command]
        assert sessions[0].environment == {}
