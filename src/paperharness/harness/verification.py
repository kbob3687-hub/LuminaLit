"""Independent, bounded artifact verification. Never execute producer code on the host."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import stat
import struct
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from paperharness.contracts.verification import (
    MetricResult,
    VerificationCheck,
    VerificationDiagnostic,
    VerificationResult,
    VerificationSpec,
)

_VALIDATOR = Path(__file__).with_name("pysindy_validator.py")
_LORENZ_VALIDATOR = Path(__file__).with_name("lorenz_validator.py")
_MAX_ARTIFACT = 32 * 1024
_MAX_RESPONSE = 4096
# Docker Desktop API latency can exceed 2s under host pressure. This is a single
# RPC ceiling, always clipped to the remaining verification budget, never a retry.
_API_TIMEOUT = 5
_CLEANUP_TIMEOUT = 2  # bounded grace for removal even after cancellation/deadline
_IMAGE = "paperharness/base-py3.10"
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_LIMITATIONS = [
    "仅验证输出系数的数值一致性，不证明产物来源或训练过程真实性。",
    "任务面向已安装的 PySINDy 包，不是检出的仓库源码；验证器本身不导入该包。",
    "未执行干净环境复跑，不代表完整论文复现。",
]
_METRIC_NAMES = ("x方程最大系数误差", "y方程最大系数误差")


class ArtifactRejected(ValueError):
    """An artifact cannot safely be removed or snapshotted (message is path-free)."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def verification_spec(rule_id: str = "pysindy-linear-v1") -> VerificationSpec:
    """Static preflight only: fingerprint the actual deployed, trusted script bytes."""
    if rule_id == "sindy-lorenz-v1":
        return VerificationSpec(
            rule_id="sindy-lorenz-v1", threshold=1e-3,
            validator_digest=hashlib.sha256(_LORENZ_VALIDATOR.read_bytes()).hexdigest(),
            code_under_test=(
                "PySINDy v1.7.5 检出源码（4c32d2603cbf1aa476efae72bc78436cb1e6fc75）；"
                "维护者适配脚本导入仓库内 SINDy / STLSQ / PolynomialLibrary / lorenz，"
                "拒绝已安装包回退。"
            ),
            dataset=(
                "Lorenz 解析方程生成 100000 点；dt=0.001，初值 (-8,8,27)，"
                "LSODA rtol/atol=1e-12；导数噪声标准差 1e-4，随机种子 1000。"
            ),
            source=(
                "Brunton 等 2016 SINDy 论文 Fig.1 的 Lorenz 方程系数；"
                "执行设置取 PySINDy examples/3_original_paper.ipynb 的低噪声子实验。"
                "初值依上游示例，区别于原图的 (-8,7,27)；误差阈值由维护者预先设定。"
            ),
        )
    if rule_id != "pysindy-linear-v1":
        raise ValueError("unknown maintained verification rule")
    return VerificationSpec(validator_digest=hashlib.sha256(_VALIDATOR.read_bytes()).hexdigest())


def _metric_names(rule_id: str) -> tuple[str, ...]:
    return (*_METRIC_NAMES, "z方程最大系数误差") if rule_id == "sindy-lorenz-v1" else _METRIC_NAMES


def _limitations(rule_id: str) -> list[str]:
    if rule_id == "sindy-lorenz-v1":
        return [
            "验收只复算输出系数，不单独认证产物来源；源码导入与数据摘要由生产脚本记录。",
            "仅复现上游 Lorenz 低噪声系数识别子任务，未执行整本 notebook、前向轨迹或全部论文实验。",
            "采用上游示例初值 (-8,8,27)，与原论文 Fig.1 初值不同；随机种子在本子任务开始时重置。",
            "本指标验收不包含干净复跑；复跑结论须另查 replay，不代表跨机器验证。",
        ]
    return list(_LIMITATIONS)


def _artifact_name(spec: VerificationSpec) -> str:
    # Recheck even model_construct()/model_copy() values; never accept paths from a caller.
    if spec.artifact != "coefficients.json":
        raise ArtifactRejected("验证产物必须是工作区根目录的 coefficients.json。")
    return "coefficients.json"


def _safe_metadata(info: os.stat_result, *, directory: bool = False) -> None:
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & _REPARSE:
        raise ArtifactRejected("拒绝链接、联接点或重解析点。")
    if directory:
        if not stat.S_ISDIR(info.st_mode):
            raise ArtifactRejected("工作区路径不是安全目录。")
    elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ArtifactRejected("产物必须是单链接普通文件。")


def _identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _fingerprint(info: os.stat_result) -> tuple[int, ...]:
    return (
        *_identity(info),
        info.st_mode,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _windows_open(path: Path, *, directory: bool, deleting: bool = False) -> int:
    """Pin NTFS objects without following reparse points or allowing rename/delete."""
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create.restype = wintypes.HANDLE
    # Directory locks allow content updates, not replacing any path component.
    access = 0x80 if directory else 0x80000000  # FILE_READ_ATTRIBUTES / GENERIC_READ
    if deleting:
        access |= 0x10000  # DELETE, required for handle-relative disposition
    share = 3 if directory else 1  # READ|WRITE for dirs; READ only for files
    handle = create(str(path), access, share, None, 3, 0x02200000, None)
    if handle == wintypes.HANDLE(-1).value:
        raise OSError("cannot pin artifact path")
    return handle


def _windows_close(handle: int) -> None:
    import ctypes
    from ctypes import wintypes

    close = ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    if not close(handle):
        raise OSError("cannot close artifact path")


def _windows_delete(fd: int) -> None:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    dispose = ctypes.WinDLL("kernel32", use_last_error=True).SetFileInformationByHandle
    dispose.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    dispose.restype = wintypes.BOOL
    delete = ctypes.c_ubyte(1)  # FILE_DISPOSITION_INFO.DeleteFile
    if not dispose(msvcrt.get_osfhandle(fd), 4, ctypes.byref(delete), ctypes.sizeof(delete)):
        raise OSError("cannot remove stale artifact")


class _Directory:
    def __init__(self, path: Path, fd: int | None, chain: list[tuple[Path, os.stat_result]]):
        self.path = path
        self.fd = fd
        self.chain = chain

    def check(self) -> None:
        for path, original in self.chain:
            current = path.lstat()
            _safe_metadata(current, directory=True)
            if _identity(current) != _identity(original):
                raise ArtifactRejected("工作区目录在访问期间发生变化。")

    def lstat(self, name: str) -> os.stat_result:
        if self.fd is None:
            return (self.path / name).lstat()
        return os.stat(name, dir_fd=self.fd, follow_symlinks=False)

    def open(self, name: str, *, deleting: bool) -> int:
        if os.name == "nt":
            import msvcrt

            handle = _windows_open(self.path / name, directory=False, deleting=deleting)
            try:
                return msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
            except BaseException:
                _windows_close(handle)
                raise
        flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
        return os.open(name, flags, dir_fd=self.fd)

    def remove(self, name: str, fd: int) -> None:
        if os.name == "nt":
            _windows_delete(fd)
        else:
            # unlinkat does not follow a final symlink; parent traversal is descriptor-relative.
            os.unlink(name, dir_fd=self.fd)


@contextmanager
def _workspace(workspace: Path) -> Iterator[_Directory]:
    path = Path(os.path.abspath(workspace))
    chain: list[tuple[Path, os.stat_result]] = []
    handles: list[int] = []
    try:
        parent_fd = None
        for component in (*reversed(path.parents), path):
            before = component.lstat()
            _safe_metadata(before, directory=True)
            if os.name == "nt":
                handle = _windows_open(component, directory=True)
                handles.append(handle)
                after = component.lstat()
            else:
                flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
                handle = os.open(
                    component.name if parent_fd is not None else str(component),
                    flags,
                    dir_fd=parent_fd,
                )
                handles.append(handle)
                parent_fd = handle
                after = os.fstat(handle)
            _safe_metadata(after, directory=True)
            if _identity(before) != _identity(after):
                raise ArtifactRejected("工作区目录在访问期间发生变化。")
            chain.append((component, after))
        directory = _Directory(path, parent_fd, chain)
        directory.check()
        yield directory
        directory.check()
    except OSError:
        raise ArtifactRejected("无法安全访问工作区或产物。") from None
    finally:
        for handle in reversed(handles):
            if os.name == "nt":
                _windows_close(handle)
            else:
                os.close(handle)


@contextmanager
def _opened_artifact(
    directory: _Directory, name: str, before: os.stat_result, *, deleting: bool
) -> Iterator[tuple[int, os.stat_result]]:
    _safe_metadata(before)
    fd = directory.open(name, deleting=deleting)
    try:
        current = os.fstat(fd)
        _safe_metadata(current)
        # On Windows fstat does not expose reparse attributes: retain both lstat checks.
        named = directory.lstat(name)
        _safe_metadata(named)
        # Windows Python versions disagree about ctime (birth vs change time) between
        # lstat and fstat. Compare shared fields here; track each API's ctime separately.
        if _fingerprint(current)[:-1] != _fingerprint(before)[:-1] or _fingerprint(
            named
        ) != _fingerprint(before):
            raise ArtifactRejected("产物在打开期间发生变化。")
        directory.check()
        yield fd, current
    finally:
        os.close(fd)


def _unchanged(
    directory: _Directory, name: str, fd: int, before: os.stat_result, opened: os.stat_result
) -> None:
    for current, baseline in ((os.fstat(fd), opened), (directory.lstat(name), before)):
        _safe_metadata(current)
        if _fingerprint(current) != _fingerprint(baseline):
            raise ArtifactRejected("产物在访问期间发生变化。")
    directory.check()


def prepare_artifact(workspace: Path, spec: VerificationSpec) -> None:
    """Remove safe stale output only while the producer is not executing."""
    name = _artifact_name(spec)
    with _workspace(workspace) as directory:
        try:
            before = directory.lstat(name)
        except FileNotFoundError:
            return
        with _opened_artifact(directory, name, before, deleting=True) as (fd, opened):
            _unchanged(directory, name, fd, before, opened)
            directory.remove(name, fd)


def snapshot_artifact(workspace: Path, spec: VerificationSpec) -> bytes:
    """After closing the producer, obtain bounded immutable bytes without parsing JSON."""
    name = _artifact_name(spec)
    with _workspace(workspace) as directory:
        try:
            before = directory.lstat(name)
        except FileNotFoundError:
            raise ArtifactRejected("缺少 coefficients.json 验证产物。") from None
        _safe_metadata(before)
        if before.st_size > _MAX_ARTIFACT:
            raise ArtifactRejected("验证产物超过 32 KiB 上限。")
        with _opened_artifact(directory, name, before, deleting=False) as (fd, opened):
            chunks = []
            total = 0
            while total <= _MAX_ARTIFACT:
                chunk = os.read(fd, min(4096, _MAX_ARTIFACT + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
            if total > _MAX_ARTIFACT:
                raise ArtifactRejected("验证产物超过 32 KiB 上限。")
            _unchanged(directory, name, fd, before, opened)
            if total != before.st_size:
                raise ArtifactRejected("产物在读取期间发生变化。")
            return b"".join(chunks)


class _Blocked(Exception):
    def __init__(self, reason: str):
        self.reason = reason


def _checkpoint(deadline: float, cancelled: Callable[[], bool]) -> None:
    if cancelled():
        raise _Blocked("verification_cancelled")
    if time.monotonic() >= deadline:
        raise _Blocked("verification_timeout")


def _new_client(timeout_seconds: float = _API_TIMEOUT) -> Any:
    import docker

    return docker.from_env(timeout=timeout_seconds)


def _fault_code(exc: Exception) -> str:
    try:
        from requests.exceptions import Timeout
    except ImportError:  # Docker and requests are optional dependencies.
        timeout_types = (TimeoutError,)
    else:
        timeout_types = (TimeoutError, Timeout)
    # Docker SDK wraps connection/version failures; inspect only exception types.
    for _ in range(4):
        if isinstance(exc, timeout_types):
            return "api_timeout"
        exc = exc.__cause__ or exc.__context__
        if exc is None:
            break
    return "environment_error"


def _bounded_response(client: Any, container_id: str, checkpoint: Callable[[], None]) -> bytes:
    # SDK logs(stream=False) buffers everything, and stream=True may allocate an entire
    # Docker frame. Use bounded HTTP chunks, then validate the non-TTY multiplex framing.
    api = client.api
    response = api._get(
        api._url("/containers/{0}/logs", container_id),
        stream=True,
        params={"stdout": 1, "stderr": 1, "follow": 0, "tail": 2},
    )
    wire = bytearray()
    try:
        response.raise_for_status()
        for chunk in response.iter_content(chunk_size=512):
            checkpoint()
            if len(wire) + len(chunk) > 2 * _MAX_RESPONSE:
                raise ValueError("oversized verifier response")
            wire.extend(chunk)
    finally:
        response.close()
    checkpoint()
    output = bytearray()
    offset = 0
    while offset < len(wire):
        if len(wire) - offset < 8:
            raise ValueError("truncated verifier frame")
        stream, length = struct.unpack_from(">BxxxI", wire, offset)
        if stream != 1 or wire[offset + 1 : offset + 4] != b"\0\0\0":
            raise ValueError("unexpected verifier stream")
        offset += 8
        if length > _MAX_RESPONSE - len(output) or length > len(wire) - offset:
            raise ValueError("invalid verifier frame size")
        output.extend(wire[offset : offset + length])
        offset += length
    return bytes(output)


def _response_result(raw: bytes, spec: VerificationSpec | None = None) -> dict[str, Any]:
    # Only this fixed, bounded trusted-verifier response is decoded on the host.
    if len(raw) > _MAX_RESPONSE:
        raise ValueError("oversized verifier response")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate response key")
            result[key] = value
        return result

    data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
    if type(data) is not dict or set(data) != {
        "status",
        "failure_category",
        "schema_valid",
        "errors",
    }:
        raise ValueError("invalid verifier response")
    errors = data["errors"]
    if type(data["schema_valid"]) is not bool or type(errors) is not list:
        raise ValueError("invalid verifier schema response")
    if not data["schema_valid"]:
        if (
            errors != []
            or data["status"] != "failed"
            or data["failure_category"] != "artifact_invalid"
        ):
            raise ValueError("inconsistent verifier response")
    else:
        names = _metric_names(spec.rule_id if spec else "pysindy-linear-v1")
        threshold = spec.threshold if spec else 1e-4
        if len(errors) != len(names) or any(
            type(value) not in (int, float)
            or not math.isfinite(value)
            or not 0 <= value <= 1_000_100
            for value in errors
        ):
            raise ValueError("invalid verifier metrics")
        passed = all(value < threshold for value in errors)
        if data["status"] != ("passed" if passed else "failed") or data["failure_category"] != (
            None if passed else "metric_not_met"
        ):
            raise ValueError("inconsistent verifier metrics")
    return data


def _apply_response(result: VerificationResult, data: dict[str, Any]) -> None:
    lorenz = result.rule_id == "sindy-lorenz-v1"
    rows, columns, threshold = (3, 56, 1e-3) if lorenz else (2, 6, 1e-4)
    result.status = data["status"]
    result.failure_category = data["failure_category"]
    result.summary = (
        "输出系数符合固定数值标准。" if result.status == "passed" else "产物未通过独立验证。"
    )
    result.checks = [
        VerificationCheck(
            name="产物结构",
            passed=data["schema_valid"],
            detail=f"严格 JSON 协议：版本 1、{rows} 行 {columns} 列有限系数，无额外字段。",
        ),
        VerificationCheck(
            name="系数指标",
            passed=result.status == "passed",
            detail=f"按完整系数行重算最大绝对误差；{rows} 行均须严格小于 {threshold:g}。",
        ),
    ]
    result.metrics = [
        MetricResult(
            name=name, observed=value, threshold=threshold, comparison="lt", passed=value < threshold
        )
        for name, value in zip(_metric_names(result.rule_id or "pysindy-linear-v1"), data["errors"])
    ]


def verify_artifact(
    raw: bytes,
    spec: VerificationSpec,
    *,
    timeout_seconds: int,
    cancelled: Callable[[], bool],
    client=None,
) -> VerificationResult:
    """Verify a snapshot in a one-shot local Docker container, never in the producer."""
    deadline = time.monotonic() + max(0, timeout_seconds)
    result = VerificationResult(
        level="target",
        status="blocked",
        rule_id=spec.rule_id,
        validator_digest=spec.validator_digest,
        artifact_sha256=hashlib.sha256(raw).hexdigest(),
        code_under_test=spec.code_under_test,
        dataset=spec.dataset,
        source=spec.source,
        limitations=_limitations(spec.rule_id),
    )
    reason = None
    owns_client = client is None
    original_timeout = None
    client_ready = False
    container = None
    attempted_create = False
    phase = "rule"
    name = f"paperharness-verifier-{uuid.uuid4().hex}"

    def diagnose(code, at=None):
        # Store only fixed phase/code values, never SDK exception text or host paths.
        result.diagnostics.append(VerificationDiagnostic(phase=at or phase, code=code))

    def diagnose_block(reason):
        diagnose({
            "verification_cancelled": "cancelled",
            "verification_timeout": "deadline_exceeded",
            "verification_rule_changed": "rule_changed",
        }[reason])

    def checkpoint():
        _checkpoint(deadline, cancelled)
        if client_ready:
            client.api.timeout = min(_API_TIMEOUT, max(0.001, deadline - time.monotonic()))

    try:
        checkpoint()
        # Read ONCE: the digest check and -c command use exactly the same immutable bytes.
        script = (_LORENZ_VALIDATOR if spec.rule_id == "sindy-lorenz-v1" else _VALIDATOR).read_bytes()
        if (
            hashlib.sha256(script).hexdigest() != spec.validator_digest
            or spec != verification_spec(spec.rule_id)
        ):
            raise _Blocked("verification_rule_changed")
        checkpoint()
        if len(raw) > _MAX_ARTIFACT:
            _apply_response(
                result,
                {
                    "status": "failed",
                    "failure_category": "artifact_invalid",
                    "schema_valid": False,
                    "errors": [],
                },
            )
        else:
            if client is None:
                phase = "client"
                client = _new_client(min(_API_TIMEOUT, max(0.001, deadline - time.monotonic())))
            client_ready = True
            original_timeout = client.api.timeout
            checkpoint()
            phase = "image"
            image = client.images.get(_IMAGE)  # Local lookup only; never pull or use run().
            checkpoint()
            image_id = image.id
            if not isinstance(image_id, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
                raise ValueError("non-immutable verifier image")
            result.verifier_image_id = image_id
            if image.attrs.get("Config", {}).get("Volumes"):
                raise ValueError("verifier image declares implicit mounts")
            command = [
                "/usr/local/bin/python",
                "-I",
                "-S",
                "-B",
                "-c",
                script.decode("utf-8"),
                base64.b64encode(raw).decode("ascii"),
            ]
            labels = {
                "paperharness.role": "verifier",
                "paperharness.verification_id": name.removeprefix("paperharness-verifier-"),
                # API has no run_id: link to the parent artifact and invoking service instead.
                # Never use paperharness.run_id (producer stale cleanup owns that label).
                "paperharness.parent_artifact_sha256": result.artifact_sha256,
                "paperharness.parent_pid": str(os.getpid()),
            }
            checkpoint()
            phase = "create"
            attempted_create = True
            container = client.containers.create(
                image=image_id,
                command=command,
                entrypoint=[],
                name=name,
                labels=labels,
                working_dir="/",
                user="1000:1000",
                network_mode="none",
                read_only=True,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                mem_limit="64m",
                memswap_limit="64m",
                nano_cpus=500_000_000,
                pids_limit=32,
                volumes={},
                mounts=[],
                environment={},
                detach=True,
                tty=False,
                stdin_open=False,
                log_config={"type": "json-file", "config": {"max-size": "8k", "max-file": "1"}},
            )
            checkpoint()
            phase = "start"
            container.start()
            checkpoint()
            phase = "wait"
            while True:
                container.reload()
                checkpoint()
                state = container.attrs.get("State", {})
                if state.get("Status") == "exited":
                    if state.get("ExitCode") != 0 or state.get("OOMKilled") or state.get("Error"):
                        raise ValueError("verifier did not exit cleanly")
                    break
                if state.get("Status") not in ("created", "running"):
                    raise ValueError("unexpected verifier state")
                # Poll only while the independent process is running; API calls are finite.
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))
                checkpoint()
            phase = "response"
            response = _bounded_response(client, container.id, checkpoint)
            _apply_response(result, _response_result(response, spec))
            checkpoint()
    except _Blocked as exc:
        reason = exc.reason
        diagnose_block(reason)
    except Exception as exc:  # noqa: BLE001 - SDK faults become blocked evidence, not metric failures.
        reason = "verification_error"
        diagnose(_fault_code(exc))
        try:
            _checkpoint(deadline, cancelled)
        except _Blocked as exc:
            reason = exc.reason
            diagnose_block(reason)
        except Exception:  # noqa: BLE001 - Cancellation lookup failure still blocks verification.
            reason = "verification_error"
    finally:
        if client_ready:
            try:
                client.api.timeout = _CLEANUP_TIMEOUT
                if attempted_create:
                    try:
                        # A create response may be lost AFTER creation: recover by our unique name.
                        client.api.remove_container(
                            container.id if container is not None else name, force=True
                        )
                    except Exception as exc:
                        if getattr(exc, "status_code", None) != 404:
                            raise
            except Exception as exc:  # noqa: BLE001 - Uncertain cleanup must block success.
                reason = "verification_error"
                diagnose(_fault_code(exc), "cleanup")
            finally:
                try:
                    if owns_client:
                        client.close()
                    elif original_timeout is not None:
                        client.api.timeout = original_timeout
                except Exception as exc:  # noqa: BLE001 - Client cleanup failure must block success.
                    reason = "verification_error"
                    diagnose(_fault_code(exc), "client_cleanup")
        if reason is None:
            phase = "cleanup"
            try:
                _checkpoint(deadline, cancelled)
            except _Blocked as exc:
                reason = exc.reason
                diagnose_block(reason)
            except Exception as exc:  # noqa: BLE001 - Uncertain cancellation must block success.
                reason = "verification_error"
                diagnose(_fault_code(exc), "cleanup")
        result.checked_at = datetime.now(UTC)
    if reason:
        result.status = "blocked"
        result.failure_category = reason
        result.summary = {
            "verification_cancelled": "独立验证已取消。",
            "verification_timeout": "独立验证超时。",
            "verification_rule_changed": "验证规则已变化，请重新体检。",
            "verification_error": "独立验证环境或清理不可用，无法确认结果。",
        }[reason]
        result.checks = []
        result.metrics = []
        if result.diagnostics:
            last = result.diagnostics[-1]
            phases = {
                "rule": "规则检查", "client": "连接 Docker", "image": "本地镜像检查",
                "create": "创建验证容器", "start": "启动验证容器", "wait": "等待验证容器",
                "response": "读取验证响应", "cleanup": "清理验证容器",
                "client_cleanup": "释放 Docker 连接",
            }
            result.summary += f" 阶段：{phases[last.phase]}（{last.code}）。"
    return result
