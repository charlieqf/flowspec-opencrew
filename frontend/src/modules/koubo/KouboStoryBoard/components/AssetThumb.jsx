import { Show, createEffect, createSignal } from "solid-js";
import { kbApi } from "../kouboStoryboardApi.js";
import { assetKind } from "../kouboStoryboardAssets.js";
import { AudioLinesIcon, FilmIcon } from "../kouboStoryboardIcons.jsx";

export default function AssetThumb(props) {
  const src = () => props.asset?.path ? kbApi.rawFileUrl(props.sessionId(), props.asset.path) : "";
  const thumbnailSrc = () => props.asset?.path ? kbApi.thumbnailFileUrl(props.sessionId(), props.asset.path) : "";
  const [thumbnailFailed, setThumbnailFailed] = createSignal(false);
  const kind = () => assetKind(props.asset);
  createEffect(() => {
    thumbnailSrc();
    setThumbnailFailed(false);
  });
  return <Show when={kind() === "video"} fallback={<Show when={kind() === "audio"} fallback={<img src={thumbnailFailed() ? src() : (thumbnailSrc() || src())} loading="lazy" draggable="false" onError={() => setThumbnailFailed(true)} />}><div class="kbsp-asset-audio-thumb"><AudioLinesIcon /></div></Show>}>
    <Show when={thumbnailSrc() && !thumbnailFailed()} fallback={<div class="kbsp-asset-video-thumb"><FilmIcon /></div>}>
      <img src={thumbnailSrc()} loading="lazy" draggable="false" onError={() => setThumbnailFailed(true)} />
    </Show>
  </Show>;
}
