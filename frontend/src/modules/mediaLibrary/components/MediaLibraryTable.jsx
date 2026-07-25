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
import { AssetTags, AssetThumbnail, CountBadge, QualitySummary } from "./MediaLibraryAssetPrimitives.jsx";
import MediaLibraryAssetActions from "./MediaLibraryAssetActions.jsx";
import { DeleteIcon, MoreIcon, PreviewIcon } from "./MediaLibraryIcons.jsx";

export default function MediaLibraryTable(props) {
  const action = (event, callback) => {
    event.stopPropagation();
    callback();
  };
  return (
    <section class="media-library-table-wrap">
      <Show when={props.items.length} fallback={props.emptyFallback}>
        <table class="media-library-table">
          <thead>
            <tr>
              <th class="actions">操作</th><th class="asset">素材</th><th>媒体信息</th><th class="dialogue">对白摘要</th><th>分析状态</th><th>内容结构</th><th>质量</th><th>标签</th><th class="updated">更新时间</th>
            </tr>
          </thead>
          <tbody>
            <For each={props.items}>{(asset) => {
              const status = () => analysisStatusMeta(asset.analysisStatus, asset.analysisStatusReason);
              const audioStatus = () => audioStatusMeta(asset);
              const visualSearchStatus = () => visualSearchStatusMeta(asset);
              return (
                <tr onClick={() => props.onPreview(asset)}>
                  <td>
                    <div class="media-library-row-actions">
                      <button type="button" title="快速预览" aria-label={`预览 ${asset.displayName}`} onClick={(event) => action(event, () => props.onPreview(asset))}><PreviewIcon /></button>
                      <div class="media-library-more-wrap">
                        <button type="button" title="更多操作" aria-label={`${asset.displayName} 更多操作`} aria-expanded={props.openMenuId === asset.assetId} onClick={(event) => action(event, () => props.onToggleMenu(asset.assetId))}><MoreIcon /></button>
                        <Show when={props.openMenuId === asset.assetId}><MediaLibraryAssetActions asset={asset} {...props} /></Show>
                      </div>
                      <button type="button" class="media-library-delete-button" title="删除素材及 Session" aria-label={`删除素材 ${asset.displayName}`} onClick={(event) => action(event, () => props.onDelete(asset))}><DeleteIcon /></button>
                    </div>
                  </td>
                  <td><div class="media-library-asset-cell"><AssetThumbnail asset={asset} /><div class="media-library-asset-copy"><a href={`#/media-library/${encodeURIComponent(asset.assetId)}`} title={asset.displayName} onClick={(event) => event.stopPropagation()}>{asset.displayName}</a><span>{subtitleModeLabel(asset.subtitleMode)}</span></div></div></td>
                  <td><div class="media-library-media-meta"><strong>{formatMediaDuration(asset.durationMs)}</strong><span>{asset.width && asset.height ? `${asset.width} × ${asset.height}` : "-"}</span><small>{[asset.format || "-", formatMediaSize(asset.sizeBytes)].join(" · ")}</small></div></td>
                  <td class="media-library-dialogue-cell"><span class="media-library-language">{asset.language || "-"}</span><p title={asset.dialogueSummary}>{asset.dialogueSummary || "-"}</p></td>
                  <td><div class="media-library-status-stack"><span class={`media-library-status ${status().tone}`}>{status().label}</span><Show when={audioStatus()}>{(meta) => <span class={`media-library-status ${meta().tone}`}>{meta().label}</span>}</Show><Show when={visualSearchStatus()}>{(meta) => <span class={`media-library-status ${meta().tone}`}>{meta().label}</span>}</Show></div></td>
                  <td><div class="media-library-structure-counts"><CountBadge label="对白" value={asset.analysisSummary.dialogueCount} tone="dialogue" /><CountBadge label="视觉" value={asset.analysisSummary.visualCount} tone="visual" /><CountBadge label="综合" value={asset.analysisSummary.compositeCount} tone="composite" /></div></td>
                  <td><QualitySummary asset={asset} /></td><td><AssetTags tags={asset.tags} /></td><td class="media-library-updated">{formatMediaDate(asset.updatedAt)}</td>
                </tr>
              );
            }}</For>
          </tbody>
        </table>
      </Show>
    </section>
  );
}
