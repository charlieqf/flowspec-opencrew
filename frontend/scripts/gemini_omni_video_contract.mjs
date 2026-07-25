import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

import {
  isStatefulVideoCapability,
  resolveVideoModelCapability,
  videoAgentModelSupportsText,
  videoCapabilitySupportsTask,
} from "../src/modules/koubo/UploadAssetLibrary/videoModelCapabilities.js";


const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const read = (path) => readFileSync(resolve(root, path), "utf8");

const config = {
  agent_model_aliases: [
    {
      alias: "Omni Flash",
      capability: {
        input_modes: ["text", "video_reference"],
        capabilities: ["Preview video model"],
        tasks: ["text_to_video", "image_to_video", "reference_to_video", "edit"],
        stateful_edit: true,
        provider_state: "interaction",
        supports_video_input: true,
        supports_audio_input: false,
        aspect_ratios: ["16:9", "9:16"],
        duration: { min: 3, max: 3, allowed: [3] },
        reference_images: { min: 0, max: 8, mode: "file_or_inline" },
        reference_videos: { min: 0, max: 1, mode: "files_api" },
        reference_audios: { min: 0, max: 0 },
      },
    },
  ],
};
const source = { agentVideoAlias: "Omni Flash" };
const capability = resolveVideoModelCapability(source, config, { isAgent: false });

assert.equal(videoAgentModelSupportsText(config, config.agent_model_aliases[0]), true);
assert.equal(isStatefulVideoCapability(capability), true);
assert.equal(videoCapabilitySupportsTask(capability, "edit"), true);
assert.deepEqual(capability.params.duration, { min: 3, max: 3, presets: [3] });
assert.deepEqual(capability.params.count, { values: [1], enabled: false });
assert.equal(capability.references.videos.max, 1);
assert.equal(capability.references.audios.max, 0);
assert.deepEqual(capability.params.aspect.values, ["16:9", "9:16"]);
assert.ok(!capability.tasks.includes("preview video model"), "display capabilities must not become task enums");

const apiSource = read("src/modules/koubo/KouboStoryBoard/kouboStoryboardApi.js");
const overlaySource = read("src/modules/koubo/UploadAssetLibrary/UploadAssetLibraryOverlay.jsx");
const panelSource = read("src/modules/koubo/UploadAssetLibrary/components/VideoAgentPanel.jsx");
const librarySource = read("src/modules/koubo/UploadAssetLibrary/components/VideoWorkspaceLibrary.jsx");

for (const token of [
  "assetLibraryCurrentVideoInteraction",
  "assetLibraryVideoInteraction",
  "deleteAssetLibraryVideoInteractionCloudContext",
  "video-interactions/current",
  "cloud-context/delete",
]) assert.ok(apiSource.includes(token), `missing API contract token: ${token}`);

for (const token of [
  "client_action_id",
  "video_thread_id",
  "parent_turn_id",
  "source_video_asset_id",
]) assert.ok(overlaySource.includes(token), `missing generation payload field: ${token}`);

for (const token of [
  "newClientActionId",
  "statefulGenerationPayload",
  "每次生成或继续编辑都会产生一次新的付费调用",
  "云端会保存编辑上下文",
  "供应商强制且不可移除的来源水印",
  "从本地视频新建链",
  "清除云端上下文",
  "provider_state_status",
]) assert.ok(panelSource.includes(token), `missing stateful component behavior: ${token}`);

assert.ok(librarySource.includes("koubo-storyboard:continue-video-version"));
assert.ok(librarySource.includes("有状态版本"));
assert.ok(apiSource.includes("const attempts = payload?.client_action_id ? 2 : 1"), "SSE replay must retain one client action id");
assert.ok(!panelSource.includes("previous_interaction_id"), "the browser component must never receive provider interaction IDs");
const directGenerationStart = panelSource.indexOf("async function sendDirectVideoGeneration");
const directGenerationEnd = panelSource.indexOf("async function requestDirectVideoGeneration", directGenerationStart);
const directGenerationSource = panelSource.slice(directGenerationStart, directGenerationEnd);
assert.ok(directGenerationStart >= 0 && directGenerationEnd > directGenerationStart);
assert.ok(
  directGenerationSource.indexOf("statefulGenerationPayload(newClientActionId())")
    < directGenerationSource.indexOf("consumeComposerReferenceAssets()"),
  "upload-edit must capture the selected source video before consuming reference slots",
);

console.log("Gemini Omni frontend capability and component contracts passed.");
