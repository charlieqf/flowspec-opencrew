import { For, Show, createEffect, createMemo, createSignal, onCleanup } from "solid-js";
import { workflowAssistantApi } from "./workflowAssistantApi";
import "./workflowAssistantStyles.css";

function CloseIcon() {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>;
}

function ReloadIcon() {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/></svg>;
}

function messageId(message) {
  return String(message?.info?.id || message?.id || message?.messageID || "");
}

function messageRole(message) {
  return String(message?.info?.role || message?.role || "assistant");
}

function messageTime(message) {
  return Number(message?.info?.time?.created || message?.info?.time?.completed || message?.created_at || 0) || 0;
}

function normalizeMessages(messages) {
  return [...(messages || [])].sort((a, b) => messageTime(a) - messageTime(b) || messageId(a).localeCompare(messageId(b)));
}

function partText(part) {
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

function planData(planRecord) {
  return planRecord?.plan_json || planRecord || null;
}

function extractPlanFromMessages(messages, partsByMessageId) {
  const candidates = [];
  for (const message of messages || []) {
    for (const part of Object.values(partsByMessageId[messageId(message)] || {})) {
      const text = partText(part);
      if (!text || !text.includes("workflow_tool_plan_v1"))
        continue;
      const fenced = [...text.matchAll(/```(?:json)?\s*([\s\S]*?)```/gi)].map((match) => match[1]);
      const rawCandidates = fenced.length ? fenced : [text.slice(text.indexOf("{"), text.lastIndexOf("}") + 1)];
      for (const raw of rawCandidates) {
        try {
          const parsed = JSON.parse(raw);
          if (parsed?.schema === "workflow_tool_plan_v1")
            candidates.push(parsed);
        } catch (_err) {
          // Ignore non-plan JSON fragments.
        }
      }
    }
  }
  return candidates[candidates.length - 1] || null;
}

export default function WorkflowAssistantDrawer(props) {
  const [bootstrap, setBootstrap] = createSignal(null);
  const [messages, setMessages] = createSignal([]);
  const [partsByMessageId, setPartsByMessageId] = createSignal({});
  const [composer, setComposer] = createSignal("");
  const [loading, setLoading] = createSignal(false);
  const [error, setError] = createSignal("");
  const [sessionStatus, setSessionStatus] = createSignal({ type: "idle" });
  const [workflowPlan, setWorkflowPlan] = createSignal(null);
  const [planDraft, setPlanDraft] = createSignal(null);
  const [planArgsText, setPlanArgsText] = createSignal({});
  const [planBusy, setPlanBusy] = createSignal("");
  const [planMessage, setPlanMessage] = createSignal("");
  const [workflowRun, setWorkflowRun] = createSignal(null);

  const workflowId = () => props.workflowId || "openclip_analysis";
  const taskId = () => props.taskId;
  const canChat = createMemo(() => Boolean(bootstrap()?.capabilities?.can_chat));
  const busy = createMemo(() => ["running", "thinking", "working"].includes(String(sessionStatus().type || "")));
  const quickPrompts = createMemo(() => bootstrap()?.quick_prompts || []);
  const context = createMemo(() => bootstrap()?.context || {});
  const extractedPlan = createMemo(() => extractPlanFromMessages(messages(), partsByMessageId()));

  function setCurrentPlan(record) {
    setWorkflowPlan(record || null);
    const data = planData(record);
    setPlanDraft(data ? JSON.parse(JSON.stringify(data)) : null);
    const nextArgs = {};
    for (const step of data?.steps || []) {
      nextArgs[step.id] = JSON.stringify(step.args || {}, null, 2);
    }
    setPlanArgsText(nextArgs);
  }

  function seedMessages(items) {
    const nextParts = {};
    for (const message of items || []) {
      const id = messageId(message);
      if (!id)
        continue;
      nextParts[id] = {};
      for (const part of message.parts || []) {
        const partId = String(part.id || part.partID || `${id}-part-${Object.keys(nextParts[id]).length + 1}`);
        nextParts[id][partId] = { ...part, id: partId, messageID: part.messageID || id };
      }
    }
    setMessages(normalizeMessages(items || []));
    setPartsByMessageId(nextParts);
  }

  async function loadBootstrap() {
    if (!props.open || !taskId())
      return;
    setLoading(true);
    setError("");
    try {
      const payload = await workflowAssistantApi.bootstrap(workflowId(), taskId());
      setBootstrap(payload);
      setSessionStatus({ type: "idle" });
      setCurrentPlan(payload.plan || null);
      seedMessages(payload.messages || []);
    } catch (exc) {
      setError(exc?.message || String(exc));
    } finally {
      setLoading(false);
    }
  }

  function upsertMessage(message) {
    const id = messageId(message);
    if (!id)
      return;
    setMessages((current) => normalizeMessages([...current.filter((item) => messageId(item) !== id && !String(messageId(item)).startsWith("local-")), message]));
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
      setCurrentPlan(properties.plan ? { plan_json: properties.plan, id: properties.plan_id, status: properties.plan.status } : properties);
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
    const source = new EventSource(workflowAssistantApi.eventsUrl(workflowId(), taskId()), { withCredentials: true });
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
    setMessages((current) => normalizeMessages([...current, localMessage]));
    setPartsByMessageId((current) => ({ ...current, [localId]: { [`${localId}-part`]: localMessage.parts[0] } }));
    setComposer("");
    setError("");
    try {
      await workflowAssistantApi.sendMessage(workflowId(), taskId(), value);
      setSessionStatus({ type: "running" });
    } catch (exc) {
      setMessages((current) => current.filter((item) => messageId(item) !== localId));
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
      await workflowAssistantApi.abort(workflowId(), taskId());
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
    const id = messageId(message);
    return Object.values(partsByMessageId()[id] || {});
  }

  function updateStep(stepId, patch) {
    setPlanDraft((current) => {
      if (!current)
        return current;
      return { ...current, steps: (current.steps || []).map((step) => step.id === stepId ? { ...step, ...patch } : step) };
    });
  }

  function updateStepArgs(stepId, value) {
    setPlanArgsText((current) => ({ ...current, [stepId]: value }));
    try {
      const parsed = JSON.parse(value || "{}");
      updateStep(stepId, { args: parsed });
      setPlanMessage("");
    } catch (_err) {
      setPlanMessage(`Step ${stepId} args JSON is invalid.`);
    }
  }

  function importExtractedPlan() {
    const plan = extractedPlan();
    if (!plan)
      return;
    setCurrentPlan({ plan_json: plan, status: plan.status || "draft" });
    setPlanMessage("Imported plan candidate from assistant message. Review and save it.");
  }

  async function savePlan() {
    if (!planDraft())
      return;
    setPlanBusy("save");
    setPlanMessage("");
    try {
      const res = await workflowAssistantApi.savePlan(workflowId(), taskId(), planDraft());
      setCurrentPlan(res.plan);
      setPlanMessage("Plan saved.");
    } catch (exc) {
      setPlanMessage(exc?.message || String(exc));
    } finally {
      setPlanBusy("");
    }
  }

  async function confirmPlan() {
    const plan = planDraft();
    if (!plan)
      return;
    const highCost = (plan.steps || []).filter((step) => step.enabled !== false && (step.requires_confirmation || step.uses_llm || step.uses_vlm || step.cost_level === "very_high"));
    if (highCost.length) {
      const labels = highCost.map((step) => `${step.id} ${step.tool_name || step.tool_id}`).join("\n");
      if (!window.confirm(`Confirm high-cost / LLM / VLM steps?\n${labels}`))
        return;
    }
    setPlanBusy("confirm");
    setPlanMessage("");
    try {
      const res = await workflowAssistantApi.confirmPlan(workflowId(), taskId(), { acknowledged_high_cost_step_ids: highCost.map((step) => step.id) });
      setCurrentPlan(res.plan);
      setPlanMessage((res.warnings || []).length ? `Plan confirmed with warning: ${res.warnings.join("; ")}` : "Plan confirmed and written back to OpenCode context.");
    } catch (exc) {
      setPlanMessage(exc?.message || String(exc));
    } finally {
      setPlanBusy("");
    }
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
            <button class="icon-action openflow-icon-action" type="button" title="Reload" onClick={() => void loadBootstrap()}><ReloadIcon /></button>
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
              <section class="workflow-plan-panel">
                <div class="workflow-plan-head">
                  <div>
                    <strong>Execution Plan</strong>
                    <span>{planDraft()?.status || workflowPlan()?.status || "No saved plan"}</span>
                  </div>
                  <div class="workflow-plan-actions">
                    <button type="button" disabled={!extractedPlan()} onClick={importExtractedPlan}>Import Latest JSON</button>
                    <button type="button" disabled={!planDraft() || planBusy()} onClick={() => void savePlan()}>{planBusy() === "save" ? "Saving..." : "Save Plan"}</button>
                    <button type="button" disabled={!planDraft() || planBusy()} onClick={() => void confirmPlan()}>{planBusy() === "confirm" ? "Confirming..." : "Confirm"}</button>
                  </div>
                </div>
                <Show when={planMessage()}><div class="workflow-plan-message">{planMessage()}</div></Show>
                <Show when={planDraft()} fallback={<div class="workflow-assistant-empty">No plan yet. Ask the assistant to generate workflow_tool_plan_v1 JSON, then import it here.</div>}>
                  <div class="workflow-plan-goal">
                    <label>Goal<input value={planDraft()?.goal || ""} onInput={(e) => setPlanDraft((current) => ({ ...current, goal: e.currentTarget.value }))} /></label>
                  </div>
                  <div class="workflow-plan-steps">
                    <For each={planDraft()?.steps || []}>{(step) => (
                      <article class={`workflow-plan-step ${step.enabled === false ? "is-disabled" : ""} ${step.requires_confirmation ? "is-high-cost" : ""}`}>
                        <div class="workflow-plan-step-main">
                          <label><input type="checkbox" checked={step.enabled !== false} onChange={(e) => updateStep(step.id, { enabled: e.currentTarget.checked })} /> Enabled</label>
                          <strong>{step.id} · {step.tool_id} {step.tool_name || ""}</strong>
                          <span>{step.cost_level || "medium"}{step.uses_llm ? " · LLM" : ""}{step.uses_vlm ? " · VLM" : ""}</span>
                        </div>
                        <label>Purpose<input value={step.purpose || ""} onInput={(e) => updateStep(step.id, { purpose: e.currentTarget.value })} /></label>
                        <label>Reason<textarea value={step.reason || ""} onInput={(e) => updateStep(step.id, { reason: e.currentTarget.value })} /></label>
                        <label>Args JSON<textarea class="workflow-plan-args" value={planArgsText()[step.id] || JSON.stringify(step.args || {}, null, 2)} onInput={(e) => updateStepArgs(step.id, e.currentTarget.value)} /></label>
                        <div class="workflow-plan-meta">Depends on: {(step.depends_on || []).join(", ") || "-"} · Outputs: {(step.expected_outputs || []).join(", ") || "-"}</div>
                      </article>
                    )}</For>
                  </div>
                </Show>
              </section>
              <div class="workflow-assistant-timeline">
                <Show when={messages().length > 0} fallback={<div class="workflow-assistant-empty">选择一个快捷提示，或直接询问当前 Task 的执行规划。Assistant 只规划，不直接执行工具。</div>}>
                  <For each={messages()}>{(message) => (
                    <article class={`workflow-message role-${messageRole(message)}`}>
                      <strong>{messageRole(message) === "user" ? "You" : "Assistant"}</strong>
                      <For each={messageParts(message)}>{(part) => (
                        <div class={`workflow-message-part part-${part.type || "text"}`}>
                          <Show when={part.type === "reasoning"}><em>Reasoning</em></Show>
                          <Show when={part.type === "tool"}><em>Tool event</em></Show>
                          <pre>{partText(part) || (part.type && part.type !== "text" ? JSON.stringify(part, null, 2) : "")}</pre>
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
