import { For, Match, Show, Switch, createEffect, createMemo, createSignal, onCleanup } from "solid-js";
import { kbApi } from "../KouboStoryBoard/kouboStoryboardApi.js";
import KouboAgentDrawer from "../KouboStoryBoard/components/KouboAgentDrawer.jsx";
import { aspectFromPrompt, assetKind, assetLabel, filterAssets, historyAssetItems, imageAssets, imageSizeForAspect } from "./uploadAssetLibraryModel.js";
import LibrarySidebar from "./components/LibrarySidebar.jsx";
import ImageToolbar from "./components/ImageToolbar.jsx";
import ImageGrid from "./components/ImageGrid.jsx";
import HistoryGrid from "./components/HistoryGrid.jsx";
import VideoWorkspaceLibrary from "./components/VideoWorkspaceLibrary.jsx";
import VideoAgentPanel from "./components/VideoAgentPanel.jsx";
import AgentPanel from "./components/AgentPanel.jsx";
import DigitalHumanWorkspace from "./digitalHuman/DigitalHumanWorkspace.jsx";
import DigitalHumanAgentPanel from "./digitalHuman/DigitalHumanAgentPanel.jsx";
import PromptAgentWorkspace from "./promptAgent/PromptAgentWorkspace.jsx";
import PromptAgentPanel from "./promptAgent/PromptAgentPanel.jsx";
import SearchAgentWorkspace, { createSearchAgentController } from "./searchAgent/SearchAgentWorkspace.jsx";
import SearchAgentPanel from "./searchAgent/SearchAgentPanel.jsx";
import TtsAgentWorkspace from "./ttsAgent/TtsAgentWorkspace.jsx";
import TtsAgentPanel from "./ttsAgent/TtsAgentPanel.jsx";
import { createTtsAgentController } from "./ttsAgent/ttsAgentModel.js";
import "./styles/index.css";

const IMAGE_UPLOAD_EXT = /\.(png|jpe?g|webp)$/i;
const VIDEO_UPLOAD_EXT = /\.(mp4|mov|webm|m4v)$/i;
const THUMBNAIL_SUPPORTED_EXT = /\.(jpe?g|png|webp|bmp|mp4|mov|m4v|webm)$/i;
const AUDIO_UPLOAD_EXT = /\.(wav|m4a|mp3|aac|ogg|oga|flac|opus|aiff|aif|caf|weba|wma)$/i;
const LIBRARY_VIEWS = new Set(["images", "images-agent", "videos", "videos-agent", "tts-agent", "digital-human-agent", "prompt-agent", "search-agent", "history"]);
const IMAGE_COLUMN_OPTIONS = new Set([4, 6, 8]);
const AGENT_VIDEO_PROGRESS_MAX = 98;
const MEDIA_UPLOAD_FORMATS = {
  videos: "MP4, MOV, WEBM, or M4V",
  audio: "WAV, MP3, M4A, AAC, OGG, FLAC, OPUS, AIFF, CAF, WEBA, or WMA",
};
const IMAGE_UPLOAD_BATCH_MAX_FILES = 12;
const IMAGE_UPLOAD_BATCH_MAX_BYTES = 24 * 1024 * 1024;
const MEDIA_UPLOAD_BATCH_MAX_FILES = 1;
const MEDIA_UPLOAD_BATCH_MAX_BYTES = 64 * 1024 * 1024;

function isImageUploadFile(file) {
  const type = String(file?.type || "").toLowerCase();
  return ["image/png", "image/jpeg", "image/webp"].includes(type) || IMAGE_UPLOAD_EXT.test(file?.name || "");
}

function extensionFromType(type) {
  if (type === "image/jpeg") return "jpg";
  if (type === "image/webp") return "webp";
  return "png";
}

function normalizeImageUploadFile(file, prefix = "image", index = 0) {
  if (!file || !isImageUploadFile(file)) return null;
  const name = String(file.name || "").trim();
  if (name && IMAGE_UPLOAD_EXT.test(name)) return file;
  const ext = extensionFromType(String(file.type || "").toLowerCase());
  return new File([file], `${prefix}-${Date.now()}-${index + 1}.${ext}`, { type: file.type || `image/${ext}` });
}

function normalizeImageUploadFiles(files, prefix = "image") {
  return Array.from(files || [])
    .map((file, index) => normalizeImageUploadFile(file, prefix, index))
    .filter(Boolean);
}

function isMediaUploadFile(file, kind) {
  const type = String(file?.type || "").toLowerCase();
  const name = String(file?.name || "");
  if (kind === "videos") return type.startsWith("video/") || VIDEO_UPLOAD_EXT.test(name);
  if (kind === "audio") return type.startsWith("audio/") || AUDIO_UPLOAD_EXT.test(name);
  return false;
}

function normalizeMediaUploadFiles(files, kind) {
  return Array.from(files || []).filter((file) => isMediaUploadFile(file, kind));
}

function readDirectoryEntries(reader) {
  return new Promise((resolve) => {
    const results = [];
    const read = () => reader.readEntries((entries) => {
      if (!entries.length) return resolve(results);
      results.push(...entries);
      read();
    }, () => resolve(results));
    read();
  });
}

async function filesFromEntry(entry, prefix) {
  if (!entry) return [];
  if (entry.isFile) {
    return await new Promise((resolve) => entry.file((file) => resolve(normalizeImageUploadFiles([file], prefix)), () => resolve([])));
  }
  if (!entry.isDirectory) return [];
  const entries = await readDirectoryEntries(entry.createReader());
  const nested = await Promise.all(entries.map((item) => filesFromEntry(item, prefix)));
  return nested.flat();
}

async function filesFromDataTransfer(dataTransfer, prefix = "dropped-image") {
  const items = Array.from(dataTransfer?.items || []);
  if (items.length && items.some((item) => item.kind === "file")) {
    const nested = await Promise.all(items.filter((item) => item.kind === "file").map((item) => {
      const entry = item.webkitGetAsEntry?.();
      if (entry) return filesFromEntry(entry, prefix);
      const file = item.getAsFile?.();
      return Promise.resolve(normalizeImageUploadFiles(file ? [file] : [], prefix));
    }));
    return nested.flat();
  }
  return normalizeImageUploadFiles(dataTransfer?.files || [], prefix);
}

function filesFromClipboard(clipboardData, prefix = "pasted-image") {
  const itemFiles = Array.from(clipboardData?.items || [])
    .filter((item) => item.kind === "file")
    .map((item) => item.getAsFile?.())
    .filter(Boolean);
  const files = itemFiles.length ? itemFiles : Array.from(clipboardData?.files || []);
  return normalizeImageUploadFiles(files, prefix);
}

function uniqueAssets(items) {
  const seen = new Set();
  return Array.from(items || []).filter((item) => {
    const key = item?.path || item?.id || item?.history_path;
    if (!key) return true;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function prependUniqueAssets(previous, added) {
  const seen = new Set(previous.map((item) => item.path || item.id));
  return [...added.filter((item) => !seen.has(item.path || item.id)), ...previous];
}

function historyAssetKey(asset) {
  return asset?.history_path || asset?.path || asset?.id || "";
}

function compactAgentAsset(asset) {
  const path = String(asset?.path || asset?.history_path || asset?.id || "");
  return {
    id: String(asset?.id || path || ""),
    path,
    label: assetLabel(asset),
    kind: assetKind(asset),
    source: String(asset?.source || ""),
    shot_id: String(asset?.shot_id || ""),
    scene_id: String(asset?.scene_id || ""),
    dialogue_id: String(asset?.dialogue_id || ""),
    duration: asset?.duration ?? asset?.duration_seconds ?? null,
  };
}

function videoReferencePayloadItem(asset) {
  const path = String(asset?.path || asset?.history_path || asset?.id || "");
  const kind = assetKind(asset);
  return {
    path,
    role: asset?.role || asset?.reference_role || "",
    reference_role: asset?.reference_role || asset?.role || "",
    label: asset?.label || asset?.filename || path.split("/").pop() || "",
    key: asset?.key || "",
    kind,
    source: String(asset?.source || ""),
  };
}

function splitVideoReferencePayload(items = []) {
  const unique = uniqueAssets(items).map(videoReferencePayloadItem).filter((item) => item.path);
  return {
    reference_assets: unique,
    reference_images: unique.filter((item) => item.kind === "image"),
    reference_audios: unique.filter((item) => item.kind === "audio"),
    reference_videos: unique.filter((item) => item.kind === "video"),
  };
}

function isMaxSr2VideoSelection(options = {}) {
  const alias = String(options.agentVideoAlias || options.agent_video_alias || "").replace(/\s+/g, "").toLowerCase();
  if (alias === "maxsi2") return false;
  return alias === "maxsr2";
}

function isMaxSi2VideoSelection(options = {}) {
  const alias = String(options.agentVideoAlias || options.agent_video_alias || "").replace(/\s+/g, "").toLowerCase();
  return alias === "maxsi2";
}

function isMaxWr27VideoSelection(options = {}) {
  const alias = String(options.agentVideoAlias || options.agent_video_alias || "").replace(/\s+/g, "").toLowerCase();
  return alias === "maxwr27" || alias === "wan27";
}

function referenceModeForVideoGeneration(options = {}, references = {}) {
  const hasExplicitReferenceMode = Object.prototype.hasOwnProperty.call(options, "referenceMode")
    || Object.prototype.hasOwnProperty.call(options, "reference_mode");
  const explicit = String(options.referenceMode || options.reference_mode || "").trim();
  if (hasExplicitReferenceMode) {
    if (["selected_images", "images", "none"].includes(explicit.toLowerCase())) return "";
    return explicit;
  }
  if (isMaxSi2VideoSelection(options)) return "first_frame";
  if (isMaxWr27VideoSelection(options)) return "";
  if (isMaxSr2VideoSelection(options)) return "input_references";
  if ((references.reference_audios || []).length || (references.reference_videos || []).length) return "input_references";
  return "";
}

function hasAgentModelAliases(config) {
  return Array.isArray(config?.agent_model_aliases) && config.agent_model_aliases.length > 0;
}

function planHasWorkingAudio(plan) {
  for (const shot of (plan?.shots || [])) {
    for (const scene of (shot?.scenes || [])) {
      const sceneAudio = scene?.working_assets?.audio?.path;
      if (sceneAudio) return true;
      for (const dialogue of (scene?.dialogues || [])) {
        if (dialogue?.audio_path || dialogue?.working_assets?.audio?.path) return true;
      }
    }
  }
  return false;
}

export default function UploadAssetLibraryOverlay(props) {
  const viewStorageKey = () => `koubo-storyboard:asset-library-view:${props.task?.()?.id || "unknown"}`;
  const imageColumnsStorageKey = () => "koubo-storyboard:asset-library-image-columns";
  const routeView = () => {
    const value = typeof props.routeView === "function" ? props.routeView() : props.routeView;
    return LIBRARY_VIEWS.has(value) ? value : "";
  };
  const readStoredView = () => {
    try {
      const routed = routeView();
      if (routed) return routed;
      if (planHasWorkingAudio(typeof props.plan === "function" ? props.plan() : props.plan)) return "tts-agent";
      const stored = window.localStorage?.getItem(viewStorageKey());
      return LIBRARY_VIEWS.has(stored) ? stored : "images";
    } catch {
      return planHasWorkingAudio(typeof props.plan === "function" ? props.plan() : props.plan) ? "tts-agent" : "images";
    }
  };
  const writeStoredView = (nextView) => {
    try {
      if (LIBRARY_VIEWS.has(nextView)) window.localStorage?.setItem(viewStorageKey(), nextView);
    } catch {
      // View persistence is a refresh convenience only.
    }
  };
  const writeRouteView = (nextView) => {
    if (props.mode !== "page" || !LIBRARY_VIEWS.has(nextView)) return;
    const taskId = props.task?.()?.id || "";
    if (!taskId) return;
    const nextHash = `#/koubo-asset-library/tasks/${taskId}/${nextView}`;
    if (window.location.hash !== nextHash) window.location.hash = nextHash;
  };
  const readStoredImageColumns = () => {
    try {
      const stored = Number(window.localStorage?.getItem(imageColumnsStorageKey()) || 6);
      return IMAGE_COLUMN_OPTIONS.has(stored) ? stored : 6;
    } catch {
      return 6;
    }
  };
  const writeStoredImageColumns = (nextColumns) => {
    try {
      window.localStorage?.setItem(imageColumnsStorageKey(), String(nextColumns));
    } catch {
      // Column preference is a display convenience only.
    }
  };
  const [theme, setTheme] = createSignal("light");
  const [imageColumns, setImageColumnsValue] = createSignal(readStoredImageColumns());
  const setImageColumns = (nextColumns) => {
    const value = IMAGE_COLUMN_OPTIONS.has(Number(nextColumns)) ? Number(nextColumns) : 6;
    writeStoredImageColumns(value);
    setImageColumnsValue(value);
  };
  const [view, setViewValue] = createSignal(readStoredView());
  const [viewRestoredForTask, setViewRestoredForTask] = createSignal("");
  const setView = (nextView) => {
    const value = LIBRARY_VIEWS.has(nextView) ? nextView : "images";
    writeStoredView(value);
    setViewValue(value);
    writeRouteView(value);
  };
  createEffect(() => {
    const taskId = String(props.task?.()?.id || "");
    const restoreKey = routeView() ? `${taskId}:${routeView()}` : taskId;
    if (!taskId || viewRestoredForTask() === restoreKey) return;
    setViewRestoredForTask(restoreKey);
    setViewValue(readStoredView());
  });
  const [query, setQuery] = createSignal("");
  const [selectedIds, setSelectedIds] = createSignal(new Set());
  const [selectedDigitalHumanAudioKey, setSelectedDigitalHumanAudioKey] = createSignal("");
  const [historySelectionMode, setHistorySelectionMode] = createSignal(false);
  const [selectedHistoryIds, setSelectedHistoryIds] = createSignal(new Set());
  const [deletingHistoryBatch, setDeletingHistoryBatch] = createSignal(false);
  const [historyActionError, setHistoryActionError] = createSignal("");
  const [sidebarCollapsed, setSidebarCollapsed] = createSignal(false);
  const [movingIds, setMovingIds] = createSignal(new Set());
  const [pendingAssets, setPendingAssets] = createSignal([]);
  const [localUploadedAssets, setLocalUploadedAssets] = createSignal([]);
  const [localUploadedVideos, setLocalUploadedVideos] = createSignal([]);
  const [localUploadedAudios, setLocalUploadedAudios] = createSignal([]);
  const [uploadBusy, setUploadBusy] = createSignal(false);
  const [uploadStatus, setUploadStatus] = createSignal({ kind: "", tone: "", text: "" });
  const uploadStatusForKind = (kind) => {
    const current = uploadStatus();
    return current?.kind === kind ? current : { kind, tone: "", text: "" };
  };
  const [mediaAgentOpen, setMediaAgentOpen] = createSignal(false);
  const [promptAgentRefreshKey, setPromptAgentRefreshKey] = createSignal(0);
  const [pendingComposerPrompt, setPendingComposerPrompt] = createSignal(null);
  let composerApplyNonce = 0;
  const applyPromptAgentToGeneration = ({ target, prompt, negative } = {}) => {
    const view = target === "videos" ? "videos" : "images";
    setView(view);
    composerApplyNonce += 1;
    setPendingComposerPrompt({ target: view, prompt: String(prompt || ""), negative: String(negative || ""), nonce: composerApplyNonce });
  };
  // Clear once a target panel consumes the prompt so revisiting the view
  // (which remounts the panel) does not re-apply the stale prompt.
  const handleComposerPromptApplied = (nonce) => {
    setPendingComposerPrompt((current) => (current && current.nonce === nonce ? null : current));
  };
  const searchAgentController = createSearchAgentController({ task: props.task, api: kbApi, onAssetLibraryResult: props.onAssetLibraryResult });
  const ttsAgentController = createTtsAgentController({ task: props.task, meta: props.meta, plan: props.plan, sessionId: props.sessionId, api: kbApi, onAssetLibraryResult: props.onAssetLibraryResult, onNavigateToView: setView, active: () => view() === "tts-agent" });
  const images = createMemo(() => uniqueAssets([...pendingAssets(), ...localUploadedAssets(), ...imageAssets(props.images?.() || [])]));
  const videos = createMemo(() => uniqueAssets([...localUploadedVideos(), ...(props.videos?.() || []).filter((item) => assetKind(item) === "video")]));
  const audios = createMemo(() => uniqueAssets([...localUploadedAudios(), ...(props.audios?.() || []).filter((item) => assetKind(item) === "audio")]));
  const historyAssets = createMemo(() => historyAssetItems(props.historyVersions?.() || []));
  const mediaAgentKind = createMemo(() => "");
  const mediaAgentItems = createMemo(() => mediaAgentKind() === "audio" ? audios() : mediaAgentKind() === "video" ? videos() : []);
  const isImageLibraryView = createMemo(() => view() === "images" || view() === "images-agent");
  const isVideoLibraryView = createMemo(() => view() === "videos" || view() === "videos-agent");
  const showImageAgentPanel = createMemo(() => view() === "images");
  const showOpenCodeAgentPanel = createMemo(() => view() === "images-agent");
  const showVideoWorkspacePanel = createMemo(() => view() === "videos");
  const showVideoAgentPanel = createMemo(() => view() === "videos-agent");
  const showTtsAgentPanel = createMemo(() => view() === "tts-agent");
  const showDigitalHumanAgentPanel = createMemo(() => view() === "digital-human-agent");
  const showPromptAgentPanel = createMemo(() => view() === "prompt-agent");
  const showSearchAgentPanel = createMemo(() => view() === "search-agent");
  const showRightPanel = createMemo(() => showImageAgentPanel() || showOpenCodeAgentPanel() || showVideoWorkspacePanel() || showVideoAgentPanel() || showTtsAgentPanel() || showDigitalHumanAgentPanel() || showPromptAgentPanel() || showSearchAgentPanel());
  const currentItems = createMemo(() => {
    if (view() === "history") return historyAssets();
    if (isVideoLibraryView()) return videos();
    if (view() === "tts-agent") return audios();
    if (view() === "digital-human-agent") return videos();
    if (view() === "prompt-agent") return images();
    if (view() === "search-agent") return images();
    return images();
  });
  const filteredItems = createMemo(() => filterAssets(currentItems(), query()));
  const filteredVideoItems = createMemo(() => filterAssets(videos(), query()));
  const filteredImageItems = createMemo(() => filterAssets(images(), query()));
  const selectedItems = createMemo(() => {
    const ids = selectedIds();
    return images().filter((item) => ids.has(item.id || item.path));
  });
  const selectedVideoReferenceAssets = createMemo(() => {
    const ids = selectedIds();
    return uniqueAssets([...images(), ...videos(), ...audios()]).filter((item) => ids.has(item.id || item.path));
  });
  const selectedDigitalHumanAvatarImage = createMemo(() => selectedItems()[0] || null);
  const selectedDigitalHumanAudio = createMemo(() => {
    const key = selectedDigitalHumanAudioKey();
    if (!key) return null;
    return audios().find((item) => (item.id || item.path) === key || item.path === key) || null;
  });
  const selectDigitalHumanAudio = (asset) => {
    setSelectedDigitalHumanAudioKey(asset?.id || asset?.path || "");
  };
  const clearDigitalHumanAudio = () => {
    setSelectedDigitalHumanAudioKey("");
  };
  const dispatchReferenceAssetAdded = (asset) => {
    if (typeof window === "undefined" || !asset?.path) return;
    window.dispatchEvent(new CustomEvent("koubo-storyboard:asset-library-add-reference", {
      detail: { asset },
    }));
  };
  const addReferenceAsset = (asset) => {
    if (!asset?.path) return;
    const key = asset.id || asset.path;
    setSelectedIds((previous) => {
      const next = new Set(previous);
      next.add(key);
      return next;
    });
    dispatchReferenceAssetAdded(asset);
  };
  const removeReferenceAsset = (asset) => {
    const key = asset?.id || asset?.path;
    if (!key && !asset?.path) return;
    setSelectedIds((previous) => {
      const next = new Set(previous);
      if (key) next.delete(key);
      if (asset?.path) next.delete(asset.path);
      return next;
    });
  };
  const clearReferenceAssets = (assets = []) => {
    const targets = Array.isArray(assets) ? assets : [];
    setSelectedIds((previous) => {
      if (!targets.length) return new Set();
      const next = new Set(previous);
      for (const asset of targets) {
        const key = asset?.id || asset?.path;
        if (key) next.delete(key);
        if (asset?.path) next.delete(asset.path);
      }
      return next;
    });
  };
  if (typeof window !== "undefined") {
    const handleClearReferences = () => setSelectedIds(new Set());
    window.addEventListener("koubo-storyboard:asset-library-clear-references", handleClearReferences);
    onCleanup(() => window.removeEventListener("koubo-storyboard:asset-library-clear-references", handleClearReferences));
  }
  const selectedHistoryItems = createMemo(() => {
    const ids = selectedHistoryIds();
    return historyAssets().filter((item) => ids.has(historyAssetKey(item)));
  });
  const allVisibleHistorySelected = createMemo(() => {
    if (view() !== "history") return false;
    const items = filteredItems().filter((item) => historyAssetKey(item));
    if (!items.length) return false;
    const ids = selectedHistoryIds();
    return items.every((item) => ids.has(historyAssetKey(item)));
  });
  createEffect(() => {
    if (view() !== "history") {
      setHistorySelectionMode(false);
      setSelectedHistoryIds(new Set());
    }
    const available = new Set(historyAssets().map(historyAssetKey).filter(Boolean));
    setSelectedHistoryIds((previous) => {
      let changed = false;
      const next = new Set();
      for (const key of previous) {
        if (available.has(key)) next.add(key);
        else changed = true;
      }
      return changed ? next : previous;
    });
  });
  createEffect(() => {
    if (!mediaAgentKind()) setMediaAgentOpen(false);
  });
  const resolveGenerationReferences = (references) => {
    const byPath = new Map();
    const byRenamedFrom = new Map();
    for (const asset of [...images(), ...videos(), ...audios()]) {
      if (!asset?.path) continue;
      byPath.set(asset.path, asset);
      if (asset.renamed_from) byRenamedFrom.set(asset.renamed_from, asset);
    }
    return uniqueAssets((references || []).map((item) => {
      const path = item?.path || "";
      if (!path) return null;
      const currentAsset = byPath.get(path) || byRenamedFrom.get(path);
      if (currentAsset) {
        return {
          ...currentAsset,
          reference_role: item.reference_role || item.role || currentAsset.reference_role || currentAsset.role || "",
          role: item.role || item.reference_role || currentAsset.role || currentAsset.reference_role || "",
          label: item.label || currentAsset.label || currentAsset.filename || "",
        };
      }
      if (path.startsWith("SessionOutput/storyboard/assets/images/")) return null;
      return item;
    }).filter(Boolean));
  };
  const toggleSelected = (asset) => {
    const key = asset?.id || asset?.path;
    if (!key) return;
    let added = false;
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else {
        next.add(key);
        added = true;
      }
      return next;
    });
    if (added && isVideoLibraryView()) dispatchReferenceAssetAdded(asset);
  };
  const selectDigitalHumanAvatarImage = (asset) => {
    const key = asset?.id || asset?.path;
    if (!key) return;
    setSelectedIds(new Set([key]));
  };
  const clearDigitalHumanAvatarImage = () => {
    setSelectedIds(new Set());
  };
  const toggleHistorySelected = (asset) => {
    const key = historyAssetKey(asset);
    if (!key) return;
    setHistorySelectionMode(true);
    setSelectedHistoryIds((previous) => {
      const next = new Set(previous);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };
  const setHistorySelectionActive = (active) => {
    setHistorySelectionMode(active);
    if (!active) setSelectedHistoryIds(new Set());
  };
  const selectAllVisibleHistory = () => {
    const visibleKeys = filteredItems().map(historyAssetKey).filter(Boolean);
    if (!visibleKeys.length) return;
    setHistorySelectionMode(true);
    setSelectedHistoryIds((previous) => {
      const next = new Set(previous);
      if (allVisibleHistorySelected()) {
        visibleKeys.forEach((key) => next.delete(key));
      } else {
        visibleKeys.forEach((key) => next.add(key));
      }
      return next;
    });
  };
  const clearHistorySelection = () => setSelectedHistoryIds(new Set());
  const imageUrl = (asset) => {
    const path = asset?.path || asset?.history_path || "";
    return path && props.sessionId?.() ? kbApi.rawFileUrl(props.sessionId(), path) : "";
  };
  const thumbnailUrl = (asset) => {
    const path = asset?.path || asset?.history_path || "";
    return path && THUMBNAIL_SUPPORTED_EXT.test(path) && props.sessionId?.() ? kbApi.thumbnailFileUrl(props.sessionId(), path) : "";
  };
  const applyAssetResult = (result) => {
    props.onAssetLibraryResult?.(result);
  };
  let activeAgentVideoPendingId = "";
  let videoLibraryInitialized = false;
  let knownVideoLibraryKeys = new Set();
  const agentVideoPendingTimers = new Map();
  const agentVideoPendingStartedAt = new Map();
  const videoAssetKey = (asset = {}) => String(asset.path || asset.id || asset.filename || "");
  const isAgentVideoPendingAsset = (asset = {}) => Boolean(asset.pending && asset.source === "agent-generating");
  const agentVideoGenerationIdFromPending = (asset = {}) => String(asset.agent_generation_id || asset.request_id || asset.id || "").replace(/^agent_pending_video_/, "");
  const agentVideoGenerationIdFromAsset = (asset = {}) => String(
    asset.agent_generation_id
    || asset.origin?.agent_generation_id
    || asset.provider_result?.agent_generation_id
    || ""
  );
  const agentVideoPendingKey = (payload = {}) => {
    const id = payload.agent_generation_id || payload.request_id;
    return id ? `agent_pending_video_${id}` : "";
  };
  const stopAgentVideoPendingProgress = (id) => {
    const timer = agentVideoPendingTimers.get(id);
    if (timer) window.clearInterval(timer);
    agentVideoPendingTimers.delete(id);
    agentVideoPendingStartedAt.delete(id);
  };
  const startAgentVideoPendingProgress = (id) => {
    if (!id) return;
    if (agentVideoPendingTimers.has(id)) return;
    stopAgentVideoPendingProgress(id);
    agentVideoPendingStartedAt.set(id, Date.now());
    agentVideoPendingTimers.set(id, window.setInterval(() => {
      const startedAt = agentVideoPendingStartedAt.get(id) || Date.now();
      const elapsed = Math.max(0, (Date.now() - startedAt) / 1000);
      const percent = Math.min(AGENT_VIDEO_PROGRESS_MAX, Math.max(8, Math.round(8 + elapsed * 2)));
      setLocalUploadedVideos((previous) => previous.map((item) => item.id === id ? { ...item, progressLabel: `${percent}%` } : item));
    }, 1000));
  };
  const upsertAgentVideoPendingAsset = (payload = {}) => {
    const id = agentVideoPendingKey(payload);
    if (!id) return "";
    activeAgentVideoPendingId = id;
    const aspect = payload.aspect || payload.asset?.aspect || payload.asset?.aspect_ratio || "9:16";
    const pendingAsset = {
      id,
      label: aspect === "16:9" ? "正在生成 16:9 视频" : aspect === "9:16" ? "正在生成 9:16 视频" : "正在生成视频",
      filename: "正在生成视频",
      kind: "video",
      asset_type: "Video",
      source: "agent-generating",
      pending: true,
      aspect_ratio: aspect,
      progressLabel: "0%",
    };
    setLocalUploadedVideos((previous) => {
      const exists = previous.some((item) => item.id === id);
      return exists
        ? previous.map((item) => item.id === id ? { ...item, ...pendingAsset, progressLabel: item.progressLabel || pendingAsset.progressLabel } : item)
        : [pendingAsset, ...previous];
    });
    startAgentVideoPendingProgress(id);
    return id;
  };
  const resolveAgentVideoPendingIds = (payload = {}) => {
    const ids = [agentVideoPendingKey(payload), activeAgentVideoPendingId].filter(Boolean);
    return new Set(ids);
  };
  const completeAgentVideoPendingAsset = (payload = {}) => {
    const pendingIds = resolveAgentVideoPendingIds(payload);
    for (const id of agentVideoPendingTimers.keys()) stopAgentVideoPendingProgress(id);
    activeAgentVideoPendingId = "";
    const completedAssets = Array.isArray(payload.assets) && payload.assets.length ? payload.assets : [payload.asset].filter(Boolean);
    setLocalUploadedVideos((previous) => {
      const withoutPending = previous.filter((item) => !pendingIds.has(item.id) && !(completedAssets.length && isAgentVideoPendingAsset(item)));
      const seen = new Set(withoutPending.map((item) => item.path || item.id));
      return [...completedAssets.filter((item) => assetKind(item) === "video" && !seen.has(item.path || item.id)), ...withoutPending];
    });
  };
  const failAgentVideoPendingAsset = (payload = {}) => {
    const pendingIds = resolveAgentVideoPendingIds(payload);
    pendingIds.forEach((id) => stopAgentVideoPendingProgress(id));
    activeAgentVideoPendingId = "";
    setLocalUploadedVideos((previous) => previous.map((item) => pendingIds.has(item.id)
      ? { ...item, failed: true, pending: false, progressLabel: "Failed" }
      : item));
  };
  createEffect(() => {
    const realVideos = (props.videos?.() || []).filter((item) => assetKind(item) === "video");
    const nextKeys = new Set(realVideos.map(videoAssetKey).filter(Boolean));
    const newKeys = [...nextKeys].filter((key) => !knownVideoLibraryKeys.has(key));
    const pendingItems = localUploadedVideos().filter(isAgentVideoPendingAsset);
    if (videoLibraryInitialized && pendingItems.length && newKeys.length) {
      const completedGenerationIds = new Set(realVideos.map(agentVideoGenerationIdFromAsset).filter(Boolean));
      const matchedPendingIds = new Set(pendingItems
        .filter((item) => completedGenerationIds.has(agentVideoGenerationIdFromPending(item)))
        .map((item) => item.id)
        .filter(Boolean));
      const shouldClearAllAgentPending = matchedPendingIds.size === 0;
      setLocalUploadedVideos((previous) => previous.filter((item) => {
        if (!isAgentVideoPendingAsset(item)) return true;
        if (shouldClearAllAgentPending) {
          stopAgentVideoPendingProgress(item.id);
          return false;
        }
        if (matchedPendingIds.has(item.id)) {
          stopAgentVideoPendingProgress(item.id);
          return false;
        }
        return true;
      }));
      if (shouldClearAllAgentPending || (activeAgentVideoPendingId && matchedPendingIds.has(activeAgentVideoPendingId))) {
        activeAgentVideoPendingId = "";
      }
    }
    knownVideoLibraryKeys = nextKeys;
    videoLibraryInitialized = true;
  });
  onCleanup(() => {
    for (const id of agentVideoPendingTimers.keys()) stopAgentVideoPendingProgress(id);
  });
  const handleAgentImageGenerationEvent = (event) => {
    if (event?.type !== "asset_agent.image_generation.completed") return;
    const payload = event.properties || {};
    applyAssetResult(payload);
  };
  const handleAgentVideoGenerationEvent = (event) => {
    const payload = event.properties || {};
    if (event?.type === "asset_agent.video_generation.started") {
      upsertAgentVideoPendingAsset(payload);
      return;
    }
    if (event?.type === "asset_agent.video_generation.failed") {
      failAgentVideoPendingAsset(payload);
      return;
    }
    if (event?.type !== "asset_agent.video_generation.completed") return;
    completeAgentVideoPendingAsset(payload);
    const asset = payload.asset;
    if (asset?.path && assetKind(asset) !== "video") {
      applyAssetResult(payload);
      return;
    }
    applyAssetResult(payload);
  };
  const updateLocalAsset = (assetId, previousPath, nextAsset) => {
    const updateList = (items) => items.map((item) => {
      const key = item?.id || item?.path;
      return key === assetId || item?.path === previousPath ? nextAsset : item;
    });
    setLocalUploadedAssets(updateList);
    setLocalUploadedVideos(updateList);
    setLocalUploadedAudios(updateList);
  };
  const removeLocalAsset = (assetId, path) => {
    const targetKeys = new Set([assetId, path].filter(Boolean));
    const updateList = (items) => items.filter((item) => {
      const key = item?.id || item?.path;
      const itemKeys = [
        key,
        item?.path,
        item?.audio_path,
        item?.history_path,
        item?.agent_session_path,
        item?.json_path,
        item?.origin?.json_path,
        item?.tts_agent_session?.json_path,
        item?.tts_agent_session?.agent_session_path,
      ].filter(Boolean);
      return !itemKeys.some((itemKey) => targetKeys.has(itemKey));
    });
    setLocalUploadedAssets(updateList);
    setLocalUploadedVideos(updateList);
    setLocalUploadedAudios(updateList);
  };
  const uploadImageFiles = async (files, options = {}) => {
    const taskId = props.task?.()?.id;
    if (!taskId) throw new Error("Task is not loaded");
    const imageFiles = normalizeImageUploadFiles(files, options.source || "uploaded-image");
    if (!imageFiles.length) return [];
    setUploadBusy(true);
    const addedImagesTotal = [];
    try {
      const result = await kbApi.uploadAssetsBatched(taskId, imageFiles, {
        maxFiles: IMAGE_UPLOAD_BATCH_MAX_FILES,
        maxBytes: IMAGE_UPLOAD_BATCH_MAX_BYTES,
        onProgress: ({ batch, completedFiles, totalFiles }) => {
          setUploadStatus({
            kind: "image",
            tone: "info",
            text: totalFiles > 1
              ? `正在上传图片 ${completedFiles + 1}-${Math.min(completedFiles + batch.length, totalFiles)}/${totalFiles}...`
              : "正在上传图片 1/1...",
          });
        },
        onBatch: (batchResult) => {
          const added = imageAssets(batchResult?.added || []);
          if (added.length) {
            addedImagesTotal.push(...added);
            setLocalUploadedAssets((previous) => prependUniqueAssets(previous, added));
          }
          props.onAssetLibraryResult?.(batchResult);
        },
      });
      const added = imageAssets(result?.added || []);
      setUploadStatus(added.length
        ? { kind: "image", tone: "success", text: `已上传 ${added.length} 张图片。` }
        : { kind: "image", tone: "error", text: "没有新增图片素材。请确认文件格式为 PNG、JPG 或 WEBP。" });
      return result?.added || [];
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setUploadStatus({
        kind: "image",
        tone: "error",
        text: addedImagesTotal.length ? `已上传 ${addedImagesTotal.length} 张图片，后续上传失败：${message}` : message,
      });
      if (addedImagesTotal.length) return addedImagesTotal;
      throw err;
    } finally {
      setUploadBusy(false);
    }
  };
  const uploadMediaFiles = async (files, kind, options = {}) => {
    const taskId = props.task?.()?.id;
    if (!taskId) throw new Error("Task is not loaded");
    const requestedFiles = Array.from(files || []);
    const mediaFiles = normalizeMediaUploadFiles(files, kind);
    if (!mediaFiles.length) {
      if (requestedFiles.length) {
        setUploadStatus({
          kind,
          tone: "error",
          text: `Unsupported ${kind === "audio" ? "audio" : "video"} file type. Use ${MEDIA_UPLOAD_FORMATS[kind] || "a supported media format"}.`,
        });
      }
      return [];
    }
    if (mediaFiles.length < requestedFiles.length) {
      setUploadStatus({
        kind,
        tone: "warning",
        text: `Skipped ${requestedFiles.length - mediaFiles.length} unsupported file${requestedFiles.length - mediaFiles.length === 1 ? "" : "s"}. Use ${MEDIA_UPLOAD_FORMATS[kind] || "a supported media format"}.`,
      });
    } else {
      setUploadStatus({ kind, tone: "", text: "" });
    }
    setUploadBusy(true);
    const addedVideosTotal = [];
    const addedAudiosTotal = [];
    try {
      const result = await kbApi.uploadAssetsBatched(taskId, mediaFiles, {
        maxFiles: MEDIA_UPLOAD_BATCH_MAX_FILES,
        maxBytes: MEDIA_UPLOAD_BATCH_MAX_BYTES,
        onProgress: ({ index, total }) => {
          setUploadStatus({
            kind,
            tone: "info",
            text: `正在上传${kind === "videos" ? "视频" : "音频"} ${index}/${total}...`,
          });
        },
        onBatch: (batchResult) => {
          const batchAdded = Array.isArray(batchResult?.added) ? batchResult.added : [];
          const addedVideos = batchAdded.filter((item) => assetKind(item) === "video");
          const addedAudios = batchAdded.filter((item) => assetKind(item) === "audio");
          if (addedVideos.length) {
            addedVideosTotal.push(...addedVideos);
            setLocalUploadedVideos((previous) => prependUniqueAssets(previous, addedVideos));
          }
          if (addedAudios.length) {
            addedAudiosTotal.push(...addedAudios);
            setLocalUploadedAudios((previous) => prependUniqueAssets(previous, addedAudios));
          }
          props.onAssetLibraryResult?.(batchResult);
        },
      });
      const added = Array.isArray(result?.added) ? result.added : [];
      const addedVideos = added.filter((item) => assetKind(item) === "video");
      const addedAudios = added.filter((item) => assetKind(item) === "audio");
      const uploadedCount = kind === "videos" ? addedVideos.length : addedAudios.length;
      setUploadStatus(uploadedCount
        ? { kind, tone: "success", text: `已上传 ${uploadedCount} 个${kind === "videos" ? "视频" : "音频"}文件。` }
        : { kind, tone: "error", text: `No ${kind === "videos" ? "video" : "audio"} assets were added. Use ${MEDIA_UPLOAD_FORMATS[kind] || "a supported media format"}.` });
      return kind === "videos" ? addedVideos : addedAudios;
    } catch (err) {
      const uploadedBeforeError = kind === "videos" ? addedVideosTotal.length : addedAudiosTotal.length;
      const message = err instanceof Error ? err.message : String(err);
      setUploadStatus({
        kind,
        tone: "error",
        text: uploadedBeforeError ? `已上传 ${uploadedBeforeError} 个文件，后续上传失败：${message}` : message,
      });
      return kind === "videos" ? addedVideosTotal : addedAudiosTotal;
    } finally {
      setUploadBusy(false);
    }
  };
  const loadImageAPISettings = async () => {
    const taskId = props.task?.()?.id;
    if (!taskId) throw new Error("Task is not loaded");
    return await kbApi.assetLibraryImageAPISettings(taskId);
  };
  const saveImageAPISettings = async (settings) => {
    const taskId = props.task?.()?.id;
    if (!taskId) throw new Error("Task is not loaded");
    return await kbApi.saveAssetLibraryImageAPISettings(taskId, settings);
  };
  const loadImageAPIHistory = async () => {
    const taskId = props.task?.()?.id;
    if (!taskId) throw new Error("Task is not loaded");
    return await kbApi.assetLibraryImageAPIHistory(taskId);
  };
  const saveImageAPIHistory = async (payload) => {
    const taskId = props.task?.()?.id;
    if (!taskId) throw new Error("Task is not loaded");
    return await kbApi.saveAssetLibraryImageAPIHistory(taskId, payload);
  };
  const loadImagesAgentSettings = async () => {
    const taskId = props.task?.()?.id;
    if (!taskId) throw new Error("Task is not loaded");
    return await kbApi.assetLibraryImagesAgentSettings(taskId);
  };
  const saveImagesAgentSettings = async (settings) => {
    const taskId = props.task?.()?.id;
    if (!taskId) throw new Error("Task is not loaded");
    return await kbApi.saveAssetLibraryImagesAgentSettings(taskId, settings);
  };
  const loadVideoAPISettings = async () => {
    const taskId = props.task?.()?.id;
    if (!taskId) throw new Error("Task is not loaded");
    return await kbApi.assetLibraryVideoAPISettings(taskId);
  };
  const saveVideoAPISettings = async (settings) => {
    const taskId = props.task?.()?.id;
    if (!taskId) throw new Error("Task is not loaded");
    return await kbApi.saveAssetLibraryVideoAPISettings(taskId, settings);
  };
  const loadVideoAPIHistory = async () => {
    const taskId = props.task?.()?.id;
    if (!taskId) throw new Error("Task is not loaded");
    return await kbApi.assetLibraryVideoAPIHistory(taskId);
  };
  const saveVideoAPIHistory = async (payload) => {
    const taskId = props.task?.()?.id;
    if (!taskId) throw new Error("Task is not loaded");
    return await kbApi.saveAssetLibraryVideoAPIHistory(taskId, payload);
  };
  const loadVideosAgentSettings = async () => {
    const taskId = props.task?.()?.id;
    if (!taskId) throw new Error("Task is not loaded");
    return await kbApi.assetLibraryVideosAgentSettings(taskId);
  };
  const saveVideosAgentSettings = async (settings) => {
    const taskId = props.task?.()?.id;
    if (!taskId) throw new Error("Task is not loaded");
    return await kbApi.saveAssetLibraryVideosAgentSettings(taskId, settings);
  };
  const loadAssetLibraryImageModelConfig = async () => {
    const taskId = props.task?.()?.id;
    if (!taskId) throw new Error("Task is not loaded");
    return await kbApi.assetLibraryImageModelConfig(taskId);
  };
  const loadAssetLibraryVideoModelConfig = async () => {
    const taskId = props.task?.()?.id;
    if (!taskId) throw new Error("Task is not loaded");
    let primaryError = null;
    const primary = await kbApi.assetLibraryVideoModelConfig(taskId).catch((err) => {
      primaryError = err;
      return null;
    });
    if (hasAgentModelAliases(primary)) return primary;

    const shared = await kbApi.videoModelConfig().catch(() => null);
    if (hasAgentModelAliases(shared)) return shared;
    if (primary) return primary;
    if (shared) return shared;
    if (primaryError) throw primaryError;
    return { kind: "video", providers: [], agent_model_aliases: [] };
  };
  const ensureAgentChatSession = async () => {
    const taskId = props.task?.()?.id;
    if (!taskId) throw new Error("Task is not loaded");
    return await kbApi.assetLibraryAgentChatEnsureSession(taskId);
  };
  const loadAgentChatMessages = async () => {
    const taskId = props.task?.()?.id;
    if (!taskId) throw new Error("Task is not loaded");
    return await kbApi.assetLibraryAgentChatMessages(taskId);
  };
  const sendAgentChatMessage = async (payload) => {
    const taskId = props.task?.()?.id;
    if (!taskId) throw new Error("Task is not loaded");
    return await kbApi.assetLibraryAgentChatSendMessage(taskId, payload);
  };
  const abortAgentChat = async () => {
    const taskId = props.task?.()?.id;
    if (!taskId) throw new Error("Task is not loaded");
    return await kbApi.assetLibraryAgentChatAbort(taskId);
  };
  const agentChatEventsUrl = () => {
    const taskId = props.task?.()?.id;
    return taskId ? kbApi.assetLibraryAgentChatEventsUrl(taskId) : "";
  };
  const buildPromptBuilder = async (payload) => {
    const taskId = props.task?.()?.id;
    if (!taskId) throw new Error("Task is not loaded");
    return await kbApi.assetLibraryPromptBuilder(taskId, payload);
  };
  const savePromptBuilder = async (requestId, payload) => {
    const taskId = props.task?.()?.id;
    if (!taskId) throw new Error("Task is not loaded");
    return await kbApi.saveAssetLibraryPromptBuilder(taskId, requestId, payload);
  };
  const loadConsistencyReferences = async () => {
    const taskId = props.task?.()?.id;
    if (!taskId) throw new Error("Task is not loaded");
    return await kbApi.hostProductBuilder(taskId);
  };
  const moveToHistory = async (asset) => {
    const taskId = props.task?.()?.id;
    const assetId = asset?.id || asset?.path || asset?.audio_path || asset?.agent_session_path || asset?.json_path;
    if (!taskId || !assetId) return;
    setMovingIds((previous) => new Set(previous).add(assetId));
    try {
      const result = await kbApi.moveAssetToHistory(taskId, assetId);
      removeLocalAsset(assetId, asset?.path);
      setSelectedIds((previous) => {
        const next = new Set(previous);
        next.delete(assetId);
        if (asset?.path) next.delete(asset.path);
        return next;
      });
      props.onAssetLibraryResult?.(result);
    } finally {
      setMovingIds((previous) => {
        const next = new Set(previous);
        next.delete(assetId);
        return next;
      });
    }
  };
  const renameAsset = async (asset, filename) => {
    const taskId = props.task?.()?.id;
    const assetId = asset?.id || asset?.path;
    if (!taskId || !assetId || !String(filename || "").trim()) return;
    setMovingIds((previous) => new Set(previous).add(assetId));
    try {
      const result = await kbApi.renameAsset(taskId, assetId, { filename });
      const renamed = result?.asset;
      if (renamed?.path) {
        updateLocalAsset(assetId, asset?.path, renamed);
      }
      setSelectedIds((previous) => {
        const next = new Set(previous);
        next.delete(assetId);
        if (asset?.path) next.delete(asset.path);
        const renamedKey = result?.asset?.id || result?.asset?.path;
        if (renamedKey) next.add(renamedKey);
        return next;
      });
      props.onAssetLibraryResult?.(result);
      if (renamed?.path) {
        window.dispatchEvent(new CustomEvent("koubo-storyboard:asset-library-asset-renamed", {
          detail: {
            taskId,
            oldPath: result?.old_path || asset?.path || assetId,
            asset: renamed,
          },
        }));
      }
    } finally {
      setMovingIds((previous) => {
        const next = new Set(previous);
        next.delete(assetId);
        return next;
      });
    }
  };
  const deleteAsset = async (asset) => {
    const taskId = props.task?.()?.id;
    const assetId = asset?.id || asset?.path;
    if (!taskId || !assetId) return;
    setMovingIds((previous) => new Set(previous).add(assetId));
    try {
      const result = await kbApi.deleteAsset(taskId, assetId);
      removeLocalAsset(assetId, asset?.path);
      props.onAssetLibraryResult?.(result);
    } finally {
      setMovingIds((previous) => {
        const next = new Set(previous);
        next.delete(assetId);
        return next;
      });
    }
  };
  const deleteHistoryAssetById = async (assetId, options = {}) => {
    const taskId = props.task?.()?.id;
    if (!taskId || !assetId) return;
    setMovingIds((previous) => new Set(previous).add(assetId));
    try {
      const result = await kbApi.deleteHistoryAsset(taskId, assetId);
      if (options.applyResult !== false) props.onAssetLibraryResult?.(result);
      return result;
    } finally {
      setMovingIds((previous) => {
        const next = new Set(previous);
        next.delete(assetId);
        return next;
      });
    }
  };
  const deleteHistoryAsset = async (asset) => {
    const assetId = historyAssetKey(asset);
    setHistoryActionError("");
    const result = await deleteHistoryAssetById(assetId);
    if (result) {
      setSelectedHistoryIds((previous) => {
        const next = new Set(previous);
        next.delete(assetId);
        return next;
      });
    }
  };
  const restoreHistoryAsset = async (asset) => {
    const taskId = props.task?.()?.id;
    const assetId = historyAssetKey(asset);
    if (!taskId || !assetId) return;
    setHistoryActionError("");
    setMovingIds((previous) => new Set(previous).add(assetId));
    try {
      const result = await kbApi.restoreHistoryAsset(taskId, assetId);
      props.onAssetLibraryResult?.(result);
      const restoredAsset = result?.asset || asset;
      const restoredKind = assetKind(restoredAsset);
      if (restoredKind === "video") {
        setLocalUploadedVideos((previous) => uniqueAssets([restoredAsset, ...previous]));
      } else if (restoredKind === "audio") {
        setLocalUploadedAudios((previous) => uniqueAssets([restoredAsset, ...previous]));
      } else {
        setLocalUploadedAssets((previous) => uniqueAssets([restoredAsset, ...previous]));
      }
      if (restoredKind === "video") setView("videos");
      else if (restoredKind === "audio") setView("tts-agent");
      else setView("images");
      setSelectedHistoryIds((previous) => {
        const next = new Set(previous);
        next.delete(assetId);
        return next;
      });
    } catch (error) {
      setHistoryActionError(error instanceof Error ? error.message : String(error));
    } finally {
      setMovingIds((previous) => {
        const next = new Set(previous);
        next.delete(assetId);
        return next;
      });
    }
  };
  const deleteSelectedHistory = async () => {
    const selected = selectedHistoryItems();
    const taskId = props.task?.()?.id;
    if (!taskId || !selected.length || deletingHistoryBatch()) return;
    const message = `Delete ${selected.length} selected History image${selected.length > 1 ? "s" : ""}?`;
    if (!window.confirm(message)) return;
    const ids = selected.map(historyAssetKey).filter(Boolean);
    setDeletingHistoryBatch(true);
    setMovingIds((previous) => {
      const next = new Set(previous);
      ids.forEach((id) => next.add(id));
      return next;
    });
    let latestResult = null;
    try {
      for (const id of ids) {
        latestResult = await deleteHistoryAssetById(id, { applyResult: false });
      }
      if (latestResult) props.onAssetLibraryResult?.(latestResult);
      setSelectedHistoryIds(new Set());
      setHistorySelectionMode(false);
    } finally {
      setDeletingHistoryBatch(false);
      setMovingIds((previous) => {
        const next = new Set(previous);
        ids.forEach((id) => next.delete(id));
        return next;
      });
    }
  };
  const generateImage = async (prompt, references, options, onEvent) => {
    const taskId = props.task?.()?.id;
    if (!taskId) throw new Error("Task is not loaded");
    const generationOptions = options || {};
    const aspect = generationOptions.aspect || aspectFromPrompt(prompt);
    const pendingId = `pending_${Date.now()}`;
    const pendingAsset = {
      id: pendingId,
      label: aspect === "9:16" ? "Generating 9:16 image" : aspect === "16:9" ? "Generating 16:9 image" : "Generating image",
      filename: "Generating image",
      kind: "image",
      source: "generating",
      pending: true,
      aspect_ratio: aspect,
      progressLabel: "0%",
    };
    setPendingAssets((previous) => [pendingAsset, ...previous]);
    let completed = null;
    try {
      const generationReferences = resolveGenerationReferences(references);
      await kbApi.streamAssetLibraryAgentGenerate(taskId, {
        prompt,
        reference_images: generationReferences.map((item) => ({
          path: item.path,
          role: item.role || item.reference_role || "",
          label: item.label || item.filename || "",
        })).filter((item) => item.path),
        agentImageAlias: generationOptions.agentImageAlias || "",
        provider: generationOptions.provider || "",
        model: generationOptions.model || "",
        count: generationOptions.count || 1,
        aspect,
        size: imageSizeForAspect(aspect),
        prompt_builder_request_id: generationOptions.promptBuilderRequestId || "",
        prompt_builder_applied_path: generationOptions.promptBuilderAppliedPath || "",
        chat_opencode_session_id: generationOptions.chatOpenCodeSessionId || "",
        prompt_candidate_id: generationOptions.promptCandidateId || "",
        prompt_candidate_title: generationOptions.promptCandidateTitle || "",
      }, (event) => {
        if (event?.type === "heartbeat") {
          const elapsed = Number(event.elapsed_seconds || 0);
          const percent = Math.min(96, Math.max(8, Math.round(8 + elapsed * 4)));
          setPendingAssets((previous) => previous.map((item) => item.id === pendingId ? { ...item, progressLabel: `${percent}%` } : item));
        }
        if (event?.type === "failed") {
          setPendingAssets((previous) => previous.map((item) => item.id === pendingId ? { ...item, failed: true, pending: false, progressLabel: "Failed" } : item));
        }
        onEvent?.(event);
        if (event?.type === "completed") completed = event;
      });
      if (completed) {
        setPendingAssets((previous) => previous.filter((item) => item.id !== pendingId));
        applyAssetResult(completed);
      }
      return completed;
    } catch (error) {
      setPendingAssets((previous) => previous.map((item) => item.id === pendingId ? { ...item, failed: true, pending: false, progressLabel: "Failed" } : item));
      throw error;
    }
  };
  const generateVideo = async (prompt, references, options, onEvent) => {
    const taskId = props.task?.()?.id;
    if (!taskId) throw new Error("Task is not loaded");
    const generationOptions = options || {};
    const aspect = generationOptions.aspect || "9:16";
    const pendingId = `pending_video_${Date.now()}`;
    const pendingAsset = {
      id: pendingId,
      label: `Generating ${aspect} video`,
      filename: "正在生成视频",
      kind: "video",
      asset_type: "Video",
      source: "generating",
      pending: true,
      aspect_ratio: aspect,
      progressLabel: "0%",
    };
    setLocalUploadedVideos((previous) => [pendingAsset, ...previous]);
    let completed = null;
    try {
      const generationReferences = resolveGenerationReferences(references);
      const referencePayload = splitVideoReferencePayload(generationReferences);
      await kbApi.streamAssetLibraryVideoGenerate(taskId, {
        title: generationOptions.title || "Direct video generation",
        prompt,
        reference_assets: referencePayload.reference_assets,
        reference_images: referencePayload.reference_images,
        reference_audios: referencePayload.reference_audios,
        reference_videos: referencePayload.reference_videos,
        reference_mode: referenceModeForVideoGeneration(generationOptions, referencePayload),
        settingsScope: generationOptions.settingsScope || "",
        agentVideoAlias: generationOptions.agentVideoAlias || "",
        provider: generationOptions.provider || "",
        model: generationOptions.model || "",
        count: generationOptions.count || 1,
        duration: generationOptions.duration || 4,
        aspect,
        prompt_builder_request_id: generationOptions.promptBuilderRequestId || "",
        prompt_builder_applied_path: generationOptions.promptBuilderAppliedPath || "",
        client_action_id: generationOptions.clientActionId || "",
        operation: generationOptions.operation || "",
        stateful: generationOptions.stateful === true,
        video_thread_id: generationOptions.videoThreadId || "",
        parent_turn_id: generationOptions.parentTurnId || "",
        source_video_asset_id: generationOptions.sourceVideoAssetId || "",
      }, (event) => {
        if (event?.type === "heartbeat") {
          const elapsed = Number(event.elapsed_seconds || 0);
          const percent = Math.min(AGENT_VIDEO_PROGRESS_MAX, Math.max(8, Math.round(8 + elapsed * 2)));
          setLocalUploadedVideos((previous) => previous.map((item) => item.id === pendingId ? { ...item, progressLabel: `${percent}%` } : item));
        }
        if (event?.type === "failed") {
          setLocalUploadedVideos((previous) => previous.map((item) => item.id === pendingId ? { ...item, failed: true, pending: false, progressLabel: "Failed" } : item));
        }
        onEvent?.(event);
        if (event?.type === "completed") completed = event;
      }, { signal: generationOptions.signal });
      if (completed) {
        const completedAssets = Array.isArray(completed.assets) && completed.assets.length ? completed.assets : [completed.asset].filter(Boolean);
        setLocalUploadedVideos((previous) => {
          const withoutPending = previous.filter((item) => item.id !== pendingId);
          const seen = new Set(withoutPending.map((item) => item.path || item.id));
          return [...completedAssets.filter((item) => !seen.has(item.path || item.id)), ...withoutPending];
        });
        applyAssetResult(completed);
      }
      return completed;
    } catch (error) {
      const aborted = error?.name === "AbortError" || generationOptions.signal?.aborted;
      setLocalUploadedVideos((previous) => aborted
        ? previous.filter((item) => item.id !== pendingId)
        : previous.map((item) => item.id === pendingId ? { ...item, failed: true, pending: false, progressLabel: "失败" } : item));
      throw error;
    }
  };
  const mediaAgentWorkspaceTitle = () => mediaAgentKind() === "audio" ? "音频" : "视频";
  const mediaAgentKey = () => mediaAgentKind() === "audio" ? "asset_audio" : "asset_video";
  const mediaAgentChips = () => [
    { label: "工作区", value: mediaAgentWorkspaceTitle() },
    { label: "素材", value: String(mediaAgentItems().length) },
    { label: "可见", value: String(filteredItems().length) },
  ];
  const videoAgentChips = () => [
    { label: "工作区", value: "视频" },
    { label: "素材", value: String(videos().length) },
    { label: "图片", value: String(images().length) },
    { label: "参考", value: String(selectedVideoReferenceAssets().length) },
    { label: "可见", value: `${filteredVideoItems().length} / ${filteredImageItems().length}` },
  ];
  const buildMediaAgentContext = () => {
    const visible = filteredItems();
    const all = mediaAgentItems();
    const visibleLimit = 80;
    const allLimit = 120;
    return {
      asset_library_workspace: mediaAgentWorkspaceTitle(),
      media_kind: mediaAgentKind(),
      query: query(),
      asset_count: all.length,
      visible_asset_count: visible.length,
      visible_assets_truncated: visible.length > visibleLimit,
      all_assets_truncated: all.length > allLimit,
      visible_assets: visible.slice(0, visibleLimit).map(compactAgentAsset),
      all_assets: all.slice(0, allLimit).map(compactAgentAsset),
    };
  };
  const buildVideoAgentContext = () => {
    const visibleVideos = filteredVideoItems();
    const visibleImages = filteredImageItems();
    const allVideos = videos();
    const allImages = images();
    const allAudios = audios();
    const references = selectedVideoReferenceAssets();
    const visibleLimit = 80;
    const allLimit = 120;
    return {
      asset_library_workspace: "Videos",
      media_kind: "video",
      query: query(),
      asset_count: allVideos.length,
      image_asset_count: allImages.length,
      audio_asset_count: allAudios.length,
      selected_reference_count: references.length,
      visible_asset_count: visibleVideos.length,
      visible_image_count: visibleImages.length,
      visible_assets_truncated: visibleVideos.length > visibleLimit,
      visible_images_truncated: visibleImages.length > visibleLimit,
      all_assets_truncated: allVideos.length > allLimit,
      all_images_truncated: allImages.length > allLimit,
      all_audios_truncated: allAudios.length > allLimit,
      selected_reference_assets: references.map(compactAgentAsset),
      selected_reference_images: references.filter((item) => assetKind(item) === "image").map(compactAgentAsset),
      selected_reference_audios: references.filter((item) => assetKind(item) === "audio").map(compactAgentAsset),
      selected_reference_videos: references.filter((item) => assetKind(item) === "video").map(compactAgentAsset),
      visible_assets: visibleVideos.slice(0, visibleLimit).map(compactAgentAsset),
      visible_images: visibleImages.slice(0, visibleLimit).map(compactAgentAsset),
      all_assets: allVideos.slice(0, allLimit).map(compactAgentAsset),
      all_images: allImages.slice(0, allLimit).map(compactAgentAsset),
      all_audios: allAudios.slice(0, allLimit).map(compactAgentAsset),
    };
  };
  const renderMediaAgentCandidate = (candidate) => {
    const payload = candidate?.payload || {};
    const findings = Array.isArray(payload.findings) ? payload.findings : [];
    const nextActions = Array.isArray(payload.next_actions) ? payload.next_actions : [];
    return <article class="kbsp-agent-candidate ual-media-agent-candidate">
      <strong>{candidate.title || payload.title || "智能体建议"}</strong>
      <Show when={payload.summary}>
        <p>{String(payload.summary || "")}</p>
      </Show>
      <Show when={findings.length}>
        <ul>
          <For each={findings}>{(item) => <li>
            <b>{String(item?.severity || "info")}</b>
            <span>{String(item?.message || item || "")}</span>
          </li>}</For>
        </ul>
      </Show>
      <Show when={nextActions.length}>
        <div class="ual-media-agent-actions">
          <For each={nextActions}>{(item) => <span title={String(item?.action || "")}>{String(item?.label || item?.action || "下一步")}</span>}</For>
        </div>
      </Show>
    </article>;
  };
  const renderImageAgentPanel = (mode = "image") => <AgentPanel
    mode={mode}
    applyComposerPrompt={pendingComposerPrompt}
    applyComposerTarget={mode === "image" ? "images" : ""}
    onComposerPromptApplied={handleComposerPromptApplied}
    showOpenCodeEntry={false}
    task={props.task}
    sessionId={props.sessionId}
    selectedItems={selectedItems}
    availableAssets={images}
    imageCount={() => images().length}
    historyCount={() => historyAssets().length}
    imageUrl={imageUrl}
    thumbnailUrl={thumbnailUrl}
    onClose={props.onClose}
    onRemoveReferenceAsset={removeReferenceAsset}
    uploadBusy={uploadBusy}
    uploadImageFiles={uploadImageFiles}
    filesFromDataTransfer={filesFromDataTransfer}
    filesFromClipboard={filesFromClipboard}
    generateImage={generateImage}
    imageModelConfig={loadAssetLibraryImageModelConfig}
    loadImageAPISettings={loadImageAPISettings}
    saveImageAPISettings={saveImageAPISettings}
    loadImageAPIHistory={loadImageAPIHistory}
    saveImageAPIHistory={saveImageAPIHistory}
    loadImagesAgentSettings={loadImagesAgentSettings}
    saveImagesAgentSettings={saveImagesAgentSettings}
    ensureAgentChatSession={ensureAgentChatSession}
    loadAgentChatMessages={loadAgentChatMessages}
    sendAgentChatMessage={sendAgentChatMessage}
    abortAgentChat={abortAgentChat}
    agentChatEventsUrl={agentChatEventsUrl}
    onAgentImageGenerationEvent={handleAgentImageGenerationEvent}
    buildPromptBuilder={buildPromptBuilder}
    savePromptBuilder={savePromptBuilder}
    loadConsistencyReferences={loadConsistencyReferences}
    onPreview={props.onPreview}
  />;
  const renderVideoAgentPanel = (variant = "agent") => <VideoAgentPanel
    variant={variant}
    deferInitialLoad={props.mode === "page" && variant === "workspace"}
    deferInitialLoadTimeoutMs={3000}
    deferHistoryLoad={props.mode === "page" && variant === "workspace"}
    applyComposerPrompt={pendingComposerPrompt}
    applyComposerTarget={variant === "workspace" ? "videos" : ""}
    onComposerPromptApplied={handleComposerPromptApplied}
    setOpen={() => {}}
    task={props.task}
    api={kbApi}
    agentKey="asset_video"
    onClose={props.onClose}
    contextChips={videoAgentChips}
    buildClientContext={buildVideoAgentContext}
    videoModelConfig={loadAssetLibraryVideoModelConfig}
    loadVideoAPISettings={loadVideoAPISettings}
    saveVideoAPISettings={saveVideoAPISettings}
    loadVideoAPIHistory={loadVideoAPIHistory}
    saveVideoAPIHistory={saveVideoAPIHistory}
    loadVideosAgentSettings={loadVideosAgentSettings}
    saveVideosAgentSettings={saveVideosAgentSettings}
    renderCandidate={renderMediaAgentCandidate}
    generatedAssets={videos}
    mediaUrl={imageUrl}
    referenceAssets={selectedVideoReferenceAssets}
    referenceUrl={imageUrl}
    thumbnailUrl={thumbnailUrl}
    onRemoveReference={removeReferenceAsset}
    onClearReferences={clearReferenceAssets}
    uploadImageFiles={uploadImageFiles}
    uploadMediaFiles={uploadMediaFiles}
    onAgentVideoGenerationEvent={handleAgentVideoGenerationEvent}
    generateVideo={generateVideo}
    buildPromptBuilder={buildPromptBuilder}
    savePromptBuilder={savePromptBuilder}
    loadConsistencyReferences={loadConsistencyReferences}
  />;
  const isVideoAgentGenerationPayload = (payload = {}) => {
    const model = String(payload.generation_model || payload.engine_type || payload.model_name || "").toLowerCase();
    return model === "video_agent"
      || model.includes("video agent")
      || payload.agent_mode === "chat"
      || Boolean(payload.provider_session_id);
  };
  const removeDigitalHumanPendingVideo = (clientId) => {
    if (!clientId) return;
    const pendingId = `digital_human_pending_${clientId}`;
    setLocalUploadedVideos((previous) => previous.filter((item) => item.id !== pendingId));
  };
  const handleDigitalHumanGenerated = (payload = {}) => {
    const pendingId = payload.client_id ? `digital_human_pending_${payload.client_id}` : "";
    const completedAssets = Array.isArray(payload.assets) && payload.assets.length ? payload.assets : [payload.asset].filter(Boolean);
    if (isVideoAgentGenerationPayload(payload) && !completedAssets.length) {
      removeDigitalHumanPendingVideo(payload.client_id);
      applyAssetResult(payload);
      return;
    }
    if (completedAssets.length) {
      setLocalUploadedVideos((previous) => {
        const withoutPending = pendingId ? previous.filter((item) => item.id !== pendingId) : previous;
        const seen = new Set(withoutPending.map((item) => item.path || item.id));
        return [...completedAssets.filter((item) => !seen.has(item.path || item.id)), ...withoutPending];
      });
    }
    applyAssetResult(payload);
  };
  const handleDigitalHumanGenerationStart = (payload = {}) => {
    if (isVideoAgentGenerationPayload(payload)) {
      removeDigitalHumanPendingVideo(payload.client_id);
      return;
    }
    const clientId = payload.client_id || String(Date.now());
    const pendingId = `digital_human_pending_${clientId}`;
    const aspect = payload.aspect || "9:16";
    const count = Number(payload.count || 1);
    const pendingAsset = {
      id: pendingId,
      label: count > 1 ? `Generating ${count} digital human videos` : `Generating ${aspect} digital human video`,
      filename: "Generating digital human video",
      kind: "video",
      asset_type: "Video",
      source: "digital-human-generating",
      pending: true,
      aspect_ratio: aspect,
      progressLabel: "0%",
    };
    setLocalUploadedVideos((previous) => {
      const exists = previous.some((item) => item.id === pendingId);
      return exists
        ? previous.map((item) => item.id === pendingId ? { ...item, ...pendingAsset, progressLabel: item.progressLabel || pendingAsset.progressLabel } : item)
        : [pendingAsset, ...previous];
    });
  };
  const handleDigitalHumanGenerationProgress = (payload = {}) => {
    if (isVideoAgentGenerationPayload(payload)) return;
    const pendingId = payload.client_id ? `digital_human_pending_${payload.client_id}` : "";
    if (!pendingId) return;
    setLocalUploadedVideos((previous) => previous.map((item) => item.id === pendingId ? { ...item, progressLabel: payload.progressLabel || item.progressLabel } : item));
  };
  const handleDigitalHumanGenerationFailed = (payload = {}) => {
    if (isVideoAgentGenerationPayload(payload)) {
      removeDigitalHumanPendingVideo(payload.client_id);
      return;
    }
    const pendingId = payload.client_id ? `digital_human_pending_${payload.client_id}` : "";
    if (!pendingId) return;
    setLocalUploadedVideos((previous) => previous.map((item) => item.id === pendingId ? { ...item, failed: true, pending: false, progressLabel: "失败" } : item));
  };
  return <div class={`ual-overlay ${props.mode === "page" ? "is-page" : ""} ual-theme-${theme()}`} role={props.mode === "page" ? undefined : "dialog"} aria-modal={props.mode === "page" ? undefined : "true"} aria-label="Upload Asset Library">
    <div class={`ual-shell ${sidebarCollapsed() ? "is-sidebar-collapsed" : ""} ${showRightPanel() ? "" : "is-main-expanded"} ${view() === "tts-agent" ? "is-tts-agent-view" : ""}`}>
      <LibrarySidebar view={view} setView={setView} collapsed={sidebarCollapsed} onCollapse={() => setSidebarCollapsed(true)} onExpand={() => setSidebarCollapsed(false)} onClose={props.onClose} />
      <main class="ual-main">
        <Show when={historyActionError()}>
          <div class="ual-action-error">{historyActionError()}</div>
        </Show>
        <ImageToolbar
          view={view}
          query={query}
          setQuery={setQuery}
          theme={theme}
          setTheme={setTheme}
          imageColumns={imageColumns}
          setImageColumns={setImageColumns}
          imageCount={() => images().length}
          videoCount={() => videos().length}
          audioCount={() => audios().length}
          historyCount={() => historyAssets().length}
          historySelectionMode={historySelectionMode}
          setHistorySelectionMode={setHistorySelectionActive}
          selectedHistoryCount={() => selectedHistoryItems().length}
          visibleHistoryCount={() => view() === "history" ? filteredItems().length : 0}
          allVisibleHistorySelected={allVisibleHistorySelected}
          onSelectAllVisibleHistory={selectAllVisibleHistory}
          onClearHistorySelection={clearHistorySelection}
          onDeleteSelectedHistory={deleteSelectedHistory}
          deletingHistoryBatch={deletingHistoryBatch}
        />
        <Switch>
          <Match when={view() === "history"}>
            <HistoryGrid items={filteredItems} imageUrl={imageUrl} thumbnailUrl={thumbnailUrl} assetLabel={assetLabel} selectedIds={selectedHistoryIds} selectionMode={historySelectionMode} onToggleHistory={toggleHistorySelected} onPreview={props.onPreview} movingIds={movingIds} onDeleteHistory={deleteHistoryAsset} onRestoreHistory={restoreHistoryAsset} />
          </Match>
          <Match when={view() === "videos"}>
            <VideoWorkspaceLibrary
              videos={filteredVideoItems}
              images={filteredImageItems}
              assetUrl={imageUrl}
              thumbnailUrl={thumbnailUrl}
              assetLabel={assetLabel}
              selectedIds={selectedIds}
              selectedAudio={selectedDigitalHumanAudio}
              movingIds={movingIds}
              imageColumns={imageColumns}
              uploadBusy={uploadBusy}
              uploadStatus={uploadStatus}
              uploadMediaFiles={uploadMediaFiles}
              uploadImageFiles={uploadImageFiles}
              deferImages={props.mode === "page"}
              onToggleReference={toggleSelected}
              onPreview={props.onPreview}
              onRenameAsset={renameAsset}
              onMoveVideoToHistory={moveToHistory}
              onMoveImageToHistory={moveToHistory}
            />
          </Match>
          <Match when={view() === "videos-agent"}>
            <VideoWorkspaceLibrary
              videos={filteredVideoItems}
              images={filteredImageItems}
              assetUrl={imageUrl}
              thumbnailUrl={thumbnailUrl}
              assetLabel={assetLabel}
              selectedIds={selectedIds}
              selectedAudio={selectedDigitalHumanAudio}
              movingIds={movingIds}
              imageColumns={imageColumns}
              uploadBusy={uploadBusy}
              uploadStatus={uploadStatus}
              uploadMediaFiles={uploadMediaFiles}
              uploadImageFiles={uploadImageFiles}
              deferImages={props.mode === "page"}
              onToggleReference={toggleSelected}
              onPreview={props.onPreview}
              onRenameAsset={renameAsset}
              onMoveVideoToHistory={moveToHistory}
              onMoveImageToHistory={moveToHistory}
            />
          </Match>
          <Match when={view() === "tts-agent"}>
            <TtsAgentWorkspace controller={ttsAgentController} audios={audios} assetLabel={assetLabel} imageColumns={imageColumns} onMoveAudioToHistory={moveToHistory} />
          </Match>
          <Match when={view() === "digital-human-agent"}>
            <DigitalHumanWorkspace
              images={images}
              audios={audios}
              videos={videos}
              assetUrl={imageUrl}
              thumbnailUrl={thumbnailUrl}
              assetLabel={assetLabel}
              selectedIds={selectedIds}
              movingIds={movingIds}
              imageColumns={imageColumns}
              uploadBusy={uploadBusy}
              uploadMediaFiles={uploadMediaFiles}
              uploadImageFiles={uploadImageFiles}
              filesFromDataTransfer={filesFromDataTransfer}
              onToggleReference={selectDigitalHumanAvatarImage}
              onSelectAudioAsset={selectDigitalHumanAudio}
              onPreview={props.onPreview}
              onRenameAsset={renameAsset}
              onMoveVideoToHistory={moveToHistory}
              onMoveImageToHistory={moveToHistory}
              onMoveAudioToHistory={moveToHistory}
            />
          </Match>
          <Match when={view() === "prompt-agent"}>
            <PromptAgentWorkspace
              task={props.task}
              api={kbApi}
              query={query}
              refreshKey={promptAgentRefreshKey}
            />
          </Match>
          <Match when={view() === "search-agent"}>
            <SearchAgentWorkspace
              task={props.task}
              api={kbApi}
              controller={searchAgentController}
              imageColumns={imageColumns}
            />
          </Match>
          <Match when={isImageLibraryView()}>
            <ImageGrid
              items={filteredItems}
              selectedIds={selectedIds}
              toggleSelected={toggleSelected}
              imageUrl={imageUrl}
              thumbnailUrl={thumbnailUrl}
              assetLabel={assetLabel}
              onPreview={props.onPreview}
              movingIds={movingIds}
              onMoveToHistory={moveToHistory}
              onRenameAsset={renameAsset}
              onAddReferenceAsset={addReferenceAsset}
              imageColumns={imageColumns}
              uploadBusy={uploadBusy}
              uploadStatus={() => uploadStatusForKind("image")}
              uploadImageFiles={uploadImageFiles}
              filesFromDataTransfer={filesFromDataTransfer}
              filesFromClipboard={filesFromClipboard}
            />
          </Match>
        </Switch>
      </main>
      <Show when={showImageAgentPanel()}>
        {renderImageAgentPanel("image")}
      </Show>
      <Show when={showOpenCodeAgentPanel()}>
        {renderImageAgentPanel("opencode")}
      </Show>
      <Show when={showVideoWorkspacePanel()}>
        {renderVideoAgentPanel("workspace")}
      </Show>
      <Show when={showVideoAgentPanel()}>
        {renderVideoAgentPanel("agent")}
      </Show>
      <Show when={showTtsAgentPanel()}>
        <TtsAgentPanel controller={ttsAgentController} />
      </Show>
      <Show when={showDigitalHumanAgentPanel()}>
        <DigitalHumanAgentPanel
          task={props.task}
          api={kbApi}
          images={images}
          audios={audios}
          videos={videos}
          assetUrl={imageUrl}
          selectedAvatarImage={selectedDigitalHumanAvatarImage}
          selectedAudio={selectedDigitalHumanAudio}
          onClearSelectedAvatarImage={clearDigitalHumanAvatarImage}
          onClearSelectedAudio={clearDigitalHumanAudio}
          uploadImageFiles={uploadImageFiles}
          uploadMediaFiles={uploadMediaFiles}
          onGenerationStart={handleDigitalHumanGenerationStart}
          onGenerationProgress={handleDigitalHumanGenerationProgress}
          onGenerationFailed={handleDigitalHumanGenerationFailed}
          onGenerated={handleDigitalHumanGenerated}
          onClose={props.onClose}
        />
      </Show>
      <Show when={showPromptAgentPanel()}>
        <PromptAgentPanel
          task={props.task}
          api={kbApi}
          onVersionSaved={() => setPromptAgentRefreshKey((value) => value + 1)}
          onApplyToGeneration={applyPromptAgentToGeneration}
        />
      </Show>
      <Show when={showSearchAgentPanel()}>
        <SearchAgentPanel
          task={props.task}
          api={kbApi}
          controller={searchAgentController}
          onClose={props.onClose}
        />
      </Show>
      <Show when={mediaAgentKind()}>
        <KouboAgentDrawer
          open={mediaAgentOpen}
          setOpen={setMediaAgentOpen}
          task={props.task}
          api={kbApi}
          agentKey={mediaAgentKey()}
          title="Workspace"
          subtitle={`${mediaAgentWorkspaceTitle()} asset workspace`}
          greeting={`你好，我可以帮你分析当前 ${mediaAgentWorkspaceTitle()} 工作区的素材、命名、质量和 StoryBoard 使用建议。`}
          contextChips={mediaAgentChips}
          buildClientContext={buildMediaAgentContext}
          renderCandidate={renderMediaAgentCandidate}
          placeholder={`询问当前 ${mediaAgentWorkspaceTitle()} 素材如何使用、整理或绑定到 StoryBoard`}
        />
      </Show>
    </div>
  </div>;
}
