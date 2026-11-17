import { AnimatePresence, motion } from "motion/react";
import clsx from "clsx";
import { useMemo } from "react";
import { labelOf, type RunState, type RunStatus } from "../types";
import { stageIndex } from "../hooks/useRunPolling";

/**
 * 场景 3：运行现场。
 *
 * 三层结构（对应 DESIGN.md §1.2）：
 *   Hero     当前状态 + 三个关键数字
 *   Track    3D 阶段轨道（当前阶段抬高 + 发光，鼠标驱动倾斜）
 *   Body     动作流（左）+ 遥测（右）
 */

const STAGES: { id: RunState | "succeeded"; label: string }[] = [
  { id: "queued", label: "排队" },
  { id: "preparing", label: "创建容器" },
  { id: "planning", label: "生成计划" },
  { id: "installing", label: "安装依赖" },
  { id: "testing", label: "运行测试" },
  { id: "diagnosing", label: "诊断失败" },
  { id: "repairing", label: "修复中" },
  { id: "succeeded", label: "成功" },
];

export interface ActionRow {
  seq: number;
  ts: string;
  kind: "call" | "reject" | "finish" | "ok";
  name: string;
  target: string;
  result: string;
  blocked?: boolean;
  reason?: string;
}

interface Props {
  status: RunStatus | null;
  rows: ActionRow[];
  onCancel?: () => void;
  cancelling?: boolean;
}

export function RunScene({ status, rows, onCancel, cancelling }: Props) {
  const state = status?.state ?? "queued";
  const idx = stageIndex(state);
  const isFailed = state === "failed";

  const blockedCount = rows.filter((r) => r.blocked).length;
  const callCount = rows.filter((r) => r.kind === "call" || r.kind === "reject").length;

  // 状态文案
  const headline = useMemo(() => {
    if (state === "succeeded") return { text: "复现成功", accent: "已验证通过" };
    if (state === "failed") return { text: "复现失败", accent: labelOf(status?.result?.failure_category ?? "") || "未分类" };
    if (state === "cancelled") return { text: "已取消", accent: "用户终止" };
    if (state === "repairing") return { text: "修复中", accent: `第 ${status?.result?.attempts ?? 1} 轮` };
    return { text: stageLabelOf(state), accent: "执行中" };
  }, [state, status]);

  return (
    <div className="grid h-full grid-rows-[auto_auto_auto_1fr]">
      {/* ── 顶栏 ── */}
      <header className="flex h-[58px] items-center gap-4 border-b border-rule bg-gradient-to-b from-white/[.035] to-transparent px-6 backdrop-blur-md">
        <div className="flex items-center gap-2.5 text-[13px] font-semibold tracking-[.1em]">
          <motion.i
            className="block size-[7px] rounded-full bg-accent shadow-[0_0_12px_var(--color-accent)]"
            animate={{ opacity: [0.35, 1, 0.35], scale: [0.85, 1.15, 0.85] }}
            transition={{ duration: 2, repeat: Infinity, ease: "easeInOut" }}
          />
          PaperHarness
        </div>
        <div className="font-mono text-[11px] tracking-[.04em] text-ink-muted">
          {status?.run_id ? `run_${status.run_id.slice(0, 8)}` : "—"}
        </div>
        <div className="flex-1" />
        <button className="cursor-pointer rounded-[7px] border border-rule-hi bg-white/[.03] px-4 py-2 text-xs text-ink-mid transition-colors hover:border-ink-muted hover:text-ink">
          查看计划
        </button>
        <button
          onClick={onCancel}
          disabled={cancelling || ["succeeded", "failed", "cancelled"].includes(state)}
          className="cursor-pointer rounded-[7px] border border-reject/45 bg-white/[.03] px-4 py-2 text-xs text-reject transition-colors hover:bg-reject/10 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {cancelling ? "取消中…" : "取消"}
        </button>
      </header>

      {/* ── Hero ── */}
      <section className="grid grid-cols-[1fr_auto] items-center gap-8 border-b border-rule px-6 pt-6 pb-5">
        <div className="min-w-0">
          <div className="mb-2 flex items-center gap-3.5">
            <motion.div
              className={clsx(
                "size-[11px] rounded-full",
                isFailed ? "bg-reject shadow-[0_0_0_4px_rgb(255_92_71_/_0.16),0_0_22px_var(--color-reject)]"
                  : state === "succeeded" ? "bg-pass shadow-[0_0_0_4px_rgb(46_230_168_/_0.16),0_0_22px_var(--color-pass)]"
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
            容器已隔离 · 网络已断开 ·{" "}
            <b className="font-medium text-warn">{blockedCount} 次拦截</b>
          </p>
        </div>

        <div className="flex gap-9">
          <Stat value={callCount} label="动作" />
          <Stat value={blockedCount} label="拦截" tone="bad" />
          <Stat value={0} label="逃逸" tone="good" />
        </div>
      </section>

      {/* ── 3D 阶段轨道 ── */}
      <section className="relative overflow-hidden border-b border-rule px-6 pt-7 pb-6 [perspective:1400px] [perspective-origin:50%_42%]">
        <div className="absolute top-4 right-6 z-[3] flex items-center gap-2 rounded-full border border-accent/40 bg-accent/10 px-3 py-1.5 font-mono text-[10.5px] tracking-[.06em] text-[#9db4ff]">
          <motion.span
            className="block size-2.5 rounded-full border-[1.5px] border-[#9db4ff]/30 border-t-[#9db4ff]"
            animate={{ rotate: 360 }}
            transition={{ duration: 1.1, repeat: Infinity, ease: "linear" }}
          />
          修复回环 ×1
        </div>

        <div
          className="flex items-center"
          style={{
            transformStyle: "preserve-3d",
            transform:
              "rotateX(calc(9deg + var(--my, 0) * -3deg)) rotateY(calc(var(--mx, 0) * 4deg)) translateZ(-40px)",
            transition: "transform .5s cubic-bezier(.16,1,.3,1)",
          }}
        >
          {STAGES.map((s, i) => {
            const done = i < idx;
            const active = i === idx && !["succeeded", "failed", "cancelled"].includes(state);
            const bad = isFailed && i === idx;
            return (
              <div key={s.id} className="contents">
                {i > 0 && (
                  <div className="h-px flex-[0_0_18px]">
                    <div
                      className={clsx(
                        "h-px w-full transition-colors duration-500",
                        done ? "bg-gradient-to-r from-pass to-pass/35" : "bg-rule-hi",
                      )}
                    />
                  </div>
                )}
                <motion.div
                  className="min-w-0 flex-1"
                  style={{ transformStyle: "preserve-3d" }}
                  animate={{
                    transform: active ? "translateZ(64px) scale(1.09)" : "translateZ(0) scale(1)",
                  }}
                  transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
                >
                  <div
                    className={clsx(
                      "relative mx-[7px] overflow-hidden rounded-[9px] border px-4 pt-4 pb-[15px] transition-colors duration-400",
                      done && "border-pass/25 bg-gradient-to-br from-pass/[.09] to-white/[.008]",
                      active && "border-accent/75 bg-gradient-to-br from-accent/20 to-accent/[.03] shadow-[0_22px_60px_-18px_rgb(108_143_255_/_0.85),0_0_0_1px_rgb(108_143_255_/_0.3)_inset,0_0_50px_-8px_rgb(108_143_255_/_0.5)]",
                      bad && "border-reject/55 bg-gradient-to-br from-reject/[.13] to-white/[.008]",
                      !done && !active && !bad && "border-rule bg-gradient-to-br from-white/[.075] to-white/[.018] opacity-55",
                    )}
                  >
                    <div
                      aria-hidden
                      className="pointer-events-none absolute inset-0 opacity-50"
                      style={{
                        background:
                          "radial-gradient(240px circle at 50% 0%, rgb(255 255 255 / .10), transparent 70%)",
                      }}
                    />
                    <div
                      className={clsx(
                        "mb-2 font-mono text-[9.5px] tracking-[.14em]",
                        active ? "text-accent" : "text-ink-muted",
                      )}
                    >
                      {String(i + 1).padStart(2, "0")}
                    </div>
                    <div
                      className={clsx(
                        "truncate font-mono text-[11.5px]",
                        active && "font-semibold text-[#cfdcff]",
                        done && "text-pass",
                        bad && "text-reject",
                        !done && !active && !bad && "text-ink-mid",
                      )}
                    >
                      {s.label}
                    </div>
                    <div className="mt-3 h-0.5 overflow-hidden rounded-sm bg-white/[.07]">
                      <motion.div
                        className={clsx(
                          "h-full rounded-sm",
                          bad
                            ? "bg-reject"
                            : done
                              ? "bg-gradient-to-r from-pass/50 to-pass"
                              : "bg-gradient-to-r from-accent to-pass",
                        )}
                        animate={
                          active
                            ? { width: ["18%", "58%"], marginLeft: ["0%", "22%"] }
                            : { width: done || bad ? "100%" : "0%", marginLeft: "0%" }
                        }
                        transition={
                          active
                            ? { duration: 1.6, repeat: Infinity, repeatType: "reverse", ease: "easeInOut" }
                            : { duration: 0.8, ease: [0.16, 1, 0.3, 1] }
                        }
                      />
                    </div>
                  </div>
                </motion.div>
              </div>
            );
          })}
        </div>
      </section>

      {/* ── 动作流 + 遥测 ── */}
      <div className="grid min-h-0 grid-cols-1 lg:grid-cols-[1fr_296px]">
        <div className="min-h-0 overflow-y-auto px-6 py-5">
          <div className="sticky top-0 z-[2] mb-1.5 flex items-center justify-between border-b border-rule bg-void pt-0.5 pb-3">
            <div className="flex gap-[3px]">
              <button className="cursor-pointer rounded-[5px] border border-rule-hi bg-surface-hi px-3.5 py-1.5 text-xs text-ink">
                动作流
              </button>
              <button className="cursor-pointer rounded-[5px] border border-transparent px-3.5 py-1.5 text-xs text-ink-muted transition-colors hover:text-ink-mid">
                补丁 diff
              </button>
            </div>
            <div className="font-mono text-[10.5px] tracking-[.03em] text-ink-muted">
              {rows.length} 个动作 · ACI 审查 {callCount} 次 ·{" "}
              <b className="text-reject">拦截 {blockedCount} 次</b>
            </div>
          </div>

          <AnimatePresence initial={false}>
            {rows.map((r) => (
              <motion.div
                key={r.seq}
                initial={{ opacity: 0, x: -18 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ duration: 0.42, ease: [0.16, 1, 0.3, 1] }}
              >
                <ActionLine row={r} />
              </motion.div>
            ))}
          </AnimatePresence>
        </div>

        <aside className="min-h-0 overflow-y-auto border-l border-rule bg-gradient-to-b from-white/[.018] to-transparent p-5">
          <div className="mb-4 text-[10px] tracking-[.18em] text-ink-muted uppercase">
            沙箱遥测
          </div>
          <Telemetry status={status} />
        </aside>
      </div>
    </div>
  );
}

function Stat({ value, label, tone }: { value: number; label: string; tone?: "bad" | "good" }) {
  return (
    <div className="text-right">
      <motion.div
        key={value}
        initial={{ opacity: 0, y: 8 }}
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
      <div className="mt-2.5 text-[10px] tracking-[.16em] text-ink-muted uppercase">
        {label}
      </div>
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
          "relative -mx-[13px] mb-0.5 grid cursor-default grid-cols-[104px_20px_166px_minmax(0,1fr)_116px] items-center gap-[11px] rounded-md px-[13px] py-[9px] font-mono text-[12.5px]",
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

function Telemetry({ status }: { status: RunStatus | null }) {
  const state = status?.state ?? "queued";
  const online = state === "installing";
  const items: { k: string; v: string; tone?: "on" | "off" | "warn" }[] = [
    { k: "容器", v: status?.run_id ? `harness-${status.run_id.slice(0, 6)}` : "—" },
    { k: "镜像", v: "base-py3.11" },
    { k: "挂载", v: "/workspace rw" },
    { k: "网络", v: online ? "已连接" : "已断开", tone: online ? "on" : "off" },
    { k: "PID", v: "7 / 128" },
    { k: "用户", v: "1000:1000" },
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
      <div className="mt-3.5">
        <div className="mb-1.5 flex justify-between font-mono text-[10px] text-ink-muted">
          <span>内存</span>
          <span>412 MB / 2 GB</span>
        </div>
        <div className="h-[3px] overflow-hidden rounded-sm bg-white/[.07]">
          <motion.div
            className="h-full rounded-sm bg-gradient-to-r from-accent to-pass shadow-[0_0_12px_rgb(108_143_255_/_0.7)]"
            initial={{ width: "0%" }}
            animate={{ width: "21%" }}
            transition={{ duration: 1, ease: [0.16, 1, 0.3, 1] }}
          />
        </div>
      </div>
    </>
  );
}

function stageLabelOf(state: RunState): string {
  return STAGES.find((s) => s.id === state)?.label ?? state;
}
