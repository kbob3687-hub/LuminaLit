import hashlib
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
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath

from paperharness.aci.paper_aci import is_safe_environment_value
from paperharness.aci.policy import ActionPolicy
from paperharness.aci.requirement_guard import RequirementNotAllowed, validate
from paperharness.agent.classifier import UNREPAIRABLE, FailureContext, classify_by_rules
from paperharness.contracts.bundle import BundleItem, BundleResult, RepairManifest
from paperharness.contracts.run import (
    ExecResult,
    Observation,
    ReplayCheck,
    ReplayResult,
    ReplayStatus,
    RunPlan,
    RunRequest,
    RunResult,
    RunState,
    validate_repo_url,
)
from paperharness.contracts.verification import (
    VerificationCheck,
    VerificationResult,
    VerificationSpec,
)
from paperharness.harness.bundle import (
    ManifestBuildError,
    build_repair_manifest,
    manifest_digest,
    pack_bundle,
)
from paperharness.harness.preflight import PreflightRecord
from paperharness.harness.replay import (
    ReplayMaterializationError,
    apply_repair_manifest,
    dependency_digest,
)
from paperharness.harness.requirements_guard import (
    RequirementsNotAllowed,
    validate_requirements_file,
)
from paperharness.harness.store import RunStore
from paperharness.harness.verification import (
    ArtifactRejected,
    prepare_artifact,
    snapshot_artifact,
    verification_spec,
    verify_artifact,
)
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


class _ReplayBlocked(RuntimeError):
    """复跑条件不足：用 blocked 记录原因，不把基础设施问题算成复跑失败。"""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


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


def _target_destination(workspace: Path, name: str) -> Path:
    path = workspace / name
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError(f"unsafe target injection destination: {name}")
    return path


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
            ["git", "-C", str(workspace), "config", "core.autocrlf", "false"],
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
                    "-c",
                    "core.autocrlf=false",
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
                    "-c",
                    "core.autocrlf=false",
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
                "-c",
                "core.autocrlf=false",
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


def _list_workspace_files(workspace: Path, limit: int = 60) -> list[str]:
    if not workspace.is_dir():
        return []
    files: list[str] = []
    root = workspace.resolve(strict=False)
    for current, dirs, filenames in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in {".git", "__pycache__"})
        rel_dir = Path(current).relative_to(root)
        depth = len(rel_dir.parts)
        if depth >= 3:
            dirs[:] = []
        for fname in sorted(filenames):
            rel = (rel_dir / fname).as_posix() if depth else fname
            files.append(rel)
            if len(files) >= limit:
                return files
    return files


class HarnessRunner:
    def __init__(
        self,
        store: RunStore,
        llm,
        sandbox_factory: Callable[[RunRequest, Path], SandboxSession],
        cloner: Callable[[RunRequest, Path], str] = git_clone,
        aci_factory: Callable[[SandboxSession, Path, RunPlan, int, ActionPolicy], object] | None = None,
        targets: list[TargetManifest] | None = None,
        verifier: Callable[..., VerificationResult] = verify_artifact,
    ):
        self.store = store
        self.llm = llm
        self.sandbox_factory = sandbox_factory
        self.cloner = cloner
        self.aci_factory = aci_factory
        self.targets = list(targets or [])
        self.verifier = verifier
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
        replay: ReplayResult | None = None,
    ) -> RunResult:
        """CANCELLED 终态：不 export 不到的 patch 就留空，不留僵尸状态。"""
        verification = self._stored_verification(run_id)
        result = RunResult(
            run_id=run_id,
            outcome="cancelled",
            verified=verification.status == "passed",
            verification=verification,
            attempts=attempts,
            resolved_revision=resolved_revision,
            patch_url=f"/api/runs/{run_id}/patch",
            environment_url=f"/api/runs/{run_id}/environment",
            report_url=f"/api/runs/{run_id}/report",
            replay=replay,
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
            run_id, "report.md", "# Report\n\noutcome=cancelled\n"
            f"verified={result.verified}\n\n```json\n{verification.model_dump_json(indent=2)}\n```\n"
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
            shutil.copy2(check_src, _target_destination(workspace, "check.py"))
            injected.append("check.py")
        req_src = target.source_dir / "requirements.txt"
        if req_src.is_file():
            extra = req_src.read_text(encoding="utf-8")
            workspace_req = _target_destination(workspace, "requirements.txt")
            existing = (
                workspace_req.read_text(encoding="utf-8") if workspace_req.exists() else ""
            )
            separator = "\n" if existing and not existing.endswith("\n") else ""
            workspace_req.write_text(existing + separator + extra, encoding="utf-8")
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
        confirmation_json = self.store.read_text(run_id, "preflight.json")
        target = None if confirmation_json else self._match_target(request)
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
        confirmed_plan: RunPlan | None = None
        verification_rule: VerificationSpec | None = None
        last_test: ExecResult | None = None
        repair_manifest: RepairManifest | None = None
        replay_result = ReplayResult(status=ReplayStatus.NOT_REQUESTED)
        try:
            self._check_cancel(run_id)
            if confirmation_json:
                try:
                    confirmation = PreflightRecord.model_validate_json(confirmation_json)
                    if (
                        not confirmation.check_digest(confirmation.view.confirmation_digest)
                        or confirmation.request.model_dump() != request.model_dump()
                    ):
                        raise ValueError("confirmed inputs changed")
                    target = confirmation.target.materialize(run_dir / "confirmed-target")
                    confirmed_plan = confirmation.view.plan
                    verification_rule = confirmation.view.verification
                    if bool(target.verification_rule) != bool(verification_rule):
                        raise ValueError("confirmed verification rule missing")
                    if verification_rule and verification_rule != verification_spec():
                        raise ValueError("confirmed verification rule changed; recheck required")
                except (OSError, ValueError) as exc:
                    return self._fail(
                        run_id, "confirmation_mismatch", f"confirmed inputs unavailable: {exc}",
                        request=request,
                    )
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
            if confirmed_plan and resolved_revision != confirmed_plan.resolved_revision:
                return self._fail(
                    run_id, "confirmed_revision_mismatch",
                    "checked-out revision differs from the confirmed plan",
                    request=request, plan=confirmed_plan, resolved_revision=resolved_revision,
                )
            self.store.append_event(
                run_id,
                RunState.PREPARING,
                "preparing",
                f"cloned at {resolved_revision}",
            )
            if target is not None:
                self._inject_target_files(run_id, workspace, target)

            self.store.write_status(run_id, RunState.PLANNING, {})
            if confirmed_plan is not None:
                plan = confirmed_plan
            elif target is not None and request.command is None:
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

            if (
                not confirmed_plan and target and target.verification_rule
                and plan.source == "target_manifest" and plan.command == target.command
                and plan.expected_artifact == target.expected_artifact
                and plan.resolved_revision == target.revision
                and plan.python_version == target.python_version
            ):
                verification_rule = verification_spec()
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
            self.store.append_event(
                run_id, RunState.PLANNING, "sandbox_start", "sandbox creation started"
            )
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
            self.store.append_event(
                run_id, RunState.PLANNING, "sandbox_ready", "sandbox session created"
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
                        verification_rule=verification_rule,
                    )
                    last_test = healed["test"]
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
                    run_id, request, session, plan, deadline, target=target,
                    verification_rule=verification_rule,
                )
                last_test = test
                verified = self._is_verified(
                    test, plan, workspace, independent=verification_rule is not None
                )
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
                        verification_rule=verification_rule,
                    )
                    last_test = healed["test"]
                    verified = healed["verified"]
                    attempts = healed["attempts"]
                    failure_category = healed["failure_category"]
                    outcome = "succeeded" if verified else healed["outcome"]

            self.store.write_text(run_id, "patch.diff", session.export_diff())
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
            verification = self._execution_verification(last_test, plan, workspace, verified)
            if verified and verification_rule:
                verification = self._verify_target(
                    run_id, request, session, verification_rule, deadline
                )
                verified = verification.status == "passed"
                outcome = "succeeded" if verified else "failed"
                failure_category = verification.failure_category
            self.store.write_text(run_id, "verification.json", verification.model_dump_json(indent=2))
            self._check_cancel(run_id)
            if verification_rule and time.monotonic() >= deadline:
                raise TimeBudgetExceeded("total wall-clock budget exhausted during verification")
            if verification_rule:
                repair_manifest = self._freeze_manifest(
                    run_id, workspace, plan, plan.resolved_revision, request
                )
                if not verified:
                    replay_result = ReplayResult(
                        status=ReplayStatus.BLOCKED,
                        reason="not_started_current_verification_failed",
                    )
                    self.store.write_text(
                        run_id, "replay_result.json", replay_result.model_dump_json(indent=2)
                    )
                elif repair_manifest is None:
                    replay_result = ReplayResult(
                        status=ReplayStatus.BLOCKED,
                        reason="repair_manifest_unavailable",
                    )
                    self.store.write_status(run_id, RunState.REPLAYING, {})
                    self.store.write_text(
                        run_id, "replay_result.json", replay_result.model_dump_json(indent=2)
                    )
                    self.store.append_event(
                        run_id, RunState.REPLAYING, "replay_started",
                        "clean-environment replay requested",
                    )
                    self.store.append_event(
                        run_id, RunState.REPLAYING, "replay_finished",
                        "repair manifest unavailable", replay_result.model_dump(mode="json"),
                    )
                else:
                    # 先建结果对象再跑复跑：取消/预算在复跑中途抛出时，
                    # 外层处理器仍能拿到已被标记为 blocked 的同一对象。
                    replay_result = ReplayResult(
                        status=ReplayStatus.RUNNING,
                        session_id=f"replay-{run_id}",
                        base_revision=repair_manifest.base_revision,
                        repair_manifest_digest=manifest_digest(repair_manifest),
                        dependency_digest=dependency_digest(repair_manifest),
                        started_at=datetime.now(UTC),
                        limitations=["当前任务未提供可核验的镜像摘要与数据文件摘要。"],
                    )
                    self._run_replay(
                        run_id,
                        request,
                        plan,
                        verification_rule,
                        repair_manifest,
                        workspace,
                        verification,
                        deadline,
                        cancel,
                        replay_result,
                    )
                if verified and replay_result.status is not ReplayStatus.PASSED:
                    outcome = "failed"
                    failure_category = (
                        "replay_failed" if replay_result.status is ReplayStatus.FAILED
                        else "replay_blocked"
                    )
            state = (
                RunState.SUCCEEDED
                if verified and replay_result.status in (
                    ReplayStatus.PASSED, ReplayStatus.NOT_REQUESTED
                )
                else RunState.FAILED
            )
            self.store.write_text(
                run_id,
                "report.md",
                "# Report\n\n"
                f"outcome={outcome}\nverified={verified}\nattempts={attempts}\n"
                f"failure_category={failure_category}\n\n"
                f"verification={verification.level}/{verification.status}\n"
                f"{verification.summary}\n\n"
                f"replay={replay_result.status.value}\n"
                f"replay_reason={replay_result.reason}\n\n"
                "```json\n" + verification.model_dump_json(indent=2) + "\n```\n",
            )

            bundle = self._finalize_bundle(
                run_id, workspace, plan, plan.resolved_revision, state,
                request=request, manifest=repair_manifest,
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
                verification=verification,
                bundle=bundle,
                bundle_url=f"/api/runs/{run_id}/bundle",
                replay=replay_result,
            )
            self.store.write_status(run_id, state, {"verified": verified})
            self.store.append_event(
                run_id, state, "final", f"run {result.outcome}",
                {"verified": verified, "failure_category": failure_category, "outcome": outcome}
            )
            self.store.write_result(run_id, result)
            return result
        except ArtifactRejected as exc:
            verification = VerificationResult(
                level="artifact", status="failed", summary=str(exc),
                failure_category="artifact_invalid", checked_at=datetime.now(UTC),
                limitations=["产物未通过安全读取检查，未执行独立指标验收。", "未进行干净环境复跑。"],
            )
            self.store.write_text(run_id, "verification.json", verification.model_dump_json(indent=2))
            return self._fail(
                run_id, "artifact_invalid", str(exc), session=session, request=request,
                plan=plan, resolved_revision=resolved_revision, attempts=attempts_tracker[0],
                replay=self._replay_on_termination(
                    run_id, replay_result,
                    required=verification_rule is not None, state=RunState.FAILED,
                ),
            )
        except _RunCancelled:
            return self._cancelled(
                run_id,
                session,
                attempts_tracker[0],
                request=request,
                plan=plan,
                resolved_revision=resolved_revision,
                replay=self._replay_on_termination(
                    run_id, replay_result,
                    required=verification_rule is not None, state=RunState.CANCELLED,
                ),
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
                replay=self._replay_on_termination(
                    run_id, replay_result,
                    required=verification_rule is not None, state=RunState.FAILED,
                ),
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
                replay=self._replay_on_termination(
                    run_id, replay_result,
                    required=verification_rule is not None, state=RunState.FAILED,
                ),
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
                    replay=self._replay_on_termination(
                        run_id, replay_result,
                        required=verification_rule is not None, state=RunState.CANCELLED,
                    ),
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
                replay=self._replay_on_termination(
                    run_id, replay_result,
                    required=verification_rule is not None, state=RunState.FAILED,
                ),
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
                    replay=self._replay_on_termination(
                        run_id, replay_result,
                        required=verification_rule is not None, state=RunState.CANCELLED,
                    ),
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
                replay=self._replay_on_termination(
                    run_id, replay_result,
                    required=verification_rule is not None, state=RunState.FAILED,
                ),
            )
        finally:
            if session is not None:
                session.close()
                # 清理失败不许吞掉：宁可报出来（事件 + 环境清单 + 报告），不能装没看见。
                self._record_cleanup_failures(run_id, session)
            self._unregister_active(run_id)

    def _is_verified(
        self, test, plan: RunPlan, workspace: Path, *, independent: bool = False
    ) -> bool:
        if test.exit_code != 0 or test.timed_out:
            return False
        if independent:
            return True
        if plan.expected_artifact:
            artifact_path = _resolve_artifact_path(workspace, plan.expected_artifact)
            return artifact_path is not None and artifact_path.is_file()
        return True

    @staticmethod
    def _execution_verification(test, plan, workspace, verified) -> VerificationResult:
        if test is None:
            return VerificationResult(summary="程序尚未完成执行，未进行指标验收")
        executed = test.exit_code == 0 and not test.timed_out
        checks = [VerificationCheck(
            name="命令执行", passed=executed,
            detail=f"退出码 {test.exit_code}；超时：{'是' if test.timed_out else '否'}",
        )]
        if plan.expected_artifact:
            path = _resolve_artifact_path(workspace, plan.expected_artifact)
            checks.append(VerificationCheck(
                name="预期文件存在", passed=path is not None and path.is_file(),
                detail="仅检查文件存在，不代表结构或指标正确。",
            ))
        return VerificationResult(
            level="execution", status="passed" if verified else "failed",
            summary="程序运行通过，尚未独立验证科研指标" if verified else "本次执行检查未通过",
            checks=checks, checked_at=datetime.now(UTC),
            limitations=["未进行独立产物结构和指标验收。", "未进行干净环境复跑，不等于完整复现论文。"],
        )

    def _verify_target(self, run_id, request, session, spec, deadline) -> VerificationResult:
        self._check_cancel(run_id)
        self.store.write_status(run_id, RunState.VERIFYING, {})
        self.store.append_event(
            run_id, RunState.VERIFYING, "verification_started", "independent verification started",
            {"rule_id": spec.rule_id, "validator_digest": spec.validator_digest},
        )
        result = self._verify_workspace(
            run_id,
            request,
            session,
            spec,
            deadline,
            workspace=self.store.run_dir(run_id) / "workspace",
            input_name="verification-input.json",
            evidence_url=f"/api/runs/{run_id}/verification/input",
            result_name="verification.json",
        )
        self.store.write_text(run_id, "verification.json", result.model_dump_json(indent=2))
        self.store.append_event(
            run_id, RunState.VERIFYING, "verification", result.summary,
            result.model_dump(mode="json"),
        )
        return result

    def _verify_workspace(
        self,
        run_id: str,
        request: RunRequest,
        session,
        spec: VerificationSpec,
        deadline: float,
        *,
        workspace: Path,
        input_name: str,
        evidence_url: str | None,
        result_name: str | None = None,
    ) -> VerificationResult:
        result = VerificationResult(
            level="target", status="blocked", summary="独立指标验收尚未完成",
            rule_id=spec.rule_id, validator_digest=spec.validator_digest,
            code_under_test=spec.code_under_test, dataset=spec.dataset, source=spec.source,
            checked_at=datetime.now(UTC),
            limitations=["未进行干净环境复跑，不等于完整复现论文。"],
        )
        if result_name is not None:
            self.store.write_text(run_id, result_name, result.model_dump_json(indent=2))
        # 先停止生产端的所有进程，再读取快照，避免后台进程在验收期间改写产物。
        session.close()
        self._check_cancel(run_id)
        if getattr(session, "cleanup_errors", None):
            result.summary = "无法确认运行容器已停止，未读取产物或开始验收"
            result.failure_category = "verification_error"
        else:
            try:
                raw = snapshot_artifact(workspace, spec)
            except ArtifactRejected as exc:
                result.status = "failed"
                result.summary = str(exc)
                result.failure_category = "artifact_invalid"
                result.checks = [VerificationCheck(name="本次产物安全读取", passed=False, detail=str(exc))]
            else:
                (self.store.run_dir(run_id) / input_name).write_bytes(raw)
                result = self.verifier(
                    raw, spec, timeout_seconds=min(20, self._step_timeout(request, deadline)),
                    cancelled=lambda: self._cancel_requested(run_id),
                )
                result.artifact_sha256 = hashlib.sha256(raw).hexdigest()
                result.evidence_url = evidence_url
        result.checks.insert(0, VerificationCheck(
            name="本次命令执行", passed=True, detail="退出码 0，未超时；旧产物在本次执行前已清除。",
        ))
        return result

    def _run_replay(
        self,
        run_id: str,
        request: RunRequest,
        plan: RunPlan,
        spec: VerificationSpec,
        manifest: RepairManifest,
        source_workspace: Path,
        current_verification: VerificationResult,
        deadline: float,
        cancel: threading.Event,
        result: ReplayResult,
    ) -> ReplayResult:
        run_dir = self.store.run_dir(run_id)
        assert result.session_id is not None
        replay_root = run_dir / "replay" / result.session_id
        replay_workspace = replay_root / "workspace"
        self.store.write_status(run_id, RunState.REPLAYING, {})
        self.store.write_text(run_id, "replay_result.json", result.model_dump_json(indent=2))
        self.store.append_event(
            run_id, RunState.REPLAYING, "replay_started", "clean-environment replay started",
            {"manifest_digest": result.repair_manifest_digest},
        )
        replay_session = None
        try:
            try:
                resolved_revision = self.cloner(request, replay_root)
            except (_RunCancelled, TimeBudgetExceeded):
                raise
            except Exception as exc:
                raise _ReplayBlocked("replay_clone_failed", f"复跑检出失败: {exc}") from exc
            if resolved_revision != manifest.base_revision:
                raise ReplayMaterializationError("复跑检出 revision 与冻结基准不一致")
            result.checks.append(ReplayCheck(
                name="冻结基准检出", status="passed", detail=resolved_revision,
            ))
            apply_repair_manifest(source_workspace, replay_workspace, manifest)
            result.checks.append(ReplayCheck(
                name="修复材料还原", status="passed", detail="只应用冻结清单中的声明变更。",
            ))
            sandbox_request = request.model_copy(update={"python_version": plan.python_version})
            replay_session = self.sandbox_factory(sandbox_request, replay_root)
            self._register_active(run_id, cancel, replay_session)
            self._check_cancel(run_id)
            for name, value in manifest.environment.items():
                if not is_safe_environment_value(name, value):
                    raise ReplayMaterializationError(
                        f"复跑环境变量值缺失或不受支持: {name}"
                    )
                replay_session.set_environment(name, value)
            result.checks.append(ReplayCheck(
                name="环境变量还原", status="passed", detail="只恢复白名单内已冻结的非敏感值。",
            ))
            install = self._run_install(
                run_id, request, replay_session, replay_workspace, deadline,
                state=RunState.REPLAYING,
            )
            if install is None or install.exit_code == 0:
                declared_install = self._run_declared_install(
                    run_id, request, replay_session, manifest.dependencies, deadline
                )
                if declared_install is not None:
                    install = declared_install
            if install is not None and install.exit_code != 0:
                result.status = ReplayStatus.FAILED
                result.reason = "replay_install_failed"
                result.checks.append(ReplayCheck(
                    name="依赖安装", status="failed", detail=install.stderr_tail[-2000:],
                ))
            else:
                result.checks.append(ReplayCheck(
                    name="依赖安装", status="passed", detail="冻结依赖安装完成。",
                ))
                test = self._run_test(
                    run_id, request, replay_session, plan, deadline,
                    verification_rule=spec, workspace=replay_workspace,
                    state=RunState.REPLAYING,
                )
                if test.exit_code != 0 or test.timed_out:
                    result.status = ReplayStatus.FAILED
                    result.reason = "replay_command_failed"
                    result.checks.append(ReplayCheck(
                        name="同一命令执行", status="failed",
                        detail=f"退出码 {test.exit_code}；超时：{test.timed_out}",
                    ))
                else:
                    result.checks.append(ReplayCheck(
                        name="同一命令执行", status="passed", detail="退出码 0，未超时。",
                    ))
                    try:
                        verification = self._verify_workspace(
                            run_id,
                            request,
                            replay_session,
                            spec,
                            deadline,
                            workspace=replay_workspace,
                            input_name="replay-verification-input.json",
                            evidence_url=None,
                        )
                    except (_RunCancelled, TimeBudgetExceeded):
                        raise
                    except Exception as exc:
                        raise _ReplayBlocked(
                            "replay_verifier_error", f"复跑验收器故障: {exc}"
                        ) from exc
                    result.verification = verification
                    result.image_digest = verification.verifier_image_id
                    passed = verification.status == "passed"
                    result.status = ReplayStatus.PASSED if passed else (
                        ReplayStatus.FAILED if verification.status == "failed"
                        else ReplayStatus.BLOCKED
                    )
                    result.reason = None if passed else verification.failure_category
                    result.checks.append(ReplayCheck(
                        name="同一可信验收", status=verification.status,
                        detail=verification.summary,
                    ))
                    if (
                        current_verification.artifact_sha256 is not None
                        and verification.artifact_sha256 is not None
                        and verification.artifact_sha256 != current_verification.artifact_sha256
                    ):
                        result.differences.append(
                            "当前运行与干净复跑的产物 SHA-256 不同；按确认口径仅记录差异。"
                        )
        except ReplayMaterializationError as exc:
            self._block_replay(result, "replay_material_incomplete", str(exc))
        except SandboxUnavailableError as exc:
            self._block_replay(result, "replay_sandbox_unavailable", str(exc))
        except _ReplayBlocked as exc:
            self._block_replay(result, exc.reason, str(exc))
        except _RunCancelled:
            self._block_replay(result, "cancelled", "复跑阶段收到取消请求，未完成干净复跑。")
            raise
        except TimeBudgetExceeded:
            self._block_replay(result, "budget_exceeded", "复跑阶段总预算耗尽，未完成干净复跑。")
            raise
        except Exception as exc:  # noqa: BLE001 - 未知故障按条件不足收敛，不伪装成复跑失败
            self._block_replay(result, "replay_error", f"复跑阶段发生未预期错误: {exc}")
        finally:
            if replay_session is not None:
                replay_session.close()
                self._record_cleanup_failures(run_id, replay_session)
            # 无论正常结束还是取消/预算异常上抛，都先把证据落盘再交给外层终态。
            result.finished_at = datetime.now(UTC)
            self.store.write_text(run_id, "replay_result.json", result.model_dump_json(indent=2))
            self.store.append_event(
                run_id, RunState.REPLAYING, "replay_finished",
                result.reason or result.status.value,
                result.model_dump(mode="json"),
            )
        return result

    @staticmethod
    def _block_replay(result: ReplayResult, reason: str, message: str) -> None:
        result.status = ReplayStatus.BLOCKED
        result.reason = reason
        result.checks.append(
            ReplayCheck(name="复跑执行", status="blocked", detail=message[:2000])
        )
        result.limitations.append(message[:2000])

    def _replay_on_termination(
        self,
        run_id: str,
        replay_result: ReplayResult,
        *,
        required: bool,
        state: RunState,
    ) -> ReplayResult | None:
        """正式任务在复跑开始前被终止时补记 blocked，不能退回 not_requested。"""
        if replay_result.status is not ReplayStatus.NOT_REQUESTED:
            return replay_result
        if not required:
            return None
        blocked = ReplayResult(
            status=ReplayStatus.BLOCKED,
            reason="not_started_before_termination",
        )
        self.store.write_text(run_id, "replay_result.json", blocked.model_dump_json(indent=2))
        self.store.append_event(
            run_id, state, "replay_finished",
            "replay not started before termination", blocked.model_dump(mode="json"),
        )
        return blocked

    def _stored_verification(self, run_id: str) -> VerificationResult:
        data = self.store.read_text(run_id, "verification.json")
        return VerificationResult.model_validate_json(data) if data else VerificationResult()

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
        verification_rule: VerificationSpec | None = None,
    ) -> dict:
        """DIAGNOSING → REPAIRING → 重跑当前阶段 的循环。

        phase 表示上一步失败的阶段：install 或 test。
        install 阶段修好后再进入 test，test 通过才算成功。
        模型不拥有控制流：循环、重验、认输全在本方法。
        """
        session.set_network(False)
        # target is the confirmed snapshot, not a fresh lookup in self.targets.
        policy = (
            ActionPolicy.for_manifest(target, plan) if target else ActionPolicy.for_plan(plan)
        )
        aci = self.aci_factory(session, workspace, plan, request.timeout_seconds, policy)
        history: list[Observation] = []
        category: str | None = None
        outcome = "budget_exceeded"
        failure = first_failure
        last_test = first_failure if phase == "test" else None
        workspace_files = _list_workspace_files(workspace)

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

            obs_summary = (
                f"round {round_no}/{request.max_repairs} ({phase}) "
                f"classified as {category}; command={plan.command} "
                f"exited with {failure.exit_code}"
            )
            if round_no == 1:
                obs_summary += (
                    f"; expected_artifact={plan.expected_artifact!r}"
                    f"; workspace_files={workspace_files}"
                )

            history.append(
                Observation(
                    ok=False,
                    action=f"{phase}_failed",
                    summary=obs_summary,
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
                    "timed_out": observation.timed_out,
                    "stdout_tail": observation.stdout_tail,
                    "stderr_tail": observation.stderr_tail,
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
                if (
                    action.action == "set_environment"
                    and observation.ok
                    and is_safe_environment_value(action.name, action.value)
                ):
                    event_data["env_value"] = action.value
                self.store.append_event(
                    run_id,
                    RunState.REPAIRING,
                    action.action,
                    observation.summary,
                    event_data,
                    log_path=observation.log_path,
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
                verification_rule=verification_rule,
            )
            last_test = test
            if self._is_verified(test, plan, workspace, independent=verification_rule is not None):
                return {
                    "test": last_test,
                    "verified": True,
                    "attempts": attempts_tracker[0],
                    "failure_category": category,
                    "outcome": "succeeded",
                }
            failure = test
        return {
            "test": last_test,
            "verified": False,
            "attempts": attempts_tracker[0],
            "failure_category": category,
            "outcome": outcome,
        }

    def _run_install(
        self,
        run_id: str,
        request: RunRequest,
        session,
        workspace: Path,
        deadline: float,
        state: RunState = RunState.INSTALLING,
    ):
        """跑依赖安装。没有 requirements.txt 时返回 None（无依赖清单，不算失败）。"""
        self.store.write_status(run_id, state, {})
        req_path = workspace / "requirements.txt"
        if not req_path.exists():
            self.store.append_event(
                run_id,
                state,
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
                state,
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
            state,
            "install",
            "dependency install finished",
            {"exit_code": install.exit_code, "stderr_tail": install.stderr_tail[-500:]},
        )
        return install

    def _run_declared_install(
        self,
        run_id: str,
        request: RunRequest,
        session,
        dependencies: list[str],
        deadline: float,
    ):
        if not dependencies:
            return None
        try:
            validated = [validate(dependency) for dependency in dependencies]
        except RequirementNotAllowed as exc:
            raise ReplayMaterializationError(
                f"冻结依赖字符串不合法: {exc.raw[:200]}"
            ) from exc
        session.set_network(True)
        install = None
        execute_error: Exception | None = None
        try:
            install = session.execute(
                ["python", "-m", "pip", "install", *validated],
                timeout_seconds=self._step_timeout(request, deadline),
            )
        except Exception as exc:  # noqa: BLE001 - 先保留原始异常，再尝试恢复网络
            execute_error = exc
        finally:
            try:
                session.set_network(False)
            except SandboxClosedError:
                pass
            except Exception as exc:
                self.store.append_event(
                    run_id, RunState.REPLAYING, "network_restore_failed",
                    str(exc)[:2000], {"phase": "declared_install"},
                )
                if execute_error is None and install is None:
                    raise
        if execute_error is not None:
            raise execute_error
        assert install is not None
        self._check_cancel(run_id)
        self.store.append_event(
            run_id,
            RunState.REPLAYING,
            "install",
            "declared repair dependencies installed",
            {"exit_code": install.exit_code, "dependencies": validated,
             "stderr_tail": install.stderr_tail[-500:]},
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
        verification_rule: VerificationSpec | None = None,
        workspace: Path | None = None,
        state: RunState = RunState.TESTING,
    ):
        self.store.write_status(
            run_id, state, {"attempt": attempt} if attempt else {}
        )
        workspace = workspace or self.store.run_dir(run_id) / "workspace"
        if target is not None and target.source_dir is not None:
            check_src = target.source_dir / "check.py"
            if check_src.is_file():
                shutil.copy2(check_src, _target_destination(workspace, "check.py"))
        if verification_rule:
            prepare_artifact(workspace, verification_rule)
        elif attempt is not None and attempt > 1 and plan.expected_artifact:
            artifact_path = _resolve_artifact_path(workspace, plan.expected_artifact)
            if artifact_path is not None and artifact_path.is_file():
                artifact_path.unlink()
        session.set_network(False)
        test = session.execute(plan.command, timeout_seconds=self._step_timeout(request, deadline))
        self._check_cancel(run_id)
        self.store.append_event(
            run_id,
            state,
            "test",
            f"smoke test finished (attempt {attempt})" if attempt else "smoke test finished",
            {"exit_code": test.exit_code, "timed_out": test.timed_out,
             "stderr_tail": test.stderr_tail, "attempt": attempt},
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
                environment = get_env()
            elif hasattr(session, "environment"):
                environment = dict(session.environment)
            else:
                environment = None
            if isinstance(environment, dict):
                manifest["environment"] = {
                    name: value if is_safe_environment_value(name, value) else ""
                    for name, value in environment.items()
                }
            collect_pkgs = getattr(session, "collect_packages", None)
            if callable(collect_pkgs):
                manifest["packages"] = collect_pkgs()
        return manifest

    # ------------------------------------------------------------ 修复材料

    def _collect_repair_actions(self, run_id: str) -> tuple[list[str], dict[str, str]]:
        """从修复事件流收集依赖与白名单环境变量；非敏感值随清单冻结。"""
        dependencies: list[str] = []
        environment: dict[str, str] = {}
        for event in self.store.read_events(run_id):
            if event.state is not RunState.REPAIRING or not event.data.get("ok"):
                continue
            if event.kind == "install_requirement":
                requirement = event.data.get("requirement")
                if isinstance(requirement, str) and requirement:
                    dependencies.append(requirement)
            elif event.kind == "set_environment":
                name = event.data.get("env_name")
                if isinstance(name, str) and name:
                    value = event.data.get("env_value")
                    environment[name] = (
                        value if is_safe_environment_value(name, value) else ""
                    )
        return dependencies, environment

    def _freeze_manifest(
        self,
        run_id: str,
        workspace: Path,
        plan: RunPlan,
        base_revision: str,
        request: RunRequest,
    ) -> RepairManifest | None:
        dependencies, environment = self._collect_repair_actions(run_id)
        try:
            manifest = build_repair_manifest(
                run_id=run_id,
                workspace=workspace,
                base_revision=base_revision,
                repository_url=request.repo_url,
                command=plan.command,
                python_version=plan.python_version,
                dependencies=dependencies,
                environment=environment,
                expected_artifacts=[plan.expected_artifact] if plan.expected_artifact else [],
            )
        except ManifestBuildError:
            return None
        self.store.write_text(run_id, "repair_manifest.json", manifest.model_dump_json(indent=2))
        return manifest

    def _finalize_bundle(
        self,
        run_id: str,
        workspace: Path,
        plan: RunPlan,
        base_revision: str,
        state: RunState,
        *,
        request: RunRequest | None = None,
        manifest: RepairManifest | None = None,
    ) -> BundleResult:
        """复用冻结清单打包；完整性不是 complete 也如实落记录（设计 7.4）。"""
        if manifest is None and request is not None:
            manifest = self._freeze_manifest(
                run_id, workspace, plan, base_revision, request
            )
        if manifest is None:
            result = BundleResult(
                integrity="blocked",
                items=[BundleItem(
                    path="repair_manifest.json", kind="metadata", status="blocked",
                    reason="工作区不是可审计的 git 仓库，无法冻结复跑材料。",
                )],
            )
            self.store.write_text(run_id, "bundle.json", result.model_dump_json(indent=2))
            return result
        metadata = {
            name: self.store.read_text(run_id, name)
            for name in ("run_plan.json", "environment.json", "patch.diff", "verification.json")
        }
        replay_result = self.store.read_text(run_id, "replay_result.json")
        if replay_result is not None:
            metadata["replay_result.json"] = replay_result
        result = pack_bundle(self.store.run_dir(run_id), workspace, manifest, metadata)
        self.store.write_text(run_id, "bundle.json", result.model_dump_json(indent=2))
        if result.integrity == "complete":
            self.store.append_event(
                run_id,
                state,
                "bundle_ready",
                "复跑材料已打包并通过完整性校验",
                {"integrity": "complete", "total_bytes": result.total_bytes,
                 "manifest_digest": result.manifest_digest},
            )
        return result

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
        replay: ReplayResult | None = None,
    ) -> RunResult:
        verification = self._stored_verification(run_id)
        result = RunResult(
            run_id=run_id,
            outcome=outcome,
            verified=verification.status == "passed",
            verification=verification,
            attempts=attempts,
            failure_category=category,
            resolved_revision=resolved_revision,
            patch_url=f"/api/runs/{run_id}/patch",
            environment_url=f"/api/runs/{run_id}/environment",
            report_url=f"/api/runs/{run_id}/report",
            replay=replay,
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
        bundle = None
        fail_workspace = self.store.run_dir(run_id) / "workspace"
        if plan is not None and fail_workspace.is_dir():
            bundle = self._finalize_bundle(
                run_id, fail_workspace, plan, plan.resolved_revision, RunState.FAILED,
                request=request,
            )
        result.bundle = bundle
        result.bundle_url = f"/api/runs/{run_id}/bundle" if bundle is not None else None
        self.store.write_text(
            run_id,
            "report.md",
            "# Report\n\n"
            f"outcome={outcome}\nverified={result.verified}\nfailure_category={category}\n\n"
            f"{message[:2000]}\n\n```json\n{verification.model_dump_json(indent=2)}\n```\n",
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
