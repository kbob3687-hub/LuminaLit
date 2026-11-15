import json
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from paperharness.agent.classifier import UNREPAIRABLE, FailureContext, classify_by_rules
from paperharness.contracts.run import RunPlan, RunRequest, RunResult, RunState
from paperharness.harness.store import RunStore
from paperharness.sandbox.base import SandboxClosedError, SandboxSession, SandboxUnavailableError

INSTALL_COMMAND = ["python", "-m", "pip", "install", "-r", "requirements.txt"]
MAX_ACTIONS_PER_REPAIR = 20
# 总墙钟预算 = 单步限时 × 3 + 60 秒，防止每步都不超时但整体卡死工作线程
TOTAL_BUDGET_FACTOR = 3
TOTAL_BUDGET_GRACE_SECONDS = 60
TIME_BUDGET_EXHAUSTED = "time_budget_exceeded"


class TimeBudgetExceeded(RuntimeError):
    """本次运行的墙钟预算耗尽。"""


def git_clone(request: RunRequest, run_dir: Path) -> str:
    """默认 cloner：宿主机 clone 仓库（契约 1.1：沙箱不做任何 git 网络操作）。"""
    workspace = run_dir / "workspace"
    subprocess.run(
        ["git", "clone", request.repo_url, str(workspace)],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if request.revision:
        subprocess.run(
            ["git", "-C", str(workspace), "checkout", request.revision],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
    head = subprocess.run(
        ["git", "-C", str(workspace), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return head.stdout.strip()


class HarnessRunner:
    def __init__(
        self,
        store: RunStore,
        llm,
        sandbox_factory: Callable[[RunRequest, Path], SandboxSession],
        cloner: Callable[[RunRequest, Path], str] = git_clone,
        aci_factory: Callable[[SandboxSession, Path, RunPlan, int], object] | None = None,
    ):
        self.store = store
        self.llm = llm
        self.sandbox_factory = sandbox_factory
        self.cloner = cloner
        self.aci_factory = aci_factory

    def run(self, run_id: str, request: RunRequest) -> RunResult:
        run_dir = self.store.run_dir(run_id)
        workspace = run_dir / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        deadline = (
            time.monotonic()
            + request.timeout_seconds * TOTAL_BUDGET_FACTOR
            + TOTAL_BUDGET_GRACE_SECONDS
        )
        try:
            session = self.sandbox_factory(request, run_dir)
        except SandboxUnavailableError as exc:
            return self._fail(run_id, "service_interrupted", f"sandbox unavailable: {exc}")
        try:
            self.store.write_status(run_id, RunState.PREPARING, {})
            try:
                resolved_revision = self.cloner(request, run_dir)
            except Exception as exc:  # noqa: BLE001
                return self._fail(run_id, "clone_failed", f"clone failed: {exc}")
            self.store.append_event(
                run_id,
                RunState.PREPARING,
                "preparing",
                f"cloned at {resolved_revision}",
            )

            self.store.write_status(run_id, RunState.PLANNING, {})
            plan = self.llm.propose_run_plan(request)
            plan = plan.model_copy(update={"resolved_revision": resolved_revision})
            self.store.write_text(run_id, "run_plan.json", plan.model_dump_json(indent=2))
            self.store.append_event(
                run_id,
                RunState.PLANNING,
                "plan",
                "run plan resolved",
                plan.model_dump(mode="json"),
            )

            install = self._run_install(run_id, request, session, workspace, deadline)
            attempts = 1

            if install is not None and install.exit_code != 0:
                # 安装失败不再被忽略：算进自愈，或直接判失败
                if self.aci_factory is not None and request.max_repairs > 0:
                    healed = self._self_heal(
                        run_id, request, session, workspace, plan, install,
                        phase="install", attempts=0, deadline=deadline,
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
                    )
            else:
                test = self._run_test(run_id, request, session, plan, deadline)
                verified = self._is_verified(test, plan, workspace)
                failure_category = None if verified else "smoke_test_failed"
                outcome = "succeeded" if verified else "failed"

                if not verified and self.aci_factory is not None and request.max_repairs > 0:
                    healed = self._self_heal(
                        run_id, request, session, workspace, plan, test,
                        phase="test", attempts=1, deadline=deadline,
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
                    {
                        "resolved_revision": plan.resolved_revision,
                        "python_version": plan.python_version,
                        "command": plan.command,
                    },
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
        except TimeBudgetExceeded as exc:
            return self._fail(
                run_id, TIME_BUDGET_EXHAUSTED, str(exc), outcome="budget_exceeded", session=session
            )
        except SandboxClosedError as exc:
            return self._fail(run_id, "timeout", f"sandbox closed: {exc}", session=session)
        except SandboxUnavailableError as exc:
            return self._fail(
                run_id, "service_interrupted", f"sandbox unavailable: {exc}", session=session
            )
        finally:
            session.close()

    def _is_verified(self, test, plan: RunPlan, workspace: Path) -> bool:
        if test.exit_code != 0 or test.timed_out:
            return False
        if plan.expected_artifact:
            return (workspace / plan.expected_artifact).exists()
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
        attempts: int,
        deadline: float,
    ) -> dict:
        """DIAGNOSING → REPAIRING → 重跑当前阶段 的循环。

        phase 表示上一步失败的阶段：install 或 test。
        install 阶段修好后再进入 test，test 通过才算成功。
        模型不拥有控制流：循环、重验、认输全在本方法。
        """
        aci = self.aci_factory(session, workspace, plan, request.timeout_seconds)
        history: list = []
        category: str | None = None
        outcome = "budget_exceeded"
        failure = first_failure

        for round_no in range(1, request.max_repairs + 1):
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

            self.store.write_status(run_id, RunState.REPAIRING, {"attempt": round_no})
            for _ in range(MAX_ACTIONS_PER_REPAIR):
                action = self.llm.propose_action(history)
                observation = aci.execute(action)
                history.append(observation)
                self.store.append_event(
                    run_id,
                    RunState.REPAIRING,
                    action.action,
                    observation.summary,
                    {"ok": observation.ok, "exit_code": observation.exit_code},
                )
                if action.action == "finish_repair":
                    break
                if action.action == "run_smoke_test" and observation.exit_code == 0:
                    break
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

            test = self._run_test(run_id, request, session, plan, deadline, attempt=attempts + 1)
            attempts += 1
            if self._is_verified(test, plan, workspace):
                return {
                    "verified": True,
                    "attempts": attempts,
                    "failure_category": category,
                    "outcome": "succeeded",
                }
            failure = test
        return {
            "verified": False,
            "attempts": attempts,
            "failure_category": category,
            "outcome": outcome,
        }

    def _run_install(self, run_id: str, request: RunRequest, session, workspace: Path, deadline: float):
        """跑依赖安装。没有 requirements.txt 时返回 None（无依赖清单，不算失败）。"""
        self.store.write_status(run_id, RunState.INSTALLING, {})
        if not (workspace / "requirements.txt").exists():
            self.store.append_event(
                run_id,
                RunState.INSTALLING,
                "install",
                "no requirements.txt, install skipped",
            )
            return None
        session.set_network(True)
        install = session.execute(
            INSTALL_COMMAND, timeout_seconds=self._step_timeout(request, deadline)
        )
        self.store.append_event(
            run_id,
            RunState.INSTALLING,
            "install",
            "dependency install finished",
            {"exit_code": install.exit_code, "stderr_tail": install.stderr_tail[-500:]},
        )
        return install

    def _run_test(self, run_id: str, request: RunRequest, session, plan: RunPlan, deadline: float, attempt: int | None = None):
        self.store.write_status(run_id, RunState.TESTING, {"attempt": attempt} if attempt else {})
        session.set_network(False)
        test = session.execute(plan.command, timeout_seconds=self._step_timeout(request, deadline))
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

    def _fail(
        self,
        run_id: str,
        category: str,
        message: str,
        outcome: str = "failed",
        session: SandboxSession | None = None,
    ) -> RunResult:
        result = RunResult(
            run_id=run_id,
            outcome=outcome,
            verified=False,
            attempts=1,
            failure_category=category,
            patch_url=f"/api/runs/{run_id}/patch",
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
