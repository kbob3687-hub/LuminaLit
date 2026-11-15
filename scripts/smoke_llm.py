"""LLM 连通性冒烟：三个调用点各打一枪。

用法（PowerShell）：
$env:PAPERHARNESS_LLM_BASE_URL='https://api.deepseek.com'
$env:PAPERHARNESS_LLM_API_KEY='sk-...'
$env:PAPERHARNESS_LLM_MODEL='deepseek-chat'
.venv/Scripts/python scripts/smoke_llm.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from paperharness.agent.classifier import FailureContext
from paperharness.agent.llm_agent import OpenAICompatibleLLM
from paperharness.contracts.run import Observation, RunRequest


def main() -> None:
    llm = OpenAICompatibleLLM()
    print(f"base_url={llm.base_url} model={llm.model}")

    request = RunRequest(
        repo_url="https://github.com/owner/repo",
        command=["python", "main.py"],
        expected_artifact="out.txt",
    )
    plan = llm.propose_run_plan(request)
    print("plan:", plan.model_dump())

    rule_hit = FailureContext(
        exit_code=1,
        timed_out=False,
        stderr_tail="ModuleNotFoundError: No module named 'yaml'",
    )
    print("classify(rules):", llm.classify_failure(rule_hit))

    llm_fallback = FailureContext(
        exit_code=1, timed_out=False, stderr_tail="Segmentation fault (core dumped)"
    )
    print("classify(llm):", llm.classify_failure(llm_fallback))

    history = [
        Observation(
            ok=True,
            action="read_file",
            summary="read 3 lines from main.py",
            stdout_tail="import yaml\nprint(yaml.safe_load('a: 1'))",
        )
    ]
    action = llm.propose_action(history)
    print("action:", action.model_dump())


if __name__ == "__main__":
    main()
