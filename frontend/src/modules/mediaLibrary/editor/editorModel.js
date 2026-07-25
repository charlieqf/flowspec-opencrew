const SCHEMES = Object.freeze(["dialogue", "visual", "composite"]);
const RETURN_DESTINATIONS = new Set(["storyboard_dialogue", "media_library_detail"]);
const SEARCH_SOURCES = new Set(["media_library", "external"]);
const RUN_PUBLIC_KEYS = new Set([
  "analysis_run_id",
  "scheme",
  "status",
  "schema_version",
  "prompt_version",
  "model_alias",
  "model_version",
  "sampling_strategy",
]);

const text = (value) => String(value ?? "").trim();

const integerOrNull = (value) => {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) ? parsed : null;
};

const positiveIntegerOrNull = (value) => {
  const parsed = integerOrNull(value);
  return parsed !== null && parsed > 0 ? parsed : null;
};

const opaqueId = (value) => {
  const normalized = text(value);
  return /^[A-Za-z0-9._:-]{1,256}$/.test(normalized) ? normalized : "";
};

const safeExternalUrl = (value) => {
  const normalized = text(value);
  if (!normalized) return "";
  try {
    const parsed = new URL(normalized);
    return ["http:", "https:"].includes(parsed.protocol) ? parsed.toString() : "";
  } catch {
    return "";
  }
};

function publicRun(raw, fallbackScheme) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const run = {};
  for (const key of RUN_PUBLIC_KEYS) {
    const value = raw[key];
    if (value !== undefined && value !== null && value !== "") run[key] = value;
  }
  run.analysis_run_id = opaqueId(raw.analysis_run_id || raw.run_id);
  run.scheme = text(raw.scheme) || fallbackScheme;
  run.status = text(raw.status) || "not_analyzed";
  return run;
}

function runForFragment(runs, scheme, runId) {
  const candidates = scheme === "visual"
    ? [runs.visual_semantic, runs.visual_structure]
    : [runs[scheme]];
  return candidates.find((run) => run && (!runId || run.analysis_run_id === runId)) || null;
}

function normalizeFragment(raw, scheme, index, durationMs, runs) {
  const fragmentId = opaqueId(
    raw?.fragment_id
      || raw?.id
      || raw?.srt_unit_id
      || raw?.visual_unit_id
      || raw?.candidate_clip_id,
  );
  const runId = opaqueId(raw?.analysis_run_id || raw?.run_id);
  const startMs = integerOrNull(raw?.start_ms);
  const endMs = integerOrNull(raw?.end_ms);
  const contractErrors = [];
  if (!fragmentId) contractErrors.push(`${scheme}[${index}] 缺少合法 fragment_id`);
  if (startMs === null || startMs < 0) contractErrors.push(`${scheme}[${index}] start_ms 必须为非负整数`);
  if (endMs === null || startMs === null || endMs <= startMs) contractErrors.push(`${scheme}[${index}] end_ms 必须大于 start_ms`);
  if (endMs !== null && endMs > durationMs) contractErrors.push(`${scheme}[${index}] end_ms 超出视频时长`);
  const run = runForFragment(runs, scheme, runId);
  const effectiveRunId = runId || run?.analysis_run_id || "";
  const status = text(raw?.status || run?.status) || "ready";
  const stale = Boolean(raw?.stale) || status === "stale";
  return {
    fragment: {
      scheme,
      fragmentId,
      runId: effectiveRunId,
      startMs: startMs ?? 0,
      endMs: endMs ?? 0,
      stale,
      status,
      label: text(
        raw?.display_name
          || raw?.title
          || raw?.dialogue_text
          || raw?.text
          || raw?.summary
          || raw?.visual_summary,
      ) || ({
        dialogue: "对白",
        visual: "画面",
        composite: "综合",
      }[scheme] || "片段") + ` ${index + 1}`,
      dialogueText: text(raw?.dialogue_text || raw?.text),
      summary: text(raw?.summary || raw?.visual_summary || raw?.reason),
      keyframeUrl: text(raw?.keyframe_url || raw?.thumbnail_url),
      editDecision: text(raw?.edit_decision),
      evidenceRefs: Array.isArray(raw?.evidence_refs)
        ? raw.evidence_refs.map(text).filter(Boolean)
        : [],
    },
    contractErrors,
  };
}

function normalizeClip(raw, index, durationMs) {
  const clipId = opaqueId(raw?.clip_id || raw?.id);
  const startMs = integerOrNull(raw?.start_ms);
  const endMs = integerOrNull(raw?.end_ms);
  const errors = [];
  if (!clipId) errors.push(`clips[${index}] 缺少合法 clip_id`);
  if (startMs === null || startMs < 0) errors.push(`clips[${index}] start_ms 非法`);
  if (endMs === null || startMs === null || endMs <= startMs || endMs > durationMs) {
    errors.push(`clips[${index}] end_ms 非法`);
  }
  return {
    clip: {
      clipId,
      displayName: text(raw?.display_name) || `派生片段 ${index + 1}`,
      tags: Array.isArray(raw?.tags) ? raw.tags.map(text).filter(Boolean) : [],
      searchEligible: Boolean(raw?.search_eligible),
      searchEnabledAt: integerOrNull(raw?.search_enabled_at),
      searchUpdatedAt: integerOrNull(raw?.search_updated_at),
      startMs: startMs ?? 0,
      endMs: endMs ?? 0,
      durationMs: integerOrNull(raw?.duration_ms) ?? Math.max(0, (endMs ?? 0) - (startMs ?? 0)),
      previewUrl: text(raw?.preview_url),
      downloadUrl: text(raw?.download_url),
      contentSha256: text(raw?.content_sha256),
      sizeBytes: integerOrNull(raw?.size_bytes),
      createdAt: raw?.created_at ?? null,
      sourceScheme: text(raw?.source_scheme),
      sourceFragmentId: opaqueId(raw?.source_fragment_id),
    },
    errors,
  };
}

export function normalizeClipItems(raw, durationMs) {
  const items = Array.isArray(raw?.items)
    ? raw.items
    : Array.isArray(raw?.clips)
      ? raw.clips
      : null;
  if (!items) return { valid: false, errors: ["GET clips 响应必须包含 items 数组"], clips: [] };
  const errors = [];
  const clips = items.map((entry, index) => {
    const normalized = normalizeClip(entry, index, durationMs);
    errors.push(...normalized.errors);
    return normalized.clip;
  });
  return { valid: errors.length === 0, errors, clips };
}

export function normalizeEditorPayload(raw, expectedAssetId = "") {
  const contractErrors = [];
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return { valid: false, contractErrors: ["GET editor 响应必须为对象"] };
  }
  const item = raw.item;
  if (!item || typeof item !== "object" || Array.isArray(item)) {
    return { valid: false, contractErrors: ["GET editor 缺少 item 素材 DTO"] };
  }
  const assetId = opaqueId(item.asset_id || item.id);
  const sourceVersion = text(raw.source_version);
  const durationMs = positiveIntegerOrNull(item.duration_ms);
  const previewUrl = text(item.preview_url);
  if (!assetId) contractErrors.push("item.asset_id 缺失或格式非法");
  if (expectedAssetId && assetId !== expectedAssetId) contractErrors.push("GET editor 返回了错误的 asset_id");
  if (!sourceVersion) contractErrors.push("source_version 缺失，不能安全剪切");
  if (durationMs === null) contractErrors.push("item.duration_ms 必须为正整数毫秒");
  if (!previewUrl) contractErrors.push("item.preview_url 缺失，不能预览原视频");
  if (text(item.upload_status) && text(item.upload_status) !== "ready") {
    contractErrors.push("素材尚未 ready，不能打开剪辑页");
  }
  if (Boolean(item.archived)) contractErrors.push("归档素材不能打开剪辑页");

  const runInput = raw.runs && typeof raw.runs === "object" && !Array.isArray(raw.runs) ? raw.runs : {};
  const runs = {
    dialogue: publicRun(runInput.dialogue, "dialogue"),
    visual_structure: publicRun(runInput.visual_structure, "visual_structure"),
    visual_semantic: publicRun(runInput.visual_semantic, "visual_semantic"),
    composite: publicRun(runInput.composite, "composite"),
  };

  const fragmentInput = raw.fragments && typeof raw.fragments === "object" && !Array.isArray(raw.fragments)
    ? raw.fragments
    : null;
  if (!fragmentInput) contractErrors.push("fragments 必须包含 dialogue/visual/composite 三个数组");
  const fragments = {};
  for (const scheme of SCHEMES) {
    const items = fragmentInput?.[scheme];
    if (!Array.isArray(items)) {
      contractErrors.push(`fragments.${scheme} 必须为数组`);
      fragments[scheme] = [];
      continue;
    }
    const seen = new Set();
    fragments[scheme] = items.map((entry, index) => {
      const normalized = normalizeFragment(entry, scheme, index, durationMs || 0, runs);
      contractErrors.push(...normalized.contractErrors);
      if (seen.has(normalized.fragment.fragmentId)) {
        contractErrors.push(`fragments.${scheme} 存在重复 fragment_id: ${normalized.fragment.fragmentId}`);
      }
      seen.add(normalized.fragment.fragmentId);
      return normalized.fragment;
    }).sort((left, right) => left.startMs - right.startMs
      || left.endMs - right.endMs
      || left.fragmentId.localeCompare(right.fragmentId));
  }

  if (!Array.isArray(raw.clips)) contractErrors.push("clips 必须为数组");
  const clips = (Array.isArray(raw.clips) ? raw.clips : []).map((entry, index) => {
    const normalized = normalizeClip(entry, index, durationMs || 0);
    contractErrors.push(...normalized.errors);
    return normalized.clip;
  });

  if (!Array.isArray(raw.import_targets)) contractErrors.push("import_targets 必须为数组");
  const importTargets = (Array.isArray(raw.import_targets) ? raw.import_targets : []).map((entry, index) => {
    const taskId = positiveIntegerOrNull(entry?.task_id);
    if (!taskId) contractErrors.push(`import_targets[${index}].task_id 必须为正整数`);
    return {
      taskId: taskId || 0,
      sessionId: positiveIntegerOrNull(entry?.session_id),
      title: text(entry?.title) || `StoryBoard Task ${taskId || "?"}`,
      workflowMode: text(entry?.workflow_mode),
      updatedAt: entry?.updated_at ?? null,
    };
  });

  const navigation = normalizeNavigationContext(raw.navigation_context, durationMs || 0, contractErrors);
  const actualFragmentCount = SCHEMES.reduce((total, scheme) => total + fragments[scheme].length, 0);
  const declaredCount = integerOrNull(raw.fragment_count ?? raw.capacity?.fragment_count);
  if (declaredCount !== null && declaredCount !== actualFragmentCount) {
    contractErrors.push(`fragment_count=${declaredCount} 与实际 ${actualFragmentCount} 不一致，拒绝静默截断`);
  }

  return {
    valid: contractErrors.length === 0,
    contractErrors,
    asset: {
      assetId,
      sourceVersion,
      displayName: text(item.display_name || item.original_filename) || "未命名素材",
      originalFilename: text(item.original_filename),
      previewUrl,
      thumbnailUrl: text(item.thumbnail_url),
      durationMs: durationMs || 0,
      width: positiveIntegerOrNull(item.width),
      height: positiveIntegerOrNull(item.height),
      uploadStatus: text(item.upload_status) || "ready",
      archived: Boolean(item.archived),
    },
    sourceVersion,
    fragments,
    runs,
    clips,
    importTargets,
    navigation,
    capacity: {
      fragmentCount: actualFragmentCount,
      serializedBytes: integerOrNull(raw.serialized_bytes ?? raw.capacity?.serialized_bytes),
    },
  };
}

function normalizeNavigationContext(raw, durationMs, contractErrors) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    contractErrors.push("navigation_context 必须为对象");
    return {};
  }
  const startMs = integerOrNull(raw.start_ms);
  const endMs = integerOrNull(raw.end_ms);
  const targetTaskId = positiveIntegerOrNull(raw.target_task_id);
  const returnTo = text(raw.return_to);
  const navigation = {
    targetValid: Boolean(raw.target_valid),
    dialogueValid: Boolean(raw.dialogue_valid),
  };
  if (startMs !== null || endMs !== null) {
    if (startMs === null || endMs === null || startMs < 0 || endMs <= startMs || endMs > durationMs) {
      contractErrors.push("navigation_context 的 start_ms/end_ms 未被服务端正确钳制");
    } else {
      navigation.startMs = startMs;
      navigation.endMs = endMs;
    }
  }
  if (targetTaskId) navigation.targetTaskId = targetTaskId;
  const dialogueAssetKey = opaqueId(raw.dialogue_asset_key);
  const searchId = opaqueId(raw.search_id);
  const matchedFragmentId = opaqueId(raw.matched_fragment_id);
  if (dialogueAssetKey) navigation.dialogueAssetKey = dialogueAssetKey;
  if (searchId) navigation.searchId = searchId;
  if (matchedFragmentId) navigation.matchedFragmentId = matchedFragmentId;
  if (returnTo) {
    if (!RETURN_DESTINATIONS.has(returnTo)) contractErrors.push("navigation_context.return_to 不在允许列表");
    else navigation.returnTo = returnTo;
  }
  return navigation;
}

export function selectionFromEditorNavigation(editor) {
  const navigation = editor?.navigation || {};
  const sourceSearchId = navigation.searchId || "";
  const sourceDialogueAssetKey = sourceSearchId
    ? navigation.dialogueAssetKey || ""
    : "";
  const allFragments = SCHEMES.flatMap((scheme) => editor?.fragments?.[scheme] || []);
  const matched = navigation.matchedFragmentId
    ? allFragments.find((fragment) => fragment.fragmentId === navigation.matchedFragmentId)
    : null;
  if (matched && !matched.stale) {
    return {
      ...selectionFromFragment(matched),
      sourceSearchId,
      sourceDialogueAssetKey,
    };
  }
  if (Number.isInteger(navigation.startMs) && Number.isInteger(navigation.endMs)) {
    return {
      startMs: navigation.startMs,
      endMs: navigation.endMs,
      sourceScheme: "",
      sourceFragmentId: "",
      sourceRunId: "",
      sourceSearchId,
      sourceDialogueAssetKey,
      manualOverride: true,
    };
  }
  const durationMs = Number(editor?.asset?.durationMs || 0);
  return {
    startMs: 0,
    endMs: Math.min(durationMs, Math.max(250, Math.min(5000, durationMs))),
    sourceScheme: "",
    sourceFragmentId: "",
    sourceRunId: "",
    sourceSearchId,
    sourceDialogueAssetKey,
    manualOverride: true,
  };
}

export function selectionFromFragment(fragment) {
  if (!fragment || fragment.stale) return null;
  return {
    startMs: fragment.startMs,
    endMs: fragment.endMs,
    sourceScheme: fragment.scheme,
    sourceFragmentId: fragment.fragmentId,
    sourceRunId: fragment.runId,
    sourceSearchId: "",
    sourceDialogueAssetKey: "",
    manualOverride: false,
  };
}

export function convertStaleFragmentToManual(fragment) {
  if (!fragment?.stale) return null;
  return {
    startMs: fragment.startMs,
    endMs: fragment.endMs,
    sourceScheme: "",
    sourceFragmentId: "",
    sourceRunId: "",
    sourceSearchId: "",
    sourceDialogueAssetKey: "",
    manualOverride: true,
  };
}

export function normalizeManualSelection(selection, startMs, endMs, durationMs) {
  const start = integerOrNull(startMs);
  const end = integerOrNull(endMs);
  if (start === null || end === null || start < 0 || end <= start || end > durationMs) return null;
  return {
    ...selection,
    startMs: start,
    endMs: end,
    sourceScheme: "",
    sourceFragmentId: "",
    sourceRunId: "",
    manualOverride: true,
  };
}

export function createClipJobInput(editor, selection, displayName, idempotencyKey) {
  const name = text(displayName);
  if (!editor?.valid || !name || !selection || !opaqueId(idempotencyKey)) return null;
  const durationMs = selection.endMs - selection.startMs;
  if (!Number.isSafeInteger(durationMs) || durationMs < 250 || durationMs > 1_800_000) return null;
  const input = {
    source_version: editor.sourceVersion,
    start_ms: selection.startMs,
    end_ms: selection.endMs,
    display_name: name,
    source_scheme: selection.sourceScheme || null,
    source_fragment_id: selection.sourceFragmentId || null,
    source_analysis_run_id: selection.sourceRunId || null,
    source_search_id: selection.sourceSearchId || null,
    source_dialogue_asset_key: selection.sourceDialogueAssetKey || null,
    manual_override: Boolean(selection.manualOverride),
    idempotency_key: idempotencyKey,
  };
  if (input.manual_override && !input.source_fragment_id) {
    input.source_scheme = null;
    input.source_analysis_run_id = null;
  }
  if (!input.source_search_id) {
    input.source_dialogue_asset_key = null;
  }
  return input;
}

export function normalizeSearchCandidate(raw, index = 0) {
  const source = text(raw?.source);
  const candidateId = opaqueId(raw?.candidate_id);
  const assetId = opaqueId(raw?.asset_id);
  const candidateKind = source === "media_library"
    ? text(raw?.candidate_kind) || "original_video"
    : "";
  const sourceAssetId = opaqueId(raw?.source_asset_id || raw?.asset_id);
  const sourceClipId = opaqueId(raw?.source_clip_id);
  const sourceVersion = text(raw?.source_version);
  const contentSha256 = text(raw?.content_sha256 || raw?.source_version);
  const providerSearchId = opaqueId(raw?.provider_search_id);
  const provider = text(raw?.provider);
  const width = positiveIntegerOrNull(raw?.width);
  const height = positiveIntegerOrNull(raw?.height);
  const creatorInput = raw?.creator && typeof raw.creator === "object" && !Array.isArray(raw.creator)
    ? raw.creator
    : {};
  const licenseInput = raw?.license && typeof raw.license === "object" && !Array.isArray(raw.license)
    ? raw.license
    : {};
  const allowedActions = Array.isArray(raw?.allowed_actions)
    ? [...new Set(raw.allowed_actions.map(text).filter(Boolean))]
    : [];
  const errors = [];
  if (!SEARCH_SOURCES.has(source)) errors.push(`候选 ${index + 1} source 非法`);
  if (!candidateId) errors.push(`候选 ${index + 1} candidate_id 非法`);
  const permitted = source === "external"
    ? new Set(["preview", "import_whole"])
    : candidateKind === "derived_clip"
      ? new Set(["preview", "import_clip"])
      : new Set(["preview", "open_editor", "import_original"]);
  for (const action of allowedActions) {
    if (!permitted.has(action)) errors.push(`${source || "unknown"} 候选包含越权动作 ${action}`);
  }
  if (source === "external" && (raw?.asset_id || raw?.source_version)) {
    errors.push("external 候选不得携带 asset_id/source_version");
  }
  if (source === "external" && (!providerSearchId || !provider)) {
    errors.push("external 候选缺少 provider/provider_search_id");
  }
  if (source === "media_library" && !sourceVersion) errors.push("media_library 候选缺少 source_version");
  if (source === "media_library" && !["original_video", "derived_clip"].includes(candidateKind)) errors.push("media_library 候选 candidate_kind 非法");
  if (source === "media_library" && candidateKind === "original_video" && (
    !assetId || candidateId !== assetId || sourceAssetId !== assetId || sourceClipId || contentSha256 !== sourceVersion
  )) errors.push("original_video 候选身份或内容哈希不一致");
  if (source === "media_library" && candidateKind === "derived_clip" && (
    assetId || !sourceAssetId || sourceClipId !== candidateId || !contentSha256 || (raw?.matched_fragments || []).length
  )) errors.push("derived_clip 候选身份、哈希或命中类型不一致");
  const durationMs = integerOrNull(raw?.duration_ms);
  const candidateStartMs = integerOrNull(raw?.candidate_start_ms);
  const candidateEndMs = integerOrNull(raw?.candidate_end_ms);
  const sourceStartMs = integerOrNull(raw?.source_start_ms);
  const sourceEndMs = integerOrNull(raw?.source_end_ms);
  const timeBasis = text(raw?.time_basis);
  if (source === "media_library" && candidateKind === "original_video" && allowedActions.join(",") !== "preview,open_editor,import_original") {
    errors.push("original_video 候选 allowed_actions 不符合固定合同");
  }
  if (source === "media_library" && candidateKind === "derived_clip" && (
    !durationMs || candidateStartMs !== 0 || candidateEndMs !== durationMs
    || sourceStartMs === null || sourceEndMs === null || sourceEndMs <= sourceStartMs
    || timeBasis !== "candidate" || allowedActions.join(",") !== "preview,import_clip"
  )) errors.push("derived_clip 候选时间基准或 allowed_actions 不符合固定合同");
  const matchedFragments = Array.isArray(raw?.matched_fragments)
    ? raw.matched_fragments.map((fragment) => ({
      scheme: text(fragment?.analysis_scheme || fragment?.scheme),
      analysisScheme: text(fragment?.analysis_scheme || fragment?.scheme),
      runId: opaqueId(fragment?.run_id),
      fragmentId: opaqueId(fragment?.fragment_id),
      startMs: integerOrNull(fragment?.start_ms),
      endMs: integerOrNull(fragment?.end_ms),
      dialogueText: text(fragment?.dialogue_text),
      summary: text(fragment?.summary),
      keyframeUrl: text(fragment?.keyframe_url),
    }))
    : [];
  return {
    valid: errors.length === 0,
    errors,
    candidate: {
      source,
      candidateKind,
      candidateId,
      assetId,
      sourceAssetId,
      sourceClipId,
      sourceVersion,
      contentSha256,
      provider,
      providerAssetId: opaqueId(raw?.provider_asset_id),
      providerSearchId,
      displayName: text(raw?.display_name) || `候选 ${index + 1}`,
      description: text(raw?.description),
      previewUrl: text(raw?.preview_url),
      thumbnailUrl: text(raw?.thumbnail_url),
      sourceUrl: safeExternalUrl(raw?.source_url),
      durationMs,
      tags: Array.isArray(raw?.tags) ? raw.tags.map(text).filter(Boolean) : [],
      candidateStartMs,
      candidateEndMs,
      sourceStartMs,
      sourceEndMs,
      timeBasis,
      orientation: text(raw?.orientation),
      width,
      height,
      aspect: text(raw?.aspect) || (width && height ? `${width}:${height}` : ""),
      creator: {
        name: text(creatorInput.name),
        url: safeExternalUrl(creatorInput.url),
      },
      license: {
        name: text(licenseInput.name),
        url: safeExternalUrl(licenseInput.url),
        status: text(licenseInput.license_status) || "unknown",
        requiresAttribution: Boolean(licenseInput.requires_attribution),
        attributionText: text(licenseInput.attribution_text),
      },
      importSupported: raw?.import_supported !== false,
      importUnsupportedReason: text(raw?.import_unsupported_reason),
      score: Number.isFinite(Number(raw?.score)) ? Number(raw.score) : null,
      scoreReasons: Array.isArray(raw?.score_reasons) ? raw.score_reasons.map(text).filter(Boolean) : [],
      matchedFragments,
      allowedActions,
    },
  };
}

export function normalizeSearchRun(raw) {
  const errors = [];
  const searchId = opaqueId(raw?.search_id);
  if (!searchId) errors.push("搜索响应缺少合法 search_id");
  if (!Array.isArray(raw?.items)) errors.push("搜索响应 items 必须为数组");
  const items = (Array.isArray(raw?.items) ? raw.items : []).map((item, index) => {
    const normalized = normalizeSearchCandidate(item, index);
    errors.push(...normalized.errors);
    return normalized.candidate;
  });
  const resultCount = integerOrNull(raw?.result_count);
  if (resultCount !== null && resultCount !== items.length) {
    errors.push("搜索 result_count 与 items 长度不一致");
  }
  return {
    valid: errors.length === 0,
    errors,
    searchId,
    plannerDegraded: Boolean(raw?.planner_degraded),
    retrievalVersion: text(raw?.retrieval_version),
    searchRuns: {
      mediaLibrary: opaqueId(raw?.search_runs?.media_library),
      external: opaqueId(raw?.search_runs?.external),
    },
    sourceErrors: raw?.source_errors && typeof raw.source_errors === "object"
      ? { ...raw.source_errors }
      : {},
    items,
  };
}

export function editorReturnHash(editor) {
  const navigation = editor?.navigation || {};
  if (navigation.returnTo === "storyboard_dialogue" && navigation.targetTaskId) {
    const query = new URLSearchParams();
    if (navigation.dialogueValid && navigation.dialogueAssetKey) {
      query.set("dialogue_asset_key", navigation.dialogueAssetKey);
    }
    return `#/koubo-storyboard/tasks/${navigation.targetTaskId}${query.size ? `?${query}` : ""}`;
  }
  return `#/media-library/${encodeURIComponent(editor?.asset?.assetId || "")}`;
}

export function newIdempotencyKey(prefix = "editor") {
  const random = globalThis.crypto?.randomUUID?.().replaceAll("-", "")
    || `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;
  return `${prefix}.${random}`;
}

export function isTransientClipJobPollError(error) {
  const name = text(error?.name);
  const message = text(error?.message || error);
  if (name === "TypeError") return true;
  return /(failed to fetch|load failed|network(?:error| request failed)|econnrefused|bad gateway|service unavailable|gateway timeout|\b50[234]\b)/i.test(
    message,
  );
}
