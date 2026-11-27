"""Real failure -> configured model repair -> independent verification -> clean replay.

Uses the maintained paper-lorenz-repair case, without injecting a broken source file.
Requires an explicit model configuration; never falls back to FakeLLM.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from accept_lorenz import NoModel
from fastapi.testclient import TestClient

from paperharness.aci import PaperACI
from paperharness.agent.llm_agent import OpenAICompatibleLLM
from paperharness.app.api import create_app
from paperharness.harness.runner import HarnessRunner
from paperharness.harness.store import RunStore
from paperharness.sandbox.docker_sandbox import create_docker_sandbox
from paperharness.targets import load_targets, public_view


class AuditedModel(OpenAICompatibleLLM):
    """Record actual successful JSON decisions, never authorization headers or keys."""

    def __init__(self, audit_path: Path):
        super().__init__(timeout_seconds=45)
        if not self.api_key:
            raise ValueError("An explicitly configured model API key is required")
        self.audit_path = audit_path
        self.decisions = []

    def _chat_json(self, system: str, user: str) -> dict:
        started = time.monotonic()
        result = super()._chat_json(system, user)
        self.decisions.append({
            "index": len(self.decisions) + 1, "model": self.model,
            "system": system, "input": user, "output": result,
            "elapsed_seconds": round(time.monotonic() - started, 3),
        })
        self.audit_path.write_text(
            json.dumps(self.decisions, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        return result


def load_model_settings(path: Path):
    """Read only three model settings; never evaluate shell or load unrelated secrets."""
    names = {f"PAPERHARNESS_LLM_{suffix}" for suffix in ("BASE_URL", "API_KEY", "MODEL")}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() in names:
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            os.environ.setdefault(key.strip(), value)


def execute(client, store, max_repairs, timeout):
    started = time.monotonic()
    preflight = client.post("/api/preflights", json={
        "target_id": "paper-lorenz-repair", "timeout_seconds": timeout,
        "max_repairs": max_repairs,
    })
    preflight.raise_for_status()
    frozen = preflight.json()
    response = client.post("/api/runs", json={
        "preflight_id": frozen["preflight_id"],
        "confirmation_digest": frozen["confirmation_digest"],
    })
    response.raise_for_status()
    run_id = response.json()["run_id"]
    print(f"run_id={run_id} max_repairs={max_repairs}", flush=True)
    sequence = 0
    while True:
        for event in store.read_events(run_id, after=sequence):
            sequence = event.sequence
            print(f"{sequence} {event.state} {event.kind}: {event.message[:180]}", flush=True)
        result = store.read_result(run_id)
        if result is not None:
            return result
        if time.monotonic() - started > timeout * 5 + 300:
            client.post(f"/api/runs/{run_id}/cancel")
            raise RuntimeError("acceptance polling deadline exceeded")
        time.sleep(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-root", type=Path, default=ROOT / "runs")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--baseline-run", help="Reuse an already recorded failed baseline run")
    parser.add_argument("--source-cache", type=Path, help="Previously cloned, clean upstream Git tree")
    args = parser.parse_args()
    if args.env_file:
        load_model_settings(args.env_file)
    store = RunStore(args.runs_root)
    target = next(t for t in load_targets(ROOT / "targets") if t.id == "paper-lorenz-repair")
    runner_options = {}
    if args.source_cache:
        cache = args.source_cache.resolve()
        revision = subprocess.check_output(
            ["git", "-C", str(cache), "rev-parse", "HEAD"], text=True, timeout=30,
        ).strip()
        dirty = subprocess.check_output(
            ["git", "-C", str(cache), "status", "--porcelain"], timeout=30,
        )
        origin = subprocess.check_output(
            ["git", "-C", str(cache), "remote", "get-url", "origin"], text=True, timeout=30,
        ).strip().removesuffix(".git")
        if revision != target.revision or dirty or origin != target.repo_url:
            raise ValueError("Source cache must be a clean clone of the exact upstream revision")

        def cached_clone(request, run_dir):
            if request.revision != revision:
                raise ValueError("Cached source revision mismatch")
            destination = run_dir / "workspace"
            if destination.exists() and any(destination.iterdir()):
                raise ValueError("Refusing to overwrite a populated workspace")
            shutil.copytree(cache, destination, dirs_exist_ok=True)
            return revision

        runner_options["cloner"] = cached_clone
    started = time.monotonic()
    if args.baseline_run:
        baseline = store.read_result(args.baseline_run)
        if baseline is None or baseline.resolved_revision != target.revision:
            raise ValueError("Baseline run is missing or uses a different source revision")
    else:
        baseline_runner = HarnessRunner(
            store=store, llm=NoModel(), sandbox_factory=create_docker_sandbox, targets=[target],
            **runner_options,
        )
        baseline_app, _ = create_app(
            store=store, runner=baseline_runner, targets=public_view([target]),
        )
        with TestClient(baseline_app) as client:
            baseline = execute(client, store, 0, args.timeout)
    logs = "\n".join(
        p.read_text(encoding="utf-8", errors="replace")
        for p in (store.run_dir(baseline.run_id) / "logs").glob("*.err")
    )
    # Actual source uses a removed sklearn private method. Do not treat a network
    # timeout, missing dependency or other incidental failure as our target bug.
    if baseline.outcome == "succeeded" or "_preprocess_data" not in logs:
        raise RuntimeError(f"Expected source compatibility failure not reproduced: {baseline.run_id}")
    audit_path = store.root / f"model-decisions-{uuid.uuid4().hex}.json"
    model = AuditedModel(audit_path)
    runner = HarnessRunner(
        store=store, llm=model, sandbox_factory=create_docker_sandbox,
        aci_factory=PaperACI, targets=[target],
        **runner_options,
    )
    app, _ = create_app(store=store, runner=runner, targets=public_view([target]))
    with TestClient(app) as client:
        repaired = execute(client, store, 2, args.timeout)
        if audit_path.exists():
            store.write_text(
                repaired.run_id, "model_decisions.json", audit_path.read_text(encoding="utf-8"),
            )
        assert model.decisions, "No successful model decision recorded"
        assert repaired.outcome == "succeeded", repaired
        assert repaired.verification.status == "passed", repaired
        assert repaired.replay.status == "passed", repaired
        assert repaired.bundle.integrity == "complete", repaired
        response = client.get(f"/api/runs/{repaired.run_id}/bundle")
        response.raise_for_status()
        assert hashlib.sha256(response.content).hexdigest() == repaired.bundle.sha256
    evidence = {
        "baseline_run_id": baseline.run_id, "repaired_run_id": repaired.run_id,
        "baseline_outcome": baseline.outcome, "source_revision": target.revision,
        "model": model.model, "successful_model_json_decisions": len(model.decisions),
        "result": repaired.model_dump(mode="json"),
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "cross_machine_handoff": "pending_windows_teammate",
        "source_transport": "validated_clean_local_git_cache" if args.source_cache else "https_git",
        "scope": "Maintained Lorenz subexperiment; original upstream source; same-host containers.",
    }
    store.write_text(repaired.run_id, "repair_acceptance.json", json.dumps(
        evidence, ensure_ascii=False, indent=2,
    ))
    print(json.dumps(evidence, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
