import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";

const require = createRequire(import.meta.url);
const PLAYWRIGHT_FALLBACK = "/private/tmp/opencrew-playwright-runner/node_modules/playwright";
const REPO_ROOT = path.resolve(new URL("../../..", import.meta.url).pathname);
const BASE_URL = (process.env.OPENCREW_E2E_FRONTEND_URL || "http://127.0.0.1:18080").replace(/\/$/, "");
const TASK_ID = Number(process.env.OPENCREW_E2E_KOUBO_TASK_ID || 9001);
const SESSION_ID = Number(process.env.OPENCREW_E2E_KOUBO_SESSION_ID || 19001);
const HEADLESS = process.env.OPENCREW_E2E_HEADLESS !== "0";
const RUN_ID = process.env.OPENCREW_E2E_RUN_ID || new Date().toISOString().replace(/[-:TZ.]/g, "").slice(0, 14);
const RESULT_DIR = process.env.OPENCREW_E2E_RESULT_DIR || path.join(REPO_ROOT, "test-results", "koubo-storyboard-regression", RUN_ID);
const REPORT_PATH = path.join(RESULT_DIR, "result.json");
const TEST_URL = `${BASE_URL}/?assetPanelTab=upload#/koubo-storyboard/tasks/${TASK_ID}`;
const PNG_1X1 = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=", "base64");

export const SAVE_BUTTON_SELECTOR = ".kbsp-workspace-head .kbsp-head-actions button[title='Save']";

function loadPlaywright() {
  for (const id of ["playwright", PLAYWRIGHT_FALLBACK]) {
    try {
      return require(id);
    } catch {
      // Try the next location.
    }
  }
  throw new Error("Playwright is not installed. Run `npm --prefix frontend install`.");
}

export function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function json(route, payload) {
  return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(payload) });
}

export function findDialogue(plan, dialogueId = "dlg_001") {
  for (const shot of plan.shots || []) {
    for (const scene of shot.scenes || []) {
      for (const dialogue of scene.dialogues || []) {
        if (dialogue.dialogue_id === dialogueId) return dialogue;
      }
    }
  }
  return null;
}

function workingAssets() {
  return {
    audio: { slot: "Audio_Final", source_type: "", path: "" },
    images: [
      { slot: "Image_New", source_type: "", path: "" },
      { slot: "Image_02", source_type: "", path: "" },
    ],
    video: { slot: "Video_Final", source_type: "", path: "" },
  };
}

function basePlan() {
  return {
    schema_version: "koubo_storyboard_edit_0.1",
    title: "E2E Koubo StoryBoard",
    shots: [{
      shot_id: "shot_001",
      shot_name: "Regression Shot",
      scenes: [{
        scene_id: "scene_001",
        scene_name: "Regression Scene",
        asset_key: "shot_001_scene_001",
        dialogues: [
          {
            dialogue_id: "dlg_001",
            dialogue_asset_key: "dak_001",
            srt_id: "srt_001",
            srt_ids: ["srt_001"],
            text: "真实 UI 回归：Raw 已存在但没有音频和终视频。",
            duration: 3.2,
            source_image_paths: [],
            image_path: "",
            bound_image_path: "",
            working_assets: workingAssets(),
          },
          {
            dialogue_id: "dlg_002",
            dialogue_asset_key: "dak_002",
            srt_id: "srt_002",
            srt_ids: ["srt_002"],
            text: "第二条对白用于确认 Scene 保持多 Dialogue。",
            duration: 2.4,
            source_image_paths: [],
            image_path: "",
            bound_image_path: "",
            working_assets: workingAssets(),
          },
        ],
      }],
    }],
  };
}

const task = {
  id: TASK_ID,
  session_id: SESSION_ID,
  status: "ready",
  title: "E2E Koubo StoryBoard",
  prompt_model_provider: "openai",
  prompt_model_id: "gpt-5.5",
  run_model_provider: "openai",
  run_model_id: "gpt-5.5",
};

export const uploadImage = {
  id: "upload-image-1",
  path: "SessionOutput/storyboard/assets/images/e2e_source.png",
  filename: "e2e_source.png",
  label: "E2E source image",
  asset_type: "Image",
  kind: "image",
  source: "upload",
};

export const uploadNewImage = {
  id: "upload-image-2",
  path: "SessionOutput/storyboard/assets/images/e2e_new.png",
  filename: "e2e_new.png",
  label: "E2E new image",
  asset_type: "Image",
  kind: "image",
  source: "upload",
};

export const uploadVideo = {
  id: "upload-video-1",
  path: "SessionOutput/storyboard/assets/videos/e2e_raw.mp4",
  filename: "e2e_raw.mp4",
  label: "E2E raw video",
  asset_type: "Video",
  kind: "video",
  source: "upload",
};

export const uploadedPoolImage = {
  id: "upload-image-pool",
  path: "SessionOutput/storyboard/assets/images/e2e_pool_upload.png",
  filename: "e2e_pool_upload.png",
  label: "E2E uploaded pool image",
  asset_type: "Image",
  kind: "image",
  source: "upload",
};

export const generatedSecondDialogueRawVideoPath = "SessionOutput/storyboard/Working/dak_002_Video_Raw.mp4";
export const generatedSecondDialogueFinalVideoPath = "SessionOutput/storyboard/Working/dak_002_Video_Final.mp4";

export let currentPlan = basePlan();
let uploadedImages = [uploadImage, uploadNewImage];
let uploadedAudios = [];
let uploadedVideos = [uploadVideo];
const rawVideoPathsByAssetKey = {
  dak_001: "SessionOutput/storyboard/Working/dak_001_Video_Raw.mp4",
  dak_002: "",
};
const finalVideoPathsByAssetKey = { dak_001: "", dak_002: "" };
const finalVideoBoundByAssetKey = { dak_001: false, dak_002: false };
export const calls = { bind: [], clear: [], upload: [], save: [], videoPlan: [], imagePlan: [], videoOnlyPlan: [], videoPlanResult: [], imagePlanResult: [], videoOnlyPlanResult: [] };

function firstNewImagePath() {
  return findDialogue(currentPlan, "dlg_001")?.working_assets?.images?.[0]?.path || "";
}

function resetCalls() {
  for (const bucket of Object.values(calls)) bucket.length = 0;
}

function resetState() {
  currentPlan = basePlan();
  uploadedImages = [uploadImage, uploadNewImage];
  uploadedAudios = [];
  uploadedVideos = [uploadVideo];
  rawVideoPathsByAssetKey.dak_001 = "SessionOutput/storyboard/Working/dak_001_Video_Raw.mp4";
  rawVideoPathsByAssetKey.dak_002 = "";
  finalVideoPathsByAssetKey.dak_001 = "";
  finalVideoPathsByAssetKey.dak_002 = "";
  finalVideoBoundByAssetKey.dak_001 = false;
  finalVideoBoundByAssetKey.dak_002 = false;
  resetCalls();
}

export function seedFullBindingState() {
  const first = findDialogue(currentPlan, "dlg_001");
  const second = findDialogue(currentPlan, "dlg_002");
  assert.ok(first, "dlg_001 should exist");
  assert.ok(second, "dlg_002 should exist");
  first.source_image_paths = [uploadImage.path];
  first.image_path = uploadImage.path;
  first.bound_image_path = uploadNewImage.path;
  first.working_assets = first.working_assets || workingAssets();
  first.working_assets.images = first.working_assets.images || workingAssets().images;
  first.working_assets.images[0] = { slot: "Image_New", source_type: "upload", path: uploadNewImage.path };
  rawVideoPathsByAssetKey.dak_002 = uploadVideo.path;
  finalVideoPathsByAssetKey.dak_002 = uploadVideo.path;
  finalVideoBoundByAssetKey.dak_002 = true;
  second.working_assets = second.working_assets || workingAssets();
  second.working_assets.video = { slot: "Video_Final", source_type: "upload", path: uploadVideo.path };
}

export function seedFinalUnboundState() {
  const first = findDialogue(currentPlan, "dlg_001");
  assert.ok(first, "dlg_001 should exist");
  first.source_image_paths = [uploadImage.path];
  first.image_path = uploadImage.path;
  finalVideoPathsByAssetKey.dak_001 = "SessionOutput/storyboard/Working/dak_001_Video_Final.mp4";
  finalVideoBoundByAssetKey.dak_001 = false;
}

export function seedMergeDialogueArchiveState() {
  seedFullBindingState();
  const second = findDialogue(currentPlan, "dlg_002");
  assert.ok(second, "dlg_002 should exist");
  rawVideoPathsByAssetKey.dak_002 = generatedSecondDialogueRawVideoPath;
  finalVideoPathsByAssetKey.dak_002 = generatedSecondDialogueFinalVideoPath;
  finalVideoBoundByAssetKey.dak_002 = true;
  second.working_assets = second.working_assets || workingAssets();
  second.working_assets.video = {
    slot: "Video_Final",
    source_type: "generated",
    path: generatedSecondDialogueFinalVideoPath,
  };
}

function videoSlotState() {
  const state = (assetKey) => {
    const rawVideoPath = rawVideoPathsByAssetKey[assetKey] || "";
    const finalVideoPath = finalVideoPathsByAssetKey[assetKey] || "";
    return {
      audio_exists: false,
      raw_video_exists: Boolean(rawVideoPath),
      raw_video_path: rawVideoPath,
      final_video_exists: Boolean(finalVideoPath),
      final_video_bound: Boolean(finalVideoBoundByAssetKey[assetKey]),
      final_video_path: finalVideoPath,
    };
  };
  const poisonedDialogueFallback = {
    audio_exists: false,
    raw_video_exists: false,
    raw_video_path: "SessionOutput/storyboard/Working/srt_001_poison_Video_Raw.mp4",
    final_video_exists: false,
    final_video_bound: false,
    final_video_path: "",
  };
  return {
    by_dialogue_id: { dlg_001: poisonedDialogueFallback, dlg_002: poisonedDialogueFallback },
    by_asset_key: { dak_001: state("dak_001"), dak_002: state("dak_002") },
  };
}

function meta() {
  return {
    analysis_session_id: SESSION_ID,
    uploaded_images: uploadedImages,
    uploaded_audios: uploadedAudios,
    uploaded_videos: uploadedVideos,
    manual_assets: [...uploadedImages, ...uploadedAudios, ...uploadedVideos],
    history_versions: [{
      version: "batch_e2e",
      reason: "regression history fixture",
      items: [{ ...uploadImage, id: "history-image-1", path: "SessionOutput/storyboard/assets/history/e2e_old.png" }],
    }],
    source_asset_groups: [{
      shot_id: "shot_001",
      duration: 5.6,
      scenes: [{ ...uploadImage, path: "SessionContext/source_frame_001.png", text: "source frame" }],
    }],
    video_plan_settings: { max_video_seconds: 4, min_video_seconds: 2, split_tolerance_seconds: 2 },
    storyboard_video_slots: videoSlotState(),
  };
}

function detail() {
  return { ok: true, task, meta: meta(), plan: clone(currentPlan) };
}

function applyBind(payload) {
  calls.bind.push(payload);
  currentPlan = clone(payload.plan || currentPlan);
  const dialogue = findDialogue(currentPlan, payload.dialogue_id);
  assert.ok(dialogue, `dialogue not found: ${payload.dialogue_id}`);
  const target = String(payload.target_kind || "");
  if (target === "source") {
    dialogue.source_image_paths = [payload.asset_path];
    dialogue.image_path = payload.asset_path;
  } else if (target === "image") {
    dialogue.bound_image_path = payload.asset_path;
    dialogue.working_assets = dialogue.working_assets || workingAssets();
    dialogue.working_assets.images = dialogue.working_assets.images || workingAssets().images;
    dialogue.working_assets.images[0] = { slot: "Image_New", source_type: "upload", path: payload.asset_path };
  } else if (target === "raw_video") {
    rawVideoPathsByAssetKey[dialogue.dialogue_asset_key] = payload.asset_path;
  } else if (target === "final_video") {
    finalVideoPathsByAssetKey[dialogue.dialogue_asset_key] = payload.asset_path;
    finalVideoBoundByAssetKey[dialogue.dialogue_asset_key] = true;
    dialogue.working_assets = dialogue.working_assets || workingAssets();
    dialogue.working_assets.video = { slot: "Video_Final", source_type: "upload", path: payload.asset_path };
  } else if (target === "audio") {
    dialogue.working_assets = dialogue.working_assets || workingAssets();
    dialogue.working_assets.audio = { slot: "Audio_Final", source_type: "upload", path: payload.asset_path };
  }
}

function applyClear(payload) {
  calls.clear.push(payload);
  currentPlan = clone(payload.plan || currentPlan);
  const dialogue = findDialogue(currentPlan, payload.dialogue_id);
  assert.ok(dialogue, `dialogue not found: ${payload.dialogue_id}`);
  const target = String(payload.target_kind || "");
  if (target === "source") {
    dialogue.source_image_paths = [];
    dialogue.image_path = "";
  } else if (target === "image") {
    dialogue.bound_image_path = "";
    dialogue.working_assets = dialogue.working_assets || workingAssets();
    dialogue.working_assets.images = dialogue.working_assets.images || workingAssets().images;
    dialogue.working_assets.images[0] = { slot: "Image_New", source_type: "", path: "" };
  } else if (target === "raw_video") {
    rawVideoPathsByAssetKey[dialogue.dialogue_asset_key] = "";
  } else if (target === "final_video") {
    finalVideoPathsByAssetKey[dialogue.dialogue_asset_key] = "";
    finalVideoBoundByAssetKey[dialogue.dialogue_asset_key] = false;
    dialogue.working_assets = dialogue.working_assets || workingAssets();
    dialogue.working_assets.video = { slot: "Video_Final", source_type: "", path: "" };
  } else if (target === "audio") {
    dialogue.working_assets = dialogue.working_assets || workingAssets();
    dialogue.working_assets.audio = { slot: "Audio_Final", source_type: "", path: "" };
  }
}

function applyUpload(request) {
  calls.upload.push({ method: request.method(), content_type: request.headers()["content-type"] || "" });
  if (!uploadedImages.some((item) => item.id === uploadedPoolImage.id)) uploadedImages = [...uploadedImages, uploadedPoolImage];
}

function videoPlanResult(payload) {
  const target = payload.target || { target_type: "scene", shot_id: "shot_001", scene_id: "scene_001" };
  const rawVideoPath = rawVideoPathsByAssetKey.dak_001 || "";
  const finalVideoPath = finalVideoPathsByAssetKey.dak_001 || "";
  const imagePath = firstNewImagePath();
  return {
    ok: true,
    task,
    target,
    settings: payload.settings || {},
    cache_status: "generated",
    binding_status: { state_matches_current_plan: true, result_matches_current_plan: true },
    execution_state: { status: "idle", segments: {} },
    plan: {
      plan_hash: `vp-${RUN_ID}`,
      target,
      summary: { shot_count: 1, scene_count: 1, segment_count: 1, blocked_scene_count: 0, skipped_scene_count: 0 },
      shots: [{
        shot_id: "shot_001",
        scenes: [{
          scene_id: "scene_001",
          segments: [{
            segment_id: "seg_001",
            segment_index: 1,
            asset_key: "dak_001",
            duration: 3.2,
            status: "ready",
            first_frame: { source_type: "bound_image", source_path: findDialogue(currentPlan).image_path || uploadImage.path },
            tasks: { need_audio: true, need_image: !imagePath, need_video: true, need_sync: true, need_lipsync: true, sync_mode: "lipsync" },
            planned_outputs: {
              segment_audio_path: "SessionOutput/storyboard/Working/dak_001_Audio_Final.wav",
              image_path: imagePath || "SessionOutput/storyboard/Working/dak_001_Image_New.png",
              video_path: rawVideoPath,
              final_video_path: "SessionOutput/storyboard/Working/dak_001_Video_Final.mp4",
            },
          }],
        }],
      }],
    },
    artifact_status: {
      segments: {
        seg_001: {
          audio_in_working: false,
          image_in_working: Boolean(imagePath),
          video_in_working: Boolean(rawVideoPath),
          sync_in_working: Boolean(finalVideoPath),
          slot_states: {
            audio: { ui_tone: "pending", reason: "need_audio" },
            image: { ui_tone: imagePath ? "done" : "pending", reason: imagePath ? "file_exists" : "need_first_frame" },
            raw_video: { ui_tone: rawVideoPath ? "done" : "pending", reason: rawVideoPath ? "file_exists" : "need_raw" },
            final_video: { ui_tone: "disabled", reason: "blocked_waiting_input" },
          },
        },
      },
    },
  };
}

function imagePlanResult(payload) {
  const target = payload.target || { target_type: "scene", shot_id: "shot_001", scene_id: "scene_001" };
  const imagePath = firstNewImagePath();
  return {
    ok: true,
    task,
    target,
    binding_status: { state_matches_current_plan: true, result_matches_current_plan: true },
    execution_state: { status: "idle", tasks: {} },
    plan: {
      target,
      summary: { total_tasks: 1, planned_prompt_tasks: 1, planned_image_tasks: 1 },
      image_tasks: [{
        image_task_id: "ip_001",
        asset_key: "dak_001",
        shot_id: "shot_001",
        scene_id: "scene_001",
        dialogue_id: "dlg_001",
        status: "ready",
        planned_outputs: {
          image_prompt_path: "SessionOutput/storyboard/Working/dak_001_ImagePrompt.json",
          image_path: imagePath || "SessionOutput/storyboard/Working/dak_001_Image_New.png",
        },
      }],
    },
    artifact_status: {
      tasks: [{
        image_task_id: "ip_001",
        asset_key: "dak_001",
        executable_task: true,
        image_in_working: Boolean(imagePath),
        slot_states: {
          image_prompt: { ui_tone: "pending", reason: "need_prompt" },
          image: { ui_tone: imagePath ? "done" : "pending", reason: imagePath ? "file_exists" : "need_image" },
        },
      }],
    },
  };
}

function videoOnlyPlanResult(payload) {
  const target = payload.target || { target_type: "scene", shot_id: "shot_001", scene_id: "scene_001" };
  const rawVideoPath = rawVideoPathsByAssetKey.dak_001 || "";
  const finalVideoPath = finalVideoPathsByAssetKey.dak_001 || "";
  return {
    ok: true,
    task,
    target,
    binding_status: { state_matches_current_plan: true, result_matches_current_plan: true },
    execution_state: { status: "idle", segments: {} },
    execution_result: { tasks: [] },
    plan: {
      target,
      video_only_tasks: [{
        video_only_task_id: "vop_001",
        asset_key: "dak_001",
        shot_id: "shot_001",
        scene_id: "scene_001",
        dialogue_id: "dlg_001",
        first_frame: { source_type: "bound_image", source_path: findDialogue(currentPlan).image_path || uploadImage.path },
        plan_steps: {
          prompt: { required: true },
          video: { required: true },
          confirm_final: { required: true },
        },
      }],
    },
    artifact_status: {
      tasks: [{
        video_only_task_id: "vop_001",
        asset_key: "dak_001",
        frame_input_ready_for_execution: true,
        raw_in_working: Boolean(rawVideoPath),
        final_in_working: false,
        final_bound: false,
        slot_states: {
          audio: { ui_tone: "disabled", reason: "video_only_no_audio_required" },
          frame_input: { ui_tone: "done", reason: "first_frame_ready" },
          video_prompt: { ui_tone: "pending", reason: "need_prompt" },
          raw_video: { ui_tone: rawVideoPath ? "done" : "pending", reason: rawVideoPath ? "file_exists" : "need_raw" },
          copy_final: { ui_tone: "pending", binding_consistency: "file_exists_unbound", reason: "raw_ready" },
        },
      }],
    },
  };
}

async function installRoutes(page) {
  await page.route("**/api/auth/status", (route) => json(route, {
    enabled: true,
    configured: true,
    authenticated: true,
    role: "user",
    capabilities: { can_manage_connection: false, can_view_metering: false },
    debug_console_enabled: false,
  }));
  await page.route("**/api/setup/media-models/tts/config", (route) => json(route, { providers: [] }));
  await page.route("**/api/session-tasks/**/raw/**", async (route) => {
    const url = decodeURIComponent(route.request().url());
    if (url.endsWith("tts_builder_candidates.json") || url.endsWith(".json")) return json(route, { candidates: [] });
    if (/\.(png|jpg|jpeg|webp)(\?|$)/i.test(url)) return route.fulfill({ status: 200, contentType: "image/png", body: PNG_1X1 });
    return route.fulfill({ status: 200, contentType: "application/octet-stream", body: Buffer.from("e2e") });
  });
  await page.route(`**/api/koubo-storyboard/tasks/${TASK_ID}`, async (route) => {
    if (route.request().method() === "PUT") {
      const payload = JSON.parse(route.request().postData() || "{}");
      calls.save.push(payload);
      currentPlan = clone(payload.plan || currentPlan);
    }
    return json(route, detail());
  });
  await page.route(`**/api/koubo-storyboard/tasks/${TASK_ID}/assets`, async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    applyUpload(route.request());
    return json(route, detail());
  });
  await page.route(`**/api/koubo-storyboard/tasks/${TASK_ID}/asset-bind`, async (route) => {
    applyBind(JSON.parse(route.request().postData() || "{}"));
    return json(route, detail());
  });
  await page.route(`**/api/koubo-storyboard/tasks/${TASK_ID}/asset-clear`, async (route) => {
    applyClear(JSON.parse(route.request().postData() || "{}"));
    return json(route, detail());
  });
  await page.route(`**/api/koubo-storyboard/tasks/${TASK_ID}/video-plan`, async (route) => {
    const payload = JSON.parse(route.request().postData() || "{}");
    const result = videoPlanResult(payload);
    calls.videoPlan.push(payload);
    calls.videoPlanResult.push(result);
    return json(route, result);
  });
  await page.route(`**/api/koubo-storyboard/tasks/${TASK_ID}/video-plan/execution`, (route) => json(route, {
    binding_status: { state_matches_current_plan: true, result_matches_current_plan: true },
    execution_state: { status: "idle", segments: {} },
  }));
  await page.route(`**/api/koubo-storyboard/tasks/${TASK_ID}/image-plan`, async (route) => {
    const payload = JSON.parse(route.request().postData() || "{}");
    const result = imagePlanResult(payload);
    calls.imagePlan.push(payload);
    calls.imagePlanResult.push(result);
    return json(route, result);
  });
  await page.route(`**/api/koubo-storyboard/tasks/${TASK_ID}/image-plan/execution`, (route) => json(route, {
    binding_status: { state_matches_current_plan: true, result_matches_current_plan: true },
    execution_state: { status: "idle", tasks: {} },
  }));
  await page.route(`**/api/koubo-storyboard/tasks/${TASK_ID}/video-only-plan`, async (route) => {
    const payload = JSON.parse(route.request().postData() || "{}");
    const result = videoOnlyPlanResult(payload);
    calls.videoOnlyPlan.push(payload);
    calls.videoOnlyPlanResult.push(result);
    return json(route, result);
  });
  await page.route(`**/api/koubo-storyboard/tasks/${TASK_ID}/video-only-plan/execution`, (route) => json(route, {
    binding_status: { state_matches_current_plan: true, result_matches_current_plan: true },
    execution_state: { status: "idle", segments: {} },
    execution_result: { tasks: [] },
  }));
}

async function assertFrontendServer() {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 3000);
  try {
    const response = await fetch(BASE_URL, { signal: controller.signal });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    throw new Error(`Frontend dev server is not reachable at ${BASE_URL}. Start it with \`npm --prefix frontend run dev -- --host 127.0.0.1 --port 18080\` or set OPENCREW_E2E_FRONTEND_URL. Original error: ${detail}`);
  } finally {
    clearTimeout(timer);
  }
}

export async function assertVisible(page, selector, countAtLeast = 1) {
  await page.locator(selector).first().waitFor({ state: "visible", timeout: 15000 });
  assert.ok(await page.locator(selector).count() >= countAtLeast, `${selector} should have at least ${countAtLeast} visible item(s)`);
}

export async function waitForCall(bucket, count) {
  const started = Date.now();
  while (Date.now() - started < 5000) {
    if ((calls[bucket] || []).length >= count) return;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error(`Timed out waiting for ${bucket} call #${count}`);
}

async function waitForUiSettled(page) {
  await page.evaluate(() => new Promise((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(resolve));
  }));
}

export async function dragAssetToSlot(page, title, targetSelector, expectedCount) {
  const card = page.locator(`.kbsp-asset-scene-card[title="${title}"]`).first();
  const target = page.locator(targetSelector).first();
  await card.waitFor({ state: "visible", timeout: 15000 });
  await target.waitFor({ state: "visible", timeout: 15000 });
  await card.scrollIntoViewIfNeeded();
  await target.scrollIntoViewIfNeeded();
  const sourceBox = await card.boundingBox();
  const targetBox = await target.boundingBox();
  assert.ok(sourceBox, `asset card not measurable: ${title}`);
  assert.ok(targetBox, `target slot not measurable: ${targetSelector}`);
  const responsePromise = page.waitForResponse((response) => (
    response.url().includes(`/api/koubo-storyboard/tasks/${TASK_ID}/asset-bind`)
    && response.request().method() === "POST"
  ));
  await page.mouse.move(sourceBox.x + sourceBox.width / 2, sourceBox.y + sourceBox.height / 2);
  await page.mouse.down();
  await page.mouse.move(sourceBox.x + sourceBox.width / 2 + 12, sourceBox.y + sourceBox.height / 2 + 12);
  await page.mouse.move(targetBox.x + targetBox.width / 2, targetBox.y + targetBox.height / 2, { steps: 12 });
  await page.mouse.up();
  await responsePromise;
  await waitForCall("bind", expectedCount);
  await waitForUiSettled(page);
}

export async function clickAndWaitForClear(page, selector, expectedCount) {
  await assertVisible(page, selector);
  const responsePromise = page.waitForResponse((response) => (
    response.url().includes(`/api/koubo-storyboard/tasks/${TASK_ID}/asset-clear`)
    && response.request().method() === "POST"
  ));
  await page.locator(selector).click({ force: true });
  await responsePromise;
  await waitForCall("clear", expectedCount);
  await waitForUiSettled(page);
}

export async function editDialogueText(page, dialogueId, value) {
  const selector = `textarea[data-kbsp-dialogue-textarea="${dialogueId}"]`;
  const textarea = page.locator(selector);
  await textarea.waitFor({ state: "visible", timeout: 15000 });
  await textarea.evaluate((node, nextValue) => {
    node.focus();
    node.value = nextValue;
    node.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: nextValue }));
  }, value);
  await page.waitForFunction(
    ([textareaSelector, expectedValue]) => document.querySelector(textareaSelector)?.value === expectedValue,
    [selector, value],
    { timeout: 5000 },
  );
  await waitForUiSettled(page);
}

export async function waitForEnabled(page, selector) {
  await page.waitForFunction((buttonSelector) => {
    const button = document.querySelector(buttonSelector);
    return Boolean(button && !button.disabled);
  }, selector, { timeout: 5000 });
}

export async function assertTextareaValue(page, dialogueId, expectedValue) {
  const selector = `textarea[data-kbsp-dialogue-textarea="${dialogueId}"]`;
  await page.waitForFunction(
    ([textareaSelector, value]) => document.querySelector(textareaSelector)?.value === value,
    [selector, expectedValue],
    { timeout: 5000 },
  );
}

export async function assertClassContains(locator, expectedClass, label) {
  const className = await locator.getAttribute("class");
  assert.ok(String(className || "").split(/\s+/).includes(expectedClass), `${label} should include ${expectedClass}; got ${className}`);
}

export function assertUsesDialogueAssetKey(planResult, expectedKey, forbiddenKeys) {
  const jsonText = JSON.stringify(planResult);
  const actualKey = planResult.plan?.shots?.[0]?.scenes?.[0]?.segments?.[0]?.asset_key || planResult.plan?.video_only_tasks?.[0]?.asset_key;
  assert.equal(actualKey, expectedKey);
  for (const forbidden of forbiddenKeys) {
    assert.ok(!jsonText.includes(`Working/${forbidden}_`), `plan outputs must not use fallback key ${forbidden}`);
  }
}

export function assertPlanDialogueKeysStable(plan) {
  assert.equal(findDialogue(plan, "dlg_001")?.dialogue_asset_key, "dak_001");
  assert.equal(findDialogue(plan, "dlg_002")?.dialogue_asset_key, "dak_002");
  assert.notEqual(findDialogue(plan, "dlg_001")?.dialogue_asset_key, findDialogue(plan, "dlg_001")?.srt_id);
  assert.notEqual(findDialogue(plan, "dlg_002")?.dialogue_asset_key, findDialogue(plan, "dlg_002")?.srt_id);
}

export function assertDialogueKeysAndBindingsStable() {
  assertPlanDialogueKeysStable(currentPlan);
  assert.equal(rawVideoPathsByAssetKey.dak_001, "SessionOutput/storyboard/Working/dak_001_Video_Raw.mp4");
  assert.equal(rawVideoPathsByAssetKey.dak_002, uploadVideo.path);
  assert.equal(finalVideoPathsByAssetKey.dak_001, "");
  assert.equal(finalVideoPathsByAssetKey.dak_002, uploadVideo.path);
  assert.equal(findDialogue(currentPlan, "dlg_002")?.working_assets?.video?.path, uploadVideo.path);
}

function writeFailureReport(error) {
  try {
    fs.mkdirSync(RESULT_DIR, { recursive: true });
    const report = {
      ok: false,
      run_id: RUN_ID,
      url: TEST_URL,
      error: {
        name: error?.name || "Error",
        message: error instanceof Error ? error.message : String(error),
        stack: error?.stack || "",
      },
    };
    fs.writeFileSync(REPORT_PATH, `${JSON.stringify(report, null, 2)}\n`);
  } catch (reportError) {
    console.error(`Failed to write failure report: ${reportError instanceof Error ? reportError.message : String(reportError)}`);
  }
}

export async function openStoryboard(page) {
  await page.goto(TEST_URL, { waitUntil: "domcontentloaded", timeout: 30000 });
  await assertVisible(page, ".kbsp-editor");
  await assertVisible(page, ".kbsp-dialogue-card", 2);
  await assertVisible(page, ".kbsp-media-original");
  await assertVisible(page, ".kbsp-media-new");
  await assertVisible(page, ".kbsp-media-raw-video");
  await assertVisible(page, ".kbsp-media-final-video");
  await page.getByText("Audio", { exact: true }).first().waitFor({ state: "visible" });
}

function scenarioScreenshotPath(name) {
  return path.join(RESULT_DIR, `${name}.png`);
}

async function runScenario(browser, scenario) {
  resetState();
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  const pageErrors = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  await installRoutes(page);
  try {
    await scenario.run(page);
    const screenshot = scenarioScreenshotPath(scenario.name);
    await page.screenshot({ path: screenshot, fullPage: true });
    assert.deepEqual(pageErrors, [], `Unexpected page errors: ${pageErrors.join("\n")}`);
    return { name: scenario.name, ok: true, screenshot, calls: clone(calls) };
  } finally {
    await context.close();
  }
}

export async function executeStoryboardScenarios(scenarios) {
  try {
    fs.mkdirSync(RESULT_DIR, { recursive: true });
    await assertFrontendServer();
    const { chromium } = loadPlaywright();
    const browser = await chromium.launch({ headless: HEADLESS });
    const scenarioReports = [];
    try {
      for (const scenario of scenarios) {
        scenarioReports.push(await runScenario(browser, scenario));
      }
    } finally {
      await browser.close();
    }
    const report = { ok: true, run_id: RUN_ID, url: TEST_URL, screenshots: scenarioReports.map((item) => item.screenshot), scenarios: scenarioReports };
    fs.writeFileSync(REPORT_PATH, `${JSON.stringify(report, null, 2)}\n`);
    console.log(JSON.stringify(report, null, 2));
  } catch (error) {
    writeFailureReport(error);
    console.error(error);
    process.exit(1);
  }
}

export function runIfMain(importMetaUrl, scenarios) {
  const entryUrl = process.argv[1] ? pathToFileURL(process.argv[1]).href : "";
  if (entryUrl === importMetaUrl) void executeStoryboardScenarios(scenarios);
}
