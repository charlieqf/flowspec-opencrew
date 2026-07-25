import { onCleanup } from "solid-js";
import { copy, renumberPlan } from "./kouboStoryboardModel.js";

const TASK_TARGET = { target_type: "task", shot_id: "", scene_id: "" };

export function createKouboStoryboardImagePlanController(deps) {
  const {
    kbApi,
    task,
    shots,
    scope,
    selectedShotIndex,
    selectedDialogueId,
    dirty,
    runAction,
    setError,
    videoPlanSettings,
    setImagePlanOpen,
    setImagePlanResult,
    setImagePlanState,
    setPlan,
    setState,
    videoPlanBusy,
    composerBusy,
  } = deps;
  let inFlight = false;
  let lastGenerateStartedAt = 0;
  let lastExecuteStartedAt = 0;
  let executionPollTimer = null;
  const repeatGuardMs = 1500;
  const executionPollIntervalMs = 1000;
  const executionPollMaxMs = 2 * 60 * 60 * 1000;

  function currentTarget() {
    const currentScope = scope();
    const shot = shots()[selectedShotIndex()] || shots()[0] || null;
    if (currentScope === "all") return { ...TASK_TARGET };
    if (currentScope === "shot") return { target_type: "shot", shot_id: shot?.shot_id || "", scene_id: "" };
    const dialogueId = selectedDialogueId();
    for (const shotItem of shots()) {
      for (const scene of shotItem.scenes || []) {
        if ((scene.dialogues || []).some((dialogue) => dialogue.dialogue_id === dialogueId)) {
          return { target_type: "scene", shot_id: shotItem.shot_id || "", scene_id: scene.scene_id || "" };
        }
      }
    }
    const scene = shot?.scenes?.[0] || null;
    return { target_type: "scene", shot_id: shot?.shot_id || "", scene_id: scene?.scene_id || "" };
  }

  function normalizeTarget(target) {
    if (!target?.target_type) return currentTarget();
    const targetType = String(target.target_type || "").trim();
    if (targetType === "task" || targetType === "all") return { ...TASK_TARGET };
    if (targetType === "shot") return { target_type: "shot", shot_id: String(target.shot_id || "").trim(), scene_id: "" };
    if (targetType === "scene") {
      return {
        target_type: "scene",
        shot_id: String(target.shot_id || "").trim(),
        scene_id: String(target.scene_id || "").trim(),
      };
    }
    return currentTarget();
  }

  function imagePlanDisabledReason(targetOverride = null) {
    if (!task()?.id) return "未选择任务";
    if (!shots().length) return "未加载 StoryBoard 计划";
    if (dirty()) return "请先保存 StoryBoard，再运行图像计划";
    if (inFlight) return "图像计划正在运行";
    if (videoPlanBusy?.()) return "生成计划正在运行";
    if (composerBusy?.()) return "视频合成正在运行";
    const target = normalizeTarget(targetOverride);
    if (target.target_type === "shot" && !target.shot_id) return "请先选择一个镜头";
    if (target.target_type === "scene" && (!target.shot_id || !target.scene_id)) return "请先选择一个场景";
    return "";
  }

  async function generateImagePlan(targetOverride = null) {
    const startedAt = Date.now();
    if (startedAt - lastGenerateStartedAt < repeatGuardMs) return null;
    const actionSource = String(targetOverride?.action_source || targetOverride?.actionSource || "").trim();
    const target = normalizeTarget(targetOverride);
    const disabledReason = imagePlanDisabledReason(target);
    if (disabledReason) {
      setError?.(disabledReason);
      return null;
    }
    if (inFlight) return null;
    inFlight = true;
    lastGenerateStartedAt = startedAt;
    setImagePlanResult((current) => current ? { ...current, execution_warning: "" } : current);
    setImagePlanState({ phase: "generating", status: "正在生成图像计划..." });
    try {
      const settings = videoPlanSettings?.() || {};
      const result = await runAction("image-plan", () => kbApi.imagePlan(task().id, {
        target,
        settings,
        force: true,
        action_source: actionSource,
        current_signature: {
          target,
          settings,
          shot_count: shots().length,
          selected_shot_index: target.target_type === "task" ? -1 : selectedShotIndex(),
          selected_dialogue_id: target.target_type === "scene" ? selectedDialogueId() : "",
        },
      }));
      setImagePlanResult({
        ...result,
        plan: copy(result.plan || {}),
        target: result.target || target,
        settings: result.settings || settings,
      });
      setImagePlanOpen?.(true);
      setImagePlanState({ phase: "ready", status: "图像计划已重新生成" });
      if (hasImageArtifacts(result)) await refreshStoryboardDetail();
      return result;
    } finally {
      inFlight = false;
      setImagePlanState({ phase: "idle", status: "" });
    }
  }

  async function executeImagePlan(mode = "prompt-only", targetTask = null) {
    const startedAt = Date.now();
    if (startedAt - lastExecuteStartedAt < repeatGuardMs) return null;
    const normalizedMode = ["prompt-only", "image-only", "prompt-and-image"].includes(mode) ? mode : "prompt-only";
    const targetTaskId = String(targetTask?.image_task_id || "").trim();
    const targetAssetKey = String(targetTask?.asset_key || "").trim();
    const disabledReason = imagePlanDisabledReason(TASK_TARGET);
    if (disabledReason) {
      setError?.(disabledReason);
      return null;
    }
    if (inFlight) return null;
    inFlight = true;
    lastExecuteStartedAt = startedAt;
    setImagePlanResult((current) => current ? { ...current, execution_warning: "" } : current);
    setImagePlanOpen?.(true);
    setImagePlanState({ phase: "executing", status: "" });
    try {
      const result = await runAction("image-plan-execute", () => kbApi.executeImagePlan(task().id, {
        mode: normalizedMode,
        target_task_id: targetTaskId,
        target_asset_key: targetAssetKey,
      }));
      setImagePlanResult((previous) => ({
        ...(previous || {}),
        ...result,
        plan: result.plan || previous?.plan || {},
        target: previous?.target,
        settings: previous?.settings,
        execution_warning: "",
      }));
      setImagePlanOpen?.(true);
      if (normalizedMode === "image-only" || normalizedMode === "prompt-and-image") {
        await refreshStoryboardAfterImageExecution(result);
        startExecutionPoll();
      }
      return result;
    } finally {
      inFlight = false;
      setImagePlanState({ phase: "idle", status: "" });
    }
  }

  function scheduleExecutionPoll(callback) {
    if (typeof window !== "undefined" && typeof window.setTimeout === "function") {
      return window.setTimeout(callback, executionPollIntervalMs);
    }
    return globalThis.setTimeout(callback, executionPollIntervalMs);
  }

  function clearExecutionPoll() {
    if (!executionPollTimer) return;
    if (typeof window !== "undefined" && typeof window.clearTimeout === "function") {
      window.clearTimeout(executionPollTimer);
    } else {
      globalThis.clearTimeout(executionPollTimer);
    }
    executionPollTimer = null;
  }

  function executionStillRunning(result) {
    const state = result?.execution_state || {};
    const status = String(state.status || "").toLowerCase();
    return Boolean(result?.binding_status?.state_matches_current_plan && ["queued", "running"].includes(status));
  }

  function hasImageArtifacts(result) {
    const artifactSummary = result?.artifact_status?.summary || {};
    const summary = result?.summary || result?.plan?.summary || {};
    return Number(artifactSummary.image_completed_count || 0) > 0 || Number(summary.ready_existing_images || 0) > 0;
  }

  async function refreshStoryboardDetail() {
    if (!task()?.id) return null;
    const detail = await kbApi.detail(task().id);
    if (detail?.task || detail?.meta) setState?.({ task: detail.task, meta: detail.meta });
    if (detail?.plan) setPlan?.(renumberPlan(copy(detail.plan)));
    return detail;
  }

  async function refreshStoryboardAfterImageExecution(result) {
    const state = result?.execution_state || {};
    const executionResult = result?.execution_result || {};
    const status = String(state.status || "").toLowerCase();
    const mode = String(state.mode || executionResult.mode || "").trim();
    const summary = state.summary || executionResult.summary || {};
    const imageCompleted = Number(summary.image_completed_count || 0);
    if (!["image-only", "prompt-and-image"].includes(mode)) return;
    if (["queued", "running"].includes(status)) return;
    if (imageCompleted <= 0) return;
    await refreshStoryboardDetail();
  }

  function startExecutionPoll() {
    const startedAt = Date.now();
    clearExecutionPoll();
    const tick = async () => {
      if (!task()?.id || Date.now() - startedAt > executionPollMaxMs) {
        clearExecutionPoll();
        return;
      }
      try {
        const result = await refreshImagePlanExecution();
        if (executionStillRunning(result)) {
          executionPollTimer = scheduleExecutionPoll(tick);
        } else {
          clearExecutionPoll();
        }
      } catch (error) {
        if (Date.now() - startedAt > 30000) {
          clearExecutionPoll();
          setError?.(error instanceof Error ? error.message : String(error || "图像计划执行状态刷新失败"));
          return;
        }
        executionPollTimer = scheduleExecutionPoll(tick);
      }
    };
    executionPollTimer = scheduleExecutionPoll(tick);
  }

  async function refreshImagePlanExecution() {
    if (!task()?.id) return null;
    const result = await kbApi.imagePlanExecution(task().id);
    setImagePlanResult((current) => ({
      ...(current || {}),
      ...result,
      plan: result.plan || current?.plan || {},
      target: current?.target,
      settings: current?.settings,
    }));
    await refreshStoryboardAfterImageExecution(result);
    return result;
  }

  onCleanup(clearExecutionPoll);

  return {
    generateImagePlan,
    executeImagePlan,
    refreshImagePlanExecution,
    imagePlanDisabledReason,
  };
}
