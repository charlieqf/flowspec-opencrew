export function formatDateTime(value) {
  const number = Number(value || 0);
  if (!number) return "-";
  return new Date(number).toLocaleString([], {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

export function normalizeTask(item) {
  return {
    taskId: Number(item.task_id || 0),
    sessionId: Number(item.session_id || 0),
    title: String(item.title || `Task #${item.task_id || "-"}`),
    profileId: String(item.profile_id || ""),
    createMode: String(item.create_mode || "video"),
    scriptCreationMode: String(item.script_creation_mode || ""),
    inputMode: String(item.input_mode || "video"),
    status: String(item.status || "draft"),
    srtStatus: String(item.srt_status || "missing"),
    storyboardStatus: String(item.storyboard_status || "missing"),
    referenceVideo: String(item.reference_video || ""),
    targetIdentityImage: String(item.target_identity_image || item.storyboard_quick_config?.target_identity_image_path || ""),
    portraitImage: String(item.portrait_image || ""),
    talkingHead: item.talking_head || {},
    referencePrivacyMode: String(item.reference_privacy_mode || item.storyboard_quick_config?.reference_privacy_mode || ""),
    scriptPreview: String(item.script_preview || ""),
    sourceScript: String(item.source_script || ""),
    industry: String(item.industry || ""),
    persona: String(item.persona || ""),
    targetAudience: String(item.target_audience || ""),
    productInfo: String(item.product_info || ""),
    constraints: String(item.constraints || ""),
    analysisGoal: String(item.analysis_goal || ""),
    videoFormula: String(item.video_formula || ""),
    simplePrompt: String(item.simple_prompt || ""),
    finalPrompt: String(item.final_prompt || ""),
    rewriteSimplePrompt: String(item.rewrite_simple_prompt || item.simple_prompt || ""),
    rewriteFinalPrompt: String(item.rewrite_final_prompt || item.final_prompt || ""),
    storyboardSimplePrompt: String(item.storyboard_simple_prompt || ""),
    storyboardFinalPrompt: String(item.storyboard_final_prompt || ""),
    storyboardQuickConfig: item.storyboard_quick_config || {},
    taskSummary: String(item.task_summary || ""),
    dialogueCount: Number(item.dialogue_count || 0),
    shotCount: Number(item.shot_count || 0),
    sceneCount: Number(item.scene_count || 0),
    audioAssetCount: Number(item.audio_asset_count || 0),
    imageAssetCount: Number(item.image_asset_count || 0),
    videoAssetCount: Number(item.video_asset_count || 0),
    voiceStatus: String(item.voice_status || "not_selected"),
    lastError: String(item.last_error || ""),
    archived: Boolean(item.archived),
    analysisUrl: String(item.analysis_url || `#/analysis-v1/tasks/${item.task_id}`),
    talkingHeadUrl: String(item.talking_head_url || `#/talking-head/tasks/${item.task_id}`),
    storyboardUrl: String(item.storyboard_url || `#/koubo-storyboard/tasks/${item.task_id}`),
    workspaceDir: String(item.workspace_dir || ""),
    updatedAt: Number(item.updated_at || 0),
  };
}

export function filterTasks(items, filters) {
  const keyword = String(filters.keyword || "").trim().toLowerCase();
  const status = String(filters.status || "all");
  const mode = String(filters.mode || "all");
  return items.filter((item) => {
    if (status !== "all" && item.status !== status) return false;
    if (mode !== "all" && item.createMode !== mode) return false;
    if (!keyword) return true;
    return [
      item.title,
      item.taskId,
      item.sessionId,
      item.taskSummary,
      item.referenceVideo,
      item.scriptPreview,
    ].some((value) => String(value || "").toLowerCase().includes(keyword));
  });
}
