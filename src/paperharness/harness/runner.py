import json
import subprocess
from collections.abc import Callable
from pathlib import Path

from paperharness.contracts.run import RunRequest, RunResult, RunState
from paperharness.harness.store import RunStore
from paperharness.sandbox.base import SandboxClosedError, SandboxSession, SandboxUnavailableError

INSTALL_COMMAND = ["python", "-m", "pip", "install", "-r", "requirements.txt"]


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
    ):
        self.store = store
        self.llm = llm
        self.sandbox_factory = sandbox_factory
        self.cloner = cloner

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
        except SandboxClosedError as exc:
            return self._fail(run_id, "timeout", f"sandbox closed: {exc}")
        except SandboxUnavailableError as exc:
            return self._fail(run_id, "service_interrupted", f"sandbox unavailable: {exc}")
        finally:
            session.close()

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
