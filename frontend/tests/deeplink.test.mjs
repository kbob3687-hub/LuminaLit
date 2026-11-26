/**
 * 数据来源模式与深链的回归断言（对应队长对 PR #39 的 review）。
 *
 * 用仓库已有的 esbuild（vite 依赖，无新增依赖）把 deepLink.ts 打成 ESM，
 * 再用 node:test 跑：npm test
 *
 * 核心反例：`?demo=1&scene=result&run=<合法 id>` 曾被 linkedRun 抢成 live，
 * 导致 ResultScene 的 isDemo=false，环境请求与真实下载没有被关掉。
 */
import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { build } from "esbuild";

const root = path.resolve(import.meta.dirname, "..");
const tmp = await mkdtemp(path.join(tmpdir(), "paperharness-deeplink-"));
const outfile = path.join(tmp, "deepLink.mjs");
await build({
  entryPoints: [path.join(root, "src", "deepLink.ts")],
  bundle: true,
  platform: "node",
  format: "esm",
  outfile,
  logLevel: "silent",
});
const { resolveRunMode, parseScene } = await import(pathToFileURL(outfile).href);
await rm(tmp, { recursive: true, force: true });

const RUN = "a".repeat(32);
const q = (s) => new URLSearchParams(s);
const mode = (s, opts = {}) => resolveRunMode(q(s), { healthy: true, ...opts });

test("review 反例：demo=1 + 合法 run 深链必须保持 replay", () => {
  const params = q(`demo=1&scene=result&run=${RUN}&still=1`);
  assert.equal(resolveRunMode(params, { healthy: true }), "replay");
  assert.equal(parseScene(params), "result", "run 仍作为结果页的回放上下文");
});

test("demo=1 优先于 live=1 / healthy=true / 环境变量 demo", () => {
  assert.equal(mode(`demo=1&run=${RUN}&live=1`), "replay");
  assert.equal(mode(`run=${RUN}&envDemo=1`, { envDemo: true }), "replay");
  assert.equal(mode(`demo=1&scene=run&run=${RUN}`), "replay");
});

test("没有 demo 时，合法 run 深链 / live=1 仍走真实 API", () => {
  assert.equal(mode(`run=${RUN}`), "live");
  assert.equal(mode(`scene=result&run=${RUN}`), "live");
  assert.equal(mode(`live=1&demo=0`), "live");
  assert.equal(parseScene(q(`scene=result&run=${RUN}`)), "result");
});

test("健康检查失败且没有 live 声明时才自动回退回放", () => {
  assert.equal(resolveRunMode(q(""), { healthy: false }), "replay");
  assert.equal(resolveRunMode(q(""), { healthy: null }), "live");
  assert.equal(resolveRunMode(q(`run=${RUN}`), { healthy: false }), "live");
});

test("非法 run 参数不会被拼进 API 路径，demo=1 仍按回放处理", () => {
  assert.equal(mode("demo=1&run=javascript:alert(1)"), "replay");
  assert.equal(parseScene(q("scene=result&run=javascript:alert(1)")), "select");
});
