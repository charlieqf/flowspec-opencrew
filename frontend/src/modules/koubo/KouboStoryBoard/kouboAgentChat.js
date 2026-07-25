import { copy, newDialogueFields, renumberPlan } from "./kouboStoryboardModel.js";

const CANDIDATE_TAGS = [
  ["STORYBOARD_EDIT_CANDIDATE", "storyboard_edit_candidate"],
  ["IMAGE_PLAN_CANDIDATE", "image_plan_candidate"],
  ["IMAGE_PLAN_ACTION", "image_plan_action"],
  ["VIDEO_PLAN_ACTION", "video_plan_action"],
  ["COMPOSER_DIAGNOSIS", "composer_diagnosis"],
  ["ASSET_AUDIO_ADVICE", "asset_audio_advice"],
  ["ASSET_VIDEO_ADVICE", "asset_video_advice"],
];

const HIDDEN_TAGS = ["VIDEO_GENERATION_REQUEST"];

function text(value, fallback = "") {
  if (value === undefined || value === null || value === "") return fallback;
  return String(value).trim();
}

function messageId(message) {
  return String(message?.info?.id || message?.id || "");
}

function messageRole(message) {
  return String(message?.info?.role || message?.role || "assistant");
}

function messageTime(message) {
  const time = message?.info?.time || message?.time || {};
  return Number(time.created || time.completed || message?.created_at || 0) || 0;
}

function normalizeChatMessages(items) {
  return Array.from(items || [])
    .filter((item) => messageId(item))
    .sort((a, b) => messageTime(a) - messageTime(b));
}

function messageParts(message, partsByMessageId = {}) {
  const id = messageId(message);
  const stored = id ? Object.values(partsByMessageId[id] || {}) : [];
  const parts = stored.length ? stored : (message?.parts || []);
  return parts.slice().sort((a, b) => String(a.id || "").localeCompare(String(b.id || "")));
}

function rawMessageText(message, partsByMessageId = {}) {
  if (message?.text) return String(message.text);
  return messageParts(message, partsByMessageId)
    .filter((part) => String(part?.type || "text") === "text")
    .map((part) => String(part?.text || ""))
    .join("");
}

function visibleMessageText(message, partsByMessageId = {}) {
  let value = rawMessageText(message, partsByMessageId);
  for (const [tag] of CANDIDATE_TAGS) {
    value = value.replace(new RegExp(`<${tag}>[\\s\\S]*?<\\/${tag}>`, "g"), "");
  }
  for (const tag of HIDDEN_TAGS) {
    value = value.replace(new RegExp(`<${tag}>[\\s\\S]*?<\\/${tag}>`, "g"), "");
  }
  value = value.replace(/^\s*Generating\s*$/gi, "");
  return value.trim();
}

function extractAgentCandidates(message, partsByMessageId = {}) {
  const source = rawMessageText(message, partsByMessageId);
  const items = [];
  for (const [tag, kind] of CANDIDATE_TAGS) {
    const pattern = new RegExp(`<${tag}>([\\s\\S]*?)<\\/${tag}>`, "g");
    let match;
    while ((match = pattern.exec(source))) {
      try {
        const parsed = JSON.parse(match[1].trim());
        items.push({
          id: `${messageId(message) || tag.toLowerCase()}_${items.length + 1}`,
          tag,
          kind,
          payload: parsed,
          title: text(parsed.title || parsed.label || `${tag} ${items.length + 1}`),
        });
      } catch {
        // Ignore incomplete streaming candidate blocks until the assistant finishes them.
      }
    }
  }
  return items;
}

function seedAgentChatState(items) {
  const normalized = normalizeChatMessages(items);
  const partsByMessageId = {};
  for (const message of normalized) {
    const id = messageId(message);
    partsByMessageId[id] = {};
    for (const part of message.parts || []) {
      const partId = String(part.id || part.partID || `${id}-part-${Object.keys(partsByMessageId[id]).length + 1}`);
      partsByMessageId[id][partId] = { ...part, id: partId, messageID: part.messageID || id };
    }
  }
  return { messages: normalized, partsByMessageId };
}

function upsertAgentMessage(state, message) {
  const id = messageId(message);
  if (!id) return state;
  const messages = normalizeChatMessages([
    ...state.messages.filter((item) => messageId(item) !== id && !String(messageId(item)).startsWith("local-")),
    message,
  ]);
  let partsByMessageId = state.partsByMessageId;
  if (message.parts?.length) {
    partsByMessageId = { ...partsByMessageId, [id]: { ...(partsByMessageId[id] || {}) } };
    for (const part of message.parts) {
      const partId = String(part.id || part.partID || `${id}-part-${Object.keys(partsByMessageId[id]).length + 1}`);
      partsByMessageId[id][partId] = { ...part, id: partId, messageID: part.messageID || id };
    }
  }
  return { ...state, messages, partsByMessageId };
}

function upsertAgentPart(state, part) {
  const parentId = String(part?.messageID || part?.messageId || part?.message_id || "");
  const id = String(part?.id || part?.partID || "");
  if (!parentId || !id) return state;
  return {
    ...state,
    partsByMessageId: {
      ...state.partsByMessageId,
      [parentId]: {
        ...(state.partsByMessageId[parentId] || {}),
        [id]: { ...(state.partsByMessageId[parentId]?.[id] || {}), ...part, id, messageID: parentId },
      },
    },
  };
}

function appendAgentPartDelta(state, properties) {
  const parentId = String(properties?.messageID || properties?.messageId || properties?.message_id || "");
  const partId = String(properties?.partID || properties?.partId || properties?.id || "");
  const field = String(properties?.field || "text");
  const delta = String(properties?.delta || "");
  if (!parentId || !partId || field !== "text" || !delta) return state;
  const existing = state.partsByMessageId[parentId]?.[partId] || { id: partId, messageID: parentId, type: "text", text: "" };
  return {
    ...state,
    partsByMessageId: {
      ...state.partsByMessageId,
      [parentId]: {
        ...(state.partsByMessageId[parentId] || {}),
        [partId]: { ...existing, text: String(existing.text || "") + delta },
      },
    },
  };
}

function reduceAgentChatEvent(state, event) {
  const type = String(event?.type || "");
  const properties = event?.properties || {};
  if (type === "message.updated") {
    const nextMessage = properties.info ? { info: properties.info, parts: properties.parts || [] } : properties.message || properties;
    const next = upsertAgentMessage(state, nextMessage);
    const completed = messageRole(nextMessage) === "assistant" && nextMessage?.info?.time?.completed;
    return { ...next, busy: completed ? false : next.busy };
  }
  if (type === "message.part.updated") return upsertAgentPart(state, properties.part || properties);
  if (type === "message.part.delta") return appendAgentPartDelta(state, properties);
  if (type === "message.part.removed") {
    const parentId = String(properties.messageID || "");
    const partId = String(properties.partID || properties.id || "");
    const nextParts = { ...(state.partsByMessageId[parentId] || {}) };
    delete nextParts[partId];
    return { ...state, partsByMessageId: { ...state.partsByMessageId, [parentId]: nextParts } };
  }
  if (type === "session.status") {
    const statusType = String(properties.status?.type || properties.type || "");
    if (statusType === "idle") return { ...state, busy: false };
    if (statusType) return { ...state, busy: true };
  }
  if (type === "session.stream.error" || type === "koubo_agent.chat.tool_blocked") {
    return { ...state, busy: false, error: String(properties.message || "Agent chat stream failed.") };
  }
  return state;
}

function normalizeAgentTarget(target = {}) {
  const targetType = text(target.target_type || target.scope || "task");
  if (targetType === "task" || targetType === "all") return { target_type: "task", shot_id: "", scene_id: "" };
  if (targetType === "shot") return { target_type: "shot", shot_id: text(target.shot_id), scene_id: "" };
  return { target_type: "scene", shot_id: text(target.shot_id), scene_id: text(target.scene_id) };
}

function findShot(plan, shotId) {
  return (plan?.shots || []).find((shot) => shot?.shot_id === shotId) || null;
}

function findScene(plan, shotId, sceneId) {
  const shot = findShot(plan, shotId);
  return shot?.scenes?.find((scene) => scene?.scene_id === sceneId) || null;
}

function findDialogue(plan, dialogueId) {
  for (const shot of plan?.shots || []) {
    for (const scene of shot.scenes || []) {
      for (const [index, dialogue] of (scene.dialogues || []).entries()) {
        if (dialogue.dialogue_id === dialogueId) return { shot, scene, dialogue, index };
      }
    }
  }
  return null;
}

function ensureShortText(value, label, max = 800) {
  const next = String(value || "");
  if (!next.trim()) throw new Error(`${label} 不能为空`);
  if (next.length > max) throw new Error(`${label} 不能超过 ${max} 字符`);
  return next;
}

function applyStoryboardEditCandidate(plan, candidate) {
  const source = candidate?.payload || candidate || {};
  const operations = Array.isArray(source.operations) ? source.operations : [];
  if (!plan?.shots?.length) return { ok: false, error: "当前 StoryBoard 为空。" };
  if (!operations.length) return { ok: false, error: "候选没有可应用的 operations。" };
  if (operations.length > 20) return { ok: false, error: "一次最多应用 20 个操作。" };
  const nextPlan = copy(plan);
  let structural = false;
  let applied = 0;
  try {
    for (const operation of operations) {
      const type = text(operation?.type);
      if (type === "replace_dialogue_text") {
        const found = findDialogue(nextPlan, text(operation.dialogue_id));
        if (!found) throw new Error(`Dialogue 不存在：${text(operation.dialogue_id)}`);
        found.dialogue.text = ensureShortText(operation.text, "Dialogue 文本");
        applied += 1;
        continue;
      }
      if (type === "update_dialogue_duration") {
        const found = findDialogue(nextPlan, text(operation.dialogue_id));
        const seconds = Number(operation.seconds ?? operation.duration);
        if (!found) throw new Error(`Dialogue 不存在：${text(operation.dialogue_id)}`);
        if (!Number.isFinite(seconds) || seconds < 0 || seconds > 120) throw new Error("Dialogue 时长必须在 0 到 120 秒之间");
        found.dialogue.duration = Number(seconds.toFixed(3));
        applied += 1;
        continue;
      }
      if (type === "update_shot_name") {
        const shot = findShot(nextPlan, text(operation.shot_id));
        if (!shot) throw new Error(`Shot 不存在：${text(operation.shot_id)}`);
        shot.shot_name = ensureShortText(operation.name, "Shot 名称", 120);
        applied += 1;
        continue;
      }
      if (type === "add_dialogue_after") {
        const found = findDialogue(nextPlan, text(operation.dialogue_id));
        if (!found || found.shot.shot_id !== text(operation.shot_id) || found.scene.scene_id !== text(operation.scene_id)) throw new Error("新增 Dialogue 的目标不存在");
        const token = `${Date.now()}_${applied}`;
        found.scene.dialogues.splice(found.index + 1, 0, {
          ...newDialogueFields(nextPlan, token),
          text: ensureShortText(operation.text, "新增 Dialogue 文本"),
          duration: Number(operation.duration || operation.seconds || 0),
        });
        structural = true;
        applied += 1;
        continue;
      }
      if (type === "split_scene_after_dialogue") {
        const found = findDialogue(nextPlan, text(operation.dialogue_id));
        if (!found || found.shot.shot_id !== text(operation.shot_id) || found.scene.scene_id !== text(operation.scene_id)) throw new Error("拆分 Scene 的目标不存在");
        if (found.index >= found.scene.dialogues.length - 1) throw new Error("最后一个 Dialogue 后无法拆分 Scene");
        const sceneIndex = found.shot.scenes.findIndex((scene) => scene.scene_id === found.scene.scene_id);
        const moved = found.scene.dialogues.splice(found.index + 1);
        found.shot.scenes.splice(sceneIndex + 1, 0, {
          ...copy(found.scene),
          scene_id: `${found.shot.shot_id}_scene_agent_${Date.now()}_${applied}`,
          scene_name: text(operation.scene_name) || "New Scene",
          dialogues: moved,
        });
        structural = true;
        applied += 1;
        continue;
      }
      if (type === "split_shot_after_dialogue") {
        const found = findDialogue(nextPlan, text(operation.dialogue_id));
        if (!found || found.shot.shot_id !== text(operation.shot_id) || found.scene.scene_id !== text(operation.scene_id)) throw new Error("拆分 Shot 的目标不存在");
        const shotIndex = nextPlan.shots.findIndex((shot) => shot.shot_id === found.shot.shot_id);
        const sceneIndex = found.shot.scenes.findIndex((scene) => scene.scene_id === found.scene.scene_id);
        const newScenes = [];
        if (found.index < found.scene.dialogues.length - 1) {
          const movedDialogues = found.scene.dialogues.splice(found.index + 1);
          newScenes.push({ ...copy(found.scene), scene_id: `${found.shot.shot_id}_scene_agent_${Date.now()}_${applied}`, dialogues: movedDialogues });
        }
        newScenes.push(...found.shot.scenes.splice(sceneIndex + 1));
        if (!newScenes.length) throw new Error("当前位置之后没有可拆分为新 Shot 的内容");
        nextPlan.shots.splice(shotIndex + 1, 0, {
          ...copy(found.shot),
          shot_id: `shot_agent_${Date.now()}_${applied}`,
          shot_name: text(operation.shot_name) || "New Shot",
          scenes: newScenes,
        });
        structural = true;
        applied += 1;
        continue;
      }
      if (type === "merge_dialogue_up") {
        const found = findDialogue(nextPlan, text(operation.dialogue_id));
        if (!found || found.shot.shot_id !== text(operation.shot_id) || found.scene.scene_id !== text(operation.scene_id)) throw new Error("合并 Dialogue 的目标不存在");
        if (found.index <= 0) throw new Error("第一个 Dialogue 不能向上合并");
        const previous = found.scene.dialogues[found.index - 1];
        previous.text = [previous.text, found.dialogue.text].map((item) => String(item || "").trim()).filter(Boolean).join(" ");
        previous.duration = Number((Number(previous.duration || 0) + Number(found.dialogue.duration || 0)).toFixed(3));
        previous.bound_image_path ||= found.dialogue.bound_image_path || "";
        found.scene.dialogues.splice(found.index, 1);
        structural = true;
        applied += 1;
        continue;
      }
      if (type === "merge_scene_up") {
        const shot = findShot(nextPlan, text(operation.shot_id));
        const index = shot?.scenes?.findIndex((scene) => scene.scene_id === text(operation.scene_id)) ?? -1;
        if (!shot || index <= 0) throw new Error("Scene 不能向上合并");
        shot.scenes[index - 1].dialogues.push(...shot.scenes[index].dialogues);
        shot.scenes.splice(index, 1);
        structural = true;
        applied += 1;
        continue;
      }
      throw new Error(`不支持的操作：${type || "(empty)"}`);
    }
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : String(error) };
  }
  return { ok: true, plan: renumberPlan(nextPlan), applied, structural };
}

function focusedStoryboardExcerpt(plan, selection = {}) {
  if (!plan?.shots?.length) return {};
  const scope = text(selection.scope, "scene");
  const shot = findShot(plan, text(selection.shot_id)) || plan.shots[0];
  if (scope === "all") {
    const dialogues = [];
    for (const item of plan.shots || []) for (const scene of item.scenes || []) for (const dialogue of scene.dialogues || []) {
      dialogues.push({ shot_id: item.shot_id, scene_id: scene.scene_id, dialogue_id: dialogue.dialogue_id, text: dialogue.text, duration: dialogue.duration });
      if (dialogues.length >= 20) break;
    }
    return { scope: "all", shot_count: plan.shots.length, dialogue_preview: dialogues };
  }
  if (scope === "shot") return { scope, shot };
  const scene = findScene(plan, shot?.shot_id, text(selection.scene_id)) || shot?.scenes?.[0] || {};
  const dialogue = findDialogue(plan, text(selection.dialogue_id))?.dialogue || scene?.dialogues?.[0] || {};
  return { scope: "scene", shot_id: shot?.shot_id || "", scene, dialogue };
}

export {
  extractAgentCandidates,
  focusedStoryboardExcerpt,
  messageId,
  messageRole,
  messageTime,
  normalizeAgentTarget,
  normalizeChatMessages,
  rawMessageText,
  reduceAgentChatEvent,
  seedAgentChatState,
  visibleMessageText,
  applyStoryboardEditCandidate,
};
