#!/usr/bin/env node
import { executeStoryboardScenarios } from "./koubo-storyboard/fixture.mjs";
import bindingSaveReload from "./koubo-storyboard/binding-save-reload.mjs";
import dialogueMergeArchiveBoundary from "./koubo-storyboard/dialogue-merge-archive-boundary.mjs";
import finalUnboundConfirm from "./koubo-storyboard/final-unbound-confirm.mjs";
import planModalStatus from "./koubo-storyboard/plan-modal-status.mjs";
import pictureInPicture from "./koubo-storyboard/picture-in-picture.mjs";
import slotIdentityRendering from "./koubo-storyboard/slot-identity-rendering.mjs";
import structureClearIsolation from "./koubo-storyboard/structure-clear-isolation.mjs";
import talkingHeadToggle from "./koubo-storyboard/talking-head-toggle.mjs";
import uploadAssetPool from "./koubo-storyboard/upload-asset-pool.mjs";

await executeStoryboardScenarios([
  slotIdentityRendering,
  pictureInPicture,
  uploadAssetPool,
  bindingSaveReload,
  finalUnboundConfirm,
  talkingHeadToggle,
  dialogueMergeArchiveBoundary,
  planModalStatus,
  structureClearIsolation,
]);
