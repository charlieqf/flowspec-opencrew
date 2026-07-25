import { Show, createSignal } from "solid-js";
import { VideoLibraryGlyph } from "../components/MediaLibraryIcons.jsx";
import { formatUploadBytes } from "./mediaLibraryUploadModel.js";

export default function MediaLibraryUploadDropzone(props) {
  const [dragging, setDragging] = createSignal(false);
  let input;
  const choose = (files) => {
    const file = files?.[0];
    if (file) props.onSelect?.(file);
  };
  return (
    <div
      class="media-library-upload-dropzone"
      classList={{ "is-dragging": dragging(), "has-file": Boolean(props.file), "is-disabled": Boolean(props.disabled) }}
      onDragEnter={(event) => { event.preventDefault(); if (!props.disabled) setDragging(true); }}
      onDragOver={(event) => event.preventDefault()}
      onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setDragging(false); }}
      onDrop={(event) => { event.preventDefault(); setDragging(false); if (!props.disabled) choose(event.dataTransfer?.files); }}
      onClick={() => { if (!props.disabled) input?.click(); }}
      role="button"
      tabindex="0"
      onKeyDown={(event) => { if (!props.disabled && (event.key === "Enter" || event.key === " ")) input?.click(); }}
    >
      <input ref={(element) => { input = element; }} type="file" accept="video/*,.mp4,.mov,.m4v,.webm" onChange={(event) => { choose(event.currentTarget.files); event.currentTarget.value = ""; }} />
      <Show when={props.file} fallback={<><span class="media-library-upload-dropzone-icon"><VideoLibraryGlyph /></span><strong>拖拽视频到这里，或点击选择视频</strong><small>支持 MP4、MOV、M4V、WebM</small></>}>
        <span class="media-library-upload-dropzone-icon"><VideoLibraryGlyph /></span>
        <div class="media-library-upload-file-copy"><strong title={props.file?.name}>{props.file?.name}</strong><small>{formatUploadBytes(props.file?.size)}</small></div>
        <Show when={!props.disabled}><button type="button" onClick={(event) => { event.stopPropagation(); input?.click(); }}>重新选择</button></Show>
      </Show>
    </div>
  );
}
