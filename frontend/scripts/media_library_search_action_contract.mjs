import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  ASSET_SEARCH_SOURCES,
  MEDIA_LIBRARY_SEARCH_CAPABILITY_TEXT,
  MEDIA_LIBRARY_ZERO_RESULT_SUGGESTIONS,
  assetSearchSourceLabel,
  buildMediaLibraryEditorHash,
  candidateSupportsAction,
  candidateSupportsImport,
  formatSearchRange,
  mediaLibraryFragmentKindLabel,
  normalizeMediaLibrarySearchResponse,
  storyboardDialogueSearchContext,
} from "../src/modules/koubo/mediaLibrarySearchModel.js";

const noDialogue = storyboardDialogueSearchContext({ id: 27 }, null);
assert.equal(noDialogue.enabled, false);
assert.match(noDialogue.disabledReason, /选择.*对白片段/);

const emptyDialogue = storyboardDialogueSearchContext(
  { id: 27 },
  { dialogue_id: "dlg_1", dialogue_asset_key: "dak_1", text: "  " },
);
assert.equal(emptyDialogue.enabled, false);
assert.match(emptyDialogue.disabledReason, /没有可检索/);

const validDialogue = storyboardDialogueSearchContext(
  { id: 27 },
  { dialogue_id: "dlg_1", dialogue_asset_key: "dak_1", text: "  防水   能力  " },
);
assert.deepEqual(validDialogue, {
  enabled: true,
  disabledReason: "",
  taskId: 27,
  dialogueId: "dlg_1",
  dialogueAssetKey: "dak_1",
  dialogueText: "防水 能力",
  key: "27:dak_1",
});
assert.notEqual(
  validDialogue.key,
  storyboardDialogueSearchContext(
    { id: 27 },
    { dialogue_id: "dlg_2", dialogue_asset_key: "dak_2", text: "另一个 Dialogue" },
  ).key,
  "switching Dialogue must produce a new context key so pending and rendered results can be discarded",
);

const normalized = normalizeMediaLibrarySearchResponse({
  search_id: "mls_1",
  retrieval_version: "dialogue_literal_v1",
  planner_degraded: true,
  result_count: 2,
  items: [
    {
      source: "media_library",
      candidate_id: "mla_1",
      candidate_kind: "original_video",
      asset_id: "mla_1",
      source_asset_id: "mla_1",
      source_clip_id: null,
      source_version: "hash_1",
      content_sha256: "hash_1",
      display_name: "原始视频 A",
      score: 0.7,
      score_reasons: ["对白原句命中"],
      allowed_actions: ["preview", "open_editor", "import_original"],
      matched_fragments: [
        { scheme: "dialogue", run_id: "mlar_1", fragment_id: "srt_2", start_ms: 4200, end_ms: 8800, dialogue_text: "防水能力" },
      ],
    },
    {
      source: "media_library",
      candidate_id: "mla_1",
      candidate_kind: "original_video",
      asset_id: "mla_1",
      source_asset_id: "mla_1",
      source_clip_id: null,
      source_version: "hash_1",
      content_sha256: "hash_1",
      display_name: "原始视频 A",
      score: 0.9,
      score_reasons: ["关键词覆盖"],
      allowed_actions: ["preview", "open_editor", "import_original"],
      matched_fragments: [
        { scheme: "dialogue", run_id: "mlar_1", fragment_id: "srt_1", start_ms: 1000, end_ms: 3000, dialogue_text: "产品防护" },
        { scheme: "visual_semantic", analysis_scheme: "visual_semantic", run_id: "mlar_v1", fragment_id: "scene_1", start_ms: 9000, end_ms: 15000, summary: "玻璃碗旁有绿色包装" },
        { scheme: "dialogue", run_id: "mlar_1", fragment_id: "broken", start_ms: 9000, end_ms: 9000 },
      ],
    },
  ],
});
assert.equal(normalized.searchId, "mls_1");
assert.equal(normalized.plannerDegraded, true);
assert.equal(normalized.items.length, 1, "multiple fragments for one source asset must render one original-video card");
assert.equal(normalized.items[0].score, 0.9);
assert.deepEqual(normalized.items[0].score_reasons, ["对白原句命中", "关键词覆盖"]);
assert.deepEqual(normalized.items[0].matched_fragments.map((item) => item.fragment_id), ["srt_1", "srt_2", "scene_1"]);
assert.equal(normalized.items[0].candidate_kind, "original_video");
assert.equal(normalized.items[0].source_asset_id, "mla_1");
assert.equal(normalized.items[0].source_clip_id, null);
assert.equal(normalized.items[0].content_sha256, "hash_1");
assert.equal(mediaLibraryFragmentKindLabel(normalized.items[0].matched_fragments[2]), "视觉命中");
assert.equal(mediaLibraryFragmentKindLabel(normalized.items[0].matched_fragments[0]), "对白命中");

const derived = normalizeMediaLibrarySearchResponse({
  search_id: "mls_clip",
  result_count: 1,
  items: [{
    source: "media_library",
    candidate_kind: "derived_clip",
    candidate_id: "mlc_1",
    asset_id: null,
    source_asset_id: "mla_1",
    source_clip_id: "mlc_1",
    source_version: "clip_hash_1",
    content_sha256: "clip_hash_1",
    display_name: "玻璃碗中的深色液体",
    preview_url: "/api/media-library/mla_1/clips/mlc_1/preview",
    duration_ms: 3_000,
    tags: ["玻璃碗", "深色液体"],
    candidate_start_ms: 0,
    candidate_end_ms: 3_000,
    source_start_ms: 4_000,
    source_end_ms: 7_000,
    time_basis: "candidate",
    allowed_actions: ["preview", "import_clip"],
    matched_fragments: [],
  }],
});
assert.equal(derived.items.length, 1);
assert.equal(derived.items[0].candidate_kind, "derived_clip");
assert.equal(derived.items[0].duration_ms, 3_000);
assert.deepEqual(derived.items[0].tags, ["玻璃碗", "深色液体"]);
assert.equal(candidateSupportsImport(derived.items[0]), true);
for (const badPatch of [
  { candidate_start_ms: 1 },
  { candidate_end_ms: 2_999 },
  { source_end_ms: 4_000 },
  { time_basis: "source" },
  { allowed_actions: ["preview", "open_editor", "import_clip"] },
  { matched_fragments: [{ analysis_scheme: "visual_semantic", fragment_id: "illegal", start_ms: 0, end_ms: 1 }] },
]) {
  const rejected = normalizeMediaLibrarySearchResponse({
    search_id: "mls_bad_clip",
    items: [{ ...derived.items[0], ...badPatch }],
  });
  assert.equal(rejected.items.length, 0, `malformed derived clip must fail closed: ${JSON.stringify(badPatch)}`);
}

const nestedRun = normalizeMediaLibrarySearchResponse({
  run: {
    search_id: "mls_nested",
    planner_degraded: true,
    items: [],
  },
});
assert.equal(nestedRun.searchId, "mls_nested");
assert.equal(nestedRun.plannerDegraded, true);

const globalCandidate = { allowed_actions: ["preview", "open_editor", "import_original"] };
const externalCandidate = { allowed_actions: ["preview", "import_whole"] };
assert.equal(candidateSupportsAction(globalCandidate, "open_editor"), true);
assert.equal(candidateSupportsAction(externalCandidate, "open_editor"), false);
assert.equal(candidateSupportsImport(globalCandidate), true);
assert.equal(candidateSupportsImport(externalCandidate), true);
assert.equal(candidateSupportsImport({ allowed_actions: ["preview"] }), false);
assert.equal(candidateSupportsImport({ allowed_actions: ["unknown"], import_supported: true }), false, "declared allowed_actions fail closed");
assert.equal(candidateSupportsImport({ import_supported: true }), true, "legacy candidates retain their current import contract");

assert.equal(assetSearchSourceLabel("local"), "当前 Task");
assert.equal(assetSearchSourceLabel("media_library"), "全局素材库");
assert.equal(ASSET_SEARCH_SOURCES.find((item) => item.key === "media_library")?.keyless, true);

const editorHash = buildMediaLibraryEditorHash({
  assetId: "mla_abc",
  startMs: 42100,
  endMs: 49800,
  targetTaskId: 27,
  dialogueAssetKey: "dak_1",
  searchId: "mls_1",
  matchedFragmentId: "srt_12",
  returnTo: "https://evil.example/",
  sourceSessionId: 99,
  sourcePath: "../../etc/passwd",
});
assert.equal(
  editorHash,
  "#/media-library/mla_abc/editor?start_ms=42100&end_ms=49800&target_task_id=27&dialogue_asset_key=dak_1&search_id=mls_1&matched_fragment_id=srt_12&return_to=storyboard_dialogue",
);
assert.equal(editorHash.includes("evil"), false);
assert.equal(editorHash.includes("source_session"), false);
assert.equal(editorHash.includes("sourcePath"), false);
assert.equal(buildMediaLibraryEditorHash({ assetId: "../unsafe", startMs: 1, endMs: 2 }), "");
assert.equal(buildMediaLibraryEditorHash({ assetId: "mla_abc", startMs: 900, endMs: 100 }), "#/media-library/mla_abc/editor");

assert.equal(formatSearchRange(42100, 49800), "00:42.100 – 00:49.800");
assert.match(MEDIA_LIBRARY_SEARCH_CAPABILITY_TEXT, /对白、关键词/);
assert.match(MEDIA_LIBRARY_SEARCH_CAPABILITY_TEXT, /优先精确率/);
assert.match(MEDIA_LIBRARY_SEARCH_CAPABILITY_TEXT, /已发布的视觉描述检索/);
assert.match(MEDIA_LIBRARY_SEARCH_CAPABILITY_TEXT, /不包含图像或视频向量相似度检索/);
assert.equal(MEDIA_LIBRARY_ZERO_RESULT_SUGGESTIONS.length, 4);
assert.ok(MEDIA_LIBRARY_ZERO_RESULT_SUGGESTIONS.some((item) => item.includes("缩短关键词")));
assert.ok(MEDIA_LIBRARY_ZERO_RESULT_SUGGESTIONS.some((item) => item.includes("移除可选画幅限制")));
assert.ok(MEDIA_LIBRARY_ZERO_RESULT_SUGGESTIONS.some((item) => item.includes("片段完整名称")));
assert.ok(MEDIA_LIBRARY_ZERO_RESULT_SUGGESTIONS.some((item) => item.includes("四帧视觉语义分析")));
assert.ok(MEDIA_LIBRARY_ZERO_RESULT_SUGGESTIONS.some((item) => item.includes("派生片段已经加入")));

const apiSource = readFileSync(new URL("../src/lib/api.ts", import.meta.url), "utf8");
for (const route of [
  "/dialogues/${encodeURIComponent(dialogueAssetKey)}/media-library-search/plan",
  "/dialogues/${encodeURIComponent(dialogueAssetKey)}/media-library-search/runs",
  "/media-library-search/runs/${encodeURIComponent(searchId)}",
  "/media-library-search/import",
]) {
  assert.ok(apiSource.includes(route), `canonical api.ts must expose ${route}`);
}

const dialogSource = readFileSync(new URL("../src/modules/koubo/KouboStoryBoard/components/MediaLibrarySearchDialog.jsx", import.meta.url), "utf8");
assert.match(dialogSource, /from "\.\.\/\.\.\/\.\.\/\.\.\/lib\/api\.ts"/);
assert.doesNotMatch(dialogSource, /\bfetch\s*\(/, "the new StoryBoard component must not create a module-level fetch helper");
assert.match(dialogSource, /candidate_kind === "derived_clip" \? "media_library_clip" : "media_library_original"/);
assert.match(dialogSource, /setImportedIds\(/, "success UI must be driven by a completed import response");
assert.match(dialogSource, /kbsp-ml-search-disabled-reason/);
assert.match(dialogSource, /role="status">\{context\(\)\.disabledReason\}/);
assert.match(dialogSource, /命中片段/);
assert.match(dialogSource, /剪切这个片段/);
assert.match(dialogSource, /mediaLibraryFragmentKindLabel/);
assert.match(dialogSource, /加入当前 Task（整条视频）/);
assert.match(dialogSource, /全局素材库 · 可复用片段/);
assert.match(dialogSource, /预览片段/);

const agentSource = readFileSync(new URL("../src/modules/koubo/UploadAssetLibrary/searchAgent/SearchAgentWorkspace.jsx", import.meta.url), "utf8");
assert.match(agentSource, /mediaLibraryFragmentKindLabel/);
assert.match(agentSource, /剪切首个命中范围/);
assert.match(agentSource, /以此范围打开剪辑/);
assert.match(agentSource, /candidate_kind \|\| "external"/);
assert.match(agentSource, /可复用派生片段/);

const editorSearchSource = readFileSync(new URL("../src/modules/mediaLibrary/editor/EditorSearchPanel.jsx", import.meta.url), "utf8");
assert.match(editorSearchSource, /视觉命中/);
assert.match(editorSearchSource, /以此范围打开剪辑/);
assert.match(editorSearchSource, /导入此片段/);

const storyboardSearchCss = readFileSync(new URL("../src/modules/koubo/KouboStoryBoard/styles/asset-panel.css", import.meta.url), "utf8");
assert.match(storyboardSearchCss, /\.kbsp-asset-upload-actions \.kbsp-ml-search-trigger:disabled/);
assert.match(storyboardSearchCss, /\.kbsp-ml-search-entry > small/);

console.log("media library search action/model contract: ok");
