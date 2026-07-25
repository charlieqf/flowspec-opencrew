#!/usr/bin/env node
import assert from "node:assert/strict";
import {
  assertClassContains,
  assertUsesDialogueAssetKey,
  assertVisible,
  calls,
  openStoryboard,
  runIfMain,
  seedFullBindingState,
} from "./fixture.mjs";

async function run(page) {
  seedFullBindingState();
  await openStoryboard(page);

  await page.locator(".kbsp-video-plan-button").click();
  await page.getByRole("dialog", { name: "生成计划" }).waitFor({ state: "visible", timeout: 15000 });
  assert.equal(calls.videoPlan[0].target.target_type, "scene");
  await page.getByLabel("生成计划指标").waitFor({ state: "visible" });
  await page.getByLabel("执行生成计划").waitFor({ state: "visible" });
  const videoPlanBadges = page.locator(".kbsp-vpm-pipeline .kbsp-vpm-task-badge");
  assert.equal(await videoPlanBadges.count(), 4);
  await assertClassContains(videoPlanBadges.nth(1), "is-done", "Video Plan image badge");
  await assertClassContains(videoPlanBadges.nth(2), "is-done", "Video Plan raw-video badge");
  await assertClassContains(videoPlanBadges.nth(3), "is-disabled", "Video Plan final-video badge");
  assertUsesDialogueAssetKey(calls.videoPlanResult[0], "dak_001", ["srt_001", "dlg_001", "scene_001", "shot_001"]);
  await page.getByLabel("关闭生成计划").click();

  await page.locator(".kbsp-image-plan-button").click();
  await page.getByRole("dialog", { name: "图像计划" }).waitFor({ state: "visible", timeout: 15000 });
  assert.equal(calls.imagePlan[0].target.target_type, "scene");
  await page.getByLabel("图像计划指标").waitFor({ state: "visible" });
  const imagePlanBadges = page.locator(".kbsp-ipm-shell .kbsp-vpm-task-badge");
  assert.equal(await imagePlanBadges.count(), 2);
  await assertClassContains(imagePlanBadges.nth(1), "is-done", "Image Plan new-image badge");
  await page.getByLabel("关闭图像计划").click();

  await page.locator(".kbsp-video-only-plan-button").click();
  await page.getByRole("dialog", { name: "视频计划" }).waitFor({ state: "visible", timeout: 15000 });
  assert.equal(calls.videoOnlyPlan[0].target.target_type, "scene");
  const videoOnlyBadges = page.locator(".kbsp-vop-shell .kbsp-vpm-task-badge");
  await assertClassContains(videoOnlyBadges.nth(3), "is-done", "Video Only Plan raw-video badge");
  await assertClassContains(videoOnlyBadges.nth(4), "is-pending", "Video Only Plan copy-final badge");
  await assertClassContains(videoOnlyBadges.nth(4), "has-copy-dot", "Video Only Plan copy-final badge");
  await page.getByText("拷贝成终视频", { exact: true }).first().waitFor({ state: "visible" });
  assertUsesDialogueAssetKey(calls.videoOnlyPlanResult[0], "dak_001", ["srt_001", "dlg_001", "scene_001", "shot_001"]);
  await page.setViewportSize({ width: 390, height: 900 });
  await page.locator(".kbsp-vop-shell").waitFor({ state: "visible" });
  await page.getByLabel("关闭视频计划").click();
  await page.setViewportSize({ width: 1440, height: 1000 });
  await assertVisible(page, ".kbsp-editor");
}

export default { name: "plan-modal-status", run };

runIfMain(import.meta.url, [{ name: "plan-modal-status", run }]);
