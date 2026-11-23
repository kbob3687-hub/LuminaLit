import clsx from "clsx";
import { useMemo, useState } from "react";
import { TERMINAL, type RunEvent, type RunStatus } from "../types";
import { buildLoop, buildStages, overallConclusion } from "../runModel";
import { VerificationPanel } from "./VerificationPanel";
import { ReplayPanel } from "./ReplayPanel";
import { StageTrack } from "./StageTrack";
import { ReportPanel } from "./ReportPanel";
import { Drawer } from "./Drawer";
import { PlanPanel } from "./DrawerPanels";
import { parsePanel } from "../deepLink";
import {
  controlClass, EventCard, explanationOf, guidanceOf, RawText,
  RunOverview, STATE_COPY, TechnicalEvent, type Panel,
} from "./RunSceneParts";

export function RunScene({ mode, status, events, onCancel, cancelling, pollError, pollRetrying, cancelError, targetName, onBack }: {
  mode: "live" | "replay";
  status: RunStatus | null;
  events: RunEvent[];
  onCancel?: () => void;
  cancelling?: boolean;
  pollError?: string | null;
  pollRetrying?: boolean;
  cancelError?: string | null;
  targetName?: string;
  onBack?: () => void;
}) {
  const [panel, setPanel] = useState<Panel>(() => {
    // 深链走白名单：非法 panel / seq 回退，不打开一个空抽屉。
    const parsed = parsePanel(new URLSearchParams(window.location.search));
    if (!parsed) return null;
    if (parsed.type === "report") return { type: "report" };
    if (parsed.type === "technical") return { type: "technical" };
    if (parsed.type === "event" && parsed.seq > 0) return { type: "event", seq: parsed.seq };
    return null;
  });
  const replay = mode === "replay";
  const state = status?.state ?? "queued";
  const terminal = Boolean(status && TERMINAL.has(state));
  const guidance = guidanceOf(status, replay);
  const stages = useMemo(() => buildStages(events, status?.state), [events, status?.state]);
  const loop = useMemo(() => buildLoop(events, status?.state), [events, status?.state]);
  const openedEvent = panel?.type === "event" ? events.find((event) => event.sequence === panel.seq) : undefined;
  const openedStage = panel?.type === "stage" ? stages.find((stage) => stage.id === panel.id) : undefined;
  const finalReady = terminal && Boolean(status?.result);
  const verification = status?.result?.verification;
  // 顶部结论只由这一处产出：当前验收 + 干净复跑 + 结束原因（评审稿 §3.3）。
  // 不再各自用 verified / verification.status / state 拼一套「成功」。
  const overall = overallConclusion(status, replay);
  const badge = { label: overall.label, tone: overall.tone };
  const stageEvents = openedStage ? events.filter((event) => event.state === openedStage.id) : [];
  const title = panel?.type === "report" ? "结果与文件"
    : panel?.type === "event" ? "这一步的技术详情"
      : openedStage ? openedStage.label : "技术详情";

  return (
    <div className="run-workspace flex h-full flex-col bg-void">
      <header className="flex shrink-0 flex-wrap items-center gap-2 border-b border-rule bg-surface px-4 py-3 sm:gap-3 sm:px-6">
        {onBack && <button onClick={onBack} className={controlClass} title={terminal ? "返回任务选择" : "返回选择页，不会取消正在运行的任务"}>返回</button>}
        <span className="text-base font-semibold text-ink">PaperHarness</span>
        <span className="hidden max-w-52 truncate text-sm text-ink-mid sm:block" title={targetName}>{targetName ?? "当前任务"}</span>
        <span className={clsx("rounded-full border px-2.5 py-1 text-xs", badge.tone === "warning" ? "border-warn/40 text-warn" : badge.tone === "success" ? "border-pass/40 text-pass" : badge.tone === "error" ? "border-reject/40 text-reject" : "border-rule-hi text-ink-mid")}>
          {badge.label}
        </span>
        <div className="ml-auto flex flex-wrap gap-2">
          <button onClick={() => setPanel({ type: "technical" })} className={controlClass}>技术详情</button>
          <button onClick={() => setPanel({ type: "report" })} className={clsx(controlClass, finalReady && "border-accent/50 bg-accent/10 text-ink")}>结果与文件</button>
          {!terminal && !replay && <button onClick={onCancel} disabled={!status || cancelling} className={controlClass}>{cancelling ? "正在取消…" : "取消任务"}</button>}
        </div>
      </header>

      {replay && <p className="shrink-0 border-b border-warn/20 bg-warn/5 px-6 py-2 text-sm leading-6 text-warn">正在播放预设示例，不代表本机执行情况，也不会生成真实报告。</p>}
      {pollError && !replay && (
        <div role="alert" className="shrink-0 border-b border-warn/30 bg-warn/5 px-6 py-3 text-sm leading-6 text-warn">
          {pollRetrying ? "进展同步暂时中断，正在尝试重新连接。当前显示的是最后收到的记录，不代表任务已经失败。" : pollError}
          {pollRetrying && <details className="mt-1"><summary className="cursor-pointer text-xs">连接详情</summary><p className="mt-1 break-all">{pollError}</p></details>}
        </div>
      )}

      {cancelError && <p role="alert" className="border-b border-warn/30 bg-warn/5 px-6 py-3 text-sm leading-6 text-warn">{cancelError}</p>}

      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto lg:grid lg:grid-cols-[250px_minmax(0,1fr)] lg:overflow-hidden">
        <aside className="order-2 shrink-0 border-b border-rule bg-surface/40 p-4 lg:order-none lg:overflow-y-auto lg:border-r lg:border-b-0 lg:px-5 lg:py-7">
          <details open className="run-stage-list">
            <summary className="mb-4 cursor-pointer text-sm font-semibold text-ink">执行步骤</summary>
            <StageTrack stages={stages} loop={loop} onSelect={(id) => setPanel({ type: "stage", id })} />
          </details>
          <p className="mt-5 text-xs leading-6 text-ink-muted">只有运行检查未通过时，才需要分析原因和尝试修复。科学指标核验是独立检查，不会触发另一轮修复。未开始的步骤不代表失败。</p>
        </aside>

        <main className="order-1 min-w-0 shrink-0 px-5 py-7 sm:px-8 lg:order-none lg:overflow-y-auto lg:px-10">
          <div className="mx-auto max-w-[920px]">
            {(replay || finalReady) && (
              <section aria-label="本次结论" className="mb-6 rounded-xl border border-rule bg-surface/40 px-5 py-4">
                <h2 className={clsx("text-lg font-semibold", {
                  "text-pass": overall.tone === "success", "text-reject": overall.tone === "error",
                  "text-warn": overall.tone === "warning", "text-ink": overall.tone === "neutral",
                })}>{overall.label}</h2>
                <p className="mt-2 max-w-[70ch] text-sm leading-7 text-ink-mid">{overall.detail}</p>
                {overall.gaps.length > 0 && (
                  <div className="mt-3 border-t border-rule pt-3">
                    <p className="text-xs font-medium text-warn">本次仍未验证 / 未记录</p>
                    <ul className="mt-1 list-disc space-y-1 pl-4 text-xs leading-6 text-ink-mid">
                      {overall.gaps.map((gap, i) => <li key={`${gap}:${i}`}>{gap}</li>)}
                    </ul>
                  </div>
                )}
              </section>
            )}
            <RunOverview guidance={guidance} terminal={finalReady} onBack={onBack}
              onDetails={() => setPanel({ type: "technical" })} onReport={() => setPanel({ type: "report" })} />
            {(replay || finalReady) && <VerificationPanel verification={verification} replay={replay}
              runId={status?.run_id === status?.result?.run_id ? status?.run_id ?? null : null} />}
            {(replay || finalReady) && <ReplayPanel result={status?.result ?? null}
              runId={status?.run_id ?? null} isDemo={replay} />}
            <section aria-label="运行经过" className="pt-7">
              <div className="mb-6 flex items-baseline justify-between gap-4">
                <h2 className="text-base font-semibold text-ink">运行经过</h2>
                <span className="text-xs text-ink-muted">{terminal ? "已记录的实际步骤" : "有新进展时自动更新"}</span>
              </div>
              {events.length > 0 ? (
                <ol>{events.map((event, index) => <EventCard key={event.sequence} event={event} index={index} onOpen={(seq) => setPanel({ type: "event", seq })} />)}</ol>
              ) : (
                <p className="rounded-lg border border-dashed border-rule-hi px-5 py-6 text-sm leading-7 text-ink-mid">
                  {!status ? "连接后即可查看进展，无需重复提交任务。" : terminal ? "这次任务没有保存步骤记录，请先查看上方结论和结果文件。" : "尚无已完成的步骤。任务开始后，下载代码、准备环境和运行结果会依次出现在这里。"}
                </p>
              )}
              {status && !terminal && events.length > 0 && (
                <div className="mt-6 border-l-2 border-accent/60 pl-4 text-sm leading-6 text-ink-mid">
                  <p className="font-medium text-ink">{guidance.title}</p>
                  <p className="mt-1">该步骤结束后会更新结果，暂时没有新记录不代表卡住。</p>
                </div>
              )}
            </section>
          </div>
        </main>
      </div>

      <Drawer open={panel !== null} wide title={title} onClose={() => setPanel(null)}>
        {panel?.type === "event" && (openedEvent ? <TechnicalEvent event={openedEvent} /> : <p className="text-sm text-ink-mid">还没有收到这一步的记录。</p>)}
        {panel?.type === "stage" && openedStage && (
          <div className="space-y-5">
            <p className="text-sm leading-7 text-ink-mid">{STATE_COPY[openedStage.id].detail}</p>
            {stageEvents.length === 0 ? <p className="text-sm text-ink-muted">这一步还没有执行记录，不代表已经完成。</p> : stageEvents.map((event) => (
              <div key={event.sequence} className="border-t border-rule pt-4">
                <h3 className="text-sm font-medium text-ink">{explanationOf(event).title}</h3>
                <p className="mt-1 text-sm leading-6 text-ink-mid">{explanationOf(event).detail}</p>
                <details className="mt-3"><summary className="cursor-pointer text-sm text-ink-muted">技术详情</summary><div className="mt-3"><TechnicalEvent event={event} /></div></details>
              </div>
            ))}
          </div>
        )}
        {panel?.type === "technical" && (
          <div className="space-y-6">
            <p className="text-sm leading-6 text-ink-mid">这里保留运行命令、原始错误与步骤记录，方便排查问题。未采集的数据不会显示为已确认。</p>
            <PlanPanel status={status} events={events} />
            <details className="rounded-lg border border-rule p-4"><summary className="cursor-pointer text-sm text-ink-mid">任务标识与接口原始结果</summary><div className="mt-3"><RawText text={JSON.stringify(status, null, 2)} /></div></details>
            <div>
              <h3 className="mb-3 text-sm font-semibold text-ink">全部步骤记录</h3>
              {events.length === 0 && <p className="text-sm text-ink-muted">暂无记录。</p>}
              {events.map((event) => <details key={event.sequence} className="border-t border-rule py-3">
                <summary className="cursor-pointer text-sm leading-6 text-ink-mid">#{event.sequence} {explanationOf(event).title}</summary>
                <div className="mt-4"><TechnicalEvent event={event} /></div>
              </details>)}
            </div>
          </div>
        )}
        {panel?.type === "report" && (replay ? <p className="text-sm leading-7 text-ink-mid">这是预设示例回放，没有真实生成的报告或补丁。返回选择任务并连接运行服务后，可以生成自己的结果文件。</p> : <ReportPanel runId={status?.run_id ?? null} status={status} />)}
      </Drawer>
    </div>
  );
}
