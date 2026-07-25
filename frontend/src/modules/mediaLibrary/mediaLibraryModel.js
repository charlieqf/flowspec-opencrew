const text = (value) => String(value ?? "").trim();

const numberOrNull = (value) => {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
};

const countOrNull = (value) => {
  const parsed = numberOrNull(value);
  return parsed === null ? null : Math.max(0, Math.round(parsed));
};

export function mediaLibraryRouteFromHash(hashValue) {
  const hash = String(hashValue || globalThis.window?.location?.hash || "");
  const editorMatch = hash.match(/^#\/media-library\/([^/?#]+)\/editor(?:\?|$)/);
  if (editorMatch) return decodeMediaLibraryRoute("editor", editorMatch[1], hash);
  const detailMatch = hash.match(/^#\/media-library\/([^/?#]+)(?:\?|$)/);
  if (detailMatch) return decodeMediaLibraryRoute("detail", detailMatch[1], hash);
  if (/^#\/media-library(?:\?|$)/.test(hash)) {
    return { view: "list", assetId: "", navigation: {}, error: "" };
  }
  return {
    view: "invalid",
    assetId: "",
    navigation: {},
    error: "素材库路由无效，未知子路径不会作为素材详情打开。",
  };
}

function decodeMediaLibraryRoute(view, encodedAssetId, hash) {
  try {
    const assetId = decodeURIComponent(encodedAssetId);
    if (!/^[A-Za-z0-9._:-]{1,256}$/.test(assetId)) throw new Error("invalid_asset_id");
    return {
      view,
      assetId,
      navigation: view === "editor" ? editorNavigationFromHash(hash) : {},
      error: "",
    };
  } catch {
    return {
      view: "invalid",
      assetId: "",
      navigation: {},
      error: "素材 ID 无法安全解析，已停止打开该页面。",
    };
  }
}

export function editorNavigationFromHash(hashValue) {
  const hash = String(hashValue || globalThis.window?.location?.hash || "");
  const queryText = hash.includes("?") ? hash.slice(hash.indexOf("?") + 1) : "";
  const query = new URLSearchParams(queryText);
  const nonNegativeInteger = (key) => {
    const raw = query.get(key);
    if (!/^\d+$/.test(raw || "")) return null;
    const value = Number(raw);
    return Number.isSafeInteger(value) && value >= 0 ? value : null;
  };
  const positiveInteger = (key) => {
    const value = nonNegativeInteger(key);
    return value !== null && value > 0 ? value : null;
  };
  const opaque = (key) => {
    const value = text(query.get(key));
    return /^[A-Za-z0-9._:-]{1,256}$/.test(value) ? value : "";
  };
  const startMs = nonNegativeInteger("start_ms");
  const endMs = nonNegativeInteger("end_ms");
  const returnTo = query.get("return_to");
  return {
    ...(startMs !== null && endMs !== null && endMs > startMs
      ? { start_ms: startMs, end_ms: endMs }
      : {}),
    ...(positiveInteger("target_task_id")
      ? { target_task_id: positiveInteger("target_task_id") }
      : {}),
    ...(opaque("dialogue_asset_key")
      ? { dialogue_asset_key: opaque("dialogue_asset_key") }
      : {}),
    ...(opaque("search_id") ? { search_id: opaque("search_id") } : {}),
    ...(opaque("matched_fragment_id")
      ? { matched_fragment_id: opaque("matched_fragment_id") }
      : {}),
    ...(["storyboard_dialogue", "media_library_detail"].includes(returnTo)
      ? { return_to: returnTo }
      : {}),
  };
}

export function mediaLibraryQueryFromHash(hashValue) {
  const hash = String(hashValue || window.location.hash || "");
  const queryText = hash.includes("?") ? hash.slice(hash.indexOf("?") + 1) : "";
  const query = new URLSearchParams(queryText);
  const page = Math.max(1, Number(query.get("page") || 1));
  const pageSize = [20, 50, 100].includes(Number(query.get("page_size"))) ? Number(query.get("page_size")) : 20;
  return {
    q: query.get("q") || "",
    analysisStatus: query.get("analysis") || "all",
    subtitleMode: query.get("subtitle") || "all",
    duration: query.get("duration") || "all",
    tag: query.get("tag") || "all",
    updated: query.get("updated") || "all",
    orientation: query.get("orientation") || "all",
    sort: query.get("sort") || "updated_desc",
    includeArchived: query.get("archived") === "1",
    page,
    pageSize,
  };
}

export function mediaLibraryListHash(filters) {
  const query = new URLSearchParams();
  const add = (key, value, defaultValue = "all") => {
    if (value && value !== defaultValue) query.set(key, String(value));
  };
  add("q", text(filters.q), "");
  add("analysis", filters.analysisStatus);
  add("subtitle", filters.subtitleMode);
  add("duration", filters.duration);
  add("tag", filters.tag);
  add("updated", filters.updated);
  add("orientation", filters.orientation);
  add("sort", filters.sort, "updated_desc");
  if (filters.includeArchived) query.set("archived", "1");
  if (Number(filters.page || 1) > 1) query.set("page", String(filters.page));
  if (Number(filters.pageSize || 20) !== 20) query.set("page_size", String(filters.pageSize));
  const suffix = query.toString();
  return `#/media-library${suffix ? `?${suffix}` : ""}`;
}

export function mediaLibraryApiParams(filters) {
  return {
    q: text(filters.q),
    analysis_status: filters.analysisStatus === "all" ? "" : filters.analysisStatus,
    subtitle_mode: filters.subtitleMode === "all" ? "" : filters.subtitleMode,
    duration_range: filters.duration === "all" ? "" : filters.duration,
    tag: filters.tag === "all" ? "" : filters.tag,
    updated_range: filters.updated === "all" ? "" : filters.updated,
    orientation: filters.orientation === "all" ? "" : filters.orientation,
    include_archived: Boolean(filters.includeArchived),
    sort: filters.sort,
    page: Number(filters.page || 1),
    page_size: Number(filters.pageSize || 20),
  };
}

export function normalizeMediaAsset(raw = {}) {
  const analysis = raw.analysis_summary || raw.analysisSummary || {};
  const width = numberOrNull(raw.width);
  const height = numberOrNull(raw.height);
  return {
    assetId: text(raw.asset_id || raw.assetId || raw.id),
    sourceVersion: text(raw.source_version || raw.sourceVersion || raw.content_sha256 || raw.contentSha256),
    displayName: text(raw.display_name || raw.displayName || raw.original_filename || raw.originalFilename) || "未命名素材",
    originalFilename: text(raw.original_filename || raw.originalFilename),
    sessionId: numberOrNull(raw.session_id ?? raw.sessionId),
    sourceVideoPath: text(raw.source_video_path || raw.sourceVideoPath),
    thumbnailUrl: text(raw.thumbnail_url || raw.thumbnailUrl),
    previewUrl: text(raw.preview_url || raw.previewUrl),
    durationMs: numberOrNull(raw.duration_ms ?? raw.durationMs),
    width,
    height,
    format: text(raw.format).toUpperCase(),
    sizeBytes: numberOrNull(raw.size_bytes ?? raw.sizeBytes),
    language: text(raw.language),
    dialogueSummary: text(raw.dialogue_summary || raw.dialogueSummary),
    analysisStatus: text(raw.analysis_status || raw.analysisStatus) || "not_analyzed",
    analysisStatusReason: text(raw.analysis_status_reason || raw.analysisStatusReason),
    noAudio: text(raw.analysis_status_reason || raw.analysisStatusReason).toLowerCase() === "video_has_no_audio",
    visualSearchReady: Boolean(raw.visual_search_ready ?? raw.visualSearchReady),
    visualSearchReanalysisRequired: Boolean(raw.visual_search_reanalysis_required ?? raw.visualSearchReanalysisRequired),
    visualSearchState: text(raw.visual_search_state || raw.visualSearchState) || "unavailable",
    visualSearchFragmentCount: countOrNull(raw.visual_search_fragment_count ?? raw.visualSearchFragmentCount) || 0,
    visualSearchSchemaVersion: text(raw.visual_search_schema_version || raw.visualSearchSchemaVersion),
    uploadStatus: text(raw.upload_status || raw.uploadStatus) || "ready",
    subtitleMode: text(raw.subtitle_mode || raw.subtitleMode) || "unknown",
    analysisSummary: {
      dialogueCount: countOrNull(analysis.dialogue_fragment_count ?? analysis.srt_unit_count ?? analysis.dialogueCount),
      visualCount: countOrNull(analysis.visual_fragment_count ?? analysis.visual_unit_count ?? analysis.visualCount),
      compositeCount: countOrNull(analysis.composite_fragment_count ?? analysis.candidate_clip_count ?? analysis.compositeCount),
      keepCount: countOrNull(analysis.keep_count ?? analysis.keepCount),
      reviewCount: countOrNull(analysis.review_count ?? analysis.reviewCount),
      excludeCount: countOrNull(analysis.exclude_count ?? analysis.excludeCount),
      editingIssueCount: countOrNull(analysis.editing_issue_count ?? analysis.editingIssueCount),
      topEditingIssue: text(analysis.top_editing_issue || analysis.topEditingIssue),
      topEditingIssueCount: countOrNull(analysis.top_editing_issue_count ?? analysis.topEditingIssueCount),
    },
    // Keep historical empty values visible/removable. Silently filtering them
    // here would delete data when the user edits an unrelated tag.
    tags: Array.isArray(raw.tags) ? raw.tags.map((tag) => String(tag ?? "").trim()) : [],
    archived: Boolean(raw.archived),
    referencedByCount: countOrNull(raw.referenced_by_count ?? raw.referencedByCount) || 0,
    createdAt: raw.created_at ?? raw.createdAt ?? null,
    updatedAt: raw.updated_at ?? raw.updatedAt ?? null,
  };
}

export function mediaOrientation(asset) {
  if (Number(asset?.height || 0) > Number(asset?.width || 0)) return "portrait";
  return "landscape";
}

export function mediaLibraryAssetCanOpenEditor(asset) {
  return Boolean(
    asset
      && asset.uploadStatus === "ready"
      && !asset.archived
      && text(asset.sourceVersion),
  );
}

export function formatMediaDuration(durationMs) {
  const totalSeconds = Math.max(0, Math.round(Number(durationMs || 0) / 1000));
  if (!Number.isFinite(totalSeconds) || durationMs === null || durationMs === undefined) return "-";
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`
    : `${minutes}:${String(seconds).padStart(2, "0")}`;
}

export function formatMediaSize(bytes) {
  const value = Number(bytes);
  if (!Number.isFinite(value) || value < 0) return "-";
  if (value < 1024) return `${value} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MB`;
  return `${(value / 1024 ** 3).toFixed(2)} GB`;
}

export function formatMediaDate(value) {
  if (!value) return "-";
  const numeric = Number(value);
  const date = Number.isFinite(numeric) ? new Date(numeric) : new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  return date.toLocaleString([], { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
}

export function analysisStatusMeta(status) {
  const map = {
    not_analyzed: ["未分析", "neutral"],
    queued: ["等待中", "warning"],
    running: ["处理中", "info"],
    processing: ["处理中", "info"],
    blocked: ["等待授权", "warning"],
    partial: ["部分完成", "warning"],
    ready: ["已完成", "success"],
    stale: ["已过期", "warning"],
    failed: ["失败", "danger"],
  };
  const value = map[text(status).toLowerCase()] || map.not_analyzed;
  return { label: value[0], tone: value[1] };
}

export function audioStatusMeta(asset = {}) {
  return asset.noAudio
    ? { label: "无音轨", tone: "neutral" }
    : null;
}

export function visualSearchStatusMeta(asset = {}) {
  if (asset.visualSearchReady && asset.visualSearchState === "ready") {
    return { label: "可按画面检索", tone: "success" };
  }
  if (asset.visualSearchReanalysisRequired || asset.visualSearchState === "reanalysis_required") {
    return { label: "需重新分析后可按画面检索", tone: "warning" };
  }
  if (asset.visualSearchState === "index_pending") {
    return { label: "画面检索索引待发布", tone: "warning" };
  }
  return null;
}

export function hasMediaQualitySummary(summary = {}) {
  return [
    summary.keepCount,
    summary.reviewCount,
    summary.excludeCount,
    summary.editingIssueCount,
  ]
    .some((value) => value !== null && value !== undefined);
}

export function subtitleModeLabel(mode) {
  if (mode === "embedded") return "有字幕";
  if (mode === "none") return "无字幕";
  if (mode === "ocr_pending") return "待 OCR 识别";
  return "未识别";
}

export function editingIssueLabel(code) {
  const labels = {
    false_start: "口误重来",
    stutter: "磕巴",
    filler: "填充词",
    repetition: "重复",
    long_pause: "长停顿",
    blur: "模糊",
    camera_shake: "抖动",
    empty_shot: "空镜",
  };
  return labels[code] || text(code) || "-";
}
