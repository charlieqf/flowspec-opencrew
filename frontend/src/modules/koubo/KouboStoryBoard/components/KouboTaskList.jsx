import { For, Show } from "solid-js";

export default function KouboTaskList(props) {
  return <section class="kbsp-home">
    <Show when={props.items().length} fallback={<div class="kbsp-empty">还没有可用的故事版（口播）。请先在视频分析（口播）中生成 SessionOutput/storyboard/srt_storyboard.json。</div>}>
      <div class="kbsp-task-list">
        <For each={props.items()}>{(item) => <article class="kbsp-task-card" onClick={() => { window.location.hash = `#/koubo-storyboard/tasks/${item.task.id}`; }}>
          <strong>Task #{item.task.id}</strong>
          <span>故事版（口播）</span>
          <p>Session #{item.task.session_id} · {item.meta.has_saved_edit ? "已保存编辑" : "来自 Analysis V1 输出"}</p>
        </article>}</For>
      </div>
    </Show>
  </section>;
}
