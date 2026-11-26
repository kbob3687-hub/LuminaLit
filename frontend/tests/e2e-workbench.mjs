/** 构建后运行 npm run test:e2e。固定 API 夹具验证页面契约，不计作真实科研运行。 */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdir } from "node:fs/promises";
import path from "node:path";
import { chromium } from "playwright";

const root = path.resolve(import.meta.dirname, "..");
const base = process.env.PREVIEW_URL ?? "http://127.0.0.1:4187";
const server = process.env.PREVIEW_URL ? null : spawn(process.execPath,
  [path.join(root, "node_modules/vite/bin/vite.js"), "preview", "--host", "127.0.0.1", "--port", "4187", "--strictPort"],
  { cwd: root, windowsHide: true, stdio: "ignore" });
let browser;
const id = "a".repeat(32), preflightId = "b".repeat(32), digest = "c".repeat(64);
const verification = { level: "target", status: "passed", summary: "解析系数夹具通过", checks: [{ name: "系数误差", passed: true, detail: "小于固定阈值" }],
  metrics: [{ name: "系数误差", observed: 0, threshold: 0.0001, comparison: "lt", passed: true }], rule_id: "pysindy-linear-v1", validator_digest: digest,
  artifact_sha256: digest, code_under_test: "工程测试夹具", dataset: "解析系数", source: "固定规则", limitations: [], evidence_url: `/api/runs/${id}/verification/input` };
const result = { run_id: id, outcome: "succeeded", verified: true, attempts: 1, failure_category: null, resolved_revision: "d".repeat(40),
  report_url: `/api/runs/${id}/report`, patch_url: `/api/runs/${id}/patch`, environment_url: `/api/runs/${id}/environment`, verification,
  replay: { status: "passed", verification, checks: [], limitations: [], differences: [] },
  bundle: { integrity: "complete", url: `/api/runs/${id}/bundle`, sha256: digest, manifest_digest: digest, missing: [], external_inputs: [] } };

async function fixture(overrides = {}, options = {}) {
  const context = await browser.newContext({ viewport: options.mobile ? { width: 390, height: 844 } : { width: 1365, height: 900 }, acceptDownloads: true });
  const page = await context.newPage();
  const errors = [], requests = [], downloads = [];
  page.on("pageerror", e => errors.push(e.message));
  page.on("download", d => downloads.push(d));
  const status = { run_id: id, state: "succeeded", result: { ...structuredClone(result), ...overrides }, last_event_sequence: 0, ...options.status };
  await page.route("**/api/**", async route => {
    const request = route.request(), url = new URL(request.url());
    requests.push({ path: url.pathname, method: request.method() });
    const json = body => route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
    if (url.pathname === "/api/health") return json({ status: "ok", checks: [] });
    if (url.pathname === "/api/targets") return json([{ id: "e2e-example", repo_url: "https://github.com/paperharness-demo/fixture", command: ["python", "check.py"], expected_artifact: "coefficients.json" }]);
    if (url.pathname === "/api/preflights") return json({ preflight_id: preflightId, target_id: "e2e-example", repo_url: "https://github.com/paperharness-demo/fixture",
      created_at: new Date().toISOString(), expires_at: new Date(Date.now()+600000).toISOString(), confirmation_digest: digest,
      plan: { resolved_revision: "d".repeat(40), python_version: "3.10", command: ["python", "check.py"], expected_artifact: "coefficients.json", source: "target_manifest" },
      timeout_seconds: 120, max_repairs: 0, checks: [], limitations: ["工程测试夹具"], run_id: null });
    if (url.pathname === "/api/runs" && request.method() === "POST") return json({ run_id: id, state: "queued" });
    if (url.pathname === `/api/runs/${id}`) return json(status);
    if (url.pathname.endsWith("/events")) return json((options.events ?? []).filter(e => e.sequence > Number(url.searchParams.get("after") ?? 0)));
    if (url.pathname.endsWith("/environment")) return json({ packages: ["numpy==1.26.4"], environment: {} });
    if (url.pathname.endsWith("/bundle")) {
      if (options.corrupted) return route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ detail: { code: "bundle_corrupted" } }) });
      return route.fulfill({ contentType: "application/zip", body: Buffer.from("UEsFBgAAAAAAAAAAAAAAAAAAAAAAAA==", "base64") });
    }
    return route.fulfill({ status: 404, contentType: "application/json", body: "{}" });
  });
  return { context, page, errors, requests, downloads };
}

async function check(name, run) { await run(); console.log(`PASS ${name}`); }
try {
  await mkdir(path.resolve(root,"../runs-test/browser"), {recursive:true});
  for (let attempt = 0; attempt < 100; attempt++) {
    try { if ((await fetch(base)).ok) break; } catch {}
    if (attempt === 99) throw new Error("preview server did not start");
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || "msedge", headless: true });
  await check("四视图：示例 → 确认 → 运行 → 结果，确认前不创建任务", async () => {
    const f = await fixture();
    await f.page.goto(`${base}/?live=1&still=1`);
    await f.page.getByText("先用内置示例了解流程", { exact: false }).click();
    await f.page.getByRole("button", { name: /e2e-example/ }).click();
    await f.page.getByRole("button", { name: "确认并开始试跑" }).waitFor();
    assert.equal(f.requests.filter(r => r.method === "POST" && r.path === "/api/runs").length, 0);
    await f.page.getByRole("button", { name: "确认并开始试跑" }).click();
    await f.page.getByRole("button", { name: "查看完整结果" }).click();
    await f.page.getByRole("heading", { name: "本次结果" }).waitFor();
    assert.equal(await f.page.locator("section[aria-label^='结果第']").count(), 7);
    assert.equal(f.requests.filter(r => r.method === "POST" && r.path === "/api/runs").length, 1);
    assert.deepEqual(f.errors, []);
    await f.context.close();
  });
  for (const [name, changes, state, expected] of [
    ["完整成功", {}, "succeeded", "指定等级通过，干净复跑通过"],
    ["复跑失败保留当前证据", { outcome: "failed", replay: { status: "failed", verification: { ...verification, status: "failed" } } }, "failed", "当前运行通过，干净复跑失败"],
    ["复跑受阻", { outcome: "failed", replay: { status: "blocked", reason: "replay_clone_failed" } }, "failed", "当前运行通过，复跑条件不足"],
    ["探索试跑", { replay: { status: "not_requested" } }, "succeeded", "试跑通过，未验证可复跑性"],
    ["材料不完整保留复跑通过", { bundle: { integrity: "incomplete", missing: [{ path: "model.py", reason: "未完整记录" }] } }, "succeeded", "指定等级通过，干净复跑通过，材料未齐全"],
    ["材料导出受阻显示原因", { bundle: { integrity: "blocked", items: [{ path: "repair_manifest.json", status: "blocked", reason: "工作区无法审计" }] } }, "succeeded", "指定等级通过，干净复跑通过，材料未齐全"],
    ["取消保留证据", { outcome: "cancelled", replay: { status: "blocked", reason: "cancelled" } }, "cancelled", "已取消，同时保留已有证据"],
  ]) await check(name, async () => {
    const f = await fixture(changes, { status: { state } });
    await f.page.goto(`${base}/?live=1&scene=result&run=${id}&still=1`);
    await f.page.getByText(expected, { exact: true }).waitFor();
    await f.page.getByText("目标指标检查", { exact: true }).waitFor();
    assert.deepEqual(f.errors, []);
    if (changes.bundle && changes.bundle.integrity !== "complete") assert.equal(await f.page.getByRole("button", { name: "下载复跑材料", exact: true }).count(), 0);
    if (name === "材料导出受阻显示原因") await f.page.getByText(/工作区无法审计/).waitFor();
    await f.context.close();
  });
  await check("校验失败留在页面、没有下载，成功响应才下载 ZIP", async () => {
    const bad = await fixture({}, { corrupted: true });
    await bad.page.goto(`${base}/?live=1&scene=result&run=${id}&still=1`);
    await bad.page.getByRole("button", { name: "下载复跑材料", exact: true }).first().click();
    await bad.page.getByRole("alert").filter({ hasText: "材料包校验失败" }).waitFor();
    assert.equal(bad.downloads.length, 0);
    await bad.context.close();
    const good = await fixture();
    await good.page.goto(`${base}/?live=1&scene=result&run=${id}&still=1`);
    const downloaded = good.page.waitForEvent("download");
    await good.page.getByRole("button", { name: "下载复跑材料", exact: true }).first().click();
    assert.equal((await downloaded).suggestedFilename(), "reproduce.zip");
    await good.context.close();
  });
  await check("离线回放不请求真实运行、不提供真实下载", async () => {
    const f = await fixture();
    await f.page.goto(`${base}/?demo=1&scene=result&run=${id}&still=1`);
    await f.page.getByText("示例回放", { exact: true }).waitFor();
    assert.equal(f.requests.filter(r => r.path.startsWith("/api/runs")).length, 0);
    assert.equal(await f.page.getByRole("button", { name: "下载复跑材料", exact: true }).count(), 0);
    await f.context.close();
  });
  await check("合并运行台：复跑失败与当前验证分开，节点可键盘打开并归还焦点", async () => {
    const f = await fixture({ outcome:"failed", replay:{status:"failed"} }, {status:{state:"failed"}});
    await f.page.goto(`${base}/?live=1&scene=run&run=${id}&still=1`);
    await f.page.getByRole("heading",{name:"当前运行通过，干净复跑失败"}).waitFor();
    assert.equal(await f.page.locator('[data-node="verify"]').getAttribute("data-tone"),"done");
    assert.equal(await f.page.locator('[data-node="replay"]').getAttribute("data-tone"),"bad");
    const node = f.page.getByRole("button",{name:/^独立验证，/});
    await node.focus(); await f.page.keyboard.press("Enter");
    await f.page.getByRole("dialog",{name:"独立验证证据"}).waitFor();
    await f.page.keyboard.press("Escape");
    await f.page.getByRole("dialog").waitFor({state:"hidden"});
    assert.equal(await node.evaluate(el=>el===document.activeElement),true);
    await f.page.screenshot({path:path.resolve(root,"../runs-test/browser/console-replay-failed.png")});
    assert.deepEqual(f.errors,[]); await f.context.close();
  });
  await check("390px 首页与运行台无页面溢出；减弱动效时禁用粒子和循环动画", async () => {
    const f = await fixture({}, {mobile:true});
    await f.page.emulateMedia({reducedMotion:"reduce"});
    await f.page.goto(`${base}/?live=1`);
    await f.page.locator('#repo-input').waitFor();
    assert.equal(await f.page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    assert.equal(await f.page.locator('canvas').count(),0);
    await f.page.screenshot({path:path.resolve(root,"../runs-test/browser/home-mobile.png")});
    await f.page.goto(`${base}/?live=1&scene=run&run=${id}`);
    await f.page.getByRole("heading",{name:"指定等级通过，干净复跑通过"}).waitFor();
    assert.equal(await f.page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    assert.equal(await f.page.locator('animateMotion').count(),0);
    await f.page.getByRole("button",{name:/^干净复跑，/}).focus();
    await f.page.keyboard.press("Enter");
    await f.page.getByRole("dialog",{name:"干净复跑与材料"}).waitFor();
    await f.page.keyboard.press("Escape");
    await f.page.getByRole("dialog").waitFor({state:"hidden"});
    await f.page.screenshot({path:path.resolve(root,"../runs-test/browser/console-mobile.png")});
    assert.deepEqual(f.errors,[]); await f.context.close();
  });
  await check("390px 结果页无横向溢出", async () => {
    const f = await fixture({}, { mobile: true });
    await f.page.goto(`${base}/?live=1&scene=result&run=${id}&still=1`);
    await f.page.getByText("指定等级通过，干净复跑通过", { exact: true }).waitFor();
    const overflow = await f.page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
    assert.equal(overflow, false);
    const output = path.resolve(root, "../runs-test/browser");
    await mkdir(output, { recursive: true });
    await f.page.screenshot({ path: path.join(output, "workbench-mobile.png"), fullPage: true });
    await f.page.setViewportSize({ width: 1365, height: 900 });
    await f.page.screenshot({ path: path.join(output, "workbench-result.png"), fullPage: true });
    await f.context.close();
  });
} finally {
  await browser?.close();
  server?.kill();
}
