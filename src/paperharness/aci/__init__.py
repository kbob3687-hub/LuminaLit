"""ACI 校验层：模型动作的唯一收口。

对外只暴露 `PaperACI` 与 `AgentComputerInterface`。`summary` 常量在
`errors` 里集中管理，前端映射表以它为真源。
"""

from paperharness.aci.base import AgentComputerInterface
from paperharness.aci.errors import (
    FILE_NOT_FOUND,
    REPLACE_NOT_FOUND,
    REPLACE_NOT_UNIQUE,
    REQUIREMENT_NOT_ALLOWED,
    UNKNOWN_ACTION,
)
from paperharness.aci.paper_aci import ALLOWED_ENV_NAMES, PaperACI

__all__ = [
    "ALLOWED_ENV_NAMES",
    "FILE_NOT_FOUND",
    "REPLACE_NOT_FOUND",
    "REPLACE_NOT_UNIQUE",
    "REQUIREMENT_NOT_ALLOWED",
    "UNKNOWN_ACTION",
    "AgentComputerInterface",
    "PaperACI",
]
