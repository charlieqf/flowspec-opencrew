import { For, Show, createEffect, createMemo, createSignal, onCleanup } from "solid-js";
import { ImageIcon, MessageIcon, PlayIcon, WorkflowIcon, XIcon } from "../kouboStoryboardIcons.jsx";
import KouboAgentDrawer from "./KouboAgentDrawer.jsx";

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

function list(value) {
  return Array.isArray(value) ? value : [];
}

function num(value, fallback = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function prettyId(value, fallback, prefix) {
  const raw = String(value || fallback);
  const match = raw.match(/(\d+)$/);
  const index = match ? match[1].padStart(3, "0") : "001";
  return `${prefix}_${index}`;
}

function redactError(value) {
  return text(value)
    .replace(/([?&]key=)[^&\s"'}]+/gi, "$1***")
    .replace(/(api[_-]?key["']?\s*[:=]\s*["']?)[^"',}\s]+/gi, "$1***")
    .replace(/(Authorization["']?\s*[:=]\s*["']?\s*Bearer\s+)[^"',}\s]+/gi, "$1***");
}

function splitPromptFallback(value) {
  const raw = text(value);
  if (!raw) return { positive: "", negative: "" };
  const match = raw.match(/\n\s*Negative prompt\s*:\s*/i);
  if (!match || match.index === undefined) return { positive: raw, negative: "" };
  return {
    positive: raw.slice(0, match.index).trim(),
    negative: raw.slice(match.index + match[0].length).trim(),
  };
}

function buildPromptText(positive, negative) {
  const pos = text(positive);
  const neg = text(negative);
  return neg ? `${pos}\n\nNegative prompt:\n${neg}` : pos;
}

function normalizedPromptFields(prompt) {
  const source = prompt && typeof prompt === "object" && !Array.isArray(prompt) ? prompt : {};
  const fallback = splitPromptFallback(source.prompt);
  return {
    positive: text(source.positive_prompt) || fallback.positive,
    negative: text(source.negative_prompt) || fallback.negative,
  };
}

function fileName(path) {
  const parts = text(path).split("/").filter(Boolean);
  return parts[parts.length - 1] || "";
}

function matchingReference(prompt, matchers) {
  const refs = list(prompt?.reference_images);
  return refs.find((item) => {
    const haystack = `${text(item?.kind)} ${text(item?.role)} ${text(item?.label)}`.toLowerCase();
    return matchers.some((matcher) => haystack.includes(matcher));
  }) || {};
}

function referenceInfo(prompt, directKey, matchers) {
  const direct = text(prompt?.[directKey]);
  const found = matchingReference(prompt, matchers);
  const working = text(found?.working_path);
  const source = text(found?.source_path);
  const fallback = text(found?.path);
  return {
    path: direct || working || source || fallback,
    previewPath: source || working || direct || fallback,
  };
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
  return shots.map((shot) => ({
    shot_id: shot.shot_id,
    scenes: shot.scenes,
  }));
}

function plannedOutput(task, key) {
  return text(task?.planned_outputs?.[key]);
}

function imageTaskExecutable(task) {
  const status = text(task?.status);
  return Boolean(
    text(task?.asset_key)
    && plannedOutput(task, "image_path")
    && status !== "skipped"
    && !status.startsWith("blocked")
  );
}

function blockedReasonText(item, fallback = "Blocked") {
  const reason = reasonText(item?.blocked_reason);
  if (reason) return reason;
  return text(item?.status).startsWith("blocked") ? fallback : "";
}

function promptSlotCount(tasks) {
  return tasks.filter((task) => Boolean(imageTaskExecutable(task) && plannedOutput(task, "image_prompt_path"))).length;
}

function imageSlotCount(tasks) {
  return tasks.filter((task) => imageTaskExecutable(task)).length;
}

function taskArtifact(result, task) {
  const artifactTasks = result?.artifact_status?.tasks || [];
  const taskId = text(task.image_task_id);
  const assetKey = text(task.asset_key);
  return artifactTasks.find((item) => text(item.image_task_id) === taskId) || artifactTasks.find((item) => text(item.asset_key) === assetKey) || {};
}

function stateStepCompletedCount(executionState, stepName) {
  const tasks = executionState?.tasks && typeof executionState.tasks === "object" && !Array.isArray(executionState.tasks) ? executionState.tasks : {};
  return Object.values(tasks).filter((taskState) => {
    const steps = taskState?.steps && typeof taskState.steps === "object" && !Array.isArray(taskState.steps) ? taskState.steps : {};
    return text(steps?.[stepName]?.status) === "completed_working";
  }).length;
}

function stateTaskExecution(executionState, task = {}) {
  const tasks = executionState?.tasks && typeof executionState.tasks === "object" && !Array.isArray(executionState.tasks) ? executionState.tasks : {};
  const taskId = text(task?.image_task_id);
  const assetKey = text(task?.asset_key);
  return (assetKey && tasks[assetKey]) || (taskId && tasks[taskId]) || {};
}

function executionTargetMatches(executionState, task = {}, idKey = "target_task_id", assetKeyName = "target_asset_key") {
  const targetTaskId = text(executionState?.[idKey]);
  const targetAssetKey = text(executionState?.[assetKeyName]);
  return !(targetTaskId || targetAssetKey) || targetTaskId === text(task?.image_task_id) || targetAssetKey === text(task?.asset_key);
}

function stateStepStatus(executionState, task = {}, stepName = "") {
  const taskExecution = stateTaskExecution(executionState, task);
  const steps = taskExecution?.steps && typeof taskExecution.steps === "object" && !Array.isArray(taskExecution.steps) ? taskExecution.steps : {};
  return text(steps?.[stepName]?.status);
}

function isRunningStepStatus(status) {
  const value = text(status);
  return value === "running" || value.startsWith("running_");
}

function stepRunning(executionState, stepName, task = {}) {
  if (!["queued", "running"].includes(text(executionState?.status))) return false;
  const currentStep = text(executionState?.current_step);
  const currentStepStatus = text(executionState?.current_step_status);
  const currentMatches = executionTargetMatches(executionState, task, "current_task_id", "current_asset_key");
  if (currentMatches && currentStep === stepName && (!currentStepStatus || isRunningStepStatus(currentStepStatus))) return true;
  return isRunningStepStatus(stateStepStatus(executionState, task, stepName));
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

function stepState(task, stepName, executionState = {}, artifact = {}) {
  const planStep = task?.steps?.[stepName] || {};
  const taskState = stateTaskExecution(executionState, task);
  const stateStatus = stateStepStatus(executionState, task, stepName);
  const taskStatus = text(taskState.status);
  const slotKey = stepName === "prompt" ? "image_prompt" : stepName;
  const rawSlot = artifact.slot_states?.[slotKey];
  const fromSlot = slotBadgeState(rawSlot);
  if (fromSlot?.tone === "done") return fromSlot;
  if (fromSlot?.tone === "disabled" && text(rawSlot?.reason) === "skipped_consumed_by_downstream") return fromSlot;
  if (stepRunning(executionState, stepName, task)) return { tone: "running", dot: "", title: stepName === "prompt" ? "正在生成 Prompt" : "正在生成 Image" };
  if (stateStatus === "failed") return { tone: "failed", dot: "", title: "执行失败" };
  if (fromSlot) return fromSlot;
  if (!imageTaskExecutable(task) && artifact.executable_task !== true) return { tone: "disabled", dot: "", title: "不执行" };
  if (stepName === "image") {
    if (artifact.image_in_storyboard || artifact.image_in_working) return { tone: "done", dot: "", title: "新图已绑定" };
    if (artifact.planned_image_exists) return { tone: "pending", dot: "generate", title: "新图槽位未绑定" };
    if (planStep.required || imageTaskExecutable(task)) return { tone: "pending", dot: "generate", title: "待生成 Image" };
    return { tone: "disabled", dot: "", title: "不执行" };
  }
  if (stepName === "prompt" && artifact.prompt_in_working) return { tone: "done", dot: "", title: "Prompt 已在 Working" };
  if (stateStatus === "completed_working" && stepName !== "prompt") return { tone: "done", dot: "", title: "已生成" };
  if (taskStatus === "failed") return { tone: "failed", dot: "", title: redactError(taskState.error) || "执行失败" };
  if (taskStatus === "blocked") return { tone: "disabled", dot: "", title: redactError(taskState.error) || "Blocked" };
  if (taskStatus === "skipped") return { tone: "disabled", dot: "", title: "本次不执行" };
  if (planStep.required) return { tone: "pending", dot: "generate", title: stepName === "prompt" ? "待生成 Prompt" : "待生成 Image" };
  if (text(task.status) === "blocked") return { tone: "disabled", dot: "", title: text(task.blocked_reason) || "Blocked" };
  return { tone: "disabled", dot: "", title: "不执行" };
}

function TaskBadge(props) {
  const Icon = props.icon;
  const state = () => props.state || { tone: "disabled", dot: "", title: "" };
  const badgeClassList = createMemo(() => ({
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
  return <Show when={props.onClick} fallback={<div class="kbsp-vpm-task-badge" classList={badgeClassList()} title={state().title}>{body}</div>}>
    <button type="button" class="kbsp-vpm-task-badge" classList={badgeClassList()} title={props.title || state().title} onClick={props.onClick}>{body}</button>
  </Show>;
}

function Arrow() {
  return <span class="kbsp-vpm-pipe-arrow">→</span>;
}

export default function KouboImagePlanModal(props) {
  const [promptEditor, setPromptEditor] = createSignal({ open: false, assetKey: "", path: "", prompt: {}, positivePrompt: "", negativePrompt: "", status: "", error: "", saving: false });
  const [agentOpen, setAgentOpen] = createSignal(false);
  const result = () => props.result?.() || {};
  const plan = () => result().plan || {};
  const tasks = createMemo(() => Array.isArray(plan().image_tasks) ? plan().image_tasks : []);
  const summary = createMemo(() => plan().summary || {});
  const executionState = createMemo(() => result().execution_state || {});
  const executionMatches = createMemo(() => Boolean(result().binding_status?.state_matches_current_plan));
  const boundExecutionState = createMemo(() => executionMatches() ? executionState() : {});
  const executionRunning = createMemo(() => ["queued", "running"].includes(text(boundExecutionState().status)));
  const grouped = createMemo(() => groupTasks(tasks()));
  const sessionId = createMemo(() => num(props.task?.()?.session_id));
  const promptReferences = createMemo(() => {
    const prompt = promptEditor().prompt || {};
    const items = [
      { key: "target", label: "Target Frame", note: "画面替换锚点", ...referenceInfo(prompt, "target_frame_path", ["target", "frame"]) },
      { key: "host", label: "HOST Reference", note: "人物一致性参考", ...referenceInfo(prompt, "host_reference_path", ["host", "character", "person"]) },
      { key: "product", label: "Product Reference", note: "产品一致性参考", ...referenceInfo(prompt, "product_reference_path", ["product"]) },
    ];
    return items.map((item) => ({
      ...item,
      fileName: fileName(item.path),
      url: item.previewPath && sessionId() && props.api?.rawFileUrl ? props.api.rawFileUrl(sessionId(), item.previewPath) : "",
    }));
  });
  const referenceSummary = createMemo(() => {
    const liveRefs = result().consistency_references;
    if (liveRefs && Array.isArray(liveRefs.references)) {
      const byKind = new Map(liveRefs.references.map((item) => [text(item.kind), item]));
      const host = Boolean(byKind.get("host")?.available || text(byKind.get("host")?.output_path));
      const product = Boolean(byKind.get("product")?.available || text(byKind.get("product")?.output_path));
      return { host, product };
    }
    let host = false;
    let product = false;
    for (const task of tasks()) {
      const refs = task?.references || {};
      const hostRef = refs.host_reference || refs.character_reference || {};
      const productRef = refs.product_reference || {};
      host = host || Boolean(hostRef.exists || text(hostRef.path));
      product = product || Boolean(productRef.exists || text(productRef.path));
    }
    return { host, product };
  });
  const referenceWarnings = createMemo(() => {
    const summary = referenceSummary();
    const warnings = [];
    if (!summary.host) warnings.push("人物一致性图");
    if (!summary.product) warnings.push("产品一致性图");
    return warnings;
  });
  const progress = createMemo(() => {
    const execSummary = boundExecutionState().summary || {};
    const artifactSummary = result().artifact_status?.summary || {};
    const promptDone = Math.max(num(artifactSummary.prompt_completed_count), num(execSummary.prompt_completed_count), stateStepCompletedCount(boundExecutionState(), "prompt"));
    const imageDone = num(artifactSummary.image_completed_count);
    const promptTotal = Math.max(num(summary().planned_prompt_tasks), promptSlotCount(tasks()), promptDone);
    const imageTotal = Math.max(num(summary().planned_image_tasks), imageSlotCount(tasks()), imageDone);
    return {
      prompt: {
        done: promptDone,
        total: promptTotal,
      },
      image: {
        done: imageDone,
        total: imageTotal,
      },
    };
  });
  createEffect(() => {
    if (!props.open?.()) return;
    void props.refreshExecution?.();
    const timer = window.setInterval(() => {
      const payload = props.result?.() || {};
      const status = text(payload.execution_state?.status);
      if (payload.binding_status?.state_matches_current_plan && ["queued", "running"].includes(status)) void props.refreshExecution?.();
    }, 1000);
    onCleanup(() => window.clearInterval(timer));
  });

  async function openPromptEditor(task) {
    const assetKey = text(task?.asset_key);
    if (!assetKey) return;
    setPromptEditor({ open: true, assetKey, path: "", prompt: {}, positivePrompt: "", negativePrompt: "", status: "Loading Prompt...", error: "", saving: false });
    try {
      const taskId = props.task?.()?.id;
      if (!taskId) throw new Error("No task selected");
      const response = await props.api?.imagePlanPrompt?.(taskId, assetKey);
      const prompt = response?.prompt && typeof response.prompt === "object" && !Array.isArray(response.prompt) ? response.prompt : {};
      const fields = normalizedPromptFields(prompt);
      setPromptEditor({
        open: true,
        assetKey,
        path: response?.path || "",
        prompt,
        positivePrompt: fields.positive,
        negativePrompt: fields.negative,
        status: "",
        error: "",
        saving: false,
      });
    } catch (error) {
      setPromptEditor((current) => ({
        ...current,
        status: "",
        error: error instanceof Error ? error.message : String(error || "Prompt load failed"),
      }));
    }
  }

  async function savePromptEditor() {
    const current = promptEditor();
    if (!text(current.positivePrompt)) {
      setPromptEditor((previous) => ({ ...previous, error: "Positive Prompt 不能为空。" }));
      return;
    }
    const payload = {
      ...(current.prompt || {}),
      positive_prompt: text(current.positivePrompt),
      negative_prompt: text(current.negativePrompt),
      prompt: buildPromptText(current.positivePrompt, current.negativePrompt),
    };
    setPromptEditor((previous) => ({ ...previous, saving: true, error: "", status: "Saving Prompt..." }));
    try {
      const taskId = props.task?.()?.id;
      if (!taskId) throw new Error("No task selected");
      const response = await props.api?.saveImagePlanPrompt?.(taskId, current.assetKey, {
        prompt: payload,
      });
      void props.refreshExecution?.();
      setPromptEditor({
        open: true,
        assetKey: current.assetKey,
        path: response?.path || current.path,
        prompt: response?.prompt || payload,
        positivePrompt: normalizedPromptFields(response?.prompt || payload).positive,
        negativePrompt: normalizedPromptFields(response?.prompt || payload).negative,
        status: "Saved",
        error: "",
        saving: false,
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error || "Prompt save failed");
      props.setError?.(message);
      setPromptEditor((previous) => ({ ...previous, saving: false, status: "", error: message }));
    }
  }

  function closePromptEditor() {
    setPromptEditor({ open: false, assetKey: "", path: "", prompt: {}, positivePrompt: "", negativePrompt: "", status: "", error: "", saving: false });
  }

  function taskForAssetKey(assetKey) {
    const key = text(assetKey);
    return tasks().find((item) => text(item.asset_key) === key) || null;
  }

  async function fillPromptCandidate(candidatePayload) {
    const assetKey = text(candidatePayload?.asset_key);
    const targetTask = taskForAssetKey(assetKey);
    if (!targetTask) throw new Error(`Image task not found: ${assetKey}`);
    await openPromptEditor(targetTask);
    setPromptEditor((current) => ({
      ...current,
      open: true,
      assetKey,
      positivePrompt: text(candidatePayload.positive_prompt),
      negativePrompt: text(candidatePayload.negative_prompt),
      status: "已填入 Agent 草稿",
      error: "",
    }));
    return targetTask;
  }

  function imagePlanAgentContext() {
    return {
      result: result(),
      selected_asset_key: promptEditor().assetKey,
      prompt_editor_open: promptEditor().open,
      execution_running: executionRunning(),
    };
  }

  function imagePlanAgentChips() {
    return [
      { label: "Task", value: String(summary().total_tasks || tasks().length || 0) },
      { label: "Prompt", value: `${progress().prompt.done}/${progress().prompt.total}` },
      { label: "Image", value: `${progress().image.done}/${progress().image.total}` },
      { label: "Running", value: executionRunning() ? "是" : "否" },
    ];
  }

  function renderImagePlanAgentCandidate(candidate, helpers) {
    const payload = candidate.payload || {};
    if (candidate.kind === "image_plan_candidate") {
      const targetTask = () => taskForAssetKey(payload.asset_key);
      return <article class="kbsp-agent-candidate">
        <strong>{payload.title || "Image Prompt 建议"}</strong>
        <Show when={payload.notes}><p>{payload.notes}</p></Show>
        <p>{payload.asset_key}</p>
        <div class="kbsp-agent-candidate-actions">
          <button type="button" class="secondary" disabled={!targetTask()} onClick={() => void openPromptEditor(targetTask())}>打开 Prompt 编辑器</button>
          <button type="button" disabled={!targetTask()} onClick={() => void fillPromptCandidate(payload).catch((error) => helpers.setError?.(error.message))}>填入 Prompt</button>
          <button type="button" disabled={!targetTask()} onClick={() => void (async () => {
            if (!window.confirm("保存 Agent 填入的 Prompt？")) return;
            try {
              await fillPromptCandidate(payload);
              await savePromptEditor();
            } catch (error) {
              helpers.setError?.(error instanceof Error ? error.message : String(error));
            }
          })()}>保存 Prompt</button>
          <button type="button" disabled={!targetTask() || executionRunning()} onClick={() => {
            if (!window.confirm("生成当前 Image？")) return;
            void props.executePlan?.("image-only", targetTask());
          }}>生成当前 Image</button>
        </div>
      </article>;
    }
    if (candidate.kind === "image_plan_action") {
      const mode = ["prompt-only", "image-only", "prompt-and-image"].includes(text(payload.mode)) ? text(payload.mode) : "prompt-only";
      const targetTask = () => taskForAssetKey(payload.target_asset_key);
      return <article class="kbsp-agent-candidate">
        <strong>{payload.title || "图像计划动作建议"}</strong>
        <Show when={payload.reason}><p>{payload.reason}</p></Show>
        <div class="kbsp-agent-candidate-actions">
          <button type="button" disabled={executionRunning()} onClick={() => {
            if (!window.confirm(`执行图像计划：${mode}？`)) return;
            void props.executePlan?.(mode, targetTask());
          }}>执行 {mode}</button>
          <button type="button" class="secondary" onClick={() => void props.refreshExecution?.()}>刷新状态</button>
        </div>
      </article>;
    }
    return <article class="kbsp-agent-candidate"><strong>{candidate.title}</strong><pre>{JSON.stringify(payload, null, 2)}</pre></article>;
  }

  return <Show when={props.open?.()}>
    <div class="kbsp-vpm-backdrop" role="dialog" aria-modal="true" aria-label="图像计划">
      <section class="kbsp-vpm-shell kbsp-ipm-shell">
        <header class="kbsp-vpm-header">
          <div class="kbsp-vpm-title-wrap">
            <div class="kbsp-vpm-mark"><ImageIcon /></div>
            <div class="kbsp-vpm-title-copy">
              <h2>图像计划</h2>
              <div class="kbsp-vpm-summary-line" aria-label="图像计划范围">
                <span>Task {summary().total_tasks || 0}</span>
                <span>Prompt {summary().planned_prompt_tasks || 0}</span>
                <span>Image {summary().planned_image_tasks || 0}</span>
              </div>
            </div>
          </div>
          <div class="kbsp-vpm-dashboard" aria-label="图像计划指标">
            <div class="kbsp-vpm-dashboard-tasks">
              <span title="Prompt Tasks" aria-label={`Prompt Tasks ${progress().prompt.done}/${progress().prompt.total}`}><WorkflowIcon /><em>{progress().prompt.done}/{progress().prompt.total}</em></span>
              <span title="Image Tasks" aria-label={`Image Tasks ${progress().image.done}/${progress().image.total}`}><ImageIcon /><em>{progress().image.done}/{progress().image.total}</em></span>
            </div>
          </div>
          <div class="kbsp-vpm-actions">
            <button type="button" aria-label="图像计划 Agent" title="图像计划 Agent" onClick={() => setAgentOpen(true)}><MessageIcon /></button>
            <button type="button" aria-label="关闭图像计划" onClick={() => props.setOpen?.(false)}><XIcon /></button>
          </div>
        </header>

        <Show when={result().execution_warning}>
          <div class="kbsp-ipm-inline-warning" role="status">{result().execution_warning}</div>
        </Show>

        <Show when={referenceWarnings().length}>
          <div class="kbsp-ipm-controls" aria-label="图像计划控制">
            <div class="kbsp-ipm-reference-row" aria-label="图像计划缺失一致性参考">
              <For each={referenceWarnings()}>{(label) =>
                <span class="is-missing"><ImageIcon />{label} <b>无</b></span>
              }</For>
            </div>
          </div>
        </Show>

        <div class="kbsp-vpm-body">
          <Show when={grouped().length} fallback={<div class="kbsp-vpm-empty">没有可展示的图像计划数据。</div>}>
            <For each={grouped()}>{(shot, shotIndex) => <section class="kbsp-vpm-shot">
              <div class="kbsp-vpm-shot-head">
                <h3>{prettyId(shot.shot_id, `shot_${String(shotIndex() + 1).padStart(3, "0")}`, "Shot")}</h3>
                <Show when={shotIndex() === 0}>
                  <div class="kbsp-ipm-shot-actions" aria-label="图像计划执行操作">
                    <button type="button" disabled={executionRunning()} onClick={() => props.executePlan?.("prompt-only")}><span class="kbsp-ipm-action-icon"><PlayIcon /></span><span>提示词</span></button>
                    <button type="button" disabled={executionRunning()} onClick={() => props.executePlan?.("image-only")}><span class="kbsp-ipm-action-icon"><PlayIcon /></span><span>新图</span></button>
                    <button type="button" disabled={executionRunning()} onClick={() => props.executePlan?.("prompt-and-image")}><span class="kbsp-ipm-action-icon"><PlayIcon /></span><span>提示词+新图</span></button>
                  </div>
                </Show>
              </div>
              <div class="kbsp-vpm-scenes">
                <For each={shot.scenes || []}>{(scene, sceneIndex) => <div class="kbsp-vpm-scene" classList={{ "has-execute": shotIndex() === 0 && sceneIndex() === 0 }}>
                  <div class="kbsp-vpm-rail-dot" />
                  <div class="kbsp-vpm-scene-content">
                    <div class="kbsp-vpm-scene-title">
                      <strong>{prettyId(scene.scene_id, `scene_${String(sceneIndex() + 1).padStart(3, "0")}`, "Scene")}</strong>
                    </div>
                    <For each={scene.tasks || []}>{(task, taskIndex) => {
                      const taskState = createMemo(() => stateTaskExecution(boundExecutionState(), task));
                      const artifact = createMemo(() => taskArtifact(result(), task));
                      const error = createMemo(() => redactError(taskState().error));
                      const blockedReason = createMemo(() => blockedReasonText(task));
                      const promptReady = createMemo(() => artifact().prompt_in_working);
                      const imageSlotPending = createMemo(() => text(artifact().slot_states?.image?.ui_tone || artifact().slot_states?.image?.tone) === "pending");
                      const canEditPrompt = createMemo(() => (imageTaskExecutable(task) || artifact().executable_task === true) && promptReady());
                      const canExecuteImage = createMemo(() => imageSlotPending() && promptReady() && !executionRunning());
                      return <article class="kbsp-vpm-segment" classList={{ "has-error": Boolean(error()) }}>
                        <div class="kbsp-ipm-segment-row">
                          <span class="kbsp-ipm-segment-index">S{taskIndex() + 1}</span>
                          <div class="kbsp-vpm-pipeline">
                            <TaskBadge state={stepState(task, "prompt", boundExecutionState(), artifact())} icon={WorkflowIcon} label="提示词" title="查看和编辑当前 Prompt" onClick={canEditPrompt() ? () => void openPromptEditor(task) : null} />
                            <Arrow />
                            <TaskBadge state={stepState(task, "image", boundExecutionState(), artifact())} icon={ImageIcon} label="新图" title="生成当前 Image" onClick={canExecuteImage() ? () => props.executePlan?.("image-only", task) : null} />
                          </div>
                        </div>
                        <Show when={error()}>
                          <div class="kbsp-vpm-error-list" aria-label="Execution errors">
                            <p class="kbsp-vpm-error"><strong>图像计划</strong><span>{error()}</span></p>
                          </div>
                        </Show>
                        <Show when={blockedReason()}>
                          <p class="kbsp-vpm-blocked">{blockedReason()}</p>
                        </Show>
                      </article>;
                    }}</For>
                  </div>
                </div>}</For>
              </div>
            </section>}</For>
          </Show>
        </div>
      </section>
      <Show when={promptEditor().open}>
        <div class="kbsp-ipm-prompt-editor" role="dialog" aria-modal="true" aria-label="Image Prompt Editor">
          <header>
            <div>
              <h3>Image Prompt</h3>
              <p>{promptEditor().assetKey}</p>
            </div>
            <button type="button" aria-label="Close Prompt" onClick={closePromptEditor}><XIcon /></button>
          </header>
          <section class="kbsp-ipm-prompt-refs" aria-label="Prompt references">
            <For each={promptReferences()}>{(item) => <article class="kbsp-ipm-prompt-ref" classList={{ "is-missing": !item.path }}>
              <div class="kbsp-ipm-prompt-ref-thumb">
                <Show when={item.url} fallback={<span>未使用</span>}>
                  <img src={item.url} alt={item.label} loading="lazy" />
                </Show>
              </div>
              <div class="kbsp-ipm-prompt-ref-copy">
                <strong>{item.label}</strong>
                <em>{item.note}</em>
                <span title={item.path || "未使用"}>{item.fileName || "未使用"}</span>
              </div>
            </article>}</For>
          </section>
          <section class="kbsp-ipm-prompt-fields" aria-label="Prompt fields">
            <label class="kbsp-ipm-prompt-field">
              <span>Positive Prompt</span>
              <textarea class="kbsp-ipm-prompt-textarea" value={promptEditor().positivePrompt} spellcheck={false} onInput={(event) => setPromptEditor((current) => ({ ...current, positivePrompt: event.currentTarget.value, status: "", error: "" }))} />
            </label>
            <label class="kbsp-ipm-prompt-field">
              <span>Negative Prompt</span>
              <textarea class="kbsp-ipm-prompt-textarea" value={promptEditor().negativePrompt} spellcheck={false} onInput={(event) => setPromptEditor((current) => ({ ...current, negativePrompt: event.currentTarget.value, status: "", error: "" }))} />
            </label>
          </section>
          <Show when={promptEditor().error}>
            <p class="kbsp-ipm-prompt-error">{promptEditor().error}</p>
          </Show>
          <Show when={promptEditor().status}>
            <p class="kbsp-ipm-prompt-status">{promptEditor().status}</p>
          </Show>
          <footer>
            <button type="button" class="secondary" disabled={promptEditor().saving} onClick={closePromptEditor}>取消</button>
            <button type="button" disabled={promptEditor().saving || !text(promptEditor().positivePrompt)} onClick={() => void savePromptEditor()}>{promptEditor().saving ? "保存中..." : "保存 Prompt"}</button>
          </footer>
        </div>
      </Show>
      <KouboAgentDrawer
        open={agentOpen}
        setOpen={setAgentOpen}
        task={props.task}
        api={props.api}
        agentKey="image_plan"
        title="图像计划 Agent"
        subtitle="Prompt 和执行状态助手"
        greeting="我可以帮你解释图像计划状态、优化单个图片 Prompt，并建议下一步执行。"
        placeholder="例如：帮我优化当前 asset 的图片提示词，或解释为什么图片没生成。"
        contextChips={imagePlanAgentChips}
        buildClientContext={imagePlanAgentContext}
        renderCandidate={renderImagePlanAgentCandidate}
      />
    </div>
  </Show>;
}
