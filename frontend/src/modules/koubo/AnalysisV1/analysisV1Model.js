export const FINAL_ITEMS_PATH = "SessionOutput/subtitle/final_srt_frame_items.json";
export const REWRITTEN_ITEMS_PATH = "SessionOutput/subtitle/rewritten_srt_items.json";
export const STORYBOARD_PATH = "SessionOutput/storyboard/srt_storyboard.json";
export const FRAME_MAP_PATH = "SessionOutput/visual/srt_frame_map.json";
export const CALIBRATED_ITEMS_PATH = "SessionOutput/subtitle/calibrated_srt_items.json";
export const AUDIO_REFERENCE_PATH = "SessionOutput/Audio_Reference.wav";
export const TTS_BUILDER_CANDIDATES_PATH = "SessionOutput/tts/tts_builder_candidates.json";
export const SOURCE_VIDEO_PATH = "SessionContext/Video_Source.mp4";
export const STEP_02_RESULT_PATH = "S3_02_01_AudioASR/Report/Result.json";
export const STEP_02_01_RESULT_PATH = "S4_02_02_VideoSRTFrame/Report/Result.json";

export function taskIdFromAnalysisV1Hash(hash) {
  const match = String(hash || "").match(/^#\/analysis-v1\/tasks\/(\d+)/);
  return match ? Number(match[1]) : null;
}

export function formatSeconds(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "-";
  return `${number.toFixed(2).replace(/0$/, "").replace(/\.0$/, "")}s`;
}

export function formatDateTime(value) {
  if (!value) return "-";
  return new Date(Number(value)).toLocaleString([], { hour12: false });
}

export function statusTone(status) {
  const value = String(status || "draft").toLowerCase();
  if (["completed", "ready", "success"].includes(value)) return "ready";
  if (["running", "queued", "preparing"].includes(value)) return "running";
  if (["blocked", "failed", "error"].includes(value)) return "failed";
  return "idle";
}

function frameMapReviewById(frameMap) {
  const result = {};
  for (const item of frameMap?.items || []) {
    const key = String(item?.sentence_id || "");
    if (!key) continue;
    result[key] = Boolean(item?.calibration?.needs_review);
  }
  return result;
}

export function normalizeDialogueItems(payload, frameMap, api, sessionId, rewrittenPayload) {
  const reviewById = frameMapReviewById(frameMap);
  if (rewrittenPayload?.rewrite_mode === "free") {
    const originalItems = payload?.items || [];
    const rewrittenItems = rewrittenPayload?.items || [];
    const rowCount = Math.max(originalItems.length, rewrittenItems.length);
    return Array.from({ length: rowCount }, (_, index) => {
      const original = originalItems[index] || {};
      const rewritten = rewrittenItems[index] || {};
      const originalId = String(original?.srt_id || "");
      const rewrittenId = String(rewritten?.srt_id || "");
      const id = rewrittenId || originalId || `free_srt_${String(index + 1).padStart(4, "0")}`;
      const start = Number(rewritten?.start);
      const end = Number(rewritten?.end);
      const originalStart = Number(original?.start);
      const originalEnd = Number(original?.end);
      const imagePath = String(rewritten?.image_path || original?.image_path || "");
      const originalDialogue = String(original?.dialogue || original?.original_dialogue || "");
      const rewrittenDialogue = String(rewritten?.dialogue || rewritten?.rewritten_dialogue || "");
      return {
        id,
        order: index + 1,
        dialogue: originalDialogue,
        originalDialogue,
        rewrittenDialogue,
        imagePath,
        imageUrl: imagePath ? api.rawFileUrl(sessionId, imagePath) : "",
        videoUrl: api.rawFileUrl(sessionId, SOURCE_VIDEO_PATH),
        start: Number.isFinite(start) ? start : Number.isFinite(originalStart) ? originalStart : 0,
        end: Number.isFinite(end) ? end : Number.isFinite(originalEnd) ? originalEnd : Number.isFinite(start) ? start : Number.isFinite(originalStart) ? originalStart : 0,
        duration: Number(rewritten?.duration || original?.duration || 0),
        needsReview: Boolean(reviewById[originalId || id]),
        rewriteMode: "free",
        originalId,
      };
    });
  }
  const rewrittenById = {};
  for (const item of rewrittenPayload?.items || []) {
    const key = String(item?.srt_id || "");
    if (key) rewrittenById[key] = item;
  }
  return (payload?.items || []).map((item, index) => {
    const id = String(item?.srt_id || `dialogue_${index + 1}`);
    const rewritten = rewrittenById[id] || {};
    const imagePath = String(item?.image_path || "");
    const start = Number(item?.start);
    const end = Number(item?.end);
    const originalDialogue = String(item?.dialogue || "");
    const rewrittenDialogue = String(rewritten?.dialogue || "");
    return {
      id,
      order: index + 1,
      dialogue: originalDialogue,
      originalDialogue,
      rewrittenDialogue,
      imagePath,
      imageUrl: imagePath ? api.rawFileUrl(sessionId, imagePath) : "",
      videoUrl: api.rawFileUrl(sessionId, SOURCE_VIDEO_PATH),
      start: Number.isFinite(start) ? start : 0,
      end: Number.isFinite(end) ? end : Number.isFinite(start) ? start : 0,
      duration: Number(item?.duration || 0),
      needsReview: Boolean(reviewById[id]),
    };
  });
}

export function buildOutputSummary(dialogueItems, step0201Result) {
  const counts = step0201Result?.counts || {};
  return {
    finalItems: dialogueItems.length,
    selectedFrames: Number(counts.selected_frames || dialogueItems.filter((item) => item.imagePath).length),
    needsReview: Number(counts.needs_review || dialogueItems.filter((item) => item.needsReview).length),
    splitSentences: Number(counts.split_sentences || 0),
  };
}

export const DEFAULT_REWRITE_CONSTRAINTS = "不能增减句子数；必须一句一句头对头改写；不得合并、拆分、跳过任何一句。";
export const DEFAULT_STORYBOARD_QUICK_CONFIG = {
  enabled: true,
  target_scene_seconds: 8,
  target_shot_seconds: 16,
  split_tolerance_seconds: 2,
  language_boundary_mode: "balanced",
};
export const PROMPT_TABS = {
  rewrite: {
    id: "rewrite",
    label: "SRT Rewrite",
    simpleField: "rewrite_simple_prompt",
    finalField: "rewrite_final_prompt",
    generateLabel: "生成 SRT 改写最终提示词",
    saveLabel: "保存 SRT 改写最终提示词",
  },
  storyboard: {
    id: "storyboard",
    label: "StoryBoard",
    simpleField: "storyboard_simple_prompt",
    finalField: "storyboard_final_prompt",
    generateLabel: "生成 StoryBoard 最终提示词",
    saveLabel: "保存 StoryBoard 最终提示词",
  },
};

function valueOrDash(value) {
  const text = String(value || "").trim();
  return text || "-";
}

function fieldTextOrDefault(source, field, fallback) {
  if (!source || !Object.prototype.hasOwnProperty.call(source, field)) return fallback;
  return String(source[field] ?? "");
}

function formulaDescription(videoFormula) {
  const formula = String(videoFormula || "").trim();
  if (/hook\s*\/\s*trust\s*\/\s*cta/i.test(formula)) {
    return "Hook=开头抓注意，Trust=中段建立信任，CTA=结尾行动引导；后续会按这个公式整理和分段对白。";
  }
  return "按该公式理解每句对白承担的表达阶段，后续会据此整理和分段对白。";
}

function positiveNumber(value, fallback) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function nonNegativeNumber(value, fallback) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : fallback;
}

export function normalizeStoryboardQuickConfig(value) {
  const raw = value && typeof value === "object" && !Array.isArray(value) ? value : {};
  const targetScene = positiveNumber(raw.target_scene_seconds, DEFAULT_STORYBOARD_QUICK_CONFIG.target_scene_seconds);
  const targetShot = positiveNumber(raw.target_shot_seconds, DEFAULT_STORYBOARD_QUICK_CONFIG.target_shot_seconds);
  const tolerance = nonNegativeNumber(raw.split_tolerance_seconds, DEFAULT_STORYBOARD_QUICK_CONFIG.split_tolerance_seconds);
  const mode = ["strict", "balanced", "loose"].includes(String(raw.language_boundary_mode || "").toLowerCase())
    ? String(raw.language_boundary_mode).toLowerCase()
    : DEFAULT_STORYBOARD_QUICK_CONFIG.language_boundary_mode;
  return {
    enabled: raw.enabled !== false,
    target_scene_seconds: Math.max(1, targetScene),
    target_shot_seconds: Math.max(1, targetShot),
    split_tolerance_seconds: Math.max(0, tolerance),
    language_boundary_mode: mode,
  };
}

function storyboardQuickPromptLines(config) {
  const normalized = normalizeStoryboardQuickConfig(config);
  return [
    "StoryBoard 结构参数：",
    `- Scene 目标时长：${Number(normalized.target_scene_seconds).toFixed(2).replace(/\.00$/, "")} 秒`,
    `- Shot 目标时长：${Number(normalized.target_shot_seconds).toFixed(2).replace(/\.00$/, "")} 秒`,
    `- 分割容忍度：${Number(normalized.split_tolerance_seconds).toFixed(2).replace(/\.00$/, "")} 秒`,
    `- 语言边界策略：${normalized.language_boundary_mode}，优先保持自然句和语义完整`,
  ];
}

function storyboardSimplePromptMatchesQuickConfig(value, config) {
  const text = String(value || "");
  return storyboardQuickPromptLines(config).every((line) => text.includes(line));
}

export function buildRewriteSimplePrompt(draft) {
  const videoFormula = valueOrDash(draft?.video_formula || "口播转化脚本");
  const constraints = valueOrDash(fieldTextOrDefault(draft, "constraints", DEFAULT_REWRITE_CONSTRAINTS));
  const productInfo = valueOrDash(draft?.product_info);
  const hasProductInfo = productInfo !== "-";
  const explicitMode = String(draft?.script_creation_mode || "").trim();
  const isRewriteMode = explicitMode === "rewrite" || (!explicitMode && Boolean(String(draft?.script || "").trim()));
  const taskRequirements = isRewriteMode
    ? [
        "最终提示词必须要求：基于参考脚本逐句改写，结合行业、人设、目标受众和产品信息，沿用原对白的句序、节奏和表达功能，生成新的口播对白。",
        hasProductInfo ? "最终提示词必须要求：把参考脚本中的产品、卖点和表达重心替换为上述产品信息，不得沿用与当前产品冲突的内容。" : "最终提示词必须要求：未提供产品信息时，不得虚构产品名称、卖点、数据或功效。",
        "最终提示词必须强调：保持参考脚本的句数和顺序完全一致；每个输入句子只输出一个对应新句子，不合并、不拆分、不新增、不删除。",
      ]
    : [
        "最终提示词必须要求：根据全部业务参数从零生成一篇结构完整、可直接用于人物口播的脚本，不依赖任何参考对白。",
        `最终提示词必须要求：按照“${videoFormula}”组织完整脚本，明确开场吸引、主体表达和结尾行动引导，各部分衔接自然。`,
        hasProductInfo ? "最终提示词必须要求：围绕上述产品信息提炼卖点和表达重心，不得添加未经提供或无法确认的事实。" : "最终提示词必须要求：未提供产品信息时，围绕行业、人设和目标受众完成通用表达，不得虚构产品名称、卖点、数据或功效。",
        "最终提示词必须要求：生成完整脚本所需的全部内容，不受参考脚本句数限制；内容应连贯、口语化，并具备明确的表达目的。",
      ];
  return [
    "请基于以下业务参数，生成一份用于人物口播脚本创作的最终提示词。",
    `任务模式：${isRewriteMode ? "改写脚本创作" : "完整脚本创作"}`,
    ...(isRewriteMode ? ["最终提示词的用途是指导后续大模型改写用户提供的参考脚本；本阶段只生成最终提示词，不直接输出口播脚本。"] : []),
    "业务参数：",
    `行业：${valueOrDash(draft?.industry)}`,
    `人设：${valueOrDash(draft?.persona)}`,
    `目标受众：${valueOrDash(draft?.target_audience)}`,
    `视频公式：${videoFormula}。公式说明：${formulaDescription(videoFormula)}`,
    `产品信息：${productInfo}`,
    `约束条件：${constraints}`,
    "任务要求：",
    ...taskRequirements,
    "最终提示词必须要求：输出对白使用简体中文，禁止繁体字；英文、数字、品牌名保持原样。",
    "最终提示词要清晰、完整、可直接交给后续脚本模型执行。",
  ].join("\n");
}

export function buildStoryboardSimplePrompt(draft) {
  const videoFormula = valueOrDash(draft?.video_formula || "口播转化脚本");
  const constraints = valueOrDash(fieldTextOrDefault(draft, "constraints", DEFAULT_REWRITE_CONSTRAINTS));
  return [
    "请基于以下业务参数，生成一份用于 SRT StoryBoard 结构化任务的最终提示词。",
    "最终提示词的用途是指导后续大模型把改写后的 SRT 组织为 Shot / Scene；本阶段不要直接输出 StoryBoard 分组结果。",
    `视频公式：${videoFormula}。公式说明：${formulaDescription(videoFormula)}`,
    ...storyboardQuickPromptLines(draft?.storyboard_quick_config),
    "最终提示词必须说明：按视频公式归纳每段表达功能；按上述 Scene / Shot 时长目标组织结构，但优先保障语义完整。",
    "最终提示词必须强调：不改写对白；不改变 srt_id、顺序、时间和图片帧；每个 srt_id 必须出现且只出现一次。",
    `业务背景：行业 ${valueOrDash(draft?.industry)}；人设 ${valueOrDash(draft?.persona)}；目标受众 ${valueOrDash(draft?.target_audience)}；产品信息 ${valueOrDash(draft?.product_info)}。`,
    `约束条件：${constraints}`,
    "最终提示词要清晰、完整、可直接交给后续 StoryBoard 模型执行。",
  ].join("\n");
}

function isLegacyRewriteSimplePrompt(value) {
  return String(value || "").trim().startsWith("请根据现有识别出来的口播对白，生成新的改写对白。");
}

function isLegacyStoryboardSimplePrompt(value) {
  return String(value || "").trim().startsWith("请根据改写后的 SRT，对口播脚本组织 Shot / Scene 结构。");
}

export function createRewriteDraft(task) {
  const draft = {
    reference_video_path: task?.reference_video_path || "",
    industry: task?.industry || "",
    persona: task?.persona || "",
    product_info: task?.product_info || "",
    constraints: fieldTextOrDefault(task, "constraints", DEFAULT_REWRITE_CONSTRAINTS),
    target_audience: task?.target_audience || "",
    video_formula: task?.video_formula || "口播转化脚本",
    prompt_model_provider: task?.prompt_model_provider || "",
    prompt_model_id: task?.prompt_model_id || "",
    run_model_provider: task?.run_model_provider || "",
    run_model_id: task?.run_model_id || "",
    storyboard_quick_config: normalizeStoryboardQuickConfig(task?.storyboard_quick_config),
    simple_prompt: "",
    final_prompt: task?.final_prompt || "",
    rewrite_simple_prompt: task?.rewrite_simple_prompt || task?.simple_prompt || "",
    rewrite_final_prompt: task?.rewrite_final_prompt || task?.final_prompt || "",
    storyboard_simple_prompt: task?.storyboard_simple_prompt || "",
    storyboard_final_prompt: task?.storyboard_final_prompt || "",
  };
  const rewriteSimple = draft.rewrite_simple_prompt && !isLegacyRewriteSimplePrompt(draft.rewrite_simple_prompt) ? draft.rewrite_simple_prompt : buildRewriteSimplePrompt(draft);
  const storyboardSimple = draft.storyboard_simple_prompt
    && !isLegacyStoryboardSimplePrompt(draft.storyboard_simple_prompt)
    && storyboardSimplePromptMatchesQuickConfig(draft.storyboard_simple_prompt, draft.storyboard_quick_config)
    ? draft.storyboard_simple_prompt
    : buildStoryboardSimplePrompt(draft);
  return {
    ...draft,
    simple_prompt: rewriteSimple,
    final_prompt: draft.rewrite_final_prompt || draft.final_prompt,
    rewrite_simple_prompt: rewriteSimple,
    storyboard_simple_prompt: storyboardSimple,
  };
}

export function normalizePromptBundle(draft) {
  const normalizedQuickConfig = normalizeStoryboardQuickConfig(draft.storyboard_quick_config);
  const draftWithQuickConfig = { ...draft, storyboard_quick_config: normalizedQuickConfig };
  const rewriteSimple = draft.rewrite_simple_prompt || draft.simple_prompt || buildRewriteSimplePrompt(draftWithQuickConfig);
  const rewriteFinal = draft.rewrite_final_prompt || draft.final_prompt || "";
  const storyboardSimple = storyboardSimplePromptMatchesQuickConfig(draft.storyboard_simple_prompt, normalizedQuickConfig)
    ? draft.storyboard_simple_prompt
    : buildStoryboardSimplePrompt(draftWithQuickConfig);
  return {
    ...draft,
    constraints: fieldTextOrDefault(draft, "constraints", DEFAULT_REWRITE_CONSTRAINTS),
    storyboard_quick_config: normalizedQuickConfig,
    simple_prompt: rewriteSimple,
    final_prompt: rewriteFinal,
    rewrite_simple_prompt: rewriteSimple,
    rewrite_final_prompt: rewriteFinal,
    storyboard_simple_prompt: storyboardSimple,
    storyboard_final_prompt: draft.storyboard_final_prompt || "",
  };
}
