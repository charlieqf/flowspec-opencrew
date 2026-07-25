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

const PAID_GATE = "OPENCREW_RUN_PAID_GEMINI_OMNI_UPLOAD_BROWSER";
const KEY_GATE = "OPENCREW_GEMINI_OMNI_BROWSER_KEY_AUTHORIZED";
const BUDGET_GATE = "OPENCREW_GEMINI_OMNI_UPLOAD_BROWSER_MAX_USD";
const CALLS_GATE = "OPENCREW_GEMINI_OMNI_UPLOAD_BROWSER_MAX_CALLS";
const SECONDS_GATE = "OPENCREW_GEMINI_OMNI_UPLOAD_BROWSER_MAX_TOTAL_SECONDS";
const EXPECTED_BUDGET_USD = 0.30;
const EXPECTED_CALLS = 1;
const EXPECTED_SECONDS = 3;

assert.equal(process.env[PAID_GATE], "1", `${PAID_GATE}=1 is required`);
assert.equal(process.env[KEY_GATE], "1", `${KEY_GATE}=1 is required`);
assert.equal(Number(process.env[BUDGET_GATE]), EXPECTED_BUDGET_USD, `${BUDGET_GATE} must equal 0.30`);
assert.equal(Number(process.env[CALLS_GATE]), EXPECTED_CALLS, `${CALLS_GATE} must equal 1`);
assert.equal(Number(process.env[SECONDS_GATE]), EXPECTED_SECONDS, `${SECONDS_GATE} must equal 3`);

const url = baseUrl();
const taskId = String(process.env.OPENCREW_E2E_KOUBO_TASK_ID || "305");
const screenshotDir = path.resolve(String(process.env.OPENCREW_E2E_SCREENSHOT_DIR || "."));
const artifactPath = path.resolve(String(process.env.OPENCREW_E2E_ARTIFACT || "gemini-omni-paid-upload-browser.json"));
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
  test: "gemini_omni_paid_upload_edit_live_browser",
  target: "macmini-4 local only",
  task_id: taskId,
  budget: { max_usd: EXPECTED_BUDGET_USD, max_calls: EXPECTED_CALLS, max_total_seconds: EXPECTED_SECONDS },
  source_reference: null,
  screenshots: [],
  turn: null,
  cloud_cleanup: null,
  ok: false,
};

async function screenshot(filename) {
  await page.screenshot({ path: path.join(screenshotDir, filename), fullPage: true });
  evidence.screenshots.push(`assets/${filename}`);
}

try {
  await page.goto(`${url}/?e2eGeminiOmniUploadPaid=${Date.now()}#/koubo-asset-library/tasks/${taskId}`, {
    waitUntil: "domcontentloaded",
    timeout: 30_000,
  });
  await page.getByText("Asset Library", { exact: true }).waitFor({ timeout: 30_000 });
  await page.locator(".ual-nav button").filter({ hasText: /^视频生成$/ }).first().click();
  const panel = page.locator(".ual-video-agent");
  await panel.waitFor({ state: "visible", timeout: 15_000 });
  const stateful = panel.locator('.ual-video-stateful[aria-label="有状态视频版本"]');
  await stateful.waitFor({ state: "visible", timeout: 15_000 });
  await stateful.getByRole("button", { name: "上传视频编辑", exact: true }).click();

  const sourceCard = page.locator(".ual-video-card").filter({ hasText: "有状态版本" }).first();
  await sourceCard.waitFor({ state: "visible", timeout: 15_000 });
  const sourceTitle = await sourceCard.locator(".ual-card-image").getAttribute("title");
  await sourceCard.getByRole("button", { name: "Add reference", exact: true }).click();
  const videoReferenceGroup = panel.locator(".ual-video-reference-slot-group.is-videos");
  await videoReferenceGroup.getByText("1/1", { exact: true }).waitFor({ timeout: 10_000 });
  const sourceLabel = await videoReferenceGroup.locator("figcaption").innerText();
  evidence.source_reference = { title: sourceTitle || "", label: sourceLabel };

  const prompt = "Edit the uploaded test video. Change only the centered circle to red. Keep the white background, static camera, duration, composition, and everything else the same. No text, no dialogue, no music.";
  await panel.locator("textarea").fill(prompt);
  await panel.getByRole("button", { name: "发送", exact: true }).click();
  const confirmation = page.getByRole("alertdialog", { name: "确认生成视频" });
  await confirmation.waitFor({ state: "visible", timeout: 15_000 });
  assert.ok((await confirmation.innerText()).includes("以上传视频新建编辑链"));
  const completedBefore = await page.getByText(/已经生成并保存到视频素材：/).count();
  const errorsBefore = await panel.locator(".ual-message.is-error").count();
  await screenshot("47-omni-browser-paid-upload-edit-confirm.png");
  await confirmation.getByRole("button", { name: "生成", exact: true }).click();

  const completed = page.getByText(/已经生成并保存到视频素材：/).nth(completedBefore);
  const failed = panel.locator(".ual-message.is-error").nth(errorsBefore);
  const outcome = await Promise.race([
    completed.waitFor({ state: "visible", timeout: 900_000 }).then(() => ({ ok: true })),
    failed.waitFor({ state: "visible", timeout: 900_000 }).then(async () => ({ ok: false, detail: await failed.innerText() })),
  ]);
  assert.equal(outcome.ok, true, `paid upload edit failed: ${outcome.detail || "unknown error"}`);
  await screenshot("48-omni-browser-paid-upload-edit-completed.png");

  const currentResponse = await context.request.get(currentThreadUrl);
  assert.equal(currentResponse.status(), 200);
  const current = await responseJson(currentResponse, "current upload edit interaction");
  assert.equal(current.turns.length, 1);
  assert.equal(current.turns[0].operation, "edit");
  assert.equal(current.turns[0].status, "completed");
  assert.equal(current.turns[0].parent_turn_id, null);
  assert.equal(current.head_turn_id, current.turns[0].video_turn_id);
  assert.ok(!JSON.stringify(current).includes("interaction_id"));
  threadId = String(current.video_thread_id || "");
  evidence.turn = current.turns[0];

  const cleanupResponsePromise = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && new URL(response.url()).pathname.endsWith(`/video-interactions/${threadId}/cloud-context/delete`)
  ));
  page.once("dialog", (dialog) => dialog.accept());
  await stateful.getByRole("button", { name: "清除云端上下文", exact: true }).click();
  const cleanupResponse = await cleanupResponsePromise;
  assert.equal(cleanupResponse.status(), 200);
  const cleanup = await responseJson(cleanupResponse, "upload edit cloud cleanup");
  assert.equal(cleanup.ok, true);
  assert.equal(cleanup.deleted_count, 1);
  cloudDeleted = true;
  evidence.cloud_cleanup = cleanup;

  await page.reload({ waitUntil: "domcontentloaded" });
  await page.getByText("Asset Library", { exact: true }).waitFor({ timeout: 30_000 });
  await page.locator(".ual-nav button").filter({ hasText: /^视频生成$/ }).first().click();
  await page.locator('.ual-video-stateful[aria-label="有状态视频版本"]').getByText("尚未创建版本", { exact: true }).waitFor({ timeout: 15_000 });
  await screenshot("49-omni-browser-paid-upload-edit-after-cleanup.png");

  assert.deepEqual(apiFailures, []);
  assert.deepEqual(pageErrors, []);
  evidence.ok = true;
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
  assert.equal(restore?.status(), 200, "the paid upload browser test must restore the original settings");
  await writeFile(artifactPath, `${JSON.stringify(evidence, null, 2)}\n`, "utf8");
  await context.close();
  await browser.close();
}

assert.equal(evidence.ok, true);
console.log("Gemini Omni paid upload-edit browser E2E: ok (1 edit turn, cloud state deleted)");
