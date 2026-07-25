from __future__ import annotations

from pathlib import Path

SOURCE_REL = "SessionOutput/storyboard/srt_storyboard.json"
EDIT_REL = "SessionOutput/storyboard/koubo_storyboard_edit.json"
ASSETS_REL = "SessionOutput/storyboard/koubo_storyboard_assets.json"
ASSET_IMAGES_REL = "SessionOutput/storyboard/assets/images"
ASSET_AUDIOS_REL = "SessionOutput/storyboard/assets/audios"
ASSET_VIDEOS_REL = "SessionOutput/storyboard/assets/videos"
ASSET_HISTORY_REL = "SessionOutput/storyboard/assets/history"
LEGACY_UPLOAD_ROOT_REL = "SessionOutput/storyboard/koubo_uploads"
WORKING_REL = "SessionOutput/storyboard/Working"
CLEAN_IMAGE_REL = "SessionScratch/CleanImageGenerations"
CLEAN_IMAGE_REFERENCES_REL = f"{CLEAN_IMAGE_REL}/References"
TTS_MANIFESTS_REL = "SessionOutput/storyboard/tts_manifests"
VIDEO_PLAN_REL = "SessionOutput/storyboard/video_generation_plan.json"
VIDEO_PLAN_UI_CACHE_REL = "SessionOutput/storyboard/video_generation_plan.ui_cache.json"
VIDEO_PLAN_SETTINGS_REL = "SessionOutput/storyboard/video_plan_settings.json"
DANCE_MIMIC_STALE_MANIFEST_REL = "SessionReport/stale_manifest.json"
VIDEO_PLAN_TOOL_DIR_REL = "S8_05_01_VideoPlanGenerator"
OPENCREW_ROOT = Path(__file__).resolve().parents[4]
VIDEO_PLAN_SCRIPT_PATH = Path(__file__).resolve().parents[4] / "ToolLibrary" / "Analysis_V1" / "05_01_VideoPlanGenerator.py"
VIDEO_PLAN_EXECUTION_RESULT_REL = "SessionOutput/storyboard/video_plan_execution_result.json"
VIDEO_PLAN_EXECUTION_STATE_REL = "SessionOutput/storyboard/video_plan_execution_state.json"
VIDEO_PLAN_EXECUTION_TOOL_DIR_REL = "S9_05_02_VideoPlanExecutor"
VIDEO_PLAN_EXECUTION_SCRIPT_PATH = Path(__file__).resolve().parents[4] / "ToolLibrary" / "Analysis_V1" / "05_02_VideoPlanExecutor.py"
TALKING_HEAD_VIDEO_PLAN_EXECUTION_SCRIPT_PATH = Path(__file__).resolve().parents[4] / "ToolLibrary" / "TalkingHead_V1" / "05_02_VideoPlanExecutor.py"
TALKING_HEAD_SESSION_VARIABLES_PREPARE_SCRIPT_PATH = Path(__file__).resolve().parents[4] / "ToolLibrary" / "TalkingHead_V1" / "00_PrepareSessionVariables.py"
IMAGE_PLAN_REL = "SessionOutput/storyboard/image_generation_plan.json"
IMAGE_PLAN_TOOL_DIR_REL = "S10_05_03_ImagePlanGenerator"
IMAGE_PLAN_SCRIPT_PATH = Path(__file__).resolve().parents[4] / "ToolLibrary" / "Analysis_V1" / "05_03_ImagePlanGenerator.py"
IMAGE_PLAN_EXECUTION_RESULT_REL = "SessionOutput/storyboard/image_plan_execution_result.json"
IMAGE_PLAN_EXECUTION_STATE_REL = "SessionOutput/storyboard/image_plan_execution_state.json"
IMAGE_PLAN_EXECUTION_TOOL_DIR_REL = "S11_05_04_ImagePlanExecutor"
IMAGE_PLAN_EXECUTION_SCRIPT_PATH = Path(__file__).resolve().parents[4] / "ToolLibrary" / "Analysis_V1" / "05_04_ImagePlanExecutor.py"
VIDEO_ONLY_PLAN_REL = "SessionOutput/storyboard/video_only_generation_plan.json"
VIDEO_ONLY_PLAN_TOOL_DIR_REL = "S12_05_05_VideoOnlyPlanGenerator"
VIDEO_ONLY_PLAN_SCRIPT_PATH = Path(__file__).resolve().parents[4] / "ToolLibrary" / "Analysis_V1" / "05_05_VideoOnlyPlanGenerator.py"
VIDEO_ONLY_PLAN_EXECUTION_RESULT_REL = "SessionOutput/storyboard/video_only_plan_execution_result.json"
VIDEO_ONLY_PLAN_EXECUTION_STATE_REL = "SessionOutput/storyboard/video_only_plan_execution_state.json"
VIDEO_ONLY_PLAN_EXECUTION_TOOL_DIR_REL = "S13_05_06_VideoOnlyPlanExecutor"
VIDEO_ONLY_PLAN_EXECUTION_SCRIPT_PATH = Path(__file__).resolve().parents[4] / "ToolLibrary" / "Analysis_V1" / "05_06_VideoOnlyPlanExecutor.py"
COMPOSER_RESULT_REL = "SessionOutput/storyboard/video_plan_compose_result.json"
COMPOSER_EXECUTION_STATE_REL = "SessionOutput/storyboard/video_plan_compose_state.json"
COMPOSER_TOOL_DIR_REL = "S10_06_01_VideoPlanComposer"
COMPOSER_SCRIPT_PATH = Path(__file__).resolve().parents[4] / "ToolLibrary" / "Analysis_V1" / "06_01_VideoPlanComposer.py"
CONSISTENCY_REFERENCE_GUIDE_PATH = Path(__file__).resolve().parents[4] / "ToolLibrary" / "Rebuild_V1" / "consistency_reference_prompt_guide.md"
CONSISTENCY_FINAL_REFERENCES = (
    {"kind": "host", "label": "人物一致性参考", "stem": "HOST"},
    {"kind": "product", "label": "产品一致性参考", "stem": "Product"},
)
HOST_PRODUCT_PROMPT_TIMEOUT_SECONDS = 600

HOST_PRODUCT_BUILDER_SYSTEM_PROMPT = """
你是 OpenCrew 故事版（口播）的 Host & Product Builder。

你的职责是把用户输入的简单提示词，结合人物/产品一致性参考生成指南，扩写成可直接提交给图像模型的复杂提示词。

严格要求：
1. 必须优先遵守用户的简单提示词。
2. kind=host 时，输出人物一致性参考板生成提示词，不要输出真实视频帧替换提示词。
3. kind=product 时，输出产品一致性参考板生成提示词，不要输出真实视频帧替换提示词。
4. 必须说明多张参考图只作为身份、包装、口播角色或产品锚点，不复制错误布局。
5. 必须包含负面约束，避免旧人物、旧产品、字幕、水印、泛化包装、广告海报感和合规敏感文案。
6. 返回严格 JSON：{"request_id":"...","prompt":"..."}。
7. request_id 必须与用户提供的完全一致。
8. prompt 必须是最终图像生成提示词本身。
9. 不要输出 Markdown、解释、标题、代码块或 JSON 以外的任何内容。
""".strip()

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".m4v"}
AUDIO_EXTS = {".wav", ".m4a", ".mp3", ".aac", ".ogg", ".oga", ".flac", ".opus", ".aiff", ".aif", ".caf", ".weba", ".wma"}
