"""PaperACI 单元测试（设计稿 §9 的 21 条）。

全部使用 `FakeSandboxSession`，不依赖 Docker。第 6、12、14 条专门断言
「拒绝时无副作用」——最容易被写漏、也最容易被评委追问的一条。
"""

import os
import subprocess
from pathlib import Path

import pytest

from paperharness.aci import (
    FILE_NOT_FOUND,
    REPLACE_NOT_FOUND,
    REPLACE_NOT_UNIQUE,
    REQUIREMENT_NOT_ALLOWED,
    UNKNOWN_ACTION,
    PaperACI,
)
from paperharness.contracts.run import (
    ExecResult,
    FinishRepairAction,
    InstallRequirementAction,
    ReadFileAction,
    ReplaceExactAction,
    RunPlan,
    RunSmokeTestAction,
    SearchTextAction,
    SetEnvironmentAction,
)
from paperharness.sandbox.fake import FakeSandboxSession


def make_plan(command=None, artifact=None) -> RunPlan:
    return RunPlan(
        resolved_revision="0" * 40,
        python_version="3.11",
        command=command or ["python", "main.py"],
        expected_artifact=artifact,
        source="user",
    )


def make_result(exit_code=0, timed_out=False, stdout="", stderr="") -> ExecResult:
    return ExecResult(
        exit_code=exit_code,
        timed_out=timed_out,
        duration_ms=1,
        stdout_path="logs/out.log",
        stderr_path="logs/err.log",
        stdout_tail=stdout,
        stderr_tail=stderr,
    )


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "workspace"
    (root / "src").mkdir(parents=True)
    (root / "src" / "main.py").write_text(
        "import numpy\nprint('hello')\n", encoding="utf-8"
    )
    return root


@pytest.fixture
def session(workspace):
    return FakeSandboxSession(workspace=workspace)


@pytest.fixture
def aci(session, workspace):
    return PaperACI(session, workspace, make_plan(), timeout_seconds=300)


def test_01_read_file_ok(aci, workspace):
    result = aci.execute(ReadFileAction(action="read_file", path="src/main.py"))

    assert result.ok is True
    assert result.summary == "read 2 lines from src/main.py"
    assert result.stdout_tail == "1: import numpy\n2: print('hello')"


def test_01b_read_file_respects_start_line_and_count(aci, workspace):
    (workspace / "many.txt").write_text("a\nb\nc\nd\n", encoding="utf-8")

    result = aci.execute(
        ReadFileAction(action="read_file", path="many.txt", start_line=2, line_count=2)
    )

    assert result.ok is True
    assert result.stdout_tail == "2: b\n3: c"
    assert result.summary == "read 2 lines from many.txt"


def test_02_read_file_missing(aci):
    result = aci.execute(ReadFileAction(action="read_file", path="nope.txt"))

    assert result.ok is False
    assert result.summary == FILE_NOT_FOUND


def test_03_read_file_parent_escape(aci):
    result = aci.execute(ReadFileAction(action="read_file", path="../secret.txt"))

    assert result.ok is False
    assert result.summary == "path_escape:../secret.txt"


def test_04_read_file_absolute_path(aci):
    result = aci.execute(ReadFileAction(action="read_file", path="C:\\Windows\\win.ini"))

    assert result.ok is False
    assert result.summary == "path_escape:C:\\Windows\\win.ini"


def _link_pointing_to(link: Path, target: Path) -> bool:
    """尽力让 link 指向 target：优先符号链接，Windows 无权限时退化成 junction。

    Windows 非管理员默认不能建文件符号链接（需 SeCreateSymbolicLinkPrivilege），
    但目录 junction 可以。两者都会被 `Path.resolve()` 展开，对路径校验是等价的
    攻击面，所以都能用来验证这条规则。
    """
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
        return True
    except (OSError, NotImplementedError):
        pass
    if os.name != "nt" or not target.is_dir():
        return False
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def test_05_symlink_outside_workspace(aci, workspace, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    if not _link_pointing_to(workspace / "leak", outside):
        pytest.skip("当前平台无法创建符号链接或 junction")

    result = aci.execute(ReadFileAction(action="read_file", path="leak/secret.txt"))

    assert result.ok is False
    assert result.summary == "path_escape:leak/secret.txt"


def test_06_bad_line_args_have_no_side_effect(aci, workspace, session):
    before = (workspace / "src" / "main.py").read_text(encoding="utf-8")
    start_bad = aci.execute(
        ReadFileAction(action="read_file", path="src/main.py", start_line=0)
    )
    count_bad = aci.execute(
        ReadFileAction(action="read_file", path="src/main.py", line_count=0)
    )

    assert start_bad.ok is False
    assert start_bad.summary == "invalid_argument:start_line"
    assert count_bad.ok is False
    assert count_bad.summary == "invalid_argument:line_count"
    assert (workspace / "src" / "main.py").read_text(encoding="utf-8") == before
    assert session.commands == []


def test_07_search_text_no_match_is_not_error(aci):
    result = aci.execute(SearchTextAction(action="search_text", query="找不到的词"))

    assert result.ok is True
    assert result.summary == "0 matches"
    assert result.stdout_tail == ""


def test_08_search_text_truncates_results(aci, workspace):
    lines = "\n".join("needle " + "x" * 300 for _ in range(80))
    (workspace / "big.txt").write_text(lines, encoding="utf-8")

    result = aci.execute(
        SearchTextAction(action="search_text", query="needle", max_results=50)
    )

    rows = result.stdout_tail.splitlines()
    assert result.ok is True
    assert result.summary == "50 matches"
    # stdout_tail 整体受 4000 字符上限约束，头部可能被截断，
    # 所以只断言「不超上限、单条不超 200、格式仍是 路径:行号:内容」。
    assert len(result.stdout_tail) <= 4000
    assert 0 < len(rows) <= 50
    complete = [row for row in rows if row.count(":") >= 2]
    assert complete
    assert all(len(row.split(":", 2)[2]) <= 200 for row in complete)
    assert all(row.startswith("big.txt:") for row in complete)


def test_09_replace_exact_unique(aci, workspace):
    result = aci.execute(
        ReplaceExactAction(
            action="replace_exact",
            path="src/main.py",
            old="import numpy",
            new="import numpy as np",
        )
    )

    assert result.ok is True
    assert result.summary == "replaced 1 occurrence in src/main.py"
    assert "import numpy as np" in (workspace / "src" / "main.py").read_text(
        encoding="utf-8"
    )


def test_10_replace_exact_not_found(aci, workspace):
    before = (workspace / "src" / "main.py").read_text(encoding="utf-8")

    result = aci.execute(
        ReplaceExactAction(action="replace_exact", path="src/main.py", old="zzz", new="y")
    )

    assert result.ok is False
    assert result.summary == REPLACE_NOT_FOUND
    assert (workspace / "src" / "main.py").read_text(encoding="utf-8") == before


def test_11_replace_exact_not_unique(aci, workspace):
    target = workspace / "dup.txt"
    target.write_text("x\nx\n", encoding="utf-8")

    result = aci.execute(
        ReplaceExactAction(action="replace_exact", path="dup.txt", old="x", new="y")
    )

    assert result.ok is False
    assert result.summary == REPLACE_NOT_UNIQUE
    assert target.read_text(encoding="utf-8") == "x\nx\n"


def test_11b_replace_exact_empty_old_is_not_unique(aci, workspace):
    result = aci.execute(
        ReplaceExactAction(action="replace_exact", path="src/main.py", old="", new="y")
    )

    assert result.ok is False
    assert result.summary == REPLACE_NOT_UNIQUE


def test_12_replace_exact_escape_has_no_side_effect(aci, workspace, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("keep", encoding="utf-8")

    result = aci.execute(
        ReplaceExactAction(action="replace_exact", path="../outside.txt", old="keep", new="lost")
    )

    assert result.ok is False
    assert result.summary == "path_escape:../outside.txt"
    assert outside.read_text(encoding="utf-8") == "keep"


def test_13_install_requirement_toggles_network(aci, session):
    result = aci.execute(
        InstallRequirementAction(action="install_requirement", requirement="numpy==2.0.0")
    )

    assert result.ok is True
    assert session.commands == [["python", "-m", "pip", "install", "numpy==2.0.0"]]
    # 装完必须回到进场时的状态
    assert session.network_enabled is True


def test_13b_install_requirement_restores_disabled_network(aci, session):
    session.network_enabled = False
    aci = PaperACI(session, session.workspace, make_plan(), timeout_seconds=300)

    aci.execute(
        InstallRequirementAction(action="install_requirement", requirement="numpy")
    )

    assert session.network_enabled is False


@pytest.mark.parametrize(
    "requirement",
    [
        "numpy; rm -rf /",
        "pkg @ https://example.com/pkg.tar.gz",
        "numpy\n&& curl evil",
        "not a requirement!!!",
    ],
)
def test_14_install_requirement_rejects_without_opening_network(aci, session, requirement):
    session.network_enabled = False
    result = aci.execute(
        InstallRequirementAction(action="install_requirement", requirement=requirement)
    )

    assert result.ok is False
    assert result.summary == REQUIREMENT_NOT_ALLOWED
    assert session.commands == []
    # 校验失败不能开网
    assert session.network_enabled is False


def test_15_install_requirement_restores_network_on_exception(workspace):
    class BoomSession(FakeSandboxSession):
        def execute(self, command, timeout_seconds):
            raise RuntimeError("container closed")

    session = BoomSession(workspace=workspace)
    session.network_enabled = False
    aci = PaperACI(session, workspace, make_plan(), timeout_seconds=300)

    with pytest.raises(RuntimeError):
        aci.execute(
            InstallRequirementAction(action="install_requirement", requirement="numpy")
        )

    assert session.network_enabled is False


def test_16_set_environment_allowed(aci, session):
    result = aci.execute(
        SetEnvironmentAction(
            action="set_environment", name="CUDA_VISIBLE_DEVICES", value="-1"
        )
    )

    assert result.ok is True
    assert session.environment == {"CUDA_VISIBLE_DEVICES": "-1"}


def test_17_set_environment_rejects_path(aci, session):
    result = aci.execute(
        SetEnvironmentAction(action="set_environment", name="PATH", value="/tmp/bin")
    )

    assert result.ok is False
    assert result.summary == "env_not_allowed:PATH"
    assert session.environment == {}


def test_18_run_smoke_test_uses_plan_command(aci, session, workspace):
    aci = PaperACI(
        session, workspace, make_plan(command=["python", "-m", "app"]), timeout_seconds=300
    )

    result = aci.execute(RunSmokeTestAction(action="run_smoke_test"))

    assert session.commands == [["python", "-m", "app"]]
    assert session.network_enabled is False
    assert result.ok is True
    assert result.exit_code == 0


def test_19_run_smoke_test_timeout_passthrough(workspace):
    session = FakeSandboxSession(
        results=[make_result(exit_code=None, timed_out=True)], workspace=workspace
    )
    aci = PaperACI(session, workspace, make_plan(), timeout_seconds=300)

    result = aci.execute(RunSmokeTestAction(action="run_smoke_test"))

    assert result.ok is False
    assert result.timed_out is True
    assert result.exit_code is None


def test_20_finish_repair_passes_summary(aci):
    result = aci.execute(
        FinishRepairAction(action="finish_repair", summary="已补上缺失依赖")
    )

    assert result.ok is True
    assert result.summary == "已补上缺失依赖"


def test_21_unknown_action(aci):
    class Unknown:
        action = "delete_everything"

    result = aci.execute(Unknown())

    assert result.ok is False
    assert result.summary == UNKNOWN_ACTION
