import { useEffect, useRef, useState } from "react";
import { ApiError, confirmPreflight, createPreflight } from "../api";
import type { PreflightResult } from "../types";

interface Props {
  initialPreflight: PreflightResult;
  onBack: () => void;
  onStarted: (runId: string) => void;
}

const CHECK_LABELS = { passed: "已核对", warning: "注意边界", not_checked: "尚未检查" };

export function PreflightScene({ initialPreflight, onBack, onStarted }: Props) {
  const [preflight, setPreflight] = useState(initialPreflight);
  const [timeout, setTimeoutValue] = useState(String(initialPreflight.timeout_seconds));
  const [repairs, setRepairs] = useState(String(initialPreflight.max_repairs));
  const [phase, setPhase] = useState<"idle" | "checking" | "confirming">("idle");
  const [error, setError] = useState<string | null>(null);
  const [needsRefresh, setNeedsRefresh] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [expired, setExpired] = useState(() => Date.now() >= Date.parse(initialPreflight.expires_at));
  const busy = useRef(false);
  const alive = useRef(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const dirty = Number(timeout) !== preflight.timeout_seconds || Number(repairs) !== preflight.max_repairs;
  const valid = timeout !== "" && Number.isInteger(Number(timeout)) && Number(timeout) >= 30
    && Number(timeout) <= 900 && repairs !== "" && Number.isInteger(Number(repairs))
    && Number(repairs) >= 0 && Number(repairs) <= 5;
  const mustRecheck = needsRefresh || (expired && !uncertain && !preflight.run_id);

  useEffect(() => {
    alive.current = true;
    heading.current?.focus();
    return () => { alive.current = false; };
  }, []);

  useEffect(() => {
    const remaining = Date.parse(preflight.expires_at) - Date.now();
    setExpired(remaining <= 0);
    if (remaining <= 0) return;
    const timer = window.setTimeout(() => setExpired(true), remaining);
    return () => window.clearTimeout(timer);
  }, [preflight.expires_at]);

  async function recheck() {
    if (busy.current || uncertain || !valid) return;
    busy.current = true;
    setPhase("checking");
    setError(null);
    try {
      const next = await createPreflight({
        target_id: preflight.target_id, timeout_seconds: Number(timeout), max_repairs: Number(repairs),
      });
      if (!alive.current) return;
      setPreflight(next);
      setNeedsRefresh(false);
    } catch (err) {
      if (alive.current) setError(`重新体检失败：${err instanceof Error ? err.message : String(err)}`);
    } finally {
      busy.current = false;
      if (alive.current) setPhase("idle");
    }
  }

  async function confirm() {
    if (busy.current || dirty || !valid || mustRecheck) return;
    busy.current = true;
    setPhase("confirming");
    setError(null);
    try {
      const result = await confirmPreflight(preflight);
      if (alive.current) onStarted(result.run_id);
    } catch (err) {
      if (!alive.current) return;
      const refreshRequired = err instanceof ApiError && [
        "preflight_expired", "confirmation_mismatch", "preflight_not_found",
      ].includes(err.code ?? "");
      setNeedsRefresh(refreshRequired);
      setUncertain((previous) => !refreshRequired && (previous || !(err instanceof ApiError) || err.status >= 500));
      setError(`确认未完成：${err instanceof Error ? err.message : String(err)}`);
    } finally {
      busy.current = false;
      if (alive.current) setPhase("idle");
    }
  }

  return (
    <main className="run-workspace h-full overflow-y-auto px-5 py-8 sm:px-8">
      <div className="mx-auto max-w-[1060px]">
        <button type="button" onClick={onBack} disabled={phase !== "idle"}
          className="mb-8 cursor-pointer text-sm text-ink-mid hover:text-ink disabled:opacity-50">
          返回任务选择
        </button>
        <header className="mb-8">
          <p className="mb-2 text-sm text-ink-mid">{preflight.target_id}</p>
          <h1 ref={heading} tabIndex={-1} className="text-3xl font-semibold tracking-tight outline-none">确认这次试跑</h1>
          <p className="mt-3 max-w-[65ch] text-sm leading-7 text-ink-mid">
            这是一份静态配置核对单，还没有运行代码。请先看清执行方式和验证范围，再决定是否开始。
          </p>
        </header>

        <div className="grid gap-8 lg:grid-cols-[1.15fr_1fr]">
          <section aria-labelledby="plan-title">
            <h2 id="plan-title" className="mb-4 text-lg font-medium">将执行的计划</h2>
            <dl className="space-y-5 rounded-xl border border-rule bg-surface-hi p-5 text-sm">
              <div><dt className="mb-1.5 text-ink-mid">代码仓库</dt><dd className="break-all leading-6">{preflight.repo_url}</dd></div>
              <div><dt className="mb-1.5 text-ink-mid">固定版本</dt><dd className="break-all font-mono text-xs leading-6">{preflight.plan.resolved_revision}</dd></div>
              <div><dt className="mb-1.5 text-ink-mid">执行命令</dt><dd className="overflow-x-auto rounded-md bg-void px-3 py-3 font-mono text-sm"><code>{preflight.plan.command.join(" ")}</code></dd></div>
              <div className="grid grid-cols-2 gap-4">
                <div><dt className="mb-1.5 text-ink-mid">Python</dt><dd>{preflight.plan.python_version}</dd></div>
                <div><dt className="mb-1.5 text-ink-mid">预期产物</dt><dd className="break-all">{preflight.plan.expected_artifact ?? "未要求产物"}</dd></div>
              </div>
            </dl>
            <form onSubmit={(event) => { event.preventDefault(); void recheck(); }} className="mt-6">
              <fieldset disabled={phase !== "idle" || uncertain}>
                <legend className="mb-3 text-sm font-medium">运行设置</legend>
                <div className="grid grid-cols-2 gap-4">
                  <label className="text-sm text-ink-mid">单步超时（秒）
                    <input aria-label="单步超时（秒）" type="number" min={30} max={900} step={1} required
                      value={timeout} onChange={(e) => setTimeoutValue(e.target.value)}
                      className="mt-2 w-full rounded-md border border-rule bg-surface px-3 py-2 text-ink focus-visible:outline-2 focus-visible:outline-accent" />
                  </label>
                  <label className="text-sm text-ink-mid">最多修复轮数
                    <select aria-label="最多修复轮数" value={repairs} onChange={(e) => setRepairs(e.target.value)}
                      className="mt-2 w-full rounded-md border border-rule bg-surface px-3 py-2 text-ink focus-visible:outline-2 focus-visible:outline-accent">
                      {[0, 1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n === 0 ? "0（不自动修复）" : n}</option>)}
                    </select>
                  </label>
                </div>
                <p className="mt-3 text-xs leading-6 text-ink-mid">单步超时不是整个任务的总预算；设置改变后，需要重新体检生成新的确认单。</p>
                <button type="submit" disabled={!valid} className="mt-3 cursor-pointer rounded-md border border-rule-hi px-4 py-2 text-sm hover:bg-surface-hi disabled:opacity-50">
                  {phase === "checking" ? "正在重新体检…" : "重新体检"}
                </button>
              </fieldset>
            </form>
          </section>

          <section aria-labelledby="checks-title">
            <h2 id="checks-title" className="mb-4 text-lg font-medium">检查依据</h2>
            <ul className="divide-y divide-rule border-y border-rule">
              {preflight.checks.map((check) => (
                <li key={check.name} className="py-4">
                  <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-sm">
                    <h3 className="font-medium">{check.name}</h3>
                    <span className={check.status === "warning" ? "text-warn" : "text-ink-mid"}>{CHECK_LABELS[check.status]}</span>
                  </div>
                  <p className="break-words text-sm leading-6 text-ink-mid [overflow-wrap:anywhere]">{check.detail}</p>
                  <p className="mt-1 text-xs leading-6 text-ink-mid">依据：{check.source}</p>
                </li>
              ))}
            </ul>
          </section>
        </div>

        <section aria-labelledby="limits-title" className="mt-8 border-l-2 border-warn/60 bg-warn/[.04] px-5 py-4">
          <h2 id="limits-title" className="mb-2 text-sm font-medium text-warn">本次还不能证明什么</h2>
          <ul className="list-disc space-y-1 pl-4 text-sm leading-7 text-ink-mid">
            {preflight.limitations.map((limitation) => <li key={limitation}>{limitation}</li>)}
          </ul>
        </section>
        <div aria-live="polite" className="mt-5 text-sm leading-7">
          {dirty && <p className="text-warn">设置已改变，请重新体检后确认。</p>}
          {mustRecheck && <p className="text-warn">这份确认单已失效，请重新体检。</p>}
          {uncertain && <p className="text-warn">服务可能已接收确认，请重试同一份确认单获取任务编号；暂不重新体检，以免重复创建任务。</p>}
          {error && <p role="alert" className="text-reject">{error}</p>}
        </div>
        <footer className="mt-5 flex flex-col gap-4 border-t border-rule pt-5 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-xs leading-6 text-ink-mid">
            确认单有效至 {new Date(preflight.expires_at).toLocaleTimeString()}<br />
            确认后才会下载代码、安装依赖并执行任务。
          </p>
          <button type="button" onClick={() => void confirm()}
            disabled={phase !== "idle" || !valid || dirty || mustRecheck}
            className="cursor-pointer rounded-lg bg-accent px-6 py-3 text-sm font-semibold text-white hover:bg-accent/85 disabled:cursor-not-allowed disabled:opacity-45">
            {phase === "confirming" ? "正在确认…" : uncertain ? "重试同一确认" : "确认并开始试跑"}
          </button>
        </footer>
      </div>
    </main>
  );
}
