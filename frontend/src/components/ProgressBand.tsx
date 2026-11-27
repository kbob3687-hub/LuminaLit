import { motion } from "motion/react";
import clsx from "clsx";
import { PROGRESS_TICKS, type ProgressView } from "../runModel";
import type { RunState } from "../types";
import { STILL } from "../still";

/**
 * 全局细进度条：独立一行（圆角轨道，不与底边框合并）。
 *
 * 已完成部分是事实（事件推导），剩余部分是经验估算（runModel.buildProgress），
 * 界面上仍把两者分开写，不混成一个数。
 */
export function ProgressBand({
  p,
  state,
  stageLabel,
}: {
  p: ProgressView;
  state: RunState;
  /** 当前阶段名（RunScene 由 activeStage / state 推导）。 */
  stageLabel: string;
}) {
  const terminal = ["succeeded", "failed", "cancelled"].includes(state);
  const failed = state === "failed" || state === "cancelled";
  const done = state === "succeeded";

  return (
    <section className="shrink-0 border-b border-rule px-6 pt-2.5 pb-3">
      <div className="flex items-center justify-between gap-6">
        <div className="flex min-w-0 items-baseline gap-2.5">
          <span
            className={clsx(
              "font-mono text-[13px] leading-none font-semibold tabular-nums",
              done ? "text-pass" : failed ? "text-reject" : "text-ink",
            )}
          >
            {p.pct}
            <span className="ml-0.5 text-[10px] font-normal text-ink-muted">%</span>
          </span>
          <span className="truncate text-[11.5px] text-ink-mid">{stageLabel}</span>
          {p.looping && (
            <span className="shrink-0 rounded border border-warn/20 bg-warn/10 px-1.5 py-[1px] font-mono text-[9px] text-warn">
              重跑中
            </span>
          )}
          {p.basis === "guess" && !terminal && (
            <span className="shrink-0 font-mono text-[9px] text-ink-muted/70">按经验估算</span>
          )}
        </div>

        <div className="flex shrink-0 items-baseline gap-4 font-mono text-[10.5px] tabular-nums text-ink-muted">
          <span>
            已运行 <b className="font-medium text-ink-mid">{clock(p.elapsedMs)}</b>
          </span>
          <span className="text-ink-muted/50">·</span>
          <span>
            预估剩余{" "}
            <b
              className={clsx(
                "font-medium",
                done ? "text-pass" : failed ? "text-reject" : "text-ink-mid",
              )}
            >
              {p.etaMs === null ? "--:--" : clock(p.etaMs)}
            </b>
          </span>
        </div>
      </div>

      {/* 圆角轨道 + 平滑渐变条，独立一行，不贴分割线 */}
      <div className="relative mt-2 h-1.5 overflow-hidden rounded-full bg-white/[0.08]">
        {PROGRESS_TICKS.map((t) => (
          <span
            key={t}
            aria-hidden
            className="absolute inset-y-0 z-[1] w-px bg-void/50"
            style={{ left: `${t * 100}%` }}
          />
        ))}
        <motion.div
          className={clsx(
            "absolute inset-y-0 left-0 rounded-full",
            done
              ? "bg-gradient-to-r from-pass/70 to-pass"
              : failed
                ? "bg-gradient-to-r from-reject/70 to-reject"
                : "bg-gradient-to-r from-accent/80 to-[#60a5fa]",
          )}
          initial={STILL ? false : { width: "0%" }}
          animate={{ width: `${p.pct}%` }}
          transition={{ duration: STILL ? 0 : 0.6, ease: [0.16, 1, 0.3, 1] }}
        />
      </div>
    </section>
  );
}

/** 00:38 / 01:19 */
function clock(ms: number): string {
  const total = Math.max(0, Math.round(ms / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}
