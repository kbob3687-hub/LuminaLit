# PaperHarness 两人接口交接文档

- 日期：2026-11-13（2026-11-15 修订：补充沙箱可测试性约定与协作流程）
- 状态：生效基线，修改需两人 review
- 配套文档：`docs/superpowers/specs/2026-11-13-paperharness-design.md`
- 已冻结不在本文重复：`src/paperharness/contracts/run.py` 里的全部数据模型

本文只写死两人要互相交接的东西：沙箱接口、ACI 接口、沙箱安全测试清单、前端怎么用 API、靶场 manifest 格式、归属与 review 规则。

## 1. SandboxSession 接口约定

代码位置：`src/paperharness/sandbox/base.py`。实现位置：`src/paperharness/sandbox/docker_sandbox.py`（待写）。

### 1.1 创建方式

Harness 不 new 沙箱，只调用工厂：

```python
SandboxFactory = Callable[[RunRequest, Path], SandboxSession]
```

第二个参数是 `run_dir`，即 `runs/<run_id>/`，不是 workspace。约定：

- `workspace = run_dir / "workspace"`，容器内挂载为 `/workspace`。
- `logs = run_dir / "logs"`，所有完整输出写这里。
- 仓库 clone 由 Harness 在宿主机完成，沙箱不负责 clone，不做任何 git 网络操作。

### 1.2 方法语义

```python
class SandboxSession(Protocol):
    def execute(self, command: list[str], timeout_seconds: int) -> ExecResult: ...
    def set_network(self, enabled: bool) -> None: ...
    def set_environment(self, name: str, value: str) -> None: ...
    def export_diff(self) -> str: ...
    def close(self) -> None: ...
```

`execute`：

- `command` 是 argv 数组，禁止拼 shell 字符串；实现必须把 argv 原样传给容器 exec，不经过 `/bin/sh -c`。
- 工作目录固定 `/workspace`，以非 root 用户执行。
- 执行时把 `session.environment` 里全部键值对作为容器 env 传入（覆盖镜像默认值）；只影响本次及后续 execute。
- 完整 stdout/stderr 写到 `logs/exec-<序号>.out` 和 `.err`；序号从 1 开始三位零填充（`exec-001`、`exec-002`……）；`ExecResult.stdout_path`、`stderr_path` 填相对 run_dir 的路径，例如 `logs/exec-001.out`。
- 完整 stdout/stderr 不进 `ExecResult`、不进事件存储，只进日志文件；`stdout_tail`、`stderr_tail` 只保留最后 2000 字符。
- 超时由沙箱自己在 Harness 侧计时：到点 kill 整个容器，返回 `timed_out=True`、`exit_code=None`，session 进入 closed 状态；之后再调 execute 抛 `SandboxClosedError`。
- 正常结束 `exit_code` 取 exec 的真实退出码。

`set_network(enabled)`：连接或断开容器网络。创建时默认连接（安装阶段用），断网后容器内任何外联必须失败。写死三条：

- 断网是断开容器与网络的连接（docker 层面对容器生效），不允许只改 DNS 或只拦域名这种"软断网"。
- 幂等：重复设置成当前状态不报错、不产生副作用。
- 不暴露状态查询接口；调用方（ACI 的 `install_requirement`）自己记录调用前的状态并在结束后恢复，沙箱只保证单次调用的语义正确。

`set_environment(name, value)`：

- 只记录进沙箱内部的 dict，不校验白名单（白名单在 ACI 层做）；同名重复设置时覆盖旧值。
- 键值只对当前 session 存活期有效，不持久化到镜像、宿主机或后续 run；`close()` 后随容器消失。
- 防御性拒绝名字或值里含换行（抛 `ValueError`，属实现内部错误，不是 ACI 层的 `env_not_allowed`）。

`export_diff()`：在 `/workspace` 内以非 root 用户执行 `git add -A && git diff --cached`，返回 unified diff 字符串；没有改动返回空字符串。workspace 由 Harness clone 保证是 git 仓库，实现不初始化 git、不创建 commit。

`close()`：kill 并删除容器、删除临时网络；必须幂等，重复调用不报错；Harness 异常路径（包括 `SandboxUnavailableError`）也必须能调用且能完成清理。close 后按第 1.3 的命名和 label 过滤 `docker ps -a` 必须查不到该容器。

### 1.3 DockerSandbox 创建参数（写死）

- 镜像：`paperharness/base-py3.10` 或 `paperharness/base-py3.11`，演示前固定 digest。
- 用户：uid 1000，非 root。
- 内存 `2g`，memory-swap `2g`，`nano_cpus=1.5e9`，`pids_limit=128`。
- `cap_drop=ALL`，`security_opt=["no-new-privileges:true"]`。
- 只挂载 `run_dir/workspace` 到 `/workspace`，读写；禁止挂载任何其他宿主机路径。
- 不挂载 docker.sock，不用 privileged。
- 容器名 `paperharness-<run_id>`。同名冲突（上次残留）视为 `SandboxUnavailableError` 之前的实现责任：创建前先按 label 清掉同名残留容器。
- 容器必须带 label，安全测试靠它断言"真销毁、无残留"：
  - `paperharness.run_id=<run_id>`
  - `paperharness.role=sandbox`
- 若创建独立网络（推荐，便于整网拆除），网络名 `paperharness-net-<run_id>`，带同样两个 label；不用独立网络则必须在 close 时把容器从默认网络断开。
- 以上命名和 label 是写死契约，安全测试清单（第 3 节）直接引用，改名视同改接口，需两人 review。

### 1.4 错误语义

- Docker 引擎或容器生命周期失败：抛 `SandboxUnavailableError`，Harness 记 `failed`，`failure_category=service_interrupted`。
- 超时后 session 已死：抛 `SandboxClosedError`，Harness 记 `failed`，`failure_category=timeout`。
- 两个异常类已定义在 `sandbox/base.py`，不要另造。

## 2. ACI 接口约定

代码位置：`src/paperharness/aci/`（待写）。

```python
class AgentComputerInterface(Protocol):
    def execute(self, action: ToolAction) -> Observation: ...
```

实现类构造参数：`PaperACI(session: SandboxSession, workspace: Path, plan: RunPlan, timeout_seconds: int)`。

Agent 只能拿到 `Observation`，永远拿不到 `SandboxSession`。动作 schema 校验在 Harness 层用 Pydantic 完成，ACI 收到的一定是合法 `ToolAction`；ACI 仍对未知动作返回 `ok=False, summary="unknown_action"` 兜底。

### 2.1 公共路径规则

所有 `path` 参数：

- 必须是相对路径；拒绝绝对路径。
- 解析 `..` 和符号链接后仍必须落在 workspace 内。
- 违反返回 `ok=False, summary="path_escape:<原路径>"`，不产生任何副作用。

### 2.2 各动作规则

`read_file`：`start_line >= 1`，`line_count <= 200`。内容带行号放进 `Observation.stdout_tail`，`summary` 形如 `read 42 lines from src/main.py`。文件不存在返回 `ok=False, summary="file_not_found"`。

`search_text`：最多 50 条结果，每条 `路径:行号:内容`，内容截断 200 字符，放 `stdout_tail`。0 条命中是 `ok=True, summary="0 matches"`，不是错误。

`replace_exact`：`old` 在文件中必须恰好出现一次。0 次返回 `ok=False, summary="replace_not_found"`；多于 1 次返回 `ok=False, summary="replace_not_unique"`。成功返回 `ok=True, summary="replaced 1 occurrence in <path>"`。不做模糊匹配，不做正则。

`install_requirement`：先用 `packaging.Requirement` 解析；拒绝直接引用（含 `@`）、URL、换行、分号、`&&`。通过后临时 `set_network(True)`，执行 `["python", "-m", "pip", "install", requirement]`，结束后恢复调用前的网络状态。Observation 透传 ExecResult 的退出码和尾部。

`set_environment`：白名单只有 `CUDA_VISIBLE_DEVICES`、`OMP_NUM_THREADS`、`MKL_NUM_THREADS`、`TOKENIZERS_PARALLELISM`。其他名字返回 `ok=False, summary="env_not_allowed:<name>"`。通过则调 `session.set_environment`。

`run_smoke_test`：先 `set_network(False)`，再执行 `plan.command`，超时用构造时的 `timeout_seconds`。Observation 带真实 `exit_code` 和尾部。模型不能通过这个动作换命令。

`finish_repair`：`ok=True`，`summary` 透传模型给的 summary。只结束本轮决策，不代表成功。

## 3. 沙箱安全测试清单（队员负责）

测试文件：`tests/integration/test_docker_sandbox.py`。只加用例，不改 `docker_sandbox.py` 实现；实现问题开 issue 或 PR 评论给队长。

- [ ] 容器内进程用户不是 root。
- [ ] 容器内看不到 `/var/run/docker.sock`。
- [ ] 容器内读不到 workspace 之外的宿主机文件。
- [ ] ACI 拒绝 `../` 路径穿越。
- [ ] ACI 拒绝指向 workspace 外的符号链接。
- [ ] `set_network(False)` 后容器内 pip 或 curl 失败。
- [ ] execute 超时后容器被 kill，`timed_out=True`，再 execute 抛 `SandboxClosedError`。
- [ ] `close()` 后 `docker ps -a --filter "label=paperharness.run_id=<run_id>"` 为空；若按 1.3 建了独立网络，`docker network ls --filter "label=paperharness.run_id=<run_id>"` 也为空。
- [ ] 中途抛异常的路径（如 execute 超时、镜像不存在）也执行了清理，同样按 label 过滤查不到残留。
- [ ] 同名容器残留场景：上一次未正常 close 留下 `paperharness-<run_id>` 时，再次创建能成功或报 `SandboxUnavailableError`，不允许起第二个同名容器。
- [ ] 连续两次运行同一靶场，第二次看不到第一次的代码修改。
- [ ] `docker inspect` 确认内存、pids、CapDrop、no-new-privileges 配置生效。
- [ ] `install_requirement` 拒绝 `numpy; rm -rf /` 和 URL 形式。
- [ ] `set_environment` 拒绝 `PATH` 覆盖。

## 4. 前端与 API 交接

前端基地址来自 `VITE_API_BASE_URL`。不用 WebSocket，轮询间隔 1000 毫秒。

### 4.1 接口与示例

`GET /api/health` 返回 `{"status": "ok"}`。

`GET /api/targets` 返回数组，元素只含展示字段：

```json
{
  "id": "clean-example",
  "repo_url": "https://github.com/paperharness-demo/clean-example",
  "command": ["python", "main.py"],
  "expected_artifact": "out.txt"
}
```

`POST /api/runs`，最小请求体：

```json
{ "repo_url": "https://github.com/owner/repo" }
```

可选字段：`revision`、`command`（字符串数组）、`expected_artifact`、`python_version`、`timeout_seconds`、`max_repairs`。成功返回 202：

```json
{ "run_id": "8d34...", "state": "queued" }
```

队列满返回 429，body 为 `{"detail": {"code": "queue_full"}}`。输入不合法返回 422，body 为 FastAPI 默认 `{"detail": [ ... ]}`，前端取 `detail[0].msg` 展示。

`GET /api/runs/{run_id}` 返回：

```json
{
  "run_id": "8d34...",
  "state": "testing",
  "result": null
}
```

终态时 `result` 为完整 RunResult JSON。

`GET /api/runs/{run_id}/events?after=<seq>` 返回序号大于 `<seq>` 的事件数组，按序号升序。

`POST /api/runs/{run_id}/cancel` 返回 `{"run_id": "...", "state": "cancelled"}` 或当前状态；排队中的任务直接取消（worker 取出时跳过）；运行中的任务终止当前容器并在阶段边界收敛为 `cancelled`；重复取消返回当前终态，不产生第二个任务。

`GET /api/runs/{run_id}/report` 返回 `text/markdown`。

`GET /api/runs/{run_id}/patch` 返回 `text/plain`，没有改动时是空字符串，不是 404。

`GET /api/runs/{run_id}/environment` 返回 JSON 环境清单。

### 4.2 轮询与终态

终态集合：`succeeded`、`failed`、`cancelled`。进入终态后停止轮询。失败必须明确展示 `failure_category` 和最后错误尾部，不允许停留在“运行中”。

### 4.3 安全约定

前端不持有 API Key，不展示容器 ID、宿主机路径或 traceback。CORS 由后端限定为 Qoder Sites 域名和本地开发地址，前端不带 credentials。

## 5. 靶场 manifest 格式

目录：`targets/<id>/manifest.json`。由队长维护，作为自愈循环的测试夹具和评测集。

```json
{
  "id": "missing-dependency",
  "repo_url": "https://github.com/owner/repo",
  "revision": "0123456789abcdef0123456789abcdef01234567",
  "python_version": "3.10",
  "command": ["python", "main.py"],
  "expected_artifact": "out.txt",
  "failure_class": "missing_dependency",
  "repairable": true,
  "allowed_actions": ["install_requirement"],
  "expected_final": "succeeded",
  "notes": "requirements.txt 缺 PyYAML"
}
```

字段规则：

- `revision` 必须是 40 位完整 commit。
- `failure_class` 取值：`none`、`missing_dependency`、`version_conflict`、`hardcoded_path`、`cuda_only`、`deprecated_api`、`missing_private_data`、`requires_gpu`。
- `repairable=false` 时 `expected_final` 必须是 `failed`。
- `allowed_actions` 是评测时判定“修复方式是否越界”的依据。
- `GET /api/targets` 只暴露 `id`、`repo_url`、`command`、`expected_artifact`，不暴露答案字段。

## 6. 归属与 review 规则

- 沙箱实现、Harness、Agent、部署：队长。
- 沙箱安全测试（第 3 节清单）：队员，只加用例不改实现。
- ACI 实现及其单元测试：队员，队长 review。
- 前端：队员。
- 靶场与评测：队长。
- `contracts/run.py`、`sandbox/base.py`、本文档：任何修改必须两人 review。
- 跨目录改动必须由目录负责人 review；PR 超过 300 行先拆分。

### 6.1 双向 review（不是单向检查）

- ACI 实现及其单元测试：队员写，**队长 review**。
- 沙箱实现（`docker_sandbox.py`）：队长写，**队员 review**，review 依据就是第 3 节他自己写的安全测试清单——测试全绿不是通过标准，他要在 PR 里逐条说明每个用例断言了什么。
- 双方只对对方代码提 issue 和 PR 评论，不直接改对方目录的实现文件。
- README 的模块归属表保持署名到角色（队长/队员），随本文档同步更新。

### 6.2 开工第一天：沙箱 spike

开工第一天两人一起跑通最小真沙箱 spike，半小时内完成：创建 session → `execute` 一条命令 → `set_network(False)` 后容器内外联失败 → `close()` 后按 label 过滤无残留。spike 用一次性脚本跑，不进 `src/`。跑通后队员再开始写 ACI 和安全测试——他要亲眼看过沙箱怎么转，测试不是照抄清单。
