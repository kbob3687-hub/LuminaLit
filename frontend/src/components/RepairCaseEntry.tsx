import { useEffect, useState } from "react";
import { recordedRepairCase } from "../api";
import type { RecordedRepairCase } from "../types";

export function RepairCaseEntry({ onView, onRetry, demo }: {
  onView: (runId: string) => void;
  onRetry?: () => void;
  demo?: boolean;
}) {
  const [record, setRecord] = useState<RecordedRepairCase | null>(null);
  const [loading, setLoading] = useState(!demo);
  useEffect(() => {
    let alive = true;
    if (!demo) void recordedRepairCase()
      .then(value => { if (alive && /^[a-f0-9]{32}$/.test(value.run_id)) setRecord(value); })
      .catch(() => { /* Do not replace unavailable history with example evidence. */ })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [demo]);
  return (
    <section aria-label="真实 AI 修复案例" className="mb-9 overflow-hidden rounded-xl border border-rule-hi bg-surface">
      <div className="px-5 py-5 sm:px-6 sm:py-6">
        <p className="mb-3 text-xs font-medium text-accent">真实源码修复记录</p>
        <h2 className="max-w-[26ch] text-2xl font-semibold leading-snug tracking-tight text-ink">代码报错之后，AI 做了什么？</h2>
        <p className="mt-3 max-w-[66ch] text-sm leading-7 text-ink-mid">PySINDy 的历史源码调用了已失效的依赖接口。跟着一份实际运行记录，查看原始报错、AI 补丁和重新计算的实验结果。</p>
        <ol className="mt-5 grid grid-cols-2 gap-x-5 gap-y-3 border-y border-rule py-4 text-xs leading-6 text-ink-mid sm:grid-cols-4">
          {["原始报错", "模型修改源码", "独立验收与复跑", "交给队友验证"].map((step, i) => <li key={step}><span className="mr-2 text-ink-muted">{i + 1}.</span>{step}</li>)}
        </ol>
        <div className="mt-5 flex flex-wrap items-center gap-4">
          <button type="button" disabled={!record || demo} onClick={() => record && onView(record.run_id)} className="cursor-pointer rounded-lg bg-accent px-5 py-2.5 text-sm font-semibold text-white hover:brightness-110 disabled:cursor-default disabled:opacity-45">{loading ? "正在查找运行记录…" : "查看这次真实修复"}</button>
          {onRetry && !demo && <button type="button" onClick={onRetry} className="cursor-pointer rounded text-sm text-ink-mid underline underline-offset-4 hover:text-ink">重新发起修复</button>}
        </div>
        <p className="mt-3 text-xs leading-6 text-ink-muted">{record && !demo ? "查看的是已完成的真实历史运行，不会启动模型或创建任务。队友的独立电脑验收仍待反馈。" : loading ? "只读取后端已有记录。" : "当前服务尚无可读取的真实修复记录。"}</p>
      </div>
    </section>
  );
}
