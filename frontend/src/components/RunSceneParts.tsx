import clsx from "clsx";
import type { EventExplanation, RunEvent, RunGuidance, RunState, RunStatus } from "../types";
import type { StageView } from "../runModel";

export type Panel =
  | { type: "event"; seq: number }
  | { type: "stage"; id: StageView["id"] }
  | { type: "technical" }
  | { type: "report" }
  | null;

export const STATE_COPY: Record<RunState, { title: string; detail: string }> = {
  queued: { title: "任务已提交，等待开始", detail: "系统会在前面的任务结束后自动开始。你可以留在这里查看进展。" },
  preparing: { title: "正在获取项目代码", detail: "下载你选择的仓库并确认代码版本。完成后会确定运行方式。" },
  planning: { title: "正在准备运行方式与环境", detail: "确认 Python 版本、运行命令和检查目标，再为本次任务创建独立环境。" },
  installing: { title: "正在安装项目需要的依赖", detail: "为代码准备所需的软件包。安装完成后会运行程序，暂时不需要你操作。" },
  testing: { title: "正在运行程序并检查结果", detail: "执行本次运行计划，检查程序是否正常结束，以及要求的结果文件是否存在。" },
  diagnosing: { title: "正在分析失败原因", detail: "程序没有通过检查，系统正在根据错误记录判断是否可以自动处理。" },
  repairing: { title: "正在尝试自动修复", detail: "系统会读取相关代码，尝试允许范围内的修改，再重新运行验证；尝试修复不代表已经成功。" },
  succeeded: { title: "已通过本次运行验证", detail: "程序通过了本次设置的检查条件，这不等于完整复现论文的全部实验结论。" },
  failed: { title: "本次运行未能完成", detail: "请查看运行说明和技术详情，确认停止原因后再开始新的任务。" },
  cancelled: { title: "本次任务已取消", detail: "本次任务不会继续执行，已有记录仍可查看。需要重试时，请返回重新提交。" },
};

export function guidanceOf(status: RunStatus | null, replay: boolean): RunGuidance {
  if (status?.guidance) return status.guidance;
  if (!status) return {
    title: replay ? "正在加载示例回放" : "正在读取任务进展",
    detail: replay ? "这是预设记录，不会创建真实运行任务。" : "连接成功后，这里会显示当前步骤和已经发生的事情。",
    tone: "neutral", next_steps: [], repair_summary: "",
  };
  const terminal = ["succeeded", "failed", "cancelled"].includes(status.state);
  if (terminal && !status.result) return {
    title: "运行已结束，正在整理结果", detail: "正在同步最终说明和报告，请稍候。",
    tone: "neutral", next_steps: [], repair_summary: "",
  };
  return {
    ...STATE_COPY[status.state],
    tone: status.state === "succeeded" ? "success" : status.state === "failed" ? "error" : "neutral",
    next_steps: terminal ? ["查看运行经过与结果文件，或返回选择其他任务。"] : [],
    repair_summary: replay ? "以下为示例中的处理记录，不是本机实际执行结果。" : "",
  };
}

const EVENT_NAMES: Record<string, string> = {
  preparing: "项目代码已获取", plan: "运行方式已确定", sandbox_start: "开始创建运行环境",
  sandbox_ready: "运行环境已创建", install: "依赖安装结果", test: "程序检查结果",
  diagnosis: "已分析失败原因", read_file: "查看相关代码", search_text: "搜索相关位置",
  replace_exact: "尝试修改代码", install_requirement: "尝试补充依赖", set_environment: "调整运行参数",
  run_smoke_test: "重新检查程序", finish_repair: "结束本轮修复决策", repair_round: "本轮修复尝试结束",
  loop_stopped: "停止重复尝试", final: "本次运行结束", cancel: "取消任务", internal_error: "运行服务发生异常",
};

export function explanationOf(event: RunEvent): EventExplanation {
  if (event.explanation) return event.explanation;
  return {
    title: EVENT_NAMES[event.kind] ?? "运行记录",
    detail: "此记录未附带用户说明，可在技术详情中查看原始信息。",
    tone: event.data.ok === false || (typeof event.data.exit_code === "number" && event.data.exit_code !== 0)
      ? "warning" : "neutral",
  };
}

export const controlClass = "cursor-pointer rounded-lg border border-rule-hi px-3.5 py-2 text-sm text-ink-mid transition-colors hover:border-ink-muted hover:text-ink disabled:cursor-not-allowed disabled:opacity-40";

export function RunOverview({ guidance, terminal, onBack, onDetails, onReport }: {
  guidance: RunGuidance;
  terminal: boolean;
  onBack?: () => void;
  onDetails: () => void;
  onReport: () => void;
}) {
  return (
    <section aria-label="当前运行说明" className="border-b border-rule pb-7">
      <div aria-live="polite" aria-atomic="true">
        <div className="mb-3 flex items-center gap-2 text-sm text-ink-mid">
          <span aria-hidden className={clsx("size-2 rounded-full", toneDot(guidance.tone))} />
          {terminal ? "本次运行结论" : "当前正在做什么"}
        </div>
        <h1 className="text-[24px] leading-snug font-semibold tracking-tight text-ink sm:text-[28px]">{guidance.title}</h1>
        <p className="mt-3 max-w-[65ch] text-[15px] leading-7 text-ink-mid">{guidance.detail}</p>
        {guidance.repair_summary && <p className="mt-3 text-sm leading-6 text-ink-mid">{guidance.repair_summary}</p>}
      </div>
      {guidance.next_steps.length > 0 && (
        <div className="mt-5 rounded-lg border border-rule bg-surface px-4 py-4 sm:px-5">
          <h2 className="text-sm font-semibold text-ink">{terminal ? "接下来可以怎么做" : "接下来会发生什么"}</h2>
          <ul className="mt-2 list-disc space-y-1.5 pl-4 text-sm leading-6 text-ink-mid">
            {guidance.next_steps.map((step) => <li key={step}>{step}</li>)}
          </ul>
          <div className="mt-4 flex flex-wrap gap-2">
            {terminal && onBack && <button className={clsx(controlClass, "border-accent/50 bg-accent/10 text-ink")} onClick={onBack}>返回选择任务</button>}
            {terminal && <button className={controlClass} onClick={onReport}>查看结果与文件</button>}
            <button className={controlClass} onClick={onDetails}>查看技术详情</button>
          </div>
        </div>
      )}
    </section>
  );
}

export function EventCard({ event, index, onOpen }: { event: RunEvent; index: number; onOpen: (seq: number) => void }) {
  const explanation = explanationOf(event);
  return (
    <li className="relative flex gap-4 pb-6 last:pb-0">
      <div className="relative flex w-7 shrink-0 justify-center">
        <span aria-hidden className="absolute top-7 bottom-[-4px] w-px bg-rule" />
        <span className={clsx("relative z-10 flex size-7 items-center justify-center rounded-full border bg-surface text-xs tabular-nums", toneText(explanation.tone))}>{index + 1}</span>
      </div>
      <div className="min-w-0 flex-1 pb-1">
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
          <h3 className="text-[15px] leading-6 font-medium text-ink">{explanation.title}</h3>
          <time className="text-xs tabular-nums text-ink-muted" dateTime={event.timestamp}>
            {new Date(event.timestamp).toLocaleTimeString("zh-CN", { hour12: false })}
          </time>
        </div>
        <p className="mt-1 max-w-[70ch] text-sm leading-6 break-words text-ink-mid">{explanation.detail}</p>
        <button onClick={() => onOpen(event.sequence)} className="mt-1.5 cursor-pointer rounded text-xs leading-6 text-ink-muted underline decoration-rule-hi underline-offset-4 hover:text-ink" aria-label={`查看技术详情：${explanation.title}`}>技术详情</button>
      </div>
    </li>
  );
}

export function TechnicalEvent({ event }: { event: RunEvent }) {
  const explanation = explanationOf(event);
  const logs = [
    ["错误输出", event.data.stderr_tail],
    ["标准输出", event.data.stdout_tail ?? event.data.output_tail],
  ].filter(([, text]) => typeof text === "string" && text.length > 0);
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-base font-medium text-ink">{explanation.title}</h3>
        <p className="mt-1 text-sm leading-6 text-ink-mid">{explanation.detail}</p>
      </div>
      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2 text-xs">
        <dt className="text-ink-muted">事件类型</dt><dd className="font-mono break-all text-ink-mid">{event.kind}</dd>
        <dt className="text-ink-muted">记录时间</dt><dd className="text-ink-mid">{new Date(event.timestamp).toLocaleString("zh-CN", { hour12: false })}</dd>
        {event.data.exit_code !== undefined && <><dt className="text-ink-muted">退出码</dt><dd className="font-mono text-ink-mid">{event.data.exit_code === null ? "未返回退出码" : String(event.data.exit_code)}</dd></>}
        {event.log_path && <><dt className="text-ink-muted">日志相对路径</dt><dd className="font-mono break-all text-ink-mid">{event.log_path}</dd></>}
      </dl>
      <div><h4 className="mb-2 text-sm text-ink-mid">原始记录</h4><RawText text={event.message} /></div>
      {logs.map(([label, text]) => <div key={label as string}><h4 className="mb-2 text-sm text-ink-mid">{label as string}（已保存的尾部）</h4><RawText text={text as string} /></div>)}
      <details className="rounded-lg border border-rule p-3">
        <summary className="cursor-pointer text-sm text-ink-mid">完整事件字段</summary>
        <div className="mt-3"><RawText text={JSON.stringify(event, null, 2)} /></div>
      </details>
    </div>
  );
}

export function RawText({ text }: { text: string }) {
  return <pre className="max-h-80 overflow-auto rounded-lg border border-rule bg-void p-3 font-mono text-xs leading-6 whitespace-pre-wrap break-all text-ink-mid">{text}</pre>;
}

function toneDot(tone: EventExplanation["tone"]) {
  return { neutral: "bg-accent", success: "bg-pass", warning: "bg-warn", error: "bg-reject" }[tone];
}

function toneText(tone: EventExplanation["tone"]) {
  return { neutral: "border-rule-hi text-ink-muted", success: "border-pass/40 text-pass", warning: "border-warn/40 text-warn", error: "border-reject/40 text-reject" }[tone];
}
