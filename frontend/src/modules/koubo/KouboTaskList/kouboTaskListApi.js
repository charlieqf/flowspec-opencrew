const API_BASE = (() => {
  const href = window.location.href.split("#")[0];
  return href.endsWith("/") ? href : `${href}/`;
})();

function errorMessageFromResponseText(text, fallback) {
  const raw = String(text || "").trim();
  if (!raw) return fallback;
  try {
    const payload = JSON.parse(raw);
    const detail = payload?.detail;
    if (typeof detail === "string" && detail.trim()) return detail.trim();
    if (detail?.message) return String(detail.message).trim();
    return raw;
  } catch {
    return raw;
  }
}

async function request(path, init = {}) {
  const relativePath = path.startsWith("/") ? path.slice(1) : path;
  const isFormData = typeof FormData !== "undefined" && init.body instanceof FormData;
  const res = await fetch(new URL(relativePath, API_BASE), {
    credentials: "include",
    headers: {
      ...(isFormData ? {} : { "Content-Type": "application/json" }),
      ...(init.headers || {}),
    },
    ...init,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(errorMessageFromResponseText(text, `Request failed (${res.status})`));
  }
  if (res.status === 204) return {};
  return res.json();
}

function danceMimicFormData(payload) {
  const referenceFile = payload?.reference_video_file;
  const targetFile = payload?.target_identity_image_file;
  const form = new FormData();
  Object.entries(payload || {}).forEach(([key, value]) => {
    if (key === "reference_video_file" || key === "target_identity_image_file") return;
    form.append(key, value == null ? "" : String(value));
  });
  if (referenceFile) form.append("reference_video_file", referenceFile);
  if (targetFile) form.append("target_identity_image_file", targetFile);
  return form;
}

function talkingHeadFormData(payload) {
  const form = new FormData();
  Object.entries(payload || {}).forEach(([key, value]) => {
    if (key === "portrait_image_file" || key === "reference_video_file") return;
    if (typeof value === "object" && value !== null) {
      form.append(key, JSON.stringify(value));
    } else {
      form.append(key, value == null ? "" : String(value));
    }
  });
  if (payload?.portrait_image_file) form.append("portrait_image_file", payload.portrait_image_file);
  if (payload?.reference_video_file) form.append("reference_video_file", payload.reference_video_file);
  return form;
}

export const kouboTaskListApi = {
  list: (includeArchived = false) => request(`/api/koubo-tasks?include_archived=${includeArchived ? "true" : "false"}`),
  detail: (taskId) => request(`/api/koubo-tasks/${taskId}`),
  createFromVideo: () => request("/api/openclip/tasks", { method: "POST" }),
  createDanceMimic: (payload) => request("/api/dance-mimic-v1/tasks/with-uploads", { method: "POST", body: danceMimicFormData(payload) }),
  updateDanceMimic: (taskId, payload) => request(`/api/dance-mimic-v1/tasks/${taskId}/with-uploads`, { method: "PUT", body: danceMimicFormData(payload) }),
  listDanceMimicReferenceVideos: () => request("/api/dance-mimic-v1/reference-videos"),
  uploadDanceMimicReferenceVideo: (file) => {
    const form = new FormData();
    form.append("file", file);
    return request("/api/dance-mimic-v1/reference-videos/upload", { method: "POST", body: form });
  },
  listDanceMimicTargetImages: () => request("/api/dance-mimic-v1/target-images"),
  uploadDanceMimicTargetImage: (file) => {
    const form = new FormData();
    form.append("file", file);
    return request("/api/dance-mimic-v1/target-images/upload", { method: "POST", body: form });
  },
  createTalkingHead: (payload) => request("/api/koubo-tasks/create-talking-head", { method: "POST", body: talkingHeadFormData(payload) }),
  updateTalkingHead: (taskId, payload) => request(`/api/koubo-tasks/${taskId}/talking-head`, { method: "PUT", body: talkingHeadFormData(payload) }),
  listTalkingHeadVoiceClones: () => request("/api/openclip/analysis-v1/tts/quick-adv/clone-list", {
    method: "POST",
    body: JSON.stringify({ clone_page_size: 100, providers: "google" }),
  }),
  deleteTalkingHeadVoiceClone: (voiceId) => request("/api/openclip/analysis-v1/tts/quick-adv/clone-delete", {
    method: "POST",
    body: JSON.stringify({ clone_voice_id: voiceId }),
  }),
  previewTalkingHeadVoiceClone: (payload) => request("/api/openclip/analysis-v1/tts/clone-preview", {
    method: "POST",
    body: JSON.stringify(payload || {}),
  }),
  createFromScript: (payload) => request("/api/koubo-tasks/create-from-script", { method: "POST", body: JSON.stringify(payload) }),
  updateFromScript: (taskId, payload) => request(`/api/koubo-tasks/${taskId}/script`, { method: "PUT", body: JSON.stringify(payload) }),
  options: () => request("/api/koubo-tasks/options"),
  promptModels: () => request("/api/openclip/prompt-models"),
  generatePrompt: (taskId, payload) => request(`/api/openclip/tasks/${taskId}/generate-prompt`, { method: "POST", body: JSON.stringify({ ...payload, task_id: taskId }) }),
  archive: (taskId) => request(`/api/koubo-tasks/${taskId}`, { method: "DELETE" }),
  delete: (taskId) => request(`/api/koubo-tasks/${taskId}/delete`, { method: "DELETE" }),
};
