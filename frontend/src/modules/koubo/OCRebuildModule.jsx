import { For, Index, Show, createEffect, createMemo, createSignal, onCleanup, onMount, untrack } from "solid-js";
import { Portal } from "solid-js/web";
import OCRebuildHostProductBuilder from "./OCRebuildHostProductBuilder.jsx";
import OCRebuildSrtBuilder from "./OCRebuildSrtBuilder.jsx";
import OCRebuildTTSBuilder from "./OCRebuildTTSBuilder.jsx";
import SharedWorkflowAssistantDrawer from "../../../../WorkflowAssistant/frontend/src/WorkflowAssistantDrawer.jsx";
import { emitDebugCompleted, emitDebugError, emitDebugRequested, emitModelAndWorkflowEvent } from "../../debug/debugAdapter.js";
import { ModelPresetCards, findModelPresetItem } from "../../components/ModelPresetCards.jsx";
import { GOOGLE_TTS_SCENARIO_GUIDES } from "../../shared/tts/googleTtsScenarioGuide";
import "./styles.css";

const API_BASE = (() => {
  const href = window.location.href.split("#")[0];
  return href.endsWith("/") ? href : `${href}/`;
})();

function errorMessageFromResponseText(text, status) {
  const raw = String(text || "").trim();
  if (!raw) return `Request failed (${status})`;
  try {
    const payload = JSON.parse(raw);
    return payload?.detail || payload?.message || raw;
  } catch {
    // Fall through to HTML/provider error handling below.
  }
  const lower = raw.slice(0, 8000).toLowerCase();
  if (lower.includes("<!doctype html") || lower.includes("<html")) {
    if (lower.includes("error code 524") || lower.includes("a timeout occurred")) return "Cloudflare 524 timeout while waiting for the provider/OpenCode tunnel. Please retry after the tunnel recovers.";
    if (lower.includes("cloudflare") || lower.includes("5xx-error-landing")) return "Cloudflare returned an error page while waiting for the provider/OpenCode tunnel. Please retry after the tunnel recovers.";
    return `Server returned an HTML error page (${status}).`;
  }
  return raw;
}

async function throwResponseError(res) {
  throw new Error(errorMessageFromResponseText(await res.text(), res.status));
}

async function request(path, init) {
  const relativePath = path.startsWith("/") ? path.slice(1) : path;
  const res = await fetch(new URL(relativePath, API_BASE), { credentials: "include", headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) }, ...init });
  if (!res.ok) await throwResponseError(res);
  return res.status === 204 ? {} : await res.json();
}

const api = {
  tasks: () => request("/api/ocrebuild/tasks"),
  createTask: () => request("/api/ocrebuild/tasks", { method: "POST" }),
  createStoryBoardCopy: (taskId) => request(`/api/ocstoryboard/copy-from-rebuild/${taskId}`, { method: "POST" }),
  storyBoardDetail: (taskId) => request(`/api/ocstoryboard/tasks/${taskId}`),
  taskDetail: (taskId) => request(`/api/ocrebuild/tasks/${taskId}`),
  shotPlan: (taskId) => request(`/api/ocrebuild/tasks/${taskId}/shot-plan`),
  deleteTask: (taskId) => request(`/api/ocrebuild/tasks/${taskId}`, { method: "DELETE" }),
  saveConfig: (taskId, payload) => request(`/api/ocrebuild/tasks/${taskId}/config`, { method: "PUT", body: JSON.stringify({ ...payload, task_id: taskId }) }),
  rebuildSimplePrompt: (taskId, payload) => request(`/api/ocrebuild/tasks/${taskId}/simple-prompt/rebuild`, { method: "POST", body: JSON.stringify({ ...payload, task_id: taskId }) }),
  generatePrompt: (taskId, payload) => request(`/api/ocrebuild/tasks/${taskId}/generate-prompt`, { method: "POST", body: JSON.stringify({ ...payload, task_id: taskId }) }),
  saveVersion: (taskId, payload) => request(`/api/ocrebuild/tasks/${taskId}/versions`, { method: "POST", body: JSON.stringify({ ...payload, task_id: taskId }) }),
  updateVersion: (taskId, versionId) => request(`/api/ocrebuild/tasks/${taskId}/versions/${versionId}`, { method: "PUT" }),
  loadVersion: (taskId, versionId) => request(`/api/ocrebuild/tasks/${taskId}/versions/load`, { method: "POST", body: JSON.stringify({ task_id: taskId, version_id: versionId }) }),
  deleteVersion: (taskId, versionId) => request(`/api/ocrebuild/tasks/${taskId}/versions/${versionId}`, { method: "DELETE" }),
  run: (taskId, payload) => request(`/api/ocrebuild/tasks/${taskId}/run`, { method: "POST", body: JSON.stringify({ ...payload, task_id: taskId }) }),
  hostProductBuilder: (taskId) => request(`/api/ocrebuild/tasks/${taskId}/host-product-builder`),
  generateHostProductPrompt: (taskId, payload) => request(`/api/ocrebuild/tasks/${taskId}/host-product-builder/prompt`, { method: "POST", body: JSON.stringify(payload) }),
  uploadHostProductRefs: async (taskId, kind, files) => {
    const form = new FormData();
    form.append("kind", kind);
    Array.from(files || []).forEach((file) => form.append("files", file));
    const res = await fetch(new URL(`api/ocrebuild/tasks/${taskId}/host-product-builder/uploads`, API_BASE), { method: "POST", credentials: "include", body: form });
    if (!res.ok) await throwResponseError(res);
    return await res.json();
  },
  deleteHostProductRef: (taskId, payload) => request(`/api/ocrebuild/tasks/${taskId}/host-product-builder/reference`, { method: "DELETE", body: JSON.stringify(payload) }),
  uploadHostProductOutput: async (taskId, kind, file) => {
    const form = new FormData();
    form.append("kind", kind);
    form.append("file", file);
    const res = await fetch(new URL(`api/ocrebuild/tasks/${taskId}/host-product-builder/output`, API_BASE), { method: "POST", credentials: "include", body: form });
    if (!res.ok) await throwResponseError(res);
    return await res.json();
  },
  uploadShotTTSReferenceAudio: async (taskId, workflowId, file) => {
    const form = new FormData();
    form.append("workflow_id", workflowId);
    form.append("file", file);
    const res = await fetch(new URL(`api/ocrebuild/tasks/${taskId}/shot-tts/reference-audio`, API_BASE), { method: "POST", credentials: "include", body: form });
    if (!res.ok) await throwResponseError(res);
    return await res.json();
  },
  deleteHostProductOutput: (taskId, payload) => request(`/api/ocrebuild/tasks/${taskId}/host-product-builder/output`, { method: "DELETE", body: JSON.stringify(payload) }),
  streamHostProductImage: async (taskId, payload, onEvent) => {
    const res = await fetch(new URL(`api/ocrebuild/tasks/${taskId}/host-product-builder/generate/events`, API_BASE), { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
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
  generateAssetImage: (taskId, payload) => request(`/api/ocrebuild/tasks/${taskId}/asset-image/generate`, { method: "POST", body: JSON.stringify(payload) }),
  refineAssetPrompt: (taskId, payload) => request(`/api/ocrebuild/tasks/${taskId}/asset-image/prompt/refine`, { method: "POST", body: JSON.stringify(payload) }),
  getAssetImageWorkflow: (taskId, workflowId) => request(`/api/ocrebuild/tasks/${taskId}/asset-image/workflows/${encodeURIComponent(workflowId)}`),
  saveAssetImageWorkflow: (taskId, workflowId, workflow) => request(`/api/ocrebuild/tasks/${taskId}/asset-image/workflows/${encodeURIComponent(workflowId)}`, { method: "PUT", body: JSON.stringify({ workflow }) }),
  imageModelConfig: () => request("/api/setup/media-models/image/config"),
  videoModelConfig: () => request("/api/setup/media-models/video/config"),
  ttsModelConfig: () => request("/api/setup/media-models/tts/config"),
  previewTTSVoice: (payload) => request("/api/setup/media-models/tts/voices/preview", { method: "POST", body: JSON.stringify(payload) }),
  copyReferenceAssetImage: (taskId, payload) => request(`/api/ocrebuild/tasks/${taskId}/asset-image/copy-reference`, { method: "POST", body: JSON.stringify(payload) }),
  deleteAssetImage: (taskId, payload) => request(`/api/ocrebuild/tasks/${taskId}/asset-image/delete`, { method: "POST", body: JSON.stringify(payload) }),
  finalizeCompareAssetImage: (taskId, payload) => request(`/api/ocrebuild/tasks/${taskId}/asset-image/compare/finalize`, { method: "POST", body: JSON.stringify(payload) }),
  getAssetTTSWorkflow: (taskId, workflowId) => request(`/api/ocrebuild/tasks/${taskId}/asset-tts/workflows/${encodeURIComponent(workflowId)}`),
  refineAssetTTSPrompt: (taskId, payload) => request(`/api/ocrebuild/tasks/${taskId}/asset-tts/prompt/refine`, { method: "POST", body: JSON.stringify(payload) }),
  finalizeCompareAssetTTS: (taskId, payload) => request(`/api/ocrebuild/tasks/${taskId}/asset-tts/compare/finalize`, { method: "POST", body: JSON.stringify(payload) }),
  getShotTTSWorkflow: (taskId, workflowId) => request(`/api/ocrebuild/tasks/${taskId}/shot-tts/workflows/${encodeURIComponent(workflowId)}`),
  recommendShotTTSVoice: (taskId, payload) => request(`/api/ocrebuild/tasks/${taskId}/shot-tts/recommend`, { method: "POST", body: JSON.stringify(payload) }),
  buildShotTTSVoice: (taskId, payload) => request(`/api/ocrebuild/tasks/${taskId}/shot-tts/builder`, { method: "POST", body: JSON.stringify(payload) }),
  saveShotTTSVoiceSelection: (taskId, payload) => request(`/api/ocrebuild/tasks/${taskId}/shot-tts/voice-selection`, { method: "PUT", body: JSON.stringify(payload) }),
  refineShotTTSPrompt: (taskId, payload) => request(`/api/ocrebuild/tasks/${taskId}/shot-tts/prompt/refine`, { method: "POST", body: JSON.stringify(payload) }),
  finalizeShotTTS: (taskId, payload) => request(`/api/ocrebuild/tasks/${taskId}/shot-tts/finalize`, { method: "POST", body: JSON.stringify(payload) }),
  streamShotTTS: async (taskId, payload, onEvent) => {
    const res = await fetch(new URL(`api/ocrebuild/tasks/${taskId}/shot-tts/events`, API_BASE), { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    if (!res.ok) throw new Error(await res.text() || `Request failed (${res.status})`);
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
  streamCompareAssetTTS: async (taskId, payload, onEvent) => {
    const res = await fetch(new URL(`api/ocrebuild/tasks/${taskId}/asset-tts/compare/events`, API_BASE), { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    if (!res.ok) throw new Error(await res.text() || `Request failed (${res.status})`);
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
  refineAssetVideoPrompt: (taskId, payload) => request(`/api/ocrebuild/tasks/${taskId}/asset-video/prompt/refine`, { method: "POST", body: JSON.stringify(payload) }),
  refineShotMultiReferencePrompt: (taskId, payload) => request(`/api/ocrebuild/tasks/${taskId}/shot-video/r2v/prompt/refine`, { method: "POST", body: JSON.stringify(payload) }),
  getAssetVideoWorkflow: (taskId, workflowId) => request(`/api/ocrebuild/tasks/${taskId}/asset-video/workflows/${encodeURIComponent(workflowId)}`),
  getShotMultiReferenceWorkflow: (taskId, workflowId) => request(`/api/ocrebuild/tasks/${taskId}/shot-video/r2v/workflows/${encodeURIComponent(workflowId)}`),
  saveAssetVideoWorkflow: (taskId, workflowId, workflow) => request(`/api/ocrebuild/tasks/${taskId}/asset-video/workflows/${encodeURIComponent(workflowId)}`, { method: "PUT", body: JSON.stringify({ workflow }) }),
  finalizeCompareAssetVideo: (taskId, payload) => request(`/api/ocrebuild/tasks/${taskId}/asset-video/compare/finalize`, { method: "POST", body: JSON.stringify(payload) }),
  finalizeShotMultiReferenceVideo: (taskId, payload) => request(`/api/ocrebuild/tasks/${taskId}/shot-video/r2v/finalize`, { method: "POST", body: JSON.stringify(payload) }),
  streamShotMultiReferenceVideo: async (taskId, payload, onEvent) => {
    const res = await fetch(new URL(`api/ocrebuild/tasks/${taskId}/shot-video/r2v/events`, API_BASE), { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    if (!res.ok) throw new Error(await res.text() || `Request failed (${res.status})`);
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
  streamCompareAssetVideos: async (taskId, payload, onEvent) => {
    const res = await fetch(new URL(`api/ocrebuild/tasks/${taskId}/asset-video/compare/events`, API_BASE), { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    if (!res.ok) throw new Error(await res.text() || `Request failed (${res.status})`);
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
  streamCompareAssetImages: async (taskId, payload, onEvent) => {
    const res = await fetch(new URL(`api/ocrebuild/tasks/${taskId}/asset-image/compare/events`, API_BASE), { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    if (!res.ok) throw new Error(await res.text() || `Request failed (${res.status})`);
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
  generateAssetImageEventsUrl: (taskId, payload) => {
    const params = new URLSearchParams({ shot_id: payload.shot_id || "", scene_mark_id: payload.scene_mark_id || "", role: payload.role || "single", use_reference_image: payload.use_reference_image ? "true" : "false" });
    return new URL(`api/ocrebuild/tasks/${taskId}/asset-image/generate/events?${params.toString()}`, API_BASE).toString();
  },
  saveShotKeyframes: (taskId, payload) => request(`/api/ocrebuild/tasks/${taskId}/shot-plan/keyframes`, { method: "PUT", body: JSON.stringify(payload) }),
  saveShotSceneMarks: (taskId, payload) => request(`/api/ocrebuild/tasks/${taskId}/shot-plan/scene-marks`, { method: "PUT", body: JSON.stringify(payload) }),
  getShotFinalPrompts: (taskId, shotId) => request(`/api/ocrebuild/tasks/${taskId}/shot-plan/final-prompts/${encodeURIComponent(shotId)}`),
  saveShotFinalPrompts: (taskId, shotId, payload) => request(`/api/ocrebuild/tasks/${taskId}/shot-plan/final-prompts/${encodeURIComponent(shotId)}`, { method: "PUT", body: JSON.stringify(payload) }),
  generateSrtRewrite: (taskId, payload) => request(`/api/ocrebuild/tasks/${taskId}/srt-rewrite/generate`, { method: "POST", body: JSON.stringify(payload) }),
  saveSrtRewrite: (taskId, payload) => request(`/api/ocrebuild/tasks/${taskId}/srt-rewrite`, { method: "PUT", body: JSON.stringify(payload) }),
  rawFileUrl: (sessionId, filePath) => new URL(`api/session-tasks/${sessionId}/raw/${filePath.split("/").map(encodeURIComponent).join("/")}`, API_BASE).toString(),
  taskDetailUrl: (taskId) => `#/ocrebuild/tasks/${taskId}`,
};

const DEFAULT_RUN_PROVIDER_ID = "openai";
const DEFAULT_RUN_MODEL_ID = "gpt-5.5";
const SOURCE_SCHEME_LABELS = { detail: "详细", balanced: "均衡", summary: "汇总" };
const IMAGE_DOC_URLS = {
  openai: "https://developers.openai.com/cookbook/examples/multimodal/image-gen-models-prompting-guide#1-introduction",
  xai: "https://docs.x.ai/developers/model-capabilities/images/generation",
  gemini: "https://ai.google.dev/gemini-api/docs/image-generation?hl=zh-cn",
};

function CodeIcon() { return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>; }
function PlayIcon() { return <svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5.5a1 1 0 0 1 1.5-.86l8 5a1 1 0 0 1 0 1.72l-8 5A1 1 0 0 1 8 15.5z"/></svg>; }
function PauseIcon() { return <svg viewBox="0 0 24 24" fill="currentColor"><path d="M7 5h3v14H7z"/><path d="M14 5h3v14h-3z"/></svg>; }
function TrashIcon() { return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 13a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/></svg>; }
function CloseIcon() { return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>; }
function SlidersIcon() { return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 17H5"/><path d="M19 7h-9"/><circle cx="17" cy="17" r="3"/><circle cx="7" cy="7" r="3"/></svg>; }
function SaveIcon() { return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/></svg>; }
function CopyIcon() { return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/></svg>; }
function SpeakerIcon() { return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M11 5 6 9H3v6h3l5 4z"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/><path d="M18.5 5.5a9 9 0 0 1 0 13"/></svg>; }
function UploadIcon() { return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 13v8"/><path d="M4 14.899A7 7 0 1 1 15.71 8h1.79a4.5 4.5 0 0 1 2.5 8.242"/><path d="m8 17 4-4 4 4"/></svg>; }
function UndoIcon() { return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 1 1 0 11H11"/></svg>; }
function ArrowsClockwiseIcon() { return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/></svg>; }
function ClockCounterClockwiseIcon() { return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 10 7 10"/><path d="M3.5 15a9 9 0 1 0 2.13-9.36L1 10"/><path d="M12 7v5l3 2"/></svg>; }
function DocumentIcon() { return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M10 9H8"/><path d="M16 13H8"/><path d="M16 17H8"/></svg>; }
function PromptPackageIcon() { return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H7a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7z"/><path d="M14 2v5h5"/><path d="M8 11h8"/><path d="M8 15h4"/><circle cx="16" cy="16" r="2.2"/><path d="M16 13.8v4.4"/></svg>; }
function SubtitleEditIcon() { return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="14" rx="3"/><path d="M7 11h10"/><path d="M7 15h6"/></svg>; }
function HostProductIcon() { return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2L2 12l10 10 10-10Z"/><circle cx="12" cy="9.5" r="2"/><path d="M8.5 15.5c0-1.5 1.5-2.5 3.5-2.5s3.5 1 3.5 2.5"/></svg>; }
function PlusIcon() { return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M12 5v14"/><path d="M5 12h14"/></svg>; }
function FrameModeIcon(props) { return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><Show when={props.mode === "first_last"} fallback={<rect x="4" y="6" width="16" height="12" rx="1.5"/>}><rect x="4" y="6" width="6" height="12" rx="1.5"/><rect x="14" y="6" width="6" height="12" rx="1.5"/></Show><path d="M7 9h1"/><Show when={props.mode === "first_last"}><path d="M17 15h1"/></Show></svg>; }
function ShotPreviewModeIcon(props) { return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><Show when={props.mode === "asset"} fallback={<><rect x="3.5" y="6" width="12" height="12" rx="2"/><path d="m15.5 10 5-3v10l-5-3"/><path d="M7.5 10h4"/><path d="M7.5 14h2.5"/></>}><rect x="3.5" y="5" width="17" height="14" rx="2"/><circle cx="8.5" cy="9.5" r="1.3"/><path d="m5.5 17 4.7-4.7 2.8 2.8 2.3-2.3 3.2 4.2"/><path d="M16.5 7.5h2"/></Show></svg>; }
function VoiceMatchIcon() { return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M4 10v4"/><path d="M8 7v10"/><path d="M12 3v18"/><path d="M16 6v12"/><path d="M20 9v6"/></svg>; }
function FilterIcon() { return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5h16"/><path d="M7 12h10"/><path d="M10 19h4"/></svg>; }
function ChevronDownIcon() { return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6"/></svg>; }
function ChevronUpIcon() { return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m18 15-6-6-6 6"/></svg>; }

function StatusBadge(props) { return <span class={`status-tag tag-${String(props.status || "draft").includes("running") ? "available" : String(props.status || "") === "failed" ? "failed" : "idle"}`}>{String(props.status || "draft")}</span>; }
function taskIdFromHash(hash) { const match = String(hash || "").match(/^#\/ocrebuild\/tasks\/(\d+)/); return match ? Number(match[1]) : null; }
function optionValue(options, current) { return options.includes(current) ? current : "__custom__"; }
function formatTime(value) { return value ? new Date(value).toLocaleString([], { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }) : "-"; }
function findPreferredRunModel(models) { return (models || []).find((item) => String(item.providerID).toLowerCase() === DEFAULT_RUN_PROVIDER_ID && String(item.modelID).toLowerCase() === DEFAULT_RUN_MODEL_ID) ?? null; }
function modelDetail(model) { return model ? [model.reasoning ? "支持推理" : "", model.inputModalities?.length ? `支持 ${model.inputModalities.join("/")}` : "", model.contextLimit ? `上下文 ${Number(model.contextLimit).toLocaleString()}` : ""].filter(Boolean).join(" · ") : "-"; }
function sourceSchemeLabel(value) { return SOURCE_SCHEME_LABELS[String(value || "")] || String(value || "详细"); }
function splitMultiValue(value) { return String(value || "").split(/[、,，]/).map((item) => item.trim()).filter(Boolean); }
function normalizeWorkflowId(value) { return String(value || "").trim().replace(/[^a-zA-Z0-9_-]+/g, "_").slice(0, 80) || `imgwf_${Date.now()}`; }
function sceneWorkflowId(pkg, role) {
  const shotId = String(pkg?.shot_id || "shot").trim();
  const sceneId = String(pkg?.scene_mark_id || "scene").trim();
  return normalizeWorkflowId(`${sceneId.startsWith(`${shotId}_`) ? sceneId : `${shotId}_${sceneId}`}_${role || "single"}`);
}
const VIDEO_PROMPT_FIELDS = [
  { key: "positive", zh: "正向", en: "Positive", rows: 4 },
  { key: "character_action", zh: "人物动作", en: "Character Action", rows: 3 },
  { key: "speech_speed", zh: "语言速度", en: "Speech Speed", rows: 2 },
  { key: "voice_description", zh: "语音描述", en: "Voice Description", rows: 2 },
  { key: "camera_motion", zh: "镜头运动", en: "Camera Motion", rows: 2 },
  { key: "scene_consistency", zh: "场景一致性", en: "Scene Consistency", rows: 3 },
  { key: "product_consistency", zh: "产品一致性", en: "Product Consistency", rows: 3 },
  { key: "negative", zh: "负向", en: "Negative", rows: 3 },
  { key: "model_notes", zh: "模型备注", en: "Model Notes", rows: 2 },
];
const VIDEO_PROMPT_LANG_LABELS = { zh: "中文", en: "English" };

export default function OCRebuildModule(props) {
  const [tasks, setTasks] = createSignal([]);
  const [detail, setDetail] = createSignal(null);
  const [draft, setDraft] = createSignal(null);
  const [selectedTaskId, setSelectedTaskId] = createSignal(taskIdFromHash(props.routeHash));
  const [error, setError] = createSignal("");
  const [busy, setBusy] = createSignal("");
  const [taskListOpen, setTaskListOpen] = createSignal(false);
  const [promptDrawerOpen, setPromptDrawerOpen] = createSignal(false);
  const [promptModelDialogOpen, setPromptModelDialogOpen] = createSignal(false);
  const [assistantOpen, setAssistantOpen] = createSignal(false);
  const [runModelDialogOpen, setRunModelDialogOpen] = createSignal(false);
  const [promptPreviewOpen, setPromptPreviewOpen] = createSignal(false);
  const [shotPlan, setShotPlan] = createSignal(null);
  const [sourceSrtTexts, setSourceSrtTexts] = createSignal({});
  const [lockedTTSFiles, setLockedTTSFiles] = createSignal({});
  const [platformMenuOpen, setPlatformMenuOpen] = createSignal(false);
  const [promptModelFilter, setPromptModelFilter] = createSignal("");
  const [runModelFilter, setRunModelFilter] = createSignal("");
  const [shotViewMode, setShotViewMode] = createSignal("card");
  const [shotCardColumns, setShotCardColumns] = createSignal(3);
  const [shotViewMenuOpen, setShotViewMenuOpen] = createSignal(false);
  const [selectedShotIndex, setSelectedShotIndex] = createSignal(0);
  const [shotDetailTable, setShotDetailTable] = createSignal(null);
  const [sceneMarkDialog, setSceneMarkDialog] = createSignal(null);
  const [sceneSrtDraft, setSceneSrtDraft] = createSignal("");
  const [srtRewriteDialogOpen, setSrtRewriteDialogOpen] = createSignal(false);
  const [assetPromptDialog, setAssetPromptDialog] = createSignal(null);
  const [shotPromptPackageDialog, setShotPromptPackageDialog] = createSignal(null);
  const [shotPromptPackageTab, setShotPromptPackageTab] = createSignal("image");
  const [assetImageViewer, setAssetImageViewer] = createSignal(null);
  const [shotAssetViewer, setShotAssetViewer] = createSignal(null);
  const [keyframeContextMenu, setKeyframeContextMenu] = createSignal(null);
  const [assetImageContextMenu, setAssetImageContextMenu] = createSignal(null);
  const [assetVideoContextMenu, setAssetVideoContextMenu] = createSignal(null);
  const [paramsCollapsed, setParamsCollapsed] = createSignal(true);
  const [keyframeEdits, setKeyframeEdits] = createSignal({});
  const [globalShotPreviewMode, setGlobalShotPreviewMode] = createSignal("source");
  const [shotPreviewMode, setShotPreviewMode] = createSignal({});
  const [assetTasks, setAssetTasks] = createSignal(null);
  const [assetTasksLoadedSessionId, setAssetTasksLoadedSessionId] = createSignal(null);
  const [assetPromptPackages, setAssetPromptPackages] = createSignal({});
  const [missingAssetImages, setMissingAssetImages] = createSignal({});
  const [missingShotAssetVideos, setMissingShotAssetVideos] = createSignal({});
  const [planCScenePlans, setPlanCScenePlans] = createSignal({});
  const [imageModelConfig, setImageModelConfig] = createSignal(null);
  const [videoModelConfig, setVideoModelConfig] = createSignal(null);
  const [ttsModelConfig, setTTSModelConfig] = createSignal(null);
  const [assetCompareWorkflows, setAssetCompareWorkflows] = createSignal({});
  const [assetCompareDialogPositions, setAssetCompareDialogPositions] = createSignal({});
  const [assetCompareExpandedSteps, setAssetCompareExpandedSteps] = createSignal({});
  const [assetVideoWorkflows, setAssetVideoWorkflows] = createSignal({});
  const [assetVideoDialogPositions, setAssetVideoDialogPositions] = createSignal({});
  const [assetVideoExpandedSteps, setAssetVideoExpandedSteps] = createSignal({});
  const [shotMultiReferenceWorkflows, setShotMultiReferenceWorkflows] = createSignal({});
  const [shotMultiReferenceDialogPositions, setShotMultiReferenceDialogPositions] = createSignal({});
  const [assetTTSWorkflows, setAssetTTSWorkflows] = createSignal({});
  const [assetTTSDialogPositions, setAssetTTSDialogPositions] = createSignal({});
  const [voiceGuideDialog, setVoiceGuideDialog] = createSignal(null);
  const [hostProductBuilderOpen, setHostProductBuilderOpen] = createSignal(false);
  let voicePreviewAudio = null;
  let voicePreviewRaf = 0;
  let voiceGuideSimplePromptEl = null;
  let voiceGuideComplexPromptEl = null;
  onCleanup(() => {
    if (voicePreviewRaf) cancelAnimationFrame(voicePreviewRaf);
    if (voicePreviewAudio) voicePreviewAudio.pause();
  });

  const task = createMemo(() => detail()?.task ?? null);
  const options = createMemo(() => detail()?.options ?? {});
  const promptModels = createMemo(() => detail()?.prompt_models ?? { items: [], default_model: { providerID: "", modelID: "" } });
  const runModels = createMemo(() => promptModels().items ?? []);
  const selectedPromptModel = createMemo(() => runModels().find((item) => item.providerID === draft()?.prompt_model_provider && item.modelID === draft()?.prompt_model_id));
  const selectedRunModel = createMemo(() => runModels().find((item) => item.providerID === draft()?.run_model_provider && item.modelID === draft()?.run_model_id));
  const shotPlanShots = createMemo(() => shotPlan()?.shots || []);
  const selectedShot = createMemo(() => shotPlanShots()[selectedShotIndex()] || shotPlanShots()[0] || null);
  const storyboardPhase2 = createMemo(() => detail()?.storyboard_phase2 || shotPlan()?.storyboard_phase2 || {});
  const isStoryboardReferencePhase2 = createMemo(() => {
    const phase2 = storyboardPhase2();
    return Boolean(
      phase2?.no_source_video_required
      || phase2?.boundary === "storyboard_to_reference_ready_phase2"
      || (phase2?.storyboard_images_role === "reference_image_only" && phase2?.final_images === "empty_until_manual_import")
    );
  });
  const shotMode = (shotId) => isStoryboardReferencePhase2() ? "asset" : (shotPreviewMode()[shotId] || globalShotPreviewMode());
  const selectedShotPreviewMode = createMemo(() => selectedShot()?.shot_id ? shotMode(selectedShot().shot_id) : globalShotPreviewMode());
  const allShotPreviewMode = createMemo(() => {
    if (isStoryboardReferencePhase2()) return "asset";
    const shots = shotPlanShots();
    if (!shots.length) return globalShotPreviewMode();
    const modes = new Set(shots.map((shot) => shotMode(shot?.shot_id)));
    return modes.size === 1 ? Array.from(modes)[0] : "mixed";
  });
  const providerItems = createMemo(() => Array.from(new Map(runModels().map((item) => [item.providerID, item.providerName])).entries()).map(([providerID, providerName]) => ({ providerID, providerName })));
  const filteredPromptModels = createMemo(() => {
    const providerID = draft()?.prompt_model_provider;
    const keyword = promptModelFilter().trim().toLowerCase();
    return runModels().filter((item) => {
      if (providerID && item.providerID !== providerID)
        return false;
      if (!keyword)
        return true;
      return `${item.providerName} ${item.modelName} ${item.modelID}`.toLowerCase().includes(keyword);
    });
  });
  const filteredRunModels = createMemo(() => {
    const providerID = draft()?.run_model_provider;
    const keyword = runModelFilter().trim().toLowerCase();
    return runModels().filter((item) => {
      if (providerID && item.providerID !== providerID)
        return false;
      if (!keyword)
        return true;
      return `${item.providerName} ${item.modelName} ${item.modelID}`.toLowerCase().includes(keyword);
    });
  });
  const assetCompareWorkflowList = createMemo(() => Object.values(assetCompareWorkflows()));
  const assetVideoWorkflowList = createMemo(() => Object.values(assetVideoWorkflows()));
  const shotMultiReferenceWorkflowList = createMemo(() => Object.values(shotMultiReferenceWorkflows()));
  const assetTTSWorkflowList = createMemo(() => Object.values(assetTTSWorkflows()));

  const debugContext = (extra = {}) => ({
    session_id: task()?.session_id || null,
    task_id: selectedTaskId(),
    ...extra,
  });
  const emitStreamDebug = (event, extra = {}) => emitModelAndWorkflowEvent(event, debugContext(extra));
  const runAction = async (key, fn) => {
    setBusy(key);
    setError("");
    try {
      await fn();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setError(message);
      emitDebugError(err, debugContext({ family: "error", request_id: key }));
    } finally {
      setBusy("");
    }
  };
  const defaultCompareExpandedSteps = () => ({ mode: true, prompts: false, firstRound: false });
  const compareExpandedStepsFor = (workflowId) => assetCompareExpandedSteps()[workflowId] || defaultCompareExpandedSteps();
  const defaultVideoExpandedSteps = () => ({ mode: true, prompts: false, results: false });
  const videoExpandedStepsFor = (workflowId) => assetVideoExpandedSteps()[workflowId] || defaultVideoExpandedSteps();
  const updateAssetCompareWorkflow = (workflowId, updater) => setAssetCompareWorkflows((prev) => {
    const current = prev[workflowId];
    const next = typeof updater === "function" ? updater(current) : updater;
    if (!next) {
      const { [workflowId]: _removed, ...rest } = prev;
      return rest;
    }
    return { ...prev, [workflowId]: next };
  });
  const updateAssetTTSWorkflow = (workflowId, updater) => setAssetTTSWorkflows((prev) => {
    const current = prev[workflowId];
    const next = typeof updater === "function" ? updater(current) : updater;
    if (!next) {
      const copy = { ...prev };
      delete copy[workflowId];
      return copy;
    }
    return { ...prev, [workflowId]: next };
  });
  const closeAssetTTSWorkflow = (workflowId) => { updateAssetTTSWorkflow(workflowId, null); setAssetTTSDialogPositions((prev) => { const copy = { ...prev }; delete copy[workflowId]; return copy; }); };
  const updateCompareExpandedSteps = (workflowId, updater) => setAssetCompareExpandedSteps((prev) => {
    const current = prev[workflowId] || defaultCompareExpandedSteps();
    const next = typeof updater === "function" ? updater(current) : updater;
    return { ...prev, [workflowId]: next };
  });
  const closeAssetCompareWorkflow = (workflowId) => {
    updateAssetCompareWorkflow(workflowId, null);
    setAssetCompareDialogPositions((prev) => { const { [workflowId]: _removed, ...rest } = prev; return rest; });
    setAssetCompareExpandedSteps((prev) => { const { [workflowId]: _removed, ...rest } = prev; return rest; });
  };
  const updateAssetVideoWorkflow = (workflowId, updater) => setAssetVideoWorkflows((prev) => {
    const current = prev[workflowId];
    const next = typeof updater === "function" ? updater(current) : updater;
    if (!next) { const { [workflowId]: _removed, ...rest } = prev; return rest; }
    return { ...prev, [workflowId]: next };
  });
  const updateVideoExpandedSteps = (workflowId, updater) => setAssetVideoExpandedSteps((prev) => {
    const current = prev[workflowId] || defaultVideoExpandedSteps();
    const next = typeof updater === "function" ? updater(current) : updater;
    return { ...prev, [workflowId]: next };
  });
  const closeAssetVideoWorkflow = (workflowId) => {
    updateAssetVideoWorkflow(workflowId, null);
    setAssetVideoDialogPositions((prev) => { const { [workflowId]: _removed, ...rest } = prev; return rest; });
    setAssetVideoExpandedSteps((prev) => { const { [workflowId]: _removed, ...rest } = prev; return rest; });
  };
  const updateShotMultiReferenceWorkflow = (workflowId, updater) => setShotMultiReferenceWorkflows((prev) => {
    const current = prev[workflowId];
    const next = typeof updater === "function" ? updater(current) : updater;
    if (!next) { const { [workflowId]: _removed, ...rest } = prev; return rest; }
    return { ...prev, [workflowId]: next };
  });
  const closeShotMultiReferenceWorkflow = (workflowId) => {
    updateShotMultiReferenceWorkflow(workflowId, null);
    setShotMultiReferenceDialogPositions((prev) => { const { [workflowId]: _removed, ...rest } = prev; return rest; });
  };
  const updateDraft = (key, value) => setDraft((prev) => ({ ...prev, [key]: value }));
  const updateStrategy = (group, key, checked) => setDraft((prev) => ({ ...prev, [group]: { ...(prev?.[group] || {}), [key]: checked } }));
  const togglePlatform = (value) => {
    const selected = new Set(splitMultiValue(draft()?.target_platform));
    if (selected.has(value)) selected.delete(value);
    else selected.add(value);
    updateDraft("target_platform", Array.from(selected).join("、"));
  };

  function syncDetail(payload) {
    setDetail(payload);
    const models = payload.prompt_models ?? promptModels();
    const defaultProvider = models.default_model?.providerID || "";
    const defaultModel = models.default_model?.modelID || "";
    const defaultPromptModel = findModelPresetItem(models.items, "max") ?? findModelPresetItem(models.items, "flash") ?? null;
    const defaultRunModel = findPreferredRunModel(models.items) ?? models.items?.find((item) => item.providerID === defaultProvider && item.modelID === defaultModel) ?? null;
    setDraft({
      ...payload.task,
      preserve_strategy: payload.task?.preserve_strategy || {},
      replace_strategy: payload.task?.replace_strategy || {},
      prompt_model_provider: payload.task?.prompt_model_provider || defaultPromptModel?.providerID || defaultProvider,
      prompt_model_id: payload.task?.prompt_model_id || defaultPromptModel?.modelID || defaultModel,
      run_model_provider: payload.task?.run_model_provider || defaultRunModel?.providerID || defaultProvider,
      run_model_id: payload.task?.run_model_id || defaultRunModel?.modelID || defaultModel,
    });
    setSelectedTaskId(payload.task?.id ?? null);
    props.onDebugSessionChange?.(payload.task?.session_id ?? null);
  }

  async function loadTasks() { const res = await api.tasks(); setTasks(res.items ?? []); }
  async function loadTask(taskId) { if (!taskId) return; const res = await api.taskDetail(taskId); syncDetail(res); }
  async function refreshCurrentTask() { if (selectedTaskId()) { await loadTask(selectedTaskId()); await loadTasks(); } }
  async function createTask() { const res = await api.createTask(); syncDetail(res); await loadTasks(); setTaskListOpen(false); window.location.hash = api.taskDetailUrl(res.task.id); }
  async function deleteTask(taskId) { if (!window.confirm(`Delete OC-Rebuild task #${taskId}?`)) return; await api.deleteTask(taskId); if (selectedTaskId() === taskId) { setDetail(null); setDraft(null); window.location.hash = "#/ocrebuild/tasks"; } await loadTasks(); }
  async function saveConfig() { const res = await api.saveConfig(selectedTaskId(), draft()); syncDetail({ ...res, prompt_models: promptModels() }); await loadTasks(); return res; }
  async function saveDraftOrCurrentVersion() { const saved = await saveConfig(); const versionId = saved?.task?.current_version_id; if (!versionId) return saved; const res = await api.updateVersion(selectedTaskId(), versionId); syncDetail({ ...res, prompt_models: promptModels() }); return res; }
  async function rebuildSimplePrompt() { await saveConfig(); const res = await api.rebuildSimplePrompt(selectedTaskId(), draft()); syncDetail({ ...res, prompt_models: promptModels() }); }
  async function generatePrompt() { await saveConfig(); const res = await api.generatePrompt(selectedTaskId(), { prompt_model_provider: draft().prompt_model_provider, prompt_model_id: draft().prompt_model_id }); syncDetail(res); setPromptModelDialogOpen(false); }
  async function saveVersion() { await saveConfig(); const res = await api.saveVersion(selectedTaskId(), { version_name: "", version_notes: draft().version_notes || "Saved Rebuild intent" }); syncDetail({ ...res, prompt_models: promptModels() }); }
  async function loadVersion(versionId) { const res = await api.loadVersion(selectedTaskId(), versionId); syncDetail({ ...res, prompt_models: promptModels() }); }
  async function deleteVersion(versionId) { if (!window.confirm("Delete this Intent version?")) return; const res = await api.deleteVersion(selectedTaskId(), versionId); syncDetail({ ...res, prompt_models: promptModels() }); }
  async function runTask() { await api.run(selectedTaskId(), { run_model_provider: draft().run_model_provider, run_model_id: draft().run_model_id }); setRunModelDialogOpen(false); await refreshCurrentTask(); }
  async function loadShotPlan() { const taskId = task()?.id; if (!taskId) return; try { setShotPlan(await api.shotPlan(taskId)); } catch { setShotPlan(null); } setSourceSrtTexts({}); setLockedTTSFiles({}); setKeyframeEdits({}); setKeyframeContextMenu(null); }
  function updateShotPromptPackageDraft(updater) {
    setShotPromptPackageDialog((prev) => {
      if (!prev) return prev;
      const next = typeof updater === "function" ? updater(prev) : updater;
      return next ? { ...prev, ...next } : prev;
    });
  }
  function selectedPromptPackageScene(dialog = shotPromptPackageDialog()) {
    const scenes = dialog?.package?.scenes || [];
    return scenes.find((item) => String(item?.scene_mark_id || "") === String(dialog?.sceneId || "")) || scenes[0] || null;
  }
  function promptLanguage(scene) {
    return scene?.active_language === "zh" ? "zh" : "en";
  }
  function normalizePromptPackage(pkg) {
    const scenes = (pkg?.scenes || []).map((scene) => {
      return {
        scene_mark_id: scene?.scene_mark_id || "",
        reference_image: scene?.reference_image || "",
        image_prompt: scene?.image_prompt || scene?.plan_d_image_prompt || "",
        video_prompt: scene?.video_prompt || scene?.grok_video_prompt || scene?.plan_d_video_prompt_timed || "",
      };
    });
    const tts = pkg?.tts && typeof pkg.tts === "object" ? pkg.tts : {};
    return {
      shot_id: pkg?.shot_id || "",
      updated_at: pkg?.updated_at || 0,
      prompt_package_version: pkg?.prompt_package_version || "final_v1",
      references: pkg?.references || {},
      tts_prompt: pkg?.tts_prompt || tts.execution_prompt || tts.prompt || "",
      tts_speed_notes: Array.isArray(pkg?.tts_speed_notes) ? pkg.tts_speed_notes : Array.isArray(tts.speed_notes) ? tts.speed_notes : [],
      scenes,
    };
  }
  function compilePromptPackage(pkg) {
    return normalizePromptPackage(pkg);
  }
  function updateShotPromptPackageTTS(patch) {
    updateShotPromptPackageDraft((prev) => ({ package: { ...(prev.package || {}), ...patch } }));
  }
  function updateShotPromptPackageScene(sceneId, patch) {
    updateShotPromptPackageDraft((prev) => {
      const scenes = (prev.package?.scenes || []).map((scene) => String(scene?.scene_mark_id || "") === String(sceneId || "") ? { ...scene, ...patch } : scene);
      return { package: { ...(prev.package || {}), scenes } };
    });
  }
  function actualVideoModelPrompt(scene = selectedPromptPackageScene()) {
    return String(scene?.video_prompt || "").trim();
  }
  function actualImageModelPrompt(scene = selectedPromptPackageScene()) {
    return String(scene?.image_prompt || "").trim();
  }
  function updateActualImageModelPrompt(value) {
    const scene = selectedPromptPackageScene();
    updateShotPromptPackageScene(scene?.scene_mark_id, { image_prompt: value });
  }
  function updateActualVideoModelPrompt(value) {
    const scene = selectedPromptPackageScene();
    updateShotPromptPackageScene(scene?.scene_mark_id, { video_prompt: value });
  }
  function imagePromptBuildMessages() {
    const build = shotPromptPackageDialog()?.imagePromptBuild;
    if (!build) return [];
    if (build.status === "blocked") {
      return (build.blocking_errors || []).map((item) => String(item || "").trim()).filter(Boolean);
    }
    if (build.status === "failed") {
      return [String(build.error || "Plan D image prompt build failed.").trim()].filter(Boolean);
    }
    return [];
  }
  async function openShotPromptPackageDialog(shot = selectedShot()) {
    if (!shot?.shot_id || !selectedTaskId()) return;
    setShotPromptPackageTab("image");
    setShotPromptPackageDialog({ shot, package: { shot_id: shot.shot_id, tts_prompt: "", tts_speed_notes: [], scenes: [] }, sceneId: "", loading: true, error: "", imagePromptBuild: null });
    try {
      const res = await api.getShotFinalPrompts(selectedTaskId(), shot.shot_id);
      const pkg = normalizePromptPackage(res.package || {});
      setShotPromptPackageDialog({ shot, path: res.path || "", package: { ...pkg, scenes: pkg.scenes || [] }, sceneId: pkg.scenes?.[0]?.scene_mark_id || "", loading: false, error: "", imagePromptBuild: res.image_prompt_build || null });
    } catch (err) {
      setShotPromptPackageDialog((prev) => prev ? { ...prev, loading: false, error: err instanceof Error ? err.message : String(err) } : prev);
    }
  }
  async function saveShotPromptPackageDialog() {
    const dialog = shotPromptPackageDialog();
    if (!dialog?.shot?.shot_id || !selectedTaskId()) return;
    setShotPromptPackageDialog((prev) => prev ? { ...prev, saving: true, error: "" } : prev);
    try {
      const res = await api.saveShotFinalPrompts(selectedTaskId(), dialog.shot.shot_id, { package: compilePromptPackage(dialog.package) });
      setShotPromptPackageDialog((prev) => prev ? { ...prev, package: normalizePromptPackage(res.package || prev.package), path: res.path || prev.path, saving: false, error: "" } : prev);
      await loadShotPlan();
    } catch (err) {
      setShotPromptPackageDialog((prev) => prev ? { ...prev, saving: false, error: err instanceof Error ? err.message : String(err) } : prev);
    }
  }
  async function loadAssetTasks(options = {}) { const sessionId = task()?.session_id; if (!sessionId) return; if (!options.force && assetTasksLoadedSessionId() === sessionId) return; const res = await fetch(`${api.rawFileUrl(sessionId, "asset_tasks.json")}?v=${Date.now()}`, { credentials: "include" }); setAssetTasks(res.ok ? await res.json() : null); setAssetTasksLoadedSessionId(sessionId); if (options.force) setMissingAssetImages({}); }
  async function loadShotAssetPrompts(shotId) { const sessionId = task()?.session_id; if (!sessionId || !shotId || assetPromptPackages()[shotId]) return; const res = await fetch(`${api.rawFileUrl(sessionId, `asset_prompts_${shotId}.json`)}?v=${Date.now()}`, { credentials: "include" }); const payload = res.ok ? await res.json() : null; setAssetPromptPackages((prev) => ({ ...prev, [shotId]: payload })); }
  async function loadPlanCScenePlan(shotId) { const sessionId = task()?.session_id; if (!sessionId || !shotId || Object.prototype.hasOwnProperty.call(planCScenePlans(), shotId)) return; const res = await fetch(`${api.rawFileUrl(sessionId, `plan_c/shots/${shotId}/scene_r2v/scene_plan.json`)}?v=${Date.now()}`, { credentials: "include" }); const payload = res.ok ? await res.json() : null; setPlanCScenePlans((prev) => ({ ...prev, [shotId]: payload })); }
  function fieldText(value, keys = []) { if (!value) return "-"; if (typeof value === "string") return value; if (typeof value === "object") { for (const key of keys) { if (value[key]) return String(value[key]); } return Object.values(value).filter((item) => typeof item === "string" && item.trim()).join(" / ") || "-"; } return String(value); }
  function resourceSessionId(scope = "rebuild") { return scope === "analysis" ? (shotPlan()?.task?.analysis_session_id || detail()?.analysis_task?.session_id || task()?.analysis_task?.session_id || task()?.session_id) : task()?.session_id; }
  function shotAssetSessionId() { return resourceSessionId("analysis"); }
  function normalizeShotAssetPath(path) {
    const value = String(path || "").trim();
    if (!value) return "";
    const sessionId = shotAssetSessionId();
    const marker = sessionId ? `/.opencrew/sessions/${sessionId}/workspace/` : "/.opencrew/sessions/";
    const index = value.indexOf(marker);
    if (index >= 0) return value.slice(index + marker.length);
    return value;
  }
  function normalizeRebuildAssetPath(path) {
    const value = String(path || "").trim();
    if (!value) return "";
    const sessionId = resourceSessionId("rebuild");
    const marker = sessionId ? `/.opencrew/sessions/${sessionId}/workspace/` : "/.opencrew/sessions/";
    const index = value.indexOf(marker);
    if (index >= 0) return value.slice(index + marker.length);
    return value;
  }
  function resourceUrl(path, scope = "rebuild") { const sessionId = resourceSessionId(scope); const normalized = scope === "analysis" ? normalizeShotAssetPath(path) : String(path || "").trim(); return sessionId && normalized ? api.rawFileUrl(sessionId, normalized) : ""; }
  function shotAssetUrl(path) { return resourceUrl(path, "analysis"); }
  function rebuildAssetUrl(path) { return resourceUrl(path, "rebuild"); }
  function rebuildAssetUrlFromPath(path) { return rebuildAssetUrl(normalizeRebuildAssetPath(path)); }
  function openCrewSessionFileUrl(path) {
    const value = String(path || "").trim();
    const match = value.match(/[/\\]\.opencrew[/\\]sessions[/\\](\d+)[/\\]workspace[/\\](.+)$/);
    if (!match) return "";
    return api.rawFileUrl(match[1], match[2].replace(/\\/g, "/"));
  }
  function referenceFrameUrl(path) {
    const value = String(path || "").trim();
    if (!value) return "";
    if (value.startsWith("uploads/storyboard_references/") || value.startsWith("uploads/storyboard/") || value.startsWith("Assets/") || value.startsWith("assets/")) return rebuildAssetUrl(value);
    return shotAssetUrl(value);
  }
  function planALockedTTSRel(shotId) { return shotId ? `Assets/variant_001/${shotId}/tts/locked.wav` : ""; }
  async function checkLockedTTSFile(shot) {
    const shotId = String(shot?.shot_id || "").trim();
    if (!shotId || Object.prototype.hasOwnProperty.call(lockedTTSFiles(), shotId)) return;
    const rel = planALockedTTSRel(shotId);
    try {
      const res = await fetch(`${rebuildAssetUrl(rel)}?v=${Date.now()}`, { credentials: "include" });
      setLockedTTSFiles((prev) => ({ ...prev, [shotId]: res.ok ? rel : "" }));
    } catch {
      setLockedTTSFiles((prev) => ({ ...prev, [shotId]: "" }));
    }
  }
  function plainSrtText(value) { return String(value || "").split(/\r?\n/).map((line) => line.trim()).filter((line) => line && !/^\d+$/.test(line) && !line.includes("-->")).join(" "); }
  function positiveNumber(value) {
    const number = Number(value);
    return Number.isFinite(number) && number > 0 ? number : null;
  }
  function tempoFromSelection(selection) {
    if (!selection || typeof selection !== "object") return null;
    return positiveNumber(selection.tempo ?? selection.speed_factor ?? selection.speed ?? selection.playback_rate ?? selection.rate);
  }
  function ttsCardTempo(card) {
    return tempoFromSelection(card?.recommendation) || positiveNumber(card?.tempo);
  }
  function srtOriginalForMark(mark) {
    return String(mark?.original_srt_text || mark?.source_srt_text || mark?.srt_text || "").trim();
  }
  async function loadSourceSrtText(shot) {
    const shotId = String(shot?.shot_id || "");
    const path = String(shot?.reference?.subtitle_path || "").trim();
    if (!shotId || !path || shot?.reference?.srt_text || sourceSrtTexts()[shotId]) return;
    try {
      const res = await fetch(`${resourceUrl(path, shot?.reference?.resource_session || "analysis")}?v=${Date.now()}`, { credentials: "include" });
      if (!res.ok) return;
      const text = plainSrtText(await res.text());
      if (text) setSourceSrtTexts((prev) => ({ ...prev, [shotId]: text }));
    } catch {
      // Source session may be unavailable; generated Rebuild content should still render.
    }
  }
  function shotReferenceVideoBounds(shot) { const ref = shot?.reference || {}; const start = Number(ref.start ?? shot?.start ?? 0); const end = Number(ref.end ?? shot?.end ?? 0); return { start: Number.isFinite(start) ? start : 0, end: Number.isFinite(end) ? end : 0 }; }
  function shotReferenceVideoSource(shot) { const ref = shot?.reference || {}; const isVirtual = String(ref.clip_status || "").toLowerCase() === "virtual"; const path = isVirtual ? (ref.source_video_path || ref.clip_path) : (ref.clip_path || ref.source_video_path); const url = shotAssetUrl(path); if (!url || !isVirtual) return url; const { start, end } = shotReferenceVideoBounds(shot); return end > start ? `${url}#t=${start},${end}` : url; }
  function syncShotReferenceVideoTime(video, shot) { const ref = shot?.reference || {}; if (String(ref.clip_status || "").toLowerCase() !== "virtual") return; const { start, end } = shotReferenceVideoBounds(shot); if (start > 0 && Math.abs(video.currentTime - start) > 0.25 && video.currentTime < start) video.currentTime = start; if (end > start && video.currentTime > end) { video.pause(); video.currentTime = start; } }
  function cloneKeyframes(frames) { return (frames || []).filter((frame) => frame && typeof frame === "object").map((frame) => ({ ...frame })); }
  function sceneGenerationMode(value) { return String(value?.generation_mode || value?.asset_generation_mode || "first_frame") === "first_last" ? "first_last" : "first_frame"; }
  function shotGenerationMode(shot) {
    const modes = editableSceneMarks(shot).map((mark) => sceneGenerationMode(mark));
    return modes.includes("first_last") ? "first_last" : "first_frame";
  }
  function withSceneGenerationMode(mark, generationMode = null) {
    const mode = generationMode || sceneGenerationMode(mark);
    const planA = mark?.plan_a && typeof mark.plan_a === "object" ? { ...mark.plan_a } : {};
    const sceneAsset = planA.scene_asset && typeof planA.scene_asset === "object" ? { ...planA.scene_asset } : {};
    sceneAsset.uses_only_first_frame = mode !== "first_last";
    planA.scene_asset = sceneAsset;
    return { ...mark, generation_mode: mode, plan_a: planA };
  }
  function cloneSceneMarks(marks) { return (marks || []).filter((mark) => mark && typeof mark === "object").map((mark) => withSceneGenerationMode({ ...mark, keyframes: { ...(mark.keyframes || {}) }, scene_description: mark.scene_description ? { ...mark.scene_description, model_notes: { ...(mark.scene_description.model_notes || {}) } } : undefined, manual_edit: mark.manual_edit ? { ...mark.manual_edit } : undefined, plan_a: mark.plan_a ? { ...mark.plan_a, scene_asset: mark.plan_a.scene_asset ? { ...mark.plan_a.scene_asset } : mark.plan_a.scene_asset } : undefined })); }
  function keyframeTime(frame) { return Number(frame?.time ?? 0); }
  function shotKeyframes(shot) {
    const frames = cloneKeyframes(shot?.reference?.keyframes || []);
    const byPath = new Map();
    for (const frame of frames) {
      const path = String(frame.path || "").trim();
      if (path && !byPath.has(path)) byPath.set(path, frame);
    }
    return Array.from(byPath.values()).sort((a, b) => (Number(a.time ?? 1000000) - Number(b.time ?? 1000000)) || String(a.path || "").localeCompare(String(b.path || "")));
  }
  function editableKeyframes(shot) { const shotId = shot?.shot_id; return shotId && keyframeEdits()[shotId] ? keyframeEdits()[shotId].keyframes : shotKeyframes(shot); }
  function editableSceneMarks(shot) { const shotId = shot?.shot_id; return shotId && keyframeEdits()[shotId]?.scene_marks ? keyframeEdits()[shotId].scene_marks : cloneSceneMarks(shot?.reference?.scene_marks || []); }
  function keyframeEditState(shot) { const shotId = shot?.shot_id; return shotId ? keyframeEdits()[shotId] : null; }
  function sceneEditState(prev, shot) { return prev[shot.shot_id] || { original: shotKeyframes(shot), keyframes: shotKeyframes(shot), scene_marks: editableSceneMarks(shot), deleted: [] }; }
  function nextDraftSceneId(shotId, frames, sceneMarks) {
    const ids = new Set([...(sceneMarks || []).map((mark) => String(mark.scene_mark_id || "")), ...(frames || []).map((frame) => String(frame.scene_mark?.scene_mark_id || ""))]);
    let index = 1;
    while (ids.has(`${shotId}_scene_draft_${String(index).padStart(3, "0")}`)) index += 1;
    return `${shotId}_scene_draft_${String(index).padStart(3, "0")}`;
  }
  function normalizeSceneMarkState(shotId, frames, sceneMarks, keepIncomplete = true) {
    const previousById = new Map((sceneMarks || []).map((mark) => [String(mark.scene_mark_id || ""), mark]));
    const groups = new Map();
    for (const frame of frames) {
      const mark = frame.scene_mark;
      if (!mark?.scene_mark_id || !["single", "first", "last"].includes(String(mark.role || ""))) continue;
      const id = String(mark.scene_mark_id);
      const group = groups.get(id) || { scene_mark_id: id, single: null, first: null, last: null };
      if (mark.role === "single") group.single = frame;
      if (mark.role === "first" && (!group.first || keyframeTime(frame) < keyframeTime(group.first))) group.first = frame;
      if (mark.role === "last" && (!group.last || keyframeTime(frame) > keyframeTime(group.last))) group.last = frame;
      groups.set(id, group);
    }
    const complete = Array.from(groups.values()).flatMap((group) => {
      if (group.single) return [{ ...group, mode: "single", startFrame: group.single, endFrame: group.single }];
      if (group.first && group.last && group.first.path !== group.last.path && keyframeTime(group.first) < keyframeTime(group.last)) return [{ ...group, mode: "first_last", startFrame: group.first, endFrame: group.last }];
      return [];
    }).sort((a, b) => keyframeTime(a.startFrame) - keyframeTime(b.startFrame));
    const canonicalIdByDraftId = new Map(complete.map((group, index) => [group.scene_mark_id, `${shotId}_scene_${String(index + 1).padStart(3, "0")}`]));
    const completeIds = new Set(canonicalIdByDraftId.keys());
    const normalizedMarks = complete.map((group, index) => {
      const previous = previousById.get(group.scene_mark_id) || {};
      const canonicalId = canonicalIdByDraftId.get(group.scene_mark_id);
      const start = keyframeTime(group.startFrame);
      const end = keyframeTime(group.endFrame);
      const isSingle = group.mode === "single";
      return withSceneGenerationMode({
        ...previous,
        scene_mark_id: canonicalId,
        shot_id: shotId,
        scene_index: index + 1,
        mode: isSingle ? "single" : "first_last",
        generation_mode: sceneGenerationMode(previous),
        start: Number(start.toFixed(3)),
        end: Number(end.toFixed(3)),
        duration: Math.max(0, Number((end - start).toFixed(3))),
        boundary_source: "manual",
        keyframes: {
          ...(previous.keyframes || {}),
          single: isSingle ? group.single.path : "",
          first: isSingle ? group.single.path : group.first.path,
          last: isSingle ? group.single.path : group.last.path,
          paths: isSingle ? [group.single.path] : [group.first.path, group.last.path],
        },
        scene_description: previous.scene_description || {},
        manual_edit: { type: "manual_scene_boundary", updated_at: Date.now() },
      });
    });
    const indexById = new Map(normalizedMarks.map((mark) => [mark.scene_mark_id, mark.scene_index]));
    const normalizedFrames = frames.map((frame) => {
      const nextFrame = { ...frame, scene_mark: frame.scene_mark ? { ...frame.scene_mark } : undefined };
      const markId = nextFrame.scene_mark?.scene_mark_id;
      if (!markId) return nextFrame;
      const canonicalId = canonicalIdByDraftId.get(markId);
      if (completeIds.has(markId) && canonicalId) nextFrame.scene_mark = { scene_mark_id: canonicalId, scene_index: indexById.get(canonicalId), role: nextFrame.scene_mark.role, click_behavior: "show_scene_description" };
      else if (!keepIncomplete) delete nextFrame.scene_mark;
      return nextFrame;
    });
    return { frames: normalizedFrames, sceneMarks: normalizedMarks };
  }
  function sceneMarkForFrame(shot, frame) {
    const markId = frame?.scene_mark?.scene_mark_id;
    if (!markId) return null;
    return editableSceneMarks(shot).find((item) => item?.scene_mark_id === markId) || null;
  }
  function canOpenSceneMark(frame) { return frame?.scene_mark?.click_behavior === "show_scene_description" || ["single", "first", "last"].includes(String(frame?.scene_mark?.role || "")); }
  function openSceneMark(shot, frame) {
    const mark = sceneMarkForFrame(shot, frame);
    if (!mark || !canOpenSceneMark(frame)) return false;
    setSceneSrtDraft(String(mark.srt_text || ""));
    setSceneMarkDialog({ shot, frame, mark });
    return true;
  }
  function openKeyframeContextMenu(event, shot, frame, index) {
    event.preventDefault();
    event.stopPropagation();
    setKeyframeContextMenu({ x: event.clientX, y: event.clientY, shot, frame, index });
  }
  function openKeyframePrimaryAction(event, shot, frame, index) {
    if (openSceneMark(shot, frame)) {
      event.preventDefault();
      event.stopPropagation();
      return;
    }
    openKeyframeContextMenu(event, shot, frame, index);
  }
  function openKeyframeContextMenuOnRightMouseDown(event, shot, frame, index) {
    if (event.button !== 2) return;
    openKeyframeContextMenu(event, shot, frame, index);
  }
  function attachKeyframeContextMenu(el, shot, frame, index) {
    const open = (event) => openKeyframeContextMenu(event, shot, frame, index);
    el.addEventListener("contextmenu", open, true);
  }
  function setShotMode(shotId, mode) {
    setShotPreviewMode((prev) => ({ ...prev, [shotId]: mode }));
    if (mode === "asset") { void loadAssetTasks(); void loadShotAssetPrompts(shotId); void loadPlanCScenePlan(shotId); }
  }
  function setAllShotMode(mode) {
    const shots = shotPlanShots();
    setGlobalShotPreviewMode(mode);
    setShotPreviewMode(Object.fromEntries(shots.map((shot) => [shot.shot_id, mode])));
    if (mode === "asset") {
      void loadAssetTasks();
      shots.forEach((shot) => {
        void loadShotAssetPrompts(shot.shot_id);
        void loadPlanCScenePlan(shot.shot_id);
      });
    }
  }
  function selectedShotAssetPackage() { const shotId = selectedShot()?.shot_id; return shotId ? assetPromptPackages()[shotId] : null; }
  function generatedAssetPackagesForShot(shotId) {
    const fromTasks = (assetTasks()?.shots || []).map((item) => item?.prompt_package).filter((pkg) => pkg?.shot_id === shotId);
    const sidecar = assetPromptPackages()[shotId];
    const fromSidecar = Array.isArray(sidecar?.scenes) ? sidecar.scenes.filter((pkg) => pkg?.shot_id === shotId) : sidecar?.shot_id === shotId ? [sidecar] : [];
    const packages = [...fromSidecar, ...fromTasks];
    const byScene = new Map();
    for (const pkg of packages) {
      const key = String(pkg?.scene_mark_id || "");
      if (pkg && !byScene.has(key)) byScene.set(key, pkg);
    }
    return Array.from(byScene.values());
  }
  function emptyAssetPackageForScene(shot, mark) {
    const keyframes = mark?.keyframes || {};
    return {
      shot_id: shot?.shot_id || "",
      scene_mark_id: mark?.scene_mark_id || "",
      mode: mark?.mode || (keyframes.single ? "single" : "first_last"),
      srt_text: mark?.srt_text || shot?.reference?.srt_text || "",
      reference: { single_frame: keyframes.single || "", first_frame: keyframes.first || keyframes.single || "", last_frame: keyframes.last || "", reference_frame_prompt: "", first_reference_prompt: "", last_reference_prompt: "" },
      image_prompts: {},
      video_prompts: {},
      generation_intent: {},
      validation: { status: "empty", warnings: ["04 asset prompts have not been generated for this scene yet."] },
    };
  }
  function assetPackageForCurrentScene(shot, mark, generatedPkg) {
    const currentPkg = generatedPkg || emptyAssetPackageForScene(shot, mark);
    const keyframes = mark?.keyframes || {};
    return {
      ...currentPkg,
      shot_id: shot?.shot_id || currentPkg?.shot_id || "",
      scene_mark_id: mark?.scene_mark_id || currentPkg?.scene_mark_id || "",
      mode: mark?.mode || (keyframes.single ? "single" : "first_last"),
      generation_mode: sceneGenerationMode(mark || currentPkg),
      plan_a: mark?.plan_a || currentPkg?.plan_a,
      srt_text: mark?.srt_text || currentPkg?.srt_text || shot?.reference?.srt_text || "",
      reference: {
        ...(currentPkg?.reference || {}),
        single_frame: keyframes.single || "",
        first_frame: keyframes.first || keyframes.single || "",
        last_frame: keyframes.last || "",
      },
    };
  }
  function selectedShotAssetPackages() {
    const shot = selectedShot();
    if (!shot) return [];
    const generated = generatedAssetPackagesForShot(shot.shot_id);
    const generatedByScene = new Map(generated.map((pkg) => [String(pkg?.scene_mark_id || ""), pkg]));
    const marks = editableSceneMarks(shot);
    if (marks.length) return marks.map((mark) => assetPackageForCurrentScene(shot, mark, generatedByScene.get(String(mark.scene_mark_id || ""))));
    if (generated.length) return generated;
    return [emptyAssetPackageForScene(shot, { scene_mark_id: "", keyframes: {} })];
  }
  function assetTasksForPackage(pkg) {
    const shotId = pkg?.shot_id;
    const sceneMarkId = pkg?.scene_mark_id || "";
    return (assetTasks()?.tasks || []).filter((item) => item?.shot_id === shotId && String(item?.scene_mark_id || "") === sceneMarkId);
  }
  function assetTaskByType(pkg, types) {
    const matches = assetTasksForPackage(pkg).filter((item) => types.includes(String(item?.type || "")));
    return matches.find((item) => String(item?.status || "") === "completed") || matches.find((item) => item?.generated_at || item?.provider || item?.model) || matches[0] || null;
  }
  function shotMultiReferenceWorkflowId(shot) { return normalizeWorkflowId(`${shot?.shot_id || "shot"}_multi_r2v_video`); }
  function shotMultiReferenceModels() {
    const allowed = new Set([
      "wan/wan2.7-r2v",
      "wan/happyhorse-1.0-r2v",
      "xai/grok-imagine-video-1.5-preview",
      "xai/grok-imagine-video",
      "gemini/veo-3.1-generate-preview",
      "gemini/veo-3.1-fast-generate-preview",
    ]);
    const providers = videoModelConfig()?.providers || [];
    return providers.flatMap((provider) => (provider.models || []).map((model) => ({ provider: provider.provider, providerLabel: provider.provider_label || provider.provider, model: model.model, label: `${provider.provider_label || provider.provider} / ${model.label || model.model}`, duration: model.duration || {}, key: `${provider.provider}/${model.model}` }))).filter((item) => allowed.has(item.key));
  }
  function defaultShotMultiReferenceModel() { return shotMultiReferenceModels().find((item) => item.key === "xai/grok-imagine-video-1.5-preview") || shotMultiReferenceModels().find((item) => item.key === "xai/grok-imagine-video") || shotMultiReferenceModels()[0] || null; }
  function shotMultiReferenceDuration(shot, model = null) {
    const requested = Number(shot?.duration || 0) || 4;
    const provider = String(model?.provider || "");
    const modelId = String(model?.model || "");
    if (provider === "gemini") return 8;
    const rounded = Math.ceil(requested);
    if (provider === "wan") return Math.max(3, Math.min(rounded, modelId.includes("happyhorse") ? 15 : 30));
    if (provider === "xai") return Math.max(1, Math.min(rounded, 15));
    return rounded;
  }
  function shotMultiReferenceImages(shot, lockedTimeline = null) {
    const lockedMap = Object.fromEntries(((lockedTimeline?.scenes || [])).map((scene) => [scene.scene_mark_id, scene]));
    return selectedShotAssetPackages().map((pkg) => {
      const firstImageTask = assetTaskByType(pkg, ["image_regenerate_first", "image_regenerate_single"]);
      const shotId = pkg?.shot_id || shot?.shot_id || "";
      const sceneId = pkg?.scene_mark_id || "";
      const generated = firstImageTask?.output || "";
      const generatedVersion = firstImageTask?.generated_at || firstImageTask?.updated_at || firstImageTask?.provider || firstImageTask?.model || "";
      const reference = pkg?.reference || {};
      const fallback = reference.first_frame || reference.single_frame || "";
      const locked = lockedMap[pkg?.scene_mark_id || ""] || {};
      return { scene_mark_id: pkg?.scene_mark_id || "", srt_text: pkg?.srt_text || "", image: generated || fallback, imageVersion: generated ? generatedVersion : "", source: generated ? "generated_first" : "reference_first", start: locked.start ?? null, end: locked.end ?? null, duration: locked.duration ?? null };
    }).filter((item) => item.image);
  }
  function shotMultiReferenceBasePrompt(shot, scenePlan) {
    const lines = scenePlan.map((item, index) => {
      const timing = item.start !== null && item.end !== null ? ` ${Number(item.start).toFixed(2)}-${Number(item.end).toFixed(2)}s` : "";
      return `图${index + 1}${timing} ${item.scene_mark_id || "scene"}: ${item.srt_text || "无对白"}`;
    }).join("\n");
    const hasLockedTiming = scenePlan.some((item) => item.start !== null && item.end !== null);
    return `生成一个完整 shot 级 9:16 竖屏视频，按参考图片顺序推进，不是分别生成 scene。\nShot: ${shot?.shot_id || ""}\n${hasLockedTiming ? "使用已锁定的 TTS 时间轴作为视觉节奏基准。" : `原始时长: ${shot?.duration || "未知"} 秒`}\n\n${lines}\n\n视觉跟随对白节奏；精确对白由后期 TTS/SRT 保证。每张参考图都要出现，最后一张参考图保留到结尾。`;
  }
  function hydrateShotMultiReferenceWorkflow(stored, shot, base) {
    const model = shotMultiReferenceModels().find((item) => item.provider === stored?.provider && item.model === stored?.model) || base.model;
    const candidates = (stored?.candidates || []).map((item) => ({ candidateId: item.candidate_id || item.candidateId, provider: item.provider, model: item.model, output: item.output, outputPath: item.output_path, src: rebuildAssetUrl(item.output), elapsedSeconds: item.elapsed_seconds, duration: item.duration, status: item.status || "completed", error: item.detail || "" }));
    const hasLockedTiming = (base.scenePlan || []).some((item) => item.start !== null && item.end !== null);
    const storedPrompt = String(stored?.prompt || "");
    const currentPrompt = hasLockedTiming && !storedPrompt.includes("锁定的 TTS 时间轴") ? base.currentPrompt : storedPrompt || base.currentPrompt;
    return { workflowId: stored.workflow_id || base.workflowId, shot, model, provider: stored?.provider || model?.provider || "", modelId: stored?.model || model?.model || "", duration: hasLockedTiming ? base.duration : stored?.duration || base.duration, variantCount: stored?.variant_count || 1, simplePrompt: stored?.simple_prompt || "", currentPrompt, referenceImages: base.referenceImages, scenePlan: base.scenePlan, candidates, final: stored?.final || null, phase: stored?.phase || "edit", refining: false, error: "" };
  }
  function defaultShotTTSVideoState(shot, lockedTimeline = null, stored = null) {
    const scenePlan = shotMultiReferenceImages(shot, lockedTimeline);
    const model = shotMultiReferenceModels().find((item) => item.provider === stored?.provider && item.model === stored?.model) || defaultShotMultiReferenceModel();
    const duration = lockedTimeline?.duration || stored?.duration || shotMultiReferenceDuration(shot, model);
    return {
      workflowId: shotMultiReferenceWorkflowId(shot),
      model,
      provider: stored?.provider || model?.provider || "",
      modelId: stored?.model || model?.model || "",
      duration,
      variantCount: stored?.variant_count || 1,
      simplePrompt: stored?.simple_prompt || "",
      currentPrompt: stored?.prompt || shotMultiReferenceBasePrompt(shot, scenePlan),
      referenceImages: scenePlan.map((item) => item.image),
      scenePlan,
      candidates: (stored?.candidates || []).map((item) => ({ candidateId: item.candidate_id || item.candidateId, provider: item.provider, model: item.model, output: item.output, outputPath: item.output_path, src: rebuildAssetUrl(item.output), elapsedSeconds: item.elapsed_seconds, duration: item.duration, status: item.status || "completed", error: item.detail || "" })),
      final: stored?.final || null,
      phase: stored?.phase || "edit",
      refining: false,
      finalizing: false,
      error: "",
    };
  }
  function withAssetVersion(url, assetTask) {
    const version = assetTask?.generated_at || assetTask?.updated_at || assetTask?.provider || assetTask?.model || "";
    if (!url || !version) return url || "";
    return `${url}${url.includes("?") ? "&" : "?"}v=${encodeURIComponent(String(version))}`;
  }
  function withUrlVersion(url, version) { return url && version ? `${url}${url.includes("?") ? "&" : "?"}v=${encodeURIComponent(String(version))}` : url || ""; }
  function generatedAssetImageUrl(assetTask) { return assetTask?.output && (assetTask?.status === "completed" || assetTask?.generated_at || assetTask?.provider || assetTask?.model) ? withAssetVersion(rebuildAssetUrl(assetTask.output), assetTask) : ""; }
  function shotPlanFinalVideoOutputs(shot) {
    if (!shot?.shot_id) return [];
    return [
      { key: "plan_d", label: "Plan D / TTS Lip Sync", output: `Assets/variant_001/${shot.shot_id}/plan_d.mp4`, updatedAt: Number(shot?.plan_d?.updated_at || 0) },
      { key: "plan_a", label: "Plan A / Image Sequence", output: `Assets/variant_001/${shot.shot_id}/plan_a.mp4` },
      { key: "plan_c", label: "Plan C / Shot R2V", output: `Assets/variant_001/${shot.shot_id}/plan_c.mp4` },
    ].sort((a, b) => {
      const knownOrder = { plan_d: 3, plan_c: 2, plan_a: 1 };
      const timeDiff = Number(b.updatedAt || 0) - Number(a.updatedAt || 0);
      return timeDiff || (knownOrder[b.key] || 0) - (knownOrder[a.key] || 0);
    });
  }
  function latestShotFinalVideo(shot) {
    if (isStoryboardReferencePhase2()) return null;
    return shotPlanFinalVideoOutputs(shot).find((item) => item.output && !missingShotAssetVideos()[item.output]) || null;
  }
  function shotPlanCFinalOutput(shot) { return latestShotFinalVideo(shot)?.output || ""; }
  function openShotAssetViewer(shot) {
    const video = latestShotFinalVideo(shot);
    if (shot && video?.output) setShotAssetViewer({ shot, output: video.output, label: video.label, src: rebuildAssetUrl(video.output) });
  }
  function latestShotFinalVideoForPackage(pkg) {
    const shot = shotPlanShots().find((item) => item?.shot_id === pkg?.shot_id) || selectedShot();
    const video = latestShotFinalVideo(shot);
    return video?.output ? { src: rebuildAssetUrl(video.output), output: video.output, label: video.label, key: video.key } : null;
  }
  function planCVideoForPackage(pkg) {
    const plan = planCScenePlans()[pkg?.shot_id];
    const sceneId = String(pkg?.scene_mark_id || "");
    if (!plan || !sceneId) return null;
    const batches = Array.isArray(plan?.batches) ? plan.batches : [];
    for (const batch of batches) {
      const ids = (batch?.scene_mark_ids || []).map((item) => String(item || "")).filter(Boolean);
      if (!ids.includes(sceneId)) continue;
      if (ids[0] !== sceneId) return { hidden: true, batch };
      const output = String(batch?.output_video || "").trim();
      return output && String(batch?.status || "") === "completed" ? { src: rebuildAssetUrl(output), output, batch } : { batch };
    }
    return null;
  }
  function fallbackAssetImageUrl(pkg, role, assetTask) {
    const sceneAsset = pkg?.scene_asset || pkg?.plan_a?.scene_asset || {};
    const selectedImage = role === "last" ? (sceneAsset.selected_last_image || sceneAsset.last_image) : sceneAsset.selected_image;
    if (selectedImage) return withUrlVersion(rebuildAssetUrl(selectedImage), sceneAsset.generated_at || "");
    return assetTask?.output ? withAssetVersion(rebuildAssetUrl(assetTask.output), assetTask) : "";
  }
  async function saveSceneGenerationMode(pkg, generationMode) {
    const shot = shotPlanShots().find((item) => item?.shot_id === pkg?.shot_id) || selectedShot();
    if (!task()?.id || !shot?.shot_id || !pkg?.scene_mark_id) return;
    const sceneMarks = editableSceneMarks(shot).map((mark) => String(mark?.scene_mark_id || "") === String(pkg.scene_mark_id || "") ? withSceneGenerationMode(mark, generationMode) : withSceneGenerationMode(mark));
    const frames = editableKeyframes(shot);
    const res = await api.saveShotSceneMarks(task().id, { shot_id: shot.shot_id, keyframes: frames, scene_marks: sceneMarks });
    setShotPlan((prev) => ({
      ...prev,
      shots: (prev?.shots || []).map((item) => item?.shot_id === shot.shot_id ? { ...item, reference: { ...(item.reference || {}), keyframes: res.keyframes || [], original_keyframes: res.original_keyframes || item.reference?.original_keyframes || [], deleted_keyframes: res.deleted_keyframes || [], scene_marks: res.scene_marks || [], scene_mark_summary: res.scene_mark_summary || item.reference?.scene_mark_summary } } : item),
    }));
    setAssetPromptPackages((prev) => {
      const sidecar = prev[shot.shot_id];
      if (!sidecar) return prev;
      const updatePkg = (item) => String(item?.scene_mark_id || "") === String(pkg.scene_mark_id || "") ? { ...item, generation_mode: generationMode, mode: generationMode === "first_last" ? "first_last" : "single" } : item;
      const nextSidecar = Array.isArray(sidecar?.scenes) ? { ...sidecar, scenes: sidecar.scenes.map(updatePkg) } : updatePkg(sidecar);
      return { ...prev, [shot.shot_id]: nextSidecar };
    });
  }
  async function saveShotGenerationMode(shot, generationMode) {
    if (!task()?.id || !shot?.shot_id) return;
    const sceneMarks = editableSceneMarks(shot).map((mark) => withSceneGenerationMode(mark, generationMode));
    const frames = editableKeyframes(shot);
    const res = await api.saveShotSceneMarks(task().id, { shot_id: shot.shot_id, keyframes: frames, scene_marks: sceneMarks });
    setShotPlan((prev) => ({
      ...prev,
      shots: (prev?.shots || []).map((item) => item?.shot_id === shot.shot_id ? { ...item, reference: { ...(item.reference || {}), keyframes: res.keyframes || [], original_keyframes: res.original_keyframes || item.reference?.original_keyframes || [], deleted_keyframes: res.deleted_keyframes || [], scene_marks: res.scene_marks || [], scene_mark_summary: res.scene_mark_summary || item.reference?.scene_mark_summary } } : item),
    }));
    setAssetPromptPackages((prev) => {
      const sidecar = prev[shot.shot_id];
      if (!sidecar) return prev;
      const updatePkg = (item) => ({ ...item, generation_mode: generationMode, mode: generationMode === "first_last" ? "first_last" : "single" });
      const nextSidecar = Array.isArray(sidecar?.scenes) ? { ...sidecar, scenes: sidecar.scenes.map(updatePkg) } : updatePkg(sidecar);
      return { ...prev, [shot.shot_id]: nextSidecar };
    });
  }
  async function saveSceneSrtText() {
    const dialog = sceneMarkDialog();
    const shot = dialog?.shot;
    const markId = String(dialog?.mark?.scene_mark_id || "");
    if (!task()?.id || !shot?.shot_id || !markId) return;
    const nextSrt = sceneSrtDraft();
    const sceneMarks = editableSceneMarks(shot).map((mark) => String(mark?.scene_mark_id || "") === markId ? { ...mark, original_srt_text: srtOriginalForMark(mark), srt_text: nextSrt } : mark);
    const frames = editableKeyframes(shot);
    const res = await api.saveShotSceneMarks(task().id, { shot_id: shot.shot_id, keyframes: frames, scene_marks: sceneMarks });
    const nextMark = (res.scene_marks || []).find((mark) => String(mark?.scene_mark_id || "") === markId) || { ...dialog.mark, srt_text: nextSrt };
    setShotPlan((prev) => ({
      ...prev,
      shots: (prev?.shots || []).map((item) => item?.shot_id === shot.shot_id ? { ...item, reference: { ...(item.reference || {}), keyframes: res.keyframes || [], original_keyframes: res.original_keyframes || item.reference?.original_keyframes || [], deleted_keyframes: res.deleted_keyframes || [], scene_marks: res.scene_marks || [], scene_mark_summary: res.scene_mark_summary || item.reference?.scene_mark_summary } } : item),
    }));
    setSceneMarkDialog((prev) => prev ? { ...prev, shot: { ...shot, reference: { ...(shot.reference || {}), keyframes: res.keyframes || [], original_keyframes: res.original_keyframes || shot.reference?.original_keyframes || [], deleted_keyframes: res.deleted_keyframes || [], scene_marks: res.scene_marks || [], scene_mark_summary: res.scene_mark_summary || shot.reference?.scene_mark_summary } }, mark: nextMark } : prev);
    setAssetPromptPackages((prev) => {
      const sidecar = prev[shot.shot_id];
      if (!sidecar) return prev;
      const updatePkg = (item) => String(item?.scene_mark_id || "") === markId ? { ...item, srt_text: nextSrt } : item;
      const nextSidecar = Array.isArray(sidecar?.scenes) ? { ...sidecar, scenes: sidecar.scenes.map(updatePkg) } : updatePkg(sidecar);
      return { ...prev, [shot.shot_id]: nextSidecar };
    });
  }
  function usableAssetImageUrl(src) { return src && !missingAssetImages()[src] ? src : ""; }
  function openAssetImageViewer(src, label) { if (src) setAssetImageViewer({ src, label, zoom: 1 }); }
  function zoomAssetImageViewer(delta) { setAssetImageViewer((prev) => prev ? { ...prev, zoom: Math.max(0.25, Math.min(4, Number((prev.zoom + delta).toFixed(2)))) } : prev); }
  function assetOutputAbsPath(output) {
    const workspace = String(task()?.workspace_dir || "").trim();
    const rel = String(output || "").trim();
    if (!workspace || !rel) return "";
    if (rel.startsWith("/")) return rel;
    return `${workspace.replace(/\/$/, "")}/${rel}`;
  }
  function hasCopyableReferenceAsset(menu) {
    const reference = menu?.pkg?.reference || {};
    if (menu?.role === "last") return Boolean(reference.last_frame || menu?.assetTask?.input?.reference_frame);
    return Boolean(reference.first_frame || reference.single_frame || menu?.assetTask?.input?.reference_frame);
  }
  function hasDeletableAssetImage(menu) {
    const sceneAsset = menu?.pkg?.scene_asset || menu?.pkg?.plan_a?.scene_asset || {};
    const selected = menu?.role === "last" ? (sceneAsset.selected_last_image || sceneAsset.last_image) : sceneAsset.selected_image;
    return Boolean(selected || menu?.assetTask?.output);
  }
  async function copyReferenceAssetImage(menu) {
    if (!menu?.pkg) return;
    setAssetImageContextMenu(null);
    await runAction(`asset-image-copy-${menu.pkg.scene_mark_id || menu.pkg.shot_id}-${menu.role}`, async () => {
      const workflowId = sceneWorkflowId(menu.pkg, menu.role);
      const result = await api.copyReferenceAssetImage(selectedTaskId(), { shot_id: menu.pkg.shot_id, scene_mark_id: menu.pkg.scene_mark_id || "", role: menu.role, api_call_id: `copy_reference_${Date.now()}_${Math.random().toString(36).slice(2, 8)}` });
      emitDebugCompleted(debugContext({ family: "asset_image", workflow_id: workflowId, action: "copy_reference", payload: result }));
      await loadAssetTasks({ force: true });
      await loadShotPlan();
    });
  }
  async function deleteAssetImage(menu) {
    if (!menu?.pkg) return;
    setAssetImageContextMenu(null);
    await runAction(`asset-image-delete-${menu.pkg.scene_mark_id || menu.pkg.shot_id}-${menu.role}`, async () => {
      const workflowId = sceneWorkflowId(menu.pkg, menu.role);
      const result = await api.deleteAssetImage(selectedTaskId(), { shot_id: menu.pkg.shot_id, scene_mark_id: menu.pkg.scene_mark_id || "", role: menu.role, api_call_id: `delete_asset_${Date.now()}_${Math.random().toString(36).slice(2, 8)}` });
      emitDebugCompleted(debugContext({ family: "asset_image", workflow_id: workflowId, action: "delete_image", payload: result }));
      await loadAssetTasks({ force: true });
      await loadShotPlan();
    });
  }
  async function generateAssetImage(menu, useReferenceImage) {
    if (!menu?.pkg) return;
    const apiCallId = `ocrebuild-image-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const outputRel = String(menu.assetTask?.output || "").trim();
    const outputPath = assetOutputAbsPath(outputRel);
    const requestPayload = {
      api_call_id: apiCallId,
      task_id: selectedTaskId(),
      session_id: task()?.session_id || null,
      shot_id: menu.pkg.shot_id,
      scene_mark_id: menu.pkg.scene_mark_id || "",
      role: menu.role,
      use_reference_image: useReferenceImage,
      workspace_dir: task()?.workspace_dir || "",
      output: outputRel,
      output_path: outputPath,
    };
    setAssetImageContextMenu(null);
    window.dispatchEvent(new CustomEvent("opencrew:debug-session", { detail: { sessionId: task()?.session_id || null, reset: true } }));
    window.dispatchEvent(new CustomEvent("opencrew:debug-entry", { detail: { sessionId: task()?.session_id || null, event: { id: `local-${apiCallId}`, kind: "ocrebuild.asset_image.requested", payload: requestPayload, created_at: Date.now(), local: true } } }));
    emitDebugRequested(debugContext({ family: "asset_image", api_call_id: apiCallId, workflow_id: requestPayload.scene_mark_id || requestPayload.shot_id, ...requestPayload }));
    await runAction(`asset-image-${menu.pkg.scene_mark_id || menu.pkg.shot_id}-${menu.role}`, async () => {
      await new Promise((resolve, reject) => {
        const source = new EventSource(api.generateAssetImageEventsUrl(selectedTaskId(), { shot_id: menu.pkg.shot_id, scene_mark_id: menu.pkg.scene_mark_id || "", role: menu.role, use_reference_image: useReferenceImage, api_call_id: apiCallId }), { withCredentials: true });
        source.onmessage = (event) => {
          try {
            const payload = JSON.parse(event.data || "{}");
            emitStreamDebug(payload, { family: "asset_image", api_call_id: apiCallId, workflow_id: requestPayload.scene_mark_id || requestPayload.shot_id });
            if (payload.type === "completed") { source.close(); resolve(payload); }
            if (payload.type === "failed") { source.close(); reject(new Error(payload.detail || "Image generation failed")); }
          } catch (err) {
            source.close();
            reject(err);
          }
        };
        source.onerror = () => { source.close(); reject(new Error("Image generation event stream disconnected")); };
      });
      await loadAssetTasks({ force: true });
    });
  }
  async function ensureImageModelConfig() {
    if (imageModelConfig()) return imageModelConfig();
    const config = await api.imageModelConfig();
    setImageModelConfig(config);
    return config;
  }
  async function ensureVideoModelConfig() {
    if (videoModelConfig()) return videoModelConfig();
    const config = await api.videoModelConfig();
    setVideoModelConfig(config);
    return config;
  }
  async function ensureTTSModelConfig() {
    if (ttsModelConfig()) return ttsModelConfig();
    const config = await api.ttsModelConfig();
    setTTSModelConfig(config);
    return config;
  }
  function shotTTSWorkflowId(shot) { return normalizeWorkflowId(`${shot?.shot_id || "shot"}_tts_lab`); }
  function planTTSWorkflowId() { return "shot_plan_tts_voice"; }
  function ttsSceneItems(shot) {
    const savedScenes = assetTTSWorkflows()[shotTTSWorkflowId(shot)]?.scenes || {};
    return selectedShotAssetPackages().map((pkg) => {
      const firstImageTask = assetTaskByType(pkg, ["image_regenerate_first", "image_regenerate_single"]);
      const reference = pkg?.reference || {};
      const generated = firstImageTask?.output || "";
      const fallback = reference.first_frame || reference.single_frame || "";
      const saved = savedScenes[pkg?.scene_mark_id || ""] || {};
      return { scene_mark_id: pkg?.scene_mark_id || "", shot_id: pkg?.shot_id || shot?.shot_id || "", srt_text: pkg?.srt_text || "", image: generated || fallback, source: generated ? "generated_first" : "reference_first", planned_duration: sceneDurationForPackage(pkg), tts_duration: saved.tts_duration || saved.final?.duration || null, final: saved.final || null, candidates: saved.candidates || [] };
    }).filter((item) => item.scene_mark_id);
  }
  function providerTTSModel(provider, preferred = "") {
    const config = ttsModelConfig();
    const providerConfig = (config?.providers || []).find((item) => item.provider === provider);
    if (!providerConfig) return null;
    const models = providerConfig.models || [];
    const preferredModel = models.find((item) => item.model === preferred);
    const configured = models.find((item) => item.model === providerConfig.model);
    const fallback = provider === "qwen" ? models.find((item) => item.model === "qwen3-tts-instruct-flash") : provider === "xai" ? models.find((item) => item.model === "xai-tts") : models.find((item) => String(item.model).includes("tts"));
    const model = preferredModel || fallback || configured || models[0];
    if (!model) return null;
    const selectedVoice = providerConfig.selected_voice_by_model?.[model.model] || model.voices?.[0]?.voice_id || "";
    return { provider: providerConfig.provider, providerLabel: providerConfig.provider_label || providerConfig.provider, model: model.model, models, voices: model.voices || [], voiceId: selectedVoice, enabled: providerConfig.enabled, hasApiKey: providerConfig.has_api_key };
  }
  function defaultTTSPrompt(scene, provider) {
    const base = "自然中文短视频旁白，吐字清晰，节奏贴合画面，不夸张，不医疗化。";
    if (provider === "qwen") return `${base} 用年轻中文女声，口语化、亲和、有轻微情绪推进。`;
    if (provider === "xai") return `${base} Voice should sound conversational, warm, and clear.`;
    return `${base} Bright, clear, conversational delivery.`;
  }
  function shotTTSFullText(sceneItems) {
    return (sceneItems || []).map((scene) => String(scene.srt_text || "").trim()).filter(Boolean).join("\n");
  }
  function defaultShotTTSPrompt(shot, targetDuration, provider) {
    const durationText = targetDuration ? `目标总时长 ${Number(targetDuration).toFixed(2)} 秒。` : "";
    const base = `整段 Shot 连续中文短视频旁白，吐字清晰，句子之间自然衔接，节奏紧凑但不机械。${durationText}严格按照提供的 SRT 文本朗读，不改词、不加词。`;
    if (provider === "qwen") return `${base} 声音自然、亲和、带轻微情绪推进。`;
    if (provider === "xai") return `${base} Voice should sound conversational, controlled, and continuous across all lines.`;
    return `${base} Bright, clear, conversational delivery with minimal dramatic pauses.`;
  }
  function defaultTTSPrompts(scene) {
    return [
      providerTTSModel("google", "gemini-2.5-flash-preview-tts"),
      providerTTSModel("xai", "xai-tts"),
      providerTTSModel("qwen", "qwen3-tts-instruct-flash"),
    ].filter(Boolean).map((item) => ({ provider: item.provider, providerLabel: item.providerLabel, model: item.model, models: item.models, voices: item.voices, voiceId: item.voiceId, currentPrompt: defaultTTSPrompt(scene, item.provider), userInstruction: "", text: scene.srt_text || "", refining: false, error: "", enabled: item.enabled, hasApiKey: item.hasApiKey }));
  }
  function defaultShotTTSPrompts(shot, sceneItems, targetDuration) {
    const text = shotTTSFullText(sceneItems);
    const planSelection = shot?.tts_selection || shotPlan()?.plan_a_tts_selection || null;
    const cards = [
      providerTTSModel("google", "gemini-2.5-flash-preview-tts"),
      providerTTSModel("xai", "xai-tts"),
      providerTTSModel("qwen", "qwen3-tts-instruct-flash"),
    ].filter(Boolean).map((item) => ({ provider: item.provider, providerLabel: item.providerLabel, model: item.model, models: item.models, voices: item.voices, voiceId: item.voiceId, currentPrompt: defaultShotTTSPrompt(shot, targetDuration, item.provider), userInstruction: "", text, refining: false, error: "", enabled: item.enabled, hasApiKey: item.hasApiKey }));
    return applyTTSPlanSelection(cards, planSelection, shot, sceneItems, targetDuration);
  }
  function mergeStoredShotTTSPrompts(baseCards, storedPrompts, shot, sceneItems, targetDuration) {
    const hydrateStored = (item) => {
      const opts = providerTTSModel(item.provider, item.model) || {};
      return { provider: item.provider, providerLabel: opts.providerLabel || item.provider, model: item.model, models: opts.models || [{ model: item.model, label: item.model }], voices: opts.voices || [], voiceId: item.voice_id || opts.voiceId || "", currentPrompt: item.prompt || "", userInstruction: item.user_instruction || "", text: item.text || shotTTSFullText(sceneItems), refining: false, error: "", enabled: true, hasApiKey: true };
    };
    const storedCards = (storedPrompts || []).map(hydrateStored);
    const merged = baseCards.map((card) => {
      const saved = storedCards.find((item) => item.provider === card.provider && item.model === card.model) || storedCards.find((item) => item.provider === card.provider);
      return saved ? { ...card, ...saved, models: saved.models?.length ? saved.models : card.models, voices: saved.voices?.length ? saved.voices : card.voices, text: saved.text || card.text } : card;
    });
    const extra = storedCards.filter((saved) => !merged.some((card) => card.provider === saved.provider));
    return applyTTSPlanSelection([...merged, ...extra], shot?.tts_selection || shotPlan()?.plan_a_tts_selection || null, shot, sceneItems, targetDuration);
  }
  function applyTTSPlanSelection(cards, planSelection, shot, sceneItems, targetDuration) {
    if (!planSelection?.provider || !planSelection?.model || !(planSelection?.voice_id || planSelection?.voice)) return cards;
    const selected = {
      provider: planSelection.provider,
      provider_label: planSelection.provider_label || planSelection.provider,
      model: planSelection.model,
      voice_id: planSelection.voice_id || planSelection.voice,
      label: planSelection.label || planSelection.voice_id || planSelection.voice,
      score: planSelection.score,
      style: planSelection.style || "",
      tempo: tempoFromSelection(planSelection),
      fit_meta: planSelection.fit_meta || {},
      top_candidates: planSelection.top_candidates || [],
    };
    return cards.map((card) => {
      if (card.provider !== selected.provider) return card;
      const opts = providerTTSModel(selected.provider, selected.model) || {};
      return { ...card, providerLabel: opts.providerLabel || selected.provider_label || card.providerLabel, model: selected.model, models: opts.models || card.models, voices: opts.voices?.length ? opts.voices : card.voices, voiceId: selected.voice_id, currentPrompt: planSelection.prompt || promptForRecommendedVoice(selected, shot, targetDuration), tempo: selected.tempo, userInstruction: "默认使用 Shot Plan 推荐声音。", recommendation: selected, text: shotTTSFullText(sceneItems) };
    });
  }
  function promptForRecommendedVoice(item, shot, targetDuration) {
    const durationText = targetDuration ? `目标总时长 ${Number(targetDuration).toFixed(2)} 秒。` : "";
    const style = item?.style ? `声音特质：${item.style}。` : "";
    const gender = item?.candidate_profile?.gender || item?.metadata?.gender || "";
    const genderText = gender === "female" ? "女声" : gender === "male" ? "男声" : "自然人声";
    return `中文普通话${genderText}，清晰自然，适合商业短视频旁白。${durationText}${style}语气有一点吐槽感和表现力，节奏利落，情绪轻微上扬。严格按照提供文本朗读，不改词、不加词。`;
  }
  function promptCardFromRecommendation(item, shot, sceneItems, targetDuration) {
    const opts = providerTTSModel(item.provider, item.model) || {};
    const builderPrompt = String(item?.prompt_template || item?.instructions || item?.prompt || "").trim();
    return { provider: item.provider, providerLabel: opts.providerLabel || item.provider_label || item.provider, model: item.model, models: opts.models || [{ model: item.model, label: item.model }], voices: opts.voices || [{ voice_id: item.voice_id, label: item.label || item.voice_id }], voiceId: item.voice_id, currentPrompt: builderPrompt || promptForRecommendedVoice(item, shot, targetDuration), tempo: tempoFromSelection(item), userInstruction: "更贴近参考视频声音，保持普通话、自然短视频旁白、轻微表现力。", text: shotTTSFullText(sceneItems), refining: false, error: "", enabled: true, hasApiKey: true, recommendation: item };
  }
  const qwenVoiceGuideScenarios = [
    { id: "qwen-basic", label: "基础中文朗读", modelType: "all", simplePrompt: "欢迎使用 OpenCrew。我们正在测试千问 TTS 的中文普通话朗读、清晰度、自然度和节奏稳定性。", buildComplexPrompt: (simple) => simple },
    { id: "qwen-voice-match", label: "音色适配对比", modelType: "all", simplePrompt: "这是一段品牌介绍旁白。语气要清楚、稳定、可信，同时保留一点亲和力，适合放在产品演示视频里。", buildComplexPrompt: (simple, voiceLabel) => `# Qwen voice match\nVoice: ${voiceLabel}\n\n${simple}` },
    { id: "qwen-multilingual", label: "多语言/中英混读", modelType: "all", simplePrompt: "Hello, welcome to OpenCrew. 今天我们测试同一个音色在 English 和中文之间切换时，发音是否自然、节奏是否稳定。", buildComplexPrompt: (simple, voiceLabel) => `# Qwen multilingual test\nVoice: ${voiceLabel}\nLanguage type: Chinese\n\n${simple}` },
    { id: "qwen-dialect", label: "中文方言音色", modelType: "flash", simplePrompt: "今天天气巴适得很，我们来试一下地方口音的自然度、亲切感和短视频旁白的生活气。", buildComplexPrompt: (simple, voiceLabel) => `# Qwen dialect test\nVoice: ${voiceLabel}\nLanguage type: Chinese\n\n${simple}` },
    { id: "qwen-news", label: "新闻/知识讲解", modelType: "all", simplePrompt: "下面进入今日重点。人工智能正在改变内容生产流程，企业需要同时关注效率、质量和合规边界。", buildComplexPrompt: (simple) => simple },
    { id: "qwen-instruct-pacing", label: "指令控制：语速语调", modelType: "instruct", simplePrompt: "这款产品今天正式上线。前三秒要迅速抓住注意力，中段讲清核心卖点，结尾要有明确行动感。", buildComplexPrompt: (simple, voiceLabel) => `语速偏快，音调略高，前三秒有明显上扬语调，整体充满活力和感染力，适合广告配音。使用 ${voiceLabel} 保持清晰自然。\n\n朗读文本：${simple}` },
    { id: "qwen-instruct-emotion", label: "指令控制：情绪递进", modelType: "instruct", simplePrompt: "我一开始只是有点惊讶，后来越来越激动，直到最后忍不住大声说：这就是我们一直在等的答案。", buildComplexPrompt: (simple) => `情绪从克制惊讶逐步增强到激动，音量由正常对话逐渐提高，最后一句有明显爆发力；吐字仍需清晰，不要失真。\n\n朗读文本：${simple}` },
    { id: "qwen-instruct-audiobook", label: "指令控制：有声书角色", modelType: "instruct", simplePrompt: "夜色压下来时，老人终于开口了。他说，真正重要的东西，从来不会在喧哗里出现。", buildComplexPrompt: (simple) => `低沉、缓慢、沉稳，带有年长叙述者的沧桑感；停顿略长，像在讲一个重要的秘密；整体适合有声书旁白。\n\n朗读文本：${simple}` },
  ];
  const xaiVoiceGuideScenarios = [
    { id: "xai-basic", label: "基础朗读", simplePrompt: "你好，欢迎使用 xAI Text to Speech。我们正在测试中文基础朗读、清晰度和自然度。", buildComplexPrompt: (simple) => simple },
    { id: "xai-voices", label: "内置声音对比", simplePrompt: "这是一段商务汇报风格的旁白。请保持专业、清晰、有条理，适合产品演示和企业培训。", buildComplexPrompt: (simple) => simple },
    { id: "xai-language", label: "语言选择/自动检测", simplePrompt: "你好，这是中文。Hello, this is English. 这段文本用于测试 auto 语言检测和混合语言朗读。", buildComplexPrompt: (simple) => simple },
    { id: "xai-inline-tags", label: "Inline Speech Tags", simplePrompt: "So I walked in and [pause] there it was. [laugh] I honestly could not believe it! [sigh] What a day.", buildComplexPrompt: (simple) => simple },
    { id: "xai-wrapping-tags", label: "Wrapping Speech Tags", simplePrompt: "I need to tell you something. <whisper>It is a secret.</whisper> Pretty cool, right? <loud>Now listen carefully.</loud>", buildComplexPrompt: (simple) => simple },
    { id: "xai-custom-voice", label: "自定义声音 ID", simplePrompt: "Hello! This is my custom voice test. If the custom voice ID is valid, this sentence should use that cloned voice.", buildComplexPrompt: (simple, voiceLabel) => `${simple}\n\nCustom voice_id to test: ${voiceLabel}` },
  ];
  const googleVoiceGuideScenarios = GOOGLE_TTS_SCENARIO_GUIDES;
  function voiceGuideProviderKind(item) {
    const provider = String(item?.provider || "").toLowerCase();
    if (provider.includes("qwen")) return "qwen";
    if (provider.includes("xai")) return "xai";
    if (provider.includes("google") || provider.includes("gemini")) return "google";
    return "generic";
  }
  function voiceGuideTitle(item) {
    const kind = voiceGuideProviderKind(item);
    if (kind === "qwen") return "Qwen Voice Guide";
    if (kind === "xai") return "xAI Voice Guide";
    if (kind === "google") return "Google Scenario Lab";
    return `${item?.provider_label || item?.provider || "Provider"} Voice Guide`;
  }
  function voiceGuideScenarioOptions(item) {
    const kind = voiceGuideProviderKind(item);
    const model = String(item?.model || "");
    if (kind === "qwen") return qwenVoiceGuideScenarios.filter((scenario) => {
      if (scenario.modelType === "instruct") return model.includes("instruct");
      if (scenario.modelType === "flash") return model.includes("qwen3-tts-flash") && !model.includes("instruct");
      return true;
    });
    if (kind === "xai") return xaiVoiceGuideScenarios;
    if (kind === "google") return googleVoiceGuideScenarios;
    return [
      { id: "commercial_narration", label: "商业短视频旁白", simplePrompt: "欢迎使用 OpenCrew。下面这段声音将用于测试语气、节奏、清晰度和商业短视频旁白的自然程度。", buildComplexPrompt: (simple) => simple },
      { id: "single_speaker_reading", label: "单说话人基础朗读", simplePrompt: "请用自然、清晰、稳定的语气朗读：欢迎使用 OpenCrew。", buildComplexPrompt: (simple) => simple },
    ];
  }
  function voiceGuideScenario(item, scenario = "") {
    const options = voiceGuideScenarioOptions(item);
    return options.find((option) => option.id === scenario) || options[0];
  }
  function voiceGuideSimplePrompt(item, scenario = "") {
    const selected = voiceGuideScenario(item, scenario);
    if (selected?.simplePrompt) return selected.simplePrompt;
    const label = item?.label || item?.voice_id || "voice";
    const style = item?.style ? `声音特质：${item.style}。` : "";
    return `请用 ${label} 做中文普通话商业短视频旁白，清晰自然，节奏利落，有轻微表现力。${style}`;
  }
  function voiceGuideComplexPlaceholder(item) {
    return voiceGuideProviderKind(item) === "qwen"
      ? "Click Generate Complex Prompt, then edit before previewing. Flash models ignore instructions; Instruct models send this as instructions."
      : "Click Generate Complex Prompt, then edit before previewing.";
  }
  function voiceGuideComplexLabel(item) {
    return voiceGuideProviderKind(item) === "qwen" ? "Complex Prompt / Instructions" : "Complex Prompt";
  }
  function voiceGuideScenarioInfo(item, scenario = "") {
    const selected = voiceGuideScenario(item, scenario);
    const kind = voiceGuideProviderKind(item);
    const byProvider = {
      qwen: {
        body: selected?.id?.includes("instruct")
          ? "千问 Instruct 模型会把复杂提示词作为 instructions 使用，用来控制语速、语调、情绪和用途风格；Flash 普通模型主要使用朗读文本本身。"
          : "千问场景主要验证 voice、language_type 和文本内容的适配。Flash 模型支持更多系统音色和部分方言音色。推荐试听时重点听普通话自然度、音色匹配和节奏稳定性。",
        verifies: selected?.id?.includes("instruct") ? ["instructions", "语速/语调", "情绪递进", "用途风格"] : ["voice", "language_type=Chinese", "普通话自然度", "场景匹配"],
      },
      xai: {
        body: selected?.id === "xai-inline-tags"
          ? "xAI 支持在文本中插入 inline speech tags，例如 [pause]、[laugh]、[sigh]，用于测试停顿、笑声和呼吸等局部表达。"
          : selected?.id === "xai-wrapping-tags"
            ? "xAI 支持 wrapping speech tags，例如 <whisper>...</whisper>、<loud>...</loud>、<slow>...</slow>，用于让一段文本改变朗读方式。"
            : selected?.id === "xai-custom-voice"
              ? "自定义声音 ID 用于验证自定义或克隆声音，实际使用时需要在 xAI Voice Library 中创建并复制 voice_id。"
              : "xAI 场景用于快速验证内置 voice、language 和文本 tags。推荐试听时重点听该声音是否符合短视频旁白的年龄感、权威感、清晰度和自然度。",
        verifies: selected?.id === "xai-inline-tags" ? ["[pause]", "[laugh]", "[sigh]", "局部表达"] : selected?.id === "xai-wrapping-tags" ? ["<whisper>", "<loud>", "<slow>", "片段风格"] : ["voice_id", "language", "基础音频生成", "业务场景匹配"],
      },
      google: {
        body: selected?.infoBodyZh || "Google/Gemini Scenario Lab 使用自然语言导演提示控制朗读风格、语气、节奏和音频标签。推荐试听时重点验证高级提示是否让声音更贴近目标短视频旁白。",
        verifies: selected?.verifies || ["Audio Profile", "Director's Notes", "Transcript", "style / pacing / tags"],
      },
      generic: {
        body: "当前 provider 没有专属场景模板，使用通用中文旁白测试场景。",
        verifies: ["普通话", "清晰度", "语速", "表现力"],
      },
    };
    const info = byProvider[kind] || byProvider.generic;
    return { title: selected?.infoTitle || selected?.label || "Scenario information", ...info };
  }
  function voiceGuideDocsUrl(item) {
    const kind = voiceGuideProviderKind(item);
    if (kind === "qwen") return "https://help.aliyun.com/zh/model-studio/qwen-tts";
    if (kind === "xai") return "https://docs.x.ai/docs/guides/text-to-speech";
    if (kind === "google") return "https://aistudio.google.com/app/generate-speech";
    return item?.docs_url || "#";
  }
  function voiceGuideComplexPrompt(item, scenario = "", simplePrompt = "") {
    const selected = voiceGuideScenario(item, scenario);
    const simple = simplePrompt || voiceGuideSimplePrompt(item, scenario);
    const voice = item?.label || item?.voice_id || "voice";
    return selected?.buildComplexPrompt ? selected.buildComplexPrompt(simple, voice) : simple;
  }
  function hydrateTTSWorkflow(stored, shot, baseScenes) {
    const scenes = stored?.scenes || {};
    const sceneItems = baseScenes.map((scene) => {
      const saved = scenes[scene.scene_mark_id] || {};
      return { ...scene, tts_duration: saved.tts_duration || saved.final?.duration || scene.tts_duration || null, final: saved.final || scene.final || null, candidates: saved.candidates || scene.candidates || [] };
    });
    const activeSceneId = stored?.active_scene_id || baseScenes[0]?.scene_mark_id || "";
    const sceneState = scenes[activeSceneId] || {};
    const shotState = stored?.shot || {};
    const targetDuration = shotState.target_duration || shot?.duration || "";
    const lockedTimeline = shotState.locked_timeline || null;
    return { workflowId: stored?.workflow_id || shotTTSWorkflowId(shot), shot, scenes, sceneItems, activeSceneId, providerPrompts: sceneState.prompts?.length ? sceneState.prompts.map((item) => { const opts = providerTTSModel(item.provider, item.model) || {}; return { provider: item.provider, providerLabel: opts.providerLabel || item.provider, model: item.model, models: opts.models || [{ model: item.model, label: item.model }], voices: opts.voices || [], voiceId: item.voice_id || opts.voiceId || "", currentPrompt: item.prompt || "", userInstruction: item.user_instruction || "", text: item.text || sceneState.srt_text || "", refining: false, error: "", enabled: true, hasApiKey: true }; }) : [], candidates: (sceneState.candidates || []).map(ttsCandidateFromEvent), final: sceneState.final || null, shotProviderPrompts: mergeStoredShotTTSPrompts(defaultShotTTSPrompts(shot, sceneItems, targetDuration), shotState.prompts || [], shot, sceneItems, targetDuration), shotCandidates: (shotState.candidates || []).map(ttsCandidateFromEvent), shotFinal: shotState.final || null, lockedTimeline, shotVideo: defaultShotTTSVideoState(shot, lockedTimeline, stored?.shot_video || null), targetDuration, voiceRecommendations: shotState.voice_recommendations || [], voiceRecommendationResult: shotState.voice_recommendation_result || null, voiceReferenceAudio: shotState.voice_reference_audio || "", voiceReferenceFitAudio: shotState.voice_reference_fit_audio || shotState.voice_recommendation_result?.reference_fit_audio || "", voiceReferenceFitAudioRel: shotState.voice_reference_fit_audio_rel || shotState.voice_recommendation_result?.reference_fit_audio_rel || "", voiceManualReferenceAudio: shotState.voice_manual_reference_audio || "", voiceManualReferenceAudioRel: shotState.voice_manual_reference_audio_rel || "", voiceReferenceStart: shotState.voice_reference_start ?? 0, voiceReferenceDuration: shotState.voice_reference_duration ?? 16, voiceReferenceText: shotState.voice_reference_text || shotState.voice_recommendation_result?.sample_text || "", voiceReferenceSrt: shotState.voice_reference_srt || shotState.voice_recommendation_result?.sample_srt || "", voiceSelection: shotState.voice_selection || null, voiceRecommendGender: "", voiceGuide: null, voiceBuilder: shotState.voice_builder || "", voiceBuilderManifest: shotState.voice_builder_manifest || "", voiceBuilderHtml: shotState.voice_builder_html || "", voiceInfoOpenKey: "", phase: stored?.phase || "list", error: "" };
  }
  async function openAssetTTSWorkflow() {
    const shot = selectedShot();
    if (!shot) return;
    await ensureTTSModelConfig();
    await ensureVideoModelConfig();
    const workflowId = shotTTSWorkflowId(shot);
    const sceneItems = ttsSceneItems(shot);
    const targetDuration = shot?.duration || "";
    const base = { workflowId, shot, scenes: {}, sceneItems, activeSceneId: sceneItems[0]?.scene_mark_id || "", providerPrompts: [], candidates: [], final: null, shotProviderPrompts: defaultShotTTSPrompts(shot, sceneItems, targetDuration), shotCandidates: [], shotFinal: null, lockedTimeline: null, shotVideo: defaultShotTTSVideoState(shot, null, null), targetDuration, voiceRecommendations: [], voiceRecommendationResult: null, voiceReferenceAudio: "", voiceReferenceFitAudio: "", voiceReferenceFitAudioRel: "", voiceManualReferenceAudio: "", voiceManualReferenceAudioRel: "", voiceReferenceStart: 0, voiceReferenceDuration: 16, voiceReferenceText: "", voiceReferenceSrt: "", voiceSelection: null, voiceRecommendGender: "", voiceGuide: null, phase: "list", error: "" };
    updateAssetTTSWorkflow(workflowId, base);
    try {
      const saved = await api.getShotTTSWorkflow(selectedTaskId(), workflowId);
      if (saved?.exists && saved.workflow) updateAssetTTSWorkflow(workflowId, hydrateTTSWorkflow(saved.workflow, shot, sceneItems));
    } catch (err) {
      updateAssetTTSWorkflow(workflowId, (prev) => prev ? { ...prev, error: err instanceof Error ? err.message : String(err) } : prev);
    }
    window.dispatchEvent(new CustomEvent("opencrew:debug-session", { detail: { sessionId: task()?.session_id || null, reset: true } }));
  }
  async function openVoiceRecommendWorkflow() {
    const shot = selectedShot();
    if (!shot) return;
    await ensureTTSModelConfig();
    await ensureVideoModelConfig();
    const workflowId = planTTSWorkflowId();
    const sceneItems = ttsSceneItems(shot);
    const targetDuration = shot?.duration || "";
    const base = { workflowId, shot, scope: "shot_plan", scenes: {}, sceneItems, activeSceneId: sceneItems[0]?.scene_mark_id || "", providerPrompts: [], candidates: [], final: null, shotProviderPrompts: defaultShotTTSPrompts(shot, sceneItems, targetDuration), shotCandidates: [], shotFinal: null, lockedTimeline: null, shotVideo: defaultShotTTSVideoState(shot, null, null), targetDuration, voiceRecommendations: [], voiceRecommendationResult: null, voiceReferenceAudio: "", voiceReferenceFitAudio: "", voiceReferenceFitAudioRel: "", voiceManualReferenceAudio: "", voiceManualReferenceAudioRel: "", voiceReferenceStart: 0, voiceReferenceDuration: 16, voiceReferenceText: "", voiceReferenceSrt: "", voiceSelection: null, voiceRecommendGender: "", voiceGuide: null, voiceBuilder: "", voiceBuilderManifest: "", voiceBuilderHtml: "", voiceInfoOpenKey: "", phase: "list", error: "" };
    updateAssetTTSWorkflow(workflowId, base);
    try {
      const saved = await api.getShotTTSWorkflow(selectedTaskId(), workflowId);
      if (saved?.exists && saved.workflow) updateAssetTTSWorkflow(workflowId, { ...hydrateTTSWorkflow(saved.workflow, shot, sceneItems), workflowId, scope: "shot_plan" });
    } catch (err) {
      updateAssetTTSWorkflow(workflowId, (prev) => prev ? { ...prev, error: err instanceof Error ? err.message : String(err) } : prev);
    }
  }
  async function recommendVoiceForShot(workflowId) {
    const wf = assetTTSWorkflows()[workflowId];
    if (!wf) return;
    updateAssetTTSWorkflow(workflowId, (prev) => prev ? { ...prev, phase: "voice_recommending", error: "" } : prev);
    try {
      const res = await api.recommendShotTTSVoice(selectedTaskId(), { workflow_id: wf.workflowId, scope: wf.scope || "shot_plan", shot_id: wf.scope === "shot_plan" ? "" : wf.shot?.shot_id, reference_text: wf.scope === "shot_plan" ? "" : shotTTSFullText(wf.sceneItems), target_gender: wf.scope === "shot_plan" ? "" : (wf.voiceRecommendGender || ""), language: "zh", top_k: 5, regenerate: false });
      const recommendations = res.recommendations || [];
      updateAssetTTSWorkflow(workflowId, (prev) => prev ? { ...prev, phase: "voice_recommended", voiceRecommendations: recommendations, voiceRecommendationResult: res.match_result || null, voiceReferenceAudio: res.reference_audio || "", voiceReferenceFitAudio: res.reference_fit_audio || res.match_result?.reference_fit_audio || "", voiceReferenceFitAudioRel: res.reference_fit_audio_rel || res.match_result?.reference_fit_audio_rel || "", voiceReferenceText: res.reference_text || "" } : prev);
    } catch (err) {
      updateAssetTTSWorkflow(workflowId, (prev) => prev ? { ...prev, phase: "list", error: err instanceof Error ? err.message : String(err) } : prev);
    }
  }
  function voiceRecommendationKey(item) {
    return `${item?.builder || ""}/${item?.provider || ""}/${item?.model || ""}/${item?.voice_id || item?.voice || ""}/${item?.candidate_id || ""}`;
  }
  function savedVoiceBuilderKey(workflow) {
    const shotState = workflow?.shot || {};
    return String(shotState.voice_builder || shotState.voice_recommendation_result?.builder || "").toLowerCase();
  }
  function applyShotTTSVoiceBuilderResult(workflowId, builder, res, shot, sceneItems) {
    if (res?.workflow && savedVoiceBuilderKey(res.workflow) === builder) {
      updateAssetTTSWorkflow(workflowId, { ...hydrateTTSWorkflow(res.workflow, shot, sceneItems), workflowId, scope: "shot_plan", activeVoiceBuilder: "" });
      return;
    }
    updateAssetTTSWorkflow(workflowId, (prev) => prev ? { ...prev, phase: "voice_builder_completed", activeVoiceBuilder: "", voiceBuilder: builder, voiceBuilderManifest: res.manifest || res.match_result?.manifest || "", voiceBuilderHtml: res.html_review || res.match_result?.html_review || "", voiceRecommendations: res.recommendations || [], voiceRecommendationResult: res.match_result || null, voiceReferenceAudio: res.reference_audio || "", voiceReferenceFitAudio: res.reference_fit_audio || res.match_result?.reference_fit_audio || "", voiceReferenceFitAudioRel: res.reference_fit_audio_rel || res.match_result?.reference_fit_audio_rel || "", voiceReferenceText: res.reference_text || res.match_result?.sample_text || "", voiceReferenceSrt: res.match_result?.sample_srt || "", voiceInfoOpenKey: "", error: "" } : prev);
  }
  async function pollShotTTSVoiceBuilderCompletion(workflowId, builder, shot, sceneItems) {
    const runningPhase = `voice_builder_${builder}_running`;
    for (let attempt = 0; attempt < 120; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 2000));
      const current = assetTTSWorkflows()[workflowId];
      if (!current || current.phase !== runningPhase) return;
      try {
        const saved = await api.getShotTTSWorkflow(selectedTaskId(), workflowId);
        if (saved?.exists && saved.workflow && saved.workflow.phase === "voice_builder_completed" && savedVoiceBuilderKey(saved.workflow) === builder) {
          updateAssetTTSWorkflow(workflowId, { ...hydrateTTSWorkflow(saved.workflow, shot, sceneItems), workflowId, scope: "shot_plan", activeVoiceBuilder: "" });
          return;
        }
      } catch {
        // The direct builder response remains the primary path; polling is only a UI recovery path.
      }
    }
    updateAssetTTSWorkflow(workflowId, (prev) => prev?.phase === runningPhase ? { ...prev, phase: "list", activeVoiceBuilder: "", error: "Builder finished state was not observed within the UI timeout. Please reopen the recommendation panel to reload saved results." } : prev);
  }
  async function runShotTTSVoiceBuilder(workflowId, builder) {
    const wf = assetTTSWorkflows()[workflowId];
    if (!wf) return;
    const label = builder === "g" ? "Builder-G / Gemini" : "Builder-Q / Qwen";
    const confirmed = window.confirm(`${label} 会重新运行对应的 TTS Voice Builder，刷新当前推荐结果并可能重新生成候选音频。确认运行？`);
    if (!confirmed) return;
    updateAssetTTSWorkflow(workflowId, (prev) => prev ? { ...prev, phase: `voice_builder_${builder}_running`, activeVoiceBuilder: builder, voiceRecommendations: [], voiceReferenceText: "", voiceReferenceSrt: "", error: "" } : prev);
    void pollShotTTSVoiceBuilderCompletion(workflowId, builder, wf.shot, wf.sceneItems);
    try {
      const manualReferenceAudio = String(wf.voiceManualReferenceAudio || "").trim();
      const referenceDuration = Number(wf.voiceReferenceDuration || 0);
      const res = await api.buildShotTTSVoice(selectedTaskId(), { workflow_id: wf.workflowId, builder, confirm: true, force: true, generate_html: true, reference_audio: manualReferenceAudio, reference_start: Number(wf.voiceReferenceStart || 0), reference_duration: referenceDuration > 0 ? referenceDuration : undefined });
      applyShotTTSVoiceBuilderResult(workflowId, builder, res, wf.shot, wf.sceneItems);
    } catch (err) {
      updateAssetTTSWorkflow(workflowId, (prev) => prev?.phase === `voice_builder_${builder}_running` ? { ...prev, phase: "list", activeVoiceBuilder: "", error: err instanceof Error ? err.message : String(err) } : prev);
    }
  }
  async function uploadShotTTSReferenceAudio(workflowId, files) {
    const file = Array.from(files || [])[0];
    if (!file || !selectedTaskId()) return;
    updateAssetTTSWorkflow(workflowId, (prev) => prev ? { ...prev, referenceUploadPhase: "uploading", error: "" } : prev);
    try {
      const res = await api.uploadShotTTSReferenceAudio(selectedTaskId(), workflowId, file);
      updateAssetTTSWorkflow(workflowId, (prev) => prev ? { ...prev, referenceUploadPhase: "uploaded", voiceManualReferenceAudio: res.abs_path || "", voiceManualReferenceAudioRel: res.path || "", voiceReferenceStart: prev.voiceReferenceStart ?? 0, voiceReferenceDuration: prev.voiceReferenceDuration || 16, error: "" } : prev);
    } catch (err) {
      updateAssetTTSWorkflow(workflowId, (prev) => prev ? { ...prev, referenceUploadPhase: "idle", error: err instanceof Error ? err.message : String(err) } : prev);
    }
  }
  function updateVoiceReferenceClip(workflowId, patch) {
    const wf = assetTTSWorkflows()[workflowId];
    if (wf?.voicePlayback?.key?.startsWith("reference/")) stopVoicePreview();
    updateAssetTTSWorkflow(workflowId, (prev) => prev ? { ...prev, ...patch } : prev);
  }
  async function selectRecommendedVoice(workflowId, recommendation) {
    const wf = assetTTSWorkflows()[workflowId];
    if (!wf || !recommendation) return;
    const card = promptCardFromRecommendation(recommendation, wf.shot, wf.sceneItems, wf.targetDuration);
    updateAssetTTSWorkflow(workflowId, (prev) => prev ? { ...prev, voiceSelection: recommendation, shotProviderPrompts: applyTTSPlanSelection(defaultShotTTSPrompts(prev.shot, prev.sceneItems, prev.targetDuration), { ...recommendation, prompt: card.currentPrompt }, prev.shot, prev.sceneItems, prev.targetDuration), error: "" } : prev);
    try {
      const res = await api.saveShotTTSVoiceSelection(selectedTaskId(), { workflow_id: wf.workflowId, scope: wf.scope || "shot_plan", shot_id: wf.scope === "shot_plan" ? "" : wf.shot?.shot_id, provider: recommendation.provider, model: recommendation.model, voice_id: recommendation.voice_id || recommendation.voice, label: recommendation.label || recommendation.voice_id || recommendation.voice, prompt: card.currentPrompt, score: recommendation.score, prompt_template: recommendation.prompt_template || "", instructions: recommendation.instructions || "", stage: recommendation.stage || "", candidate_id: recommendation.candidate_id || "", audio: recommendation.audio || recommendation.output || "", fit_audio: recommendation.fit_audio || "", raw_audio: recommendation.raw_audio || "", tempo: tempoFromSelection(recommendation), fit_meta: recommendation.fit_meta || {}, top_candidates: wf.voiceRecommendations || [] });
      if (res?.selection) updateAssetTTSWorkflow(workflowId, (prev) => prev ? { ...prev, voiceSelection: res.selection } : prev);
      await loadShotPlan();
    } catch (err) {
      updateAssetTTSWorkflow(workflowId, (prev) => prev ? { ...prev, error: err instanceof Error ? err.message : String(err) } : prev);
    }
  }
  function openVoiceGuide(workflowId, recommendation) {
    const wf = assetTTSWorkflows()[workflowId];
    if (!wf || !recommendation) return;
    voiceGuideSimplePromptEl = null;
    voiceGuideComplexPromptEl = null;
    const scenario = voiceGuideScenarioOptions(recommendation)[0]?.id || "commercial_narration";
    const key = voiceRecommendationKey(recommendation);
    const simplePrompt = voiceGuideSimplePrompt(recommendation, scenario);
    setVoiceGuideDialog({ workflowId, key, recommendation, scenario, sampleText: simplePrompt, simplePrompt, complexPrompt: voiceGuideComplexPlaceholder(recommendation), audioUrl: "", phase: "idle", infoOpen: false, error: "" });
  }
  function closeVoiceGuide() {
    voiceGuideSimplePromptEl = null;
    voiceGuideComplexPromptEl = null;
    setVoiceGuideDialog(null);
  }
  function updateVoiceGuide(patch) {
    setVoiceGuideDialog((prev) => prev ? { ...prev, ...patch } : prev);
  }
  function updateVoiceGuideScenario(scenario) {
    const recommendation = voiceGuideDialog()?.recommendation;
    if (!recommendation) return;
    const simplePrompt = voiceGuideSimplePrompt(recommendation, scenario);
    const complexPrompt = voiceGuideComplexPlaceholder(recommendation);
    if (voiceGuideSimplePromptEl) voiceGuideSimplePromptEl.value = simplePrompt;
    if (voiceGuideComplexPromptEl) voiceGuideComplexPromptEl.value = complexPrompt;
    updateVoiceGuide({ scenario, sampleText: simplePrompt, simplePrompt, complexPrompt, audioUrl: "", infoOpen: false, error: "" });
  }
  function buildVoiceGuideComplexPrompt() {
    const guide = voiceGuideDialog();
    if (!guide?.recommendation) return;
    const simplePrompt = voiceGuideSimplePromptEl?.value ?? guide.simplePrompt;
    const complexPrompt = voiceGuideComplexPrompt(guide.recommendation, guide.scenario, simplePrompt);
    if (voiceGuideComplexPromptEl) voiceGuideComplexPromptEl.value = complexPrompt;
    updateVoiceGuide({ simplePrompt, complexPrompt, audioUrl: "", phase: "prompt_ready", error: "" });
  }
  async function generateVoiceGuidePreview() {
    const guide = voiceGuideDialog();
    const recommendation = guide?.recommendation;
    if (!guide || !recommendation) return;
    const simplePrompt = voiceGuideSimplePromptEl?.value ?? guide.simplePrompt;
    const complexPrompt = voiceGuideComplexPromptEl?.value ?? guide.complexPrompt;
    updateVoiceGuide({ phase: "generating", error: "", audioUrl: "" });
    try {
      const res = await api.previewTTSVoice({
        provider: recommendation.provider,
        model: recommendation.model,
        voice_id: recommendation.voice_id,
        sample_text: simplePrompt,
        simple_prompt: simplePrompt,
        complex_prompt: complexPrompt,
        language: "zh",
      });
      updateVoiceGuide({ simplePrompt, complexPrompt, phase: "generated", audioUrl: res.audio_url || "", error: "" });
    } catch (err) {
      updateVoiceGuide({ phase: "idle", error: err instanceof Error ? err.message : String(err) });
    }
  }
  function stopVoicePreview() {
    if (voicePreviewRaf) {
      cancelAnimationFrame(voicePreviewRaf);
      voicePreviewRaf = 0;
    }
    hideReferencePlayhead();
    if (voicePreviewAudio) {
      voicePreviewAudio.pause();
      voicePreviewAudio.currentTime = 0;
      voicePreviewAudio = null;
    }
    setAssetTTSWorkflows((prev) => Object.fromEntries(Object.entries(prev).map(([key, wf]) => [key, { ...wf, voicePlayback: null, ttsPlayback: null }])));
  }
  function ttsWorkflowDialog(workflowId) {
    const escaped = typeof CSS !== "undefined" && CSS.escape ? CSS.escape(workflowId) : String(workflowId).replace(/"/g, '\\"');
    return document.querySelector(`.ocrebuild-tts-dialog[data-workflow-id="${escaped}"]`);
  }
  function updateReferencePlayheadPosition(workflowId, currentTime, duration) {
    const playhead = ttsWorkflowDialog(workflowId)?.querySelector(".ocrebuild-reference-waveform-playhead");
    if (!playhead || !duration) return;
    const pct = Math.min(Math.max(currentTime / duration, 0), 1) * 100;
    playhead.style.left = `${pct}%`;
    playhead.classList.add("is-visible");
  }
  function hideReferencePlayhead(workflowId = "") {
    const root = workflowId ? ttsWorkflowDialog(workflowId) : document;
    root?.querySelectorAll?.(".ocrebuild-reference-waveform-playhead").forEach((item) => item.classList.remove("is-visible"));
  }
  function toggleVoiceRecommendationPlayback(workflowId, item) {
    const key = voiceRecommendationKey(item);
    const wf = assetTTSWorkflows()[workflowId];
    if (wf?.voicePlayback?.key === key) {
      stopVoicePreview();
      return;
    }
    stopVoicePreview();
    const audioUrl = item.preview_audio_url || item.src || rebuildAssetUrl(item.audio || item.output || item.fit_audio || item.raw_audio || "");
    if (!audioUrl) {
      updateAssetTTSWorkflow(workflowId, (prev) => prev ? { ...prev, error: "No preview audio is available for this voice recommendation." } : prev);
      return;
    }
    voicePreviewAudio = new Audio(audioUrl);
    voicePreviewAudio.onended = () => updateAssetTTSWorkflow(workflowId, (prev) => prev?.voicePlayback?.key === key ? { ...prev, voicePlayback: null } : prev);
    voicePreviewAudio.onerror = () => updateAssetTTSWorkflow(workflowId, (prev) => prev?.voicePlayback?.key === key ? { ...prev, voicePlayback: null, error: "Preview audio playback failed." } : prev);
    updateAssetTTSWorkflow(workflowId, (prev) => prev ? { ...prev, voicePlayback: { key } } : prev);
    void voicePreviewAudio.play().catch((err) => {
      updateAssetTTSWorkflow(workflowId, (prev) => prev?.voicePlayback?.key === key ? { ...prev, voicePlayback: null, error: err instanceof Error ? err.message : String(err) } : prev);
    });
  }
  function voiceReferencePlaybackKey(wf) {
    const start = Math.max(0, Number(wf?.voiceReferenceStart || 0));
    const duration = Math.max(0.1, Number(wf?.voiceReferenceDuration || 0.1));
    return `reference/${voiceReferencePlaybackPath(wf)}#${start.toFixed(3)}-${duration.toFixed(3)}`;
  }
  function voiceReferenceDisplayPath(wf) {
    return String(wf?.voiceManualReferenceAudio || wf?.voiceRecommendationResult?.reference_clip?.source_audio || wf?.voiceRecommendationResult?.reference_audio_abs || wf?.voiceReferenceAudio || wf?.voiceRecommendationResult?.reference_audio || wf?.voiceReferenceFitAudio || wf?.voiceRecommendationResult?.reference_fit_audio || wf?.voiceRecommendationResult?.reference_clip?.clip_audio || "").trim();
  }
  function voiceReferencePlaybackPath(wf) {
    return String(wf?.voiceManualReferenceAudioRel || wf?.voiceManualReferenceAudio || wf?.voiceReferenceAudio || wf?.voiceRecommendationResult?.reference_audio || wf?.voiceRecommendationResult?.reference_clip?.source_audio || wf?.voiceRecommendationResult?.reference_audio_abs || wf?.voiceReferenceFitAudioRel || wf?.voiceRecommendationResult?.reference_fit_audio_rel || wf?.voiceReferenceFitAudio || wf?.voiceRecommendationResult?.reference_fit_audio || wf?.voiceRecommendationResult?.reference_clip?.clip_audio || "").trim();
  }
  function voiceReferenceAudioUrl(wf) {
    const path = voiceReferencePlaybackPath(wf);
    if (!path) return "";
    if (/^https?:\/\//.test(path)) return path;
    if (path.startsWith("/")) {
      const sessionFileUrl = openCrewSessionFileUrl(path);
      if (sessionFileUrl) return sessionFileUrl;
      const rebuildUrl = rebuildAssetUrlFromPath(path);
      if (normalizeRebuildAssetPath(path) !== path) return rebuildUrl;
      return shotAssetUrl(path) || rebuildUrl;
    }
    return rebuildAssetUrl(path);
  }
  function toggleVoiceReferencePlayback(workflowId) {
    const wf = assetTTSWorkflows()[workflowId];
    const key = voiceReferencePlaybackKey(wf);
    if (!wf || !voiceReferencePlaybackPath(wf)) return;
    if (wf.voicePlayback?.key === key) {
      stopVoicePreview();
      return;
    }
    stopVoicePreview();
    const audioUrl = voiceReferenceAudioUrl(wf);
    if (!audioUrl) {
      updateAssetTTSWorkflow(workflowId, (prev) => prev ? { ...prev, error: "No reference audio is available for playback." } : prev);
      return;
    }
    voicePreviewAudio = new Audio(audioUrl);
    const start = Math.max(0, Number(wf.voiceReferenceStart || 0));
    const duration = Math.max(0.1, Number(wf.voiceReferenceDuration || 0.1));
    let stopAt = start + duration;
    const updateReferencePlayhead = () => {
      if (!voicePreviewAudio) return;
      const currentTime = voicePreviewAudio.currentTime;
      const audioDuration = Number.isFinite(voicePreviewAudio.duration) ? voicePreviewAudio.duration : stopAt;
      updateReferencePlayheadPosition(workflowId, currentTime, audioDuration);
      if (!voicePreviewAudio.paused && currentTime < stopAt - 0.025) voicePreviewRaf = requestAnimationFrame(updateReferencePlayhead);
    };
    const stopWhenPastSelection = () => {
      if (!voicePreviewAudio || voicePreviewAudio.currentTime < stopAt - 0.025) return;
      stopVoicePreview();
    };
    const beginPlayback = () => {
      if (!voicePreviewAudio) return;
      const audioDuration = Number.isFinite(voicePreviewAudio.duration) ? voicePreviewAudio.duration : 0;
      stopAt = audioDuration ? Math.min(start + duration, audioDuration) : start + duration;
      const safeStart = audioDuration ? Math.min(start, Math.max(0, stopAt - 0.05)) : start;
      try {
        voicePreviewAudio.currentTime = safeStart;
      } catch {
        // Some browsers reject seeking before metadata has settled; in that case playback starts naturally.
      }
      updateReferencePlayheadPosition(workflowId, safeStart, audioDuration || stopAt);
      void voicePreviewAudio.play().catch((err) => {
        updateAssetTTSWorkflow(workflowId, (prev) => prev?.voicePlayback?.key === key ? { ...prev, voicePlayback: null, error: err instanceof Error ? err.message : String(err) } : prev);
      });
      if (voicePreviewRaf) cancelAnimationFrame(voicePreviewRaf);
      voicePreviewRaf = requestAnimationFrame(updateReferencePlayhead);
    };
    voicePreviewAudio.onended = () => {
      hideReferencePlayhead(workflowId);
      updateAssetTTSWorkflow(workflowId, (prev) => prev?.voicePlayback?.key === key ? { ...prev, voicePlayback: null } : prev);
    };
    voicePreviewAudio.onerror = () => {
      hideReferencePlayhead(workflowId);
      updateAssetTTSWorkflow(workflowId, (prev) => prev?.voicePlayback?.key === key ? { ...prev, voicePlayback: null, error: "Reference audio playback failed." } : prev);
    };
    voicePreviewAudio.ontimeupdate = stopWhenPastSelection;
    updateAssetTTSWorkflow(workflowId, (prev) => prev ? { ...prev, voicePlayback: { key } } : prev);
    if (voicePreviewAudio.readyState >= 1) beginPlayback();
    else voicePreviewAudio.onloadedmetadata = beginPlayback;
  }
  function toggleVoiceRecommendationInfo(workflowId, item) {
    const key = voiceRecommendationKey(item);
    updateAssetTTSWorkflow(workflowId, (prev) => prev ? { ...prev, voiceInfoOpenKey: prev.voiceInfoOpenKey === key ? "" : key } : prev);
  }
  function toggleTTSCandidatePlayback(workflowId, candidate) {
    const key = `${candidate.provider}/${candidate.model}/${candidate.output}`;
    const wf = assetTTSWorkflows()[workflowId];
    if (wf?.ttsPlayback?.key === key) {
      stopVoicePreview();
      return;
    }
    stopVoicePreview();
    voicePreviewAudio = new Audio(candidate.src || rebuildAssetUrl(candidate.output || ""));
    voicePreviewAudio.onended = () => updateAssetTTSWorkflow(workflowId, (prev) => prev?.ttsPlayback?.key === key ? { ...prev, ttsPlayback: null } : prev);
    voicePreviewAudio.onerror = () => updateAssetTTSWorkflow(workflowId, (prev) => prev?.ttsPlayback?.key === key ? { ...prev, ttsPlayback: null, error: "TTS audio playback failed." } : prev);
    updateAssetTTSWorkflow(workflowId, (prev) => prev ? { ...prev, ttsPlayback: { key } } : prev);
    void voicePreviewAudio.play().catch((err) => {
      updateAssetTTSWorkflow(workflowId, (prev) => prev?.ttsPlayback?.key === key ? { ...prev, ttsPlayback: null, error: err instanceof Error ? err.message : String(err) } : prev);
    });
  }
  function toggleLockedTTSPlayback(workflowId, audioSrc) {
    const key = `locked/${audioSrc}`;
    const wf = assetTTSWorkflows()[workflowId];
    if (wf?.ttsPlayback?.key === key) {
      stopVoicePreview();
      return;
    }
    stopVoicePreview();
    voicePreviewAudio = new Audio(audioSrc);
    voicePreviewAudio.onended = () => updateAssetTTSWorkflow(workflowId, (prev) => prev?.ttsPlayback?.key === key ? { ...prev, ttsPlayback: null } : prev);
    voicePreviewAudio.onerror = () => updateAssetTTSWorkflow(workflowId, (prev) => prev?.ttsPlayback?.key === key ? { ...prev, ttsPlayback: null, error: "Locked TTS playback failed." } : prev);
    updateAssetTTSWorkflow(workflowId, (prev) => prev ? { ...prev, ttsPlayback: { key } } : prev);
    void voicePreviewAudio.play().catch((err) => {
      updateAssetTTSWorkflow(workflowId, (prev) => prev?.ttsPlayback?.key === key ? { ...prev, ttsPlayback: null, error: err instanceof Error ? err.message : String(err) } : prev);
    });
  }
  function configureTTSScene(workflowId, scene) {
    const current = assetTTSWorkflows()[workflowId];
    const sceneState = current?.scenes?.[scene.scene_mark_id] || {};
    updateAssetTTSWorkflow(workflowId, (prev) => prev ? { ...prev, activeSceneId: scene.scene_mark_id, phase: "config", providerPrompts: sceneState.prompts?.length ? hydrateTTSWorkflow({ scenes: { [scene.scene_mark_id]: sceneState }, active_scene_id: scene.scene_mark_id }, prev.shot, prev.sceneItems).providerPrompts : defaultTTSPrompts(scene), candidates: (sceneState.candidates || []).map(ttsCandidateFromEvent), final: sceneState.final || null, error: "" } : prev);
  }
  function updateTTSPrompt(workflowId, index, patch) {
    updateAssetTTSWorkflow(workflowId, (prev) => prev ? { ...prev, providerPrompts: prev.providerPrompts.map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item) } : prev);
  }
  function updateShotTTSPrompt(workflowId, index, patch) {
    updateAssetTTSWorkflow(workflowId, (prev) => prev ? { ...prev, shotProviderPrompts: (prev.shotProviderPrompts || []).map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item) } : prev);
  }
  function shotTTSScenePlan(wf) {
    return (wf?.sceneItems || []).map((scene) => ({ scene_mark_id: scene.scene_mark_id, srt_text: scene.srt_text || "", image: scene.image || "", planned_duration: scene.planned_duration || null }));
  }
  async function refineShotTTSPrompt(workflowId, index, values = null) {
    const wf = assetTTSWorkflows()[workflowId];
    const item = wf?.shotProviderPrompts?.[index] ? { ...wf.shotProviderPrompts[index], ...(values || {}) } : null;
    if (!wf || !item?.userInstruction?.trim()) return;
    updateShotTTSPrompt(workflowId, index, { refining: true, error: "" });
    const requestId = `shot_tts_refine_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    emitDebugRequested(debugContext({ family: "prompt_refine", request_id: requestId, workflow_id: wf.workflowId, provider: item.provider, model: item.model }));
    try {
      const res = await api.refineShotTTSPrompt(selectedTaskId(), { request_id: requestId, workflow_id: wf.workflowId, shot_id: wf.shot?.shot_id, provider: item.provider, tts_model: item.model, voice_id: item.voiceId, srt_text: shotTTSFullText(wf.sceneItems), current_prompt: item.currentPrompt, user_instruction: item.userInstruction, target_duration: Number(wf.targetDuration || 0) || null, scene_plan: shotTTSScenePlan(wf) });
      updateShotTTSPrompt(workflowId, index, { currentPrompt: res.prompt || item.currentPrompt, refining: false });
      emitDebugCompleted(debugContext({ family: "prompt_refine", request_id: requestId, workflow_id: wf.workflowId, provider: item.provider, model: item.model, prompt_preview: res.prompt || item.currentPrompt, payload: res }));
    } catch (err) {
      updateShotTTSPrompt(workflowId, index, { refining: false, error: err instanceof Error ? err.message : String(err) });
      emitDebugError(err, debugContext({ family: "prompt_refine", request_id: requestId, workflow_id: wf.workflowId, provider: item.provider, model: item.model }));
    }
  }
  async function refineTTSPrompt(workflowId, index, values = null) {
    const wf = assetTTSWorkflows()[workflowId];
    const scene = wf?.sceneItems?.find((item) => item.scene_mark_id === wf.activeSceneId);
    const item = wf?.providerPrompts?.[index] ? { ...wf.providerPrompts[index], ...(values || {}) } : null;
    if (!wf || !scene || !item?.userInstruction?.trim()) return;
    updateTTSPrompt(workflowId, index, { refining: true, error: "" });
    const requestId = `tts_refine_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    emitDebugRequested(debugContext({ family: "prompt_refine", request_id: requestId, workflow_id: wf.workflowId, provider: item.provider, model: item.model }));
    try {
      const res = await api.refineAssetTTSPrompt(selectedTaskId(), { request_id: requestId, workflow_id: wf.workflowId, shot_id: wf.shot?.shot_id, scene_mark_id: scene.scene_mark_id, provider: item.provider, tts_model: item.model, voice_id: item.voiceId, srt_text: scene.srt_text || item.text, current_prompt: item.currentPrompt, user_instruction: item.userInstruction });
      updateTTSPrompt(workflowId, index, { currentPrompt: res.prompt || item.currentPrompt, refining: false });
      emitDebugCompleted(debugContext({ family: "prompt_refine", request_id: requestId, workflow_id: wf.workflowId, provider: item.provider, model: item.model, prompt_preview: res.prompt || item.currentPrompt, payload: res }));
    } catch (err) {
      updateTTSPrompt(workflowId, index, { refining: false, error: err instanceof Error ? err.message : String(err) });
      emitDebugError(err, debugContext({ family: "prompt_refine", request_id: requestId, workflow_id: wf.workflowId, provider: item.provider, model: item.model }));
    }
  }
  function ttsCandidateFromEvent(event) {
    return { candidateId: event.candidate_id || event.candidateId, provider: event.provider, model: event.model, voiceId: event.voice_id || event.voiceId, output: event.output, outputPath: event.output_path, src: rebuildAssetUrl(event.output), durationSeconds: event.duration_seconds ?? event.duration ?? 0, elapsedSeconds: event.elapsed_seconds, status: event.type === "failed" ? "failed" : event.status || "completed", error: event.detail || "" };
  }
  async function generateTTSForScene(workflowId) {
    const wf = assetTTSWorkflows()[workflowId];
    const scene = wf?.sceneItems?.find((item) => item.scene_mark_id === wf.activeSceneId);
    if (!wf || !scene) return;
    const cards = Array.from(document.querySelectorAll(`.ocrebuild-tts-dialog[data-workflow-id="${CSS.escape(workflowId)}"] .ocrebuild-tts-model-card`));
    const prompts = wf.providerPrompts.map((item, index) => ({ ...item, model: cards[index]?.querySelector("select.ocrebuild-tts-model-select")?.value || item.model, voiceId: cards[index]?.querySelector("select.ocrebuild-tts-voice-select")?.value || item.voiceId, userInstruction: cards[index]?.querySelector("input")?.value ?? item.userInstruction, currentPrompt: cards[index]?.querySelector("textarea")?.value ?? item.currentPrompt }));
    updateAssetTTSWorkflow(workflowId, (prev) => prev ? { ...prev, providerPrompts: prompts, phase: "generating", candidates: [], final: null, error: "" } : prev);
    emitDebugRequested(debugContext({ family: "asset_tts", workflow_id: wf.workflowId, request_id: `tts_${wf.workflowId}_${Date.now()}` }));
    await runAction(`asset-tts-${workflowId}`, async () => {
      await api.streamCompareAssetTTS(selectedTaskId(), { workflow_id: wf.workflowId, shot_id: wf.shot?.shot_id, scene_mark_id: scene.scene_mark_id, srt_text: scene.srt_text || "", prompts: prompts.filter((item) => item.provider && item.model && item.voiceId).map((item) => ({ provider: item.provider, model: item.model, voice_id: item.voiceId, prompt: item.currentPrompt || "", text: scene.srt_text || item.text || "", user_instruction: item.userInstruction || "" })) }, (event) => {
        emitStreamDebug(event, { family: "asset_tts", workflow_id: wf.workflowId });
        if (event.type === "completed" || event.type === "failed") {
          const candidate = ttsCandidateFromEvent(event);
          updateAssetTTSWorkflow(workflowId, (prev) => prev ? { ...prev, candidates: [...prev.candidates.filter((item) => item.candidateId !== candidate.candidateId), candidate] } : prev);
        }
        if (event.type === "round_completed") updateAssetTTSWorkflow(workflowId, (prev) => prev ? { ...prev, phase: "select" } : prev);
      });
    });
  }
  async function generateTTSForShot(workflowId, promptIndex = null) {
    const wf = assetTTSWorkflows()[workflowId];
    if (!wf) return;
    const cards = Array.from(document.querySelectorAll(`.ocrebuild-tts-dialog[data-workflow-id="${CSS.escape(workflowId)}"] .ocrebuild-shot-tts-model-card`));
    const targetDuration = Number(document.querySelector(`.ocrebuild-tts-dialog[data-workflow-id="${CSS.escape(workflowId)}"] .ocrebuild-shot-tts-duration-input`)?.value || wf.targetDuration || 0) || null;
    const fullText = shotTTSFullText(wf.sceneItems);
    const prompts = (wf.shotProviderPrompts || []).map((item, index) => ({ ...item, model: cards[index]?.querySelector("select.ocrebuild-tts-model-select")?.value || item.model, voiceId: cards[index]?.querySelector("select.ocrebuild-tts-voice-select")?.value || item.voiceId, userInstruction: cards[index]?.querySelector("input")?.value ?? item.userInstruction, currentPrompt: cards[index]?.querySelector("textarea")?.value ?? item.currentPrompt, text: fullText }));
    const activePrompts = promptIndex === null ? prompts : prompts.filter((_item, index) => index === promptIndex);
    updateAssetTTSWorkflow(workflowId, (prev) => prev ? { ...prev, shotProviderPrompts: prompts, targetDuration, phase: "shot_generating", shotCandidates: promptIndex === null ? [] : (prev.shotCandidates || []), shotFinal: promptIndex === null ? null : prev.shotFinal, lockedTimeline: promptIndex === null ? null : prev.lockedTimeline, error: "" } : prev);
    emitDebugRequested(debugContext({ family: "asset_tts", workflow_id: wf.workflowId, request_id: `shot_tts_${wf.workflowId}_${Date.now()}` }));
    await runAction(`shot-tts-${workflowId}`, async () => {
      await api.streamShotTTS(selectedTaskId(), { workflow_id: wf.workflowId, shot_id: wf.shot?.shot_id, target_duration: targetDuration, scene_plan: shotTTSScenePlan(wf), prompts: activePrompts.filter((item) => item.provider && item.model && item.voiceId && fullText).map((item) => ({ provider: item.provider, model: item.model, voice_id: item.voiceId, prompt: item.currentPrompt || "", text: fullText, user_instruction: item.userInstruction || "" })) }, (event) => {
        emitStreamDebug(event, { family: "asset_tts", workflow_id: wf.workflowId });
        if (event.type === "completed" || event.type === "failed") {
          const candidate = ttsCandidateFromEvent(event);
          updateAssetTTSWorkflow(workflowId, (prev) => prev ? { ...prev, shotCandidates: [...(prev.shotCandidates || []).filter((item) => item.candidateId !== candidate.candidateId), candidate] } : prev);
        }
        if (event.type === "round_completed") updateAssetTTSWorkflow(workflowId, (prev) => prev ? { ...prev, phase: "shot_select" } : prev);
      });
    });
  }
  async function finalizeShotTTSCandidate(workflowId, candidate) {
    const wf = assetTTSWorkflows()[workflowId];
    if (!wf || !candidate) return;
    const targetDuration = Number(document.querySelector(`.ocrebuild-tts-dialog[data-workflow-id="${CSS.escape(workflowId)}"] .ocrebuild-shot-tts-duration-input`)?.value || wf.targetDuration || 0) || candidate.durationSeconds || null;
    try {
      const res = await api.finalizeShotTTS(selectedTaskId(), { workflow_id: workflowId, shot_id: wf.shot?.shot_id, selected_output: candidate.output, provider: candidate.provider, model: candidate.model, voice_id: candidate.voiceId, target_duration: targetDuration, duration: candidate.durationSeconds, scene_plan: shotTTSScenePlan(wf) });
      updateAssetTTSWorkflow(workflowId, (prev) => prev ? { ...prev, phase: "shot_finalized", targetDuration, shotFinal: { selected_output: candidate.output, locked_audio: res.locked_audio, timeline: res.timeline, srt: res.srt, provider: candidate.provider, model: candidate.model, voice_id: candidate.voiceId, duration: res.duration, raw_duration: res.raw_duration, speed_factor: res.speed_factor, stretched: res.stretched }, lockedTimeline: res.locked_timeline || null, shotVideo: defaultShotTTSVideoState(prev.shot, res.locked_timeline || null, prev.shotVideo || null) } : prev);
    } catch (err) {
      updateAssetTTSWorkflow(workflowId, (prev) => prev ? { ...prev, error: err instanceof Error ? err.message : String(err) } : prev);
    }
  }
  function updateShotTTSVideo(workflowId, patch) {
    updateAssetTTSWorkflow(workflowId, (prev) => prev ? { ...prev, shotVideo: { ...(prev.shotVideo || defaultShotTTSVideoState(prev.shot, prev.lockedTimeline || null)), ...patch } } : prev);
  }
  function updateShotTTSVideoModel(workflowId, key) {
    const model = shotMultiReferenceModels().find((item) => item.key === key);
    updateAssetTTSWorkflow(workflowId, (prev) => {
      if (!prev || !model) return prev;
      const scenePlan = shotMultiReferenceImages(prev.shot, prev.lockedTimeline || null);
      const duration = prev.lockedTimeline?.duration || shotMultiReferenceDuration(prev.shot, model);
      return { ...prev, shotVideo: { ...(prev.shotVideo || {}), model, provider: model.provider, modelId: model.model, duration, scenePlan, referenceImages: scenePlan.map((item) => item.image), error: "" } };
    });
  }
  function readShotTTSVideoForm(workflowId, wf) {
    const video = wf?.shotVideo || defaultShotTTSVideoState(wf?.shot, wf?.lockedTimeline || null);
    const dialog = document.querySelector(`.ocrebuild-tts-dialog[data-workflow-id="${CSS.escape(workflowId)}"]`);
    const modelKey = dialog?.querySelector(".ocrebuild-shot-tts-video-model-select")?.value || `${video.provider}/${video.modelId}`;
    const model = shotMultiReferenceModels().find((item) => item.key === modelKey) || video.model;
    return {
      model,
      provider: model?.provider || video.provider,
      modelId: model?.model || video.modelId,
      duration: Number(dialog?.querySelector(".ocrebuild-shot-tts-video-duration-input")?.value || video.duration || 0) || video.duration,
      variantCount: Number(dialog?.querySelector(".ocrebuild-shot-tts-video-count-select")?.value || video.variantCount || 1) || 1,
      simplePrompt: String(dialog?.querySelector(".ocrebuild-shot-tts-video-simple-input")?.value || video.simplePrompt || "").trim(),
      currentPrompt: String(dialog?.querySelector(".ocrebuild-shot-tts-video-prompt-editor")?.value || video.currentPrompt || "").trim(),
    };
  }
  function effectiveShotTTSVideoImages(wf) {
    const video = wf?.shotVideo || {};
    const fromWorkflow = (video.referenceImages || []).filter(Boolean);
    if (fromWorkflow.length) return fromWorkflow;
    const fromScenePlan = (video.scenePlan || []).map((item) => item?.image).filter(Boolean);
    if (fromScenePlan.length) return fromScenePlan;
    return shotMultiReferenceImages(wf?.shot, wf?.lockedTimeline || null).map((item) => item.image).filter(Boolean);
  }
  function validateShotTTSVideoForm(wf, values, mode) {
    if (!values.provider || !values.modelId) return "请选择视频模型";
    if (!effectiveShotTTSVideoImages(wf).length) return "没有可用参考图";
    if (!Number.isFinite(Number(values.duration)) || Number(values.duration) <= 0) return "Duration 必须大于 0";
    if (!Number.isFinite(Number(values.variantCount)) || Number(values.variantCount) < 1 || Number(values.variantCount) > 3) return "生成数量必须是 1、2 或 3";
    if (mode === "refine" && !values.simplePrompt) return "请先输入视频简单提示词";
    if (mode === "generate" && !values.currentPrompt) return "请先填写视频最终提示词";
    return "";
  }
  async function refineShotTTSVideoPrompt(workflowId) {
    const wf = assetTTSWorkflows()[workflowId];
    if (!wf) return;
    const video = wf.shotVideo || defaultShotTTSVideoState(wf.shot, wf.lockedTimeline || null);
    const values = readShotTTSVideoForm(workflowId, wf);
    const validationError = validateShotTTSVideoForm(wf, values, "refine");
    if (validationError) { updateShotTTSVideo(workflowId, { error: validationError }); return; }
    updateShotTTSVideo(workflowId, { ...values, refining: true, error: "" });
    const requestId = `shot_tts_video_refine_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    try {
      const res = await api.refineShotMultiReferencePrompt(selectedTaskId(), { request_id: requestId, workflow_id: video.workflowId, shot_id: wf.shot?.shot_id, provider: values.provider, video_model: values.modelId, current_prompt: values.currentPrompt, user_instruction: values.simplePrompt, duration: values.duration, reference_images: effectiveShotTTSVideoImages(wf), scene_plan: video.scenePlan || [] });
      updateShotTTSVideo(workflowId, { currentPrompt: res.prompt || values.currentPrompt, refining: false });
    } catch (err) {
      updateShotTTSVideo(workflowId, { refining: false, error: err instanceof Error ? err.message : String(err) });
    }
  }
  async function generateShotTTSVideo(workflowId) {
    const wf = assetTTSWorkflows()[workflowId];
    if (!wf) return;
    const video = wf.shotVideo || defaultShotTTSVideoState(wf.shot, wf.lockedTimeline || null);
    const values = readShotTTSVideoForm(workflowId, wf);
    const validationError = validateShotTTSVideoForm(wf, values, "generate");
    if (validationError) { updateShotTTSVideo(workflowId, { error: validationError }); return; }
    const referenceImages = effectiveShotTTSVideoImages(wf);
    updateShotTTSVideo(workflowId, { ...values, referenceImages, phase: "generating", candidates: [], error: "" });
    setBusy(`shot-r2v-${workflowId}`);
    try {
      await api.streamShotMultiReferenceVideo(selectedTaskId(), { workflow_id: video.workflowId, shot_id: wf.shot?.shot_id, provider: values.provider, model: values.modelId, prompt: values.currentPrompt, duration: values.duration, variant_count: values.variantCount, reference_images: referenceImages, scene_plan: video.scenePlan || [] }, (event) => {
        emitStreamDebug(event, { family: "shot_video", workflow_id: video.workflowId, provider: values.provider, model: values.modelId });
        if (event.type === "completed" || event.type === "failed") {
          const candidate = videoCandidateFromEvent(event);
          updateAssetTTSWorkflow(workflowId, (prev) => prev ? { ...prev, shotVideo: { ...(prev.shotVideo || {}), candidates: [...(prev.shotVideo?.candidates || []).filter((item) => item.candidateId !== candidate.candidateId), candidate] } } : prev);
        }
        if (event.type === "round_completed") updateShotTTSVideo(workflowId, { phase: "select" });
      });
    } catch (err) {
      updateShotTTSVideo(workflowId, { phase: "edit", error: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy("");
    }
  }
  async function finalizeShotTTSVideoCandidate(workflowId, candidate) {
    const wf = assetTTSWorkflows()[workflowId];
    const video = wf?.shotVideo;
    if (!wf || !video || !candidate) return;
    updateShotTTSVideo(workflowId, { finalizing: true, error: "" });
    try {
      await api.finalizeShotMultiReferenceVideo(selectedTaskId(), { workflow_id: video.workflowId, shot_id: wf.shot?.shot_id, selected_output: candidate.output, provider: candidate.provider, model: candidate.model, duration: candidate.duration || video.duration });
      updateShotTTSVideo(workflowId, { phase: "finalized", finalizing: false, final: { selected_output: candidate.output, provider: candidate.provider, model: candidate.model, duration: candidate.duration || video.duration } });
    } catch (err) {
      updateShotTTSVideo(workflowId, { finalizing: false, error: err instanceof Error ? err.message : String(err) });
    }
  }
  async function finalizeTTSCandidate(workflowId, candidate) {
    const wf = assetTTSWorkflows()[workflowId];
    const scene = wf?.sceneItems?.find((item) => item.scene_mark_id === wf.activeSceneId);
    if (!wf || !scene || !candidate) return;
    try {
      await api.finalizeCompareAssetTTS(selectedTaskId(), { workflow_id: workflowId, shot_id: wf.shot?.shot_id, scene_mark_id: scene.scene_mark_id, selected_output: candidate.output, provider: candidate.provider, model: candidate.model, voice_id: candidate.voiceId, duration: candidate.durationSeconds });
      updateAssetTTSWorkflow(workflowId, (prev) => prev ? { ...prev, phase: "finalized", final: { selected_output: candidate.output, provider: candidate.provider, model: candidate.model, voice_id: candidate.voiceId, duration: candidate.durationSeconds }, scenes: { ...prev.scenes, [scene.scene_mark_id]: { ...(prev.scenes?.[scene.scene_mark_id] || {}), final: { selected_output: candidate.output, provider: candidate.provider, model: candidate.model, voice_id: candidate.voiceId, duration: candidate.durationSeconds }, tts_duration: candidate.durationSeconds, candidates: prev.candidates } }, sceneItems: prev.sceneItems.map((item) => item.scene_mark_id === scene.scene_mark_id ? { ...item, tts_duration: candidate.durationSeconds, final: { selected_output: candidate.output, provider: candidate.provider, model: candidate.model, voice_id: candidate.voiceId, duration: candidate.durationSeconds } } : item) } : prev);
    } catch (err) {
      updateAssetTTSWorkflow(workflowId, (prev) => prev ? { ...prev, error: err instanceof Error ? err.message : String(err) } : prev);
    }
  }
  function assetImageBasePrompt(assetTask) {
    const prompt = String(assetTask?.input?.prompt || "").trim();
    const negative = String(assetTask?.params?.negative_prompt || "").trim();
    return negative ? `${prompt}\n\nNegative prompt: ${negative}` : prompt;
  }
  function sceneDurationForPackage(pkg, videoTask = null) {
    const taskDuration = Number(videoTask?.params?.duration ?? 0);
    if (taskDuration > 0) return Number(taskDuration.toFixed(3));
    const shot = shotPlanShots().find((item) => item?.shot_id === pkg?.shot_id) || selectedShot();
    const mark = (editableSceneMarks(shot) || []).find((item) => String(item?.scene_mark_id || "") === String(pkg?.scene_mark_id || ""));
    const markDuration = Number(mark?.duration ?? 0);
    if (markDuration > 0) return Number(markDuration.toFixed(3));
    const pkgDuration = Number(pkg?.duration ?? 0);
    if (pkgDuration > 0) return Number(pkgDuration.toFixed(3));
    const shotDuration = Number(shot?.duration ?? 0);
    return shotDuration > 0 ? Number(shotDuration.toFixed(3)) : null;
  }
  function videoPromptForProvider(pkg, provider) {
    const prompts = pkg?.video_prompts || {};
    if (provider === "gemini") return prompts.veo_prompt || prompts.base_video_prompt || prompts.transition_video_prompt || "";
    if (provider === "openai") return prompts.sora_prompt || prompts.base_video_prompt || prompts.transition_video_prompt || "";
    if (provider === "xai") return prompts.grok_prompt || prompts.base_video_prompt || prompts.transition_video_prompt || "";
    if (provider === "wan") return prompts.wan_prompt || prompts.base_video_prompt || prompts.transition_video_prompt || "";
    return prompts.base_video_prompt || prompts.transition_video_prompt || "";
  }
  function providerVideoModels(providerConfig, inputMode) {
    return (providerConfig?.models || []).filter((model) => (model.input_modes || ["text", "first_frame"]).includes(inputMode));
  }
  function durationStatus(model, duration) {
    const meta = model?.duration || {};
    if (!duration) return { ok: true, label: meta.note || "未知 scene 时长，无法判断" };
    if (meta.min && duration < Number(meta.min)) return { ok: false, label: `当前 ${duration}s，模型最短 ${meta.min}s` };
    if (meta.max && duration > Number(meta.max)) return { ok: false, label: `当前 ${duration}s，模型最长 ${meta.max}s` };
    return { ok: true, label: meta.note || `当前 ${duration}s 可用` };
  }
  function hydrateVideoWorkflow(stored, menu, base) {
    const prompts = (stored?.prompts || []).map((item) => ({ provider: item.provider, providerLabel: item.provider, model: item.model, models: [], currentPrompt: item.current_prompt || item.prompt || "", userInstruction: item.user_instruction || "", duration: item.duration || base.duration, refining: false, error: "" }));
    const candidates = (stored?.candidates || []).map((item) => ({ candidateId: item.candidate_id || item.candidateId, provider: item.provider, model: item.model, output: item.output, outputPath: item.output_path, src: rebuildAssetUrl(item.output), elapsedSeconds: item.elapsed_seconds, duration: item.duration, status: item.status || "completed", error: item.detail || "" }));
    return { workflowId: stored.workflow_id, menu, phase: stored.phase || (prompts.length ? "prompts" : "mode"), inputMode: stored.input_mode || base.inputMode, duration: stored.duration || base.duration, providerPrompts: prompts, candidates, final: stored.final || null, error: "" };
  }
  async function openAssetVideoWorkflow(menu) {
    if (!menu?.pkg) return;
    setAssetVideoContextMenu(null);
    const workflowId = normalizeWorkflowId(`${menu.pkg.scene_mark_id || menu.pkg.shot_id}_video`);
    const inputMode = menu.pkg?.mode === "first_last" ? "first_last" : "first_frame";
    const duration = sceneDurationForPackage(menu.pkg, menu.videoTask);
    updateVideoExpandedSteps(workflowId, defaultVideoExpandedSteps());
    updateAssetVideoWorkflow(workflowId, { workflowId, menu, phase: "mode", inputMode, duration, providerPrompts: [], candidates: [], final: null, error: "" });
    await ensureVideoModelConfig();
    try {
      const saved = await api.getAssetVideoWorkflow(selectedTaskId(), workflowId);
      if (saved?.exists && saved.workflow) updateAssetVideoWorkflow(workflowId, hydrateVideoWorkflow(saved.workflow, menu, { inputMode, duration }));
    } catch (err) {
      updateAssetVideoWorkflow(workflowId, (prev) => prev ? { ...prev, error: err instanceof Error ? err.message : String(err) } : prev);
    }
    window.dispatchEvent(new CustomEvent("opencrew:debug-session", { detail: { sessionId: task()?.session_id || null, reset: true } }));
  }
  async function startAssetVideoMode(workflowId, inputMode) {
    const wf = assetVideoWorkflows()[workflowId];
    if (!wf) return;
    const config = await ensureVideoModelConfig();
    const providers = (config?.providers || []).filter((item) => ["gemini", "openai", "xai", "wan"].includes(String(item.provider)) && item.enabled && item.has_api_key);
    const prompts = providers.map((provider) => {
      const models = providerVideoModels(provider, inputMode);
      const model = models.find((item) => item.model === provider.model) || models[0] || provider.models?.[0] || {};
      return { provider: provider.provider, providerLabel: provider.provider_label || provider.provider, model: model.model || provider.model, models, currentPrompt: videoPromptForProvider(wf.menu?.pkg, provider.provider), userInstruction: "", duration: wf.duration, refining: false, error: "" };
    }).filter((item) => item.model && item.currentPrompt);
    updateVideoExpandedSteps(workflowId, { mode: false, prompts: false, results: false });
    updateAssetVideoWorkflow(workflowId, (prev) => prev ? { ...prev, inputMode, phase: "prompts", providerPrompts: prompts, candidates: [], final: null } : prev);
  }
  function updateVideoPrompt(workflowId, index, patch) {
    updateAssetVideoWorkflow(workflowId, (prev) => prev ? { ...prev, providerPrompts: prev.providerPrompts.map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item) } : prev);
  }
  async function refineVideoPrompt(workflowId, index, values = null) {
    const wf = assetVideoWorkflows()[workflowId];
    const item = wf?.providerPrompts?.[index] ? { ...wf.providerPrompts[index], ...(values || {}) } : null;
    if (!wf || !item?.userInstruction?.trim()) return;
    updateVideoPrompt(workflowId, index, { refining: true, error: "" });
    const requestId = `video_refine_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    emitDebugRequested(debugContext({ family: "prompt_refine", request_id: requestId, workflow_id: wf.workflowId, provider: item.provider, model: item.model }));
    try {
      const res = await api.refineAssetVideoPrompt(selectedTaskId(), { request_id: requestId, workflow_id: wf.workflowId, provider: item.provider, video_model: item.model, input_mode: wf.inputMode, current_prompt: item.currentPrompt, user_instruction: item.userInstruction, duration: item.duration || wf.duration });
      updateVideoPrompt(workflowId, index, { currentPrompt: res.prompt || item.currentPrompt, refining: false });
      emitDebugCompleted(debugContext({ family: "prompt_refine", request_id: requestId, workflow_id: wf.workflowId, provider: item.provider, model: item.model, prompt_preview: res.prompt || item.currentPrompt, payload: res }));
    } catch (err) {
      updateVideoPrompt(workflowId, index, { refining: false, error: err instanceof Error ? err.message : String(err) });
      emitDebugError(err, debugContext({ family: "prompt_refine", request_id: requestId, workflow_id: wf.workflowId, provider: item.provider, model: item.model }));
    }
  }
  function videoCandidateFromEvent(event) {
    return { candidateId: event.candidate_id, provider: event.provider, model: event.model, output: event.output, outputPath: event.output_path, src: rebuildAssetUrl(event.output), elapsedSeconds: event.elapsed_seconds, duration: event.duration, status: event.type === "failed" ? "failed" : "completed", error: event.detail || "" };
  }
  async function generateCompareVideos(workflowId) {
    const wf = assetVideoWorkflows()[workflowId];
    if (!wf) return;
    const cards = Array.from(document.querySelectorAll(`.ocrebuild-video-dialog[data-workflow-id="${CSS.escape(workflowId)}"] .ocrebuild-compare-card`));
    const prompts = wf.providerPrompts.map((item, index) => ({ ...item, model: cards[index]?.querySelector("select")?.value || item.model, userInstruction: cards[index]?.querySelector("input")?.value ?? item.userInstruction, currentPrompt: cards[index]?.querySelector("textarea")?.value ?? item.currentPrompt, duration: Number(cards[index]?.querySelector(".ocrebuild-video-duration-input")?.value || item.duration || wf.duration || 0) || wf.duration }));
    updateVideoExpandedSteps(workflowId, (prev) => ({ ...prev, prompts: false, results: false }));
    updateAssetVideoWorkflow(workflowId, (prev) => prev ? { ...prev, phase: "generating", providerPrompts: prompts, candidates: [], error: "" } : prev);
    emitDebugRequested(debugContext({ family: "asset_video", workflow_id: wf.workflowId, request_id: `video_${wf.workflowId}_${Date.now()}` }));
    await runAction(`asset-video-compare-${workflowId}`, async () => {
      await api.streamCompareAssetVideos(selectedTaskId(), { workflow_id: wf.workflowId, shot_id: wf.menu.pkg.shot_id, scene_mark_id: wf.menu.pkg.scene_mark_id || "", input_mode: wf.inputMode, first_image: wf.menu.videoTask?.input?.first_image || "", last_image: wf.menu.videoTask?.input?.last_image || "", duration: wf.duration, prompts: prompts.filter((item) => item.currentPrompt?.trim()).map((item) => ({ provider: item.provider, model: item.model, prompt: item.currentPrompt, user_instruction: item.userInstruction || "", duration: item.duration || wf.duration })) }, (event) => {
        emitStreamDebug(event, { family: "asset_video", workflow_id: wf.workflowId });
        if (event.type === "completed" || event.type === "failed") {
          const candidate = videoCandidateFromEvent(event);
          updateAssetVideoWorkflow(workflowId, (prev) => prev ? { ...prev, candidates: [...prev.candidates.filter((item) => item.candidateId !== candidate.candidateId), candidate] } : prev);
        }
        if (event.type === "round_completed") {
          updateVideoExpandedSteps(workflowId, (prev) => ({ ...prev, results: true }));
          updateAssetVideoWorkflow(workflowId, (prev) => prev ? { ...prev, phase: "select" } : prev);
        }
      });
    });
  }
  async function finalizeVideoCandidate(workflowId, candidate) {
    const wf = assetVideoWorkflows()[workflowId];
    if (!wf || !candidate) return;
    updateAssetVideoWorkflow(workflowId, (prev) => prev ? { ...prev, finalizing: true, error: "" } : prev);
    try {
      await api.finalizeCompareAssetVideo(selectedTaskId(), { workflow_id: workflowId, shot_id: wf.menu.pkg.shot_id, scene_mark_id: wf.menu.pkg.scene_mark_id || "", selected_output: candidate.output, provider: candidate.provider, model: candidate.model, duration: candidate.duration || wf.duration });
      await loadAssetTasks({ force: true });
      updateAssetVideoWorkflow(workflowId, (prev) => prev ? { ...prev, phase: "finalized", finalizing: false, final: { selected_output: candidate.output, provider: candidate.provider, model: candidate.model, duration: candidate.duration || wf.duration } } : prev);
    } catch (err) {
      updateAssetVideoWorkflow(workflowId, (prev) => prev ? { ...prev, finalizing: false, error: err instanceof Error ? err.message : String(err) } : prev);
    }
  }
  async function openShotMultiReferenceWorkflow() {
    const shot = selectedShot();
    if (!shot) return;
    setError("");
    await ensureVideoModelConfig();
    const workflowId = shotMultiReferenceWorkflowId(shot);
    let lockedTimeline = null;
    try {
      const ttsWorkflowId = shotTTSWorkflowId(shot);
      const savedTTS = await api.getShotTTSWorkflow(selectedTaskId(), ttsWorkflowId);
      lockedTimeline = savedTTS?.workflow?.shot?.locked_timeline || null;
    } catch (_err) {
      lockedTimeline = null;
    }
    const scenePlan = shotMultiReferenceImages(shot, lockedTimeline);
    const model = defaultShotMultiReferenceModel();
    const duration = lockedTimeline?.duration || shotMultiReferenceDuration(shot, model);
    const currentPrompt = shotMultiReferenceBasePrompt(shot, scenePlan);
    const base = { workflowId, shot, model, provider: model?.provider || "", modelId: model?.model || "", duration, variantCount: 1, simplePrompt: "", currentPrompt, referenceImages: scenePlan.map((item) => item.image), scenePlan, candidates: [], final: null, phase: "edit", refining: false, error: "" };
    updateShotMultiReferenceWorkflow(workflowId, base);
    try {
      const saved = await api.getShotMultiReferenceWorkflow(selectedTaskId(), workflowId);
      if (saved?.exists && saved.workflow) updateShotMultiReferenceWorkflow(workflowId, hydrateShotMultiReferenceWorkflow(saved.workflow, shot, base));
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      if (!/not found/i.test(message)) updateShotMultiReferenceWorkflow(workflowId, (prev) => prev ? { ...prev, error: message } : prev);
    }
    window.dispatchEvent(new CustomEvent("opencrew:debug-session", { detail: { sessionId: task()?.session_id || null, reset: true } }));
  }
  function updateShotMultiReferenceModel(workflowId, key) {
    const model = shotMultiReferenceModels().find((item) => item.key === key);
    updateShotMultiReferenceWorkflow(workflowId, (prev) => prev && model ? { ...prev, model, provider: model.provider, modelId: model.model, duration: shotMultiReferenceDuration(prev.shot, model), error: "" } : prev);
  }
  function readShotMultiReferenceForm(workflowId, wf) {
    const dialog = document.querySelector(`.ocrebuild-shot-r2v-dialog[data-workflow-id="${CSS.escape(workflowId)}"]`);
    const modelKey = dialog?.querySelector(".ocrebuild-shot-r2v-model-select")?.value || `${wf.provider}/${wf.modelId}`;
    const model = shotMultiReferenceModels().find((item) => item.key === modelKey) || wf.model;
    return {
      model,
      provider: model?.provider || wf.provider,
      modelId: model?.model || wf.modelId,
      duration: Number(dialog?.querySelector(".ocrebuild-shot-r2v-duration-input")?.value || wf.duration || 0) || wf.duration,
      variantCount: Number(dialog?.querySelector(".ocrebuild-shot-r2v-count-select")?.value || wf.variantCount || 1) || 1,
      simplePrompt: String(dialog?.querySelector(".ocrebuild-shot-r2v-simple-input")?.value || wf.simplePrompt || "").trim(),
      currentPrompt: String(dialog?.querySelector(".ocrebuild-shot-r2v-prompt-editor")?.value || wf.currentPrompt || "").trim(),
    };
  }
  function effectiveShotMultiReferenceImages(wf) {
    const fromWorkflow = (wf?.referenceImages || []).filter(Boolean);
    if (fromWorkflow.length) return fromWorkflow;
    const fromScenePlan = (wf?.scenePlan || []).map((item) => item?.image).filter(Boolean);
    if (fromScenePlan.length) return fromScenePlan;
    return shotMultiReferenceImages(wf?.shot).map((item) => item.image).filter(Boolean);
  }
  async function refreshShotMultiReferenceImages(workflowId) {
    const wf = shotMultiReferenceWorkflows()[workflowId];
    if (!wf) return;
    await loadAssetTasks({ force: true });
    const scenePlan = shotMultiReferenceImages(wf.shot);
    updateShotMultiReferenceWorkflow(workflowId, (prev) => prev ? { ...prev, scenePlan, referenceImages: scenePlan.map((item) => item.image), error: scenePlan.length ? "" : "没有可用参考图" } : prev);
  }
  function validateShotMultiReferenceForm(workflowId, wf, values, mode) {
    if (!values.provider || !values.modelId) return "请选择视频模型";
    if (!effectiveShotMultiReferenceImages(wf).length) return "没有可用参考图";
    if (!Number.isFinite(Number(values.duration)) || Number(values.duration) <= 0) return "Duration 必须大于 0";
    if (!Number.isFinite(Number(values.variantCount)) || Number(values.variantCount) < 1 || Number(values.variantCount) > 3) return "生成数量必须是 1、2 或 3";
    if (mode === "refine" && !values.simplePrompt) return "请先输入简单提示词";
    if (mode === "generate" && !values.currentPrompt) return "请先填写复杂提示词";
    return "";
  }
  async function refineShotMultiReferencePrompt(workflowId) {
    const wf = shotMultiReferenceWorkflows()[workflowId];
    if (!wf) return;
    const values = readShotMultiReferenceForm(workflowId, wf);
    const validationError = validateShotMultiReferenceForm(workflowId, wf, values, "refine");
    if (validationError) { updateShotMultiReferenceWorkflow(workflowId, (prev) => prev ? { ...prev, error: validationError } : prev); return; }
    updateShotMultiReferenceWorkflow(workflowId, (prev) => prev ? { ...prev, ...values, refining: true, error: "" } : prev);
    try {
      const referenceImages = effectiveShotMultiReferenceImages(wf);
      const refinePayload = { request_id: `shot_r2v_refine_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`, workflow_id: wf.workflowId, shot_id: wf.shot?.shot_id, provider: values.provider, video_model: values.modelId, current_prompt: values.currentPrompt, user_instruction: values.simplePrompt, duration: values.duration, reference_images: referenceImages, scene_plan: wf.scenePlan };
      emitDebugRequested(debugContext({ family: "prompt_refine", request_id: refinePayload.request_id, workflow_id: wf.workflowId, provider: values.provider, model: values.modelId }));
      let res;
      try {
        res = await api.refineShotMultiReferencePrompt(selectedTaskId(), refinePayload);
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        if (!message.includes("Not Found")) throw err;
        res = await api.refineAssetVideoPrompt(selectedTaskId(), { request_id: refinePayload.request_id, workflow_id: refinePayload.workflow_id, provider: refinePayload.provider, video_model: refinePayload.video_model, input_mode: "multi_reference", current_prompt: refinePayload.current_prompt, user_instruction: `Shot-level multi-image reference workflow. Use the ordered reference images and scene/SRT plan already embedded in the current prompt. ${refinePayload.user_instruction}`, duration: refinePayload.duration });
      }
      updateShotMultiReferenceWorkflow(workflowId, (prev) => prev ? { ...prev, currentPrompt: res.prompt || prev.currentPrompt, refining: false } : prev);
      emitDebugCompleted(debugContext({ family: "prompt_refine", request_id: refinePayload.request_id, workflow_id: wf.workflowId, provider: values.provider, model: values.modelId, prompt_preview: res.prompt || values.currentPrompt, payload: res }));
    } catch (err) {
      updateShotMultiReferenceWorkflow(workflowId, (prev) => prev ? { ...prev, refining: false, error: err instanceof Error ? err.message : String(err) } : prev);
      emitDebugError(err, debugContext({ family: "prompt_refine", workflow_id: wf.workflowId, provider: values.provider, model: values.modelId }));
    }
  }
  async function generateShotMultiReferenceVideos(workflowId) {
    const wf = shotMultiReferenceWorkflows()[workflowId];
    if (!wf) return;
    const values = readShotMultiReferenceForm(workflowId, wf);
    const validationError = validateShotMultiReferenceForm(workflowId, wf, values, "generate");
    if (validationError) { updateShotMultiReferenceWorkflow(workflowId, (prev) => prev ? { ...prev, error: validationError } : prev); return; }
    const referenceImages = effectiveShotMultiReferenceImages(wf);
    updateShotMultiReferenceWorkflow(workflowId, (prev) => prev ? { ...prev, ...values, referenceImages, phase: "generating", candidates: [], error: "" } : prev);
    emitDebugRequested(debugContext({ family: "shot_video", workflow_id: wf.workflowId, request_id: `shot_video_${wf.workflowId}_${Date.now()}`, provider: values.provider, model: values.modelId }));
    setBusy(`shot-r2v-${workflowId}`);
    try {
      await api.streamShotMultiReferenceVideo(selectedTaskId(), { workflow_id: wf.workflowId, shot_id: wf.shot?.shot_id, provider: values.provider, model: values.modelId, prompt: values.currentPrompt, duration: values.duration, variant_count: values.variantCount, reference_images: referenceImages, scene_plan: wf.scenePlan }, (event) => {
        emitStreamDebug(event, { family: "shot_video", workflow_id: wf.workflowId, provider: values.provider, model: values.modelId });
        if (event.type === "completed" || event.type === "failed") {
          const candidate = videoCandidateFromEvent(event);
          updateShotMultiReferenceWorkflow(workflowId, (prev) => prev ? { ...prev, candidates: [...prev.candidates.filter((item) => item.candidateId !== candidate.candidateId), candidate] } : prev);
        }
        if (event.type === "round_completed") updateShotMultiReferenceWorkflow(workflowId, (prev) => prev ? { ...prev, phase: "select" } : prev);
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      const detail = /not found/i.test(message) ? "Shot 多图 R2V 生成接口暂不可用。请确认后端已重启并加载最新代码。" : message;
      updateShotMultiReferenceWorkflow(workflowId, (prev) => prev ? { ...prev, phase: "edit", error: detail } : prev);
      emitDebugError(detail, debugContext({ family: "shot_video", workflow_id: wf.workflowId, provider: values.provider, model: values.modelId }));
    } finally {
      setBusy("");
    }
  }
  async function finalizeShotMultiReferenceCandidate(workflowId, candidate) {
    const wf = shotMultiReferenceWorkflows()[workflowId];
    if (!wf || !candidate) return;
    updateShotMultiReferenceWorkflow(workflowId, (prev) => prev ? { ...prev, finalizing: true, error: "" } : prev);
    try {
      await api.finalizeShotMultiReferenceVideo(selectedTaskId(), { workflow_id: workflowId, shot_id: wf.shot?.shot_id, selected_output: candidate.output, provider: candidate.provider, model: candidate.model, duration: candidate.duration || wf.duration });
      updateShotMultiReferenceWorkflow(workflowId, (prev) => prev ? { ...prev, phase: "finalized", finalizing: false, final: { selected_output: candidate.output, provider: candidate.provider, model: candidate.model, duration: candidate.duration || wf.duration } } : prev);
    } catch (err) {
      updateShotMultiReferenceWorkflow(workflowId, (prev) => prev ? { ...prev, finalizing: false, error: err instanceof Error ? err.message : String(err) } : prev);
    }
  }
  async function openAssetCompareWorkflow(menu) {
    if (!menu?.pkg) return;
    const resolvedAssetTask = menu.assetTask || assetTaskByType(menu.pkg, menu.role === "last" ? ["image_regenerate_last"] : ["image_regenerate_first", "image_regenerate_single"]);
    setAssetImageContextMenu(null);
    const workflowId = sceneWorkflowId(menu.pkg, menu.role);
    const basePrompt = assetImageBasePrompt(resolvedAssetTask);
    updateCompareExpandedSteps(workflowId, defaultCompareExpandedSteps());
    updateAssetCompareWorkflow(workflowId, { workflowId, menu: { ...menu, assetTask: resolvedAssetTask }, phase: "mode", mode: "", round: 1, providerPrompts: [], candidates: [], selectedCandidate: null, refinePrompts: [], refineCandidates: [], variantCount: 3, finalizing: false, error: "", basePrompt });
    await ensureImageModelConfig();
    try {
      const saved = await api.getAssetImageWorkflow(selectedTaskId(), workflowId);
      if (saved?.exists && saved.workflow) updateAssetCompareWorkflow(workflowId, hydrateCompareWorkflow(saved.workflow, { ...menu, assetTask: resolvedAssetTask }, basePrompt));
    } catch (err) {
      updateAssetCompareWorkflow(workflowId, (prev) => prev ? { ...prev, error: err instanceof Error ? err.message : String(err) } : prev);
    }
    window.dispatchEvent(new CustomEvent("opencrew:debug-session", { detail: { sessionId: task()?.session_id || null, reset: true } }));
  }
  async function openAssetSingleWorkflow(menu) {
    if (!menu?.pkg) return;
    const resolvedAssetTask = menu.assetTask || assetTaskByType(menu.pkg, menu.role === "last" ? ["image_regenerate_last"] : ["image_regenerate_first", "image_regenerate_single"]);
    setAssetImageContextMenu(null);
    const workflowId = `${sceneWorkflowId(menu.pkg, menu.role)}_single`;
    const basePrompt = assetImageBasePrompt(resolvedAssetTask);
    updateCompareExpandedSteps(workflowId, defaultCompareExpandedSteps());
    updateAssetCompareWorkflow(workflowId, { workflowId, single: true, menu: { ...menu, assetTask: resolvedAssetTask }, phase: "mode", mode: "", round: 1, providerPrompts: [], candidates: [], selectedCandidate: null, refinePrompts: [], refineCandidates: [], variantCount: 1, finalizing: false, error: "", basePrompt });
    await ensureImageModelConfig();
    try {
      const saved = await api.getAssetImageWorkflow(selectedTaskId(), workflowId);
      if (saved?.exists && saved.workflow) updateAssetCompareWorkflow(workflowId, { ...hydrateCompareWorkflow(saved.workflow, { ...menu, assetTask: resolvedAssetTask }, basePrompt), single: true, variantCount: Math.max(1, Math.min(3, saved.workflow?.variant_count || saved.workflow?.round_1?.prompts?.length || 1)) });
    } catch (err) {
      updateAssetCompareWorkflow(workflowId, (prev) => prev ? { ...prev, error: err instanceof Error ? err.message : String(err) } : prev);
    }
    window.dispatchEvent(new CustomEvent("opencrew:debug-session", { detail: { sessionId: task()?.session_id || null, reset: true } }));
  }
  function hydrateCompareWorkflow(stored, menu, basePrompt) {
    const promptOptionsFor = (provider, model) => {
      const providerConfig = (imageModelConfig()?.providers || []).find((item) => item.provider === provider);
      const models = providerConfig?.models?.length ? providerConfig.models : [];
      const hasCurrentModel = models.some((item) => item.model === model);
      return { providerLabel: providerConfig?.provider_label || providerConfig?.label || provider, models: hasCurrentModel || !model ? models : [{ model, label: model }, ...models] };
    };
    const round1Prompts = (stored?.round_1?.prompts || []).map((item) => { const options = promptOptionsFor(item.provider, item.model); return { provider: item.provider, providerLabel: options.providerLabel, model: item.model, models: options.models, docsUrl: IMAGE_DOC_URLS[item.provider] || "", originalPrompt: basePrompt, currentPrompt: item.current_prompt || item.prompt || "", userInstruction: item.user_instruction || "", confirmed: true, refining: false, error: "" }; });
    const round2Prompts = (stored?.round_2?.prompts || []).map((item, index) => { const options = promptOptionsFor(item.provider, item.model); return { provider: item.provider, providerLabel: options.providerLabel, model: item.model, models: options.models, variant: item.variant || index + 1, docsUrl: IMAGE_DOC_URLS[item.provider] || "", originalPrompt: basePrompt, currentPrompt: item.current_prompt || item.prompt || "", userInstruction: item.user_instruction || "", confirmed: true, refining: false, error: "" }; });
    const toCandidate = (item) => ({ candidateId: item.candidate_id || item.candidateId, provider: item.provider, model: item.model, output: item.output, outputPath: item.output_path, src: rebuildAssetUrl(item.output), elapsedSeconds: item.elapsed_seconds, usedReferenceImage: Boolean(item.used_reference_image), promptPreview: item.prompt_preview || "", status: item.status || "completed", error: item.detail || "" });
    const refineCandidates = (stored?.round_2?.candidates || []).map(toCandidate);
    if (!refineCandidates.length && stored?.final?.selected_output) {
      const selectedOutput = String(stored.final.selected_output || "");
      const round2Dir = selectedOutput.replace(/\/variant_\d+\.png$/, "");
      const variantTotal = Math.max(3, round2Prompts.length || 0);
      for (let variant = 1; variant <= variantTotal; variant += 1) {
        const output = round2Dir ? `${round2Dir}/variant_${variant}.png` : selectedOutput;
        refineCandidates.push(toCandidate({ candidate_id: `restored_round_2_variant_${variant}`, provider: stored.final.provider, model: stored.final.model, output, used_reference_image: stored.final.used_reference_image, status: "completed", variant }));
      }
    }
    return { workflowId: stored.workflow_id, menu, phase: stored.phase || (round1Prompts.length ? "select" : "mode"), mode: stored.mode || "", round: stored.round || 1, providerPrompts: round1Prompts, candidates: (stored?.round_1?.candidates || []).map(toCandidate), selectedCandidate: stored.selected_candidate ? toCandidate(stored.selected_candidate) : null, refinePrompts: round2Prompts, refineCandidates, final: stored.final || null, variantCount: Math.max(1, round2Prompts.length || 3), finalizing: false, error: "", basePrompt };
  }
  async function startAssetCompareMode(workflowId, mode) {
    const wf = assetCompareWorkflows()[workflowId];
    if (!wf) return;
    const config = await ensureImageModelConfig();
    const providers = (config?.providers || []).filter((item) => ["openai", "xai", "gemini"].includes(String(item.provider)) && item.enabled && item.has_api_key);
    const basePrompt = wf.basePrompt || assetImageBasePrompt(wf.menu?.assetTask);
    updateCompareExpandedSteps(workflowId, { mode: false, prompts: false, firstRound: false });
    updateAssetCompareWorkflow(workflowId, (prev) => prev ? { ...prev, mode, phase: "prompts", candidates: [], selectedCandidate: null, refinePrompts: [], refineCandidates: [], providerPrompts: providers.map((item) => ({ provider: item.provider, providerLabel: item.provider_label || item.provider, model: item.model, models: item.models || [], docsUrl: item.docs_url || IMAGE_DOC_URLS[item.provider] || "", originalPrompt: basePrompt, currentPrompt: basePrompt, userInstruction: "", confirmed: false, refining: false, error: "" })) } : prev);
  }
  async function startAssetSingleMode(workflowId, mode) {
    const wf = assetCompareWorkflows()[workflowId];
    if (!wf) return;
    const config = await ensureImageModelConfig();
    const providers = (config?.providers || []).filter((item) => ["openai", "xai", "gemini"].includes(String(item.provider)) && item.enabled && item.has_api_key);
    const activeProvider = providers.find((item) => item.provider === config?.active_provider) || providers[0];
    const basePrompt = wf.basePrompt || assetImageBasePrompt(wf.menu?.assetTask);
    updateCompareExpandedSteps(workflowId, { mode: false, prompts: false, firstRound: false });
    updateAssetCompareWorkflow(workflowId, (prev) => prev && activeProvider ? { ...prev, mode, phase: "prompts", candidates: [], selectedCandidate: null, refinePrompts: [], refineCandidates: [], providerPrompts: [{ provider: activeProvider.provider, providerLabel: activeProvider.provider_label || activeProvider.provider, model: activeProvider.model, models: activeProvider.models || [], docsUrl: activeProvider.docs_url || IMAGE_DOC_URLS[activeProvider.provider] || "", originalPrompt: basePrompt, currentPrompt: basePrompt, userInstruction: "", confirmed: false, refining: false, error: "" }] } : prev ? { ...prev, error: "未找到可用的图片生成默认 Provider" } : prev);
  }
  function updateProviderPrompt(workflowId, index, patch) {
    updateAssetCompareWorkflow(workflowId, (prev) => prev ? { ...prev, providerPrompts: prev.providerPrompts.map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item) } : prev);
  }
  async function refineProviderPrompt(workflowId, index, round = 1, values = null) {
    const wf = assetCompareWorkflows()[workflowId];
    const baseItem = round === 1 ? wf?.providerPrompts?.[index] : wf?.refinePrompts?.[index];
    const item = baseItem ? { ...baseItem, ...(values || {}) } : null;
    if (!wf || !item?.userInstruction?.trim()) return;
    const update = round === 1 ? updateProviderPrompt : updateRefinePrompt;
    update(workflowId, index, { refining: true, error: "" });
    const requestId = `refine_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    emitDebugRequested(debugContext({ family: "prompt_refine", request_id: requestId, workflow_id: wf.workflowId, provider: item.provider, model: item.model }));
    try {
      const res = await api.refineAssetPrompt(selectedTaskId(), { request_id: requestId, workflow_id: wf.workflowId, provider: item.provider, image_model: item.model, mode: round === 1 ? wf.mode : "with_reference", current_prompt: item.currentPrompt, user_instruction: item.userInstruction, round });
      update(workflowId, index, { currentPrompt: res.prompt || item.currentPrompt, docsUrl: res.docs_url || item.docsUrl, refining: false });
      emitDebugCompleted(debugContext({ family: "prompt_refine", request_id: requestId, workflow_id: wf.workflowId, provider: item.provider, model: item.model, prompt_preview: res.prompt || item.currentPrompt, docs_url: res.docs_url, payload: res }));
    } catch (err) {
      update(workflowId, index, { refining: false, error: err instanceof Error ? err.message : String(err) });
      emitDebugError(err, debugContext({ family: "prompt_refine", request_id: requestId, workflow_id: wf.workflowId, provider: item.provider, model: item.model }));
    }
  }
  function compareCandidateFromEvent(event) {
    return { candidateId: event.candidate_id, provider: event.provider, model: event.model, output: event.output, outputPath: event.output_path, src: rebuildAssetUrl(event.output), elapsedSeconds: event.elapsed_seconds, usedReferenceImage: Boolean(event.used_reference_image), promptPreview: event.prompt_preview || "", status: event.type === "failed" ? "failed" : "completed", error: event.detail || "" };
  }
  async function generateCompareRound(workflowId, round = 1) {
    const wf = assetCompareWorkflows()[workflowId];
    if (!wf) return;
    const sourcePrompts = round === 1 ? wf.providerPrompts : wf.refinePrompts.slice(0, wf.variantCount || 3);
    const cards = Array.from(document.querySelectorAll(`.ocrebuild-compare-dialog[data-workflow-id="${CSS.escape(workflowId)}"] .ocrebuild-compare-card`));
    const prompts = sourcePrompts.map((item, index) => ({
      ...item,
      model: cards[index]?.querySelector("select")?.value || item.model,
      userInstruction: cards[index]?.querySelector("input")?.value ?? item.userInstruction,
      currentPrompt: cards[index]?.querySelector("textarea")?.value ?? item.currentPrompt,
    }));
    updateCompareExpandedSteps(workflowId, (prev) => ({ ...prev, prompts: false, firstRound: false, refinePrompts: false, secondRound: false }));
    updateAssetCompareWorkflow(workflowId, (prev) => prev ? { ...prev, phase: round === 1 ? "generating" : "refine_generating", error: "", ...(round === 1 ? { providerPrompts: prompts, candidates: [], selectedCandidate: null, refinePrompts: [], refineCandidates: [] } : { refinePrompts: prompts, refineCandidates: [] }) } : prev);
    emitDebugRequested(debugContext({ family: "asset_image", workflow_id: wf.workflowId, request_id: `image_compare_${wf.workflowId}_${round}_${Date.now()}` }));
    await runAction(`asset-compare-${round}`, async () => {
      await api.streamCompareAssetImages(selectedTaskId(), { workflow_id: wf.workflowId, shot_id: wf.menu.pkg.shot_id, scene_mark_id: wf.menu.pkg.scene_mark_id || "", role: wf.menu.role, mode: round === 1 ? wf.mode : "with_reference", round, reference_output: round === 2 ? wf.selectedCandidate?.output || "" : "", prompts: prompts.filter((item) => item.currentPrompt?.trim()).map((item, index) => ({ provider: item.provider, model: item.model, prompt: item.currentPrompt, user_instruction: item.userInstruction || "", variant: round === 2 ? index + 1 : 0 })) }, (event) => {
        emitStreamDebug(event, { family: "asset_image", workflow_id: wf.workflowId });
        if (event.type === "completed" || event.type === "failed") {
          const candidate = compareCandidateFromEvent(event);
          updateAssetCompareWorkflow(workflowId, (prev) => prev ? round === 1 ? { ...prev, candidates: [...prev.candidates.filter((item) => item.candidateId !== candidate.candidateId), candidate] } : { ...prev, final: null, refineCandidates: [...prev.refineCandidates.filter((item) => item.candidateId !== candidate.candidateId), candidate] } : prev);
        }
        if (event.type === "round_completed") {
          updateCompareExpandedSteps(workflowId, (prev) => round === 1 ? { ...prev, firstRound: true } : { ...prev, refinePrompts: false, secondRound: true });
          updateAssetCompareWorkflow(workflowId, (prev) => prev ? { ...prev, phase: round === 1 ? "select" : "final_select" } : prev);
        }
      });
    });
  }
  async function generateSingleRound(workflowId) {
    const wf = assetCompareWorkflows()[workflowId];
    if (!wf) return;
    const card = document.querySelector(`.ocrebuild-compare-dialog[data-workflow-id="${CSS.escape(workflowId)}"] .ocrebuild-compare-card`);
    const sourcePrompt = wf.providerPrompts?.[0];
    if (!sourcePrompt) return;
    const prompt = {
      ...sourcePrompt,
      model: card?.querySelector("select")?.value || sourcePrompt.model,
      userInstruction: card?.querySelector("input")?.value ?? sourcePrompt.userInstruction,
      currentPrompt: card?.querySelector("textarea")?.value ?? sourcePrompt.currentPrompt,
    };
    const variantCount = Math.max(1, Math.min(3, Number(wf.variantCount || 1)));
    const prompts = Array.from({ length: variantCount }, (_, index) => ({ ...prompt, variant: index + 1 }));
    updateCompareExpandedSteps(workflowId, (prev) => ({ ...prev, prompts: false, firstRound: false }));
    updateAssetCompareWorkflow(workflowId, (prev) => prev ? { ...prev, phase: "generating", error: "", variantCount, providerPrompts: [prompt], candidates: [], selectedCandidate: null, refinePrompts: [], refineCandidates: [] } : prev);
    emitDebugRequested(debugContext({ family: "asset_image", workflow_id: wf.workflowId, request_id: `image_single_${wf.workflowId}_${Date.now()}`, provider: prompt.provider, model: prompt.model }));
    await runAction("asset-compare-single", async () => {
      await api.streamCompareAssetImages(selectedTaskId(), { workflow_id: wf.workflowId, shot_id: wf.menu.pkg.shot_id, scene_mark_id: wf.menu.pkg.scene_mark_id || "", role: wf.menu.role, mode: wf.mode, round: 1, reference_output: "", prompts: prompts.filter((item) => item.currentPrompt?.trim()).map((item) => ({ provider: item.provider, model: item.model, prompt: item.currentPrompt, user_instruction: item.userInstruction || "", variant: item.variant })) }, (event) => {
        emitStreamDebug(event, { family: "asset_image", workflow_id: wf.workflowId, provider: prompt.provider, model: prompt.model });
        if (event.type === "completed" || event.type === "failed") {
          const candidate = compareCandidateFromEvent(event);
          updateAssetCompareWorkflow(workflowId, (prev) => prev ? { ...prev, candidates: [...prev.candidates.filter((item) => item.candidateId !== candidate.candidateId), candidate] } : prev);
        }
        if (event.type === "round_completed") {
          updateCompareExpandedSteps(workflowId, (prev) => ({ ...prev, firstRound: true }));
          updateAssetCompareWorkflow(workflowId, (prev) => prev ? { ...prev, phase: "select" } : prev);
        }
      });
    });
  }
  async function selectCompareCandidate(workflowId, candidate) {
    const wf = assetCompareWorkflows()[workflowId];
    if (!wf || !candidate) return;
    const winnerPrompt = wf.providerPrompts.find((item) => item.provider === candidate.provider && item.model === candidate.model)?.currentPrompt || wf.basePrompt || "";
    const providerConfig = (imageModelConfig()?.providers || []).find((item) => item.provider === candidate.provider);
    const refinePrompts = [1, 2, 3].map((variant) => ({ provider: candidate.provider, providerLabel: candidate.provider, model: candidate.model, models: providerConfig?.models || [], variant, docsUrl: IMAGE_DOC_URLS[candidate.provider] || "", originalPrompt: winnerPrompt, currentPrompt: `${winnerPrompt}\n\nRefine variant ${variant}: keep the chosen image as visual reference and improve realism, composition, lighting, and production quality without changing the core subject.`, userInstruction: variant === 1 ? "更保守，保持一致性" : variant === 2 ? "更真实，更自然" : "强化用户想要的风格差异", confirmed: false, refining: false, error: "" }));
    updateCompareExpandedSteps(workflowId, (prev) => ({ ...prev, firstRound: false }));
    updateAssetCompareWorkflow(workflowId, (prev) => prev ? { ...prev, selectedCandidate: candidate, round: 2, phase: "refine_prompts", refinePrompts, refineCandidates: [] } : prev);
    await api.saveAssetImageWorkflow(selectedTaskId(), workflowId, { selected_candidate: { candidate_id: candidate.candidateId, provider: candidate.provider, model: candidate.model, output: candidate.output, output_path: candidate.outputPath, elapsed_seconds: candidate.elapsedSeconds, used_reference_image: candidate.usedReferenceImage, status: candidate.status }, phase: "refine_prompts", round: 2, round_2: { prompts: refinePrompts.map((item) => ({ provider: item.provider, model: item.model, prompt: item.currentPrompt, current_prompt: item.currentPrompt, user_instruction: item.userInstruction, variant: item.variant })) } });
  }
  function compareModeLabel(value) { return value === "with_reference" ? "使用原图参考" : value === "prompt_only" ? "仅用提示词" : "未选择"; }
  function displayImageModelName(candidate) {
    const provider = String(candidate?.provider || "").trim();
    const model = String(candidate?.model || "").trim();
    return provider && model.startsWith(`${provider}/`) ? model.slice(provider.length + 1) : model;
  }
  function resetCompareToMode(workflowId) {
    updateCompareExpandedSteps(workflowId, { mode: true, prompts: false, firstRound: false });
    updateAssetCompareWorkflow(workflowId, (prev) => prev ? { ...prev, phase: "mode", mode: "", providerPrompts: [], candidates: [], selectedCandidate: null, refinePrompts: [], refineCandidates: [] } : prev);
  }
  function resetCompareToPrompts(workflowId) {
    updateCompareExpandedSteps(workflowId, { mode: false, prompts: false, firstRound: false });
    updateAssetCompareWorkflow(workflowId, (prev) => prev ? { ...prev, phase: "prompts", candidates: [], selectedCandidate: null, refinePrompts: [], refineCandidates: [] } : prev);
  }
  function resetCompareToFirstRound(workflowId) {
    updateCompareExpandedSteps(workflowId, (prev) => ({ ...prev, firstRound: false }));
    updateAssetCompareWorkflow(workflowId, (prev) => prev ? { ...prev, phase: "select", selectedCandidate: null, refinePrompts: [], refineCandidates: [] } : prev);
  }
  function resetCompareToRefinePrompts(workflowId) {
    updateAssetCompareWorkflow(workflowId, (prev) => prev ? { ...prev, phase: "refine_prompts" } : prev);
  }
  function resetCompareToRefinePromptsForEdit(workflowId) {
    updateCompareExpandedSteps(workflowId, (prev) => ({ ...prev, refinePrompts: true }));
    updateAssetCompareWorkflow(workflowId, (prev) => prev ? { ...prev, phase: "refine_prompts" } : prev);
  }
  function updateRefinePrompt(workflowId, index, patch) {
    updateAssetCompareWorkflow(workflowId, (prev) => prev ? { ...prev, refinePrompts: prev.refinePrompts.map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item) } : prev);
  }
  function startAssetCompareDialogDrag(workflowId, event) {
    if (event.button !== 0 || event.target.closest("button, input, textarea, select, a")) return;
    const dialog = event.currentTarget.closest(".ocrebuild-compare-dialog");
    if (!dialog) return;
    event.preventDefault();
    const rect = dialog.getBoundingClientRect();
    const offsetX = event.clientX - rect.left;
    const offsetY = event.clientY - rect.top;
    const onMove = (moveEvent) => {
      const maxLeft = Math.max(8, window.innerWidth - rect.width - 8);
      const maxTop = Math.max(8, window.innerHeight - rect.height - 8);
      setAssetCompareDialogPositions((prev) => ({ ...prev, [workflowId]: { left: Math.max(8, Math.min(maxLeft, moveEvent.clientX - offsetX)), top: Math.max(8, Math.min(maxTop, moveEvent.clientY - offsetY)) } }));
    };
    const onUp = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  }
  function startAssetVideoDialogDrag(workflowId, event) {
    if (event.button !== 0 || event.target.closest("button, input, textarea, select, a, video")) return;
    const dialog = event.currentTarget.closest(".ocrebuild-compare-dialog");
    if (!dialog) return;
    event.preventDefault();
    const rect = dialog.getBoundingClientRect();
    const offsetX = event.clientX - rect.left;
    const offsetY = event.clientY - rect.top;
    const onMove = (moveEvent) => {
      const maxLeft = Math.max(8, window.innerWidth - rect.width - 8);
      const maxTop = Math.max(8, window.innerHeight - rect.height - 8);
      setAssetVideoDialogPositions((prev) => ({ ...prev, [workflowId]: { left: Math.max(8, Math.min(maxLeft, moveEvent.clientX - offsetX)), top: Math.max(8, Math.min(maxTop, moveEvent.clientY - offsetY)) } }));
    };
    const onUp = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  }
  function startShotMultiReferenceDialogDrag(workflowId, event) {
    if (event.button !== 0 || event.target.closest("button, input, textarea, select, a, video")) return;
    const dialog = event.currentTarget.closest(".ocrebuild-compare-dialog");
    if (!dialog) return;
    event.preventDefault();
    const rect = dialog.getBoundingClientRect();
    const offsetX = event.clientX - rect.left;
    const offsetY = event.clientY - rect.top;
    const onMove = (moveEvent) => {
      const maxLeft = Math.max(8, window.innerWidth - rect.width - 8);
      const maxTop = Math.max(8, window.innerHeight - rect.height - 8);
      setShotMultiReferenceDialogPositions((prev) => ({ ...prev, [workflowId]: { left: Math.max(8, Math.min(maxLeft, moveEvent.clientX - offsetX)), top: Math.max(8, Math.min(maxTop, moveEvent.clientY - offsetY)) } }));
    };
    const onUp = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  }
  function startAssetTTSDialogDrag(workflowId, event) {
    if (event.button !== 0 || event.target.closest("button, input, textarea, select, a, audio")) return;
    const dialog = event.currentTarget.closest(".ocrebuild-compare-dialog");
    if (!dialog) return;
    event.preventDefault();
    const rect = dialog.getBoundingClientRect();
    const offsetX = event.clientX - rect.left;
    const offsetY = event.clientY - rect.top;
    const onMove = (moveEvent) => {
      const maxLeft = Math.max(8, window.innerWidth - rect.width - 8);
      const maxTop = Math.max(8, window.innerHeight - rect.height - 8);
      setAssetTTSDialogPositions((prev) => ({ ...prev, [workflowId]: { left: Math.max(8, Math.min(maxLeft, moveEvent.clientX - offsetX)), top: Math.max(8, Math.min(maxTop, moveEvent.clientY - offsetY)) } }));
    };
    const onUp = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  }
  async function finalizeCompareCandidate(workflowId, candidate) {
    const wf = assetCompareWorkflows()[workflowId];
    if (!wf || !candidate) return;
    updateAssetCompareWorkflow(workflowId, (prev) => prev ? { ...prev, finalizing: true, error: "" } : prev);
    try {
      await api.finalizeCompareAssetImage(selectedTaskId(), { workflow_id: workflowId, shot_id: wf.menu.pkg.shot_id, scene_mark_id: wf.menu.pkg.scene_mark_id || "", role: wf.menu.role, selected_output: candidate.output, provider: candidate.provider, model: candidate.model, used_reference_image: candidate.usedReferenceImage });
      await loadAssetTasks({ force: true });
      updateAssetCompareWorkflow(workflowId, (prev) => prev ? { ...prev, phase: "finalized", finalizing: false, final: { selected_output: candidate.output, provider: candidate.provider, model: candidate.model, used_reference_image: candidate.usedReferenceImage } } : prev);
    } catch (err) {
      updateAssetCompareWorkflow(workflowId, (prev) => prev ? { ...prev, finalizing: false, error: err instanceof Error ? err.message : String(err) } : prev);
    }
  }
  function assetPromptRows(pkg, imageRole = "first") {
    const imagePrompts = pkg?.image_prompts || {};
    const videoPrompts = pkg?.video_prompts || {};
    const intent = pkg?.generation_intent || {};
    const reference = pkg?.reference || {};
    const rolePrompt = imageRole === "last" ? imagePrompts.last_image_prompt : imageRole === "single" ? imagePrompts.single_image_prompt : imagePrompts.first_image_prompt;
    return [
      { field: "scene_mark_id", value: pkg?.scene_mark_id },
      { field: "srt_text", value: pkg?.srt_text },
      { field: `${imageRole}_reference`, value: imageRole === "last" ? reference.last_reference_prompt : reference.first_reference_prompt || reference.reference_frame_prompt },
      { field: `${imageRole}_image_prompt`, value: rolePrompt },
      { field: "image_negative_prompt", value: imagePrompts.image_negative_prompt },
      { field: "veo_prompt", value: videoPrompts.veo_prompt },
      { field: "wan_prompt", value: videoPrompts.wan_prompt },
      { field: "sora_prompt", value: videoPrompts.sora_prompt },
      { field: "grok_prompt", value: videoPrompts.grok_prompt },
      { field: "transition_video_prompt", value: videoPrompts.transition_video_prompt || videoPrompts.base_video_prompt },
      { field: "video_negative_prompt", value: videoPrompts.video_negative_prompt },
      { field: "kept_meaning", value: intent.kept_meaning },
      { field: "changed_visuals", value: intent.changed_visuals },
      { field: "style_upgrade", value: intent.style_upgrade },
      { field: "srt_alignment", value: intent.srt_alignment },
      { field: "first_to_last_motion", value: intent.first_to_last_motion },
    ].map((row) => ({ ...row, value: row.value === null || row.value === undefined || row.value === "" ? "-" : String(row.value) }));
  }
  function deleteKeyframe(shot, frameIndex) {
    const shotId = shot?.shot_id;
    if (!shotId) return;
    setKeyframeEdits((prev) => {
      const current = prev[shotId] || { original: shotKeyframes(shot), keyframes: shotKeyframes(shot), scene_marks: editableSceneMarks(shot), deleted: [] };
      const snapshot = { keyframes: cloneKeyframes(current.keyframes), scene_marks: cloneKeyframes(current.scene_marks || []) };
      const nextFrames = current.keyframes.map((item) => ({ ...item, scene_mark: item.scene_mark ? { ...item.scene_mark } : undefined }));
      let nextSceneMarks = cloneKeyframes(current.scene_marks || []);
      const removed = nextFrames.splice(frameIndex, 1)[0];
      if (!removed) return prev;
      const removedSceneId = removed.scene_mark?.scene_mark_id;
      if (removedSceneId) {
        nextSceneMarks = nextSceneMarks.filter((item) => item.scene_mark_id !== removedSceneId);
        for (const frame of nextFrames) {
          if (frame.scene_mark?.scene_mark_id === removedSceneId) delete frame.scene_mark;
        }
      }
      return { ...prev, [shotId]: { ...current, keyframes: nextFrames, scene_marks: nextSceneMarks, deleted: [...current.deleted, { frame: removed, index: frameIndex, snapshot }], dirty: true, sceneMarksDirty: true } };
    });
  }
  function undoKeyframeDelete(shot) {
    const shotId = shot?.shot_id;
    if (!shotId) return;
    setKeyframeEdits((prev) => {
      const current = prev[shotId];
      if (!current?.deleted?.length) return prev;
      const deleted = [...current.deleted];
      const last = deleted.pop();
      if (last.snapshot) return { ...prev, [shotId]: { ...current, keyframes: cloneKeyframes(last.snapshot.keyframes), scene_marks: cloneKeyframes(last.snapshot.scene_marks), deleted, dirty: true, sceneMarksDirty: true } };
      const nextFrames = [...current.keyframes];
      nextFrames.splice(Math.min(last.index, nextFrames.length), 0, last.frame);
      return { ...prev, [shotId]: { ...current, keyframes: nextFrames, deleted, dirty: true, sceneMarksDirty: true } };
    });
  }
  function markSceneBoundary(shot, frame, role) {
    const shotId = shot?.shot_id;
    if (!shotId) return;
    setKeyframeEdits((prev) => {
      const current = sceneEditState(prev, shot);
      const frames = current.keyframes.map((item) => ({ ...item, scene_mark: item.scene_mark ? { ...item.scene_mark } : undefined }));
      const sceneMarks = cloneSceneMarks(current.scene_marks || []);
      const frameIndex = frames.findIndex((item) => item.path === frame.path);
      if (frameIndex < 0) return prev;
      const frameTime = Number(frames[frameIndex].time || 0);
      let assigned = false;
      if (role === "single" || frames.length === 1) {
        const previousSceneId = frames[frameIndex].scene_mark?.scene_mark_id;
        if (previousSceneId) {
          for (let index = sceneMarks.length - 1; index >= 0; index -= 1) {
            if (sceneMarks[index]?.scene_mark_id === previousSceneId) sceneMarks.splice(index, 1);
          }
        }
        frames.forEach((item) => {
          if (!previousSceneId || item.scene_mark?.scene_mark_id === previousSceneId || item.path === frames[frameIndex].path) delete item.scene_mark;
        });
        frames[frameIndex].scene_mark = { scene_mark_id: nextDraftSceneId(shotId, frames, sceneMarks), role: "single", click_behavior: "show_scene_description" };
        assigned = true;
      } else if (frames[frameIndex].scene_mark) {
        return prev;
      } else if (role === "last") {
        const partialFirst = [...frames].slice(0, frameIndex).reverse().find((item) => item.scene_mark?.role === "first" && !sceneMarks.some((mark) => mark.scene_mark_id === item.scene_mark.scene_mark_id));
        const completeFirst = [...frames].slice(0, frameIndex).reverse().find((item) => item.scene_mark?.role === "first" && sceneMarks.some((mark) => mark.scene_mark_id === item.scene_mark.scene_mark_id && frameTime < Number(mark.end ?? frameTime)));
        const first = partialFirst || completeFirst;
        const sceneId = first?.scene_mark?.scene_mark_id || nextDraftSceneId(shotId, frames, sceneMarks);
        if (first && keyframeTime(first) >= frameTime) return prev;
        frames.forEach((item) => { if (item.scene_mark?.scene_mark_id === sceneId && item.scene_mark?.role === "last") delete item.scene_mark; });
        frames[frameIndex].scene_mark = { scene_mark_id: sceneId, role: "last", click_behavior: "show_scene_description" };
        assigned = true;
      } else {
        const partialLast = frames.slice(frameIndex + 1).find((item) => item.scene_mark?.role === "last" && !sceneMarks.some((mark) => mark.scene_mark_id === item.scene_mark.scene_mark_id));
        const completeLast = frames.slice(frameIndex + 1).find((item) => item.scene_mark?.role === "last" && sceneMarks.some((mark) => mark.scene_mark_id === item.scene_mark.scene_mark_id && frameTime > Number(mark.start ?? frameTime)));
        const last = partialLast || completeLast;
        const sceneId = last?.scene_mark?.scene_mark_id || nextDraftSceneId(shotId, frames, sceneMarks);
        if (last && keyframeTime(last) <= frameTime) return prev;
        frames.forEach((item) => { if (item.scene_mark?.scene_mark_id === sceneId && item.scene_mark?.role === "first") delete item.scene_mark; });
        frames[frameIndex].scene_mark = { scene_mark_id: sceneId, role: "first", click_behavior: "show_scene_description" };
        assigned = true;
      }
      if (!assigned) return prev;
      const normalized = normalizeSceneMarkState(shotId, frames, sceneMarks, true);
      return { ...prev, [shotId]: { ...current, keyframes: normalized.frames, scene_marks: normalized.sceneMarks, dirty: true, sceneMarksDirty: true } };
    });
    setKeyframeContextMenu(null);
  }
  function unmarkSceneBoundary(shot, frame) {
    const shotId = shot?.shot_id;
    if (!shotId || !frame?.scene_mark) return;
    setKeyframeEdits((prev) => {
      const current = sceneEditState(prev, shot);
      const frames = current.keyframes.map((item) => ({ ...item, scene_mark: item.scene_mark ? { ...item.scene_mark } : undefined }));
      const frameIndex = frames.findIndex((item) => item.path === frame.path);
      if (frameIndex < 0 || !frames[frameIndex].scene_mark) return prev;
      delete frames[frameIndex].scene_mark;
      const normalized = normalizeSceneMarkState(shotId, frames, cloneSceneMarks(current.scene_marks || []), true);
      return { ...prev, [shotId]: { ...current, keyframes: normalized.frames, scene_marks: normalized.sceneMarks, dirty: true, sceneMarksDirty: true } };
    });
    setKeyframeContextMenu(null);
  }
  function restoreAllKeyframes(shot) {
    const shotId = shot?.shot_id;
    if (!shotId) return;
    setKeyframeEdits((prev) => {
      const original = cloneKeyframes(shot?.reference?.original_keyframes || []);
      if (original.length && !prev[shotId]) return { ...prev, [shotId]: { original: shotKeyframes(shot), keyframes: original, scene_marks: [], deleted: [], dirty: true, sceneMarksDirty: true } };
      const next = { ...prev };
      delete next[shotId];
      return next;
    });
  }
  async function saveSelectedShotKeyframes(shot) {
    if (!task()?.id || !shot?.shot_id) return;
    const normalized = normalizeSceneMarkState(shot.shot_id, editableKeyframes(shot), editableSceneMarks(shot), false);
    const frames = normalized.frames;
    const sceneMarks = normalized.sceneMarks;
    const res = await api.saveShotSceneMarks(task().id, { shot_id: shot.shot_id, keyframes: frames, scene_marks: sceneMarks });
    setShotPlan((prev) => ({
      ...prev,
      shots: (prev?.shots || []).map((item) => item?.shot_id === shot.shot_id ? { ...item, reference: { ...(item.reference || {}), keyframes: res.keyframes || [], original_keyframes: res.original_keyframes || item.reference?.original_keyframes || [], deleted_keyframes: res.deleted_keyframes || [], scene_marks: res.scene_marks || [], scene_mark_summary: res.scene_mark_summary || item.reference?.scene_mark_summary } } : item),
    }));
    setAssetPromptPackages((prev) => {
      const next = { ...prev };
      delete next[shot.shot_id];
      return next;
    });
    setKeyframeEdits((prev) => {
      const next = { ...prev };
      delete next[shot.shot_id];
      return next;
    });
  }
  function flattenShotFields(value, prefix = "") {
    if (Array.isArray(value)) return value.length ? value.flatMap((item, index) => flattenShotFields(item, `${prefix}[${index}]`)) : [{ field: prefix, value: "[]" }];
    if (value && typeof value === "object") {
      const entries = Object.entries(value);
      return entries.length ? entries.flatMap(([key, item]) => flattenShotFields(item, prefix ? `${prefix}.${key}` : key)) : [{ field: prefix, value: "{}" }];
    }
    return [{ field: prefix, value: value === null || value === undefined ? "-" : String(value) }];
  }
  function shotDetailRows(shot) {
    if (!shot) return [];
    const srtText = shot.reference?.srt_text || sourceSrtTexts()[shot.shot_id] || "";
    return [
      { field: "start", value: shot.start },
      { field: "end", value: shot.end },
      { field: "duration", value: shot.duration },
      { field: "role", value: shot.role },
      { field: "formula_slot", value: shot.formula_slot },
      { field: "reference.srt_text", value: srtText },
      { field: "ui_summary.summary", value: shot.ui_summary?.summary },
      { field: "rebuild_direction.direction", value: shot.rebuild_direction?.direction },
      { field: "generation_hint.hint", value: shot.generation_hint?.hint },
      ...(Array.isArray(shot.quality_notes) ? shot.quality_notes.map((value, index) => ({ field: `quality_notes[${index}]`, value })) : [{ field: "quality_notes", value: fieldText(shot.quality_notes) }]),
    ].map((row) => ({ ...row, value: row.value === null || row.value === undefined || row.value === "" ? "-" : String(row.value) }));
  }
  function sceneMarkRows(dialog) {
    const mark = dialog?.mark || {};
    const desc = mark.scene_description || {};
    const notes = desc.model_notes || {};
    return [
      { field: "shot_id", value: mark.shot_id },
      { field: "scene_mark_id", value: mark.scene_mark_id },
      { field: "time", value: `${mark.start ?? "-"}s - ${mark.end ?? "-"}s / ${mark.duration ?? "-"}s` },
      { field: "keyframes.first", value: mark.keyframes?.first || mark.keyframes?.single || "-" },
      { field: "keyframes.last", value: mark.keyframes?.last || "-" },
      { field: "original_srt_text", value: srtOriginalForMark(mark) },
      { field: "srt_text", value: mark.srt_text },
      { field: "summary", value: desc.summary },
      { field: "visual_change", value: desc.visual_change },
      { field: "motion_prompt", value: desc.motion_prompt },
      { field: "video_prompt", value: desc.video_prompt },
      { field: "negative_prompt", value: desc.negative_prompt },
      { field: "model_notes.veo", value: notes.veo },
      { field: "model_notes.sora", value: notes.sora },
      { field: "model_notes.grok", value: notes.grok },
      { field: "model_notes.wan", value: notes.wan },
    ].map((row) => ({ ...row, value: row.value === null || row.value === undefined || row.value === "" ? "-" : String(row.value) }));
  }
  function updatePromptModelProvider(providerID) { const models = runModels().filter((item) => item.providerID === providerID); const preferred = models.find((item) => item.modelID === promptModels().default_model.modelID) ?? models[0]; setDraft((prev) => ({ ...prev, prompt_model_provider: providerID, prompt_model_id: preferred?.modelID ?? "" })); }
  function updateRunModelProvider(providerID) { const models = runModels().filter((item) => item.providerID === providerID); const preferred = findPreferredRunModel(models) ?? models[0]; setDraft((prev) => ({ ...prev, run_model_provider: providerID, run_model_id: preferred?.modelID ?? "" })); }
  function selectPromptModelPreset(selection) { setDraft((prev) => ({ ...prev, prompt_model_provider: selection.providerID, prompt_model_id: selection.modelID })); }
  function selectRunModelPreset(selection) { setDraft((prev) => ({ ...prev, run_model_provider: selection.providerID, run_model_id: selection.modelID })); }
  async function createStoryBoardFromCurrentTask() {
    const currentTask = task();
    if (!currentTask) return;
    setBusy("storyboard-copy");
    setError("");
    try {
      await api.storyBoardDetail(currentTask.id);
      window.location.hash = `#/ocstoryboard/tasks/${currentTask.id}`;
      setBusy("");
      return;
    } catch (error) {
      setError("");
    }
    try {
      const result = await api.createStoryBoardCopy(currentTask.id);
      window.location.hash = `#/ocstoryboard/tasks/${result.task.id}`;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setError(message);
      emitDebugError(err, debugContext({ family: "error", request_id: "storyboard-copy" }));
    } finally {
      setBusy("");
    }
  }

  const renderShotPlanHeader = () => <div class="ocrebuild-shot-plan-head">
    <h3>Shot Plan <span>{shotPlanShots().length || 0} Shots</span></h3>
    <div class="ocrebuild-shot-plan-actions">
      <button
        class="secondary ocrebuild-storyboard-entry"
        type="button"
        disabled={!task() || !shotPlanShots().length || busy() === "storyboard-copy"}
        onClick={() => void createStoryBoardFromCurrentTask()}
      >
        StoryBoard
      </button>
      <button
        class={`icon-action ocrebuild-shot-global-mode is-${allShotPreviewMode() === "asset" ? "asset" : "source"}`}
        type="button"
        title={isStoryboardReferencePhase2() ? "StoryBoard Phase 2 仅使用新素材" : (allShotPreviewMode() === "asset" ? "当前新素材，点击切到原视频" : "当前原视频，点击切到新素材")}
        aria-label={isStoryboardReferencePhase2() ? "StoryBoard Phase 2 仅使用新素材" : (allShotPreviewMode() === "asset" ? "切换所有 Shot 到原视频" : "切换所有 Shot 到新素材")}
        disabled={!shotPlanShots().length || isStoryboardReferencePhase2()}
        onClick={() => setAllShotMode(allShotPreviewMode() === "asset" ? "source" : "asset")}
      >
        <ShotPreviewModeIcon mode={allShotPreviewMode() === "asset" ? "asset" : "source"} />
        <span>{allShotPreviewMode() === "asset" ? "新素材" : "原视频"}</span>
      </button>
      <div class="openclip-view-menu-wrap">
        <button class="secondary" type="button" onClick={() => setShotViewMenuOpen((value) => !value)}>{shotViewMode() === "list" ? "List" : `Card - ${shotCardColumns()}`}</button>
        <Show when={shotViewMenuOpen()}><div class="openclip-view-menu ocrebuild-shot-view-menu">
          <button class={`openclip-view-menu-item ${shotViewMode() === "list" ? "is-active" : ""}`} type="button" onClick={() => { setShotViewMode("list"); setShotViewMenuOpen(false); }}>List</button>
          <For each={[3, 4, 5, 6, 7]}>{(count) => <button class={`openclip-view-menu-item ${shotViewMode() === "card" && shotCardColumns() === count ? "is-active" : ""}`} type="button" onClick={() => { setShotViewMode("card"); setShotCardColumns(count); setShotViewMenuOpen(false); }}>Card - {count}</button>}</For>
        </div></Show>
      </div>
    </div>
  </div>;

  const renderShotList = () => <div class={`ocrebuild-shot-list is-${shotViewMode()}`} style={shotViewMode() === "card" ? { "grid-template-columns": `repeat(${shotCardColumns()}, minmax(0, 1fr))` } : {}}>
    <For each={shotPlanShots()}>{(shot, index) => <article class={`ocrebuild-shot-card ${selectedShotIndex() === index() ? "is-active" : ""}`} onClick={() => setSelectedShotIndex(index())}>
      <div class="ocrebuild-shot-card-head"><strong>{shot.shot_id}</strong><select class="ocrebuild-shot-source-select" value={shotMode(shot.shot_id)} disabled={isStoryboardReferencePhase2()} onClick={(event) => event.stopPropagation()} onChange={(event) => { event.stopPropagation(); setSelectedShotIndex(index()); setShotMode(shot.shot_id, event.currentTarget.value); }}><Show when={!isStoryboardReferencePhase2()}><option value="source">原视频</option></Show><option value="asset">新素材</option></select><button class="secondary ocrebuild-shot-detail-button" type="button" onClick={(event) => { event.stopPropagation(); setShotDetailTable(shot); }}>查看详情</button></div>
      <div class="ocrebuild-shot-card-body">
        <div><span>Summary</span><p>{fieldText(shot.ui_summary, ["what_happens", "title", "summary"])}</p></div>
        <div><span>Rebuild</span><p>{fieldText(shot.rebuild_direction, ["new_scene", "new_spoken_script", "direction"])}</p></div>
      </div>
      <div class="ocrebuild-shot-card-tags"><span>{Number(shot.duration || 0).toFixed(2)}s</span><em>{shot.role || shot.formula_slot || "shot"}</em></div>
    </article>}</For>
  </div>;

  const renderAssetSceneCard = (pkg) => {
    const reference = pkg?.reference || {};
    const generationMode = sceneGenerationMode(pkg);
    const usesFirstLastAsset = generationMode === "first_last";
    const firstImageTask = assetTaskByType(pkg, ["image_regenerate_first", "image_regenerate_single"]);
    const lastImageTask = assetTaskByType(pkg, ["image_regenerate_last"]);
    const videoTask = assetTaskByType(pkg, ["first_last_image_to_video", "single_image_to_video"]);
    const compositeTask = assetTaskByType(pkg, ["video_composite"]);
    const planCVideo = planCVideoForPackage(pkg);
    const shotFinalVideo = latestShotFinalVideoForPackage(pkg);
    const displayVideo = shotFinalVideo || planCVideo;
    const planCPoster = fallbackAssetImageUrl(pkg, "first", firstImageTask) || generatedAssetImageUrl(firstImageTask) || (reference.first_frame || reference.single_frame ? referenceFrameUrl(reference.first_frame || reference.single_frame) : "");
    const renderAssetThumb = (label, role, src, output, isGenerated = false, assetTask = null) => {
      const displaySrc = usableAssetImageUrl(src);
      return <button class={`ocrebuild-asset-thumb ${isGenerated ? "is-generated" : "is-reference"}`} type="button" onClick={() => isGenerated && displaySrc ? openAssetImageViewer(displaySrc, `${pkg?.scene_mark_id || pkg?.shot_id} · ${label}`) : setAssetPromptDialog({ pkg, role })} onContextMenu={(event) => { if (!isGenerated) return; event.preventDefault(); event.stopPropagation(); setAssetImageContextMenu({ x: event.clientX, y: event.clientY, pkg, role, assetTask }); }}><Show when={displaySrc} fallback={<i>{isGenerated ? "等待生成" : output || "等待生成"}</i>}><img src={displaySrc} loading={isGenerated ? "eager" : "lazy"} onError={() => setMissingAssetImages((prev) => ({ ...prev, [displaySrc]: true }))} /></Show><span>{label}</span></button>;
    };
    return <article class="ocrebuild-asset-scene-card">
      <div class="ocrebuild-asset-scene-head"><strong>{pkg?.scene_mark_id || pkg?.shot_id}</strong><span class="ocrebuild-asset-scene-mode">{usesFirstLastAsset ? "首/尾" : "首帧"}</span></div>
      <div class="ocrebuild-asset-scene-meta">{pkg?.srt_text || "No SRT"}</div>
      <div class="ocrebuild-asset-media-grid">
        {renderAssetThumb("原图 首", pkg?.mode === "single" ? "single" : "first", reference.first_frame || reference.single_frame ? referenceFrameUrl(reference.first_frame || reference.single_frame) : "", "Missing reference")}
        {renderAssetThumb("新图 首", pkg?.mode === "single" ? "single" : "first", fallbackAssetImageUrl(pkg, "first", firstImageTask) || generatedAssetImageUrl(firstImageTask), firstImageTask?.output || "等待生成", true, firstImageTask)}
        <Show when={usesFirstLastAsset && pkg?.mode !== "single"}>{renderAssetThumb("原图 尾", "last", reference.last_frame ? referenceFrameUrl(reference.last_frame) : "", "Missing reference")}</Show>
        <Show when={usesFirstLastAsset && pkg?.mode !== "single"}>{renderAssetThumb("新图 尾", "last", generatedAssetImageUrl(lastImageTask) || fallbackAssetImageUrl(pkg, "last", lastImageTask), lastImageTask?.output || "等待生成", true, lastImageTask)}</Show>
      </div>
      <Show when={shotFinalVideo || !planCVideo?.hidden} fallback={<div class="ocrebuild-asset-video-slot is-empty"><span>Video</span></div>}>
        <div class={`ocrebuild-asset-video-slot ${displayVideo?.src ? "has-video" : ""}`} onClick={(event) => event.stopPropagation()} onPointerDown={(event) => event.stopPropagation()} onContextMenu={(event) => { event.preventDefault(); event.stopPropagation(); setAssetVideoContextMenu({ x: event.clientX, y: event.clientY, pkg, videoTask, compositeTask }); }}><span>Video</span><Show when={displayVideo?.src} fallback={<Show when={compositeTask?.status === "completed" || videoTask?.status === "completed"} fallback={<p>等待生成视频资产</p>}><p>视频资产已生成</p></Show>}><><video controls playsinline preload="auto" poster={planCPoster} src={displayVideo?.src} onClick={(event) => event.stopPropagation()} onPointerDown={(event) => event.stopPropagation()} onError={() => displayVideo?.output && setMissingShotAssetVideos((prev) => ({ ...prev, [displayVideo.output]: true }))} /><button class="ocrebuild-asset-video-play" type="button" title="播放/暂停视频" aria-label="播放/暂停视频" onClick={(event) => { event.stopPropagation(); const video = event.currentTarget.parentElement?.querySelector("video"); if (!video) return; if (video.paused) void video.play(); else video.pause(); }}><PlayIcon /></button></></Show></div>
      </Show>
    </article>;
  };
  const renderAssetSidebar = () => {
    const shot = selectedShot();
    if (!shot) return null;
    return <aside class="ocrebuild-shot-detail ocrebuild-asset-detail">
    <div class="ocrebuild-asset-scene-list"><For each={selectedShotAssetPackages()}>{(pkg) => renderAssetSceneCard(pkg)}</For></div>
  </aside>;
  };
  const renderSourceSidebar = () => {
    const shot = selectedShot();
    if (!shot) return null;
    return <aside class="ocrebuild-shot-detail">
    <div class="ocrebuild-shot-detail-head"><strong>{shot.shot_id}</strong><span>{shot.source_segment_id}</span></div>
    <Show when={shotReferenceVideoSource(shot)}><video controls preload="metadata" src={shotReferenceVideoSource(shot)} onLoadedMetadata={(event) => syncShotReferenceVideoTime(event.currentTarget, shot)} onTimeUpdate={(event) => syncShotReferenceVideoTime(event.currentTarget, shot)} /></Show>
    <Show when={editableKeyframes(shot).length} fallback={<div class="ocrebuild-shot-empty ocrebuild-keyframe-empty"><strong>No candidate frames selected</strong><span>Use restore to bring back original Shot Plan candidates.</span></div>}>
      <div class="ocrebuild-keyframe-strip"><For each={editableKeyframes(shot)}>{(frame, index) => <div ref={(el) => attachKeyframeContextMenu(el, shot, frame, index())} class={`ocrebuild-keyframe-item ${frame.scene_mark ? "has-scene-mark" : ""}`} onContextMenu={(event) => openKeyframeContextMenu(event, shot, frame, index())}><a href={shotAssetUrl(frame.path)} target="_blank" title={`${frame.time ?? ""}s`} onContextMenu={(event) => openKeyframeContextMenu(event, shot, frame, index())} onClick={(event) => openKeyframePrimaryAction(event, shot, frame, index())}><img src={shotAssetUrl(frame.path)} loading="lazy" onContextMenu={(event) => openKeyframeContextMenu(event, shot, frame, index())} /><Show when={frame.scene_mark}><span class={`ocrebuild-scene-mark-badge is-${frame.scene_mark.role || "mark"}`}>{frame.scene_mark.role === "first" ? "首" : frame.scene_mark.role === "last" ? "尾" : "单"}</span></Show></a><button class="ocrebuild-keyframe-remove" type="button" title="从 Shot Plan 移除候选帧，不删除 Analysis 源文件" onClick={(event) => { event.preventDefault(); event.stopPropagation(); deleteKeyframe(shot, index()); }}><CloseIcon /></button></div>}</For></div>
    </Show>
  </aside>;
  };
  const renderShotAssetControls = () => {
    const shot = selectedShot();
    const mode = shotGenerationMode(shot);
    const nextMode = mode === "first_last" ? "first_frame" : "first_last";
    return <div class="ocrebuild-keyframe-actions ocrebuild-shot-asset-actions">
      <button class="icon-action ocrebuild-shot-prompt-entry" type="button" title="查看/编辑最终提示词" aria-label="查看/编辑最终提示词" disabled={!shot?.shot_id || busy() === "shot-final-prompts"} onClick={() => void openShotPromptPackageDialog(shot)}><PromptPackageIcon /></button>
      <button class="icon-action ocrebuild-shot-mode-icon" type="button" title={mode === "first_last" ? "当前首/尾，点击切到首帧" : "当前首帧，点击切到首/尾"} aria-label={mode === "first_last" ? "切到首帧" : "切到首尾帧"} disabled={busy() === "shot-generation-mode"} onClick={() => void runAction("shot-generation-mode", () => saveShotGenerationMode(shot, nextMode))}><FrameModeIcon mode={mode} /></button>
      <button class="icon-action ocrebuild-shot-tts-entry" type="button" title="TTS 实验室" aria-label="TTS 实验室" disabled={busy().startsWith("asset-tts-")} onClick={() => void openAssetTTSWorkflow()}><span>TTS</span></button>
      <button class="icon-action ocrebuild-shot-r2v-entry" type="button" title="多图参考工作流" aria-label="多图参考工作流" disabled={busy().startsWith("shot-r2v-")} onClick={() => void openShotMultiReferenceWorkflow()}><DocumentIcon /></button>
    </div>;
  };
  const renderShotPlanSidebar = () => <section class="panel ocrebuild-shot-sidebar-panel">
    <div class="ocrebuild-shot-sidebar-head"><h3>Shot Detail</h3><Show when={selectedShot()}><Show when={selectedShotPreviewMode() === "asset"} fallback={<div class="ocrebuild-keyframe-actions"><button class="icon-action" type="button" title="撤销本次移除" disabled={!keyframeEditState(selectedShot())?.deleted?.length} onClick={() => undoKeyframeDelete(selectedShot())}><UndoIcon /></button><button class="icon-action" type="button" title="恢复原始候选帧" disabled={!keyframeEditState(selectedShot())?.dirty && !(selectedShot()?.reference?.original_keyframes || []).length} onClick={() => restoreAllKeyframes(selectedShot())}><ArrowsClockwiseIcon /></button><button class="icon-action success" type="button" title="保存 Shot Plan 候选帧" disabled={!keyframeEditState(selectedShot())?.dirty || busy() === "keyframes"} onClick={() => void runAction("keyframes", () => saveSelectedShotKeyframes(selectedShot()))}><SaveIcon /></button></div>}>{renderShotAssetControls()}</Show></Show></div>
    <Show when={selectedShot()} fallback={<div class="ocrebuild-shot-empty"><strong>No shot selected</strong><span>Select a shot from the Shot Plan list.</span></div>}>
      <Show when={selectedShotPreviewMode() === "asset"} fallback={renderSourceSidebar()}>{renderAssetSidebar()}</Show>
    </Show>
  </section>;
  const renderShotPromptPackageDialog = () => {
    const scene = () => selectedPromptPackageScene();
    return (
      <Show when={shotPromptPackageDialog()}>
      <div class="ocrebuild-modal-root">
        <div class="drawer-backdrop openclip-model-overlay" onClick={() => setShotPromptPackageDialog(null)} />
        <section class="verify-dialog openclip-prompt-preview-dialog ocrebuild-shot-prompt-package-dialog">
          <div class="env-dialog-head">
            <div><h3>{shotPromptPackageDialog()?.shot?.shot_id || "Shot Prompts"}</h3></div>
            <div class="ocrebuild-dialog-head-actions">
              <button class="icon-action success" type="button" title="保存最终提示词" aria-label="保存最终提示词" disabled={shotPromptPackageDialog()?.loading || shotPromptPackageDialog()?.saving} onClick={() => void saveShotPromptPackageDialog()}><SaveIcon /></button>
              <button class="secondary ocrebuild-compare-close-button" type="button" title="关闭" aria-label="关闭" onClick={() => setShotPromptPackageDialog(null)}><CloseIcon /></button>
            </div>
          </div>
          <Show when={!shotPromptPackageDialog()?.loading} fallback={<div class="ocrebuild-shot-empty"><strong>Loading prompts</strong><span>{shotPromptPackageDialog()?.shot?.shot_id}</span></div>}>
            <div class="ocrebuild-shot-prompt-package-body">
              <Show when={shotPromptPackageDialog()?.error}><div class="ocrebuild-srt-tts-error">{shotPromptPackageDialog()?.error}</div></Show>
              <div class="ocrebuild-shot-prompt-tabs" role="tablist" aria-label="Shot prompt package sections">
                <button class={shotPromptPackageTab() === "image" ? "is-active" : ""} type="button" role="tab" aria-selected={shotPromptPackageTab() === "image"} onClick={() => setShotPromptPackageTab("image")}>Image</button>
                <button class={shotPromptPackageTab() === "tts" ? "is-active" : ""} type="button" role="tab" aria-selected={shotPromptPackageTab() === "tts"} onClick={() => setShotPromptPackageTab("tts")}>TTS</button>
                <button class={shotPromptPackageTab() === "video" ? "is-active" : ""} type="button" role="tab" aria-selected={shotPromptPackageTab() === "video"} onClick={() => setShotPromptPackageTab("video")}>Video</button>
              </div>
              <Show when={shotPromptPackageTab() === "image"}>
                <section class="ocrebuild-shot-prompt-section">
                  <div class="ocrebuild-shot-prompt-section-head"><strong>Image</strong><span>{scene()?.scene_mark_id || ""}</span></div>
                  <Show when={(shotPromptPackageDialog()?.package?.scenes || []).length > 1}><label class="openflow-field"><span>Scene</span><select value={shotPromptPackageDialog()?.sceneId || ""} onChange={(event) => updateShotPromptPackageDraft({ sceneId: event.currentTarget.value })}><For each={shotPromptPackageDialog()?.package?.scenes || []}>{(item) => <option value={item.scene_mark_id}>{item.scene_mark_id}</option>}</For></select></label></Show>
                  <Show when={imagePromptBuildMessages().length}>
                    <div class="ocrebuild-shot-prompt-blocker">
                      <strong>Plan D Image Prompt Blocked</strong>
                      <For each={imagePromptBuildMessages()}>{(message) => <span>{message}</span>}</For>
                    </div>
                  </Show>
                  <label class="openflow-field"><span>Image Prompt</span><textarea class="skill-editor ocrebuild-shot-prompt-textarea is-image" rows="12" value={actualImageModelPrompt(scene())} onInput={(event) => updateActualImageModelPrompt(event.currentTarget.value)} /></label>
                </section>
              </Show>
              <Show when={shotPromptPackageTab() === "tts"}>
                <section class="ocrebuild-shot-prompt-section">
                  <div class="ocrebuild-shot-prompt-section-head"><strong>TTS</strong></div>
                  <label class="openflow-field"><span>Speed</span><textarea class="skill-editor ocrebuild-shot-prompt-speed" value={Array.isArray(shotPromptPackageDialog()?.package?.tts_speed_notes) ? shotPromptPackageDialog()?.package?.tts_speed_notes.join("\n") : String(shotPromptPackageDialog()?.package?.tts_speed_notes || "")} onInput={(event) => updateShotPromptPackageTTS({ tts_speed_notes: event.currentTarget.value.split(/[、,，\n]+/).map((item) => item.trim()).filter(Boolean) })} /></label>
                  <label class="openflow-field"><span>TTS Prompt</span><textarea class="skill-editor ocrebuild-shot-prompt-textarea" value={shotPromptPackageDialog()?.package?.tts_prompt || ""} onInput={(event) => updateShotPromptPackageTTS({ tts_prompt: event.currentTarget.value })} /></label>
                </section>
              </Show>
              <Show when={shotPromptPackageTab() === "video"}>
                <section class="ocrebuild-shot-prompt-section">
                  <div class="ocrebuild-shot-prompt-section-head"><strong>Video</strong><span>{scene()?.scene_mark_id || ""}</span></div>
                  <Show when={(shotPromptPackageDialog()?.package?.scenes || []).length > 1}><label class="openflow-field"><span>Scene</span><select value={shotPromptPackageDialog()?.sceneId || ""} onChange={(event) => updateShotPromptPackageDraft({ sceneId: event.currentTarget.value })}><For each={shotPromptPackageDialog()?.package?.scenes || []}>{(item) => <option value={item.scene_mark_id}>{item.scene_mark_id}</option>}</For></select></label></Show>
                  <label class="openflow-field"><span>Video Prompt</span><textarea class="skill-editor ocrebuild-shot-prompt-textarea is-video" rows="12" value={actualVideoModelPrompt(scene())} onInput={(event) => updateActualVideoModelPrompt(event.currentTarget.value)} /></label>
                </section>
              </Show>
              <div class="field-row openflow-model-dialog-actions openclip-model-dialog-actions"><button class="secondary" type="button" onClick={() => setShotPromptPackageDialog(null)}>Cancel</button><button class="openclip-model-confirm" type="button" disabled={shotPromptPackageDialog()?.saving} onClick={() => void saveShotPromptPackageDialog()}>{shotPromptPackageDialog()?.saving ? "Saving..." : "Save Prompts"}</button></div>
            </div>
          </Show>
        </section>
      </div>
      </Show>
    );
  };

  createEffect(() => { const taskId = taskIdFromHash(props.routeHash); if (taskId && taskId !== selectedTaskId()) void loadTask(taskId); });
  createEffect(() => { if (task()?.session_id) void loadShotPlan(); });
  createEffect(() => {
    const shots = shotPlanShots();
    if (isStoryboardReferencePhase2() && shots.length) {
      setGlobalShotPreviewMode("asset");
      setShotPreviewMode(Object.fromEntries(shots.map((shot) => [shot.shot_id, "asset"])));
    }
  });
  createEffect(() => { const plan = shotPlan(); if (plan?.shots?.length) untrack(() => plan.shots.forEach((shot) => void loadSourceSrtText(shot))); });
  createEffect(() => { const shot = selectedShot(); if (shot) untrack(() => void checkLockedTTSFile(shot)); });
  createEffect(() => { const shot = selectedShot(); const mode = selectedShotPreviewMode(); if (shot && mode === "asset") untrack(() => { void loadAssetTasks(); void loadShotAssetPrompts(shot.shot_id); void loadPlanCScenePlan(shot.shot_id); }); });
  createEffect(() => props.onSidebarChange?.(renderShotPlanSidebar()));
  onMount(async () => {
    const onTaskList = () => setTaskListOpen(true);
    const onNewTask = () => void runAction("newTask", createTask);
    window.addEventListener("ocrebuild:task-list", onTaskList);
    window.addEventListener("ocrebuild:new-task", onNewTask);
    onCleanup(() => { window.removeEventListener("ocrebuild:task-list", onTaskList); window.removeEventListener("ocrebuild:new-task", onNewTask); });
    await loadTasks();
    const hashTaskId = taskIdFromHash(window.location.hash);
    if (hashTaskId) {
      await loadTask(hashTaskId);
      return;
    }
    const firstTaskId = tasks()[0]?.id ?? null;
    if (firstTaskId) window.location.hash = api.taskDetailUrl(firstTaskId);
  });
  onCleanup(() => props.onSidebarChange?.(null));

  const renderComparePromptCard = (workflowId, itemAccessor, index, round = 1) => {
    const item = () => typeof itemAccessor === "function" ? itemAccessor() : itemAccessor;
    const itemIndex = () => typeof index === "function" ? index() : index;
    return <article class="ocrebuild-compare-card">
    <select class="ocrebuild-compare-model-select" value={item().model || ""} onChange={(event) => round === 1 ? updateProviderPrompt(workflowId, itemIndex(), { model: event.currentTarget.value }) : updateRefinePrompt(workflowId, itemIndex(), { model: event.currentTarget.value })}>
      <For each={item().models?.length ? item().models : [{ model: item().model, label: item().model }]}>{(model) => <option value={model.model}>{model.label || model.model}</option>}</For>
    </select>
    <input class="ocrebuild-compare-short-input" value={item().userInstruction || ""} placeholder="例如：更真实，更黄，减少 AI 感" onInput={(event) => { item().userInstruction = event.currentTarget.value; }} />
    <button class="ocrebuild-compare-refine-button" type="button" disabled={item().refining} onClick={(event) => { const card = event.currentTarget.closest(".ocrebuild-compare-card"); void refineProviderPrompt(workflowId, itemIndex(), round, { model: card?.querySelector("select")?.value || item().model, userInstruction: card?.querySelector("input")?.value || "", currentPrompt: card?.querySelector("textarea")?.value || item().currentPrompt }); }}>{item().refining ? "优化中..." : "优化提示词"}</button>
    <Show when={item().error}><p class="ocrebuild-compare-error">{item().error}</p></Show>
    <textarea class="ocrebuild-compare-prompt-editor" value={item().currentPrompt || ""} onInput={(event) => { item().currentPrompt = event.currentTarget.value; }} />
  </article>;
  };
  const renderCompareCandidate = (workflowId, candidate, final = false, selected = false) => <article class={`ocrebuild-compare-result ${candidate.status === "failed" ? "is-failed" : ""} ${selected ? "is-selected" : ""}`}>
    <div class="ocrebuild-compare-card-head"><strong title={displayImageModelName(candidate)}>{displayImageModelName(candidate)}</strong><span>{selected ? "已选中" : candidate.elapsedSeconds ? `${candidate.elapsedSeconds}s` : candidate.status}</span></div>
    <Show when={candidate.status !== "failed"} fallback={<p class="ocrebuild-compare-error">{candidate.error || "Generation failed"}</p>}>
      <button class="ocrebuild-compare-image" type="button" onClick={() => openAssetImageViewer(candidate.src, `${candidate.provider}/${candidate.model}`)}><img src={candidate.src} /></button>
      <Show when={final} fallback={<><button type="button" onClick={() => void selectCompareCandidate(workflowId, candidate)}>选择这张进入第二轮</button><button type="button" onClick={() => void finalizeCompareCandidate(workflowId, candidate)}>直接使用这张</button></>}><button type="button" onClick={() => void finalizeCompareCandidate(workflowId, candidate)}>确认使用这张</button></Show>
    </Show>
  </article>;
  const renderStepPlayButton = (workflowId, round, title, disabled = false) => <button class="ocrebuild-compare-play-button is-step-action" type="button" title={title} aria-label={title} disabled={disabled || busy().startsWith("asset-compare-")} onClick={(event) => { event.stopPropagation(); void generateCompareRound(workflowId, round); }}><PlayIcon /></button>;
  const renderSinglePlayButton = (workflowId, disabled = false) => <button class="ocrebuild-compare-play-button is-step-action" type="button" title="确认并生成" aria-label="确认并生成" disabled={disabled || busy().startsWith("asset-compare-")} onClick={(event) => { event.stopPropagation(); void generateSingleRound(workflowId); }}><PlayIcon /></button>;
  const renderCompareStepHeader = (workflowId, key, stepNo, summary, action = null) => {
    const expanded = Boolean(compareExpandedStepsFor(workflowId)[key]);
    return <section class={`ocrebuild-step-summary ${expanded ? "is-expanded" : ""}`}>
      <div class="ocrebuild-step-summary-row">
        <button class="ocrebuild-step-summary-main" type="button" onClick={() => updateCompareExpandedSteps(workflowId, (prev) => ({ ...prev, [key]: !prev[key] }))}><strong class="ocrebuild-step-number">{stepNo}</strong><span>{summary}</span></button>
        <div class="ocrebuild-step-summary-actions">{action}<button class="secondary ocrebuild-step-icon-button" type="button" title={expanded ? "收起" : "展开"} aria-label={expanded ? "收起" : "展开"} onClick={() => updateCompareExpandedSteps(workflowId, (prev) => ({ ...prev, [key]: !prev[key] }))}>{expanded ? <ChevronUpIcon /> : <ChevronDownIcon />}</button></div>
      </div>
    </section>;
  };
  const renderSingleVariantSelect = (wf) => <div class="ocrebuild-refine-toolbar"><span>生成图片数量</span><select value={String(wf.variantCount || 1)} onChange={(event) => updateAssetCompareWorkflow(wf.workflowId, (prev) => prev ? { ...prev, variantCount: Number(event.currentTarget.value) || 1 } : prev)}><option value="1">1</option><option value="2">2</option><option value="3">3</option></select></div>;
  const renderSingleStepSummaries = (wf) => <div class="ocrebuild-step-stack">
    {renderCompareStepHeader(wf.workflowId, "mode", 1, `生成模式选择：${compareModeLabel(wf.mode)}`)}
    <Show when={compareExpandedStepsFor(wf.workflowId).mode}><div class="ocrebuild-step-expanded"><div class="ocrebuild-compare-mode is-inline"><button type="button" onClick={() => void startAssetSingleMode(wf.workflowId, "with_reference")}>使用原图参考</button><button type="button" onClick={() => void startAssetSingleMode(wf.workflowId, "prompt_only")}>仅使用提示词</button></div></div></Show>
    <Show when={wf.providerPrompts?.length}>
      {renderCompareStepHeader(wf.workflowId, "prompts", 2, `Prompt：${wf.providerPrompts[0]?.providerLabel || wf.providerPrompts[0]?.provider || "默认 Provider"} / ${wf.providerPrompts[0]?.model || "默认模型"}`, renderSinglePlayButton(wf.workflowId, !wf.providerPrompts.length))}
      <Show when={compareExpandedStepsFor(wf.workflowId).prompts}><div class="ocrebuild-step-expanded">{renderSingleVariantSelect(wf)}<div class="ocrebuild-compare-grid"><Index each={wf.providerPrompts}>{(item, index) => renderComparePromptCard(wf.workflowId, item, index, 1)}</Index></div></div></Show>
    </Show>
    <Show when={wf.providerPrompts?.length || wf.candidates?.length}>
      {renderCompareStepHeader(wf.workflowId, "firstRound", 3, wf.final?.selected_output ? `已选择：${wf.final.model}` : wf.candidates?.length ? `选择一个选中的图片：${wf.candidates.length} 张候选` : "选择一个选中的图片：等待生成")}
      <Show when={compareExpandedStepsFor(wf.workflowId).firstRound}><div class="ocrebuild-step-expanded"><div class="ocrebuild-compare-grid"><For each={wf.candidates}>{(candidate) => renderCompareCandidate(wf.workflowId, candidate, true, wf.final?.selected_output === candidate.output)}</For></div></div></Show>
    </Show>
  </div>;
  const renderCompareStepSummaries = (wf) => <div class="ocrebuild-step-stack">
    {renderCompareStepHeader(wf.workflowId, "mode", 1, `生成模式：${compareModeLabel(wf.mode)}`)}
    <Show when={compareExpandedStepsFor(wf.workflowId).mode}><div class="ocrebuild-step-expanded"><div class="ocrebuild-compare-mode is-inline"><button type="button" onClick={() => void startAssetCompareMode(wf.workflowId, "with_reference")}>使用原图参考</button><button type="button" onClick={() => void startAssetCompareMode(wf.workflowId, "prompt_only")}>仅用提示词</button></div></div></Show>
    <Show when={wf.providerPrompts?.length}>
      {renderCompareStepHeader(wf.workflowId, "prompts", 2, `Prompt：${wf.providerPrompts.length} 个模型`, renderStepPlayButton(wf.workflowId, 1, "确认并生成3张", !wf.providerPrompts.length))}
      <Show when={compareExpandedStepsFor(wf.workflowId).prompts}><div class="ocrebuild-step-expanded"><div class="ocrebuild-compare-grid"><Index each={wf.providerPrompts}>{(item, index) => renderComparePromptCard(wf.workflowId, item, index, 1)}</Index></div></div></Show>
    </Show>
    <Show when={wf.candidates?.length || wf.selectedCandidate}>
      {renderCompareStepHeader(wf.workflowId, "firstRound", 3, wf.selectedCandidate ? `第一轮选择：${wf.selectedCandidate.model}` : `第一轮结果：${wf.candidates.length} 张`)}
      <Show when={compareExpandedStepsFor(wf.workflowId).firstRound}><div class="ocrebuild-step-expanded"><div class="ocrebuild-compare-grid"><For each={wf.candidates}>{(candidate) => renderCompareCandidate(wf.workflowId, candidate, false)}</For></div></div></Show>
    </Show>
    <Show when={wf.selectedCandidate && wf.refinePrompts?.length}>
      {renderCompareStepHeader(wf.workflowId, "refinePrompts", 4, `第二轮 Prompt：${Math.min(wf.variantCount || 3, wf.refinePrompts.length)} 个变体`, renderStepPlayButton(wf.workflowId, 2, "确认并生成"))}
      <Show when={compareExpandedStepsFor(wf.workflowId).refinePrompts}><div class="ocrebuild-step-expanded"><div class="ocrebuild-refine-toolbar"><span>第二轮生成参数</span><select value={String(wf.variantCount || 3)} onChange={(event) => updateAssetCompareWorkflow(wf.workflowId, (prev) => prev ? { ...prev, variantCount: Number(event.currentTarget.value) || 3 } : prev)}><option value="1">1</option><option value="2">2</option><option value="3">3</option></select></div><div class="ocrebuild-compare-grid"><Index each={wf.refinePrompts.slice(0, wf.variantCount || 3)}>{(item, index) => renderComparePromptCard(wf.workflowId, item, index, 2)}</Index></div></div></Show>
    </Show>
    <Show when={wf.refineCandidates?.length}>
      {renderCompareStepHeader(wf.workflowId, "secondRound", 5, `第二轮结果：${wf.refineCandidates.length} 张`)}
      <Show when={compareExpandedStepsFor(wf.workflowId).secondRound}><div class="ocrebuild-step-expanded"><div class="ocrebuild-compare-grid"><For each={wf.refineCandidates}>{(candidate) => renderCompareCandidate(wf.workflowId, candidate, true, wf.final?.selected_output === candidate.output)}</For></div></div></Show>
    </Show>
  </div>;
  const renderAssetCompareWorkflow = (wf) => {
    const pos = () => assetCompareDialogPositions()[wf.workflowId];
    return <section class="verify-dialog ocrebuild-compare-dialog" data-workflow-id={wf.workflowId} classList={{ "is-dragged": Boolean(pos()) }} style={pos() ? { left: `${pos().left}px`, top: `${pos().top}px`, transform: "none" } : {}}>
      <div class="env-dialog-head ocrebuild-compare-head" onMouseDown={(event) => startAssetCompareDialogDrag(wf.workflowId, event)}><div class="ocrebuild-compare-title"><span class="ocrebuild-compare-title-icon"><DocumentIcon /></span><h3>{wf.single ? "单图工作流" : "多图工作流"}</h3><span class="ocrebuild-compare-title-meta">{wf.menu?.pkg?.shot_id} / {wf.menu?.pkg?.scene_mark_id || "-"} / {wf.menu?.role}</span></div><button class="secondary ocrebuild-compare-close-button" type="button" title="关闭" aria-label="关闭" onClick={() => closeAssetCompareWorkflow(wf.workflowId)}><CloseIcon /></button></div>
      <div class="ocrebuild-compare-scroll-body">
        <Show when={wf.error}><div class="banner bad openclip-banner">{wf.error}</div></Show>
        <Show when={wf.single} fallback={<>
          {renderCompareStepSummaries(wf)}
          <Show when={wf.phase === "prompts" && !compareExpandedStepsFor(wf.workflowId).prompts}><div class="ocrebuild-compare-body"><div class="ocrebuild-compare-grid"><Index each={wf.providerPrompts}>{(item, index) => renderComparePromptCard(wf.workflowId, item, index, 1)}</Index></div></div></Show>
          <Show when={wf.phase === "generating"}><div class="ocrebuild-compare-status-line"><strong>第一轮生成中</strong><span>API 调用、心跳和日志正在写入 Debug Console。</span></div></Show>
          <Show when={wf.phase === "select"}><div class="ocrebuild-compare-body"><div class="ocrebuild-compare-grid"><For each={wf.candidates}>{(candidate) => renderCompareCandidate(wf.workflowId, candidate, false)}</For></div></div></Show>
          <Show when={wf.phase === "refine_prompts" && !compareExpandedStepsFor(wf.workflowId).refinePrompts && !wf.refineCandidates?.length}><div class="ocrebuild-compare-body"><div class="ocrebuild-refine-toolbar"><span>第二轮生成参数</span><select value={String(wf.variantCount || 3)} onChange={(event) => updateAssetCompareWorkflow(wf.workflowId, (prev) => prev ? { ...prev, variantCount: Number(event.currentTarget.value) || 3 } : prev)}><option value="1">1</option><option value="2">2</option><option value="3">3</option></select></div><div class="ocrebuild-compare-grid"><Index each={wf.refinePrompts.slice(0, wf.variantCount || 3)}>{(item, index) => renderComparePromptCard(wf.workflowId, item, index, 2)}</Index></div></div></Show>
          <Show when={wf.phase === "refine_generating"}><div class="ocrebuild-compare-status-line"><strong>第二轮生成中</strong><span>同一模型多变体并行调用中。</span></div></Show>
          <Show when={wf.phase === "final_select"}><div class="ocrebuild-compare-body"><div class="field-row ocrebuild-compare-actions"><button class="secondary" type="button" onClick={() => resetCompareToRefinePromptsForEdit(wf.workflowId)}>返回上一层重新更新提示词</button></div><div class="ocrebuild-compare-grid"><For each={wf.refineCandidates}>{(candidate) => renderCompareCandidate(wf.workflowId, candidate, true, wf.final?.selected_output === candidate.output)}</For></div></div></Show>
        </>}>
          {renderSingleStepSummaries(wf)}
          <Show when={wf.phase === "prompts" && !compareExpandedStepsFor(wf.workflowId).prompts}><div class="ocrebuild-compare-body">{renderSingleVariantSelect(wf)}<div class="ocrebuild-compare-grid"><Index each={wf.providerPrompts}>{(item, index) => renderComparePromptCard(wf.workflowId, item, index, 1)}</Index></div></div></Show>
          <Show when={wf.phase === "generating"}><div class="ocrebuild-compare-status-line"><strong>图片生成中</strong><span>默认 Provider 正在生成候选图片。</span></div></Show>
          <Show when={wf.phase === "select" && !compareExpandedStepsFor(wf.workflowId).firstRound}><div class="ocrebuild-compare-body"><div class="ocrebuild-compare-grid"><For each={wf.candidates}>{(candidate) => renderCompareCandidate(wf.workflowId, candidate, true, wf.final?.selected_output === candidate.output)}</For></div></div></Show>
          <Show when={wf.phase === "finalized"}><div class="ocrebuild-compare-status-line"><strong>已选择图片</strong><span>{wf.final?.provider}/{wf.final?.model}</span></div></Show>
        </Show>
      </div>
    </section>;
  };

  const renderVideoStepHeader = (workflowId, key, stepNo, summary, action = null) => {
    const expanded = Boolean(videoExpandedStepsFor(workflowId)[key]);
    return <section class={`ocrebuild-step-summary ${expanded ? "is-expanded" : ""}`}>
      <div class="ocrebuild-step-summary-row">
        <button class="ocrebuild-step-summary-main" type="button" onClick={() => updateVideoExpandedSteps(workflowId, (prev) => ({ ...prev, [key]: !prev[key] }))}><strong class="ocrebuild-step-number">{stepNo}</strong><span>{summary}</span></button>
        <div class="ocrebuild-step-summary-actions">{action}<button class="secondary ocrebuild-step-icon-button" type="button" title={expanded ? "收起" : "展开"} aria-label={expanded ? "收起" : "展开"} onClick={() => updateVideoExpandedSteps(workflowId, (prev) => ({ ...prev, [key]: !prev[key] }))}>{expanded ? <ChevronUpIcon /> : <ChevronDownIcon />}</button></div>
      </div>
    </section>;
  };
  const renderVideoPromptCard = (workflowId, itemAccessor, index, wf) => {
    const item = () => typeof itemAccessor === "function" ? itemAccessor() : itemAccessor;
    const itemIndex = () => typeof index === "function" ? index() : index;
    const selectedModel = () => (item().models || []).find((model) => model.model === item().model) || item().models?.[0] || {};
    const status = () => durationStatus(selectedModel(), item().duration || wf.duration);
    return <article class="ocrebuild-compare-card ocrebuild-video-prompt-card">
      <div class="ocrebuild-compare-card-head"><strong>{item().providerLabel || item().provider}</strong><span class={status().ok ? "" : "is-warning"}>{status().label}</span></div>
      <select class="ocrebuild-compare-model-select" value={item().model || ""} onChange={(event) => updateVideoPrompt(workflowId, itemIndex(), { model: event.currentTarget.value })}>
        <For each={item().models?.length ? item().models : [{ model: item().model, label: item().model }]}>{(model) => <option value={model.model}>{model.label || model.model}</option>}</For>
      </select>
      <input class="ocrebuild-compare-short-input" value={item().userInstruction || ""} placeholder="简单提示：更自然、更慢、更真实" onInput={(event) => { item().userInstruction = event.currentTarget.value; }} />
      <div class="ocrebuild-video-duration-row"><span>时长</span><input class="ocrebuild-video-duration-input" type="number" min="0.5" step="0.5" value={item().duration || wf.duration || ""} onInput={(event) => { item().duration = Number(event.currentTarget.value) || wf.duration; }} /><em>{selectedModel().duration?.adjustable ? "支持调整" : "固定/未知"}</em></div>
      <button class="ocrebuild-compare-refine-button" type="button" disabled={item().refining} onClick={(event) => { const card = event.currentTarget.closest(".ocrebuild-compare-card"); void refineVideoPrompt(workflowId, itemIndex(), { model: card?.querySelector("select")?.value || item().model, userInstruction: card?.querySelector("input")?.value || "", currentPrompt: card?.querySelector("textarea")?.value || item().currentPrompt, duration: Number(card?.querySelector(".ocrebuild-video-duration-input")?.value || item().duration || wf.duration || 0) || wf.duration }); }}>{item().refining ? "优化中..." : "优化视频提示词"}</button>
      <Show when={item().error}><p class="ocrebuild-compare-error">{item().error}</p></Show>
      <textarea class="ocrebuild-compare-prompt-editor" value={item().currentPrompt || ""} onInput={(event) => { item().currentPrompt = event.currentTarget.value; }} />
    </article>;
  };
  const renderVideoCandidate = (workflowId, candidate, selected = false) => <article class={`ocrebuild-compare-result ${candidate.status === "failed" ? "is-failed" : ""} ${selected ? "is-selected" : ""}`}>
    <Show when={candidate.status !== "failed"} fallback={<p class="ocrebuild-compare-error">{candidate.error || "Generation failed"}</p>}>
      <div class="ocrebuild-video-preview-wrap"><div class="ocrebuild-video-preview-meta"><strong title={`${candidate.provider}/${candidate.model}`}>{candidate.provider}/{candidate.model}</strong><span>{selected ? "已选中" : candidate.duration ? `${candidate.duration}s` : candidate.status}</span></div><button class="ocrebuild-video-overlay-confirm" type="button" onClick={() => void finalizeVideoCandidate(workflowId, candidate)}>确认使用</button><video class="ocrebuild-compare-video" controls preload="metadata" src={candidate.src} /></div>
    </Show>
  </article>;
  const renderVideoStepSummaries = (wf) => <div class="ocrebuild-step-stack">
    {renderVideoStepHeader(wf.workflowId, "mode", 1, `输入模式：${wf.inputMode === "first_last" ? "首尾帧" : wf.inputMode === "text" ? "文字" : "首帧"}`)}
    <Show when={videoExpandedStepsFor(wf.workflowId).mode}><div class="ocrebuild-step-expanded"><div class="ocrebuild-compare-mode is-inline"><button type="button" onClick={() => void startAssetVideoMode(wf.workflowId, "first_frame")}>首帧</button><button type="button" disabled={!wf.menu?.videoTask?.input?.last_image && wf.menu?.pkg?.mode !== "first_last"} onClick={() => void startAssetVideoMode(wf.workflowId, "first_last")}>首尾帧</button><button type="button" onClick={() => void startAssetVideoMode(wf.workflowId, "text")}>文字</button></div><p class="ocrebuild-video-duration-note">当前 scene 时长：{wf.duration ? `${wf.duration}s` : "未知"}</p></div></Show>
    <Show when={wf.providerPrompts?.length}>
      {renderVideoStepHeader(wf.workflowId, "prompts", 2, `Prompt：${wf.providerPrompts.length} 个模型`, <button class="ocrebuild-compare-play-button is-step-action" type="button" title="确认并生成视频" aria-label="确认并生成视频" disabled={!wf.providerPrompts.length || busy().startsWith("asset-video-compare-")} onClick={(event) => { event.stopPropagation(); void generateCompareVideos(wf.workflowId); }}><PlayIcon /></button>)}
      <Show when={videoExpandedStepsFor(wf.workflowId).prompts}><div class="ocrebuild-step-expanded"><div class="ocrebuild-compare-grid"><Index each={wf.providerPrompts}>{(item, index) => renderVideoPromptCard(wf.workflowId, item, index, wf)}</Index></div></div></Show>
    </Show>
    <Show when={wf.candidates?.length}>
      {renderVideoStepHeader(wf.workflowId, "results", 3, `视频选择：${wf.candidates.length} 个候选`)}
      <Show when={videoExpandedStepsFor(wf.workflowId).results}><div class="ocrebuild-step-expanded"><div class="ocrebuild-compare-grid"><For each={wf.candidates}>{(candidate) => renderVideoCandidate(wf.workflowId, candidate, wf.final?.selected_output === candidate.output)}</For></div></div></Show>
    </Show>
  </div>;
  const renderAssetVideoWorkflow = (wf) => {
    const pos = () => assetVideoDialogPositions()[wf.workflowId];
    return <section class="verify-dialog ocrebuild-compare-dialog ocrebuild-video-dialog" data-workflow-id={wf.workflowId} classList={{ "is-dragged": Boolean(pos()) }} style={pos() ? { left: `${pos().left}px`, top: `${pos().top}px`, transform: "none" } : {}}>
      <div class="env-dialog-head ocrebuild-compare-head" onMouseDown={(event) => startAssetVideoDialogDrag(wf.workflowId, event)}><div class="ocrebuild-compare-title"><span class="ocrebuild-compare-title-icon"><DocumentIcon /></span><h3>视频比对工作流</h3><span class="ocrebuild-compare-title-meta">{wf.menu?.pkg?.shot_id} / {wf.menu?.pkg?.scene_mark_id || "-"}</span></div><button class="secondary ocrebuild-compare-close-button" type="button" title="关闭" aria-label="关闭" onClick={() => closeAssetVideoWorkflow(wf.workflowId)}><CloseIcon /></button></div>
      <div class="ocrebuild-compare-scroll-body">
        <Show when={wf.error}><div class="banner bad openclip-banner">{wf.error}</div></Show>
        {renderVideoStepSummaries(wf)}
        <Show when={wf.phase === "prompts" && !videoExpandedStepsFor(wf.workflowId).prompts}><div class="ocrebuild-compare-body"><div class="ocrebuild-compare-grid"><Index each={wf.providerPrompts}>{(item, index) => renderVideoPromptCard(wf.workflowId, item, index, wf)}</Index></div></div></Show>
        <Show when={wf.phase === "generating"}><div class="ocrebuild-compare-status-line"><strong>视频生成中</strong><span>候选视频事件流正在写入 Debug Console。</span></div></Show>
        <Show when={wf.phase === "select" && !videoExpandedStepsFor(wf.workflowId).results}><div class="ocrebuild-compare-body"><div class="ocrebuild-compare-grid"><For each={wf.candidates}>{(candidate) => renderVideoCandidate(wf.workflowId, candidate, wf.final?.selected_output === candidate.output)}</For></div></div></Show>
      </div>
    </section>;
  };

  const renderTTSPromptCard = (workflowId, item, index) => {
    const updateModel = (modelId) => {
      const model = (item().models || []).find((entry) => entry.model === modelId) || {};
      updateTTSPrompt(workflowId, index, { model: modelId, voices: model.voices || item().voices || [], voiceId: model.voices?.[0]?.voice_id || item().voiceId || "" });
    };
    return <article class="ocrebuild-tts-model-card">
      <div class="ocrebuild-tts-model-head"><strong>{item().providerLabel || item().provider}</strong><span>{item().hasApiKey ? "API ready" : "API missing"}</span></div>
      <select class="ocrebuild-tts-model-select" value={item().model || ""} onChange={(event) => updateModel(event.currentTarget.value)}>
        <For each={item().models?.length ? item().models : [{ model: item().model, label: item().model }]}>{(model) => <option value={model.model}>{model.label || model.model}</option>}</For>
      </select>
      <select class="ocrebuild-tts-voice-select" value={item().voiceId || ""} onChange={(event) => updateTTSPrompt(workflowId, index, { voiceId: event.currentTarget.value })}>
        <For each={item().voices?.length ? item().voices : [{ voice_id: item().voiceId, label: item().voiceId }]}>{(voice) => <option value={voice.voice_id}>{voice.label || voice.voice_id}</option>}</For>
      </select>
      <input class="ocrebuild-compare-short-input" value={item().userInstruction || ""} placeholder="简单提示词：年轻女声、语速自然、短视频口播" onInput={(event) => { item().userInstruction = event.currentTarget.value; }} />
      <button class="ocrebuild-compare-refine-button" type="button" disabled={item().refining} onClick={(event) => { const card = event.currentTarget.closest(".ocrebuild-tts-model-card"); void refineTTSPrompt(workflowId, index, { model: card?.querySelector(".ocrebuild-tts-model-select")?.value || item().model, voiceId: card?.querySelector(".ocrebuild-tts-voice-select")?.value || item().voiceId, userInstruction: card?.querySelector("input")?.value || "", currentPrompt: card?.querySelector("textarea")?.value || item().currentPrompt }); }}>{item().refining ? "优化中..." : "生成 Final Prompt"}</button>
      <Show when={item().error}><p class="ocrebuild-compare-error">{item().error}</p></Show>
      <textarea class="ocrebuild-compare-prompt-editor ocrebuild-tts-prompt-editor" value={item().currentPrompt || ""} onInput={(event) => { item().currentPrompt = event.currentTarget.value; }} />
    </article>;
  };

  const renderShotTTSPromptCard = (workflowId, item, index) => {
    const updateModel = (modelId) => {
      const model = (item().models || []).find((entry) => entry.model === modelId) || {};
      updateShotTTSPrompt(workflowId, index, { model: modelId, voices: model.voices || item().voices || [], voiceId: model.voices?.[0]?.voice_id || item().voiceId || "" });
    };
    const voiceGuideItem = () => {
      const voice = (item().voices || []).find((entry) => entry.voice_id === item().voiceId) || {};
      return { provider: item().provider, provider_label: item().providerLabel, model: item().model, voice_id: item().voiceId, label: voice.label || item().voiceId, style: voice.style || voice.description || "", preview_audio_url: voice.preview_audio_url || voice.sample_audio_url || "" };
    };
    return <article class="ocrebuild-tts-model-card ocrebuild-shot-tts-model-card" classList={{ "is-default-selected": Boolean(item().recommendation) }}>
      <div class="ocrebuild-tts-model-head"><strong>{item().providerLabel || item().provider}</strong><div class="ocrebuild-tts-model-actions"><Show when={item().recommendation}><span class="ocrebuild-tts-default-badge">默认选中</span></Show><button class="icon-action ocrebuild-tts-model-icon" type="button" title="单独生成" aria-label="单独生成" disabled={busy().startsWith("shot-tts-")} onClick={() => void generateTTSForShot(workflowId, index)}><PlayIcon /></button><button class="icon-action ocrebuild-tts-model-icon" type="button" title="Voice Guide" aria-label="Voice Guide" onClick={() => openVoiceGuide(workflowId, voiceGuideItem())}><DocumentIcon /></button></div></div>
      <select class="ocrebuild-tts-model-select" value={item().model || ""} onChange={(event) => updateModel(event.currentTarget.value)}>
        <For each={item().models?.length ? item().models : [{ model: item().model, label: item().model }]}>{(model) => <option value={model.model}>{model.label || model.model}</option>}</For>
      </select>
      <select class="ocrebuild-tts-voice-select" value={item().voiceId || ""} onChange={(event) => updateShotTTSPrompt(workflowId, index, { voiceId: event.currentTarget.value })}>
        <For each={item().voices?.length ? item().voices : [{ voice_id: item().voiceId, label: item().voiceId }]}>{(voice) => <option value={voice.voice_id}>{voice.label || voice.voice_id}</option>}</For>
      </select>
      <input class="ocrebuild-compare-short-input" value={item().userInstruction || ""} placeholder="简单提示词：疲惫但克制，8秒内自然说完" onInput={(event) => { item().userInstruction = event.currentTarget.value; }} />
      <button class="ocrebuild-compare-refine-button" type="button" disabled={item().refining} onClick={(event) => { const card = event.currentTarget.closest(".ocrebuild-shot-tts-model-card"); void refineShotTTSPrompt(workflowId, index, { model: card?.querySelector(".ocrebuild-tts-model-select")?.value || item().model, voiceId: card?.querySelector(".ocrebuild-tts-voice-select")?.value || item().voiceId, userInstruction: card?.querySelector("input")?.value || "", currentPrompt: card?.querySelector("textarea")?.value || item().currentPrompt }); }}>{item().refining ? "优化中..." : "生成整段 Final Prompt"}</button>
      <Show when={item().error}><p class="ocrebuild-compare-error">{item().error}</p></Show>
      <textarea class="ocrebuild-compare-prompt-editor ocrebuild-tts-prompt-editor" value={item().currentPrompt || ""} onInput={(event) => { item().currentPrompt = event.currentTarget.value; }} />
    </article>;
  };

  const renderTTSCandidate = (workflowId, candidate, final = null) => <article class={`ocrebuild-compare-result ocrebuild-tts-candidate ${candidate.status === "failed" ? "is-failed" : ""} ${final?.selected_output === candidate.output ? "is-selected" : ""}`}>
    <div class="ocrebuild-compare-card-head"><strong>{candidate.provider}/{candidate.model}</strong><span>{candidate.durationSeconds ? `${Number(candidate.durationSeconds).toFixed(2)}s` : candidate.status}</span></div>
    <Show when={candidate.status !== "failed"} fallback={<p class="ocrebuild-compare-error">{candidate.error || "Generation failed"}</p>}>
      <audio controls preload="metadata" src={candidate.src} />
      <button type="button" onClick={() => void finalizeTTSCandidate(workflowId, candidate)}>确认使用这个语音</button>
    </Show>
  </article>;

  const renderShotTTSCandidate = (workflowId, candidate, final = null) => {
    const playbackKey = `${candidate.provider}/${candidate.model}/${candidate.output}`;
    const isPlaying = () => assetTTSWorkflows()[workflowId]?.ttsPlayback?.key === playbackKey;
    const hasLockedAudio = () => Boolean(assetTTSWorkflows()[workflowId]?.shotFinal?.locked_audio || assetTTSWorkflows()[workflowId]?.lockedTimeline?.locked_audio || assetTTSWorkflows()[workflowId]?.lockedTimeline?.audio);
    const selected = () => !hasLockedAudio() && final?.selected_output === candidate.output;
    return <article class={`ocrebuild-compare-result ocrebuild-tts-candidate ocrebuild-shot-tts-candidate ${candidate.status === "failed" ? "is-failed" : ""} ${selected() ? "is-selected" : ""}`}>
      <div class="ocrebuild-shot-tts-candidate-head"><strong>{candidate.provider}/{candidate.model}</strong><span>{candidate.durationSeconds ? `${Number(candidate.durationSeconds).toFixed(2)}s` : candidate.status}</span></div>
      <Show when={candidate.status !== "failed"} fallback={<p class="ocrebuild-compare-error">{candidate.error || "Generation failed"}</p>}>
        <div class="ocrebuild-shot-tts-candidate-actions"><button class={`icon-action ocrebuild-shot-tts-play ${isPlaying() ? "is-playing" : ""}`} type="button" title={isPlaying() ? "停止" : "播放"} aria-label={isPlaying() ? "停止" : "播放"} onClick={(event) => { event.stopPropagation(); toggleTTSCandidatePlayback(workflowId, candidate); }}>{isPlaying() ? <PauseIcon /> : <PlayIcon />}</button><Show when={selected()} fallback={hasLockedAudio() ? <span class="ocrebuild-shot-tts-unselected-badge">未选定</span> : <button class="ocrebuild-shot-tts-select" type="button" onClick={() => void finalizeShotTTSCandidate(workflowId, candidate)}>选定</button>}><span class="ocrebuild-shot-tts-selected-badge">已选定</span></Show></div>
      </Show>
    </article>;
  };

  const renderLockedTTSAudioPanel = (wf) => {
    const final = wf.shotFinal || {};
    const shotId = String(wf.shot?.shot_id || "").trim();
    const lockedAudio = final.locked_audio || wf.lockedTimeline?.locked_audio || wf.lockedTimeline?.audio || lockedTTSFiles()[shotId] || "";
    const audioSrc = lockedAudio ? rebuildAssetUrl(lockedAudio) : "";
    const duration = final.duration || wf.lockedTimeline?.duration || 0;
    const playbackKey = `locked/${audioSrc}`;
    const isPlaying = () => assetTTSWorkflows()[wf.workflowId]?.ttsPlayback?.key === playbackKey;
    return <Show when={audioSrc}>
      <article class="ocrebuild-compare-result ocrebuild-tts-candidate ocrebuild-shot-tts-candidate ocrebuild-locked-audio-card is-selected">
        <div class="ocrebuild-shot-tts-candidate-head"><strong>Locked TTS</strong><span>{duration ? `${Number(duration).toFixed(2)}s` : "wav"}</span></div>
        <div class="ocrebuild-shot-tts-candidate-actions"><button class={`icon-action ocrebuild-locked-audio-play ${isPlaying() ? "is-playing" : ""}`} type="button" title={isPlaying() ? "暂停" : "播放"} aria-label={isPlaying() ? "暂停 Locked TTS" : "播放 Locked TTS"} onClick={() => toggleLockedTTSPlayback(wf.workflowId, audioSrc)}>{isPlaying() ? <PauseIcon /> : <PlayIcon />}</button><span class="ocrebuild-shot-tts-selected-badge">已选定</span></div>
      </article>
    </Show>;
  };

  const renderVoiceGuidePanel = () => {
    const guide = voiceGuideDialog();
    if (!guide?.recommendation) return null;
    const item = guide.recommendation;
    const scenarioInfo = () => voiceGuideScenarioInfo(item, guide.scenario);
    return <>
      <div class="drawer-backdrop tts-guide-backdrop ocrebuild-voice-guide-backdrop" onClick={closeVoiceGuide} />
      <section class="verify-dialog tts-guide-dialog ocrebuild-voice-guide-dialog">
        <div class="env-dialog-head">
          <div><h3>{voiceGuideTitle(item)}</h3><p>{item.model} · {item.label || item.voice_id}</p></div>
          <button class="secondary ocrebuild-compare-close-button" type="button" title="关闭" aria-label="关闭" onClick={closeVoiceGuide}><CloseIcon /></button>
        </div>
        <div class="tts-guide-tool">
          <label class="openflow-field"><span>Scenario</span><div class="tts-scenario-row"><select value={guide.scenario || ""} onChange={(event) => updateVoiceGuideScenario(event.currentTarget.value)}><For each={voiceGuideScenarioOptions(item)}>{(option) => <option value={option.id}>{option.label}</option>}</For></select><button class="tts-info-button" type="button" title="Scenario information" aria-label="Scenario information" aria-expanded={guide.infoOpen ? "true" : "false"} onClick={() => updateVoiceGuide({ infoOpen: !guide.infoOpen })}>i</button></div></label>
          <Show when={guide.infoOpen}>
            <div class="ocrebuild-voice-guide-info">
              <strong>{scenarioInfo().title}</strong>
              <p>{scenarioInfo().body}</p>
              <div><For each={scenarioInfo().verifies}>{(tag) => <span>{tag}</span>}</For></div>
            </div>
          </Show>
          <div class="tts-guide-controls">
            <label class="openflow-field"><span>Voice</span><select value={item.voice_id} disabled><option value={item.voice_id}>{item.label || item.voice_id}</option></select></label>
            <label class="openflow-field"><span>Language</span><select value="zh" disabled><option value="zh">中文</option></select></label>
            <button class="tts-play-icon-button" type="button" title="Play Preview" aria-label="Play Preview" disabled={guide.phase === "generating" || !guide.sampleText?.trim()} onClick={() => void generateVoiceGuidePreview()}><PlayIcon /></button>
          </div>
          <label class="openflow-field tts-prompt-build"><span>Simple Prompt</span><textarea ref={(el) => { voiceGuideSimplePromptEl = el; el.value = guide.simplePrompt || ""; }} /></label>
          <div class="tts-guide-actions"><button class="secondary" type="button" onClick={() => buildVoiceGuideComplexPrompt()}>Generate Complex Prompt</button><button type="button" disabled={guide.phase === "generating" || !guide.sampleText?.trim()} onClick={() => void generateVoiceGuidePreview()}>{guide.phase === "generating" ? "Generating..." : "Play Preview"}</button></div>
          <label class="openflow-field tts-prompt-build"><span>{voiceGuideComplexLabel(item)}</span><textarea class="tts-complex-prompt" ref={(el) => { voiceGuideComplexPromptEl = el; el.value = guide.complexPrompt || ""; }} /></label>
          <Show when={guide.audioUrl}><div class="tts-preview-row"><audio controls preload="metadata" src={guide.audioUrl} /></div></Show>
          <Show when={guide.error}><p class="tts-preview-error">{guide.error}</p></Show>
          <div class="tts-guide-links"><a href={voiceGuideDocsUrl(item)} target="_blank" rel="noreferrer">Provider Docs</a><a href="#" onClick={(event) => event.preventDefault()}>Voice Library</a></div>
        </div>
      </section>
    </>;
  };

  const voiceReferenceGenderText = (wf) => {
    const profile = wf.voiceRecommendationResult?.reference_profile || wf.voiceRecommendationResult?.reference_features?.profile || {};
    const gender = profile.gender === "male" ? "男声" : profile.gender === "female" ? "女声" : profile.gender || "待分析";
    return gender;
  };
  const renderLockedTimeline = (wf) => {
    const scenes = [...(wf.lockedTimeline?.scenes || [])].sort((a, b) => Number(a.start || 0) - Number(b.start || 0));
    return <Show when={scenes.length}><div class="ocrebuild-locked-timeline"><For each={scenes}>{(scene) => <div class="ocrebuild-locked-timeline-item"><em>{Number(scene.duration || 0).toFixed(2)}s</em><p>{scene.srt_text || "No SRT"}</p><span>{Number(scene.start || 0).toFixed(2)}-{Number(scene.end || 0).toFixed(2)}s</span></div>}</For></div></Show>;
  };

  const renderAssetTTSWorkflow = (wf) => {
    return <OCRebuildTTSBuilder
      workflow={wf}
      position={() => assetTTSDialogPositions()[wf.workflowId]}
      busy={busy}
      voiceGuideDialog={voiceGuideDialog}
      voiceRecommendationKey={voiceRecommendationKey}
      voiceReferenceGenderText={voiceReferenceGenderText}
      voiceReferenceDisplayPath={voiceReferenceDisplayPath}
      voiceReferenceAudioUrl={voiceReferenceAudioUrl}
      voiceReferencePlaybackKey={voiceReferencePlaybackKey}
      toggleVoiceReferencePlayback={toggleVoiceReferencePlayback}
      uploadShotTTSReferenceAudio={uploadShotTTSReferenceAudio}
      updateVoiceReferenceClip={updateVoiceReferenceClip}
      toggleVoiceRecommendationPlayback={toggleVoiceRecommendationPlayback}
      selectRecommendedVoice={selectRecommendedVoice}
      openVoiceGuide={openVoiceGuide}
      toggleVoiceRecommendationInfo={toggleVoiceRecommendationInfo}
      runShotTTSVoiceBuilder={runShotTTSVoiceBuilder}
      generateTTSForShot={generateTTSForShot}
      updateAssetTTSWorkflow={updateAssetTTSWorkflow}
      shotTTSFullText={shotTTSFullText}
      hasLockedTTSFile={(item) => Boolean(lockedTTSFiles()[item.shot?.shot_id])}
      onDragStart={startAssetTTSDialogDrag}
      onClose={closeAssetTTSWorkflow}
      renderShotTTSPromptCards={(item) => <Index each={item.shotProviderPrompts || []}>{(prompt, index) => renderShotTTSPromptCard(item.workflowId, prompt, index)}</Index>}
      renderShotTTSCandidates={(item) => <For each={item.shotCandidates || []}>{(candidate) => renderShotTTSCandidate(item.workflowId, candidate, item.shotFinal)}</For>}
      renderLockedTTSAudioPanel={renderLockedTTSAudioPanel}
      renderLockedTimeline={renderLockedTimeline}
      icons={{ CloseIcon, CopyIcon, DocumentIcon, PauseIcon, PlayIcon, SaveIcon, SpeakerIcon, UploadIcon }}
    />;
  };

  const renderShotMultiReferenceWorkflow = (wf) => {
    const pos = () => shotMultiReferenceDialogPositions()[wf.workflowId];
    const selectedModelKey = () => `${wf.provider}/${wf.modelId}`;
    const finalVideoOutputs = () => {
      const base = shotPlanFinalVideoOutputs(wf.shot);
      const selected = String(wf.final?.selected_output || "").trim();
      if (!selected || base.some((item) => item.output === selected)) return base;
      return [{ key: "selected", label: wf.final?.provider && wf.final?.model ? `${wf.final.provider}/${wf.final.model}` : "Selected Final", output: selected, duration: wf.final?.duration }, ...base];
    };
    const finalDurationLabel = (item) => item.duration || wf.final?.duration || wf.duration ? `${Number(item.duration || wf.final?.duration || wf.duration).toFixed(3)}s` : "-";
    const renderFinalVideoCard = (item) => <Show when={item.output && !missingShotAssetVideos()[item.output]}><article class="ocrebuild-compare-result ocrebuild-final-video-card is-selected"><div class="ocrebuild-video-preview-wrap"><div class="ocrebuild-video-preview-meta"><strong title={item.label}>{item.label}</strong><span>Final Video · {finalDurationLabel(item)}</span></div><div class="ocrebuild-final-compose-badge">最终合成使用</div><video class="ocrebuild-compare-video" controls preload="metadata" src={rebuildAssetUrl(item.output)} onError={() => setMissingShotAssetVideos((prev) => ({ ...prev, [item.output]: true }))} /></div></article></Show>;
    return <section class="verify-dialog ocrebuild-compare-dialog ocrebuild-video-dialog ocrebuild-shot-r2v-dialog" data-workflow-id={wf.workflowId} classList={{ "is-dragged": Boolean(pos()) }} style={pos() ? { left: `${pos().left}px`, top: `${pos().top}px`, transform: "none" } : {}}>
      <div class="env-dialog-head ocrebuild-compare-head" onMouseDown={(event) => startShotMultiReferenceDialogDrag(wf.workflowId, event)}><div class="ocrebuild-compare-title"><span class="ocrebuild-compare-title-icon"><DocumentIcon /></span><h3>多图参考工作流</h3><span class="ocrebuild-compare-title-meta">{wf.shot?.shot_id} / Shot R2V</span></div><div class="ocrebuild-dialog-head-actions"><button class="ocrebuild-compare-play-button is-step-action" type="button" title="运行生成" aria-label="运行生成" disabled={!wf.currentPrompt?.trim() || busy().startsWith("shot-r2v-")} onClick={(event) => { event.stopPropagation(); void generateShotMultiReferenceVideos(wf.workflowId); }}><PlayIcon /></button><button class="secondary ocrebuild-compare-close-button" type="button" title="关闭" aria-label="关闭" onClick={() => closeShotMultiReferenceWorkflow(wf.workflowId)}><CloseIcon /></button></div></div>
      <div class="ocrebuild-compare-scroll-body">
        <Show when={wf.error}><div class="banner bad openclip-banner">{wf.error}</div></Show>
        <div class="ocrebuild-compare-body ocrebuild-shot-r2v-body">
          <div class="ocrebuild-shot-r2v-toolbar">
            <label><span>模型</span><select class="ocrebuild-shot-r2v-model-select" value={selectedModelKey()} onChange={(event) => updateShotMultiReferenceModel(wf.workflowId, event.currentTarget.value)}><For each={shotMultiReferenceModels()}>{(item) => <option value={item.key}>{item.label}</option>}</For></select></label>
            <label><span>生成数量</span><select class="ocrebuild-shot-r2v-count-select" value={String(wf.variantCount || 1)} onChange={(event) => updateShotMultiReferenceWorkflow(wf.workflowId, (prev) => prev ? { ...prev, variantCount: Number(event.currentTarget.value) || 1 } : prev)}><option value="1">1</option><option value="2">2</option><option value="3">3</option></select></label>
            <label><span>Duration</span><input class="ocrebuild-shot-r2v-duration-input" type="number" min="1" step="0.5" value={wf.duration || ""} onInput={(event) => updateShotMultiReferenceWorkflow(wf.workflowId, (prev) => prev ? { ...prev, duration: Number(event.currentTarget.value) || prev.duration } : prev)} /></label>
          </div>
          <div class="ocrebuild-shot-r2v-refine-row"><input class="ocrebuild-compare-short-input ocrebuild-shot-r2v-simple-input" value={wf.simplePrompt || ""} placeholder="简单提示词：例如更自然、更真实、节奏更慢" /><button class="ocrebuild-compare-refine-button" type="button" disabled={wf.refining} onClick={() => void refineShotMultiReferencePrompt(wf.workflowId)}>{wf.refining ? "优化中..." : "优化提示词"}</button></div>
          <textarea class="ocrebuild-compare-prompt-editor ocrebuild-shot-r2v-prompt-editor" value={wf.currentPrompt || ""} />
        </div>
        <Show when={wf.phase === "generating"}><div class="ocrebuild-compare-status-line"><strong>Shot 多图 R2V 生成中</strong><span>候选视频事件流正在写入 Debug Console。</span></div></Show>
        <Show when={wf.candidates?.length || finalVideoOutputs().length}><div class="ocrebuild-compare-body"><div class="ocrebuild-compare-grid"><For each={finalVideoOutputs()}>{(item) => renderFinalVideoCard(item)}</For><For each={wf.candidates}>{(candidate) => <article class={`ocrebuild-compare-result ${candidate.status === "failed" ? "is-failed" : ""} ${wf.final?.selected_output === candidate.output ? "is-selected" : ""}`}><Show when={candidate.status !== "failed"} fallback={<p class="ocrebuild-compare-error">{candidate.error || "Generation failed"}</p>}><div class="ocrebuild-video-preview-wrap"><div class="ocrebuild-video-preview-meta"><strong title={`${candidate.provider}/${candidate.model}`}>{candidate.provider}/{candidate.model}</strong><span>{candidate.duration ? `${candidate.duration}s` : candidate.status}</span></div><button class="ocrebuild-video-overlay-confirm" type="button" onClick={() => void finalizeShotMultiReferenceCandidate(wf.workflowId, candidate)}>确认使用</button><video class="ocrebuild-compare-video" controls preload="metadata" src={candidate.src} /></div></Show></article>}</For></div></div></Show>
      </div>
    </section>;
  };

  const renderSelect = (key, label, values) => <label class="openflow-field"><span>{label}</span><select value={draft()?.[key] || ""} onChange={(e) => updateDraft(key, e.currentTarget.value)}><For each={values || []}>{(item) => <option value={item}>{item}</option>}</For></select></label>;

  return <>
    <Show when={error()}><div class="banner bad openclip-banner">{error()}</div></Show>
    <Portal>
      <Show when={keyframeContextMenu()}><div class="ocrebuild-context-backdrop" onClick={() => setKeyframeContextMenu(null)} onContextMenu={(event) => { event.preventDefault(); setKeyframeContextMenu(null); }} /><div class="ocrebuild-keyframe-context-menu" style={{ left: `${keyframeContextMenu().x}px`, top: `${keyframeContextMenu().y}px` }}>
        <button type="button" onClick={() => markSceneBoundary(keyframeContextMenu().shot, keyframeContextMenu().frame, "first")}>标记为 Scene 首帧</button>
        <button type="button" onClick={() => markSceneBoundary(keyframeContextMenu().shot, keyframeContextMenu().frame, "last")}>标记为 Scene 尾帧</button>
        <button type="button" onClick={() => markSceneBoundary(keyframeContextMenu().shot, keyframeContextMenu().frame, "single")}>标记为 Scene 单帧</button>
        <Show when={keyframeContextMenu().frame?.scene_mark}><button type="button" class="danger" onClick={() => unmarkSceneBoundary(keyframeContextMenu().shot, keyframeContextMenu().frame)}>取消 Scene 标记</button></Show>
        <Show when={keyframeContextMenu().frame?.scene_mark}><span>已是 Scene 标记帧</span></Show>
      </div></Show>
      <Show when={assetImageContextMenu()}><div class="ocrebuild-context-backdrop" onClick={() => setAssetImageContextMenu(null)} onContextMenu={(event) => { event.preventDefault(); setAssetImageContextMenu(null); }} /><div class="ocrebuild-keyframe-context-menu" style={{ left: `${assetImageContextMenu().x}px`, top: `${assetImageContextMenu().y}px` }}>
        <button type="button" disabled={busy().startsWith("asset-image-") || !hasCopyableReferenceAsset(assetImageContextMenu())} onClick={() => void copyReferenceAssetImage(assetImageContextMenu())}>拷贝原图</button>
        <button type="button" class="danger" disabled={busy().startsWith("asset-image-") || !hasDeletableAssetImage(assetImageContextMenu())} onClick={() => void deleteAssetImage(assetImageContextMenu())}>删除图片</button>
        <button type="button" disabled={busy().startsWith("asset-image-")} onClick={() => void openAssetCompareWorkflow(assetImageContextMenu())}>多图工作流</button>
        <button type="button" disabled={busy().startsWith("asset-image-")} onClick={() => void openAssetSingleWorkflow(assetImageContextMenu())}>单图工作流</button>
      </div></Show>
      <Show when={assetVideoContextMenu()}><div class="ocrebuild-context-backdrop" onClick={() => setAssetVideoContextMenu(null)} onContextMenu={(event) => { event.preventDefault(); setAssetVideoContextMenu(null); }} /><div class="ocrebuild-keyframe-context-menu" style={{ left: `${assetVideoContextMenu().x}px`, top: `${assetVideoContextMenu().y}px` }}>
        <button type="button" disabled={busy().startsWith("asset-video-")} onClick={() => void openAssetVideoWorkflow(assetVideoContextMenu())}>视频比对工作流</button>
        <button type="button" onClick={() => { setAssetPromptDialog({ pkg: assetVideoContextMenu().pkg, role: "video" }); setAssetVideoContextMenu(null); }}>查看提示词</button>
        <Show when={assetVideoContextMenu().videoTask?.provider}><span>{assetVideoContextMenu().videoTask.provider}/{assetVideoContextMenu().videoTask.model}</span></Show>
      </div></Show>
      <Show when={shotAssetViewer()}><div class="drawer-backdrop ocrebuild-shot-asset-backdrop" onClick={() => setShotAssetViewer(null)} /><section class="verify-dialog ocrebuild-shot-asset-dialog" onClick={(event) => event.stopPropagation()}><div class="env-dialog-head"><div><h3>Shot 资产</h3><p>{shotAssetViewer()?.shot?.shot_id} · {shotAssetViewer()?.label || "最新合成视频"}</p></div><button class="secondary ocrebuild-compare-close-button" type="button" title="关闭" aria-label="关闭" onClick={() => setShotAssetViewer(null)}><CloseIcon /></button></div><div class="ocrebuild-shot-asset-body" onClick={(event) => event.stopPropagation()}><Show when={!missingShotAssetVideos()[shotAssetViewer()?.output]} fallback={<div class="ocrebuild-shot-empty"><strong>等待生成 Shot 合成视频</strong><span>{shotAssetViewer()?.output}</span></div>}><video controls preload="metadata" src={shotAssetViewer()?.src} onClick={(event) => event.stopPropagation()} onError={() => setMissingShotAssetVideos((prev) => ({ ...prev, [shotAssetViewer()?.output]: true }))} /></Show></div></section></Show>
      <OCRebuildHostProductBuilder
        open={hostProductBuilderOpen}
        setOpen={setHostProductBuilderOpen}
        api={api}
        task={task}
        draft={draft}
        selectedTaskId={selectedTaskId}
        saveConfig={saveConfig}
        runAction={runAction}
        rebuildAssetUrl={rebuildAssetUrl}
        openAssetImageViewer={openAssetImageViewer}
        runModels={runModels}
        providerItems={providerItems}
        selectedPromptModel={selectedPromptModel}
        updatePromptModelProvider={updatePromptModelProvider}
        updateDraft={updateDraft}
        modelDetail={modelDetail}
        emitStreamDebug={emitStreamDebug}
        icons={{ ArrowsClockwiseIcon, CloseIcon, CodeIcon, DocumentIcon, PlayIcon, PlusIcon, TrashIcon }}
      />
      {renderVoiceGuidePanel()}
      {taskListOpen() ? (
        <div class="ocrebuild-modal-root">
          <div class="drawer-backdrop" onClick={() => setTaskListOpen(false)} />
          <section class="verify-dialog openflow-session-dialog"><div class="env-dialog-head"><div><h3>OC-Rebuild Tasks</h3><p>Select or delete Rebuild tasks.</p></div><button class="secondary" onClick={() => setTaskListOpen(false)}>Close</button></div><div class="openflow-session-dialog-list"><div class="field-row openflow-session-dialog-toolbar"><button onClick={() => void runAction("newTask", createTask)}>New Task</button></div><For each={tasks()}>{(item) => <div class={`openflow-session-dialog-item ${task()?.id === item.id ? "is-active" : ""}`}><button class="openflow-session-dialog-main" onClick={() => { setTaskListOpen(false); window.location.hash = api.taskDetailUrl(item.id); }}><strong>#{item.id}</strong><span>{item.source_scheme}</span><span>{formatTime(item.updated_at)}</span></button><button class="openflow-session-dialog-delete" onClick={() => void runAction(`delete-${item.id}`, () => deleteTask(item.id))}><TrashIcon /></button></div>}</For></div></section>
        </div>
      ) : null}
      {renderShotPromptPackageDialog()}
    </Portal>
    <Show when={assetCompareWorkflowList().length || assetVideoWorkflowList().length || shotMultiReferenceWorkflowList().length || assetTTSWorkflowList().length}><div class="drawer-backdrop" onClick={() => { setAssetCompareWorkflows({}); setAssetCompareDialogPositions({}); setAssetCompareExpandedSteps({}); setAssetVideoWorkflows({}); setAssetVideoDialogPositions({}); setAssetVideoExpandedSteps({}); setShotMultiReferenceWorkflows({}); setShotMultiReferenceDialogPositions({}); setAssetTTSWorkflows({}); setAssetTTSDialogPositions({}); }} /></Show>
    <For each={assetCompareWorkflowList()}>{(wf) => renderAssetCompareWorkflow(wf)}</For>
    <For each={assetVideoWorkflowList()}>{(wf) => renderAssetVideoWorkflow(wf)}</For>
    <For each={shotMultiReferenceWorkflowList()}>{(wf) => renderShotMultiReferenceWorkflow(wf)}</For>
    <For each={assetTTSWorkflowList()}>{(wf) => renderAssetTTSWorkflow(wf)}</For>
    <div class="openflow-page openclip-flow-page">
      <section class="card step-panel">
        <div class="step-card-head"><div class="step-title-wrap"><span class="step-badge">2</span><h2>Rebuild</h2><StatusBadge status={task()?.status || "draft"} /></div><div class="step-actions openflow-step-actions"><button class="secondary ocrebuild-param-toggle" type="button" onClick={() => setParamsCollapsed((value) => !value)}>{paramsCollapsed() ? "展开参数" : "收起参数"}</button><button class="icon-action ocrebuild-srt-editor-entry" type="button" title="SRT Builder" aria-label="SRT Builder" disabled={!task()} onClick={() => setSrtRewriteDialogOpen(true)}><SubtitleEditIcon /></button><button class="icon-action ocrebuild-host-product-button" type="button" title="Host & Product Builder" aria-label="Host & Product Builder" data-tooltip="Host & Product Builder" disabled={!task()} onClick={() => setHostProductBuilderOpen(true)}><HostProductIcon /></button><button class="icon-action ocrebuild-plan-voice-button" type="button" title="推荐 Shot Plan 声音" aria-label="推荐 Shot Plan 声音" disabled={!task() || !shotPlan()} onClick={() => void openVoiceRecommendWorkflow()}><VoiceMatchIcon /></button><button class="icon-action" title="Prompt Builder" disabled={!task()} onClick={() => setPromptDrawerOpen(true)}><SlidersIcon /></button><button class="icon-action" title="Workflow Assistant" disabled={!task()} onClick={() => setAssistantOpen(true)}><CodeIcon /></button><button class="icon-action" title="Run" disabled={!task()} onClick={() => setRunModelDialogOpen(true)}><PlayIcon /></button></div></div>
        <div class="step-card-body openflow-summary-body">
          <Show when={!paramsCollapsed()}><div class="openflow-summary-fields">
            <div class="openflow-summary-item">
              <div class="openflow-summary-label">Task</div>
              <div class="openflow-summary-value">#{task()?.id || "-"}</div>
            </div>
            <div class="openflow-summary-item">
              <div class="openflow-summary-label">Session</div>
              <div class="openflow-summary-value">
                <Show when={task()?.session_id} fallback={"-"}>
                  <a class="openclip-session-link" href={`#/sessions/task/${task()?.session_id}`}>#{task()?.session_id}</a>
                </Show>
              </div>
            </div>
            <div class="openflow-summary-item">
              <div class="openflow-summary-label">Source Package</div>
              <div class="openflow-summary-value"><span class="inline-code">{task()?.source_package_path || "source_package.json"}</span></div>
            </div>
            <div class="openflow-summary-item">
              <div class="openflow-summary-label">Source Scheme</div>
              <div class="openflow-summary-value">{sourceSchemeLabel(task()?.source_scheme || "detail")}</div>
            </div>
            <div class="openflow-summary-item">
              <div class="openflow-summary-label">Target Topic</div>
              <div class="openflow-summary-value">{task()?.target_topic || "-"}</div>
            </div>
            <div class="openflow-summary-item">
              <div class="openflow-summary-label">Target Platform</div>
              <div class="openflow-summary-value openflow-prompt-preview">{task()?.target_platform || "-"}</div>
            </div>
          </div></Show>
          <section class="ocrebuild-shot-plan-panel ocrebuild-shot-plan-summary">
            {renderShotPlanHeader()}
            <Show when={shotPlan()} fallback={<div class="ocrebuild-shot-empty"><strong>No shot plan yet</strong><span>Generate Intent Package first, then Generate Shot Plan. The result will appear here for page rendering.</span></div>}>
              {renderShotList()}
            </Show>
          </section>
        </div>
      </section>
    </div>

    <Show when={shotDetailTable()}><div class="drawer-backdrop" onClick={() => setShotDetailTable(null)} /><section class="verify-dialog ocrebuild-shot-field-dialog"><div class="env-dialog-head"><div><h3>{shotDetailTable()?.shot_id || "Shot Detail"}</h3></div><button class="secondary" type="button" onClick={() => setShotDetailTable(null)}>Close</button></div><div class="ocrebuild-shot-field-table-wrap"><table class="ocrebuild-shot-field-table"><tbody><For each={shotDetailRows(shotDetailTable())}>{(row) => <tr><th>{row.field}</th><td>{row.value}</td></tr>}</For></tbody></table></div></section></Show>

    <Show when={sceneMarkDialog()}><div class="drawer-backdrop" onClick={() => setSceneMarkDialog(null)} /><section class="verify-dialog ocrebuild-shot-field-dialog ocrebuild-scene-mark-dialog"><div class="env-dialog-head"><div><h3>{sceneMarkDialog()?.mark?.scene_mark_id || "Scene Mark"}</h3><p>{sceneMarkDialog()?.frame?.scene_mark?.role || "scene"} · {sceneMarkDialog()?.mark?.duration || "-"}s</p></div><div class="ocrebuild-scene-mark-head-actions"><button class="icon-action success" type="button" title={busy() === "scene-srt-save" ? "Saving srt_text" : "Save srt_text"} aria-label="Save srt_text" disabled={busy() === "scene-srt-save"} onClick={() => void runAction("scene-srt-save", saveSceneSrtText)}><SaveIcon /></button><button class="secondary" type="button" onClick={() => setSceneMarkDialog(null)}>Close</button></div></div><div class="ocrebuild-shot-field-table-wrap"><table class="ocrebuild-shot-field-table"><tbody><For each={sceneMarkRows(sceneMarkDialog())}>{(row) => <tr><th>{row.field}</th><td><Show when={row.field === "srt_text"} fallback={row.value}><input class="ocrebuild-scene-srt-input" value={sceneSrtDraft()} onInput={(event) => setSceneSrtDraft(event.currentTarget.value)} /></Show></td></tr>}</For></tbody></table></div></section></Show>
    <OCRebuildSrtBuilder
      open={srtRewriteDialogOpen}
      onClose={() => setSrtRewriteDialogOpen(false)}
      api={api}
      task={task}
      draft={draft}
      busy={busy}
      setBusy={setBusy}
      setError={setError}
      shotPlan={shotPlan}
      setShotPlan={setShotPlan}
      shotPlanShots={shotPlanShots}
      editableSceneMarks={editableSceneMarks}
      setAssetPromptPackages={setAssetPromptPackages}
      shotCount={() => shotPlanShots().length}
      runModelFilter={runModelFilter}
      setRunModelFilter={setRunModelFilter}
      providerItems={providerItems}
      updateRunModelProvider={updateRunModelProvider}
      updateDraft={updateDraft}
      runModels={runModels}
      filteredRunModels={filteredRunModels}
      selectedRunModel={selectedRunModel}
      modelDetail={modelDetail}
      plainSrtText={plainSrtText}
      normalizeWorkflowId={normalizeWorkflowId}
      rebuildAssetUrl={rebuildAssetUrl}
      ensureTTSModelConfig={ensureTTSModelConfig}
      defaultTTSPrompt={defaultTTSPrompt}
      defaultTTSPrompts={defaultTTSPrompts}
      applyTTSPlanSelection={applyTTSPlanSelection}
      promptForRecommendedVoice={promptForRecommendedVoice}
      tempoFromSelection={tempoFromSelection}
      ttsCardTempo={ttsCardTempo}
      emitDebugError={(err, extra) => emitDebugError(err, debugContext(extra))}
      emitStreamDebug={emitStreamDebug}
      icons={{ ArrowsClockwiseIcon, CloseIcon, CodeIcon, CopyIcon, DocumentIcon, FilterIcon, PauseIcon, PlayIcon, SaveIcon }}
    />

    <Show when={assetPromptDialog()}><div class="drawer-backdrop" onClick={() => setAssetPromptDialog(null)} /><section class="verify-dialog ocrebuild-shot-field-dialog ocrebuild-scene-mark-dialog"><div class="env-dialog-head"><div><h3>{assetPromptDialog()?.pkg?.scene_mark_id || "Asset Prompt"}</h3><p>{assetPromptDialog()?.role || "image"} · 04 Asset</p></div><button class="secondary" type="button" onClick={() => setAssetPromptDialog(null)}>Close</button></div><div class="ocrebuild-shot-field-table-wrap"><table class="ocrebuild-shot-field-table"><tbody><For each={assetPromptRows(assetPromptDialog()?.pkg, assetPromptDialog()?.role)}>{(row) => <tr><th>{row.field}</th><td>{row.value}</td></tr>}</For></tbody></table></div></section></Show>

    <Show when={assetImageViewer()}><div class="drawer-backdrop ocrebuild-image-viewer-backdrop" onClick={() => setAssetImageViewer(null)} /><section class="ocrebuild-image-viewer" onClick={(event) => event.stopPropagation()}><div class="ocrebuild-image-viewer-head"><strong>{assetImageViewer()?.label || "Image"}</strong><div><button class="secondary" type="button" onClick={() => zoomAssetImageViewer(-0.25)}>-</button><span>{Math.round((assetImageViewer()?.zoom || 1) * 100)}%</span><button class="secondary" type="button" onClick={() => zoomAssetImageViewer(0.25)}>+</button><button class="secondary" type="button" onClick={() => setAssetImageViewer((prev) => prev ? { ...prev, zoom: 1 } : prev)}>Reset</button><button class="secondary" type="button" onClick={() => setAssetImageViewer(null)}>Close</button></div></div><div class="ocrebuild-image-viewer-body"><img src={assetImageViewer()?.src} style={{ transform: `scale(${assetImageViewer()?.zoom || 1})` }} /></div></section></Show>

    <Show when={promptDrawerOpen() && draft()}><div class="drawer-backdrop" onClick={() => setPromptDrawerOpen(false)} /><section class="skill-drawer openflow-config-drawer openflow-prompt-drawer"><div class="skill-drawer-head"><div class="ocrebuild-drawer-title-row"><h3>Prompt Builder</h3><div class="openflow-version-chips ocrebuild-header-version-chips"><For each={detail()?.versions || []}>{(version) => <div class="openflow-version-chip-wrap"><button class={`openflow-version-chip ${task()?.current_version_id === version.id ? "is-active" : ""}`} type="button" onClick={() => void runAction(`load-${version.id}`, () => loadVersion(version.id))}><strong>{formatTime(version.created_at)}</strong></button><button class="openflow-version-delete" type="button" onClick={() => void runAction(`delete-version-${version.id}`, () => deleteVersion(version.id))}><TrashIcon /></button></div>}</For></div></div><div class="openflow-dialog-head-actions"><button class="icon-action openflow-icon-action success" title="Generate Simple Prompt" onClick={() => void runAction("simple", rebuildSimplePrompt)}><ArrowsClockwiseIcon /></button><button class="icon-action openflow-icon-action primary" title="Generate Final Prompt" onClick={() => setPromptModelDialogOpen(true)}><CodeIcon /></button><button class="icon-action openflow-icon-action" title="Edit Final Prompt" onClick={() => setPromptPreviewOpen(true)}><DocumentIcon /></button><button class="icon-action openflow-icon-action" title="Save Version" onClick={() => void runAction("version", saveVersion)}><ClockCounterClockwiseIcon /></button><button class="icon-action openflow-icon-action" title="Save Draft / Current Version" onClick={() => void runAction("save", saveDraftOrCurrentVersion)}><SaveIcon /></button><button class="icon-action openflow-icon-action close" title="Close" onClick={() => setPromptDrawerOpen(false)}><CloseIcon /></button></div></div>
      <div class="openflow-config-drawer-body">
        <section class="openflow-builder-card openclip-parameter-card ocrebuild-parameter-card">
          <div class="ocrebuild-select-grid">
            <label class="openflow-field"><span>来源 Analysis Task</span><select value={String(draft().analysis_task_id || "")} onChange={(e) => updateDraft("analysis_task_id", Number(e.currentTarget.value) || null)}><option value="">Select Analysis Task</option><For each={detail()?.analysis_tasks || []}>{(item) => <option value={item.id}>Task #{item.id} / Session #{item.session_id}</option>}</For></select></label>
            <label class="openflow-field"><span>源分镜方案</span><select value={draft().source_scheme || "detail"} onChange={(e) => updateDraft("source_scheme", e.currentTarget.value)}><For each={options().source_scheme || []}>{(item) => <option value={item}>{sourceSchemeLabel(item)}</option>}</For></select></label>
            <label class="openflow-field ocrebuild-platform-field"><span>目标平台</span><button class="ocrebuild-platform-trigger" type="button" onClick={() => setPlatformMenuOpen((value) => !value)}>{draft().target_platform || "选择目标平台"}</button><Show when={platformMenuOpen()}><div class="ocrebuild-platform-menu"><For each={options().target_platform || []}>{(item) => <button class="ocrebuild-platform-option" type="button" onClick={() => togglePlatform(item)}><span>{item}</span><input type="checkbox" checked={splitMultiValue(draft().target_platform).includes(item)} readOnly /></button>}</For></div></Show></label>
            <label class="openflow-field"><span>目标比例</span><select value={draft().aspect_ratio || "9:16"} onChange={(e) => updateDraft("aspect_ratio", e.currentTarget.value)}><For each={options().aspect_ratio || []}>{(item) => <option value={item}>{item}</option>}</For></select></label>
            {renderSelect("target_audience", "目标受众", options().target_audience)}
            <label class="openflow-field"><span>目标主题</span><input value={draft().target_topic || ""} onInput={(e) => updateDraft("target_topic", e.currentTarget.value)} /></label>
            {renderSelect("rebuild_goal", "重建目标", options().rebuild_goal)}
            {renderSelect("visual_style", "视觉风格", options().visual_style)}
            {renderSelect("subtitle_style", "字幕风格", options().subtitle_style)}
            {renderSelect("title_style", "标题风格", options().title_style)}
            {renderSelect("voice_style", "声音风格", options().voice_style)}
            <label class="openflow-field"><span>生成数量</span><input type="number" min="1" value={draft().target_count || 1} onInput={(e) => updateDraft("target_count", Number(e.currentTarget.value) || 1)} /></label>
          </div>
          <div class="ocrebuild-text-grid">
            <label class="openflow-field ocrebuild-textarea-field"><span>产品/服务</span><textarea value={draft().product_info || ""} onInput={(e) => updateDraft("product_info", e.currentTarget.value)} /></label>
            <label class="openflow-field ocrebuild-textarea-field"><span>限制条件</span><textarea value={draft().constraints || ""} onInput={(e) => updateDraft("constraints", e.currentTarget.value)} /></label>
          </div>
          <div class="ocrebuild-checkbox-section">
            <div class="ocrebuild-checkbox-title">保留策略</div>
            <div class="ocrebuild-checkbox-grid"><For each={Object.entries(options().preserve_strategy || {})}>{([key, label]) => <label class="ocrebuild-checkbox-item"><span>{label}</span><input type="checkbox" checked={Boolean(draft().preserve_strategy?.[key])} onChange={(e) => updateStrategy("preserve_strategy", key, e.currentTarget.checked)} /></label>}</For></div>
            <div class="ocrebuild-checkbox-title">替换策略</div>
            <div class="ocrebuild-checkbox-grid"><For each={Object.entries(options().replace_strategy || {})}>{([key, label]) => <label class="ocrebuild-checkbox-item"><span>{label}</span><input type="checkbox" checked={Boolean(draft().replace_strategy?.[key])} onChange={(e) => updateStrategy("replace_strategy", key, e.currentTarget.checked)} /></label>}</For></div>
          </div>
        </section>
        <section class="openflow-builder-card ocrebuild-simple-prompt-card"><h4>Simple Prompt</h4><textarea class="skill-editor openflow-modal-prompt ocrebuild-simple-prompt-editor" value={draft().simple_prompt || ""} onInput={(e) => updateDraft("simple_prompt", e.currentTarget.value)} /></section>
      </div></section></Show>

    <SharedWorkflowAssistantDrawer workflowId="oc_rebuild" taskId={task()?.id} sessionId={task()?.session_id} open={assistantOpen()} onClose={() => setAssistantOpen(false)} />

    <Show when={promptModelDialogOpen() && draft()}>
      <div class="drawer-backdrop openclip-model-overlay" onClick={() => setPromptModelDialogOpen(false)} />
      <section class="verify-dialog openflow-model-dialog openclip-prompt-model-dialog">
        <div class="env-dialog-head openclip-model-dialog-head"><div class="openclip-model-header-text"><h3>Select Model</h3></div></div>
        <div class="openflow-prompt-model-grid openclip-model-dialog-body model-preset-dialog-body">
          <ModelPresetCards
            items={runModels()}
            provider={draft().prompt_model_provider}
            model={draft().prompt_model_id}
            onSelect={selectPromptModelPreset}
            aria-label="Prompt model preset"
          />
        </div>
        <div class="openflow-model-dialog-summary openclip-model-selection"><div class="openflow-model-dialog-summary-body openclip-selection-card"><div class="openclip-selection-content"><em>{selectedPromptModel() ? modelDetail(selectedPromptModel()) : "No model available"}</em></div></div></div>
        <div class="field-row openflow-model-dialog-actions openclip-model-dialog-actions"><button class="secondary openclip-model-cancel" onClick={() => setPromptModelDialogOpen(false)}>Cancel</button><button class="openclip-model-confirm" disabled={!draft().prompt_model_provider || !draft().prompt_model_id || busy() === "final"} onClick={() => void runAction("final", generatePrompt)}>Confirm & Generate</button></div>
      </section>
    </Show>

    <Show when={runModelDialogOpen() && draft()}>
      <div class="drawer-backdrop openclip-model-overlay" onClick={() => setRunModelDialogOpen(false)} />
      <section class="verify-dialog openflow-model-dialog openclip-prompt-model-dialog openclip-run-model-dialog">
        <div class="env-dialog-head openclip-model-dialog-head"><div class="openclip-model-header-text"><h3>Select Run Model</h3></div></div>
        <div class="openflow-prompt-model-grid openclip-model-dialog-body model-preset-dialog-body">
          <ModelPresetCards
            items={runModels()}
            provider={draft().run_model_provider}
            model={draft().run_model_id}
            onSelect={selectRunModelPreset}
            aria-label="Run model preset"
          />
        </div>
        <div class="openflow-model-dialog-summary openclip-model-selection"><div class="openflow-model-dialog-summary-body openclip-selection-card"><div class="openclip-run-selection-content"><em>{modelDetail(selectedRunModel())}</em><span>Rebuild Task: #{task()?.id || "-"} / Session #{task()?.session_id || "-"}</span></div></div></div>
        <div class="field-row openflow-model-dialog-actions openclip-model-dialog-actions openclip-run-model-actions"><button class="secondary openclip-model-cancel" onClick={() => setRunModelDialogOpen(false)}>Cancel</button><button class="openclip-model-save" disabled={!draft().run_model_provider || !draft().run_model_id || busy() === "saveRunModel"} onClick={() => void runAction("saveRunModel", saveConfig)}>Save</button><button class="openclip-model-confirm" disabled={!draft().run_model_provider || !draft().run_model_id || busy() === "run"} onClick={() => void runAction("run", runTask)}>Run</button></div>
      </section>
    </Show>

    <Show when={promptPreviewOpen() && draft()}><div class="drawer-backdrop" onClick={() => setPromptPreviewOpen(false)} /><section class="verify-dialog openclip-prompt-preview-dialog"><div class="env-dialog-head"><div><h3>Final Prompt</h3><p>{(draft().final_prompt || "").length.toLocaleString()} characters</p></div><button class="secondary" onClick={() => setPromptPreviewOpen(false)}>Close</button></div><textarea class="skill-editor openclip-prompt-preview-textarea" value={draft().final_prompt || ""} onInput={(e) => updateDraft("final_prompt", e.currentTarget.value)} /></section></Show>
  </>;
}
