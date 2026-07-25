import { For, Show, createMemo } from "solid-js";
import FlowIcon from "./FlowIcon.jsx";

const imageAspects = ["16:9", "4:3", "1:1", "3:4", "9:16"];
const counts = [1, 2, 3, 4];

function text(value) {
  return String(value || "").trim();
}

function chatModelKey(item = {}) {
  return `${text(item.providerID)}::${text(item.modelID)}`;
}

function chatModelLabel(item = {}) {
  const model = text(item.modelName || item.modelID);
  const provider = text(item.providerName || item.providerID);
  return model || provider || "Model";
}

export default function ImagesAgentSettings(props) {
  const config = () => props.imageModelConfig?.() || {};
  const chatModels = () => props.chatModelItems?.() || [];
  const agentModels = createMemo(() => config().agent_model_aliases || []);
  const aliasValue = createMemo(() => {
    const current = props.settings();
    return current.agentImageAlias || agentModels().find((item) => item.provider === current.provider && item.model === current.model)?.alias || "";
  });
  const selectedChatModelKey = createMemo(() => {
    const settingsKey = props.settings().chatProvider && props.settings().chatModel
      ? `${props.settings().chatProvider}::${props.settings().chatModel}`
      : "";
    return settingsKey || props.selectedChatModelKey?.() || "";
  });
  const update = (patch) => props.setSettings((previous) => ({ ...previous, ...patch }));
  const selectAgentModel = (item) => update({
    agentImageAlias: item.alias || "",
    provider: "",
    model: "",
  });
  const selectChatModel = (item) => {
    update({ chatProvider: item.providerID || "", chatModel: item.modelID || "" });
    props.onChatModelChange?.(item);
  };

  return <div class="ual-agent-settings-panel">
    <header>
      <button type="button" class="ual-agent-settings-icon" onClick={props.onClose} aria-label="Back"><FlowIcon name="arrowBack" /></button>
      <strong>Agent settings</strong>
      <button type="button" class="ual-agent-settings-icon is-close" onClick={props.onClose} aria-label="Close"><FlowIcon name="close" /></button>
    </header>
    <section class="ual-agent-settings-body">
      <div class="ual-setting-group">
        <span class="ual-setting-label">Confirm before generating</span>
        <label class="ual-setting-radio">
          <input type="radio" name="ual-agent-confirm" checked={props.settings().confirmBeforeGenerate !== false} onChange={() => update({ confirmBeforeGenerate: true })} />
          <span><strong>Always</strong><small>Agent will ask for confirmation before generating media.</small></span>
        </label>
        <label class="ual-setting-radio">
          <input type="radio" name="ual-agent-confirm" checked={props.settings().confirmBeforeGenerate === false} onChange={() => update({ confirmBeforeGenerate: false })} />
          <span><strong>Never</strong><small>Agent will generate media automatically.</small></span>
        </label>
      </div>
      <div class="ual-setting-group">
        <span class="ual-setting-label">Image generation default</span>
        <div class="ual-setting-segment is-aspect">
          <For each={imageAspects}>{(item) => <button type="button" class={props.settings().aspect === item ? "is-active" : ""} onClick={() => update({ aspect: item })}>
            <span class={`ual-aspect-icon is-${item.replace(":", "-")}`} />
            <span>{item}</span>
          </button>}</For>
        </div>
        <div class="ual-setting-segment">
          <For each={counts}>{(count) => <button type="button" class={props.settings().count === count ? "is-active" : ""} onClick={() => update({ count })}>x{count}</button>}</For>
        </div>
        <Show when={agentModels().length} fallback={<div class="ual-setting-select is-empty">Configure Agent image models first</div>}>
          <div class="ual-setting-model-box">
            <div class="ual-setting-model-provider">
              <span>Image Models</span>
              <small>{agentModels().length} models</small>
            </div>
            <div class="ual-setting-model-options">
              <For each={agentModels()}>{(model) => <button
                type="button"
                class={aliasValue() === model.alias ? "is-active" : ""}
                title={model.alias || "Image model"}
                onClick={() => selectAgentModel(model)}
              >
                {model.alias || model.model}
              </button>}</For>
            </div>
          </div>
        </Show>
        <Show when={chatModels().length} fallback={<div class="ual-setting-select is-empty">No OpenCode Agent models</div>}>
          <div class="ual-setting-model-box">
            <div class="ual-setting-model-provider">
              <span>Agent Models</span>
              <small>{chatModels().length} models</small>
            </div>
            <div class="ual-setting-model-options">
              <For each={chatModels()}>{(model) => <button
                type="button"
                class={selectedChatModelKey() === chatModelKey(model) ? "is-active" : ""}
                onClick={() => selectChatModel(model)}
              >
                {chatModelLabel(model)}
              </button>}</For>
            </div>
          </div>
        </Show>
      </div>
    </section>
    <footer>
      <button type="button" disabled={props.saving?.()} onClick={props.onSave}>{props.saving?.() ? "Saving..." : "Save"}</button>
    </footer>
  </div>;
}
