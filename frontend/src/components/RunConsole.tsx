import clsx from "clsx";
import { useEffect, useMemo, useState } from "react";
import { TERMINAL, type RunEvent, type RunStatus } from "../types";
import { buildLoop, buildProgress, buildRows, buildStages, bundleConclusion, isActionEvent, isBlockedEvent, overallConclusion, replayConclusion, verificationConclusion } from "../runModel";
import { FlowGraph } from "./FlowGraph";
import { ActionRowItem } from "./ActionRowItem";
import { ProgressBand } from "./ProgressBand";
import { VerificationPanel } from "./VerificationPanel";
import { ReplayPanel } from "./ReplayPanel";
import { ReportPanel } from "./ReportPanel";
import { Drawer } from "./Drawer";
import { PlanPanel } from "./DrawerPanels";
import { parsePanel } from "../deepLink";
import { controlClass, EventCard, explanationOf, guidanceOf, RawText, STATE_COPY, TechnicalEvent, type Panel } from "./RunSceneParts";

type ConsolePanel = Panel | { type: "verification" } | { type: "replay" };

export function RunConsole({ mode, status, events, onCancel, cancelling, pollError, pollRetrying, cancelError, targetName, onBack, onShowResult }: {
  mode: "live" | "replay"; status: RunStatus | null; events: RunEvent[];
  onCancel?: () => void; cancelling?: boolean; pollError?: string | null; pollRetrying?: boolean;
  cancelError?: string | null; targetName?: string; onBack?: () => void; onShowResult?: () => void;
}) {
  const [panel, setPanel] = useState<ConsolePanel>(() => {
    const p = parsePanel(new URLSearchParams(window.location.search));
    if (p?.type === "event") return { type: "event", seq: p.seq };
    if (p?.type === "report") return { type: "report" };
    if (p?.type === "technical") return { type: "technical" };
    return null;
  });
  const [tab, setTab] = useState<"actions" | "history">("actions");
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [heights, setHeights] = useState<Record<number, number>>({});
  const [now, setNow] = useState(Date.now);
  const demo = mode === "replay", state = status?.state ?? "queued";
  const terminal = TERMINAL.has(state), result = status?.result ?? null;
  useEffect(() => {
    if (terminal) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [terminal]);
  const stages = useMemo(() => buildStages(events, status?.state), [events, status?.state]);
  const loop = useMemo(() => buildLoop(events, status?.state), [events, status?.state]);
  const rows = useMemo(() => buildRows(events), [events]);
  const progress = useMemo(() => { void now; return buildProgress(events, stages, state); }, [events, stages, state, now]);
  const overall = overallConclusion(status, demo), guidance = guidanceOf(status, demo);
  const actions = events.filter(isActionEvent), blocked = events.filter(isBlockedEvent);
  const active = stages.find(s => s.status === "active");
  const headline = terminal || state === "replaying" ? overall.label : guidance.title;
  const currentCheck = verificationConclusion(result?.verification);
  const replayCheck = replayConclusion(result?.replay), material = bundleConclusion(result?.bundle);
  const openedEvent = panel?.type === "event" ? events.find(e => e.sequence === panel.seq) : null;
  const openedStage = panel?.type === "stage" ? stages.find(s => s.id === panel.id) : null;
  const title = panel?.type === "report" ? "结果与文件" : panel?.type === "verification" ? "独立验证证据"
    : panel?.type === "replay" ? "干净复跑与材料" : openedStage?.label ?? "技术详情";

  return (
    <div className="run-workspace run-console flex h-full min-w-0 flex-col">
      <header className="console-header flex shrink-0 flex-wrap items-center gap-3 border-b border-rule px-5 py-3">
        <span className="flex items-center gap-2 text-sm font-semibold tracking-wide"><i className="size-2 rounded-full bg-accent shadow-[0_0_12px_var(--color-accent)]" />PaperHarness</span>
        <span className="hidden max-w-52 truncate font-mono text-[11px] text-ink-muted sm:block">{targetName || (status?.run_id ? `run_${status.run_id.slice(0, 8)}` : "等待任务")}</span>
        <span className={clsx("max-w-full rounded border px-2 py-1 text-xs", toneClass(overall.tone))}>{demo ? "示例回放" : terminal ? overall.label : active?.label ?? "连接中"}</span>
        <nav className="ml-auto flex flex-wrap gap-2" aria-label="运行操作">
          <button onClick={() => setPanel({ type: "technical" })} className={controlClass}>技术详情</button>
          <button onClick={() => setPanel({ type: "report" })} className={controlClass}>结果与文件</button>
          {terminal && result && onShowResult && <button onClick={onShowResult} className={`${controlClass} border-accent/60 text-accent`}>查看完整结果</button>}
          {onBack && <button onClick={onBack} className={controlClass}>返回</button>}
          {!terminal && !demo && <button onClick={onCancel} disabled={!status || cancelling} className={`${controlClass} border-reject/40 text-reject`}>{cancelling ? "正在取消…" : "取消任务"}</button>}
        </nav>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {demo && <p className="border-b border-warn/25 bg-warn/5 px-6 py-2 text-xs leading-6 text-warn">示例回放：预设事件仅演示界面，不代表真实执行、验证或复跑结果，不提供真实文件。</p>}
        {pollError && !demo && <p role="alert" className="border-b border-warn/30 bg-warn/5 px-6 py-3 text-sm text-warn">{pollRetrying ? "连接暂时中断，正在重试；当前保留最后收到的记录。" : pollError}</p>}
        {cancelError && <p role="alert" className="px-6 py-3 text-sm text-reject">{cancelError}</p>}
        <section className="console-hero flex flex-wrap items-center justify-between gap-5 border-b border-rule px-6 py-6 sm:px-8">
          <div className="w-full min-w-0 sm:w-auto sm:flex-1">
            <h1 className={clsx("max-w-[32ch] text-[clamp(24px,3vw,40px)] leading-tight font-semibold tracking-tight", terminal && toneClass(overall.tone, true))}>{headline}</h1>
            <p className="mt-3 max-w-[80ch] text-xs leading-6 text-ink-mid">{terminal ? overall.detail : guidance.detail}</p>
            {overall.gaps.length > 0 && <p className="mt-2 text-xs leading-6 text-warn">{overall.gaps.join("；")}</p>}
          </div>
          <div className="flex w-full justify-between gap-7 font-mono tabular-nums sm:w-auto sm:justify-start">
            <Counter value={loop.rounds.length} label="修复轮次" />
            <Counter value={actions.length} label="动作审查" />
            <Counter value={blocked.length} label="安全拦截" reject />
          </div>
        </section>
        <ProgressBand p={progress} state={state} stageLabel={active?.label ?? (terminal ? "运行结束" : "等待进展")} />
        <section aria-label="执行拓扑" className="border-b border-rule">
          <div className="flex flex-wrap items-baseline justify-between gap-2 px-6 pt-4 text-xs">
            <h2 className="font-medium text-ink">执行拓扑</h2>
            <span className="text-ink-muted">点击节点查看依据 · 修复回环来自实际事件 · 验证与复跑独立判定</span>
          </div>
          <div className="overflow-x-auto" tabIndex={0} aria-label="可横向滚动的流程图">
            <div className="h-[360px] min-w-[960px] lg:h-[400px]">
              <FlowGraph state={state} stages={stages} loop={loop} rows={rows} events={events} result={demo ? null : result}
                onSelectStage={id => setPanel(id === "verifying" ? { type: "verification" } : id === "replaying" ? { type: "replay" } : id === "succeeded" ? { type: "report" } : { type: "stage", id })}
                onSelectGate={() => setPanel({ type: "stage", id: "repairing" })}
                onSelectStep={seq => setPanel({ type: "event", seq })} />
            </div>
          </div>
        </section>
        <div className="grid min-w-0 lg:grid-cols-[minmax(0,1fr)_290px]">
          <section className="min-w-0 px-6 py-5" aria-label="执行记录">
            <div className="mb-4 flex flex-wrap items-center gap-3 border-b border-rule pb-3">
              <button aria-pressed={tab === "actions"} onClick={() => setTab("actions")} className={`${controlClass} ${tab === "actions" ? "border-accent/50 bg-accent/10 text-ink" : ""}`}>动作流</button>
              <button aria-pressed={tab === "history"} onClick={() => setTab("history")} className={`${controlClass} ${tab === "history" ? "border-accent/50 bg-accent/10 text-ink" : ""}`}>运行经过</button>
              <span className="ml-auto text-xs text-ink-muted">{events.length} 条记录 · {actions.length} 个动作</span>
            </div>
            {tab === "actions" ? rows.length > 0 ? (
              <div className="overflow-x-auto px-3.5">
                <div className="min-w-[740px]">
                  {rows.map(row => <ActionRowItem key={row.seq} row={row} event={events.find(e => e.sequence === row.seq)}
                    expanded={expanded.has(row.seq)} height={heights[row.seq] ?? null}
                    onToggle={() => setExpanded(previous => { const next = new Set(previous); if (next.has(row.seq)) next.delete(row.seq); else next.add(row.seq); return next; })}
                    onResize={height => setHeights(previous => ({ ...previous, [row.seq]: height }))}
                    onOpenDetail={() => setPanel({ type: "event", seq: row.seq })} />)}
                </div>
              </div>
            ) : <p className="py-5 text-sm leading-7 text-ink-mid">尚无安装、测试或修复动作。{events.length > 0 ? "可切换到运行经过查看准备阶段。" : "任务开始后会显示真实记录。"}</p>
            : <ol>{events.map((event, index) => <EventCard key={event.sequence} event={event} index={index} onOpen={seq => setPanel({ type: "event", seq })} />)}</ol>}
            {rows.length >= 80 && <p className="mt-3 text-xs text-ink-muted">动作流展示最近 80 条；完整事件保留在运行经过中。</p>}
          </section>
          <aside aria-label="验证与交付" className="console-evidence min-w-0 border-t border-rule px-6 py-5 lg:border-t-0 lg:border-l">
            <h2 className="mb-4 text-sm font-medium text-ink">验证与交付</h2>
            <Evidence label="当前环境" text={demo ? "示例不提供真实证据" : currentCheck.label} tone={demo ? "neutral" : currentCheck.tone} onClick={() => setPanel({ type: "verification" })} />
            <Evidence label="干净复跑" text={demo ? "示例不提供真实复跑" : replayCheck.label} tone={demo ? "neutral" : replayCheck.tone} onClick={() => setPanel({ type: "replay" })} />
            <Evidence label="材料交付" text={demo ? "示例不提供真实文件" : material.label} tone={demo ? "neutral" : material.tone} onClick={() => setPanel({ type: "replay" })} />
            <p className="mt-5 text-xs leading-6 text-ink-muted">执行通过、指标达标、干净复跑和材料完整性分别记录。通过仅针对本次任务与配置。</p>
          </aside>
        </div>
      </div>
      <Drawer open={panel !== null} wide title={title} onClose={() => setPanel(null)}>
        {panel?.type === "event" && (openedEvent ? <TechnicalEvent event={openedEvent} /> : <p className="text-sm text-ink-mid">尚未收到这一步的记录。</p>)}
        {panel?.type === "stage" && openedStage && <div className="space-y-5">
          <p className="text-sm leading-7 text-ink-mid">{STATE_COPY[openedStage.id].detail}</p>
          {events.filter(e => e.state === openedStage.id).map(event => <details key={event.sequence} className="border-t border-rule pt-4"><summary className="cursor-pointer text-sm text-ink">{explanationOf(event).title}</summary><div className="mt-4"><TechnicalEvent event={event} /></div></details>)}
        </div>}
        {panel?.type === "verification" && <VerificationPanel verification={result?.verification} replay={demo} runId={status?.run_id ?? null} />}
        {panel?.type === "replay" && <ReplayPanel result={result} runId={status?.run_id ?? null} isDemo={demo} />}
        {panel?.type === "technical" && <div className="space-y-6"><PlanPanel status={status} events={events} /><RawText text={JSON.stringify(status, null, 2)} />
          {events.map(event => <details key={event.sequence} className="border-t border-rule py-3"><summary className="cursor-pointer text-sm text-ink-mid">#{event.sequence} {explanationOf(event).title}</summary><div className="mt-4"><TechnicalEvent event={event} /></div></details>)}</div>}
        {panel?.type === "report" && (demo ? <p className="text-sm leading-7 text-ink-mid">示例回放没有真实报告、复跑记录或材料包，不提供下载。</p> : <ReportPanel runId={status?.run_id ?? null} status={status} />)}
      </Drawer>
    </div>
  );
}

function toneClass(tone: string, textOnly = false) {
  return tone === "success" ? `${textOnly ? "" : "border-pass/40 "}text-pass` : tone === "error" ? `${textOnly ? "" : "border-reject/40 "}text-reject`
    : tone === "warning" ? `${textOnly ? "" : "border-warn/40 "}text-warn` : `${textOnly ? "" : "border-rule-hi "}text-ink`;
}
function Counter({ value, label, reject }: { value: number; label: string; reject?: boolean }) {
  return <div><p className={clsx("text-3xl font-semibold", reject ? "text-reject" : "text-ink")}>{String(value).padStart(2, "0")}</p><p className="mt-2 text-[10px] text-ink-muted">{label}</p></div>;
}
function Evidence({ label, text, tone, onClick }: { label: string; text: string; tone: string; onClick: () => void }) {
  return <button type="button" onClick={onClick} className="mb-3 block w-full cursor-pointer rounded-lg border border-rule bg-white/[.02] px-4 py-3 text-left transition-colors hover:border-rule-hi"><span className="text-[11px] text-ink-muted">{label}</span><span className={clsx("mt-2 block text-sm leading-6", toneClass(tone, true))}>{text}</span></button>;
}
