import { For, Show, createEffect, createSignal } from "solid-js";
import { positiveNumber } from "../kouboStoryboardModel.js";
import { MicIcon, RefreshIcon, SaveIcon, XIcon } from "../kouboStoryboardIcons.jsx";

const TTS_TEMPLATE_OPTIONS = [
  {
    id: "single-basic",
    label: "单说话人基础朗读",
    prompt: "请用普通话自然朗读当前 Scene / Dialogue 文本，声音要清晰、稳定、有真实手机自拍视频口播感。只朗读正文，不要读说明文字。",
  },
  {
    id: "short-video-natural",
    label: "短视频自然口播",
    prompt: "请用普通话生成自然短视频口播。\n\n声音要求：自然、清晰、像手机自拍视频口播；避免硬广、夸张直播腔或机械朗读。\n节奏：中速平稳，重点词轻微强调。\n朗读规则：只朗读当前文本，不要读说明文字。",
  },
  {
    id: "steady-explainer",
    label: "稳定讲解",
    prompt: "请用普通话稳定讲解当前文本。语气可信、耐心、信息清楚，句尾收稳，不要广告腔。严格按当前文本朗读，不改词、不加词。",
  },
  {
    id: "expressive-tags",
    label: "情绪/停顿标签",
    prompt: "请用普通话朗读当前文本，并自然执行文本中的情绪、停顿、轻重音或括号提示。不要把说明性标签当正文读出。",
  },
];

export default function KouboTimingMenu(props) {
  const isAdmin = () => Boolean(props.roleAccess?.isAdmin);
  const model = () => props.timingModel() || {};
  const [settingsDraft, setSettingsDraft] = createSignal({});
  const audioSettings = () => props.audioSettings?.() || {};
  const displayVoice = () => audioSettings().label || audioSettings().voiceId || model().voice || "-";
  let providerSelectEl = null;
  let modelSelectEl = null;
  let voiceSelectEl = null;
  let templateSelectEl = null;
  let tempoInputEl = null;
  let promptTextareaEl = null;
  const validTemplateId = (value) => {
    const templateId = String(value || "").trim();
    return TTS_TEMPLATE_OPTIONS.some((item) => item.id === templateId) ? templateId : "";
  };
  const templatePrompt = (templateId) => TTS_TEMPLATE_OPTIONS.find((item) => item.id === templateId)?.prompt || "";
  const selectedTemplateId = () => validTemplateId(settingsDraft().scenarioId || settingsDraft().scenario_id || "");
  const candidateVoice = (item) => String(item?.voice_id || item?.voice || "").trim();
  const candidateLabel = (item) => {
    const voice = candidateVoice(item);
    const label = String(item?.label || item?.voice_label || voice).trim();
    const model = [item?.provider, item?.model].map((value) => String(value || "").trim()).filter(Boolean).join("/");
    return item?.voice_source === "cloud_clone" && model ? `${label} · ${model}` : label;
  };
  const candidateMatchesModel = (item, provider, model) => {
    const itemProvider = String(item?.provider || "").trim();
    const itemModel = String(item?.model || "").trim();
    return (!provider || !itemProvider || itemProvider === provider) && (!model || !itemModel || itemModel === model);
  };
  createEffect(() => {
    if (!props.timingMenuOpen()) setSettingsDraft({});
  });

  const resetSettingsDraft = () => {
    const next = { ...audioSettings() };
    const scenarioId = validTemplateId(next.scenarioId || next.scenario_id || "");
    setSettingsDraft({ ...next, recommendedTempo: next.tempo, scenarioId, scenario_id: scenarioId });
  };
  const updateSettingsDraft = (patch) => setSettingsDraft((draft) => ({ ...draft, ...patch }));
  const providerOptions = () => {
    const options = [...(props.ttsProviderOptions?.() || [])];
    for (const item of audioSettings().topCandidates || []) {
      const provider = String(item?.provider || "").trim();
      if (!provider || options.some((entry) => entry.provider === provider)) continue;
      options.push({ provider, providerLabel: provider });
    }
    return options;
  };
  const providerModels = () => {
    const provider = String(settingsDraft().provider || "").trim();
    const models = [...(props.ttsModelsForProvider?.(provider) || [])];
    for (const item of audioSettings().topCandidates || []) {
      const model = String(item?.model || "").trim();
      if (!model || String(item?.provider || "").trim() !== provider || models.some((entry) => entry.model === model)) continue;
      models.push({ model, label: model, voices: [] });
    }
    return models;
  };
  const modelVoices = () => {
    const provider = String(settingsDraft().provider || "").trim();
    const modelValue = String(settingsDraft().model || "").trim();
    const configured = (props.ttsVoicesForModel?.(provider, modelValue) || []).map((item) => ({ ...item, voice: item.voice || item.voice_id }));
    const seen = new Set();
    const voices = [...configured];
    for (const item of voices) {
      const voice = item?.voice_id || item?.voice || "";
      if (voice) seen.add(voice);
    }
    for (const item of audioSettings().topCandidates || []) {
      const voice = candidateVoice(item);
      if (!voice || seen.has(voice)) continue;
      if (configured.length && item?.voice_source !== "cloud_clone" && !candidateMatchesModel(item, provider, modelValue)) continue;
      seen.add(voice);
      voices.push({ voice_id: voice, voice, label: candidateLabel(item), candidate_id: item?.candidate_id || "", provider: item?.provider || "", model: item?.model || "", voice_source: item?.voice_source || "" });
    }
    const current = settingsDraft().voiceId || settingsDraft().voice_id || settingsDraft().voice || "";
    if (current && !seen.has(current)) voices.unshift({ voice_id: current, voice: current, label: settingsDraft().label || current });
    return voices;
  };
  const recommendationTempo = (item) => {
    const fitMeta = item?.fit_meta && typeof item.fit_meta === "object" ? item.fit_meta : {};
    const explicitTempo = positiveNumber(item?.tempo ?? item?.speed_factor ?? fitMeta.tempo ?? fitMeta.speed_factor);
    if (explicitTempo) return explicitTempo;
    if (item?.voice_source === "cloud_clone") return 1;
    const score = positiveNumber(item?.score);
    return score && score <= 3 ? score : null;
  };
  const recommendationScore = (item) => positiveNumber(item?.score);
  const candidateForValues = (values) => {
    const candidates = audioSettings().topCandidates || [];
    const valueProvider = String(values?.provider || "").trim().toLowerCase();
    const valueModel = String(values?.model || "").trim().toLowerCase();
    const valueVoiceId = String(values?.voiceId || values?.voice_id || values?.voice || "").trim().toLowerCase();
    const matchedInModel = candidates.find((item) => {
      const itemProvider = String(item?.provider || "").trim().toLowerCase();
      const itemModel = String(item?.model || "").trim().toLowerCase();
      const itemVoices = [item?.voice_id, item?.voice, item?.label, item?.voice_label].map((value) => String(value || "").trim().toLowerCase()).filter(Boolean);
      if (valueProvider && itemProvider && valueProvider !== itemProvider) return false;
      if (valueModel && itemModel && valueModel !== itemModel) return false;
      return valueVoiceId && itemVoices.some((value) => value === valueVoiceId || value.includes(valueVoiceId) || valueVoiceId.includes(value));
    });
    if (matchedInModel) return matchedInModel;
    return candidates.find((item) => {
      const itemVoices = [item?.voice_id, item?.voice, item?.label, item?.voice_label].map((value) => String(value || "").trim().toLowerCase()).filter(Boolean);
      return valueVoiceId && itemVoices.some((value) => value === valueVoiceId || value.includes(valueVoiceId) || valueVoiceId.includes(value));
    }) || null;
  };
  const voiceMatches = (draft, item) => {
    const draftCandidateId = String(draft.candidateId || draft.candidate_id || "").trim();
    const itemCandidateId = String(item?.candidate_id || "").trim();
    if (draftCandidateId && itemCandidateId && draftCandidateId === itemCandidateId) return true;
    const draftValues = [draft.voiceId, draft.voice, draft.label].map((value) => String(value || "").trim().toLowerCase()).filter(Boolean);
    const itemValues = [item?.voice_id, item?.voice, item?.label, item?.voice_label].map((value) => String(value || "").trim().toLowerCase()).filter(Boolean);
    return draftValues.some((draftValue) => itemValues.some((itemValue) => draftValue === itemValue || draftValue.includes(itemValue) || itemValue.includes(draftValue)));
  };
  const recommendationActive = (item) => {
    return voiceMatches(settingsDraft(), item);
  };
  const updateTempo = (value) => {
    const next = String(value || "").replace("。", ".").replace(",", ".");
    if (/^\d*(?:\.\d*)?$/.test(next)) updateSettingsDraft({ tempo: next, recommendedTempo: next });
  };
  const updateRecommendedTempo = (value) => {
    const next = String(value || "").replace("。", ".").replace(",", ".");
    if (/^\d*(?:\.\d*)?$/.test(next)) {
      updateSettingsDraft({ recommendedTempo: next, tempo: next });
      if (tempoInputEl) tempoInputEl.value = next;
    }
  };
  const tempoValueForSave = () => {
    const value = String(settingsDraft().recommendedTempo ?? tempoInputEl?.value ?? settingsDraft().tempo ?? "").replace("。", ".").replace(",", ".").trim();
    return positiveNumber(value) ? value : settingsDraft().tempo || "1";
  };
  const nonEmptyFieldValue = (element, ...fallbacks) => {
    for (const value of [element?.value, ...fallbacks]) {
      const next = String(value ?? "").trim();
      if (next) return next;
    }
    return "";
  };
  const updateProvider = (value) => updateSettingsDraft({ provider: value, model: "", voiceId: "", label: "", candidateId: "", candidate_id: "" });
  const updateModel = (value) => updateSettingsDraft({ model: value, voiceId: "", label: "", candidateId: "", candidate_id: "" });
  const updateVoice = (value) => {
    const matched = candidateForValues({
      provider: providerSelectEl?.value ?? settingsDraft().provider,
      model: modelSelectEl?.value ?? settingsDraft().model,
      voiceId: value,
    });
    if (matched) {
      applyRecommendation(matched);
      return;
    }
    updateSettingsDraft({ voiceId: value, label: "", candidateId: "", candidate_id: "" });
  };
  const updateTemplate = (value) => {
    const scenarioId = validTemplateId(value);
    if (!scenarioId) {
      updateSettingsDraft({ scenarioId: "", scenario_id: "" });
      return;
    }
    const prompt = templatePrompt(scenarioId);
    updateSettingsDraft({ scenarioId, scenario_id: scenarioId, prompt });
    if (promptTextareaEl) promptTextareaEl.value = prompt;
  };
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
    const scenarioId = validTemplateId(item.scenario_id || item.scenarioId || "");
    const prompt = item.prompt_template || item.instructions || item.prompt || settingsDraft().prompt || "";
    updateSettingsDraft({
      provider: item.provider || settingsDraft().provider || "",
      model: item.model || settingsDraft().model || "",
      voiceId: item.voice_id || item.voice || settingsDraft().voiceId || "",
      prompt,
      tempo: nextTempo,
      recommendedTempo: nextTempo,
      label: item.label || item.voice_label || item.voice_id || item.voice || "",
      candidateId: item.candidate_id || "",
      scenarioId,
      scenario_id: scenarioId,
    });
    if (providerSelectEl) providerSelectEl.value = item.provider || settingsDraft().provider || "";
    if (modelSelectEl) modelSelectEl.value = item.model || settingsDraft().model || "";
    if (voiceSelectEl) voiceSelectEl.value = item.voice_id || item.voice || settingsDraft().voiceId || "";
    if (templateSelectEl) templateSelectEl.value = scenarioId;
    if (tempoInputEl) tempoInputEl.value = String(nextTempo);
    if (promptTextareaEl) promptTextareaEl.value = prompt;
  };
  const saveSettings = async () => {
    const scenarioId = validTemplateId(templateSelectEl?.value ?? settingsDraft().scenarioId ?? settingsDraft().scenario_id ?? "");
    const next = {
      ...settingsDraft(),
      voiceId: nonEmptyFieldValue(voiceSelectEl, settingsDraft().voiceId, settingsDraft().voice_id, settingsDraft().voice),
      tempo: tempoValueForSave(),
      prompt: promptTextareaEl?.value ?? settingsDraft().prompt,
      scenarioId,
      scenario_id: scenarioId,
    };
    if (isAdmin()) {
      next.provider = nonEmptyFieldValue(providerSelectEl, settingsDraft().provider);
      next.model = nonEmptyFieldValue(modelSelectEl, settingsDraft().model);
    }
    await props.saveAudioSettings?.(next);
    props.setTimingMenuOpen(false);
  };

  return <div class="kbsp-menu-wrap">
    <button class="kbsp-toolbar-btn icon-only" type="button" title="Builder-G Voice Timing" onClick={() => void openTimingMenu()}><MicIcon /></button>
    <Show when={props.timingMenuOpen()}>
      <div class="kbsp-menu-panel timing">
        <div class="kbsp-timing-panel-head">
          <strong>Builder-G Timing</strong>
          <div class="kbsp-timing-actions">
            <button type="button" title="Refresh Dialogue Durations" aria-label="Refresh Dialogue Durations" disabled={!props.buildGSecondsPerChar()} onClick={props.refreshDialogueTimingsOnly}><RefreshIcon /></button>
            <button type="button" title="Save to Task" aria-label="Save to Task" onClick={() => void saveSettings()}><SaveIcon /></button>
            <button type="button" title="Cancel" aria-label="Cancel" onClick={() => props.setTimingMenuOpen(false)}><XIcon /></button>
          </div>
        </div>
        <div class="kbsp-timing-grid">
          <div><span>Voice</span><b>{displayVoice()}</b></div>
          <div><span>Duration</span><b>{Number(model().build_g_duration || 0).toFixed(2)}s</b></div>
          <div><span>Chars</span><b>{model().build_g_chars || 0}</b></div>
          <div><span>Rate</span><b>{Number(model().sec_per_char || 0).toFixed(3)}s/字</b></div>
        </div>
        <p>{String(model().build_g_text || "").slice(0, 100) || "No Builder-G text found."}</p>
        <div class="kbsp-audio-form-grid">
          <Show when={isAdmin()}>
            <label><span>Provider</span><select ref={providerSelectEl} value={settingsDraft().provider || ""} onInput={(event) => updateProvider(event.currentTarget.value)} onChange={(event) => updateProvider(event.currentTarget.value)}><option value="">-</option><For each={providerOptions()}>{(item) => <option value={item.provider}>{item.providerLabel || item.provider}</option>}</For></select></label>
            <label><span>Model</span><select ref={modelSelectEl} value={settingsDraft().model || ""} onInput={(event) => updateModel(event.currentTarget.value)} onChange={(event) => updateModel(event.currentTarget.value)}><option value="">-</option><For each={providerModels()}>{(item) => <option value={item.model}>{item.label || item.model}</option>}</For></select></label>
          </Show>
          <label><span>Voice</span><select ref={voiceSelectEl} value={settingsDraft().voiceId || ""} onInput={(event) => updateVoice(event.currentTarget.value)} onChange={(event) => updateVoice(event.currentTarget.value)}><option value="">-</option><For each={modelVoices()}>{(item) => <option value={item.voice_id || item.voice}>{item.label || item.voice_id || item.voice}</option>}</For></select></label>
          <label><span>TTS Tempo</span><input type="text" inputmode="decimal" pattern="[0-9]*[.]?[0-9]*" ref={tempoInputEl} value={String(settingsDraft().tempo ?? "")} onInput={(event) => updateTempo(event.currentTarget.value)} /></label>
          <label><span>TTS Template</span><select ref={templateSelectEl} value={selectedTemplateId()} onInput={(event) => updateTemplate(event.currentTarget.value)} onChange={(event) => updateTemplate(event.currentTarget.value)}><option value="">自定义</option><For each={TTS_TEMPLATE_OPTIONS}>{(item) => <option value={item.id}>{item.label}</option>}</For></select></label>
        </div>
        <label class="kbsp-audio-prompt-field"><span>Prompt</span><textarea ref={promptTextareaEl} value={settingsDraft().prompt || ""} onInput={(event) => updateSettingsDraft({ prompt: event.currentTarget.value })} /></label>
        <Show when={(audioSettings().topCandidates || []).length}>
          <div class="kbsp-audio-recommendations">
            <span>Recommended</span>
            <div><For each={audioSettings().topCandidates}>{(item) => <button class={recommendationActive(item) ? "is-active" : ""} type="button" onClick={() => applyRecommendation(item)}>{item.label || item.voice_label || item.voice_id || item.voice || item.model}<Show when={recommendationScore(item)}> · {Number(recommendationScore(item)).toFixed(2)}</Show></button>}</For>
              <label class="kbsp-recommended-tempo"><span>Tempo</span><input type="text" inputmode="decimal" pattern="[0-9]*[.]?[0-9]*" value={String(settingsDraft().recommendedTempo ?? settingsDraft().tempo ?? "")} onInput={(event) => updateRecommendedTempo(event.currentTarget.value)} /></label>
            </div>
          </div>
        </Show>
      </div>
    </Show>
  </div>;
}
