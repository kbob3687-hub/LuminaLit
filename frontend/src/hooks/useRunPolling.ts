import { useCallback, useEffect, useRef, useState } from "react";
import { getEvents, getRun } from "../api";
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
 */
export function useRunPolling(runId: string | null, opts: Options = {}) {
  const [status, setStatus] = useState<RunStatus | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const cursor = useRef(0);
  const timer = useRef<number | null>(null);
  const stopped = useRef(false);

  const stop = useCallback(() => {
    stopped.current = true;
    if (timer.current !== null) {
      window.clearTimeout(timer.current);
      timer.current = null;
    }
  }, []);

  useEffect(() => {
    // 演示模式
    if (opts.mock) {
      setStatus(opts.mock.status);
      setEvents(opts.mock.events);
      return;
    }
    if (!runId) return;

    stopped.current = false;
    cursor.current = 0;
    setEvents([]);
    setError(null);

    const tick = async () => {
      if (stopped.current) return;
      try {
        const [s, ev] = await Promise.all([
          getRun(runId),
          getEvents(runId, cursor.current),
        ]);
        setStatus(s);
        if (ev.length > 0) {
          cursor.current = ev[ev.length - 1].sequence;
          setEvents((prev) => [...prev, ...ev]);
        }
        // 终态停轮询
        if (TERMINAL.has(s.state)) {
          stop();
          return;
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
        stop();
        return;
      }
      timer.current = window.setTimeout(tick, POLL_MS);
    };

    void tick();
    return stop;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);

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
