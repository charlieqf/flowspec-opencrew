#!/usr/bin/env node
import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";

const require = createRequire(import.meta.url);
const PLAYWRIGHT_FALLBACK = "/private/tmp/opencrew-playwright-runner/node_modules/playwright";
const BASE_URL = (process.env.OPENCREW_E2E_FRONTEND_URL || "http://127.0.0.1:18080").replace(/\/$/, "");
const TASK_ID = process.env.OPENCREW_E2E_KOUBO_TASK_ID || "115";
const CDP_URL = process.env.OPENCREW_E2E_CDP_URL || "http://127.0.0.1:9224";
const APP_PASSWORD = process.env.OPENCREW_E2E_APP_PASSWORD || "";
const ALLOW_SETUP = process.env.OPENCREW_E2E_ALLOW_SETUP === "1";
const HEADLESS = process.env.OPENCREW_E2E_HEADLESS !== "0";
const STATEFUL_VIDEO_ONLY = process.env.OPENCREW_E2E_STATEFUL_VIDEO_ONLY === "1";
const SCREENSHOT_DIR = String(process.env.OPENCREW_E2E_SCREENSHOT_DIR || "").trim();
const ASSET_LIBRARY_URL = `${BASE_URL}/?e2eAssetAgents=${Date.now()}#/koubo-asset-library/tasks/${TASK_ID}`;

if (SCREENSHOT_DIR) await mkdir(SCREENSHOT_DIR, { recursive: true });

async function capture(page, filename) {
  if (!SCREENSHOT_DIR) return;
  await page.screenshot({ path: path.join(SCREENSHOT_DIR, filename), fullPage: true });
}

function loadPlaywright() {
  for (const id of ["playwright", PLAYWRIGHT_FALLBACK]) {
    try {
      return require(id);
    } catch {
      // Try the next location.
    }
  }
  throw new Error(
    "Playwright is not installed. Run `npm --prefix frontend install playwright` " +
      "or provide the local fallback at /private/tmp/opencrew-playwright-runner.",
  );
}

async function endpointAvailable(url) {
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(1200) });
    return response.ok;
  } catch {
    return false;
  }
}

function exactText(text) {
  return new RegExp(`^${String(text).replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`);
}

async function clickNav(page, label) {
  const button = page.locator(".ual-nav button, .ual-sidebar-bottom button").filter({ hasText: exactText(label) }).first();
  await button.waitFor({ state: "visible", timeout: 10000 }).catch(async (error) => {
    const visibleNav = await page.locator(".ual-nav button, .ual-sidebar-bottom button").allInnerTexts().catch(() => []);
    throw new Error(`Navigation ${label} was unavailable; visible navigation: ${JSON.stringify(visibleNav)}`, { cause: error });
  });
  await button.click();
  await page.waitForTimeout(250);
}

async function visibleText(page, text, timeout = 10000) {
  await page.getByText(text, { exact: true }).first().waitFor({ state: "visible", timeout });
}

async function ensureAuthenticated(page) {
  await page.waitForLoadState("domcontentloaded", { timeout: 30000 }).catch(() => {});
  await page.waitForTimeout(500);
  const authGateVisible = await page.locator(".auth-gate").isVisible().catch(() => false);
  if (!authGateVisible) return;
  const heading = (await page.locator(".auth-gate h1").first().innerText().catch(() => "")).trim();
  if (!APP_PASSWORD) {
    throw new Error(
      `Authentication is required (${heading || "auth gate"}). ` +
        "Set OPENCREW_E2E_APP_PASSWORD or run against an already-authenticated CDP browser.",
    );
  }
  if (/Create admin password/i.test(heading) && !ALLOW_SETUP) {
    throw new Error("Refusing to create an admin password during e2e. Set OPENCREW_E2E_ALLOW_SETUP=1 to allow setup.");
  }
  await page.locator(".auth-gate input[type='password']").fill(APP_PASSWORD);
  await page.locator(".auth-gate button").filter({ hasText: /Sign in|Create password/ }).click();
  await page.locator(".auth-gate").waitFor({ state: "hidden", timeout: 15000 });
}

async function openBrowser(chromium) {
  const cdpReady = !APP_PASSWORD && (await endpointAvailable(`${CDP_URL}/json/version`));
  if (cdpReady) {
    const browser = await chromium.connectOverCDP(CDP_URL);
    const context = browser.contexts()[0] || await browser.newContext();
    const reusable = context.pages().find((page) => !page.isClosed() && page.url().includes("#/koubo-asset-library/tasks/"));
    const page = reusable || context.pages().find((item) => !item.isClosed() && item.url().startsWith(BASE_URL)) || await context.newPage();
    return { browser, context, page, ownsBrowser: false, mode: "cdp" };
  }
  const browser = await chromium.launch({ headless: HEADLESS });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  return { browser, context, page, ownsBrowser: true, mode: "launch" };
}

async function navigateToAssetLibrary(page, mode) {
  if (mode === "cdp" && !page.url().startsWith("chrome://") && page.url() !== "about:blank") {
    await page.evaluate((url) => {
      window.location.href = url;
    }, ASSET_LIBRARY_URL).catch(() => {});
    await page.waitForURL(/#\/koubo-asset-library\/tasks\/\d+/, { timeout: 30000 }).catch(() => {});
  } else {
    await page.goto(ASSET_LIBRARY_URL, { waitUntil: "domcontentloaded", timeout: 30000 });
  }
  await ensureAuthenticated(page);
  await visibleText(page, "Asset Library");
  await page.waitForLoadState("networkidle", { timeout: 10000 }).catch(() => {});
  await page.waitForTimeout(500);
}

async function workspaceState(page) {
  return await page.evaluate(() => {
    const shell = document.querySelector(".ual-shell");
    const activeNav = Array.from(document.querySelectorAll(".ual-nav button, .ual-sidebar-bottom button"))
      .find((button) => button.classList.contains("is-active"));
    return {
      activeNav: activeNav?.innerText.trim() || "",
      hasImageWorkspaceText: document.body.innerText.includes("Image generation workspace"),
      imageAgentPanelCount: document.querySelectorAll(".ual-agent").length,
      videoAgentPanelCount: document.querySelectorAll(".ual-agent.ual-video-agent").length,
      openCodeAgentCount: document.querySelectorAll(".ual-opencode-agent").length,
      agentDrawerCount: document.querySelectorAll(".kbsp-agent-drawer").length,
      panelAgentDrawerCount: document.querySelectorAll(".kbsp-agent-drawer.is-panel").length,
      openCodeEntryCount: document.querySelectorAll('button[aria-label="OpenCode Agent"]').length,
      hasImageGrid: Boolean(document.querySelector('.ual-grid-wrap[aria-label="Images"]')),
      hasVideoGrid: Boolean(document.querySelector('.ual-grid-wrap[aria-label="Videos"]')),
      hasVideoWorkspaceLibrary: Boolean(document.querySelector('.ual-video-workspace-library')),
      hasVideoWorkspaceVideos: Boolean(document.querySelector('.ual-video-workspace-section[aria-label="Videos"]')),
      hasVideoWorkspaceImages: Boolean(document.querySelector('.ual-video-workspace-section[aria-label="Images"]')),
      hasVideoGenerationWorkspace: document.body.innerText.includes("Video generation workspace"),
      referenceStripCount: document.querySelectorAll(".ual-composer-references").length,
      mediaActionsText: document.querySelector(".ual-media-actions")?.innerText.trim() || "",
      shellClass: shell?.className || "",
      shellColumns: shell ? getComputedStyle(shell).gridTemplateColumns : "",
    };
  });
}

async function modelToggleTexts(page, selector) {
  await page.locator(`${selector} button`).first().waitFor({ state: "visible", timeout: 12000 });
  return await page.locator(`${selector} button`).evaluateAll((buttons) => buttons.map((button) => button.innerText.trim()));
}

function assertModelToggle(texts, label) {
  assert.deepEqual(texts, ["Max", "Flash"], `${label} should expose compact Max/Flash model toggles`);
}

async function assertNoSelect(page, rootSelector, label) {
  const count = await page.locator(`${rootSelector} select`).count();
  assert.equal(count, 0, `${label} should not render a select dropdown`);
}

function mockPromptModels() {
  return {
    items: [
      { providerID: "Max", providerName: "Max", modelID: "Max", modelName: "Max", asset_agent_image_generation: true },
      { providerID: "Flash", providerName: "Flash", modelID: "Flash", modelName: "Flash", asset_agent_image_generation: false },
    ],
    default_model: { providerID: "Max", modelID: "Max" },
  };
}

async function installMockAgentOwnedGeneration(page) {
  const ensurePattern = `**/api/koubo-storyboard/tasks/${TASK_ID}/asset-library-agent/chat/ensure-session`;
  const messagesPattern = `**/api/koubo-storyboard/tasks/${TASK_ID}/asset-library-agent/chat/messages`;
  const messagePattern = `**/api/koubo-storyboard/tasks/${TASK_ID}/asset-library-agent/chat/message`;
  const eventsPattern = `**/api/koubo-storyboard/tasks/${TASK_ID}/asset-library-agent/chat/events`;
  const legacyGeneratePattern = `**/api/koubo-storyboard/tasks/${TASK_ID}/asset-library-agent/generate/events`;
  const detail = await page.evaluate(async (taskId) => {
    const response = await fetch(`/api/koubo-storyboard/tasks/${taskId}`, { credentials: "include" });
    if (!response.ok) throw new Error(`Task detail failed: ${response.status}`);
    return await response.json();
  }, TASK_ID);
  const asset = {
    id: "e2e-images-agent-generated",
    label: "E2E Images-Agent generated image",
    filename: "e2e-images-agent-generated.png",
    kind: "image",
    source: "agent_generated",
    path: "SessionOutput/storyboard/assets/images/e2e-images-agent-generated.png",
    origin: {
      tool: "upload_asset_library_agent",
      request_id: "e2e_images_agent_generation",
      prompt: "E2E direct image generation prompt",
      provider: "e2e",
      model: "mock",
      reference_images: [],
    },
  };
  const assetImagePattern = `**/api/session-tasks/*/raw/${asset.path}`;
  const currentImages = Array.isArray(detail?.meta?.uploaded_images) ? detail.meta.uploaded_images : [];
  let releaseEvents = () => {};
  const eventsReady = new Promise((resolve) => {
    releaseEvents = resolve;
  });
  const mock = {
    patterns: [ensurePattern, messagesPattern, messagePattern, eventsPattern, legacyGeneratePattern, assetImagePattern],
    asset,
    requestPayload: null,
    legacyGenerateCalled: false,
  };
  const chatState = {
    ok: true,
    chat_opencode_session_id: "e2e-images-agent-session",
    prompt_models: mockPromptModels(),
  };
  const historyItems = Array.from({ length: 32 }, (_, index) => ({
    info: {
      id: `e2e_history_${index + 1}`,
      role: index % 2 ? "assistant" : "user",
      time: { created: 1781200000000 + index },
    },
    parts: [{
      id: `e2e_history_${index + 1}_part`,
      messageID: `e2e_history_${index + 1}`,
      type: "text",
      text: `E2E old Images-Agent message ${index + 1}`,
    }],
  }));
  await page.route(ensurePattern, async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(chatState) });
  });
  await page.route(messagesPattern, async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ...chatState, items: historyItems }) });
  });
  await page.route(messagePattern, async (route) => {
    const requestPayload = JSON.parse(route.request().postData() || "{}");
    mock.requestPayload = requestPayload;
    releaseEvents();
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ...chatState, model: { providerID: "Max", modelID: "Max", asset_agent_image_generation: true } }) });
  });
  await page.route(eventsPattern, async (route) => {
    await eventsReady;
    const payload = {
      ok: true,
      request_id: "e2e_images_agent_generation",
      task_id: Number(TASK_ID),
      session_id: Number(detail?.task?.session_id || detail?.meta?.analysis_session_id || 0),
      provider: mock.requestPayload?.provider || "e2e",
      model: mock.requestPayload?.model || "mock",
      reference_images: mock.requestPayload?.reference_images || [],
      reference_count: (mock.requestPayload?.reference_images || []).length,
      output: asset.path,
      prompt_preview: String(mock.requestPayload?.message || "").slice(0, 1000),
      prompt_length: String(mock.requestPayload?.message || "").length,
      asset,
      task: detail.task,
      plan: detail.plan || {},
      meta: {
        ...(detail.meta || {}),
        uploaded_images: [asset, ...currentImages.filter((item) => item?.path !== asset.path)],
      },
      elapsed_seconds: 0.1,
    };
    const completed = {
      type: "asset_agent.image_generation.completed",
      properties: {
        agent_generation_id: "e2e_agent_generation",
        chat_opencode_session_id: chatState.chat_opencode_session_id,
        agent_message_id: "e2e_assistant",
        title: "E2E Images-Agent generated image",
        aspect: "16:9",
        reference_count: 1,
        prompt_length: 35,
        ...payload,
      },
    };
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: [
        `data: ${JSON.stringify({ type: "ready", chat_opencode_session_id: chatState.chat_opencode_session_id })}\n\n`,
        `data: ${JSON.stringify({ type: "message.updated", properties: { info: { id: "e2e_assistant", role: "assistant", time: { created: Date.now(), completed: Date.now() + 1 } }, parts: [{ id: "e2e_part", messageID: "e2e_assistant", type: "text", text: "I will generate this image now." }] } })}\n\n`,
        `data: ${JSON.stringify(completed)}\n\n`,
      ].join(""),
    });
  });
  await page.route(legacyGeneratePattern, async (route) => {
    mock.legacyGenerateCalled = true;
    await route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ detail: "Images-Agent should not call legacy generate API" }) });
  });
  await page.route(assetImagePattern, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "image/png",
      body: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=", "base64"),
    });
  });
  return mock;
}

async function installMockVideoAgentGeneration(page) {
  const ensurePattern = `**/api/koubo-storyboard/tasks/${TASK_ID}/agents/asset_video/chat/ensure-session`;
  const messagesPattern = `**/api/koubo-storyboard/tasks/${TASK_ID}/agents/asset_video/chat/messages`;
  const messagePattern = `**/api/koubo-storyboard/tasks/${TASK_ID}/agents/asset_video/chat/message`;
  const eventsPattern = `**/api/koubo-storyboard/tasks/${TASK_ID}/agents/asset_video/chat/events`;
  const modelConfigPattern = `**/api/koubo-storyboard/tasks/${TASK_ID}/asset-library/video-model-config`;
  const settingsPattern = `**/api/koubo-storyboard/tasks/${TASK_ID}/asset-library/videos-agent/settings`;
  const currentInteractionPattern = `**/api/koubo-storyboard/tasks/${TASK_ID}/asset-library/video-interactions/**`;
  const detail = await page.evaluate(async (taskId) => {
    const response = await fetch(`/api/koubo-storyboard/tasks/${taskId}`, { credentials: "include" });
    if (!response.ok) throw new Error(`Task detail failed: ${response.status}`);
    return await response.json();
  }, TASK_ID);
  const asset = {
    id: "e2e-videos-agent-generated",
    label: "E2E Videos-Agent generated video",
    filename: "e2e-videos-agent-generated.mp4",
    kind: "video",
    source: "agent_generated",
    path: "SessionOutput/storyboard/assets/videos/e2e-videos-agent-generated.mp4",
    origin: {
      tool: "upload_asset_library_video_agent",
      request_id: "e2e_videos_agent_generation",
      prompt: "E2E direct video generation prompt",
      provider: "e2e",
      model: "mock",
      reference_images: [],
      chat_opencode_session_id: "e2e-videos-agent-session",
      agent_generation_id: "e2e_video_agent_generation",
    },
  };
  const assetVideoPattern = `**/api/session-tasks/*/raw/${asset.path}`;
  const currentVideos = Array.isArray(detail?.meta?.uploaded_videos) ? detail.meta.uploaded_videos : [];
  let releaseEvents = () => {};
  const eventsReady = new Promise((resolve) => {
    releaseEvents = resolve;
  });
  const mock = {
    patterns: [
      ensurePattern,
      messagesPattern,
      messagePattern,
      eventsPattern,
      modelConfigPattern,
      settingsPattern,
      currentInteractionPattern,
      assetVideoPattern,
    ],
    asset,
    requestPayload: null,
    ensureRequests: 0,
    currentInteractionRequests: 0,
  };
  const statefulModelConfig = {
    kind: "video",
    providers: [],
    agent_model_aliases: [{
      alias: "Stateful",
      label: "Stateful",
      provider: "Public",
      model: "Stateful",
      capability: {
        tasks: ["text_to_video", "image_to_video", "reference_to_video", "edit"],
        stateful_edit: true,
        provider_state: "interaction",
        supports_video_input: true,
        supports_audio_input: false,
        aspect_ratios: ["16:9", "9:16"],
        reference_images: { min: 0, max: 4 },
        reference_videos: { min: 0, max: 1 },
        reference_audios: { min: 0, max: 0 },
        duration: { min: 1, max: 1, values: [1] },
      },
    }],
  };
  const currentInteraction = {
    video_thread_id: "e2e-video-thread",
    head_turn_id: "e2e-video-turn-1",
    status: "active",
    turns: [{
      video_turn_id: "e2e-video-turn-1",
      parent_turn_id: null,
      operation: "generate",
      status: "completed",
      provider_state_status: "active",
      output_path: "SessionOutput/storyboard/assets/videos/e2e-stateful-v1.mp4",
      created_at: "2026-07-22T00:00:00Z",
    }],
  };
  const chatState = {
    ok: true,
    agent_key: "asset_video",
    chat_opencode_session_id: "e2e-videos-agent-session",
    prompt_models: mockPromptModels(),
  };
  await page.route(modelConfigPattern, async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(statefulModelConfig) });
  });
  await page.route(settingsPattern, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        settings: {
          agentVideoAlias: "Stateful",
          confirmBeforeGenerate: true,
          aspect: "16:9",
          duration: 1,
          count: 1,
          chatProvider: "Max",
          chatModel: "Max",
        },
      }),
    });
  });
  await page.route(currentInteractionPattern, async (route) => {
    mock.currentInteractionRequests += 1;
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(currentInteraction) });
  });
  await page.route(ensurePattern, async (route) => {
    mock.ensureRequests += 1;
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(chatState) });
  });
  await page.route(messagesPattern, async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ...chatState, items: [] }) });
  });
  await page.route(messagePattern, async (route) => {
    mock.requestPayload = JSON.parse(route.request().postData() || "{}");
    releaseEvents();
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ...chatState, model: { providerID: "Max", modelID: "Max" } }) });
  });
  await page.route(eventsPattern, async (route) => {
    await eventsReady;
    const completed = {
      type: "asset_agent.video_generation.completed",
      properties: {
        ok: true,
        agent_generation_id: "e2e_video_agent_generation",
        chat_opencode_session_id: chatState.chat_opencode_session_id,
        agent_message_id: "e2e_video_assistant",
        title: "E2E Videos-Agent generated video",
        duration: 4,
        aspect: "9:16",
        reference_count: 0,
        prompt_length: 34,
        request_id: "e2e_videos_agent_generation",
        task_id: Number(TASK_ID),
        session_id: Number(detail?.task?.session_id || detail?.meta?.analysis_session_id || 0),
        provider: "e2e",
        model: "mock",
        output: asset.path,
        asset,
        task: detail.task,
        plan: detail.plan || {},
        meta: {
          ...(detail.meta || {}),
          uploaded_videos: [asset, ...currentVideos.filter((item) => item?.path !== asset.path)],
        },
        elapsed_seconds: 0.1,
      },
    };
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: [
        `data: ${JSON.stringify({ type: "ready", agent_key: "asset_video", chat_opencode_session_id: chatState.chat_opencode_session_id })}\n\n`,
        `data: ${JSON.stringify({ type: "message.updated", properties: { info: { id: "e2e_video_assistant", role: "assistant", time: { created: Date.now(), completed: Date.now() + 1 } }, parts: [{ id: "e2e_video_part", messageID: "e2e_video_assistant", type: "text", text: "Starting controlled video generation. <VIDEO_GENERATION_REQUEST>{\"title\":\"E2E\",\"prompt\":\"make a video\",\"duration\":4,\"aspect\":\"9:16\",\"reference_images\":[]}</VIDEO_GENERATION_REQUEST>" }] } })}\n\n`,
        `data: ${JSON.stringify(completed)}\n\n`,
      ].join(""),
    });
  });
  await page.route(assetVideoPattern, async (route) => {
    await route.fulfill({ status: 200, contentType: "video/mp4", body: Buffer.from("00000018667479706d703432000000006d703432", "hex") });
  });
  return mock;
}

async function assertImageWorkspace(page) {
  await clickNav(page, "Images");
  const state = await workspaceState(page);
  assert.equal(state.activeNav, "Images");
  assert.equal(state.hasImageWorkspaceText, true, "Images should show the image generation workspace");
  assert.equal(state.imageAgentPanelCount, 1, "Images should render the image agent panel");
  assert.equal(state.openCodeAgentCount, 0, "Images should not render the OpenCode Agent panel");
  assert.equal(state.openCodeEntryCount, 0, "Images should not expose an OpenCode Agent entry button");
  assert.equal(state.shellClass.includes("is-main-expanded"), false, "Images should keep the three-column layout");
}

async function assertImagesAgentWorkspace(page) {
  const mock = await installMockAgentOwnedGeneration(page);
  await clickNav(page, "Images-Agent");
  const state = await workspaceState(page);
  assert.equal(state.activeNav, "Images-Agent");
  assert.equal(state.hasImageGrid, true, "Images-Agent should keep the Images media library in the center");
  assert.equal(state.hasImageWorkspaceText, false, "Images-Agent should not show the image generation workspace");
  assert.equal(state.imageAgentPanelCount, 1, "Images-Agent should render a right-side agent panel");
  assert.equal(state.openCodeAgentCount, 1, "Images-Agent should render the OpenCode Agent as a persistent panel");
  assert.equal(state.openCodeEntryCount, 0, "Images-Agent should not need a separate OpenCode Agent entry button");
  assert.equal(state.shellClass.includes("is-main-expanded"), false, "Images-Agent should keep the three-column layout");
  await visibleText(page, "Images-Agent");
  await page.waitForFunction(() => {
    const el = document.querySelector(".ual-opencode-agent-chat");
    const last = document.querySelector(".ual-opencode-agent-chat .ual-message:last-of-type");
    if (!el || !last || el.scrollHeight <= el.clientHeight) return false;
    const containerRect = el.getBoundingClientRect();
    const lastRect = last.getBoundingClientRect();
    return lastRect.bottom <= containerRect.bottom + 2 && lastRect.top >= containerRect.top - 2;
  }, null, { timeout: 10000 });
  assertModelToggle(await modelToggleTexts(page, ".ual-opencode-agent .ual-agent-model-toggle"), "Images-Agent");
  await assertNoSelect(page, ".ual-opencode-agent", "Images-Agent");
  const uploadReferenceButton = page.getByRole("button", { name: "Upload reference images" });
  await uploadReferenceButton.waitFor({ state: "visible", timeout: 10000 });
  assert.equal(await uploadReferenceButton.isEnabled(), true, "Images-Agent should expose an enabled reference upload entry");
  const consistencyButton = page.getByRole("button", { name: "Load consistency reference images" });
  await consistencyButton.waitFor({ state: "visible", timeout: 10000 });
  assert.equal(await consistencyButton.isEnabled(), true, "Images-Agent should expose consistency reference loading");
  await consistencyButton.click();
  await page.getByRole("dialog", { name: "Consistency reference picker" }).waitFor({ state: "visible", timeout: 10000 });
  await page.getByRole("button", { name: "Close consistency reference picker" }).click();
  await page.getByRole("dialog", { name: "Consistency reference picker" }).waitFor({ state: "hidden", timeout: 10000 });
  const removeReferenceButtons = page.locator(".ual-opencode-agent .ual-composer-reference button[aria-label='Remove reference']");
  while (await removeReferenceButtons.count()) {
    await removeReferenceButtons.first().click();
    await page.waitForTimeout(50);
  }
  await page.locator(".ual-grid-wrap[aria-label='Images'] .ual-card-image").first().click();
  await page.locator(".ual-opencode-agent .ual-composer-reference").first().waitFor({ state: "visible", timeout: 10000 });
  assert.equal(await page.locator(".ual-opencode-agent .ual-composer-reference").count(), 1, "Images-Agent should preview selected reference images");
  await removeReferenceButtons.first().click();
  await page.waitForFunction(() => document.querySelectorAll(".ual-opencode-agent .ual-composer-reference").length === 0);
  await page.locator(".ual-grid-wrap[aria-label='Images'] .ual-card-image").first().click();
  await page.locator(".ual-opencode-agent .ual-composer-reference").first().waitFor({ state: "visible", timeout: 10000 });
  assert.equal(await page.getByRole("button", { name: "Generate image from Images-Agent prompt" }).count(), 0, "Images-Agent should use chat, not a separate image generation button");
  const sendButton = page.getByRole("button", { name: "Send to OpenCode Agent" });
  await page.locator(".ual-opencode-agent textarea").fill("请生成一张 E2E direct image generation prompt");
  assert.equal(await sendButton.isEnabled(), true, "Images-Agent send button should enable for a generation request");
  await sendButton.click();
  await page.locator(`.ual-card-image[title="${mock.asset.label}"]`).waitFor({ state: "visible", timeout: 10000 });
  await page.getByText(`已经生成并保存到 Upload：${mock.asset.filename}`, { exact: true }).first().waitFor({ state: "visible", timeout: 10000 });
  await page.locator(`.ual-opencode-agent img.ual-message-image[src*="${mock.asset.filename}"]`).waitFor({ state: "visible", timeout: 10000 });
  await page.waitForFunction((filename) => {
    const cards = Array.from(document.querySelectorAll(".ual-opencode-agent .ual-result-card"));
    return cards.filter((card) => card.innerText.includes(filename)).length === 1;
  }, mock.asset.filename, { timeout: 10000 });
  assert.equal(
    await page.locator(".ual-opencode-agent .ual-result-card").filter({ hasText: mock.asset.filename }).count(),
    1,
    "Images-Agent should render each generated asset once after event and asset-list reconciliation",
  );
  assert.equal(mock.requestPayload?.intent || "", "", "Images-Agent should let the chat message drive generation without a separate UI generation intent");
  assert.ok(
    Array.isArray(mock.requestPayload?.reference_images) && mock.requestPayload.reference_images.length >= 1,
    "Images-Agent Agent generation intent should send selected reference images",
  );
  assert.ok(
    mock.requestPayload.reference_images.some((item) => item?.role === "TARGET_FRAME" && item?.path),
    `Images-Agent should send role-aware reference images with a TARGET_FRAME, got ${JSON.stringify(mock.requestPayload.reference_images)}`,
  );
  assert.equal(mock.legacyGenerateCalled, false, "Images-Agent should not call the legacy image generation API directly");
  for (const pattern of mock.patterns) await page.unroute(pattern);
}

async function assertVideosWorkspace(page) {
  await clickNav(page, "Videos");
  const state = await workspaceState(page);
  assert.equal(state.activeNav, "Videos");
  assert.equal(state.hasImageWorkspaceText, false, "Videos should not show the image generation workspace");
  assert.equal(state.videoAgentPanelCount, 1, "Videos should render a persistent right-side video workspace");
  assert.equal(state.panelAgentDrawerCount, 0, "Videos should not render the StoryBoard drawer panel");
  assert.equal(state.hasVideoGenerationWorkspace, true, "Videos should expose the video generation workspace");
  assert.equal(state.hasVideoWorkspaceLibrary, true, "Videos should use the split video workspace library");
  assert.equal(state.hasVideoWorkspaceVideos, true, "Videos should show the upper Videos section");
  assert.equal(state.hasVideoWorkspaceImages, true, "Videos should show the lower Images section");
  assert.equal(state.shellClass.includes("is-main-expanded"), false, "Videos should keep the three-column layout");
  assert.equal(/^Agent\b/.test(state.mediaActionsText), false, "Videos should not expose the old inline Agent action");
  assert.equal(await page.getByRole("button", { name: "Upload Video" }).count(), 0, "Videos should not show Upload Video in the split workspace");
  assert.equal(await page.getByRole("button", { name: "Upload Image" }).count(), 0, "Videos should not show Upload Image in the split workspace");
}

async function assertVideosAgentWorkspace(page) {
  const mock = await installMockVideoAgentGeneration(page);
  await clickNav(page, "Videos-Agent");
  const state = await workspaceState(page);
  assert.equal(state.activeNav, "Videos-Agent");
  assert.equal(state.hasVideoWorkspaceLibrary, true, "Videos-Agent should keep the split Videos/Images library in the center");
  assert.equal(state.hasVideoWorkspaceVideos, true, "Videos-Agent should show the upper Videos section");
  assert.equal(state.hasVideoWorkspaceImages, true, "Videos-Agent should show the lower Images section");
  assert.equal(state.hasImageWorkspaceText, false, "Videos-Agent should not show the image generation workspace");
  assert.equal(state.videoAgentPanelCount, 1, "Videos-Agent should render a persistent right-side agent panel");
  assert.equal(state.panelAgentDrawerCount, 0, "Videos-Agent should not render the StoryBoard drawer panel");
  assert.equal(state.shellClass.includes("is-main-expanded"), false, "Videos-Agent should keep the three-column layout");
  await visibleText(page, "Videos-Agent");
  assertModelToggle(await modelToggleTexts(page, ".ual-video-agent .ual-video-agent-model-toggle"), "Videos-Agent");
  await assertNoSelect(page, ".ual-video-agent", "Videos-Agent");
  const panelText = await page.locator(".ual-video-agent").innerText();
  assert.ok(panelText.includes("WORKSPACE"), "Videos-Agent should include workspace context chips");
  assert.ok(panelText.includes("Videos"), "Videos-Agent should identify the Videos workspace");
  await page.locator('.ual-video-stateful[aria-label="有状态视频版本"]').waitFor({ state: "visible", timeout: 10000 });
  await visibleText(page, "当前版本 1");
  const statefulPanelText = await page.locator(".ual-video-agent").innerText();
  assert.ok(statefulPanelText.includes("每次生成或继续编辑都会产生一次新的付费调用"), "stateful video should disclose per-action billing");
  assert.ok(statefulPanelText.includes("云端会保存编辑上下文"), "stateful video should disclose cloud context storage");
  assert.equal(await page.getByRole("button", { name: "继续编辑" }).isEnabled(), true, "restored successful head should allow continuation");
  await page.locator(".ual-video-agent textarea").fill("Generate an E2E direct video generation prompt");
  await page.getByRole("button", { name: "Send to Videos-Agent" }).click();
  await page.getByRole("alertdialog", { name: "确认生成视频" }).waitFor({ state: "visible", timeout: 10000 });
  await page.getByRole("alertdialog", { name: "确认生成视频" }).getByRole("button", { name: "生成" }).click();
  await page.locator(`.ual-video-card .ual-card-image[title="${mock.asset.label}"]`).waitFor({ state: "visible", timeout: 10000 });
  await page.getByText(`已经生成并保存到 Videos：${mock.asset.filename}`, { exact: true }).first().waitFor({ state: "visible", timeout: 10000 });
  await page.locator(`.ual-video-agent video.ual-message-video[src*="${mock.asset.filename}"]`).waitFor({ state: "visible", timeout: 10000 });
  assert.equal(mock.requestPayload?.client_context?.media_kind, "video", "Videos-Agent should send video asset context to the agent");
  const generationSettings = mock.requestPayload?.client_context?.video_generation_settings || {};
  assert.equal(generationSettings.stateful, true, "stateful model should mark the paid action as stateful");
  assert.equal(generationSettings.operation, "continue", "restored agent chain should continue from the latest successful turn");
  assert.equal(generationSettings.video_thread_id, "e2e-video-thread", "browser should submit the OpenCrew thread id");
  assert.equal(generationSettings.parent_turn_id, "e2e-video-turn-1", "browser should submit the OpenCrew parent turn id");
  assert.match(generationSettings.client_action_id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i, "each explicit paid action should receive a UUIDv4 idempotency key");
  assert.equal("previous_interaction_id" in generationSettings, false, "browser must never submit a provider interaction id");
  for (const pattern of mock.patterns) await page.unroute(pattern);
}

async function assertStatefulVideosAgentBrowserContract(page) {
  const mock = await installMockVideoAgentGeneration(page);
  await clickNav(page, "视频生成");
  await page.locator(".ual-video-agent").waitFor({ state: "visible", timeout: 15000 });
  await capture(page, "11-omni-video-workspace.png");
  await clickNav(page, "视频智能体");
  const panel = page.locator(".ual-video-agent");
  await panel.waitFor({ state: "visible", timeout: 15000 });
  await panel.locator('.ual-video-stateful[aria-label="有状态视频版本"]').waitFor({ state: "visible", timeout: 15000 });
  assert.equal(await panel.getByText("尚未创建版本", { exact: true }).isVisible(), true, "a newly created agent chat must not inherit an unrelated task chain");
  await capture(page, "12-omni-new-chat.png");
  await page.evaluate(() => {
    window.dispatchEvent(new CustomEvent("koubo-storyboard:continue-video-version", {
      detail: {
        asset: {
          video_thread_id: "e2e-video-thread",
          video_turn_id: "e2e-video-turn-1",
        },
      },
    }));
  });
  await panel.getByText("当前版本 1", { exact: true }).waitFor({ state: "visible", timeout: 15000 });
  assert.ok(mock.currentInteractionRequests >= 1, "choosing a historical version should load its server-owned OpenCrew thread");
  const panelText = await panel.innerText();
  assert.ok(panelText.includes("每次生成或继续编辑都会产生一次新的付费调用"), "stateful video should disclose per-action billing");
  assert.ok(panelText.includes("云端会保存编辑上下文"), "stateful video should disclose cloud context storage");
  assert.ok(panelText.includes("不可移除的来源水印"), "stateful video should disclose the retained source watermark");
  assert.equal(await panel.getByRole("button", { name: "继续编辑", exact: true }).isEnabled(), true, "restored successful head should allow continuation");
  await capture(page, "13-omni-version-tree.png");

  await panel.locator("textarea").fill("Continue the E2E video from the restored OpenCrew version");
  await panel.getByRole("button", { name: "发送到视频智能体", exact: true }).click();
  const confirmation = panel.getByRole("alertdialog", { name: "确认生成视频" });
  await confirmation.waitFor({ state: "visible", timeout: 10000 });
  assert.ok((await confirmation.innerText()).includes("供应商状态标识不会发送到浏览器或模型"));
  await capture(page, "14-omni-paid-confirmation.png");
  await confirmation.getByRole("button", { name: "生成", exact: true }).click();

  const deadline = Date.now() + 10000;
  while (!mock.requestPayload && Date.now() < deadline) await page.waitForTimeout(50);
  assert.ok(mock.requestPayload, "confirmed stateful action should reach the mocked agent endpoint");
  const generationSettings = mock.requestPayload?.client_context?.video_generation_settings || {};
  assert.equal(generationSettings.stateful, true, "stateful model should mark the paid action as stateful");
  assert.equal(generationSettings.operation, "continue", "restored agent chain should continue from the latest successful turn");
  assert.equal(generationSettings.video_thread_id, "e2e-video-thread", "browser should submit only the OpenCrew thread id");
  assert.equal(generationSettings.parent_turn_id, "e2e-video-turn-1", "browser should submit only the OpenCrew parent turn id");
  assert.match(generationSettings.client_action_id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i, "each explicit paid action should receive a UUIDv4 idempotency key");
  assert.equal("previous_interaction_id" in generationSettings, false, "browser must never submit a provider interaction id");
  assert.equal(JSON.stringify(mock.requestPayload).includes("previous_interaction_id"), false, "agent request must not leak a provider interaction field anywhere");
  await page.waitForTimeout(500);
  await capture(page, "15-omni-request-dispatched.png");
  for (const pattern of mock.patterns) await page.unroute(pattern);
}

async function assertAudioWorkspace(page, navLabel, uploadLabel) {
  await clickNav(page, navLabel);
  const state = await workspaceState(page);
  assert.equal(state.activeNav, navLabel);
  assert.equal(state.hasImageWorkspaceText, false, `${navLabel} should not show the image generation workspace`);
  assert.equal(state.imageAgentPanelCount, 0, `${navLabel} should not render the image agent panel`);
  assert.equal(state.shellClass.includes("is-main-expanded"), true, `${navLabel} should use the expanded two-column layout`);
  assert.match(state.mediaActionsText, /^Agent\b/, `${navLabel} should expose a media Agent action`);
  assert.ok(state.mediaActionsText.includes(uploadLabel), `${navLabel} should expose ${uploadLabel}`);
}

async function assertMediaAgent(page, navLabel, agentTitle) {
  await clickNav(page, navLabel);
  await page.locator(".ual-media-actions button").filter({ hasText: exactText("Agent") }).click();
  const drawer = page.locator(".kbsp-agent-drawer", { hasText: agentTitle });
  await drawer.waitFor({ state: "visible", timeout: 15000 });
  await visibleText(page, agentTitle);
  assertModelToggle(await modelToggleTexts(page, ".kbsp-agent-model-toggle"), `${agentTitle}`);
  await assertNoSelect(page, ".kbsp-agent-drawer", agentTitle);
  const drawerText = await drawer.innerText();
  assert.ok(drawerText.includes("WORKSPACE"), `${agentTitle} should include workspace context chips`);
  assert.ok(drawerText.includes(navLabel), `${agentTitle} should identify the ${navLabel} workspace`);
  assert.equal((await workspaceState(page)).hasImageWorkspaceText, false, `${agentTitle} should not resurrect the image workspace`);
  await page.getByRole("button", { name: "关闭 Agent" }).click();
  await drawer.waitFor({ state: "hidden", timeout: 10000 });
}

async function assertHistoryWorkspace(page) {
  await clickNav(page, "History");
  const state = await workspaceState(page);
  assert.equal(state.activeNav, "History");
  assert.equal(state.hasImageWorkspaceText, false, "History should not show the image generation workspace");
  assert.equal(state.imageAgentPanelCount, 0, "History should not render the image agent panel");
  assert.equal(state.shellClass.includes("is-main-expanded"), true, "History should use the expanded two-column layout");
}

async function run() {
  const { chromium } = loadPlaywright();
  const session = await openBrowser(chromium);
  const checks = STATEFUL_VIDEO_ONLY ? [
    ["browser: stateful Videos-Agent contract", () => assertStatefulVideosAgentBrowserContract(session.page)],
  ] : [
    ["workspace: images panel", () => assertImageWorkspace(session.page)],
    ["workspace: images-agent panel", () => assertImagesAgentWorkspace(session.page)],
    ["workspace: videos panel", () => assertVideosWorkspace(session.page)],
    ["workspace: videos-agent panel", () => assertVideosAgentWorkspace(session.page)],
    ["workspace: audio panel", () => assertAudioWorkspace(session.page, "Audio", "Upload Audio")],
    ["agent: audio drawer", () => assertMediaAgent(session.page, "Audio", "Audio Agent")],
    ["workspace: history panel", () => assertHistoryWorkspace(session.page)],
  ];

  try {
    console.log(`asset-library-agents: opening ${ASSET_LIBRARY_URL} (${session.mode})`);
    await navigateToAssetLibrary(session.page, session.mode);
    for (const [label, check] of checks) {
      await check();
      console.log(`ok - ${label}`);
    }
  } finally {
    await session.browser.close();
  }
}

run().catch((error) => {
  console.error(`not ok - ${error?.message || error}`);
  if (error?.stack) console.error(error.stack);
  process.exit(1);
});
