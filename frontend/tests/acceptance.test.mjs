/**
 * 三层验收结论的回归断言（对应队长对 PR #29 的 review）。
 *
 * 这些用例过去放在仓库外的临时脚本里，review/CI 无法复跑。现在用仓库已有的
 * esbuild（vite 依赖，无新增依赖）把 runModel.ts 打成 ESM，再用 node:test 跑：
 *
 *   npm run test:acceptance
 *
 * 两条核心反例：
 *  1. exit 0 + 产物存在 + 独立指标验证失败 —— verified=false 不蕴含产物缺失；
 *  2. check.py 只产生产物、没有指标断言 —— 退出 0 不等于指标达标。
 */
import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { build } from "esbuild";

const root = path.resolve(import.meta.dirname, "..");
const tmp = await mkdtemp(path.join(tmpdir(), "paperharness-acceptance-"));
const outfile = path.join(tmp, "runModel.mjs");
await build({
  entryPoints: [path.join(root, "src", "runModel.ts")],
  bundle: true,
  platform: "node",
  format: "esm",
  outfile,
  logLevel: "silent",
});
const { buildAcceptance } = await import(pathToFileURL(outfile).href);
await rm(tmp, { recursive: true, force: true });

/** planning 事件下发 RunPlan；target_manifest + check.py 是靶场注入场景。 */
function planEvent(expectedArtifact = "coefficients.json") {
  return {
    sequence: 1,
    state: "planning",
    kind: "plan",
    message: "plan",
    data: {
      source: "target_manifest",
      command: ["python", "check.py"],
      expected_artifact: expectedArtifact,
    },
  };
}

function testEvent(exitCode = 0, stderrTail = "") {
  return {
    sequence: 2,
    state: "testing",
    kind: "test",
    message: "test",
    data: { exit_code: exitCode, stderr_tail: stderrTail, attempt: 1 },
  };
}

function verification(overrides = {}) {
  return {
    level: "execution",
    status: "passed",
    summary: "程序运行通过，尚未独立验证科研指标",
    checks: [],
    metrics: [],
    rule_id: null,
    validator_digest: null,
    artifact_sha256: null,
    verifier_image_id: null,
    checked_at: null,
    code_under_test: null,
    dataset: null,
    source: null,
    failure_category: null,
    evidence_url: null,
    limitations: [],
    ...overrides,
  };
}

function result(overrides = {}) {
  return {
    run_id: "a".repeat(32),
    outcome: "succeeded",
    verified: true,
    attempts: 1,
    failure_category: null,
    resolved_revision: null,
    patch_url: null,
    environment_url: null,
    report_url: "/report.md",
    ...overrides,
  };
}

const layer = (view, id) => {
  const found = view.layers.find((l) => l.id === id);
  assert.ok(found, `缺少 ${id} 层`);
  return found;
};

const events = () => [planEvent(), testEvent(0)];

test("反例 1：exit 0 + 产物存在 + 独立验证失败，L2 不能说产物缺失", () => {
  const view = buildAcceptance(
    "failed",
    events(),
    result({
      verified: false,
      outcome: "failed",
      verification: verification({
        level: "target",
        status: "failed",
        summary: "独立指标验收未通过：rmse 超出阈值",
        artifact_sha256: "a1b2c3d4e5f6a7b8c9d0",
        rule_id: "paper-pysindy-rmse",
        failure_category: "metric_threshold",
        checks: [{ name: "本次命令执行", passed: true, detail: "退出码 0" }],
        metrics: [
          { name: "rmse", observed: 0.9, threshold: 0.1, comparison: "lt", passed: false },
        ],
      }),
    }),
  );

  assert.equal(layer(view, "L1").status, "pass", "L1 仍由 exit_code=0 判定");
  assert.equal(layer(view, "L2").status, "pass", "产物已被独立验证器读取，不能判为缺失");
  assert.doesNotMatch(layer(view, "L2").detail, /缺失|产物缺失/);
  assert.equal(layer(view, "L3").status, "fail", "指标结论以独立验证器为准");
  assert.match(layer(view, "L3").detail, /独立指标验证未通过/);
});

test("反例 2：check.py 只产生产物、没有指标断言，L3 不能判通过", () => {
  const view = buildAcceptance(
    "succeeded",
    events(),
    result({
      verification: verification({
        level: "execution",
        status: "passed",
        checks: [
          { name: "命令执行", passed: true, detail: "退出码 0" },
          { name: "预期文件存在", passed: true, detail: "仅检查文件存在，不代表结构或指标正确" },
        ],
        summary: "程序运行通过，尚未独立验证科研指标",
      }),
    }),
  );

  assert.equal(layer(view, "L2").status, "pass", "存在性检查确实通过");
  assert.equal(layer(view, "L3").status, "unknown", "level=execution 没有指标结论");
  assert.match(layer(view, "L3").detail, /未下发指标结论/);
  assert.doesNotMatch(layer(view, "L3").detail, /全部通过|指标达标/);
});

test("旧后端不下发 verification：L2/L3 都显示无法确认，不从 verified 反推", () => {
  const view = buildAcceptance("failed", events(), result({ verified: false, outcome: "failed" }));

  assert.equal(layer(view, "L2").status, "unknown");
  assert.match(layer(view, "L2").detail, /无法确认/);
  assert.doesNotMatch(layer(view, "L2").detail, /缺失/);
  assert.equal(layer(view, "L3").status, "unknown");
  assert.match(layer(view, "L3").detail, /未下发指标结论/);
});

test("正向：后端下发 target 级 metrics 且全部 passed，L3 才判通过", () => {
  const view = buildAcceptance(
    "succeeded",
    events(),
    result({
      verification: verification({
        level: "target",
        status: "passed",
        summary: "独立指标验收通过",
        artifact_sha256: "deadbeefdeadbeef",
        rule_id: "paper-umap-ari",
        checks: [{ name: "本次命令执行", passed: true, detail: "退出码 0" }],
        metrics: [
          { name: "ari", observed: 0.02, threshold: 0.05, comparison: "lt", passed: true },
        ],
      }),
    }),
  );

  assert.equal(layer(view, "L3").status, "pass");
  assert.match(layer(view, "L3").detail, /1 项指标全部达标/);
  assert.ok(layer(view, "L3").evidence.some((line) => line.includes("ari")));
});

test("target 级但 status=blocked 或未下发 metrics：仍不判通过", () => {
  const blocked = buildAcceptance(
    "failed",
    events(),
    result({ verification: verification({ level: "target", status: "blocked", summary: "环境不足" }) }),
  );
  assert.equal(layer(blocked, "L3").status, "unknown");

  const noMetrics = buildAcceptance(
    "succeeded",
    events(),
    result({ verification: verification({ level: "target", status: "passed", metrics: [] }) }),
  );
  assert.equal(layer(noMetrics, "L3").status, "unknown");
  assert.match(layer(noMetrics, "L3").detail, /没有可判定的指标项/);
});

test("L1 仍由退出码判定，不因结构化字段而改变", () => {
  const view = buildAcceptance(
    "failed",
    [planEvent(), testEvent(1, "RuntimeError: boom")],
    result({ verified: false, outcome: "failed" }),
  );
  assert.equal(layer(view, "L1").status, "fail");
  assert.match(layer(view, "L1").detail, /非零退出码/);
});
