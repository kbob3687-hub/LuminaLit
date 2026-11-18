"""一键跑指定内置靶场（真 clone + 真容器，不走 API）。

用法：
  .venv/Scripts/python scripts/run_target.py <target-id>            # 真 Docker
  .venv/Scripts/python scripts/run_target.py <target-id> --fake     # 假沙箱（不真跑）

LLM 配置同服务（PAPERHARNESS_LLM_*，未配置自动落回假模型）；
命中靶场时运行计划来自 manifest，不依赖 LLM。
"""

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from paperharness.app.main import _build_aci_factory, _build_llm
from paperharness.contracts.run import RunRequest
from paperharness.harness.runner import HarnessRunner
from paperharness.harness.store import RunStore
from paperharness.sandbox.docker_sandbox import create_docker_sandbox
from paperharness.sandbox.fake import FakeSandboxSession
from paperharness.targets import load_targets


def main() -> None:
    parser = argparse.ArgumentParser(description="跑一个内置靶场并打印结果")
    parser.add_argument("target_id", help="targets/<id> 的 id，如 clean-example")
    parser.add_argument("--fake", action="store_true", help="用假沙箱（不真跑容器）")
    parser.add_argument("--timeout", type=int, default=600, help="单步超时秒数（30-900）")
    args = parser.parse_args()

    manifests = load_targets(ROOT / "targets")
    target = next((m for m in manifests if m.id == args.target_id), None)
    if target is None:
        known = ", ".join(m.id for m in manifests)
        raise SystemExit(f"unknown target {args.target_id!r}; known: {known}")

    runs_root = ROOT / "runs-test" / f"target-{args.target_id}-{int(time.time())}"
    store = RunStore(runs_root)

    if args.fake:
        def factory(request, run_dir):
            return FakeSandboxSession(workspace=run_dir / "workspace")
    else:
        factory = create_docker_sandbox

    runner = HarnessRunner(
        store=store,
        llm=_build_llm(),
        sandbox_factory=factory,
        aci_factory=_build_aci_factory(),
        targets=manifests,
    )
    request = RunRequest(
        repo_url=target.repo_url,
        revision=target.revision,
        timeout_seconds=args.timeout,
    )
    run_id = store.create_run(request)
    print(f"target={target.id} run_id={run_id} backend={'fake' if args.fake else 'docker'}")
    result = runner.run(run_id, request)

    print(f"outcome={result.outcome} verified={result.verified} attempts={result.attempts}")
    print(f"failure_category={result.failure_category}")
    for event in store.read_events(run_id):
        print(f"  {event.sequence} {event.state} {event.message[:120]}")
    report = store.read_text(run_id, "report.md")
    if report:
        print("--- report.md ---")
        print(report)
    if result.verified:
        print(f"RUN TARGET OK: {target.id}")


if __name__ == "__main__":
    main()
