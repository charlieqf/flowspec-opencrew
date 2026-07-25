import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

import {
  analysisStatusMeta,
  audioStatusMeta,
  hasMediaQualitySummary,
  normalizeMediaAsset,
  visualSearchStatusMeta,
} from "../src/modules/mediaLibrary/mediaLibraryModel.js";

const frontendRoot = fileURLToPath(new URL("..", import.meta.url));

const statusLabels = {
  not_analyzed: "未分析",
  queued: "等待中",
  running: "处理中",
  processing: "处理中",
  blocked: "等待授权",
  partial: "部分完成",
  ready: "已完成",
  stale: "已过期",
  failed: "失败",
};
for (const [status, label] of Object.entries(statusLabels)) {
  assert.equal(analysisStatusMeta(status).label, label, status);
}
assert.equal(analysisStatusMeta("BLOCKED").label, "等待授权");
assert.equal(
  analysisStatusMeta("blocked", "video_has_no_audio").label,
  "等待授权",
);

const withoutQuality = normalizeMediaAsset({
  asset_id: "without-quality",
  analysis_status: "blocked",
  analysis_summary: { visual_fragment_count: 1 },
});
assert.equal(hasMediaQualitySummary(withoutQuality.analysisSummary), false);

const silentAsset = normalizeMediaAsset({
  asset_id: "silent-video",
  analysis_status: "blocked",
  analysis_status_reason: "video_has_no_audio",
});
assert.equal(silentAsset.analysisStatusReason, "video_has_no_audio");
assert.equal(analysisStatusMeta(silentAsset.analysisStatus).label, "等待授权");
assert.equal(audioStatusMeta(silentAsset).label, "无音轨");

const silentVisualReady = normalizeMediaAsset({
  asset_id: "silent-visual-ready",
  analysis_status: "partial",
  analysis_status_reason: "video_has_no_audio",
  visual_search_ready: true,
  visual_search_state: "ready",
  visual_search_fragment_count: 2,
  visual_search_schema_version: "media_library_visual_semantic_v2",
});
assert.equal(analysisStatusMeta(silentVisualReady.analysisStatus).label, "部分完成", "无音轨不得覆盖真实聚合状态");
assert.equal(audioStatusMeta(silentVisualReady).label, "无音轨");
assert.equal(visualSearchStatusMeta(silentVisualReady).label, "可按画面检索");

const legacyVisual = normalizeMediaAsset({
  asset_id: "legacy-visual",
  visual_search_reanalysis_required: true,
  visual_search_state: "reanalysis_required",
  visual_search_schema_version: "media_library_visual_semantic_v1",
});
assert.equal(visualSearchStatusMeta(legacyVisual).label, "需重新分析后可按画面检索");

const withQuality = normalizeMediaAsset({
  asset_id: "with-quality",
  analysis_status: "ready",
  analysis_summary: {
    keep_count: 3,
    review_count: 1,
    exclude_count: 0,
  },
});
assert.equal(hasMediaQualitySummary(withQuality.analysisSummary), true);
assert.deepEqual(
  {
    keep: withQuality.analysisSummary.keepCount,
    review: withQuality.analysisSummary.reviewCount,
    exclude: withQuality.analysisSummary.excludeCount,
  },
  { keep: 3, review: 1, exclude: 0 },
);

const editingIssueOnly = normalizeMediaAsset({
  asset_id: "editing-issue-only",
  analysis_summary: {
    editing_issue_count: 2,
    top_editing_issue: "long_pause",
  },
});
assert.equal(hasMediaQualitySummary(editingIssueOnly.analysisSummary), true);

const [tableSource, filterSource, qualitySource] = await Promise.all([
  readFile(`${frontendRoot}/src/modules/mediaLibrary/components/MediaLibraryTable.jsx`, "utf8"),
  readFile(`${frontendRoot}/src/modules/mediaLibrary/components/MediaLibraryFilters.jsx`, "utf8"),
  readFile(`${frontendRoot}/src/modules/mediaLibrary/components/MediaLibraryAssetPrimitives.jsx`, "utf8"),
]);
assert.match(tableSource, />分析状态</);
assert.match(tableSource, /audioStatusMeta/);
assert.match(tableSource, /visualSearchStatusMeta/);
assert.doesNotMatch(tableSource, />OpenCut 状态</);
for (const value of ["processing", "blocked", "partial", "ready", "stale", "failed"]) {
  assert.match(filterSource, new RegExp(`<option value="${value}">`), value);
}
assert.match(qualitySource, /暂无质量数据/);
assert.doesNotMatch(qualitySource, /keepCount \?\? "-"/);
assert.doesNotMatch(qualitySource, /reviewCount \?\? "-"/);
assert.doesNotMatch(qualitySource, /excludeCount \?\? "-"/);
assert.match(qualitySource, /Number\(summary\(\)\.excludeCount\) > 0/);

console.log("media library list contract: ok");
