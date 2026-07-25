from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class OpenClipTaskConfigPayload(BaseModel):
    reference_video_path: str = ""
    industry: str = ""
    persona: str = ""
    target_audience: str = ""
    product_info: str = ""
    constraints: str = ""
    analysis_goal: str = ""
    video_formula: str = ""
    simple_prompt: str = ""
    final_prompt: str = ""
    rewrite_simple_prompt: str = ""
    rewrite_final_prompt: str = ""
    storyboard_simple_prompt: str = ""
    storyboard_final_prompt: str = ""
    storyboard_quick_config: dict[str, Any] = Field(default_factory=dict)
    prompt_model_provider: str = ""
    prompt_model_id: str = ""
    run_model_provider: str = ""
    run_model_id: str = ""


class OpenClipTaskUpdatePayload(OpenClipTaskConfigPayload):
    task_id: int = Field(gt=0)


class KouboScriptTaskCreatePayload(OpenClipTaskConfigPayload):
    title: str = ""
    script: str = ""
    script_format: str = "plain"
    profile_id: str = ""
    create_mode: str = ""
    script_creation_mode: str = "user_provided"
    srt_target_seconds: float = Field(default=15.0, gt=0)
    portrait_segments_per_image: int = Field(default=2, gt=0)
    voice_provider: str = "heygen"
    voice_id: str = ""
    voice_label: str = ""
    tempo: float = Field(default=1.0, gt=0)
    voice_tempo_by_id: dict[str, float] = Field(default_factory=dict)
    talking_head_video_model_key: str = ""
    talking_head_video_provider: str = ""
    talking_head_video_model: str = ""
    talking_head_video_model_alias: str = ""
    use_default_reference_video: bool = True
    reference_video_path: str = ""
    reference_privacy_mode: str = "red_grid_guide"
    apply_privacy_grid_to_reference_video: bool = True
    apply_privacy_grid_to_target_identity_image: bool = True
    privacy_grid_preset: str = "dense_12_1"
    cell_size_reference: int = Field(default=12, ge=12, le=48)
    line_width_reference: float = Field(default=1.0, ge=0.5, le=1.0)


class DanceMimicTaskCreatePayload(BaseModel):
    title: str = ""
    reference_video_path: str = ""
    target_identity_image_path: str = ""
    target_video_seconds: float = Field(default=8.0, gt=0)
    minimum_video_seconds: float = Field(default=4.0, gt=0)
    face_detections_manifest: str = ""
    reference_privacy_mode: str = "face_mask_only"
    apply_privacy_grid_to_reference_video: bool = True
    apply_privacy_grid_to_target_identity_image: bool = True
    block_on_face_not_detected: bool = True
    auto_run: bool = False


class DanceMimicRunPayload(BaseModel):
    target_video_seconds: float = Field(default=8.0, gt=0)
    minimum_video_seconds: float = Field(default=4.0, gt=0)
    face_detections_manifest: str = ""
    reference_privacy_mode: str = ""
    apply_privacy_grid_to_reference_video: bool = True
    apply_privacy_grid_to_target_identity_image: bool = True
    block_on_face_not_detected: bool = False
    force: bool = False


class DanceMimicOneClickMoviePayload(BaseModel):
    max_video_seconds: float = Field(default=4.0, gt=0)
    min_video_seconds: float = Field(default=2.0, gt=0)
    split_tolerance_seconds: float = Field(default=2.0, ge=0)
    force: bool = True
    resume: bool = False
    run_only_step_id: str = ""
    run_from_step_id: str = ""
    video_provider: str = ""
    video_model: str = ""
    subtitle_mode: str = "hyperframe"
    watermark_mode: str = "never"


class OpenClipAnalysisV1OneClickMoviePayload(BaseModel):
    force: bool = True
    resume: bool = False
    run_only_step_id: str = ""
    run_from_step_id: str = ""
    asr_mode: str = "default"
    allow_cloud_asr_data_transfer: bool = False
    tts_builder_mode: str = "quick"
    rewrite_mode: str = "strict"
    storyboard_mode: str = "quick"
    run_model_provider: str = ""
    run_model_id: str = ""
    options: dict[str, Any] = Field(default_factory=dict)
    video_plan_settings: dict[str, Any] = Field(default_factory=dict)
    composer_settings: dict[str, Any] = Field(default_factory=dict)


class OpenClipVersionLoadPayload(BaseModel):
    task_id: int = Field(gt=0)
    version_id: int = Field(gt=0)


class OpenClipPromptGeneratePayload(BaseModel):
    task_id: int = Field(gt=0)
    prompt_model_provider: str = ""
    prompt_model_id: str = ""
    prompt_kind: str = "rewrite"


class OpenClipPromptVersionSavePayload(BaseModel):
    task_id: int = Field(gt=0)
    version_name: str = ""
    version_notes: str = ""


class OpenClipSkillGeneratePayload(BaseModel):
    task_id: int = Field(gt=0)
    prompt_version_id: Optional[int] = None


class OpenClipSkillDraftSavePayload(BaseModel):
    task_id: int = Field(gt=0)
    skill_content: str = ""
    version_name: str = ""
    version_notes: str = ""


class OpenClipSkillVersionSavePayload(BaseModel):
    task_id: int = Field(gt=0)
    prompt_version_id: Optional[int] = None
    skill_content: str = Field(min_length=1)
    version_name: str = ""
    version_notes: str = ""


class OpenClipRunPayload(BaseModel):
    task_id: int = Field(gt=0)
    prompt_version_id: Optional[int] = None
    skill_version_id: Optional[int] = None
    run_model_provider: str = ""
    run_model_id: str = ""


class OpenClipAnalysisV1RunPayload(BaseModel):
    task_id: Optional[int] = Field(default=None, gt=0)
    run_model_provider: str = ""
    run_model_id: str = ""
    asr_mode: str = "default"
    allow_cloud_asr_data_transfer: bool = False
    force: bool = False
    include_tts_builder: bool = True
    tts_builder_mode: str = "quick"
    tts_voice_catalog_dir: str = ""
    rewrite_mode: str = "strict"
    storyboard_mode: str = "quick"
    mode: str = "run_all"
    start_step_id: str = ""
    end_step_id: str = ""
    run_only_step_id: str = ""
    selected_step_ids: list[str] = Field(default_factory=list)
    previous_attempt_id: Optional[int] = None
    pause_before_step_id: str = ""
    options: dict[str, Any] = Field(default_factory=dict)


class OpenClipAnalysisV1StopPayload(BaseModel):
    mode: str = "graceful"
    reason: str = "user_requested"


class OpenClipAnalysisV1PauseBeforePayload(BaseModel):
    step_id: str
    reason: str = "user_requested"


class OpenClipAnalysisV1ResumePayload(BaseModel):
    reason: str = "user_requested"


class OpenClipAnalysisV1SrtRewriteItemPayload(BaseModel):
    srt_id: str = Field(min_length=1)
    dialogue: str = ""


class OpenClipAnalysisV1SrtRewriteSavePayload(BaseModel):
    task_id: Optional[int] = Field(default=None, gt=0)
    items: list[OpenClipAnalysisV1SrtRewriteItemPayload] = Field(default_factory=list)


class OpenClipTTSBuilderPayload(BaseModel):
    task_id: int = Field(gt=0)
    reference_start: float = 0.0
    reference_duration: float = 16.0
    force: bool = True


class OpenClipTTSQuickAdvPayload(BaseModel):
    task_id: Optional[int] = Field(default=None, gt=0)
    reference_start: float = 0.0
    reference_duration: float = 16.0
    stage1_count: int = 24
    stage2_count: int = 6
    final_count: int = 3
    voice_catalog_dir: str = ""
    providers: str = "google"
    voices: str = ""
    model: str = "gemini-3.1-flash-tts-preview"
    enable_speechbrain: bool = True
    force: bool = False
    resume: bool = False
    clone_provider: str = "aliyun"
    clone_target_model: str = "cosyvoice-v3.5-flash"
    clone_prefix: str = "ocadv"
    clone_language_hints: str = "zh"
    clone_consent_confirmed: bool = False
    clone_consent_actor: str = "ui"
    clone_consent_note: str = ""
    clone_audio_path: str = ""
    clone_audio_url: str = ""
    clone_api_key_env: str = "DASHSCOPE_API_KEY"
    clone_voice_id: str = ""
    clone_page_index: int = 0
    clone_page_size: int = 100


class OpenClipTTSPreviewPayload(BaseModel):
    task_id: Optional[int] = Field(default=None, gt=0)
    provider: str = "google"
    model: str = "gemini-3.1-flash-tts-preview"
    target_model: str = ""
    voice_id: str = ""
    voice_source: str = ""
    source_clone_provider: str = ""
    text: str = ""
    prompt: str = ""
    candidate_id: str = ""
    language: str = "zh"
    tempo: Optional[float] = None
    cache_only: bool = False


class OpenClipTTSSelectionPayload(BaseModel):
    task_id: int = Field(gt=0)
    candidate_id: str = ""
    provider: str = ""
    model: str = ""
    voice: str = ""
    voice_id: str = ""
    voice_label: str = ""
    voice_source: str = ""
    sample_audio_path: str = ""
    prompt: str = ""
    prompt_template: str = ""
    score: Optional[float] = None
    match_score: Optional[float] = None
    candidate: dict[str, Any] = Field(default_factory=dict)
