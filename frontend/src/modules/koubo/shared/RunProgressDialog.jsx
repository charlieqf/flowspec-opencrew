import { For, Show, createSignal } from "solid-js";
import { CloseIcon, PlayClipIcon } from "../AnalysisV1/analysisV1Icons.jsx";
import StoryboardIcon from "./StoryboardIcon.jsx";
import "./RunProgressDialog.css";

function finiteDurationSeconds(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? Math.max(0, number) : null;
}

function timestampDurationSeconds(startedAt, finishedAt) {
  const started = Number(startedAt || 0);
  const finished = Number(finishedAt || 0);
  if (started > 0 && finished > 0 && finished >= started) {
    return (finished - started) / 1000;
  }
  return null;
}

export function formatRunDuration(value) {
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

export function stepDuration(step) {
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

export function stepDurationSeconds(step) {
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

export function defaultStepStatusLabel(status) {
  const value = String(status || "").toLowerCase();
  return {
    pending: "等待",
    queued: "排队",
    reused: "复用",
    running: "运行中",
    completed: "完成",
    failed: "失败",
    blocked: "阻断",
    skipped: "跳过",
    cancelled: "已取消",
    stale_running: "失联",
    unavailable: "不可用",
  }[value] || value || "等待";
}

export function defaultStatusTone(status) {
  const value = String(status || "").toLowerCase();
  if (["completed", "ready", "reused"].includes(value)) return "ready";
  if (["running", "queued", "paused", "stopping"].includes(value)) return "running";
  if (["failed", "blocked", "cancelled", "error"].includes(value)) return "failed";
  return "idle";
}

export function defaultStepDisplayName(step) {
  return step?.display_name_zh || step?.display_name || step?.name || step?.id || "-";
}

export default function RunProgressDialog(props) {
  const [dialogPosition, setDialogPosition] = createSignal(null);
  const steps = () => Array.isArray(props.steps) && props.steps.length ? props.steps : Array.isArray(props.planSteps) ? props.planSteps : [];
  const statusTone = () => props.statusTone || defaultStatusTone;
  const statusLabel = () => props.stepStatusLabel || defaultStepStatusLabel;
  const displayName = () => props.stepDisplayName || defaultStepDisplayName;
  const totalDuration = () => finiteDurationSeconds(props.totalDurationSeconds) ?? timestampDurationSeconds(props.startedAt, props.finishedAt) ?? steps().reduce((total, step) => total + stepDurationSeconds(step), 0);

  function dialogStyle() {
    const position = dialogPosition();
    if (!position) return {};
    return { left: `${position.left}px`, top: `${position.top}px`, transform: "none" };
  }

  function startDialogDrag(event) {
    const target = event.target instanceof Element ? event.target : event.currentTarget;
    if (event.button !== 0 || target.closest("button,input,select,textarea")) return;
    const dialog = event.currentTarget.closest(".analysis-v1-run-progress-dialog");
    const rect = dialog?.getBoundingClientRect();
    if (!rect) return;
    event.preventDefault();
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

  return (
    <Show when={props.open}>
      <div class="drawer-backdrop analysis-v1-run-progress-backdrop" onClick={() => props.closeOnBackdrop !== false && props.onClose?.()} />
      <section class={`verify-dialog analysis-v1-run-progress-dialog ${props.dialogClass || ""}`} style={dialogStyle()}>
        <div class="analysis-v1-run-progress-head" onPointerDown={startDialogDrag}>
          <div class="analysis-v1-run-context-tags analysis-v1-run-progress-tags">
            <Show when={props.taskId}><span>任务 #{props.taskId}</span></Show>
            <Show when={props.sessionId}><span>会话 #{props.sessionId}</span></Show>
            <Show when={props.attemptId}><span>尝试 #{props.attemptId}</span></Show>
          </div>
          <div class="analysis-v1-run-commandbar">
            <button class="primary icon-action analysis-v1-run-start" type="button" title={props.startTitle || "运行"} aria-label={props.startTitle || "运行"} disabled={props.startDisabled} onClick={() => props.onStart?.()}>
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
        <div class="analysis-v1-run-progress-body">
          <div class="analysis-v1-run-indicator-grid">
            <div class="analysis-v1-run-step-list">
              <Show when={steps().length} fallback={<div class="analysis-v1-empty">尚未运行</div>}>
                <For each={steps()}>{(step, index) => {
                  const tone = statusTone()(step.status);
                  const rawStatus = String(step.status || "").toLowerCase();
                  const showStepMessage = Boolean(step.message && ["failed", "blocked", "cancelled", "stale_running"].includes(rawStatus));
                  return (
                    <div class={`analysis-v1-run-step is-${tone}`}>
                      <span class="analysis-v1-run-step-index">{index() + 1}</span>
                      <div class="analysis-v1-run-step-main">
                        <strong>{displayName()(step)}</strong>
                      </div>
                      <span class={`analysis-v1-run-step-status tag-${tone === "ready" ? "ready" : tone === "running" ? "available" : tone === "failed" ? "failed" : "idle"}`}>{statusLabel()(step.status)}</span>
                      <span class="analysis-v1-run-step-duration">{stepDuration(step)}</span>
                      <Show when={showStepMessage}>
                        <small>{step.message}</small>
                      </Show>
                    </div>
                  );
                }}</For>
              </Show>
            </div>
          </div>
          <Show when={props.message}>
            <div class={`analysis-v1-run-progress-message is-${statusTone()(props.status)}`}>
              {props.message}
            </div>
          </Show>
          <Show when={props.children}>
            <div class="shared-run-progress-extra">
              {props.children}
            </div>
          </Show>
          <div class="analysis-v1-run-total-duration">总耗时 {formatRunDuration(totalDuration())}</div>
        </div>
      </section>
    </Show>
  );
}
