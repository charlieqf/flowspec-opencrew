import { For, Show, createSignal } from "solid-js";
import { formatTimelineMs } from "./timelineModel.js";

function importSucceeded(message) {
  return String(message || "").includes("已导入目标 StoryBoard");
}

export default function EditorClipPanel(props) {
  const [editingClip, setEditingClip] = createSignal(null);
  const [draftName, setDraftName] = createSignal("");
  const [draftTags, setDraftTags] = createSignal("");
  const openMetadata = (clip) => {
    setEditingClip(clip);
    setDraftName(clip.displayName);
    setDraftTags((clip.tags || []).join("，"));
  };
  const saveMetadata = async () => {
    const clip = editingClip();
    if (!clip) return;
    const tags = draftTags().split(/[，,]/).map((value) => value.trim()).filter(Boolean);
    const updated = await props.onUpdateClip?.(clip, {
      display_name: draftName(),
      tags,
      search_eligible: true,
    });
    if (updated !== false) setEditingClip(null);
  };
  return <section class="ml-editor-side-section" aria-label="派生片段">
    <header>
      <div>
        <h3>派生片段</h3>
        <p>创建任务异步运行；完成后可预览、下载、导入或删除。</p>
      </div>
    </header>
    <Show when={props.job}>
      <article class={`ml-editor-job ${props.job.status}`}>
        <div><strong>{jobStatusLabel(props.job.status)}</strong><span>{Math.max(0, Math.min(100, Number(props.job.progress) || 0))}%</span></div>
        <progress value={Math.max(0, Math.min(100, Number(props.job.progress) || 0))} max="100" />
        <Show when={props.job.error}><p>{typeof props.job.error === "string" ? props.job.error : JSON.stringify(props.job.error)}</p></Show>
        <Show when={["queued", "running"].includes(props.job.status)}>
          <button type="button" disabled={!props.mutationsEnabled || props.actionBusy} onClick={() => props.onCancelJob?.()}>取消剪切</button>
        </Show>
      </article>
    </Show>
    <Show when={props.error}><div
      class={importSucceeded(props.error) ? "ml-editor-inline-success" : "ml-editor-inline-error"}
      role="status"
    >{props.error}</div></Show>
    <div class="ml-editor-clip-list">
      <For each={props.clips} fallback={<p class="ml-editor-empty">尚无成功派生片段。</p>}>{(clip) =>
        <article class="ml-editor-clip">
          <div>
            <h4>{clip.displayName}</h4>
            <p>{formatTimelineMs(clip.startMs)}–{formatTimelineMs(clip.endMs)} · {formatTimelineMs(clip.durationMs)}</p>
            <Show when={props.sourceArchived} fallback={
              <p class={clip.searchEligible ? "ml-editor-clip-search-state is-enabled" : "ml-editor-clip-search-state"}>
                {clip.searchEligible ? "已可在 StoryBoard 素材检索中复用" : "仅在当前原视频剪辑页可见"}
              </p>
            }><p class="ml-editor-clip-search-state is-warning">来源素材已归档，当前不参与检索</p></Show>
            <Show when={clip.tags?.length}><p>标签：{clip.tags.join("、")}</p></Show>
          </div>
          <div class="ml-editor-clip-actions">
            <Show when={clip.previewUrl}><a href={clip.previewUrl} target="_blank" rel="noreferrer">预览</a></Show>
            <Show when={clip.downloadUrl}><a href={clip.downloadUrl} download>下载</a></Show>
            <button type="button" disabled={!props.mutationsEnabled || !props.targetTaskId || props.actionBusy} onClick={() => props.onImportClip?.(clip)}>导入 StoryBoard</button>
            <Show when={props.clipSearchEnabled}>
              <Show when={clip.searchEligible} fallback={
                <button type="button" disabled={props.sourceArchived || props.actionBusy} onClick={() => openMetadata(clip)}>加入全局素材检索</button>
              }>
                <button type="button" disabled={props.actionBusy} onClick={() => props.onUpdateClip?.(clip, { search_eligible: false })}>移除全局素材检索</button>
                <button type="button" disabled={props.sourceArchived || props.actionBusy} onClick={() => openMetadata(clip)}>编辑检索名称与标签</button>
              </Show>
            </Show>
            <button type="button" class="danger" disabled={!props.mutationsEnabled || props.actionBusy} onClick={() => props.onDeleteClip?.(clip)}>删除</button>
          </div>
        </article>
      }</For>
    </div>
    <Show when={editingClip()}>
      <div class="ml-editor-clip-dialog-backdrop" onClick={() => setEditingClip(null)} />
      <section class="ml-editor-clip-dialog" role="dialog" aria-modal="true" aria-label="编辑派生片段检索信息">
        <header><strong>加入全局素材检索</strong><button type="button" aria-label="关闭" onClick={() => setEditingClip(null)}>×</button></header>
        <p>名称和标签将用于以后检索，请使用能描述片段内容的名称。</p>
        <label><span>片段名称</span><input maxlength="120" value={draftName()} onInput={(event) => setDraftName(event.currentTarget.value)} /></label>
        <label><span>标签（最多 10 项，用逗号分隔）</span><textarea rows="3" value={draftTags()} onInput={(event) => setDraftTags(event.currentTarget.value)} /></label>
        <small>“原文件名 + 片段”一类泛化名称可发现性较低。加入后将在当前部署中全局可见。</small>
        <footer><button type="button" onClick={() => setEditingClip(null)}>取消</button><button class="ml-editor-primary-button" type="button" disabled={props.actionBusy || !draftName().trim()} onClick={() => void saveMetadata()}>保存并加入全局素材检索</button></footer>
      </section>
    </Show>
  </section>;
}

function jobStatusLabel(status) {
  return {
    queued: "已排队",
    running: "正在剪切",
    completed: "剪切完成",
    failed: "剪切失败",
    cancelled: "已取消",
  }[status] || status;
}
