import { For, Show, createSignal } from "solid-js";
import {
  editingIssueLabel,
  formatMediaDuration,
  hasMediaQualitySummary,
  mediaOrientation,
} from "../mediaLibraryModel.js";
import { VideoLibraryGlyph } from "./MediaLibraryIcons.jsx";

export function AssetThumbnail(props) {
  const [failed, setFailed] = createSignal(false);
  const orientation = () => mediaOrientation(props.asset);
  return (
    <div class={`media-library-thumbnail is-${orientation()}`}>
      <Show when={props.asset.thumbnailUrl && !failed()} fallback={<div class="media-library-thumbnail-placeholder"><VideoLibraryGlyph /></div>}>
        <img src={props.asset.thumbnailUrl} alt="" loading="lazy" onError={() => setFailed(true)} />
      </Show>
      <span class="media-library-thumbnail-duration">{formatMediaDuration(props.asset.durationMs)}</span>
    </div>
  );
}

export function CountBadge(props) {
  return <span class={`media-library-count-badge ${props.tone || "neutral"}`} title={props.title}>{props.label}<strong>{props.value ?? "-"}</strong></span>;
}

export function QualitySummary(props) {
  const summary = () => props.asset.analysisSummary;
  const available = () => hasMediaQualitySummary(summary());
  return (
    <Show when={available()} fallback={<span class="media-library-dash">暂无质量数据</span>}>
      <div class="media-library-quality-list">
        <Show when={summary().keepCount !== null}><span class="keep">可用 {summary().keepCount}</span></Show>
        <Show when={summary().reviewCount !== null}><span class="review" title="分析已完成，建议人工确认识别准确性">建议复核 {summary().reviewCount}</span></Show>
        <Show when={Number(summary().excludeCount) > 0}><span class="exclude">已过滤 {summary().excludeCount}</span></Show>
        <Show when={summary().editingIssueCount !== null}>
          <small>问题 {summary().editingIssueCount}{summary().topEditingIssue ? ` · ${editingIssueLabel(summary().topEditingIssue)} ${summary().topEditingIssueCount ?? ""}` : ""}</small>
        </Show>
      </div>
    </Show>
  );
}

export function AssetTags(props) {
  return (
    <div class="media-library-tags">
      <For each={props.tags.slice(0, 3)}>{(tag) => <span classList={{ legacy: !tag || tag.length > 32 }}>{tag || "历史空标签"}</span>}</For>
      <Show when={props.tags.length > 3}><span class="more">+{props.tags.length - 3}</span></Show>
      <Show when={!props.tags.length}><span class="media-library-dash">-</span></Show>
    </div>
  );
}
