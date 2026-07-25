#!/usr/bin/env node
import assert from "node:assert/strict";
import {
  SAVE_BUTTON_SELECTOR,
  assertDialogueKeysAndBindingsStable,
  assertTextareaValue,
  assertVisible,
  calls,
  dragAssetToSlot,
  editDialogueText,
  findDialogue,
  openStoryboard,
  runIfMain,
  uploadImage,
  uploadNewImage,
  uploadVideo,
  waitForEnabled,
} from "./fixture.mjs";

const UPDATED_TEXT = "真实 UI 回归：修改对白后保存，key 和绑定必须稳定。";

async function run(page) {
  await openStoryboard(page);
  await page.getByRole("button", { name: "上传素材" }).click();

  await dragAssetToSlot(page, "E2E source image", '.kbsp-media-original[data-kbsp-dialogue-id="dlg_001"]', 1);
  assert.equal(calls.bind.at(-1).target_kind, "source");
  assert.equal(calls.bind.at(-1).asset_path, uploadImage.path);

  await dragAssetToSlot(page, "E2E new image", '.kbsp-media-new[data-kbsp-dialogue-id="dlg_001"]', 2);
  assert.equal(calls.bind.at(-1).target_kind, "image");
  assert.equal(calls.bind.at(-1).asset_path, uploadNewImage.path);
  await page.locator('.kbsp-media-new[data-kbsp-dialogue-id="dlg_001"].has-media').waitFor({ state: "visible", timeout: 5000 });

  await dragAssetToSlot(page, "E2E raw video", '.kbsp-media-raw-video[data-kbsp-dialogue-id="dlg_002"]', 3);
  assert.equal(calls.bind.at(-1).target_kind, "raw_video");
  assert.equal(calls.bind.at(-1).asset_path, uploadVideo.path);

  await dragAssetToSlot(page, "E2E raw video", '.kbsp-media-final-video[data-kbsp-dialogue-id="dlg_002"]', 4);
  assert.equal(calls.bind.at(-1).target_kind, "final_video");
  assert.equal(calls.bind.at(-1).asset_path, uploadVideo.path);
  await page.locator('.kbsp-media-final-video[data-kbsp-dialogue-id="dlg_002"].has-media').waitFor({ state: "visible", timeout: 5000 });
  assertDialogueKeysAndBindingsStable();

  await editDialogueText(page, "dlg_001", UPDATED_TEXT);
  await waitForEnabled(page, SAVE_BUTTON_SELECTOR);
  await page.locator(SAVE_BUTTON_SELECTOR).click();
  assert.equal(calls.save.length, 1, "Save should submit exactly once");
  assert.equal(findDialogue(calls.save[0].plan, "dlg_001").dialogue_asset_key, "dak_001");

  await page.reload({ waitUntil: "domcontentloaded", timeout: 30000 });
  await assertVisible(page, ".kbsp-editor");
  await assertTextareaValue(page, "dlg_001", UPDATED_TEXT);
  await page.locator('.kbsp-media-original[data-kbsp-dialogue-id="dlg_001"].has-media').waitFor({ state: "visible", timeout: 5000 });
  await page.locator('.kbsp-media-new[data-kbsp-dialogue-id="dlg_001"].has-media').waitFor({ state: "visible", timeout: 5000 });
  await page.locator('.kbsp-media-raw-video[data-kbsp-dialogue-id="dlg_002"].has-media').waitFor({ state: "visible", timeout: 5000 });
  await page.locator('.kbsp-media-final-video[data-kbsp-dialogue-id="dlg_002"].has-media').waitFor({ state: "visible", timeout: 5000 });
}

export default { name: "binding-save-reload", run };

runIfMain(import.meta.url, [{ name: "binding-save-reload", run }]);
