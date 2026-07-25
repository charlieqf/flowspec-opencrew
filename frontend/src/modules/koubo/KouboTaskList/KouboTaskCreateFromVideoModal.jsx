import { Show } from "solid-js";

export default function KouboTaskCreateFromVideoModal(props) {
  return (
    <Show when={props.open()}>
      <div class="koubo-task-list-modal-backdrop" onClick={props.onClose} />
      <section class="koubo-task-list-modal koubo-task-list-video-modal">
        <header>
          <div>
            <h3>视频分析创建</h3>
          </div>
        </header>
        <footer class="koubo-task-list-video-actions">
          <button type="button" class="secondary" onClick={props.onClose}>关闭</button>
          <button type="button" disabled={props.busy()} onClick={props.onCreate}>{props.busy() ? "创建中..." : "创建视频任务"}</button>
        </footer>
      </section>
    </Show>
  );
}
