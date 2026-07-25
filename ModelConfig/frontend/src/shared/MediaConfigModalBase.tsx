import { For, Index, Show, createEffect, createMemo, createSignal, onCleanup, untrack } from "solid-js";
import { ConnectionTestControl, defaultConnectionTestState, testStateKey } from "./ConnectionTestControl";
import { CloseIcon, MinimumUnitPriceIcon, PriceListIcon } from "./icons";
import {
  FALLBACK_USD_CNY_RATE,
  MEDIA_PRICE_POINTS,
  VIDEO_MIN_DURATION_SECONDS,
  formatCurrencyAmount,
  loadUsdCnyRate,
  unitLabel,
} from "./pricing";
import type { ConnectionTestState, MediaModelConfigResponse, UsdCnyRateState } from "./types";

function normalizeModelName(model: string) {
  return model;
}

function normalizeMediaConfigResponse(res: MediaModelConfigResponse): MediaModelConfigResponse {
  return {
    ...res,
    agent_model_aliases: (res.agent_model_aliases ?? []).map((item) => ({
      alias: String(item.alias || "").trim(),
      provider: String(item.provider || "").trim(),
      model: normalizeModelName(String(item.model || "").trim()),
      created_at: item.created_at ?? null,
      updated_at: item.updated_at ?? null,
    })).filter((item) => item.alias && item.provider && item.model),
    providers: res.providers.map((provider) => ({
      ...provider,
      model: normalizeModelName(provider.model),
      models: provider.models.map((model) => {
        const normalized = normalizeModelName(model.model);
        return {
          ...model,
          model: normalized,
          label: model.label === model.model ? normalized : normalizeModelName(model.label),
        };
      }),
    })),
  };
}

export function MediaConfigModalBase(props: {
  open: boolean;
  title: string;
  kind: "image" | "video" | "lipsync" | "digital-human";
  onClose: () => void;
  loadConfig: () => Promise<MediaModelConfigResponse>;
  saveConfig: (input: { active_provider: string; providers: Array<{ provider: string; model: string; api_key: string; enabled: boolean }>; agent_model_aliases?: Array<{ alias: string; provider: string; model: string; created_at?: number | null; updated_at?: number | null }> }) => Promise<MediaModelConfigResponse & { ok: boolean }>;
  testConnection: (input: { provider: string; model: string }) => Promise<{ ok: boolean; message: string; detail: string }>;
}) {
  const [loading, setLoading] = createSignal(false);
  const [saving, setSaving] = createSignal(false);
  const [error, setError] = createSignal("");
  const [config, setConfig] = createSignal<MediaModelConfigResponse | null>(null);
  const [apiKeys, setApiKeys] = createSignal<Record<string, string>>({});
  const [priceListOpen, setPriceListOpen] = createSignal(false);
  const [minimumUnitOpen, setMinimumUnitOpen] = createSignal(false);
  const [agentDrag, setAgentDrag] = createSignal<{ provider: string; model: string; label: string; x: number; y: number } | null>(null);
  const [agentDragOver, setAgentDragOver] = createSignal(false);
  const [rate, setRate] = createSignal<UsdCnyRateState>({ rate: FALLBACK_USD_CNY_RATE, date: "fallback", source: "fallback", loading: false, error: "" });
  const [tests, setTests] = createSignal<Record<string, ConnectionTestState>>({});
  const kindLabel = () => props.kind === "image" ? "Image" : props.kind === "video" ? "Video" : props.kind === "digital-human" ? "Digital Human" : "Lip Sync";
  const hasPriceTools = () => props.kind === "image" || props.kind === "video" || props.kind === "lipsync";
  const supportsAgentPool = () => props.kind === "image" || props.kind === "video";
  type Provider = NonNullable<MediaModelConfigResponse["providers"]>[number];
  type CredentialField = NonNullable<Provider["credential_fields"]>[number];
  const credentialFields = (provider: Provider): CredentialField[] => provider.credential_fields?.length
    ? provider.credential_fields
    : [{ key: "api_key", label: "API Key", type: "password", required_group: "" }];
  const credentialKey = (provider: Provider, field: CredentialField) => credentialFields(provider).length === 1 && field.key === "api_key"
    ? provider.provider
    : `${provider.provider}:${field.key}`;
  const credentialValues = (provider: Provider) => credentialFields(provider).map((field) => ({
    field,
    value: String(apiKeys()[credentialKey(provider, field)] ?? "").trim(),
  }));
  const credentialPayload = (provider: Provider) => {
    const values = credentialValues(provider);
    if (values.length === 1 && values[0].field.key === "api_key") return values[0].value;
    if (!values.some((item) => item.value)) return "";
    return JSON.stringify(Object.fromEntries(values.map((item) => [item.field.key, item.value])));
  };
  const incompleteCredentialGroup = (provider: Provider) => {
    const groups = new Map<string, string[]>();
    for (const { field, value } of credentialValues(provider)) {
      if (!field.required_group) continue;
      groups.set(field.required_group, [...(groups.get(field.required_group) ?? []), value]);
    }
    return [...groups.values()].some((values) => values.some(Boolean) && values.some((value) => !value));
  };
  let agentPoolRef: HTMLElement | undefined;
  let agentDragCandidate: { provider: string; model: string; label: string; startX: number; startY: number; moved: boolean } | null = null;

  const testState = (provider: string) => tests()[testStateKey(props.kind, provider)] ?? defaultConnectionTestState();
  const updateTest = (provider: string, patch: Partial<ConnectionTestState>) => {
    const key = testStateKey(props.kind, provider);
    setTests((prev) => ({ ...prev, [key]: { ...(prev[key] ?? defaultConnectionTestState()), ...patch } }));
  };
  const resetTest = (provider: string) => {
    const key = testStateKey(props.kind, provider);
    setTests((prev) => {
      const next = { ...prev };
      delete next[key];
      return next;
    });
  };

  createEffect(() => {
    if (!props.open) return;
    setLoading(true);
    setError("");
    setApiKeys({});
    setTests({});
    setPriceListOpen(false);
    setMinimumUnitOpen(false);
    void loadUsdCnyRate(untrack(rate)).then(setRate);
    props.loadConfig()
      .then((res) => setConfig(normalizeMediaConfigResponse(res)))
      .catch((err) => setError(err instanceof Error ? err.message : `Failed loading ${props.kind} model config`))
      .finally(() => setLoading(false));
  });

  const availableModels = createMemo(() => new Set((config()?.providers ?? []).flatMap((provider) => provider.models.map((model) => model.model))));
  const priceRanking = createMemo(() => MEDIA_PRICE_POINTS
    .filter((item) => item.kind === props.kind && availableModels().has(item.model))
    .filter((item) => props.kind !== "lipsync" || item.unit === "second")
    .map((item) => ({
      ...item,
      cnyAmount: item.currency === "USD" ? item.amount * rate().rate : item.amount,
      originalText: `${formatCurrencyAmount(item.amount, item.currency)}/${item.unit}`,
    }))
    .sort((a, b) => a.cnyAmount - b.cnyAmount || a.model.localeCompare(b.model) || a.variant.localeCompare(b.variant)));

  const minimumUnitPrices = createMemo(() => MEDIA_PRICE_POINTS
    .filter((item) => item.kind === props.kind && availableModels().has(item.model))
    .filter((item) => props.kind !== "lipsync" || item.unit === "second")
    .map((item) => {
      const minSeconds = props.kind === "video" ? VIDEO_MIN_DURATION_SECONDS[item.model] ?? 1 : 1;
      const originalUnitPrice = item.amount * minSeconds;
      const cnyUnitPrice = item.currency === "USD" ? originalUnitPrice * rate().rate : originalUnitPrice;
      return {
        ...item,
        minSeconds,
        originalUnitPrice,
        cnyUnitPrice,
        originalText: `${formatCurrencyAmount(item.amount, item.currency)}/${item.unit}`,
        unitText: props.kind === "video" ? `${formatCurrencyAmount(originalUnitPrice, item.currency)}/${minSeconds}s video` : `${formatCurrencyAmount(originalUnitPrice, item.currency)}/${unitLabel(item.unit)}`,
      };
    })
    .sort((a, b) => a.cnyUnitPrice - b.cnyUnitPrice || a.model.localeCompare(b.model) || a.variant.localeCompare(b.variant)));
  const providerLabel = (providerId: string) => config()?.providers.find((item) => item.provider === providerId)?.provider_label || providerId;
  const modelLabel = (providerId: string, modelId: string) => {
    const provider = config()?.providers.find((item) => item.provider === providerId);
    return provider?.models.find((item) => item.model === modelId)?.label || modelId;
  };
  const agentAliases = createMemo(() => config()?.agent_model_aliases ?? []);
  const updateAgentAliases = (aliases: NonNullable<MediaModelConfigResponse["agent_model_aliases"]>) => {
    setConfig((prev) => prev ? { ...prev, agent_model_aliases: aliases } : prev);
  };
  const defaultAliasFor = () => {
    const normalized = `${kindLabel()} Model`;
    const used = new Set(agentAliases().map((item) => item.alias.toLowerCase()));
    if (!used.has(normalized.toLowerCase())) return normalized;
    let index = 2;
    while (used.has(`${normalized} ${index}`.toLowerCase())) index += 1;
    return `${normalized} ${index}`;
  };
  const addAgentAlias = (provider: string, model: string) => {
    const existing = agentAliases().find((item) => item.provider === provider && item.model === model);
    if (existing) return;
    const now = Date.now();
    updateAgentAliases([
      ...agentAliases(),
      { alias: defaultAliasFor(), provider, model: normalizeModelName(model), created_at: now, updated_at: now },
    ]);
  };
  const removeAgentDragListeners = () => {
    window.removeEventListener("pointermove", handleAgentPointerMove);
    window.removeEventListener("pointerup", handleAgentPointerUp);
    window.removeEventListener("pointercancel", handleAgentPointerUp);
  };
  const finishAgentDrag = (x: number, y: number) => {
    const active = agentDrag();
    const rect = agentPoolRef?.getBoundingClientRect();
    if (active && rect && x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom) {
      addAgentAlias(active.provider, active.model);
    }
    agentDragCandidate = null;
    setAgentDrag(null);
    setAgentDragOver(false);
    removeAgentDragListeners();
  };
  function handleAgentPointerMove(event: PointerEvent) {
    const candidate = agentDragCandidate;
    if (!candidate) return;
    const dx = Math.abs(event.clientX - candidate.startX);
    const dy = Math.abs(event.clientY - candidate.startY);
    if (!candidate.moved && dx + dy < 6) return;
    candidate.moved = true;
    event.preventDefault();
    const rect = agentPoolRef?.getBoundingClientRect();
    setAgentDragOver(Boolean(rect && event.clientX >= rect.left && event.clientX <= rect.right && event.clientY >= rect.top && event.clientY <= rect.bottom));
    setAgentDrag({ provider: candidate.provider, model: candidate.model, label: candidate.label, x: event.clientX, y: event.clientY });
  }
  function handleAgentPointerUp(event: PointerEvent) {
    const candidate = agentDragCandidate;
    if (!candidate) return;
    if (candidate.moved) {
      event.preventDefault();
      finishAgentDrag(event.clientX, event.clientY);
      return;
    }
    agentDragCandidate = null;
    setAgentDrag(null);
    setAgentDragOver(false);
    removeAgentDragListeners();
  }
  const startAgentDrag = (event: PointerEvent, provider: string, model: string, label: string) => {
    if (!supportsAgentPool() || event.button !== 0) return;
    agentDragCandidate = { provider, model, label, startX: event.clientX, startY: event.clientY, moved: false };
    window.addEventListener("pointermove", handleAgentPointerMove, { passive: false });
    window.addEventListener("pointerup", handleAgentPointerUp, { passive: false });
    window.addEventListener("pointercancel", handleAgentPointerUp, { passive: false });
  };
  onCleanup(removeAgentDragListeners);
  const handleAgentDrop = (event: DragEvent) => {
    if (!supportsAgentPool()) return;
    event.preventDefault();
    const raw = event.dataTransfer?.getData("application/json") || "";
    if (!raw) return;
    try {
      const payload = JSON.parse(raw);
      const provider = String(payload.provider || "").trim();
      const model = normalizeModelName(String(payload.model || "").trim());
      if (provider && model) addAgentAlias(provider, model);
    } catch {
      // Ignore non-model drags.
    }
  };
  const updateAgentAlias = (index: number, alias: string) => {
    updateAgentAliases(agentAliases().map((item, itemIndex) => itemIndex === index ? { ...item, alias, updated_at: Date.now() } : item));
  };
  const removeAgentAlias = (index: number) => {
    updateAgentAliases(agentAliases().filter((_, itemIndex) => itemIndex !== index));
  };
  const agentPoolEmptyText = () => props.kind === "video"
    ? "Drag video models into this area. The panel stays reachable while you scroll."
    : "Drag OpenAI, Gemini, or xAI image models into this area.";

  const setActiveProvider = (provider: string) => {
    setConfig((prev) => prev ? {
      ...prev,
      active_provider: provider,
      providers: prev.providers.map((item) => ({ ...item, active: item.provider === provider })),
    } : prev);
  };

  const updateProviderModel = (provider: string, model: string) => {
    const current = config()?.providers.find((item) => item.provider === provider);
    if (current?.model !== model) resetTest(provider);
    setConfig((prev) => prev ? {
      ...prev,
      providers: prev.providers.map((item) => item.provider === provider ? { ...item, model } : item),
    } : prev);
  };

  const save = async () => {
    const current = config();
    if (!current) return;
    const incompleteProvider = current.providers.find(incompleteCredentialGroup);
    if (incompleteProvider) {
      setError(`${incompleteProvider.provider_label || "Provider"} credentials must be filled together, or all left blank to keep the saved credentials.`);
      return;
    }
    setSaving(true);
    setError("");
    try {
      const res = await props.saveConfig({
        active_provider: current.active_provider,
        providers: current.providers.map((item) => ({
          provider: item.provider,
          model: normalizeModelName(item.model),
          api_key: credentialPayload(item),
          enabled: item.enabled,
        })),
        agent_model_aliases: supportsAgentPool() ? agentAliases().map((item) => ({
          alias: item.alias.trim(),
          provider: item.provider,
          model: normalizeModelName(item.model),
          created_at: item.created_at ?? null,
          updated_at: item.updated_at ?? null,
        })) : undefined,
      });
      setConfig(normalizeMediaConfigResponse(res));
      setApiKeys({});
      setTests({});
      props.onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : `Failed saving ${props.kind} model config`);
    } finally {
      setSaving(false);
    }
  };

  const providerCard = (provider: Provider) => (
    <section class={`media-provider-card ${provider.active ? "active" : ""}`} onClick={() => setActiveProvider(provider.provider)}>
      <div class="media-provider-head">
        <h4>{provider.provider_label}</h4>
        <ConnectionTestControl
          state={() => testState(provider.provider)}
          model={provider.model}
          unsaved={credentialValues(provider).some((item) => Boolean(item.value))}
          onTest={() => runTest(provider.provider, provider.model)}
          onToggle={() => updateTest(provider.provider, { expanded: !testState(provider.provider).expanded })}
        />
        <span class={`media-provider-status ${provider.active ? "active" : ""}`}>{provider.active ? "ACTIVE" : "Set Active"}</span>
      </div>
      <div class="media-model-chip-list" onClick={(event) => event.stopPropagation()}>
        <For each={provider.models}>{(model) => (
          <span class="media-model-chip-wrap">
            <button class={`media-model-chip ${provider.model === model.model ? "selected" : ""} ${model.description ? "has-tooltip" : ""}`} type="button" title={model.description || model.label} data-tooltip={model.description || ""} onClick={() => {
              setActiveProvider(provider.provider);
              updateProviderModel(provider.provider, model.model);
            }} onPointerDown={(event) => startAgentDrag(event, provider.provider, model.model, model.label)}>
              {model.label}
            </button>
          </span>
        )}</For>
      </div>
      <div class={`media-key-row ${credentialFields(provider).length > 1 ? "media-key-row-split" : ""}`} onClick={(event) => event.stopPropagation()}>
        <For each={credentialFields(provider)}>{(field) => (
          <label class="openflow-field">
            <span>{field.label}</span>
            <input
              type={field.type || "password"}
              placeholder={provider.has_api_key ? `Leave blank to keep existing ${field.label}` : `Paste ${field.label}`}
              value={apiKeys()[credentialKey(provider, field)] ?? ""}
              onInput={(event) => setApiKeys((prev) => ({ ...prev, [credentialKey(provider, field)]: event.currentTarget.value }))}
            />
          </label>
        )}</For>
          <div class="media-key-status">
            <strong>{provider.has_api_key ? "Credentials saved" : "Credentials missing"}</strong>
          </div>
      </div>
      <div class="media-provider-foot">
        <a href={provider.docs_url} target="_blank" rel="noreferrer" onClick={(event) => event.stopPropagation()}>Docs</a>
      </div>
    </section>
  );

  const agentPool = () => (
    <section
      ref={(element) => { agentPoolRef = element; }}
      class={`media-agent-model-pool ${props.kind === "video" ? "is-video-agent-pool" : ""} ${agentDragOver() ? "is-drag-over" : ""}`}
      onDragOver={(event) => {
        event.preventDefault();
        if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
      }}
      onDrop={handleAgentDrop}
    >
      <div class="media-agent-model-pool-head">
        <div>
          <h4>Agent</h4>
        </div>
        <span>{agentAliases().length} models</span>
      </div>
      <Show when={agentAliases().length} fallback={<div class="media-agent-model-empty">{agentPoolEmptyText()}</div>}>
        <div class="media-agent-alias-list">
          <Index each={agentAliases()}>{(item, index) => (
            <div class="media-agent-alias-row">
              <input
                value={item().alias}
                placeholder="Alias"
                onInput={(event) => updateAgentAlias(index, event.currentTarget.value)}
              />
              <span>{providerLabel(item().provider)} · {modelLabel(item().provider, item().model)}</span>
              <button type="button" onClick={() => removeAgentAlias(index)}>Remove</button>
            </div>
          )}</Index>
        </div>
      </Show>
    </section>
  );

  const runTest = async (provider: string, model: string) => {
    updateTest(provider, { status: "testing", message: "Testing...", detail: "", expanded: false });
    try {
      const res = await props.testConnection({ provider, model });
      updateTest(provider, { status: res.ok ? "success" : "failed", message: res.message, detail: res.detail || "", expanded: !res.ok });
    } catch (err) {
      updateTest(provider, { status: "failed", message: "Connection failed", detail: err instanceof Error ? err.message : "Unknown error", expanded: true });
    }
  };

  return (
    <Show when={props.open}>
      <div class="drawer-backdrop" onClick={props.onClose} />
      <div class="env-dialog media-config-dialog">
        <div class="env-dialog-head">
          <h3>{props.title}</h3>
          <div class="media-dialog-head-actions">
            <Show when={hasPriceTools()}>
              <div class="media-price-menu-wrap">
                <button class="icon-action media-price-list-button" type="button" title="Price ranking by daily USD/CNY rate" aria-label="Price ranking by daily USD/CNY rate" onClick={() => { setMinimumUnitOpen(false); setPriceListOpen((value) => !value); }}><PriceListIcon /></button>
                <Show when={priceListOpen()}>
                  <div class="media-price-popover">
                    <div class="media-price-popover-head">
                      <strong>{kindLabel()} Price Ranking</strong>
                      <span>USD/CNY {rate().rate.toFixed(4)} · {rate().date}</span>
                    </div>
                    <Show when={rate().error}>
                      <p class="media-price-rate-warning">Exchange rate fetch failed; using fallback rate.</p>
                    </Show>
                    <div class="media-price-list">
                      <For each={priceRanking()}>{(item, index) => (
                        <div class="media-price-row">
                          <span class="media-price-rank">{index() + 1}</span>
                          <div class="media-price-main">
                            <strong>{item.providerLabel} · {item.model}</strong>
                            <span>{item.variant} · {item.originalText}</span>
                          </div>
                          <div class="media-price-cny">¥{item.cnyAmount.toFixed(item.cnyAmount >= 1 ? 2 : 4)}/{unitLabel(item.unit)}</div>
                        </div>
                      )}</For>
                    </div>
                  </div>
                </Show>
              </div>
            </Show>
            <Show when={props.kind === "video" || props.kind === "lipsync"}>
              <div class="media-price-menu-wrap">
                <button class="icon-action media-price-list-button" type="button" title={`${kindLabel()} minimum unit price`} aria-label={`${kindLabel()} minimum unit price`} onClick={() => { setPriceListOpen(false); setMinimumUnitOpen((value) => !value); }}><MinimumUnitPriceIcon /></button>
                <Show when={minimumUnitOpen()}>
                  <div class="media-price-popover media-min-unit-popover">
                    <div class="media-price-popover-head">
                      <strong>{kindLabel()} Minimum Unit Price</strong>
                      <span>USD/CNY {rate().rate.toFixed(4)} · {rate().date}</span>
                    </div>
                    <Show when={rate().error}>
                      <p class="media-price-rate-warning">Exchange rate fetch failed; using fallback rate.</p>
                    </Show>
                    <div class="media-price-list">
                      <For each={minimumUnitPrices()}>{(item, index) => (
                        <div class="media-price-row media-min-unit-row">
                          <span class="media-price-rank">{index() + 1}</span>
                          <div class="media-price-main">
                            <strong>{item.providerLabel} · {item.model}</strong>
                            <span>{item.variant}{props.kind === "video" ? ` · shortest ${item.minSeconds}s` : ""} · {item.originalText}</span>
                          </div>
                          <div class="media-price-cny media-min-unit-price">
                            <strong>¥{item.cnyUnitPrice.toFixed(item.cnyUnitPrice >= 1 ? 2 : 4)}</strong>
                            <span>{item.unitText}</span>
                          </div>
                        </div>
                      )}</For>
                    </div>
                  </div>
                </Show>
              </div>
            </Show>
            <button class="icon-action" type="button" title="Close" onClick={props.onClose}><CloseIcon /></button>
          </div>
        </div>
        <Show when={error()}>
          <div class="banner bad">{error()}</div>
        </Show>
        <Show when={!loading() && config()} fallback={<div class="message-panel"><p class="helper">Loading {kindLabel()} model config...</p></div>}>
          <div class={`media-provider-grid ${props.kind === "video" ? "is-video-agent-layout" : ""}`}>
            <Show when={props.kind === "video"} fallback={
              <>
                <For each={config()?.providers ?? []}>{(provider) => providerCard(provider)}</For>
                <Show when={props.kind === "image"}>{agentPool()}</Show>
              </>
            }>
              <For each={(config()?.providers ?? []).slice(0, 2)}>{(provider) => providerCard(provider)}</For>
              {agentPool()}
              <div class="media-provider-rest-grid">
                <For each={(config()?.providers ?? []).slice(2)}>{(provider) => providerCard(provider)}</For>
              </div>
            </Show>
          </div>
          <Show when={agentDrag()}>
            {(drag) => <div class="media-agent-drag-ghost" style={{ left: `${drag().x}px`, top: `${drag().y}px` }}>{drag().label}</div>}
          </Show>
          <div class="asr-config-actions media-config-actions">
            <button class="secondary" type="button" onClick={props.onClose}>Cancel</button>
            <button type="button" disabled={saving() || !config()} onClick={() => void save()}>{saving() ? "Saving..." : `Save ${kindLabel()} Config`}</button>
          </div>
        </Show>
      </div>
    </Show>
  );
}
