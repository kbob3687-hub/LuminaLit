/**
 * 演示 / 调试 / 截图直链的统一解析。
 *
 * 这些参数（`?scene=` `?panel=` `?t=` `?seq=` `?run=`）在注释里被明确
 * 定位成「调试、答辩、截图用」，是演示时的常用入口。但旧代码用
 * `as Scene` 类型断言替代运行时校验，于是：
 *
 *   ?scene=foo        → 三个渲染分支全不成立，整页白屏
 *   ?scene=run（无 run）→ live 模式下 useRunPolling(null) 永不请求，永久空壳
 *   ?t=abc            → seekMs=NaN，回放铺满预置事件后不再播放
 *   ?panel=aci&seq=abc→ events.find(NaN) 恒 undefined，打开一个空白抽屉
 *
 * 这里把解析收在一处：字符串走白名单，数字走 Number.isFinite，
 * 校验不过就退回安全值，绝不让 query 直接决定 React 状态的类型。
 */

export type Scene = "select" | "welcome" | "run";

const SCENES: readonly string[] = ["select", "welcome", "run"];
const PANELS: readonly string[] = ["aci", "loop", "plan", "report", "stage"];

/** 非负有限数，非法值一律给 fallback。 */
export function finiteNonNegative(raw: string | null, fallback = 0): number {
  const n = Number(raw);
  return Number.isFinite(n) && n >= 0 ? n : fallback;
}

/** scene 白名单；`?run=<id>` 是「直接盯一个 run」的简写，等价于 scene=run。 */
export function parseScene(q: URLSearchParams, hasRunId: boolean): Scene {
  const raw = q.get("scene");
  if (raw && SCENES.includes(raw)) return raw as Scene;
  return hasRunId ? "run" : "select";
}

/** panel 白名单；未知值返回 null（不打开抽屉，而不是打开一个空抽屉）。 */
export function parsePanel(q: URLSearchParams): string | null {
  const raw = q.get("panel");
  return raw && PANELS.includes(raw) ? raw : null;
}
