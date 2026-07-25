const API_BASE = (() => {
  const href = window.location.href.split("#")[0];
  return href.endsWith("/") ? href : `${href}/`;
})();

async function request(path, init) {
  const relativePath = path.startsWith("/") ? path.slice(1) : path;
  const res = await fetch(new URL(relativePath, API_BASE), {
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    ...init,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `Request failed (${res.status})`);
  }
  if (res.status === 204) {
    return {};
  }
  return await res.json();
}

export const openClipApi = {
  tasks: () => request("/api/openclip/tasks"),
  createTask: () => request("/api/openclip/tasks", { method: "POST" }),
  taskDetail: (taskId) => request(`/api/openclip/tasks/${taskId}`),
  deleteTask: (taskId) => request(`/api/openclip/tasks/${taskId}`, { method: "DELETE" }),
  saveConfig: (taskId, payload) => request(`/api/openclip/tasks/${taskId}/config`, { method: "PUT", body: JSON.stringify({ ...payload, task_id: taskId }) }),
  rebuildSimplePrompt: (taskId, payload) => request(`/api/openclip/tasks/${taskId}/simple-prompt/rebuild`, { method: "POST", body: JSON.stringify({ ...payload, task_id: taskId }) }),
  generatePrompt: (taskId, payload) => request(`/api/openclip/tasks/${taskId}/generate-prompt`, { method: "POST", body: JSON.stringify({ ...payload, task_id: taskId }) }),
  savePromptVersion: (taskId, payload) => request(`/api/openclip/tasks/${taskId}/prompt-versions`, { method: "POST", body: JSON.stringify({ ...payload, task_id: taskId }) }),
  updatePromptVersion: (taskId, versionId) => request(`/api/openclip/tasks/${taskId}/prompt-versions/${versionId}`, { method: "PUT" }),
  loadPromptVersion: (taskId, versionId) => request(`/api/openclip/tasks/${taskId}/prompt-versions/load`, { method: "POST", body: JSON.stringify({ task_id: taskId, version_id: versionId }) }),
  deletePromptVersion: (taskId, versionId) => request(`/api/openclip/tasks/${taskId}/prompt-versions/${versionId}`, { method: "DELETE" }),
  generateSkill: (taskId, payload) => request(`/api/openclip/tasks/${taskId}/generate-skill`, { method: "POST", body: JSON.stringify({ ...payload, task_id: taskId }) }),
  saveSkillDraft: (taskId, payload) => request(`/api/openclip/tasks/${taskId}/skill-draft`, { method: "PUT", body: JSON.stringify({ ...payload, task_id: taskId }) }),
  saveSkillVersion: (taskId, payload) => request(`/api/openclip/tasks/${taskId}/skill-versions`, { method: "POST", body: JSON.stringify({ ...payload, task_id: taskId }) }),
  loadSkillVersion: (taskId, versionId) => request(`/api/openclip/tasks/${taskId}/skill-versions/load`, { method: "POST", body: JSON.stringify({ task_id: taskId, version_id: versionId }) }),
  deleteSkillVersion: (taskId, versionId) => request(`/api/openclip/tasks/${taskId}/skill-versions/${versionId}`, { method: "DELETE" }),
  run: (taskId, payload) => request(`/api/openclip/tasks/${taskId}/run`, { method: "POST", body: JSON.stringify({ ...payload, task_id: taskId }) }),
  rerun: (taskId, payload) => request(`/api/openclip/tasks/${taskId}/rerun`, { method: "POST", body: JSON.stringify({ ...payload, task_id: taskId }) }),
  taskEvents: (taskId, since = 0) => request(`/api/openclip/tasks/${taskId}/session-events?since=${since}`),
  taskAttempts: (taskId) => request(`/api/openclip/tasks/${taskId}/attempts`),
  taskFiles: (sessionId, path = "") => request(`/api/session-tasks/${sessionId}/files${path ? `?path=${encodeURIComponent(path)}` : ""}`),
  rawFileUrl: (sessionId, filePath) => new URL(`api/session-tasks/${sessionId}/raw/${filePath.split("/").map(encodeURIComponent).join("/")}`, API_BASE).toString(),
  taskDetailUrl: (taskId) => `#/openclip/tasks/${taskId}`,
};
