from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class OCRebuildTaskConfigPayload(BaseModel):
    analysis_task_id: Optional[int] = None
    source_package_path: str = "source_package.json"
    source_scheme: str = "detail"
    target_topic: str = ""
    target_platform: str = "抖音"
    aspect_ratio: str = "9:16"
    target_count: int = 3
    target_audience: str = ""
    product_info: str = ""
    rebuild_goal: str = "复刻参考视频结构"
    preserve_strategy: dict[str, bool] = Field(default_factory=dict)
    replace_strategy: dict[str, bool] = Field(default_factory=dict)
    visual_style: str = "真实口播"
    subtitle_style: str = "底部大字"
    title_style: str = "顶部强钩子"
    voice_style: str = "年轻中文旁白"
    batch_variables: str = ""
    constraints: str = ""
    simple_prompt: str = ""
    final_prompt: str = ""
    prompt_model_provider: str = ""
    prompt_model_id: str = ""
    run_model_provider: str = ""
    run_model_id: str = ""


class OCRebuildTaskUpdatePayload(OCRebuildTaskConfigPayload):
    task_id: int = Field(gt=0)


class OCRebuildPromptGeneratePayload(BaseModel):
    task_id: int = Field(gt=0)
    prompt_model_provider: str = ""
    prompt_model_id: str = ""


class OCRebuildVersionSavePayload(BaseModel):
    task_id: int = Field(gt=0)
    version_name: str = ""
    version_notes: str = ""


class OCRebuildVersionLoadPayload(BaseModel):
    task_id: int = Field(gt=0)
    version_id: int = Field(gt=0)


class OCRebuildRunPayload(BaseModel):
    task_id: int = Field(gt=0)
    run_model_provider: str = ""
    run_model_id: str = ""


class OCRebuildShotKeyframesPayload(BaseModel):
    shot_id: str = Field(min_length=1)
    keyframes: list[dict[str, Any]] = Field(default_factory=list)
    scene_marks: Optional[list[dict[str, Any]]] = None


class OCRebuildShotSceneMarksPayload(BaseModel):
    shot_id: str = Field(min_length=1)
    keyframes: list[dict[str, Any]] = Field(default_factory=list)
    scene_marks: list[dict[str, Any]] = Field(default_factory=list)


class OCRebuildAssetImageGeneratePayload(BaseModel):
    shot_id: str = Field(min_length=1)
    scene_mark_id: str = ""
    role: str = "single"
    use_reference_image: bool = False
    api_call_id: str = ""


class OCRebuildAssetPromptRefinePayload(BaseModel):
    workflow_id: str = ""
    request_id: str = ""
    provider: str = Field(min_length=1)
    image_model: str = Field(min_length=1)
    mode: str = "prompt_only"
    current_prompt: str = Field(min_length=1)
    user_instruction: str = Field(min_length=1)
    round: int = 1


class OCRebuildAssetVideoPromptRefinePayload(BaseModel):
    workflow_id: str = ""
    request_id: str = ""
    provider: str = Field(min_length=1)
    video_model: str = Field(min_length=1)
    input_mode: str = "first_frame"
    current_prompt: str = Field(min_length=1)
    user_instruction: str = Field(min_length=1)
    duration: Optional[float] = None


class OCRebuildAssetComparePromptPayload(BaseModel):
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    variant: int = 0
    user_instruction: str = ""


class OCRebuildAssetVideoComparePromptPayload(BaseModel):
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    duration: Optional[float] = None
    user_instruction: str = ""


class OCRebuildAssetTTSPromptPayload(BaseModel):
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    voice_id: str = Field(min_length=1)
    prompt: str = ""
    text: str = Field(min_length=1)
    user_instruction: str = ""
    target_duration: Optional[float] = None
    fit_to_duration: bool = False
    tempo: Optional[float] = None


class OCRebuildAssetComparePayload(BaseModel):
    workflow_id: str = ""
    shot_id: str = Field(min_length=1)
    scene_mark_id: str = ""
    role: str = "single"
    mode: str = "prompt_only"
    prompts: list[OCRebuildAssetComparePromptPayload] = Field(default_factory=list)
    reference_output: str = ""
    round: int = 1


class OCRebuildAssetVideoComparePayload(BaseModel):
    workflow_id: str = ""
    shot_id: str = Field(min_length=1)
    scene_mark_id: str = ""
    input_mode: str = "first_frame"
    prompts: list[OCRebuildAssetVideoComparePromptPayload] = Field(default_factory=list)
    first_image: str = ""
    last_image: str = ""
    duration: Optional[float] = None


class OCRebuildAssetTTSPromptRefinePayload(BaseModel):
    workflow_id: str = ""
    request_id: str = ""
    shot_id: str = Field(min_length=1)
    scene_mark_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    tts_model: str = Field(min_length=1)
    voice_id: str = Field(min_length=1)
    srt_text: str = Field(min_length=1)
    current_prompt: str = ""
    user_instruction: str = Field(min_length=1)


class OCRebuildAssetTTSComparePayload(BaseModel):
    workflow_id: str = ""
    shot_id: str = Field(min_length=1)
    scene_mark_id: str = Field(min_length=1)
    srt_text: str = Field(min_length=1)
    prompts: list[OCRebuildAssetTTSPromptPayload] = Field(default_factory=list)
    use_locked_cache: bool = False
    locked_output: str = ""
    locked_manifest: str = ""
    locked_config_key: str = ""


class OCRebuildShotTTSPromptRefinePayload(BaseModel):
    workflow_id: str = ""
    request_id: str = ""
    shot_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    tts_model: str = Field(min_length=1)
    voice_id: str = Field(min_length=1)
    srt_text: str = Field(min_length=1)
    current_prompt: str = ""
    user_instruction: str = Field(min_length=1)
    target_duration: Optional[float] = None
    scene_plan: list[dict[str, Any]] = Field(default_factory=list)


class OCRebuildShotTTSComparePayload(BaseModel):
    workflow_id: str = ""
    shot_id: str = Field(min_length=1)
    target_duration: Optional[float] = None
    scene_plan: list[dict[str, Any]] = Field(default_factory=list)
    prompts: list[OCRebuildAssetTTSPromptPayload] = Field(default_factory=list)


class OCRebuildShotTTSRecommendPayload(BaseModel):
    workflow_id: str = ""
    shot_id: str = ""
    scope: str = "shot_plan"
    reference_text: str = ""
    target_gender: str = ""
    language: str = "zh"
    top_k: int = 5
    regenerate: bool = False


class OCRebuildShotTTSBuilderPayload(BaseModel):
    workflow_id: str = ""
    builder: str = Field(min_length=1)
    confirm: bool = False
    force: bool = True
    generate_html: bool = True
    reference_audio: str = ""
    reference_start: Optional[float] = None
    reference_duration: Optional[float] = None


class OCRebuildShotTTSVoiceSelectionPayload(BaseModel):
    workflow_id: str = ""
    shot_id: str = ""
    scope: str = "shot_plan"
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    voice_id: str = Field(min_length=1)
    label: str = ""
    prompt: str = ""
    score: Optional[float] = None
    prompt_template: str = ""
    instructions: str = ""
    stage: str = ""
    candidate_id: str = ""
    audio: str = ""
    fit_audio: str = ""
    raw_audio: str = ""
    tempo: Optional[float] = None
    fit_meta: dict[str, Any] = Field(default_factory=dict)
    top_candidates: list[dict[str, Any]] = Field(default_factory=list)


class OCRebuildShotTTSFinalizePayload(BaseModel):
    workflow_id: str = ""
    shot_id: str = Field(min_length=1)
    selected_output: str = Field(min_length=1)
    provider: str = ""
    model: str = ""
    voice_id: str = ""
    target_duration: Optional[float] = None
    duration: Optional[float] = None
    scene_plan: list[dict[str, Any]] = Field(default_factory=list)


class OCRebuildAssetCompareFinalizePayload(BaseModel):
    workflow_id: str = ""
    shot_id: str = Field(min_length=1)
    scene_mark_id: str = ""
    role: str = "single"
    selected_output: str = Field(min_length=1)
    provider: str = ""
    model: str = ""
    used_reference_image: bool = False


class OCRebuildAssetVideoCompareFinalizePayload(BaseModel):
    workflow_id: str = ""
    shot_id: str = Field(min_length=1)
    scene_mark_id: str = ""
    selected_output: str = Field(min_length=1)
    provider: str = ""
    model: str = ""
    duration: Optional[float] = None


class OCRebuildAssetTTSCompareFinalizePayload(BaseModel):
    workflow_id: str = ""
    shot_id: str = Field(min_length=1)
    scene_mark_id: str = Field(min_length=1)
    selected_output: str = Field(min_length=1)
    provider: str = ""
    model: str = ""
    voice_id: str = ""
    duration: Optional[float] = None


class OCRebuildShotMultiReferencePromptPayload(BaseModel):
    workflow_id: str = ""
    request_id: str = ""
    shot_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    video_model: str = Field(min_length=1)
    current_prompt: str = ""
    user_instruction: str = Field(min_length=1)
    duration: Optional[float] = None
    reference_images: list[str] = Field(default_factory=list)
    reference_videos: list[str] = Field(default_factory=list)
    scene_plan: list[dict[str, Any]] = Field(default_factory=list)


class OCRebuildShotMultiReferencePayload(BaseModel):
    workflow_id: str = ""
    shot_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    duration: Optional[float] = None
    variant_count: int = 1
    reference_images: list[str] = Field(default_factory=list)
    reference_videos: list[str] = Field(default_factory=list)
    scene_plan: list[dict[str, Any]] = Field(default_factory=list)


class OCRebuildShotMultiReferenceFinalizePayload(BaseModel):
    workflow_id: str = ""
    shot_id: str = Field(min_length=1)
    selected_output: str = Field(min_length=1)
    provider: str = ""
    model: str = ""
    duration: Optional[float] = None


class OCRebuildHostProductPromptPayload(BaseModel):
    kind: str = Field(pattern="^(host|product)$")
    simple_prompt: str = Field(min_length=1)
    reference_images: list[str] = Field(default_factory=list)
    provider: str = ""
    model: str = ""


class OCRebuildHostProductGeneratePayload(BaseModel):
    kind: str = Field(pattern="^(host|product)$")
    prompt: str = Field(min_length=1)
    reference_images: list[str] = Field(default_factory=list)
    provider: str = ""
    model: str = ""
    variant: int = 1


class OCRebuildHostProductDeletePayload(BaseModel):
    kind: str = Field(pattern="^(host|product)$")
    path: str = ""


class OCRebuildSRTRewriteRowPayload(BaseModel):
    row_id: str = ""
    shot_id: str = Field(min_length=1)
    scene_mark_id: str = Field(min_length=1)
    cue_index: str = ""
    time_range: str = ""
    virtual_scene: bool = False
    original_srt_text: str = ""
    original_dialogue_text: str = ""
    new_srt_text: str = ""


class OCRebuildSRTRewriteGeneratePayload(BaseModel):
    prompt: str = Field(min_length=1)
    rows: list[OCRebuildSRTRewriteRowPayload] = Field(default_factory=list)
    run_model_provider: str = ""
    run_model_id: str = ""


class OCRebuildSRTRewriteSavePayload(BaseModel):
    rows: list[OCRebuildSRTRewriteRowPayload] = Field(default_factory=list)


class OCRebuildAssetWorkflowSavePayload(BaseModel):
    workflow: dict[str, Any] = Field(default_factory=dict)
