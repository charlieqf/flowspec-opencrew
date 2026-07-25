import { For, Show, createEffect, createMemo, createSignal, onCleanup } from "solid-js";
import {
  extractAgentCandidates,
  messageId,
  messageRole,
  reduceAgentChatEvent,
  seedAgentChatState,
} from "../../KouboStoryBoard/kouboAgentChat.js";
import { assetKind, shapeFromAspect } from "../uploadAssetLibraryModel.js";
import {
  isStatefulVideoCapability,
  referencesToVideoSlots,
  resolveVideoModelCapability,
  videoAgentModelSupportsText,
  validateVideoGenerationInputs,
} from "../videoModelCapabilities.js";
import FlowIcon from "./FlowIcon.jsx";
import PromptBuilderModal from "./PromptBuilderModal.jsx";

const DEFAULT_SETTINGS = {
  aspect: "9:16",
  duration: 4,
  count: 1,
  confirmBeforeGenerate: true,
  referenceMode: "selected_images",
  agentVideoAlias: "",
  provider: "",
  model: "",
  chatProvider: "",
  chatModel: "",
};
const AGENT_VIDEO_PROGRESS_MAX = 98;
const VIDEO_WORKSPACE_HISTORY_LIMIT = 500;
const REFERENCE_FILE_ACCEPTS = {
  all: "image/*,video/mp4,video/webm,video/quicktime,audio/*",
  images: "image/*,.avif,.gif,.heic,.jpg,.jpeg,.png,.svg,.webp",
  videos: "video/mp4,video/webm,video/quicktime,.m4v,.mov,.mp4,.webm",
  audios: "audio/*,.aac,.aif,.aiff,.caf,.flac,.m4a,.mp3,.oga,.ogg,.opus,.wav,.weba,.wma",
};

function scheduleIdle(callback, timeout = 1400) {
  if (typeof window === "undefined") return () => {};
  let idleHandle = 0;
  const timer = window.setTimeout(() => {
    if (typeof window.requestIdleCallback === "function") {
      idleHandle = window.requestIdleCallback(callback, { timeout });
      return;
    }
    callback();
  }, timeout);
  return () => {
    window.clearTimeout(timer);
    if (idleHandle) window.cancelIdleCallback?.(idleHandle);
  };
}

function text(value) {
  return String(value || "").trim();
}

function newClientActionId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (token) => {
    const value = Math.floor(Math.random() * 16);
    return (token === "x" ? value : ((value & 0x3) | 0x8)).toString(16);
  });
}

function modelKey(item = {}) {
  return `${text(item.providerID)}::${text(item.modelID)}`;
}

function parseModelKey(value) {
  const [providerID = "", modelID = ""] = String(value || "").split("::");
  return { providerID, modelID };
}

function modelLabel(item = {}) {
  const provider = text(item.providerName || item.providerID);
  const model = text(item.modelName || item.modelID);
  if (!provider) return model;
  if (!model || provider.toLowerCase() === model.toLowerCase()) return provider;
  return `${provider} / ${model}`;
}

function videoModelLabel(item = {}) {
  return text(item.alias || item.label || item.model || item.provider) || "Model";
}

function videoModelKey(item = {}) {
  return `${text(item.alias)}::${text(item.provider)}::${text(item.model)}`;
}

function videoModelSelectionPayload(item = {}) {
  const alias = text(item.alias || item.agentVideoAlias || item.agent_video_alias);
  return {
    agentVideoAlias: alias,
    provider: alias ? "" : text(item.provider),
    model: alias ? "" : text(item.model),
  };
}

function videoAgentModelsFor(config = {}) {
  return Array.isArray(config?.agent_model_aliases) ? config.agent_model_aliases : [];
}

function videoReferencePayloadItem(asset) {
  const path = text(asset?.path || asset?.id);
  const kind = assetKind(asset);
  return {
    path,
    label: text(asset?.label || asset?.filename || shortPathLabel(path)),
    role: normalizeReferenceRole(asset?.role || asset?.reference_role) || "",
    reference_role: normalizeReferenceRole(asset?.reference_role || asset?.role) || "",
    key: text(asset?.key),
    kind,
    source: text(asset?.source),
  };
}

function splitVideoReferencePayload(items = []) {
  const unique = mergeAssetsByPath(items, []).map(videoReferencePayloadItem).filter((item) => item.path);
  return {
    referenceAssets: unique,
    referenceImages: unique.filter((item) => item.kind === "image"),
    referenceAudios: unique.filter((item) => item.kind === "audio"),
    referenceVideos: unique.filter((item) => item.kind === "video"),
  };
}

function normalizeReferenceRole(value) {
  const role = text(value).toUpperCase().replace(/[-\s]+/g, "_");
  const aliases = {
    TARGET: "TARGET_FRAME",
    BASE: "TARGET_FRAME",
    FRAME: "TARGET_FRAME",
    HOST: "HOST_REFERENCE",
    PERSON: "HOST_REFERENCE",
    CHARACTER: "HOST_REFERENCE",
    PRODUCT: "PRODUCT_REFERENCE",
  };
  const next = aliases[role] || role;
  return ["TARGET_FRAME", "HOST_REFERENCE", "PRODUCT_REFERENCE", "REFERENCE_IMAGE"].includes(next) ? next : "";
}

function referenceRoleLabel(role) {
  if (role === "TARGET_FRAME") return "目标帧";
  if (role === "HOST_REFERENCE") return "人物";
  if (role === "PRODUCT_REFERENCE") return "产品";
  return "参考";
}

function messageTime(message) {
  const time = message?.info?.time || message?.time || {};
  return Number(time.created || time.completed || message?.created_at || 0) || 0;
}

function messageParts(message, partsByMessageId = {}) {
  const id = messageId(message);
  const stored = id ? Object.values(partsByMessageId[id] || {}) : [];
  const parts = stored.length ? stored : (message?.parts || []);
  return parts.slice().sort((a, b) => String(a.id || "").localeCompare(String(b.id || "")));
}

function rawMessageText(message, partsByMessageId = {}) {
  if (message?.text) return String(message.text);
  return messageParts(message, partsByMessageId)
    .filter((part) => String(part?.type || "text") === "text")
    .map((part) => String(part?.text || ""))
    .join("");
}

function shortPathLabel(value) {
  const clean = String(value || "").trim().replace(/^["']|["']$/g, "");
  return clean.split(/[\\/]/).filter(Boolean).pop() || clean;
}

function normalizeTextWhitespace(value) {
  return String(value || "")
    .split("\n")
    .map((line) => line.trimEnd())
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function messageCompletedAt(message) {
  const timeInfo = message?.info?.time || message?.time || {};
  return Number(timeInfo.completed || message?.completed_at || 0) || 0;
}

function hasActiveVideoPlaceholder(messages = []) {
  return messages.some((message) => (
    messageRole(message) !== "user"
    && message.videoPlaceholder
    && !message.videoUrl
    && !message.failed
    && !messageCompletedAt(message)
  ));
}

function isPendingAssistantTextMessage(message) {
  return (
    messageRole(message) !== "user"
    && !message.videoPlaceholder
    && !message.videoUrl
    && !message.failed
    && !messageCompletedAt(message)
  );
}

function isVideoGenerationTextFragment(message, partsByMessageId = {}) {
  if (messageRole(message) === "user" || message.videoPlaceholder || message.videoUrl || message.failed) return false;
  const body = normalizeTextWhitespace(rawMessageText(message, partsByMessageId));
  if (!body || body.length > 80) return false;
  return (
    /^(\d+\s*)?Lite\.?$/i.test(body)
    || /^[\d.\s]+(?:Lite|Fast|X)\.?$/i.test(body)
    || /^(?:Max|Flash|Veo|Grok|Gemini)(?:\s*[A-Z]{0,3}[\d.]+)?(?:\s*(?:Lite|Fast|X))?\.?$/i.test(body)
  );
}

function suppressVideoGenerationTextFragments(messages = [], partsByMessageId = {}) {
  if (!hasActiveVideoPlaceholder(messages)) return messages;
  return messages.filter((message) => {
    if (isVideoGenerationTextFragment(message, partsByMessageId)) return false;
    if (!isPendingAssistantTextMessage(message)) return true;
    const body = normalizeTextWhitespace(rawMessageText(message, partsByMessageId));
    return body.length > 120;
  });
}

function firstIndexOfAny(source, patterns) {
  let index = -1;
  for (const pattern of patterns) {
    const match = pattern.exec(source);
    pattern.lastIndex = 0;
    if (match && (index === -1 || match.index < index)) index = match.index;
  }
  return index;
}

function extractReferenceItemsFromText(source) {
  const lines = String(source || "").split("\n");
  const items = [];
  for (let index = 0; index < lines.length; index += 1) {
    const match = lines[index].match(/^\s*-\s*(TARGET_FRAME|HOST_REFERENCE|PRODUCT_REFERENCE|REFERENCE_IMAGE)\s*:\s*(.*)$/i);
    if (!match) continue;
    let path = String(match[2] || "").trim();
    if (!path && lines[index + 1] && !/^\s*-/.test(lines[index + 1])) path = lines[index + 1].trim();
    path = path.replace(/\s*\([^)]*\)\s*$/g, "").trim();
    if (!path) continue;
    items.push({ role: normalizeReferenceRole(match[1]), path, label: shortPathLabel(path) });
  }
  return items;
}

function removeReferenceListText(source) {
  const lines = String(source || "").split("\n");
  const kept = [];
  let skipping = false;
  for (const line of lines) {
    const trimmed = line.trim();
    if (/^(Selected reference images|Role-bound visual references)\s*:/i.test(trimmed)) {
      skipping = true;
      continue;
    }
    if (skipping) {
      if (
        !trimmed
        || /^[-*]\s+/.test(trimmed)
        || /^\d+\.\s+/.test(trimmed)
        || /^(TARGET_FRAME|HOST_REFERENCE|PRODUCT_REFERENCE|REFERENCE_IMAGE)\s*:/i.test(trimmed)
      ) {
        continue;
      }
      skipping = false;
    }
    if (/^\s*-\s*(TARGET_FRAME|HOST_REFERENCE|PRODUCT_REFERENCE|REFERENCE_IMAGE)\s*:\s*.*$/i.test(line)) continue;
    kept.push(line);
  }
  return kept.join("\n");
}

function parseThinkingBlock(source) {
  const thinkingBlocks = [];
  let value = String(source || "").replace(/<THINKING>([\s\S]*?)<\/THINKING>/g, (_match, body) => {
    thinkingBlocks.push(normalizeTextWhitespace(body));
    return "";
  });
  const headingPattern = /(^|\n)(Planning Video Generation|Reference Interpretation|Video Prompt Planning|Generating Video|Model Selection|Checking References)\b/i;
  const headingIndex = value.search(headingPattern);
  const headingMatches = value.match(/(^|\n)(Planning Video Generation|Reference Interpretation|Video Prompt Planning|Generating Video|Model Selection|Checking References)\b/gi) || [];
  if (headingIndex >= 0 && headingMatches.length >= 2) {
    thinkingBlocks.push(normalizeTextWhitespace(value.slice(headingIndex)));
    value = value.slice(0, headingIndex);
  }
  return { text: normalizeTextWhitespace(value), thinking: thinkingBlocks.filter(Boolean).join("\n\n") };
}

function videoGenerationRequestSummary(body) {
  try {
    const payload = JSON.parse(String(body || "").trim());
    const title = text(payload.title);
    const prompt = text(payload.prompt || payload.positive_prompt);
    if (title) return `已提交视频生成请求：${title}`;
    if (prompt) return `已提交视频生成请求：${prompt.slice(0, 80)}${prompt.length > 80 ? "..." : ""}`;
  } catch {
    // Keep malformed/incomplete streaming request blocks out of the main bubble.
  }
  return "已提交视频生成请求。";
}

function parseVideoAgentDisplay(message, partsByMessageId = {}) {
  const role = messageRole(message);
  const raw = rawMessageText(message, partsByMessageId);
  if (role === "user") {
    const references = message.referenceAttachments?.length ? message.referenceAttachments : extractReferenceItemsFromText(raw);
    return { text: normalizeTextWhitespace(removeReferenceListText(raw)), thinking: "", debug: "", references };
  }
  const debugBlocks = [];
  const extractedReferences = extractReferenceItemsFromText(raw);
  let generationRequestSummary = "";
  let source = raw.replace(/<VIDEO_GENERATION_REQUEST>([\s\S]*?)<\/VIDEO_GENERATION_REQUEST>/gi, (_match, body) => {
    generationRequestSummary ||= videoGenerationRequestSummary(body);
    return "";
  });
  source = source.replace(/<([A-Z_]*(?:CANDIDATE|ACTION|ADVICE|DIAGNOSIS))>([\s\S]*?)<\/\1>/gi, (_match, tag, body) => {
    debugBlocks.push(`${tag}\n${normalizeTextWhitespace(body)}`);
    return "";
  });
  source = source.replace(/```(?:json)?\s*(\{[\s\S]*?(?:"reference_images"|"reference_videos"|"reference_audios"|"duration"|"aspect"|"prompt")[\s\S]*?\})\s*```/gi, (_match, body) => {
    debugBlocks.push(normalizeTextWhitespace(body));
    return "";
  });
  const thinkingResult = parseThinkingBlock(source);
  source = thinkingResult.text;
  const internalIndex = firstIndexOfAny(source, [
    /The user explicitly requested/i,
    /Produce exactly one/i,
    /Return only/i,
    /Do not include any other text/i,
    /Do not call tools/i,
    /Use these selected reference_images/i,
    /reference_images\s*:/i,
    /reference_audios\s*:/i,
    /reference_videos\s*:/i,
    /If TARGET_FRAME,\s*HOST_REFERENCE/i,
    /role\/path objects/i,
    /raw OpenCode protocol text/i,
    /<VIDEO_GENERATION_REQUEST>/i,
  ]);
  if (internalIndex >= 0) {
    debugBlocks.push(normalizeTextWhitespace(source.slice(internalIndex)));
    source = source.slice(0, internalIndex);
  }
  source = removeReferenceListText(source);
  source = source.replace(/^\s*(Request payload|Generation payload|Debug payload|Internal request)\s*:\s*[\s\S]*$/gim, (match) => {
    debugBlocks.push(normalizeTextWhitespace(match));
    return "";
  });
  source = source.replace(/^\s*\{[\s\S]*?(?:"reference_images"|"reference_videos"|"reference_audios"|"duration"|"aspect"|"prompt")[\s\S]*?\}\s*$/gm, (match) => {
    debugBlocks.push(normalizeTextWhitespace(match));
    return "";
  });
  source = source.replace(/已经生成并保存到\s*Videos\s*[:：]\s*.+/gi, "");
  source = source.replace(/Agent is generating[^.。]*[.。]?/gi, "");
  source = source.replace(/Generating\s+\d+:\d+\s+video\s+via\s+[^.。]*[.。]?/gi, "");
  source = source.replace(/^\s*Generating\s*$/gi, "");
  return {
    text: normalizeTextWhitespace(source) || generationRequestSummary,
    thinking: thinkingResult.thinking,
    debug: debugBlocks.filter(Boolean).join("\n\n"),
    references: extractedReferences,
  };
}

function normalizeVideoRequestReferences(values = []) {
  return (Array.isArray(values) ? values : [])
    .map((item) => {
      if (typeof item === "string") return { path: text(item) };
      if (!item || typeof item !== "object") return null;
      const path = text(item.path || item.id);
      if (!path) return null;
      return {
        ...item,
        path,
        id: text(item.id || path),
        label: text(item.label || item.filename || shortPathLabel(path)),
        kind: text(item.kind || item.asset_type),
      };
    })
    .filter(Boolean);
}

function extractVideoGenerationRequests(message, partsByMessageId = {}) {
  const source = rawMessageText(message, partsByMessageId);
  const requests = [];
  const pattern = /<VIDEO_GENERATION_REQUEST>([\s\S]*?)<\/VIDEO_GENERATION_REQUEST>/gi;
  let match;
  while ((match = pattern.exec(source))) {
    try {
      const payload = JSON.parse(String(match[1] || "").trim());
      const prompt = text(payload.prompt || payload.positive_prompt);
      if (!prompt) continue;
      requests.push({
        title: text(payload.title, "智能体生成视频"),
        prompt,
        duration: Math.max(1, Math.min(Number(payload.duration ?? payload.duration_seconds) || 4, 30)),
        aspect: ["9:16", "16:9"].includes(text(payload.aspect)) ? text(payload.aspect) : "9:16",
        referenceImages: normalizeVideoRequestReferences(payload.reference_images),
        referenceAudios: normalizeVideoRequestReferences(payload.reference_audios),
        referenceVideos: normalizeVideoRequestReferences(payload.reference_videos),
        referenceMode: text(payload.reference_mode || payload.referenceMode),
        provider: text(payload.provider),
        model: text(payload.model),
        notes: text(payload.notes),
      });
    } catch {
      // Ignore incomplete streaming request blocks until the assistant finishes them.
    }
  }
  return requests.slice(0, 1);
}

function stableVideoRequestKey(request = {}) {
  return JSON.stringify({
    title: text(request.title),
    prompt: text(request.prompt),
    duration: Number(request.duration) || 4,
    aspect: text(request.aspect, "9:16"),
    referenceImages: (request.referenceImages || []).map((item) => text(item.path || item.id)),
    referenceAudios: (request.referenceAudios || []).map((item) => text(item.path || item.id)),
    referenceVideos: (request.referenceVideos || []).map((item) => text(item.path || item.id)),
    provider: text(request.provider),
    model: text(request.model),
  });
}

function formatDebugText(value) {
  const source = String(value || "").trim();
  if (!source) return "";
  try {
    return JSON.stringify(JSON.parse(source), null, 2);
  } catch {
    // Continue with block-wise formatting for mixed debug content.
  }
  return source
    .split(/\n{2,}/)
    .map((block) => {
      const body = block.trim();
      if (!body) return "";
      try {
        return JSON.stringify(JSON.parse(body), null, 2);
      } catch {
        return body;
      }
    })
    .filter(Boolean)
    .join("\n\n");
}

function richTextBlocks(value) {
  const lines = String(value || "").split("\n");
  const blocks = [];
  let textLines = [];
  let codeLines = [];
  let inCode = false;
  let language = "";
  const flushText = () => {
    const textBlock = normalizeTextWhitespace(textLines.join("\n"));
    if (textBlock) {
      textBlock.split(/\n\s*\n/).forEach((paragraph) => {
        const next = normalizeTextWhitespace(paragraph);
        if (next) blocks.push({ type: "paragraph", text: next });
      });
    }
    textLines = [];
  };
  const flushCode = () => {
    blocks.push({ type: "code", language, text: codeLines.join("\n").trim() });
    codeLines = [];
    language = "";
  };
  for (const line of lines) {
    const fence = line.match(/^\s*```(\w+)?\s*$/);
    if (fence) {
      if (inCode) {
        flushCode();
        inCode = false;
      } else {
        flushText();
        inCode = true;
        language = fence[1] || "";
      }
      continue;
    }
    if (inCode) codeLines.push(line);
    else textLines.push(line);
  }
  if (inCode) flushCode();
  flushText();
  return blocks;
}

function shouldCollapseUserText(value) {
  const source = String(value || "");
  return source.length > 900 || source.split("\n").length > 8;
}

function mergeAssetsByPath(primary = [], secondary = []) {
  const seen = new Set();
  return [...primary, ...secondary].filter((asset) => {
    const key = text(asset?.path || asset?.id);
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function loadStoredSettings(taskId) {
  try {
    const raw = window.localStorage?.getItem(`koubo-storyboard:asset-library-video-settings:${taskId}`);
    return raw ? { ...DEFAULT_SETTINGS, ...JSON.parse(raw) } : DEFAULT_SETTINGS;
  } catch {
    return DEFAULT_SETTINGS;
  }
}

function storeSettings(taskId, settings) {
  try {
    window.localStorage?.setItem(`koubo-storyboard:asset-library-video-settings:${taskId}`, JSON.stringify(settings));
  } catch {
    // Local settings are a UI convenience; chat payload still carries the current values.
  }
}

export default function VideoAgentPanel(props) {
  let messagesScrollEl;
  let messagesBottomEl;
  let composerTextarea;
  let referenceFileInput;
  let scrollFrame = 0;
  let historyFallbackTimer = null;
  let directVideoAbortController = null;
  let streamDeltaTimer = 0;
  let streamDeltaBuffer = [];
  let directHistorySaveTimer = 0;
  let directHistorySaveSnapshot = null;
  const [draft, setDraft] = createSignal("");
  let lastAppliedComposerNonce = 0;
  createEffect(() => {
    const apply = props.applyComposerPrompt?.();
    if (!apply || !apply.nonce || apply.nonce === lastAppliedComposerNonce) return;
    if (!props.applyComposerTarget || apply.target !== props.applyComposerTarget) return;
    lastAppliedComposerNonce = apply.nonce;
    if (apply.prompt) setDraft(apply.prompt);
    props.onComposerPromptApplied?.(apply.nonce);
  });
  const [chatSessionId, setChatSessionId] = createSignal("");
  const [promptModels, setPromptModels] = createSignal({ items: [], default_model: { providerID: "", modelID: "" } });
  const [selectedModelKey, setSelectedModelKey] = createSignal("");
  const [error, setError] = createSignal("");
  const [settingsOpen, setSettingsOpen] = createSignal(false);
  const [settingsSaving, setSettingsSaving] = createSignal(false);
  const [settingsPanelError, setSettingsPanelError] = createSignal("");
  const [settings, setSettings] = createSignal(DEFAULT_SETTINGS);
  const [settingsLoadedFor, setSettingsLoadedFor] = createSignal("");
  const [remoteSettingsLoadedFor, setRemoteSettingsLoadedFor] = createSignal("");
  const [videoModelConfig, setVideoModelConfig] = createSignal(null);
  const [videoModelConfigLoadedFor, setVideoModelConfigLoadedFor] = createSignal("");
  const [state, setState] = createSignal({ messages: [], partsByMessageId: {}, busy: false, error: "" });
  const [initializedFor, setInitializedFor] = createSignal("");
  const [directHistoryLoadedFor, setDirectHistoryLoadedFor] = createSignal("");
  const [directHistoryRequested, setDirectHistoryRequested] = createSignal(!props.deferHistoryLoad);
  const [directHistoryReady, setDirectHistoryReady] = createSignal(false);
  const [localReferences, setLocalReferences] = createSignal([]);
  const [referenceFileAccept, setReferenceFileAccept] = createSignal(REFERENCE_FILE_ACCEPTS.all);
  const [referenceDragging, setReferenceDragging] = createSignal(false);
  const [consistencyPickerOpen, setConsistencyPickerOpen] = createSignal(false);
  const [consistencyLoading, setConsistencyLoading] = createSignal(false);
  const [consistencyReferences, setConsistencyReferences] = createSignal([]);
  const [selectedConsistencyKeys, setSelectedConsistencyKeys] = createSignal(new Set());
  const [promptBuilderOpen, setPromptBuilderOpen] = createSignal(false);
  const [promptBuilderLoading, setPromptBuilderLoading] = createSignal(false);
  const [promptBuilderPayload, setPromptBuilderPayload] = createSignal({});
  const [promptBuilderError, setPromptBuilderError] = createSignal("");
  const [lastAppliedPromptBuilder, setLastAppliedPromptBuilder] = createSignal(null);
  const [expandedThinkingByMessageId, setExpandedThinkingByMessageId] = createSignal({});
  const [resultActionState, setResultActionState] = createSignal({});
  const [copiedResultId, setCopiedResultId] = createSignal("");
  const [pendingDirectVideoGeneration, setPendingDirectVideoGeneration] = createSignal(null);
  const [pendingAgentVideoGeneration, setPendingAgentVideoGeneration] = createSignal(null);
  const [resultVideoAspectById, setResultVideoAspectById] = createSignal({});
  const [videoInteraction, setVideoInteraction] = createSignal({ video_thread_id: "", head_turn_id: null, status: "empty", turns: [] });
  const [videoInteractionLoading, setVideoInteractionLoading] = createSignal(false);
  const [videoInteractionDeleteBusy, setVideoInteractionDeleteBusy] = createSignal(false);
  const [statefulOperation, setStatefulOperation] = createSignal("generate");
  const [selectedParentTurnId, setSelectedParentTurnId] = createSignal("");
  const [initialLoadReady, setInitialLoadReady] = createSignal(!props.deferInitialLoad);
  let activeAgentVideoMessageId = "";
  let agentVideoProgressTimer = null;
  let agentVideoProgressStartedAt = 0;
  let pendingUserReferenceAttachments = [];
  let lastAgentVideoSendContext = null;
  const handledAgentVideoEventKeys = new Set();
  const announcedAgentVideoPendingIds = new Set();
  const fallbackVideoRequestKeys = new Set();

  const taskId = () => Number(props.task?.()?.id || 0);
  const isAgent = () => props.variant === "agent";
  const busy = () => Boolean(state().busy);
  const backendOwnsAgentVideoGeneration = () => isAgent() && text(props.agentKey) === "asset_video";
  const referenceAssets = createMemo(() => mergeAssetsByPath(Array.isArray(props.referenceAssets?.()) ? props.referenceAssets() : [], localReferences()));
  const videoCapability = createMemo(() => resolveVideoModelCapability(settings(), videoModelConfig() || {}, { isAgent: isAgent() }));
  const statefulVideoEnabled = createMemo(() => isStatefulVideoCapability(videoCapability()));
  const videoInteractionTurns = createMemo(() => Array.isArray(videoInteraction()?.turns) ? videoInteraction().turns : []);
  const currentVideoTurn = createMemo(() => {
    const requested = selectedParentTurnId() || text(videoInteraction()?.head_turn_id);
    return videoInteractionTurns().find((turn) => text(turn.video_turn_id) === requested) || null;
  });
  const referenceSlots = createMemo(() => referencesToVideoSlots(referenceAssets()));
  const referenceValidation = createMemo(() => validateVideoGenerationInputs(videoCapability(), referenceSlots()));
  const referenceSlotGroups = createMemo(() => {
    const capability = videoCapability();
    const slots = referenceSlots();
    const groups = [
      { key: "videos", icon: "video", title: "Video", limit: capability.references.videos, items: slots.videos },
      { key: "images", icon: "image", title: "Image", limit: capability.references.images, items: slots.images },
      { key: "audios", icon: "audio", title: "Audio", limit: capability.references.audios, items: slots.audios },
    ].filter((group) => group.limit.max > 0 || group.items.length);
    if (capability.references.videos.min <= 0) {
      groups.sort((a, b) => ({ images: 0, audios: 1, videos: 2 }[a.key] - { images: 0, audios: 1, videos: 2 }[b.key]));
    }
    return groups.map((group) => ({
      ...group,
      remaining: Math.max(0, group.limit.max - group.items.length),
      required: group.limit.min > 0,
    }));
  });
  const selectedConsistencyCount = createMemo(() => selectedConsistencyKeys().size);
  const modelItems = createMemo(() => Array.isArray(promptModels().items) ? promptModels().items : []);
  const selectedModel = createMemo(() => parseModelKey(selectedModelKey()));
  const selectedSettingsModelKey = createMemo(() => {
    const current = settings();
    const settingsKey = current.chatProvider && current.chatModel
      ? modelKey({ providerID: current.chatProvider, modelID: current.chatModel })
      : "";
    return settingsKey || selectedModelKey();
  });
  const videoAgentModels = createMemo(() => {
    return videoAgentModelsFor(videoModelConfig() || {});
  });
  const selectedAgentVideoModel = (config = videoModelConfig(), source = settings()) => {
    const aliases = videoAgentModelsFor(config || {});
    const alias = text(source.agentVideoAlias || source.agent_video_alias);
    const provider = text(source.provider);
    const model = text(source.model);
    if (alias) return aliases.find((item) => text(item.alias) === alias) || null;
    if (provider && model) {
      return aliases.find((item) => text(item.provider) === provider && text(item.model) === model) || null;
    }
    return null;
  };
  const selectedOrDefaultAgentVideoModel = (config = videoModelConfig(), source = settings()) => {
    const aliases = videoAgentModelsFor(config || {});
    return selectedAgentVideoModel(config, source)
      || aliases.find((item) => videoAgentModelSupportsText(config, item))
      || aliases[0]
      || null;
  };
  const selectedVideoModelKey = createMemo(() => {
    const selected = selectedAgentVideoModel();
    return selected ? videoModelKey(selected) : "";
  });
  const chips = createMemo(() => Array.isArray(props.contextChips?.()) ? props.contextChips() : []);
  const messages = createMemo(() => {
    const items = suppressVideoGenerationTextFragments(state().messages || [], state().partsByMessageId);
    if (items.length) {
      return items.filter((message) => (
        messageRole(message) === "user"
        || parseVideoAgentDisplay(message, state().partsByMessageId).text
        || parseVideoAgentDisplay(message, state().partsByMessageId).thinking
        || parseVideoAgentDisplay(message, state().partsByMessageId).debug
        || message.videoPlaceholder
        || message.videoUrl
      ));
    }
    return [{
      id: "local-greeting",
      role: "assistant",
      text: isAgent()
        ? "你好，我可以帮你分析当前视频工作区，也可以把明确的视频生成需求转成受控生成任务。"
        : "选择下方图片作为参考图，然后描述你想生成的视频。",
    }];
  });

  createEffect(() => {
    const id = taskId();
    if (!props.deferInitialLoad) {
      setInitialLoadReady(true);
      return;
    }
    setInitialLoadReady(false);
    if (!id) return;
    const cancel = scheduleIdle(() => setInitialLoadReady(true), Number(props.deferInitialLoadTimeoutMs) || 1400);
    onCleanup(cancel);
  });

  createEffect(() => {
    const id = taskId();
    const marker = `${id}:${props.variant || "workspace"}`;
    if (!id || settingsLoadedFor() === marker) return;
    setSettingsLoadedFor(marker);
    setSettings(loadStoredSettings(id));
  });

  async function loadRemoteSettings(force = false) {
    const id = taskId();
    const marker = `${id}:${props.variant || "workspace"}`;
    if (!id || (!force && remoteSettingsLoadedFor() === marker)) return;
    setRemoteSettingsLoadedFor(marker);
    const loader = isAgent() ? props.loadVideosAgentSettings : props.loadVideoAPISettings;
    if (!loader) return;
    try {
      const payload = await loader();
      if (payload?.settings) {
        setSettings((current) => ({ ...current, ...payload.settings }));
        if (payload.settings.chatProvider && payload.settings.chatModel) {
          setSelectedModelKey(modelKey({ providerID: payload.settings.chatProvider, modelID: payload.settings.chatModel }));
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  createEffect(() => {
    if (!initialLoadReady()) return;
    void loadRemoteSettings(false);
  });

  createEffect(() => {
    const id = taskId();
    if (id) storeSettings(id, settings());
  });

  async function refreshVideoModelConfig(force = false) {
    const id = taskId();
    if (!id || !props.videoModelConfig) return videoModelConfig();
    const marker = String(id);
    if (!force && videoModelConfigLoadedFor() === marker) return videoModelConfig();
    setVideoModelConfigLoadedFor(marker);
    try {
      const config = await props.videoModelConfig();
      setVideoModelConfig(config);
      normalizeSettingsForVideoAgentPool(config);
      return config;
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      return videoModelConfig();
    }
  }

  createEffect(() => {
    if (!initialLoadReady()) return;
    void refreshVideoModelConfig(false);
  });

  async function refreshVideoInteraction() {
    const id = taskId();
    if (!id || !statefulVideoEnabled() || (isAgent() && !chatSessionId()) || !props.api?.assetLibraryCurrentVideoInteraction) {
      setVideoInteraction({ video_thread_id: "", head_turn_id: null, status: "empty", turns: [] });
      return null;
    }
    setVideoInteractionLoading(true);
    try {
      const current = await props.api.assetLibraryCurrentVideoInteraction(id, isAgent() ? chatSessionId() : "");
      const normalized = {
        video_thread_id: text(current?.video_thread_id),
        head_turn_id: current?.head_turn_id || null,
        status: text(current?.status || "empty"),
        turns: Array.isArray(current?.turns) ? current.turns : [],
      };
      setVideoInteraction(normalized);
      if (isAgent() && normalized.head_turn_id) {
        setStatefulOperation("continue");
        setSelectedParentTurnId(text(normalized.head_turn_id));
      }
      return normalized;
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      return null;
    } finally {
      setVideoInteractionLoading(false);
    }
  }

  createEffect(() => {
    const id = taskId();
    const enabled = statefulVideoEnabled();
    const chat = isAgent() ? chatSessionId() : "";
    if (!id || !initialLoadReady()) return;
    if (!enabled) {
      setVideoInteraction({ video_thread_id: "", head_turn_id: null, status: "empty", turns: [] });
      setStatefulOperation("generate");
      setSelectedParentTurnId("");
      return;
    }
    void chat;
    void refreshVideoInteraction();
  });

  createEffect(() => {
    if (typeof window === "undefined") return;
    const handleVersionSelection = async (event) => {
      const asset = event?.detail?.asset || {};
      const threadId = text(asset.video_thread_id);
      const turnId = text(asset.video_turn_id);
      if (!statefulVideoEnabled() || !threadId || !turnId || !props.api?.assetLibraryVideoInteraction) return;
      try {
        const thread = await props.api.assetLibraryVideoInteraction(taskId(), threadId);
        setVideoInteraction(thread);
        setStatefulOperation("continue");
        setSelectedParentTurnId(turnId);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    };
    window.addEventListener("koubo-storyboard:continue-video-version", handleVersionSelection);
    onCleanup(() => window.removeEventListener("koubo-storyboard:continue-video-version", handleVersionSelection));
  });

  function chooseStatefulOperation(operation, turnId = "") {
    if (!statefulVideoEnabled() || busy()) return;
    const next = ["generate", "edit", "continue"].includes(operation) ? operation : "generate";
    setStatefulOperation(next);
    setSelectedParentTurnId(next === "continue" ? text(turnId || videoInteraction()?.head_turn_id) : "");
  }

  function statefulGenerationPayload(clientActionId) {
    if (!statefulVideoEnabled()) return {};
    const operation = statefulOperation();
    const threadId = text(videoInteraction()?.video_thread_id);
    const parentTurnId = text(selectedParentTurnId() || videoInteraction()?.head_turn_id);
    if (operation === "continue" && (!threadId || !parentTurnId)) {
      throw new Error("没有可继续的成功视频版本，请先新建视频或选择上传视频编辑。");
    }
    if (operation === "continue" && ["expired", "deleted"].includes(text(currentVideoTurn()?.provider_state_status))) {
      throw new Error("该版本的云端上下文已过期，请从本地视频重新开始编辑。");
    }
    if (operation === "edit" && referenceSlots().videos.length !== 1) {
      throw new Error("上传视频编辑需要选择 1 个视频素材作为输入。");
    }
    return {
      clientActionId,
      operation,
      stateful: true,
      videoThreadId: operation === "continue" ? threadId : "",
      parentTurnId: operation === "continue" ? parentTurnId : "",
      sourceVideoAssetId: operation === "edit" ? text(referenceSlots().videos[0]?.path || referenceSlots().videos[0]?.id) : "",
    };
  }

  function restartExpiredTurnFromLocal(turn) {
    const outputPath = text(turn?.output_path || turn?.output_asset_id);
    const assets = Array.isArray(props.generatedAssets?.()) ? props.generatedAssets() : [];
    const asset = assets.find((item) => text(item?.path || item?.id) === outputPath);
    if (!asset) {
      setError("本地视频素材不可用，无法从该版本重新开始编辑。");
      return;
    }
    setLocalReferences((current) => mergeAssetsByPath(current, [asset]));
    setStatefulOperation("edit");
    setSelectedParentTurnId("");
    setError("已选择本地视频。新编辑会创建一条新链，无法恢复供应商隐藏的历史上下文。");
  }

  async function deleteVideoCloudContext() {
    const id = taskId();
    const threadId = text(videoInteraction()?.video_thread_id);
    if (!id || !threadId || videoInteractionDeleteBusy()) return;
    if (!window.confirm("清除云端编辑上下文后，旧链不能继续编辑；本地 MP4 会保留。是否继续？")) return;
    setVideoInteractionDeleteBusy(true);
    try {
      const result = await props.api.deleteAssetLibraryVideoInteractionCloudContext(id, threadId);
      setVideoInteraction((current) => ({ ...current, ...result, turns: Array.isArray(result?.turns) ? result.turns : current.turns }));
      setStatefulOperation("generate");
      setSelectedParentTurnId("");
      if (result?.failed_count) setError("部分云端上下文删除失败，服务端已保留可重试状态。");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setVideoInteractionDeleteBusy(false);
    }
  }

  function scrollMessagesToBottom() {
    if (!messagesScrollEl || typeof window === "undefined") return;
    if (scrollFrame) window.cancelAnimationFrame(scrollFrame);
    scrollFrame = window.requestAnimationFrame(() => {
      scrollFrame = window.requestAnimationFrame(() => {
        if (messagesBottomEl?.scrollIntoView) messagesBottomEl.scrollIntoView({ block: "end" });
        if (messagesScrollEl) messagesScrollEl.scrollTop = messagesScrollEl.scrollHeight;
      });
    });
  }

  function isStreamDeltaEvent(payload) {
    return String(payload?.type || "") === "message.part.delta";
  }

  function reduceAndKeepReferences(current, payload) {
    const next = reduceAgentChatEvent(current, payload);
    const messages = suppressVideoGenerationTextFragments(next.messages || [], next.partsByMessageId || {});
    return {
      ...next,
      messages: withPendingUserReferences(messages, next.partsByMessageId || {}),
    };
  }

  function flushStreamDeltaBuffer() {
    if (streamDeltaTimer && typeof window !== "undefined") window.clearTimeout(streamDeltaTimer);
    streamDeltaTimer = 0;
    const pending = streamDeltaBuffer;
    streamDeltaBuffer = [];
    if (!pending.length) return;
    setState((current) => pending.reduce(reduceAndKeepReferences, current));
  }

  function queueStreamDelta(payload) {
    streamDeltaBuffer = [...streamDeltaBuffer, payload];
    if (streamDeltaTimer || typeof window === "undefined") return;
    streamDeltaTimer = window.setTimeout(() => {
      flushStreamDeltaBuffer();
    }, 350);
  }

  function assetKey(asset) {
    return asset?.path || asset?.id || "";
  }

  function mediaUrl(asset) {
    return props.mediaUrl?.(asset) || props.assetUrl?.(asset) || "";
  }

  function referenceUrl(asset) {
    return props.referenceUrl?.(asset) || mediaUrl(asset);
  }

  function referenceThumbnailUrl(asset) {
    return props.thumbnailUrl?.(asset) || referenceUrl(asset);
  }

  function directHistoryMessageForSave(message) {
    const role = messageRole(message);
    if (!["user", "assistant"].includes(role)) return null;
    const id = messageId(message);
    if (!id || id === "local-greeting") return null;
    const sourceReferences = Array.isArray(message.referenceAttachments) ? message.referenceAttachments : [];
    const referenceAttachments = sourceReferences.map((asset) => ({
      path: text(asset?.path || asset?.id),
      label: text(asset?.label || asset?.filename || shortPathLabel(asset?.path || asset?.id)),
      role: normalizeReferenceRole(asset?.role || asset?.reference_role) || "REFERENCE_IMAGE",
      kind: assetKind(asset),
    })).filter((item) => item.path);
    const saved = {
      id,
      role,
      text: text(message.text || rawMessageText(message, state().partsByMessageId)),
      created_at: Number(message.created_at || messageTime(message) || Date.now()),
    };
    if (referenceAttachments.length) saved.referenceAttachments = referenceAttachments;
    if (message.path) {
      saved.path = text(message.path);
      saved.filename = text(message.filename || shortPathLabel(message.path));
    }
    if (message.aspect) saved.aspect = text(message.aspect);
    if (message.videoPlaceholder && !message.videoUrl) {
      saved.videoPlaceholder = true;
      saved.progressLabel = text(message.progressLabel || "0%");
    }
    if (message.failed) {
      saved.failed = true;
      saved.progressLabel = text(message.progressLabel || "Failed");
    }
    if (!saved.text && !saved.path && !saved.videoPlaceholder && !saved.failed && !referenceAttachments.length) return null;
    return saved;
  }

  function directHistoryMessagesForSave(items = state().messages || []) {
    return (items || []).map(directHistoryMessageForSave).filter(Boolean).slice(-VIDEO_WORKSPACE_HISTORY_LIMIT);
  }

  function hydrateDirectHistoryMessage(message) {
    const path = text(message?.path);
    const filename = text(message?.filename || shortPathLabel(path));
    const videoUrl = path ? mediaUrl({ id: path, path, filename, kind: "video", asset_type: "Video" }) : "";
    return {
      ...message,
      filename: filename || message?.filename || "",
      videoUrl: message?.videoUrl || videoUrl,
      localAgentVideoPlaceholder: Boolean(message?.videoPlaceholder && !videoUrl && !message?.failed),
    };
  }

  async function saveDirectHistorySnapshot(messages) {
    if (isAgent() || !props.saveVideoAPIHistory) return;
    const id = taskId();
    if (!id) return;
    try {
      await props.saveVideoAPIHistory({ messages: directHistoryMessagesForSave(messages) });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  function flushDirectHistorySave() {
    if (directHistorySaveTimer && typeof window !== "undefined") window.clearTimeout(directHistorySaveTimer);
    directHistorySaveTimer = 0;
    const snapshot = directHistorySaveSnapshot;
    directHistorySaveSnapshot = null;
    if (snapshot) void saveDirectHistorySnapshot(snapshot);
  }

  function queueDirectHistorySave(messages, delay = 500) {
    if (isAgent()) return;
    directHistorySaveSnapshot = messages;
    if (!directHistoryReady()) return;
    if (directHistorySaveTimer && typeof window !== "undefined") window.clearTimeout(directHistorySaveTimer);
    if (typeof window === "undefined" || delay <= 0) {
      flushDirectHistorySave();
      return;
    }
    directHistorySaveTimer = window.setTimeout(() => {
      flushDirectHistorySave();
    }, delay);
  }

  function addReferenceAssets(items = []) {
    const nextItems = (items || []).filter((item) => text(item?.path || item?.id));
    if (!nextItems.length) return;
    setLocalReferences((previous) => mergeAssetsByPath(previous, nextItems));
  }

  function referenceKindKey(asset) {
    const kind = assetKind(asset);
    if (kind === "image") return "images";
    if (kind === "audio") return "audios";
    if (kind === "video") return "videos";
    return "";
  }

  function referenceLimitMessage(kindKey, capability = videoCapability()) {
    const limit = capability.references[kindKey];
    if (!limit || limit.max <= 0) return "This video model does not support that reference type.";
    return `${limit.label} allow at most ${limit.max}.`;
  }

  function enforceAddedLibraryReference(asset) {
    const key = text(asset?.path || asset?.id);
    if (!key) return;
    const kindKey = referenceKindKey(asset);
    const capability = videoCapability();
    const limit = kindKey ? capability.references[kindKey] : null;
    const currentReferences = referenceAssets();
    const referencesForCheck = currentReferences.some((item) => text(item?.path || item?.id) === key)
      ? currentReferences
      : [...currentReferences, asset];
    const slots = referencesToVideoSlots(referencesForCheck);
    const count = kindKey ? (slots[kindKey]?.length || 0) : 0;
    if (!kindKey || !limit || limit.max <= 0 || count > limit.max) {
      removeReferenceAsset(asset);
      setError(referenceLimitMessage(kindKey, capability));
    }
  }

  if (typeof window !== "undefined") {
    const handleLibraryReferenceAdded = (event) => {
      const asset = event?.detail?.asset;
      const run = () => enforceAddedLibraryReference(asset);
      if (typeof window.queueMicrotask === "function") window.queueMicrotask(run);
      else window.setTimeout(run, 0);
    };
    window.addEventListener("koubo-storyboard:asset-library-add-reference", handleLibraryReferenceAdded);
    onCleanup(() => window.removeEventListener("koubo-storyboard:asset-library-add-reference", handleLibraryReferenceAdded));
  }

  function appendReferenceAssetsWithinCapability(items = []) {
    const accepted = [];
    const rejected = [];
    const counts = { ...referenceValidation().counts };
    const capability = videoCapability();
    const seen = new Set(referenceAssets().map((asset) => text(asset?.path || asset?.id)));
    for (const item of items || []) {
      const key = text(item?.path || item?.id);
      if (!key || seen.has(key)) continue;
      const kindKey = referenceKindKey(item);
      if (!kindKey) {
        rejected.push("Unsupported reference asset type.");
        continue;
      }
      const limit = capability.references[kindKey];
      if (!limit || limit.max <= 0 || counts[kindKey] >= limit.max) {
        rejected.push(referenceLimitMessage(kindKey, capability));
        continue;
      }
      counts[kindKey] += 1;
      counts.total += 1;
      seen.add(key);
      accepted.push(item);
    }
    addReferenceAssets(accepted);
    if (rejected.length) setError([...new Set(rejected)].join(" "));
    else if (accepted.length) setError("");
  }

  function fileReferenceKind(file) {
    const type = String(file?.type || "").toLowerCase();
    const name = String(file?.name || "").toLowerCase();
    if (type.startsWith("image/") || /\.(avif|gif|heic|jpe?g|png|svg|webp)$/.test(name)) return "images";
    if (type.startsWith("audio/") || /\.(wav|m4a|mp3|aac|ogg|oga|flac|opus|aiff?|caf|weba|wma)$/.test(name)) return "audios";
    if (type.startsWith("video/") || /\.(mp4|mov|webm|m4v)$/.test(name)) return "videos";
    return "";
  }

  async function uploadReferenceFiles(files, source) {
    const requested = Array.from(files || []);
    if (!requested.length || busy()) return;
    const capability = videoCapability();
    const counts = { ...referenceValidation().counts };
    const groups = { images: [], audios: [], videos: [] };
    const rejected = [];
    for (const file of requested) {
      const kindKey = fileReferenceKind(file);
      if (!kindKey) {
        rejected.push("Unsupported reference file type.");
        continue;
      }
      const limit = capability.references[kindKey];
      if (!limit || limit.max <= 0 || counts[kindKey] >= limit.max) {
        rejected.push(referenceLimitMessage(kindKey, capability));
        continue;
      }
      counts[kindKey] += 1;
      counts.total += 1;
      groups[kindKey].push(file);
    }
    const uploaded = [];
    try {
      if (groups.images.length) uploaded.push(...(await props.uploadImageFiles?.(groups.images, { source }) || []));
      if (groups.videos.length) uploaded.push(...(await props.uploadMediaFiles?.(groups.videos, "videos", { source }) || []));
      if (groups.audios.length) uploaded.push(...(await props.uploadMediaFiles?.(groups.audios, "audio", { source }) || []));
      appendReferenceAssetsWithinCapability(uploaded);
      if (rejected.length) setError([...new Set(rejected)].join(" "));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function chooseReferenceFiles(event) {
    const input = event.currentTarget;
    await uploadReferenceFiles(input.files, "video_composer_plus");
    input.value = "";
    input.accept = REFERENCE_FILE_ACCEPTS.all;
    setReferenceFileAccept(REFERENCE_FILE_ACCEPTS.all);
  }

  function openReferenceFilePicker(kindKey = "all") {
    const accept = REFERENCE_FILE_ACCEPTS[kindKey] || REFERENCE_FILE_ACCEPTS.all;
    setReferenceFileAccept(accept);
    if (referenceFileInput) referenceFileInput.accept = accept;
    referenceFileInput?.click?.();
  }

  const dragHasFiles = (event) => Array.from(event.dataTransfer?.types || []).includes("Files");
  const dragHasAsset = (event) => Array.from(event.dataTransfer?.types || []).includes("application/x-koubo-storyboard-asset");
  function assetFromDataTransfer(dataTransfer) {
    try {
      const payload = dataTransfer?.getData?.("application/x-koubo-storyboard-asset") || "";
      if (!payload) return null;
      const asset = JSON.parse(payload);
      return asset && typeof asset === "object" ? asset : null;
    } catch {
      return null;
    }
  }

  function handleReferenceDrag(event) {
    if ((!dragHasFiles(event) && !dragHasAsset(event)) || busy()) return;
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = "copy";
    setReferenceDragging(true);
  }

  async function handleReferenceDrop(event) {
    if ((!dragHasFiles(event) && !dragHasAsset(event)) || busy()) return;
    event.preventDefault();
    event.stopPropagation();
    setReferenceDragging(false);
    const asset = assetFromDataTransfer(event.dataTransfer);
    if (asset) {
      appendReferenceAssetsWithinCapability([asset]);
      return;
    }
    await uploadReferenceFiles(event.dataTransfer?.files, "video_composer_drop");
  }

  async function initializeDirectHistory() {
    const id = taskId();
    const marker = `${id}:video-api-workspace`;
    if (isAgent() || !id || directHistoryLoadedFor() === marker) return;
    setDirectHistoryLoadedFor(marker);
    setDirectHistoryReady(false);
    try {
      const payload = await props.loadVideoAPIHistory?.();
      const storedMessages = Array.isArray(payload?.messages) ? payload.messages.map(hydrateDirectHistoryMessage) : [];
      setState((current) => {
        if (current.busy || (current.messages || []).length) return current;
        return { ...current, messages: storedMessages, partsByMessageId: {}, busy: false, error: "" };
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setDirectHistoryReady(true);
      flushDirectHistorySave();
    }
  }

  async function ensureDirectHistoryForAction() {
    if (isAgent() || directHistoryReady()) return;
    setDirectHistoryRequested(true);
    await initializeDirectHistory();
  }

  function requestDirectHistoryLoad() {
    if (!isAgent()) setDirectHistoryRequested(true);
  }

  function removeReferenceAsset(asset) {
    const key = text(asset?.path || asset?.id);
    if (!key) return;
    setLocalReferences((previous) => previous.filter((item) => text(item?.path || item?.id) !== key));
    props.onRemoveReference?.(asset);
  }

  function consumeComposerReferenceAssets() {
    const snapshot = referenceAssets().slice();
    if (!snapshot.length) return snapshot;
    setLocalReferences([]);
    props.onClearReferences?.();
    for (const asset of snapshot) props.onRemoveReference?.(asset);
    window.dispatchEvent(new CustomEvent("koubo-storyboard:asset-library-clear-references"));
    return snapshot;
  }

  function recordPendingUserReferences(message, references, sentAt) {
    if (!references.length) return;
    pendingUserReferenceAttachments = [
      ...pendingUserReferenceAttachments.slice(-20),
      { text: normalizeTextWhitespace(message), references, sentAt },
    ];
  }

  function withPendingUserReferences(items = [], partsByMessageId = {}) {
    if (!pendingUserReferenceAttachments.length) return items;
    return (items || []).map((message) => {
      if (messageRole(message) !== "user" || message.referenceAttachments?.length) return message;
      const body = normalizeTextWhitespace(rawMessageText(message, partsByMessageId));
      if (!body) return message;
      const match = [...pendingUserReferenceAttachments].reverse().find((entry) => (
        entry.text === body
      ));
      return match ? { ...message, referenceAttachments: match.references } : message;
    });
  }

  function normalizeConsistencyReferences(payload) {
    const active = payload?.config?.active || {};
    const consistencyRefs = Array.isArray(payload?.consistency_references?.references) ? payload.consistency_references.references : [];
    const consistencyPathFor = (key) => String(consistencyRefs.find((item) => item?.kind === key)?.output_path || "").trim();
    return [
      {
        key: "host",
        label: "人物一致性",
        section: payload?.host,
        activePath: active.host_reference,
        consistencyPath: consistencyPathFor("host"),
        fallbackPath: "SessionContext/Consistency/HOST.png",
        fallbackFilename: "HOST.png",
      },
      {
        key: "product",
        label: "产品一致性",
        section: payload?.product,
        activePath: active.product_reference,
        consistencyPath: consistencyPathFor("product"),
        fallbackPath: "SessionContext/Consistency/Product.png",
        fallbackFilename: "Product.png",
      },
    ].map((item) => {
      const path = String(item.section?.output || item.activePath || item.consistencyPath || item.fallbackPath || "").trim();
      if (!path) return null;
      return {
        id: `consistency:${item.key}:${path}`,
        key: item.key,
        label: item.label,
        filename: path.split("/").pop() || item.fallbackFilename,
        kind: "image",
        reference_role: item.key === "host" ? "HOST_REFERENCE" : "PRODUCT_REFERENCE",
        role: item.key === "host" ? "HOST_REFERENCE" : "PRODUCT_REFERENCE",
        source: "session_consistency_reference",
        path,
      };
    }).filter(Boolean);
  }

  async function openConsistencyPicker() {
    if (busy()) return;
    setConsistencyPickerOpen(true);
    setConsistencyLoading(true);
    setError("");
    try {
      const payload = await props.loadConsistencyReferences?.();
      const refs = normalizeConsistencyReferences(payload);
      setConsistencyReferences(refs);
      setSelectedConsistencyKeys(new Set(refs.map((item) => item.key)));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setConsistencyLoading(false);
    }
  }

  function toggleConsistencyReference(item) {
    setSelectedConsistencyKeys((previous) => {
      const next = new Set(previous);
      if (next.has(item.key)) next.delete(item.key);
      else next.add(item.key);
      return next;
    });
  }

  function confirmConsistencyReferences() {
    const selected = consistencyReferences().filter((item) => selectedConsistencyKeys().has(item.key));
    appendReferenceAssetsWithinCapability(selected);
    setConsistencyPickerOpen(false);
  }

  function dropMissingConsistencyReference(item) {
    setConsistencyReferences((previous) => previous.filter((candidate) => candidate.key !== item.key || candidate.path !== item.path));
    setSelectedConsistencyKeys((previous) => {
      const next = new Set(previous);
      next.delete(item.key);
      return next;
    });
  }

  async function openPromptBuilder() {
    if (busy()) return;
    setPromptBuilderOpen(true);
    setPromptBuilderLoading(true);
    setPromptBuilderError("");
    setPromptBuilderPayload({});
    setConsistencyPickerOpen(false);
    try {
      const config = await refreshVideoModelConfig(true);
      const selected = selectedAgentVideoModel(config);
      if (!selected) throw new Error("请先在视频设置中选择一个视频模型。");
      const nextSettings = {
        ...settings(),
        ...videoModelSelectionPayload(selected),
      };
      setSettings(nextSettings);
      const referencePayload = splitVideoReferencePayload(referenceAssets());
      const capability = resolveVideoModelCapability(nextSettings, config || videoModelConfig() || {}, { isAgent: isAgent() });
      const response = await props.buildPromptBuilder?.({
        mode: "video",
        agentVideoAlias: nextSettings.agentVideoAlias,
        provider: nextSettings.provider,
        model: nextSettings.model,
        draft: draft(),
        reference_images: referencePayload.referenceImages,
        reference_audios: referencePayload.referenceAudios,
        reference_videos: referencePayload.referenceVideos,
        reference_mode: capability.referenceMode,
        aspect: nextSettings.aspect || "9:16",
        duration: nextSettings.duration || 4,
      });
      setPromptBuilderPayload(response || {});
    } catch (err) {
      setPromptBuilderError(err instanceof Error ? err.message : String(err));
    } finally {
      setPromptBuilderLoading(false);
    }
  }

  async function applyPromptBuilder({ applyMode, insertMode, positivePrompt, negativePrompt, prompt }) {
    const requestId = promptBuilderPayload().request_id;
    if (!requestId) throw new Error("提示词构建器草稿缺少 request id");
    const agentVideoAlias = promptBuilderPayload().agentVideoAlias || settings().agentVideoAlias || "";
    const response = await props.savePromptBuilder?.(requestId, {
      mode: promptBuilderPayload().mode || "video",
      agentVideoAlias,
      provider: agentVideoAlias ? "" : (promptBuilderPayload().provider || settings().provider || ""),
      model: agentVideoAlias ? "" : (promptBuilderPayload().model || settings().model || ""),
      template_path: promptBuilderPayload().template_path || "",
      positive_prompt: positivePrompt,
      negative_prompt: negativePrompt,
      prompt,
      apply_mode: applyMode,
    });
    const nextPrompt = response?.prompt || prompt || "";
    setDraft((previous) => {
      const current = String(previous || "").trim();
      if (!current || insertMode === "replace") return nextPrompt;
      return `${current}\n\n${nextPrompt}`;
    });
    setLastAppliedPromptBuilder({
      requestId,
      appliedPath: response?.applied_path || "",
      prompt: nextPrompt,
    });
    setPromptBuilderOpen(false);
  }

  async function snapshotVideoPromptTemplate(sourceSettings = settings()) {
    if (!props.buildPromptBuilder) return null;
    const agentVideoAlias = text(sourceSettings.agentVideoAlias || sourceSettings.agent_video_alias);
    const provider = agentVideoAlias ? "" : text(sourceSettings.provider);
    const model = agentVideoAlias ? "" : text(sourceSettings.model);
    if (!agentVideoAlias && (!provider || !model)) return null;
    return await props.buildPromptBuilder({
      mode: "video",
      agentVideoAlias,
      provider,
      model,
      draft: "",
      reference_images: [],
      aspect: sourceSettings.aspect || "9:16",
      duration: sourceSettings.duration || 4,
      snapshot_only: true,
    });
  }

  function upsertLocalMessage(id, patch, fallback = {}) {
    if (!id) return;
    let nextSnapshot = null;
    setState((current) => {
      const exists = (current.messages || []).some((message) => messageId(message) === id);
      const nextMessage = { id, role: "assistant", created_at: Date.now(), ...fallback, ...patch };
      const nextMessages = exists
        ? current.messages.map((message) => messageId(message) === id ? { ...message, ...patch } : message)
        : [...current.messages, nextMessage];
      nextSnapshot = nextMessages;
      return { ...current, messages: nextMessages };
    });
    if (nextSnapshot) queueDirectHistorySave(nextSnapshot);
    scrollMessagesToBottom();
  }

  function agentVideoProgressPercent(startedAt = agentVideoProgressStartedAt) {
    const elapsed = Math.max(0, (Date.now() - startedAt) / 1000);
    return Math.min(AGENT_VIDEO_PROGRESS_MAX, Math.max(8, Math.round(8 + elapsed * 2)));
  }

  function stopAgentVideoProgress() {
    if (agentVideoProgressTimer) window.clearInterval(agentVideoProgressTimer);
    agentVideoProgressTimer = null;
  }

  function startAgentVideoProgress(id) {
    if (!id) return;
    if (activeAgentVideoMessageId === id && agentVideoProgressTimer) return;
    activeAgentVideoMessageId = id;
    agentVideoProgressStartedAt = Date.now();
    stopAgentVideoProgress();
    upsertLocalMessage(id, { progressLabel: "0%" });
    agentVideoProgressTimer = window.setInterval(() => {
      upsertLocalMessage(id, { progressLabel: `${agentVideoProgressPercent()}%` });
    }, 1000);
  }

  function removeStaleAgentVideoPlaceholders(keepId = "") {
    setState((current) => ({
      ...current,
      messages: (current.messages || []).filter((message) => {
        if (messageId(message) === keepId) return true;
        return !(message.localAgentVideoPlaceholder && message.videoPlaceholder && !message.videoUrl && !message.failed);
      }),
    }));
  }

  function displayProgressLabel(value) {
    const label = text(value);
    if (!label || /^generating$/i.test(label)) return "0%";
    const match = label.match(/^(\d+(?:\.\d+)?)%$/);
    if (match) return `${Math.min(AGENT_VIDEO_PROGRESS_MAX, Math.max(0, Math.round(Number(match[1]) || 0)))}%`;
    return label;
  }

  function agentVideoEventMessageId(properties = {}) {
    const eventId = properties.agent_generation_id || properties.request_id;
    return eventId ? `agent-video-${eventId}` : "";
  }

  function agentVideoHandledEventKey(type, properties = {}) {
    const eventId = properties.agent_generation_id || properties.request_id;
    return eventId && String(type || "").startsWith("asset_agent.video_generation.") ? `${type}:${eventId}` : "";
  }

  function handleAssetVideoEvent(payload) {
    const type = String(payload?.type || "");
    const properties = payload?.properties || {};
    const handledKey = agentVideoHandledEventKey(type, properties);
    if (handledKey && handledAgentVideoEventKeys.has(handledKey)) return true;
    if (type === "asset_agent.video_generation.started") {
      if (handledKey) handledAgentVideoEventKeys.add(handledKey);
      const id = agentVideoEventMessageId(properties);
      if (!id) {
        props.onAgentVideoGenerationEvent?.(payload);
        return true;
      }
      upsertLocalMessage(id, {
        text: properties.title ? `Agent is generating video: ${properties.title}` : "Agent is generating a video.",
        videoPlaceholder: true,
        progressLabel: "0%",
        aspect: properties.aspect || settings().aspect,
        localAgentVideoPlaceholder: true,
      });
      startAgentVideoProgress(id);
      setState((current) => ({ ...current, busy: true }));
      props.onAgentVideoGenerationEvent?.(payload);
      return true;
    }
    if (type === "asset_agent.video_generation.completed") {
      if (handledKey) handledAgentVideoEventKeys.add(handledKey);
      const id = agentVideoEventMessageId(properties);
      const asset = properties.asset || {};
      stopAgentVideoProgress();
      if (id) {
        upsertLocalMessage(id, {
          text: `已经生成并保存到视频素材：${asset.filename || asset.path || "智能体生成视频"}`,
          videoPlaceholder: false,
          progressLabel: "",
          videoUrl: (assetKey(asset) ? mediaUrl(asset) : "") || properties.video_url || properties.provider_result?.video_url || "",
          path: asset.path || "",
          filename: asset.filename || "",
          aspect: properties.aspect || asset.aspect || settings().aspect,
          localAgentVideoPlaceholder: false,
        });
      }
      removeStaleAgentVideoPlaceholders(id);
      activeAgentVideoMessageId = "";
      setState((current) => ({ ...current, busy: false }));
      if (properties.video_thread_id) {
        setVideoInteraction((current) => ({
          ...current,
          video_thread_id: properties.video_thread_id,
          head_turn_id: properties.video_turn_id || current.head_turn_id,
        }));
        void refreshVideoInteraction();
      }
      props.onAgentVideoGenerationEvent?.(payload);
      return true;
    }
    if (type === "asset_agent.video_generation.failed") {
      if (handledKey) handledAgentVideoEventKeys.add(handledKey);
      const id = agentVideoEventMessageId(properties);
      const detail = String(properties.detail?.message || properties.detail || properties.message || "Agent video generation failed.");
      stopAgentVideoProgress();
      if (id) upsertLocalMessage(id, { text: detail, failed: true, progressLabel: "Failed", videoPlaceholder: false });
      activeAgentVideoMessageId = "";
      setState((current) => ({ ...current, busy: false }));
      props.onAgentVideoGenerationEvent?.(payload);
      return true;
    }
    return false;
  }

  function replayAssetVideoGenerationEvents(payload = {}) {
    const events = Array.isArray(payload?.asset_video_generation_events) ? payload.asset_video_generation_events : [];
    for (const event of events) {
      const status = text(event?.status);
      if (!["started", "completed", "failed"].includes(status)) continue;
      handleAssetVideoEvent({ type: `asset_agent.video_generation.${status}`, properties: event });
    }
  }

  function referencesForVideoRequest(request, fallbackReferences = []) {
    const explicit = [
      ...(request.referenceImages || []),
      ...(request.referenceAudios || []),
      ...(request.referenceVideos || []),
    ].filter((item) => text(item?.path || item?.id));
    return explicit.length ? explicit : fallbackReferences;
  }

  async function startFallbackAgentVideoGeneration(request, context = {}) {
    const prompt = text(request?.prompt);
    if (!prompt || !props.generateVideo) return;
    const requestKey = stableVideoRequestKey(request);
    if (fallbackVideoRequestKeys.has(requestKey)) return;
    if (activeAgentVideoMessageId || hasActiveVideoPlaceholder(state().messages || [])) return;
    fallbackVideoRequestKeys.add(requestKey);
    const sourceSettings = context.settings || settings();
    const localAssistantId = `agent-video-fallback-${Date.now()}`;
    const aspect = request.aspect || sourceSettings.aspect || "9:16";
    const generationReferences = referencesForVideoRequest(request, context.references || []);
    upsertLocalMessage(localAssistantId, {
      text: request.title ? `Agent is generating video: ${request.title}` : "Agent is generating a video.",
      videoPlaceholder: true,
      progressLabel: "0%",
      aspect,
      localAgentVideoPlaceholder: true,
    });
    startAgentVideoProgress(localAssistantId);
    setState((current) => ({ ...current, busy: true }));
    try {
      let completed = null;
      let failed = false;
      await props.generateVideo(prompt, generationReferences, {
        title: request.title || "智能体生成视频",
        aspect,
        duration: Number(request.duration) || Number(sourceSettings.duration) || 4,
        count: Number(sourceSettings.count) || 1,
        provider: sourceSettings.agentVideoAlias ? "" : (request.provider || sourceSettings.provider || ""),
        model: sourceSettings.agentVideoAlias ? "" : (request.model || sourceSettings.model || ""),
        agentVideoAlias: sourceSettings.agentVideoAlias || "",
        settingsScope: "videos_agent",
        referenceMode: request.referenceMode || sourceSettings.referenceMode || "",
        promptCandidateTitle: request.title || "",
      }, (event) => {
        if (event?.type === "heartbeat") {
          const elapsed = Number(event.elapsed_seconds || 0);
          const percent = Math.min(AGENT_VIDEO_PROGRESS_MAX, Math.max(8, Math.round(8 + elapsed * 2)));
          upsertLocalMessage(localAssistantId, { progressLabel: `${percent}%` });
        }
        if (event?.type === "failed") {
          failed = true;
          const detail = String(event.detail?.message || event.detail || "视频生成失败。");
          upsertLocalMessage(localAssistantId, { failed: true, videoPlaceholder: false, progressLabel: "Failed", text: detail });
        }
        if (event?.type === "completed") {
          completed = event;
          const assets = Array.isArray(event.assets) && event.assets.length ? event.assets : [event.asset].filter(Boolean);
          const asset = assets[0] || {};
          const completedText = assets.length > 1
            ? `已经生成并保存到视频素材：${assets.length} 个视频`
            : `已经生成并保存到视频素材：${asset.filename || asset.path || "智能体生成视频"}`;
          upsertLocalMessage(localAssistantId, {
            videoPlaceholder: false,
            localAgentVideoPlaceholder: false,
            progressLabel: "",
            text: completedText,
            videoUrl: assetKey(asset) ? mediaUrl(asset) : "",
            path: asset.path || "",
            filename: asset.filename || "",
            aspect: event.aspect || asset.aspect || aspect,
          });
        }
      });
      if (!completed && !failed) {
        upsertLocalMessage(localAssistantId, { videoPlaceholder: false, progressLabel: "", text: "视频生成完成。" });
      }
    } catch (err) {
      const detail = err instanceof Error ? err.message : String(err);
      upsertLocalMessage(localAssistantId, { failed: true, videoPlaceholder: false, progressLabel: "Failed", text: detail });
      setError(detail);
    } finally {
      stopAgentVideoProgress();
      activeAgentVideoMessageId = "";
      setState((current) => ({ ...current, busy: false }));
    }
  }

  function maybeStartFallbackVideoGenerationFromMessages(items = [], partsByMessageId = {}, context = {}) {
    if (!isAgent() || !props.generateVideo) return;
    if (backendOwnsAgentVideoGeneration()) return;
    const sentAt = Number(context.sentAt || 0);
    const candidates = [];
    for (const message of items || []) {
      if (messageRole(message) !== "assistant") continue;
      const completedAt = Number(message?.info?.time?.completed || message?.time?.completed || message?.completed_at || 0);
      const completedAtMs = completedAt && completedAt < 100_000_000_000 ? completedAt * 1000 : completedAt;
      if (sentAt && completedAtMs && completedAtMs + 10_000 < sentAt) continue;
      for (const request of extractVideoGenerationRequests(message, partsByMessageId)) {
        candidates.push({ request, completedAt: completedAtMs || messageTime(message) || Date.now() });
      }
    }
    const latest = candidates.sort((a, b) => a.completedAt - b.completedAt).at(-1);
    if (!latest) return;
    const requestKey = stableVideoRequestKey(latest.request);
    if (fallbackVideoRequestKeys.has(requestKey)) return;
    window.setTimeout(() => {
      if (activeAgentVideoMessageId || hasActiveVideoPlaceholder(state().messages || [])) return;
      void startFallbackAgentVideoGeneration(latest.request, context);
    }, 1200);
  }

  function completedAssistantMessageFromAgentEvent(payload = {}) {
    if (String(payload?.type || "") !== "message.updated") return null;
    const properties = payload.properties || {};
    const message = properties.info ? { info: properties.info, parts: properties.parts || [] } : (properties.message || properties);
    if (messageRole(message) !== "assistant") return null;
    if (!message?.info?.time?.completed && !message?.time?.completed && !message?.completed_at) return null;
    return message;
  }

  function applyPromptModels(payload) {
    if (payload?.prompt_models) {
      setPromptModels(payload.prompt_models);
      const defaultModel = payload.prompt_models.default_model || {};
      const current = selectedModelKey();
      const available = payload.prompt_models.items || [];
      const stored = settings();
      const storedKey = stored.chatProvider && stored.chatModel
        ? modelKey({ providerID: stored.chatProvider, modelID: stored.chatModel })
        : "";
      if (storedKey && available.some((item) => modelKey(item) === storedKey)) {
        setSelectedModelKey(storedKey);
        return;
      }
      if (!current || !available.some((item) => modelKey(item) === current)) {
        const next = available.find((item) => modelKey(item) === modelKey(defaultModel)) || available[0] || defaultModel;
        setSelectedModelKey(modelKey(next));
      }
    }
    if (payload?.chat_opencode_session_id !== undefined) setChatSessionId(String(payload.chat_opencode_session_id || ""));
  }

  async function initialize() {
    const id = taskId();
    const key = text(props.agentKey);
    if (!isAgent() || !id || !key) return;
    const marker = `${id}:${key}`;
    if (initializedFor() === marker) return;
    setError("");
    try {
      const ensured = await props.api.agentChatEnsureSession(id, key);
      applyPromptModels(ensured);
      const history = await props.api.agentChatMessages(id, key);
      applyPromptModels(history);
      setState((current) => {
        const seeded = seedAgentChatState(history?.items || []);
        return {
          ...current,
          ...seeded,
          messages: withPendingUserReferences(seeded.messages || [], seeded.partsByMessageId || {}),
          error: "",
        };
      });
      replayAssetVideoGenerationEvents(history);
      setInitializedFor(marker);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  createEffect(() => {
    if (!initialLoadReady()) return;
    void initialize();
  });

  createEffect(() => {
    if (!initialLoadReady()) return;
    if (props.deferHistoryLoad && !directHistoryRequested()) return;
    void initializeDirectHistory();
  });

  createEffect(() => {
    const id = taskId();
    const key = text(props.agentKey);
    const sessionId = chatSessionId();
    if (!isAgent() || !id || !key || !sessionId) return;
    const source = new EventSource(props.api.agentChatEventsUrl(id, key), { withCredentials: true });
    source.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        if (isStreamDeltaEvent(payload)) {
          queueStreamDelta(payload);
          return;
        }
        flushStreamDeltaBuffer();
        if (!handleAssetVideoEvent(payload)) {
          setState((current) => reduceAndKeepReferences(current, payload));
          const completedMessage = completedAssistantMessageFromAgentEvent(payload);
          if (completedMessage) {
            const seeded = seedAgentChatState([completedMessage]);
            maybeStartFallbackVideoGenerationFromMessages(seeded.messages || [], seeded.partsByMessageId || {}, lastAgentVideoSendContext || {});
          }
        }
        scrollMessagesToBottom();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    };
    source.onerror = () => setError("智能体聊天流已断开。");
    onCleanup(() => {
      flushStreamDeltaBuffer();
      source.close();
    });
  });

  createEffect(() => {
    if (!isAgent()) return;
    const sessionId = chatSessionId();
    state().messages.length;
    const generatedAssets = Array.isArray(props.generatedAssets?.()) ? props.generatedAssets() : [];
    const resultAssets = generatedAssets
      .filter((asset) => assetKind(asset) === "video")
      .map((asset) => {
        const generationId = text(asset?.origin?.agent_generation_id || asset?.agent_generation_id);
        if (!generationId) return null;
        const assetSessionId = text(asset?.origin?.chat_opencode_session_id || asset?.chat_opencode_session_id);
        if (sessionId && assetSessionId && assetSessionId !== sessionId) return null;
        const path = text(asset?.path || asset?.id);
        if (!path) return null;
        return {
          id: `agent-video-${generationId}`,
          role: "assistant",
          created_at: Number(asset?.created_at || Date.now()) || Date.now(),
          text: `已经生成并保存到视频素材：${asset?.filename || path || "智能体生成视频"}`,
          videoUrl: mediaUrl(asset),
          path,
          filename: text(asset?.filename || shortPathLabel(path)),
          aspect: text(asset?.aspect) || settings().aspect || "9:16",
          localAgentVideoPlaceholder: false,
        };
      })
      .filter(Boolean);
    if (!resultAssets.length) return;
    setState((current) => {
      const byId = new Map(resultAssets.map((message) => [message.id, message]));
      let changed = false;
      const nextMessages = (current.messages || []).map((message) => {
        const replacement = byId.get(messageId(message));
        if (!replacement) return message;
        byId.delete(replacement.id);
        if (message.videoUrl && message.path) return message;
        changed = true;
        return { ...message, ...replacement, videoPlaceholder: false, progressLabel: "", failed: false };
      });
      if (byId.size) {
        changed = true;
        nextMessages.push(...byId.values());
      }
      if (!changed) return current;
      nextMessages.sort((a, b) => messageTime(a) - messageTime(b));
      return { ...current, messages: nextMessages };
    });
    scrollMessagesToBottom();
  });

  createEffect(() => {
    messages().length;
    busy();
    error();
    scrollMessagesToBottom();
  });

  createEffect(() => {
    if (!isAgent()) return;
    const activePlaceholder = (state().messages || []).find((message) => (
      messageRole(message) !== "user"
      && message.videoPlaceholder
      && !message.videoUrl
      && !message.failed
      && !messageCompletedAt(message)
    ));
    if (!activePlaceholder) return;
    const id = messageId(activePlaceholder);
    if (!id) return;
    const label = displayProgressLabel(activePlaceholder.progressLabel);
    const raw = rawMessageText(activePlaceholder, state().partsByMessageId);
    const patch = {
      videoPlaceholder: true,
      progressLabel: label,
      aspect: activePlaceholder.aspect || settings().aspect || "9:16",
      localAgentVideoPlaceholder: true,
    };
    if (/^generating$/i.test(text(raw))) patch.text = " ";
    if (label !== activePlaceholder.progressLabel) upsertLocalMessage(id, patch);
  });

  onCleanup(() => {
    if (historyFallbackTimer) window.clearTimeout(historyFallbackTimer);
    if (scrollFrame) window.cancelAnimationFrame(scrollFrame);
    flushDirectHistorySave();
    stopAgentVideoProgress();
  });

  function completedAssistantAfter(items, sentAt) {
    return (items || []).some((item) => {
      if (messageRole(item) !== "assistant") return false;
      const completedAt = Number(item?.info?.time?.completed || item?.time?.completed || 0);
      const completedAtMs = completedAt && completedAt < 100_000_000_000 ? completedAt * 1000 : completedAt;
      return completedAtMs + 10_000 >= sentAt;
    });
  }

  function scheduleHistoryFallback(id, key, sentAt, attempt = 0, context = {}) {
    if (historyFallbackTimer) window.clearTimeout(historyFallbackTimer);
    historyFallbackTimer = window.setTimeout(async () => {
      if (!busy()) return;
      try {
        const history = await props.api.agentChatMessages(id, key);
        applyPromptModels(history);
        const items = history?.items || [];
        const completed = completedAssistantAfter(items, sentAt);
        const seeded = items.length ? seedAgentChatState(items) : {};
        const seededMessages = Array.isArray(seeded.messages)
          ? withPendingUserReferences(seeded.messages, seeded.partsByMessageId || {})
          : null;
        setState((current) => {
          const nextSeededMessages = seededMessages || current.messages;
          const seededIds = new Set(nextSeededMessages.map((message) => messageId(message)));
          const localVideoMessages = (current.messages || []).filter((message) => (
            (message.localAgentVideoPlaceholder || String(messageId(message)).startsWith("direct-video-"))
            && !seededIds.has(messageId(message))
            && (message.videoPlaceholder || message.videoUrl || message.failed)
          ));
          const mergedMessages = suppressVideoGenerationTextFragments([...nextSeededMessages, ...localVideoMessages], seeded.partsByMessageId || current.partsByMessageId || {});
          return {
            ...current,
            ...seeded,
            messages: mergedMessages,
            busy: completed ? false : current.busy,
            error: "",
          };
        });
        if (completed && seededMessages) {
          maybeStartFallbackVideoGenerationFromMessages(seededMessages, seeded.partsByMessageId || {}, {
            ...context,
            sentAt,
          });
        }
        replayAssetVideoGenerationEvents(history);
        if (!completed && busy() && attempt < 40) scheduleHistoryFallback(id, key, sentAt, attempt + 1, context);
      } catch {
        if (busy() && attempt < 40) scheduleHistoryFallback(id, key, sentAt, attempt + 1, context);
      }
    }, attempt === 0 ? 1800 : 2500);
  }

  function coerceSettingsForCapability(source, config) {
    const capability = resolveVideoModelCapability(source, config || videoModelConfig() || {}, { isAgent: isAgent() });
    const duration = capability.params.duration;
    const durationValue = Number(source.duration) || duration.presets[0] || duration.min || 4;
    const nextDuration = duration.presets.length && !duration.presets.includes(durationValue)
      ? duration.presets[0]
      : Math.max(duration.min || 1, Math.min(durationValue, duration.max || 120));
    const countValues = capability.params.count.values.length ? capability.params.count.values : [1];
    const countValue = Number(source.count) || countValues[0] || 1;
    return {
      ...source,
      duration: nextDuration,
      count: countValues.includes(countValue) ? countValue : countValues[0],
      referenceMode: capability.referenceMode,
    };
  }

  async function resolveSelectedVideoSettings() {
    const id = taskId();
    const config = await refreshVideoModelConfig(true);
    const selected = selectedAgentVideoModel(config);
    if (!selected) {
      setError("请先在视频设置中选择一个视频模型。");
      setSettingsOpen(true);
      return null;
    }
    const nextSettings = {
      ...settings(),
      ...videoModelSelectionPayload(selected),
    };
    return { id, config, settings: coerceSettingsForCapability(nextSettings, config) };
  }

  function validateReferencesForSettings(nextSettings, config) {
    const capability = resolveVideoModelCapability(nextSettings, config || videoModelConfig() || {}, { isAgent: isAgent() });
    const validation = validateVideoGenerationInputs(capability, referencesToVideoSlots(referenceAssets()));
    if (!validation.ok) {
      setError(validation.errors.join(" "));
      return null;
    }
    return { capability, validation };
  }

  async function sendMessage(options = {}) {
    const id = taskId();
    const key = text(props.agentKey);
    const message = text(options.message ?? draft().trim());
    if (!id || !key || !message || busy()) return;
    if (!isAgent()) {
      await requestDirectVideoGeneration(message);
      return;
    }
    const resolved = await resolveSelectedVideoSettings();
    if (!resolved) return;
    const checked = validateReferencesForSettings(resolved.settings, resolved.config);
    if (!checked) return;
    setSettings(resolved.settings);
    storeSettings(id, resolved.settings);
    if (resolved.settings.confirmBeforeGenerate !== false && !options.confirmed) {
      setPendingAgentVideoGeneration({ prompt: message });
      return;
    }
    setPendingAgentVideoGeneration(null);
    let statefulAction = {};
    try {
      statefulAction = statefulGenerationPayload(newClientActionId());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      return;
    }
    const sentAt = Date.now();
    const sentReferences = consumeComposerReferenceAssets();
    const sentReferencePayload = splitVideoReferencePayload(sentReferences);
    lastAgentVideoSendContext = {
      sentAt,
      settings: resolved.settings,
      config: resolved.config,
      references: sentReferences,
    };
    recordPendingUserReferences(message, sentReferences, sentAt);
    setDraft("");
    setError("");
    const localId = `local-${sentAt}`;
    setState((current) => ({
      ...current,
      busy: true,
      messages: [...current.messages, {
        id: localId,
        role: "user",
        text: message,
        created_at: Date.now(),
        referenceAttachments: sentReferences,
      }],
    }));
    try {
      const model = selectedModel();
      const response = await props.api.agentChatSendMessage(id, key, {
        message,
        provider: model.providerID,
        model: model.modelID,
        client_context: {
          ...(props.buildClientContext?.() || {}),
          selected_reference_assets: sentReferencePayload.referenceAssets,
          selected_reference_images: sentReferencePayload.referenceImages,
          selected_reference_audios: sentReferencePayload.referenceAudios,
          selected_reference_videos: sentReferencePayload.referenceVideos,
          video_generation_settings: {
            ...resolved.settings,
            referenceMode: checked.capability.referenceMode,
            client_action_id: statefulAction.clientActionId || "",
            operation: statefulAction.operation || "",
            stateful: statefulAction.stateful === true,
            video_thread_id: statefulAction.videoThreadId || "",
            parent_turn_id: statefulAction.parentTurnId || "",
          },
        },
      });
      applyPromptModels(response);
      scheduleHistoryFallback(id, key, sentAt, 0, {
        settings: resolved.settings,
        config: resolved.config,
        references: sentReferences,
      });
    } catch (err) {
      if (activeAgentVideoMessageId) {
        stopAgentVideoProgress();
        upsertLocalMessage(activeAgentVideoMessageId, {
          failed: true,
          videoPlaceholder: false,
          progressLabel: "Failed",
          text: err instanceof Error ? err.message : String(err),
        });
        activeAgentVideoMessageId = "";
      }
      setState((current) => ({ ...current, busy: false }));
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function abortChat() {
    if (!isAgent()) {
      if (directVideoAbortController) directVideoAbortController.abort();
      setState((current) => ({ ...current, busy: false }));
      return;
    }
    const id = taskId();
    const key = text(props.agentKey);
    if (!id || !key) return;
    try {
      await props.api.agentChatAbort(id, key);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setState((current) => ({ ...current, busy: false }));
    }
  }

  async function sendDirectVideoGeneration(message, prepared = null) {
    const id = taskId();
    const prompt = text(message);
    if (!id || !prompt || busy()) return;
    await ensureDirectHistoryForAction();
    const resolved = prepared?.settings
      ? { id, config: prepared.config || videoModelConfig(), settings: prepared.settings }
      : await resolveSelectedVideoSettings();
    if (!resolved) return;
    const nextSettings = resolved.settings;
    const checked = validateReferencesForSettings(nextSettings, resolved.config);
    if (!checked) return;
    let statefulAction = {};
    try {
      statefulAction = statefulGenerationPayload(newClientActionId());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      return;
    }
    setPendingDirectVideoGeneration(null);
    setSettings(nextSettings);
    storeSettings(id, nextSettings);
    const sentReferences = consumeComposerReferenceAssets();
    setDraft("");
    setError("");
    const localUserId = `direct-video-user-${Date.now()}`;
    const localAssistantId = `direct-video-${Date.now()}`;
    let nextMessagesSnapshot = null;
    setState((current) => ({
      ...current,
      busy: true,
      messages: (nextMessagesSnapshot = [
        ...current.messages,
        { id: localUserId, role: "user", text: prompt, created_at: Date.now(), referenceAttachments: sentReferences },
        {
          id: localAssistantId,
          role: "assistant",
          text: `正在通过 ${nextSettings.agentVideoAlias || nextSettings.model || "视频 API"} 生成 ${nextSettings.aspect || "9:16"} 视频。`,
          created_at: Date.now() + 1,
          videoPlaceholder: true,
          localAgentVideoPlaceholder: true,
          progressLabel: "0%",
          aspect: nextSettings.aspect || "9:16",
        },
      ]),
    }));
    if (nextMessagesSnapshot) queueDirectHistorySave(nextMessagesSnapshot, 0);
    directVideoAbortController = new AbortController();
    try {
      let completed = null;
      let failed = false;
      await props.generateVideo?.(prompt, sentReferences, {
        title: "Direct video generation",
        aspect: nextSettings.aspect || "9:16",
        duration: Number(nextSettings.duration) || 4,
        count: Number(nextSettings.count) || 1,
        provider: nextSettings.provider || "",
        model: nextSettings.model || "",
        agentVideoAlias: nextSettings.agentVideoAlias || "",
        settingsScope: isAgent() ? "videos_agent" : "video_api",
        referenceMode: checked.capability.referenceMode,
        promptBuilderRequestId: lastAppliedPromptBuilder()?.prompt === prompt ? lastAppliedPromptBuilder()?.requestId : "",
        promptBuilderAppliedPath: lastAppliedPromptBuilder()?.prompt === prompt ? lastAppliedPromptBuilder()?.appliedPath : "",
        clientActionId: statefulAction.clientActionId || "",
        operation: statefulAction.operation || "",
        stateful: statefulAction.stateful === true,
        videoThreadId: statefulAction.videoThreadId || "",
        parentTurnId: statefulAction.parentTurnId || "",
        sourceVideoAssetId: statefulAction.sourceVideoAssetId || "",
        signal: directVideoAbortController.signal,
      }, (event) => {
        if (event?.type === "heartbeat") {
          const elapsed = Number(event.elapsed_seconds || 0);
          const percent = Math.min(AGENT_VIDEO_PROGRESS_MAX, Math.max(8, Math.round(8 + elapsed * 2)));
          upsertLocalMessage(localAssistantId, { progressLabel: `${percent}%` });
        }
        if (event?.type === "failed") {
          failed = true;
          const detail = String(event.detail?.message || event.detail || "视频生成失败。");
          upsertLocalMessage(localAssistantId, { failed: true, videoPlaceholder: false, progressLabel: "Failed", text: detail });
        }
        if (event?.type === "completed") {
          completed = event;
          if (event.video_thread_id) {
            setVideoInteraction((current) => ({
              ...current,
              video_thread_id: event.video_thread_id,
              head_turn_id: event.video_turn_id || current.head_turn_id,
            }));
          }
          const assets = Array.isArray(event.assets) && event.assets.length ? event.assets : [event.asset].filter(Boolean);
          const asset = assets[0] || {};
          const completedText = assets.length > 1
            ? `已经生成并保存到视频素材：${assets.length} 个视频`
            : `已经生成并保存到视频素材：${asset.filename || asset.path || "智能体生成视频"}`;
          upsertLocalMessage(localAssistantId, {
            videoPlaceholder: false,
            localAgentVideoPlaceholder: false,
            progressLabel: "",
            text: completedText,
            videoUrl: assetKey(asset) ? mediaUrl(asset) : "",
            path: asset.path || "",
            filename: asset.filename || "",
            aspect: event.aspect || asset.aspect || nextSettings.aspect,
            video_thread_id: event.video_thread_id || "",
            video_turn_id: event.video_turn_id || "",
            parent_turn_id: event.parent_turn_id || "",
          });
        }
      });
      if (completed?.video_thread_id) await refreshVideoInteraction();
      if (!completed && !failed) {
        upsertLocalMessage(localAssistantId, { videoPlaceholder: false, progressLabel: "", text: "视频生成完成。" });
      }
    } catch (err) {
      const detail = err instanceof Error ? err.message : String(err);
      const aborted = err?.name === "AbortError" || directVideoAbortController?.signal?.aborted;
      upsertLocalMessage(localAssistantId, {
        failed: !aborted,
        videoPlaceholder: false,
        progressLabel: aborted ? "Stopped" : "Failed",
        text: aborted ? "本地已停止视频生成，provider 任务可能仍会在后台完成。" : detail,
      });
      if (!aborted) setError(detail);
    } finally {
      directVideoAbortController = null;
      setState((current) => ({ ...current, busy: false }));
    }
  }

  async function requestDirectVideoGeneration(message) {
    const prompt = text(message);
    if (!prompt || busy()) return;
    if (settings().confirmBeforeGenerate !== false) {
      const resolved = await resolveSelectedVideoSettings();
      if (!resolved) return;
      const checked = validateReferencesForSettings(resolved.settings, resolved.config);
      if (!checked) return;
      setSettings(resolved.settings);
      storeSettings(taskId(), resolved.settings);
      setPendingDirectVideoGeneration({ prompt, resolved });
      return;
    }
    await sendDirectVideoGeneration(prompt);
  }

  async function confirmDirectVideoGeneration() {
    const pending = pendingDirectVideoGeneration();
    if (!pending || busy()) return;
    await sendDirectVideoGeneration(pending.prompt, pending.resolved);
  }

  function cancelDirectVideoGeneration() {
    setPendingDirectVideoGeneration(null);
  }

  async function confirmAgentVideoGeneration() {
    const pending = pendingAgentVideoGeneration();
    if (!pending || busy()) return;
    await sendMessage({ message: pending.prompt, confirmed: true });
  }

  function cancelAgentVideoGeneration() {
    setPendingAgentVideoGeneration(null);
  }

  async function saveSettings() {
    if (settingsSaving()) return;
    const id = taskId();
    const saver = isAgent() ? props.saveVideosAgentSettings : props.saveVideoAPISettings;
    setSettingsSaving(true);
    setSettingsPanelError("");
    try {
      if (saver) {
        const config = videoModelConfig() || await refreshVideoModelConfig(true);
        const selected = selectedOrDefaultAgentVideoModel(config);
        if (!selected) {
          throw new Error("请先在视频设置中选择一个视频模型。");
        }
        const nextSettings = coerceSettingsForCapability(selected ? {
          ...settings(),
          ...videoModelSelectionPayload(selected),
        } : {
          ...settings(),
          agentVideoAlias: "",
          provider: "",
          model: "",
        }, config || {});
        setSettings(nextSettings);
        if (id) storeSettings(id, nextSettings);
        const payload = await saver(isAgent() ? videosAgentSettingsPayload(nextSettings) : videoAPISettingsPayload(nextSettings));
        if (!payload?.prompt_template_snapshot) {
          try {
            await snapshotVideoPromptTemplate(nextSettings);
          } catch (err) {
            console.warn("Video prompt template snapshot failed after settings save", err);
          }
        }
        if (payload?.settings) setSettings((current) => ({ ...current, ...payload.settings }));
      } else if (id) {
        storeSettings(id, settings());
      }
      setSettingsOpen(false);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setSettingsPanelError(message);
      setError(message);
    } finally {
      setSettingsSaving(false);
    }
  }

  const renderReferenceSlots = () => (
    <Show when={referenceSlotGroups().length}>
      <div class="ual-video-reference-slots" aria-label="视频参考素材槽">
        <For each={referenceSlotGroups()}>{(group) => (
          <section class={`ual-video-reference-slot-group is-${group.key} ${group.required ? "is-required" : ""}`}>
            <header>
              <span><FlowIcon name={group.icon} />{group.title}</span>
              <small>{group.items.length}/{group.limit.max}</small>
            </header>
            <div class="ual-video-reference-slot-list">
              <For each={group.items}>{(asset) => (
                <figure class="ual-video-reference-slot is-filled">
                  <Show when={assetKind(asset) === "image"} fallback={<div class="ual-video-reference-slot-media"><FlowIcon name={group.icon} /></div>}>
                    <img src={referenceThumbnailUrl(asset)} alt="" loading="lazy" />
                  </Show>
                  <figcaption>{shortPathLabel(asset?.label || asset?.filename || asset?.path || asset?.id)}</figcaption>
                  <button type="button" aria-label="移除参考素材" onClick={() => removeReferenceAsset(asset)}><FlowIcon name="close" /></button>
                </figure>
              )}</For>
              <Show when={group.remaining > 0}>
                <button type="button" class="ual-video-reference-slot is-empty" disabled={busy()} onClick={() => openReferenceFilePicker(group.key)}>
                  <FlowIcon name="add" />
                  <span>{group.required && !group.items.length ? "必填" : `+${group.remaining}`}</span>
                </button>
              </Show>
            </div>
          </section>
        )}</For>
      </div>
    </Show>
  );

  const renderConsistencyPicker = () => (
    <Show when={consistencyPickerOpen()}>
      <section class="ual-consistency-picker" role="dialog" aria-label="一致性参考图选择器">
        <header class="is-title-only">
          <strong>参考图</strong>
          <button type="button" aria-label="关闭一致性参考图选择器" onClick={() => setConsistencyPickerOpen(false)}><FlowIcon name="close" /></button>
        </header>
        <Show when={!consistencyLoading()} fallback={<p class="ual-consistency-empty">加载中...</p>}>
          <Show when={consistencyReferences().length} fallback={<p class="ual-consistency-empty">当前会话还没有可用的一致性参考图。</p>}>
            <div class="ual-consistency-grid">
              <For each={consistencyReferences()}>{(item) => {
                const selected = () => selectedConsistencyKeys().has(item.key);
                return (
                  <button type="button" class={`ual-consistency-option ${selected() ? "is-selected" : ""}`} aria-pressed={selected()} onClick={() => toggleConsistencyReference(item)}>
                    <img src={referenceThumbnailUrl(item)} alt="" onError={() => dropMissingConsistencyReference(item)} />
                    <span>{item.label}</span>
                  </button>
                );
              }}</For>
            </div>
            <div class="ual-consistency-actions">
              <button type="button" onClick={() => setConsistencyPickerOpen(false)}>取消</button>
              <button type="button" class="is-primary" disabled={!selectedConsistencyCount()} onClick={confirmConsistencyReferences}>确认加载</button>
            </div>
          </Show>
        </Show>
      </section>
    </Show>
  );

  const updateSetting = (key, value) => {
    setSettingsPanelError("");
    setSettings((current) => ({ ...current, [key]: value }));
  };
  function videoAPISettingsPayload(source = settings()) {
    const agentVideoAlias = source.agentVideoAlias || "";
    return {
      confirmBeforeGenerate: source.confirmBeforeGenerate !== false,
      aspect: source.aspect || "9:16",
      duration: Number(source.duration) || 4,
      count: Number(source.count) || 1,
      referenceMode: source.referenceMode || "selected_images",
      agentVideoAlias,
      provider: agentVideoAlias ? "" : (source.provider || ""),
      model: agentVideoAlias ? "" : (source.model || ""),
    };
  }
  function videosAgentSettingsPayload(source = settings()) {
    return {
      ...videoAPISettingsPayload(source),
      chatProvider: source.chatProvider || "",
      chatModel: source.chatModel || "",
    };
  }
  function normalizeSettingsForVideoAgentPool(config) {
    setSettings((previous) => {
      const aliases = videoAgentModelsFor(config || {});
      if (!aliases.length) return { ...previous, agentVideoAlias: "", provider: "", model: "" };
      const selected = selectedOrDefaultAgentVideoModel(config, previous);
      return coerceSettingsForCapability({
        ...previous,
        ...videoModelSelectionPayload(selected),
      }, config || {});
    });
  }
  const selectVideoModel = (item) => {
    setSettingsPanelError("");
    setSettings((current) => coerceSettingsForCapability({
      ...current,
      ...videoModelSelectionPayload(item),
    }, videoModelConfig() || {}));
  };
  const selectAgentModel = (item) => {
    setSettingsPanelError("");
    setSelectedModelKey(modelKey(item));
    setSettings((current) => ({
      ...current,
      chatProvider: item.providerID || "",
      chatModel: item.modelID || "",
    }));
  };
  const updateDurationInput = (value) => {
    updateSetting("duration", value);
  };
  const commitDurationInput = (value) => {
    const parsed = Number.parseInt(String(value || ""), 10);
    const limits = videoCapability().params.duration;
    const fallback = limits.presets[0] || limits.min || 4;
    updateSetting("duration", Number.isFinite(parsed) ? Math.max(limits.min || 1, Math.min(parsed, limits.max || 120)) : fallback);
  };
  const openSettings = () => {
    setSettingsPanelError("");
    setSettingsOpen(true);
    void loadRemoteSettings(false);
    void refreshVideoModelConfig(true);
  };

  const renderSettingsPanel = () => (
    <Show when={settingsOpen()}>
      <section class="ual-agent-settings-panel" aria-label="视频生成设置">
        <header>
          <button type="button" class="ual-agent-settings-icon" aria-label="返回" onClick={() => setSettingsOpen(false)}><FlowIcon name="arrowBack" /></button>
          <strong>{isAgent() ? "智能体设置" : "视频设置"}</strong>
          <button type="button" class="ual-agent-settings-icon" aria-label="关闭设置" onClick={() => setSettingsOpen(false)}><FlowIcon name="close" /></button>
        </header>
        <div class="ual-agent-settings-body">
          <Show when={settingsPanelError()}>
            <div class="ual-agent-settings-error" role="alert">{settingsPanelError()}</div>
          </Show>
          <section class="ual-setting-group">
            <span class="ual-setting-label">生成前确认</span>
            <label class="ual-setting-radio">
              <input type="radio" name="ual-video-confirm" checked={settings().confirmBeforeGenerate !== false} onChange={() => updateSetting("confirmBeforeGenerate", true)} />
              <span><strong>每次确认</strong><small>生成媒体前先询问确认。</small></span>
            </label>
            <label class="ual-setting-radio">
              <input type="radio" name="ual-video-confirm" checked={settings().confirmBeforeGenerate === false} onChange={() => updateSetting("confirmBeforeGenerate", false)} />
              <span><strong>不确认</strong><small>直接自动生成媒体。</small></span>
            </label>
          </section>
          <section class="ual-setting-group ual-video-setting-panel">
            <div class="ual-setting-segment is-video-aspect" aria-label="画幅">
              <For each={["9:16", "16:9"]}>{(item) => (
                <button type="button" class={settings().aspect === item ? "is-active" : ""} onClick={() => updateSetting("aspect", item)}>
                  <span class={`ual-aspect-icon is-${item.replace(":", "-")}`} />
                  {item}
                </button>
              )}</For>
            </div>
            <div class="ual-setting-duration-row" aria-label="时长">
              <div class="ual-setting-segment">
                <For each={videoCapability().params.duration.presets.length ? videoCapability().params.duration.presets : [4, 8, 15].filter((item) => item >= videoCapability().params.duration.min && item <= videoCapability().params.duration.max)}>{(item) => (
                  <button type="button" class={Number(settings().duration) === item ? "is-active" : ""} onClick={() => updateSetting("duration", item)}>{item}s</button>
                )}</For>
              </div>
              <label class="ual-setting-number">
                <input
                  type="number"
                  min={videoCapability().params.duration.min || 1}
                  max={videoCapability().params.duration.max || 120}
                  step="1"
                  value={settings().duration || ""}
                  aria-label="自定义时长（秒）"
                  onInput={(event) => updateDurationInput(event.currentTarget.value)}
                  onBlur={(event) => commitDurationInput(event.currentTarget.value)}
                />
                <span>s</span>
              </label>
            </div>
            <Show when={videoCapability().params.count.enabled}>
              <div class="ual-setting-segment is-count" aria-label="数量">
                <For each={videoCapability().params.count.values}>{(item) => (
                  <button type="button" class={Number(settings().count) === item ? "is-active" : ""} onClick={() => updateSetting("count", item)}>x{item}</button>
                )}</For>
              </div>
            </Show>
            <Show when={videoAgentModels().length} fallback={<div class="ual-setting-select is-empty">请先配置视频模型</div>}>
              <div class="ual-setting-model-box">
                <div class="ual-setting-model-provider">
                  <span>视频模型</span>
                  <small>{videoAgentModels().length} 个模型</small>
                </div>
                <div class="ual-setting-model-options">
                  <For each={videoAgentModels()}>{(model) => (
                    <button
                      type="button"
                      class={selectedVideoModelKey() === videoModelKey(model) ? "is-active" : ""}
                      title={model.alias || "Video model"}
                      onClick={() => selectVideoModel(model)}
                    >
                      {videoModelLabel(model)}
                    </button>
                  )}</For>
                </div>
              </div>
            </Show>
            <Show when={isAgent()}>
              <Show when={modelItems().length} fallback={<div class="ual-setting-select is-empty">没有可用的 OpenCode Agent 模型</div>}>
                <div class="ual-setting-model-box">
                  <div class="ual-setting-model-provider">
                    <span>智能体模型</span>
                    <small>{modelItems().length} 个模型</small>
                  </div>
                  <div class="ual-setting-model-options">
                    <For each={modelItems()}>{(model) => (
                      <button
                        type="button"
                        class={selectedSettingsModelKey() === modelKey(model) ? "is-active" : ""}
                        title={`${text(model.providerName || model.providerID)} / ${text(model.modelName || model.modelID)}`}
                        onClick={() => selectAgentModel(model)}
                      >
                        {modelLabel(model)}
                      </button>
                    )}</For>
                  </div>
                </div>
              </Show>
            </Show>
          </section>
        </div>
        <footer>
          <button
            type="button"
            disabled={settingsSaving()}
            aria-busy={settingsSaving() ? "true" : "false"}
            onClick={() => void saveSettings()}
          >
            {settingsSaving() ? "保存中..." : "完成"}
          </button>
        </footer>
      </section>
    </Show>
  );

  const resolveReferenceDisplayItems = (items = []) => {
    const assets = new Map(referenceAssets().map((asset) => [text(asset?.path || asset?.id), asset]));
    return (items || []).map((item) => {
      const path = text(item?.path || item?.id);
      const asset = assets.get(path) || item;
      return {
        ...item,
        path,
        label: text(item?.label || item?.filename || asset?.label || asset?.filename || shortPathLabel(path)),
        role: normalizeReferenceRole(item?.role || item?.reference_role || asset?.role || asset?.reference_role) || "REFERENCE_IMAGE",
        imageUrl: path ? referenceUrl(asset) : "",
        thumbnailUrl: path ? referenceThumbnailUrl(asset) : "",
      };
    });
  };
  const toggleThinking = (id) => {
    setExpandedThinkingByMessageId((current) => ({ ...current, [id]: !current[id] }));
  };
  const toggleResultVote = (id, vote) => {
    setResultActionState((current) => {
      const previous = current[id] || {};
      return { ...current, [id]: { ...previous, vote: previous.vote === vote ? "" : vote } };
    });
  };
  const markResultReported = (id) => {
    setResultActionState((current) => ({ ...current, [id]: { ...(current[id] || {}), reported: true } }));
  };
  const copyText = async (value) => {
    const content = String(value || "").trim();
    if (!content) return false;
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(content);
      return true;
    }
    const textarea = document.createElement("textarea");
    textarea.value = content;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    const ok = document.execCommand("copy");
    textarea.remove();
    return ok;
  };
  const copyResultPath = async (id, value) => {
    try {
      const ok = await copyText(value);
      if (!ok) return;
      setCopiedResultId(id);
      window.setTimeout(() => setCopiedResultId((current) => current === id ? "" : current), 1400);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };
  const resultFilename = (message) => text(message.filename || shortPathLabel(message.path || message.videoUrl) || "智能体生成视频");
  const resultAspectClass = (message) => {
    const id = messageId(message) || `video-result-${messageTime(message)}`;
    const aspect = text(message.aspect || message.aspect_ratio || message.aspectRatio) || resultVideoAspectById()[id];
    if (aspect === "16:9") return "is-aspect-16-9";
    if (aspect === "9:16") return "is-aspect-9-16";
    return "";
  };
  const handleResultVideoMetadata = (message, event) => {
    const video = event.currentTarget;
    const id = messageId(message) || `video-result-${messageTime(message)}`;
    const width = Number(video.videoWidth || 0);
    const height = Number(video.videoHeight || 0);
    if (width > 0 && height > 0) {
      const nextAspect = width >= height ? "16:9" : "9:16";
      setResultVideoAspectById((current) => current[id] === nextAspect ? current : { ...current, [id]: nextAspect });
    }
    scrollMessagesToBottom();
  };
  const renderReferenceStrip = (items, variant = "") => {
    const refs = resolveReferenceDisplayItems(items).slice(0, 5);
    return <Show when={refs.length}>
      <div class={`ual-message-reference-strip ${variant}`}>
        <For each={refs}>{(item, index) => (
          <figure class="ual-message-reference" title={`${referenceRoleLabel(item.role)} · ${item.path}`}>
            <Show when={item.thumbnailUrl || item.imageUrl} fallback={<span>{shortPathLabel(item.label).slice(0, 2).toUpperCase()}</span>}>
              <img src={item.thumbnailUrl || item.imageUrl} alt="" onLoad={scrollMessagesToBottom} />
            </Show>
            <Show when={variant !== "is-compact"}>
              <figcaption>{referenceRoleLabel(item.role)} · {shortPathLabel(item.label || item.path)}</figcaption>
            </Show>
            <Show when={variant === "is-compact" && index() === 4 && items.length > 5}>
              <b>+{items.length - 5}</b>
            </Show>
          </figure>
        )}</For>
      </div>
    </Show>;
  };
  const renderThinking = (message, parsed) => {
    const id = messageId(message) || `${messageRole(message)}-${messageTime(message)}`;
    const expanded = () => Boolean(expandedThinkingByMessageId()[id]);
    return <Show when={parsed.thinking}>
      <section class={`ual-message-thinking ${expanded() ? "is-open" : ""}`}>
        <button type="button" onClick={() => toggleThinking(id)} aria-expanded={expanded()}>
          <span>{expanded() ? "Hide thinking" : "Show thinking"}</span>
          <FlowIcon name="arrowForward" />
        </button>
        <Show when={expanded()}>
          <div>{parsed.thinking}</div>
        </Show>
      </section>
    </Show>;
  };
  const renderDebugDetails = (parsed) => <Show when={parsed.debug}>
    <details class="ual-message-debug">
      <summary>详情</summary>
      <pre><code>{formatDebugText(parsed.debug)}</code></pre>
    </details>
  </Show>;
  const renderResultActions = (message) => {
    const id = messageId(message) || `video-result-${messageTime(message)}`;
    const current = () => resultActionState()[id] || {};
    const copyValue = message.path || resultFilename(message);
    return <div class="ual-result-actions" aria-label="视频结果操作">
      <button type="button" class={current().vote === "like" ? "is-active" : ""} title="喜欢" aria-label="喜欢" onClick={() => toggleResultVote(id, "like")}><FlowIcon name="thumbUp" /></button>
      <button type="button" class={current().vote === "dislike" ? "is-active" : ""} title="不喜欢" aria-label="不喜欢" onClick={() => toggleResultVote(id, "dislike")}><FlowIcon name="thumbDown" /></button>
      <button type="button" class={copiedResultId() === id ? "is-active" : ""} title={copiedResultId() === id ? "已复制" : "复制"} aria-label="复制" onClick={() => void copyResultPath(id, copyValue)}><FlowIcon name="contentCopy" /></button>
      <button type="button" class={current().reported ? "is-active" : ""} title="反馈问题" aria-label="反馈问题" onClick={() => markResultReported(id)}><FlowIcon name="flag" /></button>
    </div>;
  };
  const renderResultCard = (message) => <Show when={message.videoUrl}>
    <section class={`ual-result-card is-video ${resultAspectClass(message)}`}>
      <video class="ual-message-video" src={message.videoUrl} poster={message.path ? props.thumbnailUrl?.({ ...(message.asset || {}), path: message.path }) : ""} controls preload="none" playsInline onLoadedMetadata={(event) => handleResultVideoMetadata(message, event)} />
      <div class="ual-result-meta">
        <strong>已保存到视频素材</strong>
        <span>{resultFilename(message)}</span>
      </div>
      {renderResultActions(message)}
    </section>
  </Show>;
  const renderFormattedText = (value) => {
    const blocks = richTextBlocks(value);
    return <div class="ual-message-rich-text">
      <For each={blocks}>{(block) => (
        <Show when={block.type === "code"} fallback={<p>{block.text}</p>}>
          <pre><Show when={block.language}><small>{block.language}</small></Show><code>{block.text}</code></pre>
        </Show>
      )}</For>
    </div>;
  };
  const renderUserText = (value) => {
    const body = normalizeTextWhitespace(value);
    return <Show when={body}>
      <Show when={shouldCollapseUserText(body)} fallback={<p class="ual-user-message-text">{body}</p>}>
        <details class="ual-user-message-collapsible">
          <summary>
            <span>{body.slice(0, 180)}{body.length > 180 ? "..." : ""}</span>
            <b>展开</b>
          </summary>
          <p class="ual-user-message-text">{body}</p>
        </details>
      </Show>
    </Show>;
  };
  const renderVideoMessage = (message) => {
    const role = messageRole(message);
    const parsed = parseVideoAgentDisplay(message, state().partsByMessageId);
    const references = role === "user" ? (message.referenceAttachments || parsed.references) : parsed.references;
    const messageCandidates = role === "assistant" ? extractAgentCandidates(message, state().partsByMessageId) : [];
    const visible = Boolean(message.videoPlaceholder || message.videoUrl || parsed.text || parsed.thinking || parsed.debug || references.length || messageCandidates.length);
    const streamingText = role !== "user" && busy() && parsed.text && !message?.info?.time?.completed && !message.videoUrl && !message.failed;
    return <Show when={visible}>
      <article class={`ual-message is-${role}`}>
        <Show when={role === "user"}>
          <div class="ual-user-bubble">
            {renderReferenceStrip(references, "is-compact")}
            {renderUserText(parsed.text)}
          </div>
        </Show>
        <Show when={role !== "user"}>
          {renderThinking(message, parsed)}
          {renderReferenceStrip(references)}
          <Show when={message.videoPlaceholder}>
            <div class={`ual-message-image-placeholder is-${shapeFromAspect(message.aspect || settings().aspect)} ${message.failed ? "is-failed" : ""}`}>
              <span>{displayProgressLabel(message.progressLabel)}</span>
            </div>
          </Show>
          {renderResultCard(message)}
          <Show when={parsed.text}>
            <div class={`ual-assistant-bubble ${streamingText ? "is-streaming" : ""}`}>
              {renderFormattedText(parsed.text)}
            </div>
          </Show>
          {renderDebugDetails(parsed)}
          <Show when={messageCandidates.length}>
            <div class="ual-prompt-candidates">
              <For each={messageCandidates}>{(candidate) => props.renderCandidate
                ? props.renderCandidate(candidate, { setError })
                : <section class="ual-prompt-candidate"><header><strong>{candidate.title}</strong></header><p>{JSON.stringify(candidate.payload)}</p></section>}</For>
            </div>
          </Show>
        </Show>
      </article>
    </Show>;
  };

  const renderStatefulVideoPanel = () => <Show when={statefulVideoEnabled()}>
    <section class="ual-video-stateful" aria-label="有状态视频版本">
      <header>
        <div>
          <strong>有状态视频编辑</strong>
          <small>{videoInteractionLoading() ? "正在恢复版本..." : (videoInteraction()?.head_turn_id ? `当前版本 ${videoInteractionTurns().length}` : "尚未创建版本")}</small>
        </div>
        <Show when={videoInteraction()?.video_thread_id}>
          <button type="button" class="is-danger" disabled={busy() || videoInteractionDeleteBusy()} onClick={() => void deleteVideoCloudContext()}>
            {videoInteractionDeleteBusy() ? "清理中..." : "清除云端上下文"}
          </button>
        </Show>
      </header>
      <p class="ual-video-stateful-notice">每次生成或继续编辑都会产生一次新的付费调用。云端会保存编辑上下文；本地 MP4 始终独立保留。输出保留供应商强制且不可移除的来源水印。</p>
      <div class="ual-video-stateful-actions" role="group" aria-label="编辑方式">
        <button type="button" class={statefulOperation() === "generate" ? "is-active" : ""} disabled={busy()} onClick={() => chooseStatefulOperation("generate")}>新建视频</button>
        <button type="button" class={statefulOperation() === "edit" ? "is-active" : ""} disabled={busy()} onClick={() => chooseStatefulOperation("edit")}>上传视频编辑</button>
        <button type="button" class={statefulOperation() === "continue" ? "is-active" : ""} disabled={busy() || !videoInteraction()?.head_turn_id} onClick={() => chooseStatefulOperation("continue")}>继续编辑</button>
      </div>
      <Show when={videoInteractionTurns().length}>
        <div class="ual-video-version-tree" aria-label="视频版本树">
          <For each={videoInteractionTurns()}>{(turn, index) => (
            <article class={`${text(turn.video_turn_id) === text(videoInteraction()?.head_turn_id) ? "is-head" : ""} ${text(turn.video_turn_id) === text(selectedParentTurnId()) ? "is-selected" : ""}`}>
              <button
                type="button"
                disabled={busy() || turn.status !== "completed"}
                onClick={() => chooseStatefulOperation("continue", turn.video_turn_id)}
              >
                <span>版本 {index() + 1}</span>
                <small>{turn.status === "pending" ? "生成中" : turn.provider_state_status === "expired" ? "上下文已过期" : turn.provider_state_status === "deleted" ? "云端已删除" : turn.status}</small>
              </button>
              <Show when={["expired", "deleted"].includes(text(turn.provider_state_status)) && turn.output_path}>
                <button type="button" disabled={busy()} onClick={() => restartExpiredTurnFromLocal(turn)}>从本地视频新建链</button>
              </Show>
            </article>
          )}</For>
        </div>
      </Show>
    </section>
  </Show>;

  return <aside class={`ual-agent ual-video-agent ${isAgent() ? "is-opencode-workspace" : ""}`}>
    {renderSettingsPanel()}
    <PromptBuilderModal
      open={promptBuilderOpen}
      loading={promptBuilderLoading}
      error={promptBuilderError}
      builder={promptBuilderPayload}
      currentDraft={draft}
      onClose={() => setPromptBuilderOpen(false)}
      onApply={(payload) => applyPromptBuilder(payload)}
    />
    <Show when={isAgent()} fallback={
      <>
        <header class="ual-agent-header">
          <div class="ual-agent-title">
            <button type="button" class="ual-agent-icon" aria-label="菜单"><FlowIcon name="menu" /></button>
            <strong>工作区</strong>
          </div>
          <div class="ual-agent-actions">
            <button type="button" class="ual-agent-icon" aria-label="设置" title="设置" onClick={openSettings}><FlowIcon name="tune" /></button>
            <button type="button" class="ual-agent-icon" aria-label="关闭" onClick={() => props.onClose?.()}><FlowIcon name="close" /></button>
          </div>
        </header>
        {renderStatefulVideoPanel()}
        <section ref={(el) => { messagesScrollEl = el; }} class="ual-agent-chat">
          {renderMessages()}
        </section>
        {renderComposer("ual-agent-composer", "描述你想生成的视频，选中的图片会作为参考图传入", "发送")}
      </>
    }>
      <section class="ual-opencode-agent is-workspace" role="dialog" aria-label="视频智能体">
        <header class="ual-agent-header">
          <div class="ual-agent-title">
            <button type="button" class="ual-agent-icon" aria-label="菜单"><FlowIcon name="menu" /></button>
            <strong>工作区</strong>
          </div>
          <button type="button" class="ual-agent-icon" aria-label="关闭" onClick={() => props.onClose?.()}><FlowIcon name="close" /></button>
        </header>
        {renderStatefulVideoPanel()}
        <section ref={(el) => { messagesScrollEl = el; }} class="ual-opencode-agent-chat">
          {renderMessages()}
        </section>
        {renderComposer("ual-opencode-agent-composer", "描述你想生成的视频，或询问当前视频素材如何整理、使用、绑定", "发送到视频智能体")}
      </section>
    </Show>
  </aside>;

  function renderMessages() {
    return <>
      <Show when={error() || state().error}>
        <article class="ual-message is-assistant is-error"><p>{error() || state().error}</p></article>
      </Show>
      <For each={messages()}>{(message) => renderVideoMessage(message)}</For>
      <div ref={(el) => { messagesBottomEl = el; }} class="ual-chat-bottom-sentinel" aria-hidden="true" />
    </>;
  }

  function renderComposer(className, placeholder, ariaLabel) {
    return <footer class={className}>
      <Show when={!isAgent() && pendingDirectVideoGeneration()}>
        {(pending) => <div class="ual-agent-generate-confirm" role="alertdialog" aria-label="确认生成视频">
          <div class="ual-agent-generate-confirm-head">
            <strong>{statefulVideoEnabled() ? "确认新的付费视频生成？" : "确认生成视频？"}</strong>
            <div>
              <button type="button" onClick={cancelDirectVideoGeneration}>取消</button>
              <button type="button" class="is-primary" onClick={() => void confirmDirectVideoGeneration()}>生成</button>
            </div>
          </div>
          <p>{pending().prompt}</p>
          <Show when={statefulVideoEnabled()}><p>操作：{statefulOperation() === "continue" ? "继续编辑当前版本" : statefulOperation() === "edit" ? "以上传视频新建编辑链" : "新建视频链"}。每次确认都会单独计费。</p></Show>
        </div>}
      </Show>
      <Show when={isAgent() && pendingAgentVideoGeneration()}>
        {(pending) => <div class="ual-agent-generate-confirm" role="alertdialog" aria-label="确认生成视频">
          <div class="ual-agent-generate-confirm-head">
            <strong>{statefulVideoEnabled() ? "确认新的付费视频生成？" : "确认生成视频？"}</strong>
            <div>
              <button type="button" onClick={cancelAgentVideoGeneration}>取消</button>
              <button type="button" class="is-primary" onClick={() => void confirmAgentVideoGeneration()}>生成</button>
            </div>
          </div>
          <p>{pending().prompt}</p>
          <Show when={statefulVideoEnabled()}><p>将关联当前 OpenCrew 视频版本；供应商状态标识不会发送到浏览器或模型。</p></Show>
        </div>}
      </Show>
      <div
        class={`ual-composer-box ${referenceDragging() ? "is-reference-dragging" : ""}`}
        tabIndex="0"
        onDragEnter={handleReferenceDrag}
        onDragOver={handleReferenceDrag}
        onDragLeave={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget)) setReferenceDragging(false);
        }}
        onDrop={(event) => void handleReferenceDrop(event)}
        onPointerDown={(event) => {
        if (event.target === event.currentTarget) composerTextarea?.focus?.();
      }}>
        {renderReferenceSlots()}
	        <textarea
	          ref={(el) => { composerTextarea = el; }}
	          value={draft()}
	          disabled={busy()}
	          placeholder={busy() ? (isAgent() ? "等待智能体响应..." : "正在生成视频...") : placeholder}
	          onFocus={requestDirectHistoryLoad}
	          onInput={(event) => setDraft(event.currentTarget.value)}
          onKeyDown={(event) => {
            if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
              event.preventDefault();
              void sendMessage();
            }
          }}
        />
        <div class="ual-composer-tools">
          <input ref={(el) => { referenceFileInput = el; }} type="file" multiple accept={referenceFileAccept()} class="ual-hidden-file-input" onChange={(event) => void chooseReferenceFiles(event)} />
          <button type="button" class="ual-composer-icon is-plus" aria-label="添加参考素材" disabled={busy() || (!props.uploadImageFiles && !props.uploadMediaFiles)} title="上传参考素材" onClick={() => openReferenceFilePicker("all")}><FlowIcon name="add" /></button>
          <div>
            <button type="button" class="ual-composer-icon" aria-label="加载一致性参考图" title="加载人物/产品一致性参考图" disabled={busy()} onClick={() => void openConsistencyPicker()}><FlowIcon name="image" /></button>
            <button type="button" class="ual-composer-icon" aria-label="提示词构建器" title="提示词构建器" disabled={busy()} onClick={() => void openPromptBuilder()}><FlowIcon name="addNotes" /></button>
            <button type="button" class="ual-composer-icon" aria-label="设置" title="设置" onClick={openSettings}><FlowIcon name="tune" /></button>
            <Show when={busy()} fallback={
              <button type="button" class="ual-composer-submit" disabled={!draft().trim()} onClick={() => void sendMessage()} aria-label={ariaLabel}><FlowIcon name="arrowForward" /></button>
            }>
              <button type="button" class="ual-composer-submit" onClick={() => void abortChat()} aria-label="停止"><FlowIcon name="close" /></button>
            </Show>
          </div>
        </div>
      </div>
      {renderConsistencyPicker()}
    </footer>;
  }
}
