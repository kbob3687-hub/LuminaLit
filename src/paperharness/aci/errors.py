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
PROTECTED_FILE = "protected_file"

_PATH_ESCAPE = "path_escape:{raw}"
_ENV_NOT_ALLOWED = "env_not_allowed:{name}"
_INVALID_ARGUMENT = "invalid_argument:{field}"
_PROTECTED_FILE = "protected_file:{path}"


def path_escape(raw: str) -> str:
    """越界时回显模型给的**原始字符串**，不是解析后的路径。"""
    return _PATH_ESCAPE.format(raw=raw)


def env_not_allowed(name: str) -> str:
    return _ENV_NOT_ALLOWED.format(name=name)


def invalid_argument(field: str) -> str:
    return _INVALID_ARGUMENT.format(field=field)


def protected_file(path: str) -> str:
    """写到了验收链上的文件（官方注入的验收脚本、期望产物）。

    `path` 用 workspace 相对路径的 POSIX 形式，和模型给的原样不完全一致，
    但它只用于人读与日志对齐，判定本身在 `PaperACI._protected_reason`。
    """
    return _PROTECTED_FILE.format(path=path)
