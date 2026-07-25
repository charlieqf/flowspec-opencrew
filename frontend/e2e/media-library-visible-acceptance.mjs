import assert from "node:assert/strict";
import {
  mkdirSync,
  writeFileSync,
} from "node:fs";
import { resolve } from "node:path";
import { chromium, webkit } from "playwright";
import {
  assertMediaLibraryCapabilities,
  baseUrl,
  loginAdmin,
  poll,
  repoRoot,
  responseJson,
} from "./media-library-real-helpers.mjs";

const url = baseUrl();
const analyzedAssetId = String(
  process.env.MEDIA_LIBRARY_VISIBLE_E2E_ANALYZED_ASSET_ID
  || "mla_1784591754592_d1c20d9832d6",
).trim();
const internalCandidateAssetId = String(
  process.env.MEDIA_LIBRARY_VISIBLE_E2E_INTERNAL_CANDIDATE_ASSET_ID
  || "mla_1784500575045_607e5f061102",
).trim();
const tenMinuteAssetId = String(
  process.env.MEDIA_LIBRARY_VISIBLE_E2E_TEN_MINUTE_ASSET_ID
  || "mla_1784525528366_d0ef81c77437",
).trim();
const targetTaskId = Number(
  process.env.MEDIA_LIBRARY_VISIBLE_E2E_TASK_ID || 308,
);
const targetSessionId = Number(
  process.env.MEDIA_LIBRARY_VISIBLE_E2E_SESSION_ID || 380,
);
assert.ok(analyzedAssetId);
assert.ok(tenMinuteAssetId);
assert.ok(Number.isSafeInteger(targetTaskId) && targetTaskId > 0);
assert.ok(Number.isSafeInteger(targetSessionId) && targetSessionId > 0);
const browserEngine = String(
  process.env.MEDIA_LIBRARY_VISIBLE_E2E_BROWSER || "webkit",
).trim().toLowerCase();
assert.ok(
  ["chromium", "webkit"].includes(browserEngine),
  `unsupported browser engine: ${browserEngine}`,
);

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
  "frontend/e2e/artifacts/media-library-visible-acceptance",
  timestamp,
);
mkdirSync(artifactDir, { recursive: true });

const reuseClipId = String(
  process.env.MEDIA_LIBRARY_VISIBLE_E2E_REUSE_CLIP_ID || "",
).trim();
const reuseClipJobId = String(
  process.env.MEDIA_LIBRARY_VISIBLE_E2E_REUSE_CLIP_JOB_ID || "",
).trim();
let clipName = String(
  process.env.MEDIA_LIBRARY_VISIBLE_E2E_REUSE_CLIP_NAME
  || `UI验收留存-中文口播场景切换-${timestamp}`,
).trim();
const report = {
  schema_version: "media_library_visible_acceptance_v2",
  started_at: new Date().toISOString(),
  base_url: url,
  browser_engine: browserEngine,
  artifact_dir: artifactDir,
  task_id: targetTaskId,
  session_id: targetSessionId,
  analyzed_asset_id: analyzedAssetId,
  internal_candidate_asset_id: internalCandidateAssetId,
  ten_minute_asset_id: tenMinuteAssetId,
  clip_name: clipName,
  screenshots: [],
  api_failures: [],
  console_errors: [],
  page_errors: [],
  checks: {},
  retained: {},
};

const browserType = browserEngine === "webkit" ? webkit : chromium;
const browser = await browserType.launch({
  headless: process.env.HEADED !== "1",
});
const context = await browser.newContext({
  viewport: { width: 1600, height: 1100 },
  deviceScaleFactor: 1,
});
await loginAdmin(context, url);
const capabilities = await assertMediaLibraryCapabilities(context, url);
report.capabilities = capabilities;
const page = await context.newPage();

page.on("console", (message) => {
  if (message.type() === "error") {
    report.console_errors.push({
      text: message.text(),
      url: page.url(),
    });
  }
});
page.on("pageerror", (error) => {
  report.page_errors.push({
    message: error.message,
    url: page.url(),
  });
});
page.on("response", (response) => {
  let pathname = "";
  try {
    pathname = new URL(response.url()).pathname;
  } catch {
    return;
  }
  if (
    pathname.startsWith("/api/")
    && response.status() >= 500
  ) {
    report.api_failures.push({
      method: response.request().method(),
      status: response.status(),
      pathname,
    });
  }
});

async function settle() {
  await page.evaluate(async () => {
    if (document.fonts?.ready) await document.fonts.ready;
  });
  await page.waitForTimeout(400);
}

async function screenshot(name, label) {
  await settle();
  const path = resolve(artifactDir, name);
  const metrics = await page.evaluate(() => ({
    title: document.title,
    hash: window.location.hash,
    body_width: document.body.scrollWidth,
    viewport_width: document.documentElement.clientWidth,
    body_height: document.body.scrollHeight,
    viewport_height: document.documentElement.clientHeight,
    active_element: document.activeElement?.tagName || "",
  }));
  await page.screenshot({
    path,
    fullPage: true,
    animations: "disabled",
  });
  report.screenshots.push({
    name,
    label,
    path,
    url: page.url(),
    metrics,
  });
}

async function assertViewportUsable(locator, label, { minWidth = 44 } = {}) {
  await locator.scrollIntoViewIfNeeded();
  const box = await locator.boundingBox();
  const viewport = page.viewportSize();
  assert.ok(box, `${label}: element has no visible bounding box`);
  assert.ok(viewport, `${label}: viewport is unavailable`);
  assert.ok(box.width >= minWidth, `${label}: width ${box.width} is below ${minWidth}`);
  assert.ok(box.x >= -1, `${label}: clipped on the left (${box.x})`);
  assert.ok(
    box.x + box.width <= viewport.width + 1,
    `${label}: clipped on the right (${box.x + box.width} > ${viewport.width})`,
  );
  assert.ok(
    box.y < viewport.height && box.y + box.height > 0,
    `${label}: not reachable in the current viewport`,
  );
  return box;
}

async function mobileShellGeometry(label) {
  const geometry = await page.evaluate(() => {
    const shell = document.querySelector(".shell");
    const left = document.querySelector(".shell > .left");
    const center = document.querySelector(".shell > .center");
    const shellBox = shell?.getBoundingClientRect();
    const leftBox = left?.getBoundingClientRect();
    const centerBox = center?.getBoundingClientRect();
    return {
      shell_width: Math.round(shellBox?.width || 0),
      navigation_width: Math.round(leftBox?.width || 0),
      content_width: Math.round(centerBox?.width || 0),
      viewport_width: window.innerWidth,
    };
  });
  assert.ok(
    geometry.navigation_width <= 72,
    `${label}: navigation consumes ${geometry.navigation_width}px`,
  );
  assert.ok(
    geometry.content_width >= 300,
    `${label}: content width ${geometry.content_width}px is not usable`,
  );
  assert.equal(geometry.shell_width, geometry.viewport_width, `${label}: shell width`);
  return geometry;
}

async function jsonGet(pathname, label) {
  const response = await context.request.get(`${url}${pathname}`, {
    timeout: 30_000,
  });
  assert.equal(
    response.status(),
    200,
    `${label} failed: HTTP ${response.status()}`,
  );
  return responseJson(response, label);
}

async function taskMetadata() {
  const detail = await jsonGet(
    `/api/koubo-tasks/${targetTaskId}`,
    "acceptance Task",
  );
  const item = detail.item || {};
  assert.equal(Number(item.id || item.task_id), targetTaskId);
  assert.equal(Number(item.session_id), targetSessionId);
  return item;
}

async function openHash(hash) {
  await page.goto(`${url}/${hash}`, {
    waitUntil: "domcontentloaded",
    timeout: 30_000,
  });
}

try {
  const task = await taskMetadata();
  const initialStoryboard = await jsonGet(
    `/api/koubo-storyboard/tasks/${targetTaskId}`,
    "initial retained StoryBoard assets",
  );
  const initialUploadedVideos = (
    initialStoryboard.meta?.uploaded_videos || []
  );
  const existingOriginal = initialUploadedVideos.find((item) => (
    item?.origin?.source_asset_id === internalCandidateAssetId
    || item?.provenance?.source_asset_id === internalCandidateAssetId
  ));
  const existingClipImport = initialUploadedVideos.find((item) => (
    reuseClipId
    && (
      item?.origin?.source_clip_id === reuseClipId
      || item?.provenance?.source_clip_id === reuseClipId
    )
  ));
  report.retained.task = {
    task_id: targetTaskId,
    session_id: targetSessionId,
    title: task.title || task.task_title || "",
    workspace_dir: task.workspace_dir || "",
  };

  await openHash("#/koubo-tasks");
  const taskSearch = page.getByPlaceholder(
    "搜索任务名、Task、Session、脚本...",
  );
  await taskSearch.waitFor({ timeout: 30_000 });
  await taskSearch.fill(String(targetTaskId));
  const taskRow = page.locator(".koubo-task-list-table tbody tr")
    .filter({
      hasText: `Task #${targetTaskId} / Session #${targetSessionId}`,
    });
  await taskRow.waitFor({ timeout: 30_000 });
  assert.equal(await taskRow.count(), 1);
  report.checks.task_session_visible = true;
  await screenshot(
    "01-task-session-list.png",
    "专用验收 Task/Session 在任务列表中可见",
  );

  await openHash("#/media-library");
  const materialSearch = page.getByPlaceholder(
    "搜索素材名称、对白、标签、文件名...",
  );
  await materialSearch.waitFor({ timeout: 30_000 });
  await page.locator(".media-library-table tbody tr").first()
    .waitFor({ timeout: 30_000 });
  const rows = page.locator(".media-library-table tbody tr");
  assert.ok(await rows.count() >= 2);
  assert.equal(
    await page.locator(
      `a[href="#/media-library/${analyzedAssetId}"]`,
    ).count(),
    1,
  );
  assert.equal(
    await page.locator(
      `a[href="#/media-library/${tenMinuteAssetId}"]`,
    ).count(),
    1,
  );
  report.checks.real_assets_visible = true;
  await screenshot(
    "02-media-library-real-assets.png",
    "素材库列表展示 199 秒中文真人口播与十分钟技术基线视频",
  );

  const detailResponsePromise = page.waitForResponse(
    (response) => (
      response.request().method() === "GET"
      && new URL(response.url()).pathname
        === `/api/media-library/${analyzedAssetId}`
    ),
    { timeout: 30_000 },
  );
  await openHash(
    `#/media-library/${encodeURIComponent(analyzedAssetId)}`,
  );
  await page.locator(".media-library-detail-shell")
    .waitFor({ timeout: 30_000 });
  const detailResponse = await detailResponsePromise;
  assert.equal(detailResponse.status(), 200);
  await page.getByRole("tab", { name: /对白分析/ })
    .waitFor({ timeout: 30_000 });
  report.checks.detail_dialogue_visible = true;
  await screenshot(
    "03-analysis-dialogue.png",
    "真实视频详情与对白分析片段",
  );

  await page.getByRole("tab", { name: /画面分析/ }).click();
  await page.getByRole("region", { name: "视觉语义运行控制" })
    .waitFor({ timeout: 30_000 });
  report.checks.visual_semantic_visible = true;
  await screenshot(
    "04-analysis-visual-semantic.png",
    "画面结构与视觉语义发布状态",
  );

  await page.getByRole("tab", { name: /综合分析/ }).click();
  const compositeRegion = page.getByRole(
    "region",
    { name: "综合分析运行控制" },
  );
  await compositeRegion.waitFor({ timeout: 30_000 });
  assert.match(await compositeRegion.innerText(), /综合分析/);
  report.checks.composite_visible = true;
  await screenshot(
    "05-analysis-composite.png",
    "对白、视觉语义与综合分析依赖和发布状态",
  );

  const editorResponsePromise = page.waitForResponse(
    (response) => (
      response.request().method() === "GET"
      && new URL(response.url()).pathname
        === `/api/media-library/${analyzedAssetId}/editor`
    ),
    { timeout: 30_000 },
  );
  await openHash(
    `#/media-library/${encodeURIComponent(analyzedAssetId)}/editor`
    + `?start_ms=151217&end_ms=159217`
    + `&target_task_id=${targetTaskId}`
    + "&return_to=media_library_detail",
  );
  await page.locator(".ml-editor-shell").waitFor({
    timeout: 30_000,
  });
  const editorResponse = await editorResponsePromise;
  assert.equal(editorResponse.status(), 200);
  const editorPayload = await responseJson(
    editorResponse,
    "real long Chinese talking-head editor",
  );
  assert.equal(
    Number(editorPayload.item?.duration_ms),
    199_552,
    "the main editor must use the 199-second Chinese talking-head video",
  );
  assert.equal(
    editorPayload.navigation_context?.target_valid,
    true,
    "the retained Task must be a valid StoryBoard import target",
  );
  const expectedFragments = [
    "dialogue",
    "visual",
    "composite",
  ].reduce(
    (count, scheme) => (
      count + (editorPayload.fragments?.[scheme]?.length || 0)
    ),
    0,
  );
  assert.match(
    await page.locator(".ml-editor-header p").innerText(),
    new RegExp(`\\b${expectedFragments}\\b`),
  );
  await page.locator(".ml-editor-header-context select")
    .selectOption(String(targetTaskId));
  await page.getByLabel("入点 ms").fill("151217");
  await page.getByLabel("入点 ms").press("Tab");
  await page.getByLabel("出点 ms").fill("159217");
  await page.getByLabel("出点 ms").press("Tab");
  assert.equal(
    await page.getByLabel("入点 ms").inputValue(),
    "151217",
  );
  assert.equal(
    await page.getByLabel("出点 ms").inputValue(),
    "159217",
  );
  await page.getByLabel("时间轴缩放").fill("90");
  await page.waitForFunction(() => {
    const viewport = document.querySelector(
      ".ml-editor-timeline-viewport",
    );
    const canvas = document.querySelector(
      ".ml-editor-timeline-canvas",
    );
    return Boolean(
      viewport
      && canvas
      && canvas.scrollWidth > viewport.clientWidth,
    );
  });
  const timelineGeometry = await page.locator(
    ".ml-editor-timeline-viewport",
  ).evaluate((viewport) => {
    viewport.scrollLeft = Math.max(
      1,
      viewport.scrollWidth - viewport.clientWidth - 2,
    );
    viewport.dispatchEvent(
      new Event("scroll", { bubbles: true }),
    );
    return {
      scroll_left: viewport.scrollLeft,
      scroll_width: viewport.scrollWidth,
      client_width: viewport.clientWidth,
    };
  });
  assert.ok(timelineGeometry.scroll_left > 0);
  assert.ok(
    timelineGeometry.scroll_width > timelineGeometry.client_width,
  );
  const decodedFrame = await page.locator(
    ".ml-editor-player-shell video",
  ).evaluate(async (video) => {
    const targetSeconds = 154.0;
    const seeked = new Promise((resolveSeek, rejectSeek) => {
      const timeout = window.setTimeout(
        () => rejectSeek(new Error("video seek timed out")),
        15_000,
      );
      video.addEventListener("seeked", () => {
        window.clearTimeout(timeout);
        resolveSeek();
      }, { once: true });
    });
    video.muted = true;
    video.currentTime = targetSeconds;
    await seeked;
    await video.play();
    await new Promise((resolveFrame) => {
      window.setTimeout(resolveFrame, 250);
    });
    return {
      current_time: video.currentTime,
      duration: video.duration,
      ready_state: video.readyState,
      video_width: video.videoWidth,
      video_height: video.videoHeight,
    };
  });
  assert.ok(decodedFrame.current_time >= 153.9);
  assert.ok(decodedFrame.ready_state >= 2);
  assert.equal(decodedFrame.video_width, 1200);
  assert.equal(decodedFrame.video_height, 2670);
  const selectionScrollLeft = await page.locator(
    ".ml-editor-timeline-viewport",
  ).evaluate((viewport, range) => {
    const canvas = viewport.querySelector(".ml-editor-timeline-canvas");
    const canvasWidth = canvas?.scrollWidth || viewport.scrollWidth;
    const midpoint = (range.startMs + range.endMs) / 2;
    const target = (midpoint / range.durationMs) * canvasWidth;
    viewport.scrollLeft = Math.max(
      0,
      Math.min(
        viewport.scrollWidth - viewport.clientWidth,
        target - (viewport.clientWidth / 2),
      ),
    );
    viewport.dispatchEvent(new Event("scroll", { bubbles: true }));
    return viewport.scrollLeft;
  }, { startMs: 151217, endMs: 159217, durationMs: 199552 });
  report.checks.long_chinese_talking_head_editor = {
    duration_ms: Number(editorPayload.item?.duration_ms),
    fragment_count: expectedFragments,
    selection_start_ms: 151217,
    selection_end_ms: 159217,
    timeline_geometry: timelineGeometry,
    selection_scroll_left: selectionScrollLeft,
    decoded_frame: decodedFrame,
    screenshot_surface: "source thumbnail after verified HEVC frame decode",
  };
  const technicalEditorPromise = page.waitForResponse(
    (response) => (
      response.request().method() === "GET"
      && new URL(response.url()).pathname
        === `/api/media-library/${tenMinuteAssetId}/editor`
    ),
    { timeout: 30_000 },
  );
  await openHash(
    `#/media-library/${encodeURIComponent(tenMinuteAssetId)}/editor`
    + "?start_ms=543217&end_ms=548217"
    + `&target_task_id=${targetTaskId}`
    + "&return_to=media_library_detail",
  );
  await page.locator(".ml-editor-shell").waitFor({ timeout: 30_000 });
  const technicalEditorResponse = await technicalEditorPromise;
  assert.equal(technicalEditorResponse.status(), 200);
  const technicalEditor = await responseJson(
    technicalEditorResponse,
    "ten-minute technical baseline editor",
  );
  assert.equal(Number(technicalEditor.item?.duration_ms), 600_000);
  assert.equal(await page.getByLabel("入点 ms").inputValue(), "543217");
  assert.equal(await page.getByLabel("出点 ms").inputValue(), "548217");
  report.checks.ten_minute_technical_baseline = {
    duration_ms: 600_000,
    selection_start_ms: 543_217,
    selection_end_ms: 548_217,
    purpose: "timeline and near-tail non-keyframe technical baseline only",
  };
  await screenshot(
    "06b-editor-ten-minute-tail-technical-baseline.png",
    "十分钟靠近尾部非关键帧切点：仅作为 FFmpeg 与时间轴技术基线",
  );

  await openHash(
    `#/media-library/${encodeURIComponent(analyzedAssetId)}/editor`
    + "?start_ms=151217&end_ms=159217"
    + `&target_task_id=${targetTaskId}`
    + "&return_to=media_library_detail",
  );
  await page.locator(".ml-editor-shell").waitFor({ timeout: 30_000 });
  await page.locator(".ml-editor-header-context select")
    .selectOption(String(targetTaskId));

  await page.getByRole("button", { name: "素材检索" }).click();
  const globalSourceCheckbox = page.getByRole("checkbox", {
    name: "全局素材库",
  });
  const externalSourceCheckbox = page.getByRole("checkbox", {
    name: "外部素材",
  });
  await globalSourceCheckbox.check();
  if (await externalSourceCheckbox.isChecked()) {
    await externalSourceCheckbox.uncheck();
  }
  await page.getByLabel("补充关键词/要求")
    .fill("中医 养肾 垫脚尖 真人口播 健康建议");
  const internalSearchPromise = page.waitForResponse(
    (response) => (
      response.request().method() === "POST"
      && new URL(response.url()).pathname
        === `/api/media-library/${analyzedAssetId}/search/runs`
    ),
    { timeout: 120_000 },
  );
  await page.getByRole("button", {
    name: "开始检索",
  }).click();
  const internalSearchResponse = await internalSearchPromise;
  assert.equal(internalSearchResponse.status(), 200);
  const internalSearch = await responseJson(
    internalSearchResponse,
    "internal semantic search",
  );
  assert.ok(
    (internalSearch.items || []).some((item) => (
      String(item?.asset_id || "") === internalCandidateAssetId
    )),
    `Chinese semantic search must find ${internalCandidateAssetId}`,
  );
  await page.locator(".ml-editor-search-summary").waitFor({
    timeout: 90_000,
  });
  const internalCards = page.locator(
    ".ml-editor-candidate.media_library",
  );
  assert.ok(
    await internalCards.count() > 0,
    "real cross-page search must return an internal candidate",
  );
  report.checks.internal_semantic_search = {
    search_id: internalSearch.search_id,
    result_count: internalSearch.result_count,
    total_count: internalSearch.total_count,
    planner_degraded: internalSearch.planner_degraded,
  };
  await screenshot(
    "07-editor-internal-semantic-search.png",
    "剪辑页跨页面检索命中全局素材库真实候选",
  );
  await page.getByRole("button", { name: "片段", exact: true }).click();
  await screenshot(
    "06-editor-long-chinese-talking-head.png",
    "199 秒中文真人口播、多轨片段与真实场景切换选区",
  );
  await page.getByRole("button", { name: "素材检索", exact: true }).click();
  await page.locator(".ml-editor-search-summary").waitFor({
    timeout: 30_000,
  });

  const importableInternal = internalCards.filter({
    has: page.getByRole("button", { name: "导入原视频" }),
  }).first();
  await importableInternal.waitFor({ timeout: 30_000 });
  if (existingOriginal) {
    report.retained.original_import = {
      ok: true,
      reused_from_previous_browser_attempt: true,
      source_asset_id: internalCandidateAssetId,
      target_task_id: targetTaskId,
      item: existingOriginal,
    };
  } else {
    const originalImportPromise = page.waitForResponse(
      (response) => (
        response.request().method() === "POST"
        && /\/api\/media-library\/[^/]+\/search\/runs\/[^/]+\/import-to-storyboard$/
          .test(new URL(response.url()).pathname)
      ),
      { timeout: 120_000 },
    );
    await importableInternal.getByRole("button", {
      name: "导入原视频",
    }).click();
    const originalImportResponse = await originalImportPromise;
    assert.equal(originalImportResponse.status(), 200);
    const originalImport = await responseJson(
      originalImportResponse,
      "original material import",
    );
    await page.getByText(/已导入目标 StoryBoard/).waitFor({
      timeout: 60_000,
    });
    report.retained.original_import = originalImport;
  }
  await screenshot(
    "08-editor-original-imported.png",
    "全局素材库原视频已导入专用 StoryBoard Task",
  );

  await externalSourceCheckbox.check();
  await page.getByLabel("补充关键词/要求").fill(
    "two person interview conversation office landscape",
  );
  const externalSearchPromise = page.waitForResponse(
    (response) => (
      response.request().method() === "POST"
      && new URL(response.url()).pathname
        === `/api/media-library/${analyzedAssetId}/search/runs`
    ),
    { timeout: 180_000 },
  );
  await page.getByRole("button", {
    name: "开始检索",
  }).click();
  const externalSearchResponse = await externalSearchPromise;
  assert.equal(externalSearchResponse.status(), 200);
  const externalSearch = await responseJson(
    externalSearchResponse,
    "external semantic search",
  );
  const externalCards = page.locator(
    ".ml-editor-candidate.external",
  );
  await externalCards.first().waitFor({ timeout: 120_000 });
  assert.ok(await externalCards.count() > 0);
  const externalReasonCopy = (
    await externalCards.locator("small").allInnerTexts()
  ).join(" ");
  assert.doesNotMatch(
    externalReasonCopy,
    /(?:text embedding|provider relevance|\b(?:portrait|landscape|square) ratio\b|license metadata confirmed|provider-specific fallback query)/i,
    "external score reasons must not expose internal English ranking terms",
  );
  const externalReasonLabels = new Map([
    ["text embedding rerank", "文本关键词相关"],
    ["embedding rerank", "文本关键词相关"],
    ["provider relevance rank", "来源站点相关性排序"],
    ["provider relevance", "来源站点相关性排序"],
    ["provider rank", "来源站点排序"],
    ["portrait ratio", "竖屏比例匹配"],
    ["landscape ratio", "横屏比例匹配"],
    ["square ratio", "方形比例匹配"],
    ["unknown ratio", "画幅比例信息"],
    ["license metadata confirmed", "授权信息已确认"],
    ["provider-specific fallback query", "已使用来源站点兼容关键词"],
  ]);
  for (const rawReason of new Set((externalSearch.items || []).flatMap(
    (candidate) => candidate?.score_reasons || [],
  ))) {
    const normalizedReason = String(rawReason).trim().toLowerCase();
    const expectedLabel = externalReasonLabels.get(normalizedReason)
      || (/[a-z]/i.test(normalizedReason) ? "来源提供的相关性依据" : normalizedReason);
    assert.ok(
      externalReasonCopy.includes(expectedLabel),
      `external reason ${rawReason} must render as ${expectedLabel}`,
    );
  }
  report.checks.external_semantic_search = {
    search_id: externalSearch.search_id,
    result_count: externalSearch.result_count,
    total_count: externalSearch.total_count,
    source_errors: externalSearch.source_errors || {},
  };
  await screenshot(
    "09-editor-external-semantic-search.png",
    "剪辑页展示真实外部 Provider、Creator 与 License 元数据",
  );

  let supportedExternal = null;
  const externalCount = await externalCards.count();
  for (let index = 0; index < externalCount; index += 1) {
    const card = externalCards.nth(index);
    const importButton = card.getByRole("button", {
      name: "整条导入",
    });
    const title = await importButton.getAttribute("title") || "";
    if (title === "请先显式确认 license") {
      supportedExternal = card;
      break;
    }
  }
  assert.ok(
    supportedExternal,
    "real external results must include a downloadable candidate",
  );
  const externalTitle = await supportedExternal.locator("h4")
    .innerText();
  await supportedExternal.getByLabel(/我已阅读并确认/).check();
  const externalImportButton = supportedExternal.getByRole(
    "button",
    { name: "整条导入" },
  );
  await externalImportButton.waitFor({ state: "visible" });
  assert.equal(await externalImportButton.isEnabled(), true);
  const externalImportPromise = page.waitForResponse(
    (response) => (
      response.request().method() === "POST"
      && new URL(response.url()).pathname
        === `/api/koubo-storyboard/tasks/${targetTaskId}/asset-library-search/import`
    ),
    { timeout: 240_000 },
  );
  await externalImportButton.click();
  const externalImportResponse = await externalImportPromise;
  assert.equal(externalImportResponse.status(), 200);
  const externalImport = await responseJson(
    externalImportResponse,
    "external material import",
  );
  await page.getByText(
    new RegExp(`“${escapeRegExp(externalTitle)}”已导入目标 StoryBoard`),
  ).waitFor({ timeout: 240_000 });
  report.retained.external_import = externalImport;
  await screenshot(
    "10-editor-external-imported.png",
    "显式确认 License 后外部真实视频已导入专用 Task",
  );

  let clipId = reuseClipId;
  let clipJobId = reuseClipJobId;
  let completedJob = null;
  if (reuseClipId) {
    const reusableClips = await jsonGet(
      `/api/media-library/${analyzedAssetId}/clips`,
      "reusable retained clip",
    );
    const reusableClip = (
      reusableClips.items || reusableClips.clips || []
    ).find((item) => String(item.clip_id) === reuseClipId);
    assert.ok(
      reusableClip,
      `requested reusable clip ${reuseClipId} was not found`,
    );
    clipName = String(reusableClip.display_name || clipName);
    completedJob = {
      status: "completed",
      progress: 100,
      clip_id: reuseClipId,
      clip: reusableClip,
      resumed_after_browser_assertion: true,
    };
  } else {
    await page.getByLabel("入点 ms").fill("151217");
    await page.getByLabel("入点 ms").press("Tab");
    await page.getByLabel("出点 ms").fill("159217");
    await page.getByLabel("出点 ms").press("Tab");
    await page.getByLabel("片段名称").fill(clipName);
    const createClipPromise = page.waitForResponse(
      (response) => (
        response.request().method() === "POST"
        && new URL(response.url()).pathname
          === `/api/media-library/${analyzedAssetId}/clip-jobs`
      ),
      { timeout: 60_000 },
    );
    await page.getByRole("button", {
      name: "创建剪切任务",
    }).click();
    const createClipResponse = await createClipPromise;
    assert.equal(createClipResponse.status(), 202);
    const createdJob = await responseJson(
      createClipResponse,
      "create retained clip",
    );
    clipJobId = String(createdJob.clip_job_id || "");
    assert.match(clipJobId, /^(?:mlcj_|clipjob\.)/);
    completedJob = await poll(
      async () => jsonGet(
        `/api/media-library/${analyzedAssetId}`
        + `/clip-jobs/${clipJobId}`,
        "retained clip job",
      ),
      (value) => value?.status === "completed",
      {
        timeoutMs: 180_000,
        intervalMs: 500,
        label: "real retained FFmpeg clip completion",
      },
    );
    clipId = String(completedJob.clip_id || "");
    assert.match(clipId, /^mlc_/);
  }
  await page.getByRole("button", { name: "派生片段" }).click();
  if (!reuseClipId) {
    await page.locator(".ml-editor-job.completed").waitFor({
      timeout: 180_000,
    });
  }
  const clipCard = page.locator(".ml-editor-clip").filter({
    hasText: clipName,
  });
  await clipCard.waitFor({ timeout: 30_000 });
  report.retained.clip = {
    clip_id: clipId,
    clip_job_id: clipJobId,
    display_name: clipName,
    start_ms: 151217,
    end_ms: 159217,
    duration_ms: 8000,
    job: completedJob,
  };
  await screenshot(
    "11-editor-retained-clip-completed.png",
    "真实 FFmpeg 中文口播场景切换片段剪切完成且可预览下载",
  );

  if (existingClipImport) {
    report.retained.clip_import = {
      ok: true,
      reused_from_previous_browser_attempt: true,
      source_clip_id: clipId,
      target_task_id: targetTaskId,
      item: existingClipImport,
    };
  } else {
    const clipImportPromise = page.waitForResponse(
      (response) => (
        response.request().method() === "POST"
        && new URL(response.url()).pathname
          === `/api/media-library/${analyzedAssetId}`
            + `/clips/${clipId}/import-to-storyboard`
      ),
      { timeout: 120_000 },
    );
    await clipCard.getByRole("button", {
      name: "导入 StoryBoard",
    }).click();
    const clipImportResponse = await clipImportPromise;
    assert.equal(clipImportResponse.status(), 200);
    const clipImport = await responseJson(
      clipImportResponse,
      "retained clip import",
    );
    await page.getByText(/派生片段已导入目标 StoryBoard/).waitFor({
      timeout: 60_000,
    });
    report.retained.clip_import = clipImport;
  }
  await screenshot(
    "12-editor-retained-clip-imported.png",
    "派生片段已导入专用 StoryBoard Task 且仍留在剪辑页",
  );

  await openHash(`#/koubo-storyboard/tasks/${targetTaskId}`);
  await page.locator(".kbsp-right").waitFor({ timeout: 60_000 });
  await page.getByRole("button", {
    name: "上传素材",
    exact: true,
  }).click();
  const disabledStoryboardSearch = page.getByRole("button", {
    name: "检索素材",
    exact: true,
  });
  await disabledStoryboardSearch.waitFor();
  await page.getByText("尚未选择镜头。", { exact: true }).waitFor();
  assert.equal(
    await disabledStoryboardSearch.isDisabled(),
    true,
    "Task without a selected Dialogue must disable media-library search",
  );
  await page.getByText("请先选择一个对白片段。", { exact: true }).waitFor();
  const disabledSearchBox = await disabledStoryboardSearch.boundingBox();
  assert.ok(disabledSearchBox, "disabled StoryBoard search button must remain visibly rendered");
  await page.mouse.click(
    disabledSearchBox.x + disabledSearchBox.width / 2,
    disabledSearchBox.y + disabledSearchBox.height / 2,
  );
  assert.equal(await page.getByRole("dialog", { name: "检索全局素材库" }).count(), 0);
  await disabledStoryboardSearch.click({ force: true });
  assert.equal(
    await page.getByRole("dialog", { name: "检索全局素材库" }).count(),
    0,
    "even a forced user click must not open search without a Dialogue context",
  );
  const assetCards = page.locator(
    ".kbsp-asset-upload-section .kbsp-asset-scene-card",
  );
  await assetCards.first().waitFor({ timeout: 60_000 });
  const uploadedTitles = await assetCards.evaluateAll((cards) => (
    cards.map((card) => card.getAttribute("title") || "")
  ));
  assert.ok(
    uploadedTitles.some((title) => title.includes(clipName)),
    "retained clip must be visible in StoryBoard Asset Pool",
  );
  assert.ok(
    uploadedTitles.length >= 3,
    "original, external, and derived video imports must remain visible",
  );
  report.checks.storyboard_asset_pool = {
    card_count: uploadedTitles.length,
    titles: uploadedTitles,
    retained_clip_visible: true,
    search_disabled_without_dialogue: true,
    search_disabled_reason: "请先选择一个对白片段。",
    empty_shot_message: "尚未选择镜头。",
  };
  await screenshot(
    "13-storyboard-retained-assets.png",
    "StoryBoard Asset Pool 展示原视频、外部视频与派生剪切结果",
  );

  await openHash("#/koubo-tasks");
  await taskSearch.waitFor({ timeout: 30_000 });
  await taskSearch.fill(String(targetTaskId));
  const finalTaskRow = page.locator(
    ".koubo-task-list-table tbody tr",
  ).filter({
    hasText: `Task #${targetTaskId} / Session #${targetSessionId}`,
  });
  await finalTaskRow.waitFor({ timeout: 30_000 });
  const videoCount = Number(
    await finalTaskRow.locator(".asset-video").innerText(),
  );
  assert.ok(
    videoCount >= 3,
    "Task list must expose the retained imported video count",
  );
  report.checks.task_video_count = videoCount;
  await screenshot(
    "14-task-session-with-retained-assets.png",
    "任务列表最终显示专用 Task/Session 与留存视频素材计数",
  );

  await page.setViewportSize({ width: 390, height: 844 });
  await openHash("#/media-library");
  const mobileAssetLink = page.locator(
    `a[href="#/media-library/${analyzedAssetId}"]`,
  );
  await mobileAssetLink.waitFor({ timeout: 30_000 });
  const mobileLayout = {
    list: {
      shell: await mobileShellGeometry("mobile media-library list"),
      asset_link: await assertViewportUsable(
      mobileAssetLink,
      "mobile media-library asset name",
      { minWidth: 56 },
      ),
    },
  };
  assert.match(await mobileAssetLink.innerText(), /video\(26\)/i);
  await screenshot(
    "15-mobile-media-library.png",
    "移动端素材库列表与真实中文口播素材",
  );
  await openHash(`#/media-library/${encodeURIComponent(analyzedAssetId)}`);
  await page.locator(".media-library-detail-shell")
    .waitFor({ timeout: 30_000 });
  mobileLayout.detail = {
    shell: await mobileShellGeometry("mobile media-library detail"),
    title: await assertViewportUsable(
      page.locator(".media-library-workbench-title h2"),
      "mobile detail title",
      { minWidth: 70 },
    ),
    first_fragment: await assertViewportUsable(
      page.locator(".media-library-fragment-row").first(),
      "mobile detail first fragment",
      { minWidth: 220 },
    ),
  };
  await screenshot(
    "16-mobile-analysis-detail.png",
    "移动端素材详情与分析结果",
  );
  await openHash(
    `#/media-library/${encodeURIComponent(analyzedAssetId)}/editor`
    + "?start_ms=151217&end_ms=159217"
    + `&target_task_id=${targetTaskId}`,
  );
  await page.locator(".ml-editor-shell").waitFor({ timeout: 30_000 });
  mobileLayout.editor = {
    shell: await mobileShellGeometry("mobile media-library editor"),
    target: await assertViewportUsable(
      page.locator(".ml-editor-header-context select"),
      "mobile editor target selector",
      { minWidth: 220 },
    ),
    player: await assertViewportUsable(
      page.locator(".ml-editor-player-shell"),
      "mobile editor player",
      { minWidth: 260 },
    ),
  };
  await screenshot(
    "17-mobile-editor.png",
    "移动端中文口播剪辑器与精确选区",
  );
  const mobileSearchTab = page.getByRole("button", {
    name: "素材检索",
    exact: true,
  });
  await mobileSearchTab.scrollIntoViewIfNeeded();
  await mobileSearchTab.click();
  mobileLayout.editor.search_panel = await assertViewportUsable(
    page.locator('.ml-editor-side-section[aria-label="跨页面对白与关键词检索"]'),
    "mobile editor semantic search panel",
    { minWidth: 260 },
  );
  await screenshot(
    "17a-mobile-editor-semantic-search.png",
    "移动端剪辑器语义检索面板可访问",
  );
  mobileLayout.editor.timeline = await assertViewportUsable(
    page.locator(".ml-editor-timeline-panel"),
    "mobile editor timeline",
    { minWidth: 260 },
  );
  await screenshot(
    "17b-mobile-editor-timeline.png",
    "移动端剪辑器时间轴可访问",
  );
  report.checks.mobile_layout = mobileLayout;

  const finalClips = await jsonGet(
    `/api/media-library/${analyzedAssetId}/clips`,
    "retained clip list",
  );
  const finalClipItems = finalClips.items || finalClips.clips || [];
  assert.ok(
    finalClipItems.some((item) => (
      String(item.clip_id) === clipId
      && String(item.display_name) === clipName
    )),
    "the retained clip must survive a fresh API read",
  );
  const finalTask = await taskMetadata();
  assert.equal(Number(finalTask.session_id), targetSessionId);
  report.checks.fresh_read_persistence = true;
  report.finished_at = new Date().toISOString();
  report.ok = true;

  assert.equal(
    report.page_errors.length,
    0,
    `browser page errors: ${JSON.stringify(report.page_errors)}`,
  );
  assert.equal(
    report.api_failures.length,
    0,
    `HTTP 5xx responses: ${JSON.stringify(report.api_failures)}`,
  );
} catch (error) {
  report.ok = false;
  report.finished_at = new Date().toISOString();
  report.failure = {
    name: error?.name || "Error",
    message: error?.message || String(error),
    stack: error?.stack || "",
    url: page.url(),
  };
  try {
    await screenshot(
      "99-failure.png",
      "失败现场（用于定位，已保留此前成功创建的数据）",
    );
  } catch {
    // Preserve the primary failure below.
  }
  throw error;
} finally {
  writeFileSync(
    resolve(artifactDir, "visible-acceptance-report.json"),
    `${JSON.stringify(report, null, 2)}\n`,
  );
  await browser.close();
  console.log(JSON.stringify({
    ok: report.ok,
    artifact_dir: artifactDir,
    task_id: targetTaskId,
    session_id: targetSessionId,
    clip_id: report.retained.clip?.clip_id || "",
    screenshot_count: report.screenshots.length,
    failure: report.failure?.message || "",
  }, null, 2));
}

function escapeRegExp(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
