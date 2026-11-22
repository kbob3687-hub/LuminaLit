import { AnimatePresence, motion } from "motion/react";
import clsx from "clsx";
import { useEffect, useRef, useState } from "react";
import type { ActionRow } from "../runModel";
import type { RunEvent } from "../types";

/**
 * 动作流里的一行。
 *
 * 两个入口，按"先看近处、再跳远"分开：
 *   点行     → 就地展开，展开区可以拖动下边缘改高度（每一步的原文长度差很多，
 *              固定行高只能靠截断，用户就看不到完整参数了）
 *   ↗ 按钮   → 打开原来的独立抽屉（ACI 流水线 / 单条事件全文）
 *
 * 之前整行点击直接开抽屉：想连续对照两条动作必须反复开关，而且行内 `truncate`
 * 掉的 target/result 在展开前没有任何办法看全。
 */

/** 截图模式：关掉高度过渡，避免无头渲染抓到中间帧。 */
const STILL = new URLSearchParams(window.location.search).get("still") === "1";

const MIN_H = 96;
const MAX_H = 560;

interface Props {
  row: ActionRow;
  /** 该行对应的后端事件原文；找不到时展开区只显示动作表里的字段。 */
  event?: RunEvent;
  expanded: boolean;
  /** 用户拖过的高度；null = 由内容自适应（上限 260）。 */
  height: number | null;
  onToggle: () => void;
  onResize: (h: number) => void;
  onOpenDetail: () => void;
}

export function ActionRowItem({
  row,
  event,
  expanded,
  height,
  onToggle,
  onResize,
  onOpenDetail,
}: Props) {
  const sym =
    row.kind === "reject" ? "✕" : row.kind === "finish" ? "⊘" : row.kind === "ok" ? "✓" : "⟩";
  const [dragging, setDragging] = useState(false);
  const bodyRef = useRef<HTMLDivElement>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const drag = useRef<{ y: number; h: number } | null>(null);

  // 展开后把它拉进可视区：动作流窗口不高，展开一条 200px 的详情很容易
  // 只露出上半截，用户会以为"点了没反应"。
  useEffect(() => {
    if (!expanded) return;
    rootRef.current?.scrollIntoView({ block: "nearest", behavior: STILL ? "auto" : "smooth" });
  }, [expanded]);

  const data = event?.data ?? {};
  const hasData = Object.keys(data).length > 0;

  return (
    <div
      ref={rootRef}
      data-blocked={row.blocked ? "1" : undefined}
      className={clsx(
        "relative -mx-[13px] rounded-[6px] border-b border-white/[.045] transition-colors last:border-b-0",
        row.blocked &&
          "border-b-reject/15 bg-gradient-to-r from-reject/[.09] to-reject/[.02] shadow-[inset_2px_0_0_var(--color-reject)]",
        expanded && "bg-white/[.035]",
      )}
    >
      {/* ── 行头：点一下就地展开 ── */}
      <div
        role="button"
        tabIndex={0}
        aria-expanded={expanded}
        onClick={onToggle}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onToggle();
          }
        }}
        className={clsx(
          // 列宽按"时间 图标 动作 参数 结果 展开"分配：结果列用 max-content，
          // 宁可挤参数列也不截断结论（`20 matches` 截成 `20 ma…` 就没信息了）
          "grid cursor-pointer grid-cols-[58px_14px_140px_minmax(0,1fr)_minmax(96px,max-content)_16px] items-center gap-x-[10px] px-[13px] py-[8px] font-mono text-[12px] transition-colors hover:bg-white/[.035]",
          row.blocked && "pl-[15px] hover:bg-reject/[.10]",
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
        <motion.span
          animate={{ rotate: expanded ? 90 : 0 }}
          transition={{ duration: STILL ? 0 : 0.2 }}
          className="text-center text-[9px] text-ink-muted/55"
        >
          ▸
        </motion.span>
      </div>

      {/* 拦截原因：不展开也先给一行，红行要能自解释 */}
      {row.reason && !expanded && (
        <div className="-mx-[13px] flex items-baseline gap-2.5 rounded-b-md px-[13px] pt-1 pb-2 pl-[85px] font-mono text-[10.5px] leading-relaxed text-[rgb(255_140_128_/_0.9)]">
          <span className="text-reject/50">└</span>
          <span>{row.reason}</span>
        </div>
      )}

      {/* ── 展开区：可拖拽改高度 ── */}
      <AnimatePresence initial={false}>
        {expanded && (
          <motion.div
            key="body"
            initial={STILL ? false : { height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={STILL ? { height: 0, opacity: 0 } : { height: 0, opacity: 0 }}
            transition={{ duration: STILL || dragging ? 0 : 0.26, ease: [0.16, 1, 0.3, 1] }}
            className="overflow-hidden"
          >
            <div className="px-[13px] pb-1">
              <div
                ref={bodyRef}
                // 拖的是可视高度上限，不是硬高度：内容本来就短时不该撑出一个
                // 空白大框（按钮还会被挤出视野），长内容才会真正被"展开"。
                style={{ maxHeight: height ?? 260 }}
                className={clsx(
                  "overflow-y-auto rounded-[7px] border border-white/[.06] bg-void/45 px-3 py-2.5",
                  dragging && "border-accent/40",
                )}
              >
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 font-mono text-[10px] text-ink-muted">
                  <span>#{row.seq}</span>
                  <span className="text-ink-mid">{row.name}</span>
                  <span>{event?.state ?? "—"}</span>
                  <span className="ml-auto tabular-nums">{row.ts}</span>
                </div>

                <Field k="参数 / 目标" v={row.target || "—"} mono />
                <Field
                  k="结果"
                  v={row.result || "—"}
                  mono
                  tone={row.kind === "reject" ? "bad" : row.kind === "ok" ? "good" : undefined}
                />

                {row.reason && (
                  <div className="mt-2 rounded-[6px] border border-reject/30 bg-reject/[.07] px-2.5 py-1.5">
                    <div className="font-mono text-[9.5px] tracking-[.12em] text-reject/80 uppercase">
                      拦截原因
                    </div>
                    <p className="mt-1 font-mono text-[11px] leading-relaxed break-all text-[rgb(255_180_170_/_0.95)]">
                      {row.reason}
                    </p>
                  </div>
                )}

                {event && (
                  <div className="mt-2">
                    <div className="font-mono text-[9.5px] tracking-[.12em] text-ink-muted/80 uppercase">
                      事件原文
                    </div>
                    <p className="mt-1 font-mono text-[11px] leading-relaxed break-all text-ink-mid">
                      {event.message}
                    </p>
                  </div>
                )}

                {hasData && (
                  <details className="mt-2">
                    <summary className="cursor-pointer font-mono text-[10px] text-ink-muted transition-colors hover:text-ink-mid">
                      data 字段（{Object.keys(data).length}）
                    </summary>
                    <pre className="mt-1.5 rounded-[6px] bg-black/35 px-2 py-1.5 font-mono text-[10px] leading-relaxed whitespace-pre-wrap break-all text-ink-muted">
                      {JSON.stringify(data, null, 2)}
                    </pre>
                  </details>
                )}
              </div>

              {/* 拖拽把手：改的是上面那块内容的 maxHeight */}
              <div
                role="separator"
                aria-label="拖动调整展开高度"
                onPointerDown={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  drag.current = { y: e.clientY, h: bodyRef.current?.offsetHeight ?? 200 };
                  setDragging(true);
                  (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
                }}
                onPointerMove={(e) => {
                  if (!drag.current) return;
                  const next = drag.current.h + (e.clientY - drag.current.y);
                  onResize(Math.min(MAX_H, Math.max(MIN_H, Math.round(next))));
                }}
                onPointerUp={() => {
                  drag.current = null;
                  setDragging(false);
                }}
                onPointerCancel={() => {
                  drag.current = null;
                  setDragging(false);
                }}
                className={clsx(
                  "group mt-1 flex h-[14px] cursor-ns-resize items-center justify-center gap-1 rounded-[4px] transition-colors",
                  dragging ? "bg-accent/10" : "hover:bg-white/[.05]",
                )}
                title="拖动调整高度"
              >
                <span className="h-[2px] w-8 rounded-full bg-white/15 transition-colors group-hover:bg-white/30" />
                <span className="font-mono text-[9px] text-ink-muted/0 transition-colors group-hover:text-ink-muted/70">
                  拖动改高度
                </span>
              </div>

              <div className="flex flex-wrap items-center gap-2 pb-2">
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    onOpenDetail();
                  }}
                  className="cursor-pointer rounded-[6px] border border-rule-hi bg-white/[.03] px-2.5 py-1 font-mono text-[10.5px] text-ink-mid transition-colors hover:border-ink-muted hover:text-ink"
                >
                  单独打开详情 ↗
                </button>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    onToggle();
                  }}
                  className="cursor-pointer rounded-[6px] border border-transparent px-2.5 py-1 font-mono text-[10.5px] text-ink-muted transition-colors hover:text-ink-mid"
                >
                  收起
                </button>
                <span className="font-mono text-[9.5px] text-ink-muted/60">
                  拖动下方横条调整这块的高度{height ? `（上限 ${height}px）` : ""}
                </span>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function Field({
  k,
  v,
  mono,
  tone,
}: {
  k: string;
  v: string;
  mono?: boolean;
  tone?: "bad" | "good";
}) {
  return (
    <div className="mt-2">
      <div className="font-mono text-[9.5px] tracking-[.12em] text-ink-muted/80 uppercase">{k}</div>
      <p
        className={clsx(
          "mt-1 leading-relaxed break-all",
          mono && "font-mono text-[11px]",
          tone === "bad" ? "text-[rgb(255_180_170_/_0.95)]" : tone === "good" ? "text-pass" : "text-ink",
        )}
      >
        {v}
      </p>
    </div>
  );
}
