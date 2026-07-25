import assert from "node:assert/strict";
import {
  existsSync,
  mkdirSync,
  writeFileSync,
} from "node:fs";
import { resolve } from "node:path";
import { chromium } from "playwright";
import {
  assertMediaLibraryCapabilities,
  baseUrl,
  loginAdmin,
  poll,
  repoRoot,
  responseJson,
} from "./media-library-real-helpers.mjs";

const url = baseUrl();
const sourcePath = resolve(
  process.env.MEDIA_LIBRARY_LONG_CHINESE_SOURCE_PATH
    || "/Users/macmini-4/.opencrew/sessions/334/workspace/inbox/video(26).mp4",
);
const reuseAssetId = String(
  process.env.MEDIA_LIBRARY_LONG_CHINESE_REUSE_ASSET_ID || "",
).trim();
assert.ok(existsSync(sourcePath), `real Chinese talking-head video is missing: ${sourcePath}`);

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
  process.env.MEDIA_LIBRARY_LONG_CHINESE_ARTIFACT_DIR
    || resolve(
      repoRoot,
      "frontend/e2e/artifacts/media-library-long-chinese-talking-head",
      timestamp,
    ),
);
mkdirSync(artifactDir, { recursive: true });

const report = {
  schema_version: "media_library_long_chinese_talking_head_v1",
  started_at: new Date().toISOString(),
  base_url: url,
  source_path: sourcePath,
  source_evidence: {
    source_session_id: 334,
    language: "zh-CN",
    duration_ms: 199_552,
    dimensions: "1200x2670",
    audio: "AAC 44100Hz stereo",
    known_shot_count: 12,
    known_scene_count: 24,
    transcript_cues: 96,
  },
  screenshots: [],
  api_failures: [],
  console_errors: [],
  page_errors: [],
  runs: {},
};

const browser = await chromium.launch({
  headless: process.env.HEADED !== "1",
});
const context = await browser.newContext({
  viewport: { width: 1600, height: 1100 },
  deviceScaleFactor: 1,
});
await loginAdmin(context, url);
report.capabilities = await assertMediaLibraryCapabilities(context, url);
const page = await context.newPage();

page.on("console", (message) => {
  if (message.type() === "error") {
    report.console_errors.push({ text: message.text(), url: page.url() });
  }
});
page.on("pageerror", (error) => {
  report.page_errors.push({ message: error.message, url: page.url() });
});
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
      status: response.status(),
      pathname,
    });
  }
});

async function settle() {
  await page.evaluate(async () => {
    if (document.fonts?.ready) await document.fonts.ready;
  });
  await page.waitForTimeout(500);
}

async function screenshot(name, label) {
  await settle();
  const path = resolve(artifactDir, name);
  const metrics = await page.evaluate(() => ({
    hash: window.location.hash,
    body_width: document.body.scrollWidth,
    viewport_width: document.documentElement.clientWidth,
    body_height: document.body.scrollHeight,
    viewport_height: document.documentElement.clientHeight,
  }));
  await page.screenshot({ path, fullPage: true, animations: "disabled" });
  report.screenshots.push({ name, label, path, url: page.url(), metrics });
}

async function openHash(hash) {
  await page.goto(`${url}/${hash}`, {
    waitUntil: "domcontentloaded",
    timeout: 30_000,
  });
}

async function detail(assetId) {
  const response = await context.request.get(
    `${url}/api/media-library/${encodeURIComponent(assetId)}`,
    { timeout: 30_000 },
  );
  assert.equal(response.status(), 200);
  return responseJson(response, "long Chinese talking-head detail");
}

function openCut(payload) {
  return payload?.item?.open_cut || payload?.open_cut || {};
}

async function waitForStatus(assetId, field, accepted, label, timeoutMs) {
  return poll(
    () => detail(assetId),
    (payload) => accepted.includes(String(openCut(payload)?.[field] || "")),
    { timeoutMs, intervalMs: 1_500, label },
  );
}

async function closeToolDrawer() {
  const drawer = page.locator(".media-library-tool-drawer");
  if (await drawer.count()) {
    await drawer.getByRole("button", { name: "关闭" }).click();
    await drawer.waitFor({ state: "detached" });
  }
}

async function runHeaderTool(label, consentLabel, responsePath) {
  await page.locator(".media-library-workbench-actions")
    .getByRole("button", { name: new RegExp(label) })
    .click();
  const drawer = page.locator(".media-library-tool-drawer");
  await drawer.waitFor({ timeout: 30_000 });
  if (consentLabel) await drawer.getByLabel(consentLabel).check();
  await screenshot(
    label === "对白分析"
      ? "04-dialogue-cloud-consent.png"
      : "06-visual-structure-ready-for-run.png",
    label === "对白分析"
      ? "对白分析工具集：真实中文音轨与本次云端 ASR 授权"
      : "画面分析工具集：Scene Detect 与中点 Keyframe 步骤",
  );
  const responsePromise = page.waitForResponse(
    (response) => (
      response.request().method() === "POST"
      && new URL(response.url()).pathname === responsePath
    ),
    { timeout: 60_000 },
  );
  await drawer.locator(".media-library-tool-run-icon").click();
  const response = await responsePromise;
  assert.equal(response.status(), 200, `${label} run failed: HTTP ${response.status()}`);
  const payload = await responseJson(response, `${label} run`);
  await screenshot(
    label === "对白分析"
      ? "05-dialogue-run-submitted.png"
      : "07-visual-structure-run-submitted.png",
    `${label}真实运行已提交，UI 显示运行状态`,
  );
  await closeToolDrawer();
  return payload;
}

try {
  let assetId = reuseAssetId;
  let uploadedItem = null;
  await openHash("#/media-library");
  await page.getByRole("button", { name: "上传素材" })
    .waitFor({ timeout: 30_000 });
  await screenshot(
    "01-media-library-before-upload.png",
    "素材库上传入口与已有真实素材",
  );

  if (!assetId) {
    await page.getByRole("button", { name: "上传素材" }).click();
    const dialog = page.getByRole("dialog", { name: "素材上传" });
    await dialog.waitFor({ timeout: 30_000 });
    await dialog.locator("input[type='file']").setInputFiles(sourcePath);
    await dialog.getByText("video(26).mp4", { exact: true })
      .waitFor();
    await screenshot(
      "02-real-chinese-video-selected.png",
      "已选择系统现有的 199 秒中文真人多场景口播视频",
    );
    let completedPayload = null;
    const captureComplete = async (response) => {
      try {
        const pathname = new URL(response.url()).pathname;
        if (
          response.request().method() === "POST"
          && /\/api\/media-library\/uploads\/[^/]+\/complete$/.test(pathname)
          && response.status() === 200
        ) {
          const payload = await response.json();
          if (payload?.item?.asset_id) completedPayload = payload;
        }
      } catch {
        // The final assertion below reports a missing completion payload.
      }
    };
    page.on("response", captureComplete);
    await dialog.getByRole("button", { name: "上传", exact: true }).click();
    await dialog.waitFor({ state: "detached", timeout: 300_000 });
    page.off("response", captureComplete);
    assert.ok(completedPayload?.item?.asset_id, "upload completed without a materialized asset");
    uploadedItem = completedPayload.item;
    assetId = String(uploadedItem.asset_id);
  } else {
    await page.getByRole("button", { name: "上传素材" }).click();
    const dialog = page.getByRole("dialog", { name: "素材上传" });
    await dialog.waitFor({ timeout: 30_000 });
    await dialog.locator("input[type='file']").setInputFiles(sourcePath);
    await dialog.getByText("video(26).mp4", { exact: true }).waitFor();
    await screenshot(
      "02-real-chinese-video-selected.png",
      "已选择系统现有的 199 秒中文真人多场景口播视频",
    );
    page.once("dialog", (confirmation) => confirmation.accept());
    await dialog.getByRole("button", { name: "取消", exact: true }).click();
    await dialog.waitFor({ state: "detached", timeout: 30_000 });
  }
  assert.match(assetId, /^mla_/);
  report.asset_id = assetId;
  report.uploaded_item = uploadedItem;

  await openHash("#/media-library");
  await page.locator(`a[href="#/media-library/${assetId}"]`)
    .waitFor({ timeout: 60_000 });
  await screenshot(
    "03-real-chinese-video-in-library.png",
    "199 秒中文真人口播已真实上传并留存在素材库",
  );

  await openHash(`#/media-library/${encodeURIComponent(assetId)}`);
  await page.locator(".media-library-detail-shell")
    .waitFor({ timeout: 30_000 });
  let current = await detail(assetId);
  if (String(openCut(current).dialogue_status) !== "ready") {
    report.runs.dialogue_submitted = await runHeaderTool(
      "对白分析",
      /允许本次运行使用云端 ASR/,
      `/api/media-library/${assetId}/analyses/dialogue/run`,
    );
    current = await waitForStatus(
      assetId,
      "dialogue_status",
      ["ready", "blocked", "failed"],
      "real Chinese dialogue analysis terminal state",
      900_000,
    );
    assert.equal(
      openCut(current).dialogue_status,
      "ready",
      `Chinese dialogue analysis must be ready: ${JSON.stringify(openCut(current))}`,
    );
  }
  report.runs.dialogue = {
    status: openCut(current).dialogue_status,
    run_id: openCut(current).dialogue_current_run_id,
    fragment_count: openCut(current).counts?.dialogue,
  };
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.locator(".media-library-detail-shell").waitFor();
  await page.getByRole("tab", { name: /对白分析/ }).click();
  await page.locator(".media-library-fragment-row").first()
    .waitFor({ timeout: 30_000 });
  await screenshot(
    "08-dialogue-analysis-ready.png",
    "中文对白分析完成：真实转录被切分为可播放片段",
  );

  current = await detail(assetId);
  if (String(openCut(current).visual_structure_status) !== "ready") {
    report.runs.visual_structure_submitted = await runHeaderTool(
      "画面分析",
      null,
      `/api/media-library/${assetId}/analyses/visual/run`,
    );
    current = await waitForStatus(
      assetId,
      "visual_structure_status",
      ["ready", "blocked", "failed"],
      "real Chinese visual structure terminal state",
      900_000,
    );
    assert.equal(
      openCut(current).visual_structure_status,
      "ready",
      `visual structure analysis must be ready: ${JSON.stringify(openCut(current))}`,
    );
  }
  report.runs.visual_structure = {
    status: openCut(current).visual_structure_status,
    run_id: openCut(current).visual_structure_current_run_id,
    fragment_count: openCut(current).counts?.visual,
  };

  await page.reload({ waitUntil: "domcontentloaded" });
  await page.locator(".media-library-detail-shell").waitFor();
  await page.getByRole("tab", { name: /画面分析/ }).click();
  const semanticRegion = page.getByRole("region", {
    name: "视觉语义运行控制",
  });
  await semanticRegion.waitFor({ timeout: 30_000 });
  await screenshot(
    "09-visual-structure-ready.png",
    "画面结构完成：场景边界与中点代表画面已发布",
  );
  current = await detail(assetId);
  if (String(openCut(current).visual_semantic_status) !== "ready") {
    const consent = semanticRegion.getByLabel(
      /允许本次运行向已配置的云端视觉模型发送 Keyframe 图像/,
    );
    await consent.check();
    await screenshot(
      "10-visual-semantic-consent.png",
      "视觉语义显式授权：只发送已发布 Keyframe，不发送源视频",
    );
    const responsePromise = page.waitForResponse(
      (response) => (
        response.request().method() === "POST"
        && new URL(response.url()).pathname
          === `/api/media-library/${assetId}/analyses/visual/run`
      ),
      { timeout: 60_000 },
    );
    await semanticRegion.getByRole("button", {
      name: /运行视觉语义|重新运行视觉语义/,
    }).click();
    const response = await responsePromise;
    assert.equal(response.status(), 200);
    report.runs.visual_semantic_submitted = await responseJson(
      response,
      "real Chinese visual semantic run",
    );
    current = await waitForStatus(
      assetId,
      "visual_semantic_status",
      ["ready", "blocked", "failed"],
      "real Chinese visual semantic terminal state",
      900_000,
    );
    assert.equal(
      openCut(current).visual_semantic_status,
      "ready",
      `visual semantic analysis must be ready: ${JSON.stringify(openCut(current))}`,
    );
  }
  report.runs.visual_semantic = {
    status: openCut(current).visual_semantic_status,
    run_id: openCut(current).visual_semantic_current_run_id,
    fragment_count: openCut(current).counts?.visual,
  };
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.locator(".media-library-detail-shell").waitFor();
  await page.getByRole("tab", { name: /画面分析/ }).click();
  await page.getByRole("region", { name: "视觉语义运行控制" })
    .waitFor();
  await screenshot(
    "11-visual-semantic-ready.png",
    "多场景视觉语义分析完成并显示模型与内容依据",
  );

  current = await detail(assetId);
  await page.getByRole("tab", { name: /综合分析/ }).click();
  const compositeRegion = page.getByRole("region", {
    name: "综合分析运行控制",
  });
  await compositeRegion.waitFor();
  if (String(openCut(current).composite_status) !== "ready") {
    await screenshot(
      "12-composite-ready-for-run.png",
      "综合分析依赖门禁：对白、结构和视觉语义均已完成",
    );
    const responsePromise = page.waitForResponse(
      (response) => (
        response.request().method() === "POST"
        && new URL(response.url()).pathname
          === `/api/media-library/${assetId}/analyses/composite/run`
      ),
      { timeout: 60_000 },
    );
    await compositeRegion.getByRole("button", {
      name: /运行综合分析|重新运行综合分析/,
    }).click();
    const response = await responsePromise;
    assert.equal(response.status(), 200);
    report.runs.composite_submitted = await responseJson(
      response,
      "real Chinese composite run",
    );
    current = await waitForStatus(
      assetId,
      "composite_status",
      ["ready", "blocked", "failed"],
      "real Chinese composite terminal state",
      900_000,
    );
    assert.equal(
      openCut(current).composite_status,
      "ready",
      `composite analysis must be ready: ${JSON.stringify(openCut(current))}`,
    );
  }
  report.runs.composite = {
    status: openCut(current).composite_status,
    run_id: openCut(current).composite_current_run_id,
    fragment_count: openCut(current).counts?.composite,
  };
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.locator(".media-library-detail-shell").waitFor();
  await page.getByRole("tab", { name: /综合分析/ }).click();
  await page.getByRole("region", { name: "综合分析运行控制" })
    .waitFor();
  await screenshot(
    "13-composite-analysis-ready.png",
    "综合分析完成：中文对白与多场景画面被融合为检索/剪辑片段",
  );

  const finalDetail = await detail(assetId);
  const item = finalDetail.item || {};
  report.retained = {
    asset_id: assetId,
    task_id: item.open_cut?.task_id || null,
    session_id: item.session_id || item.open_cut?.session_id || null,
    display_name: item.display_name || "",
    duration_ms: item.duration_ms,
    dialogue_fragments: item.open_cut?.counts?.dialogue || 0,
    visual_fragments: item.open_cut?.counts?.visual || 0,
    composite_fragments: item.open_cut?.counts?.composite || 0,
  };
  assert.ok(
    Math.abs(Number(item.duration_ms) - 199_552) <= 2,
    `unexpected uploaded duration: ${item.duration_ms}`,
  );
  assert.ok(Number(report.retained.dialogue_fragments) > 0);
  assert.ok(Number(report.retained.visual_fragments) > 1);
  assert.ok(Number(report.retained.composite_fragments) > 0);
  assert.equal(report.page_errors.length, 0);
  assert.equal(report.api_failures.length, 0);
  report.ok = true;
  report.finished_at = new Date().toISOString();
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
    await screenshot("99-failure.png", "失败现场");
  } catch {
    // Preserve the primary error.
  }
  throw error;
} finally {
  writeFileSync(
    resolve(artifactDir, "long-chinese-talking-head-report.json"),
    `${JSON.stringify(report, null, 2)}\n`,
  );
  await browser.close();
  console.log(JSON.stringify({
    ok: report.ok,
    artifact_dir: artifactDir,
    asset_id: report.asset_id || "",
    task_id: report.retained?.task_id || null,
    session_id: report.retained?.session_id || null,
    screenshot_count: report.screenshots.length,
    failure: report.failure?.message || "",
  }, null, 2));
}
