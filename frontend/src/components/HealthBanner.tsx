import clsx from "clsx";
import type { HealthCheck, HealthDetails } from "../types";

/**
 * 评审稿 §11.3 / R12：新手启动前的宿主健康检查。
 *
 * 默认 `/api/health` 只是进程存活探针，不能因为 API 能访问就断言 Docker、
 * 镜像或模型已就绪。逐项结果只来自 `?details=true`；后端尚未实现该端点时
 * 明确写「未提供逐项结果」，不拿一个 200 冒充全部可用。
 *
 * 不回显模型密钥、代理凭据或宿主绝对路径；不发起任何付费推理请求。
 */

const LABELS: Record<string, string> = {
  python: "Python 运行时",
  python_version: "Python 运行时",
  docker: "Docker 引擎",
  docker_engine: "Docker 引擎",
  base_image: "基础镜像",
  image: "基础镜像",
  llm_config: "模型配置",
  model_config: "模型配置",
  model_connectivity: "模型网关连通性",
  network: "网络访问",
};

const STATUS: Record<HealthCheck["status"], { label: string; cls: string }> = {
  available: { label: "可用", cls: "text-pass" },
  missing: { label: "缺失", cls: "text-reject" },
  unsupported: { label: "当前环境不支持", cls: "text-warn" },
  not_checked: { label: "未检查", cls: "text-ink-muted" },
};

/** 兜底：即便后端误带了敏感值，界面也不回显。 */
function sanitize(text: string): string {
  return text
    .replace(/sk-[A-Za-z0-9_-]{8,}/g, "[已隐藏]")
    .replace(/(Bearer\s+)[A-Za-z0-9._-]{8,}/gi, "$1[已隐藏]")
    .replace(/[A-Za-z]:[\\/][^\s，。；)]+/g, "[已隐藏路径]")
    .replace(/(?:\/Users\/|\/home\/)[^\s，。；)]+/g, "[已隐藏路径]");
}

export function HealthBanner({ details, error }: { details: HealthDetails | null; error?: string | null }) {
  if (!details || !Array.isArray(details.checks)) return (
    <section aria-label="启动前检查" className="rounded-xl border border-rule bg-surface/40 p-4">
      <h2 className="text-sm font-semibold text-ink">启动前检查</h2>
      <p className="mt-2 text-sm leading-7 text-ink-mid">
        后端尚未提供 <code className="font-mono text-xs">/api/health?details=true</code> 的逐项结果。
        <strong className="font-medium text-warn">这不代表 Docker、基础镜像和模型配置已经就绪</strong>；
        请按 README 的最短启动步骤自行核对，缺组件时先补齐再开始任务。
      </p>
      {error && <p className="mt-1 text-xs leading-6 text-ink-muted">检查请求未成功：{sanitize(error)}</p>}
    </section>
  );

  const checks = details.checks;
  const missing = checks.filter((c) => c.status === "missing" || c.status === "unsupported");
  return (
    <section aria-label="启动前检查" className="rounded-xl border border-rule bg-surface/40 p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold text-ink">启动前检查</h2>
        <span className="font-mono text-[10.5px] text-ink-muted">
          {checks.length} 项 · {missing.length > 0 ? `${missing.length} 项待处理` : "无已知缺失"}
        </span>
      </div>
      {missing.length > 0 && (
        <p role="status" className="mt-2 text-sm leading-7 text-warn">
          有组件缺失或当前环境不支持；按每项的「下一步」处理后再开始运行，不要靠反复重试掩盖启动条件不足。
        </p>
      )}
      <ul className="mt-3 divide-y divide-rule border-y border-rule">
        {checks.map((c, i) => {
          const s = STATUS[c.status] ?? STATUS.not_checked;
          return (
            <li key={`${c.id}:${i}`} className="py-2.5 text-sm leading-6">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <span className="font-medium text-ink">{LABELS[c.id] ?? c.id}</span>
                <span className={clsx("shrink-0 text-xs", s.cls)}>{s.label}</span>
              </div>
              <p className="text-ink-mid [overflow-wrap:anywhere]">{sanitize(c.detail || "后端没有给出说明。")}</p>
              {c.next_step && <p className="mt-0.5 text-xs text-ink-muted">下一步：{sanitize(c.next_step)}</p>}
              {c.status === "not_checked" && <p className="mt-0.5 text-xs text-ink-muted">未做连接检查，不代表该项可用。</p>}
            </li>
          );
        })}
      </ul>
      <p className="mt-3 text-xs leading-6 text-ink-muted">
        模型配置齐全不等于网关已连通，更不等于推理成功。健康检查不自动发起付费推理请求，也不回显密钥、代理凭据或宿主路径。
      </p>
    </section>
  );
}
