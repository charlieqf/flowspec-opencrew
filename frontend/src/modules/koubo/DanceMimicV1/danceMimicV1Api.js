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
    throw new Error(text || `Request failed (${res.status})`);
  }
  if (res.status === 204) return {};
  return res.json();
}

export const danceMimicV1Api = {
  detail: (taskId) => request(`/api/dance-mimic-v1/tasks/${taskId}`),
  plan: (taskId) => request(`/api/dance-mimic-v1/tasks/${taskId}/run/plan`),
  run: (taskId, payload = {}) => request(`/api/dance-mimic-v1/tasks/${taskId}/run`, { method: "POST", body: JSON.stringify(payload) }),
  runStatus: (taskId, attemptId) => request(`/api/dance-mimic-v1/tasks/${taskId}/run/${attemptId}`),
  oneClickMovie: (taskId, payload = {}) => request(`/api/dance-mimic-v1/tasks/${taskId}/one-click-movie`, { method: "POST", body: JSON.stringify(payload) }),
  oneClickMovieStatus: (taskId, runId = "") => request(`/api/dance-mimic-v1/tasks/${taskId}/one-click-movie${runId ? `/${encodeURIComponent(runId)}` : ""}`),
};
