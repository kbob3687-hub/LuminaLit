"""首日沙箱 spike（契约 6.2）：创建 → execute → 断网 → close 无残留。

一次性脚本，不进 src/。运行：.venv/Scripts/python scripts/spike_sandbox.py
"""

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from paperharness.contracts.run import RunRequest
from paperharness.sandbox.docker_sandbox import create_docker_sandbox

RUN_DIR = Path("runs-test/spike-run")


def check(name: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}{('  ' + detail) if detail else ''}")
    if not ok:
        raise SystemExit(1)


def main() -> None:
    if RUN_DIR.exists():
        shutil.rmtree(RUN_DIR)
    (RUN_DIR / "workspace").mkdir(parents=True)
    # workspace 造一个最小 git 仓库，供 export_diff 用
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=RUN_DIR / "workspace", check=True)

    request = RunRequest(repo_url="https://example.com/spike", python_version="3.10")
    session = create_docker_sandbox(request, RUN_DIR)
    try:
        result = session.execute(["python", "-c", "print('hello from container')"], 30)
        check(
            "execute 跑通",
            result.exit_code == 0 and "hello" in result.stdout_tail,
            result.stdout_tail.strip(),
        )

        result = session.execute(["python", "-c", "import os; print(os.getuid())"], 30)
        check(
            "非 root（uid=1000）",
            result.exit_code == 0 and result.stdout_tail.strip() == "1000",
            result.stdout_tail.strip(),
        )

        session.set_environment("SPIKE_VAR", "42")
        result = session.execute(
            ["python", "-c", "import os; print(os.environ['SPIKE_VAR'])"], 30
        )
        check(
            "环境变量生效",
            result.exit_code == 0 and result.stdout_tail.strip() == "42",
            result.stdout_tail.strip(),
        )

        session.set_network(False)
        result = session.execute(
            [
                "python",
                "-c",
                (
                    "import socket;"
                    "socket.create_connection(('1.1.1.1', 443), timeout=3);"
                    "print('online')"
                ),
            ],
            30,
        )
        check("断网后外联失败", result.exit_code != 0, f"exit_code={result.exit_code}")

        session.set_network(True)
        result = session.execute(
            [
                "python",
                "-c",
                (
                    "import socket;"
                    "socket.create_connection(('1.1.1.1', 443), timeout=5);"
                    "print('online')"
                ),
            ],
            30,
        )
        check("恢复联网后外联成功", result.exit_code == 0, f"exit_code={result.exit_code}")

        (RUN_DIR / "workspace" / "spike.txt").write_text("spike change", encoding="utf-8")
        diff = session.export_diff()
        check("export_diff 含改动", "spike.txt" in diff, f"{len(diff)} chars")

        result = session.execute(["id", "-u"], 30)
        check("日志文件生成", (RUN_DIR / "logs" / f"exec-{session._exec_seq:03d}.out").exists())
    finally:
        session.close()

    import docker

    client = docker.from_env()
    containers = client.containers.list(
        all=True, filters={"label": "paperharness.run_id=spike-run"}
    )
    networks = client.networks.list(
        filters={"label": "paperharness.run_id=spike-run"}
    )
    check("close 后无残留容器", len(containers) == 0)
    check("close 后无残留网络", len(networks) == 0)

    print("\nspike 全部通过")


if __name__ == "__main__":
    main()
