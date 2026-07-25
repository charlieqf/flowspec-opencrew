import assert from "node:assert/strict";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { makeSceneMark, normalizeStoryboardPlan, sceneMarks, storyboardScenes } from "../../OpenCrew/OpenClip/frontend/src/OCStoryBoard/storyboardModel.js";
import { reorganizePlanByFixedTiming } from "../../OpenCrew/OpenClip/frontend/src/OCStoryBoard/storyboardReorganize.js";
import { spokenCharCount } from "../../OpenCrew/OpenClip/frontend/src/OCStoryBoard/storyboardTiming.js";

const repoRoot = resolve(dirname(new URL(import.meta.url).pathname), "../..");
const reportPath = process.env.STORYBOARD_BOUNDARY_REPORT || resolve(repoRoot, "artifacts/ocstoryboard/fixed_reorganize_timing_boundaries.json");
const targetShot = 16;
const targetScene = 4;
const secPerChar = 0.5;
const epsilon = 0.001;

function textForDuration(seconds) {
  const chars = Math.round(seconds / secPerChar);
  assert.equal(chars * secPerChar, seconds, `Test duration ${seconds}s must map exactly to characters`);
  return "测".repeat(chars);
}

function makeBoundaryPlan() {
  const sourceDurations = [2, 2, 1, 3, 4, 0.5, 3.5, 2, 2, 2, 2, 5, 18];
  const shot = {
    shot_id: "shot_source",
    source_index: 1,
    reference: { scene_marks: [], srt_text: "" },
    duration: 0,
  };
  let cursor = 0;
  shot.reference.scene_marks = sourceDurations.map((duration, index) => {
    const mark = makeSceneMark({}, shot.shot_id, index, cursor, 0.2, textForDuration(duration), "boundary_source");
    mark.duration = 0.2;
    mark.original_expected_duration = duration;
    mark.scene_id = "shot_source_scene_001";
    mark.scene_index = 1;
    cursor += 0.2;
    return mark;
  });
  shot.reference.srt_text = shot.reference.scene_marks.map((mark) => mark.srt_text).join(" ");
  return { shots: [shot] };
}

function groupSummary(plan) {
  return (plan.shots || []).map((shot) => ({
    shot_id: shot.shot_id,
    shot_duration: Number(shot.duration || 0),
    scene_count: storyboardScenes(shot).length,
    scenes: storyboardScenes(shot).map((scene) => ({
      scene_id: scene.scene_id,
      duration: scene.duration,
      mark_durations: scene.marks.map((mark) => Number(mark.duration || 0)),
    })),
  }));
}

function validateTimingBoundaries(plan) {
  for (const shot of plan.shots || []) {
    const marks = sceneMarks(shot);
    const shotSum = Number(marks.reduce((sum, mark) => sum + Number(mark.duration || 0), 0).toFixed(3));
    assert.equal(Number(shot.duration || 0), shotSum, `${shot.shot_id} duration must equal Dialogue sum`);
    if (marks.every((mark) => Number(mark.duration || 0) <= targetShot)) {
      assert.ok(shotSum <= targetShot + epsilon, `${shot.shot_id} must not exceed ${targetShot}s`);
    } else {
      assert.ok(marks.some((mark) => Number(mark.duration || 0) > targetShot), `${shot.shot_id} can exceed shot target only for an oversized Dialogue`);
    }

    const scenes = storyboardScenes(shot);
    assert.ok(scenes.length <= Math.floor(targetShot / targetScene), `${shot.shot_id} must not exceed scene slots per shot`);
    for (const scene of scenes) {
      if (scene.marks.every((mark) => Number(mark.duration || 0) <= targetScene)) {
        assert.ok(scene.duration <= targetScene + epsilon, `${scene.scene_id} must not exceed ${targetScene}s`);
      } else {
        assert.ok(scene.marks.some((mark) => Number(mark.duration || 0) > targetScene), `${scene.scene_id} can exceed scene target only for an oversized Dialogue`);
      }
    }

    for (const mark of marks) {
      const chars = spokenCharCount(mark.srt_text || "");
      const expectedDuration = chars ? Number(Math.max(0.2, chars * secPerChar).toFixed(3)) : 0;
      assert.equal(Number(mark.duration || 0), expectedDuration, "Dialogue duration must be recomputed from saved text");
    }
  }
}

const plan = makeBoundaryPlan();
reorganizePlanByFixedTiming(plan, {
  targetShot,
  targetScene,
  timingModel: { build_g_duration: 999 },
  timingInfoForDialogue: (mark) => {
    const chars = spokenCharCount(mark.srt_text || "");
    return { chars, secPerChar, duration: Number(Math.max(0.2, chars * secPerChar).toFixed(3)) };
  },
});
normalizeStoryboardPlan(plan);
validateTimingBoundaries(plan);

const summary = groupSummary(plan);
assert.deepEqual(
  summary.map((shot) => ({
    duration: shot.shot_duration,
    scenes: shot.scenes.map((scene) => scene.duration),
  })),
  [
    { duration: 16, scenes: [4, 4, 4, 4] },
    { duration: 13, scenes: [4, 4, 5] },
    { duration: 18, scenes: [18] },
  ],
  "Fixed Timing should pack Dialogues into scene and shot targets with only oversized Dialogue exceptions",
);

const report = {
  target_shot_seconds: targetShot,
  target_scene_seconds: targetScene,
  sec_per_char: secPerChar,
  checks: {
    dialogue_duration_recomputed_from_saved_text: true,
    scene_duration_lte_target_except_oversized_dialogue: true,
    shot_duration_lte_target_except_oversized_dialogue: true,
    scene_slots_per_shot_lte_target_ratio: true,
    exact_boundary_packing: true,
  },
  shots: summary,
};

mkdirSync(dirname(reportPath), { recursive: true });
writeFileSync(reportPath, JSON.stringify(report, null, 2), "utf8");
console.log(JSON.stringify(report, null, 2));
