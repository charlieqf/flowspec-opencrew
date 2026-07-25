import { For, Show, createSignal } from "solid-js";
import { assetIdentity, assetUrl } from "../storyboardAssets.js";
import { ImageIcon, SearchIcon, XIcon } from "../storyboardIcons.jsx";

export function StoryboardAssetPool(props) {
  let fileInput;
  let folderInput;
  const [draggingUpload, setDraggingUpload] = createSignal(false);
  const imageTypes = new Set(["image/png", "image/jpeg", "image/webp"]);
  const isImageFile = (file) => imageTypes.has(String(file?.type || "").toLowerCase()) || /\.(png|jpe?g|webp)$/i.test(file?.name || "");
  const readDirectory = (reader) => new Promise((resolve) => {
    const results = [];
    const read = () => reader.readEntries((entries) => {
      if (!entries.length) return resolve(results);
      results.push(...entries);
      read();
    }, () => resolve(results));
    read();
  });
  const filesFromEntry = async (entry) => {
    if (!entry) return [];
    if (entry.isFile) {
      return new Promise((resolve) => entry.file((file) => resolve(isImageFile(file) ? [file] : []), () => resolve([])));
    }
    if (!entry.isDirectory) return [];
    const entries = await readDirectory(entry.createReader());
    const nested = await Promise.all(entries.map(filesFromEntry));
    return nested.flat();
  };
  const droppedFiles = async (dataTransfer) => {
    const items = Array.from(dataTransfer?.items || []);
    if (items.length && items.some((item) => item.kind === "file")) {
      const nested = await Promise.all(items.filter((item) => item.kind === "file").map((item) => {
        const entry = item.webkitGetAsEntry?.();
        if (entry) return filesFromEntry(entry);
        const file = item.getAsFile?.();
        return Promise.resolve(file && isImageFile(file) ? [file] : []);
      }));
      return nested.flat();
    }
    return Array.from(dataTransfer?.files || []).filter(isImageFile);
  };
  const uploadFiles = async (event) => {
    const input = event.currentTarget;
    const files = input.files;
    if (files?.length) await props.onUploadAssets?.(files);
    input.value = "";
  };
  const previewAsset = (event, item) => {
    event.preventDefault();
    event.stopPropagation();
    props.onPreviewAsset?.(item);
  };
  const previewAssetKey = (event, item) => {
    if (!["Enter", " "].includes(event.key)) return;
    previewAsset(event, item);
  };
  const dragUpload = (event) => {
    if (!Array.from(event.dataTransfer?.types || []).includes("Files")) return;
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = "copy";
    setDraggingUpload(true);
  };
  const dropUpload = async (event) => {
    if (!Array.from(event.dataTransfer?.types || []).includes("Files")) return;
    event.preventDefault();
    event.stopPropagation();
    setDraggingUpload(false);
    const files = await droppedFiles(event.dataTransfer);
    if (files.length) await props.onUploadAssets?.(files);
  };
  const manualSelected = (item) => item && props.selectedAsset() && assetIdentity(props.selectedAsset()) === assetIdentity(item);
  return <aside class="ocsb-right">
    <header><h3>Asset Pool</h3></header>
    <section class="ocsb-asset-section">
      <div class="ocsb-asset-section-head">
        <strong>Rebuild References</strong>
        <span>{props.assetShotGroups().length}</span>
      </div>
      <Show when={props.assetShotGroups().length} fallback={<div class="ocsb-empty">暂无 Rebuild 参考图</div>}>
        <div class="ocsb-asset-shot-list"><For each={props.assetShotGroups()}>{(group) => <section class="ocsb-asset-shot">
          <div class="ocsb-asset-shot-head">
            <strong>{group.shot_id}</strong>
            <span>{group.scene_count || 1} Scenes · {props.formatTime(group.duration)}</span>
          </div>
          <div class="ocsb-asset-scene-grid"><For each={group.scenes}>{(sceneGroup) => <For each={sceneGroup.slots}>{(slot) => {
            const item = slot.item;
            const reference = slot.reference || item || {};
            const isPlaced = Boolean(slot.placed);
            const duration = Number(sceneGroup.duration || reference.duration || 0);
            const chars = Number(sceneGroup.char_count || reference.char_count || 0);
            const text = sceneGroup.text || reference.srt_text || "";
            const selected = item && props.selectedAsset() && assetIdentity(props.selectedAsset()) === assetIdentity(item);
            const content = <div class="ocsb-asset-scene-overlay">
              <strong>{sceneGroup.scene_id}</strong>
              <span>{duration.toFixed(2)}s · {chars}字</span>
              <p>{text || "Empty line..."}</p>
              <em>{slot.role}</em>
            </div>;
            return <Show when={item || isPlaced} fallback={<article class="ocsb-asset-scene-blank" aria-hidden="true"></article>}>
              <Show when={item} fallback={<article class="ocsb-asset-scene-card is-empty is-placed" title="已放入 Dialogue，删除后会回到这里">
                <ImageIcon />
                {content}
              </article>}>
              <button class={`ocsb-asset-scene-card ${selected ? "is-selected" : ""} ${isPlaced ? "is-placed" : ""}`} type="button" draggable="true" onPointerDown={(event) => props.beginPointerAssetDrag(event, item)} onMouseDown={(event) => props.beginPointerAssetDrag(event, item)} onClick={() => props.clickAsset(item)} onDragStart={(event) => props.dragAsset(event, item)}>
                <img src={assetUrl(item, props.task()?.session_id)} loading="lazy" draggable="false" onDragStart={(event) => props.dragAsset(event, item)} />
                <span class="ocsb-asset-preview" role="button" tabIndex="0" title="Preview Image" onPointerDown={(event) => event.stopPropagation()} onMouseDown={(event) => event.stopPropagation()} onClick={(event) => previewAsset(event, item)} onKeyDown={(event) => previewAssetKey(event, item)}><SearchIcon /></span>
                <Show when={isPlaced}><b class="ocsb-asset-used-badge">已用</b></Show>
                {content}
              </button>
              </Show>
            </Show>;
          }}</For>}</For></div>
        </section>}</For></div>
      </Show>
    </section>
    <section class={`ocsb-asset-section ocsb-asset-upload-section ${draggingUpload() ? "is-dragging" : ""}`} onDragEnter={dragUpload} onDragOver={dragUpload} onDragLeave={(event) => {
      if (!event.currentTarget.contains(event.relatedTarget)) setDraggingUpload(false);
    }} onDrop={dropUpload}>
      <div class="ocsb-asset-section-head">
        <strong>Uploaded</strong>
        <span>{props.manualAssetItems().length}</span>
      </div>
      <div class="ocsb-asset-upload-actions">
        <input ref={fileInput} type="file" accept="image/png,image/jpeg,image/webp" multiple onChange={uploadFiles} />
        <input ref={folderInput} type="file" accept="image/png,image/jpeg,image/webp" multiple webkitdirectory={true} onChange={uploadFiles} />
        <button type="button" disabled={props.uploadBusy?.()} onClick={() => fileInput?.click()}>{props.uploadBusy?.() ? "Uploading" : "Upload"}</button>
        <button type="button" disabled={props.uploadBusy?.()} onClick={() => folderInput?.click()}>Folder</button>
      </div>
      <Show when={props.manualAssetItems().length} fallback={<div class="ocsb-empty ocsb-upload-dropzone">{draggingUpload() ? "松开以上传" : "暂无手动上传图片"}</div>}>
        <div class="ocsb-asset-manual-grid"><For each={props.manualAssetItems()}>{(item) => <article class="ocsb-asset-manual-item">
          <button class={`ocsb-asset-card ${manualSelected(item) ? "is-selected" : ""} ${item.placed ? "is-placed" : ""}`} type="button" draggable="true" title={item.label || item.filename || item.path} onPointerDown={(event) => props.beginPointerAssetDrag(event, item)} onMouseDown={(event) => props.beginPointerAssetDrag(event, item)} onClick={() => props.clickAsset(item)} onDragStart={(event) => props.dragAsset(event, item)}>
            <img src={assetUrl(item, props.task()?.session_id)} loading="lazy" draggable="false" onDragStart={(event) => props.dragAsset(event, item)} />
            <span class="ocsb-asset-preview" role="button" tabIndex="0" title="Preview Image" onPointerDown={(event) => event.stopPropagation()} onMouseDown={(event) => event.stopPropagation()} onClick={(event) => previewAsset(event, item)} onKeyDown={(event) => previewAssetKey(event, item)}><SearchIcon /></span>
            <Show when={item.placed}><b class="ocsb-asset-used-badge">已用</b></Show>
            <div><span>{item.label || item.filename || "Uploaded Asset"}</span></div>
          </button>
          <button class="ocsb-asset-delete" type="button" title="Delete Uploaded Asset" disabled={props.deletingAssetId?.() === item.id} onPointerDown={(event) => event.stopPropagation()} onMouseDown={(event) => event.stopPropagation()} onClick={(event) => {
            event.preventDefault();
            event.stopPropagation();
            props.onDeleteAsset?.(item);
          }}><XIcon /></button>
        </article>}</For></div>
      </Show>
    </section>
  </aside>;
}
