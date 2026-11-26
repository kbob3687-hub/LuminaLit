"""只读、确定性的展示投影；不改写事件、结果，也不调用模型。"""

import re
from collections.abc import Sequence

from paperharness.contracts.run import EventExplanation, RunEvent, RunGuidance, RunResult, RunState

# 标题、用户解释、可手动执行的下一步。技术原文仅保留在原始事件中。
_FAILURES: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "missing_dependency": (
        "缺少运行依赖",
        "程序报告缺少所需的 Python 包，当前环境未能满足运行要求。",
        ("在详情中确认缺失的包名，并按仓库说明补充依赖清单后重新运行。",),
    ),
    "version_conflict": (
        "依赖版本不兼容",
        "已安装或要求的依赖版本之间存在冲突。",
        ("对照仓库支持的 Python 和依赖版本，调整版本约束后重新运行。",),
    ),
    "hardcoded_path": (
        "文件路径不适用于当前环境",
        "程序引用的路径在本次运行环境中不可用，可能写死了原作者的目录。",
        ("在详情中找到出错路径，将其改为仓库内的有效路径，并确认所需文件存在。",),
    ),
    "cuda_only": (
        "运行依赖 CUDA",
        "程序使用了 CUDA 相关功能，本次环境未能满足这部分要求。",
        ("检查仓库是否提供 CPU 运行选项；若没有，按项目说明准备兼容的 CUDA 环境再验证。",),
    ),
    "deprecated_api": (
        "代码与依赖接口不兼容",
        "程序调用的接口可能已弃用，或不被当前依赖版本支持。",
        ("查看详情中的接口名称，按仓库推荐版本安装依赖，或更新对应调用后重新运行。",),
    ),
    "requires_gpu": (
        "程序报告需要 GPU",
        "程序报告需要 GPU，系统将其判定为无法自动修复；这不是对宿主机显卡的检测结论。",
        ("核对仓库的硬件和 CUDA 要求；如确需 GPU，请在符合要求的环境中重新验证，仍需检查其他依赖。",),
    ),
    "missing_private_data": (
        "程序报告缺少数据",
        "程序报告缺少数据，系统将其判定为无法自动修复；数据是否私有、是否需要授权仍需核实。",
        ("对照仓库说明检查数据路径；若需受限数据，请先取得授权并准备数据，再重新验证。",),
    ),
    "confirmation_mismatch": (
        "确认内容无法校验",
        "保存的运行输入与确认记录不一致，任务在执行代码前停止。",
        ("返回任务选择，重新体检并确认；若再次出现，请维护者检查记录完整性。",),
    ),
    "confirmed_revision_mismatch": (
        "代码版本与确认内容不一致",
        "实际下载的代码版本不等于你确认的固定版本，系统未继续安装或执行。",
        ("请维护者检查仓库版本与下载流程，再重新体检并确认。",),
    ),
    "clone_failed": (
        "代码下载失败",
        "未能下载指定的代码仓库或版本，尚未进入程序验证。",
        ("确认仓库地址可公开访问、指定版本存在，并检查网络后重新提交。",),
    ),
    "install_failed": (
        "依赖安装失败",
        "安装命令未成功完成，程序所需环境尚未准备好。",
        ("打开安装详情，检查失败包的版本、Python 兼容性和下载网络，调整后重新运行。",),
    ),
    "smoke_test_failed": (
        "本次测试未通过",
        "程序未满足本次验证条件；可能是命令执行失败，也可能未生成要求的文件。具体原因尚未确认。",
        ("查看最后一次测试详情，核对运行命令、输入数据和预期输出文件后重新运行。",),
    ),
    "metric_not_met": (
        "程序运行完成，但指定指标未达标",
        "独立验证器重新计算的系数误差未满足固定阈值；日志报喜不改变该结论，未继续自动调参。",
        ("查看指标的实测值、阈值及原始产物，核对算法与输入；验收标准不能通过修复动作修改。",),
    ),
    "artifact_invalid": (
        "产物未通过验收",
        "本次输出缺失，或未满足安全读取及结构规则；不能认定指标达标。",
        ("查看验收检查项，核对本次 JSON 产物的字段、维度和数值范围。",),
    ),
    "verification_error": (
        "独立验收未完成",
        "验收环境或清理过程发生错误；这是系统验收故障，不是仓库指标不达标的证据。",
        ("请维护者检查 Docker、可信基础镜像及验收环境，恢复后重新运行。",),
    ),
    "verification_timeout": (
        "独立验收超时",
        "验收未在限定时间内完成，当前没有指标达标结论。",
        ("请维护者检查验收环境和超时记录，再重新运行。",),
    ),
    "verification_rule_changed": (
        "验收规则与确认记录不一致",
        "服务端验收器版本已改变，没有使用新规则替代已确认的规则。",
        ("返回任务选择，重新体检确认当前规则后运行。",),
    ),
    "artifact_missing": (
        "未找到预期输出文件",
        "测试未生成要求的输出文件，因此不能判定本次验证通过。",
        ("核对预期文件的相对路径，并检查运行命令是否确实会生成该文件。",),
    ),
    "timeout": (
        "运行超时",
        "执行在限定时间内未能完成，本次验证未通过。",
        ("检查是否卡在下载或计算，先用更小的输入验证；确需更久时调整超时设置后重新运行。",),
    ),
    "time_budget_exhausted": (
        "总运行时间已用完",
        "本次任务的总时间预算已耗尽，系统停止继续执行和修复。",
        ("查看最后完成的步骤，减少输入规模或耗时步骤，调整超时设置后重新提交。",),
    ),
    "llm_unavailable": (
        "分析服务暂不可用",
        "用于确认运行方式或分析故障的模型服务未能完成请求。",
        ("请维护者检查模型服务地址、凭据和可用性，恢复后重新提交任务。",),
    ),
    "requirement_rejected": (
        "依赖清单未通过检查",
        "依赖声明不符合允许的格式或来源规则，安装前已被拒绝；这不是安装命令执行失败。",
        ("在详情中定位被拒绝的依赖行，按允许的包名与版本格式修改后重新运行。",),
    ),
    "internal_error": (
        "运行服务发生异常",
        "运行服务遇到未处理的异常，任务已停止；具体原因尚未确认。",
        ("保留任务编号并查看异常详情，请维护者检查服务日志，排除故障后重新提交。",),
    ),
    "service_interrupted": (
        "运行服务中断",
        "任务因服务中断而停止，未自动续跑；仅凭当前记录还不能确认具体原因。",
        ("请维护者检查服务重启记录与运行环境，确认恢复后重新提交任务。",),
    ),
    "sandbox_unavailable": (
        "运行环境不可用",
        "无法使用隔离运行环境，本次任务未能继续；具体原因尚未确认。",
        ("请维护者查看环境故障详情，检查 Docker 服务和基础镜像，恢复后重新提交。",),
    ),
    "missing_run_command": (
        "未能确认运行命令",
        "系统没有找到可执行的运行方式，尚未开始验证。",
        ("按仓库说明填写明确的运行命令后重新提交。",),
    ),
    "invalid_expected_artifact": (
        "预期输出路径无效",
        "预期输出必须是代码仓库内的相对文件路径。",
        ("将预期输出改为仓库内的相对文件路径后重新提交。",),
    ),
}
_UNKNOWN_FAILURE = (
    "本次运行未通过",
    "具体失败原因尚未确认，现有记录不足以给出可靠结论。",
    ("打开最后的失败步骤详情，核对运行命令和错误信息；携带任务编号请维护者协助定位。",),
)
_REPLAY_REASONS = {
    "replay_material_incomplete": "复跑所需的冻结材料不完整。",
    "replay_clone_failed": "复跑代码检出失败。",
    "replay_sandbox_unavailable": "复跑运行环境不可用。",
    "replay_verifier_error": "复跑验收器发生故障。",
    "replay_error": "复跑阶段发生未预期错误。",
    "not_started_before_termination": "任务在复跑开始前已终止。",
    "budget_exceeded": "复跑阶段总预算已耗尽。",
    "cancelled": "复跑阶段收到取消请求。",
}
_TERMINAL = {RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELLED}
_CHANGES = {"replace_exact", "install_requirement", "set_environment"}
_ACTIONS = {
    "read_file": "读取文件",
    "search_text": "搜索代码",
    "replace_exact": "修改文件",
    "install_requirement": "安装依赖包",
    "set_environment": "设置环境变量",
    "run_smoke_test": "执行测试",
    "finish_repair": "结束本轮修复决策",
}
_STAGES = {
    RunState.QUEUED: ("等待开始", "任务已排队，轮到后会先下载代码。"),
    RunState.PREPARING: ("下载代码", "正在准备代码仓库，完成后会确认运行方式。"),
    RunState.PLANNING: ("确认运行方式", "正在确认运行命令和验证条件，随后会创建运行环境。"),
    RunState.INSTALLING: ("安装依赖", "正在准备程序需要的依赖，完成后会执行测试。"),
    RunState.TESTING: ("执行测试", "正在执行本次运行命令并检查验证条件；未通过时会按预算分析原因。"),
    RunState.DIAGNOSING: ("分析失败原因", "正在根据本次错误分析原因，随后决定能否尝试修复。"),
    RunState.REPAIRING: ("尝试修复", "正在尝试允许的修复动作，随后会重新测试；修改完成不代表验证通过。"),
    RunState.VERIFYING: ("独立检查指标", "运行端已进入收尾，正在用隔离验证器检查固定数值规则；尚未得出指标结论。"),
}


def _failure(category: str | None, event: RunEvent | None = None):
    # 只识别当前故障事件的短消息；不扫描 traceback、stdout/stderr 或历史已恢复错误。
    if event is not None and category in {
        "internal_error", "service_interrupted", "sandbox_unavailable"
    }:
        message = event.data.get("message", event.message)
        message = message if isinstance(message, str) else ""
        message = message.splitlines()[0].lower() if message else ""
        message = message.removeprefix("sandbox unavailable: ")
        if re.match(r"(?:image \S+ not found|no such image:|imagenotfound:)", message):
            return (
                "缺少运行环境镜像",
                "创建运行环境时未找到所需的 Docker 基础镜像，尚不能启动程序。",
                ("请维护者按任务的 Python 版本拉取或构建对应的 paperharness/base-py 镜像，再重新提交。",),
            )
        if (
            message.startswith((
                "docker engine unavailable", "no docker engine", "cannot connect to the docker daemon"
            ))
            or re.match(r"(?:dockerexception: )?error while fetching server api version", message)
            or (message.startswith("network setup failed:") and any(
                marker in message for marker in (
                    "connection refused", "failed to establish a new connection",
                    "cannot connect to the docker daemon", "createfile", "winerror 2",
                )
            ))
        ):
            return (
                "无法连接 Docker 服务",
                "运行服务无法连接 Docker API，因此无法准备或使用运行环境。",
                ("启动或恢复 Docker 服务（Windows 可检查 Docker Desktop），用 docker info 确认可连接后重新提交。",),
            )
    return _FAILURES.get(category, _UNKNOWN_FAILURE)


def _explanation(title: str, detail: str, tone: str = "neutral") -> EventExplanation:
    return EventExplanation(title=title, detail=detail, tone=tone)


def _action_subject(event: RunEvent) -> str:
    # 兼容旧 demo 的 request 嵌套，但以新版顶层字段为准。
    request = event.data.get("request")
    request = request if isinstance(request, dict) else {}
    for key in ("path", "requirement", "env_name", "name"):
        value = event.data.get(key, request.get(key))
        if isinstance(value, str) and value:
            return f"（{value}）"
    return ""


def _command_explanation(event: RunEvent, label: str) -> EventExplanation:
    if event.data.get("timed_out") is True:
        return _explanation(f"{label}超时", "命令未在限定时间内完成，请查看执行详情。", "error")
    exit_code = event.data.get("exit_code")
    if exit_code is not None and exit_code != 0:
        return _explanation(
            f"{label}失败", "命令已执行，但以非零退出码结束；这是执行失败，不是安全规则拦截。", "error"
        )
    if exit_code == 0 and event.data.get("ok") is not False:
        detail = "命令已正常结束。"
        if event.kind in {"test", "run_smoke_test"}:
            detail += "是否满足预期输出等验证条件，以最终结果为准。"
        return _explanation(f"{label}命令完成", detail, "success")
    return _explanation(f"{label}结果已记录", "现有事件未明确说明命令是否成功，请查看详情。")


def explain_event(event: RunEvent, result: RunResult | None = None) -> EventExplanation:
    """解释已经发生的事实；未知事件不转述技术日志，不猜测成功。"""
    kind = event.kind
    if kind in {"final", "cancel", "internal_error"}:
        if event.state == RunState.CANCELLED or kind == "cancel":
            return _explanation("任务已取消", "任务已停止，不再继续测试或自动修复。", "warning")
        if event.state == RunState.SUCCEEDED:
            if result and result.verification and result.verification.status == "passed":
                return _explanation(
                    "指定任务指标达标" if result.verification.level == "target" else "程序运行通过",
                    result.verification.summary + "；未进行干净环境复跑，不等于完整复现论文。",
                    "success",
                )
            return _explanation(
                "本次验证通过", "仅通过本次设置的验证条件，不等于完整复现论文。", "success"
            )
        category = event.data.get("failure_category")
        if category is None and result is not None and result.run_id == event.run_id:
            category = result.failure_category
        title, detail, _ = _failure(category if isinstance(category, str) else None, event)
        return _explanation(title, detail, "error")
    if kind == "verification":
        status = event.data.get("status")
        if status == "passed":
            return _explanation("独立指标检查通过", "固定规则已完成数值比较，实测指标与证据见验收结果。", "success")
        category = event.data.get("failure_category")
        title, detail, _ = _failure(category if isinstance(category, str) else "verification_error")
        return _explanation(title, detail, "warning" if status == "blocked" else "error")
    if kind == "diagnosis":
        category = event.data.get("failure_class")
        title, detail, _ = _failure(category if isinstance(category, str) else None)
        return _explanation(f"原因分析：{title}", detail, "warning")
    if kind == "install":
        if "no requirements.txt" in event.message and "skipped" in event.message:
            return _explanation(
                "已跳过依赖安装", "仓库没有 requirements.txt，因此没有执行清单安装；不代表程序无需依赖。"
            )
        if event.message.startswith("requirements.txt rejected:"):
            title, detail, _ = _failure("requirement_rejected")
            return _explanation(title, detail, "warning")
        return _command_explanation(event, "依赖安装")
    if kind == "test":
        return _command_explanation(event, "测试")
    if kind in _ACTIONS:
        label = _ACTIONS[kind] + _action_subject(event)
        if event.data.get("timed_out") is True or event.data.get("exit_code") is not None:
            return _command_explanation(event, label)
        if event.data.get("ok") is False:
            reason = event.message.split(":", 1)[0]
            if reason in {"path_escape", "requirement_not_allowed", "env_not_allowed"}:
                return _explanation(
                    f"{label}被拒绝", "动作未通过允许范围检查，没有执行该动作。", "warning"
                )
            detail = {
                "file_not_found": "未找到目标文件，动作未完成。",
                "replace_not_found": "未找到要替换的原文，文件未修改。",
                "replace_not_unique": "替换目标不唯一或原文为空，文件未修改。",
                "invalid_argument": "动作参数无效，未执行该动作。",
                "unknown_action": "不支持该动作，未执行。",
            }.get(reason, "动作未成功完成，具体原因请查看详情。")
            return _explanation(f"{label}未完成", detail, "warning")
        if event.data.get("ok") is True:
            detail = {
                "read_file": "已读取指定文件片段，为原因分析提供线索；没有修改文件。",
                "search_text": "已完成指定范围的搜索；搜索结果不代表已经修改代码。",
                "replace_exact": "已完成指定文本替换，仍需重新测试确认效果。",
                "install_requirement": "已完成本次装包动作，仍需重新测试确认兼容性。",
                "set_environment": "已设置本次运行的环境变量，仍需重新测试确认效果。",
                "run_smoke_test": "测试动作已完成，是否通过验证以最终结果为准。",
                "finish_repair": "本轮修复决策已结束；这不代表实施了修改或验证通过。",
            }[kind]
            return _explanation(f"已{label}", detail, "success" if kind in _CHANGES else "neutral")
        return _explanation(f"{label}记录", "已记录动作，但未明确说明是否成功，请查看详情。")
    facts = {
        "verification_started": ("已开始独立指标验收", "将停止运行端并读取本次产物，再在独立断网只读容器中检查；不加载被修复仓库的模块。", "neutral"),
        "queued": ("任务已排队", "已收到运行请求，等待开始。", "neutral"),
        "preparing": ("代码已下载", "指定版本的代码已准备好。", "success"),
        "target": ("已匹配内置示例", "已加入该示例预设的验证文件。", "neutral"),
        "plan": ("运行方式已确认", "已确定本次运行命令和验证条件。", "success"),
        "sandbox_start": ("已开始创建运行环境", "已发起隔离运行环境的创建，尚未确认就绪。", "neutral"),
        "sandbox_ready": ("运行环境已创建", "隔离运行会话已创建，接下来可安装依赖并测试。", "success"),
        "repair_round": ("本轮修复流程已结束", "本轮动作决策已结束，是否解决问题需要重新测试。", "neutral"),
        "loop_stopped": ("本轮修复尝试已停止", "重复或连续未完成的动作触发了停止条件，避免继续无效尝试。", "warning"),
        "cleanup_failed": ("运行环境清理未完成", "清理资源时发生错误；不改变验证结论，请维护者查看详情并检查残留资源。", "warning"),
    }
    if kind in facts:
        return _explanation(*facts[kind])
    return _explanation("步骤记录", "系统记录了一个步骤，具体信息可在技术详情中查看。")


def build_run_guidance(
    state: str | RunState, result: RunResult | None, events: Sequence[RunEvent]
) -> RunGuidance:
    """依据状态和真实事件投影；attempts 是测试次数，不能用来计算修复次数。"""
    diagnoses = sum(event.kind == "diagnosis" for event in events)
    changes = sum(event.kind in _CHANGES and event.data.get("ok") is True for event in events)
    summary = f"已完成 {diagnoses} 轮原因分析，完成 {changes} 个修复动作。"
    if not changes:
        summary += "尚无已完成的实际修复动作记录。"

    def guidance(title, detail, tone="neutral", next_steps=()):
        return RunGuidance(
            title=title, detail=detail, tone=tone,
            next_steps=list(next_steps), repair_summary=summary,
        )

    if state == RunState.CANCELLED:
        return guidance(
            "任务已取消", "本次任务已停止，不再继续测试或自动修复。", "warning",
            ("如需继续，检查运行参数后重新提交一个任务。",),
        )
    if state in _TERMINAL and result is None:
        return guidance("正在整理结果", "执行阶段已结束，最终结果尚未写入；请稍候再查看。")
    if state == RunState.SUCCEEDED:
        if result and result.verification and result.verification.status == "passed":
            target_passed = result.verification.level == "target"
            replay_passed = result.replay is not None and result.replay.status == "passed"
            if target_passed and replay_passed:
                steps = ["下载复跑材料，核对冻结清单、产物哈希与复跑检查项。"]
                if result.bundle is not None and result.bundle.integrity != "complete":
                    steps.append("复跑已通过，但材料尚未完整交付；查看缺项说明后重试打包。")
                return guidance(
                    "指定等级通过，干净复跑通过",
                    result.verification.summary
                    + "；同一冻结清单在干净环境中按同一验收规则通过。",
                    "success", tuple(steps),
                )
            if target_passed:
                return guidance(
                    "试跑通过，未验证可复跑性",
                    result.verification.summary + "；未进行干净环境复跑，不等于完整复现论文。",
                    "success",
                    ("查看验收检查项、实测指标和原始证据；仅对本次声明的小任务成立。",),
                )
            return guidance(
                "程序运行通过",
                result.verification.summary + "；未进行干净环境复跑，不等于完整复现论文。",
                "success",
                ("当前只完成执行检查，尚未独立验证科研指标。",),
            )
        return guidance(
            "本次验证通过", "仅通过本次设置的验证条件，不等于完整复现论文或确认论文结论。", "success",
            ("查看报告中的运行命令、输出和验证条件；若需完整复现，请按论文要求继续核对数据、指标和实验。",),
        )
    if state == RunState.FAILED:
        category = result.failure_category if result else None
        # 只使用最近一次终止故障，不借用更早的失败/诊断/清理日志推断当前原因。
        failure_event = next((
            event for event in reversed(events)
            if event.state == RunState.FAILED and event.kind in {"final", "internal_error"}
        ), None)
        if failure_event and failure_event.data.get("failure_category") not in {None, category}:
            failure_event = None
        if category == "smoke_test_failed":
            last_test = next((event for event in reversed(events) if event.kind == "test"), None)
            plan = next((event for event in reversed(events) if event.kind == "plan"), None)
            if (last_test and last_test.data.get("exit_code") == 0
                    and not last_test.data.get("timed_out") and plan
                    and plan.data.get("expected_artifact")):
                category = "artifact_missing"
        if (
            result and result.verified and result.replay is not None
            and category in {"replay_failed", "replay_blocked"}
        ):
            reason = _REPLAY_REASONS.get(result.replay.reason or "", "")
            if category == "replay_failed":
                return guidance(
                    "当前运行通过，干净复跑失败",
                    "本次环境已达到声明指标，但同一冻结清单的改动在新目录、新容器中"
                    "未通过同一验收；不能据此认定可复现。",
                    "error",
                    ("查看干净复跑的检查项和指标实测值；本次验收证据仍保留，系统不会自动开启第二轮修复。",),
                )
            return guidance(
                "当前运行通过，复跑条件不足",
                "本次环境已达到声明指标，但干净复跑未完成，"
                "尚不能判断改动在其他环境中是否有效。" + reason,
                "warning",
                ("查看复跑检查项与缺项说明；补齐冻结材料、依赖源或恢复运行环境后重新提交。",),
            )
        title, detail, steps = _failure(category, failure_event)
        if result and result.outcome == "budget_exceeded" and category != "time_budget_exhausted":
            detail += "本次自动尝试预算已用完，未继续修复。"
        return guidance(title, detail, "error", steps)
    if state == RunState.PLANNING:
        latest = next((event for event in reversed(events)
                       if event.kind in {"plan", "sandbox_start", "sandbox_ready"}), None)
        if latest and latest.kind == "sandbox_start":
            return guidance("创建运行环境", "正在创建隔离运行环境，完成后会安装依赖并测试。")
        if latest and latest.kind == "sandbox_ready":
            return guidance("运行环境已创建", "即将安装依赖并执行测试。")
        if latest and latest.kind == "plan":
            return guidance("准备创建运行环境", "运行方式已确认，接下来会创建隔离运行环境。")
    title, detail = _STAGES.get(state, ("等待状态更新", "暂时无法确认当前步骤，请稍候查看详情。"))
    return guidance(title, detail)
