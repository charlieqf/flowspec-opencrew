import { For, Show, createEffect, createMemo, createSignal, onCleanup } from "solid-js";
import { MessageIcon, PlayIcon, XIcon } from "../kouboStoryboardIcons.jsx";
import {
  extractAgentCandidates,
  messageId,
  messageRole,
  reduceAgentChatEvent,
  seedAgentChatState,
  visibleMessageText,
} from "../kouboAgentChat.js";

function text(value) {
  return String(value || "").trim();
}

function modelKey(item = {}) {
  return `${text(item.providerID)}::${text(item.modelID)}`;
}

function parseModelKey(value) {
  const [providerID = "", modelID = ""] = String(value || "").split("::");
  return { providerID, modelID };
}

function modelLabel(item = {}) {
  const provider = text(item.providerName || item.providerID);
  const model = text(item.modelName || item.modelID);
  if (!provider) return model;
  if (!model || provider.toLowerCase() === model.toLowerCase()) return provider;
  return `${provider} / ${model}`;
}

function displayProgressLabel(value) {
  const label = text(value);
  if (!label || /^generating$/i.test(label)) return "0%";
  return label;
}

const RECOVERABLE_CLOUDFLARE_TIMEOUT_MESSAGE = "公网连接等待超时，Agent 可能仍在后台处理中；系统会继续刷新结果。";

function isRecoverableCloudflareTimeout(err) {
  const message = err instanceof Error ? err.message : String(err || "");
  return /Cloudflare|524|公网请求超过|等待时间|public request waited too long|timeout/i.test(message);
}

export default function KouboAgentDrawer(props) {
  let messagesScrollEl;
  let messagesBottomEl;
  let scrollFrame = 0;
  const [draft, setDraft] = createSignal("");
  const [chatSessionId, setChatSessionId] = createSignal("");
  const [promptModels, setPromptModels] = createSignal({ items: [], default_model: { providerID: "", modelID: "" } });
  const [selectedModelKey, setSelectedModelKey] = createSignal("");
  const [error, setError] = createSignal("");
  const [state, setState] = createSignal({ messages: [], partsByMessageId: {}, busy: false, error: "" });
  const [initializedFor, setInitializedFor] = createSignal("");
  let historyFallbackTimer = null;

  const taskId = () => Number(props.task?.()?.id || 0);
  const busy = () => Boolean(state().busy);
  const modelItems = createMemo(() => Array.isArray(promptModels().items) ? promptModels().items : []);
  const selectedModel = createMemo(() => parseModelKey(selectedModelKey()));
  const chips = createMemo(() => Array.isArray(props.contextChips?.()) ? props.contextChips() : []);
  const referenceAssets = createMemo(() => Array.isArray(props.referenceAssets?.()) ? props.referenceAssets() : []);
  const messages = createMemo(() => {
    const items = state().messages || [];
    if (items.length) {
      return items.filter((message) => (
        messageRole(message) === "user"
        || visibleMessageText(message, state().partsByMessageId)
        || message.videoPlaceholder
        || message.videoUrl
      ));
    }
    return [{ id: "local-greeting", role: "assistant", text: props.greeting || "你好，我可以帮你分析当前任务并给出可应用建议。" }];
  });
  const candidates = createMemo(() => {
    const items = [];
    for (const message of state().messages || []) {
      if (messageRole(message) !== "assistant") continue;
      items.push(...extractAgentCandidates(message, state().partsByMessageId));
    }
    return items;
  });

  function scrollMessagesToBottom() {
    if (!messagesScrollEl || typeof window === "undefined") return;
    if (scrollFrame) window.cancelAnimationFrame(scrollFrame);
    scrollFrame = window.requestAnimationFrame(() => {
      scrollFrame = window.requestAnimationFrame(() => {
        if (messagesBottomEl?.scrollIntoView) messagesBottomEl.scrollIntoView({ block: "end" });
        if (messagesScrollEl) messagesScrollEl.scrollTop = messagesScrollEl.scrollHeight;
      });
    });
  }

  function assetKey(asset) {
    return asset?.path || asset?.id || "";
  }

  function mediaUrl(asset) {
    return props.mediaUrl?.(asset) || props.assetUrl?.(asset) || "";
  }

  function referenceUrl(asset) {
    return props.referenceUrl?.(asset) || mediaUrl(asset);
  }

  function upsertLocalMessage(id, patch, fallback = {}) {
    if (!id) return;
    setState((current) => {
      const exists = (current.messages || []).some((message) => messageId(message) === id);
      const nextMessage = {
        id,
        role: "assistant",
        created_at: Date.now(),
        ...fallback,
        ...patch,
      };
      const messages = exists
        ? current.messages.map((message) => messageId(message) === id ? { ...message, ...patch } : message)
        : [...current.messages, nextMessage];
      return { ...current, messages };
    });
    scrollMessagesToBottom();
  }

  function handleAssetVideoEvent(payload) {
    const type = String(payload?.type || "");
    const properties = payload?.properties || {};
    if (type === "asset_agent.video_generation.started") {
      const id = `agent-video-${properties.agent_generation_id || Date.now()}`;
      upsertLocalMessage(id, {
        text: properties.title ? `Agent is generating video: ${properties.title}` : "Agent is generating a video.",
        videoPlaceholder: true,
        progressLabel: "0%",
      });
      setState((current) => ({ ...current, busy: true }));
      props.onAgentVideoGenerationEvent?.(payload);
      return true;
    }
    if (type === "asset_agent.video_generation.completed") {
      const id = `agent-video-${properties.agent_generation_id || properties.request_id || Date.now()}`;
      const asset = properties.asset || {};
      upsertLocalMessage(id, {
        text: `已经生成并保存到 Videos：${asset.filename || asset.path || "Agent generated video"}`,
        videoPlaceholder: false,
        progressLabel: "",
        videoUrl: (assetKey(asset) ? mediaUrl(asset) : "") || properties.video_url || properties.provider_result?.video_url || "",
      });
      setState((current) => ({ ...current, busy: false }));
      props.onAgentVideoGenerationEvent?.(payload);
      return true;
    }
    if (type === "asset_agent.video_generation.failed") {
      const id = `agent-video-${properties.agent_generation_id || properties.request_id || Date.now()}`;
      const detail = String(properties.detail?.message || properties.detail || properties.message || "Agent video generation failed.");
      upsertLocalMessage(id, { text: detail, failed: true, progressLabel: "Failed", videoPlaceholder: false });
      setState((current) => ({ ...current, busy: false }));
      props.onAgentVideoGenerationEvent?.(payload);
      return true;
    }
    return false;
  }

  function applyPromptModels(payload) {
    if (payload?.prompt_models) {
      setPromptModels(payload.prompt_models);
      const defaultModel = payload.prompt_models.default_model || {};
      const current = selectedModelKey();
      const available = payload.prompt_models.items || [];
      if (!current || !available.some((item) => modelKey(item) === current)) {
        const next = available.find((item) => modelKey(item) === modelKey(defaultModel)) || available[0] || defaultModel;
        setSelectedModelKey(modelKey(next));
      }
    }
    if (payload?.chat_opencode_session_id !== undefined) setChatSessionId(String(payload.chat_opencode_session_id || ""));
  }

  async function initialize() {
    const id = taskId();
    const key = text(props.agentKey);
    if (!props.open?.() || !id || !key) return;
    const marker = `${id}:${key}`;
    if (initializedFor() === marker) return;
    setError("");
    try {
      const ensured = await props.api.agentChatEnsureSession(id, key);
      applyPromptModels(ensured);
      const history = await props.api.agentChatMessages(id, key);
      applyPromptModels(history);
      setState((current) => ({ ...current, ...seedAgentChatState(history?.items || []), error: "" }));
      setInitializedFor(marker);
    } catch (err) {
      if (isRecoverableCloudflareTimeout(err)) {
        setError(RECOVERABLE_CLOUDFLARE_TIMEOUT_MESSAGE);
        setInitializedFor(marker);
        return;
      }
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  createEffect(() => {
    if (!props.open?.()) return;
    void initialize();
  });

  createEffect(() => {
    const id = taskId();
    const key = text(props.agentKey);
    const sessionId = chatSessionId();
    if (!props.open?.() || !id || !key || !sessionId) return;
    const source = new EventSource(props.api.agentChatEventsUrl(id, key), { withCredentials: true });
    source.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        if (!handleAssetVideoEvent(payload)) {
          setState((current) => reduceAgentChatEvent(current, payload));
        }
        scrollMessagesToBottom();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    };
    source.onerror = () => setError("Agent chat stream disconnected.");
    onCleanup(() => source.close());
  });

  createEffect(() => {
    if (!props.open?.()) return;
    const sessionId = chatSessionId();
    state().messages.length;
    const generatedAssets = Array.isArray(props.generatedAssets?.()) ? props.generatedAssets() : [];
    if (!generatedAssets.length) return;
    setState((current) => {
      const existingIds = new Set((current.messages || []).map((message) => messageId(message)));
      const additions = generatedAssets
        .filter((asset) => asset?.origin?.agent_generation_id)
        .filter((asset) => {
          const assetSessionId = text(asset?.origin?.chat_opencode_session_id);
          return !sessionId || !assetSessionId || assetSessionId === sessionId;
        })
        .filter((asset) => !existingIds.has(`agent-video-${text(asset?.origin?.agent_generation_id)}`))
        .map((asset) => ({
          id: `agent-video-${text(asset?.origin?.agent_generation_id)}`,
          role: "assistant",
          created_at: Number(asset.created_at || Date.now()) || Date.now(),
          text: `已经生成并保存到 Videos：${asset.filename || asset.path || "Agent generated video"}`,
          videoUrl: mediaUrl(asset),
        }));
      if (!additions.length) return current;
      return { ...current, messages: [...current.messages, ...additions] };
    });
    scrollMessagesToBottom();
  });

  createEffect(() => {
    if (!props.open?.()) return;
    messages().length;
    busy();
    error();
    scrollMessagesToBottom();
  });

  onCleanup(() => {
    if (historyFallbackTimer) window.clearTimeout(historyFallbackTimer);
    if (scrollFrame) window.cancelAnimationFrame(scrollFrame);
  });

  function completedAssistantAfter(items, sentAt) {
    return (items || []).some((item) => {
      if (messageRole(item) !== "assistant") return false;
      const completedAt = Number(item?.info?.time?.completed || item?.time?.completed || 0);
      return completedAt >= sentAt;
    });
  }

  function scheduleHistoryFallback(id, key, sentAt, attempt = 0) {
    if (historyFallbackTimer) window.clearTimeout(historyFallbackTimer);
    historyFallbackTimer = window.setTimeout(async () => {
      if (!busy()) return;
      try {
        const history = await props.api.agentChatMessages(id, key);
        applyPromptModels(history);
        const items = history?.items || [];
        const completed = completedAssistantAfter(items, sentAt);
        setState((current) => ({ ...current, ...(items.length ? seedAgentChatState(items) : {}), busy: completed ? false : current.busy, error: "" }));
        if (!completed && busy() && attempt < 40) {
          scheduleHistoryFallback(id, key, sentAt, attempt + 1);
          return;
        }
        if (!completed && busy()) {
          setState((current) => ({ ...current, busy: false }));
          setError("Agent 暂时还没有返回结果，可以稍后刷新弹窗或重新发送。");
        }
      } catch {
        if (busy() && attempt < 40) scheduleHistoryFallback(id, key, sentAt, attempt + 1);
        if (busy() && attempt >= 40) {
          setState((current) => ({ ...current, busy: false }));
          setError("Agent 历史消息暂时读取不到，可以稍后刷新弹窗或重新发送。");
        }
      }
    }, attempt === 0 ? 1800 : 2500);
  }

  async function sendMessage() {
    const id = taskId();
    const key = text(props.agentKey);
    const message = draft().trim();
    if (!id || !key || !message || busy()) return;
    const sentAt = Date.now();
    setDraft("");
    setError("");
    const localId = `local-${sentAt}`;
    setState((current) => ({
      ...current,
      busy: true,
      messages: [...current.messages, { id: localId, role: "user", text: message, created_at: Date.now() }],
    }));
    try {
      const model = selectedModel();
      const response = await props.api.agentChatSendMessage(id, key, {
        message,
        provider: model.providerID,
        model: model.modelID,
        client_context: props.buildClientContext?.() || {},
      });
      applyPromptModels(response);
      scheduleHistoryFallback(id, key, sentAt);
    } catch (err) {
      if (isRecoverableCloudflareTimeout(err)) {
        setError(RECOVERABLE_CLOUDFLARE_TIMEOUT_MESSAGE);
        scheduleHistoryFallback(id, key, sentAt);
        return;
      }
      setState((current) => ({ ...current, busy: false }));
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function abortChat() {
    const id = taskId();
    const key = text(props.agentKey);
    if (!id || !key) return;
    try {
      await props.api.agentChatAbort(id, key);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setState((current) => ({ ...current, busy: false }));
    }
  }

  return <Show when={props.open?.()}>
    <aside class={`kbsp-agent-drawer ${props.mode === "panel" ? "is-panel" : ""}`} role={props.mode === "panel" ? "complementary" : "dialog"} aria-label={props.title || "Koubo Agent"}>
      <header class="kbsp-agent-head">
        <div class="kbsp-agent-title">
          <span><MessageIcon /></span>
          <div>
            <h3>{props.title || "Koubo Agent"}</h3>
            <p>{props.subtitle || ""}</p>
          </div>
        </div>
        <Show when={props.mode !== "panel"}>
          <button type="button" aria-label="关闭 Agent" onClick={() => props.setOpen?.(false)}><XIcon /></button>
        </Show>
      </header>

      <Show when={chips().length}>
        <div class="kbsp-agent-chips">
          <For each={chips()}>{(chip) => <span title={text(chip.title || chip.value)}><b>{chip.label}</b>{chip.value}</span>}</For>
        </div>
      </Show>

      <Show when={error() || state().error}>
        <div class="kbsp-agent-error">{error() || state().error}</div>
      </Show>

      <div class="kbsp-agent-messages" ref={(el) => { messagesScrollEl = el; }}>
        <For each={messages()}>{(message) => <article class={`kbsp-agent-message is-${messageRole(message)}`}>
          <strong>{messageRole(message) === "user" ? "你" : "Agent"}</strong>
          <p>{visibleMessageText(message, state().partsByMessageId) || (busy() && messageRole(message) === "assistant" ? "..." : "")}</p>
          <Show when={message.videoPlaceholder}>
            <div class="kbsp-agent-video-placeholder"><span class="kbsp-agent-spinner" aria-hidden="true" />{displayProgressLabel(message.progressLabel)}</div>
          </Show>
          <Show when={message.videoUrl}>
            <video class="kbsp-agent-video" src={message.videoUrl} controls preload="none" />
          </Show>
        </article>}</For>
        <div ref={(el) => { messagesBottomEl = el; }} class="kbsp-agent-bottom-sentinel" aria-hidden="true" />
      </div>

      <Show when={candidates().length}>
        <section class="kbsp-agent-candidates" aria-label="Agent candidates">
          <For each={candidates()}>{(candidate) => props.renderCandidate
            ? props.renderCandidate(candidate, { setError })
            : <article class="kbsp-agent-candidate"><strong>{candidate.title}</strong><pre>{JSON.stringify(candidate.payload, null, 2)}</pre></article>}</For>
        </section>
      </Show>

      <footer class="kbsp-agent-compose">
        <Show when={referenceAssets().length}>
          <div class="kbsp-agent-reference-strip" aria-label="Selected reference images">
            <For each={referenceAssets()}>{(asset) => <div class="kbsp-agent-reference-thumb" title={asset?.label || asset?.filename || asset?.path || "Reference"}>
              <img src={referenceUrl(asset)} alt="" loading="lazy" />
              <button type="button" aria-label="Remove reference" onClick={() => props.onRemoveReference?.(asset)}><XIcon /></button>
            </div>}</For>
          </div>
        </Show>
        <Show when={modelItems().length}>
          <div class="kbsp-agent-model-toggle" role="radiogroup" aria-label="Agent model">
            <For each={modelItems()}>{(item) => {
              const key = modelKey(item);
              return <button
                type="button"
                role="radio"
                aria-checked={selectedModelKey() === key}
                class={selectedModelKey() === key ? "is-active" : ""}
                title={`${text(item.providerName || item.providerID)} / ${text(item.modelName || item.modelID)}`}
                onClick={() => setSelectedModelKey(key)}
              >{modelLabel(item)}</button>;
            }}</For>
          </div>
        </Show>
        <textarea
          value={draft()}
          placeholder={props.placeholder || "输入你想让 Agent 帮忙分析或改写的内容"}
          onInput={(event) => setDraft(event.currentTarget.value)}
          onKeyDown={(event) => {
            if ((event.metaKey || event.ctrlKey) && event.key === "Enter") void sendMessage();
          }}
        />
        <div class="kbsp-agent-compose-actions">
          <button type="button" class="secondary" disabled={!busy()} onClick={() => void abortChat()}>停止</button>
          <button type="button" disabled={busy() || !draft().trim()} onClick={() => void sendMessage()}>
            <Show when={busy()} fallback={<PlayIcon />}>
              <span class="kbsp-agent-spinner" aria-hidden="true" />
            </Show>
            <span>{busy() ? "处理中" : "发送"}</span>
          </button>
        </div>
      </footer>
    </aside>
  </Show>;
}
