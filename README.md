# LuminaLit

**LuminaLit / PaperHarness** —— 科研代码复现与交接工作台。

接手论文仓库，记录运行与修复，按固定标准核对结果，再用干净环境复跑检验能否交接。

首个源码实验：`paper-lorenz` 在固定版本的 PySINDy 源码上运行 SINDy 论文
Lorenz 系数识别的低噪声子实验。三个方程各 56 个系数，逐行最大绝对误差须
严格小于 `0.001`。它执行维护者适配脚本，不代表论文全部实验或原图逐像素复现。

```powershell
# 真 Git + 真 Docker + 确认单 + 独立验收 + 全新容器复跑 + 错误指标反例
.venv/Scripts/python scripts/accept_lorenz.py
```

脚本不调用模型、不演示 AI 修复；会下载源码与依赖，向 `runs/` 写入真实运行及
`acceptance.json`。实验设置、运行说明和待补证据见 [Lorenz 实验说明](docs/lorenz-case.md)。

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
docker build -t paperharness/base-py3.11 -f docker/base-py3.11.Dockerfile docker/
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

```powershell
# 后端（http://127.0.0.1:8000）
.venv/Scripts/python -m uvicorn paperharness.app.main:app --host 127.0.0.1 --port 8000

# 前端（http://127.0.0.1:5173）
cd frontend && npm install && npm run dev -- --host 127.0.0.1 --port 5173
```

访问 `http://127.0.0.1:5173` 打开可视化界面（`http://localhost:8000/api/health` 返回 `{"status":"ok"}`，`/api/targets` 列出内置靶场）。

### 5. 跑一个靶场

```powershell
.venv/Scripts/python scripts/e2e_docker.py        # 端到端冒烟（本地仓库）
.venv/Scripts/python scripts/spike_sandbox.py     # 沙箱九项安全验收
.venv/Scripts/python scripts/smoke_llm.py         # LLM 三调用点冒烟
.venv/Scripts/python scripts/run_target.py paper-pysindy      # 跑指定靶场（真 clone + 真容器）
```

---

## 内置靶场

**8 个固定 commit 的自造靶场**，托管在 [kbob3687-hub/paperharness-targets](https://github.com/kbob3687-hub/paperharness-targets)，每分支一个失败类型：

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

**科研案例**（适配脚本及依赖随确认单冻结；应区分源码实验和发布包试跑）：

| 靶场 | 论文 | 验证断言 |
|---|---|---|
| `paper-lorenz` | [SINDy 原始论文](https://pmc.ncbi.nlm.nih.gov/articles/PMC4839439/) 的 Lorenz 子实验 | 执行 v1.7.5 检出源码，三个方程各 56 个系数，最大误差均 < 1e-3，独立验收后自动干净复跑 |
| `paper-pysindy` | [arXiv:2004.08424](https://arxiv.org/abs/2004.08424) PySINDy | SINDy 识别线性系统，系数误差 < 1e-4（实测 3.67e-06） |
| `paper-umap` | [arXiv:1802.03426](https://arxiv.org/abs/1802.03426) UMAP | 合成数据 200×10→2 维，重新聚类 ARI=1.000 |

命中靶场时运行计划来自 manifest（不问模型）；论文靶场用 `python -I` 隔离模式跑注入的 `check.py`——防止仓库源码 shadow import，依赖 pin 在靶场注入清单里。UMAP 0.6.0 的 `compatibility_layout` FutureWarning 是版本漂移的活演示素材。

---

## 成功判定：分级验证

**「命令跑通」不等于「结果可信」。** `result.json` 的 `verified` 只在 `verification.status == "passed"` 时为 true，`verification.level` 说明这个结论依据的是哪一级证据：

| level | 依据 | 典型场景 |
|---|---|---|
| `none` | 尚未验证 | 运行未完成，或失败在安装阶段 |
| `execution` | 退出码 0 + 预期产物存在 | 未声明验证规则的普通运行 |
| `artifact` | 产物安全读取／结构校验未通过 | 符号链接、越界路径、旧产物残留被拒 |
| `target` | 独立验证器按预置规则复算指标 | manifest 声明 `verification_rule` 且体检已确认 |

`target` 级最严：先停掉生产容器再取产物快照（防后台进程改写），固定 `artifact_sha256` 与 `validator_digest`，然后把产物字节交给**一次性本地 Docker 容器**里的独立验证器（只查本地镜像、从不拉取），按解析参考值复算并返回 `checks` / `metrics` / `limitations`。验证器脚本和 `check.py` 都由维护者写死，模型无权创建或修改——考生不能自己判卷。证据可从 `GET /api/runs/{id}/verification/input` 下载，接口复核字节数与 SHA-256，不一致返回 409；完整结果在 `verification.json`、`GET /api/runs/{id}/verification` 和报告页的「验证」面板。

目前有 `pysindy-linear-v1`（发布包最小线性任务，误差 < 1e-4）与
`sindy-lorenz-v1`（检出源码上的 Lorenz 子实验，误差 < 1e-3）两条规则。
`target` 级结论只覆盖该规则的指标，干净复跑另由 `replay` 记录；均不代表完整复现论文。

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
| `PAPERHARNESS_GIT_MIRROR` | — | GitHub clone 失败时追加的镜像前缀（如 `https://ghfast.top/`） |
| `PAPERHARNESS_PIP_INDEX_URL` | — | 注入容器内 pip 的 `PIP_INDEX_URL`（如阿里云 PyPI 镜像） |
| `PAPERHARNESS_CORS_ORIGINS` | `localhost/127.0.0.1:5173,4173` | 逗号分隔的允许跨域来源 |

---

## 仓库结构

```
src/paperharness/
  app/        FastAPI、组装入口、工作线程
  harness/    状态机、分级验证、运行存储
  agent/      LLM 客户端、分类器、假模型
  sandbox/    DockerSession、FakeSession、协议
  aci/        受限动作校验层
  contracts/  冻结的数据模型（动作、结果、状态）
  targets/    manifest 契约与加载
frontend/     React + Vite 可视化界面
docker/       沙箱基础镜像 Dockerfile
scripts/      e2e / spike / LLM 冒烟脚本
targets/      11 个案例 manifest（8 自造 + 2 发布包试跑 + 1 论文关联源码实验）
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
- ✅ 内置靶场命中与注入物（check.py + requirements），2 个真实论文靶场真机一次通过（PySINDy / UMAP）
- ✅ ACI 受限动作校验层、沙箱安全集成测试、React + Vite 前端联调上线
- ✅ 环境清单依赖版本采集（pip freeze）、Windows 换行符兼容及容器 PyPI 镜像支持
- ✅ 体检确认：注入物、运行计划与验证规则须经用户确认才放行；规则或 revision 变更一律要求重新体检
- ✅ 分级成功判定：`execution / artifact / target` 三级证据、独立断网只读容器复算指标、证据哈希复核，前端「分级验证证据」面板如实区分「跑通」与「指标达标」，旧结果不推断验证等级
