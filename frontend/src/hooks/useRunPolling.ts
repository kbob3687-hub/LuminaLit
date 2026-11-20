import { useCallback, useEffect, useRef, useState } from "react";
import { getEvents, getRun } from "../api";
import { TERMINAL, type RunEvent, type RunState, type RunStatus } from "../types";

const POLL_MS = 1000; // 契约 §4.2
/** 单次请求失败后的重试间隔：2s → 4s → 8s 封顶，成功一次即归零。 */
const RETRY_MAX_MS = 8000;

interface Options {
  /** 演示模式：不请求后端，用给定的假数据驱动。 */
  mock?: {
    status: RunStatus;
    events: RunEvent[];
  };
}

/**
 * 轮询 run 状态与事件。进入终态后停止轮询（契约 §4.2）。
 * 事件按 `after=<seq>` 增量拉取，不重复渲染。
 *
 * 所有循环状态（cursor / timer / 取消标志）都放在 effect 内部，**不能用
 * ref 共享**：StrictMode 下 effect 会 mount → cleanup → mount，第一轮的
 * fetch 已经发出去了，如果 cursor 和取消标志是共享 ref，第二轮会把它重置，
 * 于是两轮请求同时返回、同一批事件被追加两次——表现为事件数翻倍、
 * 动作行重复（实测 5 条事件渲染成 10 条）。
 */
export function useRunPolling(runId: string | null, opts: Options = {}) {
  const [status, setStatus] = useState<RunStatus | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  /** 外部强制停止（取消任务后立刻停轮询用）。 */
  const forced = useRef(false);

  useEffect(() => {
    // 演示模式
    if (opts.mock) {
      setStatus(opts.mock.status);
      setEvents(opts.mock.events);
      return;
    }
    if (!runId) return;

    let cancelled = false;
    let timer: number | null = null;
    let cursor = 0;
    let retryMs = 0;

    forced.current = false;
    setEvents([]);
    setStatus(null); // 换 run 必须清空旧状态，否则新 run 一进场就显示上一轮的绿
    setError(null);

    /**
     * 两个请求必须分开看结果：真沙箱一次 run 要几十秒，期间后端一次 5xx、
     * 代理抖动或网络瞬断都很常见。旧写法用 Promise.all + catch 里直接
     * `return`，于是 `getRun` 明明成功也拿不到 state，而且**不再排下一次
     * timer**，界面永久停在最后一帧 —— 看上去和「任务卡住」一模一样。
     * 现在失败也继续调度（带退避），状态和事件各自独立更新。
     */
    const tick = async () => {
      if (cancelled || forced.current) return;
      const [statusRes, eventsRes] = await Promise.allSettled([
        getRun(runId),
        getEvents(runId, cursor),
      ]);
      // 请求在途时可能已经被 cleanup 掉，这里必须再查一次
      if (cancelled || forced.current) return;

      let next: RunStatus | null = null;
      if (statusRes.status === "fulfilled") {
        next = statusRes.value;
        setStatus(next);
      }
      if (eventsRes.status === "fulfilled" && eventsRes.value.length > 0) {
        const ev = eventsRes.value;
        cursor = ev[ev.length - 1].sequence;
        setEvents((prev) => [...prev, ...ev]);
      }

      const failed = statusRes.status === "rejected" || eventsRes.status === "rejected";
      if (failed) {
        const reason = statusRes.status === "rejected" ? statusRes.reason : eventsRes.status === "rejected" ? eventsRes.reason : null;
        setError(reason instanceof Error ? reason.message : String(reason));
        // 只有拿到了终态才允许停；请求失败不等于任务结束。
        retryMs = retryMs === 0 ? POLL_MS * 2 : Math.min(retryMs * 2, RETRY_MAX_MS);
        timer = window.setTimeout(tick, retryMs);
        return;
      }

      retryMs = 0;
      setError(null);
      // 终态停轮询
      if (next && TERMINAL.has(next.state)) return;
      timer = window.setTimeout(tick, POLL_MS);
    };

    void tick();
    return () => {
      cancelled = true;
      if (timer !== null) window.clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);

  const stop = useCallback(() => {
    forced.current = true;
  }, []);

  return { status, events, error, stop };
}

/** 当前阶段在状态链上的索引，用于点亮进度轨道。 */
export function stageIndex(state: RunState | undefined): number {
  const order: RunState[] = [
    "queued",
    "preparing",
    "planning",
    "installing",
    "testing",
    "diagnosing",
    "repairing",
    "succeeded",
  ];
  if (!state) return 0;
  if (state === "failed" || state === "cancelled") return 6;
  return Math.max(0, order.indexOf(state));
}
