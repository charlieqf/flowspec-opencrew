const API_BASE = (() => {
  const href = window.location.href.split("#")[0];
  return href.endsWith("/") ? href : `${href}/`;
})();

async function request(path, init = {}) {
  const relativePath = path.startsWith("/") ? path.slice(1) : path;
  const res = await fetch(new URL(relativePath, API_BASE), {
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(init.headers || {}),
    },
    ...init,
  });
  if (!res.ok) {
    const text = await res.text();
    let message = text || `Request failed (${res.status})`;
    try {
      const payload = JSON.parse(text);
      if (typeof payload?.detail === "string" && payload.detail.trim()) message = payload.detail.trim();
      else if (payload?.detail?.message) message = String(payload.detail.message).trim();
    } catch {
      // Keep the raw response text.
    }
    throw new Error(message);
  }
  if (res.status === 204) return {};
  return res.json();
}

export const talkingHeadV1Api = {
  detail: (taskId) => request(`/api/talking-head-v1/tasks/${taskId}`),
  saveRewrittenSrt: (taskId, payload) => request(`/api/openclip/tasks/${taskId}/analysis-v1/rewritten-srt`, { method: "PUT", body: JSON.stringify({ task_id: taskId, ...payload }) }),
  runStoryboard: (taskId, payload = {}) => request(`/api/talking-head-v1/tasks/${taskId}/run-storyboard`, { method: "POST", body: JSON.stringify(payload) }),
  runStoryboardStatus: (taskId, attemptId) => request(`/api/talking-head-v1/tasks/${taskId}/run-storyboard/${attemptId}`),
  oneClickMovie: (taskId, payload = {}) => request(`/api/talking-head-v1/tasks/${taskId}/one-click-movie`, { method: "POST", body: JSON.stringify(payload) }),
  oneClickMovieStatus: (taskId, runId = "") => request(`/api/talking-head-v1/tasks/${taskId}/one-click-movie${runId ? `/${encodeURIComponent(runId)}` : ""}`),
};
