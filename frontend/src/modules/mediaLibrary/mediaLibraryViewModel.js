export const MEDIA_LIBRARY_VIEW_MODE_KEY = "opencrew.mediaLibrary.viewMode";
export const MEDIA_LIBRARY_CARD_COLUMNS_KEY = "opencrew.mediaLibrary.cardColumns";

export function normalizeMediaLibraryViewMode(value) {
  return value === "cards" ? "cards" : "table";
}

export function normalizeMediaLibraryCardColumns(value) {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed >= 2 && parsed <= 6 ? parsed : 4;
}

export function readMediaLibraryViewPreferences(storage = globalThis.localStorage) {
  try {
    return {
      viewMode: normalizeMediaLibraryViewMode(storage?.getItem(MEDIA_LIBRARY_VIEW_MODE_KEY)),
      cardColumns: normalizeMediaLibraryCardColumns(storage?.getItem(MEDIA_LIBRARY_CARD_COLUMNS_KEY)),
    };
  } catch {
    return { viewMode: "table", cardColumns: 4 };
  }
}

export function saveMediaLibraryViewMode(value, storage = globalThis.localStorage) {
  const normalized = normalizeMediaLibraryViewMode(value);
  try {
    storage?.setItem(MEDIA_LIBRARY_VIEW_MODE_KEY, normalized);
  } catch {
    // localStorage can be disabled; the in-memory preference still works.
  }
  return normalized;
}

export function saveMediaLibraryCardColumns(value, storage = globalThis.localStorage) {
  const normalized = normalizeMediaLibraryCardColumns(value);
  try {
    storage?.setItem(MEDIA_LIBRARY_CARD_COLUMNS_KEY, String(normalized));
  } catch {
    // localStorage can be disabled; the in-memory preference still works.
  }
  return normalized;
}

const TAG_ERROR_MESSAGES = {
  media_library_tags_too_many: "每个素材最多可保存 20 个标签。",
  media_library_tag_too_long: "单个标签最多可包含 32 个字符。",
  media_library_tag_empty: "标签不能为空或只包含空白。",
};

export function mediaLibraryPatchErrorMessage(error, fallback = "更新素材失败") {
  const raw = error instanceof Error ? error.message : String(error || "");
  try {
    const detail = JSON.parse(raw)?.detail;
    if (detail && typeof detail === "object" && !Array.isArray(detail)) {
      return TAG_ERROR_MESSAGES[detail.code] || detail.message || fallback;
    }
    if (typeof detail === "string") return detail;
  } catch {
    // Non-JSON errors retain the request wrapper's useful message.
  }
  return raw || fallback;
}

export function normalizeEditableTags(tags) {
  return Array.isArray(tags)
    ? tags.map((tag) => String(tag ?? "").trim())
    : [];
}

export function addEditableTag(tags, value) {
  const normalized = String(value ?? "").trim();
  const current = normalizeEditableTags(tags);
  if (!normalized || current.includes(normalized)) return current;
  if (normalized.length > 32 || current.length >= 20) return current;
  return [...current, normalized];
}
