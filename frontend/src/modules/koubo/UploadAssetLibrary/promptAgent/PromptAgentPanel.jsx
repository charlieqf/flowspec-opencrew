import { For, Show, createEffect, createMemo, createSignal, onCleanup } from "solid-js";
import FlowIcon from "../components/FlowIcon.jsx";
import { extractPromptAgentResult, normalizedResult, visibleText } from "./promptAgentResult.js";
import "./promptAgent.css";

const AGENT_KEY = "prompt_agent";
const MODES = [
  { key: "critique", label: "批注" },
  { key: "optimize", label: "优化" },
  { key: "rewrite", label: "改写" },
  { key: "adapt", label: "模型适配" },
  { key: "compare", label: "对比", disabled: true },
];
const RESULT_LABELS = {
  critique: "批注结果",
  optimize: "优化结果",
  rewrite: "改写结果",
  adapt: "模型适配结果",
};
const MODEL_FAMILIES = ["image", "video", "digital_human", "lipsync", "general"];
const MODEL_FAMILY_LABELS = {
  image: "图像",
  video: "视频",
  digital_human: "数字人",
  lipsync: "对嘴型",
  general: "通用",
};

function text(value, fallback = "") {
  if (value === null || value === undefined || value === "") return fallback;
  return String(value);
}

function messageId(message) {
  const explicit = text(message?.id || message?.info?.id);
  if (explicit) return explicit;
  const time = messageTime(message);
  if (time) return `${messageRole(message)}_${time}`;
  let hash = 0;
  const source = rawMessageText(message);
  for (let index = 0; index < source.length; index += 1) hash = ((hash << 5) - hash + source.charCodeAt(index)) | 0;
  return `${messageRole(message)}_${Math.abs(hash)}`;
}

function messageRole(message) {
  return text(message?.role || message?.info?.role, "assistant").toLowerCase();
}

function messageTime(message) {
  const raw = Number(message?.created_at || message?.createdAt || message?.info?.time?.created || 0);
  return Number.isFinite(raw) ? raw : 0;
}

function partId(part, fallback = "") {
  return text(part?.id || part?.partID || part?.partId, fallback);
}

function partMessageId(part, fallback = "") {
  return text(part?.messageID || part?.messageId || part?.message_id, fallback);
}

function normalizePart(part, fallbackMessageId = "") {
  const id = partId(part, "text");
  return {
    ...part,
    id,
    messageID: partMessageId(part, fallbackMessageId),
    type: text(part?.type, "text"),
    text: text(part?.text),
  };
}

function normalizeParts(parts, fallbackMessageId = "") {
  const seen = new Map();
  for (const item of Array.isArray(parts) ? parts : []) {
    const normalized = normalizePart(item, fallbackMessageId);
    if (!normalized.id) continue;
    seen.set(normalized.id, normalized);
  }
  return [...seen.values()].sort((a, b) => String(a.id || "").localeCompare(String(b.id || "")));
}

function textFromParts(parts) {
  return normalizeParts(parts)
    .filter((part) => text(part?.type, "text") === "text")
    .map((part) => text(part?.text))
    .join("");
}

function rawMessageText(message) {
  if (text(message?.text)) return text(message.text);
  if (text(message?.content)) return text(message.content);
  return textFromParts(message?.parts);
}

function normalizeMessage(message) {
  const id = messageId(message);
  const parts = normalizeParts(message?.parts || [], id);
  return {
    ...message,
    id,
    role: messageRole(message),
    text: rawMessageText({ ...message, parts }),
    created_at: messageTime(message) || Date.now(),
    info: message?.info || {},
    parts,
  };
}

function normalizeMessages(items) {
  const seen = new Map();
  for (const item of Array.isArray(items) ? items : []) {
    const normalized = normalizeMessage(item);
    seen.set(normalized.id, normalized);
  }
  return [...seen.values()].sort((a, b) => (a.created_at || 0) - (b.created_at || 0));
}

function formatSeverity(value) {
  const severity = text(value, "medium").toLowerCase();
  if (severity === "high") return "高";
  if (severity === "low") return "低";
  return "中";
}

function firstModel(promptModels) {
  const items = Array.isArray(promptModels?.items) ? promptModels.items : [];
  return items[0] || {};
}

function modelProviderId(item = {}) {
  return text(item.providerID || item.provider);
}

function modelId(item = {}) {
  return text(item.modelID || item.model);
}

function modelKey(item = {}) {
  return `${modelProviderId(item)}::${modelId(item)}`;
}

function modelLabel(item = {}) {
  const provider = text(item.providerName || item.providerID || item.provider);
  const model = text(item.modelName || item.modelID || item.model);
  if (!provider) return model || "Model";
  if (!model || provider.toLowerCase() === model.toLowerCase()) return provider;
  return `${provider} / ${model}`;
}

export default function PromptAgentPanel(props) {
  let scrollEl;
  let bottomEl;
  const taskId = () => Number(props.task?.()?.id || 0);
  const [messages, setMessages] = createSignal([]);
  const [prompt, setPrompt] = createSignal("");
  const [lastPrompt, setLastPrompt] = createSignal("");
  const [mode, setMode] = createSignal("optimize");
  const [modelFamily, setModelFamily] = createSignal("image");
  const [provider, setProvider] = createSignal("");
  const [model, setModel] = createSignal("");
  const [settingsOpen, setSettingsOpen] = createSignal(false);
  const [promptModels, setPromptModels] = createSignal({ items: [] });
  const [normalizedResults, setNormalizedResults] = createSignal({});
  const [retrievalItems, setRetrievalItems] = createSignal([]);
  const [retrievalNotice, setRetrievalNotice] = createSignal("");
  const [currentRetrievalId, setCurrentRetrievalId] = createSignal("");
  const [busy, setBusy] = createSignal(false);
  const [error, setError] = createSignal("");
  const [savingId, setSavingId] = createSignal("");
  const [applyingKey, setApplyingKey] = createSignal("");
  const [copiedKey, setCopiedKey] = createSignal("");
  let eventSource;
  let eventSourceTaskId = 0;
  let eventSourceErrored = false;

  const modelItems = createMemo(() => Array.isArray(promptModels().items) ? promptModels().items : []);
  const providerItems = createMemo(() => {
    const seen = new Map();
    for (const item of modelItems()) {
      const providerID = modelProviderId(item);
      if (!providerID || seen.has(providerID)) continue;
      seen.set(providerID, text(item.providerName || item.provider || providerID));
    }
    return [...seen.entries()].map(([providerID, label]) => ({ providerID, label }));
  });
  const filteredModelItems = createMemo(() => {
    const providerID = provider();
    return modelItems().filter((item) => !providerID || modelProviderId(item) === providerID);
  });
  const selectedModelKey = createMemo(() => provider() && model() ? `${provider()}::${model()}` : "");
  const providerCountLabel = createMemo(() => `${providerItems().length} ${providerItems().length === 1 ? "provider" : "providers"}`);
  const modelCountLabel = createMemo(() => `${filteredModelItems().length} ${filteredModelItems().length === 1 ? "model" : "models"}`);
  const selectedModelLabel = createMemo(() => {
    const explicit = modelItems().find((item) => modelKey(item) === selectedModelKey());
    return explicit ? modelLabel(explicit) : "Auto";
  });
  const selectedModel = createMemo(() => {
    const explicit = modelItems().find((item) => modelKey(item) === selectedModelKey());
    if (explicit) return { providerID: modelProviderId(explicit), modelID: modelId(explicit) };
    if (provider()) {
      const firstForProvider = modelItems().find((item) => modelProviderId(item) === provider());
      if (firstForProvider) return { providerID: modelProviderId(firstForProvider), modelID: modelId(firstForProvider) };
    }
    const fallback = firstModel(promptModels());
    return {
      providerID: fallback.providerID || fallback.provider || "",
      modelID: fallback.modelID || fallback.model || "",
    };
  });

  createEffect(() => {
    const items = modelItems();
    if (provider() && !items.some((item) => modelProviderId(item) === provider())) {
      setProvider("");
      setModel("");
      return;
    }
    if (model() && !items.some((item) => modelProviderId(item) === provider() && modelId(item) === model())) setModel("");
  });

  const selectProvider = (providerID) => {
    setProvider(providerID);
    if (!providerID) {
      setModel("");
      return;
    }
    if (model() && !modelItems().some((item) => modelProviderId(item) === providerID && modelId(item) === model())) setModel("");
  };

  const selectModel = (item) => {
    if (!item) {
      setModel("");
      return;
    }
    setProvider(modelProviderId(item));
    setModel(modelId(item));
  };

  const scrollToBottom = () => {
    window.requestAnimationFrame(() => {
      if (bottomEl?.scrollIntoView) bottomEl.scrollIntoView({ block: "end" });
      else if (scrollEl) scrollEl.scrollTop = scrollEl.scrollHeight;
    });
  };

  createEffect(() => {
    messages();
    scrollToBottom();
  });

  const upsertMessage = (message) => {
    const normalized = normalizeMessage(message);
    if (!normalized.id) return null;
    setMessages((previous) => {
      const next = new Map(previous.map((item) => [item.id, item]));
      const current = next.get(normalized.id) || {};
      const parts = normalized.parts.length ? normalized.parts : normalizeParts(current.parts || [], normalized.id);
      const merged = normalizeMessage({
        ...current,
        ...normalized,
        info: { ...(current.info || {}), ...(normalized.info || {}) },
        parts,
        text: normalized.text || textFromParts(parts) || current.text || "",
        created_at: normalized.created_at || current.created_at || Date.now(),
      });
      next.set(merged.id, merged);
      return [...next.values()].sort((a, b) => (a.created_at || 0) - (b.created_at || 0));
    });
    return normalized;
  };

  const upsertPart = (part) => {
    const normalizedPart = normalizePart(part);
    const parentId = partMessageId(normalizedPart);
    if (!parentId || !normalizedPart.id) return;
    setMessages((previous) => {
      const next = new Map(previous.map((item) => [item.id, item]));
      const current = next.get(parentId) || {
        id: parentId,
        role: "assistant",
        created_at: Date.now(),
        info: { id: parentId, role: "assistant" },
        parts: [],
      };
      const byId = new Map(normalizeParts(current.parts || [], parentId).map((item) => [item.id, item]));
      byId.set(normalizedPart.id, normalizedPart);
      const parts = normalizeParts([...byId.values()], parentId);
      const merged = normalizeMessage({
        ...current,
        id: parentId,
        parts,
        text: textFromParts(parts),
        created_at: current.created_at || Date.now(),
      });
      next.set(parentId, merged);
      return [...next.values()].sort((a, b) => (a.created_at || 0) - (b.created_at || 0));
    });
  };

  const appendPartDelta = (properties) => {
    const field = text(properties?.field, "text");
    if (field && field !== "text") return;
    const parentId = text(properties?.messageID || properties?.messageId || properties?.message_id);
    if (!parentId) return;
    const id = text(properties?.partID || properties?.partId || properties?.id, `${parentId}_text`);
    const delta = String(properties?.delta ?? "");
    setMessages((previous) => {
      const next = new Map(previous.map((item) => [item.id, item]));
      const current = next.get(parentId) || {
        id: parentId,
        role: "assistant",
        created_at: Date.now(),
        info: { id: parentId, role: "assistant" },
        parts: [],
      };
      const byId = new Map(normalizeParts(current.parts || [], parentId).map((item) => [item.id, item]));
      const currentPart = byId.get(id) || { id, messageID: parentId, type: "text", text: "" };
      byId.set(id, normalizePart({ ...currentPart, text: `${text(currentPart.text)}${delta}` }, parentId));
      const parts = normalizeParts([...byId.values()], parentId);
      const merged = normalizeMessage({
        ...current,
        id: parentId,
        parts,
        text: textFromParts(parts),
        created_at: current.created_at || Date.now(),
      });
      next.set(parentId, merged);
      return [...next.values()].sort((a, b) => (a.created_at || 0) - (b.created_at || 0));
    });
  };

  const removePart = (properties) => {
    const parentId = text(properties?.messageID || properties?.messageId || properties?.message_id);
    const id = text(properties?.partID || properties?.partId || properties?.id);
    if (!parentId || !id) return;
    setMessages((previous) => {
      const next = new Map(previous.map((item) => [item.id, item]));
      const current = next.get(parentId);
      if (!current) return previous;
      const parts = normalizeParts(current.parts || [], parentId).filter((part) => part.id !== id);
      next.set(parentId, normalizeMessage({
        ...current,
        parts,
        text: textFromParts(parts),
      }));
      return [...next.values()].sort((a, b) => (a.created_at || 0) - (b.created_at || 0));
    });
  };

  const connectEvents = () => {
    const id = taskId();
    if (!id) return;
    if (eventSource && eventSourceTaskId === id && !eventSourceErrored && eventSource.readyState !== EventSource.CLOSED) return;
    if (eventSource) eventSource.close();
    eventSourceTaskId = id;
    eventSourceErrored = false;
    eventSource = new EventSource(props.api.agentChatEventsUrl(id, AGENT_KEY), { withCredentials: true });
    eventSource.onmessage = (event) => {
      try {
        eventSourceErrored = false;
        const payload = JSON.parse(event.data);
        if (payload?.type === "message.updated") {
          const message = payload?.properties?.message || payload?.properties;
          if (message) {
            const normalized = upsertMessage(message);
            if (normalized.role === "assistant" && normalized.info?.time?.completed) setBusy(false);
          }
        } else if (payload?.type === "message.part.updated") {
          upsertPart(payload?.properties?.part || payload?.properties);
        } else if (payload?.type === "message.part.delta") {
          appendPartDelta(payload?.properties);
        } else if (payload?.type === "message.part.removed") {
          removePart(payload?.properties);
        } else if (payload?.type === "session.status") {
          const status = text(payload?.properties?.status?.type || payload?.properties?.type);
          if (status && status !== "idle") setBusy(true);
          if (status === "idle") setBusy(false);
        } else if (payload?.type === "prompt_agent.result.normalized") {
          const messageId = text(payload?.properties?.message_id);
          const result = normalizedResult(payload?.properties?.result);
          if (messageId && result) {
            setNormalizedResults((previous) => ({ ...previous, [messageId]: result }));
          }
          setBusy(false);
        } else if (payload?.type === "session.stream.error" || payload?.type === "koubo_agent.chat.tool_blocked") {
          setBusy(false);
          setError(text(payload?.properties?.message, "提示词智能体连接中断"));
        }
      } catch {
        // Keep the stream alive on malformed non-chat events.
      }
    };
    eventSource.onerror = () => {
      eventSourceErrored = true;
      setBusy(false);
    };
  };

  const loadChat = async () => {
    const id = taskId();
    if (!id) return;
    try {
      setError("");
      const ensured = await props.api.agentChatEnsureSession(id, AGENT_KEY);
      if (ensured?.prompt_models) setPromptModels(ensured.prompt_models);
      const history = await props.api.agentChatMessages(id, AGENT_KEY);
      if (history?.prompt_models) setPromptModels(history.prompt_models);
      setMessages(normalizeMessages(history?.items || []));
      connectEvents();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err || ""));
    }
  };

  createEffect(() => {
    if (!taskId()) return;
    void loadChat();
  });

  onCleanup(() => {
    if (eventSource) eventSource.close();
    eventSourceTaskId = 0;
    eventSourceErrored = false;
  });

  const copyText = async (value, key) => {
    try {
      await navigator.clipboard.writeText(value);
      setCopiedKey(key);
      window.setTimeout(() => setCopiedKey((current) => current === key ? "" : current), 1200);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err || ""));
    }
  };

  const saveVersion = async (result, key) => {
    const id = taskId();
    if (!id || !result) return;
    setSavingId(key);
    try {
      const payload = {
        mode: result.mode,
        model_family: modelFamily(),
        provider: provider(),
        model: model(),
        original_prompt: lastPrompt() || prompt(),
        summary: result.summary,
        issues: result.issues,
        revised_prompt: result.revised_prompt,
        negative_prompt: result.negative_prompt,
        changes: result.changes,
        model_notes: result.model_notes,
        used_sources: result.used_sources,
        retrieval_id: result.retrieval_id || currentRetrievalId(),
      };
      await props.api.createPromptAgentVersion(id, payload);
      props.onVersionSaved?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err || ""));
    } finally {
      setSavingId("");
    }
  };

  const applyToGeneration = async (result, target, key) => {
    const id = taskId();
    if (!id || !result?.revised_prompt) return;
    setApplyingKey(`${key}_${target}`);
    try {
      await props.api.createPromptAgentApplied(id, {
        target,
        mode: result.mode,
        model_family: modelFamily(),
        provider: provider(),
        model: model(),
        prompt: result.revised_prompt,
        negative_prompt: result.negative_prompt,
        original_prompt: lastPrompt() || prompt(),
        retrieval_id: result.retrieval_id || currentRetrievalId(),
      });
      props.onApplyToGeneration?.({ target, prompt: result.revised_prompt, negative: result.negative_prompt });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err || ""));
    } finally {
      setApplyingKey("");
    }
  };

  const send = async () => {
    const id = taskId();
    const message = prompt().trim();
    if (!id || !message || busy()) return;
    setBusy(true);
    setError("");
    setRetrievalNotice("");
    setLastPrompt(message);
    const localId = `local_${Date.now()}`;
    setMessages((previous) => [...previous, { id: localId, role: "user", text: message, created_at: Date.now() }]);
    try {
      let retrievalId = "";
      try {
        const retrieval = await props.api.promptAgentKnowledgeSearch(id, {
          query: message,
          mode: mode(),
          model_family: modelFamily(),
          provider: provider(),
          model: model(),
          limit: 8,
        });
        retrievalId = text(retrieval?.retrieval_id);
        const items = Array.isArray(retrieval?.items) ? retrieval.items : [];
        if (!items.length) retrievalId = "";
        setCurrentRetrievalId(retrievalId);
        setRetrievalItems(items);
        if (!retrievalId || !items.length) setRetrievalNotice("本次无知识库命中");
      } catch {
        setCurrentRetrievalId("");
        setRetrievalItems([]);
        setRetrievalNotice("本次无知识库命中");
      }
      const chosen = selectedModel();
      await props.api.agentChatSendMessage(id, AGENT_KEY, {
        message,
        provider: chosen.providerID,
        model: chosen.modelID,
        client_context: {
          knowledge: retrievalId ? { retrieval_id: retrievalId } : {},
          prompt_agent: {
            mode: mode(),
            model_family: modelFamily(),
            provider: provider(),
            model: model(),
            prompt: message,
          },
        },
      });
      setPrompt("");
      connectEvents();
    } catch (err) {
      setBusy(false);
      setError(err instanceof Error ? err.message : String(err || ""));
    }
  };

  const abort = async () => {
    const id = taskId();
    if (!id) return;
    try {
      await props.api.agentChatAbort(id, AGENT_KEY);
    } finally {
      setBusy(false);
    }
  };

  const renderResult = (message, result) => {
    const key = `${message.id}_result`;
    return <section class="prompt-agent-result-card">
      <header>
        <strong>{RESULT_LABELS[result.mode] || "优化结果"}</strong>
        <span>{modelFamily()}{provider() ? ` / ${provider()}` : ""}</span>
      </header>
      <Show when={result.issues.length}>
        <div class="prompt-agent-result-section">
          <h3>问题批注</h3>
          <ul>
            <For each={result.issues}>{(issue) => (
              <li><b>{formatSeverity(issue?.severity)}</b><span>{text(issue?.problem || issue?.suggestion)}</span></li>
            )}</For>
          </ul>
        </div>
      </Show>
      <Show when={result.revised_prompt}>
        <div class="prompt-agent-result-section">
          <h3>优化后 Prompt</h3>
          <pre>{result.revised_prompt}</pre>
          <button type="button" onClick={() => void copyText(result.revised_prompt, `${key}_prompt`)}>
            <FlowIcon name={copiedKey() === `${key}_prompt` ? "check" : "contentCopy"} />{copiedKey() === `${key}_prompt` ? "Copied" : "Copy"}
          </button>
        </div>
      </Show>
      <Show when={result.negative_prompt}>
        <div class="prompt-agent-result-section">
          <h3>Negative Prompt</h3>
          <pre>{result.negative_prompt}</pre>
          <button type="button" onClick={() => void copyText(result.negative_prompt, `${key}_negative`)}>
            <FlowIcon name={copiedKey() === `${key}_negative` ? "check" : "contentCopy"} />{copiedKey() === `${key}_negative` ? "Copied" : "Copy"}
          </button>
        </div>
      </Show>
      <Show when={result.model_notes.length}>
        <div class="prompt-agent-result-section">
          <h3>模型注意事项</h3>
          <ul><For each={result.model_notes}>{(note) => <li><span>{text(note)}</span></li>}</For></ul>
        </div>
      </Show>
      <Show when={result.used_sources.length}>
        <div class="prompt-agent-result-section">
          <h3>来源依据</h3>
          <ul>
            <For each={result.used_sources}>{(source) => (
              <li>
                <b>{text(source?.trust_level, "source")}</b>
                <span>{text(source?.title || source?.doc_id)}{text(source?.reason) ? ` — ${text(source.reason)}` : ""}</span>
              </li>
            )}</For>
          </ul>
        </div>
      </Show>
      <footer>
        <button type="button" disabled={savingId() === key} onClick={() => void saveVersion(result, key)}>
          <FlowIcon name="download" />{savingId() === key ? "Saving" : "保存版本"}
        </button>
        <Show when={result.revised_prompt}>
          <button type="button" disabled={applyingKey() === `${key}_images`} onClick={() => void applyToGeneration(result, "images", key)}>
            <FlowIcon name="image" />{applyingKey() === `${key}_images` ? "..." : "应用到图像生成"}
          </button>
          <button type="button" disabled={applyingKey() === `${key}_videos`} onClick={() => void applyToGeneration(result, "videos", key)}>
            <FlowIcon name="video" />{applyingKey() === `${key}_videos` ? "..." : "应用到视频生成"}
          </button>
        </Show>
      </footer>
    </section>;
  };

  const renderMessage = (message) => {
    const role = message.role === "user" ? "user" : "assistant";
    const trustedResult = role === "assistant" ? normalizedResults()[message.id] : null;
    const parsedResult = role === "assistant" && !trustedResult ? normalizedResult(extractPromptAgentResult(message.text)) : null;
    const result = trustedResult || (parsedResult ? { ...parsedResult, used_sources: [] } : null);
    const body = visibleText(message.text);
    return <article class={`ual-message prompt-agent-message is-${role}`}>
      <Show when={role === "user"}>
        <div class="ual-user-bubble">
          <Show when={body}><p class="ual-user-message-text">{body}</p></Show>
        </div>
      </Show>
      <Show when={role !== "user"}>
        <Show when={body && !result}>
          <div class="ual-assistant-bubble"><p>{body}</p></div>
        </Show>
        <Show when={result}>{renderResult(message, result)}</Show>
      </Show>
    </article>;
  };

  const renderSettingsPanel = () => (
    <Show when={settingsOpen()}>
      <section class="ual-agent-settings-panel prompt-agent-settings-panel" aria-label="Prompt agent settings">
        <header>
          <button type="button" class="ual-agent-settings-icon" aria-label="Back" onClick={() => setSettingsOpen(false)}><FlowIcon name="arrowBack" /></button>
          <strong>Prompt Settings</strong>
          <button type="button" class="ual-agent-settings-icon" aria-label="Close settings" onClick={() => setSettingsOpen(false)}><FlowIcon name="close" /></button>
        </header>
        <div class="ual-agent-settings-body">
          <section class="ual-setting-group">
            <Show when={providerItems().length} fallback={<div class="ual-setting-select is-empty">No prompt providers configured</div>}>
              <div class="ual-setting-model-box">
                <div class="ual-setting-model-provider">
                  <span>Providers</span>
                  <small>{providerCountLabel()}</small>
                </div>
                <div class="ual-setting-model-options">
                  <button
                    type="button"
                    class={!provider() ? "is-active" : ""}
                    title="Automatically select provider"
                    onClick={() => selectProvider("")}
                  >
                    Auto
                  </button>
                  <For each={providerItems()}>{(item) => (
                    <button
                      type="button"
                      class={provider() === item.providerID ? "is-active" : ""}
                      title={item.providerID}
                      onClick={() => selectProvider(item.providerID)}
                    >
                      {item.label}
                    </button>
                  )}</For>
                </div>
              </div>
            </Show>
          </section>
          <section class="ual-setting-group">
            <Show when={filteredModelItems().length} fallback={<div class="ual-setting-select is-empty">No prompt models configured</div>}>
              <div class="ual-setting-model-box">
                <div class="ual-setting-model-provider">
                  <span>Prompt Models</span>
                  <small>{modelCountLabel()}</small>
                </div>
                <div class="ual-setting-model-options">
                  <button
                    type="button"
                    class={!model() ? "is-active" : ""}
                    title="Automatically select model"
                    onClick={() => selectModel(null)}
                  >
                    Auto
                  </button>
                  <For each={filteredModelItems()}>{(item) => (
                    <button
                      type="button"
                      class={selectedModelKey() === modelKey(item) ? "is-active" : ""}
                      title={`${text(item.providerName || item.providerID || item.provider)} / ${text(item.modelName || item.modelID || item.model)}`}
                      onClick={() => selectModel(item)}
                    >
                      {modelLabel(item)}
                    </button>
                  )}</For>
                </div>
              </div>
            </Show>
          </section>
        </div>
        <footer>
          <button type="button" onClick={() => setSettingsOpen(false)}>Done</button>
        </footer>
      </section>
    </Show>
  );

  return <aside class="ual-agent ual-video-agent is-opencode-workspace prompt-agent-panel">
    {renderSettingsPanel()}
    <section class="ual-opencode-agent is-workspace prompt-agent-dialog" role="dialog" aria-label="提示词智能体">
      <header class="ual-agent-header prompt-agent-header">
        <div class="ual-agent-title">
          <button type="button" class="ual-agent-icon" aria-label="Menu"><FlowIcon name="menu" /></button>
          <div class="prompt-agent-title-row">
            <strong>Workspace</strong>
            <span class="prompt-agent-status-badge">{busy() ? "Running" : "Ready"}</span>
          </div>
        </div>
        <button type="button" class="ual-agent-icon" title="Stop" aria-label="Stop" disabled={!busy()} onClick={() => void abort()}><FlowIcon name="close" /></button>
      </header>
      <div class="prompt-agent-controls">
        <div class="prompt-agent-mode-tabs">
          <For each={MODES}>{(item) => (
            <button type="button" class={mode() === item.key ? "is-active" : ""} disabled={item.disabled} title={item.disabled ? "P1 启用" : item.label} onClick={() => !item.disabled && setMode(item.key)}>
              {item.label}
            </button>
          )}</For>
        </div>
        <div class="prompt-agent-family-tabs" aria-label="Prompt family">
          <For each={MODEL_FAMILIES}>{(item) => (
            <button type="button" class={modelFamily() === item ? "is-active" : ""} onClick={() => setModelFamily(item)}>
              {MODEL_FAMILY_LABELS[item] || item}
            </button>
          )}</For>
        </div>
      </div>
      <Show when={error()}>
        <div class="prompt-agent-error">{error()}</div>
      </Show>
      <Show when={retrievalNotice()}>
        <div class="prompt-agent-notice">{retrievalNotice()}</div>
      </Show>
      <div ref={(el) => { scrollEl = el; }} class="ual-opencode-agent-chat prompt-agent-chat">
        <Show when={messages().length}>
          <For each={messages()}>{renderMessage}</For>
        </Show>
        <div ref={(el) => { bottomEl = el; }} class="ual-chat-bottom-sentinel prompt-agent-bottom" aria-hidden="true" />
      </div>
      <form class="ual-opencode-agent-composer prompt-agent-composer-shell" onSubmit={(event) => {
        event.preventDefault();
        void send();
      }}>
        <div class="ual-composer-box">
          <textarea
            value={prompt()}
            disabled={busy()}
            onInput={(event) => setPrompt(event.currentTarget.value)}
            onKeyDown={(event) => {
              if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
                event.preventDefault();
                void send();
              }
            }}
            placeholder={busy() ? "Running..." : "粘贴 / 输入提示词..."}
          />
          <div class="ual-composer-tools">
            <span aria-hidden="true" />
            <div>
              <button type="button" class="ual-composer-icon prompt-agent-model-settings-trigger" aria-label="Prompt Settings" title={`Prompt Settings · ${modelFamily()} · ${selectedModelLabel()}`} onClick={() => setSettingsOpen(true)}><FlowIcon name="tune" /></button>
              <Show when={busy()} fallback={
                <button type="submit" class="ual-composer-submit" disabled={!prompt().trim()} title="Send" aria-label="Send"><FlowIcon name="arrowForward" /></button>
              }>
                <button type="button" class="ual-composer-submit" title="Stop" aria-label="Stop" onClick={() => void abort()}><FlowIcon name="close" /></button>
              </Show>
            </div>
          </div>
        </div>
      </form>
    </section>
  </aside>;
}
