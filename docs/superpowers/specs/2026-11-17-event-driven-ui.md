# 事件驱动的前端：状态条、自愈回环、ACI 过程

- 日期：2026-11-17
- 范围：`frontend/`（React 应用 + 设计原型）
- 契约：`docs/interface-contract.md` §4（8 个接口）、§7（Observation）
- 边界：**没有改 `harness/`、`contracts/`、`sandbox/`、`targets/`**

## 0. 起因

三条反馈，指向同一个根因：**界面上的"进度"不是后端说的，是前端自己编的。**

1. 一进运行页，顶部阶段条已经有一排绿色；
2. 自愈回环只在"修复中"这个状态上有个 ×1 徽标，看不到它正在诊断什么、正在执行哪个动作；
3. ACI 校验层是答辩卖点，但界面上只有一句 `path_escape`，看不到"它到底怎么拦的"。

## 1. 状态条为什么一进场就绿

三个来源叠在一起：

| 来源 | 具体表现 |
|---|---|
| `useRunPolling` 初始 `status = null`，`RunScene` 把 `null` 兜底成 `queued` | 本身没错，但下面两条把它盖住了 |
| 演示模式**默认开启**，mock 状态写死 `repairing` | `stageIndex("repairing") = 6` → 前 6 格 `i < idx` 全部 `done` |
| `prototype.html` 的阶段数组把 `st:'done'` 写死在前 6 项 | 静态文件根本没接状态 |

再加上 `GET /api/runs/{id}` 只回 `state` 一个字段，前端拿不到"这个阶段有没有真的跑过"，
于是用"下标比当前状态小"来代替——这在自愈回环里是错的：`testing` 可能失败后又重跑，
`repairing` 之后还要回到 `testing`。

## 2. 改法：只认事件

新增 `src/runModel.ts`，全部界面状态从 `GET /api/runs/{id}/events` 的事件流推导：

```
buildStages(events, state)  → 每格 { status, count, durationMs }
buildLoop(events, state)    → 每轮 { failureClass, phase, actions[] } + doing
buildRows(events)           → 动作流（ACI 动作 + install/test 执行结果）
aciTrace(event)             → 一次动作的五步校验轨迹
```

判定规则（写进代码注释，避免下次又被"顺手优化"掉）：

- `done` 必须有事件：收到过该阶段事件、且已经离开它、且它最后一次执行 `exit_code == 0`；
- `bad`：最后一次执行 `exit_code != 0`，或 `failed` 终态落在这一格（按 `failure_category` 映射，
  映射不出来就退到最后有事件的阶段）；
- `active`：`state` 恰好是它且未终态；
- **唯一例外是 `queued`**：状态机里只有它不写事件，所以只允许它"没事件也算走过"；
- `×N` 是**状态切换次数**，不是事件条数（跑两遍测试才是 ×2）；
- 耗时取"本阶段首事件 → 下一阶段首事件"，进行中取到当前时间；
  取不到就显示空，不编估时。

演示模式的变化：

- 默认**关闭**（`?demo=1` 或 `VITE_DEMO=1` 才开），真实后端是唯一事实来源；
- 演示不再塞一个假 `status`，改用 `useDemoRun` 按时间轴一条条回放事件，
  从 `queued` 起步，走完整的两轮自愈——**前端的推导代码路径与真接口完全一致**；
- `?t=<ms>` 可快进到第 N 毫秒（截图/演示定位用）。

`prototype.html`（静态设计稿）同步把写死的 `st:'done'` 改成随仿真时间推进，
进场只点亮"排队"，hero 也不再预先写"修复中"。

## 3. 自愈回环：正在干什么

`LoopStrip`（轨道下方一条，只在真的收到过 `diagnosis` 事件时出现）：

```
⟳ 自愈第 1 / 3 轮   诊断 missing_dependency  安装阶段失败
正在做 install_requirement → install pysindy==2.1.0 exited with 0
本轮动作  [读取][检索][改码✕][装包✕][装包]   ← 点任意一个看 ACI 过程
```

- 轮次来自 `diagnosis.data.attempt`，预算取契约默认 `max_repairs=3`；
- 「正在做」在 `diagnosing` 时是"判定失败原因"，在 `repairing` 时是**最后一个动作 + 它的
  Observation**；修复后回到 `installing` / `testing` 时显示"第 N 轮修复后重新安装/重跑"；
- 点轮次徽标打开 `LoopPanel`：每轮的分类结论、阶段、时间、全部动作（✓ / ✕ / …），
  动作可继续点开 ACI 过程。

## 4. ACI 过程：点开看它怎么拦的

动作流每一行、右侧「ACI 审查」列表、回环条上的动作 chip，点开都是同一个抽屉：

1. **判定**：已拦截 / 已放行 + 标签（`path_escape` · 路径越界，标签表与 `aci/errors.py` 逐字一致）；
2. **动作目标**：路径 / 依赖串 / 环境变量；
3. **五步流水线**：动作分派 → 路径守卫 → 专属校验 → 执行副作用 → Observation，
   校验失败的那一步标红，后面的步骤标"未执行"（对应"拒绝时不产生副作用"）；
4. **Observation**：模型唯一能看到的那句话 + `exit_code`；
5. 脚注写明数据来源。

专属校验这一步的文案照后端实现写死（`read_file` 的 `1 ≤ line_count ≤ 200`、
`replace_exact` 的空 `old` 直接拒、`install_requirement` 禁 `; | & $` 与空白拼接、
`set_environment` 四个白名单变量、`run_smoke_test` 命令来自 plan）。

## 5. 诚实边界（答辩要能说清楚）

- 后端只在动作结束后回**一条** Observation，没有逐步骤埋点。所以"第几步失败"是前端按
  `summary` 的固定标签反推的（`path_escape` → 路径守卫，`file_not_found` /
  `replace_not_unique` / `requirement_not_allowed` / `env_not_allowed` / `invalid_argument`
  → 专属校验，`unknown_action` → 分派）。标签本身逐字来自后端，不是前端造的。
- 动作参数（`path` / `requirement` / `old`）没有单列，只能从 summary 的固定格式里读回来；
  读不出来就显示空，**不编一个像真的路径**。
  若要展示完整参数，需要 `harness/runner.py` 在动作事件里补一个 `request` 字段——
  那是队长的目录，本 PR 不动，已在 PR 评论里作为可选改进提出。

## 6. 验证

```bash
cd frontend && npm run build          # tsc -b + vite build 通过
# 演示回放（不需要后端）
open http://localhost:5173/?demo=1&scene=run           # 从 queued 起步，只亮排队
open http://localhost:5173/?demo=1&scene=run&t=11000   # 第 1 轮修复中
open http://localhost:5173/?demo=1&scene=run&t=22000   # 成功态
open http://localhost:5173/?demo=1&scene=run&t=11000&panel=aci&seq=7   # 拦截过程
```

截图对照（本机）：进场只有「排队」亮；`t=11000` 时装依赖红、修复中高亮；
`t=22000` 全绿 + 成功高亮；`panel=aci&seq=7` 打开路径越界的五步流水线。
