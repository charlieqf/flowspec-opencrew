import { openClipApi } from "../api";

const API_BASE = (() => {
  const href = window.location.href.split("#")[0];
  return href.endsWith("/") ? href : `${href}/`;
})();

function fetchWithCredentials(input, init) {
  return fetch(input, { ...(init || {}), credentials: "include" });
}

function formatRawError(value, fallback) {
  const raw = String(value || "").trim();
  if (!raw) return fallback;
  if (/<!doctype html|<html[\s>]/i.test(raw)) {
    if (/524|a timeout occurred|cloudflare/i.test(raw)) {
      return "公网 Cloudflare 隧道超时（524）。这次请求耗时超过公网隧道限制；请稍后重试，或从本地地址访问后再试听。";
    }
    const title = raw.match(/<title[^>]*>([\s\S]*?)<\/title>/i)?.[1] || "";
    const body = raw
      .replace(/<script[\s\S]*?<\/script>/gi, " ")
      .replace(/<style[\s\S]*?<\/style>/gi, " ")
      .replace(/<[^>]+>/g, " ")
      .replace(/\s+/g, " ")
      .trim();
    return (title || body || fallback).slice(0, 1000);
  }
  return raw.length > 1000 ? `${raw.slice(0, 1000)}...` : raw;
}

function parseErrorText(text, fallback) {
  if (!text) return fallback;
  try {
    const payload = JSON.parse(text);
    const detail = payload?.detail;
    if (typeof detail === "string") return formatRawError(detail, fallback);
    if (detail?.message) {
      const missing = Array.isArray(detail.missing) && detail.missing.length ? ` Missing: ${detail.missing.join(", ")}` : "";
      const action = detail.suggested_action ? ` ${detail.suggested_action}` : "";
      return formatRawError(`${detail.message}${missing}${action}`, fallback);
    }
    return formatRawError(JSON.stringify(payload), fallback);
  } catch {
    return formatRawError(text, fallback);
  }
}

function encodePathSegments(value) {
  return String(value || "").split("/").map(encodeURIComponent).join("/");
}

async function readWorkspaceJson(sessionId, filePath) {
  if (!sessionId || !filePath) return null;
  const res = await fetchWithCredentials(`${openClipApi.rawFileUrl(sessionId, filePath)}?v=${Date.now()}`);
  if (res.status === 404) return null;
  if (!res.ok) {
    const detail = await parseError(res, `Unable to read ${filePath} (${res.status})`);
    if (res.status === 401) {
      throw new Error(`登录状态已失效，无法读取工作区文件：${filePath}。请重新登录后刷新页面。`);
    }
    if (res.status === 403) {
      throw new Error(`当前账号无权读取工作区文件：${filePath}。${detail}`);
    }
    throw new Error(`读取工作区文件失败：${filePath} (${res.status})。${detail}`);
  }
  return await res.json();
}

async function parseError(res, fallback) {
  const text = await res.text();
  return parseErrorText(text, fallback);
}

function uploadFormData(url, form, { onProgress } = {}) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", String(url));
    xhr.withCredentials = true;
    xhr.upload.onprogress = (event) => {
      if (typeof onProgress !== "function") return;
      onProgress({
        loaded: event.loaded,
        total: event.lengthComputable ? event.total : 0,
        lengthComputable: event.lengthComputable,
      });
    };
    xhr.onload = () => {
      const text = xhr.responseText || "";
      if (xhr.status < 200 || xhr.status >= 300) {
        reject(new Error(parseErrorText(text, `上传失败 (${xhr.status})`)));
        return;
      }
      try {
        resolve(text ? JSON.parse(text) : {});
      } catch {
        resolve({});
      }
    };
    xhr.onerror = () => reject(new Error("上传失败：网络连接中断"));
    xhr.onabort = () => reject(new Error("上传已取消"));
    xhr.send(form);
  });
}

const TTS_REFERENCE_CHUNK_BYTES = 2 * 1024 * 1024;
const TTS_REFERENCE_CHUNK_THRESHOLD_BYTES = TTS_REFERENCE_CHUNK_BYTES;

function uploadId() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID().replace(/[^a-zA-Z0-9_-]/g, "");
  }
  return `upload_${Date.now()}_${Math.random().toString(36).slice(2, 12)}`;
}

async function uploadTTSReferenceAudioChunked(taskId, file, options = {}) {
  const totalSize = Number(file?.size || 0);
  const totalChunks = Math.max(1, Math.ceil(totalSize / TTS_REFERENCE_CHUNK_BYTES));
  const id = uploadId();
  let completedBytes = 0;
  let response = {};
  for (let index = 0; index < totalChunks; index += 1) {
    const start = index * TTS_REFERENCE_CHUNK_BYTES;
    const end = Math.min(totalSize, start + TTS_REFERENCE_CHUNK_BYTES);
    const form = new FormData();
    form.append("upload_id", id);
    form.append("chunk_index", String(index));
    form.append("total_chunks", String(totalChunks));
    form.append("filename", file.name || "reference_audio");
    form.append("total_size", String(totalSize));
    form.append("content_type", file.type || "");
    form.append("file", file.slice(start, end), file.name || "reference_audio");
    response = await uploadFormData(new URL(`api/openclip/tasks/${taskId}/analysis-v1/tts/reference-audio/chunk`, API_BASE), form, {
      onProgress: (event) => {
        if (typeof options?.onProgress !== "function") return;
        options.onProgress({
          loaded: completedBytes + Number(event.loaded || 0),
          total: totalSize,
          lengthComputable: true,
          chunkIndex: index,
          totalChunks,
        });
      },
    });
    completedBytes = end;
    options?.onProgress?.({
      loaded: completedBytes,
      total: totalSize,
      lengthComputable: true,
      chunkIndex: index,
      totalChunks,
    });
  }
  return response;
}

export const analysisV1Api = {
  tasks: async () => (await openClipApi.tasks()).items || [],
  createTask: openClipApi.createTask,
  taskDetail: openClipApi.taskDetail,
  deleteTask: openClipApi.deleteTask,
  saveConfig: openClipApi.saveConfig,
  generatePrompt: openClipApi.generatePrompt,
  run: openClipApi.run,
  runToStoryBoard: async (taskId, payload) => {
    const res = await fetchWithCredentials(new URL(`api/openclip/tasks/${taskId}/analysis-v1/run-to-storyboard`, API_BASE), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...(payload || {}), task_id: taskId }),
    });
    if (!res.ok) throw new Error(await parseError(res, `Analysis V1 run failed (${res.status})`));
    return await res.json();
  },
  runToStoryBoardPlan: async (taskId) => {
    const res = await fetchWithCredentials(new URL(`api/openclip/tasks/${taskId}/analysis-v1/run-to-storyboard/plan`, API_BASE));
    if (!res.ok) throw new Error(await parseError(res, `Unable to load Analysis V1 run plan (${res.status})`));
    return await res.json();
  },
  runToStoryBoardStatus: async (taskId, attemptId) => {
    const res = await fetchWithCredentials(new URL(`api/openclip/tasks/${taskId}/analysis-v1/run-to-storyboard/${attemptId}`, API_BASE));
    if (!res.ok) throw new Error(await parseError(res, `Unable to load Analysis V1 run status (${res.status})`));
    return await res.json();
  },
  stopRunToStoryBoard: async (taskId, attemptId, payload = {}) => {
    const res = await fetchWithCredentials(new URL(`api/openclip/tasks/${taskId}/analysis-v1/run-to-storyboard/${attemptId}/stop`, API_BASE), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: "graceful", reason: "user_requested", ...(payload || {}) }),
    });
    if (!res.ok) throw new Error(await parseError(res, `Unable to stop Analysis V1 run (${res.status})`));
    return await res.json();
  },
  pauseBeforeRunToStoryBoard: async (taskId, attemptId, stepId, payload = {}) => {
    const res = await fetchWithCredentials(new URL(`api/openclip/tasks/${taskId}/analysis-v1/run-to-storyboard/${attemptId}/pause-before`, API_BASE), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ step_id: stepId, reason: "user_requested", ...(payload || {}) }),
    });
    if (!res.ok) throw new Error(await parseError(res, `Unable to set pause point (${res.status})`));
    return await res.json();
  },
  cancelPauseBeforeRunToStoryBoard: async (taskId, attemptId) => {
    const res = await fetchWithCredentials(new URL(`api/openclip/tasks/${taskId}/analysis-v1/run-to-storyboard/${attemptId}/pause-before`, API_BASE), { method: "DELETE" });
    if (!res.ok) throw new Error(await parseError(res, `Unable to cancel pause point (${res.status})`));
    return await res.json();
  },
  resumeRunToStoryBoard: async (taskId, attemptId, payload = {}) => {
    const res = await fetchWithCredentials(new URL(`api/openclip/tasks/${taskId}/analysis-v1/run-to-storyboard/${attemptId}/resume`, API_BASE), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason: "user_requested", ...(payload || {}) }),
    });
    if (!res.ok) throw new Error(await parseError(res, `Unable to resume Analysis V1 run (${res.status})`));
    return await res.json();
  },
  runToStoryBoardQuickWatch: async (taskId, attemptId, stepId) => {
    const res = await fetchWithCredentials(new URL(`api/openclip/tasks/${taskId}/analysis-v1/run-to-storyboard/${attemptId}/steps/${stepId}/quick-watch`, API_BASE));
    if (!res.ok) throw new Error(await parseError(res, `Unable to load step detail (${res.status})`));
    return await res.json();
  },
  runToStoryBoardLogs: async (taskId, attemptId, stepId, cursor = "") => {
    const res = await fetchWithCredentials(new URL(`api/openclip/tasks/${taskId}/analysis-v1/run-to-storyboard/${attemptId}/steps/${stepId}/logs${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ""}`, API_BASE));
    if (!res.ok) throw new Error(await parseError(res, `Unable to load step logs (${res.status})`));
    return await res.json();
  },
  oneClickMovie: async (taskId, payload = {}) => {
    const res = await fetchWithCredentials(new URL(`api/openclip/tasks/${taskId}/analysis-v1/one-click-movie`, API_BASE), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload || {}),
    });
    if (!res.ok) throw new Error(await parseError(res, `Unable to start one-click movie (${res.status})`));
    return await res.json();
  },
  oneClickMovieStatus: async (taskId, runId = "") => {
    const suffix = runId ? `/${encodeURIComponent(runId)}` : "";
    const res = await fetchWithCredentials(new URL(`api/openclip/tasks/${taskId}/analysis-v1/one-click-movie${suffix}`, API_BASE));
    if (!res.ok) throw new Error(await parseError(res, `Unable to load one-click movie status (${res.status})`));
    return await res.json();
  },
  saveRewrittenSrt: async (taskId, payload) => {
    const res = await fetchWithCredentials(new URL(`api/openclip/tasks/${taskId}/analysis-v1/rewritten-srt`, API_BASE), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...(payload || {}), task_id: taskId }),
    });
    if (!res.ok) throw new Error(await parseError(res, `Unable to save rewritten SRT (${res.status})`));
    return await res.json();
  },
  promptModels: async () => {
    const res = await fetchWithCredentials(new URL("api/openclip/prompt-models", API_BASE));
    if (!res.ok) throw new Error(await res.text() || `Unable to load prompt models (${res.status})`);
    return await res.json();
  },
  rawFileUrl: openClipApi.rawFileUrl,
  voiceCatalogAudioUrl: (taskId, model, filePath) => new URL(`api/openclip/tasks/${taskId}/analysis-v1/voice-catalog/${encodeURIComponent(String(model || ""))}/audio/${encodePathSegments(filePath)}`, API_BASE).toString(),
  ttsModelConfig: async () => {
    const res = await fetchWithCredentials(new URL("api/setup/media-models/tts/config", API_BASE));
    if (!res.ok) throw new Error(await res.text() || `Unable to load TTS model config (${res.status})`);
    return await res.json();
  },
  ttsModelConfigForTask: async (taskId) => {
    const res = await fetchWithCredentials(new URL(`api/koubo-storyboard/tasks/${taskId}/asset-library/tts-model-config`, API_BASE));
    if (!res.ok) throw new Error(await parseError(res, `Unable to load task TTS model config (${res.status})`));
    return await res.json();
  },
  ttsBuilderCandidates: async (taskId) => {
    const res = await fetchWithCredentials(new URL(`api/koubo-storyboard/tasks/${taskId}/tts-builder-candidates`, API_BASE));
    if (!res.ok) throw new Error(await parseError(res, `Unable to load task TTS candidates (${res.status})`));
    return await res.json();
  },
  previewTTSVoice: async (payload) => {
    const res = await fetchWithCredentials(new URL("api/setup/media-models/tts/voices/preview", API_BASE), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload || {}),
    });
    if (!res.ok) throw new Error(await res.text() || `TTS voice preview failed (${res.status})`);
    return await res.json();
  },
  uploadSessionFile: async (sessionId, file, path = "inbox") => {
    const form = new FormData();
    form.append("file", file);
    const res = await fetchWithCredentials(new URL(`api/session-tasks/${sessionId}/upload${path ? `?path=${encodeURIComponent(path)}` : ""}`, API_BASE), { method: "POST", body: form });
    if (!res.ok) throw new Error(await parseError(res, `上传失败 (${res.status})`));
    return await res.json();
  },
  uploadTTSReferenceAudio: async (taskId, file, options = {}) => {
    if (Number(file?.size || 0) > TTS_REFERENCE_CHUNK_THRESHOLD_BYTES) {
      return await uploadTTSReferenceAudioChunked(taskId, file, options);
    }
    const form = new FormData();
    form.append("file", file);
    if (typeof options?.onProgress === "function") {
      return await uploadFormData(new URL(`api/openclip/tasks/${taskId}/analysis-v1/tts/reference-audio`, API_BASE), form, options);
    }
    const res = await fetchWithCredentials(new URL(`api/openclip/tasks/${taskId}/analysis-v1/tts/reference-audio`, API_BASE), { method: "POST", body: form });
    if (!res.ok) throw new Error(await parseError(res, `上传失败 (${res.status})`));
    return await res.json();
  },
  runTTSBuilderG: async (taskId, payload) => {
    const res = await fetchWithCredentials(new URL(`api/openclip/tasks/${taskId}/analysis-v1/tts/builder-g`, API_BASE), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...(payload || {}), task_id: taskId }),
    });
    if (!res.ok) throw new Error(await parseError(res, `Builder-G failed (${res.status})`));
    return await res.json();
  },
  quickAdvState: async (taskId, payload = {}) => {
    const res = await fetchWithCredentials(new URL(`api/openclip/tasks/${taskId}/analysis-v1/tts/quick-adv/state`, API_BASE), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...(payload || {}), task_id: taskId }),
    });
    if (!res.ok) throw new Error(await parseError(res, `QuickAdv state failed (${res.status})`));
    return await res.json();
  },
  quickAdvCatalogList: async (taskId, payload = {}) => {
    const res = await fetchWithCredentials(new URL(`api/openclip/tasks/${taskId}/analysis-v1/tts/quick-adv/catalog-list`, API_BASE), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...(payload || {}), task_id: taskId }),
    });
    if (!res.ok) throw new Error(await parseError(res, `QuickAdv catalog failed (${res.status})`));
    return await res.json();
  },
  quickAdvSampleReference: async (taskId, payload = {}) => {
    const res = await fetchWithCredentials(new URL(`api/openclip/tasks/${taskId}/analysis-v1/tts/quick-adv/sample-reference`, API_BASE), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...(payload || {}), task_id: taskId }),
    });
    if (!res.ok) throw new Error(await parseError(res, `QuickAdv sampling failed (${res.status})`));
    return await res.json();
  },
  quickAdvRank: async (taskId, payload = {}) => {
    const res = await fetchWithCredentials(new URL(`api/openclip/tasks/${taskId}/analysis-v1/tts/quick-adv/rank`, API_BASE), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...(payload || {}), task_id: taskId }),
    });
    if (!res.ok) throw new Error(await parseError(res, `QuickAdv ranking failed (${res.status})`));
    return await res.json();
  },
  quickAdvCloneVoice: async (taskId, payload = {}) => {
    const res = await fetchWithCredentials(new URL(`api/openclip/tasks/${taskId}/analysis-v1/tts/quick-adv/clone-voice`, API_BASE), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...(payload || {}), task_id: taskId }),
    });
    if (!res.ok) throw new Error(await parseError(res, `QuickAdv clone failed (${res.status})`));
    return await res.json();
  },
  quickAdvCloneList: async (taskId, payload = {}) => {
    const res = await fetchWithCredentials(new URL(`api/openclip/tasks/${taskId}/analysis-v1/tts/quick-adv/clone-list`, API_BASE), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ clone_page_size: 100, ...(payload || {}), task_id: taskId }),
    });
    if (!res.ok) throw new Error(await parseError(res, `QuickAdv clone list failed (${res.status})`));
    return await res.json();
  },
  quickAdvCloneImport: async (taskId, payload = {}) => {
    const res = await fetchWithCredentials(new URL(`api/openclip/tasks/${taskId}/analysis-v1/tts/quick-adv/clone-import`, API_BASE), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...(payload || {}), task_id: taskId }),
    });
    if (!res.ok) throw new Error(await parseError(res, `QuickAdv clone import failed (${res.status})`));
    return await res.json();
  },
  quickAdvCloneDelete: async (taskId, payload = {}) => {
    const res = await fetchWithCredentials(new URL(`api/openclip/tasks/${taskId}/analysis-v1/tts/quick-adv/clone-delete`, API_BASE), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...(payload || {}), task_id: taskId }),
    });
    if (!res.ok) throw new Error(await parseError(res, `QuickAdv clone delete failed (${res.status})`));
    return await res.json();
  },
  previewTTS: async (taskId, payload) => {
    const res = await fetchWithCredentials(new URL(`api/openclip/tasks/${taskId}/analysis-v1/tts/preview`, API_BASE), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...(payload || {}), task_id: taskId }),
    });
    if (!res.ok) throw new Error(await parseError(res, `TTS preview failed (${res.status})`));
    return await res.json();
  },
  saveTTSSelection: async (taskId, selection = {}) => {
    const res = await fetchWithCredentials(new URL(`api/openclip/tasks/${taskId}/analysis-v1/tts/selection`, API_BASE), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...(selection || {}), task_id: taskId }),
    });
    if (!res.ok) throw new Error(await parseError(res, `Unable to save TTS selection (${res.status})`));
    return await res.json();
  },
  applyStoryBoardTTSSelection: async (taskId, selection = {}) => {
    const prompt = String(selection.prompt || "").trim();
    if (!prompt) throw new Error("试听提示词为空，无法应用到故事版本");
    const detailRes = await fetchWithCredentials(new URL(`api/koubo-storyboard/tasks/${taskId}`, API_BASE));
    if (!detailRes.ok) throw new Error(await parseError(detailRes, `Unable to load StoryBoard (${detailRes.status})`));
    const detail = await detailRes.json();
    const plan = detail?.plan && typeof detail.plan === "object" ? detail.plan : {};
    if (!Array.isArray(plan.shots)) throw new Error("故事版本还没有可保存的镜头结构");
    const previous = plan.storyboard_tts_selection && typeof plan.storyboard_tts_selection === "object" ? plan.storyboard_tts_selection : {};
    const candidateChanged = Boolean(selection.candidate_id && previous.candidate_id && selection.candidate_id !== previous.candidate_id);
    const voiceId = String(selection.voice_id || selection.voice || (!candidateChanged ? previous.voice_id || previous.voice : "") || "").trim();
    const provider = String(selection.provider || (!candidateChanged ? previous.provider : "") || "").trim();
    const model = String(selection.model || (!candidateChanged ? previous.model : "") || "").trim();
    const tempo = Number(selection.tempo ?? previous.tempo ?? 1);
    const nextSelection = {
      ...previous,
      provider,
      provider_label: String(selection.provider_label || selection.providerLabel || (!candidateChanged ? previous.provider_label : "") || "").trim(),
      model,
      model_label: String(selection.model_label || selection.modelLabel || (!candidateChanged ? previous.model_label : "") || "").trim(),
      voice_id: voiceId,
      voice: voiceId || String(previous.voice || "").trim(),
      voice_label: String(selection.voice_label || selection.label || (!candidateChanged ? previous.voice_label : "") || voiceId || "").trim(),
      label: String(selection.label || selection.voice_label || (!candidateChanged ? previous.label : "") || voiceId || "").trim(),
      candidate_id: String(selection.candidate_id || previous.candidate_id || "").trim(),
      voice_source: String(selection.voice_source || previous.voice_source || "").trim(),
      source_clone_provider: String(selection.source_clone_provider || previous.source_clone_provider || "").trim(),
      sample_audio_path: String(selection.sample_audio_path || previous.sample_audio_path || "").trim(),
      prompt,
      prompt_template: prompt,
      scenario_id: String(selection.scenario_id || previous.scenario_id || "").trim(),
      language: String(selection.language || previous.language || "").trim(),
      base_prompt: String(selection.base_prompt || previous.base_prompt || "").trim(),
      source: "analysis_v1_tts_preview_template",
      applied_at: Date.now(),
      tempo: Number.isFinite(tempo) && tempo > 0 ? tempo : 1,
    };
    const nextPlan = { ...plan, storyboard_tts_selection: nextSelection };
    const saveRes = await fetchWithCredentials(new URL(`api/koubo-storyboard/tasks/${taskId}`, API_BASE), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ plan: nextPlan }),
    });
    if (!saveRes.ok) throw new Error(await parseError(saveRes, `Unable to save StoryBoard TTS selection (${saveRes.status})`));
    return await saveRes.json();
  },
  readWorkspaceJson,
};
