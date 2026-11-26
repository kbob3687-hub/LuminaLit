from datetime import UTC, datetime

from paperharness.contracts import bundle, run


def test_run_state_serializes_replaying_for_clean_replay_events():
    event = run.RunEvent(
        run_id="run-1",
        sequence=8,
        timestamp=datetime(2026, 11, 18, tzinfo=UTC),
        state=run.RunState.REPLAYING,
        kind="replay_started",
        message="开始干净复跑",
    )

    payload = event.model_dump(mode="json")
    assert payload["state"] == "replaying"
    assert run.RunEvent.model_validate_json(event.model_dump_json()).state is run.RunState.REPLAYING


def test_replay_check_roundtrips_all_contract_fields():
    check = run.ReplayCheck(
        name="clean_checkout", status="blocked", detail="缺少基础修订"
    )

    payload = check.model_dump(mode="json")
    assert payload == {
        "name": "clean_checkout",
        "status": "blocked",
        "detail": "缺少基础修订",
    }
    assert run.ReplayCheck.model_validate_json(check.model_dump_json()) == check


def test_replay_result_roundtrips_frontend_summary_and_evidence():
    started_at = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
    finished_at = datetime(2026, 11, 18, 9, 2, tzinfo=UTC)
    result = run.ReplayResult(
        status=run.ReplayStatus.FAILED,
        reason="产物不一致",
        session_id="session-1",
        started_at=started_at,
        finished_at=finished_at,
        base_revision="a" * 40,
        repair_manifest_digest="b" * 64,
        image_digest="sha256:image",
        dataset_digest="c" * 64,
        dependency_digest="d" * 64,
        verification=run.VerificationResult(status="failed", level="execution"),
        checks=[run.ReplayCheck(name="artifact", status="failed", detail="指标不同")],
        differences=["coefficients.json 内容不同"],
        limitations=["未提供外部数据集"],
    )

    payload = result.model_dump(mode="json")
    assert payload["status"] == "failed"
    assert payload["started_at"] == "2026-11-18T09:00:00Z"
    assert payload["finished_at"] == "2026-11-18T09:02:00Z"
    assert payload["checks"] == [
        {"name": "artifact", "status": "failed", "detail": "指标不同"}
    ]
    assert payload["differences"] == ["coefficients.json 内容不同"]
    assert payload["limitations"] == ["未提供外部数据集"]
    restored = run.ReplayResult.model_validate_json(result.model_dump_json())
    assert restored == result
    assert restored.status is run.ReplayStatus.FAILED


def test_empty_replay_results_have_independent_default_lists():
    first = run.ReplayResult(status=run.ReplayStatus.NOT_REQUESTED)
    second = run.ReplayResult(status=run.ReplayStatus.PENDING)

    assert first.reason is None
    assert first.session_id is None
    assert first.started_at is None
    assert first.finished_at is None
    assert first.base_revision is None
    assert first.repair_manifest_digest is None
    assert first.image_digest is None
    assert first.dataset_digest is None
    assert first.dependency_digest is None
    assert first.verification is None
    assert first.checks == []
    assert first.differences == []
    assert first.limitations == []
    first.differences.append("only first")
    assert second.differences == []
    assert run.ReplayResult.model_validate_json(first.model_dump_json()) == first


def test_run_result_accepts_and_serializes_replay_without_dropping_bundle_fields():
    replay = run.ReplayResult(status=run.ReplayStatus.PASSED)
    result = run.RunResult(
        run_id="run-1",
        outcome="succeeded",
        verified=True,
        attempts=2,
        report_url="/api/runs/run-1/report",
        bundle=bundle.BundleResult(),
        bundle_url="/api/runs/run-1/bundle",
        replay=replay,
    )

    payload = result.model_dump(mode="json")
    assert payload["replay"]["status"] == "passed"
    assert payload["bundle"]["integrity"] == "blocked"
    assert payload["bundle"]["items"] == []
    assert payload["bundle"]["total_bytes"] == 0
    assert payload["bundle_url"] == "/api/runs/run-1/bundle"
    assert run.RunResult.model_validate_json(result.model_dump_json()).replay == replay
    without_replay = run.RunResult(
        run_id="run-2",
        outcome="failed",
        verified=False,
        attempts=1,
        report_url="/api/runs/run-2/report",
    )
    assert without_replay.replay is None


def test_repair_manifest_reads_legacy_json_without_repository_url():
    payload = {
        "schema_version": 1,
        "run_id": "run-1",
        "base_revision": "a" * 40,
        "command": ["python", "main.py"],
        "python_version": "3.10",
        "frozen_at": "2026-11-18T09:00:00Z",
    }

    manifest = bundle.RepairManifest.model_validate(payload)
    assert manifest.repository_url is None
    assert manifest.files == []
    assert "repository_url" not in payload
    with_url = bundle.RepairManifest.model_validate(
        {**payload, "repository_url": "https://github.com/example/repo.git"}
    )
    assert with_url.repository_url == "https://github.com/example/repo.git"
    assert bundle.RepairManifest.model_validate_json(with_url.model_dump_json()) == with_url


def test_bundle_result_roundtrips_frontend_summary_fields():
    result = bundle.BundleResult(
        integrity="incomplete",
        items=[
            bundle.BundleItem(path="files/main.py", kind="source", status="included")
        ],
        total_bytes=12,
        url="/api/runs/run-1/bundle",
        sha256="e" * 64,
        manifest_digest="f" * 64,
        entries=[bundle.BundleEntry(path="files/main.py", sha256="1" * 64, size=12)],
        missing=[bundle.BundleMissing(path="patch.diff", reason="修复补丁不可用")],
        external_inputs=["data/train.csv"],
        limitations=["数据集未包含在材料包中"],
    )

    payload = result.model_dump(mode="json")
    assert payload["url"] == "/api/runs/run-1/bundle"
    assert payload["sha256"] == "e" * 64
    assert payload["manifest_digest"] == "f" * 64
    assert payload["entries"] == [
        {"path": "files/main.py", "sha256": "1" * 64, "size": 12}
    ]
    assert payload["missing"] == [
        {"path": "patch.diff", "reason": "修复补丁不可用"}
    ]
    assert payload["external_inputs"] == ["data/train.csv"]
    assert payload["limitations"] == ["数据集未包含在材料包中"]
    assert "manifest_sha256" not in payload
    assert bundle.BundleResult.model_validate_json(result.model_dump_json()) == result


def test_empty_bundle_results_have_independent_frontend_lists():
    first = bundle.BundleResult()
    second = bundle.BundleResult()

    assert first.url is None
    assert first.sha256 is None
    assert first.manifest_digest is None
    assert first.entries == []
    assert first.missing == []
    assert first.external_inputs == []
    assert first.limitations == []
    first.entries.append(bundle.BundleEntry(path="main.py", sha256="0" * 64, size=1))
    assert second.entries == []
    assert bundle.BundleResult.model_validate_json(first.model_dump_json()) == first
