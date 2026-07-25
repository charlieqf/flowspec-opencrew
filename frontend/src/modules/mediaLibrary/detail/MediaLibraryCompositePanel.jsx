import { Show } from "solid-js";
import {
  compositeRunState,
} from "./mediaLibraryDetailModel.js";

export default function MediaLibraryCompositePanel(props) {
  const openCut = () => props.openCut || {};
  const currentRun = () => props.current?.run || null;
  const runState = () => compositeRunState(openCut());
  const featureEnabled = () => props.featureEnabled !== false;
  const message = () => props.runError
    || props.currentLoadError
    || openCut()?.schemes?.composite?.error
    || currentRun()?.error
    || "";

  return <section class="media-library-composite-control" aria-label="综合分析运行控制">
    <Show when={runState().prerequisiteMessage}>
      <div class="media-library-composite-gate"><strong>运行条件未满足</strong><span>{runState().prerequisiteMessage}</span></div>
    </Show>
    <Show when={currentRun()?.scheme === "composite"}>
      <div class="media-library-visual-model-public">
        <Show when={currentRun().modelAlias}><span>模型别名 <strong>{currentRun().modelAlias}</strong></span></Show>
        <Show when={currentRun().modelVersion}><span>结果版本 <strong>{currentRun().modelVersion}</strong></span></Show>
      </div>
    </Show>
    <Show when={message()}>
      <div class="media-library-composite-message" role="status">{message()}</div>
    </Show>
    <div class="media-library-composite-action">
      <p>综合分析只读取当前对白、画面结构和视觉语义的已发布结果，不读取源视频、音频或四帧采样图片文件。</p>
      <button
        type="button"
        disabled={!featureEnabled() || !runState().runnable || props.runBusy}
        title={!featureEnabled() ? "综合分析功能当前已关闭；已发布结果仍可查看" : runState().disabledReason || runState().label}
        onClick={props.onRun}
      >
        {props.runBusy ? "正在提交…" : runState().label}
      </button>
      <Show when={!featureEnabled()}>
        <small>综合分析新运行已关闭；历史发布结果保持只读可见。</small>
      </Show>
    </div>
  </section>;
}
