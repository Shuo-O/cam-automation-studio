"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const { chromium } = require("playwright");

const BASE_URL = process.env.CAM_DELIVERY_BASE_URL || "http://127.0.0.1:8765/delivery/";
const REAL_HTTP = process.env.CAM_DELIVERY_REAL_HTTP === "1";
const SCREENSHOT_ROOT = path.resolve(process.env.CAM_DELIVERY_SCREENSHOTS || "build/customer-browser");

const snapshot = {
  schema_version: 1,
  snapshot_id: "snapshot:fixture",
  content_hash: "sha256:snapshot",
  product: "powermill",
  instance_id: "pm-fixture",
  project_id: "demo-cavity",
  target_version: "PowerMill 2026",
  units: "mm",
  material: "P20",
  machine: { axes: 3 },
  source: "fixture",
  objects: [{ object_id: "cavity-1", kind: "pocket", name: "型腔", attributes: { role: "cavity" } }]
};
const sampleCase = {
  schema_version: 1,
  case_id: "case:fixture",
  case_hash: "sha256:case",
  name: "三轴型腔示例",
  product: "powermill",
  material: "P20",
  units: "mm",
  machine: { axes: 3 },
  tags: ["cavity"],
  parameters: { stepover: 0.5 },
  steps: [{ action: "cam.operation.plan" }],
  match: {
    score: 8,
    matched_conditions: ["product", "units", "material", "machine.axes"],
    missing_conditions: [],
    explanation: "适用性依据为产品、单位、材料、机床轴数和文本条件的逐项匹配；score 仅用于稳定排序，不代表成功率。"
  },
  provenance: { source: "fixture" }
};
const proposal = {
  schema_version: 1,
  proposal_id: "proposal:fixture",
  snapshot_id: snapshot.snapshot_id,
  case_id: sampleCase.case_id,
  snapshot_hash: snapshot.content_hash,
  case_hash: sampleCase.case_hash,
  proposal_hash: "sha256:proposal",
  parameters: sampleCase.parameters,
  dry_run: true,
  selector_bindings: [{
    step_id: "step-001",
    selector: { kind: "pocket", attributes: { role: "cavity" } },
    status: "resolved",
    matches: [{ object_id: "cavity-1", kind: "pocket", name: "型腔" }]
  }]
};

async function mockDeliveryApi(page, { unavailable = false } = {}) {
  let mockEvidence = [];
  await page.route("**/api/delivery/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (unavailable) {
      await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ schema_version: 1, error: "offline", code: "unavailable" }) });
      return;
    }
    if (path.endsWith("/status")) {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ schema_version: 1, mode: "offline_review", dry_run: true, machine_output_enabled: false, capabilities: { schema_version: 1, products: { nx: {}, powermill: {} } } }) });
    } else if (path.endsWith("/snapshots") && request.method() === "GET") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ schema_version: 1, items: [snapshot] }) });
    } else if (path.endsWith("/snapshots") && request.method() === "POST") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(snapshot) });
    } else if (path.includes("/snapshots/")) {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(snapshot) });
    } else if (path.includes("/proposals/") && request.method() === "GET") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(proposal) });
    } else if (path.endsWith("/cases/search")) {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ schema_version: 1, items: [sampleCase] }) });
    } else if (path.endsWith("/cases") && request.method() === "POST") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(sampleCase) });
    } else if (path.endsWith("/proposals") && request.method() === "POST") {
      mockEvidence = [];
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(proposal) });
    } else if (path.endsWith("/assess")) {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ schema_version: 1, ready: false, status: "blocked", checks: ["review gate is pending.", "simulation gate is pending.", "collision gate is pending.", "shop_approval gate is pending."], evidence: mockEvidence }) });
    } else if (path.endsWith("/evidence")) {
      const body = JSON.parse(request.postData() || "{}");
      mockEvidence.push({ ...body, schema_version: 1 });
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ...body, schema_version: 1 }) });
    } else if (path.endsWith("/export")) {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ schema_version: 1, status: "blocked", proposal, snapshot, assessment: { status: "blocked" }, evidence: mockEvidence }) });
    } else {
      await route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ schema_version: 1, error: "not_found", code: "not_found" }) });
    }
  });
}

function browserOptions() {
  const options = { headless: true };
  if (process.env.CAM_FLOW_CHROME) options.executablePath = process.env.CAM_FLOW_CHROME;
  return options;
}

test("客户工作台完成导入、检索、建议、证据和导出路径", { concurrency: false }, async () => {
  const browser = await chromium.launch(browserOptions());
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  try {
    if (!REAL_HTTP) await mockDeliveryApi(page);
    fs.mkdirSync(SCREENSHOT_ROOT, { recursive: true });
    await page.goto(BASE_URL, { waitUntil: "domcontentloaded" });
    await page.getByText("本地 API 已就绪").waitFor();
    await page.getByText("已导入快照").waitFor();
    await page.getByRole("button", { name: "载入示例" }).first().click();
    await page.getByText("快照已导入并固定哈希。").waitFor();
    await page.getByText("几何/加工对象").waitFor();
    await page.getByText("唯一绑定").waitFor();
    await page.getByRole("button", { name: "案例复用" }).click();
    await page.locator("#loadCaseExample").click();
    await page.getByText("案例已导入，可在结果中选择。").waitFor();
    await page.getByText("匹配依据").waitFor();
    await page.getByText("适用性依据为产品、单位、材料、机床轴数和文本条件的逐项匹配").waitFor();
    await page.locator(".resource-card").filter({ hasText: "三轴型腔示例" }).click();
    await page.getByText("建议已建立，状态仍需人工门禁。").waitFor();
    assert.equal(await page.locator("#proposalEmpty").isVisible(), false);
    assert.equal(await page.locator("#proposalView").isVisible(), true);
    await page.getByText("案例步骤的真实对象绑定").waitFor();
    await page.getByText("唯一命中", { exact: true }).waitFor();
    await page.getByRole("button", { name: "检查门禁" }).click();
    await page.getByText("未就绪", { exact: true }).waitFor();
    await page.getByText("人工审阅门禁待登记", { exact: true }).waitFor();
    await page.screenshot({ path: path.join(SCREENSHOT_ROOT, "customer-workbench-proposal.png"), fullPage: true });
    await page.getByRole("button", { name: "证据闭环" }).click();
    assert.equal(await page.locator(".evidence-card").count(), 4);
    const reviewGate = page.locator('[data-gate="review"]');
    await reviewGate.locator(".gate-state").selectOption("passed");
    await reviewGate.locator(".gate-actor").fill("验收员-01");
    await reviewGate.locator(".gate-note").fill("已核对当前建议、快照哈希和来源。");
    await reviewGate.getByRole("button", { name: "登记证据" }).click();
    await page.locator("#evidenceSummary").getByText("1 / 4 已登记").waitFor();
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.getByRole("button", { name: "证据闭环" }).click();
    assert.equal(await page.locator('[data-gate="review"] .gate-actor').inputValue(), "验收员-01");
    assert.match(await page.locator("#evidenceSummary").innerText(), /1 \/ 4/);
    for (const gate of ["simulation", "collision", "shop_approval"]) {
      const card = page.locator(`[data-gate="${gate}"]`);
      await card.locator(".gate-state").selectOption("passed");
      await card.locator(".gate-actor").fill(`验收员-${gate}`);
      await card.locator(".gate-note").fill(`已登记 ${gate} 的审阅说明。`);
      await card.getByRole("button", { name: "登记证据" }).click();
      await page.waitForTimeout(50);
    }
    await page.locator("#evidenceSummary").getByText("4 / 4 已登记").waitFor();
    await page.getByRole("button", { name: "导出交付包" }).click();
    assert.equal(await page.getByRole("button", { name: "下载 JSON" }).isEnabled(), true);
    assert.match(await page.locator("#exportSummary").innerText(), /fixture|P20|验收员|证据门禁/);
    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("button", { name: "下载 JSON" }).click();
    const download = await downloadPromise;
    const downloadPath = path.join(SCREENSHOT_ROOT, await download.suggestedFilename());
    await download.saveAs(downloadPath);
    const exported = JSON.parse(fs.readFileSync(downloadPath, "utf8"));
    assert.equal(exported.schema_version, 1);
    assert.equal(exported.dry_run ?? exported.proposal?.dry_run, true);
    assert.equal(exported.snapshot?.source, "fixture");
    assert.equal(exported.evidence?.length, 4);
    assert.deepEqual(exported.evidence.map((item) => item.actor).sort(), ["验收员-01", "验收员-collision", "验收员-shop_approval", "验收员-simulation"].sort());
    await page.getByRole("button", { name: "建立建议" }).click();
    await page.locator("#proposalParameters").fill('{"stepover":0.25}');
    await page.getByRole("button", { name: "重新生成" }).click();
    await page.getByText("建议已建立，状态仍需人工门禁。").waitFor();
    await page.getByRole("button", { name: "证据闭环" }).click();
    await page.locator("#evidenceSummary").getByText("0 / 4 已登记").waitFor();
    await page.getByRole("button", { name: "导出交付包" }).click();
    await page.screenshot({ path: path.join(SCREENSHOT_ROOT, "customer-workbench-desktop.png"), fullPage: true });
    await page.getByRole("button", { name: "制造上下文" }).click();
    await page.screenshot({ path: path.join(SCREENSHOT_ROOT, "customer-workbench-context.png"), fullPage: true });
    const mobile = await browser.newPage({ viewport: { width: 390, height: 844 } });
    if (!REAL_HTTP) await mockDeliveryApi(mobile);
    await mobile.goto(BASE_URL, { waitUntil: "domcontentloaded" });
    assert.equal(await mobile.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), true);
    await mobile.screenshot({ path: path.join(SCREENSHOT_ROOT, "customer-workbench-mobile.png"), fullPage: true });
    await mobile.close();
  } finally {
    await browser.close();
  }
});

test("API 不可用时显示可理解的错误状态", { concurrency: false }, async () => {
  const browser = await chromium.launch(browserOptions());
  const page = await browser.newPage();
  try {
    await mockDeliveryApi(page, { unavailable: true });
    await page.goto(BASE_URL, { waitUntil: "domcontentloaded" });
    await page.getByText("本地 API 不可用").waitFor();
    await page.getByText(/无法加载快照/).waitFor();
    assert.match(await page.locator("#snapshotList").innerText(), /还没有制造上下文/);
  } finally {
    await browser.close();
  }
});
