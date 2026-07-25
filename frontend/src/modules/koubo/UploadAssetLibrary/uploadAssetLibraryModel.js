export function assetKind(item) {
  const explicit = String(item?.kind || item?.asset_type || "").toLowerCase();
  if (explicit.includes("image")) return "image";
  if (explicit.includes("video")) return "video";
  if (explicit.includes("audio")) return "audio";
  const path = String(item?.path || item?.history_path || item?.filename || "").toLowerCase();
  if (/\.(avif|gif|heic|jpeg|jpg|png|svg|webp)$/.test(path)) return "image";
  if (/\.(mp4|mov|webm|m4v)$/.test(path)) return "video";
  if (/\.(wav|m4a|mp3|aac|ogg|oga|flac|opus|aiff|aif|caf|weba|wma)$/.test(path)) return "audio";
  return "other";
}

export function imageAssets(items) {
  return (items || []).filter((item) => assetKind(item) === "image");
}

export function historyImageItems(historyVersions) {
  return (historyVersions || []).flatMap((version) => (version.items || []).map((item) => ({
    ...item,
    version: version.version,
    reason: version.reason,
    created_at: item.created_at || version.created_at,
  }))).filter((item) => assetKind(item) === "image");
}

export function historyAssetItems(historyVersions) {
  return (historyVersions || []).flatMap((version) => (version.items || []).map((item) => ({
    ...item,
    version: version.version,
    reason: version.reason,
    created_at: item.created_at || version.created_at,
  }))).filter((item) => ["image", "video", "audio"].includes(assetKind(item)));
}

export function assetLabel(asset) {
  const path = String(asset?.path || asset?.history_path || "");
  return String(asset?.label || asset?.filename || path.split("/").pop() || "Image");
}

export function assetSearchText(asset) {
  return [
    assetLabel(asset),
    asset?.path,
    asset?.history_path,
    asset?.source,
    asset?.version,
    asset?.reason,
    asset?.origin?.prompt,
    asset?.origin?.provider,
    asset?.origin?.model,
    ...(asset?.tags || []),
  ].filter(Boolean).join(" ").toLowerCase();
}

export function filterAssets(items, query) {
  const needle = String(query || "").trim().toLowerCase();
  if (!needle) return items || [];
  return (items || []).filter((item) => assetSearchText(item).includes(needle));
}

export function aspectFromPrompt(prompt) {
  const text = String(prompt || "").toLowerCase();
  if (/(^|[^0-9])9\s*[:：x]\s*16([^0-9]|$)|竖屏|竖图|portrait/.test(text)) return "9:16";
  if (/(^|[^0-9])16\s*[:：x]\s*9([^0-9]|$)|横屏|横图|landscape/.test(text)) return "16:9";
  if (/(^|[^0-9])1\s*[:：x]\s*1([^0-9]|$)|方图|square/.test(text)) return "1:1";
  return "1:1";
}

export function imageSizeForAspect(aspect) {
  if (aspect === "9:16") return "1024x1536";
  if (aspect === "16:9") return "1536x1024";
  if (aspect === "4:3") return "1024x768";
  if (aspect === "3:4") return "768x1024";
  return "1024x1024";
}

export function shapeFromAspect(aspect) {
  if (aspect === "9:16") return "portrait";
  if (aspect === "16:9") return "landscape";
  return "square";
}

export function shapeFromDimensions(width, height, fallback = "square") {
  const ratio = Number(width || 0) / Number(height || 0);
  if (!Number.isFinite(ratio) || ratio <= 0) return fallback;
  if (ratio < 0.85) return "portrait";
  if (ratio > 1.25) return "landscape";
  return "square";
}
