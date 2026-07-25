import assert from "node:assert/strict";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { chromium } from "playwright";
import {
  baseUrl,
  findStableStoryboardDialogue,
  loginAdmin,
  repoRoot,
  responseJson,
} from "./media-library-real-helpers.mjs";

const url = baseUrl();
const assetId = String(
  process.env.MEDIA_LIBRARY_LONG_WINDOW_ASSET_ID
  || "mla_1784604415914_0e96d4f546a0",
).trim();
const query = String(
  process.env.MEDIA_LIBRARY_LONG_WINDOW_QUERY || "发酵化橘红",
).trim();
const stamp = new Date().toISOString().replace(/[:.]/g, "-");
const artifactDir = resolve(
  process.env.MEDIA_LIBRARY_LONG_WINDOW_ARTIFACT_DIR
  || resolve(
    repoRoot,
    "frontend/e2e/artifacts/media-library-long-window-regression",
    stamp,
  ),
);
mkdirSync(artifactDir, { recursive: true });

const report = {
  schema_version: "media_library_long_window_regression_v1",
  started_at: new Date().toISOString(),
  base_url: url,
  asset_id: assetId,
  query,
  screenshots: [],
  console_errors: [],
  page_errors: [],
  api_failures: [],
};

const browser = await chromium.launch({
  headless: process.env.HEADED !== "1",
});
const context = await browser.newContext({
  viewport: { width: 1600, height: 1000 },
  deviceScaleFactor: 1,
});
await loginAdmin(context, url);
const page = await context.newPage();

page.on("console", (message) => {
  if (message.type() === "error") report.console_errors.push(message.text());
});
page.on("pageerror", (error) => report.page_errors.push(error.message));
page.on("response", (response) => {
  let pathname = "";
  try {
    pathname = new URL(response.url()).pathname;
  } catch {
    return;
  }
  if (pathname.startsWith("/api/") && response.status() >= 500) {
    report.api_failures.push({
      method: response.request().method(),
      pathname,
      status: response.status(),
    });
  }
});

async function capture(name, label, fullPage = true) {
  await page.evaluate(async () => {
    if (document.fonts?.ready) await document.fonts.ready;
  });
  await page.waitForTimeout(350);
  const path = resolve(artifactDir, name);
  await page.screenshot({ path, fullPage, animations: "disabled" });
  report.screenshots.push({ name, label, path, url: page.url() });
}

try {
  await page.goto(`${url}/#/media-library/${encodeURIComponent(assetId)}`, {
    waitUntil: "domcontentloaded",
    timeout: 30_000,
  });
  await page.locator(".media-library-detail-shell").waitFor({
    timeout: 30_000,
  });

  await page.getByRole("tab", { name: /对白分析/ }).click();
  const dialogueRows = page.locator(".media-library-fragment-row");
  await dialogueRows.first().waitFor();
  assert.equal(await dialogueRows.count(), 30);
  assert.equal(
    await dialogueRows.filter({ hasText: "建议复核" }).count(),
    30,
    "completed-but-unverified dialogue quality must say 建议复核",
  );
  await capture(
    "01-dialogue-complete-review-recommendation.png",
    "对白运行已完成；逐条质量提示明确为建议复核",
  );

  await page.getByRole("tab", { name: /画面分析/ }).click();
  const visualRows = page.locator(".media-library-fragment-row");
  await visualRows.first().waitFor();
  assert.equal(
    await visualRows.count(),
    5,
    "74-second fixed-camera video must expose five bounded visual windows",
  );
  await capture(
    "02-five-visual-analysis-windows.png",
    "74 秒固定机位口播已拆为 5 个连续画面分析窗口",
  );

  await page.getByRole("tab", { name: /综合分析/ }).click();
  const compositeRows = page.locator(".media-library-fragment-row");
  await compositeRows.first().waitFor();
  assert.ok(
    await compositeRows.count() >= 2,
    "long multi-window source must not publish one whole-video composite",
  );
  report.composite_fragment_count = await compositeRows.count();
  await capture(
    "03-multiple-composite-segments.png",
    "综合分析基于画面窗口输出多个可用语义片段",
  );

  const storyboard = await findStableStoryboardDialogue(context, url);
  report.storyboard = storyboard;
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
  await dialog.waitFor();
  await dialog.getByLabel("补充要求（可选）").fill(query);
  const responsePromise = page.waitForResponse(
    (response) => (
      response.request().method() === "POST"
      && /\/media-library-search\/runs$/.test(new URL(response.url()).pathname)
    ),
    { timeout: 120_000 },
  );
  await dialog.getByRole("button", { name: "开始检索", exact: true }).click();
  const response = await responsePromise;
  assert.equal(response.status(), 200);
  const search = await responseJson(response, "long-window StoryBoard search");
  const candidate = (search.items || []).find(
    (item) => item?.asset_id === assetId,
  );
  assert.ok(candidate, "real StoryBoard search must return the target source");
  assert.ok(candidate.matched_fragments?.length > 0);
  report.search = {
    search_id: search.search_id,
    target_result_count: candidate.matched_fragments.length,
    matched_fragments: candidate.matched_fragments,
  };
  const card = dialog.locator(".kbsp-ml-search-card").filter({
    hasText: candidate.display_name,
  });
  await card.waitFor();
  assert.ok(await card.getByText(/按原视频归组/).count());
  assert.equal(
    await card.locator(".kbsp-ml-search-fragments section").count(),
    candidate.matched_fragments.length,
  );
  await card.getByRole("button", { name: "剪切这个片段" }).first().waitFor();
  await card.getByRole("button", { name: /加入当前 Task（整条视频）/ }).waitFor();
  await capture(
    "04-storyboard-explicit-matched-fragments.png",
    "StoryBoard 按原视频归组，同时逐条显示可剪切的命中片段",
  );

  await card.getByRole("button", { name: "剪切这个片段" }).first().click();
  await page.locator(".ml-editor-shell").waitFor({ timeout: 30_000 });
  const first = candidate.matched_fragments[0];
  const editorHash = new URL(page.url()).hash;
  assert.ok(editorHash.includes(`start_ms=${first.start_ms}`));
  assert.ok(editorHash.includes(`end_ms=${first.end_ms}`));
  await capture(
    "05-matched-fragment-opened-in-editor.png",
    "命中片段的精确起止时间已带入剪辑页",
    false,
  );

  assert.deepEqual(report.console_errors, []);
  assert.deepEqual(report.page_errors, []);
  assert.deepEqual(report.api_failures, []);
  report.finished_at = new Date().toISOString();
  report.ok = true;
  writeFileSync(
    resolve(artifactDir, "report.json"),
    `${JSON.stringify(report, null, 2)}\n`,
    "utf8",
  );
  console.log(JSON.stringify({
    ok: true,
    artifact_dir: artifactDir,
    asset_id: assetId,
    visual_fragment_count: 5,
    composite_fragment_count: report.composite_fragment_count,
    search_id: report.search.search_id,
    matched_fragment_count: report.search.target_result_count,
    storyboard_task_id: storyboard.taskId,
  }));
} finally {
  await browser.close();
}
