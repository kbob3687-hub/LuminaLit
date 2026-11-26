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
  | "replaying"
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

/**
 * 评审稿 §3.1：干净复跑状态。它与「当前环境验证结果」是两个独立维度，
 * 当前验收 passed 不代表复跑 passed，更不能反过来抹掉已取得的证据。
 */
export type ReplayStatus =
  | "not_requested"
  | "pending"
  | "running"
  | "passed"
  | "failed"
  | "blocked";

export interface ReplayCheck {
  name: string;
  status: "passed" | "failed" | "blocked" | "not_run";
  detail: string;
}

/**
 * `ReplayResult`（评审稿 §7.4 / §8.6）：新会话、原始 commit、冻结修复清单、
 * 镜像/数据/依赖摘要、同一规则下的每项验证结果与不一致原因。
 *
 * 旧记录可能缺少这些字段；缺失时不能拿 verified 或 state 推断复跑结论。
 */
export interface ReplayResult {
  status: ReplayStatus;
  reason?: string | null;
  session_id?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  base_revision?: string | null;
  repair_manifest_digest?: string | null;
  image_digest?: string | null;
  dataset_digest?: string | null;
  dependency_digest?: string | null;
  /** 复跑使用的验证规则/阈值必须与当前验收一致，界面据此对照。 */
  verification?: VerificationResult | null;
  checks?: ReplayCheck[];
  differences?: string[];
  limitations?: string[];
}

/** 评审稿 §7.4 / §10.3：材料完整性是独立结论，不等于复跑通过。 */
export type BundleIntegrity = "complete" | "incomplete" | "blocked";

export interface BundleResult {
  integrity: BundleIntegrity;
  items?: Array<{ path: string; kind: "source" | "artifact" | "metadata"; status: "included" | "missing" | "blocked"; reason: string | null; size: number; sha256: string | null }>;
  total_bytes?: number;
  /** 只有 integrity=complete 且压缩包写入完成，后端才会给可下载 URL。 */
  url: string | null;
  sha256: string | null;
  manifest_digest: string | null;
  entries?: Array<{ path: string; sha256: string; size: number }>;
  missing?: Array<{ path: string; reason: string }>;
  /** 仅通过合法来源引用的外部输入（代码/数据/镜像），不计入离线包。 */
  external_inputs?: string[];
  limitations?: string[];
}

export interface BudgetUsage {
  total_seconds: number | null;
  used_seconds: number | null;
  model_calls: number | null;
  model_call_limit: number | null;
  token_in: number | null;
  token_out: number | null;
  /** 后端没有可用价格表时费用为未知，界面不得编造金额。 */
  cost_known: boolean;
}

/** 评审稿 §11.3：健康检查逐项状态，默认 /api/health 仍是存活探针。 */
export interface HealthCheck {
  id: string;
  status: "available" | "missing" | "unsupported" | "not_checked";
  detail: string;
  next_step: string | null;
}

export interface HealthDetails {
  status: string;
  checks: HealthCheck[];
}

/** 环境清单（后端 environment.json），旧版 run 可能整个缺文件。 */
export interface EnvironmentManifest {
  resolved_revision?: string | null;
  python_version?: string;
  base_image?: string;
  command?: string[] | null;
  environment?: Record<string, string>;
  packages?: string[];
  cleanup_failures?: number;
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
  /** 以下为评审稿 §9.2 的增量字段，旧记录一律没有，界面按「未记录」处理。 */
  replay?: ReplayResult | null;
  bundle?: BundleResult | null;
  bundle_url?: string | null;
  effective_plan_digest?: string | null;
  code_under_test?: string | null;
  budget_usage?: BudgetUsage | null;
  evidence?: string[] | null;
  limitations?: string[];
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
  /**
   * §9.3：final 落盘、finalized=true 且事件游标达到 final_event_sequence
   * 才能停止刷新。历史记录没有这些字段时按「历史完整性提示」读取，
   * 不进入等待 finalized 的无限轮询。
   */
  finalized?: boolean;
  final_event_sequence?: number;
  schema_version?: number;
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

/** ACI 失败标签（设计 §8，2026-11-17 修订；v0.2 补执行权限与验收链保护）。 */
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
  action_not_allowed: "动作不在任务允许范围",
  change_not_allowed: "变更不在允许范围",
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
  "action_not_allowed",
  "change_not_allowed",
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
