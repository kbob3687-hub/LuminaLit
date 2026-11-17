from paperharness.agent.classifier import FailureContext, classify_by_rules
from paperharness.contracts.run import (
    FinishRepairAction,
    Observation,
    RunPlan,
    RunRequest,
    ToolAction,
)


class FakeLLM:
    """测试用假模型：分类走规则表，动作按脚本出队。"""

    def __init__(self, actions: list[ToolAction] | None = None):
        self._actions = list(actions or [])

    def propose_run_plan(self, request: RunRequest) -> RunPlan:
        command = request.command or ["python", "main.py"]
        return RunPlan(
            resolved_revision=request.revision or "fake-commit",
            python_version=request.python_version or "3.11",
            command=command,
            expected_artifact=request.expected_artifact,
            source="user" if request.command else "repository_analysis",
        )

    def classify_failure(self, context: FailureContext) -> str:
        return classify_by_rules(context) or "unknown"

    def propose_action(self, history: list[Observation]) -> ToolAction:
        if self._actions:
            return self._actions.pop(0)
        return FinishRepairAction(action="finish_repair", summary="no scripted action left")
