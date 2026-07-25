import { copy } from "./kouboStoryboardModel.js";

const DEFAULT_VIDEO_PLAN_SETTINGS = {
  max_video_seconds: 4,
  min_video_seconds: 2,
  split_tolerance_seconds: 2,
};

export const VIDEO_PLAN_MAX_OPTIONS = [4, 8, 10, 15];
const TASK_TARGET = { target_type: "task", shot_id: "", scene_id: "" };

function positiveNumber(value, fallback) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function nonNegativeNumber(value, fallback) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : fallback;
}

function normalizeSettings(values = {}) {
  const requestedMaxVideo = positiveNumber(values.max_video_seconds, DEFAULT_VIDEO_PLAN_SETTINGS.max_video_seconds);
  const maxVideo = VIDEO_PLAN_MAX_OPTIONS.reduce((closest, option) => {
    return Math.abs(option - requestedMaxVideo) < Math.abs(closest - requestedMaxVideo) ? option : closest;
  }, 4);
  const requestedTolerance = Math.max(0, nonNegativeNumber(values.split_tolerance_seconds, DEFAULT_VIDEO_PLAN_SETTINGS.split_tolerance_seconds));
  return {
    max_video_seconds: maxVideo,
    min_video_seconds: Math.min(Math.max(2, positiveNumber(values.min_video_seconds, DEFAULT_VIDEO_PLAN_SETTINGS.min_video_seconds)), maxVideo),
    split_tolerance_seconds: maxVideo === 10 ? 0 : requestedTolerance,
  };
}

export function createKouboStoryboardVideoPlanController(deps) {
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
    setVideoPlanSettings,
    setVideoPlanOpen,
    setVideoPlanResult,
    setVideoPlanState,
    syncStoryboardDetail,
  } = deps;
  let inFlight = false;
  let lastSyncedExecutionKey = "";

  function currentTarget() {
    const currentScope = scope();
    const shot = shots()[selectedShotIndex()] || shots()[0] || null;
    if (currentScope === "all") return { target_type: "task", shot_id: "", scene_id: "" };
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

  async function applyVideoPlanSettings(values) {
    if (inFlight) return;
    const settings = normalizeSettings(values);
    setVideoPlanSettings(settings);
    if (!task()?.id) return { ok: true, settings };
    const result = await runAction("video-plan-settings", () => kbApi.saveVideoPlanSettings(task().id, settings));
    setVideoPlanSettings(result.settings || settings);
    return result;
  }

  function videoPlanDisabledReason(targetOverride = null) {
    if (!task()?.id) return "未选择任务";
    if (!shots().length) return "未加载 StoryBoard 计划";
    if (dirty()) return "请先保存 StoryBoard，再打开生成计划";
    if (inFlight) return "生成计划正在检查或生成";
    const target = normalizeTarget(targetOverride);
    if (target.target_type === "shot" && !target.shot_id) return "请先选择一个镜头";
    if (target.target_type === "scene" && (!target.shot_id || !target.scene_id)) return "请先选择一个场景";
    return "";
  }

  async function openVideoPlan(targetOverride = null) {
    const actionSource = String(targetOverride?.action_source || targetOverride?.actionSource || "").trim();
    const force = targetOverride?.force === true;
    const target = normalizeTarget(targetOverride);
    const disabledReason = videoPlanDisabledReason(target);
    if (disabledReason) {
      setError?.(disabledReason);
      return;
    }
    if (inFlight) return;
    inFlight = true;
    setVideoPlanState({ phase: "checking", status: "正在检查生成计划缓存..." });
    try {
      const settings = normalizeSettings(videoPlanSettings());
      const result = await runAction("video-plan", () => kbApi.videoPlan(task().id, {
        target,
        settings,
        force,
        action_source: actionSource,
        current_signature: {
          target,
          settings,
          shot_count: shots().length,
          selected_shot_index: target.target_type === "task" ? -1 : selectedShotIndex(),
          selected_dialogue_id: target.target_type === "scene" ? selectedDialogueId() : "",
        },
      }));
      setVideoPlanResult({
        ...result,
        plan: copy(result.plan || {}),
        target: result.target || target,
        settings: result.settings || settings,
      });
      setVideoPlanOpen(true);
      setVideoPlanState({ phase: "ready", status: result.cache_status === "cache_hit" ? "使用缓存的生成计划" : "生成计划已重新生成" });
    } finally {
      inFlight = false;
      setVideoPlanState({ phase: "idle", status: "" });
    }
  }

  async function refreshVideoPlanExecution() {
    if (!task()?.id) return null;
    const result = await kbApi.videoPlanExecution(task().id);
    setVideoPlanResult((current) => ({
      ...(current || {}),
      ...result,
      plan: current?.plan || result.plan || {},
      target: current?.target,
      settings: current?.settings,
    }));
    const status = String(result?.execution_state?.status || "").trim();
    if (["queued", "running"].includes(status)) {
      setVideoPlanState({ phase: "executing", status: "正在执行生成计划..." });
    } else if (status) {
      setVideoPlanState({ phase: "idle", status: "" });
    }
    if (status.startsWith("completed")) {
      const syncKey = [
        result?.execution_state?.job_id || "",
        result?.binding_status?.current_plan_hash || result?.plan_hash || "",
        status,
      ].join(":");
      if (syncKey && syncKey !== lastSyncedExecutionKey) {
        lastSyncedExecutionKey = syncKey;
        const taskId = task()?.id;
        void syncStoryboardDetail?.(taskId);
        window.setTimeout(() => void syncStoryboardDetail?.(taskId), 750);
      }
    }
    return result;
  }

  async function executeVideoPlan() {
    const disabledReason = videoPlanDisabledReason();
    if (disabledReason) {
      setError?.(disabledReason);
      return null;
    }
    const current = typeof deps.videoPlanResult === "function" ? deps.videoPlanResult() : null;
    setVideoPlanState({ phase: "executing", status: "正在执行生成计划..." });
    let keepExecutionState = false;
    try {
      const result = await runAction("video-plan-execute", () => kbApi.executeVideoPlan(task().id, {
        plan_hash: current?.plan?.plan_hash || current?.plan_hash || "",
      }));
      const status = String(result?.execution_state?.status || "").trim();
      keepExecutionState = ["queued", "running"].includes(status);
      setVideoPlanResult((previous) => ({
        ...(previous || {}),
        ...result,
        plan: previous?.plan || result.plan || {},
        target: previous?.target,
        settings: previous?.settings,
      }));
      setVideoPlanOpen(true);
      return result;
    } finally {
      if (!keepExecutionState) setVideoPlanState({ phase: "idle", status: "" });
    }
  }

  return {
    normalizeVideoPlanSettings: normalizeSettings,
    applyVideoPlanSettings,
    openVideoPlan,
    executeVideoPlan,
    refreshVideoPlanExecution,
    currentVideoPlanTarget: currentTarget,
    videoPlanDisabledReason,
  };
}

export { DEFAULT_VIDEO_PLAN_SETTINGS };
