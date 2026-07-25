import { For, Show, createEffect, createSignal, onCleanup } from "solid-js";
import { ModelPresetCards, findModelPresetItem } from "../../../components/ModelPresetCards.jsx";
import { CheckIcon, CopyIcon, FileTextIcon, PlayIcon, RefreshIcon, SaveIcon, SparklesIcon, StopIcon, TrashIcon, WaveformIcon, XIcon } from "../AnalysisV1/analysisV1Icons.jsx";
import { buildRewriteSimplePrompt, buildStoryboardSimplePrompt, normalizeStoryboardQuickConfig } from "../AnalysisV1/analysisV1Model.js";

const DEFAULT_FORM = {
  script: "",
  script_format: "plain",
  industry: "医美",
  persona: "强判断老板型",
  target_audience: "老板",
  video_formula: "Hook/Trust/CTA",
  product_info: "",
  constraints: "",
  rewrite_simple_prompt: "",
  rewrite_final_prompt: "",
  storyboard_simple_prompt: "",
  storyboard_final_prompt: "",
  storyboard_quick_config: {
    target_scene_seconds: 8,
    target_shot_seconds: 16,
    split_tolerance_seconds: 2,
    language_boundary_mode: "balanced",
  },
  profile_id: "script",
  create_mode: "script",
  portrait_image_file: null,
  portrait_image_name: "",
  reference_video_file: null,
  reference_video_name: "",
  use_default_reference_video: true,
  reference_privacy_mode: "red_grid_guide",
  apply_privacy_grid_to_reference_video: false,
  apply_privacy_grid_to_target_identity_image: true,
  privacy_grid_preset: "dense_12_1",
  cell_size_reference: 12,
  line_width_reference: 1,
  srt_target_seconds: 8,
  portrait_segments_per_image: 2,
  voice_provider: "",
  voice_id: "",
  voice_label: "",
  tempo: 1,
  voice_tempo_by_id: {},
  talking_head_video_model_key: "max_1_5_x",
};

const TALKING_HEAD_PROFILE_ID = "person_talking_head_v1";
const TALKING_HEAD_QUICK_CONFIG = {
  target_scene_seconds: 15,
  target_shot_seconds: 15,
  split_tolerance_seconds: 0,
  language_boundary_mode: "strict",
};

const TALKING_HEAD_WIZARD_STEPS = [
  { id: "script", label: "脚本", description: "脚本生成或改写" },
  { id: "avatar", label: "人物声音", description: "首帧、声音与节奏" },
  { id: "storyboard", label: "故事版", description: "分镜规则与创建提示词" },
];

const TALKING_HEAD_VIDEO_MODELS = [
  {
    key: "flush_x",
    alias: "Flush X",
    maxSeconds: 15,
    tags: ["性价比高", "速度快"],
  },
  {
    key: "max_1_5_x",
    alias: "Max 1.5 X",
    maxSeconds: 15,
    tags: ["控制力强", "稳定性强"],
  },
  {
    key: "max_2_7_w",
    alias: "Max 2.7 W",
    maxSeconds: 10,
    tags: ["中国人物自然", "控制力一般", "速度慢"],
  },
  {
    key: "max_sd_2",
    alias: "Max SD 2",
    maxSeconds: 15,
    tags: ["表情动作参考", "隐私网格"],
  },
];

const PRIVACY_GRID_PRESETS = [
  { value: "dense_12_1", label: "密集 12×1（默认）", cellSizeReference: 12, lineWidthReference: 1 },
  { value: "dense_12_0_5", label: "密集细线 12×0.5", cellSizeReference: 12, lineWidthReference: 0.5 },
  { value: "medium_dense_24_1", label: "较密 24×1", cellSizeReference: 24, lineWidthReference: 1 },
  { value: "medium_dense_24_0_5", label: "较密细线 24×0.5", cellSizeReference: 24, lineWidthReference: 0.5 },
  { value: "sparse_36_1", label: "稀疏 36×1", cellSizeReference: 36, lineWidthReference: 1 },
  { value: "sparse_36_0_5", label: "稀疏细线 36×0.5", cellSizeReference: 36, lineWidthReference: 0.5 },
  { value: "very_sparse_48_1", label: "极疏 48×1", cellSizeReference: 48, lineWidthReference: 1 },
  { value: "very_sparse_48_0_5", label: "极疏细线 48×0.5", cellSizeReference: 48, lineWidthReference: 0.5 },
];

function privacyGridPresetFromSettings(settings = {}) {
  const requested = String(settings.privacy_grid_preset || "").trim();
  const byKey = PRIVACY_GRID_PRESETS.find((item) => item.value === requested);
  if (byKey) return byKey;
  const cellSize = Number(settings.cell_size_reference);
  const lineWidth = Number(settings.line_width_reference);
  return PRIVACY_GRID_PRESETS.find((item) => item.cellSizeReference === cellSize && item.lineWidthReference === lineWidth) || PRIVACY_GRID_PRESETS[0];
}

function talkingHeadModelSupportsReferenceVideo(model) {
  return ["max_2_7_w", "max_sd_2"].includes(String(model?.key || ""));
}

function talkingHeadVideoModelKey(videoModel) {
  const key = String(videoModel?.model_key || videoModel?.key || "").trim();
  if (TALKING_HEAD_VIDEO_MODELS.some((item) => item.key === key)) return key;
  const alias = String(videoModel?.model_alias || videoModel?.alias || "").trim().toLowerCase();
  return TALKING_HEAD_VIDEO_MODELS.find((item) => item.alias.toLowerCase() === alias)?.key || DEFAULT_FORM.talking_head_video_model_key;
}

const REWRITE_LINKED_LINE_PREFIXES = ["任务模式：", "行业：", "人设：", "目标受众：", "视频公式：", "产品信息：", "约束条件："];

function optionList(options, key) {
  return Array.isArray(options?.[key]) ? options[key] : [];
}

function uiScriptCreationMode(value) {
  return {
    user: "user",
    user_provided: "user",
    generate: "generate",
    ai_create: "generate",
    rewrite: "rewrite",
    ai_rewrite: "rewrite",
  }[String(value || "").trim().toLowerCase()] || "user";
}

function defaultOption(options, key, fallback) {
  return optionList(options, key)[0] || fallback;
}

function optionValue(options, key, current) {
  return optionList(options, key).includes(current) ? current : "__custom__";
}

function withSimplePromptDefaults(source) {
  const draft = {
    ...source,
    storyboard_quick_config: normalizeStoryboardQuickConfig(source.storyboard_quick_config),
  };
  const rewriteSimple = draft.rewrite_simple_prompt?.trim() || draft.simple_prompt?.trim() || buildRewriteSimplePrompt(draft);
  const storyboardSimple = draft.storyboard_simple_prompt?.trim() || buildStoryboardSimplePrompt(draft);
  return {
    ...draft,
    simple_prompt: rewriteSimple,
    rewrite_simple_prompt: rewriteSimple,
    storyboard_simple_prompt: storyboardSimple,
  };
}

function blankForm(options = {}) {
  return withSimplePromptDefaults({
    ...DEFAULT_FORM,
    industry: defaultOption(options, "industry", DEFAULT_FORM.industry),
    persona: defaultOption(options, "persona", DEFAULT_FORM.persona),
    target_audience: defaultOption(options, "target_audience", DEFAULT_FORM.target_audience),
    video_formula: defaultOption(options, "video_formula", DEFAULT_FORM.video_formula),
    storyboard_quick_config: { ...DEFAULT_FORM.storyboard_quick_config },
  });
}

function formFromTask(task, options = {}) {
  if (!task) return blankForm(options);
  const talkingHead = task.talkingHead || {};
  const segmentPlanning = talkingHead.segment_planning || {};
  const voiceTiming = talkingHead.voice_timing || {};
  const videoModel = talkingHead.video_model || {};
  const portrait = talkingHead.portrait || {};
  const referenceVideo = talkingHead.reference_video || {};
  const referencePrivacy = talkingHead.reference_privacy || {};
  const privacyGridPreset = privacyGridPresetFromSettings({
    ...(referencePrivacy.render_config?.privacy_grid || {}),
    ...referencePrivacy,
  });
  const portraitImage = String(portrait.portrait_image_path || task.portraitImage || "");
  const quickConfig = normalizeStoryboardQuickConfig(task.storyboardQuickConfig || DEFAULT_FORM.storyboard_quick_config);
  if (Number(segmentPlanning.srt_target_seconds) > 0) {
    quickConfig.target_shot_seconds = Number(segmentPlanning.srt_target_seconds);
    quickConfig.target_scene_seconds = Number(segmentPlanning.srt_target_seconds);
  }
  return withSimplePromptDefaults({
    ...blankForm(options),
    script: task.sourceScript || "",
    industry: task.industry || DEFAULT_FORM.industry,
    persona: task.persona || DEFAULT_FORM.persona,
    target_audience: task.targetAudience || DEFAULT_FORM.target_audience,
    video_formula: task.videoFormula || DEFAULT_FORM.video_formula,
    product_info: task.productInfo || "",
    constraints: task.constraints || "",
    rewrite_simple_prompt: task.rewriteSimplePrompt || task.simplePrompt || "",
    rewrite_final_prompt: task.rewriteFinalPrompt || task.finalPrompt || "",
    storyboard_simple_prompt: task.storyboardSimplePrompt || "",
    storyboard_final_prompt: task.storyboardFinalPrompt || "",
    storyboard_quick_config: quickConfig,
    profile_id: task.profileId || (task.createMode === "person_talking_head" ? TALKING_HEAD_PROFILE_ID : DEFAULT_FORM.profile_id),
    create_mode: task.createMode || DEFAULT_FORM.create_mode,
    script_creation_mode: task.scriptCreationMode || "user_provided",
    portrait_image_file: null,
    portrait_image_name: portraitImage ? portraitImage.split("/").pop() : "",
    reference_video_file: null,
    reference_video_name: String(referenceVideo.reference_video_path || "").split("/").pop(),
    use_default_reference_video: referenceVideo.use_system_default !== false,
    reference_privacy_mode: referencePrivacy.reference_privacy_mode || "red_grid_guide",
    apply_privacy_grid_to_reference_video: Object.prototype.hasOwnProperty.call(referencePrivacy, "apply_privacy_grid_to_reference_video")
      ? Boolean(referencePrivacy.apply_privacy_grid_to_reference_video)
      : referenceVideo.use_system_default === false,
    apply_privacy_grid_to_target_identity_image: referencePrivacy.apply_privacy_grid_to_target_identity_image !== false,
    privacy_grid_preset: privacyGridPreset.value,
    cell_size_reference: privacyGridPreset.cellSizeReference,
    line_width_reference: privacyGridPreset.lineWidthReference,
    srt_target_seconds: Number(segmentPlanning.srt_target_seconds || quickConfig.target_shot_seconds || DEFAULT_FORM.srt_target_seconds),
    portrait_segments_per_image: Number(segmentPlanning.portrait_segments_per_image || DEFAULT_FORM.portrait_segments_per_image),
    voice_provider: String(voiceTiming.provider || voiceTiming.voice_provider || DEFAULT_FORM.voice_provider),
    voice_id: String(voiceTiming.voice_id || ""),
    voice_label: String(voiceTiming.voice_label || ""),
    tempo: String(voiceTiming.tempo || DEFAULT_FORM.tempo),
    voice_tempo_by_id: voiceTiming.tempo_by_voice_id || {},
    talking_head_video_model_key: talkingHeadVideoModelKey(videoModel),
  });
}

function formFromDetailTask(task, previous) {
  if (!task) return previous;
  return {
    ...previous,
    industry: task.industry || previous.industry,
    persona: task.persona || previous.persona,
    target_audience: task.target_audience || previous.target_audience,
    video_formula: task.video_formula || previous.video_formula,
    product_info: task.product_info || previous.product_info,
    constraints: task.constraints || previous.constraints,
    rewrite_simple_prompt: task.rewrite_simple_prompt || task.simple_prompt || previous.rewrite_simple_prompt,
    rewrite_final_prompt: task.rewrite_final_prompt || task.final_prompt || previous.rewrite_final_prompt,
    storyboard_simple_prompt: task.storyboard_simple_prompt || previous.storyboard_simple_prompt,
    storyboard_final_prompt: task.storyboard_final_prompt || previous.storyboard_final_prompt,
    storyboard_quick_config: normalizeStoryboardQuickConfig(task.storyboard_quick_config || previous.storyboard_quick_config),
  };
}

function scriptHint(script) {
  const text = String(script || "").trim();
  if (!text) return "";
  return [
    "",
    "用户输入脚本：",
    text.length > 2400 ? `${text.slice(0, 2400)}...` : text,
  ].join("\n");
}

function secondsLabel(value) {
  return Number(value || 0).toFixed(2).replace(/\.00$/, "");
}

function decimalNumber(value, fallback = 1) {
  const normalized = String(value ?? "").trim().replace(/，/g, ".").replace(/,/g, ".");
  const parsed = Number.parseFloat(normalized);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function quickAdvResult(response) {
  return response?.result || response || {};
}

function voiceCloneItems(payload) {
  const result = quickAdvResult(payload);
  const voices = Array.isArray(result?.voices) ? result.voices : (Array.isArray(result?.data) ? result.data : []);
  return voices.map((item) => (item && typeof item === "object" ? item : { voice_id: String(item || "") }));
}

function cloudVoiceId(item) {
  return String(item?.voice_id || item?.voice_clone_id || item?.voice || item?.id || "").trim();
}

function cloudVoiceName(item) {
  return String(item?.voice_name || item?.name || item?.label || cloudVoiceId(item) || "-").trim();
}

function cloneVoiceSuffix(value) {
  const text = String(value || "").trim();
  return text.length > 6 ? text.slice(-6) : text || "-";
}

function isVoiceCreditInsufficientError(value) {
  const message = String(value || "").toLowerCase();
  return message.includes("insufficient_credit")
    || message.includes("insufficient credit")
    || message.includes("insufficient sub-credit")
    || message.includes("purchase additional credits");
}

function buildTalkingHeadStoryboardSimplePrompt(draft) {
  const quick = normalizeStoryboardQuickConfig(draft?.storyboard_quick_config || TALKING_HEAD_QUICK_CONFIG);
  return [
    "请基于以下业务参数，生成一份用于人物口播 StoryBoard 结构化任务的最终提示词。",
    "结构定义：人物口播固定为一个 Shot、一个 Scene。",
    "关键定义：分镜（秒）= 单个 Segment 的目标时长 = 单个 Dialogue 的目标长度，也是大模型单次生成视频的长度。",
    `- 单个 Segment / Dialogue 目标时长：${secondsLabel(quick.target_shot_seconds)} 秒`,
    `- 分割容忍度：${secondsLabel(quick.split_tolerance_seconds)} 秒`,
    `- 分组方式：${quick.language_boundary_mode}`,
    "结构规则：每一句 SRT 对应一个 Dialogue，也对应一个 Segment；不拆句、不合并句、不插入空镜；人物口播只使用人物形象首帧和尾帧延续策略。",
    `业务背景：行业 ${draft?.industry || "-"}；人设 ${draft?.persona || "-"}；目标受众 ${draft?.target_audience || "-"}；视频公式 ${draft?.video_formula || "-"}。`,
    `产品信息：${draft?.product_info || "-"}`,
    `约束条件：${draft?.constraints || "-"}`,
    "最终提示词必须强调：每个 srt_id 必须出现且只出现一次，顺序不变，不能切断一句对白。",
  ].join("\n");
}

export default function KouboTaskCreateFromScriptModal(props) {
  const [form, setForm] = createSignal(blankForm());
  const [activeTab, setActiveTab] = createSignal("rewrite");
  const [activeWizardStep, setActiveWizardStep] = createSignal("script");
  const [scriptCreationMode, setScriptCreationMode] = createSignal("user");
  const [scriptValidationError, setScriptValidationError] = createSignal("");
  const [promptDialogOpen, setPromptDialogOpen] = createSignal(false);
  const [promptGenerationScope, setPromptGenerationScope] = createSignal("all");
  const [promptProvider, setPromptProvider] = createSignal("");
  const [promptModel, setPromptModel] = createSignal("");
  const [promptError, setPromptError] = createSignal("");
  const [voiceClonePayload, setVoiceClonePayload] = createSignal(null);
  const [voiceCloneBusy, setVoiceCloneBusy] = createSignal(false);
  const [voiceCloneError, setVoiceCloneError] = createSignal("");
  const [voicePreviewBusy, setVoicePreviewBusy] = createSignal("");
  const [voicePreviewing, setVoicePreviewing] = createSignal("");
  const [voiceTempoById, setVoiceTempoById] = createSignal({});
  const [voiceDeleteConfirmId, setVoiceDeleteConfirmId] = createSignal("");
  const [voiceDeleteBusyId, setVoiceDeleteBusyId] = createSignal("");
  const [portraitDragging, setPortraitDragging] = createSignal(false);
  const [portraitUploadError, setPortraitUploadError] = createSignal("");
  const [referenceVideoDragging, setReferenceVideoDragging] = createSignal(false);
  const [referenceVideoUploadError, setReferenceVideoUploadError] = createSignal("");
  const [modalPosition, setModalPosition] = createSignal(null);
  let modalEl;
  let rewritePromptHighlightEl;
  let dragState = null;
  let hydrateKey = "";
  let promptGenerationInFlight = false;
  let voicePreviewAudio = null;
  const scriptModeDraftCache = new Map();

  function stopVoicePreview() {
    if (voicePreviewAudio) {
      voicePreviewAudio.pause();
      voicePreviewAudio.currentTime = 0;
      voicePreviewAudio = null;
    }
    setVoicePreviewing("");
  }

  function voiceTempo(item) {
    const voiceId = cloudVoiceId(item);
    const cached = voiceTempoById()[voiceId];
    // Tempo belongs to an individual voice row. Never fall back to the
    // currently selected voice's form tempo, otherwise editing one row makes
    // every untouched row appear to have changed as well.
    return String(cached ?? item?.recommended_tempo ?? item?.tempo ?? 1);
  }

  function updateVoiceTempo(item, value) {
    const voiceId = cloudVoiceId(item);
    if (!voiceId) return;
    setVoiceTempoById((prev) => ({ ...prev, [voiceId]: value }));
    if (form().voice_id === voiceId) update("tempo", value);
  }

  async function deleteVoiceClone(item) {
    const voiceId = cloudVoiceId(item);
    if (!voiceId || !props.onDeleteVoiceClone) return;
    if (voiceDeleteConfirmId() !== voiceId) {
      setVoiceDeleteConfirmId(voiceId);
      setVoiceCloneError("");
      return;
    }
    setVoiceDeleteConfirmId("");
    setVoiceDeleteBusyId(voiceId);
    setVoiceCloneError("");
    if (voicePreviewing() === voiceId) stopVoicePreview();
    try {
      const response = await props.onDeleteVoiceClone(voiceId);
      const result = response?.result || response || {};
      if (response?.ok === false || result?.ok === false) {
        const reasons = Array.isArray(result?.blocked_reasons) ? result.blocked_reasons : [];
        throw new Error(reasons.map((reason) => reason?.message || reason?.code).filter(Boolean).join("；") || result?.message || "删除克隆声音失败");
      }
      setVoiceTempoById((prev) => {
        const next = { ...prev };
        delete next[voiceId];
        return next;
      });
      if (form().voice_id === voiceId) setForm((prev) => ({ ...prev, voice_id: "", voice_label: "", tempo: 1 }));
      await loadVoiceClones(false);
    } catch (err) {
      setVoiceCloneError(err instanceof Error ? err.message : "删除克隆声音失败");
    } finally {
      setVoiceDeleteBusyId("");
    }
  }

  onCleanup(stopVoicePreview);

  createEffect(() => {
    if (!props.open()) {
      hydrateKey = "";
      setModalPosition(null);
      return;
    }
    const task = props.task?.();
    const nextKey = task ? `task-${task.taskId}` : "new";
    if (nextKey === hydrateKey) return;
    const previousHydrateKey = hydrateKey;
    hydrateKey = nextKey;
    // Prompt generation saves a new task before refreshing the task list. During
    // that refresh, props.task briefly changes from null to a list summary, which
    // intentionally omits source_script and the other editable detail fields.
    // Only that in-flight promotion owns the current draft; ordinary new -> task
    // transitions must still hydrate the authoritative task detail.
    if (previousHydrateKey === "new" && task && promptGenerationInFlight) return;
    const profile = props.profile?.() || {};
    const base = formFromTask(task, props.promptOptions?.() || {});
    setVoiceTempoById({
      ...Object.fromEntries(Object.entries(base.voice_tempo_by_id || {}).map(([voiceId, tempo]) => [voiceId, String(tempo || 1)])),
      ...(base.voice_id ? { [base.voice_id]: String(base.tempo || 1) } : {}),
    });
    setVoiceDeleteConfirmId("");
    setVoiceDeleteBusyId("");
    if (profile.id === TALKING_HEAD_PROFILE_ID) {
      const baseQuickConfig = normalizeStoryboardQuickConfig(base.storyboard_quick_config || TALKING_HEAD_QUICK_CONFIG);
      const nextQuickConfig = task ? baseQuickConfig : normalizeStoryboardQuickConfig(TALKING_HEAD_QUICK_CONFIG);
      const next = {
        ...base,
        profile_id: TALKING_HEAD_PROFILE_ID,
        create_mode: "person_talking_head",
        video_formula: base.video_formula || "人物口播",
        storyboard_quick_config: nextQuickConfig,
        srt_target_seconds: Number(task ? (base.srt_target_seconds || nextQuickConfig.target_shot_seconds) : nextQuickConfig.target_shot_seconds),
        portrait_segments_per_image: Number(base.portrait_segments_per_image || 2),
        voice_provider: base.voice_provider,
        tempo: String(base.tempo || 1),
      };
      next.storyboard_simple_prompt = buildTalkingHeadStoryboardSimplePrompt(next);
      setForm(next);
    } else {
      setForm(base);
    }
    setActiveTab("rewrite");
    setActiveWizardStep("script");
    scriptModeDraftCache.clear();
    setScriptCreationMode(task ? uiScriptCreationMode(base.script_creation_mode) : "user");
    setScriptValidationError("");
    setPortraitDragging(false);
    setPortraitUploadError("");
    setReferenceVideoDragging(false);
    setReferenceVideoUploadError("");
  });

  function update(key, value) {
    if (key === "script" && String(value || "").trim()) setScriptValidationError("");
    setForm((prev) => {
      const next = { ...prev, [key]: value };
      const rewriteLinkedFields = ["script", "industry", "persona", "target_audience", "video_formula", "product_info", "constraints"];
      const storyboardLinkedFields = ["industry", "persona", "target_audience", "video_formula", "product_info", "constraints"];
      if (rewriteLinkedFields.includes(key) || storyboardLinkedFields.includes(key)) {
        const draft = { ...next, script_creation_mode: scriptCreationMode(), storyboard_quick_config: normalizeStoryboardQuickConfig(next.storyboard_quick_config) };
        if (rewriteLinkedFields.includes(key)) {
          next.rewrite_simple_prompt = buildRewriteSimplePrompt(draft);
          next.rewrite_final_prompt = "";
          next.simple_prompt = next.rewrite_simple_prompt;
        }
        if (storyboardLinkedFields.includes(key)) {
          next.storyboard_simple_prompt = (props.profile?.()?.id || next.profile_id) === TALKING_HEAD_PROFILE_ID ? buildTalkingHeadStoryboardSimplePrompt(draft) : buildStoryboardSimplePrompt(draft);
          next.storyboard_final_prompt = "";
        }
      }
      return next;
    });
  }

  function updateUseDefaultReferenceVideo(value) {
    const useSystemDefault = Boolean(value);
    setForm((prev) => ({
      ...prev,
      use_default_reference_video: useSystemDefault,
      // The bundled system reference is already prepared and must never be
      // processed again. Upload mode restores the recommended checked state.
      apply_privacy_grid_to_reference_video: !useSystemDefault,
    }));
  }

  function updatePrivacyGridPreset(value) {
    const preset = PRIVACY_GRID_PRESETS.find((item) => item.value === value) || PRIVACY_GRID_PRESETS[0];
    setForm((prev) => ({
      ...prev,
      privacy_grid_preset: preset.value,
      cell_size_reference: preset.cellSizeReference,
      line_width_reference: preset.lineWidthReference,
    }));
  }

  function updateQuickConfig(key, value) {
    setForm((prev) => {
      const isTalkingHeadProfile = (props.profile?.()?.id || prev.profile_id) === TALKING_HEAD_PROFILE_ID;
      const nextConfig = normalizeStoryboardQuickConfig({
        ...prev.storyboard_quick_config,
        ...(isTalkingHeadProfile && key === "target_shot_seconds" ? { target_scene_seconds: value } : {}),
        [key]: value,
      });
      const next = {
        ...prev,
        storyboard_quick_config: nextConfig,
        storyboard_final_prompt: "",
      };
      if (isTalkingHeadProfile && key === "target_shot_seconds") {
        next.srt_target_seconds = nextConfig.target_shot_seconds;
      }
      next.storyboard_simple_prompt = isTalkingHeadProfile ? buildTalkingHeadStoryboardSimplePrompt(next) : buildStoryboardSimplePrompt(next);
      return next;
    });
  }

  function clampModalPosition(left, top) {
    const rect = modalEl?.getBoundingClientRect?.();
    const width = rect?.width || 980;
    const height = rect?.height || 640;
    const margin = 12;
    return {
      left: Math.min(Math.max(margin, left), Math.max(margin, window.innerWidth - width - margin)),
      top: Math.min(Math.max(margin, top), Math.max(margin, window.innerHeight - Math.min(height, window.innerHeight - margin * 2) - margin)),
    };
  }

  function stopDrag() {
    window.removeEventListener("pointermove", dragModal);
    window.removeEventListener("pointerup", stopDrag);
    window.removeEventListener("pointercancel", stopDrag);
    dragState = null;
  }

  function dragModal(event) {
    if (!dragState) return;
    setModalPosition(clampModalPosition(event.clientX - dragState.offsetX, event.clientY - dragState.offsetY));
  }

  function startDrag(event) {
    if (event.button !== 0 || event.target?.closest?.("button")) return;
    const rect = modalEl?.getBoundingClientRect?.();
    if (!rect) return;
    event.preventDefault();
    dragState = {
      offsetX: event.clientX - rect.left,
      offsetY: event.clientY - rect.top,
    };
    setModalPosition({ left: rect.left, top: rect.top });
    window.addEventListener("pointermove", dragModal);
    window.addEventListener("pointerup", stopDrag);
    window.addEventListener("pointercancel", stopDrag);
  }

  function modalStyle() {
    const position = modalPosition();
    return position ? { left: `${position.left}px`, top: `${position.top}px`, transform: "none" } : undefined;
  }

  function scriptModeErrorMessage() {
    if (scriptCreationMode() === "user" && !String(form().script || "").trim()) return "请填写用户给定脚本。";
    if (scriptCreationMode() === "rewrite" && !String(form().script || "").trim()) return "智能改写脚本模式必须填写参考脚本。";
    return "";
  }

  function validateScriptMode() {
    if (!isTalkingHead()) return true;
    const message = scriptModeErrorMessage();
    setScriptValidationError(message);
    if (message) setActiveWizardStep("script");
    return !message;
  }

  function selectScriptCreationMode(mode) {
    const previousMode = scriptCreationMode();
    if (mode === previousMode) return;
    setScriptValidationError("");
    setForm((prev) => {
      scriptModeDraftCache.set(previousMode, {
        script: prev.script,
        industry: prev.industry,
        persona: prev.persona,
        target_audience: prev.target_audience,
        video_formula: prev.video_formula,
        product_info: prev.product_info,
        constraints: prev.constraints,
        rewrite_simple_prompt: prev.rewrite_simple_prompt,
        rewrite_final_prompt: prev.rewrite_final_prompt,
      });
      const cached = scriptModeDraftCache.get(mode);
      if (cached) {
        return {
          ...prev,
          ...cached,
          simple_prompt: cached.rewrite_simple_prompt,
        };
      }
      if (mode === "user") return prev;
      const draft = {
        ...prev,
        script: mode === "rewrite" ? "" : prev.script,
        script_creation_mode: mode,
      };
      const rewriteSimple = buildRewriteSimplePrompt(draft);
      return {
        ...prev,
        script: draft.script,
        rewrite_simple_prompt: rewriteSimple,
        rewrite_final_prompt: "",
        simple_prompt: rewriteSimple,
      };
    });
    setScriptCreationMode(mode);
  }

  function generateFinalPrompts() {
    const draft = {
      ...form(),
      script_creation_mode: scriptCreationMode(),
      storyboard_quick_config: normalizeStoryboardQuickConfig(form().storyboard_quick_config),
    };
    if (isTalkingHead()) {
      draft.srt_target_seconds = draft.storyboard_quick_config.target_shot_seconds;
      draft.tempo = decimalNumber(draft.tempo, 1);
    }
    const rewriteSimple = draft.rewrite_simple_prompt.trim() || buildRewriteSimplePrompt(draft);
    const storyboardSimple = draft.storyboard_simple_prompt.trim() || (isTalkingHead() ? buildTalkingHeadStoryboardSimplePrompt(draft) : buildStoryboardSimplePrompt(draft));
    setForm((prev) => ({
      ...prev,
      rewrite_simple_prompt: rewriteSimple,
      storyboard_simple_prompt: storyboardSimple,
      rewrite_final_prompt: [
        rewriteSimple,
        scriptHint(prev.script),
      ].filter(Boolean).join("\n"),
      storyboard_final_prompt: [
        storyboardSimple,
        scriptHint(prev.script),
      ].filter(Boolean).join("\n"),
      storyboard_quick_config: normalizeStoryboardQuickConfig(prev.storyboard_quick_config),
    }));
  }

  function payloadFromForm() {
    const draft = {
      ...form(),
      script_creation_mode: scriptCreationMode(),
      storyboard_quick_config: normalizeStoryboardQuickConfig(form().storyboard_quick_config),
    };
    if (isTalkingHead() && scriptCreationMode() === "generate") draft.script = "";
    if (isTalkingHead()) {
      draft.srt_target_seconds = draft.storyboard_quick_config.target_shot_seconds;
    }
    const rewriteSimple = draft.rewrite_simple_prompt.trim() || buildRewriteSimplePrompt(draft);
    const storyboardSimple = draft.storyboard_simple_prompt.trim() || (isTalkingHead() ? buildTalkingHeadStoryboardSimplePrompt(draft) : buildStoryboardSimplePrompt(draft));
    return {
      ...draft,
      voice_tempo_by_id: Object.fromEntries(
        Object.entries(voiceTempoById()).map(([voiceId, tempo]) => [voiceId, decimalNumber(tempo, 1)]),
      ),
      profile_id: draft.profile_id || props.profile?.()?.id || "script",
      create_mode: draft.create_mode || props.profile?.()?.createMode || "script",
      simple_prompt: rewriteSimple,
      final_prompt: draft.rewrite_final_prompt,
      rewrite_simple_prompt: rewriteSimple,
      storyboard_simple_prompt: storyboardSimple,
    };
  }

  async function submit(action = "save") {
    if (!validateScriptMode()) return;
    await props.onCreate(payloadFromForm(), { action });
    setForm(blankForm(props.promptOptions?.() || {}));
    setActiveTab("rewrite");
    setActiveWizardStep("script");
    scriptModeDraftCache.clear();
    setScriptCreationMode("user");
    setScriptValidationError("");
  }

  async function openPromptDialog(scope = "all") {
    if (!validateScriptMode()) return;
    setPromptError("");
    setPromptGenerationScope(scope);
    setPromptDialogOpen(true);
    try {
      const models = await props.onLoadPromptModels?.();
      const item = findModelPresetItem(models?.items || [], "max") ?? findModelPresetItem(models?.items || [], "flash") ?? models?.items?.[0];
      if (item && (!promptProvider() || !promptModel())) {
        setPromptProvider(item.providerID);
        setPromptModel(item.modelID);
      }
    } catch (err) {
      setPromptError(err instanceof Error ? err.message : "加载模型失败");
    }
  }

  async function runPromptGeneration() {
    setPromptError("");
    const requestedScope = promptGenerationScope();
    const requestedMode = scriptCreationMode();
    promptGenerationInFlight = true;
    try {
      const generate = requestedScope === "rewrite" ? props.onGenerateScriptFinalPrompt : props.onGeneratePrompts;
      const detail = await generate?.(payloadFromForm(), {
        providerID: promptProvider(),
        modelID: promptModel(),
      });
      const generatedTask = detail?.task || detail?.item || detail;
      if (generatedTask?.id) hydrateKey = `task-${generatedTask.id}`;
      if (requestedScope === "rewrite") {
        setForm((prev) => ({
          ...prev,
          rewrite_final_prompt: String(generatedTask?.rewrite_final_prompt || generatedTask?.final_prompt || prev.rewrite_final_prompt || ""),
        }));
      } else {
        setForm((prev) => formFromDetailTask(generatedTask, prev));
      }
      setScriptCreationMode(requestedMode);
      setPromptDialogOpen(false);
    } catch (err) {
      setPromptError(err instanceof Error ? err.message : "生成复杂提示词失败");
    } finally {
      promptGenerationInFlight = false;
    }
  }

  async function loadVoiceClones(showError = true) {
    if (!props.onListVoiceClones) return;
    setVoiceCloneBusy(true);
    if (showError) setVoiceCloneError("");
    try {
      const payload = await props.onListVoiceClones();
      setVoiceClonePayload(payload);
    } catch (err) {
      if (showError) setVoiceCloneError(err instanceof Error ? err.message : "读取云端克隆声音失败");
    } finally {
      setVoiceCloneBusy(false);
    }
  }

  function selectVoiceClone(item) {
    const voiceId = cloudVoiceId(item);
    if (!voiceId) return;
    setForm((prev) => ({
      ...prev,
      voice_id: voiceId,
      voice_label: cloudVoiceName(item),
      voice_provider: "",
      tempo: voiceTempo(item),
    }));
  }

  function selectPortraitFile(file) {
    if (!file) return;
    const fileName = String(file.name || "");
    const fileType = String(file.type || "");
    const isImage = fileType.startsWith("image/") || /\.(png|jpe?g|webp)$/i.test(fileName);
    if (!isImage) {
      setPortraitUploadError("请上传 PNG、JPG 或 WebP 图片");
      return;
    }
    setPortraitUploadError("");
    setForm((prev) => ({ ...prev, portrait_image_file: file, portrait_image_name: fileName }));
  }

  function selectReferenceVideoFile(file) {
    if (!file) return;
    const fileName = String(file.name || "");
    const fileType = String(file.type || "");
    if (!(fileType.startsWith("video/") || /\.(mp4|mov|webm|m4v)$/i.test(fileName))) {
      setReferenceVideoUploadError("请上传 MP4、MOV、WebM 或 M4V 视频");
      return;
    }
    setReferenceVideoUploadError("");
    setForm((prev) => ({ ...prev, reference_video_file: file, reference_video_name: fileName }));
  }

  function referenceVideoDragOver(event) {
    if (form().use_default_reference_video || !talkingHeadModelSupportsReferenceVideo(selectedTalkingHeadVideoModel())) return;
    event.preventDefault();
    event.stopPropagation();
    if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
    setReferenceVideoDragging(true);
  }

  function referenceVideoDragLeave(event) {
    event.preventDefault();
    event.stopPropagation();
    setReferenceVideoDragging(false);
  }

  function referenceVideoDrop(event) {
    event.preventDefault();
    event.stopPropagation();
    setReferenceVideoDragging(false);
    if (!form().use_default_reference_video && talkingHeadModelSupportsReferenceVideo(selectedTalkingHeadVideoModel())) selectReferenceVideoFile(event.dataTransfer?.files?.[0] || null);
  }

  function portraitDragOver(event) {
    event.preventDefault();
    event.stopPropagation();
    if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
    setPortraitDragging(true);
  }

  function portraitDragLeave(event) {
    event.preventDefault();
    event.stopPropagation();
    const related = event.relatedTarget;
    if (related && event.currentTarget.contains(related)) return;
    setPortraitDragging(false);
  }

  function portraitDrop(event) {
    event.preventDefault();
    event.stopPropagation();
    setPortraitDragging(false);
    selectPortraitFile(event.dataTransfer?.files?.[0] || null);
  }

  async function toggleVoicePreview(item) {
    const voiceId = cloudVoiceId(item);
    if (!voiceId || !props.onPreviewVoiceClone) return;
    if (voicePreviewing() === voiceId) {
      stopVoicePreview();
      return;
    }
    stopVoicePreview();
    setVoicePreviewBusy(voiceId);
    setVoiceCloneError("");
    try {
      const result = await props.onPreviewVoiceClone({
        voice_id: voiceId,
        voice_source: "cloud_clone",
        sample_text: "你好，这是一段克隆声音试听。",
        text: "你好，这是一段克隆声音试听。",
        simple_prompt: "你好，这是一段克隆声音试听。",
        complex_prompt: "你好，这是一段克隆声音试听。",
        prompt: "你好，这是一段克隆声音试听。",
        language: "zh",
        tempo: decimalNumber(voiceTempo(item), 1),
      });
      const url = result?.audio_url || result?.url || "";
      if (!url) throw new Error("试听没有返回音频地址");
      const audio = new Audio(url);
      voicePreviewAudio = audio;
      audio.onended = () => {
        if (voicePreviewAudio === audio) {
          voicePreviewAudio = null;
          setVoicePreviewing("");
        }
      };
      audio.onerror = () => {
        if (voicePreviewAudio === audio) {
          voicePreviewAudio = null;
          setVoicePreviewing("");
          setVoiceCloneError("声音试听播放失败");
        }
      };
      setVoicePreviewing(voiceId);
      await audio.play();
    } catch (err) {
      stopVoicePreview();
      setVoiceCloneError(err instanceof Error ? err.message : "声音试听失败");
    } finally {
      setVoicePreviewBusy("");
    }
  }

  const quickConfig = () => normalizeStoryboardQuickConfig(form().storyboard_quick_config);
  const selectedTalkingHeadVideoModel = () => TALKING_HEAD_VIDEO_MODELS.find((item) => item.key === form().talking_head_video_model_key) || TALKING_HEAD_VIDEO_MODELS[0];
  const isTalkingHead = () => (props.profile?.()?.id || form().profile_id) === TALKING_HEAD_PROFILE_ID;
  const activeSimpleField = () => activeTab() === "storyboard" ? "storyboard_simple_prompt" : "rewrite_simple_prompt";
  const activeFinalField = () => activeTab() === "storyboard" ? "storyboard_final_prompt" : "rewrite_final_prompt";
  const promptOptions = () => props.promptOptions?.() || {};
  const voiceClones = () => voiceCloneItems(voiceClonePayload());
  const activeWizardIndex = () => Math.max(0, TALKING_HEAD_WIZARD_STEPS.findIndex((item) => item.id === activeWizardStep()));
  const goWizard = (offset) => {
    if (offset > 0 && activeWizardStep() === "script" && !validateScriptMode()) return;
    const nextIndex = Math.min(Math.max(0, activeWizardIndex() + offset), TALKING_HEAD_WIZARD_STEPS.length - 1);
    setActiveWizardStep(TALKING_HEAD_WIZARD_STEPS[nextIndex].id);
  };
  const renderOptionField = (field, label) => {
    const items = () => optionList(promptOptions(), field);
    const currentOption = () => optionValue(promptOptions(), field, form()[field] || "");
    return (
      <label>
        <span>{label}</span>
        <select value={currentOption()} onChange={(event) => update(field, event.currentTarget.value === "__custom__" ? "" : event.currentTarget.value)}>
          <For each={items()}>{(item) => <option value={item}>{item}</option>}</For>
          <option value="__custom__">自定义</option>
        </select>
        <Show when={currentOption() === "__custom__"}>
          <input value={form()[field] || ""} onInput={(event) => update(field, event.currentTarget.value)} placeholder={`自定义${label}`} />
        </Show>
      </label>
    );
  };

  const renderRewritePromptHighlight = () => {
    const lines = String(form().rewrite_simple_prompt || "").split("\n");
    const linkedTokens = Array.from(new Set([
      form().industry,
      form().persona,
      form().target_audience,
      form().video_formula,
      form().product_info,
      form().constraints,
    ].map((value) => String(value || "").trim()).filter((value) => value && value !== "-"))).sort((left, right) => right.length - left.length);
    const escapedTokens = linkedTokens.map((value) => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
    const tokenPattern = escapedTokens.length ? new RegExp(`(${escapedTokens.join("|")})`, "g") : null;
    const tokenSet = new Set(linkedTokens);
    const lineParts = (line) => {
      if (REWRITE_LINKED_LINE_PREFIXES.some((prefix) => line.startsWith(prefix))) return [{ text: line, linked: true }];
      if (!tokenPattern) return [{ text: line, linked: false }];
      return line.split(tokenPattern).filter(Boolean).map((text) => ({ text, linked: tokenSet.has(text) }));
    };
    return (
      <For each={lines}>{(line, index) => (
        <>
          <For each={lineParts(line)}>{(part) => <span class={part.linked ? "is-linked" : ""}>{part.text}</span>}</For>
          <Show when={index() < lines.length - 1}>{"\n"}</Show>
        </>
      )}</For>
    );
  };

  createEffect(() => {
    if (!props.open() || !isTalkingHead() || voiceClonePayload() || voiceCloneBusy()) return;
    void loadVoiceClones(false);
  });

  const renderScriptIdentityFields = () => (
    <div class="koubo-task-list-form-grid">
      {renderOptionField("industry", "行业")}
      {renderOptionField("persona", "人设")}
      {renderOptionField("target_audience", "目标受众")}
      {renderOptionField("video_formula", "视频公式")}
    </div>
  );

  const renderStoryboardQuickFields = () => (
    <div class="koubo-task-list-form-grid">
      <label><span>场景（秒）</span><input type="number" min="1" step="0.5" value={quickConfig().target_scene_seconds} onInput={(event) => updateQuickConfig("target_scene_seconds", event.currentTarget.value)} /></label>
      <label><span>分镜（秒）</span><input type="number" min="1" step="0.5" value={quickConfig().target_shot_seconds} onInput={(event) => updateQuickConfig("target_shot_seconds", event.currentTarget.value)} /></label>
      <label><span>容忍度（秒）</span><input type="number" min="0" step="0.5" value={quickConfig().split_tolerance_seconds} onInput={(event) => updateQuickConfig("split_tolerance_seconds", event.currentTarget.value)} /></label>
      <label><span>分组方式</span><select value={quickConfig().language_boundary_mode} onChange={(event) => updateQuickConfig("language_boundary_mode", event.currentTarget.value)}><option value="strict">严格</option><option value="balanced">均衡</option><option value="loose">宽松</option></select></label>
    </div>
  );

  const renderTalkingHeadStoryboardFields = () => (
    <div class="koubo-task-list-form-grid koubo-task-list-talking-head-storyboard-grid">
      <label><span>分组方式</span><select value={quickConfig().language_boundary_mode} onChange={(event) => updateQuickConfig("language_boundary_mode", event.currentTarget.value)}><option value="strict">严格</option><option value="balanced">均衡</option><option value="loose">宽松</option></select></label>
    </div>
  );

  const renderTalkingHeadWizard = () => (
    <div class="koubo-task-list-wizard-body">
      <nav class="koubo-task-list-wizard-nav" aria-label="人物口播创建步骤">
        <For each={TALKING_HEAD_WIZARD_STEPS}>
          {(step, index) => (
            <button type="button" class={activeWizardStep() === step.id ? "is-active" : ""} onClick={() => setActiveWizardStep(step.id)}>
              <span>{index() + 1}</span>
              <strong>{step.label}</strong>
              <small>{step.description}</small>
            </button>
          )}
        </For>
      </nav>
      <div class="koubo-task-list-wizard-content">
        <Show when={activeWizardStep() === "script"}>
          <section class={`koubo-task-list-wizard-step koubo-task-list-script-step is-${scriptCreationMode()}-script`}>
            <div class="koubo-task-list-script-mode" role="group" aria-label="脚本创建方式">
              <button
                type="button"
                class={scriptCreationMode() === "user" ? "is-active" : ""}
                aria-pressed={scriptCreationMode() === "user"}
                onClick={() => selectScriptCreationMode("user")}
              >
                <span class="koubo-task-list-script-mode-icon"><FileTextIcon /></span>
                <strong>用户给定脚本</strong>
                <small>直接使用你提供的完整口播脚本</small>
              </button>
              <button
                type="button"
                class={scriptCreationMode() === "generate" ? "is-active" : ""}
                aria-pressed={scriptCreationMode() === "generate"}
                onClick={() => selectScriptCreationMode("generate")}
              >
                <span class="koubo-task-list-script-mode-icon"><SparklesIcon /></span>
                <strong>智能创作脚本</strong>
                <small>由AI根据业务信息创作完整的口播脚本</small>
              </button>
              <button
                type="button"
                class={scriptCreationMode() === "rewrite" ? "is-active" : ""}
                aria-pressed={scriptCreationMode() === "rewrite"}
                onClick={() => selectScriptCreationMode("rewrite")}
              >
                <span class="koubo-task-list-script-mode-icon"><RefreshIcon /></span>
                <strong>智能改写脚本</strong>
                <small>由AI根据参考脚本和业务信息改写口播脚本</small>
              </button>
            </div>
            <Show when={scriptCreationMode() !== "generate"}>
              <label class="koubo-task-list-script-field">
                <span class="koubo-task-list-script-field-label">
                  <span>{scriptCreationMode() === "user" ? "口播脚本（必填）" : "参考脚本（必填）"}</span>
                  <Show when={scriptValidationError()}>
                    <small>{scriptValidationError()}</small>
                  </Show>
                </span>
                <textarea
                  rows={scriptCreationMode() === "rewrite" ? 3 : undefined}
                  value={form().script}
                  onInput={(event) => update("script", event.currentTarget.value)}
                  placeholder={scriptCreationMode() === "user" ? "请粘贴或输入完整口播脚本。" : "请粘贴需要 AI 改写的参考脚本。"}
                />
              </label>
            </Show>
            <Show when={scriptCreationMode() !== "user"}>
              {renderScriptIdentityFields()}
              <div class="koubo-task-list-text-grid">
                <label>
                  <span>产品信息</span>
                  <textarea rows="2" value={form().product_info} onInput={(event) => update("product_info", event.currentTarget.value)} />
                </label>
                <label>
                  <span>约束条件</span>
                  <textarea rows="2" value={form().constraints} onInput={(event) => update("constraints", event.currentTarget.value)} />
                </label>
              </div>
              <div class="koubo-task-list-prompt-grid">
                <label>
                  <span class="koubo-task-list-prompt-label-row">
                    <span>{scriptCreationMode() === "generate" ? "脚本创作简单提示词" : "脚本改写简单提示词"}</span>
                  </span>
                  <div class="koubo-task-list-highlighted-textarea">
                    <pre ref={(el) => { rewritePromptHighlightEl = el; }} aria-hidden="true">{renderRewritePromptHighlight()}</pre>
                    <textarea
                      value={form().rewrite_simple_prompt}
                      onInput={(event) => update("rewrite_simple_prompt", event.currentTarget.value)}
                      onScroll={(event) => {
                        if (!rewritePromptHighlightEl) return;
                        rewritePromptHighlightEl.scrollTop = event.currentTarget.scrollTop;
                        rewritePromptHighlightEl.scrollLeft = event.currentTarget.scrollLeft;
                      }}
                      placeholder={scriptCreationMode() === "generate" ? "输入完整脚本创作方向..." : "输入参考脚本改写方向..."}
                    />
                  </div>
                </label>
                <label>
                  <span class="koubo-task-list-prompt-label-row">
                    <span>{scriptCreationMode() === "generate" ? "脚本创作最终提示词" : "脚本改写最终提示词"}</span>
                    <span class="koubo-task-list-prompt-field-actions">
                      <button
                        type="button"
                        title="拷贝到最终提示词"
                        aria-label="拷贝简单提示词到最终提示词"
                        disabled={!form().rewrite_simple_prompt?.trim()}
                        onClick={() => update("rewrite_final_prompt", form().rewrite_simple_prompt)}
                      >
                        <CopyIcon />
                      </button>
                      <button
                        type="button"
                        title="调用模型创建最终提示词"
                        aria-label="调用模型创建当前最终提示词"
                        disabled={props.promptBusy?.() || !form().rewrite_simple_prompt?.trim()}
                        onClick={() => void openPromptDialog("rewrite")}
                      >
                        <SparklesIcon />
                      </button>
                    </span>
                  </span>
                  <textarea class="koubo-task-list-final-prompt" value={form().rewrite_final_prompt} onInput={(event) => update("rewrite_final_prompt", event.currentTarget.value)} placeholder="点击生成复杂提示词，或手动粘贴最终提示词。" />
                </label>
              </div>
            </Show>
          </section>
        </Show>
        <Show when={activeWizardStep() === "storyboard"}>
          <section class="koubo-task-list-wizard-step">
            {renderTalkingHeadStoryboardFields()}
            <div class="koubo-task-list-prompt-grid koubo-task-list-storyboard-prompt-grid">
              <label>
                <span>故事版创建提示词</span>
                <textarea value={form().storyboard_simple_prompt} onInput={(event) => update("storyboard_simple_prompt", event.currentTarget.value)} placeholder="输入 StoryBoard 分组方向..." />
              </label>
              <label>
                <span>故事版最终提示词</span>
                <textarea class="koubo-task-list-final-prompt" value={form().storyboard_final_prompt} onInput={(event) => update("storyboard_final_prompt", event.currentTarget.value)} placeholder="点击生成复杂提示词，或手动粘贴最终提示词。" />
              </label>
            </div>
          </section>
        </Show>
        <Show when={activeWizardStep() === "avatar"}>
          <section class="koubo-task-list-wizard-step">
            <div class="koubo-task-list-talking-head-model-switch" role="radiogroup" aria-label="人物视频模型">
              <For each={TALKING_HEAD_VIDEO_MODELS}>{(item) => {
                const selected = () => form().talking_head_video_model_key === item.key;
                return (
                  <button
                    type="button"
                    role="radio"
                    aria-checked={selected()}
                    class={selected() ? "is-active" : ""}
                    onClick={() => setForm((prev) => {
                      const current = normalizeStoryboardQuickConfig(prev.storyboard_quick_config);
                      const targetSeconds = item.maxSeconds;
                      const useSystemDefault = talkingHeadModelSupportsReferenceVideo(item) ? prev.use_default_reference_video : true;
                      const next = {
                        ...prev,
                        talking_head_video_model_key: item.key,
                        use_default_reference_video: useSystemDefault,
                        apply_privacy_grid_to_reference_video: useSystemDefault ? false : prev.apply_privacy_grid_to_reference_video,
                        srt_target_seconds: targetSeconds,
                        storyboard_quick_config: {
                          ...current,
                          target_scene_seconds: targetSeconds,
                          target_shot_seconds: targetSeconds,
                          split_tolerance_seconds: targetSeconds >= item.maxSeconds ? 0 : current.split_tolerance_seconds,
                        },
                        storyboard_final_prompt: "",
                      };
                      next.storyboard_simple_prompt = buildTalkingHeadStoryboardSimplePrompt(next);
                      return next;
                    })}
                  >
                    <strong>{item.alias}</strong>
                    <span>{item.tags.map((tag) => <em>{tag}</em>)}</span>
                  </button>
                );
              }}</For>
            </div>
            <div class={`koubo-task-list-avatar-settings-row koubo-task-list-video-duration-settings ${selectedTalkingHeadVideoModel().key === "max_sd_2" ? "has-reference-privacy" : ""}`}>
              <label>
                <span>单个视频长度（最大 {selectedTalkingHeadVideoModel().maxSeconds} 秒）</span>
                <input type="number" min="1" max={selectedTalkingHeadVideoModel().maxSeconds} step="0.5" value={quickConfig().target_shot_seconds} onInput={(event) => {
                  const value = Math.min(Number(event.currentTarget.value) || 1, selectedTalkingHeadVideoModel().maxSeconds);
                  updateQuickConfig("target_shot_seconds", value);
                  if (value >= selectedTalkingHeadVideoModel().maxSeconds) updateQuickConfig("split_tolerance_seconds", 0);
                }} />
              </label>
              <label><span>容忍度（秒）</span><input type="number" min="0" step="0.5" value={quickConfig().split_tolerance_seconds} onInput={(event) => updateQuickConfig("split_tolerance_seconds", event.currentTarget.value)} /></label>
              <label><span>首帧覆盖视频个数</span><input type="number" min="1" step="1" value={form().portrait_segments_per_image} onInput={(event) => update("portrait_segments_per_image", event.currentTarget.value)} /></label>
              <Show when={selectedTalkingHeadVideoModel().key === "max_sd_2"}>
                <label>
                  <span>网格密度与线宽</span>
                  <select value={form().privacy_grid_preset} onChange={(event) => updatePrivacyGridPreset(event.currentTarget.value)}>
                    <For each={PRIVACY_GRID_PRESETS}>{(item) => <option value={item.value}>{item.label}</option>}</For>
                  </select>
                </label>
                <div class="koubo-task-list-talking-head-privacy-checkboxes">
                  <label class="koubo-task-list-checkbox-tight">
                    <input type="checkbox" checked={form().apply_privacy_grid_to_reference_video} disabled={form().use_default_reference_video} onChange={(event) => update("apply_privacy_grid_to_reference_video", event.currentTarget.checked)} />
                    <span>应用视频</span>
                  </label>
                  <label class="koubo-task-list-checkbox-tight">
                    <input type="checkbox" checked={form().apply_privacy_grid_to_target_identity_image} onChange={(event) => update("apply_privacy_grid_to_target_identity_image", event.currentTarget.checked)} />
                    <span>应用目标图</span>
                  </label>
                </div>
              </Show>
            </div>
            <Show when={selectedTalkingHeadVideoModel().key === "max_sd_2"}>
              <Show when={!form().apply_privacy_grid_to_reference_video && !form().apply_privacy_grid_to_target_identity_image}>
                <div class="koubo-task-list-target-error">参考视频和目标人物图均不会添加隐私网格，身份内容将按原始视觉输入发送给模型。</div>
              </Show>
            </Show>
            <div class={`koubo-task-list-talking-head-upload-grid ${talkingHeadModelSupportsReferenceVideo(selectedTalkingHeadVideoModel()) ? "" : "is-single"}`}>
              <label
                class={`koubo-task-list-upload-box koubo-task-list-avatar-upload-row ${portraitDragging() ? "is-dragging" : ""} ${form().portrait_image_name ? "has-file" : ""}`}
                onDragEnter={portraitDragOver}
                onDragOver={portraitDragOver}
                onDragLeave={portraitDragLeave}
                onDrop={portraitDrop}
              >
                <span>人物形象照片</span>
                <strong>{form().portrait_image_name || "选择文件"}</strong>
                <small>{portraitDragging() ? "释放以上传图片" : "作为首帧和后续复用的新图来源"}</small>
                <Show when={portraitUploadError()}><em>{portraitUploadError()}</em></Show>
                <input type="file" accept="image/png,image/jpeg,image/webp" onChange={(event) => { selectPortraitFile(event.currentTarget.files?.[0] || null); event.currentTarget.value = ""; }} />
              </label>
              <Show when={talkingHeadModelSupportsReferenceVideo(selectedTalkingHeadVideoModel())}>
              <div class={`koubo-task-list-reference-video-field ${form().use_default_reference_video ? "is-disabled" : ""}`}>
                <label class="koubo-task-list-default-reference-toggle">
                  <input type="checkbox" checked={form().use_default_reference_video} disabled={!talkingHeadModelSupportsReferenceVideo(selectedTalkingHeadVideoModel())} onChange={(event) => updateUseDefaultReferenceVideo(event.currentTarget.checked)} />
                  <span>使用系统默认参考视频</span>
                </label>
                <label
                  class={`koubo-task-list-upload-box koubo-task-list-avatar-upload-row ${referenceVideoDragging() ? "is-dragging" : ""} ${form().reference_video_name ? "has-file" : ""}`}
                  onDragEnter={referenceVideoDragOver}
                  onDragOver={referenceVideoDragOver}
                  onDragLeave={referenceVideoDragLeave}
                  onDrop={referenceVideoDrop}
                >
                  <span>参考视频</span>
                  <strong>{form().use_default_reference_video ? "系统默认参考视频" : (form().reference_video_name || "选择文件")}</strong>
                  <small>{!talkingHeadModelSupportsReferenceVideo(selectedTalkingHeadVideoModel()) ? "当前模型不支持参考视频" : (form().use_default_reference_video ? "取消默认后可上传新参考视频" : "用于参考人物表情、动作、姿态、节奏、手势和镜头运动")}</small>
                  <Show when={referenceVideoUploadError()}><em>{referenceVideoUploadError()}</em></Show>
                  <input type="file" accept="video/mp4,video/quicktime,video/webm,video/x-m4v" disabled={form().use_default_reference_video || !talkingHeadModelSupportsReferenceVideo(selectedTalkingHeadVideoModel())} onChange={(event) => { selectReferenceVideoFile(event.currentTarget.files?.[0] || null); event.currentTarget.value = ""; }} />
                </label>
              </div>
              </Show>
            </div>
            <section class="koubo-task-list-voice-clone-panel">
              <div class="koubo-task-list-voice-clone-head">
                <div class="koubo-task-list-voice-clone-title-row">
                  <strong>声音选择</strong>
                  <span class="koubo-task-list-voice-count-tag">{voiceClones().length} 个</span>
                  <Show when={isVoiceCreditInsufficientError(voiceCloneError())}>
                    <span class="koubo-task-list-voice-credit-warning">语音模型余额不足，请充值</span>
                  </Show>
                </div>
                <button type="button" class="secondary" disabled={voiceCloneBusy()} onClick={() => void loadVoiceClones(true)}>{voiceCloneBusy() ? "刷新中..." : "刷新"}</button>
              </div>
              <Show when={voiceCloneError() && !isVoiceCreditInsufficientError(voiceCloneError())}>
                <div class="koubo-task-list-banner bad">{voiceCloneError()}</div>
              </Show>
              <Show when={voiceClones().length} fallback={<div class="koubo-task-list-voice-clone-empty">{voiceCloneBusy() ? "正在读取云端克隆声音..." : "暂无云端克隆声音"}</div>}>
                <div class="koubo-task-list-voice-clone-list" role="table" aria-label="云端克隆声音">
                  <div class="koubo-task-list-voice-clone-row is-head" role="row">
                    <span>操作</span>
                    <span>名称</span>
                    <span>语速</span>
                  </div>
                  <For each={voiceClones()}>{(item) => {
                    const voiceId = () => cloudVoiceId(item);
                    const selected = () => voiceId() && form().voice_id === voiceId();
                    return (
                      <div class={`koubo-task-list-voice-clone-row ${selected() ? "is-selected" : ""}`} role="row" title={voiceId()}>
                        <div class="koubo-task-list-voice-clone-actions" role="cell">
                          <button type="button" class="is-icon" title={voicePreviewing() === voiceId() ? "停止试听" : "播放试听"} aria-label={voicePreviewing() === voiceId() ? "停止试听" : "播放试听"} disabled={!voiceId() || voicePreviewBusy() === voiceId()} onClick={() => void toggleVoicePreview(item)}>{voicePreviewing() === voiceId() ? <StopIcon /> : <PlayIcon />}</button>
                          <button type="button" class={`is-icon ${selected() ? "is-selected" : ""}`} title={selected() ? "已选用" : "选用声音"} aria-label={selected() ? "已选用" : "选用声音"} disabled={!voiceId()} onClick={() => selectVoiceClone(item)}><CheckIcon /></button>
                          <button
                            type="button"
                            class={`is-icon ${voiceDeleteConfirmId() === voiceId() ? "is-delete-confirm" : "is-delete"}`}
                            title={voiceDeleteConfirmId() === voiceId() ? "再次点击确认删除" : "删除克隆声音"}
                            aria-label={voiceDeleteConfirmId() === voiceId() ? "确认删除克隆声音" : "删除克隆声音"}
                            disabled={!voiceId() || voiceDeleteBusyId() === voiceId()}
                            onClick={() => void deleteVoiceClone(item)}
                          >
                            <TrashIcon />
                          </button>
                        </div>
                        <div class="koubo-task-list-voice-clone-name" role="cell">
                          <strong>{cloudVoiceName(item)}</strong>
                          <span>{[item.language, item.gender].filter(Boolean).join(" · ") || "云端声音"}</span>
                        </div>
                        <label class="koubo-task-list-voice-row-tempo" role="cell" aria-label={`${cloudVoiceName(item)}语速`}>
                          <input type="text" inputMode="decimal" pattern="[0-9]*[.,]?[0-9]*" value={voiceTempo(item)} onInput={(event) => updateVoiceTempo(item, event.currentTarget.value)} />
                        </label>
                      </div>
                    );
                  }}</For>
                </div>
              </Show>
            </section>
          </section>
        </Show>
        <div class="koubo-task-list-wizard-actions">
          <button type="button" class="secondary" disabled={activeWizardIndex() === 0} onClick={() => goWizard(-1)}>上一步</button>
          <button type="button" disabled={activeWizardIndex() === TALKING_HEAD_WIZARD_STEPS.length - 1} onClick={() => goWizard(1)}>下一步</button>
        </div>
      </div>
    </div>
  );

  return (
    <Show when={props.open()}>
      <div class="koubo-task-list-modal-backdrop" onClick={props.onClose} />
      <section ref={(el) => { modalEl = el; }} class={`koubo-task-list-modal koubo-task-list-script-modal${isTalkingHead() ? " is-talking-head" : ""}`} style={modalStyle()}>
        <header class="koubo-task-list-draggable-header" onPointerDown={startDrag}>
          <div class="koubo-task-list-modal-title-row">
            <h3>{isTalkingHead() ? "人物口播创建" : "脚本生成创建"}</h3>
            <Show when={props.task?.()}>
              {(task) => (
                <div class="koubo-task-list-modal-title-tags" aria-label={`任务 ${task().taskId}，会话 ${task().sessionId}`}>
                  <span>任务 #{task().taskId}</span>
                  <span>会话 #{task().sessionId}</span>
                </div>
              )}
            </Show>
          </div>
          <div class="koubo-task-list-modal-head-actions">
            <button type="button" class="koubo-task-list-icon-action" disabled={props.busy()} title="保存" aria-label="保存" onClick={() => void submit("save")}>
              <SaveIcon />
            </button>
            <Show when={!isTalkingHead() || scriptCreationMode() !== "user"}>
              <button type="button" class="koubo-task-list-icon-action" disabled={props.promptBusy?.()} title="生成全部复杂提示词" aria-label="生成全部复杂提示词" onClick={() => void openPromptDialog("all")}>
                <SparklesIcon />
              </button>
            </Show>
            <button type="button" class="koubo-task-list-icon-action" disabled={props.busy()} title="运行全部流程" aria-label="运行全部流程" onClick={() => void submit("run_all")}>
              {isTalkingHead() ? <WaveformIcon /> : <PlayIcon />}
            </button>
            <button type="button" class="koubo-task-list-close-action" title="关闭" aria-label="关闭" onClick={props.onClose}>
              <XIcon />
            </button>
          </div>
        </header>
        <div class="koubo-task-list-modal-body">
          <Show when={isTalkingHead()} fallback={
            <>
              <label class="koubo-task-list-script-field">
                <span>脚本</span>
                <textarea value={form().script} onInput={(event) => update("script", event.currentTarget.value)} placeholder="粘贴口播脚本；也可以先留空，后续根据提示词生成脚本。" />
              </label>
              {renderScriptIdentityFields()}
              {renderStoryboardQuickFields()}
              <div class="koubo-task-list-text-grid">
                <label>
                  <span>产品信息</span>
                  <textarea value={form().product_info} onInput={(event) => update("product_info", event.currentTarget.value)} />
                </label>
                <label>
                  <span>约束条件</span>
                  <textarea value={form().constraints} onInput={(event) => update("constraints", event.currentTarget.value)} />
                </label>
              </div>
              <section class="koubo-task-list-prompt-workbench">
                <div class="koubo-task-list-prompt-tabs">
                  <button type="button" class={activeTab() === "rewrite" ? "is-active" : ""} onClick={() => setActiveTab("rewrite")}>脚本改写</button>
                  <button type="button" class={activeTab() === "storyboard" ? "is-active" : ""} onClick={() => setActiveTab("storyboard")}>故事版创建</button>
                </div>
                <div class="koubo-task-list-prompt-grid">
                  <label>
                    <span>{activeTab() === "rewrite" ? "脚本改写简单提示词" : "故事版创建简单提示词"}</span>
                    <textarea value={form()[activeSimpleField()]} onInput={(event) => update(activeSimpleField(), event.currentTarget.value)} placeholder={activeTab() === "rewrite" ? "输入 SRT 改写方向..." : "输入 StoryBoard 分组方向..."} />
                  </label>
                  <label>
                    <span>{activeTab() === "rewrite" ? "脚本改写最终提示词" : "故事版创建最终提示词"}</span>
                    <textarea class="koubo-task-list-final-prompt" value={form()[activeFinalField()]} onInput={(event) => update(activeFinalField(), event.currentTarget.value)} placeholder="点击生成复杂提示词，或手动粘贴最终提示词。" />
                  </label>
                </div>
              </section>
            </>
          }>
            {renderTalkingHeadWizard()}
          </Show>
        </div>
      </section>
      <Show when={promptDialogOpen()}>
        <div class="koubo-task-list-prompt-model-backdrop" onClick={() => setPromptDialogOpen(false)} />
        <section class="koubo-task-list-prompt-model-dialog">
          <div class="koubo-task-list-prompt-model-body">
            <ModelPresetCards
              items={props.promptModels?.().items || []}
              provider={promptProvider()}
              model={promptModel()}
              disabled={props.promptBusy?.()}
              onSelect={(selection) => {
                setPromptProvider(selection.providerID);
                setPromptModel(selection.modelID);
              }}
              aria-label="Prompt model preset"
            />
            <Show when={promptError()}>
              <div class="koubo-task-list-banner bad">{promptError()}</div>
            </Show>
          </div>
          <div class="koubo-task-list-prompt-model-actions">
            <button type="button" class="secondary" onClick={() => setPromptDialogOpen(false)}>取消</button>
            <button type="button" disabled={!promptProvider() || !promptModel() || props.promptBusy?.()} onClick={() => void runPromptGeneration()}>{props.promptBusy?.() ? "生成中..." : "生成"}</button>
          </div>
        </section>
      </Show>
    </Show>
  );
}
