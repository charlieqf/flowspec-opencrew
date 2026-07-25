const text = (value) => String(value ?? "").trim();
const numberOrNull = (value) => {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
};

export const OPEN_CUT_SCHEMES = [
  {
    id: "dialogue",
    label: "对白分析",
    title: "对白分析工具集",
    description: "识别原始对白并与画面字幕、代表画面校准，形成一句话一个可播放片段。",
    output: "SessionOutput/subtitle/final_srt_frame_items.json",
    steps: ["准备分析环境", "读取视频信息", "识别音频对白", "对白与代表画面对齐"],
    emptyTitle: "尚未生成对白分析结果",
    emptyDescription: "打开对白分析工具集完成配置后，结果会以只读 SRT 片段显示在这里。",
  },
  {
    id: "visual",
    label: "画面分析",
    title: "画面分析工具集",
    description: "先完成场景切分，并在每个不超过 15 秒的片段固定采样四帧，再由视觉模型生成带依据的只读描述。",
    output: "SessionOutput/visual/visual_semantic_segments.json",
    steps: ["场景切分", "按 12.5% / 37.5% / 62.5% / 87.5% 采样四帧", "固定本次分析输入", "单次四图生成并校验画面描述"],
    emptyTitle: "尚未生成画面分析结果",
    emptyDescription: "先运行画面结构分析；结构完成后可显式授权并生成画面语义描述。",
  },
  {
    id: "composite",
    label: "综合分析",
    title: "综合分析工具集",
    description: "融合对白和画面边界，生成适合检索、挑片和剪辑的综合片段。",
    output: "SessionOutput/json/composite_semantic_segments.json",
    steps: ["读取对白结果", "读取画面结果", "边界融合", "质量标注与片段索引"],
    emptyTitle: "尚未生成综合分析结果",
    emptyDescription: "对白分析和画面分析完成后，才可以配置并运行综合分析。",
  },
];

export const openCutSchemeById = (schemeId) => OPEN_CUT_SCHEMES.find((item) => item.id === schemeId) || OPEN_CUT_SCHEMES[0];

export function openCutStatusMeta(status) {
  const map = {
    draft: ["DRAFT", "neutral"],
    not_analyzed: ["未分析", "neutral"],
    queued: ["等待中", "warning"],
    running: ["运行中", "info"],
    processing: ["运行中", "info"],
    partial: ["部分完成", "warning"],
    ready: ["已完成", "success"],
    blocked: ["等待授权", "warning"],
    stale: ["已过期", "warning"],
    failed: ["失败", "danger"],
  };
  const normalized = text(status).toLowerCase();
  const value = map[normalized];
  if (!value) {
    return normalized
      ? { label: `未知状态：${normalized}`, tone: "neutral" }
      : { label: map.not_analyzed[0], tone: map.not_analyzed[1] };
  }
  return { label: value[0], tone: value[1] };
}

export function isNoAudioDialogueResult(result = {}) {
  const code = text(result.errorCode || result.error_code).toLowerCase();
  const message = text(result.error).toLowerCase();
  return code === "video_has_no_audio"
    || message.includes("video_has_no_audio")
    || message.includes("source video has no audio track")
    || message.includes("源视频没有音轨");
}

export function analysisSchemeStatusMeta(schemeId, result = {}) {
  if (schemeId === "dialogue" && isNoAudioDialogueResult(result)) {
    return { label: "无音轨", tone: "neutral" };
  }
  return openCutStatusMeta(result.status);
}

export function openCutOverallStatusMeta(openCut = {}) {
  if (
    text(openCut.status).toLowerCase() === "blocked"
    && isNoAudioDialogueResult(openCut.schemes?.dialogue)
  ) {
    return { label: "部分可用", tone: "warning" };
  }
  return openCutStatusMeta(openCut.status);
}

function normalizeKeyframe(raw = {}, index = 0) {
  const hasMillisecondTime = raw.time_ms !== null && raw.time_ms !== undefined
    || raw.keyframe_time_ms !== null && raw.keyframe_time_ms !== undefined;
  const rawTime = hasMillisecondTime
    ? raw.time_ms ?? raw.keyframe_time_ms
    : raw.time ?? raw.keyframe_time;
  return {
    id: text(raw.id || raw.keyframe_id || `keyframe-${index + 1}`),
    timeMs: hasMillisecondTime
      ? numberOrNull(rawTime)
      : secondsToMilliseconds(rawTime),
    imageUrl: text(raw.image_url || raw.imageUrl),
    imageHash: text(raw.image_sha256 || raw.image_hash),
  };
}

function normalizeFragment(raw = {}, index = 0) {
  const hasMillisecondRange = raw.start_ms !== null && raw.start_ms !== undefined
    || raw.end_ms !== null && raw.end_ms !== undefined;
  const startMs = hasMillisecondRange
    ? numberOrNull(raw.start_ms) ?? 0
    : secondsToMilliseconds(raw.start ?? raw.start_time) ?? 0;
  const endMs = hasMillisecondRange
    ? numberOrNull(raw.end_ms) ?? startMs
    : secondsToMilliseconds(raw.end ?? raw.end_time) ?? startMs;
  const hasAction = Object.prototype.hasOwnProperty.call(raw, "action");
  const action = hasAction
    ? raw.action === null
      ? null
      : text(raw.action)
    : undefined;
  const claimEvidence = Object.fromEntries(
    Object.entries(raw.claim_evidence || raw.claimEvidence || {})
      .filter(([, refs]) => Array.isArray(refs))
      .map(([claim, refs]) => [
        text(claim),
        refs.map(text).filter(Boolean),
      ])
      .filter(([claim]) => Boolean(claim)),
  );
  const visualClaimRefs = Object.fromEntries(
    Object.entries(raw.visual_claim_refs || raw.visualClaimRefs || {})
      .filter(([, refs]) => Array.isArray(refs))
      .map(([claim, refs]) => [
        text(claim),
        refs.map(text).filter(Boolean),
      ])
      .filter(([claim]) => Boolean(claim)),
  );
  return {
    id: text(raw.fragment_id || raw.id || `fragment-${index + 1}`),
    index: index + 1,
    title: text(raw.title || raw.scene_title || raw.summary) || `片段 ${index + 1}`,
    dialogue: text(raw.dialogue_text || raw.dialogue || raw.text || raw.srt_text),
    summary: text(raw.summary || raw.visual_summary || raw.description),
    visualSummary: text(raw.visual_summary || raw.visual_description || raw.scene_description),
    startMs,
    endMs,
    keywords: Array.isArray(raw.keywords) ? raw.keywords.map(text).filter(Boolean) : [],
    people: Array.isArray(raw.people) ? raw.people.map(text).filter(Boolean) : [],
    objects: Array.isArray(raw.objects) ? raw.objects.map(text).filter(Boolean) : [],
    scene: text(raw.scene),
    action,
    shotType: text(raw.shot_type || raw.shotType),
    usability: text(raw.usability || raw.quality_status)
      || (raw.needs_review === false ? "keep" : "review"),
    excludeReasons: Array.isArray(raw.exclude_reasons) ? raw.exclude_reasons.map(text).filter(Boolean) : [],
    videoUrl: text(raw.preview_url || raw.video_url || raw.videoUrl),
    keyframes: (Array.isArray(raw.keyframes) ? raw.keyframes : []).map(normalizeKeyframe),
    keyframeRefs: (Array.isArray(raw.keyframe_refs) ? raw.keyframe_refs : []).map(text).filter(Boolean),
    dialogueRefs: (Array.isArray(raw.dialogue_refs) ? raw.dialogue_refs : []).map(text).filter(Boolean),
    visualRefs: (Array.isArray(raw.visual_refs) ? raw.visual_refs : []).map(text).filter(Boolean),
    claimEvidence,
    visualClaimRefs,
    boundaryReasons: (Array.isArray(raw.boundary_reasons) ? raw.boundary_reasons : []).map(text).filter(Boolean),
    samplingStrategy: text(raw.sampling_strategy),
    confidence: numberOrNull(raw.confidence),
    needsReview: Boolean(raw.needs_review),
  };
}

export function normalizeOpenCutDetail(raw = {}, asset = {}) {
  const task = raw.open_cut || raw.openCut || {};
  const results = raw.analysis_results || raw.analysisResults || {};
  const counts = task.counts || {};
  const statuses = {
    dialogue: text(task.dialogue_status || task.dialogueStatus) || "not_analyzed",
    visualStructure: text(task.visual_structure_status || task.visualStructureStatus) || "not_analyzed",
    visualSemantic: text(task.visual_semantic_status || task.visualSemanticStatus) || "not_analyzed",
    composite: text(task.composite_status || task.compositeStatus) || "not_analyzed",
  };
  statuses.visual = text(task.visual_status || task.visualStatus)
    || deriveVisualStatus(statuses.visualStructure, statuses.visualSemantic);
  const progressByScheme = {
    dialogue: task.dialogue_progress || task.dialogueProgress || {},
    visual: task.visual_progress || task.visualProgress || {},
    visualSemantic: task.visual_semantic_progress || task.visualSemanticProgress || {},
    composite: task.composite_progress || task.compositeProgress || {},
  };
  const errorByScheme = {
    dialogue: task.dialogue_error || task.dialogueError,
    visual: task.visual_error || task.visualError,
    visualSemantic: task.visual_semantic_error || task.visualSemanticError,
    composite: task.composite_error || task.compositeError,
  };
  const errorCodeByScheme = {
    dialogue: text(task.dialogue_error_code || task.dialogueErrorCode),
    visual: "",
    visualSemantic: "",
    composite: "",
  };
  const summaryCounts = {
    dialogue: numberOrNull(counts.dialogue ?? asset.analysisSummary?.dialogueCount),
    visual: numberOrNull(counts.visual ?? asset.analysisSummary?.visualCount),
    composite: numberOrNull(counts.composite ?? asset.analysisSummary?.compositeCount),
  };
  return {
    taskId: numberOrNull(task.task_id ?? task.taskId),
    sessionId: numberOrNull(task.session_id ?? task.sessionId ?? asset.sessionId),
    status: text(task.status) || "draft",
    schemes: Object.fromEntries(OPEN_CUT_SCHEMES.map((scheme) => {
      const result = results[scheme.id] || {};
      const items = (Array.isArray(result.items) ? result.items : []).map(normalizeFragment);
      return [scheme.id, {
        status: statuses[scheme.id],
        count: summaryCounts[scheme.id] ?? (items.length ? items.length : null),
        error: text(result.error || errorByScheme[scheme.id]),
        errorCode: text(result.error_code || result.errorCode || errorCodeByScheme[scheme.id]),
        progress: progressByScheme[scheme.id] || {},
        items,
        ...(scheme.id === "visual" ? {
          structureStatus: statuses.visualStructure,
          semanticStatus: statuses.visualSemantic,
          structureRunId: text(task.visual_structure_current_run_id || task.visualStructureCurrentRunId),
          semanticRunId: text(task.visual_semantic_current_run_id || task.visualSemanticCurrentRunId),
          semanticError: text(errorByScheme.visualSemantic),
          semanticProgress: progressByScheme.visualSemantic || {},
        } : {}),
      }];
    })),
  };
}

export function canRunComposite(openCut) {
  const visual = openCut?.schemes?.visual;
  return openCut?.schemes?.dialogue?.status === "ready"
    && visual?.structureStatus === "ready"
    && visual?.semanticStatus === "ready";
}

export function deriveVisualStatus(structureStatus, semanticStatus) {
  const structure = text(structureStatus).toLowerCase() || "not_analyzed";
  const semantic = text(semanticStatus).toLowerCase() || "not_analyzed";
  if (["queued", "running"].includes(structure)) return "running";
  if (structure === "failed") return "failed";
  if (structure !== "ready") return "not_analyzed";
  if (["queued", "running"].includes(semantic)) return "running";
  if (semantic === "blocked") return "blocked";
  if (semantic === "ready") return "ready";
  if (semantic === "stale") return "stale";
  if (["not_analyzed", "failed"].includes(semantic)) return "partial";
  return "partial";
}

export function normalizeVisualCurrent(raw = {}) {
  const run = raw.run && typeof raw.run === "object" ? raw.run : null;
  const scheme = text(run?.scheme);
  const samplingStrategy = text(
    run?.sampling_strategy
    || run?.progress?.sampling_strategy
    || raw.sampling_strategy,
  );
  const normalizedRun = run ? {
    id: text(run.analysis_run_id || run.run_id),
    scheme,
    status: text(run.status) || "not_analyzed",
    schemaVersion: text(run.schema_version),
    promptVersion: text(run.prompt_version),
    modelAlias: text(run.model_alias || run.model_config_alias || run.model_config_label),
    modelVersion: text(run.model_version || run.model_alias_version || run.prompt_version || run.schema_version),
    samplingStrategy,
    error: run.error && typeof run.error === "object"
      ? text(run.error.user_message || run.error.message || run.error.code)
      : text(run.error),
  } : null;
  return {
    run: normalizedRun,
    items: (Array.isArray(raw.items) ? raw.items : [])
      .map(normalizeFragment)
      .map((item) => ({
        ...item,
        samplingStrategy: item.samplingStrategy || samplingStrategy,
      })),
  };
}

export function resolveVisualDisplayResult(structureResult = {}, current = null) {
  if (current?.run?.scheme !== "visual_semantic") return structureResult;
  const structureById = new Map(
    (structureResult.items || []).map((item) => [item.id, item]),
  );
  return {
    ...structureResult,
    error: current.run.error || structureResult.semanticError || "",
    items: current.items.map((semanticItem) => {
      const structureItem = structureById.get(semanticItem.id) || {};
      return {
        ...structureItem,
        ...semanticItem,
        keyframes: semanticItem.keyframes.length
          ? semanticItem.keyframes
          : structureItem.keyframes || [],
      };
    }),
    semanticRun: current.run,
  };
}

export function normalizeCompositeCurrent(raw = {}) {
  const run = raw.run && typeof raw.run === "object" ? raw.run : null;
  return {
    run: run ? {
      id: text(run.analysis_run_id || run.run_id),
      scheme: text(run.scheme),
      status: text(run.status) || "not_analyzed",
      schemaVersion: text(run.schema_version),
      promptVersion: text(run.prompt_version),
      modelAlias: text(run.model_alias || run.model_config_alias || run.model_config_label),
      modelVersion: text(run.model_version || run.model_alias_version || run.prompt_version || run.schema_version),
      error: run.error && typeof run.error === "object"
        ? text(run.error.user_message || run.error.message || run.error.code)
        : text(run.error),
    } : null,
    items: (Array.isArray(raw.items) ? raw.items : []).map(normalizeFragment),
  };
}

export function resolveCompositeDisplayResult(detailResult = {}, current = null) {
  if (current?.run?.scheme !== "composite") return detailResult;
  return {
    ...detailResult,
    error: current.run.error || detailResult.error || "",
    items: current.items,
    semanticRun: current.run,
  };
}

export function visualSemanticRunState(visualResult = {}, allowCloudVisualDataTransfer = false) {
  const structureStatus = text(visualResult.structureStatus).toLowerCase() || "not_analyzed";
  const semanticStatus = text(visualResult.semanticStatus).toLowerCase() || "not_analyzed";
  const active = ["queued", "running"].includes(semanticStatus);
  const retry = ["blocked", "ready", "stale", "failed"].includes(semanticStatus);
  let disabledReason = "";
  if (structureStatus !== "ready") disabledReason = "请先完成画面结构分析";
  else if (active) disabledReason = "视觉语义正在运行";
  else if (!allowCloudVisualDataTransfer) disabledReason = "请先确认本次运行的云端图像传输授权";
  return {
    active,
    retry,
    runnable: !disabledReason,
    disabledReason,
    label: active ? "视觉语义运行中" : retry ? "重新运行视觉语义" : "运行视觉语义",
  };
}

export function compositeRunState(openCut = {}) {
  const dialogueStatus = text(openCut?.schemes?.dialogue?.status).toLowerCase() || "not_analyzed";
  const visual = openCut?.schemes?.visual || {};
  const structureStatus = text(visual.structureStatus).toLowerCase() || "not_analyzed";
  const semanticStatus = text(visual.semanticStatus).toLowerCase() || "not_analyzed";
  const compositeStatus = text(openCut?.schemes?.composite?.status).toLowerCase() || "not_analyzed";
  const active = ["queued", "running"].includes(compositeStatus);
  const retry = ["blocked", "ready", "stale", "failed"].includes(compositeStatus);
  const missingPrerequisites = [
    ["对白分析", dialogueStatus],
    ["画面结构分析", structureStatus],
    ["视觉语义分析", semanticStatus],
  ].filter(([, status]) => status !== "ready").map(([label]) => label);
  let disabledReason = "";
  if (dialogueStatus !== "ready") disabledReason = "请先完成当前对白分析";
  else if (structureStatus !== "ready") disabledReason = "请先完成当前画面结构分析";
  else if (semanticStatus !== "ready") disabledReason = "请先完成当前视觉语义分析";
  else if (active) disabledReason = "综合分析正在运行";
  return {
    active,
    retry,
    runnable: !disabledReason,
    disabledReason,
    prerequisiteMessage: missingPrerequisites.length
      ? `请先完成${missingPrerequisites.join("、")}`
      : "",
    label: active ? "综合分析运行中" : retry ? "重新运行综合分析" : "运行综合分析",
  };
}

export function samplingStrategyLabel(value) {
  if (text(value) === "scene_midpoint_v1") return "画面中点单帧";
  if (text(value) === "scene_uniform_4_v1") return "四帧均匀采样（12.5% / 37.5% / 62.5% / 87.5%）";
  return text(value) ? "已记录的画面采样方式" : "未提供采样方式";
}

export function actionEvidenceLabel(item = {}) {
  if (item.action === null && item.samplingStrategy === "scene_midpoint_v1") {
    return "仅凭当前画面中点单帧无法可靠判断连续动作。";
  }
  if (item.action === null) return "当前没有足够画面证据判断连续动作。";
  if (typeof item.action === "string" && item.action) return item.action;
  return "当前结果未提供可核验的动作描述。";
}

export function evidenceClaimLabel(value) {
  return {
    people: "人物",
    objects: "物体",
    scene: "场景",
    action: "动作",
  }[text(value)] || "其他依据";
}

export function formatFragmentTimeMs(milliseconds) {
  const value = Math.max(0, Number(milliseconds || 0));
  const totalSeconds = value / 1000;
  const minutes = Math.floor(totalSeconds / 60);
  const remaining = totalSeconds - minutes * 60;
  return `${minutes}:${remaining.toFixed(3).padStart(6, "0")}`;
}

const secondsToMilliseconds = (seconds) => {
  const value = numberOrNull(seconds);
  return value === null ? null : Math.round(value * 1000);
};

export const usabilityMeta = (value) => {
  const map = {
    keep: ["可用", "success"],
    usable: ["可用", "success"],
    review: ["建议复核", "warning"],
    exclude: ["已过滤", "danger"],
    filtered: ["已过滤", "danger"],
    detected: ["已检测", "info"],
  };
  const item = map[text(value).toLowerCase()] || map.review;
  return { label: item[0], tone: item[1] };
};
