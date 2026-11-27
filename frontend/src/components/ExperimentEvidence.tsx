import { bundleConclusion, replayConclusion, verificationConclusion } from "../runModel";
import type { RunResult } from "../types";

/** Scientific scope is fixed metadata; every outcome below comes from this run. */
export function ExperimentEvidence({ result }: { result: RunResult | null }) {
  if (result?.verification?.rule_id !== "sindy-lorenz-v1") return null;
  const current = verificationConclusion(result.verification);
  const replay = replayConclusion(result.replay);
  const bundle = bundleConclusion(result.bundle);
  return (
    <section aria-label="Lorenz 实验结果" className="my-7 overflow-hidden rounded-xl border border-rule-hi bg-surface">
      <div className="px-5 py-5 sm:px-6">
        <h2 className="text-xl font-semibold text-ink">从数据中重新识别 Lorenz 方程</h2>
        <p className="mt-2 max-w-[70ch] text-sm leading-7 text-ink-mid">
          检查 SINDy 能否找回三个方程的系数，包括应当为零的项。目标：每个方程的最大系数误差严格小于 0.001。
        </p>
        <div className="mt-4 flex flex-wrap gap-x-6 gap-y-1 text-xs leading-6 text-ink-mid">
          <a href="https://pmc.ncbi.nlm.nih.gov/articles/PMC4839439/" target="_blank" rel="noreferrer" className="text-accent underline underline-offset-4">原始论文与 Lorenz 方程</a>
          <a href="https://github.com/dynamicslab/pysindy/blob/4c32d2603cbf1aa476efae72bc78436cb1e6fc75/examples/3_original_paper.ipynb" target="_blank" rel="noreferrer" className="text-accent underline underline-offset-4">固定版本的上游实验</a>
        </div>
      </div>
      <dl className="grid border-y border-rule sm:grid-cols-3">
        {[["当前指标验收", current.label], ["干净环境复跑", replay.label], ["交接材料", bundle.label]].map(([label, value]) => (
          <div key={label} className="border-b border-rule px-5 py-4 last:border-0 sm:border-r sm:border-b-0 sm:px-6">
            <dt className="text-xs text-ink-mid">{label}</dt>
            <dd className="mt-2 text-sm font-medium leading-6 text-ink">{value}</dd>
          </div>
        ))}
      </dl>
      <div className="px-5 py-5 sm:px-6">
        {result.verification.metrics.length > 0 ? (
          <dl className="grid gap-4 sm:grid-cols-3">
            {result.verification.metrics.map(metric => (
              <div key={metric.name}>
                <dt className="text-xs text-ink-mid">{metric.name}</dt>
                <dd className="mt-2 font-mono text-lg tabular-nums text-ink">{metric.observed.toExponential(4)}</dd>
                <dd className={`mt-1 text-xs ${metric.passed ? "text-pass" : "text-reject"}`}>{metric.passed ? "满足固定标准" : "未满足固定标准"}</dd>
              </div>
            ))}
          </dl>
        ) : <p className="text-sm text-ink-mid">尚无已记录的指标观测值。</p>}
        <p className="mt-5 text-xs leading-6 text-ink-mid">
          范围：PySINDy 检出源码上的低噪声系数识别子实验。采用上游示例初值，未重现论文全部实验或轨迹图。干净复跑使用同一宿主机的新容器；跨机器交接需另行验证。
        </p>
      </div>
    </section>
  );
}
