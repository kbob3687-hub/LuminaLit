import type { PreflightResult, PreflightSettings, RunEvent, RunStatus, Target } from "./types";

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
  return new ApiError(message || code || `HTTP ${res.status}`, res.status, code);
}

export function health() {
  return req<{ status: string }>("/api/health");
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
export function confirmPreflight(preflight: Pick<PreflightResult, "preflight_id" | "confirmation_digest">) {
  return req<{ run_id: string; state: string }>("/api/runs", {
    method: "POST",
    body: JSON.stringify({
      preflight_id: preflight.preflight_id,
      confirmation_digest: preflight.confirmation_digest,
    }),
  });
}

/** 自定义仓库探索运行，命令由模型分析；内置靶场必须先预检再确认。 */
export function createRun(body: { repo_url: string; revision?: string; command?: string[] }) {
  return req<{ run_id: string; state: string }>("/api/runs", {
    method: "POST",
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
