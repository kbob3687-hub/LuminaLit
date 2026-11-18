import { useMemo } from "react";
import { motion } from "motion/react";
import dagre from "@dagrejs/dagre";
import type { RunState } from "../types";

/**
 * 执行拓扑图 —— 用 dagre 自动布局。
 *
 * 为什么自动布局：手算坐标改一次错一次，而且节点一多就排不下。
 * dagre 只负责「谁在哪」；视觉（光环/粒子/发光）仍由这里画。
 *
 * 图的形状：
 *   拉取 → 安装 → 测试 ──✕ exit 1──→ 诊断 ──→ ACI 门岗 ──→ 修复
 *                        ↑                                    │
 *                        └──────── 修复回环（虚线弧）─────────┘
 *   修复 ──→ 复现报告（成功路径）
 *   ACI 门岗 ──✕──→ 两个被拦截的动作（卫星）
 */

/** 截图模式：关闭入场动画，避免无头渲染抓到半透明中间帧。 */
const STILL = new URLSearchParams(window.location.search).get("still") === "1";

const NODE_W = 148;
const NODE_H = 74;

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
}

/** 由后端状态推导每个节点的色调。 */
function buildTones(state: RunState): Record<string, Tone> {
  const order: RunState[] = [
    "queued",
    "preparing",
    "planning",
    "installing",
    "testing",
    "diagnosing",
    "repairing",
    "succeeded",
  ];
  const idx = state === "failed" || state === "cancelled" ? 6 : Math.max(0, order.indexOf(state));
  const failed = state === "failed";

  return {
    clone: idx >= 1 ? "done" : "active",
    install: idx >= 4 ? "done" : idx >= 3 ? "active" : "todo",
    test: state === "succeeded" ? "done" : idx >= 5 || failed ? "bad" : idx >= 4 ? "active" : "todo",
    diag: idx >= 6 ? "done" : idx >= 5 ? "active" : "todo",
    gate: idx >= 6 ? "gate" : "todo",
    repair: idx >= 6 && state !== "succeeded" ? "active" : idx >= 7 ? "done" : "todo",
    report: state === "succeeded" ? "done" : "todo",
    block1: "bad",
    block2: "bad",
  };
}

const TONE: Record<Tone, { line: string; fill: string; glow: string }> = {
  done: { line: "#2ee6a8", fill: "rgba(46,230,168,.10)", glow: "rgba(46,230,168,.55)" },
  active: { line: "#6c8fff", fill: "rgba(108,143,255,.16)", glow: "rgba(108,143,255,.75)" },
  bad: { line: "#ff5c47", fill: "rgba(255,92,71,.12)", glow: "rgba(255,92,71,.6)" },
  todo: { line: "#3a4152", fill: "rgba(58,65,82,.18)", glow: "rgba(58,65,82,.25)" },
  gate: { line: "#9db4ff", fill: "rgba(108,143,255,.22)", glow: "rgba(108,143,255,.9)" },
};

/** 图定义：节点 + 边。改流程只改这里，坐标由 dagre 算。 */
const NODES: NodeDef[] = [
  { id: "clone", label: "拉取仓库", sub: "git clone", tone: "todo" },
  { id: "install", label: "安装依赖", sub: "pip install", tone: "todo" },
  { id: "test", label: "运行测试", sub: "exit 1", tone: "todo" },
  { id: "diag", label: "诊断失败", sub: "missing_dep", tone: "todo" },
  { id: "gate", label: "ACI 门岗", sub: "审查 8 次", tone: "todo", big: true },
  { id: "repair", label: "修复动作", sub: "第 1 轮", tone: "todo" },
  { id: "report", label: "复现报告", sub: "待生成", tone: "todo" },
  { id: "block1", label: "read_file", sub: "../../etc/passwd", tone: "bad", satellite: true },
  { id: "block2", label: "install_req", sub: "numpy; rm -rf /", tone: "bad", satellite: true },
];

const EDGES: EdgeDef[] = [
  { from: "clone", to: "install", kind: "flow" },
  { from: "install", to: "test", kind: "flow" },
  { from: "test", to: "diag", kind: "fail", label: "✕ exit 1" },
  { from: "diag", to: "gate", kind: "flow" },
  { from: "gate", to: "repair", kind: "flow" },
  { from: "repair", to: "report", kind: "ok", label: "通过" },
  { from: "repair", to: "test", kind: "loop", label: "修复回环 ×1" },
  { from: "gate", to: "block1", kind: "block" },
  { from: "gate", to: "block2", kind: "block" },
];

interface Props {
  state: RunState;
  blockedCount: number;
  onSelectStage?: (id: RunState | "succeeded") => void;
  onSelectGate?: () => void;
}

const NODE_TO_STATE: Record<string, RunState | "succeeded"> = {
  clone: "preparing",
  install: "installing",
  test: "testing",
  diag: "diagnosing",
  gate: "repairing",
  repair: "repairing",
  report: "succeeded",
};

export function FlowGraph({ state, blockedCount, onSelectStage, onSelectGate }: Props) {
  const tones = useMemo(() => buildTones(state), [state]);

  // ── dagre 布局：只在图结构变化时算一次 ──
  const layout = useMemo(() => {
    const g = new dagre.graphlib.Graph();
    g.setGraph({
      rankdir: "LR",
      nodesep: 34,
      ranksep: 62,
      marginx: 24,
      marginy: 20,
      ranker: "network-simplex",
    });
    g.setDefaultEdgeLabel(() => ({}));

    for (const n of NODES) {
      g.setNode(n.id, {
        width: n.big ? NODE_W + 24 : NODE_W,
        height: n.big ? NODE_H + 12 : NODE_H,
      });
    }
    for (const e of EDGES) {
      g.setEdge(e.from, e.to);
    }
    dagre.layout(g);

    const nodes: Record<string, { x: number; y: number; w: number; h: number }> = {};
    for (const n of NODES) {
      const d = g.node(n.id);
      if (!d) continue;
      nodes[n.id] = { x: d.x, y: d.y, w: d.width, h: d.height };
    }

    // 边的路径：dagre 给了控制点，转成 SVG path
    const edges: { def: EdgeDef; path: string; labelAt?: { x: number; y: number } }[] = [];
    for (const e of EDGES) {
      const d = g.edge(e.from, e.to);
      if (!d?.points?.length) continue;
      const pts = d.points;
      let path = `M ${pts[0].x},${pts[0].y}`;
      if (pts.length === 2) {
        path += ` L ${pts[1].x},${pts[1].y}`;
      } else {
        // 用二次贝塞尔平滑
        for (let i = 1; i < pts.length - 1; i++) {
          const mx = (pts[i].x + pts[i + 1].x) / 2;
          const my = (pts[i].y + pts[i + 1].y) / 2;
          path += ` Q ${pts[i].x},${pts[i].y} ${mx},${my}`;
        }
        path += ` L ${pts[pts.length - 1].x},${pts[pts.length - 1].y}`;
      }
      const mid = pts[Math.floor(pts.length / 2)];
      edges.push({ def: e, path, labelAt: e.label ? { x: mid.x, y: mid.y - 10 } : undefined });
    }

    return { nodes, edges, width: g.graph().width ?? 800, height: g.graph().height ?? 400 };
  }, []);

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
          const color = isLoop
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

              {labelAt && def.label && (
                <motion.text
                  x={labelAt.x}
                  y={labelAt.y}
                  textAnchor="middle"
                  className="font-mono"
                  fontSize={9.5}
                  fill={isLoop ? "#9db4ff" : "rgba(255,140,128,.85)"}
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
        {NODES.map((n, i) => {
          const box = layout.nodes[n.id];
          if (!box) return null;
          const tone = tones[n.id] ?? "todo";
          const c = TONE[tone];
          const isActive = tone === "active" || tone === "gate";

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
              onClick={() => {
                if (n.satellite) return;
                if (n.id === "gate") onSelectGate?.();
                else onSelectStage?.(NODE_TO_STATE[n.id] ?? "queued");
              }}
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
                strokeOpacity={tone === "todo" ? 0.55 : 0.95}
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
                y={box.y - 5}
                textAnchor="middle"
                fontSize={n.big ? 13.5 : 12}
                fontWeight={n.big ? 600 : 500}
                fill={tone === "todo" ? "rgba(154,163,181,.75)" : "#eef1f6"}
              >
                {n.label}
              </text>
              <text
                x={box.x}
                y={box.y + 12}
                textAnchor="middle"
                className="font-mono"
                fontSize={9}
                fill={tone === "bad" ? "rgba(255,140,128,.9)" : "rgba(154,163,181,.62)"}
              >
                {n.sub}
              </text>
            </motion.g>
          );
        })}

        {/* 拦截计数 */}
        {blockedCount > 0 && (
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
              ✕ 拦截 {blockedCount}
            </text>
          </motion.g>
        )}
      </svg>
    </div>
  );
}
