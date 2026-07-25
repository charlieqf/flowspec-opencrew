#!/usr/bin/env node
import assert from "node:assert/strict";
import {
  calls,
  findDialogue,
  currentPlan,
  openStoryboard,
  runIfMain,
  seedFinalUnboundState,
  waitForCall,
} from "./fixture.mjs";

const FINAL_PATH = "SessionOutput/storyboard/Working/dak_001_Video_Final.mp4";

async function run(page) {
  seedFinalUnboundState();
  await openStoryboard(page);
  await page.locator('.kbsp-media-final-video[data-kbsp-dialogue-id="dlg_001"].has-media').waitFor({ state: "visible", timeout: 5000 });

  await page.locator('.kbsp-media-final-video[data-kbsp-dialogue-id="dlg_001"] button[title="Confirm Final Binding"]').evaluate((button) => button.click());
  await waitForCall("bind", 1);

  assert.equal(calls.bind[0].dialogue_id, "dlg_001");
  assert.equal(calls.bind[0].target_kind, "final_video");
  assert.equal(calls.bind[0].asset_path, FINAL_PATH);
  assert.equal(findDialogue(currentPlan, "dlg_001")?.working_assets?.video?.path, FINAL_PATH);
  await page.waitForFunction(() => !document.querySelector('.kbsp-media-final-video[data-kbsp-dialogue-id="dlg_001"] button[title="Confirm Final Binding"]'), { timeout: 5000 });
}

export default { name: "final-unbound-confirm", run };

runIfMain(import.meta.url, [{ name: "final-unbound-confirm", run }]);
