"""组装入口：uvicorn paperharness.app.main:app 启动完整服务。

环境变量：
- PAPERHARNESS_SANDBOX：docker（默认）或 fake
- PAPERHARNESS_RUNS：run 存储目录，默认 ./runs
"""

import os
from pathlib import Path

from paperharness.agent.fake import FakeLLM
from paperharness.app.api import create_app
from paperharness.harness.runner import HarnessRunner
from paperharness.harness.store import RunStore
from paperharness.sandbox.docker_sandbox import create_docker_sandbox
from paperharness.sandbox.fake import FakeSandboxSession


def build_app(runs_root: str | Path | None = None, backend: str | None = None):
    runs_root = Path(runs_root or os.environ.get("PAPERHARNESS_RUNS", "runs"))
    backend = (backend or os.environ.get("PAPERHARNESS_SANDBOX", "docker")).lower()
    if backend == "docker":
        factory = create_docker_sandbox
    elif backend == "fake":

        def factory(request, run_dir):
            return FakeSandboxSession(workspace=run_dir / "workspace")

    else:
        raise ValueError(f"unknown sandbox backend: {backend}")

    store = RunStore(runs_root)
    runner = HarnessRunner(store=store, llm=FakeLLM(), sandbox_factory=factory)
    return create_app(store=store, runner=runner)


app, worker = build_app()
