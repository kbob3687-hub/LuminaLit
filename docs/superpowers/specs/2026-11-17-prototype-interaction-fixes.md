# 原型交互问题与修复记录（v3）

- 日期：2026-11-17
- 状态：**已修复并验证**（提交 `061ccaa`）
- 对象：`frontend/prototype.html`（单文件交互原型，mock 驱动）
- 来源：嘉豪在演示走查中提出的三个问题

> 这份文档记录「发现的问题 → 根因 → 修法 → 怎么证明修好了」。
> 目的是让 review 不必依赖聊天记录，也能核对每一处改动是否真的解决了问题。

---

## 问题 1：产物是死标签，点不动

### 现象

阶段详情页右栏「产出」面板里，`out.txt` / `report.md` / `export tar` 等显示为带绿点的 chip，
鼠标移上去有 hover 效果（`cursor:pointer`），但点击没有任何反应。

### 根因

chips 是纯展示的 `<span>`，模板里没有任何事件绑定：

```js
<span class="chip"><span class="cdot"></span>${a[0]} · ${a[1]}</span>
```

`cursor:pointer` 是从通用 `.chip` 样式继承来的，视觉上「看起来能点」，
实际没有 handler——这是最坏的一种情况：给了交互暗示但不兑现。

### 修法

1. 视图层新增 `#view-artifact`（全屏产物查看器），与主控台 / 阶段详情 / 沙箱实时并列。
2. chip 加 `data-art` 序号 + hover 出现 `↗`，点击调 `openArtifact(stageId, idx)`。
3. 产物内容按 `artifact[1]`（产物名）查表渲染：
   - 代码类 → `.av-code`，逐行加行号（`out.txt` / `stderr tail` / `patch.diff` / `events.jsonl` / `environment.json` / 执行计划 / 诊断结论 / export tar 清单）
   - `report.md` → `.av-report`，排成报告：结论 / 环境 / 自愈过程 / 产物 四张表
4. **适应屏幕**：页头 `flex:0 0 auto` 固定，内容区 `flex:1 + overflow:auto` 填满剩余视口，
   只滚内容不滚页面；投屏 1440×900 下无裁切、无横向溢出。
5. 查表未命中的产物不编造内容：渲染「该产物由 XXX 阶段产生 + 说明 + 接入约定表」，
   明确写 `GET /api/runs/{id}/artifacts/{name}`，避免把演示数据当成真实产物。

### 为什么不做「弹窗」

弹层（`.overlay`）复用现有 modal 样式只适合短内容；产物是长文本 + 表格 + 需要滚动定位，
全屏视图才能同时容纳「页头元信息 + 可滚动正文」，也更接近最终 React 路由的形态。

---

## 问题 2：进度指示器太素，没有技术感

### 现象

顶栏进度是 30px 的 SVG 细环（`stroke-width:3`）+ 两行文字，视觉上是一张「静态截图」，
和队长要求的「技术感、炫酷一点」不匹配。

### 根因

v1 用 `stroke-dasharray` 单环表达进度，只有二维信息（弧长），没有层次、没有光源、
没有材质差异，所以无论怎么调颜色都像仪表盘控件而不是「运行中的机器」。

### 修法

改成 3D 立体盘，靠 `transform-style:preserve-3d` + `translateZ` 分层堆出厚度：

| 层 | translateZ | 作用 |
|---|---|---|
| `.g3d-side` | −8px | 柱身，露出盘体厚度（两层 box-shadow 造侧壁） |
| `.g3d-base` | 0 | 盘面底，带内阴影 |
| `.g3d-arc` | 3px | `conic-gradient` 进度弧（`--p` 变量驱动角度）+ 发光 |
| `.g3d-ticks` | 3.5px | 外圈刻度（`repeating-conic-gradient` 遮罩） |
| `.g3d-rim` | 4px | 边缘高光/暗边，造出金属倒角 |
| `.g3d-boss` | 10px | 凸起中枢，百分比嵌在球面里 |
| `.g3d-spec` | 11px | 镜面高光，斜向线性渐变 |

外层 `perspective:95px`，盘面 `rotateX(20deg) rotateZ(-10deg)`。
hover 时 `rotateX` 收到 8°、`scale(1.07)`——像抬头看表，强化立体感。
底部 `.g3d-shadow` 是一层模糊椭圆投影，把盘「按」在顶栏上。

进度值仍由 `updateProgress()` 单点驱动，同时写 `--p`、进度条宽度和三个文本节点，
不存在两套状态。

---

## 问题 3：返回上一层只能点左上角面包屑

### 现象

进到阶段详情或沙箱实时后，唯一的退路是顶部的 `主控台 / 阶段 08 · 成功` 面包屑里那个小字链接；
按 Esc 无效，浏览器后退键会把整个页面退出去（因为是单页 + 无历史记录）。

### 根因

`showView()` 只切换 `.view` 的 `on` 类，完全没有接入 History API：

```js
function showView(name){
  curView=name;
  $$('.view').forEach(v=>v.classList.remove('on'));
  $('#view-'+name).classList.add('on');
}
```

所以「层」在视觉上存在，在浏览器和键盘语义上不存在。

### 修法

1. `showView()` 改为 `applyNav(state)` + `history.pushState()` / `replaceState()` 两条路径。
2. `popstate` 监听 → `applyNav(e.state)`，浏览器后退键直接可用。
3. `Esc` 逐级返回：弹层打开时先关弹层，否则 `history.back()`。
4. 左下角常驻悬浮药丸「← 返回 `Esc`」，只在 `curView !== 'dash'` 时出现。
5. hash 深链与历史统一：`#run` / `#stage/<id>` / `#artifact/<stage>/<idx>` / `#sandbox`。

### 修的过程中发现的一个连带 bug

第一版把「返回阶段」按钮实现成 `showView('stage')`，等于**又 push 了一条历史**：

```
栈：[dash] → [stage] → [artifact] → [stage]   ← 点返回后
```

结果按 Esc 会回到产物页，而不是主控台。修法两条：

- 产物页返回用 `history.back()`（弹出，而不是新增）；
- 阶段之间切换（上一/下一阶段）用 `replaceState`，不在同一层里堆栈。

修完栈最坏情况是 `[dash] → [stage] → [artifact]`，Esc 两次稳定回到主控台。

---

## 验证

不只截图看，用无头浏览器（Edge `--headless --dump-dom`）注入断言脚本跑真实点击：

```
stageOn=true | chips=3 | artOn=true | file=report.md | report=4tables
back=true | esc1=stage | esc2=dash | pill=false
```

对应关系：

| 断言 | 证明 |
|---|---|
| `chips=3` | 成功阶段三个产物都渲染成可点 chip |
| `artOn=true` + `file=report.md` + `report=4tables` | 点第 2 个 chip 正确进入查看器并渲染报告 |
| `back=true` | 「返回阶段」回到阶段详情，且未新增历史 |
| `esc1=stage` / `esc2=dash` | Esc 逐级退回，不跳级、不卡在产物页 |
| `pill=false` | 回到主控台后悬浮返回按钮自动隐藏 |

另有 `node --check` 校验内联脚本语法（单文件里一处语法错会杀掉整块 JS，
之前踩过一次数据块括号错误的坑，所以每次改完必跑）。

---

## 未做的事（有意留下）

- **没有同步到 React（`frontend/src/`）**。这三处交互会重写 `RunScene` 的视图层与路由，
  在视觉方向被队长确认前改两遍不划算。定稿后一次性落，`api.ts` / `useRunPolling.ts` 不受影响。
- **没有做产物搜索/下载**。真实产物可能几十 MB，前端不该拉全量；
  下载应走 `GET /api/runs/{id}/artifacts/{name}` 后由浏览器保存，属于接后端时的事。
- **没有做产物的空态/错误态**。等接口形状确定后与 `failure_category` 一起补。
