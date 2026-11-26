"""真实容器的修复清单 → 新环境复跑 → ZIP 下载闭环。

本地固定仓库、脚本化模型、解析系数夹具；验证工程闭环，不作为论文复现成绩。
运行：PAPERHARNESS_TEST_DOCKER=1 pytest tests/integration/test_bundle_docker.py -v
"""
import hashlib
import json
import os
import subprocess
import zipfile
from contextlib import closing
from io import BytesIO

import pytest
from fastapi.testclient import TestClient

from paperharness.aci.paper_aci import PaperACI
from paperharness.agent.fake import FakeLLM
from paperharness.app.api import create_app
from paperharness.contracts.preflight import PreflightRequest
from paperharness.contracts.run import FinishRepairAction, ReplaceExactAction
from paperharness.harness.preflight import PreflightService
from paperharness.harness.runner import HarnessRunner
from paperharness.harness.store import RunStore
from paperharness.sandbox.docker_sandbox import create_docker_sandbox
from paperharness.targets.manifest import TargetManifest

pytestmark = pytest.mark.skipif(
    os.environ.get("PAPERHARNESS_TEST_DOCKER") != "1",
    reason="set PAPERHARNESS_TEST_DOCKER=1 for the real replay/bundle chain",
)


@pytest.mark.parametrize("needs_repair", [False, True], ids=["unchanged", "scripted-repair"])
def test_real_docker_replays_frozen_material_and_downloads_bundle(tmp_path, needs_repair):
    docker = pytest.importorskip("docker")
    with closing(docker.from_env(timeout=5)) as client:
        client.ping()
        client.images.get("paperharness/base-py3.10")

    def git(directory, *args):
        return subprocess.run(
            ["git", "-C", str(directory), *args], check=True,
            capture_output=True, text=True, timeout=30,
        ).stdout.strip()

    source = tmp_path / "source"
    source.mkdir()
    (source / "model.py").write_bytes(
        f"COEFFICIENT = {'None' if needs_repair else '-2.0'}\n".encode(),
    )
    git(source, "init", "-q")
    git(source, "config", "core.autocrlf", "false")
    git(source, "add", ".")
    git(source, "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture")
    revision = git(source, "rev-parse", "HEAD")
    injection = tmp_path / "injection"
    injection.mkdir()
    (injection / "requirements.txt").write_text("", encoding="utf-8")
    (injection / "check.py").write_text(
        "import json, runpy\n"
        "coefficient = runpy.run_path('model.py')['COEFFICIENT']\n"
        "if coefficient is None: raise RuntimeError('coefficient is not configured')\n"
        "with open('coefficients.json', 'w') as f:\n"
        "    json.dump({'schema_version': 1, 'coefficients': [[0, coefficient, 0, 0, 0, 0], [0, 0, 1, 0, 0, 0]]}, f)\n",
        encoding="utf-8",
    )
    target = TargetManifest(
        id="docker-replay-fixture", repo_url="https://github.com/paperharness-demo/replay-fixture",
        revision=revision, python_version="3.10", command=["python", "-I", "check.py"],
        expected_artifact="coefficients.json", failure_class="none", repairable=True,
        allowed_actions=["replace_exact"], expected_final="succeeded",
        verification_rule="pysindy-linear-v1", source_dir=injection,
    )
    clone_paths, container_ids = [], []

    def cloner(request, run_dir):
        workspace = run_dir / "workspace"
        subprocess.run(["git", "-c", "core.autocrlf=false", "clone", "-q", "--no-hardlinks", str(source), str(workspace)], check=True, timeout=30)
        git(workspace, "checkout", "-q", request.revision)
        clone_paths.append(workspace)
        return git(workspace, "rev-parse", "HEAD")

    def factory(request, run_dir):
        session = create_docker_sandbox(request, run_dir)
        with closing(docker.from_env(timeout=5)) as client:
            containers = client.containers.list(filters={"label": f"paperharness.run_id={run_dir.name}"})
            assert len(containers) == 1
            container_ids.append(containers[0].id)
        return session

    model = FakeLLM(actions=[
        ReplaceExactAction(action="replace_exact", path="model.py", old="COEFFICIENT = None", new="COEFFICIENT = -2.0"),
        FinishRepairAction(action="finish_repair", summary="configure the fixed coefficient fixture"),
    ] if needs_repair else [])
    store = RunStore(tmp_path / "runs")
    runner = HarnessRunner(store=store, llm=model, sandbox_factory=factory, cloner=cloner, aci_factory=PaperACI, targets=[target])
    record = PreflightService(store, [target]).create(PreflightRequest(target_id=target.id, timeout_seconds=120, max_repairs=int(needs_repair)))
    run_id = store.create_run(record.request, confirmation=record.model_dump(mode="json"))
    runner.run(run_id, record.request)
    result = store.read_result(run_id)
    assert result.outcome == "succeeded", result.model_dump()
    assert result.verification.status == "passed"
    assert result.replay.status == "passed"
    assert result.replay.verification.validator_digest == result.verification.validator_digest
    assert len(clone_paths) == 2 and clone_paths[0] != clone_paths[1]
    assert len(container_ids) == 2 and len(set(container_ids)) == 2
    assert result.bundle.integrity == "complete"
    app, _ = create_app(store, runner)
    response = TestClient(app).get(f"/api/runs/{run_id}/bundle")
    assert response.status_code == 200
    assert hashlib.sha256(response.content).hexdigest() == result.bundle.sha256
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        manifest = json.loads(archive.read("repair_manifest.json"))
        model_changes = [f for f in manifest["files"] if f["path"] == "model.py"]
        assert bool(model_changes) is needs_repair
        assert "replay_result.json" in archive.namelist()
    with closing(docker.from_env(timeout=5)) as client:
        for label in [run_id, f"replay-{run_id}"]:
            assert not client.containers.list(all=True, filters={"label": f"paperharness.run_id={label}"})
