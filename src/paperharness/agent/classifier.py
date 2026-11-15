"""失败分类：规则优先（确定、零成本），规则未命中再问模型兜底。

分类值对齐靶场八类（见 targets/manifest.py 的 FailureClass）。
"""

import re

from pydantic import BaseModel


class FailureContext(BaseModel):
    exit_code: int | None
    timed_out: bool
    stdout_tail: str = ""
    stderr_tail: str = ""


# 顺序即优先级：具体错误在前，宽泛模式在后
_RULES: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"ModuleNotFoundError|ImportError"),
        "missing_dependency",
    ),
    (
        re.compile(r"VersionConflict|conflicting versions|DependencyResolutionError"),
        "version_conflict",
    ),
    (
        re.compile(r"FileNotFoundError[^\n]*(dataset|corpus|private)|dataset not found"),
        "missing_private_data",
    ),
    (re.compile(r"/(home|Users|data|mnt|root)/"), "hardcoded_path"),
    (re.compile(r"CUDA", re.IGNORECASE), "cuda_only"),
    (re.compile(r"requires GPU|nvidia-smi|No CUDA-capable device"), "requires_gpu"),
    (re.compile(r"DeprecationWarning|deprecated", re.IGNORECASE), "deprecated_api"),
]

UNREPAIRABLE = frozenset({"missing_private_data", "requires_gpu"})


def classify_by_rules(context: FailureContext) -> str | None:
    """规则表分类：命中返回八类之一，未命中返回 None（交给模型兜底）。"""
    text = f"{context.stderr_tail}\n{context.stdout_tail}"
    for pattern, category in _RULES:
        if pattern.search(text):
            return category
    return None
