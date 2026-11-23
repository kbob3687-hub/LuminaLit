/**
 * 深链白名单（评审稿 §11.2）：URL 参数是不可信输入，必须逐项校验后回退。
 *
 * 之前 App / RunScene / ReportPanel 各自 `new URLSearchParams(...).get(...)`，
 * 出现了 `?scene=preflight` 可以伪造确认页、`?run=javascript:...` 会拼进请求
 * 路径、`?seq=abc` 变 NaN 打开空详情等问题。这里统一收口：
 *   - scene 只接受四个固定值；preflight 绝不接受深链（必须持有服务端确认单）。
 *   - run_id 只接受 32 位小写十六进制，非法值一律当作没有。
 *   - panel / tab / seq 都走白名单与整数校验，非法值回退到默认视图。
 */

export type Scene = "select" | "preflight" | "welcome" | "run" | "result";

const RUN_ID_RE = /^[0-9a-f]{32}$/;
const SCENES: ReadonlySet<string> = new Set(["select", "preflight", "welcome", "run", "result"]);
const PANELS: ReadonlySet<string> = new Set(["event", "stage", "technical", "report"]);
const TABS: ReadonlySet<string> = new Set(["report", "patch", "json"]);

export function isRunId(value: string | null | undefined): value is string {
  return typeof value === "string" && RUN_ID_RE.test(value);
}

/** 非法 run 参数返回 null，调用方据此回退，不把原字符串带进 API 路径。 */
export function parseRunId(q: URLSearchParams): string | null {
  const raw = q.get("run");
  return isRunId(raw) ? raw : null;
}

export function parseScene(q: URLSearchParams, hasRealPreflight = false): Scene {
  const requested = q.get("scene");
  if (requested && !SCENES.has(requested)) return "select";
  // 确认页必须来自真实的 POST /api/preflights 结果，深链不得凭空进入。
  // 确认页与结果页都必须持有真实数据：前者来自服务端确认单，后者来自 run。
  if (requested === "preflight") return hasRealPreflight ? "preflight" : "select";
  if (requested === "result") return parseRunId(q) ? "result" : "select";
  if (parseRunId(q)) return "run";
  if (requested === "run") {
    // 只有显式演示/离线回放才允许「没有 run 的运行页」。
    return q.get("demo") === "1" || q.get("live") === "1" ? "run" : "select";
  }
  return (requested as Scene | null) ?? "select";
}

export type PanelId = "event" | "stage" | "technical" | "report";

export function parsePanel(q: URLSearchParams): { type: PanelId; seq: number } | null {
  const raw = q.get("panel");
  // 旧直链里的 aci / loop / plan 曾指向不同抽屉，现在统一归入技术详情/单步详情。
  const mapped = raw === "aci" ? "event" : raw === "loop" || raw === "plan" ? "technical" : raw;
  if (!mapped || !PANELS.has(mapped)) return raw === "report" ? { type: "report", seq: 0 } : null;
  const seq = Number(q.get("seq") ?? 0);
  return { type: mapped as PanelId, seq: Number.isSafeInteger(seq) && seq > 0 ? seq : 0 };
}

export type TabId = "report" | "patch" | "json";

export function parseTab(q: URLSearchParams): TabId {
  const raw = q.get("tab");
  return raw && TABS.has(raw) ? (raw as TabId) : "report";
}

/** 回放时间轴参数：只允许有限非负整数，NaN/Infinity/负数全部回退 0。 */
export function finiteNonNegative(value: string | null): number {
  if (value === null) return 0;
  const n = Number(value);
  return Number.isFinite(n) && n >= 0 && Number.isSafeInteger(n) ? n : 0;
}
