import { useEffect, useRef, useState } from "react";
import clsx from "clsx";
import { ApiError, getPatch, getReport } from "../api";
import type { RunStatus } from "../types";
import { VerificationPanel } from "./VerificationPanel";
import { guidanceOf } from "./RunSceneParts";

/**
 * 结果报告面板：把后端落盘的三个文件搬到界面上。
 *
 * 三个端点各管一件事，不要混：
 *   GET /api/runs/{id}/report  → report.md 原文（收尾才落盘，运行中 404）
 *   GET /api/runs/{id}/patch   → patch.diff（无改动回空串，不是 404）
 *   GET /api/runs/{id}         → result.json 里的字段（已在 status 里）
 *
 * 首屏展示后端 guidance；原始表格、JSON、补丁都收在技术文件中。
 * 下载只使用真实取得的文本，不改写内容，也不为回放生成假文件。
 */

type Tab = "report" | "patch" | "json";

const TABS: { id: Tab; label: string; file: (rid: string) => string }[] = [
  { id: "report", label: "结果报告", file: (r) => `paperharness-${r.slice(0, 8)}-report.md` },
  { id: "patch", label: "补丁", file: (r) => `paperharness-${r.slice(0, 8)}-patch.diff` },
  { id: "json", label: "结果 JSON", file: (r) => `paperharness-${r.slice(0, 8)}-result.json` },
];

export function ReportPanel({ runId, status }: { runId: string | null; status: RunStatus | null }) {
  // 显式文件直链展开技术区，普通入口默认只展示可读结论。
  const [tab, setTab] = useState<Tab>(() => {
    const t = new URLSearchParams(window.location.search).get("tab");
    return t === "patch" || t === "json" ? t : "report";
  });
  const [filesOpen, setFilesOpen] = useState(() => {
    const t = new URLSearchParams(window.location.search).get("tab");
    return t === "patch" || t === "json";
  });
  const result = status?.result ?? null;
  const terminal = Boolean(status && ["succeeded", "failed", "cancelled"].includes(status.state));
  const guidance = status ? guidanceOf(status, false) : undefined;
  const tone = guidance?.tone ?? "neutral";
  // result.json 直接来自轮询，不重新请求或改写结果字段。
  const jsonText = result ? JSON.stringify(result, null, 2) : "";

  return (
    <div className="space-y-4">
      <div
        className={clsx(
          "rounded-[10px] border px-4 py-3.5",
          tone === "success" ? "border-pass/40 bg-pass/[.06]"
            : tone === "error" ? "border-reject/40 bg-reject/[.06]"
              : tone === "warning" ? "border-warn/40 bg-warn/[.06]"
                : "border-rule bg-white/[.02]",
        )}
      >
        <h3 className={clsx(
          "text-[15px] font-semibold",
          tone === "success" ? "text-pass" : tone === "error" ? "text-reject"
            : tone === "warning" ? "text-warn" : "text-ink",
        )}>
          {guidance?.title ?? (terminal ? (result ? "运行已结束" : "正在整理运行报告") : "运行结果将在结束后生成")}
        </h3>
        {guidance?.detail && (
          <p className="mt-2 text-[13px] leading-relaxed text-ink-mid">{guidance.detail}</p>
        )}
        {guidance?.repair_summary && (
          <p className="mt-2 text-[13px] leading-relaxed text-ink-mid">
            <span className="font-medium text-ink">修复情况：</span>{guidance.repair_summary}
          </p>
        )}
        {!result && (
          <p className="mt-2 text-[12px] leading-relaxed text-ink-muted">
            {terminal ? "运行已结束，正在整理最终结果和报告。" : "报告会在运行结束后生成，你可以先查看当前进度。"}
          </p>
        )}
        {guidance && guidance.next_steps.length > 0 && (
          <div className="mt-4 border-t border-rule pt-3">
            <h4 className="text-[13px] font-medium text-ink">下一步</h4>
            <ul className="mt-2 list-disc space-y-1.5 pl-5 text-[13px] leading-relaxed text-ink-mid">
              {guidance.next_steps.map((step, i) => <li key={i}>{step}</li>)}
            </ul>
          </div>
        )}
      </div>

      {result && <VerificationPanel verification={result.verification} runId={runId === result.run_id ? runId : null} />}

      <details
        open={filesOpen}
        onToggle={(event) => setFilesOpen(event.currentTarget.open)}
        className="rounded-[9px] border border-rule bg-void/40"
      >
        <summary className="cursor-pointer px-3.5 py-3 text-[13px] text-ink-mid hover:text-ink">
          技术文件与下载
        </summary>
        {filesOpen && (
          <div className="space-y-3 border-t border-rule p-3.5">
            <div className="flex flex-wrap items-center gap-1.5">
              {TABS.map((t) => (
                <button
                  key={t.id}
                  onClick={() => setTab(t.id)}
                  aria-pressed={tab === t.id}
                  className={clsx(
                    "cursor-pointer rounded-[5px] border px-3 py-1.5 font-mono text-[11px] transition-colors",
                    tab === t.id ? "border-rule-hi bg-surface-hi text-ink"
                      : "border-transparent text-ink-muted hover:text-ink-mid",
                  )}
                >
                  {t.label}
                </button>
              ))}
            </div>
            {/* 换任务/标签/进入终态/结果落盘都重建文件视图，不缓存运行中的空补丁。 */}
            <TechnicalFile
              key={`${runId}:${tab}:${terminal}:${Boolean(result)}`}
              runId={runId}
              tab={tab}
              terminal={terminal}
              jsonText={jsonText}
            />
            <p className="font-mono text-[10px] leading-relaxed text-ink-muted">
              文件由后端写入 <code className="text-ink-mid">runs/&lt;run_id&gt;/</code> 目录；
              界面只做展示与下载，不改写内容。
            </p>
          </div>
        )}
      </details>
    </div>
  );
}

/** 每个文件视图独立持有请求与错误，切换后旧响应不能写入新标签。 */
function TechnicalFile({ runId, tab, terminal, jsonText }: {
  runId: string | null;
  tab: Tab;
  terminal: boolean;
  jsonText: string;
}) {
  const [doc, setDoc] = useState<string | null>(null);
  const [loading, setLoading] = useState(Boolean(runId && tab !== "json"));
  const [err, setErr] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState<string | null>(null);
  const mounted = useRef(false);
  const copyTimer = useRef<number | null>(null);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      if (copyTimer.current !== null) window.clearTimeout(copyTimer.current);
    };
  }, []);

  useEffect(() => {
    if (!runId || tab === "json") return;
    let cancelled = false;
    setLoading(true);
    setErr(null);
    setDoc(null);
    const load = async () => {
      try {
        const text = tab === "report" ? await getReport(runId) : await getPatch(runId);
        if (!cancelled) setDoc(text);
      } catch (error) {
        if (cancelled) return;
        setErr(error instanceof ApiError && error.status === 404
          ? terminal ? "文件尚未就绪，请稍后重试。" : "文件将在运行结束后生成，请稍后再看。"
          : `读取失败：${error instanceof Error ? error.message : String(error)}`);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    return () => { cancelled = true; };
  }, [runId, tab, terminal, retry]);

  const text = runId ? (tab === "json" ? jsonText : doc ?? "") : "";
  const file = TABS.find((t) => t.id === tab)!.file(runId ?? "run");
  const downloadable = Boolean(text) && !loading && !err;
  const finalised = terminal && Boolean(jsonText);
  const copy = async () => {
    if (!downloadable) return;
    setCopyError(null);
    try {
      await navigator.clipboard.writeText(text);
      if (!mounted.current) return;
      setCopied(true);
      if (copyTimer.current !== null) window.clearTimeout(copyTimer.current);
      copyTimer.current = window.setTimeout(() => setCopied(false), 1600);
    } catch {
      if (mounted.current) setCopyError("剪贴板不可用，请用「下载」保存文件。");
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-end gap-1.5">
        <button
          onClick={copy}
          disabled={!downloadable}
          className="cursor-pointer rounded-[5px] border border-rule-hi px-2.5 py-1 font-mono text-[10.5px] text-ink-mid transition-colors hover:border-ink-muted hover:text-ink disabled:opacity-40"
        >
          {copied ? "已复制" : "复制"}
        </button>
        <button
          onClick={() => download(file, text)}
          disabled={!downloadable}
          className="cursor-pointer rounded-[5px] border border-accent/45 bg-accent/[.10] px-2.5 py-1 font-mono text-[10.5px] text-accent transition-colors hover:bg-accent/[.18] disabled:opacity-40"
        >
          下载 {file.split(".").pop()}
        </button>
      </div>
      {copyError && <p role="alert" className="text-[12px] text-warn">{copyError}</p>}
      {!runId && (
        <p className="text-[12px] leading-relaxed text-ink-muted">
          当前没有可读取的真实运行文件；回放仅演示过程，不提供文件下载。
        </p>
      )}
      {loading && <p className="py-4 text-center text-[12px] text-ink-muted">读取中…</p>}
      {!loading && err && (
        <div role="alert" className="rounded-[9px] border border-warn/35 bg-warn/[.06] px-3.5 py-3 text-[12px] leading-relaxed text-ink-mid">
          <p>{err}</p>
          <button onClick={() => setRetry((n) => n + 1)} className="mt-2 cursor-pointer text-accent underline underline-offset-4">
            重试读取
          </button>
        </div>
      )}
      {!loading && !err && tab === "report" && doc !== null && <ReportBody md={doc} />}
      {!loading && !err && tab === "patch" && doc !== null && (
        !finalised && !doc.trim()
          ? <p className="text-[12px] text-ink-muted">{terminal ? "正在整理最终补丁，请稍后查看。" : "运行尚未结束，请等待完成后查看最终补丁。"}</p>
          : <div className="space-y-2">
              {!finalised && <p className="text-[12px] text-ink-muted">{terminal ? "正在整理最终补丁，内容可能继续更新。" : "运行尚未结束，当前补丁可能继续变化。"}</p>}
              <DiffBody diff={doc} />
            </div>
      )}
      {runId && tab === "json" && (
        <pre className="max-h-[52vh] overflow-auto rounded-[9px] border border-rule bg-void/60 px-3.5 py-3 font-mono text-[11px] leading-relaxed whitespace-pre-wrap text-ink-mid">
          {jsonText || (terminal ? "运行已结束，正在整理最终结果。" : "结果会在运行结束后生成。")}
        </pre>
      )}
    </div>
  );
}

/* ─────────────────────────────────────── 报告原文：先表格，再原文 */

function ReportBody({ md }: { md: string }) {
  const { title, kv, rest } = parseReport(md);

  if (!md.trim()) {
    return (
      <div className="rounded-[9px] border border-rule px-3.5 py-4 font-mono text-[11px] text-ink-muted">
        report.md 是空的。
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="overflow-hidden rounded-[9px] border border-rule">
        <div className="border-b border-rule bg-white/[.02] px-3 py-1.5 font-mono text-[9.5px] tracking-[.16em] text-ink-muted uppercase">
          {title}
        </div>
        {kv.length === 0 ? (
          <div className="px-3 py-2.5 font-mono text-[11px] text-ink-muted">
            没有 key=value 行，正文见下方原文。
          </div>
        ) : (
          kv.map(([k, v], i) => (
            <div
              key={k}
              className={clsx(
                "flex items-baseline gap-3 px-3 py-2 font-mono text-[11.5px]",
                i > 0 && "border-t border-white/[.045]",
              )}
            >
              <span className="w-[148px] shrink-0 text-ink-muted">{k}</span>
              <span
                className={clsx(
                  "min-w-0 flex-1 break-all",
                  v === "true" || v === "succeeded"
                    ? "text-pass"
                    : v === "false" || v === "failed" || v === "budget_exceeded"
                      ? "text-reject"
                      : v === "None" || v === "none" || v === "null"
                        ? "text-ink-muted"
                        : "text-ink",
                )}
              >
                {v}
              </span>
            </div>
          ))
        )}
      </div>

      {rest && (
        <div>
          <div className="mb-1.5 font-mono text-[9.5px] tracking-[.16em] text-ink-muted uppercase">
            失败原因 / 附注
          </div>
          <pre className="max-h-[34vh] overflow-auto rounded-[9px] border border-reject/25 bg-reject/[.045] px-3.5 py-3 font-mono text-[11px] leading-relaxed whitespace-pre-wrap text-[#ffd9d5]">
            {rest}
          </pre>
        </div>
      )}

      <details className="rounded-[9px] border border-rule bg-void/40">
        <summary className="cursor-pointer px-3.5 py-2 font-mono text-[10.5px] text-ink-muted hover:text-ink-mid">
          查看文件原文（下载得到的就是这段）
        </summary>
        <pre className="max-h-[34vh] overflow-auto border-t border-rule px-3.5 py-3 font-mono text-[10.5px] leading-relaxed whitespace-pre-wrap text-ink-mid">
          {md}
        </pre>
      </details>
    </div>
  );
}

function parseReport(md: string) {
  const lines = md.split("\n");
  let title = "Report";
  const kv: [string, string][] = [];
  const rest: string[] = [];
  let body = false;

  for (const line of lines) {
    const t = line.trim();
    if (!body) {
      if (t.startsWith("# ")) {
        title = t.slice(2).trim() || "Report";
        continue;
      }
      if (t === "") continue;
      const m = /^([A-Za-z_]+)=(.+)$/.exec(t);
      if (m) {
        kv.push([m[1], m[2]]);
        continue;
      }
      body = true;
    }
    rest.push(line);
  }
  return { title, kv, rest: rest.join("\n").trim() };
}

/* ────────────────────────────────────────────────── 补丁 */

function DiffBody({ diff }: { diff: string }) {
  if (!diff.trim()) {
    return (
      <div className="rounded-[9px] border border-rule px-3.5 py-4 font-mono text-[11px] leading-relaxed text-ink-muted">
        patch.diff 为空——这次运行没有改动工作区里的任何文件。
        <div className="mt-1 text-[10.5px] text-ink-muted/70">
          （正常现象：依赖齐全、一次通过的靶场不需要改代码。）
        </div>
      </div>
    );
  }

  const lines = diff.split("\n");
  return (
    <pre className="max-h-[56vh] overflow-auto rounded-[9px] border border-rule bg-void/60 py-2 font-mono text-[10.5px] leading-[1.65]">
      {lines.map((l, i) => (
        <div
          key={i}
          className={clsx(
            "px-3 whitespace-pre-wrap",
            l.startsWith("+") && !l.startsWith("+++") && "bg-pass/[.09] text-pass",
            l.startsWith("-") && !l.startsWith("---") && "bg-reject/[.09] text-reject",
            l.startsWith("@@") && "text-accent/85",
            l.startsWith("diff ") && "text-ink",
            !/^[+\-@]/.test(l) && "text-ink-muted",
          )}
        >
          {l || " "}
        </div>
      ))}
    </pre>
  );
}

/** Blob 下载：不依赖后端支持 Range / Content-Disposition。 */
function download(name: string, text: string) {
  if (!text) return;
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
