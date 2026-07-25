import { Show, createEffect } from "solid-js";
import MediaLibraryUploadDropzone from "./MediaLibraryUploadDropzone.jsx";
import MediaLibraryUploadProgress from "./MediaLibraryUploadProgress.jsx";
import { useMediaLibraryUpload } from "./useMediaLibraryUpload.js";

export default function MediaLibraryUploadDialog(props) {
  const upload = useMediaLibraryUpload({
    onComplete: async (item, transaction) => {
      await props.onComplete?.(item, transaction);
      props.onClose?.();
    },
  });

  createEffect(() => {
    if (!props.open && !upload.active()) upload.reset();
  });

  async function requestClose() {
    if (upload.active() || upload.uploadId()) {
      if (!window.confirm("上传尚未完成，确认取消并清理本次上传吗？")) return;
      await upload.cancel();
    }
    upload.reset();
    props.onClose?.();
  }

  return (
    <Show when={props.open}>
      <div class="media-library-upload-overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) void requestClose(); }}>
        <section class="media-library-upload-dialog" role="dialog" aria-modal="true" aria-labelledby="media-library-upload-title">
          <header><h3 id="media-library-upload-title">素材上传</h3><button type="button" class="media-library-upload-close" title="关闭" aria-label="关闭" onClick={() => void requestClose()}>×</button></header>
          <div class="media-library-upload-body">
            <MediaLibraryUploadDropzone file={upload.file()} disabled={upload.active() || Boolean(upload.uploadId())} onSelect={upload.selectFile} />
            <Show when={["preparing", "uploading", "saving", "completed", "failed"].includes(upload.stage())}>
              <MediaLibraryUploadProgress stage={upload.stage()} percent={upload.percent()} loaded={upload.progressBytes()} total={upload.file()?.size || 0} />
            </Show>
            <Show when={upload.error()}><div class="media-library-upload-error">{upload.error()}</div></Show>
          </div>
          <footer>
            <button type="button" class="secondary" onClick={() => void requestClose()} disabled={upload.stage() === "saving"}>取消</button>
            <button type="button" class="primary" disabled={!upload.file() || upload.active() || upload.stage() === "completed"} onClick={() => void upload.start()}>{upload.stage() === "failed" ? "重试" : upload.active() ? "上传中" : "上传"}</button>
          </footer>
        </section>
      </div>
    </Show>
  );
}
