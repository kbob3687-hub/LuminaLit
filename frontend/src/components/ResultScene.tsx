import { useEffect, useState } from "react";
import clsx from "clsx";
import { bundleConclusion, overallConclusion, replayConclusion, VERIFICATION_TYPES } from "../runModel";
import { bundleDownloadUrl, getEnvironment } from "../api";
import { ReplayPanel } from "./ReplayPanel";
import type { EnvironmentManifest, RunStatus } from "../types";

/**
 * 评审稿 §5.6：结果页首屏按固定七项顺序展示。
 *
 * 原则：结论与验证数据只从结构化字段取，不从 report 文本反推；缺字段
 * 一律写「未记录」，不补造证据。本页只在用户点击后进入，终态不自动跳转
 * （用户明确要求「成功之后不要自动返回」）。
 */

const API_BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "";
const RUN_ID_RE = /^[0-9a-f]{32}$/;

function Section({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <section aria-label={`结果第 ${n} 项：${title}`} className="border-b border-rule py-6">
      <h2 className="mb-3 flex items-baseline gap-3 text-base font-semibold text-ink">
        <span className="font-mono text-xs text-ink-muted">{String(n).padStart(2, "0")}</span>{title}
      </h2>
      {children}
    </section>
  );
}

function Missing({ text }: { text: string }) {
  return <p className="text-sm leading-7 text-ink-muted">{text}</p>;
}

function Row({ label, value, tone }: { label: string; value: string; tone?: "pass" | "warn" | "error" }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-rule py-2 text-sm last:border-b-0">
      <span className="text-ink-mid">{label}</span>
      <span className={clsx("min-w-0 text-right [overflow-wrap:anywhere]", tone === "pass" ? "text-pass" : tone === "warn" ? "text-warn" : tone === "error" ? "text-reject" : "text-ink")}>{value}</span>
    </div>
  );
}

function DownloadRow({ label, href, available, note }: { label: string; href: string | null; available: boolean; note: string }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-b border-rule py-2.5 text-sm last:border-b-0">
      <div className="min-w-0">
        <span className="text-ink">{label}</span>
        <p className="mt-0.5 text-xs leading-6 text-ink-muted">{available ? note : "尚未生成或后端未下发，不提供伪造文件"}</p>
      </div>
      {available && href ? (
        <a href={href} download className="shrink-0 rounded text-accent underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent">下载</a>
      ) : (
        <span className="shrink-0 text-xs text-ink-muted">不可用</span>
      )}
    </div>
  );
}

export function ResultScene({ status, runId, onBack, onNewRun, onTechnical }: {
  status: RunStatus | null;
  runId: string | null;
  onBack: () => void;
  onNewRun: () => void;
  onTechnical: () => void;
}) {
  const result = status?.result ?? null;
  const [env, setEnv] = useState<EnvironmentManifest | null>(null);
  const [envError, setEnvError] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setEnv(null);
    setEnvError(false);
    if (!runId || !RUN_ID_RE.test(runId)) return;
    void getEnvironment(runId)
      .then((v) => { if (!cancelled) setEnv(v); })
      .catch(() => { if (!cancelled) setEnvError(true); });
    return () => { cancelled = true; };
  }, [runId]);

  const overall = overallConclusion(status, false);
  const verification = result?.verification ?? null;
  const replay = replayConclusion(result?.replay);
  const bundle = bundleConclusion(result?.bundle);
  const guidance = status?.guidance;
  const safeRun = runId && RUN_ID_RE.test(runId) ? runId : null;
  const reportUrl = safeRun && result?.report_url ? `${API_BASE}/api/runs/${safeRun}/report` : null;
  const patchUrl = safeRun && result?.patch_url ? `${API_BASE}/api/runs/${safeRun}/patch` : null;
  const envUrl = safeRun && (env || result?.environment_url) ? `${API_BASE}/api/runs/${safeRun}/environment` : null;
  const bundleUrl = bundleDownloadUrl(safeRun, result?.bundle);

  const copyConfig = async () => {
    if (!result) return;
    const config = {
      run_id: result.run_id,
      resolved_revision: result.resolved_revision,
      code_under_test: result.code_under_test ?? null,
      effective_plan_digest: result.effective_plan_digest ?? null,
      outcome: result.outcome,
      verification: verification ? { level: verification.level, status: verification.status, rule_id: verification.rule_id } : null,
      replay_status: result.replay?.status ?? null,
      bundle_integrity: result.bundle?.integrity ?? null,
    };
    try {
      await navigator.clipboard.writeText(JSON.stringify(config, null, 2));
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      setCopied(false);
    }
  };

  return (
    <main className="run-workspace h-full overflow-y-auto px-5 py-8 sm:px-8">
      <div className="mx-auto max-w-[920px]">
        <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
          <button type="button" onClick={onBack} className="cursor-pointer rounded-md border border-rule-hi px-3.5 py-2 text-sm text-ink-mid hover:text-ink">返回运行页</button>
          <span className="font-mono text-xs text-ink-muted">run {safeRun ?? "未记录"}</span>
        </div>

        <header className="mb-2">
          <h1 className="text-3xl font-semibold tracking-tight text-ink">本次结果</h1>
          <p className="mt-2 text-sm leading-7 text-ink-mid">下面是本次运行实际记录的内容；未采集的字段显示为「未记录」，不会补造结果。</p>
        </header>

        {/* 1. 结论及验证范围 */}
        <Section n={1} title="结论及验证范围">
          <p className={clsx("text-lg font-semibold", overall.tone === "success" ? "text-pass" : overall.tone === "error" ? "text-reject" : overall.tone === "warning" ? "text-warn" : "text-ink")}>{overall.label}</p>
          <p className="mt-2 text-sm leading-7 text-ink-mid">{overall.detail}</p>
          <div className="mt-3">
            <Row label="验证类型" value={verification ? VERIFICATION_TYPES[verification.level] : "未记录分级验证"} />
            <Row label="验证状态" value={verification?.status ?? "未记录"} tone={verification?.status === "passed" ? "pass" : verification?.status === "failed" ? "error" : "warn"} />
            <Row label="被测代码" value={result?.code_under_test ?? verification?.code_under_test ?? "未记录"} />
            <Row label="数据集" value={verification?.dataset ?? "未记录"} />
            <Row label="确认摘要" value={result?.effective_plan_digest ?? "未记录"} />
          </div>
          {overall.gaps.length > 0 && (
            <ul className="mt-3 list-disc space-y-1 pl-4 text-xs leading-6 text-warn">
              {overall.gaps.map((g, i) => <li key={`${g}:${i}`}>{g}</li>)}
            </ul>
          )}
          {result?.limitations && result.limitations.length > 0 && (
            <ul className="mt-2 list-disc space-y-1 pl-4 text-xs leading-6 text-ink-mid">
              {result.limitations.map((l, i) => <li key={i} className="[overflow-wrap:anywhere]">{l}</li>)}
            </ul>
          )}
        </Section>

        {/* 2. 原始失败是什么，或原仓库已通过 */}
        <Section n={2} title="原始失败 / 原仓库状态">
          {result ? (
            <>
              <Row label="结束原因" value={result.outcome} tone={result.outcome === "succeeded" ? "pass" : "error"} />
              <Row label="失败类别" value={result.failure_category ?? verification?.failure_category ?? "无（或后端未记录）"} />
              <Row label="修复轮次" value={String(result.attempts)} />
              <p className="mt-3 text-sm leading-7 text-ink-mid">{verification?.summary ?? guidance?.detail ?? "后端没有下发可读的运行说明；可查看技术详情里的原始事件。"}</p>
            </>
          ) : <Missing text="结果尚未落盘，暂时无法说明原始失败或原仓库状态。" />}
        </Section>

        {/* 3. 修改了什么：代码 / 依赖 / 允许的环境变量分开列 */}
        <Section n={3} title="修改了什么">
          <Row label="代码变更" value={result?.patch_url ? "已生成补丁（可在下方下载）" : "无补丁或后端未记录"} />
          <Row label="依赖变化" value={env?.packages?.length ? `${env.packages.length} 项已采集` : envError ? "环境清单尚未生成" : "未采集"} />
          <Row label="允许的环境变量" value={env?.environment ? `${Object.keys(env.environment).length} 项已采集` : "未采集"} />
          <Row label="产物与源码变更" value={result?.bundle ? "在材料清单中分开记录" : "未记录"} />
          <p className="mt-3 text-xs leading-6 text-ink-muted">环境清单只显示脱敏后的变量名与包信息；密钥、代理凭据和宿主路径不会进入结果或下载材料。</p>
        </Section>

        {/* 4. 哪些检查通过 / 失败 / 未执行，以及证据 */}
        <Section n={4} title="检查项与证据">
          {verification && verification.checks.length > 0 ? (
            <ul className="divide-y divide-rule border-y border-rule">
              {verification.checks.map((c, i) => (
                <li key={`${c.name}:${i}`} className="py-2.5 text-sm leading-6">
                  <div className="flex items-baseline justify-between gap-3">
                    <span className="min-w-0 font-medium text-ink [overflow-wrap:anywhere]">{c.name}</span>
                    <span className={clsx("shrink-0 text-xs", c.passed ? "text-pass" : "text-reject")}>{c.passed ? "通过" : "未通过"}</span>
                  </div>
                  <p className="text-ink-mid [overflow-wrap:anywhere]">{c.detail}</p>
                </li>
              ))}
            </ul>
          ) : <Missing text="没有已记录的结构化检查项；不把日志里的一句 PASS 当作检查通过。" />}
          {verification?.evidence_url && safeRun && (
            <a href={`${API_BASE}${verification.evidence_url}`} download className="mt-3 inline-block text-sm text-accent underline underline-offset-4">下载本次核验输入 JSON</a>
          )}
          <button type="button" onClick={onTechnical} className="mt-3 block cursor-pointer text-sm text-ink-mid underline underline-offset-4 hover:text-ink">查看全部技术事件与原始错误</button>
        </Section>

        {/* 5. 干净复跑是否通过（含材料完整性，见 ReplayPanel） */}
        <Section n={5} title="干净复跑">
          <p className={clsx("text-sm font-medium", replay.tone === "success" ? "text-pass" : replay.tone === "error" ? "text-reject" : replay.tone === "warning" ? "text-warn" : "text-ink-mid")}>{replay.label}</p>
          <p className="mt-1 text-xs leading-6 text-ink-mid">材料完整性：{bundle.label}（与复跑通过与否是两个独立结论）。</p>
        </Section>
        <ReplayPanel result={result} runId={safeRun} />

        {/* 6. 下载报告、补丁、环境信息和复跑材料 */}
        <Section n={6} title="下载">
          <DownloadRow label="结果报告" href={reportUrl} available={Boolean(reportUrl)} note="后端收尾时写入的 report.md 原文" />
          <DownloadRow label="补丁" href={patchUrl} available={Boolean(patchUrl)} note="无改动时为空补丁，是正常结果" />
          <DownloadRow label="环境信息" href={envUrl} available={Boolean(envUrl)} note="已采集的 Python、镜像、依赖与允许环境变量" />
          <DownloadRow label="复跑材料" href={bundleUrl} available={Boolean(bundleUrl)} note={bundle.downloadable ? "完整性已通过，仅含声明范围内的相对路径" : "材料不完整或被阻止，不提供下载"} />
        </Section>

        {/* 7. 可执行的下一步 */}
        <Section n={7} title="下一步">
          {guidance?.next_steps && guidance.next_steps.length > 0 ? (
            <ul className="list-disc space-y-1.5 pl-4 text-sm leading-7 text-ink-mid">
              {guidance.next_steps.map((s, i) => <li key={i}>{s}</li>)}
            </ul>
          ) : <Missing text="后端没有下发下一步建议。可以先查看失败步骤与技术详情，再补充条件后新建运行。" />}
          <div className="mt-4 flex flex-wrap gap-2">
            <button type="button" onClick={onNewRun} className="cursor-pointer rounded-md bg-accent px-4 py-2 text-sm font-semibold text-white hover:bg-accent/85">补充条件后新建运行</button>
            <button type="button" onClick={() => void copyConfig()} className="cursor-pointer rounded-md border border-rule-hi px-4 py-2 text-sm text-ink-mid hover:text-ink">{copied ? "已复制" : "复制运行配置"}</button>
          </div>
          <p className="mt-2 text-xs leading-6 text-ink-muted">补充资料或更换计划会创建带 parent_run_id 的新运行，不覆盖旧记录，也不伪装成原运行无缝恢复。</p>
        </Section>
      </div>
    </main>
  );
}
