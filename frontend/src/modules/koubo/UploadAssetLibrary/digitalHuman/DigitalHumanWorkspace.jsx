import { For, Show, createEffect, createSignal } from "solid-js";
import { shapeFromAspect, shapeFromDimensions } from "../uploadAssetLibraryModel.js";
import FloatingAssetMenu from "../components/FloatingAssetMenu.jsx";
import FlowIcon from "../components/FlowIcon.jsx";
import ImageCard from "../components/ImageCard.jsx";
import { browserSupportsVideoPictureInPicture, toggleVideoPictureInPicture } from "../videoPictureInPicture.js";

const VIDEO_PENDING_PROGRESS_MAX = 98;

function assetTitle(asset, assetLabel) {
  return String(assetLabel?.(asset) || asset?.label || asset?.filename || asset?.path || "Asset");
}

function assetDownloadName(asset, fallback) {
  const source = String(asset?.filename || asset?.label || asset?.path || fallback).trim();
  return source.split(/[\\/]/).filter(Boolean).pop() || fallback;
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
  if (busy) return "Uploading...";
  if (kind === "image") return "Drop to upload Images";
  if (kind === "audio") return "Drop to upload Audios";
  return "Drop to upload Videos";
}

function compactColumns(columns) {
  const value = Number(columns?.() || columns || 6);
  if (value >= 8) return 4;
  if (value >= 6) return 3;
  return 2;
}

function audioColumns(columns) {
  const value = Number(columns?.() || columns || 6);
  if (value >= 8) return 2;
  return 1;
}

function audioCardMaxWidth(columns) {
  const value = Number(columns?.() || columns || 6);
  if (value >= 8) return "180px";
  if (value >= 6) return "260px";
  return "360px";
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
  const handleDownloadClick = (event) => {
    stop(event);
    window.setTimeout(() => setMenuOpen(false), 0);
  };
  return <article
    class={`ual-card ual-video-card is-${shape()} ${asset()?.pending ? "is-pending" : ""} ${asset()?.failed ? "is-failed" : ""} ${props.moving ? "is-moving" : ""} ${menuOpen() ? "is-menu-open" : ""}`}
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
      </Show>
      <Show when={asset()?.progressLabel}><span class="ual-card-progress">{displayProgressLabel(asset().progressLabel)}</span></Show>
    </button>
    <div class="ual-card-actions">
      <button type="button" title="Preview" onClick={togglePreview}>
        <FlowIcon name="video" />
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
      <button type="button" role="menuitem" onClick={requestFullscreen}><FlowIcon name="fullscreen" />Fullscreen</button>
      <button type="button" role="menuitem" disabled={!canPictureInPicture()} onClick={(event) => void togglePictureInPicture(event)}><FlowIcon name="pictureInPicture" />Picture in Picture</button>
      <button type="button" role="menuitem" disabled={props.moving} onClick={renameAsset}><FlowIcon name="editSquare" />Rename</button>
      {src()
        ? <a role="menuitem" href={downloadUrl(src())} download={assetDownloadName(asset(), "asset-video.mp4")} onClick={handleDownloadClick}><FlowIcon name="download" />Download</a>
        : <button type="button" role="menuitem" disabled><FlowIcon name="download" />Download</button>}
      <hr />
      <button type="button" role="menuitem" class="is-danger" disabled={props.moving} onClick={moveToHistory}><FlowIcon name="delete" />Move to Trash</button>
    </FloatingAssetMenu> : null}
  </article>;
}

function AudioCard(props) {
  let menuButtonEl;
  const asset = () => props.asset;
  const [menuOpen, setMenuOpen] = createSignal(false);
  const title = () => assetTitle(asset(), props.assetLabel);
  const src = () => props.assetUrl?.(asset()) || "";
  const stop = (event) => event.stopPropagation();
  const handleDragStart = (event) => {
    event.dataTransfer.effectAllowed = "copy";
    event.dataTransfer.setData("application/x-koubo-storyboard-asset", JSON.stringify(asset()));
    event.dataTransfer.setData("text/plain", asset()?.path || asset()?.filename || "");
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
  const handleDownloadClick = (event) => {
    stop(event);
    window.setTimeout(() => setMenuOpen(false), 0);
  };
  return <article
    class={`dh-audio-card ${props.selected ? "is-selected" : ""} ${props.moving ? "is-moving" : ""} ${menuOpen() ? "is-menu-open" : ""}`}
    draggable
    onDragStart={handleDragStart}
    onClick={() => props.onSelect?.(asset())}
  >
    <audio src={src()} title={title()} controls preload="none" onClick={() => props.onSelect?.(asset())} />
    <button ref={(el) => { menuButtonEl = el; }} type="button" class="dh-audio-menu-button" title="More" aria-expanded={menuOpen()} onClick={(event) => {
      stop(event);
      setMenuOpen((value) => !value);
    }}>
      <FlowIcon name="moreVert" />
    </button>
    {menuOpen() ? <FloatingAssetMenu anchor={() => menuButtonEl} onClose={() => setMenuOpen(false)} onClick={stop}>
      <button type="button" role="menuitem" disabled={props.moving} onClick={renameAsset}><FlowIcon name="editSquare" />Rename</button>
      {src()
        ? <a role="menuitem" href={downloadUrl(src())} download={assetDownloadName(asset(), "asset-audio.wav")} onClick={handleDownloadClick}><FlowIcon name="download" />Download</a>
        : <button type="button" role="menuitem" disabled><FlowIcon name="download" />Download</button>}
      <hr />
      <button type="button" role="menuitem" class="is-danger" disabled={props.moving} onClick={moveToHistory}><FlowIcon name="delete" />Move to Trash</button>
    </FloatingAssetMenu> : null}
  </article>;
}

function assetKey(asset) {
  return asset?.id || asset?.path || "";
}

function AssetSection(props) {
  const [draggingUpload, setDraggingUpload] = createSignal(false);
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
    if (props.kind === "image") {
      const files = await props.filesFromDataTransfer?.(event.dataTransfer, "digital_human_images_drop");
      if (files?.length) await props.uploadImageFiles?.(files, { source: "digital_human_images_drop" });
      return;
    }
    if (props.kind === "audio") {
      await props.uploadMediaFiles?.(event.dataTransfer?.files, "audio", { source: "digital_human_audios_drop" });
      return;
    }
    await props.uploadMediaFiles?.(event.dataTransfer?.files, "videos", { source: "digital_human_videos_drop" });
  };
  return <section
    class={`ual-grid-wrap ual-video-workspace-section dh-asset-section is-${props.kind} ${draggingUpload() ? "is-dragging-upload" : ""}`}
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
    </header>
    <Show when={props.items().length} fallback={<div class="ual-empty">{props.emptyText}</div>}>
      <div
        class="ual-grid"
        style={{
          "--ual-image-columns": String(props.imageColumns?.() || 6),
          "--dh-audio-card-max": props.audioCardMaxWidth?.() || "360px",
        }}
      >
        <For each={props.items()}>{(asset) => {
          if (props.kind === "image") return <ImageCard
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
          />;
          if (props.kind === "audio") return <AudioCard
            asset={asset}
            assetUrl={props.assetUrl}
            thumbnailUrl={props.thumbnailUrl}
            assetLabel={props.assetLabel}
            selected={assetKey(props.selectedAudio?.()) && assetKey(props.selectedAudio?.()) === assetKey(asset)}
            moving={props.movingIds?.().has(asset.id || asset.path)}
            onSelect={props.onSelectAudioAsset}
            onRenameAsset={props.onRenameAsset}
            onMoveToHistory={props.onMoveAudioToHistory}
          />;
          return <VideoCard
            asset={asset}
            assetUrl={props.assetUrl}
            thumbnailUrl={props.thumbnailUrl}
            assetLabel={props.assetLabel}
            moving={props.movingIds?.().has(asset.id || asset.path)}
            onRenameAsset={props.onRenameAsset}
            onMoveToHistory={props.onMoveVideoToHistory}
          />;
        }}</For>
      </div>
    </Show>
  </section>;
}

export default function DigitalHumanWorkspace(props) {
  return <div class="dh-asset-workspace ual-video-workspace-library" aria-label="Digital Human Asset Library">
    <div class="dh-asset-top-row">
      <AssetSection
        kind="image"
        title="Images"
        items={props.images}
        emptyText="No images in SessionOutput/storyboard/assets/images/"
        assetUrl={props.assetUrl}
        thumbnailUrl={props.thumbnailUrl}
        assetLabel={props.assetLabel}
        selectedIds={props.selectedIds}
        selectedAudio={props.selectedAudio}
        movingIds={props.movingIds}
        uploadBusy={props.uploadBusy}
        uploadImageFiles={props.uploadImageFiles}
        filesFromDataTransfer={props.filesFromDataTransfer}
        onToggleReference={props.onToggleReference}
        onSelectAudioAsset={props.onSelectAudioAsset}
        onPreview={props.onPreview}
        onRenameAsset={props.onRenameAsset}
        onMoveImageToHistory={props.onMoveImageToHistory}
        imageColumns={() => compactColumns(props.imageColumns)}
      />
      <AssetSection
        kind="audio"
        title="Audios"
        items={props.audios}
        emptyText="No audios in SessionOutput/storyboard/assets/audios/"
        assetUrl={props.assetUrl}
        thumbnailUrl={props.thumbnailUrl}
        assetLabel={props.assetLabel}
        selectedAudio={props.selectedAudio}
        movingIds={props.movingIds}
        uploadBusy={props.uploadBusy}
        uploadMediaFiles={props.uploadMediaFiles}
        onSelectAudioAsset={props.onSelectAudioAsset}
        onRenameAsset={props.onRenameAsset}
        onMoveAudioToHistory={props.onMoveAudioToHistory}
        imageColumns={() => audioColumns(props.imageColumns)}
        audioCardMaxWidth={() => audioCardMaxWidth(props.imageColumns)}
      />
    </div>
    <AssetSection
      kind="video"
      title="Videos"
      items={props.videos}
      emptyText="No videos in SessionOutput/storyboard/assets/videos/"
      assetUrl={props.assetUrl}
      thumbnailUrl={props.thumbnailUrl}
      assetLabel={props.assetLabel}
      movingIds={props.movingIds}
      uploadBusy={props.uploadBusy}
      uploadMediaFiles={props.uploadMediaFiles}
      onRenameAsset={props.onRenameAsset}
        onMoveVideoToHistory={props.onMoveVideoToHistory}
        imageColumns={props.imageColumns}
    />
  </div>;
}
