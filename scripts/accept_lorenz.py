"""Real Git + Docker + API acceptance, including frozen replay and negative control.

Run on a machine with the two local base images available:
    python scripts/accept_lorenz.py
No LLM is called. This validates the scientific execution chain, not AI repair.
"""

import argparse
import hashlib
import io
import json
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fastapi.testclient import TestClient

from paperharness.app.api import create_app
from paperharness.harness.runner import HarnessRunner
from paperharness.harness.store import RunStore
from paperharness.harness.verification import verification_spec, verify_artifact
from paperharness.sandbox.docker_sandbox import create_docker_sandbox
from paperharness.targets import load_targets, public_view


class NoModel:
    def __getattr__(self, name):
        raise AssertionError(f"This deterministic experiment must not call an agent: {name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-root", type=Path, default=ROOT / "runs")
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()
    store = RunStore(args.runs_root)
    targets = load_targets(ROOT / "targets")
    runner = HarnessRunner(
        store=store, llm=NoModel(), sandbox_factory=create_docker_sandbox, targets=targets,
    )
    app, _ = create_app(store=store, runner=runner, targets=public_view(targets))
    started = time.monotonic()
    with TestClient(app) as client:
        preflight = client.post("/api/preflights", json={
            "target_id": "paper-lorenz", "timeout_seconds": args.timeout, "max_repairs": 0,
        })
        preflight.raise_for_status()
        frozen = preflight.json()
        assert frozen["verification"]["rule_id"] == "sindy-lorenz-v1"
        response = client.post("/api/runs", json={
            "preflight_id": frozen["preflight_id"],
            "confirmation_digest": frozen["confirmation_digest"],
        })
        response.raise_for_status()
        run_id = response.json()["run_id"]
        print(f"run_id={run_id} runs_root={args.runs_root}", flush=True)
        last_sequence = 0
        while True:
            events = client.get(f"/api/runs/{run_id}/events", params={"after": last_sequence})
            events.raise_for_status()
            for event in events.json():
                print(f'{event["sequence"]} {event["state"]} {event["kind"]}: '
                      f'{event["message"][:180]}', flush=True)
                last_sequence = event["sequence"]
            status_response = client.get(f"/api/runs/{run_id}")
            status_response.raise_for_status()
            result = status_response.json().get("result")
            if result is not None:
                break
            if time.monotonic() - started > args.timeout * 5 + 300:
                client.post(f"/api/runs/{run_id}/cancel")
                raise RuntimeError("acceptance polling deadline exceeded")
            time.sleep(1)
        assert result["outcome"] == "succeeded", result
        assert result["verification"]["status"] == "passed", result
        assert result["replay"]["status"] == "passed", result
        assert result["replay"]["verification"]["rule_id"] == "sindy-lorenz-v1"
        assert result["bundle"]["integrity"] == "complete", result
        artifact_response = client.get(f"/api/runs/{run_id}/verification/input")
        artifact_response.raise_for_status()
        raw = artifact_response.content
        assert hashlib.sha256(raw).hexdigest() == result["verification"]["artifact_sha256"]
        wrong = json.loads(raw)
        wrong["coefficients"][1][1] += 1
        negative = verify_artifact(
            json.dumps(wrong).encode(), verification_spec("sindy-lorenz-v1"),
            timeout_seconds=30, cancelled=lambda: False,
        )
        assert negative.status == "failed" and negative.failure_category == "metric_not_met"
        bundle_response = client.get(f"/api/runs/{run_id}/bundle")
        bundle_response.raise_for_status()
        assert hashlib.sha256(bundle_response.content).hexdigest() == result["bundle"]["sha256"]
        with zipfile.ZipFile(io.BytesIO(bundle_response.content)) as bundle:
            assert "files/experiment.json" in bundle.namelist()
            experiment = json.loads(bundle.read("files/experiment.json"))
            assert len(experiment["executed_source_sha256"]) >= 4
            assert experiment["samples"] == 100000
        evidence = {
            "run_id": run_id, "target_id": "paper-lorenz", "agent_calls": 0,
            "outcome": result["outcome"], "verification": result["verification"],
            "replay": result["replay"], "bundle_sha256": result["bundle"]["sha256"],
            "negative_control": negative.model_dump(mode="json"),
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "scope": "Real upstream clone and two producer containers on one host; no AI repair.",
            "dependency_note": (
                "Dependencies may be preinstalled in the base image; see install logs and image ID. "
                "Fresh containers do not imply cold online dependency installation."
            ),
            "cross_machine_handoff": "not_verified",
        }
        store.write_text(run_id, "acceptance.json", json.dumps(evidence, ensure_ascii=False, indent=2))
        print(json.dumps(evidence, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
