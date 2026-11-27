"""Reject damaged or unsafe handoff inputs before executing external tools."""

import hashlib
import importlib.util
import io
import zipfile
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "replay_handoff", Path(__file__).resolve().parents[2] / "scripts/replay_handoff.py",
)
handoff = importlib.util.module_from_spec(spec)
spec.loader.exec_module(handoff)


@pytest.mark.parametrize("name", ["../escape", "/absolute", "C:/host", "a\\b", ".git/config",
                                  "a/../b", "a//b"])
def test_handoff_rejects_unsafe_material_paths(tmp_path, name):
    with pytest.raises(ValueError, match="Unsafe material path"):
        handoff.checked_path(tmp_path, name)


def test_handoff_rejects_changed_zip_before_reading_materials(tmp_path):
    path = tmp_path / "reproduce.zip"
    path.write_bytes(b"a corrupted download")
    with pytest.raises(ValueError, match="Bundle SHA-256 mismatch"):
        handoff.read_bundle(path, "0" * 64)


def test_handoff_rejects_payload_that_disagrees_with_internal_checksum(tmp_path):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        digest = hashlib.sha256(b"original").hexdigest()
        archive.writestr("SHA256SUMS", f"{digest}  files/check.py\n")
        archive.writestr("files/check.py", b"modified")
    path = tmp_path / "reproduce.zip"
    path.write_bytes(stream.getvalue())
    with pytest.raises(ValueError, match="Material checksum mismatch"):
        handoff.read_bundle(path, hashlib.sha256(path.read_bytes()).hexdigest())
