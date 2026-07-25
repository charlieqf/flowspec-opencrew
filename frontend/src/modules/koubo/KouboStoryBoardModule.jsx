import { ErrorBoundary, For, Show, createEffect, createMemo, createSignal, onCleanup, onMount, untrack } from "solid-js";
import { getCachedKouboStoryboardDetail, kbApi, rememberKouboStoryboardDetail } from "./KouboStoryBoard/kouboStoryboardApi.js";
import { createKouboStoryboardPlaybackController } from "./KouboStoryBoard/kouboStoryboardPlayback.js";
import { createKouboStoryboardTtsController } from "./KouboStoryBoard/kouboStoryboardTts.js";
import { DEFAULT_COMPOSER_SETTINGS, createKouboStoryboardComposerController } from "./KouboStoryBoard/kouboStoryboardComposer.js";
import { createKouboStoryboardImagePlanController } from "./KouboStoryBoard/kouboStoryboardImagePlan.js";
import { createKouboStoryboardVideoOnlyPlanController } from "./KouboStoryBoard/kouboStoryboardVideoOnlyPlan.js";
import { DEFAULT_VIDEO_PLAN_SETTINGS, createKouboStoryboardVideoPlanController } from "./KouboStoryBoard/kouboStoryboardVideoPlan.js";
import { routeFromHash, locateStoryboardDialogue, copy, renumberPlan, allDialogues, shotDuration, dialogueText, dialogueCharCount, spokenCharCount, positiveNumber, newDialogueFields } from "./KouboStoryBoard/kouboStoryboardModel.js";
import { applyStoryboardEditCandidate, focusedStoryboardExcerpt } from "./KouboStoryBoard/kouboAgentChat.js";
import { assetKind } from "./KouboStoryBoard/kouboStoryboardAssets.js";
import { CodeIcon, DocumentIcon, PlayIcon, PlusIcon, RefreshIcon, TrashIcon, XIcon } from "./KouboStoryBoard/kouboStoryboardIcons.jsx";
import KouboTaskList from "./KouboStoryBoard/components/KouboTaskList.jsx";
import KouboSidebar from "./KouboStoryBoard/components/KouboSidebar.jsx";
import KouboEditorHeader from "./KouboStoryBoard/components/KouboEditorHeader.jsx";
import ShotCard from "./KouboStoryBoard/components/ShotCard.jsx";
import KouboTimeline from "./KouboStoryBoard/components/KouboTimeline.jsx";
import KouboVideoPlanModal from "./KouboStoryBoard/components/KouboVideoPlanModal.jsx";
import KouboImagePlanModal from "./KouboStoryBoard/components/KouboImagePlanModal.jsx";
import KouboVideoOnlyPlanModal from "./KouboStoryBoard/components/KouboVideoOnlyPlanModal.jsx";
import KouboComposerModal from "./KouboStoryBoard/components/KouboComposerModal.jsx";
import KouboAgentDrawer from "./KouboStoryBoard/components/KouboAgentDrawer.jsx";
import CleanImagePanel from "./KouboStoryBoard/components/CleanImagePanel.jsx";
import AssetPanel from "./KouboStoryBoard/components/AssetPanel.jsx";
import ImagePreview from "./KouboStoryBoard/components/ImagePreview.jsx";
import KouboHostProductBuilder from "./KouboStoryBoard/hostProduct/KouboHostProductBuilder.jsx";
import "./KouboStoryBoard/styles/index.css";

const ASSET_PANEL_TAB_KEY = "koubo-storyboard:asset-panel-tab";
const ASSET_PANEL_TABS = new Set(["source", "upload", "history"]);
const ANALYSIS_V1_TERMINAL_STATUSES = new Set(["completed", "completed_with_sync_error", "failed", "blocked", "cancelled", "stale_running", "error"]);
const SESSION_VARIABLES_PATH = "SessionContext/Variables.json";
const DANCE_MIMIC_WORKFLOW_ID = "dance_mimic_v1";

function initialAssetPanelTab() {
  try {
    const value = new URLSearchParams(window.location.search || "").get("assetPanelTab");
    if (ASSET_PANEL_TABS.has(value)) return value;
  } catch {
    // Fall back to localStorage below.
  }
  try {
    const value = window.localStorage?.getItem(ASSET_PANEL_TAB_KEY);
    return ASSET_PANEL_TABS.has(value) ? value : "source";
  } catch {
    return "source";
  }
}

function rememberAssetPanelTabInUrl(tab) {
  try {
    const url = new URL(window.location.href);
    url.searchParams.set("assetPanelTab", tab);
    window.history.replaceState(window.history.state, "", `${url.pathname}${url.search}${url.hash}`);
  } catch {
    // URL state is a convenience for refresh survival; local state still works.
  }
}

function sleep(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function stableHash(value) {
  let hash = 5381;
  const text = String(value || "");
  for (let index = 0; index < text.length; index += 1) hash = ((hash << 5) + hash) ^ text.charCodeAt(index);
  return (hash >>> 0).toString(36);
}

function mediaAssetTokensFromPlan(plan) {
  const tokens = [];
  const remember = (value) => {
    const text = String(value || "").trim();
    if (text) tokens.push(text);
  };
  for (const shot of plan?.shots || []) {
    remember(shot?.shot_id);
    for (const scene of shot?.scenes || []) {
      remember(scene?.scene_id);
      const sceneAssets = scene?.working_assets || {};
      remember(sceneAssets.audio?.path);
      remember(sceneAssets.video?.path);
      for (const image of sceneAssets.images || []) remember(image?.path);
      for (const dialogue of scene?.dialogues || []) {
        remember(dialogue?.dialogue_id);
        remember(dialogue?.dialogue_asset_key);
        remember(dialogue?.image_path);
        remember(dialogue?.bound_image_path);
        for (const path of dialogue?.source_image_paths || []) remember(path);
        const assets = dialogue?.working_assets || {};
        remember(assets.audio?.path);
        remember(assets.video?.path);
        for (const image of assets.images || []) remember(image?.path);
      }
    }
  }
  return tokens;
}

function mediaAssetTokensFromMeta(meta) {
  const tokens = [];
  const rememberAsset = (asset) => {
    if (!asset || typeof asset !== "object") return;
    tokens.push([
      asset.id,
      asset.path,
      asset.history_path,
      asset.kind || asset.asset_type,
      asset.source,
      asset.created_at,
      asset.updated_at,
      asset.duration_seconds || asset.duration,
    ].map((item) => String(item || "").trim()).join("|"));
  };
  for (const key of ["manual_assets", "uploaded_images", "uploaded_audios", "uploaded_videos"]) {
    for (const item of meta?.[key] || []) rememberAsset(item);
  }
  for (const group of meta?.source_asset_groups || []) {
    tokens.push([group?.shot_id, group?.duration].map((item) => String(item || "").trim()).join("|"));
    for (const item of group?.scenes || []) rememberAsset(item);
  }
  for (const item of meta?.history_versions || []) {
    tokens.push([item?.id, item?.version, item?.path, item?.created_at, item?.reason].map((value) => String(value || "").trim()).join("|"));
  }
  return tokens;
}

function buildMediaVersion(result, nextPlan) {
  const task = result?.task || {};
  const meta = result?.meta || {};
  const tokens = [
    task.id,
    task.updated_at,
    meta.source_storyboard_sha256,
    meta.source_schema_version,
    ...mediaAssetTokensFromMeta(meta),
    ...mediaAssetTokensFromPlan(nextPlan),
  ];
  return `m${stableHash(JSON.stringify(tokens))}`;
}

function prettyJson(value) {
  if (value === null || value === undefined) return "";
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function filenameFromPath(path) {
  return String(path || "").split(/[\\/]/).filter(Boolean).pop() || String(path || "");
}

function collectStoryboardSourceImages(shots, groupedAssets = []) {
  const byPath = new Map();
  const remember = (item, fallback = {}) => {
    const path = String(typeof item === "string" ? item : item?.path || "").trim();
    if (!path || byPath.has(path)) return;
    byPath.set(path, {
      ...fallback,
      ...(typeof item === "object" && item ? item : {}),
      path,
      kind: "image",
      source: "source",
      label: String(item?.label || fallback.label || filenameFromPath(path)).trim() || path,
    });
  };
  for (const group of groupedAssets || []) {
    for (const item of group?.scenes || []) {
      if (assetKind(item) === "image") remember(item, { shot_id: group.shot_id });
    }
  }
  for (const shot of shots || []) {
    for (const scene of shot.scenes || []) {
      const sceneText = (scene.dialogues || []).map((dialogue) => dialogueText(dialogue)).filter(Boolean).join(" ");
      const sceneAssets = scene.working_assets || {};
      for (const image of sceneAssets.images || []) remember(image, { shot_id: shot.shot_id, scene_id: scene.scene_id, text: sceneText });
      for (const dialogue of scene.dialogues || []) {
        const text = dialogueText(dialogue);
        const fallback = { shot_id: shot.shot_id, scene_id: scene.scene_id, dialogue_id: dialogue.dialogue_id, duration: dialogue.duration, text };
        for (const path of dialogue.source_image_paths || []) remember(path, fallback);
        remember(dialogue.image_path, fallback);
        remember(dialogue.bound_image_path, fallback);
        const dialogueAssets = dialogue.working_assets || {};
        for (const image of dialogueAssets.images || []) remember(image, fallback);
      }
    }
  }
  return Array.from(byPath.values()).filter((item) => assetKind(item) === "image");
}

export default function KouboStoryBoardModule(props) {
  const [route, setRoute] = createSignal(routeFromHash(props.routeHash));
  const [items, setItems] = createSignal([]);
  const [state, setState] = createSignal(null);
  const [plan, setPlan] = createSignal(null);
  const [selectedShotIndex, setSelectedShotIndex] = createSignal(0);
  const [selectedDialogueId, setSelectedDialogueId] = createSignal("");
  const [editingDialogueId, setEditingDialogueId] = createSignal("");
  const [selectedAsset, setSelectedAsset] = createSignal(null);
  const [theme, setTheme] = createSignal("light");
  const [dirty, setDirty] = createSignal(false);
  const [groupingDirty, setGroupingDirty] = createSignal(false);
  const [busy, setBusy] = createSignal("");
  const [error, setError] = createSignal("");
  const [routeNavigationNotice, setRouteNavigationNotice] = createSignal("");
  const [assetUploadStatus, setAssetUploadStatus] = createSignal({ tone: "", text: "" });
  const [mediaVersion, setMediaVersion] = createSignal("");
  const [leftWidth, setLeftWidth] = createSignal(256);
  const [resizeState, setResizeState] = createSignal(null);
  const [scope, setScope] = createSignal("scene");
  const [imagePreview, setImagePreview] = createSignal(null);
  const [hostProductBuilderOpen, setHostProductBuilderOpen] = createSignal(false);
  const [cleanImageOpen, setCleanImageOpen] = createSignal(false);
  const [cleanImageTarget, setCleanImageTarget] = createSignal(null);
  const [fixedMenuOpen, setFixedMenuOpen] = createSignal(false);
  const [timingMenuOpen, setTimingMenuOpen] = createSignal(false);
  const [fixedShotSeconds, setFixedShotSeconds] = createSignal(8);
  const [fixedSceneSeconds, setFixedSceneSeconds] = createSignal(4);
  const [timingSecondsPerChar, setTimingSecondsPerChar] = createSignal(0.18);
  const [timingModel, setTimingModel] = createSignal(null);
  const [ttsModelConfig, setTTSModelConfig] = createSignal(null);
  const [playbackSpeedOpen, setPlaybackSpeedOpen] = createSignal(false);
  const [playbackSpeed, setPlaybackSpeed] = createSignal(1);
  const [playbackState, setPlaybackState] = createSignal({ phase: "idle", status: "", currentShotId: "", currentSceneId: "" });
  const [videoPlanParamsOpen, setVideoPlanParamsOpen] = createSignal(false);
  const [videoPlanOpen, setVideoPlanOpen] = createSignal(false);
  const [videoPlanSettings, setVideoPlanSettings] = createSignal(DEFAULT_VIDEO_PLAN_SETTINGS);
  const [videoPlanResult, setVideoPlanResult] = createSignal(null);
  const [videoPlanState, setVideoPlanState] = createSignal({ phase: "idle", status: "" });
  const [imagePlanResult, setImagePlanResult] = createSignal(null);
  const [imagePlanState, setImagePlanState] = createSignal({ phase: "idle", status: "" });
  const [imagePlanOpen, setImagePlanOpen] = createSignal(false);
  const [videoOnlyPlanResult, setVideoOnlyPlanResult] = createSignal(null);
  const [videoOnlyPlanState, setVideoOnlyPlanState] = createSignal({ phase: "idle", status: "" });
  const [videoOnlyPlanOpen, setVideoOnlyPlanOpen] = createSignal(false);
  const [composerParamsOpen, setComposerParamsOpen] = createSignal(false);
  const [composerOpen, setComposerOpen] = createSignal(false);
  const [composerSettings, setComposerSettings] = createSignal(DEFAULT_COMPOSER_SETTINGS);
  const [composerResult, setComposerResult] = createSignal(null);
  const [composerState, setComposerState] = createSignal({ phase: "idle", status: "" });
  const [storyboardAgentOpen, setStoryboardAgentOpen] = createSignal(false);
  const [sessionVariablesOpen, setSessionVariablesOpen] = createSignal(false);
  const [sessionVariablesPayload, setSessionVariablesPayload] = createSignal(null);
  const [sessionVariablesUpdatedAt, setSessionVariablesUpdatedAt] = createSignal("");
  const [sceneAudioState, setSceneAudioState] = createSignal({});
  const [deletingAssetId, setDeletingAssetId] = createSignal("");
  const [activeAssetTab, setActiveAssetTabState] = createSignal(initialAssetPanelTab());
  let detailLoadToken = 0;
  let suppressNextAssetClick = false;
  let activeAssetDragCleanup = null;

  function setActiveAssetTab(tab) {
    if (!ASSET_PANEL_TABS.has(tab)) return;
    setActiveAssetTabState(tab);
    rememberAssetPanelTabInUrl(tab);
    try {
      window.localStorage?.setItem(ASSET_PANEL_TAB_KEY, tab);
    } catch {
      // localStorage can be unavailable in some embedded browser contexts.
    }
  }

  const task = createMemo(() => state()?.task || null);
  const meta = createMemo(() => state()?.meta || {});
  const isDanceMimicTask = createMemo(() => {
    const values = [
      task()?.workflow_mode,
      task()?.workflow_id,
      task()?.source_type,
      meta()?.workflow_mode,
      meta()?.workflow_id,
      meta()?.source_type,
    ].map((value) => String(value || "").trim().toLowerCase());
    return values.includes(DANCE_MIMIC_WORKFLOW_ID);
  });
  const shots = createMemo(() => plan()?.shots || []);
  const totalDuration = createMemo(() => shots().reduce((sum, shot) => sum + shotDuration(shot), 0));
  const sessionId = createMemo(() => Number(task()?.session_id || meta()?.analysis_session_id || 0));
  const manualAssets = createMemo(() => Array.isArray(meta()?.manual_assets) ? meta().manual_assets : []);
  const uploadedImages = createMemo(() => Array.isArray(meta()?.uploaded_images) ? meta().uploaded_images : manualAssets().filter((item) => assetKind(item) === "image"));
  const uploadedAudios = createMemo(() => Array.isArray(meta()?.uploaded_audios) ? meta().uploaded_audios : manualAssets().filter((item) => assetKind(item) === "audio"));
  const uploadedVideos = createMemo(() => Array.isArray(meta()?.uploaded_videos) ? meta().uploaded_videos : manualAssets().filter((item) => assetKind(item) === "video"));
  const historyVersions = createMemo(() => Array.isArray(meta()?.history_versions) ? meta().history_versions : []);
  const videoSlotState = (dialogueId, assetKey = "") => {
    const slots = meta()?.storyboard_video_slots || {};
    const byDialogue = slots.by_dialogue_id || {};
    const byAsset = slots.by_asset_key || {};
    return byAsset[String(assetKey || "")] || byDialogue[String(dialogueId || "")] || {};
  };
  const usedPaths = createMemo(() => {
    const paths = new Set();
    for (const shot of shots()) for (const scene of shot.scenes || []) {
      const assets = scene.working_assets || {};
      if (assets.audio?.path) paths.add(assets.audio.path);
      if (assets.video?.path) paths.add(assets.video.path);
      for (const image of assets.images || []) if (image?.path) paths.add(image.path);
      for (const dialogue of scene.dialogues || []) {
        const dialogueAssets = dialogue.working_assets || {};
        if (dialogueAssets.audio?.path) paths.add(dialogueAssets.audio.path);
        if (dialogueAssets.video?.path) paths.add(dialogueAssets.video.path);
        for (const image of dialogueAssets.images || []) if (image?.path) paths.add(image.path);
        if (dialogue.bound_image_path) paths.add(dialogue.bound_image_path);
        if (dialogue.image_path) paths.add(dialogue.image_path);
        for (const path of dialogue.source_image_paths || []) if (path) paths.add(path);
      }
    }
    return paths;
  });
  const assetTextByPath = createMemo(() => {
    const textByPath = new Map();
    const remember = (path, text) => {
      const key = String(path || "").trim();
      const value = String(text || "").trim();
      if (key && value && !textByPath.has(key)) textByPath.set(key, value);
    };
    for (const shot of shots()) for (const scene of shot.scenes || []) {
      const sceneText = (scene.dialogues || []).map((dialogue) => dialogueText(dialogue)).filter(Boolean).join(" ");
      const sceneAssets = scene.working_assets || {};
      remember(sceneAssets.audio?.path, sceneText);
      remember(sceneAssets.video?.path, sceneText);
      for (const image of sceneAssets.images || []) remember(image?.path, sceneText);
      for (const dialogue of scene.dialogues || []) {
        const text = dialogueText(dialogue);
        const dialogueAssets = dialogue.working_assets || {};
        remember(dialogueAssets.audio?.path, text);
        remember(dialogueAssets.video?.path, text);
        for (const image of dialogueAssets.images || []) remember(image?.path, text);
        remember(dialogue.bound_image_path, text);
        remember(dialogue.image_path, text);
        for (const path of dialogue.source_image_paths || []) remember(path, text);
      }
    }
    return textByPath;
  });
  const sourceAssetGroups = createMemo(() => Array.isArray(meta()?.source_asset_groups) ? meta().source_asset_groups : []);
  const assetGroups = createMemo(() => sourceAssetGroups().length ? sourceAssetGroups() : shots().map((shot) => ({
    shot_id: shot.shot_id,
    duration: shotDuration(shot),
    scenes: (shot.scenes || []).flatMap((scene) => (scene.dialogues || []).flatMap((dialogue) => {
      const paths = dialogue.source_image_paths?.length ? dialogue.source_image_paths : [dialogue.bound_image_path || dialogue.image_path].filter(Boolean);
	      return paths.slice(0, 1).map((path) => ({ path, shot_id: shot.shot_id, scene_id: scene.scene_id, duration: dialogue.duration, text: dialogue.text, kind: "image", source: "source" }));
    })).filter((item) => item.path),
  })).filter((group) => group.scenes.length));
  const sourceImages = createMemo(() => collectStoryboardSourceImages(shots(), assetGroups()));
  const referenceImageOptions = createMemo(() => {
    const byPath = new Map();
    const remember = (item, source) => {
      const path = String(item?.path || "").trim();
      if (!path || byPath.has(path) || assetKind(item) !== "image") return;
      byPath.set(path, {
        ...item,
        path,
        source,
        label: String(item?.label || item?.filename || filenameFromPath(path)).trim() || path,
        filename: String(item?.filename || filenameFromPath(path)).trim() || filenameFromPath(path),
      });
    };
    for (const item of sourceImages()) remember(item, "source");
    for (const item of uploadedImages()) remember(item, "upload");
    for (const version of historyVersions()) {
      for (const item of version.items || []) remember({ ...item, version: version.version }, "history");
    }
    return Array.from(byPath.values());
  });
  const dialogueOptions = createMemo(() => shots().flatMap((shot, shotIndex) => (shot.scenes || []).flatMap((scene, sceneIndex) => (scene.dialogues || []).map((dialogue, dialogueIndex) => ({
    dialogue_id: dialogue.dialogue_id,
    label: `${shot.shot_id || `Shot ${shotIndex + 1}`} / ${scene.scene_id || `Scene ${sceneIndex + 1}`} / ${dialogue.srt_id || dialogue.dialogue_id || `D${dialogueIndex + 1}`} · ${String(dialogue.text || "").slice(0, 36)}`,
  })))));
  const selectedDialogue = createMemo(() => {
    const targetId = selectedDialogueId();
    if (!targetId) return null;
    for (const shot of shots()) for (const scene of shot.scenes || []) {
      const dialogue = (scene.dialogues || []).find((item) => item.dialogue_id === targetId);
      if (dialogue) return { ...dialogue, text: dialogueText(dialogue) };
    }
    return null;
  });

  const {
    buildGSecondsPerChar,
    ttsProviderOptions,
    ttsModelsForProvider,
    ttsVoicesForModel,
    audioSettings,
    openAudioSettings,
    saveAudioSettings,
    reorganizeFixedStoryboard,
    refreshDialogueTimingsOnly,
    applySceneAudioDuration,
    generateSceneAudio,
    cancelActiveTtsRequests,
  } = createKouboStoryboardTtsController({
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
    roleAccess: props.roleAccess,
  });

  const {
    stopTimelinePlayback,
    playSceneTTS,
    toggleTimelinePlayback,
    applyPlaybackSpeed,
    pauseActiveAudio,
  } = createKouboStoryboardPlaybackController({
    shots,
    scope,
    selectedShotIndex,
    selectedDialogueId,
    playbackState,
    setPlaybackState,
    playbackSpeed,
    setPlaybackSpeed,
    setPlaybackSpeedOpen,
    setError,
    scrollToNode,
    generateSceneAudio,
    applySceneAudioDuration,
    cancelActiveTtsRequests,
    selectScene,
  });

  const {
    applyVideoPlanSettings,
    openVideoPlan,
    executeVideoPlan,
    refreshVideoPlanExecution,
    currentVideoPlanTarget,
    videoPlanDisabledReason,
  } = createKouboStoryboardVideoPlanController({
    kbApi,
    task,
    shots,
    scope,
    selectedShotIndex,
    selectedDialogueId,
    dirty,
    runAction,
    setError,
    videoPlanSettings,
    videoPlanResult,
    setVideoPlanSettings,
    setVideoPlanOpen,
    setVideoPlanResult,
    setVideoPlanState,
    syncStoryboardDetail,
  });

  const {
    applyComposerSettings,
    openComposer,
    executeComposer,
    refreshComposerExecution,
    composerDisabledReason,
  } = createKouboStoryboardComposerController({
    kbApi,
    task,
    shots,
    scope,
    selectedShotIndex,
    selectedDialogueId,
    dirty,
    runAction,
    setError,
    composerSettings,
    composerResult,
    setComposerSettings,
    setComposerOpen,
    setComposerResult,
    setComposerState,
    setPlan,
    setState,
  });

  const videoPlanBusy = () => busy() === "video-plan" || busy() === "video-plan-execute" || ["checking", "generating", "executing"].includes(videoPlanState().phase);
  const composerBusy = () => busy() === "composer-candidates" || busy() === "composer-execute" || ["checking", "executing"].includes(composerState().phase);
  const imagePlanBusy = () => busy() === "image-plan" || busy() === "image-plan-execute" || ["generating", "executing"].includes(imagePlanState().phase);
  const videoOnlyPlanBusy = () => busy() === "video-only-plan" || busy() === "video-only-plan-execute" || busy() === "video-only-confirm-final" || ["generating", "executing"].includes(videoOnlyPlanState().phase);

  const {
    generateImagePlan,
    executeImagePlan,
    refreshImagePlanExecution,
    imagePlanDisabledReason,
  } = createKouboStoryboardImagePlanController({
    kbApi,
    task,
    shots,
    scope,
    selectedShotIndex,
    selectedDialogueId,
    dirty,
    runAction,
    setError,
    videoPlanSettings,
    imagePlanResult,
    setImagePlanOpen,
    setImagePlanResult,
    setImagePlanState,
    setPlan,
    setState,
    videoPlanBusy,
    composerBusy,
  });

  const {
    generateVideoOnlyPlan,
    executeVideoOnlyPlan,
    materializeVideoOnlyTailFrame,
    confirmVideoOnlyFinal,
    refreshVideoOnlyPlanExecution,
    videoOnlyPlanDisabledReason,
  } = createKouboStoryboardVideoOnlyPlanController({
    kbApi,
    task,
    shots,
    scope,
    selectedShotIndex,
    selectedDialogueId,
    dirty,
    runAction,
    setError,
    videoPlanSettings,
    videoOnlyPlanResult,
    setVideoOnlyPlanOpen,
    setVideoOnlyPlanResult,
    setVideoOnlyPlanState,
    setPlan,
    setState,
    bumpMediaVersion: () => setMediaVersion(String(Date.now())),
    videoPlanBusy,
    imagePlanBusy,
    composerBusy,
  });

  async function openFullVideoPlanFromComposer(targetOverride = {}) {
    const actionSource = String(targetOverride.action_source || "composer_scope_mismatch_cta").trim() || "composer_scope_mismatch_cta";
    setComposerOpen(false);
    setScope("all");
    setSelectedDialogueId("");
    await openVideoPlan({
      target_type: "task",
      shot_id: "",
      scene_id: "",
      action_source: actionSource,
    });
  }

  createEffect(() => {
    const next = routeFromHash(props.routeHash);
    setRoute(next);
    if (next.view === "detail" && next.taskId) void loadDetail(next.taskId).catch((err) => setError(err instanceof Error ? err.message : String(err)));
    if (next.view === "list") void loadList().catch((err) => setError(err instanceof Error ? err.message : String(err)));
  });

  createEffect(() => {
    const state = resizeState();
    if (!state) return;
    const onMove = (event) => setLeftWidth(Math.min(Math.max(220, state.startWidth + event.clientX - state.startX), Math.min(460, window.innerWidth - 760)));
    const onUp = () => setResizeState(null);
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    onCleanup(() => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    });
  });

  onMount(() => {
    const toList = () => { window.location.hash = "#/koubo-storyboard/tasks"; };
    window.addEventListener("koubo-storyboard:task-list", toList);
    onCleanup(() => window.removeEventListener("koubo-storyboard:task-list", toList));
  });

  createEffect(() => {
    const currentRoute = route();
    if (currentRoute.view === "detail" && currentRoute.taskId) {
      untrack(() => props.onSidebarChange?.(<AssetPanel task={task} selectedDialogue={selectedDialogue} activeAssetTab={activeAssetTab} setActiveAssetTab={setActiveAssetTab} assetGroups={assetGroups} sourceImages={sourceImages} manualAssets={manualAssets} uploadedImages={uploadedImages} uploadedAudios={uploadedAudios} uploadedVideos={uploadedVideos} historyVersions={historyVersions} selectedAsset={selectedAsset} setSelectedAsset={setSelectedAsset} usedPaths={usedPaths} assetTextByPath={assetTextByPath} sessionId={sessionId} openImage={setImagePreview} uploadBusy={() => busy() === "upload"} uploadStatus={assetUploadStatus} deletingAssetId={deletingAssetId} uploadManualAssets={uploadManualAssets} deleteManualAsset={deleteManualAsset} deleteHistoryAsset={deleteHistoryAsset} onAssetLibraryResult={applyAssetLibraryResult} onMediaLibraryImported={refreshAfterMediaLibraryImport} beginPointerAssetDrag={beginPointerAssetDrag} clickAsset={clickAsset} dragAsset={dragAsset} openAssetLibraryAgent={() => { const id = task()?.id || currentRoute.taskId; window.location.hash = `#/koubo-asset-library/tasks/${id}`; }} />));
    } else {
      props.onSidebarChange?.(null);
    }
  });
  onCleanup(() => {
    stopTimelinePlayback();
    pauseActiveAudio();
    activeAssetDragCleanup?.();
    props.onSidebarChange?.(null);
  });

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
    const result = await runAction("list", () => kbApi.tasks());
    setItems(result.items || []);
  }

  function extractPromptText(prompt) {
    const value = String(prompt || "").trim();
    const markerMatch = value.match(/(?:正文|朗读文本|Text)\s*[:：]\s*([\s\S]+)$/i);
    return (markerMatch?.[1] || value).trim();
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

  function buildFallbackTimingModel(nextPlan, savedSelection = null) {
    const text = (nextPlan?.shots || []).flatMap((shot) => allDialogues(shot).map((item) => dialogueText(item.dialogue))).filter(Boolean).join(" ");
    const chars = spokenCharCount(text);
    const duration = (nextPlan?.shots || []).reduce((sum, shot) => sum + shotDuration(shot), 0);
    const secPerChar = chars ? duration / chars : Number(timingSecondsPerChar() || 0.18);
    return {
      voice: savedSelection?.label || savedSelection?.voice || savedSelection?.voice_id || "-",
      build_g_duration: duration,
      build_g_chars: chars,
      sec_per_char: secPerChar,
      build_g_text: text,
      selection: savedSelection,
      top_candidates: savedSelection?.top_candidates || savedSelection?.recommendations || [],
    };
  }

  function buildTimingModelFromCandidates(nextPlan, payload) {
    const candidates = (Array.isArray(payload?.candidates) ? payload.candidates : Array.isArray(payload?.top_candidates) ? payload.top_candidates : [])
      .map(normalizeTtsCandidate)
      .filter(Boolean);
    const savedSelection = nextPlan?.storyboard_tts_selection && typeof nextPlan.storyboard_tts_selection === "object" ? nextPlan.storyboard_tts_selection : null;
    if (!candidates.length) return buildFallbackTimingModel(nextPlan, savedSelection);
    const selected = normalizeTtsCandidate(candidates.find((item) => item.selected) || candidates[0]);
    const samplePolicy = payload?.sample_policy && typeof payload.sample_policy === "object" ? payload.sample_policy : {};
    const text = extractPromptText(selected?.prompt || selected?.prompt_template || selected?.instructions || "");
    const chars = spokenCharCount(text);
    const duration = positiveNumber(selected?.fit_duration) || positiveNumber(selected?.target_duration) || positiveNumber(samplePolicy.selected_duration) || positiveNumber(selected?.duration) || 0;
    const secPerChar = chars && duration ? duration / chars : Number(timingSecondsPerChar() || 0.18);
    return {
      provider: selected?.provider || "",
      model: selected?.model || "",
      voice: selected?.label || selected?.voice || selected?.voice_id || "",
      voice_id: selected?.voice_id || selected?.voice || "",
      candidate_id: selected?.candidate_id || "",
      build_g_duration: duration,
      build_g_chars: chars,
      sec_per_char: secPerChar,
      build_g_text: text,
      selection: savedSelection || selected,
      top_candidates: candidates,
    };
  }

  async function loadBuilderGCandidates(taskId) {
    if (!taskId) return null;
    try {
      return await kbApi.ttsBuilderCandidates(taskId);
    } catch {
      return null;
    }
  }

  function firstDialogueId(nextPlan) {
    return nextPlan?.shots?.[0]?.scenes?.[0]?.dialogues?.[0]?.dialogue_id || "";
  }

  function shotIndexForDialogue(nextPlan, dialogueId) {
    const id = String(dialogueId || "");
    if (!id) return -1;
    for (let shotIndex = 0; shotIndex < (nextPlan?.shots || []).length; shotIndex += 1) {
      for (const item of allDialogues(nextPlan.shots[shotIndex])) {
        if (item.dialogue?.dialogue_id === id) return shotIndex;
      }
    }
    return -1;
  }

  function applyLoadedSelection(nextPlan, preserveSelection = false, dialogueAssetKey = "") {
    if (dialogueAssetKey) {
      const located = locateStoryboardDialogue(nextPlan, dialogueAssetKey);
      if (located.status === "found") {
        setSelectedShotIndex(located.shotIndex);
        setSelectedDialogueId(located.dialogueId);
        setScope("scene");
        setEditingDialogueId("");
        setRouteNavigationNotice("");
        requestAnimationFrame(() => requestAnimationFrame(() => {
          scrollToNode(`kbsp-dialogue-${located.dialogueId}`, "auto");
        }));
        return;
      }
      setRouteNavigationNotice(
        located.status === "duplicate"
          ? "返回的 Dialogue 素材标识在当前 Task 中不唯一，已拒绝猜测并选择第一个 Dialogue。"
          : "返回的 Dialogue 已不存在或不属于当前 Task，已选择第一个可用 Dialogue。",
      );
    }
    if (!preserveSelection) {
      setSelectedShotIndex(0);
      setSelectedDialogueId(firstDialogueId(nextPlan));
      setScope("scene");
      setEditingDialogueId("");
      return;
    }
    const shotCount = nextPlan?.shots?.length || 0;
    const currentDialogueId = selectedDialogueId();
    const nextShotIndex = shotIndexForDialogue(nextPlan, currentDialogueId);
    if (currentDialogueId && nextShotIndex >= 0) {
      setSelectedShotIndex(nextShotIndex);
      setSelectedDialogueId(currentDialogueId);
    } else if (scope() === "shot" && shotCount) {
      setSelectedShotIndex(Math.min(Math.max(0, selectedShotIndex()), shotCount - 1));
      setSelectedDialogueId("");
    } else {
      setSelectedShotIndex(0);
      setSelectedDialogueId(firstDialogueId(nextPlan));
      setScope("scene");
    }
    setEditingDialogueId((current) => (shotIndexForDialogue(nextPlan, current) >= 0 ? current : ""));
  }

  async function loadDetail(taskId) {
    const requestToken = ++detailLoadToken;
    stopTimelinePlayback();
    const cached = getCachedKouboStoryboardDetail(taskId);
    const sameDirtyTask = dirty() && Number(task()?.id || 0) === Number(taskId || 0);
    const appliedCached = Boolean(
      cached?.task
      && cached?.meta
      && cached?.plan
      && !sameDirtyTask
      && Number(cached.task?.id || 0) === Number(taskId),
    );
    if (cached?.task && Number(cached.task?.id || 0) !== Number(taskId)) {
      setRouteNavigationNotice("缓存 Task 与路由不一致，已丢弃缓存并重新读取权威 Task。");
    }
    if (appliedCached) {
      const cachedPlan = renumberPlan(copy(cached.plan));
      setState({ task: cached.task, meta: cached.meta });
      setPlan(cachedPlan);
      setMediaVersion(buildMediaVersion(cached, cachedPlan));
      setVideoPlanSettings(cached.meta?.video_plan_settings || DEFAULT_VIDEO_PLAN_SETTINGS);
      setTimingModel(buildTimingModelFromCandidates(cachedPlan, null));
      applyLoadedSelection(cachedPlan, false, route().dialogueAssetKey);
      setDirty(false);
      setGroupingDirty(false);
    }
    const result = await runAction(cached ? "refresh" : "load", () => kbApi.detail(taskId));
    if (requestToken !== detailLoadToken) return;
    if (Number(route().taskId || 0) !== Number(taskId || 0)) return;
    if (dirty() && Number(task()?.id || 0) === Number(taskId || 0)) return;
    const nextPlan = renumberPlan(copy(result.plan));
    const currentTaskId = Number(result.task?.id || taskId || 0);
    if (currentTaskId !== Number(taskId) || currentTaskId !== Number(route().taskId || 0)) {
      setState(null);
      setPlan(null);
      setRouteNavigationNotice("服务端返回的 Task 与路由不一致，已停止加载以避免定位到错误 Dialogue。");
      return;
    }
    setState({ task: result.task, meta: result.meta });
    setPlan(nextPlan);
    setMediaVersion(buildMediaVersion(result, nextPlan));
    setVideoPlanSettings(result.meta?.video_plan_settings || DEFAULT_VIDEO_PLAN_SETTINGS);
    setTimingModel(buildTimingModelFromCandidates(nextPlan, null));
    setRouteNavigationNotice(route().navigationError || "");
    applyLoadedSelection(nextPlan, appliedCached, route().dialogueAssetKey);
    setDirty(false);
    setGroupingDirty(false);
    void loadBuilderGCandidates(currentTaskId).then((candidates) => {
      if (!candidates || requestToken !== detailLoadToken || Number(task()?.id || 0) !== currentTaskId || dirty()) return;
      setTimingModel(buildTimingModelFromCandidates(nextPlan, candidates));
    });
  }

  async function syncStoryboardDetail(expectedTaskId = null) {
    const taskId = Number(expectedTaskId || task()?.id || 0);
    if (!taskId || dirty() || Number(task()?.id || 0) !== taskId) return null;
    try {
      const result = await kbApi.detail(taskId);
      if (dirty() || Number(task()?.id || 0) !== taskId) return null;
      const nextPlan = renumberPlan(copy(result.plan));
      setState({ task: result.task, meta: result.meta });
      setPlan(nextPlan);
      setMediaVersion(buildMediaVersion(result, nextPlan));
      setDirty(false);
      setGroupingDirty(false);
      return result;
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      return null;
    }
  }

  function updatePlan(mutator) {
    const scrollContainer = document.querySelector(".kbsp-shot-scroll");
    const previousScrollTop = scrollContainer?.scrollTop || 0;
    const next = renumberPlan(copy(plan()));
    mutator(next);
    renumberPlan(next);
    setPlan(next);
    setDirty(true);
    // Solid's identity-keyed <For> remounts copied shot objects. Preserve the
    // editor viewport while those descendants are replaced.
    const restoreScroll = () => {
      if (scrollContainer?.isConnected) scrollContainer.scrollTop = previousScrollTop;
    };
    queueMicrotask(restoreScroll);
    requestAnimationFrame(restoreScroll);
  }

  function scrollToNode(id, behavior = "smooth") {
    const node = document.getElementById(id);
    const container = node?.closest?.(".kbsp-shot-scroll");
    if (!node || !container) return;
    const nodeRect = node.getBoundingClientRect();
    const containerRect = container.getBoundingClientRect();
    container.scrollTo({ top: Math.max(0, container.scrollTop + nodeRect.top - containerRect.top - 82), behavior });
  }

  function focusDialogueEditor(dialogueId) {
    const focus = () => {
      const node = document.getElementById(`kbsp-dialogue-${dialogueId}`);
      const textarea = node?.querySelector?.("textarea[data-kbsp-dialogue-textarea]");
      if (!node || !textarea) return;
      scrollToNode(node.id, "auto");
      textarea.focus({ preventScroll: true });
    };
    queueMicrotask(focus);
    requestAnimationFrame(() => requestAnimationFrame(focus));
  }

  function selectDialogue(shotIndex, dialogueId) {
    setSelectedShotIndex(shotIndex);
    setSelectedDialogueId(dialogueId || "");
    setScope(dialogueId ? "scene" : "shot");
  }

  function openCleanImage(target = null) {
    setCleanImageTarget(target);
    if (target?.dialogue_id) setSelectedDialogueId(target.dialogue_id);
    setCleanImageOpen(true);
  }

  function scrollToDialogue(dialogueId) {
    if (!dialogueId) return;
    requestAnimationFrame(() => scrollToNode(`kbsp-dialogue-${dialogueId}`));
  }

  function selectScene(shotIndex, scene) {
    setSelectedShotIndex(shotIndex);
    setSelectedDialogueId(scene?.dialogues?.[0]?.dialogue_id || "");
    setScope("scene");
  }

  function updateShotName(shotId, value, options = {}) {
    const normalize = options.commit === true;
    const nextValue = normalize ? String(value || shotId || "").trim() || shotId : String(value ?? "");
    if (normalize) {
      updatePlan((draft) => {
        const shot = (draft.shots || []).find((item) => item.shot_id === shotId);
        if (shot) shot.shot_name = nextValue || shot.shot_id;
      });
      return;
    }
    const current = plan();
    const shot = (current?.shots || []).find((item) => item.shot_id === shotId);
    if (!shot) return;
    shot.shot_name = nextValue;
    setDirty(true);
  }

  function updateDialogue(dialogueId, key, value) {
    if (key === "text") {
      const current = plan();
      if (!current) return;
      for (const shot of current.shots || []) for (const scene of shot.scenes || []) for (const dialogue of scene.dialogues || []) {
        if (dialogue.dialogue_id !== dialogueId) continue;
        dialogue.text = value;
        setDirty(true);
        return;
      }
    }
    updatePlan((draft) => {
      for (const shot of draft.shots || []) for (const scene of shot.scenes || []) for (const dialogue of scene.dialogues || []) {
        if (dialogue.dialogue_id !== dialogueId) continue;
        dialogue[key] = key === "duration" ? Number(value || 0) : value;
      }
    });
  }

  async function setDialogueTalkingHead(dialogueId, isTalkingHead) {
    if (!task()?.id || !plan() || !dialogueId) return false;
    if (busy()) return false;
    if (dirty()) {
      setError("请先保存当前 StoryBoard，再标记口播/空镜");
      return false;
    }
    let found = false;
    const nextPlan = renumberPlan(copy(plan()));
    for (const shot of nextPlan.shots || []) for (const scene of shot.scenes || []) for (const dialogue of scene.dialogues || []) {
      if (dialogue.dialogue_id !== dialogueId) continue;
      const current = dialogue.video_plan && typeof dialogue.video_plan === "object" ? dialogue.video_plan : {};
      dialogue.video_plan = {
        ...current,
        is_talking_head: Boolean(isTalkingHead),
        lipsync_override: isTalkingHead ? "" : "skip_cutaway",
        lipsync_override_source: "storyboard_original_image_context_menu",
        lipsync_override_reason: isTalkingHead ? "user_marked_talking_head" : "user_marked_cutaway",
        lipsync_override_updated_at: new Date().toISOString(),
      };
      found = true;
    }
    if (!found) return false;
    const result = await runAction("save-dialogue-video-plan", () => kbApi.save(task().id, nextPlan, { regroup_working_assets: groupingDirty() }));
    setState({ task: result.task, meta: result.meta });
    setPlan(renumberPlan(copy(result.plan)));
    setDirty(false);
    setGroupingDirty(false);
    return true;
  }

  function bumpDuration(dialogueId, delta) {
    updatePlan((draft) => {
      for (const shot of draft.shots || []) for (const scene of shot.scenes || []) for (const dialogue of scene.dialogues || []) {
        if (dialogue.dialogue_id === dialogueId) dialogue.duration = Math.max(0, Number((Number(dialogue.duration || 0) + delta).toFixed(2)));
      }
    });
  }

  function ensureSceneWorkingAssets(scene) {
    const current = scene.working_assets && typeof scene.working_assets === "object" ? scene.working_assets : {};
    const images = Array.isArray(current.images) ? current.images : [];
    scene.working_assets = {
      audio: { slot: "Audio_Final", source_type: current.audio?.source_type || "", path: current.audio?.path || "" },
      images: [
        { slot: "Image_New", source_type: images[0]?.source_type || "", path: images[0]?.path || "" },
        { slot: "Image_02", source_type: images[1]?.source_type || "", path: images[1]?.path || "" },
      ],
      video: { slot: "Video_Final", source_type: current.video?.source_type || "", path: current.video?.path || "" },
    };
    return scene.working_assets;
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

  function expectedAssetKind(targetKind = "") {
    if (targetKind === "source") return "image";
    if (targetKind === "raw_video" || targetKind === "final_video") return "video";
    if (targetKind === "video") return "";
    return targetKind || "";
  }

  function forgetAudioState(dialogueId, sceneId = "") {
    setSceneAudioState((previous) => {
      if (!previous?.[dialogueId] && (!sceneId || !previous?.[sceneId])) return previous;
      const next = { ...previous };
      delete next[dialogueId];
      if (sceneId) delete next[sceneId];
      return next;
    });
  }

  function sceneIdForDialogue(dialogueId) {
    for (const shot of shots()) for (const scene of shot.scenes || []) for (const dialogue of scene.dialogues || []) {
      if (dialogue.dialogue_id === dialogueId) return scene.scene_id || "";
    }
    return "";
  }

  async function refreshOpenPlanStates() {
    if (imagePlanOpen()) {
      try {
        await refreshImagePlanExecution();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    }
    if (videoOnlyPlanOpen()) {
      try {
        await refreshVideoOnlyPlanExecution();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    }
    if (videoPlanOpen()) {
      try {
        await refreshVideoPlanExecution();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    }
  }

  async function assignAsset(dialogueId, asset, targetKind = "") {
    const path = typeof asset === "string" ? asset : asset?.path;
    const inferredKind = assetKind(typeof asset === "string" ? { path } : asset || { path });
    const requiredKind = expectedAssetKind(targetKind);
    if (requiredKind && path && inferredKind !== requiredKind) return false;
    if (!dialogueId) return false;
    if (targetKind === "video") return false;
    const target = targetKind || (inferredKind === "video" ? "" : inferredKind);
    if (!target) return false;
    if (!task()?.id || !plan()) return false;
    const payload = {
      dialogue_id: dialogueId,
      target_kind: target,
      plan: renumberPlan(copy(plan())),
      regroup_working_assets: groupingDirty(),
    };
    const changedSceneId = target === "audio" ? sceneIdForDialogue(dialogueId) : "";
    const result = path
      ? await runAction(`bind-${target}`, () => kbApi.bindAsset(task().id, { ...payload, asset_path: path }))
      : await runAction(`clear-${target}`, () => kbApi.clearAsset(task().id, payload));
    setState({ task: result.task, meta: result.meta });
    setPlan(renumberPlan(copy(result.plan)));
    setDirty(false);
    setGroupingDirty(false);
    setSelectedAsset(null);
    if (target === "audio") forgetAudioState(dialogueId, changedSceneId);
    await refreshOpenPlanStates();
    return true;
  }

  function dragAsset(event, item) {
    if (!item?.path) return;
    event.stopPropagation();
    window.getSelection?.()?.removeAllRanges();
    event.dataTransfer.setData("application/json", JSON.stringify(item));
    event.dataTransfer.setData("text/plain", item.path);
    event.dataTransfer.effectAllowed = "copy";
    if (event.currentTarget) event.dataTransfer.setDragImage(event.currentTarget, event.currentTarget.clientWidth / 2, event.currentTarget.clientHeight / 2);
  }

  function allowAssetDrop(event) {
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = "copy";
  }

  function dropAsset(event, dialogueId, targetKind = "") {
    event.preventDefault();
    event.stopPropagation();
    window.getSelection?.()?.removeAllRanges();
    const raw = event.dataTransfer.getData("application/json") || event.dataTransfer.getData("text/plain");
    if (!raw) return;
    try {
      assignAsset(dialogueId, JSON.parse(raw), targetKind);
    } catch {
      assignAsset(dialogueId, raw, targetKind);
    }
  }

  function dropTargetFromPoint(x, y) {
    const node = document.elementFromPoint(x, y);
    return node?.closest?.("[data-kbsp-drop='true']") || null;
  }

  async function uploadManualAssets(files) {
    const picked = Array.from(files || []).filter(Boolean);
    if (!task()?.id || !picked.length) return;
    const applyUploadResult = (result) => {
      if (!result?.task || !result?.meta || !result?.plan) return;
      setState({ task: result.task, meta: result.meta });
      setPlan(renumberPlan(copy(result.plan)));
      setDirty(false);
    };
    let uploadedCount = 0;
    setAssetUploadStatus({ tone: "info", text: `准备上传 ${picked.length} 个文件...` });
    try {
      const result = await runAction("upload", () => kbApi.uploadAssetsBatched(task().id, picked, {
        maxFiles: 1,
        maxBytes: 64 * 1024 * 1024,
        onProgress: ({ index, total }) => {
          setAssetUploadStatus({ tone: "info", text: `正在上传文件 ${index}/${total}...` });
        },
        onBatch: (batchResult, info) => {
          uploadedCount = info?.completedFiles || uploadedCount;
          applyUploadResult(batchResult);
        },
      }));
      applyUploadResult(result);
      const addedCount = Array.isArray(result?.added) ? result.added.length : uploadedCount;
      setAssetUploadStatus({ tone: "success", text: `已上传 ${addedCount || picked.length} 个文件。` });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setAssetUploadStatus({
        tone: "error",
        text: uploadedCount ? `已上传 ${uploadedCount} 个文件，后续上传失败：${message}` : message,
      });
    }
  }

  function applyAssetLibraryResult(result) {
    if (!result?.task || !result?.meta || !result?.plan) return;
    rememberKouboStoryboardDetail(result);
    const nextPlan = renumberPlan(copy(result.plan));
    setState({ task: result.task, meta: result.meta });
    setPlan(nextPlan);
    setMediaVersion(buildMediaVersion(result, nextPlan));
    setDirty(false);
  }

  async function refreshAfterMediaLibraryImport(result) {
    setActiveAssetTab("upload");
    const taskId = Number(task()?.id || 0);
    if (!taskId) return;
    const latest = result?.task && result?.meta ? result : await kbApi.detail(taskId);
    if (!latest?.task || !latest?.meta || Number(latest.task.id || taskId) !== taskId) return;
    rememberKouboStoryboardDetail({ ...latest, plan: latest.plan || plan() });
    setState({ task: latest.task, meta: latest.meta });
    setMediaVersion(buildMediaVersion(latest, plan()));
  }

  function applyAssetLibraryMetaResult(result) {
    if (!result?.task || !result?.meta) return;
    setState({ task: result.task, meta: result.meta });
  }

  async function deleteManualAsset(assetId) {
    if (!task()?.id || !assetId) return;
    setDeletingAssetId(assetId);
    try {
      const result = await runAction("delete-asset", () => kbApi.deleteAsset(task().id, assetId));
      setState({ task: result.task, meta: result.meta });
      setPlan(renumberPlan(copy(result.plan)));
      setSelectedAsset((current) => current?.id === assetId ? null : current);
      setDirty(false);
    } finally {
      setDeletingAssetId("");
    }
  }

  async function deleteHistoryAsset(assetId) {
    if (!task()?.id || !assetId) return;
    setDeletingAssetId(assetId);
    try {
      const result = await runAction("delete-history-asset", () => kbApi.deleteHistoryAsset(task().id, assetId));
      setState({ task: result.task, meta: result.meta });
      setPlan(renumberPlan(copy(result.plan)));
      setSelectedAsset((current) => current?.id === assetId || current?.path === assetId ? null : current);
      setDirty(false);
    } finally {
      setDeletingAssetId("");
    }
  }

  async function savePlan() {
    if (!task()?.id || !plan()) return;
    const result = await runAction("save", () => kbApi.save(task().id, renumberPlan(copy(plan())), { regroup_working_assets: groupingDirty() }));
    setState({ task: result.task, meta: result.meta });
    setPlan(renumberPlan(copy(result.plan)));
    setDirty(false);
    setGroupingDirty(false);
    if (videoOnlyPlanOpen()) {
      try {
        await refreshVideoOnlyPlanExecution();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    }
  }

  async function waitForAnalysisRun(taskId, attemptId) {
    let latest = await kbApi.analysisRunStatus(taskId, attemptId);
    for (let index = 0; index < 120; index += 1) {
      const status = String(latest?.status || "").toLowerCase();
      if (ANALYSIS_V1_TERMINAL_STATUSES.has(status)) return latest;
      await sleep(1000);
      latest = await kbApi.analysisRunStatus(taskId, attemptId);
    }
    throw new Error("00 步骤仍在运行，请稍后刷新当前页面状态。");
  }

  async function refreshSessionVariables() {
    const currentTask = task();
    if (!currentTask?.id) return;
    if (dirty()) {
      setError("请先保存当前 StoryBoard，再刷新 Session Variables。");
      return;
    }
    const taskId = currentTask.id;
    const currentSessionId = Number(currentTask.session_id || sessionId() || 0);
    setSessionVariablesOpen(true);
    setSessionVariablesPayload(null);
    setSessionVariablesUpdatedAt(isDanceMimicTask() ? "正在读取 DanceMimic Session Variables..." : "正在运行 00 步骤...");
    await runAction("refresh-session-vars", async () => {
      if (isDanceMimicTask()) {
        const variables = await kbApi.readWorkspaceJson(currentSessionId, SESSION_VARIABLES_PATH);
        setSessionVariablesPayload(variables);
        setSessionVariablesUpdatedAt(new Date().toLocaleString());
        return;
      }
      const refreshed = await kbApi.refreshSessionVariables(taskId);
      await loadDetail(taskId);
      const variables = refreshed?.variables || await kbApi.readWorkspaceJson(currentSessionId, SESSION_VARIABLES_PATH);
      setSessionVariablesPayload(variables);
      setSessionVariablesUpdatedAt(refreshed?.updated_at || new Date().toLocaleString());
    });
  }

  function openRawSessionVariables() {
    const currentSessionId = sessionId();
    if (!currentSessionId) return;
    window.open(`${kbApi.rawFileUrl(currentSessionId, SESSION_VARIABLES_PATH)}?v=${Date.now()}`, "_blank", "noopener,noreferrer");
  }

  function beginPointerAssetDrag(event, item, sourceTab = "") {
    if (event.button !== 0 || !item?.path) return;
    if (ASSET_PANEL_TABS.has(sourceTab)) setActiveAssetTab(sourceTab);
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
      document.body.classList.remove("kbsp-dragging-asset");
      if (activeAssetDragCleanup === cleanup) activeAssetDragCleanup = null;
    };

    const onMove = (moveEvent) => {
      const distance = Math.hypot(moveEvent.clientX - startX, moveEvent.clientY - startY);
      if (distance < 8) return;
      moved = true;
      document.body.classList.add("kbsp-dragging-asset");
      moveEvent.preventDefault();
    };

    const onUp = (upEvent) => {
      if (finished) return;
      finished = true;
      cleanup();
      if (!moved) return;
      const target = dropTargetFromPoint(upEvent.clientX, upEvent.clientY);
      const dialogueId = target?.getAttribute("data-kbsp-dialogue-id");
      const targetKind = target?.getAttribute("data-kbsp-drop-kind") || "";
      if (dialogueId) {
        const requiredKind = expectedAssetKind(targetKind);
        if (requiredKind && assetKind(item) !== requiredKind) return;
        assignAsset(dialogueId, item, targetKind);
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

  function clickAsset(item) {
    if (suppressNextAssetClick) return;
    setSelectedAsset(item);
    window.getSelection?.()?.removeAllRanges();
  }

  function addDialogueAfter(shotId, sceneId, dialogueId) {
    let nextDialogueId = "";
    updatePlan((draft) => {
      const shotIndex = draft.shots.findIndex((item) => item.shot_id === shotId);
      const shot = draft.shots[shotIndex];
      const scene = shot?.scenes?.find((item) => item.scene_id === sceneId);
      const index = scene?.dialogues?.findIndex((item) => item.dialogue_id === dialogueId) ?? -1;
      if (!scene || index < 0) return;
      const token = Date.now();
      const next = {
        ...newDialogueFields(draft, token),
        text: "",
        duration: 0,
      };
      scene.dialogues.splice(index + 1, 0, next);
      nextDialogueId = next.dialogue_id;
      setSelectedShotIndex(shotIndex);
      setSelectedDialogueId(next.dialogue_id);
      setEditingDialogueId(next.dialogue_id);
      setScope("scene");
      setSceneAudioState({});
    });
    if (nextDialogueId) focusDialogueEditor(nextDialogueId);
  }

  function splitScene(shotId, sceneId, dialogueId) {
    updatePlan((draft) => {
      const shot = draft.shots.find((item) => item.shot_id === shotId);
      const sceneIndex = shot?.scenes?.findIndex((item) => item.scene_id === sceneId) ?? -1;
      const scene = shot?.scenes?.[sceneIndex];
      const dialogueIndex = scene?.dialogues?.findIndex((item) => item.dialogue_id === dialogueId) ?? -1;
      if (!shot || !scene || dialogueIndex < 0 || dialogueIndex >= scene.dialogues.length - 1) return;
      const moved = scene.dialogues.splice(dialogueIndex + 1);
      const newScene = { ...copy(scene), scene_id: `${shot.shot_id}_scene_new_${Date.now()}`, scene_name: "New Scene", dialogues: moved };
      shot.scenes.splice(sceneIndex + 1, 0, newScene);
      setSelectedDialogueId(moved[0]?.dialogue_id || "");
      setEditingDialogueId("");
      setScope("scene");
      setSceneAudioState({});
      setGroupingDirty(true);
    });
  }

  function splitShot(shotId, sceneId, dialogueId) {
    updatePlan((draft) => {
      const shotIndex = draft.shots.findIndex((item) => item.shot_id === shotId);
      const shot = draft.shots[shotIndex];
      if (!shot) return;
      const sceneIndex = shot.scenes.findIndex((item) => item.scene_id === sceneId);
      const scene = shot.scenes[sceneIndex];
      const dialogueIndex = scene?.dialogues?.findIndex((item) => item.dialogue_id === dialogueId) ?? -1;
      if (!scene || dialogueIndex < 0) return;
      const newScenes = [];
      if (dialogueIndex < scene.dialogues.length - 1) {
        const movedDialogues = scene.dialogues.splice(dialogueIndex + 1);
        newScenes.push({ ...copy(scene), scene_id: `shot_new_scene_${Date.now()}`, dialogues: movedDialogues });
      }
      newScenes.push(...shot.scenes.splice(sceneIndex + 1));
      if (!newScenes.length) return;
      const newShot = { ...copy(shot), shot_id: `shot_new_${Date.now()}`, shot_name: "New Shot", scenes: newScenes };
      draft.shots.splice(shotIndex + 1, 0, newShot);
      setSelectedShotIndex(shotIndex + 1);
      setSelectedDialogueId(newScenes[0]?.dialogues?.[0]?.dialogue_id || "");
      setEditingDialogueId("");
      setScope("scene");
      setSceneAudioState({});
      setGroupingDirty(true);
    });
  }

  function mergeDialogueUp(shotId, sceneId, dialogueId) {
    updatePlan((draft) => {
      const scene = draft.shots.find((item) => item.shot_id === shotId)?.scenes?.find((item) => item.scene_id === sceneId);
      const index = scene?.dialogues?.findIndex((item) => item.dialogue_id === dialogueId) ?? -1;
      if (!scene || index <= 0) return;
      const previous = scene.dialogues[index - 1];
      const current = scene.dialogues[index];
      previous.text = [previous.text, current.text].map((item) => String(item || "").trim()).filter(Boolean).join(" ");
      previous.duration = Number((Number(previous.duration || 0) + Number(current.duration || 0)).toFixed(3));
      previous.bound_image_path ||= current.bound_image_path || "";
      scene.dialogues.splice(index, 1);
      setSelectedDialogueId(previous.dialogue_id);
      setEditingDialogueId("");
      setScope("scene");
      setSceneAudioState({});
    });
  }

  function mergeSceneUp(shotId, sceneId) {
    updatePlan((draft) => {
      const shot = draft.shots.find((item) => item.shot_id === shotId);
      const index = shot?.scenes?.findIndex((item) => item.scene_id === sceneId) ?? -1;
      if (!shot || index <= 0) return;
      const previousSceneId = shot.scenes[index - 1].scene_id;
      shot.scenes[index - 1].dialogues.push(...shot.scenes[index].dialogues);
      shot.scenes.splice(index, 1);
      setSelectedDialogueId(`${previousSceneId}_dialogue_001`);
      setEditingDialogueId("");
      setScope("scene");
      setSceneAudioState({});
      setGroupingDirty(true);
    });
  }

  function mergeShotUp(shotIndex) {
    updatePlan((draft) => {
      if (shotIndex <= 0 || !draft.shots?.[shotIndex]) return;
      draft.shots[shotIndex - 1].scenes.push(...draft.shots[shotIndex].scenes);
      draft.shots.splice(shotIndex, 1);
      setSelectedShotIndex(shotIndex - 1);
      setSelectedDialogueId(draft.shots[shotIndex - 1]?.scenes?.[0]?.scene_id ? `${draft.shots[shotIndex - 1].scenes[0].scene_id}_dialogue_001` : "");
      setEditingDialogueId("");
      setScope("scene");
      setSceneAudioState({});
      setGroupingDirty(true);
    });
  }

  function deleteDialogue(shotId, sceneId, dialogueId) {
    updatePlan((draft) => {
      const shot = draft.shots.find((item) => item.shot_id === shotId);
      const scene = shot?.scenes?.find((item) => item.scene_id === sceneId);
      const total = allDialogues(shot).length;
      const index = scene?.dialogues?.findIndex((item) => item.dialogue_id === dialogueId) ?? -1;
      if (!scene || index < 0 || total <= 1) return;
      scene.dialogues.splice(index, 1);
      if (!scene.dialogues.length) shot.scenes = shot.scenes.filter((item) => item.scene_id !== sceneId);
      setSelectedDialogueId(shot.scenes?.[0]?.dialogues?.[0]?.dialogue_id || "");
      setEditingDialogueId("");
      setScope("scene");
      setSceneAudioState({});
    });
  }

  function currentStoryboardAgentSelection() {
    const shot = shots()[selectedShotIndex()] || shots()[0] || {};
    let sceneId = "";
    if (selectedDialogueId()) {
      for (const shotItem of shots()) {
        for (const scene of shotItem.scenes || []) {
          if ((scene.dialogues || []).some((dialogue) => dialogue.dialogue_id === selectedDialogueId())) {
            return {
              scope: scope(),
              shot_id: shotItem.shot_id || "",
              scene_id: scene.scene_id || "",
              dialogue_id: selectedDialogueId(),
            };
          }
        }
      }
    }
    sceneId = shot?.scenes?.[0]?.scene_id || "";
    return {
      scope: scope(),
      shot_id: shot?.shot_id || "",
      scene_id: sceneId,
      dialogue_id: selectedDialogueId(),
    };
  }

  function storyboardAgentClientContext() {
    const selection = currentStoryboardAgentSelection();
    return {
      selection,
      dirty: dirty(),
      focused_plan_excerpt: focusedStoryboardExcerpt(plan(), selection),
    };
  }

  function storyboardAgentChips() {
    const selection = currentStoryboardAgentSelection();
    return [
      { label: "Scope", value: selection.scope || "-" },
      { label: "Shot", value: selection.shot_id || "-" },
      { label: "Scene", value: selection.scene_id || "-" },
      { label: "Dirty", value: dirty() ? "是" : "否" },
    ];
  }

  function renderStoryboardAgentCandidate(candidate, helpers) {
    if (candidate.kind !== "storyboard_edit_candidate") {
      return <article class="kbsp-agent-candidate"><strong>{candidate.title}</strong><pre>{JSON.stringify(candidate.payload, null, 2)}</pre></article>;
    }
    const payload = candidate.payload || {};
    const operations = Array.isArray(payload.operations) ? payload.operations : [];
    return <article class="kbsp-agent-candidate">
      <strong>{payload.title || candidate.title || "StoryBoard 修改建议"}</strong>
      <Show when={payload.summary}><p>{payload.summary}</p></Show>
      <p>{operations.length} 个操作，将只应用到当前草稿。</p>
      <Show when={Array.isArray(payload.warnings) && payload.warnings.length}>
        <ul>
          <For each={payload.warnings}>{(item) => <li>{String(item || "")}</li>}</For>
        </ul>
      </Show>
      <div class="kbsp-agent-candidate-actions">
        <button type="button" onClick={() => {
          const result = applyStoryboardEditCandidate(plan(), candidate);
          if (!result.ok) {
            helpers.setError?.(result.error || "候选应用失败");
            return;
          }
          setPlan(result.plan);
          setDirty(true);
          if (result.structural) {
            setGroupingDirty(true);
            setSceneAudioState({});
          }
          helpers.setError?.("");
        }}>应用到草稿</button>
        <button type="button" class="secondary" onClick={() => navigator.clipboard?.writeText(JSON.stringify(payload, null, 2))}>复制建议</button>
      </div>
    </article>;
  }

  const renderSessionVariablesModal = () => <Show when={sessionVariablesOpen()}>
    <div class="kbsp-vpm-backdrop kbsp-vars-backdrop" role="dialog" aria-modal="true" aria-label="Session Variables" onClick={() => setSessionVariablesOpen(false)}>
      <section class="kbsp-vpm-shell kbsp-vars-shell" onClick={(event) => event.stopPropagation()}>
        <header class="kbsp-vpm-header kbsp-vars-header">
          <div class="kbsp-vpm-title-wrap">
            <div class="kbsp-vpm-mark"><CodeIcon /></div>
            <div class="kbsp-vpm-title-copy">
              <h2>Session Variables</h2>
              <p class="kbsp-vpm-summary-line">
                <span>{SESSION_VARIABLES_PATH}</span>
                <Show when={sessionVariablesUpdatedAt()}><span>updated {sessionVariablesUpdatedAt()}</span></Show>
              </p>
            </div>
          </div>
          <div class="kbsp-vars-spacer" />
          <div class="kbsp-vpm-actions">
            <button type="button" title="重新运行 00" aria-label="重新运行 00" disabled={Boolean(busy()) || dirty()} onClick={() => void refreshSessionVariables()}><RefreshIcon /></button>
            <button type="button" title="打开原始文件" aria-label="打开原始文件" disabled={!sessionId()} onClick={openRawSessionVariables}><DocumentIcon /></button>
            <button type="button" title="复制 JSON" aria-label="复制 JSON" disabled={!sessionVariablesPayload()} onClick={() => navigator.clipboard?.writeText(prettyJson(sessionVariablesPayload()))}><CodeIcon /></button>
            <button type="button" title="关闭" aria-label="关闭" onClick={() => setSessionVariablesOpen(false)}><XIcon /></button>
          </div>
        </header>
        <div class="kbsp-vars-body">
          <Show when={sessionVariablesPayload()} fallback={<div class="kbsp-vars-loading">{busy() === "refresh-session-vars" || busy() === "load" ? "正在运行 00 并读取最新 Session Variables..." : "还没有读取到 Session Variables。"}</div>}>
            <pre class="kbsp-vars-code">{prettyJson(sessionVariablesPayload())}</pre>
          </Show>
        </div>
      </section>
    </div>
  </Show>;

  const renderDetail = () => <div class={`kbsp-editor ${theme() === "dark" ? "is-dark" : ""} ${editingDialogueId() ? "is-editing-dialogue" : ""}`}>
    <div class="kbsp-main-row">
      <KouboSidebar shots={shots} selectedShotIndex={selectedShotIndex} selectedDialogueId={selectedDialogueId} setSelectedShotIndex={setSelectedShotIndex} setSelectedDialogueId={setSelectedDialogueId} setScope={setScope} theme={theme} setTheme={setTheme} leftWidth={leftWidth} startResize={(event) => { event.preventDefault(); setResizeState({ startX: event.clientX, startWidth: leftWidth() }); }} scrollToNode={scrollToNode} updateShotName={updateShotName} setDialogueTalkingHead={setDialogueTalkingHead} dialogueVideoPlanBusy={() => busy() === "save-dialogue-video-plan"} />
      <main class="kbsp-workspace">
        <div class="kbsp-workspace-inner">
          <KouboEditorHeader
            task={task}
            meta={meta}
            busy={busy}
            dirty={dirty}
            fixedMenuOpen={fixedMenuOpen}
            setFixedMenuOpen={setFixedMenuOpen}
            timingMenuOpen={timingMenuOpen}
            setTimingMenuOpen={setTimingMenuOpen}
            fixedShotSeconds={fixedShotSeconds}
            setFixedShotSeconds={setFixedShotSeconds}
            fixedSceneSeconds={fixedSceneSeconds}
            setFixedSceneSeconds={setFixedSceneSeconds}
            timingModel={timingModel}
            buildGSecondsPerChar={buildGSecondsPerChar}
            refreshDialogueTimingsOnly={refreshDialogueTimingsOnly}
            openAudioSettings={openAudioSettings}
            audioSettings={audioSettings}
            saveAudioSettings={saveAudioSettings}
            ttsProviderOptions={ttsProviderOptions}
            ttsModelsForProvider={ttsModelsForProvider}
            ttsVoicesForModel={ttsVoicesForModel}
            reorganizeFixedStoryboard={reorganizeFixedStoryboard}
            refreshSessionVariables={refreshSessionVariables}
            savePlan={savePlan}
            setHostProductBuilderOpen={setHostProductBuilderOpen}
            openStoryboardAgent={() => setStoryboardAgentOpen(true)}
            roleAccess={props.roleAccess}
          />
          <Show when={shots().length} fallback={<div class="kbsp-empty">尚未选择镜头。</div>}>
            <div class="kbsp-shot-scroll">
              <div class="kbsp-shot-stack">
                <For each={shots()}>{(shot, shotIndex) => <ShotCard shot={shot} shotIndex={shotIndex} selectedDialogueId={selectedDialogueId} editingDialogueId={editingDialogueId} setEditingDialogueId={setEditingDialogueId} selectedAsset={selectedAsset} setSelectedShotIndex={setSelectedShotIndex} selectDialogue={selectDialogue} updateShotName={updateShotName} updateDialogue={updateDialogue} setDialogueTalkingHead={setDialogueTalkingHead} dialogueVideoPlanBusy={() => busy() === "save-dialogue-video-plan"} bumpDuration={bumpDuration} assignAsset={assignAsset} allowAssetDrop={allowAssetDrop} dropAsset={dropAsset} openImage={setImagePreview} sessionId={sessionId} mediaVersion={mediaVersion} videoSlotState={videoSlotState} addDialogueAfter={addDialogueAfter} splitScene={splitScene} splitShot={splitShot} mergeDialogueUp={mergeDialogueUp} mergeSceneUp={mergeSceneUp} mergeShotUp={mergeShotUp} deleteDialogue={deleteDialogue} playSceneTTS={playSceneTTS} sceneAudioState={sceneAudioState} playbackPhase={() => playbackState().phase} playbackCurrentSceneId={() => playbackState().currentSceneId} />}</For>
              </div>
            </div>
          </Show>
        </div>
      </main>
    </div>
    <KouboAgentDrawer
      open={storyboardAgentOpen}
      setOpen={setStoryboardAgentOpen}
      task={task}
      api={kbApi}
      agentKey="storyboard_edit"
      title="故事版 Agent"
      subtitle="台词和结构草稿建议"
      greeting="我可以帮你改写当前台词、整理镜头结构，并把建议应用到草稿。"
      placeholder="例如：把当前 scene 的口播改得更自然，保留原意。"
      contextChips={storyboardAgentChips}
      buildClientContext={storyboardAgentClientContext}
      renderCandidate={renderStoryboardAgentCandidate}
    />
    <KouboTimeline shots={shots} totalDuration={totalDuration} selectedShotIndex={selectedShotIndex} selectedDialogueId={selectedDialogueId} setSelectedShotIndex={setSelectedShotIndex} setSelectedDialogueId={setSelectedDialogueId} scope={scope} setScope={setScope} scrollToNode={scrollToNode} playbackPhase={() => playbackState().phase} playbackStatus={() => videoOnlyPlanState().status || imagePlanState().status || playbackState().status} playbackCurrentShotId={() => playbackState().currentShotId} playbackCurrentSceneId={() => playbackState().currentSceneId} toggleTimelinePlayback={toggleTimelinePlayback} playbackSpeed={playbackSpeed} playbackSpeedOpen={playbackSpeedOpen} setPlaybackSpeedOpen={setPlaybackSpeedOpen} applyPlaybackSpeed={applyPlaybackSpeed} composerSettings={composerSettings} composerParamsOpen={composerParamsOpen} setComposerParamsOpen={setComposerParamsOpen} applyComposerSettings={applyComposerSettings} openComposer={openComposer} composerBusy={composerBusy} composerDisabledReason={composerDisabledReason} generateVideoOnlyPlan={generateVideoOnlyPlan} videoOnlyPlanBusy={videoOnlyPlanBusy} videoOnlyPlanDisabledReason={videoOnlyPlanDisabledReason} generateImagePlan={generateImagePlan} executeImagePlan={executeImagePlan} imagePlanBusy={imagePlanBusy} imagePlanDisabledReason={imagePlanDisabledReason} videoPlanSettings={videoPlanSettings} videoPlanParamsOpen={videoPlanParamsOpen} setVideoPlanParamsOpen={setVideoPlanParamsOpen} applyVideoPlanSettings={applyVideoPlanSettings} openVideoPlan={openVideoPlan} currentVideoPlanTarget={currentVideoPlanTarget} videoPlanBusy={videoPlanBusy} videoPlanDisabledReason={videoPlanDisabledReason} />
  </div>;

  return <div class="kbsp-module">
    <Show when={error()}><div class="banner bad">{error()}</div></Show>
    <Show when={routeNavigationNotice()}><div class="banner warning" data-kbsp-route-notice>{routeNavigationNotice()}</div></Show>
    <ErrorBoundary fallback={(err) => <div class="banner bad">{err instanceof Error ? err.message : String(err)}</div>}>
      <Show when={busy() === "load"}><div class="kbsp-empty">Loading 故事版（口播）...</div></Show>
      <Show when={route().view === "list"}><KouboTaskList items={items} /></Show>
      <Show when={route().view === "detail" && state()}>{renderDetail()}</Show>
      <KouboHostProductBuilder
        open={hostProductBuilderOpen}
        setOpen={setHostProductBuilderOpen}
        task={task}
        api={kbApi}
        runAction={runAction}
        assetUrl={(path) => kbApi.rawFileUrl(sessionId(), path)}
        openImage={setImagePreview}
        icons={{ CodeIcon, DocumentIcon, PlayIcon, PlusIcon, RefreshIcon, TrashIcon, XIcon }}
        roleAccess={props.roleAccess}
      />
      <CleanImagePanel
        open={cleanImageOpen}
        setOpen={setCleanImageOpen}
        target={cleanImageTarget}
        task={task}
        plan={plan}
        api={kbApi}
        runAction={runAction}
        dialogueOptions={dialogueOptions}
        referenceImageOptions={referenceImageOptions}
        assetUrl={(path) => kbApi.rawFileUrl(sessionId(), path)}
        selectedDialogueId={selectedDialogueId}
        applyTaskPayload={applyAssetLibraryResult}
        applyAssetPayload={applyAssetLibraryMetaResult}
        openUploadAssets={() => setActiveAssetTab("upload")}
        openHostProductBuilder={() => setHostProductBuilderOpen(true)}
        scrollToDialogue={scrollToDialogue}
      />
      <KouboVideoPlanModal open={videoPlanOpen} setOpen={setVideoPlanOpen} result={videoPlanResult} executePlan={executeVideoPlan} refreshExecution={refreshVideoPlanExecution} task={task} api={kbApi} applySettings={applyVideoPlanSettings} openVideoPlan={openVideoPlan} />
      <KouboVideoOnlyPlanModal open={videoOnlyPlanOpen} setOpen={setVideoOnlyPlanOpen} result={videoOnlyPlanResult} executePlan={executeVideoOnlyPlan} materializeTailFrame={materializeVideoOnlyTailFrame} confirmFinal={confirmVideoOnlyFinal} refreshExecution={refreshVideoOnlyPlanExecution} task={task} api={kbApi} setError={setError} />
      <KouboImagePlanModal open={imagePlanOpen} setOpen={setImagePlanOpen} result={imagePlanResult} executePlan={executeImagePlan} refreshExecution={refreshImagePlanExecution} task={task} api={kbApi} setError={setError} />
      <KouboComposerModal open={composerOpen} setOpen={setComposerOpen} result={composerResult} state={composerState} settings={composerSettings} executeComposer={executeComposer} refreshExecution={refreshComposerExecution} regenerateVideoPlan={openFullVideoPlanFromComposer} task={task} api={kbApi} assetUrl={(path) => kbApi.rawFileUrl(sessionId(), path)} />
      {renderSessionVariablesModal()}
      <ImagePreview image={imagePreview} setImage={setImagePreview} sessionId={sessionId} />
    </ErrorBoundary>
  </div>;
}
