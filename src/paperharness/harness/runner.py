import json
from collections.abc import Callable
from pathlib import Path

from paperharness.contracts.run import RunRequest, RunResult, RunState
from paperharness.harness.store import RunStore
from paperharness.sandbox.base import SandboxSession

INSTALL_COMMAND = ["python", "-m", "pip", "install", "-r", "requirements.txt"]


class HarnessRunner:
    def __init__(
        self,
        store: RunStore,
        llm,
        sandbox_factory: Callable[[RunRequest, Path], SandboxSession],
    ):
        self.store = store
        self.llm = llm
        self.sandbox_factory = sandbox_factory

    def run(self, run_id: str, request: RunRequest) -> RunResult:
        run_dir = self.store.run_dir(run_id)
        workspace = run_dir / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        session = self.sandbox_factory(request, run_dir)
        try:
            self.store.write_status(run_id, RunState.PREPARING, {})
            self.store.append_event(run_id, RunState.PREPARING, "preparing", "workspace prepared")

            self.store.write_status(run_id, RunState.PLANNING, {})
            plan = self.llm.propose_run_plan(request)
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

            verified = test.exit_code == 0 and not test.timed_out
            if verified and plan.expected_artifact:
                verified = (workspace / plan.expected_artifact).exists()

            state = RunState.SUCCEEDED if verified else RunState.FAILED
            self.store.write_text(run_id, "patch.diff", session.export_diff())
            self.store.write_text(run_id, "report.md", f"# Report\n\nverified={verified}\n")
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
                outcome="succeeded" if verified else "failed",
                verified=verified,
                attempts=1,
                failure_category=None if verified else "smoke_test_failed",
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
        finally:
            session.close()
