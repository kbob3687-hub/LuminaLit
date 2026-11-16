import time

from paperharness.app.worker import RunWorker
from paperharness.contracts.run import RunRequest, RunState
from paperharness.harness.store import RunStore


class ExplodingRunner:
    """第一个 run 抛未分类异常，第二个正常——验证 worker 存活。"""

    def __init__(self, store: RunStore):
        self.store = store
        self.calls: list[str] = []

    def run(self, run_id, request):
        self.calls.append(run_id)
        if len(self.calls) == 1:
            raise ValueError("boom inside runner")
        self.store.write_status(run_id, RunState.SUCCEEDED, {})

    def abort(self, run_id):
        return False


def test_worker_survives_runner_crash(tmp_path):
    store = RunStore(tmp_path)
    runner = ExplodingRunner(store)
    worker = RunWorker(store=store, runner=runner)
    first = store.create_run(RunRequest(repo_url="https://github.com/a/b"))
    second = store.create_run(RunRequest(repo_url="https://github.com/a/b"))

    worker.start()
    assert worker.submit(first)
    assert worker.submit(second)

    status_first = status_second = None
    deadline = time.time() + 5
    while time.time() < deadline:
        status_first = store.read_status(first)
        status_second = store.read_status(second)
        if (
            status_first.get("state") == RunState.FAILED.value
            and status_second.get("state") == RunState.SUCCEEDED.value
        ):
            break
        time.sleep(0.02)
    worker.stop()

    # 第一个：internal_error 兜底，不留僵尸状态
    assert status_first["state"] == "failed"
    assert status_first["failure_category"] == "internal_error"
    assert store.read_result(first).failure_category == "internal_error"
    assert "internal_error" in store.read_text(first, "report.md")
    # 第二个：worker 线程活着并跑完了后续任务
    assert status_second["state"] == "succeeded"
    assert runner.calls == [first, second]


def test_recover_interrupted_marks_stale_runs(tmp_path):
    store = RunStore(tmp_path)
    run_id = store.create_run(RunRequest(repo_url="https://github.com/a/b"))
    store.write_status(run_id, RunState.TESTING, {})  # 模拟服务崩溃时的现场

    assert store.recover_interrupted() == [run_id]
    status = store.read_status(run_id)
    assert status["state"] == "failed"
    assert status["failure_category"] == "service_interrupted"
    result = store.read_result(run_id)
    assert result.outcome == "failed"
    assert result.failure_category == "service_interrupted"

    # 幂等：终态 run 不重复处理
    assert store.recover_interrupted() == []
    assert store.read_status(run_id)["state"] == "failed"
