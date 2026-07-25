import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { existsSync, readdirSync } from "node:fs";
import { homedir } from "node:os";
import { resolve } from "node:path";
import { promisify } from "node:util";
import { chromium } from "playwright";
import {
  assertMediaLibraryCapabilities,
  baseUrl,
  loginAdmin,
  poll,
  repoRoot,
  requiredEnv,
  responseJson,
} from "./media-library-real-helpers.mjs";

const execFileAsync = promisify(execFile);
const url = baseUrl();
const assetId = requiredEnv(
  "MEDIA_LIBRARY_CLIP_RESTART_E2E_ASSET_ID",
);
assert.equal(
  process.env.MEDIA_LIBRARY_CLIP_RESTART_E2E_ALLOW_RESTART,
  "1",
  "MEDIA_LIBRARY_CLIP_RESTART_E2E_ALLOW_RESTART=1 is required because this test restarts the macmini-4 local test stack",
);
const browser = await chromium.launch({
  headless: process.env.HEADED !== "1",
});
const context = await browser.newContext({
  viewport: { width: 1440, height: 1000 },
});
const timestamp = Date.now();
const durableName = `E2E restart durable ${timestamp}`;
const interruptedName = `E2E restart interrupted ${timestamp}`;
const durableIdempotencyKey = `clip_restart_durable_${timestamp}`;
let durableClipId = "";
let interruptedClipJobId = "";

function orphanArtifactCount(workspace) {
  if (!existsSync(workspace)) return -1;
  return readdirSync(workspace, { recursive: true })
    .map((entry) => String(entry))
    .filter((entry) => (
      entry.endsWith(".part.mp4")
      || entry.endsWith(".deleting")
      || entry.includes(".deleting/")
    )).length;
}

async function createClip(payload) {
  const response = await context.request.post(
    `${url}/api/media-library/${encodeURIComponent(assetId)}/clip-jobs`,
    { data: payload, timeout: 30_000 },
  );
  const body = await responseJson(response, "clip submit");
  assert.equal(
    response.status(),
    202,
    `clip submit failed: HTTP ${response.status()} ${JSON.stringify(body)}`,
  );
  return body;
}

async function getJob(clipJobId) {
  const response = await context.request.get(
    `${url}/api/media-library/${encodeURIComponent(assetId)}/clip-jobs/${encodeURIComponent(clipJobId)}`,
    { timeout: 15_000 },
  );
  assert.equal(response.status(), 200);
  return responseJson(response, "clip job");
}

async function cancelJob(clipJobId) {
  if (!clipJobId) return;
  const response = await context.request.post(
    `${url}/api/media-library/${encodeURIComponent(assetId)}/clip-jobs/${encodeURIComponent(clipJobId)}/cancel`,
    { timeout: 15_000 },
  );
  assert.ok(
    [200, 404, 410].includes(response.status()),
    `clip job cleanup failed: HTTP ${response.status()}`,
  );
}

async function listClips() {
  const response = await context.request.get(
    `${url}/api/media-library/${encodeURIComponent(assetId)}/clips`,
    { timeout: 15_000 },
  );
  assert.equal(response.status(), 200);
  return (await responseJson(response, "clip list")).items || [];
}

async function deleteClip(clipId) {
  const response = await context.request.delete(
    `${url}/api/media-library/${encodeURIComponent(assetId)}/clips/${encodeURIComponent(clipId)}`,
    { timeout: 30_000 },
  );
  assert.ok(
    [200, 404].includes(response.status()),
    `clip cleanup failed: HTTP ${response.status()}`,
  );
}

try {
  await loginAdmin(context, url);
  await assertMediaLibraryCapabilities(context, url);
  const editorResponse = await context.request.get(
    `${url}/api/media-library/${encodeURIComponent(assetId)}/editor`,
  );
  assert.equal(editorResponse.status(), 200);
  const editor = await responseJson(editorResponse, "editor DTO");
  const durationMs = Number(editor.item?.duration_ms);
  const sessionId = Number(editor.item?.session_id);
  const sourceVersion = String(editor.source_version || "");
  assert.ok(
    Number.isSafeInteger(durationMs) && durationMs === 600_000,
    "clip restart E2E requires the real 10-minute representative asset",
  );
  assert.match(sourceVersion, /^[0-9a-f]{64}$/);
  assert.ok(Number.isSafeInteger(sessionId) && sessionId > 0);
  const dataDir = resolve(
    process.env.OPENCREW_DATA_DIR || resolve(homedir(), ".opencrew"),
  );
  const workspace = resolve(
    dataDir,
    "sessions",
    String(sessionId),
    "workspace",
  );
  assert.ok(
    workspace.startsWith(`${resolve(dataDir, "sessions")}/`),
    "derived session workspace escaped OPENCREW_DATA_DIR",
  );
  assert.ok(
    orphanArtifactCount(workspace) >= 0,
    "controlled source session workspace is missing",
  );

  const durableJob = await createClip({
    source_version: sourceVersion,
    start_ms: durationMs - 1_000,
    end_ms: durationMs,
    display_name: durableName,
    manual_override: true,
    idempotency_key: durableIdempotencyKey,
  });
  const durableTerminal = await poll(
    () => getJob(durableJob.clip_job_id),
    (job) => ["completed", "failed", "cancelled"].includes(
      job.status,
    ),
    {
      timeoutMs: 180_000,
      label: "durable clip completion",
    },
  );
  assert.equal(
    durableTerminal.status,
    "completed",
    JSON.stringify(durableTerminal.error),
  );
  durableClipId = String(durableTerminal.clip_id || "");
  assert.match(durableClipId, /^mlc_/);

  const jobPage = await context.newPage();
  await jobPage.goto(
    `${url}/#/media-library/${encodeURIComponent(assetId)}/editor`
      + `?start_ms=0&end_ms=${durationMs}`,
    { waitUntil: "domcontentloaded" },
  );
  await jobPage.locator(".ml-editor-shell").waitFor({
    timeout: 30_000,
  });
  await jobPage.getByLabel("入点 ms").fill("0");
  await jobPage.getByLabel("入点 ms").press("Tab");
  await jobPage.getByLabel("出点 ms").fill(String(durationMs));
  await jobPage.getByLabel("出点 ms").press("Tab");
  await jobPage.getByLabel("片段名称").fill(interruptedName);
  const interruptedResponsePromise = jobPage.waitForResponse(
    (response) => (
      response.request().method() === "POST"
      && new URL(response.url()).pathname
        === `/api/media-library/${assetId}/clip-jobs`
    ),
    { timeout: 30_000 },
  );
  await jobPage.getByRole("button", {
    name: "创建剪切任务",
  }).click();
  const interruptedResponse = await interruptedResponsePromise;
  assert.equal(interruptedResponse.status(), 202);
  const interruptedJob = await responseJson(
    interruptedResponse,
    "browser interrupted clip submit",
  );
  interruptedClipJobId = String(
    interruptedJob.clip_job_id || "",
  );
  assert.match(
    interruptedClipJobId,
    /^[A-Za-z0-9._:-]{1,256}$/,
  );
  await jobPage.getByRole("button", {
    name: "派生片段",
    exact: true,
  }).click();
  await jobPage.locator(
    ".ml-editor-job.queued, .ml-editor-job.running",
  ).waitFor({ timeout: 10_000 });
  const activeBeforeRestart = await getJob(
    interruptedJob.clip_job_id,
  );
  assert.ok(
    ["queued", "running"].includes(activeBeforeRestart.status),
    `long clip must be active before restart, got ${activeBeforeRestart.status}`,
  );
  const orphanCountBeforeRestart = await poll(
    async () => orphanArtifactCount(workspace),
    (count) => count > 0,
    {
      timeoutMs: 30_000,
      intervalMs: 250,
      label: "real in-flight .part.mp4 before restart",
    },
  );

  await execFileAsync(
    `${repoRoot}/scripts/opencrew_local_stack.sh`,
    ["restart"],
    {
      cwd: repoRoot,
      env: process.env,
      timeout: 180_000,
      maxBuffer: 10 * 1024 * 1024,
    },
  );
  await loginAdmin(context, url);
  await assertMediaLibraryCapabilities(context, url);
  const lostNotice = jobPage.locator(
    ".ml-editor-inline-error",
    { hasText: "clip_job_lost" },
  );
  await lostNotice.waitFor({ timeout: 30_000 });
  assert.match(
    await lostNotice.innerText(),
    /后端已重启.*任务已丢失.*clip_job_lost/,
  );

  const lostResponse = await context.request.get(
    `${url}/api/media-library/${encodeURIComponent(assetId)}/clip-jobs/${encodeURIComponent(interruptedJob.clip_job_id)}`,
    { timeout: 15_000 },
  );
  assert.equal(lostResponse.status(), 410);
  const lost = await responseJson(lostResponse, "lost clip job");
  assert.equal(lost.detail?.code, "clip_job_lost");
  const orphanCountAfterRestart = orphanArtifactCount(workspace);
  assert.equal(
    orphanCountAfterRestart,
    0,
    "startup cleanup must remove interrupted .part.mp4/.deleting artifacts",
  );

  const afterRestart = await listClips();
  const durableClip = afterRestart.find(
    (clip) => clip.clip_id === durableClipId,
  );
  assert.ok(
    durableClip,
    "completed derivative must remain listed after service restart",
  );
  assert.equal(durableClip.display_name, durableName);
  const durableGet = await context.request.get(
    `${url}/api/media-library/${encodeURIComponent(assetId)}/clips/${encodeURIComponent(durableClipId)}`,
  );
  assert.equal(durableGet.status(), 200);

  const replay = await createClip({
    source_version: sourceVersion,
    start_ms: durationMs - 1_000,
    end_ms: durationMs,
    display_name: durableName,
    manual_override: true,
    idempotency_key: durableIdempotencyKey,
  });
  assert.equal(replay.status, "completed");
  assert.equal(
    replay.clip_id,
    durableClipId,
    "idempotent replay after restart must reuse the published derivative",
  );

  const persistedPage = await context.newPage();
  await persistedPage.goto(
    `${url}/#/media-library/${encodeURIComponent(assetId)}/editor`
      + `?start_ms=${durationMs - 1000}&end_ms=${durationMs}`,
    { waitUntil: "domcontentloaded" },
  );
  await persistedPage.locator(".ml-editor-shell").waitFor({
    timeout: 30_000,
  });
  await persistedPage.getByRole("button", {
    name: "派生片段",
    exact: true,
  }).click();
  await persistedPage.locator(".ml-editor-clip", {
    hasText: durableName,
  }).waitFor({ timeout: 30_000 });

  console.log(JSON.stringify({
    ok: true,
    asset_id: assetId,
    duration_ms: durationMs,
    completed_clip_id: durableClipId,
    old_job_http_status: lostResponse.status(),
    old_job_error_code: lost.detail?.code,
    browser_lost_message: true,
    durable_after_restart: true,
    idempotent_replay_same_clip: true,
    orphan_candidates_before_restart: orphanCountBeforeRestart,
    orphan_artifacts_after_restart: orphanCountAfterRestart,
  }));
} finally {
  try {
    await loginAdmin(context, url);
    await cancelJob(interruptedClipJobId);
    const clips = await listClips();
    for (const clip of clips) {
      if (
        clip.clip_id === durableClipId
        || clip.display_name === interruptedName
      ) {
        await deleteClip(clip.clip_id);
      }
    }
  } catch {
    // Preserve the primary assertion error; names are unique and reported.
  }
  await browser.close();
}
