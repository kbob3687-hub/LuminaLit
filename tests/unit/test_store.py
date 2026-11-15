from paperharness.contracts.run import RunRequest, RunState
from paperharness.harness.store import RunStore


def test_store_appends_and_reads_events(tmp_path):
    store = RunStore(tmp_path)
    run_id = store.create_run(RunRequest(repo_url="https://github.com/a/b"))
    store.append_event(run_id, RunState.QUEUED, "queued", "task queued")
    store.append_event(run_id, RunState.PREPARING, "preparing", "workspace ready")

    events = store.read_events(run_id, after=0)
    assert [event.sequence for event in events] == [1, 2]

    later = store.read_events(run_id, after=1)
    assert [event.sequence for event in later] == [2]


def test_store_status_is_atomic_and_readable(tmp_path):
    store = RunStore(tmp_path)
    run_id = store.create_run(RunRequest(repo_url="https://github.com/a/b"))
    store.write_status(run_id, RunState.TESTING, {"attempts": 1})
    status = store.read_status(run_id)
    assert status["state"] == "testing"
