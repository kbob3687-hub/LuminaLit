import { AnimatePresence, motion } from "motion/react";
import clsx from "clsx";
import { useEffect, useMemo, useRef, useState } from "react";
import { labelOf, type RunEvent, type RunState, type RunStatus } from "../types";
import {
  aciTrace,
  buildLoop,
  buildProgress,
  buildRows,
  buildStages,
  explainFailure,
  isActionEvent,
  isBlockedEvent,
  type ActionRow,
  type StageView,
} from "../runModel";
import { FlowGraph } from "./FlowGraph";
import { LoopStrip } from "./LoopStrip";
import { ProgressBand } from "./ProgressBand";
import { ReportPanel } from "./ReportPanel";
import { Drawer } from "./Drawer";
import { AciPanel, LoopPanel, PlanPanel, StagePanel } from "./DrawerPanels";

/**
 * 场景 3：运行现场。
 *
 * 所有"进度"都由后端事件推导（runModel），组件不按索引猜。
 * 三层结构（DESIGN.md §1.2）：
 *   Hero     当前状态 + 三个关键数字
 *   Track    3D 阶段轨道 + 自愈回环条
 *   Body     动作流（左）+ 遥测 / ACI 审查（右）
 */

type Panel =
  | { type: "aci"; seq: number }
  | { type: "loop" }
  | { type: "stage"; id: StageView["id"] }
  | { type: "plan" }
  | { type: "report" }
  | null;

/** 截图模式：关掉入场动画，避免无头渲染抓到 opacity:0 的中间帧。 */
const STILL = new URLSearchParams(window.location.search).get("still") === "1";

interface Props {
  /** live=接后端轮询；replay=离线回放（没起后端时的默认，界面必须写明白）。 */
  mode: "live" | "replay";
  status: RunStatus | null;
  events: RunEvent[];
  onCancel?: () => void;
  cancelling?: boolean;
  /**
   * 轮询自身出错（网络断、后端 5xx）。必须和"任务卡住"区分开：
   * 轮询断了界面会停在最后一帧，看上去和任务没进展一模一样。
   */
  pollError?: string | null;
  /** 本次运行的靶场名（用户选的那张卡）。直链进来看不到，就留空。 */
  targetName?: string;
  /** 返回选择页换个仓库再跑。 */
  onBack?: () => void;
}

export function RunScene({
  mode,
  status,
  events,
  onCancel,
  cancelling,
  pollError,
  targetName,
  onBack,
}: Props) {
  const [panel, setPanel] = useState<Panel>(() => {
    // 演示/截图直链：?panel=aci&seq=7 / ?panel=loop（只读，不影响业务逻辑）
    const q = new URLSearchParams(window.location.search);
    const which = q.get("panel");
    if (which === "aci") return { type: "aci", seq: Number(q.get("seq") ?? 0) };
    if (which === "loop") return { type: "loop" };
    if (which === "plan") return { type: "plan" };
    if (which === "report") return { type: "report" };
    return null;
  });
  const state: RunState = status?.state ?? "queued";
  const replay = mode === "replay";
  /** 有没有"听到"后端说话。回放模式下本地时间轴就是数据源，恒为真。 */
  const connected = replay || status !== null;
  /** 后端已经收尾：终态之后再显示"当前阶段 XX 已持续 0s"就成了假信息。 */
  const terminal = ["succeeded", "failed", "cancelled"].includes(state);

  const stages = useMemo(() => buildStages(events, status?.state), [events, status?.state]);
  const loop = useMemo(() => buildLoop(events, status?.state), [events, status?.state]);
  const rows = useMemo(() => buildRows(events), [events]);
  const runId = status?.run_id ?? null;

  /**
   * 当前状态已持续多久（本地观测，不是后端时间戳——契约里没有）。
   *
   * 用途只有一个：把"卡住"变得可见。后端在 preparing 阶段只写 state、
   * 不写事件，界面就会一片空白；不给一个跳动的数字，看的人分不清是
   * "没连上"还是"连上了但后端没话说"。
   */
  const stateSince = useRef<number>(Date.now());
  const lastState = useRef<RunState | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (lastState.current !== state) {
      lastState.current = state;
      stateSince.current = Date.now();
    }
  }, [state]);

  useEffect(() => {
    if (!connected || ["succeeded", "failed", "cancelled"].includes(state)) return;
    const t = window.setInterval(() => setTick((n) => n + 1), 1000);
    return () => window.clearInterval(t);
  }, [connected, state]);

  const stageElapsedMs = Math.max(0, Date.now() - stateSince.current + tick * 0);
  const silent = connected && rows.length === 0 && stageElapsedMs > 4000;

  // 整体进度：已完成部分是事实，剩余部分是经验估算（runModel.buildProgress）。
  // 依赖 tick 才会每秒重算——否则倒计时会停在进入页面那一刻。
  const progress = useMemo(
    () => buildProgress(events, stages, status?.state),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [events, stages, status?.state, tick],
  );

  const blockedCount = rows.filter((r) => r.blocked).length;
  const reviewCount = rows.filter((r) => r.name !== "install" && r.name !== "test").length;
  const isFailed = state === "failed";

  /**
   * 失败原因的人话版。三类失败的原文来源不同：clone_failed 的 git stderr 在
   * 事件 message 里（PR #15 起），install / test 带 `stderr_tail` 字段，其余
   * 只有类别。这里**只陈述能核实的事实**，不替用户判定成因
   * （见 runModel.explainFailure 的注释）。
   */
  const failureCategory = labelOf(status?.result?.failure_category ?? "");
  const failExplain = useMemo(
    () => explainFailure(failureCategory, events),
    [failureCategory, events],
  );

  const headline = useMemo(() => {
    if (state === "succeeded") return { text: "复现成功", accent: "已验证通过" };
    if (state === "failed")
      return {
        text: "复现失败",
        accent: labelOf(status?.result?.failure_category ?? "") || "未分类",
      };
    if (state === "cancelled") return { text: "已取消", accent: "用户终止" };
    if (state === "repairing")
      return { text: "修复中", accent: loop.doing ? `第 ${loop.rounds.at(-1)?.round ?? 1} 轮` : "执行中" };
    if (!connected) return { text: "连接中", accent: "等待后端" };
    if (state === "queued") return { text: "排队中", accent: "等待调度" };
    return { text: stageLabelOf(state), accent: "执行中" };
  }, [state, status, connected, loop]);

  const activeStage = stages.find((s) => s.status === "active");
  const openedEvent =
    panel?.type === "aci" ? events.find((e) => e.sequence === panel.seq) : undefined;

  return (
    <div className="flex h-full flex-col">
      {/* ── 顶栏：状态徽标跟后端同步 ── */}
      <header className="flex h-[58px] shrink-0 items-center gap-4 border-b border-rule bg-gradient-to-b from-white/[.035] to-transparent px-6 backdrop-blur-md">
        <div className="flex items-center gap-2.5 text-[13px] font-semibold tracking-[.1em]">
          <motion.i
            className={clsx(
              "block size-[7px] rounded-full",
              !connected
                ? "bg-ink-muted"
                : isFailed
                  ? "bg-reject shadow-[0_0_12px_var(--color-reject)]"
                  : state === "succeeded"
                    ? "bg-pass shadow-[0_0_12px_var(--color-pass)]"
                    : "bg-accent shadow-[0_0_12px_var(--color-accent)]",
            )}
            animate={
              connected && !["succeeded", "failed", "cancelled"].includes(state)
                ? { opacity: [0.35, 1, 0.35], scale: [0.85, 1.15, 0.85] }
                : { opacity: 1, scale: 1 }
            }
            transition={{ duration: 2, repeat: Infinity, ease: "easeInOut" }}
          />
          PaperHarness
        </div>

        {/* 靶场名来自用户选的那张卡，不写死；直链进来没有名字就不显示 */}
        {targetName && (
          <div
            className="max-w-[220px] truncate rounded-[5px] border border-rule-hi bg-white/[.03] px-2 py-0.5 font-mono text-[10.5px] text-ink-mid"
            title={targetName}
          >
            {targetName}
          </div>
        )}

        <div className="font-mono text-[11px] tracking-[.04em] text-ink-muted">
          {status?.run_id ? `run_${status.run_id.slice(0, 8)}` : "run_—"}
        </div>

        {/* 后端状态徽标：live 时是 GET /api/runs/{id} 的 state 原值；replay 时写明是回放 */}
        <div
          className={clsx(
            "flex items-center gap-2 rounded-[6px] border px-2.5 py-1 font-mono text-[10.5px]",
            replay && "border-warn/40 bg-warn/[.07] text-warn",
            !replay && !connected && "border-rule-hi text-ink-muted",
            !replay &&
              connected &&
              !isFailed &&
              state !== "succeeded" &&
              "border-accent/45 bg-accent/[.08] text-[#9db4ff]",
            !replay && state === "succeeded" && "border-pass/45 bg-pass/[.08] text-pass",
            !replay && isFailed && "border-reject/45 bg-reject/[.10] text-reject",
          )}
          title={
            replay
              ? "离线回放：本地按事件时间轴播放，不代表任何真实运行"
              : "来自 GET /api/runs/{run_id} 的 state 字段，前端不改写"
          }
        >
          <span className="opacity-70">{replay ? "mode" : "state"}</span>
          <span className="tracking-[.02em]">{replay ? "replay" : connected ? state : "connecting"}</span>
        </div>

        <div className="flex-1" />

        <div className="font-mono text-[10px] text-ink-muted">
          {replay
            ? `${events.length} 事件 · 离线回放（未连后端）`
            : !connected
              ? "等待首个响应"
              : terminal
                ? `${events.length} 事件 · 轮询已停（终态）`
                : `${events.length} 事件 · 1s 轮询`}
        </div>
        <button
          onClick={() => setPanel({ type: "report" })}
          className={clsx(
            "cursor-pointer rounded-[7px] border px-4 py-2 text-xs transition-colors",
            terminal
              ? state === "succeeded"
                ? "border-pass/50 bg-pass/[.10] text-pass hover:bg-pass/[.18]"
                : "border-reject/45 bg-reject/[.08] text-reject hover:bg-reject/[.16]"
              : "border-rule-hi bg-white/[.03] text-ink-mid hover:border-ink-muted hover:text-ink",
          )}
          title="report.md / patch.diff / result.json，可下载"
        >
          结果报告
        </button>
        <button
          onClick={() => setPanel({ type: "plan" })}
          className="cursor-pointer rounded-[7px] border border-rule-hi bg-white/[.03] px-4 py-2 text-xs text-ink-mid transition-colors hover:border-ink-muted hover:text-ink"
        >
          查看计划
        </button>
        {onBack && (
          <button
            onClick={onBack}
            title="回到首页，可以粘另一个仓库再跑一遍"
            className="cursor-pointer rounded-[7px] border border-accent/45 bg-accent/[.08] px-4 py-2 text-xs text-[#9db4ff] transition-colors hover:bg-accent/15 hover:text-white"
          >
            ← 换一篇
          </button>
        )}
        <button
          onClick={onCancel}
          disabled={replay || cancelling || ["succeeded", "failed", "cancelled"].includes(state)}
          className="cursor-pointer rounded-[7px] border border-reject/45 bg-white/[.03] px-4 py-2 text-xs text-reject transition-colors hover:bg-reject/10 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {cancelling ? "取消中…" : "取消"}
        </button>
      </header>

      {/* ── 终态横幅：明确告诉用户「完了，可以跑下一篇」 ── */}
      {terminal && onBack && (
        <motion.section
          initial={{ opacity: 0, height: 0 }}
          animate={{ opacity: 1, height: "auto" }}
          transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
          className="shrink-0 overflow-hidden border-b border-rule bg-gradient-to-r from-accent/[.10] to-transparent"
        >
          <div className="flex items-center gap-4 px-6 py-3">
            <span
              className={clsx(
                "font-mono text-[10px] tracking-[.16em] uppercase",
                isFailed ? "text-reject" : state === "succeeded" ? "text-pass" : "text-ink-muted",
              )}
            >
              {isFailed ? "已判定不可复现" : state === "succeeded" ? "复现完成" : "已取消"}
            </span>
            <span className="flex-1" />
            <button
              onClick={onBack}
              className="cursor-pointer rounded-[7px] border border-accent/50 bg-accent/[.12] px-4 py-1.5 text-[11.5px] text-[#bcd0ff] transition-colors hover:bg-accent/20 hover:text-white"
            >
              换个仓库再跑 →
            </button>
          </div>
        </motion.section>
      )}

      {/* ── Hero ── */}
      <section className="grid shrink-0 grid-cols-[1fr_auto] items-center gap-8 border-b border-rule px-6 pt-6 pb-5">
        <div className="min-w-0">
          <div className="mb-2 flex items-center gap-3.5">
            <motion.div
              className={clsx(
                "size-[11px] rounded-full",
                isFailed
                  ? "bg-reject shadow-[0_0_0_4px_rgb(255_92_71_/_0.16),0_0_22px_var(--color-reject)]"
                  : state === "succeeded"
                    ? "bg-pass shadow-[0_0_0_4px_rgb(46_230_168_/_0.16),0_0_22px_var(--color-pass)]"
                    : "bg-accent shadow-[0_0_0_4px_rgb(108_143_255_/_0.16),0_0_22px_var(--color-accent)]",
              )}
              animate={{ scale: [0.9, 1.1, 0.9] }}
              transition={{ duration: 1.8, repeat: Infinity, ease: "easeInOut" }}
            />
            <h2 className="text-[clamp(26px,3.1vw,40px)] leading-none font-semibold tracking-[-.028em]">
              {headline.text}{" "}
              <em
                className={clsx(
                  "not-italic",
                  isFailed ? "text-reject" : state === "succeeded" ? "text-pass" : "text-accent",
                )}
                style={{
                  textShadow: isFailed
                    ? "0 0 40px rgb(255 92 71 / .6)"
                    : state === "succeeded"
                      ? "0 0 40px rgb(46 230 168 / .6)"
                      : "0 0 40px rgb(108 143 255 / .6)",
                }}
              >
                {headline.accent}
              </em>
            </h2>
          </div>
          <p className="font-mono text-[11.5px] tracking-[.04em] text-ink-muted">
            {replay ? "离线回放 · 逐条事件时间轴" : "真实运行 · 状态与事件来自后端"}
            {" · "}
            <b className="font-medium text-warn">{blockedCount} 次拦截</b>
            {terminal ? (
              <>
                {" · 已结束 · 共 "}
                <b className="font-medium text-ink-mid">{events.length}</b> 条事件
              </>
            ) : activeStage ? (
              <>
                {" · 当前阶段 "}
                <b className="font-medium text-ink-mid">{activeStage.label}</b>
                {` 已持续 ${fmtSec(stageElapsedMs)}`}
              </>
            ) : null}
            {silent && !terminal && (
              <>
                {" · "}
                <b className="font-medium text-warn">
                  {state === "preparing"
                    ? "git clone 进行中，此阶段后端不写动作事件"
                    : state === "planning"
                      ? "正在生成运行计划，此阶段后端不写动作事件"
                      : "后端本阶段无新事件"}
                </b>
              </>
            )}
          </p>

          {/* 轮询断了要说清楚：否则界面停在最后一帧，看起来像任务卡住 */}
          {pollError && !replay && (
            <div className="mt-3 max-w-[74ch] rounded-[9px] border border-warn/35 bg-warn/[.06] px-3.5 py-2.5">
              <div className="font-mono text-[10px] tracking-[.16em] text-warn/90 uppercase">
                界面与后端的连接中断（§4.2 轮询已停）
              </div>
              <p className="mt-1 text-[12.5px] leading-relaxed text-ink-mid">
                最后一次同步停在 <b className="text-ink">{state}</b>，
                {events.length} 条事件。任务本身可能仍在后端执行，
                刷新页面会重新拉取，不是任务卡住。
                <span className="mt-1 block font-mono text-[11px] text-warn/80">{pollError}</span>
              </p>
            </div>
          )}

          {isFailed && (
            <div className="mt-3 max-w-[74ch] rounded-[9px] border border-reject/30 bg-reject/[.055] px-3.5 py-2.5">
              <div className="font-mono text-[10px] tracking-[.16em] text-reject/85 uppercase">
                失败原因 · {failureCategory || "未分类"}
              </div>
              <p className="mt-1 text-[12.5px] leading-relaxed text-ink">{failExplain.verdict}</p>
              <p className="mt-1.5 text-[11.5px] leading-relaxed text-ink-muted">
                {failExplain.detail}
              </p>
              {failExplain.note && (
                <p className="mt-1.5 border-l-2 border-warn/40 pl-2.5 text-[11.5px] leading-relaxed text-warn/85">
                  {failExplain.note}
                </p>
              )}
              {failExplain.stderr && (
                <div className="mt-2">
                  <div className="font-mono text-[10px] tracking-[.1em] text-reject/70">
                    stderr 原文 · {failExplain.stderrFrom}
                  </div>
                  <pre className="mt-1 max-h-36 overflow-auto rounded-md bg-black/35 p-2 font-mono text-[10.5px] leading-relaxed whitespace-pre-wrap break-all text-[rgb(255_180_170_/_0.9)]">
                    {failExplain.stderr}
                  </pre>
                </div>
              )}
              {failExplain.repoUrl && (
                <div className="mt-2 flex flex-wrap items-baseline gap-x-2 gap-y-1 font-mono text-[11px]">
                  <span className="text-ink-muted">提交地址</span>
                  <code className="break-all text-ink-mid">{failExplain.repoUrl}</code>
                </div>
              )}
              {failExplain.raw && (
                <details className="mt-2">
                  <summary className="cursor-pointer font-mono text-[10.5px] text-ink-muted transition-colors hover:text-ink-mid">
                    后端原始 message
                  </summary>
                  <pre className="mt-1.5 max-h-36 overflow-auto rounded-md bg-black/35 p-2 font-mono text-[10.5px] leading-relaxed whitespace-pre-wrap break-all text-ink-muted">
                    {failExplain.raw}
                  </pre>
                </details>
              )}
            </div>
          )}

          {/* 终态一定有三份文件可交付：报告 / 补丁 / 结果 JSON */}
          {terminal && (
            <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-2">
              <button
                onClick={() => setPanel({ type: "report" })}
                className={clsx(
                  "cursor-pointer rounded-[7px] border px-3.5 py-1.5 font-mono text-[11.5px] transition-colors",
                  state === "succeeded"
                    ? "border-pass/45 bg-pass/[.10] text-pass hover:bg-pass/[.18]"
                    : "border-reject/40 bg-reject/[.07] text-reject hover:bg-reject/[.14]",
                )}
              >
                打开结果报告 →
              </button>
              <span className="font-mono text-[10.5px] leading-relaxed text-ink-muted">
                report.md · patch.diff · result.json 三份文件，可预览、复制、下载
              </span>
            </div>
          )}
        </div>

        <div className="flex gap-9">
          <Stat value={reviewCount} label="ACI 审查" />
          <Stat value={blockedCount} label="拦截" tone="bad" />
          <Stat value={0} label="逃逸" tone="good" />
        </div>
      </section>

      {/* ── 整体进度：百分比 + 倒计时 + 分段条 ── */}
      <ProgressBand p={progress} state={state} />

      {/* ── 执行拓扑图：节点像墨迹铺开，回路像活物回转 ── */}
      <section className="relative h-[340px] shrink-0 overflow-hidden border-b border-rule">
        <div className="absolute top-3 left-6 z-[3] font-mono text-[9.5px] tracking-[.18em] text-ink-muted uppercase">
          执行拓扑
        </div>
        <div className="absolute top-3 right-6 z-[3] flex items-center gap-2 rounded-full border border-rule-hi bg-white/[.03] px-3 py-1 font-mono text-[9.5px] tracking-[.06em] text-ink-muted">
          点节点看事件
        </div>
        <FlowGraph
          state={state}
          stages={stages}
          loop={loop}
          rows={rows}
          onSelectStage={(id) => setPanel({ type: "stage", id })}
          onSelectGate={() => setPanel({ type: "loop" })}
        />
      </section>

      {/* ── 自愈回环：正在干什么 ── */}
      <LoopStrip
        loop={loop}
        state={state}
        onOpenAction={(seq) => setPanel({ type: "aci", seq })}
        onOpenLoop={() => setPanel({ type: "loop" })}
      />

      {/* ── 动作流 + 遥测 ── */}
      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[1fr_296px]">
        <div className="min-h-0 overflow-y-auto px-6 py-5">
          <div className="sticky top-0 z-[2] mb-1.5 flex items-center justify-between border-b border-rule bg-void pt-0.5 pb-3">
            <div className="flex gap-[3px]">
              <button className="cursor-pointer rounded-[5px] border border-rule-hi bg-surface-hi px-3.5 py-1.5 text-xs text-ink">
                动作流
              </button>
              <button
                onClick={() => setPanel({ type: "loop" })}
                className="cursor-pointer rounded-[5px] border border-transparent px-3.5 py-1.5 text-xs text-ink-muted transition-colors hover:text-ink-mid"
              >
                自愈回环 {loop.rounds.length > 0 && `×${loop.rounds.length}`}
              </button>
            </div>
            <div className="font-mono text-[10.5px] tracking-[.03em] text-ink-muted">
              {rows.length} 个动作 · ACI 审查 {reviewCount} 次 ·{" "}
              <b className="text-reject">拦截 {blockedCount} 次</b>
            </div>
          </div>

          {rows.length === 0 && (
            <div className="mt-10 text-center font-mono text-[11.5px] leading-relaxed text-ink-muted">
              {replay ? (
                "回放即将开始…（本地时间轴，逐条事件推进）"
              ) : silent ? (
                <>
                  <div className="text-warn">
                    已连接后端，但它在 <b>{state}</b> 阶段没写任何动作事件
                  </div>
                  <div className="mt-2 text-[11px] text-ink-muted">
                    该阶段只更新 state、不产生事件，已持续 {fmtSec(stageElapsedMs)}。
                    界面不替它编进度——下方 trace 由 GET /api/runs/{"{id}"}/events 增量拉取。
                  </div>
                </>
              ) : events.length > 0 ? (
                <>
                  <div className="text-ink-mid">
                    已收到 <b>{events.length}</b> 条事件，但都不是动作
                  </div>
                  <div className="mt-2 text-[11px] text-ink-muted">
                    {terminal
                      ? `本次运行在「${stageLabelOf(state)}」结束，除状态迁移外没有产生任何动作事件——` +
                        "所以这里没有能展开的 ACI 记录，不是界面漏了。要看失败原因，用上面的红色区块与结果报告。"
                      : `当前阶段（${stageLabelOf(state)}）只写状态迁移，模型还没动手。动作会在这之后逐条追加。`}
                  </div>
                </>
              ) : (
                "等待后端事件…（日志与动作由 1000ms 轮询增量拉取）"
              )}
            </div>
          )}

          <AnimatePresence initial={false}>
            {rows.map((r) => (
              <motion.button
                key={r.seq}
                onClick={() => setPanel({ type: "aci", seq: r.seq })}
                className="block w-full cursor-pointer text-left"
                initial={STILL ? false : { opacity: 0, x: -18 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ duration: 0.42, ease: [0.16, 1, 0.3, 1] }}
              >
                <ActionLine row={r} />
              </motion.button>
            ))}
          </AnimatePresence>

          {/* 动作少不等于卡住：把"还有多少条非动作事件"摆出来，省得以为是没跑 */}
          {rows.length > 0 && events.length > rows.length && (
            <div className="mt-3 flex flex-wrap items-baseline gap-x-2 gap-y-1 border-t border-white/[.05] pt-3 font-mono text-[10.5px] leading-relaxed text-ink-muted">
              <span>
                另有 <b className="text-ink-mid">{events.length - rows.length}</b> 条事件不是动作
              </span>
              <span className="text-ink-muted/60">
                （clone、计划解析、阶段迁移、熔断与收尾清理），可在上方阶段条与自愈回环里逐格查看——
                动作流本来就只收动作。
              </span>
            </div>
          )}
        </div>

        <aside className="min-h-0 overflow-y-auto border-l border-rule bg-gradient-to-b from-white/[.018] to-transparent p-5">
          <div className="mb-4 text-[10px] tracking-[.18em] text-ink-muted uppercase">
            沙箱遥测
          </div>
          <Telemetry status={status} state={state} />

          <div className="mt-3 rounded-[7px] border border-rule bg-white/[.015] px-3 py-2 font-mono text-[10px] leading-relaxed text-ink-muted">
            镜像名 / 挂载点在事件里没有，宁可不显示也不编。
          </div>

          <div className="mt-6 mb-3 flex items-center justify-between">
            <div className="text-[10px] tracking-[.18em] text-ink-muted uppercase">ACI 审查</div>
            <span className="font-mono text-[10px] text-ink-muted">点条目看流水线</span>
          </div>
          <div className="rounded-[8px] border border-rule bg-white/[.015]">
            {rows.filter((r) => r.name !== "install" && r.name !== "test").length === 0 && (
              <div className="px-3 py-2.5 font-mono text-[10.5px] text-ink-muted">
                还没有动作过门。
              </div>
            )}
            {rows
              .filter((r) => r.name !== "install" && r.name !== "test")
              .slice(-5)
              .reverse()
              .map((r) => (
                <button
                  key={r.seq}
                  onClick={() => setPanel({ type: "aci", seq: r.seq })}
                  className="flex w-full cursor-pointer items-baseline gap-2 border-b border-white/[.04] px-3 py-2 text-left last:border-b-0 hover:bg-white/[.04]"
                >
                  <span
                    className={clsx(
                      "w-2.5 shrink-0 text-center font-mono text-[10px]",
                      r.blocked ? "text-reject" : "text-pass",
                    )}
                  >
                    {r.blocked ? "✕" : "✓"}
                  </span>
                  <span
                    className="w-[92px] shrink-0 truncate font-mono text-[10.5px] text-ink-mid"
                    title={r.name}
                  >
                    {r.name.replace("_requirement", "").replace("_exact", "")}
                  </span>
                  <span
                    className={clsx(
                      "min-w-0 flex-1 truncate font-mono text-[10.5px]",
                      r.blocked ? "text-reject" : "text-ink-muted",
                    )}
                    title={r.target || r.result}
                  >
                    {r.target || r.result}
                  </span>
                </button>
              ))}
          </div>
        </aside>
      </div>

      {/* ── 抽屉 ── */}
      <Drawer
        open={panel !== null}
        wide={panel?.type === "report" || panel?.type === "plan"}
        title={drawerTitle(panel, openedEvent, stages, loop)}
        subtitle={drawerSubtitle(panel, openedEvent)}
        onClose={() => setPanel(null)}
      >
        {panel?.type === "aci" && openedEvent && isActionEvent(openedEvent) && (
          <AciPanel trace={aciTrace(openedEvent)} event={openedEvent} />
        )}
        {panel?.type === "loop" && (
          <LoopPanel
            loop={loop}
            state={state}
            onOpenAction={(seq) => setPanel({ type: "aci", seq })}
          />
        )}
        {panel?.type === "stage" && (() => {
          const stage = stages.find((s) => s.id === panel.id)!;
          return <StagePanel stage={stage} events={events} state={state} />;
        })()}
        {panel?.type === "plan" && <PlanPanel status={status} />}
        {panel?.type === "report" && <ReportPanel runId={runId} status={status} />}
      </Drawer>
    </div>
  );
}

function drawerTitle(
  panel: Panel,
  event: RunEvent | undefined,
  stages: StageView[],
  loop: ReturnType<typeof buildLoop>,
): string {
  if (panel?.type === "aci") return event ? `ACI 审查 · ${event.kind}` : "ACI 审查";
  if (panel?.type === "loop")
    return loop.rounds.length ? `自愈回环 · ${loop.rounds.length} 轮` : "自愈回环";
  if (panel?.type === "stage")
    return `阶段详情 · ${stages.find((s) => s.id === panel.id)?.label ?? panel.id}`;
  if (panel?.type === "report") return "结果报告";
  return "运行计划与结果";
}

function drawerSubtitle(panel: Panel, event: RunEvent | undefined): string | undefined {
  if (panel?.type === "aci" && event)
    return isBlockedEvent(event) ? "校验失败：零副作用拦截" : "校验通过：动作已执行";
  if (panel?.type === "stage") return "该阶段全部事件（后端原样）";
  if (panel?.type === "report") return "report.md · patch.diff · result.json（后端落盘原文）";
  return undefined;
}

function Stat({ value, label, tone }: { value: number; label: string; tone?: "bad" | "good" }) {
  return (
    <div className="text-right">
      <motion.div
        key={value}
        initial={STILL ? false : { opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.45, ease: [0.16, 1, 0.3, 1] }}
        className={clsx(
          "font-mono text-[clamp(26px,3vw,36px)] leading-none font-semibold tracking-[-.03em] tabular-nums",
          tone === "bad" && "text-reject [text-shadow:0_0_30px_rgb(255_92_71_/_0.4)]",
          tone === "good" && "text-pass [text-shadow:0_0_30px_rgb(46_230_168_/_0.35)]",
        )}
      >
        {String(value).padStart(2, "0")}
      </motion.div>
      <div className="mt-2.5 text-[10px] tracking-[.16em] text-ink-muted uppercase">{label}</div>
    </div>
  );
}

function ActionLine({ row }: { row: ActionRow }) {
  const sym =
    row.kind === "reject" ? "✕" : row.kind === "finish" ? "⊘" : row.kind === "ok" ? "✓" : "⟩";

  return (
    <>
      <motion.div
        whileHover={{ x: 3, backgroundColor: "rgb(255 255 255 / .04)" }}
        transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
        className={clsx(
          // 列宽按"时间 图标 动作 参数 结果"分配：结果列用 max-content，
          // 宁可挤参数列也不截断结论（`20 matches` 截成 `20 ma…` 就没信息了）
          "relative -mx-[13px] grid grid-cols-[62px_15px_146px_minmax(0,1fr)_minmax(104px,max-content)] items-center gap-x-[10px] rounded-[5px] border-b border-white/[.045] px-[13px] py-[8px] font-mono text-[12px] last:border-b-0",
          row.blocked &&
            "border-b-reject/15 bg-gradient-to-r from-reject/[.11] to-reject/[.02] pl-[15px] shadow-[inset_2px_0_0_var(--color-reject)] hover:from-reject/[.18] hover:to-reject/[.04]",
        )}
      >
        <span className="text-[10.5px] tabular-nums text-ink-muted/85">{row.ts}</span>
        <span
          className={clsx(
            "text-center text-[12px]",
            row.kind === "reject" ? "font-bold text-reject" : "text-ink-muted/70",
          )}
        >
          {sym}
        </span>
        <span className="truncate text-[11.5px] text-ink" title={row.name}>
          {row.name}
        </span>
        <span
          className={clsx(
            "truncate text-[11.5px]",
            row.blocked ? "text-[rgb(255_180_170_/_0.9)]" : "text-ink-mid",
          )}
          title={row.target}
        >
          {row.target}
        </span>
        <span
          className={clsx(
            "max-w-[300px] justify-self-end truncate text-right text-[11.5px] tabular-nums",
            row.kind === "reject" && "font-semibold text-reject",
            row.kind === "ok" && "text-pass",
            row.kind === "call" && "text-ink-muted",
          )}
          title={row.result}
        >
          {row.result}
        </span>
      </motion.div>

      {row.reason && (
        <div className="mb-0.5 -mx-[13px] flex items-baseline gap-2.5 rounded-b-md px-[13px] pt-1 pb-2 pl-[87px] font-mono text-[10.5px] leading-relaxed text-[rgb(255_140_128_/_0.9)]">
          <span className="text-reject/50">└</span>
          <span>{row.reason}</span>
        </div>
      )}
    </>
  );
}

function Telemetry({ status, state }: { status: RunStatus | null; state: RunState }) {
  const online = state === "installing";
  const shot = status?.result;
  /**
   * 只写有观测依据的值。镜像名和挂载点是 docker 层的，事件契约里没有，
   * 之前这里硬编码 "base-py3.11" / "/workspace rw"，等于凭空造事实。
   */
  const items: { k: string; v: string; tone?: "on" | "off" | "warn" }[] = [
    { k: "容器", v: status?.run_id ? `harness-${status.run_id.slice(0, 6)}` : "—" },
    { k: "镜像", v: "—" },
    { k: "挂载", v: "—" },
    { k: "网络", v: online ? "已连接（装依赖）" : "已断开", tone: online ? "on" : "off" },
    { k: "尝试次数", v: shot ? String(shot.attempts) : "—" },
    { k: "结果", v: shot ? `${shot.outcome}${shot.verified ? " · verified" : ""}` : "—" },
  ];

  return (
    <>
      {items.map((it) => (
        <div
          key={it.k}
          className="flex items-baseline justify-between gap-2.5 border-b border-white/[.045] py-2 transition-all hover:bg-white/[.03] hover:pl-1.5"
        >
          <span className="shrink-0 text-[11px] text-ink-muted">{it.k}</span>
          <span
            className={clsx(
              "truncate text-right font-mono text-[11px] tabular-nums",
              it.tone === "on" ? "text-pass" : it.tone === "warn" ? "text-warn" : "text-ink-mid",
            )}
          >
            {it.v}
          </span>
        </div>
      ))}
    </>
  );
}

function fmtSec(ms: number): string {
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return `${m}m${String(s % 60).padStart(2, "0")}s`;
}

function stageLabelOf(state: RunState): string {
  const map: Record<string, string> = {
    queued: "排队中",
    preparing: "准备环境",
    planning: "生成计划",
    installing: "安装依赖",
    testing: "运行测试",
    diagnosing: "诊断失败",
    repairing: "修复中",
    succeeded: "复现成功",
    failed: "复现失败",
    cancelled: "已取消",
  };
  return map[state] ?? state;
}
