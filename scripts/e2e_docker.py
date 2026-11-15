"""真沙箱端到端验证：本地建仓 → API 提交 → 真容器执行 → 校验结果与清理。

运行：.venv/Scripts/python scripts/e2e_docker.py
"""

import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fastapi.testclient import TestClient

from paperharness.app.main import build_app


def make_repo() -> str:
    repo = ROOT / "runs-test" / "e2e-repo"
    if repo.exists():
        shutil.rmtree(repo)
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
    repo_url = make_repo()
    runs_root = ROOT / "runs-test" / "e2e-runs"
    if runs_root.exists():
        shutil.rmtree(runs_root)

    app, _worker = build_app(runs_root=runs_root, backend="docker")
    with TestClient(app) as client:  # with 进入 lifespan，worker 自动启动
        response = client.post(
            "/api/runs",
            json={
                "repo_url": repo_url,
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
    print("E2E OK: 真沙箱闭环通过，无残留")


if __name__ == "__main__":
    main()
