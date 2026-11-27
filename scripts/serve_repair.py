"""Local repair-case UI backend. Reading history never calls the configured model.

Creating a confirmed repair run uses the real configured model and Docker.
No FakeLLM fallback. Credentials stay in the backend process.
"""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import uvicorn
from accept_repair import load_model_settings

from paperharness.aci import PaperACI
from paperharness.agent.llm_agent import OpenAICompatibleLLM
from paperharness.app.api import create_app
from paperharness.harness.runner import HarnessRunner
from paperharness.harness.store import RunStore
from paperharness.sandbox.docker_sandbox import create_docker_sandbox
from paperharness.targets import load_targets, public_view


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8017)
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args()
    if args.env_file:
        load_model_settings(args.env_file)
    if not os.environ.get("PAPERHARNESS_LLM_API_KEY"):
        raise ValueError("Configure a real model before enabling new repair runs")
    targets = [t for t in load_targets(ROOT / "targets")
               if t.id in {"paper-lorenz", "paper-lorenz-repair"}]
    store = RunStore(ROOT / "runs")
    runner = HarnessRunner(
        store=store, llm=OpenAICompatibleLLM(), aci_factory=PaperACI,
        targets=targets, sandbox_factory=create_docker_sandbox,
    )
    app, _ = create_app(store=store, runner=runner, targets=public_view(targets))
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
