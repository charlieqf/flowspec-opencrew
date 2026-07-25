
const API_BASE = (() => {
  const origin = window.location.origin || window.location.href.split("#")[0].split("?")[0];
  return origin.endsWith("/") ? origin : `${origin}/`;
})();

function errorMessageFromResponseText(text, status) {
  const raw = String(text || "").trim();
  if (!raw) return `Request failed (${status})`;
  const readableError = (value) => {
    if (value == null) return "";
    if (typeof value === "string") return value;
    if (Array.isArray(value)) return value.map(readableError).filter(Boolean).join("\n");
    if (typeof value === "object") {
      return value.message || value.detail || value.error || value.code || JSON.stringify(value, null, 2);
    }
    return String(value);
  };
  try {
    const payload = JSON.parse(raw);
    return readableError(payload?.detail) || readableError(payload?.message) || raw;
  } catch {
    // Fall through to HTML/provider error handling below.
  }
  const lower = raw.slice(0, 8000).toLowerCase();
  if (lower.includes("<!doctype html") || lower.includes("<html")) {
    if (lower.includes("error code 524") || lower.includes("a timeout occurred")) return "公网请求超过 Cloudflare 等待时间（524）。后台任务可能仍在继续，系统会继续刷新结果；如果长时间没有结果再重试。";
    if (lower.includes("cloudflare") || lower.includes("5xx-error-landing")) return "Cloudflare 返回了公网错误页。请稍后重试，或检查公网隧道是否恢复。";
    return `Server returned an HTML error page (${status}).`;
  }
  return raw;
}

async function throwResponseError(res) {
  throw new Error(errorMessageFromResponseText(await res.text(), res.status));
}

function formatBytes(bytes) {
  const value = Number(bytes || 0);
  if (!value) return "";
  if (value < 1024 * 1024) return `${Math.max(1, Math.round(value / 1024))} KB`;
  return `${(value / 1024 / 1024).toFixed(value < 10 * 1024 * 1024 ? 1 : 0)} MB`;
}

function networkErrorMessage(path, body, err) {
  const raw = err instanceof Error ? err.message : String(err || "");
  if (body instanceof FormData) {
    const files = typeof body.getAll === "function" ? body.getAll("files") : [];
    const total = files.reduce((sum, file) => sum + Number(file?.size || 0), 0);
    const sizeText = formatBytes(total);
    return `上传请求连接失败${sizeText ? `（文件合计 ${sizeText}）` : ""}。文件未到达后端；如果使用公网隧道，请确认隧道在线且当前文件大小未超过限制，然后重试。`;
  }
  if (!raw || raw === "Failed to fetch" || raw === "fetch failed") {
    return `请求连接失败：${path}`;
  }
  return raw;
}

async function kbRequest(path, init) {
  const relativePath = path.startsWith("/") ? path.slice(1) : path;
  const body = init?.body;
  const headers = body instanceof FormData ? (init?.headers || {}) : { "Content-Type": "application/json", ...(init?.headers || {}) };
  let res;
  try {
    res = await fetch(new URL(relativePath, API_BASE), {
      credentials: "include",
      headers,
      ...init,
    });
  } catch (err) {
    throw new Error(networkErrorMessage(path, body, err));
  }
  if (!res.ok) {
    await throwResponseError(res);
  }
  return await res.json();
}

const storyboardDetailCache = new Map();

export function rememberKouboStoryboardDetail(result) {
  const taskId = Number(result?.task?.id || result?.task_id || 0);
  if (taskId && result?.task && result?.meta) storyboardDetailCache.set(taskId, result);
  return result;
}

export function getCachedKouboStoryboardDetail(taskId) {
  return storyboardDetailCache.get(Number(taskId || 0)) || null;
}

function rawFileUrl(sessionId, filePath) {
  return new URL(`api/session-tasks/${sessionId}/raw/${String(filePath || "").split("/").map(encodeURIComponent).join("/")}`, API_BASE).toString();
}

function rawDownloadLink(sessionId, filePath) {
  return kbRequest(`/api/session-tasks/${sessionId}/raw-download-link/${String(filePath || "").split("/").map(encodeURIComponent).join("/")}`, { method: "POST", body: JSON.stringify({}) });
}

function thumbnailFileUrl(sessionId, filePath) {
  return new URL(`api/session-tasks/${sessionId}/thumbnail/${String(filePath || "").split("/").map(encodeURIComponent).join("/")}`, API_BASE).toString();
}

async function readWorkspaceJson(sessionId, filePath) {
  const res = await fetch(`${rawFileUrl(sessionId, filePath)}?v=${Date.now()}`, { credentials: "include" });
  if (!res.ok) {
    await throwResponseError(res);
  }
  return await res.json();
}

const ASSET_UPLOAD_BATCH_MAX_FILES = 12;
const ASSET_UPLOAD_BATCH_MAX_BYTES = 24 * 1024 * 1024;

function uploadAssets(taskId, files) {
  const form = new FormData();
  for (const file of files || []) form.append("files", file);
  return kbRequest(`/api/koubo-storyboard/tasks/${taskId}/assets`, { method: "POST", body: form });
}

function splitAssetUploadBatches(files, options = {}) {
  const fileList = Array.from(files || []).filter(Boolean);
  const maxFiles = Math.max(1, Number(options.maxFiles || ASSET_UPLOAD_BATCH_MAX_FILES));
  const maxBytes = Math.max(1, Number(options.maxBytes || ASSET_UPLOAD_BATCH_MAX_BYTES));
  const batches = [];
  let current = [];
  let currentBytes = 0;
  const flush = () => {
    if (!current.length) return;
    batches.push(current);
    current = [];
    currentBytes = 0;
  };
  for (const file of fileList) {
    const size = Number(file?.size || 0);
    if (current.length && (current.length >= maxFiles || currentBytes + size > maxBytes)) flush();
    current.push(file);
    currentBytes += size;
  }
  flush();
  return batches;
}

async function uploadAssetsBatched(taskId, files, options = {}) {
  const fileList = Array.from(files || []).filter(Boolean);
  const batches = splitAssetUploadBatches(fileList, options);
  const added = [];
  let latest = null;
  let completedFiles = 0;
  for (let index = 0; index < batches.length; index += 1) {
    const batch = batches[index];
    options.onProgress?.({
      index: index + 1,
      total: batches.length,
      batch,
      completedFiles,
      totalFiles: fileList.length,
    });
    const result = await uploadAssets(taskId, batch);
    latest = result;
    const batchAdded = Array.isArray(result?.added) ? result.added : [];
    added.push(...batchAdded);
    completedFiles += batch.length;
    options.onBatch?.(result, {
      index: index + 1,
      total: batches.length,
      batch,
      batchAdded,
      added,
      completedFiles,
      totalFiles: fileList.length,
    });
  }
  return latest ? { ...latest, added } : { added };
}

export const kbApi = {
  tasks: () => kbRequest("/api/koubo-storyboard/tasks"),
  detail: async (taskId) => rememberKouboStoryboardDetail(await kbRequest(`/api/koubo-storyboard/tasks/${taskId}`)),
  ttsBuilderCandidates: (taskId) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/tts-builder-candidates`),
  save: (taskId, plan, options = {}) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}`, { method: "PUT", body: JSON.stringify({ plan, ...options }) }),
  saveVideoPlanSettings: (taskId, settings) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/video-plan/settings`, { method: "PUT", body: JSON.stringify({ settings }) }),
  videoPlan: (taskId, payload) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/video-plan`, { method: "POST", body: JSON.stringify(payload || {}) }),
  executeVideoPlan: (taskId, payload) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/video-plan/execute`, { method: "POST", body: JSON.stringify(payload || {}) }),
  videoPlanExecution: (taskId) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/video-plan/execution`),
  imagePlan: (taskId, payload) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/image-plan`, { method: "POST", body: JSON.stringify(payload || {}) }),
  executeImagePlan: (taskId, payload) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/image-plan/execute`, { method: "POST", body: JSON.stringify(payload || {}) }),
  imagePlanExecution: (taskId) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/image-plan/execution`),
  imagePlanPrompt: (taskId, assetKey) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/image-plan/prompts/${encodeURIComponent(assetKey)}`),
  saveImagePlanPrompt: (taskId, assetKey, payload) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/image-plan/prompts/${encodeURIComponent(assetKey)}`, { method: "PUT", body: JSON.stringify(payload || {}) }),
  videoOnlyPlan: (taskId, payload) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/video-only-plan`, { method: "POST", body: JSON.stringify(payload || {}) }),
  executeVideoOnlyPlan: (taskId, payload) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/video-only-plan/execute`, { method: "POST", body: JSON.stringify(payload || {}) }),
  videoOnlyPlanExecution: (taskId) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/video-only-plan/execution`),
  videoOnlyPlanPrompt: (taskId, assetKey) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/video-only-plan/prompts/${encodeURIComponent(assetKey)}`),
  saveVideoOnlyPlanPrompt: (taskId, assetKey, payload) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/video-only-plan/prompts/${encodeURIComponent(assetKey)}`, { method: "PUT", body: JSON.stringify(payload || {}) }),
  reloadVideoOnlyPlanPrompt: (taskId, assetKey) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/video-only-plan/prompts/${encodeURIComponent(assetKey)}/reload`, { method: "POST", body: JSON.stringify({}) }),
  materializeVideoOnlyTailFrame: (taskId, assetKey) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/video-only-plan/segments/${encodeURIComponent(assetKey)}/materialize-tail-frame`, { method: "POST", body: JSON.stringify({}) }),
  confirmVideoOnlyFinal: (taskId, assetKey) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/video-only-plan/segments/${encodeURIComponent(assetKey)}/confirm-final`, { method: "POST", body: JSON.stringify({}) }),
  refreshSessionVariables: (taskId) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/session-variables/refresh`, { method: "POST", body: JSON.stringify({}) }),
  runAnalysisStep: (taskId, stepId, payload = {}) => kbRequest(`/api/openclip/tasks/${taskId}/analysis-v1/run-to-storyboard`, { method: "POST", body: JSON.stringify({ ...(payload || {}), mode: "run_only_step", run_only_step_id: stepId }) }),
  analysisRunStatus: (taskId, attemptId) => kbRequest(`/api/openclip/tasks/${taskId}/analysis-v1/run-to-storyboard/${attemptId}`),
  readWorkspaceJson,
  composerCandidates: (taskId, payload = {}) => {
    const target = payload?.target || {};
    const params = new URLSearchParams();
    if (target.target_type) params.set("target_type", target.target_type);
    if (target.shot_id) params.set("shot_id", target.shot_id);
    if (target.scene_id) params.set("scene_id", target.scene_id);
    if (payload.action_source) params.set("action_source", payload.action_source);
    const query = params.toString();
    return kbRequest(`/api/koubo-storyboard/tasks/${taskId}/composer/candidates${query ? `?${query}` : ""}`);
  },
  composerExecution: (taskId) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/composer/execution`),
  executeComposer: (taskId, payload) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/composer/execute`, { method: "POST", body: JSON.stringify(payload || {}) }),
  applyHyperframeTemplate: (taskId, payload) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/hyperframe-template/apply`, { method: "POST", body: JSON.stringify(payload || {}) }),
  imageModelConfig: () => kbRequest("/api/setup/media-models/image/config"),
  videoModelConfig: () => kbRequest("/api/setup/media-models/video/config"),
  cleanImageGenerations: (taskId) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/clean-image/generations`),
  uploadCleanImageReferences: (taskId, files) => {
    const form = new FormData();
    for (const file of files || []) form.append("files", file);
    return kbRequest(`/api/koubo-storyboard/tasks/${taskId}/clean-image/references`, { method: "POST", body: form });
  },
  generateCleanImage: (taskId, payload) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/clean-image/generate`, { method: "POST", body: JSON.stringify(payload || {}) }),
  promoteCleanImageToAssetLibrary: (taskId, generationId, payload) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/clean-image/${encodeURIComponent(generationId)}/promote/asset-library`, { method: "POST", body: JSON.stringify(payload || {}) }),
  promoteCleanImageToDialogue: (taskId, generationId, payload) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/clean-image/${encodeURIComponent(generationId)}/promote/dialogue-image`, { method: "POST", body: JSON.stringify(payload || {}) }),
  promoteCleanImageToConsistency: (taskId, generationId, payload) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/clean-image/${encodeURIComponent(generationId)}/promote/consistency`, { method: "POST", body: JSON.stringify(payload || {}) }),
  cleanImageUrl: (taskId, generationId) => new URL(`api/koubo-storyboard/tasks/${taskId}/clean-image/${encodeURIComponent(generationId)}/image`, API_BASE).toString(),
  cleanImageReferenceUrl: (taskId, referenceId) => new URL(`api/koubo-storyboard/tasks/${taskId}/clean-image/references/${encodeURIComponent(referenceId)}/image`, API_BASE).toString(),
  ttsModelConfig: () => kbRequest("/api/setup/media-models/tts/config"),
  assetLibraryImageModelConfig: (taskId) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-library/image-model-config`),
  assetLibraryVideoModelConfig: (taskId) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-library/video-model-config`),
  assetLibraryTtsModelConfig: (taskId) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-library/tts-model-config`),
  assetLibraryTtsAgentDraft: (taskId, payload) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-library/tts-agent/draft`, { method: "POST", body: JSON.stringify(payload || {}) }),
  assetLibraryTtsAgentMessages: (taskId) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-library/tts-agent/messages`),
  saveAssetLibraryTtsAgentMessages: (taskId, messages) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-library/tts-agent/messages`, { method: "PUT", body: JSON.stringify({ messages }) }),
  saveAssetLibraryTtsAgentSession: (taskId, sessionId, session) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-library/tts-agent/sessions/${encodeURIComponent(sessionId)}`, { method: "PUT", body: JSON.stringify({ session }) }),
  assetLibraryImageAPISettings: (taskId) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-library/image-api/settings`),
  saveAssetLibraryImageAPISettings: (taskId, settings) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-library/image-api/settings`, { method: "PUT", body: JSON.stringify({ settings }) }),
  assetLibraryImageAPIHistory: (taskId) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-library/image-api/history`),
  saveAssetLibraryImageAPIHistory: (taskId, payload) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-library/image-api/history`, { method: "PUT", body: JSON.stringify(payload || {}) }),
  assetLibraryImagesAgentSettings: (taskId) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-library/images-agent/settings`),
  saveAssetLibraryImagesAgentSettings: (taskId, settings) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-library/images-agent/settings`, { method: "PUT", body: JSON.stringify({ settings }) }),
  assetLibraryVideoAPISettings: (taskId) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-library/video-api/settings`),
  saveAssetLibraryVideoAPISettings: (taskId, settings) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-library/video-api/settings`, { method: "PUT", body: JSON.stringify({ settings }) }),
  assetLibraryVideoAPIHistory: (taskId) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-library/video-api/history`),
  saveAssetLibraryVideoAPIHistory: (taskId, payload) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-library/video-api/history`, { method: "PUT", body: JSON.stringify(payload || {}) }),
  assetLibraryCurrentVideoInteraction: (taskId, chatSessionId = "") => {
    const query = chatSessionId ? `?chat_session_id=${encodeURIComponent(chatSessionId)}` : "";
    return kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-library/video-interactions/current${query}`);
  },
  assetLibraryVideoInteraction: (taskId, threadId) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-library/video-interactions/${encodeURIComponent(threadId)}`),
  deleteAssetLibraryVideoInteractionCloudContext: (taskId, threadId) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-library/video-interactions/${encodeURIComponent(threadId)}/cloud-context/delete`, { method: "POST", body: JSON.stringify({}) }),
  assetLibraryVideosAgentSettings: (taskId) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-library/videos-agent/settings`),
  saveAssetLibraryVideosAgentSettings: (taskId, settings) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-library/videos-agent/settings`, { method: "PUT", body: JSON.stringify({ settings }) }),
  assetLibraryDigitalHumanSettings: (taskId) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-library/digital-human/settings`),
  saveAssetLibraryDigitalHumanSettings: (taskId, settings) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-library/digital-human/settings`, { method: "PUT", body: JSON.stringify({ settings }) }),
  assetLibraryDigitalHumanAgentSession: (taskId, providerSessionId, options = {}) => {
    const query = options.materialize === false ? "?materialize=false" : "";
    return kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-library/digital-human/agents/${encodeURIComponent(providerSessionId)}${query}`);
  },
  stopAssetLibraryDigitalHumanAgentSession: (taskId, providerSessionId) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-library/digital-human/agents/${encodeURIComponent(providerSessionId)}/stop`, { method: "POST", body: JSON.stringify({}) }),
  assetLibraryDigitalHumanAvatars: (taskId, params = {}) => {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(params || {})) {
      if (value !== undefined && value !== null && value !== "") query.set(key, String(value));
    }
    const suffix = query.toString() ? `?${query.toString()}` : "";
    return kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-library/digital-human/avatars${suffix}`);
  },
  createAssetLibraryDigitalHumanPhotoAvatar: (taskId, payload = {}) => {
    const form = new FormData();
    form.append("name", payload.name || "");
    form.append("description", payload.description || "");
    form.append("asset_path", payload.assetPath || payload.asset_path || "");
    if (payload.file) form.append("file", payload.file);
    return kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-library/digital-human/avatars/photo`, { method: "POST", body: form });
  },
  deleteAssetLibraryDigitalHumanAvatar: (taskId, avatarId) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-library/digital-human/avatars/${encodeURIComponent(avatarId || "")}`, { method: "DELETE" }),
  assetLibraryDigitalHumanVoices: (taskId, params = {}) => {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(params || {})) {
      if (value !== undefined && value !== null && value !== "") query.set(key, String(value));
    }
    const suffix = query.toString() ? `?${query.toString()}` : "";
    return kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-library/digital-human/voices${suffix}`);
  },
  cloneAssetLibraryDigitalHumanVoice: (taskId, payload = {}) => {
    const form = new FormData();
    form.append("voice_name", payload.voiceName || payload.voice_name || "");
    form.append("asset_path", payload.assetPath || payload.asset_path || "");
    form.append("language", payload.language || "");
    form.append("remove_background_noise", payload.removeBackgroundNoise === false ? "false" : "true");
    if (payload.file) form.append("file", payload.file);
    return kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-library/digital-human/voices/clone`, { method: "POST", body: form });
  },
  registerAssetLibraryDigitalHumanAudio: (taskId, payload = {}) => {
    const form = new FormData();
    form.append("asset_path", payload.assetPath || payload.asset_path || "");
    if (payload.file) form.append("file", payload.file);
    return kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-library/digital-human/audio-assets`, { method: "POST", body: form });
  },
  assetLibraryAgentChatEnsureSession: (taskId) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-library-agent/chat/ensure-session`, { method: "POST", body: JSON.stringify({}) }),
  assetLibraryAgentChatMessages: (taskId) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-library-agent/chat/messages`),
  assetLibraryAgentChatSendMessage: (taskId, payload) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-library-agent/chat/message`, { method: "POST", body: JSON.stringify(payload || {}) }),
  assetLibraryAgentChatAbort: (taskId) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-library-agent/chat/abort`, { method: "POST", body: JSON.stringify({}) }),
  assetLibraryAgentChatEventsUrl: (taskId) => new URL(`api/koubo-storyboard/tasks/${taskId}/asset-library-agent/chat/events`, API_BASE).toString(),
  agentChatEnsureSession: (taskId, agentKey) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/agents/${encodeURIComponent(agentKey)}/chat/ensure-session`, { method: "POST", body: JSON.stringify({}) }),
  agentChatMessages: (taskId, agentKey) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/agents/${encodeURIComponent(agentKey)}/chat/messages`),
  agentChatSendMessage: (taskId, agentKey, payload) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/agents/${encodeURIComponent(agentKey)}/chat/message`, { method: "POST", body: JSON.stringify(payload || {}) }),
  agentChatAbort: (taskId, agentKey) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/agents/${encodeURIComponent(agentKey)}/chat/abort`, { method: "POST", body: JSON.stringify({}) }),
  agentChatEventsUrl: (taskId, agentKey) => new URL(`api/koubo-storyboard/tasks/${taskId}/agents/${encodeURIComponent(agentKey)}/chat/events`, API_BASE).toString(),
  promptAgentKnowledgeSearch: (taskId, payload) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/prompt-agent/knowledge/search`, { method: "POST", body: JSON.stringify(payload || {}) }),
  promptAgentVersions: (taskId) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/prompt-agent/versions`),
  createPromptAgentVersion: (taskId, payload) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/prompt-agent/versions`, { method: "POST", body: JSON.stringify(payload || {}) }),
  updatePromptAgentVersion: (taskId, versionId, payload) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/prompt-agent/versions/${encodeURIComponent(versionId)}`, { method: "PUT", body: JSON.stringify(payload || {}) }),
  deletePromptAgentVersion: (taskId, versionId) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/prompt-agent/versions/${encodeURIComponent(versionId)}`, { method: "DELETE" }),
  createPromptAgentApplied: (taskId, payload) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/prompt-agent/applied`, { method: "POST", body: JSON.stringify(payload || {}) }),
  assetLibrarySearchSettings: (taskId) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-library-search/settings`),
  saveAssetLibrarySearchSettings: (taskId, payload) => {
    const body = payload && (payload.settings || payload.provider_keys) ? payload : { settings: payload || {} };
    return kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-library-search/settings`, { method: "PUT", body: JSON.stringify(body) });
  },
  assetLibrarySearchPlan: (taskId, payload) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-library-search/plan`, { method: "POST", body: JSON.stringify(payload || {}) }),
  assetLibrarySearchStoryboardPlan: (taskId, payload) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-library-search/storyboard-plan`, { method: "POST", body: JSON.stringify(payload || {}) }),
  assetLibrarySearchRuns: (taskId) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-library-search/runs`),
  assetLibrarySearchRun: (taskId, searchId) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-library-search/runs/${encodeURIComponent(searchId)}`),
  importAssetLibrarySearch: (taskId, payload) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-library-search/import`, { method: "POST", body: JSON.stringify(payload || {}) }),
  assetLibrarySearchSourceList: (taskId) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-library-search/source-list`),
  exportAssetLibrarySearchSourceList: (taskId) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-library-search/source-list/export`, { method: "POST", body: JSON.stringify({}) }),
  streamAssetLibrarySearch: async (taskId, payload, onEvent) => {
    const res = await fetch(new URL(`api/koubo-storyboard/tasks/${taskId}/asset-library-search/search/events`, API_BASE), { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload || {}) });
    if (!res.ok) await throwResponseError(res);
    const reader = res.body?.getReader();
    if (!reader) throw new Error("Streaming response is not readable");
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const chunks = buffer.split("\n\n");
      buffer = chunks.pop() || "";
      for (const chunk of chunks) {
        const line = chunk.split("\n").find((item) => item.startsWith("data: "));
        if (line) onEvent(JSON.parse(line.slice(6)));
      }
    }
    if (buffer.trim().startsWith("data: ")) onEvent(JSON.parse(buffer.trim().slice(6)));
  },
  assetLibraryPromptBuilder: (taskId, payload) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-library-agent/prompt-builder`, { method: "POST", body: JSON.stringify(payload || {}) }),
  saveAssetLibraryPromptBuilder: (taskId, requestId, payload) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-library-agent/prompt-builder/${encodeURIComponent(requestId)}`, { method: "PUT", body: JSON.stringify(payload || {}) }),
  streamCompareAssetTTS: async (taskId, payload, onEvent, options = {}) => {
    let res;
    try {
      res = await fetch(new URL(`api/koubo-storyboard/tasks/${taskId}/scene-tts/events`, API_BASE), { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload), signal: options.signal });
    } catch (err) {
      if (options.signal?.aborted) throw err;
      throw new Error("TTS 试听连接失败。请检查本地服务/公网隧道是否可用，然后重试。");
    }
    if (!res.ok) await throwResponseError(res);
    const reader = res.body?.getReader();
    if (!reader) throw new Error("Streaming response is not readable");
    const decoder = new TextDecoder();
    let buffer = "";
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const chunks = buffer.split("\n\n");
        buffer = chunks.pop() || "";
        for (const chunk of chunks) {
          const line = chunk.split("\n").find((item) => item.startsWith("data: "));
          if (line) onEvent(JSON.parse(line.slice(6)));
        }
      }
      if (buffer.trim().startsWith("data: ")) onEvent(JSON.parse(buffer.trim().slice(6)));
    } catch (err) {
      if (options.signal?.aborted) throw err;
      throw new Error("TTS 试听连接中断。后台可能仍在处理上一条请求，请稍后刷新状态或重试。");
    }
  },
  uploadAssets,
  uploadAssetsBatched,
  copySourceAssets: (taskId, payload) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/assets/source-copy`, { method: "POST", body: JSON.stringify(payload || {}) }),
  streamAssetLibraryAgentGenerate: async (taskId, payload, onEvent) => {
    const res = await fetch(new URL(`api/koubo-storyboard/tasks/${taskId}/asset-library-agent/generate/events`, API_BASE), { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload || {}) });
    if (!res.ok) await throwResponseError(res);
    const reader = res.body?.getReader();
    if (!reader) throw new Error("Streaming response is not readable");
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const chunks = buffer.split("\n\n");
      buffer = chunks.pop() || "";
      for (const chunk of chunks) {
        const line = chunk.split("\n").find((item) => item.startsWith("data: "));
        if (line) onEvent(JSON.parse(line.slice(6)));
      }
    }
    if (buffer.trim().startsWith("data: ")) onEvent(JSON.parse(buffer.trim().slice(6)));
  },
  streamAssetLibraryVideoGenerate: async (taskId, payload, onEvent, options = {}) => {
    const attempts = payload?.client_action_id ? 2 : 1;
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      try {
        const res = await fetch(new URL(`api/koubo-storyboard/tasks/${taskId}/asset-library/video-api/generate/events`, API_BASE), { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload || {}), signal: options.signal });
        if (!res.ok) await throwResponseError(res);
        const reader = res.body?.getReader();
        if (!reader) throw new Error("Streaming response is not readable");
        const decoder = new TextDecoder();
        let buffer = "";
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const chunks = buffer.split("\n\n");
          buffer = chunks.pop() || "";
          for (const chunk of chunks) {
            const line = chunk.split("\n").find((item) => item.startsWith("data: "));
            if (line) onEvent(JSON.parse(line.slice(6)));
          }
        }
        if (buffer.trim().startsWith("data: ")) onEvent(JSON.parse(buffer.trim().slice(6)));
        return;
      } catch (error) {
        if (options.signal?.aborted || attempt + 1 >= attempts) throw error;
      }
    }
  },
  streamAssetLibraryDigitalHumanGenerate: async (taskId, payload, onEvent, options = {}) => {
    const res = await fetch(new URL(`api/koubo-storyboard/tasks/${taskId}/asset-library/digital-human/generate/events`, API_BASE), { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload || {}), signal: options.signal });
    if (!res.ok) await throwResponseError(res);
    const reader = res.body?.getReader();
    if (!reader) throw new Error("Streaming response is not readable");
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const chunks = buffer.split("\n\n");
      buffer = chunks.pop() || "";
      for (const chunk of chunks) {
        const line = chunk.split("\n").find((item) => item.startsWith("data: "));
        if (line) onEvent(JSON.parse(line.slice(6)));
      }
    }
    if (buffer.trim().startsWith("data: ")) onEvent(JSON.parse(buffer.trim().slice(6)));
  },
  deleteAsset: (taskId, assetId) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/assets/${encodeURIComponent(assetId)}`, { method: "DELETE" }),
  renameAsset: (taskId, assetId, payload) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/assets/${encodeURIComponent(assetId)}/rename`, { method: "POST", body: JSON.stringify(payload || {}) }),
  moveAssetToHistory: (taskId, assetId) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/assets/${encodeURIComponent(assetId)}/move-to-history`, { method: "POST" }),
  restoreHistoryAsset: (taskId, assetId) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-history/restore`, { method: "POST", body: JSON.stringify({ asset_id: assetId }) }),
  deleteHistoryAsset: (taskId, assetId) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-history/${encodeURIComponent(assetId)}`, { method: "DELETE" }),
  bindAsset: (taskId, payload) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-bind`, { method: "POST", body: JSON.stringify(payload) }),
  clearAsset: (taskId, payload) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/asset-clear`, { method: "POST", body: JSON.stringify(payload) }),
  hostProductBuilder: (taskId) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/host-product-builder`),
  uploadHostProductRefs: (taskId, kind, files) => {
    const form = new FormData();
    form.append("kind", kind);
    for (const file of files || []) form.append("files", file);
    return kbRequest(`/api/koubo-storyboard/tasks/${taskId}/host-product-builder/uploads`, { method: "POST", body: form });
  },
  deleteHostProductRef: (taskId, payload) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/host-product-builder/reference`, { method: "DELETE", body: JSON.stringify(payload) }),
  uploadHostProductOutput: (taskId, kind, file) => {
    const form = new FormData();
    form.append("kind", kind);
    form.append("file", file);
    return kbRequest(`/api/koubo-storyboard/tasks/${taskId}/host-product-builder/output`, { method: "POST", body: form });
  },
  deleteHostProductOutput: (taskId, payload) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/host-product-builder/output`, { method: "DELETE", body: JSON.stringify(payload) }),
  generateHostProductPrompt: (taskId, payload) => kbRequest(`/api/koubo-storyboard/tasks/${taskId}/host-product-builder/prompt`, { method: "POST", body: JSON.stringify(payload) }),
  streamHostProductPrompt: async (taskId, payload, onEvent) => {
    const res = await fetch(new URL(`api/koubo-storyboard/tasks/${taskId}/host-product-builder/prompt/events`, API_BASE), { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    if (!res.ok) await throwResponseError(res);
    const reader = res.body?.getReader();
    if (!reader) throw new Error("Streaming response is not readable");
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const chunks = buffer.split("\n\n");
      buffer = chunks.pop() || "";
      for (const chunk of chunks) {
        const line = chunk.split("\n").find((item) => item.startsWith("data: "));
        if (line) onEvent(JSON.parse(line.slice(6)));
      }
    }
    if (buffer.trim().startsWith("data: ")) onEvent(JSON.parse(buffer.trim().slice(6)));
  },
  streamHostProductImage: async (taskId, payload, onEvent) => {
    const res = await fetch(new URL(`api/koubo-storyboard/tasks/${taskId}/host-product-builder/generate/events`, API_BASE), { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    if (!res.ok) await throwResponseError(res);
    const reader = res.body?.getReader();
    if (!reader) throw new Error("Streaming response is not readable");
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const chunks = buffer.split("\n\n");
      buffer = chunks.pop() || "";
      for (const chunk of chunks) {
        const line = chunk.split("\n").find((item) => item.startsWith("data: "));
        if (line) onEvent(JSON.parse(line.slice(6)));
      }
    }
    if (buffer.trim().startsWith("data: ")) onEvent(JSON.parse(buffer.trim().slice(6)));
  },
  rawFileUrl,
  rawDownloadLink,
  thumbnailFileUrl,
};
