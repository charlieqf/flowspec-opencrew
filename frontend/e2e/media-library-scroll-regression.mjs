import assert from "node:assert/strict";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { chromium } from "playwright";
import { baseUrl, loginAdmin, repoRoot } from "./media-library-real-helpers.mjs";

const url = baseUrl();
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
  "frontend/e2e/artifacts/media-library-scroll-regression",
  timestamp,
);
mkdirSync(artifactDir, { recursive: true });

const report = {
  schema_version: "media_library_scroll_regression_v1",
  viewport: { width: 1920, height: 878 },
  started_at: new Date().toISOString(),
  screenshots: [],
  console_errors: [],
  page_errors: [],
  api_failures: [],
};

function attachDiagnostics(page) {
  page.on("console", (message) => {
    if (message.type() === "error") report.console_errors.push(message.text());
  });
  page.on("pageerror", (error) => report.page_errors.push(error.message));
  page.on("response", (response) => {
    if (response.status() >= 500 && response.url().includes("/api/")) {
      report.api_failures.push({ status: response.status(), url: response.url() });
    }
  });
}

async function capture(page, filename, label) {
  const path = resolve(artifactDir, filename);
  await page.screenshot({ path, fullPage: false, animations: "disabled" });
  report.screenshots.push({ filename, label, path });
}

const browser = await chromium.launch({ headless: process.env.HEADED !== "1" });
try {
  const context = await browser.newContext({ viewport: report.viewport, deviceScaleFactor: 1 });
  await loginAdmin(context, url);
  const page = await context.newPage();
  attachDiagnostics(page);

  await page.goto(`${url}/#/media-library`, { waitUntil: "domcontentloaded", timeout: 30_000 });
  await page.locator(".media-library-table tbody tr").first().waitFor({ timeout: 20_000 });
  const editorHref = await page.locator('.media-library-asset-copy a').first().getAttribute("href");
  assert.ok(editorHref?.startsWith("#/media-library/"), "a real library asset is required for the editor check");

  const tableBefore = await page.locator(".media-library-table-wrap").evaluate((node) => {
    const rect = node.getBoundingClientRect();
    const header = node.querySelector("thead th").getBoundingClientRect();
    return {
      clientHeight: node.clientHeight,
      scrollHeight: node.scrollHeight,
      scrollTop: node.scrollTop,
      top: rect.top,
      bottom: rect.bottom,
      headerTop: header.top,
    };
  });
  assert.ok(tableBefore.scrollHeight > tableBefore.clientHeight, "the real 12-item list must have an internal vertical scroll range");
  const tableAfter = await page.locator(".media-library-table-wrap").evaluate((node) => {
    node.scrollTop = node.scrollHeight;
    node.dispatchEvent(new Event("scroll", { bubbles: true }));
    const rect = node.getBoundingClientRect();
    const header = node.querySelector("thead th").getBoundingClientRect();
    const lastRow = node.querySelector("tbody tr:last-child").getBoundingClientRect();
    return {
      clientHeight: node.clientHeight,
      scrollHeight: node.scrollHeight,
      scrollTop: node.scrollTop,
      top: rect.top,
      bottom: rect.bottom,
      headerTop: header.top,
      lastRowTop: lastRow.top,
      lastRowBottom: lastRow.bottom,
    };
  });
  assert.ok(tableAfter.scrollTop > 0, "the material list must scroll vertically");
  assert.ok(Math.abs(tableAfter.headerTop - tableAfter.top) <= 2, "the material table header must stay sticky while rows scroll");
  assert.ok(tableAfter.lastRowTop < tableAfter.bottom && tableAfter.lastRowBottom <= tableAfter.bottom + 2, "the final material row must be reachable");
  const pagination = await page.locator(".media-library-pagination").boundingBox();
  assert.ok(pagination && pagination.y + pagination.height <= report.viewport.height, "pagination must remain visible below the independently scrolling list");
  report.material_list = { before: tableBefore, after: tableAfter, pagination };
  await capture(page, "01-material-list-bottom-visible.png", "素材列表滚到底后，末行与分页仍在 1920×878 视口内");

  const editorUrl = `${url}/${editorHref}/editor`;
  await page.goto(editorUrl, { waitUntil: "domcontentloaded", timeout: 30_000 });
  await page.locator(".ml-editor-timeline-panel").waitFor({ timeout: 20_000 });
  const editorBefore = await page.locator(".ml-editor-page").evaluate((node) => ({
    clientHeight: node.clientHeight,
    scrollHeight: node.scrollHeight,
    scrollTop: node.scrollTop,
    overflowY: getComputedStyle(node).overflowY,
  }));
  assert.equal(editorBefore.overflowY, "auto");
  assert.ok(editorBefore.scrollHeight > editorBefore.clientHeight, "the editor must own a vertical scroll range when the timeline is below the fold");
  await page.locator(".ml-editor-timeline-panel").hover();
  await page.mouse.wheel(0, 65);
  await page.waitForFunction(() => {
    const node = document.querySelector(".ml-editor-page");
    return node && node.scrollTop >= node.scrollHeight - node.clientHeight - 1;
  });
  const editorAfter = await page.locator(".ml-editor-page").evaluate((node) => {
    const pageRect = node.getBoundingClientRect();
    const timeline = node.querySelector(".ml-editor-timeline-panel").getBoundingClientRect();
    const status = node.querySelector(".ml-editor-timeline-status").getBoundingClientRect();
    const tracks = [...node.querySelectorAll(".ml-editor-track-head")].map((track) => ({
      label: track.textContent.trim(),
      top: track.getBoundingClientRect().top,
      bottom: track.getBoundingClientRect().bottom,
    }));
    return {
      clientHeight: node.clientHeight,
      scrollHeight: node.scrollHeight,
      scrollTop: node.scrollTop,
      pageTop: pageRect.top,
      pageBottom: pageRect.bottom,
      timelineTop: timeline.top,
      timelineBottom: timeline.bottom,
      statusTop: status.top,
      statusBottom: status.bottom,
      tracks,
    };
  });
  assert.ok(editorAfter.scrollTop > 0, "mouse-wheel/page scrolling must reach the timeline");
  assert.ok(editorAfter.timelineTop >= editorAfter.pageTop - 2 && editorAfter.timelineBottom <= editorAfter.pageBottom + 2, "the complete timeline panel must be visible after scrolling");
  assert.ok(editorAfter.timelineBottom <= editorAfter.pageBottom - 48, "the complete timeline must retain bottom clearance for floating browser/application controls");
  assert.ok(editorAfter.statusBottom <= editorAfter.pageBottom + 2, "the timeline footer must not be clipped");
  assert.deepEqual(editorAfter.tracks.map(({ label }) => label), ["综综合片段", "白对白片段", "画画面片段", "源原视频"]);
  assert.ok(editorAfter.tracks.every((track) => track.top >= editorAfter.pageTop && track.bottom <= editorAfter.pageBottom), "every timeline track must be visible");
  report.editor = { url: editorUrl, before: editorBefore, after: editorAfter };
  await capture(page, "02-editor-timeline-complete.png", "鼠标滚动后完整显示综合、对白、画面、原视频轨和时间轴底栏");

  assert.deepEqual(report.console_errors, []);
  assert.deepEqual(report.page_errors, []);
  assert.deepEqual(report.api_failures, []);
  report.ok = true;
  report.finished_at = new Date().toISOString();
  writeFileSync(resolve(artifactDir, "result.json"), `${JSON.stringify(report, null, 2)}\n`);
  process.stdout.write(`${JSON.stringify({ ok: true, artifact_dir: artifactDir, material_list: report.material_list, editor: report.editor }, null, 2)}\n`);
} catch (error) {
  report.ok = false;
  report.error = error instanceof Error ? error.stack || error.message : String(error);
  report.finished_at = new Date().toISOString();
  writeFileSync(resolve(artifactDir, "result.json"), `${JSON.stringify(report, null, 2)}\n`);
  throw error;
} finally {
  await browser.close();
}
