import base64
import hashlib
import json
import os
import stat
import struct
import subprocess
import sys
from types import SimpleNamespace

import pytest

from paperharness.harness import verification as v

GOOD = b'{"schema_version":1,"coefficients":[[0,-2,0,0,0,0],[0,0,1,0,0,0]]}'
IMAGE_ID = "sha256:" + "a" * 64
GOOD_RESPONSE = {
    "status": "passed",
    "failure_category": None,
    "schema_valid": True,
    "errors": [0, 0],
}
INVALID_RESPONSE = {
    "status": "failed",
    "failure_category": "artifact_invalid",
    "schema_valid": False,
    "errors": [],
}
BAD_METRICS = {
    "status": "failed",
    "failure_category": "metric_not_met",
    "schema_valid": True,
    "errors": [0.5, 0],
}


def encoded_rows(rows):
    return json.dumps({"schema_version": 1, "coefficients": rows}).encode()


def run_script(raw, *, cwd=None, encoded=None):
    # Host execution is ONLY a test of the trusted script, always fully isolated.
    encoded = encoded if encoded is not None else base64.b64encode(raw).decode("ascii")
    script = v._VALIDATOR.read_bytes().decode("utf-8")
    command = [sys.executable, "-I", "-S", "-B", "-c", script, encoded]
    stdin = None
    if len(encoded) > 20000:
        # Windows has a 32K command-line limit; test-only transport, unchanged validator.
        command = command[:5] + [
            "import sys; sys.argv = [sys.argv[0], sys.stdin.read()]\n" + script
        ]
        stdin = encoded.encode("ascii")
    process = subprocess.run(
        command, input=stdin, cwd=cwd, capture_output=True, timeout=10, check=True
    )
    assert process.stderr == b""
    assert len(process.stdout) < v._MAX_RESPONSE
    return json.loads(process.stdout)


def test_spec_is_static_actual_file_digest(monkeypatch):
    monkeypatch.setattr(v, "_new_client", lambda: pytest.fail("Docker in static preflight"))
    spec = v.verification_spec()
    assert spec.rule_id == "pysindy-linear-v1"
    assert spec.artifact == "coefficients.json"
    assert spec.threshold == 1e-4
    assert spec.validator_digest == hashlib.sha256(v._VALIDATOR.read_bytes()).hexdigest()


def test_script_valid_and_small_error():
    assert run_script(GOOD) == GOOD_RESPONSE
    data = run_script(encoded_rows([[0, -2, 0, 0, 0, 0.00009], [0, 0, 1, 0, 0, 0]]))
    assert data["status"] == "passed"
    assert data["errors"] == [0.00009, 0]


@pytest.mark.parametrize("row", [0, 1])
@pytest.mark.parametrize("column", range(6))
def test_script_checks_every_coefficient(row, column):
    rows = [[0, -2, 0, 0, 0, 0], [0, 0, 1, 0, 0, 0]]
    rows[row][column] += 0.5
    result = run_script(encoded_rows(rows))
    assert result["status"] == "failed"
    assert result["failure_category"] == "metric_not_met"
    assert result["errors"][row] == 0.5


def test_script_threshold_is_strict_and_magnitude_boundary_is_valid():
    result = run_script(encoded_rows([[1e-4, -2, 0, 0, 0, 0], [0, 0, 1, 0, 0, 0]]))
    assert result["failure_category"] == "metric_not_met"
    assert result["errors"] == [1e-4, 0]
    result = run_script(encoded_rows([[-1e6, -2, 0, 0, 0, 0], [0, 0, 1, 0, 0, 0]]))
    assert result["schema_valid"] is True
    assert result["errors"] == [1e6, 0]


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"\xff",
        b"not json",
        b"[]",
        b"null",
        b"{}",
        GOOD + b"{}",
        GOOD.replace(b'"schema_version":1', b'"schema_version":true'),
        GOOD.replace(b'"schema_version":1', b'"schema_version":1.0'),
        GOOD.replace(b'"schema_version":1', b'"schema_version":2'),
        GOOD.replace(b'"schema_version":1', b'"schema_version":1,"schema_version":1'),
        GOOD.replace(b'"schema_version":1', b'"schema_version":1,"\\u0073chema_version":1'),
        GOOD[:-1] + b',"PASS":true}',
        GOOD[:-1] + b',"error":0}',
        GOOD[:-1] + b',"threshold":999}',
        GOOD.replace(b"[0,-2,0,0,0,0]", b"[true,-2,0,0,0,0]"),
        GOOD.replace(b"[0,-2,0,0,0,0]", b"[NaN,-2,0,0,0,0]"),
        GOOD.replace(b"[0,-2,0,0,0,0]", b"[Infinity,-2,0,0,0,0]"),
        GOOD.replace(b"[0,-2,0,0,0,0]", b"[-Infinity,-2,0,0,0,0]"),
        GOOD.replace(b"[0,-2,0,0,0,0]", b"[1e999,-2,0,0,0,0]"),
        GOOD.replace(b"[0,-2,0,0,0,0]", b"[1000001,-2,0,0,0,0]"),
        GOOD.replace(b"[0,-2,0,0,0,0]", b"[-1000001,-2,0,0,0,0]"),
        GOOD.replace(b"[0,-2,0,0,0,0]", b'["0",-2,0,0,0,0]'),
        GOOD.replace(b"[0,-2,0,0,0,0]", b"[null,-2,0,0,0,0]"),
        GOOD.replace(b"[0,-2,0,0,0,0]", b"[0,-2,0,0,0]"),
        GOOD.replace(b"[0,-2,0,0,0,0]", b"[0,-2,0,0,0,0,0]"),
        b'{"schema_version":1,"coefficients":[[0,-2,0,0,0,0]]}',
        b'{"schema_version":1,"coefficients":[[[0]],[[1]]]}',
        b'{"schema_version":1,"coefficients":' + b"[" * 2000 + b"0" + b"]" * 2000 + b"}",
        b" " * (32 * 1024 + 1),
        b'{"schema_version":1,"coefficients":[[' + b"9" * 5000 + b"]]}",
    ],
    ids=lambda raw: hashlib.sha256(raw).hexdigest()[:12],
)
def test_script_rejects_invalid_inputs(raw):
    assert run_script(raw) == INVALID_RESPONSE


def test_script_rejects_bad_base64():
    assert run_script(b"", encoded="!") == INVALID_RESPONSE


def test_script_isolated_from_shadow_modules(tmp_path):
    for name in ("json", "math", "base64", "sitecustomize"):
        (tmp_path / f"{name}.py").write_text("raise RuntimeError('hostile import')")
    assert run_script(GOOD, cwd=tmp_path) == GOOD_RESPONSE
    assert not (tmp_path / "__pycache__").exists()


def test_prepare_removes_only_stale_output_before_each_execution(tmp_path):
    spec = v.verification_spec()
    unrelated = tmp_path / "keep.txt"
    unrelated.write_bytes(b"keep")
    (tmp_path / "keep-dir").mkdir()
    for _ in range(3):
        (tmp_path / spec.artifact).write_bytes(GOOD)
        v.prepare_artifact(tmp_path, spec)
        assert not (tmp_path / spec.artifact).exists()
    v.prepare_artifact(tmp_path, spec)  # No existing artifact, including first execution.
    assert unrelated.read_bytes() == b"keep"
    assert (tmp_path / "keep-dir").is_dir()


def test_snapshot_is_immutable_bounded_and_does_not_parse_json(tmp_path):
    spec = v.verification_spec()
    artifact = tmp_path / spec.artifact
    for raw in (b"", b"\xff", GOOD, b"x" * (32 * 1024)):
        artifact.write_bytes(raw)
        snapshot = v.snapshot_artifact(tmp_path, spec)
        artifact.write_bytes(b"changed later")
        assert type(snapshot) is bytes
        assert snapshot == raw
    artifact.write_bytes(b"x" * (32 * 1024 + 1))
    with pytest.raises(v.ArtifactRejected, match="32 KiB"):
        v.snapshot_artifact(tmp_path, spec)
    # Safe stale files can be removed even if they exceed the snapshot limit.
    v.prepare_artifact(tmp_path, spec)


def test_missing_snapshot_has_sanitized_message(tmp_path):
    with pytest.raises(v.ArtifactRejected) as error:
        v.snapshot_artifact(tmp_path, v.verification_spec())
    assert str(tmp_path) not in str(error.value)
    assert error.value.message == str(error.value)


@pytest.mark.parametrize("operation", [v.prepare_artifact, v.snapshot_artifact])
@pytest.mark.parametrize("artifact", ["../coefficients.json", "/coefficients.json", "a/b", "x:ads"])
def test_rejects_nonliteral_artifact(operation, artifact, tmp_path):
    spec = v.verification_spec().model_copy(update={"artifact": artifact})
    with pytest.raises(v.ArtifactRejected):
        operation(tmp_path, spec)


@pytest.mark.parametrize("operation", [v.prepare_artifact, v.snapshot_artifact])
def test_rejects_directory_and_hardlink(operation, tmp_path):
    spec = v.verification_spec()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    artifact = workspace / spec.artifact
    artifact.mkdir()
    with pytest.raises(v.ArtifactRejected):
        operation(workspace, spec)
    artifact.rmdir()
    original = tmp_path / "original"
    original.write_bytes(GOOD)
    os.link(original, artifact)
    with pytest.raises(v.ArtifactRejected):
        operation(workspace, spec)
    assert original.read_bytes() == GOOD
    assert artifact.exists()


@pytest.mark.parametrize("operation", [v.prepare_artifact, v.snapshot_artifact])
@pytest.mark.parametrize("ancestor", [False, True])
def test_rejects_symlink_artifact_and_workspace(operation, ancestor, tmp_path):
    spec = v.verification_spec()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / spec.artifact).write_bytes(GOOD)
    workspace = tmp_path / "workspace"
    try:
        if ancestor:
            workspace.symlink_to(outside, target_is_directory=True)
        else:
            workspace.mkdir()
            (workspace / spec.artifact).symlink_to(outside / spec.artifact)
    except OSError:
        pytest.skip("symlink privilege unavailable")
    with pytest.raises(v.ArtifactRejected):
        operation(workspace, spec)
    assert (outside / spec.artifact).read_bytes() == GOOD


@pytest.mark.skipif(os.name != "nt", reason="Windows junction semantics")
@pytest.mark.parametrize("operation", [v.prepare_artifact, v.snapshot_artifact])
def test_windows_workspace_junction_rejected(operation, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    artifact = outside / "coefficients.json"
    artifact.write_bytes(GOOD)
    workspace = tmp_path / "workspace"
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(workspace), str(outside)], check=True, capture_output=True
    )
    with pytest.raises(v.ArtifactRejected):
        operation(workspace, v.verification_spec())
    assert artifact.read_bytes() == GOOD


@pytest.mark.parametrize("mode", [stat.S_IFREG, stat.S_IFDIR])
def test_rejects_reparse_metadata_even_when_not_a_symlink(mode):
    info = SimpleNamespace(st_mode=mode, st_nlink=1, st_file_attributes=0x400)
    with pytest.raises(v.ArtifactRejected):
        v._safe_metadata(info, directory=mode == stat.S_IFDIR)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFO")
@pytest.mark.parametrize("operation", [v.prepare_artifact, v.snapshot_artifact])
def test_rejects_fifo_without_opening(operation, tmp_path):
    os.mkfifo(tmp_path / "coefficients.json")
    with pytest.raises(v.ArtifactRejected):
        operation(tmp_path, v.verification_spec())


@pytest.mark.parametrize("operation", [v.prepare_artifact, v.snapshot_artifact])
def test_detects_replacement_between_lstat_and_open(operation, tmp_path, monkeypatch):
    artifact = tmp_path / "coefficients.json"
    artifact.write_bytes(GOOD)
    other = tmp_path / "other"
    other.write_bytes(b"replacement")
    original_open = v._Directory.open

    def replace_then_open(directory, name, *, deleting):
        os.replace(other, artifact)
        return original_open(directory, name, deleting=deleting)

    monkeypatch.setattr(v._Directory, "open", replace_then_open)
    with pytest.raises(v.ArtifactRejected):
        operation(tmp_path, v.verification_spec())
    assert artifact.read_bytes() == b"replacement"


def test_detects_metadata_changes_after_read(tmp_path, monkeypatch):
    (tmp_path / "coefficients.json").write_bytes(GOOD)
    original_fstat = os.fstat
    calls = 0

    def changing_fstat(fd):
        nonlocal calls
        info = original_fstat(fd)
        if stat.S_ISREG(info.st_mode):
            calls += 1
            if calls >= 2:
                values = {
                    key: getattr(info, key)
                    for key in (
                        "st_dev",
                        "st_ino",
                        "st_mode",
                        "st_nlink",
                        "st_size",
                        "st_mtime_ns",
                        "st_ctime_ns",
                    )
                }
                values["st_nlink"] = 2
                return SimpleNamespace(**values)
        return info

    monkeypatch.setattr(v.os, "fstat", changing_fstat)
    with pytest.raises(v.ArtifactRejected):
        v.snapshot_artifact(tmp_path, v.verification_spec())


class NotFound(Exception):
    status_code = 404


class FakeResponse:
    def __init__(self, client, payload):
        self.client = client
        self.payload = payload
        self.closed = False

    def raise_for_status(self):
        self.client.step("response")

    def iter_content(self, chunk_size):
        assert chunk_size <= 512
        for offset in range(0, len(self.payload), chunk_size):
            self.client.step("chunk")
            yield self.payload[offset : offset + chunk_size]

    def close(self):
        self.closed = True


class FakeAPI:
    def __init__(self, client):
        self.client = client
        self.timeout = 60
        self.removals = []
        self.response = None

    def _url(self, path, container_id):
        assert container_id == self.client.container.id
        return path.format(container_id)

    def _get(self, url, **kwargs):
        self.client.step("logs")
        assert kwargs == {
            "stream": True,
            "params": {"stdout": 1, "stderr": 1, "follow": 0, "tail": 2},
        }
        assert url == "/containers/exact-verifier/logs"
        payload = self.client.payload
        wire = self.client.wire
        if wire is None:
            wire = struct.pack(">BxxxI", 1, len(payload)) + payload
        self.response = FakeResponse(self.client, wire)
        return self.response

    def remove_container(self, identifier, *, force):
        assert force is True
        assert self.timeout == 2
        self.removals.append(identifier)
        if "cleanup" in self.client.hooks:
            self.client.hooks["cleanup"]()
        if self.client.cleanup_error:
            raise RuntimeError("SECRET host cleanup path")
        if self.client.absent:
            raise NotFound()


class FakeContainer:
    id = "exact-verifier"

    def __init__(self, client):
        self.client = client
        self.attrs = {"State": {"Status": "created"}}

    def start(self):
        self.client.step("start")

    def reload(self):
        self.client.step("reload")
        self.attrs = {"State": self.client.state}


class FakeClient:
    def __init__(self, result=None):
        self.api = FakeAPI(self)
        self.container = FakeContainer(self)
        self.images = SimpleNamespace(get=self.get_image)
        self.containers = SimpleNamespace(create=self.create)
        self.image = SimpleNamespace(id=IMAGE_ID, attrs={"Config": {}})
        self.payload = json.dumps(result or GOOD_RESPONSE).encode()
        self.wire = None
        self.state = {"Status": "exited", "ExitCode": 0, "OOMKilled": False, "Error": ""}
        self.calls = []
        self.hooks = {}
        self.fail = None
        self.cleanup_error = False
        self.absent = False
        self.kwargs = None
        self.closed = False

    def step(self, stage):
        assert 0 < self.api.timeout <= 2
        self.calls.append(stage)
        if stage in self.hooks:
            self.hooks[stage]()
        if stage == self.fail:
            raise RuntimeError("SECRET host path and raw exception")

    def get_image(self, tag):
        self.step("image")
        assert tag == "paperharness/base-py3.10"
        return self.image

    def create(self, **kwargs):
        self.kwargs = kwargs
        self.step("create")
        return self.container

    def close(self):
        self.closed = True


def verify(client, raw=GOOD, **kwargs):
    return v.verify_artifact(
        raw,
        v.verification_spec(),
        timeout_seconds=kwargs.pop("timeout", 30),
        cancelled=kwargs.pop("cancelled", lambda: False),
        client=client,
        **kwargs,
    )


def test_docker_isolation_immutable_image_exact_script_and_metadata():
    client = FakeClient()
    result = verify(client)
    assert result.status == "passed"
    assert result.level == "target"
    assert result.checked_at.utcoffset().total_seconds() == 0
    assert result.verifier_image_id == IMAGE_ID
    assert result.artifact_sha256 == hashlib.sha256(GOOD).hexdigest()
    spec = v.verification_spec()
    for field in ("rule_id", "validator_digest", "code_under_test", "dataset", "source"):
        assert getattr(result, field) == getattr(spec, field)
    assert len(result.limitations) == 3
    assert all(check.passed for check in result.checks)
    assert [metric.name for metric in result.metrics] == ["x方程最大系数误差", "y方程最大系数误差"]
    assert all(metric.threshold == 1e-4 and metric.comparison == "lt" for metric in result.metrics)
    args = client.kwargs
    assert args["image"] == IMAGE_ID
    assert args["command"][:5] == ["/usr/local/bin/python", "-I", "-S", "-B", "-c"]
    assert args["command"][5].encode() == v._VALIDATOR.read_bytes()
    assert base64.b64decode(args["command"][6], validate=True) == GOOD
    assert args["entrypoint"] == []
    assert args["working_dir"] == "/"
    assert args["user"] == "1000:1000"
    assert args["network_mode"] == "none"
    assert args["read_only"] is True
    assert args["cap_drop"] == ["ALL"]
    assert args["security_opt"] == ["no-new-privileges:true"]
    assert args["volumes"] == {} and args["mounts"] == []
    assert args["mem_limit"] == args["memswap_limit"] == "64m"
    assert args["nano_cpus"] == 500_000_000 and args["pids_limit"] == 32
    assert not args["stdin_open"] and not args["tty"]
    assert args["environment"] == {}
    assert args["log_config"]["config"] == {"max-size": "8k", "max-file": "1"}
    assert args["name"].startswith("paperharness-verifier-")
    assert args["labels"]["paperharness.role"] == "verifier"
    assert args["labels"]["paperharness.parent_artifact_sha256"] == result.artifact_sha256
    assert "paperharness.run_id" not in args["labels"]
    assert client.api.removals == [client.container.id]
    assert client.api.response.closed
    assert client.api.timeout == 60
    assert not client.closed
    second = FakeClient()
    verify(second)
    assert client.kwargs["name"] != second.kwargs["name"]


@pytest.mark.parametrize("data", [GOOD_RESPONSE, BAD_METRICS, INVALID_RESPONSE])
def test_verifier_outcomes_always_remove_container(data):
    client = FakeClient(data)
    result = verify(client)
    assert result.status == data["status"]
    assert result.failure_category == data["failure_category"]
    assert client.api.removals == [client.container.id]
    if data["schema_valid"]:
        assert [m.observed for m in result.metrics] == data["errors"]
    else:
        assert result.metrics == []
        assert not result.checks[0].passed


@pytest.mark.parametrize(
    "stage", ["image", "create", "start", "reload", "logs", "response", "chunk"]
)
def test_infrastructure_failures_are_sanitized_and_cleaned(stage):
    client = FakeClient()
    client.fail = stage
    result = verify(client)
    assert result.status == "blocked"
    assert result.failure_category == "verification_error"
    assert "SECRET" not in result.model_dump_json()
    if stage == "image":
        assert client.api.removals == []
    elif stage == "create":
        assert client.api.removals == [client.kwargs["name"]]
    else:
        assert client.api.removals == [client.container.id]
    if client.api.response is not None:
        assert client.api.response.closed
    assert client.api.timeout == 60


def test_creation_failure_with_no_container_is_not_cleanup_failure():
    client = FakeClient()
    client.fail = "create"
    client.absent = True
    assert verify(client).failure_category == "verification_error"
    assert client.api.removals == [client.kwargs["name"]]


@pytest.mark.parametrize("data", [GOOD_RESPONSE, BAD_METRICS, INVALID_RESPONSE])
def test_cleanup_failure_never_reports_success_or_ordinary_failure(data):
    client = FakeClient(data)
    client.cleanup_error = True
    result = verify(client)
    assert result.status == "blocked"
    assert result.failure_category == "verification_error"
    assert not result.metrics and not result.checks
    assert "SECRET" not in result.model_dump_json()


def test_digest_change_is_blocked_before_any_docker_access(monkeypatch, tmp_path):
    spec = v.verification_spec()
    changed = tmp_path / "validator.py"
    changed.write_bytes(v._VALIDATOR.read_bytes() + b"\n# changed\n")
    monkeypatch.setattr(v, "_VALIDATOR", changed)
    monkeypatch.setattr(v, "_new_client", lambda: pytest.fail("must not initialize Docker"))
    # An opaque client additionally proves no SDK attribute access occurred.
    for client in (None, object()):
        result = v.verify_artifact(
            GOOD, spec, timeout_seconds=30, cancelled=lambda: False, client=client
        )
        assert result.failure_category == "verification_rule_changed"
        assert result.status == "blocked"
        assert result.verifier_image_id is None


def test_script_bytes_are_not_reread_after_digest_check(monkeypatch, tmp_path):
    path = tmp_path / "validator.py"
    original = v._VALIDATOR.read_bytes()
    path.write_bytes(original)
    monkeypatch.setattr(v, "_VALIDATOR", path)
    client = FakeClient()
    client.hooks["image"] = lambda: path.write_bytes(b"raise RuntimeError('changed')")
    assert verify(client).status == "passed"
    assert client.kwargs["command"][5].encode() == original


@pytest.mark.parametrize("stage", ["image", "create", "start", "reload", "logs", "chunk"])
@pytest.mark.parametrize("mode", ["cancel", "timeout"])
def test_cancellation_and_deadline_cover_setup_and_response(stage, mode, monkeypatch):
    now = [100.0]
    cancelled = [False]
    monkeypatch.setattr(v.time, "monotonic", lambda: now[0])
    client = FakeClient()

    def interrupt():
        if mode == "cancel":
            cancelled[0] = True
        else:
            now[0] += 31

    client.hooks[stage] = interrupt
    result = verify(client, cancelled=lambda: cancelled[0])
    assert result.status == "blocked"
    assert result.failure_category == (
        "verification_cancelled" if mode == "cancel" else "verification_timeout"
    )
    assert client.api.removals == ([] if stage == "image" else [client.container.id])
    if client.api.response:
        assert client.api.response.closed


@pytest.mark.parametrize("mode", ["cancel", "timeout"])
def test_preexisting_cancellation_or_timeout_never_initializes_docker(mode, monkeypatch):
    monkeypatch.setattr(v, "_new_client", lambda: pytest.fail("must not initialize Docker"))
    result = v.verify_artifact(
        GOOD,
        v.verification_spec(),
        timeout_seconds=0 if mode == "timeout" else 2,
        cancelled=lambda: mode == "cancel",
    )
    assert result.failure_category == (
        "verification_cancelled" if mode == "cancel" else "verification_timeout"
    )


def test_running_container_observes_deadline(monkeypatch):
    now = [1.0]
    monkeypatch.setattr(v.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(v.time, "sleep", lambda _: now.__setitem__(0, now[0] + 1))
    client = FakeClient()
    client.state = {"Status": "running"}
    assert verify(client, timeout=2).failure_category == "verification_timeout"
    assert client.api.removals == [client.container.id]


def test_client_initialization_time_counts_and_owned_client_closes(monkeypatch):
    now = [1.0]
    client = FakeClient()
    monkeypatch.setattr(v.time, "monotonic", lambda: now[0])

    def slow_client():
        now[0] += 10
        return client

    monkeypatch.setattr(v, "_new_client", slow_client)
    result = v.verify_artifact(
        GOOD, v.verification_spec(), timeout_seconds=2, cancelled=lambda: False
    )
    assert result.failure_category == "verification_timeout"
    assert client.closed
    assert client.calls == []


def test_client_factory_uses_finite_api_timeout(monkeypatch):
    fake = SimpleNamespace(from_env=lambda **kwargs: kwargs)
    monkeypatch.setitem(sys.modules, "docker", fake)
    assert v._new_client() == {"timeout": 2}


@pytest.mark.parametrize(
    "mutation",
    [
        {"id": "paperharness/base-py3.10"},
        {"attrs": {"Config": {"Volumes": {"/unsafe": {}}}}},
    ],
)
def test_rejects_mutable_image_or_implicit_mounts(mutation):
    client = FakeClient()
    for key, value in mutation.items():
        setattr(client.image, key, value)
    assert verify(client).failure_category == "verification_error"
    assert client.kwargs is None


@pytest.mark.parametrize(
    "state",
    [
        {"Status": "exited", "ExitCode": 1},
        {"Status": "exited", "ExitCode": 0, "OOMKilled": True},
        {"Status": "exited", "ExitCode": 0, "Error": "private details"},
        {"Status": "dead"},
    ],
)
def test_failed_verifier_process_is_infrastructure_not_bad_artifact(state):
    client = FakeClient()
    client.state = state
    assert verify(client).failure_category == "verification_error"
    assert client.api.removals == [client.container.id]


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"{}",
        b"not json",
        b"\xff",
        b"[" * 2000 + b"]" * 2000,
        json.dumps({**GOOD_RESPONSE, "errors": [True, 0]}).encode(),
        json.dumps({**GOOD_RESPONSE, "errors": [float("nan"), 0]}).encode(),
        json.dumps({**GOOD_RESPONSE, "errors": [float("inf"), 0]}).encode(),
        json.dumps({**GOOD_RESPONSE, "errors": [-1, 0]}).encode(),
        json.dumps({**GOOD_RESPONSE, "errors": [0]}).encode(),
        json.dumps({**GOOD_RESPONSE, "errors": [1, 0]}).encode(),
        json.dumps({**GOOD_RESPONSE, "extra": "PASS"}).encode(),
        json.dumps({**GOOD_RESPONSE, "failure_category": "metric_not_met"}).encode(),
        b'{"status":"failed",' + json.dumps(GOOD_RESPONSE).encode()[1:],
        b"x" * 9000,
    ],
)
def test_bounded_response_rejects_malformed_or_inconsistent_data(payload):
    client = FakeClient()
    client.payload = payload
    result = verify(client)
    assert result.status == "blocked" and result.failure_category == "verification_error"
    assert client.api.removals == [client.container.id]
    assert client.api.response.closed


@pytest.mark.parametrize(
    "wire",
    [
        b"123",
        struct.pack(">BxxxI", 1, 2**32 - 1),
        struct.pack(">BxxxI", 2, 1) + b"x",
        b"\1\1\0\0\0\0\0\1x",
    ],
)
def test_docker_frame_validation_is_bounded(wire):
    client = FakeClient()
    client.wire = wire
    assert verify(client).failure_category == "verification_error"
    assert client.api.response.closed
    assert client.api.removals == [client.container.id]


@pytest.mark.parametrize("mode", ["cancel", "timeout"])
def test_cleanup_time_and_cancellation_count_toward_result(mode, monkeypatch):
    now = [1.0]
    cancel = [False]
    monkeypatch.setattr(v.time, "monotonic", lambda: now[0])
    client = FakeClient()

    def cleanup_interrupt():
        if mode == "cancel":
            cancel[0] = True
        else:
            now[0] += 31

    client.hooks["cleanup"] = cleanup_interrupt
    result = verify(client, cancelled=lambda: cancel[0])
    assert result.failure_category == (
        "verification_cancelled" if mode == "cancel" else "verification_timeout"
    )
    assert result.status == "blocked"
    assert client.api.removals == [client.container.id]


@pytest.mark.parametrize("mode", ["cancel", "timeout", "create", "start"])
def test_cleanup_failure_overrides_every_other_outcome(mode, monkeypatch):
    now = [1.0]
    cancel = [False]
    monkeypatch.setattr(v.time, "monotonic", lambda: now[0])
    client = FakeClient()
    client.cleanup_error = True
    if mode == "cancel":
        client.hooks["start"] = lambda: cancel.__setitem__(0, True)
    elif mode == "timeout":
        client.hooks["start"] = lambda: now.__setitem__(0, 32.0)
    else:
        client.fail = mode
    result = verify(client, cancelled=lambda: cancel[0])
    assert result.status == "blocked"
    assert result.failure_category == "verification_error"
    assert client.api.removals


def test_client_close_failure_blocks_otherwise_successful_verification(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(v, "_new_client", lambda: client)

    def close_error():
        raise RuntimeError("SECRET")

    monkeypatch.setattr(client, "close", close_error)
    result = v.verify_artifact(
        GOOD, v.verification_spec(), timeout_seconds=30, cancelled=lambda: False
    )
    assert result.status == "blocked"
    assert result.failure_category == "verification_error"
    assert client.api.removals == [client.container.id]
    assert "SECRET" not in result.model_dump_json()


def test_oversized_raw_fails_without_docker(monkeypatch):
    monkeypatch.setattr(
        v, "_new_client", lambda: pytest.fail("oversized input must not reach Docker")
    )
    result = v.verify_artifact(
        b"x" * (32 * 1024 + 1), v.verification_spec(), timeout_seconds=2, cancelled=lambda: False
    )
    assert result.status == "failed"
    assert result.failure_category == "artifact_invalid"
