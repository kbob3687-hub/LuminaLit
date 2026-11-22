import clsx from "clsx";
import type { AcceptanceLayer, AcceptanceView } from "../runModel";

/**
 * 三层验收面板：L1 程序跑通 / L2 产物合格 / L3 指标达标。
 *
 * 三层不是后端下发的，是从事件推出来的（推导规则见 `runModel.buildAcceptance`）。
 * 因此每一层都必须把「依据」和「缺什么」同时摆出来：只写「通过」而不写
 * 依据，就等于要求评委相信前端；只写「未下发」而不写缺什么，就成了一片糊。
 *
 * 第四种状态 `unknown` 是这版的重点：后端没有指标字段时，界面显式说
 * 「未下发」，而不是把退出码 0 顺势说成「指标达标」。
 */

const TONE: Record<
  AcceptanceLayer["status"],
  { dot: string; text: string; badge: string; label: string }
> = {
  pass: {
    dot: "bg-pass",
    text: "text-pass",
    badge: "border-pass/40 bg-pass/10",
    label: "通过",
  },
  fail: {
    dot: "bg-reject",
    text: "text-reject",
    badge: "border-reject/45 bg-reject/10",
    label: "未通过",
  },
  unknown: {
    dot: "bg-warn",
    text: "text-warn",
    badge: "border-warn/35 bg-warn/10",
    label: "后端未下发",
  },
  not_reached: {
    dot: "bg-ink-muted",
    text: "text-ink-muted",
    badge: "border-rule",
    label: "未评估",
  },
};

function TamperBadge({ count }: { count: number }) {
  if (count <= 0) return null;
  return (
    <span
      className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-pass/40 bg-pass/10 px-2 py-[2px] text-[10px] text-pass"
      title="模型尝试改写验收脚本或期望产物，被 ACI 的 protected_file 拦截。拦截不改变本次运行结论，但说明这条修复轨迹里出现过 Reward Hacking 动作。"
    >
      <span className="size-1.5 rounded-full bg-pass" />
      改写判据 {count} 次被拦
    </span>
  );
}

export function AcceptancePanel({ view }: { view: AcceptanceView }) {
  return (
    <section className="overflow-hidden rounded-[11px] border border-rule">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-rule bg-surface/70 px-4 py-2.5">
        <div>
          <div className="text-[10px] tracking-[.18em] text-ink-muted uppercase">
            Independent Acceptance
          </div>
          <div className="mt-0.5 text-[12.5px] text-ink-mid">
            独立指标验收 · L1 程序跑通 / L2 产物合格 / L3 指标达标
          </div>
        </div>
        <TamperBadge count={view.tamperAttempts} />
      </header>

      <ol className="divide-y divide-rule">
        {view.layers.map((layer) => {
          const tone = TONE[layer.status];
          return (
            <li key={layer.id} className="flex gap-3 px-4 py-3">
              <span className="mt-0.5 inline-flex size-6 shrink-0 items-center justify-center rounded-[6px] border border-rule font-mono text-[10.5px] text-ink-mid">
                {layer.id}
              </span>

              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                  <span className="text-[13px] text-ink">{layer.title}</span>
                  <span
                    className={clsx(
                      "inline-flex items-center gap-1.5 rounded-full border px-1.5 py-[1px] text-[10px]",
                      tone.badge,
                      tone.text,
                    )}
                  >
                    <span className={clsx("size-1.5 rounded-full", tone.dot)} />
                    {tone.label}
                  </span>
                </div>

                <p className="mt-1 text-[11.5px] leading-relaxed text-ink-mid">{layer.detail}</p>

                {layer.evidence.length > 0 && (
                  <ul className="mt-1.5 space-y-0.5">
                    {layer.evidence.map((line) => (
                      <li
                        key={line}
                        className="font-mono text-[10.5px] leading-relaxed break-all text-ink-muted"
                      >
                        {line}
                      </li>
                    ))}
                  </ul>
                )}

                {layer.caveat && (
                  <p className="mt-1.5 border-l-2 border-warn/30 pl-2 text-[10.5px] leading-relaxed text-ink-muted">
                    {layer.caveat}
                  </p>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

/** 紧凑版：塞进运行页状态条，只给状态不给依据。 */
export function AcceptanceStrip({ view }: { view: AcceptanceView }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {view.layers.map((layer) => {
        const tone = TONE[layer.status];
        return (
          <span
            key={layer.id}
            title={`${layer.title}：${layer.detail}`}
            className={clsx(
              "inline-flex cursor-default items-center gap-1.5 rounded-full border px-2 py-[2px] text-[10.5px]",
              tone.badge,
            )}
          >
            <span className={clsx("size-1.5 rounded-full", tone.dot)} />
            <span className="text-ink-mid">{layer.id}</span>
            <span className={tone.text}>{tone.label}</span>
          </span>
        );
      })}
      <TamperBadge count={view.tamperAttempts} />
    </div>
  );
}
