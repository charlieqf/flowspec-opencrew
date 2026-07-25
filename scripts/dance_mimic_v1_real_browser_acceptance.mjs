#!/usr/bin/env node
import assert from "node:assert/strict";
import { existsSync, mkdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { join } from "node:path";

const require = createRequire(import.meta.url);
const { chromium } = require("../frontend/node_modules/playwright");

const REPO_ROOT = "/Users/macmini-4/work/code/OpenCrew";
const FRONTEND_URL = (process.env.OPENCREW_E2E_FRONTEND_URL || "http://127.0.0.1:18080").replace(/\/$/, "");
const runId = process.env.OPENCREW_E2E_RUN_ID || `${Date.now()}`;
const workDir = process.env.OPENCREW_E2E_RESULT_DIR || `/private/tmp/opencrew-dm-business-loop-${runId}`;
const screenshotDir = join(workDir, "screenshots");
const targetIdentityImage = process.env.OPENCREW_DANCE_MIMIC_TARGET_IMAGE
  || `${REPO_ROOT}/ToolLibrary/DanceMimic_V1/test_fixtures/target_ai_digital_human_avatar.png`;
const targetImageMode = process.env.OPENCREW_DANCE_MIMIC_TARGET_IMAGE_MODE || "upload_once";
const referenceVideoMode = process.env.OPENCREW_DANCE_MIMIC_REFERENCE_VIDEO_MODE || "library";

mkdirSync(screenshotDir, { recursive: true });

const fixtures = [
  {
    key: "frontal",
    role: "干净正脸基线(动作小)",
    title: `DanceMimic Business Frontal ${runId}`,
    sourceVideo: `${REPO_ROOT}/ToolLibrary/DanceMimic_V1/test_fixtures/dance_solo_frontal_studio.mp4`,
    targetSeconds: "8",
    minimumSeconds: "4",
  },
  {
    key: "bigmotion",
    role: "纯白影棚,大动作压力",
    title: `DanceMimic Business BigMotion ${runId}`,
    sourceVideo: `${REPO_ROOT}/ToolLibrary/DanceMimic_V1/test_fixtures/dance_solo_bigmotion_studio.mp4`,
    targetSeconds: "8",
    minimumSeconds: "4",
  },
  {
    key: "dance1",
    role: "单人正脸,城市夜景,真实编舞",
    title: `DanceMimic Business Dance1 ${runId}`,
    sourceVideo: `${REPO_ROOT}/ToolLibrary/DanceMimic_V1/test_fixtures/dance1.mp4`,
    targetSeconds: "8",
    minimumSeconds: "4",
  },
];
const fixtureFilter = (process.env.OPENCREW_DANCE_MIMIC_FIXTURES || "")
  .split(",")
  .map((item) => item.trim())
  .filter(Boolean);
const selectedFixtures = fixtureFilter.length
  ? fixtures.filter((fixture) => fixtureFilter.includes(fixture.key))
  : fixtures;
assert.ok(selectedFixtures.length > 0, `No DanceMimic fixtures selected by OPENCREW_DANCE_MIMIC_FIXTURES=${process.env.OPENCREW_DANCE_MIMIC_FIXTURES || ""}`);

function readEnvFile(path) {
  const env = {};
  if (!existsSync(path)) return env;
  const text = readFileSync(path, "utf-8");
  for (const line of text.split(/\r?\n/)) {
    const match = line.match(/^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)\s*$/);
    if (!match) continue;
    let value = match[2].trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    env[match[1]] = value;
  }
  return env;
}

async function screenshot(page, name) {
  const path = join(screenshotDir, `${name}.png`);
  await page.screenshot({ path, fullPage: true });
  return path;
}

async function api(page, path, options = {}) {
  return await page.evaluate(async ({ path, options }) => {
    const response = await fetch(path, {
      credentials: "include",
      headers: { "content-type": "application/json", ...(options.headers || {}) },
      ...options,
      body: options.body && typeof options.body !== "string" ? JSON.stringify(options.body) : options.body,
    });
    const text = await response.text();
    let payload = {};
    try {
      payload = text ? JSON.parse(text) : {};
    } catch {
      payload = { text };
    }
    return { status: response.status, ok: response.ok, payload };
  }, { path, options });
}

async function loginIfNeeded(page, password) {
  await page.goto(`${FRONTEND_URL}/?dmBusinessLoop=${runId}#/koubo-tasks`, { waitUntil: "domcontentloaded" });
  const passwordInput = page.locator('input[type="password"]').first();
  try {
    await passwordInput.waitFor({ state: "visible", timeout: 5000 });
    await passwordInput.fill(password);
    await page.getByRole("button", { name: /Sign in|Create password/ }).click();
  } catch {
    // Already authenticated.
  }
  await page.getByRole("heading", { name: /任务列表（口播）/ }).waitFor({ state: "visible", timeout: 30000 });
}

async function visibleErrorBanners(page) {
  return await page.locator(".banner.bad, .analysis-v1-banner.bad, .kbsp-banner.bad, .kbsp-error, .openflow-error").evaluateAll((nodes) => {
    return nodes.map((node) => (node.innerText || "").trim()).filter(Boolean);
  });
}

function allDialogues(plan) {
  const result = [];
  for (const shot of plan?.shots || []) {
    for (const scene of shot.scenes || []) {
      for (const dialogue of scene.dialogues || scene.dialogue_items || []) {
        result.push(dialogue);
      }
    }
  }
  return result;
}

function allSegments(plan) {
  const result = [];
  for (const shot of plan?.shots || []) {
    for (const scene of shot.scenes || []) {
      for (const segment of scene.segments || []) {
        result.push(segment);
      }
    }
  }
  return result;
}

function workspacePath(workspace, relPath) {
  if (!relPath) return "";
  if (relPath.startsWith("/")) return relPath;
  return join(workspace, relPath);
}

async function waitForDanceMimicCompleted(page, fixtureKey) {
  await page.waitForFunction(() => {
    const bodyText = document.body.innerText || "";
    if (!bodyText.includes("已完成")) return false;
    const required = ["Variables.json", "参考源视频", "目标人物图片", "参考媒体 manifest", "遮脸分段 manifest", "SRT StoryBoard", "StoryBoard seed"];
    const cards = Array.from(document.querySelectorAll(".dmv1-artifact")).map((node) => node.innerText || "");
    return required.every((label) => cards.some((text) => text.includes(label) && text.includes("存在")));
  }, null, { timeout: 900000 });
  await screenshot(page, `${fixtureKey}-04-dance-mimic-completed-artifacts`);
}

async function selectTargetIdentityImage(page, preferredTargetPath, useUpload) {
  const picker = page.locator(".koubo-task-list-identity-picker");
  if (useUpload) {
    await picker.getByRole("button", { name: "上传", exact: true }).click();
    const uploadInput = picker.locator('.koubo-task-list-target-upload input[type="file"]').first();
    const [uploadResponse] = await Promise.all([
      page.waitForResponse((response) => response.url().includes("/api/dance-mimic-v1/target-images/upload") && response.request().method() === "POST", { timeout: 60000 }),
      uploadInput.setInputFiles(targetIdentityImage),
    ]);
    const payload = await uploadResponse.json();
    if (!uploadResponse.ok()) {
      throw new Error(`Target image upload failed ${uploadResponse.status()}: ${JSON.stringify(payload).slice(0, 2000)}`);
    }
    const selectedPath = payload?.target_identity_image_path || payload?.item?.path || payload?.item?.absolute_path || "";
    assert.ok(selectedPath, "Target image upload should return target_identity_image_path");
    const selectedFilename = selectedPath.split("/").pop();
    await picker.evaluate(async (root, { selectedPath, selectedFilename }) => {
      await new Promise((resolve, reject) => {
        const started = Date.now();
        const poll = () => {
          const selectedCard = Array.from(root.querySelectorAll(".koubo-task-list-target-card"))
            .some((card) => card.getAttribute("title") === selectedPath && card.classList.contains("is-selected"));
          const selectedText = root.querySelector(".koubo-task-list-target-selected")?.textContent || "";
          if (selectedCard || selectedText.includes(selectedFilename)) {
            resolve(true);
            return;
          }
          if (Date.now() - started > 30000) {
            reject(new Error("Timed out waiting for selected target image"));
            return;
          }
          setTimeout(poll, 100);
        };
        poll();
      });
    }, { selectedPath, selectedFilename });
    return selectedPath;
  }

  await picker.getByRole("button", { name: "素材库", exact: true }).click();
  await picker.locator(".koubo-task-list-target-card").first().waitFor({ state: "visible", timeout: 30000 });
  const selectedTargetPath = await picker.evaluate((root, targetPath) => {
    const cards = Array.from(root.querySelectorAll(".koubo-task-list-target-card"));
    const exact = cards.find((card) => (card.getAttribute("title") || "") === targetPath);
    const candidate = exact || cards.find((card) => (card.innerText || "").includes("AI 生成人物"));
    candidate?.click();
    return candidate?.getAttribute("title") || "";
  }, preferredTargetPath);
  assert.equal(selectedTargetPath, preferredTargetPath, "DanceMimic acceptance must select the configured AI-generated person from the asset library");
  return selectedTargetPath;
}

async function selectReferenceVideo(page, fixture, useUpload) {
  const picker = page.locator(".koubo-task-list-reference-picker");
  if (useUpload) {
    await picker.getByRole("button", { name: "上传", exact: true }).click();
    const uploadInput = picker.locator('.koubo-task-list-target-upload input[type="file"]').first();
    const [uploadResponse] = await Promise.all([
      page.waitForResponse((response) => response.url().includes("/api/dance-mimic-v1/reference-videos/upload") && response.request().method() === "POST", { timeout: 60000 }),
      uploadInput.setInputFiles(fixture.sourceVideo),
    ]);
    const payload = await uploadResponse.json();
    if (!uploadResponse.ok()) {
      throw new Error(`Reference video upload failed ${uploadResponse.status()}: ${JSON.stringify(payload).slice(0, 2000)}`);
    }
    const selectedPath = payload?.reference_video_path || payload?.item?.path || payload?.item?.absolute_path || "";
    assert.ok(selectedPath, "Reference video upload should return reference_video_path");
    const selectedFilename = selectedPath.split("/").pop();
    await picker.evaluate(async (root, { selectedPath, selectedFilename }) => {
      await new Promise((resolve, reject) => {
        const started = Date.now();
        const poll = () => {
          const selectedCard = Array.from(root.querySelectorAll(".koubo-task-list-reference-card"))
            .some((card) => card.getAttribute("title") === selectedPath && card.classList.contains("is-selected"));
          const selectedText = root.querySelector(".koubo-task-list-reference-selected")?.textContent || "";
          if (selectedCard || selectedText.includes(selectedFilename)) {
            resolve(true);
            return;
          }
          if (Date.now() - started > 30000) {
            reject(new Error("Timed out waiting for selected reference video"));
            return;
          }
          setTimeout(poll, 100);
        };
        poll();
      });
    }, { selectedPath, selectedFilename });
    return selectedPath;
  }

  await picker.getByRole("button", { name: "素材库", exact: true }).click();
  await picker.locator(".koubo-task-list-reference-card").first().waitFor({ state: "visible", timeout: 30000 });
  const selectedReferencePath = await picker.evaluate((root, sourceVideo) => {
    const cards = Array.from(root.querySelectorAll(".koubo-task-list-reference-card"));
    const exact = cards.find((card) => (card.getAttribute("title") || "") === sourceVideo);
    exact?.click();
    return exact?.getAttribute("title") || "";
  }, fixture.sourceVideo);
  assert.equal(selectedReferencePath, fixture.sourceVideo, "DanceMimic acceptance must select the configured reference dance video from the library");
  return selectedReferencePath;
}

async function createDanceMimicTask(page, fixture, preferredTargetPath, useUploadTarget, useUploadReference) {
  const shots = {};
  await page.goto(`${FRONTEND_URL}/?dmBusinessLoop=${runId}#/koubo-tasks`, { waitUntil: "domcontentloaded" });
  await page.getByRole("heading", { name: /任务列表（口播）/ }).waitFor({ state: "visible", timeout: 30000 });
  shots.listBeforeCreate = await screenshot(page, `${fixture.key}-01-task-list-before-create`);
  await page.getByRole("button", { name: "动作模拟" }).click();
  await page.getByRole("heading", { name: "DanceMimic 创建" }).waitFor({ state: "visible", timeout: 30000 });
  await page.getByLabel("任务名").fill(fixture.title);
  const selectedReferencePath = await selectReferenceVideo(page, fixture, useUploadReference);
  const selectedTargetPath = await selectTargetIdentityImage(page, preferredTargetPath, useUploadTarget);
  await page.getByLabel("目标分段秒数").fill(fixture.targetSeconds);
  await page.getByLabel("最小分段秒数").fill(fixture.minimumSeconds);
  await page.getByLabel("检测 manifest").fill("");
  await page.getByLabel("参考隐私模式").selectOption("provider_safe_outline");
  await page.getByLabel("无人脸时阻断").check();
  await page.getByLabel("创建后运行").check();
  shots.createModalFilled = await screenshot(page, `${fixture.key}-02-create-modal-filled-real-detector-target-image`);

  const createButton = page.getByRole("button", { name: "创建 DanceMimic", exact: true });
  await createButton.waitFor({ state: "visible", timeout: 30000 });
  await createButton.scrollIntoViewIfNeeded();
  assert.equal(await createButton.isDisabled(), false, "DanceMimic create button should be enabled after selecting a reference video and target person image");
  const [createResponse] = await Promise.all([
    page.waitForResponse((response) => response.url().includes("/api/dance-mimic-v1/tasks") && response.request().method() === "POST", { timeout: 60000 }),
    createButton.click(),
  ]);
  if (!createResponse.ok()) {
    throw new Error(`DanceMimic create failed ${createResponse.status()}: ${await createResponse.text()}`);
  }
  await page.waitForURL(/#\/dance-mimic\/tasks\/\d+/, { timeout: 30000 });
  const taskId = Number(new URL(page.url()).hash.match(/tasks\/(\d+)/)?.[1] || 0);
  assert.ok(taskId > 0, "DanceMimic task id not found in URL");
  shots.runPageStarted = await screenshot(page, `${fixture.key}-03-run-page-started`);
  await waitForDanceMimicCompleted(page, fixture.key);
  return { taskId, shots, selectedReferencePath, selectedTargetPath, referenceVideoMode: useUploadReference ? "upload" : "library", targetImageMode: useUploadTarget ? "upload" : "library" };
}

async function openStoryboardAndGeneratePlan(page, fixture, taskId) {
  const shots = {};
  await page.goto(`${FRONTEND_URL}/#/koubo-storyboard/tasks/${taskId}`, { waitUntil: "domcontentloaded" });
  await page.locator(".kbsp-editor").waitFor({ state: "visible", timeout: 60000 });
  await page.locator('textarea[data-kbsp-dialogue-textarea]').first().waitFor({ state: "visible", timeout: 30000 });
  await page.waitForTimeout(1500);
  assert.deepEqual(await visibleErrorBanners(page), [], "StoryBoard should open without visible error banners");
  assert.equal(await page.locator(".kbsp-media-final-video.has-media").count(), 0, "reference video must not be bound as Video_Final before execution");
  shots.storyboardOpened = await screenshot(page, `${fixture.key}-05-storyboard-opened-target-image-reference-visible`);

  const storyboard = await api(page, `/api/koubo-storyboard/tasks/${taskId}`);
  assert.equal(storyboard.status, 200);
  const dialogues = allDialogues(storyboard.payload.plan || {});
  assert.ok(dialogues.length > 0, "StoryBoard should contain DanceMimic dialogues");
  for (const dialogue of dialogues) {
    assert.ok(dialogue.dance_mimic?.reference_video_path, "DanceMimic dialogue must keep reference video metadata");
    assert.ok(dialogue.dance_mimic?.target_identity_image_path, "DanceMimic dialogue must keep target identity image metadata");
    assert.match(dialogue.working_assets?.images?.[0]?.path || "", /_Image_New\.(png|jpg|jpeg|webp)$/);
    assert.equal(dialogue.working_assets?.video?.path || "", "", "Reference video must not be pre-bound as Video_Final");
  }

  const responsePromise = page.waitForResponse((response) => response.url().includes(`/api/koubo-storyboard/tasks/${taskId}/video-plan`) && response.request().method() === "POST", { timeout: 300000 });
  await page.getByRole("button", { name: "生成计划", exact: true }).click();
  await page.getByRole("dialog", { name: "生成计划" }).waitFor({ state: "visible", timeout: 60000 });
  const planResponse = await responsePromise;
  const planPayload = await planResponse.json();
  if (!planResponse.ok()) {
    throw new Error(`VideoPlan generation failed ${planResponse.status()}: ${JSON.stringify(planPayload).slice(0, 2000)}`);
  }
  await page.getByLabel("生成计划指标").waitFor({ state: "visible", timeout: 30000 });
  const planText = await page.locator(".kbsp-vpm-backdrop").innerText();
  assert.match(planText, /新视频|Raw Video|生成计划/);
  assert.doesNotMatch(planText, /first_scene_missing_visual_source|dancemimic_first_frame_missing/);
  shots.videoPlanGenerated = await screenshot(page, `${fixture.key}-06-video-plan-generated-openrouter-reference-target`);

  const plan = planPayload.plan || {};
  const segments = allSegments(plan);
  assert.ok(segments.length > 0, "VideoPlan should contain executable DanceMimic segments");
  for (const segment of segments) {
    assert.notEqual(segment.status, "blocked", `DanceMimic segment must not be blocked: ${JSON.stringify(segment.blocked_reason || {})}`);
    assert.equal(segment.provider, "openrouter");
    assert.equal(segment.model, "bytedance/seedance-2.0");
    assert.equal(segment.reference_mode, "input_references");
    assert.equal(segment.prompt_template, "Video_SDR2V_DanceMimic.md");
    assert.match(segment.reference_video_path || "", /Reference_FaceMasked\.mp4$/);
    assert.match(segment.first_frame?.source_path || "", /_Image_New\.(png|jpg|jpeg|webp)$/);
    assert.doesNotMatch(segment.first_frame?.source_path || "", /Reference_FaceMasked\.mp4$/);
    assert.equal(Boolean(segment.tasks?.need_video), true);
    assert.equal(Boolean(segment.tasks?.need_lipsync), false);
  }
  assert.ok(planPayload.plan_hash, "VideoPlan hash should be available");
  return { shots, plan, planPayload, segmentCount: segments.length };
}

async function executeVideoPlanFromModal(page, fixture, taskId) {
  const shots = {};
  const executeButton = page.getByRole("button", { name: "执行生成计划" });
  await executeButton.waitFor({ state: "visible", timeout: 30000 });
  shots.beforeExecute = await screenshot(page, `${fixture.key}-07-before-execute-video-plan`);
  const executeResponsePromise = page.waitForResponse((response) => response.url().includes(`/api/koubo-storyboard/tasks/${taskId}/video-plan/execute`) && response.request().method() === "POST", { timeout: 60000 });
  await executeButton.click();
  const executeResponse = await executeResponsePromise;
  const executePayload = await executeResponse.json();
  if (!executeResponse.ok()) {
    throw new Error(`VideoPlan execute failed ${executeResponse.status()}: ${JSON.stringify(executePayload).slice(0, 2000)}`);
  }
  await page.waitForTimeout(2000);
  shots.executionQueued = await screenshot(page, `${fixture.key}-08-execution-queued-running`);

  const start = Date.now();
  let last = null;
  while (Date.now() - start < 60 * 60 * 1000) {
    const response = await api(page, `/api/koubo-storyboard/tasks/${taskId}/video-plan/execution`);
    if (response.status !== 200) {
      throw new Error(`VideoPlan execution poll failed ${response.status}: ${JSON.stringify(response.payload).slice(0, 500)}`);
    }
    last = response.payload;
    const status = String(last.execution_state?.status || "").trim();
    if (status.startsWith("completed")) {
      shots.executionCompleted = await screenshot(page, `${fixture.key}-09-execution-completed-modal`);
      return { shots, execution: last };
    }
    if (status === "failed" || status === "blocked") {
      shots.executionFailed = await screenshot(page, `${fixture.key}-09-execution-failed-modal`);
      throw new Error(`VideoPlan execution ${status}: ${JSON.stringify(last.execution_state?.error || last.execution_result || last).slice(0, 3000)}`);
    }
    await page.waitForTimeout(15000);
  }
  shots.executionTimeout = await screenshot(page, `${fixture.key}-09-execution-timeout-modal`);
  throw new Error(`Timed out waiting for VideoPlan execution. Last state: ${JSON.stringify(last?.execution_state || {}).slice(0, 1000)}`);
}

async function verifyFinalOutputsVisible(page, fixture, taskId, expectedSegments) {
  const shots = {};
  await page.goto(`${FRONTEND_URL}/#/koubo-storyboard/tasks/${taskId}`, { waitUntil: "domcontentloaded" });
  await page.locator(".kbsp-editor").waitFor({ state: "visible", timeout: 60000 });
  await page.waitForFunction((expected) => document.querySelectorAll(".kbsp-media-final-video.has-media").length >= expected, expectedSegments, { timeout: 120000 });
  shots.storyboardFinalVisible = await screenshot(page, `${fixture.key}-10-storyboard-final-video-visible`);

  const story = await api(page, `/api/koubo-storyboard/tasks/${taskId}`);
  assert.equal(story.status, 200);
  const dialogues = allDialogues(story.payload.plan || {});
  assert.ok(dialogues.length >= expectedSegments, "StoryBoard should expose generated dialogues");
  for (const dialogue of dialogues) {
    const finalVideo = dialogue.working_assets?.video?.path || "";
    assert.ok(finalVideo, `Dialogue ${dialogue.dialogue_asset_key} should have Video_Final path`);
    assert.notEqual(finalVideo, dialogue.dance_mimic?.reference_video_path || "reference-path");
  }

  await page.goto(`${FRONTEND_URL}/#/dance-mimic/tasks/${taskId}`, { waitUntil: "domcontentloaded" });
  await page.getByText("遮脸分段 manifest").waitFor({ state: "visible", timeout: 30000 });
  shots.danceMimicFinalArtifacts = await screenshot(page, `${fixture.key}-11-dance-mimic-final-artifacts`);

  const detail = await api(page, `/api/dance-mimic-v1/tasks/${taskId}`);
  assert.equal(detail.status, 200);
  const workspace = detail.payload.workspace_dir || "";
  const finalVideoPaths = dialogues.map((dialogue) => dialogue.working_assets?.video?.path || "").filter(Boolean);
  for (const relPath of finalVideoPaths) {
    const absolute = workspacePath(workspace, relPath);
    assert.ok(existsSync(absolute), `Final video file should exist: ${absolute}`);
    assert.ok(statSync(absolute).size > 0, `Final video file should be non-empty: ${absolute}`);
  }
  return { shots, story: story.payload, detail: detail.payload, finalVideoPaths };
}

const env = readEnvFile(`${REPO_ROOT}/.opencrew-e2e-auth.env`);
const password = process.env.OPENCREW_E2E_ADMIN_PASSWORD || env.OPENCREW_E2E_ADMIN_PASSWORD || process.env.OPENCREW_E2E_APP_PASSWORD || env.OPENCREW_E2E_APP_PASSWORD;
assert.ok(password, "OPENCREW_E2E_APP_PASSWORD is required");
assert.ok(existsSync(targetIdentityImage), `Target identity image does not exist: ${targetIdentityImage}`);
for (const fixture of selectedFixtures) {
  assert.ok(existsSync(fixture.sourceVideo), `Fixture video does not exist: ${fixture.sourceVideo}`);
}

const browser = await chromium.launch({ headless: process.env.OPENCREW_E2E_HEADED !== "1" });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
page.setDefaultTimeout(60000);
page.on("pageerror", (error) => console.error("[pageerror]", error.message));
page.on("console", (message) => {
  if (["error", "warning"].includes(message.type())) console.error(`[browser:${message.type()}]`, message.text());
});
page.on("dialog", async (dialog) => {
  await dialog.accept();
});

const summary = {
  ok: false,
  run_id: runId,
  frontend_url: FRONTEND_URL,
  work_dir: workDir,
  screenshot_dir: screenshotDir,
  target_identity_image_source: targetIdentityImage,
  target_image_mode: targetImageMode,
  reference_video_mode: referenceVideoMode,
  fixtures: [],
};

try {
  await loginIfNeeded(page, password);
  const config = await api(page, "/api/setup/media-models/video/config");
  const openrouterProvider = (config.payload?.providers || []).find((provider) => provider?.provider === "openrouter") || {};
  summary.video_provider_config = { status: config.status, openrouter: openrouterProvider };
  assert.equal(config.status, 200);
  assert.equal(Boolean(openrouterProvider?.has_api_key), true, "OpenRouter video key is required for real final video execution");

  let reusableTargetIdentityImage = "";
  for (const fixture of selectedFixtures) {
    const useUploadTarget = targetImageMode === "upload" || (targetImageMode === "upload_once" && !reusableTargetIdentityImage);
    const useUploadReference = referenceVideoMode === "upload";
    const preferredTargetPath = reusableTargetIdentityImage || targetIdentityImage;
    const fixtureSummary = {
      key: fixture.key,
      role: fixture.role,
      source_video: fixture.sourceVideo,
      reference_video_mode: useUploadReference ? "upload" : "library",
      target_identity_image_source: targetIdentityImage,
      target_identity_image_mode: useUploadTarget ? "upload" : "library",
      screenshots: {},
    };
    fixtureSummary.reference_privacy_mode = "provider_safe_outline";
    summary.fixtures.push(fixtureSummary);
    const created = await createDanceMimicTask(page, fixture, preferredTargetPath, useUploadTarget, useUploadReference);
    fixtureSummary.task_id = created.taskId;
    fixtureSummary.reference_video = created.selectedReferencePath;
    fixtureSummary.target_identity_image = created.selectedTargetPath;
    Object.assign(fixtureSummary.screenshots, created.shots);
    reusableTargetIdentityImage = created.selectedTargetPath;

    const detail = await api(page, `/api/dance-mimic-v1/tasks/${created.taskId}`);
    assert.equal(detail.status, 200);
    assert.equal(detail.payload.latest_run?.status, "completed");
    assert.equal(detail.payload.reference_video_path, created.selectedReferencePath);
    assert.equal(detail.payload.target_identity_image_path, created.selectedTargetPath);
    assert.equal(detail.payload.reference_privacy_mode, "provider_safe_outline");
    assert.equal(detail.payload.artifacts?.storyboard_ready, true);
    assert.equal(detail.payload.artifacts?.files?.target_identity_image?.exists, true);
    fixtureSummary.dance_mimic_artifacts = detail.payload.artifacts;

    const planResult = await openStoryboardAndGeneratePlan(page, fixture, created.taskId);
    Object.assign(fixtureSummary.screenshots, planResult.shots);
    fixtureSummary.video_plan_hash = planResult.planPayload.plan_hash;
    fixtureSummary.video_plan_summary = planResult.planPayload.summary;
    fixtureSummary.segment_count = planResult.segmentCount;

    const execResult = await executeVideoPlanFromModal(page, fixture, created.taskId);
    Object.assign(fixtureSummary.screenshots, execResult.shots);
    fixtureSummary.execution_state = execResult.execution.execution_state;
    fixtureSummary.execution_result = execResult.execution.execution_result;
    fixtureSummary.execution_artifact_status = execResult.execution.artifact_status;

    const visible = await verifyFinalOutputsVisible(page, fixture, created.taskId, planResult.segmentCount);
    Object.assign(fixtureSummary.screenshots, visible.shots);
    fixtureSummary.storyboard_url = `${FRONTEND_URL}/#/koubo-storyboard/tasks/${created.taskId}`;
    fixtureSummary.dance_mimic_url = `${FRONTEND_URL}/#/dance-mimic/tasks/${created.taskId}`;
    fixtureSummary.final_video_paths = visible.finalVideoPaths;
    fixtureSummary.final_detail_artifacts = visible.detail.artifacts;
  }

  summary.ok = true;
  writeFileSync(join(workDir, "summary.json"), JSON.stringify(summary, null, 2));
  console.log(JSON.stringify(summary, null, 2));
} catch (error) {
  try {
    summary.failure_screenshot = await screenshot(page, "failure");
  } catch {}
  summary.error = error?.stack || error?.message || String(error);
  writeFileSync(join(workDir, "failure.json"), JSON.stringify(summary, null, 2));
  console.error(JSON.stringify(summary, null, 2));
  process.exitCode = 1;
} finally {
  if (process.env.OPENCREW_E2E_KEEP_BROWSER !== "1") {
    await browser.close();
  }
}
