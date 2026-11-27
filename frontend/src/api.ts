import type {
  BundleResult,
  EnvironmentManifest,
  HealthDetails,
  PreflightResult,
  PreflightSettings,
  RunEvent,
  RunStatus,
  Target,
  VerificationResult,
} from "./types";

/** 契约 §4：基地址来自 VITE_API_BASE_URL，轮询间隔 1000ms。 */
const BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) throw await responseError(res);
  return res.json() as Promise<T>;
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** 兼容结构化 detail、FastAPI 422 数组，以及普通字符串/非 JSON 错误。 */
async function responseError(res: Response): Promise<ApiError> {
  const text = await res.text();
  let detail: unknown = text;
  try {
    const body: unknown = JSON.parse(text);
    detail = body && typeof body === "object" && "detail" in body ? body.detail : body;
  } catch {
    // 网关等服务可能返回纯文本；仍保留 HTTP 状态供调用方判断。
  }
  let message: string | undefined;
  let code: string | undefined;
  if (typeof detail === "string") {
    message = detail.trim();
  } else if (Array.isArray(detail)) {
    message = detail.map((item: unknown) => {
      if (typeof item === "string") return item;
      return item && typeof item === "object" && "msg" in item && typeof item.msg === "string"
        ? item.msg
        : "";
    }).filter(Boolean).join("；");
  } else if (detail && typeof detail === "object") {
    if ("message" in detail && typeof detail.message === "string") message = detail.message;
    if ("code" in detail && typeof detail.code === "string") code = detail.code;
  }
  if (!code && res.status === 429) code = "queue_full";
  if (!message && code === "queue_full") message = "当前等待任务较多，请稍后重试。";
  const bundleErrors: Record<string, string> = {
    bundle_corrupted: "材料包校验失败，文件可能已被修改或截断。本次未下载，请重新运行生成材料。",
    bundle_incomplete: "复跑材料尚不完整，暂时无法下载。已有报告与验证证据仍可查看。",
    bundle_blocked: "材料导出受阻，请查看材料清单中的原因。已有验证证据仍然保留。",
    bundle_not_ready: "材料包尚未生成，请稍后重试。",
  };
  if (!message && code) message = bundleErrors[code];
  return new ApiError(message || code || `HTTP ${res.status}`, res.status, code);
}

export function health() {
  return req<{ status: string }>("/api/health");
}

/**
 * 评审稿 §11.3：`?details=true` 才返回逐项检查；默认 /api/health 仍是进程
 * 存活探针。检查项只回脱敏状态，不含密钥、宿主路径或容器标识。
 */
export function healthDetails() {
  return req<HealthDetails>("/api/health?details=true");
}

export function targets() {
  return req<Target[]>("/api/targets");
}

export function createPreflight(body: { target_id: string } & Partial<PreflightSettings>) {
  return req<PreflightResult>("/api/preflights", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function getPreflight(preflightId: string) {
  return req<PreflightResult>(`/api/preflights/${encodeURIComponent(preflightId)}`);
}

/** 只确认服务端冻结的计划；不得混入仓库、命令或更改后的设置。 */
/**
 * 幂等键：确认单的摘要由服务端冻结，天然适合同一确认的重试；
 * 不能靠按钮变灰保证「重复点击只创建一个任务」（评审稿 §5.4 / §9.2）。
 */
export function confirmPreflight(preflight: Pick<PreflightResult, "preflight_id" | "confirmation_digest">) {
  return req<{ run_id: string; state: string }>("/api/runs", {
    method: "POST",
    headers: { "Idempotency-Key": preflight.confirmation_digest },
    body: JSON.stringify({
      preflight_id: preflight.preflight_id,
      confirmation_digest: preflight.confirmation_digest,
    }),
  });
}

/** 同一份输入在同一会话里重试必须复用同一个幂等键（不含用户输入原文）。 */
function idempotencyKey(body: unknown): string {
  const text = JSON.stringify(body);
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i += 1) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  const digest = (h >>> 0).toString(16).padStart(8, "0");
  const slot = `ph.idem.${digest}`;
  try {
    const existing = sessionStorage.getItem(slot);
    if (existing) return existing;
    const created = `run-${digest}-${Date.now().toString(36)}`;
    sessionStorage.setItem(slot, created);
    return created;
  } catch {
    return `run-${digest}`;
  }
}

/** 自定义仓库探索运行，命令由模型分析；内置靶场必须先预检再确认。 */
export function createRun(body: { repo_url: string; revision?: string; command?: string[] }) {
  return req<{ run_id: string; state: string }>("/api/runs", {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey(body) },
    body: JSON.stringify(body),
  });
}

export function getRun(runId: string) {
  return req<RunStatus>(`/api/runs/${runId}`);
}

export function getEvents(runId: string, after = 0) {
  return req<RunEvent[]>(`/api/runs/${runId}/events?after=${after}`);
}

export function cancelRun(runId: string) {
  return req<{ run_id: string; state: string }>(`/api/runs/${runId}/cancel`, { method: "POST" });
}

/** 纯文本端点。没有改动时返回空字符串，不是 404。 */
export async function getText(path: string): Promise<string> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) throw await responseError(res);
  return res.text();
}

/**
 * 结果报告（后端 `report.md` 原文）。
 *
 * 运行中会 404（`report_not_ready`）——后端只在收尾时落盘，这不是错误，
 * 界面用「报告尚未生成」表示即可。`patch.diff` 同理，但没有 diff 时后端
 * 回空串而不是 404（无改动 = 空补丁，是正常结果）。
 */
export function getReport(runId: string) {
  return getText(`/api/runs/${runId}/report`);
}

export function getPatch(runId: string) {
  return getText(`/api/runs/${runId}/patch`);
}

/**
 * 环境清单（environment.json）。404 有两种含义：run_id 不对，或收尾前
 * 尚未落盘；后者是正常状态，界面显示「尚未采集」，不拿空对象冒充。
 */
export async function getEnvironment(runId: string): Promise<EnvironmentManifest> {
  const res = await fetch(`${BASE}/api/runs/${runId}/environment`);
  if (res.status === 404) throw await responseError(res);
  if (!res.ok) throw await responseError(res);
  return res.json() as Promise<EnvironmentManifest>;
}

/** 评审稿 §9.2：结构化验收结果；不从 report 文本反推。 */
export function getVerification(runId: string) {
  return req<VerificationResult>(`/api/runs/${runId}/verification`);
}

/**
 * 复跑材料下载。字段（integrity / sha256 / 缺项）来自 result.bundle，
 * 这里只给同源 URL；不完整时后端的 404/409 会带 bundle_incomplete /
 * bundle_blocked，界面据此显示缺项，不提供误导性下载。
 */
export function bundleDownloadUrl(runId: string | null | undefined, bundle: BundleResult | null | undefined): string | null {
  if (!bundle || bundle.integrity !== "complete" || !bundle.url) return null;
  if (!runId || !/^[0-9a-f]{32}$/.test(runId)) return null;
  // 只允许本次运行的固定下载端点，包括拒绝同前缀下的 ../ 或查询参数。
  if (bundle.url !== `/api/runs/${runId}/bundle`) return null;
  return `${BASE}${bundle.url}`;
}

/** 先检查 HTTP 结果，再保存二进制文件；409 JSON 不能被另存为 ZIP。 */
export async function fetchBundle(runId: string | null, bundle: BundleResult | null | undefined, signal?: AbortSignal): Promise<Blob> {
  const url = bundleDownloadUrl(runId, bundle);
  if (!url) throw new Error("当前没有完整且可下载的复跑材料。");
  const res = await fetch(url, { signal });
  if (!res.ok) throw await responseError(res);
  if (res.headers.get("Content-Type")?.split(";")[0].trim() !== "application/zip") {
    throw new Error("服务返回的不是 ZIP 材料包，本次未下载，请稍后重试。");
  }
  return res.blob();
}
