from typing import Protocol

from paperharness.contracts.run import RunPlan, RunRequest


class LLMPort(Protocol):
    def propose_run_plan(self, request: RunRequest) -> RunPlan: ...
