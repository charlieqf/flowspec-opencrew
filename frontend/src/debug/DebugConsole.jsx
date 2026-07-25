import { For, Show, createEffect, createSignal, onCleanup } from "solid-js";
import { api } from "../lib/api";
import {
  appendDebugHistory,
  clearDebug,
  debugConsoleHeight,
  debugConsoleText,
  debugStore,
  groupedDebugSessions,
  markDebugStatus,
  mergedEventsForSession,
} from "./debugStore.js";
import { emitDebugError } from "./debugAdapter.js";

function formatTime(value) {
  if (!value) return "--:--:--";
  return new Date(value).toLocaleTimeString();
}

function StatusBadge(props) {
  return <span class={`badge ${props.status || "idle"}`}>{props.status || "idle"}</span>;
}

function ExpandIcon() {
  return <svg class={debugStore.expanded() ? "is-expanded" : ""} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>;
}

function CopyIcon() {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>;
}

function ClearIcon() {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 11v6"/><path d="M14 11v6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>;
}

function LoadIcon() {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/></svg>;
}

function detailRows(details) {
  return Object.entries(details || {})
    .filter(([, value]) => value !== "" && value !== null && value !== undefined)
    .map(([key, value]) => ({ key, value: typeof value === "object" ? JSON.stringify(value, null, 2) : String(value) }));
}

export default function DebugConsole(props) {
  const [expandedRows, setExpandedRows] = createSignal([]);
  const [resizeState, setResizeState] = createSignal(null);
  const [loadingHistory, setLoadingHistory] = createSignal(false);
  const [liveSource, setLiveSource] = createSignal(null);

  createEffect(() => {
    const nextSessionId = props.currentSessionId ? Number(props.currentSessionId() || 0) || null : null;
    if (nextSessionId && !debugStore.activeSessionId()) {
      debugStore.setActiveSessionId(nextSessionId);
    }
  });

  createEffect(() => {
    const state = resizeState();
    if (!state) return;
    const onMove = (event) => {
      const next = Math.min(window.innerHeight - 120, Math.max(180, state.startHeight + (state.startY - event.clientY)));
      debugStore.setHeight(next);
    };
    const onUp = () => setResizeState(null);
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    onCleanup(() => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    });
  });

  onCleanup(() => {
    liveSource()?.close();
  });

  const toggleRow = (key) => setExpandedRows((prev) => prev.includes(key) ? prev.filter((item) => item !== key) : [...prev, key]);

  const loadHistory = async () => {
    const sessionId = debugStore.activeSessionId();
    if (!sessionId) return;
    setLoadingHistory(true);
    try {
      const currentEvents = mergedEventsForSession(sessionId);
      const since = 0;
      const res = await api.sessionEvents(sessionId, since, "debug");
      appendDebugHistory(sessionId, res.items || []);
      markDebugStatus("loaded");
      if (!currentEvents.length && (res.items || []).length) {
        const maxId = Math.max(...res.items.map((event) => Number(event.id || 0)));
        debugStore.setStreamCursor(maxId);
      }
    } catch (err) {
      emitDebugError(err, { family: "network", session_id: sessionId, detail: "Load history failed" });
    } finally {
      setLoadingHistory(false);
    }
  };

  const disconnect = () => {
    liveSource()?.close();
    setLiveSource(null);
    markDebugStatus("disconnected");
  };

  const connect = () => {
    const sessionId = debugStore.activeSessionId();
    if (!sessionId) return;
    liveSource()?.close();
    const since = Math.max(debugStore.streamCursor(), ...mergedEventsForSession(sessionId).map((event) => Number(event.source_id || event.id || 0)).filter(Boolean), 0);
    const source = new EventSource(api.sessionScopedEventStreamUrl(sessionId, since, "debug"), { withCredentials: true });
    setLiveSource(source);
    markDebugStatus("connected");
    source.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        appendDebugHistory(sessionId, [payload]);
        debugStore.setStreamCursor((prev) => Math.max(prev, Number(payload.id || 0)));
      } catch (err) {
        emitDebugError(err, { family: "stream", session_id: sessionId, detail: "Failed to parse Debug SSE event" });
      }
    };
    source.onerror = () => {
      source.close();
      setLiveSource(null);
      markDebugStatus("disconnected", "Debug stream disconnected");
      emitDebugError("Debug stream disconnected", { family: "stream", session_id: sessionId });
    };
  };

  return (
    <section class={`debug-console ${debugStore.expanded() ? "expanded" : "collapsed"}`}>
      <button class="debug-console-grip" onMouseDown={(event) => {
        debugStore.setExpanded(true);
        setResizeState({ startY: event.clientY, startHeight: debugStore.height() });
      }} aria-label="Resize debug console" />
      <div class="debug-console-head">
        <div class="debug-console-info">
          <div class="debug-console-title">Debug Console</div>
          <span class="debug-console-status">
            {debugStore.status()} {debugStore.activeSessionId() ? `· Session #${debugStore.activeSessionId()}` : "· System"}
          </span>
        </div>
        <div class="debug-console-actions">
          <button class="debug-console-button" type="button" title="Load History" disabled={loadingHistory()} onClick={() => void loadHistory()}><LoadIcon /></button>
          <button class="debug-console-button" type="button" title="Connect" onClick={() => connect()}>Live</button>
          <button class="debug-console-button" type="button" title="Disconnect" onClick={disconnect}>Off</button>
          <button class="debug-console-button" type="button" title="Clear" onClick={clearDebug}><ClearIcon /></button>
          <button class="debug-console-button" type="button" title="Copy" onClick={() => void navigator.clipboard.writeText(debugConsoleText())}><CopyIcon /></button>
          <button class={`debug-console-button active ${debugStore.expanded() ? "is-expanded" : ""}`} type="button" title={debugStore.expanded() ? "Collapse" : "Expand"} onClick={() => debugStore.setExpanded((value) => !value)}><ExpandIcon /></button>
        </div>
      </div>
      <div class="debug-console-body">
        <Show when={groupedDebugSessions().length > 0} fallback={<div class="debug-console-empty">No debug output yet. Model calls and errors will appear here.</div>}>
          <For each={groupedDebugSessions()}>{(session) => (
            <section class="debug-session">
              <div class="debug-session-head">
                <div class="debug-session-title">{session.title}</div>
                <div class="debug-session-meta">
                  <span>{session.subtitle}</span>
                  <StatusBadge status={session.status} />
                </div>
              </div>
              <div class="debug-session-body">
                <For each={session.entries}>{(entry) => (
                  <div class={`debug-log-item ${entry.level}`}>
                    <div class={`debug-log-line ${entry.level} ${entry.expandable ? "is-expandable" : ""}`} onClick={() => entry.expandable && toggleRow(entry.key)}>
                      <span class="time">{formatTime(entry.time)}</span>
                      <span class="level" title={entry.source}>{entry.source}</span>
                      <span class="message"><Show when={entry.expandable}><button class="debug-expand-toggle" type="button">{expandedRows().includes(entry.key) ? "-" : "+"}</button></Show>{entry.message}</span>
                    </div>
                    <Show when={entry.expandable && expandedRows().includes(entry.key)}>
                      <div class="debug-call-detail">
                        <For each={detailRows(entry.details)}>{(row) => <div class="debug-call-detail-row"><span>{row.key}</span><code>{row.value}</code></div>}</For>
                      </div>
                    </Show>
                  </div>
                )}</For>
              </div>
            </section>
          )}</For>
        </Show>
      </div>
    </section>
  );
}

export { debugConsoleHeight };
