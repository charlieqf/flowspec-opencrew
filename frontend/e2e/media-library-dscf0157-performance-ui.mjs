import assert from "node:assert/strict";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { chromium } from "playwright";
import {
  baseUrl,
  loginAdmin,
  repoRoot,
  responseJson,
} from "./media-library-real-helpers.mjs";

const url = baseUrl();
let assetId = String(
  process.env.MEDIA_LIBRARY_DSCF0157_ASSET_ID
  || "mla_1784601908573_70c828790521",
).trim();
const timestamp = new Intl.DateTimeFormat("sv-SE", {
  timeZone: "Australia/Sydney",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
}).format(new Date()).replace(/[ :]/g, "-");
const artifactDir = resolve(
  repoRoot,
  "frontend/e2e/artifacts/media-library-dscf0157-performance-ui",
  timestamp,
);
mkdirSync(artifactDir, { recursive: true });

const report = {
  schema_version: "media_library_dscf0157_performance_ui_v1",
  asset_id: assetId,
  started_at: new Date().toISOString(),
  artifact_dir: artifactDir,
  api: {},
  desktop: {},
  mobile: {},
  screenshots: [],
  console_errors: [],
  page_errors: [],
  api_failures: [],
};

const browser = await chromium.launch({ headless: process.env.HEADED !== "1" });

async function attachDiagnostics(page) {
  page.on("console", (message) => {
    if (message.type() === "error") report.console_errors.push({ text: message.text(), url: page.url() });
  });
  page.on("pageerror", (error) => report.page_errors.push({ message: error.message, url: page.url() }));
  page.on("response", (response) => {
    let pathname = "";
    try { pathname = new URL(response.url()).pathname; } catch { return; }
    if (pathname.startsWith("/api/") && response.status() >= 500) {
      report.api_failures.push({ status: response.status(), method: response.request().method(), pathname });
    }
  });
}

async function capture(page, filename, label) {
  await page.evaluate(async () => { if (document.fonts?.ready) await document.fonts.ready; });
  await page.waitForTimeout(250);
  const path = resolve(artifactDir, filename);
  await page.screenshot({ path, fullPage: true, animations: "disabled" });
  report.screenshots.push({ filename, label, path });
}

try {
  const context = await browser.newContext({ viewport: { width: 1600, height: 1100 }, deviceScaleFactor: 1 });
  await loginAdmin(context, url);
  if (process.env.MEDIA_LIBRARY_DSCF0157_RUN_UPLOAD === "1") {
    const uploadPage = await context.newPage();
    await attachDiagnostics(uploadPage);
    const activeChunks = new Set();
    let maxConcurrentChunks = 0;
    let chunkRequestCount = 0;
    const isChunkRequest = (request) => {
      try {
        return new URL(request.url()).pathname.endsWith("/chunks")
          && new URL(request.url()).pathname.includes("/api/media-library/uploads/");
      } catch {
        return false;
      }
    };
    uploadPage.on("request", (request) => {
      if (!isChunkRequest(request)) return;
      chunkRequestCount += 1;
      activeChunks.add(request);
      maxConcurrentChunks = Math.max(maxConcurrentChunks, activeChunks.size);
    });
    const finishChunk = (request) => { if (isChunkRequest(request)) activeChunks.delete(request); };
    uploadPage.on("requestfinished", finishChunk);
    uploadPage.on("requestfailed", finishChunk);
    await uploadPage.goto(`${url}/#/media-library`, { waitUntil: "domcontentloaded", timeout: 30_000 });
    await uploadPage.getByRole("button", { name: "上传素材" }).click();
    const dialog = uploadPage.getByRole("dialog", { name: "素材上传" });
    await dialog.waitFor({ state: "visible", timeout: 10_000 });
    const sourcePath = String(
      process.env.MEDIA_LIBRARY_DSCF0157_SOURCE_PATH
      || "/Users/macmini-4/.opencrew/sessions/382/workspace/inbox/DSCF0157.mov",
    );
    await dialog.locator('input[type="file"]').setInputFiles(sourcePath);
    const completeResponsePromise = uploadPage.waitForResponse((response) => {
      let pathname = "";
      try { pathname = new URL(response.url()).pathname; } catch { return false; }
      return response.request().method() === "POST" && pathname.endsWith("/complete") && pathname.includes("/api/media-library/uploads/");
    }, { timeout: 240_000 });
    const uploadStarted = performance.now();
    await dialog.getByRole("button", { name: "上传", exact: true }).click();
    const completeResponse = await completeResponsePromise;
    assert.equal(completeResponse.status(), 200);
    const completePayload = await responseJson(completeResponse, "DSCF0157 upload completion");
    await dialog.waitFor({ state: "hidden", timeout: 60_000 });
    const uploadedItem = completePayload.item || {};
    assert.ok(uploadedItem.asset_id);
    assert.match(String(uploadedItem.preview_url || ""), /\/SessionOutput\/media_library\/previews\//);
    assert.equal(Number(uploadedItem.size_bytes), 335_579_136);
    assert.equal(chunkRequestCount, 21);
    assert.ok(maxConcurrentChunks >= 2, `expected concurrent chunks, observed ${maxConcurrentChunks}`);
    assetId = String(uploadedItem.asset_id);
    report.asset_id = assetId;
    report.upload = {
      source_path: sourcePath,
      elapsed_ms: Math.round(performance.now() - uploadStarted),
      chunk_request_count: chunkRequestCount,
      max_concurrent_chunks: maxConcurrentChunks,
      asset_id: assetId,
      session_id: uploadedItem.session_id,
      preview_url: uploadedItem.preview_url,
      thumbnail_url: uploadedItem.thumbnail_url,
    };
    await uploadPage.getByPlaceholder("搜索素材名称、对白、标签、文件名...").fill("DSCF0157");
    await uploadPage.locator(`a[href="#/media-library/${assetId}"]`).waitFor({ state: "visible", timeout: 30_000 });
    await capture(uploadPage, "00-dscf0157-concurrent-upload-completed.png", "DSCF0157 并发上传完成并保留在素材库");
    await uploadPage.close();
  }
  const editorResponse = await context.request.get(`${url}/api/media-library/${encodeURIComponent(assetId)}/editor`);
  assert.equal(editorResponse.status(), 200);
  const editorPayload = await responseJson(editorResponse, "DSCF0157 editor");
  const item = editorPayload.item || editorPayload.asset || {};
  const previewUrl = String(item.preview_url || "");
  const thumbnailUrl = String(item.thumbnail_url || "");
  assert.match(previewUrl, /\/SessionOutput\/media_library\/previews\/[^?]+\.mp4\?v=/);
  assert.match(thumbnailUrl, /\/SessionOutput\/media_library\/previews\/[^?]+\.mp4\?v=/);
  assert.equal(Number(item.size_bytes), 335_579_136);
  assert.equal(Number(item.duration_ms), 26_000);
  report.api.editor = {
    preview_url: previewUrl,
    thumbnail_url: thumbnailUrl,
    original_source_path: item.source_video_path,
    original_size_bytes: Number(item.size_bytes),
    duration_ms: Number(item.duration_ms),
  };

  const rangeStarted = performance.now();
  const rangeResponse = await context.request.get(`${url}${previewUrl}`, {
    headers: { Range: "bytes=0-1048575" },
  });
  const rangeBody = await rangeResponse.body();
  report.api.preview_range = {
    status: rangeResponse.status(),
    elapsed_ms: Math.round(performance.now() - rangeStarted),
    bytes: rangeBody.length,
    accept_ranges: rangeResponse.headers()["accept-ranges"],
    content_range: rangeResponse.headers()["content-range"],
    cache_control: rangeResponse.headers()["cache-control"],
  };
  assert.equal(rangeResponse.status(), 206);
  if (rangeResponse.headers()["accept-ranges"] !== undefined) {
    assert.equal(rangeResponse.headers()["accept-ranges"], "bytes");
  }
  assert.equal(rangeBody.length, 1_048_576);
  assert.match(rangeResponse.headers()["content-range"] || "", /^bytes 0-1048575\/\d+$/);
  assert.match(rangeResponse.headers()["cache-control"] || "", /max-age=86400/);

  const fullStarted = performance.now();
  const fullResponse = await context.request.get(`${url}${previewUrl}`);
  const fullBody = await fullResponse.body();
  report.api.preview_full = {
    status: fullResponse.status(),
    elapsed_ms: Math.round(performance.now() - fullStarted),
    bytes: fullBody.length,
  };
  assert.equal(fullResponse.status(), 200);
  assert.ok(fullBody.length > 0 && fullBody.length < 8 * 1024 * 1024);

  const page = await context.newPage();
  await attachDiagnostics(page);
  const navigationStarted = performance.now();
  await page.goto(`${url}/#/media-library/${encodeURIComponent(assetId)}/editor`, {
    waitUntil: "domcontentloaded",
    timeout: 30_000,
  });
  const video = page.locator(".ml-editor-player-shell video");
  await video.waitFor({ state: "visible", timeout: 30_000 });
  await page.waitForFunction(() => document.querySelector(".ml-editor-player-shell video")?.readyState >= 1, null, { timeout: 10_000 });
  const metadataMs = Math.round(performance.now() - navigationStarted);
  await video.evaluate(async (node) => {
    window.__dscfPlayback = { waiting: 0, stalled: 0, errors: 0 };
    node.addEventListener("waiting", () => { window.__dscfPlayback.waiting += 1; });
    node.addEventListener("stalled", () => { window.__dscfPlayback.stalled += 1; });
    node.addEventListener("error", () => { window.__dscfPlayback.errors += 1; });
    node.muted = true;
    await node.play();
  });
  await page.waitForFunction(() => document.querySelector(".ml-editor-player-shell video")?.currentTime >= 2, null, { timeout: 8_000 });
  const playback = await video.evaluate((node) => {
    node.pause();
    return {
      ...window.__dscfPlayback,
      current_time: node.currentTime,
      duration: node.duration,
      ready_state: node.readyState,
      network_state: node.networkState,
      video_width: node.videoWidth,
      video_height: node.videoHeight,
      current_src: node.currentSrc,
    };
  });
  assert.equal(playback.errors, 0);
  assert.equal(playback.stalled, 0);
  assert.ok(playback.current_time >= 2);
  assert.equal(Math.round(playback.duration), 26);
  assert.equal(playback.video_width, 720);
  assert.equal(playback.video_height, 1280);

  const desktopLayout = await page.evaluate(() => {
    const rect = (selector) => {
      const box = document.querySelector(selector)?.getBoundingClientRect();
      return box ? { x: box.x, y: box.y, width: box.width, height: box.height, right: box.right, bottom: box.bottom } : null;
    };
    const cards = [...document.querySelectorAll(".ml-editor-time-input, .ml-editor-range-summary, .ml-editor-clip-create")]
      .map((element) => {
        const box = element.getBoundingClientRect();
        return { x: box.x, y: box.y, width: box.width, height: box.height, right: box.right, bottom: box.bottom };
      });
    return {
      background: getComputedStyle(document.querySelector(".ml-editor-page")).backgroundColor,
      body_width: document.body.scrollWidth,
      viewport_width: document.documentElement.clientWidth,
      workspace: rect(".ml-editor-workspace"),
      stage: rect(".ml-editor-stage"),
      sidebar: rect(".ml-editor-sidebar"),
      player: rect(".ml-editor-player-shell"),
      controls: rect(".ml-editor-range-controls"),
      cards,
    };
  });
  assert.equal(desktopLayout.background, "rgb(244, 247, 251)");
  assert.ok(desktopLayout.body_width <= desktopLayout.viewport_width + 1);
  assert.ok(desktopLayout.sidebar.width >= 340);
  assert.ok(desktopLayout.player.height >= 390);
  assert.ok(desktopLayout.stage.right + 8 <= desktopLayout.sidebar.x);
  for (const card of desktopLayout.cards) {
    assert.ok(card.x >= desktopLayout.controls.x - 1);
    assert.ok(card.right <= desktopLayout.controls.right + 1);
  }
  report.desktop = { metadata_ms: metadataMs, playback, layout: desktopLayout };
  await capture(page, "01-dscf0157-editor-light-desktop.png", "DSCF0157 浅色剪辑页与流畅代理播放");

  const duplicateResponse = await context.request.get(`${url}/api/media-library?q=DSCF0157&page_size=20`);
  assert.equal(duplicateResponse.status(), 200);
  const duplicatePayload = await responseJson(duplicateResponse, "DSCF0157 retained uploads");
  const retainedUploads = (duplicatePayload.items || []).map((entry) => ({
    asset_id: String(entry.asset_id || ""),
    session_id: Number(entry.session_id || 0),
    analysis_status: String(entry.analysis_status || ""),
    analysis_status_reason: String(entry.analysis_status_reason || ""),
  }));
  assert.equal(retainedUploads.length, 3, "baseline/local/public performance uploads must remain visible");
  report.retained_uploads = retainedUploads;

  const silentSearchResponse = await context.request.post(
    `${url}/api/media-library/${encodeURIComponent(assetId)}/search/runs`,
    {
      data: {
        sources: ["media_library"],
        fragment_refs: [],
        user_text: "DSCF0157",
        orientation: "any",
        limit: 12,
      },
    },
  );
  assert.equal(silentSearchResponse.status(), 200);
  const silentSearch = await responseJson(silentSearchResponse, "silent material dialogue retrieval boundary");
  const retainedIds = new Set(retainedUploads.map((entry) => entry.asset_id));
  assert.equal(
    (silentSearch.items || []).some((entry) => retainedIds.has(String(entry.asset_id || ""))),
    false,
    "silent assets without active dialogue fragments must not be falsely advertised as dialogue-searchable",
  );
  report.silent_search_boundary = {
    search_id: String(silentSearch.search_id || ""),
    query: "DSCF0157",
    result_count: Number(silentSearch.result_count || 0),
    retained_silent_asset_returned: false,
    retrieval_version: String(silentSearch.retrieval_version || ""),
  };

  await page.goto(`${url}/#/media-library/${encodeURIComponent(assetId)}`, {
    waitUntil: "domcontentloaded",
    timeout: 30_000,
  });
  await page.locator(".media-library-tool-entry.is-dialogue").waitFor({ state: "visible", timeout: 30_000 });
  await page.locator(".media-library-tool-entry.is-dialogue").click();
  const dialogueDrawer = page.getByRole("dialog", { name: "对白分析工具集" });
  await dialogueDrawer.waitFor({ state: "visible", timeout: 10_000 });
  await assert.doesNotReject(async () => {
    await dialogueDrawer.getByText("此素材没有音轨，无需 ASR 授权").waitFor({ state: "visible", timeout: 10_000 });
  });
  assert.equal(await dialogueDrawer.locator('input[type="checkbox"]').count(), 0);
  const drawerText = await dialogueDrawer.innerText();
  assert.match(drawerText, /画面分析和视频剪辑仍可使用/);
  assert.match(drawerText, /首版全库语义检索以对白为准/);
  assert.doesNotMatch(drawerText, /video_has_no_audio|Video metadata says/);
  report.no_audio_state = {
    status_label: await dialogueDrawer.locator(".media-library-status").first().innerText(),
    consent_checkbox_count: await dialogueDrawer.locator('input[type="checkbox"]').count(),
    message_sanitized: true,
  };
  await capture(page, "03-dscf0157-no-audio-business-state.png", "DSCF0157 无音轨对白状态与画面分析指引");
  await dialogueDrawer.getByRole("button", { name: "关闭" }).click();
  await page.setViewportSize({ width: 1920, height: 910 });
  await page.getByRole("tab", { name: /画面分析/ }).click();
  const visualRail = page.getByRole("complementary", { name: "片段视觉证据与语义详情" });
  await visualRail.waitFor({ state: "visible", timeout: 10_000 });
  await page.waitForFunction(() => [...document.querySelectorAll('.media-library-detail-visual-rail img')]
    .every((image) => image.complete && image.naturalWidth > 0), null, { timeout: 10_000 });
  await page.waitForTimeout(150);
  const visualRailInitial = await visualRail.evaluate((element) => ({
    client_height: element.clientHeight,
    scroll_height: element.scrollHeight,
    overflow_y: getComputedStyle(element).overflowY,
  }));
  assert.equal(visualRailInitial.overflow_y, "auto");
  assert.ok(visualRailInitial.client_height <= 910 - 24);
  const visualRailNeedsScroll = visualRailInitial.scroll_height > visualRailInitial.client_height;
  if (visualRailNeedsScroll) {
    await visualRail.evaluate((element) => { element.scrollTop = element.scrollHeight; });
  }
  await page.waitForTimeout(150);
  const visualRailBottom = await page.evaluate(() => {
    const rail = document.querySelector('.media-library-detail-visual-rail .media-library-visual-panel');
    const description = rail?.querySelector('.media-library-visual-description');
    const railBox = rail?.getBoundingClientRect();
    const descriptionBox = description?.getBoundingClientRect();
    return {
      scroll_top: rail?.scrollTop || 0,
      rail: railBox ? { top: railBox.top, bottom: railBox.bottom } : null,
      description: descriptionBox ? { top: descriptionBox.top, bottom: descriptionBox.bottom } : null,
    };
  });
  if (visualRailNeedsScroll) assert.ok(visualRailBottom.scroll_top > 0);
  assert.ok(visualRailBottom.rail.bottom <= 910);
  assert.ok(visualRailBottom.description.top >= visualRailBottom.rail.top - 1);
  assert.ok(visualRailBottom.description.bottom <= visualRailBottom.rail.bottom + 1);
  report.visual_semantic_rail = {
    viewport: { width: 1920, height: 910 },
    initial: visualRailInitial,
    bottom: visualRailBottom,
    internal_scroll_required: visualRailNeedsScroll,
    full_description_visible: true,
  };
  await capture(page, "04-dscf0157-visual-semantic-rail-scroll.png", "DSCF0157 视觉语义详情完整滚动显示");

  await page.goto(`${url}/#/media-library/${encodeURIComponent(assetId)}/editor`, {
    waitUntil: "domcontentloaded",
    timeout: 30_000,
  });
  await page.getByRole("button", { name: "派生片段" }).click();
  const retainedClipCard = page.locator(".ml-editor-clip").filter({ hasText: "化橘红倒入玻璃碗中" }).first();
  await retainedClipCard.waitFor({ state: "visible", timeout: 20_000 });
  const clipImportButton = retainedClipCard.getByRole("button", { name: "导入 StoryBoard" });
  const targetSelect = page.locator(".ml-editor-header-context select");
  assert.equal(await targetSelect.inputValue(), "");
  assert.equal(await clipImportButton.isDisabled(), true);
  const targetOptions = await targetSelect.locator("option").evaluateAll((options) => options.map((option) => ({
    value: option.value,
    label: option.textContent?.trim() || "",
  })).filter((option) => option.value));
  assert.ok(targetOptions.length > 0, "a real StoryBoard import target must be available");
  for (const option of targetOptions) {
    assert.match(option.label, new RegExp(`^Task #${option.value} · `));
  }
  const selectedTarget = targetOptions.find((option) => option.value === "308") || targetOptions[0];
  await targetSelect.selectOption(selectedTarget.value);
  assert.equal(await clipImportButton.isEnabled(), true);
  report.clip_import_target_gate = {
    clip_name: "化橘红倒入玻璃碗中",
    without_target_disabled: true,
    selected_target_id: Number(selectedTarget.value),
    selected_target_label: selectedTarget.label,
    with_target_enabled: true,
    import_executed: false,
  };
  await capture(page, "05-dscf0157-clip-import-target-selected.png", "选择 StoryBoard 目标后派生片段导入按钮启用");
  await context.close();

  const mobileContext = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 1 });
  await loginAdmin(mobileContext, url);
  const mobilePage = await mobileContext.newPage();
  await attachDiagnostics(mobilePage);
  await mobilePage.goto(`${url}/#/media-library/${encodeURIComponent(assetId)}/editor`, { waitUntil: "domcontentloaded", timeout: 30_000 });
  await mobilePage.locator(".ml-editor-range-controls").waitFor({ state: "visible", timeout: 30_000 });
  await mobilePage.waitForFunction(() => document.querySelector(".ml-editor-player-shell video")?.readyState >= 1, null, { timeout: 10_000 });
  await mobilePage.locator(".ml-editor-player-shell video").evaluate(async (node) => {
    node.muted = true;
    await node.play();
  });
  await mobilePage.waitForFunction(() => document.querySelector(".ml-editor-player-shell video")?.currentTime >= 1, null, { timeout: 8_000 });
  await mobilePage.locator(".ml-editor-player-shell video").evaluate((node) => node.pause());
  const mobileLayout = await mobilePage.evaluate(() => ({
    body_width: document.body.scrollWidth,
    viewport_width: document.documentElement.clientWidth,
    content_width: document.querySelector(".shell > .center")?.getBoundingClientRect().width || 0,
    range_columns: getComputedStyle(document.querySelector(".ml-editor-range-controls")).gridTemplateColumns,
    video_ready_state: document.querySelector(".ml-editor-player-shell video")?.readyState || 0,
  }));
  assert.ok(mobileLayout.body_width <= mobileLayout.viewport_width + 1);
  assert.ok(mobileLayout.content_width >= 300);
  assert.equal(mobileLayout.video_ready_state >= 1, true);
  report.mobile = mobileLayout;
  await capture(mobilePage, "02-dscf0157-editor-light-mobile.png", "DSCF0157 移动端浅色剪辑页布局");
  await mobileContext.close();

  assert.deepEqual(report.console_errors, []);
  assert.deepEqual(report.page_errors, []);
  assert.deepEqual(report.api_failures, []);
  report.ok = true;
} catch (error) {
  report.ok = false;
  report.error = { name: error.name, message: error.message, stack: error.stack };
  throw error;
} finally {
  report.finished_at = new Date().toISOString();
  writeFileSync(resolve(artifactDir, "dscf0157-performance-ui-report.json"), JSON.stringify(report, null, 2));
  await browser.close();
}

console.log(JSON.stringify({ ok: report.ok, artifact_dir: artifactDir, api: report.api, desktop: report.desktop, mobile: report.mobile }, null, 2));
