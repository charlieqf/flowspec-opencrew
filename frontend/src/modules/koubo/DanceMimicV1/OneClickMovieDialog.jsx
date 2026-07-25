import { For, Show, createEffect, createMemo, createSignal, onCleanup } from "solid-js";
import { Portal } from "solid-js/web";
import { CloseIcon, PlayClipIcon } from "../AnalysisV1/analysisV1Icons.jsx";
import { AudioLinesIcon, ClapperboardIcon, ImageIcon, MicIcon } from "../KouboStoryBoard/kouboStoryboardIcons.jsx";
import StoryboardIcon from "../shared/StoryboardIcon.jsx";

function finiteDurationSeconds(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? Math.max(0, number) : null;
}

function timestampDurationSeconds(startedAt, finishedAt) {
  const started = Number(startedAt || 0);
  const finished = Number(finishedAt || 0);
  if (started > 0 && finished > 0 && finished >= started) return (finished - started) / 1000;
  return null;
}

function formatRunDuration(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "-";
  const totalSeconds = Math.max(0, Math.round(number));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) return `${hours}小时${minutes}分${seconds}秒`;
  if (minutes > 0) return `${minutes}分${seconds}秒`;
  return `${seconds}秒`;
}

function stepDuration(step) {
  const finished = finiteDurationSeconds(step?.duration_seconds);
  if (finished !== null) return formatRunDuration(finished);
  const recorded = timestampDurationSeconds(step?.started_at, step?.finished_at);
  if (recorded !== null) return formatRunDuration(recorded);
  const startedAt = Number(step?.started_at || 0);
  if (String(step?.status || "").toLowerCase() === "running" && startedAt > 0) {
    return formatRunDuration(Math.max(0, (Date.now() - startedAt) / 1000));
  }
  return "-";
}

function stepDurationSeconds(step) {
  const finished = finiteDurationSeconds(step?.duration_seconds);
  if (finished !== null) return finished;
  const recorded = timestampDurationSeconds(step?.started_at, step?.finished_at);
  if (recorded !== null) return recorded;
  const startedAt = Number(step?.started_at || 0);
  if (String(step?.status || "").toLowerCase() === "running" && startedAt > 0) {
    return Math.max(0, (Date.now() - startedAt) / 1000);
  }
  return 0;
}

const STEP_ESTIMATE_SECONDS = {
  "00": 8,
  "01": 20,
  "02": 90,
  "03": 20,
  "05_01": 20,
  "05_02": 240,
  "06_01": 60,
};

function stepEstimatedSeconds(step) {
  const duration = stepDurationSeconds(step);
  if (duration > 0) return duration;
  return STEP_ESTIMATE_SECONDS[String(step?.id || "")] || 30;
}

function weightedStepProgress(steps) {
  const items = Array.isArray(steps) ? steps : [];
  const total = items.reduce((sum, step) => sum + stepEstimatedSeconds(step), 0);
  if (!total) return 0;
  const completed = items.reduce((sum, step) => {
    const status = String(step?.status || "").toLowerCase();
    if (["completed", "ready", "reused", "skipped"].includes(status)) return sum + stepEstimatedSeconds(step);
    if (["running", "queued"].includes(status)) return sum + Math.min(stepDurationSeconds(step), stepEstimatedSeconds(step) * 0.8);
    return sum;
  }, 0);
  return Math.max(0, Math.min(100, Math.round((completed / total) * 100)));
}

function stepStatusLabel(status) {
  const value = String(status || "").toLowerCase();
  return {
    pending: "等待",
    queued: "排队",
    running: "运行中",
    completed: "完成",
    failed: "失败",
    blocked: "阻断",
    skipped: "跳过",
    cancelled: "已取消",
  }[value] || value || "等待";
}

function stepTone(status) {
  const value = String(status || "").toLowerCase();
  if (["completed", "ready", "reused"].includes(value)) return "ready";
  if (["running", "queued"].includes(value)) return "running";
  if (["failed", "blocked", "cancelled", "error"].includes(value)) return "failed";
  return "idle";
}

function taskToneFromStatus(status) {
  const value = String(status || "").toLowerCase();
  if (["completed", "completed_working", "done", "ready", "reused", "existing"].includes(value)) return "done";
  if (["running", "running_copy", "running_generate", "queued", "executing", "processing"].includes(value)) return "running";
  if (["pending", "pending_copy", "pending_generate", "waiting"].includes(value)) return "pending";
  if (["failed", "blocked", "error"].includes(value)) return "failed";
  if (["cancelled", "skipped", "disabled", "skipped_by_cutaway", "skipped_by_bound_video"].includes(value)) return "disabled";
  return "";
}

function segmentStepStatus(segment, stepKey) {
  return String(segment?.steps?.[stepKey]?.status || "").toLowerCase();
}

function taskState(segment, stepKey, fileKey, fallback = {}) {
  const statusTone = taskToneFromStatus(segmentStepStatus(segment, stepKey));
  if (segment?.files?.[fileKey]?.exists) return { tone: "done", title: fallback.doneTitle || "已在 Working" };
  if (statusTone) return { tone: statusTone, title: fallback.title || segmentStepStatus(segment, stepKey) };
  return { tone: fallback.tone || "pending", dot: fallback.dot || "generate", title: fallback.title || "等待执行" };
}

function storyboardItemForSegment(segment, storyboardItems = []) {
  const dialogueIds = Array.isArray(segment?.dialogue_ids) ? segment.dialogue_ids : [];
  return storyboardItems.find((item) => Number(item?.index) === Number(segment?.index))
    || storyboardItems.find((item) => item?.dialogue_asset_key && item.dialogue_asset_key === segment?.asset_key)
    || storyboardItems.find((item) => dialogueIds.includes(item?.dialogue_asset_key) || dialogueIds.includes(item?.srt_id) || dialogueIds.includes(item?.dialogue_id))
    || null;
}

function audioState(segment, storyboardItem = null) {
  if (storyboardItem?.audio?.exists || storyboardItem?.audio?.path) {
    return { tone: "done", title: "音频已匹配" };
  }
  const dialogueAudioTasks = Array.isArray(segment?.dialogue_audio_tasks) ? segment.dialogue_audio_tasks : [];
  if (dialogueAudioTasks.length && dialogueAudioTasks.every((item) => item?.existing_audio_path || item?.planned_audio_path || item?.audio_path)) {
    return { tone: "done", title: "音频已匹配" };
  }
  const lipsync = segment?.lipsync || {};
  if (lipsync.sync_mode === "lipsync" && lipsync.reason === "dialogue_marked_talking_head") {
    return { tone: "done", title: "口播音频已匹配" };
  }
  return taskState(segment, "audio", "audio", { doneTitle: "音频已匹配并在 Working", title: "等待音频匹配" });
}

function text(value) {
  return String(value || "").trim();
}

function frameLabel(segment) {
  const plannedLabel = text(segment?.image_step_label || segment?.first_frame?.image_step_label);
  if (plannedLabel) return plannedLabel;
  const sourceType = text(segment?.first_frame_source_type || segment?.first_frame?.materialize_source_type || segment?.first_frame?.source_type || segment?.first_frame_policy);
  if (sourceType === "previous_segment_tail_frame" || sourceType === "previous_scene_tail_frame" || segment?.image_input_kind === "tail_frame_pending_copy") return "尾帧";
  if (segment?.image_input_kind === "tail_frame_materialized") return "尾帧作为新图";
  return "新图";
}

function frameState(segment) {
  const label = frameLabel(segment);
  if (segment?.files?.first_frame?.exists) {
    return {
      tone: "done",
      title: label.includes("尾帧") ? "上一句尾帧已作为当前首帧" : "首句新图已准备",
    };
  }
  return taskState(segment, "image", "first_frame", {
    tone: "pending",
    dot: "copy",
    title: label.includes("尾帧") ? "等待上一句尾帧作为首帧" : "等待首句新图",
  });
}

function videoState(segment) {
  return taskState(segment, "video", "raw_video", { title: "等待新视频生成" });
}

function syncLabel(segment, props) {
  if (typeof props.syncLabel === "function") return props.syncLabel(segment);
  return props.syncLabel || "音频合成";
}

function syncState(segment, props) {
  return taskState(segment, "sync", "final_video", { title: `等待${syncLabel(segment, props)}` });
}

function segmentDuration(segment, storyboardItems = []) {
  const direct = finiteDurationSeconds(segment?.duration ?? segment?.duration_seconds);
  if (direct !== null) return `${direct.toFixed(2).replace(/\.?0+$/, "")}s`;
  const byIndex = storyboardItemForSegment(segment, storyboardItems);
  const fromStory = finiteDurationSeconds(byIndex?.duration);
  if (fromStory !== null) return `${fromStory.toFixed(2).replace(/\.?0+$/, "")}s`;
  const start = Number(byIndex?.start);
  const end = Number(byIndex?.end);
  if (Number.isFinite(start) && Number.isFinite(end) && end >= start) {
    return `${(end - start).toFixed(2).replace(/\.?0+$/, "")}s`;
  }
  return "-";
}

function TaskBadge(props) {
  const Icon = props.icon;
  const state = () => props.state || { tone: "pending", dot: "generate", title: "" };
  return (
    <div class={`dmv1-movie-task-badge is-${state().tone}`} classList={{ "has-dot": Boolean(state().dot), "has-generate-dot": state().dot === "generate", "has-copy-dot": state().dot === "copy" }} title={state().title}>
      <Icon />
      <span>{props.label}</span>
    </div>
  );
}

function Arrow() {
  return <span class="dmv1-movie-pipe-arrow">→</span>;
}

export default function OneClickMovieDialog(props) {
  const [dialogPosition, setDialogPosition] = createSignal(null);
  const [stepsExpanded, setStepsExpanded] = createSignal(false);
  const [segmentsExpanded, setSegmentsExpanded] = createSignal(false);
  const [stepMenu, setStepMenu] = createSignal(null);
  const steps = () => Array.isArray(props.steps) && props.steps.length ? props.steps : Array.isArray(props.planSteps) ? props.planSteps : [];
  const segments = () => Array.isArray(props.segments) ? props.segments : [];
  const totalDuration = createMemo(() => finiteDurationSeconds(props.totalDurationSeconds) ?? timestampDurationSeconds(props.startedAt, props.finishedAt) ?? steps().reduce((total, step) => total + stepDurationSeconds(step), 0));
  const stepProgressPercent = createMemo(() => weightedStepProgress(steps()));

  createEffect(() => {
    if (props.open) {
      setDialogPosition(null);
      setStepsExpanded(false);
      setSegmentsExpanded(false);
      setStepMenu(null);
    }
  });

  const closeStepMenu = () => setStepMenu(null);
  window.addEventListener("click", closeStepMenu);
  window.addEventListener("blur", closeStepMenu);
  window.addEventListener("keydown", closeStepMenu);
  onCleanup(() => {
    window.removeEventListener("click", closeStepMenu);
    window.removeEventListener("blur", closeStepMenu);
    window.removeEventListener("keydown", closeStepMenu);
  });

  function dialogStyle() {
    const position = dialogPosition();
    if (!position) return {};
    return { left: `${position.left}px`, top: `${position.top}px` };
  }

  function startDialogDrag(event) {
    const target = event.target instanceof Element ? event.target : event.currentTarget;
    if (event.button !== 0 || target.closest("button,input,select,textarea")) return;
    const dialog = event.currentTarget.closest(".dmv1-movie-dialog");
    const rect = dialog?.getBoundingClientRect();
    if (!rect) return;
    event.preventDefault();
    setDialogPosition({ left: rect.left, top: rect.top });
    const offsetX = event.clientX - rect.left;
    const offsetY = event.clientY - rect.top;
    const onMove = (moveEvent) => {
      const maxLeft = Math.max(8, window.innerWidth - rect.width - 8);
      const maxTop = Math.max(8, window.innerHeight - rect.height - 8);
      setDialogPosition({
        left: Math.min(Math.max(8, moveEvent.clientX - offsetX), maxLeft),
        top: Math.min(Math.max(8, moveEvent.clientY - offsetY), maxTop),
      });
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("blur", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("blur", onUp);
  }

  function openStepContextMenu(event, step) {
    if (String(step?.id || "") !== "05_02") return;
    event.preventDefault();
    event.stopPropagation();
    const menuWidth = 240;
    const menuHeight = 92;
    setStepMenu({
      left: Math.min(event.clientX, Math.max(8, window.innerWidth - menuWidth - 8)),
      top: Math.min(event.clientY, Math.max(8, window.innerHeight - menuHeight - 8)),
      stepId: "05_02",
    });
  }

  return (
    <Show when={props.open}>
      <div class="drawer-backdrop dmv1-movie-backdrop" onClick={() => props.onClose?.()} />
      <section class="verify-dialog dmv1-movie-dialog" classList={{ "is-dragged": Boolean(dialogPosition()) }} style={dialogStyle()}>
        <div class="dmv1-movie-head" onPointerDown={startDialogDrag}>
          <div class="dmv1-movie-tags">
            <Show when={props.taskId}><span>任务 #{props.taskId}</span></Show>
            <Show when={props.sessionId}><span>会话 #{props.sessionId}</span></Show>
            <Show when={props.runId}><span>尝试 #{props.runId}</span></Show>
          </div>
          <div class="dmv1-movie-commandbar">
            <button class="primary icon-action dmv1-movie-start" type="button" title={props.startTitle || "一键成片"} aria-label={props.startTitle || "一键成片"} disabled={props.startDisabled} onClick={() => props.onStart?.()}>
              <PlayClipIcon />
            </button>
            <button class="secondary icon-action" type="button" disabled={props.storyboardDisabled} title={props.storyboardTitle || "故事板"} aria-label={props.storyboardTitle || "故事板"} onClick={() => props.onOpenStoryBoard?.()}>
              <StoryboardIcon />
            </button>
            <button class="icon-action" type="button" title="关闭" aria-label="关闭" onClick={() => props.onClose?.()}>
              <CloseIcon />
            </button>
          </div>
        </div>
        <div class="dmv1-movie-body">
          <Show when={props.message}>
            <div class={`dmv1-movie-message is-${stepTone(props.status)}`}>{props.message}</div>
          </Show>
          <section class="dmv1-movie-flow-status" classList={{ "is-collapsed": !stepsExpanded() }}>
            <Show when={!stepsExpanded()} fallback={
              <div class="dmv1-movie-step-expanded">
                <button class="dmv1-movie-inline-collapse" type="button" onClick={() => setStepsExpanded(false)}>收起</button>
                <div class="dmv1-movie-step-list">
                <Show when={steps().length} fallback={<div class="dmv1-empty">尚未运行</div>}>
                  <For each={steps()}>{(step, index) => {
                    const tone = stepTone(step.status);
                    return (
                      <div class={`dmv1-movie-step is-${tone}`} classList={{ "has-context-menu": step.id === "05_02" }} onContextMenu={(event) => openStepContextMenu(event, step)}>
                        <b>{index() + 1}</b>
                        <strong>{props.stepDisplayName?.(step) || step.display_name_zh || step.name || step.id}</strong>
                        <span class={`dmv1-movie-step-status tag-${tone === "ready" ? "ready" : tone === "running" ? "available" : tone === "failed" ? "failed" : "idle"}`}>{stepStatusLabel(step.status)}</span>
                        <em>{stepDuration(step)}</em>
                      </div>
                    );
                  }}</For>
                </Show>
                </div>
              </div>
            }>
              <button class="dmv1-movie-section-head dmv1-movie-section-toggle dmv1-movie-progress-toggle" type="button" aria-expanded={stepsExpanded()} onClick={() => setStepsExpanded(true)}>
                <div class="dmv1-movie-section-title">
                  <h3>全流程进度</h3>
                  <div class="dmv1-movie-progress-track" aria-hidden="true">
                    <span style={{ width: `${stepProgressPercent()}%` }} />
                  </div>
                </div>
                <div class="dmv1-movie-section-meta">
                  <b>{stepProgressPercent()}%</b>
                  <em>展开</em>
                </div>
              </button>
            </Show>
          </section>
          <section class="dmv1-movie-segment-status" classList={{ "is-collapsed": !segmentsExpanded() }}>
            <button class="dmv1-movie-section-head dmv1-movie-section-toggle" type="button" aria-expanded={segmentsExpanded()} onClick={() => setSegmentsExpanded((value) => !value)}>
              <div class="dmv1-movie-section-title">
                <h3>逐句成片状态</h3>
              </div>
              <div class="dmv1-movie-section-meta">
                <b>{segments().length ? `${segments().length} 段` : "暂无"}</b>
                <em>{segmentsExpanded() ? "收起" : "展开"}</em>
              </div>
            </button>
            <Show when={segmentsExpanded()}>
              <Show when={segments().length} fallback={<div class="dmv1-empty">等待视频计划生成逐句状态。</div>}>
                <div class="dmv1-movie-segments">
                  <For each={segments()}>{(segment) => {
                    const storyboardItem = storyboardItemForSegment(segment, props.storyboardItems);
                    return (
                      <article class={`dmv1-movie-segment is-${stepTone(segment.status)}`}>
                        <div class="dmv1-movie-segment-row">
                          <div class="dmv1-movie-segment-top">
                            <span>S{segment.index}</span>
                            <b>{segmentDuration(segment, props.storyboardItems)}</b>
                          </div>
                          <div class="dmv1-movie-pipeline">
                            <TaskBadge state={audioState(segment, storyboardItem)} icon={MicIcon} label="音频" />
                            <Arrow />
                            <TaskBadge state={frameState(segment)} icon={ImageIcon} label={frameLabel(segment)} />
                            <Arrow />
                            <TaskBadge state={videoState(segment)} icon={ClapperboardIcon} label="新视频" />
                            <Arrow />
                            <TaskBadge state={syncState(segment, props)} icon={AudioLinesIcon} label={syncLabel(segment, props)} />
                          </div>
                        </div>
                        <Show when={segment.error}>
                          <small>{segment.error}</small>
                        </Show>
                      </article>
                    );
                  }}</For>
                </div>
              </Show>
            </Show>
          </section>
          <div class="dmv1-movie-total-duration">总耗时 {formatRunDuration(totalDuration())}</div>
        </div>
      </section>
      <Show when={stepMenu()}>
        <Portal>
          <div class="dmv1-movie-context-menu" style={{ left: `${stepMenu().left}px`, top: `${stepMenu().top}px` }} onClick={(event) => event.stopPropagation()}>
            <button type="button" onClick={() => { setStepMenu(null); props.onResumeVideoStep?.(); }}>继续完成该步</button>
            <button type="button" onClick={() => { setStepMenu(null); props.onResumeVideoStepAndFollowing?.(); }}>继续完成后续步骤</button>
          </div>
        </Portal>
      </Show>
    </Show>
  );
}
