import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { build } from "esbuild";

const temp = await mkdtemp(path.join(tmpdir(), "paperharness-workbench-"));
const modules = {};
try {
  for (const name of ["runModel", "api", "deepLink", "components/FlowGraph"]) {
    const key = path.basename(name);
    const outfile = path.join(temp, `${key}.mjs`);
    await build({ entryPoints: [path.resolve(import.meta.dirname, `../src/${name}.${key === "FlowGraph" ? "tsx" : "ts"}`)], bundle: true,
      platform: "node", format: "esm", outfile, define: { "import.meta.env": "{}", "window.location.search": '""' }, logLevel: "silent" });
    modules[key] = await import(pathToFileURL(outfile).href);
  }
} finally {
  await rm(temp, { recursive: true, force: true });
}
const { overallConclusion, buildLoop, isBlockedEvent } = modules.runModel;
const { bundleDownloadUrl, fetchBundle } = modules.api;
const id = "a".repeat(32);
const { buildGraph } = modules.FlowGraph;
test("保留旧前端修复：扁平动作参数优先，历史嵌套字段仍可读取", () => {
  const { targetOf, actionParams } = modules.runModel;
  const event = { kind:"search_text", message:"3 matches", data:{query:"ImportError",request:{query:"stale"}} };
  assert.equal(actionParams(event).query,"ImportError");
  assert.equal(targetOf(event),'搜索 "ImportError"');
  assert.equal(targetOf({...event,data:{path:"src/main.py"}}),"src/main.py");
  assert.equal(targetOf({...event,data:{request:{path:"legacy.py",start_line:12}}}),"legacy.py:12");
  assert.equal(targetOf({kind:"set_environment",message:"set OMP_NUM_THREADS=2",data:{env_name:"OMP_NUM_THREADS",env_value:"2"}}),"OMP_NUM_THREADS=2");
});
test("保留旧前端修复：完整动作表不截断，错误类别与阶段不丢失", () => {
  const { buildRows, explainFailure, buildStages, failureText } = modules.runModel;
  const events = Array.from({length:100},(_,i)=>({run_id:id,sequence:i+1,timestamp:new Date(i*1000).toISOString(),state:"repairing",kind:"read_file",message:"read file",data:{ok:true}}));
  assert.equal(buildRows(events).length,100);
  for (const category of ["invalid_expected_artifact","timeout","internal_error","unknown","none"]) {
    assert.ok(!explainFailure(category,[]).verdict.includes("没有对应的解释"));
    assert.notEqual(failureText(category),category);
  }
  const failed = [{...events[0],state:"failed",kind:"final",message:"failed",data:{failure_category:"invalid_expected_artifact"}}];
  assert.equal(buildStages(failed,"failed").find(s=>s.id==="planning").status,"bad");
});
test("保留 main 的环境清单直链，非法文件标签回退报告", () => {
  assert.equal(modules.deepLink.parseTab(new URLSearchParams("tab=env")),"env");
  assert.equal(modules.deepLink.parseTab(new URLSearchParams("tab=../../secret")),"report");
});
test("拓扑不凭终态点亮验证；复跑失败保留当前通过", () => {
  const base = { state: "succeeded", stages: [{ id: "verifying", status: "done" }, { id: "replaying", status: "done" }], loop: { rounds: [] }, rows: [], events: [] };
  const empty = buildGraph(base);
  assert.equal(empty.nodes.find(n => n.id === "verify").tone, "todo");
  assert.equal(empty.nodes.find(n => n.id === "replay").tone, "todo");
  const failed = buildGraph({ ...base, result: { verification: { status: "passed" }, replay: { status: "failed" }, report_url: "/report" } });
  assert.equal(failed.nodes.find(n => n.id === "verify").tone, "done");
  assert.equal(failed.nodes.find(n => n.id === "replay").tone, "bad");
  assert.equal(failed.nodes.find(n => n.id === "report").sub, "已生成");
  assert.ok(failed.edges.some(e => e.from === "test" && e.to === "verify"));
  assert.ok(!failed.edges.some(e => e.from === "repair" && e.to === "report"));
});
test("拓扑审查与拦截计数来自完整事件，不受最近80行动作限制", () => {
  const events = Array.from({length:100}, (_,i) => ({sequence:i+1,kind:"read_file",state:"repairing",message:"path_escape:outside",data:{failure_label:"path_escape"}}));
  const graph = buildGraph({state:"repairing",stages:[],loop:{rounds:[]},rows:[],events});
  assert.equal(graph.blockedTotal,100);
  assert.equal(graph.nodes.find(n => n.id === "gate").sub,"审查 100 次");
});
const bundle = { integrity: "complete", url: `/api/runs/${id}/bundle`, sha256: "b".repeat(64) };
const result = { run_id: id, outcome: "succeeded", verified: true,
  verification: { level: "target", status: "passed" }, replay: { status: "passed" }, bundle };
const conclude = (changes = {}, state = "succeeded") => overallConclusion({ state, result: { ...result, ...changes } }, false);

test("当前通过而复跑失败时保留当前证据，不显示完整成功", () => {
  const c = conclude({ outcome: "failed", replay: { status: "failed" } }, "failed");
  assert.equal(c.label, "当前运行通过，干净复跑失败");
  assert.equal(c.completeSuccess, false);
});
test("未复跑、条件不足、取消与预算耗尽分别保留含义", () => {
  assert.match(conclude({ replay: { status: "not_requested" } }).label, /未验证可复跑性/);
  assert.match(conclude({ replay: { status: "blocked" } }, "failed").label, /复跑条件不足/);
  assert.match(conclude({ outcome: "cancelled", replay: { status: "blocked", reason: "cancelled" } }, "cancelled").label, /已取消/);
  assert.match(conclude({ outcome: "budget_exceeded" }, "failed").label, /预算已用完/);
});
test("只有当前验收、复跑和材料齐全才显示完整成功", () => {
  assert.equal(conclude().completeSuccess, true);
  assert.equal(conclude({ verification: { level: "target", status: "failed" } }).completeSuccess, false);
  assert.equal(conclude({ bundle: { integrity: "incomplete" } }).completeSuccess, false);
  assert.equal(conclude({ replay: undefined }).completeSuccess, false);
  assert.equal(conclude({ verification: undefined }).completeSuccess, false);
  assert.match(overallConclusion({ state: "succeeded", result: null }, false).label, /整理结果/);
});
test("安全拒绝被显示为拦截，干净复跑不继续显示修复中", () => {
  assert.equal(isBlockedEvent({ kind: "replace_exact", message: "protected_file:check.py", data: {} }), true);
  const timestamp = "2026-11-25T00:00:00Z";
  const loop = buildLoop([{ kind: "diagnosis", state: "diagnosing", timestamp, data: {} },
    { kind: "replay_started", state: "replaying", timestamp, data: {} }], "replaying");
  assert.equal(loop.active, false);
  assert.ok(loop.rounds[0].finishedAt);
});
test("材料 URL 只允许当前任务的固定下载端点", () => {
  assert.equal(bundleDownloadUrl(id, bundle), bundle.url);
  for (const url of [`/api/runs/${id}/../other/bundle`, `/api/runs/${id}/bundle?redirect=x`, "https://example.com/bundle"]) {
    assert.equal(bundleDownloadUrl(id, { ...bundle, url }), null);
  }
  assert.equal(bundleDownloadUrl("c".repeat(32), bundle), null);
  assert.equal(bundleDownloadUrl(id, { ...bundle, integrity: "blocked" }), null);
});
test("409 校验失败给出可读错误，JSON 不会当作 ZIP", async () => {
  const previous = globalThis.fetch;
  try {
    globalThis.fetch = async () => new Response(JSON.stringify({ detail: { code: "bundle_corrupted" } }), { status: 409 });
    await assert.rejects(fetchBundle(id, bundle), /材料包校验失败/);
    globalThis.fetch = async () => new Response("{}", { headers: { "Content-Type": "application/json" } });
    await assert.rejects(fetchBundle(id, bundle), /不是 ZIP/);
    globalThis.fetch = async () => new Response("zip fixture", { headers: { "Content-Type": "application/zip" } });
    assert.equal(await (await fetchBundle(id, bundle)).text(), "zip fixture");
  } finally { globalThis.fetch = previous; }
});
