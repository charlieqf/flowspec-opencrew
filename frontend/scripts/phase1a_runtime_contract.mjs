import assert from "node:assert/strict";

import { createKouboStoryboardTtsController } from "../src/modules/koubo/KouboStoryBoard/kouboStoryboardTts.js";
import { resolveVideoModelCapability } from "../src/modules/koubo/UploadAssetLibrary/videoModelCapabilities.js";

function ttsController({ planValue = {}, taskValue = {}, metaValue = {} } = {}) {
  const config = {
    active_provider: "generic-provider",
    providers: [{
      provider: "generic-provider",
      provider_label: "Generic",
      model: "generic-tts-model",
      enabled: true,
      models: [{ model: "generic-tts-model", enabled: true, voices: [{ voice_id: "generic-voice" }] }],
    }],
  };
  const noop = () => {};
  return createKouboStoryboardTtsController({
    kbApi: {},
    plan: () => planValue,
    setPlan: noop,
    task: () => taskValue,
    meta: () => metaValue,
    setState: noop,
    setDirty: noop,
    timingModel: () => null,
    setTimingModel: noop,
    timingSecondsPerChar: () => 0,
    ttsModelConfig: () => config,
    setTTSModelConfig: noop,
    runAction: async (_key, fn) => fn(),
    sessionId: () => 1,
    sceneAudioState: () => ({}),
    setSceneAudioState: noop,
    updatePlan: noop,
    ensureSceneWorkingAssets: noop,
    fixedShotSeconds: () => 0,
    fixedSceneSeconds: () => 0,
    setSelectedShotIndex: noop,
    setSelectedDialogueId: noop,
    setScope: noop,
    setFixedMenuOpen: noop,
    setGroupingDirty: noop,
    roleAccess: { isAdmin: true },
  });
}

function videoReferenceMode(mode) {
  return resolveVideoModelCapability(
    { provider: "provider", model: "model" },
    {
      providers: [{
        provider: "provider",
        models: [{ model: "model", reference_images: { min: 0, max: 2, mode } }],
      }],
    },
  ).referenceMode;
}

const clonedVoiceSettings = ttsController({
  taskValue: {
    create_mode: "person_talking_head",
    talking_head: { voice_id: "clone-voice" },
  },
}).audioSettings();
assert.deepEqual(
  {
    provider: clonedVoiceSettings.provider,
    model: clonedVoiceSettings.model,
    voiceId: clonedVoiceSettings.voiceId,
    voiceSource: clonedVoiceSettings.voiceSource,
  },
  { provider: "", model: "", voiceId: "clone-voice", voiceSource: "cloud_clone" },
  "Talking Head cloud-clone voices must not inherit the active generic TTS provider/model",
);

const normalVoiceSettings = ttsController().audioSettings();
assert.deepEqual(
  {
    provider: normalVoiceSettings.provider,
    model: normalVoiceSettings.model,
    voiceId: normalVoiceSettings.voiceId,
  },
  { provider: "generic-provider", model: "generic-tts-model", voiceId: "generic-voice" },
  "normal TTS without a saved selection should still use the active configured model",
);

assert.equal(videoReferenceMode("first_frame"), "first_frame");
assert.equal(videoReferenceMode("input_references"), "input_references");
assert.equal(videoReferenceMode("reference_to_video"), "input_references");
for (const unsupported of ["start_frame", "ref_img_url/start_frame", "reference_image", "reference_images"]) {
  assert.equal(videoReferenceMode(unsupported), "", `unsupported runtime reference mode leaked through: ${unsupported}`);
}

console.log("Phase 1A runtime contract passed.");
