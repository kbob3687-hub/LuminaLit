import os
import subprocess
from pathlib import Path

from paperharness.harness.runner import _clone_once
from paperharness.targets import load_targets


def git(*args, cwd=None):
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
        check=True, timeout=30,
    ).stdout.strip()


def test_lorenz_sparse_checkout_supports_generated_evidence_and_patch_export(tmp_path):
    # This internal Git helper test uses our own local fixture, never a public API URL.
    source = tmp_path / "source"
    source.mkdir()
    for name, text in {
        "pysindy/__init__.py": "# fixture source, not executed\n",
        "examples/3_original_paper.ipynb": "{}\n",
        "requirements.txt": "# original constraints\n",
        ".gitignore": "__pycache__/\n",
        "LICENSE": "fixture\n",
        "unused-data/data.txt": "not part of this experiment\n",
    }.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    git("init", "-q", str(source))
    git("add", "-A", cwd=source)
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "-qm", "fixture", cwd=source)
    revision = git("rev-parse", "HEAD", cwd=source)
    target = next(t for t in load_targets(Path(__file__).resolve().parents[2] / "targets")
                  if t.id == "paper-lorenz")
    workspace = tmp_path / "workspace"
    assert _clone_once(source.as_uri(), revision, workspace, dict(os.environ),
                       target.checkout_paths) == revision
    assert (workspace / "pysindy/__init__.py").is_file()
    assert not (workspace / "unused-data").exists()
    for name in ("check.py", "coefficients.json", "experiment.json"):
        (workspace / name).write_text("{}\n", encoding="utf-8")
    git("add", "-A", cwd=workspace)
    staged = git("diff", "--cached", "--name-only", cwd=workspace).splitlines()
    assert set(staged) == {"check.py", "coefficients.json", "experiment.json"}
