"""失败标签的完整清单测试。

契约 §4.2 规定前端靠 `summary` 做 `failure_category` 映射，少一种标签前端就
对不上。设计 §8 原来写"固定 6 个"，实际有 8 种（3 个模板 + 5 个常量），
`invalid_argument` 一直没申报——这个文件把它钉死，任何新增/改名都会失败。
"""

import pytest

from paperharness.aci import errors

#: 前端映射表必须覆盖的全部失败标签。新增标签必须先出现在这里。
EXPECTED_TAGS = {
    "file_not_found",
    "replace_not_found",
    "replace_not_unique",
    "unknown_action",
    "requirement_not_allowed",
    "path_escape",
    "env_not_allowed",
    "invalid_argument",
    # 写到验收链上的文件（官方注入的 check.py、期望产物）：写入被拒。
    "protected_file",
}


def _actual_tags() -> set[str]:
    """从 errors 模块真实导出中提取标签词干。"""
    tags: set[str] = set()
    for name in dir(errors):
        if name.isupper() and isinstance(getattr(errors, name), str):
            value = getattr(errors, name)
            if "{" in value:  # 模板形如 "path_escape:{raw}"
                tags.add(value.split("{", 1)[0].rstrip(":"))
            else:
                tags.add(value)
    return tags


def test_error_tags_match_declared_set():
    """errors.py 导出的标签必须与清单完全一致（不多不少）。"""
    assert _actual_tags() == EXPECTED_TAGS


def test_every_tag_has_a_builder_or_constant():
    """3 个模板标签必须有对应的构造函数，否则调用方会手写字符串。"""
    for fn_name, tag in [
        ("path_escape", "path_escape"),
        ("env_not_allowed", "env_not_allowed"),
        ("invalid_argument", "invalid_argument"),
    ]:
        fn = getattr(errors, fn_name)
        produced = fn("X")
        assert produced.startswith(tag + ":"), f"{fn_name} 产出 {produced!r} 与标签不符"


@pytest.mark.parametrize(
    ("builder", "arg", "expected"),
    [
        ("path_escape", "../x", "path_escape:../x"),
        ("env_not_allowed", "PATH", "env_not_allowed:PATH"),
        ("invalid_argument", "start_line", "invalid_argument:start_line"),
    ],
)
def test_templated_tags_are_verbatim(builder, arg, expected):
    """模板标签逐字拼接，不允许额外装饰（前端做前缀匹配）。"""
    assert getattr(errors, builder)(arg) == expected


def test_templates_use_single_colon_separator():
    """模板必须是 `tag:{value}` 单冒号形态，否则拼出来会是 `path_escape::../x`。

    冒号本身是合法的分隔符（前端按 `:` 前缀匹配），要查的是**没有双冒号**。
    """
    for name in ("_PATH_ESCAPE", "_ENV_NOT_ALLOWED", "_INVALID_ARGUMENT"):
        value = getattr(errors, name)
        head, _, tail = value.partition("{")
        assert head.endswith(":"), f"{name} 缺少分隔冒号：{value!r}"
        assert not head.endswith("::"), f"{name} 出现双冒号：{value!r}"
        assert tail.startswith(("raw", "name", "field")), f"{name} 占位符异常：{value!r}"
