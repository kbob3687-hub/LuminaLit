import clsx from "clsx";
import { bundleConclusion, replayConclusion } from "../runModel";
import { bundleDownloadUrl } from "../api";
import type { BundleResult, ReplayResult, RunResult, VerificationResult } from "../types";

/**
 * 评审稿 §3.3 / §7.4 / §8.6 / §10.3：干净环境复跑与材料交付。
 *
 * 严格只消费 `status.result` 里已经落盘的字段。本轮后端尚未实现复跑
 * （Issue #30 开放），`replay` / `bundle` 缺省时显示「后端未下发」，绝不
 * 拿 `verified`、`state` 或「运行成功」推导出一个复跑结论。
 *
 * 不展示容器 ID、宿主机路径、镜像标签或任何凭据；只展示内容摘要。
 */

const BOUNDARY =
  "干净复跑使用新的工作目录与新的容器，只还原冻结的输入和显式声明的变更；" +
  "通过只代表指定版本、数据与配置下的指定任务可复现，不等于整篇论文结论成立。";

const FIELD_CLASS = "min-w-0 text-ink text-sm leading-6 [overflow-wrap:anywhere]";

function Field({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <div className="sm:grid sm:grid-cols-[10rem_minmax(0,1fr)] sm:gap-3">
      <dt className="text-sm leading-6 text-ink-mid">{label}</dt>
      <dd className={clsx(FIELD_CLASS, !value && "font-mono text-xs text-ink-muted")}>
        {value || "未记录"}
      </dd>
    </div>
  );
}

function Digest({ value }: { value: string | null | undefined }) {
  // 只展示内容哈希；不合法或不存在的摘要一律说「未提供」，不回显原始串。
  const valid = value && /^(sha256:)?[0-9a-f]{64}$/.test(value);
  return <span className="font-mono text-xs leading-6 break-all">{valid ? value : "未提供可展示的内容摘要"}</span>;
}

function VerificationRow({ label, v }: { label: string; v: VerificationResult | null | undefined }) {
  return (
    <tr className="border-b border-rule align-top">
      <th scope="row" className="py-2 pr-3 text-left text-sm font-normal text-ink">{label}</th>
      <td className="py-2 pr-3 text-sm text-ink-mid">{v ? `${v.level} / ${v.status}` : "未记录"}</td>
      <td className="py-2 pr-3 font-mono text-xs text-ink-mid break-all">{v?.rule_id ?? "未记录"}</td>
      <td className="py-2 font-mono text-xs text-ink-mid break-all">{v?.artifact_sha256 ?? "未记录"}</td>
    </tr>
  );
}

function Material({ bundle, runId }: { bundle: BundleResult | null | undefined; runId: string | null }) {
  const conclusion = bundleConclusion(bundle);
  const url = bundleDownloadUrl(runId, bundle);
  return (
    <div className="mt-6 border-t border-rule pt-5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-sm font-semibold text-ink">复跑材料</h3>
        <span className={clsx("text-sm", conclusion.tone === "success" ? "text-pass" : conclusion.tone === "warning" ? "text-warn" : "text-ink-mid")}>
          {conclusion.label}
        </span>
      </div>
      {!bundle ? (
        <p className="mt-2 text-sm leading-7 text-ink-mid">
          材料清单由服务端在收尾时生成；当前没有记录，不能提供「完整材料」下载，也不能据此判断补丁是否包含新增/删除文件。
        </p>
      ) : (
        <>
          <dl className="mt-3 space-y-2">
            <div><dt className="text-xs text-ink-mid">清单摘要</dt><dd><Digest value={bundle.manifest_digest} /></dd></div>
            <div><dt className="text-xs text-ink-mid">压缩包 SHA-256</dt><dd><Digest value={bundle.sha256} /></dd></div>
          </dl>
          {bundle.missing && bundle.missing.length > 0 && (
            <div className="mt-3">
              <p className="text-xs text-warn">未包含的内容（完整性按缺项判定，不静默遗漏）：</p>
              <ul className="mt-1 list-disc space-y-1 pl-4 text-sm leading-6 text-ink-mid">
                {bundle.missing.map((m, i) => <li key={`${m.path}:${i}`} className="[overflow-wrap:anywhere]">{m.path} —— {m.reason}</li>)}
              </ul>
            </div>
          )}
          {bundle.external_inputs && bundle.external_inputs.length > 0 && (
            <p className="mt-3 text-xs leading-6 text-ink-mid">
              仅通过合法来源引用的外部输入：{bundle.external_inputs.join("、")}。完整表示在声明的外部前提下配方齐全，不表示压缩包离线包含全部代码、数据和镜像。
            </p>
          )}
          {!conclusion.downloadable && (
            <p role="status" className="mt-3 text-sm leading-6 text-warn">
              不提供完整材料下载；报告与可安全查看的证据仍然保留。
              {bundle.integrity === "blocked" ? "导出被安全或许可条件阻止。" : "清单存在缺项或未完成写入。"}
            </p>
          )}
        </>
      )}
      {url ? (
        <a href={url} download className="mt-3 inline-block rounded text-sm text-accent underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent">
          下载复跑材料（完整性已通过）
        </a>
      ) : (
        <p className="mt-3 text-xs leading-6 text-ink-muted">下载入口在后端给出完整性通过的材料清单后才会出现。</p>
      )}
    </div>
  );
}

export function ReplayPanel({ result, runId, isDemo }: {
  result: RunResult | null;
  runId: string | null;
  isDemo?: boolean;
}) {
  if (isDemo) return (
    <section aria-label="干净环境复跑" className="border-b border-rule py-6">
      <h2 className="text-base font-semibold text-ink">干净环境复跑</h2>
      <p className="mt-2 text-sm leading-7 text-ink-mid">示例回放没有真实的复跑记录或材料包，不提供下载。</p>
    </section>
  );

  const replay: ReplayResult | null | undefined = result?.replay;
  const conclusion = replayConclusion(replay);
  const current = result?.verification ?? null;
  const replayVerification = replay?.verification ?? null;

  return (
    <section aria-label="干净环境复跑" className="min-w-0 border-b border-rule py-6">
      <h2 className="text-base font-semibold text-ink">干净环境复跑</h2>
      <p className={clsx("mt-2 text-sm font-medium", {
        "text-pass": conclusion.tone === "success", "text-reject": conclusion.tone === "error",
        "text-warn": conclusion.tone === "warning", "text-ink-mid": conclusion.tone === "neutral",
      })}>{conclusion.label}</p>
      <p className="mt-2 max-w-[70ch] text-sm leading-7 text-ink-mid">{BOUNDARY}</p>

      {!replay ? (
        <div className="mt-4 rounded-lg border border-dashed border-rule-hi px-4 py-4 text-sm leading-7 text-ink-mid">
          <p>后端尚未下发干净复跑结果。当前环境的验证记录仍然有效，但不能据此判断修复在新目录、新容器中是否可复现。</p>
          <p className="mt-1 text-xs text-ink-muted">
            需要服务端按评审稿 §8.6 记录：新会话标识、原始 commit、修复清单摘要、镜像/数据/依赖摘要、同一规则下的每项验证结果。
          </p>
        </div>
      ) : (
        <>
          <dl className="mt-4 space-y-3">
            <Field label="复跑状态" value={`${replay.status}${replay.reason ? `（${replay.reason}）` : ""}`} />
            <Field label="新会话标识" value={replay.session_id} />
            <Field label="开始 / 结束" value={[replay.started_at, replay.finished_at].filter(Boolean).join(" → ") || null} />
            <Field label="原始代码版本" value={replay.base_revision} />
          </dl>
          <details className="mt-4 border-t border-rule pt-3">
            <summary className="cursor-pointer text-sm leading-6 text-ink-mid hover:text-ink">冻结输入的内容摘要</summary>
            <dl className="mt-3 space-y-3">
              <div><dt className="text-xs text-ink-mid">修复清单</dt><dd><Digest value={replay.repair_manifest_digest} /></dd></div>
              <div><dt className="text-xs text-ink-mid">镜像</dt><dd><Digest value={replay.image_digest} /></dd></div>
              <div><dt className="text-xs text-ink-mid">数据版本</dt><dd><Digest value={replay.dataset_digest} /></dd></div>
              <div><dt className="text-xs text-ink-mid">依赖还原</dt><dd><Digest value={replay.dependency_digest} /></dd></div>
            </dl>
          </details>
          <div className="mt-4 overflow-x-auto">
            <table className="w-full min-w-[520px] border-collapse text-left">
              <caption className="pb-2 text-left text-sm font-medium text-ink">当前环境与干净复跑使用同一验证规则</caption>
              <thead className="border-y border-rule text-xs text-ink-mid">
                <tr><th scope="col" className="py-2 pr-3 font-medium">环境</th><th scope="col" className="py-2 pr-3 font-medium">等级 / 状态</th><th scope="col" className="py-2 pr-3 font-medium">规则 ID</th><th scope="col" className="py-2 font-medium">产物 SHA-256</th></tr>
              </thead>
              <tbody>
                <VerificationRow label="当前环境" v={current} />
                <VerificationRow label="干净复跑" v={replayVerification} />
              </tbody>
            </table>
          </div>
          {replay.checks && replay.checks.length > 0 && (
            <ul className="mt-3 divide-y divide-rule border-y border-rule text-sm leading-6">
              {replay.checks.map((c, i) => (
                <li key={`${c.name}:${i}`} className="flex items-baseline justify-between gap-3 py-2">
                  <span className="min-w-0 text-ink-mid [overflow-wrap:anywhere]">{c.name}：{c.detail}</span>
                  <span className={clsx("shrink-0 text-xs", c.status === "passed" ? "text-pass" : c.status === "failed" ? "text-reject" : "text-warn")}>{c.status}</span>
                </li>
              ))}
            </ul>
          )}
          {replay.differences && replay.differences.length > 0 && (
            <p className="mt-3 text-sm leading-7 text-warn [overflow-wrap:anywhere]">不一致原因：{replay.differences.join("；")}</p>
          )}
          {replay.limitations && replay.limitations.length > 0 && (
            <ul className="mt-2 list-disc space-y-1 pl-4 text-xs leading-6 text-ink-mid">
              {replay.limitations.map((l, i) => <li key={i} className="[overflow-wrap:anywhere]">{l}</li>)}
            </ul>
          )}
        </>
      )}
      <Material bundle={result?.bundle} runId={runId} />
    </section>
  );
}
