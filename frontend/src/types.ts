/** 契约 §4 的接口形状，与后端字段严格对应。 */

export type RunState =
  | "queued"
  | "preparing"
  | "planning"
  | "installing"
  | "testing"
  | "diagnosing"
  | "repairing"
  | "verifying"
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

export interface PreflightSettings {
  /** 每个执行步骤的超时秒数，不是整个运行的总预算。 */
  timeout_seconds: number;
  max_repairs: number;
}

/** 固定验证规则来自服务端确认单，不能由前端修改。 */
export interface VerificationSpec {
  rule_id: "pysindy-linear-v1";
  validator_digest: string;
  artifact: "coefficients.json";
  threshold: 0.0001;
  code_under_test: string;
  dataset: string;
  source: string;
}

export interface VerificationCheck {
  name: string;
  passed: boolean;
  detail: string;
}

export interface MetricResult {
  name: string;
  observed: number;
  threshold: number;
  comparison: "lt";
  passed: boolean;
}

export interface VerificationResult {
  /** 检查类型，不是已获得的等级；必须与 status 一起解读。 */
  level: "none" | "execution" | "artifact" | "target";
  status: "not_run" | "passed" | "failed" | "blocked";
  summary: string;
  checks: VerificationCheck[];
  metrics: MetricResult[];
  rule_id: string | null;
  validator_digest: string | null;
  artifact_sha256: string | null;
  verifier_image_id: string | null;
  checked_at: string | null;
  code_under_test: string | null;
  dataset: string | null;
  source: string | null;
  failure_category: string | null;
  evidence_url: string | null;
  limitations: string[];
}

export interface PreflightResult extends PreflightSettings {
  preflight_id: string;
  target_id: string;
  repo_url: string;
  created_at: string;
  expires_at: string;
  confirmation_digest: string;
  plan: {
    resolved_revision: string;
    python_version: "3.10" | "3.11";
    command: string[];
    expected_artifact: string | null;
    source: "user" | "target_manifest" | "repository_analysis";
  };
  checks: Array<{
    name: string;
    status: "passed" | "warning" | "not_checked";
    detail: string;
    source: string;
  }>;
  limitations: string[];
  run_id: string | null;
  verification?: VerificationSpec | null;
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
  verification?: VerificationResult | null;
}

export interface EventExplanation {
  title: string;
  detail: string;
  tone: "neutral" | "success" | "warning" | "error";
}

export interface RunGuidance extends EventExplanation {
  next_steps: string[];
  repair_summary: string;
}

export interface RunStatus {
  run_id: string;
  state: RunState;
  result: RunResult | null;
  guidance?: RunGuidance;
  last_event_sequence?: number;
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
  explanation?: EventExplanation;
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
