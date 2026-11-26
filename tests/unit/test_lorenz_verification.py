import base64
import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from paperharness.contracts.preflight import PreflightRequest
from paperharness.contracts.verification import VerificationSpec
from paperharness.harness import lorenz_validator
from paperharness.harness import verification as v
from paperharness.harness.preflight import PreflightService
from paperharness.harness.store import RunStore
from paperharness.targets import load_targets

ROOT = Path(__file__).resolve().parents[2]


def validate(payload):
    raw = json.dumps(payload).encode()
    process = subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(v._LORENZ_VALIDATOR),
         base64.b64encode(raw).decode()],
        capture_output=True, check=True, timeout=10,
    )
    assert not process.stderr
    return json.loads(process.stdout)


def coefficients():
    return {"schema_version": 1, "coefficients": copy.deepcopy(lorenz_validator.EXPECTED)}


def test_lorenz_positive_and_wrong_scientific_result():
    data = coefficients()
    assert validate(data)["status"] == "passed"
    data["coefficients"][1][1] = 27  # process can exit zero while rho is wrong
    response = validate(data)
    assert response["status"] == "failed"
    assert response["failure_category"] == "metric_not_met"
    assert response["errors"] == [0, 1, 0]


def test_lorenz_zero_terms_and_strict_boundary_are_checked():
    data = coefficients()
    data["coefficients"][2][-1] = 0.001
    assert validate(data)["status"] == "failed"
    data["coefficients"][2][-1] = 0.000999
    assert validate(data)["status"] == "passed"


@pytest.mark.parametrize("mutation", ["shape", "boolean", "nan", "extra", "version"])
def test_lorenz_rejects_bad_schema_and_success_claims(mutation):
    data = coefficients()
    if mutation == "shape":
        data["coefficients"][0].pop()
    elif mutation == "boolean":
        data["coefficients"][0][0] = True
    elif mutation == "nan":
        data["coefficients"][0][0] = float("nan")
    elif mutation == "extra":
        data["PASS"] = True
    else:
        data["schema_version"] = True
    assert validate(data)["failure_category"] == "artifact_invalid"


def test_lorenz_rule_is_frozen_and_survives_preflight_roundtrip(tmp_path):
    targets = load_targets(ROOT / "targets")
    record = PreflightService(RunStore(tmp_path), targets).create(
        PreflightRequest(target_id="paper-lorenz", max_repairs=0)
    )
    spec = record.view.verification
    assert spec == v.verification_spec("sindy-lorenz-v1")
    assert spec.threshold == 0.001
    assert spec.validator_digest == hashlib.sha256(v._LORENZ_VALIDATOR.read_bytes()).hexdigest()
    assert record.target.manifest.revision == "4c32d2603cbf1aa476efae72bc78436cb1e6fc75"
    assert "pysindy==" not in base64.b64decode(record.target.files["requirements.txt"]).decode()
    assert "不是论文实验指标" not in spec.source
    assert "子实验" in spec.source
    restored = type(record).model_validate_json(record.model_dump_json())
    assert restored.check_digest(record.view.confirmation_digest)
    with pytest.raises(ValidationError):
        VerificationSpec(**{**spec.model_dump(), "threshold": 0.0001})


def test_response_metrics_cannot_cross_rules_or_raise_threshold():
    spec = v.verification_spec("sindy-lorenz-v1")
    data = {"status": "passed", "failure_category": None,
            "schema_valid": True, "errors": [0, 0, 0]}
    assert len(v._response_result(json.dumps(data).encode(), spec)["errors"]) == 3
    with pytest.raises(ValueError):
        v._response_result(json.dumps(data).encode())
    data["errors"][2] = 0.001
    with pytest.raises(ValueError, match="inconsistent"):
        v._response_result(json.dumps(data).encode(), spec)


def test_rule_metadata_and_threshold_tampering_block_before_docker():
    spec = v.verification_spec("sindy-lorenz-v1")
    for updates in ({"threshold": 1}, {"code_under_test": "arbitrary package"}):
        result = v.verify_artifact(
            b"{}", spec.model_copy(update=updates), timeout_seconds=5, cancelled=lambda: False,
        )
        assert result.status == "blocked"
        assert result.failure_category == "verification_rule_changed"
