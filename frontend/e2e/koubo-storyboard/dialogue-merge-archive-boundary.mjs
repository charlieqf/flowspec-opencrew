#!/usr/bin/env node
import assert from "node:assert/strict";
import {
  SAVE_BUTTON_SELECTOR,
  assertTextareaValue,
  calls,
  findDialogue,
  generatedSecondDialogueFinalVideoPath,
  generatedSecondDialogueRawVideoPath,
  openStoryboard,
  runIfMain,
  seedMergeDialogueArchiveState,
  uploadNewImage,
  waitForEnabled,
} from "./fixture.mjs";

const MERGED_TEXT = "真实 UI 回归：Raw 已存在但没有音频和终视频。 第二条对白用于确认 Scene 保持多 Dialogue。";

async function run(page) {
  seedMergeDialogueArchiveState();
  await openStoryboard(page);

  await page.locator('.kbsp-media-final-video[data-kbsp-dialogue-id="dlg_002"].has-media').waitFor({ state: "visible", timeout: 5000 });
  await page.locator('#kbsp-dialogue-dlg_002 [data-kbsp-action="merge-dialogue-up"]').click({ force: true });
  await page.locator("#kbsp-dialogue-dlg_002").waitFor({ state: "detached", timeout: 5000 });
  await assertTextareaValue(page, "dlg_001", MERGED_TEXT);

  await waitForEnabled(page, SAVE_BUTTON_SELECTOR);
  await page.locator(SAVE_BUTTON_SELECTOR).click();
  assert.equal(calls.save.length, 1, "Dialogue merge should submit exactly once");

  const saved = calls.save[0].plan;
  const retained = findDialogue(saved, "dlg_001");
  assert.ok(retained, "retained dialogue should remain in save payload");
  assert.equal(findDialogue(saved, "dlg_002"), null, "merged dialogue should be removed from save payload");
  assert.equal(retained.dialogue_asset_key, "dak_001");
  assert.notEqual(retained.dialogue_asset_key, retained.srt_id);
  assert.equal(retained.bound_image_path, uploadNewImage.path);
  assert.equal(retained.duration, 5.6);

  const savedText = JSON.stringify(saved);
  assert.ok(!savedText.includes(generatedSecondDialogueFinalVideoPath), "removed generated Final path must not remain referenced");
  assert.ok(!savedText.includes(generatedSecondDialogueRawVideoPath), "removed generated Raw path must not remain referenced");
  assert.ok(!savedText.includes("Working/dak_002_"), "removed dialogue must not leave dak_002 Working references");
}

export default { name: "dialogue-merge-archive-boundary", run };

runIfMain(import.meta.url, [{ name: "dialogue-merge-archive-boundary", run }]);
