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
from pathlib import Path

from pydantic import TypeAdapter

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
若失败发生在 install 阶段（requirements.txt 安装失败），请用 replace_exact 修改 requirements.txt（Harness 会重跑 pip install -r requirements.txt 验证）。
若失败发生在 test 阶段且缺依赖或版本冲突，请使用 install_requirement（或按需使用 replace_exact / set_environment）。
禁止修改 check.py（确定性验证脚本）；不要重复发出与上一条完全相同且已失败的动作。
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
        last: Exception | None = None
        for _ in range(2):  # 设计 18.4：单次失败做一次短重试，持续失败抛给 runner 记 llm_unavailable
            try:
                data = self._chat_json(
                    "根据仓库信息给出运行计划，只输出 JSON："
                    '{"resolved_revision":"<commit>","python_version":"3.10|3.11",'
                    '"command":["python","main.py"],"expected_artifact":"out.txt 或 null",'
                    '"source":"user|target_manifest|repository_analysis"}',
                    request.model_dump_json(),
                )
                return RunPlan.model_validate(data)
            except Exception as exc:  # noqa: BLE001
                last = exc
        assert last is not None  # 循环至少执行一次
        raise last

    def propose_run_plan_with_workspace(
        self, request: RunRequest, workspace: Path
    ) -> RunPlan:
        if request.command:
            return RunPlan(
                resolved_revision=request.revision or "HEAD",
                python_version=request.python_version or "3.10",
                command=list(request.command),
                expected_artifact=request.expected_artifact,
                source="user",
            )
        context = _inspect_workspace_for_plan(request, workspace)
        system = (
            "根据仓库目录文件列表、README 和依赖文件给出最小复现运行计划，只输出 JSON："
            '{"resolved_revision":"<commit>","python_version":"3.10|3.11",'
            '"command":["python","main.py"],"expected_artifact":"out.txt 或 null",'
            '"source":"repository_analysis"}。'
            '默认 python_version 使用 "3.10"（除非明确要求 3.11）。'
            '如果无法从仓库可靠判断可运行入口命令，请返回 "command":[]。'
        )
        last: Exception | None = None
        for _ in range(2):
            try:
                data = self._chat_json(system, json.dumps(context, ensure_ascii=False))
                return RunPlan.model_validate(data)
            except Exception as exc:  # noqa: BLE001
                last = exc
        assert last is not None
        raise last

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
        parts: list[str] = []
        for obs in history:
            entry = (
                f"[{obs.action}] ok={obs.ok} exit={obs.exit_code} "
                f"timed_out={obs.timed_out} {obs.summary}"
            )
            if obs.stdout_tail:
                entry += f"\n--- stdout ---\n{obs.stdout_tail[-800:]}"
            if obs.stderr_tail:
                entry += f"\n--- stderr ---\n{obs.stderr_tail[-1200:]}"
            parts.append(entry)
        transcript = (
            "\n\n".join(parts)
            if parts
            else "（尚无观测记录，请先用 read_file 或 search_text 查看入口文件）"
        )
        adapter = TypeAdapter(ToolAction)
        last_error: Exception | None = None
        for _ in range(3):
            try:
                data = self._chat_json(ACTION_SYSTEM_PROMPT, transcript)
                return adapter.validate_python(data)
            except Exception as exc:  # noqa: BLE001 - 重试后兜底
                last_error = exc
        return FinishRepairAction(
            action="finish_repair", summary=f"model output unparseable: {last_error}"
        )


def _inspect_workspace_for_plan(request: RunRequest, workspace: Path) -> dict[str, object]:
    files: list[str] = []
    readme_excerpt = ""
    config_parts: list[str] = []
    if workspace.is_dir():
        root_resolved = workspace.resolve(strict=False)
        for current, dirs, filenames in os.walk(root_resolved):
            dirs[:] = sorted(d for d in dirs if d != ".git")
            rel_dir = Path(current).relative_to(root_resolved)
            depth = len(rel_dir.parts)
            if depth >= 3:
                dirs[:] = []
            for fname in sorted(filenames):
                rel = (rel_dir / fname).as_posix() if depth else fname
                files.append(rel)
                if not readme_excerpt and depth == 0 and fname.lower().startswith("readme"):
                    try:
                        readme_excerpt = (Path(current) / fname).read_text(
                            encoding="utf-8", errors="replace"
                        )[:3000]
                    except OSError:
                        pass
                if len(files) >= 60:
                    break
            if len(files) >= 60:
                break
        remaining_cfg = 1000
        for cfg_name in ("requirements.txt", "pyproject.toml"):
            cfg_path = root_resolved / cfg_name
            if cfg_path.is_file() and remaining_cfg > 0:
                try:
                    snippet = cfg_path.read_text(encoding="utf-8", errors="replace")[:remaining_cfg]
                except OSError:
                    continue
                config_parts.append(f"[{cfg_name}]\n{snippet}")
                remaining_cfg -= len(snippet)
    return {
        "request": request.model_dump(mode="json"),
        "files": files[:60],
        "readme_excerpt": readme_excerpt,
        "config_excerpt": "\n".join(config_parts),
    }
