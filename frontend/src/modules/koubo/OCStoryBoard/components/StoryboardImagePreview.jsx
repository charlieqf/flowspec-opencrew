import { Show, createEffect, createSignal, onCleanup } from "solid-js";
import { DownloadIcon, XIcon, ZoomInIcon, ZoomOutIcon } from "../storyboardIcons.jsx";

const clampZoom = (value) => Math.min(4, Math.max(0.25, Number(value.toFixed(2))));

function downloadName(image) {
  const label = image?.label || image?.filename || "storyboard-image";
  const clean = String(label).split("/").pop().replace(/[^\w.\-\u4e00-\u9fa5]+/g, "_");
  return clean || "storyboard-image";
}

function downloadUrl(href) {
  const value = String(href || "").trim();
  if (!value) return "";
  try {
    const url = new URL(value, window.location.href);
    url.searchParams.set("download", "1");
    return url.toString();
  } catch {
    const separator = value.includes("?") ? "&" : "?";
    return `${value}${separator}download=1`;
  }
}

export function StoryboardImagePreview(props) {
  const [zoom, setZoom] = createSignal(1);
  const [naturalSize, setNaturalSize] = createSignal({ width: 0, height: 0 });

  createEffect(() => {
    if (!props.image?.()) return;
    setZoom(1);
    setNaturalSize({ width: 0, height: 0 });
  });

  const close = () => props.onClose?.();
  const changeZoom = (delta) => setZoom((value) => clampZoom(value + delta));
  const imageStyle = () => {
    const size = naturalSize();
    if (!size.width || !size.height) return {};
    return {
      width: `${Math.max(1, Math.round(size.width * zoom()))}px`,
      height: `${Math.max(1, Math.round(size.height * zoom()))}px`,
    };
  };

  const onKeyDown = (event) => {
    if (event.key === "Escape" && props.image?.()) close();
  };
  window.addEventListener("keydown", onKeyDown);
  onCleanup(() => window.removeEventListener("keydown", onKeyDown));

  return <Show when={props.image?.()}>
    {(image) => <div class="ocsb-image-preview" role="dialog" aria-modal="true" onClick={close}>
      <figure class="ocsb-image-preview-panel" onClick={(event) => event.stopPropagation()}>
        <figcaption>
          <strong>{image().label || image().filename || "Image"}</strong>
          <div>
            <button type="button" title="Zoom Out" aria-label="Zoom Out" onClick={() => changeZoom(-0.25)}><ZoomOutIcon /></button>
            <span>{Math.round(zoom() * 100)}%</span>
            <button type="button" title="Zoom In" aria-label="Zoom In" onClick={() => changeZoom(0.25)}><ZoomInIcon /></button>
            <button type="button" title="Reset Zoom" aria-label="Reset Zoom" onClick={() => setZoom(1)}>100%</button>
            <a href={downloadUrl(image().src)} download={downloadName(image())} title="Save Image" aria-label="Save Image"><DownloadIcon /></a>
            <button type="button" title="Close" aria-label="Close" onClick={close}><XIcon /></button>
          </div>
        </figcaption>
        <div class="ocsb-image-preview-body">
          <img
            src={image().src}
            alt={image().label || image().filename || "Storyboard image"}
            style={imageStyle()}
            onLoad={(event) => setNaturalSize({ width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight })}
          />
        </div>
      </figure>
    </div>}
  </Show>;
}
