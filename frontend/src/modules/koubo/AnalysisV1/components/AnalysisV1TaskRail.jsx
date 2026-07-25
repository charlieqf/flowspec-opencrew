import { For, Show } from "solid-js";
import { formatDateTime, statusTone } from "../analysisV1Model";

export default function AnalysisV1TaskRail(props) {
  return <aside class="analysis-v1-task-rail">
    <div class="analysis-v1-rail-head">
      <div>
        <h2>任务</h2>
        <span>{props.tasks.length} 个</span>
      </div>
      <button type="button" class="secondary" onClick={props.onRefresh}>刷新</button>
    </div>
    <Show when={props.tasks.length > 0} fallback={<div class="analysis-v1-empty">暂无任务</div>}>
      <div class="analysis-v1-task-list">
        <For each={props.tasks}>{(task) => (
          <button type="button" class={`analysis-v1-task-item ${props.selectedTaskId === task.id ? "is-active" : ""}`} onClick={() => props.onSelect(task.id)}>
            <strong>任务 #{task.id}</strong>
            <span>{task.title || `会话 #${task.session_id}`}</span>
            <em class={`analysis-v1-status is-${statusTone(task.status)}`}>{task.status || "draft"}</em>
            <small>{formatDateTime(task.updated_at)}</small>
          </button>
        )}</For>
      </div>
    </Show>
  </aside>;
}
