import { Show } from "solid-js";
import {
  analysisStatusMeta,
  audioStatusMeta,
  formatMediaDate,
  formatMediaDuration,
  formatMediaSize,
  mediaOrientation,
  subtitleModeLabel,
  visualSearchStatusMeta,
} from "../mediaLibraryModel.js";
import { AssetTags } from "./MediaLibraryAssetPrimitives.jsx";
import { CloseIcon, VideoLibraryGlyph } from "./MediaLibraryIcons.jsx";

export default function MediaPreviewDrawer(props) {
  const asset = () => props.asset;
  const status = () => analysisStatusMeta(asset()?.analysisStatus, asset()?.analysisStatusReason);
  const audioStatus = () => audioStatusMeta(asset());
  const visualSearchStatus = () => visualSearchStatusMeta(asset());
  return (
    <Show when={asset()}>
      <div class="media-library-drawer-backdrop" onClick={props.onClose} />
      <aside class="media-library-preview-drawer" aria-label="素材快速预览">
        <header><span>素材预览</span><button type="button" title="关闭预览" aria-label="关闭预览" onClick={props.onClose}><CloseIcon /></button></header>
        <div class={`media-library-preview-stage is-${mediaOrientation(asset())}`}>
          <Show when={asset().previewUrl} fallback={<div class="media-library-preview-placeholder"><VideoLibraryGlyph /><span>暂无可播放预览</span></div>}><video src={asset().previewUrl} controls preload="metadata" poster={asset().thumbnailUrl || undefined} /></Show>
        </div>
        <div class="media-library-drawer-body">
          <div class="media-library-drawer-actions"><a class="primary" href={`#/media-library/${encodeURIComponent(asset().assetId)}`}>打开素材详情</a></div>
          <dl class="media-library-detail-list compact">
            <div><dt>分析状态</dt><dd><span class={`media-library-status ${status().tone}`}>{status().label}</span></dd></div><Show when={audioStatus()}>{(meta) => <div><dt>音轨</dt><dd><span class={`media-library-status ${meta().tone}`}>{meta().label}</span></dd></div>}</Show><Show when={visualSearchStatus()}>{(meta) => <div><dt>画面检索</dt><dd><span class={`media-library-status ${meta().tone}`}>{meta().label}</span></dd></div>}</Show><div><dt>字幕</dt><dd>{subtitleModeLabel(asset().subtitleMode)}</dd></div><div><dt>时长</dt><dd>{formatMediaDuration(asset().durationMs)}</dd></div><div><dt>画面</dt><dd>{asset().width && asset().height ? `${asset().width} × ${asset().height}` : "-"}</dd></div><div><dt>格式</dt><dd>{asset().format || "-"}</dd></div><div><dt>大小</dt><dd>{formatMediaSize(asset().sizeBytes)}</dd></div><div><dt>原始文件</dt><dd title={asset().originalFilename}>{asset().originalFilename || "-"}</dd></div><div><dt>更新时间</dt><dd>{formatMediaDate(asset().updatedAt)}</dd></div>
          </dl>
          <section class="media-library-drawer-section"><h4>对白摘要</h4><p>{asset().dialogueSummary || "尚无对白摘要"}</p></section><section class="media-library-drawer-section"><h4>标签</h4><AssetTags tags={asset().tags} /></section>
        </div>
      </aside>
    </Show>
  );
}
