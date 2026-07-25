import { For, Show } from "solid-js";
import {
  analysisStatusMeta,
  audioStatusMeta,
  formatMediaDate,
  formatMediaDuration,
  formatMediaSize,
  subtitleModeLabel,
  visualSearchStatusMeta,
} from "../mediaLibraryModel.js";
import { AssetThumbnail, CountBadge, QualitySummary } from "./MediaLibraryAssetPrimitives.jsx";
import MediaLibraryAssetActions from "./MediaLibraryAssetActions.jsx";
import { DeleteIcon, MoreIcon, PreviewIcon } from "./MediaLibraryIcons.jsx";

export default function MediaLibraryCard(props) {
  const status = () => analysisStatusMeta(props.asset.analysisStatus, props.asset.analysisStatusReason);
  const audioStatus = () => audioStatusMeta(props.asset);
  const visualSearchStatus = () => visualSearchStatusMeta(props.asset);
  const visibleTags = () => props.asset.tags.slice(0, 4);
  const action = (event, callback) => {
    event.stopPropagation();
    callback();
  };

  return (
    <article class="media-library-card" onClick={() => props.onPreview(props.asset)}>
      <div class="media-library-card-preview">
        <AssetThumbnail asset={props.asset} />
        <button type="button" class="media-library-card-preview-button" aria-label={`预览 ${props.asset.displayName}`} onClick={(event) => action(event, () => props.onPreview(props.asset))}><PreviewIcon /></button>
      </div>
      <div class="media-library-card-body">
        <div class="media-library-card-title-row">
          <div><a href={`#/media-library/${encodeURIComponent(props.asset.assetId)}`} title={props.asset.displayName} onClick={(event) => event.stopPropagation()}>{props.asset.displayName}</a><span>{props.asset.format || "视频"} · {subtitleModeLabel(props.asset.subtitleMode)}</span></div>
          <div class="media-library-more-wrap">
            <button type="button" title="更多操作" aria-label={`${props.asset.displayName} 更多操作`} aria-expanded={props.openMenuId === props.asset.assetId} onClick={(event) => action(event, () => props.onToggleMenu(props.asset.assetId))}><MoreIcon /></button>
            <Show when={props.openMenuId === props.asset.assetId}><MediaLibraryAssetActions asset={props.asset} {...props} /></Show>
          </div>
        </div>
        <div class="media-library-card-media-meta"><strong>{formatMediaDuration(props.asset.durationMs)}</strong><span>{props.asset.width && props.asset.height ? `${props.asset.width} × ${props.asset.height}` : "-"}</span><span>{formatMediaSize(props.asset.sizeBytes)}</span></div>
        <div class="media-library-status-stack"><span class={`media-library-status ${status().tone}`}>{status().label}</span><Show when={audioStatus()}>{(meta) => <span class={`media-library-status ${meta().tone}`}>{meta().label}</span>}</Show><Show when={visualSearchStatus()}>{(meta) => <span class={`media-library-status ${meta().tone}`}>{meta().label}</span>}</Show></div>
        <div class="media-library-card-structure"><CountBadge label="对白" value={props.asset.analysisSummary.dialogueCount} tone="dialogue" /><CountBadge label="视觉" value={props.asset.analysisSummary.visualCount} tone="visual" /><CountBadge label="综合" value={props.asset.analysisSummary.compositeCount} tone="composite" /></div>
        <QualitySummary asset={props.asset} />
        <div class="media-library-card-tags">
          <For each={visibleTags()}>{(tag) => <span classList={{ legacy: !tag || tag.length > 32 }}>{tag || "历史空标签"}</span>}</For>
          <Show when={props.asset.tags.length > 4}><span class="more">+{props.asset.tags.length - 4}</span></Show>
          <Show when={!props.asset.tags.length}><span class="media-library-dash">暂无标签</span></Show>
        </div>
      </div>
      <footer><time>{formatMediaDate(props.asset.updatedAt)}</time><button type="button" class="media-library-delete-button" title="删除素材及 Session" aria-label={`删除素材 ${props.asset.displayName}`} onClick={(event) => action(event, () => props.onDelete(props.asset))}><DeleteIcon /></button></footer>
    </article>
  );
}
