from typing import Protocol

from paperharness.agent.classifier import FailureContext
from paperharness.contracts.run import Observation, RunPlan, RunRequest, ToolAction


class LLMPort(Protocol):
    def propose_run_plan(self, request: RunRequest) -> RunPlan: ...

    def classify_failure(self, context: FailureContext) -> str: ...

    def propose_action(self, history: list[Observation]) -> ToolAction: ...
