import hashlib
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from paperharness.contracts.bundle import (
    ChangeKind,
    ExcludedChange,
    ExclusionReason,
    FileChange,
    RepairManifest,
)
from paperharness.harness.replay import (
    ReplayMaterializationError,
    apply_repair_manifest,
    dependency_digest,
)

FROZEN_AT = datetime(2026, 11, 21, tzinfo=UTC)


def file_change(path: str, raw: bytes, change: ChangeKind = ChangeKind.ADDED) -> FileChange:
    return FileChange(
        path=path,
        change=change,
        sha256=hashlib.sha256(raw).hexdigest(),
        size=len(raw),
    )


def repair_manifest(*changes: FileChange, dependencies: list[str] | None = None) -> RepairManifest:
    return RepairManifest(
        run_id="run-1",
        base_revision="a" * 40,
        command=["python", "main.py"],
        python_version="3.11",
        files=list(changes),
        dependencies=dependencies or [],
        frozen_at=FROZEN_AT,
    )


def make_symlink(link: Path, target: Path, *, target_is_directory: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except OSError as exc:
        pytest.skip(f"symlinks are unavailable: {exc}")


def make_junction(link: Path, target: Path) -> None:
    if os.name != "nt":
        pytest.skip("Windows junctions are only available on Windows")
    proc = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0:
        pytest.skip(f"Windows junctions are unavailable: {proc.stderr!r}")


def test_apply_repair_manifest_restores_added_modified_and_deleted_files(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (source / "nested").mkdir()
    (source / "nested" / "added.txt").write_bytes(b"added\n")
    (source / "modified.txt").write_bytes(b"fixed\n")
    (source / "undeclared.txt").write_bytes(b"must not be copied\n")
    (target / "modified.txt").write_bytes(b"old\n")
    (target / "deleted.txt").write_bytes(b"delete me\n")
    manifest = repair_manifest(
        file_change("nested/added.txt", b"added\n"),
        file_change("modified.txt", b"fixed\n", ChangeKind.MODIFIED),
        file_change("deleted.txt", b"", ChangeKind.DELETED),
        file_change("already-missing.txt", b"", ChangeKind.DELETED),
    )

    apply_repair_manifest(source, target, manifest)

    assert (target / "nested" / "added.txt").read_bytes() == b"added\n"
    assert (target / "modified.txt").read_bytes() == b"fixed\n"
    assert not (target / "deleted.txt").exists()
    assert not (target / "already-missing.txt").exists()
    assert not (target / "undeclared.txt").exists()


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/absolute.txt",
        "../escape.txt",
        "nested/../escape.txt",
        ".",
        "./file.txt",
        "nested/./file.txt",
        ".git/config",
        "nested/.git/config",
        "nested\\file.txt",
        "C:/file.txt",
        "C:file.txt",
        "nested/file:stream",
        "nested//file.txt",
        "nested/",
    ],
)
def test_apply_repair_manifest_rejects_illegal_posix_paths(tmp_path, path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()

    with pytest.raises(ReplayMaterializationError):
        apply_repair_manifest(source, target, repair_manifest(file_change(path, b"x")))

    assert list(target.iterdir()) == []


def test_apply_repair_manifest_rejects_duplicate_paths(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (source / "file.txt").write_bytes(b"x")
    manifest = repair_manifest(
        file_change("file.txt", b"x"),
        file_change("file.txt", b"x", ChangeKind.MODIFIED),
    )

    with pytest.raises(ReplayMaterializationError):
        apply_repair_manifest(source, target, manifest)

    assert list(target.iterdir()) == []


def test_apply_repair_manifest_rejects_incomplete_manifest_before_writing(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (source / "file.txt").write_bytes(b"x")
    manifest = repair_manifest(file_change("file.txt", b"x")).model_copy(update={
        "excluded": [ExcludedChange(
            path="secret.bin", change=ChangeKind.ADDED, reason=ExclusionReason.BINARY_FILE,
        )],
    })

    with pytest.raises(ReplayMaterializationError, match="不完整"):
        apply_repair_manifest(source, target, manifest)

    assert list(target.iterdir()) == []


@pytest.mark.parametrize("mismatch", ["sha256", "size"])
def test_apply_repair_manifest_preflights_hash_failures_before_writing(tmp_path, mismatch):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (source / "first.txt").write_bytes(b"first\n")
    (source / "second.txt").write_bytes(b"second\n")
    first = file_change("first.txt", b"first\n")
    second = file_change("second.txt", b"second\n")
    if mismatch == "sha256":
        second = second.model_copy(update={"sha256": "0" * 64})
    else:
        second = second.model_copy(update={"size": second.size + 1})

    with pytest.raises(ReplayMaterializationError):
        apply_repair_manifest(source, target, repair_manifest(first, second))

    assert list(target.iterdir()) == []


@pytest.mark.parametrize("workspace_name", ["source", "target"])
def test_apply_repair_manifest_rejects_symlinked_workspace_root(tmp_path, workspace_name):
    real = tmp_path / "real"
    real.mkdir()
    (real / "file.txt").write_bytes(b"x")
    link = tmp_path / "link"
    make_symlink(link, real, target_is_directory=True)
    source = link if workspace_name == "source" else real
    target = link if workspace_name == "target" else tmp_path / "target"
    if workspace_name == "target":
        target.mkdir()

    with pytest.raises(ReplayMaterializationError):
        apply_repair_manifest(source, target, repair_manifest(file_change("file.txt", b"x")))


@pytest.mark.parametrize("workspace_name", ["source", "target"])
def test_apply_repair_manifest_rejects_symlinked_parent_component(tmp_path, workspace_name):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    real = tmp_path / "real"
    real.mkdir()
    (real / "file.txt").write_bytes(b"x")
    workspace = source if workspace_name == "source" else target
    make_symlink(workspace / "nested", real, target_is_directory=True)

    with pytest.raises(ReplayMaterializationError):
        apply_repair_manifest(
            source,
            target,
            repair_manifest(file_change("nested/file.txt", b"x")),
        )


@pytest.mark.parametrize("workspace_name", ["source", "target"])
def test_apply_repair_manifest_rejects_symlinked_file(tmp_path, workspace_name):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    real = tmp_path / "real.txt"
    real.write_bytes(b"x")
    workspace = source if workspace_name == "source" else target
    make_symlink(workspace / "file.txt", real)

    with pytest.raises(ReplayMaterializationError):
        apply_repair_manifest(source, target, repair_manifest(file_change("file.txt", b"x")))

    assert real.read_bytes() == b"x"


@pytest.mark.parametrize("workspace_name", ["source", "target"])
def test_apply_repair_manifest_rejects_windows_reparse_parent(tmp_path, workspace_name):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    real = tmp_path / "real"
    real.mkdir()
    (real / "file.txt").write_bytes(b"x")
    workspace = source if workspace_name == "source" else target
    make_junction(workspace / "nested", real)

    with pytest.raises(ReplayMaterializationError):
        apply_repair_manifest(
            source,
            target,
            repair_manifest(file_change("nested/file.txt", b"x")),
        )


@pytest.mark.parametrize("workspace_name", ["source", "target"])
def test_apply_repair_manifest_rejects_hardlinked_files(tmp_path, workspace_name):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    real = tmp_path / "real.txt"
    real.write_bytes(b"x")
    workspace = source if workspace_name == "source" else target
    os.link(real, workspace / "file.txt")

    with pytest.raises(ReplayMaterializationError):
        apply_repair_manifest(source, target, repair_manifest(file_change("file.txt", b"x")))

    assert real.read_bytes() == b"x"


@pytest.mark.parametrize("workspace_name", ["source", "target"])
def test_apply_repair_manifest_rejects_non_regular_files(tmp_path, workspace_name):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    workspace = source if workspace_name == "source" else target
    (workspace / "file.txt").mkdir()

    with pytest.raises(ReplayMaterializationError):
        apply_repair_manifest(source, target, repair_manifest(file_change("file.txt", b"x")))


def test_apply_repair_manifest_rejects_symlink_in_workspace_ancestor(tmp_path):
    real_parent = tmp_path / "real-parent"
    source = real_parent / "source"
    source.mkdir(parents=True)
    (source / "file.txt").write_bytes(b"x")
    linked_parent = tmp_path / "linked-parent"
    make_symlink(linked_parent, real_parent, target_is_directory=True)
    target = tmp_path / "target"
    target.mkdir()

    with pytest.raises(ReplayMaterializationError):
        apply_repair_manifest(
            linked_parent / "source",
            target,
            repair_manifest(file_change("file.txt", b"x")),
        )


def test_apply_repair_manifest_rejects_missing_target_under_linked_ancestor(tmp_path):
    real_parent = tmp_path / "real-parent"
    source = real_parent / "source"
    source.mkdir(parents=True)
    (source / "file.txt").write_bytes(b"x")
    linked_parent = tmp_path / "linked-parent"
    make_junction(linked_parent, real_parent)
    target = linked_parent / "new-target"

    with pytest.raises(ReplayMaterializationError):
        apply_repair_manifest(source, target, repair_manifest(file_change("file.txt", b"x")))

    assert not (real_parent / "new-target" / "file.txt").exists()


def test_apply_repair_manifest_rejects_target_parent_file_before_writing(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (source / "first.txt").write_bytes(b"first\n")
    (source / "second.txt").write_bytes(b"second\n")
    (target / "nested").write_bytes(b"not a directory\n")

    with pytest.raises(ReplayMaterializationError):
        apply_repair_manifest(
            source,
            target,
            repair_manifest(
                file_change("first.txt", b"first\n"),
                file_change("nested/second.txt", b"second\n"),
            ),
        )

    assert not (target / "first.txt").exists()
    assert (target / "nested").read_bytes() == b"not a directory\n"


def test_dependency_digest_is_stable_and_sensitive():
    manifest = repair_manifest(dependencies=["pyyaml", "numpy", "pyyaml"])
    reordered = repair_manifest(dependencies=["numpy", "pyyaml"])

    assert dependency_digest(manifest) == "ef22e164727f2b8f181a53982f4956c89dab9653e3b9a9b6e5eb8691b4ae9be1"
    assert dependency_digest(manifest) == dependency_digest(reordered)
    assert dependency_digest(manifest) != dependency_digest(
        manifest.model_copy(update={"python_version": "3.12"})
    )
    assert dependency_digest(manifest) != dependency_digest(
        manifest.model_copy(update={"dependencies": ["numpy", "scipy"]})
    )
