import { For, Show, createEffect, createMemo, createSignal, onCleanup, onMount } from "solid-js";
import "./styles.css";
import { StoryboardAssetPool } from "./OCStoryBoard/components/StoryboardAssetPool.jsx";
import { StoryboardImagePreview } from "./OCStoryBoard/components/StoryboardImagePreview.jsx";
import { StoryboardNewTask } from "./OCStoryBoard/components/StoryboardNewTask.jsx";
import { StoryboardShotCard } from "./OCStoryBoard/components/StoryboardShotCard.jsx";
import { StoryboardSidebar } from "./OCStoryBoard/components/StoryboardSidebar.jsx";
import { StoryboardTaskList } from "./OCStoryBoard/components/StoryboardTaskList.jsx";
import { StoryboardTimeline } from "./OCStoryBoard/components/StoryboardTimeline.jsx";
import { AiMenu, FixedMenu, SaveButton, SourceMeta, TimingMenu } from "./OCStoryBoard/components/StoryboardToolbarMenus.jsx";
import { api, routeFromHash } from "./OCStoryBoard/storyboardApi.js";
import {
  clone,
  makeSceneMark,
  normalizeStoryboardPlan,
  nextSceneId,
  sceneMarks,
  shotDisplayName,
  splitDialogueText,
  storyboardScenes,
} from "./OCStoryBoard/storyboardModel.js";
import { reorganizePlanByFixedTiming } from "./OCStoryBoard/storyboardReorganize.js";
import {
  assetIdentity,
  assetUrl,
  dialogueAssetIdForScene,
  dialogueBoundAsset,
  dialogueBoundImageForScene,
  scenePathIdentity,
} from "./OCStoryBoard/storyboardAssets.js";
import {
  applyBuilderGTimings,
  buildGSecondsPerChar as modelBuildGSecondsPerChar,
  formatTime,
  spokenCharCount,
  timingInfoForDialogue as modelTimingInfoForDialogue,
} from "./OCStoryBoard/storyboardTiming.js";

export default function OCStoryBoardModule(props) {
  const [route, setRoute] = createSignal(routeFromHash(props.routeHash));
  const [items, setItems] = createSignal([]);
  const [state, setState] = createSignal(null);
  const [shotPlan, setShotPlan] = createSignal(null);
  const [assetPool, setAssetPool] = createSignal([]);
  const [timingModel, setTimingModel] = createSignal(null);
  const [selectedShotIndex, setSelectedShotIndex] = createSignal(0);
  const [selectedSceneId, setSelectedSceneId] = createSignal("");
  const [editingSceneId, setEditingSceneId] = createSignal("");
  const [busy, setBusy] = createSignal("");
  const [error, setError] = createSignal("");
  const [dirty, setDirty] = createSignal(false);
  const [storyTheme, setStoryTheme] = createSignal("light");
  const [fixedMenuOpen, setFixedMenuOpen] = createSignal(false);
  const [aiMenuOpen, setAiMenuOpen] = createSignal(false);
  const [timingMenuOpen, setTimingMenuOpen] = createSignal(false);
  const [fixedShotSeconds, setFixedShotSeconds] = createSignal(16);
  const [fixedSceneSeconds, setFixedSceneSeconds] = createSignal(4);
  const [aiPrompt, setAiPrompt] = createSignal("");
  const [selectedAsset, setSelectedAsset] = createSignal(null);
  const [consumedAssetIds, setConsumedAssetIds] = createSignal(new Set());
  const [assetUploadBusy, setAssetUploadBusy] = createSignal(false);
  const [deletingAssetId, setDeletingAssetId] = createSignal("");
  const [imagePreview, setImagePreview] = createSignal(null);
  const [leftPanelWidth, setLeftPanelWidth] = createSignal(256);
  const [leftResizeState, setLeftResizeState] = createSignal(null);
  const [timelineSelectionScope, setTimelineSelectionScope] = createSignal("scene");
  const [ttsModelConfig, setTTSModelConfig] = createSignal(null);
  const [playbackSpeedOpen, setPlaybackSpeedOpen] = createSignal(false);
  const [playbackSpeed, setPlaybackSpeed] = createSignal(1);
  const [sceneAudioState, setSceneAudioState] = createSignal({});
  const [playbackState, setPlaybackState] = createSignal({ phase: "idle", status: "" });
  let suppressNextAssetClick = false;
  let activeAssetDragCleanup = null;
  let storyboardAudio = null;
  let playbackRunId = 0;

  createEffect(() => {
    const nextRoute = routeFromHash(props.routeHash);
    setRoute(nextRoute);
    if (nextRoute.view === "detail" && nextRoute.taskId) void loadDetail(nextRoute.taskId);
    if (nextRoute.view === "list") void loadList();
  });

  createEffect(() => {
    const resizeState = leftResizeState();
    if (!resizeState) return;
    const onMove = (event) => {
      const next = Math.min(Math.max(220, resizeState.startWidth + (event.clientX - resizeState.startX)), Math.min(460, window.innerWidth - 760));
      setLeftPanelWidth(next);
    };
    const onUp = () => setLeftResizeState(null);
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    onCleanup(() => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    });
  });

  onMount(() => {
    const toList = () => { window.location.hash = "#/ocstoryboard/tasks"; };
    const toNew = () => { window.location.hash = "#/ocstoryboard/new"; };
    window.addEventListener("ocstoryboard:task-list", toList);
    window.addEventListener("ocstoryboard:new-task", toNew);
    onCleanup(() => {
      window.removeEventListener("ocstoryboard:task-list", toList);
      window.removeEventListener("ocstoryboard:new-task", toNew);
    });
  });

  onCleanup(() => {
    if (storyboardAudio) storyboardAudio.pause();
  });

  const task = createMemo(() => state()?.task || null);
  const meta = createMemo(() => state()?.meta || {});
  const shots = createMemo(() => shotPlan()?.shots || []);
  const totalDuration = createMemo(() => shots().reduce((sum, shot) => sum + Number(shot.duration || 0), 0));
  const assignedAssetIds = createMemo(() => {
    const ids = new Set();
    for (const shot of shots()) {
      const marks = sceneMarks(shot);
      marks.forEach((mark, index) => {
        const id = dialogueAssetIdForScene(mark, index, marks.length || 1);
        if (id) ids.add(id);
      });
    }
    return ids;
  });
  const assetUseCounts = createMemo(() => {
    const counts = new Map();
    for (const shot of shots()) {
      const marks = sceneMarks(shot);
      marks.forEach((mark, index) => {
        const id = dialogueAssetIdForScene(mark, index, marks.length || 1);
        if (!id) return;
        counts.set(id, (counts.get(id) || 0) + 1);
      });
    }
    return counts;
  });
  const assignedScenePaths = createMemo(() => {
    const keys = new Set();
    for (const shot of shots()) {
      const marks = sceneMarks(shot);
      marks.forEach((mark, index) => {
        const path = dialogueBoundImageForScene(mark);
        if (path) keys.add(scenePathIdentity(mark.scene_mark_id, path));
      });
    }
    return keys;
  });
  const assetShotGroups = createMemo(() => {
    const byShot = new Map();
    const roleRank = { display: 0, single: 1, first: 2, last: 3 };
    const isAssignedAsset = (item) => Boolean(
      item
      && (
        assignedAssetIds().has(assetIdentity(item))
        || assignedScenePaths().has(scenePathIdentity(item.scene_mark_id, item.path))
      )
    );
    for (const item of assetPool()) {
      if (!item?.path || !item?.shot_id || !item?.scene_mark_id) continue;
      if (item.role === "candidate" || item.role === "pool") continue;
      if (!byShot.has(item.shot_id)) byShot.set(item.shot_id, { shot_id: item.shot_id, scenes: new Map() });
      const group = byShot.get(item.shot_id);
      const sceneId = item.scene_mark_id;
      if (!group.scenes.has(sceneId)) group.scenes.set(sceneId, []);
      group.scenes.get(sceneId).push(item);
    }
    return Array.from(byShot.values()).map((group) => {
      const scenes = Array.from(group.scenes.entries()).map(([sceneId, items]) => {
        const sorted = [...items].sort((a, b) => (roleRank[a.role] ?? 10) - (roleRank[b.role] ?? 10));
        const first = sorted.find((item) => ["display", "single", "first"].includes(item.role)) || sorted[0] || null;
        const tail = sorted.find((item) => item.role === "last" && item.path && item.path !== first?.path) || null;
        const sceneMeta = first || tail || {};
        const slots = [
          { reference: first, item: first, role: "首帧", placed: isAssignedAsset(first) },
        ];
        if (tail) slots.push({ reference: tail, item: tail, role: "尾帧", placed: isAssignedAsset(tail) });
        return {
          scene_id: sceneMeta.scene_id || sceneId,
          scene_mark_id: sceneId,
          duration: Number(sceneMeta.duration || 0),
          text: sceneMeta.srt_text || "",
          char_count: Number(sceneMeta.char_count || 0),
          slots,
        };
      });
      return {
        shot_id: group.shot_id,
        scene_count: scenes.length,
        duration: scenes.reduce((sum, scene) => sum + Number(scene.duration || 0), 0),
        scenes,
      };
    });
  });
  const manualAssetItems = createMemo(() => {
    return assetPool()
      .filter((item) => {
        const source = String(item?.source || "");
        const section = String(item?.pool_section || "");
        const path = String(item?.path || "");
        return Boolean(item?.path) && (
          section === "manual"
          || source === "manual_upload"
          || source === "upload"
          || path.startsWith("uploads/storyboard/asset_pool/manual/")
          || path.startsWith("uploads/storyboard/images/")
        );
      })
      .map((item) => ({
        ...item,
        placed: consumedAssetIds().has(assetIdentity(item)) || assignedAssetIds().has(assetIdentity(item)) || assignedScenePaths().has(scenePathIdentity(item.scene_mark_id, item.path)),
      }))
      .filter((item) => !item.placed);
  });

  function openImagePreview(item, label = "") {
    const src = assetUrl(item, task()?.session_id);
    if (!src) return;
    setImagePreview({
      ...item,
      src,
      label: label || item?.label || item?.filename || item?.path || "Image",
    });
  }

  function buildGSecondsPerChar() {
    return modelBuildGSecondsPerChar(timingModel());
  }

  function timingInfoForDialogue(mark) {
    return modelTimingInfoForDialogue(mark, timingModel());
  }

  function refreshDialogueTimingsOnly() {
    updatePlan((plan) => {
      applyBuilderGTimings(plan, timingModel());
    });
  }

  function positiveNumber(value) {
    const number = Number(value);
    return Number.isFinite(number) && number > 0 ? number : null;
  }

  function tempoFromCandidate(item) {
    if (!item || typeof item !== "object") return null;
    const fitMeta = item.fit_meta && typeof item.fit_meta === "object" ? item.fit_meta : {};
    return positiveNumber(item.tempo ?? item.speed_factor ?? fitMeta.tempo ?? fitMeta.speed_factor);
  }

  function voiceMatchesCandidate(selection, item) {
    const selectedCandidateId = String(selection?.candidate_id || "").trim();
    const candidateId = String(item?.candidate_id || "").trim();
    if (selectedCandidateId && candidateId && selectedCandidateId === candidateId) return true;
    const selectedValues = [selection?.voice_id, selection?.voice, selection?.label].map((value) => String(value || "").trim().toLowerCase()).filter(Boolean);
    const candidateValues = [item?.voice_id, item?.voice, item?.label].map((value) => String(value || "").trim().toLowerCase()).filter(Boolean);
    return selectedValues.some((selected) => candidateValues.some((candidate) => selected === candidate || selected.includes(candidate) || candidate.includes(selected)));
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
  if (candidateTempo) return candidateTempo;
  if (fitTempo) return fitTempo;
  if (directTempo && Math.abs(directTempo - 1) > 0.0001) return directTempo;
    return directTempo;
  }

  function resourceUrl(path) {
    const sessionId = task()?.session_id;
    const value = String(path || "").trim();
    return sessionId && value ? api.rawFileUrl(sessionId, value) : "";
  }

  function providerTTSModel(provider, preferred = "") {
    const providerConfig = (ttsModelConfig()?.providers || []).find((item) => item.provider === provider);
    if (!providerConfig) return null;
    const models = providerConfig.models || [];
    const preferredModel = models.find((item) => item.model === preferred);
    const configured = models.find((item) => item.model === providerConfig.model);
    const fallback = provider === "qwen" ? models.find((item) => item.model === "qwen3-tts-instruct-flash") : provider === "xai" ? models.find((item) => item.model === "xai-tts") : models.find((item) => String(item.model).includes("tts"));
    const model = preferredModel || fallback || configured || models[0];
    if (!model) return null;
    const selectedVoice = providerConfig.selected_voice_by_model?.[model.model] || model.voices?.[0]?.voice_id || "";
    return { provider: providerConfig.provider, providerLabel: providerConfig.provider_label || providerConfig.provider, model: model.model, models, voices: model.voices || [], voiceId: selectedVoice, enabled: providerConfig.enabled, hasApiKey: providerConfig.has_api_key };
  }

  async function ensureTTSModelConfig() {
    if (ttsModelConfig()) return ttsModelConfig();
    const config = await api.ttsModelConfig();
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

  function sceneText(scene) {
    return (scene?.marks || []).map((mark) => String(mark.srt_text || "").trim()).filter(Boolean).join("\n");
  }

  function buildGAudioSelection() {
    const model = timingModel() || {};
    const selection = model.selection && typeof model.selection === "object" ? model.selection : null;
    const topCandidates = Array.isArray(model.top_candidates) ? model.top_candidates : [];
    const candidate = selection || topCandidates[0] || null;
    if (!candidate) return null;
    return {
      ...candidate,
      provider: candidate.provider || model.provider || "",
      model: candidate.model || model.model || "",
      voice_id: candidate.voice_id || candidate.voice || model.voice || "",
      voice: candidate.voice || candidate.voice_id || model.voice || "",
      label: candidate.label || candidate.voice_id || candidate.voice || model.voice || "",
      candidate_id: candidate.candidate_id || model.candidate_id || "",
      top_candidates: topCandidates,
      recommendations: topCandidates,
      source: "builder_g_timing",
    };
  }

  function storyboardAudioSelection() {
    const selectedShot = shots()[Math.max(0, selectedShotIndex())] || shots()[0] || {};
    const saved = shotPlan()?.storyboard_tts_selection || selectedShot?.tts_selection || shotPlan()?.plan_a_tts_selection || null;
    const builder = buildGAudioSelection();
    if (!saved) return builder;
    const builderCandidates = builder?.top_candidates || builder?.recommendations || [];
    if (!Array.isArray(saved.top_candidates) && !Array.isArray(saved.recommendations) && builderCandidates.length) {
      return { ...saved, top_candidates: builderCandidates, recommendations: builderCandidates };
    }
    return saved;
  }

  function audioSettings() {
    const selection = storyboardAudioSelection();
    const fallback = providerTTSModel("qwen", "qwen3-tts-instruct-flash") || providerTTSModel("google", "gemini-2.5-flash-preview-tts") || providerTTSModel("xai", "xai-tts") || {};
    const provider = selection?.provider || fallback.provider || "";
    const model = selection?.model || fallback.model || "";
    const voiceId = selection?.voice_id || selection?.voice || fallback.voiceId || "";
    return {
      provider,
      model,
      voiceId,
      candidateId: selection?.candidate_id || "",
      label: selection?.label || voiceId,
      prompt: selection?.prompt || promptForRecommendedVoice(selection || {}, null) || defaultTTSPrompt(provider),
      tempo: tempoFromSelection(selection) || 1,
      topCandidates: selection?.top_candidates || selection?.recommendations || [],
    };
  }

  function candidateFromAudioValues(selection, values) {
    const candidates = Array.isArray(selection?.top_candidates) ? selection.top_candidates : Array.isArray(selection?.recommendations) ? selection.recommendations : [];
    const valueProvider = String(values?.provider || "").trim().toLowerCase();
    const valueModel = String(values?.model || "").trim().toLowerCase();
    const valueVoiceId = String(values?.voiceId || values?.voice_id || values?.voice || "").trim().toLowerCase();
    const matchesValues = (item) => {
      if (!item) return false;
      const itemProvider = String(item?.provider || "").trim().toLowerCase();
      const itemModel = String(item?.model || "").trim().toLowerCase();
      const itemValues = [item?.voice_id, item?.voice, item?.label].map((value) => String(value || "").trim().toLowerCase()).filter(Boolean);
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
    return candidates.find((item) => matchesValues(item)) || null;
  }

  function applyTtsTextToPrompt(prompt, provider, text) {
    const promptValue = String(prompt || "").trim();
    const textValue = String(text || "").trim();
    if (provider === "google") {
      if (!promptValue || promptValue === "{text}") return textValue;
      if (promptValue.includes("{text}")) return promptValue.replaceAll("{text}", textValue);
      return `${promptValue}\n\n正文：${textValue}`;
    }
    if (promptValue.includes("{text}")) return promptValue.replaceAll("{text}", textValue);
    return promptValue || defaultTTSPrompt(provider, textValue);
  }

  function sceneTtsConfigKey(scene, card) {
    const text = sceneText(scene);
    const markIds = (scene?.marks || []).map((mark) => String(mark.scene_mark_id || mark.dialogue_id || "").trim()).filter(Boolean);
    return JSON.stringify({ sceneId: scene?.scene_id || "", markIds, provider: card.provider || "", model: card.model || "", voiceId: card.voiceId || "", prompt: applyTtsTextToPrompt(card.prompt, card.provider, text), text, tempo: positiveNumber(card.tempo) });
  }

  function updateSceneAudioState(sceneId, patch) {
    setSceneAudioState((previous) => ({ ...previous, [sceneId]: { ...(previous[sceneId] || {}), ...patch } }));
  }

  function lockedTTSExt(provider) {
    return provider === "xai" ? "mp3" : "wav";
  }

  function sceneAssetBaseRel(shot, scene) {
    const shotId = String(shot?.shot_id || scene?.marks?.[0]?.shot_id || "").trim();
    const sceneId = String(scene?.scene_id || scene?.marks?.[0]?.scene_id || "").trim();
    const scenePrefix = shotId && sceneId ? `Assets/variant_001/${shotId}/${sceneId}` : "";
    const candidates = (scene?.marks || []).flatMap((mark) => [
      mark?.keyframes?.single,
      mark?.keyframes?.first,
      mark?.keyframes?.last,
      mark?.asset?.selected_image,
      mark?.asset?.image,
    ]).map((value) => String(value || "").trim()).filter(Boolean);
    const matched = scenePrefix ? candidates.find((value) => value.startsWith(`${scenePrefix}/`)) : "";
    if (matched) return scenePrefix;
    return scenePrefix || (shotId ? `Assets/variant_001/${shotId}/tts` : "");
  }

  function lockedSceneTTSPaths(shot, scene, settings) {
    const base = sceneAssetBaseRel(shot, scene);
    if (!base) return { output: "", manifest: "" };
    const ttsBase = base.endsWith("/tts") ? base : `${base}/tts`;
    return {
      output: `${ttsBase}/locked.${lockedTTSExt(settings.provider)}`,
      manifest: `${ttsBase}/tts_manifest.json`,
    };
  }

  async function loadLockedSceneAudio(shot, scene, settings, configKey) {
    const paths = lockedSceneTTSPaths(shot, scene, settings);
    if (!paths.output || !paths.manifest) return null;
    const manifestUrl = resourceUrl(paths.manifest);
    if (!manifestUrl) return null;
    try {
      const res = await fetch(`${manifestUrl}?v=${Date.now()}`, { credentials: "include" });
      if (!res.ok) return null;
      const manifest = await res.json();
      if (manifest?.config_key !== configKey) return null;
      const output = String(manifest.output || paths.output || "").trim();
      if (!output) return null;
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

  async function saveAudioSettings(values) {
    if (!shotPlan()) return;
    const selection = storyboardAudioSelection() || {};
    const selectedCandidate = candidateFromAudioValues(selection, values);
    const provider = String(values?.provider || "").trim();
    const model = String(values?.model || "").trim();
    const voiceId = String(values?.voiceId || values?.voice_id || "").trim();
    const prompt = String(values?.prompt || "").trim();
    const candidateTempo = tempoFromCandidate(selectedCandidate);
    const valueTempo = positiveNumber(values?.tempo);
    const candidateScore = positiveNumber(selectedCandidate?.score);
    const tempoLooksLikeScore = valueTempo && candidateScore && Math.abs(valueTempo - candidateScore) < 0.0005;
    const tempo = (tempoLooksLikeScore && candidateTempo ? candidateTempo : valueTempo) || candidateTempo || 1;
    const preservedSelection = selectedCandidate ? selection : {
      top_candidates: selection.top_candidates,
      recommendations: selection.recommendations,
    };
    const nextSelection = {
      ...preservedSelection,
      ...(selectedCandidate ? {
        provider: selectedCandidate.provider || provider,
        model: selectedCandidate.model || model,
        voice_id: selectedCandidate.voice_id || selectedCandidate.voice || voiceId,
        voice: selectedCandidate.voice || selectedCandidate.voice_id || voiceId,
        label: selectedCandidate.label || selectedCandidate.voice_id || selectedCandidate.voice || values?.label || voiceId,
        score: selectedCandidate.score,
        audio: selectedCandidate.audio || selectedCandidate.output || selectedCandidate.fit_audio || "",
        fit_audio: selectedCandidate.fit_audio || selectedCandidate.output || selectedCandidate.audio || "",
        raw_audio: selectedCandidate.raw_audio || "",
        prompt_template: selectedCandidate.prompt_template || selectedCandidate.instructions || selectedCandidate.prompt || prompt,
        fit_meta: selectedCandidate.fit_meta,
        raw_duration: selectedCandidate.raw_duration,
        fit_duration: selectedCandidate.fit_duration,
      } : {}),
      provider: selectedCandidate?.provider || provider,
      model: selectedCandidate?.model || model,
      voice_id: selectedCandidate?.voice_id || selectedCandidate?.voice || voiceId,
      voice: selectedCandidate?.voice || selectedCandidate?.voice_id || voiceId,
      label: selectedCandidate?.label || values?.label || voiceId,
      candidate_id: selectedCandidate?.candidate_id || values?.candidateId || values?.candidate_id || "",
      prompt,
      tempo,
      source: "storyboard_audio_settings",
    };
    const nextPlan = clone(shotPlan());
    nextPlan.storyboard_tts_selection = nextSelection;
    normalizeStoryboardPlan(nextPlan);
    setShotPlan(nextPlan);
    setDirty(true);
    if (!task()?.id) return;
    const result = await runAction("save-audio-settings", () => api.save(task().id, nextPlan));
    setState({ task: result.task, meta: result.meta });
    setShotPlan(result.shot_plan);
    setAssetPool(result.asset_pool || []);
    setTimingModel(result.timing_model || null);
    setDirty(false);
  }

  async function openAudioSettings() {
    await ensureTTSModelConfig();
  }

  function applyPlaybackSpeed(value) {
    const speed = Math.min(4, Math.max(0.25, positiveNumber(value) || 1));
    setPlaybackSpeed(speed);
    if (storyboardAudio) storyboardAudio.playbackRate = speed;
    setPlaybackSpeedOpen(false);
  }

  function selectedScenesForPlayback() {
    const allScenes = shots().flatMap((shot) => storyboardScenes(shot).map((scene) => ({ shot, scene })));
    if (timelineSelectionScope() === "all") return allScenes;
    const sceneKey = String(selectedSceneId() || "").replace(/_dialogue_\d+$/, "");
    if (sceneKey) return allScenes.filter((item) => item.scene.scene_id === sceneKey);
    const shot = shots()[Math.max(0, selectedShotIndex())];
    return shot ? storyboardScenes(shot).map((scene) => ({ shot, scene })) : allScenes;
  }

  function applySceneAudioDuration(sceneId, durationSeconds) {
    const duration = Number(durationSeconds || 0);
    if (!sceneId || !Number.isFinite(duration) || duration <= 0) return;
    updatePlan((plan) => {
      for (const shot of plan.shots || []) {
        const marks = sceneMarks(shot).filter((mark) => (mark.scene_id || String(mark.scene_mark_id || "").replace(/_dialogue_\d+$/, "")) === sceneId);
        if (!marks.length) continue;
        const weights = marks.map((mark) => spokenCharCount(mark.srt_text || "") || 1);
        const totalWeight = weights.reduce((sum, value) => sum + value, 0) || marks.length;
        let assigned = 0;
        marks.forEach((mark, index) => {
          const value = index === marks.length - 1 ? Math.max(0, duration - assigned) : Number((duration * (weights[index] / totalWeight)).toFixed(3));
          mark.duration = Number(value.toFixed(3));
          mark.tts_duration = mark.duration;
          mark.timing = { ...(mark.timing || {}), source: "storyboard_tts_audio", audio_scene_id: sceneId };
          assigned += mark.duration;
        });
        return;
      }
    });
  }

  async function generateSceneAudio(shot, scene) {
    if (!task()?.id || !scene?.scene_id) throw new Error("当前 StoryBoard Task 不完整，无法生成声音");
    await ensureTTSModelConfig();
    const settings = audioSettings();
    const text = sceneText(scene).trim();
    if (!text) throw new Error("这个 Scene 没有可朗读的文字");
    if (!settings.provider || !settings.model || !settings.voiceId) throw new Error("请先设置可用的 TTS Provider / Model / Voice");
    const card = { ...settings };
    const configKey = sceneTtsConfigKey(scene, card);
    const cached = sceneAudioState()[scene.scene_id];
    if (cached?.audioSrc && cached.configKey === configKey) return cached;
    const locked = lockedSceneTTSPaths(shot, scene, settings);
    const lockedAudio = await loadLockedSceneAudio(shot, scene, settings, configKey);
    if (lockedAudio?.audioSrc) {
      updateSceneAudioState(scene.scene_id, lockedAudio);
      return lockedAudio;
    }
    let completed = null;
    let failed = "";
    updateSceneAudioState(scene.scene_id, { phase: "generating", error: "" });
    await api.streamCompareAssetTTS(task().id, {
      workflow_id: `storyboard_scene_tts_${scene.scene_id}`,
      shot_id: shot?.shot_id || scene.marks?.[0]?.shot_id || "",
      scene_mark_id: scene.scene_id,
      srt_text: text,
      use_locked_cache: true,
      locked_output: locked.output,
      locked_manifest: locked.manifest,
      locked_config_key: configKey,
      prompts: [{
        provider: settings.provider,
        model: settings.model,
        voice_id: settings.voiceId,
        prompt: applyTtsTextToPrompt(settings.prompt, settings.provider, text),
        text,
        user_instruction: "StoryBoard Scene 级声音生成，严格按 Scene 文本朗读。",
        tempo: positiveNumber(settings.tempo),
        target_duration: null,
        fit_to_duration: false,
      }],
    }, (event) => {
      if (event.type === "completed") {
        const audioVersion = Date.now();
        completed = {
          phase: "ready",
          audioSrc: `${resourceUrl(event.output)}?v=${audioVersion}`,
          audioVersion,
          output: event.output,
          provider: event.provider,
          model: event.model,
          voiceId: event.voice_id || settings.voiceId,
          durationSeconds: event.duration_seconds ?? event.duration ?? 0,
          rawDuration: event.raw_duration,
          tempo: event.tempo || settings.tempo,
          configKey,
          error: "",
          cacheHit: Boolean(event.cache_hit),
        };
        updateSceneAudioState(scene.scene_id, completed);
      }
      if (event.type === "failed") {
        failed = event.detail || "TTS 生成失败";
        updateSceneAudioState(scene.scene_id, { phase: "error", error: failed });
      }
    });
    if (!completed) throw new Error(failed || "TTS 没有返回可播放音频");
    return completed;
  }

  function playAudioSource(src) {
    if (!src) return Promise.reject(new Error("没有可播放的音频"));
    if (storyboardAudio) storyboardAudio.pause();
    storyboardAudio = new Audio(src);
    storyboardAudio.playbackRate = playbackSpeed();
    return new Promise((resolve, reject) => {
      storyboardAudio.onended = () => resolve(storyboardAudio.duration || 0);
      storyboardAudio.onerror = () => reject(new Error("音频播放失败"));
      void storyboardAudio.play().catch(reject);
    });
  }

  async function startTimelinePlayback() {
    const queue = selectedScenesForPlayback();
    if (!queue.length) return;
    const runId = playbackRunId + 1;
    playbackRunId = runId;
    try {
      for (let index = 0; index < queue.length; index += 1) {
        if (runId !== playbackRunId) return;
        const { shot, scene } = queue[index];
        const progress = `${index + 1}/${queue.length}`;
        setPlaybackState({ phase: "generating", status: progress, currentShotId: shot?.shot_id || "", currentSceneId: scene.scene_id || "", progressIndex: index + 1, progressTotal: queue.length });
        const audio = await generateSceneAudio(shot, scene);
        if (runId !== playbackRunId) return;
        setPlaybackState({ phase: "playing", status: progress, currentShotId: shot?.shot_id || "", currentSceneId: scene.scene_id || "", progressIndex: index + 1, progressTotal: queue.length });
        const duration = await playAudioSource(audio.audioSrc);
        if (runId !== playbackRunId) return;
        applySceneAudioDuration(scene.scene_id, audio.durationSeconds || duration);
      }
      if (runId === playbackRunId) setPlaybackState({ phase: "idle", status: "", currentShotId: "", currentSceneId: "", progressIndex: 0, progressTotal: 0 });
    } catch (err) {
      console.error(err);
      if (runId === playbackRunId) setPlaybackState({ phase: "error", status: "", currentShotId: "", currentSceneId: "", progressIndex: 0, progressTotal: 0 });
    }
  }

  async function toggleTimelinePlayback() {
    const phase = playbackState().phase;
    if (phase === "playing") {
      storyboardAudio?.pause();
      setPlaybackState((previous) => ({ ...previous, phase: "paused" }));
      return;
    }
    if (phase === "paused") {
      if (storyboardAudio) {
        storyboardAudio.playbackRate = playbackSpeed();
        await storyboardAudio.play();
        setPlaybackState((previous) => ({ ...previous, phase: "playing" }));
      }
      return;
    }
    if (phase === "generating") {
      playbackRunId += 1;
      storyboardAudio?.pause();
      setPlaybackState({ phase: "idle", status: "", currentShotId: "", currentSceneId: "", progressIndex: 0, progressTotal: 0 });
      return;
    }
    await startTimelinePlayback();
  }

  function selectFullTimeline() {
    setTimelineSelectionScope("all");
    setSelectedSceneId("");
  }

  function scrollToStoryboardNode(id) {
    const node = document.getElementById(id);
    const container = node?.closest?.(".ocsb-shot-scroll");
    if (!node || !container) return;
    const pinBelowShotTitle = id.startsWith("ocsb-dialogue-") || id.startsWith("ocsb-scene-");
    const shotCard = pinBelowShotTitle ? node.closest(".ocsb-shot-card") : null;
    const stickyTitle = shotCard?.querySelector?.(".ocsb-shot-sticky");
    const titleRow = stickyTitle?.querySelector?.(".ocsb-shot-title");
    const nodeRect = node.getBoundingClientRect();
    const containerRect = container.getBoundingClientRect();
    const titleOffset = stickyTitle
      ? stickyTitle.offsetHeight + 8
      : titleRow?.offsetHeight || 0;
    const targetTop = pinBelowShotTitle && titleOffset ? containerRect.top + titleOffset : containerRect.top;
    const nextTop = container.scrollTop + nodeRect.top - targetTop;
    const maxTop = Math.max(0, container.scrollHeight - container.clientHeight);
    container.scrollTo({ top: Math.min(Math.max(0, nextTop), maxTop), behavior: "smooth" });
  }

  async function runAction(name, action) {
    setBusy(name);
    setError("");
    try {
      return await action();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      throw err;
    } finally {
      setBusy("");
    }
  }

  async function loadList() {
    const result = await runAction("list", () => api.tasks());
    setItems(result.items || []);
  }

  async function loadDetail(taskId) {
    const result = await runAction("load", () => api.detail(taskId));
    setTimingModel(result.timing_model || null);
    applyBuilderGTimings(result.shot_plan, result.timing_model || null);
    normalizeStoryboardPlan(result.shot_plan);
    setState({ task: result.task, meta: result.meta });
    setShotPlan(result.shot_plan);
    setAssetPool(result.asset_pool || []);
    setConsumedAssetIds(new Set());
    setSelectedShotIndex(0);
    const firstScene = result.shot_plan?.shots?.[0]?.reference?.scene_marks?.[0];
    setSelectedSceneId(firstScene?.scene_mark_id || "");
    setDirty(false);
  }

  async function saveStoryboard() {
    if (!task() || !shotPlan()) return;
    const plan = normalizedDialogueImagePlan(shotPlan());
    const result = await runAction("save", () => api.save(task().id, plan));
    setState({ task: result.task, meta: result.meta });
    setShotPlan(result.shot_plan);
    setAssetPool(result.asset_pool || []);
    setTimingModel(result.timing_model || null);
    setConsumedAssetIds(new Set());
    setDirty(false);
  }

  async function refreshTaskToPhase2() {
    if (!task()) return;
    if (dirty() && shotPlan()) {
      const plan = normalizedDialogueImagePlan(shotPlan());
      const saved = await runAction("save", () => api.save(task().id, plan));
      setState({ task: saved.task, meta: saved.meta });
      setShotPlan(saved.shot_plan);
      setAssetPool(saved.asset_pool || []);
      setTimingModel(saved.timing_model || null);
      setConsumedAssetIds(new Set());
      setDirty(false);
    }
    const result = await runAction("phase2-refresh", () => api.refreshPhase2(task().id));
    setState({ task: result.task, meta: result.meta });
    setShotPlan(result.shot_plan);
    setAssetPool(result.asset_pool || []);
    setTimingModel(result.timing_model || null);
    setConsumedAssetIds(new Set());
    setDirty(false);
    window.location.hash = `#/ocrebuild/tasks/${task().id}`;
  }

  function openCopiedRebuildTask(taskId) {
    const id = Number(taskId || 0);
    if (!id) return;
    window.location.hash = `#/ocrebuild/tasks/${id}`;
  }

  function updatePlan(mutator) {
    const next = clone(shotPlan());
    mutator(next);
    normalizeStoryboardPlan(next);
    setShotPlan(next);
    setDirty(true);
  }

  function updateShotField(shotId, key, value) {
    updatePlan((plan) => {
      const shot = (plan.shots || []).find((item) => item.shot_id === shotId);
      if (!shot) return;
      shot[key] = key === "shot_name" ? String(value || shot.shot_id || "").trim() || shot.shot_id : value;
    });
  }

  function normalizedDialogueImagePlan(plan) {
    const next = clone(plan);
    for (const shot of next.shots || []) {
      const marks = sceneMarks(shot);
      marks.forEach((mark, index) => {
        const asset = dialogueBoundAsset(mark);
        if (!asset?.path) return;
        mark.keyframes = { single: asset.path, first: "", last: "", paths: [asset.path] };
        mark.plan_a = { ...(mark.plan_a || {}), scene_asset: { ...(mark.plan_a?.scene_asset || {}), selected_image: asset.path, source: asset.source || "storyboard_dialogue_asset", asset_id: assetIdentity(asset) } };
        mark.generation_mode = "first_frame";
        mark.mode = "single";
      });
    }
    return next;
  }

  function updateSceneField(sceneId, key, value) {
    if (key === "srt_text") {
      const plan = shotPlan();
      if (!plan) return;
      for (const shot of plan.shots || []) {
        const mark = sceneMarks(shot).find((item) => item.scene_mark_id === sceneId);
        if (!mark) continue;
        mark.srt_text = value;
        mark.source_srt_text ||= value;
        shot.reference.srt_text = sceneMarks(shot).map((item) => item.srt_text || "").join(" ");
        shot.ui_summary = { ...(shot.ui_summary || {}), summary: shot.reference.srt_text };
        setDirty(true);
        return;
      }
    }
    updatePlan((plan) => {
      for (const shot of plan.shots || []) {
        const mark = sceneMarks(shot).find((item) => item.scene_mark_id === sceneId);
        if (!mark) continue;
        mark[key] = key === "duration" || key === "start" || key === "end" ? Number(value || 0) : value;
        if (key === "duration") {
          mark.end = Number((Number(mark.start || 0) + Number(mark.duration || 0)).toFixed(3));
          shot.duration = sceneMarks(shot).reduce((sum, item) => sum + Number(item.duration || 0), 0);
          shot.reference.duration = shot.duration;
        }
        if (key === "srt_text") {
          mark.source_srt_text ||= value;
          shot.reference.srt_text = sceneMarks(shot).map((item) => item.srt_text || "").join(" ");
          shot.ui_summary = { ...(shot.ui_summary || {}), summary: shot.reference.srt_text };
        }
        return;
      }
    });
  }

  function splitScene(shotId, sceneId) {
    updatePlan((plan) => {
      const shot = (plan.shots || []).find((item) => item.shot_id === shotId);
      const marks = sceneMarks(shot);
      const index = marks.findIndex((item) => item.scene_mark_id === sceneId);
      if (!shot || index < 0) return;
      const currentSceneId = marks[index].scene_id || String(marks[index].scene_mark_id || "").replace(/_dialogue_\d+$/, "");
      const nextId = nextSceneId(shot);
      const moved = marks.slice(index + 1).filter((mark) => (mark.scene_id || String(mark.scene_mark_id || "").replace(/_dialogue_\d+$/, "")) === currentSceneId);
      if (!moved.length) return;
      moved.forEach((mark, movedIndex) => {
        mark.scene_id = nextId;
        mark.scene_index = Number(marks[index].scene_index || 1) + 1;
        mark.dialogue_index = movedIndex + 1;
        mark.dialogue_id = `${nextId}_dialogue_${String(movedIndex + 1).padStart(3, "0")}`;
        mark.scene_mark_id = movedIndex === 0 ? nextId : mark.dialogue_id;
        mark.boundary_source = "storyboard_manual_split";
      });
      setSelectedSceneId(moved[0]?.scene_mark_id || nextId);
    });
  }

  function mergeSceneUp(shotId, sceneId) {
    updatePlan((plan) => {
      const shot = (plan.shots || []).find((item) => item.shot_id === shotId);
      const marks = sceneMarks(shot);
      const index = marks.findIndex((item) => item.scene_mark_id === sceneId);
      if (!shot || index <= 0) return;
      const currentSceneId = marks[index].scene_id || String(marks[index].scene_mark_id || "").replace(/_dialogue_\d+$/, "");
      let previousSceneId = "";
      for (let i = index - 1; i >= 0; i -= 1) {
        const candidate = marks[i].scene_id || String(marks[i].scene_mark_id || "").replace(/_dialogue_\d+$/, "");
        if (candidate && candidate !== currentSceneId) {
          previousSceneId = candidate;
          break;
        }
      }
      if (!previousSceneId) return;
      marks.forEach((mark) => {
        const markSceneId = mark.scene_id || String(mark.scene_mark_id || "").replace(/_dialogue_\d+$/, "");
        if (markSceneId === currentSceneId) mark.scene_id = previousSceneId;
      });
      setSelectedSceneId(previousSceneId);
    });
  }

  function splitShot(shotId, sceneId) {
    updatePlan((plan) => {
      const index = (plan.shots || []).findIndex((item) => item.shot_id === shotId);
      const shot = plan.shots?.[index];
      const marks = sceneMarks(shot);
      const sceneIndex = marks.findIndex((item) => item.scene_mark_id === sceneId);
      if (!shot || sceneIndex < 0 || sceneIndex >= marks.length - 1) return;
      const newShot = clone(shot);
      newShot.shot_id = `shot_${String((plan.shots || []).length + 1).padStart(3, "0")}`;
      newShot.source_index = (plan.shots || []).length + 1;
      const moved = marks.splice(sceneIndex + 1);
      if (!moved.length) return;
      const sceneIdMap = new Map();
      newShot.reference.scene_marks = moved.map((mark) => {
        const oldSceneId = mark.scene_id || mark.scene_mark_id;
        if (!sceneIdMap.has(oldSceneId)) sceneIdMap.set(oldSceneId, `${newShot.shot_id}_scene_${String(sceneIdMap.size + 1).padStart(3, "0")}`);
        const nextSceneId = sceneIdMap.get(oldSceneId);
        const dialogueIndex = moved.filter((item) => (item.scene_id || item.scene_mark_id) === oldSceneId).indexOf(mark) + 1;
        return { ...mark, shot_id: newShot.shot_id, scene_id: nextSceneId, scene_index: sceneIdMap.size, dialogue_index: dialogueIndex, dialogue_id: `${nextSceneId}_dialogue_${String(dialogueIndex).padStart(3, "0")}`, scene_mark_id: `${nextSceneId}_dialogue_${String(dialogueIndex).padStart(3, "0")}` };
      });
      shot.duration = marks.reduce((sum, mark) => sum + Number(mark.duration || 0), 0);
      newShot.duration = newShot.reference.scene_marks.reduce((sum, mark) => sum + Number(mark.duration || 0), 0);
      newShot.reference.srt_text = newShot.reference.scene_marks.map((mark) => mark.srt_text || "").join(" ");
      shot.reference.srt_text = marks.map((mark) => mark.srt_text || "").join(" ");
      plan.shots.splice(index + 1, 0, newShot);
      plan.shots.forEach((item, itemIndex) => { item.source_index = itemIndex + 1; });
      setSelectedShotIndex(index + 1);
      setSelectedSceneId(newShot.reference.scene_marks[0]?.scene_mark_id || "");
    });
  }

  function mergeDialogueUp(shotId, sceneId) {
    updatePlan((plan) => {
      const shot = (plan.shots || []).find((item) => item.shot_id === shotId);
      const marks = sceneMarks(shot);
      const index = marks.findIndex((item) => item.scene_mark_id === sceneId);
      if (!shot || index <= 0) return;
      const current = marks[index];
      const previous = marks[index - 1];
      const currentScene = current.scene_id || String(current.scene_mark_id || "").replace(/_dialogue_\d+$/, "");
      const previousScene = previous.scene_id || String(previous.scene_mark_id || "").replace(/_dialogue_\d+$/, "");
      if (currentScene !== previousScene) return;
      previous.srt_text = [previous.srt_text, current.srt_text].map((text) => String(text || "").trim()).filter(Boolean).join(" ");
      previous.duration = Number((Number(previous.duration || 0) + Number(current.duration || 0)).toFixed(3));
      if (!previous.storyboard_dialogue_asset && current.storyboard_dialogue_asset) {
        previous.storyboard_dialogue_asset = current.storyboard_dialogue_asset;
      }
      previous.end = Number((Number(previous.start || 0) + Number(previous.duration || 0)).toFixed(3));
      marks.splice(index, 1);
      setSelectedSceneId(previous.scene_mark_id);
    });
  }

  function mergeShotUp(shotIndex) {
    updatePlan((plan) => {
      if (shotIndex <= 0 || !plan.shots?.[shotIndex]) return;
      const prev = plan.shots[shotIndex - 1];
      const current = plan.shots[shotIndex];
      const prevMarks = sceneMarks(prev);
      const existingSceneIds = new Set(prevMarks.map((mark) => mark.scene_id || mark.scene_mark_id).filter(Boolean));
      let sceneOffset = existingSceneIds.size;
      const sceneIdMap = new Map();
      const currentMarks = sceneMarks(current).map((mark) => {
        const oldSceneId = mark.scene_id || mark.scene_mark_id;
        if (!sceneIdMap.has(oldSceneId)) {
          sceneOffset += 1;
          sceneIdMap.set(oldSceneId, `${prev.shot_id}_scene_${String(sceneOffset).padStart(3, "0")}`);
        }
        const nextSceneId = sceneIdMap.get(oldSceneId);
        const dialogueIndex = sceneMarks(current).filter((item) => (item.scene_id || item.scene_mark_id) === oldSceneId).indexOf(mark) + 1;
        return { ...mark, shot_id: prev.shot_id, scene_id: nextSceneId, dialogue_index: dialogueIndex, dialogue_id: `${nextSceneId}_dialogue_${String(dialogueIndex).padStart(3, "0")}`, scene_mark_id: `${nextSceneId}_dialogue_${String(dialogueIndex).padStart(3, "0")}` };
      });
      prev.reference.scene_marks = [...prevMarks, ...currentMarks].map((mark, index) => ({ ...mark, scene_index: index + 1 }));
      prev.duration = prev.reference.scene_marks.reduce((sum, mark) => sum + Number(mark.duration || 0), 0);
      prev.reference.srt_text = prev.reference.scene_marks.map((mark) => mark.srt_text || "").join(" ");
      plan.shots.splice(shotIndex, 1);
      plan.shots.forEach((item, itemIndex) => { item.source_index = itemIndex + 1; });
      setSelectedShotIndex(shotIndex - 1);
      setSelectedSceneId(prev.reference.scene_marks[0]?.scene_mark_id || "");
    });
  }

  function addDialogueAfter(shotId, sceneId) {
    updatePlan((plan) => {
      const shot = (plan.shots || []).find((item) => item.shot_id === shotId);
      const marks = sceneMarks(shot);
      const index = marks.findIndex((item) => item.scene_mark_id === sceneId);
      if (!shot || index < 0) return;
      const current = marks[index];
      const start = Number(current.end || (Number(current.start || 0) + Number(current.duration || 0)));
      const sceneIdValue = current.scene_id || current.scene_mark_id || `${shot.shot_id}_scene_${String(current.scene_index || 1).padStart(3, "0")}`;
      const dialogueIndex = marks.filter((mark) => (mark.scene_id || mark.scene_mark_id) === sceneIdValue).length + 1;
      const next = makeSceneMark(current, shot.shot_id, index + 1, start, 0.2, "", "storyboard_add_dialogue");
      next.scene_id = sceneIdValue;
      next.scene_index = current.scene_index || 1;
      next.dialogue_index = dialogueIndex;
      next.dialogue_id = `${sceneIdValue}_dialogue_${String(dialogueIndex).padStart(3, "0")}`;
      next.scene_mark_id = next.dialogue_id;
      next.start = start;
      next.end = start;
      next.duration = 0;
      next.srt_text = "";
      next.source_srt_text = "";
      next.original_srt_text = "";
      next.keyframes = { single: "", first: "", last: "", paths: [] };
      delete next.keyframe_asset_ids;
      delete next.storyboard_dialogue_asset;
      delete next.plan_d;
      if (next.plan_a?.scene_asset) delete next.plan_a.scene_asset.selected_image;
      if (next.plan_a?.scene_asset) delete next.plan_a.scene_asset.asset_id;
      marks.splice(index + 1, 0, next);
      shot.duration = marks.reduce((sum, mark) => sum + Number(mark.duration || 0), 0);
      shot.reference.srt_text = marks.map((mark) => mark.srt_text || "").join(" ");
      setSelectedSceneId(next.scene_mark_id);
    });
  }

  function reorganizeFixedStoryboard() {
    const targetShot = Math.max(1, Number(fixedShotSeconds() || 16));
    const targetScene = Math.max(0.2, Number(fixedSceneSeconds() || 4));
    updatePlan((plan) => {
      reorganizePlanByFixedTiming(plan, { targetShot, targetScene, timingInfoForDialogue, timingModel });
      setSelectedShotIndex(0);
      setSelectedSceneId(plan.shots?.[0]?.reference?.scene_marks?.[0]?.scene_mark_id || "");
      setFixedMenuOpen(false);
      setTimingMenuOpen(false);
    });
  }

  function aiReorganizeStoryboard() {
    const prompt = aiPrompt().trim();
    if (!prompt) return;
    updatePlan((plan) => {
      for (const shot of plan.shots || []) {
        const baseMarks = sceneMarks(shot);
        const nextMarks = [];
        let cursor = Number(shot.start || shot.reference?.start || 0);
        baseMarks.forEach((mark) => {
          const parts = splitDialogueText(mark.srt_text || "");
          if (parts.length <= 1) {
            const duration = Number(mark.duration || 0) || 1;
            nextMarks.push(makeSceneMark(mark, shot.shot_id, nextMarks.length, cursor, duration, mark.srt_text || "", "storyboard_ai_prompt_split"));
            cursor += duration;
            return;
          }
          const unit = Math.max(0.2, Number(mark.duration || parts.length) / parts.length);
          parts.forEach((part) => {
            nextMarks.push(makeSceneMark(mark, shot.shot_id, nextMarks.length, cursor, unit, part, "storyboard_ai_prompt_split"));
            cursor += unit;
          });
        });
        shot.reference.scene_marks = nextMarks.length ? nextMarks : baseMarks;
        shot.duration = shot.reference.scene_marks.reduce((sum, item) => sum + Number(item.duration || 0), 0);
        shot.reference.srt_text = shot.reference.scene_marks.map((item) => item.srt_text || "").join(" ");
        shot.ui_summary = { ...(shot.ui_summary || {}), summary: shot.reference.srt_text };
      }
      setSelectedShotIndex(0);
      setSelectedSceneId(plan.shots?.[0]?.reference?.scene_marks?.[0]?.scene_mark_id || "");
      setAiMenuOpen(false);
    });
  }

  function dragAsset(event, item) {
    event.stopPropagation();
    window.getSelection?.()?.removeAllRanges();
    event.dataTransfer.setData("application/json", JSON.stringify(item));
    event.dataTransfer.setData("text/plain", JSON.stringify(item));
    event.dataTransfer.effectAllowed = "copy";
    if (event.currentTarget) event.dataTransfer.setDragImage(event.currentTarget, event.currentTarget.clientWidth / 2, event.currentTarget.clientHeight / 2);
  }

  function dropTargetFromPoint(x, y) {
    const node = document.elementFromPoint(x, y);
    return node?.closest?.("[data-storyboard-drop='true']") || null;
  }

  function beginPointerAssetDrag(event, item) {
    if (event.button !== 0 || !item?.path) return;
    activeAssetDragCleanup?.();
    window.getSelection?.()?.removeAllRanges();
    setSelectedAsset(item);
    const startX = event.clientX;
    const startY = event.clientY;
    let moved = false;
    let finished = false;

    const cleanup = () => {
      window.removeEventListener("pointermove", onMove, true);
      window.removeEventListener("pointerup", onUp, true);
      window.removeEventListener("mousemove", onMove, true);
      window.removeEventListener("mouseup", onUp, true);
      document.body.classList.remove("ocsb-dragging-asset");
      if (activeAssetDragCleanup === cleanup) activeAssetDragCleanup = null;
    };

    const onMove = (moveEvent) => {
      const distance = Math.hypot(moveEvent.clientX - startX, moveEvent.clientY - startY);
      if (distance < 8) return;
      moved = true;
      document.body.classList.add("ocsb-dragging-asset");
      moveEvent.preventDefault();
    };

    const onUp = (upEvent) => {
      if (finished) return;
      finished = true;
      cleanup();
      if (!moved) return;
      const target = dropTargetFromPoint(upEvent.clientX, upEvent.clientY);
      const sceneId = target?.getAttribute("data-scene-id");
      const role = target?.getAttribute("data-role") || "display";
      if (sceneId) {
        assignAssetToScene(item, sceneId, role);
        suppressNextAssetClick = true;
        window.setTimeout(() => { suppressNextAssetClick = false; }, 0);
        upEvent.preventDefault();
        upEvent.stopPropagation();
      }
    };

    window.addEventListener("pointermove", onMove, true);
    window.addEventListener("pointerup", onUp, true);
    window.addEventListener("mousemove", onMove, true);
    window.addEventListener("mouseup", onUp, true);
    activeAssetDragCleanup = cleanup;
  }

  function allowAssetDrop(event) {
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = "copy";
  }

  function startLeftResize(event) {
    event.preventDefault();
    setLeftResizeState({ startX: event.clientX, startWidth: leftPanelWidth() });
  }

  function assignAssetToScene(asset, sceneId, role) {
    if (!asset?.path) return;
    const assignedId = assetIdentity(asset);
    updatePlan((plan) => {
      for (const shot of plan.shots || []) {
        const mark = sceneMarks(shot).find((item) => item.scene_mark_id === sceneId);
        if (!mark) continue;
        mark.keyframes = { ...(mark.keyframes || {}) };
        if (role === "display") {
          mark.storyboard_dialogue_asset = { path: asset.path, source: asset.source || "storyboard_asset_pool", resource_session_id: asset.resource_session_id, asset_id: assetIdentity(asset), scene_mark_id: asset.scene_mark_id || sceneId, role: "display" };
        } else {
          mark.keyframes[role] = asset.path;
          mark.keyframe_asset_ids = { ...(mark.keyframe_asset_ids || {}), [role]: assetIdentity(asset) };
          mark.keyframes.paths = Array.from(new Set([mark.keyframes.single, mark.keyframes.first, mark.keyframes.last].filter(Boolean)));
          mark.generation_mode = role === "last" || mark.keyframes.last ? "first_last" : "first_frame";
          mark.mode = mark.generation_mode === "first_last" ? "first_last" : "single";
          mark.plan_a = { ...(mark.plan_a || {}), scene_confirmed: true, first_last_confirmed: true };
        }
        return;
      }
    });
    if (assignedId) {
      setConsumedAssetIds((previous) => {
        const next = new Set(previous);
        next.add(assignedId);
        return next;
      });
    }
    setSelectedAsset(null);
  }

  function dropAsset(event, sceneId, role) {
    event.preventDefault();
    event.stopPropagation();
    window.getSelection?.()?.removeAllRanges();
    const raw = event.dataTransfer.getData("application/json") || event.dataTransfer.getData("text/plain");
    if (!raw) return;
    try {
      assignAssetToScene(JSON.parse(raw), sceneId, role);
    } catch {
      return;
    }
  }

  function clickAsset(item) {
    if (suppressNextAssetClick) return;
    setSelectedAsset(item);
    window.getSelection?.()?.removeAllRanges();
  }

  function clearSceneImage(sceneId) {
    let clearedAssetId = "";
    updatePlan((plan) => {
      for (const shot of plan.shots || []) {
        const mark = sceneMarks(shot).find((item) => item.scene_mark_id === sceneId);
        if (!mark) continue;
        clearedAssetId = mark.storyboard_dialogue_asset?.asset_id || "";
        delete mark.storyboard_dialogue_asset;
        return;
      }
    });
    if (clearedAssetId) {
      setConsumedAssetIds((previous) => {
        const next = new Set(previous);
        next.delete(clearedAssetId);
        return next;
      });
    }
  }

  function deleteDialogue(shotId, sceneId) {
    updatePlan((plan) => {
      const shot = (plan.shots || []).find((item) => item.shot_id === shotId);
      const marks = sceneMarks(shot);
      const index = marks.findIndex((item) => item.scene_mark_id === sceneId);
      if (!shot || index < 0 || marks.length <= 1) return;
      marks.splice(index, 1);
      const sceneOrder = new Map();
      const dialogueCounts = new Map();
      marks.forEach((mark, markIndex) => {
        const previousId = mark.scene_id || String(mark.scene_mark_id || "").replace(/_dialogue_\d+$/, "") || `${shot.shot_id}_scene_${String(markIndex + 1).padStart(3, "0")}`;
        if (!sceneOrder.has(previousId)) sceneOrder.set(previousId, sceneOrder.size + 1);
        const sceneIndex = sceneOrder.get(previousId);
        const sceneIdValue = `${shot.shot_id}_scene_${String(sceneIndex).padStart(3, "0")}`;
        const dialogueIndex = (dialogueCounts.get(sceneIdValue) || 0) + 1;
        dialogueCounts.set(sceneIdValue, dialogueIndex);
        mark.shot_id = shot.shot_id;
        mark.scene_id = sceneIdValue;
        mark.scene_index = sceneIndex;
        mark.dialogue_index = dialogueIndex;
        mark.dialogue_id = `${sceneIdValue}_dialogue_${String(dialogueIndex).padStart(3, "0")}`;
        mark.scene_mark_id = dialogueIndex === 1 ? sceneIdValue : mark.dialogue_id;
      });
      shot.duration = marks.reduce((sum, mark) => sum + Number(mark.duration || 0), 0);
      shot.reference.duration = shot.duration;
      shot.reference.srt_text = marks.map((mark) => mark.srt_text || "").join(" ");
      shot.ui_summary = { ...(shot.ui_summary || {}), summary: shot.reference.srt_text };
      const next = marks[Math.min(index, marks.length - 1)];
      setSelectedSceneId(next?.scene_mark_id || "");
    });
  }

  async function submitBlank(event) {
    event.preventDefault();
    const formEl = event.currentTarget;
    const form = new FormData(formEl);
    const result = await runAction("blank", () => api.blank(form));
    setState({ task: result.task, meta: result.meta });
    setShotPlan(result.shot_plan);
    setAssetPool(result.asset_pool || []);
    setTimingModel(result.timing_model || null);
    window.location.hash = `#/ocstoryboard/tasks/${result.task.id}`;
  }

  async function submitJson(event) {
    event.preventDefault();
    const formEl = event.currentTarget;
    const form = new FormData(formEl);
    const result = await runAction("json", () => api.jsonImport(form));
    setState({ task: result.task, meta: result.meta });
    setShotPlan(result.shot_plan);
    setAssetPool(result.asset_pool || []);
    setTimingModel(result.timing_model || null);
    window.location.hash = `#/ocstoryboard/tasks/${result.task.id}`;
  }

  async function uploadStoryboardAssets(files) {
    if (!task() || !files?.length) return;
    const form = new FormData();
    Array.from(files).forEach((file) => form.append("files", file));
    setAssetUploadBusy(true);
    try {
      const result = await runAction("asset-upload", () => api.uploadAssets(task().id, form));
      setState({ task: result.task, meta: result.meta });
      setShotPlan(result.shot_plan);
      setAssetPool(result.asset_pool || []);
      setTimingModel(result.timing_model || null);
    } finally {
      setAssetUploadBusy(false);
    }
  }

  async function deleteStoryboardAsset(item) {
    if (!task() || !item?.id) return;
    const identity = assetIdentity(item);
    setDeletingAssetId(String(item.id));
    try {
      const result = await runAction("asset-delete", () => api.deleteAsset(task().id, item.id));
      setState({ task: result.task, meta: result.meta });
      setShotPlan(result.shot_plan);
      setAssetPool(result.asset_pool || []);
      setTimingModel(result.timing_model || null);
      if (selectedAsset() && assetIdentity(selectedAsset()) === identity) setSelectedAsset(null);
      setConsumedAssetIds(new Set());
    } finally {
      setDeletingAssetId("");
    }
  }

  async function deleteStoryboardTask(event, item) {
    event.preventDefault();
    event.stopPropagation();
    const taskId = Number(item?.task?.id || 0);
    if (!taskId) return;
    const confirmed = window.confirm(`Delete StoryBoard Task #${taskId}? This will remove its StoryBoard session and workspace.`);
    if (!confirmed) return;
    await runAction(`delete-${taskId}`, () => api.deleteTask(taskId));
    setItems((previous) => previous.filter((entry) => Number(entry?.task?.id || 0) !== taskId));
  }

  const renderDetail = () => <div class={`ocsb-editor ${storyTheme() === "dark" ? "is-dark" : ""}`}>
    <div class="ocsb-main-row">
      <StoryboardSidebar
        shots={shots}
        selectedShotIndex={selectedShotIndex}
        selectedSceneId={selectedSceneId}
        setSelectedShotIndex={setSelectedShotIndex}
        setSelectedSceneId={setSelectedSceneId}
        storyTheme={storyTheme}
        setStoryTheme={setStoryTheme}
        leftPanelWidth={leftPanelWidth}
        startLeftResize={startLeftResize}
        scrollToStoryboardNode={scrollToStoryboardNode}
        shotDisplayName={shotDisplayName}
        updateShotField={updateShotField}
      />
      <main class="ocsb-workspace">
        <div class="ocsb-workspace-inner">
          <header class="ocsb-workspace-head">
            <div>
              <h1>Timeline Editor</h1>
              <SourceMeta task={task} meta={meta} busy={busy} refreshTaskToPhase2={refreshTaskToPhase2} openCopiedRebuildTask={openCopiedRebuildTask} />
            </div>
            <div class="ocsb-head-actions">
              <TimingMenu
                timingModel={timingModel}
                timingMenuOpen={timingMenuOpen}
                setTimingMenuOpen={setTimingMenuOpen}
                buildGSecondsPerChar={buildGSecondsPerChar}
                refreshDialogueTimingsOnly={refreshDialogueTimingsOnly}
                openAudioSettings={openAudioSettings}
                audioSettings={audioSettings}
                saveAudioSettings={saveAudioSettings}
                ttsProviderOptions={ttsProviderOptions}
                ttsModelsForProvider={ttsModelsForProvider}
                ttsVoicesForModel={ttsVoicesForModel}
              />
              <FixedMenu fixedMenuOpen={fixedMenuOpen} setFixedMenuOpen={setFixedMenuOpen} fixedShotSeconds={fixedShotSeconds} setFixedShotSeconds={setFixedShotSeconds} fixedSceneSeconds={fixedSceneSeconds} setFixedSceneSeconds={setFixedSceneSeconds} reorganizeFixedStoryboard={reorganizeFixedStoryboard} />
              <AiMenu aiMenuOpen={aiMenuOpen} setAiMenuOpen={setAiMenuOpen} aiPrompt={aiPrompt} setAiPrompt={setAiPrompt} aiReorganizeStoryboard={aiReorganizeStoryboard} />
              <SaveButton busy={busy} dirty={dirty} saveStoryboard={saveStoryboard} />
            </div>
          </header>
          <Show when={shots().length} fallback={<div class="ocsb-empty">No Shot selected.</div>}>
            <div class="ocsb-shot-scroll">
              <div class="ocsb-shot-stack"><For each={shots()}>{(shot, shotIndex) => <StoryboardShotCard
                shot={shot}
                shotIndex={shotIndex}
                selectedAsset={selectedAsset}
                selectedSceneId={selectedSceneId}
                editingSceneId={editingSceneId}
                setSelectedAsset={setSelectedAsset}
                setSelectedShotIndex={setSelectedShotIndex}
                setSelectedSceneId={setSelectedSceneId}
                setEditingSceneId={setEditingSceneId}
                task={task}
                meta={meta}
                timingInfoForDialogue={timingInfoForDialogue}
                shotDisplayName={shotDisplayName}
                updateShotField={updateShotField}
                updateSceneField={updateSceneField}
                assignAssetToScene={assignAssetToScene}
                allowAssetDrop={allowAssetDrop}
                dropAsset={dropAsset}
                clearSceneImage={clearSceneImage}
                deleteDialogue={deleteDialogue}
                mergeDialogueUp={mergeDialogueUp}
                mergeSceneUp={mergeSceneUp}
                splitScene={splitScene}
                splitShot={splitShot}
                addDialogueAfter={addDialogueAfter}
                mergeShotUp={mergeShotUp}
                scrollToStoryboardNode={scrollToStoryboardNode}
                openImagePreview={openImagePreview}
              />}</For></div>
            </div>
          </Show>
        </div>
      </main>
    </div>
    <StoryboardTimeline
      shots={shots}
      totalDuration={totalDuration}
      selectedShotIndex={selectedShotIndex}
      selectedSceneId={selectedSceneId}
      setSelectedShotIndex={setSelectedShotIndex}
      setSelectedSceneId={setSelectedSceneId}
      scrollToStoryboardNode={scrollToStoryboardNode}
      timelineSelectionScope={timelineSelectionScope}
      setTimelineSelectionScope={setTimelineSelectionScope}
      selectFullTimeline={selectFullTimeline}
      playbackPhase={() => playbackState().phase}
      playbackStatus={() => playbackState().status}
      playbackCurrentShotId={() => playbackState().currentShotId || ""}
      playbackCurrentSceneId={() => playbackState().currentSceneId || ""}
      shotDisplayName={shotDisplayName}
      toggleTimelinePlayback={toggleTimelinePlayback}
      playbackSpeed={playbackSpeed}
      playbackSpeedOpen={playbackSpeedOpen}
      setPlaybackSpeedOpen={setPlaybackSpeedOpen}
      applyPlaybackSpeed={applyPlaybackSpeed}
    />
  </div>;

  createEffect(() => {
    if (route().view === "detail" && state()) props.onSidebarChange?.(<StoryboardAssetPool
      assetShotGroups={assetShotGroups}
      manualAssetItems={manualAssetItems}
      selectedAsset={selectedAsset}
      task={task}
      uploadBusy={assetUploadBusy}
      deletingAssetId={deletingAssetId}
      onUploadAssets={uploadStoryboardAssets}
      onDeleteAsset={deleteStoryboardAsset}
      beginPointerAssetDrag={beginPointerAssetDrag}
      clickAsset={clickAsset}
      dragAsset={dragAsset}
      formatTime={formatTime}
      onPreviewAsset={openImagePreview}
    />);
    else props.onSidebarChange?.(null);
  });

  onCleanup(() => props.onSidebarChange?.(null));

  return <div class="ocstoryboard-module">
    <Show when={error()}><div class="banner bad">{error()}</div></Show>
    <Show when={busy() === "load"}><div class="ocstoryboard-empty">Loading StoryBoard...</div></Show>
    <Show when={route().view === "list"}><StoryboardTaskList items={items} busy={busy} onDelete={deleteStoryboardTask} /></Show>
    <Show when={route().view === "new"}><StoryboardNewTask busy={busy} onSubmit={submitBlank} onJsonSubmit={submitJson} /></Show>
    <Show when={route().view === "detail" && state()}>{renderDetail()}</Show>
    <StoryboardImagePreview image={imagePreview} onClose={() => setImagePreview(null)} />
  </div>;
}
