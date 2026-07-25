import { cleanDialogueText, clone, makeSceneMark, sceneMarks } from "./storyboardModel.js";

export function reorganizePlanByFixedTiming(plan, options = {}) {
  const targetShot = Math.max(1, Number(options.targetShot || 16));
  const targetScene = Math.max(0.2, Number(options.targetScene || 4));
  const maxScenesPerShot = Math.max(1, Math.floor(targetShot / targetScene));
  const timingInfoForDialogue = typeof options.timingInfoForDialogue === "function"
    ? options.timingInfoForDialogue
    : (mark) => ({ chars: 0, secPerChar: 0, duration: Number(mark?.duration || 0) });
  const timingModel = typeof options.timingModel === "function" ? options.timingModel() : options.timingModel;
  const flattened = [];

  for (const shot of plan?.shots || []) {
    for (const mark of sceneMarks(shot)) {
      const nextMark = clone(mark);
      nextMark.srt_text = cleanDialogueText(nextMark.srt_text || "");
      flattened.push({ mark: nextMark, shot });
    }
  }
  if (!flattened.length) return plan;

  const nextShots = [];
  let currentShot = null;
  let currentShotDuration = 0;
  let currentSceneDuration = 0;
  let currentSceneIndex = 1;

  const startNextShot = (sourceShot) => {
    const shotIndex = nextShots.length;
    const shotId = `shot_${String(shotIndex + 1).padStart(3, "0")}`;
    currentShot = clone(sourceShot);
    currentShot.shot_id = shotId;
    currentShot.source_index = shotIndex + 1;
    currentShot.reference = { ...(currentShot.reference || {}), scene_marks: [], srt_text: "" };
    currentShotDuration = 0;
    currentSceneDuration = 0;
    currentSceneIndex = 1;
    nextShots.push(currentShot);
  };

  flattened.forEach(({ mark, shot }) => {
    const timing = timingInfoForDialogue(mark) || {};
    const markDuration = Number((timing.duration || mark.duration || 0.2).toFixed(3));
    const sceneWouldOverflow = currentSceneDuration > 0 && currentSceneDuration + markDuration > targetScene;
    const shotWouldOverflow = currentShotDuration > 0 && currentShotDuration + markDuration > targetShot;
    const sceneLimitWouldOverflow = sceneWouldOverflow && currentSceneIndex >= maxScenesPerShot;
    if (!currentShot || shotWouldOverflow || sceneLimitWouldOverflow) {
      startNextShot(shot);
    }
    if (currentSceneDuration > 0 && currentSceneDuration + markDuration > targetScene) {
      currentSceneIndex += 1;
      currentSceneDuration = 0;
    }

    const nextIndex = currentShot.reference.scene_marks.length;
    const sceneId = `${currentShot.shot_id}_scene_${String(currentSceneIndex).padStart(3, "0")}`;
    const dialogueIndex = currentShot.reference.scene_marks.filter((item) => item.scene_id === sceneId).length + 1;
    const dialogueId = `${sceneId}_dialogue_${String(dialogueIndex).padStart(3, "0")}`;
    const nextMark = makeSceneMark(mark, currentShot.shot_id, nextIndex, currentShotDuration, markDuration, mark.srt_text || "", "storyboard_builder_g_fixed_reorganize");
    nextMark.source_shot_id = mark.source_shot_id || shot.shot_id;
    nextMark.source_scene_mark_id = mark.source_scene_mark_id || mark.scene_mark_id;
    nextMark.scene_id = sceneId;
    nextMark.scene_index = currentSceneIndex;
    nextMark.dialogue_id = dialogueId;
    nextMark.dialogue_index = dialogueIndex;
    nextMark.scene_mark_id = dialogueIndex === 1 ? sceneId : dialogueId;
    nextMark.scene_target_duration = targetScene;
    nextMark.scene_duration = Number((currentSceneDuration + markDuration).toFixed(3));
    ["id", "index", "target", "duration"].forEach((suffix) => {
      delete nextMark[`scene_${"group"}_${suffix}`];
    });
    nextMark.timing = {
      ...(nextMark.timing || {}),
      source: "builder_g_char_ratio",
      char_count: timing.chars || 0,
      sec_per_char: timing.secPerChar || 0,
      build_g_duration: timingModel?.build_g_duration || 0,
    };
    currentShot.reference.scene_marks.push(nextMark);
    currentSceneDuration += markDuration;
    currentShotDuration += markDuration;
    currentShot.duration = Number(currentShotDuration.toFixed(3));
    currentShot.reference.duration = currentShot.duration;
    currentShot.reference.srt_text = currentShot.reference.scene_marks.map((item) => item.srt_text || "").join(" ");
    currentShot.ui_summary = { ...(currentShot.ui_summary || {}), summary: currentShot.reference.srt_text };
  });

  plan.shots = nextShots;
  return plan;
}
