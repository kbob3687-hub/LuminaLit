"""OpenAI 兼容 LLM 客户端：结构化输出，规则优先分类。

环境变量：
- PAPERHARNESS_LLM_BASE_URL（如 https://api.openai.com/v1 或本地兼容网关）
- PAPERHARNESS_LLM_API_KEY
- PAPERHARNESS_LLM_MODEL

每个决策点一次调用，JSON 输出用 Pydantic 校验；解析失败重试两次后
以 finish_repair 兜底，让外层状态机收敛而不是崩溃。
"""

import json
import os

from paperharness.agent.classifier import FailureContext, classify_by_rules
from paperharness.contracts.run import (
    FinishRepairAction,
    Observation,
    RunPlan,
    RunRequest,
    ToolAction,
)

FAILURE_CLASSES = [
    "none",
    "missing_dependency",
    "version_conflict",
    "hardcoded_path",
    "cuda_only",
    "deprecated_api",
    "missing_private_data",
    "requires_gpu",
]

ACTION_SYSTEM_PROMPT = """你是论文代码复现助手，在一个受限沙箱里修复运行失败。
只能输出一个动作的 JSON 对象（字段 action 是判别器）：

- {"action":"read_file","path":"相对路径","start_line":1,"line_count":100}
- {"action":"search_text","query":"文本","path":".","max_results":20}
- {"action":"replace_exact","path":"相对路径","old":"必须唯一匹配的原文","new":"替换后内容"}
- {"action":"install_requirement","requirement":"包名==版本"}
- {"action":"set_environment","name":"CUDA_VISIBLE_DEVICES","value":"-1"}
- {"action":"run_smoke_test"}
- {"action":"finish_repair","summary":"一句话说明本轮修复了什么"}

约束：path 必须相对；old 必须在文件中唯一；环境变量只允许
CUDA_VISIBLE_DEVICES、OMP_NUM_THREADS、MKL_NUM_THREADS、TOKENIZERS_PARALLELISM。
先读文件再改；改完用 run_smoke_test 验证；认为修好了就 finish_repair。
只输出 JSON，不要解释。"""

CLASSIFY_SYSTEM_PROMPT = (
    "把失败输出归类为以下之一，只输出 JSON："
    '{"failure_class": "<类别>"}。类别：' + "、".join(FAILURE_CLASSES)
)


class OpenAICompatibleLLM:
    def __init__(self, base_url: str | None = None, api_key: str | None = None,
                 model: str | None = None, timeout_seconds: float = 60.0):
        self.base_url = (
            base_url or os.environ.get("PAPERHARNESS_LLM_BASE_URL", "")
        ).rstrip("/")
        self.api_key = api_key or os.environ.get("PAPERHARNESS_LLM_API_KEY", "")
        self.model = model or os.environ.get("PAPERHARNESS_LLM_MODEL", "")
        self.timeout_seconds = timeout_seconds
        if not self.base_url or not self.model:
            raise ValueError("需要 PAPERHARNESS_LLM_BASE_URL 和 PAPERHARNESS_LLM_MODEL")

    def _chat_json(self, system: str, user: str) -> dict:
        import httpx

        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        response = httpx.post(
            f"{self.base_url}/chat/completions",
            headers=headers,
            json={
                "model": self.model,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return json.loads(response.json()["choices"][0]["message"]["content"])

    def propose_run_plan(self, request: RunRequest) -> RunPlan:
        data = self._chat_json(
            "根据仓库信息给出运行计划，只输出 JSON："
            '{"resolved_revision":"<commit>","python_version":"3.10|3.11",'
            '"command":["python","main.py"],"expected_artifact":"out.txt 或 null",'
            '"source":"user|target_manifest|repository_analysis"}',
            request.model_dump_json(),
        )
        return RunPlan.model_validate(data)

    def classify_failure(self, context: FailureContext) -> str:
        hit = classify_by_rules(context)
        if hit:
            return hit
        try:
            data = self._chat_json(CLASSIFY_SYSTEM_PROMPT, context.model_dump_json())
            got = data.get("failure_class")
        except Exception:  # noqa: BLE001 - 分类失败不阻断流程
            return "unknown"
        return got if got in FAILURE_CLASSES else "unknown"

    def propose_action(self, history: list[Observation]) -> ToolAction:
        transcript = "\n".join(
            f"[{obs.action}] ok={obs.ok} exit={obs.exit_code} {obs.summary}\n"
            f"{obs.stdout_tail[-500:]}"
            for obs in history
        )
        if not transcript:
            transcript = "（首轮，尚无动作历史，报错见上一条用户消息）"
        last_error: Exception | None = None
        for _ in range(3):
            try:
                data = self._chat_json(ACTION_SYSTEM_PROMPT, transcript)
                return ToolAction.model_validate(data)
            except Exception as exc:  # noqa: BLE001 - 重试后兜底
                last_error = exc
        return FinishRepairAction(
            action="finish_repair", summary=f"model output unparseable: {last_error}"
        )
