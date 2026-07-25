import { For, Show } from "solid-js";
import { VideoLibraryGlyph } from "../components/MediaLibraryIcons.jsx";
import { formatFragmentTimeMs, openCutSchemeById, usabilityMeta } from "./mediaLibraryDetailModel.js";

function FragmentCopy(props) {
  return <>
    <strong>{props.item.title}</strong>
    <Show when={props.schemeId === "dialogue"}><p>{props.item.dialogue || props.item.summary || "-"}</p></Show>
    <Show when={props.schemeId === "visual"}><p>{props.item.visualSummary || props.item.summary || "场景切分片段"}</p></Show>
    <Show when={props.schemeId === "composite"}><p>{props.item.dialogue || props.item.summary || "-"}</p><small>{props.item.visualSummary || "暂无画面摘要"}</small></Show>
  </>;
}

export default function MediaLibraryFragmentList(props) {
  const scheme = () => openCutSchemeById(props.schemeId);
  const noAudio = () => props.schemeId === "dialogue" && props.errorCode === "video_has_no_audio";
  return <section class="media-library-fragment-panel">
    <div class="media-library-fragment-head"><div><h3>片段列表</h3><p>{scheme().label} · 结果只读</p></div><span>共 {props.items.length} 条</span></div>
    <Show when={props.error}><div class={`media-library-fragment-error ${noAudio() ? "is-unavailable" : ""}`}><strong>{noAudio() ? "对白分析不可用" : "分析结果读取失败"}</strong><p>{props.error}</p><Show when={noAudio()}><small>你仍可以切换到“画面分析”，并继续使用视频剪辑；完成合格的四帧画面分析后可按视觉描述检索。</small></Show></div></Show>
    <Show when={props.items.length} fallback={<div class="media-library-fragment-empty"><VideoLibraryGlyph /><strong>{noAudio() ? "此素材没有可识别对白" : scheme().emptyTitle}</strong><p>{noAudio() ? "无需重复运行对白分析；请切换到画面分析，或继续使用视频剪辑。" : scheme().emptyDescription}</p></div>}>
      <div class="media-library-fragment-list" role="listbox" aria-label={`${scheme().label}片段列表`}>
        <For each={props.items}>{(item) => {
          const quality = () => usabilityMeta(item.usability);
          return <button type="button" role="option" aria-selected={props.selectedId === item.id} class={`media-library-fragment-row ${props.selectedId === item.id ? "is-active" : ""}`} onClick={() => props.onSelect(item.id)}>
            <span class="media-library-fragment-index">{item.index}</span>
            <div class="media-library-fragment-copy"><FragmentCopy item={item} schemeId={props.schemeId} /></div>
            <em>{formatFragmentTimeMs(item.startMs)} - {formatFragmentTimeMs(item.endMs)}</em>
            <span class={`media-library-status ${quality().tone}`}>{quality().label}</span>
          </button>;
        }}</For>
      </div>
    </Show>
  </section>;
}
