export function assetKind(item) {
  const explicit = String(item?.kind || item?.asset_type || "").toLowerCase();
  if (explicit.includes("video")) return "video";
  if (explicit.includes("audio")) return "audio";
  const path = String(item?.path || "").toLowerCase();
  if (/\.(mp4|mov|webm|m4v)$/.test(path)) return "video";
  if (/\.(wav|m4a|mp3|aac|ogg|oga|flac|opus|aiff|aif|caf|weba|wma)$/.test(path)) return "audio";
  return "image";
}
