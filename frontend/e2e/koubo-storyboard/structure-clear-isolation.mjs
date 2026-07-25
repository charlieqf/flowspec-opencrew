#!/usr/bin/env node
import assert from "node:assert/strict";
import {
  SAVE_BUTTON_SELECTOR,
  assertDialogueKeysAndBindingsStable,
  assertPlanDialogueKeysStable,
  calls,
  clickAndWaitForClear,
  currentPlan,
  findDialogue,
  openStoryboard,
  runIfMain,
  seedFullBindingState,
  waitForEnabled,
} from "./fixture.mjs";

async function run(page) {
  seedFullBindingState();
  await openStoryboard(page);

  await page.locator('#kbsp-dialogue-dlg_001 + .kbsp-dialogue-break [data-kbsp-action="split-scene"]').click({ force: true });
  await page.waitForFunction(() => document.querySelectorAll(".kbsp-scene-wrap").length === 2, { timeout: 5000 });
  await page.locator('[data-kbsp-action="merge-scene-up"]').click({ force: true });
  await page.waitForFunction(() => document.querySelectorAll(".kbsp-scene-wrap").length === 1, { timeout: 5000 });
  await waitForEnabled(page, SAVE_BUTTON_SELECTOR);
  await page.locator(SAVE_BUTTON_SELECTOR).click();
  assert.equal(calls.save.length, 1, "Structure mutation should submit exactly once");
  assertPlanDialogueKeysStable(calls.save[0].plan);

  await clickAndWaitForClear(page, '.kbsp-media-new[data-kbsp-dialogue-id="dlg_001"] button[title="Remove Image"]', 1);
  assert.equal(calls.clear.at(-1).target_kind, "image");
  assert.equal(findDialogue(currentPlan, "dlg_001")?.working_assets?.images?.[0]?.path || "", "");
  assertDialogueKeysAndBindingsStable();
  await page.locator('.kbsp-media-new[data-kbsp-dialogue-id="dlg_001"].has-media').waitFor({ state: "detached", timeout: 5000 });
  await page.locator('.kbsp-media-raw-video[data-kbsp-dialogue-id="dlg_001"].has-media').waitFor({ state: "visible", timeout: 5000 });
  await page.locator('.kbsp-media-final-video[data-kbsp-dialogue-id="dlg_002"].has-media').waitFor({ state: "visible", timeout: 5000 });
}

export default { name: "structure-clear-isolation", run };

runIfMain(import.meta.url, [{ name: "structure-clear-isolation", run }]);
