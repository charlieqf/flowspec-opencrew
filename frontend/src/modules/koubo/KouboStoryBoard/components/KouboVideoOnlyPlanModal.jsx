import { For, Show, createEffect, createMemo, createSignal, onCleanup } from "solid-js";
import { AudioLinesIcon, FilmIcon, ImageIcon, PlayIcon, RefreshIcon, WorkflowIcon, XIcon } from "../kouboStoryboardIcons.jsx";

function text(value) {
  return String(value || "").trim();
}

function list(value) {
  return Array.isArray(value) ? value : [];
}

function num(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

const STEP_LABELS = {
  audio: "音频",
  first_frame: "首帧",
  prompt: "提示词",
  video: "新视频",
  confirm_final: "终视频",
  task: "视频计划",
};

function redactError(value) {
  return text(value)
    .replace(/([?&]key=)[^&\s"'}]+/gi, "$1***")
    .replace(/(api[_-]?key["']?\s*[:=]\s*["']?)[^"',}\s]+/gi, "$1***")
    .replace(/(Authorization["']?\s*[:=]\s*["']?\s*Bearer\s+)[^"',}\s]+/gi, "$1***");
}

function groupTasks(tasks = []) {
  const shots = [];
  const shotMap = new Map();
  for (const task of tasks) {
    const shotId = text(task.shot_id) || "shot_001";
    const sceneId = text(task.scene_id) || "scene_001";
    let shot = shotMap.get(shotId);
    if (!shot) {
      shot = { shot_id: shotId, scenes: [], sceneMap: new Map() };
      shotMap.set(shotId, shot);
      shots.push(shot);
    }
    let scene = shot.sceneMap.get(sceneId);
    if (!scene) {
      scene = { scene_id: sceneId, tasks: [] };
      shot.sceneMap.set(sceneId, scene);
      shot.scenes.push(scene);
    }
    scene.tasks.push(task);
  }
  return shots.map((shot) => ({ shot_id: shot.shot_id, scenes: shot.scenes }));
}

function taskArtifact(result, task) {
  const artifactTasks = result?.artifact_status?.tasks || [];
  const taskId = text(task.video_only_task_id);
  const assetKey = text(task.asset_key);
  return artifactTasks.find((item) => text(item.video_only_task_id) === taskId) || artifactTasks.find((item) => text(item.asset_key) === assetKey) || {};
}

function stateTask(executionState, task) {
  const segments = executionState?.segments && typeof executionState.segments === "object" && !Array.isArray(executionState.segments) ? executionState.segments : {};
  return segments[text(task.asset_key)] || segments[text(task.video_only_task_id)] || {};
}

function resultTask(executionResult, task) {
  const tasks = Array.isArray(executionResult?.tasks) ? executionResult.tasks : [];
  const taskId = text(task.video_only_task_id);
  const assetKey = text(task.asset_key);
  return tasks.find((item) => text(item.video_only_task_id) === taskId) || tasks.find((item) => text(item.asset_key) === assetKey) || {};
}

function stateStep(executionState, task, step) {
  const state = stateTask(executionState, task);
  const steps = state?.steps && typeof state.steps === "object" && !Array.isArray(state.steps) ? state.steps : {};
  return steps?.[step] && typeof steps[step] === "object" ? steps[step] : {};
}

function stateStepStatus(executionState, task, step) {
  return text(stateStep(executionState, task, step)?.status);
}

function taskErrors(task, executionState, executionResult) {
  const errors = [];
  const state = stateTask(executionState, task);
  const result = resultTask(executionResult, task);
  const stepSources = [state?.steps, result?.steps].filter((steps) => steps && typeof steps === "object" && !Array.isArray(steps));
  for (const steps of stepSources) {
    for (const [step, payload] of Object.entries(steps)) {
      if (text(payload?.status) !== "failed") continue;
      const message = redactError(payload?.error || state?.error || result?.error);
      if (message && !errors.some((item) => item.step === step && item.message === message)) {
        errors.push({ step, label: STEP_LABELS[step] || step, message });
      }
    }
  }
  const taskError = redactError(state?.error || result?.error);
  if (taskError && !errors.some((item) => item.message === taskError)) {
    errors.push({ step: "task", label: STEP_LABELS.task, message: taskError });
  }
  return errors;
}

function isRunningStatus(status) {
  const value = text(status);
  return value === "running" || value.startsWith("running_");
}

function stepRunning(executionState, task, step) {
  if (!["queued", "running"].includes(text(executionState?.status))) return false;
  if (text(executionState?.current_asset_key) === text(task.asset_key) && text(executionState?.current_step) === step && isRunningStatus(executionState?.current_step_status)) return true;
  return isRunningStatus(stateStepStatus(executionState, task, step));
}

function slotBadgeState(slot = null, pendingDot = "generate") {
  if (!slot || typeof slot !== "object") return null;
  const tone = text(slot.ui_tone || slot.tone);
  if (!tone) return null;
  return {
    tone,
    title: text(slot.title || slot.reason),
    dot: tone === "pending" ? pendingDot : "",
  };
}

function previousTailFrameSource(task = {}, artifact = {}) {
  const sourceSegment = task?.source_segment && typeof task.source_segment === "object" ? task.source_segment : {};
  const segmentFirstFrame = sourceSegment?.first_frame && typeof sourceSegment.first_frame === "object" ? sourceSegment.first_frame : {};
  const sourceType = text(artifact.frame_input_source_type || task?.first_frame?.source_type || segmentFirstFrame.source_type);
  return sourceType === "previous_segment_tail_frame" || sourceType === "previous_scene_tail_frame";
}

function stepState(task, step, executionState, artifact) {
  const stepPayload = stateStep(executionState, task, step);
  const stateStatus = text(stepPayload?.status);
  const slotKey = {
    audio: "audio",
    first_frame: "image",
    prompt: "video_prompt",
    video: "raw_video",
    confirm_final: "copy_final",
  }[step];
  const rawSlot = artifact?.slot_states?.[slotKey];
  const fromSlot = slotBadgeState(rawSlot, step === "confirm_final" ? "copy" : "generate");
  if (fromSlot?.tone === "done") return fromSlot;
  if (stepRunning(executionState, task, step)) return { tone: "running", title: "正在执行" };
  if (stateStatus === "failed") return { tone: "failed", title: redactError(stepPayload?.error) || "执行失败" };
  if (fromSlot?.tone === "disabled" && text(rawSlot?.reason) === "skipped_consumed_by_downstream") return fromSlot;
  if (step === "first_frame" && previousTailFrameSource(task, artifact)) {
    if (artifact.frame_input_exists && artifact.frame_input_kind === "tail_frame_materialized") return { tone: "done", title: "尾帧作为新图" };
    if (artifact.frame_input_exists) return { tone: "done", title: "新图可用" };
    if (artifact.frame_input_copy_pending || artifact.tail_frame_materializable) return { tone: "pending", dot: "copy", title: "点击物化尾帧为新图" };
    return { tone: "pending", title: "等待上一段/上一 Shot 尾帧" };
  }
  if (fromSlot) return fromSlot;
  if (step === "audio") {
    if (artifact.audio_in_working) return { tone: "done", title: "Audio 已可用" };
    if (artifact.audio_copy_pending) return { tone: "pending", title: "Audio 待拷贝到 Working" };
    if (artifact.audio_generate_pending) return { tone: "pending", title: "待生成 Audio" };
    if (Array.isArray(artifact.audio_files) && artifact.audio_files.length) return { tone: "pending", title: "Dialogue Audio 缺失" };
  }
  if (step === "first_frame") {
    if (artifact.frame_input_exists && artifact.frame_input_kind === "tail_frame_materialized") return { tone: "done", title: "尾帧作为新图" };
    if (artifact.frame_input_exists) return { tone: "done", title: "新图可用" };
    if (artifact.frame_input_copy_pending) return { tone: "pending", title: "执行时物化尾帧为新图" };
    if (artifact.new_image?.path || artifact.tail_frame_input?.path) return { tone: "pending", title: "首帧来源文件不存在" };
    return { tone: "pending", title: "缺少新图或上一段尾帧" };
  }
  if (step === "prompt" && artifact.prompt_in_working) return { tone: "done", title: "Prompt 已生成" };
  if (step === "video" && (artifact.raw_in_working || artifact.final_in_working)) return { tone: "done", title: artifact.final_in_working ? "Final 已存在" : "Raw 已生成" };
  if (step === "confirm_final" && artifact.final_bound) return { tone: "done", title: "Final 已确认" };
  if (step === "confirm_final" && artifact.raw_in_working) return { tone: "pending", title: "等待确认 Final" };
  if (stateStatus === "completed_working" && !["first_frame", "prompt", "video", "confirm_final"].includes(step)) return { tone: "done", title: "已完成" };
  const planStep = task?.steps?.[step] || {};
  if (planStep.status === "completed_working" && !["first_frame", "prompt", "video", "confirm_final"].includes(step)) return { tone: "done", title: "已完成" };
  if (text(task.status).startsWith("blocked")) return { tone: "disabled", title: text(task.blocked_reason) || "Blocked" };
  if (step === "video") return { tone: "pending", title: "待执行" };
  if (step === "confirm_final") return { tone: "disabled", title: "Raw 不存在，不能确认" };
  if (planStep.status === "disabled" || planStep.status === "disabled_consumed_by_video") return { tone: "disabled", title: "不可执行" };
  return { tone: planStep.required === false ? "disabled" : "pending", title: "待执行" };
}

function TaskBadge(props) {
  const Icon = props.icon;
  const state = () => props.state || { tone: "disabled", title: "" };
  const classList = createMemo(() => ({
    "is-pending": state().tone === "pending",
    "is-done": state().tone === "done",
    "is-running": state().tone === "running",
    "is-disabled": state().tone === "disabled",
    "is-failed": state().tone === "failed",
    "has-dot": Boolean(state().dot),
    "has-generate-dot": state().dot === "generate",
    "has-copy-dot": state().dot === "copy",
    "is-clickable": Boolean(props.onClick),
  }));
  const body = <>
    <Icon />
    <span>{props.label}</span>
  </>;
  return <Show when={props.onClick} fallback={<div class="kbsp-vpm-task-badge" classList={classList()} title={state().title}>{body}</div>}>
    <button type="button" class="kbsp-vpm-task-badge" classList={classList()} title={props.title || state().title} onClick={props.onClick}>{body}</button>
  </Show>;
}

function Arrow() {
  return <span class="kbsp-vpm-pipe-arrow">→</span>;
}

function frameInputLabel(task = {}, artifact = {}) {
  if (artifact.frame_input_kind === "tail_frame_materialized") return "尾帧作为新图";
  if (artifact.frame_input_copy_pending || previousTailFrameSource(task, artifact)) return "尾帧";
  return "新图";
}

export default function KouboVideoOnlyPlanModal(props) {
  const [promptEditor, setPromptEditor] = createSignal({ open: false, assetKey: "", path: "", positivePrompt: "", negativePrompt: "", dialogueText: "", error: "", saving: false, reloading: false });
  const result = () => props.result?.() || {};
  const plan = () => result().plan || {};
  const tasks = createMemo(() => Array.isArray(plan().video_only_tasks) ? plan().video_only_tasks : []);
  const executionState = createMemo(() => result().binding_status?.state_matches_current_plan ? result().execution_state || {} : {});
  const executionResult = createMemo(() => result().binding_status?.result_matches_current_plan ? result().execution_result || {} : {});
  const executionRunning = createMemo(() => ["queued", "running"].includes(text(executionState().status)));
  const grouped = createMemo(() => groupTasks(tasks()));
  const progress = createMemo(() => {
    const artifactTasks = result().artifact_status?.tasks || [];
    return {
      prompt: { total: tasks().length, done: artifactTasks.filter((item) => item.prompt_in_working).length },
      video: { total: tasks().length, done: artifactTasks.filter((item) => item.raw_in_working || item.final_in_working).length },
      final: { total: tasks().length, done: artifactTasks.filter((item) => item.final_bound).length },
    };
  });

  createEffect(() => {
    if (!props.open?.()) return;
    const timer = window.setInterval(() => props.refreshExecution?.().catch?.((err) => props.setError?.(err instanceof Error ? err.message : String(err))), executionRunning() ? 1600 : 5000);
    onCleanup(() => window.clearInterval(timer));
  });

  const slotTone = (artifact, key) => text(artifact?.slot_states?.[key]?.ui_tone || artifact?.slot_states?.[key]?.tone);
  const slotBinding = (artifact, key) => text(artifact?.slot_states?.[key]?.binding_consistency);
  const canExecutePrompt = (task) => {
    const artifact = taskArtifact(result(), task);
    return !executionRunning() && slotTone(artifact, "video_prompt") === "pending";
  };
  const canExecuteVideo = (task) => {
    const artifact = taskArtifact(result(), task);
    return !executionRunning() && slotTone(artifact, "raw_video") === "pending";
  };
  const canMaterializeTailFrame = (task) => {
    const artifact = taskArtifact(result(), task);
    return !executionRunning() && previousTailFrameSource(task, artifact) && !artifact.frame_input_exists && Boolean(artifact.frame_input_copy_pending || artifact.tail_frame_materializable);
  };
  const canExecutePromptAndVideo = (task) => {
    const artifact = taskArtifact(result(), task);
    return !executionRunning() && artifact.frame_input_ready_for_execution && slotTone(artifact, "video_prompt") === "pending" && !artifact.raw_in_working && !artifact.final_in_working;
  };
  const canConfirmFinal = (task) => {
    const artifact = taskArtifact(result(), task);
    return !executionRunning() && (slotTone(artifact, "copy_final") === "pending" || slotBinding(artifact, "copy_final") === "file_exists_unbound");
  };

  function promptEditorPayload(response, task, assetKey) {
    const prompt = response?.prompt && typeof response.prompt === "object" ? response.prompt : {};
    const extractedFields = prompt.extracted_fields && typeof prompt.extracted_fields === "object" ? prompt.extracted_fields : {};
    return {
      open: true,
      assetKey,
      path: response?.path || "",
      positivePrompt: text(prompt.positive_prompt || prompt.prompt),
      negativePrompt: text(prompt.negative_prompt),
      dialogueText: text(extractedFields.dialogue_text || task?.dialogue_text || task?.status),
      error: "",
      saving: false,
      reloading: false,
    };
  }

  async function openPrompt(task) {
    const assetKey = text(task.asset_key);
    if (!assetKey || !props.task?.()?.id) return;
    try {
      const response = await props.api?.videoOnlyPlanPrompt?.(props.task().id, assetKey);
      setPromptEditor(promptEditorPayload(response, task, assetKey));
    } catch (err) {
      props.setError?.(err instanceof Error ? err.message : String(err));
    }
  }

  async function reloadPrompt() {
    const current = promptEditor();
    if (!current.assetKey || !props.task?.()?.id) return;
    const confirmed = window.confirm("重新加载会丢弃当前未保存编辑，并用最新模板覆盖当前提示词。是否继续？");
    if (!confirmed) return;
    const task = tasks().find((item) => text(item.asset_key) === current.assetKey) || {};
    setPromptEditor((previous) => ({ ...previous, reloading: true, saving: false, error: "" }));
    try {
      const response = await props.api?.reloadVideoOnlyPlanPrompt?.(props.task().id, current.assetKey);
      setPromptEditor(promptEditorPayload(response, task, current.assetKey));
      await props.refreshExecution?.();
    } catch (err) {
      setPromptEditor((previous) => ({ ...previous, reloading: false, error: err instanceof Error ? err.message : String(err) }));
    }
  }

  async function savePrompt() {
    const current = promptEditor();
    if (!text(current.positivePrompt)) {
      setPromptEditor((previous) => ({ ...previous, error: "Positive Prompt is required" }));
      return;
    }
    setPromptEditor((previous) => ({ ...previous, saving: true, error: "" }));
    try {
      await props.api?.saveVideoOnlyPlanPrompt?.(props.task().id, current.assetKey, {
        prompt: {
          positive_prompt: current.positivePrompt,
          negative_prompt: current.negativePrompt,
        },
      });
      setPromptEditor((previous) => ({ ...previous, open: false, saving: false }));
      await props.refreshExecution?.();
    } catch (err) {
      setPromptEditor((previous) => ({ ...previous, saving: false, error: err instanceof Error ? err.message : String(err) }));
    }
  }

  return <Show when={props.open?.()}>
    <div class="kbsp-vpm-backdrop" role="dialog" aria-modal="true" aria-label="视频计划">
      <section class="kbsp-vpm-shell kbsp-vop-shell">
        <header class="kbsp-vpm-header">
          <div class="kbsp-vpm-title-wrap">
            <div class="kbsp-vpm-mark"><FilmIcon /></div>
            <div class="kbsp-vpm-title-copy">
              <h2>视频计划</h2>
              <div class="kbsp-vpm-summary-line" aria-label="视频计划范围">
                <span>Task {tasks().length}</span>
                <span>Prompt {tasks().length}</span>
              <span>Raw {progress().video.done}/{progress().video.total}</span>
              </div>
            </div>
          </div>
          <div class="kbsp-vpm-dashboard" aria-label="视频计划指标">
            <div class="kbsp-vpm-dashboard-tasks">
              <span title="Prompt"><WorkflowIcon /><em>{progress().prompt.done}/{progress().prompt.total}</em></span>
              <span title="Video"><FilmIcon /><em>{progress().video.done}/{progress().video.total}</em></span>
              <span title="Final"><PlayIcon /><em>{progress().final.done}/{progress().final.total}</em></span>
            </div>
          </div>
          <div class="kbsp-vpm-actions">
            <button type="button" aria-label="关闭视频计划" onClick={() => props.setOpen?.(false)}><XIcon /></button>
          </div>
        </header>
        <Show when={grouped().length} fallback={<div class="kbsp-vpm-empty">没有可展示的视频计划数据。</div>}>
          <div class="kbsp-vpm-body">
            <For each={grouped()}>{(shot) => {
              const shotTasks = () => shot.scenes?.flatMap((scene) => scene.tasks || []) || [];
              const firstPromptTask = () => shotTasks().find((task) => canExecutePrompt(task)) || null;
              const firstVideoTask = () => shotTasks().find((task) => canExecuteVideo(task)) || null;
              const firstPromptAndVideoTask = () => shotTasks().find((task) => canExecutePromptAndVideo(task)) || null;
              const shotBusyTitle = () => executionRunning() ? "视频计划正在执行" : "";
              const noPromptTitle = () => shotBusyTitle() || "没有可生成 Prompt 的片段；已有 Raw/Final 的片段不能重跑 Prompt";
              const noVideoTitle = () => shotBusyTitle() || "没有可生成 Video 的片段；请先确认新图或上一段尾帧、Prompt 都存在，且该片段还没有 Raw/Final";
              return <section class="kbsp-vpm-shot">
              <div class="kbsp-vpm-shot-head"><h3>{shot.shot_id}</h3>
                <div class="kbsp-ipm-shot-actions">
                  <button type="button" disabled={!firstPromptTask()} title={firstPromptTask() ? "" : noPromptTitle()} onClick={() => props.executePlan?.("prompt-only", firstPromptTask())}><span class="kbsp-ipm-action-icon"><PlayIcon /></span><span>提示词</span></button>
                  <button type="button" disabled={!firstVideoTask()} title={firstVideoTask() ? "" : noVideoTitle()} onClick={() => props.executePlan?.("video-only", firstVideoTask())}><span class="kbsp-ipm-action-icon"><PlayIcon /></span><span>新视频</span></button>
                  <button type="button" disabled={!firstPromptAndVideoTask()} title={firstPromptAndVideoTask() ? "" : noPromptTitle()} onClick={() => props.executePlan?.("prompt-and-video", firstPromptAndVideoTask())}><span class="kbsp-ipm-action-icon"><PlayIcon /></span><span>提示词+新视频</span></button>
                </div>
              </div>
              <div class="kbsp-vpm-scenes">
              <For each={shot.scenes}>{(scene) => {
                return <div class="kbsp-vpm-scene">
                <div class="kbsp-vpm-rail-dot" />
                <div class="kbsp-vpm-scene-content">
                <div class="kbsp-vpm-scene-title"><strong>{scene.scene_id}</strong></div>
                <div class="kbsp-vop-segments">
                <For each={scene.tasks}>{(task, taskIndex) => {
                  const artifact = () => taskArtifact(result(), task);
                  const errors = createMemo(() => taskErrors(task, executionState(), executionResult()));
                  return <article class="kbsp-vpm-segment kbsp-vop-segment" classList={{ "has-error": Boolean(errors().length) }}>
                    <div class="kbsp-vop-segment-copy">
                      <span class="kbsp-ipm-segment-index">S{taskIndex() + 1}</span>
                    </div>
                    <div class="kbsp-vpm-pipeline kbsp-vop-pipeline">
                      <TaskBadge state={stepState(task, "audio", executionState(), artifact())} icon={AudioLinesIcon} label="音频" />
                      <Arrow />
                      <TaskBadge state={stepState(task, "first_frame", executionState(), artifact())} icon={ImageIcon} label={frameInputLabel(task, artifact())} onClick={canMaterializeTailFrame(task) ? () => props.materializeTailFrame?.(task) : null} />
                      <Arrow />
                      <TaskBadge state={stepState(task, "prompt", executionState(), artifact())} icon={WorkflowIcon} label="提示词" onClick={artifact().prompt_in_working ? () => openPrompt(task) : (canExecutePrompt(task) ? () => props.executePlan?.("prompt-only", task) : null)} />
                      <Arrow />
                      <TaskBadge state={stepState(task, "video", executionState(), artifact())} icon={FilmIcon} label="新视频" onClick={canExecuteVideo(task) ? () => props.executePlan?.("video-only", task) : null} />
                      <Arrow />
                      <TaskBadge state={stepState(task, "confirm_final", executionState(), artifact())} icon={PlayIcon} label="拷贝成终视频" onClick={canConfirmFinal(task) ? () => props.confirmFinal?.(task) : null} />
                    </div>
                    <Show when={errors().length}>
                      <div class="kbsp-vpm-error-list" aria-label="Execution errors">
                        <For each={errors()}>{(item) => <p class="kbsp-vpm-error"><strong>{item.label}</strong><span>{item.message}</span></p>}</For>
                      </div>
                    </Show>
                  </article>;
                }}</For>
                </div>
                </div>
              </div>;
              }}</For>
              </div>
            </section>;
            }}</For>
          </div>
        </Show>
      </section>
      <Show when={promptEditor().open}>
        <div class="kbsp-ipm-prompt-editor kbsp-vop-prompt-editor" role="dialog" aria-modal="true" aria-label="Video Prompt Editor" onClick={(event) => event.stopPropagation()}>
          <header>
            <div>
              <h3>Video Prompt</h3>
              <p>{promptEditor().assetKey}</p>
            </div>
            <button type="button" aria-label="Close Prompt" disabled={promptEditor().reloading || promptEditor().saving} onClick={() => setPromptEditor((current) => ({ ...current, open: false }))}><XIcon /></button>
          </header>
          <div class="kbsp-ipm-prompt-path">{promptEditor().path}</div>
          <label class="kbsp-vop-dialogue-field">
            <span>Dialogue Text</span>
            <textarea value={promptEditor().dialogueText} readonly spellcheck={false} />
          </label>
          <section class="kbsp-ipm-prompt-fields" aria-label="Prompt fields">
            <label class="kbsp-ipm-prompt-field">
              <span>Positive Prompt</span>
              <textarea class="kbsp-ipm-prompt-textarea" spellcheck={false} value={promptEditor().positivePrompt} onInput={(event) => setPromptEditor((current) => ({ ...current, positivePrompt: event.currentTarget.value, error: "" }))} />
            </label>
            <label class="kbsp-ipm-prompt-field">
              <span>Negative Prompt</span>
              <textarea class="kbsp-ipm-prompt-textarea" spellcheck={false} value={promptEditor().negativePrompt} onInput={(event) => setPromptEditor((current) => ({ ...current, negativePrompt: event.currentTarget.value, error: "" }))} />
            </label>
          </section>
          <Show when={promptEditor().error}><p class="kbsp-ipm-prompt-error">{promptEditor().error}</p></Show>
          <footer>
            <button type="button" class="secondary kbsp-ipm-prompt-reload" disabled={promptEditor().saving || promptEditor().reloading} onClick={reloadPrompt}><RefreshIcon /><span>{promptEditor().reloading ? "重新加载中..." : "重新加载"}</span></button>
            <button type="button" class="secondary" disabled={promptEditor().saving || promptEditor().reloading} onClick={() => setPromptEditor((current) => ({ ...current, open: false }))}>Cancel</button>
            <button type="button" disabled={promptEditor().saving || promptEditor().reloading || !text(promptEditor().positivePrompt)} onClick={savePrompt}>{promptEditor().saving ? "Saving..." : "Save Prompt"}</button>
          </footer>
        </div>
      </Show>
    </div>
  </Show>;
}
