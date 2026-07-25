import { For, Show, createEffect, createMemo, createSignal } from "solid-js";
import { normalizeEditableTags } from "../mediaLibraryViewModel.js";
import MediaLibraryTagInput from "./MediaLibraryTagInput.jsx";

export default function MediaLibraryTagEditor(props) {
  const [tags, setTags] = createSignal([]);

  createEffect(() => {
    props.asset?.assetId;
    setTags(normalizeEditableTags(props.asset?.tags));
  });

  const hasLegacyValues = createMemo(() => (
    tags().length > 20 || tags().some((tag) => !tag || tag.length > 32)
  ));

  return (
    <div class="media-library-tag-editor-backdrop" onPointerDown={(event) => {
      if (event.target === event.currentTarget && !props.busy) props.onClose();
    }}>
      <section class="media-library-tag-editor" role="dialog" aria-modal="true" aria-labelledby="media-library-tag-editor-title">
        <form onSubmit={(event) => {
          event.preventDefault();
          if (!props.busy) props.onSave(tags());
        }}>
          <header>
            <div><h3 id="media-library-tag-editor-title">编辑标签</h3><p title={props.asset.displayName}>{props.asset.displayName}</p></div>
            <button type="button" aria-label="关闭标签编辑器" disabled={props.busy} onClick={props.onClose}>×</button>
          </header>
          <div class="media-library-tag-editor-body">
            <div class="media-library-tag-chips" aria-label="当前标签">
              <For each={tags()} fallback={<span class="media-library-tag-empty">暂未添加标签</span>}>{(tag, index) => (
                <span classList={{ legacy: !tag || tag.length > 32 }} title={tag || "历史空标签"}>
                  <b>{tag || "历史空标签"}</b>
                  <button type="button" aria-label={`删除标签 ${tag || "历史空标签"}`} onClick={() => setTags((current) => current.filter((_, itemIndex) => itemIndex !== index()))}>×</button>
                </span>
              )}</For>
            </div>
            <Show when={hasLegacyValues()}><p class="media-library-tag-legacy-note">此素材含历史异常标签；可原样保留，但不能新增异常值或增加超限数量，请逐步清理。</p></Show>
            <MediaLibraryTagInput tags={tags()} suggestions={props.suggestions} onChange={setTags} />
            <Show when={props.error}><p class="media-library-tag-save-error" role="alert">{props.error}</p></Show>
          </div>
          <footer><button type="button" disabled={props.busy} onClick={props.onClose}>取消</button><button type="submit" class="primary" disabled={props.busy}>{props.busy ? "保存中…" : "保存"}</button></footer>
        </form>
      </section>
    </div>
  );
}
