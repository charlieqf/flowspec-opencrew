import { For, Show, createEffect, createMemo, createSignal, onCleanup } from "solid-js";
import { AudioLinesIcon, ClapperboardIcon, ImageIcon, MessageIcon, MicIcon, PlayIcon, WorkflowIcon, XIcon } from "../kouboStoryboardIcons.jsx";
import KouboAgentDrawer from "./KouboAgentDrawer.jsx";

function num(value, fallback = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function taskCount(plan, key) {
  let count = 0;
  for (const shot of plan?.shots || []) {
    for (const scene of shot.scenes || []) {
      for (const segment of scene.segments || []) {
        if (segment.tasks?.[key]) count += 1;
      }
    }
  }
  return count;
}

function text(value) {
  return String(value || "").trim();
}

function reasonText(value) {
  if (Array.isArray(value)) return value.map(reasonText).filter(Boolean).join("；");
  if (value && typeof value === "object") {
    return text(value.message || value.detail || value.reason || value.code);
  }
  return text(value);
}

function targetScopeLabel(target = {}) {
  const targetType = text(target.target_type || target.scope);
  if (targetType === "task" || targetType === "all") return "整片";
  if (targetType === "shot") return text(target.shot_id) || "镜头";
  if (targetType === "scene") return [text(target.shot_id), text(target.scene_id)].filter(Boolean).join(" / ") || "场景";
  return "未指定";
}

function shortHash(value) {
  const hash = text(value);
  return hash ? hash.slice(0, 8) : "-";
}

const STEP_LABELS = {
  audio: "Audio",
  image: "First Frame",
  video: "Raw Video",
  sync: "Final Video",
  tail: "Tail Frame",
  segment: "Segment",
};

function redactError(value) {
  return reasonText(value)
    .replace(/([?&]key=)[^&\s"'}]+/gi, "$1***")
    .replace(/(api[_-]?key["']?\s*[:=]\s*["']?)[^"',}\s]+/gi, "$1***")
    .replace(/(Authorization["']?\s*[:=]\s*["']?\s*Bearer\s+)[^"',}\s]+/gi, "$1***");
}

function isWorkingPath(path) {
  return text(path).startsWith("SessionOutput/storyboard/Working/");
}

function hasPlannedPath(path) {
  return Boolean(text(path));
}

function executionTone(step = {}) {
  const status = text(step.status);
  if (!status) return null;
  if (status === "completed_working") return { tone: "done", dot: "", title: "已在 Working" };
  if (status === "pending_copy") return { tone: "pending", dot: "copy", title: "待拷贝到 Working" };
  if (status === "pending_generate") return { tone: "pending", dot: "generate", title: "待生成" };
  if (status === "running_copy") return { tone: "running", dot: "", title: "正在拷贝到 Working" };
  if (status === "running_generate") return { tone: "running", dot: "", title: "正在生成" };
  if (status === "skipped_by_cutaway") return { tone: "disabled", dot: "", title: "空镜不执行" };
  if (status === "skipped_by_bound_video") return { tone: "disabled", dot: "", title: "绑定视频不执行" };
  if (status === "skipped") {
    const reason = text(step.reason);
    if (reason === "bound_video" || reason === "skipped_by_bound_video") return { tone: "disabled", dot: "", title: "绑定视频不执行" };
    if (reason === "user_marked_cutaway" || reason === "skipped_by_cutaway") return { tone: "disabled", dot: "", title: "空镜不执行" };
    return { tone: "disabled", dot: "", title: reason || "不执行" };
  }
  if (status === "failed") return { tone: "failed", dot: "", title: text(step.error) || "执行失败" };
  return null;
}

function slotBadgeState(slot = null, pendingDot = "generate") {
  if (!slot || typeof slot !== "object") return null;
  const tone = text(slot.ui_tone || slot.tone);
  if (!tone) return null;
  return {
    tone,
    dot: tone === "pending" ? pendingDot : "",
    title: text(slot.title || slot.reason),
  };
}

function badgeState(segment, kind, artifact = {}, executionStep = {}, executionSegment = {}) {
  const tasks = segment?.tasks || {};
  const outputs = segment?.planned_outputs || {};
  const firstFrame = segment?.first_frame || {};
  const materializeFrame = firstFrame?.materialize_first_frame || {};
  const existingVideo = segment?.existing_video || {};
  const materializeVideo = existingVideo?.materialize_video || {};
  const audioTasks = Array.isArray(segment?.dialogue_audio_tasks) ? segment.dialogue_audio_tasks : [];
  const needsSync = Boolean(tasks.need_sync || tasks.need_lipsync || tasks.need_audio_video_sync);
  const needsAudioVideoSync = Boolean(tasks.need_audio_video_sync || tasks.sync_mode === "audio_replace_retime");
  const fromExecution = executionTone(executionStep);
  const slotKey = { audio: "audio", image: "image", video: "raw_video", sync: "final_video" }[kind];
  const rawSlot = artifact.slot_states?.[slotKey];
  const fromSlot = slotBadgeState(rawSlot, kind === "sync" ? "copy" : "generate");
  if (kind === "sync") {
    const syncLabel = syncStepLabel(segment);
    const pendingTitle = `待执行${syncLabel}`;
    if (fromExecution?.tone === "running") return fromExecution;
    if (fromSlot?.tone === "done") return { ...fromSlot, title: `${syncLabel}已完成` };
    if (fromSlot?.tone === "disabled") return fromSlot;
    if (fromExecution && fromExecution.tone !== "disabled" && fromExecution.tone !== "done") return fromExecution;
    if (artifact.sync_in_working) return { tone: "done", dot: "", title: `${syncLabel}已完成` };
    if (fromSlot?.tone === "pending" && ["raw_ready", "raw_and_audio_ready"].includes(text(rawSlot?.reason)) && (artifact.sync_generate_pending || needsSync)) {
      return { tone: "pending", dot: "generate", title: pendingTitle };
    }
    if (fromSlot && fromSlot.tone !== "disabled") return fromSlot;
    if (fromSlot) return fromSlot;
    if (needsAudioVideoSync) return { tone: "pending", dot: "generate", title: pendingTitle };
    if (tasks.lipsync_reason === "existing_video_bound_complete") return { tone: "disabled", dot: "", title: "绑定视频不执行 Sync" };
    if (tasks.lipsync_disabled_by_ui || tasks.lipsync_reason === "user_marked_cutaway") return { tone: "disabled", dot: "", title: "空镜不执行音频匹配" };
    return { tone: "disabled", dot: "", title: "不执行 Sync" };
  }
  if (fromExecution?.tone === "running") return fromExecution;
  if (fromSlot?.tone === "done") return fromSlot;
  if (fromSlot?.tone === "disabled" && text(rawSlot?.reason) === "skipped_consumed_by_downstream") return fromSlot;
  if (fromExecution && fromExecution.tone !== "disabled" && fromExecution.tone !== "done") return fromExecution;
  if (fromSlot && fromSlot.tone !== "disabled") return fromSlot;
  if (kind === "video" && artifact.video_in_working) return { tone: "done", dot: "", title: "Raw Video 已在 Working" };
  if (kind === "sync" && artifact.sync_in_working) return { tone: "done", dot: "", title: "Final Video 已在 Working" };
  if (fromSlot) return fromSlot;
  if (segment?.status === "blocked") return { tone: "disabled", dot: "", title: "Blocked" };
  if (["running", "executing", "processing"].includes(String(segment?.status || ""))) {
    return { tone: "running", dot: "", title: "正在生成" };
  }

  if (kind === "audio") {
    if (artifact.audio_in_working) return { tone: "done", dot: "", title: "Audio 已在 Working" };
    if (artifact.audio_copy_pending) return { tone: "pending", dot: "copy", title: "Audio 待拷贝到 Working" };
    if (artifact.audio_generate_pending || tasks.need_audio) return { tone: "pending", dot: "generate", title: "待生成 Audio" };
    if (Array.isArray(artifact.audio_files) && artifact.audio_files.length) return { tone: "pending", dot: "generate", title: "Dialogue Audio 缺失" };
    const hasWorkingAudio = audioTasks.some((item) => isWorkingPath(item?.existing_audio_path || item?.planned_audio_path));
    const hasAudio = audioTasks.some((item) => hasPlannedPath(item?.existing_audio_path || item?.planned_audio_path));
    if (hasWorkingAudio || isWorkingPath(outputs.segment_audio_path)) return { tone: "done", dot: "", title: "Audio 已在 Working" };
    if (hasAudio) return { tone: "pending", dot: "copy", title: "Audio 待拷贝到 Working" };
    return { tone: "disabled", dot: "", title: "不执行 Audio" };
  }

  if (kind === "image") {
    if (artifact.image_in_working && artifact.image_input_kind === "tail_frame_materialized") return { tone: "done", dot: "", title: "尾帧作为新图" };
    if (artifact.image_in_working) return { tone: "done", dot: "", title: "新图已绑定" };
    if (artifact.image_copy_pending && artifact.image_input_kind === "tail_frame_pending_copy") return { tone: "pending", dot: "copy", title: "执行时物化尾帧为新图" };
    if (artifact.image_copy_pending) return { tone: "pending", dot: "copy", title: "新图待拷贝到 Working" };
    if (firstFrame.source_type === "bound_video") return { tone: "disabled", dot: "", title: "绑定视频不执行新图" };
    if (artifact.image_generate_pending || tasks.need_image || tasks.need_image_prompt) return { tone: "pending", dot: "generate", title: "待生成新图" };
    if (materializeFrame.required) return { tone: "pending", dot: "copy", title: "新图待拷贝到 Working" };
    if (firstFrame.source_type === "previous_segment_tail_frame" || firstFrame.source_type === "previous_scene_tail_frame") {
      return { tone: "pending", dot: "generate", title: "等待上一个视频尾帧" };
    }
    return { tone: "disabled", dot: "", title: "不执行新图" };
  }

  if (kind === "video") {
    if (artifact.video_in_working) return { tone: "done", dot: "", title: "Raw Video 已在 Working" };
    if (artifact.video_copy_pending) return { tone: "pending", dot: "copy", title: "Raw Video 待拷贝到 Working" };
    if (artifact.video_generate_pending || tasks.need_video || tasks.need_video_prompt) return { tone: "pending", dot: "generate", title: "待生成 Raw Video" };
    if (firstFrame.source_type === "bound_video" || text(existingVideo.path)) return { tone: "disabled", dot: "", title: "绑定视频不执行 Raw Video" };
    const videoPath = outputs.video_path;
    if (materializeVideo.required || text(existingVideo.path)) return { tone: "pending", dot: "copy", title: "Raw Video 待拷贝到 Working" };
    if (isWorkingPath(videoPath) && segment?.status === "ready") return { tone: "pending", dot: "generate", title: "等待 Raw Video 文件" };
    return { tone: "disabled", dot: "", title: "不执行 Raw Video" };
  }

  return { tone: "disabled", dot: "", title: "" };
}

function syncStepLabel(segment = {}) {
  const tasks = segment?.tasks || {};
  if (tasks.need_audio_video_sync || tasks.sync_mode === "audio_replace_retime") return "音频合成";
  if (tasks.need_lipsync || tasks.sync_mode === "lipsync") return "音频匹配";
  return "Sync";
}

function imageStepLabel(segment = {}, artifact = {}) {
  if (artifact.image_input_kind === "tail_frame_materialized") return "尾帧作为新图";
  if (artifact.image_input_kind === "tail_frame_pending_copy") return "尾帧";
  const firstFrame = segment?.first_frame || {};
  const materializeFrame = firstFrame?.materialize_first_frame || {};
  const sourceType = text(materializeFrame.source_type || firstFrame.source_type);
  if (sourceType === "previous_segment_tail_frame" || sourceType === "previous_scene_tail_frame") return artifact.image_in_working ? "尾帧作为新图" : "尾帧";
  return "新图";
}

function stepRequired(segment, kind, artifact = {}) {
  const tasks = segment?.tasks || {};
  const outputs = segment?.planned_outputs || {};
  const firstFrame = segment?.first_frame || {};
  const materializeFrame = firstFrame?.materialize_first_frame || {};
  const existingVideo = segment?.existing_video || {};
  const materializeVideo = existingVideo?.materialize_video || {};
  const audioTasks = Array.isArray(segment?.dialogue_audio_tasks) ? segment.dialogue_audio_tasks : [];
  if (kind === "audio") {
    return Boolean(tasks.need_audio || audioTasks.length || outputs.segment_audio_path || artifact.audio_in_working || artifact.audio_copy_pending || artifact.audio_generate_pending);
  }
  if (kind === "image") {
    if (artifact.image_in_working || artifact.image_copy_pending || artifact.image_generate_pending) return true;
    if (firstFrame.source_type === "bound_video") return false;
    return Boolean(tasks.need_image || tasks.need_image_prompt || materializeFrame.required || firstFrame.source_path || outputs.image_path || artifact.image_in_working || artifact.image_copy_pending || artifact.image_generate_pending);
  }
  if (kind === "video") {
    if (!tasks.need_video && !tasks.need_video_prompt && (firstFrame.source_type === "bound_video" || text(existingVideo.path)) && !artifact.video_in_working && !artifact.video_copy_pending && !artifact.video_generate_pending) return false;
    return Boolean(tasks.need_video || tasks.need_video_prompt || materializeVideo.required || text(existingVideo.path) || outputs.video_path || artifact.video_in_working || artifact.video_copy_pending || artifact.video_generate_pending);
  }
  if (kind === "sync") {
    return Boolean(tasks.need_sync || tasks.need_lipsync || tasks.need_audio_video_sync || artifact.sync_in_working || artifact.sync_generate_pending);
  }
  return false;
}

function stepCompleted(kind, artifact = {}, executionStep = {}, segment = {}) {
  if (artifact?.[`${kind}_in_working`]) return true;
  if (["image", "video", "sync"].includes(kind)) return false;
  if (executionStep?.status === "completed_working") return true;
  if (executionStep?.status === "failed") return false;
  return false;
}

function segmentErrors(executionSegment = {}) {
  const errors = [];
  const steps = executionSegment?.steps || {};
  for (const [step, payload] of Object.entries(steps)) {
    if (text(payload?.status) !== "failed") continue;
    const message = redactError(payload?.error || executionSegment?.error);
    if (message) errors.push({ step, label: STEP_LABELS[step] || step, message });
  }
  const segmentError = redactError(executionSegment?.error);
  if (segmentError && !errors.some((item) => item.message === segmentError)) {
    errors.push({ step: "segment", label: STEP_LABELS.segment, message: segmentError });
  }
  return errors;
}

function TaskBadge(props) {
  const Icon = props.icon;
  const state = () => props.state || { tone: props.active ? "pending" : "disabled", dot: props.active ? "generate" : "", title: "" };
  return <div class={`kbsp-vpm-task-badge is-${state().tone}`} classList={{ "has-dot": Boolean(state().dot), "has-generate-dot": state().dot === "generate", "has-copy-dot": state().dot === "copy" }} title={state().title}>
    <Icon />
    <span>{props.label}</span>
  </div>;
}

function Arrow() {
  return <span class="kbsp-vpm-pipe-arrow">→</span>;
}

function prettyId(value, fallback, prefix) {
  const raw = String(value || fallback);
  const match = raw.match(/(\d+)$/);
  const index = match ? match[1].padStart(3, "0") : "001";
  return `${prefix}_${index}`;
}

const CONSISTENCY_LABELS = {
  host: "人物一致性",
  product: "产品一致性",
};

function consistencyMissingLabels(source) {
  const refs = Array.isArray(source?.references) ? source.references : [];
  const labels = [];
  const addLabel = (value) => {
    const label = String(value || "").trim();
    if (label && !labels.includes(label)) labels.push(label);
  };
  for (const item of refs) {
    if (item?.available === false) addLabel(item.label || CONSISTENCY_LABELS[item.kind] || item.kind);
  }
  const missing = Array.isArray(source?.missing) ? source.missing : [];
  for (const item of missing) {
    if (typeof item === "string") addLabel(CONSISTENCY_LABELS[item] || item);
    else addLabel(item?.label || CONSISTENCY_LABELS[item?.kind] || item?.kind);
  }
  return labels;
}

function blockedReasonText(item, fallback = "Blocked") {
  const reason = reasonText(item?.blocked_reason);
  if (reason) return reason;
  return text(item?.status) === "blocked" ? fallback : "";
}

function sceneEmptyText(scene) {
  return reasonText(scene?.blocked_reason) || reasonText(scene?.skipped_reason) || "No segments in this scene.";
}

export default function KouboVideoPlanModal(props) {
  const [agentOpen, setAgentOpen] = createSignal(false);
  const result = () => props.result?.() || {};
  const plan = () => props.result?.()?.plan || {};
  const summary = createMemo(() => plan().summary || {});
  const artifactStatus = createMemo(() => result().artifact_status?.segments || {});
  const executionState = createMemo(() => result().execution_state || {});
  const executionMatches = createMemo(() => Boolean(result().binding_status?.state_matches_current_plan));
  const executionRunning = createMemo(() => ["queued", "running"].includes(text(executionState().status)));
  const traceItems = createMemo(() => {
    const taskObj = typeof props.task === "function" ? props.task() : props.task;
    return [
      ["Task", taskObj?.id ? `#${taskObj.id}` : "-"],
      ["Session", taskObj?.session_id ? `#${taskObj.session_id}` : "-"],
      ["Req", targetScopeLabel(result().target || plan().target)],
      ["Plan", targetScopeLabel(plan().target || result().target)],
      ["Hash", shortHash(plan().plan_hash || result().plan_hash)],
    ];
  });
  const segmentExecution = (segmentId) => executionMatches() ? (executionState().segments?.[segmentId] || {}) : {};
  const stepExecution = (segmentId, step) => {
    if (!executionMatches()) return {};
    const nestedStep = segmentExecution(segmentId).steps?.[step] || {};
    const executionStatus = text(executionState().status);
    const isCurrentStep = ["queued", "running", "failed"].includes(executionStatus)
      && text(executionState().current_segment_id) === text(segmentId)
      && text(executionState().current_step) === text(step);
    if (!isCurrentStep) return nestedStep;
    if (executionStatus === "failed" && text(nestedStep.status) !== "completed_working") {
      return { ...nestedStep, status: "failed", error: text(executionState().error) || text(nestedStep.error) || "执行失败" };
    }
    const currentStatus = text(executionState().current_step_status) || text(nestedStep.status) || "running_generate";
    if (currentStatus.startsWith("running") || ["queued", "running", "processing", "executing"].includes(currentStatus)) {
      return { ...nestedStep, status: currentStatus };
    }
    return nestedStep;
  };

  function videoPlanAgentContext() {
    return {
      result: result(),
      target: result().target || plan().target || {},
      settings: result().settings || {},
      execution_running: executionRunning(),
    };
  }

  function videoPlanAgentChips() {
    return [
      { label: "Req", value: targetScopeLabel(result().target || plan().target) },
      { label: "Plan", value: targetScopeLabel(plan().target || result().target) },
      { label: "Hash", value: shortHash(plan().plan_hash || result().plan_hash) },
      { label: "Running", value: executionRunning() ? "是" : "否" },
    ];
  }

  function flattenAgentTarget(target = {}) {
    const targetType = text(target.target_type || target.scope || "task");
    if (targetType === "task" || targetType === "all") return { target_type: "task", shot_id: "", scene_id: "" };
    if (targetType === "shot") return { target_type: "shot", shot_id: text(target.shot_id), scene_id: "" };
    return { target_type: "scene", shot_id: text(target.shot_id), scene_id: text(target.scene_id) };
  }

  function renderVideoPlanAgentCandidate(candidate) {
    const payload = candidate.payload || {};
    if (candidate.kind !== "video_plan_action") {
      return <article class="kbsp-agent-candidate"><strong>{candidate.title}</strong><pre>{JSON.stringify(payload, null, 2)}</pre></article>;
    }
    const target = flattenAgentTarget(payload.target || result().target || plan().target || {});
    const settings = payload.settings && typeof payload.settings === "object" ? payload.settings : null;
    return <article class="kbsp-agent-candidate">
      <strong>{payload.title || "生成计划动作建议"}</strong>
      <Show when={payload.reason}><p>{payload.reason}</p></Show>
      <p>{targetScopeLabel(target)}</p>
      <div class="kbsp-agent-candidate-actions">
        <button type="button" class="secondary" disabled={!settings} onClick={() => void props.applySettings?.(settings)}>应用参数</button>
        <button type="button" onClick={() => void (async () => {
          if (!window.confirm("生成计划？")) return;
          if (settings) await props.applySettings?.(settings);
          await props.openVideoPlan?.({ ...target, force: true, action_source: "agent_candidate" });
        })()}>生成计划</button>
        <button type="button" disabled={executionRunning()} onClick={() => {
          if (!window.confirm("执行当前生成计划？")) return;
          void props.executePlan?.();
        }}>执行生成计划</button>
        <button type="button" class="secondary" onClick={() => void props.refreshExecution?.()}>刷新状态</button>
      </div>
    </article>;
  }

  createEffect(() => {
    if (!props.open?.()) return;
    void props.refreshExecution?.();
    const timer = window.setInterval(() => {
      const status = text((props.result?.() || {}).execution_state?.status);
      if (["queued", "running"].includes(status)) void props.refreshExecution?.();
    }, 1000);
    onCleanup(() => window.clearInterval(timer));
  });
  const topProgress = createMemo(() => {
    const counters = {
      audio: { done: 0, total: 0 },
      image: { done: 0, total: 0 },
      video: { done: 0, total: 0 },
      sync: { done: 0, total: 0 },
    };
    const keyByKind = { audio: "audio", image: "image", video: "video", sync: "sync" };
    for (const shot of plan().shots || []) {
      for (const scene of shot.scenes || []) {
        for (const segment of scene.segments || []) {
          const artifact = artifactStatus()[segment.segment_id] || {};
          const execSegment = segmentExecution(segment.segment_id);
          for (const [counterKey, stepKey] of Object.entries(keyByKind)) {
            const execStep = execSegment.steps?.[stepKey] || {};
            if (stepRequired(segment, stepKey, artifact)) {
              counters[counterKey].total += 1;
              if (stepCompleted(stepKey, artifact, execStep, segment)) counters[counterKey].done += 1;
            }
          }
        }
      }
    }
    return counters;
  });
  const notice = createMemo(() => {
    const currentLabels = consistencyMissingLabels(result().signature?.consistency_references);
    const planLabels = consistencyMissingLabels(plan().consistency_references);
    const labels = currentLabels.length ? currentLabels : planLabels;
    if (labels.length) {
      return `缺少${labels.join("、") || "一致性参考图"}，计划已按当前参考状态刷新。`;
    }
    const blocked = num(summary().blocked_scene_count);
    const skipped = num(summary().skipped_scene_count);
    if (blocked > 0) return `${blocked} scene blocked. The plan is visible, but these items need media or timing fixes before execution.`;
    if (skipped > 0) return `${skipped} scene skipped. The plan is visible and remaining items can be reviewed.`;
    return "";
  });

  return <Show when={props.open?.()}>
    <div class="kbsp-vpm-backdrop" role="dialog" aria-modal="true" aria-label="生成计划">
      <section class="kbsp-vpm-shell">
        <header class="kbsp-vpm-header">
          <div class="kbsp-vpm-title-wrap">
            <div class="kbsp-vpm-mark"><WorkflowIcon /></div>
            <div class="kbsp-vpm-title-copy">
              <h2>生成计划</h2>
              <div class="kbsp-vpm-summary-line" aria-label="生成计划范围">
                <span>Shot {summary().shot_count || 0}</span>
                <span>Scene {summary().scene_count || 0}</span>
                <span>Segment {summary().segment_count || 0}</span>
              </div>
            </div>
          </div>
          <div class="kbsp-vpm-dashboard" aria-label="生成计划指标">
            <div class="kbsp-vpm-dashboard-tasks">
              <span title="Audio Tasks" aria-label={`Audio Tasks ${topProgress().audio.done}/${topProgress().audio.total}`}><MicIcon /><em>{topProgress().audio.done}/{topProgress().audio.total}</em></span>
              <span title="Frame Tasks" aria-label={`Frame Tasks ${topProgress().image.done}/${topProgress().image.total}`}><ImageIcon /><em>{topProgress().image.done}/{topProgress().image.total}</em></span>
              <span title="Raw Video Tasks" aria-label={`Raw Video Tasks ${topProgress().video.done}/${topProgress().video.total}`}><ClapperboardIcon /><em>{topProgress().video.done}/{topProgress().video.total}</em></span>
              <span title="Sync Tasks" aria-label={`Sync Tasks ${topProgress().sync.done}/${topProgress().sync.total}`}><AudioLinesIcon /><em>{topProgress().sync.done}/{topProgress().sync.total}</em></span>
            </div>
          </div>
          <div class="kbsp-vpm-actions">
            <button type="button" aria-label="生成计划 Agent" title="生成计划 Agent" onClick={() => setAgentOpen(true)}><MessageIcon /></button>
            <button type="button" aria-label="关闭生成计划" onClick={() => props.setOpen?.(false)}><XIcon /></button>
          </div>
        </header>

        <Show when={notice()}>
          <div class="kbsp-vpm-notice">{notice()}</div>
        </Show>

        <div class="kbsp-trace-strip" aria-label="生成计划追踪标记">
          <For each={traceItems()}>{([label, value]) => <span><b>{label}</b>{value}</span>}</For>
        </div>

        <div class="kbsp-vpm-body">
          <Show when={(plan().shots || []).length} fallback={<div class="kbsp-vpm-empty">没有可展示的生成计划数据。</div>}>
            <For each={plan().shots || []}>{(shot, shotIndex) => <section class="kbsp-vpm-shot">
              <div class="kbsp-vpm-shot-head">
                <h3>{prettyId(shot.shot_id, `shot_${String(shotIndex() + 1).padStart(3, "0")}`, "Shot")}</h3>
                <Show when={shotIndex() === 0}>
                  <button type="button" class="kbsp-vpm-execute" aria-label="执行生成计划" disabled={executionRunning()} classList={{ "is-running": executionRunning() }} onClick={() => props.executePlan?.()}>
                    <PlayIcon />
                  </button>
                </Show>
              </div>
              <div class="kbsp-vpm-scenes">
                <For each={shot.scenes || []}>{(scene, sceneIndex) => <div class="kbsp-vpm-scene" classList={{ "has-execute": shotIndex() === 0 && sceneIndex() === 0 }}>
                  <div class="kbsp-vpm-rail-dot" />
                  <div class="kbsp-vpm-scene-content">
                    <div class="kbsp-vpm-scene-title">
                      <strong>{prettyId(scene.scene_id, `scene_${String(sceneIndex() + 1).padStart(3, "0")}`, "Scene")}</strong>
                    </div>
                    <For each={scene.segments || []}>{(segment) => {
                      const errors = createMemo(() => segmentErrors(segmentExecution(segment.segment_id)));
                      const blockedReason = createMemo(() => blockedReasonText(segment));
                      return <article class="kbsp-vpm-segment" classList={{ "has-error": Boolean(errors().length) }}>
                      <div class="kbsp-vpm-segment-row">
                        <div class="kbsp-vpm-segment-top">
                          <span>S{segment.segment_index || 1}</span>
                          <b>{num(segment.duration).toFixed(2)}s</b>
                        </div>
                        <div class="kbsp-vpm-pipeline">
                          <TaskBadge state={badgeState(segment, "audio", artifactStatus()[segment.segment_id] || {}, stepExecution(segment.segment_id, "audio"), segmentExecution(segment.segment_id))} icon={MicIcon} label="音频" />
                          <Arrow />
                          <TaskBadge state={badgeState(segment, "image", artifactStatus()[segment.segment_id] || {}, stepExecution(segment.segment_id, "image"), segmentExecution(segment.segment_id))} icon={ImageIcon} label={imageStepLabel(segment, artifactStatus()[segment.segment_id] || {})} />
                          <Arrow />
                          <TaskBadge state={badgeState(segment, "video", artifactStatus()[segment.segment_id] || {}, stepExecution(segment.segment_id, "video"), segmentExecution(segment.segment_id))} icon={ClapperboardIcon} label="新视频" />
                          <Arrow />
                          <TaskBadge state={badgeState(segment, "sync", artifactStatus()[segment.segment_id] || {}, stepExecution(segment.segment_id, "sync"), segmentExecution(segment.segment_id))} icon={AudioLinesIcon} label={syncStepLabel(segment)} />
                        </div>
                      </div>
                      <Show when={errors().length}>
                        <div class="kbsp-vpm-error-list" aria-label="Execution errors">
                          <For each={errors()}>{(item) => <p class="kbsp-vpm-error"><strong>{item.label}</strong><span>{item.message}</span></p>}</For>
                        </div>
                      </Show>
                      <Show when={blockedReason()}>
                        <p class="kbsp-vpm-blocked">{blockedReason()}</p>
                      </Show>
                    </article>;
                    }}</For>
                    <Show when={!(scene.segments || []).length}>
                      <div class="kbsp-vpm-scene-empty">{sceneEmptyText(scene)}</div>
                    </Show>
                  </div>
                </div>}</For>
              </div>
            </section>}</For>
          </Show>
        </div>
      </section>
      <KouboAgentDrawer
        open={agentOpen}
        setOpen={setAgentOpen}
        task={props.task}
        api={props.api}
        agentKey="video_plan"
        title="生成计划 Agent"
        subtitle="计划、参数和执行状态助手"
        greeting="我可以帮你解释生成计划的 blocked/failed 原因，并给出重跑或执行建议。"
        placeholder="例如：为什么当前计划只覆盖一个 scene？下一步应该怎么做？"
        contextChips={videoPlanAgentChips}
        buildClientContext={videoPlanAgentContext}
        renderCandidate={renderVideoPlanAgentCandidate}
      />
    </div>
  </Show>;
}
