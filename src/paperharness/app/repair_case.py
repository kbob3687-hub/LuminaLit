"""Read-only projections of the maintained, recorded PySINDy repair case."""

import difflib
import hashlib
import json
import re
import subprocess
from pathlib import Path

from fastapi import HTTPException

from paperharness.harness.store import RunStore

REVISION = "d4e64c487f03065d96f4bb2b8b9113408c2b357c"
SOURCE_FILE = "pysindy/optimizers/base.py"


def _json(path: Path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 256000:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _record(store, run_id):
    try:
        root = store.run_dir(run_id)
        if root.is_symlink() or root.resolve().parent != store.root.resolve():
            raise ValueError("Invalid run directory")
        result = store.read_result(run_id)
    except ValueError as exc:
        raise HTTPException(404, detail={"code": "run_not_found"}) from exc
    if result is None or result.resolved_revision != REVISION:
        raise HTTPException(404, detail={"code": "repair_evidence_not_available"})
    return result


def featured_case(store: RunStore):
    # Only accepted, explicitly recorded real-model runs qualify for the homepage.
    for path in sorted(store.root.glob("*/repair_acceptance.json"),
                       key=lambda item: item.stat().st_mtime, reverse=True):
        if not re.fullmatch(r"[0-9a-f]{32}", path.parent.name) or path.parent.is_symlink():
            continue
        record = _json(path)
        if not isinstance(record, dict) or record.get("source_revision") != REVISION:
            continue
        decisions = record.get("successful_model_json_decisions")
        if type(decisions) is not int or decisions < 1:
            continue
        try:
            result = _record(store, path.parent.name)
        except HTTPException:
            continue
        if (result.outcome != "succeeded" or not result.verification
                or result.verification.status != "passed" or not result.replay
                or result.replay.status != "passed"):
            continue
        return {"run_id": result.run_id, "source_revision": REVISION,
                "model": record.get("model"), "recorded_at": result.verification.checked_at}
    raise HTTPException(404, detail={"code": "recorded_case_not_available"})


def handoff_metadata(store, run_id):
    _record(store, run_id)
    root = store.run_dir(run_id)
    metadata = _json(root / "handoff_package.json")
    path = root / "handoff.zip"
    available = (
        isinstance(metadata, dict) and metadata.get("run_id") == run_id
        and isinstance(metadata.get("sha256"), str)
        and re.fullmatch(r"[0-9a-f]{64}", metadata["sha256"]) is not None
        and not path.is_symlink() and path.is_file()
        and path.stat().st_size == metadata.get("size_bytes")
    )
    return {
        # A sender's successful container run never establishes teammate success.
        "status": "pending", "available": bool(available),
        "size_bytes": metadata["size_bytes"] if available else None,
        "sha256": metadata["sha256"] if available else None,
        "download_url": f"/api/runs/{run_id}/handoff" if available else None,
    }


def handoff_file(store, run_id):
    metadata = handoff_metadata(store, run_id)
    if not metadata["available"]:
        raise HTTPException(404, detail={"code": "handoff_not_ready"})
    path = store.run_dir(run_id) / "handoff.zip"
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != metadata["sha256"]:
        raise HTTPException(409, detail={"code": "handoff_corrupted"})
    return path, digest


def repair_evidence(store, run_id):
    _record(store, run_id)
    events = store.read_events(run_id)
    original = next((e for e in events if e.kind == "test"
                     and e.data.get("exit_code") not in (None, 0)), None)
    actions = [{
        "sequence": event.sequence, "action": event.kind, "ok": event.data.get("ok"),
        "path": event.data.get("path"), "summary": event.message,
    } for event in events if event.state == "repairing" and event.kind in {
        "read_file", "search_text", "replace_exact", "install_requirement",
        "run_smoke_test", "finish_repair", "set_environment",
    }]
    root = store.run_dir(run_id)
    acceptance = _json(root / "repair_acceptance.json") or {}
    if not isinstance(acceptance, dict):
        acceptance = {}
    changed = any(a["action"] == "replace_exact" and a["ok"] is True
                  and a["path"] == SOURCE_FILE for a in actions)
    diff = None
    if changed:
        workspace = root / "workspace"
        path = workspace / SOURCE_FILE
        # Fixed maintained path only; never take a subprocess argument from an API request.
        if (not workspace.is_symlink() and path.resolve().is_relative_to(workspace.resolve())
                and path.is_file() and path.stat().st_size < 128000):
            try:
                before = subprocess.run(
                    ["git", "-C", str(workspace), "show", f"{REVISION}:{SOURCE_FILE}"],
                    capture_output=True, check=True, timeout=10,
                ).stdout.decode("utf-8")
                after = path.read_text(encoding="utf-8")
                diff = "".join(difflib.unified_diff(
                    before.splitlines(keepends=True), after.splitlines(keepends=True),
                    fromfile="a/" + SOURCE_FILE, tofile="b/" + SOURCE_FILE,
                ))
            except (OSError, ValueError, subprocess.SubprocessError):
                pass  # Source may have been archived; never fabricate a diff.
    return {
        "run_id": run_id, "source_revision": REVISION, "model": acceptance.get("model"),
        "decisions_count": acceptance.get("successful_model_json_decisions"),
        "original_error": original.data.get("stderr_tail", "") if original else None,
        "actions": actions, "diff": diff, "changed_files": [SOURCE_FILE] if changed else [],
        "handoff": handoff_metadata(store, run_id),
    }
