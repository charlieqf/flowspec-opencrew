import { createMemo, createSignal } from "solid-js";

const LIVE_CAP = 500;
const HISTORY_CAP = 3000;
const FIELD_PREVIEW_LIMIT = 4096;
const RAW_PREVIEW_LIMIT = 32768;
const TERMINAL_STATUSES = new Set(["completed", "failed", "finalized", "timeout"]);
const STATUS_RANK = {
  requested: 1,
  calling: 2,
  running: 2,
  heartbeat: 3,
  completed: 10,
  failed: 10,
  finalized: 10,
  timeout: 10,
  stale: 9,
  disconnected: 8,
};

const [expanded, setExpanded] = createSignal(false);
const [height, setHeight] = createSignal(280);
const [activeSessionId, setActiveSessionId] = createSignal(null);
const [sessions, setSessions] = createSignal({});
const [systemEvents, setSystemEvents] = createSignal([]);
const [status, setStatus] = createSignal("idle");
const [lastError, setLastError] = createSignal("");
const [streamCursor, setStreamCursor] = createSignal(0);
let localSeq = 1;

function nowMs() {
  return Date.now();
}

function stableEventId(event) {
  return String(event.id || event.event_id || event.request_id || event.api_call_id || event.local_id || `local-${localSeq++}`);
}

function isLargeDataString(value) {
  return /^data:[^,]+;base64,/i.test(value) || (value.length > 512 && /^[A-Za-z0-9+/=\r\n]+$/.test(value));
}

function truncateString(value, limit = FIELD_PREVIEW_LIMIT) {
  if (value.length <= limit) return value;
  if (isLargeDataString(value)) return "[truncated large data field]";
  return `${value.slice(0, limit)}... [truncated ${value.length - limit} chars]`;
}

export function sanitizePayload(value, limit = RAW_PREVIEW_LIMIT) {
  if (value == null) return value;
  if (typeof value === "string") return truncateString(value, limit);
  if (typeof value === "number" || typeof value === "boolean") return value;
  if (Array.isArray(value)) return value.slice(0, 50).map((item) => sanitizePayload(item, Math.max(1024, Math.floor(limit / 4))));
  if (typeof value === "object") {
    const output = {};
    Object.entries(value).slice(0, 120).forEach(([key, item]) => {
      const lower = key.toLowerCase();
      if (lower.includes("base64") || lower.includes("bytes") || lower.includes("blob")) {
        output[key] = "[truncated large data field]";
      } else {
        output[key] = sanitizePayload(item, Math.max(1024, Math.floor(limit / 4)));
      }
    });
    return output;
  }
  return String(value);
}

function normalizeStatus(input) {
  const raw = String(input || "").toLowerCase();
  if (raw === "started" || raw === "provider_call.started") return "calling";
  if (raw === "round_completed") return "completed";
  if (raw === "error") return "failed";
  return raw || "requested";
}

function eventSessionId(event) {
  const value = Number(event.session_id || event.sessionId || event.payload?.session_id || 0);
  return Number.isFinite(value) && value > 0 ? value : null;
}

function normalizeEvent(event, defaults = {}) {
  const payload = sanitizePayload(event.payload || event.detail_payload || event);
  const statusValue = normalizeStatus(event.status || event.type || event.payload?.status || defaults.status);
  const sessionId = eventSessionId(event) || defaults.session_id || null;
  const normalized = {
    id: stableEventId(event),
    source_id: event.id || event.event_id || null,
    session_id: sessionId,
    task_id: event.task_id || event.taskId || event.payload?.task_id || defaults.task_id || null,
    workflow_id: event.workflow_id || event.workflowId || event.payload?.workflow_id || defaults.workflow_id || "",
    request_id: event.request_id || event.requestId || event.payload?.request_id || defaults.request_id || "",
    api_call_id: event.api_call_id || event.apiCallId || event.payload?.api_call_id || defaults.api_call_id || "",
    family: event.family || defaults.family || inferFamily(event),
    kind: event.kind || defaults.kind || buildKind(event, statusValue, defaults),
    provider: event.provider || event.payload?.provider || defaults.provider || "",
    model: event.model || event.payload?.model || event.payload?.image_model || event.payload?.video_model || event.payload?.tts_model || defaults.model || "",
    status: statusValue,
    created_at: Number(event.created_at || event.createdAt || defaults.created_at || nowMs()),
    elapsed_seconds: event.elapsed_seconds ?? event.payload?.elapsed_seconds ?? defaults.elapsed_seconds ?? null,
    output: event.output || event.payload?.output || defaults.output || "",
    output_path: event.output_path || event.payload?.output_path || defaults.output_path || "",
    detail: event.detail || event.message || event.payload?.detail || defaults.detail || "",
    payload,
    local: Boolean(event.local || defaults.local),
  };
  return normalized;
}

function inferFamily(event) {
  const kind = String(event.kind || event.family || event.type || "");
  if (kind.includes("asset_tts")) return "asset_tts";
  if (kind.includes("asset_video")) return "asset_video";
  if (kind.includes("shot") || kind.includes("r2v")) return "shot_video";
  if (kind.includes("prompt_refine") || kind.includes("refine")) return "prompt_refine";
  if (kind.includes("run_model")) return "run_model";
  if (kind.includes("provider_call")) return "provider_call";
  if (kind.includes("asset_image") || kind.includes("image")) return "asset_image";
  if (kind.includes("session_run")) return "session_run";
  if (kind.includes("error") || kind.includes("failed")) return "error";
  return "workflow";
}

function buildKind(event, statusValue, defaults) {
  const family = event.family || defaults.family || inferFamily(event);
  return `${family}.${statusValue}`;
}

function mergeUnique(existing, incoming, cap) {
  const map = new Map();
  [...existing, ...incoming].forEach((event) => {
    const key = String(event.source_id || event.id);
    const current = map.get(key);
    if (!current || Number(event.created_at || 0) >= Number(current.created_at || 0)) {
      map.set(key, event);
    }
  });
  const merged = Array.from(map.values()).sort((a, b) => Number(a.created_at || 0) - Number(b.created_at || 0));
  return cap ? merged.slice(Math.max(0, merged.length - cap)) : merged;
}

function ensureSession(sessionsMap, sessionId) {
  const key = String(sessionId);
  return {
    liveEvents: [],
    historyEvents: [],
    unseenCount: 0,
    status: "idle",
    historyCursor: 0,
    liveCursor: 0,
    ...(sessionsMap[key] || {}),
  };
}

export function appendDebugEvent(event, defaults = {}) {
  const normalized = normalizeEvent(event, defaults);
  if (!normalized.session_id) {
    setSystemEvents((prev) => mergeUnique(prev, [normalized], LIVE_CAP));
    if (!expanded()) setStatus("unseen");
    return normalized;
  }
  setSessions((prev) => {
    const next = { ...prev };
    const current = ensureSession(next, normalized.session_id);
    const liveEvents = mergeUnique(current.liveEvents, [normalized], LIVE_CAP);
    next[String(normalized.session_id)] = {
      ...current,
      liveEvents,
      liveCursor: Math.max(Number(current.liveCursor || 0), Number(normalized.source_id || 0)),
      unseenCount: expanded() && activeSessionId() === normalized.session_id ? current.unseenCount : current.unseenCount + 1,
      status: normalized.status === "failed" ? "error" : "running",
    };
    return next;
  });
  if (!expanded()) setStatus("unseen");
  return normalized;
}

export function appendDebugHistory(sessionId, events) {
  if (!sessionId) return;
  const normalizedEvents = (events || []).map((event) => normalizeEvent(event, { session_id: sessionId }));
  setSessions((prev) => {
    const next = { ...prev };
    const current = ensureSession(next, sessionId);
    const historyEvents = mergeUnique(current.historyEvents, normalizedEvents, HISTORY_CAP);
    const cursor = Math.max(Number(current.historyCursor || 0), ...normalizedEvents.map((event) => Number(event.source_id || 0)));
    next[String(sessionId)] = { ...current, historyEvents, historyCursor: cursor, status: "loaded" };
    return next;
  });
}

export function clearDebug() {
  setSystemEvents([]);
  setSessions({});
  setStreamCursor(0);
  setStatus("idle");
  setLastError("");
}

export function clearDebugSession(sessionId) {
  if (!sessionId) return;
  setSessions((prev) => {
    const next = { ...prev };
    delete next[String(sessionId)];
    return next;
  });
}

export function setDebugActiveSession(sessionId) {
  const normalized = Number(sessionId || 0) || null;
  setActiveSessionId(normalized);
  if (normalized) {
    setSessions((prev) => {
      const next = { ...prev };
      const current = ensureSession(next, normalized);
      next[String(normalized)] = { ...current, unseenCount: 0 };
      return next;
    });
  }
}

export function markDebugStatus(nextStatus, message = "") {
  setStatus(nextStatus);
  setLastError(message);
}

export const debugConsoleHeight = createMemo(() => (expanded() ? `${height()}px` : "38px"));

export const debugStore = {
  expanded,
  setExpanded,
  height,
  setHeight,
  activeSessionId,
  setActiveSessionId: setDebugActiveSession,
  sessions,
  systemEvents,
  status,
  setStatus: markDebugStatus,
  lastError,
  streamCursor,
  setStreamCursor,
};

export function allSessionIds() {
  const ids = new Set(Object.keys(sessions()).map((id) => Number(id)).filter(Boolean));
  if (activeSessionId()) ids.add(activeSessionId());
  return Array.from(ids).sort((a, b) => b - a);
}

export function mergedEventsForSession(sessionId) {
  if (!sessionId) return [];
  const current = ensureSession(sessions(), sessionId);
  return mergeUnique(current.historyEvents, current.liveEvents, null);
}

function groupKey(event) {
  return [
    event.session_id || "system",
    event.workflow_id || "-",
    event.api_call_id || event.request_id || "-",
    event.provider || "-",
    event.model || "-",
    event.family || "-",
  ].join("|");
}

function groupStatus(events) {
  let selected = events[0]?.status || "requested";
  for (const event of events) {
    const next = event.status || "requested";
    if (TERMINAL_STATUSES.has(selected) && !TERMINAL_STATUSES.has(next)) continue;
    if ((STATUS_RANK[next] || 0) >= (STATUS_RANK[selected] || 0)) selected = next;
  }
  return selected;
}

export function groupedDebugSessions() {
  const sessionIds = allSessionIds();
  const groups = [];
  if (systemEvents().length) {
    groups.push({
      key: "system",
      title: "System",
      subtitle: "Global errors and system events",
      status: status(),
      entries: systemEvents().map(eventToEntry),
    });
  }
  sessionIds.forEach((sessionId) => {
    const events = mergedEventsForSession(sessionId);
    if (!events.length) return;
    const grouped = new Map();
    events.forEach((event) => {
      const key = groupKey(event);
      grouped.set(key, [...(grouped.get(key) || []), event]);
    });
    const entries = Array.from(grouped.entries()).map(([key, items]) => groupToEntry(key, items));
    groups.push({
      key: `session-${sessionId}`,
      title: `Session #${sessionId}`,
      subtitle: activeSessionId() === sessionId ? "active debug session" : "loaded session",
      status: sessions()[String(sessionId)]?.status || "idle",
      entries: entries.sort((a, b) => a.time - b.time),
    });
  });
  return groups;
}

function eventToEntry(event) {
  return {
    key: String(event.source_id || event.id),
    time: Number(event.created_at || nowMs()),
    level: event.status === "failed" || event.family === "error" ? "error" : "info",
    source: event.kind || event.family,
    message: event.detail || event.kind || event.status,
    expandable: true,
    details: event.payload || event,
  };
}

function groupToEntry(key, events) {
  const ordered = [...events].sort((a, b) => Number(a.created_at || 0) - Number(b.created_at || 0));
  const latest = ordered[ordered.length - 1];
  const statusValue = groupStatus(ordered);
  const provider = latest.provider && latest.model ? `${latest.provider}/${latest.model}` : latest.provider || latest.model || "-";
  const output = latest.output_path || latest.output || "";
  const detail = latest.detail ? ` | ${latest.detail}` : "";
  return {
    key,
    time: Number(latest.created_at || nowMs()),
    level: statusValue === "failed" || latest.family === "error" ? "error" : statusValue === "stale" || statusValue === "disconnected" ? "warn" : "info",
    source: latest.family || latest.kind,
    message: `${statusValue.toUpperCase()} ${latest.family || "event"} | ${provider}${output ? ` | ${output}` : ""}${detail}`,
    expandable: true,
    details: {
      status: statusValue,
      family: latest.family,
      workflow_id: latest.workflow_id,
      request_id: latest.request_id,
      api_call_id: latest.api_call_id,
      provider: latest.provider,
      model: latest.model,
      elapsed_seconds: latest.elapsed_seconds ?? "",
      output: latest.output,
      output_path: latest.output_path,
      detail: latest.detail,
      event_count: ordered.length,
      latest_payload: latest.payload,
    },
  };
}

export function debugConsoleText() {
  return groupedDebugSessions().map((session) => [
    `${session.title} ${session.subtitle} ${session.status}`,
    ...session.entries.map((entry) => `${new Date(entry.time).toISOString()} [${entry.level}] ${entry.source}: ${entry.message}`),
  ].join("\n")).join("\n\n");
}

