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
    # 仓库自带 + 靶场注入两行都在（追加合并）
    assert "repo-dep==2.0" in requirements
    assert "example-pkg==1.0" in requirements


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
