"""Package an accepted real repair for a Windows teammate; no credentials included."""

import argparse
import difflib
import gzip
import hashlib
import json
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(argv, **kwargs):
    return subprocess.run(argv, check=True, timeout=1800, **kwargs)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id")
    parser.add_argument("--source-cache", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--image", help="Previously captured runtime image with identical pip freeze")
    args = parser.parse_args()
    run_dir = ROOT / "runs" / args.run_id
    evidence = json.loads((run_dir / "repair_acceptance.json").read_text(encoding="utf-8"))
    result = evidence["result"]
    if (result["outcome"] != "succeeded" or result["replay"]["status"] != "passed"
            or result["verification"]["status"] != "passed"
            or result["bundle"]["integrity"] != "complete"):
        raise ValueError("A fully accepted repair and clean replay are required")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    cache = args.source_cache.resolve()
    git = ["git", "-C", str(cache)]
    revision = subprocess.check_output(git + ["rev-parse", "HEAD"], text=True).strip()
    if revision != evidence["source_revision"] or subprocess.check_output(
        git + ["status", "--porcelain"],
    ):
        raise ValueError("Original upstream source cache is dirty or differs from accepted revision")
    run(git + ["archive", "--format=zip", "--output", str(output / "upstream-source.zip"),
               "HEAD", "pysindy", "examples/3_original_paper.ipynb", "requirements.txt", "LICENSE"])
    shutil.copy2(run_dir / "reproduce.zip", output / "reproduce.zip")
    if digest(output / "reproduce.zip") != result["bundle"]["sha256"]:
        raise ValueError("Accepted bundle is corrupted")
    for source, target in (
        (ROOT / "scripts/replay_handoff.py", "replay.py"),
        (ROOT / "src/paperharness/harness/lorenz_validator.py", "validator.py"),
        (run_dir / "model_decisions.json", "model_decisions.json"),
        (run_dir / "repair_acceptance.json", "sender_evidence.json"),
    ):
        shutil.copy2(source, output / target)
    baseline_dir = ROOT / "runs" / evidence["baseline_run_id"]
    error = (baseline_dir / "logs/exec-002.err").read_text(encoding="utf-8")
    (output / "before.err").write_text(error, encoding="utf-8")
    before = baseline_dir / "workspace"
    after = run_dir / "workspace"
    diff = []
    changed = []
    for path in sorted((after / "pysindy").rglob("*.py")):
        relative = path.relative_to(after).as_posix()
        old = (before / relative).read_text(encoding="utf-8")
        new = path.read_text(encoding="utf-8")
        if old != new:
            changed.append(relative)
            diff.extend(difflib.unified_diff(
                old.splitlines(keepends=True), new.splitlines(keepends=True),
                fromfile="a/" + relative, tofile="b/" + relative,
            ))
    if not changed or (before / "check.py").read_bytes() != (after / "check.py").read_bytes():
        raise ValueError("Expected a source patch with the scientific adapter unchanged")
    (output / "ai-repair.diff").write_text("".join(diff), encoding="utf-8")
    packages = json.loads((run_dir / "environment.json").read_text(encoding="utf-8"))["packages"]
    if not packages or any(not re.fullmatch(r"[A-Za-z0-9_.-]+==[A-Za-z0-9_.+!-]+", p)
                           for p in packages):
        raise ValueError("Expected exact, index-based dependency versions")
    (output / "requirements.lock").write_text("\n".join(sorted(packages)) + "\n", encoding="utf-8")
    # Derive a portable image from the sender's local base without another Docker Hub pull.
    # This is explicitly an environment handoff, not an independent cold dependency build.
    image = args.image or "luminalit/handoff:" + args.run_id[:12]
    (output / "Dockerfile").write_text(
        "FROM paperharness/base-py3.10\nCOPY requirements.lock /tmp/requirements.lock\n"
        "RUN python -m pip install --no-cache-dir -r /tmp/requirements.lock "
        "&& python -m pip check\n", encoding="utf-8",
    )
    (output / ".dockerignore").write_text("*\n!requirements.lock\n!Dockerfile\n", encoding="utf-8")
    if not args.image:
        print("Building frozen dependency image", flush=True)
        run(["docker", "build", "-t", image, str(output)])
    image_id = subprocess.check_output(
        ["docker", "image", "inspect", image, "--format", "{{.Id}}"], text=True,
    ).strip()
    actual_packages = subprocess.check_output(
        ["docker", "run", "--rm", "--network", "none", image, "python", "-m", "pip", "freeze"],
        text=True,
    ).splitlines()
    if sorted(actual_packages) != sorted(packages):
        raise ValueError("Export image packages differ from the accepted sender environment")
    print("Exporting compressed image (may take several minutes)", flush=True)
    with gzip.open(output / "image.tar.gz", "wb", compresslevel=1) as stream:
        process = subprocess.Popen(["docker", "save", image], stdout=subprocess.PIPE)
        try:
            shutil.copyfileobj(process.stdout, stream, length=1024 * 1024)
            if process.wait(timeout=900):
                raise RuntimeError("Docker image export failed")
        finally:
            process.stdout.close()
            if process.poll() is None:
                process.kill()
                process.wait()
    source_provenance = {
        "repository_url": "https://github.com/dynamicslab/pysindy", "revision": revision,
        "source_snapshot": "git archive HEAD pysindy examples/3_original_paper.ipynb requirements.txt LICENSE",
        "changed_source_files": changed, "adapter_unchanged": True,
        "original_failure": "AttributeError: 'STLSQ' object has no attribute '_preprocess_data'",
        "image_source": "captured_runtime" if args.image else "built_from_frozen_package_list",
        "historical_label_correction": (
            "Original sender verification.code_under_test had a static v1.7.5 label. "
            "Actual run used v1.7, commit " + revision + "; confirmed by frozen manifest and "
            "source hashes. Raw sender records are retained unchanged. Future rule metadata "
            "now refers to the frozen run revision instead of hardcoding a version."
        ),
        "limitations": ["Selected source snapshot, not the full repository history.",
                        "Frozen Linux amd64 environment; not a native Windows execution.",
                        "Same-host checks do not establish teammate success."],
    }
    (output / "source-provenance.json").write_text(json.dumps(
        source_provenance, ensure_ascii=False, indent=2,
    ), encoding="utf-8")
    (output / "HANDOFF.md").write_text(
        "# 给 Windows 队友的复跑说明\n\n"
        "这是 PySINDy v1.7 真实源码兼容性错误的修复包。AI 修复轨迹、原始报错和补丁均随包提供。\n"
        "你需要 Windows x64、Python 3.11+、Docker Desktop（Linux containers / WSL2 后端）。"
        "磁盘建议空余 5 GB。无需模型密钥、Git 或联网下载源码/依赖。\n\n"
        "1. 完整解压到一个普通本地文件夹，启动 Docker Desktop，等引擎就绪。\n"
        "2. 在该文件夹打开 PowerShell，执行：\n\n"
        "```powershell\npython replay.py --participant 队友\n```\n\n"
        "3. 等待脚本结束。脚本先核对文件、导入固定镜像，再重现原始报错；应用冻结补丁后，"
        "在另一个断网新容器里执行修复后源码，"
        "最后在另一个断网只读容器里独立验收三个方程的系数。\n"
        "4. 把新生成的 receiver-*/feedback.json 发回；失败也照样发回该文件和同目录 *.err。"
        "另请写明是否独立电脑、遇到哪些困难、有没有口头指导、准备环境花了多久。\n\n"
        "不需要也不要修改代码、验收器或阈值。不用复制发送者的系数结果；脚本会重新计算。\n"
        "这是 Windows 宿主上的 Linux Docker 复跑，不代表原生 Windows 或全新安装依赖通过。"
        "只复现一个 Lorenz 子实验，不代表完整论文。\n\n"
        "发送者本机预检使用 --role local-check；不能把其反馈当成你的跨机器结果。"
        "反馈中只有你亲自运行得到的 succeeded 才能作为交接证据，仍需本人确认独立电脑。\n",
        encoding="utf-8",
    )
    case = {
        "schema_version": 1, "sender_run_id": args.run_id, "source_revision": revision,
        "bundle_sha256": result["bundle"]["sha256"],
        "artifact_sha256": result["verification"]["artifact_sha256"],
        "image_id": image_id, "file_sha256": {
            path.name: digest(path) for path in sorted(output.iterdir()) if path.is_file()
        },
    }
    (output / "case.json").write_text(json.dumps(case, ensure_ascii=False, indent=2), encoding="utf-8")
    archive_path = output.with_suffix(".zip")
    with zipfile.ZipFile(archive_path, "x", compression=zipfile.ZIP_STORED) as archive:
        for path in sorted(output.iterdir()):
            archive.write(path, path.name)
    # Publish only this run's reviewed package, without exposing its host file path.
    shutil.copy2(archive_path, run_dir / "handoff.zip")
    (run_dir / "handoff_package.json").write_text(json.dumps({
        "run_id": args.run_id, "sha256": digest(archive_path),
        "size_bytes": archive_path.stat().st_size,
    }, indent=2), encoding="utf-8")
    print(json.dumps({"handoff": str(archive_path), "sha256": digest(archive_path),
                      "bytes": archive_path.stat().st_size}, indent=2), flush=True)


if __name__ == "__main__":
    main()
