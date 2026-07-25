import { For, Show, createEffect, createMemo, createSignal, onCleanup, onMount } from "solid-js";
import { aspectFromPrompt, shapeFromAspect } from "../uploadAssetLibraryModel.js";
import FlowIcon from "./FlowIcon.jsx";
import ImageAPISettings from "./ImageAPISettings.jsx";
import ImagesAgentSettings from "./ImagesAgentSettings.jsx";
import PromptBuilderModal from "./PromptBuilderModal.jsx";

function text(value) {
  return String(value || "").trim();
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

function inferReferenceRole(asset = {}) {
  const value = [
    asset.path,
    asset.label,
    asset.filename,
    asset.key,
    asset.kind,
    asset.source,
  ].map(text).join(" ").toLowerCase();
  if (value.includes("host") || value.includes("person") || value.includes("character") || value.includes("人物")) return "HOST_REFERENCE";
  if (value.includes("product") || value.includes("prodcut") || value.includes("产品")) return "PRODUCT_REFERENCE";
  if (value.includes("target") || value.includes("frame")) return "TARGET_FRAME";
  return "";
}

function referenceRoleLabel(role) {
  if (role === "TARGET_FRAME") return "Target";
  if (role === "HOST_REFERENCE") return "Host";
  if (role === "PRODUCT_REFERENCE") return "Product";
  return "Ref";
}

function buildReferencePayload(assets = []) {
  const explicitTarget = assets.some((asset) => normalizeReferenceRole(asset?.reference_role || asset?.role) === "TARGET_FRAME");
  let targetAssigned = explicitTarget;
  return (assets || [])
    .map((asset) => {
      const path = text(asset?.path);
      if (!path) return null;
      let role = normalizeReferenceRole(asset?.reference_role || asset?.role);
      if (!role && !targetAssigned && asset?.source !== "session_consistency_reference") {
        role = "TARGET_FRAME";
        targetAssigned = true;
      }
      if (!role) role = inferReferenceRole(asset);
      return {
        path,
        role: role || "REFERENCE_IMAGE",
        label: text(asset?.label || asset?.filename || path.split("/").pop()),
      };
    })
    .filter(Boolean);
}

const IMAGE_WORKSPACE_HISTORY_LIMIT = 500;

function defaultImageWorkspaceMessages() {
  return [
    {
      id: "local-greeting",
      role: "assistant",
      text: "你好！我是你的创意助手。我可以帮你构思、生成图像。今天想做点什么？",
      suggestions: [
        "描述图像或视频，我来为你制作",
        "构思角色、场景或世界观并将其呈现",
        "逐场景创作故事并制作故事板",
        "将你的创意整理成收藏夹",
      ],
    },
  ];
}

export default function AgentPanel(props) {
  let referenceFileInput;
  let chatScrollEl;
  let chatBottomEl;
  let agentChatScrollEl;
  let agentChatBottomEl;
  let agentComposerTextarea;
  let composerTextarea;
  let chatScrollFrame = 0;
  let agentScrollFrame = 0;
  let directHistorySaveTimer = 0;
  let directHistorySaveSnapshot = null;
  let directHistoryInitializing = false;
  let agentChatSubmitInFlightKey = "";
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
  const [busy, setBusy] = createSignal("");
  const [settingsOpen, setSettingsOpen] = createSignal(false);
  const [settingsTarget, setSettingsTarget] = createSignal("image-api");
  const [settingsSaving, setSettingsSaving] = createSignal(false);
  const [promptBuilderOpen, setPromptBuilderOpen] = createSignal(false);
  const [promptBuilderLoading, setPromptBuilderLoading] = createSignal(false);
  const [promptBuilderPayload, setPromptBuilderPayload] = createSignal({});
  const [promptBuilderError, setPromptBuilderError] = createSignal("");
  const [promptBuilderTarget, setPromptBuilderTarget] = createSignal("default");
  const [lastAppliedPromptBuilder, setLastAppliedPromptBuilder] = createSignal(null);
  const [imageModelConfig, setImageModelConfig] = createSignal(null);
  const [agentOpen, setAgentOpen] = createSignal(false);
  const [agentDraft, setAgentDraft] = createSignal("");
  const [agentBusy, setAgentBusy] = createSignal("");
  const [agentInitialized, setAgentInitialized] = createSignal(false);
  const [chatSessionId, setChatSessionId] = createSignal("");
  const [chatPromptModels, setChatPromptModels] = createSignal({ items: [], default_model: { providerID: "", modelID: "" } });
  const [selectedChatModelKey, setSelectedChatModelKey] = createSignal("");
  const [agentMessages, setAgentMessages] = createSignal([]);
  const [partsByMessageId, setPartsByMessageId] = createSignal({});
  const [chatError, setChatError] = createSignal("");
  const [pendingImageGeneration, setPendingImageGeneration] = createSignal(null);
  const [pendingAgentGeneration, setPendingAgentGeneration] = createSignal(null);
  const [expandedThinkingByMessageId, setExpandedThinkingByMessageId] = createSignal({});
  const [resultActionState, setResultActionState] = createSignal({});
  const [copiedResultId, setCopiedResultId] = createSignal("");
  const [referenceDragging, setReferenceDragging] = createSignal(false);
  const [consistencyPickerOpen, setConsistencyPickerOpen] = createSignal(false);
  const [consistencyLoading, setConsistencyLoading] = createSignal(false);
  const [consistencyReferences, setConsistencyReferences] = createSignal([]);
  const [selectedConsistencyKeys, setSelectedConsistencyKeys] = createSignal(new Set());
  const [directHistoryLoadedFor, setDirectHistoryLoadedFor] = createSignal("");
  const [directHistoryReady, setDirectHistoryReady] = createSignal(false);
  const [settings, setSettings] = createSignal({
    confirmBeforeGenerate: true,
    aspect: "16:9",
    count: 1,
    agentImageAlias: "",
    provider: "",
    model: "",
    chatProvider: "",
    chatModel: "",
  });
  const taskId = () => Number(props.task?.()?.id || 0);
  const isOpenCodeWorkspace = () => props.mode === "opencode";
  const showOpenCodeEntry = () => props.showOpenCodeEntry !== false;
  const openCodeVisible = () => isOpenCodeWorkspace() || agentOpen();
  const [messages, setMessages] = createSignal(defaultImageWorkspaceMessages());
  const scrollChatToBottom = () => {
    if (!chatScrollEl || typeof window === "undefined") return;
    if (chatScrollFrame) window.cancelAnimationFrame(chatScrollFrame);
    chatScrollFrame = window.requestAnimationFrame(() => {
      chatScrollFrame = window.requestAnimationFrame(() => {
        if (chatBottomEl?.scrollIntoView) {
          chatBottomEl.scrollIntoView({ block: "end" });
        }
        if (chatScrollEl) {
          chatScrollEl.scrollTop = chatScrollEl.scrollHeight;
        }
      });
    });
  };
  const localDirectMessage = (message) => ({
    ...message,
    id: message?.id || `local-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    created_at: message?.created_at || Date.now(),
  });
  const addMessage = (message) => {
    setMessages((prev) => {
      const next = [...prev, localDirectMessage(message)];
      queueDirectImageHistorySave(next);
      return next;
    });
    scrollChatToBottom();
  };
  const updateMessage = (id, patch) => {
    setMessages((prev) => {
      const next = prev.map((message) => message.id === id ? { ...message, ...patch } : message);
      queueDirectImageHistorySave(next);
      return next;
    });
    scrollChatToBottom();
  };
  const messageId = (message) => String(message?.info?.id || message?.id || "");
  const messageRole = (message) => String(message?.info?.role || message?.role || "assistant");
  const messageTime = (message) => {
    const time = message?.info?.time || message?.time || {};
    return Number(time.created || time.completed || message?.created_at || 0) || 0;
  };
  const normalizeChatMessages = (items) => Array.from(items || [])
    .filter((item) => messageId(item))
    .sort((a, b) => messageTime(a) - messageTime(b));
  const addAgentLocalMessage = (message) => {
    setAgentMessages((current) => normalizeChatMessages([...current, message]));
    scrollAgentChatToBottom();
  };
  const updateAgentLocalMessage = (id, patch) => {
    setAgentMessages((current) => normalizeChatMessages(current.map((message) => messageId(message) === id ? { ...message, ...patch } : message)));
    scrollAgentChatToBottom();
  };
  const agentAssetOrigin = (asset = {}) => asset?.origin && typeof asset.origin === "object" ? asset.origin : {};
  const agentAssetGenerationId = (asset = {}) => text(asset.agent_generation_id || agentAssetOrigin(asset).agent_generation_id);
  const agentAssetMessageId = (asset = {}) => text(asset.agent_message_id || agentAssetOrigin(asset).agent_message_id);
  const agentAssetSessionId = (asset = {}) => text(asset.chat_opencode_session_id || agentAssetOrigin(asset).chat_opencode_session_id);
  const agentAssetRequestId = (asset = {}) => text(asset.request_id || agentAssetOrigin(asset).request_id);
  const agentAssetCreatedAt = (asset = {}) => Number(asset.created_at || agentAssetOrigin(asset).created_at || 0) || Date.now();
  const agentAssetGenerationIndex = (asset = {}) => Number(agentAssetOrigin(asset).generation_index || asset.generation_index || 0) || 0;
  const agentImageResultText = (assets = []) => {
    const first = assets[0] || {};
    return assets.length > 1
      ? `已经生成并保存到 Upload：${assets.length} 张图片`
      : `已经生成并保存到 Upload：${first.filename || first.path || "Agent generated image"}`;
  };
  const agentImageResultPatch = (assets = []) => {
    const sorted = assets.slice().sort((a, b) => agentAssetGenerationIndex(a) - agentAssetGenerationIndex(b) || agentAssetCreatedAt(a) - agentAssetCreatedAt(b));
    const asset = sorted[0] || {};
    return {
      imagePlaceholder: false,
      imageUrl: asset.path ? props.imageUrl?.(asset) : "",
      path: asset.path || "",
      asset,
      progressLabel: "",
      text: agentImageResultText(sorted),
    };
  };
  const collectAgentImageResultGroups = (availableAssets = [], sessionId = "") => {
    const groups = new Map();
    for (const asset of availableAssets || []) {
      const path = text(asset?.path);
      if (!path) continue;
      const origin = agentAssetOrigin(asset);
      const generationId = agentAssetGenerationId(asset);
      const messageId = agentAssetMessageId(asset);
      const requestId = agentAssetRequestId(asset);
      const source = text(asset?.source || origin.tool);
      if (source !== "agent_generated" && origin.tool !== "upload_asset_library_agent") continue;
      const assetSessionId = agentAssetSessionId(asset);
      if (!generationId && !messageId && !assetSessionId) continue;
      if (sessionId && assetSessionId && assetSessionId !== sessionId) continue;
      const key = generationId || requestId || messageId || path;
      const current = groups.get(key) || { key, generationId, messageId, requestId, assets: [] };
      current.assets.push(asset);
      if (!current.generationId && generationId) current.generationId = generationId;
      if (!current.messageId && messageId) current.messageId = messageId;
      if (!current.requestId && requestId) current.requestId = requestId;
      groups.set(key, current);
    }
    return Array.from(groups.values());
  };
  const mergeAgentImageResultIntoMessage = (message, group) => {
    const patch = agentImageResultPatch(group.assets);
    const parsedText = parseAgentDisplay(message).text;
    const next = {
      ...message,
      imagePlaceholder: false,
      imageUrl: patch.imageUrl,
      path: patch.path,
      asset: patch.asset,
      progressLabel: "",
    };
    if (!text(message.text) && !parsedText) next.text = patch.text;
    return next;
  };
  const upsertAgentLocalMessage = (id, patch, fallback = {}) => {
    if (!id) return;
    setAgentMessages((current) => {
      const exists = current.some((message) => messageId(message) === id);
      if (exists) {
        return normalizeChatMessages(current.map((message) => messageId(message) === id ? { ...message, ...patch } : message));
      }
      return normalizeChatMessages([...current, {
        id,
        role: "assistant",
        created_at: Date.now(),
        ...fallback,
        ...patch,
      }]);
    });
    scrollAgentChatToBottom();
  };
  const scrollAgentChatToBottom = () => {
    if (!agentChatScrollEl || typeof window === "undefined") return;
    if (agentScrollFrame) window.cancelAnimationFrame(agentScrollFrame);
    agentScrollFrame = window.requestAnimationFrame(() => {
      agentScrollFrame = window.requestAnimationFrame(() => {
        if (agentChatBottomEl?.scrollIntoView) {
          agentChatBottomEl.scrollIntoView({ block: "end" });
        }
        if (agentChatScrollEl) {
          agentChatScrollEl.scrollTop = agentChatScrollEl.scrollHeight;
        }
      });
    });
  };
  const messageParts = (message) => {
    const id = messageId(message);
    const stored = id ? Object.values(partsByMessageId()[id] || {}) : [];
    const parts = stored.length ? stored : (message?.parts || []);
    return parts.slice().sort((a, b) => String(a.id || "").localeCompare(String(b.id || "")));
  };
  const rawMessageText = (message) => {
    if (message?.text) return String(message.text);
    return messageParts(message)
      .filter((part) => String(part?.type || "text") === "text")
      .map((part) => String(part?.text || ""))
      .join("");
  };
  const shortPathLabel = (value) => {
    const clean = String(value || "").trim().replace(/^["']|["']$/g, "");
    return clean.split(/[\\/]/).filter(Boolean).pop() || clean;
  };
  const normalizeTextWhitespace = (value) => String(value || "")
    .split("\n")
    .map((line) => line.trimEnd())
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
  const directImageHistoryMessageForSave = (message) => {
    const role = messageRole(message);
    if (!["user", "assistant"].includes(role)) return null;
    const id = messageId(message);
    if (!id || id === "local-greeting") return null;
    const saved = {
      id,
      role,
      text: normalizeTextWhitespace(message.text || rawMessageText(message)),
      created_at: Number(message.created_at || messageTime(message) || Date.now()),
    };
    const path = text(message.path || message.asset?.path);
    if (path) {
      saved.path = path;
      saved.filename = text(message.filename || message.asset?.filename || shortPathLabel(path));
    }
    const aspect = text(message.aspect || message.aspectRatio || message.asset?.aspect || message.asset?.aspect_ratio);
    if (aspect) saved.aspect = aspect;
    if (message.imagePlaceholder && !message.imageUrl) {
      saved.imagePlaceholder = true;
      saved.progressLabel = text(message.progressLabel || "0%");
    }
    if (message.failed) {
      saved.failed = true;
      saved.progressLabel = text(message.progressLabel || "Failed");
    }
    if (!saved.text && !saved.path && !saved.imagePlaceholder && !saved.failed) return null;
    return saved;
  };
  const directImageHistoryMessagesForSave = (items = messages()) => (items || [])
    .map(directImageHistoryMessageForSave)
    .filter(Boolean)
    .slice(-IMAGE_WORKSPACE_HISTORY_LIMIT);
  const hydrateDirectImageHistoryMessage = (message) => {
    const path = text(message?.path);
    const filename = text(message?.filename || shortPathLabel(path));
    const imageAsset = path ? { id: path, path, filename, kind: "image", asset_type: "Image" } : null;
    const imageUrl = imageAsset ? props.imageUrl?.(imageAsset) || "" : "";
    return {
      ...message,
      filename: filename || message?.filename || "",
      imageUrl: message?.imageUrl || imageUrl,
      asset: message?.asset || imageAsset,
      aspectRatio: text(message?.aspectRatio || message?.aspect),
    };
  };
  const saveDirectImageHistorySnapshot = async (items) => {
    if (isOpenCodeWorkspace() || !props.saveImageAPIHistory) return;
    if (!taskId()) return;
    try {
      await props.saveImageAPIHistory({ messages: directImageHistoryMessagesForSave(items) });
    } catch (err) {
      setChatError(err instanceof Error ? err.message : String(err));
    }
  };
  const flushDirectImageHistorySave = () => {
    if (directHistorySaveTimer && typeof window !== "undefined") window.clearTimeout(directHistorySaveTimer);
    directHistorySaveTimer = 0;
    if (!directHistoryReady()) return;
    const snapshot = directHistorySaveSnapshot;
    directHistorySaveSnapshot = null;
    if (snapshot) void saveDirectImageHistorySnapshot(snapshot);
  };
  const queueDirectImageHistorySave = (items, delay = 500) => {
    if (isOpenCodeWorkspace()) return;
    if (!directHistoryReady() || directHistoryInitializing) {
      if (directImageHistoryMessagesForSave(items).length) directHistorySaveSnapshot = items;
      return;
    }
    directHistorySaveSnapshot = items;
    if (directHistorySaveTimer && typeof window !== "undefined") window.clearTimeout(directHistorySaveTimer);
    if (typeof window === "undefined" || delay <= 0) {
      flushDirectImageHistorySave();
      return;
    }
    directHistorySaveTimer = window.setTimeout(() => {
      flushDirectImageHistorySave();
    }, delay);
  };
  const initializeDirectImageHistory = async () => {
    const id = taskId();
    const marker = `${id}:image-api-workspace`;
    if (isOpenCodeWorkspace() || !id || directHistoryLoadedFor() === marker) return;
    setDirectHistoryLoadedFor(marker);
    setDirectHistoryReady(false);
    directHistoryInitializing = true;
    directHistorySaveSnapshot = null;
    let nextMessages = null;
    try {
      const payload = await props.loadImageAPIHistory?.();
      const storedMessages = Array.isArray(payload?.messages) ? payload.messages.map(hydrateDirectImageHistoryMessage) : [];
      if (storedMessages.length) {
        nextMessages = normalizeChatMessages(storedMessages);
        setMessages(nextMessages);
      }
    } catch (err) {
      setChatError(err instanceof Error ? err.message : String(err));
    } finally {
      const finishInitialization = () => {
        directHistoryInitializing = false;
        setDirectHistoryReady(true);
        queueDirectImageHistorySave(nextMessages || directHistorySaveSnapshot || messages(), 0);
      };
      if (typeof window !== "undefined") window.setTimeout(finishInitialization, 0);
      else finishInitialization();
    }
  };
  const firstIndexOfAny = (source, patterns) => {
    let index = -1;
    for (const pattern of patterns) {
      const match = pattern.exec(source);
      pattern.lastIndex = 0;
      if (match && (index === -1 || match.index < index)) index = match.index;
    }
    return index;
  };
  const extractReferenceItemsFromText = (source) => {
    const lines = String(source || "").split("\n");
    const items = [];
    for (let index = 0; index < lines.length; index += 1) {
      const match = lines[index].match(/^\s*-\s*(TARGET_FRAME|HOST_REFERENCE|PRODUCT_REFERENCE|REFERENCE_IMAGE)\s*:\s*(.*)$/i);
      if (!match) continue;
      let path = String(match[2] || "").trim();
      if (!path && lines[index + 1] && !/^\s*-/.test(lines[index + 1])) {
        path = lines[index + 1].trim();
      }
      path = path.replace(/\s*\([^)]*\)\s*$/g, "").trim();
      if (!path) continue;
      items.push({
        role: normalizeReferenceRole(match[1]),
        path,
        label: shortPathLabel(path),
      });
    }
    return items;
  };
  const removeReferenceListText = (source) => String(source || "")
    .replace(/Selected reference images:\s*[\s\S]*?(?=\n\s*\n|$)/gi, "")
    .replace(/^\s*-\s*(TARGET_FRAME|HOST_REFERENCE|PRODUCT_REFERENCE|REFERENCE_IMAGE)\s*:\s*.*$/gim, "");
  const parseThinkingBlock = (source) => {
    const thinkingBlocks = [];
    let text = String(source || "").replace(/<THINKING>([\s\S]*?)<\/THINKING>/g, (_match, body) => {
      thinkingBlocks.push(normalizeTextWhitespace(body));
      return "";
    });
    const headingPattern = /(^|\n)(Considering Image Style|Focusing on Structure Flow|Refining Detail & Composition|Planning Image Generation|Reference Interpretation)\b/i;
    const headingIndex = text.search(headingPattern);
    const headingMatches = text.match(/(^|\n)(Considering Image Style|Focusing on Structure Flow|Refining Detail & Composition|Planning Image Generation|Reference Interpretation)\b/gi) || [];
    if (headingIndex >= 0 && headingMatches.length >= 2) {
      thinkingBlocks.push(normalizeTextWhitespace(text.slice(headingIndex)));
      text = text.slice(0, headingIndex);
    }
    return {
      text: normalizeTextWhitespace(text),
      thinking: thinkingBlocks.filter(Boolean).join("\n\n"),
    };
  };
  const parseAgentDisplay = (message) => {
    const role = messageRole(message);
    const raw = rawMessageText(message);
    if (role === "user") {
      const references = message.referenceAttachments?.length ? message.referenceAttachments : extractReferenceItemsFromText(raw);
      return { text: normalizeTextWhitespace(removeReferenceListText(raw)), thinking: "", debug: "", references };
    }
    const debugBlocks = [];
    const extractedReferences = extractReferenceItemsFromText(raw);
    let source = raw.replace(/<PROMPT_CANDIDATE>[\s\S]*?<\/PROMPT_CANDIDATE>/g, "");
    source = source.replace(/<IMAGE_GENERATION_REQUEST>([\s\S]*?)<\/IMAGE_GENERATION_REQUEST>/g, (_match, body) => {
      debugBlocks.push(normalizeTextWhitespace(body));
      return "";
    });
    const thinkingResult = parseThinkingBlock(source);
    source = thinkingResult.text;
    const internalIndex = firstIndexOfAny(source, [
      /The user explicitly requested/i,
      /Produce exactly one/i,
      /Use these selected reference_images/i,
      /If TARGET_FRAME,\s*HOST_REFERENCE/i,
      /role\/path objects/i,
    ]);
    if (internalIndex >= 0) {
      debugBlocks.push(normalizeTextWhitespace(source.slice(internalIndex)));
      source = source.slice(0, internalIndex);
    }
    source = removeReferenceListText(source);
    source = source.replace(/已经生成并保存到\s*Upload\s*[:：]\s*.+/gi, "");
    source = source.replace(/Agent is generating[^.。]*[.。]?/gi, "");
    return {
      text: normalizeTextWhitespace(source),
      thinking: thinkingResult.thinking,
      debug: debugBlocks.filter(Boolean).join("\n\n"),
      references: extractedReferences,
    };
  };
  const visibleMessageText = (message) => parseAgentDisplay(message).text;
  const chatModelItems = createMemo(() => Array.isArray(chatPromptModels().items) ? chatPromptModels().items : []);
  const selectedChatModel = createMemo(() => parseModelKey(selectedChatModelKey()));
  const candidatePrompt = (candidate) => [
    String(candidate?.positive_prompt || candidate?.prompt || "").trim(),
    String(candidate?.negative_prompt || "").trim() ? `Negative prompt: ${String(candidate.negative_prompt).trim()}` : "",
  ].filter(Boolean).join("\n\n");
  const extractPromptCandidates = (message) => {
    const source = rawMessageText(message);
    const pattern = /<PROMPT_CANDIDATE>([\s\S]*?)<\/PROMPT_CANDIDATE>/g;
    const items = [];
    let match;
    while ((match = pattern.exec(source))) {
      try {
        const parsed = JSON.parse(match[1].trim());
        const prompt = candidatePrompt(parsed);
        if (!prompt) continue;
        const aspect = ["16:9", "4:3", "1:1", "3:4", "9:16"].includes(parsed.aspect) ? parsed.aspect : settings().aspect;
        items.push({
          ...parsed,
          id: `${messageId(message) || "candidate"}_${items.length + 1}`,
          prompt,
          aspect,
          title: String(parsed.title || `Candidate ${items.length + 1}`),
        });
      } catch {
        // Ignore incomplete streaming candidate blocks until the assistant finishes them.
      }
    }
    return items;
  };
  const seedChatMessages = (items) => {
    const normalized = normalizeChatMessages(items);
    setPartsByMessageId(() => {
      const next = {};
      for (const message of normalized) {
        const id = messageId(message);
        next[id] = {};
        for (const part of message.parts || []) {
          const partId = String(part.id || part.partID || `${id}-part-${Object.keys(next[id]).length + 1}`);
          next[id][partId] = { ...part, id: partId, messageID: part.messageID || id };
        }
      }
      return next;
    });
    setAgentMessages(normalized);
    scrollAgentChatToBottom();
  };
  const upsertMessage = (message) => {
    const id = messageId(message);
    if (!id) return;
    setAgentMessages((current) => normalizeChatMessages([
      ...current.filter((item) => messageId(item) !== id && !String(messageId(item)).startsWith("local-")),
      message,
    ]));
    if (message.parts?.length) {
      setPartsByMessageId((current) => {
        const next = { ...current, [id]: { ...(current[id] || {}) } };
        for (const part of message.parts) {
          const partId = String(part.id || part.partID || `${id}-part-${Object.keys(next[id]).length + 1}`);
          next[id][partId] = { ...part, id: partId, messageID: part.messageID || id };
        }
        return next;
      });
    }
  };
  const upsertPart = (part) => {
    const parentId = String(part?.messageID || part?.messageId || part?.message_id || "");
    const id = String(part?.id || part?.partID || "");
    if (!parentId || !id) return;
    setPartsByMessageId((current) => ({
      ...current,
      [parentId]: {
        ...(current[parentId] || {}),
        [id]: { ...(current[parentId]?.[id] || {}), ...part, id, messageID: parentId },
      },
    }));
  };
  const appendPartDelta = (properties) => {
    const parentId = String(properties?.messageID || properties?.messageId || properties?.message_id || "");
    const partId = String(properties?.partID || properties?.partId || properties?.id || "");
    const field = String(properties?.field || "text");
    const delta = String(properties?.delta || "");
    if (!parentId || !partId || field !== "text" || !delta) return;
    setPartsByMessageId((current) => {
      const existing = current[parentId]?.[partId] || { id: partId, messageID: parentId, type: "text", text: "" };
      return {
        ...current,
        [parentId]: {
          ...(current[parentId] || {}),
          [partId]: { ...existing, text: String(existing.text || "") + delta },
        },
      };
    });
  };
  const handleChatEvent = (event) => {
    const type = String(event?.type || "");
    const properties = event?.properties || {};
    if (type === "asset_agent.image_generation.started") {
      const id = `agent-image-${properties.agent_generation_id || Date.now()}`;
      upsertAgentLocalMessage(id, {
        text: properties.title ? `Agent is generating: ${properties.title}` : "Agent is generating an image.",
        imagePlaceholder: true,
        aspectRatio: properties.aspect || settings().aspect,
        progressLabel: "0%",
      });
      setAgentBusy("image");
      props.onAgentImageGenerationEvent?.(event);
      return;
    }
    if (type === "asset_agent.image_generation.completed") {
      const id = `agent-image-${properties.agent_generation_id || properties.request_id || ""}`;
      const completedAssets = Array.isArray(properties.assets) && properties.assets.length ? properties.assets : (properties.asset ? [properties.asset] : []);
      const previewAsset = completedAssets[0] || {};
      const completedText = completedAssets.length > 1
        ? `已经生成并保存到 Upload：${completedAssets.length} 张图片`
        : `已经生成并保存到 Upload：${previewAsset.filename || previewAsset.path || "Agent generated image"}`;
      if (properties.agent_generation_id || properties.request_id) {
        upsertAgentLocalMessage(id, {
          imagePlaceholder: false,
          imageUrl: previewAsset.path ? props.imageUrl?.(previewAsset) : "",
          path: previewAsset.path || "",
          asset: previewAsset,
          progressLabel: "",
          text: completedText,
        }, {
          aspectRatio: properties.aspect || settings().aspect,
        });
      } else {
        addAgentLocalMessage({ id: `agent-image-${Date.now()}`, role: "assistant", text: completedText, created_at: Date.now(), imageUrl: properties.asset?.path ? props.imageUrl?.(properties.asset) : "", path: properties.asset?.path || "", asset: properties.asset || null });
      }
      props.onAgentImageGenerationEvent?.(event);
      setAgentBusy((current) => current === "image" ? "" : current);
      return;
    }
    if (type === "asset_agent.image_generation.failed") {
      const id = `agent-image-${properties.agent_generation_id || properties.request_id || ""}`;
      const detail = String(properties.detail?.message || properties.detail || properties.message || "Agent image generation failed.");
      if (properties.agent_generation_id || properties.request_id) {
        upsertAgentLocalMessage(id, { failed: true, progressLabel: "Failed", text: detail });
      } else {
        setChatError(detail);
      }
      props.onAgentImageGenerationEvent?.(event);
      setAgentBusy((current) => current === "image" ? "" : current);
      return;
    }
    if (type === "message.updated") {
      const nextMessage = properties.info ? { info: properties.info, parts: properties.parts || [] } : properties.message || properties;
      upsertMessage(nextMessage);
      if (messageRole(nextMessage) === "assistant" && nextMessage?.info?.time?.completed) {
        setAgentBusy((current) => current === "chat" ? "" : current);
      }
      return;
    }
    if (type === "message.part.updated") {
      upsertPart(properties.part || properties);
      return;
    }
    if (type === "message.part.delta") {
      appendPartDelta(properties);
      return;
    }
    if (type === "message.part.removed") {
      const parentId = String(properties.messageID || "");
      const partId = String(properties.partID || properties.id || "");
      setPartsByMessageId((current) => {
        const nextParts = { ...(current[parentId] || {}) };
        delete nextParts[partId];
        return { ...current, [parentId]: nextParts };
      });
      return;
    }
    if (type === "session.status") {
      const statusType = String(properties.status?.type || properties.type || "");
      if (statusType && statusType !== "idle") setAgentBusy("chat");
      if (statusType === "idle") setAgentBusy((current) => current === "chat" ? "" : current);
      return;
    }
    if (type === "session.stream.error" || type === "asset_agent.chat.tool_blocked") {
      setChatError(String(properties.message || "Asset agent chat stream failed."));
      setAgentBusy((current) => current === "chat" ? "" : current);
    }
  };
  const assetKey = (asset) => asset?.path || asset?.id || "";
  const referenceStorageKey = () => `koubo-storyboard:asset-library-reference-items:${props.task?.()?.id || "unknown"}`;
  const readStoredReferenceItems = () => {
    if (isOpenCodeWorkspace()) return [];
    try {
      const items = JSON.parse(window.sessionStorage?.getItem(referenceStorageKey()) || "[]");
      return Array.isArray(items) ? items.filter((item) => item?.path) : [];
    } catch {
      return [];
    }
  };
  const writeStoredReferenceItems = (items) => {
    try {
      if (isOpenCodeWorkspace()) {
        window.sessionStorage?.removeItem(referenceStorageKey());
        return;
      }
      window.sessionStorage?.setItem(referenceStorageKey(), JSON.stringify((items || []).filter((item) => item?.path)));
    } catch {
      // References are still kept in memory if browser session storage is unavailable.
    }
  };
  const [referenceItems, setReferenceItemsValue] = createSignal(readStoredReferenceItems());
  const setReferenceItems = (updater) => {
    setReferenceItemsValue((previous) => {
      const next = typeof updater === "function" ? updater(previous) : updater;
      writeStoredReferenceItems(next);
      return next;
    });
  };
  const mergeAssetsByPath = (...groups) => {
    const seen = new Set();
    const merged = [];
    for (const asset of groups.flat()) {
      const key = assetKey(asset);
      if (!key || seen.has(key)) continue;
      seen.add(key);
      merged.push(asset);
    }
    return merged;
  };
  const reconcileReferenceItems = (items, availableAssets) => {
    const byPath = new Map();
    const byRenamedFrom = new Map();
    for (const asset of availableAssets || []) {
      if (!asset?.path) continue;
      byPath.set(asset.path, asset);
      if (asset.renamed_from) byRenamedFrom.set(asset.renamed_from, asset);
    }
    let changed = false;
    const next = [];
    for (const item of items || []) {
      const path = item?.path || "";
      if (!path) continue;
      const renamed = byRenamedFrom.get(path);
      if (renamed) {
        next.push(renamed);
        changed = true;
        continue;
      }
      if (path.startsWith("SessionOutput/storyboard/assets/images/") && !byPath.has(path)) {
        changed = true;
        continue;
      }
      next.push(byPath.get(path) || item);
    }
    const merged = mergeAssetsByPath(next);
    if (merged.length !== next.length) changed = true;
    return changed ? merged : items;
  };
  const referenceAssets = createMemo(() => mergeAssetsByPath(referenceItems(), isOpenCodeWorkspace() ? [] : (props.selectedItems?.() || [])));
  const referencePayloadItems = createMemo(() => buildReferencePayload(referenceAssets()));
  const referenceRoleByPath = createMemo(() => new Map(referencePayloadItems().map((item) => [item.path, item.role])));
  const assetByPath = createMemo(() => {
    const items = mergeAssetsByPath(referenceAssets(), props.availableAssets?.() || []);
    return new Map(items.map((asset) => [asset?.path, asset]).filter(([path]) => path));
  });
  const displayReferenceItems = () => referencePayloadItems().map((item) => {
    const asset = assetByPath().get(item.path) || item;
    return {
      ...item,
      label: item.label || shortPathLabel(item.path),
      imageUrl: props.imageUrl?.(asset),
      thumbnailUrl: props.thumbnailUrl?.(asset) || props.imageUrl?.(asset),
    };
  });
  const displayReferenceItemsForPayload = (items = []) => (items || []).map((item) => {
    const asset = assetByPath().get(item.path) || item;
    return {
      ...item,
      label: item.label || shortPathLabel(item.path),
      imageUrl: props.imageUrl?.(asset),
      thumbnailUrl: props.thumbnailUrl?.(asset) || props.imageUrl?.(asset),
    };
  });
  const agentChatSubmitKey = (value, options = {}, refs = []) => JSON.stringify({
    value: text(value),
    intent: text(options.intent),
    confirmed: Boolean(options.confirmed),
    aspect: text(options.aspect || settings().aspect),
    count: Number(options.count || settings().count || 1) || 1,
    references: (refs || []).map((item) => `${text(item.role)}:${text(item.path)}`),
  });
  const selectedConsistencyCount = createMemo(() => selectedConsistencyKeys().size);
  const addReferenceAssets = (assets) => {
    const added = (assets || []).filter((asset) => asset?.path);
    if (!added.length) return;
    setReferenceItems((previous) => mergeAssetsByPath(previous, added));
  };
  createEffect(() => {
    const available = props.availableAssets?.() || [];
    if (!available.length) return;
    setReferenceItems((previous) => reconcileReferenceItems(previous, available));
  });
  onMount(() => {
    if (!isOpenCodeWorkspace()) {
      void loadStoredSettings().catch((err) => {
        addMessage({ role: "assistant", text: err instanceof Error ? err.message : String(err) });
      });
    }
    const handleRename = (event) => {
      const oldPath = event?.detail?.oldPath;
      const renamed = event?.detail?.asset;
      if (!oldPath || !renamed?.path) return;
      setReferenceItems((previous) => previous.map((item) => item?.path === oldPath ? renamed : item));
    };
    const handleAddReference = (event) => {
      const asset = event?.detail?.asset;
      if (!asset?.path) return;
      addReferenceAssets([asset]);
    };
    window.addEventListener("koubo-storyboard:asset-library-asset-renamed", handleRename);
    window.addEventListener("koubo-storyboard:asset-library-add-reference", handleAddReference);
    onCleanup(() => {
      if (chatScrollFrame) window.cancelAnimationFrame(chatScrollFrame);
      if (agentScrollFrame) window.cancelAnimationFrame(agentScrollFrame);
      flushDirectImageHistorySave();
      window.removeEventListener("koubo-storyboard:asset-library-asset-renamed", handleRename);
      window.removeEventListener("koubo-storyboard:asset-library-add-reference", handleAddReference);
    });
  });
  createEffect(() => {
    if (isOpenCodeWorkspace()) return;
    void initializeDirectImageHistory();
  });
  createEffect(() => {
    if (isOpenCodeWorkspace()) return;
    queueDirectImageHistorySave(messages());
  });
  createEffect(() => {
    if (!openCodeVisible()) return;
    const sessionId = chatSessionId();
    agentMessages().length;
    const agentGroups = collectAgentImageResultGroups(props.availableAssets?.() || [], sessionId);
    if (!agentGroups.length) return;
    setAgentMessages((current) => {
      const groupsByMessageId = new Map(agentGroups.filter((group) => group.messageId).map((group) => [group.messageId, group]));
      const currentIds = new Set(current.map((message) => messageId(message)));
      const duplicateSyntheticIds = new Set(agentGroups
        .filter((group) => group.messageId && currentIds.has(group.messageId) && groupsByMessageId.get(group.messageId)?.key === group.key)
        .map((group) => `agent-image-${group.key}`));
      const usedGroupKeys = new Set();
      let changed = false;
      const merged = current.flatMap((message) => {
        const id = messageId(message);
        if (duplicateSyntheticIds.has(id)) {
          changed = true;
          return [];
        }
        const group = groupsByMessageId.get(id);
        if (!group) return [message];
        const patch = agentImageResultPatch(group.assets);
        if (message.imageUrl === patch.imageUrl && message.path === patch.path) {
          usedGroupKeys.add(group.key);
          return [message];
        }
        usedGroupKeys.add(group.key);
        changed = true;
        return [mergeAgentImageResultIntoMessage(message, group)];
      });
      const existingIds = new Set(merged.map((message) => messageId(message)));
      const additions = agentGroups
        .filter((group) => !usedGroupKeys.has(group.key) && !existingIds.has(`agent-image-${group.key}`))
        .map((group) => {
          const patch = agentImageResultPatch(group.assets);
          return {
            id: `agent-image-${group.key}`,
            role: "assistant",
            created_at: Math.min(...group.assets.map(agentAssetCreatedAt)),
            text: patch.text,
            imageUrl: patch.imageUrl,
            path: patch.path,
            asset: patch.asset,
          };
        });
      if (!changed && !additions.length) return current;
      return normalizeChatMessages([...merged, ...additions]);
    });
    scrollAgentChatToBottom();
  });
  createEffect(() => {
    if (!openCodeVisible()) return;
    agentMessages().length;
    chatError();
    agentBusy();
    scrollAgentChatToBottom();
  });
  createEffect(() => {
    const url = props.agentChatEventsUrl?.();
    if (!openCodeVisible() || !url || !chatSessionId()) return;
    const source = new EventSource(url, { withCredentials: true });
    source.onmessage = (event) => {
      try {
        handleChatEvent(JSON.parse(event.data));
      } catch (err) {
        setChatError(err instanceof Error ? err.message : String(err));
      }
    };
    source.onerror = () => setChatError("Asset agent chat stream disconnected.");
    onCleanup(() => source.close());
  });
  const removeReferenceAsset = (asset) => {
    const key = assetKey(asset);
    setReferenceItems((previous) => previous.filter((item) => assetKey(item) !== key));
    props.onRemoveReferenceAsset?.(asset);
  };
  const reportComposerError = (err) => {
    const message = err instanceof Error ? err.message : String(err);
    if (isOpenCodeWorkspace()) {
      setChatError(message);
      return;
    }
    addMessage({ role: "assistant", text: message });
  };
  const setSuggestion = (text) => {
    if (busy()) return;
    setDraft(text);
  };
  const readableFailureDetail = (event) => {
    const detail = event?.detail ?? event?.message ?? event?.error ?? "";
    if (typeof detail === "string") return detail || "Image generation failed.";
    if (detail?.message) {
      const missing = Array.isArray(detail.missing_reference_images) && detail.missing_reference_images.length
        ? `\nMissing references: ${detail.missing_reference_images.join(", ")}`
        : "";
      return `${detail.message}${missing}`;
    }
    try {
      return JSON.stringify(detail, null, 2);
    } catch {
      return String(detail || "Image generation failed.");
    }
  };
  const applyStoredSettings = (payload, options = {}) => {
    if (!payload) return;
    const applyPromptModels = options.applyPromptModels !== false;
    const applyChatSession = options.applyChatSession !== false;
    if (payload.settings) {
      setSettings((previous) => ({ ...previous, ...payload.settings }));
      if (applyPromptModels && payload.settings.chatProvider && payload.settings.chatModel) {
        setSelectedChatModelKey(modelKey({ providerID: payload.settings.chatProvider, modelID: payload.settings.chatModel }));
      }
    }
    if (applyChatSession && payload.chat_opencode_session_id !== undefined) setChatSessionId(String(payload.chat_opencode_session_id || ""));
    if (applyPromptModels && payload.prompt_models) applyChatPromptModels(payload.prompt_models);
  };
  const applyChatPromptModels = (models) => {
    if (!models) return;
    setChatPromptModels(models);
    const available = Array.isArray(models.items) ? models.items : [];
    const stored = settings();
    const storedKey = stored.chatProvider && stored.chatModel ? modelKey({ providerID: stored.chatProvider, modelID: stored.chatModel }) : "";
    if (storedKey && available.some((item) => modelKey(item) === storedKey)) {
      setSelectedChatModelKey(storedKey);
      return;
    }
    const current = selectedChatModelKey();
    if (current && available.some((item) => modelKey(item) === current)) return;
    const defaultModel = models.default_model || {};
    const next = available.find((item) => modelKey(item) === modelKey(defaultModel)) || available[0] || defaultModel;
    setSelectedChatModelKey(modelKey(next));
  };
  const agentImageModels = (config = imageModelConfig()) => Array.isArray(config?.agent_model_aliases) ? config.agent_model_aliases : [];
  const selectedAgentImageModel = (config = imageModelConfig(), source = settings()) => {
    const aliases = agentImageModels(config);
    const alias = text(source.agentImageAlias || source.agent_image_alias);
    const provider = text(source.provider);
    const model = text(source.model);
    if (alias) return aliases.find((item) => text(item.alias) === alias) || null;
    if (provider && model) {
      return aliases.find((item) => text(item.provider) === provider && text(item.model) === model) || null;
    }
    return null;
  };
  const imageAliasKey = (value) => text(value).replace(/[\s_.-]+/g, "").toLowerCase();
  const isGrokImageModel = (item = {}) => {
    const provider = text(item.provider).toLowerCase();
    const model = text(item.model).toLowerCase();
    const aliasKey = imageAliasKey(item.alias);
    return provider === "xai"
      || model.includes("grok")
      || aliasKey === "qualityx"
      || aliasKey === "flashx"
      || aliasKey.includes("grok");
  };
  const requireAgentImageModel = (config = imageModelConfig(), source = settings()) => {
    const model = selectedAgentImageModel(config, source);
    if (!model) {
      throw new Error("请先在 Image Model Settings 的 Agent 模块中配置并选择图像模型。");
    }
    const alias = text(model.alias);
    return {
      alias,
      provider: alias ? "" : text(model.provider),
      model: alias ? "" : text(model.model),
    };
  };
  const imageAPISettingsPayload = (source = settings()) => ({
    confirmBeforeGenerate: source.confirmBeforeGenerate !== false,
    aspect: source.aspect || "16:9",
    count: source.count || 1,
    agentImageAlias: source.agentImageAlias || "",
    provider: source.provider || "",
    model: source.model || "",
  });
  const imagesAgentSettingsPayload = (source = settings()) => ({
    ...imageAPISettingsPayload(source),
    chatProvider: source.chatProvider || "",
    chatModel: source.chatModel || "",
  });
  const normalizeSettingsForAgentPool = (config) => {
    setSettings((previous) => {
      const selected = selectedAgentImageModel(config, previous);
      if (!selected) return { ...previous, agentImageAlias: "", provider: "", model: "" };
      return { ...previous, agentImageAlias: selected.alias || "", provider: "", model: "" };
    });
  };
  const loadStoredSettings = async (options = {}) => {
    const loader = options.target === "images-agent" ? props.loadImagesAgentSettings : props.loadImageAPISettings;
    const payload = await loader?.();
    applyStoredSettings(payload, options);
    return payload;
  };
  const initializeChat = async () => {
    if (!props.ensureAgentChatSession || !props.loadAgentChatMessages) return;
    setChatError("");
    const state = await props.ensureAgentChatSession();
    applyStoredSettings(state);
    const history = await props.loadAgentChatMessages();
    if (history?.prompt_models) applyChatPromptModels(history.prompt_models);
    if (history?.chat_opencode_session_id !== undefined) setChatSessionId(String(history.chat_opencode_session_id || ""));
    seedChatMessages(history?.items || []);
  };
  const openAgentChat = async () => {
    setAgentOpen(true);
    if (agentInitialized()) return;
    setAgentInitialized(true);
    try {
      await initializeChat();
    } catch (err) {
      setAgentInitialized(false);
      setChatError(err instanceof Error ? err.message : String(err));
    }
  };
  const ensureImageModelConfig = async (options = {}) => {
    if (!options.force && imageModelConfig()) return imageModelConfig();
    const config = await props.imageModelConfig?.();
    setImageModelConfig(config);
    return config;
  };
  const openSettings = async (target = "image-api") => {
    const nextTarget = target === "images-agent" ? "images-agent" : "image-api";
    setSettingsTarget(nextTarget);
    setSettingsOpen(true);
    try {
      const payload = await loadStoredSettings({
        target: nextTarget,
        applyPromptModels: nextTarget === "images-agent",
        applyChatSession: nextTarget === "images-agent",
      });
      if (nextTarget === "images-agent" && payload?.prompt_models) applyChatPromptModels(payload.prompt_models);
      else if (nextTarget === "images-agent" && !chatModelItems().length && props.ensureAgentChatSession) {
        const state = await props.ensureAgentChatSession();
        applyStoredSettings(state);
      }
      const config = await ensureImageModelConfig({ force: true });
      normalizeSettingsForAgentPool(config);
    } catch (err) {
      if (isOpenCodeWorkspace()) setChatError(err instanceof Error ? err.message : String(err));
      else addMessage({ role: "assistant", text: err instanceof Error ? err.message : String(err) });
    }
  };
  const saveSettings = async () => {
    if (settingsSaving()) return;
    setSettingsSaving(true);
    try {
      const isAgentSettings = settingsTarget() === "images-agent";
      const saver = isAgentSettings ? props.saveImagesAgentSettings : props.saveImageAPISettings;
      const config = await ensureImageModelConfig();
      const imageModel = requireAgentImageModel(config);
      const nextSettings = {
        ...settings(),
        agentImageAlias: imageModel.alias,
        provider: imageModel.provider,
        model: imageModel.model,
      };
      setSettings(nextSettings);
      const payload = await saver?.(isAgentSettings ? imagesAgentSettingsPayload(nextSettings) : imageAPISettingsPayload(nextSettings));
      applyStoredSettings(payload, {
        applyPromptModels: isAgentSettings,
        applyChatSession: isAgentSettings,
      });
      setSettingsOpen(false);
    } catch (err) {
      if (isOpenCodeWorkspace()) setChatError(err instanceof Error ? err.message : String(err));
      else addMessage({ role: "assistant", text: err instanceof Error ? err.message : String(err) });
    } finally {
      setSettingsSaving(false);
    }
  };
  const renderSettingsPanel = () => <Show when={settingsOpen()}>
    <Show
      when={settingsTarget() === "images-agent"}
      fallback={<ImageAPISettings
        settings={settings}
        setSettings={setSettings}
        imageModelConfig={imageModelConfig}
        saving={settingsSaving}
        onClose={() => setSettingsOpen(false)}
        onSave={saveSettings}
      />}
    >
      <ImagesAgentSettings
        settings={settings}
        setSettings={setSettings}
        imageModelConfig={imageModelConfig}
        chatModelItems={chatModelItems}
        selectedChatModelKey={selectedChatModelKey}
        onChatModelChange={(item) => setSelectedChatModelKey(modelKey(item))}
        saving={settingsSaving}
        onClose={() => setSettingsOpen(false)}
        onSave={saveSettings}
      />
    </Show>
  </Show>;
  const promptBuilderCurrentDraft = () => promptBuilderTarget() === "agent" ? agentDraft() : draft();
  const openPromptBuilder = async (override = {}) => {
    if (busy()) return;
    const target = override.target || promptBuilderTarget() || "default";
    const draftText = String((override.draft ?? (target === "agent" ? agentDraft() : draft())) || "");
    setPromptBuilderTarget(target);
    setPromptBuilderOpen(true);
    setPromptBuilderLoading(true);
    setPromptBuilderError("");
    setPromptBuilderPayload({});
    setConsistencyPickerOpen(false);
    try {
      const config = await ensureImageModelConfig();
      const imageModel = requireAgentImageModel(config, { ...settings(), ...override });
      const referencePayload = referenceAssets().map((asset) => ({
        path: asset?.path || "",
        label: asset?.label || asset?.filename || "",
        key: asset?.key || "",
        kind: asset?.kind || "",
        source: asset?.source || "",
      })).filter((item) => item.path);
      const response = await props.buildPromptBuilder?.({
        agentImageAlias: imageModel.alias,
        provider: imageModel.provider,
        model: imageModel.model,
        draft: draftText,
        reference_images: referencePayload,
        mode: "image",
        aspect: settings().aspect || aspectFromPrompt(draftText),
      });
      setPromptBuilderPayload(response || {});
    } catch (err) {
      setPromptBuilderError(err instanceof Error ? err.message : String(err));
    } finally {
      setPromptBuilderLoading(false);
    }
  };
  const switchToGrokModel = async () => {
    try {
      const config = await ensureImageModelConfig();
      const model = agentImageModels(config).find((item) => isGrokImageModel(item));
      if (!model) {
        setPromptBuilderError("当前 Agent 模型池中没有可用的 Grok 图像模型。");
        return;
      }
      const next = { agentImageAlias: model.alias || "", provider: "", model: "" };
      setSettings((previous) => ({ ...previous, ...next }));
      await openPromptBuilder(next);
    } catch (err) {
      setPromptBuilderError(err instanceof Error ? err.message : String(err));
    }
  };
  const applyPromptBuilder = async ({ applyMode, insertMode, positivePrompt, negativePrompt, prompt }) => {
    const requestId = promptBuilderPayload().request_id;
    if (!requestId) throw new Error("Prompt Builder draft is missing request id");
    const response = await props.savePromptBuilder?.(requestId, {
      positive_prompt: positivePrompt,
      negative_prompt: negativePrompt,
      prompt,
      apply_mode: applyMode,
    });
    const nextPrompt = response?.prompt || prompt || "";
    const setTargetDraft = promptBuilderTarget() === "agent" ? setAgentDraft : setDraft;
    setTargetDraft((previous) => {
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
    if (promptBuilderTarget() === "agent") {
      addAgentLocalMessage({ id: `prompt-builder-${Date.now()}`, role: "assistant", text: "Prompt 已添加到输入框，你可以继续修改或发送给 Agent。", created_at: Date.now() });
    } else {
      addMessage({ role: "assistant", text: "Prompt 已添加到输入框，你可以继续修改或直接生成。" });
    }
  };
  const normalizeConsistencyReferences = (payload) => {
    const active = payload?.config?.active || {};
    const consistencyRefs = Array.isArray(payload?.consistency_references?.references) ? payload.consistency_references.references : [];
    const consistencyPathFor = (key) => String(consistencyRefs.find((item) => item?.kind === key)?.output_path || "").trim();
    const items = [
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
    ];
    return items
      .map((item) => {
        const path = String(item.section?.output || item.activePath || item.consistencyPath || item.fallbackPath || "").trim();
        if (!path) return null;
        return {
          id: `consistency:${item.key}:${path}`,
          key: item.key,
          label: item.label,
          filename: path.split("/").pop() || item.fallbackFilename,
          kind: "image",
          reference_role: item.key === "host" ? "HOST_REFERENCE" : "PRODUCT_REFERENCE",
          source: "session_consistency_reference",
          path,
        };
      })
      .filter(Boolean);
  };
  const openConsistencyPicker = async () => {
    if (busy()) return;
    setConsistencyPickerOpen(true);
    setConsistencyLoading(true);
    try {
      const payload = await props.loadConsistencyReferences?.();
      const refs = normalizeConsistencyReferences(payload);
      setConsistencyReferences(refs);
      setSelectedConsistencyKeys(new Set(refs.map((item) => item.key)));
    } catch (err) {
      reportComposerError(err);
    } finally {
      setConsistencyLoading(false);
    }
  };
  const toggleConsistencyReference = (item) => {
    setSelectedConsistencyKeys((previous) => {
      const next = new Set(previous);
      if (next.has(item.key)) next.delete(item.key);
      else next.add(item.key);
      return next;
    });
  };
  const confirmConsistencyReferences = () => {
    const selected = consistencyReferences().filter((item) => selectedConsistencyKeys().has(item.key));
    addReferenceAssets(selected);
    setConsistencyPickerOpen(false);
  };
  const dropMissingConsistencyReference = (item) => {
    setConsistencyReferences((previous) => previous.filter((candidate) => candidate.key !== item.key || candidate.path !== item.path));
    setSelectedConsistencyKeys((previous) => {
      const next = new Set(previous);
      next.delete(item.key);
      return next;
    });
  };
  const uploadReferenceFiles = async (files, source) => {
    if (busy()) return;
    setBusy("upload");
    try {
      const added = await props.uploadImageFiles?.(files, { source, attachAsReference: true });
      addReferenceAssets(added);
    } catch (err) {
      reportComposerError(err);
    } finally {
      setBusy("");
    }
  };
  const chooseReferenceFiles = async (event) => {
    const input = event.currentTarget;
    await uploadReferenceFiles(input.files, "composer_plus");
    input.value = "";
  };
  const dragHasFiles = (event) => Array.from(event.dataTransfer?.types || []).includes("Files");
  const dragHasAsset = (event) => Array.from(event.dataTransfer?.types || []).includes("application/x-koubo-storyboard-asset");
  const assetFromDataTransfer = (dataTransfer) => {
    try {
      const payload = dataTransfer?.getData("application/x-koubo-storyboard-asset");
      if (!payload) return null;
      const asset = JSON.parse(payload);
      return asset?.path ? asset : null;
    } catch {
      return null;
    }
  };
  const handleReferenceDrag = (event) => {
    if ((!dragHasFiles(event) && !dragHasAsset(event)) || busy()) return;
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = "copy";
    setReferenceDragging(true);
  };
  const handleReferenceDrop = async (event) => {
    if ((!dragHasFiles(event) && !dragHasAsset(event)) || busy()) return;
    event.preventDefault();
    event.stopPropagation();
    setReferenceDragging(false);
    const draggedAsset = assetFromDataTransfer(event.dataTransfer);
    if (draggedAsset) {
      addReferenceAssets([draggedAsset]);
      return;
    }
    const files = await props.filesFromDataTransfer?.(event.dataTransfer, "composer_drop");
    if (files?.length) await uploadReferenceFiles(files, "composer_drop");
  };
  const handleReferencePaste = async (event) => {
    const files = props.filesFromClipboard?.(event.clipboardData, "composer_paste");
    if (!files?.length || busy()) return;
    event.preventDefault();
    event.stopPropagation();
    await uploadReferenceFiles(files, "composer_paste");
  };
  const focusComposerTextarea = (textarea, event) => {
    if (event.target?.closest?.("button,input,textarea")) return;
    textarea?.focus?.({ preventScroll: true });
  };
  const looksLikeImageGenerationRequest = (value) => /生成|生图|出图|做图|generate|create\s+(an?\s+)?image|make\s+(an?\s+)?image/i.test(String(value || ""));
  const runGenerate = async (promptText, source = {}) => {
    const text = String(promptText || "").trim();
    if (!text || busy()) return;
    setBusy("generate");
    const aspect = source.aspect || settings().aspect || aspectFromPrompt(text);
    let imageModel = null;
    try {
      const config = await ensureImageModelConfig();
      imageModel = requireAgentImageModel(config, { ...settings(), ...source });
    } catch (err) {
      addMessage({ role: "assistant", text: err instanceof Error ? err.message : String(err) });
      setBusy("");
      return;
    }
    const now = Date.now();
    const messageId = `generation_${now}`;
    const userMessage = {
      id: `${messageId}_user`,
      role: "user",
      text,
      created_at: now,
    };
    const assistantMessage = {
      id: messageId,
      role: "assistant",
      created_at: now + 1,
    };
    const mirrorToAgent = isOpenCodeWorkspace();
    const agentGenerationId = `agent_${messageId}`;
    const generationText = aspect === "9:16"
      ? "我会生成一张 9:16 的画面，并保存到 Upload。"
      : aspect === "16:9"
        ? "我会生成一张 16:9 的画面，并保存到 Upload。"
        : "我会根据你的描述和选中的参考图生成新图，并保存到 Upload。";
    addMessage(userMessage);
    addMessage({
      ...assistantMessage,
      text: generationText,
      imagePlaceholder: true,
      aspectRatio: aspect,
      progressLabel: "0%",
    });
    if (mirrorToAgent) {
      const now = Date.now();
      addAgentLocalMessage({ id: `${agentGenerationId}_user`, role: "user", text, created_at: now, referenceAttachments: displayReferenceItems() });
      addAgentLocalMessage({
        id: agentGenerationId,
        role: "assistant",
        text: generationText,
        created_at: now + 1,
        imagePlaceholder: true,
        aspectRatio: aspect,
        progressLabel: "0%",
      });
    }
    try {
      const result = await props.generateImage?.(text, referenceAssets(), {
        aspect,
        count: settings().count || 1,
        agentImageAlias: imageModel.alias,
        provider: imageModel.provider,
        model: imageModel.model,
        promptBuilderRequestId: lastAppliedPromptBuilder()?.prompt === text ? lastAppliedPromptBuilder()?.requestId : "",
        promptBuilderAppliedPath: lastAppliedPromptBuilder()?.prompt === text ? lastAppliedPromptBuilder()?.appliedPath : "",
        chatOpenCodeSessionId: source.chatOpenCodeSessionId || "",
        promptCandidateId: source.promptCandidateId || "",
        promptCandidateTitle: source.promptCandidateTitle || "",
      }, (event) => {
        if (event?.type === "heartbeat") {
          const elapsed = Number(event.elapsed_seconds || 0);
          const percent = Math.min(96, Math.max(8, Math.round(8 + elapsed * 4)));
          updateMessage(messageId, { progressLabel: `${percent}%` });
          if (mirrorToAgent) updateAgentLocalMessage(agentGenerationId, { progressLabel: `${percent}%` });
        }
        if (event?.type === "failed") {
          const detail = readableFailureDetail(event);
          const failedPatch = { failed: true, progressLabel: "Failed", text: detail };
          updateMessage(messageId, failedPatch);
          void saveDirectImageHistorySnapshot([...messages().filter((message) => message.id !== userMessage.id && message.id !== messageId), userMessage, { ...assistantMessage, ...failedPatch }]);
          if (mirrorToAgent) updateAgentLocalMessage(agentGenerationId, { failed: true, progressLabel: "Failed", text: detail });
        }
        if (event?.type === "completed" && (event.asset?.path || event.assets?.length)) {
          const completedAssets = Array.isArray(event.assets) && event.assets.length ? event.assets : [event.asset];
          const previewAsset = completedAssets[0] || {};
          const completedText = completedAssets.length > 1 ? `已经生成并保存到 Upload：${completedAssets.length} 张图片` : `已经生成并保存到 Upload：${previewAsset.filename || previewAsset.path}`;
          const completedPatch = {
            imagePlaceholder: false,
            imageUrl: props.imageUrl?.(previewAsset),
            path: previewAsset.path || "",
            asset: previewAsset,
            progressLabel: "",
            text: completedText,
          };
          updateMessage(messageId, completedPatch);
          void saveDirectImageHistorySnapshot([...messages().filter((message) => message.id !== userMessage.id && message.id !== messageId), userMessage, { ...assistantMessage, ...completedPatch }]);
          if (mirrorToAgent) {
            updateAgentLocalMessage(agentGenerationId, {
              imagePlaceholder: false,
              imageUrl: props.imageUrl?.(previewAsset),
              path: previewAsset.path || "",
              asset: previewAsset,
              progressLabel: "",
              text: completedText,
            });
          }
        }
      });
      if (result?.asset?.path) {
        setDraft("");
      }
    } catch (err) {
      const detail = err instanceof Error ? err.message : String(err);
      addMessage({ role: "assistant", text: detail });
      if (mirrorToAgent) updateAgentLocalMessage(agentGenerationId, { failed: true, progressLabel: "Failed", text: detail });
    } finally {
      setBusy("");
    }
  };
  const requestGenerate = async (promptText, source = {}) => {
    const text = String(promptText || "").trim();
    if (!text || busy()) return false;
    const { confirmed, ...generationSource } = source || {};
    if (settings().confirmBeforeGenerate !== false && !confirmed) {
      setPendingImageGeneration({ prompt: text, source: generationSource });
      return false;
    }
    setPendingImageGeneration(null);
    await runGenerate(text, generationSource);
    return true;
  };
  const generate = async () => requestGenerate(draft().trim(), {});
  const applyPromptCandidate = async (candidate, mode) => {
    const prompt = candidatePrompt(candidate);
    if (!prompt || busy()) return;
    if (isOpenCodeWorkspace()) setAgentDraft(prompt);
    else setDraft(prompt);
    setLastAppliedPromptBuilder(null);
    if (mode === "generate") {
      if (isOpenCodeWorkspace()) {
        setAgentDraft(`请根据这个候选生成图片：\n\n${prompt}`);
        return;
      }
      await requestGenerate(prompt, {
        aspect: candidate.aspect,
        chatOpenCodeSessionId: chatSessionId(),
        promptCandidateId: candidate.id || "",
        promptCandidateTitle: candidate.title || "",
      });
    }
  };
  const sendAgentChatPayload = async (value, options = {}) => {
    if (!value || agentBusy()) return;
    const imageGenerationIntent = options.intent === "generate_image" || looksLikeImageGenerationRequest(value);
    if (settings().confirmBeforeGenerate !== false && imageGenerationIntent && !options.confirmed) {
      setPendingAgentGeneration({ value, options: { ...options, confirmed: true } });
      return;
    }
    const submittedReferences = referencePayloadItems();
    const submitKey = agentChatSubmitKey(value, options, submittedReferences);
    if (agentChatSubmitInFlightKey === submitKey) return;
    agentChatSubmitInFlightKey = submitKey;
    setPendingAgentGeneration(null);
    const localId = `local-${Date.now()}`;
    const part = { id: `${localId}-part`, messageID: localId, type: "text", text: value };
    setAgentMessages((current) => normalizeChatMessages([...current, { info: { id: localId, role: "user", time: { created: Date.now() } }, parts: [part], referenceAttachments: displayReferenceItemsForPayload(submittedReferences) }]));
    setPartsByMessageId((current) => ({ ...current, [localId]: { [part.id]: part } }));
    setAgentDraft("");
    setLastAppliedPromptBuilder(null);
    setAgentBusy("chat");
    setChatError("");
    try {
      const model = selectedChatModel();
      const response = await props.sendAgentChatMessage?.({
        message: value,
        provider: model.providerID || "",
        model: model.modelID || "",
        reference_images: submittedReferences,
        intent: imageGenerationIntent ? "generate_image" : (options.intent || ""),
        generation_intent: imageGenerationIntent,
        aspect: options.aspect || settings().aspect,
        count: options.count || settings().count || 1,
        prompt_candidate_id: options.promptCandidateId || "",
        prompt_candidate_title: options.promptCandidateTitle || "",
      });
      if (response?.chat_opencode_session_id !== undefined) setChatSessionId(String(response.chat_opencode_session_id || ""));
      if (response?.prompt_models) applyChatPromptModels(response.prompt_models);
    } catch (err) {
      setChatError(err instanceof Error ? err.message : String(err));
      setAgentBusy("");
    } finally {
      if (agentChatSubmitInFlightKey === submitKey) agentChatSubmitInFlightKey = "";
    }
  };
  const sendAgentChat = async () => sendAgentChatPayload(agentDraft().trim(), {});
  const cancelPendingImageGeneration = () => setPendingImageGeneration(null);
  const confirmPendingImageGeneration = async () => {
    const pending = pendingImageGeneration();
    if (!pending || busy()) return;
    await requestGenerate(pending.prompt, { ...(pending.source || {}), confirmed: true });
  };
  const cancelPendingAgentGeneration = () => setPendingAgentGeneration(null);
  const confirmPendingAgentGeneration = async () => {
    const pending = pendingAgentGeneration();
    if (!pending || agentBusy()) return;
    await sendAgentChatPayload(pending.value, pending.options || {});
  };
  const abortChat = async () => {
    if (agentBusy() !== "chat") return;
    try {
      await props.abortAgentChat?.();
    } catch (err) {
      setChatError(err instanceof Error ? err.message : String(err));
    } finally {
      setAgentBusy("");
    }
  };
  createEffect(() => {
    if (isOpenCodeWorkspace()) void openAgentChat();
  });
  const resolveReferenceDisplayItems = (items = []) => (items || [])
    .filter((item) => item?.path)
    .map((item) => {
      const asset = assetByPath().get(item.path) || item;
      return {
        ...item,
        label: item.label || shortPathLabel(item.path),
        imageUrl: item.imageUrl || props.imageUrl?.(asset),
        thumbnailUrl: item.thumbnailUrl || props.thumbnailUrl?.(asset) || item.imageUrl || props.imageUrl?.(asset),
      };
    });
  const messageImageDisplayUrl = (message) => {
    const path = message?.path || message?.asset?.path || "";
    return path ? (props.thumbnailUrl?.({ ...(message?.asset || {}), path }) || message.imageUrl) : message.imageUrl;
  };
  const resultFilename = (message) => {
    const raw = rawMessageText(message);
    const match = raw.match(/(?:已|已经)生成并保存到\s*Upload\s*[:：]\s*([^\n]+)/i);
    return shortPathLabel(match?.[1] || message.filename || message.path || "Agent generated image");
  };
  const copyText = async (value) => {
    const text = String(value || "");
    if (!text) return false;
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(textarea);
    return ok;
  };
  const toggleResultVote = (id, vote) => {
    setResultActionState((current) => {
      const previous = current[id] || {};
      const active = previous.vote === vote ? "" : vote;
      return { ...current, [id]: { ...previous, vote: active } };
    });
  };
  const markResultReported = (id) => {
    setResultActionState((current) => ({ ...current, [id]: { ...(current[id] || {}), reported: true } }));
  };
  const copyResultPath = async (id, value) => {
    try {
      const ok = await copyText(value);
      if (!ok) return;
      setCopiedResultId(id);
      window.setTimeout(() => setCopiedResultId((current) => current === id ? "" : current), 1400);
    } catch (err) {
      setChatError(err instanceof Error ? err.message : String(err));
    }
  };
  const toggleThinking = (id) => {
    setExpandedThinkingByMessageId((current) => ({ ...current, [id]: !current[id] }));
  };
  const renderReferenceStrip = (items, variant = "") => {
    const refs = resolveReferenceDisplayItems(items).slice(0, 5);
    return <Show when={refs.length}>
      <div class={`ual-message-reference-strip ${variant}`}>
        <For each={refs}>{(item, index) => (
          <figure class="ual-message-reference" title={`${referenceRoleLabel(item.role)} · ${item.path}`}>
            <Show when={item.thumbnailUrl || item.imageUrl} fallback={<span>{shortPathLabel(item.label).slice(0, 2).toUpperCase()}</span>}>
              <img src={item.thumbnailUrl || item.imageUrl} alt="" onLoad={scrollAgentChatToBottom} />
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
  const formatDebugText = (value) => {
    const source = String(value || "").trim();
    if (!source) return "";
    try {
      return JSON.stringify(JSON.parse(source), null, 2);
    } catch {
      // Continue with line-by-line formatting for mixed debug blocks.
    }
    return source
      .split(/\n{2,}/)
      .map((block) => {
        const text = block.trim();
        if (!text) return "";
        try {
          return JSON.stringify(JSON.parse(text), null, 2);
        } catch {
          return text;
        }
      })
      .filter(Boolean)
      .join("\n\n");
  };
  const renderDebugDetails = (parsed) => <Show when={parsed.debug}>
    <details class="ual-message-debug">
      <summary>Details</summary>
      <pre><code>{formatDebugText(parsed.debug)}</code></pre>
    </details>
  </Show>;
  const renderResultActions = (message) => {
    const id = messageId(message) || `result-${messageTime(message)}`;
    const state = () => resultActionState()[id] || {};
    const copyValue = message.path || resultFilename(message);
    return <div class="ual-result-actions" aria-label="Image result actions">
      <button type="button" class={state().vote === "like" ? "is-active" : ""} title="Like" aria-label="Like" onClick={() => toggleResultVote(id, "like")}><FlowIcon name="thumbUp" /></button>
      <button type="button" class={state().vote === "dislike" ? "is-active" : ""} title="Dislike" aria-label="Dislike" onClick={() => toggleResultVote(id, "dislike")}><FlowIcon name="thumbDown" /></button>
      <button type="button" class={copiedResultId() === id ? "is-active" : ""} title={copiedResultId() === id ? "Copied" : "Copy"} aria-label="Copy" onClick={() => void copyResultPath(id, copyValue)}><FlowIcon name="contentCopy" /></button>
      <button type="button" class={state().reported ? "is-active" : ""} title="Report" aria-label="Report" onClick={() => markResultReported(id)}><FlowIcon name="flag" /></button>
    </div>;
  };
  const previewMessageImage = (message) => {
    if (!message.imageUrl) return;
    props.onPreview?.({
      ...(message.asset || {}),
      path: message.path || message.asset?.path || "",
      src: message.imageUrl,
      filename: resultFilename(message),
      label: resultFilename(message),
    });
  };
  const renderResultCard = (message) => <Show when={message.imageUrl}>
    <section class="ual-result-card">
      <button type="button" class="ual-result-preview" title="Preview" onClick={() => previewMessageImage(message)}>
        <img class="ual-message-image" src={messageImageDisplayUrl(message)} alt="" onLoad={scrollAgentChatToBottom} />
      </button>
      <div class="ual-result-meta">
        <strong>已保存到 Upload</strong>
        <span>{resultFilename(message)}</span>
      </div>
      {renderResultActions(message)}
    </section>
  </Show>;
  const richTextBlocks = (value) => {
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
  };
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
  const renderAgentMessage = (message) => {
    const role = messageRole(message);
    const parsed = parseAgentDisplay(message);
    const candidates = extractPromptCandidates(message);
    const references = role === "user" ? (message.referenceAttachments || []) : parsed.references;
    const visible = Boolean(message.imagePlaceholder || message.imageUrl || parsed.text || parsed.thinking || candidates.length || references.length);
    return <Show when={visible}>
      <article class={`ual-message is-${role}`}>
        <Show when={role === "user"}>
          <div class="ual-user-bubble">
            {renderReferenceStrip(references, "is-compact")}
            <Show when={parsed.text}><p>{parsed.text}</p></Show>
          </div>
        </Show>
        <Show when={role !== "user"}>
          {renderThinking(message, parsed)}
          {renderReferenceStrip(references)}
          <Show when={message.imagePlaceholder}>
            <div class={`ual-message-image-placeholder is-${shapeFromAspect(message.aspectRatio)} ${message.failed ? "is-failed" : ""}`}>
              <span>{message.progressLabel}</span>
            </div>
          </Show>
          {renderResultCard(message)}
          <Show when={parsed.text}>
            <div class="ual-assistant-bubble">
              {renderFormattedText(parsed.text)}
            </div>
          </Show>
          <Show when={candidates.length}>
            <div class="ual-prompt-candidates">
              <For each={candidates}>{(candidate) => (
                <section class="ual-prompt-candidate">
                  <header>
                    <strong>{candidate.title}</strong>
                    <span>{candidate.aspect}</span>
                  </header>
                  <p>{candidate.prompt}</p>
                  <div>
                    <button type="button" disabled={Boolean(busy())} onClick={() => void applyPromptCandidate(candidate, "draft")}>填入草稿</button>
                    <Show when={!isOpenCodeWorkspace()}>
                      <button type="button" class="is-primary" disabled={Boolean(busy())} onClick={() => void applyPromptCandidate(candidate, "generate")}>直接生成</button>
                    </Show>
                  </div>
                </section>
              )}</For>
            </div>
          </Show>
          <Show when={!message.imageUrl}>
            {renderDebugDetails(parsed)}
          </Show>
        </Show>
      </article>
    </Show>;
  };
  const renderReferencePreviews = () => (
    <Show when={referenceItems().length}>
      <div class="ual-composer-references" aria-label="Reference images">
        <For each={referenceItems()}>{(asset) => (
          <figure class="ual-composer-reference">
            <img src={props.thumbnailUrl?.(asset) || props.imageUrl?.(asset)} alt="" />
            <span class="ual-composer-reference-role">{referenceRoleLabel(referenceRoleByPath().get(asset?.path) || "REFERENCE_IMAGE")}</span>
            <button type="button" aria-label="Remove reference" onClick={() => removeReferenceAsset(asset)}><FlowIcon name="close" /></button>
          </figure>
        )}</For>
      </div>
    </Show>
  );
  const renderConsistencyPicker = () => (
    <Show when={consistencyPickerOpen()}>
      <section class="ual-consistency-picker" role="dialog" aria-label="Consistency reference picker">
        <header>
          <strong>Reference</strong>
          <button type="button" aria-label="Close consistency reference picker" onClick={() => setConsistencyPickerOpen(false)}><FlowIcon name="close" /></button>
        </header>
        <Show when={!consistencyLoading()} fallback={<p class="ual-consistency-empty">Loading...</p>}>
          <Show when={consistencyReferences().length} fallback={<p class="ual-consistency-empty">当前 Session 还没有可用的一致性参考图。</p>}>
            <div class="ual-consistency-grid">
              <For each={consistencyReferences()}>{(item) => {
                const selected = () => selectedConsistencyKeys().has(item.key);
                return (
                  <button type="button" class={`ual-consistency-option ${selected() ? "is-selected" : ""}`} aria-pressed={selected()} onClick={() => toggleConsistencyReference(item)}>
                    <img src={props.thumbnailUrl?.(item) || props.imageUrl?.(item)} alt="" onError={() => dropMissingConsistencyReference(item)} />
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
  const renderReferenceFileInput = () => <input ref={referenceFileInput} type="file" accept="image/png,image/jpeg,image/webp" multiple hidden onChange={chooseReferenceFiles} />;
  const renderOpenCodeAgent = () => <section class={`ual-opencode-agent ${isOpenCodeWorkspace() ? "is-workspace" : ""}`} role="dialog" aria-label={isOpenCodeWorkspace() ? "图像智能体" : "OpenCode Agent"}>
    <header class="ual-agent-header">
      <div class="ual-agent-title">
        <button type="button" class="ual-agent-icon" aria-label="Menu"><FlowIcon name="menu" /></button>
        <strong>Workspace</strong>
        <Show when={!isOpenCodeWorkspace()}>
          <p>优化生图提示词，不接管原创意助手。</p>
        </Show>
      </div>
      <button
        type="button"
        class="ual-agent-icon"
        aria-label={isOpenCodeWorkspace() ? "Close" : "Close OpenCode Agent"}
        onClick={() => isOpenCodeWorkspace() ? props.onClose?.() : setAgentOpen(false)}
      ><FlowIcon name="close" /></button>
    </header>
    <section ref={(el) => { agentChatScrollEl = el; }} class="ual-opencode-agent-chat">
      <Show when={chatError()}>
        <article class="ual-message is-assistant is-error"><p>{chatError()}</p></article>
      </Show>
      <Show when={agentMessages().length} fallback={
        <article class="ual-message is-assistant">
          <p>告诉我你想生成的画面，我会用对话发起受控生图或给出可继续编辑的提示词候选。</p>
        </article>
      }>
        <For each={agentMessages()}>{(message) => renderAgentMessage(message)}</For>
      </Show>
      <div ref={(el) => { agentChatBottomEl = el; }} class="ual-chat-bottom-sentinel" aria-hidden="true" />
    </section>
    <footer class="ual-opencode-agent-composer">
      <Show when={pendingAgentGeneration()}>
        {(pending) => <div class="ual-agent-generate-confirm" role="alertdialog" aria-label="Confirm image generation">
          <div class="ual-agent-generate-confirm-head">
            <strong>Generate image?</strong>
            <div>
              <button type="button" onClick={cancelPendingAgentGeneration}>Cancel</button>
              <button type="button" class="is-primary" onClick={() => void confirmPendingAgentGeneration()}>Generate</button>
            </div>
          </div>
          <p>{pending().value}</p>
        </div>}
      </Show>
      <div
        class={`ual-composer-box ${referenceDragging() ? "is-reference-dragging" : ""}`}
        tabIndex="0"
        onPointerDown={(event) => focusComposerTextarea(agentComposerTextarea, event)}
        onDragEnter={handleReferenceDrag}
        onDragOver={handleReferenceDrag}
        onDragLeave={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget)) setReferenceDragging(false);
        }}
        onDrop={handleReferenceDrop}
        onPaste={handleReferencePaste}
      >
        {renderReferencePreviews()}
        <textarea
          ref={(el) => { agentComposerTextarea = el; }}
          value={agentDraft()}
          disabled={Boolean(agentBusy()) || Boolean(busy())}
          onInput={(event) => setAgentDraft(event.currentTarget.value)}
          onKeyDown={(event) => {
            if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
              event.preventDefault();
              void sendAgentChat();
            }
          }}
          placeholder={busy() === "upload" ? "Uploading references..." : agentBusy() === "chat" ? "Waiting for agent..." : "让 Agent 帮你优化生图提示词"}
        />
        <div class="ual-composer-tools">
          <button type="button" class="ual-composer-icon is-plus" disabled={Boolean(busy()) || Boolean(agentBusy()) || props.uploadBusy?.()} onClick={() => referenceFileInput?.click()} aria-label="Upload reference images"><FlowIcon name="add" /></button>
          <div>
            <button type="button" class="ual-composer-icon" disabled={Boolean(busy()) || Boolean(agentBusy())} aria-label="Load consistency reference images" title="加载人物/产品一致性参考图" onClick={openConsistencyPicker}><FlowIcon name="image" /></button>
            <button type="button" class="ual-composer-icon" disabled={Boolean(busy()) || Boolean(agentBusy())} aria-label="Prompt Builder" title="Prompt Builder" onClick={() => void openPromptBuilder({ target: "agent", draft: agentDraft() })}><FlowIcon name="addNotes" /></button>
            <button type="button" class="ual-composer-icon" disabled={Boolean(busy()) || Boolean(agentBusy())} aria-label="Settings" title="Settings" onClick={() => void openSettings("images-agent")}><FlowIcon name="tune" /></button>
            <button
              type="button"
              class="ual-composer-submit"
              disabled={Boolean(agentBusy()) || Boolean(busy()) || !agentDraft().trim()}
              onClick={() => void sendAgentChat()}
              aria-label="Send to OpenCode Agent"
            ><FlowIcon name="arrowForward" /></button>
          </div>
        </div>
      </div>
      {renderConsistencyPicker()}
      {renderReferenceFileInput()}
    </footer>
  </section>;
  if (isOpenCodeWorkspace()) {
    return <aside class="ual-agent is-opencode-workspace">
      {renderSettingsPanel()}
      <PromptBuilderModal
        open={promptBuilderOpen}
        loading={promptBuilderLoading}
        error={promptBuilderError}
        builder={promptBuilderPayload}
        currentDraft={promptBuilderCurrentDraft}
        onClose={() => setPromptBuilderOpen(false)}
        onSwitchToGrok={() => void switchToGrokModel()}
        onApply={(payload) => applyPromptBuilder(payload)}
      />
      {renderOpenCodeAgent()}
    </aside>;
  }
  return <aside
    class={`ual-agent ${referenceDragging() ? "is-reference-dragging" : ""}`}
    onDragEnter={handleReferenceDrag}
    onDragOver={handleReferenceDrag}
    onDragLeave={(event) => {
      if (!event.currentTarget.contains(event.relatedTarget)) setReferenceDragging(false);
    }}
    onDrop={handleReferenceDrop}
  >
    {renderSettingsPanel()}
    <PromptBuilderModal
      open={promptBuilderOpen}
      loading={promptBuilderLoading}
      error={promptBuilderError}
      builder={promptBuilderPayload}
      currentDraft={promptBuilderCurrentDraft}
      onClose={() => setPromptBuilderOpen(false)}
      onSwitchToGrok={() => void switchToGrokModel()}
      onApply={(payload) => applyPromptBuilder(payload)}
    />
    <Show when={agentOpen()}>
      {renderOpenCodeAgent()}
    </Show>
    <header class="ual-agent-header">
      <div class="ual-agent-title">
        <button type="button" class="ual-agent-icon" aria-label="Menu"><FlowIcon name="menu" /></button>
        <strong>Workspace</strong>
      </div>
      <div class="ual-agent-actions">
        <Show when={showOpenCodeEntry()}>
          <button type="button" class="ual-agent-icon" aria-label="OpenCode Agent" title="OpenCode Agent" onClick={() => void openAgentChat()}><FlowIcon name="addNotes" /></button>
        </Show>
        <button type="button" class="ual-agent-icon" aria-label="Close" onClick={() => props.onClose?.()}><FlowIcon name="close" /></button>
      </div>
    </header>
    <section ref={(el) => { chatScrollEl = el; }} class="ual-agent-chat">
      <For each={messages()}>{(message) => <article class={`ual-message is-${message.role}`}>
          <Show when={message.imagePlaceholder}>
            <div class={`ual-message-image-placeholder is-${shapeFromAspect(message.aspectRatio)} ${message.failed ? "is-failed" : ""}`}>
              <span>{message.progressLabel}</span>
            </div>
          </Show>
          <Show when={message.imageUrl}>
            <button type="button" class="ual-result-preview" title="Preview" onClick={() => previewMessageImage(message)}>
              <img class="ual-message-image" src={message.imageUrl} alt="" onLoad={scrollChatToBottom} />
            </button>
          </Show>
          <p>{message.text}</p>
          <Show when={message.suggestions?.length}>
            <div class="ual-agent-suggestions">
              <For each={message.suggestions}>{(item) => (
                <button type="button" onClick={() => setSuggestion(item)}>
                  <FlowIcon name="radioButtonUnchecked" />
                  {item}
                </button>
              )}</For>
            </div>
          </Show>
        </article>}</For>
      <div ref={(el) => { chatBottomEl = el; }} class="ual-chat-bottom-sentinel" aria-hidden="true" />
    </section>
    <footer class="ual-agent-composer">
      <Show when={pendingImageGeneration()}>
        {(pending) => <div class="ual-agent-generate-confirm" role="alertdialog" aria-label="Confirm image generation">
          <div class="ual-agent-generate-confirm-head">
            <strong>Generate image?</strong>
            <div>
              <button type="button" onClick={cancelPendingImageGeneration}>Cancel</button>
              <button type="button" class="is-primary" onClick={() => void confirmPendingImageGeneration()}>Generate</button>
            </div>
          </div>
          <p>{pending().prompt}</p>
        </div>}
      </Show>
      <div
        class={`ual-composer-box ${referenceDragging() ? "is-reference-dragging" : ""}`}
        tabIndex="0"
        onPointerDown={(event) => focusComposerTextarea(composerTextarea, event)}
        onDragEnter={handleReferenceDrag}
        onDragOver={handleReferenceDrag}
        onDragLeave={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget)) setReferenceDragging(false);
        }}
        onDrop={handleReferenceDrop}
        onPaste={handleReferencePaste}
      >
        {renderReferencePreviews()}
        <textarea
          ref={(el) => { composerTextarea = el; }}
          value={draft()}
          disabled={Boolean(busy())}
          onInput={(event) => {
            setDraft(event.currentTarget.value);
            setLastAppliedPromptBuilder(null);
          }}
          onKeyDown={(event) => {
            if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
              event.preventDefault();
              void generate();
            }
          }}
          placeholder={busy() === "generate" ? "Generating..." : busy() === "upload" ? "Uploading references..." : "What do you want to create?"}
        />
        <div class="ual-composer-tools">
          <button type="button" class="ual-composer-icon is-plus" disabled={Boolean(busy()) || props.uploadBusy?.()} onClick={() => referenceFileInput?.click()} aria-label="Upload reference images"><FlowIcon name="add" /></button>
          <div>
            <button type="button" class="ual-composer-icon" disabled={Boolean(busy())} aria-label="Load consistency reference images" title="加载人物/产品一致性参考图" onClick={openConsistencyPicker}><FlowIcon name="image" /></button>
            <button type="button" class="ual-composer-icon" disabled={Boolean(busy())} aria-label="Prompt Builder" title="Prompt Builder" onClick={() => void openPromptBuilder({ target: "default", draft: draft() })}><FlowIcon name="addNotes" /></button>
            <button type="button" class="ual-composer-icon" aria-label="Settings" onClick={() => void openSettings("image-api")}><FlowIcon name="tune" /></button>
            <button type="button" class="ual-composer-submit" disabled={!draft().trim() || Boolean(busy())} onClick={generate} aria-label="Generate"><FlowIcon name="arrowForward" /></button>
          </div>
        </div>
        {renderConsistencyPicker()}
        {renderReferenceFileInput()}
      </div>
    </footer>
  </aside>;
}
