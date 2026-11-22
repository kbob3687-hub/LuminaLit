/** 契约 §4 的接口形状，与后端字段严格对应。 */

export type RunState =
  | "queued"
  | "preparing"
  | "planning"
  | "installing"
  | "testing"
  | "diagnosing"
  | "repairing"
  | "succeeded"
  | "failed"
  | "cancelled";

export const TERMINAL: ReadonlySet<RunState> = new Set(["succeeded", "failed", "cancelled"]);

export interface Target {
  id: string;
  repo_url: string;
  command: string[];
  expected_artifact: string | null;
}

export interface RunResult {
  run_id: string;
  outcome: "succeeded" | "failed" | "budget_exceeded" | "unrepairable" | "cancelled";
  verified: boolean;
  attempts: number;
  failure_category: string | null;
  resolved_revision: string | null;
  patch_url: string | null;
  environment_url: string | null;
  report_url: string;
}

export interface RunStatus {
  run_id: string;
  state: RunState;
  result: RunResult | null;
}

/**
 * `GET /api/runs/{id}/environment` 的形状，对应
 * `runner._build_environment_manifest()`。
 *
 * 全部字段都可选，因为历史 run 里确实缺：`base_image` 是 2026-11-18 之后才写的
 * （PR #15），排队阶段就被取消的 run 更是一个空对象 `{}`。把必填写在类型上会
 * 让界面拿 undefined 当字符串渲染，不如老实承认它可能没有。
 *
 * `environment` / `packages` 只有真的跑过沙箱会话才有。fake 沙箱不实现
 * `get_environment` / `collect_packages`，这两个字段就不会出现——界面据此
 * 说明"没有采集"，不拿空对象冒充"没有环境变量"。
 */
export interface EnvironmentManifest {
  resolved_revision?: string | null;
  python_version?: string;
  base_image?: string;
  command?: string[] | null;
  environment?: Record<string, string>;
  packages?: string[];
  /**
   * 沙箱清理失败计数（runner._record_cleanup_failures 写）。
   * 后端仅在**有**失败时写入该键；没有这个键 = 没有清理失败记录。
   * 契约把「容器清理失败为 0」当作硬指标，靠这里的记数才能被审计。
   */
  cleanup_failures?: number;
}

export interface RunEvent {
  run_id: string;
  sequence: number;
  timestamp: string;
  state: RunState;
  kind: string;
  message: string;
  data: Record<string, unknown>;
  log_path: string | null;
}

/** ACI 的 8 种失败标签（设计 §8，2026-11-17 修订）。 */
export const FAILURE_LABELS = {
  file_not_found: "文件不存在",
  replace_not_found: "未找到待替换内容",
  replace_not_unique: "匹配不唯一",
  unknown_action: "未知动作",
  requirement_not_allowed: "依赖字符串非法",
  path_escape: "路径越界",
  env_not_allowed: "环境变量不在白名单",
  invalid_argument: "参数越界",
  protected_file: "验收链文件受保护",
} as const;

export type FailureLabel = keyof typeof FAILURE_LABELS;

/** 从 summary 里取标签词干（`path_escape:xx` → `path_escape`）。 */
export function labelOf(summary: string): string {
  return summary.split(":", 1)[0];
}

/** 三分法：校验类（我们拦下）/ 环境类（外部原因）/ 系统类（我们的问题）。 */
export type FailureKind = "aci" | "env" | "system";

const ACI_KINDS: ReadonlySet<string> = new Set([
  "path_escape",
  "replace_not_unique",
  "replace_not_found",
  "env_not_allowed",
  "unknown_action",
  "requirement_not_allowed",
  "file_not_found",
  "invalid_argument",
  "protected_file",
]);

const SYSTEM_KINDS: ReadonlySet<string> = new Set([
  "internal_error",
  "service_interrupted",
  "sandbox_unavailable",
]);

export function kindOf(category: string | null): FailureKind {
  if (!category) return "env";
  const stem = labelOf(category);
  if (ACI_KINDS.has(stem)) return "aci";
  if (SYSTEM_KINDS.has(stem)) return "system";
  return "env";
}
