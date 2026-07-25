import { For, Show, createEffect, createSignal } from "solid-js";
import { ArrowIcon, SaveIcon, ShuffleIcon, SparkIcon, VolumeIcon } from "../storyboardIcons.jsx";

export function FixedMenu(props) {
  return <div class="ocsb-menu-wrap">
    <button class="ocsb-toolbar-btn icon-only" type="button" title="Fixed Splitting" onClick={() => props.setFixedMenuOpen(!props.fixedMenuOpen())}>
      <ShuffleIcon />
    </button>
    <Show when={props.fixedMenuOpen()}>
      <div class="ocsb-menu-panel">
        <label>分镜时长</label>
        <div class="ocsb-choice-grid">
          <For each={[8, 16]}>{(seconds) => <button class={props.fixedShotSeconds() === seconds ? "is-active" : ""} type="button" onClick={() => props.setFixedShotSeconds(seconds)}>{seconds}s</button>}</For>
        </div>
        <label>场景时长</label>
        <div class="ocsb-choice-grid sky">
          <For each={[4, 8]}>{(seconds) => <button class={props.fixedSceneSeconds() === seconds ? "is-active" : ""} type="button" onClick={() => props.setFixedSceneSeconds(seconds)}>{seconds}s</button>}</For>
        </div>
        <button class="ocsb-apply-btn" type="button" onClick={props.reorganizeFixedStoryboard}>应用固定时长</button>
      </div>
    </Show>
  </div>;
}

export function AiMenu(props) {
  return <div class="ocsb-menu-wrap">
    <button class="ocsb-toolbar-btn ai icon-only" type="button" title="AI Splitting" onClick={() => props.setAiMenuOpen(!props.aiMenuOpen())}>
      <SparkIcon />
    </button>
    <Show when={props.aiMenuOpen()}>
      <div class="ocsb-menu-panel ai">
        <strong>Story Logic Splitting</strong>
        <label>Directorial Prompt</label>
        <input value={props.aiPrompt()} placeholder="e.g. 按情绪转折和动作节奏拆分" onInput={(event) => props.setAiPrompt(event.currentTarget.value)} />
        <button class="ocsb-apply-btn ai" type="button" disabled={!props.aiPrompt().trim()} onClick={props.aiReorganizeStoryboard}>Split Script Sequence</button>
      </div>
    </Show>
  </div>;
}

export function TimingMenu(props) {
  const model = () => props.timingModel() || {};
  const [settingsDraft, setSettingsDraft] = createSignal({});
  const audioSettings = () => props.audioSettings?.() || {};
  let providerSelectEl = null;
  let modelSelectEl = null;
  let voiceSelectEl = null;
  let tempoInputEl = null;
  let promptTextareaEl = null;
  createEffect(() => {
    if (!props.timingMenuOpen()) setSettingsDraft({});
  });
  const resetSettingsDraft = () => setSettingsDraft({ ...audioSettings() });
  const updateSettingsDraft = (patch) => setSettingsDraft((draft) => ({ ...draft, ...patch }));
  const providerModels = () => props.ttsModelsForProvider?.(settingsDraft().provider) || [];
  const modelVoices = () => props.ttsVoicesForModel?.(settingsDraft().provider, settingsDraft().model) || [];
  const positiveNumber = (value) => {
    const number = Number(value);
    return Number.isFinite(number) && number > 0 ? number : null;
  };
  const recommendationTempo = (item) => {
    const fitMeta = item?.fit_meta && typeof item.fit_meta === "object" ? item.fit_meta : {};
    return positiveNumber(item?.tempo ?? item?.speed_factor ?? fitMeta.tempo ?? fitMeta.speed_factor);
  };
  const recommendationScore = (item) => {
    return positiveNumber(item?.score);
  };
  const voiceMatches = (draft, item) => {
    const draftCandidateId = String(draft.candidateId || draft.candidate_id || "").trim();
    const itemCandidateId = String(item?.candidate_id || "").trim();
    if (draftCandidateId && itemCandidateId && draftCandidateId === itemCandidateId) return true;
    const draftValues = [draft.voiceId, draft.voice, draft.label].map((value) => String(value || "").trim().toLowerCase()).filter(Boolean);
    const itemValues = [item?.voice_id, item?.voice, item?.label].map((value) => String(value || "").trim().toLowerCase()).filter(Boolean);
    return draftValues.some((draftValue) => itemValues.some((itemValue) => draftValue === itemValue || draftValue.includes(itemValue) || itemValue.includes(draftValue)));
  };
  const recommendationActive = (item) => {
    return voiceMatches(settingsDraft(), item);
  };
  const updateTempo = (value) => {
    const next = String(value || "").replace("。", ".").replace(",", ".");
    if (/^\d*(?:\.\d*)?$/.test(next)) updateSettingsDraft({ tempo: next });
  };
  const tempoValueForSave = () => {
    const value = String(tempoInputEl?.value ?? settingsDraft().tempo ?? "").replace("。", ".").replace(",", ".").trim();
    const number = Number(value);
    if (Number.isFinite(number) && number > 0) return value;
    return settingsDraft().tempo || "1";
  };
  const updateProvider = (value) => updateSettingsDraft({ provider: value, model: "", voiceId: "", label: "", candidateId: "", candidate_id: "" });
  const updateModel = (value) => updateSettingsDraft({ model: value, voiceId: "", label: "", candidateId: "", candidate_id: "" });
  const updateVoice = (value) => updateSettingsDraft({ voiceId: value, label: "", candidateId: "", candidate_id: "" });
  const openTimingMenu = async () => {
    const nextOpen = !props.timingMenuOpen();
    if (!nextOpen) {
      props.setTimingMenuOpen(false);
      return;
    }
    await props.openAudioSettings?.();
    resetSettingsDraft();
    props.setTimingMenuOpen(true);
  };
  const applyRecommendation = (item) => {
    const nextTempo = recommendationTempo(item) || settingsDraft().tempo || 1;
    updateSettingsDraft({
      provider: item.provider || settingsDraft().provider || "",
      model: item.model || settingsDraft().model || "",
      voiceId: item.voice_id || item.voice || settingsDraft().voiceId || "",
      prompt: item.prompt_template || item.instructions || item.prompt || settingsDraft().prompt || "",
      tempo: nextTempo,
      label: item.label || item.voice_id || item.voice || "",
      candidateId: item.candidate_id || "",
    });
    if (tempoInputEl) tempoInputEl.value = String(nextTempo);
  };
  const saveSettings = async () => {
    await props.saveAudioSettings?.({
      ...settingsDraft(),
      provider: providerSelectEl?.value ?? settingsDraft().provider,
      model: modelSelectEl?.value ?? settingsDraft().model,
      voiceId: voiceSelectEl?.value ?? settingsDraft().voiceId,
      tempo: tempoValueForSave(),
      prompt: promptTextareaEl?.value ?? settingsDraft().prompt,
    });
    props.setTimingMenuOpen(false);
  };
  return <div class="ocsb-menu-wrap">
    <button class="ocsb-toolbar-btn icon-only" type="button" title="Builder-G Voice Timing" onClick={() => void openTimingMenu()}>
      <VolumeIcon />
    </button>
    <Show when={props.timingMenuOpen()}>
      <div class="ocsb-menu-panel timing">
        <strong>Builder-G Timing</strong>
        <div class="ocsb-timing-grid">
          <span>Voice</span><b>{model().voice || "-"}</b>
          <span>Duration</span><b>{Number(model().build_g_duration || 0).toFixed(2)}s</b>
          <span>Chars</span><b>{model().build_g_chars || 0}</b>
          <span>Rate</span><b>{Number(model().sec_per_char || 0).toFixed(3)}s/字</b>
        </div>
        <p>{String(model().build_g_text || "").slice(0, 100) || "No Builder-G text found."}</p>
        <div class="ocsb-audio-form-grid">
          <label><span>Provider</span><select ref={providerSelectEl} value={settingsDraft().provider || ""} onInput={(event) => updateProvider(event.currentTarget.value)} onChange={(event) => updateProvider(event.currentTarget.value)}><option value="">-</option><For each={props.ttsProviderOptions?.() || []}>{(item) => <option value={item.provider}>{item.providerLabel || item.provider}</option>}</For></select></label>
          <label><span>Model</span><select ref={modelSelectEl} value={settingsDraft().model || ""} onInput={(event) => updateModel(event.currentTarget.value)} onChange={(event) => updateModel(event.currentTarget.value)}><option value="">-</option><For each={providerModels()}>{(item) => <option value={item.model}>{item.label || item.model}</option>}</For></select></label>
          <label><span>Voice</span><select ref={voiceSelectEl} value={settingsDraft().voiceId || ""} onInput={(event) => updateVoice(event.currentTarget.value)} onChange={(event) => updateVoice(event.currentTarget.value)}><option value="">-</option><For each={modelVoices()}>{(item) => <option value={item.voice_id || item.voice}>{item.label || item.voice_id || item.voice}</option>}</For></select></label>
          <label><span>TTS Tempo</span><input type="text" inputmode="decimal" pattern="[0-9]*[.]?[0-9]*" ref={tempoInputEl} value={String(settingsDraft().tempo ?? "")} onInput={(event) => updateTempo(event.currentTarget.value)} /></label>
        </div>
        <label class="ocsb-audio-prompt-field"><span>Prompt</span><textarea ref={promptTextareaEl} value={settingsDraft().prompt || ""} onInput={(event) => updateSettingsDraft({ prompt: event.currentTarget.value })} /></label>
        <Show when={(audioSettings().topCandidates || []).length}>
          <div class="ocsb-audio-recommendations">
            <span>Recommended</span>
            <div><For each={audioSettings().topCandidates}>{(item) => <button class={recommendationActive(item) ? "is-active" : ""} type="button" onClick={() => applyRecommendation(item)}>{item.label || item.voice_id || item.voice || item.model}<Show when={recommendationScore(item)}> · {Number(recommendationScore(item)).toFixed(2)}</Show></button>}</For></div>
          </div>
        </Show>
        <button class="ocsb-apply-btn" type="button" disabled={!props.buildGSecondsPerChar()} onClick={props.refreshDialogueTimingsOnly}>Refresh Dialogue Durations</button>
        <div class="ocsb-audio-popover-actions">
          <button type="button" onClick={() => props.setTimingMenuOpen(false)}>Cancel</button>
          <button type="button" onClick={() => void saveSettings()}>Save to Task</button>
        </div>
      </div>
    </Show>
  </div>;
}

export function SourceMeta(props) {
  return <p class="ocsb-source-meta">
    <span>Task #{props.task()?.id}</span>
    <button
      class="ocsb-meta-continue"
      type="button"
      title={`Refresh Task #${props.task()?.id || ""} to Phase 2`}
      aria-label={`Refresh Task #${props.task()?.id || ""} to Phase 2`}
      disabled={Boolean(props.busy())}
      onClick={() => void props.refreshTaskToPhase2()}
    >
      <ArrowIcon />
    </button>
    <span>Session #{props.task()?.session_id}</span>
    <Show
      when={props.meta().source_type !== "blank_upload"}
      fallback={<span>Blank Upload</span>}
    >
      <span>Copied from Rebuild Task #{props.meta().copied_from_rebuild_task_id}</span>
      <button
        class="ocsb-meta-continue source-task"
        type="button"
        title={`Open Rebuild Task #${props.meta().copied_from_rebuild_task_id || ""}`}
        aria-label={`Open Rebuild Task #${props.meta().copied_from_rebuild_task_id || ""}`}
        onClick={() => props.openCopiedRebuildTask?.(props.meta().copied_from_rebuild_task_id)}
      >
        <ArrowIcon />
      </button>
    </Show>
  </p>;
}

export function SaveButton(props) {
  return <button class="ocsb-toolbar-btn icon-only" type="button" title={props.busy() === "save" ? "Saving" : "Save"} disabled={!props.dirty() || props.busy() === "save"} onClick={() => void props.saveStoryboard()}><SaveIcon /></button>;
}
