# PaperHarness 开发设计文档

- 日期：2026-11-13
- 状态：待团队评审
- 目标提交日期：2026-11-30
- 项目类型：2026 Qoder 高校创造力大赛，StudyHack 学习科研赛道
- 团队规模：2 人
- 本文性质：开发设计基线，不代表功能已经实现

## 1. 一句话定义

PaperHarness 面向需要复现公开学术论文代码的科研人员和学生：用户只需提供公开 GitHub 仓库地址，系统在隔离 Docker 环境中尝试运行最小示例，并在有限预算内修复常见工程问题，最终给出可审计的运行日志、代码补丁、环境清单和复现报告。

## 2. 核心演示目标

现场或公网演示的主线是：

```text
提交仓库
→ 自动确定运行计划
→ 首次运行失败
→ 展示真实错误
→ Agent 提出受限修复动作
→ Harness 校验并执行
→ 重新运行
→ 真实验证成功
→ 展示补丁、环境和报告
```

UI 是重要的展示层，但不能替代真实执行和验证。

### 2.1 成功定义

一次任务只有同时满足以下条件才可标记为 `verified=true`：

1. 最终运行未超时。
2. 最终命令退出码为 0。
3. 如果运行计划声明了 `expected_artifact`，该文件必须存在且位于当前任务工作区内。

大模型返回“修复成功”不构成成功证据。

### 2.2 复现边界

本项目验证的是“工程可运行性复现”，不是论文实验指标复现。

第一版不承诺：

- 复现论文精度、训练曲线或最终指标。
- 完整训练大型模型。
- 支持 GPU、CUDA 或多机分布式训练。
- 自动阅读论文 PDF 并生成完整实验流程。
- 支持任意恶意代码的强安全隔离。
- 多用户高并发或多 Agent 并行执行。

云端公网部署是比赛硬性交付要求，不属于可选项。多 Agent 和更强的恶意代码隔离只能作为后续研究方向，不进入本次比赛主线。

## 3. 比赛交付约束

作品帖子必须包含：

1. 自我介绍。
2. 作品是什么：一句话说明谁在什么场景下使用。
3. 为什么做：问题或想法来源。
4. 怎么用：不超过 5 步，评委可以照着操作。
5. 用 Qoder 做出来的过程：说明哪些步骤交给 Qoder、人工修改了什么，并保留截图证据。

作品链接必须使用 Qoder Sites 部署并设置为公开访问。原则上不接受仅提交本地文件或压缩包。

分享链接是可选项，不占用核心开发时间。

## 4. 使用流程与最少输入设计

### 4.1 用户输入

默认只要求：

```text
repo_url：公开 GitHub 仓库的 HTTPS 地址
```

以下内容放入“高级设置”，均可省略：

```text
revision：分支、tag 或完整 commit
command：最小运行命令，参数数组形式
expected_artifact：预期产物相对路径
python_version：3.10 或 3.11
```

约束：

- 不接受包含用户名、密码或访问令牌的仓库 URL。
- 不支持私有仓库。
- 评测任务必须固定完整 commit。
- 普通任务未提供 `revision` 时，解析默认分支并记录实际 commit。
- `command` 是参数数组，不是 Shell 字符串。

### 4.2 自动运行计划

系统按以下顺序确定运行计划：

1. 如果仓库属于内置靶场，读取经过人工验证的 target manifest。
2. 否则检查 README、依赖文件和常见入口文件。
3. 需要模型辅助时，让模型返回结构化 `RunPlan`，而不是直接执行自然语言建议。
4. Harness 校验命令形式、路径和资源预算。
5. 无法可靠确定运行命令时，任务以 `failed` 结束，`failure_category=missing_run_command`，UI 明确提示用户只需补充运行命令后重新提交。

系统不能为了“全自动”而随机选择命令并宣称复现成功。

### 4.3 评委操作步骤

公网作品页的使用说明控制在 5 步以内：

1. 打开 Qoder Sites 作品地址。
2. 选择一个内置示例，或粘贴公开 GitHub 仓库地址。
3. 点击“开始复现”。
4. 查看运行、报错和自愈时间线。
5. 查看补丁、环境清单和复现报告。

## 5. 总体架构

```text
评委浏览器
   │
   ▼
Qoder Sites：Vue 3 静态前端
   │ HTTPS + JSON，事件轮询
   ▼
域名 HTTPS
   │
   ▼
Nginx
   │
   ▼
FastAPI / Uvicorn，单进程
   ├── 请求校验与限流
   ├── 单工作线程和内存队列
   ├── Harness 状态机
   ├── Agent / LLM 适配器
   ├── 事件与产物存储
   └── Docker Sandbox
           │
           └── 每次任务一个隔离容器
```

### 5.1 部署分配

Qoder Sites 负责：

- Vue 3 构建后的静态前端。
- 公开展示和评委访问入口。
- 通过 `VITE_API_BASE_URL` 指向后端 HTTPS API。

云服务器负责：

- Ubuntu 22.04。
- Docker Engine。
- Python 3.11。
- FastAPI 和 Uvicorn。
- Nginx 和 HTTPS。
- 每次任务的 Docker 容器。
- 运行日志和产物目录。

LLM API 只由后端访问。API Key 不进入前端、Docker 容器或事件日志。

### 5.2 云服务器现实约束

现有服务器为 2 vCPU、4 GiB 内存。设计必须遵守：

- 同时最多一个运行中任务。
- 其他任务进入有界队列，队列最多保留 5 个等待任务。
- 单个容器最多使用约 1.5 核 CPU。
- 单个容器内存上限为 2 GiB，并限制交换空间。
- Uvicorn 只启动一个进程，不能通过增加 worker 进程绕过串行约束。
- 不做多容器并行和自动扩容。

服务器当前处于停止状态，截图只显示私网 IP。部署前必须完成：

1. 启动实例。
2. 绑定公网 IP 或 EIP。
3. 配置安全组，只开放必要的 80、443 和管理端口。
4. 将已有域名解析到公网地址。
5. 配置 HTTPS。
6. 限制后端 CORS 来源为正式 Qoder Sites 域名和本地开发地址。

### 5.3 本地开发环境

队长开发机采用 Windows 11、WSL2、Docker Desktop 和 CPU，内存 16 GiB。

本地与云端共用同一套 Python 后端和 Docker Sandbox。区别只通过环境配置表达：

- 本地 API 地址。
- 云端 API 地址。
- 运行产物根目录。
- LLM 服务商配置。

不维护两套业务实现。

## 6. 技术选型

### 6.1 前端

- Vue 3。
- Vite。
- TypeScript。
- 原生 `fetch` 或轻量封装访问 API。
- 一秒一次事件轮询，不引入 WebSocket。
- 组件和页面样式由队员负责，避免引入大型 UI 框架阻塞主线。

### 6.2 后端

- Python 3.11。
- FastAPI。
- Pydantic v2。
- Uvicorn，单进程。
- Docker Python SDK。
- 官方 OpenAI Python SDK，通过 OpenAI 兼容配置接入团队提供的模型服务。
- `packaging.Requirement` 校验依赖动作。
- `pytest` 和 `ruff`。

不使用 LangChain、AutoGen、CrewAI、Celery、Redis、Kafka、Kubernetes 或独立前端后端消息服务。

### 6.3 存储

采用文件系统，不引入数据库：

```text
runs/<run_id>/
├── request.json
├── run_plan.json
├── status.json
├── events.jsonl
├── result.json
├── environment.json
├── patch.diff
├── report.md
└── logs/
```

内存队列只负责调度，文件系统负责结果和证据。事件文件只追加写入；状态和结果文件通过临时文件加原子替换更新，避免前端读到半份 JSON。

服务重启后：

- 扫描 `runs/`。
- 将仍处于非终态的运行标记为 `failed`。
- `failure_category=service_interrupted`。
- 不自动续跑，不假装任务仍在执行。

公网临时运行产物保留 24 小时。正式评测产物单独保存，不按临时任务清理。

## 7. 核心组件职责

### 7.1 API 层

只负责：

- 请求和响应校验。
- 限流和队列容量检查。
- 创建、查询和取消任务。
- 提供事件、补丁和报告。
- 将内部异常转换成稳定错误格式。

API 层不包含 Agent 决策或 Docker 细节。

### 7.2 Harness

Harness 是确定性调度者和最终裁判，负责：

- 状态流转。
- 运行计划校验。
- 初次安装和测试。
- 失败分类。
- 调用 Agent 获取下一步动作。
- 动作合法性检查。
- 修复次数、时间和日志长度预算。
- 重新运行和最终验证。
- 事件记录、产物生成和容器清理。

### 7.3 Agent

Agent 只负责根据裁剪后的上下文提出下一步动作。

输入包括：

- 失败类别。
- 标准错误尾部。
- 依赖和环境摘要。
- 与失败相关的文件片段。
- 已尝试动作及其结果。
- 剩余修复预算。

不输入：

- 完整仓库。
- 完整日志。
- 宿主机环境变量。
- API Key。
- 与当前失败无关的大量上下文。

### 7.4 ACI

ACI 是模型动作与真实执行之间的唯一桥梁，负责：

- 分段读文件。
- 有界搜索。
- 唯一匹配精确替换。
- 安装经过校验的 Python 依赖。
- 设置允许列表内的环境变量。
- 请求重新运行最小示例。
- 返回简短、结构化 Observation。

Agent 不直接接触 Docker SDK。

### 7.5 Docker Sandbox

Sandbox 只负责：

- 创建和销毁容器。
- 挂载当前任务工作区。
- 执行参数数组命令。
- 设置资源限制。
- 安装阶段联网和验证阶段断网。
- 收集退出码、日志和执行时长。
- 超时终止。
- 导出代码 diff。

## 8. 运行状态机

```text
QUEUED
  → PREPARING
  → PLANNING
  → INSTALLING
  → TESTING
      ├── 验证通过 → SUCCEEDED
      └── 验证失败且仍有预算
            → DIAGNOSING
            → REPAIRING
            → TESTING

无法确定命令、不可恢复错误、预算耗尽或修复后仍失败
  → FAILED

用户取消
  → CANCELLED
```

约束：

- 默认最多修复 3 次，接口允许的范围是 0 到 5 次。
- 单条命令默认最多 5 分钟。
- 整个任务最长 15 分钟。
- 连续重复同一失败和同一动作时停止，不消耗完全相同的修复循环。
- 超时、内存不足和服务中断是失败原因，不新增大量顶层状态。
- MVP 不做断点续跑。

## 9. 核心数据契约

以下定义是接口基线，实际字段名变更必须双方 review。

### 9.1 RunRequest

```python
class RunRequest(BaseModel):
    repo_url: str
    revision: str | None = None
    command: list[str] | None = None
    expected_artifact: str | None = None
    python_version: Literal["3.10", "3.11"] | None = None
    timeout_seconds: int = Field(default=300, ge=30, le=900)
    max_repairs: int = Field(default=3, ge=0, le=5)
```

### 9.2 RunPlan

```python
class RunPlan(BaseModel):
    resolved_revision: str
    python_version: Literal["3.10", "3.11"]
    command: list[str]
    expected_artifact: str | None
    source: Literal["user", "target_manifest", "repository_analysis"]
```

`resolved_revision` 必须记录本次实际运行的 commit。

### 9.3 RunState

```python
class RunState(StrEnum):
    QUEUED = "queued"
    PREPARING = "preparing"
    PLANNING = "planning"
    INSTALLING = "installing"
    TESTING = "testing"
    DIAGNOSING = "diagnosing"
    REPAIRING = "repairing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
```

### 9.4 RunEvent

```python
class RunEvent(BaseModel):
    run_id: str
    sequence: int
    timestamp: datetime
    state: RunState
    kind: str
    message: str
    data: dict[str, JsonValue]
    log_path: str | None = None
```

规则：

- `sequence` 在一次运行内严格递增。
- 事件只追加，不修改历史。
- `data` 中不能包含密钥或完整环境变量。
- 大日志只保存路径和尾部摘要。
- 不记录或展示模型隐式思维链，只记录可审计动作和简短理由。

### 9.5 ExecResult

```python
class ExecResult(BaseModel):
    exit_code: int | None
    timed_out: bool
    duration_ms: int
    stdout_path: str
    stderr_path: str
    stdout_tail: str
    stderr_tail: str
```

### 9.6 Observation

```python
class Observation(BaseModel):
    ok: bool
    action: str
    summary: str
    exit_code: int | None = None
    timed_out: bool = False
    stdout_tail: str = ""
    stderr_tail: str = ""
    log_path: str | None = None
```

非法动作返回 `ok=false`，不能修改环境。

### 9.7 RunResult

```python
class RunResult(BaseModel):
    run_id: str
    outcome: Literal[
        "succeeded",
        "failed",
        "budget_exceeded",
        "unrepairable",
        "cancelled"
    ]
    verified: bool
    attempts: int
    failure_category: str | None
    resolved_revision: str | None
    patch_url: str | None
    environment_url: str | None
    report_url: str
```

## 10. Agent 动作契约

模型只能返回以下动作之一。

### 10.1 ReadFile

```json
{
  "action": "read_file",
  "path": "src/main.py",
  "start_line": 1,
  "line_count": 100
}
```

最多读取 200 行。

### 10.2 SearchText

```json
{
  "action": "search_text",
  "query": "cuda",
  "path": ".",
  "max_results": 20
}
```

最多返回 50 条，单条内容需要截断。

### 10.3 ReplaceExact

```json
{
  "action": "replace_exact",
  "path": "src/main.py",
  "old": "device = \"cuda\"",
  "new": "device = \"cpu\""
}
```

`old` 必须在目标文件中唯一匹配，否则动作失败。

### 10.4 InstallRequirement

```json
{
  "action": "install_requirement",
  "requirement": "numpy==1.26.4"
}
```

依赖字符串必须通过 `packaging.Requirement` 校验。拒绝：

- Shell 参数。
- 分号和换行。
- 直接 URL。
- 本地宿主机路径。
- `-r`、`-e` 等额外 pip 参数。

### 10.5 SetEnvironment

```json
{
  "action": "set_environment",
  "name": "CUDA_VISIBLE_DEVICES",
  "value": "-1"
}
```

环境变量名采用允许列表。初始允许：

- `CUDA_VISIBLE_DEVICES`
- `OMP_NUM_THREADS`
- `MKL_NUM_THREADS`
- `TOKENIZERS_PARALLELISM`

### 10.6 RunSmokeTest

```json
{
  "action": "run_smoke_test"
}
```

实际命令来自已校验的 `RunPlan`，模型不能在此动作中替换命令。

### 10.7 FinishRepair

```json
{
  "action": "finish_repair",
  "summary": "已切换到 CPU，等待最终验证"
}
```

该动作只结束 Agent 决策，不直接产生成功结果。

## 11. Sandbox 与 ACI 接口

### 11.1 SandboxSession

```python
class SandboxSession(Protocol):
    def execute(
        self,
        command: list[str],
        timeout_seconds: int,
    ) -> ExecResult: ...

    def set_network(self, enabled: bool) -> None: ...

    def export_diff(self) -> str: ...

    def close(self) -> None: ...
```

### 11.2 ACI

```python
class AgentComputerInterface(Protocol):
    def execute(self, action: ToolAction) -> Observation: ...
```

Harness 可以调用 Sandbox 和 ACI。Agent 只能看到动作 schema 和 Observation，不能获得 SandboxSession。

## 12. 沙箱安全与资源基线

每个任务必须满足：

- 每个任务独立工作目录和独立容器。
- 容器使用非 root 用户。
- 不挂载 Docker Socket。
- 不使用 `privileged`。
- 删除默认 capabilities。
- 启用 `no-new-privileges`。
- 保留 Docker 默认 seccomp 配置。
- 只挂载当前任务工作区，不挂载宿主机 home、SSH、云密钥或项目根目录。
- 容器内不注入 LLM 密钥。
- 限制 CPU、2 GiB 内存、交换空间和最多 128 个进程。
- 单任务工作区需要限制仓库大小，公开仓库最大下载量为 200 MiB。
- 不自动克隆子模块，不自动下载 Git LFS 内容。
- 安装依赖阶段允许联网，验证阶段默认断开网络。
- 所有命令都有 Harness 侧超时；阻塞中的 Docker SDK 调用不能替代超时控制。
- 超时后终止并清理容器。
- 正常、失败、取消和异常路径都必须执行清理。

路径规则：

- Agent 只能访问容器内 `/workspace` 下的路径。
- 拒绝 `..` 越界。
- 拒绝宿主机绝对路径。
- 解析符号链接后再次确认仍位于工作区。
- `expected_artifact` 也必须是工作区内的相对路径。

Docker 是本次比赛的隔离底座，不宣称能够抵御所有内核漏洞或故意构造的恶意镜像。公网输入仅限公开仓库，并在页面明确说明代码会发送到配置的 LLM 服务进行故障分析。

## 13. 公网 API 契约

所有接口以 `/api` 为前缀。

### 13.1 健康检查

```text
GET /api/health
```

用于部署后检查 API 是否正常，不暴露 Docker 内部信息。

### 13.2 内置示例

```text
GET /api/targets
```

返回可直接运行的内置靶场，供评委一键选择。

### 13.3 创建任务

```text
POST /api/runs
```

成功返回 HTTP 202：

```json
{
  "run_id": "01...",
  "state": "queued"
}
```

队列已满返回 HTTP 429，错误码为 `queue_full`。

### 13.4 查询任务

```text
GET /api/runs/{run_id}
```

返回状态、运行计划、修复次数、结果摘要和产物 URL。

### 13.5 增量查询事件

```text
GET /api/runs/{run_id}/events?after={sequence}
```

只返回大于指定序号的事件。前端终止轮询的条件是任务进入终态。

### 13.6 取消任务

```text
POST /api/runs/{run_id}/cancel
```

取消等待任务或终止当前容器。重复取消返回当前终态，不产生第二个任务。

### 13.7 获取报告

```text
GET /api/runs/{run_id}/report
```

返回 Markdown 报告。

### 13.8 获取补丁

```text
GET /api/runs/{run_id}/patch
```

没有代码修改时返回空 diff 和明确说明，不返回 404 伪装成系统故障。

### 13.9 获取环境清单

```text
GET /api/runs/{run_id}/environment
```

返回 `environment.json`，包括实际 commit、基础镜像、Python 版本、依赖版本和允许记录的环境变量，不包含任何密钥。

### 13.10 统一错误格式

```json
{
  "error": {
    "code": "invalid_repo_url",
    "message": "只支持公开 GitHub HTTPS 仓库",
    "details": {}
  }
}
```

不向公网返回 Python traceback、容器 ID、宿主机绝对路径或环境变量。

## 14. 前端设计

### 14.1 首页

包含：

- 一句话作品说明。
- 内置示例选择。
- 仓库 URL 输入框。
- 默认折叠的高级设置。
- “开始复现”按钮。
- 工程复现而非论文指标复现的边界说明。

### 14.2 运行详情页

包含：

- 当前状态和已用时间。
- 实际 commit、Python 版本和运行命令。
- 动作与 Observation 时间线。
- 修复次数和剩余预算。
- 日志尾部展开区。
- 取消按钮。
- 队列位置或等待提示。

### 14.3 结果页

成功时显示：

- 真实验证通过。
- 最终退出码和产物。
- 代码 diff。
- 环境清单。
- 复现报告。

失败时显示：

- 明确失败类别。
- 已尝试动作。
- 最后错误摘要。
- 是否可以通过补充运行命令重试。
- 日志和报告。

禁止为了展示效果而伪造进度、事件或成功状态。备用演示可以回放真实历史事件，但必须标注“历史运行回放”。

## 15. 工程目录与所有权

```text
paperharness/
├── pyproject.toml
├── README.md
├── .env.example
│
├── src/paperharness/
│   ├── contracts/                 # 双方共同所有
│   ├── app/                       # 队长：FastAPI、服务入口
│   ├── harness/                   # 队长：状态机、验证、预算、事件
│   ├── agent/                     # 队长：LLM、提示、失败分类
│   ├── worker/                    # 队长：单工作线程、队列、恢复
│   ├── sandbox/                   # 队员：Docker 生命周期
│   ├── aci/                       # 队员：受限工具和路径防护
│   └── reporting/                 # 队长：环境、补丁和报告汇总
│
├── frontend/                      # 队员：Vue 3 + Vite + TypeScript
├── targets/                       # 队员维护，队长审核
├── deploy/                        # 队长：Nginx、服务配置、部署脚本
│
├── tests/
│   ├── contract/                  # 双方共同
│   ├── unit/                      # 模块所有者维护
│   ├── integration/               # 队长
│   └── e2e/                       # 队员，队长提供真实 API
│
├── evaluation/                    # 队长
├── docs/                          # 共同
└── runs/                          # 运行产物，不提交 Git
```

## 16. 两人分工与验收

### 16.1 队长

负责：

- 创建最小工程骨架、契约草案、假实现和 CI。
- Harness、Agent、FastAPI、Worker 和报告。
- `FakeLLM`、`FakeSandbox` 和集成测试。
- 后端部署、HTTPS、Nginx 和 Docker 环境。
- 评测执行和结果汇总。
- 技术叙事、最终集成和范围控制。

验收：

- 不用真实模型也能完成端到端测试。
- 坏 JSON、非法动作和重复动作不会导致崩溃。
- 达到修复或时间预算后一定退出。
- 模型自称成功但验证失败时，结果仍为失败。
- 服务重启不会留下永远“运行中”的任务。
- 云端同时只有一个运行容器。

### 16.2 队员

负责：

- Docker Sandbox 和 ACI。
- 路径安全和资源限制测试。
- Vue 3 前端和 Qoder Sites 部署。
- 靶场、内置示例和前端测试。
- UI 截图、操作步骤和 Qoder 过程证据整理。

验收：

- 容器异常、超时和取消后都能清理。
- 路径越界和危险依赖字符串会被拒绝。
- 验证阶段网络关闭。
- 同一靶场连续运行不会共享代码修改。
- 前端可独立完成创建、轮询、取消和结果展示。
- 失败任务不会一直显示“运行中”。
- Qoder Sites 地址无需登录即可打开。

## 17. 防止代码冲突的协作规则

1. 第 0 天共同冻结契约和 API JSON 示例。
2. 队长创建骨架，队员当天 review 后合并，再开始并行开发。
3. 除 `contracts/`、`README.md` 和 `docs/` 外，不直接修改对方目录。
4. 契约变更先修改示例和契约测试，再修改实现。
5. 前端先使用固定假 JSON，后端先使用 FakeSandbox。
6. 每个短分支只完成一个小功能，当天合并。
7. 超过 300 行的 PR 优先拆分。
8. 跨目录修改必须由目录负责人 review。
9. 不直接在 `main` 上开发，不强推共享分支。
10. 每天固定 20 分钟同步接口、阻塞和次日集成点。

## 18. 错误处理

### 18.1 用户输入错误

包括非法 URL、私有仓库、命令格式错误和非法路径。返回稳定错误码，不创建容器。

### 18.2 环境错误

包括依赖安装失败、版本冲突和缺少系统库。允许进入有限自愈；缺少系统库且无法通过当前动作解决时，明确标记不可修复。

### 18.3 执行错误

包括非零退出码、超时、内存不足和产物缺失。只有非零退出码中可识别的四类问题进入修复；超时和内存不足默认终止，避免在 2C4G 服务器上反复放大资源消耗。

### 18.4 模型错误

包括请求失败、结构化输出校验失败和非法动作。

- 模型服务单次失败可按后端配置做一次短重试。
- 持续失败时结束任务，`failure_category=llm_unavailable`。
- 非法动作作为 Observation 返回一次；再次非法则结束任务。
- 不在模型故障时伪造修复结果。

### 18.5 服务错误

API 异常不泄露内部路径。Worker 异常必须记录事件并清理容器。服务重启后的中断任务按 `service_interrupted` 处理。

## 19. 测试策略

### 19.1 契约测试

- 请求和结果 JSON 往返序列化。
- 所有动作 schema 的正反例。
- 前后端共享示例保持一致。
- 真实 Sandbox 与 FakeSandbox 对共同用例返回相同结构。

### 19.2 Harness 单元测试

覆盖：

- 首次成功。
- 首次失败、修复后成功。
- 修复预算耗尽。
- 相同动作重复。
- 非法模型输出。
- 模型不可用。
- 验证失败但模型声称成功。
- 取消和服务中断。
- 缺少运行命令。

### 19.3 Sandbox 集成测试

覆盖：

- 非 root 执行。
- 无 Docker Socket。
- 工作区之外文件不可见。
- 路径穿越和符号链接越界。
- 网络开关。
- CPU、内存和进程限制配置。
- 超时清理。
- 异常清理。
- 连续运行不共享修改。

### 19.4 API 测试

覆盖：

- 创建和查询任务。
- 队列满。
- 增量事件。
- 重复取消。
- 不存在任务。
- 统一错误格式。
- CORS 和响应中不泄露内部信息。

### 19.5 前端与端到端测试

- 使用模拟 API 测试加载、排队、成功、失败和取消状态。
- 至少一条 Playwright 真实链路：选择内置靶场、创建任务、查看事件、打开报告。
- 最终 UI 必须在浏览器实际操作，不能只以类型检查代替功能验证。

## 20. 靶场与评测

### 20.1 靶场组成

总共 8 个任务：

1. 一个无需修复的干净仓库。
2. 缺少 Python 依赖。
3. 明确的依赖版本冲突。
4. 数据或输出路径写死。
5. CUDA-only 配置需要切换到 CPU。
6. 简单的废弃 API。
7. 缺少私有数据，应该不可修复。
8. 确实需要 GPU，应该不可修复。

其中至少两个任务来自固定 commit 的真实小型公开论文仓库，其余可以是可控最小仓库。真实仓库选择标准是依赖简单、数据公开、CPU 可运行且几分钟内完成。

### 20.2 对照组

至少比较：

- `no_repair`：只安装和运行，不自愈。
- `agent`：启用受限自愈。

时间允许再增加 `rules_only`。

保持相同 commit、运行命令、资源限制和修复预算。记录模型名称、参数和调用成本。

### 20.3 报告指标

- 首次验证通过数量。
- 最终真实验证通过数量。
- 可修复任务中的成功数量。
- 不可修复任务是否正确停止。
- 修复次数、耗时和模型调用费用。
- 虚假成功数量，必须为 0。
- 容器清理失败数量，必须为 0。

样本较小时报告原始数量和失败案例，不宣称普遍适用或统计显著。

## 21. 里程碑

### 2026-11-13 至 2026-11-14：契约和部署探测

- 冻结契约、目录和 API 示例。
- 队长创建骨架和假实现，队员 review。
- 验证本地 WSL2 Docker。
- 启动云服务器，验证 Docker、公网 IP、域名和 HTTPS 路径。
- 确定第一个最小靶场。

通过条件：不用 LLM，可以创建容器、执行固定命令、保存日志并销毁容器。

### 2026-11-15 至 2026-11-17：本地 MVP

- 完成无自愈 Harness、FastAPI 和事件文件。
- 完成 Sandbox 和基础 ACI。
- 完成干净与缺依赖靶场。
- 前端先用假 JSON 开发页面。

通过条件：干净仓库直接成功，缺依赖仓库经预设动作修复后成功，生成 patch、环境和报告。

### 2026-11-18 至 2026-11-21：自愈核心

- 接入结构化 LLM。
- 支持缺依赖、版本冲突、路径写死和 CUDA 转 CPU。
- 完成预算、非法动作和重复动作处理。
- 扩充确定性测试。

通过条件：四类问题都有自动测试，所有成功都经重新运行验证。

### 2026-11-22 至 2026-11-24：公网 UI、云端部署和评测

- 前端接真实 API。
- 后端部署到云服务器。
- 前端部署到 Qoder Sites。
- 完成 8 个靶场和对照实验。
- 跑通公网真实浏览器链路。

2026-11-24 功能冻结，之后只修 bug、补证据和改进展示。

### 2026-11-25 至 2026-11-27：硬化和彩排

- 从干净环境按 README 重装。
- 预拉镜像、缓存靶场和依赖。
- 检查密钥、日志和容器清理。
- 断网或模型故障彩排。
- 录制真实演示备用视频。
- 至少完成两次完整答辩彩排。

### 2026-11-28 至 2026-11-30：提交

- 11 月 28 日完成作品帖子、PPT 和讲稿。
- 11 月 29 日完成最终视频、公开链接检查和过程截图。
- 11 月 30 日只处理提交问题，不增加核心功能。

## 22. 部署失败的后备方案

如果公网 HTTPS 链路在 11 月 15 日前仍未打通：

- 核心 Docker 复现继续按主线开发。
- Qoder Sites 先提供内置任务选择、真实历史事件回放、补丁和报告。
- 页面显著标注“历史运行回放”，不能伪装成实时云端执行。
- 同时保留本地真实演示和录屏。
- 继续修复公网部署，不能因为后备页面存在就停止处理硬性要求。

## 23. 全局 Definition of Done

作品完成必须同时满足：

- 公开 Qoder Sites 地址可直接打开。
- 评委能在 5 步内发起或选择一个复现任务。
- 至少一个真实任务展示完整失败、自愈和再验证过程。
- 至少 8 个固定靶场有可复核结果。
- 所有成功由真实命令和产物验证。
- 虚假成功为 0。
- 容器清理失败为 0。
- 前端没有永久“运行中”的僵尸状态。
- API 不泄露密钥、traceback 和宿主机路径。
- README 包含本地启动、云端配置、边界和故障排查。
- 作品帖子包含要求的五部分。
- 保留 Qoder 使用过程截图和人工修改说明。
- 演示视频和真实历史运行记录可作为故障备份。

## 24. 明确的设计决策

1. 采用确定性 Harness，不采用自由 Shell Agent。
2. Agent 只提出受限动作，最终成功由 Harness 判定。
3. 默认只要求仓库 URL，但不能可靠推断命令时允许要求补充。
4. 前端使用 Vue 3，部署到 Qoder Sites。
5. 后端使用 Python 和 FastAPI，部署到 2C4G Ubuntu 云服务器。
6. 云端一次只运行一个任务，使用有界内存队列。
7. 不引入数据库，运行证据写入文件系统。
8. 不使用 WebSocket，前端按事件序号轮询。
9. 不使用多 Agent、Kubernetes、Redis 或独立消息队列。
10. 云端部署是必须交付项，多 Agent 和强恶意代码隔离不是本次比赛主线。

## 25. 参考资料

- SWE-agent 官方仓库：https://github.com/SWE-agent/SWE-agent
- SWE-agent 论文：https://arxiv.org/abs/2405.15793
- mini-SWE-agent：https://github.com/SWE-agent/mini-swe-agent
- OpenHands 官方仓库：https://github.com/OpenHands/OpenHands
- OpenHands Software Agent SDK：https://github.com/OpenHands/software-agent-sdk
- Docker SDK for Python：https://docker-py.readthedocs.io/en/stable/containers.html
