import hashlib
import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from paperharness.contracts.bundle import (
    BundleResult,
    ChangeKind,
    ExclusionReason,
    FileChange,
)
from paperharness.harness.bundle import (
    MAX_FILE_BYTES,
    ManifestBuildError,
    build_repair_manifest,
    manifest_digest,
    pack_bundle,
)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")

BASE = {
    "main.py": "print('hello')\n",
    "old.py": "to be deleted\n",
    "requirements.txt": "numpy\n",
}


def git(workspace: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(workspace), *args],
        check=True, capture_output=True, timeout=30,
        env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
             "PATH": os.environ.get("PATH", "")},
    )


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    for name, content in BASE.items():
        (ws / name).write_text(content, encoding="utf-8")
    git(ws, "init", "-q")
    git(ws, "add", "-A")
    git(ws, "-c", "core.autocrlf=false", "commit", "-q", "-m", "base")
    revision = subprocess.run(
        ["git", "-C", str(ws), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True, timeout=30,
    ).stdout.strip()
    # 变更：修改 main.py、删除 old.py、新增 new.py、预期产物 out.txt
    # 全部按字节写入：Windows 下 write_text 会把 \n 转成 \r\n，污染哈希断言。
    (ws / "main.py").write_bytes(b"print('fixed')\n")
    (ws / "old.py").unlink()
    (ws / "new.py").write_bytes(b"# new\n")
    (ws / "out.txt").write_bytes(b"artifact\n")
    return ws, revision


def make_manifest(ws, revision, **overrides):
    kwargs = {
        "run_id": "r1", "workspace": ws, "base_revision": revision,
        "command": ["python", "main.py"], "python_version": "3.10",
        "expected_artifacts": ["out.txt"],
    }
    kwargs.update(overrides)
    return build_repair_manifest(**kwargs)


def test_manifest_separates_changes_artifacts_and_exclusions(workspace):
    ws, revision = workspace
    (ws / "blob.bin").write_bytes(b"\x00\x01\x02")
    (ws / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (ws / "big.txt").write_bytes(b"x" * (MAX_FILE_BYTES + 1))

    manifest = make_manifest(ws, revision)
    by_path = {change.path: change for change in manifest.files}
    assert by_path["main.py"].change is ChangeKind.MODIFIED
    assert by_path["new.py"].change is ChangeKind.ADDED
    assert by_path["old.py"].change is ChangeKind.DELETED
    assert by_path["old.py"].sha256 == hashlib.sha256(b"").hexdigest()
    assert by_path["main.py"].sha256 == hashlib.sha256(b"print('fixed')\n").hexdigest()
    # 产物与源码变更分开：out.txt 不算变更文件
    assert "out.txt" not in by_path
    assert manifest.artifacts == ["out.txt"]
    reasons = {item.path: item.reason for item in manifest.excluded}
    assert reasons["blob.bin"] is ExclusionReason.BINARY_FILE
    assert reasons[".env"] is ExclusionReason.SENSITIVE_NAME
    assert reasons["big.txt"] is ExclusionReason.OVERSIZED_FILE


def test_manifest_rejects_unauditable_workspace(tmp_path):
    (tmp_path / "ws").mkdir()
    with pytest.raises(ManifestBuildError):
        build_repair_manifest(
            run_id="r1", workspace=tmp_path / "ws", base_revision="a" * 40,
            command=["python"], python_version="3.10",
        )


def test_manifest_records_dependencies_and_env_names(workspace):
    ws, revision = workspace
    manifest = make_manifest(
        ws, revision,
        repository_url="https://github.com/example/repo.git",
        dependencies=["pyyaml", "numpy", "pyyaml"],
        environment={"MY_FLAG": "任意值都会被脱敏"},
    )
    assert manifest.repository_url == "https://github.com/example/repo.git"
    assert manifest.dependencies == ["numpy", "pyyaml"]
    assert manifest.environment == {"MY_FLAG": ""}


def test_manifest_only_preserves_safe_whitelisted_environment_values(workspace):
    ws, revision = workspace
    manifest = make_manifest(
        ws, revision,
        environment={
            "OMP_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "/tmp/secret",
            "PATH": "/tmp/bin",
        },
    )

    assert manifest.environment == {
        "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "", "PATH": "",
    }


def test_pack_redacts_unsafe_environment_metadata(workspace, tmp_path):
    ws, revision = workspace
    manifest = make_manifest(ws, revision, environment={"OMP_NUM_THREADS": "1"})
    metadata = {
        "patch.diff": "",
        "environment.json": json.dumps({
            "python_version": "3.10",
            "environment": {"OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "token=secret"},
        }),
    }

    result = pack_bundle(tmp_path, ws, manifest, metadata)

    assert result.integrity == "complete"
    with zipfile.ZipFile(tmp_path / "reproduce.zip") as archive:
        environment = json.loads(archive.read("environment.json"))["environment"]
    assert environment == {"OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": ""}


@pytest.mark.parametrize(
    "environment_metadata",
    [
        "{",
        json.dumps({"environment": "token=secret"}),
        json.dumps(["token=secret"]),
    ],
)
def test_pack_excludes_unparseable_environment_metadata(
    workspace, tmp_path, environment_metadata,
):
    ws, revision = workspace
    manifest = make_manifest(ws, revision)

    result = pack_bundle(
        tmp_path, ws, manifest,
        {"patch.diff": "", "environment.json": environment_metadata},
    )

    assert result.integrity == "incomplete"
    assert not (tmp_path / "reproduce.zip").exists()
    item = next(item for item in result.items if item.path == "environment.json")
    assert item.status == "missing"
    assert any("环境变量元数据格式无效，已从材料包剔除" in limitation
               for limitation in result.limitations)


def test_pack_complete_writes_zip_with_hashes(workspace, tmp_path):
    ws, revision = workspace
    manifest = make_manifest(
        ws, revision,
        repository_url="https://github.com/example/repo.git",
        environment={"MY_FLAG": "secret"},
    )
    metadata = {"patch.diff": "diff --git a/main.py b/main.py\n",
                "run_plan.json": json.dumps({"command": ["python", "main.py"]}),
                "environment.json": "{}"}
    result = pack_bundle(tmp_path, ws, manifest, metadata)
    assert result.integrity == "complete"
    assert result.url == f"/api/runs/{manifest.run_id}/bundle"
    assert result.manifest_digest == manifest_digest(manifest)
    assert result.sha256 == hashlib.sha256((tmp_path / "reproduce.zip").read_bytes()).hexdigest()
    assert result.missing == []
    assert result.external_inputs == [f"{manifest.repository_url}@{revision}"]
    assert "仅包含 UTF-8 文本变更；二进制、超限或不可读内容见缺项清单" in result.limitations
    assert "环境变量仅记录名称，需按名称补填非敏感值" in result.limitations

    with zipfile.ZipFile(tmp_path / "reproduce.zip") as archive:
        names = set(archive.namelist())
        assert {"files/main.py", "files/new.py", "artifacts/out.txt",
                "repair_manifest.json", "patch.diff", "SHA256SUMS"} <= names
        # 删除的文件只列清单，不进压缩包
        assert "files/old.py" not in names
        stored = archive.read("files/main.py")
        assert stored == b"print('fixed')\n"
        manifest_in_zip = json.loads(archive.read("repair_manifest.json"))
        assert manifest_in_zip["schema_version"] == 1
        sums = dict(
            line.split()[::-1]
            for line in archive.read("SHA256SUMS").decode().splitlines()
        )
        assert sums["files/main.py"] == hashlib.sha256(stored).hexdigest()
        assert sums["repair_manifest.json"] == result.manifest_digest
    assert {item.path for item in result.items if item.status == "included"} == names - {"SHA256SUMS"}
    entries = {entry.path: entry for entry in result.entries}
    assert set(entries) == names - {"SHA256SUMS"}
    with zipfile.ZipFile(tmp_path / "reproduce.zip") as archive:
        for path, entry in entries.items():
            raw = archive.read(path)
            assert entry.sha256 == hashlib.sha256(raw).hexdigest()
            assert entry.size == len(raw)


def test_pack_missing_metadata_is_incomplete_and_no_zip(workspace, tmp_path):
    ws, revision = workspace
    manifest = make_manifest(ws, revision)
    result = pack_bundle(tmp_path, ws, manifest, {"patch.diff": None})
    assert result.integrity == "incomplete"
    assert result.url is None
    assert result.sha256 is None
    assert result.manifest_digest == manifest_digest(manifest)
    assert not (tmp_path / "reproduce.zip").exists()
    missing = [item for item in result.items if item.status == "missing"]
    assert [item.path for item in missing] == ["patch.diff"]
    assert [item.model_dump() for item in result.missing] == [
        {"path": "patch.diff", "reason": "运行记录缺少该材料。"}
    ]
    assert {entry.path for entry in result.entries} == {
        item.path for item in result.items if item.status == "included"
    }
    assert result.external_inputs == []
    assert "仅包含 UTF-8 文本变更；二进制、超限或不可读内容见缺项清单" in result.limitations


def test_pack_sensitive_exclusion_blocks_bundle(workspace, tmp_path):
    ws, revision = workspace
    (ws / "credentials.json").write_text("{}", encoding="utf-8")
    manifest = make_manifest(ws, revision)
    result = pack_bundle(tmp_path, ws, manifest, {"patch.diff": ""})
    assert result.integrity == "blocked"
    assert result.url is None
    assert result.sha256 is None
    assert not (tmp_path / "reproduce.zip").exists()
    blocked = [item for item in result.items if item.status == "blocked"]
    assert [item.path for item in blocked] == ["credentials.json"]
    assert "凭据" in blocked[0].reason
    assert blocked[0].reason in result.limitations


def test_pack_refuses_stale_content_changed_after_freeze(workspace, tmp_path):
    ws, revision = workspace
    manifest = make_manifest(ws, revision)
    (ws / "new.py").write_text("# tampered after freeze\n", encoding="utf-8")
    result = pack_bundle(tmp_path, ws, manifest, {"patch.diff": ""})
    assert result.integrity == "incomplete"
    stale = [item for item in result.items if item.path == "files/new.py"]
    assert stale[0].status == "missing"
    assert result.missing[0].path == "files/new.py"
    assert result.missing[0].reason


def test_bundle_result_roundtrips_json():
    result = BundleResult(integrity="incomplete", total_bytes=3)
    assert BundleResult.model_validate_json(result.model_dump_json()) == result


def test_pack_rejects_backslash_declared_path(workspace, tmp_path):
    ws, revision = workspace
    (ws / "nested").mkdir()
    raw = b"must stay missing\n"
    (ws / "nested" / "file.txt").write_bytes(raw)
    change = FileChange(
        path="nested\\file.txt", change=ChangeKind.ADDED,
        sha256=hashlib.sha256(raw).hexdigest(), size=len(raw),
    )
    manifest = make_manifest(ws, revision).model_copy(update={"files": [change], "artifacts": []})

    result = pack_bundle(tmp_path, ws, manifest, {"patch.diff": ""})

    assert result.integrity == "incomplete"
    assert not any(item.path == "files/nested\\file.txt" for item in result.entries)
    assert any(item.path == "files/nested\\file.txt" and item.status == "missing"
               for item in result.items)


def test_pack_does_not_follow_parent_junction(workspace, tmp_path):
    ws, revision = workspace
    outside = tmp_path / "outside"
    outside.mkdir()
    raw = b"outside secret\n"
    (outside / "secret.txt").write_bytes(raw)
    link = ws / "linked"
    if os.name == "nt":
        proc = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
            capture_output=True, timeout=30, check=False,
        )
        if proc.returncode != 0:
            pytest.skip(f"Windows junctions are unavailable: {proc.stderr!r}")
    else:
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            pytest.skip(f"symlinks are unavailable: {exc}")
    change = FileChange(
        path="linked/secret.txt", change=ChangeKind.ADDED,
        sha256=hashlib.sha256(raw).hexdigest(), size=len(raw),
    )
    manifest = make_manifest(ws, revision).model_copy(update={"files": [change], "artifacts": []})

    result = pack_bundle(tmp_path, ws, manifest, {"patch.diff": ""})

    assert result.integrity == "incomplete"
    assert not any(item.path == "files/linked/secret.txt" for item in result.entries)
    assert any(item.path == "files/linked/secret.txt" and item.status == "missing"
               for item in result.items)
