import { For, Show, createEffect, createMemo, createSignal, onCleanup, onMount, untrack } from "solid-js";
import { CheckIcon, CloseIcon, CodeIcon, CopyIcon, PlayClipIcon, RefreshIcon, SaveIcon, SlidersIcon, SpeechIcon, StopIcon, TrashIcon, UploadIcon, WaveformIcon } from "../analysisV1Icons.jsx";
import { AUDIO_REFERENCE_PATH, FINAL_ITEMS_PATH, TTS_BUILDER_CANDIDATES_PATH } from "../analysisV1Model.js";
import { emitDebugError } from "../../../../debug/debugAdapter.js";
import { getSharedAudioContext } from "../../shared/audioContext.js";

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

function fileName(path) {
  return String(path || "").split(/[\\/]/).filter(Boolean).pop() || String(path || "");
}

function cleanText(value) {
  return String(value ?? "").trim();
}

function formatSeconds(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "-";
  return `${number.toFixed(2).replace(/0$/, "").replace(/\.0$/, "")}s`;
}

function formatBytes(value) {
  const bytes = Number(value);
  if (!Number.isFinite(bytes) || bytes <= 0) return "-";
  if (bytes < 1024) return `${Math.round(bytes)} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 100 * 1024 ? 1 : 0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(bytes < 10 * 1024 * 1024 ? 1 : 0)} MB`;
}

const REFERENCE_MEDIA_ACCEPT = "audio/*,video/mp4,video/quicktime,video/webm,.wav,.mp3,.m4a,.aac,.flac,.ogg,.opus,.webm,.mp4,.mov,.m4v";
const REFERENCE_MEDIA_EXTENSIONS = new Set([".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".webm", ".mp4", ".mov", ".m4v"]);
const REFERENCE_MEDIA_VIDEO_TYPES = new Set(["video/mp4", "video/quicktime", "video/webm", "video/x-m4v"]);
const REFERENCE_MEDIA_SUPPORT_TEXT = "参考声音支持 WAV/MP3/M4A/AAC/FLAC/OGG/Opus，也支持 MP4/MOV/WebM 视频自动提取音频。";

function fileExtension(value) {
  const name = String(value || "");
  const index = name.lastIndexOf(".");
  return index >= 0 ? name.slice(index).toLowerCase() : "";
}

function isReferenceMediaFile(file) {
  const type = String(file?.type || "").split(";", 1)[0].trim().toLowerCase();
  if (type.startsWith("audio/") || REFERENCE_MEDIA_VIDEO_TYPES.has(type)) return true;
  return REFERENCE_MEDIA_EXTENSIONS.has(fileExtension(file?.name));
}

function referenceUploadErrorMessage(exc, file) {
  const raw = exc instanceof Error ? exc.message : String(exc || "");
  const text = raw.trim() || "上传失败";
  const name = String(file?.name || "参考声音");
  const size = formatBytes(file?.size);
  const fileMeta = size === "-" ? name : `${name}（${size}）`;
  if (/network error|networkerror|failed to fetch|load failed|网络连接中断/i.test(text)) {
    return `上传失败：网络连接中断。文件 ${fileMeta} 可能已传完，但公网隧道/代理或后端抽取音频阶段断开了连接；请重试，或先把视频导出为 WAV/MP3 后上传。`;
  }
  if (/无法从上传文件提取参考声音|reference audio conversion failed|does not contain any stream|matches no streams|invalid data/i.test(text)) {
    return `上传失败：无法从 ${fileMeta} 提取声音。请确认 MP4/MOV/WebM 内有可播放音频轨道，或先导出 WAV/MP3 后上传。`;
  }
  return text;
}

function inferVoiceBadge(sceneProfile) {
  const text = `${sceneProfile?.speaker_profile || ""} ${sceneProfile?.voice_prompt_guidance?.speaker || ""}`;
  if (/女|female|woman|girl/i.test(text)) return "女声";
  if (/男|male|man|boy/i.test(text)) return "男声";
  return "声音";
}

function promptHash(text) {
  let hash = 0;
  const value = String(text || "");
  for (let index = 0; index < value.length; index += 1) {
    hash = ((hash << 5) - hash + value.charCodeAt(index)) | 0;
  }
  return String(hash >>> 0);
}

function candidatePrompt(item) {
  const trustedFields = [
    item?.tts_builder_prompt,
    item?.generation_prompt,
    item?.prompt_text,
  ];
  const trusted = String(trustedFields.find((value) => String(value || "").trim()) || "").trim();
  if (trusted) return trusted;
  if (item?.prompt_path || item?.prompt_source === "Prompt" || item?.prompt_sha256) {
    return String(item?.prompt || "").trim();
  }
  return String(item?.prompt || "").trim();
}

function candidatePromptHash(item) {
  return String(item?.prompt_sha256 || item?.tts_builder_prompt_sha256 || item?.generation_prompt_sha256 || promptHash(candidatePrompt(item))).trim();
}

function candidateDisplayScore(item) {
  const value = Number(item?.match_score ?? item?.score ?? item?.scores?.final_score ?? 0);
  return Number.isFinite(value) ? value : 0;
}

function scoringModeLabel(value) {
  const mode = String(value || "");
  if (mode === "full_speechbrain") return "高精度匹配";
  if (mode === "degraded_resemblyzer_acoustic") return "基础匹配";
  if (mode === "stage1_resemblyzer_acoustic") return "首轮粗筛";
  return mode || "-";
}

const DIMENSION_LABELS = {
  timbre: "音色",
  pitch: "音高",
  pace: "语速",
  articulation: "清晰度",
  texture: "声音质感",
  persona: "性别/年龄",
  style: "风格/口音",
  energy: "能量",
  stability: "稳定性",
  timbre_score: "音色",
  pitch_score: "音高",
  pace_score: "语速",
  articulation_score: "清晰度",
  texture_score: "声音质感",
  persona_score: "性别/年龄",
  style_score: "风格/口音",
  energy_score: "能量",
  stability_score: "稳定性",
};

const DIMENSION_ORDER = [
  ["timbre_score", "音色"],
  ["pitch_score", "音高"],
  ["pace_score", "语速"],
  ["articulation_score", "清晰度"],
  ["texture_score", "声音质感"],
  ["persona_score", "性别/年龄"],
  ["style_score", "风格/口音"],
];

function dimensionLabel(value) {
  const key = String(value || "").trim();
  return DIMENSION_LABELS[key] || key || "-";
}

function dimensionScores(item) {
  const direct = item?.dimension_scores;
  if (direct && typeof direct === "object") return direct;
  const scores = item?.scores;
  return scores && typeof scores === "object" ? scores : {};
}

function dimensionRows(item) {
  const scores = dimensionScores(item);
  return DIMENSION_ORDER
    .map(([key, label]) => {
      const value = Number(scores?.[key]);
      return { key, label, value: Number.isFinite(value) ? Math.max(0, Math.min(100, value)) : Number.NaN };
    })
    .filter((entry) => Number.isFinite(entry.value));
}

function closestDimensionText(item) {
  const best = Array.isArray(item?.explanation?.best_dimensions) ? item.explanation.best_dimensions : [];
  const labels = best.map(dimensionLabel).filter(Boolean);
  if (labels.length) return labels.slice(0, 2).join("、");
  const rows = dimensionRows(item).sort((left, right) => right.value - left.value);
  return rows.slice(0, 2).map((entry) => entry.label).join("、") || "-";
}

function recommendationText(item) {
  const best = closestDimensionText(item);
  const watch = (Array.isArray(item?.explanation?.watch_dimensions) ? item.explanation.watch_dimensions : []).map(dimensionLabel).filter(Boolean).slice(0, 2);
  return watch.length ? `最接近：${best}；试听关注：${watch.join("、")}` : `最接近：${best}`;
}

function qualityLabel(value) {
  const text = String(value || "");
  if (text === "good") return "好";
  if (text === "usable") return "可用";
  if (text === "weak") return "弱";
  return text || "-";
}

function titleCaseLabel(value) {
  const text = String(value || "").trim();
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : "-";
}

function payloadIssue(payload, fallback = "高级匹配执行失败") {
  const reasons = Array.isArray(payload?.blocked_reasons) ? payload.blocked_reasons : [];
  const message = reasons.map((item) => [item?.code, item?.message].filter(Boolean).join(": ")).filter(Boolean).join("\n");
  const text = message || payload?.message || payload?.error || fallback;
  if (/resource_limit_reached|voice clone limit reached/i.test(String(text || ""))) {
    return "云端克隆音色已达账户上限。请在克隆配置里删除不用的云端音色后重试；如果这些音色都要保留，请联系服务管理员提升克隆音色限额。";
  }
  const inUseMatch = String(text || "").match(/Voice is associated with an active template\s+(.+?)\s+please change the voice used in that template first before deleting this voice/i);
  if (/resource_access_denied/i.test(String(text || "")) && inUseMatch) {
    return `云端服务拒绝删除：这个音色正在被模板「${inUseMatch[1].trim()}」使用。请先在服务端模板中把该模板的声音换成其他音色并保存，再回来删除。`;
  }
  if (/clone_delete_not_confirmed|did not confirm deletion|still returned by \/v3\/voices/i.test(String(text || ""))) {
    return "云端服务没有确认删除成功，列表中仍能查到这个音色。请刷新后重试；如果仍失败，可能是该音色正在被模板占用或当前账户不允许删除。";
  }
  if (/voice_not_found/i.test(String(text || ""))) {
    return "云端服务已经找不到这个音色，可能已被其他入口删除。请刷新云端音色列表。";
  }
  return text;
}

function builderPrompt(item) {
  const prompt = candidatePrompt(item);
  return prompt;
}

const TTS_LANGUAGE_OPTIONS = [
  { value: "zh", label: "中文" },
  { value: "en", label: "英文" },
  { value: "auto", label: "自动" },
];

const GEMINI_TTS_VOICE_GENDERS = {
  Achernar: "female",
  Achird: "male",
  Algenib: "male",
  Algieba: "male",
  Alnilam: "male",
  Aoede: "female",
  Autonoe: "female",
  Callirrhoe: "female",
  Charon: "male",
  Despina: "female",
  Enceladus: "male",
  Erinome: "female",
  Fenrir: "male",
  Gacrux: "female",
  Iapetus: "male",
  Kore: "female",
  Laomedeia: "female",
  Leda: "female",
  Orus: "male",
  Puck: "male",
  Pulcherrima: "female",
  Rasalgethi: "male",
  Sadachbia: "male",
  Sadaltager: "male",
  Schedar: "male",
  Sulafat: "female",
  Umbriel: "male",
  Vindemiatrix: "female",
  Zephyr: "female",
  Zubenelgenubi: "male",
};
const NORMAL_TTS_PREVIEW_PROMPT = "欢迎使用 OpenCrew。这是一段常规配音试听，请用自然、清晰、稳定的短视频口播方式朗读。";

const TTS_BUILDER_MODES = {
  quick: { mode: "quick", stepId: "03_02", script: "03_02_TTSBuilderQuick", label: "快速匹配" },
  quick_adv: { mode: "quick_adv", stepId: "03_03", script: "03_03_TTSBuilderQuickAdv", label: "高级匹配" },
};

const TTS_PREVIEW_SCENARIOS = [
  {
    id: "single-basic",
    label: "单说话人基础朗读",
    buildPrompt: (text, language) => `请用${language === "en" ? "英文" : "普通话"}自然朗读正文，声音要清晰、稳定、有真实手机自拍视频口播感。只朗读正文，不要读说明文字。\n\n正文：${text}`,
  },
  {
    id: "short-video-natural",
    label: "短视频自然口播",
    buildPrompt: (text, language) => `请用${language === "en" ? "英文" : "普通话"}生成自然短视频口播。\n\n声音要求：自然、清晰、像手机自拍视频口播；避免硬广、夸张直播腔或机械朗读。\n节奏：中速平稳，重点词轻微强调。\n朗读规则：只朗读正文，不要读说明文字。\n\n正文：${text}`,
  },
  {
    id: "steady-explainer",
    label: "稳定讲解",
    buildPrompt: (text, language) => `请用${language === "en" ? "英文" : "普通话"}稳定讲解下面正文。语气可信、耐心、信息清楚，句尾收稳，不要广告腔。\n\n正文：${text}`,
  },
  {
    id: "expressive-tags",
    label: "情绪/停顿标签",
    buildPrompt: (text, language) => `请用${language === "en" ? "英文" : "普通话"}朗读正文，并自然执行正文中的情绪、停顿、轻重音或括号提示。不要把说明性标签当正文读出。\n\n正文：${text}`,
  },
];

function providerConfig(ttsModelConfig, provider) {
  const value = String(provider || "").toLowerCase();
  return (ttsModelConfig?.providers || []).find((item) => {
    const providerId = String(item?.provider || "").toLowerCase();
    const publicProvider = String(item?.public_provider || item?.provider_alias || "").toLowerCase();
    return providerId === value || publicProvider === value;
  }) || null;
}

function modelConfig(ttsModelConfig, provider, model) {
  const providerItem = providerConfig(ttsModelConfig, provider);
  const value = String(model || "");
  return (providerItem?.models || []).find((item) => {
    const modelId = String(item?.model || "");
    const publicModel = String(item?.public_model || item?.model_alias || "");
    return modelId === value || publicModel === value;
  }) || providerItem?.models?.[0] || null;
}

function normalizedVoiceGender(value) {
  const text = cleanText(value).toLowerCase();
  if (!text) return "";
  if (/^(female|woman|girl|f|女|女声|女性|女生)$/.test(text)) return "female";
  if (/^(male|man|boy|m|男|男声|男性|男生)$/.test(text)) return "male";
  if (/^(neutral|nonbinary|non-binary|中性)$/.test(text)) return "neutral";
  if (/^(unknown|unspecified|未标注)$/.test(text)) return "";
  return text;
}

function voiceGenderLabel(value) {
  const gender = normalizedVoiceGender(value);
  if (gender === "female") return "女声";
  if (gender === "male") return "男声";
  if (gender === "neutral") return "中性";
  return "未标注";
}

function geminiVoiceGender(provider, model, voiceId) {
  const source = `${provider || ""} ${model || ""}`.toLowerCase();
  if (!/(google|gemini)/.test(source)) return "";
  return GEMINI_TTS_VOICE_GENDERS[cleanText(voiceId)] || "";
}

function voiceOptionLabel(voice, provider = "", model = "") {
  const voiceId = cleanText(voice?.voice_id || voice?.id || voice?.value);
  const base = cleanText(voice?.label || voice?.name || voiceId);
  const gender = normalizedVoiceGender(voice?.gender || voice?.sex || voice?.voice_gender) || geminiVoiceGender(provider, model, voiceId);
  const label = voiceGenderLabel(gender);
  return label === "未标注" || base.includes(label) ? base : `${base} · ${label}`;
}

function quickAdvProviderOptions(ttsModelConfig) {
  const providers = (ttsModelConfig?.providers || [])
    .map((item) => ({ provider: publicTTSProviderId(item), label: item.provider_label || item.label || publicTTSProviderId(item), models: item.models || [] }))
    .filter((item) => item.provider && item.models.length);
  return providers;
}

function voiceOptionsForItem(ttsModelConfig, item) {
  const modelItem = modelConfig(ttsModelConfig, item?.provider || "", item?.model || "");
  const voices = Array.isArray(modelItem?.voices) ? modelItem.voices : [];
  const current = String(item?.voice || item?.voice_id || item?.voice_label || "").trim();
  const options = voices.map((voice) => ({
    value: String(voice?.voice_id || "").trim(),
    label: voiceOptionLabel(voice, item?.provider || "", item?.model || ""),
  })).filter((voice) => voice.value);
  if (current && !options.some((voice) => voice.value === current)) {
    options.unshift({ value: current, label: current });
  }
  return options.length ? options : [{ value: current, label: current || "-" }];
}

function defaultVoiceForItem(ttsModelConfig, item) {
  const current = String(item?.voice || item?.voice_id || item?.voice_label || "").trim();
  if (current) return current;
  const providerItem = providerConfig(ttsModelConfig, item?.provider || "");
  const model = String(item?.model || "");
  return String(
    providerItem?.selected_voice_by_public_model?.[model]
    || providerItem?.selected_voice_by_model?.[model]
    || voiceOptionsForItem(ttsModelConfig, item)[0]?.value
    || ""
  ).trim();
}

function modelSupportsNormalVoice(modelItem) {
  const modelId = cleanText(modelItem?.model).toLowerCase();
  const label = cleanText(modelItem?.label).toLowerCase();
  if (/clone/.test(`${modelId} ${label}`)) return false;
  const modes = Array.isArray(modelItem?.voice_modes) ? modelItem.voice_modes.map((item) => cleanText(item).toLowerCase()).filter(Boolean) : [];
  return !modes.length || modes.includes("preset");
}

function normalVoiceModeAllowed(voice) {
  const mode = cleanText(voice?.mode).toLowerCase();
  return !["custom_voice_id", "voice_clone", "cloud_clone", "clone"].includes(mode);
}

function normalVoiceCandidateId(provider, model, voice) {
  return `normal_${safeCandidateToken(provider)}_${safeCandidateToken(model)}_${safeCandidateToken(voice)}`;
}

function publicTTSProviderId(provider) {
  return cleanText(provider?.public_provider || provider?.provider_alias || provider?.provider).toLowerCase();
}

function publicTTSModelId(model) {
  return cleanText(model?.public_model || model?.model_alias || model?.model);
}

function normalVoiceItems(ttsModelConfig) {
  const configProviders = Array.isArray(ttsModelConfig?.providers) ? ttsModelConfig.providers : [];
  const anyKeyConfigured = configProviders.some((provider) => provider?.has_api_key);
  const activeProvider = cleanText(ttsModelConfig?.active_public_provider || ttsModelConfig?.active_provider).toLowerCase();
  const rows = [];
  configProviders.forEach((provider, providerIndex) => {
    const providerId = publicTTSProviderId(provider);
    if (!providerId || provider?.enabled === false) return;
    if (anyKeyConfigured && provider?.has_api_key === false) return;
    const providerLabel = cleanText(provider?.provider_label || provider?.label || providerId);
    const selectedByModel = provider?.selected_voice_by_model && typeof provider.selected_voice_by_model === "object" ? provider.selected_voice_by_model : {};
    const selectedByPublicModel = provider?.selected_voice_by_public_model && typeof provider.selected_voice_by_public_model === "object" ? provider.selected_voice_by_public_model : {};
    const models = Array.isArray(provider?.models) ? provider.models : [];
    models.forEach((model, modelIndex) => {
      const modelId = publicTTSModelId(model);
      if (!modelId || model?.enabled === false || !modelSupportsNormalVoice(model)) return;
      const modelLabel = cleanText(model?.label || modelId);
      const selectedVoice = cleanText(selectedByPublicModel[modelId] || selectedByModel[modelId] || selectedByModel[cleanText(model?.model)]);
      const voices = (Array.isArray(model?.voices) ? model.voices : [])
        .map((voice, voiceIndex) => {
          const voiceId = cleanText(voice?.voice_id || voice?.id || voice?.value);
          if (!voiceId || !normalVoiceModeAllowed(voice)) return null;
          return {
            voice,
            voiceId,
            voiceLabel: cleanText(voice?.label || voice?.name || voiceId),
            voiceGender: normalizedVoiceGender(voice?.gender || voice?.sex || voice?.voice_gender) || geminiVoiceGender(providerId, modelId, voiceId),
            voiceIndex,
          };
        })
        .filter(Boolean)
        .sort((left, right) => {
          if (selectedVoice && left.voiceId === selectedVoice) return -1;
          if (selectedVoice && right.voiceId === selectedVoice) return 1;
          return left.voiceIndex - right.voiceIndex;
        });
      voices.forEach((voiceItem, voiceIndex) => {
        rows.push({
          candidate_id: normalVoiceCandidateId(providerId, modelId, voiceItem.voiceId),
          provider: providerId,
          provider_label: providerLabel,
          model: modelId,
          model_label: modelLabel,
          voice: voiceItem.voiceId,
          voice_id: voiceItem.voiceId,
          voice_label: voiceItem.voiceLabel,
          voice_gender: voiceItem.voiceGender,
          voice_source: "preset",
          prompt: NORMAL_TTS_PREVIEW_PROMPT,
          generation_prompt: NORMAL_TTS_PREVIEW_PROMPT,
          tts_builder_prompt: NORMAL_TTS_PREVIEW_PROMPT,
          score: selectedVoice && voiceItem.voiceId === selectedVoice ? 100 : 90,
          match_score: selectedVoice && voiceItem.voiceId === selectedVoice ? 100 : 90,
          scoring_mode: "preset_voice",
          reason: selectedVoice && voiceItem.voiceId === selectedVoice ? "当前模型默认常规声音，可直接试听和选用。" : "系统常规声音，可直接试听和选用。",
          sort_weight: `${providerId === activeProvider ? "0" : "1"}:${providerIndex}:${modelIndex}:${voiceIndex}`,
        });
      });
    });
  });
  return rows.sort((left, right) => cleanText(left.sort_weight).localeCompare(cleanText(right.sort_weight)));
}

function safeCandidateToken(value) {
  return String(value || "").trim().replace(/[^a-zA-Z0-9_-]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 64) || "clone";
}

function cloneVoiceValue(item) {
  return String(item?.voice || item?.voice_id || "").trim();
}

function cloudVoiceId(item) {
  return String(item?.voice_id || item?.voice_clone_id || item?.voice || item?.id || "").trim();
}

function cloudVoiceName(item) {
  return String(item?.voice_name || item?.name || item?.label || cloudVoiceId(item) || "-").trim();
}

function cloudVoiceInCurrentTask(item, localVoiceIds) {
  return item?.in_current_task === true || localVoiceIds.has(cloudVoiceId(item));
}

function cloudVoiceExists(payload, voiceId) {
  const target = String(voiceId || "").trim();
  if (!target) return false;
  const voices = Array.isArray(payload?.voices) ? payload.voices : [];
  return voices.some((item) => cloudVoiceId(item) === target);
}

function isRedactedModelField(value) {
  return /^\[(?:model|provider)\]$/i.test(String(value || "").trim());
}

function publicModelField(value) {
  const next = cleanText(value);
  return isRedactedModelField(next) ? "" : next;
}

function normalizeCloneProvider(value) {
  const provider = String(value || "").trim().toLowerCase();
  if (!provider || isRedactedModelField(provider)) return "";
  return provider;
}

const CLONE_PREVIEW_PROMPT = "欢迎使用 OpenCrew。这是一段克隆音色试听，请用自然、清晰、稳定的短视频口播方式朗读。";

function isCloudCloneCandidate(item) {
  return String(item?.voice_source || "").trim() === "cloud_clone" || String(item?.candidate_id || "").startsWith("clone_");
}

function cloneCandidateItem(item) {
  const voice = cloneVoiceValue(item);
  const model = publicModelField(item?.target_model || item?.model);
  const provider = normalizeCloneProvider(item?.provider || item?.source_clone_provider);
  return {
    ...(item || {}),
    candidate_id: item?.candidate_id || `clone_${safeCandidateToken(model)}_${safeCandidateToken(voice)}`,
    provider,
    model,
    voice,
    voice_id: voice,
    voice_label: item?.voice_label || item?.label || `克隆音色 ${cloneVoiceSuffix(voice)}`,
    voice_source: "cloud_clone",
    prompt: item?.prompt || CLONE_PREVIEW_PROMPT,
    generation_prompt: item?.generation_prompt || item?.prompt || CLONE_PREVIEW_PROMPT,
    tts_builder_prompt: item?.tts_builder_prompt || item?.prompt || CLONE_PREVIEW_PROMPT,
    sample_audio_path: item?.sample_audio_path || "",
    score: Number(item?.score ?? item?.match_score ?? 100),
    match_score: Number(item?.match_score ?? item?.score ?? 100),
    scores: item?.scores || { scoring_mode: "cloud_clone", final_score: 100 },
    scoring_mode: item?.scoring_mode || "cloud_clone",
    reason: item?.reason || "云端克隆音色，已从参考声音创建。正式使用前请先试听确认。",
  };
}

function candidateDisplayName(item, fallback = "") {
  if (isCloudCloneCandidate(item)) {
    return `克隆音色 ${cloneVoiceSuffix(cloneVoiceValue(item) || item?.voice_label || item?.candidate_id || fallback)}`;
  }
  return item?.voice_label || item?.voice || item?.candidate_id || fallback || "-";
}

function extractPreviewSpeechText(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  const marker = /(?:^|\n)\s*(?:#+\s*)?(?:朗读文本|正文|Text|TRANSCRIPT)\s*[:：]?\s*/gi;
  let match = null;
  let current = marker.exec(text);
  while (current) {
    match = current;
    current = marker.exec(text);
  }
  return match ? text.slice(match.index + match[0].length).trim() : text;
}

function stripPreviewVoiceLine(value) {
  return String(value || "").replace(/^\s*当前\s*voice\s*[:：].*(?:\n|$)/gim, "\n").replace(/\n{3,}/g, "\n\n").trim();
}

function scenarioPrompt(scenarioId, text, language, voice) {
  const scenario = TTS_PREVIEW_SCENARIOS.find((item) => item.id === scenarioId) || TTS_PREVIEW_SCENARIOS[0];
  return scenario.buildPrompt(String(text || "").trim(), language || "zh", voice || "");
}

function validScenarioId(value) {
  const scenarioId = String(value || "").trim();
  return TTS_PREVIEW_SCENARIOS.some((item) => item.id === scenarioId) ? scenarioId : "single-basic";
}

function defaultTempoForItem(item) {
  return String(tempoNumberForItem(item));
}

function tempoNumberForItem(item) {
  const value = Number(item?.tempo || item?.speed_factor || item?.fit_meta?.tempo || 1);
  return Number.isFinite(value) && value > 0 ? Number(value.toFixed(3)) : 1;
}

function cloneVoiceSuffix(value) {
  const text = String(value || "").trim();
  return text ? text.slice(-6) : "-";
}

function sanitizeTempoInput(value) {
  const text = String(value || "").replace(/[^\d.]/g, "");
  const [head, ...tail] = text.split(".");
  return tail.length ? `${head}.${tail.join("")}` : head;
}

function delay(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function isTunnelTimeoutError(value) {
  return /524|cloudflare|隧道超时|a timeout occurred/i.test(String(value || ""));
}

function normalizePromptEditEntry(entry, item) {
  if (!entry || typeof entry !== "object") return "";
  const prompt = String(entry.prompt || "").trim();
  if (!prompt) return "";
  return String(entry.source_hash || "") === candidatePromptHash(item) ? entry : "";
}

function normalizePromptEdit(entry, item) {
  const normalized = normalizePromptEditEntry(entry, item);
  return normalized ? String(normalized.prompt || "").trim() : "";
}

function ReferenceWaveform(props) {
  let canvasEl;
  let trackEl;
  let resizeObserver;
  let activeDrag = null;
  const [phase, setPhase] = createSignal("idle");
  const [peaks, setPeaks] = createSignal([]);
  const [duration, setDuration] = createSignal(0);
  const [trackWidth, setTrackWidth] = createSignal(1);
  const range = createMemo(() => {
    const total = duration() || Number(props.fallbackDuration || 64.55);
    const start = clamp(Number(props.range?.start ?? 0), 0, total);
    const end = clamp(Number(props.range?.end ?? start + 16), start + 0.1, total);
    return { start, end };
  });
  const pct = (time) => {
    const total = duration() || Number(props.fallbackDuration || 64.55);
    return total > 0 ? `${clamp(time / total, 0, 1) * 100}%` : "0%";
  };
  const selectedDuration = createMemo(() => Math.max(0, range().end - range().start));

  function draw() {
    if (!canvasEl) return;
    const width = Math.max(1, Math.floor(trackWidth()));
    const height = 58;
    const dpr = window.devicePixelRatio || 1;
    canvasEl.width = Math.floor(width * dpr);
    canvasEl.height = Math.floor(height * dpr);
    canvasEl.style.width = `${width}px`;
    canvasEl.style.height = `${height}px`;
    const ctx = canvasEl.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = "#f8fafc";
    ctx.fillRect(0, 0, width, height);
    ctx.fillStyle = "#cbd5e1";
    ctx.fillRect(0, height / 2 - 0.5, width, 1);
    const data = peaks();
    const selectedLeft = (range().start / Math.max(0.1, duration() || props.fallbackDuration || 64.55)) * width;
    const selectedRight = (range().end / Math.max(0.1, duration() || props.fallbackDuration || 64.55)) * width;
    const barWidth = Math.max(1, width / Math.max(1, data.length));
    data.forEach((peak, index) => {
      const x = index * barWidth;
      const barHeight = Math.max(2, peak * (height - 12));
      ctx.fillStyle = x >= selectedLeft && x <= selectedRight ? "#2563eb" : "#94a3b8";
      ctx.fillRect(x, height / 2 - barHeight / 2, Math.max(1, barWidth - 1), barHeight);
    });
  }

  function timeFromEvent(event) {
    const rect = trackEl.getBoundingClientRect();
    const total = duration() || Number(props.fallbackDuration || 64.55);
    return clamp(((event.clientX - rect.left) / Math.max(1, rect.width)) * total, 0, total);
  }

  function commit(next) {
    const total = duration() || Number(props.fallbackDuration || 64.55);
    const start = clamp(Number(next.start || 0), 0, Math.max(0, total - 0.1));
    const end = clamp(Number(next.end || start + 0.1), start + 0.1, total);
    props.onRangeChange?.({ start: Number(start.toFixed(3)), end: Number(end.toFixed(3)) });
  }

  function startDrag(mode, event) {
    event.preventDefault();
    event.stopPropagation();
    activeDrag = { mode, x: event.clientX, range: range() };
    window.addEventListener("pointermove", moveDrag, true);
    window.addEventListener("pointerup", stopDrag, true);
    window.addEventListener("pointercancel", stopDrag, true);
  }

  function onTrackPointerDown(event) {
    if (!trackEl || event.button !== 0) return;
    const time = timeFromEvent(event);
    const total = duration() || Number(props.fallbackDuration || 64.55);
    const pixel = (value) => (value / Math.max(0.1, total)) * trackEl.getBoundingClientRect().width;
    const x = (time / Math.max(0.1, total)) * trackEl.getBoundingClientRect().width;
    const leftDist = Math.abs(x - pixel(range().start));
    const rightDist = Math.abs(x - pixel(range().end));
    const mode = leftDist < 10 ? "left" : rightDist < 10 ? "right" : (time > range().start && time < range().end ? "move" : "right");
    if (mode === "right" && !(time > range().start && time < range().end) && time < range().start) commit({ start: time, end: range().end });
    startDrag(mode, event);
  }

  function moveDrag(event) {
    if (!activeDrag) return;
    const time = timeFromEvent(event);
    const total = duration() || Number(props.fallbackDuration || 64.55);
    const current = activeDrag.range;
    if (activeDrag.mode === "left") {
      commit({ start: Math.min(time, current.end - 0.1), end: current.end });
      return;
    }
    if (activeDrag.mode === "right") {
      commit({ start: current.start, end: Math.max(time, current.start + 0.1) });
      return;
    }
    const delta = timeFromEvent(event) - timeFromEvent({ clientX: activeDrag.x });
    const length = current.end - current.start;
    const start = clamp(current.start + delta, 0, Math.max(0, total - length));
    commit({ start, end: start + length });
  }

  function stopDrag() {
    activeDrag = null;
    window.removeEventListener("pointermove", moveDrag, true);
    window.removeEventListener("pointerup", stopDrag, true);
    window.removeEventListener("pointercancel", stopDrag, true);
  }

  createEffect(() => {
    const url = props.audioUrl;
    let canceled = false;
    setPeaks([]);
    setDuration(0);
    setPhase(url ? "loading" : "idle");
    if (!url) return;
    const audioContext = getSharedAudioContext();
    if (!audioContext) {
      setPhase("unsupported");
      return;
    }
    fetch(url, { credentials: "include" })
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.arrayBuffer();
      })
      .then((buffer) => audioContext.decodeAudioData(buffer.slice(0)))
      .then((decoded) => {
        if (canceled) return;
        const channel = decoded.getChannelData(0);
        const bins = 260;
        const samplesPerBin = Math.max(1, Math.floor(channel.length / bins));
        const nextPeaks = Array.from({ length: bins }, (_, bin) => {
          const startIndex = bin * samplesPerBin;
          const endIndex = Math.min(channel.length, startIndex + samplesPerBin);
          let max = 0;
          for (let i = startIndex; i < endIndex; i += 1) max = Math.max(max, Math.abs(channel[i]));
          return Math.min(1, max);
        });
        setDuration(decoded.duration || 0);
        props.onDuration?.(decoded.duration || 0);
        setPeaks(nextPeaks);
        setPhase("ready");
      })
      .catch(() => {
        if (!canceled) setPhase("error");
      });
    onCleanup(() => { canceled = true; });
  });

  createEffect(() => {
    peaks();
    duration();
    trackWidth();
    range();
    draw();
  });

  onMount(() => {
    const updateWidth = () => setTrackWidth(Math.max(1, Math.floor(trackEl?.getBoundingClientRect().width || 1)));
    updateWidth();
    resizeObserver = new ResizeObserver(updateWidth);
    if (trackEl) resizeObserver.observe(trackEl);
  });

  onCleanup(() => {
    resizeObserver?.disconnect();
    stopDrag();
  });

  return <div class={`analysis-v1-tts-waveform ${phase()}`}>
    <div class="analysis-v1-tts-waveform-meta">
      <strong>选择片段</strong>
      <span>{formatSeconds(range().start)} - {formatSeconds(range().end)} / {formatSeconds(selectedDuration())}</span>
      <em>{duration() ? `共 ${formatSeconds(duration())}` : phase() === "loading" ? "载入波形" : ""}</em>
    </div>
    <div class="analysis-v1-tts-waveform-track" ref={trackEl} onPointerDown={onTrackPointerDown}>
      <canvas ref={canvasEl} />
      <Show when={duration() || props.fallbackDuration}>
        <>
          <div class="analysis-v1-tts-waveform-selection" style={{ left: pct(range().start), width: `calc(${pct(range().end)} - ${pct(range().start)})` }} />
          <button type="button" class="analysis-v1-tts-waveform-line is-left" aria-label="拖动开始时间" style={{ left: pct(range().start) }} onPointerDown={(event) => startDrag("left", event)} />
          <button type="button" class="analysis-v1-tts-waveform-line is-right" aria-label="拖动结束时间" style={{ left: pct(range().end) }} onPointerDown={(event) => startDrag("right", event)} />
          <Show when={Number.isFinite(Number(props.playheadTime))}>
            <span class="analysis-v1-tts-waveform-playhead" style={{ left: pct(Number(props.playheadTime)) }} />
          </Show>
        </>
      </Show>
    </div>
  </div>;
}

function TTSPreviewDialog(props) {
  let ttsPromptEl;
  let complexPromptEl;
  const savedMeta = () => props.savedPromptMeta || {};
  const initialScenarioId = () => validScenarioId(savedMeta().scenario_id);
  const initialLanguage = () => TTS_LANGUAGE_OPTIONS.some((item) => item.value === savedMeta().language) ? String(savedMeta().language) : "zh";
  const initialVoiceId = () => String(savedMeta().voice_id || defaultVoiceForItem(props.ttsModelConfig, props.item)).trim();
  const initialTempo = () => String(savedMeta().tempo || defaultTempoForItem(props.item));
  const initialBasePrompt = () => String(savedMeta().base_prompt || builderPrompt(props.item)).trim();
  const initialComplexPrompt = () => stripPreviewVoiceLine(props.savedPrompt) || scenarioPrompt(initialScenarioId(), initialBasePrompt(), initialLanguage(), initialVoiceId());
  const [ttsPrompt, setTtsPrompt] = createSignal(initialBasePrompt());
  const [scenarioId, setScenarioId] = createSignal(initialScenarioId());
  const [language, setLanguage] = createSignal(initialLanguage());
  const [voiceId, setVoiceId] = createSignal(initialVoiceId());
  const [tempo, setTempo] = createSignal(initialTempo());
  const [complexPrompt, setComplexPrompt] = createSignal(initialComplexPrompt());
  const [busy, setBusy] = createSignal("");
  const [error, setError] = createSignal("");
  const [notice, setNotice] = createSignal("");
  const [confirmApply, setConfirmApply] = createSignal(false);
  const [applied, setApplied] = createSignal(false);
  const [saved, setSaved] = createSignal(false);
  const voiceOptions = createMemo(() => voiceOptionsForItem(props.ttsModelConfig, props.item));
  let lastResetKey = "";

  createEffect(() => {
    const key = [
      props.item?.candidate_id,
      props.item?.provider,
      props.item?.model,
      props.item?.voice_id || props.item?.voice || props.item?.voice_label,
    ].map((value) => String(value || "")).join("::");
    if (key === lastResetKey) return;
    lastResetKey = key;
    setTtsPrompt(initialBasePrompt());
    setScenarioId(initialScenarioId());
    setLanguage(initialLanguage());
    setVoiceId(initialVoiceId());
    setTempo(initialTempo());
    setComplexPrompt(initialComplexPrompt());
    setError("");
    setNotice("");
    setConfirmApply(false);
    setApplied(false);
    setSaved(false);
  });

  onMount(() => {
    window.setTimeout(() => {
      const target = complexPromptEl || ttsPromptEl;
      if (!target) return;
      target.focus();
      const end = target.value.length;
      target.setSelectionRange(end, end);
    }, 0);
  });

  function keepFocusEvent(event) {
    event.stopPropagation();
  }

  function focusTextArea(event) {
    event.stopPropagation();
    event.currentTarget.focus();
  }

  function currentPromptDraft() {
    const prompt = stripPreviewVoiceLine(complexPromptEl?.value || ttsPromptEl?.value || complexPrompt());
    const basePrompt = (ttsPromptEl?.value || ttsPrompt()).trim();
    return {
      prompt,
      basePrompt,
      meta: {
        scenario_id: scenarioId(),
        language: language(),
        voice_id: voiceId(),
        tempo: tempo(),
        base_prompt: basePrompt,
      },
    };
  }

  function markDraftChanged() {
    setSaved(false);
    setApplied(false);
    setConfirmApply(false);
    setNotice("");
  }

  async function preview() {
    if (!props.item) return;
    setBusy("preview");
    setError("");
    const promptValue = stripPreviewVoiceLine(complexPromptEl?.value || complexPrompt());
    let previewPayload = null;
    try {
      const isClone = isCloudCloneCandidate(props.item);
      const model = publicModelField(props.item.model || props.item.target_model);
      const provider = isCloudCloneCandidate(props.item)
        ? normalizeCloneProvider(props.item.provider || props.item.source_clone_provider)
        : publicModelField(props.item.provider || "");
      if (!isClone && (!provider || !model)) throw new Error("当前声音缺少公开模型别名，无法生成试听。请刷新声音列表后重试。");
      const selectedVoice = voiceId() || defaultVoiceForItem(props.ttsModelConfig, props.item);
      const tempoValue = Number(tempo());
      if (!Number.isFinite(tempoValue) || tempoValue <= 0) throw new Error("语速必须大于 0，例如 0.9、1、1.1");
      const basePromptValue = (ttsPromptEl?.value || ttsPrompt()).trim();
      const speechText = extractPreviewSpeechText(basePromptValue) || extractPreviewSpeechText(promptValue) || basePromptValue || promptValue;
      previewPayload = {
        provider,
        model,
        target_model: isClone ? publicModelField(props.item.target_model || props.item.model) : "",
        voice_source: isClone ? "cloud_clone" : String(props.item.voice_source || "").trim(),
        source_clone_provider: isClone ? normalizeCloneProvider(props.item.source_clone_provider || props.item.provider) : "",
        voice_id: selectedVoice,
        text: speechText,
        prompt: promptValue,
        candidate_id: props.item.candidate_id || props.item.voice || selectedVoice,
        language: language(),
        tempo: tempoValue,
      };
      const result = props.taskId && props.api.previewTTS
        ? await props.api.previewTTS(props.taskId, previewPayload)
        : await props.api.previewTTSVoice({
          provider,
          model,
          target_model: isClone ? publicModelField(props.item.target_model || props.item.model) : "",
          voice_source: isClone ? "cloud_clone" : String(props.item.voice_source || "").trim(),
          source_clone_provider: isClone ? normalizeCloneProvider(props.item.source_clone_provider || props.item.provider) : "",
          voice_id: selectedVoice,
          sample_text: speechText,
          simple_prompt: speechText,
          complex_prompt: promptValue,
          language: language(),
        });
      const url = result.audio_url || (result.output ? `${props.api.rawFileUrl(props.sessionId, result.output)}?v=${Date.now()}` : "");
      if (!url) throw new Error("TTS 预览没有返回可播放音频");
      props.onPlayPreview?.(url);
    } catch (exc) {
      const message = exc instanceof Error ? exc.message : String(exc);
      const fallbackUrl = props.sampleUrl || (props.sessionId && props.item?.sample_audio_path ? props.api.rawFileUrl(props.sessionId, props.item.sample_audio_path) : "");
      if (props.taskId && props.api.previewTTS && previewPayload && isTunnelTimeoutError(message)) {
        setError("公网隧道超时，后台可能仍在生成试听；正在尝试读取缓存...");
        await delay(6000);
        try {
          const retryResult = await props.api.previewTTS(props.taskId, { ...previewPayload, cache_only: true });
          const retryUrl = retryResult.audio_url || (retryResult.output ? `${props.api.rawFileUrl(props.sessionId, retryResult.output)}?v=${Date.now()}` : "");
          if (!retryUrl) throw new Error("TTS 预览缓存还没有可播放音频");
          props.onPlayPreview?.(retryUrl);
          setError("");
          setNotice("已读取生成完成的试听音频");
          return;
        } catch {
          setError("公网隧道超时，试听生成可能仍在后台进行。请稍后再点播放，或从本地地址访问后重试。");
          return;
        }
      }
      if (fallbackUrl) {
        props.onPlayPreview?.(fallbackUrl);
        setError(`${message}\n\nTTS 试听生成失败，已回放当前 SessionOutput 中的候选音频。`);
      } else {
        setError(message);
      }
    } finally {
      setBusy("");
    }
  }

  function generateComplexPrompt() {
    setBusy("prompt");
    setError("");
    const sourcePrompt = (ttsPromptEl?.value || ttsPrompt()).trim();
    setComplexPrompt(scenarioPrompt(scenarioId(), sourcePrompt, language(), voiceId()));
    window.setTimeout(() => setBusy(""), 180);
  }

  function updateScenario(nextScenarioId) {
    setScenarioId(nextScenarioId);
    setComplexPrompt(scenarioPrompt(nextScenarioId, ttsPromptEl?.value || ttsPrompt(), language(), voiceId()));
    markDraftChanged();
  }

  function updateLanguage(nextLanguage) {
    setLanguage(nextLanguage);
    setComplexPrompt(scenarioPrompt(scenarioId(), ttsPromptEl?.value || ttsPrompt(), nextLanguage, voiceId()));
    markDraftChanged();
  }

  function updateVoice(nextVoiceId) {
    setVoiceId(nextVoiceId);
    setComplexPrompt(scenarioPrompt(scenarioId(), ttsPromptEl?.value || ttsPrompt(), language(), nextVoiceId));
    markDraftChanged();
  }

  function savePrompt() {
    const { prompt, basePrompt, meta } = currentPromptDraft();
    if (!prompt) return;
    setTtsPrompt(basePrompt);
    setComplexPrompt(prompt);
    props.onSavePrompt?.(props.item, prompt, meta);
    setSaved(true);
  }

  function openStoryBoardApplyConfirm() {
    setError("");
    setNotice("");
    setConfirmApply(true);
  }

  async function applyToStoryBoard() {
    const { prompt, basePrompt, meta } = currentPromptDraft();
    if (!prompt) return;
    if (!props.onApplyToStoryBoard) {
      setError("当前页面无法应用到故事版本，请先生成故事版本后再试。");
      return;
    }
    setBusy("storyboard");
    setError("");
    setNotice("");
    try {
      setTtsPrompt(basePrompt);
      setComplexPrompt(prompt);
      props.onSavePrompt?.(props.item, prompt, meta);
      await props.onApplyToStoryBoard(props.item, prompt, meta);
      setSaved(true);
      setApplied(true);
      setConfirmApply(false);
      setNotice("已应用到故事版本口播；重新生成 Scene / Dialogue 音频后可听到变化。");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy("");
    }
  }

  return <section
    class="verify-dialog analysis-v1-tts-preview-dialog"
    onMouseDown={(event) => event.stopPropagation()}
    onClick={(event) => event.stopPropagation()}
  >
    <div class="env-dialog-head analysis-v1-tts-preview-head">
      <div>
        <h3>试听调音</h3>
      </div>
      <div class="analysis-v1-tts-preview-head-actions">
        <button class={`icon-action analysis-v1-tts-preview-action ${applied() ? "is-applied" : ""}`} type="button" title={applied() ? "已应用到故事版本" : "应用到故事版本"} aria-label={applied() ? "已应用到故事版本" : "应用到故事版本"} disabled={busy() || !complexPrompt().trim() || !props.onApplyToStoryBoard} onClick={(event) => { event.stopPropagation(); openStoryBoardApplyConfirm(); }}><Show when={applied()} fallback={<SaveIcon />}><CheckIcon /></Show></button>
        <button class="icon-action analysis-v1-tts-preview-action" type="button" title="生成复杂提示词" aria-label="生成复杂提示词" disabled={busy()} onClick={(event) => { event.stopPropagation(); generateComplexPrompt(); }}><CodeIcon /></button>
        <button class="icon-action analysis-v1-tts-preview-action" type="button" title={saved() ? "已保存提示词" : "保存提示词"} aria-label={saved() ? "已保存提示词" : "保存提示词"} disabled={busy() || !complexPrompt().trim()} onClick={(event) => { event.stopPropagation(); savePrompt(); }}><Show when={saved()} fallback={<SaveIcon />}><CheckIcon /></Show></button>
        <button class="icon-action analysis-v1-tts-preview-action is-primary" type="button" title="播放预览" aria-label="播放预览" disabled={busy() || !complexPrompt().trim()} onClick={(event) => { event.stopPropagation(); void preview(); }}><PlayClipIcon /></button>
      </div>
      <button class="secondary analysis-v1-tts-close" type="button" title="关闭" aria-label="关闭" onClick={props.onClose}><CloseIcon /></button>
    </div>
    <div class="analysis-v1-tts-preview-body">
      <div class="analysis-v1-tts-preview-grid">
        <label class="analysis-v1-tts-preview-field"><span>试听模板</span><select value={scenarioId()} onChange={(event) => updateScenario(event.currentTarget.value)}><For each={TTS_PREVIEW_SCENARIOS}>{(scenario) => <option value={scenario.id}>{scenario.label}</option>}</For></select></label>
        <label class="analysis-v1-tts-preview-field"><span>声音</span><select value={voiceId()} onChange={(event) => updateVoice(event.currentTarget.value)}><For each={voiceOptions()}>{(voice) => <option value={voice.value}>{voice.label || voice.value}</option>}</For></select></label>
        <label class="analysis-v1-tts-preview-field"><span>语言</span><select value={language()} onChange={(event) => updateLanguage(event.currentTarget.value)}><For each={TTS_LANGUAGE_OPTIONS}>{(item) => <option value={item.value}>{item.label}</option>}</For></select></label>
        <label class="analysis-v1-tts-preview-field"><span>语速</span><input type="text" inputmode="decimal" pattern="[0-9.]*" value={tempo()} onInput={(event) => { const next = sanitizeTempoInput(event.currentTarget.value); event.currentTarget.value = next; setTempo(next); markDraftChanged(); }} /></label>
      </div>
      <div class="analysis-v1-tts-preview-apply">
        <Show when={confirmApply()}>
          <div class="analysis-v1-tts-apply-confirm">
            <div>
              <strong>确认应用到整个故事版本口播</strong>
              <span>当前试听模板、音色和语速会写入故事版本；已有音频需要重新生成。</span>
            </div>
            <div class="analysis-v1-tts-apply-confirm-actions">
              <button class="secondary" type="button" disabled={busy()} onClick={(event) => { event.stopPropagation(); setConfirmApply(false); }}>取消</button>
              <button class="primary" type="button" disabled={busy()} onClick={(event) => { event.stopPropagation(); void applyToStoryBoard(); }}>{busy() === "storyboard" ? "应用中..." : "确认应用"}</button>
            </div>
          </div>
        </Show>
      </div>
      <Show when={notice()}><div class="banner good analysis-v1-tts-apply-status">{notice()}</div></Show>
      <div class="analysis-v1-tts-preview-field"><span>音色提示词</span><textarea
        ref={(el) => { ttsPromptEl = el; }}
        aria-label="音色提示词"
        tabindex="0"
        spellcheck={false}
        value={ttsPrompt()}
        onPointerDown={focusTextArea}
        onMouseDown={focusTextArea}
        onFocus={keepFocusEvent}
        onClick={keepFocusEvent}
        onKeyDown={keepFocusEvent}
        onKeyUp={keepFocusEvent}
        onBeforeInput={keepFocusEvent}
        onCompositionStart={keepFocusEvent}
        onCompositionUpdate={keepFocusEvent}
        onCompositionEnd={keepFocusEvent}
        onInput={(event) => { event.stopPropagation(); setTtsPrompt(event.currentTarget.value); setComplexPrompt(scenarioPrompt(scenarioId(), event.currentTarget.value, language(), voiceId())); markDraftChanged(); }}
        onChange={(event) => { event.stopPropagation(); setTtsPrompt(event.currentTarget.value); setComplexPrompt(scenarioPrompt(scenarioId(), event.currentTarget.value, language(), voiceId())); markDraftChanged(); }}
      /></div>
      <div class="analysis-v1-tts-preview-field"><span>试听提示词</span><textarea
        ref={(el) => { complexPromptEl = el; }}
        aria-label="试听提示词"
        tabindex="0"
        spellcheck={false}
        class="analysis-v1-tts-complex-preview"
        value={complexPrompt()}
        onPointerDown={focusTextArea}
        onMouseDown={focusTextArea}
        onFocus={keepFocusEvent}
        onClick={keepFocusEvent}
        onKeyDown={keepFocusEvent}
        onKeyUp={keepFocusEvent}
        onBeforeInput={keepFocusEvent}
        onCompositionStart={keepFocusEvent}
        onCompositionUpdate={keepFocusEvent}
        onCompositionEnd={keepFocusEvent}
        onInput={(event) => { event.stopPropagation(); setComplexPrompt(event.currentTarget.value); markDraftChanged(); }}
        onChange={(event) => { event.stopPropagation(); setComplexPrompt(event.currentTarget.value); markDraftChanged(); }}
      /></div>
      <Show when={error()}><div class="banner bad analysis-v1-tts-error">{error()}</div></Show>
    </div>
  </section>;
}

export default function AnalysisV1TTSBuilder(props) {
  let audioEl;
  let uploadInput;
  let rafId = 0;
  let candidateReloadTimerIds = [];
  const [playingKey, setPlayingKey] = createSignal("");
  const [selectedKey, setSelectedKey] = createSignal("");
  const [dialogPosition, setDialogPosition] = createSignal(null);
  const [referenceDuration, setReferenceDuration] = createSignal(64.55);
  const [range, setRange] = createSignal({ start: 0, end: 16 });
  const [playheadTime, setPlayheadTime] = createSignal(Number.NaN);
  const [busy, setBusy] = createSignal("");
  const [status, setStatus] = createSignal("");
  const [error, setError] = createSignal("");
  const [uploadProgress, setUploadProgress] = createSignal(null);
  const [previewItem, setPreviewItem] = createSignal(null);
  const [promptEdits, setPromptEdits] = createSignal({});
  const [generatedPreviewPaths, setGeneratedPreviewPaths] = createSignal({});
  const [infoKey, setInfoKey] = createSignal("");
  const [voiceView, setVoiceView] = createSignal("normal");
  const [voiceViewTouched, setVoiceViewTouched] = createSignal(false);
  const [localTtsPayload, setLocalTtsPayload] = createSignal(null);
  const [requiresCloudCloneRefresh, setRequiresCloudCloneRefresh] = createSignal(Boolean(props.ttsPayload?.requires_cloud_clone_refresh));
  const [advState, setAdvState] = createSignal(null);
  const [advCatalog, setAdvCatalog] = createSignal(null);
  const [advBusy, setAdvBusy] = createSignal("");
  const [advStatus, setAdvStatus] = createSignal("");
  const [advError, setAdvError] = createSignal("");
  const [advStage1Count, setAdvStage1Count] = createSignal("24");
  const [advStage2Count, setAdvStage2Count] = createSignal("6");
  const [advFinalCount, setAdvFinalCount] = createSignal("3");
  const [advSpeechbrain, setAdvSpeechbrain] = createSignal(true);
  const [advProvider, setAdvProvider] = createSignal("");
  const [advModel, setAdvModel] = createSignal("");
  const [advProviderTouched, setAdvProviderTouched] = createSignal(false);
  const [clonePrefix, setClonePrefix] = createSignal("ocadv");
  const [cloneNote, setCloneNote] = createSignal("");
  const [cloudClonePayload, setCloudClonePayload] = createSignal(null);
  const [cloneDeleteVoiceId, setCloneDeleteVoiceId] = createSignal("");
  const [cloneDeleteConfirmVoiceId, setCloneDeleteConfirmVoiceId] = createSignal("");
  const [cloneImportVoiceId, setCloneImportVoiceId] = createSignal("");
  const [matchConfigOpen, setMatchConfigOpen] = createSignal(false);
  const [cloneConfigOpen, setCloneConfigOpen] = createSignal(false);
  const [cloneConsentOpen, setCloneConsentOpen] = createSignal(false);
  let activeDialogDrag = null;

  function emitTtsBuilderDebugError(error, detail) {
    emitDebugError(error, {
      family: "analysis_v1_tts_builder",
      task_id: props.taskId || null,
      session_id: props.sessionId || null,
      detail,
    });
  }

  const payload = createMemo(() => localTtsPayload() || props.ttsPayload || {});
  const rawCandidates = createMemo(() => Array.isArray(payload().candidates) ? payload().candidates : []);
  const advClones = createMemo(() => Array.isArray(advState()?.cloned_voices) ? advState().cloned_voices : []);
  const localCloneVoiceIds = createMemo(() => new Set(advClones().map((item) => cloneVoiceValue(item)).filter(Boolean)));
  const cloudCloneVoices = createMemo(() => {
    const result = cloudClonePayload();
    if (Array.isArray(result?.voices)) return result.voices;
    if (Array.isArray(result?.data)) return result.data;
    return [];
  });
  const cloudCloneCount = createMemo(() => {
    const value = Number(cloudClonePayload()?.count);
    return Number.isFinite(value) ? value : cloudCloneVoices().length;
  });
  const cloneCandidates = createMemo(() => requiresCloudCloneRefresh()
    ? []
    : advClones().map(cloneCandidateItem).filter((item) => item.voice_id));
  const normalVoices = createMemo(() => normalVoiceItems(props.ttsModelConfig));
  const candidates = createMemo(() => {
    const rows = requiresCloudCloneRefresh()
      ? rawCandidates().filter((item) => !isCloudCloneCandidate(item))
      : rawCandidates();
    const existingKeys = new Set(rows.map((item, index) => candidateKey(item, index)));
    const existingVoices = new Set(rows.map((item) => `${item?.model || ""}::${item?.voice_id || item?.voice || ""}::${item?.voice_source || ""}`));
    const missingClones = cloneCandidates().filter((item) => !existingKeys.has(candidateKey(item)) && !existingVoices.has(`${item.model}::${item.voice_id}::cloud_clone`));
    return [...missingClones, ...rows];
  });
  const sceneProfile = createMemo(() => payload().scene_profile || {});
  const samplePolicy = createMemo(() => payload().sample_policy || {});
  const infoItem = createMemo(() => candidates().find((item, index) => candidateKey(item, index) === infoKey()) || null);
  const advRanking = createMemo(() => advState()?.ranking_board || null);
  const advReference = createMemo(() => advState()?.reference?.profile || advRanking()?.reference_profile || null);
  const advSamplingAudit = createMemo(() => advState()?.reference?.sampling_audit || null);
  const advRows = createMemo(() => {
    const board = advRanking();
    const stage2 = Array.isArray(board?.stage2) ? board.stage2 : [];
    const recommended = Array.isArray(board?.recommended) ? board.recommended : [];
    return stage2.length ? stage2 : recommended;
  });
  const advRecommended = createMemo(() => Array.isArray(advRanking()?.recommended) ? advRanking().recommended : []);
  const advProviders = createMemo(() => quickAdvProviderOptions(props.ttsModelConfig));
  const advModels = createMemo(() => advProviders().find((item) => item.provider === advProvider())?.models || []);
  const referenceAudioUrl = createMemo(() => props.sessionId ? `${props.api.rawFileUrl(props.sessionId, AUDIO_REFERENCE_PATH)}?v=${props.cacheKey || ""}` : "");
  const previewCacheKey = (item, key = "") => {
    const baseKey = key || candidateKey(item);
    return isCloudCloneCandidate(item) ? `${baseKey}:tempo:${tempoNumberForItem(item)}` : baseKey;
  };
  const candidateAudioRel = (item, key = "") => String(item?.sample_audio_path || item?.preview_audio_path || generatedPreviewPaths()[previewCacheKey(item, key)] || "").trim();
  const candidateAudioUrl = (item, key = "") => {
    const rel = candidateAudioRel(item, key);
    if (!props.sessionId || !rel) return "";
    if (/^[a-z][a-z0-9+.-]*:/i.test(rel)) return rel;
    if (rel.startsWith("/")) return "";
    return `${props.api.rawFileUrl(props.sessionId, rel)}?v=${props.cacheKey || ""}`;
  };
  const catalogAudioUrl = (item) => {
    const catalogRel = String(item?.catalog_index_item?.sample_audio_path || item?.catalog_index_item?.audio?.path || item?.sample_audio_path || "").trim();
    const model = String(item?.model || item?.catalog_index_item?.model || "").trim();
    if (props.taskId && catalogRel && model && props.api?.voiceCatalogAudioUrl && !catalogRel.startsWith("/") && !/^[a-z][a-z0-9+.-]*:/i.test(catalogRel)) {
      return `${props.api.voiceCatalogAudioUrl(props.taskId, model, catalogRel)}?v=${props.cacheKey || ""}`;
    }
    const rel = String(item?.catalog_audio_path || "").trim();
    if (!props.sessionId || !rel || rel.startsWith("/") || /^[a-z][a-z0-9+.-]*:/i.test(rel)) return "";
    return `${props.api.rawFileUrl(props.sessionId, rel)}?v=${props.cacheKey || ""}`;
  };
  const referenceLabel = createMemo(() => fileName(AUDIO_REFERENCE_PATH));
  const uploadPercent = createMemo(() => {
    const value = Number(uploadProgress()?.percent);
    if (!Number.isFinite(value)) return 0;
    return Math.max(0, Math.min(100, Math.round(value)));
  });
  const uploadProgressMeta = createMemo(() => {
    const progress = uploadProgress();
    if (!progress) return "";
    const loaded = formatBytes(progress.loaded);
    const total = formatBytes(progress.total || progress.size);
    if (progress.phase === "done") {
      const savedSize = formatBytes(progress.size || progress.total);
      return savedSize === "-" ? "已保存" : `${savedSize} · 已保存`;
    }
    if (progress.total || progress.size) return `${loaded} / ${total}`;
    return "正在上传";
  });
  const selectionStorageKey = createMemo(() => `analysis-v1-tts-selection:${props.sessionId || "draft"}`);
  const promptStorageKey = createMemo(() => `analysis-v1-tts-prompt-edits:${props.sessionId || "draft"}`);
  const cloneMode = createMemo(() => String(props.mode || "") === "clone");

  function chooseVoiceView(nextView) {
    setVoiceView(nextView === "match" ? "match" : "normal");
    setVoiceViewTouched(true);
  }

  createEffect(() => {
    if (cloneMode() || voiceViewTouched()) return;
    setVoiceView(candidates().length ? "match" : "normal");
  });

  createEffect(() => {
    const nextPayload = props.ttsPayload || null;
    props.sessionId;
    setLocalTtsPayload(nextPayload);
    setRequiresCloudCloneRefresh(Boolean(nextPayload?.requires_cloud_clone_refresh));
  });

  createEffect(() => {
    props.sessionId;
    setGeneratedPreviewPaths({});
  });

  onMount(() => {
    setAdvSpeechbrain(true);
  });

  createEffect(() => {
    const providers = advProviders();
    if (!providers.length) return;
    const activeProvider = String(props.ttsModelConfig?.active_public_provider || props.ttsModelConfig?.active_provider || "").toLowerCase();
    const preferred = providers.find((item) => item.provider === activeProvider) || providers[0];
    if (!advProviderTouched() && preferred && advProvider() !== preferred.provider) {
      setAdvProvider(preferred.provider);
      setAdvModel(String(preferred.models?.[0]?.model || advModel()));
      return;
    }
    if (!providers.some((item) => item.provider === advProvider())) {
      setAdvProvider(preferred.provider);
      setAdvModel(String(preferred.models?.[0]?.model || advModel()));
    }
  });

  createEffect(() => {
    const models = advModels();
    if (!models.length) return;
    if (!models.some((item) => String(item.model || "") === advModel())) {
      setAdvModel(String(models[0].model || ""));
    }
  });

  createEffect(() => {
    const selected = samplePolicy()?.selected_range || {};
    const start = Number(selected.start ?? 0);
    const end = Number(selected.end ?? start + Number(samplePolicy()?.selected_duration || 16));
    if (Number.isFinite(start) && Number.isFinite(end) && end > start) {
      setRange((current) => (
        Math.abs(Number(current.start || 0) - start) < 0.001 && Math.abs(Number(current.end || 0) - end) < 0.001
          ? current
          : { start, end }
      ));
    }
  });

  function tickPlayhead() {
    if (!audioEl || playingKey() !== "reference") return;
    setPlayheadTime(audioEl.currentTime);
    if (audioEl.currentTime >= range().end) {
      audioEl.pause();
      setPlayingKey("");
      setPlayheadTime(Number.NaN);
      return;
    }
    rafId = window.requestAnimationFrame(tickPlayhead);
  }

  function play(key, url, options = {}) {
    if (!url) return;
    if (playingKey() === key) {
      audioEl?.pause();
      setPlayingKey("");
      setPlayheadTime(Number.NaN);
      return;
    }
    if (!audioEl) audioEl = new Audio();
    window.cancelAnimationFrame(rafId);
    audioEl.pause();
    audioEl.src = url;
    if (options.selection) audioEl.currentTime = range().start;
    audioEl.onended = () => {
      setPlayingKey("");
      setPlayheadTime(Number.NaN);
    };
    audioEl.onerror = () => {
      setPlayingKey("");
      setPlayheadTime(Number.NaN);
    };
    void audioEl.play().then(() => {
      setPlayingKey(key);
      if (options.selection) tickPlayhead();
    }).catch(() => setPlayingKey(""));
  }

  function previewModelForItem(item) {
    return publicModelField(item?.model || item?.target_model);
  }

  function previewProviderForItem(item) {
    if (isCloudCloneCandidate(item)) {
      const model = previewModelForItem(item);
      return normalizeCloneProvider(item?.provider || item?.source_clone_provider);
    }
    return cleanText(item?.provider).toLowerCase();
  }

  function previewVoiceForItem(item) {
    if (isCloudCloneCandidate(item)) return cloneVoiceValue(item);
    return cleanText(item?.voice_id || item?.voice || item?.voice_label);
  }

  function previewPromptForItem(item) {
    const fallback = isCloudCloneCandidate(item) ? CLONE_PREVIEW_PROMPT : NORMAL_TTS_PREVIEW_PROMPT;
    return cleanText(item?.tts_builder_prompt || item?.generation_prompt || item?.prompt || fallback) || fallback;
  }

  function canGenerateCandidatePreview(item) {
    if (isCloudCloneCandidate(item)) {
      return Boolean(props.taskId && props.sessionId && props.api.previewTTS && previewVoiceForItem(item));
    }
    return Boolean(props.taskId && props.sessionId && props.api.previewTTS && previewProviderForItem(item) && previewModelForItem(item) && previewVoiceForItem(item));
  }

  async function playCandidate(key, item) {
    const playKey = previewCacheKey(item, key);
    const currentUrl = candidateAudioUrl(item, key);
    if (currentUrl) {
      play(playKey, currentUrl);
      return;
    }
    if (!canGenerateCandidatePreview(item)) return;
    const busyKey = `preview:${playKey}`;
    const isClone = isCloudCloneCandidate(item);
    const previewLabel = isClone ? "克隆音色" : "声音";
    let previewPayload = null;
    setBusy(busyKey);
    setError("");
    setStatus(`正在生成${previewLabel}试听...`);
    try {
      const model = previewModelForItem(item);
      const provider = previewProviderForItem(item);
      const voiceId = previewVoiceForItem(item);
      const prompt = previewPromptForItem(item);
      const speechText = extractPreviewSpeechText(prompt) || (isClone ? CLONE_PREVIEW_PROMPT : NORMAL_TTS_PREVIEW_PROMPT);
      const tempoValue = tempoNumberForItem(item);
      previewPayload = {
        provider,
        model,
        target_model: isClone ? publicModelField(item?.target_model || item?.model) : "",
        voice_source: isClone ? "cloud_clone" : String(item?.voice_source || "").trim(),
        source_clone_provider: isClone ? normalizeCloneProvider(item?.source_clone_provider || item?.provider) : "",
        voice_id: voiceId,
        text: speechText,
        prompt,
        candidate_id: item.candidate_id || item.voice || voiceId,
        language: "zh",
        tempo: tempoValue,
      };
      const playPreviewResult = (result, nextStatus) => {
        const outputRel = String(result?.output || result?.raw_output || "").trim();
        const outputUrl = result?.audio_url || (outputRel ? `${props.api.rawFileUrl(props.sessionId, outputRel)}?v=${Date.now()}` : "");
        if (!outputUrl) throw new Error("TTS 预览没有返回可播放音频");
        if (outputRel) {
          setGeneratedPreviewPaths((current) => ({ ...current, [playKey]: outputRel }));
        }
        play(playKey, outputUrl);
        setStatus(nextStatus);
      };
      const result = await props.api.previewTTS(props.taskId, previewPayload);
      playPreviewResult(result, `已生成${previewLabel}试听`);
    } catch (exc) {
      const message = exc instanceof Error ? exc.message : String(exc);
      if (isTunnelTimeoutError(message) && previewPayload) {
        setStatus("公网隧道超时，后台可能仍在生成试听；正在尝试读取缓存...");
        await delay(6000);
        try {
          const retryResult = await props.api.previewTTS(props.taskId, {
            ...previewPayload,
            cache_only: true,
          });
          const outputRel = String(retryResult?.output || retryResult?.raw_output || "").trim();
          const outputUrl = retryResult?.audio_url || (outputRel ? `${props.api.rawFileUrl(props.sessionId, outputRel)}?v=${Date.now()}` : "");
          if (!outputUrl) throw new Error("TTS 预览缓存还没有可播放音频");
          if (outputRel) {
            setGeneratedPreviewPaths((current) => ({ ...current, [playKey]: outputRel }));
          }
          play(playKey, outputUrl);
          setError("");
          setStatus(`已读取生成完成的${previewLabel}试听`);
        } catch {
          setError("公网隧道超时，试听生成可能仍在后台进行。请稍后再点播放，或从本地地址访问后重试。");
          setStatus("");
        }
      } else {
        setError(message);
      }
    } finally {
      setBusy("");
    }
  }

  async function copyReferencePath() {
    const text = AUDIO_REFERENCE_PATH;
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const el = document.createElement("textarea");
      el.value = text;
      document.body.appendChild(el);
      el.select();
      document.execCommand("copy");
      el.remove();
    }
    setStatus("已复制参考声音路径");
  }

  function hasCandidates(nextPayload) {
    return Array.isArray(nextPayload?.candidates) && nextPayload.candidates.length > 0;
  }

  async function reloadCandidatesFromSession() {
    if (!props.taskId && (!props.sessionId || !props.api?.readWorkspaceJson)) return null;
    const nextPayload = props.taskId && props.api?.ttsBuilderCandidates
      ? await props.api.ttsBuilderCandidates(props.taskId)
      : await props.api.readWorkspaceJson(props.sessionId, TTS_BUILDER_CANDIDATES_PATH);
    if (nextPayload && typeof nextPayload === "object" && Array.isArray(nextPayload.candidates)) {
      setLocalTtsPayload(nextPayload);
      setRequiresCloudCloneRefresh(Boolean(nextPayload.requires_cloud_clone_refresh));
    }
    return nextPayload || null;
  }

  function clearCandidateReloadTimers() {
    candidateReloadTimerIds.forEach((timerId) => window.clearTimeout(timerId));
    candidateReloadTimerIds = [];
  }

  function scheduleCandidateReloads() {
    clearCandidateReloadTimers();
    candidateReloadTimerIds = [1200, 3000, 7000, 15000, 30000, 60000, 120000, 240000, 480000, 900000].map((delay) => window.setTimeout(() => {
      void reloadCandidatesFromSession().catch((err) => emitTtsBuilderDebugError(err, "Scheduled candidate reload failed"));
    }, delay));
  }

  function boundedCount(value, fallback, max) {
    const number = Number(value);
    if (!Number.isFinite(number)) return fallback;
    return Math.max(1, Math.min(max, Math.round(number)));
  }

  function quickAdvPayload(extra = {}) {
    return {
      reference_start: range().start,
      reference_duration: Math.max(0.1, range().end - range().start),
      stage1_count: boundedCount(advStage1Count(), 24, 50),
      stage2_count: boundedCount(advStage2Count(), 6, 30),
      final_count: boundedCount(advFinalCount(), 3, 10),
      enable_speechbrain: Boolean(advSpeechbrain()),
      providers: advProvider(),
      model: advModel(),
      ...extra,
    };
  }

  function quickAdvResult(response) {
    return response?.result || response || {};
  }

  async function loadQuickAdvState(showStatus = false) {
    if (!props.taskId || !props.api?.quickAdvState) return null;
    if (showStatus) {
      setAdvBusy("state");
      setAdvError("");
    }
    try {
      const response = await props.api.quickAdvState(props.taskId, quickAdvPayload());
      const result = quickAdvResult(response);
      setAdvState(result);
      if (result?.final_candidates && Array.isArray(result.final_candidates.candidates)) {
        try {
          await reloadCandidatesFromSession();
        } catch (reloadError) {
          emitTtsBuilderDebugError(reloadError, "Safe candidate reload after QuickAdv state failed");
        }
      }
      if (showStatus) setAdvStatus("状态已更新");
      return result;
    } catch (exc) {
      const message = exc instanceof Error ? exc.message : String(exc);
      setAdvError(message);
      return null;
    } finally {
      if (showStatus) setAdvBusy("");
    }
  }

  async function runQuickAdvCatalog() {
    if (!props.taskId || !props.api?.quickAdvCatalogList) return;
    setAdvBusy("catalog");
    setAdvError("");
    try {
      const response = await props.api.quickAdvCatalogList(props.taskId, quickAdvPayload());
      const result = quickAdvResult(response);
      if (result.ok === false) throw new Error(payloadIssue(result, "读取音色库失败"));
      setAdvCatalog(result);
      setAdvStatus(`已读取 ${result.count || 0} 个系统音色`);
    } catch (exc) {
      setAdvError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setAdvBusy("");
    }
  }

  async function runQuickAdvSample() {
    if (!props.taskId || !props.api?.quickAdvSampleReference) return;
    setAdvBusy("sample");
    setAdvError("");
    try {
      const response = await props.api.quickAdvSampleReference(props.taskId, quickAdvPayload());
      const result = quickAdvResult(response);
      if (result.ok === false) throw new Error(payloadIssue(result, "采样失败"));
      const score = Number(result?.sampling_audit?.sampling_score);
      setAdvStatus(Number.isFinite(score) ? `采样完成，质量 ${Math.round(score)}` : "采样完成");
      await loadQuickAdvState(false);
    } catch (exc) {
      setAdvError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setAdvBusy("");
    }
  }

  async function runQuickAdvRank() {
    if (!props.taskId || !props.api?.quickAdvRank) return;
    setAdvBusy("rank");
    setAdvError("");
    try {
      const response = await props.api.quickAdvRank(props.taskId, quickAdvPayload());
      const result = quickAdvResult(response);
      if (result.ok === false) throw new Error(payloadIssue(result, "排行失败"));
      setAdvState((current) => ({ ...(current || {}), ranking_board: result, reference: { ...(current?.reference || {}), profile: result.reference_profile || current?.reference?.profile } }));
      setAdvStatus(`排行完成，推荐 ${Array.isArray(result.recommended) ? result.recommended.length : 0} 个音色`);
      await loadQuickAdvState(false);
    } catch (exc) {
      setAdvError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setAdvBusy("");
    }
  }

  async function runQuickAdvGenerate() {
    await runBuilderG("quick_adv");
    await loadQuickAdvState(false);
  }

  async function runQuickAdvClone() {
    if (!props.taskId || !props.api?.quickAdvCloneVoice) return;
    setAdvBusy("clone");
    setAdvError("");
    try {
      const response = await props.api.quickAdvCloneVoice(props.taskId, quickAdvPayload({
        clone_consent_confirmed: true,
        clone_consent_actor: "ui",
        clone_consent_note: cloneNote(),
        clone_prefix: clonePrefix(),
      }));
      const result = quickAdvResult(response);
      if (result.ok === false) throw new Error(payloadIssue(result, "云端克隆失败"));
      setAdvStatus(result.reused_existing ? `已复用 voice_id ${result.voice_id}` : `已生成 voice_id ${result.voice_id}`);
      await loadQuickAdvState(false);
      await loadQuickAdvCloneList(false);
      await reloadCandidatesFromSession();
    } catch (exc) {
      setAdvError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setAdvBusy("");
    }
  }

  async function loadQuickAdvCloneList(showStatus = false) {
    if (!props.taskId || !props.api?.quickAdvCloneList) return null;
    if (showStatus) {
      setAdvBusy("clone-list");
      setAdvError("");
    }
    try {
      const response = await props.api.quickAdvCloneList(props.taskId, quickAdvPayload({ clone_page_size: 100 }));
      const result = quickAdvResult(response);
      if (result.ok === false) throw new Error(payloadIssue(result, "读取云端克隆音色失败"));
      setCloudClonePayload(result);
      setCloneDeleteConfirmVoiceId("");
      if (showStatus) setAdvStatus(`已读取 ${Number(result.count ?? (result.voices || []).length) || 0} 个云端音色`);
      return result;
    } catch (exc) {
      const message = exc instanceof Error ? exc.message : String(exc);
      setAdvError(message);
      return null;
    } finally {
      if (showStatus) setAdvBusy("");
    }
  }

  async function deleteQuickAdvCloneVoice(item) {
    const voiceId = cloudVoiceId(item);
    if (!props.taskId || !props.api?.quickAdvCloneDelete || !voiceId) return;
    const name = cloudVoiceName(item);
    if (cloneDeleteConfirmVoiceId() !== voiceId) {
      setCloneDeleteConfirmVoiceId(voiceId);
      setAdvError("");
      setAdvStatus(`点击右侧红色“确认”按钮删除 ${name}，释放云端克隆额度`);
      return;
    }
    setAdvBusy("clone-delete");
    setCloneDeleteVoiceId(voiceId);
    setCloneDeleteConfirmVoiceId("");
    setAdvError("");
    let cloneListRefreshed = false;
    try {
      const response = await props.api.quickAdvCloneDelete(props.taskId, quickAdvPayload({ clone_voice_id: voiceId }));
      const result = quickAdvResult(response);
      if (result.ok === false) throw new Error(payloadIssue(result, "删除云端克隆音色失败"));
      const refreshed = await loadQuickAdvCloneList(false);
      cloneListRefreshed = true;
      if (!refreshed) throw new Error("删除请求已提交，但刷新云端音色列表失败，无法确认是否已删除。请手动刷新云端音色列表确认。");
      if (cloudVoiceExists(refreshed, voiceId)) {
        throw new Error(payloadIssue({
          blocked_reasons: [{
            code: "clone_delete_not_confirmed",
            message: `voice_id ${voiceId} is still returned by the refreshed cloud voice list.`,
          }],
        }, "删除云端克隆音色失败"));
      }
      setAdvStatus(`已删除云端音色 ${cloneVoiceSuffix(voiceId)}`);
      await loadQuickAdvState(false);
      await reloadCandidatesFromSession();
    } catch (exc) {
      setAdvStatus("");
      setAdvError(exc instanceof Error ? exc.message : String(exc));
      if (!cloneListRefreshed) await loadQuickAdvCloneList(false);
    } finally {
      setCloneDeleteVoiceId("");
      setAdvBusy("");
    }
  }

  async function importQuickAdvCloneVoice(item) {
    const voiceId = cloudVoiceId(item);
    if (!props.taskId || !props.api?.quickAdvCloneImport || !voiceId) return;
    if (cloudVoiceInCurrentTask(item, localCloneVoiceIds())) {
      setAdvStatus(`云端音色 ${cloneVoiceSuffix(voiceId)} 已在当前任务`);
      return;
    }
    setAdvBusy("clone-import");
    setCloneImportVoiceId(voiceId);
    setAdvError("");
    try {
      const response = await props.api.quickAdvCloneImport(props.taskId, quickAdvPayload({
        clone_voice_id: voiceId,
        clone_prefix: clonePrefix(),
      }));
      const result = quickAdvResult(response);
      if (result.ok === false) throw new Error(payloadIssue(result, "选用云端音色失败"));
      setAdvStatus(`已选用云端音色 ${cloneVoiceSuffix(result.voice_id || voiceId)} 到当前任务`);
      await loadQuickAdvState(false);
      await reloadCandidatesFromSession();
    } catch (exc) {
      setAdvStatus("");
      setAdvError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setCloneImportVoiceId("");
      setAdvBusy("");
    }
  }

  function quickAdvPreviewItem(item) {
    const dialogue = String(advReference()?.dialogue || "").trim();
    const prompt = candidatePrompt(item) || dialogue;
    return {
      ...item,
      candidate_id: item?.candidate_id || item?.voice || item?.voice_label,
      prompt,
      generation_prompt: prompt,
      tts_builder_prompt: prompt,
    };
  }

  async function copyText(text, message) {
    const value = String(text || "");
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
    } catch {
      const el = document.createElement("textarea");
      el.value = value;
      document.body.appendChild(el);
      el.select();
      document.execCommand("copy");
      el.remove();
    }
    setAdvStatus(message || "已复制");
  }

  createEffect(() => {
    const sessionId = props.sessionId;
    const cacheKey = props.cacheKey;
    if (!sessionId) return;
    void cacheKey;
    untrack(() => {
      void reloadCandidatesFromSession().catch((err) => emitTtsBuilderDebugError(err, "Initial candidate reload failed"));
      void loadQuickAdvState(false).catch((err) => emitTtsBuilderDebugError(err, "Initial quick-advanced TTS state load failed"));
    });
  });

  createEffect(() => {
    if (!cloneConfigOpen() || cloudClonePayload()) return;
    untrack(() => {
      void loadQuickAdvCloneList(false).catch((err) => emitTtsBuilderDebugError(err, "Initial quick-advanced clone list load failed"));
    });
  });

  async function uploadReferenceAudio(file) {
    if (!file || !props.taskId) return;
    if (!isReferenceMediaFile(file)) {
      const size = Number(file.size) || 0;
      setStatus("");
      setError(`不支持的参考声音格式：${file.name || "未命名文件"}。${REFERENCE_MEDIA_SUPPORT_TEXT}`);
      setUploadProgress({
        phase: "error",
        name: file.name || "参考声音",
        size,
        loaded: 0,
        total: size,
        percent: 0,
      });
      return;
    }
    setBusy("upload");
    setError("");
    setStatus("");
    setUploadProgress({
      phase: "uploading",
      name: file.name || "参考声音",
      size: Number(file.size) || 0,
      loaded: 0,
      total: Number(file.size) || 0,
      percent: Number(file.size) ? 1 : 0,
    });
    try {
      await props.api.uploadTTSReferenceAudio(props.taskId, file, {
        onProgress: (event) => {
          const total = Number(event.total || file.size || 0);
          const loaded = Number(event.loaded || 0);
          const finishedSending = total > 0 && loaded >= total;
          const percent = finishedSending ? 100 : total > 0 ? clamp((loaded / total) * 100, 1, 99) : 0;
          setUploadProgress((current) => ({
            ...(current || {}),
            phase: finishedSending ? "processing" : "uploading",
            name: file.name || current?.name || "参考声音",
            size: Number(file.size) || current?.size || 0,
            loaded,
            total,
            percent,
          }));
        },
      });
      setUploadProgress((current) => ({
        ...(current || {}),
        phase: "processing",
        loaded: current?.total || current?.size || current?.loaded || 0,
        total: current?.total || current?.size || 0,
        percent: 100,
      }));
      setStatus("参考声音已上传，正在刷新声音数据");
      await props.onReload?.();
      await reloadCandidatesFromSession();
      setUploadProgress((current) => ({
        ...(current || {}),
        phase: "done",
        loaded: current?.total || current?.size || current?.loaded || 0,
        total: current?.total || current?.size || 0,
        percent: 100,
      }));
      setStatus("参考声音已上传，可试听或继续匹配");
    } catch (exc) {
      setUploadProgress((current) => current ? { ...current, phase: "error" } : current);
      setError(referenceUploadErrorMessage(exc, file));
    } finally {
      setBusy("");
    }
  }

  async function runBuilderG(builderMode = "quick") {
    if (!props.taskId) return;
    const builderConfig = TTS_BUILDER_MODES[builderMode] || TTS_BUILDER_MODES.quick;
    setBusy("builder");
    setError("");
    setStatus(`${builderConfig.label}正在启动后台声音匹配`);
    try {
      if (props.sessionId && props.api?.readWorkspaceJson) {
        const finalItemsPayload = await props.api.readWorkspaceJson(props.sessionId, FINAL_ITEMS_PATH);
        if (!finalItemsPayload) {
          throw new Error(`${builderConfig.stepId} 需要先完成 02_02 字幕帧对齐；当前还没有读取到 ${FINAL_ITEMS_PATH}。请先运行到 02_02，或等待当前工具链完成后再试。`);
        }
      }
      const payload = {
        builder_mode: builderConfig.mode,
        reference_start: range().start,
        reference_duration: Math.max(0.1, range().end - range().start),
        force: true,
      };
      if (builderConfig.mode === "quick_adv") {
        payload.stage1_count = boundedCount(advStage1Count(), 24, 50);
        payload.stage2_count = boundedCount(advStage2Count(), 6, 30);
        payload.final_count = boundedCount(advFinalCount(), 3, 10);
        payload.enable_speechbrain = Boolean(advSpeechbrain());
        payload.providers = advProvider();
        payload.model = advModel();
      }
      if (props.onRunQuickBuilder) {
        const started = await props.onRunQuickBuilder(payload);
        const attempt = started?.attempt_id ? ` #${started.attempt_id}` : "";
        setStatus(`${builderConfig.label}已启动后台运行${attempt}`);
        scheduleCandidateReloads();
      } else {
        if (builderConfig.mode !== "quick") throw new Error("高级匹配需要通过 Analysis V1 后台运行接口启动。");
        await props.api.runTTSBuilderG(props.taskId, payload);
        await props.onReload?.();
        const nextPayload = await reloadCandidatesFromSession();
        if (!hasCandidates(nextPayload)) {
          throw new Error(`${builderConfig.label}已返回完成，但没有读取到 ${TTS_BUILDER_CANDIDATES_PATH}`);
        }
        setStatus(`${builderConfig.label}已完成，已读取 ${nextPayload.candidates.length} 个候选声音`);
      }
    } catch (exc) {
      const message = exc instanceof Error ? exc.message : String(exc);
      if (message.includes("已有 Analysis_V1 工具链运行中")) {
        scheduleCandidateReloads();
        setStatus("已有 Analysis_V1 工具链运行中，将继续自动读取候选声音结果");
      } else {
        setStatus("");
      }
      setError(message);
    } finally {
      setBusy("");
    }
  }

  function stopDialogDragListeners() {
    window.removeEventListener("mousemove", moveDialog, true);
    window.removeEventListener("mouseup", stopDialogDrag, true);
    window.removeEventListener("blur", stopDialogDrag);
  }

  function stopDialogDrag() {
    activeDialogDrag = null;
    stopDialogDragListeners();
  }

  function moveDialog(event) {
    if (!activeDialogDrag) return;
    const nextLeft = clamp(activeDialogDrag.left + event.clientX - activeDialogDrag.x, 8, Math.max(8, window.innerWidth - activeDialogDrag.width - 8));
    const nextTop = clamp(activeDialogDrag.top + event.clientY - activeDialogDrag.y, 8, Math.max(8, window.innerHeight - activeDialogDrag.height - 8));
    setDialogPosition({ left: nextLeft, top: nextTop });
  }

  function startDialogDrag(event) {
    if (event.button !== 0 || event.target.closest("button,input,select,textarea")) return;
    const dialog = event.currentTarget.closest(".analysis-v1-tts-dialog");
    const rect = dialog?.getBoundingClientRect();
    if (!rect) return;
    activeDialogDrag = { x: event.clientX, y: event.clientY, left: rect.left, top: rect.top, width: rect.width, height: rect.height };
    setDialogPosition({ left: rect.left, top: rect.top });
    window.addEventListener("mousemove", moveDialog, true);
    window.addEventListener("mouseup", stopDialogDrag, true);
    window.addEventListener("blur", stopDialogDrag);
  }

  function ttsSelectionPayload(item, key) {
    const prompt = normalizePromptEdit(promptEdits()[key] || promptEdits()[candidateKey(item)], item) || candidatePrompt(item);
    const voiceId = String(item?.voice_id || item?.voice || item?.voice_label || "").trim();
    return {
      candidate_id: String(item?.candidate_id || key || "").trim(),
      provider: String(item?.provider || "").trim(),
      provider_label: String(item?.provider_label || item?.providerLabel || item?.provider || "").trim(),
      model: String(item?.model || item?.target_model || "").trim(),
      model_label: String(item?.model_label || item?.modelLabel || item?.model || item?.target_model || "").trim(),
      voice: voiceId,
      voice_id: voiceId,
      voice_label: String(item?.voice_label || item?.label || voiceId || key || "").trim(),
      voice_source: String(item?.voice_source || "").trim(),
      sample_audio_path: String(item?.sample_audio_path || item?.preview_audio_path || "").trim(),
      prompt,
      prompt_template: prompt,
      score: Number.isFinite(Number(item?.score)) ? Number(item.score) : undefined,
      match_score: Number.isFinite(Number(item?.match_score)) ? Number(item.match_score) : undefined,
      candidate: item || {},
    };
  }

  async function persistSelectedCandidate(key, item) {
    if (!props.taskId || !props.api?.saveTTSSelection || !item) {
      setStatus("已选用；当前环境没有保存到 Session Variables 的接口");
      return null;
    }
    const busyKey = `select:${key}`;
    setBusy(busyKey);
    setError("");
    try {
      const result = await props.api.saveTTSSelection(props.taskId, ttsSelectionPayload(item, key));
      setStatus("已保存选用到 SessionContext/Variables.json");
      return result;
    } catch (exc) {
      const message = exc instanceof Error ? exc.message : String(exc);
      setError(message);
      return null;
    } finally {
      if (busy() === busyKey) setBusy("");
    }
  }

  async function selectCandidate(key, event, item = null) {
    event?.stopPropagation();
    const selectedItem = item || candidates().find((candidate, index) => candidateKey(candidate, index) === key) || null;
    setSelectedKey(key);
    try {
      window.localStorage.setItem(selectionStorageKey(), key);
    } catch {
      // Local storage only preserves the UI choice across reopen.
    }
    await persistSelectedCandidate(key, selectedItem);
  }

  function candidateKey(item, index = 0) {
    return item?.candidate_id || item?.voice || item?.voice_label || `tts_${index + 1}`;
  }

  function saveCandidatePrompt(item, prompt, meta = {}) {
    const key = candidateKey(item);
    setPromptEdits((current) => {
      const next = { ...current, [key]: { prompt, source_hash: candidatePromptHash(item), saved_at: Date.now(), ...meta } };
      try {
        window.localStorage.setItem(promptStorageKey(), JSON.stringify(next));
      } catch {
        // Prompt edits still stay active in the current UI session.
      }
      return next;
    });
    setStatus("已保存测试提示词；故事版本需在弹窗中确认应用");
  }

  function saveCloneTempo(item, value) {
    const key = candidateKey(item);
    const prompt = candidatePrompt(item) || CLONE_PREVIEW_PROMPT;
    const tempo = sanitizeTempoInput(value) || "1";
    setPromptEdits((current) => {
      const previous = current[key] || {};
      const next = {
        ...current,
        [key]: {
          ...previous,
          prompt: String(previous.prompt || prompt),
          source_hash: candidatePromptHash(item),
          saved_at: Date.now(),
          tempo,
        },
      };
      try {
        window.localStorage.setItem(promptStorageKey(), JSON.stringify(next));
      } catch {
        // Tempo edits are still active for the current UI session.
      }
      return next;
    });
  }

  async function applyCandidatePromptToStoryBoard(item, prompt, meta = {}) {
    if (!props.taskId || !props.api?.applyStoryBoardTTSSelection) {
      throw new Error("当前任务无法保存故事版本口播设置");
    }
    const voiceId = String(meta.voice_id || item?.voice_id || item?.voice || item?.voice_label || "").trim();
    const provider = String(item?.provider || "").trim();
    const model = String(item?.model || item?.target_model || "").trim();
    const tempoValue = Number(meta.tempo || defaultTempoForItem(item));
    const result = await props.api.applyStoryBoardTTSSelection(props.taskId, {
      provider,
      provider_label: item?.provider_label || item?.providerLabel || provider,
      model,
      model_label: item?.model_label || item?.modelLabel || model,
      voice_id: voiceId,
      voice: voiceId,
      label: item?.voice_label || item?.label || voiceId,
      voice_label: item?.voice_label || item?.label || voiceId,
      candidate_id: item?.candidate_id || "",
      voice_source: item?.voice_source || "",
      source_clone_provider: item?.source_clone_provider || "",
      sample_audio_path: item?.sample_audio_path || item?.preview_audio_path || "",
      prompt,
      prompt_template: prompt,
      scenario_id: meta.scenario_id || "",
      language: meta.language || "",
      tempo: Number.isFinite(tempoValue) && tempoValue > 0 ? tempoValue : 1,
      base_prompt: meta.base_prompt || "",
    });
    setStatus("已应用到故事版本口播；重新生成 Scene / Dialogue 音频后会使用该模板");
    return result;
  }

  const previewPromptEdit = createMemo(() => normalizePromptEditEntry(promptEdits()[candidateKey(previewItem() || {})], previewItem() || {}) || {});
  const cloneConsentDialog = () => (
    <>
      <div class="drawer-backdrop analysis-v1-tts-clone-consent-backdrop" onClick={() => setCloneConsentOpen(false)} />
      <section
        class="verify-dialog analysis-v1-tts-clone-consent-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="analysis-v1-tts-clone-consent-title"
        onMouseDown={(event) => event.stopPropagation()}
        onClick={(event) => event.stopPropagation()}
      >
        <div class="analysis-v1-tts-clone-consent-head">
          <h3 id="analysis-v1-tts-clone-consent-title">授权克隆</h3>
          <button class="secondary analysis-v1-tts-close" type="button" title="关闭" aria-label="关闭" onClick={() => setCloneConsentOpen(false)}><CloseIcon /></button>
        </div>
        <div class="analysis-v1-tts-clone-consent-body">
          <p>请确认你已获得参考声音权利人的授权。</p>
        </div>
        <div class="analysis-v1-tts-clone-consent-actions">
          <button class="secondary" type="button" disabled={Boolean(advBusy())} onClick={() => setCloneConsentOpen(false)}>取消</button>
          <button class="primary" type="button" disabled={Boolean(advBusy())} onClick={() => {
            setCloneConsentOpen(false);
            void runQuickAdvClone();
          }}>{advBusy() === "clone" ? "克隆中..." : "确认授权"}</button>
        </div>
      </section>
    </>
  );

  const normalVoicePanel = () => (
    <Show when={normalVoices().length} fallback={<div class="analysis-v1-empty">还没有可用的常规声音</div>}>
      <div class="analysis-v1-tts-table analysis-v1-tts-normal-table">
        <div class="analysis-v1-tts-table-head analysis-v1-tts-normal-head"><span>声音</span><span>性别</span><span>模型</span><span>播放</span><span>选用</span><span>调音</span></div>
        <For each={normalVoices()}>{(item, index) => {
          const key = candidateKey(item, index());
          const playKey = () => previewCacheKey(item, key);
          const audioUrl = () => candidateAudioUrl(item, key);
          const canPlay = () => Boolean(audioUrl()) || canGenerateCandidatePreview(item);
          const previewBusy = () => busy() === `preview:${playKey()}`;
          const selected = () => selectedKey() === key;
          const displayName = () => candidateDisplayName(item, key);
          return <div class={`analysis-v1-tts-row analysis-v1-tts-normal-row ${selected() ? "is-selected" : ""}`} role="button" tabindex="0" aria-pressed={selected() ? "true" : "false"} onClick={(event) => void selectCandidate(key, event, item)} onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") void selectCandidate(key, event, item);
          }}>
            <div class="analysis-v1-tts-name"><strong>{displayName()}</strong><Show when={selected()}><span>当前选用</span></Show></div>
            <div class={`analysis-v1-tts-voice-gender is-${normalizedVoiceGender(item.voice_gender) || "unknown"}`}>{voiceGenderLabel(item.voice_gender)}</div>
            <div class="analysis-v1-tts-normal-model"><strong>{item.provider_label || item.provider || "-"}</strong><span>{item.model_label || item.model || "-"}</span></div>
            <button class={`icon-action analysis-v1-tts-row-action ${playingKey() === playKey() ? "is-playing" : ""}`} type="button" title={playingKey() === playKey() ? "停止" : audioUrl() ? "播放试听" : "生成并播放试听"} aria-label={`${playingKey() === playKey() ? "停止" : "播放"} ${displayName()}`} disabled={!canPlay() || previewBusy()} onClick={(event) => { event.stopPropagation(); void playCandidate(key, item); }}><Show when={playingKey() === playKey()} fallback={<PlayClipIcon />}><StopIcon /></Show></button>
            <button class={`icon-action analysis-v1-tts-row-action is-primary ${selected() ? "is-selected" : ""}`} type="button" title={selected() ? "已选用" : "设为选用"} aria-label={`${selected() ? "已选用" : "设为选用"} ${displayName()}`} aria-pressed={selected() ? "true" : "false"} disabled={busy() === `select:${key}`} onClick={(event) => void selectCandidate(key, event, item)}><Show when={selected()} fallback={<SaveIcon />}><CheckIcon /></Show></button>
            <button class="icon-action analysis-v1-tts-row-action" type="button" title="试听调音" aria-label={`试听调音 ${displayName()}`} onClick={(event) => { event.stopPropagation(); setPreviewItem(item); }}><SlidersIcon /></button>
          </div>;
        }}</For>
      </div>
    </Show>
  );

  const candidatePanel = () => (
    <Show when={candidates().length} fallback={<div class="analysis-v1-empty">{requiresCloudCloneRefresh() ? "克隆声音服务已切换，旧云端音色已停用。请关闭本窗口，点击顶部“音色克隆”重新创建云端音色。" : `还没有读取到 ${TTS_BUILDER_CANDIDATES_PATH}`}</div>}>
      <div class="analysis-v1-tts-table">
        <div class="analysis-v1-tts-table-head"><span>序号</span><span>声音名称</span><span>播放</span><span>选用</span><span>测试</span><span>评分</span></div>
        <For each={candidates()}>{(item, index) => {
          const key = candidateKey(item, index());
          const audioUrl = () => candidateAudioUrl(item, key);
          const canPlay = () => Boolean(audioUrl()) || canGenerateCandidatePreview(item);
          const selected = () => selectedKey() === key;
          const infoOpen = () => infoKey() === key;
          const playKey = () => previewCacheKey(testItem(), key);
          const previewBusy = () => busy() === `preview:${playKey()}`;
          const displayName = () => candidateDisplayName(item, key);
          const editedPrompt = () => normalizePromptEdit(promptEdits()[candidateKey(item, index())], item);
          const testItem = () => editedPrompt() ? { ...item, prompt: editedPrompt(), generation_prompt: editedPrompt(), tts_builder_prompt: editedPrompt() } : item;
          return <div class={`analysis-v1-tts-row ${selected() ? "is-selected" : ""}`} role="button" tabindex="0" aria-pressed={selected() ? "true" : "false"} onClick={(event) => void selectCandidate(key, event, testItem())} onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") void selectCandidate(key, event, testItem());
          }}>
            <div class="analysis-v1-tts-rank">{index() + 1}</div>
            <div class="analysis-v1-tts-name"><strong>{displayName()}</strong><Show when={selected()}><span>当前选用</span></Show></div>
            <button class={`icon-action analysis-v1-tts-row-action ${playingKey() === playKey() ? "is-playing" : ""}`} type="button" title={playingKey() === playKey() ? "停止" : audioUrl() ? "播放" : "生成并播放试听"} aria-label={`${playingKey() === playKey() ? "停止" : "播放"} ${displayName()}`} disabled={!canPlay() || previewBusy()} onClick={(event) => { event.stopPropagation(); void playCandidate(key, testItem()); }}><Show when={playingKey() === playKey()} fallback={<PlayClipIcon />}><StopIcon /></Show></button>
            <button class={`icon-action analysis-v1-tts-row-action is-primary ${selected() ? "is-selected" : ""}`} type="button" title={selected() ? "已选用" : "设为选用"} aria-label={`${selected() ? "已选用" : "设为选用"} ${displayName()}`} aria-pressed={selected() ? "true" : "false"} disabled={busy() === `select:${key}`} onClick={(event) => void selectCandidate(key, event, testItem())}><Show when={selected()} fallback={<SaveIcon />}><CheckIcon /></Show></button>
            <button class="icon-action analysis-v1-tts-row-action" type="button" title="测试" aria-label={`测试 ${displayName()}`} onClick={(event) => { event.stopPropagation(); setPreviewItem(testItem()); }}><CodeIcon /></button>
            <button class={`icon-action analysis-v1-tts-row-action analysis-v1-tts-info ${infoOpen() ? "is-selected" : ""}`} type="button" title={infoOpen() ? "收起详情" : `查看评分 ${candidateDisplayScore(item).toFixed(2)}`} aria-label={`${infoOpen() ? "收起详情" : "查看评分"} ${displayName()}`} aria-expanded={infoOpen() ? "true" : "false"} onClick={(event) => { event.stopPropagation(); setInfoKey(infoOpen() ? "" : key); }}>i</button>
          </div>;
        }}</For>
      </div>
      <Show when={infoItem()}>
        {(item) => (
          <div class="analysis-v1-tts-detail" aria-live="polite">
            <div><strong>{candidateDisplayName(item(), infoKey())}</strong><span>评分 {candidateDisplayScore(item()).toFixed(2)}</span><span>{item().provider || "-"}</span><span>{item().model || "-"}</span><Show when={item().scoring_mode}><span>{item().scoring_mode === "full_speechbrain" ? "高精度匹配" : "基础匹配"}</span></Show></div>
            <div><span>候选 ID</span><code>{item().candidate_id || infoKey()}</code></div>
            <div><span>音频</span><code>{candidateAudioRel(item(), infoKey()) || (canGenerateCandidatePreview(item()) ? "点击播放生成试听" : "-")}</code></div>
            <div class="analysis-v1-tts-dimension-grid">
              <For each={dimensionRows(item())}>{(entry) => (
                <div class="analysis-v1-tts-dimension">
                  <span>{entry.label}</span>
                  <strong>{entry.value.toFixed(0)}</strong>
                  <i style={{ width: `${entry.value}%` }} />
                </div>
              )}</For>
            </div>
            <p>{recommendationText(item())}</p>
            <Show when={item().reason || item().needs_review}>
              <p>{item().reason || (item().needs_review ? "需复核" : "")}</p>
            </Show>
          </div>
        )}
      </Show>
    </Show>
  );

  const quickAdvActionButtons = () => (
    <>
      <button class="secondary" type="button" title="刷新当前分析和排行结果" disabled={Boolean(advBusy())} onClick={() => void loadQuickAdvState(true)}><RefreshIcon />状态</button>
      <button class="secondary" type="button" title="读取当前可用于匹配的系统音色库" disabled={Boolean(advBusy())} onClick={() => void runQuickAdvCatalog()}><SpeechIcon />音色库</button>
      <button class="secondary" type="button" title="分析当前选中的原声片段" disabled={Boolean(advBusy())} onClick={() => void runQuickAdvSample()}><WaveformIcon />采样</button>
      <button class="secondary" type="button" title="用 Resemblyzer/声学特征对音色库进行排行" disabled={Boolean(advBusy())} onClick={() => void runQuickAdvRank()}><SlidersIcon />排行</button>
      <button class="primary" type="button" title="按排行结果生成最终候选音频" disabled={Boolean(advBusy()) || busy() === "builder"} onClick={() => void runQuickAdvGenerate()}><PlayClipIcon />生成候选</button>
    </>
  );

  const quickAdvConfigPanel = () => (
    <div class="analysis-v1-tts-adv">
      <div class="analysis-v1-tts-adv-controls">
        <div class="analysis-v1-tts-adv-control-row is-provider-row">
          <label title="参与高级匹配的音色来源"><span>音色来源</span><select value={advProvider()} onChange={(event) => { setAdvProviderTouched(true); setAdvProvider(event.currentTarget.value); }}><For each={advProviders()}>{(item) => <option value={item.provider}>{item.label}</option>}</For></select></label>
          <label title="用于音色库和候选生成的 TTS 模型"><span>模型</span><select value={advModel()} onChange={(event) => setAdvModel(event.currentTarget.value)}><For each={advModels()}>{(item) => <option value={item.model}>{item.label || item.model}</option>}</For></select></label>
        </div>
        <div class="analysis-v1-tts-adv-control-row is-count-row">
          <label title="第一轮粗筛数量"><span>首轮数量</span><input class="analysis-v1-tts-count-input" type="number" min="1" max="50" step="1" value={advStage1Count()} onInput={(event) => setAdvStage1Count(event.currentTarget.value)} /></label>
          <label title="第二轮精排数量"><span>二轮数量</span><input class="analysis-v1-tts-count-input" type="number" min="1" max="30" step="1" value={advStage2Count()} onInput={(event) => setAdvStage2Count(event.currentTarget.value)} /></label>
          <label title="最终生成候选数量"><span>候选数量</span><input class="analysis-v1-tts-count-input" type="number" min="1" max="10" step="1" value={advFinalCount()} onInput={(event) => setAdvFinalCount(event.currentTarget.value)} /></label>
          <label class="analysis-v1-tts-adv-check"><input class="analysis-v1-tts-adv-checkbox" type="checkbox" checked={advSpeechbrain()} onChange={(event) => setAdvSpeechbrain(event.currentTarget.checked)} /><span>高精度匹配</span></label>
        </div>
      </div>
      <div class="analysis-v1-tts-adv-metrics">
        <div><span>采样</span><strong>{Number.isFinite(Number(advSamplingAudit()?.sampling_score)) ? Math.round(Number(advSamplingAudit()?.sampling_score)) : "-"}</strong><em>{qualityLabel(advSamplingAudit()?.quality_label)}</em></div>
        <div><span>评分模式</span><strong>{scoringModeLabel(advRanking()?.scoring_mode)}</strong><em></em></div>
        <Show when={advReference()}>
          {(profile) => (
            <>
            <div><span>采样范围</span><strong>{formatSeconds(profile().selected_duration)}</strong><code>{formatSeconds(profile().selected_range?.start)} - {formatSeconds(profile().selected_range?.end)}</code></div>
            <div><span>性别判断</span><strong>{titleCaseLabel(profile().gender_gate?.target_gender)}</strong><em></em></div>
            <div><span>音高</span><strong>{Number(profile().features?.pitch_hz || 0).toFixed(1)} Hz</strong><em></em></div>
            </>
          )}
        </Show>
      </div>
      <Show when={advStatus()}><div class="analysis-v1-tts-status">{advStatus()}</div></Show>
      <Show when={advError()}><div class="banner bad analysis-v1-tts-error">{advError()}</div></Show>
      <Show when={advRows().length} fallback={<div class="analysis-v1-empty">还没有高级匹配排行结果</div>}>
        <div class="analysis-v1-tts-adv-rank">
          <div class="analysis-v1-tts-adv-rank-head"><span>#</span><span>音色</span><span>匹配</span><span>最接近</span><span>动作</span></div>
          <For each={advRows()}>{(item) => {
            const key = () => `adv:${item.voice || item.voice_label || item.rank}`;
            const audioUrl = () => catalogAudioUrl(item);
            return <div class="analysis-v1-tts-adv-rank-row">
              <span>{item.rank || "-"}</span>
              <div><strong>{item.voice_label || item.voice || "-"}</strong></div>
              <strong>{candidateDisplayScore(item).toFixed(0)}</strong>
              <span>{closestDimensionText(item)}</span>
              <div class="analysis-v1-tts-adv-row-actions">
                <button class={`icon-action analysis-v1-tts-row-action ${playingKey() === key() ? "is-playing" : ""}`} type="button" title="播放音色样本" aria-label={`播放 ${item.voice_label || item.voice || key()}`} disabled={!audioUrl()} onClick={() => play(key(), audioUrl())}><Show when={playingKey() === key()} fallback={<PlayClipIcon />}><StopIcon /></Show></button>
                <button class="icon-action analysis-v1-tts-row-action" type="button" title="测试" aria-label={`测试 ${item.voice_label || item.voice || key()}`} onClick={() => setPreviewItem(quickAdvPreviewItem(item))}><CodeIcon /></button>
                <button class="icon-action analysis-v1-tts-row-action" type="button" title="复制 Voice ID" aria-label={`复制 ${item.voice || item.voice_label || key()}`} onClick={() => void copyText(item.voice || item.voice_label, "已复制 Voice ID")}><CopyIcon /></button>
              </div>
            </div>;
          }}</For>
        </div>
      </Show>
    </div>
  );

  const matchConfigDialog = () => (
    <>
      <div class="drawer-backdrop analysis-v1-tts-match-config-backdrop" onClick={() => setMatchConfigOpen(false)} />
      <section
        class="verify-dialog analysis-v1-tts-match-config-dialog"
        onMouseDown={(event) => event.stopPropagation()}
        onClick={(event) => event.stopPropagation()}
      >
        <div class="env-dialog-head analysis-v1-tts-preview-head">
          <div>
            <h3>匹配配置</h3>
          </div>
          <div class="analysis-v1-tts-match-head-actions">
            {quickAdvActionButtons()}
          </div>
          <button class="secondary analysis-v1-tts-close" type="button" title="关闭" aria-label="关闭" onClick={() => setMatchConfigOpen(false)}><CloseIcon /></button>
        </div>
        <div class="analysis-v1-tts-match-config-body">
          {quickAdvConfigPanel()}
        </div>
      </section>
    </>
  );

  const cloneConfigDialog = () => (
    <>
      <div class="drawer-backdrop analysis-v1-tts-clone-config-backdrop" onClick={() => setCloneConfigOpen(false)} />
      <section
        class="verify-dialog analysis-v1-tts-clone-config-dialog"
        onMouseDown={(event) => event.stopPropagation()}
        onClick={(event) => event.stopPropagation()}
      >
        <div class="env-dialog-head analysis-v1-tts-preview-head">
          <div>
            <h3>克隆配置</h3>
          </div>
          <div class="analysis-v1-tts-clone-head-actions">
            <button class="secondary" type="button" title="刷新当前采样和克隆状态" disabled={Boolean(advBusy())} onClick={() => void loadQuickAdvState(true)}><RefreshIcon />状态</button>
            <button class="secondary" type="button" title="读取云端克隆音色" disabled={Boolean(advBusy())} onClick={() => void loadQuickAdvCloneList(true)}><RefreshIcon />云端</button>
            <button class="secondary" type="button" title="分析当前选中的原声片段" disabled={Boolean(advBusy())} onClick={() => void runQuickAdvSample()}><WaveformIcon />采样</button>
          </div>
          <button class="secondary analysis-v1-tts-close" type="button" title="关闭" aria-label="关闭" onClick={() => setCloneConfigOpen(false)}><CloseIcon /></button>
        </div>
        <div class="analysis-v1-tts-clone-config-body">
          <Show when={advStatus()}><div class="analysis-v1-tts-status">{advStatus()}</div></Show>
          <Show when={advError()}><div class="banner bad analysis-v1-tts-error">{advError()}</div></Show>
          <div class="analysis-v1-tts-clone-config-fields">
            <label><span>克隆前缀</span><input value={clonePrefix()} maxlength="9" onInput={(event) => setClonePrefix(event.currentTarget.value)} /></label>
            <label><span>授权备注</span><input value={cloneNote()} placeholder="可选" onInput={(event) => setCloneNote(event.currentTarget.value)} /></label>
          </div>
          <div class="analysis-v1-tts-clone-param-grid">
            <div><span>克隆</span><strong>{advClones().length || "-"}</strong><em></em></div>
            <div><span>采样</span><strong>{Number.isFinite(Number(advSamplingAudit()?.sampling_score)) ? Math.round(Number(advSamplingAudit()?.sampling_score)) : "-"}</strong><em>{qualityLabel(advSamplingAudit()?.quality_label)}</em></div>
            <div><span>范围</span><strong>{formatSeconds(advReference()?.selected_duration)}</strong><em>{formatSeconds(advReference()?.selected_range?.start)} - {formatSeconds(advReference()?.selected_range?.end)}</em></div>
            <div><span>性别</span><strong>{titleCaseLabel(advReference()?.gender_gate?.target_gender)}</strong><em></em></div>
            <div><span>音高</span><strong>{Number.isFinite(Number(advReference()?.features?.pitch_hz)) ? `${Number(advReference()?.features?.pitch_hz).toFixed(1)} Hz` : "-"}</strong><em></em></div>
          </div>
          <div class="analysis-v1-tts-cloud-clone-panel">
            <div class="analysis-v1-tts-cloud-clone-head">
              <div>
                <strong>云端音色</strong>
                <span>{cloudClonePayload()?.provider || "voice-clone"} · {cloudCloneCount()} 个</span>
              </div>
              <button class="secondary" type="button" title="刷新云端音色列表" disabled={Boolean(advBusy())} onClick={() => void loadQuickAdvCloneList(true)}><RefreshIcon />刷新</button>
            </div>
            <Show when={cloudClonePayload()} fallback={<div class="analysis-v1-empty">点击“云端”读取克隆音色</div>}>
              <Show when={cloudCloneVoices().length} fallback={<div class="analysis-v1-empty">云端没有克隆音色</div>}>
                <div class="analysis-v1-tts-cloud-clone-list" role="table" aria-label="云端克隆音色">
                  <div class="analysis-v1-tts-cloud-clone-list-head" role="row">
                    <span>名称</span>
                    <span>Voice ID</span>
                    <span>状态</span>
                    <span>选用</span>
                    <span>操作</span>
                  </div>
                  <For each={cloudCloneVoices()}>{(item) => {
                    const voiceId = () => cloudVoiceId(item);
                    const local = () => cloudVoiceInCurrentTask(item, localCloneVoiceIds());
                    const deleting = () => cloneDeleteVoiceId() === voiceId();
                    const importing = () => cloneImportVoiceId() === voiceId();
                    const confirming = () => cloneDeleteConfirmVoiceId() === voiceId();
                    return <div class="analysis-v1-tts-cloud-clone-row" role="row" title={voiceId()}>
                      <div class="analysis-v1-tts-cloud-clone-name" role="cell">
                        <strong>{cloudVoiceName(item)}</strong>
                        <span>{[item.language, item.gender].filter(Boolean).join(" · ") || item.provider || "-"}</span>
                      </div>
                      <code role="cell">{cloneVoiceSuffix(voiceId())}</code>
                      <span class={`analysis-v1-tts-cloud-clone-local ${local() ? "is-local" : ""}`} role="cell">{local() ? "当前任务" : "云端"}</span>
                      <button class={`icon-action analysis-v1-tts-row-action analysis-v1-tts-cloud-clone-use ${local() ? "is-selected" : ""}`} type="button" role="cell" title={local() ? "已在当前任务" : "选用到当前任务"} aria-label={`${local() ? "已选用" : "选用"} ${cloudVoiceName(item)}`} disabled={!voiceId() || local() || Boolean(advBusy())} onClick={() => void importQuickAdvCloneVoice(item)}><Show when={importing()} fallback={<><CheckIcon /><span>{local() ? "已选" : "选用"}</span></>}><RefreshIcon /></Show></button>
                      <div class="analysis-v1-tts-cloud-clone-actions" role="cell">
                        <button class="icon-action analysis-v1-tts-row-action" type="button" title="复制 Voice ID" aria-label={`复制 ${cloudVoiceName(item)} Voice ID`} disabled={!voiceId()} onClick={() => void copyText(voiceId(), "已复制 Voice ID")}><CopyIcon /></button>
                        <button class={`icon-action analysis-v1-tts-row-action danger ${confirming() ? "is-confirming" : ""}`} type="button" title={confirming() ? "点击确认删除" : "删除云端克隆音色"} aria-label={`${confirming() ? "确认删除" : "删除"} ${cloudVoiceName(item)}`} disabled={!voiceId() || Boolean(advBusy())} onClick={() => void deleteQuickAdvCloneVoice(item)}><Show when={deleting()} fallback={<Show when={confirming()} fallback={<TrashIcon />}><CheckIcon /><span>确认</span></Show>}><RefreshIcon /></Show></button>
                      </div>
                    </div>;
                  }}</For>
                </div>
              </Show>
            </Show>
          </div>
        </div>
      </section>
    </>
  );
  const clonePanel = () => (
    <div class="analysis-v1-tts-clone-mode">
      <Show when={advClones().length} fallback={<div class="analysis-v1-empty">还没有克隆音色 ID</div>}>
        <div class="analysis-v1-tts-clone-table" role="table" aria-label="克隆音色">
          <div class="analysis-v1-tts-clone-table-head" role="row">
            <span>克隆ID</span>
            <span>Tempo</span>
            <span>播放</span>
            <span>试听</span>
          </div>
          <For each={advClones()}>{(item) => {
            const cloneItem = () => cloneCandidateItem(item);
            const key = () => candidateKey(cloneItem());
            const edit = () => normalizePromptEditEntry(promptEdits()[key()], cloneItem()) || {};
            const tempo = () => String(edit().tempo || defaultTempoForItem(cloneItem()));
            const tableItem = () => ({ ...cloneItem(), tempo: tempo(), ...(edit().prompt ? { prompt: edit().prompt, generation_prompt: edit().prompt, tts_builder_prompt: edit().prompt } : {}) });
            const playKey = () => previewCacheKey(tableItem(), key());
            const audioUrl = () => candidateAudioUrl(tableItem(), key());
            const canPlay = () => Boolean(audioUrl()) || canGenerateCandidatePreview(tableItem());
            const previewBusy = () => busy() === `preview:${playKey()}`;
            return <div class="analysis-v1-tts-clone-table-row" role="row" title={cloneItem().voice_id || item.reference_audio_sha256 || ""}>
              <div class="analysis-v1-tts-clone-id" role="cell">
                <strong>{cloneVoiceSuffix(cloneItem().voice_id)}</strong>
              </div>
              <label class="analysis-v1-tts-clone-tempo" role="cell" title="Tempo">
                <input
                  type="text"
                  inputmode="decimal"
                  pattern="[0-9.]*"
                  value={tempo()}
                  onInput={(event) => {
                    const next = sanitizeTempoInput(event.currentTarget.value);
                    event.currentTarget.value = next;
                    saveCloneTempo(cloneItem(), next);
                  }}
                />
              </label>
              <button class={`icon-action analysis-v1-tts-row-action ${playingKey() === playKey() ? "is-playing" : ""}`} type="button" title={playingKey() === playKey() ? "停止" : audioUrl() ? "播放" : "生成并播放"} aria-label={`${playingKey() === playKey() ? "停止" : "播放"} ${cloneItem().voice_label || cloneItem().voice_id || key()}`} disabled={!canPlay() || previewBusy()} onClick={() => void playCandidate(key(), tableItem())}><Show when={playingKey() === playKey()} fallback={<PlayClipIcon />}><StopIcon /></Show></button>
              <button class="icon-action analysis-v1-tts-row-action" type="button" title="打开试听声音" aria-label={`打开试听声音 ${cloneItem().voice_label || cloneItem().voice_id || key()}`} onClick={() => setPreviewItem(tableItem())}><CodeIcon /></button>
            </div>;
          }}</For>
        </div>
      </Show>
    </div>
  );

  createEffect(() => {
    const items = candidates();
    if (!items.length || selectedKey()) return;
    const currentPayload = payload() || {};
    const persistedSelection = currentPayload.selected_tts_candidate || currentPayload.tts_builder_selection || {};
    const persistedId = String(currentPayload.selected_candidate_id || currentPayload.selected_tts_candidate_id || persistedSelection.candidate_id || "").trim();
    const persistedVoice = String(persistedSelection.voice_id || persistedSelection.voice || "").trim();
    const selectedIndex = items.findIndex((item, index) => {
      const key = candidateKey(item, index);
      const rowId = String(item?.candidate_id || "").trim();
      const rowVoice = String(item?.voice_id || item?.voice || item?.voice_label || "").trim();
      return Boolean(item?.selected || item?.is_selected)
        || (persistedId && [key, rowId, rowVoice].includes(persistedId))
        || (persistedVoice && rowVoice === persistedVoice);
    });
    if (selectedIndex >= 0) {
      setSelectedKey(candidateKey(items[selectedIndex], selectedIndex));
      return;
    }
    try {
      const saved = window.localStorage.getItem(selectionStorageKey());
      if (saved && items.some((item, index) => candidateKey(item, index) === saved)) setSelectedKey(saved);
    } catch {
      // Ignore storage access failures.
    }
  });

  createEffect(() => {
    try {
      const raw = window.localStorage.getItem(promptStorageKey());
      if (raw) setPromptEdits(JSON.parse(raw));
    } catch {
      setPromptEdits({});
    }
  });

  onCleanup(() => {
    audioEl?.pause();
    audioEl = null;
    window.cancelAnimationFrame(rafId);
    clearCandidateReloadTimers();
    stopDialogDrag();
  });

  return <>
    <section
      class="verify-dialog analysis-v1-tts-dialog"
      classList={{ "is-dragged": Boolean(dialogPosition()), "is-clone-mode": cloneMode() }}
      style={dialogPosition() ? { left: `${dialogPosition().left}px`, top: `${dialogPosition().top}px`, transform: "none" } : {}}
      onMouseDown={(event) => event.stopPropagation()}
      onClick={(event) => event.stopPropagation()}
    >
      <div class="env-dialog-head analysis-v1-tts-head" onMouseDown={startDialogDrag}>
        <div class="analysis-v1-tts-title">
          <span class="analysis-v1-tts-title-icon"><SpeechIcon /></span>
          <h3>{cloneMode() ? "音色克隆" : "配音声音"}</h3>
        </div>
        <button class="secondary analysis-v1-tts-close" type="button" title="关闭" aria-label="关闭" onClick={props.onClose}><CloseIcon /></button>
      </div>
      <div class="analysis-v1-tts-body">
        <section class="analysis-v1-tts-panel">
          <Show when={!cloneMode()}>
            <div class="analysis-v1-tts-view-switch" role="tablist" aria-label="配音声音模式">
              <button type="button" role="tab" classList={{ "is-active": voiceView() === "normal" }} aria-selected={voiceView() === "normal" ? "true" : "false"} onClick={() => chooseVoiceView("normal")}>常规声音</button>
              <button type="button" role="tab" classList={{ "is-active": voiceView() === "match" }} aria-selected={voiceView() === "match" ? "true" : "false"} onClick={() => chooseVoiceView("match")}>音色匹配</button>
            </div>
          </Show>
          <Show when={cloneMode() || voiceView() === "match"}>
            <div class="analysis-v1-tts-active">
              <div class="analysis-v1-tts-reference-row">
                <div class="analysis-v1-tts-gender">{inferVoiceBadge(sceneProfile())}</div>
                <div class="analysis-v1-tts-reference-path">
                  <span>参考声音</span>
                  <code title={AUDIO_REFERENCE_PATH}>{referenceLabel()}</code>
                  <button class="icon-action analysis-v1-tts-icon-button" type="button" title="复制 SessionOutput 路径" aria-label="复制 SessionOutput 路径" onClick={() => void copyReferencePath()}><CopyIcon /></button>
                  <button class="icon-action analysis-v1-tts-icon-button" classList={{ "is-uploading": busy() === "upload" }} type="button" title={busy() === "upload" ? `上传中 ${uploadPercent()}%` : "上传参考声音"} aria-label={busy() === "upload" ? `上传参考声音中 ${uploadPercent()}%` : "上传参考声音"} disabled={Boolean(busy())} onClick={() => uploadInput?.click()}><UploadIcon /></button>
                  <input ref={(el) => { uploadInput = el; }} class="analysis-v1-tts-upload-input" type="file" accept={REFERENCE_MEDIA_ACCEPT} onChange={(event) => { const file = event.currentTarget.files?.[0]; if (file) void uploadReferenceAudio(file); event.currentTarget.value = ""; }} />
                  <button class={`icon-action analysis-v1-tts-icon-button ${playingKey() === "reference" ? "is-playing" : ""}`} type="button" title={playingKey() === "reference" ? "停止参考声音" : "播放参考声音"} aria-label={playingKey() === "reference" ? "停止参考声音" : "播放参考声音"} disabled={!referenceAudioUrl()} onClick={() => play("reference", referenceAudioUrl(), { selection: true })}><Show when={playingKey() === "reference"} fallback={<PlayClipIcon />}><StopIcon /></Show></button>
                  <Show when={cloneMode()}>
                    <div class="analysis-v1-tts-clone-top-actions">
                      <button class="secondary" type="button" title="按当前默认配置授权克隆" disabled={Boolean(advBusy())} onClick={() => setCloneConsentOpen(true)}><SaveIcon />授权克隆</button>
                      <button class="secondary" type="button" title="打开克隆配置" onClick={() => setCloneConfigOpen(true)}><SlidersIcon />克隆配置</button>
                    </div>
                  </Show>
                  <Show when={!cloneMode()}>
                    <div class="analysis-v1-tts-builder-actions">
                      <button class="secondary" type="button" title="运行 03_02_TTSBuilderQuick" disabled={Boolean(busy())} onClick={() => void runBuilderG("quick")}>{busy() === "builder" ? "运行中" : "快速匹配"}</button>
                      <button class="secondary" type="button" title="运行 03_03_TTSBuilderQuickAdv" disabled={Boolean(busy())} onClick={() => void runBuilderG("quick_adv")}>{busy() === "builder" ? "运行中" : "高级匹配"}</button>
                      <button class="secondary" type="button" title="打开高级匹配配置" disabled={Boolean(busy())} onClick={() => setMatchConfigOpen(true)}><SlidersIcon />匹配配置</button>
                    </div>
                  </Show>
                </div>
              </div>
              <Show when={uploadProgress()}>
                {(progress) => <div class={`analysis-v1-tts-upload-progress ${progress().phase || ""}`}>
                  <div class="analysis-v1-tts-upload-progress-head">
                    <strong>{progress().phase === "done" ? "已上传" : progress().phase === "processing" ? "保存中" : progress().phase === "error" ? "上传失败" : `上传中 ${uploadPercent()}%`}</strong>
                    <span title={progress().name || ""}>{progress().name || "参考声音"}</span>
                    <code>{uploadProgressMeta()}</code>
                  </div>
                  <div class="analysis-v1-tts-upload-progress-track" aria-label="参考声音上传进度" aria-valuemin="0" aria-valuemax="100" aria-valuenow={uploadPercent()} role="progressbar">
                    <span style={{ width: `${uploadPercent()}%` }} />
                  </div>
                </div>}
              </Show>
              <ReferenceWaveform audioUrl={referenceAudioUrl()} range={range()} fallbackDuration={referenceDuration()} playheadTime={playheadTime()} onDuration={setReferenceDuration} onRangeChange={setRange} />
            </div>
          </Show>
          <Show when={status()}><div class="analysis-v1-tts-status">{status()}</div></Show>
          <Show when={error()}><div class="banner bad analysis-v1-tts-error">{error()}</div></Show>
          <Show when={cloneMode()} fallback={<Show when={voiceView() === "normal"} fallback={candidatePanel()}>{normalVoicePanel()}</Show>}>
            {clonePanel()}
          </Show>
        </section>
      </div>
    </section>
    <Show when={previewItem()}>
      <div class="drawer-backdrop analysis-v1-tts-preview-backdrop" />
      <TTSPreviewDialog item={previewItem()} sampleUrl={candidateAudioUrl(previewItem() || {})} savedPrompt={String(previewPromptEdit().prompt || "")} savedPromptMeta={previewPromptEdit()} ttsModelConfig={props.ttsModelConfig} taskId={props.taskId} sessionId={props.sessionId} api={props.api} onClose={() => setPreviewItem(null)} onSavePrompt={saveCandidatePrompt} onApplyToStoryBoard={applyCandidatePromptToStoryBoard} onPlayPreview={(url) => play("preview", url)} />
    </Show>
    <Show when={matchConfigOpen()}>
      {matchConfigDialog()}
    </Show>
    <Show when={cloneConfigOpen()}>
      {cloneConfigDialog()}
    </Show>
    <Show when={cloneConsentOpen()}>
      {cloneConsentDialog()}
    </Show>
  </>;
}
