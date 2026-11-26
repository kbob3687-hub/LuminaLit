import base64
import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from paperharness.contracts.preflight import PreflightCheck, PreflightRequest, PreflightResult
from paperharness.contracts.run import RunPlan, RunRequest
from paperharness.harness.store import RunStore
from paperharness.harness.verification import verification_spec
from paperharness.targets.manifest import TargetManifest

PREFLIGHT_TTL = timedelta(minutes=30)
MAX_TARGET_FILE_BYTES = 256 * 1024


class FrozenTarget(BaseModel):
    manifest: TargetManifest
    files: dict[Literal["check.py", "requirements.txt"], str]

    @classmethod
    def capture(cls, target: TargetManifest) -> "FrozenTarget":
        files = {}
        if target.source_dir is not None:
            for name in ("check.py", "requirements.txt"):
                path = target.source_dir / name
                if path.is_symlink():
                    raise ValueError("target injection must not be a symlink")
                if not path.exists():
                    continue
                with path.open("rb") as handle:
                    content = handle.read(MAX_TARGET_FILE_BYTES + 1)
                if len(content) > MAX_TARGET_FILE_BYTES:
                    raise ValueError("target injection exceeds snapshot limit")
                content.decode("utf-8")
                files[name] = base64.b64encode(content).decode("ascii")
        return cls(manifest=target.model_copy(update={"source_dir": None}, deep=True), files=files)

    def materialize(self, directory: Path) -> TargetManifest:
        directory.mkdir(parents=True, exist_ok=True)
        for name, encoded in self.files.items():
            (directory / name).write_bytes(base64.b64decode(encoded, validate=True))
        return self.manifest.model_copy(update={"source_dir": directory}, deep=True)


class PreflightRecord(BaseModel):
    view: PreflightResult
    request: RunRequest
    target: FrozenTarget

    def digest(self) -> str:
        data = self.model_dump(
            mode="json", exclude={"view": {"confirmation_digest", "run_id"}}
        )
        encoded = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def check_digest(self, digest: str) -> bool:
        return hmac.compare_digest(self.digest(), digest)


class PreflightError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 409):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class PreflightService:
    def __init__(self, store: RunStore, targets: list[TargetManifest]):
        self.store = store
        self.targets = targets

    def create(self, request: PreflightRequest) -> PreflightRecord:
        target = next((t for t in self.targets if t.id == request.target_id), None)
        if target is None:
            raise PreflightError("target_not_found", "未找到受维护任务，请刷新任务列表。", 404)
        try:
            frozen = FrozenTarget.capture(target)
            verification = verification_spec(target.verification_rule) if target.verification_rule else None
            run_request = RunRequest(
                repo_url=target.repo_url,
                revision=target.revision,
                python_version=target.python_version,
                expected_artifact=target.expected_artifact,
                timeout_seconds=request.timeout_seconds,
                max_repairs=request.max_repairs,
            )
            if not target.command or any(not part.strip() for part in target.command):
                raise ValueError("target command is empty")
        except (OSError, ValueError) as exc:
            raise PreflightError(
                "preflight_unavailable", "任务配置或注入文件不可用，请维护者检查后重新体检。"
            ) from exc
        now = datetime.now(UTC)
        checks = [
            PreflightCheck(
                name="固定代码版本", status="passed", detail=target.revision,
                source="受维护任务配置；尚未联网核对仓库可达性",
            ),
            PreflightCheck(
                name="运行环境", status="not_checked",
                detail=f"Python {target.python_version}；尚未检测 Docker、镜像及依赖下载。",
                source="受维护任务配置；确认后在运行阶段检查",
            ),
            PreflightCheck(
                name="自动修复服务", status="not_checked",
                detail="未探测模型服务；未配置真实模型时，不能据此认定 AI 修复可用。",
                source="静态体检不发起模型请求",
            ),
            PreflightCheck(
                name="验证范围", status="warning",
                detail=(
                    "运行成功后，在独立、断网、只读容器检查系数 JSON；"
                    f"各方程的最大系数误差须 < {verification.threshold:g}。指标失败不自动调参。"
                    if verification else
                    "当前以退出状态和指定产物是否存在判定试跑结果；不含独立指标验收。"
                ),
                source=verification.source if verification else "当前 Harness 验证逻辑",
            ),
            PreflightCheck(
                name="数据与硬件条件", status="not_checked",
                detail="本次只核对受维护配置，未执行代码或检查外部数据、GPU 可用性。",
                source="静态体检边界",
            ),
        ]
        if target.checkout_paths:
            checks.append(PreflightCheck(
                name="源码检出范围", status="passed",
                detail="固定提交按路径检出：" + "、".join(target.checkout_paths),
                source="受维护任务配置；路径已冻结，不下载其余仓库文件。",
            ))
        for name, encoded in frozen.files.items():
            file_hash = hashlib.sha256(base64.b64decode(encoded)).hexdigest()
            checks.append(PreflightCheck(
                name=f"已冻结 {name}", status="passed", detail=f"SHA-256 {file_hash}",
                source="本地受维护文件快照；排队后不重新读取原文件",
            ))
        limitations = [
            "体检未下载仓库、安装依赖或调用模型，不保证任务可运行。",
            "单步超时不是端到端总预算；克隆和安装仍有各自的时间上限。",
            (
                "独立验收只证明输出系数满足固定数值规则，不认证训练过程或输出来源；"
                "未进行干净环境复跑，不等于完整复现论文。"
                if verification else
                "暂未提供独立指标验收和干净环境复跑；试跑通过不等于完整复现论文。"
            ),
        ]
        if target.command[:2] == ["python", "-I"] and "check.py" in frozen.files and (
            target.verification_rule != "sindy-lorenz-v1"
        ):
            limitations.append("该任务使用隔离模式运行维护者脚本，可能测试已安装包而非检出的仓库源码。")
        if request.max_repairs:
            limitations.append("发生失败时可能调用已配置模型，发送报错和读取的代码片段，并产生模型费用。")
        record = PreflightRecord(
            view=PreflightResult(
                preflight_id=uuid.uuid4().hex,
                target_id=target.id,
                repo_url=target.repo_url,
                created_at=now,
                expires_at=now + PREFLIGHT_TTL,
                confirmation_digest="",
                plan=RunPlan(
                    resolved_revision=target.revision,
                    python_version=target.python_version,
                    command=list(target.command),
                    expected_artifact=target.expected_artifact,
                    source="target_manifest",
                ),
                timeout_seconds=request.timeout_seconds,
                max_repairs=request.max_repairs,
                checks=checks,
                limitations=limitations,
                verification=verification,
            ),
            request=run_request,
            target=frozen,
        )
        record.view.confirmation_digest = record.digest()
        self.save(record)
        return record

    def read(self, preflight_id: str) -> PreflightRecord:
        try:
            data = self.store.read_preflight(preflight_id)
        except ValueError as exc:
            raise PreflightError("preflight_not_found", "体检记录不存在，请重新体检。", 404) from exc
        if data is None:
            raise PreflightError("preflight_not_found", "体检记录不存在，请重新体检。", 404)
        record = PreflightRecord.model_validate(data)
        if not record.check_digest(record.view.confirmation_digest):
            raise PreflightError("confirmation_mismatch", "体检记录已改变，请重新体检。")
        return record

    def save(self, record: PreflightRecord) -> None:
        self.store.write_preflight(record.view.preflight_id, record.model_dump(mode="json"))
