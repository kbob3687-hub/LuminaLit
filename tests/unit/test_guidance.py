from datetime import UTC, datetime

import pytest

from paperharness.app.guidance import build_run_guidance, explain_event
from paperharness.contracts.run import EventExplanation, RunEvent, RunGuidance, RunResult, RunState


def event(kind, data=None, message="technical detail", state=RunState.REPAIRING, sequence=1):
    return RunEvent(
        run_id="run", sequence=sequence, timestamp=datetime.now(UTC),
        state=state, kind=kind, message=message, data=data or {},
    )


def result(category=None, outcome="failed"):
    return RunResult(
        run_id="run", outcome=outcome, verified=outcome == "succeeded",
        attempts=9, failure_category=category, report_url="/report",
    )


@pytest.mark.parametrize("category, title", [
    ("missing_dependency", "缺少运行依赖"),
    ("version_conflict", "依赖版本不兼容"),
    ("hardcoded_path", "文件路径不适用于当前环境"),
    ("cuda_only", "运行依赖 CUDA"),
    ("deprecated_api", "代码与依赖接口不兼容"),
    ("requires_gpu", "程序报告需要 GPU"),
    ("missing_private_data", "程序报告缺少数据"),
    ("clone_failed", "代码下载失败"),
    ("install_failed", "依赖安装失败"),
    ("smoke_test_failed", "本次测试未通过"),
    ("artifact_missing", "未找到预期输出文件"),
    ("timeout", "运行超时"),
    ("time_budget_exhausted", "总运行时间已用完"),
    ("llm_unavailable", "分析服务暂不可用"),
    ("requirement_rejected", "依赖清单未通过检查"),
    ("internal_error", "运行服务发生异常"),
    ("service_interrupted", "运行服务中断"),
    ("sandbox_unavailable", "运行环境不可用"),
    ("missing_run_command", "未能确认运行命令"),
    ("invalid_expected_artifact", "预期输出路径无效"),
])
def test_failure_categories_have_actionable_guidance(category, title):
    guidance = build_run_guidance("failed", result(category), [])
    assert guidance.title == title
    assert guidance.tone == "error"
    assert guidance.next_steps
    assert RunGuidance.model_validate(guidance.model_dump()) == guidance


@pytest.mark.parametrize("category", [None, "unknown", "new_failure", "none"])
def test_unknown_failure_does_not_invent_a_cause(category):
    guidance = build_run_guidance("failed", result(category), [])
    assert "原因尚未确认" in guidance.detail
    assert "Docker" not in guidance.detail


def test_gpu_and_private_data_are_reports_not_host_inspection():
    gpu = build_run_guidance("failed", result("requires_gpu", "unrepairable"), [])
    assert "程序报告需要 GPU，系统将其判定为无法自动修复" in gpu.detail
    assert "不是对宿主机显卡的检测结论" in gpu.detail
    assert "重新验证" in gpu.next_steps[0]
    data = build_run_guidance("failed", result("missing_private_data", "unrepairable"), [])
    assert "是否私有" in data.detail and "仍需核实" in data.detail
    assert "若需受限数据" in data.next_steps[0]


@pytest.mark.parametrize("category, message, title", [
    ("internal_error", ("DockerException: Error while fetching server API version: "
     "(2, 'CreateFile', 'The system cannot find the file specified.')"), "无法连接 Docker 服务"),
    ("service_interrupted", "sandbox unavailable: docker engine unavailable: boom", "无法连接 Docker 服务"),
    ("sandbox_unavailable", "Cannot connect to the Docker daemon at unix:///var/run/docker.sock", "无法连接 Docker 服务"),
    ("service_interrupted", "sandbox unavailable: network setup failed: Connection refused", "无法连接 Docker 服务"),
    ("service_interrupted", ("sandbox unavailable: image paperharness/base-py3.11 not found; "
     "pull or build it before running"), "缺少运行环境镜像"),
])
def test_docker_current_failure_message_is_specific(category, message, title):
    failure = event(
        "internal_error" if category == "internal_error" else "final",
        {"failure_category": category, "message": message}, state=RunState.FAILED,
    )
    guidance = build_run_guidance("failed", result(category), [failure])
    assert guidance.title == title
    assert explain_event(failure).title == title
    if "镜像" in title:
        assert "docker info" not in guidance.next_steps[0]
    else:
        assert "docker info" in guidance.next_steps[0]
    # worker 的异常消息在顶层 message，不在 data 中。
    failure.data.pop("message")
    failure.message = message
    assert build_run_guidance("failed", result(category), [failure]).title == title


def test_docker_not_inferred_from_traceback_or_recovered_history():
    old = event("final", {
        "failure_category": "service_interrupted",
        "message": "sandbox unavailable: image paperharness/base-py3.10 not found",
    }, state=RunState.FAILED)
    latest = event("final", {"failure_category": "service_interrupted"}, state=RunState.FAILED)
    unrelated = event("read_file", {"ok": True, "stderr_tail": "docker engine unavailable"})
    assert build_run_guidance("failed", result("service_interrupted"), [old, latest, unrelated]).title == "运行服务中断"
    traceback = event("internal_error", {"failure_category": "internal_error"}, message=(
        "ValueError: unrelated error\nTraceback:\nDockerException: "
        "Error while fetching server API version: connection refused"
    ), state=RunState.FAILED)
    assert build_run_guidance("failed", result("internal_error"), [traceback]).title == "运行服务发生异常"
    assert build_run_guidance("failed", result("unknown"), [old]).title == "本次运行未通过"
    assert build_run_guidance("testing", None, [old, unrelated]).title == "执行测试"


def test_success_ignores_old_failure_category_and_events():
    events = [event("diagnosis", {"failure_class": "missing_dependency"})]
    guidance = build_run_guidance("succeeded", result("missing_dependency", "succeeded"), events)
    assert guidance.tone == "success"
    assert "不等于完整复现论文" in guidance.detail
    assert "缺少" not in guidance.title
    final = event("final", {"failure_category": "missing_dependency"}, state=RunState.SUCCEEDED)
    assert explain_event(final).tone == "success"


@pytest.mark.parametrize("state", ["failed", "succeeded"])
def test_terminal_without_result_is_finalizing(state):
    guidance = build_run_guidance(state, None, [])
    assert guidance.title == "正在整理结果"
    assert guidance.tone == "neutral"


@pytest.mark.parametrize("run_result", [None, result(outcome="cancelled")])
def test_cancelled_has_its_own_guidance(run_result):
    guidance = build_run_guidance("cancelled", run_result, [])
    assert guidance.title == "任务已取消"
    assert guidance.tone == "warning"
    assert "重新提交" in guidance.next_steps[0]


def test_repair_summary_counts_only_successful_change_actions():
    events = [
        event("diagnosis", {"attempt": 1}),
        event("diagnosis", {"attempt": 2}),
        event("read_file", {"ok": True}),
        event("search_text", {"ok": True}),
        event("finish_repair", {"ok": True}),
        event("repair_round"),
        event("replace_exact", {"ok": False}),
        event("install_requirement", {"ok": False, "exit_code": 1}),
        event("set_environment"),
    ]
    guidance = build_run_guidance("failed", result("unknown"), events)
    assert "2 轮原因分析" in guidance.repair_summary
    assert "0 个修复动作" in guidance.repair_summary
    assert "已修复" not in guidance.repair_summary
    events += [event(kind, {"ok": True}) for kind in (
        "replace_exact", "install_requirement", "set_environment"
    )]
    assert "3 个修复动作" in build_run_guidance("testing", None, events).repair_summary


@pytest.mark.parametrize("state, title", [
    ("queued", "等待开始"), ("preparing", "下载代码"), ("planning", "确认运行方式"),
    ("installing", "安装依赖"), ("testing", "执行测试"),
    ("diagnosing", "分析失败原因"), ("repairing", "尝试修复"),
])
def test_running_stages(state, title):
    guidance = build_run_guidance(state, None, [])
    assert guidance.title == title
    assert guidance.tone == "neutral"
    assert guidance.detail


def test_planning_uses_latest_relevant_event_not_arbitrary_last_event():
    events = [event("plan", state=RunState.PLANNING)]
    assert build_run_guidance("planning", None, events).title == "准备创建运行环境"
    events += [event("sandbox_start", state=RunState.PLANNING), event("custom")]
    assert build_run_guidance("planning", None, events).title == "创建运行环境"
    events += [event("sandbox_ready", state=RunState.PLANNING), event("custom")]
    assert build_run_guidance("planning", None, events).title == "运行环境已创建"
    assert build_run_guidance("installing", None, events).title == "安装依赖"


def test_install_skip_is_not_success_or_failure():
    explanation = explain_event(event("install", message="no requirements.txt, install skipped"))
    assert explanation.title == "已跳过依赖安装"
    assert explanation.tone == "neutral"
    assert "不代表程序无需依赖" in explanation.detail


@pytest.mark.parametrize("kind", ["install", "test", "install_requirement", "run_smoke_test"])
def test_nonzero_command_exit_is_not_a_rejection(kind):
    explanation = explain_event(event(kind, {"ok": False, "exit_code": 1}))
    assert explanation.tone == "error"
    assert "非零退出码" in explanation.detail
    assert "被拒绝" not in explanation.title
    timed_out = explain_event(event(kind, {"ok": False, "exit_code": None, "timed_out": True}))
    assert "超时" in timed_out.title


@pytest.mark.parametrize("kind, message", [
    ("replace_exact", "path_escape:../secret"),
    ("install_requirement", "requirement_not_allowed"),
    ("set_environment", "env_not_allowed:PATH"),
])
def test_rejected_actions_are_not_successful(kind, message):
    explanation = explain_event(event(kind, {"ok": False}, message))
    assert "被拒绝" in explanation.title
    assert explanation.tone == "warning"
    assert "没有执行" in explanation.detail


def test_file_failure_is_not_reported_as_safety_rejection_or_success():
    explanation = explain_event(event("replace_exact", {"ok": False}, "replace_not_found"))
    assert "未完成" in explanation.title
    assert "文件未修改" in explanation.detail
    assert "检查" not in explanation.detail
    unknown = explain_event(event("read_file", {"ok": False}, "unrecognized failure"))
    assert "未成功" in unknown.detail
    assert unknown.tone != "success"


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("kind, key, value", [
    ("read_file", "path", "src/main.py"),
    ("search_text", "path", "src"),
    ("replace_exact", "path", "main.py"),
    ("install_requirement", "requirement", "numpy==1.26.4"),
    ("set_environment", "name", "OMP_NUM_THREADS"),
])
def test_actions_use_real_subjects_from_new_or_historical_data(nested, kind, key, value):
    data = {"ok": True, **({"request": {key: value}} if nested else {key: value})}
    explanation = explain_event(event(kind, data))
    assert value in explanation.title
    assert "正在" not in explanation.title + explanation.detail


def test_historical_final_event_uses_result_without_changing_raw_fields():
    final = event("final", {"verified": False}, "run unrepairable", state=RunState.FAILED)
    before = final.model_dump()
    explanation = explain_event(final, result("requires_gpu", "unrepairable"))
    assert explanation.title == "程序报告需要 GPU"
    assert final.model_dump() == before


def test_unknown_event_is_neutral_and_does_not_echo_raw_details():
    raw = event("future_kind", {"ok": True}, "Traceback: secret technical content")
    before = raw.model_dump()
    explanation = explain_event(raw)
    assert explanation.tone == "neutral"
    assert "Traceback" not in explanation.detail
    assert raw.model_dump() == before
    assert EventExplanation.model_validate(explanation.model_dump()) == explanation


def test_artifact_failure_uses_last_test_and_real_plan():
    events = [event("plan", {"expected_artifact": "out.txt"}), event("test", {"exit_code": 0})]
    assert build_run_guidance("failed", result("smoke_test_failed"), events).title == "未找到预期输出文件"
    events.append(event("test", {"exit_code": 1}))
    assert build_run_guidance("failed", result("smoke_test_failed"), events).title == "本次测试未通过"


def test_budget_exhaustion_does_not_claim_repair_was_successful():
    guidance = build_run_guidance("failed", result("unknown", "budget_exceeded"), [])
    assert "原因尚未确认" in guidance.detail
    assert "预算已用完" in guidance.detail
    assert "0 个修复动作" in guidance.repair_summary
