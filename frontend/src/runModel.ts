/**
 * 从后端事件流推导界面状态（阶段条 / 自愈回环 / ACI 校验轨迹）。
 *
 * 原则：**界面上的"已完成"只能由后端事件证明**，不能由前端按索引猜测。
 * 之前 `i < stageIndex(state)` 的写法会在 run 一开始就把前面所有阶段
 * 点亮成绿色（demo 里更糟：mock 状态直接是 repairing，进场即 6 个绿）。
 * 这里改成：某阶段只有真的收到过该阶段的事件、且已经离开它，才算完成。
 */

import {
  labelOf,
  TERMINAL,
  type BundleResult,
  type EventExplanation,
  type ReplayResult,
  type RunEvent,
  type RunState,
  type RunStatus,
  type VerificationResult,
} from "./types";

export const VERIFICATION_TYPES: Record<VerificationResult["level"], string> = {
  none: "未指定检查类型",
  execution: "执行检查",
  artifact: "产物检查",
  target: "目标指标检查",
};

/** level 是检查类型；绝不能仅凭 target 或旧 verified 字段称为达标。 */
export function verificationConclusion(verification: VerificationResult | null | undefined): {
  label: string;
  tone: EventExplanation["tone"];
} {
  if (!verification) return { label: "未记录分级验证", tone: "neutral" };
  const status = {
    not_run: { label: "未运行", tone: "neutral" },
    passed: { label: "通过", tone: "success" },
    failed: { label: "未通过", tone: "error" },
    blocked: { label: "受阻", tone: "warning" },
  } as const;
  const conclusion = status[verification.status];
  if (verification.level === "none") {
    return { label: verification.status === "passed" ? "未指定检查类型" : `验证${conclusion.label}`, tone: verification.status === "passed" ? "neutral" : conclusion.tone };
  }
  return { label: `${VERIFICATION_TYPES[verification.level]}${conclusion.label}`, tone: conclusion.tone };
}

/** 只允许当前真实任务的固定同源快照端点，不接受任意 URL 或路径。 */
export function verificationEvidencePath(runId: string | null, evidenceUrl: string | null | undefined): string | null {
  if (!runId || !/^[0-9a-f]{32}$/.test(runId)) return null;
  const path = `/api/runs/${runId}/verification/input`;
  return evidenceUrl === path ? path : null;
}

/* ─────────────────────────── 评审稿 §3.3 / §3.4：结论语义 ───────────────────────────
 *
 * 顶部结论必须由「当前环境验收 + 干净复跑 + 结束原因」三者共同决定。
 * 下面几条是硬约束，改之前请先读评审稿：
 *   - `verified` 只代表当前环境验收 passed，不能因复跑失败被抹掉，也不能
 *     单独用来显示「完整成功」。
 *   - 历史结果可能没有 replay / bundle 字段，缺字段时
 *     一律显示「未记录」，绝不推断成 passed 或 not_requested。
 *   - 只有 replay.status=passed 且 bundle.integrity=complete 才算可交付的
 *     完整成功；材料缺项必须留在 gaps 里，不能用绿色徽标盖住。
 *   - 复跑失败 / 条件不足 / 未要求复跑，分别有各自的固定文案。
 */

export interface OverallConclusion {
  label: string;
  detail: string;
  tone: EventExplanation["tone"];
  /** 是否允许展示「完整可复跑成功」。 */
  completeSuccess: boolean;
  /** 未验证、未记录或缺失的事项，必须原样留在结论里。 */
  gaps: string[];
}

const REPLAY_COPY: Record<ReplayResult["status"], { label: string; tone: EventExplanation["tone"] }> = {
  not_requested: { label: "未要求干净复跑", tone: "neutral" },
  pending: { label: "等待干净复跑", tone: "neutral" },
  running: { label: "干净复跑进行中", tone: "neutral" },
  passed: { label: "干净复跑通过", tone: "success" },
  failed: { label: "干净复跑未通过", tone: "error" },
  blocked: { label: "复跑条件不足", tone: "warning" },
};

export function replayConclusion(replay: ReplayResult | null | undefined) {
  if (!replay) return { label: "后端未下发复跑结果", detail: "这次运行没有干净复跑记录，不能据此判断可复跑性。", tone: "neutral" as const };
  const base = REPLAY_COPY[replay.status] ?? REPLAY_COPY.pending;
  const reasons: Record<string, string> = {
    cancelled: "已取消", budget_exceeded: "预算已用完",
    not_started_before_termination: "任务结束前尚未开始复跑",
    replay_clone_failed: "代码检出失败", replay_verifier_error: "验收器故障",
    replay_error: "复跑服务发生异常", replay_material_incomplete: "冻结材料不完整",
    replay_sandbox_unavailable: "复跑环境不可用",
  };
  const reason = replay.reason ? `（${reasons[replay.reason] ?? replay.reason}）` : "";
  return { label: `${base.label}${reason}`, tone: base.tone, detail: replay.differences?.join("；") || "" };
}

/** 材料完整性是独立结论：不完整不提供下载，也不能冒充已交付。 */
export function bundleConclusion(bundle: BundleResult | null | undefined): {
  label: string;
  tone: EventExplanation["tone"];
  downloadable: boolean;
} {
  if (!bundle) return { label: "后端未下发材料清单", tone: "neutral", downloadable: false };
  if (bundle.integrity === "complete" && bundle.url) {
    return { label: "复跑材料完整", tone: "success", downloadable: true };
  }
  if (bundle.integrity === "blocked") {
    return { label: "复跑材料被阻止导出", tone: "warning", downloadable: false };
  }
  return { label: "复跑材料不完整", tone: "warning", downloadable: false };
}

/** 顶部唯一结论：页面、报告、抽屉都用它，避免各自维护一套成功语义（§11.1）。 */
export function overallConclusion(status: RunStatus | null, isReplay: boolean): OverallConclusion {
  if (!status) return { label: "连接中", detail: "尚未取得这次运行的记录。", tone: "neutral", completeSuccess: false, gaps: [] };
  const state = status.state;
  const result = status.result;
  if (isReplay) return { label: "示例回放", detail: "离线预设记录，不代表本机执行或验证结果。", tone: "warning", completeSuccess: false, gaps: [] };
  if (!TERMINAL.has(state)) {
    if (state === "replaying") return { label: "干净复跑进行中", detail: "当前环境已有结果，正在新目录与新容器中按同一规则复验。", tone: "neutral", completeSuccess: false, gaps: [] };
    return { label: "正在运行", detail: "结论会在运行结束、结果与收尾记录写完后给出。", tone: "neutral", completeSuccess: false, gaps: [] };
  }
  if (!result) return { label: "运行已结束，正在整理结果", detail: "尚未取得最终结果，请稍候。", tone: "neutral", completeSuccess: false, gaps: [] };

  const outcome = result?.outcome;
  if (outcome === "cancelled") return { label: "已取消，同时保留已有证据", detail: "已完成的检查不会被抹去。", tone: "neutral", completeSuccess: false, gaps: [] };
  if (outcome === "budget_exceeded") return { label: "预算已用完，同时保留已通过检查", detail: "未完成的复跑不得记为通过。", tone: "warning", completeSuccess: false, gaps: ["总预算耗尽"] };
  if (outcome === "unrepairable") return { label: "当前任务条件不足或超出支持范围", detail: "不评价论文真实性。", tone: "warning", completeSuccess: false, gaps: [] };

  const verification = result?.verification ?? null;
  const verificationPassed = verification?.status === "passed";
  // 历史 run 只有 verified=true、没有结构化 verification：不能升格为「已验证」。
  const legacyOnly = !verification && result?.verified === true;
  const replay = result?.replay ?? null;
  const bundle = result?.bundle ?? null;
  const gaps: string[] = [];
  if (legacyOnly) gaps.push("历史结果未记录分级验证");
  if (!replay) gaps.push("后端未下发干净复跑结果");
  if (!bundle) gaps.push("后端未下发材料完整性清单");

  if (!verificationPassed) {
    const stage = result?.failure_category ?? verification?.failure_category;
    return {
      label: legacyOnly ? "当前运行通过，但缺少分级验证记录" : "当前运行未通过验收",
      detail: stage ? `实际失败阶段/类别：${stage}。` : "展示实际失败阶段；验收器故障不得归因于仓库。",
      tone: verification?.status === "blocked" ? "warning" : "error",
      completeSuccess: false,
      gaps,
    };
  }

  // 当前验收 passed：从此处起，结论只由复跑与材料决定。
  if (!replay || replay.status === "pending" || replay.status === "running") {
    return { label: "当前运行通过，复跑结论未记录", detail: "当前环境的证据已保留，但还不能证明修复在新环境可复现。", tone: "warning", completeSuccess: false, gaps };
  }
  if (replay.status === "not_requested") {
    return { label: "试跑通过，未验证可复跑性", detail: "这是事先确认不要求干净复跑的探索试跑。", tone: "warning", completeSuccess: false, gaps };
  }
  if (replay.status === "failed") {
    return { label: "当前运行通过，干净复跑失败", detail: "不得显示完整成功；当前环境已通过的检查仍然有效。", tone: "error", completeSuccess: false, gaps };
  }
  if (replay.status === "blocked") {
    return { label: "当前运行通过，复跑条件不足", detail: "网络或数据来源不可用导致无法复跑，不能断言修复无效。", tone: "warning", completeSuccess: false, gaps };
  }

  const bundleResult = bundleConclusion(bundle);
  if (!bundleResult.downloadable) {
    gaps.push(bundleResult.label);
    return { label: "指定等级通过，干净复跑通过，材料未齐全", detail: "复跑通过的事实保留，但材料尚未完整交付，不提供误导性的完整材料下载。", tone: "warning", completeSuccess: false, gaps };
  }
  return {
    label: "指定等级通过，干净复跑通过",
    detail: "仅代表指定版本、数据与运行配置下的指定任务通过，不等于整篇论文结论得到验证。",
    tone: "success",
    completeSuccess: true,
    gaps,
  };
}

export const ACTIONS = [
  "read_file",
  "search_text",
  "replace_exact",
  "install_requirement",
  "set_environment",
  "run_smoke_test",
  "finish_repair",
] as const;

export type ActionKind = (typeof ACTIONS)[number];

const ACTION_SET: ReadonlySet<string> = new Set(ACTIONS);

/** ACI 的 8 种失败标签（与后端 `aci/errors.py` 逐字一致）。 */
export const BLOCK_LABELS: ReadonlySet<string> = new Set([
  "file_not_found",
  "replace_not_found",
  "replace_not_unique",
  "unknown_action",
  "requirement_not_allowed",
  "path_escape",
  "env_not_allowed",
  "invalid_argument",
  "protected_file",
  "action_not_allowed",
  "change_not_allowed",
]);

export function isActionEvent(e: RunEvent): boolean {
  return ACTION_SET.has(e.kind);
}

/** 拦截标签：优先用后端显式给的 failure_label，老事件从 summary 前缀取。 */
export function labelOfEvent(e: RunEvent): string {
  const explicit = e.data?.failure_label;
  if (typeof explicit === "string" && explicit) return explicit;
  return labelOf(e.message);
}

export function isBlockedEvent(e: RunEvent): boolean {
  return isActionEvent(e) && BLOCK_LABELS.has(labelOfEvent(e));
}

/**
 * 事件里可用于展示的"目标"。
 *
 * 后端目前只回 `{ok, exit_code}` + 一句 Observation（契约 §7），动作参数
 * 并不单列。所以这里从 summary 里把目标读回来——**读不出来就返回空串**，
 * 不编一个看着像真的路径。
 */
export function targetOf(e: RunEvent): string {
  const r = (e.data?.request ?? {}) as Record<string, unknown>;
  if (typeof r.path === "string" && r.path) {
    const start = typeof r.start_line === "number" && r.start_line > 1 ? `:${r.start_line}` : "";
    return `${r.path}${start}`;
  }
  if (typeof r.requirement === "string" && r.requirement) return r.requirement;
  if (typeof r.name === "string" && r.name) {
    return typeof r.value === "string" ? `${r.name}=${r.value}` : r.name;
  }
  if (typeof r.summary === "string" && r.summary) return `"${r.summary}"`;

  const m = e.message;
  // 越界时回显模型给的原始串（errors.path_escape 的格式，逐字固定）。
  const escape = /^path_escape:(.*)$/.exec(m);
  if (escape) return escape[1];
  const env = /^env_not_allowed:(.*)$/.exec(m);
  if (env) return env[1];

  const read = /^read \d+ lines from (.+)$/.exec(m);
  if (read) return read[1];
  const replaced = /^replaced \d+ occurrences? in (.+)$/.exec(m);
  if (replaced) return replaced[1];
  const installed = /^install (.+?) exited with -?\d+$/.exec(m);
  if (installed) return installed[1];
  const setEnv = /^set ([A-Z0-9_]+=.+)$/.exec(m);
  if (setEnv) return setEnv[1];
  return "";
}

// ───────────────────────────────────────────────────────── 阶段条

export interface StageView {
  id: RunState | "succeeded";
  label: string;
  status: "todo" | "active" | "done" | "bad" | "skipped";
  /** 进入该阶段的次数：自愈回环里 installing / testing 会 > 1。 */
  count: number;
  /** 首次进入的绝对时间（ms），没有事件时为 null。 */
  startedAt: number | null;
  /** 该阶段耗时（到下一阶段开始为止），进行中为到现在。 */
  durationMs: number | null;
}

export const STAGE_ORDER: { id: RunState | "succeeded"; label: string }[] = [
  { id: "queued", label: "等待开始" },
  { id: "preparing", label: "获取项目代码" },
  { id: "planning", label: "确定运行方式" },
  { id: "installing", label: "安装所需依赖" },
  { id: "testing", label: "运行并检查结果" },
  { id: "diagnosing", label: "分析失败原因" },
  { id: "repairing", label: "尝试自动修复" },
  { id: "verifying", label: "独立核验科学指标" },
  { id: "replaying", label: "干净环境复跑" },
  { id: "succeeded", label: "运行结束，查看结论" },
];

/** 失败类别 → 它发生在哪个阶段（用来把断掉的那一格标红）。 */
function stageOfFailure(category: string | null | undefined): RunState | null {
  if (!category) return null;
  const stem = labelOf(category);
  switch (stem) {
    case "clone_failed":
      return "preparing";
    case "install_failed":
    case "requirement_rejected":
      return "installing";
    case "smoke_test_failed":
    case "requires_gpu":
    case "missing_private_data":
    case "cuda_only":
    case "hardcoded_path":
    case "version_conflict":
    case "deprecated_api":
    case "missing_dependency":
    case "missing_module":
    case "missing_attribute":
    case "import_error":
    case "syntax_error":
    case "runtime_error":
    case "assertion_failed":
    case "artifact_missing":
      return "testing";
    case "llm_unavailable":
      return "planning";
    default:
      return null;
  }
}

export function buildStages(events: RunEvent[], state: RunState | undefined): StageView[] {
  const current = state ?? "queued";
  const terminal = TERMINAL.has(current);

  const byState = new Map<string, RunEvent[]>();
  for (const e of events) {
    const list = byState.get(e.state);
    if (list) list.push(e);
    else byState.set(e.state, [e]);
  }

  // 进入次数 = 状态切换次数（不是事件条数）：跑两遍测试就是 ×2。
  const entries = new Map<string, number>();
  let prev: string | null = null;
  for (const e of events) {
    if (e.state !== prev) entries.set(e.state, (entries.get(e.state) ?? 0) + 1);
    prev = e.state;
  }

  // 失败落在哪一格：先按 failure_category 映射，映射不出来就退到最后有事件的那格。
  const lastWithEvents = [...events].reverse().find((event) => !TERMINAL.has(event.state));
  const failedCommand = [...events].reverse().find((event) =>
    (event.kind === "test" || event.kind === "install") &&
    (event.data.timed_out === true || (typeof event.data.exit_code === "number" && event.data.exit_code !== 0)),
  );
  const failureStage = current === "failed"
    ? (lastWithEvents?.kind === "diagnosis"
      ? failedCommand?.state ?? "testing"
      : lastWithEvents?.state ?? stageOfFailure((events.at(-1)?.data?.failure_category as string | undefined) ?? null))
    : null;

  const starts = STAGE_ORDER.map((s) => {
    const list = byState.get(s.id) ?? [];
    return list.length ? Date.parse(list[0].timestamp) : null;
  });

  const currentIdx = Math.max(
    0,
    STAGE_ORDER.findIndex((s) => s.id === (failureStage ?? current)),
  );

  return STAGE_ORDER.map((s, i) => {
    const list = byState.get(s.id) ?? [];
    const startedAt = starts[i];
    const nextStart = starts.slice(i + 1).find((t) => t !== null) ?? null;
    const end = nextStart ?? (startedAt !== null ? (terminal ? Date.parse(events.at(-1)!.timestamp) : Date.now()) : null);
    const durationMs = startedAt !== null && end !== null ? Math.max(0, end - startedAt) : null;

    let status: StageView["status"];
    if (s.id === current && !terminal) {
      status = "active";
    } else if (s.id === "succeeded") {
      status = current === "succeeded" ? "done" : "todo";
    } else if (failureStage === s.id) {
      status = "bad";
    } else if (list.length > 0) {
      const last = list.at(-1)!;
      const skipped = last.kind === "install" && last.message.includes("no requirements.txt") && last.message.includes("skipped");
      const code = last.data.exit_code;
      const verificationFailed = s.id === "verifying" && (last.data.status === "failed" || last.data.status === "blocked");
      const interrupted = current === "cancelled" && s.id === lastWithEvents?.state;
      status = skipped || interrupted ? "skipped" : (verificationFailed || last.data.ok === false || last.data.timed_out === true || (typeof code === "number" && code !== 0)) ? "bad" : "done";
    } else if (s.id === "queued" && current !== "queued" && i < currentIdx) {
      // queued 是状态机里唯一不写事件的阶段：只有它允许"没事件也算走过"。
      status = "done";
    } else {
      status = "todo";
    }

    return {
      id: s.id,
      label: s.label,
      status,
      count: entries.get(s.id) ?? 0,
      startedAt,
      durationMs,
    };
  });
}

// ───────────────────────────────────────────────────────── 进度估计

/**
 * 整体进度 + 倒计时。
 *
 * 两段拼起来，谁也不冒充谁：
 *   1. **已完成部分**由事件推导（阶段状态 + 该阶段实际耗时），这一段是事实；
 *   2. **剩余部分**是本文件里的经验耗时（STAGE_EST），这一段是估算。
 *
 * 界面上必须把两者分开写（「已运行 00:41 / 预估 01:19 / 剩余 00:38」），
 * 因为契约里没有 ETA 字段，硬编一个漂亮的倒计时等于编数据。
 */
export interface ProgressView {
  /** 0–100 整数 */
  pct: number;
  /** 从首条事件算起的墙钟；终态用最后一条事件的时间 */
  elapsedMs: number;
  /** 预估剩余；终态为 0，没有任何事件时为 null */
  etaMs: number | null;
  /** 已运行 + 预估剩余 */
  totalMs: number | null;
  /** 正处在自愈回环里（进度条会标「重跑中」） */
  looping: boolean;
  /** 有没有真实事件可依据 */
  basis: "events" | "guess";
}

/** 各阶段经验耗时（ms）。只喂给「预估剩余」，不是契约字段。 */
const STAGE_EST: Partial<Record<RunState, number>> = {
  preparing: 32_000, // git clone，实测 14–40s
  planning: 8_000,
  installing: 45_000, // pip install，实测 10–90s
  testing: 22_000,
  diagnosing: 6_000,
  repairing: 45_000,
  verifying: 8_000,
  replaying: 60_000, // 新容器里重新安装 + 运行 + 同规则验收
};

/** 各阶段在总进度里的权重（合计 96，余下 4 是收尾）。 */
const STAGE_WEIGHT: Partial<Record<RunState, number>> = {
  queued: 2,
  preparing: 26,
  planning: 6,
  installing: 34,
  testing: 22,
  verifying: 5,
  replaying: 5,
};

/** 进度条上的阶段分界刻度（0–1），与上面权重逐项对应。 */
export const PROGRESS_TICKS = [0.02, 0.28, 0.34, 0.68, 0.90];

export function buildProgress(
  events: RunEvent[],
  stages: StageView[],
  state: RunState | undefined,
): ProgressView {
  const current = state ?? "queued";
  const terminal = TERMINAL.has(current);
  const startAt = events.length ? Date.parse(events[0].timestamp) : null;
  const endAt = terminal && events.length ? Date.parse(events.at(-1)!.timestamp) : Date.now();
  const elapsedMs = startAt !== null ? Math.max(0, endAt - startAt) : 0;

  let pct = 0;
  let remaining = 0;

  for (const s of stages) {
    const w = STAGE_WEIGHT[s.id as RunState] ?? 0;
    const est = STAGE_EST[s.id as RunState] ?? 0;

    if (s.status === "done") {
      pct += w;
    } else if (s.status === "bad") {
      // 这一格开始了但没做完，只记一半，别把失败画成完成
      pct += w * 0.5;
    } else if (s.status === "active") {
      const ran = s.durationMs ?? 0;
      // 没有事件时也从 8% 起步，让条子"动起来"而不是贴着 0
      const ratio = est > 0 ? Math.min(0.92, Math.max(0.08, ran / est)) : 0.12;
      pct += w * ratio;
      if (est > 0) remaining += Math.max(0, est - ran);
    } else if (!terminal && current !== "verifying") {
      remaining += est;
    }
  }

  // 成功才算 100；失败/取消停在已经走到的位置，不回流也不补满
  pct = current === "succeeded" ? 100 : Math.min(96, pct);

  // 还没有任何事件时也给一个数：全按经验耗时估，总比 "--:--" 有用。
  // 第一条事件到齐后立刻被真实耗时替代。
  const etaMs = terminal ? 0 : remaining > 0 ? remaining : null;
  const totalMs = etaMs === null ? null : elapsedMs + etaMs;

  return {
    pct: Math.round(pct),
    elapsedMs,
    etaMs,
    totalMs,
    looping: current === "diagnosing" || current === "repairing",
    basis: events.length ? "events" : "guess",
  };
}

// ───────────────────────────────────────────────────── 自愈回环

export interface LoopAction {
  seq: number;
  name: string;
  summary: string;
  ok: boolean;
  blocked: boolean;
  timestamp: string;
}

/**
 * 本轮为什么提前结束（后端 `loop_stopped` 事件）。
 *
 * 后端熔断只有两种触发：模型连续提出同一个动作，或动作连续 3 次被 ACI 拦下
 * （见 runner.py 的两处 `loop_stopped`）。没有这条事件就是正常跑完一轮。
 */
export interface LoopStop {
  reason: "repeated" | "rejected";
  detail: string;
}

export interface LoopRound {
  round: number;
  failureClass: string;
  phase: string;
  startedAt: string;
  finishedAt: string | null;
  actions: LoopAction[];
  stopped: LoopStop | null;
}

export interface LoopView {
  rounds: LoopRound[];
  /** 契约 §2 默认 max_repairs=3。 */
  budget: number;
  /** 当前是否处在回环里（诊断 / 修复 / 修复后的重跑）。 */
  active: boolean;
  /** 一句话：现在正在干什么。 */
  doing: string;
  /** 正在进行的动作名（给高亮用）。 */
  doingKind: string | null;
}

/**
 * `loop_stopped` → 熔断原因。
 *
 * 只读后端显式给的 data 字段：重复动作带 `action`，连续被拒带 `count`。
 * 两个都没有就按"被拦下"记，不猜它为什么停。
 */
function parseLoopStop(e: RunEvent): LoopStop {
  const action = typeof e.data?.action === "string" ? e.data.action : null;
  if (action) {
    return { reason: "repeated", detail: `连续提出同一个动作 ${action}` };
  }
  const count = typeof e.data?.count === "number" ? e.data.count : null;
  return {
    reason: "rejected",
    detail: count === null ? "动作连续被 ACI 拦下" : `动作连续 ${count} 次被 ACI 拦下`,
  };
}

/** 修复后回到的阶段顺序：install → test，所以 installing/testing 也可能属于回环。 */
export function buildLoop(events: RunEvent[], state: RunState | undefined): LoopView {
  const rounds: LoopRound[] = [];
  for (const e of events) {
    if (e.kind === "diagnosis") {
      rounds.push({
        round: Number(e.data?.attempt ?? rounds.length + 1),
        failureClass: String(e.data?.failure_class ?? "unknown"),
        phase: String(e.data?.phase ?? "test"),
        startedAt: e.timestamp,
        finishedAt: null,
        actions: [],
        stopped: null,
      });
      continue;
    }
    if (e.kind === "loop_stopped" && rounds.length) {
      rounds.at(-1)!.stopped = parseLoopStop(e);
      continue;
    }
    if (e.kind === "repair_round" && rounds.length) {
      rounds.at(-1)!.finishedAt = e.timestamp;
      continue;
    }
    if (isActionEvent(e) && rounds.length) {
      const blocked = isBlockedEvent(e);
      rounds.at(-1)!.actions.push({
        seq: e.sequence,
        name: e.kind,
        summary: e.message,
        ok: e.data?.ok === true && !blocked,
        blocked,
        timestamp: e.timestamp,
      });
    }
  }

  const current = state ?? "queued";
  // 核验是独立阶段，不是又一轮修复；即使缺少 repair_round / final 事件也不保留“进行中”。
  if (current === "verifying" || current === "replaying" || TERMINAL.has(current)) {
    const boundary = events.find((event) => event.state === "verifying" || event.state === "replaying" || TERMINAL.has(event.state))?.timestamp
      ?? events.at(-1)?.timestamp;
    if (boundary) for (const round of rounds) round.finishedAt ??= boundary;
  }
  const last = rounds.at(-1) ?? null;
  const inLoop = current === "diagnosing" || current === "repairing";

  let doing = "";
  let doingKind: string | null = null;
  if (inLoop) {
    if (current === "diagnosing") {
      doing = "正在判定失败原因（规则表 → 分类器）";
      doingKind = "diagnosis";
    } else {
      const lastAction = last?.actions.at(-1);
      doing = lastAction
        ? `${lastAction.name} → ${lastAction.summary}`
        : "等待模型提出修复动作";
      doingKind = lastAction?.name ?? null;
    }
  } else if (last && !last.finishedAt && current === "installing") {
    doing = `第 ${last.round} 轮修复后重新安装依赖`;
    doingKind = "install_requirement";
  } else if (last && !last.finishedAt && current === "testing") {
    doing = `第 ${last.round} 轮修复后重跑验证`;
    doingKind = "run_smoke_test";
  }

  return { rounds, budget: 3, active: inLoop || doing !== "", doing, doingKind };
}

// ───────────────────────────────────────────────────── 动作流

export interface ActionRow {
  seq: number;
  ts: string;
  kind: "call" | "reject" | "finish" | "ok";
  name: string;
  target: string;
  result: string;
  blocked?: boolean;
  reason?: string;
}

/** 事件 → 动作行。动作流里同时出现 ACI 动作与 install/test 的执行结果。 */
export function buildRows(events: RunEvent[]): ActionRow[] {
  const rows: ActionRow[] = [];
  for (const e of events) {
    const isExec = e.kind === "install" || e.kind === "test";
    if (!isActionEvent(e) && !isExec) continue;
    const blocked = isBlockedEvent(e);
    const code = typeof e.data?.exit_code === "number" ? (e.data.exit_code as number) : null;
    const ok = e.data?.ok === true || (code !== null && code === 0);
    rows.push({
      seq: e.sequence,
      ts: new Date(e.timestamp).toLocaleTimeString("zh-CN", { hour12: false }),
      kind: blocked
        ? "reject"
        : e.kind === "finish_repair"
          ? "finish"
          : ok && !isExec
            ? "ok"
            : "call",
      name: e.kind,
      target: isExec ? "plan.command" : targetOf(e),
      // install 事件没带 exit_code（依赖为空时后端直接 skipped），
      // 硬写 `exit ?` 等于把"没有这个字段"当成"退出码未知"，
      // 这时退回事件原文更有信息量。
      result: isExec
        ? code !== null
          ? `exit ${code}`
          : e.message.slice(0, 40)
        : blocked
          ? labelOfEvent(e)
          : e.message.slice(0, 52),
      blocked,
      reason: blocked ? e.message : undefined,
    });
  }
  return rows.slice(-80);
}

// ─────────────────────────────────────────────────── ACI 校验轨迹

export interface AciStep {
  key: string;
  title: string;
  detail: string;
  status: "pass" | "fail" | "skip";
}

export interface AciTrace {
  action: string;
  target: string;
  /** 拦截时命中的标签（file_not_found / path_escape …），放行时为 null。 */
  label: string | null;
  steps: AciStep[];
  verdict: "pass" | "block";
  summary: string;
  exitCode: number | null;
  at: string;
  seq: number;
}

/** 每个动作在"专属校验"这一步上真正检查的东西（照后端实现写）。 */
function guardDetail(kind: string): string {
  switch (kind) {
    case "read_file":
      return "start_line ≥ 1 · line_count ∈ [1,200]";
    case "search_text":
      return "max_results ≤ 50 · 单文件扫描 ≤ 2 MiB";
    case "replace_exact":
      return "old 必须在文件里唯一命中（空 old 直接拒）";
    case "install_requirement":
      return "依赖串逐行白名单：禁 ; | & $ ` > < 与空白拼接";
    case "set_environment":
      return "变量名 ∈ {CUDA_VISIBLE_DEVICES, OMP_NUM_THREADS, MKL_NUM_THREADS, TOKENIZERS_PARALLELISM}";
    case "run_smoke_test":
      return "命令来自 run_plan，模型不可替换";
    case "finish_repair":
      return "只记录本轮结论，无副作用";
    default:
      return "无专属校验";
  }
}

const STEP_OF_LABEL: Record<string, string> = {
  unknown_action: "dispatch",
  path_escape: "path",
  file_not_found: "guard",
  replace_not_found: "guard",
  replace_not_unique: "guard",
  requirement_not_allowed: "guard",
  env_not_allowed: "guard",
  invalid_argument: "guard",
};

export function aciTrace(e: RunEvent): AciTrace {
  const stem = labelOfEvent(e);
  const label = BLOCK_LABELS.has(stem) ? stem : null;
  const failAt = label ? (STEP_OF_LABEL[label] ?? "guard") : null;
  const target = targetOf(e);
  const exitCode = typeof e.data?.exit_code === "number" ? (e.data.exit_code as number) : null;

  const step = (key: string, title: string, detail: string): AciStep => {
    let status: AciStep["status"] = "pass";
    if (failAt === key) status = "fail";
    // 校验失败时不产生副作用，第 4、5 步标记为未执行。
    else if (failAt === "dispatch" || failAt === "path" || failAt === "guard") status = "skip";
    return { key, title, detail, status };
  };

  return {
    action: e.kind,
    target,
    label,
    verdict: label ? "block" : "pass",
    summary: e.message,
    exitCode,
    at: e.timestamp,
    seq: e.sequence,
    steps: [
      step("dispatch", "动作分派", `action ∈ 7 种收口动作 → ${e.kind}`),
      step(
        "path",
        "路径守卫",
        target ? `resolve(${target}) 必须落在 workspace 内，否则回显原始字符串` : "本动作不涉及路径",
      ),
      step("guard", "专属校验", guardDetail(e.kind)),
      step(
        "effect",
        "执行副作用",
        e.kind === "install_requirement"
          ? "临时开网 pip install → 结束立刻恢复断网（ACI 自持网络状态）"
          : e.kind === "run_smoke_test"
            ? "断网执行 plan.command"
            : "只读写 workspace，不碰宿主机其他目录",
      ),
      step(
        "observe",
        "Observation",
        `回到模型：${e.message}${exitCode !== null ? ` · exit ${exitCode}` : ""}`,
      ),
    ],
  };
}

// ─────────────────────────────────────────────── 失败原因：先说证据，再说结论

/**
 * 失败原因的解释。
 *
 * 后端 `_fail` 只为失败留了两样东西：`failure_category` 和一条 message。
 * 原文从哪来，三处不同：
 *   - `clone_failed`：PR #15 起 `git_clone` 把 `CalledProcessError` 的 stderr
 *     脱敏后塞进 RuntimeError，`_fail` 再拼成 `clone failed: git exited with
 *     <code>: <stderr>`——所以**现在有 git 原文**，只是藏在 message 里。
 *   - `install` / `test`：事件自带 `stderr_tail` 字段。
 *   - 其余（规划、预算耗尽、不可修复）：只有类别，没有原文。
 *
 * 所以前端能核实的是：失败类别、git 的 stderr、stderr_tail、命令行里的地址、
 * 退出码。这里不给"成因"下断言：把能核实的写进 verdict，把无法区分的情况
 * 并列写进 detail，并给出用户自己能跑的验证命令。
 *
 * 反例（2026-11-17 修的）：旧代码对任何 clone_failed 都写"pack 数据在传输中
 * 被截断，不是连不上，是没下完"。但真实失败是地址填成了论文页面（USENIX
 * presentation 页），git clone 立刻 exit 128——和"没下完"完全无关。
 *
 * 第二个反例（本次修的）：旧代码还写"后端没有保存 git 的 stderr（runs/<id>/logs/
 * 是空目录）"。PR #15 合进来之后这句话已经不成立——stderr 就在 message 里，
 * 界面一边展示原文一边说"没有原文"，比不展示更糟。
 */
export interface FailureExplain {
  /** 能核实的第一句，不含推断 */
  verdict: string;
  /** 为什么只能说到这里 + 怎么自己验 */
  detail: string;
  /** 旁证（地址形态等），措辞上不当作结论 */
  note?: string;
  /** 从命令行里抓出的仓库地址 */
  repoUrl?: string;
  /** 后端原始 message，折叠展示 */
  raw?: string;
  /** 失败动作的 stderr 尾段（install / test 事件里带 stderr_tail） */
  stderr?: string;
  /** 这段 stderr 出自哪条事件、什么动作 */
  stderrFrom?: string;
}

/**
 * 已知失败类别 → 一句话结论。类别名与后端逐字相同，不做映射。
 *
 * 键必须覆盖后端所有会写进 `failure_category` 的值：`_fail` 的四个直接类别
 * （clone_failed / missing_run_command / requirement_rejected / time_budget_exceeded）
 * 加上 classifier 的八类靶场分类里的七个（`none` 不会作为失败出现）。
 * 漏一个，界面就只能说"没有对应的解释文本"——那等于把后端已经给出的结论吞掉。
 *
 * 写这些句子时守同一条规矩：**只陈述后端凭什么这么判**（命中哪条规则、
 * 哪一步退出码非零），不替它分析成因。
 */
const FAILURE_VERDICTS: Record<string, string> = {
  clone_failed: "克隆失败：git clone 直接返回非零退出码，没有进入安装与测试阶段。",
  missing_run_command:
    "没能确定运行命令：仓库里没有可识别的入口，规划阶段拿到的命令是空的，因此没有执行任何东西。",
  smoke_test_failed: "测试命令跑完了，但退出码非零，产物校验没过。",
  install_failed: "依赖没装上：requirements 里的包在沙箱内装不下来，或不在允许的依赖清单里。",
  requirement_rejected: "依赖字符串被校验层拦下（含命令分隔符等），这一步没有产生副作用。",
  missing_dependency:
    "缺依赖导致 import 失败：stderr 命中 ModuleNotFoundError / ImportError（自愈回环开着时会先尝试补齐）。",
  version_conflict:
    "依赖版本冲突：stderr 命中版本解析失败（VersionConflict / incompatible version），pip 在解依赖时就退出了。",
  hardcoded_path:
    "代码里写死了宿主机路径：stderr 里出现 /home/、/Users/、/mnt/、/root/ 这类目录，沙箱里没有它们。",
  cuda_only: "代码要求 CUDA：stderr 提到 CUDA，而沙箱是无 GPU 的 CPU 容器。",
  deprecated_api:
    "用了已移除或签名变了的旧 API：stderr 命中 deprecated / unexpected keyword argument，导入或调用时直接报错。",
  missing_private_data:
    "代码要读的数据集不在仓库里：stderr 是 FileNotFoundError 且指向 dataset / corpus / private 数据。",
  requires_gpu: "运行前提是要 GPU：stderr 命中 requires GPU / nvidia-smi / CUDA 未编译，沙箱没有 GPU。",
  llm_unavailable: "生成运行计划时模型不可用（网关或配额），没进入执行阶段。",
  service_interrupted: "沙箱不可用或后端服务中断，本次运行被打断，可重试。",
  time_budget_exceeded: "整体墙钟预算耗尽被强制结束——通常是某一步一直没有返回。",
};

const FAILURE_DETAILS: Record<string, string> = {
  smoke_test_failed:
    "若当前是 fake 沙箱，通常是它并不真正产出 expected_artifact，属于沙箱行为差异，不是代码问题。",
  install_failed: "先看 pip 那一步的 stderr 尾段（动作流里点开该行），再看包名是否在允许清单内。",
};

/** 常见代码托管站点：只有这些 host 才敢按"仓库地址"来判断路径形态。 */
const FORGE_HOSTS = new Set([
  "github.com",
  "gitee.com",
  "gitlab.com",
  "bitbucket.org",
  "codeberg.org",
  "gitcode.com",
  "gitea.com",
  "git.sr.ht",
]);

/** 从"最后一条带 message 的失败事件"里取原始文本。 */
function rawFailureMessage(events: RunEvent[], needle: string): string | undefined {
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const m = events[i].data?.message;
    if (typeof m === "string" && m.includes(needle)) return m;
  }
  return undefined;
}

/** 命令行里的第一个 http(s) 地址；去掉尾部的引号与括号。 */
function firstUrl(text: string): string | undefined {
  const m = text.match(/https?:\/\/[^\s'",\])}]+/);
  return m ? m[0].replace(/[.,;:]+$/, "") : undefined;
}

/**
 * 从 clone 失败事件里拆出 `git exited with <code>: <stderr>`。
 *
 * PR #15 起 `git_clone` 才会这么拼；更早的 run 只有 `exit status N`，
 * 那时返回 null，由调用方退回"没有原文"的说法。
 */
function parseGitExit(raw: string | undefined): { code: number; stderr: string } | null {
  if (!raw) return null;
  const m = /git exited with (-?\d+):\s*([\s\S]+)$/.exec(raw);
  if (!m) return null;
  const text = m[2].trim();
  return text ? { code: Number(m[1]), stderr: text } : null;
}

/** 只做形态判断，返回 null 表示"看不出来"，不硬给结论。 */
function urlShape(url: string): "forge-repo" | "not-forge" | null {
  const m = /^https?:\/\/([^/]+)\/(.*)$/.exec(url);
  if (!m) return null;
  const host = m[1].toLowerCase().replace(/:\d+$/, "");
  const path = m[2].replace(/\/+$/, "");
  if (path.endsWith(".git")) return "forge-repo";
  if (FORGE_HOSTS.has(host)) return path.split("/").filter(Boolean).length >= 2 ? "forge-repo" : "not-forge";
  return null;
}

/**
 * 最后一条"非零退出 + 带 stderr_tail"的事件。
 *
 * install / test 事件里后端是带 `stderr_tail` 的（见 runner.py 的
 * `{"exit_code": ..., "stderr_tail": ...}`），所以这两类失败**有原始报错可看**，
 * 不该再说"后端没带 stderr"。clone 失败没有这条，因为克隆不是 action。
 */
function lastStderr(events: RunEvent[]): { text: string; from: string } | undefined {
  const pick = (onlyFailed: boolean) => {
    for (let i = events.length - 1; i >= 0; i -= 1) {
      const d = events[i].data ?? {};
      const text = d.stderr_tail;
      const code = typeof d.exit_code === "number" ? d.exit_code : null;
      if (onlyFailed && code === 0) continue;
      if (typeof text === "string" && text.trim()) {
        return {
          text: text.trim(),
          from: `#${events[i].sequence} ${events[i].kind}${code === null ? "" : ` · exit ${code}`}`,
        };
      }
    }
    return undefined;
  };
  return pick(true) ?? pick(false);
}

export function explainFailure(category: string, events: RunEvent[]): FailureExplain {
  if (!category) {
    return {
      verdict: "后端没有给出 failure_category，无法判断失败环节。",
      detail: "只有 state=failed 这一个事实；具体是哪一步没过，要看后端日志。",
    };
  }

  if (category === "clone_failed") {
    const raw = rawFailureMessage(events, "clone failed");
    const git = parseGitExit(raw);
    const repoUrl = raw ? firstUrl(raw) : undefined;
    const shape = repoUrl ? urlShape(repoUrl) : null;

    const note =
      shape === "forge-repo"
        ? undefined
        : "旁证：该地址没有 .git 后缀，主机也不在常见托管站点（GitHub / Gitee / GitLab 等）名单内。" +
          "如果它其实是论文页面或项目主页而不是仓库地址，git clone 必然失败——" +
          "请换成仓库地址（例：https://github.com/<owner>/<repo>）。";

    // 有 git 原文就摆原文：这时界面唯一该做的是说明这段字是怎么来的。
    if (git) {
      return {
        verdict: FAILURE_VERDICTS.clone_failed,
        detail:
          `git 自己的报错就在下面（退出码 ${git.code}）：后端把它的 stderr 拼进了这条事件，` +
          "宿主机路径已替换成 /workspace。判断依据是这段原文，界面不做二次解读。",
        note,
        repoUrl,
        stderr: git.stderr,
        stderrFrom: `clone_failed · exit ${git.code}`,
        raw,
      };
    }

    return {
      verdict: FAILURE_VERDICTS.clone_failed,
      detail:
        "这条事件的 message 里只有退出码，没有 git 的原始 stderr，" +
        "所以「地址不是仓库」「网络中断」「需要凭据」三种情况在前端无法区分。" +
        "用同一个地址手动验一下最快：git ls-remote <地址> —— " +
        "立刻报 repository not found 是地址问题，卡住或中途断开才是网络问题。",
      note,
      repoUrl,
      raw,
    };
  }

  const last = lastStderr(events);
  if (last) {
    return {
      verdict: FAILURE_VERDICTS[category] ?? "后端回了失败类别，但没有对应的解释文本。",
      detail:
        "下面是这次运行里最后一条非零退出动作的 stderr 尾段（后端在 install / test 事件里带 stderr_tail），" +
        "判断依据就在这段原文里，界面不做二次解读。",
      stderr: last.text,
      stderrFrom: last.from,
      raw: rawFailureMessage(events, category) ?? rawFailureMessage(events, "failed"),
    };
  }

  return {
    verdict: FAILURE_VERDICTS[category] ?? "后端回了失败类别，但没有对应的解释文本。",
    detail:
      FAILURE_DETAILS[category] ??
      "这次失败的事件里没有带 stderr_tail（只有 install / test 动作会带），后端也只记了失败类别，" +
        "所以具体报错要去看后端日志；前端不替它猜原因。",
    raw: rawFailureMessage(events, category) ?? rawFailureMessage(events, "failed"),
  };
}
