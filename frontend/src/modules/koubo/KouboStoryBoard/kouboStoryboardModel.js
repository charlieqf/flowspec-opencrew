function routeFromHash(hash) {
  const value = String(hash || globalThis.window?.location?.hash || "#/koubo-storyboard/tasks");
  const taskMatch = value.match(/^#\/koubo-storyboard\/tasks\/(\d+)(?:\?|$)/);
  if (!taskMatch) return { view: "list", taskId: 0, dialogueAssetKey: "", navigationError: "" };
  const queryText = value.includes("?") ? value.slice(value.indexOf("?") + 1) : "";
  const rawDialogueAssetKey = new URLSearchParams(queryText).get("dialogue_asset_key");
  const dialogueAssetKey = safeStoryboardDialogueAssetKey(rawDialogueAssetKey);
  return {
    view: "detail",
    taskId: Number(taskMatch[1]),
    dialogueAssetKey,
    navigationError: rawDialogueAssetKey && !dialogueAssetKey
      ? "返回参数中的 Dialogue 素材标识不安全，已忽略并选择安全默认项。"
      : "",
  };
}

function safeStoryboardDialogueAssetKey(value) {
  const normalized = String(value ?? "").trim();
  return /^[A-Za-z0-9._:-]{1,256}$/.test(normalized) ? normalized : "";
}

function locateStoryboardDialogue(plan, dialogueAssetKey) {
  const key = safeStoryboardDialogueAssetKey(dialogueAssetKey);
  if (!key) return { status: "none", shotIndex: -1, dialogueId: "" };
  const matches = [];
  for (const [shotIndex, shot] of (plan?.shots || []).entries()) {
    for (const scene of shot?.scenes || []) {
      for (const dialogue of scene?.dialogues || []) {
        if (String(dialogue?.dialogue_asset_key || "").trim() === key) {
          matches.push({ shotIndex, dialogueId: String(dialogue?.dialogue_id || "") });
        }
      }
    }
  }
  if (matches.length === 1 && matches[0].dialogueId) return { status: "found", ...matches[0] };
  if (matches.length > 1) return { status: "duplicate", shotIndex: -1, dialogueId: "" };
  return { status: "missing", shotIndex: -1, dialogueId: "" };
}

function copy(value) {
  return JSON.parse(JSON.stringify(value || {}));
}

function fmtTime(value) {
  const seconds = Math.max(0, Number(value || 0));
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
}

function fmtSeconds(value) {
  const formatted = Math.max(0, Number(value || 0)).toFixed(2).replace(/\.00$/, "").replace(/(\.\d)0$/, "$1");
  return `${formatted}s`;
}

function shotDuration(shot) {
  return (shot?.scenes || []).reduce((sum, scene) => sum + Number(scene.duration || 0), 0);
}

function sceneDuration(scene) {
  return (scene?.dialogues || []).reduce((sum, dialogue) => sum + Number(dialogue.duration || 0), 0);
}

function dialogueText(dialogue) {
  return String(dialogue?.text || "").replace(/\s+/g, " ").trim();
}

function dialogueCharCount(dialogue) {
  return spokenCharCount(dialogueText(dialogue));
}

function spokenCharCount(text) {
  return String(text || "")
    .replace(/\s+/g, "")
    .replace(/[，。！？、；：,.!?;:"“”‘’（）()\[\]【】《》<>-]/g, "")
    .length;
}

function positiveNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : null;
}

function emptyDialogueWorkingAssets() {
  return {
    audio: { slot: "Audio_Final", source_type: "", path: "" },
    images: [
      { slot: "Image_New", source_type: "", path: "" },
      { slot: "Image_02", source_type: "", path: "" },
    ],
    video: { slot: "Video_Final", source_type: "", path: "" },
  };
}

function randomHex(length = 12) {
  const bytes = new Uint8Array(Math.ceil(length / 2));
  if (globalThis.crypto?.getRandomValues) {
    globalThis.crypto.getRandomValues(bytes);
    return Array.from(bytes, (item) => item.toString(16).padStart(2, "0")).join("").slice(0, length);
  }
  return `${Date.now().toString(16)}${Math.floor(Math.random() * 0xffffffff).toString(16)}`.slice(0, length);
}

function allPlanDialogues(plan) {
  const dialogues = [];
  for (const shot of plan?.shots || []) {
    for (const scene of shot?.scenes || []) {
      for (const dialogue of scene?.dialogues || []) dialogues.push(dialogue);
    }
  }
  return dialogues;
}

function uniqueId(prefix, used, token = "") {
  let candidate = `${prefix}_${token || randomHex()}`.replace(/[^A-Za-z0-9_]+/g, "_").replace(/^_+|_+$/g, "");
  while (!candidate || used.has(candidate)) candidate = `${prefix}_${randomHex()}`;
  used.add(candidate);
  return candidate;
}

function newDialogueFields(plan, token = "") {
  const dialogues = allPlanDialogues(plan);
  const usedDialogueIds = new Set(dialogues.map((dialogue) => String(dialogue?.dialogue_id || "").trim()).filter(Boolean));
  const usedAssetKeys = new Set(dialogues.map((dialogue) => String(dialogue?.dialogue_asset_key || "").trim()).filter(Boolean));
  return {
    dialogue_id: uniqueId("dlg", usedDialogueIds, token),
    srt_id: "",
    srt_ids: [],
    dialogue_asset_key: uniqueId("dak", usedAssetKeys, token),
    source_image_paths: [],
    image_path: "",
    bound_image_path: "",
    working_assets: emptyDialogueWorkingAssets(),
  };
}

function renumberPlan(plan) {
  let globalCursor = 0;
  const usedDialogueIds = new Set();
  for (const [shotIndex, shot] of (plan.shots || []).entries()) {
    shot.shot_id = shot.shot_id || `shot_${String(shotIndex + 1).padStart(3, "0")}`;
    shot.shot_name = String(shot.shot_name || shot.shot_id).trim() || shot.shot_id;
    let shotCursor = 0;
    for (const [sceneIndex, scene] of (shot.scenes || []).entries()) {
      scene.scene_id = scene.scene_id || `${shot.shot_id}_scene_${String(sceneIndex + 1).padStart(3, "0")}`;
      scene.scene_index = sceneIndex + 1;
      scene.asset_key = `${shot.shot_id}_${scene.scene_id}`;
      let sceneCursor = 0;
      for (const [dialogueIndex, dialogue] of (scene.dialogues || []).entries()) {
        const currentDialogueId = String(dialogue.dialogue_id || "").trim();
        dialogue.dialogue_id = currentDialogueId && !usedDialogueIds.has(currentDialogueId)
          ? currentDialogueId
          : uniqueId("dlg", usedDialogueIds);
        usedDialogueIds.add(dialogue.dialogue_id);
        dialogue.scene_id = scene.scene_id;
        dialogue.dialogue_index = dialogueIndex + 1;
        dialogue.duration = Number(Math.max(0, Number(dialogue.duration || 0)).toFixed(3));
        dialogue.start = Number((globalCursor + shotCursor + sceneCursor).toFixed(3));
        dialogue.end = Number((dialogue.start + dialogue.duration).toFixed(3));
        sceneCursor += dialogue.duration;
      }
      scene.start = Number((globalCursor + shotCursor).toFixed(3));
      scene.duration = Number(sceneCursor.toFixed(3));
      scene.end = Number((scene.start + scene.duration).toFixed(3));
      shotCursor += scene.duration;
    }
    shot.start = Number(globalCursor.toFixed(3));
    shot.duration = Number(shotCursor.toFixed(3));
    shot.end = Number((globalCursor + shot.duration).toFixed(3));
    globalCursor += shot.duration;
  }
  return plan;
}

function allDialogues(shot) {
  return (shot?.scenes || []).flatMap((scene) => (scene.dialogues || []).map((dialogue) => ({ scene, dialogue })));
}

export { routeFromHash, safeStoryboardDialogueAssetKey, locateStoryboardDialogue, copy, fmtTime, fmtSeconds, shotDuration, sceneDuration, dialogueText, dialogueCharCount, spokenCharCount, positiveNumber, emptyDialogueWorkingAssets, newDialogueFields, renumberPlan, allDialogues };
