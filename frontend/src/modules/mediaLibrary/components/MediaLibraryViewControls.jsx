import { For, Show } from "solid-js";

export default function MediaLibraryViewControls(props) {
  return (
    <div class="media-library-view-controls" aria-label="素材库视图设置">
      <div class="media-library-view-switch" role="group" aria-label="视图">
        <button type="button" classList={{ active: props.viewMode === "table" }} aria-pressed={props.viewMode === "table"} onClick={() => props.onViewMode("table")}>列表</button>
        <button type="button" classList={{ active: props.viewMode === "cards" }} aria-pressed={props.viewMode === "cards"} onClick={() => props.onViewMode("cards")}>卡片</button>
      </div>
      <Show when={props.viewMode === "cards"}>
        <label>列数
          <select value={props.cardColumns} onChange={(event) => props.onCardColumns(Number(event.currentTarget.value))}>
            <For each={[2, 3, 4, 5, 6]}>{(columns) => <option value={columns}>{columns} 列</option>}</For>
          </select>
        </label>
      </Show>
    </div>
  );
}
