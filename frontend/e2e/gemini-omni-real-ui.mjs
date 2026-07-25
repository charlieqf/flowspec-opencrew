#!/usr/bin/env node
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
const taskId = String(process.env.OPENCREW_E2E_KOUBO_TASK_ID || "305");
const screenshotDir = String(process.env.OPENCREW_E2E_SCREENSHOT_DIR || "").trim();
if (screenshotDir) await mkdir(screenshotDir, { recursive: true });

const browser = await chromium.launch({ headless: process.env.HEADED !== "1" });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
await loginAdmin(context, url);

const configResponse = await context.request.get(
  `${url}/api/koubo-storyboard/tasks/${taskId}/asset-library/video-model-config`,
);
assert.equal(configResponse.status(), 200);
const config = await responseJson(configResponse, "public video model config");
const omniAlias = (config.agent_model_aliases || []).find(
  (item) => item?.capability?.stateful_edit === true,
);
assert.ok(omniAlias, "the deployed local configuration must expose a stateful video alias");
assert.equal("provider" in omniAlias, false, "the public alias must not expose its provider");
assert.equal("model" in omniAlias, false, "the public alias must not expose its model id");
assert.deepEqual(omniAlias.capability.duration, {
  adjustable: false,
  min: 3,
  max: 3,
  allowed: [3],
});

const settingsUrl = `${url}/api/koubo-storyboard/tasks/${taskId}/asset-library/videos-agent/settings`;
const originalSettingsResponse = await context.request.get(settingsUrl);
assert.equal(originalSettingsResponse.status(), 200);
const originalSettings = await responseJson(originalSettingsResponse, "Videos-Agent settings");

const page = await context.newPage();
const apiFailures = [];
const pageErrors = [];
page.on("pageerror", (error) => pageErrors.push(error.message));
page.on("response", (response) => {
  const pathname = new URL(response.url()).pathname;
  if (pathname.startsWith("/api/") && response.status() >= 500) {
    apiFailures.push(`${response.request().method()} ${pathname} ${response.status()}`);
  }
});

try {
  await page.goto(
    `${url}/?e2eGeminiOmniReal=${Date.now()}#/koubo-asset-library/tasks/${taskId}`,
    { waitUntil: "domcontentloaded", timeout: 30_000 },
  );
  await page.getByText("Asset Library", { exact: true }).waitFor({ timeout: 30_000 });
  const videoAgentNav = page.locator(".ual-nav button").filter({ hasText: /^视频智能体$/ }).first();
  await videoAgentNav.click();
  const panel = page.locator(".ual-video-agent");
  await panel.waitFor({ state: "visible", timeout: 15_000 });

  await panel.getByRole("button", { name: "设置", exact: true }).last().click();
  const settingsPanel = panel.getByRole("region", { name: "视频生成设置" });
  await settingsPanel.waitFor({ state: "visible", timeout: 15_000 });
  const modelButton = settingsPanel.getByTitle(String(omniAlias.alias), { exact: true });
  await modelButton.waitFor({ state: "visible", timeout: 15_000 });
  if (!(await modelButton.getAttribute("class") || "").includes("is-active")) {
    await modelButton.click();
    await assertDurationIsThreeSeconds(settingsPanel);
    if (screenshotDir) {
      await page.screenshot({
        path: path.join(screenshotDir, "17-omni-real-model-settings.png"),
        fullPage: true,
      });
    }
    const saved = page.waitForResponse((response) => (
      response.request().method() === "PUT"
      && new URL(response.url()).pathname === `/api/koubo-storyboard/tasks/${taskId}/asset-library/videos-agent/settings`
      && response.status() === 200
    ), { timeout: 10_000 });
    await settingsPanel.getByRole("button", { name: "完成", exact: true }).click();
    await saved;
  } else {
    await assertDurationIsThreeSeconds(settingsPanel);
    if (screenshotDir) {
      await page.screenshot({
        path: path.join(screenshotDir, "17-omni-real-model-settings.png"),
        fullPage: true,
      });
    }
    await settingsPanel.getByRole("button", { name: "返回", exact: true }).click();
  }

  const stateful = panel.locator('.ual-video-stateful[aria-label="有状态视频版本"]');
  await stateful.waitFor({ state: "visible", timeout: 15_000 });
  const panelText = await panel.innerText();
  assert.ok(panelText.includes("每次生成或继续编辑都会产生一次新的付费调用"));
  assert.ok(panelText.includes("云端会保存编辑上下文"));
  assert.ok(panelText.includes("不可移除的来源水印"));
  assert.ok(panelText.includes("新建视频"));
  assert.ok(panelText.includes("上传视频编辑"));
  assert.deepEqual(apiFailures, []);
  assert.deepEqual(pageErrors, []);

  if (screenshotDir) {
    await page.screenshot({
      path: path.join(screenshotDir, "16-omni-real-local-config.png"),
      fullPage: true,
    });
  }
  console.log(`Gemini Omni real local UI E2E: ok (${omniAlias.alias})`);
} finally {
  const restore = await context.request.put(settingsUrl, {
    data: { settings: originalSettings.settings || originalSettings },
  }).catch(() => null);
  assert.equal(restore?.status(), 200, "the browser test must restore the original Videos-Agent settings");
  await context.close();
  await browser.close();
}

async function assertDurationIsThreeSeconds(settingsPanel) {
  const durationInput = settingsPanel.getByRole("spinbutton", { name: "自定义时长（秒）" });
  await durationInput.waitFor({ state: "visible", timeout: 10_000 });
  assert.equal(await durationInput.inputValue(), "3");
  assert.equal(await durationInput.getAttribute("min"), "3");
  assert.equal(await durationInput.getAttribute("max"), "3");
  await settingsPanel.getByRole("button", { name: "3s", exact: true }).waitFor({ state: "visible" });
}
