import clsx from "clsx";
import type { LoopView, StageView } from "../runModel";

const PURPOSE: Record<string, string> = {
  queued: "等待前面的任务完成",
  preparing: "下载仓库并确定代码版本",
  planning: "确认命令并创建运行环境",
  installing: "准备代码需要的软件包",
  testing: "检查程序与要求的结果文件",
  diagnosing: "判断哪里出错、能否处理",
  repairing: "调整后再次运行检查",
  succeeded: "仅代表通过本次检查条件",
};

export function StageTrack({ stages, loop, onSelect }: {
  stages: StageView[];
  loop: LoopView;
  onSelect: (id: StageView["id"]) => void;
}) {
  return (
    <ol className="space-y-1">
      {stages.map((stage) => {
        const active = stage.status === "active";
        const done = stage.status === "done" || (stage.id === "succeeded" && active);
        const bad = stage.status === "bad";
        return (
          <li key={stage.id}>
            {stage.id === "diagnosing" && <p className="mt-5 mb-2 px-2 text-xs text-ink-muted">检查未通过时自动尝试处理{loop.rounds.length > 0 && ` · 已分析 ${loop.rounds.length} 次`}</p>}
            <button onClick={() => onSelect(stage.id)} aria-current={active ? "step" : undefined}
              className={clsx("flex w-full cursor-pointer gap-3 rounded-lg px-2 py-3 text-left transition-colors hover:bg-white/[.04]", active && "bg-white/[.035]")}>
              <span aria-hidden className={clsx("mt-1.5 size-2 shrink-0 rounded-full", done ? "bg-pass" : bad ? "bg-reject" : active ? "bg-accent" : "border border-rule-hi")} />
              <span className="min-w-0 flex-1">
                <span className={clsx("block text-sm", active ? "font-medium text-ink" : bad ? "text-reject" : done ? "text-ink-mid" : "text-ink-muted")}>{stage.label}</span>
                <span className="mt-1 block text-xs leading-5 text-ink-muted">{PURPOSE[stage.id]}</span>
              </span>
              <span className={clsx("mt-0.5 shrink-0 text-[10px]", bad ? "text-reject" : "text-ink-muted")}>{done ? "已完成" : bad ? "未通过" : active ? "进行中" : stage.status === "skipped" ? "已跳过" : "未开始"}</span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}

export function dur(ms: number | null | undefined): string | null {
  if (ms === null || ms === undefined) return null;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)}s`;
  return `${Math.floor(s / 60)}m ${String(Math.round(s % 60)).padStart(2, "0")}s`;
}
