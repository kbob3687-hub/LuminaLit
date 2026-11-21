import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, getEvents, getRun } from "../api";
import { TERMINAL, type RunEvent, type RunState, type RunStatus } from "../types";

const POLL_MS = 1000; // 契约 §4.2

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
  const [retrying, setRetrying] = useState(false);
  /** 外部强制停止（取消任务后立刻停轮询用）。 */
  const forced = useRef(false);

  useEffect(() => {
    forced.current = false;
    setEvents([]);
    setStatus(null);
    setError(null);
    setRetrying(false);

    // 演示模式与空任务也必须清掉上一轮的状态和错误。
    if (opts.mock) {
      setStatus(opts.mock.status);
      setEvents(opts.mock.events);
      return;
    }
    if (!runId) return;

    let cancelled = false;
    let timer: number | null = null;
    let cursor = 0;
    let retryMs = POLL_MS;

    const tick = async () => {
      if (cancelled || forced.current) return;
      let next: RunStatus;
      try {
        const [s, ev] = await Promise.all([getRun(runId), getEvents(runId, cursor)]);
        // 请求在途时可能已经被 cleanup 掉，这里必须再查一次。
        if (cancelled || forced.current) return;
        next = s;
        setStatus(s);
        setError(null);
        setRetrying(false);
        retryMs = POLL_MS;
        if (ev.length > 0) {
          cursor = ev[ev.length - 1].sequence;
          setEvents((prev) => [...prev, ...ev]);
        }
      } catch (e) {
        if (cancelled || forced.current) return;
        setRetrying(false);
        if (e instanceof ApiError && e.status === 404) {
          setError("任务不存在或已被清理，请返回选择页重新开始。");
          return;
        }
        const detail = e instanceof Error ? e.message : String(e);
        // fetch 的网络失败为 TypeError；解析错误和其他不可恢复错误不能伪装成网络重试。
        const retryable = e instanceof ApiError
          ? e.status >= 500 || e.status === 408 || e.status === 429
          : e instanceof TypeError;
        if (!retryable) {
          setError(`读取运行状态失败：${detail}。请刷新页面重试。`);
          return;
        }
        setRetrying(true);
        setError(`暂时无法更新运行进度：${detail}。将在 ${retryMs / 1000} 秒后自动重试。`);
        timer = window.setTimeout(tick, retryMs);
        retryMs = Math.min(retryMs * 2, 8000);
        return;
      }
      // 终态可能先于结果落盘或最后一页事件到达，必须等两者都收齐。
      if (TERMINAL.has(next.state) && next.result && cursor >= (next.last_event_sequence ?? 0)) return;
      timer = window.setTimeout(tick, POLL_MS);
    };

    void tick();
    return () => {
      cancelled = true;
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [runId, opts.mock]);

  const stop = useCallback(() => {
    forced.current = true;
  }, []);

  return { status, events, error, retrying, stop };
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
