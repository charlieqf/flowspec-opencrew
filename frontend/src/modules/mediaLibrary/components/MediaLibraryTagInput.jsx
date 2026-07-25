import { For, Show, createMemo, createSignal } from "solid-js";
import { addEditableTag } from "../mediaLibraryViewModel.js";

export default function MediaLibraryTagInput(props) {
  const [draft, setDraft] = createSignal("");
  const [inputError, setInputError] = createSignal("");
  const suggestions = createMemo(() => {
    const query = draft().trim().toLocaleLowerCase();
    return (props.suggestions || [])
      .filter((tag) => !props.tags.includes(tag))
      .filter((tag) => !query || tag.toLocaleLowerCase().includes(query))
      .slice(0, 8);
  });

  const commit = (value = draft()) => {
    const normalized = String(value ?? "").trim();
    if (!normalized) {
      setDraft("");
      return;
    }
    if (normalized.length > 32) {
      setInputError("单个标签最多 32 个字符。");
      return;
    }
    if (props.tags.length >= 20) {
      setInputError("标签已达到 20 个，请先删除标签。");
      return;
    }
    props.onChange(addEditableTag(props.tags, normalized));
    setDraft("");
    setInputError("");
  };

  const onInput = (event) => {
    const value = event.currentTarget.value;
    if (/[,，]/.test(value)) {
      value.split(/[,，]/).forEach((part) => commit(part));
      setDraft("");
      return;
    }
    setDraft(value);
    setInputError("");
  };

  return (
    <div class="media-library-tag-input">
      <label for="media-library-tag-entry">添加标签</label>
      <input
        id="media-library-tag-entry"
        value={draft()}
        maxlength="33"
        autocomplete="off"
        placeholder="输入标签，按 Enter 或逗号添加"
        onInput={onInput}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === "," || event.key === "，") {
            event.preventDefault();
            commit();
          }
        }}
      />
      <div class="media-library-tag-input-meta"><span>{props.tags.length}/20 个</span><span>{draft().length}/32 字符</span></div>
      <Show when={inputError()}><p class="media-library-tag-error" role="alert">{inputError()}</p></Show>
      <Show when={draft().trim() && suggestions().length}>
        <div class="media-library-tag-suggestions" aria-label="已有标签建议">
          <For each={suggestions()}>{(tag) => <button type="button" onClick={() => commit(tag)}>{tag}</button>}</For>
        </div>
      </Show>
    </div>
  );
}
