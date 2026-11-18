import { motion } from "motion/react";
import clsx from "clsx";
import type { LoopView } from "../runModel";
import type { RunState } from "../types";

const SHORT: Record<string, string> = {
  read_file: "读取",
  search_text: "检索",
  replace_exact: "改码",
  install_requirement: "装包",
  set_environment: "环境变量",
  run_smoke_test: "跑测试",
  finish_repair: "收尾",
};

/**
 * 自愈回环条：诊断 → 修复 → 重跑，模型现在在干什么。
 *
 * 只在真的进过回环（收到过 diagnosis 事件）时出现；没失败过就不占地方，
 * 也不假装"修复中"。
 */
export function LoopStrip({
  loop,
  state,
  onOpenAction,
  onOpenLoop,
}: {
  loop: LoopView;
  state: RunState;
  onOpenAction: (seq: number) => void;
  onOpenLoop: () => void;
}) {
  if (loop.rounds.length === 0) return null;

  const current = loop.rounds.at(-1)!;
  const running = state === "diagnosing" || state === "repairing";
  // 熔断可能发生在更早的轮次（最后一轮正常收尾），所以取最近一次被熔断的轮。
  const stopped = [...loop.rounds].reverse().find((r) => r.stopped);

  return (
    <section className="relative border-b border-rule bg-gradient-to-r from-accent/[.055] via-transparent to-reject/[.04] px-6 py-3">
      <div className="flex items-center gap-5">
        {/* 轮次 */}
        <button
          onClick={onOpenLoop}
          className="group flex shrink-0 cursor-pointer items-center gap-3 rounded-[9px] border border-accent/35 bg-accent/[.07] px-3 py-2 transition-colors hover:border-accent/70 hover:bg-accent/[.12]"
        >
          <motion.span
            className={clsx(
              "block size-3 rounded-full border-[1.6px]",
              running
                ? "border-[#9db4ff]/30 border-t-[#9db4ff]"
                : state === "failed"
                  ? "border-reject/40 border-t-reject"
                  : "border-pass/40 border-t-pass",
            )}
            animate={running ? { rotate: 360 } : { rotate: 0 }}
            transition={running ? { duration: 1.1, repeat: Infinity, ease: "linear" } : {}}
          />
          <span className="font-mono text-[11px] tracking-[.06em] text-[#9db4ff]">
            自愈第 <b className="text-[13px] text-ink">{current.round}</b> / {loop.budget} 轮
          </span>
          <span className="font-mono text-[9.5px] text-ink-muted transition-colors group-hover:text-ink-mid">
            全部 ×
          </span>
        </button>

        {/* 诊断结论 */}
        <div className="flex min-w-0 shrink-0 items-center gap-2">
          <span className="font-mono text-[9.5px] tracking-[.16em] text-ink-muted uppercase">
            诊断
          </span>
          <span
            className={clsx(
              "rounded-[5px] border px-2 py-[3px] font-mono text-[10.5px]",
              current.failureClass === "unknown"
                ? "border-rule-hi text-ink-mid"
                : "border-warn/40 bg-warn/[.08] text-warn",
            )}
            title="后端分类器给出的 failure_class，前端原样展示，不做翻译猜测"
          >
            {current.failureClass}
          </span>
          <span className="rounded-[5px] border border-rule-hi px-2 py-[3px] font-mono text-[10.5px] text-ink-mid">
            {current.phase === "install" ? "安装阶段失败" : "测试阶段失败"}
          </span>
          {/* 熔断 = 这一轮不是正常跑完的，不写出来会被当成「模型收敛了」 */}
          {stopped?.stopped && (
            <span
              title={stopped.stopped.detail}
              className="rounded-[5px] border border-reject/40 bg-reject/[.08] px-2 py-[3px] font-mono text-[10.5px] text-reject"
            >
              第 {stopped.round} 轮熔断
            </span>
          )}
        </div>

        {/* 正在做 */}
        <div className="flex min-w-0 flex-1 items-center gap-2.5 border-l border-rule pl-5">
          <motion.span
            className={clsx(
              "size-[6px] shrink-0 rounded-full",
              running ? "bg-accent" : "bg-ink-muted",
            )}
            animate={running ? { opacity: [0.3, 1, 0.3] } : { opacity: 1 }}
            transition={{ duration: 1.4, repeat: Infinity, ease: "easeInOut" }}
          />
          <span className="shrink-0 font-mono text-[9.5px] tracking-[.16em] text-ink-muted uppercase">
            正在做
          </span>
          <span
            className={clsx(
              "truncate font-mono text-[11.5px]",
              running ? "text-[#cfdcff]" : "text-ink-mid",
            )}
          >
            {loop.doing || (state === "failed" ? "回环已结束" : "回环已收敛")}
          </span>
        </div>

        {/* 本轮动作 */}
        <div className="hidden shrink-0 items-center gap-1 xl:flex">
          {current.actions.map((a) => (
            <button
              key={a.seq}
              onClick={() => onOpenAction(a.seq)}
              title={`${a.name} · ${a.summary}`}
              className={clsx(
                "cursor-pointer rounded-[5px] border px-1.5 py-[3px] font-mono text-[10px] transition-colors",
                a.blocked
                  ? "border-reject/45 bg-reject/[.10] text-reject hover:bg-reject/20"
                  : a.ok
                    ? "border-pass/35 bg-pass/[.07] text-pass hover:bg-pass/15"
                    : "border-rule-hi text-ink-mid hover:text-ink",
              )}
            >
              {SHORT[a.name] ?? a.name}
              {a.blocked ? " ✕" : a.ok ? "" : " …"}
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}
