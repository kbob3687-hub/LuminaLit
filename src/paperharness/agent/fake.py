from paperharness.contracts.run import RunPlan, RunRequest


class FakeLLM:
    def propose_run_plan(self, request: RunRequest) -> RunPlan:
        command = request.command or ["python", "main.py"]
        return RunPlan(
            resolved_revision=request.revision or "fake-commit",
            python_version=request.python_version or "3.11",
            command=command,
            expected_artifact=request.expected_artifact,
            source="user" if request.command else "repository_analysis",
        )
