import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  convertStaleFragmentToManual,
  createClipJobInput,
  isTransientClipJobPollError,
  normalizeClipItems,
  normalizeEditorPayload,
  normalizeSearchCandidate,
  normalizeSearchRun,
  selectionFromEditorNavigation,
} from "../src/modules/mediaLibrary/editor/editorModel.js";

const fragment = (scheme, index, overrides = {}) => ({
  fragment_id: `${scheme}_${String(index).padStart(4, "0")}`,
  analysis_run_id: `mlar_${scheme}_1`,
  start_ms: index * 1_000,
  end_ms: index * 1_000 + 900,
  summary: `${scheme} ${index}`,
  ...overrides,
});

const payload = {
  item: {
    asset_id: "mla_contract",
    display_name: "10 分钟代表视频",
    duration_ms: 600_000,
    preview_url: "/api/media-library/mla_contract/preview",
    thumbnail_url: "/api/media-library/mla_contract/thumbnail",
    upload_status: "ready",
    archived: false,
  },
  source_version: "abc123",
  fragments: {
    dialogue: Array.from({ length: 500 }, (_, index) => fragment("dialogue", index)),
    visual: Array.from({ length: 500 }, (_, index) => fragment("visual", index)),
    composite: Array.from({ length: 500 }, (_, index) => fragment("composite", index)),
  },
  runs: {
    dialogue: {
      analysis_run_id: "mlar_dialogue_1",
      scheme: "dialogue",
      status: "ready",
      model_alias: "public-alias",
      provider: "must-not-leak",
      model_config_label: "must-not-leak",
      tool_session_id: "must-not-leak",
    },
    visual_structure: null,
    visual_semantic: { analysis_run_id: "mlar_visual_1", scheme: "visual_semantic", status: "ready" },
    composite: { analysis_run_id: "mlar_composite_1", scheme: "composite", status: "ready" },
  },
  clips: [],
  import_targets: [{
    task_id: 27,
    session_id: 31,
    title: "产品口播",
    workflow_mode: "script",
    updated_at: 1,
  }],
  navigation_context: {
    start_ms: 42_100,
    end_ms: 49_800,
    target_task_id: 27,
    dialogue_asset_key: "dialogue_0005",
    search_id: "mls_1",
    return_to: "storyboard_dialogue",
    target_valid: true,
    dialogue_valid: true,
  },
  fragment_count: 1_500,
};

const editor = normalizeEditorPayload(payload, "mla_contract");
assert.equal(editor.valid, true, editor.contractErrors.join("\n"));
assert.equal(editor.capacity.fragmentCount, 1_500, "all fragments must be retained");
assert.equal(editor.fragments.dialogue.length, 500);
assert.deepEqual(Object.keys(editor.runs.dialogue).sort(), [
  "analysis_run_id",
  "model_alias",
  "scheme",
  "status",
]);
assert.equal(selectionFromEditorNavigation(editor).startMs, 42_100);
assert.equal(selectionFromEditorNavigation(editor).manualOverride, true);
assert.equal(
  selectionFromEditorNavigation(editor).sourceDialogueAssetKey,
  "dialogue_0005",
);

const directStoryboardPayload = structuredClone(payload);
directStoryboardPayload.navigation_context.search_id = null;
const directStoryboardEditor = normalizeEditorPayload(
  directStoryboardPayload,
  "mla_contract",
);
assert.equal(directStoryboardEditor.valid, true);
assert.equal(
  selectionFromEditorNavigation(
    directStoryboardEditor,
  ).sourceDialogueAssetKey,
  "",
  "return navigation without search_id must not become clip search provenance",
);

const truncated = normalizeEditorPayload({ ...payload, fragment_count: 1_501 }, "mla_contract");
assert.equal(truncated.valid, false);
assert.match(truncated.contractErrors.join(" "), /拒绝静默截断/);

const stalePayload = structuredClone(payload);
stalePayload.fragments.dialogue[0].stale = true;
stalePayload.navigation_context.matched_fragment_id = "dialogue_0000";
stalePayload.navigation_context.start_ms = 0;
stalePayload.navigation_context.end_ms = 900;
const staleEditor = normalizeEditorPayload(stalePayload, "mla_contract");
const stale = staleEditor.fragments.dialogue[0];
const converted = convertStaleFragmentToManual(stale);
assert.deepEqual(converted, {
  startMs: 0,
  endMs: 900,
  sourceScheme: "",
  sourceFragmentId: "",
  sourceRunId: "",
  sourceSearchId: "",
  sourceDialogueAssetKey: "",
  manualOverride: true,
});

const clipInput = createClipJobInput(
  editor,
  {
    startMs: 599_000,
    endMs: 600_000,
    sourceScheme: "",
    sourceFragmentId: "",
    sourceRunId: "",
    sourceSearchId: "mls_1",
    sourceDialogueAssetKey: "dialogue_0005",
    manualOverride: true,
  },
  "尾部非关键帧",
  "clip.contract-1",
);
assert.deepEqual(clipInput, {
  source_version: "abc123",
  start_ms: 599_000,
  end_ms: 600_000,
  display_name: "尾部非关键帧",
  source_scheme: null,
  source_fragment_id: null,
  source_analysis_run_id: null,
  source_search_id: "mls_1",
  source_dialogue_asset_key: "dialogue_0005",
  manual_override: true,
  idempotency_key: "clip.contract-1",
});
const directNavigationClipInput = createClipJobInput(
  editor,
  {
    startMs: 599_000,
    endMs: 600_000,
    sourceScheme: "",
    sourceFragmentId: "",
    sourceRunId: "",
    sourceSearchId: "",
    sourceDialogueAssetKey: "dialogue_0005",
    manualOverride: true,
  },
  "直接导航尾部",
  "clip.contract-direct",
);
assert.equal(directNavigationClipInput.source_search_id, null);
assert.equal(
  directNavigationClipInput.source_dialogue_asset_key,
  null,
  "dialogue key without a real search run must be cleared before submit",
);
assert.equal(createClipJobInput(editor, { startMs: 0, endMs: 249 }, "too short", "clip.short"), null);
assert.equal(normalizeClipItems({ items: [] }, 600_000).valid, true);
assert.equal(normalizeClipItems({ clips: [] }, 600_000).valid, true);
assert.equal(normalizeClipItems({}, 600_000).valid, false);
assert.equal(
  isTransientClipJobPollError(new TypeError("Load failed")),
  true,
);
assert.equal(
  isTransientClipJobPollError(new Error("502 Bad Gateway")),
  true,
);
assert.equal(
  isTransientClipJobPollError(
    new Error('{"detail":{"code":"clip_job_lost"}}'),
  ),
  false,
  "terminal clip_job_lost must not be retried as a transient outage",
);

const external = normalizeSearchCandidate({
  source: "external",
  candidate_id: "external_1",
  provider: "pexels",
  provider_asset_id: "asset_1",
  provider_search_id: "provider_search_1",
  asset_id: null,
  source_version: null,
  display_name: "外部候选",
  width: 1920,
  height: 1080,
  creator: { name: "Example Creator", url: "https://example.test/creator" },
  license: {
    name: "Example License",
    url: "https://example.test/license",
    license_status: "verified",
    requires_attribution: true,
    attribution_text: "Example Creator",
  },
  allowed_actions: ["preview", "import_whole"],
});
assert.equal(external.valid, true);
assert.deepEqual(external.candidate.allowedActions, ["preview", "import_whole"]);
assert.equal(external.candidate.assetId, "");
assert.equal(external.candidate.provider, "pexels");
assert.equal(external.candidate.aspect, "1920:1080");
assert.deepEqual(external.candidate.creator, {
  name: "Example Creator",
  url: "https://example.test/creator",
});
assert.deepEqual(external.candidate.license, {
  name: "Example License",
  url: "https://example.test/license",
  status: "verified",
  requiresAttribution: true,
  attributionText: "Example Creator",
});

const externalWithEditor = normalizeSearchCandidate({
  source: "external",
  candidate_id: "external_2",
  provider: "pexels",
  provider_search_id: "provider_search_2",
  display_name: "越权候选",
  allowed_actions: ["preview", "open_editor", "import_whole"],
});
assert.equal(externalWithEditor.valid, false);
assert.match(externalWithEditor.errors.join(" "), /越权动作 open_editor/);

const internal = normalizeSearchCandidate({
  source: "media_library",
  candidate_id: "mla_other",
  asset_id: "mla_other",
  source_version: "def456",
  display_name: "全局素材",
  allowed_actions: ["preview", "open_editor", "import_original"],
});
assert.equal(internal.valid, true);

const derivedClip = normalizeSearchCandidate({
  source: "media_library",
  candidate_kind: "derived_clip",
  candidate_id: "mlc_searchable",
  asset_id: null,
  source_asset_id: "mla_other",
  source_clip_id: "mlc_searchable",
  source_version: "clip-hash",
  content_sha256: "clip-hash",
  display_name: "玻璃碗中的深色液体",
  duration_ms: 3_000,
  tags: ["玻璃碗", "深色液体"],
  candidate_start_ms: 0,
  candidate_end_ms: 3_000,
  source_start_ms: 5_000,
  source_end_ms: 8_000,
  time_basis: "candidate",
  matched_fragments: [],
  allowed_actions: ["preview", "import_clip"],
});
assert.equal(derivedClip.valid, true, derivedClip.errors.join("\n"));
assert.equal(derivedClip.candidate.assetId, "");
assert.equal(derivedClip.candidate.sourceClipId, "mlc_searchable");
assert.deepEqual(derivedClip.candidate.tags, ["玻璃碗", "深色液体"]);
const badDerivedClip = normalizeSearchCandidate({
  ...derivedClip.candidate,
  source: "media_library",
  candidate_kind: "derived_clip",
  candidate_id: "mlc_searchable",
  asset_id: null,
  source_asset_id: "mla_other",
  source_clip_id: "mlc_searchable",
  source_version: "clip-hash",
  content_sha256: "clip-hash",
  duration_ms: 3_000,
  candidate_start_ms: 0,
  candidate_end_ms: 3_000,
  source_start_ms: 5_000,
  source_end_ms: 5_000,
  time_basis: "candidate",
  matched_fragments: [],
  allowed_actions: ["preview", "import_clip"],
});
assert.equal(badDerivedClip.valid, false);
assert.match(badDerivedClip.errors.join(" "), /时间基准/);

const run = normalizeSearchRun({
  search_id: "mls_unified",
  result_count: 2,
  planner_degraded: false,
  search_runs: { media_library: "mls_internal", external: "provider_search_1" },
  source_errors: {},
  items: [
    {
      source: "external",
      candidate_id: "external_1",
      provider: "pexels",
      provider_search_id: "provider_search_1",
      display_name: "外部",
      creator: { name: "Creator", url: "https://example.test/creator" },
      license: { name: "License", url: "https://example.test/license", license_status: "verified" },
      allowed_actions: ["preview", "import_whole"],
    },
    {
      source: "media_library",
      candidate_id: "mla_other",
      asset_id: "mla_other",
      source_version: "def",
      display_name: "内部",
      allowed_actions: ["preview", "open_editor", "import_original"],
    },
  ],
});
assert.equal(run.valid, true, run.errors.join("\n"));
assert.equal(run.searchRuns.external, "provider_search_1");

const searchPanelSource = readFileSync(new URL("../src/modules/mediaLibrary/editor/EditorSearchPanel.jsx", import.meta.url), "utf8");
const editorPageSource = readFileSync(new URL("../src/modules/mediaLibrary/pages/MediaLibraryEditorPage.jsx", import.meta.url), "utf8");
assert.match(searchPanelSource, /跨页面对白\/关键词检索/);
assert.match(searchPanelSource, /已发布的四帧视觉描述检索/);
assert.match(searchPanelSource, /暂不包含图像或视频向量相似度检索/);
assert.match(searchPanelSource, /正在检索…" : "开始检索"/);
assert.match(searchPanelSource, /Task #\{target\.taskId\} · \{target\.title\}/);
assert.match(editorPageSource, /导入目标 StoryBoard/);
assert.match(editorPageSource, /Task #\{target\.taskId\} · \{target\.title\}/);
assert.match(editorPageSource, /source_kind:\s*"media_library_clip"/);
assert.match(editorPageSource, /search-clip-import/);
assert.match(searchPanelSource, /"text embedding rerank"[\s\S]*return "文本关键词相关"/);
for (const label of [
  "竖屏比例匹配",
  "横屏比例匹配",
  "方形比例匹配",
  "授权信息已确认",
  "已使用来源站点兼容关键词",
  "来源提供的相关性依据",
]) {
  assert.ok(searchPanelSource.includes(label), `external score reason mapping must include ${label}`);
}
assert.match(searchPanelSource, /<Show when=\{props\.searchFragmentRefs\.length\}>[\s\S]*>清空<\/button>/);

console.log("media library editor model contract: ok");
