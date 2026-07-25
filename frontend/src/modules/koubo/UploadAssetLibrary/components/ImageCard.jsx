import { createEffect, createSignal } from "solid-js";
import { shapeFromAspect, shapeFromDimensions } from "../uploadAssetLibraryModel.js";
import FloatingAssetMenu from "./FloatingAssetMenu.jsx";
import FlowIcon from "./FlowIcon.jsx";

function assetDownloadName(asset) {
  const source = String(asset?.filename || asset?.label || asset?.path || "asset-image.png").trim();
  return source.split(/[\\/]/).filter(Boolean).pop() || "asset-image.png";
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

export default function ImageCard(props) {
  let menuButtonEl;
  const asset = () => props.asset;
  const src = () => props.imageUrl(asset());
  const thumbnailSrc = () => props.thumbnailUrl?.(asset()) || src();
  const [thumbnailFailed, setThumbnailFailed] = createSignal(false);
  const [shape, setShape] = createSignal(shapeFromAspect(asset()?.aspect_ratio || asset()?.aspectRatio));
  const [menuOpen, setMenuOpen] = createSignal(false);
  const displaySrc = () => thumbnailFailed() ? src() : thumbnailSrc();
  createEffect(() => {
    thumbnailSrc();
    setThumbnailFailed(false);
  });
  const stop = (event) => event.stopPropagation();
  const addToReference = (event) => {
    stop(event);
    setMenuOpen(false);
    props.onAddReferenceAsset?.(asset());
  };
  const handleDragStart = (event) => {
    if (asset()?.pending) {
      event.preventDefault();
      return;
    }
    event.dataTransfer.effectAllowed = "copy";
    event.dataTransfer.setData("application/x-koubo-storyboard-asset", JSON.stringify(asset()));
    event.dataTransfer.setData("text/plain", asset()?.path || asset()?.filename || "");
  };
  const previewAsset = (event) => {
    stop(event);
    setMenuOpen(false);
    props.onPreview?.(asset());
  };
  const handleDownloadClick = (event) => {
    stop(event);
    window.setTimeout(() => setMenuOpen(false), 0);
  };
  const moveToHistory = (event) => {
    stop(event);
    setMenuOpen(false);
    void props.onMoveToHistory?.(asset());
  };
  const renameAsset = (event) => {
    stop(event);
    setMenuOpen(false);
    const current = props.assetLabel(asset());
    const nextName = window.prompt("Rename asset", current);
    if (!nextName || nextName.trim() === current) return;
    void props.onRenameAsset?.(asset(), nextName.trim());
  };
  return <article class={`ual-card is-${shape()} ${asset()?.pending ? "is-pending" : ""} ${asset()?.failed ? "is-failed" : ""} ${props.selected ? "is-selected" : ""} ${menuOpen() ? "is-menu-open" : ""}`} draggable={!asset()?.pending} onDragStart={handleDragStart}>
    <button class="ual-card-image" type="button" title={props.assetLabel(asset())} disabled={asset()?.pending} draggable={!asset()?.pending} onDragStart={handleDragStart} onClick={previewAsset}>
      {asset()?.pending ? <span class="ual-card-placeholder" aria-hidden="true" /> : <img src={displaySrc()} loading="lazy" draggable="false" onError={() => setThumbnailFailed(true)} onLoad={(event) => {
        setShape(shapeFromDimensions(event.currentTarget.naturalWidth, event.currentTarget.naturalHeight, shape()));
      }} />}
      {asset()?.pending ? <span class="ual-card-source" aria-hidden="true" /> : null}
      {asset()?.progressLabel ? <span class="ual-card-progress">{asset().progressLabel}</span> : null}
    </button>
    <div class="ual-card-actions">
      <button type="button" title="Preview" onClick={previewAsset}>
        <FlowIcon name="image" />
      </button>
      <button type="button" title="Add to prompt" onClick={(event) => {
        addToReference(event);
      }}>
        <FlowIcon name="redo" />
      </button>
      <button ref={(el) => { menuButtonEl = el; }} type="button" title="More" aria-expanded={menuOpen()} onClick={(event) => {
        stop(event);
        setMenuOpen((value) => !value);
      }}>
        <FlowIcon name="moreVert" />
      </button>
    </div>
    {menuOpen() ? <FloatingAssetMenu anchor={() => menuButtonEl} onClose={() => setMenuOpen(false)} onClick={stop}>
      <button type="button" role="menuitem"><FlowIcon name="swap" />Animate</button>
      <button type="button" role="menuitem" onClick={previewAsset}><FlowIcon name="image" />Preview</button>
      <button type="button" role="menuitem" onClick={addToReference}><FlowIcon name="add" />Add to prompt</button>
      {src()
        ? <a role="menuitem" href={downloadUrl(src())} download={assetDownloadName(asset())} onClick={handleDownloadClick}><FlowIcon name="download" />Download</a>
        : <button type="button" role="menuitem" disabled><FlowIcon name="download" />Download</button>}
      <button type="button" role="menuitem" disabled={props.moving} onClick={renameAsset}><FlowIcon name="editSquare" />Rename</button>
      <button type="button" role="menuitem"><FlowIcon name="share" />Share</button>
      <hr />
      <button type="button" role="menuitem"><FlowIcon name="setCover" />Set project cover</button>
      <hr />
      <button type="button" role="menuitem"><FlowIcon name="flag" />Flag output</button>
      <hr />
      <button type="button" role="menuitem" class="is-danger" disabled={props.moving} onClick={moveToHistory}><FlowIcon name="delete" />Move to Trash</button>
    </FloatingAssetMenu> : null}
  </article>;
}
