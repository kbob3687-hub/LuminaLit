import { useEffect, useState } from "react";
import type { RunEvent, RunResult, RunState, RunStatus } from "../types";

/**
 * 演示模式：按真实时间轴回放一段 run 的事件流（不连后端）。
 *
 * 为什么不用一个写死的 status：之前 demo 直接把状态设成 repairing，
 * 阶段条一进场就整排变绿——演示看着像"还没跑就成功了"。这里从 queued
 * 开始，一条一条往后推，前端所有推导逻辑（阶段条/回环/ACI）走的是
 * 和真接口完全相同的代码路径。
 */

interface Step {
  after: number;
  state: RunState;
  kind: string;
  message: string;
  data?: Record<string, unknown>;
}

const PLAN = {
  resolved_revision: "5e84a93",
  python_version: "3.11",
  command: ["python", "-I", "check.py"],
  // 与 targets/paper-umap/manifest.json 对齐：真实靶场都声明了产物。
  // 原来写 null，会让 L2 在演示里显示「未下发」，而真实运行不是这样。
  expected_artifact: "out.txt",
  source: "target_manifest",
};

const RESULT: RunResult = {
  run_id: "demo5e84a93",
  outcome: "succeeded",
  verified: true,
  attempts: 3,
  failure_category: "version_conflict",
  resolved_revision: "5e84a93",
  patch_url: "/api/runs/demo/patch",
  environment_url: "/api/runs/demo/environment",
  report_url: "/api/runs/demo/report",
};

const SCRIPT: Step[] = [
  { after: 900, state: "preparing", kind: "preparing", message: "cloned at 5e84a93" },
  { after: 1500, state: "planning", kind: "plan", message: "run plan resolved", data: PLAN },
  {
    after: 1300,
    state: "installing",
    kind: "install",
    message: "dependency install finished",
    data: { exit_code: 1, stderr_tail: "ModuleNotFoundError: No module named 'pysindy'" },
  },
  {
    after: 1400,
    state: "diagnosing",
    kind: "diagnosis",
    message: "failure classified as missing_dependency",
    data: { failure_class: "missing_dependency", attempt: 1, phase: "install" },
  },
  {
    after: 1100,
    state: "repairing",
    kind: "read_file",
    message: "read 42 lines from examples/example.py",
    data: { ok: true },
  },
  {
    after: 1000,
    state: "repairing",
    kind: "search_text",
    message: "3 matches",
    data: { ok: true },
  },
  {
    after: 1000,
    state: "repairing",
    kind: "read_file",
    message: "path_escape:../../etc/passwd",
    data: { ok: false },
  },
  {
    after: 1100,
    state: "repairing",
    kind: "install_requirement",
    message: "requirement_not_allowed",
    data: { ok: false },
  },
  {
    after: 1100,
    state: "repairing",
    kind: "install_requirement",
    message: "install pysindy==2.1.0 exited with 0",
    data: { ok: true, exit_code: 0 },
  },
  {
    after: 1200,
    state: "repairing",
    kind: "finish_repair",
    message: "补上 pysindy 依赖并固定版本",
    data: { ok: true },
  },
  { after: 800, state: "repairing", kind: "repair_round", message: "repair round 1 finished" },
  {
    after: 800,
    state: "installing",
    kind: "install",
    message: "dependency install finished",
    data: { exit_code: 0 },
  },
  {
    after: 1200,
    state: "testing",
    kind: "test",
    message: "smoke test finished (attempt 2)",
    data: { exit_code: 1, stderr_tail: "AssertionError: 系数 -1.83 ≠ -2.0", attempt: 2 },
  },
  {
    after: 1300,
    state: "diagnosing",
    kind: "diagnosis",
    message: "failure classified as version_conflict",
    data: { failure_class: "version_conflict", attempt: 2, phase: "test" },
  },
  {
    // 模型的第一反应是改判据让自己过关。ACI 的 protected_file 会拦下它——
    // 这条事件就是演示「考生不能自己判卷」的那一帧。
    after: 900,
    state: "repairing",
    kind: "replace_exact",
    message: "protected_file:check.py",
    data: { ok: false, path: "check.py" },
  },
  {
    // 被拦之后才去做真正的修复：改依赖声明，而不是改验收标准。
    after: 1000,
    state: "repairing",
    kind: "replace_exact",
    message: "replaced 1 occurrence in requirements.txt",
    data: { ok: true, path: "requirements.txt" },
  },
  {
    after: 1200,
    state: "repairing",
    kind: "run_smoke_test",
    message: "smoke test exited with 0",
    data: { ok: true, exit_code: 0 },
  },
  {
    after: 1100,
    state: "repairing",
    kind: "finish_repair",
    message: "锁定 pysindy 2.1.0 后系数命中 -2.000",
    data: { ok: true },
  },
  { after: 700, state: "repairing", kind: "repair_round", message: "repair round 2 finished" },
  {
    after: 800,
    state: "testing",
    kind: "test",
    message: "smoke test finished (attempt 3)",
    data: { exit_code: 0, attempt: 3 },
  },
  {
    after: 1100,
    state: "succeeded",
    kind: "final",
    message: "run succeeded",
    data: { verified: true },
  },
];

export function useDemoRun(enabled: boolean, seekMs = 0) {
  const [status, setStatus] = useState<RunStatus | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);

  useEffect(() => {
    if (!enabled) return;
    let timers: number[] = [];
    let seq = 0;

    const start = () => {
      seq = 0;
      const pre: RunEvent[] = [];
      let acc = 0;
      // ?t=<ms> 直接快进到第 N 毫秒（截图与演示定位用），之前的事件一次性铺好。
      for (const step of SCRIPT) {
        acc += step.after;
        if (acc > seekMs) break;
        seq += 1;
        pre.push({
          run_id: RESULT.run_id,
          sequence: seq,
          timestamp: new Date(Date.now() - (seekMs - acc)).toISOString(),
          state: step.state,
          kind: step.kind,
          message: step.message,
          data: step.data ?? {},
          log_path: null,
        });
      }
      setEvents(pre);
      setStatus({
        run_id: RESULT.run_id,
        state: pre.at(-1)?.state ?? "queued",
        result: pre.at(-1)?.state === "succeeded" ? RESULT : null,
      });

      let offset = 0;
      for (const step of SCRIPT.slice(pre.length)) {
        offset += step.after;
        timers.push(
          window.setTimeout(() => {
            seq += 1;
            const event: RunEvent = {
              run_id: RESULT.run_id,
              sequence: seq,
              timestamp: new Date().toISOString(),
              state: step.state,
              kind: step.kind,
              message: step.message,
              data: step.data ?? {},
              log_path: null,
            };
            setEvents((prev) => [...prev, event]);
            setStatus({
              run_id: RESULT.run_id,
              state: step.state,
              result: step.state === "succeeded" ? RESULT : null,
            });
          }, offset),
        );
      }
      // 循环演示：结束后停 6 秒再从头播
      timers.push(
        window.setTimeout(() => {
          timers.forEach((t) => window.clearTimeout(t));
          timers = [];
          start();
        }, offset + 6000),
      );
    };

    start();
    return () => {
      timers.forEach((t) => window.clearTimeout(t));
      timers = [];
    };
  }, [enabled, seekMs]);

  return { status, events };
}
