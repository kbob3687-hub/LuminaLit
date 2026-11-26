"""Local scientific-case preview: real Docker, one maintained target, no model credentials.

Run `python scripts/serve_lorenz.py --port 8016` from the project root.
The general application remains `paperharness.app.main:app`.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import uvicorn
from accept_lorenz import NoModel

from paperharness.app.api import create_app
from paperharness.harness.runner import HarnessRunner
from paperharness.harness.store import RunStore
from paperharness.sandbox.docker_sandbox import create_docker_sandbox
from paperharness.targets import load_targets, public_view


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8016)
    args = parser.parse_args()
    targets = [target for target in load_targets(ROOT / "targets") if target.id == "paper-lorenz"]
    store = RunStore(ROOT / "runs")
    runner = HarnessRunner(
        store=store, llm=NoModel(), targets=targets, sandbox_factory=create_docker_sandbox,
    )
    app, _ = create_app(store=store, runner=runner, targets=public_view(targets))
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
