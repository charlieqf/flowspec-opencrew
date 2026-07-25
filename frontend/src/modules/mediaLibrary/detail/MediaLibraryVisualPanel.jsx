import { For, Show, createEffect, createSignal } from "solid-js";
import { KeyframeIcon, VideoLibraryGlyph } from "../components/MediaLibraryIcons.jsx";
import {
  actionEvidenceLabel,
  evidenceClaimLabel,
  formatFragmentTimeMs,
  samplingStrategyLabel,
  usabilityMeta,
} from "./mediaLibraryDetailModel.js";

export default function MediaLibraryVisualPanel(props) {
  let videoEl;
  const [selectedFrameId, setSelectedFrameId] = createSignal("");
  const keyframes = () => props.item?.keyframes || [];
  const selectedFrame = () => keyframes().find((frame) => frame.id === selectedFrameId()) || keyframes()[0] || null;

  createEffect(() => {
    const item = props.item;
    setSelectedFrameId(item?.keyframes?.[0]?.id || "");
    if (videoEl && Number.isFinite(Number(item?.startMs))) videoEl.currentTime = Number(item.startMs) / 1000;
  });

  const seekFrame = (frame) => {
    setSelectedFrameId(frame.id);
    if (videoEl && Number.isFinite(Number(frame.timeMs))) videoEl.currentTime = Number(frame.timeMs) / 1000;
  };

  const stopAtEnd = () => {
    const endMs = Number(props.item?.endMs);
    if (videoEl && Number.isFinite(endMs) && videoEl.currentTime * 1000 >= endMs) videoEl.pause();
  };

  const quality = () => usabilityMeta(props.item?.usability);
  const videoUrl = () => props.item?.videoUrl || props.asset.previewUrl;
  const evidenceEntries = () => Object.entries(
    props.schemeId === "composite"
      ? props.item?.visualClaimRefs || {}
      : props.item?.claimEvidence || {},
  );
  const evidenceRefs = () => Array.from(new Set([
    ...(props.item?.keyframeRefs || []),
    ...(props.item?.dialogueRefs || []),
    ...(props.item?.visualRefs || []),
    ...evidenceEntries().flatMap(([, refs]) => refs),
  ]));
  const semanticResult = () => ["visual_semantic", "composite"].includes(props.semanticRun?.scheme)
    || props.schemeId === "composite";
  const resultTitle = () => props.schemeId === "composite"
    ? "综合语义"
    : semanticResult()
      ? "视觉语义"
      : "画面结构";
  return <aside class="media-library-visual-panel" aria-label="片段视觉证据与语义详情" tabIndex={0}>
    <div class="media-library-visual-head">
      <div><h3>{props.item?.title || "视觉预览"}</h3><p>{props.item ? `${formatFragmentTimeMs(props.item.startMs)} - ${formatFragmentTimeMs(props.item.endMs)}` : "选择片段后查看对应证据"}</p></div>
      <Show when={props.item}><span class={`media-library-status ${quality().tone}`}>{quality().label}</span></Show>
    </div>
    <div class={`media-library-detail-video is-${props.orientation}`}>
      <Show when={videoUrl()} fallback={<div class="media-library-detail-video-empty"><VideoLibraryGlyph /><span>暂无可播放视频</span></div>}>
        <video ref={videoEl} src={videoUrl()} poster={props.asset.thumbnailUrl || undefined} controls preload="metadata" onTimeUpdate={stopAtEnd} />
      </Show>
    </div>
    <section class="media-library-keyframes">
      <header><div><KeyframeIcon /><strong>四帧采样证据</strong></div><span>{keyframes().length} 张</span></header>
      <Show when={keyframes().length} fallback={<div class="media-library-visual-empty">当前片段还没有四帧采样证据</div>}>
        <div class="media-library-keyframe-strip"><For each={keyframes()}>{(frame) => <button type="button" class={selectedFrame()?.id === frame.id ? "is-active" : ""} onClick={() => seekFrame(frame)}><Show when={frame.imageUrl}><img src={frame.imageUrl} alt="" /></Show><span>{formatFragmentTimeMs(frame.timeMs)}</span></button>}</For></div>
        <Show when={selectedFrame()?.imageUrl}><img class="media-library-keyframe-main" src={selectedFrame().imageUrl} alt="当前采样画面" /></Show>
      </Show>
      <Show when={evidenceRefs().length}>
        <div class="media-library-evidence-refs"><strong>证据引用</strong><div><For each={evidenceRefs()}>{(ref) => <code>{ref}</code>}</For></div></div>
      </Show>
    </section>
    <section class="media-library-visual-description">
      <h4>{resultTitle()}</h4>
      <Show when={semanticResult()} fallback={<p>当前只展示场景切分与四帧采样证据；这不代表画面语义描述已经完成。</p>}>
        <p>{props.schemeId === "composite"
          ? props.item?.summary || "当前综合片段未提供可校验摘要。"
          : props.item?.visualSummary || "当前场景未提供可校验的画面摘要。"}</p>
        <Show when={props.schemeId === "composite" && props.item?.dialogue}>
          <blockquote class="media-library-composite-dialogue">{props.item.dialogue}</blockquote>
        </Show>
        <Show when={props.schemeId !== "composite"}>
          <div class="media-library-sampling-strategy"><span>采样策略</span><strong>{samplingStrategyLabel(props.item?.samplingStrategy || props.semanticRun?.samplingStrategy)}</strong></div>
        </Show>
        <dl>
          <div><dt>人物</dt><dd>{props.item?.people?.join("、") || "未识别"}</dd></div>
          <div><dt>场景</dt><dd>{props.item?.scene || "未识别"}</dd></div>
          <div class="is-wide"><dt>动作</dt><dd>{actionEvidenceLabel({
            ...props.item,
            samplingStrategy: props.item?.samplingStrategy || props.semanticRun?.samplingStrategy,
          })}</dd></div>
          <div><dt>物体</dt><dd>{props.item?.objects?.join("、") || "未识别"}</dd></div>
          <div><dt>置信度</dt><dd>{Number.isFinite(props.item?.confidence) ? props.item.confidence.toFixed(2) : "未提供"}</dd></div>
        </dl>
        <Show when={evidenceEntries().length}>
          <div class="media-library-claim-evidence"><strong>内容依据</strong><For each={evidenceEntries()}>{([claim, refs]) => <div><span>{evidenceClaimLabel(claim)}</span><p>{refs.length ? refs.join("、") : props.schemeId === "composite" ? "暂无可核验的画面依据" : "暂无可核验的四帧证据"}</p></div>}</For></div>
        </Show>
        <Show when={props.schemeId === "composite" && props.item?.boundaryReasons?.length}>
          <div class="media-library-boundary-reasons"><strong>边界依据</strong><p>{props.item.boundaryReasons.join("；")}</p></div>
        </Show>
      </Show>
    </section>
  </aside>;
}
