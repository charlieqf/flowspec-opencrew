import { For, Show, createSignal } from "solid-js";
import ImageCard from "./ImageCard.jsx";

export default function ImageGrid(props) {
  const [draggingUpload, setDraggingUpload] = createSignal(false);
  let gridEl;
  const focusGrid = () => gridEl?.focus?.({ preventScroll: true });
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
    const files = await props.filesFromDataTransfer?.(event.dataTransfer, "grid_drop");
    if (files?.length) await props.uploadImageFiles?.(files, { source: "grid_drop" });
  };
  const handlePaste = async (event) => {
    const files = props.filesFromClipboard?.(event.clipboardData, "grid_paste");
    if (!files?.length) return;
    event.preventDefault();
    event.stopPropagation();
    await props.uploadImageFiles?.(files, { source: "grid_paste" });
  };
  return <section
    ref={(el) => { gridEl = el; }}
    class={`ual-grid-wrap ${draggingUpload() ? "is-dragging-upload" : ""}`}
    aria-label="图片"
    tabIndex="0"
    onPointerDown={focusGrid}
    onDragEnter={handleDrag}
    onDragOver={handleDrag}
    onDragLeave={(event) => {
      if (!event.currentTarget.contains(event.relatedTarget)) setDraggingUpload(false);
    }}
    onDrop={handleDrop}
    onPaste={handlePaste}
  >
    <Show when={draggingUpload()}>
      <div class="ual-grid-drop-overlay">{props.uploadBusy?.() ? "Uploading..." : "松开以上传到 Asset Library"}</div>
    </Show>
    <Show when={props.uploadStatus?.()?.text}>
      <div class={`ual-media-upload-status is-${props.uploadStatus?.()?.tone || "info"}`}>
        {props.uploadStatus?.()?.text}
      </div>
    </Show>
    <Show when={props.items().length} fallback={<div class="ual-empty">No images in SessionOutput/storyboard/assets/images/</div>}>
      <div class="ual-grid" style={{ "--ual-image-columns": String(props.imageColumns?.() || 6) }}>
        <For each={props.items()}>{(asset) => <ImageCard
          asset={asset}
          selected={props.selectedIds().has(asset.id || asset.path)}
          imageUrl={props.imageUrl}
          thumbnailUrl={props.thumbnailUrl}
          assetLabel={props.assetLabel}
          onToggle={props.toggleSelected}
          onPreview={props.onPreview}
          moving={props.movingIds?.().has(asset.id || asset.path)}
          onMoveToHistory={props.onMoveToHistory}
          onRenameAsset={props.onRenameAsset}
          onAddReferenceAsset={props.onAddReferenceAsset}
        />}</For>
      </div>
    </Show>
  </section>;
}
