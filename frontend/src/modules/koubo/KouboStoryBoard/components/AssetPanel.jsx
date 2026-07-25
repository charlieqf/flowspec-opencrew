import { For, Show, createSignal } from "solid-js";
import AssetThumb from "./AssetThumb.jsx";
import MediaLibrarySearchDialog from "./MediaLibrarySearchDialog.jsx";
import { assetKind } from "../kouboStoryboardAssets.js";
import { fmtTime } from "../kouboStoryboardModel.js";
import { ImageIcon, XIcon } from "../kouboStoryboardIcons.jsx";

const ASSET_TABS = new Set(["source", "upload", "history"]);
const ASSET_CARD_COLUMNS = [2, 3, 4];
const ASSET_PANEL_TAB_KEY = "koubo-storyboard:asset-panel-tab";
const ASSET_PANEL_CARD_COLUMNS_KEY = "koubo-storyboard:asset-card-columns";

function readStoredAssetTab() {
  try {
    const value = new URLSearchParams(window.location.search || "").get("assetPanelTab");
    if (ASSET_TABS.has(value)) return value;
  } catch {
    // Fall back to localStorage below.
  }
  try {
    const value = window.localStorage?.getItem(ASSET_PANEL_TAB_KEY);
    return ASSET_TABS.has(value) ? value : "source";
  } catch {
    return "source";
  }
}

function rememberAssetTab(tab) {
  try {
    const url = new URL(window.location.href);
    url.searchParams.set("assetPanelTab", tab);
    window.history.replaceState(window.history.state, "", `${url.pathname}${url.search}${url.hash}`);
  } catch {
    // URL state is a refresh convenience; localStorage below is the fallback.
  }
  try {
    window.localStorage?.setItem(ASSET_PANEL_TAB_KEY, tab);
  } catch {
    // localStorage can be unavailable in embedded or restricted contexts.
  }
}

function readStoredAssetCardColumns() {
  try {
    const value = Number(window.localStorage?.getItem(ASSET_PANEL_CARD_COLUMNS_KEY));
    return ASSET_CARD_COLUMNS.includes(value) ? value : 3;
  } catch {
    return 3;
  }
}

function rememberAssetCardColumns(columns) {
  try {
    window.localStorage?.setItem(ASSET_PANEL_CARD_COLUMNS_KEY, String(columns));
  } catch {
    // The selector still works for the current session without storage.
  }
}

export default function AssetPanel(props) {
  let fileInput;
  let folderInput;
  const [draggingUpload, setDraggingUpload] = createSignal(false);
  const [fallbackTab, setFallbackTab] = createSignal(readStoredAssetTab());
  const [cardColumns, setCardColumns] = createSignal(readStoredAssetCardColumns());
  const activeTab = () => ASSET_TABS.has(props.activeAssetTab?.()) ? props.activeAssetTab() : fallbackTab();
  const chooseTab = (tab) => {
    if (!ASSET_TABS.has(tab)) return;
    setFallbackTab(tab);
    rememberAssetTab(tab);
    props.setActiveAssetTab?.(tab);
  };
  const chooseCardColumns = (columns) => {
    if (!ASSET_CARD_COLUMNS.includes(columns)) return;
    setCardColumns(columns);
    rememberAssetCardColumns(columns);
  };
  const chooseFiles = (files) => {
    const picked = Array.from(files || []);
    if (picked.length) void props.uploadManualAssets(picked);
  };
  const openAssetLibraryAgentPage = () => {
    const taskId = props.task?.()?.id || String(window.location.hash || "").match(/#\/koubo-storyboard\/tasks\/(\d+)/)?.[1] || "";
    if (!taskId) return;
    window.location.hash = `#/koubo-asset-library/tasks/${taskId}`;
  };
  const isUsed = (asset) => props.usedPaths().has(asset?.path);
  const isMediaAsset = (asset) => /\.(png|jpe?g|webp|gif|mp4|mov|webm|m4v|wav|m4a|mp3|aac|ogg|oga|flac|opus|aiff|aif|caf|weba|wma)$/i.test(String(asset?.path || ""));
  const isCleanGeneratedAsset = (asset) => asset?.source === "clean_generated" || asset?.origin?.tool === "clean_single_image_generation" || /(^|_)clean_generated(_|\.|$)/.test(String(asset?.path || asset?.filename || ""));
  const assetText = (asset) => String(asset?.text || asset?.srt_text || asset?.caption || props.assetTextByPath?.().get(asset?.path) || "").trim();
  const assetLabel = (asset) => String(asset?.label || asset?.filename || asset?.path || "素材").trim();
  const uploadedImagesAll = () => props.uploadedImages?.() || [];
  const uploadedAudiosAll = () => props.uploadedAudios?.() || [];
  const uploadedVideosAll = () => props.uploadedVideos?.() || [];
  const uploadedAssets = () => [...uploadedImagesAll(), ...uploadedAudiosAll(), ...uploadedVideosAll()];
  const historyItems = () => (props.historyVersions?.() || []).flatMap((version) => (version.items || []).map((item) => ({ ...item, version: version.version, reason: version.reason, created_at: version.created_at }))).filter(isMediaAsset);
  const historyVersionsAll = () => (props.historyVersions?.() || []).map((version) => ({
    ...version,
    items: (version.items || []).filter(isMediaAsset),
  })).filter((version) => version.items.length);
  const rememberCurrentTab = () => chooseTab(activeTab());
  const renderAssetCard = (asset, options = {}) => <div class={`kbsp-asset-scene-card ${props.selectedAsset()?.path === asset.path ? "is-selected" : ""} ${isUsed(asset) ? "is-placed" : ""}`} role="button" tabIndex="0" draggable="false" title={assetText(asset) || assetLabel(asset)} aria-label={assetLabel(asset)} onPointerDown={(event) => {
    rememberCurrentTab();
    props.beginPointerAssetDrag(event, asset, activeTab());
  }} onMouseDown={(event) => {
    rememberCurrentTab();
    props.beginPointerAssetDrag(event, asset, activeTab());
  }} onClick={() => props.clickAsset(asset)} onKeyDown={(event) => {
    if (event.key === "Enter" || event.key === " ") props.clickAsset(asset);
  }}>
    <AssetThumb asset={asset} sessionId={props.sessionId} />
    <Show when={assetKind(asset) !== "video" && assetKind(asset) !== "audio"}><span class="kbsp-asset-preview" role="button" tabIndex="0" title="Preview Image" onPointerDown={(event) => event.stopPropagation()} onMouseDown={(event) => event.stopPropagation()} onClick={(event) => { event.stopPropagation(); props.openImage(asset); }}>⌕</span></Show>
    <Show when={options.deletable}><button class="kbsp-asset-delete" type="button" title={options.deleteTitle || "Delete Uploaded Asset"} disabled={props.deletingAssetId() === asset.id} onClick={(event) => {
      event.stopPropagation();
      void (options.onDelete || props.deleteManualAsset)?.(asset.id || asset.path);
    }}><XIcon /></button></Show>
    <Show when={isCleanGeneratedAsset(asset)}><b class="kbsp-asset-clean-badge">干净生图</b></Show>
    <Show when={isUsed(asset)}><b class="kbsp-asset-used-badge">已用</b></Show>
    <Show when={assetText(asset)}><p class="kbsp-asset-srt-text">{assetText(asset)}</p></Show>
    <Show when={options.showLabel && !assetText(asset)}><span class="kbsp-asset-label">{assetLabel(asset)}</span></Show>
  </div>;
  return <aside class="kbsp-right" style={{ "--kbsp-asset-card-columns": String(cardColumns()) }}>
    <header>
      <h3><ImageIcon />Asset Pool</h3>
      <div class="kbsp-card-density" aria-label="Asset cards per row">
        <span>Card</span>
        <For each={ASSET_CARD_COLUMNS}>{(columns) => <button class={cardColumns() === columns ? "is-active" : ""} type="button" onClick={() => chooseCardColumns(columns)}>{columns}</button>}</For>
      </div>
    </header>
    <div class="kbsp-asset-tabs">
      <button class={activeTab() === "source" ? "is-active" : ""} type="button" onClick={() => chooseTab("source")}>原始素材</button>
      <button class={activeTab() === "upload" ? "is-active" : ""} type="button" onClick={() => chooseTab("upload")}>上传素材</button>
      <button class={activeTab() === "history" ? "is-active" : ""} type="button" onClick={() => chooseTab("history")}>历史素材</button>
    </div>
    <Show when={activeTab() === "source"}>
      <section class="kbsp-asset-section">
        <Show when={props.assetGroups().length} fallback={<div class="kbsp-empty">暂无原始素材</div>}>
          <div class="kbsp-asset-shot-list">
            <For each={props.assetGroups()}>{(group) => <section class="kbsp-asset-shot">
              <div class="kbsp-asset-shot-head"><strong>{group.shot_id}</strong><span>{group.scenes.length} Scenes · {fmtTime(group.duration)}</span></div>
              <div class="kbsp-asset-scene-grid">
                <For each={group.scenes}>{(scene) => renderAssetCard(scene, { badge: "SRT Frame" })}</For>
              </div>
            </section>}</For>
          </div>
        </Show>
      </section>
    </Show>
    <Show when={activeTab() === "upload"}>
      <section class={`kbsp-asset-section kbsp-asset-upload-section ${draggingUpload() ? "is-dragging" : ""}`} onDragEnter={(event) => {
      event.preventDefault();
      chooseTab("upload");
      setDraggingUpload(true);
      }} onDragOver={(event) => {
        event.preventDefault();
        chooseTab("upload");
        setDraggingUpload(true);
      }} onDragLeave={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setDraggingUpload(false);
      }} onDrop={(event) => {
        event.preventDefault();
        setDraggingUpload(false);
        chooseFiles(event.dataTransfer?.files);
      }}>
        <div class="kbsp-asset-upload-actions">
          <button type="button" disabled={props.uploadBusy()} onClick={() => fileInput?.click()}>{props.uploadBusy() ? "Uploading" : "Upload"}</button>
          <button type="button" disabled={props.uploadBusy()} onClick={() => folderInput?.click()}>Folder</button>
          <button class="kbsp-agent-entry" type="button" onClick={openAssetLibraryAgentPage}>Agent</button>
          <MediaLibrarySearchDialog
            task={props.task}
            dialogue={props.selectedDialogue}
            onImported={async (result) => {
              chooseTab("upload");
              await props.onMediaLibraryImported?.(result);
            }}
          />
          <input ref={fileInput} type="file" accept="image/*,audio/*,video/*" multiple hidden onChange={(event) => chooseFiles(event.currentTarget.files)} />
          <input ref={folderInput} type="file" accept="image/*,audio/*,video/*" multiple webkitdirectory="" hidden onChange={(event) => chooseFiles(event.currentTarget.files)} />
        </div>
        <Show when={props.uploadStatus?.()?.text}>
          <div class={`kbsp-asset-upload-status is-${props.uploadStatus?.()?.tone || "info"}`}>
            {props.uploadStatus?.()?.text}
          </div>
        </Show>
        <Show when={uploadedAssets().length} fallback={<div class="kbsp-empty kbsp-upload-dropzone">{draggingUpload() ? "松开以上传" : "暂无上传素材"}</div>}>
          <Show when={uploadedImagesAll().length}><div class="kbsp-asset-subhead">Images · {uploadedImagesAll().length}</div>
          <div class="kbsp-asset-scene-grid"><For each={uploadedImagesAll()}>{(asset) => renderAssetCard(asset, { badge: "Image", deletable: true, showLabel: true })}</For></div></Show>
          <Show when={uploadedAudiosAll().length}><div class="kbsp-asset-subhead">Audios · {uploadedAudiosAll().length}</div>
          <div class="kbsp-asset-scene-grid"><For each={uploadedAudiosAll()}>{(asset) => renderAssetCard(asset, { badge: "Audio", deletable: true, showLabel: true })}</For></div></Show>
          <Show when={uploadedVideosAll().length}><div class="kbsp-asset-subhead">Videos · {uploadedVideosAll().length}</div>
          <div class="kbsp-asset-scene-grid"><For each={uploadedVideosAll()}>{(asset) => renderAssetCard(asset, { badge: "Video", deletable: true, showLabel: true })}</For></div></Show>
        </Show>
      </section>
    </Show>
    <Show when={activeTab() === "history"}>
      <section class="kbsp-asset-section">
        <Show when={historyItems().length} fallback={<div class="kbsp-empty">暂无历史素材</div>}>
          <div class="kbsp-asset-shot-list">
            <For each={historyVersionsAll()}>{(version) => <section class="kbsp-asset-shot">
              <div class="kbsp-asset-shot-head"><strong>{version.version}</strong><span>{(version.items || []).length} Assets</span></div>
              <div class="kbsp-asset-scene-grid"><For each={version.items || []}>{(asset) => renderAssetCard({ ...asset, version: version.version }, { badge: asset.asset_type || assetKind(asset), deletable: true, deleteTitle: "Delete History Asset", onDelete: props.deleteHistoryAsset })}</For></div>
            </section>}</For>
          </div>
        </Show>
      </section>
    </Show>
  </aside>;
}
