#!/usr/bin/env node
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { loadPlaywright } from "./playwright-loader.mjs";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const FRONTEND_ROOT = resolve(SCRIPT_DIR, "..");
const REPO_ROOT = resolve(FRONTEND_ROOT, "..");
const BASE_URL = (process.env.OPENCREW_E2E_FRONTEND_URL || "http://127.0.0.1:18080").replace(/\/$/, "");
const RUN_ID = process.env.OPENCREW_E2E_RUN_ID || `${Date.now()}`;
const HEADLESS = process.env.OPENCREW_E2E_HEADLESS !== "0";

function readEnvFile(path) {
  const env = {};
  if (!existsSync(path)) return env;
  const text = readFileSync(path, "utf-8");
  for (const line of text.split(/\r?\n/)) {
    const match = line.match(/^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)\s*$/);
    if (!match) continue;
    let value = match[2].trim();
    if ((value.startsWith("\"") && value.endsWith("\"")) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    env[match[1]] = value;
  }
  return env;
}

function exactText(text) {
  return new RegExp(`^${String(text).replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`);
}

function appUrl(hash = "#/analysis-v1/tasks") {
  return `${BASE_URL}/?shellNavSmoke=${RUN_ID}${hash}`;
}

async function waitForHash(page, prefix) {
  await page.waitForFunction((expected) => window.location.hash.startsWith(expected), prefix, { timeout: 12000 });
}

async function clickNav(page, title, expectedHash) {
  const button = page.locator(".nav .nav-item").filter({ hasText: exactText(title) }).first();
  await button.waitFor({ state: "visible", timeout: 12000 });
  await button.click();
  if (expectedHash === "") {
    await page.waitForFunction(() => window.location.hash === "", null, { timeout: 12000 });
  } else {
    await waitForHash(page, expectedHash);
  }
}

async function assertNoPageErrors(pageErrors, label) {
  assert.deepEqual(pageErrors, [], `${label} emitted pageerror(s): ${pageErrors.join(" | ")}`);
}

async function waitForShell(page, label) {
  try {
    await page.locator(".shell").waitFor({ state: "visible", timeout: 15000 });
  } catch (error) {
    const authStatus = await page.evaluate(async () => {
      try {
        return await (await fetch("/api/auth/status", { credentials: "include" })).json();
      } catch (err) {
        return { error: err instanceof Error ? err.message : String(err) };
      }
    }).catch((err) => ({ error: err instanceof Error ? err.message : String(err) }));
    const bodyText = await page.locator("body").innerText().catch((err) => `body read failed: ${err instanceof Error ? err.message : String(err)}`);
    throw new Error(`${label} shell did not appear; authStatus=${JSON.stringify(authStatus)} body=${bodyText.slice(0, 240)}`, { cause: error });
  }
}

async function loginContext(context, password, expectedRole) {
  const response = await context.request.post(`${BASE_URL}/api/auth/login`, {
    data: { password },
  });
  const payload = await response.json().catch(() => ({}));
  assert.equal(response.status(), 200, `login as ${expectedRole} failed: HTTP ${response.status()}`);
  assert.equal(payload.role, expectedRole, `login returned role ${payload.role || ""}, expected ${expectedRole}`);
}

async function adminSmoke(browser, password) {
  const context = await browser.newContext();
  await loginContext(context, password, "admin");
  const page = await context.newPage();
  const pageErrors = [];
  page.on("pageerror", (error) => pageErrors.push(error?.stack || error?.message || String(error)));
  await page.goto(appUrl("#/analysis-v1/tasks"), { waitUntil: "domcontentloaded", timeout: 30000 });
  await waitForShell(page, "admin");

  await clickNav(page, "Connection", "");
  await clickNav(page, "任务列表（口播）", "#/koubo-tasks");
  await clickNav(page, "视频分析（口播）", "#/analysis-v1/tasks");
  await clickNav(page, "故事版（口播）", "#/koubo-storyboard/tasks");
  await clickNav(page, "计费", "#/metering");

  await page.goto(appUrl("#/metering"), { waitUntil: "domcontentloaded", timeout: 30000 });
  await waitForHash(page, "#/metering");
  await page.getByRole("heading", { name: "本地计费" }).waitFor({ state: "visible", timeout: 12000 });
  await assertNoPageErrors(pageErrors, "admin shell nav smoke");
  await context.close();
}

async function meteringReactivitySmoke(browser, password) {
  const context = await browser.newContext();
  await loginContext(context, password, "admin");
  const page = await context.newPage();
  const pageErrors = [];
  const taskRequests = [];
  page.on("pageerror", (error) => pageErrors.push(error?.stack || error?.message || String(error)));
  await page.route("**/api/local-metering/report**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        totals: { request_count: 1, actual_cost_count: 1, actual_cost_micros: 1000, estimated_cost_count: 1, estimated_cost_micros: 1000, charge_micros: 2000, profit_micros: 1000, units: {} },
        by_task: [{ task_id: 987654, title: "Mock metering task", task_status: "completed", session_status: "completed", request_count: 1, provider_cost_micros: 1000, estimated_cost_micros: 1000, charge_micros: 2000, profit_micros: 1000, units: {} }],
        by_provider_model: [],
        by_modality: [],
        pricebook: [],
        items: [],
      }),
    });
  });
  await page.route("**/api/local-metering/tasks/*", async (route) => {
    taskRequests.push(route.request().url());
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        found: true,
        task: { task_id: 987654, title: "Mock metering task", task_status: "completed", session_status: "completed" },
        totals: { request_count: 1, actual_cost_count: 1, provider_cost_micros: 1000, estimated_cost_count: 1, estimated_cost_micros: 1000, charge_micros: 2000, profit_micros: 1000, units: {} },
        by_action: [],
        items: [],
        warnings: [],
        attempts: [{ id: 1, attempt_no: 1, status: "completed" }],
        attempt_scope: { requested: "all", mode: "all" },
      }),
    });
  });
  await page.goto(appUrl("#/metering"), { waitUntil: "domcontentloaded", timeout: 30000 });
  await waitForShell(page, "metering reactivity");
  await page.locator(".metering-task-table-row").first().waitFor({ state: "visible", timeout: 12000 });
  await page.locator(".metering-task-table-row").first().click();
  await page.waitForFunction(() => window.location.hash === "#/metering/task/987654", null, { timeout: 12000 });
  await page.getByText("Task #987654").first().waitFor({ state: "visible", timeout: 12000 });
  assert.match(await page.locator(".metering-task-table-row").first().getAttribute("class"), /is-selected/, "clicked metering task row should stay selected after replaceState");
  assert.ok(taskRequests.some((url) => url.includes("/api/local-metering/tasks/987654")), "selecting a metering task should load its task report");
  await assertNoPageErrors(pageErrors, "metering reactivity smoke");
  await context.close();
}

async function restrictedUserSmoke(browser, password) {
  const context = await browser.newContext();
  await loginContext(context, password, "user");
  const page = await context.newPage();
  const pageErrors = [];
  page.on("pageerror", (error) => pageErrors.push(error?.stack || error?.message || String(error)));
  await page.goto(appUrl("#/metering"), { waitUntil: "domcontentloaded", timeout: 30000 });
  await waitForShell(page, "restricted user");
  await page.waitForFunction(() => !window.location.hash.startsWith("#/metering"), null, { timeout: 12000 });
  assert.equal(await page.locator(".nav .nav-item").filter({ hasText: exactText("计费") }).count(), 0, "restricted user must not see metering nav");
  await assertNoPageErrors(pageErrors, "restricted user shell nav smoke");
  await context.close();
}

const envFile = readEnvFile(resolve(REPO_ROOT, ".opencrew-e2e-auth.env"));
const adminPassword = process.env.OPENCREW_E2E_ADMIN_PASSWORD || envFile.OPENCREW_E2E_ADMIN_PASSWORD || "";
const userPassword = process.env.OPENCREW_E2E_USER_PASSWORD || envFile.OPENCREW_E2E_USER_PASSWORD || "";
assert.ok(adminPassword, "OPENCREW_E2E_ADMIN_PASSWORD is required");
assert.ok(userPassword, "OPENCREW_E2E_USER_PASSWORD is required");

const { chromium } = loadPlaywright();
const browser = await chromium.launch({ headless: HEADLESS });
try {
  await adminSmoke(browser, adminPassword);
  await meteringReactivitySmoke(browser, adminPassword);
  await restrictedUserSmoke(browser, userPassword);
  console.log(`shell-nav-smoke passed against ${BASE_URL}`);
} finally {
  await browser.close();
}
