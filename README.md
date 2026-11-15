# LuminaLit

PaperHarness：基于隔离沙箱的学术论文代码自动复现与环境自愈 Agent 系统。

## 本地开发

1. 安装 Python 3.11。
2. 运行 `python -m venv .venv`。
3. 运行 `.venv/Scripts/python -m pip install -e ".[dev]"`。
4. 运行 `.venv/Scripts/python -m pytest -q`。
5. 运行 `.venv/Scripts/python -m ruff check .`。

沙箱开发另需安装 docker SDK：`.venv/Scripts/python -m pip install -e ".[docker]"`。

当前骨架不包含真实 LLM 和前端，验证契约、事件存储、真沙箱（Docker）与 API。

## 启动服务

```
.venv/Scripts/python -m uvicorn paperharness.app.main:app --port 8000
```

环境变量：`PAPERHARNESS_SANDBOX=docker|fake`（默认 docker）、`PAPERHARNESS_RUNS=runs`。

端到端验证真沙箱：`.venv/Scripts/python scripts/e2e_docker.py`
