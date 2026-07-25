#!/usr/bin/env node
// P1 UI e2e: verifies rewrite/adapt mode enablement, apply-to-generation
// (Applied audit + view switch + composer prefill), and the version Diff view.
// Chat/SSE are mocked; knowledge/search, versions and applied hit the real backend.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { chromium } = require("../node_modules/playwright");

const BASE_URL = (process.env.OPENCREW_E2E_FRONTEND_URL || "http://127.0.0.1:18080").replace(/\/$/, "");
let TASK_ID = process.env.OPENCREW_E2E_KOUBO_TASK_ID || "";
const APP_PASSWORD = process.env.OPENCREW_E2E_APP_PASSWORD || "";
const RUN_ID = new Date().toISOString().replace(/[-:TZ.]/g, "").slice(0, 14);
const OUT_DIR = path.resolve("frontend/test-results/prompt-agent-p1", RUN_ID);
const NAV_LABEL = "提示词智能体";

fs.mkdirSync(OUT_DIR, { recursive: true });

function exactText(value) {
  return new RegExp(`^${String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`);
}
function jsonResponse(payload) {
  return { status: 200, contentType: "application/json", body: JSON.stringify(payload) };
}
function sseBody(events) {
  return events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join("");
}

async function ensureAuthenticated(page) {
  await page.waitForLoadState("domcontentloaded", { timeout: 30000 }).catch(() => {});
  const gate = page.locator(".auth-gate");
  if (!(await gate.isVisible().catch(() => false))) return;
  assert.ok(APP_PASSWORD, "OPENCREW_E2E_APP_PASSWORD is required when auth gate is visible");
  await page.locator(".auth-gate input[type='password']").fill(APP_PASSWORD);
  await page.locator(".auth-gate button").filter({ hasText: /Sign in|Create password/ }).click();
  await gate.waitFor({ state: "hidden", timeout: 15000 });
}
async function forceAssetLibraryRoute(page) {
  await page.evaluate((taskId) => {
    window.location.hash = "#/analysis-v1/tasks";
    window.dispatchEvent(new HashChangeEvent("hashchange"));
    window.setTimeout(() => {
      window.location.hash = `#/koubo-asset-library/tasks/${taskId}`;
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    }, 50);
  }, TASK_ID);
}
async function clickNav(page, label) {
  const button = page.locator(".ual-nav button, .ual-sidebar-bottom button").filter({ hasText: exactText(label) }).first();
  await button.waitFor({ state: "visible", timeout: 15000 });
  await button.click();
}
async function fetchJsonInPage(page, url, options = {}) {
  return await page.evaluate(async ({ url, options }) => {
    const response = await fetch(url, { credentials: "include", ...options, headers: { "Content-Type": "application/json", ...(options.headers || {}) } });
    const text = await response.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch { data = text; }
    if (!response.ok) throw new Error(`${url} failed: ${response.status} ${typeof data === "string" ? data : JSON.stringify(data)}`);
    return data;
  }, { url, options });
}
async function discoverTaskId(page) {
  const payload = await fetchJsonInPage(page, "/api/koubo-storyboard/tasks");
  const firstTaskId = payload?.items?.[0]?.task?.id;
  assert.ok(firstTaskId, "No Koubo StoryBoard task is available for E2E");
  return String(firstTaskId);
}

const REVISED = "Use TARGET_FRAME and PRODUCT_REFERENCE in order. Keep camera stable and motion subtle.";
const NEGATIVE_ONLY = "scene reset, identity drift, subtitles, watermark";
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
const page = await context.newPage();

const pageErrors = [];
const consoleErrors = [];
page.on("pageerror", (err) => pageErrors.push(err.message));
page.on("console", (msg) => { if (msg.type() === "error") consoleErrors.push(msg.text()); });

let eventConnectionCount = 0;
let capturedChatPayload = null;
let capturedRetrievalId = "";
const assistantMessageId = `e2e_prompt_agent_p1_${RUN_ID}`;
const mockModels = { items: [{ providerID: "e2e", providerName: "E2E", modelID: "mock-prompt-agent", modelName: "Mock Prompt Agent" }], default_model: { providerID: "e2e", modelID: "mock-prompt-agent" } };

await page.route(`**/api/koubo-storyboard/tasks/*/agents/prompt_agent/chat/ensure-session`, (route) =>
  route.fulfill(jsonResponse({ ok: true, agent_key: "prompt_agent", chat_opencode_session_id: `e2e_session_${RUN_ID}`, prompt_models: mockModels })));
await page.route(`**/api/koubo-storyboard/tasks/*/agents/prompt_agent/chat/messages`, (route) =>
  route.fulfill(jsonResponse({ ok: true, agent_key: "prompt_agent", chat_opencode_session_id: `e2e_session_${RUN_ID}`, items: [], prompt_models: mockModels })));
await page.route(`**/api/koubo-storyboard/tasks/*/agents/prompt_agent/chat/message`, (route) => {
  capturedChatPayload = JSON.parse(route.request().postData() || "{}");
  capturedRetrievalId = String(capturedChatPayload?.client_context?.knowledge?.retrieval_id || "");
  return route.fulfill(jsonResponse({ ok: true, agent_key: "prompt_agent", chat_opencode_session_id: `e2e_session_${RUN_ID}`, model: { providerID: "e2e", modelID: "mock-prompt-agent" }, prompt_models: mockModels }));
});
await page.route(`**/api/koubo-storyboard/tasks/*/agents/prompt_agent/chat/events`, (route) => {
  eventConnectionCount += 1;
  const now = Date.now();
  const result = {
    mode: "optimize",
    summary: "E2E normalized prompt result.",
    issues: [{ severity: "medium", span: "reference images video prompt", problem: "Reference roles underspecified.", why_it_matters: "Model may confuse reference order.", suggestion: "Name reference roles." }],
    revised_prompt: REVISED,
    negative_prompt: "jitter, flicker, warped hands",
    changes: ["Added reference roles.", "Added video stability constraints."],
    model_notes: ["Use checked retrieval sources only."],
    used_sources: [{ doc_id: "seed_reference_roles_001", title: "Reference asset roles", trust_level: "local_experience", reason: "Validates reference role labeling." }],
    source_validation: { kept: [{ doc_id: "seed_reference_roles_001" }], dropped: [] },
    retrieval_id: capturedRetrievalId,
  };
  const events = [{ type: "ready", agent_key: "prompt_agent", chat_opencode_session_id: `e2e_session_${RUN_ID}` }];
  if (eventConnectionCount >= 2 && capturedRetrievalId) {
    events.push({ type: "message.updated", properties: { message: { info: { id: assistantMessageId, role: "assistant", time: { created: now, completed: now + 1 } }, parts: [{ id: `${assistantMessageId}_part`, messageID: assistantMessageId, type: "text", text: `Done.\n<PROMPT_AGENT_RESULT>${JSON.stringify(result)}</PROMPT_AGENT_RESULT>` }] } } });
    events.push({ type: "prompt_agent.result.normalized", properties: { message_id: assistantMessageId, path: `SessionContext/PromptAgent/Critiques/critique_${assistantMessageId}.json`, result } });
  }
  return route.fulfill({ status: 200, contentType: "text/event-stream", body: sseBody(events) });
});

const checks = {};
try {
  await page.goto(`${BASE_URL}/?promptAgentP1E2E=${RUN_ID}`, { waitUntil: "domcontentloaded", timeout: 30000 });
  await ensureAuthenticated(page);
  if (!TASK_ID) TASK_ID = await discoverTaskId(page);
  await page.goto(`${BASE_URL}/?promptAgentP1E2E=${RUN_ID}#/koubo-asset-library/tasks/${TASK_ID}`, { waitUntil: "domcontentloaded", timeout: 30000 });
  await forceAssetLibraryRoute(page);
  await page.getByText("Asset Library", { exact: true }).first().waitFor({ state: "visible", timeout: 30000 });
  await clickNav(page, NAV_LABEL);
  await page.locator(".prompt-agent-panel h2").filter({ hasText: NAV_LABEL }).waitFor({ state: "visible", timeout: 15000 });
  await page.locator(".prompt-agent-workspace h2").filter({ hasText: NAV_LABEL }).waitFor({ state: "visible", timeout: 15000 });

  // --- P1 check 1: rewrite/adapt modes enabled, compare disabled ---
  const modeBtn = (label) => page.locator(".prompt-agent-mode-tabs button").filter({ hasText: exactText(label) }).first();
  checks.rewrite_enabled = !(await modeBtn("改写").isDisabled());
  checks.adapt_enabled = !(await modeBtn("模型适配").isDisabled());
  checks.compare_disabled = await modeBtn("对比").isDisabled();
  assert.ok(checks.rewrite_enabled, "改写 mode tab should be enabled");
  assert.ok(checks.adapt_enabled, "模型适配 mode tab should be enabled");
  assert.ok(checks.compare_disabled, "对比 mode tab should stay disabled");
  await modeBtn("模型适配").click();
  checks.adapt_selectable = (await modeBtn("模型适配").getAttribute("class") || "").includes("is-active");
  assert.ok(checks.adapt_selectable, "clicking 模型适配 should activate it");
  await modeBtn("优化").click(); // back to optimize so the mocked result mode matches

  await page.locator(".prompt-agent-model-row label").filter({ hasText: "Provider" }).locator("select").selectOption("e2e");
  await page.locator(".prompt-agent-model-row label").filter({ hasText: "Model" }).locator("select").selectOption("e2e::mock-prompt-agent");
  await page.screenshot({ path: path.join(OUT_DIR, "01-loaded.png"), fullPage: true });

  // --- submit prompt -> result ---
  const knowledgePromise = page.waitForResponse((r) => r.url().includes(`/prompt-agent/knowledge/search`) && r.request().method() === "POST", { timeout: 15000 });
  await page.locator(".prompt-agent-composer textarea").fill("reference images video prompt: optimize a product shot with camera stability");
  await page.locator(".prompt-agent-composer button[type='submit']").click();
  const knowledgeResponse = await knowledgePromise;
  assert.equal(knowledgeResponse.status(), 200, "knowledge search should return 200");
  const knowledgePayload = await knowledgeResponse.json();
  checks.retrieval_id = String(knowledgePayload.retrieval_id || "");
  assert.match(checks.retrieval_id, /^retrieval_\d+_[a-z0-9]{4,}$/);

  // Scope to the result card: the revised prompt also appears as saved-version
  // titles in the workspace list, which would make a global text match ambiguous.
  await page.locator(".prompt-agent-result-card pre").filter({ hasText: REVISED.slice(0, 30) }).first().waitFor({ state: "visible", timeout: 15000 });

  // --- P1 check 2: apply-to-generation buttons present ---
  const applyImageBtn = page.locator(".prompt-agent-result-card footer button").filter({ hasText: /应用到图像生成/ }).first();
  const applyVideoBtn = page.locator(".prompt-agent-result-card footer button").filter({ hasText: /应用到视频生成/ }).first();
  await applyImageBtn.waitFor({ state: "visible", timeout: 10000 });
  checks.apply_image_button = await applyImageBtn.isVisible();
  checks.apply_video_button = await applyVideoBtn.isVisible();
  assert.ok(checks.apply_image_button && checks.apply_video_button, "both apply buttons should render");
  await page.screenshot({ path: path.join(OUT_DIR, "02-result-with-apply.png"), fullPage: true });

  // --- save version ---
  const savePromise = page.waitForResponse((r) => r.url().includes(`/prompt-agent/versions`) && r.request().method() === "POST", { timeout: 15000 });
  await page.locator(".prompt-agent-result-card footer button").filter({ hasText: /保存版本|Saving/ }).click();
  const saveResponse = await savePromise;
  assert.equal(saveResponse.status(), 200, "version save should return 200");
  const savePayload = await saveResponse.json();
  checks.saved_version_id = savePayload.version_id || "";
  assert.ok(checks.saved_version_id, "version_id should be returned");

  // --- P1 check 3: version Diff view in the workspace ---
  const versionMenu = page.locator(".prompt-agent-version-actions button").first();
  await versionMenu.waitFor({ state: "visible", timeout: 15000 });
  await versionMenu.click();
  await page.locator(".ual-floating-card-menu [role='menuitem']").filter({ hasText: "查看Diff" }).click();
  await page.locator(".prompt-agent-version-detail .prompt-agent-diff-pane.is-revised pre").filter({ hasText: REVISED.slice(0, 30) }).waitFor({ state: "visible", timeout: 10000 });
  const detail = await page.evaluate(() => {
    const el = document.querySelector(".prompt-agent-version-detail");
    return el ? el.innerText : "";
  });
  checks.diff_has_original = detail.includes("原始");
  checks.diff_has_revised = detail.includes("优化后") && detail.includes(REVISED.slice(0, 20));
  checks.diff_is_compare_only = !detail.includes("Negative") && !detail.includes("改动") && !detail.includes("来源依据");
  assert.ok(checks.diff_has_original, "diff should show 原始");
  assert.ok(checks.diff_has_revised, "diff should show 优化后 + revised prompt");
  assert.ok(checks.diff_is_compare_only, "diff should only show the side-by-side comparison");
  await page.screenshot({ path: path.join(OUT_DIR, "03-version-diff.png"), fullPage: true });

  // --- P1 check 3b: negative-only versions use negative_prompt as the optimized side ---
  const negativeOnlyPayload = await fetchJsonInPage(page, `/api/koubo-storyboard/tasks/${TASK_ID}/prompt-agent/versions`, {
    method: "POST",
    body: JSON.stringify({
      mode: "critique",
      original_prompt: `请帮我批注如下提示词： ${NEGATIVE_ONLY}, generated text, jump cut`,
      negative_prompt: NEGATIVE_ONLY,
    }),
  });
  checks.negative_only_version_id = negativeOnlyPayload.version_id || "";
  assert.ok(checks.negative_only_version_id, "negative-only version_id should be returned");
  await page.locator(".prompt-agent-workspace-header button").click();
  await page.locator(".prompt-agent-version-actions button").first().waitFor({ state: "visible", timeout: 15000 });
  await page.locator(".prompt-agent-version-actions button").first().click();
  await page.locator(".ual-floating-card-menu [role='menuitem']").filter({ hasText: "查看Diff" }).click();
  const fallbackRevisedPane = page.locator(".prompt-agent-version-detail .prompt-agent-diff-pane.is-revised pre").first();
  await fallbackRevisedPane.filter({ hasText: NEGATIVE_ONLY }).waitFor({ state: "visible", timeout: 10000 });
  checks.negative_only_diff_has_optimized_side = (await fallbackRevisedPane.innerText()).includes(NEGATIVE_ONLY);
  assert.ok(checks.negative_only_diff_has_optimized_side, "negative-only diff should show negative_prompt on optimized side");

  // --- P1 check 4: apply to image generation (audit + view switch + prefill) ---
  const appliedPromise = page.waitForResponse((r) => r.url().includes(`/prompt-agent/applied`) && r.request().method() === "POST", { timeout: 15000 });
  await applyImageBtn.click();
  const appliedResponse = await appliedPromise;
  checks.applied_status = appliedResponse.status();
  assert.equal(checks.applied_status, 200, "applied audit should return 200");
  const appliedPayload = await appliedResponse.json();
  checks.applied_target = appliedPayload.target;
  assert.equal(appliedPayload.target, "images", "applied record target should be images");
  // view switched away from prompt agent -> image composer mounts
  await page.locator(".ual-composer-box textarea").first().waitFor({ state: "visible", timeout: 15000 });
  checks.prompt_panel_gone = (await page.locator(".prompt-agent-panel").count()) === 0;
  const composerValue = await page.locator(".ual-composer-box textarea").first().inputValue().catch(() => "");
  checks.composer_prefilled = composerValue.includes(REVISED.slice(0, 30));
  assert.ok(checks.prompt_panel_gone, "view should switch away from the prompt agent panel");
  await page.screenshot({ path: path.join(OUT_DIR, "04-applied-image-composer.png"), fullPage: true });

  const report = { ok: true, base_url: BASE_URL, task_id: TASK_ID, output_dir: OUT_DIR, checks, page_errors: pageErrors, console_errors: consoleErrors.slice(0, 20) };
  fs.writeFileSync(path.join(OUT_DIR, "report.json"), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
} catch (err) {
  await page.screenshot({ path: path.join(OUT_DIR, "failure.png"), fullPage: true }).catch(() => {});
  const failureState = await page.evaluate(() => ({ url: window.location.href, body: document.body.innerText.slice(0, 3000), authGate: Boolean(document.querySelector(".auth-gate")) })).catch((e) => ({ evaluate_error: String(e) }));
  const report = { ok: false, base_url: BASE_URL, task_id: TASK_ID, output_dir: OUT_DIR, error: err?.message || String(err), checks, state: failureState, page_errors: pageErrors, console_errors: consoleErrors.slice(0, 20) };
  fs.writeFileSync(path.join(OUT_DIR, "failure-report.json"), JSON.stringify(report, null, 2));
  console.error(JSON.stringify(report, null, 2));
  throw err;
} finally {
  await browser.close();
}
