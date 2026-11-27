"""Portable, no-model replay of a reviewed Lorenz repair handoff (stdlib only).

Copy this file into the handoff folder with case.json, reproduce.zip,
upstream-source.zip, image.tar.gz and validator.py. Run with Python and Docker.
The receiver's execution is reported separately from the sender's local check.
"""

import argparse
import base64
import hashlib
import io
import json
import platform
import re
import shutil
import subprocess
import sys
import time
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def file_sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def checked_path(root, name):
    parts = PurePosixPath(name).parts
    if (not parts or name != PurePosixPath(name).as_posix()
            or name.startswith("/") or "\\" in name or ":" in name
            or any(part in {"..", ".git"} for part in parts)):
        raise ValueError(f"Unsafe material path: {name}")
    return root.joinpath(*parts)


def read_bundle(path, expected_hash):
    raw = path.read_bytes()
    if sha256(raw) != expected_hash:
        raise ValueError("Bundle SHA-256 mismatch")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or sum(i.file_size for i in archive.infolist()) > 5000000:
            raise ValueError("Duplicate or oversized ZIP material")
        entries = {name: archive.read(name) for name in names}
    for line in entries["SHA256SUMS"].decode("utf-8").splitlines():
        digest, name = line.split("  ", 1)
        if sha256(entries[name]) != digest:
            raise ValueError(f"Material checksum mismatch: {name}")
    return entries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--participant", default="teammate")
    parser.add_argument("--role", choices=["teammate", "local-check"], default="teammate")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    folder = Path(__file__).resolve().parent
    output = (args.output_dir or folder / ("receiver-" + uuid.uuid4().hex[:10])).resolve()
    output.mkdir(parents=True, exist_ok=False)  # Never replace a previous result/workspace.
    report = {
        "schema_version": 1, "role": args.role, "participant": args.participant,
        "started_at": datetime.now(UTC).isoformat(),
        "system": platform.platform(), "machine": platform.machine(),
        "python": sys.version, "outcome": "failed", "stage": "materials",
        "cross_machine_claim": "receiver_self_report_requires_human_confirmation",
    }
    started = time.monotonic()
    container_name = "luminalit-handoff-" + uuid.uuid4().hex[:12]
    sequence = 0

    def command(argv, *, timeout=900, allow_failure=False):
        nonlocal sequence
        sequence += 1
        print(f"[{sequence}] {report['stage']}: {argv[0]}", flush=True)
        proc = subprocess.run(argv, capture_output=True, timeout=timeout, check=False)
        (output / f"{sequence:02d}.out").write_bytes(proc.stdout)
        (output / f"{sequence:02d}.err").write_bytes(proc.stderr)
        if proc.returncode and not allow_failure:
            raise RuntimeError(f"Command failed ({proc.returncode}); see {sequence:02d}.err")
        return proc

    try:
        case = json.loads((folder / "case.json").read_text(encoding="utf-8"))
        for name, digest in case["file_sha256"].items():
            if file_sha256(checked_path(folder, name)) != digest:
                raise ValueError(f"Handoff file changed: {name}")
        entries = read_bundle(folder / "reproduce.zip", case["bundle_sha256"])
        manifest = json.loads(entries["repair_manifest.json"])
        if (manifest["repository_url"] != "https://github.com/dynamicslab/pysindy"
                or manifest["base_revision"] != case["source_revision"]
                or not re.fullmatch("[0-9a-f]{40}", case["source_revision"])
                or manifest["command"] != ["python", "-I", "check.py"]):
            raise ValueError("Unexpected repository, revision or command")
        report.update({"sender_run_id": manifest["run_id"], "bundle_sha256": case["bundle_sha256"],
                       "source_revision": case["source_revision"]})
        if not shutil.which("docker"):
            raise RuntimeError("Required tool unavailable: docker")
        report["docker_version"] = command(
            ["docker", "version", "--format", "{{.Server.Version}}"], timeout=30,
        ).stdout.decode().strip()
        report["stage"] = "restore_original_upstream_snapshot"
        workspace = output / "workspace"
        workspace.mkdir()
        with zipfile.ZipFile(folder / "upstream-source.zip") as source:
            if sum(item.file_size for item in source.infolist()) > 20000000:
                raise ValueError("Oversized upstream snapshot")
            for item in source.infolist():
                if item.is_dir():
                    continue
                target = checked_path(workspace, item.filename)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read(item))
        def apply_material(only_adapter=False):
            # Never copy sender coefficients or experiment metadata into this workspace.
            for change in manifest["files"]:
                name = change["path"]
                if name in {"coefficients.json", "experiment.json"}:
                    continue
                if only_adapter and name != "check.py":
                    continue
                target = checked_path(workspace, name)
                if change["change"] == "deleted":
                    target.unlink(missing_ok=True)
                    continue
                raw = entries["files/" + name]
                if len(raw) != change["size"] or sha256(raw) != change["sha256"]:
                    raise ValueError(f"Frozen source mismatch: {name}")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)

        apply_material(only_adapter=True)
        if (workspace / "coefficients.json").exists():
            raise ValueError("Receiver artifact must not exist before execution")
        report["stage"] = "load_frozen_image"
        image = case["image_id"]
        command(["docker", "load", "--input", str(folder / "image.tar.gz")], timeout=900)
        report["image_id"] = command(
            ["docker", "image", "inspect", image, "--format", "{{.Id}}"], timeout=30,
        ).stdout.decode().strip()
        if report["image_id"] != image:
            raise ValueError("Loaded image does not match the frozen image ID")
        producer_command = [
            "docker", "run", "--rm", "--name", container_name, "--platform", "linux/amd64",
            "--network", "none", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--memory", "2g", "--cpus", "1.5",
            "--pids-limit", "128", "--tmpfs", "/tmp:rw,size=128m",
            "--mount", f"type=bind,source={workspace},target=/workspace",
            "-w", "/workspace", image, "python", "-I", "check.py",
        ]
        report["stage"] = "reproduce_original_failure"
        baseline = command(producer_command, allow_failure=True)
        if baseline.returncode == 0 or b"has no attribute '_preprocess_data'" not in baseline.stderr:
            raise ValueError("The original scientific source failure did not reproduce")
        report["original_failure_reproduced"] = True
        report["original_stderr_sha256"] = sha256(baseline.stderr)
        if (workspace / "coefficients.json").exists():
            raise ValueError("Failed baseline unexpectedly produced coefficients")
        apply_material()
        report["stage"] = "execute_repaired_source"
        command(producer_command)
        raw = (workspace / "coefficients.json").read_bytes()
        report["artifact_sha256"] = sha256(raw)
        report["stage"] = "independent_verifier"
        validator = (folder / "validator.py").read_text(encoding="utf-8")
        verdict = command([
            "docker", "run", "--rm", "--name", container_name, "--platform", "linux/amd64",
            "--network", "none", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--memory", "256m", "--cpus", "0.5",
            "--pids-limit", "32", image, "python", "-I", "-c", validator,
            base64.b64encode(raw).decode("ascii"),
        ], timeout=45)
        report["verification"] = json.loads(verdict.stdout)
        if report["verification"]["status"] != "passed":
            raise RuntimeError("Independent scientific verification failed")
        report["matches_sender_artifact"] = report["artifact_sha256"] == case["artifact_sha256"]
        report["outcome"] = "succeeded"
        report["stage"] = "finished"
    except Exception as exc:  # noqa: BLE001 - failures must leave receiver feedback
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if shutil.which("docker"):
            try:
                subprocess.run(["docker", "rm", "-f", container_name], capture_output=True,
                               timeout=30, check=False)
            except (OSError, subprocess.TimeoutExpired) as exc:
                report["cleanup_error"] = str(exc)
        report["elapsed_seconds"] = round(time.monotonic() - started, 2)
        (output / "feedback.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
        print(f"Feedback: {output / 'feedback.json'}", flush=True)
    return 0 if report["outcome"] == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
