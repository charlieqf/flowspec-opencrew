#!/usr/bin/env node
import assert from "node:assert/strict";
import {
  calls,
  openStoryboard,
  runIfMain,
  uploadedPoolImage,
  waitForCall,
} from "./fixture.mjs";

const PNG_1X1 = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=", "base64");

async function run(page) {
  await openStoryboard(page);
  await page.getByRole("button", { name: "上传素材" }).click();
  await page.locator(".kbsp-asset-upload-actions input[type='file']").first().setInputFiles({
    name: uploadedPoolImage.filename,
    mimeType: "image/png",
    buffer: PNG_1X1,
  });
  await waitForCall("upload", 1);
  assert.match(calls.upload[0].content_type, /multipart\/form-data/);
  await page.locator(`.kbsp-asset-scene-card[title="${uploadedPoolImage.label}"]`).waitFor({ state: "visible", timeout: 5000 });
  await page.waitForFunction(() => document.querySelector(".kbsp-asset-tabs button.is-active")?.textContent?.trim() === "上传素材", { timeout: 5000 });
}

export default { name: "upload-asset-pool", run };

runIfMain(import.meta.url, [{ name: "upload-asset-pool", run }]);
