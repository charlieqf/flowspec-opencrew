import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import {
  actionEvidenceLabel,
  compositeRunState,
  normalizeCompositeCurrent,
  resolveCompositeDisplayResult,
} from "../src/modules/mediaLibrary/detail/mediaLibraryDetailModel.js";

const frontendRoot = fileURLToPath(new URL("..", import.meta.url));

const readyDependencies = {
  schemes: {
    dialogue: { status: "ready" },
    visual: {
      structureStatus: "ready",
      semanticStatus: "ready",
    },
    composite: { status: "not_analyzed", items: [] },
  },
};

assert.deepEqual(compositeRunState(readyDependencies), {
  active: false,
  retry: false,
  runnable: true,
  disabledReason: "",
  prerequisiteMessage: "",
  label: "运行综合分析",
});
assert.equal(compositeRunState({
  ...readyDependencies,
  schemes: {
    ...readyDependencies.schemes,
    dialogue: { status: "completed" },
  },
}).disabledReason, "请先完成当前对白分析");
assert.match(compositeRunState({
  ...readyDependencies,
  schemes: {
    ...readyDependencies.schemes,
    dialogue: { status: "not_analyzed" },
    visual: { structureStatus: "stale", semanticStatus: "blocked" },
  },
}).prerequisiteMessage, /对白分析、画面结构分析、视觉语义分析/);
assert.equal(compositeRunState({
  ...readyDependencies,
  schemes: {
    ...readyDependencies.schemes,
    visual: { structureStatus: "stale", semanticStatus: "ready" },
  },
}).disabledReason, "请先完成当前画面结构分析");
assert.equal(compositeRunState({
  ...readyDependencies,
  schemes: {
    ...readyDependencies.schemes,
    visual: { structureStatus: "ready", semanticStatus: "blocked" },
  },
}).disabledReason, "请先完成当前视觉语义分析");
assert.equal(compositeRunState({
  ...readyDependencies,
  schemes: {
    ...readyDependencies.schemes,
    composite: { status: "running" },
  },
}).runnable, false);
for (const status of ["blocked", "ready", "stale", "failed"]) {
  const state = compositeRunState({
    ...readyDependencies,
    schemes: {
      ...readyDependencies.schemes,
      composite: { status },
    },
  });
  assert.equal(state.retry, true);
  assert.equal(state.runnable, true);
  assert.equal(state.label, "重新运行综合分析");
}

const current = normalizeCompositeCurrent({
  run: {
    analysis_run_id: "mlar_composite_public",
    scheme: "composite",
    status: "stale",
    schema_version: "media_library_composite_v1",
    prompt_version: "composite_default_v1",
    model_config_label: "approved-text",
    model_version: "alias-version-3",
    provider: "must-not-leak-provider",
    model_id: "must-not-leak-model-id",
    model_session_id: "must-not-leak-session",
    error: {
      code: "analysis_upstream_changed",
      user_message: "上游分析已变化，当前结果只读且不可发布。",
    },
  },
  items: [{
    fragment_id: "composite_0001",
    start_ms: 12_300,
    end_ms: 18_800,
    title: "讲解产品的核心卖点",
    summary: "讲解者手持产品并说明核心用途。",
    dialogue_text: "核心用途包括……",
    visual_summary: "讲解者在室内手持桌面产品。",
    keywords: ["产品讲解", "演示"],
    people: ["讲解者"],
    objects: ["产品"],
    scene: "室内演示区",
    action: null,
    dialogue_refs: ["dialogue_0008"],
    visual_refs: ["scene_0003"],
    visual_claim_refs: {
      people: ["scene_0003"],
      objects: ["scene_0003"],
      scene: ["scene_0003"],
      action: [],
    },
    keyframe_refs: ["keyframe_0012"],
    boundary_reasons: ["沿用视觉 Scene 边界"],
    confidence: 0.91,
    needs_review: false,
  }],
});

assert.equal(current.run.scheme, "composite");
assert.equal(current.run.modelAlias, "approved-text");
assert.equal(current.run.modelVersion, "alias-version-3");
assert.equal(current.run.error, "上游分析已变化，当前结果只读且不可发布。");
assert.equal(current.items[0].startMs, 12_300);
assert.equal(current.items[0].endMs, 18_800);
assert.equal(current.items[0].action, null);
assert.deepEqual(current.items[0].dialogueRefs, ["dialogue_0008"]);
assert.deepEqual(current.items[0].visualRefs, ["scene_0003"]);
assert.deepEqual(current.items[0].visualClaimRefs.action, []);
assert.deepEqual(current.items[0].keyframeRefs, ["keyframe_0012"]);
assert.deepEqual(current.items[0].boundaryReasons, ["沿用视觉 Scene 边界"]);
assert.equal(
  actionEvidenceLabel(current.items[0]),
  "当前没有足够画面证据判断连续动作。",
);
assert.doesNotMatch(actionEvidenceLabel(current.items[0]), /action|null|上游/i);
for (const secret of [
  "must-not-leak-provider",
  "must-not-leak-model-id",
  "must-not-leak-session",
]) {
  assert.equal(JSON.stringify(current).includes(secret), false);
}

const displayed = resolveCompositeDisplayResult(
  { status: "stale", error: "", items: [] },
  current,
);
assert.equal(displayed.semanticRun.id, "mlar_composite_public");
assert.equal(displayed.items[0].id, "composite_0001");
assert.match(displayed.error, /上游分析已变化/);

const apiSource = await readFile(`${frontendRoot}/src/lib/api.ts`, "utf8");
const compositeApiBlock = apiSource.slice(
  apiSource.indexOf("mediaLibraryCompositeCurrent:"),
  apiSource.indexOf("mediaLibraryUploadCreate:"),
);
assert.match(compositeApiBlock, /analyses\/composite\/current/);
assert.match(compositeApiBlock, /analyses\/composite\/run/);
assert.match(compositeApiBlock, /JSON\.stringify\(\{ force \}\)/);
assert.doesNotMatch(
  compositeApiBlock,
  /prompt_version|model_config_id|provider|model_id|api_key/,
);

const pageSource = await readFile(
  `${frontendRoot}/src/modules/mediaLibrary/pages/MediaLibraryDetailPage.jsx`,
  "utf8",
);
assert.match(pageSource, /api\.mediaLibraryCompositeCurrent/);
assert.match(pageSource, /api\.mediaLibraryRunComposite/);
assert.match(pageSource, /resolveCompositeDisplayResult/);
assert.doesNotMatch(
  pageSource,
  /prompt_version|model_config_id|provider|model_id|api_key/,
);

const compositePanelSource = await readFile(
  `${frontendRoot}/src/modules/mediaLibrary/detail/MediaLibraryCompositePanel.jsx`,
  "utf8",
);
assert.match(compositePanelSource, /运行条件未满足/);
assert.doesNotMatch(compositePanelSource, /media-library-composite-dependencies/);
assert.doesNotMatch(compositePanelSource, /currentRun\(\)\.id/);
assert.doesNotMatch(compositePanelSource, /\bcomposite\(\)/);

console.log("media library composite model/API contract: ok");
