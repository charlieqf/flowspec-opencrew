#!/usr/bin/env node
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const PLAYWRIGHT_FALLBACK = "/private/tmp/opencrew-playwright-runner/node_modules/playwright";
const BASE_URL = (process.env.OPENCREW_E2E_FRONTEND_URL || "http://127.0.0.1:18080").replace(/\/$/, "");
const CDP_URL = process.env.OPENCREW_E2E_CDP_URL || "http://127.0.0.1:9224";
const APP_PASSWORD = process.env.OPENCREW_E2E_APP_PASSWORD || "";
const ALLOW_SETUP = process.env.OPENCREW_E2E_ALLOW_SETUP === "1";
const HEADLESS = process.env.OPENCREW_E2E_HEADLESS !== "0";
const RUN_ID = process.env.OPENCREW_E2E_RUN_ID || new Date().toISOString().replace(/[-:TZ.]/g, "").slice(0, 14);
const APP_URL = `${BASE_URL}/?e2eModelConfigTts=${RUN_ID}`;
const SCREENSHOT = process.env.OPENCREW_E2E_MODEL_CONFIG_TTS_SCREENSHOT || `/private/tmp/opencrew_modelconfig_tts_scenario_lab_${RUN_ID}.png`;
const FAILURE_SCREENSHOT = process.env.OPENCREW_E2E_MODEL_CONFIG_TTS_FAILURE_SCREENSHOT || `/private/tmp/opencrew_modelconfig_tts_failure_${RUN_ID}.png`;

function loadPlaywright() {
  for (const id of ["playwright", PLAYWRIGHT_FALLBACK]) {
    try {
      return require(id);
    } catch {
      // Try the next location.
    }
  }
  throw new Error("Playwright is not installed. Run `npm --prefix frontend install playwright` or provide the fallback runner.");
}

async function endpointAvailable(url) {
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(1200) });
    return response.ok;
  } catch {
    return false;
  }
}

async function openBrowser(chromium) {
  const cdpReady = !APP_PASSWORD && (await endpointAvailable(`${CDP_URL}/json/version`));
  if (cdpReady) {
    const browser = await chromium.connectOverCDP(CDP_URL);
    const context = browser.contexts()[0] || await browser.newContext();
    const page = context.pages().find((item) => !item.isClosed() && item.url().startsWith(BASE_URL)) || await context.newPage();
    return { browser, context, page, ownsBrowser: false, mode: "cdp" };
  }
  const browser = await chromium.launch({ headless: HEADLESS });
  const context = await browser.newContext({ viewport: { width: 1440, height: 980 }, deviceScaleFactor: 1 });
  const page = await context.newPage();
  return { browser, context, page, ownsBrowser: true, mode: "launch" };
}

async function ensureAuthenticated(page) {
  await page.waitForLoadState("domcontentloaded", { timeout: 30000 }).catch(() => {});
  await page.waitForTimeout(400);
  const authGateVisible = await page.locator(".auth-gate").isVisible().catch(() => false);
  if (!authGateVisible) return;
  const heading = (await page.locator(".auth-gate h1").first().innerText().catch(() => "")).trim();
  if (!APP_PASSWORD) {
    throw new Error(`Authentication is required (${heading || "auth gate"}). Set OPENCREW_E2E_APP_PASSWORD or use an authenticated CDP browser.`);
  }
  if (/Create admin password/i.test(heading) && !ALLOW_SETUP) {
    throw new Error("Refusing to create an admin password during e2e. Set OPENCREW_E2E_ALLOW_SETUP=1 to allow setup.");
  }
  await page.locator(".auth-gate input[type='password']").fill(APP_PASSWORD);
  await page.locator(".auth-gate button").filter({ hasText: /Sign in|Create password/ }).click();
  await page.locator(".auth-gate").waitFor({ state: "hidden", timeout: 15000 });
}

async function navigate(page, mode) {
  if (mode === "cdp" && !page.url().startsWith("chrome://") && page.url() !== "about:blank") {
    await page.evaluate((url) => {
      window.location.href = url;
    }, APP_URL).catch(() => {});
    await page.waitForURL(new RegExp(`^${BASE_URL.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}/`), { timeout: 30000 }).catch(() => {});
  } else {
    await page.goto(APP_URL, { waitUntil: "domcontentloaded", timeout: 30000 });
  }
  await ensureAuthenticated(page);
  const connectionNav = page.locator('.nav .nav-item[title="Connection"]').first();
  await connectionNav.waitFor({ state: "visible", timeout: 20000 });
  await connectionNav.click();
  await page.getByRole("heading", { name: "Connection", exact: true }).waitFor({ state: "visible", timeout: 20000 });
  await page.getByRole("button", { name: "TTS Model Settings" }).waitFor({ state: "visible", timeout: 20000 });
}

async function assertScenarioOptions(select) {
  const options = await select.locator("option").evaluateAll((items) =>
    items.map((item) => ({ value: item.value, label: item.textContent.trim() })),
  );
  assert.equal(options.length, 14, `Expected 14 shared Google TTS scenarios, got ${options.length}`);
  assert.ok(options.some((item) => item.value === "single-basic" && item.label === "单说话人基础朗读"), "Missing shared single-basic scenario");
  assert.ok(options.some((item) => item.value === "multi-dialogue" && item.label === "多说话人对话"), "Missing multi-dialogue scenario");
  assert.ok(options.some((item) => item.value === "classifier-safe" && item.label === "提示分类器规避"), "Missing classifier-safe scenario");
  assert.ok(!options.some((item) => item.value === "single_basic_reading"), "Found legacy underscore scenario id");
  return options;
}

async function main() {
  const { chromium } = loadPlaywright();
  const session = await openBrowser(chromium);
  const { browser, page, ownsBrowser, mode } = session;
  const browserErrors = [];
  page.on("pageerror", (error) => browserErrors.push(error.message));
  page.on("console", (message) => {
    if (message.type() !== "error") return;
    const text = message.text();
    if (/Failed to load resource:.*404/.test(text)) return;
    browserErrors.push(text);
  });

  try {
    await navigate(page, mode);
    await page.getByRole("button", { name: "TTS Model Settings" }).click();

    const modal = page.locator(".media-config-dialog").filter({ hasText: "TTS Model Settings" });
    await modal.waitFor({ state: "visible", timeout: 20000 });
    await modal.locator(".media-provider-card").first().waitFor({ state: "visible", timeout: 20000 });

    const googleCard = modal.locator(".media-provider-card").filter({ hasText: /Google|Gemini/i }).first();
    await googleCard.waitFor({ state: "visible", timeout: 10000 });
    await googleCard.getByRole("button", { name: "Voice Guide" }).click();

    const guide = page.locator(".tts-guide-dialog").filter({ hasText: /Google|Gemini/i });
    await guide.waitFor({ state: "visible", timeout: 10000 });
    const scenarioSelect = guide.locator(".tts-scenario-row select").first();
    const options = await assertScenarioOptions(scenarioSelect);

    await scenarioSelect.selectOption("classifier-safe");
    await guide.getByRole("button", { name: "Scenario information" }).click();
    const infoDialog = page.locator(".tts-info-dialog");
    await infoDialog.waitFor({ state: "visible", timeout: 10000 });
    await infoDialog.getByText("提示分类器规避", { exact: true }).waitFor({ state: "visible", timeout: 10000 });
    await infoDialog.getByText(/降低提示误分类|清晰转写边界/).first().waitFor({ state: "visible", timeout: 10000 });
    await infoDialog.getByRole("button", { name: "Close" }).click();
    await infoDialog.waitFor({ state: "hidden", timeout: 10000 });

    await scenarioSelect.selectOption("multi-dialogue");
    const multiSpeakerControls = guide.locator(".tts-guide-controls.multi-speaker");
    await multiSpeakerControls.waitFor({ state: "visible", timeout: 10000 });
    await multiSpeakerControls.locator("label").filter({ hasText: "小林" }).waitFor({ state: "visible", timeout: 10000 });
    await multiSpeakerControls.locator("label").filter({ hasText: "小周" }).waitFor({ state: "visible", timeout: 10000 });
    await guide.getByRole("button", { name: "Generate Complex Prompt" }).click();
    const complexPrompt = await guide.locator("textarea.tts-complex-prompt").inputValue();
    assert.match(complexPrompt, /MULTI-SPEAKER TTS SCENE/);
    assert.match(complexPrompt, /小林/);
    assert.match(complexPrompt, /小周/);

    await page.screenshot({ path: SCREENSHOT, fullPage: true });
    assert.deepEqual(browserErrors, []);
    console.log(JSON.stringify({ ok: true, scenarioCount: options.length, screenshot: SCREENSHOT }, null, 2));
  } catch (error) {
    await page.screenshot({ path: FAILURE_SCREENSHOT, fullPage: true }).catch(() => {});
    const state = await page.evaluate(() => ({
      url: window.location.href,
      title: document.title,
      body: document.body.innerText.slice(0, 4000),
      authGate: Boolean(document.querySelector(".auth-gate")),
      activeModal: document.querySelector(".media-config-dialog")?.textContent?.slice(0, 1000) || "",
      guide: document.querySelector(".tts-guide-dialog")?.textContent?.slice(0, 1000) || "",
    })).catch((err) => ({ evaluate_error: String(err) }));
    throw new Error(`ModelConfig TTS Scenario Lab check failed. Screenshot: ${FAILURE_SCREENSHOT}. State: ${JSON.stringify(state, null, 2)}. Original: ${error instanceof Error ? error.message : String(error)}`);
  } finally {
    if (ownsBrowser) await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
