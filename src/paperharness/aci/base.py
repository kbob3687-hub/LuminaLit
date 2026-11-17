"""ACI 的接口协议。

模型只能拿到 `Observation`，永远拿不到 `SandboxSession`——这是整层的信任边界。
"""

from typing import Protocol, runtime_checkable

from paperharness.contracts.run import Observation, ToolAction


@runtime_checkable
class AgentComputerInterface(Protocol):
    def execute(self, action: ToolAction) -> Observation: ...
