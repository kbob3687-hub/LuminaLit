"""沙箱安全测试（契约第 3 节 14 条清单）。

**只加用例，不改实现。** 发现 `docker_sandbox.py` 的问题走 issue / PR 评论给队长。

契约 :134-147 的 checkbox 实际是 **14 条**；onboarding 与本文件旧版写的「13 条」
是把早期 13 个测试函数当成了清单条目数，已订正。第 13/14 条
（install_requirement 注入、set_environment 白名单）属 ACI 层，动作到不了
沙箱，用 FakeSandboxSession 离线验证，不必陪 Docker 用例一起 skip。

## 怎么跑

需要真实 Docker 引擎 + 已构建的基础镜像：

```
docker build -t paperharness/base-py3.10 -f docker/base-py3.10.Dockerfile docker/
pytest tests/integration/test_docker_sandbox.py -v
```

## 没有 Docker 时的行为

需要起容器 / 查 `docker ps` 的用例由 autouse fixture 统一 `skip`（不是 `xfail`）：
`xfail` 会让人误以为跑过了只是没过，`skip` 才诚实表达「当前环境不具备验证条件」。
`_NO_DOCKER_TESTS` 里的纯 ACI 用例不需要 Docker，无 Docker 环境也会真跑。
skip 理由写明缺什么，拿到云服务器后直接 `pytest` 就能补跑，不需要改代码。

## 断言原则

每条都断言**可观测的事实**（退出码、`docker inspect` 的 Mounts/Networks、
`docker ps` 输出、异常类型），不断言实现细节，也不写「目标文件从未创建」这类
恒通过判据。这样队长改实现时测试不会误报，但行为退化一定会被抓住。
"""

from __future__ import annotations

import contextlib
import subprocess
import uuid
from pathlib import Path

import pytest

from paperharness.sandbox.base import SandboxClosedError, SandboxUnavailableError

IMAGE = "paperharness/base-py3.10"


# --------------------------------------------------------------------- 夹具


def _docker_cli(*args: str, timeout: int = 20) -> subprocess.CompletedProcess:
    """统一的 docker CLI 调用。

    显式 `check=False`：我们要自己看 returncode 决定语义（探针要区分"引擎挂了"和
    "镜像缺了"），异常驱动的写法会让这类分支变成 try/except 套娃。
    """
    return subprocess.run(
        ["docker", *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _docker_available() -> tuple[bool, str]:
    """返回 (可用?, 原因)。原因会原样进 skip 信息，方便事后定位。"""
    try:
        probe = _docker_cli("version", "--format", "{{.Server.Version}}")
    except FileNotFoundError:
        return False, "docker CLI 不在 PATH（本机未安装 Docker）"
    except subprocess.TimeoutExpired:
        return False, "docker version 超时（引擎未响应）"

    if probe.returncode != 0:
        detail = (probe.stderr or probe.stdout).strip().splitlines()
        tail = detail[-1] if detail else "unknown"
        return False, f"docker 引擎不可用：{tail}"

    image = _docker_cli("image", "inspect", IMAGE)
    if image.returncode != 0:
        return False, (
            f"基础镜像 {IMAGE} 不存在，先执行：\n"
            f"  docker build -t {IMAGE} -f docker/base-py3.10.Dockerfile docker/"
        )
    return True, ""


_DOCKER_OK, _DOCKER_REASON = _docker_available()

# 纯 ACI 层的用例（动作在到沙箱之前就被拦下）不需要真 Docker，
# 不该被模块级 skipif 连带跳过。名字与下方测试函数一一对应。
_NO_DOCKER_TESTS = frozenset(
    {
        "test_13_aci_rejects_command_injection_in_requirement",
        "test_14_aci_rejects_path_env_override",
        "test_aci_install_respects_runner_step_timeout",
        "test_aci_install_rejects_pep508_marker_semicolon",
        "test_aci_read_file_line_count_upper_bound",
        "test_aci_search_text_path_escape",
        "test_aci_search_text_max_results_guard",
    }
)


@pytest.fixture(autouse=True)
def _require_real_docker(request):
    """模块级 skipif 会把离线用例一起跳过，改用 autouse fixture 做名字豁免。

    不注册自定义 marker：pyproject.toml 未登记 markers，加 marker 会触发
    PytestUnknownMarkWarning，而本 PR 只允许改这一个文件。

    豁免判据是「显式名单 or `test_aci_` 前缀」。加前缀是因为名单式豁免
    会让新增的离线用例**静默变成 skip**：用例看着绿，其实一次没跑过
    （本 PR 加验收链用例时就踩到了）。ACI 层用例不碰 Docker，统一用前缀
    自证身份，以后再加不用回头改名单。
    """
    if _DOCKER_OK:
        return
    if request.node.name in _NO_DOCKER_TESTS or request.node.name.startswith("test_aci_"):
        return
    pytest.skip(f"需要真实 Docker 环境：{_DOCKER_REASON}")


@pytest.fixture
def run_dir(tmp_path) -> Path:
    """每个用例独立的 run 目录，name 即 run_id（契约 1.2）。"""
    directory = tmp_path / uuid.uuid4().hex
    workspace = directory / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "main.py").write_text("print('hello')\n", encoding="utf-8")
    return directory


@pytest.fixture
def session(run_dir):
    from paperharness.sandbox.docker_sandbox import DockerSandboxSession

    instance = DockerSandboxSession(run_dir=run_dir, python_version="3.10")
    try:
        yield instance
    finally:
        # 清理失败不能盖住用例本身的断言结果，但要让人看得见
        with contextlib.suppress(Exception):
            instance.close()


def _run_id_of(session) -> str:
    return session.run_id


def _containers_by_label(run_id: str) -> list[str]:
    """按契约 1.2 的 label 过滤查容器残留——这是清单里写死的判据。"""
    result = _docker_cli(
        "ps", "-a",
        "--filter", f"label=paperharness.run_id={run_id}",
        "--format", "{{.Names}}",
        timeout=30,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def _networks_by_label(run_id: str) -> list[str]:
    result = _docker_cli(
        "network", "ls",
        "--filter", f"label=paperharness.run_id={run_id}",
        "--format", "{{.Name}}",
        timeout=30,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def _container_ids_by_label(run_id: str) -> list[str]:
    """按 label 查容器 ID：判断「没有起第二个同名容器」与「两次不复用容器」。"""
    result = _docker_cli(
        "ps", "-a",
        "--filter", f"label=paperharness.run_id={run_id}",
        "--format", "{{.ID}}",
        timeout=30,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def _network_names_of(session) -> list[str]:
    """`docker inspect` 看容器实际连着的网络，不依赖公网。

    旧判据连 1.1.1.1:443：评测环境本就无外网时，「连不通」恒真，
    无法区分 `set_network(False)` 生效还是环境没网；恢复联网那条则相反，
    会恒失败。容器创建时 `network=self._network.name`（独立 bridge），
    disconnect 后 Networks 应为空，connect 后重新出现。
    """
    import json

    inspect = _docker_cli("inspect", session._container_name, timeout=30)
    assert inspect.returncode == 0, inspect.stderr
    data = json.loads(inspect.stdout)[0]
    return list(data["NetworkSettings"]["Networks"].keys())


# ------------------------------------------------------- 1. 非 root / 无 docker.sock


def test_01_container_process_is_not_root(session):
    """清单第 1 条：容器内进程用户不是 root。

    判据用 `id -u` 的实际输出，不是看配置字段——配置对但没生效的情况真实存在。
    """
    result = session.execute(["id", "-u"], timeout_seconds=30)

    assert result.exit_code == 0
    assert result.stdout_tail.strip() == "1000", (
        f"容器内 uid 应为 1000（契约 1.3 CONTAINER_USER），实际 {result.stdout_tail.strip()!r}"
    )


def test_02_cannot_see_docker_socket(session):
    """清单第 2 条：容器内看不到 /var/run/docker.sock。

    能拿到 docker.sock 等于拿到宿主机 docker 控制权，容器隔离当场失效。
    """
    socket_check = session.execute(["test", "-e", "/var/run/docker.sock"], timeout_seconds=30)
    listing = session.execute(["ls", "-la", "/var/run/"], timeout_seconds=30)

    assert socket_check.exit_code != 0, "容器内存在 /var/run/docker.sock"
    assert "docker.sock" not in listing.stdout_tail


# ---------------------------------------------------- 3. workspace 外的宿主文件


def test_03_cannot_read_host_files_outside_workspace(session):
    """清单第 3 条：容器内读不到 workspace 之外的宿主机文件。

    旧判据是 `cat /etc/paperharness-leak`（该文件从未创建）和
    `"LuminaLit" not in ls -a /`，两条在任何正常容器里都恒真，
    隔离失效也照样通过。改断言 `docker inspect` 的 Mounts：
    只挂 workspace 一个目录，且没有挂 docker.sock 或任何其它宿主路径。
    """
    import json

    marker = "host-only-marker-7f3a"
    (session.workspace / "marker.txt").write_text(marker, encoding="utf-8")

    inside = session.execute(["cat", "/workspace/marker.txt"], timeout_seconds=30)
    assert inside.exit_code == 0
    assert marker in inside.stdout_tail, "workspace 没有正确挂载进容器"

    inspect = _docker_cli("inspect", session._container_name, timeout=30)
    assert inspect.returncode == 0, inspect.stderr
    mounts = json.loads(inspect.stdout)[0]["Mounts"]
    assert len(mounts) == 1, f"预期仅挂载 workspace 一个目录，实际 {mounts}"
    assert mounts[0]["Destination"] == "/workspace"
    assert Path(mounts[0]["Source"]).resolve() == session.workspace.resolve()
    assert all("docker.sock" not in m.get("Source", "") for m in mounts)


# --------------------------------------------------------------- 4/5. ACI 拦路径


def test_04_aci_rejects_parent_traversal(session):
    """清单第 4 条：ACI 拒绝 `../` 路径穿越。"""
    from paperharness.aci import PaperACI
    from paperharness.contracts.run import ReadFileAction, RunPlan

    plan = RunPlan(
        resolved_revision="0" * 40,
        python_version="3.10",
        command=["python", "main.py"],
        source="user",
    )
    aci = PaperACI(session, session.workspace, plan, timeout_seconds=60)

    observation = aci.execute(ReadFileAction(action="read_file", path="../../etc/passwd"))

    assert observation.ok is False
    assert observation.summary == "path_escape:../../etc/passwd"


def test_05_aci_rejects_symlink_escaping_workspace(session):
    """清单第 5 条：ACI 拒绝指向 workspace 外的符号链接。

    在宿主机上造一个越界链接再让 ACI 解析——容器内建链接需要 root，宿主机更贴近真实攻击面。
    """
    import os

    from paperharness.aci import PaperACI
    from paperharness.contracts.run import ReadFileAction, RunPlan

    outside = session.run_dir / "outside-secret.txt"
    outside.write_text("should-not-be-readable", encoding="utf-8")
    link = session.workspace / "leak.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("当前宿主机无法创建符号链接（Windows 需管理员或开发者模式）")

    plan = RunPlan(
        resolved_revision="0" * 40,
        python_version="3.10",
        command=["python", "main.py"],
        source="user",
    )
    aci = PaperACI(session, session.workspace, plan, timeout_seconds=60)

    observation = aci.execute(ReadFileAction(action="read_file", path="leak.txt"))

    assert observation.ok is False
    assert observation.summary.startswith("path_escape:")
    assert os.path.exists(outside)  # 链接没被"顺手删掉"，只是拒绝读


# ------------------------------------------------------------ 6. 断网真的生效


def test_06_network_disabled_blocks_outbound(session):
    """清单第 6 条：`set_network(False)` 后容器脱离独立网络。

    判据用 `docker inspect` 的 Networks，不再连 1.1.1.1:443：
    评测环境本就无外网时，旧判据「连不通」恒真，证不了断网生效。
    """
    assert _network_names_of(session), "前置条件：容器创建时应连着独立网络"
    session.set_network(False)
    assert _network_names_of(session) == [], (
        "set_network(False) 后容器仍连着网络，隔离失效"
    )


def test_06b_network_can_be_reenabled(session):
    """`set_network(True)` 后网络重新连上——证明开关是双向的，不是单向熔断。"""
    before = _network_names_of(session)
    assert before, "前置条件：容器创建时应连着独立网络"
    session.set_network(False)
    assert _network_names_of(session) == []
    session.set_network(True)
    assert _network_names_of(session) == before, "重新开网后独立网络没恢复"


# -------------------------------------------------------- 7. 超时 kill 与会话关闭


def test_07_timeout_kills_container_and_closes_session(session):
    """清单第 7 条：超时后容器被 kill、`timed_out=True`、再 execute 抛 SandboxClosedError。"""
    result = session.execute(["sleep", "120"], timeout_seconds=3)

    assert result.timed_out is True
    assert result.exit_code is None
    # 两条日志路径在超时提前返回时也必须存在（契约 1.2）
    assert (session.run_dir / result.stdout_path).exists()
    assert (session.run_dir / result.stderr_path).exists()

    with pytest.raises(SandboxClosedError):
        session.execute(["echo", "after-timeout"], timeout_seconds=10)


# ------------------------------------------------------------- 8/9/10. 清理无残留


def test_08_close_leaves_no_container_or_network(session, run_dir):
    """清单第 8 条：`close()` 后按 label 过滤查不到容器和网络。"""
    run_id = _run_id_of(session)
    assert _containers_by_label(run_id), "前置条件：容器应当存在"

    session.close()

    assert _containers_by_label(run_id) == []
    assert _networks_by_label(run_id) == []
    assert session.cleanup_errors == []


def test_09_abnormal_path_still_cleans_up(tmp_path):
    """清单第 9 条：中途抛异常的路径也执行了清理，按 label 查不到残留。

    构造方式：镜像不存在 → 构造期抛 SandboxUnavailableError。
    容器没建成，但网络可能已经建了，所以必须验证网络也没留。
    """
    from paperharness.sandbox.docker_sandbox import DockerSandboxSession

    directory = tmp_path / ("missing-image-" + uuid.uuid4().hex[:12])
    (directory / "workspace").mkdir(parents=True)

    with pytest.raises(SandboxUnavailableError):
        DockerSandboxSession(
            run_dir=directory, python_version="9.9"  # 不存在的镜像版本
        )

    run_id = directory.name
    assert _containers_by_label(run_id) == []
    assert _networks_by_label(run_id) == [], (
        "构造失败时网络残留了——异常路径没有走清理"
    )


def test_09b_timeout_then_close_leaves_no_residue(session):
    """清单第 9 条点名的「execute 超时」异常路径：close 后按 label 查不到残留。

    `execute` 超时只 kill 不 remove（容器要等 `close()` 才删），所以必须显式
    close 之后再查，不能断言「超时瞬间即无容器」。`close()` 只看
    `_cleaned_up`，超时后仍可正常清理。
    """
    run_id = _run_id_of(session)
    result = session.execute(["sleep", "120"], timeout_seconds=3)
    assert result.timed_out is True
    assert result.exit_code is None

    with pytest.raises(SandboxClosedError):
        session.execute(["echo", "after-timeout"], timeout_seconds=10)

    session.close()

    assert _containers_by_label(run_id) == []
    assert _networks_by_label(run_id) == []
    assert session.cleanup_errors == []


def test_10_same_container_name_residue_is_handled(tmp_path):
    """清单第 10 条：同名容器残留场景，再次创建能成功或抛 SandboxUnavailableError，
    不允许起第二个同名容器。

    旧断言 `second._container_name == leftover_name` 比较的是同一个 run_id
    推导出的名字，必然相等；改按 label 查实际容器/网络数量。
    """
    from paperharness.sandbox.docker_sandbox import DockerSandboxSession

    directory = tmp_path / uuid.uuid4().hex
    workspace = directory / "workspace"
    workspace.mkdir(parents=True)

    first = DockerSandboxSession(run_dir=directory, python_version="3.10")
    try:
        # 绕过 close，模拟上次未正常清理就留下容器
        second = None
        try:
            second = DockerSandboxSession(run_dir=directory, python_version="3.10")
            assert len(_containers_by_label(directory.name)) == 1, (
                "起了第二个同名容器"
            )
            assert len(_networks_by_label(directory.name)) == 1, (
                "起了第二个同名网络"
            )
        except SandboxUnavailableError:
            pass  # 明确拒绝也算合格行为
        finally:
            if second is not None:
                second.close()
    finally:
        first.close()

    assert _containers_by_label(directory.name) == []


# ----------------------------------------------------------- 11. 两次运行互不污染


def test_11_second_run_does_not_reuse_first_container(tmp_path):
    """清单第 11 条：连续两次运行同一靶场，第二次不复用第一次的容器。

    旧测试自己把 main.py 写回 original 再断言读到 original，是循环论证：
    `cat` 能跑就通过，跟沙箱行为无关。契约 1.1 明确 workspace 重建是 Harness
    职责，沙箱层能验证的是「每次新起容器、可写层不串味」，故改判据为
    容器 ID 不同 + 任一时刻按 label 只有 1 个容器。
    「第二次看不到第一次的代码修改」的完整语义需在 Harness/worker 层验证，
    归属错位已写进 PR 待队长确认，不在这里伪造与实现不符的断言。
    """
    from paperharness.sandbox.docker_sandbox import DockerSandboxSession

    directory = tmp_path / uuid.uuid4().hex
    workspace = directory / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "main.py").write_text("print('original')\n", encoding="utf-8")

    first = DockerSandboxSession(run_dir=directory, python_version="3.10")
    try:
        first_ids = _container_ids_by_label(directory.name)
        assert len(first_ids) == 1, "前置条件：第一次运行应恰好一个容器"
        first.execute(
            ["python", "-c", "open('/workspace/main.py','w').write(\"print('hacked')\\n\")"],
            timeout_seconds=30,
        )
    finally:
        first.close()

    second = DockerSandboxSession(run_dir=directory, python_version="3.10")
    try:
        second_ids = _container_ids_by_label(directory.name)
        assert len(second_ids) == 1, "第二次运行期间按 label 应恰好一个容器"
        assert second_ids[0] != first_ids[0], (
            "第二次复用了第一次的容器（可写层会串味）"
        )
    finally:
        second.close()

    assert _containers_by_label(directory.name) == []


# ------------------------------------------------------------ 12. 资源限制生效


def test_12_container_resource_limits_are_applied(session):
    """清单第 12 条：`docker inspect` 确认内存、pids、CapDrop、no-new-privileges 生效。"""
    inspect = _docker_cli("inspect", session._container_name, timeout=30)
    assert inspect.returncode == 0, inspect.stderr

    import json

    data = json.loads(inspect.stdout)[0]
    host = data["HostConfig"]

    assert host["Memory"] == 2 * 1024**3, f"内存限制应为 2g，实际 {host['Memory']}"
    assert host["MemorySwap"] == 2 * 1024**3, "swap 应与内存相同（禁用 swap）"
    assert host["PidsLimit"] == 128, f"pids 限制应为 128，实际 {host['PidsLimit']}"
    assert host["NanoCpus"] == 1_500_000_000, f"CPU 限制不符：{host['NanoCpus']}"
    assert "ALL" in host["CapDrop"], f"必须 CapDrop ALL，实际 {host['CapDrop']}"
    assert "no-new-privileges:true" in host["SecurityOpt"]
    assert data["Config"]["User"] == "1000:1000"


# ------------------------------------------- 13/14. ACI 层离线用例（不需要 Docker）


def _offline_aci(tmp_path, timeout_seconds: int = 60):
    """构造不依赖 Docker 的 ACI：清单 13/14 与 ACI 契约边界共用。

    这两条的动作在 `PaperACI` 就被拦下，从不调用 `session.execute`，
    用 FakeSandboxSession 即可真跑；不该因为本机没有 Docker 就被 skip 掉。
    """
    from paperharness.aci import PaperACI
    from paperharness.contracts.run import RunPlan
    from paperharness.sandbox.fake import FakeSandboxSession

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    fake = FakeSandboxSession(workspace=workspace)
    plan = RunPlan(
        resolved_revision="0" * 40,
        python_version="3.10",
        command=["python", "main.py"],
        source="user",
    )
    return PaperACI(fake, workspace, plan, timeout_seconds), fake, workspace


def test_13_aci_rejects_command_injection_in_requirement(tmp_path):
    """清单第 13 条：install_requirement 拒绝 `numpy; rm -rf /` 和 URL 形式。"""
    from paperharness.contracts.run import InstallRequirementAction

    aci, fake, _ = _offline_aci(tmp_path)
    for payload in ("numpy; rm -rf /", "numpy @ https://evil.example/x.tar.gz"):
        observation = aci.execute(
            InstallRequirementAction(action="install_requirement", requirement=payload)
        )
        assert observation.ok is False
        assert observation.summary == "requirement_not_allowed"
    assert fake.commands == [], "校验失败不应发任何命令"
    assert fake.network_state is True, "校验失败不应开网"


def test_14_aci_rejects_path_env_override(tmp_path):
    """清单第 14 条：set_environment 拒绝 PATH 覆盖（白名单在 ACI 层）。

    不能对 `DockerSandboxSession.set_environment` 断言拒绝 PATH：
    真沙箱只拒换行，白名单由 ACI 负责（契约 1.2）。
    """
    from paperharness.contracts.run import SetEnvironmentAction

    aci, fake, _ = _offline_aci(tmp_path)
    observation = aci.execute(
        SetEnvironmentAction(action="set_environment", name="PATH", value="/tmp/evil")
    )
    assert observation.ok is False
    assert observation.summary == "env_not_allowed:PATH"
    assert fake.environment == {}


# ------------------------------------------------- ACI 契约边界（离线防回归）


def test_aci_install_respects_runner_step_timeout(tmp_path):
    """runner 每步把 aci.timeout_seconds 压成剩余墙钟预算，install 不得抬高。

    旧实现 `max(step_timeout, 120)` 会让临近 deadline 的 pip 跑满 120s，
    绕过总墙钟，令 `time_budget_exceeded` 收敛失效。
    """
    from paperharness.contracts.run import InstallRequirementAction
    from paperharness.sandbox.fake import FakeSandboxSession

    aci, _, workspace = _offline_aci(tmp_path, timeout_seconds=30)

    class TrackingSession(FakeSandboxSession):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.timeouts: list[int] = []

        def execute(self, command, timeout_seconds):
            self.timeouts.append(timeout_seconds)
            return super().execute(command, timeout_seconds)

    tracking = TrackingSession(workspace=workspace)
    aci.session = tracking
    aci.execute(InstallRequirementAction(action="install_requirement", requirement="numpy"))
    assert tracking.timeouts == [30], f"install 应透传 30，实际 {tracking.timeouts}"

    aci.timeout_seconds = 5  # 模拟 runner 把预算压成剩余墙钟
    aci.execute(InstallRequirementAction(action="install_requirement", requirement="numpy"))
    assert tracking.timeouts == [30, 5]


def test_aci_install_rejects_pep508_marker_semicolon(tmp_path):
    """契约 §2.2：ACI 层拒分号；§1.5 的 requirements.txt 才放行 PEP 508 标记。

    钉死这个口径差，防止把文件层的放行误套到动作层（`;` 是命令注入载体）。
    旧用例只用 `numpy; rm -rf /`，它同时也是非法 Requirement，
    即使有人删掉禁词表，测试照样绿。
    """
    from paperharness.contracts.run import InstallRequirementAction

    aci, fake, _ = _offline_aci(tmp_path)
    for payload in (
        "numpy>=1.20; python_version<'3.12'",
        "torch==2.2; sys_platform=='linux'",
    ):
        observation = aci.execute(
            InstallRequirementAction(action="install_requirement", requirement=payload)
        )
        assert observation.ok is False
        assert observation.summary == "requirement_not_allowed"
    assert fake.commands == []


def test_aci_read_file_line_count_upper_bound(tmp_path):
    """契约 §2.2 唯一写死的数值上界 line_count<=200，Pydantic 与 ACI 双层都要挡。"""
    from pydantic import ValidationError

    from paperharness.contracts.run import ReadFileAction

    aci, _, workspace = _offline_aci(tmp_path)
    (workspace / "main.py").write_text("hello\n", encoding="utf-8")

    with pytest.raises(ValidationError):
        ReadFileAction(action="read_file", path="main.py", line_count=201)

    # 绕过 schema 后 ACI 自己的上界分支也必须拒（此前是死代码、从未触达）
    bypass = ReadFileAction.model_construct(
        action="read_file", path="main.py", start_line=1, line_count=201
    )
    observation = aci.execute(bypass)
    assert observation.ok is False
    assert observation.summary == "invalid_argument:line_count"


def test_aci_search_text_path_escape(tmp_path):
    """§2.1「所有 path 参数」：search_text 的越界此前没有用例钉住。"""
    from paperharness.contracts.run import SearchTextAction

    aci, _, _ = _offline_aci(tmp_path)
    observation = aci.execute(
        SearchTextAction(action="search_text", query="x", path="../outside")
    )
    assert observation.ok is False
    assert observation.summary == "path_escape:../outside"


def test_aci_search_text_max_results_guard(tmp_path):
    """ACI 兜住 max_results 下界，避免 0 条时恒返回 "0 matches"。"""
    from paperharness.contracts.run import SearchTextAction

    aci, _, workspace = _offline_aci(tmp_path)
    (workspace / "main.py").write_text("hello\n", encoding="utf-8")
    for bad in (0, -3):
        observation = aci.execute(
            SearchTextAction(action="search_text", query="hello", path=".", max_results=bad)
        )
        assert observation.ok is False
        assert observation.summary == "invalid_argument:max_results"
    good = aci.execute(
        SearchTextAction(action="search_text", query="hello", path=".", max_results=50)
    )
    assert good.ok is True


# ----------------------------- ACI 验收链隔离：模型不能自己判卷（离线防 Reward Hacking）




# New-session replay regressions retained from PRs #38/#39.
def _live_containers(run_id: str, fmt: str) -> list[str]:
    """按 label 查运行中容器，返回 ID 或名称（只用 docker CLI 的可观测输出）。"""
    result = _docker_cli(
        "ps",
        "--filter", f"label=paperharness.run_id={run_id}",
        "--format", fmt,
        timeout=30,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _live_container_ids(run_id: str) -> list[str]:
    return _live_containers(run_id, "{{.ID}}")


def _live_container_names(run_id: str) -> list[str]:
    return _live_containers(run_id, "{{.Names}}")


def _fresh_run_dir(tmp_path: Path) -> Path:
    """复跑用的全新目录：与上一次运行没有任何共享路径。"""
    directory = tmp_path / uuid.uuid4().hex
    workspace = directory / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "main.py").write_text("print('original')\n", encoding="utf-8")
    return directory


def test_13_clean_replay_uses_new_directory_container_and_empty_artifacts(tmp_path):
    """复跑不能看到旧容器的可写层标记、旧产物，且必须使用不同的容器。"""
    from paperharness.sandbox.docker_sandbox import DockerSandboxSession

    first_dir = _fresh_run_dir(tmp_path)
    first = DockerSandboxSession(run_dir=first_dir, python_version="3.10")
    first_id = _run_id_of(first)
    try:
        seeded = first.execute(
            ["sh", "-c",
             ("printf 'hacked' > /workspace/main.py; "
              "mkdir -p /workspace/reports; printf 'stale' > /workspace/reports/out.txt; "
              ": > /tmp/replay-writable-layer-marker")],
            timeout_seconds=30,
        )
        assert seeded.exit_code == 0, seeded.stderr_tail
        first_ids = _live_container_ids(first_id)
        assert first_ids, "首轮会话没有运行中的容器，先检查沙箱构造"
    finally:
        first.close()

    second_dir = _fresh_run_dir(tmp_path)
    assert second_dir != first_dir, "复跑必须使用新的工作目录"
    second = DockerSandboxSession(run_dir=second_dir, python_version="3.10")
    second_id = _run_id_of(second)
    try:
        second_ids = _live_container_ids(second_id)
        assert second_ids, "复跑会话没有运行中的容器"
        assert set(first_ids).isdisjoint(second_ids), "复跑复用了旧容器实例（容器 ID 相同）"
        assert second_id != first_id

        marker = second.execute(["test", "-e", "/tmp/replay-writable-layer-marker"], timeout_seconds=30)
        assert marker.exit_code != 0, "旧容器的可写层标记泄漏到了复跑会话"

        stale = second.execute(["test", "-e", "/workspace/reports/out.txt"], timeout_seconds=30)
        assert stale.exit_code != 0, "预期产物位置未清空，陈旧产物可能被当作本轮证据"

        restored = second.execute(["cat", "/workspace/main.py"], timeout_seconds=30)
        assert restored.exit_code == 0 and "original" in restored.stdout_tail
        assert "hacked" not in restored.stdout_tail
    finally:
        second.close()

    for run_id in (first_id, second_id):
        assert _containers_by_label(run_id) == [], f"{run_id} 容器残留"
        assert _networks_by_label(run_id) == [], f"{run_id} 网络残留"


def test_14_same_run_dir_new_session_gets_fresh_container_instance(tmp_path):
    """同一 run 目录连续两次会话：容器名可以相同，但容器实例与可写层不得复用。

    容器名固定为 `paperharness-{run_dir.name}`，同目录必然同名——旧版断言
    「名字必须不同」与实现矛盾，会确定性失败。这里改为按 label 查实际容器 ID，
    保留 §8.6「不得复用之前容器的可写层」的业务断言。
    """
    from paperharness.sandbox.docker_sandbox import DockerSandboxSession

    directory = _fresh_run_dir(tmp_path)
    first = DockerSandboxSession(run_dir=directory, python_version="3.10")
    run_id = _run_id_of(first)
    try:
        seeded = first.execute(["sh", "-c", ": > /tmp/session-marker"], timeout_seconds=30)
        assert seeded.exit_code == 0, seeded.stderr_tail
        first_ids = _live_container_ids(run_id)
        first_names = _live_container_names(run_id)
        assert first_ids and first_names, "首轮会话没有运行中的容器"
    finally:
        first.close()

    second = DockerSandboxSession(run_dir=directory, python_version="3.10")
    try:
        second_ids = _live_container_ids(run_id)
        second_names = _live_container_names(run_id)
        assert second_ids, "第二轮会话没有运行中的容器"
        # 名称相同是允许且符合实现的；它不能用来判断容器是否被复用。
        assert second_names == first_names, "同一 run 目录的容器名按实现应保持固定"
        assert set(first_ids).isdisjoint(second_ids), "同一 run 目录的新会话复用了旧容器实例"
        marker = second.execute(["test", "-e", "/tmp/session-marker"], timeout_seconds=30)
        assert marker.exit_code != 0, "同一 run 目录的新会话继承了旧容器可写层"
    finally:
        second.close()

    assert _containers_by_label(run_id) == [], "会话关闭后不应残留容器"


def test_15_consecutive_sessions_in_same_dir_never_inherit_state(tmp_path):
    """同一目录连续三轮创建/关闭：名称固定、ID 两两不同、每轮都从零开始。"""
    from paperharness.sandbox.docker_sandbox import DockerSandboxSession

    directory = _fresh_run_dir(tmp_path)
    seen_ids: list[str] = []
    names: list[str] | None = None

    for round_no in range(1, 4):
        session = DockerSandboxSession(run_dir=directory, python_version="3.10")
        run_id = _run_id_of(session)
        try:
            ids = _live_container_ids(run_id)
            current_names = _live_container_names(run_id)
            assert ids and current_names, f"第 {round_no} 轮没有运行中的容器"
            if names is None:
                names = current_names
            else:
                assert current_names == names, f"第 {round_no} 轮容器名发生了变化"
            assert set(ids).isdisjoint(seen_ids), f"第 {round_no} 轮复用了此前的容器实例"

            marker = session.execute(["test", "-e", "/tmp/session-marker"], timeout_seconds=30)
            assert marker.exit_code != 0, f"第 {round_no} 轮继承了上一轮的容器可写层"
            seeded = session.execute(["sh", "-c", ": > /tmp/session-marker"], timeout_seconds=30)
            assert seeded.exit_code == 0, seeded.stderr_tail
            seen_ids.extend(ids)
        finally:
            session.close()
        assert _containers_by_label(run_id) == [], f"第 {round_no} 轮关闭后容器残留"

    assert len(set(seen_ids)) == 3, f"三轮会话应对应 3 个不同容器实例，实际 {seen_ids}"
