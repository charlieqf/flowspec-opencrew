#!/usr/bin/env node
import assert from "node:assert/strict";
import {
  assertPlanDialogueKeysStable,
  currentPlan,
  openStoryboard,
  runIfMain,
} from "./fixture.mjs";

async function run(page) {
  await openStoryboard(page);
  assertPlanDialogueKeysStable(currentPlan);
  assert.equal(await page.locator(".kbsp-media-raw-video.has-media").count(), 1, "Raw video slot should reflect existing raw file");
  assert.equal(await page.locator(".kbsp-media-final-video.has-media").count(), 0, "Final video slot should stay empty");
  assert.equal(await page.locator('.kbsp-media-raw-video[data-kbsp-dialogue-id="dlg_002"].has-media').count(), 0, "Poisoned dialogue fallback must not render Raw for dlg_002");
}

export default { name: "slot-identity-rendering", run };

runIfMain(import.meta.url, [{ name: "slot-identity-rendering", run }]);
