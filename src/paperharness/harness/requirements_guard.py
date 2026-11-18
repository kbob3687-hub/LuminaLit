"""requirements.txt 文件内容校验（Harness 层）。

`pip install -r requirements.txt` 读的是仓库自带的文件——内容来自任意公开
仓库，与模型给的依赖字符串同属不可信输入。单行规则复用
`paperharness.aci.requirement_guard.FORBIDDEN_SUBSTRINGS`，文件级仅放行分号 `;`：
`INSTALL_COMMAND` 以 argv 数组传给容器 exec、不经 shell，分号用于 PEP 508
环境标记（如 `numpy==1.26.4; python_version<"3.12"`），其余非法写法由
`packaging.Requirement` 拦截。

文件级额外规则：

- 空行、`#` 注释行跳过；行内注释（空白 + `#` 起）截断后继续校验
- 以 `-` 开头的 pip 选项（`--index-url`、`-r`、`-e` …）一律拒绝：这是换源、
  递归拉别的文件、执行 setup.py 的入口
- 反斜杠一律拒绝：行尾 ``\\`` 续行能把内容拆到两行，绕开逐行校验
"""

from __future__ import annotations

import re

from packaging.requirements import InvalidRequirement, Requirement

from paperharness.aci.requirement_guard import FORBIDDEN_SUBSTRINGS

# 文件级复用 ACI 单一来源禁词表，但放行分号（PEP 508 环境标记；INSTALL_COMMAND 不经 shell）。
_FILE_FORBIDDEN_SUBSTRINGS = tuple(tok for tok in FORBIDDEN_SUBSTRINGS if tok != ";")
_INLINE_COMMENT = re.compile(r"\s#")

# 机器可读的拒绝原因（进事件 detail，前端不映射，原样展示）。
REASON_PIP_OPTION = "pip_option"
REASON_LINE_CONTINUATION = "line_continuation"
REASON_FORBIDDEN_TOKEN = "forbidden_token"
REASON_DIRECT_REFERENCE = "direct_reference"
REASON_INVALID_REQUIREMENT = "invalid_requirement"


class RequirementsNotAllowed(Exception):
    """requirements.txt 里有一行不合法：整体拒绝安装。"""

    def __init__(self, line_no: int, raw_line: str, reason: str, detail: str = ""):
        self.line_no = line_no
        self.raw_line = raw_line
        self.reason = reason
        self.detail = detail
        super().__init__(self.__str__())

    def __str__(self) -> str:
        raw = self.raw_line.strip()[:200]
        message = f"requirements.txt:{self.line_no}: {self.reason}: {raw}"
        if self.detail:
            message += f" ({self.detail[:200]})"
        return message


def validate_requirements_file(text: str) -> list[str]:
    """校验通过返回解析出的依赖行列表；任一行不合法抛 `RequirementsNotAllowed`。

    拒绝发生在读文件的调用方拿到任何执行机会之前：调用方不得在校验通过前
    启动 pip（拒绝时零副作用）。
    """
    requirements: list[str] = []
    for line_no, raw_line in enumerate(text.lstrip("\ufeff").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        # 续行符先于一切检查：`\` 能把内容拆到两行，躲开下面的逐行规则。
        if "\\" in raw_line:
            raise RequirementsNotAllowed(line_no, raw_line, REASON_LINE_CONTINUATION)

        # 行内注释：pip 的规则是空白 + `#` 起，截断后继续校验剩余部分。
        comment = _INLINE_COMMENT.search(line)
        if comment is not None:
            line = line[: comment.start()].strip()
            if not line:
                continue

        if line.startswith("-"):
            raise RequirementsNotAllowed(line_no, raw_line, REASON_PIP_OPTION)

        if any(token in line for token in _FILE_FORBIDDEN_SUBSTRINGS):
            raise RequirementsNotAllowed(line_no, raw_line, REASON_FORBIDDEN_TOKEN)

        # 直接引用（pkg @ …）绕过索引从任意 URL 拉包，与依赖串口径一致，一律拒绝。
        if "@" in line:
            raise RequirementsNotAllowed(line_no, raw_line, REASON_DIRECT_REFERENCE)

        try:
            Requirement(line)
        except InvalidRequirement as exc:
            raise RequirementsNotAllowed(
                line_no, raw_line, REASON_INVALID_REQUIREMENT, str(exc)
            ) from exc

        requirements.append(line)
    return requirements
