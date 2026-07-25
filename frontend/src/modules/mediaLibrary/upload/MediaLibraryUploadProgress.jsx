import { Show } from "solid-js";
import { formatUploadBytes, mediaUploadStageLabel } from "./mediaLibraryUploadModel.js";

export default function MediaLibraryUploadProgress(props) {
  return (
    <div class={`media-library-upload-progress is-${props.stage}`}>
      <div class="media-library-upload-progress-head"><strong>{mediaUploadStageLabel(props.stage)}</strong><span>{props.percent}%</span></div>
      <div class="media-library-upload-progress-track" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow={props.percent}><span style={{ width: `${props.percent}%` }} /></div>
      <div class="media-library-upload-progress-meta"><span>{formatUploadBytes(props.loaded)} / {formatUploadBytes(props.total)}</span><Show when={props.stage === "saving"}><small>视频已传完，正在合并并保存到 Session</small></Show></div>
    </div>
  );
}
