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
const clipId = String(
  process.env.MEDIA_LIBRARY_SILENT_VISUAL_CLIP_ID
  || "mlc_1784605289217_6a71573ce61e",
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

const clipName = `化橘红倒入玻璃碗中的深色液体 ${timestamp}`;
const uniqueTag = `R2复用${timestamp}`;
const tags = ["化橘红", "玻璃碗", "深色液体", "绿色包装", uniqueTag];

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
  assert.equal(events.find((event) => event?.type === "failed"), undefined);
  return events;
}

async function assertNoPageOverflow(page, label) {
  const geometry = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  assert.ok(
    geometry.scrollWidth <= geometry.clientWidth + 1,
    `${label} has horizontal overflow: ${JSON.stringify(geometry)}`,
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
    const expected = label.startsWith("全局素材库");
    const active = await button.evaluate((node) => node.classList.contains("is-active"));
    if (active !== expected) await button.click();
  }
  const typeButtons = rows.nth(1).locator("button");
  for (let index = 0; index < await typeButtons.count(); index += 1) {
    const button = typeButtons.nth(index);
    const expected = String(await button.innerText()).trim() === "视频";
    const active = await button.evaluate((node) => node.classList.contains("is-active"));
    if (active !== expected) await button.click();
  }
}

async function openStoryboardSearch(page, taskId, dialogueAssetKey, query) {
  await page.goto(
    `${url}/#/koubo-storyboard/tasks/${taskId}`
      + `?dialogue_asset_key=${encodeURIComponent(dialogueAssetKey)}`,
    { waitUntil: "domcontentloaded", timeout: 30_000 },
  );
  await page.locator(
    `.kbsp-dialogue-card.is-active[data-kbsp-dialogue-asset-key="${dialogueAssetKey}"]`,
  ).waitFor({ timeout: 30_000 });
  let uploadTab = page.getByRole("button", { name: "上传素材", exact: true });
  if (await uploadTab.count() === 0) {
    await page.getByRole("button", { name: "打开素材面板", exact: true }).click();
    uploadTab = page.getByRole("button", { name: "上传素材", exact: true });
    await uploadTab.waitFor({ timeout: 30_000 });
  }
  await uploadTab.click();
  await page.getByRole("button", { name: "检索素材", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "检索全局素材库" });
  await dialog.waitFor();
  await dialog.getByLabel("补充画面或关键词（可选）").fill(query);
  const responsePromise = page.waitForResponse(
    (response) => response.request().method() === "POST"
      && /\/api\/koubo-storyboard\/tasks\/\d+\/dialogues\/[^/]+\/media-library-search\/runs$/.test(new URL(response.url()).pathname),
    { timeout: 120_000 },
  );
  await dialog.getByRole("button", { name: "开始检索", exact: true }).click();
  const response = await responsePromise;
  assert.equal(response.status(), 200);
  return {
    dialog,
    payload: await responseJson(response, "StoryBoard derived clip search"),
  };
}

async function findClip(context) {
  const response = await context.request.get(
    `${url}/api/media-library/${assetId}/clips`,
  );
  assert.equal(response.status(), 200);
  const payload = await responseJson(response, "DSCF0157 clips");
  const clip = (payload.items || payload.clips || []).find(
    (item) => item.clip_id === clipId,
  );
  assert.ok(clip, `authoritative clip ${clipId} is missing`);
  return clip;
}

const browser = await chromium.launch({
  headless: process.env.HEADED !== "1",
});
const context = await browser.newContext({
  viewport: { width: 1440, height: 1000 },
});
const report = {
  schema_version: "media_library_silent_visual_search_r2_browser_e2e_v1",
  timestamp,
  base_url: url,
  artifact_dir: artifactDir,
  asset_id: assetId,
  clip_id: clipId,
  editor_source_asset_id: editorSourceAssetId,
  clip_name: clipName,
  clip_tags: tags,
  screenshots: [],
  search_ids: [],
  import_ids: [],
  imported_paths: [],
};

try {
  await loginAdmin(context, url);
  const capabilities = await assertMediaLibraryCapabilities(context, url);
  assert.equal(capabilities.features?.visual_search_v1?.enabled, true);
  assert.equal(capabilities.features?.clip_search_v1?.enabled, true);
  const initialClip = await findClip(context);
  assert.equal(initialClip.source_asset_id, assetId);
  assert.equal(initialClip.start_ms, 1_752);
  assert.equal(initialClip.end_ms, 5_974);
  assert.equal(initialClip.duration_ms, 4_240);
  report.clip_content_sha256 = initialClip.content_sha256;
  report.clip_output_path = initialClip.output_path;
  if (initialClip.search_eligible) {
    const resetResponse = await context.request.patch(
      `${url}/api/media-library/${assetId}/clips/${clipId}`,
      { data: { search_eligible: false } },
    );
    assert.equal(resetResponse.status(), 200);
    report.resumed_from_prior_eligible_state = true;
  }

  const page = await context.newPage();
  await page.goto(
    `${url}/#/media-library/${encodeURIComponent(assetId)}/editor?return_to=media_library_detail`,
    { waitUntil: "domcontentloaded", timeout: 30_000 },
  );
  await page.locator(".ml-editor-shell").waitFor({ timeout: 30_000 });
  await page.getByRole("button", { name: "派生片段", exact: true }).click();
  const sourceClipCard = page.locator(".ml-editor-clip", { hasText: initialClip.display_name }).first();
  await sourceClipCard.waitFor({ timeout: 30_000 });
  await sourceClipCard.getByRole("button", { name: "加入全局素材检索", exact: true }).click();
  const metadataDialog = page.getByRole("dialog", { name: "编辑派生片段检索信息" });
  await metadataDialog.waitFor();
  await metadataDialog.getByLabel("片段名称").fill(clipName);
  await metadataDialog.getByLabel("标签（最多 10 项，用逗号分隔）").fill(tags.join("，"));
  await page.screenshot({ path: artifact("r2-clip-join-metadata.png"), fullPage: true });
  report.screenshots.push("r2-clip-join-metadata.png");
  const patchResponsePromise = page.waitForResponse(
    (response) => response.request().method() === "PATCH"
      && new URL(response.url()).pathname === `/api/media-library/${assetId}/clips/${clipId}`,
  );
  await metadataDialog.getByRole("button", { name: "保存并加入全局素材检索", exact: true }).click();
  const patchResponse = await patchResponsePromise;
  assert.equal(patchResponse.status(), 200);
  await page.getByText("派生片段名称、标签和全局检索状态已保存。", { exact: true }).waitFor();
  const enabledClip = await findClip(context);
  assert.equal(enabledClip.search_eligible, true);
  assert.equal(enabledClip.display_name, clipName);
  assert.deepEqual(enabledClip.tags, tags);
  assert.ok(Number(enabledClip.search_enabled_at) > 0);
  assert.ok(Number(enabledClip.search_updated_at) >= Number(enabledClip.search_enabled_at));
  report.search_enabled_at = enabledClip.search_enabled_at;
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.locator(".ml-editor-shell").waitFor({ timeout: 30_000 });
  await page.getByRole("button", { name: "派生片段", exact: true }).click();
  const enabledClipCard = page.locator(".ml-editor-clip", { hasText: clipName }).first();
  await enabledClipCard.getByText("已可在 StoryBoard 素材检索中复用", { exact: true }).waitFor();
  await enabledClipCard.getByText(`标签：${tags.join("、")}`, { exact: true }).waitFor();
  await page.screenshot({ path: artifact("r2-clip-global-search-enabled.png"), fullPage: true });
  report.screenshots.push("r2-clip-global-search-enabled.png");

  const requestedTaskId = Number(process.env.MEDIA_LIBRARY_R2_TASK_ID || 0);
  if (requestedTaskId > 0) {
    const taskResponse = await context.request.get(
      `${url}/api/koubo-storyboard/tasks/${requestedTaskId}`,
    );
    assert.equal(taskResponse.status(), 200);
    const taskPayload = await responseJson(taskResponse, "requested R2 Task");
    report.task_id = requestedTaskId;
    report.session_id = Number(taskPayload.task?.session_id);
  } else {
    const createTaskResponse = await context.request.post(
      `${url}/api/koubo-tasks/create-from-script`,
      {
        data: {
          title: `R2 派生片段全局复用验收 ${timestamp}`,
          script: "展示化橘红产品包装。随后说明这段素材会被精确复用。",
          script_format: "plain",
          industry: "食品与健康",
          product_info: "化橘红",
        },
        timeout: 30_000,
      },
    );
    assert.equal(createTaskResponse.status(), 200);
    const createdTask = await responseJson(createTaskResponse, "R2 Task creation");
    report.task_id = Number(createdTask.task_id);
    report.session_id = Number(createdTask.session_id);
  }
  assert.ok(report.task_id > 0 && report.session_id > 0);
  process.env.MEDIA_LIBRARY_STORYBOARD_E2E_TASK_ID = String(report.task_id);
  const storyboard = await findStableStoryboardDialogue(context, url);
  report.dialogue_asset_key = storyboard.dialogueAssetKey;
  report.dialogue_text = storyboard.dialogueText;

  const storyboardSearch = await openStoryboardSearch(
    page,
    report.task_id,
    report.dialogue_asset_key,
    uniqueTag,
  );
  const storyboardCandidate = storyboardSearch.payload.items?.find(
    (item) => item.candidate_id === clipId,
  );
  assert.ok(storyboardCandidate, "StoryBoard did not return the eligible derived clip");
  assert.equal(storyboardCandidate.candidate_kind, "derived_clip");
  assert.equal(storyboardCandidate.asset_id, null);
  assert.equal(storyboardCandidate.source_asset_id, assetId);
  assert.equal(storyboardCandidate.source_clip_id, clipId);
  assert.equal(storyboardCandidate.duration_ms, 4_240);
  assert.equal(storyboardCandidate.candidate_start_ms, 0);
  assert.equal(storyboardCandidate.candidate_end_ms, 4_240);
  assert.equal(storyboardCandidate.source_start_ms, 1_752);
  assert.equal(storyboardCandidate.source_end_ms, 5_974);
  assert.equal(storyboardCandidate.time_basis, "candidate");
  assert.deepEqual(storyboardCandidate.matched_fragments, []);
  assert.deepEqual(storyboardCandidate.allowed_actions, ["preview", "import_clip"]);
  assert.deepEqual(storyboardCandidate.tags, tags);
  report.storyboard_search_id = storyboardSearch.payload.search_id;
  report.search_ids.push(storyboardSearch.payload.search_id);
  const storyboardCard = storyboardSearch.dialog.locator(".kbsp-ml-search-card", { hasText: clipName }).first();
  await storyboardCard.getByText("全局素材库 · 可复用片段", { exact: true }).waitFor();
  await storyboardCard.getByText("片段时长 00:04.240", { exact: true }).waitFor();
  await page.screenshot({ path: artifact("r2-storyboard-derived-clip-result.png"), fullPage: true });
  report.screenshots.push("r2-storyboard-derived-clip-result.png");
  await storyboardCard.getByRole("button", { name: "预览片段", exact: true }).click();
  const clipVideo = storyboardCard.locator("video");
  await clipVideo.waitFor();
  assert.equal(
    decodeURIComponent(await clipVideo.getAttribute("src")),
    decodeURIComponent(storyboardCandidate.preview_url),
  );
  assert.match(
    decodeURIComponent(storyboardCandidate.preview_url),
    new RegExp(`/clips/${clipId}/`),
  );
  await page.screenshot({ path: artifact("r2-storyboard-clip-exact-preview.png"), fullPage: true });
  report.screenshots.push("r2-storyboard-clip-exact-preview.png");
  const storyboardImportPromise = page.waitForResponse(
    (response) => response.request().method() === "POST"
      && new URL(response.url()).pathname === `/api/koubo-storyboard/tasks/${report.task_id}/media-library-search/import`,
    { timeout: 60_000 },
  );
  await storyboardCard.getByRole("button", { name: "加入当前 Task", exact: true }).click();
  const storyboardImportResponse = await storyboardImportPromise;
  assert.equal(storyboardImportResponse.status(), 200);
  const storyboardImport = await responseJson(storyboardImportResponse, "StoryBoard clip import");
  assert.equal(storyboardImport.source_kind, "media_library_clip");
  assert.equal(storyboardImport.source_clip_id, clipId);
  assert.equal(storyboardImport.target_task_id, report.task_id);
  assert.equal(storyboardImport.status, "completed");
  report.import_ids.push(storyboardImport.import_id);
  report.imported_paths.push(storyboardImport.item?.path);
  await page.locator(`.kbsp-asset-scene-card[aria-label^="${clipName}"]`).first().waitFor({ timeout: 60_000 });
  await page.screenshot({ path: artifact("r2-storyboard-clip-imported.png"), fullPage: true });
  report.screenshots.push("r2-storyboard-clip-imported.png");

  await page.reload({ waitUntil: "domcontentloaded" });
  await page.locator(
    `.kbsp-dialogue-card.is-active[data-kbsp-dialogue-asset-key="${report.dialogue_asset_key}"]`,
  ).waitFor({ timeout: 30_000 });
  await page.getByRole("button", { name: "上传素材", exact: true }).click();
  await page.locator(`.kbsp-asset-scene-card[aria-label^="${clipName}"]`).first().waitFor({ timeout: 60_000 });
  await page.screenshot({ path: artifact("r2-asset-pool-import-retained.png"), fullPage: true });
  report.screenshots.push("r2-asset-pool-import-retained.png");

  await page.goto(
    `${url}/#/koubo-asset-library/tasks/${report.task_id}/search-agent`,
    { waitUntil: "domcontentloaded", timeout: 30_000 },
  );
  await page.locator(".ual-search-workspace").waitFor({ timeout: 30_000 });
  await setOnlyGlobalVideos(page);
  const agentTextarea = page.locator(
    '.ual-search-agent-panel textarea[placeholder="医院走廊里医生查看平板，横屏，真实纪录片风格"]',
  );
  await agentTextarea.fill(uniqueTag);
  const agentResponsePromise = page.waitForResponse(
    (response) => response.request().method() === "POST"
      && new URL(response.url()).pathname === `/api/koubo-storyboard/tasks/${report.task_id}/asset-library-search/search/events`,
    { timeout: 120_000 },
  );
  await page.getByRole("button", { name: "开始检索", exact: true }).click();
  const agentResponse = await agentResponsePromise;
  assert.equal(agentResponse.status(), 200);
  await agentResponse.finished();
  const agentEvents = parseSseEvents(await agentResponse.text(), "Agent derived clip search");
  const agentStarted = agentEvents.find((event) => event.type === "started");
  const agentCompleted = agentEvents.find((event) => event.type === "completed");
  const agentCandidate = agentCompleted?.items?.find((item) => item.candidate_id === clipId);
  assert.ok(agentStarted && agentCandidate, "Agent did not return the derived clip");
  assert.equal(agentCandidate.candidate_kind, "derived_clip");
  assert.deepEqual(agentCandidate.allowed_actions, ["preview", "import_clip"]);
  report.agent_search_id = agentStarted.search_id;
  report.agent_media_library_search_id = agentCandidate.media_library_search_id;
  report.search_ids.push(agentStarted.search_id, agentCandidate.media_library_search_id);
  const agentCard = page.locator(`.ual-search-card[title="${clipName}"]`).first();
  await agentCard.waitFor({ timeout: 30_000 });
  await agentCard.locator('button[title="More"]').click();
  await page.getByRole("menuitem", { name: "查看详情" }).click();
  const agentDetail = page.getByRole("dialog", { name: "候选素材详情" });
  await agentDetail.getByText("可复用派生片段", { exact: true }).waitFor();
  await agentDetail.getByText(`标签：${tags.join("、")}`, { exact: true }).waitFor();
  await page.screenshot({ path: artifact("r2-agent-derived-clip-result.png"), fullPage: true });
  report.screenshots.push("r2-agent-derived-clip-result.png");
  await agentDetail.getByRole("button", { name: "Close" }).click();
  await agentCard.locator('button[title="选择导入"]').click();
  const tray = page.locator(".ual-search-import-tray");
  await tray.getByText("1 / 12 candidates", { exact: true }).waitFor();
  await tray.getByRole("button", { name: "导入", exact: true }).click();
  const agentImportPromise = page.waitForResponse(
    (response) => response.request().method() === "POST"
      && new URL(response.url()).pathname === `/api/koubo-storyboard/tasks/${report.task_id}/asset-library-search/import`,
    { timeout: 60_000 },
  );
  await tray.getByRole("button", { name: "确认导入", exact: true }).click();
  const agentImportResponse = await agentImportPromise;
  assert.equal(agentImportResponse.status(), 200);
  const agentImport = await responseJson(agentImportResponse, "Agent clip import");
  assert.equal(agentImport.imported?.length, 1);
  assert.equal(agentImport.imported[0].source_kind, "media_library_clip");
  assert.equal(agentImport.imported[0].source_clip_id, clipId);
  report.import_ids.push(agentImport.imported[0].import_id);
  report.imported_paths.push(agentImport.imported[0].path);
  await page.screenshot({ path: artifact("r2-agent-clip-imported.png"), fullPage: true });
  report.screenshots.push("r2-agent-clip-imported.png");

  await page.goto(
    `${url}/#/media-library/${encodeURIComponent(editorSourceAssetId)}/editor?target_task_id=${report.task_id}&return_to=media_library_detail`,
    { waitUntil: "domcontentloaded", timeout: 30_000 },
  );
  await page.locator(".ml-editor-shell").waitFor({ timeout: 30_000 });
  await page.getByRole("button", { name: "素材检索", exact: true }).click();
  await page.getByLabel("补充关键词/要求").fill(uniqueTag);
  const editorSearchPromise = page.waitForResponse(
    (response) => response.request().method() === "POST"
      && new URL(response.url()).pathname === `/api/media-library/${editorSourceAssetId}/search/runs`,
    { timeout: 120_000 },
  );
  await page.getByRole("button", { name: "开始检索", exact: true }).click();
  const editorSearchResponse = await editorSearchPromise;
  assert.equal(editorSearchResponse.status(), 200);
  const editorSearch = await responseJson(editorSearchResponse, "editor derived clip search");
  const editorCandidate = editorSearch.items?.find((item) => item.candidate_id === clipId);
  assert.ok(editorCandidate, "editor did not return the derived clip");
  assert.equal(editorCandidate.candidate_kind, "derived_clip");
  report.editor_search_id = editorSearch.search_id;
  report.search_ids.push(editorSearch.search_id);
  const editorCard = page.locator(".ml-editor-candidate.media_library", { hasText: clipName }).first();
  await editorCard.getByText("全局素材库 · 可复用片段", { exact: false }).waitFor();
  assert.equal(await editorCard.getByRole("button", { name: /打开.*剪辑/ }).count(), 0);
  await page.screenshot({ path: artifact("r2-editor-derived-clip-result.png"), fullPage: true });
  report.screenshots.push("r2-editor-derived-clip-result.png");
  const editorImportPromise = page.waitForResponse(
    (response) => response.request().method() === "POST"
      && new URL(response.url()).pathname === `/api/koubo-storyboard/tasks/${report.task_id}/media-library-search/import`,
    { timeout: 60_000 },
  );
  await editorCard.getByRole("button", { name: "导入此片段", exact: true }).click();
  const editorImportResponse = await editorImportPromise;
  assert.equal(editorImportResponse.status(), 200);
  const editorImport = await responseJson(editorImportResponse, "editor clip import");
  assert.equal(editorImport.source_kind, "media_library_clip");
  report.import_ids.push(editorImport.import_id);
  report.imported_paths.push(editorImport.item?.path);
  await page.getByText(`“${clipName}”已导入目标 StoryBoard。`, { exact: true }).waitFor();
  await page.screenshot({ path: artifact("r2-editor-clip-imported.png"), fullPage: true });
  report.screenshots.push("r2-editor-clip-imported.png");

  const mobile = await browser.newContext({
    viewport: { width: 390, height: 844 },
    isMobile: true,
    hasTouch: true,
  });
  try {
    await loginAdmin(mobile, url);
    const mobilePage = await mobile.newPage();
    const mobileSearch = await openStoryboardSearch(
      mobilePage,
      report.task_id,
      report.dialogue_asset_key,
      uniqueTag,
    );
    await mobileSearch.dialog.locator(".kbsp-ml-search-card", { hasText: clipName }).first().waitFor();
    await assertNoPageOverflow(mobilePage, "mobile StoryBoard derived search");
    await mobilePage.screenshot({ path: artifact("r2-storyboard-derived-clip-mobile.png"), fullPage: true });
    report.screenshots.push("r2-storyboard-derived-clip-mobile.png");
    await mobilePage.goto(
      `${url}/#/media-library/${encodeURIComponent(assetId)}/editor?return_to=media_library_detail`,
      { waitUntil: "domcontentloaded", timeout: 30_000 },
    );
    await mobilePage.locator(".ml-editor-shell").waitFor({ timeout: 30_000 });
    await mobilePage.getByRole("button", { name: "派生片段", exact: true }).click();
    await mobilePage.locator(".ml-editor-clip", { hasText: clipName }).first().waitFor();
    await assertNoPageOverflow(mobilePage, "mobile clip eligibility");
    await mobilePage.screenshot({ path: artifact("r2-clip-global-search-mobile.png"), fullPage: true });
    report.screenshots.push("r2-clip-global-search-mobile.png");
  } finally {
    await mobile.close();
  }

  await page.goto(
    `${url}/#/media-library/${encodeURIComponent(assetId)}/editor?return_to=media_library_detail`,
    { waitUntil: "domcontentloaded", timeout: 30_000 },
  );
  await page.locator(".ml-editor-shell").waitFor({ timeout: 30_000 });
  await page.getByRole("button", { name: "派生片段", exact: true }).click();
  const removableCard = page.locator(".ml-editor-clip", { hasText: clipName }).first();
  const removePromise = page.waitForResponse(
    (response) => response.request().method() === "PATCH"
      && new URL(response.url()).pathname === `/api/media-library/${assetId}/clips/${clipId}`,
  );
  await removableCard.getByRole("button", { name: "移除全局素材检索", exact: true }).click();
  const removeResponse = await removePromise;
  assert.equal(removeResponse.status(), 200);
  await page.getByText("派生片段已移除全局素材检索；既有导入文件不受影响。", { exact: true }).waitFor();
  const disabledClip = await findClip(context);
  assert.equal(disabledClip.search_eligible, false);
  assert.equal(disabledClip.display_name, clipName);
  assert.deepEqual(disabledClip.tags, tags);
  await page.screenshot({ path: artifact("r2-clip-global-search-removed.png"), fullPage: true });
  report.screenshots.push("r2-clip-global-search-removed.png");

  const replayResponse = await context.request.get(
    `${url}/api/koubo-storyboard/tasks/${report.task_id}/media-library-search/runs/${report.storyboard_search_id}`,
  );
  assert.equal(replayResponse.status(), 200);
  const replay = await responseJson(replayResponse, "removed clip replay");
  assert.equal(replay.items?.some((item) => item.candidate_id === clipId), false);
  report.replay_removed_ineligible_clip = true;

  const zeroSearch = await openStoryboardSearch(
    page,
    report.task_id,
    report.dialogue_asset_key,
    uniqueTag,
  );
  assert.equal(zeroSearch.payload.items?.some((item) => item.candidate_id === clipId), false);
  assert.equal(zeroSearch.payload.result_count, 0);
  report.removal_search_id = zeroSearch.payload.search_id;
  report.search_ids.push(zeroSearch.payload.search_id);
  await zeroSearch.dialog.getByText("没有找到符合条件的素材", { exact: true }).waitFor();
  await page.screenshot({ path: artifact("r2-removed-clip-zero-result.png"), fullPage: true });
  report.screenshots.push("r2-removed-clip-zero-result.png");

  await page.reload({ waitUntil: "domcontentloaded" });
  await page.locator(
    `.kbsp-dialogue-card.is-active[data-kbsp-dialogue-asset-key="${report.dialogue_asset_key}"]`,
  ).waitFor({ timeout: 30_000 });
  await page.getByRole("button", { name: "上传素材", exact: true }).click();
  await page.locator(`.kbsp-asset-scene-card[aria-label^="${clipName}"]`).first().waitFor({ timeout: 60_000 });
  await page.screenshot({ path: artifact("r2-import-retained-after-removal.png"), fullPage: true });
  report.screenshots.push("r2-import-retained-after-removal.png");
  report.import_retained_after_removal = true;

  report.ok = true;
  report.completed_at = new Date().toISOString();
  writeFileSync(
    artifact("r2-browser-e2e-report.json"),
    `${JSON.stringify(report, null, 2)}\n`,
    "utf8",
  );
  console.log(JSON.stringify(report));
} catch (error) {
  report.ok = false;
  report.error = error instanceof Error ? error.stack || error.message : String(error);
  report.failed_at = new Date().toISOString();
  writeFileSync(
    artifact("r2-browser-e2e-report.json"),
    `${JSON.stringify(report, null, 2)}\n`,
    "utf8",
  );
  throw error;
} finally {
  await context.close();
  await browser.close();
}
