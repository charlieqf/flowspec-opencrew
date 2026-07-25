import { For, Show, createEffect, createSignal } from "solid-js";
import { shapeFromAspect, shapeFromDimensions } from "../uploadAssetLibraryModel.js";
import FloatingAssetMenu from "./FloatingAssetMenu.jsx";
import FlowIcon from "./FlowIcon.jsx";
import ImageCard from "./ImageCard.jsx";
import { browserSupportsVideoPictureInPicture, toggleVideoPictureInPicture } from "../videoPictureInPicture.js";

const VIDEO_PENDING_PROGRESS_MAX = 98;

function assetTitle(asset, assetLabel) {
  return String(assetLabel?.(asset) || asset?.label || asset?.filename || asset?.path || "Asset");
}

function assetDownloadName(asset) {
  const source = String(asset?.filename || asset?.label || asset?.path || "asset-video.mp4").trim();
  return source.split(/[\\/]/).filter(Boolean).pop() || "asset-video.mp4";
}

function downloadUrl(href) {
  const value = String(href || "").trim();
  if (!value) return "";
  try {
    const url = new URL(value, window.location.href);
    url.searchParams.set("download", "1");
    return url.toString();
  } catch {
    const separator = value.includes("?") ? "&" : "?";
    return `${value}${separator}download=1`;
  }
}

function displayProgressLabel(value) {
  const label = String(value || "").trim();
  const match = label.match(/^(\d+(?:\.\d+)?)%$/);
  if (match) return `${Math.min(VIDEO_PENDING_PROGRESS_MAX, Math.max(0, Math.round(Number(match[1]) || 0)))}%`;
  return label;
}

function uploadDropText(kind, busy) {
  if (busy) return "上传中...";
  return kind === "image" ? "拖拽上传图片" : "拖拽上传视频";
}

function VideoCard(props) {
  let videoEl;
  let menuButtonEl;
  const asset = () => props.asset;
  const [shape, setShape] = createSignal(shapeFromAspect(asset()?.aspect_ratio || asset()?.aspectRatio) || "landscape");
  const [menuOpen, setMenuOpen] = createSignal(false);
  const [previewActive, setPreviewActive] = createSignal(false);
  const [thumbnailFailed, setThumbnailFailed] = createSignal(false);
  const title = () => assetTitle(asset(), props.assetLabel);
  const src = () => props.assetUrl?.(asset()) || "";
  const thumbnailSrc = () => props.thumbnailUrl?.(asset()) || "";
  const stop = (event) => event.stopPropagation();
  createEffect(() => {
    thumbnailSrc();
    setThumbnailFailed(false);
  });
  const handleDragStart = (event) => {
    if (asset()?.pending) {
      event.preventDefault();
      return;
    }
    event.dataTransfer.effectAllowed = "copy";
    event.dataTransfer.setData("application/x-koubo-storyboard-asset", JSON.stringify(asset()));
    event.dataTransfer.setData("text/plain", asset()?.path || asset()?.filename || "");
  };
  const activatePreview = (afterReady) => {
    if (!previewActive()) {
      setPreviewActive(true);
      Promise.resolve().then(() => {
        if (!videoEl) return;
        videoEl.controls = true;
        afterReady?.();
      });
      return;
    }
    afterReady?.();
  };
  const togglePreview = (event) => {
    stop(event);
    activatePreview(() => {
      if (!videoEl) return;
      if (videoEl.paused) void videoEl.play();
      else videoEl.pause();
    });
  };
  const addToReference = (event) => {
    stop(event);
    setMenuOpen(false);
    props.onAddReferenceAsset?.(asset());
  };
  const requestFullscreen = (event) => {
    stop(event);
    setMenuOpen(false);
    activatePreview(() => {
      if (!videoEl) return;
      videoEl.controls = true;
      const request = videoEl.requestFullscreen || videoEl.webkitRequestFullscreen || videoEl.webkitEnterFullscreen;
      if (request) void request.call(videoEl);
    });
  };
  const canPictureInPicture = () => Boolean(src() && browserSupportsVideoPictureInPicture());
  const notifyPictureInPictureFailure = () => {
    if (typeof window !== "undefined" && typeof window.alert === "function") {
      window.alert("Picture in Picture could not be opened for this video.");
    }
  };
  const togglePictureInPicture = (event) => {
    stop(event);
    setMenuOpen(false);
    if (!canPictureInPicture()) {
      notifyPictureInPictureFailure();
      return;
    }
    activatePreview(() => {
      void toggleVideoPictureInPicture(videoEl).catch((error) => {
        console.warn("Unable to open picture-in-picture video preview", error);
        notifyPictureInPictureFailure();
      });
    });
  };
  const handleDownloadClick = (event) => {
    stop(event);
    window.setTimeout(() => setMenuOpen(false), 0);
  };
  const renameAsset = (event) => {
    stop(event);
    setMenuOpen(false);
    const current = title();
    const nextName = window.prompt("Rename asset", current);
    if (!nextName || nextName.trim() === current) return;
    void props.onRenameAsset?.(asset(), nextName.trim());
  };
  const moveToHistory = (event) => {
    stop(event);
    setMenuOpen(false);
    void props.onMoveToHistory?.(asset());
  };
  const continueStatefulVersion = (event) => {
    stop(event);
    setMenuOpen(false);
    window.dispatchEvent(new CustomEvent("koubo-storyboard:continue-video-version", {
      detail: { asset: asset() },
    }));
  };
  return <article
    class={`ual-card ual-video-card is-${shape()} ${asset()?.pending ? "is-pending" : ""} ${asset()?.failed ? "is-failed" : ""} ${props.selected ? "is-selected" : ""} ${props.moving ? "is-moving" : ""} ${menuOpen() ? "is-menu-open" : ""}`}
    draggable={!asset()?.pending}
    onDragStart={handleDragStart}
  >
    <button class="ual-card-image" type="button" title={title()} disabled={asset()?.pending} draggable={!asset()?.pending} onDragStart={handleDragStart} onClick={togglePreview}>
      <Show when={!asset()?.pending} fallback={<span class="ual-card-placeholder" aria-hidden="true" />}>
        <Show when={previewActive()} fallback={<Show when={thumbnailSrc() && !thumbnailFailed()} fallback={<span class="ual-video-thumb-placeholder" aria-hidden="true"><FlowIcon name="video" /></span>}>
          <img src={thumbnailSrc()} loading="lazy" draggable="false" onError={() => setThumbnailFailed(true)} onLoad={(event) => {
            setShape(shapeFromDimensions(event.currentTarget.naturalWidth, event.currentTarget.naturalHeight, shape()));
          }} />
        </Show>}>
          <video
            ref={(el) => { videoEl = el; }}
            src={src()}
            preload="metadata"
            muted
            playsInline
            onLoadedMetadata={(event) => {
              setShape(shapeFromDimensions(event.currentTarget.videoWidth, event.currentTarget.videoHeight, shape()));
            }}
          />
        </Show>
        <span class="ual-video-card-badge" aria-hidden="true"><FlowIcon name="video" /></span>
        <Show when={asset()?.stateful}><span class="ual-card-progress">有状态版本</span></Show>
      </Show>
      <Show when={asset()?.progressLabel}><span class="ual-card-progress">{displayProgressLabel(asset().progressLabel)}</span></Show>
    </button>
    <div class="ual-card-actions">
      <button type="button" title="Preview" onClick={togglePreview}>
        <FlowIcon name="video" />
      </button>
      <button type="button" title={props.selected ? "Remove reference" : "Add reference"} onClick={addToReference}>
        <FlowIcon name={props.selected ? "close" : "add"} />
      </button>
      <button ref={(el) => { menuButtonEl = el; }} type="button" title="More" aria-expanded={menuOpen()} onClick={(event) => {
        stop(event);
        if (!menuOpen()) activatePreview();
        setMenuOpen((value) => !value);
      }}>
        <FlowIcon name="moreVert" />
      </button>
    </div>
    {menuOpen() ? <FloatingAssetMenu anchor={() => menuButtonEl} onClose={() => setMenuOpen(false)} onClick={stop}>
      <button type="button" role="menuitem" onClick={togglePreview}><FlowIcon name="video" />Preview</button>
      <button type="button" role="menuitem" onClick={addToReference}><FlowIcon name={props.selected ? "close" : "add"} />{props.selected ? "Remove reference" : "Add reference"}</button>
      <button type="button" role="menuitem" onClick={requestFullscreen}><FlowIcon name="fullscreen" />Fullscreen</button>
      <button type="button" role="menuitem" disabled={!canPictureInPicture()} onClick={(event) => void togglePictureInPicture(event)}><FlowIcon name="pictureInPicture" />Picture in Picture</button>
      <button type="button" role="menuitem" disabled={props.moving} onClick={renameAsset}><FlowIcon name="editSquare" />Rename</button>
      <Show when={asset()?.stateful && asset()?.video_thread_id && asset()?.video_turn_id}>
        <button type="button" role="menuitem" onClick={continueStatefulVersion}><FlowIcon name="add" />从此版本继续编辑</button>
      </Show>
      {src()
        ? <a role="menuitem" href={downloadUrl(src())} download={assetDownloadName(asset())} onClick={handleDownloadClick}><FlowIcon name="download" />Download</a>
        : <button type="button" role="menuitem" disabled><FlowIcon name="download" />Download</button>}
      <hr />
      <button type="button" role="menuitem" class="is-danger" disabled={props.moving} onClick={moveToHistory}><FlowIcon name="delete" />Move to Trash</button>
    </FloatingAssetMenu> : null}
  </article>;
}

function WorkspaceSection(props) {
  const [draggingUpload, setDraggingUpload] = createSignal(false);
  const visibleUploadStatus = () => {
    const status = props.uploadStatus?.();
    return status?.kind === props.uploadKind ? status : { kind: props.uploadKind, tone: "", text: "" };
  };
  const dragHasFiles = (event) => Array.from(event.dataTransfer?.types || []).includes("Files");
  const handleDrag = (event) => {
    if (!dragHasFiles(event)) return;
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = "copy";
    setDraggingUpload(true);
  };
  const handleDrop = async (event) => {
    if (!dragHasFiles(event)) return;
    event.preventDefault();
    event.stopPropagation();
    setDraggingUpload(false);
    await props.onUploadFiles?.(event.dataTransfer?.files, `${props.kind}_drop`);
  };
  return <section
    class={`ual-grid-wrap ual-video-workspace-section is-${props.kind} ${draggingUpload() ? "is-dragging-upload" : ""}`}
    aria-label={props.title}
    tabIndex="0"
    onDragEnter={handleDrag}
    onDragOver={handleDrag}
    onDragLeave={(event) => {
      if (!event.currentTarget.contains(event.relatedTarget)) setDraggingUpload(false);
    }}
    onDrop={handleDrop}
  >
    <Show when={draggingUpload()}>
      <div class="ual-grid-drop-overlay">{uploadDropText(props.kind, props.uploadBusy?.())}</div>
    </Show>
    <header class="ual-video-workspace-head">
      <div>
        <strong>{props.title}</strong>
      </div>
      <Show when={props.actions}>
        <div class="ual-video-workspace-actions">{props.actions}</div>
      </Show>
    </header>
    <Show when={visibleUploadStatus().text}>
      <div class={`ual-media-upload-status is-${visibleUploadStatus().tone || "info"}`}>
        {visibleUploadStatus().text}
      </div>
    </Show>
    <Show when={props.items().length} fallback={<div class="ual-empty">{props.emptyText}</div>}>
      <div class="ual-grid" style={{ "--ual-image-columns": String(props.imageColumns?.() || 6) }}>
        <For each={props.items()}>{(asset) => props.kind === "image"
          ? <ImageCard
            asset={asset}
            selected={props.selectedIds?.().has(asset.id || asset.path)}
            imageUrl={props.assetUrl}
            thumbnailUrl={props.thumbnailUrl}
            assetLabel={props.assetLabel}
            moving={props.movingIds?.().has(asset.id || asset.path)}
            onPreview={props.onPreview}
            onMoveToHistory={props.onMoveImageToHistory}
            onRenameAsset={props.onRenameAsset}
            onAddReferenceAsset={props.onToggleReference}
          />
          : <VideoCard
            asset={asset}
            selected={props.selectedIds?.().has(asset.id || asset.path)}
            assetUrl={props.assetUrl}
            thumbnailUrl={props.thumbnailUrl}
            assetLabel={props.assetLabel}
            moving={props.movingIds?.().has(asset.id || asset.path)}
            onRenameAsset={props.onRenameAsset}
            onMoveToHistory={props.onMoveVideoToHistory}
            onAddReferenceAsset={props.onToggleReference}
          />
        }</For>
      </div>
    </Show>
  </section>;
}

export default function VideoWorkspaceLibrary(props) {
  const [imagesReady, setImagesReady] = createSignal(false);
  const imageCount = () => props.images?.()?.length || 0;
  const showImages = () => !props.deferImages || imagesReady();
  const loadImageReferences = () => setImagesReady(true);
  return <div class={`ual-video-workspace-library ${showImages() ? "" : "is-video-only"}`} aria-label="视频和图片">
    <WorkspaceSection
      kind="video"
      title="视频"
      items={props.videos}
      emptyText="SessionOutput/storyboard/assets/videos/ 中暂无视频"
      assetUrl={props.assetUrl}
      thumbnailUrl={props.thumbnailUrl}
      assetLabel={props.assetLabel}
      selectedIds={props.selectedIds}
      movingIds={props.movingIds}
      uploadBusy={props.uploadBusy}
      uploadStatus={props.uploadStatus}
      uploadKind="videos"
      onUploadFiles={(files, source) => props.uploadMediaFiles?.(files, "videos", { source })}
      onRenameAsset={props.onRenameAsset}
      onMoveVideoToHistory={props.onMoveVideoToHistory}
      onToggleReference={props.onToggleReference}
      imageColumns={props.imageColumns}
      actions={<Show when={!showImages()}>
        <button type="button" title="加载图片参考" onClick={loadImageReferences}>
          <FlowIcon name="image" /> 图片参考 {imageCount()}
        </button>
      </Show>}
    />
    <Show when={showImages()}>
      <WorkspaceSection
        kind="image"
        title="图片"
        items={props.images}
        emptyText="SessionOutput/storyboard/assets/images/ 中暂无图片"
        assetUrl={props.assetUrl}
        thumbnailUrl={props.thumbnailUrl}
        assetLabel={props.assetLabel}
        selectedIds={props.selectedIds}
        movingIds={props.movingIds}
        uploadBusy={props.uploadBusy}
        uploadStatus={props.uploadStatus}
        uploadKind="image"
        onUploadFiles={(files, source) => props.uploadImageFiles?.(files, { source })}
        onToggleReference={props.onToggleReference}
        onPreview={props.onPreview}
        onRenameAsset={props.onRenameAsset}
        onMoveImageToHistory={props.onMoveImageToHistory}
        imageColumns={props.imageColumns}
      />
    </Show>
  </div>;
}
