#!/usr/bin/env node
import assert from "node:assert/strict";
import { loadPlaywright } from "./playwright-loader.mjs";

const BASE_URL = (process.env.OPENCREW_E2E_FRONTEND_URL || "http://127.0.0.1:18080").replace(/\/$/, "");
const HEADLESS = process.env.OPENCREW_E2E_HEADLESS !== "0";
const TASK_ID = 305;
const SESSION_ID = 365;
const DIALOGUE = "人穷衣服破";
const PROTECTED_REPORTS = new Set([
  "S3_02_01_AudioASR/Report/Result.json",
  "S4_02_02_VideoSRTFrame/Report/Result.json",
]);

function json(route, payload, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(payload) });
}

function decodeRawPath(pathname) {
  const marker = `/api/session-tasks/${SESSION_ID}/raw/`;
  return pathname
    .slice(pathname.indexOf(marker) + marker.length)
    .split("/")
    .map((part) => decodeURIComponent(part))
    .join("/");
}

function task() {
  return {
    id: TASK_ID,
    session_id: SESSION_ID,
    status: "completed",
    reference_video_path: "inbox/task305.mp4",
    industry: "测试行业",
    persona: "测试人设",
    target_audience: "测试受众",
    video_formula: "口播转化脚本",
    latest_attempt_id: 548,
    updated_at: Date.now(),
  };
}

function taskDetail() {
  return {
    task: task(),
    current_prompt_version: {},
    prompt_models: { items: [], default_model: { providerID: "", modelID: "" } },
    options: {},
  };
}

function publicWorkspacePayload(path) {
  if (path === "SessionOutput/subtitle/final_srt_frame_items.json") {
    return {
      schema_version: "analysis_v1_final_srt_frame_items_0.2",
      items: [{
        srt_id: "srt_0001",
        dialogue: DIALOGUE,
        image_path: "SessionOutput/visual/srt_frames/srt_0001.jpg",
        start: 0.16,
        end: 1.64,
        duration: 1.48,
      }],
    };
  }
  if (path === "SessionOutput/subtitle/rewritten_srt_items.json") {
    return { items: [{ srt_id: "srt_0001", dialogue: DIALOGUE }] };
  }
  if (path === "SessionOutput/storyboard/srt_storyboard.json") {
    return { shots: [] };
  }
  if (path === "SessionOutput/visual/srt_frame_map.json") {
    return { items: [] };
  }
  return null;
}

async function installRoutes(page, protectedReportRequests) {
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;

    if (path === "/api/auth/status") {
      return json(route, {
        enabled: true,
        configured: true,
        authenticated: true,
        role: "user",
        capabilities: {},
        debug_console_enabled: false,
      });
    }
    if (path === "/api/openclip/tasks") return json(route, { items: [task()] });
    if (path === `/api/openclip/tasks/${TASK_ID}`) return json(route, taskDetail());
    if (path === "/api/openclip/prompt-models") {
      return json(route, { items: [], default_model: { providerID: "", modelID: "" } });
    }
    if (path === `/api/openclip/tasks/${TASK_ID}/analysis-v1/run-to-storyboard/548`) {
      return json(route, {
        attempt_id: 548,
        attempt_family: "analysis_v1_tool_run",
        status: "completed",
        steps: [],
      });
    }
    if (path === `/api/openclip/tasks/${TASK_ID}/analysis-v1/one-click-movie`) {
      return json(route, { status: "idle", run_id: "" });
    }
    if (path === `/api/koubo-storyboard/tasks/${TASK_ID}/tts-builder-candidates`) {
      return json(route, { items: [] });
    }
    if (path === `/api/koubo-storyboard/tasks/${TASK_ID}/asset-library/tts-model-config`) {
      return json(route, { providers: [] });
    }
    if (path.startsWith(`/api/session-tasks/${SESSION_ID}/raw/`)) {
      const relativePath = decodeRawPath(path);
      if (PROTECTED_REPORTS.has(relativePath)) {
        protectedReportRequests.push(relativePath);
        return json(route, { detail: "File is not downloadable" }, 403);
      }
      const payload = publicWorkspacePayload(relativePath);
      return payload ? json(route, payload) : json(route, { detail: "File not found" }, 404);
    }
    return json(route, {});
  });
}

async function assertDialogueLoaded(page, phase) {
  await page.getByText(DIALOGUE, { exact: true }).first().waitFor({ state: "visible", timeout: 15000 });
  assert.equal(await page.locator(".analysis-v1-banner.bad").count(), 0, `${phase}: page must not show a protected-file error`);
  assert.equal(await page.locator(".analysis-v1-dialogue-row").count(), 1, `${phase}: completed dialogue should render`);
}

const { chromium } = loadPlaywright();
const browser = await chromium.launch({ headless: HEADLESS });
const context = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
const page = await context.newPage();
const pageErrors = [];
const protectedReportRequests = [];
page.on("pageerror", (error) => pageErrors.push(error.message));

try {
  await installRoutes(page, protectedReportRequests);
  const url = `${BASE_URL}/?analysisDialogueLoad=${Date.now()}#/analysis-v1/tasks/${TASK_ID}`;
  await page.goto(url, { waitUntil: "domcontentloaded", timeout: 30000 });
  await assertDialogueLoaded(page, "initial load");
  await page.reload({ waitUntil: "domcontentloaded", timeout: 30000 });
  await assertDialogueLoaded(page, "refresh");
  assert.deepEqual(protectedReportRequests, [], "customer UI must not request protected tool reports");
  assert.deepEqual(pageErrors, [], "page should not throw uncaught errors");
  console.log("analysis-v1-dialogue-load passed: completed dialogue renders before and after refresh");
} finally {
  await page.unrouteAll({ behavior: "ignoreErrors" }).catch(() => {});
  await browser.close().catch(() => {});
}
