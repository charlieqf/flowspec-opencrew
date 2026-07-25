import { For, Show } from "solid-js";
import FlowIcon from "../components/FlowIcon.jsx";

const MODEL_OPTIONS = [
  {
    id: "avatar_iv",
    label: "Avatar IV",
    engine_type: "avatar_iv",
    model_name: "Avatar IV",
  },
  {
    id: "avatar_v",
    label: "Avatar V",
    engine_type: "avatar_v",
    model_name: "Avatar V",
  },
  {
    id: "video_agent",
    label: "Video Agent",
    engine_type: "video_agent",
    model_name: "Cloud Video Agent",
  },
];

export default function DigitalHumanSettingsPanel(props) {
  const settings = () => props.settings?.() || {};
  const update = (patch) => props.setSettings?.({ ...settings(), ...patch });
  const selectedModel = () => settings().generation_model || settings().engine_type || "avatar_iv";
  const updateModel = (option) => update({
    generation_model: option.id,
    model_name: option.model_name,
    engine_type: option.engine_type,
  });
  return <Show when={props.open?.()}>
    <div class="dh-modal-backdrop" role="presentation" onClick={() => props.onClose?.()}>
      <section class="dh-settings" role="dialog" aria-label="Agent Settings" onClick={(event) => event.stopPropagation()}>
        <header class="dh-modal-head">
          <button type="button" onClick={() => props.onClose?.()} aria-label="Back"><FlowIcon name="arrowBack" /></button>
          <strong>Agent Settings</strong>
          <button type="button" onClick={() => props.onClose?.()} aria-label="Close"><FlowIcon name="close" /></button>
        </header>
        <div class="dh-settings-body">
          <section>
            <h3>Confirm before generating</h3>
            <button class={`dh-option-row ${settings().confirm_before_generating !== "never" ? "is-active" : ""}`} type="button" onClick={() => update({ confirm_before_generating: "always" })}>
              <span class="dh-radio" />
              <div><strong>Always</strong><small>Agent will ask for confirmation before generating media.</small></div>
            </button>
            <button class={`dh-option-row ${settings().confirm_before_generating === "never" ? "is-active" : ""}`} type="button" onClick={() => update({ confirm_before_generating: "never" })}>
              <span class="dh-radio" />
              <div><strong>Never</strong><small>Agent will generate media automatically.</small></div>
            </button>
          </section>
          <div class="dh-segment">
            <button class={settings().aspect !== "16:9" ? "is-active" : ""} type="button" onClick={() => update({ aspect: "9:16" })}><FlowIcon name="video" />9:16</button>
            <button class={settings().aspect === "16:9" ? "is-active" : ""} type="button" onClick={() => update({ aspect: "16:9" })}><FlowIcon name="video" />16:9</button>
          </div>
          <div class="dh-segment">
            <button class={Number(settings().count || 1) !== 2 ? "is-active" : ""} type="button" onClick={() => update({ count: 1 })}>x1</button>
            <button class={Number(settings().count || 1) === 2 ? "is-active" : ""} type="button" onClick={() => update({ count: 2 })}>x2</button>
          </div>
          <section class="dh-model-strip">
            <div class="dh-model-strip-head">
              <h3>Video Models</h3>
              <span>{MODEL_OPTIONS.length} models</span>
            </div>
            <div class="dh-model-pills">
              <For each={MODEL_OPTIONS}>{(option) => (
                <button class={`dh-model-pill ${selectedModel() === option.id || settings().engine_type === option.engine_type ? "is-active" : ""}`} type="button" onClick={() => updateModel(option)}>
                  {option.label}
                </button>
              )}</For>
            </div>
          </section>
        </div>
        <button class="dh-done" type="button" onClick={() => props.onClose?.()}>Done</button>
      </section>
    </div>
  </Show>;
}
