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
# 评审稿 §4.1 / R06 / §13：执行权限与验收链保护。
_ACTION_NOT_ALLOWED = "action_not_allowed:{name}"
_CHANGE_NOT_ALLOWED = "change_not_allowed:{path}"
_PROTECTED_FILE = "protected_file:{path}"


def path_escape(raw: str) -> str:
    """越界时回显模型给的**原始字符串**，不是解析后的路径。"""
    return _PATH_ESCAPE.format(raw=raw)


def env_not_allowed(name: str) -> str:
    return _ENV_NOT_ALLOWED.format(name=name)


def invalid_argument(field: str) -> str:
    return _INVALID_ARGUMENT.format(field=field)


def action_not_allowed(name: str) -> str:
    """动作不在该任务声明的 allowed_actions 里。"""
    return _ACTION_NOT_ALLOWED.format(name=name)


def change_not_allowed(path: str) -> str:
    """变更不在 allowed_changes 声明的路径/依赖范围内。"""
    return _CHANGE_NOT_ALLOWED.format(path=path)


def protected_file(path: str) -> str:
    """验收链文件（check.py / 预期产物）不可由修复动作改写。"""
    return _PROTECTED_FILE.format(path=path)
