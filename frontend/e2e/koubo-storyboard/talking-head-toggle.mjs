#!/usr/bin/env node
import assert from "node:assert/strict";
import {
  calls,
  currentPlan,
  findDialogue,
  openStoryboard,
  runIfMain,
  seedFullBindingState,
  waitForCall,
} from "./fixture.mjs";

async function setOriginalImageMode(page, label, expectedSaveCount) {
  await page.locator('.kbsp-media-original[data-kbsp-dialogue-id="dlg_001"]').click({ button: "right" });
  const item = page.locator(".kbsp-talking-head-menu button", { hasText: label }).first();
  await item.waitFor({ state: "visible", timeout: 5000 });
  await item.evaluate((node) => node.click());
  await waitForCall("save", expectedSaveCount);
}

async function run(page) {
  seedFullBindingState();
  await openStoryboard(page);

  await setOriginalImageMode(page, "设置空镜", 1);
  let videoPlan = findDialogue(currentPlan, "dlg_001")?.video_plan || {};
  assert.equal(videoPlan.is_talking_head, false);
  assert.equal(videoPlan.lipsync_override, "skip_cutaway");
  await page.locator('.kbsp-media-original[data-kbsp-dialogue-id="dlg_001"].is-cutaway').waitFor({ state: "visible", timeout: 5000 });

  await setOriginalImageMode(page, "标记口播", 2);
  videoPlan = findDialogue(currentPlan, "dlg_001")?.video_plan || {};
  assert.equal(videoPlan.is_talking_head, true);
  assert.equal(videoPlan.lipsync_override, "");
  assert.equal(calls.save.length, 2);
  await page.waitForFunction(
    () => !document.querySelector('.kbsp-media-original[data-kbsp-dialogue-id="dlg_001"].is-cutaway'),
    null,
    { timeout: 5000 },
  );
  assert.equal(await page.locator('.kbsp-media-original[data-kbsp-dialogue-id="dlg_001"].is-cutaway').count(), 0);
}

export default { name: "talking-head-toggle", run };

runIfMain(import.meta.url, [{ name: "talking-head-toggle", run }]);
