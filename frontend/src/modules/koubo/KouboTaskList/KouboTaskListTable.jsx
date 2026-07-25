import { For, Show } from "solid-js";
import { TrashIcon, WaveformIcon } from "../AnalysisV1/analysisV1Icons.jsx";
import StoryboardIcon from "../shared/StoryboardIcon.jsx";
import { createModeLabel, statusLabel, statusTone } from "./kouboTaskListStatus.js";
import { formatDateTime } from "./kouboTaskListModel.js";

function ArchiveIcon() {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="5" rx="1.5"/><path d="M5 9v9a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V9"/><path d="M12 12v5"/><path d="m9.5 14.5 2.5 2.5 2.5-2.5"/></svg>;
}

function CountIcon(props) {
  const path = () => {
    if (props.kind === "scene") return <><rect x="4" y="5" width="16" height="14" rx="2"/><path d="M8 9h8"/><path d="M8 13h5"/></>;
    if (props.kind === "shot") return <><rect x="4" y="6" width="16" height="12" rx="2"/><path d="M8 6v12"/><path d="M16 6v12"/></>;
    if (props.kind === "audio") return <><path d="M5 10v4"/><path d="M9 7v10"/><path d="M13 9v6"/><path d="M17 6v12"/></>;
    if (props.kind === "image") return <><rect x="4" y="5" width="16" height="14" rx="2"/><circle cx="9" cy="10" r="1.3"/><path d="m7 17 4-4 3 3 2-2 2 3"/></>;
    if (props.kind === "video") return <><rect x="4" y="6" width="16" height="12" rx="2"/><path d="m10 10 5 2-5 2v-4z"/></>;
    return <><path d="M4 5h16v10H8l-4 4V5z"/><path d="M8 9h8"/><path d="M8 12h5"/></>;
  };
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{path()}</svg>;
}

export default function KouboTaskListTable(props) {
  const action = (event, callback) => {
    event.stopPropagation();
    callback();
  };
  const isTalkingHead = (item) => item.createMode === "person_talking_head" || item.profileId === "person_talking_head_v1";
  const primaryTaskUrl = (item) => isTalkingHead(item) ? item.talkingHeadUrl : item.analysisUrl;
  const primaryTaskLabel = (item) => isTalkingHead(item) ? "打开人物口播" : "打开视频分析";
  return (
    <section class="koubo-task-list-table-wrap">
      <Show when={props.items().length} fallback={<div class="koubo-task-list-empty">还没有口播任务。可以从视频导入，也可以直接用脚本创建。</div>}>
        <table class="koubo-task-list-table">
          <thead>
            <tr>
              <th class="koubo-task-list-actions-heading">操作</th>
              <th>任务</th>
              <th class="koubo-task-list-summary-heading">简介</th>
              <th>来源</th>
              <th class="koubo-task-list-status-heading">状态</th>
              <th>规模</th>
              <th>素材</th>
              <th class="koubo-task-list-updated-heading">更新时间</th>
            </tr>
          </thead>
          <tbody>
            <For each={props.items()}>{(item) => (
              <tr class={props.selectedTaskId?.() === item.taskId ? "is-selected" : ""} onClick={() => props.onSelect?.(item)}>
                <td>
                  <div class="koubo-task-list-row-actions">
                    <button type="button" title={primaryTaskLabel(item)} aria-label={primaryTaskLabel(item)} onClick={(event) => action(event, () => { window.location.hash = primaryTaskUrl(item); })}>
                      <WaveformIcon />
                    </button>
                    <button type="button" title="打开故事版" aria-label="打开故事版" onClick={(event) => action(event, () => { window.location.hash = item.storyboardUrl; })}>
                      <StoryboardIcon />
                    </button>
                    <button type="button" title="归档任务" aria-label="归档任务" onClick={(event) => action(event, () => props.onArchive(item))} disabled={item.archived}>
                      <ArchiveIcon />
                    </button>
                    <button type="button" class="danger" title="物理删除任务" aria-label="物理删除任务" onClick={(event) => action(event, () => props.onDelete(item))}>
                      <TrashIcon />
                    </button>
                  </div>
                </td>
                <td>
                  <div class="koubo-task-list-title-cell">
                    <span>Task #{item.taskId} / Session #{item.sessionId}</span>
                  </div>
                </td>
                <td class="koubo-task-list-summary-cell">
                  <p class="koubo-task-list-summary" title={item.taskSummary || ""}>{item.taskSummary || "-"}</p>
                </td>
                <td><span class={`koubo-task-list-mode ${item.createMode}`}>{createModeLabel(item.createMode)}</span></td>
                <td class="koubo-task-list-status-cell"><span class={`koubo-task-list-status ${statusTone(item.status)}`}>{statusLabel(item.status)}</span></td>
                <td>
                  <div class="koubo-task-list-counts">
                    <span class="shot" title="Shot 数"><CountIcon kind="shot" />{item.shotCount}</span>
                    <span class="scene" title="Scene 数"><CountIcon kind="scene" />{item.sceneCount}</span>
                    <span class="dialogue" title="Dialogue 数"><CountIcon kind="dialogue" />{item.dialogueCount}</span>
                  </div>
                </td>
                <td>
                  <div class="koubo-task-list-counts koubo-task-list-asset-counts">
                    <span class="asset-audio" title="音频素材"><CountIcon kind="audio" />{item.audioAssetCount}</span>
                    <span class="asset-image" title="图片素材"><CountIcon kind="image" />{item.imageAssetCount}</span>
                    <span class="asset-video" title="视频素材"><CountIcon kind="video" />{item.videoAssetCount}</span>
                  </div>
                </td>
                <td class="koubo-task-list-updated-cell">{formatDateTime(item.updatedAt)}</td>
              </tr>
            )}</For>
          </tbody>
        </table>
      </Show>
    </section>
  );
}
