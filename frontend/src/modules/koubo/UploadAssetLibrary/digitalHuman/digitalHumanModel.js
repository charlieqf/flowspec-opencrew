export const DEFAULT_DIGITAL_HUMAN_SETTINGS = {
  confirm_before_generating: "always",
  aspect: "9:16",
  count: 1,
  generation_model: "avatar_iv",
  model_name: "Avatar IV",
  engine_type: "avatar_iv",
  selected_avatar_id: "",
  selected_voice_id: "",
  selected_audio_asset_path: "",
  generation_mode: "voice_script",
  motion_prompt_enabled: false,
  motion_prompt: "",
  expressiveness: "low",
};

export function text(value, fallback = "") {
  const raw = value === undefined || value === null || value === "" ? fallback : value;
  return String(raw || "").trim();
}

export function normalizeAvatar(item = {}) {
  return {
    id: text(item.id || item.avatar_id || item.avatar_item?.id),
    name: text(item.name || item.avatar_item?.name || "Avatar"),
    group_id: text(item.group_id || item.avatar_item?.group_id),
    avatar_type: text(item.avatar_type || item.avatar_item?.avatar_type),
    status: text(item.status || item.avatar_item?.status || "completed"),
    preview_image_url: text(item.preview_image_url || item.avatar_item?.preview_image_url),
    preview_video_url: text(item.preview_video_url || item.avatar_item?.preview_video_url),
    source_path: text(item.source_path || item.avatar_item?.source_path),
    record_path: text(item.record_path || item.avatar_item?.record_path),
    default_voice_id: text(item.default_voice_id || item.avatar_item?.default_voice_id),
    supported_api_engines: Array.isArray(item.supported_api_engines || item.avatar_item?.supported_api_engines)
      ? (item.supported_api_engines || item.avatar_item?.supported_api_engines).map((value) => text(value)).filter(Boolean)
      : [],
    raw: item,
  };
}

export function normalizeVoice(item = {}) {
  const id = text(item.voice_id || item.voice_clone_id || item.id || item.data?.voice_clone_id);
  return {
    id,
    voice_id: id,
    name: text(item.name || item.voice_name || item.data?.name || id || "Voice"),
    language: text(item.language),
    gender: text(item.gender),
    preview_audio_url: text(item.preview_audio_url),
    status: text(item.status || item.data?.status || "complete"),
    raw: item,
  };
}

export function normalizeAudioAsset(asset = {}) {
  const path = text(asset.path || asset.id);
  return {
    id: text(asset.id, path),
    path,
    name: text(asset.label || asset.filename || path.split("/").pop() || "Audio file"),
    filename: text(asset.filename || path.split("/").pop()),
    raw: asset,
  };
}

export function dataItems(payload) {
  const data = payload?.data;
  if (Array.isArray(data)) return data;
  if (Array.isArray(payload?.items)) return payload.items;
  if (Array.isArray(payload?.voices)) return payload.voices;
  if (Array.isArray(payload?.avatars)) return payload.avatars;
  return [];
}
