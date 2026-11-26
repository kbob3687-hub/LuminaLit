# 队员开工指南

- 日期：2026-11-15
- 读者：负责 ACI、沙箱安全测试、前端的队员
- 唯一契约：`docs/interface-contract.md`（本文只做导航和验收标准，规则冲突以契约为准）
- 冻结数据模型：`src/paperharness/contracts/run.py`，不要改

## 1. 你的定位

你负责系统的"可控"和"可见"：模型每个动作要过的校验门（ACI）、隔离卖点的证据链（安全测试）、评委看到的全部界面（前端）、答辩材料。

答辩叙事是你的："隔离和校验是我做的，界面也是我做的。"

## 2. 交付物与完成标准

| 交付物 | 位置 | 完成标准 |
|---|---|---|
| ACI 校验层 | `src/paperharness/aci/` | 契约 2.1/2.2 每条规则至少一个单元测试；全用 `FakeSandboxSession`，不依赖 Docker |
| 沙箱安全测试 | `tests/integration/test_docker_sandbox.py` | 契约第 3 节 14 条清单每条一个可运行用例；PR 里逐条说明断言了什么 |
| 前端 | 独立前端仓库/目录 | 契约第 4 节 8 个 API 全接、1000ms 轮询、终态停轮询、失败必须展示 `failure_category` |
| 演示材料 | 仓库外或 `docs/` 之外 | 截图、PPT 素材、演示脚本 |

## 3. 环境搭建（第一天）

```
git pull
pip install -e ".[dev,docker]"
docker build -t paperharness/base-py3.10 -f docker/base-py3.10.Dockerfile docker/
python scripts/spike_sandbox.py
```

spike 九项全 PASS 说明环境正确。这个脚本就是你以后审沙箱的方式：创建、执行、断网、销毁、按 label 查残留。

## 4. ACI 怎么写

- 入口：实现 `AgentComputerInterface` 协议，构造参数 `PaperACI(session, workspace, plan, timeout_seconds)`（契约第 2 节）。
- Agent 永远拿不到 `SandboxSession`，只能拿 `Observation`；这是不可破坏的边界。
- 收到的一定是合法 `ToolAction`（Harness 层 Pydantic 已校验），但对未知 action 仍返回 `ok=False, summary="unknown_action"` 兜底。
- 每条校验规则的失败 `summary` 是写死的（`path_escape:<原路径>`、`replace_not_unique`、`env_not_allowed:<name>` 等），前端和评测靠这些字符串，别改措辞。
- `install_requirement` 的临时开网/恢复由你实现：记录调用前网络状态，结束后恢复——沙箱不替你记。
- 单元测试用 `sandbox/fake.py` 的 `FakeSandboxSession`，不需要 Docker。

## 5. 安全测试怎么写

- 契约第 3 节 14 条清单，一条一个用例，全绿不是通过标准——你审的是实现，不是跑测试。
- 残留断言用契约 1.3 写死的 label：`docker ps -a --filter "label=paperharness.run_id=<run_id>"`。
- 发现实现问题：PR 评论或 issue 给队长，不直接改 `docker_sandbox.py`。

## 6. 前端怎么接

- 基地址 `VITE_API_BASE_URL`，轮询 1000ms，终态集合 `succeeded/failed/cancelled`。
- 8 个接口的请求响应示例在契约 4.1，逐个对着接。
- 不展示容器 ID、宿主机路径、traceback；不持 API Key。

## 7. 流程规则

- 分支自由推，`main` 必须走 PR；PR 一律 base main。
- 双向 review：你的 ACI 队长审；队长的沙箱实现你拿着第 3 节清单审。
- 只在 PR/issue 上提意见，不直接改对方负责的文件。
- 你的目录：`src/paperharness/aci/`、`tests/integration/test_docker_sandbox.py`、前端。不要动：`sandbox/docker_sandbox.py`、`harness/`、`contracts/`、`targets/`。
- 改 `docs/interface-contract.md` 或 `contracts/run.py` 必须两人 review。

## 8. 卡住了找谁

- 契约没写的、写错的、互相矛盾的：直接问队长，当场改契约再继续。
- 需要沙箱行为变化的（比如想加个查询接口）：提 issue，队长评估，不加就不加——ACI 靠自己记录状态解决。
