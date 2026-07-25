import { createEffect, createMemo, createSignal, untrack } from "solid-js";
import { GOOGLE_TTS_SCENARIO_GUIDES, googleTtsScenarioById } from "../../../../shared/tts/googleTtsScenarioGuide";

const DEFAULT_REQUEST = "生成一段可编辑的语音台词，我来调整";
const DEFAULT_ROLE_STYLE = ["自然口播", "清晰", "可信"];
const DEFAULT_ROLE_PACE = ["中速", "句尾干净"];
const DEFAULT_ROLE_PREFIX = uniqueWords([...DEFAULT_ROLE_STYLE, ...DEFAULT_ROLE_PACE]).join("、");
const FALLBACK_VOICES = ["Kore", "Puck", "Aoede", "Achernar", "Enceladus", "Laomedeia", "Vindemiatrix"];
const GOOGLE_VOICE_DESCRIPTIONS = {
  Achernar: "柔和",
  Achird: "友好",
  Algenib: "沙哑",
  Algieba: "平滑",
  Alnilam: "坚定",
  Aoede: "轻快",
  Autonoe: "明亮",
  Callirrhoe: "放松",
  Charon: "信息丰富",
  Despina: "平滑",
  Enceladus: "气声",
  Erinome: "清晰",
  Fenrir: "易兴奋",
  Gacrux: "成熟",
  Iapetus: "清晰",
  Kore: "坚定",
  Laomedeia: "欢快",
  Leda: "年轻",
  Orus: "坚定",
  Puck: "欢快",
  Pulcherrima: "直接",
  Rasalgethi: "信息丰富",
  Sadachbia: "活泼",
  Sadaltager: "知识渊博",
  Schedar: "平稳",
  Sulafat: "温暖",
  Umbriel: "随和",
  Vindemiatrix: "温柔",
  Zephyr: "明亮",
  Zubenelgenubi: "随意",
};
const ASSET_AUDIO_REL = "SessionOutput/storyboard/assets/audios";
const INITIAL_AGENT_MESSAGE = "选择 Agent 音频可恢复生成过程；点击新增 Session 可以创建新的语音合成。";

function copy(value) {
  return JSON.parse(JSON.stringify(value || {}));
}

function text(value) {
  return String(value ?? "").trim();
}

function compactText(value) {
  return text(value).replace(/\s+/g, " ");
}

function wordsFromText(value) {
  return text(value).split(/[、,，;；]/).map(text).filter(Boolean);
}

function clampTempo(value) {
  const next = Number(value);
  if (!Number.isFinite(next)) return 1;
  return Math.min(2, Math.max(0.5, Math.round(next * 100) / 100));
}

function rolePromptPrefix(role = {}) {
  return text(role.prompt_prefix) || uniqueWords([...(role.style || []), ...(role.pace || [])]).join("、") || DEFAULT_ROLE_PREFIX;
}

function makeMessage(role, value, index = 0) {
  const now = Date.now();
  return {
    id: `m${now}_${index}`,
    role,
    text: text(value),
    created_at: now,
  };
}

function normalizeMessage(value, index = 0) {
  const source = value && typeof value === "object" ? value : {};
  const role = source.role === "user" ? "user" : "assistant";
  const content = text(source.text);
  if (!content) return null;
  const createdAt = Number(source.created_at || source.createdAt || Date.now());
  return {
    id: text(source.id) || `m${createdAt}_${index}`,
    role,
    text: content,
    created_at: Number.isFinite(createdAt) ? createdAt : Date.now(),
  };
}

function normalizeMessages(values) {
  return (Array.isArray(values) ? values : [])
    .map((item, index) => normalizeMessage(item, index))
    .filter(Boolean)
    .slice(-200);
}

function safeName(value, fallback = "tts_agent") {
  return text(value || fallback).normalize("NFKC").replace(/[^\p{L}\p{N}_:-]+/gu, "_").replace(/^_+|_+$/g, "") || fallback;
}

function audioExt(provider) {
  return provider === "xai" ? "mp3" : "wav";
}

function selectedProviderConfig(config) {
  const providers = Array.isArray(config?.providers) ? config.providers : [];
  const activeProvider = text(config?.active_public_provider || config?.active_provider).toLowerCase();
  return providers.find((item) => text(item.public_provider || item.provider_alias || item.provider).toLowerCase() === activeProvider)
    || providers.find((item) => item.active)
    || providers.find((item) => item.has_api_key && item.enabled !== false)
    || providers[0]
    || null;
}

function selectedModel(provider) {
  const models = Array.isArray(provider?.models) ? provider.models : [];
  return models.find((item) => item.model === provider?.model) || models.find((item) => String(item.model || "").includes("tts")) || models[0] || null;
}

function selectedTtsSettings(config) {
  const provider = selectedProviderConfig(config);
  const model = selectedModel(provider);
  const modelId = text(model?.model || provider?.model);
  const voiceId = text(provider?.selected_voice_by_model?.[modelId] || model?.voices?.[0]?.voice_id);
  return {
    provider: text(provider?.provider),
    providerLabel: text(provider?.provider_label || provider?.provider),
    model: modelId,
    voiceId,
    hasApiKey: Boolean(provider?.has_api_key),
    voices: Array.isArray(model?.voices) ? model.voices : [],
  };
}

function voiceIds(settings) {
  const values = settings?.voices?.map((item) => text(item.voice_id)).filter(Boolean) || [];
  return values.length ? values : FALLBACK_VOICES;
}

function voiceOptionFromValue(value, index = 0) {
  const source = value && typeof value === "object" ? value : {};
  const voiceId = text(source.voice_id || source.id || source.voice || source.name || value);
  const label = text(source.label || source.name || source.title || voiceId);
  const labelParts = label.split(/\s+-\s+|\s+—\s+|\s+–\s+/).map(text).filter(Boolean);
  const name = text(source.name || labelParts[0] || voiceId);
  const configuredDescription = text(source.description || source.style || source.tone || source.sample_text || labelParts.slice(1).join(" - "));
  const description = configuredDescription || GOOGLE_VOICE_DESCRIPTIONS[voiceId] || GOOGLE_VOICE_DESCRIPTIONS[name] || "内置 TTS 声音";
  return {
    voice_id: voiceId || FALLBACK_VOICES[index % FALLBACK_VOICES.length],
    name: name || voiceId || FALLBACK_VOICES[index % FALLBACK_VOICES.length],
    gender: text(source.gender || source.sex || source.voice_gender) || "未标注",
    description,
    label: label || `${name || voiceId} - ${description}`,
    sample_text: text(source.sample_text),
  };
}

function voiceOptionItems(settings) {
  const source = Array.isArray(settings?.voices) && settings.voices.length ? settings.voices : FALLBACK_VOICES;
  const items = source.map((item, index) => voiceOptionFromValue(item, index)).filter((item) => item.voice_id);
  const seen = new Set();
  return items.filter((item) => {
    if (seen.has(item.voice_id)) return false;
    seen.add(item.voice_id);
    return true;
  });
}

function voiceForSettings(settings, requested, index = 0) {
  const providerVoices = settings?.voices?.map((item) => text(item.voice_id)).filter(Boolean) || [];
  const requestedId = text(requested);
  if (requestedId && (!providerVoices.length || providerVoices.includes(requestedId))) return requestedId;
  return text(settings?.voiceId) || providerVoices[index % Math.max(providerVoices.length, 1)] || FALLBACK_VOICES[index % FALLBACK_VOICES.length];
}

function speakerId(value, index = 0) {
  return safeName(`speaker_${value || index + 1}`, `speaker_${index + 1}`);
}

function boundedRoleCount(value) {
  const count = Number(value);
  if (!Number.isFinite(count)) return 0;
  return Math.max(1, Math.min(8, Math.round(count)));
}

function repeatedSingleRoleMentionCount(source) {
  const matches = [...text(source).matchAll(/(?:一个|一位|一名|1\s*(?:个|位|名)?)\s*[A-Za-z0-9\u4e00-\u9fa5]{0,8}(?:角色|人物|人|speaker|声音|配音)/gi)];
  if (matches.length < 2) return 0;
  const firstIndex = matches[0].index || 0;
  const last = matches[matches.length - 1];
  const lastIndex = (last.index || 0) + last[0].length;
  const span = text(source).slice(firstIndex, lastIndex);
  return /[和与跟、,，]/.test(span) ? boundedRoleCount(matches.length) : 0;
}

function requestedRoleCount(value) {
  const source = text(value);
  if (!source) return 0;
  const digit = source.match(/([2-8])\s*(?:个|位|名)?\s*(?:[A-Za-z0-9\u4e00-\u9fa5]{0,8})?(?:角色|人物|人|speaker|声音|配音)/i);
  if (digit) return boundedRoleCount(digit[1]);
  const chinese = source.match(/([二两三四五六七八])\s*(?:个|位|名)?\s*(?:[A-Za-z0-9\u4e00-\u9fa5]{0,8})?(?:角色|人物|人|speaker|声音|配音)/i);
  if (chinese) return boundedRoleCount({ 一: 1, 二: 2, 两: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8 }[chinese[1]]);
  const repeatedSingles = repeatedSingleRoleMentionCount(source);
  if (repeatedSingles) return repeatedSingles;
  const singleDigit = source.match(/(1)\s*(?:个|位|名)?\s*(?:[A-Za-z0-9\u4e00-\u9fa5]{0,8})?(?:角色|人物|人|speaker|声音|配音)/i);
  if (singleDigit) return 1;
  const singleChinese = source.match(/(一)\s*(?:个|位|名)?\s*(?:[A-Za-z0-9\u4e00-\u9fa5]{0,8})?(?:角色|人物|人|speaker|声音|配音)/i);
  if (singleChinese) return 1;
  if (/单人|单个角色|单角色|一个人|一个角色|一位角色|一名角色|1\s*个角色/i.test(source)) return 1;
  if (/双人|两人|二人|两个角色|两个.*配音|2\s*个角色/i.test(source)) return 2;
  if (/多人|多个角色/.test(source)) return 3;
  return 0;
}

function requestedRoleGender(value) {
  const source = text(value).toLowerCase();
  if (/女|女性|女生|女声|female/.test(source)) return "female";
  if (/男|男性|男生|男声|male/.test(source)) return "male";
  return "";
}

function defaultRoleName(index, request = "") {
  const gender = requestedRoleGender(request);
  if (index === 0 && gender === "female") return "女性角色";
  if (index === 0 && gender === "male") return "男性角色";
  return `角色${String.fromCharCode(65 + Math.min(index, 25))}`;
}

function impliedRoleCount(value) {
  const requested = requestedRoleCount(value);
  if (requested) return requested;
  return /对话|互相|交流/.test(text(value)) ? 2 : 1;
}

function uniqueRoleRows(rows) {
  const seen = new Set();
  return (Array.isArray(rows) ? rows : []).map((role, index) => {
    const speaker = text(role?.speaker || role?.name) || `角色${index + 1}`;
    const preferred = text(role?.speaker_id);
    let id = preferred && !seen.has(preferred) ? preferred : speakerId(speaker, index);
    let suffix = 2;
    while (seen.has(id)) {
      id = `${speakerId(speaker, index)}_${suffix}`;
      suffix += 1;
    }
    seen.add(id);
    return { ...role, speaker, speaker_id: id };
  });
}

function inferRoleNames(value) {
  const source = text(value);
  const targetCount = impliedRoleCount(source);
  const names = [];
  const seen = new Set();
  const blocked = ["角色", "生成", "帮我", "调整", "对话", "两个", "一个", "多人", "人的"];
  const add = (name, options = {}) => {
    const cleaned = text(name).replace(/^一个/, "").replace(/[，,。；;：:、\s]+$/g, "");
    if (!cleaned || seen.has(cleaned) || cleaned.length > 12 || (!options.fallback && blocked.some((word) => cleaned.includes(word)))) return;
    seen.add(cleaned);
    names.push(cleaned);
  };
  for (const match of source.matchAll(/一个\s*([A-Za-z0-9\u4e00-\u9fa5]{1,12})/g)) add(match[1]);
  const scoped = source.includes("角色") ? source.slice(source.indexOf("角色")) : source;
  for (const match of scoped.matchAll(/([A-Za-z0-9\u4e00-\u9fa5]{1,12})[、和跟与,，]\s*([A-Za-z0-9\u4e00-\u9fa5]{1,12})/g)) {
    if (names.length < 2) {
      add(match[1]);
      add(match[2]);
    }
  }
  if (!names.length) {
    for (let index = 0; index < targetCount; index += 1) add(defaultRoleName(index, source), { fallback: true });
  }
  while (names.length < targetCount) add(defaultRoleName(names.length, source), { fallback: true });
  return names.slice(0, targetCount || 8);
}

function roleRowFromName(name, settings, index = 0) {
  return {
    speaker_id: speakerId(name, index),
    speaker: name,
    voice: voiceForSettings(settings, "", index),
    style: [...DEFAULT_ROLE_STYLE],
    pace: [...DEFAULT_ROLE_PACE],
    prompt_prefix: DEFAULT_ROLE_PREFIX,
    scenario_id: "style-control",
  };
}

function enforceRoleCountForRequest(rows, request, settings) {
  const targetCount = requestedRoleCount(request);
  if (!targetCount) return uniqueRoleRows(rows);
  const next = uniqueRoleRows(rows).slice(0, targetCount);
  while (next.length < targetCount) {
    next.push(roleRowFromName(defaultRoleName(next.length, request), settings, next.length));
  }
  return uniqueRoleRows(next);
}

function normalizeRoleDrafts(draft, settings, userText = "") {
  const voices = voiceIds(settings);
  const rows = Array.isArray(draft?.roles) ? draft.roles : [];
  const roles = rows.map((item, index) => {
    const speaker = text(item?.speaker || item?.name) || `角色${index + 1}`;
    return {
      speaker_id: text(item?.speaker_id) || speakerId(speaker, index),
      speaker,
      voice: voiceForSettings(settings, item?.voice, index) || voices[index % voices.length],
      style: Array.isArray(item?.style) ? item.style.map(text).filter(Boolean) : [...DEFAULT_ROLE_STYLE],
      pace: Array.isArray(item?.pace) ? item.pace.map(text).filter(Boolean) : [...DEFAULT_ROLE_PACE],
      prompt_prefix: text(item?.prompt_prefix) || uniqueWords([...(Array.isArray(item?.style) ? item.style.map(text).filter(Boolean) : DEFAULT_ROLE_STYLE), ...(Array.isArray(item?.pace) ? item.pace.map(text).filter(Boolean) : DEFAULT_ROLE_PACE)]).join("、"),
      scenario_id: "style-control",
    };
  }).filter((item) => item.speaker);
  return roles.length ? enforceRoleCountForRequest(roles, userText, settings) : rolesFromRequest(userText, settings);
}

function generatedDialogueRowsFromDraft(draft, roleRows, request = "") {
  const roleByName = new Map(roleRows.map((role) => [role.speaker, role]));
  const rows = Array.isArray(draft?.dialogues) ? draft.dialogues : [];
  const dialogues = rows.map((item, index) => {
    const role = roleByName.get(text(item?.speaker)) || roleRows[index % Math.max(roleRows.length, 1)] || roleRows[0];
    const sourceText = text(item?.text || item?.line || item?.dialogue);
    if (!sourceText) return null;
    return {
      line_id: `line_${String(index + 1).padStart(3, "0")}`,
      dialogue_id: "asset_audio_dialogue_001",
      dialogue_asset_key: "asset_audio_dialogue_001",
      scene_id: "asset_audio_dialogue",
      shot_id: "asset_library_audio",
      scene_label: "Dialogue 01",
      dialogue_label: `Line ${String(index + 1).padStart(2, "0")}`,
      speaker_id: role?.speaker_id || "speaker_1",
      voice_id: voiceForSettings(null, item?.voice || role?.voice, index),
      source_text: sourceText,
      tempo: clampTempo(item?.tempo || 1),
      scenario_id: "",
      scenario_keywords: [],
    };
  }).filter(Boolean);
  return dialogues.length ? dialogues : generatedDialogueRows(request, roleRows);
}

function rolesFromRequest(value, settings) {
  return uniqueRoleRows(inferRoleNames(value).map((name, index) => roleRowFromName(name, settings, index)));
}

function generatedDialogueRows(request, roleRows) {
  const names = roleRows.map((role) => role.speaker);
  const topic = compactText(request).replace(/^我有.*?帮我/, "帮我").slice(0, 80) || "围绕当前需求展开一段自然对话";
  const templates = roleRows.length <= 1 ? [
    `${names[0] || "角色A"}用一句自然开场说明重点：${topic}`,
    `${names[0] || "角色A"}补充核心信息，语气保持清晰自然。`,
    `${names[0] || "角色A"}再强调一个细节，让表达更可信。`,
    `${names[0] || "角色A"}用一句话收束，方便后续调整。`,
  ] : [
    `${names[0] || "角色一"}先说明需求和目标。`,
    `${names[1] || names[0] || "角色二"}回应，并提出需要调整的地方。`,
    `${names[0] || "角色一"}补充细节，让语气更自然。`,
    `${names[1] || names[0] || "角色二"}确认下一步，收束这段对话。`,
  ];
  return templates.map((line, index) => {
    const role = roleRows[index % Math.max(roleRows.length, 1)] || roleRows[0];
    return {
      line_id: `line_${String(index + 1).padStart(3, "0")}`,
      dialogue_id: "asset_audio_dialogue_001",
      dialogue_asset_key: "asset_audio_dialogue_001",
      scene_id: "asset_audio_dialogue",
      shot_id: "asset_library_audio",
      scene_label: "Dialogue 01",
      dialogue_label: `Line ${String(index + 1).padStart(2, "0")}`,
      speaker_id: role?.speaker_id || "speaker_1",
      source_text: roleRows.length <= 1 ? line : index === 0 ? line.replace("需求和目标", `需求和目标：${topic}`) : line,
      tempo: 1,
      scenario_id: "",
      scenario_keywords: [],
    };
  });
}

function roleForDialogue(roles, dialogue) {
  return roles.find((item) => item.speaker_id === dialogue.speaker_id) || roles[0] || {
    speaker_id: "speaker_narrator",
    speaker: "旁白",
    voice: FALLBACK_VOICES[0],
    style: [...DEFAULT_ROLE_STYLE],
    pace: [...DEFAULT_ROLE_PACE],
  };
}

function buildPromptText(dialogue, role, keywords) {
  const local = Array.isArray(keywords) ? keywords : [];
  const tags = local.filter((item) => String(item).startsWith("["));
  const words = local.filter((item) => !String(item).startsWith("["));
  const parts = [
    rolePromptPrefix(role),
    ...words,
  ].filter(Boolean);
  return `${parts.join("、")}${tags.length ? `；保留 ${tags.join("、")}` : ""}；只朗读当前句，不改词、不加词：${dialogue.source_text}`;
}

function renderedPrompt(dialogue, role) {
  const finalPrompt = text(dialogue.final_prompt);
  if (finalPrompt) return finalPrompt;
  return buildPromptText(dialogue, role, Array.isArray(dialogue.scenario_keywords) ? dialogue.scenario_keywords : []);
}

function scenarioPrimaryWords(scenarioId) {
  const scenario = googleTtsScenarioById(scenarioId);
  const styleWords = scenario.groups[0]?.words?.slice(0, 3) || [];
  const paceWords = scenario.groups[1]?.words?.slice(0, 3) || [];
  return { styleWords, paceWords };
}

function uniqueWords(values) {
  const result = [];
  const seen = new Set();
  for (const value of Array.isArray(values) ? values : []) {
    const word = text(value);
    if (!word || seen.has(word)) continue;
    seen.add(word);
    result.push(word);
  }
  return result;
}

function keywordGroupBucket(groupTitle) {
  return /节奏|速度|停顿|pace|pacing|tempo/i.test(text(groupTitle)) ? "paceWords" : "styleWords";
}

function normalizeScenarioSelections(source = {}) {
  const raw = source?.scenario_selections || source?.scenarioSelections || {};
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
  const result = {};
  for (const [key, value] of Object.entries(raw)) {
    const scenarioId = text(key);
    const words = uniqueWords(value || []);
    if (scenarioId && words.length) result[scenarioId] = words;
  }
  return result;
}

function flattenScenarioSelections(selections = {}) {
  return uniqueWords(Object.values(selections || {}).flat());
}

function guideSeedForScenario(kind, scenarioId, source = {}) {
  const savedSelections = normalizeScenarioSelections(source);
  if (Object.keys(savedSelections).length) {
    return {
      styleWords: [],
      paceWords: [],
      keywords: [],
      scenarioSelections: savedSelections,
    };
  }
  if (kind === "role") {
    const selectedWords = wordsFromText(rolePromptPrefix(source));
    return {
      styleWords: selectedWords,
      paceWords: [],
      keywords: [],
      scenarioSelections: scenarioId ? { [scenarioId]: selectedWords } : {},
    };
  }
  return {
    styleWords: [],
    paceWords: [],
    keywords: uniqueWords(source.scenario_keywords || []),
    scenarioSelections: source.scenario_id ? { [source.scenario_id]: uniqueWords(source.scenario_keywords || []) } : {},
  };
}

function guideScenarioWords(current, scenarioId = "") {
  if (!current) return [];
  const key = text(scenarioId || current.scenario_id);
  if (current.scenarioSelections && Object.prototype.hasOwnProperty.call(current.scenarioSelections, key)) {
    return uniqueWords(current.scenarioSelections[key] || []);
  }
  return [];
}

function guideAllWords(current) {
  if (!current) return [];
  return flattenScenarioSelections(current.scenarioSelections || {});
}

function sourceTextFromPromptPrompt(value, fallback = "") {
  const source = text(value);
  if (!source) return text(fallback);
  const marker = "只朗读当前句，不改词、不加词：";
  const index = source.lastIndexOf(marker);
  if (index >= 0) return text(source.slice(index + marker.length)) || text(fallback);
  return source;
}

function dialogueOutputText(dialogue, role) {
  return sourceTextFromPromptPrompt(renderedPrompt(dialogue, role), dialogue.source_text);
}

function assetPath(asset) {
  return text(asset?.path || asset?.history_path || asset?.audio_path || asset?.id);
}

function sessionJsonPath(value) {
  const source = value && typeof value === "object" ? value : {};
  return text(source.json_path || source.agent_session_path || source.session_path || source?.origin?.json_path);
}

function normalizeSession(value) {
  const source = value && typeof value === "object" ? value : {};
  const id = text(source.id) || `tts_agent_${Date.now()}`;
  const jsonPath = sessionJsonPath(source);
  return {
    id,
    title: text(source.title) || `Agent Session ${id.slice(-6)}`,
    created_at: Number(source.created_at || Date.now()),
    updated_at: Number(source.updated_at || Date.now()),
    json_path: jsonPath,
    agent_session_path: jsonPath,
    request_text: text(source.request_text),
    roles: uniqueRoleRows(source.roles),
    dialogues: Array.isArray(source.dialogues) ? source.dialogues : [],
    messages: normalizeMessages(source.messages),
    audio: source.audio && typeof source.audio === "object" ? source.audio : null,
    audio_state: text(source.audio_state) || (source.audio?.path ? "ready" : "empty"),
    progress_text: text(source.progress_text),
  };
}

function sessionFromAudioAsset(asset) {
  const path = assetPath(asset);
  const payload = asset?.tts_agent_session && typeof asset.tts_agent_session === "object" ? asset.tts_agent_session : {};
  if (!path || (!asset?.agent_session_path && !Object.keys(payload).length)) return null;
  const filename = path.split("/").pop() || "Audio";
  const stem = filename.replace(/\.[^.]+$/, "");
  return normalizeSession({
    ...payload,
    id: text(payload.id || payload.session_id) || safeName(stem, "tts_agent_audio"),
    title: text(payload.title) || stem,
    request_text: text(payload.request_text || payload.prompt || payload.user_text),
    roles: Array.isArray(payload.roles) ? payload.roles : (Array.isArray(payload.role_table) ? payload.role_table : []),
    dialogues: Array.isArray(payload.dialogues) ? payload.dialogues : (Array.isArray(payload.tts_table) ? payload.tts_table : []),
    messages: Array.isArray(payload.messages) ? payload.messages : (Array.isArray(payload.chat_messages) ? payload.chat_messages : []),
    audio: payload.audio && typeof payload.audio === "object" ? payload.audio : asset,
    audio_state: text(payload.audio_state) || "ready",
    json_path: sessionJsonPath(payload) || sessionJsonPath(asset),
  });
}

function ttsConfigKey(dialogues, roles, settings) {
  return JSON.stringify({
    dialogue_id: "asset_audio_dialogue_001",
    dialogue_asset_key: "asset_audio_dialogue_001",
    provider: settings.provider,
    model: settings.model,
    lines: dialogues.map((dialogue) => {
      const role = roleForDialogue(roles, dialogue);
      return {
        line_id: dialogue.line_id,
        speaker_id: role.speaker_id,
        voice_id: dialogue.voice_id || role.voice || settings.voiceId,
        tempo: clampTempo(dialogue.tempo || 1),
        text: dialogueOutputText(dialogue, role),
        prompt: renderedPrompt(dialogue, role),
      };
    }),
  });
}

export function createTtsAgentController(options = {}) {
  const api = options.api;
  const task = options.task || (() => null);
  const sessionId = options.sessionId || (() => 0);
  const onAssetLibraryResult = options.onAssetLibraryResult;
  const active = () => typeof options.active === "function" ? Boolean(options.active()) : options.active !== false;

  const [requestText, setRequestText] = createSignal(DEFAULT_REQUEST);
  const [workspaceMode, setWorkspaceMode] = createSignal("library");
  const [agentSessions, setAgentSessions] = createSignal([]);
  const [activeSessionId, setActiveSessionId] = createSignal("");
  const [selectedUploadAsset, setSelectedUploadAsset] = createSignal(null);
  const [started, setStarted] = createSignal(false);
  const [roles, setRoles] = createSignal([]);
  const [dialogues, setDialogues] = createSignal([]);
  const [messages, setMessages] = createSignal([
    makeMessage("assistant", INITIAL_AGENT_MESSAGE, 0),
  ]);
  const [guide, setGuide] = createSignal(null);
  const [playingDialogueId, setPlayingDialogueId] = createSignal("");
  const [generatingDialogueId, setGeneratingDialogueId] = createSignal("");
  const [previewingVoiceId, setPreviewingVoiceId] = createSignal("");
  const [audioState, setAudioState] = createSignal("empty");
  const [audioPlaying, setAudioPlaying] = createSignal(false);
  const [playingAssetPath, setPlayingAssetPath] = createSignal("");
  const [toast, setToast] = createSignal("");
  const [ttsConfig, setTtsConfig] = createSignal(null);
  const [ttsConfigLoaded, setTtsConfigLoaded] = createSignal(false);
  const [ttsConfigError, setTtsConfigError] = createSignal("");
  const [generationError, setGenerationError] = createSignal("");
  const [progressText, setProgressText] = createSignal("");
  const [audioResult, setAudioResult] = createSignal(null);

  let toastTimer = 0;
  let activeAudio = null;
  let messagesPersistSeq = 0;
  let messagesLoadedFor = "";
  let sessionStorageLoadedFor = "";
  let sessionPersistTimer = 0;
  let sessionArtifactPersistTimer = 0;
  let sessionArtifactPersistSeq = 0;

  const sessionStorageKey = () => `opencrew:asset-library:tts-agent:sessions:${task()?.id || "none"}`;
  const activeSession = createMemo(() => agentSessions().find((item) => item.id === activeSessionId()) || null);

  const ttsSettings = createMemo(() => selectedTtsSettings(ttsConfig()));
  const ttsProviderBlockedReason = createMemo(() => {
    const settings = ttsSettings();
    if (ttsConfigError()) return ttsConfigError();
    if (!ttsConfigLoaded()) return "正在读取 TTS provider 配置";
    if (!settings.provider || !settings.model || !settings.voiceId || !settings.hasApiKey) {
      return "请先在 Connection / TTS Model Settings 配置可用 TTS provider、model、voice 和 API Key。";
    }
    return "";
  });
  const generateDisabledReason = createMemo(() => {
    if (audioState() === "generating") return "真实生成中";
    if (workspaceMode() !== "session") return "请先新增或选择 Agent Session";
    if (!started()) return "请先通过右侧 Agent 发送 TTS 需求";
    if (!dialogues().length) return "请先生成或添加 TTS 表对白";
    return ttsProviderBlockedReason();
  });
  const promptPlayDisabledReason = createMemo(() => {
    if (!started()) return "请先通过右侧 Agent 发送 TTS 需求";
    if (audioState() === "generating") return "真实生成中";
    return ttsProviderBlockedReason();
  });
  const playAudioDisabledReason = createMemo(() => {
    if (workspaceMode() === "upload") return "";
    if (audioState() === "generating") return "真实生成中";
    if (audioState() !== "ready") return "请先生成真实声音文件";
    return "";
  });

  createEffect(() => {
    if (!active()) return;
    const key = sessionStorageKey();
    if (!task()?.id || sessionStorageLoadedFor === key) return;
    sessionStorageLoadedFor = key;
    try {
      const payload = JSON.parse(window.localStorage?.getItem(key) || "{}");
      const sessions = (Array.isArray(payload?.sessions) ? payload.sessions : []).map(normalizeSession);
      setAgentSessions(sessions);
    } catch {
      setAgentSessions([]);
    }
    setWorkspaceMode("library");
    setActiveSessionId("");
    setSelectedUploadAsset(null);
    setStarted(false);
    setRoles([]);
    setDialogues([]);
    setAudioResult(null);
    setAudioState("empty");
    setProgressText("");
    setGenerationError("");
    setMessages([makeMessage("assistant", INITIAL_AGENT_MESSAGE, 0)]);
  });

  createEffect(() => {
    if (!active()) return;
    const taskId = task()?.id;
    if (!taskId) return;
    const key = String(taskId);
    if (messagesLoadedFor === key) return;
    messagesLoadedFor = key;
    void loadPersistedMessages(taskId);
  });

  createEffect(() => {
    if (workspaceMode() !== "session" || !activeSessionId()) return;
    const currentSession = untrack(activeSession);
    const snapshot = {
      id: activeSessionId(),
      title: currentSession?.title || "Agent Session",
      created_at: currentSession?.created_at || Date.now(),
      updated_at: Date.now(),
      json_path: currentSession?.json_path || currentSession?.agent_session_path || "",
      request_text: requestText(),
      roles: roles(),
      dialogues: dialogues(),
      messages: messages(),
      audio: audioResult(),
      audio_state: audioState(),
      progress_text: progressText(),
    };
    setAgentSessions((items) => {
      const exists = items.some((item) => item.id === snapshot.id);
      const next = exists ? items.map((item) => item.id === snapshot.id ? normalizeSession({ ...item, ...snapshot }) : item) : [normalizeSession(snapshot), ...items];
      if (sessionPersistTimer) window.clearTimeout(sessionPersistTimer);
      sessionPersistTimer = window.setTimeout(() => {
        try {
          window.localStorage?.setItem(sessionStorageKey(), JSON.stringify({ sessions: next }));
        } catch {
          // Local session restore is best-effort; generation output remains in Asset Audio.
        }
      }, 120);
      persistSessionArtifact(snapshot);
      return next;
    });
  });
  const audioAsset = createMemo(() => {
    const result = audioResult();
    if (result) return result;
    return {
      filename: "等待生成",
      path: `${ASSET_AUDIO_REL}/`,
      duration_seconds: 0,
      source: "TTS-Agent",
      generation_mode: "real_provider",
      provider: ttsSettings().provider || "",
      model: ttsSettings().model || "",
      audio_src: "",
    };
  });
  const promptItems = createMemo(() => {
    const currentRoles = roles();
    return dialogues().map((dialogue) => {
      const role = roleForDialogue(currentRoles, dialogue);
      return {
        ...dialogue,
        speaker: role.speaker,
        voice: dialogue.voice_id || role.voice,
        tempo: clampTempo(dialogue.tempo || 1),
        rendered_prompt: renderedPrompt(dialogue, role),
      };
    });
  });

  createEffect(() => {
    if (!active()) return;
    void ensureTtsConfig().catch((error) => {
      const message = error instanceof Error ? error.message : String(error);
      setTtsConfigError(message);
      setTtsConfigLoaded(true);
    });
  });

  createEffect(() => {
    const settings = ttsSettings();
    if (!settings.provider || !settings.model || !settings.voiceId) return;
    const currentRoles = roles();
    let changed = false;
    const uniqueRoles = uniqueRoleRows(currentRoles);
    const normalized = uniqueRoles.map((role, index) => {
      const nextVoice = voiceForSettings(settings, role.voice, index);
      if (nextVoice === role.voice) return role;
      changed = true;
      return { ...role, voice: nextVoice };
    });
    if (!changed) changed = normalized.some((role, index) => role.speaker_id !== currentRoles[index]?.speaker_id);
    if (changed) setRoles(normalized);
  });

  function persistMessages(nextMessages) {
    const taskId = task()?.id;
    if (!api?.saveAssetLibraryTtsAgentMessages || !taskId) return;
    const payload = normalizeMessages(nextMessages);
    const seq = ++messagesPersistSeq;
    void api.saveAssetLibraryTtsAgentMessages(taskId, payload).catch((error) => {
      if (seq === messagesPersistSeq) console.warn("TTS Agent messages save failed", error);
    });
  }

  async function loadPersistedMessages(taskId) {
    if (!api?.assetLibraryTtsAgentMessages || !taskId) return;
    try {
      const result = await api.assetLibraryTtsAgentMessages(taskId);
      const saved = normalizeMessages(result?.messages);
      if (!saved.length) return;
      if (String(task()?.id || "") !== String(taskId)) return;
      if (workspaceMode() !== "library" || activeSessionId()) return;
      const current = messages();
      const hasLocalChat = current.some((message) => text(message?.text) && text(message?.text) !== INITIAL_AGENT_MESSAGE);
      if (hasLocalChat) return;
      commitMessages(saved, { persist: false });
    } catch (error) {
      console.warn("TTS Agent messages load failed", error);
    }
  }

  function persistSessionArtifact(snapshot) {
    const taskId = task()?.id;
    if (!api?.saveAssetLibraryTtsAgentSession || !taskId || !snapshot?.id) return;
    if (!snapshot.roles?.length && !snapshot.dialogues?.length) return;
    const seq = ++sessionArtifactPersistSeq;
    if (sessionArtifactPersistTimer) window.clearTimeout(sessionArtifactPersistTimer);
    sessionArtifactPersistTimer = window.setTimeout(() => {
      api.saveAssetLibraryTtsAgentSession(taskId, snapshot.id, snapshot).then((result) => {
        if (seq !== sessionArtifactPersistSeq) return;
        onAssetLibraryResult?.(result);
      }).catch((error) => {
        if (seq === sessionArtifactPersistSeq) console.warn("TTS Agent session save failed", error);
      });
    }, 350);
  }

  function commitMessages(nextMessages, options = {}) {
    const normalized = normalizeMessages(nextMessages);
    setMessages(normalized);
    if (options.persist !== false) persistMessages(normalized);
    return normalized;
  }

  function pushMessage(role, value) {
    setMessages((items) => {
      const nextMessages = [...items, makeMessage(role, value, items.length)];
      persistMessages(nextMessages);
      return nextMessages;
    });
  }

  function showToast(value) {
    setToast(value);
    if (toastTimer) window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => setToast(""), 2600);
  }

  async function ensureTtsConfig() {
    if (ttsConfig()) return ttsConfig();
    if (!api?.assetLibraryTtsModelConfig && !api?.ttsModelConfig) {
      setTtsConfigLoaded(true);
      throw new Error("当前前端没有可用的 TTS ModelConfig API");
    }
    const config = api?.assetLibraryTtsModelConfig && task()?.id
      ? await api.assetLibraryTtsModelConfig(task().id)
      : await api.ttsModelConfig();
    setTtsConfig(config);
    setTtsConfigError("");
    setTtsConfigLoaded(true);
    return config;
  }

  async function refreshTaskDetail() {
    if (!api?.detail || !task()?.id) return;
    try {
      const result = await api.detail(task().id);
      onAssetLibraryResult?.(result);
    } catch {
      // Refresh failure should not hide a successful generated audio result.
    }
  }

  function resetSessionWorkspace(sessionId, title = "Agent Session") {
    setWorkspaceMode("session");
    setSelectedUploadAsset(null);
    setActiveSessionId(sessionId);
    setStarted(false);
    setRoles([]);
    setDialogues([]);
    setRequestText(DEFAULT_REQUEST);
    setAudioResult(null);
    setAudioState("empty");
    setAudioPlaying(false);
    setGenerationError("");
    setProgressText("");
    setMessages([makeMessage("assistant", "新的 Agent Session 已创建。输入语音需求后，我会生成角色表和 TTS 表。", 0)]);
    setAgentSessions((items) => {
      if (items.some((item) => item.id === sessionId)) return items;
      return [normalizeSession({ id: sessionId, title, messages: [makeMessage("assistant", "新的 Agent Session 已创建。输入语音需求后，我会生成角色表和 TTS 表。", 0)] }), ...items];
    });
  }

  function createAgentSession() {
    const id = `tts_agent_${Date.now()}`;
    resetSessionWorkspace(id, `Agent Session ${agentSessions().length + 1}`);
  }

  function loadAgentSession(session) {
    const item = normalizeSession(session);
    setWorkspaceMode("session");
    setSelectedUploadAsset(null);
    setActiveSessionId(item.id);
    setRequestText(item.request_text || DEFAULT_REQUEST);
    setStarted(Boolean(item.roles.length || item.dialogues.length || item.audio));
    setRoles(item.roles);
    setDialogues(item.dialogues);
    setMessages(item.messages.length ? item.messages : [makeMessage("assistant", "已加载 Agent 合成过程。", 0)]);
    setAudioResult(item.audio);
    setAudioState(item.audio_state || (item.audio?.path ? "ready" : "empty"));
    setProgressText(item.progress_text || (item.audio?.filename ? `已加载：${item.audio.filename}` : ""));
    setGenerationError("");
  }

  function persistAgentSessions(next) {
    try {
      window.localStorage?.setItem(sessionStorageKey(), JSON.stringify({ sessions: next }));
    } catch {
      // Local session restore is best-effort; generated JSON/audio files remain on disk.
    }
  }

  function removeAgentSession(sessionId) {
    const id = text(sessionId);
    if (!id) return;
    if (activeSessionId() === id) backToAudioLibrary();
    setAgentSessions((items) => {
      const next = items.filter((item) => item.id !== id);
      persistAgentSessions(next);
      return next;
    });
  }

  function backToAudioLibrary() {
    setWorkspaceMode("library");
    setSelectedUploadAsset(null);
  }

  function sessionForAudio(asset) {
    const path = assetPath(asset);
    if (!path) return null;
    return agentSessions().find((session) => assetPath(session.audio) === path) || sessionFromAudioAsset(asset);
  }

  function audioKind(asset) {
    return sessionForAudio(asset) ? "agent" : "upload";
  }

  function audioAssetUrl(asset) {
    if (asset?.missing_audio || asset?.audio_exists === false) return "";
    const path = assetPath(asset);
    return asset?.audio_src || (path && sessionId() && api?.rawFileUrl ? api.rawFileUrl(sessionId(), path) : "");
  }

  function selectAudioAsset(asset) {
    const session = sessionForAudio(asset);
    if (session) {
      loadAgentSession(session);
      return;
    }
    setWorkspaceMode("upload");
    setSelectedUploadAsset(asset || null);
    setMessages([makeMessage("assistant", "这是 Upload 音频，只能播放和拖拽使用，不进入角色表和 TTS 表。", 0)]);
  }

  async function playAssetAudio(asset) {
    const path = assetPath(asset);
    const src = audioAssetUrl(asset);
    if (!src) {
      showToast("没有可播放的音频路径");
      return;
    }
    if (playingAssetPath() === path) {
      stopAudio();
      return;
    }
    try {
      showToast(`正在播放 ${path.split("/").pop() || "Audio"}`);
      await playGeneratedAudio({ audio_src: src }, path);
    } catch (error) {
      showToast(error instanceof Error ? error.message : String(error));
    }
  }

  async function submitRequest(value = requestText()) {
    const nextText = text(value) || DEFAULT_REQUEST;
    if (workspaceMode() !== "session" || !activeSessionId()) createAgentSession();
    setRequestText(nextText);
    setStarted(true);
    setGenerationError("");
    setAudioState("empty");
    setAudioPlaying(false);
    setAudioResult(null);
    pushMessage("user", nextText);
    let settings = ttsSettings();
    try {
      const config = await ensureTtsConfig();
      settings = selectedTtsSettings(config);
      let draft = null;
      if (api?.assetLibraryTtsAgentDraft && task()?.id) {
        draft = await api.assetLibraryTtsAgentDraft(task().id, {
          user_text: nextText,
          voices: voiceIds(settings),
          provider: settings.provider,
          model: settings.model,
        });
      }
      const nextRoles = draft ? normalizeRoleDrafts(draft, settings, nextText) : rolesFromRequest(nextText, settings);
      const nextDialogues = draft ? generatedDialogueRowsFromDraft(draft, nextRoles, nextText) : generatedDialogueRows(nextText, nextRoles);
      setRoles(nextRoles);
      setDialogues(nextDialogues);
      const sourceText = draft?.source === "opencode" ? "已通过 OpenCode Agent Session 识别" : "已生成";
      const providerText = settings.provider && settings.model ? `当前 TTS provider：${settings.providerLabel || settings.provider} / ${settings.model}。` : "当前还没有可用的 TTS provider。";
      pushMessage("assistant", `${sourceText} ${nextRoles.length} 个角色和 ${nextDialogues.length} 行可编辑对白。${providerText}`);
      pushMessage("assistant", "可以直接修改角色表和 TTS 表；点击“生成声音文件”会把当前 Dialogue 生成一个 Asset Audio 音频素材。");
    } catch (error) {
      const nextRoles = rolesFromRequest(nextText, settings);
      setRoles(nextRoles);
      setDialogues(generatedDialogueRows(nextText, nextRoles));
      const message = error instanceof Error ? error.message : String(error);
      setGenerationError(message);
      pushMessage("assistant", message);
    }
  }

  function openRoleGuide(speakerId) {
    const role = roles().find((item) => item.speaker_id === speakerId) || roles()[0];
    if (!role) return;
    const scenarioId = role.scenario_id || "commercial-short";
    setGuide({
      kind: "role",
      speaker_id: role.speaker_id,
      dialogue_id: "",
      title: "角色提示词配置",
      scenario_id: scenarioId,
      voice: role.voice,
      ...guideSeedForScenario("role", scenarioId, role),
    });
  }

  function openPromptGuide(lineId) {
    const dialogue = dialogues().find((item) => item.line_id === lineId) || dialogues()[0];
    const role = roleForDialogue(roles(), dialogue);
    if (!dialogue) return;
    const scenarioId = dialogue.scenario_id || "local-tone-shift";
    setGuide({
      kind: "prompt",
      speaker_id: role.speaker_id,
      line_id: dialogue.line_id,
      title: "单句配置",
      scenario_id: scenarioId,
      voice: dialogue.voice_id || role.voice,
      ...guideSeedForScenario("prompt", scenarioId, dialogue),
    });
  }

  function closeGuide() {
    setGuide(null);
  }

  function updateGuide(patch) {
    setGuide((current) => current ? { ...current, ...patch } : current);
  }

  function selectGuideScenario(scenarioId) {
    setGuide((current) => current ? {
      ...current,
      scenario_id: scenarioId,
    } : current);
  }

  function guideKeywordSelected(groupTitle, word) {
    const current = guide();
    if (!current) return false;
    return guideScenarioWords(current, current.scenario_id).includes(word);
  }

  function guideSelectedWords(scenarioId = "") {
    const current = guide();
    if (!current) return [];
    return guideScenarioWords(current, scenarioId || current.scenario_id);
  }

  function clearGuideKeywords(scenarioId = "") {
    setGuide((current) => current ? {
      ...current,
      scenarioSelections: {
        ...(current.scenarioSelections || {}),
        [text(scenarioId || current.scenario_id)]: [],
      },
      styleWords: current.kind === "role" && (!scenarioId || scenarioId === current.scenario_id) ? [] : current.styleWords,
      paceWords: current.kind === "role" && (!scenarioId || scenarioId === current.scenario_id) ? [] : current.paceWords,
      keywords: current.kind !== "role" && (!scenarioId || scenarioId === current.scenario_id) ? [] : current.keywords,
    } : current);
  }

  function removeGuideKeyword(word, scenarioId = "") {
    const value = text(word);
    if (!value) return;
    setGuide((current) => {
      if (!current) return current;
      const key = text(scenarioId || current.scenario_id);
      return {
        ...current,
        scenarioSelections: {
          ...(current.scenarioSelections || {}),
          [key]: guideScenarioWords(current, key).filter((item) => item !== value),
        },
        styleWords: key === current.scenario_id ? (current.styleWords || []).filter((item) => item !== value) : current.styleWords,
        paceWords: key === current.scenario_id ? (current.paceWords || []).filter((item) => item !== value) : current.paceWords,
        keywords: key === current.scenario_id ? (current.keywords || []).filter((item) => item !== value) : current.keywords,
      };
    });
  }

  function toggleGuideKeyword(groupTitle, word) {
    const value = text(word);
    if (!value) return;
    setGuide((current) => {
      if (!current) return current;
      const bucket = current.kind === "role" ? keywordGroupBucket(groupTitle) : "keywords";
      const values = guideScenarioWords(current, current.scenario_id);
      const nextValues = values.includes(value) ? values.filter((item) => item !== value) : [...values, value];
      const nextUnique = uniqueWords(nextValues);
      return {
        ...current,
        [bucket]: nextUnique,
        scenarioSelections: {
          ...(current.scenarioSelections || {}),
          [current.scenario_id]: nextUnique,
        },
      };
    });
  }

  function applyGuide() {
    const current = guide();
    if (!current) return;
    const scenarioSelections = normalizeScenarioSelections({ scenarioSelections: current.scenarioSelections || {} });
    if (current.kind === "role") {
      const selectedStyleWords = uniqueWords(flattenScenarioSelections(scenarioSelections).filter((word) => !String(word).startsWith("[")));
      const selectedPaceWords = [];
      const nextPrefix = selectedStyleWords.join("、");
      let nextRoleForSync = null;
      setRoles((items) => items.map((role) => {
        if (role.speaker_id !== current.speaker_id) return role;
        nextRoleForSync = {
          ...role,
          voice: current.voice || role.voice,
          style: selectedStyleWords,
          pace: selectedPaceWords,
          prompt_prefix: nextPrefix,
          scenario_id: current.scenario_id,
          scenario_selections: scenarioSelections,
        };
        return nextRoleForSync;
      }));
      syncRoleVoiceToDialogues(nextRoleForSync);
      pushMessage("assistant", "已套用角色提示词配置。后续真实生成会使用更新后的可见 Prompt。");
      showToast("已套用角色提示词配置");
    } else {
      const selectedKeywords = flattenScenarioSelections(scenarioSelections);
      setDialogues((items) => items.map((dialogue) => dialogue.line_id === current.line_id ? {
        ...(() => {
          const role = roleForDialogue(roles(), dialogue);
          const nextDialogue = {
            ...dialogue,
            voice_id: current.voice || dialogue.voice_id,
            voice_locked: text(current.voice) !== text(role.voice),
            scenario_id: current.scenario_id,
            scenario_keywords: selectedKeywords,
            scenario_selections: scenarioSelections,
            final_prompt: "",
          };
          return {
            ...nextDialogue,
            final_prompt: buildPromptText(nextDialogue, role, selectedKeywords),
          };
        })(),
      } : dialogue));
      pushMessage("assistant", "已套用单句 Prompt 配置。后续真实生成只会影响当前 Dialogue。");
      showToast("已套用单句 Prompt 配置");
    }
    closeGuide();
  }

  function syncRoleVoiceToDialogues(nextRole) {
    const speakerIdValue = text(nextRole?.speaker_id);
    const nextVoice = text(nextRole?.voice);
    if (!speakerIdValue || !nextVoice) return;
    setDialogues((items) => items.map((dialogue) => {
      if (dialogue.speaker_id !== speakerIdValue) return dialogue;
      if (dialogue.voice_locked) return dialogue;
      return { ...dialogue, voice_id: nextVoice };
    }));
  }

  function updateRole(speakerIdValue, patch) {
    let nextRoleForSync = null;
    setRoles((items) => items.map((role, index) => {
      if (role.speaker_id !== speakerIdValue) return role;
      const speaker = patch.speaker !== undefined ? text(patch.speaker) || role.speaker : role.speaker;
      const nextSpeakerId = role.speaker_id || speakerId(speaker, index);
      nextRoleForSync = {
        ...role,
        ...patch,
        speaker,
        speaker_id: nextSpeakerId,
        prompt_prefix: patch.prompt_prefix !== undefined ? text(patch.prompt_prefix) : role.prompt_prefix,
        style: patch.prompt_prefix !== undefined ? wordsFromText(patch.prompt_prefix) : patch.style !== undefined ? wordsFromText(patch.style) : role.style,
        pace: patch.prompt_prefix !== undefined ? [] : patch.pace !== undefined ? wordsFromText(patch.pace) : role.pace,
      };
      return nextRoleForSync;
    }));
    if (patch.voice !== undefined) syncRoleVoiceToDialogues(nextRoleForSync);
  }

  function addRole() {
    const index = roles().length;
    const settings = ttsSettings();
    const speaker = `角色${index + 1}`;
    setRoles((items) => [...items, {
      speaker_id: speakerId(speaker, index),
      speaker,
      voice: voiceForSettings(settings, "", index),
      style: [...DEFAULT_ROLE_STYLE],
      pace: [...DEFAULT_ROLE_PACE],
      prompt_prefix: DEFAULT_ROLE_PREFIX,
      scenario_id: "style-control",
    }]);
  }

  function removeRole(speakerIdValue) {
    setRoles((items) => {
      if (items.length <= 1) return items;
      const next = items.filter((role) => role.speaker_id !== speakerIdValue);
      const fallback = next[0]?.speaker_id || items[0]?.speaker_id || "";
      setDialogues((rows) => rows.map((row) => row.speaker_id === speakerIdValue ? { ...row, speaker_id: fallback } : row));
      return next;
    });
  }

  function updateDialogue(lineId, patch) {
    setDialogues((items) => items.map((item) => {
      if (item.line_id !== lineId) return item;
      const nextPatch = { ...patch };
      if (nextPatch.tempo !== undefined) nextPatch.tempo = clampTempo(nextPatch.tempo);
      if (nextPatch.final_prompt !== undefined && nextPatch.source_text === undefined) {
        nextPatch.source_text = sourceTextFromPromptPrompt(nextPatch.final_prompt, item.source_text);
      }
      if (nextPatch.voice_id !== undefined) {
        const role = roleForDialogue(roles(), item);
        nextPatch.voice_locked = text(nextPatch.voice_id) !== text(role.voice);
      }
      return { ...item, ...nextPatch };
    }));
  }

  function updateDialogueSpeaker(lineId, speakerIdValue) {
    const currentRoles = roles();
    const nextRole = currentRoles.find((role) => role.speaker_id === speakerIdValue) || currentRoles[0];
    setDialogues((items) => items.map((item) => {
      if (item.line_id !== lineId) return item;
      const sourceText = sourceTextFromPromptPrompt(item.final_prompt || "", item.source_text);
      const nextDialogue = {
        ...item,
        speaker_id: nextRole?.speaker_id || speakerIdValue,
        voice_id: nextRole?.voice || item.voice_id,
        voice_locked: false,
        tempo: clampTempo(item.tempo || 1),
        source_text: sourceText,
        final_prompt: "",
      };
      return {
        ...nextDialogue,
        final_prompt: buildPromptText(nextDialogue, nextRole || roleForDialogue(currentRoles, nextDialogue), nextDialogue.scenario_keywords || []),
      };
    }));
  }

  function addDialogue() {
    const index = dialogues().length;
    const role = roles()[index % Math.max(roles().length, 1)] || roles()[0];
    setDialogues((items) => [...items, {
      line_id: `line_${String(index + 1).padStart(3, "0")}_${Date.now()}`,
      dialogue_id: "asset_audio_dialogue_001",
      dialogue_asset_key: "asset_audio_dialogue_001",
      scene_id: "asset_audio_dialogue",
      shot_id: "asset_library_audio",
      scene_label: "Dialogue 01",
      dialogue_label: `Line ${String(index + 1).padStart(2, "0")}`,
      speaker_id: role?.speaker_id || "",
      source_text: "在这里输入新的对白。",
      tempo: 1,
      scenario_id: "",
      scenario_keywords: [],
      final_prompt: "",
    }]);
  }

  function removeDialogue(lineId) {
    setDialogues((items) => items.length <= 1 ? items : items.filter((item) => item.line_id !== lineId));
  }

  function outputPaths(settings, options = {}) {
    const ext = audioExt(settings.provider);
    const stamp = Date.now();
    const base = options.previewOnly ? "tts_agent_preview" : "asset_audio_dialogue_001";
    const dir = options.previewOnly ? "SessionOutput/storyboard/Working" : ASSET_AUDIO_REL;
    const sessionOutput = !options.previewOnly && activeSessionId() ? `${dir}/${safeName(activeSessionId(), "tts_agent_audio")}.${ext}` : "";
    const name = safeName(`${base}_${stamp}`, "tts_agent_audio");
    return {
      output: sessionOutput || `${dir}/${name}.${ext}`,
      manifest: `SessionOutput/storyboard/tts_manifests/${name}.json`,
    };
  }

  function dialogueScript(rows, currentRoles) {
    return rows.map((dialogue) => {
      const role = roleForDialogue(currentRoles, dialogue);
      return `${role.speaker}: ${dialogueOutputText(dialogue, role)}`;
    }).join("\n");
  }

  async function generateTtsAudio(rows, options = {}) {
    if (!started()) await submitRequest();
    const blocked = ttsProviderBlockedReason();
    if (blocked) throw new Error(blocked);
    const currentRows = rows.length ? rows : dialogues();
    if (!currentRows.length) throw new Error("请先生成或添加 TTS 表对白");
    const settings = selectedTtsSettings(await ensureTtsConfig());
    if (!settings.provider || !settings.model || !settings.voiceId || !settings.hasApiKey) {
      throw new Error("请先在 Connection / TTS Model Settings 配置可用 TTS provider、model、voice 和 API Key。");
    }
    const currentRoles = roles();
    const paths = outputPaths(settings, options);
    const configKey = ttsConfigKey(currentRows, currentRoles, settings);
    let completed = null;
    let failed = "";
    setGenerationError("");
    setProgressText(options.previewOnly ? "正在生成临时试听音频..." : `正在逐句生成 ${currentRows.length} 段 TTS，并合并为一个 Asset Audio 音频...`);
    setGeneratingDialogueId(currentRows[0]?.dialogue_id || "");
    if (!options.previewOnly) {
      setAudioState("generating");
    }
    try {
      await api.streamCompareAssetTTS(task().id, {
        workflow_id: `tts_agent_${options.previewOnly ? "preview" : "asset_audio"}_${Date.now()}`,
        shot_id: "asset_library_audio",
        scene_mark_id: "asset_audio_dialogue",
        dialogue_id: "asset_audio_dialogue_001",
        dialogue_asset_key: "asset_audio_dialogue_001",
        asset_library_only: true,
        srt_text: dialogueScript(currentRows, currentRoles),
        use_locked_cache: false,
        locked_output: paths.output,
        locked_manifest: paths.manifest,
        locked_config_key: configKey,
        prompts: currentRows.map((dialogue, index) => {
          const role = roleForDialogue(currentRoles, dialogue);
          const roleIndex = Math.max(currentRoles.findIndex((item) => item.speaker_id === role.speaker_id), 0);
          const voiceId = voiceForSettings(settings, dialogue.voice_id || role.voice, roleIndex);
          return {
            provider: settings.provider,
            model: settings.model,
            voice_id: voiceId,
            prompt: renderedPrompt(dialogue, role),
            text: dialogueOutputText(dialogue, role),
            user_instruction: `TTS Agent ${options.previewOnly ? "临时试听" : "Asset Audio 生成"}。只朗读当前行文本，不改词、不加词。行号 ${index + 1}，角色 ${role.speaker}。`,
            tempo: clampTempo(dialogue.tempo || 1),
          };
        }),
      }, (event) => {
        if (event.type === "requested") setProgressText(`已提交第 ${event.segment_index || 1} 段：${event.provider || settings.provider} / ${event.model || settings.model}`);
        if (event.type === "segment_completed") setProgressText(`已生成第 ${event.segment_index || ""} 段，等待合并...`);
        if (event.type === "heartbeat") setProgressText(`真实生成中... ${event.elapsed_seconds || 0}s`);
        if (event.type === "failed") {
          failed = event.detail || "TTS 生成失败";
          setGenerationError(failed);
        }
        if (event.type === "completed") {
          const output = text(event.output);
          const audioSrc = output && sessionId() && api.rawFileUrl ? `${api.rawFileUrl(sessionId(), output)}?v=${Date.now()}` : "";
          completed = {
            filename: output.split("/").pop() || `asset_audio_dialogue_001.${audioExt(settings.provider)}`,
            path: output,
            audio_src: audioSrc,
            duration_seconds: Number(event.duration_seconds || event.duration || 0),
            provider: event.provider || settings.provider,
            model: event.model || settings.model,
            voice_id: event.voice_id || settings.voiceId,
            source: output.includes("/assets/audios/") ? "Asset Audio" : "TTS-Agent Preview",
            generation_mode: "real_provider",
            dialogue_id: "asset_audio_dialogue_001",
            dialogue_asset_key: "asset_audio_dialogue_001",
          };
        }
      });
    } catch (error) {
      failed = error instanceof Error ? error.message : String(error);
      setGenerationError(failed);
      if (!options.previewOnly) setAudioState("error");
      throw error;
    } finally {
      setGeneratingDialogueId("");
    }
    if (!completed?.path) {
      const message = failed || "TTS provider 没有返回可播放音频";
      setAudioState("error");
      setGenerationError(message);
      pushMessage("assistant", message);
      throw new Error(message);
    }
    if (!options.previewOnly) {
      setAudioResult(completed);
      setAudioState("ready");
      setProgressText(`完成：${completed.filename}`);
      pushMessage("assistant", "声音文件已生成到 Asset Audio，可以直接使用。");
      showToast("声音文件已生成");
      void refreshTaskDetail();
    }
    return completed;
  }

  function stopAudio() {
    if (activeAudio) {
      activeAudio.pause();
      activeAudio = null;
    }
    setAudioPlaying(false);
    setPlayingAssetPath("");
  }

  async function playGeneratedAudio(asset, key = "") {
    const src = asset?.audio_src;
    if (!src) throw new Error("没有可播放的真实音频 URL");
    stopAudio();
    const audio = new Audio(src);
    activeAudio = audio;
    setAudioPlaying(true);
    setPlayingAssetPath(key);
    try {
      await audio.play();
      await new Promise((resolve) => {
        audio.onended = resolve;
        audio.onerror = resolve;
      });
    } finally {
      if (activeAudio === audio) {
        activeAudio = null;
        setAudioPlaying(false);
        setPlayingAssetPath("");
      }
    }
  }

  async function playSinglePrompt(lineId) {
    const blocked = promptPlayDisabledReason();
    if (blocked) {
      showToast(blocked);
      return;
    }
    const item = dialogues().find((dialogue) => dialogue.line_id === lineId);
    if (!item) return;
    try {
      setPlayingDialogueId(lineId);
      const asset = await generateTtsAudio([item], { previewOnly: true });
      showToast("正在播放临时试听");
      await playGeneratedAudio(asset);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setGenerationError(message);
      showToast(message);
    } finally {
      setPlayingDialogueId("");
    }
  }

  async function previewVoice(voiceId, context = {}) {
    const blocked = promptPlayDisabledReason();
    if (blocked) {
      showToast(blocked);
      return;
    }
    const currentRoles = roles();
    const baseDialogue = dialogues().find((dialogue) => dialogue.line_id === context.lineId) || dialogues()[0];
    const contextRole = currentRoles.find((role) => role.speaker_id === context.speakerId)
      || (baseDialogue ? roleForDialogue(currentRoles, baseDialogue) : currentRoles[0]);
    const sampleDialogue = baseDialogue ? {
      ...baseDialogue,
      speaker_id: contextRole?.speaker_id || baseDialogue.speaker_id,
      voice_id: voiceId,
    } : {
      line_id: `voice_preview_${Date.now()}`,
      dialogue_id: "asset_audio_dialogue_001",
      dialogue_asset_key: "asset_audio_dialogue_001",
      scene_id: "asset_audio_dialogue",
      shot_id: "asset_library_audio",
      scene_label: "Dialogue 01",
      dialogue_label: "Voice Preview",
      speaker_id: contextRole?.speaker_id || "speaker_preview",
      voice_id: voiceId,
      source_text: `${contextRole?.speaker || "角色"} 的声音试听。`,
      scenario_id: "",
      scenario_keywords: [],
      final_prompt: "",
    };
    try {
      setPreviewingVoiceId(voiceId);
      const asset = await generateTtsAudio([sampleDialogue], { previewOnly: true });
      showToast(`正在试听 ${voiceId}`);
      await playGeneratedAudio(asset);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setGenerationError(message);
      showToast(message);
    } finally {
      setPreviewingVoiceId("");
    }
  }

  async function generateAudio() {
    const blocked = generateDisabledReason();
    if (blocked) {
      showToast(blocked);
      return;
    }
    try {
      await generateTtsAudio(dialogues());
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setGenerationError(message);
      showToast(message);
    }
  }

  async function playAudio() {
    if (audioPlaying()) {
      stopAudio();
      return;
    }
    if (workspaceMode() === "upload") {
      await playAssetAudio(selectedUploadAsset());
      return;
    }
    const blocked = playAudioDisabledReason();
    if (blocked) {
      showToast(blocked);
      return;
    }
    try {
      showToast(`正在播放 ${audioAsset().filename}`);
      await playGeneratedAudio(audioAsset());
    } catch (error) {
      showToast(error instanceof Error ? error.message : String(error));
    }
  }

  return {
    requestText,
    setRequestText,
    workspaceMode,
    agentSessions,
    activeSession,
    selectedUploadAsset,
    createAgentSession,
    loadAgentSession,
    backToAudioLibrary,
    sessionForAudio,
    audioKind,
    audioAssetUrl,
    selectAudioAsset,
    playAssetAudio,
    removeAgentSession,
    started,
    roles,
    promptItems,
    messages,
    guide,
    updateGuide,
    selectGuideScenario,
    guideKeywordSelected,
    guideSelectedWords,
    clearGuideKeywords,
    removeGuideKeyword,
    toggleGuideKeyword,
    closeGuide,
    applyGuide,
    scenarioGuides: () => GOOGLE_TTS_SCENARIO_GUIDES,
    playingDialogueId,
    generatingDialogueId,
    previewingVoiceId,
    audioState,
    audioPlaying,
    playingAssetPath,
    audioAsset,
    toast,
    ttsSettings,
    generateDisabledReason,
    promptPlayDisabledReason,
    playAudioDisabledReason,
    generationError,
    progressText,
    submitRequest,
    openRoleGuide,
    openPromptGuide,
    updateRole,
    addRole,
    removeRole,
    updateDialogue,
    updateDialogueSpeaker,
    addDialogue,
    removeDialogue,
    playSinglePrompt,
    previewVoice,
    generateAudio,
    playAudio,
    voices: () => voiceIds(ttsSettings()),
    voiceOptions: () => voiceOptionItems(ttsSettings()),
  };
}
