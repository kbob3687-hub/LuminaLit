import clsx from "clsx";
import { motion } from "motion/react";
import {
  FAILURE_LABELS,
  type RunEvent,
  type RunState,
  type RunStatus,
} from "../types";
import { actionParams, failureText, type AciTrace, type LoopView, type StageView } from "../runModel";
import { dur } from "./StageTrack";

const labelZh = (label: string | null): string =>
  label ? ((FAILURE_LABELS as Record<string, string>)[label] ?? label) : "";

/* ───────────────────────────────────────────── ACI 校验过程 */

export function AciPanel({ trace, event }: { trace: AciTrace; event: RunEvent }) {
  const blocked = trace.verdict === "block";
  // 后端 runner.py:904-922 把动作参数作为扁平字段下发（path / requirement /
  // query / env_name），没有 `request` 嵌套对象。actionParams 统一读这些字段，
  // 并兼容早期可能存在的 request 嵌套。
  const request = actionParams(event);
  // 动作事件不下发 stdout/stderr 尾段（runner.py:904-922 只写扁平参数）。
  // install / test 的 stderr_tail 在失败原因区块展示，见 runModel.lastStderr；
  // 这里若再读一次 tail，永远是空串，是个渲染不到的死分支。

  return (
    <div className="space-y-5">
      {/* 判定 */}
      <div
        className={clsx(
          "rounded-[10px] border px-4 py-3.5",
          blocked
            ? "border-reject/45 bg-gradient-to-br from-reject/[.13] to-transparent"
            : "border-pass/35 bg-gradient-to-br from-pass/[.09] to-transparent",
        )}
      >
        <div className="flex items-center gap-2.5">
          <span
            className={clsx(
              "font-mono text-[19px] leading-none font-bold",
              blocked ? "text-reject" : "text-pass",
            )}
          >
            {blocked ? "✕" : "✓"}
          </span>
          <span className="text-[14px] font-semibold text-ink">
            {blocked ? "已拦截" : "已放行"}
          </span>
          {trace.label && (
            <span className="rounded-[5px] border border-reject/45 bg-reject/[.10] px-2 py-[2px] font-mono text-[10.5px] text-reject">
              {trace.label} · {labelZh(trace.label)}
            </span>
          )}
          <span className="ml-auto font-mono text-[10.5px] text-ink-muted">
            #{trace.seq} · {new Date(trace.at).toLocaleTimeString("zh-CN", { hour12: false })}
          </span>
        </div>
        <p className="mt-2.5 font-mono text-[11.5px] leading-relaxed text-ink-mid">
          {blocked
            ? "校验未通过：动作在任何副作用之前被拒绝，workspace 与网络状态均未改变。"
            : "校验通过：动作已执行，Observation 已回给模型。"}
        </p>
      </div>

      {/* 动作目标 */}
      <div>
        <SectionTitle>
          {Object.keys(request).length ? "动作参数（后端事件里的字段）" : "动作目标"}
        </SectionTitle>
        <div className="mt-2 overflow-hidden rounded-[9px] border border-rule">
          {Object.entries(request).length === 0 ? (
            <div className="px-3 py-2.5 font-mono text-[11.5px] leading-relaxed text-ink-mid">
              <span className="text-ink">{trace.target || "—"}</span>
              <div className="mt-1 text-[10.5px] text-ink-muted">
                这个动作没有带路径、依赖串或查询参数（例如 run_smoke_test / finish_repair）；
                目标一栏是从事件的 Observation 与 message 里读回来的，不是界面编的。
              </div>
            </div>
          ) : (
            Object.entries(request).map(([k, v], i) => (
              <div
                key={k}
                className={clsx(
                  "flex items-baseline gap-3 px-3 py-2 font-mono text-[11.5px]",
                  i > 0 && "border-t border-white/[.05]",
                )}
              >
                <span className="w-[88px] shrink-0 text-ink-muted">{k}</span>
                <span className="min-w-0 flex-1 break-all text-ink">
                  {typeof v === "string" ? v : JSON.stringify(v)}
                </span>
              </div>
            ))
          )}
        </div>
      </div>

      {/* 五步流水线 */}
      <div>
        <SectionTitle>ACI 执行流水线（先验后做，顺序不可换）</SectionTitle>
        <ol className="mt-2.5">
          {trace.steps.map((s, i) => (
            <li key={s.key} className="relative pb-3.5 pl-8 last:pb-0">
              {i < trace.steps.length - 1 && (
                <span
                  aria-hidden
                  className="absolute top-[18px] left-[9px] h-[calc(100%-18px)] w-px bg-rule"
                />
              )}
              <span
                className={clsx(
                  "absolute top-0 left-0 flex size-[19px] items-center justify-center rounded-full border font-mono text-[9.5px]",
                  s.status === "pass" && "border-pass/45 bg-pass/[.10] text-pass",
                  s.status === "fail" && "border-reject/55 bg-reject/[.14] text-reject",
                  s.status === "skip" && "border-rule-hi text-ink-muted",
                )}
              >
                {s.status === "fail" ? "✕" : s.status === "skip" ? "–" : i + 1}
              </span>
              <div
                className={clsx(
                  "text-[12px] font-medium",
                  s.status === "fail"
                    ? "text-reject"
                    : s.status === "skip"
                      ? "text-ink-muted"
                      : "text-ink",
                )}
              >
                {s.title}
                {s.status === "skip" && (
                  <span className="ml-2 font-mono text-[9.5px] tracking-[.1em] text-ink-muted uppercase">
                    未执行
                  </span>
                )}
              </div>
              <div className="mt-1 font-mono text-[11px] leading-relaxed break-all text-ink-mid">
                {s.detail}
              </div>
            </li>
          ))}
        </ol>
      </div>

      {/* Observation */}
      <div>
        <SectionTitle>Observation（模型唯一能看到的东西）</SectionTitle>
        <div className="mt-2 rounded-[9px] border border-rule bg-void/60 px-3 py-2.5 font-mono text-[11.5px] leading-relaxed break-all text-ink-mid">
          <div className="text-ink">{trace.summary}</div>
          {trace.exitCode !== null && (
            <div className="mt-1 text-ink-muted">exit_code = {trace.exitCode}</div>
          )}
          {/* 动作事件没有尾段字段；命令输出原文见失败原因区块 */}
        </div>
        <p className="mt-2 text-[10.5px] leading-relaxed text-ink-muted">
          注：后端只在动作结束后回一条 Observation，没有逐步骤埋点。上面的通过/拦截
          判定由 summary 的标签反推（path_escape / replace_not_unique … 与
          <code className="mx-1 text-ink-mid">aci/errors.py</code>逐字一致），不是界面编的。
        </p>
      </div>
    </div>
  );
}

/* ───────────────────────────────────────────── 自愈回环全量 */

export function LoopPanel({
  loop,
  state,
  onOpenAction,
}: {
  loop: LoopView;
  state: RunState;
  onOpenAction: (seq: number) => void;
}) {
  if (loop.rounds.length === 0) {
    return (
      <p className="font-mono text-[11.5px] text-ink-muted">
        这次运行没有进入自愈回环——首次执行就通过了，或失败类别被判为不可修复。
      </p>
    );
  }

  return (
    <div className="space-y-4">
      <div className="rounded-[9px] border border-accent/30 bg-accent/[.06] px-3 py-2.5 font-mono text-[11px] text-ink-mid">
        已用 <b className="text-ink">{loop.rounds.length}</b> / {loop.budget} 轮预算 ·{" "}
        {state === "diagnosing" || state === "repairing" ? (
          <span className="text-[#9db4ff]">{loop.doing}</span>
        ) : (
          <span>回环已结束</span>
        )}
      </div>

      {loop.rounds.map((r, ri) => (
        <div key={ri} className="rounded-[10px] border border-rule bg-white/[.015]">
          <div className="flex items-center gap-2.5 border-b border-rule px-3.5 py-2.5">
            <span className="font-mono text-[11px] text-ink">第 {r.round} 轮</span>
            <span className="rounded-[4px] border border-warn/40 bg-warn/[.07] px-1.5 py-[1px] font-mono text-[10px] text-warn">
              {failureText(r.failureClass)}
            </span>
            <span className="font-mono text-[10px] text-ink-muted">
              {r.phase === "install" ? "安装失败" : "测试失败"}
            </span>
            {r.stopped && (
              <span
                title={r.stopped.detail}
                className="rounded-[4px] border border-reject/40 bg-reject/[.08] px-1.5 py-[1px] font-mono text-[10px] text-reject"
              >
                {r.stopped.reason === "repeated" ? "重复动作熔断" : "连续拒绝熔断"}
              </span>
            )}
            <span className="ml-auto font-mono text-[10px] text-ink-muted">
              {new Date(r.startedAt).toLocaleTimeString("zh-CN", { hour12: false })}
              {r.finishedAt ? " → 结束" : " → 进行中"}
            </span>
          </div>
          <div className="divide-y divide-white/[.045]">
            {r.actions.length === 0 && (
              <div className="px-3.5 py-2 font-mono text-[11px] text-ink-muted">尚未提出动作</div>
            )}
            {r.actions.map((a) => (
              <button
                key={a.seq}
                onClick={() => onOpenAction(a.seq)}
                className="flex w-full cursor-pointer items-baseline gap-2.5 px-3.5 py-2 text-left transition-colors hover:bg-white/[.04]"
              >
                <span
                  className={clsx(
                    "w-3 shrink-0 text-center font-mono text-[11px]",
                    a.blocked ? "text-reject" : a.ok ? "text-pass" : "text-ink-muted",
                  )}
                >
                  {a.blocked ? "✕" : a.ok ? "✓" : "⟩"}
                </span>
                <span className="w-[132px] shrink-0 truncate font-mono text-[11.5px] text-ink">
                  {a.name}
                </span>
                <span
                  className={clsx(
                    "min-w-0 flex-1 truncate font-mono text-[11px]",
                    a.blocked ? "text-reject" : "text-ink-mid",
                  )}
                >
                  {a.summary}
                </span>
                <span className="shrink-0 font-mono text-[10px] text-ink-muted">查看 →</span>
              </button>
            ))}
          </div>
          {r.stopped && (
            <div className="border-t border-rule px-3.5 py-2 font-mono text-[10.5px] leading-relaxed text-reject/85">
              这一轮提前结束：{r.stopped.detail}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

/* ───────────────────────────────────────────── 阶段详情 */

export function StagePanel({
  stage,
  events,
  state,
}: {
  stage: StageView;
  events: RunEvent[];
  state: RunState;
}) {
  const mine = events.filter((e) => e.state === stage.id);
  const active = stage.status === "active";

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-3 gap-2.5">
        <Meta k="状态" v={active ? "进行中" : stage.status === "done" ? "已完成" : stage.status === "bad" ? "失败" : "未开始"} />
        <Meta k="耗时" v={dur(stage.durationMs) ?? "—"} />
        <Meta k="进入次数" v={String(stage.count || 1)} />
      </div>

      {mine.length === 0 ? (
        <p className="font-mono text-[11.5px] text-ink-muted">
          这个阶段还没有任何事件。{active ? "正在执行。" : `当前 run 处于 ${state}。`}
        </p>
      ) : (
        <div className="divide-y divide-white/[.045] overflow-hidden rounded-[9px] border border-rule">
          {mine.map((e) => (
            <div key={e.sequence} className="px-3 py-2.5">
              <div className="flex items-baseline gap-2.5 font-mono text-[10.5px] text-ink-muted">
                <span>{new Date(e.timestamp).toLocaleTimeString("zh-CN", { hour12: false })}</span>
                <span className="rounded-[4px] border border-rule-hi px-1.5 text-ink-mid">
                  {e.kind}
                </span>
                <span className="ml-auto">#{e.sequence}</span>
              </div>
              <div className="mt-1.5 font-mono text-[11.5px] leading-relaxed break-all text-ink">
                {e.message}
              </div>
              {Object.keys(e.data ?? {}).length > 0 && (
                <pre className="mt-1.5 max-h-32 overflow-y-auto rounded-[6px] bg-void/60 px-2 py-1.5 font-mono text-[10.5px] whitespace-pre-wrap text-ink-muted">
                  {JSON.stringify(e.data, null, 2)}
                </pre>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ───────────────────────────────────────────── 执行计划 */

export function PlanPanel({
  status,
  events,
}: {
  status: RunStatus | null;
  events: RunEvent[];
}) {
  const result = status?.result;
  // 后端 runner.py:605-611 在 planning 阶段下发 kind="plan" 事件，
  // data 是 RunPlan.model_dump()；/events 按原样返回，不按 kind 过滤。
  // 旧实现只读 status.result，运行中打开「查看计划」时五行全是「运行中 / —」。
  const plan = [...events].reverse().find((e) => e.kind === "plan")?.data as
    | Record<string, unknown>
    | undefined;
  const command = Array.isArray(plan?.command)
    ? (plan?.command as unknown[]).map(String).join(" ")
    : null;
  const rows: [string, string][] = [
    ["command", command ?? "—"],
    ["python_version", typeof plan?.python_version === "string" ? plan.python_version : "—"],
    [
      "expected_artifact",
      typeof plan?.expected_artifact === "string" ? plan.expected_artifact : "—",
    ],
    ["source", typeof plan?.source === "string" ? plan.source : "—"],
    ["resolved_revision", result?.resolved_revision ?? "—"],
    ["outcome", result?.outcome ?? "运行中"],
    ["verified", result ? String(result.verified) : "—"],
    ["attempts", result ? String(result.attempts) : "—"],
    ["failure_category", result?.failure_category ? failureText(result.failure_category) : "—"],
  ];
  return (
    <div className="space-y-3">
      <div className="divide-y divide-white/[.045] overflow-hidden rounded-[9px] border border-rule">
        {rows.map(([k, v]) => (
          <KV key={k} k={k} v={v} />
        ))}
      </div>
      <p className="text-[10.5px] leading-relaxed text-ink-muted">
        前四行来自 planning 阶段的 plan 事件（后端同时落盘 run_plan.json），运行中即可查看；
        outcome / verified / attempts / failure_category 要到收尾才有值。
      </p>
    </div>
  );
}

function KV({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex items-baseline gap-3 px-3 py-2">
      <span className="w-[132px] shrink-0 font-mono text-[10.5px] text-ink-muted">{k}</span>
      <span className="min-w-0 flex-1 break-all font-mono text-[11.5px] text-ink">{v}</span>
    </div>
  );
}

function Meta({ k, v }: { k: string; v: string }) {
  return (
    <div className="rounded-[8px] border border-rule bg-white/[.02] px-3 py-2">
      <div className="font-mono text-[9.5px] tracking-[.14em] text-ink-muted uppercase">{k}</div>
      <div className="mt-1 font-mono text-[13px] text-ink">{v}</div>
    </div>
  );
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      className="font-mono text-[9.5px] tracking-[.18em] text-ink-muted uppercase"
    >
      {children}
    </motion.div>
  );
}
