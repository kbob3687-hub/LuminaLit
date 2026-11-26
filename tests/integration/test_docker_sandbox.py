"""沙箱安全测试（契约第 3 节 13 条清单）。

**只加用例，不改实现。** 发现 `docker_sandbox.py` 的问题走 issue / PR 评论给队长。

## 怎么跑

需要真实 Docker 引擎 + 已构建的基础镜像：

```
docker build -t paperharness/base-py3.10 -f docker/base-py3.Dockerfile docker/
pytest tests/integration/test_docker_sandbox.py -v
```

## 没有 Docker 时的行为

整个模块 `skip`，不是 `xfail`。区别很重要：

- `xfail` = 预期会失败，测试跑了但结果不符合预期
- `skip` = 根本没有跑

这 13 条是「必须真跑出日志才算证据」的用例。标 `xfail` 会让人误以为跑过了只是没过；
`skip` 才诚实地表达「当前环境不具备验证条件」。skip 理由会写明缺什么，
拿到云服务器后直接 `pytest` 就能补跑，不需要改代码。

## 断言原则

每条都断言**可观测的事实**（退出码、文件内容、`docker ps` 的输出、异常类型），
不断言实现细节。这样队长改实现时测试不会误报，但行为退化一定会被抓住。
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

pytestmark = pytest.mark.skipif(
    not _DOCKER_OK,
    reason=f"需要真实 Docker 环境：{_DOCKER_REASON}",
)


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

    容器只挂载了 workspace，宿主机的 /etc/hostname 之类不该出现在容器里。
    这里验证两点：容器里没有宿主机独有路径，且 workspace 内容是挂载进来的。
    """
    # 宿主机的 workspace 有一个只有我们知道的文件
    marker = "host-only-marker-7f3a"
    (session.workspace / "marker.txt").write_text(marker, encoding="utf-8")

    inside = session.execute(["cat", "/workspace/marker.txt"], timeout_seconds=30)
    assert inside.exit_code == 0
    assert marker in inside.stdout_tail, "workspace 没有正确挂载进容器"

    # 容器根目录下不该有宿主机项目的痕迹
    root_listing = session.execute(["ls", "-a", "/"], timeout_seconds=30)
    leaked = session.execute(["cat", "/etc/paperharness-leak"], timeout_seconds=30)

    assert root_listing.exit_code == 0
    assert "LuminaLit" not in root_listing.stdout_tail
    assert leaked.exit_code != 0


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
    """清单第 6 条：`set_network(False)` 后容器内 pip / curl 失败。

    这条是「断网」承诺的实际验证点，也是最容易被评委要求现场演示的一条。
    """
    session.set_network(False)

    # 用 Python 建 TCP 连接，不依赖 curl 是否装了
    probe = session.execute(
        [
            "python", "-c",
            ("import socket,sys\n"
            "socket.setdefaulttimeout(5)\n"
            "try:\n"
            "    socket.create_connection(('1.1.1.1', 443))\n"
            "    print('CONNECTED')\n"
            "except Exception as e:\n"
            "    print('BLOCKED', type(e).__name__)\n"),
        ],
        timeout_seconds=60,
    )

    assert probe.exit_code == 0  # 探针脚本本身要能跑
    assert "BLOCKED" in probe.stdout_tail, (
        f"断网后仍能对外连接，隔离失效：{probe.stdout_tail}"
    )
    assert "CONNECTED" not in probe.stdout_tail


def test_06b_network_can_be_reenabled(session):
    """`set_network(True)` 之后恢复了能力——证明开关是双向的，不是单向熔断。"""
    session.set_network(False)
    session.set_network(True)

    probe = session.execute(
        [
            "python", "-c",
            ("import socket\n"
            "socket.setdefaulttimeout(5)\n"
            "try:\n"
            "    socket.create_connection(('1.1.1.1', 443))\n"
            "    print('CONNECTED')\n"
            "except Exception as e:\n"
            "    print('BLOCKED', type(e).__name__)\n"),
        ],
        timeout_seconds=60,
    )

    assert "CONNECTED" in probe.stdout_tail, (
        f"重新开网后仍连不上，网络开关可能只单向生效：{probe.stdout_tail}"
    )


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


# ------------------------------------------------------------- 8/9/11. 清理无残留


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


def test_11_same_container_name_residue_is_handled(tmp_path):
    """清单第 11 条：同名容器残留场景，再次创建能成功或抛 SandboxUnavailableError，
    不允许起第二个同名容器。
    """
    from paperharness.sandbox.docker_sandbox import DockerSandboxSession

    directory = tmp_path / uuid.uuid4().hex
    workspace = directory / "workspace"
    workspace.mkdir(parents=True)

    first = DockerSandboxSession(run_dir=directory, python_version="3.10")
    try:
        # 绕过 close，模拟上次未正常清理就留下容器
        leftover_name = first._container_name
        second = None
        try:
            second = DockerSandboxSession(run_dir=directory, python_version="3.10")
            # 能起来就算通过：说明复用/清掉了旧容器，而不是起第二个同名的
            assert second._container_name == leftover_name
        except SandboxUnavailableError:
            pass  # 明确拒绝也算合格行为
        finally:
            if second is not None:
                second.close()
    finally:
        first.close()

    assert _containers_by_label(directory.name) == []


# ------------------------------------------------------------ 10. 两次运行互不污染


def test_10_second_run_does_not_see_first_run_changes(tmp_path):
    """清单第 10 条：连续两次运行同一靶场，第二次看不到第一次的代码修改。

    判据是 workspace 目录本身被重建/清空，而不是靠 git checkout——
    后者会因残留 .git 状态而出现隐蔽的串味。
    """
    from paperharness.sandbox.docker_sandbox import DockerSandboxSession

    directory = tmp_path / uuid.uuid4().hex
    workspace = directory / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "main.py").write_text("print('original')\n", encoding="utf-8")

    first = DockerSandboxSession(run_dir=directory, python_version="3.10")
    try:
        first.execute(
            ["python", "-c", "open('/workspace/main.py','w').write(\"print('hacked')\\n\")"],
            timeout_seconds=30,
        )
    finally:
        first.close()

    # 第二次运行前，Harness 会重建 workspace（契约 1.1）；这里手动模拟同样的前提
    (workspace / "main.py").write_text("print('original')\n", encoding="utf-8")

    second = DockerSandboxSession(run_dir=directory, python_version="3.10")
    try:
        observed = second.execute(["cat", "/workspace/main.py"], timeout_seconds=30)
    finally:
        second.close()

    assert observed.exit_code == 0
    assert "original" in observed.stdout_tail
    assert "hacked" not in observed.stdout_tail
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


# ------------------------------------------- 13/14/15. 干净复跑的会话隔离（R07 / §8.6）
#
# 评审稿 §8.6：复跑必须「创建新的工作目录和新的沙箱会话；不得复用之前容器的
# 可写层、已安装环境或输出目录」。下面两条只审**可观测的隔离事实**，不审
# 复跑调度、清单还原或指标一致性——那些属于 Harness/验证器实现，不在本文件
# 的目录归属内。发现实现问题走 issue / PR 评论给队长，不直接改 docker_sandbox.py。
#
# 容器名按实现固定为 `paperharness-{run_dir.name}`（docker_sandbox.py:65）：
# 同一 run 目录两次会话**必然同名**，所以「容器实例是否复用」只能比较实际
# 容器 ID / 名称列表，不能拿命令行里的名字字符串当判据（PR #38 review）。


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
