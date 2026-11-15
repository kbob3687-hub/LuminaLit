import pytest
from pydantic import ValidationError

from paperharness.contracts.run import RunRequest, RunState


def test_run_request_defaults():
    request = RunRequest(repo_url="https://github.com/example/repo")
    assert request.max_repairs == 3
    assert request.command is None


def test_run_request_rejects_bad_repair_budget():
    with pytest.raises(ValidationError):
        RunRequest(repo_url="https://github.com/example/repo", max_repairs=9)


def test_run_state_values():
    assert RunState.SUCCEEDED.value == "succeeded"
