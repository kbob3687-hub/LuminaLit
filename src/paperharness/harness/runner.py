import json
import subprocess
from collections.abc import Callable
from pathlib import Path

from paperharness.agent.classifier import UNREPAIRABLE, FailureContext
from paperharness.contracts.run import RunPlan, RunRequest, RunResult, RunState
from paperharness.harness.store import RunStore
from paperharness.sandbox.base import SandboxClosedError, SandboxSession, SandboxUnavailableError

INSTALL_COMMAND = ["python", "-m", "pip", "install", "-r", "requirements.txt"]
MAX_ACTIONS_PER_REPAIR = 20


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

            self.store.write_status(run_id, RunState.INSTALLING, {})
            session.set_network(True)
            install = session.execute(INSTALL_COMMAND, timeout_seconds=request.timeout_seconds)
            self.store.append_event(
                run_id,
                RunState.INSTALLING,
                "install",
                "dependency install finished",
                {"exit_code": install.exit_code},
            )

            self.store.write_status(run_id, RunState.TESTING, {})
            session.set_network(False)
            test = session.execute(plan.command, timeout_seconds=request.timeout_seconds)
            self.store.append_event(
                run_id,
                RunState.TESTING,
                "test",
                "smoke test finished",
                {"exit_code": test.exit_code, "stderr_tail": test.stderr_tail},
            )

            verified = self._is_verified(test, plan, workspace)
            attempts = 1
            failure_category = None if verified else "smoke_test_failed"
            outcome = "succeeded" if verified else "failed"

            if not verified and self.aci_factory is not None and request.max_repairs > 0:
                healed = self._self_heal(run_id, request, session, workspace, plan, test)
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
        except SandboxClosedError as exc:
            return self._fail(run_id, "timeout", f"sandbox closed: {exc}")
        except SandboxUnavailableError as exc:
            return self._fail(run_id, "service_interrupted", f"sandbox unavailable: {exc}")
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
        first_test,
    ) -> dict:
        """DIAGNOSING → REPAIRING → TESTING 循环，预算由 max_repairs 控制。

        模型不拥有控制流：只通过 propose_action 出动作，循环、重验、认输全在本方法。
        """
        aci = self.aci_factory(session, workspace, plan, request.timeout_seconds)
        history: list = []
        attempts = 1
        category: str | None = None
        outcome = "budget_exceeded"
        test = first_test

        for round_no in range(1, request.max_repairs + 1):
            self.store.write_status(run_id, RunState.DIAGNOSING, {"attempt": round_no})
            category = self.llm.classify_failure(
                FailureContext(
                    exit_code=test.exit_code,
                    timed_out=test.timed_out,
                    stdout_tail=test.stdout_tail,
                    stderr_tail=test.stderr_tail,
                )
            )
            self.store.append_event(
                run_id,
                RunState.DIAGNOSING,
                "diagnosis",
                f"failure classified as {category}",
                {"failure_class": category, "attempt": round_no},
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

            self.store.write_status(run_id, RunState.TESTING, {"attempt": round_no + 1})
            session.set_network(False)
            test = session.execute(plan.command, timeout_seconds=request.timeout_seconds)
            attempts += 1
            self.store.append_event(
                run_id,
                RunState.TESTING,
                "test",
                f"smoke test finished (attempt {attempts})",
                {"exit_code": test.exit_code, "attempt": attempts},
            )
            if self._is_verified(test, plan, workspace):
                return {
                    "verified": True,
                    "attempts": attempts,
                    "failure_category": category,
                    "outcome": "succeeded",
                }
        return {
            "verified": False,
            "attempts": attempts,
            "failure_category": category,
            "outcome": outcome,
        }

    def _fail(self, run_id: str, category: str, message: str) -> RunResult:
        result = RunResult(
            run_id=run_id,
            outcome="failed",
            verified=False,
            attempts=1,
            failure_category=category,
            patch_url=f"/api/runs/{run_id}/patch",
            report_url=f"/api/runs/{run_id}/report",
        )
        self.store.write_text(run_id, "patch.diff", "")
        self.store.write_text(
            run_id,
            "report.md",
            f"# Report\n\noutcome=failed\nfailure_category={category}\n\n{message[:2000]}\n",
        )
        self.store.write_status(run_id, RunState.FAILED, {"failure_category": category})
        self.store.append_event(
            run_id,
            RunState.FAILED,
            "final",
            f"run failed: {category}",
            {"failure_category": category, "message": message[:2000]},
        )
        self.store.write_result(run_id, result)
        return result
