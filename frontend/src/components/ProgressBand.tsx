import { motion } from "motion/react";
import clsx from "clsx";
import { PROGRESS_TICKS, type ProgressView } from "../runModel";
import type { RunState } from "../types";
import { STILL } from "../still";

/**
 * 整体进度带：百分比 + 倒计时 + 分段进度条。
 *
 * 为什么单独一条：Hero 上的「当前阶段 已持续 Xs」只说的是**这一步**，
 * 用户真正想知道的是「还要多久」和「整体到哪了」。这两个数在契约里没有，
 * 所以这里把「已完成（事实）」和「剩余（经验估算）」分开写，不混成一个数。
 */
export function ProgressBand({ p, state }: { p: ProgressView; state: RunState }) {
  const terminal = ["succeeded", "failed", "cancelled"].includes(state);
  const failed = state === "failed" || state === "cancelled";
  const done = state === "succeeded";

  return (
    <section className="shrink-0 border-b border-rule px-6 pt-4 pb-[18px]">
      <div className="flex items-end justify-between gap-6">
        <div className="flex items-baseline gap-3">
          <div
            className={clsx(
              "font-mono text-[clamp(24px,2.6vw,32px)] leading-none font-semibold tracking-[-.03em] tabular-nums",
              done && "text-pass [text-shadow:0_0_28px_rgb(46_230_168_/_0.45)]",
              failed && "text-reject",
              !done && !failed && "text-ink",
            )}
          >
            {String(p.pct).padStart(2, "0")}
            <span className="ml-0.5 text-[13px] font-normal text-ink-muted">%</span>
          </div>

          <div className="pb-1 font-mono text-[9.5px] tracking-[.2em] text-ink-muted uppercase">
            整体进度
            {p.looping && (
              <span className="ml-2 rounded-[4px] border border-warn/40 bg-warn/[.08] px-1.5 py-[1px] text-[9px] tracking-[.1em] text-warn">
                重跑中
              </span>
            )}
            {p.basis === "guess" && (
              <span className="ml-2 text-[9px] tracking-[.1em] text-ink-muted/70">尚无事件</span>
            )}
          </div>
        </div>

        <div className="flex items-baseline gap-5 pb-1 font-mono text-[10.5px] tracking-[.03em] text-ink-muted">
          <span>
            剩余{" "}
            <b
              className={clsx(
                "text-[12px] font-medium tabular-nums",
                done ? "text-pass" : failed ? "text-reject" : "text-ink",
              )}
            >
              {p.etaMs === null ? "--:--" : clock(p.etaMs)}
            </b>
          </span>
          <span className="text-ink-muted/50">·</span>
          <span>
            已运行 <b className="text-ink-mid tabular-nums">{clock(p.elapsedMs)}</b>
          </span>
          <span className="text-ink-muted/50">·</span>
          <span>
            预估 <b className="text-ink-mid tabular-nums">{p.totalMs === null ? "--:--" : clock(p.totalMs)}</b>
          </span>
        </div>
      </div>

      {/* 进度条本体：分段刻度 + 流光 + 终态发光 */}
      <div className="relative mt-3 h-[7px] overflow-hidden rounded-full bg-white/[.06] shadow-[inset_0_1px_2px_rgb(0_0_0_/_0.45)]">
        <motion.div
          className={clsx(
            "absolute inset-y-0 left-0 rounded-full",
            done && "bg-gradient-to-r from-pass/70 to-pass shadow-[0_0_18px_rgb(46_230_168_/_0.65)]",
            failed && "bg-gradient-to-r from-reject/60 to-reject",
            !done &&
              !failed &&
              "bg-gradient-to-r from-[#4f6fe0] via-accent to-pass shadow-[0_0_16px_rgb(108_143_255_/_0.6)]",
          )}
          initial={STILL ? false : { width: "0%" }}
          animate={{ width: `${p.pct}%` }}
          transition={{ duration: STILL ? 0 : 0.7, ease: [0.16, 1, 0.3, 1] }}
        >
          {!terminal && (
            <motion.span
              aria-hidden
              className="absolute inset-0 rounded-full"
              style={{
                background:
                  "linear-gradient(90deg, transparent, rgb(255 255 255 / .45), transparent)",
              }}
              initial={{ x: "-100%" }}
              animate={{ x: "120%" }}
              transition={{ duration: 1.9, repeat: Infinity, ease: "easeInOut" }}
            />
          )}
        </motion.div>

        {/* 阶段分界刻度 */}
        {PROGRESS_TICKS.map((t) => (
          <span
            key={t}
            aria-hidden
            className="absolute top-0 h-full w-px bg-void/70"
            style={{ left: `${t * 100}%` }}
          />
        ))}
      </div>

      <div className="mt-2 flex justify-between font-mono text-[9px] tracking-[.08em] text-ink-muted/70">
        <span>克隆 · 计划 · 安装 · 测试</span>
        <span>
          {terminal
            ? done
              ? "已完成，结果见结果报告（report.md 原文）"
              : "已结束，未走完全程"
            : p.basis === "guess"
              ? "尚无事件，剩余时间按各阶段经验耗时估算"
              : "剩余时间按各阶段经验耗时估算，非契约字段"}
        </span>
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
