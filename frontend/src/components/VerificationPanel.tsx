import clsx from "clsx";
import { verificationConclusion, verificationEvidencePath, VERIFICATION_TYPES } from "../runModel";
import type { MetricResult, VerificationResult, VerificationSpec } from "../types";

const API_BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "";
const BOUNDARY = "本次检查不包含干净环境重放，也不等于完整复现论文的全部实验结论。";
const detailClass = "mt-4 border-t border-rule pt-3";
const summaryClass = "cursor-pointer rounded text-sm leading-6 text-ink-mid hover:text-ink focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent";

function Digest({ value, image = false }: { value: string | null; image?: boolean }) {
  // 镜像只展示内容哈希，不回显容器标识、镜像标签或宿主机路径。
  const valid = value && (image ? /^sha256:[0-9a-f]{64}$/ : /^[0-9a-f]{64}$/).test(value);
  return <span className="font-mono text-xs leading-6 break-all">{valid ? value : value ? "未提供可展示的内容哈希" : "未记录"}</span>;
}

function Scope({ source, dataset, code_under_test }: {
  source: string | null;
  dataset: string | null;
  code_under_test: string | null;
}) {
  return (
    <dl className="mt-4 space-y-3 text-sm leading-6">
      {[["被测代码", code_under_test], ["数据集", dataset], ["指标来源", source]].map(([label, value]) => (
        <div key={label} className="sm:grid sm:grid-cols-[6rem_minmax(0,1fr)] sm:gap-3">
          <dt className="text-ink-mid">{label}</dt>
          <dd className="min-w-0 text-ink [overflow-wrap:anywhere]">{value ?? "未记录"}</dd>
        </div>
      ))}
    </dl>
  );
}

function Metrics({ metrics }: { metrics: MetricResult[] }) {
  return (
    <div className="mt-4 min-w-0">
      <table className="w-full table-fixed border-collapse text-left text-xs leading-6 text-ink-mid sm:text-sm">
        <caption className="pb-2 text-left text-sm font-medium text-ink">指标观测与固定阈值</caption>
        <colgroup><col className="w-[26%]" /><col className="w-[28%]" /><col className="w-[28%]" /><col className="w-[18%]" /></colgroup>
        <thead className="border-y border-rule">
          <tr>
            <th scope="col" className="py-2 pr-2 font-medium">指标</th>
            <th scope="col" className="py-2 pr-2 text-right font-medium">观测值</th>
            <th scope="col" className="py-2 pr-2 text-right font-medium">要求</th>
            <th scope="col" className="py-2 text-right font-medium">判定</th>
          </tr>
        </thead>
        <tbody>
          {metrics.map((metric, index) => (
            <tr key={`${metric.name}:${index}`} className="border-b border-rule align-top">
              <th scope="row" className="py-2 pr-2 text-left font-normal text-ink [overflow-wrap:anywhere]">{metric.name}</th>
              <td className="py-2 pr-2 text-right font-mono text-[11px] tabular-nums text-ink [overflow-wrap:anywhere]">{metric.observed.toExponential(4)}</td>
              <td className="py-2 pr-2 text-right font-mono text-[11px] tabular-nums text-ink [overflow-wrap:anywhere]">&lt; {metric.threshold.toExponential()}</td>
              <td className={clsx("py-2 text-right", metric.passed ? "text-pass" : "text-reject")}>{metric.passed ? "通过" : "未通过"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <details className="mt-2">
        <summary className={clsx(summaryClass, "text-xs")}>完整精度数值（表中观测值已舍入，判定使用原始精度）</summary>
        <dl className="mt-2 space-y-2 text-xs leading-6">
          {metrics.map((metric, index) => (
            <div key={`${metric.name}:${index}`} className="[overflow-wrap:anywhere]">
              <dt className="text-ink-mid">{metric.name}</dt>
              <dd className="font-mono tabular-nums text-ink">观测 {String(metric.observed)}；要求 &lt; {String(metric.threshold)}</dd>
            </div>
          ))}
        </dl>
      </details>
    </div>
  );
}

/** 主工作区与报告复用同一证据视图；只消费 status.result，不额外请求。 */
export function VerificationPanel({ verification, runId, replay = false }: {
  verification?: VerificationResult | null;
  runId: string | null;
  replay?: boolean;
}) {
  if (replay || !verification) return (
    <section aria-label="分级验证证据" className="border-b border-rule py-6">
      <h2 className="text-base font-semibold text-ink">{replay ? "示例不提供真实验证证据" : "历史结果未记录分级验证"}</h2>
      <p className="mt-2 text-sm leading-6 text-ink-mid">
        {replay ? "这是离线预设记录，不能据此判断执行检查或目标指标是否通过，也没有真实证据文件。" : "无法从旧版成功状态或 verified 字段判断执行检查或目标指标是否通过；不推断验证等级。"}
      </p>
    </section>
  );

  const conclusion = verificationConclusion(verification);
  const evidencePath = verification.level === "target" ? verificationEvidencePath(runId, verification.evidence_url) : null;
  return (
    <section aria-label="分级验证证据" className="min-w-0 border-b border-rule py-6">
      <h2 className="text-base font-semibold text-ink">分级验证证据</h2>
      <p className={clsx("mt-2 text-sm font-medium", {
        "text-pass": conclusion.tone === "success", "text-reject": conclusion.tone === "error",
        "text-warn": conclusion.tone === "warning", "text-ink-mid": conclusion.tone === "neutral",
      })}>{conclusion.label}</p>
      <p className="mt-1 text-xs leading-6 text-ink-mid">检查类型：{VERIFICATION_TYPES[verification.level]}；类型不代表检查已通过，结论以上述状态为准。</p>
      <p className="mt-2 max-w-[70ch] text-sm leading-7 text-ink-mid [overflow-wrap:anywhere]">{verification.summary}</p>
      {verification.level === "execution" && <p className="mt-2 text-sm leading-6 text-ink-mid">仅检查命令执行及要求的产物是否存在；未进行目标指标检查。</p>}
      {verification.level === "artifact" && <p className="mt-2 text-sm leading-6 text-ink-mid">产物检查不代表目标科学指标通过。</p>}

      {verification.metrics.length > 0 ? <Metrics metrics={verification.metrics} /> : (
        <p className="mt-4 text-sm leading-6 text-ink-mid">未记录指标观测值，不表示指标为零或已通过。</p>
      )}
      <div className="mt-4">
        <h3 className="text-sm font-medium text-ink">检查项</h3>
        {verification.checks.length > 0 ? (
          <ul className="mt-2 divide-y divide-rule border-y border-rule">
            {verification.checks.map((check, index) => (
              <li key={`${check.name}:${index}`} className="py-2 text-sm leading-6">
                <div className="flex items-baseline justify-between gap-3">
                  <span className="min-w-0 font-medium text-ink [overflow-wrap:anywhere]">{check.name}</span>
                  <span className={clsx("shrink-0 text-xs", check.passed ? "text-pass" : "text-reject")}>{check.passed ? "通过" : "未通过"}</span>
                </div>
                <p className="text-ink-mid [overflow-wrap:anywhere]">{check.detail}</p>
              </li>
            ))}
          </ul>
        ) : <p className="mt-2 text-sm text-ink-mid">没有已记录的检查项。</p>}
      </div>

      <Scope source={verification.source} dataset={verification.dataset} code_under_test={verification.code_under_test} />
      <details className={detailClass}>
        <summary className={summaryClass}>规则、可信校验器与证据哈希</summary>
        <dl className="mt-3 space-y-3 text-sm leading-6 text-ink">
          <div><dt className="text-ink-mid">规则 ID</dt><dd className="break-all">{verification.rule_id ?? "未记录"}</dd></div>
          <div><dt className="text-ink-mid">可信校验器 SHA-256</dt><dd><Digest value={verification.validator_digest} /></dd></div>
          <div><dt className="text-ink-mid">产物 SHA-256</dt><dd><Digest value={verification.artifact_sha256} /></dd></div>
          <div><dt className="text-ink-mid">校验镜像 ID（内容哈希）</dt><dd><Digest value={verification.verifier_image_id} image /></dd></div>
          <div><dt className="text-ink-mid">核验时间</dt><dd className="break-all">{verification.checked_at ?? "未记录"}</dd></div>
          {verification.failure_category && <div><dt className="text-ink-mid">未通过或受阻类别</dt><dd className="break-all">{verification.failure_category}</dd></div>}
        </dl>
      </details>
      {evidencePath && (
        <div className="mt-4 text-sm leading-6">
          <a href={`${API_BASE}${evidencePath}`} download className="inline-block rounded text-accent underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent">下载本次核验输入 JSON</a>
          <p className="mt-1 text-xs text-ink-mid">仅为服务端保存的有限大小指标产物快照，不是重放包或完整实验数据。</p>
        </div>
      )}
      <div className="mt-4 text-sm leading-6 text-ink-mid">
        <h3 className="font-medium text-ink">结论边界</h3>
        <p className="mt-1">{BOUNDARY}</p>
        {verification.limitations.length > 0 && <ul className="mt-2 list-disc space-y-1 pl-4 [overflow-wrap:anywhere]">
          {verification.limitations.map((limitation, index) => <li key={index}>{limitation}</li>)}
        </ul>}
      </div>
    </section>
  );
}

export function VerificationSpecPanel({ spec }: { spec: VerificationSpec }) {
  return (
    <section aria-label="固定科学指标验证规则" className="mt-6 min-w-0 border-y border-rule py-5">
      <h3 className="text-base font-medium text-ink">固定科学指标验证规则</h3>
      <p className="mt-2 text-sm leading-7 text-ink-mid">运行后由独立校验器核对指标；以下规则已随确认单固定，不可编辑。目前尚未执行核验。</p>
      <Scope source={spec.source} dataset={spec.dataset} code_under_test={spec.code_under_test} />
      <dl className="mt-4 space-y-3 text-sm leading-6">
        <div><dt className="text-ink-mid">固定判定条件</dt><dd className="mt-1 text-ink">{spec.rule_id === "sindy-lorenz-v1" ? "三个方程各 56 个系数" : "两个方程各 6 个系数"}，逐行计算最大绝对误差；每行都必须严格小于 <span className="font-mono tabular-nums">{spec.threshold.toExponential()}</span>，等于阈值不通过。</dd></div>
        <div><dt className="text-ink-mid">核验产物</dt><dd className="font-mono text-xs text-ink">{spec.artifact}</dd></div>
        <div><dt className="text-ink-mid">规则 ID</dt><dd className="break-all text-ink">{spec.rule_id}</dd></div>
      </dl>
      <details className={detailClass}>
        <summary className={summaryClass}>可信校验器 SHA-256（固定）</summary>
        <p className="mt-2 text-ink"><Digest value={spec.validator_digest} /></p>
      </details>
      <p className="mt-4 text-sm leading-6 text-ink-mid">{BOUNDARY}</p>
    </section>
  );
}
