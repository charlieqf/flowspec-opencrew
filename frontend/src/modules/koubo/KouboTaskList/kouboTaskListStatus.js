export const TASK_STATUS_LABELS = {
  draft: "草稿",
  queued: "排队中",
  pending: "待处理",
  waiting_input: "待配置",
  initializing: "初始化中",
  editable: "可编辑",
  running: "运行中",
  completed: "已完成",
  failed: "失败",
  blocked: "已阻断",
  archived: "已归档",
};

export const CREATE_MODE_LABELS = {
  video: "视频分析",
  script: "脚本生成",
  dance_mimic: "动作模拟",
  person_talking_head: "人物口播",
};

export function statusLabel(status) {
  return TASK_STATUS_LABELS[String(status || "").toLowerCase()] || status || "-";
}

export function createModeLabel(mode) {
  return CREATE_MODE_LABELS[String(mode || "").toLowerCase()] || mode || "-";
}

export function statusTone(status) {
  const value = String(status || "").toLowerCase();
  if (["editable", "completed"].includes(value)) return "good";
  if (["running", "initializing", "queued", "pending"].includes(value)) return "busy";
  if (["failed", "blocked"].includes(value)) return "bad";
  if (value === "archived") return "muted";
  return "neutral";
}
