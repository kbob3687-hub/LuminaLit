import { motion } from "motion/react";
import clsx from "clsx";
import type { StageView } from "../runModel";

/**
 * 3D 阶段轨道。
 *
 * 每一格的 `status` 全部来自后端事件（见 runModel.buildStages），
 * 组件本身不做任何"按索引推断"——进场时只有 queued 是 active，别的一律暗着。
 */

export function StageTrack({
  stages,
  onSelect,
}: {
  stages: StageView[];
  onSelect: (id: StageView["id"]) => void;
}) {
  return (
    <div
      className="flex items-center"
      style={{
        transformStyle: "preserve-3d",
        transform:
          "rotateX(calc(9deg + var(--my, 0) * -3deg)) rotateY(calc(var(--mx, 0) * 4deg)) translateZ(-40px)",
        transition: "transform .5s cubic-bezier(.16,1,.3,1)",
      }}
    >
      {stages.map((s, i) => {
        const done = s.status === "done";
        const active = s.status === "active";
        const bad = s.status === "bad";
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
              <button
                onClick={() => onSelect(s.id)}
                title={`查看「${s.label}」阶段详情`}
                className={clsx(
                  "group relative block w-full cursor-pointer overflow-hidden rounded-[9px] border px-4 pt-4 pb-[15px] text-left transition-colors duration-400",
                  done && "border-pass/25 bg-gradient-to-br from-pass/[.09] to-white/[.008]",
                  active &&
                    "border-accent/75 bg-gradient-to-br from-accent/20 to-accent/[.03] shadow-[0_22px_60px_-18px_rgb(108_143_255_/_0.85),0_0_0_1px_rgb(108_143_255_/_0.3)_inset,0_0_50px_-8px_rgb(108_143_255_/_0.5)]",
                  bad && "border-reject/55 bg-gradient-to-br from-reject/[.13] to-white/[.008]",
                  !done &&
                    !active &&
                    !bad &&
                    "border-rule bg-gradient-to-br from-white/[.075] to-white/[.018] opacity-55 hover:opacity-90",
                )}
              >
                <div
                  aria-hidden
                  className="pointer-events-none absolute inset-0 opacity-50 transition-opacity group-hover:opacity-80"
                  style={{
                    background:
                      "radial-gradient(240px circle at 50% 0%, rgb(255 255 255 / .10), transparent 70%)",
                  }}
                />
                <div
                  className={clsx(
                    "mb-2 flex items-center justify-between font-mono text-[9.5px] tracking-[.14em]",
                    active ? "text-accent" : bad ? "text-reject" : "text-ink-muted",
                  )}
                >
                  <span>{String(i + 1).padStart(2, "0")}</span>
                  <span className="text-[9px] opacity-80">
                    {dur(s.durationMs) ?? ""}
                    {s.count > 1 && <b className="ml-1 text-warn">×{s.count}</b>}
                  </span>
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
                        ? {
                            duration: 1.6,
                            repeat: Infinity,
                            repeatType: "reverse",
                            ease: "easeInOut",
                          }
                        : { duration: 0.8, ease: [0.16, 1, 0.3, 1] }
                    }
                  />
                </div>
              </button>
            </motion.div>
          </div>
        );
      })}
    </div>
  );
}

/** 12.4s / 1m 03s。没有耗时就返回 null，不编一个数出来。 */
export function dur(ms: number | null | undefined): string | null {
  if (ms === null || ms === undefined) return null;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)}s`;
  return `${Math.floor(s / 60)}m ${String(Math.round(s % 60)).padStart(2, "0")}s`;
}
