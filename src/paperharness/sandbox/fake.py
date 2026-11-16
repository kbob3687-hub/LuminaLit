from pathlib import Path

from paperharness.contracts.run import ExecResult


class FakeSandboxSession:
    def __init__(
        self,
        results: list[ExecResult] | None = None,
        artifact_path: str | None = None,
        workspace: Path | None = None,
    ):
        self.results = list(results or [])
        self.artifact_path = artifact_path
        self.workspace = workspace or Path(".")
        self.network_enabled = True
        self.closed = False
        self.cleanup_errors: list[str] = []  # 与 DockerSandboxSession 对齐
        self.environment: dict[str, str] = {}
        self.commands: list[list[str]] = []

    def execute(self, command: list[str], timeout_seconds: int) -> ExecResult:
        self.commands.append(command)
        if self.results:
            result = self.results.pop(0)
        else:
            result = ExecResult(
                exit_code=0,
                timed_out=False,
                duration_ms=1,
                stdout_path="logs/fake.log",
                stderr_path="logs/fake.err",
            )
        if result.exit_code == 0 and self.artifact_path:
            artifact = self.workspace / self.artifact_path
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text("fake artifact", encoding="utf-8")
        return result

    def set_network(self, enabled: bool) -> None:
        self.network_enabled = enabled

    def set_environment(self, name: str, value: str) -> None:
        self.environment[name] = value

    def export_diff(self) -> str:
        return ""

    def close(self) -> None:
        self.closed = True
