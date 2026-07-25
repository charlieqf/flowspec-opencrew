import { copy } from "./kouboStoryboardModel.js";

const DEFAULT_COMPOSER_SETTINGS = {
  subtitle_mode: "hyperframe",
  watermark_mode: "never",
  force: true,
};

const TASK_TARGET = { target_type: "task", shot_id: "", scene_id: "" };

function normalizeSettings(values = {}) {
  const subtitleMode = ["hyperframe", "none"].includes(values.subtitle_mode) ? values.subtitle_mode : DEFAULT_COMPOSER_SETTINGS.subtitle_mode;
  const watermarkMode = ["always", "auto", "never"].includes(values.watermark_mode) ? values.watermark_mode : DEFAULT_COMPOSER_SETTINGS.watermark_mode;
  return {
    subtitle_mode: subtitleMode,
    watermark_mode: watermarkMode,
    force: values.force !== false,
  };
}

function sleep(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function composerStatus(result = {}) {
  const state = result.execution_state || {};
  return String(state.status || result.status || "").trim();
}

function composerIsRunning(result = {}) {
  const status = composerStatus(result);
  if (["queued", "running"].includes(status)) return true;
  return Boolean(result.job_id || result.execution_state?.job_id) && !["completed", "failed", "blocked"].includes(status);
}

function composerFailureDetail(result = {}) {
  const state = result.execution_state || {};
  const detail = state.error || result.error || "视频合成失败";
  return typeof detail === "string" ? detail : JSON.stringify(detail);
}

export function createKouboStoryboardComposerController(deps) {
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
    composerSettings,
    setComposerSettings,
    setComposerOpen,
    setComposerResult,
    setComposerState,
    setPlan,
    setState,
  } = deps;
  let inFlight = false;

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

  function composerDisabledReason() {
    if (!task()?.id) return "未选择任务";
    if (!shots().length) return "尚未加载分镜计划";
    if (dirty()) return "请先保存分镜再合成";
    if (inFlight) return "视频合成正在检查或渲染";
    return "";
  }

  function applyComposerSettings(values) {
    if (inFlight) return;
    setComposerSettings(normalizeSettings(values));
  }

  function applyComposerResult(result, target) {
    if (result.plan) setPlan(copy(result.plan));
    if (result.task) setState((previous) => ({ ...(previous || {}), task: result.task, meta: result.meta || previous?.meta || {} }));
    setComposerResult((previous) => ({
      ...(previous || {}),
      ...result,
      candidates: result.candidates || previous?.candidates || [],
      current_target: target || result.target || previous?.current_target,
    }));
    setComposerOpen(true);
  }

  function setTerminalComposerState(result, target) {
    if (["failed", "blocked"].includes(composerStatus(result))) {
      const detail = composerFailureDetail(result);
      setError?.(detail);
      setComposerState({ phase: "failed", status: detail, target });
      return;
    }
    if (composerStatus(result) === "completed") {
      setComposerState({ phase: "completed", status: "合成已完成。", target });
      return;
    }
    setComposerState({ phase: "idle", status: "", target });
  }

  async function openComposer(targetOverride = null) {
    const disabledReason = composerDisabledReason();
    if (disabledReason) {
      setError?.(disabledReason);
      return;
    }
    inFlight = true;
    const actionSource = String(targetOverride?.action_source || targetOverride?.actionSource || "").trim();
    const target = normalizeTarget(targetOverride);
    setComposerState({ phase: "checking", status: "正在检查可合成目标...", startedAt: Date.now(), target });
    try {
      const result = await runAction("composer-candidates", () => kbApi.composerCandidates(task().id, { target, action_source: actionSource }));
      setComposerResult({
        ...result,
        current_target: result.requested_target || target,
      });
      setComposerOpen(true);
      const execution = await kbApi.composerExecution(task().id).catch(() => null);
      if (execution && composerIsRunning(execution)) {
        const activeTarget = execution.target || target;
        applyComposerResult(execution, activeTarget);
        await waitForComposerExecution(activeTarget);
        return;
      }
      setComposerState({ phase: "ready", status: "" });
    } finally {
      inFlight = false;
      setComposerState((previous) => ["failed", "completed"].includes(previous?.phase) ? previous : { phase: "idle", status: "" });
    }
  }

  async function waitForComposerExecution(target) {
    let latest = null;
    for (;;) {
      latest = await kbApi.composerExecution(task().id);
      const state = latest.execution_state || {};
      const status = composerStatus(latest);
      const elapsed = Number(state.elapsed_seconds || 0);
      setComposerState({
        phase: "executing",
        status: status === "queued" ? "合成任务排队中..." : elapsed ? `正在合成视频... ${elapsed.toFixed(0)}s` : "正在合成视频...",
        startedAt: Date.now() - Math.max(0, elapsed * 1000),
        target,
      });
      applyComposerResult(latest, target);
      if (!composerIsRunning(latest)) break;
      await sleep(2500);
    }
    setTerminalComposerState(latest, target);
    return latest;
  }

  async function executeComposer(target) {
    const disabledReason = composerDisabledReason();
    if (disabledReason) {
      setError?.(disabledReason);
      return null;
    }
    if (!target?.target_type) {
      setError?.("请先选择合成目标");
      return null;
    }
    inFlight = true;
    const settings = normalizeSettings(composerSettings());
    setComposerState({ phase: "executing", status: "正在使用 HyperFrame 渲染合成视频...", startedAt: Date.now(), target });
    try {
      const result = await runAction("composer-execute", () => kbApi.executeComposer(task().id, { target, settings }));
      const activeTarget = result.target || target;
      applyComposerResult(result, activeTarget);
      let finalResult = result;
      try {
        finalResult = composerIsRunning(result) ? await waitForComposerExecution(activeTarget) : result;
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        setError?.(message);
        setComposerState({ phase: "failed", status: message, target: activeTarget });
        throw err;
      }
      setTerminalComposerState(finalResult, activeTarget);
      return finalResult;
    } finally {
      inFlight = false;
      setComposerState((previous) => ["failed", "completed"].includes(previous?.phase) ? previous : { phase: "idle", status: "" });
    }
  }

  async function refreshComposerExecution() {
    if (!task()?.id) return null;
    const result = await kbApi.composerExecution(task().id);
    const target = result.target || result.execution_state?.target || deps.composerResult?.()?.current_target || currentTarget();
    applyComposerResult(result, target);
    if (composerIsRunning(result)) {
      const elapsed = Number(result.execution_state?.elapsed_seconds || 0);
      setComposerState({
        phase: "executing",
        status: elapsed ? `正在合成视频... ${elapsed.toFixed(0)}s` : "正在合成视频...",
        startedAt: Date.now() - Math.max(0, elapsed * 1000),
        target,
      });
    } else {
      setTerminalComposerState(result, target);
    }
    return result;
  }

  return {
    normalizeComposerSettings: normalizeSettings,
    applyComposerSettings,
    openComposer,
    executeComposer,
    refreshComposerExecution,
    composerDisabledReason,
  };
}

export { DEFAULT_COMPOSER_SETTINGS };
