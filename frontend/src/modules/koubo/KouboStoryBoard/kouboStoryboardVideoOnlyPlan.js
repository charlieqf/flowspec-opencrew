import { copy, renumberPlan } from "./kouboStoryboardModel.js";

const TASK_TARGET = { target_type: "task", shot_id: "", scene_id: "" };

export function createKouboStoryboardVideoOnlyPlanController(deps) {
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
    setVideoOnlyPlanOpen,
    setVideoOnlyPlanResult,
    setVideoOnlyPlanState,
    setPlan,
    setState,
    bumpMediaVersion,
    videoPlanBusy,
    imagePlanBusy,
    composerBusy,
  } = deps;
  let inFlight = false;
  let lastGenerateStartedAt = 0;
  let lastExecuteStartedAt = 0;
  const repeatGuardMs = 1500;

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
    if (targetType === "scene") return { target_type: "scene", shot_id: String(target.shot_id || "").trim(), scene_id: String(target.scene_id || "").trim() };
    return currentTarget();
  }

  function disabledReason(targetOverride = null) {
    if (!task()?.id) return "未选择任务";
    if (!shots().length) return "未加载 StoryBoard 计划";
    if (dirty()) return "请先保存 StoryBoard，再运行视频计划";
    if (inFlight) return "视频计划正在运行";
    if (videoPlanBusy?.()) return "生成计划正在运行";
    if (imagePlanBusy?.()) return "图像计划正在运行";
    if (composerBusy?.()) return "视频合成正在运行";
    const target = normalizeTarget(targetOverride);
    if (target.target_type === "shot" && !target.shot_id) return "请先选择一个镜头";
    if (target.target_type === "scene" && (!target.shot_id || !target.scene_id)) return "请先选择一个场景";
    return "";
  }

  async function refreshStoryboardDetail() {
    const detail = await kbApi.detail(task().id);
    if (detail?.task || detail?.meta) setState?.({ task: detail.task, meta: detail.meta });
    if (detail?.plan) setPlan?.(renumberPlan(copy(detail.plan)));
    bumpMediaVersion?.();
  }

  async function generateVideoOnlyPlan(targetOverride = null) {
    const startedAt = Date.now();
    if (startedAt - lastGenerateStartedAt < repeatGuardMs) return null;
    const target = normalizeTarget(targetOverride);
    const reason = disabledReason(target);
    if (reason) {
      setError?.(reason);
      return null;
    }
    if (inFlight) return null;
    inFlight = true;
    lastGenerateStartedAt = startedAt;
    setVideoOnlyPlanState({ phase: "generating", status: "正在生成视频计划..." });
    try {
      const settings = videoPlanSettings?.() || {};
      const result = await runAction("video-only-plan", () => kbApi.videoOnlyPlan(task().id, {
        target,
        settings,
        force: true,
        action_source: String(targetOverride?.action_source || targetOverride?.actionSource || "").trim(),
      }));
      setVideoOnlyPlanResult({ ...result, plan: copy(result.plan || {}), target: result.target || target, settings: result.settings || settings });
      setVideoOnlyPlanOpen?.(true);
      return result;
    } finally {
      inFlight = false;
      setVideoOnlyPlanState({ phase: "idle", status: "" });
    }
  }

  async function executeVideoOnlyPlan(mode = "prompt-only", targetTask = null) {
    const startedAt = Date.now();
    if (startedAt - lastExecuteStartedAt < repeatGuardMs) return null;
    const normalizedMode = ["prompt-only", "video-only", "prompt-and-video"].includes(mode) ? mode : "prompt-only";
    const reason = disabledReason(TASK_TARGET);
    if (reason) {
      setError?.(reason);
      return null;
    }
    if (inFlight) return null;
    inFlight = true;
    lastExecuteStartedAt = startedAt;
    setVideoOnlyPlanOpen?.(true);
    setVideoOnlyPlanState({ phase: "executing", status: "" });
    try {
      const result = await runAction("video-only-plan-execute", () => kbApi.executeVideoOnlyPlan(task().id, {
        mode: normalizedMode,
        target_task_id: String(targetTask?.video_only_task_id || "").trim(),
        target_asset_key: String(targetTask?.asset_key || "").trim(),
      }));
      setVideoOnlyPlanResult((previous) => ({ ...(previous || {}), ...result, plan: result.plan || previous?.plan || {}, target: previous?.target, settings: previous?.settings }));
      if (normalizedMode === "video-only" || normalizedMode === "prompt-and-video") await refreshStoryboardDetail();
      else bumpMediaVersion?.();
      return result;
    } finally {
      inFlight = false;
      setVideoOnlyPlanState({ phase: "idle", status: "" });
    }
  }

  async function confirmVideoOnlyFinal(targetTask) {
    const reason = disabledReason(TASK_TARGET);
    if (reason) {
      setError?.(reason);
      return null;
    }
    const assetKey = String(targetTask?.asset_key || "").trim();
    if (!assetKey) return null;
    const result = await runAction("video-only-confirm-final", () => kbApi.confirmVideoOnlyFinal(task().id, assetKey));
    setVideoOnlyPlanResult((previous) => ({ ...(previous || {}), ...result, plan: result.plan || previous?.plan || {}, target: previous?.target, settings: previous?.settings }));
    await refreshStoryboardDetail();
    bumpMediaVersion?.();
    return result;
  }

  async function materializeVideoOnlyTailFrame(targetTask) {
    const reason = disabledReason(TASK_TARGET);
    if (reason) {
      setError?.(reason);
      return null;
    }
    const assetKey = String(targetTask?.asset_key || "").trim();
    if (!assetKey) return null;
    const result = await runAction("video-only-materialize-tail-frame", () => kbApi.materializeVideoOnlyTailFrame(task().id, assetKey));
    setVideoOnlyPlanResult((previous) => ({ ...(previous || {}), ...result, plan: result.plan || previous?.plan || {}, target: previous?.target, settings: previous?.settings }));
    await refreshStoryboardDetail();
    bumpMediaVersion?.();
    return result;
  }

  return {
    generateVideoOnlyPlan,
    executeVideoOnlyPlan,
    materializeVideoOnlyTailFrame,
    confirmVideoOnlyFinal,
    async refreshVideoOnlyPlanExecution() {
      if (!task()?.id) return null;
      const result = await kbApi.videoOnlyPlanExecution(task().id);
      setVideoOnlyPlanResult((current) => ({ ...(current || {}), ...result, plan: result.plan || current?.plan || {}, target: current?.target, settings: current?.settings }));
      return result;
    },
    videoOnlyPlanDisabledReason: disabledReason,
  };
}
