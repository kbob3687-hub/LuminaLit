# LuminaLit

**PaperHarness** —— 基于隔离沙箱的学术论文代码自动复现与环境自愈 Agent 系统。

给它一个公开仓库地址，它在一个一次性 Docker 容器里装依赖、跑命令；跑挂了就分类失败、问模型、在受限动作集里自动修复、再验证，全程隔离、可控、可复核。

---

## 它解决什么问题

论文代码经常跑不起来：依赖没装、版本冲突、路径写死、要 GPU……人工复现又慢又不可靠。PaperHarness 让这件事变成一条可观测的流水线：

- **隔离**：每个任务一间用完即焚的容器，非 root、断网、限资源，跑完不留渣
- **可控**：模型只能通过 7 个受限动作操作沙箱，每步过校验门（ACI），动作不合法就动不了
- **自愈**：跑挂了自动分类失败、问模型怎么修、在预算内重试，验证始终由真实命令产物说了算
- **可复核**：每次运行产出 patch、环境清单、事件时间线、Markdown 报告，成功必须由真实产物验证，虚假成功为 0

---

## 架构

```
用户 ──HTTP──▶ FastAPI ──▶ HarnessRunner（状态机 + 预算 + 事件）
                              │
            ┌─────────────────┼─────────────────┐
            ▼                 ▼                 ▼
        LLM 客户端        ACI 校验层         Docker 沙箱
        （出动作）       （动作合法性门）    （一次性容器）
                              │
                              ▼
                         真实命令 / 产物
```

| 模块 | 职责 | 负责 |
|---|---|---|
| `app/` | FastAPI、服务组装、工作线程 | 队长 |
| `harness/` | 状态机、验证、预算、事件、clone | 队长 |
| `agent/` | LLM 客户端、失败分类、提示 | 队长 |
| `sandbox/` | Docker 生命周期（实现归队长，安全测试归队员审） | 队长 / 队员 |
| `aci/` | 动作校验层（路径、依赖、环境变量、替换） | 队员 |
| `targets/` | manifest 校验与加载 | 队长 |
| 前端 | 评委看到的界面 | 队员 |

自愈范式：**外层确定性状态机 + 内层 ReAct 式工具调用循环**。模型只挑下一步动作，循环、重验、认输全由状态机控制。

---

## 快速开始

### 1. 安装

需要 Python 3.11 和 Docker。

```
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev,docker]"
```

### 2. 构建沙箱基础镜像

```
docker build -t paperharness/base-py3.10 -f docker/base-py3.10.Dockerfile docker/
```

### 3.（可选）配置真 LLM

支持任何 OpenAI 兼容接口，已用 DeepSeek 实测：

```powershell
$env:PAPERHARNESS_LLM_BASE_URL='https://api.deepseek.com'
$env:PAPERHARNESS_LLM_API_KEY='sk-...'
$env:PAPERHARNESS_LLM_MODEL='deepseek-flash'
```

不配则自动落回假模型（测试 / CI 不受影响）。

### 4. 启动服务

```
.venv/Scripts/python -m uvicorn paperharness.app.main:app --port 8000
```

打开 `http://localhost:8000/api/health` 应返回 `{"status":"ok"}`，`/api/targets` 列出内置靶场。

### 5. 跑一个靶场

```powershell
.venv/Scripts/python scripts/e2e_docker.py        # 端到端冒烟（本地仓库）
.venv/Scripts/python scripts/spike_sandbox.py     # 沙箱九项安全验收
.venv/Scripts/python scripts/smoke_llm.py         # LLM 三调用点冒烟
.venv/Scripts/python runs-test/run_target.py clean-example   # 跑指定靶场
```

---

## 内置靶场

8 个固定 commit 的靶场，托管在 [kbob3687-hub/paperharness-targets](https://github.com/kbob3687-hub/paperharness-targets)，每分支一个失败类型：

| 靶场 | 失败类型 | 预期 |
|---|---|---|
| `clean-example` | 无 | 一次成功 |
| `missing-dependency` | 缺依赖 | 装包修复 |
| `version-conflict` | 版本冲突 | 锁版本修复 |
| `hardcoded-path` | 路径写死 | 改相对路径 |
| `cuda-only` | CUDA 配置 | 设环境变量切 CPU |
| `deprecated-api` | 废弃 API | 改调用 |
| `missing-private-data` | 缺私有数据 | 判不可修复 |
| `requires-gpu` | 确需 GPU | 判不可修复 |

最后两个是**反向测试**，考察系统会不会诚实地说“修不了”。靶场答案字段不对外暴露，`/api/targets` 只返回 `id / repo_url / command / expected_artifact`。

---

## 配置

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `PAPERHARNESS_SANDBOX` | `docker` | `docker` 或 `fake` |
| `PAPERHARNESS_AGENT` | `auto` | `auto`（有 LLM 配置就用真模型）、`llm`、`fake` |
| `PAPERHARNESS_LLM_BASE_URL` | — | OpenAI 兼容 base URL |
| `PAPERHARNESS_LLM_API_KEY` | — | API key |
| `PAPERHARNESS_LLM_MODEL` | — | 模型名 |
| `PAPERHARNESS_RUNS` | `runs` | 运行存储目录 |
| `PAPERHARNESS_TARGETS` | `targets` | 靶场目录 |

---

## 仓库结构

```
src/paperharness/
  app/        FastAPI、组装入口、工作线程
  harness/    状态机、运行存储
  agent/      LLM 客户端、分类器、假模型
  sandbox/    DockerSession、FakeSession、协议
  contracts/  冻结的数据模型（动作、结果、状态）
  targets/    manifest 契约与加载
docker/       沙箱基础镜像 Dockerfile
scripts/      e2e / spike / LLM 冒烟脚本
targets/      8 个靶场 manifest
tests/        unit / integration / contract
docs/         接口契约、队员开工指南、设计与计划
```

---

## 开发

```
.venv/Scripts/python -m pytest -q      # 全套测试
.venv/Scripts/python -m ruff check .   # All checks passed
```

修改契约（`docs/interface-contract.md`、`src/paperharness/contracts/run.py`、`sandbox/base.py`）需两人 review。分工、review 规则、靶场格式见 `docs/interface-contract.md`；队员起步见 `docs/teammate-onboarding.md`。

---

## 状态

- ✅ 真 Docker 沙箱（九项安全验收通过）、契约冻结、靶场 8 个全部上线
- ✅ 自愈循环状态机 + 真 DeepSeek 客户端（三调用点实测通过）
- ✅ 真 Docker + 真 DeepSeek 端到端跑通
- ✅ 运行中取消（终止容器、阶段边界收敛 `cancelled`）、服务重启恢复（非终态标 `service_interrupted`）、worker 异常兜底（`internal_error`，工作线程不死）
- ⏳ 队员：ACI 校验层、沙箱安全测试、前端
- ⏳ 待补：环境清单依赖版本采集、2 个真实论文靶场替换
