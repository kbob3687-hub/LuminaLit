import json
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path, PurePosixPath, PureWindowsPath

from paperharness.agent.classifier import UNREPAIRABLE, FailureContext, classify_by_rules
from paperharness.contracts.run import (
    Observation,
    RunPlan,
    RunRequest,
    RunResult,
    RunState,
    validate_repo_url,
)
from paperharness.harness.requirements_guard import (
    RequirementsNotAllowed,
    validate_requirements_file,
)
from paperharness.harness.store import RunStore
from paperharness.sandbox.base import SandboxClosedError, SandboxSession, SandboxUnavailableError
from paperharness.targets.manifest import TargetManifest

INSTALL_COMMAND = ["python", "-m", "pip", "install", "-r", "requirements.txt"]
MAX_ACTIONS_PER_REPAIR = 20
CLONE_TIMEOUT_SECONDS = 240
MAX_WORKSPACE_BYTES = 200 * 1024 * 1024
_FULL_COMMIT = re.compile(r"^[0-9a-f]{40}$")
# 总墙钟预算 = 单步限时 × 3 + 60 秒，防止每步都不超时但整体卡死工作线程
TOTAL_BUDGET_FACTOR = 3
TOTAL_BUDGET_GRACE_SECONDS = 60
TIME_BUDGET_EXHAUSTED = "time_budget_exceeded"
LLM_UNAVAILABLE = "llm_unavailable"


class TimeBudgetExceeded(RuntimeError):
    """本次运行的墙钟预算耗尽。"""


class _RunCancelled(RuntimeError):
    """用户请求取消：在阶段边界抛出，由 run() 收敛到 CANCELLED 终态。"""


class _RequirementsRejected(RuntimeError):
    """仓库 requirements.txt 内容不合法：整体拒绝安装，run 判失败。"""


class _CloneSizeExceeded(RuntimeError):
    """仓库下载体积超过 200 MiB 上限，不重试。"""


def _rmtree_force(path: Path) -> None:
    """Windows 下 .git/objects/pack 常带只读属性，清目录前先去掉只读位。"""
    if not path.exists():
        return

    def _on_error(func, target_path, _exc) -> None:
        os.chmod(target_path, stat.S_IWRITE)
        func(target_path)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_on_error)
    else:
        shutil.rmtree(path, onerror=_on_error)


def _workspace_size_bytes(workspace: Path) -> int:
    total = 0
    for root, dirs, filenames in os.walk(workspace, followlinks=False):
        dirs[:] = [d for d in dirs if d != ".git"]
        for fname in filenames:
            fpath = Path(root) / fname
            if fpath.is_symlink():
                continue
            try:
                total += fpath.stat().st_size
            except OSError:
                continue
    return total


def _scrub_host_paths(text: str, workspace: Path, run_dir: Path) -> str:
    if not text:
        return text
    replacements = [
        (str(workspace.resolve(strict=False)), "/workspace"),
        (str(workspace), "/workspace"),
        (workspace.resolve(strict=False).as_posix(), "/workspace"),
        (workspace.as_posix(), "/workspace"),
        (str(run_dir.resolve(strict=False)), "/run"),
        (str(run_dir), "/run"),
        (run_dir.resolve(strict=False).as_posix(), "/run"),
        (run_dir.as_posix(), "/run"),
    ]
    for src, dst in replacements:
        if src:
            text = text.replace(src, dst)
    return text


def _resolve_artifact_path(workspace: Path, raw: str) -> Path | None:
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        return None
    if PureWindowsPath(raw).is_absolute() or PurePosixPath(raw).is_absolute() or raw.startswith("~"):
        return None
    ws = workspace.resolve(strict=False)
    candidate = (ws / raw).resolve(strict=False)
    base = os.path.normcase(str(ws))
    target = os.path.normcase(str(candidate))
    if target == base or not target.startswith(base + os.sep):
        return None
    return candidate


def _clone_once(
    clone_url: str,
    revision: str | None,
    workspace: Path,
    git_env: dict[str, str],
) -> str:
    if workspace.exists():
        _rmtree_force(workspace)
    workspace.parent.mkdir(parents=True, exist_ok=True)

    if revision and _FULL_COMMIT.match(revision):
        # 固定完整 commit：只拉该快照（depth 1），避免全历史 clone 把大仓库拖超时
        subprocess.run(
            ["git", "init", "-q", str(workspace)],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
            env=git_env,
        )
        subprocess.run(
            ["git", "-C", str(workspace), "remote", "add", "origin", clone_url],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
            env=git_env,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(workspace),
                "-c",
                "http.version=HTTP/1.1",
                "fetch",
                "--depth",
                "1",
                "--no-tags",
                "--no-recurse-submodules",
                "origin",
                revision,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=CLONE_TIMEOUT_SECONDS,
            env=git_env,
        )
        subprocess.run(
            ["git", "-C", str(workspace), "checkout", "-q", "FETCH_HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
            env=git_env,
        )
    elif revision:
        try:
            subprocess.run(
                [
                    "git",
                    "-c",
                    "http.version=HTTP/1.1",
                    "clone",
                    "--depth",
                    "1",
                    "--single-branch",
                    "--no-tags",
                    "--no-recurse-submodules",
                    "--branch",
                    revision,
                    clone_url,
                    str(workspace),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=CLONE_TIMEOUT_SECONDS,
                env=git_env,
            )
        except subprocess.CalledProcessError:
            if workspace.exists():
                _rmtree_force(workspace)
            subprocess.run(
                [
                    "git",
                    "-c",
                    "http.version=HTTP/1.1",
                    "clone",
                    "--no-tags",
                    "--no-recurse-submodules",
                    clone_url,
                    str(workspace),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=CLONE_TIMEOUT_SECONDS,
                env=git_env,
            )
            subprocess.run(
                ["git", "-C", str(workspace), "checkout", "-q", revision],
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
                env=git_env,
            )
    else:
        subprocess.run(
            [
                "git",
                "-c",
                "http.version=HTTP/1.1",
                "clone",
                "--depth",
                "1",
                "--single-branch",
                "--no-tags",
                "--no-recurse-submodules",
                clone_url,
                str(workspace),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=CLONE_TIMEOUT_SECONDS,
            env=git_env,
        )

    if _workspace_size_bytes(workspace) > MAX_WORKSPACE_BYTES:
        _rmtree_force(workspace)
        raise _CloneSizeExceeded("repository size exceeds 200 MiB limit")

    head = subprocess.run(
        ["git", "-C", str(workspace), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
        env=git_env,
    )
    return head.stdout.strip()


def git_clone(request: RunRequest, run_dir: Path) -> str:
    """默认 cloner：宿主机 clone 仓库（契约 1.1：沙箱不做任何 git 网络操作）。"""
    # 防御纵深：即使绕过 API 的 schema 校验，也绝不允许本地路径/file:///
    # ssh:// 源——git clone 支持本地目录，会把宿主机任意目录搬进容器挂载区。
    try:
        validate_repo_url(request.repo_url)
    except ValueError as exc:
        raise ValueError(f"invalid repo_url: {exc}") from exc

    workspace = run_dir / "workspace"
    git_env = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_LFS_SKIP_SMUDGE": "1",
    }
    candidates = [request.repo_url, request.repo_url]
    mirror = os.environ.get("PAPERHARNESS_GIT_MIRROR", "").strip()
    if mirror and request.repo_url.startswith("https://github.com/"):
        candidates.append(f"{mirror.rstrip('/')}/{request.repo_url}")

    last_error: Exception | None = None
    for clone_url in candidates:
        try:
            return _clone_once(clone_url, request.revision, workspace, git_env)
        except _CloneSizeExceeded as exc:
            raise RuntimeError(str(exc)) from exc
        except subprocess.CalledProcessError as exc:
            if workspace.exists():
                _rmtree_force(workspace)
            detail = _scrub_host_paths((exc.stderr or exc.stdout or "").strip(), workspace, run_dir)
            if not detail:
                detail = f"exit status {exc.returncode}"
            last_error = RuntimeError(f"git exited with {exc.returncode}: {detail[:1000]}")
        except subprocess.TimeoutExpired as exc:
            if workspace.exists():
                _rmtree_force(workspace)
            last_error = RuntimeError(
                f"git clone timed out after {int(exc.timeout)}s (network transfer incomplete)"
            )
    assert last_error is not None
    raise last_error


class HarnessRunner:
    def __init__(
        self,
        store: RunStore,
        llm,
        sandbox_factory: Callable[[RunRequest, Path], SandboxSession],
        cloner: Callable[[RunRequest, Path], str] = git_clone,
        aci_factory: Callable[[SandboxSession, Path, RunPlan, int], object] | None = None,
        targets: list[TargetManifest] | None = None,
    ):
        self.store = store
        self.llm = llm
        self.sandbox_factory = sandbox_factory
        self.cloner = cloner
        self.aci_factory = aci_factory
        self.targets = list(targets or [])
        # run_id -> (取消事件, 当前 session)；abort() 由 API 线程跨线程调用。
        self._active: dict[str, tuple[threading.Event, SandboxSession | None]] = {}
        self._active_lock = threading.RLock()

    # ----------------------------------------------------------- 取消控制

    def abort(self, run_id: str) -> bool:
        """请求取消一个运行中的 run（API 线程调用，契约 13.6：终止当前容器）。

        置取消事件 + 立即销毁容器；工作线程在最近的阶段边界收敛到
        CANCELLED 终态。对未在跑的 run 返回 False。
        """
        with self._active_lock:
            entry = self._active.get(run_id)
        if entry is None:
            return False
        event, session = entry
        event.set()
        if session is not None:
            try:
                session.close()  # 幂等；杀容器让阻塞中的 execute 尽快返回
            except Exception:  # noqa: BLE001, S110 - 取消路径尽力而为
                pass
        return True

    def _register_active(
        self, run_id: str, cancel: threading.Event, session: SandboxSession | None = None
    ) -> None:
        with self._active_lock:
            self._active[run_id] = (cancel, session)

    def _unregister_active(self, run_id: str) -> None:
        with self._active_lock:
            self._active.pop(run_id, None)

    def _check_cancel(self, run_id: str) -> None:
        """阶段边界检查：用户已取消就立即停止，不再消耗预算和 LLM 调用。"""
        if self._cancel_requested(run_id):
            raise _RunCancelled()

    def _cancel_requested(self, run_id: str) -> bool:
        with self._active_lock:
            entry = self._active.get(run_id)
        return entry is not None and entry[0].is_set()

    def _cancelled(
        self,
        run_id: str,
        session: SandboxSession | None,
        attempts: int,
        request: RunRequest | None = None,
        plan: RunPlan | None = None,
        resolved_revision: str | None = None,
    ) -> RunResult:
        """CANCELLED 终态：不 export 不到的 patch 就留空，不留僵尸状态。"""
        result = RunResult(
            run_id=run_id,
            outcome="cancelled",
            verified=False,
            attempts=attempts,
            resolved_revision=resolved_revision,
            patch_url=f"/api/runs/{run_id}/patch",
            environment_url=f"/api/runs/{run_id}/environment",
            report_url=f"/api/runs/{run_id}/report",
        )
        patch = ""
        if session is not None:
            try:
                patch = session.export_diff()
            except Exception:  # noqa: BLE001 - 会话可能已被 abort 关闭
                patch = ""
        self.store.write_text(run_id, "patch.diff", patch)
        self.store.write_text(
            run_id,
            "environment.json",
            json.dumps(
                self._build_environment_manifest(request, plan, resolved_revision, session),
                ensure_ascii=False,
                indent=2,
            ),
        )
        self.store.write_text(
            run_id, "report.md", "# Report\n\noutcome=cancelled\nverified=False\n"
        )
        self.store.write_status(run_id, RunState.CANCELLED, {})
        self.store.append_event(
            run_id, RunState.CANCELLED, "final", "run cancelled", {"outcome": "cancelled"}
        )
        self.store.write_result(run_id, result)
        return result

    # ------------------------------------------------------------ 内置靶场

    def _match_target(self, request: RunRequest) -> TargetManifest | None:
        """按 (repo_url, revision) 匹配内置靶场；未指定 revision 时回退到无故障或首个候选。"""
        candidates = [target for target in self.targets if target.repo_url == request.repo_url]
        if not candidates:
            return None
        if request.revision is not None:
            for target in candidates:
                if target.revision == request.revision:
                    return target
            return None
        for target in candidates:
            if target.failure_class == "none":
                return target
        return candidates[0]

    def _inject_target_files(self, run_id: str, workspace: Path, target: TargetManifest) -> None:
        """靶场注入物：check.py 与 requirements.txt 拷进 workspace，仓库本身一个字不动。

        仓库自带 requirements.txt 时追加（pip 重复行无害）。check.py 是我们
        写死的确定性断言，模型无权创建或修改——验证不能让考生自己判卷。
        """
        if target.source_dir is None:
            return
        injected: list[str] = []
        check_src = target.source_dir / "check.py"
        if check_src.is_file():
            shutil.copy2(check_src, workspace / "check.py")
            injected.append("check.py")
        req_src = target.source_dir / "requirements.txt"
        if req_src.is_file():
            extra = req_src.read_text(encoding="utf-8")
            workspace_req = workspace / "requirements.txt"
            existing = (
                workspace_req.read_text(encoding="utf-8") if workspace_req.exists() else ""
            )
            workspace_req.write_text(existing + extra, encoding="utf-8")
            injected.append("requirements.txt")
        if injected:
            self.store.append_event(
                run_id,
                RunState.PREPARING,
                "target",
                f"matched target {target.id}: injected {', '.join(injected)}",
                {"target_id": target.id, "injected": injected},
            )

    def run(self, run_id: str, request: RunRequest) -> RunResult:
        run_dir = self.store.run_dir(run_id)
        workspace = run_dir / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        # 内置靶场命中（设计 4.2 第 1 步）：回填 python_version 和 revision，确保浅拉取精确提交
        target = self._match_target(request)
        if target is not None:
            updates: dict[str, object] = {}
            if request.python_version is None:
                updates["python_version"] = target.python_version
            if request.revision is None:
                updates["revision"] = target.revision
            if updates:
                request = request.model_copy(update=updates)
        deadline = (
            time.monotonic()
            + request.timeout_seconds * TOTAL_BUDGET_FACTOR
            + TOTAL_BUDGET_GRACE_SECONDS
        )
        cancel = threading.Event()
        session: SandboxSession | None = None
        resolved_revision: str | None = None
        plan: RunPlan | None = None
        attempts_tracker = [1]
        self._register_active(run_id, cancel, None)
        try:
            self._check_cancel(run_id)
            self.store.write_status(run_id, RunState.PREPARING, {})
            try:
                resolved_revision = self.cloner(request, run_dir)
            except Exception as exc:  # noqa: BLE001
                return self._fail(
                    run_id,
                    "clone_failed",
                    f"clone failed: {exc}",
                    request=request,
                )
            self._check_cancel(run_id)
            self.store.append_event(
                run_id,
                RunState.PREPARING,
                "preparing",
                f"cloned at {resolved_revision}",
            )
            if target is not None:
                self._inject_target_files(run_id, workspace, target)

            self.store.write_status(run_id, RunState.PLANNING, {})
            if target is not None and request.command is None:
                # 内置靶场：运行计划来自人工验证过的 manifest（设计 4.2 第 1 步），
                # 不问模型、不猜命令
                plan = RunPlan(
                    resolved_revision=resolved_revision,
                    python_version=request.python_version or target.python_version,
                    command=target.command,
                    expected_artifact=request.expected_artifact or target.expected_artifact,
                    source="target_manifest",
                )
            else:
                try:
                    propose_with_ws = getattr(self.llm, "propose_run_plan_with_workspace", None)
                    if callable(propose_with_ws):
                        plan = propose_with_ws(request, workspace)
                    else:
                        plan = self.llm.propose_run_plan(request)
                except Exception as exc:  # noqa: BLE001 - LLM 网关故障不杀死 worker（设计 18.4）
                    return self._fail(
                        run_id,
                        LLM_UNAVAILABLE,
                        f"plan generation failed: {exc}",
                        request=request,
                        resolved_revision=resolved_revision,
                    )
                plan_updates: dict[str, object] = {"resolved_revision": resolved_revision}
                if request.command is not None:
                    plan_updates["command"] = list(request.command)
                    plan_updates["source"] = "user"
                if request.expected_artifact is not None:
                    plan_updates["expected_artifact"] = request.expected_artifact
                if request.python_version is not None:
                    plan_updates["python_version"] = request.python_version
                plan = plan.model_copy(update=plan_updates)

            if not plan.command or any(not str(part).strip() for part in plan.command):
                return self._fail(
                    run_id,
                    "missing_run_command",
                    "unable to determine a runnable command; please provide command explicitly",
                    request=request,
                    plan=plan,
                    resolved_revision=resolved_revision,
                )
            if (
                plan.expected_artifact is not None
                and _resolve_artifact_path(workspace, plan.expected_artifact) is None
            ):
                return self._fail(
                    run_id,
                    "invalid_expected_artifact",
                    f"expected_artifact must be a relative path inside workspace: {plan.expected_artifact}",
                    request=request,
                    plan=plan,
                    resolved_revision=resolved_revision,
                )

            self._check_cancel(run_id)
            self.store.write_text(run_id, "run_plan.json", plan.model_dump_json(indent=2))
            self.store.append_event(
                run_id,
                RunState.PLANNING,
                "plan",
                "run plan resolved",
                plan.model_dump(mode="json"),
            )

            sandbox_request = request.model_copy(update={"python_version": plan.python_version})
            try:
                session = self.sandbox_factory(sandbox_request, run_dir)
            except SandboxUnavailableError as exc:
                return self._fail(
                    run_id,
                    "service_interrupted",
                    f"sandbox unavailable: {exc}",
                    request=request,
                    plan=plan,
                    resolved_revision=resolved_revision,
                )
            self._register_active(run_id, cancel, session)
            self._check_cancel(run_id)

            install = self._run_install(run_id, request, session, workspace, deadline)
            attempts_tracker[0] = 1

            if install is not None and install.exit_code != 0:
                attempts_tracker[0] = 0
                # 安装失败不再被忽略：算进自愈，或直接判失败
                if self.aci_factory is not None and request.max_repairs > 0:
                    healed = self._self_heal(
                        run_id,
                        request,
                        session,
                        workspace,
                        plan,
                        install,
                        phase="install",
                        attempts_tracker=attempts_tracker,
                        deadline=deadline,
                        target=target,
                    )
                    verified = healed["verified"]
                    attempts = healed["attempts"]
                    failure_category = healed["failure_category"]
                    outcome = "succeeded" if verified else healed["outcome"]
                else:
                    category = classify_by_rules(self._context(install)) or "install_failed"
                    return self._fail(
                        run_id,
                        category,
                        (install.stderr_tail or install.stdout_tail)[:2000],
                        session=session,
                        request=request,
                        plan=plan,
                        resolved_revision=resolved_revision,
                        attempts=1,
                    )
            else:
                test = self._run_test(
                    run_id, request, session, plan, deadline, target=target
                )
                verified = self._is_verified(test, plan, workspace)
                attempts = 1
                attempts_tracker[0] = 1
                failure_category = None if verified else "smoke_test_failed"
                outcome = "succeeded" if verified else "failed"

                if not verified and self.aci_factory is not None and request.max_repairs > 0:
                    healed = self._self_heal(
                        run_id,
                        request,
                        session,
                        workspace,
                        plan,
                        test,
                        phase="test",
                        attempts_tracker=attempts_tracker,
                        deadline=deadline,
                        target=target,
                    )
                    verified = healed["verified"]
                    attempts = healed["attempts"]
                    failure_category = healed["failure_category"]
                    outcome = "succeeded" if verified else healed["outcome"]

            state = RunState.SUCCEEDED if verified else RunState.FAILED
            self.store.write_text(run_id, "patch.diff", session.export_diff())
            self.store.write_text(
                run_id,
                "report.md",
                "# Report\n\n"
                f"outcome={outcome}\nverified={verified}\nattempts={attempts}\n"
                f"failure_category={failure_category}\n",
            )
            self.store.write_text(
                run_id,
                "environment.json",
                json.dumps(
                    self._build_environment_manifest(
                        request, plan, resolved_revision, session
                    ),
                    ensure_ascii=False,
                    indent=2,
                ),
            )

            result = RunResult(
                run_id=run_id,
                outcome=outcome,
                verified=verified,
                attempts=attempts,
                failure_category=failure_category,
                resolved_revision=plan.resolved_revision,
                patch_url=f"/api/runs/{run_id}/patch",
                environment_url=f"/api/runs/{run_id}/environment",
                report_url=f"/api/runs/{run_id}/report",
            )
            self.store.write_status(run_id, state, {"verified": verified})
            self.store.append_event(
                run_id, state, "final", f"run {result.outcome}", {"verified": verified}
            )
            self.store.write_result(run_id, result)
            return result
        except _RunCancelled:
            return self._cancelled(
                run_id,
                session,
                attempts_tracker[0],
                request=request,
                plan=plan,
                resolved_revision=resolved_revision,
            )
        except TimeBudgetExceeded as exc:
            return self._fail(
                run_id,
                TIME_BUDGET_EXHAUSTED,
                str(exc),
                outcome="budget_exceeded",
                session=session,
                request=request,
                plan=plan,
                resolved_revision=resolved_revision,
                attempts=attempts_tracker[0],
            )
        except _RequirementsRejected as exc:
            return self._fail(
                run_id,
                "requirement_rejected",
                str(exc),
                session=session,
                request=request,
                plan=plan,
                resolved_revision=resolved_revision,
                attempts=attempts_tracker[0],
            )
        except SandboxClosedError as exc:
            if self._cancel_requested(run_id):
                return self._cancelled(
                    run_id,
                    session,
                    attempts_tracker[0],
                    request=request,
                    plan=plan,
                    resolved_revision=resolved_revision,
                )
            return self._fail(
                run_id,
                "timeout",
                f"sandbox closed: {exc}",
                session=session,
                request=request,
                plan=plan,
                resolved_revision=resolved_revision,
                attempts=attempts_tracker[0],
            )
        except SandboxUnavailableError as exc:
            if self._cancel_requested(run_id):
                return self._cancelled(
                    run_id,
                    session,
                    attempts_tracker[0],
                    request=request,
                    plan=plan,
                    resolved_revision=resolved_revision,
                )
            return self._fail(
                run_id,
                "service_interrupted",
                f"sandbox unavailable: {exc}",
                session=session,
                request=request,
                plan=plan,
                resolved_revision=resolved_revision,
                attempts=attempts_tracker[0],
            )
        finally:
            if session is not None:
                session.close()
                # 清理失败不许吞掉：宁可报出来（事件 + 环境清单 + 报告），不能装没看见。
                self._record_cleanup_failures(run_id, session)
            self._unregister_active(run_id)

    def _is_verified(self, test, plan: RunPlan, workspace: Path) -> bool:
        if test.exit_code != 0 or test.timed_out:
            return False
        if plan.expected_artifact:
            artifact_path = _resolve_artifact_path(workspace, plan.expected_artifact)
            return artifact_path is not None and artifact_path.is_file()
        return True

    def _self_heal(
        self,
        run_id: str,
        request: RunRequest,
        session: SandboxSession,
        workspace: Path,
        plan: RunPlan,
        first_failure,
        phase: str,
        attempts_tracker: list[int],
        deadline: float,
        target: TargetManifest | None = None,
    ) -> dict:
        """DIAGNOSING → REPAIRING → 重跑当前阶段 的循环。

        phase 表示上一步失败的阶段：install 或 test。
        install 阶段修好后再进入 test，test 通过才算成功。
        模型不拥有控制流：循环、重验、认输全在本方法。
        """
        session.set_network(False)
        aci = self.aci_factory(session, workspace, plan, request.timeout_seconds)
        history: list[Observation] = []
        category: str | None = None
        outcome = "budget_exceeded"
        failure = first_failure

        for round_no in range(1, request.max_repairs + 1):
            self._check_cancel(run_id)
            self.store.write_status(run_id, RunState.DIAGNOSING, {"attempt": round_no})
            category = self.llm.classify_failure(self._context(failure))
            self.store.append_event(
                run_id,
                RunState.DIAGNOSING,
                "diagnosis",
                f"failure classified as {category}",
                {"failure_class": category, "attempt": round_no, "phase": phase},
            )
            if category in UNREPAIRABLE:
                outcome = "unrepairable"
                break

            history.append(
                Observation(
                    ok=False,
                    action=f"{phase}_failed",
                    summary=(
                        f"round {round_no}/{request.max_repairs} ({phase}) "
                        f"classified as {category}; command={plan.command} "
                        f"exited with {failure.exit_code}"
                    ),
                    exit_code=failure.exit_code,
                    timed_out=failure.timed_out,
                    stdout_tail=failure.stdout_tail,
                    stderr_tail=failure.stderr_tail,
                    log_path=failure.stderr_path or failure.stdout_path,
                )
            )

            self.store.write_status(run_id, RunState.REPAIRING, {"attempt": round_no})
            prev_action = None
            consecutive_rejections = 0
            for _ in range(MAX_ACTIONS_PER_REPAIR):
                self._check_cancel(run_id)
                step_timeout = self._step_timeout(request, deadline)
                if hasattr(aci, "timeout_seconds"):
                    aci.timeout_seconds = step_timeout
                action = self.llm.propose_action(history)
                if prev_action is not None and action == prev_action:
                    self.store.append_event(
                        run_id,
                        RunState.REPAIRING,
                        "loop_stopped",
                        f"repeated identical action {action.action}, ending repair round",
                        {"action": action.action},
                    )
                    break
                prev_action = action
                observation = aci.execute(action)
                self._check_cancel(run_id)
                history.append(observation)
                event_data: dict[str, object] = {
                    "ok": observation.ok,
                    "exit_code": observation.exit_code,
                }
                for field, key in (
                    ("path", "path"),
                    ("requirement", "requirement"),
                    ("query", "query"),
                    ("name", "env_name"),
                ):
                    val = getattr(action, field, None)
                    if val is not None:
                        event_data[key] = val
                self.store.append_event(
                    run_id,
                    RunState.REPAIRING,
                    action.action,
                    observation.summary,
                    event_data,
                )
                if action.action == "finish_repair":
                    break
                if action.action == "run_smoke_test" and observation.exit_code == 0:
                    break
                if (
                    not observation.ok
                    and observation.exit_code is None
                    and not observation.timed_out
                ):
                    consecutive_rejections += 1
                    if consecutive_rejections >= 3:
                        self.store.append_event(
                            run_id,
                            RunState.REPAIRING,
                            "loop_stopped",
                            "too many consecutive rejected actions, ending repair round",
                            {"count": consecutive_rejections},
                        )
                        break
                else:
                    consecutive_rejections = 0
            self.store.append_event(
                run_id,
                RunState.REPAIRING,
                "repair_round",
                f"repair round {round_no} finished",
            )

            if phase == "install":
                install = self._run_install(run_id, request, session, workspace, deadline)
                if install is not None and install.exit_code != 0:
                    failure = install
                    continue
                phase = "test"

            attempts_tracker[0] += 1
            test = self._run_test(
                run_id,
                request,
                session,
                plan,
                deadline,
                attempt=attempts_tracker[0],
                target=target,
            )
            if self._is_verified(test, plan, workspace):
                return {
                    "verified": True,
                    "attempts": attempts_tracker[0],
                    "failure_category": category,
                    "outcome": "succeeded",
                }
            failure = test
        return {
            "verified": False,
            "attempts": attempts_tracker[0],
            "failure_category": category,
            "outcome": outcome,
        }

    def _run_install(self, run_id: str, request: RunRequest, session, workspace: Path, deadline: float):
        """跑依赖安装。没有 requirements.txt 时返回 None（无依赖清单，不算失败）。"""
        self.store.write_status(run_id, RunState.INSTALLING, {})
        req_path = workspace / "requirements.txt"
        if not req_path.exists():
            self.store.append_event(
                run_id,
                RunState.INSTALLING,
                "install",
                "no requirements.txt, install skipped",
            )
            return None
        # 文件内容来自任意公开仓库，与模型给的依赖串同属不可信输入：逐行校验，
        # 任一行不合法就整体拒绝——在 pip 拿到任何执行机会之前（拒绝时零副作用）。
        try:
            validate_requirements_file(
                req_path.read_text(encoding="utf-8", errors="replace")
            )
        except RequirementsNotAllowed as exc:
            self.store.append_event(
                run_id,
                RunState.INSTALLING,
                "install",
                f"requirements.txt rejected: {exc}",
                {
                    "reason": exc.reason,
                    "line_no": exc.line_no,
                    "source": "requirements_file",
                },
            )
            raise _RequirementsRejected(str(exc)) from exc
        session.set_network(True)
        try:
            install = session.execute(
                INSTALL_COMMAND, timeout_seconds=self._step_timeout(request, deadline)
            )
        finally:
            try:
                # 成败都关回断网：修复期基线是断网，execute 抛异常也不许把网留在开态。
                # install 成功后下一步 _run_test 本来就要求断网，行为不变。
                session.set_network(False)
            except SandboxClosedError:
                pass  # 会话已死，无需恢复；不能让恢复动作盖掉原始异常
        self._check_cancel(run_id)
        self.store.append_event(
            run_id,
            RunState.INSTALLING,
            "install",
            "dependency install finished",
            {"exit_code": install.exit_code, "stderr_tail": install.stderr_tail[-500:]},
        )
        return install

    def _run_test(
        self,
        run_id: str,
        request: RunRequest,
        session,
        plan: RunPlan,
        deadline: float,
        attempt: int | None = None,
        target: TargetManifest | None = None,
    ):
        self.store.write_status(run_id, RunState.TESTING, {"attempt": attempt} if attempt else {})
        workspace = self.store.run_dir(run_id) / "workspace"
        if target is not None and target.source_dir is not None:
            check_src = target.source_dir / "check.py"
            if check_src.is_file():
                shutil.copy2(check_src, workspace / "check.py")
        if attempt is not None and attempt > 1 and plan.expected_artifact:
            artifact_path = _resolve_artifact_path(workspace, plan.expected_artifact)
            if artifact_path is not None and artifact_path.is_file():
                artifact_path.unlink()
        session.set_network(False)
        test = session.execute(plan.command, timeout_seconds=self._step_timeout(request, deadline))
        self._check_cancel(run_id)
        self.store.append_event(
            run_id,
            RunState.TESTING,
            "test",
            f"smoke test finished (attempt {attempt})" if attempt else "smoke test finished",
            {"exit_code": test.exit_code, "stderr_tail": test.stderr_tail, "attempt": attempt},
        )
        return test

    @staticmethod
    def _context(result) -> FailureContext:
        return FailureContext(
            exit_code=result.exit_code,
            timed_out=result.timed_out,
            stdout_tail=result.stdout_tail,
            stderr_tail=result.stderr_tail,
        )

    @staticmethod
    def _step_timeout(request: RunRequest, deadline: float) -> int:
        remaining = int(deadline - time.monotonic())
        if remaining <= 1:
            raise TimeBudgetExceeded("total wall-clock budget exhausted")
        return max(1, min(request.timeout_seconds, remaining))

    def _record_cleanup_failures(self, run_id: str, session: SandboxSession) -> None:
        """沙箱清理失败必须可见：写 cleanup_failed 事件，并在环境清单/报告里记数。

        不改变 run 的 outcome（结果已定），但硬指标"容器清理失败为 0"
        依赖这里的记数才能被审计。
        """
        errors = list(getattr(session, "cleanup_errors", None) or [])
        if not errors:
            return
        status = self.store.read_status(run_id) or {}
        try:
            state = RunState(status.get("state", RunState.FAILED.value))
        except ValueError:
            state = RunState.FAILED
        self.store.append_event(
            run_id,
            state,
            "cleanup_failed",
            "; ".join(errors)[:2000],
            {"count": len(errors), "failures": [error[:300] for error in errors]},
        )
        environment: dict = {}
        existing = self.store.read_text(run_id, "environment.json")
        if existing:
            try:
                environment = json.loads(existing)
            except json.JSONDecodeError:
                environment = {}
        environment["cleanup_failures"] = len(errors)
        self.store.write_text(
            run_id,
            "environment.json",
            json.dumps(environment, ensure_ascii=False, indent=2),
        )
        report = self.store.read_text(run_id, "report.md") or "# Report\n\n"
        self.store.write_text(run_id, "report.md", report + f"cleanup_failures={len(errors)}\n")

    def _build_environment_manifest(
        self,
        request: RunRequest | None,
        plan: RunPlan | None,
        resolved_revision: str | None,
        session: SandboxSession | None,
    ) -> dict[str, object]:
        py_ver = (
            plan.python_version
            if plan is not None
            else (request.python_version if request and request.python_version else "3.10")
        )
        manifest: dict[str, object] = {
            "resolved_revision": resolved_revision or (plan.resolved_revision if plan else None),
            "python_version": py_ver,
            "base_image": f"paperharness/base-py{py_ver}",
            "command": (
                plan.command
                if plan is not None
                else (request.command if request is not None else None)
            ),
        }
        if session is not None:
            get_env = getattr(session, "get_environment", None)
            if callable(get_env):
                manifest["environment"] = get_env()
            elif hasattr(session, "environment"):
                manifest["environment"] = dict(session.environment)
            collect_pkgs = getattr(session, "collect_packages", None)
            if callable(collect_pkgs):
                manifest["packages"] = collect_pkgs()
        return manifest

    def _fail(
        self,
        run_id: str,
        category: str,
        message: str,
        outcome: str = "failed",
        session: SandboxSession | None = None,
        request: RunRequest | None = None,
        plan: RunPlan | None = None,
        resolved_revision: str | None = None,
        attempts: int = 1,
    ) -> RunResult:
        result = RunResult(
            run_id=run_id,
            outcome=outcome,
            verified=False,
            attempts=attempts,
            failure_category=category,
            resolved_revision=resolved_revision,
            patch_url=f"/api/runs/{run_id}/patch",
            environment_url=f"/api/runs/{run_id}/environment",
            report_url=f"/api/runs/{run_id}/report",
        )
        patch = ""
        if session is not None:
            try:
                patch = session.export_diff()
            except Exception:  # noqa: BLE001 - 会话可能已死，尽力而为
                patch = ""
        self.store.write_text(run_id, "patch.diff", patch)
        self.store.write_text(
            run_id,
            "environment.json",
            json.dumps(
                self._build_environment_manifest(request, plan, resolved_revision, session),
                ensure_ascii=False,
                indent=2,
            ),
        )
        self.store.write_text(
            run_id,
            "report.md",
            "# Report\n\n"
            f"outcome={outcome}\nverified=False\nfailure_category={category}\n\n"
            f"{message[:2000]}\n",
        )
        self.store.write_status(run_id, RunState.FAILED, {"failure_category": category})
        self.store.append_event(
            run_id,
            RunState.FAILED,
            "final",
            f"run {outcome}: {category}",
            {"failure_category": category, "message": message[:2000]},
        )
        self.store.write_result(run_id, result)
        return result
