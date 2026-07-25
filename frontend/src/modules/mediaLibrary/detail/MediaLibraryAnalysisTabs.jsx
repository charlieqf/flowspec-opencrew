import { For, Show } from "solid-js";
import { OPEN_CUT_SCHEMES, analysisSchemeStatusMeta, openCutStatusMeta } from "./mediaLibraryDetailModel.js";

export default function MediaLibraryAnalysisTabs(props) {
  return <div class="media-library-analysis-tabs" role="tablist" aria-label="素材分析结果">
    <For each={OPEN_CUT_SCHEMES}>{(scheme) => {
      const result = () => props.openCut.schemes[scheme.id];
      const status = () => analysisSchemeStatusMeta(scheme.id, result());
      const structureStatus = () => openCutStatusMeta(result()?.structureStatus);
      const semanticStatus = () => openCutStatusMeta(result()?.semanticStatus);
      return <button type="button" role="tab" aria-selected={props.activeTab === scheme.id} class={props.activeTab === scheme.id ? "is-active" : ""} onClick={() => props.onChange(scheme.id)}>
        <span>{scheme.label}</span>
        <Show when={scheme.id === "visual"} fallback={<small class={status().tone}>{status().label}</small>}>
          <div class="media-library-tab-substatuses">
            <small class={structureStatus().tone}>结构 {structureStatus().label}</small>
            <small class={semanticStatus().tone}>语义 {semanticStatus().label}</small>
          </div>
        </Show>
        <i>{result()?.count ?? "-"}</i>
      </button>;
    }}</For>
  </div>;
}
