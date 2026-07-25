import assert from "node:assert/strict";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { chromium } from "playwright";
import {
  baseUrl,
  findStableStoryboardDialogue,
  loginAdmin,
  repoRoot,
} from "../media-library-real-helpers.mjs";

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
  "frontend/e2e/artifacts/media-library-search-layering",
  timestamp,
);
mkdirSync(artifactDir, { recursive: true });

const report = {
  schema_version: "media_library_search_layering_v1",
  viewport: { width: 1920, height: 910 },
  started_at: new Date().toISOString(),
  console_errors: [],
  page_errors: [],
  api_failures: [],
};
const browser = await chromium.launch({ headless: process.env.HEADED !== "1" });

try {
  const context = await browser.newContext({ viewport: report.viewport, deviceScaleFactor: 1 });
  await loginAdmin(context, url);
  const storyboard = await findStableStoryboardDialogue(context, url);
  const page = await context.newPage();
  page.on("console", (message) => {
    if (message.type() === "error") report.console_errors.push(message.text());
  });
  page.on("pageerror", (error) => report.page_errors.push(error.message));
  page.on("response", (response) => {
    if (response.status() >= 500 && response.url().includes("/api/")) {
      report.api_failures.push({ status: response.status(), url: response.url() });
    }
  });

  await page.goto(
    `${url}/#/koubo-storyboard/tasks/${storyboard.taskId}`
      + `?dialogue_asset_key=${encodeURIComponent(storyboard.dialogueAssetKey)}`,
    { waitUntil: "domcontentloaded", timeout: 30_000 },
  );
  await page.locator(
    `.kbsp-dialogue-card.is-active[data-kbsp-dialogue-asset-key="${storyboard.dialogueAssetKey}"]`,
  ).waitFor({ timeout: 30_000 });
  await page.getByRole("button", { name: "上传素材", exact: true }).click();
  await page.getByRole("button", { name: "检索素材", exact: true }).click();

  const dialog = page.getByRole("dialog", { name: "检索全局素材库" });
  const close = dialog.getByRole("button", { name: "关闭检索素材" });
  await close.waitFor({ state: "visible", timeout: 10_000 });
  const geometry = await page.evaluate(() => {
    const toolbar = document.querySelector(".kbsp-workspace-head");
    const dialogNode = document.querySelector(".kbsp-ml-search-dialog");
    const backdrop = document.querySelector(".kbsp-ml-search-backdrop");
    const closeButton = dialogNode?.querySelector('button[aria-label="关闭检索素材"]');
    const header = dialogNode?.querySelector(":scope > header");
    const dialogRect = dialogNode?.getBoundingClientRect();
    const closeRect = closeButton?.getBoundingClientRect();
    const headerRect = header?.getBoundingClientRect();
    const hit = closeRect
      ? document.elementFromPoint(closeRect.left + closeRect.width / 2, closeRect.top + closeRect.height / 2)
      : null;
    return {
      toolbarZ: Number(getComputedStyle(toolbar).zIndex),
      backdropZ: Number(getComputedStyle(backdrop).zIndex),
      dialogZ: Number(getComputedStyle(dialogNode).zIndex),
      dialog: dialogRect && { top: dialogRect.top, bottom: dialogRect.bottom },
      header: headerRect && { top: headerRect.top, bottom: headerRect.bottom },
      close: closeRect && { top: closeRect.top, bottom: closeRect.bottom },
      closeOwnsHitTarget: Boolean(hit && closeButton?.contains(hit)),
    };
  });
  assert.ok(geometry.backdropZ > geometry.toolbarZ, "modal backdrop must cover the StoryBoard toolbar");
  assert.ok(geometry.dialogZ > geometry.backdropZ, "search dialog must sit above its backdrop");
  assert.ok(geometry.dialog.top >= 16 && geometry.dialog.bottom <= report.viewport.height - 16, "dialog must retain viewport safety margins");
  assert.ok(geometry.close.top >= geometry.header.top && geometry.close.bottom <= geometry.header.bottom, "close button must be fully inside the visible dialog header");
  assert.equal(geometry.closeOwnsHitTarget, true, "close button center must not be covered by the StoryBoard toolbar");
  report.geometry = geometry;

  const screenshot = resolve(artifactDir, "01-search-dialog-close-visible.png");
  await page.screenshot({ path: screenshot, fullPage: false, animations: "disabled" });
  report.screenshot = screenshot;
  await close.click();
  await dialog.waitFor({ state: "hidden" });
  report.close_button_clicked = true;
  assert.deepEqual(report.console_errors, []);
  assert.deepEqual(report.page_errors, []);
  assert.deepEqual(report.api_failures, []);
  report.ok = true;
  report.finished_at = new Date().toISOString();
  writeFileSync(resolve(artifactDir, "result.json"), `${JSON.stringify(report, null, 2)}\n`);
  process.stdout.write(`${JSON.stringify({ ok: true, artifact_dir: artifactDir, geometry }, null, 2)}\n`);
} catch (error) {
  report.ok = false;
  report.error = error instanceof Error ? error.stack || error.message : String(error);
  report.finished_at = new Date().toISOString();
  writeFileSync(resolve(artifactDir, "result.json"), `${JSON.stringify(report, null, 2)}\n`);
  throw error;
} finally {
  await browser.close();
}
