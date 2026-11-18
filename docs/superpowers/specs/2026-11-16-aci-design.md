# ACI 校验层设计（PaperACI）

- 日期：2026-11-16
- 状态：待队长评审（设计稿，代码未开始）
- 负责人：队员（实现 + 单元测试），队长 review
- 依据：`docs/interface-contract.md` 第 2 节
- 本文性质：实现前设计，不代表功能已经实现；与契约冲突时以契约为准

> 按「先文档后代码」的约定先提这一版，队长 review 通过后再动 `src/paperharness/aci/`。本文只补契约没写死的实现细节，新增内容一律标「提案」。

---

## 1. 为什么需要这一层

自愈循环里，模型每一轮都会产出一个动作：读哪个文件、替换哪段字符串、装哪个包、设哪个环境变量。模型是**不可信输入源**——它可能被仓库里的恶意注释诱导，也可能只是幻觉，写出 `../../etc/passwd` 或 `numpy; rm -rf /`。

ACI 是这套系统里**唯一收口**：所有动作必须经过它，才可能触达沙箱。它的职责不是「判断修复对不对」（那是 Harness 的验证阶段），而是**判断这个动作合不合法**。

一句话定位：**把模型无限多种可能的动作，收窄成一个可枚举的合法集合。**

## 2. 边界与职责

| 项 | 约定 |
|---|---|
| 输入 | `ToolAction`（已由 Harness 用 Pydantic 校验过）、`SandboxSession`、`workspace: Path`（宿主机侧 `run_dir/workspace`）、`RunPlan`、`timeout_seconds` |
| 输出 | `Observation`——模型**只能**看到它，永远拿不到 `SandboxSession` |
| 做 | 路径合法性、依赖字符串合法性、环境变量白名单、替换唯一匹配、网络状态开合 |
| 不做 | 不做模糊匹配、不做正则替换、不判断修复正确性、不重试、不写事件（事件由 Harness 记） |

`workspace` 是宿主机路径，同一个目录以 `/workspace` 挂进容器（`docker_sandbox.py` 的 `WORKSPACE_MOUNT`）。因此 `read_file` / `replace_exact` **可以由 ACI 直接在宿主机文件系统上完成**，不必绕道容器——代价是路径校验必须做扎实，这是本层最核心的一段代码。

## 3. 模块划分

```
src/paperharness/aci/
  __init__.py          # 导出 PaperACI
  base.py              # AgentComputerInterface Protocol
  paper_aci.py         # PaperACI：动作分发 + 公共前置校验
  path_guard.py        # 路径解析与 workspace 边界判定
  requirement_guard.py # install_requirement 的字符串校验
  errors.py            # summary 常量（逐字，禁止改写）
```

`errors.py` 单独存在的理由：`summary` 是前端和评测共同依赖的机器可读信号。散落在各处写字符串，迟早有人改一个字，前端就静默失灵。

## 4. 执行流水线

```
execute(action)
  ├─ 1. 动作分派：不认识的 action → ok=False, summary="unknown_action"（兜底，正常不会走到）
  ├─ 2. 公共前置：路径类动作先过 path_guard，失败即返回，不产生任何副作用
  ├─ 3. 动作专属校验：依赖字符串 / 环境变量名
  ├─ 4. 执行副作用：读文件、原子写文件、set_network + execute
  └─ 5. 组装 Observation（截断尾部、带上 exit_code / timed_out）
```

第 2 步的关键是**顺序**：先验后做。任何校验失败都必须在产生副作用之前返回，否则「用一次非法写入换一次合法读」就成了绕过通道。

## 5. 逐动作规则

`summary` 必须逐字一致，测试里用等值断言而不是 `in`。

### 5.1 `read_file`

| 项 | 值 |
|---|---|
| 前置 | `start_line >= 1`、`line_count <= 200`（Pydantic 已挡一层，ACI 兜底） |
| 路径 | 过 `path_guard` |
| 成功 | `ok=True`，`stdout_tail` 放带行号的内容，`summary="read 42 lines from src/main.py"` |
| 文件不存在 | `ok=False, summary="file_not_found"` |
| 路径越界 | `ok=False, summary="path_escape:<原路径>"` |

行号格式提案为 `42: 内容`（行号 + 冒号 + 空格 + 原文），与 `search_text` 的 `路径:行号:内容` 保持同一套心智。

### 5.2 `search_text`

| 项 | 值 |
|---|---|
| 上限 | 最多 50 条（`max_results` 已 `le=50`） |
| 成功 | 每条 `路径:行号:内容`，内容截断 200 字符，放 `stdout_tail` |
| 0 条命中 | `ok=True, summary="0 matches"`——**不是错误** |
| 有命中 | `ok=True, summary="<n> matches"`（提案：契约未规定措辞，仅约定 0 条那条） |

0 条命中必须返回 `ok=True`：模型据此得知「这个文件里没有我要改的东西」，而不是「我的动作被拒了」。混淆这两件事会让自愈循环走进错误分支。

### 5.3 `replace_exact`

| 项 | 值 |
|---|---|
| 规则 | `old` 在文件中必须**恰好出现一次**（按字节/子串计数，不是按行） |
| 0 次 | `ok=False, summary="replace_not_found"` |
| 多次 | `ok=False, summary="replace_not_unique"` |
| 成功 | `ok=True, summary="replaced 1 occurrence in <path>"` |
| 写入 | **原子写**：同目录临时文件 + `os.replace`，避免半写留下坏文件 |
| 换行 | 按文本读、按原样写，不做换行符归一化（否则 diff 会整文件飘红） |

为什么必须唯一：`replace_exact` 是模型改代码的唯一手段。允许「改第一处」，模型就可能改到同名函数的另一处，自愈变成制造新 bug。唯一匹配是把「改动范围」钉死成一个可核对的断言。

提案（待确认）：`old` 为空字符串时直接返回 `replace_not_unique`（空串在任何文件里都出现无数次），不做特殊处理。

### 5.4 `install_requirement`

校验顺序：

1. `packaging.Requirement(req)` 能否解析——解析不了的直接拒。
2. 拒绝**直接引用**（含 `@`，如 `pkg @ https://...`）。
3. 拒绝 URL、换行、分号、`&&`。
4. 通过后：**ACI 自己记录当前网络状态** → `set_network(True)` → `execute(["python", "-m", "pip", "install", req], timeout)` → **`finally` 恢复到 ACI 记录的状态**。
5. `Observation` 透传 `ExecResult` 的 `exit_code` 与尾部。

第 4 步的 `finally` 是硬要求：装包中途抛异常（超时、容器已关）也必须把网络关回去，否则后面所有动作都在联网状态下跑，「断网」这条隔离承诺当场失效。

**网络状态必须由 ACI 自己维护，不能向会话查询**（2026-11-17 修订，队长 review 指出）：

契约 §1.2 写死沙箱不暴露状态查询接口，调用方自己记录。真沙箱 `DockerSandboxSession`
只有私有 `_network_enabled`，所以任何 `getattr(session, "network_enabled", True)` 都会
拿到默认值 `True`——模型装一次包，本该断开的网就被永久打开，隔离当场失效。

**修复期基线约定为断网**：自愈发生在 `runner._run_test()` 的 `set_network(False)` 之后，
所以 `install_requirement` 结束必须回到 `False`。控制流上安全：`runner._run_install`
自己会 `set_network(True)`、`_run_test` 自己会断网，都不依赖 ACI 留网。

替身 `FakeSandboxSession` 已同步为只有私有 `_network_enabled` + 仅测试用的
`network_state` 只读属性，杜绝「替身有公开属性、真实现没有」造成的单测假绿。

提案（待确认）：这里的超时用 `max(timeout_seconds, 120)`——pip 装 scipy/numba 这类包通常比跑一次 smoke test 慢，沿用同一个 300 秒可能不够。

### 5.5 `set_environment`

白名单**只有四个**：

| 变量 | 用途 |
|---|---|
| `CUDA_VISIBLE_DEVICES` | 无 GPU 机器上强制 CPU（演示 `cuda_only` 靶场的修复动作） |
| `OMP_NUM_THREADS` | 限制 BLAS 线程数 |
| `MKL_NUM_THREADS` | 同上，MKL 分支 |
| `TOKENIZERS_PARALLELISM` | 关掉 tokenizer 并行告警 |

其他名字 → `ok=False, summary="env_not_allowed:<name>"`。

为什么必须拒 `PATH`：改 `PATH` 等于替换命令解析路径，`python` 可以被指向任意脚本——一个环境变量就能绕过整层校验。同理 `PYTHONPATH`、`LD_PRELOAD` 也不在白名单里，默认拒绝而不是默认放行。

### 5.6 `run_smoke_test`

- 先 `set_network(False)`，再 `execute(plan.command, timeout_seconds)`。
- 命令来自 `plan`，**模型无法通过这个动作换命令**。
- `Observation` 带真实 `exit_code` 和尾部。超时时 `timed_out=True`。

这条是「验证由真实命令产物说了算」的落点：模型能改代码、能装包、能设环境变量，但它不能自己宣布「跑通了」。

### 5.7 `finish_repair`

`ok=True`，`summary` 透传模型给的 summary。**只结束本轮决策，不代表成功**——成功与否由 Harness 的验证阶段判。

## 6. 路径校验算法（path_guard）

```
def guard(raw: str) -> Path:            # 抛 PathEscape 或返回安全的绝对路径
    if raw 是绝对路径（含 Windows 盘符、UNC、/ 开头）: 拒绝
    if raw 含 NUL 或为空: 拒绝
    target = (workspace / raw).resolve(strict=False)   # 展开 .. 与符号链接
    if not target.is_relative_to(workspace_resolved): 拒绝
    return target
```

细节与坑：

- `workspace` 本身先 `resolve()` 一次并缓存，否则符号链接会让边界判断漂移。
- 用 `resolve(strict=False)` 是因为目标文件可能还不存在（写新文件），但**符号链接仍会被展开**——指向 workspace 外的链接因此被拦下。
- Windows 下路径大小写不敏感：比较前统一 `os.path.normcase`，否则 `C:\Run\WS` 与 `c:\run\ws` 会被判成两个目录。
- 拒绝绝对路径要在 `resolve` **之前**做：`C:\Windows\...` 在 `workspace / raw` 里会被拼接成奇怪路径，先拦更清晰。
- 失败时的 `summary` 用**模型给的原始字符串**，不是解析后的路径——前端和事件日志要能对上模型当时的输入。

## 7. Observation 组装约定

- `stdout_tail` 单次不超过 4000 字符，超了保留尾部（模型最需要看的是报错最后几行）。
- `read_file` 与 `search_text` 的内容放 `stdout_tail`，不放 `stderr_tail`——`stderr_tail` 保留给真实命令的报错，两者混用会让前端「错误尾部」面板出现文件内容。
- `log_path`：ACI 自己产生的操作不写日志文件，沿用 `ExecResult` 的 `stdout_path`；文件类动作留空。

## 8. 与前端 `failure_category` 的关系

前端失败态要展示 `failure_category`（契约 4.2）。ACI 的 `summary` 是它的上游：模型连续收到 `path_escape` / `replace_not_unique`，最终仍没修好，Harness 分类出的 category 就该能和这些信号对上。

因此 `summary` 的取值集合要**稳定、可枚举**，前端才能做映射表。

**当前共 8 种**（2026-11-17 修订：原口径写"固定 6 个"，漏申报了第 7、8 两种）：

| # | 标签 | 形态 | 触发 |
|---|---|---|---|
| 1 | `file_not_found` | 常量 | 文件不存在 / 目录 / 二进制 / 超大 |
| 2 | `replace_not_found` | 常量 | `old` 在文件中出现 0 次 |
| 3 | `replace_not_unique` | 常量 | `old` 出现多次，或 `old` 为空串 |
| 4 | `unknown_action` | 常量 | 动作不在枚举内 |
| 5 | `requirement_not_allowed` | 常量 | 依赖字符串非法（含 `@`/URL/换行/分号/`&&`） |
| 6 | `path_escape:<原路径>` | 模板 | 路径越界 |
| 7 | `env_not_allowed:<name>` | 模板 | 环境变量不在白名单 |
| 8 | `invalid_argument:<field>` | 模板 | `start_line < 1` 或 `line_count` 越界 |

> 第 5 种由队长在 2026-11-17 review 时批准为独立常量（复用 `path_escape` 会误导前端）。
> 第 8 种此前一直在用（`test_06` 断言它），但没有申报，属于遗漏；现补齐并加
> `tests/unit/test_aci_error_tags.py` 钉死清单，任何新增/改名都会失败。

前端映射表必须覆盖全部 8 种，否则失败态会显示不出原因（契约 §4.2）。

## 9. 单元测试计划

全部使用 `FakeSandboxSession`，不依赖 Docker。每条规则至少一个用例，覆盖「拒绝」与「放行」两侧。

| # | 用例 | 断言 |
|---|---|---|
| 1 | `read_file` 正常 | `ok=True`，`stdout_tail` 行号从 `start_line` 起，`summary` 逐字 |
| 2 | `read_file` 不存在 | `ok=False`，`summary=="file_not_found"` |
| 3 | `read_file` `../` 越界 | `summary=="path_escape:../secret.txt"`，原样回显 |
| 4 | 绝对路径 | 拒绝，`summary` 以 `path_escape:` 开头 |
| 5 | 指向 workspace 外的符号链接 | 拒绝（`resolve` 后越界） |
| 6 | `start_line=0` / `line_count=201` | 拒绝，且**未产生任何读副作用** |
| 7 | `search_text` 0 命中 | `ok=True, summary=="0 matches"` |
| 8 | `search_text` 超 50 条 | 只返回 50 条，每条 ≤200 字符 |
| 9 | `replace_exact` 唯一命中 | `ok=True`，文件内容真的改了，`summary` 逐字 |
| 10 | `replace_exact` 0 命中 | `summary=="replace_not_found"`，文件未变 |
| 11 | `replace_exact` 2 处命中 | `summary=="replace_not_unique"`，文件未变 |
| 12 | `replace_exact` 越界路径 | 拒绝且文件未变 |
| 13 | `install_requirement` 合法 | 调用序列为 `set_network(True)` → `pip install` → 恢复 |
| 14 | `install_requirement` 合法性检查 | `numpy; rm -rf /`、`pkg @ https://...`、含换行 → 拒绝，且**未开网** |
| 15 | `install_requirement` 执行中抛异常 | 网络状态仍被恢复（`finally`） |
| 16 | `set_environment` 白名单内 | 透传到 `session.environment` |
| 17 | `set_environment` 拒 `PATH` | `summary=="env_not_allowed:PATH"` |
| 18 | `run_smoke_test` | 先 `set_network(False)`，命令等于 `plan.command` |
| 19 | `run_smoke_test` 超时 | `timed_out=True` 透传 |
| 20 | `finish_repair` | `ok=True`，`summary` 等于模型给的文本 |
| 21 | 未知动作 | `ok=False, summary=="unknown_action"` |

第 6、12、14 条专门断言「拒绝时无副作用」——这是最容易被写漏、也最容易被评委追问的一条。

## 10. 待队长确认（review 时逐条过）

1. `search_text` 有命中时 `summary` 的措辞（契约只规定了 `0 matches`）。
2. `read_file` 的行号格式用 `42: 内容` 是否可以。
3. 目标是目录时返回 `file_not_found` 还是新增 `not_a_file`（新增会动前端映射表，倾向复用 `file_not_found`）。
4. `install_requirement` 的超时是否单独放宽（建议 `max(timeout_seconds, 120)`）。
5. 二进制文件 `read_file` 的行为：直接返回 `file_not_found`，还是解码失败时报错（倾向前者，模型不该读二进制）。
6. `replace_exact` 的 `old` 为空串如何处理（建议按 `replace_not_unique`）。

## 11. 明确不做

- 不做语法级代码分析（AST 校验）——两天做不完，且和「唯一匹配」已能覆盖大部分误改。
- 不做命令白名单——`run_smoke_test` 的命令来自 `plan`，模型本来就换不了。
- 不改 `sandbox/`、`harness/`、`contracts/`。发现沙箱实现问题只开 issue / PR 评论。
