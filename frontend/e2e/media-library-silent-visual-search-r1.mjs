import assert from "node:assert/strict";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { chromium } from "playwright";

import {
  assertMediaLibraryCapabilities,
  baseUrl,
  findStableStoryboardDialogue,
  loginAdmin,
  repoRoot,
  responseJson,
} from "./media-library-real-helpers.mjs";

const url = baseUrl();
const assetId = String(
  process.env.MEDIA_LIBRARY_SILENT_VISUAL_ASSET_ID
  || "mla_1784601908573_70c828790521",
).trim();
const editorSourceAssetId = String(
  process.env.MEDIA_LIBRARY_SILENT_VISUAL_EDITOR_SOURCE_ASSET_ID
  || "mla_1784615441629_44c90c6be2af",
).trim();
const timestamp = String(
  process.env.MEDIA_LIBRARY_SILENT_VISUAL_ARTIFACT_TIMESTAMP
  || new Date().toISOString().replace(/[-:TZ.]/g, "").slice(0, 14),
);
const artifactDir = resolve(
  repoRoot,
  "frontend/e2e/artifacts/media-library-silent-visual-search",
  timestamp,
);
mkdirSync(artifactDir, { recursive: true });

function artifact(name) {
  return resolve(artifactDir, name);
}

function parseSseEvents(body, label) {
  const events = [];
  for (const block of String(body || "").split(/\r?\n\r?\n/)) {
    for (const line of block.split(/\r?\n/)) {
      if (!line.startsWith("data: ")) continue;
      try {
        events.push(JSON.parse(line.slice(6)));
      } catch {
        assert.fail(`${label} returned invalid SSE: ${line}`);
      }
    }
  }
  assert.ok(events.length, `${label} returned no SSE events`);
  assert.equal(
    events.find((event) => event?.type === "failed"),
    undefined,
    `${label} emitted a failed event`,
  );
  return events;
}

async function assertNoPageOverflow(page, label) {
  const geometry = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  assert.ok(
    geometry.scrollWidth <= geometry.clientWidth + 1,
    `${label} has horizontal page overflow: ${JSON.stringify(geometry)}`,
  );
}

async function setOnlyGlobalVideos(page) {
  const filters = page.locator(
    ".ual-search-agent-panel .ual-search-panel-brief .ual-search-filters",
  );
  await filters.waitFor({ state: "visible", timeout: 30_000 });
  const rows = filters.locator(".ual-search-filter-row");
  const sourceButtons = rows.nth(0).locator("button");
  for (let index = 0; index < await sourceButtons.count(); index += 1) {
    const button = sourceButtons.nth(index);
    const label = String(await button.innerText()).replace(/\s+/g, " ").trim();
    const shouldBeActive = label.startsWith("全局素材库");
    const active = await button.evaluate((node) => node.classList.contains("is-active"));
    if (active !== shouldBeActive) await button.click();
  }
  const typeButtons = rows.nth(1).locator("button");
  for (let index = 0; index < await typeButtons.count(); index += 1) {
    const button = typeButtons.nth(index);
    const shouldBeActive = String(await button.innerText()).trim() === "视频";
    const active = await button.evaluate((node) => node.classList.contains("is-active"));
    if (active !== shouldBeActive) await button.click();
  }
}

async function openVisualDetail(page) {
  await page.goto(`${url}/#/media-library/${encodeURIComponent(assetId)}`, {
    waitUntil: "domcontentloaded",
    timeout: 30_000,
  });
  const tab = page.getByRole("tab", { name: /画面分析/ });
  await tab.waitFor({ timeout: 30_000 });
  await tab.click();
  await page.getByText("四帧采样证据", { exact: true }).waitFor({ timeout: 30_000 });
  const strip = page.locator(".media-library-keyframe-strip");
  await strip.waitFor();
  assert.equal(await strip.locator("button").count(), 4);
  assert.deepEqual(
    await strip.locator("button span").allInnerTexts(),
    ["0:01.625", "0:04.875", "0:08.125", "0:11.375"],
  );
  await page.getByText("四帧均匀采样", { exact: false }).waitFor();
  await page.getByText("透明纹理碗", { exact: false }).first().waitFor();
}

const browser = await chromium.launch({
  headless: process.env.HEADED !== "1",
});
const context = await browser.newContext({
  viewport: { width: 1440, height: 1000 },
});

const report = {
  schema_version: "media_library_silent_visual_search_r1_browser_e2e_v1",
  timestamp,
  base_url: url,
  artifact_dir: artifactDir,
  asset_id: assetId,
  editor_source_asset_id: editorSourceAssetId,
  screenshots: [],
};

try {
  await loginAdmin(context, url);
  const capabilities = await assertMediaLibraryCapabilities(context, url);
  assert.equal(capabilities.features?.library_search?.enabled, true);

  const detailResponse = await context.request.get(
    `${url}/api/media-library/${encodeURIComponent(assetId)}`,
  );
  assert.equal(detailResponse.status(), 200);
  const detailPayload = await responseJson(detailResponse, "DSCF0157 detail");
  const asset = detailPayload.item;
  assert.equal(asset.asset_id, assetId);
  assert.equal(asset.original_filename.toLowerCase(), "dscf0157.mov");
  assert.equal(asset.analysis_status, "partial");
  assert.equal(asset.open_cut?.dialogue_error_code, "video_has_no_audio");
  assert.equal(asset.visual_search_ready, true);
  assert.equal(asset.visual_search_state, "ready");
  assert.equal(asset.visual_search_fragment_count, 2);
  assert.equal(asset.visual_search_schema_version, "media_library_visual_semantic_v2");
  report.asset_session_id = asset.session_id;
  report.asset_content_sha256 = asset.content_sha256;
  report.visual_structure_run_id = asset.open_cut?.visual_structure_current_run_id;
  report.visual_semantic_run_id = asset.open_cut?.visual_semantic_current_run_id;

  const page = await context.newPage();
  await page.goto(`${url}/#/media-library`, {
    waitUntil: "domcontentloaded",
    timeout: 30_000,
  });
  const assetLink = page.locator(
    `a[href="#/media-library/${assetId}"]`,
  ).first();
  await assetLink.waitFor({ timeout: 30_000 });
  const assetRow = page.locator(".media-library-table tbody tr", {
    has: assetLink,
  });
  const rowText = await assetRow.innerText();
  for (const expected of ["部分完成", "无音轨", "可按画面检索"]) {
    assert.match(rowText, new RegExp(expected));
  }
  await page.screenshot({ path: artifact("r1-asset-list-desktop.png"), fullPage: true });
  report.screenshots.push("r1-asset-list-desktop.png");

  await openVisualDetail(page);
  await page.screenshot({ path: artifact("r1-four-frame-detail-desktop.png"), fullPage: true });
  report.screenshots.push("r1-four-frame-detail-desktop.png");

  const storyboard = await findStableStoryboardDialogue(context, url);
  assert.ok(storyboard.dialogueText);
  report.task_id = storyboard.taskId;
  report.dialogue_asset_key = storyboard.dialogueAssetKey;
  const taskResponse = await context.request.get(
    `${url}/api/koubo-storyboard/tasks/${storyboard.taskId}`,
  );
  assert.equal(taskResponse.status(), 200);
  const taskPayload = await responseJson(taskResponse, "StoryBoard task");
  report.target_session_id = taskPayload.task?.session_id || null;

  await page.goto(
    `${url}/#/koubo-storyboard/tasks/${storyboard.taskId}`
      + `?dialogue_asset_key=${encodeURIComponent(storyboard.dialogueAssetKey)}`,
    { waitUntil: "domcontentloaded", timeout: 30_000 },
  );
  const selectedDialogue = page.locator(
    `.kbsp-dialogue-card.is-active[data-kbsp-dialogue-asset-key="${storyboard.dialogueAssetKey}"]`,
  );
  await selectedDialogue.waitFor({ timeout: 30_000 });
  await page.getByRole("button", { name: "上传素材", exact: true }).click();
  await page.getByRole("button", { name: "检索素材", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "检索全局素材库" });
  await dialog.waitFor();
  await dialog.getByLabel("补充画面或关键词（可选）").fill("绿色包装");
  const storyboardSearchResponsePromise = page.waitForResponse(
    (response) => response.request().method() === "POST"
      && /\/api\/koubo-storyboard\/tasks\/\d+\/dialogues\/[^/]+\/media-library-search\/runs$/.test(new URL(response.url()).pathname),
    { timeout: 120_000 },
  );
  await dialog.getByRole("button", { name: "开始检索", exact: true }).click();
  const storyboardSearchResponse = await storyboardSearchResponsePromise;
  assert.equal(storyboardSearchResponse.status(), 200);
  const storyboardSearch = await responseJson(
    storyboardSearchResponse,
    "StoryBoard visual search",
  );
  const storyboardCandidate = storyboardSearch.items?.find(
    (item) => item.asset_id === assetId,
  );
  assert.ok(storyboardCandidate, "StoryBoard visual search did not return DSCF0157");
  assert.equal(storyboardCandidate.candidate_kind, "original_video");
  assert.equal(storyboardCandidate.source_asset_id, assetId);
  assert.equal(storyboardCandidate.source_clip_id, null);
  assert.equal(storyboardCandidate.content_sha256, asset.content_sha256);
  const matchedFragment = storyboardCandidate.matched_fragments?.find(
    (item) => item.analysis_scheme === "visual_semantic" && item.start_ms === 0,
  );
  assert.ok(matchedFragment, "DSCF0157 is missing the 0–13s visual hit");
  assert.equal(matchedFragment.end_ms, 13_000);
  assert.equal(matchedFragment.keyframe_ref?.length, 4);
  assert.match(matchedFragment.summary, /绿色包装/);
  report.storyboard_search_id = storyboardSearch.search_id;
  report.matched_fragment = {
    analysis_scheme: matchedFragment.analysis_scheme,
    fragment_id: matchedFragment.fragment_id,
    start_ms: matchedFragment.start_ms,
    end_ms: matchedFragment.end_ms,
    summary: matchedFragment.summary,
    keyframe_refs: matchedFragment.keyframe_ref,
  };
  const storyboardCard = dialog.locator(".kbsp-ml-search-card", {
    hasText: "DSCF0157",
  }).first();
  await storyboardCard.waitFor({ timeout: 30_000 });
  const visualSection = storyboardCard.locator(".kbsp-ml-search-fragments section", {
    hasText: "视觉命中",
  }).filter({ hasText: "00:00.000 – 00:13.000" }).first();
  await visualSection.waitFor();
  await visualSection.getByText("绿色包装", { exact: false }).waitFor();
  await page.screenshot({ path: artifact("r1-storyboard-visual-hit.png"), fullPage: true });
  report.screenshots.push("r1-storyboard-visual-hit.png");

  const editorResponsePromise = page.waitForResponse(
    (response) => response.request().method() === "GET"
      && new URL(response.url()).pathname === `/api/media-library/${assetId}/editor`,
    { timeout: 30_000 },
  );
  await visualSection.getByRole("button", { name: "剪切这个片段", exact: true }).click();
  await page.locator(".ml-editor-shell").waitFor({ timeout: 30_000 });
  const editorResponse = await editorResponsePromise;
  assert.equal(editorResponse.status(), 200);
  const editorPayload = await responseJson(editorResponse, "DSCF0157 editor");
  assert.equal(editorPayload.navigation_context?.search_valid, true);
  assert.equal(editorPayload.navigation_context?.target_valid, true);
  assert.equal(await page.getByLabel("入点 ms").inputValue(), "0");
  assert.equal(await page.getByLabel("出点 ms").inputValue(), "13000");
  await page.getByText("当前选区 00:00.000–00:13.000", { exact: false }).first().waitFor();
  await page.screenshot({ path: artifact("r1-editor-suggested-range.png"), fullPage: true });
  report.screenshots.push("r1-editor-suggested-range.png");

  const clipName = `R1 DSCF0157 视觉命中 0-13秒 ${timestamp}`;
  report.clip_name = clipName;
  await page.getByLabel("片段名称").fill(clipName);
  const clipJobResponsePromise = page.waitForResponse(
    (response) => response.request().method() === "POST"
      && new URL(response.url()).pathname === `/api/media-library/${assetId}/clip-jobs`,
    { timeout: 30_000 },
  );
  await page.getByRole("button", { name: "创建剪切任务", exact: true }).click();
  const clipJobResponse = await clipJobResponsePromise;
  assert.equal(clipJobResponse.status(), 202);
  const clipJob = await responseJson(clipJobResponse, "visual-range clip job");
  report.clip_job_id = clipJob.clip_job_id;
  await page.getByRole("button", { name: "派生片段", exact: true }).click();
  await page.locator(".ml-editor-job.completed").waitFor({ timeout: 120_000 });
  const clipCard = page.locator(".ml-editor-clip", { hasText: clipName });
  await clipCard.waitFor({ timeout: 30_000 });
  await clipCard.getByText("00:00.000–00:13.000", { exact: false }).waitFor();
  const clipsResponse = await context.request.get(
    `${url}/api/media-library/${assetId}/clips`,
  );
  assert.equal(clipsResponse.status(), 200);
  const clipsPayload = await responseJson(clipsResponse, "DSCF0157 clips");
  const clip = (clipsPayload.items || []).find(
    (item) => item.display_name === clipName,
  );
  assert.ok(clip, "completed visual-range clip is missing from authoritative list");
  assert.equal(clip.start_ms, 0);
  assert.equal(clip.end_ms, 13_000);
  assert.equal(clip.duration_ms, 13_000);
  report.clip_id = clip.clip_id;
  report.clip_output_path = clip.output_path;
  report.clip_content_sha256 = clip.content_sha256;
  await page.screenshot({ path: artifact("r1-real-cut-completed.png"), fullPage: true });
  report.screenshots.push("r1-real-cut-completed.png");

  const clipImportResponsePromise = page.waitForResponse(
    (response) => response.request().method() === "POST"
      && new URL(response.url()).pathname
        === `/api/media-library/${assetId}/clips/${clip.clip_id}/import-to-storyboard`,
    { timeout: 60_000 },
  );
  await clipCard.getByRole("button", { name: "导入 StoryBoard", exact: true }).click();
  const clipImportResponse = await clipImportResponsePromise;
  assert.equal(clipImportResponse.status(), 200);
  const clipImport = await responseJson(clipImportResponse, "visual-range clip import");
  assert.equal(clipImport.source_kind, "media_library_clip");
  assert.equal(clipImport.source_asset_id, assetId);
  assert.equal(clipImport.source_clip_id, clip.clip_id);
  assert.equal(clipImport.target_task_id, storyboard.taskId);
  assert.equal(clipImport.status, "completed");
  report.import_id = clipImport.import_id;
  report.imported_path = clipImport.item?.path || null;
  await page.getByText("派生片段已导入目标 StoryBoard。", { exact: true }).waitFor();
  await page.screenshot({ path: artifact("r1-real-cut-imported.png"), fullPage: true });
  report.screenshots.push("r1-real-cut-imported.png");

  await page.locator(".ml-editor-back").click();
  await selectedDialogue.waitFor({ timeout: 30_000 });
  await page.getByRole("button", { name: "上传素材", exact: true }).click();
  const retainedAsset = page.locator('.kbsp-asset-scene-card[aria-label^="R1 DSCF0157 视觉命中"]').first();
  await retainedAsset.waitFor({ timeout: 60_000 });
  await page.reload({ waitUntil: "domcontentloaded" });
  await selectedDialogue.waitFor({ timeout: 30_000 });
  await page.getByRole("button", { name: "上传素材", exact: true }).click();
  await page.locator('.kbsp-asset-scene-card[aria-label^="R1 DSCF0157 视觉命中"]').first().waitFor({ timeout: 60_000 });
  await page.screenshot({ path: artifact("r1-asset-pool-import-retained.png"), fullPage: true });
  report.screenshots.push("r1-asset-pool-import-retained.png");

  await page.goto(
    `${url}/#/koubo-asset-library/tasks/${storyboard.taskId}/search-agent`,
    { waitUntil: "domcontentloaded", timeout: 30_000 },
  );
  await page.locator(".ual-search-workspace").waitFor({ timeout: 30_000 });
  await setOnlyGlobalVideos(page);
  const agentTextarea = page.locator(
    '.ual-search-agent-panel textarea[placeholder="医院走廊里医生查看平板，横屏，真实纪录片风格"]',
  );
  await agentTextarea.fill("玻璃碗");
  const agentResponsePromise = page.waitForResponse(
    (response) => response.request().method() === "POST"
      && new URL(response.url()).pathname
        === `/api/koubo-storyboard/tasks/${storyboard.taskId}/asset-library-search/search/events`,
    { timeout: 120_000 },
  );
  await page.getByRole("button", { name: "开始检索", exact: true }).click();
  const agentResponse = await agentResponsePromise;
  assert.equal(agentResponse.status(), 200);
  await agentResponse.finished();
  const agentEvents = parseSseEvents(await agentResponse.text(), "Agent visual search");
  const agentStarted = agentEvents.find((event) => event.type === "started");
  const agentCompleted = agentEvents.find((event) => event.type === "completed");
  assert.ok(agentStarted && agentCompleted);
  const agentCandidate = agentCompleted.items?.find(
    (item) => item.asset_id === assetId,
  );
  assert.ok(agentCandidate, "Agent visual search did not return DSCF0157");
  assert.equal(agentCandidate.candidate_kind, "original_video");
  assert.ok(agentCandidate.matched_fragments?.some(
    (item) => item.analysis_scheme === "visual_semantic" && item.keyframe_ref?.length === 4,
  ));
  report.agent_search_id = agentStarted.search_id;
  report.agent_media_library_search_id = agentCandidate.media_library_search_id;
  const agentCard = page.locator(`.ual-search-card[title="DSCF0157"]`).first();
  await agentCard.waitFor({ timeout: 30_000 });
  await agentCard.locator(".ual-search-card-match", { hasText: "视觉命中" }).waitFor();
  await agentCard.locator('button[title="More"]').click();
  await page.getByRole("menuitem", { name: "剪切首个命中范围" }).waitFor();
  await page.getByRole("menuitem", { name: "查看详情" }).click();
  const agentDetail = page.getByRole("dialog", { name: "候选素材详情" });
  await agentDetail.waitFor();
  await agentDetail.getByText("视觉命中", { exact: true }).first().waitFor();
  await agentDetail.getByText("透明纹理碗", { exact: false }).first().waitFor();
  await agentDetail.getByRole("button", { name: "以此范围打开剪辑" }).first().waitFor();
  await page.screenshot({ path: artifact("r1-agent-visual-hit.png"), fullPage: true });
  report.screenshots.push("r1-agent-visual-hit.png");

  await page.goto(
    `${url}/#/media-library/${encodeURIComponent(editorSourceAssetId)}/editor?return_to=media_library_detail`,
    { waitUntil: "domcontentloaded", timeout: 30_000 },
  );
  await page.locator(".ml-editor-shell").waitFor({ timeout: 30_000 });
  await page.getByRole("button", { name: "素材检索", exact: true }).click();
  await page.getByLabel("补充关键词/要求").fill("深色液体");
  const editorSearchResponsePromise = page.waitForResponse(
    (response) => response.request().method() === "POST"
      && new URL(response.url()).pathname
        === `/api/media-library/${editorSourceAssetId}/search/runs`,
    { timeout: 120_000 },
  );
  await page.getByRole("button", { name: "开始检索", exact: true }).click();
  const editorSearchResponse = await editorSearchResponsePromise;
  assert.equal(editorSearchResponse.status(), 200);
  const editorSearch = await responseJson(editorSearchResponse, "editor visual search");
  const editorCandidate = editorSearch.items?.find((item) => item.asset_id === assetId);
  assert.ok(editorCandidate, "editor visual search did not return DSCF0157");
  assert.ok(editorCandidate.matched_fragments?.some(
    (item) => item.analysis_scheme === "visual_semantic" && /深红色液体/.test(item.summary || ""),
  ));
  report.editor_search_id = editorSearch.search_id;
  const editorCandidateCard = page.locator(".ml-editor-candidate.media_library", { hasText: "DSCF0157" }).first();
  await editorCandidateCard.waitFor({ timeout: 30_000 });
  await editorCandidateCard.locator(".ml-editor-match-list", { hasText: "视觉命中" }).waitFor();
  await editorCandidateCard.getByRole("button", { name: "以此范围打开剪辑" }).first().waitFor();
  await page.screenshot({ path: artifact("r1-editor-search-visual-hit.png"), fullPage: true });
  report.screenshots.push("r1-editor-search-visual-hit.png");

  const storyboardReplay = await context.request.get(
    `${url}/api/koubo-storyboard/tasks/${storyboard.taskId}/media-library-search/runs/${storyboardSearch.search_id}`,
  );
  assert.equal(storyboardReplay.status(), 200);
  const replayPayload = await responseJson(storyboardReplay, "StoryBoard search replay");
  assert.ok(replayPayload.items?.some((item) => item.asset_id === assetId));
  report.storyboard_replay_persisted = true;

  const mobile = await browser.newContext({
    viewport: { width: 390, height: 844 },
    isMobile: true,
    hasTouch: true,
  });
  try {
    await loginAdmin(mobile, url);
    const mobilePage = await mobile.newPage();
    await mobilePage.goto(`${url}/#/media-library`, {
      waitUntil: "domcontentloaded",
      timeout: 30_000,
    });
    await mobilePage.locator(`a[href="#/media-library/${assetId}"]`).first().waitFor({ timeout: 30_000 });
    await assertNoPageOverflow(mobilePage, "mobile asset list");
    await mobilePage.screenshot({ path: artifact("r1-asset-list-mobile.png"), fullPage: true });
    report.screenshots.push("r1-asset-list-mobile.png");
    await openVisualDetail(mobilePage);
    await assertNoPageOverflow(mobilePage, "mobile four-frame detail");
    await mobilePage.screenshot({ path: artifact("r1-four-frame-detail-mobile.png"), fullPage: true });
    report.screenshots.push("r1-four-frame-detail-mobile.png");
  } finally {
    await mobile.close();
  }

  report.ok = true;
  report.completed_at = new Date().toISOString();
  writeFileSync(
    artifact("r1-browser-e2e-report.json"),
    `${JSON.stringify(report, null, 2)}\n`,
    "utf8",
  );
  console.log(JSON.stringify(report));
} catch (error) {
  report.ok = false;
  report.error = error instanceof Error ? error.stack || error.message : String(error);
  report.failed_at = new Date().toISOString();
  writeFileSync(
    artifact("r1-browser-e2e-report.json"),
    `${JSON.stringify(report, null, 2)}\n`,
    "utf8",
  );
  throw error;
} finally {
  await context.close();
  await browser.close();
}
