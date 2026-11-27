"""内置靶场命中与注入物测试：假沙箱，不依赖 Docker 和真实 clone。"""

import json

from paperharness.agent.fake import FakeLLM
from paperharness.contracts.run import RunRequest
from paperharness.harness.runner import HarnessRunner
from paperharness.harness.store import RunStore
from paperharness.sandbox.fake import FakeSandboxSession
from paperharness.targets import load_targets


def make_targets(tmp_path, repo_url="https://github.com/paper/example"):
    """造一个带 check.py 和 requirements.txt 的靶场目录。"""
    target_dir = tmp_path / "targets" / "paper-example"
    target_dir.mkdir(parents=True)
    (target_dir / "manifest.json").write_text(
        json.dumps(
            {
                "id": "paper-example",
                "repo_url": repo_url,
                "revision": "a" * 40,
                "python_version": "3.10",
                "command": ["python", "check.py"],
                "expected_artifact": "out.txt",
                "failure_class": "none",
                "repairable": True,
                "allowed_actions": [],
                "expected_final": "succeeded",
            }
        ),
        encoding="utf-8",
    )
    (target_dir / "check.py").write_text("print('pass')\n", encoding="utf-8")
    (target_dir / "requirements.txt").write_text("example-pkg==1.0\n", encoding="utf-8")
    return load_targets(tmp_path / "targets")


def make_runner(tmp_path, targets, repo_url, command=None, with_repo_requirements=False):
    store = RunStore(tmp_path / "runs")

    def factory(request, run_dir):
        workspace = run_dir / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        return FakeSandboxSession(artifact_path="out.txt", workspace=workspace)

    def cloner(request, run_dir):
        workspace = run_dir / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        if with_repo_requirements:
            (workspace / "requirements.txt").write_text("repo-dep==2.0\n", encoding="utf-8")
        return "f" * 40

    runner = HarnessRunner(
        store=store,
        llm=FakeLLM(),
        sandbox_factory=factory,
        cloner=cloner,
        targets=targets,
    )
    request = RunRequest(repo_url=repo_url, command=command)
    run_id = store.create_run(request)
    return runner, store, run_id, request


def test_matched_target_uses_manifest_plan_and_injects_files(tmp_path):
    targets = make_targets(tmp_path)
    runner, store, run_id, request = make_runner(tmp_path, targets, targets[0].repo_url)
    result = runner.run(run_id, request)

    assert result.verified is True
    assert result.outcome == "succeeded"
    # 计划来自 manifest，不问模型
    plan = json.loads(store.read_text(run_id, "run_plan.json"))
    assert plan["source"] == "target_manifest"
    assert plan["command"] == ["python", "check.py"]
    assert plan["expected_artifact"] == "out.txt"
    # 注入物进了 workspace
    workspace = store.run_dir(run_id) / "workspace"
    assert (workspace / "check.py").read_text(encoding="utf-8") == "print('pass')\n"
    requirements = (workspace / "requirements.txt").read_text(encoding="utf-8")
    assert "example-pkg==1.0" in requirements
    # 事件里记录了命中与注入
    target_events = [e for e in store.read_events(run_id) if e.kind == "target"]
    assert len(target_events) == 1
    assert target_events[0].data["target_id"] == "paper-example"
    assert "check.py" in target_events[0].data["injected"]


def test_requirements_are_merged_not_replaced(tmp_path):
    targets = make_targets(tmp_path)
    runner, store, run_id, request = make_runner(
        tmp_path, targets, targets[0].repo_url, with_repo_requirements=True
    )
    runner.run(run_id, request)
    requirements = (store.run_dir(run_id) / "workspace" / "requirements.txt").read_text(
        encoding="utf-8"
    )
    # 维护者 pins 在前，原仓库全部约束仍保留。
    assert "repo-dep==2.0" in requirements
    assert "example-pkg==1.0" in requirements
    assert requirements.index("example-pkg==1.0") < requirements.index("repo-dep==2.0")


def test_unmatched_repo_falls_back_to_llm_plan(tmp_path):
    targets = make_targets(tmp_path)
    runner, store, run_id, request = make_runner(
        tmp_path, targets, repo_url="https://github.com/other/repo",
        command=["python", "main.py"],
    )
    runner.run(run_id, request)
    plan = json.loads(store.read_text(run_id, "run_plan.json"))
    assert plan["source"] != "target_manifest"
    workspace = store.run_dir(run_id) / "workspace"
    assert not (workspace / "check.py").exists()
    assert all(e.kind != "target" for e in store.read_events(run_id))


def test_user_command_overrides_manifest(tmp_path):
    targets = make_targets(tmp_path)
    runner, store, run_id, request = make_runner(
        tmp_path, targets, targets[0].repo_url, command=["python", "my_demo.py"],
    )
    runner.run(run_id, request)
    plan = json.loads(store.read_text(run_id, "run_plan.json"))
    # 用户显式指定命令优先，但注入物仍然生效（命令想跑 check.py 或自带脚本都行）
    assert plan["command"] == ["python", "my_demo.py"]
    workspace = store.run_dir(run_id) / "workspace"
    assert (workspace / "check.py").exists()


def test_runner_without_targets_behaves_as_before(tmp_path):
    store = RunStore(tmp_path / "runs")

    def factory(request, run_dir):
        workspace = run_dir / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        return FakeSandboxSession(artifact_path="out.txt", workspace=workspace)

    runner = HarnessRunner(
        store=store,
        llm=FakeLLM(),
        sandbox_factory=factory,
        cloner=lambda request, run_dir: "f" * 40,
    )
    request = RunRequest(
        repo_url="https://github.com/a/b", command=["python", "main.py"],
        expected_artifact="out.txt",
    )
    run_id = store.create_run(request)
    result = runner.run(run_id, request)
    assert result.outcome == "succeeded"


def test_same_repo_url_targets_disambiguated_by_revision(tmp_path):
    """共享 repo_url 的多个靶场按 revision 精确命中，不能总是落到第一个。"""
    targets_root = tmp_path / "targets"
    shared_url = "https://github.com/kbob3687-hub/paperharness-targets"
    specs = [
        ("clean-example", "a" * 40, "none", True, "succeeded", "out.txt"),
        ("missing-dependency", "b" * 40, "missing_dependency", True, "succeeded", "out.txt"),
        ("requires-gpu", "c" * 40, "requires_gpu", False, "failed", None),
    ]
    for tid, rev, fclass, repairable, expected_final, artifact in specs:
        tdir = targets_root / tid
        tdir.mkdir(parents=True)
        (tdir / "manifest.json").write_text(
            json.dumps(
                {
                    "id": tid,
                    "repo_url": shared_url,
                    "revision": rev,
                    "python_version": "3.10",
                    "command": ["python", "main.py"],
                    "expected_artifact": artifact,
                    "failure_class": fclass,
                    "repairable": repairable,
                    "allowed_actions": [],
                    "expected_final": expected_final,
                }
            ),
            encoding="utf-8",
        )
        (tdir / "requirements.txt").write_text(f"{tid}-marker==1.0\n", encoding="utf-8")

    manifests = load_targets(targets_root)
    store = RunStore(tmp_path / "runs")

    def factory(request, run_dir):
        workspace = run_dir / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        return FakeSandboxSession(workspace=workspace)

    runner = HarnessRunner(
        store=store,
        llm=FakeLLM(),
        sandbox_factory=factory,
        cloner=lambda req, run_dir: req.revision,
        targets=manifests,
    )

    request = RunRequest(repo_url=shared_url, revision="c" * 40)
    run_id = store.create_run(request)
    runner.run(run_id, request)

    target_events = [e for e in store.read_events(run_id) if e.kind == "target"]
    assert len(target_events) == 1
    assert target_events[0].data["target_id"] == "requires-gpu"
    plan = json.loads(store.read_text(run_id, "run_plan.json"))
    assert plan["expected_artifact"] is None
    reqs = (store.run_dir(run_id) / "workspace" / "requirements.txt").read_text(encoding="utf-8")
    assert "requires-gpu-marker==1.0" in reqs


def test_matched_target_backfills_revision_for_cloner(tmp_path):
    """未显式传 revision 命中靶场时，必须回填 target.revision 给 cloner 走浅拉取。"""
    targets = make_targets(tmp_path)
    seen_revisions: list[str | None] = []
    store = RunStore(tmp_path / "runs")

    def factory(request, run_dir):
        workspace = run_dir / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        return FakeSandboxSession(artifact_path="out.txt", workspace=workspace)

    def cloner(request, run_dir):
        seen_revisions.append(request.revision)
        return request.revision or "unexpected"

    runner = HarnessRunner(
        store=store,
        llm=FakeLLM(),
        sandbox_factory=factory,
        cloner=cloner,
        targets=targets,
    )
    request = RunRequest(repo_url=targets[0].repo_url)
    run_id = store.create_run(request)
    runner.run(run_id, request)

    assert seen_revisions == ["a" * 40]


def test_check_py_tampering_is_overwritten_before_verification(tmp_path):
    """自愈循环里篡改 check.py 不能逃过最终验证：重测前 Harness 会重新覆盖权威 check.py。"""
    from paperharness.contracts.run import ExecResult, Observation, ReplaceExactAction

    targets = make_targets(tmp_path)
    store = RunStore(tmp_path / "runs")
    observed_check_contents: list[str] = []

    class RecordingSandbox(FakeSandboxSession):
        def execute(self, command, timeout_seconds):
            if command == ["python", "check.py"]:
                observed_check_contents.append(
                    (self.workspace / "check.py").read_text(encoding="utf-8")
                )
            return super().execute(command, timeout_seconds)

    class TamperingACI:
        def __init__(self, session, workspace, plan, timeout_seconds, policy):
            self.workspace = workspace

        def execute(self, action):
            if action.action == "replace_exact":
                (self.workspace / "check.py").write_text("print('tampered')\n", encoding="utf-8")
            return Observation(ok=True, action=action.action, summary="tampered", exit_code=0)

    def factory(request, run_dir):
        workspace = run_dir / "workspace"
        return RecordingSandbox(
            results=[
                ExecResult(
                    exit_code=0, timed_out=False, duration_ms=1,
                    stdout_path="logs/i.out", stderr_path="logs/i.err",
                ),
                ExecResult(
                    exit_code=1, timed_out=False, duration_ms=1,
                    stdout_path="logs/t1.out", stderr_path="logs/t1.err",
                    stderr_tail="AssertionError: bad coeff",
                ),
                ExecResult(
                    exit_code=0, timed_out=False, duration_ms=1,
                    stdout_path="logs/t2.out", stderr_path="logs/t2.err",
                ),
            ],
            artifact_path="out.txt",
            workspace=workspace,
        )

    runner = HarnessRunner(
        store=store,
        llm=FakeLLM(
            actions=[
                ReplaceExactAction(
                    action="replace_exact",
                    path="check.py",
                    old="print('pass')",
                    new="print('tampered')",
                )
            ]
        ),
        sandbox_factory=factory,
        cloner=lambda req, run_dir: "f" * 40,
        aci_factory=TamperingACI,
        targets=targets,
    )
    request = RunRequest(repo_url=targets[0].repo_url, max_repairs=1)
    run_id = store.create_run(request)
    runner.run(run_id, request)

    assert len(observed_check_contents) == 2
    assert observed_check_contents[-1] == "print('pass')\n"
