import { useMemo } from "react";
import { motion } from "motion/react";
import dagre from "@dagrejs/dagre";
import type { RunEvent, RunState } from "../types";
import { isActionEvent, isBlockedEvent, type ActionRow, type LoopView, type StageView } from "../runModel";

/**
 * 执行拓扑图 —— 用 dagre 自动布局。
 *
 * 图的**结构**是固定的（拉取 → 安装 → 测试 → 诊断 → 门岗 → 修复 → 报告），
 * 但节点状态、副标题、边标签、以及"被拦截动作"的卫星节点，全部来自后端事件
 * （buildStages / buildLoop / buildRows）。
 *
 * 这里不允许按 `state` 在状态链上的索引去猜"已完成"：runModel.ts 开头已经
 * 写明那条写法会在 run 一开始就把前面所有阶段点亮成绿色。也不允许写死演示
 * 数据——上一版把 `exit 1` / `审查 8 次` / `第 1 轮` / `修复回环 ×1` 和两个
 * 固定的红色拦截节点（`../../etc/passwd`）画进了每一次运行，包括零拦截的
 * clean-example。界面只陈述后端证明过的事实。
 */

/** 截图模式：关闭入场动画，避免无头渲染抓到半透明中间帧。 */
const STILL = new URLSearchParams(window.location.search).get("still") === "1";

const NODE_W = 148;
/** 节点高度要放得下「标签 / 副标题 / 步骤圆点」三行，72 那版没有圆点的位置。 */
const NODE_H = 92;
/** 节点下方最多画几个圆点；多出来的折成「+N」。 */
const MAX_DOTS = 4;
const DOT_GAP = 16;
/** 图会被缩到 0.9 倍左右，圆点本身得画大些才看得见（命中区再大一圈）。 */
const DOT_R = 5;
const DOT_HIT = 11;

type Tone = "done" | "active" | "bad" | "todo" | "gate";

interface NodeDef {
  id: string;
  label: string;
  sub: string;
  tone: Tone;
  big?: boolean;
  /** 卫星节点（被拦截动作）不参与主流程布局 */
  satellite?: boolean;
}

interface EdgeDef {
  from: string;
  to: string;
  kind: "flow" | "fail" | "loop" | "ok" | "block";
  label?: string;
  /** 未发生的条件分支画暗色，不冒充本次运行的真实结果。 */
  dim?: boolean;
}

interface Graph {
  nodes: NodeDef[];
  edges: EdgeDef[];
  blocks: NodeDef[];
  /** 真实被拦总数（卫星节点最多画 2 个，但徽标要说全量）。 */
  blockedTotal: number;
}

const TONE: Record<Tone, { line: string; fill: string; glow: string }> = {
  done: { line: "#2ee6a8", fill: "rgba(46,230,168,.10)", glow: "rgba(46,230,168,.55)" },
  active: { line: "#6c8fff", fill: "rgba(108,143,255,.16)", glow: "rgba(108,143,255,.75)" },
  bad: { line: "#ff5c47", fill: "rgba(255,92,71,.12)", glow: "rgba(255,92,71,.6)" },
  todo: { line: "#3a4152", fill: "rgba(58,65,82,.18)", glow: "rgba(58,65,82,.25)" },
  gate: { line: "#9db4ff", fill: "rgba(108,143,255,.22)", glow: "rgba(108,143,255,.9)" },
};

/** 节点 → 它由哪个阶段的状态证明。 */
const NODE_TO_STAGE: Record<string, RunState | "succeeded"> = {
  clone: "preparing",
  install: "installing",
  test: "testing",
  diag: "diagnosing",
  gate: "repairing",
  repair: "repairing",
  report: "succeeded",
};

interface Props {
  state: RunState;
  stages: StageView[];
  loop: LoopView;
  rows: ActionRow[];
  /** 原始事件：节点下面的步骤圆点就是按 state 从这儿分桶的。 */
  events: RunEvent[];
  onSelectStage?: (id: RunState | "succeeded") => void;
  onSelectGate?: () => void;
  /** 点某一个圆点：看那一步的原文（动作走 ACI 流水线，其余走事件详情）。 */
  onSelectStep?: (seq: number) => void;
}

/**
 * 由事件推导整张图。唯一的数据来源是 buildStages / buildLoop / buildRows，
 * 没有从 state 索引或写死字符串里"推"出来的结论。
 */
export function buildGraph({
  state,
  stages,
  loop,
  rows,
}: Omit<Props, "events" | "onSelectStage" | "onSelectGate" | "onSelectStep">): Graph {
  const statusOf = (id: RunState | "succeeded"): StageView["status"] =>
    stages.find((s) => s.id === id)?.status ?? "todo";
  const toneOf = (id: RunState | "succeeded"): Tone => {
    const st = statusOf(id);
    if (st === "bad") return "bad";
    if (st === "active") return "active";
    if (st === "done") return "done";
    return "todo";
  };

  // 最后一次测试的执行结果：rows 里 test 行的 result 是 `exit N` 或事件原文。
  const lastTest = [...rows].reverse().find((r) => r.name === "test");
  const testSub = lastTest ? lastTest.result.slice(0, 18) : "待运行";
  const exitMatch = lastTest ? /^exit (-?\d+)$/.exec(lastTest.result) : null;
  const testFailed = statusOf("testing") === "bad";

  // 诊断结论、门岗审查次数、修复轮数都来自真实事件。
  const lastRound = loop.rounds.at(-1) ?? null;
  const reviewCount = rows.filter((r) => r.name !== "install" && r.name !== "test").length;
  const roundCount = loop.rounds.length;
  const hasReport = state === "succeeded";

  const nodes: NodeDef[] = [
    { id: "clone", label: "拉取仓库", sub: "git clone", tone: toneOf("preparing") },
    { id: "install", label: "安装依赖", sub: "pip install", tone: toneOf("installing") },
    { id: "test", label: "运行测试", sub: testSub, tone: toneOf("testing") },
    {
      id: "diag",
      label: "诊断失败",
      sub: lastRound ? lastRound.failureClass.slice(0, 18) : "待诊断",
      tone: toneOf("diagnosing"),
    },
    {
      id: "gate",
      label: "ACI 门岗",
      sub: `审查 ${reviewCount} 次`,
      tone: statusOf("repairing") === "active" ? "gate" : toneOf("repairing"),
      big: true,
    },
    {
      id: "repair",
      label: "修复动作",
      sub: roundCount > 0 ? `第 ${roundCount} 轮` : "待修复",
      tone: toneOf("repairing"),
    },
    {
      id: "report",
      label: "复现报告",
      sub: hasReport ? "已生成" : "待生成",
      tone: hasReport ? "done" : toneOf("succeeded"),
    },
  ];

  const edges: EdgeDef[] = [
    { from: "clone", to: "install", kind: "flow" },
    { from: "install", to: "test", kind: "flow" },
    {
      from: "test",
      to: "diag",
      kind: "fail",
      // 只有真的失败过才敢写退出码；否则只标"这是一条失败分支"。
      label: testFailed && exitMatch ? `✕ exit ${exitMatch[1]}` : "✕ 失败时",
      dim: !testFailed,
    },
    { from: "diag", to: "gate", kind: "flow" },
    { from: "gate", to: "repair", kind: "flow" },
    { from: "repair", to: "report", kind: "ok", label: "通过", dim: !hasReport },
  ];

  // 修复回环：跑过几轮才画几条弧的标注，没有回环就不画。
  if (roundCount > 0) {
    edges.push({ from: "repair", to: "test", kind: "loop", label: `修复回环 ×${roundCount}` });
  }

  // 卫星节点：只对真实被 ACI 拦下的动作生成，最多两个，没有就一个都不画。
  const blockedRows = rows.filter((r) => r.blocked);
  const blocks: NodeDef[] = blockedRows.slice(-2).map((r, i) => ({
    id: `block${i + 1}`,
    label: r.name,
    sub: (r.target || r.reason || "—").slice(0, 22),
    tone: "bad" as Tone,
    satellite: true,
  }));
  for (const b of blocks) {
    nodes.push(b);
    edges.push({ from: "gate", to: b.id, kind: "block" });
  }

  return { nodes, edges, blocks, blockedTotal: blockedRows.length };
}

export function FlowGraph(props: Props) {
  const graph = useMemo(
    () => buildGraph(props),
    // props 里的对象由父组件 useMemo 缓存，只在 events/state 变化时换引用
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [props.state, props.stages, props.loop, props.rows],
  );
  const { nodes, edges, blockedTotal } = graph;

  /**
   * 节点 → 它自己的步骤（后端事件原文，不按数量编）。
   *
   * 用 event.state 分桶而不是**动作行**：install / test / preparing 这些
   * 阶段事件不在动作流里（动作流只收 ACI 动作），但它们是节点的真实步骤。
   * gate / repair 共用 repairing：ACI 动作归门岗，其余（patch、finish_repair）
   * 归修复节点，否则门岗会吃掉修复的步骤。
   */
  const stepsByNode = useMemo(() => {
    const map = new Map<string, RunEvent[]>();
    const push = (id: string, e: RunEvent) => {
      const arr = map.get(id);
      if (arr) arr.push(e);
      else map.set(id, [e]);
    };
    for (const e of props.events) {
      if (e.state === "repairing") {
        push(isActionEvent(e) ? "gate" : "repair", e);
        continue;
      }
      const node = (Object.keys(NODE_TO_STAGE) as string[]).find(
        (k) => NODE_TO_STAGE[k] === e.state,
      );
      // planning / queued 没有对应节点，落在「另有 N 条事件」里，不硬塞给谁
      if (node) push(node, e);
    }
    return map;
  }, [props.events]);

  // ── dagre 布局：只在图结构变化时算一次 ──
  const layout = useMemo(() => {
    const g = new dagre.graphlib.Graph();
    g.setGraph({
      rankdir: "LR",
      // 三条竖排（主链 + 两个卫星节点）已经很高了，再留 34px 的节点间距，
      // 整张图只能缩到 0.8 倍，节点下方的步骤圆点就糊成一片了。
      nodesep: 26,
      ranksep: 56,
      marginx: 18,
      marginy: 14,
      ranker: "network-simplex",
    });
    g.setDefaultEdgeLabel(() => ({}));

    for (const n of nodes) {
      g.setNode(n.id, {
        width: n.big ? NODE_W + 24 : NODE_W,
        height: n.big ? NODE_H + 12 : NODE_H,
      });
    }
    for (const e of edges) {
      g.setEdge(e.from, e.to);
    }
    dagre.layout(g);

    const pos: Record<string, { x: number; y: number; w: number; h: number }> = {};
    for (const n of nodes) {
      const d = g.node(n.id);
      if (!d) continue;
      pos[n.id] = { x: d.x, y: d.y, w: d.width, h: d.height };
    }

    // 边的路径：dagre 给了控制点，转成 SVG path
    const paths: { def: EdgeDef; path: string; labelAt?: { x: number; y: number } }[] = [];
    for (const e of edges) {
      const d = g.edge(e.from, e.to);
      if (!d?.points?.length) continue;
      const pts = d.points;
      let path = `M ${pts[0].x},${pts[0].y}`;
      if (pts.length === 2) {
        path += ` L ${pts[1].x},${pts[1].y}`;
      } else {
        for (let i = 1; i < pts.length - 1; i++) {
          const mx = (pts[i].x + pts[i + 1].x) / 2;
          const my = (pts[i].y + pts[i + 1].y) / 2;
          path += ` Q ${pts[i].x},${pts[i].y} ${mx},${my}`;
        }
        path += ` L ${pts[pts.length - 1].x},${pts[pts.length - 1].y}`;
      }
      const mid = pts[Math.floor(pts.length / 2)];
      paths.push({ def: e, path, labelAt: e.label ? { x: mid.x, y: mid.y - 10 } : undefined });
    }

    return { nodes: pos, edges: paths, width: g.graph().width ?? 800, height: g.graph().height ?? 400 };
  }, [nodes, edges]);

  const pad = 20;
  const vbW = layout.width + pad * 2;
  const vbH = layout.height + pad * 2;

  return (
    <div
      className="h-full w-full"
      style={{
        transform:
          "rotateX(calc(var(--my, 0) * -2deg)) rotateY(calc(var(--mx, 0) * 2.4deg))",
        transformStyle: "preserve-3d",
        transition: "transform .6s cubic-bezier(.16,1,.3,1)",
        perspective: "1600px",
      }}
    >
      <svg
        viewBox={`${-pad} ${-pad} ${vbW} ${vbH}`}
        preserveAspectRatio="xMidYMid meet"
        className="h-full w-full"
      >
        <defs>
          <filter id="fg-glow" x="-70%" y="-70%" width="240%" height="240%">
            <feGaussianBlur stdDeviation="6" result="b" />
            <feMerge>
              <feMergeNode in="b" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
          <filter id="fg-glow-strong" x="-120%" y="-120%" width="340%" height="340%">
            <feGaussianBlur stdDeviation="13" result="b" />
            <feMerge>
              <feMergeNode in="b" />
              <feMergeNode in="b" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
          <marker
            id="fg-arrow"
            viewBox="0 0 10 10"
            refX="8"
            refY="5"
            markerWidth="5"
            markerHeight="5"
            orient="auto-start-reverse"
          >
            <path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor" />
          </marker>
        </defs>

        {/* ── 边 ── */}
        {layout.edges.map(({ def, path, labelAt }, i) => {
          const isLoop = def.kind === "loop";
          const isFail = def.kind === "fail";
          const isBlock = def.kind === "block";
          const isOk = def.kind === "ok";
          // 正在进行的节点，入边上有颗光点在跑：这是「这一步还在走」的唯一动效，
          // 不再给所有边加无意义的流水动画。
          const targetTone = nodes.find((n) => n.id === def.to)?.tone;
          const flowing = !def.dim && (targetTone === "active" || targetTone === "gate");
          const color = def.dim
            ? "rgba(148,163,196,.26)"
            : isLoop
              ? "#6c8fff"
              : isFail || isBlock
                ? "#ff5c47"
                : isOk
                  ? "#2ee6a8"
                  : "rgba(148,163,196,.5)";

          return (
            <g key={`${def.from}-${def.to}`}>
              {/* 回路加一层柔光底 */}
              {isLoop && (
                <motion.path
                  d={path}
                  fill="none"
                  stroke="rgba(108,143,255,.16)"
                  strokeWidth={8}
                  strokeLinecap="round"
                  initial={STILL ? false : { pathLength: 0 }}
                  animate={{ pathLength: 1 }}
                  transition={STILL ? { duration: 0 } : { duration: 1.4, delay: 0.5 + i * 0.1, ease: [0.16, 1, 0.3, 1] }}
                />
              )}

              <motion.path
                d={path}
                fill="none"
                stroke={color}
                strokeWidth={isLoop || isFail ? 1.6 : 1.1}
                strokeOpacity={isBlock ? 0.65 : 0.9}
                strokeDasharray={isFail ? "5 4" : isBlock ? "3 4" : isLoop ? "6 10" : undefined}
                strokeLinecap="round"
                markerEnd={isFail ? undefined : "url(#fg-arrow)"}
                style={{ color }}
                initial={STILL ? false : { pathLength: 0 }}
                animate={{ pathLength: 1 }}
                transition={STILL ? { duration: 0 } : { duration: 1.1, delay: 0.25 + i * 0.09, ease: [0.16, 1, 0.3, 1] }}
              />

              {/* 回路：流动虚线 + 沿线粒子，这是「回转」的动感来源 */}
              {isLoop && (
                <>
                  <motion.path
                    d={path}
                    fill="none"
                    stroke="#a8c4ff"
                    strokeWidth={2.2}
                    strokeDasharray="5 16"
                    strokeLinecap="round"
                    initial={STILL ? false : { opacity: 0 }}
                    animate={{ opacity: 0.95, strokeDashoffset: [0, -42] }}
                    transition={{
                      opacity: { duration: 0.5, delay: 1.5 },
                      strokeDashoffset: { duration: 1.6, repeat: Infinity, ease: "linear" },
                    }}
                  />
                  <circle r={2.6} fill="#cfe0ff">
                    <animateMotion dur="3s" repeatCount="indefinite" path={path} begin="1.6s" />
                  </circle>
                  <circle r={1.8} fill="#8fb0ff" opacity={0.75}>
                    <animateMotion dur="3s" repeatCount="indefinite" path={path} begin="3.1s" />
                  </circle>
                </>
              )}

              {flowing && !isLoop && (
                <circle r={2.2} fill={color === "rgba(148,163,196,.5)" ? "#cfd9f2" : color}>
                  <animateMotion dur="1.9s" repeatCount="indefinite" path={path} />
                </circle>
              )}

              {labelAt && def.label && (
                <motion.text
                  x={labelAt.x}
                  y={labelAt.y}
                  textAnchor="middle"
                  className="font-mono"
                  fontSize={9.5}
                  fill={def.dim ? "rgba(154,163,181,.5)" : isLoop ? "#9db4ff" : "rgba(255,140,128,.85)"}
                  initial={STILL ? false : { opacity: 0 }}
                  animate={{ opacity: 1 }}
                  transition={STILL ? { duration: 0 } : { delay: 1.2, duration: 0.5 }}
                >
                  {isLoop ? `⟲ ${def.label}` : def.label}
                </motion.text>
              )}
            </g>
          );
        })}

        {/* ── 节点 ── */}
        {nodes.map((n, i) => {
          const box = layout.nodes[n.id];
          if (!box) return null;
          const c = TONE[n.tone];
          const isActive = n.tone === "active" || n.tone === "gate";

          /** 这个节点的真实步骤（卫星节点是被拦动作，没有自己的步骤列表）。 */
          const steps = n.satellite ? [] : (stepsByNode.get(n.id) ?? []);
          const shown = steps.slice(-MAX_DOTS);
          const overflow = steps.length - shown.length;
          const dotY = box.y + box.h / 2 - (n.big ? 21 : 20);
          const dotsW = Math.max(0, (shown.length - 1) * DOT_GAP);
          const stripW = dotsW + (overflow > 0 ? 26 : 0);
          const dotsX0 = box.x - stripW / 2 + (overflow > 0 ? 26 : 0);

          const openAll = () => {
            if (n.satellite) return;
            if (n.id === "gate") props.onSelectGate?.();
            else props.onSelectStage?.(NODE_TO_STAGE[n.id] ?? "queued");
          };

          return (
            <motion.g
              key={n.id}
              initial={STILL ? false : { opacity: 0, scale: 0.86 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={STILL ? { duration: 0 } : { duration: 0.55, delay: 0.12 + i * 0.07, ease: [0.16, 1, 0.3, 1] }}
              style={{
                transformOrigin: `${box.x}px ${box.y}px`,
                cursor: n.satellite ? "default" : "pointer",
              }}
              onClick={openAll}
            >
              {/* 外发光 */}
              <rect
                x={box.x - box.w / 2}
                y={box.y - box.h / 2}
                width={box.w}
                height={box.h}
                rx={12}
                fill={c.fill}
                stroke={c.line}
                strokeWidth={n.big ? 1.8 : 1.2}
                strokeOpacity={n.tone === "todo" ? 0.55 : 0.95}
                filter={n.big ? "url(#fg-glow-strong)" : isActive ? "url(#fg-glow)" : undefined}
              />

              {/* 呼吸描边（活跃节点） */}
              {isActive && (
                <motion.rect
                  x={box.x - box.w / 2 - 4}
                  y={box.y - box.h / 2 - 4}
                  width={box.w + 8}
                  height={box.h + 8}
                  rx={15}
                  fill="none"
                  stroke={c.line}
                  strokeWidth={1}
                  animate={{ opacity: [0.55, 0], scale: [1, 1.06] }}
                  transition={{ duration: 2.2, repeat: Infinity, ease: "easeOut" }}
                  style={{ transformOrigin: `${box.x}px ${box.y}px` }}
                />
              )}

              {/* 顶部受光高光（立体感） */}
              <rect
                x={box.x - box.w / 2 + 1}
                y={box.y - box.h / 2 + 1}
                width={box.w - 2}
                height={2}
                rx={1}
                fill="#fff"
                fillOpacity={0.16}
              />

              {/* 门岗十字 */}
              {n.big && (
                <g opacity={0.8} transform={`translate(${box.x - box.w / 2 + 16},${box.y - box.h / 2 + 16})`}>
                  <line x1={-5} y1={0} x2={5} y2={0} stroke={c.line} strokeWidth={1.6} />
                  <line x1={0} y1={-5} x2={0} y2={5} stroke={c.line} strokeWidth={1.6} />
                </g>
              )}

              {/* 标签 */}
              <text
                x={box.x}
                y={box.y - (n.big ? 22 : 20)}
                textAnchor="middle"
                fontSize={n.big ? 13.5 : 12}
                fontWeight={n.big ? 600 : 500}
                fill={n.tone === "todo" ? "rgba(154,163,181,.75)" : "#eef1f6"}
              >
                {n.label}
              </text>
              <text
                x={box.x}
                y={box.y - (n.big ? 6 : 4)}
                textAnchor="middle"
                className="font-mono"
                fontSize={9}
                fill={n.tone === "bad" ? "rgba(255,140,128,.9)" : "rgba(154,163,181,.62)"}
              >
                {n.sub}
              </text>

              {/*
                步骤圆点：一个点 = 一条后端事件（不是按进度编的格子）。
                只画最近 MAX_DOTS 个，更早的折成「+N」——点它等于点节点，
                看该阶段全部步骤；点某个点只看那一步的原文。
              */}
              {shown.length > 0 && (
                <g>
                  {shown.length > 1 && (
                    <line
                      x1={dotsX0}
                      y1={dotY}
                      x2={dotsX0 + dotsW}
                      y2={dotY}
                      stroke="rgba(148,163,196,.3)"
                      strokeWidth={1}
                      strokeLinecap="round"
                    />
                  )}

                  {/* 新的一步落下来时，轨道从左往右重新亮一遍——"这一步接上了" */}
                  {shown.length > 1 && (
                    <motion.line
                      key={`fill-${steps.length}-${shown.length}`}
                      x1={dotsX0}
                      y1={dotY}
                      x2={dotsX0 + dotsW}
                      y2={dotY}
                      stroke="rgba(168,189,228,.8)"
                      strokeWidth={1.3}
                      strokeLinecap="round"
                      initial={STILL ? false : { pathLength: 0, opacity: 0.9 }}
                      animate={{ pathLength: 1, opacity: 0.55 }}
                      transition={STILL ? { duration: 0 } : { duration: 0.65, ease: [0.16, 1, 0.3, 1] }}
                    />
                  )}

                  {/* 步骤之间的动态：活跃节点上有一颗光点沿圆点轨道跑 */}
                  {isActive && shown.length > 1 && !STILL && (
                    <circle r={1.9} fill="#dce7ff" opacity={0.9}>
                      <animateMotion
                        dur="1.8s"
                        repeatCount="indefinite"
                        path={`M ${dotsX0},${dotY} L ${dotsX0 + dotsW},${dotY}`}
                      />
                    </circle>
                  )}

                  {shown.map((e, k) => {
                    const cx = dotsX0 + k * DOT_GAP;
                    const blocked = isBlockedEvent(e);
                    const code = typeof e.data?.exit_code === "number" ? e.data.exit_code : null;
                    const ok = e.data?.ok === true || code === 0;
                    // 退出码非 0 是"这一步真的失败了"，不是被拦（红）；用琥珀色分开，
                    // 否则 install 第一次 exit 1 会和成功一样是绿点，等于骗人。
                    const fill = blocked
                      ? "#ff5c47"
                      : code !== null && code !== 0
                        ? "#ffb454"
                        : ok
                          ? "#2ee6a8"
                          : "#a8bde4";
                    const newest = k === shown.length - 1;
                    return (
                      <g
                        key={e.sequence}
                        style={{ cursor: "pointer" }}
                        onClick={(ev) => {
                          ev.stopPropagation();
                          props.onSelectStep?.(e.sequence);
                        }}
                      >
                        <title>{`#${e.sequence} ${e.kind} · ${e.message.slice(0, 46)}`}</title>
                        {/* 命中区比圆点大：缩放后 5px 的点直接点也很难点中 */}
                        <circle cx={cx} cy={dotY} r={DOT_HIT} fill="transparent" />
                        {newest && isActive && !STILL && (
                          <motion.circle
                            cx={cx}
                            cy={dotY}
                            r={DOT_R}
                            fill="none"
                            stroke={fill}
                            strokeWidth={1}
                            animate={{ r: [DOT_R, DOT_R * 2.1], opacity: [0.75, 0] }}
                            transition={{ duration: 1.7, repeat: Infinity, ease: "easeOut" }}
                          />
                        )}
                        <motion.circle
                          cx={cx}
                          cy={dotY}
                          r={blocked ? DOT_R + 0.6 : DOT_R}
                          fill={fill}
                          initial={STILL ? false : { scale: 0, opacity: 0 }}
                          animate={{ scale: 1, opacity: 1 }}
                          transition={
                            STILL ? { duration: 0 } : { duration: 0.32, ease: [0.16, 1, 0.3, 1] }
                          }
                          style={{ transformOrigin: `${cx}px ${dotY}px` }}
                        />
                      </g>
                    );
                  })}

                  {overflow > 0 && (
                    <g
                      style={{ cursor: "pointer" }}
                      onClick={(ev) => {
                        ev.stopPropagation();
                        openAll();
                      }}
                    >
                      <title>{`还有 ${overflow} 步更早，点开看全部`}</title>
                      <text
                        x={dotsX0 - 8}
                        y={dotY + 3.6}
                        textAnchor="end"
                        className="font-mono"
                        fontSize={10}
                        fill="rgba(168,189,228,.95)"
                      >
                        +{overflow}
                      </text>
                    </g>
                  )}
                </g>
              )}
            </motion.g>
          );
        })}

        {/* 拦截计数：只在真有被拦动作时出现，数字是全量不是卫星节点数 */}
        {blockedTotal > 0 && (
          <motion.g
            initial={STILL ? false : { opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={STILL ? { duration: 0 } : { delay: 1.8, duration: 0.6 }}
          >
            <rect
              x={layout.nodes["gate"].x - 42}
              y={layout.nodes["gate"].y - layout.nodes["gate"].h / 2 - 34}
              width={84}
              height={22}
              rx={11}
              fill="rgba(255,92,71,.14)"
              stroke="rgba(255,92,71,.5)"
            />
            <text
              x={layout.nodes["gate"].x}
              y={layout.nodes["gate"].y - layout.nodes["gate"].h / 2 - 19}
              textAnchor="middle"
              className="font-mono"
              fontSize={10}
              fill="#ff8c80"
            >
              ✕ 拦截 {blockedTotal}
            </text>
          </motion.g>
        )}
      </svg>
    </div>
  );
}
