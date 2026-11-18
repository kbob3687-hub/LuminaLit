# 组件库参考目录

- 日期：2026-11-17
- 状态：**待评审**
- 配套：根目录 `DESIGN.md`（设计参数）、`docs/superpowers/specs/2026-11-17-frontend-design-rules.md`（规则来源）

> 这份目录回答一个问题：**哪些现成组件能用，哪些不能用，为什么。**
>
> 判断依据不是“好不好看”，而是“用了之后会不会变成 AI 模板”。
> 每条都标注了「是否采用」和「采用的部分」，不写“可以考虑”这种含糊话。

---

## 1. 总原则

三条，按优先级排序：

1. **能用原生与 Tailwind 写的，不引库。** 三天窗口，每多一个依赖就多一处调试。
2. **引库只引“结构”，不引“皮肤”。** 组件的布局逻辑可用，配色、圆角、阴影全部按 `DESIGN.md` 重写。
3. **任何带来“光效/玻璃/渐变”的组件，一律不引。** 这是 `DESIGN.md` §7.1 的禁令，没有例外。

### 依赖预算

前端总依赖上限：**6 个运行时依赖**（不含 devDependencies）。

| 已定 | 用途 |
|---|---|
| `react` + `react-dom` | 基础 |
| `typescript` | 语言 |
| `tailwindcss` | 样式（不外引 UI 框架） |
| `clsx` | 类名拼接（可选，也可手写） |

**剩余 2 个名额**留给下面判定为「必用」的库。**不引入完整 UI 框架**（不用 shadcn/ui 全量、不用 Ant Design、不用 MUI）。

**为什么不用 UI 框架**：这是最直接的“AI 味”来源。Ant Design 一看就是后台模板；shadcn 默认样式被大量 AI 工具使用，同样有辨识度。我们的设计是“纸与墨 + 朱批”，任何框架的默认皮肤都会稀释它。

---

## 2. 已评估的库

### 2.1 React Bits

- 地址：[reactbits.dev](https://www.reactbits.dev/)，[GitHub](https://github.com/DavidHDev/react-bits)
- 协议：MIT + Commons Clause（可商用，不可转售组件本身）
- 形态：**复制源码进项目**，不是 npm 依赖。提供 JS-CSS / JS-TW / TS-CSS / TS-TW 四种版本
- 内容：Text Animations、Animations、Components、Backgrounds、Micro Interactions

| 组件 | 是否采用 | 理由 |
|---|---|---|
| `AnimatedList` | **采用** | 日志流的新行进入动画。只取它的进入动画逻辑，不用默认样式 |
| `CountUp` | **采用** | 耗时、尝试次数、文件行数的数字滚动。展示“跑了 47 秒”比静态数字有说服力 |
| `SplitText` | 不采用 | 逐字拆分动画，中文场景断字难看，且演示时每次刷新重播 |
| `DecryptedText` | 不采用 | 解密式乱码，属于典型的“演示用炫技”，与工具定位冲突 |
| `BlurText` | 不采用 | 同上，装饰性 |
| `Backgrounds`（粒子/光斑/网格等） | **一律不采用** | `DESIGN.md` §7.1 明确禁止装饰性背景 |

**净采用：2 个组件的逻辑，0 行默认样式。**

> 复制进项目后必须做的三件事：删掉所有背景与阴影样式、把缓动改成 `ease-out-quart`、把高亮色换成语义色。

### 2.2 Aceternity UI

- 地址：[ui.aceternity.com](https://ui.aceternity.com/)
- 协议：组件级复制，基于 Tailwind + Framer Motion
- 内容：背景光束、3D 卡片、聚光跟随、粒子、文字效果

| 组件 | 是否采用 | 理由 |
|---|---|---|
| 全部 | **不采用** | 它的核心卖点（光斑、光束、3D 倾斜、聚光跟随）**正是 `DESIGN.md` §7.1 的禁令对象** |

**净采用：0。**

**为什么明确否决**：Aceternity 的设计语言是“戏剧性视觉”，适合落地页与营销页。我们是工具型产品（register = product），用它等于把产品的信息密度让位给装饰。而且它是当前 AI 生成 UI 里出现频率最高的库之一，用了必然被识别为“AI 做的”。

### 2.3 Uiverse

- 地址：[uiverse.io](https://uiverse.io/)
- 协议：开源 CSS 元素集合，用户上传
- 形态：纯 HTML + CSS 片段，不依赖任何框架

| 用途 | 是否采用 | 理由 |
|---|---|---|
| 加载态（沙箱启动时的占位） | **可参考** | 只需一个简单的脉动块，Uiverse 上有大量零依赖实现 |
| 按钮 | 不采用 | 默认样式过于花哨（渐变、光晕、3D 按压），与“纸墨”冲突 |
| 开关 / 复选框 | 不采用 | 我们只有少量开关，原生 + Tailwind 足够 |
| 卡片 / 加载动画 / 文字效果 | 不采用 | 装饰性 |

**净采用：最多 1 个加载态实现，且需重写配色。**

### 2.4 awesome-design-md

- 地址：[VoltAgent/awesome-design-md](https://github.com/VoltAgent/awesome-design-md)（84 个网站），[中文版](https://github.com/bayshier/awesome-design-md-cn)
- 协议：MIT
- 形态：**不是组件库**，是 84 份 `DESIGN.md` 设计系统文档

| 用途 | 是否采用 | 理由 |
|---|---|---|
| 直接复制某份 `DESIGN.md` | **不采用** | 84 个网站里没有一个匹配“纸与墨 + 朱批”，照抄会变成“又一个抄袭的开发者工具” |
| **抄它的 9 章结构** | **采用** | 结构是方法论，不是风格。我们的 `DESIGN.md` 已按此结构写 |
| 章节内的检查项 | **采用** | 特别是第 7 章「Do's and Don'ts」和第 9 章「Agent Prompt Guide」，这两章直接提升 AI 生成的一致性 |

**其中值得单独看的 3 份**（只看结构取舍，不抄配色）：

| 网站 | 值得借鉴的点 |
|---|---|
| **Warp** | “块状命令 UI”：把每条命令及其输出包成一个视觉块。我们的日志流可以用同样思路分组 |
| **Sentry** | 数据密集仪表盘的信息分层：什么该放大，什么该压到次要 |
| **ClickHouse** | 技术文档风格的表头与代码块处理，适合报告视图 |

> 注意：这三份的**配色一律不取**。Warp 的深色、Sentry 的粉紫、ClickHouse 的黄，都会把方向带偏。

### 2.5 Taste Skill / Impeccable（不是组件库）

两者都是**方法论 Skill**，不提供组件。作用写在
`docs/superpowers/specs/2026-11-17-frontend-design-rules.md`。

| | 采用的部分 |
|---|---|
| Taste Skill | 三旋钮取值（方差中高 / 动效低到中 / 密度高）、硬禁令清单、pre-flight 检查思路 |
| Impeccable | 共享设计法则（色彩策略、主题判定、排版、布局、动效）、绝对禁令、AI slop test 两问 |

**不采用**：Impeccable 的 23 个命令流程（时间不够）、Taste Skill 的 v2 experimental 规则（仍在变）。

---

## 3. 最终依赖清单

```jsonc
// package.json dependencies（运行时，共 5 项）
{
  "react": "^19",
  "react-dom": "^19",
  "clsx": "^2"
}
// devDependencies 关键项
{
  "typescript": "^5",
  "vite": "^7",
  "tailwindcss": "^4",
  "@vitejs/plugin-react": "^5"
}
```

**没有动画库。** 全部动效用 CSS transition + 少量 `requestAnimationFrame`。

理由：Framer Motion 会让 bundle 增加数十 KB，而我们的动效只有三类（状态点过渡、日志行淡入、终态呈现），CSS 完全够。少一个依赖，演示时就少一个可能出问题的环节。

## 4. 需要自己写的组件

这些没有现成库可用，因为它们是**本产品特有的**：

| 组件 | 为什么必须自写 |
|---|---|
| `StateTimeline` 阶段时间线 | 状态枚举与切换逻辑是本项目独有 |
| `LogStream` 日志流 | 需要处理游标 `after=<seq>`、断线续读、自动滚动打断 |
| `DiffView` 补丁视图 | 只需增删行标记，不要语法高亮，现成库都带高亮 |
| `VerdictBadge` 判定徽章 | 三分法（校验类 / 环境类 / 系统类）是本项目的信息设计 |
| `ResultBar` 结果条 | 字段来自 `RunResult`，形状固定 |
| `FenceNotice` 拦截提示 | “围栏可见”概念的核心载体，没有任何库提供 |

**六个组件，都是小件。** 估算工作量：每个 40–80 行，合计约 350 行。

---

## 5. 否决清单（明确不引入）

| 库 / 方案 | 否决理由 |
|---|---|
| shadcn/ui 全量 | 默认样式辨识度太高，且需要 Radix 一整串依赖 |
| Ant Design / MUI / Chakra | 后台模板感，直接踩“古老管理系统”这条线 |
| Framer Motion | 动效需求太少，不值一个依赖 |
| GSAP | 同上，且体积更大 |
| 任何“玻璃拟态”组件 | `DESIGN.md` §7.1 禁令 |
| 任何“渐变文字”组件 | 同上 |
| 图标库（lucide / heroicons） | **暂不引入**。我们的状态用色点 + 文字表达，不用图标。确实需要时手写 3–5 个 SVG |

> 图标这一条说明一下：图标库本身不算 AI 味，但“每个功能配一个图标”的排版习惯会立刻让界面变成后台模板。我们先用文字，确有必要再补。

---

## 6. 使用纪律

采用任何一个组件后，必须做四件事，否则不许合入：

1. **删皮肤**：移除所有自带背景、阴影、圆角、渐变色
2. **换色**：只用 `DESIGN.md` §2 定义的 token
3. **换缓动**：统一 `ease-out-quart`，禁止 bounce / elastic
4. **过自检**：跑 `DESIGN.md` §7.3 的两问

---

## 7. 参考链接汇总

| 名称 | 地址 | 协议 | 我们的用法 |
|---|---|---|---|
| React Bits | https://www.reactbits.dev/ | MIT + Commons Clause | 借 `AnimatedList` / `CountUp` 逻辑 |
| Aceternity UI | https://ui.aceternity.com/ | 组件复制 | 不采用 |
| Uiverse | https://uiverse.io/ | 开源 CSS | 最多借 1 个加载态 |
| awesome-design-md | https://github.com/VoltAgent/awesome-design-md | MIT | 借 9 章结构，不抄内容 |
| awesome-design-md 中文版 | https://github.com/bayshier/awesome-design-md-cn | MIT | 中文表述参考 |
| Taste Skill | https://github.com/Leonxlnx/taste-skill | MIT | 借三旋钮与禁令 |
| Impeccable | https://github.com/pbakaus/impeccable | Apache 2.0 | 借设计法则与 slop test |
| Google Stitch DESIGN.md 规范 | https://stitch.withgoogle.com/docs/design-md/specification/ | — | `DESIGN.md` 格式依据 |
