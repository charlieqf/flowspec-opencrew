import { Show } from "solid-js";
import {
  openCutStatusMeta,
  visualSemanticRunState,
} from "./mediaLibraryDetailModel.js";

export default function MediaLibraryVisualSemanticPanel(props) {
  const visual = () => props.visual || {};
  const currentRun = () => props.current?.run || null;
  const structureStatus = () => openCutStatusMeta(visual().structureStatus);
  const semanticStatus = () => openCutStatusMeta(visual().semanticStatus);
  const runState = () => visualSemanticRunState(
    visual(),
    props.allowCloudVisualDataTransfer,
  );
  const featureEnabled = () => props.featureEnabled !== false;
  const message = () => props.runError
    || props.currentLoadError
    || visual().semanticError
    || currentRun()?.error
    || "";

  return <section class="media-library-visual-semantic-control" aria-label="视觉语义运行控制">
    <div class="media-library-visual-stage-statuses">
      <div>
        <span>画面结构</span>
        <strong class={`media-library-status ${structureStatus().tone}`}>{structureStatus().label}</strong>
        <small>镜头切分与每片段四帧固定采样</small>
      </div>
      <div>
        <span>视觉语义</span>
        <strong class={`media-library-status ${semanticStatus().tone}`}>{semanticStatus().label}</strong>
        <small>每片段一次请求读取四张已发布采样图，不读取源视频</small>
      </div>
    </div>
    <Show when={currentRun()?.scheme === "visual_semantic"}>
      <div class="media-library-visual-model-public">
        <Show when={currentRun().modelAlias}><span>模型别名 <strong>{currentRun().modelAlias}</strong></span></Show>
        <Show when={currentRun().modelVersion}><span>结果版本 <strong>{currentRun().modelVersion}</strong></span></Show>
      </div>
    </Show>
    <Show when={message()}>
      <div class="media-library-visual-semantic-message" role="status">{message()}</div>
    </Show>
    <div class="media-library-visual-semantic-action">
      <label>
        <input
          type="checkbox"
          checked={Boolean(props.allowCloudVisualDataTransfer)}
          disabled={!featureEnabled() || runState().active || props.runBusy}
          onChange={(event) => props.onAllowCloudVisualDataTransferChange?.(event.currentTarget.checked)}
        />
        <span>
          <strong>允许本次分析向已配置的云端视觉模型发送每片段四张采样图</strong>
          <small>授权仅用于本次视觉语义分析；每片段四图在一次请求中发送，不上传整段视频。未授权不会外发图像，本地模型也不会因此产生云传输。</small>
        </span>
      </label>
      <button
        type="button"
        disabled={!featureEnabled() || !runState().runnable || props.runBusy}
        title={!featureEnabled() ? "视觉语义功能当前已关闭；已发布结果仍可查看" : runState().disabledReason || runState().label}
        onClick={props.onRun}
      >
        {props.runBusy ? "正在提交…" : runState().label}
      </button>
      <Show when={!featureEnabled()}>
        <small>视觉语义新运行已关闭；历史发布结果保持只读可见。</small>
      </Show>
    </div>
  </section>;
}
