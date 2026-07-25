import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import {
  analysisSchemeStatusMeta,
  actionEvidenceLabel,
  canRunComposite,
  deriveVisualStatus,
  evidenceClaimLabel,
  formatFragmentTimeMs,
  normalizeOpenCutDetail,
  normalizeVisualCurrent,
  isNoAudioDialogueResult,
  openCutOverallStatusMeta,
  openCutStatusMeta,
  resolveVisualDisplayResult,
  samplingStrategyLabel,
  visualSemanticRunState,
} from "../src/modules/mediaLibrary/detail/mediaLibraryDetailModel.js";

const frontendRoot = fileURLToPath(new URL("..", import.meta.url));

assert.equal(deriveVisualStatus("not_analyzed", "not_analyzed"), "not_analyzed");
assert.equal(deriveVisualStatus("running", "not_analyzed"), "running");
assert.equal(deriveVisualStatus("ready", "blocked"), "blocked");
assert.equal(deriveVisualStatus("ready", "not_analyzed"), "partial");
assert.equal(deriveVisualStatus("ready", "failed"), "partial");
assert.equal(deriveVisualStatus("ready", "running"), "running");
assert.equal(deriveVisualStatus("ready", "ready"), "ready");
assert.equal(deriveVisualStatus("ready", "stale"), "stale");
assert.equal(deriveVisualStatus("failed", "not_analyzed"), "failed");

for (const status of [
  "not_analyzed",
  "queued",
  "running",
  "blocked",
  "ready",
  "stale",
  "failed",
]) {
  assert.notEqual(openCutStatusMeta(status).label, "");
}
assert.equal(openCutStatusMeta("processing").label, "运行中");
assert.match(openCutStatusMeta("completed").label, /未知状态/);

const silentDetail = normalizeOpenCutDetail({
  open_cut: {
    status: "blocked",
    dialogue_status: "blocked",
    dialogue_error_code: "video_has_no_audio",
    dialogue_error: "源视频没有音轨，无法进行对白识别。",
    visual_structure_status: "ready",
    visual_semantic_status: "not_analyzed",
    composite_status: "not_analyzed",
  },
});
assert.equal(silentDetail.schemes.dialogue.errorCode, "video_has_no_audio");
assert.equal(isNoAudioDialogueResult(silentDetail.schemes.dialogue), true);
assert.equal(
  analysisSchemeStatusMeta("dialogue", silentDetail.schemes.dialogue).label,
  "无音轨",
);
assert.equal(openCutOverallStatusMeta(silentDetail).label, "部分可用");

const detail = normalizeOpenCutDetail({
  open_cut: {
    task_id: 42,
    session_id: 84,
    dialogue_status: "ready",
    visual_structure_status: "ready",
    visual_semantic_status: "blocked",
    visual_semantic_error: "cloud_visual_data_transfer_not_authorized",
    composite_status: "not_analyzed",
  },
  analysis_results: {
    visual: {
      items: [{
        fragment_id: "scene_0001",
        start: 1.25,
        end: 4.5,
        keyframes: [{
          keyframe_id: "scene_0001-midpoint",
          keyframe_time: 2.875,
          image_url: "/api/session-files/1",
        }],
      }],
    },
  },
});
assert.equal(detail.schemes.visual.structureStatus, "ready");
assert.equal(detail.schemes.visual.semanticStatus, "blocked");
assert.equal(detail.schemes.visual.status, "blocked");
assert.equal(detail.schemes.visual.items[0].startMs, 1250);
assert.equal(detail.schemes.visual.items[0].endMs, 4500);
assert.equal(detail.schemes.visual.items[0].keyframes[0].timeMs, 2875);
assert.equal(canRunComposite(detail), false);

const publicCurrent = normalizeVisualCurrent({
  run: {
    analysis_run_id: "mlar_visual_semantic_public",
    scheme: "visual_semantic",
    status: "ready",
    schema_version: "media_library_visual_semantic_v1",
    prompt_version: "visual-prompt-v1",
    model_config_label: "approved-vision",
    model_version: "alias-version-7",
    provider: "must-not-leak-provider",
    model_id: "must-not-leak-model-id",
    progress: { sampling_strategy: "scene_midpoint_v1" },
  },
  items: [{
    fragment_id: "scene_0001",
    start_ms: 1250,
    end_ms: 4500,
    keyframe_refs: ["scene_0001-midpoint"],
    visual_summary: "一名讲解者在室内手持桌面产品。",
    people: ["一名讲解者"],
    objects: ["桌面产品"],
    scene: "室内演示区",
    action: null,
    claim_evidence: {
      people: ["scene_0001-midpoint"],
      objects: ["scene_0001-midpoint"],
      scene: ["scene_0001-midpoint"],
      action: [],
    },
    confidence: 0.82,
    needs_review: false,
  }],
});
assert.equal(publicCurrent.run.modelAlias, "approved-vision");
assert.equal(publicCurrent.run.modelVersion, "alias-version-7");
assert.equal(publicCurrent.items[0].action, null);
assert.equal(publicCurrent.items[0].startMs, 1250);
assert.equal(publicCurrent.items[0].samplingStrategy, "scene_midpoint_v1");
assert.deepEqual(publicCurrent.items[0].claimEvidence.action, []);
assert.equal(
  JSON.stringify(publicCurrent).includes("must-not-leak-provider"),
  false,
);
assert.equal(
  JSON.stringify(publicCurrent).includes("must-not-leak-model-id"),
  false,
);

const displayed = resolveVisualDisplayResult(
  detail.schemes.visual,
  publicCurrent,
);
assert.equal(displayed.semanticRun.scheme, "visual_semantic");
assert.equal(displayed.items[0].people[0], "一名讲解者");
assert.equal(displayed.items[0].keyframes[0].id, "scene_0001-midpoint");
assert.equal(
  actionEvidenceLabel(displayed.items[0]),
  "仅凭当前画面中点单帧无法可靠判断连续动作。",
);
assert.doesNotMatch(actionEvidenceLabel(displayed.items[0]), /action|null|上游/i);
assert.equal(samplingStrategyLabel(displayed.items[0].samplingStrategy), "画面中点单帧");
assert.equal(
  samplingStrategyLabel("scene_uniform_4_v1"),
  "四帧均匀采样（12.5% / 37.5% / 62.5% / 87.5%）",
);
assert.equal(evidenceClaimLabel("people"), "人物");
assert.equal(evidenceClaimLabel("action"), "动作");
assert.equal(formatFragmentTimeMs(61_234), "1:01.234");

assert.deepEqual(
  visualSemanticRunState(detail.schemes.visual, false),
  {
    active: false,
    retry: true,
    runnable: false,
    disabledReason: "请先确认本次运行的云端图像传输授权",
    label: "重新运行视觉语义",
  },
);
assert.equal(
  visualSemanticRunState(detail.schemes.visual, true).runnable,
  true,
);
assert.equal(
  visualSemanticRunState(
    { structureStatus: "ready", semanticStatus: "running" },
    true,
  ).runnable,
  false,
);
assert.equal(
  canRunComposite({
    schemes: {
      dialogue: { status: "ready" },
      visual: { structureStatus: "ready", semanticStatus: "ready" },
    },
  }),
  true,
);
assert.equal(
  canRunComposite({
    schemes: {
      dialogue: { status: "completed" },
      visual: { structureStatus: "ready", semanticStatus: "ready" },
    },
  }),
  false,
);

const apiSource = await readFile(
  `${frontendRoot}/src/lib/api.ts`,
  "utf8",
);
const visualApiBlock = apiSource.slice(
  apiSource.indexOf("mediaLibraryRunVisual:"),
  apiSource.indexOf("mediaLibraryUploadCreate:"),
);
const visualInputTypeBlock = apiSource.slice(
  apiSource.indexOf("export type MediaLibraryVisualRunInput"),
  apiSource.indexOf("export type MediaLibraryVisualRunPayload"),
);
assert.match(
  visualApiBlock,
  /analyses\/visual\/run/,
);
assert.match(
  visualInputTypeBlock,
  /allow_cloud_visual_data_transfer/,
);
assert.match(
  visualInputTypeBlock,
  /force_structure/,
);
assert.match(
  visualInputTypeBlock,
  /force_semantic/,
);
assert.match(
  visualApiBlock,
  /analyses\/visual\/current/,
);
assert.doesNotMatch(
  visualApiBlock,
  /visual-semantic\/runs/,
);
assert.doesNotMatch(
  `${visualApiBlock}\n${visualInputTypeBlock}`,
  /provider|model_config_id|api_key/,
);

const detailHeaderSource = await readFile(
  `${frontendRoot}/src/modules/mediaLibrary/detail/MediaLibraryDetailHeader.jsx`,
  "utf8",
);
const tabsSource = await readFile(
  `${frontendRoot}/src/modules/mediaLibrary/detail/MediaLibraryAnalysisTabs.jsx`,
  "utf8",
);
const assetInfoSource = await readFile(
  `${frontendRoot}/src/modules/mediaLibrary/detail/MediaLibraryAssetInfo.jsx`,
  "utf8",
);
const visualPanelSource = await readFile(
  `${frontendRoot}/src/modules/mediaLibrary/detail/MediaLibraryVisualPanel.jsx`,
  "utf8",
);
assert.match(detailHeaderSource, />素材分析</);
assert.doesNotMatch(detailHeaderSource, />OpenCut</);
assert.doesNotMatch(tabsSource, /aria-label="OpenCut/);
assert.match(tabsSource, /aria-label="素材分析结果"/);
assert.match(assetInfoSource, /<details class="media-library-technical-details">/);
assert.match(assetInfoSource, /globalThis\.navigator\.clipboard\.writeText/);
assert.match(assetInfoSource, /内容版本/);
assert.match(assetInfoSource, /视觉语义运行/);
assert.doesNotMatch(visualPanelSource, /action=null|无上游视觉证据/);

console.log("media library visual semantic model/API contract: ok");
