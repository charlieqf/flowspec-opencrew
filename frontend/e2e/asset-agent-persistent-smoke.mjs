#!/usr/bin/env node
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import zlib from "node:zlib";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const PLAYWRIGHT_FALLBACK = "/private/tmp/opencrew-playwright-runner/node_modules/playwright";
const REPO_ROOT = path.resolve(new URL("../..", import.meta.url).pathname);
const BASE_URL = (process.env.OPENCREW_E2E_FRONTEND_URL || "http://127.0.0.1:18080").replace(/\/$/, "");
const TASK_ID = process.env.OPENCREW_E2E_KOUBO_TASK_ID || "116";
const CDP_URL = process.env.OPENCREW_E2E_CDP_URL || "http://127.0.0.1:9224";
const APP_PASSWORD = process.env.OPENCREW_E2E_APP_PASSWORD || "";
const ALLOW_SETUP = process.env.OPENCREW_E2E_ALLOW_SETUP === "1";
const HEADLESS = process.env.OPENCREW_E2E_HEADLESS !== "0";
const SEND_AGENT_MESSAGES = process.env.OPENCREW_E2E_SEND_AGENT_MESSAGES === "1";
const DATA_DIR = process.env.OPENCREW_DATA_DIR || path.join(os.homedir(), ".opencrew");
const FFMPEG_PATH = process.env.OPENCREW_FFMPEG_PATH || path.join(REPO_ROOT, "ToolLibrary", ".bin", "ffmpeg");
const RUN_ID = process.env.OPENCREW_E2E_RUN_ID || new Date().toISOString().replace(/[-:TZ.]/g, "").slice(0, 14);
const RESULT_ROOT = path.join(REPO_ROOT, "test-results", "asset-agent-persistent", RUN_ID);
const REPORT_PATH = path.join(REPO_ROOT, "docs", "asset_agent_ui_automation_latest.md");
const ASSET_LIBRARY_URL = `${BASE_URL}/?e2ePersistentAssetAgents=${RUN_ID}#/koubo-asset-library/tasks/${TASK_ID}`;

function loadPlaywright() {
  for (const id of ["playwright", PLAYWRIGHT_FALLBACK]) {
    try {
      return require(id);
    } catch {
      // Try the next location.
    }
  }
  throw new Error("Playwright is not installed. Run `npm --prefix frontend install playwright`.");
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
    const reusable = context.pages().find((page) => !page.isClosed() && page.url().startsWith(BASE_URL));
    const page = reusable || await context.newPage();
    await page.setViewportSize({ width: 1440, height: 1000 }).catch(() => {});
    return { browser, context, page, ownsBrowser: false, mode: "cdp" };
  }
  const browser = await chromium.launch({ headless: HEADLESS });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  return { browser, context, page, ownsBrowser: true, mode: "launch" };
}

async function ensureAuthenticated(page) {
  await page.waitForLoadState("domcontentloaded", { timeout: 30000 }).catch(() => {});
  await page.waitForTimeout(500);
  const authGateVisible = await page.locator(".auth-gate").isVisible().catch(() => false);
  if (!authGateVisible) return;
  const heading = (await page.locator(".auth-gate h1").first().innerText().catch(() => "")).trim();
  if (!APP_PASSWORD) {
    throw new Error(`Authentication is required (${heading || "auth gate"}). Use CDP on ${CDP_URL} or set OPENCREW_E2E_APP_PASSWORD.`);
  }
  if (/Create admin password/i.test(heading) && !ALLOW_SETUP) {
    throw new Error("Refusing to create an admin password during e2e. Set OPENCREW_E2E_ALLOW_SETUP=1 to allow setup.");
  }
  await page.locator(".auth-gate input[type='password']").fill(APP_PASSWORD);
  await page.locator(".auth-gate button").filter({ hasText: /Sign in|Create password/ }).click();
  await page.locator(".auth-gate").waitFor({ state: "hidden", timeout: 15000 });
}

async function navigate(page) {
  await page.goto(ASSET_LIBRARY_URL, { waitUntil: "domcontentloaded", timeout: 30000 });
  await ensureAuthenticated(page);
  await page.getByText("Asset Library", { exact: true }).first().waitFor({ state: "visible", timeout: 30000 });
  await page.waitForLoadState("networkidle", { timeout: 10000 }).catch(() => {});
}

async function reloadAssetLibrary(page) {
  await page.reload({ waitUntil: "domcontentloaded", timeout: 30000 });
  await ensureAuthenticated(page);
  await page.getByText("Asset Library", { exact: true }).first().waitFor({ state: "visible", timeout: 30000 });
  await page.waitForLoadState("networkidle", { timeout: 10000 }).catch(() => {});
}

function exactText(value) {
  return new RegExp(`^${String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`);
}

async function clickNav(page, label) {
  const button = page.locator(".ual-nav button, .ual-sidebar-bottom button").filter({ hasText: exactText(label) }).first();
  await button.waitFor({ state: "visible", timeout: 15000 });
  await button.click();
  await page.waitForTimeout(500);
}

async function fetchJsonInPage(page, url, options = {}) {
  return await page.evaluate(async ({ url, options }) => {
    const response = await fetch(url, {
      credentials: "include",
      ...options,
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    });
    const text = await response.text();
    let data = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      data = text;
    }
    if (!response.ok) throw new Error(`${url} failed: ${response.status} ${typeof data === "string" ? data : JSON.stringify(data)}`);
    return data;
  }, { url, options });
}

function workspaceForSession(sessionId) {
  return path.join(DATA_DIR, "sessions", String(sessionId), "workspace");
}

function readJson(filePath, fallback) {
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch {
    return fallback;
  }
}

function writeJson(filePath, payload) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, `${JSON.stringify(payload, null, 2)}\n`);
}

function crc32(buffer) {
  let crc = ~0;
  for (const byte of buffer) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1));
  }
  return (~crc) >>> 0;
}

function pngChunk(type, data) {
  const typeBuffer = Buffer.from(type, "ascii");
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length, 0);
  const checksum = Buffer.alloc(4);
  checksum.writeUInt32BE(crc32(Buffer.concat([typeBuffer, data])), 0);
  return Buffer.concat([length, typeBuffer, data, checksum]);
}

function createPng(filePath, width = 640, height = 360) {
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0);
  header.writeUInt32BE(height, 4);
  header[8] = 8;
  header[9] = 2;
  const rows = [];
  for (let y = 0; y < height; y += 1) {
    const row = Buffer.alloc(1 + width * 3);
    for (let x = 0; x < width; x += 1) {
      const offset = 1 + x * 3;
      row[offset] = Math.round(45 + (x / width) * 150);
      row[offset + 1] = Math.round(80 + (y / height) * 120);
      row[offset + 2] = Math.round(210 - (x / width) * 80);
    }
    rows.push(row);
  }
  const png = Buffer.concat([
    Buffer.from("89504e470d0a1a0a", "hex"),
    pngChunk("IHDR", header),
    pngChunk("IDAT", zlib.deflateSync(Buffer.concat(rows))),
    pngChunk("IEND", Buffer.alloc(0)),
  ]);
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, png);
}

function createVideo(filePath, sourcePath = "") {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  if (fs.existsSync(FFMPEG_PATH)) {
    const args = fs.existsSync(sourcePath)
      ? [
        "-y",
        "-ss", "0",
        "-t", "2",
        "-i", sourcePath,
        "-an",
        "-vf", "scale=360:640:force_original_aspect_ratio=increase,crop=360:640",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        filePath,
      ]
      : [
        "-y",
        "-f", "lavfi",
        "-i", "color=c=#1f2937:size=360x640:rate=15:duration=1.2",
        "-f", "lavfi",
        "-i", "sine=frequency=440:duration=1.2",
        "-shortest",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        filePath,
      ];
    const result = spawnSync(FFMPEG_PATH, args, { encoding: "utf8" });
    if (result.status === 0 && fs.existsSync(filePath) && fs.statSync(filePath).size > 1000) return;
    throw new Error(`ffmpeg failed to create test video: ${result.stderr || result.stdout}`);
  }
  throw new Error(`ffmpeg not found at ${FFMPEG_PATH}; set OPENCREW_FFMPEG_PATH.`);
}

function upsertManifestAsset(workspace, asset) {
  const manifestPath = path.join(workspace, "SessionOutput", "storyboard", "koubo_storyboard_assets.json");
  const store = readJson(manifestPath, { assets: [] });
  const assets = Array.isArray(store.assets) ? store.assets : [];
  const next = assets.filter((item) => item?.path !== asset.path && item?.id !== asset.id);
  next.push(asset);
  writeJson(manifestPath, { assets: next, updated_at: Date.now() });
}

function seedPersistentAssets(taskDetail, imageSessionId, videoSessionId) {
  const sessionId = Number(taskDetail?.task?.session_id || taskDetail?.meta?.analysis_session_id || 0);
  assert.ok(sessionId, "task session_id is required to seed persistent assets");
  const workspace = workspaceForSession(sessionId);
  assert.ok(fs.existsSync(workspace), `workspace does not exist: ${workspace}`);
  const imageFilename = `${RUN_ID}_e2e_images_agent_persistent.png`;
  const videoFilename = `${RUN_ID}_e2e_videos_agent_persistent.mp4`;
  const imageRel = `SessionOutput/storyboard/assets/images/${imageFilename}`;
  const videoRel = `SessionOutput/storyboard/assets/videos/${videoFilename}`;
  createPng(path.join(workspace, imageRel));
  createVideo(path.join(workspace, videoRel), path.join(workspace, "SessionContext", "Video_Source.mp4"));
  const now = Date.now();
  const imageAsset = {
    id: imageRel,
    path: imageRel,
    label: `UI Automation Images-Agent fixture ${RUN_ID}`,
    filename: imageFilename,
    asset_type: "Image",
    kind: "image",
    source: "agent_generated",
    created_at: now,
    origin: {
      tool: "upload_asset_library_agent",
      request_id: `e2e_images_agent_${RUN_ID}`,
      prompt: `UI automation Images-Agent fixture ${RUN_ID}`,
      provider: "e2e",
      model: "persistent-smoke",
      chat_opencode_session_id: imageSessionId,
      agent_generation_id: `e2e-images-agent-${RUN_ID}`,
      test_run_id: RUN_ID,
    },
  };
  const videoAsset = {
    id: videoRel,
    path: videoRel,
    label: `UI Automation Videos-Agent fixture ${RUN_ID}`,
    filename: videoFilename,
    asset_type: "Video",
    kind: "video",
    source: "ui_test_fixture",
    created_at: now + 1,
    origin: {
      tool: "upload_asset_library_video_agent",
      request_id: `e2e_videos_agent_${RUN_ID}`,
      prompt: `UI automation Videos-Agent fixture ${RUN_ID}`,
      provider: "e2e",
      model: "persistent-smoke",
      chat_opencode_session_id: videoSessionId,
      agent_message_id: `e2e-video-agent-message-${RUN_ID}`,
      agent_generation_id: `e2e-videos-agent-${RUN_ID}`,
      test_run_id: RUN_ID,
    },
  };
  upsertManifestAsset(workspace, imageAsset);
  upsertManifestAsset(workspace, videoAsset);
  return { workspace, imageAsset, videoAsset };
}

function createReferenceUploadFiles() {
  const first = path.join(RESULT_ROOT, `${RUN_ID}_reference_a.png`);
  const second = path.join(RESULT_ROOT, `${RUN_ID}_reference_b.png`);
  createPng(first, 320, 240);
  createPng(second, 240, 320);
  return [first, second];
}

async function installJsonPostCapture(page, pattern) {
  const captures = [];
  const handler = async (route) => {
    const raw = route.request().postData() || "";
    try {
      captures.push(JSON.parse(raw || "{}"));
    } catch {
      captures.push({ raw });
    }
    await route.continue();
  };
  await page.route(pattern, handler);
  return {
    captures,
    stop: async () => page.unroute(pattern, handler),
  };
}

async function waitForText(page, text, timeout = 15000) {
  await page.getByText(text, { exact: false }).first().waitFor({ state: "visible", timeout });
}

async function screenshot(page, name, description, shots) {
  const filePath = path.join(RESULT_ROOT, `${String(shots.length + 1).padStart(2, "0")}-${name}.png`);
  await page.screenshot({ path: filePath, fullPage: false });
  shots.push({ filePath, description });
  return filePath;
}

async function waitForImageAgentReady(page) {
  await page.getByRole("dialog", { name: "Images-Agent" }).waitFor({ state: "visible", timeout: 20000 });
  await page.locator(".ual-opencode-agent textarea").waitFor({ state: "visible", timeout: 20000 });
}

async function waitForVideoAgentReady(page) {
  await page.locator(".kbsp-agent-drawer.is-panel", { hasText: "Videos Agent" }).waitFor({ state: "visible", timeout: 20000 });
  await page.locator(".kbsp-agent-drawer.is-panel textarea").waitFor({ state: "visible", timeout: 20000 });
}

async function pollChatForText(page, url, needle, timeout = 12000) {
  const started = Date.now();
  while (Date.now() - started < timeout) {
    const payload = await fetchJsonInPage(page, url).catch(() => null);
    const items = Array.isArray(payload?.items) ? payload.items : [];
    const found = items.some((message) => JSON.stringify(message).includes(needle));
    if (found) return true;
    await page.waitForTimeout(750);
  }
  return false;
}

async function uploadMultipleImageReferences(page) {
  const before = await page.locator(".ual-opencode-agent .ual-composer-reference").count();
  const files = createReferenceUploadFiles();
  await page.locator(".ual-opencode-agent input[type='file']").setInputFiles(files);
  await page.waitForFunction(
    (expected) => document.querySelectorAll(".ual-opencode-agent .ual-composer-reference").length >= expected,
    before + files.length,
    { timeout: 20000 },
  );
  return files.map((filePath) => path.basename(filePath));
}

async function sendImageAgentRecord(page, runId, options = {}) {
  if (!SEND_AGENT_MESSAGES) return false;
  const capture = await installJsonPostCapture(page, `**/api/koubo-storyboard/tasks/${TASK_ID}/asset-library-agent/chat/message`);
  const message = `E2E Images-Agent persistent UI test ${runId}: please reply E2E OK only. Do not generate images and do not output IMAGE_GENERATION_REQUEST.`;
  try {
    await page.locator(".ual-opencode-agent textarea").fill(message);
    await page.getByRole("button", { name: "Send to OpenCode Agent" }).click();
    await waitForText(page, message);
    const persisted = await pollChatForText(page, `/api/koubo-storyboard/tasks/${TASK_ID}/asset-library-agent/chat/messages`, message);
    const stop = page.getByRole("button", { name: "Stop OpenCode Agent" });
    if (await stop.isEnabled().catch(() => false)) await stop.click().catch(() => {});
    assert.equal(persisted, true, "Images-Agent user chat message should persist in OpenCode history");
    const payload = capture.captures.at(-1) || {};
    const references = Array.isArray(payload.reference_images) ? payload.reference_images : [];
    for (const filename of options.expectedReferenceFilenames || []) {
      assert.ok(
        references.some((item) => String(item?.path || "").includes(filename)),
        `Images-Agent payload should include uploaded reference ${filename}; got ${JSON.stringify(references)}`,
      );
    }
    assert.ok(references.length >= (options.minReferenceCount || 0), `Images-Agent payload should include at least ${options.minReferenceCount || 0} references`);
    return true;
  } finally {
    await capture.stop();
  }
}

async function sendVideoAgentRecord(page, runId) {
  if (!SEND_AGENT_MESSAGES) return false;
  const capture = await installJsonPostCapture(page, `**/api/koubo-storyboard/tasks/${TASK_ID}/agents/asset_video/chat/message`);
  const message = `E2E Videos-Agent persistent UI test ${runId}: please reply E2E OK only. Do not generate video and do not output VIDEO_GENERATION_REQUEST.`;
  try {
    await page.locator(".kbsp-agent-drawer.is-panel textarea").fill(message);
    await page.locator(".kbsp-agent-drawer.is-panel .kbsp-agent-compose-actions button").filter({ hasText: exactText("发送") }).click();
    await waitForText(page, message);
    const persisted = await pollChatForText(page, `/api/koubo-storyboard/tasks/${TASK_ID}/agents/asset_video/chat/messages`, message);
    const stop = page.locator(".kbsp-agent-drawer.is-panel .kbsp-agent-compose-actions button").filter({ hasText: exactText("停止") });
    if (await stop.isEnabled().catch(() => false)) await stop.click().catch(() => {});
    assert.equal(persisted, true, "Videos-Agent user chat message should persist in OpenCode history");
    const payload = capture.captures.at(-1) || {};
    assert.equal(payload?.client_context?.media_kind, "video", `Videos-Agent should send video context; got ${JSON.stringify(payload)}`);
    return true;
  } finally {
    await capture.stop();
  }
}

function writeReport({ shots, imageAsset, videoAsset, imageMessageSent, videoMessageSent, referenceFilenames }) {
  fs.mkdirSync(path.dirname(REPORT_PATH), { recursive: true });
  const rel = (filePath) => path.relative(path.dirname(REPORT_PATH), filePath).replaceAll(path.sep, "/");
  const imageReferenceStep = imageMessageSent
    ? `4. 通过 Images-Agent 对话框左下角的上传参考图入口一次上传多张参考图：${referenceFilenames.map((item) => `\`${item}\``).join("、")}，并断言发送给 Agent 的 \`reference_images\` 包含这些文件。`
    : `4. 通过 Images-Agent 对话框左下角的上传参考图入口一次上传多张参考图：${referenceFilenames.map((item) => `\`${item}\``).join("、")}，确认多图参考入口在真实页面可用。`;
  const body = [
    "# Asset Agent UI Automation Report",
    "",
    `Run ID: \`${RUN_ID}\``,
    `Task: \`${TASK_ID}\``,
    `URL: ${ASSET_LIBRARY_URL}`,
    "",
    "## 验证范围",
    "",
    "- Images-Agent：左侧导航入口、中间 Images 媒体库、右侧 Agent 对话框、模型切换、聊天记录、Agent 产出物回填。",
    "- Videos-Agent：左侧导航入口、中间 Videos 媒体库、右侧 Videos Agent 面板、模型切换、聊天记录、视频产出物回填。",
    "- 持久化：刷新/重新打开 Asset Library 后，自动化测试留下的聊天记录和产出物仍可见。",
    "- 默认不向真实 OpenCode 会话写入 E2E 用户消息；接口 payload 细节由 `test:e2e:asset-agents` 的 mock 用例覆盖，避免污染真实 task 聊天记录。",
    "",
    "## 自动化操作说明",
    "",
    "1. 打开 Asset Library 页面并进入 `Images-Agent`。",
    "2. 确认右侧是 `Images-Agent` 对话框，中间仍是 Images 媒体库。",
    `3. 创建并登记测试图片产出物：\`${imageAsset.path}\`。`,
    imageReferenceStep,
    imageMessageSent
      ? "5. 在 Images-Agent 对话框发送一条带 Run ID 的测试消息，并确认该消息进入 OpenCode 会话历史。"
      : "5. 跳过真实 Agent 消息发送，仅验证聊天区中的持久化产出物消息。",
    "6. 刷新页面后重新进入 `Images-Agent`，确认媒体库和聊天区仍显示测试产出物。",
    "7. 进入 `Videos-Agent`，确认右侧是 Videos Agent 面板，中间仍是 Videos 媒体库。",
    `8. 创建并登记测试视频产出物：\`${videoAsset.path}\`。`,
    videoMessageSent
      ? "9. 在 Videos-Agent 对话框发送一条带 Run ID 的测试消息，并确认该消息进入 OpenCode 会话历史，同时断言发送 payload 中 `client_context.media_kind` 为 `video`。"
      : "9. 跳过真实 Agent 消息发送，仅验证聊天区中的持久化产出物消息。",
    "10. 刷新页面后重新进入 `Videos-Agent`，确认媒体库和聊天区仍显示测试视频产出物。",
    "",
    "## 截图",
    "",
    ...shots.flatMap((shot, index) => [
      `### ${index + 1}. ${shot.description}`,
      "",
      `![${shot.description}](${rel(shot.filePath)})`,
      "",
    ]),
    "## 用户复核",
    "",
    `打开 ${ASSET_LIBRARY_URL}，进入左侧 \`Images-Agent\` 和 \`Videos-Agent\`，应能看到本次 Run ID \`${RUN_ID}\` 对应的聊天区产出物消息和媒体库产出物。`,
    "",
  ].join("\n");
  fs.writeFileSync(REPORT_PATH, body);
}

async function run() {
  fs.mkdirSync(RESULT_ROOT, { recursive: true });
  const { chromium } = loadPlaywright();
  const session = await openBrowser(chromium);
  const shots = [];
  let imageAsset;
  let videoAsset;
  let imageMessageSent = false;
  let videoMessageSent = false;
  let referenceFilenames = [];
  try {
    console.log(`asset-agent-persistent-smoke: opening ${ASSET_LIBRARY_URL} (${session.mode})`);
    await navigate(session.page);
    let taskDetail = await fetchJsonInPage(session.page, `/api/koubo-storyboard/tasks/${TASK_ID}`);

    await clickNav(session.page, "Images-Agent");
    await waitForImageAgentReady(session.page);
    const imageState = await fetchJsonInPage(session.page, `/api/koubo-storyboard/tasks/${TASK_ID}/asset-library-agent/chat/ensure-session`, { method: "POST", body: "{}" });

    await clickNav(session.page, "Videos-Agent");
    await waitForVideoAgentReady(session.page);
    const videoState = await fetchJsonInPage(session.page, `/api/koubo-storyboard/tasks/${TASK_ID}/agents/asset_video/chat/ensure-session`, { method: "POST", body: "{}" });

    const seeded = seedPersistentAssets(taskDetail, imageState.chat_opencode_session_id, videoState.chat_opencode_session_id);
    imageAsset = seeded.imageAsset;
    videoAsset = seeded.videoAsset;
    console.log(`seeded image: ${imageAsset.path}`);
    console.log(`seeded video: ${videoAsset.path}`);

    await reloadAssetLibrary(session.page);
    taskDetail = await fetchJsonInPage(session.page, `/api/koubo-storyboard/tasks/${TASK_ID}`);
    assert.ok(JSON.stringify(taskDetail.meta || {}).includes(imageAsset.filename), "seeded image should be visible in task meta");
    assert.ok(JSON.stringify(taskDetail.meta || {}).includes(videoAsset.filename), "seeded video should be visible in task meta");

    await clickNav(session.page, "Images-Agent");
    await waitForImageAgentReady(session.page);
    await session.page.locator(`.ual-card-image[title*="${RUN_ID}"]`).first().waitFor({ state: "visible", timeout: 20000 });
    await waitForText(session.page, `已经生成并保存到 Upload：${imageAsset.filename}`);
    await session.page.locator(`.ual-opencode-agent img.ual-message-image[src*="${imageAsset.filename}"]`).first().waitFor({ state: "visible", timeout: 20000 });
    referenceFilenames = await uploadMultipleImageReferences(session.page);
    await session.page.waitForTimeout(500);
    await screenshot(session.page, "images-agent-output-visible", "Images-Agent 页面：中间媒体库与右侧 Agent 产出消息均显示测试图片", shots);
    imageMessageSent = await sendImageAgentRecord(session.page, RUN_ID, {
      expectedReferenceFilenames: referenceFilenames,
      minReferenceCount: referenceFilenames.length,
    });
    await screenshot(
      session.page,
      "images-agent-chat-record",
      imageMessageSent
        ? "Images-Agent 页面：发送并保留带 Run ID 的测试聊天记录"
        : "Images-Agent 页面：多张参考图与测试图片产出消息保持可见",
      shots,
    );

    await clickNav(session.page, "Videos-Agent");
    await waitForVideoAgentReady(session.page);
    await session.page.locator(`.ual-media-card:has-text("${RUN_ID}")`).first().waitFor({ state: "visible", timeout: 20000 });
    await waitForText(session.page, `已经生成并保存到 Videos：${videoAsset.filename}`);
    await session.page.locator(`.kbsp-agent-drawer.is-panel video.kbsp-agent-video[src*="${videoAsset.filename}"]`).first().waitFor({ state: "visible", timeout: 20000 });
    await screenshot(session.page, "videos-agent-output-visible", "Videos-Agent 页面：中间媒体库与右侧 Agent 产出消息均显示测试视频", shots);
    videoMessageSent = await sendVideoAgentRecord(session.page, RUN_ID);
    await screenshot(
      session.page,
      "videos-agent-chat-record",
      videoMessageSent
        ? "Videos-Agent 页面：发送并保留带 Run ID 的测试聊天记录"
        : "Videos-Agent 页面：测试视频产出消息保持可见",
      shots,
    );

    await navigate(session.page);
    await clickNav(session.page, "Images-Agent");
    await waitForImageAgentReady(session.page);
    await waitForText(session.page, imageAsset.filename);
    if (imageMessageSent) await waitForText(session.page, `E2E Images-Agent persistent UI test ${RUN_ID}`);
    await screenshot(session.page, "reopen-images-agent-persistent", "重新打开后：Images-Agent 仍显示测试聊天记录和图片产出物", shots);

    await clickNav(session.page, "Videos-Agent");
    await waitForVideoAgentReady(session.page);
    await waitForText(session.page, videoAsset.filename);
    if (videoMessageSent) await waitForText(session.page, `E2E Videos-Agent persistent UI test ${RUN_ID}`);
    await screenshot(session.page, "reopen-videos-agent-persistent", "重新打开后：Videos-Agent 仍显示测试聊天记录和视频产出物", shots);

    writeReport({ shots, imageAsset, videoAsset, imageMessageSent, videoMessageSent, referenceFilenames });
    console.log(`ok - persistent UI smoke completed`);
    console.log(`report: ${REPORT_PATH}`);
    console.log(`screenshots: ${RESULT_ROOT}`);
  } finally {
    await session.browser.close().catch(() => {});
  }
}

run().catch((error) => {
  console.error(`not ok - ${error?.message || error}`);
  if (error?.stack) console.error(error.stack);
  process.exit(1);
});
