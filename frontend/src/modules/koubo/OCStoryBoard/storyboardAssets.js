import { api } from "./storyboardApi.js";

export function selectedImageForScene(mark) {
  const planD = mark?.plan_d?.replacement_first_frame?.selected_image;
  const planA = mark?.plan_a?.scene_asset?.selected_image;
  const keyframes = mark?.keyframes || {};
  const paths = Array.isArray(keyframes.paths) ? keyframes.paths : [];
  return planD || planA || keyframes.single || keyframes.first || paths[0] || keyframes.last || paths[paths.length - 1] || "";
}

export function generatedImageForScene(mark) {
  const planD = mark?.plan_d?.replacement_first_frame?.selected_image;
  if (planD) return planD;
  const sceneAsset = mark?.plan_a?.scene_asset;
  const source = String(sceneAsset?.source || "");
  if (sceneAsset?.selected_image && /storyboard|generated|replacement|upload/i.test(source)) return sceneAsset.selected_image;
  return "";
}

export function shotFallbackImagePath(shot, sceneIndex = 0, sceneCount = 1) {
  const frames = Array.isArray(shot?.reference?.keyframes) ? shot.reference.keyframes : [];
  const paths = frames.map((frame) => frame?.path).filter(Boolean);
  if (!paths.length) return "";
  if (sceneCount <= 1 || paths.length === 1) return paths[0];
  const ratio = Math.max(0, Math.min(1, sceneIndex / Math.max(1, sceneCount - 1)));
  return paths[Math.min(paths.length - 1, Math.round(ratio * (paths.length - 1)))] || paths[0];
}

export function dialogueImageForScene(mark, sceneIndex = 0, sceneCount = 1, shot = null) {
  const generated = generatedImageForScene(mark);
  if (generated) return generated;
  const keyframes = mark?.keyframes || {};
  const paths = Array.isArray(keyframes.paths) ? keyframes.paths : [];
  if (keyframes.single) return keyframes.single;
  if (sceneCount <= 1) return keyframes.first || paths[0] || keyframes.last || paths[paths.length - 1] || shotFallbackImagePath(shot, sceneIndex, sceneCount);
  if (sceneIndex === 0) return keyframes.first || paths[0] || keyframes.last || paths[paths.length - 1] || shotFallbackImagePath(shot, sceneIndex, sceneCount);
  if (sceneIndex === sceneCount - 1) return keyframes.last || paths[paths.length - 1] || keyframes.first || paths[0] || shotFallbackImagePath(shot, sceneIndex, sceneCount);
  return paths[Math.min(paths.length - 1, sceneIndex)] || keyframes.first || keyframes.last || shotFallbackImagePath(shot, sceneIndex, sceneCount);
}

export function dialogueBoundAsset(mark) {
  const asset = mark?.storyboard_dialogue_asset;
  return asset && asset.path ? asset : null;
}

export function dialogueBoundImageForScene(mark) {
  return dialogueBoundAsset(mark)?.path || "";
}

export function firstFramePathForScene(mark, shot = null, sceneIndex = 0, sceneCount = 1) {
  const generated = generatedImageForScene(mark);
  if (generated) return generated;
  const keyframes = mark?.keyframes || {};
  const paths = Array.isArray(keyframes.paths) ? keyframes.paths : [];
  return keyframes.single || keyframes.first || paths[0] || keyframes.last || paths[paths.length - 1] || shotFallbackImagePath(shot, sceneIndex, sceneCount);
}

export function lastFramePathForScene(mark) {
  const keyframes = mark?.keyframes || {};
  const paths = Array.isArray(keyframes.paths) ? keyframes.paths : [];
  const first = keyframes.single || keyframes.first || paths[0] || "";
  const last = keyframes.last || (paths.length > 1 ? paths[paths.length - 1] : "") || "";
  return last && last !== first ? last : "";
}

export function assetSlotPath(mark, role, shot = null, sceneIndex = 0, sceneCount = 1) {
  return role === "尾帧" ? lastFramePathForScene(mark) : firstFramePathForScene(mark, shot, sceneIndex, sceneCount);
}

export function assetUrl(item, fallbackSessionId) {
  const path = item?.path || item;
  const isTaskLocalPath = String(path || "").startsWith("uploads/storyboard_references/") || String(path || "").startsWith("uploads/storyboard/");
  const sessionId = isTaskLocalPath ? fallbackSessionId : item?.resource_session_id || fallbackSessionId;
  return sessionId && path ? api.rawFileUrl(sessionId, path) : "";
}

export function assetIdentity(item) {
  return item?.id || item?.asset_id || [item?.resource_session_id || "", item?.path || "", item?.role || "", item?.scene_mark_id || "", item?.label || ""].join("::");
}

export function dialogueAssetIdForScene(mark, sceneIndex = 0, sceneCount = 1) {
  const asset = dialogueBoundAsset(mark);
  return asset ? assetIdentity(asset) : "";
}

export function scenePathIdentity(sceneId, path) {
  return `${sceneId || ""}::${path || ""}`;
}

export function frameSessionForPath(path, taskSessionId, meta) {
  const value = String(path || "");
  if (!value) return taskSessionId;
  if (value.startsWith("Assets/") || value.startsWith("uploads/") || value.startsWith("consistency_references/")) return taskSessionId;
  return meta?.analysis_session_id || taskSessionId;
}
