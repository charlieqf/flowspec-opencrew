import { For, Index, Show } from "solid-js";
import { formatCnyAmount, unitLabel } from "../lib/meteringFormat.js";
import { ClockCounterClockwiseIcon, CloseIcon, PriceListIcon } from "./appShellUtils.jsx";

export default function SettingsDrawers(props) {
  const {
    asrDialog,
    setAsrDialog,
    asrProviderCards,
    asrConfig,
    updateAsrModel,
    renderConnectionTestControl,
    runAsrConnectionTest,
    activateAsrProviderForInput,
    setAsrConfig,
    saveAsrConfig,
    mediaDialog,
    setMediaDialog,
    mediaDialogTitle,
    setMediaUnitPriceOpen,
    setMediaPriceListOpen,
    mediaPriceListOpen,
    usdCnyRate,
    mediaPriceRanking,
    mediaDialogKindLabel,
    mediaUnitPriceOpen,
    mediaUnitPriceRows,
    mediaConfig,
    setActiveMediaProvider,
    hasMediaKeyInput,
    runMediaConnectionTest,
    selectedMediaModelPriceText,
    mediaCredentialFields,
    mediaCredentialKey,
    mediaApiKeys,
    setMediaApiKeys,
    startMediaAgentDrag,
    updateMediaProviderModel,
    mediaSupportsAgentAliases,
    setMediaAgentPoolElement,
    handleMediaAgentDrop,
    mediaAgentKindLabel,
    mediaAgentAliases,
    updateMediaAgentAlias,
    mediaProviderLabel,
    mediaModelLabel,
    removeMediaAgentAlias,
    lipsyncPriceComparisonRows,
    mediaAgentDrag,
    saveMediaConfig
  } = props;
  return (
    <>
      <Show when={asrDialog().open}>
        <div class="drawer-backdrop" onClick={() => setAsrDialog((prev) => ({ ...prev, open: false }))}/>
        <div class="env-dialog asr-config-dialog">
          <div class="env-dialog-head">
            <div>
              <h3>ASR Model Settings</h3>
            </div>
            <button class="icon-action" type="button" title="Close" onClick={() => setAsrDialog((prev) => ({ ...prev, open: false }))}><CloseIcon /></button>
          </div>
          <Show when={asrDialog().error}>
            <div class="banner bad">{asrDialog().error}</div>
          </Show>
          <Show when={!asrDialog().loading} fallback={<div class="message-panel"><p class="helper">Loading ASR config...</p></div>}>
            <div class="media-provider-grid">
              <For each={asrProviderCards()}>{(provider) => (<section class={`media-provider-card ${asrConfig()?.provider === provider.provider ? "active" : ""}`} onClick={() => updateAsrModel(provider.provider, provider.models[0]?.model ?? "")}>
                  <div class="media-provider-head">
                    <div>
                      <h4>{provider.providerLabel}</h4>
                    </div>
                    {renderConnectionTestControl("asr", provider.provider, asrConfig()?.provider === provider.provider ? asrConfig()?.model ?? provider.models[0]?.model ?? "" : provider.models[0]?.model ?? "", provider.provider !== "local_whisper" && asrConfig()?.provider === provider.provider && Boolean(asrDialog().api_key.trim()), () => runAsrConnectionTest(provider.provider, asrConfig()?.provider === provider.provider ? asrConfig()?.model ?? provider.models[0]?.model ?? "" : provider.models[0]?.model ?? ""))}
                    <span class={`media-provider-status ${asrConfig()?.provider === provider.provider ? "active" : ""}`}>{asrConfig()?.provider === provider.provider ? "ACTIVE" : "Set Active"}</span>
                  </div>
                  <div class="media-model-chip-list" onClick={(event) => event.stopPropagation()}>
                    <For each={provider.models}>{(model) => (<button class={`media-model-chip ${asrConfig()?.provider === provider.provider && asrConfig()?.model === model.model ? "selected" : ""} ${model.description ? "has-tooltip" : ""}`} type="button" title={model.description || model.label} data-tooltip={model.description || ""} onClick={() => updateAsrModel(provider.provider, model.model)}>
                        {model.label}
                      </button>)}</For>
                  </div>
                  <Show when={provider.provider !== "local_whisper"}>
                    <div class="media-key-row" onPointerDown={(event) => event.stopPropagation()} onClick={(event) => event.stopPropagation()}>
                      <label class="openflow-field">
                        <span>API Key</span>
                        <input type="password" placeholder={asrConfig()?.provider === provider.provider && asrConfig()?.has_api_key ? "Leave blank to keep existing key" : "Paste API Key"} value={asrConfig()?.provider === provider.provider ? asrDialog().api_key : ""} onFocus={() => activateAsrProviderForInput(provider.provider, provider.models[0]?.model ?? "")} onPaste={(event) => {
                const pasted = event.clipboardData?.getData("text") ?? "";
                if (!pasted)
                    return;
                event.preventDefault();
                activateAsrProviderForInput(provider.provider, provider.models[0]?.model ?? "");
                setAsrDialog((prev) => ({ ...prev, api_key: pasted }));
            }} onInput={(e) => {
                const nextApiKey = e.currentTarget.value;
                activateAsrProviderForInput(provider.provider, provider.models[0]?.model ?? "");
                setAsrDialog((prev) => ({ ...prev, api_key: nextApiKey }));
            }}/>
                      </label>
                      <div class="media-key-status">
                        <strong>{asrConfig()?.provider === provider.provider && asrConfig()?.has_api_key ? "API Key saved" : "API Key missing"}</strong>
                      </div>
                    </div>
                  </Show>
                  <Show when={asrConfig()?.provider === provider.provider}>
                    <div class="media-provider-foot" onClick={(event) => event.stopPropagation()}>
                      <label class="openflow-field asr-language-field">
                        <span>Language</span>
                        <input value={asrConfig()?.language ?? "zh"} onInput={(e) => setAsrConfig((prev) => prev ? { ...prev, language: e.currentTarget.value } : prev)}/>
                      </label>
                    </div>
                  </Show>
                </section>)}</For>
            </div>
            <div class="asr-config-actions">
              <button class="secondary" type="button" onClick={() => setAsrDialog((prev) => ({ ...prev, open: false }))}>Cancel</button>
              <button type="button" disabled={asrDialog().saving || !asrConfig()} onClick={() => void saveAsrConfig()}>{asrDialog().saving ? "Saving..." : "Save ASR Config"}</button>
            </div>
          </Show>
        </div>
      </Show>

      <Show when={mediaDialog().open}>
        <div class="drawer-backdrop" onClick={() => setMediaDialog((prev) => ({ ...prev, open: false }))}/>
        <div class="env-dialog media-config-dialog">
          <div class="env-dialog-head">
            <div>
              <h3>{mediaDialogTitle()}</h3>
            </div>
            <div class="media-dialog-head-actions">
              <div class="media-price-menu-wrap">
                <button class="icon-action media-price-list-button" type="button" title="Price ranking by daily USD/CNY rate" aria-label="Price ranking by daily USD/CNY rate" onClick={() => {
            setMediaUnitPriceOpen(false);
            setMediaPriceListOpen((value) => !value);
        }}><PriceListIcon /></button>
                <Show when={mediaPriceListOpen()}>
                  <div class="media-price-popover">
                    <div class="media-price-popover-head">
                      <strong>{mediaDialogKindLabel()} Price Ranking</strong>
                      <span>USD/CNY {usdCnyRate().rate.toFixed(4)} · {usdCnyRate().date}</span>
                    </div>
                    <Show when={usdCnyRate().error}>
                      <p class="media-price-rate-warning">Exchange rate fetch failed; using fallback rate.</p>
                    </Show>
                    <div class="media-price-list">
                      <Show when={mediaPriceRanking().length} fallback={<div class="media-price-empty">No fixed public per-unit prices found for the currently available models.</div>}>
                      <For each={mediaPriceRanking()}>{(item, index) => (<div class="media-price-row">
                          <span class="media-price-rank">{index() + 1}</span>
                          <div class="media-price-main">
                            <strong>{item.providerLabel} · {item.model}</strong>
                            <span>{item.variant} · {item.originalText}</span>
                          </div>
                          <div class="media-price-cny">{formatCnyAmount(item.cnyAmount)}/{unitLabel(item.unit)}</div>
                        </div>)}</For>
                      </Show>
                    </div>
                  </div>
                </Show>
              </div>
              <div class="media-price-menu-wrap">
                <button class="icon-action media-price-list-button" type="button" title="Minimum unit price" aria-label="Minimum unit price" onClick={() => {
            setMediaPriceListOpen(false);
            setMediaUnitPriceOpen((value) => !value);
        }}><ClockCounterClockwiseIcon /></button>
                <Show when={mediaUnitPriceOpen()}>
                  <div class="media-price-popover media-min-unit-popover">
                    <div class="media-price-popover-head">
                      <strong>{mediaDialogKindLabel()} Minimum Unit Price</strong>
                      <span>USD/CNY {usdCnyRate().rate.toFixed(4)} · {usdCnyRate().date}</span>
                    </div>
                    <Show when={usdCnyRate().error}>
                      <p class="media-price-rate-warning">Exchange rate fetch failed; using fallback rate.</p>
                    </Show>
                    <div class="media-price-list">
                      <Show when={mediaUnitPriceRows().length} fallback={<div class="media-price-empty">No fixed public per-unit prices found for the currently available models.</div>}>
                      <For each={mediaUnitPriceRows()}>{(item, index) => (<div class="media-price-row media-min-unit-row">
                          <span class="media-price-rank">{index() + 1}</span>
                          <div class="media-price-main">
                            <strong>{item.providerLabel} · {item.model}</strong>
                            <span>{item.variant} · {item.originalText}</span>
                          </div>
                          <div class="media-min-unit-price">
                            <strong>{item.cnyPrice}</strong>
                            <span>{item.originalText}</span>
                          </div>
                        </div>)}</For>
                      </Show>
                    </div>
                  </div>
                </Show>
              </div>
              <button class="icon-action" type="button" title="Close" onClick={() => setMediaDialog((prev) => ({ ...prev, open: false }))}><CloseIcon /></button>
            </div>
          </div>
          <Show when={mediaDialog().error}>
            <div class="banner bad">{mediaDialog().error}</div>
          </Show>
          <Show when={!mediaDialog().loading && mediaConfig()} fallback={<div class="message-panel"><p class="helper">Loading {mediaDialog().kind} model config...</p></div>}>
            <div class="media-provider-grid">
              <For each={mediaConfig()?.providers ?? []}>{(provider) => (<section class={`media-provider-card ${provider.active ? "active" : ""}`} onClick={() => setActiveMediaProvider(provider.provider)}>
                  <div class="media-provider-head">
                    <div>
                      <h4>{provider.provider_label}</h4>
                    </div>
                    {renderConnectionTestControl(`media:${mediaDialog().kind}`, provider.provider, provider.model, hasMediaKeyInput(provider.provider), () => runMediaConnectionTest(provider.provider, provider.model))}
                    <span class={`media-provider-status ${provider.active ? "active" : ""}`}>{provider.active ? "ACTIVE" : "Set Active"}</span>
                  </div>
                  <div class="media-model-chip-list" onClick={(event) => event.stopPropagation()}>
                    <For each={provider.models}>{(model) => (<span class="media-model-chip-wrap">
                      <button class={`media-model-chip ${provider.model === model.model ? "selected" : ""} ${model.description ? "has-tooltip" : ""}`} type="button" title={model.description || model.label} data-tooltip={model.description || ""} onPointerDown={(event) => startMediaAgentDrag(event, provider.provider, model.model, model.label)} onClick={() => {
                setActiveMediaProvider(provider.provider);
                updateMediaProviderModel(provider.provider, model.model);
            }}>
                        {model.label}
                      </button>
                    </span>)}</For>
                  </div>
                  <Show when={selectedMediaModelPriceText(provider)}>
                    <div class="media-selected-model-price" onClick={(event) => event.stopPropagation()}>
                      {selectedMediaModelPriceText(provider)}
                    </div>
                  </Show>
                  <div class={`media-key-row ${mediaCredentialFields(provider).length > 1 ? "media-key-row-split" : ""}`} onClick={(event) => event.stopPropagation()}>
                    <For each={mediaCredentialFields(provider)}>{(field) => (<label class="openflow-field">
                        <span>{field.label}</span>
                        <input
                          type={field.type || "password"}
                          placeholder={provider.has_api_key ? `Leave blank to keep existing ${field.label}` : `Paste ${field.label}`}
                          value={mediaApiKeys()[mediaCredentialKey(provider, field)] ?? ""}
                          onInput={(e) => setMediaApiKeys((prev) => ({ ...prev, [mediaCredentialKey(provider, field)]: e.currentTarget.value }))}
                        />
                      </label>)}</For>
                      <div class="media-key-status">
                        <strong>{provider.has_api_key ? "Credentials saved" : "Credentials missing"}</strong>
                      </div>
                  </div>
                  <div class="media-provider-foot">
                    <a href={provider.docs_url} target="_blank" rel="noreferrer" onClick={(event) => event.stopPropagation()}>Docs</a>
                  </div>
                </section>)}</For>
              <Show when={mediaSupportsAgentAliases()}>
                <section ref={setMediaAgentPoolElement} class="media-agent-model-pool" onDragOver={(event) => {
            event.preventDefault();
            if (event.dataTransfer)
                event.dataTransfer.dropEffect = "copy";
        }} onDrop={handleMediaAgentDrop}>
                  <div class="media-agent-model-pool-head">
                    <div>
                      <h4>{mediaAgentKindLabel()} Models</h4>
                    </div>
                    <span>{mediaAgentAliases().length} models</span>
                  </div>
                  <Show when={mediaAgentAliases().length} fallback={<div class="media-agent-model-empty">Drag configured {mediaAgentKindLabel().toLowerCase()} models into this area.</div>}>
                    <div class="media-agent-alias-list">
                      <Index each={mediaAgentAliases()}>{(item, index) => (<div class="media-agent-alias-row">
                          <input value={item().alias} placeholder="Alias" onInput={(event) => updateMediaAgentAlias(index, event.currentTarget.value)}/>
                          <span>{mediaProviderLabel(item().provider)} · {mediaModelLabel(item().provider, item().model)}</span>
                          <button type="button" onClick={() => removeMediaAgentAlias(index)}>Remove</button>
                        </div>)}</Index>
                    </div>
                  </Show>
                </section>
              </Show>
            </div>
            <Show when={mediaDialog().kind === "lipsync"}>
              <div class="lipsync-price-panel">
                <div class="lipsync-price-head">
                  <strong>Provider Model Pricing · RMB</strong>
                  <span>USD/CNY {usdCnyRate().rate.toFixed(4)}</span>
                </div>
                <div class="lipsync-price-table">
                  <For each={lipsyncPriceComparisonRows()}>{(row) => (<div class="lipsync-price-table-row">
                    <span>{row.provider}</span>
                    <strong>{row.model}</strong>
                    <em>{row.cnyPrice}</em>
                    <small>{`${row.note}${row.conversionNote ? ` ${row.conversionNote}` : ""}`}</small>
                  </div>)}</For>
                </div>
              </div>
            </Show>
            <Show when={mediaAgentDrag()}>
              {(drag) => <div class="media-agent-drag-ghost" style={{ left: `${drag().x}px`, top: `${drag().y}px` }}>{drag().label}</div>}
            </Show>
            <div class="asr-config-actions media-config-actions">
              <button class="secondary" type="button" onClick={() => setMediaDialog((prev) => ({ ...prev, open: false }))}>Cancel</button>
              <button type="button" disabled={mediaDialog().saving || !mediaConfig()} onClick={() => void saveMediaConfig()}>{mediaDialog().saving ? "Saving..." : `Save ${mediaDialogKindLabel()} Config`}</button>
            </div>
          </Show>
        </div>
      </Show>
    </>
  );
}
