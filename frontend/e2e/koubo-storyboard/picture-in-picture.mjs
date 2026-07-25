#!/usr/bin/env node
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import {
  openStoryboard,
  runIfMain,
  seedFullBindingState,
} from "./fixture.mjs";

const TINY_VIDEO_PATH = resolve(
  new URL("../../..", import.meta.url).pathname,
  "ToolLibrary/DanceMimic_V1/test_fixtures/dance_solo_frontal_studio.mp4",
);

async function run(page) {
  seedFullBindingState();
  const tinyVideo = readFileSync(TINY_VIDEO_PATH);
  await page.route(/\/api\/session-tasks\/.*\.mp4(?:\?.*)?$/, async (route) => {
    await new Promise((resolveDelay) => setTimeout(resolveDelay, 300));
    await route.fulfill({
      status: 200,
      contentType: "video/mp4",
      headers: { "Accept-Ranges": "bytes" },
      body: tinyVideo,
    });
  });
  await openStoryboard(page);

  const slot = page.locator(
    '.kbsp-media-final-video[data-kbsp-dialogue-id="dlg_002"]',
  );
  await slot.locator(".kbsp-video-thumb-placeholder").click();
  const video = slot.locator("video");
  const pipButton = slot.getByRole("button", {
    name: "画中画预览终视频",
  });
  await pipButton.waitFor({ state: "visible" });
  const visibility = await pipButton.evaluate((button) => {
    const style = getComputedStyle(button);
    return {
      opacity: Number(style.opacity),
      pointerEvents: style.pointerEvents,
    };
  });
  assert.ok(visibility.opacity > 0, "StoryBoard PIP button must remain visible without slot hover");
  assert.equal(visibility.pointerEvents, "auto");

  await video.evaluate((element) => {
    window.__storyboardPipEvents = [];
    element.addEventListener("enterpictureinpicture", () => {
      window.__storyboardPipEvents.push("enter");
    });
  });
  await pipButton.click();
  await page.waitForFunction(() => window.__storyboardPipEvents?.includes("enter"), null, {
    timeout: 5000,
  });
  assert.equal(
    await video.evaluate((element) => document.pictureInPictureElement === element),
    true,
    "StoryBoard PIP must target the activated final video after delayed metadata loading",
  );
  await page.evaluate(() => document.exitPictureInPicture());
}

export default { name: "picture-in-picture", run };

runIfMain(import.meta.url, [{ name: "picture-in-picture", run }]);
