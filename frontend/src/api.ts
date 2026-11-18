import type { RunEvent, RunStatus, Target } from "./types";

/** 契约 §4：基地址来自 VITE_API_BASE_URL，轮询间隔 1000ms。 */
const BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    // 契约 §4.1：429 是 queue_full，422 取 detail[0].msg
    if (res.status === 429) throw new ApiError("queue_full", 429);
    if (res.status === 422) {
      const body = (await res.json()) as { detail?: Array<{ msg?: string }> };
      throw new ApiError(body.detail?.[0]?.msg ?? "输入不合法", 422);
    }
    if (res.status === 404) throw new ApiError("run_not_found", 404);
    throw new ApiError(`HTTP ${res.status}`, res.status);
  }
  return res.json() as Promise<T>;
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export function health() {
  return req<{ status: string }>("/api/health");
}

export function targets() {
  return req<Target[]>("/api/targets");
}

/**
 * 提交运行。`revision` 不是可选装饰：同一 repo_url 下挂了多个内置靶场，
 * 后端按 repo_url 匹配靶场、按 revision 检出代码，不带 revision 只会跑到
 * 仓库默认分支（多数靶场分支因此永远不会被命中）。
 */
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
  if (!res.ok) throw new ApiError(`HTTP ${res.status}`, res.status);
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
