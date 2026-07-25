import { Show, createSignal } from "solid-js";
import { audioStatusMeta, formatMediaDate, formatMediaDuration, formatMediaSize, mediaOrientation, subtitleModeLabel, visualSearchStatusMeta } from "../mediaLibraryModel.js";

async function copyTechnicalValue(value) {
  const normalized = String(value || "").trim();
  if (!normalized || !globalThis.navigator?.clipboard?.writeText) return false;
  try {
    await globalThis.navigator.clipboard.writeText(normalized);
    return true;
  } catch {
    return false;
  }
}

function TechnicalValue(props) {
  const [copyState, setCopyState] = createSignal("idle");
  const copy = async () => {
    setCopyState(await copyTechnicalValue(props.value) ? "copied" : "failed");
  };
  return <div>
    <dt>{props.label}</dt>
    <dd>
      <code>{props.value}</code>
      <button type="button" aria-label={`复制${props.label}`} title={copyState() === "failed" ? "复制失败，请手动选择文本" : `复制${props.label}`} onClick={copy}>
        {copyState() === "copied" ? "已复制" : copyState() === "failed" ? "复制失败" : "复制"}
      </button>
    </dd>
  </div>;
}

export default function MediaLibraryAssetInfo(props) {
  const audioStatus = () => audioStatusMeta(props.asset);
  const visualSearchStatus = () => visualSearchStatusMeta(props.asset);
  const technicalValues = () => [
    ["素材标识", props.asset.assetId],
    ["内容版本", props.asset.sourceVersion],
    ["分析任务", props.openCut?.taskId],
    ["分析会话", props.openCut?.sessionId],
    ["画面结构运行", props.openCut?.schemes?.visual?.structureRunId],
    ["视觉语义运行", props.visualRun?.id || props.openCut?.schemes?.visual?.semanticRunId],
    ["综合分析运行", props.compositeRun?.id],
  ].filter(([, value]) => String(value ?? "").trim());

  return <Show when={props.expanded}><section class="media-library-asset-info" aria-label="素材信息">
    <dl>
      <div><dt>时长</dt><dd>{formatMediaDuration(props.asset.durationMs)}</dd></div>
      <div><dt>画面</dt><dd>{props.asset.width && props.asset.height ? `${props.asset.width} × ${props.asset.height}` : "-"}</dd></div>
      <div><dt>方向</dt><dd>{mediaOrientation(props.asset) === "portrait" ? "竖屏" : "横屏"}</dd></div>
      <div><dt>格式</dt><dd>{props.asset.format || "-"}</dd></div>
      <div><dt>文件大小</dt><dd>{formatMediaSize(props.asset.sizeBytes)}</dd></div>
      <div><dt>字幕</dt><dd>{subtitleModeLabel(props.asset.subtitleMode)}</dd></div>
      <Show when={audioStatus()}>{(meta) => <div><dt>音轨</dt><dd><span class={`media-library-status ${meta().tone}`}>{meta().label}</span></dd></div>}</Show>
      <Show when={visualSearchStatus()}>{(meta) => <div><dt>画面检索</dt><dd><span class={`media-library-status ${meta().tone}`}>{meta().label}</span></dd></div>}</Show>
      <div><dt>语言</dt><dd>{props.asset.language || "-"}</dd></div>
      <div><dt>更新时间</dt><dd>{formatMediaDate(props.asset.updatedAt)}</dd></div>
    </dl>
    <Show when={technicalValues().length}>
      <details class="media-library-technical-details">
        <summary>技术详情</summary>
        <p>用于问题排查和结果核对，日常使用无需关注。</p>
        <dl>
          {technicalValues().map(([label, value]) => <TechnicalValue label={label} value={value} />)}
        </dl>
      </details>
    </Show>
  </section></Show>;
}
