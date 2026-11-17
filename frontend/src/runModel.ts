/**
 * 从后端事件流推导界面状态（阶段条 / 自愈回环 / ACI 校验轨迹）。
 *
 * 原则：**界面上的"已完成"只能由后端事件证明**，不能由前端按索引猜测。
 * 之前 `i < stageIndex(state)` 的写法会在 run 一开始就把前面所有阶段
 * 点亮成绿色（demo 里更糟：mock 状态直接是 repairing，进场即 6 个绿）。
 * 这里改成：某阶段只有真的收到过该阶段的事件、且已经离开它，才算完成。
 */

import { labelOf, TERMINAL, type RunEvent, type RunState } from "./types";

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
  status: "todo" | "active" | "done" | "bad";
  /** 进入该阶段的次数：自愈回环里 installing / testing 会 > 1。 */
  count: number;
  /** 首次进入的绝对时间（ms），没有事件时为 null。 */
  startedAt: number | null;
  /** 该阶段耗时（到下一阶段开始为止），进行中为到现在。 */
  durationMs: number | null;
}

export const STAGE_ORDER: { id: RunState | "succeeded"; label: string }[] = [
  { id: "queued", label: "排队" },
  { id: "preparing", label: "准备环境" },
  { id: "planning", label: "生成计划" },
  { id: "installing", label: "安装依赖" },
  { id: "testing", label: "运行测试" },
  { id: "diagnosing", label: "诊断失败" },
  { id: "repairing", label: "修复中" },
  { id: "succeeded", label: "成功" },
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
  const lastWithEvents = [...STAGE_ORDER]
    .reverse()
    .find((s) => byState.has(s.id) && s.id !== "succeeded")?.id as RunState | undefined;
  const failureStage =
    current === "failed"
      ? (stageOfFailure((events.at(-1)?.data?.failure_category as string | undefined) ?? null) ??
        lastWithEvents ??
        null)
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
    const end = nextStart ?? (startedAt !== null && !terminal ? Date.now() : null);
    const durationMs = startedAt !== null && end !== null ? Math.max(0, end - startedAt) : null;

    let status: StageView["status"];
    if (s.id === current && !terminal) {
      status = "active";
    } else if (s.id === "succeeded") {
      status = current === "succeeded" ? "active" : "todo";
    } else if (failureStage === s.id) {
      status = "bad";
    } else if (list.length > 0) {
      // 离开该阶段后才看最后一次执行结果：exit 0 才算做完，非 0 是坏的。
      const code = list.at(-1)?.data?.exit_code;
      status = typeof code === "number" && code !== 0 ? "bad" : "done";
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

// ───────────────────────────────────────────────────── 自愈回环

export interface LoopAction {
  seq: number;
  name: string;
  summary: string;
  ok: boolean;
  blocked: boolean;
  timestamp: string;
}

export interface LoopRound {
  round: number;
  failureClass: string;
  phase: string;
  startedAt: string;
  finishedAt: string | null;
  actions: LoopAction[];
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
      });
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
      result: isExec ? `exit ${code ?? "?"}` : blocked ? labelOfEvent(e) : e.message.slice(0, 52),
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
