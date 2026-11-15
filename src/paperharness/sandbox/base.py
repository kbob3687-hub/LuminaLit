from typing import Protocol

from paperharness.contracts.run import ExecResult


class SandboxClosedError(RuntimeError):
    """Session is unusable after timeout or explicit close."""


class SandboxUnavailableError(RuntimeError):
    """Docker engine or container lifecycle failed."""


class SandboxSession(Protocol):
    def execute(self, command: list[str], timeout_seconds: int) -> ExecResult: ...

    def set_network(self, enabled: bool) -> None: ...

    def set_environment(self, name: str, value: str) -> None: ...

    def export_diff(self) -> str: ...

    def close(self) -> None: ...
