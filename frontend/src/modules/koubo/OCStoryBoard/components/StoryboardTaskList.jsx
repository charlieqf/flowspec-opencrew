import { For, Show } from "solid-js";
import { XIcon } from "../storyboardIcons.jsx";

export function StoryboardTaskList(props) {
  return <section class="ocstoryboard-home">
    <Show when={props.items().length} fallback={<div class="ocstoryboard-empty">还没有 StoryBoard Task。可以从 OC-Rebuild Shot Plan 进入，或新建空白 StoryBoard。</div>}>
      <div class="ocstoryboard-task-list">
        <For each={props.items()}>{(item) => <article class="ocstoryboard-task-card" onClick={() => { window.location.hash = `#/ocstoryboard/tasks/${item.task.id}`; }}>
          <button
            class="ocstoryboard-task-delete"
            type="button"
            title={`Delete Task #${item.task.id}`}
            aria-label={`Delete Task #${item.task.id}`}
            disabled={props.busy() === `delete-${item.task.id}`}
            onClick={(event) => void props.onDelete(event, item)}
          >
            <XIcon />
          </button>
          <strong>Task #{item.task.id}</strong>
          <span>{item.meta.source_type === "blank_upload" ? "Blank Upload" : `Copy from Task #${item.meta.copied_from_rebuild_task_id || "-"}`}</span>
          <p>Session #{item.task.session_id} · {item.task.status}</p>
        </article>}</For>
      </div>
    </Show>
  </section>;
}
