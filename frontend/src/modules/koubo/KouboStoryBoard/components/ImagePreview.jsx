import { Show, createEffect, createSignal } from "solid-js";
import { kbApi } from "../kouboStoryboardApi.js";
import { XIcon } from "../kouboStoryboardIcons.jsx";

export default function ImagePreview(props) {
  const [zoom, setZoom] = createSignal(1);
  createEffect(() => {
    if (props.image()) setZoom(1);
  });
  const path = () => props.image()?.path || props.image()?.history_path || "";
  const src = () => props.image()?.src || (path() ? kbApi.rawFileUrl(props.sessionId(), path()) : "");
  const label = () => props.image()?.label || props.image()?.filename || props.image()?.text || path() || "Image";
  const downloadName = () => String(label()).split(/[\\/]/).filter(Boolean).pop() || "image.png";
  const downloadHref = () => {
    const value = src();
    if (!value) return "";
    try {
      const url = new URL(value, window.location.href);
      url.searchParams.set("download", "1");
      return url.toString();
    } catch {
      const separator = value.includes("?") ? "&" : "?";
      return `${value}${separator}download=1`;
    }
  };
  return <Show when={props.image()}>
    <div class="kbsp-image-preview" role="dialog" aria-modal="true" onClick={() => props.setImage(null)}>
      <figure class="kbsp-image-preview-panel" onClick={(event) => event.stopPropagation()}>
        <figcaption>
          <strong>{label()}</strong>
          <div>
            <button type="button" title="Zoom Out" aria-label="Zoom Out" onClick={() => setZoom((value) => Math.max(0.25, Number((value - 0.25).toFixed(2))))}>-</button>
            <span>{Math.round(zoom() * 100)}%</span>
            <button type="button" title="Zoom In" aria-label="Zoom In" onClick={() => setZoom((value) => Math.min(4, Number((value + 0.25).toFixed(2))))}>+</button>
            <button type="button" title="Reset Zoom" aria-label="Reset Zoom" onClick={() => setZoom(1)}>100%</button>
            <a href={downloadHref()} download={downloadName()} title="Save Image" aria-label="Save Image">↓</a>
            <button type="button" title="Close" aria-label="Close" onClick={() => props.setImage(null)}><XIcon /></button>
          </div>
        </figcaption>
        <div class="kbsp-image-preview-body"><img src={src()} alt="Storyboard image" style={{ width: `${Math.round(360 * zoom())}px` }} /></div>
      </figure>
    </div>
  </Show>;
}
