import assert from "node:assert/strict";
import { chromium } from "playwright";
import {
  MEDIA_LIBRARY_ZERO_RESULT_SUGGESTIONS,
} from "../src/modules/koubo/mediaLibrarySearchModel.js";
import {
  assertMediaLibraryCapabilities,
  baseUrl,
  loginAdmin,
  responseJson,
} from "./media-library-real-helpers.mjs";

const url = baseUrl();
const normalQuery = String(
  process.env.MEDIA_LIBRARY_AGENT_SEARCH_QUERY || "养肾",
).trim();
const zeroResultQuery = String(
  process.env.MEDIA_LIBRARY_AGENT_SEARCH_ZERO_QUERY
  || "锫锎钔锘虚构术语量子帆船绝对不存在",
).trim();

assert.equal(
  process.env.MEDIA_LIBRARY_AGENT_SEARCH_E2E_ALLOW_IMPORT,
  "1",
  "MEDIA_LIBRARY_AGENT_SEARCH_E2E_ALLOW_IMPORT=1 is required because this real E2E imports an original global-media candidate",
);
assert.ok(normalQuery, "normal media-library Agent query must not be empty");
assert.ok(
  zeroResultQuery,
  "zero-result media-library Agent query must not be empty",
);

function assetSearchPath(taskId, suffix) {
  return `/api/koubo-storyboard/tasks/${taskId}/asset-library-search/${suffix}`;
}

async function loadTask(context, taskId) {
  const response = await context.request.get(
    `${url}/api/koubo-storyboard/tasks/${taskId}`,
    { timeout: 15_000 },
  );
  if (response.status() !== 200) return null;
  const payload = await responseJson(response, `StoryBoard Task ${taskId}`);
  return payload?.task?.id ? payload : null;
}

async function resolveTargetTask(context) {
  const requestedTaskId = Number(
    process.env.MEDIA_LIBRARY_AGENT_SEARCH_E2E_TASK_ID || 278,
  );
  if (Number.isSafeInteger(requestedTaskId) && requestedTaskId > 0) {
    const requested = await loadTask(context, requestedTaskId);
    if (requested) return requested;
    if (process.env.MEDIA_LIBRARY_AGENT_SEARCH_E2E_TASK_ID) {
      assert.fail(
        `requested real Agent target Task ${requestedTaskId} is not available`,
      );
    }
  }

  const targetsResponse = await context.request.get(
    `${url}/api/media-library/import-targets/storyboards`,
    { timeout: 15_000 },
  );
  assert.equal(
    targetsResponse.status(),
    200,
    "failed to load StoryBoard import targets for Agent E2E",
  );
  const targetsPayload = await responseJson(
    targetsResponse,
    "StoryBoard import targets",
  );
  for (const target of targetsPayload.items || []) {
    const taskId = Number(target?.task_id);
    if (!Number.isSafeInteger(taskId) || taskId <= 0) continue;
    const detail = await loadTask(context, taskId);
    if (detail) return detail;
  }
  assert.fail("no real StoryBoard Task is available for Agent E2E");
}

function parseSseEvents(body, label) {
  const events = [];
  for (const block of String(body || "").split(/\r?\n\r?\n/)) {
    for (const line of block.split(/\r?\n/)) {
      if (!line.startsWith("data: ")) continue;
      try {
        events.push(JSON.parse(line.slice(6)));
      } catch {
        assert.fail(`${label} returned an invalid SSE event: ${line}`);
      }
    }
  }
  assert.ok(events.length, `${label} returned no SSE events`);
  const failed = events.find((event) => event?.type === "failed");
  assert.equal(
    failed,
    undefined,
    `${label} failed: ${String(failed?.detail || "")}`,
  );
  return events;
}

function eventOfType(events, type, label) {
  const event = events.find((item) => item?.type === type);
  assert.ok(event, `${label} did not emit ${type}`);
  return event;
}

async function setOnlyGlobalVideos(page) {
  const filters = page.locator(
    ".ual-search-agent-panel .ual-search-panel-brief .ual-search-filters",
  );
  await filters.waitFor({ state: "visible", timeout: 30_000 });
  const rows = filters.locator(".ual-search-filter-row");
  assert.ok(
    await rows.count() >= 2,
    "Search-Agent source/type filters are missing",
  );

  const sourceButtons = rows.nth(0).locator("button");
  for (let index = 0; index < await sourceButtons.count(); index += 1) {
    const button = sourceButtons.nth(index);
    const text = String(await button.innerText()).replace(/\s+/g, " ").trim();
    const shouldBeActive = text.startsWith("全局素材库");
    const isActive = await button.evaluate(
      (node) => node.classList.contains("is-active"),
    );
    if (isActive !== shouldBeActive) await button.click();
  }

  const typeButtons = rows.nth(1).locator("button");
  for (let index = 0; index < await typeButtons.count(); index += 1) {
    const button = typeButtons.nth(index);
    const text = String(await button.innerText()).trim();
    const shouldBeActive = text === "视频";
    const isActive = await button.evaluate(
      (node) => node.classList.contains("is-active"),
    );
    if (isActive !== shouldBeActive) await button.click();
  }

  assert.deepEqual(
    await sourceButtons.evaluateAll((buttons) => buttons
      .filter((button) => button.classList.contains("is-active"))
      .map((button) => button.innerText.replace(/\s+/g, " ").trim())
      .filter(Boolean)),
    [await sourceButtons.filter({ hasText: "全局素材库" }).first()
      .innerText().then((text) => text.replace(/\s+/g, " ").trim())],
    "Agent E2E must select only the global media library source",
  );
  assert.deepEqual(
    await typeButtons.evaluateAll((buttons) => buttons
      .filter((button) => button.classList.contains("is-active"))
      .map((button) => button.innerText.trim())),
    ["视频"],
    "Agent E2E must select only video media",
  );
}

async function runSearch(page, taskId, query, label) {
  const textarea = page.locator(
    '.ual-search-agent-panel textarea[placeholder="医院走廊里医生查看平板，横屏，真实纪录片风格"]',
  );
  await textarea.fill(query);
  const endpoint = assetSearchPath(taskId, "search/events");
  const responsePromise = page.waitForResponse(
    (response) => (
      response.request().method() === "POST"
      && new URL(response.url()).pathname === endpoint
    ),
    { timeout: 120_000 },
  );
  await page.getByRole("button", {
    name: "开始检索",
    exact: true,
  }).click();
  const response = await responsePromise;
  assert.equal(
    response.status(),
    200,
    `${label} failed: HTTP ${response.status()}`,
  );
  const request = response.request().postDataJSON();
  assert.deepEqual(
    request?.sources,
    ["media_library"],
    `${label} must send only media_library`,
  );
  assert.deepEqual(
    request?.media_types,
    ["video"],
    `${label} must send only video`,
  );
  await response.finished();
  const events = parseSseEvents(await response.text(), label);
  const started = eventOfType(events, "started", label);
  const plan = eventOfType(events, "plan", label);
  const completed = eventOfType(events, "completed", label);
  assert.match(String(started.search_id || ""), /^search_\d+_[a-f0-9]+$/);
  assert.equal(completed.search_id, started.search_id);
  assert.deepEqual(
    plan.plan?.sources,
    ["media_library"],
    `${label} planner must not add sources`,
  );
  assert.deepEqual(
    plan.plan?.media_types,
    ["video"],
    `${label} planner must preserve the video-only scope`,
  );
  const providerStarted = events.filter(
    (event) => event?.type === "provider.started",
  );
  const providerCompleted = events.filter(
    (event) => event?.type === "provider.completed",
  );
  assert.deepEqual(
    providerStarted.map((event) => event.provider),
    ["media_library"],
    `${label} must invoke only media_library`,
  );
  assert.deepEqual(
    providerCompleted.map((event) => event.provider),
    ["media_library"],
    `${label} must complete only media_library`,
  );
  return {
    completed,
    events,
    plan: plan.plan,
    request,
    searchId: started.search_id,
  };
}

const browser = await chromium.launch({
  headless: process.env.HEADED !== "1",
});
const context = await browser.newContext({
  viewport: { width: 1440, height: 1000 },
});

try {
  await loginAdmin(context, url);
  await assertMediaLibraryCapabilities(context, url);
  const target = await resolveTargetTask(context);
  const taskId = Number(target.task.id);
  const targetSessionId = Number(
    target.task.session_id || target.meta?.analysis_session_id || 0,
  );
  assert.ok(targetSessionId > 0, "target Task must have a real session");

  const page = await context.newPage();
  const settingsResponsePromise = page.waitForResponse(
    (response) => (
      response.request().method() === "GET"
      && new URL(response.url()).pathname
        === assetSearchPath(taskId, "settings")
    ),
    { timeout: 30_000 },
  );
  await page.goto(
    `${url}/?mediaLibraryAgentE2E=${Date.now()}`
      + `#/koubo-asset-library/tasks/${taskId}/search-agent`,
    { waitUntil: "domcontentloaded", timeout: 30_000 },
  );
  const settingsResponse = await settingsResponsePromise;
  assert.equal(settingsResponse.status(), 200);
  await page.locator(".ual-search-workspace").waitFor({
    state: "visible",
    timeout: 30_000,
  });
  await page.locator(".ual-search-agent-panel").waitFor({
    state: "visible",
    timeout: 30_000,
  });
  assert.equal(
    await page.locator(".ual-nav button.is-active").innerText(),
    "素材检索智能体",
  );
  await setOnlyGlobalVideos(page);

  const normal = await runSearch(
    page,
    taskId,
    normalQuery,
    "real global-media Agent search",
  );
  const candidates = Array.isArray(normal.completed.items)
    ? normal.completed.items
    : [];
  assert.equal(
    normal.completed.candidate_count,
    candidates.length,
  );
  assert.ok(
    candidates.length > 0,
    `real Agent query returned no global candidates: ${normalQuery}`,
  );
  for (const candidate of candidates) {
    assert.equal(candidate.provider, "media_library");
    assert.equal(candidate.source, "media_library");
    assert.equal(candidate.media_type, "video");
    assert.equal(candidate.global_media_library, true);
    assert.equal(candidate.local_reuse, false);
    assert.equal(candidate.candidate_id, candidate.asset_id);
    assert.equal(candidate.provider_asset_id, candidate.asset_id);
    assert.match(String(candidate.media_library_search_id || ""), /^mls_/);
    assert.match(String(candidate.source_version || ""), /^[a-f0-9]{64}$/);
    assert.deepEqual(
      candidate.allowed_actions,
      ["preview", "open_editor", "import_original"],
    );
  }
  const sourceLabels = page.locator(
    ".ual-search-card .ual-search-source-label.is-media_library",
  );
  await sourceLabels.first().waitFor({ state: "visible", timeout: 30_000 });
  assert.equal(await sourceLabels.count(), candidates.length);
  assert.equal(
    await sourceLabels.first().innerText(),
    "全局素材库",
    "Agent candidate must visibly distinguish the global media-library source",
  );

  const firstCandidate = candidates[0];
  const firstCard = page.locator(".ual-search-card").first();
  await firstCard.locator('button[title="选择导入"]').click();
  const importResponsePromise = page.waitForResponse(
    (response) => (
      response.request().method() === "POST"
      && new URL(response.url()).pathname
        === assetSearchPath(taskId, "import")
    ),
    { timeout: 120_000 },
  );
  await page.getByRole("button", {
    name: "导入",
    exact: true,
  }).click();
  const confirm = page.getByRole("alertdialog", {
    name: "Confirm asset import",
  });
  await confirm.waitFor({ state: "visible" });
  await confirm.getByRole("button", {
    name: "确认导入",
    exact: true,
  }).click();
  const importResponse = await importResponsePromise;
  assert.equal(
    importResponse.status(),
    200,
    `real Agent original import failed: HTTP ${importResponse.status()}`,
  );
  const importRequest = importResponse.request().postDataJSON();
  assert.equal(importRequest?.search_id, normal.searchId);
  assert.deepEqual(importRequest?.candidate_ids, [
    firstCandidate.candidate_id,
  ]);
  assert.equal(importRequest?.confirm_license, true);
  const importPayload = await responseJson(
    importResponse,
    "real Agent original import",
  );
  assert.equal(importPayload.ok, true);
  assert.deepEqual(importPayload.failed, []);
  assert.equal(importPayload.imported?.length, 1);
  const imported = importPayload.imported[0];
  assert.equal(imported.global_media_library, true);
  assert.equal(imported.local_reuse, false);
  assert.equal(imported.source_kind, "media_library_original");
  assert.equal(imported.source, "media_library_original");
  assert.equal(imported.source_asset_id, firstCandidate.asset_id);
  assert.equal(
    imported.media_library_search_id,
    firstCandidate.media_library_search_id,
  );
  assert.equal(imported.source_version, firstCandidate.source_version);
  assert.match(String(imported.import_id || ""), /^mli_/);
  assert.ok(
    String(imported.path || "").startsWith(
      "SessionOutput/storyboard/assets/videos/",
    ),
  );
  assert.equal(
    imported.provenance?.source,
    "media_library_original",
  );
  assert.equal(
    imported.provenance?.source_asset_id,
    firstCandidate.asset_id,
  );
  assert.equal(
    imported.provenance?.source_search_id,
    firstCandidate.media_library_search_id,
  );
  assert.equal(
    imported.provenance?.source_version,
    firstCandidate.source_version,
  );
  assert.match(
    String(imported.provenance?.content_sha256 || ""),
    /^[a-f0-9]{64}$/,
  );
  assert.notEqual(
    Number(imported.provenance?.source_session_id),
    targetSessionId,
    "Agent acceptance must prove a real cross-session original import",
  );
  const importResult = page.locator(
    ".ual-search-import-results .is-success",
  ).first();
  await importResult.waitFor({ state: "visible", timeout: 60_000 });
  assert.match(
    await importResult.innerText(),
    /^(已导入|已存在，复用) · /,
  );

  await setOnlyGlobalVideos(page);
  const zero = await runSearch(
    page,
    taskId,
    zeroResultQuery,
    "real zero-result global-media Agent search",
  );
  assert.equal(zero.completed.candidate_count, 0);
  assert.deepEqual(zero.completed.items, []);
  assert.equal(
    zero.events.some((event) => event?.type === "candidate.batch"),
    false,
    "zero-result search must not surface a silently broadened candidate batch",
  );
  const zeroProvider = eventOfType(
    zero.events,
    "provider.completed",
    "real zero-result global-media Agent search",
  );
  assert.equal(zeroProvider.provider, "media_library");
  assert.equal(Number(zeroProvider.kept || 0), 0);

  const persistedResponse = await context.request.get(
    `${url}${assetSearchPath(taskId, `runs/${zero.searchId}`)}`,
    { timeout: 15_000 },
  );
  assert.equal(persistedResponse.status(), 200);
  const persistedPayload = await responseJson(
    persistedResponse,
    "persisted zero-result Agent search",
  );
  assert.equal(
    persistedPayload.run?.search_id,
    zero.searchId,
  );
  assert.deepEqual(
    persistedPayload.run?.request?.sources,
    ["media_library"],
  );
  assert.deepEqual(
    persistedPayload.run?.plan?.sources,
    ["media_library"],
  );
  assert.deepEqual(
    persistedPayload.run?.candidates,
    [],
    "persisted zero-result run must remain empty without auto-relaxation",
  );

  const empty = page.locator(".ual-search-empty");
  await empty.waitFor({ state: "visible", timeout: 30_000 });
  await empty.getByText("暂无候选素材", { exact: true }).waitFor();
  await empty.getByText(
    "全局素材库不会自动放宽“原始视频、未归档、已完成对白分析”等资格条件。可以尝试：",
    { exact: true },
  ).waitFor();
  const suggestions = await empty.locator("li").allInnerTexts();
  assert.deepEqual(
    suggestions,
    [...MEDIA_LIBRARY_ZERO_RESULT_SUGGESTIONS],
    "zero-result UI must show all four explicit query-change suggestions",
  );

  console.log(JSON.stringify({
    ok: true,
    task_id: taskId,
    target_session_id: targetSessionId,
    normal_query: normalQuery,
    agent_search_id: normal.searchId,
    media_library_search_id: firstCandidate.media_library_search_id,
    candidate_count: candidates.length,
    source_asset_id: firstCandidate.asset_id,
    import_id: imported.import_id,
    imported_path: imported.path,
    source_session_id: imported.provenance.source_session_id,
    zero_query: zeroResultQuery,
    zero_search_id: zero.searchId,
    zero_result_count: zero.completed.candidate_count,
    zero_suggestions: suggestions,
    auto_relaxation_observed: false,
  }));
} finally {
  await context.close();
  await browser.close();
}
