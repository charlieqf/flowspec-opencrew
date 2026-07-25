import { For, Show, createMemo } from "solid-js";
import FlowIcon from "./FlowIcon.jsx";

const imageAspects = ["16:9", "4:3", "1:1", "3:4", "9:16"];
const counts = [1, 2, 3, 4];

export default function ImageAPISettings(props) {
  const config = () => props.imageModelConfig?.() || {};
  const agentModels = createMemo(() => config().agent_model_aliases || []);
  const aliasValue = createMemo(() => {
    const current = props.settings();
    return current.agentImageAlias || agentModels().find((item) => item.provider === current.provider && item.model === current.model)?.alias || "";
  });
  const update = (patch) => props.setSettings((previous) => ({ ...previous, ...patch }));
  const selectAgentModel = (item) => update({
    agentImageAlias: item.alias || "",
    provider: "",
    model: "",
  });

  return <div class="ual-agent-settings-panel">
    <header>
      <button type="button" class="ual-agent-settings-icon" onClick={props.onClose} aria-label="Back"><FlowIcon name="arrowBack" /></button>
      <strong>API Settings</strong>
      <button type="button" class="ual-agent-settings-icon is-close" onClick={props.onClose} aria-label="Close"><FlowIcon name="close" /></button>
    </header>
    <section class="ual-agent-settings-body">
      <div class="ual-setting-group">
        <span class="ual-setting-label">Confirm before generating</span>
        <label class="ual-setting-radio">
          <input type="radio" name="ual-api-confirm" checked={props.settings().confirmBeforeGenerate !== false} onChange={() => update({ confirmBeforeGenerate: true })} />
          <span><strong>Always</strong><small>Ask before calling the image API.</small></span>
        </label>
        <label class="ual-setting-radio">
          <input type="radio" name="ual-api-confirm" checked={props.settings().confirmBeforeGenerate === false} onChange={() => update({ confirmBeforeGenerate: false })} />
          <span><strong>Never</strong><small>Call the image API without confirmation.</small></span>
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
      </div>
    </section>
    <footer>
      <button type="button" disabled={props.saving?.()} onClick={props.onSave}>{props.saving?.() ? "Saving..." : "Save"}</button>
    </footer>
  </div>;
}
