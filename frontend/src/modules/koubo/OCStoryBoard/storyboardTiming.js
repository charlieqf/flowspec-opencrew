import { cleanDialogueText, sceneMarks } from "./storyboardModel.js";

export function formatTime(value) {
  const seconds = Math.max(0, Number(value || 0));
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
}

export function formatDurationSeconds(value) {
  const seconds = Math.max(0, Number(value || 0));
  return `${seconds.toFixed(2).replace(/\.?0+$/, "")}s`;
}

export function shotDuration(shot) {
  return sceneMarks(shot).reduce((sum, mark) => sum + Number(mark.duration || 0), 0) || Number(shot?.duration || 0);
}

export function spokenCharCount(text) {
  return cleanDialogueText(text)
    .replace(/\s+/g, "")
    .replace(/[，。！？、；：,.!?;:"“”‘’（）()\[\]【】《》<>-]/g, "")
    .length;
}

export function buildGSecondsPerChar(model) {
  return Number(model?.sec_per_char || model?.avg_sec_per_char || 0);
}

export function timingInfoForDialogue(mark, model) {
  const chars = spokenCharCount(mark?.srt_text || "");
  const secPerChar = buildGSecondsPerChar(model);
  const duration = secPerChar && chars ? Number(Math.max(0.2, chars * secPerChar).toFixed(3)) : Number(mark?.duration || 0);
  return { chars, secPerChar, duration };
}

export function applyBuilderGTimings(plan, model) {
  const secPerChar = buildGSecondsPerChar(model);
  if (!secPerChar) return;
  for (const shot of plan?.shots || []) {
    for (const mark of sceneMarks(shot)) {
      const chars = spokenCharCount(mark?.srt_text || "");
      const duration = chars ? Number(Math.max(0.2, chars * secPerChar).toFixed(3)) : 0;
      mark.duration = duration;
      mark.timing = { ...(mark.timing || {}), source: "builder_g_char_ratio", char_count: chars, sec_per_char: secPerChar, build_g_duration: model?.build_g_duration || 0 };
    }
  }
}
