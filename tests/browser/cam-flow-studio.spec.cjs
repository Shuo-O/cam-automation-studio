"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");
const sharp = require("sharp");

const ROOT = path.resolve(__dirname, "..", "..");
const FIXTURE_ROOT = path.join(__dirname, "fixtures", "cam-flow");
const CONFIG = JSON.parse(
  fs.readFileSync(path.join(FIXTURE_ROOT, "acceptance.json"), "utf8")
);
const ARTIFACT_ROOT = path.resolve(
  process.env.CAM_FLOW_BROWSER_ARTIFACTS
    || path.join(FIXTURE_ROOT, "artifacts")
);
const SCREENSHOT_ROOT = path.join(ARTIFACT_ROOT, "screenshots");
const TRACE_ROOT = path.join(ARTIFACT_ROOT, "traces");
const AUDIT_ROOT = path.join(ARTIFACT_ROOT, "audits");
const BASE_URL = process.env.CAM_FLOW_BASE_URL || "http://127.0.0.1:8765";
const CHROME_PATH = process.env.CAM_FLOW_CHROME;

for (const directory of [ARTIFACT_ROOT, SCREENSHOT_ROOT, TRACE_ROOT, AUDIT_ROOT]) {
  fs.mkdirSync(directory, { recursive: true });
}

let browser;

test.before(async () => {
  assert.ok(CHROME_PATH, "CAM_FLOW_CHROME must point to an installed Chrome/Edge executable.");
  browser = await chromium.launch({
    executablePath: CHROME_PATH,
    headless: process.env.CAM_FLOW_HEADED !== "1",
    args: [
      "--disable-background-networking",
      "--disable-component-update",
      "--disable-default-apps",
      "--disable-sync",
      "--metrics-recording-only",
      "--no-first-run"
    ]
  });
});

test.after(async () => {
  await browser?.close();
});

function viewport(name) {
  const item = CONFIG.viewports.find((candidate) => candidate.name === name);
  assert.ok(item, `Missing viewport fixture ${name}.`);
  return { width: item.width, height: item.height };
}

async function pluginRequest(pluginId, action = "install") {
  const response = await fetch(`${BASE_URL}/api/plugins/${action}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ plugin_id: pluginId })
  });
  assert.ok(response.ok, `${action} ${pluginId} returned HTTP ${response.status}.`);
  return response.json();
}

async function ensureFlowPluginInstalled() {
  await pluginRequest("cam-local-capture");
  await pluginRequest("powermill-cam-copilot");
}

function attachAudit(page) {
  const audit = {
    schema_version: 1,
    page_errors: [],
    console_errors: [],
    request_failures: [],
    requests: [],
    responses: []
  };
  page.on("pageerror", (error) => {
    audit.page_errors.push(String(error?.stack || error));
  });
  page.on("console", (message) => {
    if (message.type() === "error") {
      audit.console_errors.push(message.text());
    }
  });
  page.on("request", (request) => {
    audit.requests.push({
      method: request.method(),
      resource_type: request.resourceType(),
      url: request.url()
    });
  });
  page.on("requestfailed", (request) => {
    audit.request_failures.push({
      method: request.method(),
      url: request.url(),
      error: request.failure()?.errorText || "unknown"
    });
  });
  page.on("response", (response) => {
    audit.responses.push({
      status: response.status(),
      url: response.url()
    });
  });
  return audit;
}

function auditIssues(audit) {
  const issues = [];
  for (const request of audit.requests) {
    if (request.url.startsWith("data:") || request.url.startsWith("blob:")) {
      continue;
    }
    const host = new URL(request.url).hostname;
    if (!CONFIG.allowed_hosts.includes(host)) {
      issues.push(`unauthorized network request: ${request.method} ${request.url}`);
    }
  }
  issues.push(...audit.page_errors.map((item) => `pageerror: ${item}`));
  issues.push(...audit.console_errors.map((item) => `console.error: ${item}`));
  issues.push(...audit.request_failures.map((item) =>
    `requestfailed: ${item.method} ${item.url} (${item.error})`
  ));
  return issues;
}

async function writeAudit(name, audit) {
  const issues = auditIssues(audit);
  const value = {
    ...audit,
    result: issues.length ? "failed" : "passed",
    issues
  };
  fs.writeFileSync(
    path.join(AUDIT_ROOT, `${name}.json`),
    `${JSON.stringify(value, null, 2)}\n`,
    "utf8"
  );
  return issues;
}

function acceptanceCase(name, size, run) {
  test(name, { concurrency: false }, async () => {
    const context = await browser.newContext({
      viewport: size,
      locale: "zh-CN",
      colorScheme: "light",
      reducedMotion: "reduce"
    });
    await context.tracing.start({
      screenshots: true,
      snapshots: true,
      sources: true
    });
    const page = await context.newPage();
    const audit = attachAudit(page);
    let failed = false;
    try {
      await run({ page, context, audit });
      const issues = await writeAudit(name, audit);
      assert.deepEqual(issues, [], "Console/network audit must remain clean.");
      await context.tracing.stop();
    } catch (error) {
      failed = true;
      await page.screenshot({
        path: path.join(SCREENSHOT_ROOT, `${name}-failure.png`),
        fullPage: true
      }).catch(() => {});
      await context.tracing.stop({
        path: path.join(TRACE_ROOT, `${name}.zip`)
      }).catch(() => {});
      await writeAudit(name, audit);
      throw error;
    } finally {
      if (!failed) {
        const staleTrace = path.join(TRACE_ROOT, `${name}.zip`);
        const staleFailureScreenshot = path.join(
          SCREENSHOT_ROOT,
          `${name}-failure.png`
        );
        if (fs.existsSync(staleTrace)) {
          fs.rmSync(staleTrace);
        }
        if (fs.existsSync(staleFailureScreenshot)) {
          fs.rmSync(staleFailureScreenshot);
        }
      }
      await context.close();
    }
  });
}

async function openStudio(page) {
  await page.goto(BASE_URL, { waitUntil: "domcontentloaded" });
  await page.getByRole("region", {
    name: "CAM Flow Studio fixture 工作台"
  }).waitFor({ state: "visible" });
}

async function screenshot(page, name) {
  await page.screenshot({
    path: path.join(SCREENSHOT_ROOT, `${name}.png`),
    fullPage: false
  });
}

async function layoutReport(page) {
  return page.evaluate(() => {
    const selectors = {
      host: "#flowView",
      app: ".flow-app",
      topbar: ".flow-topbar",
      targetbar: ".flow-targetbar",
      workspace: ".flow-workspace",
      library: ".flow-library",
      canvas: ".flow-canvas-panel",
      inspector: ".flow-inspector",
      bottom: ".flow-bottom",
      safety: ".flow-safety-strip"
    };
    const rects = {};
    const issues = [];
    const rect = (element) => {
      const value = element.getBoundingClientRect();
      return {
        x: value.x,
        y: value.y,
        right: value.right,
        bottom: value.bottom,
        width: value.width,
        height: value.height
      };
    };
    for (const [name, selector] of Object.entries(selectors)) {
      const element = document.querySelector(selector);
      if (!element) {
        issues.push(`${name} is missing`);
        continue;
      }
      rects[name] = rect(element);
    }
    if (document.documentElement.scrollWidth > window.innerWidth + 1) {
      issues.push(
        `document horizontal overflow ${document.documentElement.scrollWidth} > ${window.innerWidth}`
      );
    }
    if (rects.host) {
      for (const name of ["topbar", "targetbar", "workspace", "bottom"]) {
        const item = rects[name];
        if (!item) {
          continue;
        }
        if (item.x < rects.host.x - 1 || item.right > rects.host.right + 1) {
          issues.push(`${name} escapes host horizontally`);
        }
        if (item.y < rects.host.y - 1 || item.bottom > rects.host.bottom + 1) {
          issues.push(
            `${name} escapes host vertically (${item.y.toFixed(1)}..${item.bottom.toFixed(1)} vs ${rects.host.y.toFixed(1)}..${rects.host.bottom.toFixed(1)})`
          );
        }
      }
    }
    for (const name of ["library", "canvas", "inspector"]) {
      const item = rects[name];
      if (item && (item.width < 40 || item.height < 40)) {
        issues.push(`${name} collapsed to ${item.width.toFixed(1)}x${item.height.toFixed(1)}`);
      }
    }
    const pairs = [
      ["topbar", "targetbar"],
      ["targetbar", "workspace"],
      ["workspace", "bottom"],
      ["library", "bottom"],
      ["canvas", "bottom"],
      ["inspector", "bottom"]
    ];
    for (const [leftName, rightName] of pairs) {
      const left = rects[leftName];
      const right = rects[rightName];
      if (!left || !right) {
        continue;
      }
      const overlapWidth = Math.max(
        0,
        Math.min(left.right, right.right) - Math.max(left.x, right.x)
      );
      const overlapHeight = Math.max(
        0,
        Math.min(left.bottom, right.bottom) - Math.max(left.y, right.y)
      );
      if (overlapWidth * overlapHeight > 1) {
        issues.push(
          `${leftName}/${rightName} overlap ${overlapWidth.toFixed(1)}x${overlapHeight.toFixed(1)}`
        );
      }
    }
    for (const selector of [".flow-topbar", ".flow-targetbar", ".flow-canvas-toolbar"]) {
      const container = document.querySelector(selector);
      if (!container) {
        continue;
      }
      const children = Array.from(container.children)
        .filter((element) => {
          const style = getComputedStyle(element);
          const value = element.getBoundingClientRect();
          return style.display !== "none" && style.visibility !== "hidden"
            && value.width > 1 && value.height > 1;
        })
        .map((element) => ({
          label: element.getAttribute("aria-label")
            || element.textContent.trim().replace(/\s+/g, " ").slice(0, 48)
            || element.tagName,
          rect: rect(element)
        }));
      for (let index = 0; index < children.length; index += 1) {
        for (let other = index + 1; other < children.length; other += 1) {
          const left = children[index];
          const right = children[other];
          const overlapWidth = Math.max(
            0,
            Math.min(left.rect.right, right.rect.right)
              - Math.max(left.rect.x, right.rect.x)
          );
          const overlapHeight = Math.max(
            0,
            Math.min(left.rect.bottom, right.rect.bottom)
              - Math.max(left.rect.y, right.rect.y)
          );
          if (overlapWidth * overlapHeight > 1) {
            issues.push(
              `${selector} children overlap: ${left.label} / ${right.label}`
            );
          }
        }
      }
    }
    return {
      viewport: { width: innerWidth, height: innerHeight },
      document: {
        scrollWidth: document.documentElement.scrollWidth,
        scrollHeight: document.documentElement.scrollHeight
      },
      rects,
      issues
    };
  });
}

async function assertAccessibleControls(page) {
  const failures = await page.locator(
    ".flow-app button:visible, .flow-app input:visible, .flow-app select:visible"
  ).evaluateAll((controls) => controls.flatMap((control) => {
    const label = control.getAttribute("aria-label")
      || control.labels?.[0]?.textContent?.trim()
      || control.textContent?.trim()
      || control.getAttribute("title")
      || control.getAttribute("placeholder");
    return label ? [] : [`${control.tagName.toLowerCase()} has no accessible name`];
  }));
  assert.deepEqual(failures, [], "Every visible Flow control needs an accessible name.");

  const iconFailures = await page.locator(
    ".flow-app .flow-icon-button:visible"
  ).evaluateAll((buttons) => buttons.flatMap((button) => {
    const missing = [];
    if (!button.getAttribute("aria-label")) {
      missing.push("missing aria-label");
    }
    if (!button.getAttribute("title")) {
      missing.push("missing tooltip title");
    }
    return missing.length
      ? [`${button.outerHTML.slice(0, 100)}: ${missing.join(", ")}`]
      : [];
  }));
  assert.deepEqual(iconFailures, [], "Icon buttons need aria labels and tooltips.");
}

async function assertSafetyStripReachable(page, name) {
  const safetyStrip = page.locator(".flow-safety-strip");
  await safetyStrip.scrollIntoViewIfNeeded();
  const report = {
    strip: await safetyStrip.evaluate((element) => {
      const rect = element.getBoundingClientRect();
      const point = {
        x: Math.max(0, Math.min(innerWidth - 1, rect.left + rect.width / 2)),
        y: Math.max(0, Math.min(innerHeight - 1, rect.top + rect.height / 2))
      };
      const target = document.elementFromPoint(point.x, point.y);
      return {
        rect: {
          left: rect.left,
          top: rect.top,
          right: rect.right,
          bottom: rect.bottom,
          width: rect.width,
          height: rect.height
        },
        viewport: { width: innerWidth, height: innerHeight },
        fully_in_viewport: rect.left >= -1
          && rect.top >= -1
          && rect.right <= innerWidth + 1
          && rect.bottom <= innerHeight + 1,
        hit_target: Boolean(target && (target === element || element.contains(target)))
      };
    }),
    fields: []
  };

  for (const expected of CONFIG.required_zero_execution_fields) {
    const field = safetyStrip.getByText(expected, { exact: true });
    await field.scrollIntoViewIfNeeded();
    report.fields.push(await field.evaluate((element, label) => {
      const rect = element.getBoundingClientRect();
      const point = {
        x: Math.max(0, Math.min(innerWidth - 1, rect.left + rect.width / 2)),
        y: Math.max(0, Math.min(innerHeight - 1, rect.top + rect.height / 2))
      };
      const target = document.elementFromPoint(point.x, point.y);
      return {
        label,
        rect: {
          left: rect.left,
          top: rect.top,
          right: rect.right,
          bottom: rect.bottom,
          width: rect.width,
          height: rect.height
        },
        fully_in_viewport: rect.left >= -1
          && rect.top >= -1
          && rect.right <= innerWidth + 1
          && rect.bottom <= innerHeight + 1,
        hit_target: Boolean(target && (target === element || element.contains(target)))
      };
    }, expected));
  }

  fs.writeFileSync(
    path.join(AUDIT_ROOT, `${name}-safety-strip.json`),
    `${JSON.stringify(report, null, 2)}\n`,
    "utf8"
  );
  await screenshot(page, `${name}-safety-strip`);
  assert.equal(
    report.strip.fully_in_viewport,
    true,
    `${name} host safety strip must be fully inside the viewport after scrolling.`
  );
  assert.equal(
    report.strip.hit_target,
    true,
    `${name} host safety strip must not be obscured.`
  );
  for (const field of report.fields) {
    assert.equal(
      field.fully_in_viewport,
      true,
      `${name} safety field ${field.label} must be reachable without clipping.`
    );
    assert.equal(
      field.hit_target,
      true,
      `${name} safety field ${field.label} must not be obscured.`
    );
  }
}

async function stableRects(page, selectors) {
  return page.evaluate((items) => Object.fromEntries(items.map((selector) => {
    const value = document.querySelector(selector).getBoundingClientRect();
    return [selector, {
      x: value.x,
      y: value.y,
      width: value.width,
      height: value.height
    }];
  })), selectors);
}

function assertRectsStable(before, after, tolerance = 1) {
  for (const selector of Object.keys(before)) {
    for (const field of ["x", "y", "width", "height"]) {
      assert.ok(
        Math.abs(before[selector][field] - after[selector][field]) <= tolerance,
        `${selector} ${field} shifted from ${before[selector][field]} to ${after[selector][field]}.`
      );
    }
  }
}

acceptanceCase(
  "desktop-1440x900-first-workspace",
  viewport("desktop-1440x900"),
  async ({ page }) => {
    await pluginRequest("powermill-cam-copilot", "uninstall");
    await page.goto(BASE_URL, { waitUntil: "domcontentloaded" });
    const flowRegion = page.getByRole("region", { name: "CAM Flow Studio" });
    await flowRegion.waitFor({ state: "visible" });
    await page.getByText("流程工作台需要显式安装产品插件").waitFor({
      state: "visible"
    });
    assert.equal(
      await page.locator("#flowView").getAttribute("hidden"),
      null,
      "The Flow workspace must be the first visible product view."
    );
    assert.equal(
      await page.locator("main .hero, main [data-view-panel='marketing']").count(),
      0,
      "The first screen must not be a marketing page."
    );

    await ensureFlowPluginInstalled();
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.getByRole("region", {
      name: "CAM Flow Studio fixture 工作台"
    }).waitFor({ state: "visible" });
    const studio = page.locator(".flow-app");
    await page.getByRole("button", {
      name: /ROUGHING_FIXTURE\.mac/
    }).waitFor({ state: "visible" });
    assert.equal(
      await studio.locator('[data-target="product"]').inputValue(),
      "",
      "Multiple targets must not be auto-selected."
    );
    assert.equal(
      await studio.locator('[data-target="target_version"]').inputValue(),
      ""
    );
    assert.equal(
      await studio.locator('[data-target="target_instance_id"]').inputValue(),
      ""
    );
    assert.equal(
      await studio.locator('[data-target="project_id"]').inputValue(),
      ""
    );
    assert.equal(
      await page.getByRole("button", { name: "生成 fixture 预览" }).isDisabled(),
      true
    );

    await screenshot(page, "desktop-1440x900-first-workspace");
    const report = await layoutReport(page);
    fs.writeFileSync(
      path.join(AUDIT_ROOT, "desktop-1440x900-layout.json"),
      `${JSON.stringify(report, null, 2)}\n`,
      "utf8"
    );
    assert.deepEqual(report.issues, [], "1440x900 layout must not clip or overlap.");
    await assertSafetyStripReachable(page, "desktop-1440x900");
  }
);

acceptanceCase(
  "desktop-1440x900-workflow",
  viewport("desktop-1440x900"),
  async ({ page }) => {
    await ensureFlowPluginInstalled();
    await openStudio(page);
    const studio = page.locator(".flow-app");

    const safetyStrip = page.locator(".flow-safety-strip");
    for (const expected of CONFIG.required_zero_execution_fields) {
      await assert.doesNotReject(
        () => safetyStrip.getByText(expected, { exact: true }).waitFor({
          state: "visible"
        }),
        `${expected} must be visible before preview.`
      );
    }
    for (const forbidden of CONFIG.forbidden_text) {
      assert.equal(
        await page.getByText(forbidden, { exact: true }).count(),
        0,
        `Unsafe command copy must not appear: ${forbidden}`
      );
    }

    await assertAccessibleControls(page);
    const tooltip = page.getByRole("button", { name: "缩小画布" });
    assert.equal(await tooltip.getAttribute("title"), "缩小画布");
    await tooltip.focus();
    const focusStyle = await tooltip.evaluate((element) => {
      const style = getComputedStyle(element);
      return {
        focusVisible: element.matches(":focus-visible"),
        outlineStyle: style.outlineStyle,
        outlineWidth: style.outlineWidth
      };
    });
    assert.equal(focusStyle.focusVisible, true, "Keyboard focus must be visible.");
    assert.notEqual(focusStyle.outlineStyle, "none");
    assert.notEqual(focusStyle.outlineWidth, "0px");

    const permissionModule = page.getByRole("article", {
      name: /商业外挂不透明步骤/
    });
    await permissionModule.getByText("权限缺失", { exact: true }).waitFor({
      state: "visible"
    });
    assert.equal(await permissionModule.locator("svg").count(), 1);

    await page.getByRole("button", {
      name: /CAPABILITY_MISSING.*mapping:opaque/
    }).click();
    await page.getByText("不透明 / unsupported / 只读", {
      exact: true
    }).waitFor({ state: "visible" });
    await page.locator('[data-source-line="5"].is-active').waitFor({
      state: "visible"
    });
    await page.getByText("能力不可用", { exact: true }).waitFor({
      state: "visible"
    });

    await page.getByRole("button", {
      name: /区域清除参数.*方向键可移动/
    }).click();
    await page.getByRole("button", { name: "定位当前步骤到源码" }).click();
    await page.locator('[data-source-line="4"].is-active').waitFor({
      state: "visible"
    });
    await page.locator('[data-source-line="4"]').click();
    await page.getByText("SOURCE_MAPPING_AMBIGUOUS", {
      exact: true
    }).waitFor({ state: "visible" });
    assert.equal(
      await page.locator(".flow-candidate-list button").count(),
      2,
      "Ambiguous source mapping must list candidates instead of guessing."
    );
    await page.locator('[data-source-line="2"]').click();
    await page.getByRole("complementary", {
      name: "Schema inspector"
    }).getByText("node:stock", { exact: true }).waitFor({ state: "visible" });

    const toleranceNode = page.locator(
      '.flow-node[data-node-id="node:tolerance"]'
    );
    await toleranceNode.click();
    const valueInput = page.getByLabel("数值");
    assert.equal(await valueInput.inputValue(), "0.05");
    await valueInput.fill("0.075");
    await valueInput.press("Tab");
    assert.equal(
      await page.getByRole("button", { name: "撤销 GraphCommand" }).isEnabled(),
      true
    );
    const canvas = page.getByRole("application", { name: /FlowGraph 画布/ });
    await canvas.focus();
    await page.keyboard.press("Control+z");
    assert.equal(await page.getByLabel("数值").inputValue(), "0.05");
    await canvas.focus();
    await page.keyboard.press("Control+y");
    assert.equal(await page.getByLabel("数值").inputValue(), "0.075");

    const reviewModule = page.getByRole("article", {
      name: /人工审阅点/
    });
    await reviewModule.focus();
    await reviewModule.press("Enter");
    const addedNode = page.locator(
      '.flow-node[data-node-id="node:ui:cam-fixture-review_point:02"]'
    );
    await addedNode.waitFor({ state: "visible" });
    await page.getByRole("button", {
      name: "node:roughing next，output，cam.control"
    }).press("Enter");
    await page.getByRole("button", {
      name: "node:ui:cam-fixture-review_point:02 previous，input，cam.control"
    }).press("Enter");
    await page.getByText(/7 节点 · 6 关系/).waitFor({ state: "visible" });

    await page.getByRole("button", { name: "全部关系" }).click();
    await page.getByLabel("新关系").selectOption("data");
    await page.getByRole("button", {
      name: "node:tolerance value，output，cam.length"
    }).press("Enter");
    await page.getByRole("button", {
      name: "node:ui:cam-fixture-review_point:02 previous，input，cam.control"
    }).press("Enter");
    await page.getByText(/PORT_KIND_MISMATCH/).waitFor({ state: "visible" });
    await page.getByText(/7 节点 · 6 关系/).waitFor({ state: "visible" });

    await page.getByRole("button", { name: "记录版本" }).click();
    await page.locator(".flow-version-row").filter({
      hasText: "本地 draft 03"
    }).waitFor({ state: "visible" });
    await page.getByRole("tab", { name: "图 Diff" }).click();
    await page.getByText("Graph Diff", { exact: true }).waitFor({
      state: "visible"
    });
    await page.getByText("layout_excluded=true", { exact: true }).waitFor({
      state: "visible"
    });
    await page.getByRole("tab", { name: "源码 Diff" }).click();
    await page.getByText("Source Diff", { exact: true }).waitFor({
      state: "visible"
    });
    await page.getByText("opaque preserved", { exact: true }).waitFor({
      state: "visible"
    });
    await page.getByRole("tab", { name: "Test" }).click();
    await page.getByRole("button", { name: /检查当前图/ }).click();
    await page.getByText("Schema 与 typed ports", { exact: true }).waitFor({
      state: "visible"
    });
    await page.getByText("network egress", { exact: true }).waitFor({
      state: "visible"
    });

    const product = studio.locator('[data-target="product"]');
    const version = studio.locator('[data-target="target_version"]');
    const instance = studio.locator('[data-target="target_instance_id"]');
    const project = studio.locator('[data-target="project_id"]');
    assert.equal(await instance.inputValue(), "");
    await product.selectOption(CONFIG.fixture_target.product);
    assert.equal(await version.inputValue(), "");
    assert.equal(await instance.inputValue(), "");
    await version.selectOption(CONFIG.fixture_target.target_version);
    assert.equal(
      await instance.inputValue(),
      "",
      "A single filtered foreground/instance result still must not be auto-selected."
    );
    await instance.selectOption(CONFIG.fixture_target.target_instance_id);
    assert.equal(await project.inputValue(), "");
    await project.selectOption(CONFIG.fixture_target.project_id);
    await page.getByRole("button", { name: "生成 fixture 预览" }).click();
    await page.getByText("PreviewPlan", { exact: true }).waitFor({
      state: "visible"
    });
    const preview = page.locator(".flow-bottom-content");
    for (const expected of CONFIG.required_zero_execution_fields) {
      await preview.getByText(expected, { exact: true }).waitFor({
        state: "visible"
      });
    }
    for (const expected of [
      CONFIG.fixture_target.product,
      CONFIG.fixture_target.target_version,
      CONFIG.fixture_target.target_instance_id,
      CONFIG.fixture_target.project_id
    ]) {
      await preview.getByText(expected, { exact: true }).waitFor({
        state: "visible"
      });
    }
    await page.waitForTimeout(750);
    await screenshot(page, "desktop-1440x900-preview");
    await page.getByRole("tab", { name: "门禁" }).click();
    for (const gate of ["cam_simulation", "collision_check", "shop_approval"]) {
      const gateRow = page.locator(".flow-gate-list > div").filter({
        hasText: gate
      });
      await gateRow.waitFor({ state: "visible" });
      assert.equal(await gateRow.locator("svg").count(), 1);
      assert.match(await gateRow.innerText(), /未完成|必需/);
    }

    await screenshot(page, "desktop-1440x900-workflow");
  }
);

for (const name of ["desktop-1280x720", "mobile-390x844"]) {
  acceptanceCase(name, viewport(name), async ({ page }) => {
    await ensureFlowPluginInstalled();
    await openStudio(page);
    const title = page.locator(".flow-targetbar__title small");
    await title.evaluate((element, value) => {
      element.textContent = value;
    }, CONFIG.long_chinese_probe);
    await screenshot(page, name);
    const report = await layoutReport(page);
    fs.writeFileSync(
      path.join(AUDIT_ROOT, `${name}-layout.json`),
      `${JSON.stringify(report, null, 2)}\n`,
      "utf8"
    );
    assert.deepEqual(
      report.issues,
      [],
      `${name} layout must remain contained, non-overlapping, and non-collapsed.`
    );
    await assertAccessibleControls(page);
    await assertSafetyStripReachable(page, name);
  });
}

acceptanceCase(
  "desktop-1440x900-canvas-render-and-500",
  viewport("desktop-1440x900"),
  async ({ page }) => {
    await ensureFlowPluginInstalled();
    await openStudio(page);
    const canvas = page.locator(".flow-canvas");
    const canvasPng = await canvas.screenshot();
    fs.writeFileSync(
      path.join(SCREENSHOT_ROOT, "desktop-1440x900-canvas.png"),
      canvasPng
    );
    const { data, info } = await sharp(canvasPng)
      .removeAlpha()
      .raw()
      .toBuffer({ resolveWithObject: true });
    const colors = new Set();
    const stride = Math.max(3, Math.floor((info.width * info.height) / 20000) * 3);
    for (let offset = 0; offset + 2 < data.length; offset += stride) {
      colors.add(`${data[offset]},${data[offset + 1]},${data[offset + 2]}`);
      if (colors.size > 48) {
        break;
      }
    }
    assert.ok(colors.size > 16, `Canvas must be nonblank; sampled ${colors.size} colors.`);

    const selectors = [
      ".flow-canvas-panel",
      ".flow-inspector",
      ".flow-bottom"
    ];
    const beforeZoom = await stableRects(page, selectors);
    await page.getByRole("button", { name: "放大画布" }).click();
    assert.equal(await page.locator('[data-region="zoom"]').innerText(), "98%");
    await page.getByRole("button", { name: "缩小画布" }).click();
    assert.equal(await page.locator('[data-region="zoom"]').innerText(), "88%");
    const afterZoom = await stableRects(page, selectors);
    assertRectsStable(beforeZoom, afterZoom);

    const beforeHook = await stableRects(page, selectors);
    await page.getByRole("button", { name: "500 节点 hook" }).click();
    await page.getByText(/500 hook · visible \d+\/500 ·/).waitFor({
      state: "visible"
    });
    assertRectsStable(beforeHook, await stableRects(page, selectors));
    await screenshot(page, "desktop-1440x900-canvas-after-500-hook");
  }
);

acceptanceCase(
  "desktop-1440x900-canvas-pointer-drag",
  viewport("desktop-1440x900"),
  async ({ page }) => {
    await ensureFlowPluginInstalled();
    await openStudio(page);
    const selectors = [
      ".flow-canvas-panel",
      ".flow-inspector",
      ".flow-bottom"
    ];
    const stableBeforeDrag = await stableRects(page, selectors);
    const node = page.locator('.flow-node[data-node-id="node:roughing"]');
    const handle = node.locator("[data-drag-handle]");
    await handle.evaluate(() => {
      window.__camFlowPointerEvents = [];
      for (const type of ["pointerdown", "pointermove", "pointerup"]) {
        document.addEventListener(type, (event) => {
          window.__camFlowPointerEvents.push({
            type: event.type,
            is_trusted: event.isTrusted,
            pointer_type: event.pointerType,
            button: event.button,
            buttons: event.buttons
          });
        }, { capture: true });
      }
    });
    const beforeTransform = await node.evaluate((element) => element.style.transform);
    const box = await handle.boundingBox();
    assert.ok(box, "Roughing node drag handle must be visible.");
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(
      box.x + box.width / 2 + 48,
      box.y + box.height / 2 + 24,
      { steps: 4 }
    );
    await page.mouse.up();
    const afterTransform = await node.evaluate((element) => element.style.transform);
    const pointerEvents = await page.evaluate(() => window.__camFlowPointerEvents);
    assert.notEqual(afterTransform, beforeTransform, "Pointer drag must move the node.");
    for (const type of ["pointerdown", "pointermove", "pointerup"]) {
      assert.ok(
        pointerEvents.some((event) => event.type === type && event.is_trusted),
        `Real browser drag must emit a trusted ${type}.`
      );
    }
    assertRectsStable(stableBeforeDrag, await stableRects(page, selectors));
    await page.getByRole("button", { name: "撤销 GraphCommand" }).click();
    const undoTransform = await node.evaluate((element) => element.style.transform);
    fs.writeFileSync(
      path.join(AUDIT_ROOT, "desktop-1440x900-canvas-pointer-drag-interaction.json"),
      `${JSON.stringify({
        before_transform: beforeTransform,
        after_transform: afterTransform,
        undo_transform: undoTransform,
        moved: afterTransform !== beforeTransform,
        undo_restored: undoTransform === beforeTransform,
        pointer_events: pointerEvents
      }, null, 2)}\n`,
      "utf8"
    );
    assert.equal(
      undoTransform,
      beforeTransform,
      "Undo must restore the node position."
    );
    await screenshot(page, "desktop-1440x900-canvas-pointer-drag");
  }
);
