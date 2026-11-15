# LuminaLit

PaperHarness：基于隔离沙箱的学术论文代码自动复现与环境自愈 Agent 系统。

## 本地开发

1. 安装 Python 3.11。
2. 运行 `python -m venv .venv`。
3. 运行 `.venv/Scripts/python -m pip install -e ".[dev]"`。
4. 运行 `.venv/Scripts/python -m pytest -q`。
5. 运行 `.venv/Scripts/python -m ruff check .`。

沙箱开发另需安装 docker SDK：`.venv/Scripts/python -m pip install -e ".[docker]"`。

当前骨架不包含 Docker、真实 LLM 和前端，只验证契约、事件存储、假沙箱、无自愈 Harness 和 API。
