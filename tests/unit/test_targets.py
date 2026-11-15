"""靶场 manifest 校验测试（契约第 5 节）。"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from paperharness.targets import TargetManifest, load_targets, public_view

VALID = {
    "id": "missing-dependency",
    "repo_url": "https://github.com/owner/repo",
    "revision": "0123456789abcdef0123456789abcdef01234567",
    "python_version": "3.10",
    "command": ["python", "main.py"],
    "expected_artifact": "out.txt",
    "failure_class": "missing_dependency",
    "repairable": True,
    "allowed_actions": ["install_requirement"],
    "expected_final": "succeeded",
    "notes": "requirements.txt 缺 PyYAML",
}


def write_target(root: Path, data: dict) -> Path:
    target_dir = root / data["id"]
    target_dir.mkdir(parents=True)
    (target_dir / "manifest.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )
    return target_dir


def test_valid_manifest_roundtrip():
    manifest = TargetManifest.model_validate(VALID)
    assert manifest.failure_class.value == "missing_dependency"
    assert manifest.repairable is True


def test_revision_must_be_full_commit():
    with pytest.raises(ValidationError):
        TargetManifest.model_validate(VALID | {"revision": "0123456"})


def test_unknown_failure_class_rejected():
    with pytest.raises(ValidationError):
        TargetManifest.model_validate(VALID | {"failure_class": "typo_class"})


def test_unrepairable_requires_failed_final():
    with pytest.raises(ValidationError, match="expected_final"):
        TargetManifest.model_validate(
            VALID | {"repairable": False, "expected_final": "succeeded"}
        )


def test_repairable_requires_succeeded_final():
    with pytest.raises(ValidationError, match="expected_final"):
        TargetManifest.model_validate(
            VALID | {"repairable": True, "expected_final": "failed"}
        )


def test_allowed_actions_must_be_known():
    with pytest.raises(ValidationError, match="unknown actions"):
        TargetManifest.model_validate(VALID | {"allowed_actions": ["rm_dash_rf"]})


def test_load_targets_requires_id_match_dir(tmp_path):
    mismatch_dir = tmp_path / "wrong-dir-name"
    mismatch_dir.mkdir()
    (mismatch_dir / "manifest.json").write_text(
        json.dumps(VALID, ensure_ascii=False), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="不一致"):
        load_targets(tmp_path)


def test_load_targets_returns_sorted(tmp_path):
    write_target(tmp_path, VALID)
    other = VALID | {"id": "clean-example", "failure_class": "none", "allowed_actions": []}
    write_target(tmp_path, other)
    loaded = load_targets(tmp_path)
    assert [m.id for m in loaded] == ["clean-example", "missing-dependency"]


def test_public_view_hides_answer_fields(tmp_path):
    write_target(tmp_path, VALID)
    views = public_view(load_targets(tmp_path))
    assert len(views) == 1
    assert set(views[0]) == {"id", "repo_url", "command", "expected_artifact"}
    assert "failure_class" not in views[0]
    assert "allowed_actions" not in views[0]
