import { useEffect, useRef, useState } from "react";
import { fetchHandoff, getRepairEvidence } from "../api";
import { replayConclusion } from "../runModel";
import type { RepairEvidence as Evidence, RunResult } from "../types";

const REPAIR_REVISION = "d4e64c487f03065d96f4bb2b8b9113408c2b357c";
const ACTIONS: Record<string, string> = {
  read_file: "读取源码", search_text: "搜索代码", replace_exact: "修改源码",
  install_requirement: "安装依赖", run_smoke_test: "试运行", finish_repair: "结束修复",
  set_environment: "设置运行环境",
};

function HandoffDownload({ runId, evidence }: { runId: string; evidence: Evidence }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const active = useRef<AbortController | null>(null);
  useEffect(() => () => { active.current?.abort(); }, [runId]);
  const download = async () => {
    if (active.current && !active.current.signal.aborted) return;
    const controller = new AbortController();
    active.current = controller;
    setBusy(true);
    setError(null);
    try {
      const blob = await fetchHandoff(runId, controller.signal);
      if (controller.signal.aborted) return;
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "lorenz-repair-handoff.zip";
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (cause) {
      if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "下载失败，请重试。");
    } finally {
      if (!controller.signal.aborted) { active.current = null; setBusy(false); }
    }
  };
  return <>
    <button type="button" disabled={busy || !evidence.handoff.available} onClick={() => void download()} className="mt-4 cursor-pointer rounded-lg bg-accent px-5 py-2.5 text-sm font-semibold text-white hover:brightness-110 disabled:cursor-default disabled:opacity-50">{busy ? "正在校验并下载…" : "下载 Windows 队友交接包"}</button>
    <p className="mt-2 text-xs leading-6 text-ink-muted">{evidence.handoff.available ? `约 ${Math.round((evidence.handoff.size_bytes ?? 0) / 1_000_000)} MB，包含固定环境。无需模型密钥。` : "本次尚未准备独立 Windows 交接包，可在下方下载已有复跑材料。"}</p>
    {error && <p role="alert" className="mt-3 text-sm text-reject">{error}</p>}
  </>;
}

export function RepairEvidence({ result, runId }: { result: RunResult | null; runId: string | null }) {
  const [evidence, setEvidence] = useState<Evidence | null>(null);
  const [error, setError] = useState(false);
  const relevant = result?.resolved_revision === REPAIR_REVISION;
  useEffect(() => {
    let alive = true;
    setEvidence(null);
    setError(false);
    if (relevant && runId) void getRepairEvidence(runId)
      .then(value => { if (alive && value.run_id === runId) setEvidence(value); })
      .catch(() => { if (alive) setError(true); });
    return () => { alive = false; };
  }, [relevant, runId]);
  if (!relevant || !runId) return null;
  if (!evidence) return <p role="status" className="my-6 text-sm text-ink-mid">{error ? "暂时无法读取修复过程，下面仍保留本次验收结果。刷新后可重试。" : "正在读取原始报错与修复过程…"}</p>;
  const verified = result?.verification?.status === "passed";
  const repaired = verified && evidence.changed_files.length > 0;
  const replay = replayConclusion(result?.replay);
  const shortError = evidence.original_error?.trim().split("\n").at(-1);
  return (
    <section aria-label="真实修复与交接" className="my-7 min-w-0 overflow-hidden rounded-xl border border-rule-hi bg-surface">
      <header className="px-5 py-5 sm:px-6">
        <p className="text-xs font-medium text-accent">PySINDy v1.7 · 已记录的修复过程</p>
        <h2 className="mt-2 text-2xl font-semibold tracking-tight text-ink">从一次真实报错，到一份可交接的修复</h2>
        <p className="mt-3 max-w-[70ch] text-sm leading-7 text-ink-mid">旧版科研代码调用了已失效的依赖接口。这里保留实际报错、模型执行的修改和重验结果；队友的独立电脑验证另行记录。</p>
        <a className="mt-2 inline-block text-xs text-accent underline underline-offset-4" target="_blank" rel="noreferrer" href={`https://github.com/dynamicslab/pysindy/tree/${evidence.source_revision}`}>查看本次固定的上游源码</a>
        {result?.verification?.code_under_test?.includes("v1.7.5") && <details className="mt-3 text-xs leading-6 text-ink-muted"><summary className="cursor-pointer">源码版本与历史记录说明</summary><p className="mt-2">实际运行的是上方固定提交对应的 v1.7。原始验收记录中的版本文字曾误标为 v1.7.5，历史文件保留原样；提交号与源码哈希以冻结清单为准。</p></details>}
      </header>
      <dl className="grid border-y border-rule sm:grid-cols-3">
        {[
          { label: "这次修复", value: repaired ? "已修改源码，指标验收通过" : "尚未验证修复成功", tone: repaired ? "text-pass" : "text-warn" },
          { label: "干净环境复跑", value: replay.label, tone: replay.tone === "success" ? "text-pass" : "text-warn" },
          { label: "队友独立电脑", value: "待队友反馈", tone: "text-warn" },
        ].map(item => <div key={item.label} className="border-b border-rule px-5 py-4 last:border-0 sm:border-r sm:border-b-0 sm:px-6"><dt className="text-xs text-ink-mid">{item.label}</dt><dd className={`mt-2 text-sm font-medium leading-6 ${item.tone}`}>{item.value}</dd></div>)}
      </dl>
      <div className="grid min-w-0 gap-6 px-5 py-6 sm:px-6 lg:grid-cols-2">
        <div className="min-w-0">
          <h3 className="text-base font-semibold">原始代码为什么没跑起来</h3>
          <p className="mt-2 text-sm leading-7 text-ink-mid">{evidence.original_error ? "下面是修改之前，真实执行留下的异常。" : "本次未记录修改前的异常，不能补充推测。"}</p>
          {shortError && <pre className="mt-3 whitespace-pre-wrap break-words border-l-2 border-reject bg-inset px-4 py-3 font-mono text-xs leading-6 text-ink [overflow-wrap:anywhere]">{shortError}</pre>}
          {evidence.original_error && <details className="mt-3 text-xs"><summary className="cursor-pointer text-ink-mid">展开原始错误堆栈</summary><pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap break-words bg-inset p-3 font-mono leading-6 text-ink-muted">{evidence.original_error}</pre></details>}
        </div>
        <div className="min-w-0">
          <h3 className="text-base font-semibold">AI 实际改了哪里</h3>
          <p className="mt-2 text-sm leading-7 text-ink-mid">{evidence.changed_files.length ? `已执行的源码修改：${evidence.changed_files.length} 个文件。` : "尚未记录执行成功的源码修改。"}验收脚本和系数产物受保护。</p>
          {evidence.changed_files.map(path => <p key={path} className="mt-3 break-all font-mono text-xs text-ink">{path}</p>)}
          <p className="mt-3 text-xs leading-6 text-ink-muted">{evidence.model ? `模型：${evidence.model}。` : "模型名称未记录。"}{evidence.decisions_count != null && ` 已保存 ${evidence.decisions_count} 次模型 JSON 决策。`}</p>
          <p className="mt-1 text-xs leading-6 text-ink-muted">当前结论适用于本次 Lorenz 子实验，补丁的其他使用场景仍需审查。</p>
        </div>
      </div>
      <details className="border-t border-rule px-5 py-4 sm:px-6">
        <summary className="cursor-pointer text-sm font-medium text-ink">查看 AI 的源码补丁</summary>
        {evidence.diff ? <pre aria-label="AI 源码补丁" className="mt-4 max-h-[420px] overflow-auto rounded-lg bg-void p-4 font-mono text-xs leading-6">{evidence.diff.split("\n").map((line, i) => <span key={i} className={`block min-h-6 ${line.startsWith("+") ? "text-pass" : line.startsWith("-") ? "text-reject" : "text-ink-mid"}`}>{line || " "}</span>)}</pre> : <p className="mt-3 text-sm text-ink-muted">源码补丁暂不可读取，可在下方下载已保存的运行补丁。</p>}
      </details>
      <details className="border-t border-rule px-5 py-4 sm:px-6">
        <summary className="cursor-pointer text-sm font-medium">查看实际执行动作（{evidence.actions.length} 条）</summary>
        <p className="mt-3 text-xs leading-6 text-ink-muted">按原始事件顺序展示。被拒绝的动作也保留，不计为成功修改。</p>
        <ol className="mt-3 divide-y divide-rule">{evidence.actions.map(action => <li key={action.sequence} className="flex min-w-0 items-start gap-3 py-3 text-xs leading-6"><span className="w-5 shrink-0 text-ink-muted">{action.sequence}</span><div className="min-w-0 flex-1"><p className="text-ink">{ACTIONS[action.action] ?? action.action}</p><p className="break-all text-ink-muted">{action.path ?? action.summary}</p></div><span className={`shrink-0 ${action.ok ? "text-pass" : "text-warn"}`}>{action.ok ? "已执行" : "未执行成功"}</span></li>)}</ol>
      </details>
      <div className="border-t border-rule px-5 py-5 sm:px-6">
        <h3 className="text-base font-semibold">把这份修复交给另一位同学</h3>
        <p className="mt-2 max-w-[70ch] text-sm leading-7 text-ink-mid">Windows 队友解压交接包，启动 Docker Desktop，再执行下面的命令。脚本会重新计算结果，并生成反馈文件。</p>
        <pre className="mt-3 overflow-auto rounded-lg bg-inset px-4 py-3 font-mono text-xs leading-6 text-ink">python replay.py --participant 队友</pre>
        <p className="mt-2 text-xs leading-6 text-ink-muted">需要 Python 3.11+ 和 Docker Desktop 的 Linux 容器模式。把 receiver-*/feedback.json 发回，再由队友确认是在独立电脑运行。</p>
        <HandoffDownload runId={runId} evidence={evidence} />
        <p className="mt-4 text-xs leading-6 text-warn">当前仍待队友反馈。本机的新容器复跑通过，不等于另一台电脑已通过。</p>
      </div>
    </section>
  );
}
