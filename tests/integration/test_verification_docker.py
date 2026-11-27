import hashlib
import json
import os

import pytest

from paperharness.harness.verification import verification_spec, verify_artifact

pytestmark = pytest.mark.skipif(
    os.environ.get("PAPERHARNESS_TEST_DOCKER") != "1",
    reason="set PAPERHARNESS_TEST_DOCKER=1 to exercise the local Docker verifier",
)


@pytest.fixture
def client():
    docker = pytest.importorskip("docker")
    client = docker.from_env(timeout=5)
    try:
        client.ping()
        client.images.get("paperharness/base-py3.10")
    except docker.errors.DockerException as exc:
        client.close()
        pytest.skip(f"Docker verifier image unavailable: {type(exc).__name__}")
    yield client
    client.close()


@pytest.mark.parametrize("coefficient,passed", [(-2.0, True), (42.0, False)])
def test_real_isolated_verifier_and_cleanup(client, coefficient, passed):
    before = {c.id for c in client.containers.list(all=True, filters={"label": "paperharness.role=verifier"})}
    raw = json.dumps({
        "schema_version": 1,
        "coefficients": [[0, coefficient, 0, 0, 0, 0], [0, 0, 1, 0, 0, 0]],
    }).encode()
    result = verify_artifact(
        raw, verification_spec(), timeout_seconds=20, cancelled=lambda: False, client=client,
    )
    assert result.status == ("passed" if passed else "failed"), result.model_dump()
    assert result.level == "target"
    assert result.verifier_image_id.startswith("sha256:")
    assert result.artifact_sha256 == hashlib.sha256(raw).hexdigest()
    assert len(result.metrics) == 2
    assert result.metrics[0].observed == abs(coefficient + 2)
    after = {c.id for c in client.containers.list(all=True, filters={"label": "paperharness.role=verifier"})}
    assert after == before


def test_real_verifier_rejects_fake_success_fields(client):
    raw = b'{"schema_version":1,"coefficients":[[0,42,0,0,0,0],[0,0,1,0,0,0]],"PASS":true,"threshold":100}'
    result = verify_artifact(
        raw, verification_spec(), timeout_seconds=20, cancelled=lambda: False, client=client,
    )
    assert result.status == "failed", result.model_dump()
    assert result.failure_category == "artifact_invalid"
    assert not result.metrics
