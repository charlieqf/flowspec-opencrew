import { For, Show, createSignal, onCleanup, onMount } from "solid-js";
import FlowIcon from "./FlowIcon.jsx";

const IMAGE_COLUMN_OPTIONS = [4, 6, 8];

export default function ImageToolbar(props) {
  let columnPickerEl;
  const [columnsOpen, setColumnsOpen] = createSignal(false);
  const selectedCount = () => props.selectedHistoryCount?.() || 0;
  const visibleCount = () => props.visibleHistoryCount?.() || 0;
  const imageColumns = () => Number(props.imageColumns?.() || 6);
  const twoDigitCount = (value) => String(Number(value || 0)).padStart(2, "0");
  const searchPlaceholder = () => props.view() === "history"
    ? "Search history"
    : props.view() === "videos" || props.view() === "videos-agent"
      ? "Search videos and images"
      : props.view() === "digital-human-agent"
        ? "Search digital human assets"
        : props.view() === "prompt-agent"
          ? "Search prompt versions"
        : "Search images";
  onMount(() => {
    const closeColumnPicker = (event) => {
      if (!columnPickerEl?.contains?.(event.target)) setColumnsOpen(false);
    };
    document.addEventListener("pointerdown", closeColumnPicker, true);
    onCleanup(() => document.removeEventListener("pointerdown", closeColumnPicker, true));
  });
  return <header class="ual-toolbar">
    <div class="ual-search">
      <span><FlowIcon name="search" /></span>
      <input value={props.query()} onInput={(event) => props.setQuery(event.currentTarget.value)} placeholder={searchPlaceholder()} />
    </div>
    <Show when={props.view() === "history"}>
      <div class={`ual-history-select-tools ${props.historySelectionMode?.() ? "is-active" : ""}`} aria-label="History selection">
        <button class="ual-toolbar-icon-button" type="button" title={props.historySelectionMode?.() ? "Exit select mode" : "Select multiple images"} aria-pressed={props.historySelectionMode?.() ? "true" : "false"} onClick={() => props.setHistorySelectionMode?.(!props.historySelectionMode?.())}>
          <FlowIcon name={props.historySelectionMode?.() ? "check" : "radioButtonUnchecked"} />
        </button>
        <Show when={props.historySelectionMode?.()}>
          <button type="button" disabled={!visibleCount()} onClick={props.onSelectAllVisibleHistory}>{props.allVisibleHistorySelected?.() ? "取消选择" : "全选"}</button>
          <button type="button" disabled={!selectedCount()} onClick={props.onClearHistorySelection}>清空</button>
          <button class="is-danger" type="button" disabled={!selectedCount() || props.deletingHistoryBatch?.()} onClick={props.onDeleteSelectedHistory}>
            <FlowIcon name="delete" />
            <span>{props.deletingHistoryBatch?.() ? "删除中" : `删除 ${selectedCount()} 个`}</span>
          </button>
        </Show>
      </div>
    </Show>
    <div class="ual-toolbar-meta">
      <span class="ual-toolbar-meta-item is-images" title="图片"><FlowIcon name="image" />{twoDigitCount(props.imageCount())}</span>
      <span class="ual-toolbar-meta-item is-videos" title="视频"><FlowIcon name="video" />{twoDigitCount(props.videoCount?.())}</span>
      <span class="ual-toolbar-meta-item is-audio" title="音频"><FlowIcon name="audio" />{twoDigitCount(props.audioCount?.())}</span>
      <span class="ual-toolbar-meta-item is-history" title="历史"><FlowIcon name="history" />{twoDigitCount(props.historyCount())}</span>
    </div>
    <div class="ual-toolbar-icon-group">
      <div ref={(el) => { columnPickerEl = el; }} class="ual-column-picker">
        <button
          class="ual-toolbar-circle-button"
          type="button"
          aria-label="每行图片数"
          aria-expanded={columnsOpen() ? "true" : "false"}
          title={`每行 ${imageColumns()} 张图片`}
          onClick={() => setColumnsOpen((value) => !value)}
        >
          <FlowIcon name="gridView" />
        </button>
        <Show when={columnsOpen()}>
          <div class="ual-column-menu" role="menu" aria-label="每行图片数">
            <For each={IMAGE_COLUMN_OPTIONS}>{(count) => (
              <button
                class={imageColumns() === count ? "is-active" : ""}
                type="button"
                role="menuitemradio"
                aria-checked={imageColumns() === count ? "true" : "false"}
                onClick={() => {
                  props.setImageColumns?.(count);
                  setColumnsOpen(false);
                }}
              >{count}</button>
            )}</For>
          </div>
        </Show>
      </div>
      <button
        class="ual-toolbar-circle-button"
        type="button"
        aria-label={props.theme() === "light" ? "Switch to dark theme" : "Switch to light theme"}
        title={props.theme() === "light" ? "Switch to dark theme" : "Switch to light theme"}
        onClick={() => props.setTheme(props.theme() === "light" ? "dark" : "light")}
      >
        <FlowIcon name={props.theme() === "light" ? "sun" : "moon"} />
      </button>
    </div>
  </header>;
}
