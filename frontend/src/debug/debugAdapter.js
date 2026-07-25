import { appendDebugEvent, markDebugStatus } from "./debugStore.js";

function sessionIdFrom(input, context = {}) {
  const value = Number(input?.session_id || input?.sessionId || input?.payload?.session_id || context.session_id || context.sessionId || 0);
  return Number.isFinite(value) && value > 0 ? value : null;
}

function familyFrom(input, context = {}) {
  if (context.family) return context.family;
  const text = `${input?.family || ""} ${input?.kind || ""} ${input?.type || ""}`.toLowerCase();
  if (text.includes("tts")) return "asset_tts";
  if (text.includes("asset_video")) return "asset_video";
  if (text.includes("shot") || text.includes("r2v")) return "shot_video";
  if (text.includes("refine")) return "prompt_refine";
  if (text.includes("run_model")) return "run_model";
  if (text.includes("provider")) return "provider_call";
  if (text.includes("image")) return "asset_image";
  if (text.includes("session")) return "session_run";
  if (text.includes("failed") || text.includes("error")) return "error";
  return "workflow";
}

function statusFrom(input, fallback = "requested") {
  const raw = String(input?.status || input?.type || fallback || "requested").toLowerCase();
  if (raw === "started") return "calling";
  if (raw === "round_completed") return "completed";
  if (raw === "error") return "failed";
  return raw;
}

export function emitModelDebugEvent(input, context = {}) {
  const family = familyFrom(input, context);
  const status = statusFrom(input, context.status);
  const event = {
    ...input,
    session_id: sessionIdFrom(input, context),
    task_id: input?.task_id || context.task_id || context.taskId || null,
    workflow_id: input?.workflow_id || input?.workflowId || context.workflow_id || context.workflowId || "",
    request_id: input?.request_id || input?.requestId || context.request_id || "",
    api_call_id: input?.api_call_id || input?.apiCallId || context.api_call_id || "",
    provider: input?.provider || context.provider || "",
    model: input?.model || input?.image_model || input?.video_model || input?.tts_model || context.model || "",
    family,
    status,
    kind: input?.kind || `${family}.${status}`,
    payload: input?.payload || input,
    created_at: input?.created_at || Date.now(),
    local: input?.local ?? true,
  };
  return appendDebugEvent(event, { family, status });
}

export function emitDebugError(error, context = {}) {
  const message = error instanceof Error ? error.message : String(error || context.detail || "Unknown error");
  const event = {
    ...context,
    session_id: sessionIdFrom(context, context),
    family: context.family || "error",
    status: "failed",
    kind: `${context.family || "error"}.failed`,
    detail: message,
    payload: { ...context, detail: message },
    created_at: Date.now(),
    local: true,
  };
  markDebugStatus("error", message);
  return appendDebugEvent(event, { family: context.family || "error", status: "failed" });
}

export function emitDebugRequested(context = {}) {
  return emitModelDebugEvent({ type: "requested", ...context }, { ...context, status: "requested" });
}

export function emitDebugCompleted(context = {}) {
  return emitModelDebugEvent({ type: "completed", ...context }, { ...context, status: "completed" });
}

export function workflowEventBusEmit(event) {
  window.dispatchEvent(new CustomEvent("opencrew:workflow-event", { detail: event }));
}

export function emitModelAndWorkflowEvent(event, context = {}) {
  const normalized = emitModelDebugEvent(event, context);
  workflowEventBusEmit(normalized);
  return normalized;
}
