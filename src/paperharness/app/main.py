"""组装入口：uvicorn paperharness.app.main:app 启动完整服务。

环境变量：
- PAPERHARNESS_SANDBOX：docker（默认）或 fake
- PAPERHARNESS_AGENT：auto（默认，有 LLM 配置就用真模型）、llm、fake
- PAPERHARNESS_LLM_BASE_URL / PAPERHARNESS_LLM_API_KEY / PAPERHARNESS_LLM_MODEL
- PAPERHARNESS_RUNS：run 存储目录，默认 ./runs
- PAPERHARNESS_TARGETS：靶场目录，默认 ./targets
"""

import os
from pathlib import Path

from paperharness.agent.fake import FakeLLM
from paperharness.agent.llm_agent import OpenAICompatibleLLM
from paperharness.app.api import create_app
from paperharness.harness.runner import HarnessRunner
from paperharness.harness.store import RunStore
from paperharness.sandbox.docker_sandbox import create_docker_sandbox
from paperharness.sandbox.fake import FakeSandboxSession
from paperharness.targets import load_targets, public_view


def _build_llm():
    backend = os.environ.get("PAPERHARNESS_AGENT", "auto").lower()
    if backend == "llm":
        return OpenAICompatibleLLM()
    if backend == "fake":
        return FakeLLM()
    if backend == "auto":
        configured = all(
            os.environ.get(name)
            for name in (
                "PAPERHARNESS_LLM_BASE_URL",
                "PAPERHARNESS_LLM_MODEL",
                "PAPERHARNESS_LLM_API_KEY",
            )
        )
        return OpenAICompatibleLLM() if configured else FakeLLM()
    raise ValueError(f"unknown agent backend: {backend}")


def _build_aci_factory():
    try:
        from paperharness.aci import PaperACI
    except ImportError:
        return None
    return PaperACI


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

    targets_root = Path(os.environ.get("PAPERHARNESS_TARGETS", "targets"))
    manifests = load_targets(targets_root) if targets_root.is_dir() else []
    targets = public_view(manifests) if manifests else None

    store = RunStore(runs_root)
    runner = HarnessRunner(
        store=store,
        llm=_build_llm(),
        sandbox_factory=factory,
        aci_factory=_build_aci_factory(),
        targets=manifests,
    )
    return create_app(store=store, runner=runner, targets=targets)


app, worker = build_app()
