import { For, Show, createSignal } from "solid-js";
import FloatingAssetMenu from "./FloatingAssetMenu.jsx";
import FlowIcon from "./FlowIcon.jsx";

const MEDIA_LABELS = {
  videos: {
    singular: "视频",
    plural: "视频",
    accept: "video/*,.mp4,.mov,.webm,.m4v",
    empty: "SessionOutput/storyboard/assets/videos/ 中暂无视频",
  },
  audio: {
    singular: "音频",
    plural: "音频",
    accept: "audio/*,.wav,.mp3,.m4a,.aac,.ogg,.oga,.flac,.opus,.aiff,.aif,.caf,.weba,.wma",
    empty: "SessionOutput/storyboard/assets/audios/ 中暂无音频",
  },
};

function mediaLabel(kind, key) {
  return MEDIA_LABELS[kind]?.[key] || MEDIA_LABELS.videos[key];
}

function assetTitle(asset) {
  return String(asset?.label || asset?.filename || asset?.path || "媒体");
}

function MediaCard(props) {
  let menuButtonEl;
  const asset = () => props.asset;
  const [menuOpen, setMenuOpen] = createSignal(false);
  const src = () => props.mediaUrl?.(asset()) || "";
  const title = () => props.assetLabel?.(asset()) || assetTitle(asset());
  const stop = (event) => event.stopPropagation();
  const renameAsset = (event) => {
    stop(event);
    setMenuOpen(false);
    const current = title();
    const nextName = window.prompt("重命名素材", current);
    if (!nextName || nextName.trim() === current) return;
    void props.onRenameAsset?.(asset(), nextName.trim());
  };
  const moveToHistory = (event) => {
    stop(event);
    setMenuOpen(false);
    void props.onMoveToHistory?.(asset());
  };
  return <article class={`ual-media-card ${props.kind === "audio" ? "is-audio" : "is-video"} ${props.moving ? "is-moving" : ""}`}>
    <div class="ual-media-preview">
      <Show when={props.kind === "audio"} fallback={<video src={src()} controls preload="none" />}>
        <div class="ual-audio-preview">
          <FlowIcon name="audio" />
          <audio src={src()} controls preload="none" />
        </div>
      </Show>
    </div>
    <div class="ual-media-body">
      <strong title={title()}>{title()}</strong>
      <span title={asset()?.path || ""}>{asset()?.source || asset()?.kind || mediaLabel(props.kind, "singular")}</span>
    </div>
    <button ref={(el) => { menuButtonEl = el; }} class="ual-media-menu-button" type="button" title="更多" aria-expanded={menuOpen()} onClick={(event) => {
      stop(event);
      setMenuOpen((value) => !value);
    }}>
      <FlowIcon name="moreVert" />
    </button>
    {menuOpen() ? <FloatingAssetMenu class="ual-media-menu" anchor={() => menuButtonEl} onClose={() => setMenuOpen(false)} onClick={stop}>
      <button type="button" role="menuitem" disabled={props.moving} onClick={renameAsset}><FlowIcon name="editSquare" />重命名</button>
      <button type="button" role="menuitem" class="is-danger" disabled={props.moving} onClick={moveToHistory}><FlowIcon name="delete" />移到回收站</button>
    </FloatingAssetMenu> : null}
  </article>;
}

export default function MediaGrid(props) {
  let fileInput;
  let folderInput;
  const [draggingUpload, setDraggingUpload] = createSignal(false);
  const kind = () => props.kind || "videos";
  const dragHasFiles = (event) => Array.from(event.dataTransfer?.types || []).includes("Files");
  const handleDrag = (event) => {
    if (!dragHasFiles(event)) return;
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = "copy";
    setDraggingUpload(true);
  };
  const uploadFiles = async (files, source) => {
    if (!files?.length || props.uploadBusy?.()) return;
    await props.uploadMediaFiles?.(files, kind(), { source });
  };
  const handleDrop = async (event) => {
    if (!dragHasFiles(event)) return;
    event.preventDefault();
    event.stopPropagation();
    setDraggingUpload(false);
    await uploadFiles(event.dataTransfer?.files, `${kind()}_drop`);
  };
  const chooseFiles = async (event, source) => {
    const input = event.currentTarget;
    await uploadFiles(input.files, source);
    input.value = "";
  };
  return <section
    class={`ual-grid-wrap ual-media-wrap ${draggingUpload() ? "is-dragging-upload" : ""}`}
    aria-label={mediaLabel(kind(), "plural")}
    tabIndex="0"
    onDragEnter={handleDrag}
    onDragOver={handleDrag}
    onDragLeave={(event) => {
      if (!event.currentTarget.contains(event.relatedTarget)) setDraggingUpload(false);
    }}
    onDrop={handleDrop}
  >
    <Show when={draggingUpload()}>
      <div class="ual-grid-drop-overlay">{props.uploadBusy?.() ? "上传中..." : `拖拽上传${mediaLabel(kind(), "plural")}`}</div>
    </Show>
    <div class="ual-media-actions">
      <Show when={props.showAgent !== false}>
        <button type="button" class="is-agent" onClick={() => props.openAgent?.()}>
          <FlowIcon name="addNotes" />
          <span>智能体</span>
        </button>
      </Show>
      <button type="button" disabled={props.uploadBusy?.()} onClick={() => fileInput?.click()}>
        <FlowIcon name="add" />
        <span>{props.uploadBusy?.() ? "上传中" : `上传${mediaLabel(kind(), "singular")}`}</span>
      </button>
      <button type="button" disabled={props.uploadBusy?.()} onClick={() => folderInput?.click()}>
        <FlowIcon name="folder" />
        <span>文件夹</span>
      </button>
      <input ref={fileInput} type="file" accept={mediaLabel(kind(), "accept")} multiple hidden onChange={(event) => chooseFiles(event, `${kind()}_button`)} />
      <input ref={folderInput} type="file" accept={mediaLabel(kind(), "accept")} multiple webkitdirectory="" hidden onChange={(event) => chooseFiles(event, `${kind()}_folder`)} />
    </div>
    <Show when={props.uploadStatus?.()?.text}>
      <div class={`ual-media-upload-status is-${props.uploadStatus?.()?.tone || "info"}`}>
        {props.uploadStatus?.()?.text}
      </div>
    </Show>
    <Show when={props.items().length} fallback={<div class="ual-empty">{mediaLabel(kind(), "empty")}</div>}>
      <div class="ual-media-grid">
        <For each={props.items()}>{(asset) => <MediaCard
          kind={kind()}
          asset={asset}
          mediaUrl={props.mediaUrl}
          assetLabel={props.assetLabel}
          moving={props.movingIds?.().has(asset.id || asset.path)}
          onRenameAsset={props.onRenameAsset}
          onMoveToHistory={props.onMoveToHistory}
        />}</For>
      </div>
    </Show>
  </section>;
}
