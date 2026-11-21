import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import clsx from "clsx";
import { ApiError, getEnvironment, getPatch, getReport } from "../api";
import { failureText } from "../runModel";
import type { EnvironmentManifest, RunStatus } from "../types";

/**
 * 结果报告面板：把后端落盘的四个文件搬到界面上。
 *
 * 四个端点各管一件事，不要混：
 *   GET /api/runs/{id}/report       → report.md 原文（收尾才落盘，运行中 404）
 *   GET /api/runs/{id}/patch        → patch.diff（无改动回空串，不是 404）
 *   GET /api/runs/{id}/environment  → environment.json（收尾才落盘，运行中 404）
 *   GET /api/runs/{id}              → result.json 里的字段（已在 status 里）
 *
 * 报告原文很朴素（`outcome=...` 几行），所以这里做两层：
 * 上层是把 key=value 解析成表格的摘要，下层是「原文」——原文永远可下载，
 * 界面再怎么美化也不改文件内容。
 *
 * 环境清单是唯一一个结构化端点（JSON 而非纯文本）。它回答的是报告里看不到的
 * 那半句：这份结论是在哪个镜像、哪个 commit、装了哪些包的前提下得到的。
 */

type Tab = "report" | "patch" | "env" | "json";

const TABS: { id: Tab; label: string; file: (rid: string) => string }[] = [
  { id: "report", label: "结果报告", file: (r) => `paperharness-${r.slice(0, 8)}-report.md` },
  { id: "patch", label: "补丁", file: (r) => `paperharness-${r.slice(0, 8)}-patch.diff` },
  { id: "env", label: "环境", file: (r) => `paperharness-${r.slice(0, 8)}-environment.json` },
  { id: "json", label: "结果 JSON", file: (r) => `paperharness-${r.slice(0, 8)}-result.json` },
];

export function ReportPanel({ runId, status }: { runId: string | null; status: RunStatus | null }) {
  // 演示/截图直链：?panel=report&tab=patch
  const [tab, setTab] = useState<Tab>(() => {
    const t = new URLSearchParams(window.location.search).get("tab");
    return t === "patch" || t === "json" || t === "env" ? t : "report";
  });
  /**
   * 缓存带 runId：换 run 后 docs / envDoc 自动失效，不会短暂（或持续）显示
   * 上一次运行的报告、补丁、环境清单。旧写法是裸 `{}`，runId 从 null 变成
   * 真值、demo → live 切换、或退出动画期间换 run，都可能串数据。
   */
  const [cache, setCache] = useState<{
    runId: string;
    docs: Partial<Record<Tab, string>>;
    env: EnvironmentManifest | null;
  }>({ runId: "", docs: {}, env: null });
  /** loading / err 按 tab 分开：快速切 tab 时两个在途请求不会互相覆盖状态。 */
  const [loading, setLoading] = useState<Partial<Record<Tab, boolean>>>({});
  const [err, setErr] = useState<Partial<Record<Tab, string>>>({});
  const [copied, setCopied] = useState(false);
  /** 每个 tab 一个请求序号，只有最后一次请求的回包才允许写状态。 */
  const seqRef = useRef<Partial<Record<Tab, number>>>({});
  const copyTimer = useRef<number | null>(null);

  const docs = cache.runId === runId ? cache.docs : {};
  const envDoc = cache.runId === runId ? cache.env : null;

  // 换 run：让在途请求全部失效、清掉 loading/err；卸载时清复制计时器。
  useEffect(() => {
    seqRef.current = {};
    setLoading({});
    setErr({});
    setCopied(false);
    return () => {
      if (copyTimer.current !== null) window.clearTimeout(copyTimer.current);
    };
  }, [runId]);

  const result = status?.result ?? null;
  const terminal = Boolean(
    status && ["succeeded", "failed", "cancelled"].includes(status.state),
  );

  // result.json 直接来自轮询拿到的 status，不用再请求
  const jsonText = useMemo(() => (result ? JSON.stringify(result, null, 2) : ""), [result]);

  const load = useCallback(
    async (which: Tab) => {
      if (!runId || which === "json") return;
      const seq = (seqRef.current[which] ?? 0) + 1;
      seqRef.current[which] = seq;
      const alive = () => seqRef.current[which] === seq;
      setLoading((s) => ({ ...s, [which]: true }));
      setErr((s) => ({ ...s, [which]: undefined }));
      try {
        if (which === "env") {
          const manifest = await getEnvironment(runId);
          if (!alive()) return;
          setCache((c) => ({
            runId,
            docs: { ...(c.runId === runId ? c.docs : {}), env: JSON.stringify(manifest, null, 2) },
            env: manifest,
          }));
          return;
        }
        const text = which === "report" ? await getReport(runId) : await getPatch(runId);
        if (!alive()) return;
        setCache((c) => ({
          runId,
          docs: { ...(c.runId === runId ? c.docs : {}), [which]: text },
          env: c.runId === runId ? c.env : null,
        }));
      } catch (e) {
        if (!alive()) return;
        const code = (e as ApiError)?.status;
        setErr((s) => ({
          ...s,
          [which]:
            code === 404
              ? which === "env"
                ? "环境清单尚未生成：后端在运行收尾时才写 environment.json，运行中请稍后再看。"
                : "报告尚未生成：后端只在运行收尾时写 report.md，运行中请稍后再看。"
              : `读取失败：${(e as Error)?.message ?? String(e)}`,
        }));
      } finally {
        if (alive()) setLoading((s) => ({ ...s, [which]: false }));
      }
    },
    [runId],
  );

  // 切页 / 进终态 / 换 run 时拉一次；已取到的就不重复请求
  useEffect(() => {
    if (tab === "json") return;
    if (docs[tab] !== undefined) return;
    void load(tab);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, runId, terminal]);

  const text =
    tab === "json" ? jsonText : (docs[tab] ?? "");
  const file = TABS.find((t) => t.id === tab)!.file(runId ?? "run");

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text || "");
      setCopied(true);
      if (copyTimer.current !== null) window.clearTimeout(copyTimer.current);
      copyTimer.current = window.setTimeout(() => setCopied(false), 1600);
    } catch {
      setErr((s) => ({ ...s, [tab]: "剪贴板不可用，请用「下载」保存文件。" }));
    }
  };

  return (
    <div className="space-y-4">
      {!runId && (
        <div className="rounded-[9px] border border-rule px-3.5 py-3 font-mono text-[11px] leading-relaxed text-ink-muted">
          还没拿到 run_id：前端只在 <code className="text-ink-mid">GET /api/runs/{"{id}"}</code>{" "}
          返回后才知道这次运行是谁。后端没响应时这里不会有报告。
        </div>
      )}

      {/* 结论摘要：全部取自 result.json，不额外推断 */}
      <div
        className={clsx(
          "rounded-[10px] border px-4 py-3.5",
          result?.outcome === "succeeded"
            ? "border-pass/40 bg-gradient-to-br from-pass/[.10] to-transparent"
            : result
              ? "border-reject/40 bg-gradient-to-br from-reject/[.09] to-transparent"
              : "border-rule bg-white/[.02]",
        )}
      >
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1.5">
          <span
            className={clsx(
              "font-mono text-[15px] font-semibold",
              result?.outcome === "succeeded" ? "text-pass" : result ? "text-reject" : "text-ink-muted",
            )}
          >
            {result?.outcome ?? "运行中"}
          </span>
          {result && (
            <>
              <span className="font-mono text-[11px] text-ink-muted">
                verified=<b className="text-ink-mid">{String(result.verified)}</b>
              </span>
              <span className="font-mono text-[11px] text-ink-muted">
                attempts=<b className="text-ink-mid">{result.attempts}</b>
              </span>
              {result.failure_category && (
                <span className="rounded-[4px] border border-reject/40 bg-reject/[.08] px-1.5 py-[1px] font-mono text-[10px] text-reject">
                  {failureText(result.failure_category)}
                </span>
              )}
              {result.resolved_revision && (
                <span className="ml-auto truncate font-mono text-[10.5px] text-ink-muted">
                  rev {result.resolved_revision.slice(0, 12)}
                </span>
              )}
            </>
          )}
        </div>
        {!result && (
          <p className="mt-1.5 font-mono text-[10.5px] leading-relaxed text-ink-muted">
            运行还没收尾，result.json 尚未落盘。报告文件在终态后由后端写入。
          </p>
        )}
      </div>

      {/* 三份文件切换 */}
      <div className="flex items-center gap-1.5">
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => {
              setTab(t.id);
              setErr((s) => ({ ...s, [t.id]: undefined }));
            }}
            className={clsx(
              "cursor-pointer rounded-[5px] border px-3 py-1.5 font-mono text-[11px] transition-colors",
              tab === t.id
                ? "border-rule-hi bg-surface-hi text-ink"
                : "border-transparent text-ink-muted hover:text-ink-mid",
            )}
          >
            {t.label}
            {t.id === "patch" && docs.patch === "" && (
              <span className="ml-1 text-[9.5px] text-ink-muted">空</span>
            )}
          </button>
        ))}

        <div className="ml-auto flex items-center gap-1.5">
          <button
            onClick={copy}
            disabled={!text}
            className="cursor-pointer rounded-[5px] border border-rule-hi px-2.5 py-1 font-mono text-[10.5px] text-ink-mid transition-colors hover:border-ink-muted hover:text-ink disabled:opacity-40"
          >
            {copied ? "已复制" : "复制"}
          </button>
          <button
            onClick={() => download(file, text)}
            disabled={!text}
            className="cursor-pointer rounded-[5px] border border-accent/45 bg-accent/[.10] px-2.5 py-1 font-mono text-[10.5px] text-[#9db4ff] transition-colors hover:bg-accent/[.18] disabled:opacity-40"
          >
            下载 {file.split(".").pop()}
          </button>
        </div>
      </div>

      {/* 文件内容：loading / err 都按当前 tab 取，切页不会串状态 */}
      {loading[tab] && (
        <div className="rounded-[9px] border border-rule px-3 py-6 text-center font-mono text-[11px] text-ink-muted">
          读取中…
        </div>
      )}

      {!loading[tab] && err[tab] && (
        <div className="rounded-[9px] border border-warn/35 bg-warn/[.06] px-3.5 py-3 font-mono text-[11px] leading-relaxed text-ink-mid">
          {err[tab]}
        </div>
      )}

      {!loading[tab] && !err[tab] && tab === "report" && docs.report !== undefined && (
        <ReportBody md={docs.report} />
      )}

      {!loading[tab] && !err[tab] && tab === "patch" && docs.patch !== undefined && (
        <DiffBody diff={docs.patch} />
      )}

      {!loading[tab] && !err[tab] && tab === "env" && envDoc && <EnvBody env={envDoc} />}

      {!loading[tab] && !err[tab] && tab === "json" && (
        <pre className="max-h-[52vh] overflow-auto rounded-[9px] border border-rule bg-void/60 px-3.5 py-3 font-mono text-[11px] leading-relaxed whitespace-pre-wrap text-ink-mid">
          {jsonText || "（运行中，暂无 result.json）"}
        </pre>
      )}

      <p className="font-mono text-[10px] leading-relaxed text-ink-muted">
        文件由后端写入 <code className="text-ink-mid">runs/&lt;run_id&gt;/</code> 目录；
        界面只做展示与下载，不改写内容。
      </p>
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

/* ────────────────────────────────────────────────── 环境清单 */

/**
 * environment.json 的表格版。
 *
 * 四个头字段由 harness 填（一定有），`environment` / `packages` 由沙箱会话
 * 提供（只有真实 Docker 沙箱才有）。缺了就说缺——不拿空对象冒充
 * 「没有环境变量」，那是两件事。
 */
function EnvBody({ env }: { env: EnvironmentManifest }) {
  // 排队阶段就被取消的 run，后端写的是 `{}`——那是"还没有沙箱"，不是"环境干净"
  if (Object.keys(env).length === 0) {
    return (
      <div className="rounded-[9px] border border-rule px-3.5 py-4 font-mono text-[11px] leading-relaxed text-ink-muted">
        这份环境清单是空的：run 在排队阶段就被取消，还没有创建沙箱会话，所以没有可记录的内容。
      </div>
    );
  }

  const vars = env.environment ? Object.entries(env.environment) : null;
  const pkgs = env.packages ?? null;

  const rows: [string, string][] = [
    ["resolved_revision", env.resolved_revision || "None"],
    ["python_version", env.python_version || "None"],
    ["base_image", env.base_image || "（这份清单没有记录）"],
    [
      "command",
      env.command && env.command.length > 0 ? env.command.join(" ") : "（没有规划出运行命令）",
    ],
  ];

  return (
    <div className="space-y-3">
      <div className="overflow-hidden rounded-[9px] border border-rule">
        <div className="border-b border-rule bg-white/[.02] px-3 py-1.5 font-mono text-[9.5px] tracking-[.16em] text-ink-muted uppercase">
          environment.json
        </div>
        {rows.map(([k, v], i) => (
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
                v === "None" || v.startsWith("（") ? "text-ink-muted" : "text-ink",
              )}
            >
              {v}
            </span>
          </div>
        ))}
      </div>

      <EnvSection
        title={vars ? `环境变量 · ${vars.length}` : "环境变量"}
        empty={
          vars === null
            ? "本次沙箱没有提供环境变量清单（宿主机进程的环境不会进容器）。"
            : vars.length === 0
              ? "沙箱报告了空的变量表。"
              : null
        }
      >
        <div className="divide-y divide-white/[.045]">
          {vars?.map(([k, v]) => (
            <div key={k} className="flex items-baseline gap-3 px-3 py-1.5 font-mono text-[11px]">
              <span className="w-[190px] shrink-0 break-all text-ink-muted">{k}</span>
              <span className="min-w-0 flex-1 break-all text-ink-mid">{v || "（空）"}</span>
            </div>
          ))}
        </div>
      </EnvSection>

      <EnvSection
        title={pkgs ? `pip freeze · ${pkgs.length} 个包` : "pip freeze"}
        empty={
          pkgs === null
            ? "本次沙箱没有采集包清单（只有真实 Docker 沙箱会跑 pip freeze）。"
            : pkgs.length === 0
              ? "pip freeze 返回空——数据盘镜像里没有可列的包。"
              : null
        }
      >
        <pre className="max-h-[38vh] overflow-auto px-3 py-2 font-mono text-[10.5px] leading-relaxed whitespace-pre-wrap text-ink-mid">
          {pkgs?.join("\n")}
        </pre>
      </EnvSection>

      <p className="font-mono text-[10px] leading-relaxed text-ink-muted">
        这份清单说明的是「结论在什么前提下得到的」：同样是 verified=true，在不同 base_image
        或包版本下含义并不相同。
      </p>
    </div>
  );
}

function EnvSection({
  title,
  empty,
  children,
}: {
  title: string;
  empty: string | null;
  children?: ReactNode;
}) {
  return (
    <div className="overflow-hidden rounded-[9px] border border-rule">
      <div className="border-b border-rule bg-white/[.02] px-3 py-1.5 font-mono text-[9.5px] tracking-[.16em] text-ink-muted uppercase">
        {title}
      </div>
      {empty ? (
        <div className="px-3 py-2.5 font-mono text-[11px] leading-relaxed text-ink-muted">
          {empty}
        </div>
      ) : (
        children
      )}
    </div>
  );
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
