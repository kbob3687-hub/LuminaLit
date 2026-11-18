"""真沙箱端到端验证：本地建仓 → API 提交 → 真容器执行 → 校验结果与清理。

运行：.venv/Scripts/python scripts/e2e_docker.py

repo_url 安全契约只放行 https://，本地路径会被 API 422 拒绝（脚本先断言这点），
真闭环用注入 cloner 把本地仓库复制进 workspace，跳过网络 clone。
"""

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fastapi.testclient import TestClient

from paperharness.agent.fake import FakeLLM
from paperharness.app.api import create_app
from paperharness.harness.runner import HarnessRunner, _rmtree_force
from paperharness.harness.store import RunStore
from paperharness.sandbox.docker_sandbox import create_docker_sandbox


def make_repo() -> str:
    repo = ROOT / "runs-test" / "e2e-repo"
    if repo.exists():
        _rmtree_force(repo)
    repo.mkdir(parents=True)
    (repo / "main.py").write_text(
        "from pathlib import Path\n"
        "Path('out.txt').write_text('ok')\n"
        "print('hello e2e')\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"],
        cwd=repo,
        check=True,
    )
    return str(repo.resolve())


def main() -> None:
    repo = make_repo()
    runs_root = ROOT / "runs-test" / "e2e-runs"
    if runs_root.exists():
        _rmtree_force(runs_root)

    def local_repo_cloner(request, run_dir):
        """repo_url 只允许 https://，本地仓通过注入 cloner 复制进 workspace。"""
        workspace = run_dir / "workspace"
        shutil.copytree(repo, workspace, dirs_exist_ok=True)
        head = subprocess.run(
            ["git", "-C", str(workspace), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True, timeout=30,
        )
        return head.stdout.strip()

    store = RunStore(runs_root)
    runner = HarnessRunner(
        store=store,
        llm=FakeLLM(),
        sandbox_factory=create_docker_sandbox,
        cloner=local_repo_cloner,
    )
    app, _worker = create_app(store=store, runner=runner)
    with TestClient(app) as client:  # with 进入 lifespan，worker 自动启动
        # 安全回归：本地路径必须被 422 拒绝，不能钻 git clone 本地源的洞
        rejected = client.post(
            "/api/runs",
            json={"repo_url": repo, "command": ["python", "main.py"]},
        )
        assert rejected.status_code == 422, rejected.text
        print(f"local path rejected as expected: {rejected.status_code}")

        response = client.post(
            "/api/runs",
            json={
                "repo_url": "https://github.com/paperharness-demo/e2e-local",
                "command": ["python", "main.py"],
                "expected_artifact": "out.txt",
                "timeout_seconds": 120,
            },
        )
        assert response.status_code == 202, response.text
        run_id = response.json()["run_id"]
        print(f"submitted run_id={run_id}")

        deadline = time.time() + 180
        state = {"state": "queued", "result": None}
        while time.time() < deadline:
            state = client.get(f"/api/runs/{run_id}").json()
            if state["state"] in {"succeeded", "failed", "cancelled"}:
                break
            time.sleep(1)

        print("state:", state["state"])
        print("result:", state["result"])
        for event in client.get(f"/api/runs/{run_id}/events?after=0").json():
            print(f"  {event['sequence']} {event['state']} {event['message']}")
        assert state["state"] == "succeeded", f"expected succeeded, got {state}"

    import docker

    client = docker.from_env()
    leftovers = client.containers.list(
        all=True, filters={"label": f"paperharness.run_id={run_id}"}
    )
    assert not leftovers, f"leftover containers: {[c.name for c in leftovers]}"
    patch = (runs_root / run_id / "patch.diff").read_text(encoding="utf-8")
    assert "out.txt" in patch, "patch should contain the artifact"
    environment = json.loads(
        (runs_root / run_id / "environment.json").read_text(encoding="utf-8")
    )
    assert environment.get("cleanup_failures", 0) == 0, environment
    print("E2E OK: 真沙箱闭环通过，无残留、清理零失败")


if __name__ == "__main__":
    main()
