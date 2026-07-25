import { For, Show } from "solid-js";
import MediaLibraryCard from "./MediaLibraryCard.jsx";

export default function MediaLibraryCardGrid(props) {
  return (
    <section class={`media-library-card-grid-wrap columns-${props.cardColumns}`}>
      <Show when={props.items.length} fallback={props.emptyFallback}>
        <div class="media-library-card-grid">
          <For each={props.items}>{(asset) => <MediaLibraryCard asset={asset} {...props} />}</For>
        </div>
      </Show>
    </section>
  );
}
