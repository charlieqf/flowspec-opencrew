#!/usr/bin/env node
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const PLAYWRIGHT_FALLBACK = "/private/tmp/opencrew-playwright-runner/node_modules/playwright";
const BASE_URL = (process.env.OPENCREW_E2E_FRONTEND_URL || "http://127.0.0.1:18080").replace(/\/$/, "");
const TASK_ID = process.env.OPENCREW_E2E_KOUBO_TASK_ID || "135";
const ASSET_VIEW = process.env.OPENCREW_E2E_KOUBO_ASSET_VIEW || "tts-agent";
const BROWSER_NAME = process.env.OPENCREW_E2E_BROWSER || "chromium";
const APP_PASSWORD = process.env.OPENCREW_E2E_APP_PASSWORD || "";
const ALLOW_SETUP = process.env.OPENCREW_E2E_ALLOW_SETUP === "1";
const HEADLESS = process.env.OPENCREW_E2E_HEADLESS !== "0";
const SMOKE_ONLY = process.env.OPENCREW_E2E_TTS_AGENT_SMOKE === "1";
const BATCH_ALL = process.env.OPENCREW_E2E_TTS_AGENT_BATCH_ALL === "1";
const RUN_ID = process.env.OPENCREW_E2E_RUN_ID || `tts-real-${Date.now()}`;
const ASSET_VIEW_SUFFIX = ASSET_VIEW ? `/${encodeURIComponent(ASSET_VIEW)}` : "";
const ASSET_LIBRARY_URL = `${BASE_URL}/#/koubo-asset-library/tasks/${TASK_ID}${ASSET_VIEW_SUFFIX}`;
const DESKTOP_SCREENSHOT = process.env.OPENCREW_E2E_TTS_AGENT_DESKTOP_SCREENSHOT || `/private/tmp/opencrew_tts_agent_desktop_${RUN_ID}_${BROWSER_NAME}.png`;
const MOBILE_SCREENSHOT = process.env.OPENCREW_E2E_TTS_AGENT_MOBILE_SCREENSHOT || `/private/tmp/opencrew_tts_agent_mobile_${RUN_ID}_${BROWSER_NAME}.png`;
const FAILURE_SCREENSHOT = process.env.OPENCREW_E2E_TTS_AGENT_FAILURE_SCREENSHOT || `/private/tmp/opencrew_tts_agent_failure_${RUN_ID}_${BROWSER_NAME}.png`;

function loadPlaywright() {
  for (const id of ["playwright", PLAYWRIGHT_FALLBACK]) {
    try {
      return require(id);
    } catch {
      // Try the next location.
    }
  }
  throw new Error("Playwright is not installed.");
}

async function ensureAuthenticated(page) {
  await page.waitForLoadState("domcontentloaded", { timeout: 30000 }).catch(() => {});
  await page.waitForTimeout(400);
  const authGateVisible = await page.locator(".auth-gate").isVisible().catch(() => false);
  if (!authGateVisible) return;
  await page.waitForFunction(() => {
    const gate = document.querySelector(".auth-gate");
    const button = gate?.querySelector("button");
    return Boolean(button && !button.disabled && /Sign in|Create password/i.test(button.textContent || ""));
  }, null, { timeout: 15000 });
  const action = (await page.locator(".auth-gate button").first().innerText().catch(() => "")).trim();
  if (!APP_PASSWORD) {
    throw new Error(`Authentication is required (${action || "auth gate"}). Set OPENCREW_E2E_APP_PASSWORD.`);
  }
  if (/Create password/i.test(action) && !ALLOW_SETUP) {
    throw new Error("Refusing to create an admin password during e2e.");
  }
  await page.locator(".auth-gate input[type='password']").fill(APP_PASSWORD);
  await page.locator(".auth-gate button").filter({ hasText: /Sign in|Create password/ }).click();
  await page.locator(".auth-gate").waitFor({ state: "hidden", timeout: 15000 });
}

function exactText(value) {
  return new RegExp(`^${String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`);
}

async function assertPressed(locator, expected, message) {
  const pressed = await locator.getAttribute("aria-pressed");
  assert.equal(pressed, expected ? "true" : "false", message);
}

async function waitForTtsPage(page) {
  await page.goto(ASSET_LIBRARY_URL, { waitUntil: "domcontentloaded", timeout: 30000 });
  await ensureAuthenticated(page);
  await page.waitForLoadState("networkidle", { timeout: 10000 }).catch(() => {});
  const heading = page.locator(".ual-tts-workspace h2").filter({ hasText: exactText("语音智能体") }).first();
  if (await heading.isVisible().catch(() => false)) return;
  const navButton = page.locator(".ual-nav button").filter({ hasText: exactText("语音智能体") }).first();
  await navButton.waitFor({ state: "visible", timeout: 20000 });
  await navButton.click();
  await heading.waitFor({ state: "visible", timeout: 15000 });
}

async function selectOptionByValueOrLabel(page, selector, valueOrLabel) {
  const select = page.locator(selector).first();
  const value = await select.evaluate((node, wanted) => {
    const options = Array.from(node.options);
    const option = options.find((item) => item.value === wanted || item.textContent.trim() === wanted);
    return option?.value || "";
  }, valueOrLabel);
  assert.ok(value, `Missing select option: ${valueOrLabel}`);
  await select.selectOption(value);
}

async function googleScenarioOptions(page, selector) {
  return page.locator(`${selector} option`).evaluateAll((items) =>
    items.map((item) => ({ value: item.value, label: item.textContent.trim() })),
  );
}

async function chatTexts(page) {
  return page.locator(".ual-tts-agent-chat article").evaluateAll((items) =>
    items.map((item) => item.textContent.replace(/\s+/g, " ").trim()),
  );
}

async function submitRequest(page, marker) {
  await page.locator(".ual-tts-composer textarea").fill(`真实 TTS Agent 浏览器验收 ${marker}：生成音频、写回 StoryBoard，并在刷新后保留完整对话记录。`);
  await page.getByLabel("Send TTS request").click();
  await page.locator(".ual-tts-agent-chat").getByText(marker).waitFor({ state: "visible", timeout: 15000 });
  await page.locator(".ual-tts-role-row:not(.is-head)").first().waitFor({ state: "visible", timeout: 20000 });
  await page.locator(".ual-tts-prompt").first().waitFor({ state: "visible", timeout: 20000 });
}

async function applyRoleGuide(page) {
  await page.locator(".ual-tts-role-row:not(.is-head)").first().getByRole("button", { name: "角色配置" }).click();
  await page.getByRole("heading", { name: "角色提示词配置" }).waitFor({ state: "visible", timeout: 10000 });
  const options = await googleScenarioOptions(page, '.ual-tts-guide-controls label:has-text("情景") select');
  assert.equal(options.length, 14, `Expected 14 shared Google TTS scenarios, got ${options.length}`);
  assert.ok(options.some((item) => item.value === "single-basic"), "Missing shared single-basic scenario");
  assert.ok(options.some((item) => item.value === "classifier-safe"), "Missing classifier-safe scenario");
  assert.ok(!options.some((item) => item.value === "single_basic_reading"), "Found legacy underscore scenario id");
  await selectOptionByValueOrLabel(page, '.ual-tts-guide-controls label:has-text("情景") select', "commercial-short");
  const roleKeyword = page.locator(".ual-tts-keyword-chip").filter({ hasText: exactText("亲切真实") }).first();
  await roleKeyword.click();
  await assertPressed(roleKeyword, true, "Expected role keyword chip to be selected");
  const beforeCount = await page.locator(".ual-tts-agent-chat").getByText("已套用角色提示词配置").count();
  await page.getByRole("button", { name: "保存并套用到当前角色" }).click();
  await page.waitForFunction((before) =>
    Array.from(document.querySelectorAll(".ual-tts-agent-chat article")).filter((item) => item.textContent.includes("已套用角色提示词配置")).length > before,
  beforeCount, { timeout: 10000 });
  await page.locator(".ual-tts-role-row:not(.is-head)").first().getByText("亲切真实").waitFor({ state: "visible", timeout: 10000 });
}

async function applyPromptGuide(page) {
  await page.locator(".ual-tts-prompt").first().getByRole("button", { name: "Prompt 配置" }).click();
  await page.getByRole("heading", { name: "单句 Prompt 配置" }).waitFor({ state: "visible", timeout: 10000 });
  await selectOptionByValueOrLabel(page, '.ual-tts-guide-controls label:has-text("情景") select', "local-tone-shift");
  const promptKeyword = page.locator(".ual-tts-keyword-chip").filter({ hasText: exactText("末尾确认") }).first();
  await promptKeyword.click();
  await assertPressed(promptKeyword, true, "Expected prompt keyword chip to be selected");
  const beforeCount = await page.locator(".ual-tts-agent-chat").getByText("已套用单句 Prompt 配置").count();
  await page.getByRole("button", { name: "保存并套用到当前单句" }).click();
  await page.waitForFunction((before) =>
    Array.from(document.querySelectorAll(".ual-tts-agent-chat article")).filter((item) => item.textContent.includes("已套用单句 Prompt 配置")).length > before,
  beforeCount, { timeout: 10000 });
  await page.locator(".ual-tts-prompt").first().getByText("先平稳").waitFor({ state: "visible", timeout: 10000 });
  await page.locator(".ual-tts-prompt").first().getByText("末尾确认").waitFor({ state: "visible", timeout: 10000 });
}

async function selectSecondSlotIfAvailable(page) {
  const options = await page.locator(".ual-tts-slot-panel select option").evaluateAll((items) =>
    items.map((item) => ({ value: item.value, label: item.textContent.trim() })),
  );
  assert.ok(options.length >= 1, "Expected at least one StoryBoard Audio_Final slot");
  const target = options[1] || options[0];
  await page.locator(".ual-tts-slot-panel select").selectOption(target.value);
  await page.locator(".ual-tts-slot-panel code").getByText(/SessionOutput\/storyboard\/Working\/[^/]+\/Audio_Final\.(wav|mp3)/).waitFor({ state: "visible", timeout: 5000 });
  return target;
}

async function waitForGeneration(page, beforeGeneratedMessageCount) {
  await page.getByText(/已提交 provider|真实生成中|完成：/).first().waitFor({ state: "visible", timeout: 20000 });
  await page.waitForFunction((before) =>
    Array.from(document.querySelectorAll(".ual-tts-agent-chat article")).filter((item) => item.textContent.includes("真实声音已生成并写回")).length > before,
  beforeGeneratedMessageCount, { timeout: 120000 });
  const outputPath = (await page.locator(".ual-tts-audio-result > div > span").first().innerText()).trim();
  assert.match(outputPath, /^SessionOutput\/storyboard\/Working\/[^/]+\/Audio_Final\.(wav|mp3)$/);
  assert.equal(outputPath.includes("double_host_preview"), false);
  return outputPath;
}

async function waitForBatchGeneration(page, beforeRequestCount, requests) {
  const totalSlots = await page.locator(".ual-tts-slot-panel select option").count();
  assert.ok(totalSlots >= 1, "Expected at least one slot before batch generation");
  await page.getByRole("button", { name: "生成全部台词" }).click();
  await page.getByText(/全部生成 \d+\/\d+|全部生成完成/).first().waitFor({ state: "visible", timeout: 30000 });
  await page.waitForFunction((expected) =>
    document.querySelectorAll(".ual-tts-batch-results article").length === expected,
  totalSlots, { timeout: 600000 });
  await page.getByRole("button", { name: "生成全部台词" }).waitFor({ state: "visible", timeout: 600000 });
  const doneCount = await page.locator(".ual-tts-batch-results article.is-done").count();
  const failedCount = await page.locator(".ual-tts-batch-results article.is-failed").count();
  assert.equal(failedCount, 0, `Expected no failed batch items, got ${failedCount}`);
  assert.equal(doneCount, totalSlots, `Expected ${totalSlots} batch outputs, got ${doneCount}`);
  assert.ok(requests.length >= beforeRequestCount + totalSlots, "Batch generation did not call real TTS for each slot");
  const paths = await page.locator(".ual-tts-batch-results article.is-done span").evaluateAll((items) => items.map((item) => item.textContent.trim()));
  for (const path of paths) {
    assert.match(path, /^SessionOutput\/storyboard\/Working\/[^/]+\/Audio_Final\.(wav|mp3)$/);
  }
  return { totalSlots, doneCount, paths };
}

async function verifyAudioReadable(page, outputPath) {
  return page.evaluate(async ({ taskId, output }) => {
    const messagesRes = await fetch(`/api/koubo-storyboard/tasks/${taskId}/asset-library/tts-agent/messages`, { credentials: "include" });
    const messages = await messagesRes.json();
    const sessionId = Number(messages.session_id || 0);
    if (!sessionId) throw new Error("Missing session_id from persisted TTS Agent messages");
    const encoded = output.split("/").map(encodeURIComponent).join("/");
    const rawRes = await fetch(`/api/session-tasks/${sessionId}/raw/${encoded}?v=${Date.now()}`, { credentials: "include" });
    const bytes = rawRes.ok ? (await rawRes.arrayBuffer()).byteLength : 0;
    const dialogueKey = output.match(/Working\/([^/]+)\/Audio_Final\./)?.[1] || "";
    const manifestRel = `SessionOutput/storyboard/tts_manifests/${dialogueKey}_Audio_Final.json`;
    const manifestEncoded = manifestRel.split("/").map(encodeURIComponent).join("/");
    const manifestRes = await fetch(`/api/session-tasks/${sessionId}/raw/${manifestEncoded}?v=${Date.now()}`, { credentials: "include" });
    const manifest = manifestRes.ok ? await manifestRes.json() : {};
    const detail = await fetch(`/api/koubo-storyboard/tasks/${taskId}`, { credentials: "include" }).then((res) => res.json());
    const dialogueAudioPaths = [];
    const visit = (node) => {
      if (!node || typeof node !== "object") return;
      if (node.working_assets?.audio?.path) dialogueAudioPaths.push(node.working_assets.audio.path);
      for (const value of Object.values(node)) {
        if (Array.isArray(value)) value.forEach(visit);
        else if (value && typeof value === "object") visit(value);
      }
    };
    visit(detail?.plan || detail);
    return {
      messagesStatus: messagesRes.status,
      messageCount: Array.isArray(messages.messages) ? messages.messages.length : 0,
      persistedTexts: Array.isArray(messages.messages) ? messages.messages.map((item) => String(item.text || "")) : [],
      rawStatus: rawRes.status,
      bytes,
      manifestStatus: manifestRes.status,
      manifest,
      dialogueAudioPaths,
    };
  }, { taskId: TASK_ID, output: outputPath });
}

async function verifyReloadedChat(page, marker, outputPath) {
  await page.reload({ waitUntil: "domcontentloaded", timeout: 30000 });
  await ensureAuthenticated(page);
  await page.waitForLoadState("networkidle", { timeout: 10000 }).catch(() => {});
  await page.locator(".ual-tts-workspace h2").filter({ hasText: exactText("语音智能体") }).first().waitFor({ state: "visible", timeout: 20000 });
  await page.locator(".ual-tts-agent-chat").getByText(marker).waitFor({ state: "visible", timeout: 20000 });
  await page.locator(".ual-tts-agent-chat").getByText("已套用角色提示词配置").first().waitFor({ state: "visible", timeout: 10000 });
  await page.locator(".ual-tts-agent-chat").getByText("已套用单句 Prompt 配置").first().waitFor({ state: "visible", timeout: 10000 });
  await page.locator(".ual-tts-agent-chat").getByText("真实声音已生成并写回").first().waitFor({ state: "visible", timeout: 10000 });
  await page.locator(".ual-tts-agent-chat").getByText("已从后端刷新并确认 Audio_Final 写回").first().waitFor({ state: "visible", timeout: 10000 });
  await page.getByText(outputPath).first().waitFor({ state: "visible", timeout: 10000 });
  await page.locator(".ual-tts-audio-result > div > span").filter({ hasText: exactText(outputPath) }).waitFor({ state: "visible", timeout: 10000 });
  await page.getByText("下游使用状态", { exact: true }).waitFor({ state: "visible", timeout: 10000 });
  await page.locator(".ual-tts-downstream-steps strong").filter({ hasText: exactText("Audio_Final") }).waitFor({ state: "visible", timeout: 10000 });
  await page.locator(".ual-tts-downstream code").filter({ hasText: exactText(outputPath) }).waitFor({ state: "visible", timeout: 10000 });
  await page.locator(".ual-tts-downstream").getByRole("button", { name: "去视频智能体" }).waitFor({ state: "visible", timeout: 10000 });
  const texts = await chatTexts(page);
  assert.ok(texts.length >= 6, `Expected at least 6 chat records after reload, got ${texts.length}`);
  assert.equal(texts.some((item) => item.includes("double_host_preview")), false);
  return texts;
}

async function main() {
  const playwright = loadPlaywright();
  const browserType = playwright[BROWSER_NAME] || playwright.chromium;
  if (!browserType) throw new Error(`Unsupported browser: ${BROWSER_NAME}`);
  const browser = await browserType.launch({ headless: HEADLESS });
  const context = await browser.newContext({ viewport: { width: 1440, height: 980 }, deviceScaleFactor: 1 });
  const page = await context.newPage();
  const apiFailures = [];
  const ttsRequests = [];
  const browserErrors = [];
  page.on("pageerror", (error) => browserErrors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(message.text());
  });
  page.on("request", (request) => {
    if (request.url().includes("/api/koubo-storyboard/tasks/") && request.url().includes("/scene-tts/events")) {
      try {
        ttsRequests.push(JSON.parse(request.postData() || "{}"));
      } catch {
        ttsRequests.push({ parse_error: true, raw: request.postData() });
      }
    }
  });
  page.on("response", (response) => {
    const url = response.url();
    if (url.includes("/api/") && response.status() >= 400) {
      apiFailures.push({ status: response.status(), url });
    }
  });

  try {
    await waitForTtsPage(page);
    const navItems = await page.locator(".ual-nav button").evaluateAll((buttons) => buttons.map((button) => button.innerText.trim()));
    assert.deepEqual(navItems, ["图像生成", "图像智能体", "视频生成", "视频智能体", "语音智能体", "数字人智能体", "提示词智能体", "素材检索智能体"]);
    assert.equal(await page.locator(".ual-nav button.is-active").innerText(), "语音智能体");
    assert.equal(navItems.indexOf("语音智能体"), navItems.indexOf("视频智能体") + 1);
    assert.equal(navItems.indexOf("数字人智能体"), navItems.indexOf("语音智能体") + 1);

    if (SMOKE_ONLY) {
      const texts = await chatTexts(page);
      assert.ok(texts.length >= 1, "Expected right-side Agent chat to render");
      console.log(JSON.stringify({ ok: true, mode: "smoke", browser: BROWSER_NAME, url: ASSET_LIBRARY_URL, chatCount: texts.length }, null, 2));
      return;
    }

    const marker = RUN_ID;
    await submitRequest(page, marker);
    await applyRoleGuide(page);
    await applyPromptGuide(page);
    const selectedSlot = await selectSecondSlotIfAvailable(page);
    await page.getByRole("button", { name: "生成全部台词" }).waitFor({ state: "visible", timeout: 10000 });

    const singlePromptBefore = ttsRequests.length;
    const singlePromptMessageBefore = await page.locator(".ual-tts-agent-chat").getByText("真实声音已生成并写回").count();
    await page.locator(".ual-tts-prompt .ual-tts-icon-button").first().click();
    await page.waitForFunction((before) =>
      Array.from(document.querySelectorAll(".ual-tts-agent-chat article")).filter((item) => item.textContent.includes("真实声音已生成并写回")).length > before,
    singlePromptMessageBefore, { timeout: 120000 });
    await page.waitForFunction(() => !document.querySelector(".ual-tts-prompt.is-playing"), null, { timeout: 120000 }).catch(() => {});
    assert.ok(ttsRequests.length > singlePromptBefore, "Single prompt play did not call real TTS endpoint");

    const generateBefore = ttsRequests.length;
    const generateMessageBefore = await page.locator(".ual-tts-agent-chat").getByText("真实声音已生成并写回").count();
    await page.getByRole("button", { name: /生成选中台词|重新生成选中台词/ }).first().click();
    await page.waitForFunction(() => document.body.innerText.includes("已提交 provider") || document.body.innerText.includes("真实生成中"), null, { timeout: 15000 }).catch(() => {});
    const outputPath = await waitForGeneration(page, generateMessageBefore);
    assert.ok(ttsRequests.length > generateBefore, "Generate audio did not call real TTS endpoint");

    await page.getByLabel("播放声音").click();
    await page.waitForFunction(() => document.querySelector(".ual-tts-audio-result.is-playing"), null, { timeout: 5000 }).catch(() => {});
    await page.waitForFunction(() => !document.querySelector(".ual-tts-audio-result.is-playing"), null, { timeout: 20000 }).catch(() => {});

    await page.getByRole("button", { name: "确认 StoryBoard 写回" }).click();
    await page.locator(".ual-tts-agent-chat").getByText("已从后端刷新并确认 Audio_Final 写回").first().waitFor({ state: "visible", timeout: 15000 });
    const backendState = await verifyAudioReadable(page, outputPath);
    assert.equal(backendState.messagesStatus, 200);
    assert.ok(backendState.messageCount >= 6, `Expected persisted messages, got ${backendState.messageCount}`);
    assert.equal(backendState.rawStatus, 200);
    assert.ok(backendState.bytes > 1000, `Generated audio is too small: ${backendState.bytes}`);
    assert.equal(backendState.manifestStatus, 200);
    assert.equal(backendState.manifest.provider, ttsRequests.at(-1)?.prompts?.[0]?.provider || backendState.manifest.provider);
    assert.equal(backendState.manifest.voice_id, ttsRequests.at(-1)?.prompts?.[0]?.voice_id || backendState.manifest.voice_id);
    assert.ok(backendState.dialogueAudioPaths.includes(outputPath), `StoryBoard detail did not include generated Audio_Final path ${outputPath}`);

    const reloadedTexts = await verifyReloadedChat(page, marker, outputPath);
    let batchResult = null;
    if (BATCH_ALL) {
      batchResult = await waitForBatchGeneration(page, ttsRequests.length, ttsRequests);
    }
    await page.screenshot({ path: DESKTOP_SCREENSHOT, fullPage: true });
    await page.setViewportSize({ width: 390, height: 900 });
    await page.waitForTimeout(300);
    const mobileState = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
      hasWorkspace: Boolean(document.querySelector(".ual-tts-workspace")),
      hasChat: Boolean(document.querySelector(".ual-tts-agent-chat article")),
    }));
    assert.equal(mobileState.hasWorkspace, true);
    assert.equal(mobileState.hasChat, true);
    assert.ok(mobileState.scrollWidth <= mobileState.clientWidth + 6, `Unexpected mobile horizontal overflow: ${JSON.stringify(mobileState)}`);
    await page.screenshot({ path: MOBILE_SCREENSHOT, fullPage: true });

    await page.locator(".ual-tts-downstream").getByRole("button", { name: "去视频智能体" }).click();
    await page.locator(".ual-nav button.is-active").filter({ hasText: exactText("视频智能体") }).waitFor({ state: "visible", timeout: 10000 });
    assert.ok(page.url().endsWith(`#/koubo-asset-library/tasks/${TASK_ID}/videos-agent`), `Expected videos-agent URL, got ${page.url()}`);

    const relevantFailures = apiFailures.filter((item) => !item.url.includes("/favicon"));
    const relevantBrowserErrors = browserErrors.filter((message) =>
      !(message.includes("koubo-storyboard.css") && message.includes("non CSS MIME types")),
    );
    assert.deepEqual(relevantFailures, []);
    assert.deepEqual(relevantBrowserErrors, []);
    const lastRequest = ttsRequests.at(-1) || {};
    assert.equal(lastRequest.use_locked_cache, false);
    assert.match(String(lastRequest.locked_output || ""), /^SessionOutput\/storyboard\/Working\/[^/]+\/Audio_Final\.(wav|mp3)$/);
    assert.notEqual(lastRequest.prompts?.[0]?.voice_id, "Kore");
    console.log(JSON.stringify({
      ok: true,
      browser: BROWSER_NAME,
      url: ASSET_LIBRARY_URL,
      marker,
      selectedSlot,
      outputPath,
      audioBytes: backendState.bytes,
      manifest: {
        provider: backendState.manifest.provider,
        model: backendState.manifest.model,
        voice_id: backendState.manifest.voice_id,
      },
      requestCount: ttsRequests.length,
      batchResult,
      chatCountAfterReload: reloadedTexts.length,
      screenshots: [DESKTOP_SCREENSHOT, MOBILE_SCREENSHOT],
    }, null, 2));
  } catch (error) {
    await page.screenshot({ path: FAILURE_SCREENSHOT, fullPage: true }).catch(() => {});
    const state = await page.evaluate(() => ({
      url: window.location.href,
      body: document.body.innerText.slice(0, 5000),
    })).catch((err) => ({ evaluate_error: String(err) }));
    console.error(JSON.stringify({ failureScreenshot: FAILURE_SCREENSHOT, state }, null, 2));
    throw error;
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
