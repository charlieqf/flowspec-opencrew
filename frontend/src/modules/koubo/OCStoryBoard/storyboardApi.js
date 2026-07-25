const API_BASE = (() => {
  const href = window.location.href.split("#")[0];
  return href.endsWith("/") ? href : `${href}/`;
})();

async function request(path, init) {
  const relativePath = path.startsWith("/") ? path.slice(1) : path;
  const headers = init?.body instanceof FormData ? (init?.headers ?? {}) : { "Content-Type": "application/json", ...(init?.headers ?? {}) };
  const res = await fetch(new URL(relativePath, API_BASE), { credentials: "include", headers, ...init });
  if (!res.ok) {
    const text = await res.text();
    try {
      const payload = JSON.parse(text);
      throw new Error(payload?.detail || payload?.message || text || `Request failed (${res.status})`);
    } catch (err) {
      if (err instanceof SyntaxError) throw new Error(text || `Request failed (${res.status})`);
      throw err;
    }
  }
  return res.status === 204 ? {} : await res.json();
}

export const api = {
  tasks: () => request("/api/ocstoryboard/tasks"),
  blank: (form) => request("/api/ocstoryboard/blank", { method: "POST", body: form }),
  jsonImport: (form) => request("/api/ocstoryboard/json", { method: "POST", body: form }),
  detail: (taskId) => request(`/api/ocstoryboard/tasks/${taskId}`),
  uploadAssets: (taskId, form) => request(`/api/ocstoryboard/tasks/${taskId}/assets`, { method: "POST", body: form }),
  deleteAsset: (taskId, assetId) => request(`/api/ocstoryboard/tasks/${taskId}/assets/${encodeURIComponent(assetId)}`, { method: "DELETE" }),
  deleteTask: (taskId) => request(`/api/ocstoryboard/tasks/${taskId}`, { method: "DELETE" }),
  save: (taskId, shotPlan) => request(`/api/ocstoryboard/tasks/${taskId}`, { method: "PUT", body: JSON.stringify({ shot_plan: shotPlan }) }),
  finalize: (taskId) => request(`/api/ocstoryboard/tasks/${taskId}/finalize`, { method: "POST" }),
  refreshPhase2: (taskId) => request(`/api/ocstoryboard/tasks/${taskId}/refresh-phase2`, { method: "POST" }),
  ttsModelConfig: () => request("/api/setup/media-models/tts/config"),
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
  rawFileUrl: (sessionId, filePath) => new URL(`api/session-tasks/${sessionId}/raw/${String(filePath || "").split("/").map(encodeURIComponent).join("/")}`, API_BASE).toString(),
};

export function routeFromHash(hash) {
  const value = hash || window.location.hash || "#/ocstoryboard/tasks";
  const taskMatch = value.match(/^#\/ocstoryboard\/tasks\/(\d+)/);
  if (taskMatch) return { view: "detail", taskId: Number(taskMatch[1]) };
  if (value.startsWith("#/ocstoryboard/new")) return { view: "new", taskId: null };
  return { view: "list", taskId: null };
}
