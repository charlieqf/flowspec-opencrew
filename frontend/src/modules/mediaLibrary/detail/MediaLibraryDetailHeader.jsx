import { For, Show } from "solid-js";
import { ChevronIcon, CompositeAnalysisIcon, DialogueAnalysisIcon, VideoClipIcon, VisualAnalysisIcon } from "../components/MediaLibraryIcons.jsx";
import { OPEN_CUT_SCHEMES, analysisSchemeStatusMeta, canRunComposite, openCutOverallStatusMeta } from "./mediaLibraryDetailModel.js";

const ICONS = { dialogue: DialogueAnalysisIcon, visual: VisualAnalysisIcon, composite: CompositeAnalysisIcon };

export default function MediaLibraryDetailHeader(props) {
  const taskStatus = () => openCutOverallStatusMeta(props.openCut);
  const compositeReady = () => canRunComposite(props.openCut);
  return <section class="media-library-workbench-head">
    <div class="media-library-workbench-title">
      <div class="media-library-workbench-identity">
        <span class="media-library-step-badge">1</span>
        <h2>素材分析</h2>
        <span class={`media-library-status ${taskStatus().tone}`}>{taskStatus().label}</span>
      </div>
    </div>
    <div class="media-library-workbench-actions">
      <For each={OPEN_CUT_SCHEMES}>{(scheme) => {
        const Icon = ICONS[scheme.id];
        const blocked = () => (
          !props.analysisRunsEnabled
          || (
            scheme.id === "composite"
            && (!props.compositeEnabled || !compositeReady())
          )
        );
        const status = () => analysisSchemeStatusMeta(scheme.id, props.openCut.schemes[scheme.id]);
        return <button type="button" class={`media-library-tool-entry is-${scheme.id}`} disabled={blocked()} title={!props.analysisRunsEnabled ? "分析运行功能当前已关闭" : scheme.id === "composite" && !props.compositeEnabled ? "综合分析功能当前已关闭" : blocked() ? "请先完成对白分析和画面分析" : `打开${scheme.label}工具集`} onClick={() => props.onOpenTool(scheme.id)}>
          <Icon /><span>{scheme.label}</span><small class={status().tone}>{status().label}</small>
        </button>;
      }}</For>
      <Show when={props.editorEntryVisible}>
        <button
          type="button"
          class="media-library-tool-entry is-editor"
          disabled={!props.canOpenEditor}
          title={props.canOpenEditor ? "打开视频剪辑" : "素材需上传完成且具备完整的源视频版本信息"}
          onClick={props.onOpenEditor}
        >
          <VideoClipIcon /><span>视频剪辑</span><small class={props.canOpenEditor ? "success" : "neutral"}>{props.canOpenEditor ? "可用" : "不可用"}</small>
        </button>
      </Show>
      <button type="button" class="media-library-info-toggle" aria-expanded={props.infoExpanded} title={props.infoExpanded ? "收起素材信息" : "展开素材信息"} onClick={props.onToggleInfo}><ChevronIcon expanded={props.infoExpanded} /></button>
    </div>
  </section>;
}
