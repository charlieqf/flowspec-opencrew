import { For, Show, createEffect, createSignal } from "solid-js";
import { assetKind, shapeFromAspect, shapeFromDimensions } from "../uploadAssetLibraryModel.js";
import FlowIcon from "./FlowIcon.jsx";

function HistoryCard(props) {
  const asset = () => props.asset;
  const [shape, setShape] = createSignal(shapeFromAspect(asset()?.aspect_ratio || asset()?.aspectRatio));
  const selected = () => props.selected?.();
  const moving = () => props.movingIds?.().has(asset().history_path || asset().path || asset().id);
  const kind = () => assetKind(asset());
  const thumbnailUrl = () => props.thumbnailUrl?.(asset()) || props.imageUrl(asset());
  const [thumbnailFailed, setThumbnailFailed] = createSignal(false);
  const imageSrc = () => thumbnailFailed() ? props.imageUrl(asset()) : thumbnailUrl();
  createEffect(() => {
    thumbnailUrl();
    setThumbnailFailed(false);
  });
  const handleAssetClick = () => {
    if (props.selectionMode?.()) {
      props.onToggleHistory?.(asset());
      return;
    }
    props.onPreview?.(asset());
  };
  const toggleSelected = (event) => {
    event.stopPropagation();
    props.onToggleHistory?.(asset());
  };
  const restoreAsset = (event) => {
    event.preventDefault();
    event.stopPropagation();
    if (moving()) return;
    void props.onRestoreHistory?.(asset());
  };
  const restoreTitle = () => {
    if (kind() === "video") return "恢复到视频";
    if (kind() === "audio") return "恢复到音频";
    return "恢复到图片";
  };
  return <article class={`ual-history-card is-${shape()} ${props.selectionMode?.() ? "is-selection-mode" : ""} ${selected() ? "is-selected" : ""}`}>
    <button class="ual-history-select" type="button" title={selected() ? "取消选择素材" : "选择素材"} aria-pressed={selected() ? "true" : "false"} disabled={moving()} onClick={toggleSelected}>
      <FlowIcon name={selected() ? "check" : "radioButtonUnchecked"} />
    </button>
    <button class="ual-history-image" type="button" title={props.assetLabel(asset())} onClick={handleAssetClick}>
      <Show when={kind() === "video"} fallback={
        <Show when={kind() === "audio"} fallback={
          <img src={imageSrc()} loading="lazy" draggable="false" onError={() => setThumbnailFailed(true)} onLoad={(event) => {
            setShape(shapeFromDimensions(event.currentTarget.naturalWidth, event.currentTarget.naturalHeight, shape()));
          }} />
        }>
          <span class="ual-history-audio"><FlowIcon name="audio" />音频</span>
        </Show>
      }>
        <Show when={thumbnailUrl() && !thumbnailFailed()} fallback={<div class="ual-video-thumb-placeholder ual-history-video-thumb" aria-hidden="true"><FlowIcon name="video" /></div>}>
          <img src={thumbnailUrl()} loading="lazy" draggable="false" onError={() => setThumbnailFailed(true)} onLoad={(event) => {
            setShape(shapeFromDimensions(event.currentTarget.naturalWidth, event.currentTarget.naturalHeight, shape()));
          }} />
        </Show>
        <span class="ual-video-card-badge" aria-hidden="true"><FlowIcon name="video" /></span>
      </Show>
    </button>
    <div class="ual-history-actions">
      <button type="button" title="Delete History Asset" disabled={moving()} onClick={(event) => {
        event.stopPropagation();
        void props.onDeleteHistory?.(asset());
      }}>
        <FlowIcon name="delete" />
      </button>
      <button class="ual-history-restore" type="button" title={restoreTitle()} disabled={moving()} onMouseDown={restoreAsset} onClick={(event) => {
        event.preventDefault();
        event.stopPropagation();
      }} onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") restoreAsset(event);
      }}>
        <FlowIcon name="redo" />
      </button>
    </div>
  </article>;
}

export default function HistoryGrid(props) {
  return <section class="ual-history-wrap" aria-label="History">
    <Show when={props.items().length} fallback={<div class="ual-empty">No assets in SessionOutput/storyboard/assets/history/</div>}>
      <div class="ual-history-grid">
        <For each={props.items()}>{(asset) => <HistoryCard
          asset={asset}
          imageUrl={props.imageUrl}
          thumbnailUrl={props.thumbnailUrl}
          assetLabel={props.assetLabel}
          selected={() => props.selectedIds?.().has(asset.history_path || asset.path || asset.id)}
          selectionMode={props.selectionMode}
          onToggleHistory={props.onToggleHistory}
          onPreview={props.onPreview}
          movingIds={props.movingIds}
          onDeleteHistory={props.onDeleteHistory}
          onRestoreHistory={props.onRestoreHistory}
        />}</For>
      </div>
    </Show>
  </section>;
}
