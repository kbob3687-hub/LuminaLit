/**
 * 浏览器回归：?demo=1&scene=result&run=<合法 id> 必须保持离线回放。
 *
 * 运行前置（Playwright 不进 package.json 依赖，避免 CI 无浏览器时假绿）：
 *
 *   cd frontend
 *   npm i -D playwright && npx playwright install chromium
 *   npm run build && npx vite preview --port 4173 --host 127.0.0.1
 *   node tests/e2e-deeplink.mjs            # 可设 PREVIEW_URL 覆盖地址
 *
 * 断言与队长 review 的三条要求一一对应：保持 replay、结论显示「示例回放」、
 * 不发 environment 请求、不渲染真实下载入口。
 */
import assert from "node:assert/strict";
import { chromium } from "playwright";

const BASE = process.env.PREVIEW_URL ?? "http://127.0.0.1:4173";
const RUN = "a".repeat(32);
const url = `${BASE}/?demo=1&scene=result&run=${RUN}&still=1`;

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

const envRequests = [];
const runApiRequests = [];
const pageErrors = [];
page.on("request", (r) => {
  if (/\/api\/runs\/[^/]+\/environment/.test(r.url())) envRequests.push(r.url());
  if (/\/api\/runs\/[^/]+\/(status|events|result)/.test(r.url())) runApiRequests.push(r.url());
});
page.on("pageerror", (e) => pageErrors.push(e.message));

await page.goto(url, { waitUntil: "networkidle" });
await page.waitForTimeout(600);

const heading = await page.getByRole("heading", { name: "本次结果" }).count();
const replayLabel = await page.getByText("示例回放", { exact: false }).count();
const demoNotice = await page.getByRole("status").filter({ hasText: "离线预设回放" }).count();
const realDownloads = await page.locator('a[href*="/api/runs/"]').count();
const sections = await page.locator("section[aria-label^='结果第']").count();

assert.ok(heading > 0, "应进入独立结果页");
assert.equal(sections, 7, "结果页七项顺序保持完整");
assert.ok(replayLabel > 0, "结论必须显示「示例回放」");
assert.equal(demoNotice, 1, "回放必须带明确的离线提示");
assert.equal(envRequests.length, 0, `回放不得请求 environment.json：${envRequests.join(", ")}`);
assert.equal(runApiRequests.length, 0, `回放不得按 run 深链打真实 API：${runApiRequests.join(", ")}`);
assert.equal(realDownloads, 0, "回放不得渲染真实下载入口");
assert.deepEqual(pageErrors, [], "页面不应有未捕获异常");

console.log(`PASS  ${url}`);
console.log(`      七项=${sections} 示例回放=${replayLabel} 离线提示=${demoNotice}`);
console.log(`      environment 请求=${envRequests.length} run API 请求=${runApiRequests.length} 真实下载=${realDownloads}`);
await browser.close();
