import { copy, dialogueCharCount, dialogueText, positiveNumber, renumberPlan, spokenCharCount } from "./kouboStoryboardModel.js";

export function createKouboStoryboardTtsController(deps) {
  const {
    kbApi,
    plan,
    setPlan,
    task,
    meta,
    setState,
    setDirty,
    timingModel,
    setTimingModel,
    timingSecondsPerChar,
    ttsModelConfig,
    setTTSModelConfig,
    runAction,
    sessionId,
    sceneAudioState,
    setSceneAudioState,
    updatePlan,
    ensureSceneWorkingAssets,
    fixedShotSeconds,
    fixedSceneSeconds,
    setSelectedShotIndex,
    setSelectedDialogueId,
    setScope,
    setFixedMenuOpen,
    setGroupingDirty,
    roleAccess,
  } = deps;
  const isAdmin = () => Boolean(roleAccess?.isAdmin);
  const TTS_REQUEST_TIMEOUT_MS = 70000;
  const activeTtsRequests = new Map();
  let ttsRequestSerial = 0;

  function abortErrorForTts(entry, err) {
    if (entry?.timedOut) return new Error("TTS 试听请求超过 70 秒仍未返回，已自动停止。请换一个声音或稍后重试。");
    if (entry?.cancelReason === "settings_changed") return new Error("TTS 试听已因音色设置变更而停止。");
    if (entry?.controller?.signal?.aborted || err?.name === "AbortError") return new Error("TTS 试听已停止。");
    return null;
  }

  function isBenignTtsStop(err) {
    const message = err instanceof Error ? err.message : String(err || "");
    return /TTS 试听已停止|TTS 试听已因音色设置变更而停止/.test(message);
  }

  function beginTtsRequest(key, stateKey) {
    cancelTtsRequest(key, "replaced");
    const controller = new AbortController();
    const entry = {
      key,
      stateKey,
      requestId: ++ttsRequestSerial,
      controller,
      cancelReason: "",
      timedOut: false,
      timeoutId: null,
    };
    entry.timeoutId = globalThis.setTimeout?.(() => {
      entry.timedOut = true;
      controller.abort();
    }, TTS_REQUEST_TIMEOUT_MS);
    activeTtsRequests.set(key, entry);
    return entry;
  }

  function isActiveTtsRequest(entry) {
    return Boolean(entry && activeTtsRequests.get(entry.key)?.requestId === entry.requestId && !entry.controller.signal.aborted);
  }

  function finishTtsRequest(entry) {
    if (!entry) return;
    if (entry.timeoutId) globalThis.clearTimeout?.(entry.timeoutId);
    if (activeTtsRequests.get(entry.key)?.requestId === entry.requestId) activeTtsRequests.delete(entry.key);
  }

  function cancelTtsRequest(key, reason = "cancelled") {
    const entry = activeTtsRequests.get(key);
    if (!entry) return;
    entry.cancelReason = reason;
    if (entry.timeoutId) globalThis.clearTimeout?.(entry.timeoutId);
    entry.controller.abort();
    activeTtsRequests.delete(key);
    if (entry.stateKey) updateSceneAudioState(entry.stateKey, { phase: "idle", error: "" });
  }

  function cancelActiveTtsRequests(reason = "cancelled") {
    for (const key of Array.from(activeTtsRequests.keys())) cancelTtsRequest(key, reason);
  }

  function buildGSecondsPerChar() {
    return Number(timingModel()?.sec_per_char || timingModel()?.avg_sec_per_char || 0);
  }

  function providerTTSModel(provider) {
    const providerConfig = (ttsModelConfig()?.providers || []).find((item) => item.provider === provider);
    if (!providerConfig) return null;
    const models = providerConfig.models || [];
    const configured = models.find((item) => item.model === providerConfig.model);
    const model = configured || models.find((item) => item.enabled !== false) || models[0];
    if (!model) return null;
    const selectedVoice = providerConfig.selected_voice_by_model?.[model.model] || model.voices?.[0]?.voice_id || "";
    return { provider: providerConfig.provider, providerLabel: providerConfig.provider_label || providerConfig.provider, model: model.model, models, voices: model.voices || [], voiceId: selectedVoice, enabled: providerConfig.enabled, hasApiKey: providerConfig.has_api_key };
  }

  function defaultTTSModel() {
    const config = ttsModelConfig() || {};
    const providers = Array.isArray(config.providers) ? config.providers : [];
    const active = providers.find((item) => item.provider === config.active_provider);
    const ordered = active ? [active, ...providers.filter((item) => item !== active)] : providers;
    for (const provider of ordered) {
      if (provider.enabled === false) continue;
      const selected = providerTTSModel(provider.provider);
      if (selected) return selected;
    }
    return null;
  }

  async function ensureTTSModelConfig() {
    if (ttsModelConfig()) return ttsModelConfig();
    if (!isAdmin()) {
      const emptyConfig = { providers: [] };
      setTTSModelConfig(emptyConfig);
      return emptyConfig;
    }
    const config = await kbApi.ttsModelConfig();
    setTTSModelConfig(config);
    return config;
  }

  function defaultTTSPrompt(provider, text = "") {
    const base = "自然中文短视频旁白，吐字清晰，节奏贴合画面，不夸张，不医疗化。严格按照提供文本朗读，不改词、不加词。";
    if (provider === "qwen") return `${base} 用自然中文人声，口语化、亲和、有轻微情绪推进。\n\n朗读文本：${text}`;
    if (provider === "xai") return `${base} Voice should sound conversational, warm, and clear.\n\nText: ${text}`;
    return `${base} Bright, clear, conversational delivery.\n\n{text}`;
  }

  function promptForRecommendedVoice(item, targetDuration = null) {
    const durationText = targetDuration ? `目标总时长 ${Number(targetDuration).toFixed(2)} 秒。` : "";
    const style = item?.style ? `声音特质：${item.style}。` : "";
    const gender = item?.candidate_profile?.gender || item?.metadata?.gender || "";
    const genderText = gender === "female" ? "女声" : gender === "male" ? "男声" : "自然人声";
    return `中文普通话${genderText}，清晰自然，适合商业短视频旁白。${durationText}${style}语气有一点吐槽感和表现力，节奏利落，情绪轻微上扬。严格按照提供文本朗读，不改词、不加词。`;
  }

  function ttsProviderOptions() {
    return (ttsModelConfig()?.providers || []).map((item) => ({ provider: item.provider, providerLabel: item.provider_label || item.provider }));
  }

  function ttsModelsForProvider(provider) {
    return ((ttsModelConfig()?.providers || []).find((item) => item.provider === provider)?.models || []);
  }

  function ttsVoicesForModel(provider, model) {
    return ttsModelsForProvider(provider).find((item) => item.model === model)?.voices || [];
  }

  function compactText(...values) {
    for (const value of values) {
      const next = String(value ?? "").trim();
      if (next) return next;
    }
    return "";
  }

  function voiceOptionValues(item) {
    return [item?.voice_id, item?.voice, item?.label, item?.voice_label].map((value) => String(value || "").trim()).filter(Boolean);
  }

  function voiceTextMatches(candidateValue, requestedValue) {
    const candidate = String(candidateValue || "").trim().toLowerCase();
    const requested = String(requestedValue || "").trim().toLowerCase();
    return Boolean(candidate && requested && (candidate === requested || candidate.includes(requested) || requested.includes(candidate)));
  }

  function voiceIdFromOption(item, fallback = "") {
    return compactText(item?.voice_id, item?.voice, fallback);
  }

  function resolveVoiceId(provider, model, value, selection = null) {
    const requested = compactText(value);
    if (!requested) return "";
    const candidates = Array.isArray(selection?.top_candidates) ? selection.top_candidates : Array.isArray(selection?.recommendations) ? selection.recommendations : [];
    const configured = ttsVoicesForModel(provider, model);
    const options = [
      ...configured,
      ...candidates.filter((item) => {
        const itemProvider = String(item?.provider || "").trim();
        const itemModel = String(item?.model || "").trim();
        return (!provider || !itemProvider || itemProvider === provider) && (!model || !itemModel || itemModel === model);
      }),
    ];
    for (const item of options) {
      if (voiceOptionValues(item).some((candidate) => voiceTextMatches(candidate, requested))) return voiceIdFromOption(item, requested);
    }
    const displayPrefix = requested.split(/\s+-\s+/)[0]?.trim() || "";
    if (displayPrefix && displayPrefix !== requested) {
      for (const item of options) {
        if (voiceOptionValues(item).some((candidate) => voiceTextMatches(candidate, displayPrefix))) return voiceIdFromOption(item, displayPrefix);
      }
      if (/^[A-Za-z][A-Za-z0-9 _-]{0,80}$/.test(displayPrefix)) return displayPrefix;
    }
    return requested;
  }

  function selectionVoiceId(selection, provider, model, fallbackVoiceId = "") {
    const direct = resolveVoiceId(provider, model, compactText(selection?.voice_id, selection?.voice), selection);
    if (direct) return direct;
    const label = resolveVoiceId(provider, model, compactText(selection?.label, selection?.voice_label), selection);
    return label || fallbackVoiceId || "";
  }

  function tempoFromCandidate(item) {
    if (!item || typeof item !== "object") return null;
    const fitMeta = item.fit_meta && typeof item.fit_meta === "object" ? item.fit_meta : {};
    const explicitTempo = positiveNumber(item.tempo ?? item.speed_factor ?? fitMeta.tempo ?? fitMeta.speed_factor);
    if (explicitTempo) return explicitTempo;
    if (item.voice_source === "cloud_clone") return 1;
    const score = positiveNumber(item.score);
    return score && score <= 3 ? score : null;
  }

  function voiceMatchesCandidate(selection, item) {
    const selectedCandidateId = String(selection?.candidate_id || "").trim();
    const candidateId = String(item?.candidate_id || "").trim();
    if (selectedCandidateId && candidateId && selectedCandidateId === candidateId) return true;
    const selectedValues = [selection?.voice_id, selection?.voice, selection?.label].map((value) => String(value || "").trim().toLowerCase()).filter(Boolean);
    const candidateValues = [item?.voice_id, item?.voice, item?.label, item?.voice_label].map((value) => String(value || "").trim().toLowerCase()).filter(Boolean);
    return selectedValues.some((selected) => candidateValues.some((candidate) => selected === candidate || selected.includes(candidate) || candidate.includes(selected)));
  }

  function normalizeTtsCandidate(item) {
    if (!item || typeof item !== "object") return null;
    const voice = String(item.voice_id || item.voice || item.voice_label || "").trim();
    const label = String(item.label || item.voice_label || voice || "").trim();
    return {
      ...item,
      provider: String(item.provider || "").trim(),
      model: String(item.model || "").trim(),
      voice_id: voice,
      voice,
      label,
      candidate_id: String(item.candidate_id || "").trim(),
      prompt_template: item.prompt_template || item.instructions || item.prompt || "",
    };
  }

  function candidateMergeKey(item) {
    const voice = String(item?.voice_id || item?.voice || item?.label || item?.voice_label || "").trim().toLowerCase();
    const candidateId = String(item?.candidate_id || "").trim().toLowerCase();
    return voice ? `voice|${voice}` : candidateId ? `candidate|${candidateId}` : "";
  }

  function mergeTtsCandidates(...groups) {
    const indexByKey = new Map();
    const merged = [];
    for (const group of groups) {
      for (const raw of Array.isArray(group) ? group : []) {
        const item = normalizeTtsCandidate(raw);
        if (!item) continue;
        const key = candidateMergeKey(item);
        const existingIndex = key ? indexByKey.get(key) : undefined;
        if (existingIndex !== undefined) {
          const existingScore = positiveNumber(merged[existingIndex]?.score ?? merged[existingIndex]?.match_score) || 0;
          const itemScore = positiveNumber(item.score ?? item.match_score) || 0;
          if (itemScore > existingScore) merged[existingIndex] = item;
          continue;
        }
        if (key) indexByKey.set(key, merged.length);
        merged.push(item);
      }
    }
    return merged;
  }

  function objectValue(value) {
    if (value && typeof value === "object" && !Array.isArray(value)) return value;
    if (typeof value !== "string" || !value.trim()) return null;
    try {
      const parsed = JSON.parse(value);
      return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : null;
    } catch (_err) {
      return null;
    }
  }

  function firstObject(...values) {
    return values.map(objectValue).find((value) => value && Object.keys(value).length) || {};
  }

  function talkingHeadContext() {
    const taskObj = task?.() || {};
    const metaObj = meta?.() || {};
    const planObj = plan?.() || {};
    const quickConfig = firstObject(
      taskObj.storyboard_quick_config,
      taskObj.storyboardQuickConfig,
      taskObj.storyboard_quick_config_json,
      taskObj.storyboardQuickConfigJson,
      metaObj.storyboard_quick_config,
      metaObj.storyboardQuickConfig,
      planObj.storyboard_quick_config,
      planObj.storyboardQuickConfig,
    );
    const talkingHead = firstObject(
      taskObj.talking_head,
      taskObj.talkingHead,
      metaObj.talking_head,
      metaObj.talkingHead,
      quickConfig.talking_head,
      quickConfig.talkingHead,
      planObj.talking_head,
      planObj.talkingHead,
    );
    return { taskObj, metaObj, planObj, quickConfig, talkingHead };
  }

  function isTalkingHeadWorkflow() {
    const { taskObj, metaObj, quickConfig, talkingHead } = talkingHeadContext();
    const profile = firstObject(quickConfig.workflow_profile, quickConfig.workflowProfile, taskObj.workflow_profile, taskObj.workflowProfile, metaObj.workflow_profile, metaObj.workflowProfile);
    const values = [
      taskObj.create_mode,
      taskObj.createMode,
      taskObj.workflow_id,
      taskObj.workflowId,
      taskObj.profile_id,
      taskObj.profileId,
      metaObj.workflow_mode,
      metaObj.workflow_id,
      metaObj.profile_id,
      quickConfig.create_mode,
      quickConfig.createMode,
      quickConfig.workflow_id,
      quickConfig.workflowId,
      quickConfig.profile_id,
      quickConfig.profileId,
      profile.workflow_id,
      profile.workflowId,
      profile.profile_id,
      profile.profileId,
    ].map((value) => String(value || "").trim());
    return Boolean(Object.keys(talkingHead).length) || values.includes("person_talking_head") || values.includes("person_talking_head_v1");
  }

  function talkingHeadSelection() {
    const { taskObj, metaObj, quickConfig, talkingHead } = talkingHeadContext();
    const voiceTiming = firstObject(talkingHead.voice_timing, talkingHead.voiceTiming, quickConfig.voice_timing, quickConfig.voiceTiming);
    const voiceId = compactText(voiceTiming.voice_id, voiceTiming.voice, talkingHead.voice_id, talkingHead.voice, taskObj.voice_id, metaObj.voice_id);
    if (!voiceId) return null;
    const provider = compactText(voiceTiming.provider, voiceTiming.voice_provider, talkingHead.provider, taskObj.voice_provider, metaObj.voice_provider);
    const model = compactText(voiceTiming.model, voiceTiming.voice_model, talkingHead.model, talkingHead.voice_model);
    const label = compactText(voiceTiming.voice_label, voiceTiming.label, talkingHead.voice_label, voiceId);
    const tempo = positiveNumber(voiceTiming.tempo ?? talkingHead.tempo ?? quickConfig.tempo ?? taskObj.tempo ?? metaObj.tempo) || 1;
    const candidate = {
      provider,
      model,
      voice_id: voiceId,
      voice: voiceId,
      label,
      voice_label: label,
      voice_source: "cloud_clone",
      candidate_id: compactText(voiceTiming.candidate_id, voiceTiming.candidateId, voiceId),
      tempo,
      prompt: compactText(voiceTiming.prompt, voiceTiming.prompt_template, "自然中文口播，清晰稳定，严格按当前文本朗读，不改词、不加词。"),
      prompt_template: compactText(voiceTiming.prompt_template, voiceTiming.prompt, "自然中文口播，清晰稳定，严格按当前文本朗读，不改词、不加词。"),
      source: "talking_head_voice_selection",
    };
    return {
      provider,
      model,
      voice_id: voiceId,
      voice: voiceId,
      voice_label: label,
      label,
      candidate_id: candidate.candidate_id,
      tempo,
      prompt: candidate.prompt,
      prompt_template: candidate.prompt_template,
      source: "talking_head_voice_selection",
      top_candidates: [candidate],
      recommendations: [candidate],
    };
  }

  function selectionMatchesTalkingHead(selection, talkingHead) {
    if (!selection || !talkingHead) return false;
    const sameVoice = voiceTextMatches(compactText(selection.voice_id, selection.voice, selection.label), compactText(talkingHead.voice_id, talkingHead.voice, talkingHead.label));
    const selectionProvider = String(selection.provider || "").trim();
    const talkingHeadProvider = String(talkingHead.provider || "").trim();
    const selectionModel = String(selection.model || "").trim();
    const talkingHeadModel = String(talkingHead.model || "").trim();
    const sameProvider = !selectionProvider || !talkingHeadProvider || selectionProvider === talkingHeadProvider;
    const sameModel = !selectionModel || !talkingHeadModel || selectionModel === talkingHeadModel;
    return sameVoice && sameProvider && sameModel;
  }

  function tempoFromSelection(selection) {
    if (!selection || typeof selection !== "object") return null;
    const candidates = Array.isArray(selection.top_candidates) ? selection.top_candidates : Array.isArray(selection.recommendations) ? selection.recommendations : [];
    const matched = candidates.find((item) => voiceMatchesCandidate(selection, item));
    const candidateTempo = matched ? tempoFromCandidate(matched) : null;
    const directTempo = positiveNumber(selection.tempo ?? selection.speed_factor ?? selection.speed ?? selection.rate);
    const fitMeta = selection.fit_meta && typeof selection.fit_meta === "object" ? selection.fit_meta : {};
    const fitTempo = positiveNumber(fitMeta.tempo ?? fitMeta.speed_factor);
    if (selection.source === "storyboard_audio_settings" && directTempo) return directTempo;
    return candidateTempo || fitTempo || directTempo;
  }

  function storyboardAudioSelection() {
    const saved = plan()?.storyboard_tts_selection || null;
    const talkingHead = talkingHeadSelection();
    const builder = timingModel()?.selection ? {
      ...timingModel().selection,
      top_candidates: timingModel().top_candidates || [],
      recommendations: timingModel().top_candidates || [],
      source: "builder_g_timing",
    } : null;
    const useTalkingHeadDefault = isTalkingHeadWorkflow() && talkingHead && !selectionMatchesTalkingHead(saved, talkingHead);
    const base = useTalkingHeadDefault ? talkingHead : saved || talkingHead || builder;
    if (!base) return null;
    const builderCandidates = builder?.top_candidates || builder?.recommendations || [];
    const savedCandidates = Array.isArray(saved?.top_candidates) ? saved.top_candidates : Array.isArray(saved?.recommendations) ? saved.recommendations : [];
    const talkingHeadCandidates = talkingHead?.top_candidates || talkingHead?.recommendations || [];
    const mergedCandidates = mergeTtsCandidates(talkingHeadCandidates, builderCandidates, savedCandidates, base.top_candidates, base.recommendations);
    if (mergedCandidates.length) return { ...base, top_candidates: mergedCandidates, recommendations: mergedCandidates };
    return base;
  }

  function selectedCandidateForSelection(selection) {
    if (!selection || typeof selection !== "object") return null;
    const candidates = Array.isArray(selection.top_candidates) ? selection.top_candidates : Array.isArray(selection.recommendations) ? selection.recommendations : [];
    if (!candidates.length) return null;
    const labelProvider = String(selection.provider_label || selection.providerLabel || "").trim().toLowerCase();
    const labelModel = String(selection.model_label || selection.modelLabel || "").trim().toLowerCase();
    const labelVoice = String(selection.voice_label || "").trim().toLowerCase();
    if (labelProvider || labelModel || labelVoice) {
      const labelMatched = candidates.find((item) => {
        const itemProvider = String(item?.provider_label || item?.providerLabel || item?.provider || "").trim().toLowerCase();
        const itemModel = String(item?.model_label || item?.modelLabel || item?.model || "").trim().toLowerCase();
        const itemVoices = [item?.voice_label, item?.label, item?.voice_id, item?.voice].map((value) => String(value || "").trim().toLowerCase()).filter(Boolean);
        if (labelProvider && itemProvider && labelProvider !== itemProvider) return false;
        if (labelModel && itemModel && labelModel !== itemModel) return false;
        if (labelVoice && itemVoices.length && !itemVoices.some((value) => value === labelVoice || value.includes(labelVoice) || labelVoice.includes(value))) return false;
        return true;
      });
      if (labelMatched) return labelMatched;
    }
    const candidateId = String(selection.candidate_id || "").trim();
    if (candidateId) {
      const matched = candidates.find((item) => String(item?.candidate_id || "").trim() === candidateId);
      if (matched) return matched;
    }
    return candidates.find((item) => voiceMatchesCandidate(selection, item)) || null;
  }

  function audioSettings() {
    const selection = storyboardAudioSelection();
    const selectedCandidate = selectedCandidateForSelection(selection);
    const voiceSource = selectedCandidate?.voice_source || selection?.voice_source || "";
    const fallback = voiceSource === "cloud_clone" ? {} : (defaultTTSModel() || {});
    const provider = selection?.provider || selectedCandidate?.provider || fallback.provider || "";
    const model = selection?.model || selectedCandidate?.model || fallback.model || "";
    const selectedCandidateId = String(selectedCandidate?.candidate_id || "").trim();
    const selectionCandidateId = String(selection?.candidate_id || "").trim();
    const repairedVoiceId = selectedCandidateId && selectionCandidateId && selectedCandidateId !== selectionCandidateId ? compactText(selectedCandidate?.voice_id, selectedCandidate?.voice) : "";
    const voiceId = repairedVoiceId || selectionVoiceId(selection, provider, model, compactText(selectedCandidate?.voice_id, selectedCandidate?.voice, fallback.voiceId));
    return {
      provider,
      model,
      voiceId,
      candidateId: selectedCandidate?.candidate_id || selection?.candidate_id || "",
      voiceSource,
      label: repairedVoiceId ? selectedCandidate?.label || selectedCandidate?.voice_label || voiceId : selection?.label || selection?.voice_label || selectedCandidate?.label || selectedCandidate?.voice_label || voiceId,
      scenarioId: selection?.scenario_id || selection?.scenarioId || "",
      prompt: selection?.prompt || selection?.prompt_template || promptForRecommendedVoice(selectedCandidate || selection || {}, null) || defaultTTSPrompt(provider),
      tempo: tempoFromSelection(selection) || tempoFromCandidate(selectedCandidate) || 1,
      topCandidates: mergeTtsCandidates(selection?.top_candidates, selection?.recommendations),
    };
  }

  function candidateFromAudioValues(selection, values) {
    const candidates = Array.isArray(selection?.top_candidates) ? selection.top_candidates : Array.isArray(selection?.recommendations) ? selection.recommendations : [];
    const valueProvider = String(values?.provider || "").trim().toLowerCase();
    const valueModel = String(values?.model || "").trim().toLowerCase();
    const valueVoiceId = String(values?.voiceId || values?.voice_id || values?.voice || values?.label || "").trim().toLowerCase();
    const matchesValues = (item) => {
      if (!item) return false;
      const itemProvider = String(item?.provider || "").trim().toLowerCase();
      const itemModel = String(item?.model || "").trim().toLowerCase();
      const itemValues = [item?.voice_id, item?.voice, item?.label, item?.voice_label].map((value) => String(value || "").trim().toLowerCase()).filter(Boolean);
      if (valueProvider && itemProvider && valueProvider !== itemProvider) return false;
      if (valueModel && itemModel && valueModel !== itemModel) return false;
      if (valueVoiceId && itemValues.length && !itemValues.some((value) => value === valueVoiceId || value.includes(valueVoiceId) || valueVoiceId.includes(value))) return false;
      return true;
    };
    const candidateId = String(values?.candidateId || values?.candidate_id || selection?.candidate_id || "").trim();
    if (candidateId) {
      const matched = candidates.find((item) => String(item?.candidate_id || "").trim() === candidateId);
      if (matched && matchesValues(matched)) return matched;
    }
    if (!valueVoiceId) return null;
    return candidates.find((item) => matchesValues(item)) || null;
  }

  async function openAudioSettings() {
    await ensureTTSModelConfig();
  }

  async function saveAudioSettings(values) {
    cancelActiveTtsRequests("settings_changed");
    if (!plan()) return;
    const selection = storyboardAudioSelection() || {};
    const selectedCandidate = candidateFromAudioValues(selection, values);
    const provider = String(values?.provider || selection.provider || "").trim();
    const model = String(values?.model || selection.model || "").trim();
    const voiceId = String(values?.voiceId || values?.voice_id || values?.voice || "").trim();
    const prompt = String(values?.prompt || "").trim();
    const scenarioId = String(values?.scenarioId ?? values?.scenario_id ?? selection.scenario_id ?? "").trim();
    const candidateTempo = tempoFromCandidate(selectedCandidate);
    const valueTempo = positiveNumber(values?.tempo);
    const candidateScore = positiveNumber(selectedCandidate?.score);
    const tempoLooksLikeScore = valueTempo && candidateScore && Math.abs(valueTempo - candidateScore) < 0.0005;
    const tempo = (tempoLooksLikeScore && candidateTempo ? candidateTempo : valueTempo) || candidateTempo || 1;
    const preservedCandidates = mergeTtsCandidates(selection.top_candidates, selection.recommendations);
    const preservedSelection = {
      top_candidates: preservedCandidates,
      recommendations: preservedCandidates,
    };
    const selectedProvider = selectedCandidate?.provider || provider || selection.provider || "";
    const selectedModel = selectedCandidate?.model || model || selection.model || "";
    const selectedCandidateVoiceId = selectedCandidate
      ? resolveVoiceId(selectedProvider, selectedModel, compactText(selectedCandidate.voice_id, selectedCandidate.voice, selectedCandidate.label, selectedCandidate.voice_label), selection)
      : "";
    const resolvedVoiceId = compactText(
      selectedCandidateVoiceId,
      resolveVoiceId(selectedProvider, selectedModel, compactText(voiceId, values?.label), selection),
      resolveVoiceId(selectedProvider, selectedModel, compactText(selection.voice_id, selection.voice, selection.label), selection),
    );
    const nextSelection = {
      ...preservedSelection,
      ...(selectedCandidate ? {
        provider: selectedCandidate.provider || provider,
        provider_label: selectedCandidate.provider_label || selectedCandidate.providerLabel || selectedCandidate.provider || provider || "",
        model: selectedCandidate.model || model,
        model_label: selectedCandidate.model_label || selectedCandidate.modelLabel || selectedCandidate.model || model || "",
        voice_id: resolvedVoiceId,
        voice: selectedCandidate.voice || selectedCandidate.voice_id || resolvedVoiceId,
        voice_label: selectedCandidate.voice_label || selectedCandidate.label || selectedCandidate.voice_id || selectedCandidate.voice || resolvedVoiceId,
        label: selectedCandidate.label || selectedCandidate.voice_label || selectedCandidate.voice_id || selectedCandidate.voice || values?.label || resolvedVoiceId,
        score: selectedCandidate.score,
        audio: selectedCandidate.audio || selectedCandidate.output || selectedCandidate.fit_audio || selectedCandidate.sample_audio_path || "",
        fit_audio: selectedCandidate.fit_audio || selectedCandidate.output || selectedCandidate.audio || selectedCandidate.sample_audio_path || "",
        raw_audio: selectedCandidate.raw_audio || "",
        prompt_template: selectedCandidate.prompt_template || selectedCandidate.instructions || selectedCandidate.prompt || prompt,
        voice_source: selectedCandidate.voice_source || selection.voice_source || "",
        source_clone_provider: selectedCandidate.source_clone_provider || selection.source_clone_provider || "",
        fit_meta: selectedCandidate.fit_meta,
        raw_duration: selectedCandidate.raw_duration,
        fit_duration: selectedCandidate.fit_duration,
      } : {}),
      provider: isAdmin() ? selectedProvider : "",
      model: isAdmin() ? selectedModel : "",
      voice_id: resolvedVoiceId,
      voice: selectedCandidate?.voice || selectedCandidate?.voice_id || resolvedVoiceId,
      label: selectedCandidate?.label || selectedCandidate?.voice_label || values?.label || resolvedVoiceId,
      candidate_id: selectedCandidate?.candidate_id || values?.candidateId || values?.candidate_id || "",
      prompt,
      prompt_template: prompt || selectedCandidate?.prompt_template || selectedCandidate?.instructions || selectedCandidate?.prompt || "",
      scenario_id: scenarioId,
      tempo,
      source: "storyboard_audio_settings",
    };
    const nextPlan = renumberPlan(copy(plan()));
    nextPlan.storyboard_tts_selection = nextSelection;
    setPlan(nextPlan);
    setDirty(true);
    setTimingModel((previous) => ({ ...(previous || {}), selection: nextSelection, top_candidates: nextSelection.top_candidates || nextSelection.recommendations || previous?.top_candidates || [] }));
    if (!task()?.id) return;
    const result = await runAction("save-audio-settings", () => kbApi.save(task().id, nextPlan));
    const savedPlan = renumberPlan(copy(result.plan));
    setState({ task: result.task, meta: result.meta });
    setPlan(savedPlan);
    setDirty(false);
  }

  function timingDurationForDialogue(dialogue, secPerChar = buildGSecondsPerChar() || timingSecondsPerChar()) {
    const current = Number(dialogue?.duration || 0);
    const chars = dialogueCharCount(dialogue);
    if (chars > 0) return Number(Math.max(0.2, chars * Math.max(0.01, Number(secPerChar || 0.18))).toFixed(3));
    return Number(Math.max(0.2, current || 0.2).toFixed(3));
  }

  function rebuildPlanByFixedTiming(sourcePlan, shotSeconds, sceneSeconds) {
    const targetShot = Math.max(1, Number(shotSeconds || 16));
    const targetScene = Math.max(0.2, Number(sceneSeconds || 4));
    const maxScenesPerShot = Math.max(1, Math.floor(targetShot / targetScene));
    const flattened = [];
    for (const shot of sourcePlan?.shots || []) {
      for (const scene of shot.scenes || []) {
        for (const dialogue of scene.dialogues || []) flattened.push({ shot, dialogue: copy(dialogue) });
      }
    }
    if (!flattened.length) return sourcePlan;

    const nextShots = [];
    let currentShot = null;
    let currentScene = null;
    let currentShotDuration = 0;
    let currentSceneDuration = 0;
    let currentSceneIndex = 0;

    const startShot = (sourceShot) => {
      const shotIndex = nextShots.length + 1;
      const shotId = `shot_${String(shotIndex).padStart(3, "0")}`;
      currentShot = {
        ...copy(sourceShot),
        shot_id: shotId,
        shot_name: sourceShot?.shot_name || sourceShot?.shot_id || shotId,
        scenes: [],
        duration: 0,
      };
      currentShotDuration = 0;
      currentSceneDuration = 0;
      currentSceneIndex = 0;
      nextShots.push(currentShot);
      currentScene = null;
    };

    const startScene = () => {
      currentSceneIndex += 1;
      const sceneId = `${currentShot.shot_id}_scene_${String(currentSceneIndex).padStart(3, "0")}`;
      currentScene = {
        scene_id: sceneId,
        scene_index: currentSceneIndex,
        scene_name: `Scene ${currentSceneIndex}`,
        duration: 0,
        dialogues: [],
      };
      currentSceneDuration = 0;
      currentShot.scenes.push(currentScene);
    };

    for (const item of flattened) {
      const duration = timingDurationForDialogue(item.dialogue);
      const sceneWouldOverflow = currentSceneDuration > 0 && currentSceneDuration + duration > targetScene;
      const shotWouldOverflow = currentShotDuration > 0 && currentShotDuration + duration > targetShot;
      const sceneLimitWouldOverflow = sceneWouldOverflow && currentSceneIndex >= maxScenesPerShot;
      if (!currentShot || shotWouldOverflow || sceneLimitWouldOverflow) startShot(item.shot);
      if (!currentScene || (currentSceneDuration > 0 && currentSceneDuration + duration > targetScene)) startScene();

      const dialogueIndex = currentScene.dialogues.length + 1;
      const dialogueId = `${currentScene.scene_id}_dialogue_${String(dialogueIndex).padStart(3, "0")}`;
      currentScene.dialogues.push({
        ...item.dialogue,
        dialogue_id: dialogueId,
        dialogue_index: dialogueIndex,
        scene_id: currentScene.scene_id,
        duration,
        timing: {
          ...(item.dialogue.timing || {}),
          source: "builder_g_char_ratio",
          char_count: dialogueCharCount(item.dialogue),
          sec_per_char: Math.max(0.01, Number(timingSecondsPerChar() || 0.18)),
        },
      });
      currentSceneDuration = Number((currentSceneDuration + duration).toFixed(3));
      currentShotDuration = Number((currentShotDuration + duration).toFixed(3));
      currentScene.duration = currentSceneDuration;
      currentShot.duration = currentShotDuration;
    }

    sourcePlan.shots = nextShots;
    return sourcePlan;
  }

  function reorganizeFixedStoryboard() {
    const shotSeconds = Number(fixedShotSeconds() || 8);
    const sceneSeconds = Number(fixedSceneSeconds() || 4);
    updatePlan((draft) => rebuildPlanByFixedTiming(draft, shotSeconds, sceneSeconds));
    setSelectedShotIndex(0);
    setSelectedDialogueId("");
    setScope("all");
    setFixedMenuOpen(false);
    setGroupingDirty(true);
  }

  function refreshDialogueTimingsOnly() {
    const secPerChar = Math.max(0.01, Number(buildGSecondsPerChar() || timingSecondsPerChar() || 0.18));
    updatePlan((draft) => {
      for (const shot of draft.shots || []) for (const scene of shot.scenes || []) for (const dialogue of scene.dialogues || []) {
        const chars = dialogueCharCount(dialogue);
        dialogue.duration = Number(Math.max(0.2, chars * secPerChar).toFixed(3));
        dialogue.timing = { ...(dialogue.timing || {}), source: "builder_g_char_ratio", char_count: chars, sec_per_char: secPerChar, build_g_duration: timingModel()?.build_g_duration || 0 };
      }
    });
  }

  function sceneText(scene) {
    return (scene?.dialogues || []).map((dialogue) => dialogueText(dialogue)).filter(Boolean).join("\n");
  }

  function resourceUrl(path) {
    const value = String(path || "").trim();
    return sessionId() && value ? kbApi.rawFileUrl(sessionId(), value) : "";
  }

  function ttsDurationIsSuspicious(text, durationSeconds) {
    const duration = Number(durationSeconds || 0);
    const chars = spokenCharCount(String(text || ""));
    if (!Number.isFinite(duration) || duration <= 0 || chars <= 0) return false;
    const expectedMax = Math.max(4, chars * 0.8);
    return duration > expectedMax && duration > chars;
  }

  function canonicalTtsText(value) {
    return String(value || "").trim().replace(/\s+/g, " ");
  }

  function ttsPromptBodyText(prompt) {
    const value = String(prompt || "");
    const matches = [...value.matchAll(/(?:朗读文本|正文|Text)\s*[:：]/gi)];
    if (!matches.length) return "";
    const last = matches[matches.length - 1];
    return canonicalTtsText(value.slice((last.index || 0) + last[0].length));
  }

  function lockedTtsCacheTextMatches(expectedText, manifest) {
    const expected = canonicalTtsText(expectedText);
    if (!expected || canonicalTtsText(manifest?.text) !== expected) return false;
    let config = {};
    try {
      config = JSON.parse(String(manifest?.config_key || "{}"));
    } catch {
      config = {};
    }
    if (canonicalTtsText(config?.text) !== expected) return false;
    const promptText = ttsPromptBodyText(manifest?.prompt || config?.prompt);
    return !promptText || promptText === expected;
  }

  function lockedDialogueCacheTextMatches(dialogue, manifest) {
    return lockedTtsCacheTextMatches(dialogueText(dialogue), manifest);
  }

  function lockedSceneCacheTextMatches(scene, manifest) {
    return lockedTtsCacheTextMatches(sceneText(scene), manifest);
  }

  function dialogueAudioPath(dialogue) {
    const assets = dialogue?.working_assets && typeof dialogue.working_assets === "object" ? dialogue.working_assets : {};
    return String(assets.audio?.path || "").trim();
  }

  async function loadBoundDialogueAudio(dialogue) {
    const output = dialogueAudioPath(dialogue);
    if (!output || !(await audioResourceExists(output))) return null;
    const assets = dialogue?.working_assets && typeof dialogue.working_assets === "object" ? dialogue.working_assets : {};
    const audio = assets.audio && typeof assets.audio === "object" ? assets.audio : {};
    const audioVersion = Date.now();
    return {
      phase: "ready",
      audioSrc: `${resourceUrl(output)}?v=${audioVersion}`,
      audioVersion,
      output,
      provider: String(audio.voice_provider || ""),
      model: String(audio.model || ""),
      voiceId: String(audio.voice_id || ""),
      durationSeconds: positiveNumber(audio.duration_seconds) || positiveNumber(dialogue?.tts_duration) || positiveNumber(dialogue?.duration) || 0,
      tempo: positiveNumber(audio.tempo),
      configKey: `bound:${output}`,
      error: "",
      cacheHit: true,
      boundAudio: true,
    };
  }

  async function audioResourceExists(output) {
    const url = resourceUrl(output);
    if (!url) return false;
    const probeUrl = `${url}${url.includes("?") ? "&" : "?"}probe=${Date.now()}`;
    try {
      const head = await fetch(probeUrl, { method: "HEAD", credentials: "include", cache: "no-store" });
      if (head.ok) return true;
      if (![405, 501].includes(head.status)) return false;
    } catch {
      // Some local static handlers do not support HEAD. Fall back to a tiny range GET.
    }
    try {
      const res = await fetch(probeUrl, { credentials: "include", headers: { Range: "bytes=0-0" }, cache: "no-store" });
      return res.ok || res.status === 206;
    } catch {
      return false;
    }
  }

  function applyTtsTextToPrompt(prompt, provider, text) {
    const promptValue = String(prompt || "").trim();
    const textValue = String(text || "").trim();
    const stripEmbeddedText = (value) => String(value || "").replace(/(?:朗读文本|正文|Text)\s*[:：]\s*[\s\S]*$/i, "").trim();
    if (provider === "google") {
      if (!promptValue || promptValue === "{text}") return textValue;
      if (promptValue.includes("{text}")) return promptValue.replaceAll("{text}", textValue);
      return `${stripEmbeddedText(promptValue) || promptValue}\n\n正文：${textValue}`;
    }
    if (promptValue.includes("{text}")) return promptValue.replaceAll("{text}", textValue);
    const instruction = stripEmbeddedText(promptValue) || stripEmbeddedText(defaultTTSPrompt(provider, ""));
    return `${instruction}\n\n严格朗读当前 Scene 文本，不要朗读 prompt 中的示例文本或历史文本。`;
  }

  function dialogueAssetKey(dialogue) {
    const explicit = String(dialogue?.dialogue_asset_key || "").trim();
    return explicit;
  }

  function sceneTtsConfigKey(scene, card) {
    const text = sceneText(scene);
    const dialogueIds = (scene?.dialogues || []).map((dialogue) => String(dialogue.dialogue_id || "").trim()).filter(Boolean);
    return JSON.stringify({ sceneId: scene?.scene_id || "", dialogueIds, provider: card.provider || "", model: card.model || "", voiceId: card.voiceId || "", prompt: applyTtsTextToPrompt(card.prompt, card.provider, text), text, tempo: positiveNumber(card.tempo) });
  }

  function dialogueTtsConfigKey(dialogue, card) {
    const text = dialogueText(dialogue).trim();
    return JSON.stringify({ dialogueId: dialogue?.dialogue_id || "", assetKey: dialogueAssetKey(dialogue), provider: card.provider || "", model: card.model || "", voiceId: card.voiceId || "", prompt: applyTtsTextToPrompt(card.prompt, card.provider, text), text, tempo: positiveNumber(card.tempo) });
  }

  function lockedTTSExt(provider) {
    return provider === "xai" ? "mp3" : "wav";
  }

  function lockedSceneTTSPaths(scene, settings) {
    const sceneId = String(scene?.scene_id || "").trim();
    const assetKey = String(scene?.asset_key || "").trim();
    if (!sceneId || !assetKey) return { output: "", manifest: "" };
    return {
      output: `SessionOutput/storyboard/Working/${assetKey}_Audio_Final.${lockedTTSExt(settings.provider)}`,
      manifest: `SessionOutput/storyboard/tts_manifests/${assetKey}_Audio_Final.json`,
    };
  }

  function lockedDialogueTTSPaths(dialogue, settings) {
    const assetKey = dialogueAssetKey(dialogue);
    if (!assetKey) return { output: "", manifest: "" };
    return {
      output: `SessionOutput/storyboard/Working/${assetKey}_Audio_Final.${lockedTTSExt(settings.provider)}`,
      manifest: `SessionOutput/storyboard/tts_manifests/${assetKey}_Audio_Final.json`,
    };
  }

  function ensureDialogueWorkingAssets(dialogue) {
    const current = dialogue.working_assets && typeof dialogue.working_assets === "object" ? dialogue.working_assets : {};
    const images = Array.isArray(current.images) ? current.images : [];
    dialogue.working_assets = {
      audio: { slot: "Audio_Final", source_type: current.audio?.source_type || "", path: current.audio?.path || "" },
      images: [
        { slot: "Image_New", source_type: images[0]?.source_type || "", path: images[0]?.path || "" },
        { slot: "Image_02", source_type: images[1]?.source_type || "", path: images[1]?.path || "" },
      ],
      video: { slot: "Video_Final", source_type: current.video?.source_type || "", path: current.video?.path || "" },
    };
    return dialogue.working_assets;
  }

  function setDialogueAudioWorkingPath(dialogueId, output, durationSeconds = null) {
    if (!dialogueId || !output) return;
    const duration = Number(durationSeconds || 0);
    const nextPlan = renumberPlan(copy(plan()));
    for (const shot of nextPlan.shots || []) for (const scene of shot.scenes || []) {
      for (const dialogue of scene.dialogues || []) {
        if (dialogue.dialogue_id !== dialogueId) continue;
        const workingAssets = ensureDialogueWorkingAssets(dialogue);
        workingAssets.audio = { slot: "Audio_Final", source_type: "generated", path: output };
        if (Number.isFinite(duration) && duration > 0) {
          dialogue.duration = Number(duration.toFixed(3));
          dialogue.tts_duration = dialogue.duration;
          dialogue.timing = { ...(dialogue.timing || {}), source: "storyboard_dialogue_tts_audio", audio_dialogue_id: dialogueId };
        }
      }
    }
    setPlan(nextPlan);
    setDirty(true);
  }

  function applySceneAudioDuration(sceneId, durationSeconds, audioItems = []) {
    const duration = Number(durationSeconds || 0);
    if (!sceneId || !Number.isFinite(duration) || duration <= 0) return;
    const nextPlan = renumberPlan(copy(plan()));
    for (const shot of nextPlan.shots || []) for (const scene of shot.scenes || []) {
      if (scene.scene_id !== sceneId) continue;
      const dialogues = scene.dialogues || [];
      if (!dialogues.length) return;
      const itemByDialogueId = new Map((Array.isArray(audioItems) ? audioItems : []).map((item) => [item.dialogueId, Number(item.durationSeconds || 0)]));
      if (itemByDialogueId.size) {
        dialogues.forEach((dialogue) => {
          const itemDuration = itemByDialogueId.get(dialogue.dialogue_id);
          if (!Number.isFinite(itemDuration) || itemDuration <= 0) return;
          dialogue.duration = Number(itemDuration.toFixed(3));
          dialogue.tts_duration = dialogue.duration;
          dialogue.timing = { ...(dialogue.timing || {}), source: "storyboard_dialogue_tts_audio", audio_dialogue_id: dialogue.dialogue_id, audio_scene_id: sceneId };
        });
        setPlan(renumberPlan(nextPlan));
        setDirty(true);
        return;
      }
      const weights = dialogues.map((dialogue) => dialogueCharCount(dialogue) || 1);
      const totalWeight = weights.reduce((sum, value) => sum + value, 0) || dialogues.length;
      let assigned = 0;
      dialogues.forEach((dialogue, index) => {
        const value = index === dialogues.length - 1 ? Math.max(0, duration - assigned) : Number((duration * (weights[index] / totalWeight)).toFixed(3));
        dialogue.duration = Number(value.toFixed(3));
        dialogue.tts_duration = dialogue.duration;
        dialogue.timing = { ...(dialogue.timing || {}), source: "storyboard_tts_audio", audio_scene_id: sceneId };
        assigned += dialogue.duration;
      });
      setPlan(renumberPlan(nextPlan));
      setDirty(true);
      return;
    }
  }

  function updateSceneAudioState(sceneId, patch) {
    setSceneAudioState((previous) => ({ ...previous, [sceneId]: { ...(previous[sceneId] || {}), ...patch } }));
  }

  async function loadLockedSceneAudio(scene, settings, configKey) {
    const paths = lockedSceneTTSPaths(scene, settings);
    if (!paths.output || !paths.manifest) return null;
    const manifestUrl = resourceUrl(paths.manifest);
    if (!manifestUrl) return null;
    try {
      const res = await fetch(`${manifestUrl}?v=${Date.now()}`, { credentials: "include" });
      if (!res.ok) return null;
      const manifest = await res.json();
      if (manifest?.config_key !== configKey) return null;
      if (!lockedSceneCacheTextMatches(scene, manifest)) return null;
      const output = String(manifest.output || paths.output || "").trim();
      if (!output) return null;
      if (!(await audioResourceExists(output))) return null;
      const audioVersion = Number(manifest.updated_at || 0) || Date.now();
      return {
        phase: "ready",
        audioSrc: `${resourceUrl(output)}?v=${audioVersion}`,
        audioVersion,
        output,
        provider: manifest.provider || settings.provider,
        model: manifest.model || settings.model,
        voiceId: manifest.voice_id || settings.voiceId,
        durationSeconds: manifest.duration_seconds ?? manifest.duration ?? 0,
        rawDuration: manifest.raw_duration,
        tempo: manifest.tempo || settings.tempo,
        configKey,
        error: "",
        cacheHit: true,
      };
    } catch {
      return null;
    }
  }

  async function loadLockedDialogueAudio(dialogue, settings, configKey) {
    const paths = lockedDialogueTTSPaths(dialogue, settings);
    if (!paths.output || !paths.manifest) return null;
    const manifestUrl = resourceUrl(paths.manifest);
    if (!manifestUrl) return null;
    try {
      const res = await fetch(`${manifestUrl}?v=${Date.now()}`, { credentials: "include" });
      if (!res.ok) return null;
      const manifest = await res.json();
      if (manifest?.config_key !== configKey) return null;
      if (!lockedDialogueCacheTextMatches(dialogue, manifest)) return null;
      const output = String(manifest.output || paths.output || "").trim();
      if (!output) return null;
      const durationSeconds = manifest.duration_seconds ?? manifest.duration ?? 0;
      if (ttsDurationIsSuspicious(dialogueText(dialogue), durationSeconds)) return null;
      if (!(await audioResourceExists(output))) return null;
      const audioVersion = Number(manifest.updated_at || 0) || Date.now();
      return {
        phase: "ready",
        audioSrc: `${resourceUrl(output)}?v=${audioVersion}`,
        audioVersion,
        output,
        provider: manifest.provider || settings.provider,
        model: manifest.model || settings.model,
        voiceId: manifest.voice_id || settings.voiceId,
        durationSeconds,
        rawDuration: manifest.raw_duration,
        tempo: manifest.tempo || settings.tempo,
        configKey,
        error: "",
        cacheHit: true,
      };
    } catch {
      return null;
    }
  }

  async function generateDialogueAudio(shot, scene, dialogue) {
    if (!task()?.id || !scene?.scene_id || !dialogue?.dialogue_id) throw new Error("当前 StoryBoard Dialogue 不完整，无法生成声音");
    try {
      const stateKey = dialogue.dialogue_id;
      const boundAudio = await loadBoundDialogueAudio(dialogue);
      if (boundAudio?.audioSrc) {
        updateSceneAudioState(stateKey, boundAudio);
        return boundAudio;
      }
      await ensureTTSModelConfig();
      const settings = audioSettings();
      const assetKey = dialogueAssetKey(dialogue);
      if (!assetKey) throw new Error("当前 Dialogue 缺少素材 Key，请先保存并刷新 StoryBoard 后再生成声音");
      const text = dialogueText(dialogue).trim();
      if (!text) throw new Error("这个 Dialogue 没有可朗读的文字");
      if (isAdmin() && (!settings.provider || !settings.model || !settings.voiceId)) throw new Error("请先设置可用的 TTS Provider / Model / Voice");
      if (!isAdmin() && !settings.voiceId) throw new Error("请先设置可用的 TTS Voice");
      const card = { ...settings };
      const configKey = dialogueTtsConfigKey(dialogue, card);
      const cached = sceneAudioState()[stateKey];
      const currentAudioPath = dialogueAudioPath(dialogue);
      if (cached?.audioSrc && cached.configKey === configKey && cached.output && currentAudioPath === cached.output && await audioResourceExists(cached.output)) return cached;
      if (cached?.audioSrc && cached.configKey === configKey) updateSceneAudioState(stateKey, { phase: "idle", audioSrc: "", output: "", error: "" });
      const locked = lockedDialogueTTSPaths(dialogue, settings);
      let completed = null;
      let failed = "";
      updateSceneAudioState(stateKey, { phase: "generating", error: "" });
      const lockedCached = await loadLockedDialogueAudio(dialogue, settings, configKey);
      if (lockedCached?.audioSrc) {
        setDialogueAudioWorkingPath(dialogue.dialogue_id, lockedCached.output, lockedCached.durationSeconds);
        updateSceneAudioState(stateKey, lockedCached);
        return lockedCached;
      }
      const ttsRequest = beginTtsRequest(`dialogue:${stateKey}`, stateKey);
      updateSceneAudioState(stateKey, { phase: "generating", error: "" });
      try {
        await kbApi.streamCompareAssetTTS(task().id, {
          workflow_id: `koubo_storyboard_dialogue_tts_${dialogue.dialogue_id}`,
          shot_id: shot?.shot_id || "",
          scene_mark_id: scene.scene_id,
          dialogue_id: dialogue.dialogue_id,
          dialogue_asset_key: assetKey,
          srt_text: text,
          use_locked_cache: true,
          locked_output: locked.output,
          locked_manifest: locked.manifest,
          locked_config_key: configKey,
          prompts: [{
            ...(isAdmin() ? { provider: settings.provider, model: settings.model } : {}),
            voice_id: settings.voiceId,
            voice_source: settings.voiceSource || "",
            candidate_id: settings.candidateId || "",
            prompt: applyTtsTextToPrompt(settings.prompt, settings.provider, text),
            text,
            user_instruction: "故事版（口播）Dialogue 级声音生成，严格按当前 Dialogue 文本朗读。",
            tempo: positiveNumber(settings.tempo),
            target_duration: null,
            fit_to_duration: false,
          }],
        }, (event) => {
          if (!isActiveTtsRequest(ttsRequest)) return;
          if (event.type === "completed") {
            const audioVersion = Date.now();
            completed = {
              phase: "ready",
              audioSrc: `${resourceUrl(event.output)}?v=${audioVersion}`,
              audioVersion,
              output: event.output,
              provider: isAdmin() ? event.provider : "",
              model: isAdmin() ? event.model : "",
              voiceId: event.voice_id || settings.voiceId,
              durationSeconds: event.duration_seconds ?? event.duration ?? 0,
              rawDuration: event.raw_duration,
              tempo: event.tempo || settings.tempo,
              configKey,
              error: "",
              cacheHit: Boolean(event.cache_hit),
            };
            setDialogueAudioWorkingPath(dialogue.dialogue_id, event.output, completed.durationSeconds);
            updateSceneAudioState(stateKey, completed);
          }
          if (event.type === "failed") {
            failed = event.detail || "TTS 生成失败";
            updateSceneAudioState(stateKey, { phase: "error", error: failed });
          }
        }, { signal: ttsRequest.controller.signal });
      } catch (err) {
        const abortError = abortErrorForTts(ttsRequest, err);
        if (abortError) {
          if (ttsRequest.timedOut) updateSceneAudioState(stateKey, { phase: "error", error: abortError.message });
          throw abortError;
        }
        throw err;
      } finally {
        finishTtsRequest(ttsRequest);
      }
      if (completed?.audioSrc) return completed;
      throw new Error(failed || "TTS 没有返回可播放音频");
    } catch (err) {
      console.warn(err);
      throw err;
    }
  }

  async function generateSceneAudio(shot, scene) {
    const dialogues = scene?.dialogues || [];
    if (!dialogues.length) throw new Error("这个 Scene 没有可朗读的 Dialogue");
    const sceneStateKey = scene.scene_id;
    updateSceneAudioState(sceneStateKey, { phase: "generating", error: "" });
    const items = [];
    try {
      for (const dialogue of dialogues) {
        items.push({ dialogueId: dialogue.dialogue_id, ...(await generateDialogueAudio(shot, scene, dialogue)) });
      }
    } catch (err) {
      if (isBenignTtsStop(err)) {
        updateSceneAudioState(sceneStateKey, { phase: "idle", error: "" });
      } else {
        updateSceneAudioState(sceneStateKey, { phase: "error", error: err instanceof Error ? err.message : String(err) });
      }
      throw err;
    }
    const result = {
      phase: "ready",
      items,
      audioSrc: items[0]?.audioSrc || "",
      output: items[0]?.output || "",
      durationSeconds: items.reduce((sum, item) => sum + Number(item.durationSeconds || 0), 0),
      configKey: sceneTtsConfigKey(scene, audioSettings()),
      error: "",
    };
    updateSceneAudioState(sceneStateKey, result);
    return result;
  }

  return {
    buildGSecondsPerChar,
    providerTTSModel,
    ensureTTSModelConfig,
    defaultTTSPrompt,
    promptForRecommendedVoice,
    ttsProviderOptions,
    ttsModelsForProvider,
    ttsVoicesForModel,
    storyboardAudioSelection,
    audioSettings,
    openAudioSettings,
    saveAudioSettings,
    timingDurationForDialogue,
    rebuildPlanByFixedTiming,
    reorganizeFixedStoryboard,
    refreshDialogueTimingsOnly,
    sceneText,
    resourceUrl,
    applyTtsTextToPrompt,
    sceneTtsConfigKey,
    lockedTTSExt,
    lockedSceneTTSPaths,
    lockedDialogueTTSPaths,
    setDialogueAudioWorkingPath,
    applySceneAudioDuration,
    updateSceneAudioState,
    loadLockedSceneAudio,
    loadLockedDialogueAudio,
    generateDialogueAudio,
    generateSceneAudio,
    cancelActiveTtsRequests,
  };
}
