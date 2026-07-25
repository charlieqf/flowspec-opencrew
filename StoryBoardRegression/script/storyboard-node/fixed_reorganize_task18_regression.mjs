import assert from "node:assert/strict";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { opencrewDataDir } from "../../../scripts/opencrew-paths.mjs";
import {
  clone,
  makeSceneMark,
  normalizeStoryboardPlan,
  sceneMarks,
} from "../../OpenCrew/OpenClip/frontend/src/OCStoryBoard/storyboardModel.js";
import { reorganizePlanByFixedTiming } from "../../OpenCrew/OpenClip/frontend/src/OCStoryBoard/storyboardReorganize.js";
import { spokenCharCount } from "../../OpenCrew/OpenClip/frontend/src/OCStoryBoard/storyboardTiming.js";

const repoRoot = resolve(dirname(new URL(import.meta.url).pathname), "../..");
const sourcePlanPath = process.env.STORYBOARD_PLAN || resolve(opencrewDataDir(), "sessions/73/workspace/rebuild_shot_plan.json");
const reportPath = process.env.STORYBOARD_REPORT || resolve(repoRoot, "artifacts/ocstoryboard/task18_fixed_reorganize_regression.json");
const targetShot = 16;
const targetScene = 4;
const secPerChar = 0.1455;

function prng(seed) {
  let value = seed >>> 0;
  return () => {
    value = (value * 1664525 + 1013904223) >>> 0;
    return value / 0x100000000;
  };
}

function groupedSceneDurations(shot) {
  const groups = new Map();
  for (const mark of sceneMarks(shot)) {
    const key = mark.scene_id || String(mark.scene_mark_id || "").replace(/_dialogue_\d+$/, "");
    if (!groups.has(key)) groups.set(key, { duration: 0, markDurations: [] });
    const group = groups.get(key);
    group.duration = Number((group.duration + Number(mark.duration || 0)).toFixed(3));
    group.markDurations.push(Number(mark.duration || 0));
  }
  return Array.from(groups.values());
}

function allDialogueTexts(plan) {
  return (plan.shots || []).flatMap((shot) => sceneMarks(shot).map((mark) => String(mark.srt_text || "").trim()));
}

function mergeRandomAdjacentDialogue(plan, rand) {
  const candidates = [];
  const fallbackCandidates = [];
  for (const shot of plan.shots || []) {
    const marks = sceneMarks(shot);
    if (marks.length > 1) fallbackCandidates.push({ shot, index: 1, forceScene: true });
    for (let index = 1; index < marks.length; index += 1) {
      if (marks[index - 1].scene_id && marks[index - 1].scene_id === marks[index].scene_id) {
        candidates.push({ shot, index });
      }
    }
  }
  const pool = candidates.length ? candidates : fallbackCandidates;
  assert.ok(pool.length, "Task #18 should contain adjacent Dialogues to merge");
  const selected = pool[Math.floor(rand() * pool.length)];
  const marks = sceneMarks(selected.shot);
  const previous = marks[selected.index - 1];
  const current = marks[selected.index];
  if (selected.forceScene) {
    current.scene_id = previous.scene_id || previous.scene_mark_id || `${selected.shot.shot_id}_scene_${String(previous.scene_index || 1).padStart(3, "0")}`;
    current.scene_index = previous.scene_index || 1;
    current.dialogue_id = `${current.scene_id}_dialogue_${String(Number(previous.dialogue_index || 1) + 1).padStart(3, "0")}`;
    current.scene_mark_id = current.dialogue_id;
  }
  previous.srt_text = [previous.srt_text, current.srt_text].map((text) => String(text || "").trim()).filter(Boolean).join(" ");
  previous.duration = Number((Number(previous.duration || 0) + Number(current.duration || 0)).toFixed(3));
  marks.splice(selected.index, 1);
  return previous.srt_text;
}

function addRandomDialogue(plan, rand) {
  const populatedShots = (plan.shots || []).filter((shot) => sceneMarks(shot).length);
  assert.ok(populatedShots.length, "Task #18 should contain Dialogues");
  const shot = populatedShots[Math.floor(rand() * populatedShots.length)];
  const marks = sceneMarks(shot);
  const index = Math.floor(rand() * marks.length);
  const current = marks[index];
  const sceneId = current.scene_id || current.scene_mark_id || `${shot.shot_id}_scene_${String(current.scene_index || 1).padStart(3, "0")}`;
  const dialogueIndex = marks.filter((mark) => (mark.scene_id || mark.scene_mark_id) === sceneId).length + 1;
  const text = "新增测试对白，保留为一整句，不按逗号拆开。";
  const next = makeSceneMark(current, shot.shot_id, index + 1, Number(current.end || 0), 0.2, text, "storyboard_regression_add_dialogue");
  next.scene_id = sceneId;
  next.scene_index = current.scene_index || 1;
  next.dialogue_index = dialogueIndex;
  next.dialogue_id = `${sceneId}_dialogue_${String(dialogueIndex).padStart(3, "0")}`;
  next.scene_mark_id = next.dialogue_id;
  next.source_srt_text = "";
  next.original_srt_text = "";
  next.keyframes = { single: "", first: "", last: "", paths: [] };
  delete next.keyframe_asset_ids;
  delete next.storyboard_dialogue_asset;
  marks.splice(index + 1, 0, next);
  return text;
}

const source = JSON.parse(readFileSync(sourcePlanPath, "utf8"));
const plan = clone(source);
const rand = prng(18);
const mergedText = mergeRandomAdjacentDialogue(plan, rand);
const addedText = addRandomDialogue(plan, rand);
normalizeStoryboardPlan(plan);
const expectedTexts = allDialogueTexts(plan);

reorganizePlanByFixedTiming(plan, {
  targetShot,
  targetScene,
  timingModel: { build_g_duration: 123 },
  timingInfoForDialogue: (mark) => {
    const chars = spokenCharCount(mark.srt_text || "");
    return { chars, secPerChar, duration: chars ? Number(Math.max(0.2, chars * secPerChar).toFixed(3)) : Number(mark.duration || 0) };
  },
});
normalizeStoryboardPlan(plan);

const actualTexts = allDialogueTexts(plan);
assert.deepEqual(actualTexts, expectedTexts, "Fixed Timing must preserve saved Dialogue rows exactly");
assert.equal(actualTexts.filter((text) => text === mergedText).length, 1, "Merged Dialogue should remain one Dialogue");
assert.equal(actualTexts.filter((text) => text === addedText).length, 1, "Added Dialogue should remain one Dialogue");
assert.ok(!actualTexts.includes("新增测试对白，"), "Added Dialogue must not be split at the comma");
assert.ok(!actualTexts.includes("保留为一整句，"), "Added Dialogue must not be split at the second comma");

for (const shot of plan.shots || []) {
  const marks = sceneMarks(shot);
  const markSum = Number(marks.reduce((sum, mark) => sum + Number(mark.duration || 0), 0).toFixed(3));
  assert.equal(Number(shot.duration || 0), markSum, `${shot.shot_id} duration should equal Dialogue sum`);
  if (marks.every((mark) => Number(mark.duration || 0) <= targetShot)) {
    assert.ok(Number(shot.duration || 0) <= targetShot + 0.001, `${shot.shot_id} should fit target shot duration`);
  }
  for (const scene of groupedSceneDurations(shot)) {
    if (scene.markDurations.every((duration) => duration <= targetScene)) {
      assert.ok(scene.duration <= targetScene + 0.001, "Scene should fit target scene duration");
    }
  }
  for (const mark of marks) {
    const chars = spokenCharCount(mark.srt_text || "");
    if (!chars) continue;
    const expected = Number(Math.max(0.2, chars * secPerChar).toFixed(3));
    assert.equal(Number(mark.duration || 0), expected, "Dialogue duration should be recomputed from saved text");
  }
}

const report = {
  source_plan_path: sourcePlanPath,
  target_shot_seconds: targetShot,
  target_scene_seconds: targetScene,
  before_dialogue_count_after_manual_edits: expectedTexts.length,
  after_dialogue_count: actualTexts.length,
  merged_dialogue: mergedText,
  added_dialogue: addedText,
  shot_count_after_reorganize: plan.shots?.length || 0,
  checks: {
    preserves_saved_dialogue_rows: true,
    recomputes_duration_from_saved_text: true,
    respects_scene_and_shot_targets_except_oversized_single_dialogue: true,
  },
};

mkdirSync(dirname(reportPath), { recursive: true });
writeFileSync(reportPath, JSON.stringify(report, null, 2), "utf8");
console.log(JSON.stringify(report, null, 2));
