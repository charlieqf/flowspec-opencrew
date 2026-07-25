import { For, Show, batch, createEffect, createMemo, createSignal } from "solid-js";
import { modelConfigApi } from "../shared/api";
import { CloseIcon } from "../shared/icons";
import { ConnectionTestControl, defaultConnectionTestState, testStateKey } from "../shared/ConnectionTestControl";
import type { ASRConfigPayload, ASRModelOption, ConnectionTestState } from "../shared/types";

type ASRProviderCard = {
  provider: string;
  providerLabel: string;
  models: ASRModelOption[];
};

export function ASRConfigModal(props: { open: boolean; onClose: () => void }) {
  const [loading, setLoading] = createSignal(false);
  const [saving, setSaving] = createSignal(false);
  const [error, setError] = createSignal("");
  const [apiKey, setApiKey] = createSignal("");
  const [models, setModels] = createSignal<ASRModelOption[]>([]);
  const [config, setConfig] = createSignal<ASRConfigPayload | null>(null);
  const [tests, setTests] = createSignal<Record<string, ConnectionTestState>>({});

  const providerCards = createMemo<ASRProviderCard[]>(() => {
    const grouped = new Map<string, ASRProviderCard>();
    for (const item of models()) {
      const providerLabel = item.provider === "aliyun_bailian_fun_asr" ? "Aliyun Bailian Fun-ASR" : item.provider === "local_whisper" ? "Local Whisper" : item.provider;
      const card = grouped.get(item.provider) ?? { provider: item.provider, providerLabel, models: [] };
      card.models.push(item);
      grouped.set(item.provider, card);
    }
    return Array.from(grouped.values());
  });

  const selectedModel = createMemo(() => models().find((item) => item.provider === config()?.provider && item.model === config()?.model));
  const testState = (scope: string, provider: string) => tests()[testStateKey(scope, provider)] ?? defaultConnectionTestState();
  const updateTest = (scope: string, provider: string, patch: Partial<ConnectionTestState>) => {
    const key = testStateKey(scope, provider);
    setTests((prev) => ({ ...prev, [key]: { ...(prev[key] ?? defaultConnectionTestState()), ...patch } }));
  };
  const resetTest = (scope: string, provider: string) => {
    const key = testStateKey(scope, provider);
    setTests((prev) => {
      const next = { ...prev };
      delete next[key];
      return next;
    });
  };

  const load = async () => {
    setLoading(true);
    setError("");
    setApiKey("");
    setTests({});
    try {
      const res = await modelConfigApi.asrConfig();
      setModels(res.models);
      setConfig(res.config);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed loading ASR config");
    } finally {
      setLoading(false);
    }
  };

  createEffect(() => {
    if (props.open) void load();
  });

  const updateModel = (provider: string, model: string) => {
    if (config()?.provider !== provider || config()?.model !== model) resetTest("asr", provider);
    const option = models().find((item) => item.provider === provider && item.model === model);
    setConfig((prev) => ({
      ...(prev ?? { config_name: "default_asr_provider", provider, model, language: "zh", api_url: option?.api_url ?? "", enabled: true, has_api_key: false, api_key_ref: "", updated_at: null }),
      provider,
      model,
      api_url: option?.api_url ?? prev?.api_url ?? "",
    }));
  };

  const modelForProviderInput = (provider: string, fallbackModel: string) => (
    config()?.provider === provider ? config()?.model ?? fallbackModel : fallbackModel
  );

  const activateProviderForInput = (provider: string, fallbackModel: string) => {
    updateModel(provider, modelForProviderInput(provider, fallbackModel) || "");
  };

  const updateProviderApiKey = (provider: string, fallbackModel: string, nextApiKey: string) => {
    batch(() => {
      updateModel(provider, modelForProviderInput(provider, fallbackModel) || "");
      setApiKey(nextApiKey);
    });
  };

  const save = async () => {
    const current = config();
    if (!current) return;
    setSaving(true);
    setError("");
    try {
      const res = await modelConfigApi.asrConfigSave({
        config_name: "default_asr_provider",
        provider: current.provider,
        model: current.model,
        language: current.language || "zh",
        api_url: current.api_url || selectedModel()?.api_url || "",
        api_key: apiKey(),
        enabled: current.enabled,
      });
      setModels(res.models);
      setConfig(res.config);
      setApiKey("");
      setTests({});
      props.onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed saving ASR config");
    } finally {
      setSaving(false);
    }
  };

  const runTest = async (provider: string, model: string) => {
    updateTest("asr", provider, { status: "testing", message: "Testing...", detail: "", expanded: false });
    try {
      const res = await modelConfigApi.asrConnectionTest({ provider, model });
      updateTest("asr", provider, { status: res.ok ? "success" : "failed", message: res.message, detail: res.detail || "", expanded: !res.ok });
    } catch (err) {
      updateTest("asr", provider, { status: "failed", message: "Connection failed", detail: err instanceof Error ? err.message : "Unknown error", expanded: true });
    }
  };

  return (
    <Show when={props.open}>
      <div class="drawer-backdrop" onClick={props.onClose} />
      <div class="env-dialog asr-config-dialog">
        <div class="env-dialog-head">
          <h3>ASR Model Settings</h3>
          <button class="icon-action" type="button" title="Close" onClick={props.onClose}><CloseIcon /></button>
        </div>
        <Show when={error()}>
          <div class="banner bad">{error()}</div>
        </Show>
        <Show when={!loading()} fallback={<div class="message-panel"><p class="helper">Loading ASR config...</p></div>}>
          <div class="media-provider-grid">
            <For each={providerCards()}>{(provider) => (
              <section class={`media-provider-card ${config()?.provider === provider.provider ? "active" : ""}`} onClick={() => updateModel(provider.provider, provider.models[0]?.model ?? "")}>
                <div class="media-provider-head">
                  <h4>{provider.providerLabel}</h4>
                  <ConnectionTestControl
                    state={() => testState("asr", provider.provider)}
                    model={config()?.provider === provider.provider ? config()?.model ?? provider.models[0]?.model ?? "" : provider.models[0]?.model ?? ""}
                    unsaved={provider.provider !== "local_whisper" && config()?.provider === provider.provider && Boolean(apiKey().trim())}
                    onTest={() => runTest(provider.provider, config()?.provider === provider.provider ? config()?.model ?? provider.models[0]?.model ?? "" : provider.models[0]?.model ?? "")}
                    onToggle={() => updateTest("asr", provider.provider, { expanded: !testState("asr", provider.provider).expanded })}
                  />
                  <span class={`media-provider-status ${config()?.provider === provider.provider ? "active" : ""}`}>{config()?.provider === provider.provider ? "ACTIVE" : "Set Active"}</span>
                </div>
                <div class="media-model-chip-list" onClick={(event) => event.stopPropagation()}>
                  <For each={provider.models}>{(model) => (
                    <button class={`media-model-chip ${config()?.provider === provider.provider && config()?.model === model.model ? "selected" : ""} ${model.description ? "has-tooltip" : ""}`} type="button" title={model.description || model.label} data-tooltip={model.description || ""} onClick={() => updateModel(provider.provider, model.model)}>
                      {model.label}
                    </button>
                  )}</For>
                </div>
                <Show when={provider.provider !== "local_whisper"}>
                  <div class="media-key-row" onPointerDown={(event) => event.stopPropagation()} onClick={(event) => event.stopPropagation()}>
                    <label class="openflow-field">
                      <span>API Key</span>
                      <input
                        type="password"
                        placeholder={config()?.provider === provider.provider && config()?.has_api_key ? "Leave blank to keep existing key" : "Paste API Key"}
                        value={config()?.provider === provider.provider ? apiKey() : ""}
                        onFocus={() => activateProviderForInput(provider.provider, provider.models[0]?.model ?? "")}
                        onPaste={(event) => {
                          const pasted = event.clipboardData?.getData("text") ?? "";
                          if (!pasted) return;
                          event.preventDefault();
                          updateProviderApiKey(provider.provider, provider.models[0]?.model ?? "", pasted);
                        }}
                        onInput={(event) => {
                          updateProviderApiKey(provider.provider, provider.models[0]?.model ?? "", event.currentTarget.value);
                        }}
                      />
                    </label>
                    <div class="media-key-status">
                      <strong>{config()?.provider === provider.provider && config()?.has_api_key ? "API Key saved" : "API Key missing"}</strong>
                    </div>
                  </div>
                </Show>
                <Show when={config()?.provider === provider.provider}>
                  <div class="media-provider-foot" onClick={(event) => event.stopPropagation()}>
                    <label class="openflow-field asr-language-field">
                      <span>Language</span>
                      <input value={config()?.language ?? "zh"} onInput={(event) => setConfig((prev) => prev ? { ...prev, language: event.currentTarget.value } : prev)} />
                    </label>
                  </div>
                </Show>
              </section>
            )}</For>
          </div>
          <div class="asr-config-actions">
            <button class="secondary" type="button" onClick={props.onClose}>Cancel</button>
            <button type="button" disabled={saving() || !config()} onClick={() => void save()}>{saving() ? "Saving..." : "Save ASR Config"}</button>
          </div>
        </Show>
      </div>
    </Show>
  );
}
