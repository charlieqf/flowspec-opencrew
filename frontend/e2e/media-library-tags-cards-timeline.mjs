import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import path from "node:path";
import { chromium } from "playwright";
import {
  baseUrl,
  loginAdmin,
  responseJson,
} from "./media-library-real-helpers.mjs";

const url = baseUrl();
const screenshotDir = String(process.env.OPENCREW_E2E_SCREENSHOT_DIR || "").trim();
if (screenshotDir) await mkdir(screenshotDir, { recursive: true });
const capture = async (page, filename) => {
  if (!screenshotDir) return;
  await page.screenshot({ path: path.join(screenshotDir, filename), fullPage: true });
};
const browser = await chromium.launch({ headless: process.env.HEADED !== "1" });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
await loginAdmin(context, url);

const listResponse = await context.request.get(
  `${url}/api/media-library?page_size=100&sort=updated_desc`,
);
assert.equal(listResponse.status(), 200);
const listPayload = await responseJson(listResponse, "media library list");
const requestedAssetId = String(process.env.MEDIA_LIBRARY_R2_E2E_ASSET_ID || "").trim();
const asset = (listPayload.items || []).find((item) => (
  (!requestedAssetId || item.asset_id === requestedAssetId)
  && item.upload_status === "ready"
  && !item.archived
  && item.source_version
  && Number(item.duration_ms) >= 30_000
  && Array.isArray(item.tags)
  && item.tags.length < 20
  && item.tags.every((tag) => String(tag ?? "").trim() && String(tag).trim().length <= 32)
));
assert.ok(asset, "a ready >=30s asset with editable tags is required for the R2 browser test");

const assetId = String(asset.asset_id);
const originalTags = [...asset.tags];
const testTag = `R2验收-${Date.now()}`;
const page = await context.newPage();
const apiFailures = [];
const pageErrors = [];
page.on("pageerror", (error) => pageErrors.push(error.message));
page.on("response", (response) => {
  if (new URL(response.url()).pathname.startsWith("/api/") && response.status() >= 500) {
    apiFailures.push(`${response.request().method()} ${response.url()} ${response.status()}`);
  }
});

let restored = false;
try {
  await page.goto(`${url}/#/media-library`, { waitUntil: "domcontentloaded", timeout: 30_000 });
  await page.locator(".media-library-table").waitFor({ timeout: 30_000 });
  await capture(page, "01-media-library-list.png");
  const assetLink = page.locator(`a[href="#/media-library/${encodeURIComponent(assetId)}"]`).first();
  await assetLink.waitFor();
  const row = assetLink.locator("xpath=ancestor::tr");

  await row.locator(".media-library-more-wrap > button").click();
  await page.getByRole("button", { name: "编辑标签", exact: true }).click();
  const tagDialog = page.getByRole("dialog", { name: "编辑标签" });
  await tagDialog.waitFor();
  await tagDialog.getByLabel("添加标签").fill(testTag);
  await tagDialog.getByLabel("添加标签").press("Enter");
  await tagDialog.getByText(testTag, { exact: true }).waitFor();
  await capture(page, "02-tag-editor.png");

  let failNextPatch = true;
  const patchUrl = `**/api/media-library/${encodeURIComponent(assetId)}`;
  await page.route(patchUrl, async (route) => {
    if (route.request().method() === "PATCH" && failNextPatch) {
      failNextPatch = false;
      await route.fulfill({
        status: 422,
        contentType: "application/json",
        body: JSON.stringify({
          detail: {
            code: "media_library_tag_too_long",
            message: "server text deliberately differs",
          },
        }),
      });
      return;
    }
    await route.continue();
  });
  await tagDialog.getByRole("button", { name: "保存", exact: true }).click();
  await tagDialog.getByText("单个标签最多可包含 32 个字符。").waitFor();
  await capture(page, "03-tag-server-validation.png");
  assert.equal(await tagDialog.getByText(testTag, { exact: true }).count(), 1, "failed save must retain tag input");

  const successfulPatch = page.waitForResponse((response) => (
    response.request().method() === "PATCH"
    && new URL(response.url()).pathname === `/api/media-library/${assetId}`
    && response.status() === 200
  ));
  await tagDialog.getByRole("button", { name: "保存", exact: true }).click();
  await successfulPatch;
  await tagDialog.waitFor({ state: "detached" });

  const updatedResponse = await context.request.get(`${url}/api/media-library/${encodeURIComponent(assetId)}`);
  assert.equal(updatedResponse.status(), 200);
  const updated = await responseJson(updatedResponse, "updated media asset");
  assert.ok(updated.item.tags.includes(testTag));

  await page.getByRole("button", { name: "筛选", exact: true }).click();
  await page.getByLabel("素材标签").selectOption({ label: testTag });
  await page.getByRole("button", { name: "取消", exact: true }).click();

  const hashBeforeViewSwitch = await page.evaluate(() => window.location.hash);
  await page.getByRole("button", { name: "卡片", exact: true }).click();
  await page.locator(".media-library-card-grid").waitFor();
  assert.equal(await page.evaluate(() => window.location.hash), hashBeforeViewSwitch, "view switching must preserve query state");
  assert.equal(await page.evaluate(() => localStorage.getItem("opencrew.mediaLibrary.viewMode")), "cards");
  await page.getByLabel("列数").selectOption("6");
  await page.setViewportSize({ width: 1000, height: 900 });
  assert.equal(
    await page.locator(".media-library-card-grid").evaluate((node) => getComputedStyle(node).gridTemplateColumns.split(" ").length),
    3,
    "medium viewport must cap a six-column preference at three",
  );
  await page.setViewportSize({ width: 450, height: 850 });
  assert.equal(
    await page.locator(".media-library-card-grid").evaluate((node) => getComputedStyle(node).gridTemplateColumns.split(" ").length),
    1,
    "mobile viewport must collapse cards to one column",
  );
  assert.equal(await page.evaluate(() => localStorage.getItem("opencrew.mediaLibrary.cardColumns")), "6", "responsive caps must not overwrite the preference");
  await page.setViewportSize({ width: 1440, height: 1000 });
  assert.equal(
    await page.locator(".media-library-card-grid").evaluate((node) => getComputedStyle(node).gridTemplateColumns.split(" ").length),
    6,
    "restoring the viewport must restore the preferred column count",
  );
  await page.getByLabel("列数").selectOption("2");
  assert.equal(await page.evaluate(() => localStorage.getItem("opencrew.mediaLibrary.cardColumns")), "2");
  assert.equal(
    await page.locator(".media-library-card-grid").evaluate((node) => getComputedStyle(node).gridTemplateColumns.split(" ").length),
    2,
  );
  await capture(page, "04-card-view-two-columns.png");
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.locator(".media-library-card-grid").waitFor();
  assert.equal(await page.getByLabel("列数").inputValue(), "2", "column preference must survive refresh");
  const card = page.locator(".media-library-card").filter({ has: page.locator(`a[href="#/media-library/${encodeURIComponent(assetId)}"]`) }).first();
  await card.locator(".media-library-more-wrap > button").click();
  await capture(page, "05-card-actions.png");
  for (const operation of ["重命名", "编辑标签", asset.archived ? "恢复归档" : "归档"]) {
    assert.equal(await card.getByRole("button", { name: operation, exact: true }).count(), 1);
  }
  await page.getByRole("button", { name: "列表", exact: true }).click();
  await page.locator(".media-library-table").waitFor();

  await page.goto(`${url}/#/media-library/${encodeURIComponent(assetId)}/editor`, { waitUntil: "domcontentloaded", timeout: 30_000 });
  await page.locator(".ml-editor-shell").waitFor({ timeout: 30_000 });
  await page.locator(".ml-editor-playhead-handle").waitFor();
  await page.waitForFunction(() => {
    const video = document.querySelector(".ml-editor-player-shell video");
    return Boolean(video && Number.isFinite(video.duration) && video.duration > 0);
  }, null, { timeout: 30_000 });
  await capture(page, "06-video-editor-timeline.png");

  const hitAreas = await page.evaluate(() => {
    const line = document.querySelector(".ml-editor-playhead");
    const handle = document.querySelector(".ml-editor-playhead-handle");
    const lineStyle = getComputedStyle(line);
    const handleBounds = handle.getBoundingClientRect();
    return {
      lineWidth: lineStyle.width,
      linePointerEvents: lineStyle.pointerEvents,
      handleWidth: handleBounds.width,
      handleHeight: handleBounds.height,
    };
  });
  assert.equal(hitAreas.lineWidth, "1px");
  assert.equal(hitAreas.linePointerEvents, "none");
  assert.ok(hitAreas.handleWidth >= 24 && hitAreas.handleHeight <= 30);

  await page.getByRole("button", { name: "预览选区" }).click();
  const geometry = await page.evaluate(() => {
    const viewport = document.querySelector(".ml-editor-timeline-viewport");
    const canvas = document.querySelector(".ml-editor-timeline-canvas");
    const ruler = document.querySelector(".ml-editor-ruler");
    const durationMs = Number(document.querySelector(".ml-editor-playhead-handle").getAttribute("aria-valuemax"));
    const viewportBounds = viewport.getBoundingClientRect();
    const rulerBounds = ruler.getBoundingClientRect();
    return {
      viewportLeft: viewportBounds.left,
      rulerY: rulerBounds.y + rulerBounds.height / 2,
      width: viewport.clientWidth,
      scrollLeft: viewport.scrollLeft,
      canvasWidth: canvas.getBoundingClientRect().width,
      durationMs,
    };
  });
  const startX = geometry.viewportLeft + 2;
  const middleX = geometry.viewportLeft + geometry.width * 0.35;
  const finalX = geometry.viewportLeft + geometry.width * 0.68;
  const expectedMs = Math.round((finalX - geometry.viewportLeft + geometry.scrollLeft) / (geometry.canvasWidth / geometry.durationMs));
  await page.mouse.move(startX, geometry.rulerY);
  await page.mouse.down();
  await page.mouse.move(middleX, geometry.rulerY, { steps: 4 });
  const middleMs = Number(await page.getByRole("slider", { name: "播放头" }).getAttribute("aria-valuenow"));
  await page.mouse.move(finalX, geometry.rulerY, { steps: 4 });
  const movingMs = Number(await page.getByRole("slider", { name: "播放头" }).getAttribute("aria-valuenow"));
  assert.ok(middleMs > 0 && movingMs > middleMs, "pointermove must continuously advance the playhead");
  assert.equal(await page.locator(".ml-editor-player-shell video").evaluate((video) => video.paused), true, "scrubbing must cancel range preview and pause");
  await page.mouse.up();
  await page.waitForFunction((targetMs) => {
    const slider = document.querySelector('.ml-editor-playhead-handle[role="slider"]');
    const video = document.querySelector(".ml-editor-player-shell video");
    return Number(slider?.getAttribute("aria-valuenow")) === targetMs
      && video?.paused
      && Math.abs(video.currentTime * 1000 - targetMs) <= 50;
  }, expectedMs, { timeout: 5_000 });

  const finalPlayheadMs = Number(await page.getByRole("slider", { name: "播放头" }).getAttribute("aria-valuenow"));
  assert.equal(finalPlayheadMs, expectedMs);
  await capture(page, "07-playhead-after-drag.png");
  const selectionStartBefore = await page.getByLabel("入点 ms").inputValue();
  const selectionHandle = page.locator(".ml-editor-selection-handle.start");
  const selectionBox = await selectionHandle.boundingBox();
  assert.ok(selectionBox);
  await page.mouse.move(selectionBox.x + selectionBox.width / 2, selectionBox.y + selectionBox.height / 2);
  await page.mouse.down();
  await page.mouse.move(selectionBox.x + selectionBox.width / 2 + 18, selectionBox.y + selectionBox.height / 2, { steps: 3 });
  await page.mouse.up();
  assert.notEqual(await page.getByLabel("入点 ms").inputValue(), selectionStartBefore);
  assert.equal(Number(await page.getByRole("slider", { name: "播放头" }).getAttribute("aria-valuenow")), finalPlayheadMs, "selection drag must not steal the playhead");

  await page.getByLabel("时间轴缩放").fill("90");
  await page.waitForFunction(() => {
    const viewport = document.querySelector(".ml-editor-timeline-viewport");
    return viewport && viewport.scrollWidth > viewport.clientWidth;
  });
  const edgeGeometry = await page.locator(".ml-editor-timeline-viewport").evaluate((viewport) => {
    const bounds = viewport.getBoundingClientRect();
    return { left: bounds.left, right: bounds.right, y: bounds.top + 15, scrollLeft: viewport.scrollLeft };
  });
  await page.mouse.move(edgeGeometry.left + 80, edgeGeometry.y);
  await page.mouse.down();
  await page.mouse.move(edgeGeometry.right - 2, edgeGeometry.y, { steps: 5 });
  await page.waitForTimeout(250);
  const autoScrolledLeft = await page.locator(".ml-editor-timeline-viewport").evaluate((viewport) => viewport.scrollLeft);
  assert.ok(autoScrolledLeft > edgeGeometry.scrollLeft, "edge-zone drag must auto-scroll the zoomed timeline");
  await capture(page, "08-timeline-edge-auto-scroll.png");
  await page.mouse.up();
  const releasedMs = Number(await page.getByRole("slider", { name: "播放头" }).getAttribute("aria-valuenow"));
  await page.mouse.move(edgeGeometry.left - 100, edgeGeometry.y);
  await page.waitForTimeout(80);
  assert.equal(Number(await page.getByRole("slider", { name: "播放头" }).getAttribute("aria-valuenow")), releasedMs, "pointer cleanup must stop updates after release");

  assert.deepEqual(apiFailures, []);
  assert.deepEqual(pageErrors, []);
  console.log(`media library R2 tags/cards/timeline E2E: ok (${assetId})`);
} finally {
  const restoreResponse = await context.request.patch(
    `${url}/api/media-library/${encodeURIComponent(assetId)}`,
    { data: { tags: originalTags } },
  ).catch(() => null);
  restored = restoreResponse?.status() === 200;
  await context.close();
  await browser.close();
  assert.equal(restored, true, "the browser test must restore the asset's original tags");
}
