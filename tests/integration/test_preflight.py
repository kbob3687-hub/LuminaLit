"""静态体检、确认入队及冻结执行：全部使用假模型、假沙箱和假 clone。"""

import base64
import hashlib
import json
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from paperharness.agent.fake import FakeLLM
from paperharness.app import api as api_module
from paperharness.app.api import create_app
from paperharness.contracts.preflight import PreflightResult
from paperharness.contracts.run import RunState
from paperharness.harness import preflight as preflight_module
from paperharness.harness import runner as runner_module
from paperharness.harness.bundle import ManifestBuildError
from paperharness.harness.preflight import MAX_TARGET_FILE_BYTES, PreflightRecord
from paperharness.harness.runner import INSTALL_COMMAND, HarnessRunner
from paperharness.harness.store import RunStore
from paperharness.sandbox.fake import FakeSandboxSession
from paperharness.targets.manifest import TargetManifest

CHECK_BYTES = "# 私有冻结脚本\r\nprint('PRIVATE_SNAPSHOT_SCRIPT')\r\n".encode()
REQUIREMENTS_BYTES = b"snapshot-dependency==1.0\n"
REPO_REQUIREMENTS = "repository-dependency==2.0\n"
PRIVATE_NOTES = "PRIVATE_MAINTAINER_ANSWER"


class RecordingSandbox(FakeSandboxSession):
    def __init__(self, workspace):
        super().__init__(artifact_path="out.txt", workspace=workspace)
        self.executions = []

    def execute(self, command, timeout_seconds):
        # 在实际 execute 边界观察内容，不能仅检查排队时保存了什么。
        self.executions.append({
            "command": list(command),
            "timeout_seconds": timeout_seconds,
            "check": (self.workspace / "check.py").read_bytes(),
            "requirements": (self.workspace / "requirements.txt").read_text(encoding="utf-8"),
        })
        return super().execute(command, timeout_seconds)


@pytest.fixture
def harness(tmp_path, monkeypatch):
    def forbidden_io(*args, **kwargs):
        raise AssertionError("preflight tests must not access the network or subprocesses")

    monkeypatch.setattr(socket, "create_connection", forbidden_io)
    monkeypatch.setattr(subprocess, "run", forbidden_io)
    monkeypatch.setattr(
        runner_module,
        "build_repair_manifest",
        Mock(side_effect=ManifestBuildError("preflight fixture has no git workspace")),
    )
    source_dir = tmp_path / "private-target"
    source_dir.mkdir()
    (source_dir / "check.py").write_bytes(CHECK_BYTES)
    (source_dir / "requirements.txt").write_bytes(REQUIREMENTS_BYTES)
    target = TargetManifest(
        id="maintained-example",
        repo_url="https://github.com/preflight-tests/example",
        revision="a" * 40,
        python_version="3.11",
        command=["python", "-I", "check.py"],
        expected_artifact="out.txt",
        failure_class="missing_dependency",
        repairable=True,
        allowed_actions=["read_file", "install_requirement", "finish_repair"],
        expected_final="succeeded",
        notes=PRIVATE_NOTES,
        source_dir=source_dir,
    )
    store = RunStore(tmp_path / "runs")
    llm = FakeLLM()
    llm_calls = []
    for method in (
        "propose_run_plan", "propose_run_plan_with_workspace", "classify_failure", "propose_action",
    ):
        spy = Mock(side_effect=AssertionError(f"unexpected LLM call: {method}"))
        monkeypatch.setattr(llm, method, spy, raising=False)
        llm_calls.append(spy)
    clones = []
    sandbox_requests = []
    sessions = []

    def cloner(request, run_dir):
        clones.append(request.model_copy(deep=True))
        workspace = run_dir / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "check.py").write_bytes(b"# repository copy, not the frozen check\n")
        (workspace / "requirements.txt").write_text(REPO_REQUIREMENTS, encoding="utf-8")
        return request.revision

    def factory(request, run_dir):
        sandbox_requests.append(request.model_copy(deep=True))
        session = RecordingSandbox(run_dir / "workspace")
        sessions.append(session)
        return session

    runner = HarnessRunner(
        store=store, llm=llm, sandbox_factory=factory, cloner=cloner, targets=[target],
    )
    # 故意提供不同的公开列表，体检必须从 runner.targets 取 manifest。
    app, worker = create_app(store, runner, targets=[{"id": "public-list-only"}])
    client = TestClient(app)  # 不启动 lifespan，先观察确认和排队，再显式启动 worker。
    context = SimpleNamespace(
        store=store, runner=runner, app=app, worker=worker, client=client,
        target=target, source_dir=source_dir, clones=clones, sessions=sessions,
        sandbox_requests=sandbox_requests, llm_calls=llm_calls,
    )
    try:
        yield context
    finally:
        worker.stop()
        client.close()
        for spy in llm_calls:
            spy.assert_not_called()


def create_preflight(harness, **overrides):
    response = harness.client.post("/api/preflights", json={
        "target_id": harness.target.id, **overrides,
    })
    assert response.status_code == 201, response.text
    PreflightResult.model_validate(response.json())
    return response.json()


def confirmation(view):
    return {key: view[key] for key in ("preflight_id", "confirmation_digest")}


def confirm(harness, view):
    response = harness.client.post("/api/runs", json=confirmation(view))
    assert response.status_code == 202, response.text
    assert response.json()["state"] == "queued"
    return response.json()["run_id"]


def run_ids(store):
    return {path.parent.name for path in store.root.glob("*/request.json")}


def record_for(harness, view):
    return PreflightRecord.model_validate(harness.store.read_preflight(view["preflight_id"]))


def assert_error(response, status, code):
    assert response.status_code == status, response.text
    assert response.json()["detail"]["code"] == code


def assert_static(harness):
    assert harness.clones == []
    assert harness.sessions == []
    assert harness.worker.queue.empty()
    assert run_ids(harness.store) == set()
    for spy in harness.llm_calls:
        spy.assert_not_called()


def advance_past_expiry(monkeypatch, view):
    instant = datetime.fromisoformat(view["expires_at"]) + timedelta(seconds=1)

    class AfterExpiry(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)

    monkeypatch.setattr(api_module, "datetime", AfterExpiry)
    monkeypatch.setattr(preflight_module, "datetime", AfterExpiry)


def finish_queued(harness, run_id):
    harness.worker.start()
    harness.worker.queue.join()
    result = harness.store.read_result(run_id)
    assert result is not None
    return result


def test_create_and_read_are_static_private_and_persisted(harness):
    view = create_preflight(harness)
    assert view["target_id"] == harness.target.id
    assert view["repo_url"] == harness.target.repo_url
    assert view["timeout_seconds"] == 300
    assert view["max_repairs"] == 3
    assert view["run_id"] is None
    assert view["plan"] == {
        "resolved_revision": harness.target.revision,
        "python_version": "3.11",
        "command": ["python", "-I", "check.py"],
        "expected_artifact": "out.txt",
        "source": "target_manifest",
    }
    assert datetime.fromisoformat(view["expires_at"]) > datetime.fromisoformat(view["created_at"])
    assert {check["status"] for check in view["checks"]} == {"passed", "warning", "not_checked"}
    assert view["limitations"]
    response = harness.client.get(f"/api/preflights/{view['preflight_id']}")
    assert response.status_code == 200
    assert response.json() == view
    public = json.dumps(view, ensure_ascii=False)
    for private in (
        "failure_class", "notes", "source_dir", "allowed_actions", "expected_final",
        PRIVATE_NOTES, "missing_dependency", "PRIVATE_SNAPSHOT_SCRIPT",
        "snapshot-dependency==1.0", str(harness.source_dir), harness.source_dir.as_posix(),
    ):
        assert private not in public
    for content in (CHECK_BYTES, REQUIREMENTS_BYTES):
        assert hashlib.sha256(content).hexdigest() in public
        assert base64.b64encode(content).decode() not in public
    path = harness.store.root / "preflights" / f"{view['preflight_id']}.json"
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert set(stored) == {"view", "request", "target"}
    assert set(stored["target"]) == {"manifest", "files"}
    record = record_for(harness, view)
    assert record.check_digest(view["confirmation_digest"])
    assert record.target.manifest.source_dir is None
    assert record.target.manifest.notes == PRIVATE_NOTES
    assert {name: base64.b64decode(data) for name, data in record.target.files.items()} == {
        "check.py": CHECK_BYTES, "requirements.txt": REQUIREMENTS_BYTES,
    }
    assert_static(harness)


@pytest.mark.parametrize("payload", [
    {}, {"target_id": ""}, {"target_id": "x" * 129}, {"target_id": None},
    {"timeout_seconds": 29}, {"timeout_seconds": 901}, {"timeout_seconds": 30.5},
    {"timeout_seconds": None}, {"max_repairs": -1}, {"max_repairs": 6},
    {"max_repairs": 0.5}, {"max_repairs": None},
    {"repo_url": "https://github.com/other/repository"},
    {"revision": "b" * 40}, {"command": ["python", "other.py"]},
    {"python_version": "3.10"}, {"expected_artifact": "other.txt"},
    {"preflight_id": "b" * 32}, {"unexpected": True},
])
def test_preflight_rejects_invalid_inputs_without_side_effects(harness, payload):
    body = {"target_id": harness.target.id, **payload} if payload else {}
    response = harness.client.post("/api/preflights", json=body)
    assert response.status_code == 422, response.text
    assert not list(harness.store.root.glob("preflights/*.json"))
    assert_static(harness)


@pytest.mark.parametrize("timeout, repairs", [(30, 0), (900, 5)])
def test_preflight_accepts_schema_boundaries(harness, timeout, repairs):
    harness.target.id = "x" * 128
    view = create_preflight(harness, timeout_seconds=timeout, max_repairs=repairs)
    record = record_for(harness, view)
    assert view["timeout_seconds"] == record.request.timeout_seconds == timeout
    assert view["max_repairs"] == record.request.max_repairs == repairs
    assert_static(harness)


@pytest.mark.parametrize("extra", [
    {"repo_url": "https://github.com/other/repository"}, {"revision": "b" * 40},
    {"command": ["python", "other.py"]}, {"expected_artifact": "other.txt"},
    {"python_version": "3.10"}, {"timeout_seconds": 300}, {"max_repairs": 3},
    {"target_id": "maintained-example"}, {"unexpected": True},
])
def test_confirmation_forbids_mixed_requests_and_overrides(harness, extra):
    view = create_preflight(harness)
    response = harness.client.post("/api/runs", json={**confirmation(view), **extra})
    assert response.status_code == 422, response.text
    assert record_for(harness, view).view.run_id is None
    assert_static(harness)


@pytest.mark.parametrize("payload", [
    {}, {"preflight_id": "a" * 32}, {"confirmation_digest": "a" * 64},
    {"preflight_id": "../outside", "confirmation_digest": "a" * 64},
    {"preflight_id": "a" * 31, "confirmation_digest": "a" * 64},
    {"preflight_id": "G" * 32, "confirmation_digest": "a" * 64},
    {"preflight_id": "a" * 32, "confirmation_digest": "g" * 64},
    {"preflight_id": "a" * 32, "confirmation_digest": "a" * 63},
    {"repo_url": "https://github.com/a/b", "preflight_id": "a" * 32},
    {"repo_url": "https://github.com/a/b", "confirmation_digest": "a" * 64},
    {"repo_url": "https://github.com/a/b", "unexpected": True},
])
def test_run_union_rejects_malformed_or_incomplete_confirmation(harness, payload):
    response = harness.client.post("/api/runs", json=payload)
    assert response.status_code == 422, response.text
    assert_static(harness)


def test_unknown_target_and_preflight_ids(harness):
    for target_id in ("missing", "public-list-only"):
        response = harness.client.post("/api/preflights", json={"target_id": target_id})
        assert_error(response, 404, "target_not_found")
    for preflight_id in ("f" * 32, "bad..id", "not-an-id"):
        response = harness.client.get(f"/api/preflights/{preflight_id}")
        assert_error(response, 404, "preflight_not_found")
    response = harness.client.post("/api/runs", json={
        "preflight_id": "f" * 32, "confirmation_digest": "f" * 64,
    })
    assert_error(response, 404, "preflight_not_found")
    assert_static(harness)


def test_wrong_digest_does_not_consume_preflight(harness):
    view = create_preflight(harness)
    body = confirmation(view)
    body["confirmation_digest"] = "0" * 64
    assert body["confirmation_digest"] != view["confirmation_digest"]
    assert_error(harness.client.post("/api/runs", json=body), 409, "confirmation_mismatch")
    assert record_for(harness, view).view.run_id is None
    assert_static(harness)
    run_id = confirm(harness, view)
    assert record_for(harness, view).view.run_id == run_id
    # 即使已受理，也不能用错误摘要取到另一次确认的运行。
    assert_error(harness.client.post("/api/runs", json=body), 409, "confirmation_mismatch")
    assert harness.worker.queue.qsize() == 1


def test_expired_first_confirmation_is_rejected_and_new_preflight_can_run(harness, monkeypatch):
    view = create_preflight(harness)
    advance_past_expiry(monkeypatch, view)
    response = harness.client.post("/api/runs", json=confirmation(view))
    assert_error(response, 409, "preflight_expired")
    assert record_for(harness, view).view.run_id is None
    assert harness.client.get(f"/api/preflights/{view['preflight_id']}").json() == view
    assert_static(harness)
    renewed = create_preflight(harness)
    assert renewed["preflight_id"] != view["preflight_id"]
    confirm(harness, renewed)


@pytest.mark.parametrize("state", [
    RunState.QUEUED, RunState.TESTING, RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELLED,
])
def test_accepted_confirmation_remains_idempotent_after_expiry(harness, monkeypatch, state):
    view = create_preflight(harness)
    run_id = confirm(harness, view)
    harness.store.write_status(run_id, state, {})
    advance_past_expiry(monkeypatch, view)
    can_accept = Mock(return_value=False)
    submit = Mock(return_value=False)
    monkeypatch.setattr(harness.worker, "can_accept", can_accept)
    monkeypatch.setattr(harness.worker, "submit", submit)
    response = harness.client.post("/api/runs", json=confirmation(view))
    assert response.status_code == 202
    assert response.json() == {"run_id": run_id, "state": state.value}
    can_accept.assert_not_called()
    submit.assert_not_called()
    assert run_ids(harness.store) == {run_id}
    assert harness.worker.queue.qsize() == 1


def test_concurrent_confirmation_retries_enqueue_exactly_once(harness, monkeypatch):
    view = create_preflight(harness)
    barrier = Barrier(8)
    submit = Mock(wraps=harness.worker.submit)
    monkeypatch.setattr(harness.worker, "submit", submit)

    def retry(_):
        barrier.wait(timeout=10)
        return harness.client.post("/api/runs", json=confirmation(view))

    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(retry, range(8)))
    assert all(response.status_code == 202 for response in responses)
    ids = {response.json()["run_id"] for response in responses}
    assert len(ids) == 1
    assert run_ids(harness.store) == ids
    assert harness.worker.queue.qsize() == 1
    submit.assert_called_once_with(next(iter(ids)))
    assert record_for(harness, view).view.run_id in ids
    assert harness.clones == harness.sessions == []


@pytest.mark.parametrize("rejected_at", ["capacity", "submit"])
def test_queue_full_rolls_back_and_same_confirmation_can_retry(harness, monkeypatch, rejected_at):
    view = create_preflight(harness)
    before = harness.store.read_preflight(view["preflight_id"])
    with monkeypatch.context() as patch:
        if rejected_at == "capacity":
            patch.setattr(harness.worker, "max_queue", 1)
            harness.worker.queue.put_nowait("occupied")
        else:
            patch.setattr(harness.worker, "submit", Mock(return_value=False))
        response = harness.client.post("/api/runs", json=confirmation(view))
        assert_error(response, 429, "queue_full")
        assert harness.store.read_preflight(view["preflight_id"]) == before
        assert {path.name for path in harness.store.root.iterdir()} == {"preflights"}
        if rejected_at == "capacity":
            assert harness.worker.queue.get_nowait() == "occupied"
            harness.worker.queue.task_done()
    assert_static(harness)
    run_id = confirm(harness, view)
    assert record_for(harness, view).view.run_id == run_id
    assert run_ids(harness.store) == {run_id}
    assert harness.worker.queue.qsize() == 1


def test_reservation_save_failure_does_not_create_run(harness, monkeypatch):
    view = create_preflight(harness)
    create_run = Mock(wraps=harness.store.create_run)
    submit = Mock(wraps=harness.worker.submit)
    monkeypatch.setattr(harness.store, "create_run", create_run)
    monkeypatch.setattr(harness.worker, "submit", submit)
    write_json = harness.store._write_json
    attempts = []

    def fail_first_reservation(path, data):
        if path.name == f"{view['preflight_id']}.json":
            attempts.append(data["view"]["run_id"])
            if len(attempts) == 1:
                raise OSError("reservation storage unavailable")
        return write_json(path, data)

    monkeypatch.setattr(harness.store, "_write_json", fail_first_reservation)
    response = harness.client.post("/api/runs", json=confirmation(view))
    assert_error(response, 503, "storage_unavailable")
    assert "同一体检记录和确认摘要重试" in response.json()["detail"]["message"]
    assert len(attempts) == 1 and len(attempts[0]) == 32
    create_run.assert_not_called()
    submit.assert_not_called()
    assert record_for(harness, view).view.run_id is None
    assert harness.client.get(f"/api/preflights/{view['preflight_id']}").json() == view
    assert {path.name for path in harness.store.root.iterdir()} == {"preflights"}
    assert_static(harness)

    run_id = confirm(harness, view)
    assert attempts[-1] == run_id
    assert record_for(harness, view).view.run_id == run_id
    assert run_ids(harness.store) == {run_id}
    create_run.assert_called_once()
    submit.assert_called_once_with(run_id)


@pytest.mark.parametrize("failed_file", ["request.json", "preflight.json", "status.json"])
def test_creation_write_failure_preserves_reservation_for_retry(harness, monkeypatch, failed_file):
    view = create_preflight(harness)
    write_json = harness.store._write_json
    submit = harness.worker.submit
    writes = []
    failed = False

    def fail_once(path, data):
        nonlocal failed
        writes.append(path.name)
        if path.name == failed_file and not failed:
            # 在原子写入之前失败，不模拟 replace 成功后又抛异常。
            failed = True
            assert record_for(harness, view).view.run_id == path.parent.name
            raise OSError(f"cannot write {failed_file}")
        return write_json(path, data)

    def checked_submit(run_id):
        writes.append("submit")
        assert harness.store.read_status(run_id) == {"state": "queued"}
        record = record_for(harness, view)
        assert record.view.run_id == run_id
        assert json.loads(harness.store.read_text(run_id, "preflight.json")) == record.model_dump(
            mode="json"
        )
        assert harness.store.read_request(run_id) == record.request
        return submit(run_id)

    submit_spy = Mock(side_effect=checked_submit)
    monkeypatch.setattr(harness.store, "_write_json", fail_once)
    monkeypatch.setattr(harness.worker, "submit", submit_spy)
    response = harness.client.post("/api/runs", json=confirmation(view))
    assert_error(response, 503, "storage_unavailable")
    assert "同一体检记录和确认摘要重试" in response.json()["detail"]["message"]
    run_id = record_for(harness, view).view.run_id
    assert run_id is not None
    fetched = harness.client.get(f"/api/preflights/{view['preflight_id']}")
    assert fetched.status_code == 200
    assert fetched.json() == {**view, "run_id": run_id}
    # 公开预留 ID 不等于创建成功；没有 status、执行或提交记录。
    assert harness.store.read_status(run_id) is None
    assert_error(harness.client.get(f"/api/runs/{run_id}"), 404, "run_not_found")
    assert harness.store.read_result(run_id) is None
    assert harness.worker.queue.empty()
    assert harness.clones == harness.sessions == []
    submit_spy.assert_not_called()
    assert {path.name for path in harness.store.root.iterdir()} == {"preflights", run_id}
    creation_files = ["request.json", "preflight.json", "status.json"]
    assert writes == [f"{view['preflight_id']}.json"] + creation_files[
        :creation_files.index(failed_file) + 1
    ]

    advance_past_expiry(monkeypatch, view)
    # 已预留的半创建 run 排队受阻时也保留身份，且不受首次确认过期校验限制。
    with monkeypatch.context() as patch:
        patch.setattr(harness.worker, "can_accept", Mock(return_value=False))
        assert_error(harness.client.post("/api/runs", json=confirmation(view)), 429, "queue_full")
    assert record_for(harness, view).view.run_id == run_id
    submit_spy.assert_not_called()
    writes.clear()
    assert confirm(harness, view) == run_id
    # 最后写 queued 到 submit 之间没有额外必须落盘的操作。
    assert writes == ["request.json", "preflight.json", "status.json", "submit"]
    assert record_for(harness, view).check_digest(view["confirmation_digest"])
    assert run_ids(harness.store) == {run_id}
    assert harness.worker.queue.qsize() == 1
    submit_spy.assert_called_once_with(run_id)
    assert confirm(harness, view) == run_id
    submit_spy.assert_called_once_with(run_id)


@pytest.mark.parametrize("crash_at", [
    "create", "request.json", "preflight.json", "status.json", "submit",
])
def test_restart_completes_only_statusless_reservations(harness, monkeypatch, crash_at):
    view = create_preflight(harness)

    class SimulatedCrash(RuntimeError):
        pass

    write_json = harness.store._write_json

    def crash_before_write(path, data):
        if path.name == crash_at:
            raise SimulatedCrash()
        return write_json(path, data)

    with monkeypatch.context() as patch:
        if crash_at == "create":
            patch.setattr(harness.store, "create_run", Mock(side_effect=SimulatedCrash))
        elif crash_at == "submit":
            patch.setattr(harness.worker, "submit", Mock(side_effect=SimulatedCrash))
        else:
            patch.setattr(harness.store, "_write_json", crash_before_write)
        with pytest.raises(SimulatedCrash):
            harness.client.post("/api/runs", json=confirmation(view))
    run_id = record_for(harness, view).view.run_id
    assert run_id is not None
    assert harness.worker.queue.empty()
    if crash_at == "submit":
        assert harness.store.read_status(run_id) == {"state": "queued"}
    else:
        assert harness.store.read_status(run_id) is None
    if crash_at == "create":
        assert not harness.store.run_dir(run_id).exists()

    advance_past_expiry(monkeypatch, view)
    restored_store = RunStore(harness.store.root)
    restored_runner = HarnessRunner(
        store=restored_store, llm=harness.runner.llm,
        sandbox_factory=harness.runner.sandbox_factory, cloner=harness.runner.cloner,
        targets=[],
    )
    app, worker = create_app(restored_store, restored_runner)
    # 真实执行 lifespan 恢复，但不消费队列，便于验证没有自动续跑。
    start = Mock()
    submit = Mock(wraps=worker.submit)
    monkeypatch.setattr(worker, "start", start)
    monkeypatch.setattr(worker, "submit", submit)
    with TestClient(app) as client:
        start.assert_called_once_with()
        submit.assert_not_called()
        assert worker.queue.empty()
        fetched = client.get(f"/api/preflights/{view['preflight_id']}")
        assert fetched.status_code == 200
        assert fetched.json() == {**view, "run_id": run_id}
        if crash_at == "submit":
            assert restored_store.read_status(run_id) == {
                "state": "failed", "failure_category": "service_interrupted",
            }
            assert restored_store.read_result(run_id).failure_category == "service_interrupted"
            state = "failed"
        else:
            assert restored_store.read_status(run_id) is None
            assert restored_store.read_result(run_id) is None
            assert_error(client.get(f"/api/runs/{run_id}"), 404, "run_not_found")
            state = "queued"
        for _ in range(2):
            response = client.post("/api/runs", json=confirmation(view))
            assert response.status_code == 202
            assert response.json() == {"run_id": run_id, "state": state}
        if crash_at == "submit":
            submit.assert_not_called()
            assert worker.queue.empty()
        else:
            submit.assert_called_once_with(run_id)
            assert worker.queue.qsize() == 1
        assert {path.name for path in restored_store.root.iterdir()} == {"preflights", run_id}
        assert run_ids(restored_store) == {run_id}
        snapshot = PreflightRecord.model_validate_json(
            restored_store.read_text(run_id, "preflight.json")
        )
        assert snapshot.view.run_id == run_id
        assert snapshot.check_digest(view["confirmation_digest"])
        assert harness.clones == harness.sessions == []


def test_queue_rejection_only_removes_current_unsubmitted_run(harness, monkeypatch):
    accepted = create_preflight(harness)
    accepted_id = confirm(harness, accepted)
    view = create_preflight(harness)
    before = harness.store.read_preflight(view["preflight_id"])
    with monkeypatch.context() as patch:
        submit = Mock(return_value=False)
        patch.setattr(harness.worker, "submit", submit)
        response = harness.client.post("/api/runs", json=confirmation(view))
        assert_error(response, 429, "queue_full")
    rejected_id = submit.call_args.args[0]
    assert rejected_id != accepted_id
    assert not harness.store.run_dir(rejected_id).exists()
    assert harness.store.read_preflight(view["preflight_id"]) == before
    assert record_for(harness, accepted).view.run_id == accepted_id
    assert harness.store.read_status(accepted_id) == {"state": "queued"}
    assert run_ids(harness.store) == {accepted_id}
    assert harness.worker.queue.qsize() == 1
    retried_id = confirm(harness, view)
    assert run_ids(harness.store) == {accepted_id, retried_id}
    assert harness.worker.queue.qsize() == 2


def test_queue_rollback_save_failure_keeps_reservation_retryable(harness, monkeypatch):
    view = create_preflight(harness)
    write_preflight = harness.store.write_preflight

    def fail_to_clear(preflight_id, data):
        if data["view"]["run_id"] is None:
            raise OSError("cannot clear reservation")
        return write_preflight(preflight_id, data)

    with monkeypatch.context() as patch:
        submit = Mock(return_value=False)
        patch.setattr(harness.worker, "submit", submit)
        patch.setattr(harness.store, "write_preflight", fail_to_clear)
        response = harness.client.post("/api/runs", json=confirmation(view))
        assert_error(response, 503, "storage_unavailable")
    run_id = submit.call_args.args[0]
    assert record_for(harness, view).view.run_id == run_id
    assert not harness.store.run_dir(run_id).exists()
    assert harness.worker.queue.empty()
    assert confirm(harness, view) == run_id
    assert harness.worker.queue.qsize() == 1


def test_idempotency_survives_new_store_and_restart_recovery(harness, monkeypatch):
    view = create_preflight(harness)
    run_id = confirm(harness, view)
    advance_past_expiry(monkeypatch, view)
    restored_store = RunStore(harness.store.root)
    assert restored_store.recover_interrupted() == [run_id]
    restored_runner = HarnessRunner(
        store=restored_store, llm=harness.runner.llm,
        sandbox_factory=harness.runner.sandbox_factory, cloner=harness.runner.cloner,
        targets=[],
    )
    app, worker = create_app(restored_store, restored_runner)
    client = TestClient(app)
    try:
        fetched = client.get(f"/api/preflights/{view['preflight_id']}")
        assert fetched.status_code == 200
        assert fetched.json() == {**view, "run_id": run_id}
        response = client.post("/api/runs", json=confirmation(view))
        assert response.status_code == 202
        assert response.json() == {"run_id": run_id, "state": "failed"}
        assert restored_store.read_result(run_id).failure_category == "service_interrupted"
        assert run_ids(restored_store) == {run_id}
        assert worker.queue.empty()
        assert harness.clones == harness.sessions == []
    finally:
        worker.stop()
        client.close()


def test_each_preflight_has_new_identity_and_keeps_previous_snapshot(harness):
    first = create_preflight(harness)
    second = create_preflight(harness)
    (harness.source_dir / "check.py").write_bytes(b"print('changed')\n")
    harness.target.command[:] = ["python", "changed.py"]
    third = create_preflight(harness)
    assert len({view["preflight_id"] for view in (first, second, third)}) == 3
    assert len({view["confirmation_digest"] for view in (first, second, third)}) == 3
    assert third["plan"]["command"] == ["python", "changed.py"]
    for view in (first, second):
        assert harness.client.get(f"/api/preflights/{view['preflight_id']}").json() == view
        assert base64.b64decode(record_for(harness, view).target.files["check.py"]) == CHECK_BYTES
    assert_static(harness)


@pytest.mark.parametrize("change_at", ["before_confirmation", "after_confirmation"])
@pytest.mark.parametrize("source_change", ["overwrite", "remove"])
def test_worker_uses_frozen_snapshot_despite_configuration_changes_and_expiry(
    harness, monkeypatch, change_at, source_change,
):
    view = create_preflight(harness, timeout_seconds=73, max_repairs=2)
    frozen = record_for(harness, view)

    def change_source():
        harness.target.command[:] = ["python", "changed.py"]
        harness.target.revision = "b" * 40
        harness.target.python_version = "3.10"
        harness.target.expected_artifact = "changed.txt"
        harness.target.notes = "changed private answer"
        for name in ("check.py", "requirements.txt"):
            path = harness.source_dir / name
            if source_change == "remove":
                path.unlink()
            else:
                path.write_text("changed-content\n", encoding="utf-8")
        if source_change == "remove":
            harness.source_dir.rmdir()

    if change_at == "before_confirmation":
        change_source()
    run_id = confirm(harness, view)
    persisted = PreflightRecord.model_validate_json(harness.store.read_text(run_id, "preflight.json"))
    assert persisted.check_digest(view["confirmation_digest"])
    assert persisted.request == frozen.request
    assert persisted.target == frozen.target
    assert persisted.view.plan == frozen.view.plan
    assert harness.store.read_request(run_id) == frozen.request
    if change_at == "after_confirmation":
        change_source()
    advance_past_expiry(monkeypatch, view)
    result = finish_queued(harness, run_id)
    assert result.outcome == "succeeded", result.model_dump()
    assert result.verified is True
    assert result.resolved_revision == frozen.request.revision
    assert harness.clones == [frozen.request]
    assert harness.sandbox_requests == [frozen.request]
    assert len(harness.sessions) == 1
    session = harness.sessions[0]
    assert session.commands == [INSTALL_COMMAND, view["plan"]["command"]]
    for execution in session.executions:
        assert execution["check"] == CHECK_BYTES
        assert execution["requirements"] == REPO_REQUIREMENTS + REQUIREMENTS_BYTES.decode()
        assert execution["timeout_seconds"] == 73
    assert session.closed
    assert json.loads(harness.store.read_text(run_id, "run_plan.json")) == view["plan"]
    environment = json.loads(harness.store.read_text(run_id, "environment.json"))
    assert environment["python_version"] == "3.11"
    assert environment["command"] == view["plan"]["command"]
    response = harness.client.post("/api/runs", json=confirmation(view))
    assert response.status_code == 202
    assert response.json() == {"run_id": run_id, "state": "succeeded"}
    assert harness.worker.queue.empty()


@pytest.mark.parametrize("field, value", [
    ("repo_url", "https://github.com/changed/repository"), ("revision", "b" * 40),
    ("command", ["python", "changed.py"]), ("python_version", "3.10"),
    ("expected_artifact", "changed.txt"), ("timeout_seconds", 31), ("max_repairs", 0),
])
def test_worker_rejects_tampered_request_before_clone_or_sandbox(harness, field, value):
    view = create_preflight(harness)
    run_id = confirm(harness, view)
    request = harness.store.read_request(run_id).model_dump(mode="json")
    request[field] = value
    harness.store.write_text(run_id, "request.json", json.dumps(request))
    result = finish_queued(harness, run_id)
    assert result.outcome == "failed"
    assert result.failure_category == "confirmation_mismatch"
    assert not result.verified
    assert harness.clones == harness.sessions == []
    assert not any(event.kind == "sandbox_start" for event in harness.store.read_events(run_id))


@pytest.mark.parametrize("location", ["stored_preflight", "run_snapshot"])
@pytest.mark.parametrize("component", ["request", "plan", "manifest", "files"])
def test_tampered_snapshot_digest_is_rejected(harness, location, component):
    view = create_preflight(harness)
    if location == "run_snapshot":
        run_id = confirm(harness, view)
        record = json.loads(harness.store.read_text(run_id, "preflight.json"))
    else:
        record = harness.store.read_preflight(view["preflight_id"])
    if component == "request":
        record["request"]["timeout_seconds"] = 31
    elif component == "plan":
        record["view"]["plan"]["command"] = ["python", "changed.py"]
    elif component == "manifest":
        record["target"]["manifest"]["notes"] = "changed private answer"
    else:
        record["target"]["files"]["check.py"] = base64.b64encode(b"changed").decode()
    if location == "stored_preflight":
        harness.store.write_preflight(view["preflight_id"], record)
        response = harness.client.get(f"/api/preflights/{view['preflight_id']}")
        assert_error(response, 409, "confirmation_mismatch")
        response = harness.client.post("/api/runs", json=confirmation(view))
        assert_error(response, 409, "confirmation_mismatch")
        assert_static(harness)
    else:
        harness.store.write_text(run_id, "preflight.json", json.dumps(record))
        result = finish_queued(harness, run_id)
        assert result.outcome == "failed"
        assert result.failure_category == "confirmation_mismatch"
        assert harness.clones == harness.sessions == []


def test_revision_mismatch_fails_before_container_or_llm(harness, monkeypatch):
    view = create_preflight(harness)
    run_id = confirm(harness, view)
    cloner = Mock(return_value="b" * 40)
    monkeypatch.setattr(harness.runner, "cloner", cloner)
    result = finish_queued(harness, run_id)
    assert result.outcome == "failed"
    assert result.failure_category == "confirmed_revision_mismatch"
    assert result.resolved_revision == "b" * 40
    assert not result.verified
    cloner.assert_called_once_with(harness.store.read_request(run_id), harness.store.run_dir(run_id))
    assert harness.sessions == []
    assert not any(event.kind == "sandbox_start" for event in harness.store.read_events(run_id))


@pytest.mark.parametrize("invalid", ["oversized", "invalid_utf8", "directory", "empty_command"])
def test_unavailable_target_rejected_without_partial_record(harness, invalid):
    path = harness.source_dir / "check.py"
    if invalid == "oversized":
        path.write_bytes(b"x" * (MAX_TARGET_FILE_BYTES + 1))
    elif invalid == "invalid_utf8":
        path.write_bytes(b"\xff")
    elif invalid == "directory":
        path.unlink()
        path.mkdir()
    else:
        harness.target.command = []
    response = harness.client.post("/api/preflights", json={"target_id": harness.target.id})
    assert_error(response, 409, "preflight_unavailable")
    assert not list(harness.store.root.glob("preflights/*.json"))
    assert_static(harness)


def test_injection_size_limit_is_inclusive(harness):
    content = b"#" + b"x" * (MAX_TARGET_FILE_BYTES - 1)
    (harness.source_dir / "check.py").write_bytes(content)
    view = create_preflight(harness)
    assert base64.b64decode(record_for(harness, view).target.files["check.py"]) == content
    assert_static(harness)


@pytest.mark.parametrize("name", ["check.py", "requirements.txt"])
def test_symlink_injections_are_rejected(harness, tmp_path, name):
    destination = tmp_path / "outside-target.txt"
    destination.write_text("private external content", encoding="utf-8")
    link = harness.source_dir / name
    link.unlink()
    try:
        link.symlink_to(destination)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"environment cannot create symlinks: {exc}")
    response = harness.client.post("/api/preflights", json={"target_id": harness.target.id})
    assert_error(response, 409, "preflight_unavailable")
    assert not list(harness.store.root.glob("preflights/*.json"))
    assert_static(harness)
