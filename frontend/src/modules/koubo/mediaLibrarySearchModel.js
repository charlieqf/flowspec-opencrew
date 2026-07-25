export const MEDIA_LIBRARY_SEARCH_CAPABILITY_TEXT = "支持对白、关键词和已发布的视觉描述检索；当前优先精确率。暂不包含图像或视频向量相似度检索。";

export const MEDIA_LIBRARY_ZERO_RESULT_SUGGESTIONS = Object.freeze([
  "缩短关键词或输入片段完整名称",
  "确认无声原视频已经完成四帧视觉语义分析，而非只有画面结构或历史单帧结果",
  "确认派生片段已经加入素材检索",
  "移除可选画幅限制；规划器降级时输入更明确的物体、场景或片段名称",
]);

export const ASSET_SEARCH_SOURCES = Object.freeze([
  { key: "local", label: "当前 Task", keyless: true },
  { key: "media_library", label: "全局素材库", keyless: true },
  { key: "pexels", label: "Pexels", keyless: false },
  { key: "pixabay", label: "Pixabay", keyless: false },
  { key: "wikimedia", label: "Wikimedia", keyless: true },
  { key: "unsplash", label: "Unsplash", keyless: false },
]);

const KNOWN_SEARCH_ACTIONS = new Set([
  "preview",
  "open_editor",
  "import",
  "import_original",
  "import_clip",
  "import_whole",
  "reuse_local",
]);

const IMPORT_ACTIONS = new Set(["import", "import_original", "import_clip", "import_whole", "reuse_local"]);

function cleanText(value) {
  return String(value ?? "").replace(/\s+/g, " ").trim();
}

function safeOpaqueId(value) {
  const text = cleanText(value);
  return /^[A-Za-z0-9._:-]{1,256}$/.test(text) ? text : "";
}

function positiveInteger(value) {
  const number = Number(value);
  return Number.isSafeInteger(number) && number > 0 ? number : 0;
}

function nonNegativeInteger(value) {
  const number = Number(value);
  return Number.isSafeInteger(number) && number >= 0 ? number : null;
}

function actionSet(candidate) {
  const actions = Array.isArray(candidate?.allowed_actions) ? candidate.allowed_actions : [];
  return new Set(actions.map(cleanText).filter((action) => KNOWN_SEARCH_ACTIONS.has(action)));
}

export function assetSearchSourceLabel(source) {
  const key = cleanText(source).toLowerCase();
  return ASSET_SEARCH_SOURCES.find((item) => item.key === key)?.label || cleanText(source) || "未知来源";
}

export function candidateSupportsAction(candidate, action) {
  return actionSet(candidate).has(cleanText(action));
}

export function candidateSupportsImport(candidate) {
  const actions = actionSet(candidate);
  if (Array.isArray(candidate?.allowed_actions)) return Array.from(IMPORT_ACTIONS).some((action) => actions.has(action));
  return candidate?.import_supported !== false && candidate?.import_supported != null;
}

export function storyboardDialogueSearchContext(task, dialogue) {
  const taskId = positiveInteger(task?.id);
  const dialogueId = cleanText(dialogue?.dialogue_id);
  const dialogueAssetKey = cleanText(dialogue?.dialogue_asset_key);
  const dialogueText = cleanText(dialogue?.text);
  let disabledReason = "";
  if (!taskId) disabledReason = "当前 Task 尚未加载。";
  else if (!dialogueId) disabledReason = "请先选择一个对白片段。";
  else if (!dialogueAssetKey) disabledReason = "当前 Dialogue 缺少稳定素材标识，请保存或刷新 StoryBoard 后重试。";
  else if (!dialogueText) disabledReason = "当前 Dialogue 没有可检索的对白文本。";
  return {
    enabled: !disabledReason,
    disabledReason,
    taskId,
    dialogueId,
    dialogueAssetKey,
    dialogueText,
    key: taskId && dialogueAssetKey ? `${taskId}:${dialogueAssetKey}` : "",
  };
}

function fragmentKey(fragment) {
  return [
    cleanText(fragment?.analysis_scheme || fragment?.scheme),
    cleanText(fragment?.run_id),
    cleanText(fragment?.fragment_id),
    nonNegativeInteger(fragment?.start_ms) ?? "",
    nonNegativeInteger(fragment?.end_ms) ?? "",
  ].join(":");
}

function normalizeFragment(fragment) {
  const startMs = nonNegativeInteger(fragment?.start_ms);
  const endMs = nonNegativeInteger(fragment?.end_ms);
  if (startMs == null || endMs == null || endMs <= startMs) return null;
  return {
    ...fragment,
    scheme: cleanText(fragment?.analysis_scheme || fragment?.scheme) || "dialogue",
    analysis_scheme: cleanText(fragment?.analysis_scheme || fragment?.scheme) || "dialogue",
    run_id: cleanText(fragment?.run_id),
    fragment_id: cleanText(fragment?.fragment_id),
    start_ms: startMs,
    end_ms: endMs,
    dialogue_text: cleanText(fragment?.dialogue_text),
    summary: cleanText(fragment?.summary),
    keyframe_url: cleanText(fragment?.keyframe_url),
  };
}

function candidateIdentity(candidate) {
  return [
    cleanText(candidate?.source || candidate?.provider) || "media_library",
    cleanText(candidate?.candidate_kind) || "original_video",
    cleanText(candidate?.candidate_id || candidate?.asset_id),
  ].join(":");
}

function normalizeCandidate(candidate) {
  const candidateId = cleanText(candidate?.candidate_id || candidate?.asset_id);
  if (!candidateId) return null;
  const candidateKind = cleanText(candidate?.candidate_kind) || "original_video";
  if (!["original_video", "derived_clip"].includes(candidateKind)) return null;
  const assetId = cleanText(candidate?.asset_id);
  const sourceAssetId = cleanText(candidate?.source_asset_id || candidate?.asset_id);
  const sourceClipId = cleanText(candidate?.source_clip_id);
  if (candidateKind === "original_video" && (!assetId || assetId !== candidateId || sourceAssetId !== assetId || sourceClipId)) return null;
  if (candidateKind === "derived_clip" && (assetId || !sourceAssetId || sourceClipId !== candidateId)) return null;
  const durationMs = nonNegativeInteger(candidate?.duration_ms);
  const candidateStartMs = nonNegativeInteger(candidate?.candidate_start_ms);
  const candidateEndMs = nonNegativeInteger(candidate?.candidate_end_ms);
  const sourceStartMs = nonNegativeInteger(candidate?.source_start_ms);
  const sourceEndMs = nonNegativeInteger(candidate?.source_end_ms);
  const timeBasis = cleanText(candidate?.time_basis);
  const declaredActions = Array.isArray(candidate?.allowed_actions)
    ? candidate.allowed_actions.map(cleanText)
    : [];
  if (candidateKind === "original_video" && declaredActions.join(",") !== "preview,open_editor,import_original") return null;
  if (candidateKind === "derived_clip" && (
    !durationMs
    || candidateStartMs !== 0
    || candidateEndMs !== durationMs
    || sourceStartMs == null
    || sourceEndMs == null
    || sourceEndMs <= sourceStartMs
    || timeBasis !== "candidate"
    || declaredActions.join(",") !== "preview,import_clip"
    || (Array.isArray(candidate?.matched_fragments) && candidate.matched_fragments.length)
  )) return null;
  const fragments = [];
  const seenFragments = new Set();
  for (const rawFragment of Array.isArray(candidate?.matched_fragments) ? candidate.matched_fragments : []) {
    const fragment = normalizeFragment(rawFragment);
    const key = fragment ? fragmentKey(fragment) : "";
    if (!fragment || seenFragments.has(key)) continue;
    seenFragments.add(key);
    fragments.push(fragment);
  }
  return {
    ...candidate,
    source: cleanText(candidate?.source || candidate?.provider) || "media_library",
    candidate_kind: candidateKind,
    candidate_id: candidateId,
    asset_id: assetId || null,
    source_asset_id: sourceAssetId,
    source_clip_id: sourceClipId || null,
    source_version: cleanText(candidate?.source_version),
    content_sha256: cleanText(candidate?.content_sha256 || candidate?.source_version),
    display_name: cleanText(candidate?.display_name || candidate?.title) || candidateId,
    preview_url: cleanText(candidate?.preview_url),
    thumbnail_url: cleanText(candidate?.thumbnail_url),
    duration_ms: durationMs,
    tags: Array.from(new Set((Array.isArray(candidate?.tags) ? candidate.tags : []).map(cleanText).filter(Boolean))),
    candidate_start_ms: candidateStartMs,
    candidate_end_ms: candidateEndMs,
    source_start_ms: sourceStartMs,
    source_end_ms: sourceEndMs,
    time_basis: timeBasis,
    orientation: cleanText(candidate?.orientation) || "unknown",
    score: Number.isFinite(Number(candidate?.score)) ? Math.max(0, Math.min(1, Number(candidate.score))) : null,
    score_reasons: Array.from(new Set((Array.isArray(candidate?.score_reasons) ? candidate.score_reasons : []).map(cleanText).filter(Boolean))),
    allowed_actions: Array.from(actionSet(candidate)),
    matched_fragments: fragments,
  };
}

export function normalizeMediaLibrarySearchResponse(payload) {
  const rawItems = Array.isArray(payload?.items)
    ? payload.items
    : Array.isArray(payload?.run?.items)
      ? payload.run.items
      : [];
  const grouped = new Map();
  for (const rawCandidate of rawItems) {
    const candidate = normalizeCandidate(rawCandidate);
    if (!candidate) continue;
    const key = candidateIdentity(candidate);
    const current = grouped.get(key);
    if (!current) {
      grouped.set(key, candidate);
      continue;
    }
    const fragmentMap = new Map(current.matched_fragments.map((fragment) => [fragmentKey(fragment), fragment]));
    for (const fragment of candidate.matched_fragments) fragmentMap.set(fragmentKey(fragment), fragment);
    grouped.set(key, {
      ...current,
      ...candidate,
      score: Math.max(Number(current.score || 0), Number(candidate.score || 0)),
      score_reasons: Array.from(new Set([...current.score_reasons, ...candidate.score_reasons])),
      allowed_actions: Array.from(new Set([...current.allowed_actions, ...candidate.allowed_actions])),
      matched_fragments: Array.from(fragmentMap.values()).sort((a, b) => a.start_ms - b.start_ms || a.end_ms - b.end_ms || a.fragment_id.localeCompare(b.fragment_id)),
    });
  }
  const items = Array.from(grouped.values());
  const plannerDegraded = Boolean(payload?.planner_degraded ?? payload?.run?.planner_degraded);
  return {
    searchId: cleanText(payload?.search_id || payload?.run?.search_id),
    retrievalVersion: cleanText(payload?.retrieval_version || payload?.run?.retrieval_version),
    plannerDegraded,
    resultCount: Number.isSafeInteger(Number(payload?.result_count)) ? Number(payload.result_count) : items.length,
    items,
  };
}

export function formatSearchRange(startMs, endMs) {
  const format = (value) => {
    const totalMs = Math.max(0, nonNegativeInteger(value) ?? 0);
    const hours = Math.floor(totalMs / 3_600_000);
    const minutes = Math.floor((totalMs % 3_600_000) / 60_000);
    const seconds = Math.floor((totalMs % 60_000) / 1000);
    const milliseconds = totalMs % 1000;
    const head = hours ? `${String(hours).padStart(2, "0")}:` : "";
    return `${head}${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}.${String(milliseconds).padStart(3, "0")}`;
  };
  return `${format(startMs)} – ${format(endMs)}`;
}

export function mediaLibraryFragmentKindLabel(fragment) {
  const scheme = cleanText(fragment?.analysis_scheme || fragment?.analysisScheme || fragment?.scheme);
  if (scheme === "visual_semantic") return "视觉命中";
  if (scheme === "dialogue") return "对白命中";
  return "片段命中";
}

export function buildMediaLibraryEditorHash(input) {
  const assetId = safeOpaqueId(input?.assetId);
  if (!assetId) return "";
  const params = new URLSearchParams();
  const startMs = nonNegativeInteger(input?.startMs);
  const endMs = nonNegativeInteger(input?.endMs);
  if (startMs != null && endMs != null && endMs > startMs) {
    params.set("start_ms", String(startMs));
    params.set("end_ms", String(endMs));
  }
  const targetTaskId = positiveInteger(input?.targetTaskId);
  if (targetTaskId) params.set("target_task_id", String(targetTaskId));
  const controlledTextParams = [
    ["dialogue_asset_key", input?.dialogueAssetKey],
    ["search_id", input?.searchId],
    ["matched_fragment_id", input?.matchedFragmentId],
  ];
  for (const [key, rawValue] of controlledTextParams) {
    const value = safeOpaqueId(rawValue);
    if (value) params.set(key, value);
  }
  if (targetTaskId && safeOpaqueId(input?.dialogueAssetKey)) params.set("return_to", "storyboard_dialogue");
  const query = params.toString();
  return `#/media-library/${encodeURIComponent(assetId)}/editor${query ? `?${query}` : ""}`;
}
