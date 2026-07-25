import { For, Show, batch, createEffect, createMemo } from "solid-js";
import "./ModelPresetCards.css";

export const MODEL_PRESETS = [
  {
    key: "max",
    label: "Max",
    description: "Full reasoning capacity. Highest quality output and complex planning.",
    providerValues: ["max"],
    modelValues: ["max"],
  },
  {
    key: "flash",
    label: "Flash",
    description: "Optimized for speed. Fastest execution for straightforward tasks.",
    providerValues: ["flash"],
    modelValues: ["flash"],
  },
];

function norm(value) {
  return String(value || "").trim().toLowerCase();
}

function valuesForItem(item, keys) {
  return keys.map((key) => norm(item?.[key])).filter(Boolean);
}

function hasExactMatch(values, aliases) {
  const normalizedAliases = aliases.map(norm);
  return values.some((value) => normalizedAliases.includes(value));
}

function itemMatchesPreset(item, preset) {
  const providerValues = valuesForItem(item, ["providerID", "providerName", "providerLabel"]);
  const modelValues = valuesForItem(item, ["modelID", "modelName", "modelLabel"]);
  const providerMatch = hasExactMatch(providerValues, preset.providerValues);
  const modelMatch = hasExactMatch(modelValues, preset.modelValues);
  return providerMatch && modelMatch;
}

export function findModelPresetItem(items, presetKey) {
  const preset = MODEL_PRESETS.find((item) => item.key === presetKey);
  if (!preset) return null;
  return (items || []).find((item) => itemMatchesPreset(item, preset)) || null;
}

function directSelectionMatchesPreset(provider, model, preset) {
  const providerValues = [norm(provider)];
  const modelValues = [norm(model)];
  const providerMatch = hasExactMatch(providerValues, preset.providerValues);
  const modelMatch = hasExactMatch(modelValues, preset.modelValues);
  return providerMatch && modelMatch;
}

export function modelPresetKeyForSelection(items, provider, model) {
  const selectedItem = (items || []).find((item) => (
    (norm(item.providerID) === norm(provider) || norm(item.providerName) === norm(provider))
    && (norm(item.modelID) === norm(model) || norm(item.modelName) === norm(model))
  ));
  for (const preset of MODEL_PRESETS) {
    if (selectedItem && itemMatchesPreset(selectedItem, preset)) return preset.key;
    if (directSelectionMatchesPreset(provider, model, preset)) return preset.key;
  }
  return "";
}

export function modelPresetLabelForSelection(items, provider, model) {
  const key = modelPresetKeyForSelection(items, provider, model);
  return MODEL_PRESETS.find((item) => item.key === key)?.label || "";
}

function SparklesIcon() {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <path d="M12 3l1.9 4.1L18 9l-4.1 1.9L12 15l-1.9-4.1L6 9l4.1-1.9L12 3z" />
    <path d="M19 14l.9 2.1L22 17l-2.1.9L19 20l-.9-2.1L16 17l2.1-.9L19 14z" />
    <path d="M5 15l.8 1.7L8 17.5l-2.2.8L5 20l-.8-1.7L2 17.5l2.2-.8L5 15z" />
  </svg>;
}

function ZapIcon() {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <path d="M13 2L4 14h7l-1 8 10-12h-7l1-8z" />
  </svg>;
}

function CircleIcon(props) {
  if (props.checked) {
    return <svg class="model-preset-check" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
      <path d="M8 12.2l2.4 2.4L16.5 8.5" />
    </svg>;
  }
  return <svg class="model-preset-check" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
    <circle cx="12" cy="12" r="9" />
  </svg>;
}

export function ModelPresetCards(props) {
  const items = () => Array.isArray(props.items) ? props.items : [];
  const selectedKey = createMemo(() => modelPresetKeyForSelection(items(), props.provider, props.model));

  function emitSelection(selection) {
    batch(() => props.onSelect?.(selection));
  }

  createEffect(() => {
    if (props.autoSelect === false || props.disabled || selectedKey()) return;
    const item = findModelPresetItem(items(), "max") ?? findModelPresetItem(items(), "flash");
    if (!item) return;
    const preset = MODEL_PRESETS.find((candidate) => candidate.key === modelPresetKeyForSelection([item], item.providerID, item.modelID));
    emitSelection({
      preset,
      providerID: item.providerID,
      modelID: item.modelID,
      item,
    });
  });

  function selectPreset(preset) {
    const item = findModelPresetItem(items(), preset.key);
    if (!item || props.disabled) return;
    emitSelection({
      preset,
      providerID: item.providerID,
      modelID: item.modelID,
      item,
    });
  }

  return <div class={`model-preset-card-grid ${props.class || ""}`} role="radiogroup" aria-label={props["aria-label"] || "Model preset"}>
    <For each={MODEL_PRESETS}>{(preset) => {
      const item = () => findModelPresetItem(items(), preset.key);
      const selected = () => selectedKey() === preset.key;
      const unavailable = () => !item();
      return <button
        type="button"
        role="radio"
        aria-checked={selected()}
        class={`model-preset-card model-preset-${preset.key} ${selected() ? "is-selected" : ""}`}
        disabled={Boolean(props.disabled) || unavailable()}
        onClick={() => selectPreset(preset)}
      >
        <span class="model-preset-selected-bar" />
        <span class="model-preset-head">
          <span class="model-preset-icon">
            <Show when={preset.key === "max"} fallback={<ZapIcon />}>
              <SparklesIcon />
            </Show>
          </span>
          <CircleIcon checked={selected()} />
        </span>
        <span class="model-preset-title">{preset.label}</span>
        <span class="model-preset-description">{preset.description}</span>
        <Show when={unavailable()}>
          <span class="model-preset-unavailable">Not connected</span>
        </Show>
      </button>;
    }}</For>
  </div>;
}
