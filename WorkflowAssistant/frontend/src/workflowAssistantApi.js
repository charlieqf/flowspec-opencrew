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
  if (res.status === 204)
    return {};
  return await res.json();
}

export const workflowAssistantApi = {
  bootstrap: (workflowId, taskId) => request(`/api/workflows/${workflowId}/tasks/${taskId}/assistant/bootstrap`),
  messages: (workflowId, taskId) => request(`/api/workflows/${workflowId}/tasks/${taskId}/assistant/messages`),
  quickPrompts: (workflowId, taskId) => request(`/api/workflows/${workflowId}/tasks/${taskId}/assistant/quick-prompts`),
  plan: (workflowId, taskId) => request(`/api/workflows/${workflowId}/tasks/${taskId}/assistant/plan`),
  savePlan: (workflowId, taskId, plan) => request(`/api/workflows/${workflowId}/tasks/${taskId}/assistant/plan`, { method: "PUT", body: JSON.stringify({ plan }) }),
  confirmPlan: (workflowId, taskId, payload) => request(`/api/workflows/${workflowId}/tasks/${taskId}/assistant/plan/confirm`, { method: "POST", body: JSON.stringify(payload || {}) }),
  sendMessage: (workflowId, taskId, text) => request(`/api/workflows/${workflowId}/tasks/${taskId}/assistant/message`, { method: "POST", body: JSON.stringify({ text }) }),
  abort: (workflowId, taskId) => request(`/api/workflows/${workflowId}/tasks/${taskId}/assistant/abort`, { method: "POST" }),
  eventsUrl: (workflowId, taskId) => new URL(`api/workflows/${workflowId}/tasks/${taskId}/assistant/events`, API_BASE).toString(),
};
