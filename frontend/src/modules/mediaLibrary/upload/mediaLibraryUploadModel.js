const VIDEO_EXTENSIONS = [".mp4", ".mov", ".m4v", ".webm"];
export const MEDIA_UPLOAD_CONCURRENCY = 3;

export function mediaUploadChunkBounds(fileSize, chunkSize, chunkIndex) {
  const size = Math.max(0, Number(fileSize || 0));
  const width = Math.max(1, Number(chunkSize || 1));
  const index = Math.max(0, Number(chunkIndex || 0));
  const start = Math.min(size, index * width);
  return { start, end: Math.min(size, start + width) };
}

export function mediaUploadWorkerCount(pendingCount) {
  return Math.max(0, Math.min(MEDIA_UPLOAD_CONCURRENCY, Number(pendingCount || 0)));
}

export function validateMediaUploadFile(file) {
  if (!file) return "请选择一个视频文件。";
  if (!Number(file.size || 0)) return "视频文件为空，请重新选择。";
  const name = String(file.name || "").toLowerCase();
  if (!VIDEO_EXTENSIONS.some((extension) => name.endsWith(extension))) return "仅支持 MP4、MOV、M4V 和 WebM 视频。";
  return "";
}

export function mediaUploadErrorMessage(error) {
  const fallback = error instanceof Error ? error.message : String(error || "上传失败");
  try {
    const payload = JSON.parse(fallback);
    const detail = payload?.detail;
    if (typeof detail === "string") return detail;
    if (detail?.message) return String(detail.message);
  } catch {
    // The error is already plain text.
  }
  return fallback || "上传失败，请重试。";
}

export function formatUploadBytes(bytes) {
  const value = Math.max(0, Number(bytes || 0));
  if (value < 1024) return `${Math.round(value)} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MB`;
  return `${(value / 1024 ** 3).toFixed(2)} GB`;
}

export const mediaUploadStageLabel = (stage) => ({
  idle: "选择视频",
  selected: "等待上传",
  preparing: "正在准备",
  uploading: "正在上传",
  saving: "正在保存",
  completed: "上传完成",
  failed: "上传失败",
  cancelled: "已取消",
}[stage] || "等待上传");
