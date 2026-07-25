#!/usr/bin/env node
import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { chromium } from "playwright";
import {
  baseUrl,
  loginAdmin,
  responseJson,
} from "./media-library-real-helpers.mjs";

const PAID_GATE = "OPENCREW_RUN_PAID_GEMINI_OMNI_BROWSER";
const KEY_GATE = "OPENCREW_GEMINI_OMNI_BROWSER_KEY_AUTHORIZED";
const BUDGET_GATE = "OPENCREW_GEMINI_OMNI_BROWSER_MAX_USD";
const CALLS_GATE = "OPENCREW_GEMINI_OMNI_BROWSER_MAX_CALLS";
const SECONDS_GATE = "OPENCREW_GEMINI_OMNI_BROWSER_MAX_TOTAL_SECONDS";
const EXPECTED_BUDGET_USD = 0.60;
const EXPECTED_CALLS = 2;
const EXPECTED_SECONDS = 6;

function requirePaidGates() {
  assert.equal(process.env[PAID_GATE], "1", `${PAID_GATE}=1 is required`);
  assert.equal(process.env[KEY_GATE], "1", `${KEY_GATE}=1 is required`);
  assert.equal(Number(process.env[BUDGET_GATE]), EXPECTED_BUDGET_USD, `${BUDGET_GATE} must equal 0.60`);
  assert.equal(Number(process.env[CALLS_GATE]), EXPECTED_CALLS, `${CALLS_GATE} must equal 2`);
  assert.equal(Number(process.env[SECONDS_GATE]), EXPECTED_SECONDS, `${SECONDS_GATE} must equal 6`);
}

requirePaidGates();
const url = baseUrl();
const taskId = String(process.env.OPENCREW_E2E_KOUBO_TASK_ID || "305");
const screenshotDir = path.resolve(String(process.env.OPENCREW_E2E_SCREENSHOT_DIR || "."));
const artifactPath = path.resolve(String(process.env.OPENCREW_E2E_ARTIFACT || "gemini-omni-paid-browser.json"));
await mkdir(screenshotDir, { recursive: true });
await assert.rejects(readFile(artifactPath), { code: "ENOENT" }, "refusing to rerun over an existing paid artifact");

const browser = await chromium.launch({ headless: process.env.HEADED !== "1" });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
await loginAdmin(context, url);

const configResponse = await context.request.get(`${url}/api/koubo-storyboard/tasks/${taskId}/asset-library/video-model-config`);
assert.equal(configResponse.status(), 200);
const config = await responseJson(configResponse, "public video model config");
const omniAlias = (config.agent_model_aliases || []).find((item) => item?.capability?.stateful_edit === true);
assert.ok(omniAlias, "Omni Flash must be enabled in the deployed local configuration");
assert.deepEqual(omniAlias.capability.duration, { adjustable: false, min: 3, max: 3, allowed: [3] });

const settingsUrl = `${url}/api/koubo-storyboard/tasks/${taskId}/asset-library/video-api/settings`;
const currentThreadUrl = `${url}/api/koubo-storyboard/tasks/${taskId}/asset-library/video-interactions/current`;
const originalSettingsResponse = await context.request.get(settingsUrl);
assert.equal(originalSettingsResponse.status(), 200);
const originalSettings = await responseJson(originalSettingsResponse, "Video API settings");
const testSettings = {
  ...(originalSettings.settings || originalSettings),
  confirmBeforeGenerate: true,
  aspect: "16:9",
  duration: 3,
  count: 1,
  referenceMode: "selected_images",
  agentVideoAlias: omniAlias.alias,
  provider: "",
  model: "",
};
const saveSettings = await context.request.put(settingsUrl, { data: { settings: testSettings } });
assert.equal(saveSettings.status(), 200);

const page = await context.newPage();
const apiFailures = [];
const pageErrors = [];
page.on("pageerror", (error) => pageErrors.push(error.message));
page.on("response", (response) => {
  const pathname = new URL(response.url()).pathname;
  if (pathname.startsWith("/api/") && response.status() >= 500) {
    apiFailures.push(`${response.request().method()} ${pathname} ${response.status()}`);
  }
});

let threadId = "";
let cloudDeleted = false;
const evidence = {
  schema_version: 1,
  test: "gemini_omni_paid_live_browser",
  target: "macmini-4 local only",
  task_id: taskId,
  budget: { max_usd: EXPECTED_BUDGET_USD, max_calls: EXPECTED_CALLS, max_total_seconds: EXPECTED_SECONDS },
  screenshots: [],
  turns: [],
  cloud_cleanup: null,
  ok: false,
};

async function screenshot(filename) {
  await page.screenshot({ path: path.join(screenshotDir, filename), fullPage: true });
  evidence.screenshots.push(`assets/${filename}`);
}

async function currentThread() {
  const response = await context.request.get(currentThreadUrl);
  assert.equal(response.status(), 200);
  return responseJson(response, "current video interaction");
}

async function submitPaidTurn(prompt, confirmationFilename, completedFilename) {
  const completedBefore = await page.getByText(/已经生成并保存到视频素材：/).count();
  const errorsBefore = await page.locator(".ual-video-agent .ual-message.is-error").count();
  const composer = page.locator(".ual-video-agent textarea");
  await composer.fill(prompt);
  await page.locator(".ual-video-agent").getByRole("button", { name: "发送", exact: true }).click();
  const confirmation = page.getByRole("alertdialog", { name: "确认生成视频" });
  await confirmation.waitFor({ state: "visible", timeout: 15_000 });
  assert.ok((await confirmation.innerText()).includes("每次确认都会单独计费"));
  await screenshot(confirmationFilename);
  await confirmation.getByRole("button", { name: "生成", exact: true }).click();
  const completed = page.getByText(/已经生成并保存到视频素材：/).nth(completedBefore);
  const failed = page.locator(".ual-video-agent .ual-message.is-error").nth(errorsBefore);
  const outcome = await Promise.race([
    completed.waitFor({ state: "visible", timeout: 900_000 }).then(() => ({ ok: true })),
    failed.waitFor({ state: "visible", timeout: 900_000 }).then(async () => ({
      ok: false,
      detail: await failed.innerText(),
    })),
  ]);
  assert.equal(outcome.ok, true, `paid browser turn failed: ${outcome.detail || "unknown error"}`);
  await screenshot(completedFilename);
}

try {
  await page.goto(`${url}/?e2eGeminiOmniPaid=${Date.now()}#/koubo-asset-library/tasks/${taskId}`, {
    waitUntil: "domcontentloaded",
    timeout: 30_000,
  });
  await page.getByText("Asset Library", { exact: true }).waitFor({ timeout: 30_000 });
  await page.locator(".ual-nav button").filter({ hasText: /^视频生成$/ }).first().click();
  const panel = page.locator(".ual-video-agent");
  await panel.waitFor({ state: "visible", timeout: 15_000 });
  const stateful = panel.locator('.ual-video-stateful[aria-label="有状态视频版本"]');
  await stateful.waitFor({ state: "visible", timeout: 15_000 });
  await stateful.getByRole("button", { name: "新建视频", exact: true }).click();

  await submitPaidTurn(
    "Create a three-second minimal test video: a solid blue circle centered on a white background, static camera, no motion, no text, no dialogue, no music. One continuous shot.",
    "20-omni-browser-paid-turn-1-confirm.png",
    "21-omni-browser-paid-turn-1-completed.png",
  );
  const first = await currentThread();
  threadId = String(first.video_thread_id || "");
  assert.ok(threadId);
  assert.equal(first.turns.length, 1);
  assert.equal(first.turns[0].status, "completed");
  assert.equal(first.turns[0].provider_state_status, "available");
  assert.ok(!JSON.stringify(first).includes("interaction_id"));
  evidence.turns.push(first.turns[0]);

  await stateful.getByRole("button", { name: "继续编辑", exact: true }).click();
  await submitPaidTurn(
    "Change only the centered circle from blue to green. Keep the white background, static camera, duration, composition, and everything else the same.",
    "22-omni-browser-paid-turn-2-confirm.png",
    "23-omni-browser-paid-turn-2-completed.png",
  );
  const second = await currentThread();
  assert.equal(second.video_thread_id, threadId);
  assert.equal(second.turns.length, 2);
  assert.equal(second.turns[1].status, "completed");
  assert.equal(second.turns[1].parent_turn_id, second.turns[0].video_turn_id);
  assert.equal(second.head_turn_id, second.turns[1].video_turn_id);
  assert.ok(!JSON.stringify(second).includes("interaction_id"));
  evidence.turns = second.turns;

  const cleanupResponse = await context.request.post(
    `${url}/api/koubo-storyboard/tasks/${taskId}/asset-library/video-interactions/${encodeURIComponent(threadId)}/cloud-context/delete`,
    { data: {} },
  );
  assert.equal(cleanupResponse.status(), 200);
  const cleanup = await responseJson(cleanupResponse, "cloud cleanup");
  assert.equal(cleanup.ok, true);
  assert.equal(cleanup.deleted_count, 2);
  cloudDeleted = true;
  evidence.cloud_cleanup = cleanup;
  evidence.ok = true;
  assert.deepEqual(apiFailures, []);
  assert.deepEqual(pageErrors, []);
} finally {
  if (threadId && !cloudDeleted) {
    await context.request.post(
      `${url}/api/koubo-storyboard/tasks/${taskId}/asset-library/video-interactions/${encodeURIComponent(threadId)}/cloud-context/delete`,
      { data: {} },
    ).catch(() => null);
  }
  const restore = await context.request.put(settingsUrl, {
    data: { settings: originalSettings.settings || originalSettings },
  }).catch(() => null);
  assert.equal(restore?.status(), 200, "the paid browser test must restore the original settings");
  await writeFile(artifactPath, `${JSON.stringify(evidence, null, 2)}\n`, "utf8");
  await context.close();
  await browser.close();
}

assert.equal(evidence.ok, true);
console.log(`Gemini Omni paid live browser E2E: ok (${evidence.turns.length} turns, cloud state deleted)`);
