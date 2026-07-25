import { For, Show, createEffect, createSignal } from "solid-js";
import { emitDebugError } from "../../../../debug/debugAdapter.js";
import FlowIcon from "../components/FlowIcon.jsx";
import AvatarPickerModal from "./AvatarPickerModal.jsx";
import VoicePickerModal from "./VoicePickerModal.jsx";
import DigitalHumanSettingsPanel from "./DigitalHumanSettingsPanel.jsx";
import { DEFAULT_DIGITAL_HUMAN_SETTINGS, normalizeAvatar, text } from "./digitalHumanModel.js";

const VIDEO_AGENT_REVISION_PREFIX = "不要生成视频，只修改计划";

function Chip(props) {
  return <div class={`dh-chip is-${props.kind || "item"}`}>
    <Show when={props.image}>
      <div class="dh-chip-preview">
        <img src={props.image} alt="" />
        <Show when={props.badge}><em>{props.badge}</em></Show>
      </div>
    </Show>
    <Show when={props.audio}>
      <div class="dh-chip-audio">
        <Show when={props.badge}><em>{props.badge}</em></Show>
        <audio src={props.audio} controls preload="none" />
      </div>
    </Show>
    <Show when={!props.image && !props.audio}>
      <div class="dh-chip-label">
        <Show when={props.badge}><em>{props.badge}</em></Show>
        <Show when={props.label}><span>{props.label}</span></Show>
      </div>
    </Show>
    <button type="button" aria-label="Remove" onClick={(event) => {
      event.preventDefault();
      event.stopPropagation();
      props.onRemove?.();
    }}><FlowIcon name="close" /></button>
  </div>;
}

function basename(value) {
  return String(value || "").split(/[\\/]/).filter(Boolean).pop() || "";
}

function compactAssetLabel(value, fallback) {
  const raw = text(value, "");
  const name = basename(raw) || raw || fallback;
  if (!name) return fallback;
  if (name.length <= 32) return name;
  const dot = name.lastIndexOf(".");
  const ext = dot > 0 && name.length - dot <= 8 ? name.slice(dot) : "";
  const body = ext ? name.slice(0, dot) : name;
  return `${body.slice(0, 14)}...${body.slice(-8)}${ext}`;
}

function requestLine(label, value, fallback = "-") {
  const content = text(value, fallback);
  return `${label}: ${content || fallback}`;
}

function revisionOnlyPrompt(value) {
  const content = text(value);
  if (!content) return "";
  return content.startsWith(VIDEO_AGENT_REVISION_PREFIX) ? content : `${VIDEO_AGENT_REVISION_PREFIX}\n${content}`;
}

function arrayItems(value) {
  return Array.isArray(value) ? value : [];
}

function avatarSupportsEngine(item, engineType) {
  const engine = text(engineType, DEFAULT_DIGITAL_HUMAN_SETTINGS.engine_type).toLowerCase();
  if (engine !== "avatar_v") return true;
  const avatarType = text(item?.avatar_type).toLowerCase();
  const supported = arrayItems(item?.supported_api_engines).map((value) => text(value).toLowerCase()).filter(Boolean);
  if (supported.length && !supported.includes("avatar_v")) return false;
  return !avatarType || avatarType === "digital_twin";
}

function compatibleAvatarEngine(settings, item) {
  const selected = text(settings?.generation_model || settings?.engine_type, DEFAULT_DIGITAL_HUMAN_SETTINGS.engine_type).toLowerCase();
  if (selected === "video_agent") return "video_agent";
  if (selected === "avatar_v" && !avatarSupportsEngine(item, "avatar_v")) return "avatar_iv";
  return selected === "avatar_v" ? "avatar_v" : "avatar_iv";
}

function avatarModelName(engineType) {
  if (engineType === "video_agent") return "Cloud Video Agent";
  return engineType === "avatar_v" ? "Avatar V" : "Avatar IV";
}

function messageRoleLabel(message) {
  const role = text(message?.role, "agent").toLowerCase();
  if (role === "user") return "You";
  if (role === "model" || role === "assistant" || role === "agent") return "Agent";
  return role;
}

function messageTime(message) {
  const raw = Number(message?.created_at || message?.createdAt || 0);
  return Number.isFinite(raw) ? raw : 0;
}

function normalizedContent(value) {
  return text(value).replace(/\s+/g, " ").trim();
}

function dedupeContent(value) {
  const content = text(value);
  const withoutPrefix = content.startsWith(VIDEO_AGENT_REVISION_PREFIX)
    ? content.slice(VIDEO_AGENT_REVISION_PREFIX.length)
    : content;
  return normalizedContent(withoutPrefix);
}

function messageDedupeKey(role, content) {
  return `${String(role || "").toLowerCase()}|${dedupeContent(content)}`;
}

function messageTextValue(message) {
  return text(message?.content || message?.text || message?.message);
}

function chronologicalMessages(messages) {
  const seen = new Set();
  return [...arrayItems(messages)]
    .sort((a, b) => messageTime(a) - messageTime(b))
    .filter((message) => {
      const key = messageDedupeKey(message?.role, messageTextValue(message));
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
}

function buildResourceIndex(resources) {
  const index = new Map();
  for (const resource of arrayItems(resources)) {
    const keys = [
      resource?.client_resource_id,
      resource?.source_resource_id,
      resource?.request_resource_id,
      resource?.resource_id,
      resource?.id,
    ];
    for (const key of keys) {
      const value = text(key);
      if (value) index.set(value, resource);
    }
  }
  return index;
}

function sessionTimeline(messages, localEvents = []) {
  const items = [];
  const seenMessages = new Map();
  const pushMessage = (role, content, at) => {
    const normalized = normalizedContent(content);
    if (!normalized) return;
    const key = messageDedupeKey(role, content);
    if (seenMessages.has(key)) {
      const index = seenMessages.get(key);
      if (!items[index]?.text?.startsWith(VIDEO_AGENT_REVISION_PREFIX) && text(content).startsWith(VIDEO_AGENT_REVISION_PREFIX)) {
        items[index] = { ...items[index], text: content };
      }
      return;
    }
    seenMessages.set(key, items.length);
    items.push({ type: "message", role, text: content, at });
  };
  for (const message of chronologicalMessages(messages)) {
    const content = messageTextValue(message);
    if (content) pushMessage(messageRoleLabel(message), content, messageTime(message));
  }
  for (const event of arrayItems(localEvents)) {
    if (event?.role === "assistant" && event?.progress) {
      const progressText = event.progress === "0%" ? event.text : `${event.text}\n${event.progress}`;
      pushMessage("Agent", progressText, Number(event.created_at || 0));
    } else {
      pushMessage(messageRoleLabel(event), messageTextValue(event), Number(event?.created_at || 0));
    }
  }
  return items.sort((a, b) => (Number(a.at) || 0) - (Number(b.at) || 0));
}

function blueprintTextFromSession(messages, resources, planText) {
  const resourceIndex = buildResourceIndex(resources);
  for (const message of chronologicalMessages(messages)) {
    for (const resourceId of arrayItems(message?.resource_ids)) {
      const resource = resourceIndex.get(text(resourceId)) || { resource_id: resourceId };
      if (text(resource?.resource_type).toLowerCase() !== "blueprint") continue;
      const content = text(resource?.metadata?.description || resource?.metadata?.summary || resource?.metadata?.title || planText);
      if (content) return content;
    }
  }
  return text(planText);
}

function videoAgentErrorMessage(err, action) {
  const message = err instanceof Error ? err.message : String(err || "");
  if (message === "Not Found" || /request failed \(404\)/i.test(message)) {
    if (action === "refresh") return "刷新接口返回 404：当前后端未加载视频智能体会话路由，或云端服务已找不到这个会话。请重启 backend 后再刷新。";
  }
  if (/video-agents.*HTTP 404/i.test(message)) return "云端服务已找不到当前视频智能体会话，可能是会话已失效或服务端会话标识不再可用。当前页面会保留已有蓝图和对话。";
  return message;
}

export default function DigitalHumanAgentPanel(props) {
  let uploadInput;
  const taskId = () => Number(props.task?.()?.id || 0);
  const [prompt, setPrompt] = createSignal("");
  const [messages, setMessages] = createSignal([{ role: "assistant", text: "你好，我可以帮你创建数字人 Avatar、克隆声音，并生成数字人口播视频。" }]);
  const [settings, setSettings] = createSignal({ ...DEFAULT_DIGITAL_HUMAN_SETTINGS });
  const [avatar, setAvatar] = createSignal(null);
  const [avatarPhoto, setAvatarPhoto] = createSignal(null);
  const [voice, setVoice] = createSignal(null);
  const [audioFile, setAudioFile] = createSignal(null);
  const [avatarOpen, setAvatarOpen] = createSignal(false);
  const [voiceOpen, setVoiceOpen] = createSignal(false);
  const [settingsOpen, setSettingsOpen] = createSignal(false);
  const [busy, setBusy] = createSignal(false);
  const [error, setError] = createSignal("");
  const [confirmPending, setConfirmPending] = createSignal(null);
  const [agentSession, setAgentSession] = createSignal(null);
  const [agentRefreshing, setAgentRefreshing] = createSignal(false);
  const [agentControlBusy, setAgentControlBusy] = createSignal(false);
  const [settingsLoadedFor, setSettingsLoadedFor] = createSignal(0);
  const [referenceDragging, setReferenceDragging] = createSignal(false);

  createEffect(() => {
    const id = taskId();
    if (!id) return;
    setSettingsLoadedFor(0);
    void (async () => {
      try {
        const payload = await props.api.assetLibraryDigitalHumanSettings(id);
        if (payload?.settings) setSettings({ ...DEFAULT_DIGITAL_HUMAN_SETTINGS, ...payload.settings });
        setSettingsLoadedFor(id);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    })();
  });

  createEffect(() => {
    const item = props.selectedAvatarImage?.();
    if (!item?.path) return;
    selectAvatarPhoto(item);
  });

  createEffect(() => {
    if ((settings().generation_model || settings().engine_type) !== "video_agent") setAgentSession(null);
  });

  createEffect(() => {
    const item = props.selectedAudio?.();
    const key = item?.id || item?.path || "";
    if (!key) return;
    const currentKey = audioFile()?.id || audioFile()?.path || "";
    if (currentKey === key) return;
    selectAudio(item);
  });

  createEffect(() => {
    const id = taskId();
    if (!id) return;
    if (settingsLoadedFor() !== id) return;
    const next = settings();
    void props.api.saveAssetLibraryDigitalHumanSettings(id, next).catch((err) => emitDebugError(err, {
      family: "digital_human_settings",
      task_id: id,
      detail: "Save digital-human settings failed",
    }));
  });

  function selectAvatar(item) {
    props.onClearSelectedAvatarImage?.();
    setAvatar(item);
    setAvatarPhoto(null);
    setSettings((current) => {
      const engineType = compatibleAvatarEngine(current, item);
      return {
        ...current,
        generation_model: engineType,
        model_name: avatarModelName(engineType),
        engine_type: engineType,
        selected_avatar_id: item?.id || "",
      };
    });
  }

  function selectAvatarPhoto(item) {
    setAvatarPhoto(item);
    setAvatar(null);
    setSettings((current) => ({
      ...current,
      generation_model: "avatar_iv",
      model_name: "Avatar IV",
      engine_type: "avatar_iv",
      selected_avatar_id: "",
    }));
  }

  function selectVoice(item) {
    props.onClearSelectedAudio?.();
    setVoice(item);
    setAudioFile(null);
    setSettings((current) => ({ ...current, selected_voice_id: item?.voice_id || item?.id || "", selected_audio_asset_path: "", generation_mode: "voice_script" }));
  }

  function selectAudio(item) {
    setAudioFile(item);
    setVoice(null);
    setSettings((current) => ({ ...current, selected_voice_id: "", selected_audio_asset_path: item?.path || "", generation_mode: item?.path ? "audio_file" : "voice_script" }));
  }

  function removeAvatarPhoto() {
    props.onClearSelectedAvatarImage?.();
    selectAvatarPhoto(null);
  }

  function removeAudioFile() {
    props.onClearSelectedAudio?.();
    selectAudio(null);
  }

  function validationMessage(payload) {
    if (payload.generation_model !== "video_agent" && !payload.avatar_id && !payload.avatar_photo_path) return "请先选择 Avatar，或从 Images 拖入一张照片。";
    if (payload.generation_mode === "audio_file") {
      if (payload.generation_model === "video_agent") return "Video Agent Chat 暂不使用声音文件驱动，请选择 Voice ID 或只输入需求。";
      if (!payload.audio_asset_path) return "请先选择声音文件。";
      return "";
    }
    if (payload.generation_model !== "video_agent" && !payload.voice_id) return "请先选择或克隆声音。";
    if (!payload.prompt) return "请输入口播脚本或视频需求。";
    return "";
  }

  function buildPayload() {
    const s = settings();
    const audioPath = audioFile()?.path || "";
    const mode = audioPath ? "audio_file" : "voice_script";
    const activeAgentSession = agentSession();
    const generationModel = text(s.generation_model || s.engine_type, DEFAULT_DIGITAL_HUMAN_SETTINGS.generation_model).toLowerCase() || DEFAULT_DIGITAL_HUMAN_SETTINGS.generation_model;
    const hasPhotoAvatarInput = Boolean(avatarPhoto()?.path || props.selectedAvatarImage?.()?.path);
    const engineType = generationModel === "video_agent" ? "video_agent" : hasPhotoAvatarInput ? "avatar_iv" : compatibleAvatarEngine(s, avatar());
    const modelName = avatarModelName(engineType);
    const promptText = prompt().trim();
    return {
      prompt: engineType === "video_agent" ? revisionOnlyPrompt(promptText) : promptText,
      generation_model: engineType,
      model_name: modelName,
      engine_type: engineType,
      provider_session_id: engineType === "video_agent" ? text(activeAgentSession?.id || activeAgentSession?.provider_session_id) : "",
      agent_confirm_generate: false,
      avatar_id: avatar()?.id || s.selected_avatar_id || "",
      avatar_group_id: avatar()?.group_id || "",
      avatar_photo_path: avatarPhoto()?.path || props.selectedAvatarImage?.()?.path || "",
      supported_api_engines: arrayItems(avatar()?.supported_api_engines),
      voice_id: voice()?.voice_id || voice()?.id || s.selected_voice_id || "",
      audio_asset_path: audioPath,
      generation_mode: mode,
      aspect: s.aspect || "9:16",
      count: Number(s.count || 1),
      title: promptText.slice(0, 80) || "Digital Human",
      avatar_type: avatar()?.avatar_type || "",
      motion_prompt: s.motion_prompt_enabled === true ? text(s.motion_prompt) : "",
      requested_engine_type: generationModel === "video_agent" ? "video_agent" : generationModel === "avatar_v" ? "avatar_v" : "avatar_iv",
      expressiveness: engineType === "avatar_iv" ? text(s.expressiveness, DEFAULT_DIGITAL_HUMAN_SETTINGS.expressiveness).toLowerCase() : "",
    };
  }

  function applyAgentResult(result) {
    if (!result?.provider_session_id) return;
    const finalAssets = result.assets || [result.asset].filter(Boolean);
    setAgentSession({
      id: result.provider_session_id,
      provider_session_id: result.provider_session_id,
      status: finalAssets.length ? "completed" : result.agent_status || result.provider_result?.status || "reviewing",
      progress: result.agent_progress ?? result.provider_result?.progress,
      title: result.agent_title || result.provider_result?.title || "",
      video_id: result.provider_video_id || result.provider_result?.video_id || result.provider_result?.id || "",
      messages: chronologicalMessages(result.agent_messages || result.agent_snapshot?.messages),
      resources: arrayItems(result.agent_resources || result.agent_snapshot?.resources),
      plan_text: text(result.plan_text || result.agent_snapshot?.plan_text),
      record_path: result.record_path || "",
      provider_result: result.provider_result || {},
      request: result.request || {},
      send_result: result.send_result || {},
      agent_snapshot: result.agent_snapshot || {},
      raw_result: result,
      aspect: result.aspect || result.request?.orientation || settings().aspect || "",
      avatar_id: result.avatar_id || result.request?.avatar_id || "",
      voice_id: result.voice_id || result.request?.voice_id || "",
      outputs: arrayItems(result.outputs),
      assets: finalAssets,
    });
  }

  function notifyGeneratedAssets(result) {
    const generatedAssets = arrayItems(result?.assets || [result?.asset].filter(Boolean));
    if (generatedAssets.length) props.onGenerated?.({ ...result, assets: generatedAssets, asset: generatedAssets[0], generated_count: generatedAssets.length });
  }

  async function refreshAgentSession() {
    const id = taskId();
    const sessionId = text(agentSession()?.provider_session_id || agentSession()?.id);
    if (!id || !sessionId) return;
    setAgentRefreshing(true);
    setError("");
    try {
      const result = await props.api.assetLibraryDigitalHumanAgentSession(id, sessionId);
      applyAgentResult(result);
      notifyGeneratedAssets(result);
    } catch (err) {
      setError(videoAgentErrorMessage(err, "refresh"));
    } finally {
      setAgentRefreshing(false);
    }
  }

  function assetFromDataTransfer(dataTransfer) {
    const raw = dataTransfer?.getData?.("application/x-koubo-storyboard-asset") || "";
    if (!raw) return null;
    try {
      const asset = JSON.parse(raw);
      return asset && typeof asset === "object" ? asset : null;
    } catch {
      return null;
    }
  }

  function isImageAsset(asset) {
    const kind = String(asset?.kind || asset?.asset_type || "").toLowerCase();
    const path = String(asset?.path || asset?.filename || "").toLowerCase();
    return kind.includes("image") || /\.(png|jpe?g|webp)$/.test(path);
  }

  const dragHasAsset = (event) => Array.from(event.dataTransfer?.types || []).includes("application/x-koubo-storyboard-asset");

  function handleReferenceDrag(event) {
    if (!dragHasAsset(event) || busy()) return;
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = "copy";
    setReferenceDragging(true);
  }

  function handleReferenceDrop(event) {
    if (!dragHasAsset(event) || busy()) return;
    event.preventDefault();
    event.stopPropagation();
    setReferenceDragging(false);
    const asset = assetFromDataTransfer(event.dataTransfer);
    if (!asset) return;
    if (!isImageAsset(asset)) {
      setError("请从下方 Images 拖入一张图片作为 Avatar 照片。");
      return;
    }
    selectAvatarPhoto(asset);
    setError("");
  }

  function avatarFromCreateResult(result, fallbackName) {
    return normalizeAvatar(result?.result?.data?.avatar_item || result?.result?.avatar_item || result?.result?.data || {
      id: result?.result?.data?.avatar_id || result?.result?.avatar_id,
      name: fallbackName,
    });
  }

  const imageUploadExt = /\.(png|jpe?g|webp)$/i;
  const videoUploadExt = /\.(mp4|mov|webm|m4v)$/i;
  const audioUploadExt = /\.(wav|m4a|mp3|aac|ogg|oga|flac|opus|aiff|aif|caf|weba|wma)$/i;

  function splitUploadFiles(files) {
    const groups = { images: [], videos: [], audios: [] };
    for (const file of Array.from(files || [])) {
      const type = String(file?.type || "").toLowerCase();
      const name = String(file?.name || "");
      if (type.startsWith("image/") || imageUploadExt.test(name)) groups.images.push(file);
      else if (type.startsWith("video/") || videoUploadExt.test(name)) groups.videos.push(file);
      else if (type.startsWith("audio/") || audioUploadExt.test(name)) groups.audios.push(file);
    }
    return groups;
  }

  async function uploadComposerFiles(files) {
    const groups = splitUploadFiles(files);
    const total = groups.images.length + groups.videos.length + groups.audios.length;
    if (!total) {
      setError("请选择图片、视频或音频素材。");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const uploadedImages = groups.images.length
        ? await props.uploadImageFiles?.(groups.images, { source: "digital_human_composer_plus" })
        : [];
      if (uploadedImages?.length) {
        const firstImage = uploadedImages.find((item) => item?.path);
        if (firstImage) selectAvatarPhoto(firstImage);
      }
      if (groups.videos.length) await props.uploadMediaFiles?.(groups.videos, "videos", { source: "digital_human_composer_plus" });
      const uploadedAudios = groups.audios.length
        ? await props.uploadMediaFiles?.(groups.audios, "audio", { source: "digital_human_composer_plus" })
        : [];
      if (uploadedAudios?.length) {
        const firstAudio = uploadedAudios.find((item) => item?.path);
        if (firstAudio) selectAudio({ id: firstAudio.id || firstAudio.path, path: firstAudio.path, name: firstAudio.label || firstAudio.filename || firstAudio.path, filename: firstAudio.filename, raw: firstAudio });
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function ensureAvatarForPayload(payload) {
    if (payload.avatar_id) return payload;
    if (!payload.avatar_photo_path) return payload;
    const photo = avatarPhoto();
    const avatarName = text(photo?.label || photo?.filename || photo?.path?.split("/")?.pop(), "Digital Human Avatar");
    setMessages((items) => [...items, { role: "assistant", text: `正在用图片创建云端数字人形象：${avatarName}` }]);
    const result = await props.api.createAssetLibraryDigitalHumanPhotoAvatar(taskId(), {
      name: avatarName,
      description: "Photo avatar generated from Asset Library image.",
      assetPath: payload.avatar_photo_path,
    });
    const createdAvatar = avatarFromCreateResult(result, avatarName);
    if (!createdAvatar.id) throw new Error("云端数字人形象创建完成，但响应中没有形象标识。");
    selectAvatar(createdAvatar);
    const engineType = compatibleAvatarEngine(payload, createdAvatar);
    return {
      ...payload,
      generation_model: engineType,
      model_name: avatarModelName(engineType),
      engine_type: engineType,
      avatar_id: createdAvatar.id,
      avatar_group_id: createdAvatar.group_id || "",
      avatar_type: createdAvatar.avatar_type || "photo_avatar",
      supported_api_engines: arrayItems(createdAvatar.supported_api_engines),
      avatar_photo_path: "",
      expressiveness: engineType === "avatar_iv" ? text(payload.expressiveness, DEFAULT_DIGITAL_HUMAN_SETTINGS.expressiveness).toLowerCase() : "",
    };
  }

  function generationRequestSummary(payload) {
    const avatarLabel = avatar()?.name || avatarPhoto()?.label || avatarPhoto()?.filename || payload.avatar_id || payload.avatar_photo_path || "Selected avatar";
    const voiceLabel = payload.generation_mode === "audio_file"
      ? (audioFile()?.name || audioFile()?.filename || payload.audio_asset_path)
      : (voice()?.name || payload.voice_id);
    const lines = [
      "云端服务请求参数",
      requestLine("调用模型", payload.model_name || DEFAULT_DIGITAL_HUMAN_SETTINGS.model_name),
      requestLine("engine.type", payload.engine_type || DEFAULT_DIGITAL_HUMAN_SETTINGS.engine_type),
      requestLine("agent.mode", payload.generation_model === "video_agent" ? "chat" : ""),
      requestLine("agent.session", payload.provider_session_id),
      requestLine("agent.auto_proceed", "false"),
      requestLine("模式", payload.generation_mode),
      requestLine("Avatar", avatarLabel),
      requestLine("avatar_id", payload.avatar_id, payload.avatar_photo_path ? "将先用图片创建 Avatar" : "-"),
      requestLine("avatar_type", payload.avatar_type),
      requestLine(payload.generation_mode === "audio_file" ? "Audio" : "Voice", voiceLabel),
      requestLine("voice_id", payload.generation_mode === "audio_file" ? "" : payload.voice_id),
      requestLine("audio_asset_path", payload.generation_mode === "audio_file" ? payload.audio_asset_path : ""),
      requestLine("aspect_ratio", payload.aspect),
      requestLine("count", `x${payload.count || 1}`),
      requestLine("motion_prompt", payload.motion_prompt, "不发送"),
      requestLine("expressiveness", payload.expressiveness, payload.engine_type === "avatar_iv" ? "-" : "不发送"),
      requestLine(payload.generation_mode === "audio_file" ? "附加动作提示" : "script", payload.prompt),
    ];
    return lines.join("\n");
  }

  async function runGenerate(payload) {
    const id = taskId();
    if (!id) return;
    setBusy(true);
    setError("");
    const clientId = `dh_${Date.now()}`;
    let generationStarted = false;
    let requestPayload = payload;
    const pendingId = `dh_${Date.now()}`;
    let completed = null;
    try {
      requestPayload = await ensureAvatarForPayload(payload);
      if (requestPayload.generation_model !== "video_agent") {
        setMessages((items) => [...items, { role: "user", text: generationRequestSummary(requestPayload) }]);
      } else {
        const userText = requestPayload.agent_confirm_generate ? "确认生成" : requestPayload.prompt;
        setMessages((items) => [...items, { role: "user", text: userText, agentEvent: true, created_at: Date.now() }]);
      }
      setMessages((items) => [...items, { id: pendingId, role: "assistant", text: requestPayload.generation_model === "video_agent" ? "正在请求，会先返回可审阅的 Plan..." : "正在通过云端服务生成数字人视频...", progress: "0%", agentEvent: requestPayload.generation_model === "video_agent", created_at: Date.now() + 1 }]);
      props.onGenerationStart?.({ ...requestPayload, client_id: clientId });
      generationStarted = true;
      await props.api.streamAssetLibraryDigitalHumanGenerate(id, requestPayload, (event) => {
        if (event?.type === "heartbeat") {
          const percent = Math.min(98, Math.max(8, Math.round(8 + Number(event.elapsed_seconds || 0) * 2)));
          setMessages((items) => items.map((item) => item.id === pendingId ? { ...item, progress: `${percent}%` } : item));
          props.onGenerationProgress?.({ ...requestPayload, ...event, client_id: clientId, progressLabel: `${percent}%` });
        }
        if (event?.type === "failed") {
          setMessages((items) => items.map((item) => item.id === pendingId ? { ...item, text: `生成失败：${event.detail || "云端服务失败"}`, failed: true, progress: "Failed" } : item));
          props.onGenerationFailed?.({ ...requestPayload, ...event, client_id: clientId });
        }
        if (event?.type === "completed") completed = event;
      });
      if (completed) {
        const generatedAssets = completed.assets || [completed.asset].filter(Boolean);
        if (completed.generation_model === "video_agent" && generatedAssets.length) {
          applyAgentResult(completed);
          props.onGenerated?.({ ...completed, client_id: clientId });
          setMessages((items) => items.map((item) => item.id === pendingId ? { ...item, text: `Video Agent 最终视频已生成：${completed.generated_count || 1} 个视频`, progress: "Done", assets: generatedAssets } : item));
          setPrompt("");
        } else if (completed.generation_model === "video_agent" || completed.agent_mode === "chat" || completed.provider_session_id) {
          applyAgentResult(completed);
          setMessages((items) => items.filter((item) => item.id !== pendingId));
          setPrompt("");
        } else {
          props.onGenerated?.({ ...completed, client_id: clientId });
          setMessages((items) => items.map((item) => item.id === pendingId ? { ...item, text: `生成完成：${completed.generated_count || 1} 个视频`, progress: "Done", assets: completed.assets || [completed.asset].filter(Boolean) } : item));
        }
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setError(message);
      setMessages((items) => items.map((item) => item.id === pendingId ? { ...item, text: `生成失败：${message}`, failed: true, progress: "Failed" } : item));
      if (generationStarted) props.onGenerationFailed?.({ ...requestPayload, client_id: clientId, detail: message });
    } finally {
      setBusy(false);
      setConfirmPending(null);
    }
  }

  function submit() {
    const payload = buildPayload();
    const invalid = validationMessage(payload);
    if (invalid) {
      setError(invalid);
      return;
    }
    if (payload.generation_model === "video_agent" && payload.provider_session_id) {
      void runGenerate(payload);
      return;
    }
    if (settings().confirm_before_generating !== "never") {
      setConfirmPending(payload);
      return;
    }
    void runGenerate(payload);
  }

  function confirmGenerate() {
    const payload = confirmPending();
    if (!payload) return;
    setConfirmPending(null);
    void runGenerate(payload);
  }

  function canConfirmAgentGenerate() {
    const session = agentSession();
    const status = text(session?.status).toLowerCase();
    const hasBlueprint = Boolean(currentBlueprintText());
    return Boolean(session)
      && !busy()
      && !agentControlBusy()
      && !currentAgentFinalAssets().length
      && hasBlueprint
      && !["generating", "completed", "failed"].includes(status);
  }

  function confirmVideoAgentFinal() {
    const sessionId = currentProviderSessionId();
    if (!sessionId) {
      setError("当前 Video Agent session 缺少 provider_session_id，请先刷新 Plan 后再确认生成。");
      return;
    }
    const payload = {
      ...buildPayload(),
      prompt: "Approve",
      generation_model: "video_agent",
      model_name: "Cloud Video Agent",
      engine_type: "video_agent",
      provider_session_id: sessionId,
      agent_confirm_generate: true,
    };
    void runGenerate(payload);
  }

  const confirmAvatarValue = () => avatar()?.name || avatarPhoto()?.label || avatarPhoto()?.filename || confirmPending()?.avatar_id || confirmPending()?.avatar_photo_path || "";
  const confirmVoiceValue = () => confirmPending()?.generation_mode === "audio_file" ? (audioFile()?.name || audioFile()?.filename || confirmPending()?.audio_asset_path || "") : (voice()?.name || confirmPending()?.voice_id || "");
  const currentAgentMessages = () => arrayItems(agentSession()?.messages);
  const currentAgentResources = () => arrayItems(agentSession()?.resources);
  const currentProviderSessionId = () => text(agentSession()?.provider_session_id || agentSession()?.id);
  const currentAgentFinalAssets = () => arrayItems(agentSession()?.assets);
  const visiblePlanText = () => text(agentSession()?.plan_text);
  const localAgentEvents = () => messages().filter((message) => message.agentEvent);
  const currentBlueprintText = () => blueprintTextFromSession(currentAgentMessages(), currentAgentResources(), visiblePlanText());
  const timelineItems = () => sessionTimeline(currentAgentMessages(), localAgentEvents());
  const visibleMessages = () => agentSession() ? messages().filter((message) => !message.agentEvent) : messages();
  const confirmTitle = () => confirmPending()?.generation_model === "video_agent" ? "确认创建 Video Agent Plan" : "Confirm before generating";
  const confirmPrimaryLabel = () => confirmPending()?.generation_model === "video_agent" ? "创建 Plan" : "Generate";
  const submitLabel = () => agentSession() ? "提交修改" : "发送";

  return <aside class="dh-agent">
    <AvatarPickerModal
      open={avatarOpen}
      onClose={() => setAvatarOpen(false)}
      taskId={taskId}
      api={props.api}
      images={props.images}
      selectedImage={props.selectedAvatarImage}
      settings={settings}
      setSettings={setSettings}
      assetUrl={props.assetUrl}
      uploadImageFiles={props.uploadImageFiles}
      onSelectImage={selectAvatarPhoto}
      onSelect={selectAvatar}
    />
    <VoicePickerModal open={voiceOpen} onClose={() => setVoiceOpen(false)} taskId={taskId} api={props.api} audios={props.audios} selectedAudio={props.selectedAudio} assetUrl={props.assetUrl} onSelectVoice={selectVoice} onSelectAudio={selectAudio} />
    <DigitalHumanSettingsPanel open={settingsOpen} onClose={() => setSettingsOpen(false)} settings={settings} setSettings={setSettings} />

    <header class="ual-agent-header">
      <div class="ual-agent-title">
        <button type="button" class="ual-agent-icon" aria-label="Menu"><FlowIcon name="menu" /></button>
        <strong>Workspace</strong>
      </div>
      <button type="button" class="ual-agent-icon" aria-label="Close" onClick={() => props.onClose?.()}><FlowIcon name="close" /></button>
    </header>

    <section class="dh-chat">
      <Show when={error()}><article class="ual-message is-assistant is-error"><p>{error()}</p></article></Show>
      <For each={visibleMessages()}>{(message) => (
        <article class={`ual-message is-${message.role} ${message.failed ? "is-error" : ""}`}>
          <div class={message.role === "user" ? "ual-user-bubble" : "ual-assistant-bubble"}>
            <p>{message.text}</p>
            <Show when={message.progress}><small>{message.progress}</small></Show>
            <Show when={message.assets?.length}>
              <div class="dh-message-assets">
                <For each={message.assets}>{(asset) => <span>{asset.label || asset.filename || asset.path}</span>}</For>
              </div>
            </Show>
          </div>
        </article>
      )}</For>
      <Show when={agentSession()}>
        {(session) => <section class="dh-agent-plan">
          <header>
            <div>
              <strong>{session().title || "Video Agent Plan"}</strong>
            </div>
            <div class="dh-agent-plan-actions">
              <button type="button" onClick={refreshAgentSession} disabled={agentRefreshing() || Boolean(agentControlBusy())} title="从云端服务重新读取当前会话的消息和资源">{agentRefreshing() ? "刷新中" : "刷新"}</button>
              <button type="button" class="dh-primary" onClick={confirmVideoAgentFinal} disabled={!canConfirmAgentGenerate()}>确认生成</button>
            </div>
          </header>
          <Show when={currentBlueprintText()}>
            <article class="dh-agent-blueprint-card">
              <strong>视频蓝图</strong>
              <p>{currentBlueprintText()}</p>
            </article>
          </Show>
          <div class="dh-agent-timeline" aria-label="Video Agent timeline">
            <For each={timelineItems()}>{(item) => (
              <article>
                <strong>{item.role}</strong>
                <p>{item.text}</p>
              </article>
            )}</For>
          </div>
        </section>}
      </Show>
    </section>

    <footer class="dh-composer">
      <input
        ref={(el) => { uploadInput = el; }}
        type="file"
        multiple
        hidden
        accept="image/*,video/*,audio/*,.png,.jpg,.jpeg,.webp,.mp4,.mov,.webm,.m4v,.wav,.mp3,.m4a,.aac,.ogg,.flac,.opus,.aiff,.caf"
        onChange={(event) => {
          const files = event.currentTarget.files;
          void uploadComposerFiles(files);
          event.currentTarget.value = "";
        }}
      />
      <Show when={confirmPending()}>
        <div class="dh-confirm" role="alertdialog" aria-label="Confirm digital human generation">
          <strong>{confirmTitle()}</strong>
          <span>Model: {confirmPending().model_name || DEFAULT_DIGITAL_HUMAN_SETTINGS.model_name} ({confirmPending().generation_model === "video_agent" ? "chat" : confirmPending().engine_type || DEFAULT_DIGITAL_HUMAN_SETTINGS.engine_type})</span>
          <span title={confirmAvatarValue()}>Avatar: {compactAssetLabel(confirmAvatarValue(), "Selected avatar")}</span>
          <span title={confirmVoiceValue()}>Voice: {compactAssetLabel(confirmVoiceValue(), "Selected audio")}</span>
          <span>{confirmPending().aspect} · x{confirmPending().count}</span>
          <div>
            <button type="button" onClick={() => setConfirmPending(null)}>Cancel</button>
            <button type="button" class="dh-primary" onClick={confirmGenerate}>{confirmPrimaryLabel()}</button>
          </div>
        </div>
      </Show>
      <div class="dh-chip-row">
        <Show when={avatar()}><Chip kind="avatar-id" image={avatar().preview_image_url} badge="Avatar" label={avatar().name || avatar().id} onRemove={() => selectAvatar(null)} /></Show>
        <Show when={avatarPhoto()}><Chip kind="image-ref" image={props.assetUrl?.(avatarPhoto())} badge="Image" label="Selected image" onRemove={removeAvatarPhoto} /></Show>
        <Show when={voice()}><Chip kind="voice-id" badge="Voice ID" label={voice().name || "Voice"} onRemove={() => selectVoice(null)} /></Show>
        <Show when={audioFile()}><Chip kind="audio-file" audio={props.assetUrl?.(audioFile())} badge="Audio" label="Audio" onRemove={removeAudioFile} /></Show>
      </div>
      <div
        class={`ual-composer-box ${referenceDragging() ? "is-reference-dragging" : ""}`}
        onDragEnter={handleReferenceDrag}
        onDragOver={handleReferenceDrag}
        onDragLeave={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget)) setReferenceDragging(false);
        }}
        onDrop={handleReferenceDrop}
      >
        <textarea
          value={prompt()}
          onInput={(event) => setPrompt(event.currentTarget.value)}
          placeholder={agentSession() ? "输入对当前 Video Agent Plan 的修改意见，发送后 Agent 会继续会话" : "描述你想生成的数字人口播视频，或输入口播脚本"}
          rows="3"
        />
        <div class="ual-composer-tools">
          <button type="button" class="ual-composer-icon is-plus" aria-label="上传素材" title="上传素材" disabled={busy()} onClick={() => uploadInput?.click()}><FlowIcon name="add" /></button>
          <div>
            <button type="button" class="ual-composer-icon" aria-label="生成和选择 Avatar" title="生成和选择 Avatar" disabled={busy()} onClick={() => setAvatarOpen(true)}><FlowIcon name="image" /></button>
            <button type="button" class="ual-composer-icon" aria-label="克隆和选择声音" title="克隆和选择声音" disabled={busy()} onClick={() => setVoiceOpen(true)}><FlowIcon name="addNotes" /></button>
            <button type="button" class="ual-composer-icon" aria-label="Settings" title="Settings" disabled={busy()} onClick={() => setSettingsOpen(true)}><FlowIcon name="tune" /></button>
            <button type="button" class="ual-composer-submit" aria-label={submitLabel()} title={submitLabel()} disabled={busy()} onClick={submit}><FlowIcon name="arrowForward" /></button>
          </div>
        </div>
      </div>
    </footer>
  </aside>;
}
