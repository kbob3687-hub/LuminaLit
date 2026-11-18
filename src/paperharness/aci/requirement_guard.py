"""`install_requirement` 的字符串校验。

模型给的是一个依赖字符串，最终会变成 `pip install <字符串>`。校验的目标不是
「这个包好不好」，而是「这个字符串会不会变成第二条命令」。
"""

from packaging.requirements import InvalidRequirement, Requirement

FORBIDDEN_SUBSTRINGS = ("\n", "\r", ";", "&&", "|", "`", "$(")
_FORBIDDEN_SUBSTRINGS = FORBIDDEN_SUBSTRINGS


class RequirementNotAllowed(Exception):
    """依赖字符串不合法（解析不了，或含命令注入面）。"""

    def __init__(self, raw: str):
        super().__init__(raw)
        self.raw = raw


def validate(raw: str) -> str:
    """校验通过返回原字符串；不通过抛 `RequirementNotAllowed`。

    顺序：先查注入面再 parse。反过来做也能拦住大部分，但
    `packaging` 报的错误对模型没有意义，先给一条明确原因更好定位。
    """
    if not isinstance(raw, str) or not raw.strip():
        raise RequirementNotAllowed(raw if isinstance(raw, str) else "")

    if any(token in raw for token in FORBIDDEN_SUBSTRINGS):
        raise RequirementNotAllowed(raw)

    # 直接引用（pkg @ https://...）会绕过索引从任意 URL 拉包，
    # 等价于允许联网下载任意内容，一律拒绝。
    if "@" in raw:
        raise RequirementNotAllowed(raw)

    try:
        Requirement(raw)
    except InvalidRequirement as exc:
        raise RequirementNotAllowed(raw) from exc

    return raw
