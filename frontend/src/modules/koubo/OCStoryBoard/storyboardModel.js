export function clone(value) {
  return JSON.parse(JSON.stringify(value ?? {}));
}

export function fieldText(value, keys = []) {
  if (typeof value === "string") return value;
  if (!value || typeof value !== "object") return "";
  for (const key of keys) {
    const text = String(value[key] || "").trim();
    if (text) return text;
  }
  return "";
}

export function sceneMarks(shot) {
  return shot?.reference?.scene_marks || [];
}

export function shotDisplayName(shot) {
  return String(shot?.shot_name || shot?.shot_id || "").trim();
}

export function cleanDialogueText(value) {
  let text = String(value || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  text = text.replace(/(^|\n)\s*\d+\s+(?=\d{1,2}:\d{2}:\d{2}[,.]\d{3}\s*-->)/g, "$1");
  text = text.replace(/\d{1,2}:\d{2}:\d{2}[,.]\d{3}\s*-->\s*\d{1,2}:\d{2}:\d{2}[,.]\d{3}/g, " ");
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line && !/^\d+$/.test(line))
    .join(" ")
    .replace(/\s+/g, " ")
    .trim();
}

export function storyboardScenes(shot) {
  const scenes = [];
  const byKey = new Map();
  sceneMarks(shot).forEach((mark, markIndex) => {
    const sceneIndex = Number(mark.scene_index || markIndex + 1);
    const key = mark.scene_id || `${shot?.shot_id || mark.shot_id || "shot"}_scene_${String(sceneIndex).padStart(3, "0")}`;
    let scene = byKey.get(key);
    if (!scene) {
      scene = {
        scene_id: key,
        scene_index: scenes.length + 1,
        target: Number(mark.scene_target_duration || 0),
        marks: [],
        duration: 0,
      };
      byKey.set(key, scene);
      scenes.push(scene);
    }
    scene.marks.push({ ...mark, srt_text: cleanDialogueText(mark.srt_text), __mark_index: markIndex });
    scene.duration = Number((scene.duration + Number(mark.duration || 0)).toFixed(3));
  });
  return scenes;
}

export function nextSceneId(shot) {
  const shotId = shot?.shot_id || "shot";
  const indexes = sceneMarks(shot).map((mark) => Number(String(mark.scene_id || mark.scene_mark_id || "").split("_scene_")[1] || mark.scene_index || 0)).filter(Boolean);
  const next = Math.max(0, ...indexes) + 1;
  return `${shotId}_scene_${String(next).padStart(3, "0")}`;
}

export function sceneKeyForMark(mark, shot, fallbackIndex = 0) {
  const value = String(mark?.scene_id || mark?.source_scene_id || mark?.scene_mark_id || "").trim();
  const stripped = value.replace(/_dialogue_\d+$/, "");
  return stripped || `${shot?.shot_id || mark?.shot_id || "shot"}_scene_${String(Number(mark?.scene_index || fallbackIndex + 1)).padStart(3, "0")}`;
}

export function recalculateShot(shot) {
  if (!shot) return;
  shot.shot_id = String(shot.shot_id || "shot").trim() || "shot";
  shot.shot_name = String(shot.shot_name || shot.shot_id).trim() || shot.shot_id;
  shot.reference = shot.reference || {};
  const marks = sceneMarks(shot);
  let cursor = 0;
  const sceneDurations = new Map();
  const sceneIndexes = new Map();
  const dialogueIndexes = new Map();
  marks.forEach((mark, index) => {
    const originalSceneId = sceneKeyForMark(mark, shot, index);
    if (!sceneIndexes.has(originalSceneId)) sceneIndexes.set(originalSceneId, sceneIndexes.size + 1);
    const sceneIndex = sceneIndexes.get(originalSceneId);
    const sceneId = `${shot.shot_id}_scene_${String(sceneIndex).padStart(3, "0")}`;
    const dialogueIndex = (dialogueIndexes.get(sceneId) || 0) + 1;
    dialogueIndexes.set(sceneId, dialogueIndex);
    mark.shot_id = shot.shot_id;
    mark.scene_id = sceneId;
    mark.scene_index = sceneIndex;
    mark.dialogue_index = dialogueIndex;
    mark.dialogue_id = `${sceneId}_dialogue_${String(dialogueIndex).padStart(3, "0")}`;
    mark.scene_mark_id = dialogueIndex === 1 ? sceneId : mark.dialogue_id;
    mark.srt_text = cleanDialogueText(mark.srt_text || mark.source_srt_text || mark.original_srt_text || "");
    mark.duration = Number(Math.max(0, Number(mark.duration || 0)).toFixed(3));
    mark.start = Number(cursor.toFixed(3));
    mark.end = Number((cursor + mark.duration).toFixed(3));
    sceneDurations.set(sceneId, Number(((sceneDurations.get(sceneId) || 0) + mark.duration).toFixed(3)));
    cursor += mark.duration;
  });
  marks.forEach((mark) => {
    mark.scene_duration = Number(sceneDurations.get(mark.scene_id) || mark.duration || 0);
  });
  shot.start = Number(shot.start || 0);
  shot.duration = Number(cursor.toFixed(3));
  shot.end = Number((shot.start + shot.duration).toFixed(3));
  shot.reference.duration = shot.duration;
  shot.reference.end = Number((Number(shot.reference.start || shot.start || 0) + shot.duration).toFixed(3));
  shot.reference.srt_text = marks.map((mark) => mark.srt_text || "").filter(Boolean).join(" ");
  shot.ui_summary = { ...(shot.ui_summary || {}), summary: shot.reference.srt_text };
}

export function normalizeStoryboardPlan(plan) {
  for (const shot of plan?.shots || []) recalculateShot(shot);
}

export function splitDialogueText(text) {
  const lines = cleanDialogueText(text)
    .split(/\n+/)
    .map((line) => line.trim())
    .filter((line) => line && !/^\d+$/.test(line) && !/-->/.test(line));
  const source = lines.join(" ").trim() || cleanDialogueText(text);
  return source.split(/(?<=[，。！？,.!?])\s*/).map((item) => item.trim()).filter(Boolean);
}

export function makeSceneMark(base, shotId, index, start, duration, text, source) {
  const mark = clone(base || {});
  const sceneId = `${shotId}_scene_${String(index + 1).padStart(3, "0")}`;
  mark.scene_mark_id = sceneId;
  mark.shot_id = shotId;
  mark.scene_id = sceneId;
  mark.scene_index = index + 1;
  mark.dialogue_index = 1;
  mark.dialogue_id = `${sceneId}_dialogue_001`;
  mark.start = Number(start.toFixed(3));
  mark.duration = Number(Math.max(0.2, duration).toFixed(3));
  mark.end = Number((mark.start + mark.duration).toFixed(3));
  mark.srt_text = text;
  mark.boundary_source = source;
  mark.keyframes = mark.keyframes || { single: "", first: "", last: "", paths: [] };
  return mark;
}
