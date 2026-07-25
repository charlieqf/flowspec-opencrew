import assert from "node:assert/strict";
import { chromium } from "playwright";
import {
  assertMediaLibraryCapabilities,
  baseUrl,
  findStableStoryboardDialogue,
  loginAdmin,
  responseJson,
} from "./media-library-real-helpers.mjs";
import {
  buildMediaLibraryEditorHash,
} from "../src/modules/koubo/mediaLibrarySearchModel.js";

const url = baseUrl();
const expectPlannerDegraded = (
  process.env.MEDIA_LIBRARY_STORYBOARD_SEARCH_E2E_EXPECT_PLANNER_DEGRADED
  === "1"
);
const expectTelemetryFailure = (
  process.env.MEDIA_LIBRARY_STORYBOARD_SEARCH_E2E_EXPECT_TELEMETRY_FAILURE
  === "1"
);
assert.equal(
  process.env.MEDIA_LIBRARY_STORYBOARD_SEARCH_E2E_ALLOW_IMPORT,
  "1",
  "MEDIA_LIBRARY_STORYBOARD_SEARCH_E2E_ALLOW_IMPORT=1 is required because this real E2E imports the original candidate into StoryBoard",
);
const browser = await chromium.launch({
  headless: process.env.HEADED !== "1",
});
const context = await browser.newContext({
  viewport: { width: 1440, height: 1000 },
});

try {
  await loginAdmin(context, url);
  await assertMediaLibraryCapabilities(context, url);
  const storyboard = await findStableStoryboardDialogue(context, url);
  assert.ok(
    storyboard.dialogueText,
    "StoryBoard search E2E requires a real non-empty Dialogue",
  );
  const page = await context.newPage();
  await page.goto(
    `${url}/#/koubo-storyboard/tasks/${storyboard.taskId}`
      + `?dialogue_asset_key=${encodeURIComponent(storyboard.dialogueAssetKey)}`,
    { waitUntil: "domcontentloaded" },
  );
  const selected = page.locator(
    `.kbsp-dialogue-card.is-active[data-kbsp-dialogue-asset-key="${storyboard.dialogueAssetKey}"]`,
  );
  await selected.waitFor({ timeout: 30_000 });
  await page.getByRole("button", {
    name: "上传素材",
    exact: true,
  }).click();
  await page.getByRole("button", {
    name: "检索素材",
    exact: true,
  }).click();
  const dialog = page.getByRole("dialog", {
    name: "检索全局素材库",
  });
  await dialog.waitFor();
  await dialog.getByLabel("补充要求（可选）").fill(
    process.env.MEDIA_LIBRARY_STORYBOARD_SEARCH_QUERY ?? "",
  );
  const responsePromise = page.waitForResponse(
    (response) => (
      response.request().method() === "POST"
      && /\/api\/koubo-storyboard\/tasks\/\d+\/dialogues\/[^/]+\/media-library-search\/runs$/.test(
        new URL(response.url()).pathname,
      )
    ),
    { timeout: 120_000 },
  );
  await dialog.getByRole("button", {
    name: "开始检索",
    exact: true,
  }).click();
  const searchResponse = await responsePromise;
  assert.equal(
    searchResponse.status(),
    200,
    `real StoryBoard search failed: HTTP ${searchResponse.status()}`,
  );
  const searchPayload = await responseJson(
    searchResponse,
    "StoryBoard media-library search",
  );
  assert.match(String(searchPayload.search_id || ""), /^mls_/);
  assert.equal(Number.isSafeInteger(searchPayload.result_count), true);
  assert.equal(Number.isSafeInteger(searchPayload.total_count), true);
  assert.equal(Array.isArray(searchPayload.items), true);
  assert.equal(searchPayload.result_count, searchPayload.items.length);
  if (expectPlannerDegraded) {
    assert.equal(
      searchPayload.planner_degraded,
      true,
      "planner-disabled acceptance run must expose planner_degraded=true",
    );
    await dialog.locator(
      ".kbsp-ml-search-message.is-warning",
    ).filter({
      hasText: "查询规划暂时不可用",
    }).waitFor({
      timeout: 30_000,
    });
  } else {
    assert.equal(
      searchPayload.planner_degraded,
      false,
      "normal acceptance run must use the configured query planner",
    );
  }
  await dialog.locator(".kbsp-ml-search-result-head").waitFor({
    timeout: 30_000,
  });
  const cards = dialog.locator(".kbsp-ml-search-card");
  assert.ok(
    await cards.count() > 0,
    "real StoryBoard search returned zero candidates; editor round-trip cannot be accepted as an empty optional branch",
  );
  for (let index = 0; index < await cards.count(); index += 1) {
    const card = cards.nth(index);
    await card.scrollIntoViewIfNeeded();
    const layout = await card.evaluate((node) => {
      const actions = node.querySelector(".kbsp-ml-search-card-actions");
      const cardBox = node.getBoundingClientRect();
      const actionBox = actions?.getBoundingClientRect();
      return {
        flexShrink: getComputedStyle(node).flexShrink,
        actionCount: actions?.querySelectorAll("button").length || 0,
        actionsInsideCard: Boolean(
          actionBox
          && actionBox.top >= cardBox.top - 1
          && actionBox.bottom <= cardBox.bottom + 1
        ),
      };
    });
    assert.equal(
      layout.flexShrink,
      "0",
      `search result card ${index + 1} must not shrink and clip its action row`,
    );
    assert.ok(
      layout.actionCount > 0 && layout.actionsInsideCard,
      `search result card ${index + 1} must keep all actions inside the visible card`,
    );
  }
  const editorCard = cards.filter({
    has: page.getByRole("button", { name: /打开剪辑/ }),
  }).first();
  await editorCard.getByRole("button", {
    name: /打开剪辑/,
  }).waitFor();
  await editorCard.getByRole("button", {
    name: "加入当前 Task",
  }).waitFor();
  const importResponsePromise = page.waitForResponse(
    (response) => (
      response.request().method() === "POST"
      && new URL(response.url()).pathname
        === `/api/koubo-storyboard/tasks/${storyboard.taskId}/media-library-search/import`
    ),
    { timeout: 60_000 },
  );
  await editorCard.getByRole("button", {
    name: "加入当前 Task",
  }).click();
  const importResponse = await importResponsePromise;
  const importRequest = importResponse.request().postDataJSON();
  assert.equal(importResponse.status(), 200);
  assert.equal(importRequest?.source_kind, "media_library_original");
  assert.equal(importRequest?.search_id, searchPayload.search_id);
  assert.equal(
    importRequest?.dialogue_asset_key,
    storyboard.dialogueAssetKey,
  );
  assert.equal(importRequest?.target_task_id, storyboard.taskId);
  assert.match(
    String(importRequest?.idempotency_key || ""),
    /^mlui_/,
  );
  const importPayload = await responseJson(
    importResponse,
    "StoryBoard original import",
  );
  assert.match(String(importPayload.import_id || ""), /^mli_/);
  assert.equal(importPayload.status, "completed");
  assert.equal(importPayload.source_kind, "media_library_original");
  assert.equal(importPayload.target_task_id, storyboard.taskId);
  if (expectTelemetryFailure) {
    assert.equal(
      importPayload.search_action_recorded,
      false,
      "injected search telemetry failure must be visible without failing the import",
    );
  } else {
    assert.equal(
      importPayload.search_action_recorded,
      true,
      "normal original import must persist its search action",
    );
  }
  assert.equal(
    importPayload.source_asset_id,
    importRequest.source_id,
  );
  assert.ok(
    String(importPayload.item?.path || "").startsWith(
      "SessionOutput/storyboard/assets/videos/",
    ),
  );
  assert.match(
    String(importPayload.item?.provenance?.source_version || ""),
    /^[a-f0-9]{64}$/,
  );
  assert.equal(
    importPayload.item?.provenance?.source_search_id,
    searchPayload.search_id,
  );
  assert.equal(
    importPayload.item?.provenance?.source_dialogue_asset_key,
    storyboard.dialogueAssetKey,
  );
  const importedLabel = String(
    importPayload.item?.label
    || importPayload.item?.filename
    || "",
  );
  assert.ok(importedLabel);
  await page.waitForFunction(
    ({ label }) => Array.from(
      document.querySelectorAll(
        ".kbsp-asset-upload-section .kbsp-asset-scene-card",
      ),
    ).some((node) => node.getAttribute("title") === label),
    { label: importedLabel },
    { timeout: 60_000 },
  );
  const uploadTabActive = await page.getByRole("button", {
    name: "上传素材",
    exact: true,
  }).evaluate((button) => button.classList.contains("is-active"));
  assert.equal(
    uploadTabActive,
    true,
    "original import must keep the Asset Pool upload tab active",
  );
  assert.equal(
    page.url().includes(
      `#/koubo-storyboard/tasks/${storyboard.taskId}`,
    ),
    true,
  );
  await selected.waitFor({ timeout: 30_000 });

  const editorCandidate = (searchPayload.items || []).find(
    (candidate) => (
      candidate?.asset_id === importRequest.source_id
      && candidate?.allowed_actions?.includes("open_editor")
    ),
  );
  assert.ok(
    editorCandidate,
    "the imported original candidate must retain its editor action",
  );
  const matchedFragment = editorCandidate.matched_fragments?.[0] || {};
  const editorHash = buildMediaLibraryEditorHash({
    assetId: editorCandidate.asset_id,
    startMs: matchedFragment.start_ms,
    endMs: matchedFragment.end_ms,
    targetTaskId: storyboard.taskId,
    dialogueAssetKey: storyboard.dialogueAssetKey,
    searchId: searchPayload.search_id,
    matchedFragmentId: matchedFragment.fragment_id,
  });
  assert.ok(editorHash);
  const editorResponsePromise = page.waitForResponse(
    (response) => (
      response.request().method() === "GET"
      && /\/api\/media-library\/[^/]+\/editor$/.test(
        new URL(response.url()).pathname,
      )
    ),
    { timeout: 30_000 },
  );
  await page.evaluate((hash) => {
    window.location.hash = hash;
  }, editorHash);
  await page.locator(".ml-editor-shell").waitFor({
    timeout: 30_000,
  });
  const editorResponse = await editorResponsePromise;
  assert.equal(editorResponse.status(), 200);
  const editorPayload = await responseJson(
    editorResponse,
    "editor navigation context",
  );
  assert.equal(
    editorPayload.navigation_context?.target_valid,
    true,
  );
  assert.equal(
    editorPayload.navigation_context?.dialogue_valid,
    true,
  );
  assert.equal(
    editorPayload.navigation_context?.search_valid,
    true,
  );
  assert.equal(
    editorPayload.navigation_context?.dialogue_asset_key,
    storyboard.dialogueAssetKey,
  );
  assert.equal(
    editorPayload.navigation_context?.search_id,
    searchPayload.search_id,
  );

  await page.locator(".ml-editor-back").click();
  await page.waitForFunction(
    ({ taskId, dialogueAssetKey }) => (
      window.location.hash
      === `#/koubo-storyboard/tasks/${taskId}?dialogue_asset_key=${encodeURIComponent(dialogueAssetKey)}`
    ),
    storyboard,
    { timeout: 30_000 },
  );
  await selected.waitFor({ timeout: 30_000 });

  console.log(JSON.stringify({
    ok: true,
    task_id: storyboard.taskId,
    dialogue_asset_key: storyboard.dialogueAssetKey,
    search_id: searchPayload.search_id,
    result_count: searchPayload.items?.length || 0,
    original_import_checked: true,
    import_id: importPayload.import_id || importPayload.asset?.id || null,
    editor_asset_id: editorPayload.item?.asset_id || null,
    return_to_dialogue: true,
    planner_degraded: Boolean(searchPayload.planner_degraded),
    search_action_recorded: importPayload.search_action_recorded,
  }));
} finally {
  await browser.close();
}
