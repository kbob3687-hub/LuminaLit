"""ACI 的 summary 常量。

`summary` 是前端与评测共同依赖的机器可读信号（契约 §4.2 的 `failure_category`
由它上游派生）。散落在各处写字符串，迟早有人改一个字，前端就静默失灵，
因此集中在此处，逐字固定。
"""

FILE_NOT_FOUND = "file_not_found"
REPLACE_NOT_FOUND = "replace_not_found"
REPLACE_NOT_UNIQUE = "replace_not_unique"
UNKNOWN_ACTION = "unknown_action"
REQUIREMENT_NOT_ALLOWED = "requirement_not_allowed"

_PATH_ESCAPE = "path_escape:{raw}"
_ENV_NOT_ALLOWED = "env_not_allowed:{name}"
_INVALID_ARGUMENT = "invalid_argument:{field}"


def path_escape(raw: str) -> str:
    """越界时回显模型给的**原始字符串**，不是解析后的路径。"""
    return _PATH_ESCAPE.format(raw=raw)


def env_not_allowed(name: str) -> str:
    return _ENV_NOT_ALLOWED.format(name=name)


def invalid_argument(field: str) -> str:
    return _INVALID_ARGUMENT.format(field=field)
