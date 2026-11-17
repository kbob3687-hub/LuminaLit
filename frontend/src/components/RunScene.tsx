import { AnimatePresence, motion } from "motion/react";
import clsx from "clsx";
import { useMemo, useState } from "react";
import { labelOf, type RunEvent, type RunState, type RunStatus } from "../types";
import {
  aciTrace,
  buildLoop,
  buildRows,
  buildStages,
  isActionEvent,
  isBlockedEvent,
  type ActionRow,
  type StageView,
} from "../runModel";
import { StageTrack, dur } from "./StageTrack";
import { LoopStrip } from "./LoopStrip";
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
}

export function RunScene({ mode, status, events, onCancel, cancelling }: Props) {
  const [panel, setPanel] = useState<Panel>(() => {
    // 演示/截图直链：?panel=aci&seq=7 / ?panel=loop（只读，不影响业务逻辑）
    const q = new URLSearchParams(window.location.search);
    const which = q.get("panel");
    if (which === "aci") return { type: "aci", seq: Number(q.get("seq") ?? 0) };
    if (which === "loop") return { type: "loop" };
    if (which === "plan") return { type: "plan" };
    return null;
  });
  const state: RunState = status?.state ?? "queued";
  const replay = mode === "replay";
  /** 有没有"听到"后端说话。回放模式下本地时间轴就是数据源，恒为真。 */
  const connected = replay || status !== null;

  const stages = useMemo(() => buildStages(events, status?.state), [events, status?.state]);
  const loop = useMemo(() => buildLoop(events, status?.state), [events, status?.state]);
  const rows = useMemo(() => buildRows(events), [events]);

  const blockedCount = rows.filter((r) => r.blocked).length;
  const reviewCount = rows.filter((r) => r.name !== "install" && r.name !== "test").length;
  const isFailed = state === "failed";

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
            : connected
              ? `${events.length} 事件 · 1s 轮询`
              : "等待首个响应"}
        </div>
        <button
          onClick={() => setPanel({ type: "plan" })}
          className="cursor-pointer rounded-[7px] border border-rule-hi bg-white/[.03] px-4 py-2 text-xs text-ink-mid transition-colors hover:border-ink-muted hover:text-ink"
        >
          查看计划
        </button>
        <button
          onClick={onCancel}
          disabled={replay || cancelling || ["succeeded", "failed", "cancelled"].includes(state)}
          className="cursor-pointer rounded-[7px] border border-reject/45 bg-white/[.03] px-4 py-2 text-xs text-reject transition-colors hover:bg-reject/10 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {cancelling ? "取消中…" : "取消"}
        </button>
      </header>

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
            {activeStage && (
              <>
                {" · 当前阶段 "}
                <b className="font-medium text-ink-mid">{activeStage.label}</b>
                {dur(activeStage.durationMs) && ` ${dur(activeStage.durationMs)}`}
              </>
            )}
          </p>
        </div>

        <div className="flex gap-9">
          <Stat value={reviewCount} label="ACI 审查" />
          <Stat value={blockedCount} label="拦截" tone="bad" />
          <Stat value={0} label="逃逸" tone="good" />
        </div>
      </section>

      {/* ── 3D 阶段轨道 ── */}
      <section className="relative shrink-0 overflow-hidden border-b border-rule px-6 pt-7 pb-6 [perspective:1400px] [perspective-origin:50%_42%]">
        <div className="absolute top-4 right-6 z-[3] flex items-center gap-2 rounded-full border border-rule-hi bg-white/[.03] px-3 py-1.5 font-mono text-[10.5px] tracking-[.06em] text-ink-muted">
          点阶段看事件
        </div>
        <StageTrack stages={stages} onSelect={(id) => setPanel({ type: "stage", id })} />
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
            <div className="mt-10 text-center font-mono text-[11.5px] text-ink-muted">
              {replay
                ? "回放即将开始…（本地时间轴，逐条事件推进）"
                : "等待后端事件…（日志与动作由 1000ms 轮询增量拉取）"}
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
                  <span className="w-[74px] shrink-0 truncate font-mono text-[10.5px] text-ink-mid">
                    {r.name.replace("_requirement", "").replace("_exact", "")}
                  </span>
                  <span
                    className={clsx(
                      "min-w-0 flex-1 truncate font-mono text-[10.5px]",
                      r.blocked ? "text-reject" : "text-ink-muted",
                    )}
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
  return "运行计划与结果";
}

function drawerSubtitle(panel: Panel, event: RunEvent | undefined): string | undefined {
  if (panel?.type === "aci" && event)
    return isBlockedEvent(event) ? "校验失败：零副作用拦截" : "校验通过：动作已执行";
  if (panel?.type === "stage") return "该阶段全部事件（后端原样）";
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
        whileHover={{ x: 4, backgroundColor: "rgb(255 255 255 / .045)" }}
        transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
        className={clsx(
          "relative -mx-[13px] mb-0.5 grid grid-cols-[74px_20px_166px_minmax(0,1fr)_116px] items-center gap-[11px] rounded-md px-[13px] py-[9px] font-mono text-[12.5px]",
          row.blocked &&
            "-ml-[13px] bg-gradient-to-r from-reject/[.11] to-reject/[.03] pl-[15px] shadow-[inset_2px_0_0_var(--color-reject)] hover:from-reject/[.19] hover:to-reject/[.05]",
        )}
      >
        <span className="text-[11px] tabular-nums text-ink-muted">{row.ts}</span>
        <span
          className={clsx(
            "text-center text-[13px]",
            row.kind === "reject" ? "font-bold text-reject" : "text-ink-muted",
          )}
        >
          {sym}
        </span>
        <span className="truncate text-ink">{row.name}</span>
        <span
          className={clsx(
            "truncate text-xs",
            row.blocked ? "text-[rgb(255_180_170_/_0.9)]" : "text-ink-mid",
          )}
        >
          {row.target}
        </span>
        <span
          className={clsx(
            "truncate text-right text-[11.5px]",
            row.kind === "reject" && "font-semibold text-reject",
            row.kind === "ok" && "text-pass",
            row.kind === "call" && "text-ink-muted",
          )}
        >
          {row.result}
        </span>
      </motion.div>

      {row.reason && (
        <div className="mb-0.5 -mx-[13px] flex items-baseline gap-2.5 rounded-b-md px-[13px] pt-1.5 pb-2.5 pl-10 font-mono text-[11px] leading-relaxed text-[rgb(255_140_128_/_0.9)]">
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
