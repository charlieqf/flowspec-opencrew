import { For, Show, createEffect, createMemo, createSignal, onCleanup, onMount } from "solid-js";
import SharedWorkflowAssistantDrawer from "../../../../WorkflowAssistant/frontend/src/WorkflowAssistantDrawer.jsx";
import { ModelPresetCards, findModelPresetItem } from "../../components/ModelPresetCards.jsx";
import { openClipApi } from "./api";
import "./styles.css";

const OPENCLIP_SCHEMES = [
  { id: "scheme_1", title: "Scheme 1", label: "细分镜" },
  { id: "scheme_2", title: "Scheme 2", label: "均衡分镜" },
  { id: "scheme_3", title: "Scheme 3", label: "粗分镜" },
];
const OPENCLIP_DEFAULT_RUN_PROVIDER_ID = "openai";
const OPENCLIP_DEFAULT_RUN_MODEL_ID = "gpt-5.5";

function clipSortIndex(file) {
  const name = String(file?.name || "");
  const match = name.match(/^(\d+)_/);
  return match ? Number(match[1]) : Number.MAX_SAFE_INTEGER;
}

function CodeIcon() {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>;
}

function PlayIcon() {
  return <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5.5a1 1 0 0 1 1.5-.86l8 5a1 1 0 0 1 0 1.72l-8 5A1 1 0 0 1 8 15.5z"/></svg>;
}

function TrashIcon() {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 13a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>;
}

function CloseIcon() {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>;
}

function SlidersIcon() {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 17H5"/><path d="M19 7h-9"/><circle cx="17" cy="17" r="3"/><circle cx="7" cy="7" r="3"/></svg>;
}

function ClockCounterClockwiseIcon() {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 10 7 10"/><path d="M3.5 15a9 9 0 1 0 2.13-9.36L1 10"/><path d="M12 7v5l3 2"/></svg>;
}

function SaveIcon() {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>;
}

function FolderIcon() {
  return <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M3 6.75A1.75 1.75 0 0 1 4.75 5h4.12c.46 0 .9.18 1.23.51l1.39 1.39c.14.14.33.22.53.22h7.23A1.75 1.75 0 0 1 21 8.88v8.37A1.75 1.75 0 0 1 19.25 19H4.75A1.75 1.75 0 0 1 3 17.25V6.75z" /></svg>;
}

function ConnectionIcon() {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 14l-2.5 2.5a3.536 3.536 0 0 1-5-5l2.5-2.5"/><path d="M14 10l2.5-2.5a3.536 3.536 0 0 1 5 5l-2.5 2.5"/><line x1="10" y1="14" x2="14" y2="10"/></svg>;
}

function ArrowsClockwiseIcon() {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/></svg>;
}

function DocumentIcon() {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M10 9H8"/><path d="M16 13H8"/><path d="M16 17H8"/></svg>;
}

function BracesIcon() {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M8 4H7a3 3 0 0 0-3 3v2a3 3 0 0 1-2 3 3 3 0 0 1 2 3v2a3 3 0 0 0 3 3h1"/><path d="M16 4h1a3 3 0 0 1 3 3v2a3 3 0 0 0 2 3 3 3 0 0 0-2 3v2a3 3 0 0 1-3 3h-1"/></svg>;
}

function StatusBadge(props) {
  return <span class={`status-tag tag-${String(props.status || "idle") === "completed" || String(props.status || "").includes("ready") ? "ready" : String(props.status || "").includes("running") ? "available" : String(props.status || "") === "failed" ? "failed" : "idle"}`}>{String(props.status || "draft")}</span>;
}

function formatTime(value) {
  if (!value)
    return "-";
  return new Date(value).toLocaleString([], { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
}

function formatSeconds(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric))
    return "-";
  return `${numeric.toFixed(1).replace(/\.0$/, "")}s`;
}

function workspaceRelativePath(value) {
  const normalized = String(value || "").replace(/\\/g, "/");
  const marker = "/workspace/";
  const markerIndex = normalized.indexOf(marker);
  if (markerIndex >= 0)
    return normalized.slice(markerIndex + marker.length);
  return normalized.replace(/^\/+/, "");
}

function pathBasename(value) {
  return String(value || "").split(/[\\/]/).filter(Boolean).pop() || "";
}

function segmentJsonMeta(json) {
  if (!json || typeof json !== "object")
    return {};
  const retake = json.retake_fields || {};
  return {
    index: json.segment_index,
    title: json.source?.title || json.vlm_input?.segment?.title || retake.summary?.split("；")?.[0]?.slice(0, 36),
    start: json.time?.start ?? json.vlm_input?.segment?.start,
    end: json.time?.end ?? json.vlm_input?.segment?.end,
    duration: json.time?.duration ?? json.vlm_input?.segment?.duration,
    dialogueText: json.subtitle?.dialogue_text || json.vlm_input?.segment?.dialogue_text || retake.spoken_script || "",
    semanticRole: json.source?.semantic_role || json.vlm_input?.segment?.semantic_role || "",
    formulaSlot: json.source?.formula_slot || json.vlm_input?.segment?.formula_slot || retake.video_structure || "",
  };
}

function formatPreviewValue(value) {
  if (value == null || value === "")
    return "-";
  if (Array.isArray(value))
    return value.map((item) => typeof item === "object" ? JSON.stringify(item) : String(item)).join("、") || "-";
  if (typeof value === "object")
    return JSON.stringify(value, null, 2);
  return String(value);
}

function retakePreviewSections(json) {
  const retake = json?.retake_fields || {};
  const meta = segmentJsonMeta(json);
  return [
    { title: "片段概览", rows: [["标题", meta.title], ["时间", `${formatSeconds(meta.start)} - ${formatSeconds(meta.end)}`], ["时长", formatSeconds(meta.duration)], ["结构槽位", meta.formulaSlot], ["语义角色", meta.semanticRole], ["对白", meta.dialogueText]] },
    { title: "复拍执行", rows: [["复拍指南", retake.guide], ["拍摄方法", retake.shooting_method], ["景别", retake.shot_type], ["机位/镜头", retake.camera], ["运镜", retake.camera_movement], ["构图", retake.composition], ["转场", retake.transition_type], ["剪辑备注", retake.editing_notes]] },
    { title: "画面与场景", rows: [["摘要", retake.summary], ["画面内容", retake.visual_content], ["主场景", retake.main_scene], ["场景判断", retake.scene], ["必须保留", retake.visual_must_have], ["道具/产品", retake.props]] },
    { title: "人物与业务重点", rows: [["人物配合", retake.people_coordination], ["出镜人", retake.performer], ["人物画像", retake.character_profile], ["主要动作", retake.main_action], ["情绪", retake.emotion], ["情绪触发", retake.emotion_trigger], ["内容高光", retake.content_highlights], ["业务焦点", retake.product_or_business_focus], ["复拍备注", retake.retake_notes]] },
  ].map((section) => ({ ...section, rows: section.rows.filter(([, value]) => value != null && value !== "") }));
}

function SchemeSegmentVideo(props) {
  let videoEl;
  const isVirtual = () => props.item?.clipStatus === "virtual";
  const start = () => Number(props.item?.start);
  const end = () => Number(props.item?.end);
  const seekToStart = () => {
    if (!isVirtual() || !videoEl || !Number.isFinite(start()))
      return;
    if (Math.abs(videoEl.currentTime - start()) > 0.15)
      videoEl.currentTime = start();
  };
  const onTimeUpdate = () => {
    if (isVirtual() && Number.isFinite(end()) && videoEl?.currentTime >= end())
      videoEl.pause();
  };
  return <video ref={videoEl} class={props.class} controls preload="metadata" src={props.src} onLoadedMetadata={seekToStart} onPlay={seekToStart} onTimeUpdate={onTimeUpdate} />;
}

function taskIdFromHash(hash) {
  const match = String(hash || "").match(/^#\/openclip\/tasks\/(\d+)/);
  return match ? Number(match[1]) : null;
}

function optionValue(options, current) {
  return options.includes(current) ? current : "__custom__";
}

function modelDetail(model) {
  if (!model)
    return "-";
  const parts = [];
  if (model.reasoning)
    parts.push("支持推理");
  if (model.inputModalities?.length)
    parts.push(`支持 ${model.inputModalities.join("/")}`);
  if (model.contextLimit)
    parts.push(`上下文 ${Number(model.contextLimit).toLocaleString()}`);
  return parts.join(" · ") || "-";
}

function findPreferredRunModel(models) {
  return (models || []).find((item) => {
    const providerID = String(item.providerID || "").toLowerCase();
    const providerName = String(item.providerName || "").toLowerCase();
    const modelID = String(item.modelID || "").toLowerCase();
    const modelName = String(item.modelName || "").toLowerCase();
    return (providerID === OPENCLIP_DEFAULT_RUN_PROVIDER_ID || providerName === "openai")
      && (modelID === OPENCLIP_DEFAULT_RUN_MODEL_ID || modelName === "gpt-5.5");
  }) ?? null;
}

function assistantMessageId(message) {
  return String(message?.info?.id || message?.id || message?.messageID || "");
}

function assistantMessageRole(message) {
  return String(message?.info?.role || message?.role || "assistant");
}

function assistantMessageTime(message) {
  return Number(message?.info?.time?.created || message?.info?.time?.completed || message?.created_at || 0) || 0;
}

function normalizeAssistantMessages(messages) {
  return [...(messages || [])].sort((a, b) => assistantMessageTime(a) - assistantMessageTime(b) || assistantMessageId(a).localeCompare(assistantMessageId(b)));
}

function assistantPartText(part) {
  if (!part)
    return "";
  if (typeof part.text === "string")
    return part.text;
  if (typeof part.content === "string")
    return part.content;
  if (typeof part.title === "string")
    return part.title;
  return "";
}

function LegacyEmbeddedWorkflowAssistantDrawer(props) {
  const [bootstrap, setBootstrap] = createSignal(null);
  const [messages, setMessages] = createSignal([]);
  const [partsByMessageId, setPartsByMessageId] = createSignal({});
  const [composer, setComposer] = createSignal("");
  const [loading, setLoading] = createSignal(false);
  const [error, setError] = createSignal("");
  const [sessionStatus, setSessionStatus] = createSignal({ type: "idle" });
  const [workflowPlan, setWorkflowPlan] = createSignal(null);
  const [workflowRun, setWorkflowRun] = createSignal(null);

  const taskId = () => props.taskId;
  const canChat = createMemo(() => Boolean(bootstrap()?.capabilities?.can_chat));
  const busy = createMemo(() => ["running", "thinking", "working"].includes(String(sessionStatus().type || "")));
  const quickPrompts = createMemo(() => bootstrap()?.quick_prompts || []);
  const context = createMemo(() => bootstrap()?.context || {});

  function seedMessages(items) {
    const nextParts = {};
    for (const message of items || []) {
      const id = assistantMessageId(message);
      if (!id)
        continue;
      nextParts[id] = {};
      for (const part of message.parts || []) {
        const partId = String(part.id || part.partID || `${id}-part-${Object.keys(nextParts[id]).length + 1}`);
        nextParts[id][partId] = { ...part, id: partId, messageID: part.messageID || id };
      }
    }
    setMessages(normalizeAssistantMessages(items || []));
    setPartsByMessageId(nextParts);
  }

  async function loadBootstrap() {
    if (!props.open || !taskId())
      return;
    setLoading(true);
    setError("");
    try {
      const payload = await openClipApi.assistantBootstrap(taskId());
      setBootstrap(payload);
      setSessionStatus({ type: "idle" });
      seedMessages(payload.messages || []);
    } catch (exc) {
      setError(exc?.message || String(exc));
    } finally {
      setLoading(false);
    }
  }

  function upsertMessage(message) {
    const id = assistantMessageId(message);
    if (!id)
      return;
    setMessages((current) => normalizeAssistantMessages([...current.filter((item) => assistantMessageId(item) !== id && !String(assistantMessageId(item)).startsWith("local-")), message]));
    if (message.parts?.length) {
      setPartsByMessageId((current) => {
        const next = { ...current, [id]: { ...(current[id] || {}) } };
        for (const part of message.parts) {
          const partId = String(part.id || part.partID || `${id}-part-${Object.keys(next[id]).length + 1}`);
          next[id][partId] = { ...part, id: partId, messageID: part.messageID || id };
        }
        return next;
      });
    }
  }

  function upsertPart(part) {
    const messageID = String(part?.messageID || part?.messageId || part?.message_id || "");
    const id = String(part?.id || part?.partID || "");
    if (!messageID || !id)
      return;
    setPartsByMessageId((current) => ({
      ...current,
      [messageID]: {
        ...(current[messageID] || {}),
        [id]: { ...(current[messageID]?.[id] || {}), ...part, id, messageID },
      },
    }));
  }

  function appendPartDelta(properties) {
    const messageID = String(properties?.messageID || properties?.messageId || properties?.message_id || "");
    const partID = String(properties?.partID || properties?.partId || properties?.id || "");
    if (!messageID || !partID)
      return;
    const field = String(properties?.field || "text");
    const delta = String(properties?.delta || "");
    setPartsByMessageId((current) => {
      const existingPart = current[messageID]?.[partID] || { id: partID, messageID, type: "text", text: "" };
      const nextPart = { ...existingPart, [field]: String(existingPart[field] || "") + delta };
      return { ...current, [messageID]: { ...(current[messageID] || {}), [partID]: nextPart } };
    });
  }

  function handleAssistantEvent(event) {
    const type = String(event?.type || "");
    const properties = event?.properties || {};
    if (type === "message.updated") {
      upsertMessage(properties.info ? { info: properties.info, parts: properties.parts || [] } : properties.message || properties);
      return;
    }
    if (type === "message.part.updated") {
      upsertPart(properties.part || properties);
      return;
    }
    if (type === "message.part.delta") {
      appendPartDelta(properties);
      return;
    }
    if (type === "message.part.removed") {
      const messageID = String(properties.messageID || "");
      const partID = String(properties.partID || properties.id || "");
      setPartsByMessageId((current) => {
        const nextParts = { ...(current[messageID] || {}) };
        delete nextParts[partID];
        return { ...current, [messageID]: nextParts };
      });
      return;
    }
    if (type === "session.status") {
      setSessionStatus(properties.status || { type: String(properties.type || "idle") });
      return;
    }
    if (type === "workflow.plan.created" || type === "workflow.plan.confirmed") {
      setWorkflowPlan(properties.plan || properties);
      return;
    }
    if (type.startsWith("workflow.run") || type.startsWith("workflow.step")) {
      setWorkflowRun(properties.run || properties);
    }
  }

  createEffect(() => {
    if (!props.open || !taskId())
      return;
    void loadBootstrap();
  });

  createEffect(() => {
    if (!props.open || !taskId() || !canChat())
      return;
    const source = new EventSource(openClipApi.assistantEventsUrl(taskId()), { withCredentials: true });
    source.onmessage = (event) => {
      try {
        handleAssistantEvent(JSON.parse(event.data));
      } catch (exc) {
        setError(`Assistant stream event parse failed: ${exc?.message || exc}`);
      }
    };
    source.onerror = () => setError("Assistant stream disconnected. Reopen the drawer or retry if messages stop updating.");
    onCleanup(() => source.close());
  });

  async function sendMessage(text) {
    const value = String(text ?? composer()).trim();
    if (!value || !canChat())
      return;
    const localId = `local-${Date.now()}`;
    const localMessage = { info: { id: localId, role: "user", time: { created: Date.now() } }, parts: [{ id: `${localId}-part`, messageID: localId, type: "text", text: value }] };
    setMessages((current) => normalizeAssistantMessages([...current, localMessage]));
    setPartsByMessageId((current) => ({ ...current, [localId]: { [`${localId}-part`]: localMessage.parts[0] } }));
    setComposer("");
    setError("");
    try {
      await openClipApi.assistantSendMessage(taskId(), value);
      setSessionStatus({ type: "running" });
    } catch (exc) {
      setMessages((current) => current.filter((item) => assistantMessageId(item) !== localId));
      setPartsByMessageId((current) => {
        const next = { ...current };
        delete next[localId];
        return next;
      });
      setComposer(value);
      setError(exc?.message || String(exc));
    }
  }

  async function abortAssistant() {
    if (!taskId())
      return;
    try {
      await openClipApi.assistantAbort(taskId());
      setSessionStatus({ type: "idle" });
    } catch (exc) {
      setError(exc?.message || String(exc));
    }
  }

  function handlePromptClick(prompt) {
    if (prompt.mode === "send") {
      void sendMessage(prompt.prompt);
      return;
    }
    setComposer(prompt.prompt || "");
  }

  function messageParts(message) {
    const id = assistantMessageId(message);
    return Object.values(partsByMessageId()[id] || {});
  }

  return (
    <Show when={props.open}>
      <div class="drawer-backdrop workflow-assistant-backdrop" onClick={props.onClose} />
      <section class="skill-drawer workflow-assistant-drawer">
        <div class="workflow-assistant-head">
          <div>
            <span class="workflow-assistant-kicker">Task Assistant</span>
            <h3>{bootstrap()?.workflow?.name || "OpenClip Analysis"}</h3>
            <p>Task #{taskId() || "-"} · Session #{bootstrap()?.session?.id || props.sessionId || "-"} · {context()?.business_context?.run_model || "No run model"}</p>
          </div>
          <div class="openflow-dialog-head-actions">
            <button class="icon-action openflow-icon-action" type="button" title="Reload" onClick={() => void loadBootstrap()}><ArrowsClockwiseIcon /></button>
            <button class="icon-action openflow-icon-action close" type="button" title="Close" onClick={props.onClose}><CloseIcon /></button>
          </div>
        </div>
        <div class="workflow-assistant-body">
          <Show when={error()}><div class="workflow-assistant-error">{error()}</div></Show>
          <Show when={loading()} fallback={
            <>
              <div class="workflow-assistant-chips">
                <span class={context()?.business_context?.final_prompt ? "is-ready" : ""}>Final Prompt {context()?.business_context?.final_prompt ? "ready" : "missing"}</span>
                <span class={context()?.business_context?.reference_video_path ? "is-ready" : ""}>Video {context()?.business_context?.reference_video_path ? "ready" : "missing"}</span>
                <span>Workspace {(context()?.workspace_state?.existing_outputs || []).length} outputs</span>
                <span>{workflowPlan() ? "Plan ready" : "No plan"}</span>
                <span class={canChat() ? "is-ready" : "is-bad"}>{canChat() ? "OpenCode connected" : "OpenCode unavailable"}</span>
              </div>
              <div class="workflow-assistant-prompts">
                <For each={quickPrompts()}>{(prompt) => <button type="button" onClick={() => handlePromptClick(prompt)} disabled={!canChat() && prompt.mode === "send"}>{prompt.label}</button>}</For>
              </div>
              <div class="workflow-assistant-timeline">
                <Show when={messages().length > 0} fallback={<div class="workflow-assistant-empty">选择一个快捷提示，或直接询问当前 Task 的执行规划。Assistant 只规划，不直接执行工具。</div>}>
                  <For each={messages()}>{(message) => (
                    <article class={`workflow-message role-${assistantMessageRole(message)}`}>
                      <strong>{assistantMessageRole(message) === "user" ? "You" : "Assistant"}</strong>
                      <For each={messageParts(message)}>{(part) => (
                        <div class={`workflow-message-part part-${part.type || "text"}`}>
                          <Show when={part.type === "reasoning"}><em>Reasoning</em></Show>
                          <Show when={part.type === "tool"}><em>Tool event</em></Show>
                          <pre>{assistantPartText(part) || (part.type && part.type !== "text" ? JSON.stringify(part, null, 2) : "")}</pre>
                        </div>
                      )}</For>
                    </article>
                  )}</For>
                </Show>
              </div>
              <Show when={workflowRun()}><div class="workflow-assistant-run-state">Workflow status: {JSON.stringify(workflowRun())}</div></Show>
              <div class="workflow-assistant-composer">
                <textarea value={composer()} disabled={!canChat()} onInput={(e) => setComposer(e.currentTarget.value)} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void sendMessage(); } }} placeholder={canChat() ? "Ask about this task, plan, tools, or outputs..." : "OpenCode session is not bound or unavailable"} />
                <button type="button" disabled={!canChat()} onClick={() => busy() ? void abortAssistant() : void sendMessage()}>{busy() ? "Stop" : "Send"}</button>
              </div>
            </>
          }>
            <div class="workflow-assistant-empty">Loading assistant context...</div>
          </Show>
        </div>
      </section>
    </Show>
  );
}

function transitionForSegment(segment, judgements, schemeId) {
  if (schemeId !== "scheme_1")
    return null;
  const start = Number(segment?.start);
  if (!Number.isFinite(start) || start <= 0)
    return null;
  return (judgements || []).find((item) => item?.is_transition && Math.abs(Number(item.time) - start) <= 0.15) || null;
}

function transitionSourceMeta(transition) {
  const sources = (transition?.sources || []).map((item) => String(item));
  const reviewed = transition?.review_status === "reviewed_by_vlm" || sources.includes("open_code_vlm");
  const fromPyScene = sources.includes("pyscenedetect");
  const fromVlm = reviewed && sources.includes("open_code_vlm");
  const locationPair = [transition?.before_location, transition?.after_location].filter(Boolean).join(" -> ");
  const locationDetail = locationPair ? `${locationPair} · ` : "";
  if (!reviewed)
    return { level: "unreviewed", label: "未审候选", detail: "仅本地候选，未送大模型确认" };
  if (transition?.is_reshoot_boundary && fromPyScene && fromVlm)
    return { level: "dual", label: "重点转场", detail: `${locationDetail}PySceneDetect + 大模型复拍边界` };
  if (transition?.is_reshoot_boundary && fromVlm)
    return { level: "vlm", label: transition?.location_changed ? "地点转场" : "结构转场", detail: `${locationDetail}大模型复拍边界` };
  if (transition?.same_location)
    return { level: "same-location", label: "同场景变化", detail: `${locationDetail}同地点内画面变化，不作为复拍重点` };
  if (fromVlm)
    return { level: "vlm", label: "大模型转场", detail: `${locationDetail}大模型视觉判断` };
  if (fromPyScene)
    return { level: "pyscene", label: "PySceneDetect", detail: "PySceneDetect 检测" };
  return { level: "other", label: "转场", detail: sources.length ? sources.join(" + ") : "转场判断" };
}

function formatVersionChipTime(value) {
  return new Date(value).toLocaleString([], { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
}

export default function OpenClipModule(props) {
  const [tasks, setTasks] = createSignal([]);
  const [selectedTaskId, setSelectedTaskId] = createSignal(taskIdFromHash(props.routeHash));
  const [detail, setDetail] = createSignal(null);
  const [draft, setDraft] = createSignal(null);
  const [error, setError] = createSignal("");
  const [busy, setBusy] = createSignal("");
  const [selectedSchemeId, setSelectedSchemeId] = createSignal("scheme_2");
  const [viewMode, setViewMode] = createSignal("card");
  const [cardColumns, setCardColumns] = createSignal(6);
  const [viewMenuOpen, setViewMenuOpen] = createSignal(false);
  const [schemeCards, setSchemeCards] = createSignal([]);
  const [schemeLoadKey, setSchemeLoadKey] = createSignal("");
  const [summary, setSummary] = createSignal(null);
  const [events, setEvents] = createSignal([]);
  const [taskListOpen, setTaskListOpen] = createSignal(false);
  const [promptDrawerOpen, setPromptDrawerOpen] = createSignal(false);
  const [assistantOpen, setAssistantOpen] = createSignal(false);
  const [skillDrawerOpen, setSkillDrawerOpen] = createSignal(false);
  const [promptModelDialogOpen, setPromptModelDialogOpen] = createSignal(false);
  const [promptModelDialogPosition, setPromptModelDialogPosition] = createSignal(null);
  const [runModelDialogOpen, setRunModelDialogOpen] = createSignal(false);
  const [promptPreviewOpen, setPromptPreviewOpen] = createSignal(null);
  const [segmentPreview, setSegmentPreview] = createSignal(null);
  const [promptModelFilter, setPromptModelFilter] = createSignal("");
  const [runModelFilter, setRunModelFilter] = createSignal("");
  const [selectedPromptVersionId, setSelectedPromptVersionId] = createSignal(null);
  const [selectedSkillVersionId, setSelectedSkillVersionId] = createSignal(null);

  const task = createMemo(() => detail()?.task ?? null);
  const options = createMemo(() => detail()?.options ?? { industry: [], persona: [], target_audience: [], analysis_goal: [], video_formula: [] });
  const promptModels = createMemo(() => detail()?.prompt_models ?? { items: [], default_model: { providerID: "", modelID: "" } });
  const promptModelProviders = createMemo(() => {
    const seen = new Map();
    promptModels().items.forEach((item) => {
      if (!seen.has(item.providerID))
        seen.set(item.providerID, item.providerName);
    });
    return Array.from(seen.entries()).map(([providerID, providerName]) => ({ providerID, providerName }));
  });
  const latestAttempt = createMemo(() => detail()?.attempts?.[0] ?? null);
  const latestRun = createMemo(() => {
    const currentTask = task();
    if (!currentTask?.session_id)
      return null;
    return { sessionId: currentTask.session_id, taskUrl: openClipApi.taskDetailUrl(currentTask.id), attempt: latestAttempt() };
  });
  const selectedPromptModel = createMemo(() => promptModels().items.find((item) => item.providerID === draft()?.prompt_model_provider && item.modelID === draft()?.prompt_model_id));
  const selectedRunModel = createMemo(() => promptModels().items.find((item) => item.providerID === draft()?.run_model_provider && item.modelID === draft()?.run_model_id));
  const filteredPromptModels = createMemo(() => {
    const providerID = draft()?.prompt_model_provider;
    const keyword = promptModelFilter().trim().toLowerCase();
    return promptModels().items.filter((item) => {
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
    return promptModels().items.filter((item) => {
      if (providerID && item.providerID !== providerID)
        return false;
      if (!keyword)
        return true;
      return `${item.providerName} ${item.modelName} ${item.modelID}`.toLowerCase().includes(keyword);
    });
  });
  const selectedSchemeCards = createMemo(() => schemeCards());
  const previewModeLabel = createMemo(() => viewMode() === "list" ? "List" : `Card - ${cardColumns()}`);
  const basePromptVersion = createMemo(() => detail()?.base_prompt_version ?? null);
  const savedPromptVersion = createMemo(() => basePromptVersion() ?? (detail()?.current_prompt_version?.id ? detail()?.current_prompt_version : null) ?? detail()?.prompt_versions?.[0] ?? null);
  const promptVersionDirty = createMemo(() => Boolean(detail()?.prompt_version_dirty));
  const activePromptVersionId = createMemo(() => detail()?.current_prompt_version?.id ?? basePromptVersion()?.id ?? detail()?.prompt_versions?.[0]?.id ?? null);
  const currentSkillVersion = createMemo(() => detail()?.current_skill_version ?? null);
  const skillVersionDirty = createMemo(() => Boolean(detail()?.skill_version_dirty));
  const canGenerateSkill = createMemo(() => Boolean(savedPromptVersion()?.id));
  const promptPreviewTitle = createMemo(() => promptPreviewOpen() === "simple" ? "Simple Prompt" : "Final Prompt / Output");
  const promptPreviewText = createMemo(() => promptPreviewOpen() === "simple" ? (draft()?.simple_prompt || "") : (draft()?.final_prompt || ""));

  const runAction = async (key, fn) => {
    setBusy(key);
    setError("");
    try {
      await fn();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setBusy("");
    }
  };

  const assetPathForClip = (clipPath, extension) => String(clipPath || "").replace(/\.mp4$/i, extension);

  const segmentAssetPath = (item, kind) => {
    if (kind === "json" && item.retakeDescriptionPath)
      return item.retakeDescriptionPath;
    if (kind === "srt" && item.srtPath)
      return item.srtPath;
    return assetPathForClip(item.clipPath, kind === "json" ? ".json" : ".srt");
  };

  const segmentVideoPath = (item) => item?.clipStatus === "virtual" ? (item.sourceVideoPath || item.clipPath) : item?.clipPath;

  const segmentVideoUrl = (sessionId, item, attemptId) => `${openClipApi.rawFileUrl(sessionId, segmentVideoPath(item))}?v=${attemptId || ""}`;

  const openSegmentAsset = async (sessionId, item, kind) => {
    const isJson = kind === "json";
    const filePath = segmentAssetPath(item, kind);
    setSegmentPreview({
      kind,
      title: `${String(item.index).padStart(2, "0")} · ${item.title}`,
      filePath,
      loading: true,
      body: "",
    });
    try {
      const res = await fetch(`${openClipApi.rawFileUrl(sessionId, filePath)}?v=${latestAttempt()?.id || "workspace"}`, { credentials: "include" });
      if (!res.ok)
        throw new Error(`Unable to load ${filePath} (${res.status})`);
      const text = await res.text();
      const jsonData = isJson ? JSON.parse(text) : null;
      const body = isJson ? JSON.stringify(jsonData, null, 2) : text;
      const meta = isJson ? segmentJsonMeta(jsonData) : {};
      setSegmentPreview({ kind, title: `${String(item.index).padStart(2, "0")} · ${meta.title || item.title}`, filePath, loading: false, body, jsonData });
    } catch (err) {
      setSegmentPreview({
        kind,
        title: `${String(item.index).padStart(2, "0")} · ${item.title}`,
        filePath,
        loading: false,
        error: err instanceof Error ? err.message : "Failed to load file",
        body: "",
      });
    }
  };

  const openPromptModelDialog = () => {
    setPromptModelDialogPosition(null);
    setPromptModelDialogOpen(true);
  };

  const closePromptModelDialog = () => {
    setPromptModelDialogOpen(false);
    setPromptModelDialogPosition(null);
  };

  const promptModelDialogStyle = () => {
    const position = promptModelDialogPosition();
    if (!position)
      return {};
    return { left: `${position.x}px`, top: `${position.y}px`, transform: "none" };
  };

  const startPromptModelDialogDrag = (event) => {
    if (event.button !== 0)
      return;
    const dialog = event.currentTarget.closest(".openclip-prompt-model-dialog");
    if (!dialog)
      return;
    const rect = dialog.getBoundingClientRect();
    const offsetX = event.clientX - rect.left;
    const offsetY = event.clientY - rect.top;
    const onMove = (moveEvent) => {
      const maxX = Math.max(8, window.innerWidth - rect.width - 8);
      const maxY = Math.max(8, window.innerHeight - rect.height - 8);
      setPromptModelDialogPosition({
        x: Math.min(Math.max(8, moveEvent.clientX - offsetX), maxX),
        y: Math.min(Math.max(8, moveEvent.clientY - offsetY), maxY),
      });
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  const syncDetail = (payload) => {
    setDetail(payload);
    const models = payload.prompt_models ?? promptModels();
    const defaultProvider = models.default_model?.providerID || "";
    const defaultModel = models.default_model?.modelID || "";
    const defaultPromptModel = findModelPresetItem(models.items, "max") ?? findModelPresetItem(models.items, "flash") ?? null;
    const defaultRunModel = findPreferredRunModel(models.items) ?? models.items?.find((item) => item.providerID === defaultProvider && item.modelID === defaultModel) ?? null;
    setDraft({
      ...payload.task,
      prompt_model_provider: payload.task?.prompt_model_provider || payload.current_prompt_version?.prompt_model_provider || defaultPromptModel?.providerID || defaultProvider,
      prompt_model_id: payload.task?.prompt_model_id || payload.current_prompt_version?.prompt_model_id || defaultPromptModel?.modelID || defaultModel,
      run_model_provider: payload.task?.run_model_provider || defaultRunModel?.providerID || defaultProvider,
      run_model_id: payload.task?.run_model_id || defaultRunModel?.modelID || defaultModel,
      version_name: payload.current_prompt_version?.name || "",
      version_notes: payload.current_prompt_version?.notes || "",
      skill_version_name: payload.task?.skill_version_name || payload.current_skill_version?.name || "",
      skill_version_notes: payload.task?.skill_version_notes || payload.current_skill_version?.notes || "",
      final_prompt: payload.task?.final_prompt || payload.current_prompt_version?.final_prompt || "",
      skill_content: payload.task?.generated_skill_content || payload.current_skill_version?.skill_content || "",
    });
    setSelectedPromptVersionId(payload.current_prompt_version?.id ?? payload.base_prompt_version?.id ?? null);
    setSelectedSkillVersionId(payload.current_skill_version?.id ?? null);
  };

  const loadTasks = async () => {
    const res = await openClipApi.tasks();
    setTasks(res.items ?? []);
    if (!selectedTaskId() && res.items?.length) {
      const nextId = taskIdFromHash(props.routeHash) ?? res.items[0].id;
      setSelectedTaskId(nextId);
    }
  };

  const loadSummaryAndScheme = async (sessionId, schemeId, attemptId = latestAttempt()?.id, force = false) => {
    const nextKey = `${sessionId}:${schemeId}:${attemptId || "none"}`;
    if (!force && schemeLoadKey() === nextKey) {
      return;
    }
    const cacheBust = attemptId || "latest";
    const summaryRes = await fetch(`${openClipApi.rawFileUrl(sessionId, "reports/analysis_summary.json")}?v=${cacheBust}`, { credentials: "include" });
    const manifestRes = await fetch(`${openClipApi.rawFileUrl(sessionId, "storyboards/scheme_filename_manifest.json")}?v=${cacheBust}`, { credentials: "include" });
    const schemeManifestRes = await fetch(`${openClipApi.rawFileUrl(sessionId, `schemes/${schemeId}/manifest.json`)}?v=${cacheBust}`, { credentials: "include" });
    const dialogueRes = await fetch(`${openClipApi.rawFileUrl(sessionId, `transcripts/original_dialogue_segments_${schemeId}.json`)}?v=${cacheBust}`, { credentials: "include" });
    const transitionRes = await fetch(`${openClipApi.rawFileUrl(sessionId, "meta/vlm_transition_judgement.json")}?v=${cacheBust}`, { credentials: "include" });
    const nextSummary = summaryRes.ok ? await summaryRes.json() : null;
    const manifest = manifestRes.ok ? await manifestRes.json() : [];
    const schemeManifest = schemeManifestRes.ok ? await schemeManifestRes.json() : null;
    const dialogues = dialogueRes.ok ? await dialogueRes.json() : [];
    const transitionJudgement = transitionRes.ok ? await transitionRes.json() : null;
    const schemeManifestItems = Array.isArray(schemeManifest?.items) ? schemeManifest.items : [];
    const directory = schemeManifestItems.length ? { files: [] } : await openClipApi.taskFiles(sessionId, `schemes/${schemeId}`).catch(() => ({ files: [] }));
    const files = (directory.files ?? [])
      .filter((item) => item.type === "file" && item.name.toLowerCase().endsWith(".mp4"))
      .sort((left, right) => clipSortIndex(left) - clipSortIndex(right) || String(left.name).localeCompare(String(right.name), undefined, { numeric: true }));
    const storyboardManifestItems = Array.isArray(manifest) ? manifest.filter((item) => item.scheme === schemeId) : [];
    const manifestItems = schemeManifestItems.length ? schemeManifestItems : storyboardManifestItems;
    const cardEntries = manifestItems.length
      ? manifestItems.map((item, index) => {
        const path = workspaceRelativePath(item.clip_path || item.source_video_path || schemeManifest?.source_video_path || "");
        const jsonPath = workspaceRelativePath(item.retake_description_path || assetPathForClip(path, ".json"));
        return { item, index, path, jsonPath, key: jsonPath || `${index}:${path}` };
      })
      : files.map((file, index) => {
        const jsonPath = assetPathForClip(file.path, ".json");
        return { item: {}, index, file, path: file.path, jsonPath, key: jsonPath || file.path };
      });
    const jsonByPath = new Map(await Promise.all(cardEntries.map(async (entry) => {
      const jsonPath = workspaceRelativePath(entry.jsonPath || entry.item.retake_description_path || entry.item.retakeDescriptionPath || assetPathForClip(entry.path, ".json"));
      try {
        const res = await fetch(`${openClipApi.rawFileUrl(sessionId, jsonPath)}?v=${cacheBust}`, { credentials: "include" });
        return [entry.key, res.ok ? await res.json() : null];
      } catch (_err) {
        return [entry.key, null];
      }
    })));
    setSummary(nextSummary);
    setSchemeCards(cardEntries.map((entry) => {
      const file = entry.file || { name: pathBasename(entry.path), path: entry.path };
      const manifestMatch = manifestItems.find((candidate) => workspaceRelativePath(candidate.clip_path) === file.path || String(candidate.clip_path || "").endsWith(`/${file.name}`) || candidate.clip_filename === file.name);
      const item = Object.keys(entry.item || {}).length ? entry.item : manifestMatch ?? {};
      const jsonMeta = segmentJsonMeta(jsonByPath.get(entry.key));
      const sourceVideoPath = workspaceRelativePath(item.source_video_path || schemeManifest?.source_video_path || item.clip_path || entry.path);
      const clipPath = workspaceRelativePath(item.clip_path || entry.path || sourceVideoPath);
      const segment = {
        ...item,
        index: item.index ?? item.segment_index ?? jsonMeta.index ?? entry.index + 1,
        title: jsonMeta.title ?? item.title ?? file.name.replace(/\.mp4$/i, "").replace(/^\d+_\[[^\]]+\]_/, ""),
        start: jsonMeta.start ?? item.start ?? "-",
        end: jsonMeta.end ?? item.end ?? "-",
        duration: jsonMeta.duration ?? item.duration,
        clip_filename: file.name,
        clipPath,
        sourceVideoPath,
        srtPath: workspaceRelativePath(item.srt_path || item.srtPath || assetPathForClip(clipPath, ".srt")),
        retakeDescriptionPath: workspaceRelativePath(item.retake_description_path || item.retakeDescriptionPath || assetPathForClip(clipPath, ".json")),
        clipStatus: item.clip_status || item.clipStatus || "exported",
        dialogueText: jsonMeta.dialogueText || dialogues[entry.index]?.text || "",
        semanticRole: jsonMeta.semanticRole || item.semantic_role || "",
        formulaSlot: jsonMeta.formulaSlot || item.formula_slot || "",
      };
      const transition = transitionForSegment(segment, transitionJudgement?.items || [], schemeId);
      const transitionSource = transitionSourceMeta(transition);
      return {
        ...segment,
        transitionJudgement: transition,
        transitionSource,
      };
    }));
    setSchemeLoadKey(nextKey);
  };

  const loadEvents = async (taskId) => {
    const res = await openClipApi.taskEvents(taskId, 0);
    setEvents(res.items ?? []);
  };

  const loadTask = async (taskId) => {
    if (!taskId)
      return;
    const res = await openClipApi.taskDetail(taskId);
    syncDetail(res);
    setSelectedTaskId(taskId);
    await loadSummaryAndScheme(res.task.session_id, selectedSchemeId(), res.attempts?.[0]?.id, true);
    await loadEvents(taskId);
  };

  const refreshCurrentTask = async () => {
    if (!selectedTaskId())
      return;
    const res = await openClipApi.taskDetail(selectedTaskId());
    const previousAttemptId = latestAttempt()?.id;
    syncDetail(res);
    const nextAttemptId = res.attempts?.[0]?.id;
    if (res.task?.session_id) {
      await loadSummaryAndScheme(res.task.session_id, selectedSchemeId(), nextAttemptId, true);
    }
    await loadEvents(selectedTaskId());
    await loadTasks();
  };

  const createTask = async () => {
    const res = await openClipApi.createTask();
    syncDetail(res);
    await loadTasks();
    setTaskListOpen(false);
    window.location.hash = openClipApi.taskDetailUrl(res.task.id);
  };

  const deleteTask = async (taskId) => {
    if (!window.confirm(`Delete OC-Analysis task #${taskId}?`))
      return;
    await openClipApi.deleteTask(taskId);
    if (selectedTaskId() === taskId) {
      setDetail(null);
      setDraft(null);
      setSummary(null);
      setSchemeCards([]);
      setSelectedTaskId(null);
      window.location.hash = "#/openclip/tasks";
    }
    await loadTasks();
  };

  const selectTask = async (taskId) => {
    setTaskListOpen(false);
    window.location.hash = openClipApi.taskDetailUrl(taskId);
  };

  const updateDraft = (key, value) => setDraft((prev) => ({ ...prev, [key]: value }));

  const updatePromptPreviewText = (value) => {
    if (promptPreviewOpen() === "simple") {
      updateDraft("simple_prompt", value);
      return;
    }
    updateDraft("final_prompt", value);
  };

  const updatePromptModelProvider = (providerID) => {
    const models = promptModels().items.filter((item) => item.providerID === providerID);
    const preferred = models.find((item) => item.modelID === promptModels().default_model.modelID) ?? models[0];
    setDraft((prev) => ({ ...prev, prompt_model_provider: providerID, prompt_model_id: preferred?.modelID ?? "" }));
  };

  const updateRunModelProvider = (providerID) => {
    const models = promptModels().items.filter((item) => item.providerID === providerID);
    const preferred = findPreferredRunModel(models) ?? models.find((item) => item.modelID === promptModels().default_model.modelID) ?? models[0];
    setDraft((prev) => ({ ...prev, run_model_provider: providerID, run_model_id: preferred?.modelID ?? "" }));
  };

  const selectPromptModelPreset = (selection) => {
    setDraft((prev) => ({ ...prev, prompt_model_provider: selection.providerID, prompt_model_id: selection.modelID }));
  };

  const selectRunModelPreset = (selection) => {
    setDraft((prev) => ({ ...prev, run_model_provider: selection.providerID, run_model_id: selection.modelID }));
  };

  const saveConfig = async () => {
    const res = await openClipApi.saveConfig(selectedTaskId(), draft());
    syncDetail({ ...res, prompt_models: promptModels() });
    await loadTasks();
    return res;
  };

  const rebuildSimplePrompt = async () => {
    await saveConfig();
    const res = await openClipApi.rebuildSimplePrompt(selectedTaskId(), draft());
    syncDetail({ ...res, prompt_models: promptModels() });
  };

  const generatePrompt = async () => {
    await saveConfig();
    const res = await openClipApi.generatePrompt(selectedTaskId(), {
      prompt_model_provider: draft().prompt_model_provider,
      prompt_model_id: draft().prompt_model_id,
    });
    syncDetail(res);
    closePromptModelDialog();
  };

  const savePromptVersion = async () => {
    const versionName = draft().version_name || "";
    const versionNotes = draft().version_notes ?? "";
    await saveConfig();
    const res = await openClipApi.savePromptVersion(selectedTaskId(), {
      version_name: versionName,
      version_notes: versionNotes,
    });
    syncDetail({ ...res, prompt_models: promptModels() });
  };

  const saveDraftOrCurrentPromptVersion = async () => {
    const saved = await saveConfig();
    const versionId = saved?.task?.current_prompt_version_id;
    if (!versionId)
      return saved;
    const res = await openClipApi.updatePromptVersion(selectedTaskId(), versionId);
    syncDetail({ ...res, prompt_models: promptModels() });
    return res;
  };

  const loadPromptVersion = async (versionId) => {
    const res = await openClipApi.loadPromptVersion(selectedTaskId(), versionId);
    syncDetail({ ...res, prompt_models: promptModels() });
  };

  const deletePromptVersion = async (versionId) => {
    if (!window.confirm("Delete this Prompt Builder version?"))
      return;
    const res = await openClipApi.deletePromptVersion(selectedTaskId(), versionId);
    syncDetail({ ...res, prompt_models: promptModels() });
  };

  const generateSkill = async () => {
    const res = await openClipApi.generateSkill(selectedTaskId(), {
      prompt_version_id: savedPromptVersion()?.id ?? null,
    });
    syncDetail({ ...res, prompt_models: promptModels() });
  };

  const saveSkillDraft = async () => {
    const res = await openClipApi.saveSkillDraft(selectedTaskId(), {
      skill_content: draft().skill_content || "",
      version_name: draft().skill_version_name || "",
      version_notes: draft().skill_version_notes || "",
    });
    syncDetail({ ...res, prompt_models: promptModels() });
  };

  const saveSkillVersion = async () => {
    const editorContent = document.querySelector("textarea.openflow-skill-builder-editor")?.value;
    const content = editorContent ?? draft().skill_content ?? "";
    const versionName = draft().skill_version_name || "";
    const versionNotes = draft().skill_version_notes || currentSkillVersion()?.notes || "Saved current Skill";
    const draftRes = await openClipApi.saveSkillDraft(selectedTaskId(), {
      skill_content: content,
      version_name: versionName,
      version_notes: versionNotes,
    });
    syncDetail({ ...draftRes, prompt_models: promptModels() });
    const res = await openClipApi.saveSkillVersion(selectedTaskId(), {
      prompt_version_id: savedPromptVersion()?.id ?? null,
      skill_content: content,
      version_name: versionName,
      version_notes: versionNotes,
    });
    syncDetail({ ...res, prompt_models: promptModels() });
  };

  const loadSkillVersion = async (versionId) => {
    const res = await openClipApi.loadSkillVersion(selectedTaskId(), versionId);
    syncDetail({ ...res, prompt_models: promptModels() });
  };

  const deleteSkillVersion = async (versionId) => {
    if (!window.confirm(selectedSkillVersionId() === versionId ? "Delete the current Skill Builder version? The latest remaining version will become current." : "Delete this Skill Builder version?"))
      return;
    const res = await openClipApi.deleteSkillVersion(selectedTaskId(), versionId);
    syncDetail({ ...res, prompt_models: promptModels() });
  };

  const runTask = async () => {
    const payload = {
      skill_version_id: detail()?.current_skill_version?.id ?? null,
      run_model_provider: draft().run_model_provider,
      run_model_id: draft().run_model_id,
    };
    await openClipApi.run(selectedTaskId(), payload);
    setRunModelDialogOpen(false);
    await refreshCurrentTask();
  };

  const saveRunModel = async () => {
    await saveConfig();
    setRunModelDialogOpen(false);
  };

  const applyPreviewMode = (mode, columns = 6) => {
    setViewMode(mode);
    if (mode === "card") {
      setCardColumns(columns);
    }
    setViewMenuOpen(false);
  };

  createEffect(() => {
    const taskId = taskIdFromHash(props.routeHash);
    if (!taskId)
      return;
    if (taskId !== selectedTaskId() || !detail() || detail()?.task?.id !== taskId) {
      setSelectedTaskId(taskId);
      void loadTask(taskId);
    }
  });

  createEffect(() => {
    const sessionId = task()?.session_id;
    if (sessionId) {
      void loadSummaryAndScheme(sessionId, selectedSchemeId(), latestAttempt()?.id, true);
    }
  });

  onMount(async () => {
    const onTaskList = () => setTaskListOpen(true);
    const onNewTask = () => void runAction("newTask", createTask);
    window.addEventListener("openclip:task-list", onTaskList);
    window.addEventListener("openclip:new-task", onNewTask);
    onCleanup(() => {
      window.removeEventListener("openclip:task-list", onTaskList);
      window.removeEventListener("openclip:new-task", onNewTask);
    });
    await loadTasks();
    const taskId = taskIdFromHash(window.location.hash);
    if (taskId) {
      await loadTask(taskId);
      return;
    }
    const firstTaskId = taskIdFromHash(props.routeHash) ?? (tasks()[0]?.id ?? null);
    if (firstTaskId) {
      setSelectedTaskId(firstTaskId);
      window.location.hash = openClipApi.taskDetailUrl(firstTaskId);
    }
  });

  return (
    <>
      <Show when={error()}>
        <div class="banner bad openclip-banner">{error()}</div>
      </Show>
      <div class="openflow-page openclip-flow-page">
        <section class="card step-panel">
          <div class="step-card-head">
            <div class="step-title-wrap">
              <span class="step-badge">1</span>
              <h2>Analysis</h2>
              <StatusBadge status={task()?.status || "draft"} />
            </div>
            <div class="step-actions openflow-step-actions">
              <button class="icon-action" type="button" title="Prompt Builder" disabled={!task()} onClick={() => setPromptDrawerOpen(true)}><SlidersIcon /></button>
              <button class="icon-action" type="button" title="Task Assistant" disabled={!task()} onClick={() => setAssistantOpen(true)}><CodeIcon /></button>
              <button class="icon-action" type="button" title="Run Analysis" disabled={!task()} onClick={() => setRunModelDialogOpen(true)}><PlayIcon /></button>
            </div>
          </div>
          <div class="step-card-body openflow-summary-body">
            <div class="openflow-summary-fields">
              <div class="openflow-summary-item">
                <div class="openflow-summary-label">Reference Video</div>
                <div class="openflow-summary-value"><span class="inline-code">{task()?.reference_video_path || "-"}</span></div>
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
                <div class="openflow-summary-label">行业</div>
                <div class="openflow-summary-value">{task()?.industry || "-"}</div>
              </div>
              <div class="openflow-summary-item">
                <div class="openflow-summary-label">目标受众</div>
                <div class="openflow-summary-value">{task()?.target_audience || "-"}</div>
              </div>
              <div class="openflow-summary-item">
                <div class="openflow-summary-label">视频公式</div>
                <div class="openflow-summary-value">{task()?.video_formula || "-"}</div>
              </div>
              <div class="openflow-summary-item">
                <div class="openflow-summary-label">产品信息</div>
                <div class="openflow-summary-value openflow-prompt-preview">{task()?.product_info || "-"}</div>
              </div>
            </div>
          </div>
        </section>

        <Show when={latestRun()}>
          {(run) => (
            <section class="jobs-panel">
              <div class="jobs-panel-header">
                <div>
                  <div class="jobs-section-eyebrow openclip-latest-run-eyebrow">
                    <span>{run().attempt ? "Latest Run" : "Workspace Outputs"}</span>
                    <span>{formatTime(run().attempt?.finished_at || run().attempt?.created_at)}</span>
                  </div>
                </div>
              </div>
              <div class="openflow-main-schemes">
                <div class="openclip-scheme-toolbar">
                  <div class="openflow-main-scheme-tabs">
                    <For each={OPENCLIP_SCHEMES}>{(scheme) => (
                      <button class={`jobs-pill-button ${selectedSchemeId() === scheme.id ? "is-active" : ""}`} type="button" onClick={() => setSelectedSchemeId(scheme.id)}>
                        {scheme.title} / {scheme.label}
                      </button>
                    )}</For>
                  </div>
                  <div class="openclip-preview-controls">
                    <div class="openclip-view-menu-wrap">
                      <button class="jobs-pill-button" type="button" onClick={() => setViewMenuOpen((value) => !value)}>
                        {previewModeLabel()}
                      </button>
                      <Show when={viewMenuOpen()}>
                        <div class="openclip-view-menu">
                          <button class={`openclip-view-menu-item ${viewMode() === "list" ? "is-active" : ""}`} type="button" onClick={() => applyPreviewMode("list")}>List</button>
                          <For each={[3, 4, 5, 6]}>{(count) => (
                            <button class={`openclip-view-menu-item ${viewMode() === "card" && cardColumns() === count ? "is-active" : ""}`} type="button" onClick={() => applyPreviewMode("card", count)}>
                              Card - {count}
                            </button>
                          )}</For>
                        </div>
                      </Show>
                    </div>
                  </div>
                </div>
                <Show when={selectedSchemeCards().length > 0} fallback={<div class="jobs-empty">No split videos yet. Run analysis to generate clips.</div>}>
                  <Show when={viewMode() === "card"} fallback={
                    <div class="openclip-scheme-list">
                      <For each={selectedSchemeCards()}>{(item) => (
                        <div class="openclip-scheme-list-item">
                          <SchemeSegmentVideo class="openclip-scheme-list-video" item={item} src={segmentVideoUrl(run().sessionId, item, run().attempt?.id)} />
                          <div class="openclip-scheme-list-meta">
                            <strong>{String(item.index).padStart(2, "0")} · {item.title}</strong>
                            <span>{formatSeconds(item.start)} - {formatSeconds(item.end)}</span>
                            <Show when={item.formulaSlot || item.semanticRole}>
                              <div class="openclip-segment-meta-row">
                                <Show when={item.formulaSlot}><em>{item.formulaSlot}</em></Show>
                                <Show when={item.semanticRole}><em>{item.semanticRole}</em></Show>
                              </div>
                            </Show>
                            <Show when={item.transitionJudgement}>
                              {(transition) => (
                                <div class={`openclip-transition-note transition-${item.transitionSource?.level || "other"}`}>
                                  <span class="openclip-transition-badge">{item.transitionSource?.label || "转场"}</span>
                                  <span>{item.transitionSource?.detail || "转场判断"} · {transition().transition_label || "transition"} · 置信度 {transition().confidence}</span>
                                  <p>{transition().reason}</p>
                                </div>
                              )}
                            </Show>
                            <div class="openclip-segment-actions">
                              <button class="openclip-segment-action srt" type="button" onClick={() => void openSegmentAsset(run().sessionId, item, "srt")}><DocumentIcon /> 语音</button>
                              <button class="openclip-segment-action json" type="button" onClick={() => void openSegmentAsset(run().sessionId, item, "json")}><BracesIcon /> 复拍</button>
                            </div>
                          </div>
                        </div>
                      )}</For>
                    </div>
                  }>
                    <div class="openflow-main-scheme-cards openclip-card-grid" style={{ "grid-template-columns": `repeat(${cardColumns()}, minmax(0, 1fr))` }}>
                      <For each={selectedSchemeCards()}>{(item) => (
                        <div class={`openflow-main-scheme-card ${item.transitionSource?.level === "dual" ? "openclip-key-transition-card" : ""}`}>
                          <SchemeSegmentVideo class="openflow-main-scheme-video" item={item} src={segmentVideoUrl(run().sessionId, item, run().attempt?.id)} />
                          <div class="openflow-main-scheme-caption">
                            <strong>{String(item.index).padStart(2, "0")} · {item.title}</strong>
                            <span>{formatSeconds(item.start)} - {formatSeconds(item.end)}</span>
                            <Show when={item.formulaSlot || item.semanticRole}>
                              <div class="openclip-segment-meta-row">
                                <Show when={item.formulaSlot}><em>{item.formulaSlot}</em></Show>
                                <Show when={item.semanticRole}><em>{item.semanticRole}</em></Show>
                              </div>
                            </Show>
                            <Show when={item.transitionJudgement}>
                              {(transition) => (
                                <div class={`openclip-transition-note transition-${item.transitionSource?.level || "other"}`}>
                                  <span class="openclip-transition-badge">{item.transitionSource?.label || "转场"}</span>
                                  <span>{item.transitionSource?.detail || "转场判断"} · {transition().transition_label || "transition"} · 置信度 {transition().confidence}</span>
                                  <p>{transition().reason}</p>
                                </div>
                              )}
                            </Show>
                            <div class="openclip-segment-actions">
                              <button class="openclip-segment-action srt" type="button" onClick={() => void openSegmentAsset(run().sessionId, item, "srt")}><DocumentIcon /> 语音</button>
                              <button class="openclip-segment-action json" type="button" onClick={() => void openSegmentAsset(run().sessionId, item, "json")}><BracesIcon /> 复拍</button>
                            </div>
                          </div>
                        </div>
                      )}</For>
                    </div>
                  </Show>
                </Show>
                <Show when={summary()}>
                  <div class="openclip-slot-summary-row">
                    <For each={summary()?.slot_mapping_summary ?? []}>{(slot) => (
                      <div class="openclip-slot-summary-card">
                        <strong>{slot.label}</strong>
                        <span>{slot.start}s - {slot.end}s</span>
                      </div>
                    )}</For>
                  </div>
                </Show>
              </div>
            </section>
          )}
        </Show>
      </div>

      <Show when={taskListOpen()}>
        <div class="drawer-backdrop" onClick={() => setTaskListOpen(false)} />
        <section class="verify-dialog openflow-session-dialog">
          <div class="env-dialog-head">
            <div>
              <h3>OC-Analysis Tasks</h3>
              <p>Select a task to inspect or delete old tasks.</p>
            </div>
            <button class="secondary" onClick={() => setTaskListOpen(false)}>Close</button>
          </div>
          <div class="openflow-session-dialog-list">
            <div class="field-row openflow-session-dialog-toolbar">
              <button onClick={() => void runAction("newTask", createTask)}>New Task</button>
            </div>
            <For each={tasks()}>{(item) => (
              <div class={`openflow-session-dialog-item ${task()?.id === item.id ? "is-active" : ""}`}>
                <button class="openflow-session-dialog-main" type="button" onClick={() => void selectTask(item.id)}>
                  <strong>#{item.id}</strong>
                  <span>{item.status}</span>
                  <span>{formatTime(item.updated_at)}</span>
                </button>
                <button class="openflow-session-dialog-delete" type="button" title="Delete Task" onClick={() => void runAction(`delete-${item.id}`, () => deleteTask(item.id))}><TrashIcon /></button>
              </div>
            )}</For>
          </div>
        </section>
      </Show>

      <Show when={promptDrawerOpen() && draft()}>
        <div class="drawer-backdrop" onClick={() => setPromptDrawerOpen(false)} />
        <section class="skill-drawer openflow-config-drawer openflow-prompt-drawer">
          <div class="skill-drawer-head">
            <div class="ocrebuild-drawer-title-row">
              <h3>Prompt Builder</h3>
              <div class="openflow-version-chips ocrebuild-header-version-chips">
                <For each={detail()?.prompt_versions ?? []}>{(version) => (
                  <div class="openflow-version-chip-wrap">
                    <button class={`openflow-version-chip ${activePromptVersionId() === version.id ? "is-active" : ""} ${promptVersionDirty() && basePromptVersion()?.id === version.id ? "is-modified" : ""}`} type="button" title={`${version.name}${version.notes ? ` · ${version.notes}` : ""}`} onClick={() => void runAction(`load-prompt-${version.id}`, () => loadPromptVersion(version.id))}>
                      <strong>{formatVersionChipTime(version.created_at)}</strong>
                    </button>
                    <button class="openflow-version-delete" type="button" title="Delete Version" onClick={() => void runAction(`delete-prompt-${version.id}`, () => deletePromptVersion(version.id))}><TrashIcon /></button>
                  </div>
                )}</For>
              </div>
            </div>
            <div class="openflow-dialog-head-actions">
              <button class="icon-action openflow-icon-action success" type="button" title="Generate Simple Prompt" onClick={() => void runAction("rebuildSimple", rebuildSimplePrompt)}><ArrowsClockwiseIcon /></button>
              <button class="icon-action openflow-icon-action primary" type="button" title="Generate Final Prompt" onClick={openPromptModelDialog}><CodeIcon /></button>
              <button class="icon-action openflow-icon-action openclip-final-prompt-icon" type="button" title="Edit Final Prompt" onClick={() => setPromptPreviewOpen("final")}><DocumentIcon /></button>
              <button class="icon-action openflow-icon-action" type="button" title="Save Version" onClick={() => void runAction("savePromptVersion", savePromptVersion)}><ClockCounterClockwiseIcon /></button>
              <button class="icon-action openflow-icon-action" type="button" title="Save Draft / Current Version" onClick={() => void runAction("saveConfig", saveDraftOrCurrentPromptVersion)}><SaveIcon /></button>
              <button class="icon-action openflow-icon-action close" type="button" title="Close" onClick={() => setPromptDrawerOpen(false)}><CloseIcon /></button>
            </div>
          </div>
          <div class="openflow-config-drawer-body">
            <section class="openflow-builder-card openclip-parameter-card">
              <label class="openflow-field openflow-video-path-field">
                <span>参考视频路径 <small>/ OPTIONAL</small></span>
                <div class="openflow-input-with-actions">
                  <input value={draft().reference_video_path || ""} onInput={(e) => updateDraft("reference_video_path", e.currentTarget.value)} placeholder="粘贴视频链接或本地路径，如：/Users/xxx/video.mp4" />
                  <button class="openflow-inline-icon" type="button" title="Local path"><FolderIcon /></button>
                  <button class="openflow-inline-icon" type="button" title="Video link"><ConnectionIcon /></button>
                </div>
              </label>
              <div class="openflow-parameter-grid openflow-builder-parameter-grid">
                <label class="openflow-field">
                  <span>行业 <small>/ INDUSTRY</small></span>
                  <select value={optionValue(options().industry, draft().industry)} onChange={(e) => updateDraft("industry", e.currentTarget.value === "__custom__" ? "" : e.currentTarget.value)}><For each={options().industry}>{(item) => <option value={item}>{item}</option>}</For><option value="__custom__">自定义</option></select>
                  <Show when={optionValue(options().industry, draft().industry) === "__custom__"}><input value={draft().industry || ""} onInput={(e) => updateDraft("industry", e.currentTarget.value)} placeholder="自定义行业" /></Show>
                </label>
                <label class="openflow-field">
                  <span>人设 <small>/ PERSONA</small></span>
                  <select value={optionValue(options().persona, draft().persona)} onChange={(e) => updateDraft("persona", e.currentTarget.value === "__custom__" ? "" : e.currentTarget.value)}><For each={options().persona}>{(item) => <option value={item}>{item}</option>}</For><option value="__custom__">自定义</option></select>
                  <Show when={optionValue(options().persona, draft().persona) === "__custom__"}><input value={draft().persona || ""} onInput={(e) => updateDraft("persona", e.currentTarget.value)} placeholder="自定义人设" /></Show>
                </label>
                <label class="openflow-field">
                  <span>目标受众 <small>/ TARGET AUDIENCE</small></span>
                  <select value={optionValue(options().target_audience, draft().target_audience)} onChange={(e) => updateDraft("target_audience", e.currentTarget.value === "__custom__" ? "" : e.currentTarget.value)}><For each={options().target_audience}>{(item) => <option value={item}>{item}</option>}</For><option value="__custom__">自定义</option></select>
                  <Show when={optionValue(options().target_audience, draft().target_audience) === "__custom__"}><input value={draft().target_audience || ""} onInput={(e) => updateDraft("target_audience", e.currentTarget.value)} placeholder="自定义目标受众" /></Show>
                </label>
                <label class="openflow-field">
                  <span>分析目标 <small>/ ANALYSIS GOAL</small></span>
                  <select value={optionValue(options().analysis_goal, draft().analysis_goal)} onChange={(e) => updateDraft("analysis_goal", e.currentTarget.value === "__custom__" ? "" : e.currentTarget.value)}><For each={options().analysis_goal}>{(item) => <option value={item}>{item}</option>}</For><option value="__custom__">自定义</option></select>
                  <Show when={optionValue(options().analysis_goal, draft().analysis_goal) === "__custom__"}><input value={draft().analysis_goal || ""} onInput={(e) => updateDraft("analysis_goal", e.currentTarget.value)} placeholder="自定义分析目标" /></Show>
                </label>
                <label class="openflow-field">
                  <span>视频公式 <small>/ VIDEO FORMULA</small></span>
                  <select value={optionValue(options().video_formula, draft().video_formula)} onChange={(e) => updateDraft("video_formula", e.currentTarget.value === "__custom__" ? "" : e.currentTarget.value)}><For each={options().video_formula}>{(item) => <option value={item}>{item}</option>}</For><option value="__custom__">自定义</option></select>
                  <Show when={optionValue(options().video_formula, draft().video_formula) === "__custom__"}><input value={draft().video_formula || ""} onInput={(e) => updateDraft("video_formula", e.currentTarget.value)} placeholder="自定义视频公式" /></Show>
                </label>
                <label class="openflow-field openclip-product-field">
                  <span>产品信息 <small>/ PRODUCT INFO</small></span>
                  <textarea value={draft().product_info || ""} onInput={(e) => updateDraft("product_info", e.currentTarget.value)} placeholder="输入产品或服务的关键信息、卖点、使用场景等..." />
                  <em class="openflow-field-counter">{(draft().product_info || "").length} / 1000</em>
                </label>
                <label class="openflow-field openflow-field-wide openclip-constraints-field">
                  <span>约束条件 <small>/ CONSTRAINTS</small></span>
                  <textarea value={draft().constraints || ""} onInput={(e) => updateDraft("constraints", e.currentTarget.value)} placeholder="输入必须遵守的要求、限制条件、合规说明等..." />
                  <em class="openflow-field-counter">{(draft().constraints || "").length} / 1000</em>
                </label>
              </div>
            </section>

            <section class="openflow-builder-card openflow-source-card openclip-simple-prompt-card">
              <div class="openflow-card-head openclip-prompt-card-head">
                <div>
                  <h4>Simple Prompt</h4>
                </div>
                <button class="secondary openclip-text-button" type="button" onClick={() => setPromptPreviewOpen("simple")}>Full View</button>
              </div>
              <label class="openflow-field">
                <textarea value={draft().simple_prompt || ""} onInput={(e) => updateDraft("simple_prompt", e.currentTarget.value)} class="skill-editor openflow-modal-prompt openclip-simple-prompt-editor" placeholder="将在此处展示拼接后的简单提示词..." />
                <em class="openflow-field-counter">{(draft().simple_prompt || "").length} / 2000</em>
              </label>
            </section>

          </div>
        </section>
      </Show>

      <SharedWorkflowAssistantDrawer
        workflowId="openclip_analysis"
        taskId={task()?.id}
        sessionId={task()?.session_id}
        open={assistantOpen()}
        onClose={() => setAssistantOpen(false)}
      />

      <Show when={skillDrawerOpen() && draft()}>
        <div class="drawer-backdrop" onClick={() => setSkillDrawerOpen(false)} />
        <section class="skill-drawer openflow-config-drawer openflow-prompt-drawer openclip-skill-drawer">
          <div class="skill-drawer-head">
            <div>
              <h3>Skill Builder</h3>
            </div>
            <div class="openflow-dialog-head-actions">
              <button class="icon-action openflow-icon-action primary" type="button" title="Generate Skill" disabled={!canGenerateSkill() || busy() === "generateSkill"} onClick={() => void runAction("generateSkill", generateSkill)}><CodeIcon /></button>
              <button class="icon-action openflow-icon-action" type="button" title="Save Current" onClick={() => void runAction("saveSkillDraft", saveSkillDraft)}><SaveIcon /></button>
              <button class="icon-action openflow-icon-action" type="button" title="Save Version" disabled={!draft().skill_content} onClick={() => void runAction("saveSkillVersion", saveSkillVersion)}><ClockCounterClockwiseIcon /></button>
              <button class="icon-action openflow-icon-action close" type="button" title="Close" onClick={() => setSkillDrawerOpen(false)}><CloseIcon /></button>
            </div>
          </div>
          <div class="openflow-config-drawer-body">
            <section class="openflow-builder-card openclip-skill-source-card">
              <div class="openflow-card-head">
                <div>
                  <h4>Skill Source</h4>
                </div>
              </div>
              <div class="openclip-skill-source-grid">
                <div class="openclip-version-context">
                  <strong>Task</strong>
                  <span>#{task()?.id || "-"} / Session #{task()?.session_id || "-"}</span>
                </div>
                <div class={`openclip-version-context ${promptVersionDirty() ? "is-dirty" : ""}`}>
                  <strong>Current Final Prompt</strong>
                  <span>{savedPromptVersion()?.notes || savedPromptVersion()?.name || "No version notes"}</span>
                  <Show when={promptVersionDirty()}><em>Prompt draft changed. Skill generation still uses this saved Current Version.</em></Show>
                </div>
                <div class={`openclip-version-context ${skillVersionDirty() ? "is-dirty" : ""}`}>
                  <strong>{skillVersionDirty() ? "Editing Skill" : "Current Skill"}</strong>
                  <span>{currentSkillVersion()?.notes || currentSkillVersion()?.name || "No version notes"}</span>
                  <Show when={skillVersionDirty()}><em>Modified, save as a new version to freeze this state.</em></Show>
                </div>
              </div>
              <Show when={!canGenerateSkill()}>
                <div class="openflow-empty-version">请先在 Prompt Builder 中保存 Final Prompt Version，然后再生成 Skill。</div>
              </Show>
            </section>

            <section class="openflow-builder-card openclip-skill-version-card">
              <input class="openclip-version-notes-input openclip-skill-version-notes-input" value={draft().skill_version_notes || ""} onInput={(e) => updateDraft("skill_version_notes", e.currentTarget.value)} placeholder="Skill Version Notes" />
              <Show when={(detail()?.skill_versions ?? []).length > 0} fallback={<div class="openflow-empty-version">还没有保存 Skill Version。生成并确认 Skill 后，可保存为运行时版本。</div>}>
                <div class="openflow-version-chips openflow-version-switcher">
                  <For each={detail()?.skill_versions ?? []}>{(version) => (
                    <div class="openflow-version-chip-wrap">
                      <button class={`openflow-version-chip ${selectedSkillVersionId() === version.id ? "is-active" : ""} ${skillVersionDirty() && selectedSkillVersionId() === version.id ? "is-modified" : ""}`} type="button" title={`${version.name}${version.notes ? ` · ${version.notes}` : ""}`} onClick={() => void runAction(`load-skill-${version.id}`, () => loadSkillVersion(version.id))}>
                        <strong>{formatVersionChipTime(version.created_at)}</strong>
                        <span>{skillVersionDirty() && selectedSkillVersionId() === version.id ? "Modified from this version" : (version.notes || version.name || "Saved version")}</span>
                      </button>
                      <button class="openflow-version-delete" type="button" title="Delete Skill Version" onClick={() => void runAction(`delete-skill-${version.id}`, () => deleteSkillVersion(version.id))}><TrashIcon /></button>
                    </div>
                  )}</For>
                </div>
              </Show>
            </section>

            <section class="openflow-builder-card openclip-final-prompt-card openclip-skill-editor-card">
              <div class="openflow-card-head">
                <div>
                  <h4>Generated Skill</h4>
                </div>
              </div>
              <textarea class="skill-editor openflow-skill-builder-editor" value={draft().skill_content || ""} onInput={(e) => updateDraft("skill_content", e.currentTarget.value)} placeholder="Generate Skill 后，这里会出现完整的 OpenClip Analysis Skill，并可继续编辑。" />
            </section>
          </div>
        </section>
      </Show>

      <Show when={promptModelDialogOpen() && draft()}>
        <div class="drawer-backdrop openclip-model-overlay" onClick={closePromptModelDialog} />
        <section class="verify-dialog openflow-model-dialog openclip-prompt-model-dialog" style={promptModelDialogStyle()}>
          <div class="env-dialog-head openclip-model-dialog-head" onPointerDown={startPromptModelDialogDrag}>
            <div class="openclip-model-header-text">
              <h3>Select Model</h3>
            </div>
          </div>
          <div class="openflow-prompt-model-grid openclip-model-dialog-body model-preset-dialog-body">
            <ModelPresetCards
              items={promptModels().items}
              provider={draft().prompt_model_provider}
              model={draft().prompt_model_id}
              onSelect={selectPromptModelPreset}
              aria-label="Prompt model preset"
            />
          </div>
          <div class="openflow-model-dialog-summary openclip-model-selection">
            <div class="openflow-model-dialog-summary-body openclip-selection-card">
              <div class="openclip-selection-content">
                <em>{selectedPromptModel() ? modelDetail(selectedPromptModel()) : "No model available"}</em>
              </div>
            </div>
          </div>
          <div class="field-row openflow-model-dialog-actions openclip-model-dialog-actions">
            <button class="secondary openclip-model-cancel" onClick={closePromptModelDialog}>Cancel</button>
            <button class="openclip-model-confirm" disabled={!draft().prompt_model_provider || !draft().prompt_model_id || busy() === "generatePrompt"} onClick={() => void runAction("generatePrompt", generatePrompt)}>Confirm & Generate</button>
          </div>
        </section>
      </Show>

      <Show when={runModelDialogOpen() && draft()}>
        <div class="drawer-backdrop openclip-model-overlay" onClick={() => setRunModelDialogOpen(false)} />
        <section class="verify-dialog openflow-model-dialog openclip-prompt-model-dialog openclip-run-model-dialog">
          <div class="env-dialog-head openclip-model-dialog-head">
            <div class="openclip-model-header-text">
              <h3>Select Run Model</h3>
            </div>
          </div>
          <div class="openflow-prompt-model-grid openclip-model-dialog-body model-preset-dialog-body">
            <ModelPresetCards
              items={promptModels().items}
              provider={draft().run_model_provider}
              model={draft().run_model_id}
              onSelect={selectRunModelPreset}
              aria-label="Run model preset"
            />
          </div>
          <div class="openflow-model-dialog-summary openclip-model-selection">
            <div class="openflow-model-dialog-summary-body openclip-selection-card">
              <div class="openclip-run-selection-content">
                <em>{modelDetail(selectedRunModel())}</em>
                <span>Skill Notes: {detail()?.current_skill_version?.notes || detail()?.current_skill_version?.name || "-"}</span>
              </div>
            </div>
          </div>
          <div class="field-row openflow-model-dialog-actions openclip-model-dialog-actions openclip-run-model-actions">
            <button class="secondary openclip-model-cancel" onClick={() => setRunModelDialogOpen(false)}>Cancel</button>
            <button class="openclip-model-save" disabled={!draft().run_model_provider || !draft().run_model_id || busy() === "saveRunModel"} onClick={() => void runAction("saveRunModel", saveRunModel)}>Save</button>
            <button class="openclip-model-confirm" disabled={!detail()?.current_skill_version || !draft().run_model_provider || !draft().run_model_id || busy() === "run"} onClick={() => void runAction("run", runTask)}>Run Current Skill</button>
          </div>
        </section>
      </Show>

      <Show when={promptPreviewOpen() && draft()}>
        <div class="drawer-backdrop" onClick={() => setPromptPreviewOpen(null)} />
        <section class="verify-dialog openclip-prompt-preview-dialog">
          <div class="env-dialog-head">
            <div>
              <h3>{promptPreviewTitle()}</h3>
              <p>{promptPreviewText().length.toLocaleString()} characters</p>
            </div>
            <button class="secondary" onClick={() => setPromptPreviewOpen(null)}>Close</button>
          </div>
          <textarea class="skill-editor openclip-prompt-preview-textarea" value={promptPreviewText()} onInput={(e) => updatePromptPreviewText(e.currentTarget.value)} placeholder={promptPreviewOpen() === "simple" ? "将在此处展示拼接后的简单提示词..." : "Generate Final Prompt 后，会在这里完整展示并可继续编辑。"} />
        </section>
      </Show>

      <Show when={segmentPreview()}>
        {(preview) => (
          <>
            <div class="drawer-backdrop openclip-segment-preview-overlay" onClick={() => setSegmentPreview(null)} />
            <section class={`verify-dialog openclip-segment-preview-dialog preview-${preview().kind}`}>
              <div class="openclip-segment-preview-glow" />
              <div class="env-dialog-head openclip-segment-preview-head">
                <div>
                  <span class="openclip-segment-preview-kicker">{preview().kind === "json" ? "Retake Description JSON" : "Subtitle Track"}</span>
                  <h3>{preview().title}</h3>
                  <p>{preview().filePath}</p>
                </div>
                <button class="openclip-segment-preview-close" type="button" onClick={() => setSegmentPreview(null)}><CloseIcon /></button>
              </div>
              <div class="openclip-segment-preview-body">
                <Show when={!preview().loading} fallback={<div class="openclip-segment-preview-loading">Loading asset...</div>}>
                  <Show when={!preview().error} fallback={<div class="openclip-segment-preview-error">{preview().error}</div>}>
                    <Show when={preview().kind === "json" && preview().jsonData} fallback={<pre class="openclip-segment-preview-code">{preview().body || "Empty file"}</pre>}>
                      <div class="openclip-retake-table-wrap">
                        <For each={retakePreviewSections(preview().jsonData)}>{(section) => (
                          <section class="openclip-retake-section">
                            <h4>{section.title}</h4>
                            <table class="openclip-retake-table">
                              <tbody>
                                <For each={section.rows}>{(row) => (
                                  <tr>
                                    <th>{row[0]}</th>
                                    <td>{formatPreviewValue(row[1])}</td>
                                  </tr>
                                )}</For>
                              </tbody>
                            </table>
                          </section>
                        )}</For>
                      </div>
                    </Show>
                  </Show>
                </Show>
              </div>
            </section>
          </>
        )}
      </Show>
    </>
  );
}
