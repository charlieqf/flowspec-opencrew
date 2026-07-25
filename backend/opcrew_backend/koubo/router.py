from __future__ import annotations

import base64
import asyncio
import hashlib
import io
import json
import mimetypes
import os
import re
import subprocess
import shutil
import struct
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import wave
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import text

from opcrew_backend.adapters.opencode import OpenCodeAuthError, OpenCodeSessionClient
from opcrew_backend.context import AppContext, now_ms
from opcrew_backend.model_policy import (
    SURFACE_ANALYSIS_V1_PROMPT,
    SURFACE_ANALYSIS_V1_RUN,
    fixed_fields_update_for_role,
    mask_model_fields_for_role,
    mask_prompt_models_for_role,
    request_role,
    resolve_prompt_model_for_role,
)
from opcrew_backend.routes.media_model_config import CONFIG_TABLE, audio_url_bytes, bytedance_tts_preview_url, dashscope_tts_preview_url, ensure_table, load_config, load_stored_key
from opcrew_backend.services.local_metering import add_amount, enrich_usage_row, finalize_price_lines
from opcrew_backend.services.opencode_runtime import discover_and_save_opencode_runtime, opencode_client_for_context
from opcrew_backend.services.tts_voice_aliases import (
    PUBLIC_TTS_VOICE_PREFIX,
    resolve_tts_voice_alias,
    storyboard_tts_candidate_is_inactive_cloud_clone,
)
from opcrew_backend.workflow_modes import (
    WORKFLOW_DANCE_MIMIC_V1,
    WORKFLOW_PERSON_TALKING_HEAD_V1,
    infer_openclip_workflow_mode,
    is_analysis_v1_compatible_workflow,
)

# Contract-visible metering guard: analysis_v1_artifact_billing skips local artifact
# rows when the same step already has usage with modality <> 'local_artifact'.
from .analysis_v1_artifact_billing import record_local_artifacts as analysis_v1_record_local_artifacts
from .analysis_v1_artifact_billing import safe_int as analysis_v1_safe_int
from .koubo_storyboard.usage_metering import chat_usage_units, record_storyboard_usage, stable_usage_request_id, tts_usage_units
from .koubo_storyboard.io_utils import write_json as write_storyboard_json
from .koubo_storyboard.video_plan_execution_state_services import normalize_video_plan_execution_state
from .koubo_storyboard.tts_public_aliases import PUBLIC_TTS_MODEL_SEGMENT, PUBLIC_TTS_PROVIDER_PREFIX, resolve_tts_public_alias
from .prompt_options import ANALYSIS_GOAL_OPTIONS, INDUSTRY_OPTIONS, PERSONA_OPTIONS, TARGET_AUDIENCE_OPTIONS, VIDEO_FORMULA_OPTIONS
from .repository import OpenClipRepository
from .talking_head_models import resolve_talking_head_video_model
from .schemas import (
    OpenClipAnalysisV1OneClickMoviePayload,
    OpenClipAnalysisV1PauseBeforePayload,
    OpenClipAnalysisV1ResumePayload,
    OpenClipAnalysisV1RunPayload,
    OpenClipAnalysisV1SrtRewriteSavePayload,
    OpenClipAnalysisV1StopPayload,
    OpenClipPromptGeneratePayload,
    OpenClipPromptVersionSavePayload,
    OpenClipRunPayload,
    OpenClipSkillDraftSavePayload,
    OpenClipSkillGeneratePayload,
    OpenClipSkillVersionSavePayload,
    OpenClipTaskUpdatePayload,
    OpenClipTTSBuilderPayload,
    OpenClipTTSQuickAdvPayload,
    OpenClipTTSPreviewPayload,
    OpenClipTTSSelectionPayload,
    OpenClipVersionLoadPayload,
)

_ANALYSIS_V1_TTS_BODY_MARKER_RE = re.compile(
    r"(?im)^\s*(?:#+\s*)?(?:朗读文本|正文|Text|TRANSCRIPT)\s*[:：]?\s*"
)
_ANALYSIS_V1_TTS_VOICE_LINE_RE = re.compile(r"(?im)^\s*当前\s*voice\s*[:：].*(?:\n|$)")


def talking_head_one_click_prerequisite_step_ids(selected_step_ids: set[str]) -> list[str]:
    """Return server-owned prerequisites for a partial TalkingHead movie run."""

    needs_storyboard_config = bool(selected_step_ids & {"05_01", "05_02"}) and "03" not in selected_step_ids
    if not needs_storyboard_config:
        return []
    prerequisite_ids = ["03"]
    if "05_02" in selected_step_ids and "05_01" not in selected_step_ids:
        prerequisite_ids.append("05_01")
    return prerequisite_ids


def talking_head_one_click_public_error_message(value: Any) -> str:
    """Translate provider/internal video errors into customer-safe guidance."""

    message = str(value or "").strip()
    lowered = message.lower()
    if (
        "inputimagesensitivecontentdetected.privacyinformation" in lowered
        or "input image may contain real person" in lowered
    ):
        return "连续画面未通过隐私安全检查。系统已改用更强的隐私网格，请从“逐句生成视频”继续运行。"
    if (
        "inputvideosensitivecontentdetected.privacyinformation" in lowered
        or "input video may contain real person" in lowered
    ):
        return "上传参考视频未通过隐私安全检查。系统已增强视频网格线，请从“逐句生成视频”继续运行。"
    if "参考视频稳定人脸区域覆盖不足" in message:
        return "上传参考视频中的人物移动范围较大，系统已自动扩展隐私网格覆盖；请重新运行人物口播。"
    if "privacygriderror" in lowered or "reference_privacy_grid" in lowered:
        return "上传参考视频的隐私网格处理失败，请确认视频中人物面部持续可见后重新运行。"
    if "segment_video_missing" in lowered or "segment final video is missing" in lowered:
        return "存在尚未成功生成的视频片段，系统已停止合成；请先继续完成“逐句生成视频”。"
    internal_video_markers = (
        "openrouter",
        "bytedance/",
        "seedance",
        "grok-imagine",
        "wan2.",
        "/api/v1/videos",
    )
    if any(marker in lowered for marker in internal_video_markers):
        return "视频生成服务请求失败，请稍后从“逐句生成视频”继续运行；如持续失败请联系管理员。"
    if message == "[object Object]":
        return "视频生成失败，请从“逐句生成视频”继续运行；如持续失败请联系管理员。"
    return message


def analysis_v1_one_click_step_succeeded(step_id: str, returncode: int, parsed_status: str) -> bool:
    """Preserve legacy warning statuses while rejecting partial video output."""

    status = str(parsed_status or "").strip().lower()
    if returncode != 0 or status in {"failed", "blocked"}:
        return False
    return not (str(step_id or "").strip() == "05_02" and status == "completed_with_failed_items")


def extract_analysis_v1_tts_preview_text(text: str, prompt: str = "") -> str:
    for value in (text, prompt):
        candidate = str(value or "").strip()
        if not candidate:
            continue
        matches = list(_ANALYSIS_V1_TTS_BODY_MARKER_RE.finditer(candidate))
        if matches:
            body = candidate[matches[-1].end() :].strip()
            if body:
                return body
        return candidate
    return ""


def strip_analysis_v1_tts_preview_instruction(prompt: str) -> str:
    value = str(prompt or "").strip()
    if not value:
        return ""
    match = None
    for current in _ANALYSIS_V1_TTS_BODY_MARKER_RE.finditer(value):
        match = current
    if match:
        value = value[: match.start()].strip()
    value = _ANALYSIS_V1_TTS_VOICE_LINE_RE.sub("\n", value)
    return re.sub(r"\n{3,}", "\n\n", value).strip()


_ANALYSIS_V1_REDACTED_MODEL_TOKENS = {"", "[model]", "[provider]", "model", "provider"}


def normalize_analysis_v1_clone_provider_value(value: str) -> str:
    provider = str(value or "").strip().lower()
    if provider in _ANALYSIS_V1_REDACTED_MODEL_TOKENS:
        return ""
    if provider in {"aliyun", "aliyun_dashscope", "dashscope", "qwen", "cosyvoice"}:
        return "cosyvoice"
    if provider == "heygen":
        return "heygen"
    if provider in {"minimax", "minimaxi", "hailuo"}:
        return "minimax"
    return provider


def analysis_v1_clone_model_from_provider_value(provider: str, model: str) -> str:
    model_value = str(model or "").strip()
    if model_value.lower() in _ANALYSIS_V1_REDACTED_MODEL_TOKENS:
        model_value = ""
    if model_value:
        return model_value
    if provider == "heygen":
        return "heygen-voice-clone-v3"
    if provider == "minimax":
        return "minimax-voice-clone-v1"
    if provider == "cosyvoice":
        return "cosyvoice-v3.5-flash"
    return ""


def analysis_v1_clone_payload_model_value(payload: OpenClipTTSPreviewPayload) -> str:
    model = str(payload.target_model or payload.model or "").strip()
    if model == "gemini-3.1-flash-tts-preview":
        return ""
    return model


def resolve_analysis_v1_clone_delete_payload(
    payload: OpenClipTTSQuickAdvPayload,
    voice_target: dict[str, str] | None,
) -> tuple[OpenClipTTSQuickAdvPayload, str, str]:
    target = voice_target if isinstance(voice_target, dict) else {}
    provider = normalize_analysis_v1_clone_provider_value(target.get("provider") or "")
    model = analysis_v1_clone_model_from_provider_value(provider, target.get("model") or "") if provider else ""
    return payload.model_copy(update={
        "clone_voice_id": target.get("voice_id") or payload.clone_voice_id,
        "clone_provider": provider or payload.clone_provider,
        "clone_target_model": model or payload.clone_target_model,
    }), provider, model


def read_analysis_v1_clone_preview_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def analysis_v1_cloud_clone_preview_defaults_from_workspace(
    workspace: Path,
    payload: OpenClipTTSPreviewPayload,
    read_json_file: Callable[[Path], dict[str, Any]] | None = None,
) -> dict[str, str]:
    voice_id = str(payload.voice_id or "").strip()
    candidate_id = str(payload.candidate_id or "").strip()
    voice_source = str(payload.voice_source or "").strip()
    is_cloud_clone = voice_source == "cloud_clone" or candidate_id.startswith("clone_")
    if not is_cloud_clone:
        return {}

    reader = read_json_file or read_analysis_v1_clone_preview_json
    records: list[dict[str, Any]] = []
    clones_payload = reader(workspace / "SessionOutput" / "tts" / "cloud_voice_clones.json")
    clone_records = clones_payload.get("clones") if isinstance(clones_payload.get("clones"), list) else []
    records.extend([item for item in clone_records if isinstance(item, dict)])
    candidates_payload = reader(workspace / "SessionOutput" / "tts" / "tts_builder_candidates.json")
    candidate_records = candidates_payload.get("candidates") if isinstance(candidates_payload.get("candidates"), list) else []
    records.extend([item for item in candidate_records if isinstance(item, dict)])

    for record in records:
        record_voice = str(record.get("voice_id") or record.get("voice") or "").strip()
        record_candidate = str(record.get("candidate_id") or "").strip()
        if (voice_id and record_voice == voice_id) or (candidate_id and record_candidate == candidate_id):
            provider = normalize_analysis_v1_clone_provider_value(str(record.get("provider") or record.get("source_clone_provider") or ""))
            model = analysis_v1_clone_model_from_provider_value(provider, str(record.get("target_model") or record.get("model") or ""))
            if provider:
                return {"provider": provider, "model": model}

    provider = normalize_analysis_v1_clone_provider_value(str(payload.source_clone_provider or payload.provider or ""))
    model = analysis_v1_clone_model_from_provider_value(provider, analysis_v1_clone_payload_model_value(payload))
    return {"provider": provider, "model": model} if provider else {}


def mark_analysis_v1_cloud_clone_task_membership(workspace: Path, result: dict[str, Any]) -> dict[str, Any]:
    try:
        local_payload = read_analysis_v1_clone_preview_json(workspace / "SessionOutput" / "tts" / "cloud_voice_clones.json")
    except Exception:
        local_payload = {}
    local_records = local_payload.get("clones") if isinstance(local_payload.get("clones"), list) else []
    local_voice_ids = {
        str(item.get("voice_id") or item.get("voice") or "").strip()
        for item in local_records
        if isinstance(item, dict)
    }
    voices = result.get("voices") if isinstance(result.get("voices"), list) else []
    for item in voices:
        if not isinstance(item, dict):
            continue
        voice_id = str(item.get("voice_id") or item.get("voice_clone_id") or item.get("voice") or item.get("id") or "").strip()
        item["in_current_task"] = bool(voice_id and voice_id in local_voice_ids)
    return result


def filter_analysis_v1_quick_adv_clones(
    result: dict[str, Any],
    active_clone_provider: str,
) -> dict[str, Any]:
    """Hide task-local clones that belong to a provider which is no longer active."""

    active_provider = normalize_analysis_v1_clone_provider_value(active_clone_provider)
    if not active_provider:
        return result

    rows = result.get("cloned_voices") if isinstance(result.get("cloned_voices"), list) else []
    filtered: list[Any] = []
    inactive_count = 0
    for item in rows:
        if not isinstance(item, dict):
            filtered.append(item)
            continue
        provider = normalize_analysis_v1_clone_provider_value(
            str(item.get("provider") or item.get("source_clone_provider") or "")
        )
        if provider and provider != active_provider:
            inactive_count += 1
            continue
        filtered.append(item)

    result["cloned_voices"] = filtered
    result["inactive_clone_count"] = inactive_count

    final_candidates = result.get("final_candidates") if isinstance(result.get("final_candidates"), dict) else None
    if final_candidates is not None and isinstance(final_candidates.get("candidates"), list):
        final_rows = final_candidates["candidates"]
        filtered_final_rows = [
            item
            for item in final_rows
            if not (
                isinstance(item, dict)
                and storyboard_tts_candidate_is_inactive_cloud_clone(item, active_provider)
            )
        ]
        final_candidates["candidates"] = filtered_final_rows
        result["inactive_final_candidate_count"] = len(final_rows) - len(filtered_final_rows)
    return result


def analysis_v1_google_tts_retry_prompt(prompt: str) -> str:
    body = extract_analysis_v1_tts_preview_text("", prompt) or str(prompt or "").strip()
    return (
        "请用自然普通话朗读以下文本。只朗读文本内容，不要读出任何说明、标题或标点名称。\n\n"
        f"{body}"
    ).strip()


def analysis_v1_google_tts_finish_summary(payload: dict[str, Any] | None) -> str:
    parts: list[str] = []
    for candidate in (payload or {}).get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        reason = str(candidate.get("finishReason") or "").strip()
        message = str(candidate.get("finishMessage") or "").strip()
        if reason or message:
            parts.append(": ".join(item for item in [reason, message] if item))
    return "; ".join(parts)


OPENCLIP_SOURCE = "openclip-analysis"
OPENCLIP_GROUP_ID = "openclip-analysis"
BACKEND_ROOT = Path(__file__).resolve().parents[3] / "backend"
BACKEND_VENV_PYTHON = BACKEND_ROOT / ".venv" / "bin" / "python"
OPENCLIP_RUNNER = BACKEND_ROOT / "scripts" / "openclip_analysis_runner.py"
TOOL_LIBRARY_PROMPT_TEMPLATES = Path(__file__).resolve().parents[3] / "ToolLibrary" / "Analysis" / "PROMPT_TEMPLATES.md"
OPENCREW_REPO_ROOT = Path(__file__).resolve().parents[3]
ANALYSIS_V1_ROOT = OPENCREW_REPO_ROOT / "ToolLibrary" / "Analysis_V1"
TALKING_HEAD_V1_ROOT = OPENCREW_REPO_ROOT / "ToolLibrary" / "TalkingHead_V1"
DANCE_MIMIC_V1_ROOT = OPENCREW_REPO_ROOT / "ToolLibrary" / "DanceMimic_V1"
ANALYSIS_V1_SRT_REWRITE_FREE = ANALYSIS_V1_ROOT / "04_01_SRTRewriteFree.py"
ANALYSIS_V1_TTS_BUILDER_G = ANALYSIS_V1_ROOT / "03_01_TTSBuilderG.py"
ANALYSIS_V1_TTS_BUILDER_QUICK = ANALYSIS_V1_ROOT / "03_02_TTSBuilderQuick.py"
ANALYSIS_V1_TTS_BUILDER_QUICK_ADV = ANALYSIS_V1_ROOT / "03_03_TTSBuilderQuickAdv.py"
ANALYSIS_V1_DEFAULT_VOICE_CATALOG = ANALYSIS_V1_ROOT / "VoiceCatalog" / "gemini-3.1-flash-tts-preview"
ANALYSIS_V1_REWRITTEN_SRT_ITEMS_REL = "SessionOutput/subtitle/rewritten_srt_items.json"
ANALYSIS_V1_STORYBOARD_REL = "SessionOutput/storyboard/srt_storyboard.json"
ANALYSIS_V1_STORYBOARD_EDIT_REL = "SessionOutput/storyboard/koubo_storyboard_edit.json"
ANALYSIS_V1_STORYBOARD_TOOL_SOURCE_RELS = (
    "S7_04_02_StoryBoard/Output/srt_storyboard.json",
    "S7_04_03_StoryBoardQuick/Output/srt_storyboard.json",
)
ANALYSIS_V1_STORYBOARD_SYNC_RELS = (
    ANALYSIS_V1_STORYBOARD_REL,
    *ANALYSIS_V1_STORYBOARD_TOOL_SOURCE_RELS,
)
ANALYSIS_V1_STORYBOARD_WORKING_REL = "SessionOutput/storyboard/Working"
ANALYSIS_V1_STORYBOARD_HISTORY_REL = "SessionOutput/storyboard/assets/history"
ANALYSIS_V1_STORYBOARD_PLAN_RESET_RELS = (
    "SessionOutput/storyboard/video_generation_plan.json",
    "SessionOutput/storyboard/video_generation_plan.ui_cache.json",
    "SessionOutput/storyboard/video_plan_execution_state.json",
    "SessionOutput/storyboard/video_plan_execution_result.json",
    "S8_05_01_VideoPlanGenerator",
    "S9_05_02_VideoPlanExecutor",
    "SessionOutput/storyboard/image_generation_plan.json",
    "SessionOutput/storyboard/image_plan_execution_state.json",
    "SessionOutput/storyboard/image_plan_execution_result.json",
    "S10_05_03_ImagePlanGenerator",
    "S11_05_04_ImagePlanExecutor",
    "SessionOutput/storyboard/video_only_generation_plan.json",
    "SessionOutput/storyboard/video_only_plan_execution_state.json",
    "SessionOutput/storyboard/video_only_plan_execution_result.json",
    "S12_05_05_VideoOnlyPlanGenerator",
    "S13_05_06_VideoOnlyPlanExecutor",
    "SessionOutput/storyboard/video_plan_compose_result.json",
    "SessionOutput/storyboard/video_plan_compose_state.json",
    "S10_06_01_VideoPlanComposer",
)
SIMPLE_PROMPT_TEMPLATE_START = "<!-- OPENCLIP_SIMPLE_PROMPT_TEMPLATE_START -->"
SIMPLE_PROMPT_TEMPLATE_END = "<!-- OPENCLIP_SIMPLE_PROMPT_TEMPLATE_END -->"

FALLBACK_SIMPLE_PROMPT_TEMPLATE = """
请根据下面的简单提示词，生成一段更详细的复杂业务提示词，用于指导视频内容理解和拆解逻辑。
复杂业务提示词必须只描述业务信息、结构意图、场景规则、三套方案业务规则和复拍关注点。
不要包含工具名称、代码、命令、文件路径或执行步骤。

按“{video_formula}”理解并拆解这条视频。

行业：{industry}
人设：{persona}
目标观众：{target_audience}
产品/服务：{product_info}
分析目标：{analysis_goal}

公式槽位：
{formula_slots}

业务拆解重点：
请识别视频中的关键观点、角色关系、冲突/证据/方案/转化节点，并按“{video_formula}”归纳结构。

主拍摄场景：
{shooting_scene_list}

场景判断规则：
只有真实物理拍摄空间变化才算场景转场。同一空间内的机位、景别、人物、动作、话题或字幕变化，不单独算真实场景转场。标题卡、黑屏、截图、信息插页、平台导流页作为特殊视觉类型单独识别。

三套方案业务规则：
detail：保留最细业务表达单元。
balanced：合并同一表达功能的连续片段，形成可复拍、可交付的业务单元。
summary：按视频公式槽位和业务阶段聚合。

复拍描述重点：
人物关系、主场景、关键动作、道具/产品露出、情绪触发、口播落点、画面必须保留信息。

特殊要求：
{constraints}

请输出仅面向业务理解和拆解逻辑的提示词，不要包含工具名称、代码、命令、文件路径或执行步骤。
""".strip()


def load_simple_prompt_template() -> str:
    try:
        content = TOOL_LIBRARY_PROMPT_TEMPLATES.read_text(encoding="utf-8")
    except OSError:
        return FALLBACK_SIMPLE_PROMPT_TEMPLATE
    start = content.find(SIMPLE_PROMPT_TEMPLATE_START)
    end = content.find(SIMPLE_PROMPT_TEMPLATE_END)
    if start < 0 or end < 0 or end <= start:
        return FALLBACK_SIMPLE_PROMPT_TEMPLATE
    return content[start + len(SIMPLE_PROMPT_TEMPLATE_START):end].strip()

PROMPT_BUILDER_SYSTEM_PROMPT = """
你是 OpenClip Prompt Builder，只负责把业务输入扩写成一份适合视频拆分与分析的业务任务书。

严格要求：
1. 只能输出业务语义、业务判断和业务输出要求。
2. 不得输出任何技术执行细节。
3. 不得出现脚本、命令、工具名、软件名、路径、目录、文件名、JSON、Markdown、解释器、依赖、运行顺序。
4. 不要使用代码块。

输出要求：
1. 直接输出最终业务版 Final Prompt。
2. 必须覆盖：项目背景、业务目标、拆解视角、视频公式、公式槽位、场景判断规则、三套方案业务规则、复拍描述关注点、质量要求。
3. 必须围绕当前视频公式组织槽位和结构，不得写死 Hook / Trust / CTA，除非输入公式本身就是 Hook/Trust/CTA。
4. 如果输入提供主拍摄场景列表，必须明确列出；如果没有提供，应要求按视频实际画面归纳主场景，不要臆造。
5. 复杂提示词要比 Simple Prompt 更详细，但只能扩展业务逻辑，不得加入工具执行方法。
6. 语言保持专业、清晰、可执行。
""".strip()

SRT_REWRITE_PROMPT_BUILDER_SYSTEM_PROMPT = """
你是 Analysis_V1 SRT Rewrite Prompt Builder。你的职责是把简单提示词扩写成用于逐句改写 SRT 的业务 Final Prompt。

严格要求：
1. 只指导逐句改写，不要组织 Shot、Scene 或分镜。
2. 必须强调句子数量不变、srt_id 不变、顺序不变、时间不变、图片帧不变。
3. 必须说明按照行业、人设、目标受众、产品信息和约束条件替换原对白中的产品与卖点。
4. 必须保留视频公式语义，供后续整理和分段使用，但不得要求本步骤做分段。
5. 必须要求改写后的对白使用简体中文，禁止繁体字；英文、数字、品牌名保持原样。
6. 直接输出最终业务版 Final Prompt，不要代码块，不要技术执行步骤。
""".strip()

FULL_SCRIPT_PROMPT_BUILDER_SYSTEM_PROMPT = """
你是人物口播完整脚本 Prompt Builder。你的职责是把简单提示词扩写成一份用于从零生成完整人物口播脚本的业务 Final Prompt。

严格要求：
1. 任务是从零创作完整口播脚本，不是改写 SRT 或参考对白；不得要求输入原脚本。
2. 必须结合行业、人设、目标受众、产品信息、视频公式和约束条件，明确脚本的表达目标、内容结构、语气和行动引导。
3. 必须按照当前视频公式组织完整脚本；如果是 Hook/Trust/CTA，应明确开头抓注意、中段建立信任、结尾行动引导，但不要输出 Shot、Scene 或分镜。
4. 不得加入句子数量不变、srt_id 不变、顺序不变、时间不变、图片帧不变、不新增或不删除等改写任务约束。
5. 未提供产品信息时，不得虚构产品名称、卖点、数据、功效或承诺；已提供产品信息时，不得添加未经提供或无法确认的事实。
6. 必须要求最终脚本完整、连贯、口语化、可直接用于人物口播，并使用简体中文；英文、数字、品牌名保持原样。
7. 直接输出最终业务版 Final Prompt，不要代码块，不要技术执行步骤，也不要直接代写口播脚本。
""".strip()

SRT_STORYBOARD_PROMPT_BUILDER_SYSTEM_PROMPT = """
你是 Analysis_V1 SRT StoryBoard Prompt Builder。你的职责是把简单提示词扩写成用于把改写后 SRT 组织为 Shot / Scene 的业务 Final Prompt。

严格要求：
1. 只指导结构化分组，不要改写对白。
2. 必须强调每个 srt_id 出现且只出现一次，顺序不变，不能切断一句话。
3. 必须围绕视频公式组织 formula_stage、Shot 和 Scene。
4. 必须采用 Simple Prompt 中写明的 StoryBoard 结构参数（Scene 目标时长、Shot 目标时长、分割容忍度、语言边界策略）；不得自行改写成固定秒数；如果 Simple Prompt 未提供结构参数，才按业务语义给出温和建议。
5. 直接输出最终业务版 Final Prompt，不要代码块，不要技术执行步骤。
""".strip()

SKILL_BUILDER_SYSTEM_PROMPT = """
你是 OpenClip Skill Builder。你的职责是把最新 Final Prompt 与稳定执行工作流合成为一份可直接执行的视频拆分 Skill。

生成要求：
1. 输出必须是一份可直接给 OpenCode 使用的 Skill，不是说明文。
2. 必须覆盖业务目标、执行顺序、输入输出、质量校验和交付标准。
3. 必须明确要求：先提取关键帧、对白、视频元数据和候选切点，再结合业务公式完成拆分。
4. 必须明确：不能只按视觉切点机械拆分，必须结合关键帧、对白、句子边界、故事动作和转场。
5. 必须明确：所有关键结果都写入工作目录。
6. 必须围绕当前视频公式输出公式槽位映射，不得把所有公式都写成 Hook / Trust / CTA。
 7. 必须明确：三套分镜方案的段数不能预设固定值，必须依据视频实际逻辑密度、场景变化、对白推进和表达功能动态生成。
 8. 必须明确：公式槽位时间范围不能按视频时长均分，必须从逻辑段中归纳映射。
 9. 不要使用代码块。
""".strip()

SKILL_COMPILER_SYSTEM_PROMPT = """
你是 OpenClip Skill Compiler。你的职责是只根据用户提供的 Current Skill，判断其中的 Final Prompt Context 是否包含明确拆分要求，并输出 runner 可执行的严格 JSON。

规则：
1. Current Skill 是唯一输入，不得引入 Skill 外部的新业务要求。
2. 如果 Final Prompt Context 中明确给出了片段名称、场景顺序、隔断画面、必须单独成段的节点、命名要求或 scheme 合并规则，则 segmentation_mode 必须为 skill_guided。
3. 如果没有明确拆分要求，只输出 auto 模式。
4. 每个 scene_anchor 必须来自 Skill 原文，并包含 evidence_text。
5. 只输出 JSON，不要 Markdown，不要代码块，不要解释。

JSON schema：
{
  "source": "compiled_from_current_skill",
  "segmentation_mode": "auto" | "skill_guided",
  "has_explicit_segmentation": boolean,
  "priority": "final_prompt_inside_skill" | "default_auto",
  "reason": string,
  "scene_anchors": [
    {"label": string, "role": string, "required": boolean, "evidence_text": string}
  ],
  "scheme_rules": {
    "scheme_1": string,
    "scheme_2": string,
    "scheme_3": string
  },
  "quality_checks": string[]
}
""".strip()

VLM_TRANSITION_SYSTEM_PROMPT = """
你是 OpenClip 复拍场景转换裁判。你只根据用户提供的候选切点 JSON 和压缩 contact sheet 判断每个候选是否是复拍意义上的场景边界。

地点标签只能从以下选项中选择：后厨、前厅、宴会厅、走廊/通道、门口/大门、黑屏/标题卡、截图/信息插页、不确定。

判断优先级：
1. 第一优先级是 before/after 是否属于不同地点标签。地点变化才是复拍重点场景转换。
2. 第二优先级是结构转场：黑屏、标题卡、章节隔断、截图/信息插页。
3. 第三优先级才是主体、构图、机位、远近景、字幕变化。
4. 如果 before_location 与 after_location 相同，即使人物、字幕、构图、焦点、远近景变化，也不要判定为复拍重点场景转换。
5. 同一后厨内切厨师/老板/灶台/通道，不算地点转场；同一宴会厅内切人物/角度/远近景，不算地点转场；同一前厅内走动或对话延续，不算地点转场。
6. 宴会厅到前厅、前厅到后厨、后厨到宴会厅、宴会厅到走廊/通道、走廊/通道到门口/大门，才是复拍重点地点转场。
7. is_transition 表示广义画面切换；is_reshoot_boundary 表示复拍意义上的强边界。Scheme 重点只看 is_reshoot_boundary。
8. 必须输出严格 JSON，不要 Markdown，不要代码块，不要解释。

输出 schema：
{
  "judgement_source": "open_code_vlm",
  "items": [
    {
      "id": number,
      "time": number,
      "is_transition": boolean,
      "is_reshoot_boundary": boolean,
      "before_location": "后厨" | "前厅" | "宴会厅" | "走廊/通道" | "门口/大门" | "黑屏/标题卡" | "截图/信息插页" | "不确定",
      "after_location": "后厨" | "前厅" | "宴会厅" | "走廊/通道" | "门口/大门" | "黑屏/标题卡" | "截图/信息插页" | "不确定",
      "same_location": boolean,
      "location_changed": boolean,
      "transition_type": "location_change" | "structural_transition" | "same_location_change" | "not_transition" | "uncertain",
      "transition_label": string,
      "confidence": number,
      "reason": string
    }
  ]
}
""".strip()

def build_openclip_router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    repo = OpenClipRepository(ctx.engine)
    analysis_v1_run_lock = threading.RLock()
    analysis_v1_run_states: dict[int, dict[str, Any]] = {}
    analysis_v1_processes: dict[int, subprocess.Popen[str]] = {}
    analysis_v1_one_click_lock = threading.RLock()
    # One-click runs are isolated by Task/Session workspace. Keep the owning
    # task for each in-process run so different tasks may run concurrently,
    # while duplicate starts for the same task remain blocked.
    analysis_v1_active_one_click_runs: dict[str, int] = {}
    shared_video_plan_jobs = getattr(ctx, "koubo_video_plan_execution_jobs", None)
    if not isinstance(shared_video_plan_jobs, dict):
        shared_video_plan_jobs = {}
        setattr(ctx, "koubo_video_plan_execution_jobs", shared_video_plan_jobs)
    ANALYSIS_V1_ATTEMPT_FAMILY = "analysis_v1_tool_run"
    ANALYSIS_V1_TARGET = "analysis_v1.run_to_storyboard"
    ANALYSIS_V1_ONE_CLICK_TARGET = "analysis_v1_koubo_one_click_movie"
    ANALYSIS_V1_ACTIVE_STATUSES = {"queued", "running", "paused", "stopping"}
    ANALYSIS_V1_TERMINAL_STATUSES = {"completed", "completed_with_sync_error", "failed", "blocked", "cancelled", "stale_running"}
    ANALYSIS_V1_RUN_MODES = {"run_all", "run_range", "run_from_step", "run_only_step", "run_selected_steps", "rerun_all", "rerun_failed", "rerun_from_step"}
    ANALYSIS_V1_HEARTBEAT_STALE_MS = max(10_000, int(os.environ.get("OPENCREW_ANALYSIS_V1_HEARTBEAT_STALE_MS") or "120000"))
    ANALYSIS_V1_LOG_TAIL_LIMIT = max(1000, int(os.environ.get("OPENCREW_ANALYSIS_V1_LOG_TAIL_LIMIT") or "16000"))

    def formula_slots(video_formula: str) -> list[dict[str, str]]:
        formula = video_formula.strip()
        if formula == "Hook/Trust/CTA":
            return [
                {"key": "hook", "label": "Hook", "role": "前段强抓钩"},
                {"key": "trust", "label": "Trust", "role": "中段证据与可信信息"},
                {"key": "cta", "label": "CTA", "role": "末段动作引导或转化收束"},
            ]
        if formula == "老板巡店冲突型":
            return [
                {"key": "patrol_hook", "label": "巡店开场", "role": "建立老板进入现场与强判断开场"},
                {"key": "problem_exposure", "label": "问题暴露", "role": "暴露管理或服务问题"},
                {"key": "boss_judgement", "label": "老板判断", "role": "输出判断、标准或方法"},
                {"key": "value_close", "label": "价值收束", "role": "形成价值落点与动作引导"},
            ]
        if formula == "问题-过程-方案型":
            return [
                {"key": "problem", "label": "问题", "role": "提出问题或痛点"},
                {"key": "process", "label": "过程", "role": "展示过程、证据或推演"},
                {"key": "solution", "label": "方案", "role": "提出方案与价值结果"},
            ]
        if formula == "反常识抓钩型":
            return [
                {"key": "anti_common_sense", "label": "反常识抓钩", "role": "用反常识钩子打断预期"},
                {"key": "evidence", "label": "证据展开", "role": "给出证据、过程或案例"},
                {"key": "reframe", "label": "认知翻转", "role": "完成观点转向"},
                {"key": "action", "label": "动作引导", "role": "给出动作落点"},
            ]
        parts = [item.strip() for item in formula.replace("->", "/").replace("-", "/").split("/") if item.strip()]
        if not parts:
            return [{"key": "formula_slot", "label": formula or "公式槽位", "role": "围绕当前公式输出结构槽位"}]
        return [{"key": f"slot_{index}", "label": part, "role": f"{part}对应的结构槽位"} for index, part in enumerate(parts, start=1)]

    def dynamic_slot_summary(video_formula: str) -> str:
        slots = formula_slots(video_formula)
        return "\n".join([f"- {slot['label']}：{slot['role']}" for slot in slots])

    def workspace_dirs(session_row: dict[str, Any]) -> dict[str, Path]:
        root = Path(str(session_row["workspace_dir"]))
        return {
            "workspace": root,
            "inbox": root / "inbox",
            "input": root / "input",
            "audio": root / "audio",
            "meta": root / "meta",
            "transcripts": root / "transcripts",
            "storyboards": root / "storyboards",
            "keyframes": root / "keyframes",
            "clips": root / "clips",
            "reports": root / "reports",
            "schemes": root / "schemes",
            "outbox": root / "outbox",
            "history": root / "history",
        }

    def ensure_workspace(session_row: dict[str, Any]) -> None:
        workspace_dirs(session_row)["workspace"].mkdir(parents=True, exist_ok=True)

    def safe_session(session_id: int) -> dict[str, Any]:
        row = ctx.session_repo.get(session_id)
        if not row:
            raise HTTPException(status_code=404, detail="Session not found")
        expected_workspace = ctx.workspace_store.sessions_root() / str(session_id) / "workspace"
        current_workspace = Path(str(row.get("workspace_dir") or ""))
        if expected_workspace.exists() and current_workspace != expected_workspace:
            ctx.session_repo.update(session_id, workspace_dir=str(expected_workspace), updated_at=now_ms())
            row["workspace_dir"] = str(expected_workspace)
        return row

    def get_task(task_id: int) -> dict[str, Any]:
        row = repo.get_task(task_id)
        if not row:
            raise HTTPException(status_code=404, detail="OpenClip task not found")
        return row

    def openclip_workflow_mode(task_row: dict[str, Any]) -> str:
        return infer_openclip_workflow_mode(task_row, workspace=task_row.get("workspace_dir"))

    def ensure_analysis_v1_compatible_task(task_row: dict[str, Any]) -> None:
        mode = openclip_workflow_mode(task_row)
        if is_analysis_v1_compatible_workflow(mode):
            return
        raise HTTPException(
            status_code=400,
            detail={
                "code": "workflow_mode_not_analysis_v1",
                "message": f"Task #{task_row.get('id')} uses workflow_mode={mode}; use its dedicated workflow surface.",
                "workflow_mode": mode,
                "target": "dance_mimic_v1" if mode == WORKFLOW_DANCE_MIMIC_V1 else mode,
            },
        )

    def ensure_talking_head_v1_task(task_row: dict[str, Any]) -> None:
        mode = openclip_workflow_mode(task_row)
        if mode == WORKFLOW_PERSON_TALKING_HEAD_V1:
            return
        raise HTTPException(
            status_code=400,
            detail={
                "code": "workflow_mode_not_talking_head_v1",
                "message": f"Task #{task_row.get('id')} is not a person_talking_head_v1 task.",
                "workflow_mode": mode,
                "target": WORKFLOW_PERSON_TALKING_HEAD_V1,
            },
        )

    def add_session_event(session_id: int, kind: str, payload: dict[str, Any] | None, **event_fields: Any) -> None:
        ctx.session_event_service.add_event(session_id, kind, payload or {}, workflow_id="openclip_analysis", **event_fields)

    def frontend_task_url(request: Request, task_id: int) -> str:
        proto = str(request.headers.get("x-forwarded-proto") or request.url.scheme).strip() or request.url.scheme
        host = str(request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc).strip() or request.url.netloc
        prefix_raw = str(request.headers.get("x-forwarded-prefix") or "").strip()
        prefix = "" if not prefix_raw else (prefix_raw if prefix_raw.startswith("/") else f"/{prefix_raw}").rstrip("/")
        return f"{proto}://{host}{prefix}/#/openclip/tasks/{task_id}"

    def opencode_client_for(session_row: dict[str, Any]) -> OpenCodeSessionClient:
        try:
            return opencode_client_for_context(ctx, session_row, "OpenCode connection is incomplete. Finish Step 1 before using OpenClip.")
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def refresh_opencode_client_for(session_row: dict[str, Any], reason: str) -> OpenCodeSessionClient:
        result = discover_and_save_opencode_runtime(ctx, reason=reason)
        selected = result.get("selected") or {}
        if not selected.get("healthy") or not selected.get("username") or not selected.get("password"):
            raise HTTPException(
                status_code=400,
                detail="OpenCode authentication failed and automatic rediscovery did not find valid credentials. Reconnect OpenCode in Connection / Step 1, then retry.",
            )
        return opencode_client_for(session_row)

    WORKFLOW_ASSISTANT_BASE_PROMPT = """
You are the Workflow Assistant running inside an OpenCode Session.

Your job:
1. Understand the current Task, Session, Workspace, and Tool Library.
2. Plan before execution.
3. Explain recommended tools in a concise table with tool, purpose, reason, cost, and whether to run.
4. Wait for the user to confirm or edit the plan before execution.
5. High-cost, long-running, LLM, and VLM tools require explicit confirmation.
6. Do not invent tools that are not in the Tool Registry.
7. Do not output arbitrary shell commands as the execution source of truth.
8. Execution is performed by the backend Plan Runner from confirmed JSON Plan only.
9. If the user asks about progress, answer based on injected workflow run status.
10. If the user edits the plan, treat the latest confirmed plan as authoritative.
""".strip()

    OPENCLIP_ASSISTANT_ADDITION = """
The current workflow is OpenClip Analysis.
The goal is to use the Task Final Prompt, selected Run Model, and reference video to produce video semantic segmentation, retake descriptions, and three scheme outputs when requested.
Always respect Final Prompt business requirements over generic segmentation defaults.
""".strip()

    WORKFLOW_CONFIGS = {
        "openclip_analysis": {
            "id": "openclip_analysis",
            "name": "OC-Analysis",
            "source": OPENCLIP_SOURCE,
            "task_adapter": "openclip",
            "tool_library": {
                "root": "OpenCrew/ToolLibrary/Analysis",
                "registry": "OpenCrew/ToolLibrary/Analysis/tool_registry.json",
                "agent_guide": "OpenCrew/ToolLibrary/Analysis/AGENT_TOOL_GUIDE.md",
            },
            "assistant": {
                "system_prompt_template": "openclip_assistant_system_prompt",
                "quick_prompts": "openclip_quick_prompts",
            },
            "context_builder": "openclip_context_builder",
            "plan_schema": "workflow_tool_plan_v1",
            "runner": "tool_plan_runner",
        }
    }

    OPENCLIP_QUICK_PROMPTS = [
        {
            "id": "plan_segmentation_path",
            "label": "规划拆分路径",
            "mode": "fill",
            "prompt": "请浏览 Tool Library 中的所有工具，针对当前 Task 和 Session，使用这个 Task 的 Final Prompt / Run Model，以及 Video，规划整体视频拆分路径。请用简洁表格输出：工具、目的、理由、成本、是否建议执行。先不要执行。",
        },
        {
            "id": "check_outputs",
            "label": "检查已有产物",
            "mode": "send",
            "prompt": "请检查当前 workspace 已有产物，识别可以跳过的步骤、缺失的关键步骤，以及下一步建议。先不要执行任何工具。",
        },
        {
            "id": "optimize_balanced_scheme",
            "label": "优化均衡分镜",
            "mode": "fill",
            "prompt": "请基于当前已有输出，重点复盘 Scheme 2 均衡分镜是否适合复拍交付，并提出需要合并、拆分或重排的建议。先不要执行。",
        },
        {
            "id": "make_plan_json",
            "label": "生成执行计划",
            "mode": "send",
            "prompt": "请把当前讨论过的执行方案转换为 workflow_tool_plan_v1 JSON。只能使用 Tool Registry 中存在的工具，并标明成本、LLM/VLM 使用、依赖和 expected_outputs。先不要执行。",
        },
        {
            "id": "confirm_execution_plan",
            "label": "执行确认方案",
            "mode": "fill",
            "prompt": "请复核当前计划，指出高成本、LLM、VLM 或长耗时步骤，并列出需要我显式确认的项目。确认前不要执行。",
        },
        {
            "id": "review_quality",
            "label": "复盘质量",
            "mode": "fill",
            "prompt": "请复盘当前 OpenClip 交付物质量，检查三套方案、复拍描述、证据链和质量报告是否完整，并提出修复计划。先不要执行。",
        },
    ]

    def workflow_config(workflow_id: str) -> dict[str, Any]:
        config = WORKFLOW_CONFIGS.get(workflow_id)
        if not config:
            raise HTTPException(status_code=404, detail=f"Unknown workflow: {workflow_id}")
        return config

    def resolve_workflow_task(workflow_id: str, task_id: int) -> dict[str, Any]:
        config = workflow_config(workflow_id)
        if config["task_adapter"] != "openclip":
            raise HTTPException(status_code=404, detail=f"Unsupported workflow adapter: {config['task_adapter']}")
        return get_task(task_id)

    def tool_library_root() -> Path:
        root = Path(__file__).resolve().parents[3] / "ToolLibrary"
        analysis_root = root / "Analysis"
        return analysis_root if (analysis_root / "tool_registry.json").exists() else root

    def load_tool_registry_summary(warnings: list[str]) -> list[dict[str, Any]]:
        path = tool_library_root() / "tool_registry.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Failed to load Tool Registry: {exc}") from exc
        tools = payload.get("tools")
        if not isinstance(tools, list):
            raise HTTPException(status_code=500, detail="Tool Registry is invalid: tools must be a list")
        summary: list[dict[str, Any]] = []
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            summary.append({
                "id": tool.get("id"),
                "name": tool.get("name"),
                "stage": tool.get("stage"),
                "purpose": tool.get("agent_notes") or tool.get("description") or "",
                "cost_level": tool.get("cost_level"),
                "uses_llm": bool(tool.get("uses_llm")),
                "uses_vlm": bool(tool.get("uses_vlm")),
                "required_by_default": bool(tool.get("required_by_default")),
                "supports_resume": bool(tool.get("supports_resume")),
                "hard_dependencies": tool.get("hard_dependencies") or [],
                "soft_dependencies": tool.get("soft_dependencies") or [],
                "main_outputs": tool.get("main_outputs") or [],
                "args_schema": tool.get("args_schema") or None,
            })
        if not summary:
            warnings.append("Tool Registry loaded but contains no tools.")
        return summary

    def load_agent_guide_summary(warnings: list[str]) -> str:
        path = tool_library_root() / "AGENT_TOOL_GUIDE.md"
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            warnings.append("Agent guide is missing; assistant will rely on registry summary only.")
            return ""
        keep: list[str] = []
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#") or "confirmation" in stripped.lower() or "VLM" in stripped or "LLM" in stripped or "Run `" in stripped or "Skip" in stripped or "Cost" in stripped or "Recommended" in stripped:
                keep.append(stripped)
            if len("\n".join(keep)) > 5000:
                break
        return "\n".join(keep[:120])

    def workspace_state_summary(task_row: dict[str, Any], registry_summary: list[dict[str, Any]]) -> dict[str, Any]:
        root = Path(str(task_row.get("workspace_dir") or ""))
        existing: list[str] = []
        if root.exists() and root.is_dir():
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                rel = path.relative_to(root).as_posix()
                if rel.startswith("history/") or "/." in rel or Path(rel).name.startswith("."):
                    continue
                existing.append(rel)
                if len(existing) >= 300:
                    break
        expected: list[str] = []
        for tool in registry_summary:
            for rel in tool.get("main_outputs") or []:
                if isinstance(rel, str) and rel and not rel.endswith("/"):
                    expected.append(rel)
        existing_set = set(existing)
        missing = [rel for rel in dict.fromkeys(expected) if rel not in existing_set][:120]
        shallow: list[str] = []
        if root.exists() and root.is_dir():
            for child in sorted(root.iterdir(), key=lambda item: item.name)[:80]:
                marker = "/" if child.is_dir() else ""
                shallow.append(f"{child.name}{marker}")
        return {
            "existing_outputs": existing,
            "missing_expected_outputs": missing,
            "tree_summary": "\n".join(shallow),
            "workspace_exists": root.exists() and root.is_dir(),
        }

    def build_openclip_assistant_context(workflow_id: str, task_id: int) -> tuple[dict[str, Any], list[str]]:
        task_row = resolve_workflow_task(workflow_id, task_id)
        warnings: list[str] = []
        registry_summary = load_tool_registry_summary(warnings)
        guide_summary = load_agent_guide_summary(warnings)
        run_provider = str(task_row.get("run_model_provider") or "").strip()
        run_model_id = str(task_row.get("run_model_id") or "").strip()
        context = {
            "workflow": {
                "id": workflow_id,
                "name": workflow_config(workflow_id)["name"],
            },
            "task": {
                "id": int(task_row["id"]),
                "title": str(task_row.get("title") or f"OpenClip Task #{task_row['id']}"),
                "status": str(task_row.get("status") or "draft"),
            },
            "session": {
                "id": int(task_row["session_id"]),
                "opencode_session_id": str(task_row.get("opencode_session_id") or ""),
                "workspace_dir": str(task_row.get("workspace_dir") or ""),
            },
            "business_context": {
                "final_prompt": str(task_row.get("final_prompt") or ""),
                "run_model": f"{run_provider}/{run_model_id}" if run_provider or run_model_id else "",
                "reference_video_path": str(task_row.get("reference_video_path") or ""),
                "industry": str(task_row.get("industry") or ""),
                "persona": str(task_row.get("persona") or ""),
                "target_audience": str(task_row.get("target_audience") or ""),
                "product_info": str(task_row.get("product_info") or ""),
                "constraints": str(task_row.get("constraints") or ""),
                "analysis_goal": str(task_row.get("analysis_goal") or ""),
                "video_formula": str(task_row.get("video_formula") or ""),
            },
            "workspace_state": workspace_state_summary(task_row, registry_summary),
            "tool_library": {
                "registry_summary": registry_summary,
                "agent_guide_summary": guide_summary,
            },
            "current_plan": None,
            "current_run": None,
        }
        return context, warnings

    def build_assistant_system_prompt(context: dict[str, Any]) -> str:
        compact_context = json.dumps(context, ensure_ascii=False, indent=2)
        return f"{WORKFLOW_ASSISTANT_BASE_PROMPT}\n\n{OPENCLIP_ASSISTANT_ADDITION}\n\n[Workflow Context]\n{compact_context}"

    def assistant_bootstrap_payload(workflow_id: str, task_id: int) -> dict[str, Any]:
        config = workflow_config(workflow_id)
        task_row = resolve_workflow_task(workflow_id, task_id)
        context, warnings = build_openclip_assistant_context(workflow_id, task_id)
        messages: list[dict[str, Any]] = []
        opencode_session_id = str(task_row.get("opencode_session_id") or "")
        can_chat = bool(opencode_session_id)
        if opencode_session_id:
            try:
                messages = opencode_client_for(task_row).messages(opencode_session_id, limit=120)
            except Exception as exc:
                can_chat = False
                warnings.append(f"OpenCode messages unavailable: {exc}")
        return {
            "workflow": {"id": config["id"], "name": config["name"], "source": config["source"]},
            "task": context["task"],
            "session": context["session"],
            "context": context,
            "quick_prompts": OPENCLIP_QUICK_PROMPTS,
            "messages": messages,
            "plan": None,
            "run": None,
            "capabilities": {
                "can_chat": can_chat,
                "can_abort": bool(opencode_session_id),
                "can_execute": False,
            },
            "warnings": warnings,
        }

    def last_completed_assistant(messages: list[dict[str, Any]], started_after: int) -> str | None:
        for message in reversed(messages):
            info = message.get("info") or {}
            if info.get("role") != "assistant":
                continue
            completed = int(((info.get("time") or {}).get("completed") or 0) or 0)
            if completed < started_after:
                continue
            texts = [str(part.get("text") or "") for part in (message.get("parts") or []) if part.get("type") == "text"]
            text = "\n".join([item.strip() for item in texts if item.strip()]).strip()
            if text:
                return text
        return None

    def serialize_prompt_models(session_row: dict[str, Any]) -> dict[str, Any]:
        client = opencode_client_for(session_row)
        try:
            provider_payload = client.providers()
        except OpenCodeAuthError as exc:
            add_session_event(int(session_row["id"]), "openclip.opencode_auth_refreshed", {"stage": "providers", "detail": str(exc)})
            client = refresh_opencode_client_for(session_row, "openclip.prompt_models.providers_401")
            try:
                provider_payload = client.providers()
            except OpenCodeAuthError as retry_exc:
                raise HTTPException(status_code=400, detail=str(retry_exc)) from retry_exc
        connected = set([str(item) for item in (provider_payload.get("connected") or []) if item])
        default_map = provider_payload.get("default") or {}
        items: list[dict[str, Any]] = []
        default_model = {"providerID": "", "modelID": ""}
        for provider in provider_payload.get("all") or []:
            provider_id = str(provider.get("id") or "").strip()
            if not provider_id or provider_id not in connected:
                continue
            provider_name = str(provider.get("name") or provider_id)
            models = provider.get("models") or {}
            for model in models.values():
                model_id = str((model or {}).get("id") or "").strip()
                if not model_id:
                    continue
                items.append({
                    "providerID": provider_id,
                    "providerName": provider_name,
                    "modelID": model_id,
                    "modelName": str((model or {}).get("name") or model_id),
                    "reasoning": bool((model or {}).get("reasoning")),
                    "contextLimit": int((((model or {}).get("limit") or {}).get("context") or 0) or 0),
                    "inputModalities": list((((model or {}).get("modalities") or {}).get("input") or [])),
                })
            configured_default = str(default_map.get(provider_id) or "").strip()
            if configured_default and not default_model["providerID"]:
                default_model = {"providerID": provider_id, "modelID": configured_default}
        items.sort(key=lambda item: (str(item["providerName"]), str(item["modelName"])))
        if not default_model["providerID"] and items:
            default_model = {"providerID": str(items[0]["providerID"]), "modelID": str(items[0]["modelID"])}
        return {"items": items, "default_model": default_model}

    def resolve_model(
        session_row: dict[str, Any],
        provider: str,
        model_id: str,
        purpose: str,
        role: str = "admin",
        surface: str = SURFACE_ANALYSIS_V1_PROMPT,
    ) -> tuple[dict[str, str], dict[str, Any]]:
        payload = serialize_prompt_models(session_row)
        return resolve_prompt_model_for_role(ctx, role, surface, payload, provider, model_id, purpose)

    def mask_for_role(role: str, surface: str, payload: Any) -> Any:
        return mask_model_fields_for_role(ctx, role, surface, payload)

    def mask_prompt_models(role: str, surface: str, payload: dict[str, Any]) -> dict[str, Any]:
        return mask_prompt_models_for_role(ctx, role, surface, payload)

    def build_simple_prompt(task_row: dict[str, Any]) -> str:
        def strip_terminal_punctuation(value: str) -> str:
            return value.rstrip("。！？!?；;，,、. ")

        video_formula = str(task_row.get("video_formula") or "当前视频公式").strip()
        industry = str(task_row.get("industry") or "通用行业").strip()
        persona = str(task_row.get("persona") or "核心角色").strip()
        target_audience = str(task_row.get("target_audience") or "目标受众").strip()
        product_info = strip_terminal_punctuation(str(task_row.get("product_info") or "当前产品或服务").strip())
        analysis_goal = str(task_row.get("analysis_goal") or "提取整体公式").strip()
        constraints = strip_terminal_punctuation(str(task_row.get("constraints") or "无额外限制条件").strip())
        formula_slot_text = dynamic_slot_summary(video_formula)
        shooting_scene_list = "如特殊要求中已明确列出主拍摄场景，请严格使用该列表；如未提供，请根据视频实际画面归纳主拍摄场景。"

        return load_simple_prompt_template().format(
            video_formula=video_formula,
            industry=industry,
            persona=persona,
            target_audience=target_audience,
            product_info=product_info,
            analysis_goal=analysis_goal,
            formula_slots=formula_slot_text,
            shooting_scene_list=shooting_scene_list,
            constraints=constraints,
        )

    def build_prompt_builder_input(task_row: dict[str, Any], simple_prompt: str) -> str:
        fields = [
            ("Industry", task_row.get("industry") or "通用行业"),
            ("Persona", task_row.get("persona") or "核心角色"),
            ("Target Audience", task_row.get("target_audience") or "目标受众"),
            ("Product Info", task_row.get("product_info") or "未提供"),
            ("Constraints", task_row.get("constraints") or "无额外限制条件"),
            ("Analysis Goal", task_row.get("analysis_goal") or "提取整体公式"),
            ("Video Formula", task_row.get("video_formula") or "当前视频公式"),
            ("Formula Slots", dynamic_slot_summary(str(task_row.get("video_formula") or ""))),
            ("Simple Prompt", simple_prompt),
        ]
        return "\n".join([f"{label}: {value}" for label, value in fields])

    def prompt_kind_spec(prompt_kind: str) -> dict[str, str]:
        if str(prompt_kind or "").strip().lower() == "storyboard":
            return {
                "kind": "storyboard",
                "simple_field": "storyboard_simple_prompt",
                "final_field": "storyboard_final_prompt",
                "system_prompt": SRT_STORYBOARD_PROMPT_BUILDER_SYSTEM_PROMPT,
                "purpose": "analysis_v1.prompt_builder.generate_storyboard_final_prompt",
            }
        return {
            "kind": "rewrite",
            "simple_field": "rewrite_simple_prompt",
            "final_field": "rewrite_final_prompt",
            "system_prompt": SRT_REWRITE_PROMPT_BUILDER_SYSTEM_PROMPT,
            "purpose": "analysis_v1.prompt_builder.generate_rewrite_final_prompt",
        }

    def build_skill_content(task_row: dict[str, Any], final_prompt: str) -> str:
        return (
            "你是 OpenClip Analysis Skill。你的职责是基于 Final Prompt，对输入视频执行稳定、可复用、可校验的视频拆分分析，并输出适合复拍、复盘、结构提炼和片段导出的结果。\n\n"
            "## 一、输入定义\n"
            "本 Skill 的唯一业务输入是 Final Prompt。\n"
            "Simple Prompt 和项目参数只用于生成 Final Prompt，不得作为额外业务输入参与判断。\n"
            "如 Final Prompt 与默认经验冲突，始终以 Final Prompt 为最高优先级约束。\n\n"
            "## 二、固定工具链\n"
            "1. 视频读取与拆分\n"
            "- ffprobe：读取时长、帧率、编码、时间轴\n"
            "- ffmpeg：抽音频、抽首帧、抽关键帧、拆分视频、导出片段\n"
            "2. 视觉证据采集\n"
            "- PySceneDetect 必须作为第一层可信起点，先提取内容切点、自适应切点、亮度/阈值切点，后续细分只能在这些候选场景基础上继续向下找转场关键帧。\n"
            "- 基于 FPS 与帧变化自适应扫描视频，不得把固定时间间隔抽帧作为主策略；固定间隔只允许作为兜底密度检查。\n"
            "- 识别帧间差异、颜色变化、亮度突变、结构变化、主体/动作变化和镜头稳定点，输出可追溯的关键帧候选与候选边界。\n"
            "- 标题卡、黑屏、纯色隔断页和短暂结构隔断帧优先级高于普通视觉去重，即使持续时间短也必须保留为证据。\n"
            "3. 语音识别\n"
            "- mlx-whisper 或 Whisper：优先使用更高质量模型，整段 ASR、分段对白、时间戳、密度校验；可接受更慢以换取更准确语义。\n"
            "4. 结构判断\n"
            "- 结合视觉关键帧、候选边界、ASR、OCR 辅助语义、对白语义转折、问答结构、情绪变化、主体变化、动作变化和转场信号，按照 Final Prompt 的要求完成结构判断和镜头拆分。\n"
            "- OCR 只用于结合语音做逻辑语义分析，不作为标题卡或隔断页判断的主依据。\n"
            "- 如 Final Prompt 包含显式锚点，必须用视觉/语音/OCR 辅助证据匹配；匹配不到时标记低置信度或未匹配，禁止按顺序强行贴标签。\n"
            "- 关键帧、标题卡、OCR、ASR、锚点匹配都是证据，不是最终视频片段；最终 Scheme 必须由边界决策生成完整覆盖全视频的连续片段。\n\n"
            "- 是否为转场画面必须经过视觉大模型或其结构化转场判断层确认；本地 CV 只负责提供候选证据。\n\n"
            "## 三、固定执行流程\n"
            "1. 读取视频元数据，输出 meta/video_metadata.json。\n"
            "2. 抽取音频与基础帧，输出 audio/reference_audio.wav 和关键帧基础文件。\n"
            "3. 执行视觉变化扫描，输出 meta/frame_change_scores.json、meta/visual_keyframes.json、meta/visual_scene_candidates.json、meta/boundary_candidates.json、meta/title_card_candidates.json。\n"
            "4. 执行语音识别，输出 transcripts/transcript.json、meta/asr_segments.json、transcripts/original_asr_full.txt。\n"
            "5. 提取关键帧与证据帧，输出 keyframes/、keyframes/visual_candidates/、meta/segment_keyframes.json。\n"
            "6. 根据视觉候选边界、ASR 边界、停顿、语义转折、问答变化、情绪变化、主体变化、动作变化构建候选逻辑片段，输出 meta/logical_segment_candidates.json。\n"
            "7. 基于 Final Prompt 提取当前视频的实际公式实例与槽位映射，输出 meta/formula_extraction.json、meta/formula_slot_analysis.json、storyboards/formula_slot_mapping.md、meta/story_formula.json。\n"
            "8. 生成主逻辑段时间线，输出 meta/logical_segments.json。\n"
            "9. 从主逻辑段和边界决策派生三套完整覆盖全视频的方案：细分镜、均衡分镜、粗分镜。三套方案只允许粒度不同，不允许逻辑冲突，不允许只导出证据窗口。\n"
            "10. 导出三套方案的 mp4、txt、srt、复拍描述.md、建议文件名.txt，所有最终视频必须写入 schemes/scheme_*。\n"
            "11. 执行质量校验，输出 reports/quality_check.json、reports/analysis_summary.json、reports/analysis_summary.md、reports/openclip_main_result.json。\n\n"
            "## 四、三套方案规则\n"
            "1. 细分镜 scheme_1：保留真实视觉变化和语言逻辑变化，可以比 Final Prompt 锚点更细，但必须从 0 到视频结束完整覆盖。\n"
            "2. 均衡分镜 scheme_2：在 scheme_1 证据链基础上贴近业务锚点和复拍结构，以完整表达功能为单位适度合并，必须完整覆盖全视频。\n"
            "3. 粗分镜 scheme_3：按大场景和大表达阶段合并，用于快速确认整体结构，必须完整覆盖全视频。\n"
            "4. 三套方案的段数不得预设固定值，必须由 Final Prompt Context、视频实际逻辑密度、场景变化、对白推进和表达功能动态生成。\n"
            "5. 证据窗口不得直接作为 Scheme 导出片段；证据只写入 meta/ 和 keyframes/ 用于验证。\n"
            "6. 公式槽位时间范围不得按视频总时长均分，必须从逻辑段中归纳映射。\n\n"
            "## 五、命名与复拍描述\n"
            "每个片段必须输出：序号、开始-结束、标题、景别、机位、运镜、主体动作、转场类型、复拍要点、所属公式槽位、切分依据。\n"
            "命名规则固定为：[序号]_[N-N]_[标题].mp4，并输出同名 txt 和 srt。\n"
            "最终 MP4、同名 txt、同名 srt 的写入路径固定为：schemes/scheme_1/（细分镜）、schemes/scheme_2/（均衡分镜）、schemes/scheme_3/（粗分镜）。\n"
            "每个 schemes/scheme_* 目录下还必须包含复拍描述.md 和建议文件名.txt。\n"
            "标题应优先采用通用、可迁移、易复拍的表达，除非 Final Prompt 明确要求保留行业术语。\n\n"
            "## 六、质量校验\n"
            "必须检查：evidence_traceable、segmentation_reasonable、structure_complete、scheme_consistency、deliverables_complete。\n\n"
            "## 七、工作目录\n"
            "工作目录就是当前 session workspace。\n"
            f"Python 解释器优先使用 {BACKEND_VENV_PYTHON}。\n"
            f"主分析脚本固定使用 {OPENCLIP_RUNNER}。\n"
            "所有关键结果必须写入 workspace/input、meta、audio、keyframes、transcripts、storyboards、reports、schemes。\n"
            "OpenClip 最新交付视频只允许写入 workspace/schemes/scheme_1、workspace/schemes/scheme_2、workspace/schemes/scheme_3；禁止将最新交付 MP4 写入 clips/。\n\n"
            "## 八、禁止事项\n"
            "禁止预设固定段数。\n"
            "禁止按时长均分公式槽位。\n"
            "禁止只按视觉切点机械拆分。\n"
            "禁止只按字幕句号切分。\n"
            "禁止按固定时间间隔作为主抽帧逻辑。\n"
            "禁止把 Final Prompt 锚点按顺序贴到旧时间段。\n"
            "禁止把关键帧、标题卡、锚点匹配等证据窗口直接导出成 Scheme 视频。\n"
            "禁止 Scheme 内出现时间缺口；Scheme 1、Scheme 2、Scheme 3 都必须完整覆盖全视频。\n"
            "禁止跳过中间产物生成。\n"
            "禁止把 OpenClip 最新交付视频写入 clips/；页面展示与人工验收均以 schemes/scheme_* 下的 MP4 为准。\n"
            "禁止脱离 Final Prompt 的业务要求空泛命名。\n\n"
            "## Final Prompt Context\n"
            f"{final_prompt.strip()}\n"
        )

    def build_package_spec(task_row: dict[str, Any]) -> dict[str, Any]:
        slot_schema = formula_slots(str(task_row.get("video_formula") or ""))
        required_files = [
            "input/project_input.json",
            "input/skill.txt",
            "input/current_skill.txt",
            "meta/video_metadata.json",
            "meta/asr_segments.json",
            "meta/asr_quality.json",
            "meta/scene_candidates.json",
            "meta/frame_change_scores.json",
            "meta/pyscenedetect_scenes.json",
            "meta/pyscenedetect_cuts.json",
            "meta/visual_keyframes.json",
            "meta/keyframe_clusters.json",
            "meta/deduped_visual_keyframes.json",
            "meta/visual_scene_candidates.json",
            "meta/background_transition_candidates.json",
            "meta/vlm_transition_judgement.json",
            "meta/boundary_candidates.json",
            "meta/title_card_candidates.json",
            "meta/segment_keyframes.json",
            "meta/anchor_matching.json",
            "meta/segmentation_decision.json",
            "meta/scheme_1_decision.json",
            "meta/timeline_analysis.json",
            "meta/story_formula.json",
            "meta/schemes.json",
            "meta/formula_slot_analysis.json",
            "meta/result_package_spec.json",
            "transcripts/original_asr_full.txt",
            "transcripts/original_dialogue_segments_scheme_1.json",
            "transcripts/original_dialogue_segments_scheme_2.json",
            "transcripts/original_dialogue_segments_scheme_3.json",
            "transcripts/formula_slot_dialogues.json",
            "transcripts/rhythm_density_table.csv",
            "storyboards/scheme_1_fine_storyboard.md",
            "storyboards/scheme_2_balanced_storyboard.md",
            "storyboards/scheme_3_coarse_storyboard.md",
            "storyboards/formula_slot_mapping.md",
            "storyboards/scheme_filename_manifest.json",
            "schemes/scheme_1/复拍描述.md",
            "schemes/scheme_1/建议文件名.txt",
            "schemes/scheme_2/复拍描述.md",
            "schemes/scheme_2/建议文件名.txt",
            "schemes/scheme_3/复拍描述.md",
            "schemes/scheme_3/建议文件名.txt",
            "reports/analysis_summary.json",
            "reports/analysis_summary.md",
            "reports/componentized_analysis.json",
            "reports/quality_check.json",
            "reports/openclip_main_result.json",
        ]
        return {
            "workflow": "OpenClip - Analysis",
            "summary_format": "json_primary",
            "video_formula": str(task_row.get("video_formula") or ""),
            "slot_schema": slot_schema,
            "export_all_scheme_videos": True,
            "dialogues_per_scheme": True,
            "schemes": [
                {"id": "scheme_1", "name": "Scheme 1", "label": "细分镜"},
                {"id": "scheme_2", "name": "Scheme 2", "label": "均衡分镜", "recommended": True},
                {"id": "scheme_3", "name": "Scheme 3", "label": "粗分镜"},
            ],
            "required_files": required_files,
            "clip_naming_rule": "[序号]_[开始-结束]_[标题].mp4",
        }

    def sync_session_files(session_row: dict[str, Any]) -> None:
        workspace = workspace_dirs(session_row)["workspace"]
        if not workspace.exists():
            return
        for path in workspace.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(workspace).as_posix()
            stat = path.stat()
            origin = "uploaded" if rel.startswith("inbox/") else "generated"
            downloadable = 1 if not Path(rel).name.startswith(".") else 0
            ctx.session_repo.upsert_file(int(session_row["id"]), rel, "file", int(stat.st_size), origin, downloadable, int(stat.st_mtime * 1000))

    def write_text(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def write_json(path: Path, payload: Any) -> None:
        write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))

    def write_json_atomic(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(path)

    def stable_json_sha256(payload: Any) -> str:
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def sync_analysis_v1_storyboard_dialogues(workspace: Path, edits: dict[str, str]) -> dict[str, Any]:
        def apply_dialogue_edit(item: Any) -> bool:
            if not isinstance(item, dict):
                return False
            srt_id = str(item.get("srt_id") or "")
            if not srt_id or srt_id not in edits:
                return False
            next_text = edits[srt_id]
            changed = False
            if "dialogue" in item and str(item.get("dialogue") or "") != next_text:
                item["dialogue"] = next_text
                changed = True
            if "text" in item and str(item.get("text") or "") != next_text:
                item["text"] = next_text
                changed = True
            if "dialogue" not in item and "text" not in item:
                item["dialogue"] = next_text
                changed = True
            return changed

        def sync_storyboard_payload(payload: Any) -> int:
            if not isinstance(payload, dict):
                return 0
            updated = 0
            for shot in payload.get("shots") or []:
                if not isinstance(shot, dict):
                    continue
                for scene in shot.get("scenes") or []:
                    if not isinstance(scene, dict):
                        continue
                    for key in ("dialogue_items", "dialogues"):
                        values = scene.get(key)
                        if not isinstance(values, list):
                            continue
                        for item in values:
                            if apply_dialogue_edit(item):
                                updated += 1
                    scene_srt_ids = [str(value) for value in (scene.get("srt_ids") or []) if str(value or "")]
                    if len(scene_srt_ids) == 1 and scene_srt_ids[0] in edits and "dialogue" in scene:
                        next_text = edits[scene_srt_ids[0]]
                        if str(scene.get("dialogue") or "") != next_text:
                            scene["dialogue"] = next_text
                            updated += 1
            if updated:
                payload["updated_at"] = now_ms()
            return updated

        updated_files: list[dict[str, Any]] = []
        active_source_signature: dict[str, Any] = {}
        for rel in ANALYSIS_V1_STORYBOARD_SYNC_RELS:
            path = workspace / rel
            if not path.exists():
                continue
            try:
                current = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"Invalid JSON: {rel}") from exc
            updated = sync_storyboard_payload(current)
            if updated:
                write_json_atomic(path, current)
                updated_files.append({"path": rel, "updated": updated})

        source_candidates: list[tuple[int, str, Path, dict[str, Any]]] = []
        for rel in (*ANALYSIS_V1_STORYBOARD_TOOL_SOURCE_RELS, ANALYSIS_V1_STORYBOARD_REL):
            path = workspace / rel
            if not path.exists():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            try:
                mtime_ms = int(path.stat().st_mtime * 1000)
            except OSError:
                mtime_ms = 0
            source_candidates.append((mtime_ms, rel, path, payload))
        if source_candidates:
            _mtime, rel, path, payload = sorted(source_candidates, key=lambda item: item[0], reverse=True)[0]
            active_source_signature = {
                "path": rel,
                "sha256": stable_json_sha256(payload),
                "tool": str(payload.get("tool") or ""),
                "mtime_ms": int(path.stat().st_mtime * 1000),
            }

        edit_path = workspace / ANALYSIS_V1_STORYBOARD_EDIT_REL
        if edit_path.exists():
            try:
                edit_payload = json.loads(edit_path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"Invalid JSON: {ANALYSIS_V1_STORYBOARD_EDIT_REL}") from exc
            updated = sync_storyboard_payload(edit_payload)
            signature_changed = False
            if isinstance(edit_payload, dict) and active_source_signature:
                signature_fields = {
                    "source_storyboard_path": active_source_signature["path"],
                    "source_storyboard_sha256": active_source_signature["sha256"],
                    "source_storyboard_tool": active_source_signature["tool"],
                    "source_storyboard_mtime_ms": active_source_signature["mtime_ms"],
                }
                for key, value in signature_fields.items():
                    if edit_payload.get(key) != value:
                        edit_payload[key] = value
                        signature_changed = True
            if updated or signature_changed:
                edit_payload["updated_at"] = now_ms()
                write_json_atomic(edit_path, edit_payload)
                updated_files.append({"path": ANALYSIS_V1_STORYBOARD_EDIT_REL, "updated": updated})

        return {
            "updated": sum(int(item.get("updated") or 0) for item in updated_files),
            "files": updated_files,
            "active_source": active_source_signature,
        }

    def stage_reference_video(session_row: dict[str, Any], reference_video_path: str) -> dict[str, Any]:
        source = Path(reference_video_path).expanduser()
        if not source.exists() or not source.is_file():
            raise HTTPException(status_code=400, detail=f"Reference video not found: {source}")
        ensure_workspace(session_row)
        target = workspace_dirs(session_row)["inbox"] / f"reference_video{source.suffix or '.mp4'}"
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
        add_session_event(int(session_row["id"]), "openclip.reference_video.staged", {"source": str(source), "target": str(target)})
        return {"source": str(source), "target": str(target)}

    def archive_current_outputs(session_row: dict[str, Any], attempt_no: int) -> None:
        ensure_workspace(session_row)
        dirs = workspace_dirs(session_row)
        archive_root: Path | None = None
        for name in ["input", "meta", "audio", "transcripts", "storyboards", "keyframes", "clips", "reports", "schemes", "outbox"]:
            source = dirs[name]
            if not source.exists():
                continue
            if not any(source.iterdir()):
                if name == "clips":
                    source.rmdir()
                continue
            if archive_root is None:
                archive_root = dirs["history"] / f"attempt_{attempt_no:03d}_{now_ms()}"
                archive_root.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(archive_root / name))

    def stage_run_inputs(session_row: dict[str, Any], task_row: dict[str, Any], skill_version: dict[str, Any], attempt_row: dict[str, Any]) -> dict[str, Any]:
        staged_video = stage_reference_video(session_row, str(task_row.get("reference_video_path") or ""))
        dirs = workspace_dirs(session_row)
        project_input = {
            "reference_video_path": str(staged_video["target"]),
            "industry": str(task_row.get("industry") or ""),
            "persona": str(task_row.get("persona") or ""),
            "target_audience": str(task_row.get("target_audience") or ""),
            "product_info": str(task_row.get("product_info") or ""),
            "constraints": str(task_row.get("constraints") or ""),
            "analysis_goal": str(task_row.get("analysis_goal") or ""),
            "video_formula": str(task_row.get("video_formula") or ""),
        }
        package_spec = build_package_spec(task_row)
        write_json(dirs["input"] / "project_input.json", project_input)
        for legacy_input in (dirs["input"] / "simple_prompt.txt", dirs["input"] / "final_prompt.txt"):
            if legacy_input.exists():
                legacy_input.unlink()
        current_skill = str(skill_version.get("skill_content") or "")
        write_text(dirs["input"] / "skill.txt", current_skill)
        write_text(dirs["input"] / "current_skill.txt", current_skill)
        write_json(dirs["input"] / "analysis_directives.json", default_skill_directives("Skill has not been compiled yet"))
        write_json(dirs["meta"] / "result_package_spec.json", package_spec)
        write_json(dirs["meta"] / "run_manifest.json", {
            "workflow": "OpenClip - Analysis",
            "input_policy": "current_skill_only",
            "attempt_id": int(attempt_row["id"]),
            "attempt_no": int(attempt_row["attempt_no"]),
            "video_formula": str(task_row.get("video_formula") or ""),
            "staged_video": staged_video,
            "skill_version": {
                "id": int(skill_version.get("id") or 0),
                "name": str(skill_version.get("name") or ""),
            },
            "created_at": now_ms(),
        })
        write_text(dirs["reports"] / "analysis_summary.md", "# OpenClip Analysis\n\nPending execution.\n")
        return package_spec

    DEFAULT_STORYBOARD_QUICK_CONFIG = {
        "enabled": True,
        "target_scene_seconds": 8.0,
        "target_shot_seconds": 16.0,
        "split_tolerance_seconds": 2.0,
        "language_boundary_mode": "balanced",
    }

    def normalize_storyboard_quick_config(value: Any) -> dict[str, Any]:
        raw: dict[str, Any] = {}
        if isinstance(value, dict):
            raw = value
        elif isinstance(value, str) and value.strip():
            try:
                parsed = json.loads(value)
                raw = parsed if isinstance(parsed, dict) else {}
            except Exception:
                raw = {}

        def positive_number(key: str, fallback: float) -> float:
            try:
                parsed = float(raw.get(key))
                return parsed if parsed > 0 else fallback
            except Exception:
                return fallback

        scene = positive_number("target_scene_seconds", DEFAULT_STORYBOARD_QUICK_CONFIG["target_scene_seconds"])
        shot = positive_number("target_shot_seconds", DEFAULT_STORYBOARD_QUICK_CONFIG["target_shot_seconds"])
        tolerance = positive_number("split_tolerance_seconds", DEFAULT_STORYBOARD_QUICK_CONFIG["split_tolerance_seconds"])
        mode = str(raw.get("language_boundary_mode") or DEFAULT_STORYBOARD_QUICK_CONFIG["language_boundary_mode"]).strip().lower()
        if mode not in {"strict", "balanced", "loose"}:
            mode = DEFAULT_STORYBOARD_QUICK_CONFIG["language_boundary_mode"]
        return {
            "enabled": raw.get("enabled") is not False,
            "target_scene_seconds": max(1.0, scene),
            "target_shot_seconds": max(1.0, shot),
            "split_tolerance_seconds": max(0.0, tolerance),
            "language_boundary_mode": mode,
        }

    def storyboard_quick_config_json(value: Any) -> str:
        return json.dumps(normalize_storyboard_quick_config(value), ensure_ascii=False, sort_keys=True)

    def storyboard_quick_config_from_row(row: dict[str, Any]) -> dict[str, Any]:
        return normalize_storyboard_quick_config(row.get("storyboard_quick_config_json"))

    def normalize_task_payload(task_id: int, payload: OpenClipTaskUpdatePayload) -> dict[str, Any]:
        rewrite_simple = payload.rewrite_simple_prompt.strip() or payload.simple_prompt.strip()
        rewrite_final = payload.rewrite_final_prompt.strip() or payload.final_prompt.strip()
        return {
            "id": task_id,
            "reference_video_path": payload.reference_video_path.strip(),
            "industry": payload.industry.strip(),
            "persona": payload.persona.strip(),
            "target_audience": payload.target_audience.strip(),
            "product_info": payload.product_info.strip(),
            "constraints": payload.constraints.strip(),
            "analysis_goal": payload.analysis_goal.strip(),
            "video_formula": payload.video_formula.strip(),
            "simple_prompt": rewrite_simple,
            "final_prompt": rewrite_final,
            "rewrite_simple_prompt": rewrite_simple,
            "rewrite_final_prompt": rewrite_final,
            "storyboard_simple_prompt": payload.storyboard_simple_prompt.strip(),
            "storyboard_final_prompt": payload.storyboard_final_prompt.strip(),
            "storyboard_quick_config_json": storyboard_quick_config_json(payload.storyboard_quick_config),
            "prompt_model_provider": payload.prompt_model_provider.strip(),
            "prompt_model_id": payload.prompt_model_id.strip(),
            "run_model_provider": payload.run_model_provider.strip(),
            "run_model_id": payload.run_model_id.strip(),
        }

    def current_prompt_draft(task_row: dict[str, Any]) -> dict[str, Any] | None:
        final_prompt = str(task_row.get("final_prompt") or "").strip()
        rewrite_final = str(task_row.get("rewrite_final_prompt") or final_prompt).strip()
        storyboard_final = str(task_row.get("storyboard_final_prompt") or "").strip()
        if not rewrite_final and not storyboard_final:
            return None
        return {
            "id": None,
            "task_id": int(task_row["id"]),
            "name": "Current Draft",
            "notes": "",
            "reference_video_path": str(task_row.get("reference_video_path") or ""),
            "industry": str(task_row.get("industry") or ""),
            "persona": str(task_row.get("persona") or ""),
            "target_audience": str(task_row.get("target_audience") or ""),
            "product_info": str(task_row.get("product_info") or ""),
            "constraints": str(task_row.get("constraints") or ""),
            "analysis_goal": str(task_row.get("analysis_goal") or ""),
            "video_formula": str(task_row.get("video_formula") or ""),
            "simple_prompt": str(task_row.get("simple_prompt") or ""),
            "rewrite_simple_prompt": str(task_row.get("rewrite_simple_prompt") or task_row.get("simple_prompt") or ""),
            "rewrite_final_prompt": rewrite_final,
            "storyboard_simple_prompt": str(task_row.get("storyboard_simple_prompt") or ""),
            "storyboard_final_prompt": storyboard_final,
            "storyboard_quick_config_json": str(task_row.get("storyboard_quick_config_json") or ""),
            "storyboard_quick_config": storyboard_quick_config_from_row(task_row),
            "prompt_model_provider": str(task_row.get("prompt_model_provider") or ""),
            "prompt_model_id": str(task_row.get("prompt_model_id") or ""),
            "final_prompt": rewrite_final,
            "created_at": int(task_row.get("updated_at") or task_row.get("created_at") or 0),
        }

    def prompt_matches_task(task_row: dict[str, Any], prompt_version: dict[str, Any] | None) -> bool:
        if not prompt_version:
            return False
        fields = [
            "reference_video_path",
            "industry",
            "persona",
            "target_audience",
            "product_info",
            "constraints",
            "analysis_goal",
            "video_formula",
            "simple_prompt",
            "final_prompt",
            "rewrite_simple_prompt",
            "rewrite_final_prompt",
            "storyboard_simple_prompt",
            "storyboard_final_prompt",
            "storyboard_quick_config_json",
        ]
        return all(str(task_row.get(field) or "") == str(prompt_version.get(field) or "") for field in fields)

    def current_prompt_version(task_row: dict[str, Any]) -> dict[str, Any] | None:
        draft = current_prompt_draft(task_row)
        version_id = int(task_row.get("current_prompt_version_id") or 0)
        if version_id:
            version = hydrate_prompt_version(task_row, repo.get_prompt_version(version_id))
            if draft and not prompt_matches_task(task_row, version):
                return draft
            return version or draft
        versions = repo.list_prompt_versions(int(task_row["id"]))
        if versions:
            version = hydrate_prompt_version(task_row, versions[0])
            if draft and not prompt_matches_task(task_row, version):
                return draft
            return version or draft
        return draft

    def base_prompt_version(task_row: dict[str, Any]) -> dict[str, Any] | None:
        version_id = int(task_row.get("current_prompt_version_id") or 0)
        if not version_id:
            return None
        return hydrate_prompt_version(task_row, repo.get_prompt_version(version_id))

    def active_prompt_version(task_row: dict[str, Any], preferred_version_id: int | None = None) -> dict[str, Any] | None:
        if preferred_version_id:
            version = hydrate_prompt_version(task_row, repo.get_prompt_version(preferred_version_id))
            return version or current_prompt_version(task_row)
        return current_prompt_version(task_row)

    def hydrate_prompt_version(task_row: dict[str, Any], version: dict[str, Any] | None) -> dict[str, Any] | None:
        if not version:
            return None
        def resolve_field(name: str) -> str:
            if version.get(name) not in (None, ""):
                return str(version.get(name) or "")
            return str(task_row.get(name) or "")
        return {
            **version,
            "reference_video_path": resolve_field("reference_video_path"),
            "industry": resolve_field("industry"),
            "persona": resolve_field("persona"),
            "target_audience": resolve_field("target_audience"),
            "product_info": resolve_field("product_info"),
            "constraints": resolve_field("constraints"),
            "analysis_goal": resolve_field("analysis_goal"),
            "video_formula": resolve_field("video_formula"),
            "simple_prompt": resolve_field("simple_prompt"),
            "rewrite_simple_prompt": resolve_field("rewrite_simple_prompt") or resolve_field("simple_prompt"),
            "rewrite_final_prompt": resolve_field("rewrite_final_prompt") or str(version.get("final_prompt") or task_row.get("final_prompt") or ""),
            "storyboard_simple_prompt": resolve_field("storyboard_simple_prompt"),
            "storyboard_final_prompt": resolve_field("storyboard_final_prompt"),
            "storyboard_quick_config_json": resolve_field("storyboard_quick_config_json"),
            "storyboard_quick_config": normalize_storyboard_quick_config(resolve_field("storyboard_quick_config_json")),
        }

    def current_skill_version(task_row: dict[str, Any]) -> dict[str, Any] | None:
        version_id = int(task_row.get("current_skill_version_id") or 0)
        if version_id:
            return repo.get_skill_version(version_id)
        versions = repo.list_skill_versions(int(task_row["id"]))
        return versions[0] if versions else None

    def skill_matches_task(task_row: dict[str, Any], skill_version: dict[str, Any] | None) -> bool:
        if not skill_version:
            return False
        return str(task_row.get("generated_skill_content") or "") == str(skill_version.get("skill_content") or "")

    def serialize_task_detail(task_row: dict[str, Any]) -> dict[str, Any]:
        session_row = safe_session(int(task_row["session_id"]))
        current_prompt = current_prompt_version(task_row)
        base_prompt = base_prompt_version(task_row)
        prompt_dirty = bool(base_prompt and not prompt_matches_task(task_row, base_prompt))
        current_skill = current_skill_version(task_row)
        skill_draft_content = str(task_row.get("generated_skill_content") or "")
        skill_dirty = bool(current_skill and skill_draft_content and not skill_matches_task(task_row, current_skill))
        return {
            "task": {
                "id": int(task_row["id"]),
                "session_id": int(task_row["session_id"]),
                "title": str(task_row.get("title") or session_row.get("title") or f"Task {task_row['id']}"),
                "status": str(task_row.get("status") or "draft"),
                "workflow_mode": openclip_workflow_mode(task_row),
                "session_status": str(session_row.get("status") or "queued"),
                "opencode_session_id": str(session_row.get("opencode_session_id") or ""),
                "workspace_dir": str(session_row.get("workspace_dir") or ""),
                "reference_video_path": str(task_row.get("reference_video_path") or ""),
                "industry": str(task_row.get("industry") or ""),
                "persona": str(task_row.get("persona") or ""),
                "target_audience": str(task_row.get("target_audience") or ""),
                "product_info": str(task_row.get("product_info") or ""),
                "constraints": str(task_row.get("constraints") or ""),
                "analysis_goal": str(task_row.get("analysis_goal") or ""),
                "video_formula": str(task_row.get("video_formula") or ""),
                "simple_prompt": str(task_row.get("simple_prompt") or ""),
                "final_prompt": str(task_row.get("final_prompt") or ""),
                "rewrite_simple_prompt": str(task_row.get("rewrite_simple_prompt") or task_row.get("simple_prompt") or ""),
                "rewrite_final_prompt": str(task_row.get("rewrite_final_prompt") or task_row.get("final_prompt") or ""),
                "storyboard_simple_prompt": str(task_row.get("storyboard_simple_prompt") or ""),
                "storyboard_final_prompt": str(task_row.get("storyboard_final_prompt") or ""),
                "storyboard_quick_config_json": str(task_row.get("storyboard_quick_config_json") or ""),
                "storyboard_quick_config": storyboard_quick_config_from_row(task_row),
                "prompt_model_provider": str(task_row.get("prompt_model_provider") or ""),
                "prompt_model_id": str(task_row.get("prompt_model_id") or ""),
                "generated_skill_content": skill_draft_content,
                "skill_version_name": str(task_row.get("skill_version_name") or ""),
                "skill_version_notes": str(task_row.get("skill_version_notes") or ""),
                "run_model_provider": str(task_row.get("run_model_provider") or ""),
                "run_model_id": str(task_row.get("run_model_id") or ""),
                "slot_schema": formula_slots(str(task_row.get("video_formula") or "")),
                "current_prompt_version_id": int(task_row.get("current_prompt_version_id") or 0) or None,
                "current_skill_version_id": int(task_row.get("current_skill_version_id") or 0) or None,
                "latest_attempt_id": int(task_row.get("latest_attempt_id") or 0) or None,
                "created_at": int(task_row.get("created_at") or 0),
                "updated_at": int(task_row.get("updated_at") or 0),
            },
            "current_prompt_version": current_prompt,
            "base_prompt_version": base_prompt,
            "prompt_version_dirty": prompt_dirty,
            "current_skill_version": current_skill,
            "skill_version_dirty": skill_dirty,
            "prompt_versions": repo.list_prompt_versions(int(task_row["id"])),
            "skill_versions": repo.list_skill_versions(int(task_row["id"])),
            "attempts": repo.list_attempts(int(task_row["id"])),
            "package_spec": build_package_spec(task_row),
            "options": {
                "industry": INDUSTRY_OPTIONS,
                "persona": PERSONA_OPTIONS,
                "target_audience": TARGET_AUDIENCE_OPTIONS,
                "analysis_goal": ANALYSIS_GOAL_OPTIONS,
                "video_formula": VIDEO_FORMULA_OPTIONS,
            },
        }

    def preview_text(value: str, limit: int = 500) -> str:
        text = str(value or "").strip()
        if len(text) <= limit:
            return text
        return text[:limit].rstrip() + "..."

    def default_skill_directives(reason: str = "Current Skill does not contain explicit scene-level segmentation requirements") -> dict[str, Any]:
        return {
            "source": "compiled_from_current_skill",
            "segmentation_mode": "auto",
            "has_explicit_segmentation": False,
            "priority": "default_auto",
            "reason": reason,
            "scene_anchors": [],
            "scheme_rules": {},
            "quality_checks": ["prompt_directive_applied"],
        }

    def extract_json_object(text: str) -> dict[str, Any]:
        value = text.strip()
        if value.startswith("```"):
            value = value.strip("`").strip()
            if value.lower().startswith("json"):
                value = value[4:].strip()
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            start = value.find("{")
            end = value.rfind("}")
            if start >= 0 and end > start:
                parsed = json.loads(value[start:end + 1])
                return parsed if isinstance(parsed, dict) else {}
            raise

    def read_json_file(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def normalize_existing_video_plan_execution_state(workspace: Path) -> dict[str, Any]:
        state_path = workspace / "SessionOutput/storyboard/video_plan_execution_state.json"
        state = read_json_file(state_path)
        return normalize_video_plan_execution_state(
            workspace,
            state,
            sc=SimpleNamespace(
                text=lambda value, default="": str(value if value is not None else default).strip(),
                write_json=write_storyboard_json,
                video_plan_execution_jobs=shared_video_plan_jobs,
            ),
        )

    def image_file_part(path: Path, workspace: Path) -> dict[str, Any]:
        mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return {
            "type": "file",
            "mime": mime,
            "filename": path.relative_to(workspace).as_posix() if path.is_relative_to(workspace) else path.name,
            "url": f"data:{mime};base64,{encoded}",
        }

    def model_supports_image(session_row: dict[str, Any], model: dict[str, str]) -> bool:
        try:
            payload = serialize_prompt_models(session_row)
            for item in payload.get("items") or []:
                if str(item.get("providerID") or "") == str(model.get("providerID") or "") and str(item.get("modelID") or "") == str(model.get("modelID") or ""):
                    modalities = [str(value) for value in (item.get("inputModalities") or [])]
                    return True if not modalities else "image" in modalities
        except Exception:
            return True
        return True

    def local_transition_judgement_item(candidate_id: int, item: dict[str, Any], reason: str = "not_reviewed_by_vlm") -> dict[str, Any]:
        confidence = float(item.get("confidence") or 0.0)
        transition_type = str(item.get("type") or "transition_candidate")
        is_structural = transition_type in {"title_card_or_separator"}
        return {
            "id": candidate_id,
            "time": item.get("time"),
            "review_status": "not_reviewed",
            "is_transition": transition_type in {"pyscenedetect_cut", "title_card_or_separator"} or confidence >= 0.74,
            "is_reshoot_boundary": is_structural,
            "before_location": "不确定",
            "after_location": "不确定",
            "same_location": False,
            "location_changed": False,
            "transition_type": "structural_transition" if is_structural else "uncertain",
            "transition_label": transition_type,
            "confidence": round(min(0.88, confidence), 3),
            "reason": reason,
            "sources": item.get("sources") or [],
            "before_keyframe": item.get("before_keyframe"),
            "after_keyframe": item.get("after_keyframe"),
        }

    def parse_vlm_bool(value: Any, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "1", "yes", "y", "是"}:
                return True
            if lowered in {"false", "0", "no", "n", "否"}:
                return False
        return default

    def normalize_vlm_location(value: Any) -> str:
        location = str(value or "不确定").strip()
        return location if location in {"后厨", "前厅", "宴会厅", "走廊/通道", "门口/大门", "黑屏/标题卡", "截图/信息插页", "不确定"} else "不确定"

    def normalize_vlm_transition_batch(raw: dict[str, Any], candidates_by_id: dict[int, dict[str, Any]], batch_ids: set[int]) -> dict[int, dict[str, Any]]:
        raw_items = raw.get("items") if isinstance(raw.get("items"), list) else []
        judged: dict[int, dict[str, Any]] = {}
        for raw_item in raw_items:
            if not isinstance(raw_item, dict):
                continue
            try:
                candidate_id = int(raw_item.get("id") or 0)
            except Exception:
                continue
            if candidate_id not in candidates_by_id or candidate_id not in batch_ids:
                continue
            confidence = max(0.0, min(0.99, float(raw_item.get("confidence") or 0.0)))
            candidate = candidates_by_id[candidate_id]
            before_location = normalize_vlm_location(raw_item.get("before_location"))
            after_location = normalize_vlm_location(raw_item.get("after_location"))
            inferred_same_location = before_location == after_location and before_location != "不确定"
            same_location = parse_vlm_bool(raw_item.get("same_location"), default=inferred_same_location) if "same_location" in raw_item else inferred_same_location
            location_changed = parse_vlm_bool(raw_item.get("location_changed"), default=before_location != after_location and "不确定" not in {before_location, after_location})
            transition_label = str(raw_item.get("transition_label") or candidate.get("type") or "transition_candidate")
            transition_type = str(raw_item.get("transition_type") or "uncertain")
            structural = before_location in {"黑屏/标题卡", "截图/信息插页"} or after_location in {"黑屏/标题卡", "截图/信息插页"} or transition_label in {"title_card_separator", "graphic_insert_transition", "black_screen_separator"}
            if same_location:
                location_changed = False
            is_reshoot_boundary = parse_vlm_bool(raw_item.get("is_reshoot_boundary")) and (location_changed or structural) and not same_location
            judged[candidate_id] = {
                "id": candidate_id,
                "time": candidate.get("time"),
                "review_status": "reviewed_by_vlm",
                "is_transition": parse_vlm_bool(raw_item.get("is_transition")),
                "is_reshoot_boundary": is_reshoot_boundary,
                "before_location": before_location,
                "after_location": after_location,
                "same_location": same_location,
                "location_changed": location_changed,
                "transition_type": transition_type,
                "location_field_source": "vlm_schema",
                "transition_label": transition_label,
                "confidence": round(confidence, 3),
                "reason": str(raw_item.get("reason") or "vlm_judgement"),
                "sources": sorted(set((candidate.get("sources") or []) + ["open_code_vlm"])),
                "before_keyframe": candidate.get("before_keyframe"),
                "after_keyframe": candidate.get("after_keyframe"),
            }
        return judged

    def build_merged_vlm_transition_judgement(candidates: list[dict[str, Any]], judged: dict[int, dict[str, Any]], failures: list[dict[str, Any]]) -> dict[str, Any]:
        by_id = {int(item.get("id") or index): item for index, item in enumerate(candidates, start=1)}
        items = [judged[candidate_id] if candidate_id in judged else local_transition_judgement_item(candidate_id, item) for candidate_id, item in sorted(by_id.items())]
        reviewed_count = len(judged)
        candidate_count = len(by_id)
        return {
            "judgement_source": "open_code_vlm" if reviewed_count else "local_evidence_fallback",
            "candidate_count": candidate_count,
            "reviewed_candidate_count": reviewed_count,
            "vlm_review_coverage": round(reviewed_count / max(candidate_count, 1), 4),
            "batch_failures": failures,
            "items": items,
        }

    def run_vlm_transition_judgement(session_row: dict[str, Any], model: dict[str, str], attempt_id: int | None) -> dict[str, Any]:
        dirs = workspace_dirs(session_row)
        workspace = dirs["workspace"]
        meta_dir = dirs["meta"]
        candidates = read_json_file(meta_dir / "background_transition_candidates.json").get("items") or []
        contact_sheets_meta = read_json_file(meta_dir / "transition_contact_sheets.json")
        fallback_contact_sheet_meta = read_json_file(meta_dir / "transition_contact_sheet.json")
        batches = contact_sheets_meta.get("batches") if isinstance(contact_sheets_meta.get("batches"), list) else []
        if not batches and fallback_contact_sheet_meta:
            batches = [fallback_contact_sheet_meta]
        if not candidates:
            result = {"judgement_source": "local_evidence_fallback", "fallback_reason": "no_transition_candidates", "items": []}
            write_json(meta_dir / "vlm_transition_judgement.json", result)
            return result
        if not model_supports_image(session_row, model):
            by_id = {int(item.get("id") or index): item for index, item in enumerate(candidates, start=1)}
            result = {"judgement_source": "local_evidence_fallback", "fallback_reason": "selected_model_does_not_support_image_input", "candidate_count": len(by_id), "reviewed_candidate_count": 0, "vlm_review_coverage": 0.0, "items": [local_transition_judgement_item(candidate_id, item, "selected_model_does_not_support_image_input") for candidate_id, item in sorted(by_id.items())]}
            write_json(meta_dir / "vlm_transition_judgement.json", result)
            add_session_event(int(session_row["id"]), "openclip.vlm_transition.skipped", {"attempt_id": attempt_id, "reason": "model_without_image_input", "model": model})
            return result
        if not batches:
            by_id = {int(item.get("id") or index): item for index, item in enumerate(candidates, start=1)}
            result = {"judgement_source": "local_evidence_fallback", "fallback_reason": "contact_sheet_missing", "candidate_count": len(by_id), "reviewed_candidate_count": 0, "vlm_review_coverage": 0.0, "items": [local_transition_judgement_item(candidate_id, item, "contact_sheet_missing") for candidate_id, item in sorted(by_id.items())]}
            write_json(meta_dir / "vlm_transition_judgement.json", result)
            return result

        candidates_by_id = {int(item.get("id") or index): item for index, item in enumerate(candidates, start=1)}
        all_judged: dict[int, dict[str, Any]] = {}
        failures: list[dict[str, Any]] = []
        client = opencode_client_for(session_row)
        add_session_event(int(session_row["id"]), "openclip.vlm_transition.started", {"attempt_id": attempt_id, "candidate_count": len(candidates_by_id), "batch_count": len(batches)})
        for batch_index, batch in enumerate(batches, start=1):
            contact_sheet_path = workspace / str(batch.get("path") or "")
            batch_ids = {int(value) for value in (batch.get("candidate_ids") or []) if int(value or 0) in candidates_by_id}
            if not batch_ids:
                batch_ids = {int(item.get("id") or 0) for item in (batch.get("items") or []) if int(item.get("id") or 0) in candidates_by_id}
            if not contact_sheet_path.exists() or not batch_ids:
                failures.append({"batch_index": batch_index, "reason": "contact_sheet_or_candidates_missing", "path": str(batch.get("path") or "")})
                continue
            simplified = []
            for candidate_id in sorted(batch_ids, key=lambda value: float(candidates_by_id[value].get("time") or 0.0)):
                item = candidates_by_id[candidate_id]
                simplified.append({"id": item.get("id"), "time": item.get("time"), "type": item.get("type"), "confidence": item.get("confidence"), "reason": item.get("reason"), "sources": item.get("sources") or []})
            prompt = (
                "请根据 contact sheet 判断候选切点是否是真正转场。contact sheet 中每格显示 before/after 两张压缩关键帧，标签 #id 对应候选 id。\n"
                "只判断下列候选，输出严格 JSON。\n\n"
                + json.dumps({"batch_index": batch_index, "candidates": simplified, "contact_sheet": batch}, ensure_ascii=False, indent=2)
            )
            started_at = now_ms()
            try:
                client.prompt_async(str(session_row["opencode_session_id"]), prompt, model=model, system=VLM_TRANSITION_SYSTEM_PROMPT, parts=[{"type": "text", "text": prompt}, image_file_part(contact_sheet_path, workspace)])
                deadline = time.time() + 240
                assistant_text = ""
                while time.time() < deadline:
                    assistant_text = last_completed_assistant(client.messages(str(session_row["opencode_session_id"]), limit=180), started_at) or ""
                    if assistant_text:
                        break
                    time.sleep(1)
                if not assistant_text:
                    raise RuntimeError("OpenCode timed out before returning VLM transition judgement")
                batch_judged = normalize_vlm_transition_batch(extract_json_object(assistant_text), candidates_by_id, batch_ids)
                all_judged.update(batch_judged)
                add_session_event(int(session_row["id"]), "openclip.vlm_transition.batch_completed", {"attempt_id": attempt_id, "batch_index": batch_index, "batch_count": len(batches), "candidate_count": len(batch_ids), "reviewed_count": len(batch_judged), "preview": preview_text(assistant_text)})
            except Exception as exc:
                failures.append({"batch_index": batch_index, "reason": str(exc), "path": str(batch.get("path") or "")})
                add_session_event(int(session_row["id"]), "openclip.vlm_transition.batch_failed", {"attempt_id": attempt_id, "batch_index": batch_index, "message": str(exc)})

        result = build_merged_vlm_transition_judgement(candidates, all_judged, failures)
        write_json(meta_dir / "vlm_transition_judgement.json", result)
        add_session_event(int(session_row["id"]), "openclip.vlm_transition.completed", {"attempt_id": attempt_id, "judgement_source": result.get("judgement_source"), "candidate_count": result.get("candidate_count"), "reviewed_candidate_count": result.get("reviewed_candidate_count"), "vlm_review_coverage": result.get("vlm_review_coverage"), "transition_count": len([item for item in result.get("items") or [] if item.get("is_transition")])})
        return result

    def normalize_skill_directives(payload: dict[str, Any], skill_version: dict[str, Any] | None, attempt_id: int | None, compiler_model: dict[str, str]) -> dict[str, Any]:
        directives = default_skill_directives()
        if isinstance(payload, dict):
            directives.update(payload)
        mode = str(directives.get("segmentation_mode") or "auto").strip()
        anchors = directives.get("scene_anchors") if isinstance(directives.get("scene_anchors"), list) else []
        normalized_anchors = []
        for item in anchors:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or "").strip()
            if not label:
                continue
            normalized_anchors.append({
                "label": label,
                "role": str(item.get("role") or label).strip(),
                "required": bool(item.get("required", True)),
                "evidence_text": str(item.get("evidence_text") or "").strip(),
            })
        if mode != "skill_guided" or not normalized_anchors:
            directives.update(default_skill_directives(str(directives.get("reason") or "No explicit scene anchors were compiled from Current Skill")))
            normalized_anchors = []
        else:
            directives["segmentation_mode"] = "skill_guided"
            directives["has_explicit_segmentation"] = True
            directives["priority"] = "final_prompt_inside_skill"
        directives["scene_anchors"] = normalized_anchors
        directives["source"] = "compiled_from_current_skill"
        directives["attempt_id"] = attempt_id
        directives["skill_version_id"] = int((skill_version or {}).get("id") or 0) or None
        directives["prompt_version_id"] = int((skill_version or {}).get("prompt_version_id") or 0) or None
        directives["compiler_model"] = compiler_model
        directives["compiled_at"] = now_ms()
        if not isinstance(directives.get("quality_checks"), list):
            directives["quality_checks"] = []
        for check in ["prompt_directive_applied", "scene_anchor_coverage", "scheme_consistency"]:
            if check not in directives["quality_checks"]:
                directives["quality_checks"].append(check)
        return directives

    def fallback_scene_anchors_from_skill(current_skill: str) -> list[dict[str, Any]]:
        candidates: list[str] = []
        for pattern in [r"严格依据[“\"]([^”\"]+)[”\"]的场景", r"配合[“\"]([^”\"]+)[”\"]的场景", r"按照[“\"]?([^。\n]+片段)[”\"]?来拆"]:
            for match in re.finditer(pattern, current_skill):
                candidates.append(match.group(1))
        if not candidates:
            return []
        text = max(candidates, key=len)
        raw_parts = [part.strip() for part in re.split(r"[、,，/\-]+", text) if part.strip()]
        labels: list[str] = []
        for part in raw_parts:
            normalized = part.strip("“”\" ")
            if "综合" in normalized and "钩" in normalized:
                label = "综合钩子"
            elif "黑" in normalized and "隔断" in normalized:
                label = "黑屏隔断"
            elif normalized == "钩子":
                label = "综合钩子"
            elif normalized == "隔断" or "问题整改" in normalized:
                label = "黑屏隔断"
            elif "前厅" in normalized and ("了解" in normalized or "问题" in normalized):
                label = "前厅了解问题"
            elif "后厨" in normalized and "叫人" in normalized:
                label = "后厨叫人"
            elif "前厅" in normalized and "争吵" in normalized:
                label = "前厅争吵"
            elif "大门" in normalized or "落地" in normalized:
                label = "走向大门落地问题"
            elif normalized == "前厅":
                label = "前厅了解问题"
            elif normalized == "后厨":
                label = "后厨叫人"
            else:
                label = normalized
            if label and label not in labels:
                labels.append(label)
        if len(labels) < 3:
            return []
        return [{"label": label, "role": label, "required": True, "evidence_text": text} for label in labels]

    def fallback_skill_guided_directives(current_skill: str, reason: str) -> dict[str, Any]:
        anchors = fallback_scene_anchors_from_skill(current_skill)
        if not anchors:
            return default_skill_directives(reason)
        return {
            "source": "compiled_from_current_skill",
            "segmentation_mode": "skill_guided",
            "has_explicit_segmentation": True,
            "priority": "final_prompt_inside_skill",
            "reason": f"Fallback extracted explicit scene anchors from Current Skill after compiler error: {reason}",
            "scene_anchors": anchors,
            "scheme_rules": {},
            "quality_checks": ["prompt_directive_applied", "scene_anchor_coverage", "scheme_consistency"],
        }

    def write_skill_directives(session_row: dict[str, Any], directives: dict[str, Any]) -> None:
        dirs = workspace_dirs(session_row)
        write_json(dirs["input"] / "analysis_directives.json", directives)
        write_json(dirs["meta"] / "skill_compilation.json", directives)

    def compile_current_skill_to_directives(session_row: dict[str, Any], current_skill: str, model: dict[str, str], skill_version: dict[str, Any] | None, attempt_id: int | None) -> dict[str, Any]:
        client = opencode_client_for(session_row)
        started_at = now_ms()
        add_session_event(int(session_row["id"]), "openclip.skill_compiler.started", {"attempt_id": attempt_id, "input_policy": "current_skill_only", "model": model})
        try:
            client.prompt_async(str(session_row["opencode_session_id"]), current_skill, model=model, system=SKILL_COMPILER_SYSTEM_PROMPT)
            deadline = time.time() + 180
            compiler_text = ""
            while time.time() < deadline:
                messages = client.messages(str(session_row["opencode_session_id"]), limit=120)
                compiler_text = last_completed_assistant(messages, started_at) or ""
                if compiler_text:
                    break
                time.sleep(1)
            if not compiler_text:
                raise RuntimeError("OpenCode timed out before returning skill directives")
            directives = normalize_skill_directives(extract_json_object(compiler_text), skill_version, attempt_id, model)
            if directives.get("segmentation_mode") == "auto" and fallback_scene_anchors_from_skill(current_skill):
                directives = normalize_skill_directives(fallback_skill_guided_directives(current_skill, "compiler_output_missing_explicit_segmentation_schema"), skill_version, attempt_id, model)
            write_skill_directives(session_row, directives)
            add_session_event(int(session_row["id"]), "openclip.skill_compiler.completed", {"attempt_id": attempt_id, "segmentation_mode": directives.get("segmentation_mode"), "scene_anchor_count": len(directives.get("scene_anchors") or []), "preview": preview_text(compiler_text)})
            return directives
        except Exception as exc:
            directives = normalize_skill_directives(fallback_skill_guided_directives(current_skill, str(exc)), skill_version, attempt_id, model)
            write_skill_directives(session_row, directives)
            add_session_event(int(session_row["id"]), "openclip.skill_compiler.fallback", {"attempt_id": attempt_id, "message": str(exc), "segmentation_mode": directives.get("segmentation_mode"), "scene_anchor_count": len(directives.get("scene_anchors") or [])})
            return directives

    def run_local_analysis(session_row: dict[str, Any], phase: str = "full") -> None:
        ensure_workspace(session_row)
        command = [str(BACKEND_VENV_PYTHON), str(OPENCLIP_RUNNER), "--workspace", ".", "--phase", phase]
        result = subprocess.run(command, cwd=str(session_row["workspace_dir"]), check=False, text=True, capture_output=True)
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "OpenClip analysis runner failed").strip()
            raise RuntimeError(message)

    def stream_prompt(session_id: int, prompt: str, system_prompt: str, model: dict[str, str], attempt_id: int | None = None, rerun: bool = False) -> None:
        session_row = safe_session(session_id)
        prompt_started_at = now_ms()
        ctx.session_repo.update(session_id, status="running", started_at=prompt_started_at, finished_at=None, updated_at=prompt_started_at)
        user_event_payload = {"text": prompt, "model": model, "rerun": rerun, "attempt_id": attempt_id}
        if attempt_id:
            user_event_payload.update({"purpose": "openclip.analysis.run", "input_policy": "current_skill_only"})
        add_session_event(session_id, "user.message", user_event_payload)
        stop_event = threading.Event()

        def on_event(payload: dict[str, Any]) -> None:
            raw_kind = str(payload.get("type") or "event").strip() or "event"
            kind = raw_kind if raw_kind.startswith("opencode.") else f"opencode.{raw_kind}"
            raw_properties = payload.get("properties") or {}
            properties = raw_properties if isinstance(raw_properties, dict) else {}
            add_session_event(
                session_id,
                kind,
                properties if isinstance(raw_properties, dict) else {"value": raw_properties},
                visibility="internal",
                event_scope="debug",
                family="opencode",
            )
            if raw_kind == "session.status":
                status = str(((properties.get("status") or {}).get("type") or "running")).strip() or "running"
                ctx.session_repo.update(session_id, status=status, updated_at=now_ms())

        try:
            task_row = repo.get_task_by_session(session_id) or {}
            if attempt_id:
                repo.update_attempt(attempt_id, status="running", started_at=prompt_started_at)
                add_session_event(session_id, "openclip.current_skill.injected", {"attempt_id": attempt_id, "input_policy": "current_skill_only", "preview": preview_text(prompt)})
                skill_version = None
                task_row_for_skill = repo.get_task_by_session(session_id) or {}
                if task_row_for_skill:
                    skill_version = repo.get_skill_version(int(task_row_for_skill.get("current_skill_version_id") or 0))
                directives = compile_current_skill_to_directives(session_row, prompt, model, skill_version, attempt_id)
                add_session_event(session_id, "openclip.runner.started", {"attempt_id": attempt_id, "input_policy": "current_skill_only", "phase": "evidence"})
                run_local_analysis(session_row, phase="evidence")
                add_session_event(session_id, "openclip.runner.completed", {"attempt_id": attempt_id, "input_policy": "current_skill_only", "phase": "evidence", "segmentation_mode": directives.get("segmentation_mode"), "scene_anchor_count": len(directives.get("scene_anchors") or [])})
                run_vlm_transition_judgement(session_row, model, attempt_id)
                add_session_event(session_id, "openclip.runner.started", {"attempt_id": attempt_id, "input_policy": "current_skill_only", "phase": "finalize"})
                run_local_analysis(session_row, phase="finalize")
                add_session_event(session_id, "openclip.runner.completed", {"attempt_id": attempt_id, "input_policy": "current_skill_only", "phase": "finalize", "segmentation_mode": directives.get("segmentation_mode"), "scene_anchor_count": len(directives.get("scene_anchors") or [])})
                sync_session_files(safe_session(session_id))
            client = opencode_client_for(session_row)
            collector = threading.Thread(target=client.collect_events, args=(str(session_row["opencode_session_id"]), stop_event, on_event), daemon=True)
            collector.start()
            main_prompt_started_at = now_ms()
            client.prompt_async(str(session_row["opencode_session_id"]), prompt, model=model, system=system_prompt)
            deadline = time.time() + 1800
            assistant_text: str | None = None
            while time.time() < deadline:
                messages = client.messages(str(session_row["opencode_session_id"]), limit=120)
                assistant_text = last_completed_assistant(messages, main_prompt_started_at)
                if assistant_text:
                    break
                time.sleep(1)
            if not assistant_text:
                raise RuntimeError("OpenCode timed out before returning an assistant response")
            add_session_event(session_id, "assistant.final", {"text": assistant_text, "attempt_id": attempt_id, "fallback": False})
            ctx.session_repo.update(session_id, status="waiting_input", last_summary=assistant_text[:4000], finished_at=now_ms(), updated_at=now_ms())
            sync_session_files(safe_session(session_id))
            if attempt_id:
                reports_dir = workspace_dirs(session_row)["reports"]
                run_manifest_path = workspace_dirs(session_row)["meta"] / "run_manifest.json"
                manifest = json.loads(run_manifest_path.read_text(encoding="utf-8")) if run_manifest_path.exists() else {}
                repo.update_attempt(attempt_id, status="completed", summary=assistant_text[:4000], result_manifest_json=json.dumps(manifest, ensure_ascii=False), finished_at=now_ms())
                if task_row:
                    repo.update_task(int(task_row["id"]), status="completed", latest_attempt_id=attempt_id, updated_at=now_ms())
                add_session_event(session_id, "openclip.analysis.completed", {"attempt_id": attempt_id, "report": str(reports_dir / 'analysis_summary.json')})
        except Exception as exc:
            add_session_event(session_id, "session.error", {"message": str(exc), "attempt_id": attempt_id})
            ctx.session_repo.update(session_id, status="failed", finished_at=now_ms(), updated_at=now_ms())
            if attempt_id:
                repo.update_attempt(attempt_id, status="failed", summary=str(exc), finished_at=now_ms())
                task_row = repo.get_task_by_session(session_id)
                if task_row:
                    repo.update_task(int(task_row["id"]), status="failed", latest_attempt_id=attempt_id, updated_at=now_ms())
        finally:
            stop_event.set()

    def start_prompt_thread(session_id: int, prompt: str, system_prompt: str, model: dict[str, str], attempt_id: int | None = None, rerun: bool = False) -> None:
        thread = threading.Thread(target=stream_prompt, args=(session_id, prompt, system_prompt, model, attempt_id, rerun), daemon=True)
        thread.start()

    def analysis_v1_workspace(task_row: dict[str, Any]) -> Path:
        workspace_dir = str(task_row.get("workspace_dir") or "").strip()
        if workspace_dir:
            return Path(workspace_dir)
        return Path(str(safe_session(int(task_row["session_id"]))["workspace_dir"]))

    def sync_analysis_v1_variables_prompt_snapshot(task_row: dict[str, Any]) -> bool:
        workspace = analysis_v1_workspace(task_row)
        variables_path = workspace / "SessionContext" / "Variables.json"
        if not variables_path.exists():
            return False
        try:
            variables = json.loads(variables_path.read_text(encoding="utf-8"))
        except Exception:
            return False
        if not isinstance(variables, dict):
            return False
        for field in (
            "industry",
            "persona",
            "target_audience",
            "product_info",
            "constraints",
            "analysis_goal",
            "video_formula",
            "simple_prompt",
            "final_prompt",
            "rewrite_simple_prompt",
            "rewrite_final_prompt",
            "storyboard_simple_prompt",
            "storyboard_final_prompt",
            "run_model_provider",
            "run_model_id",
        ):
            variables[field] = str(task_row.get(field) or "")
        variables["current_prompt_version_id"] = int(task_row.get("current_prompt_version_id") or 0) or None
        variables["storyboard_quick_config"] = storyboard_quick_config_from_row(task_row)
        variables["rewrite_prompt"] = {
            "simple_prompt": str(task_row.get("rewrite_simple_prompt") or task_row.get("simple_prompt") or ""),
            "final_prompt": str(task_row.get("rewrite_final_prompt") or task_row.get("final_prompt") or ""),
            "source": "openclip_tasks.rewrite_final_prompt",
        }
        variables["storyboard_prompt"] = {
            "simple_prompt": str(task_row.get("storyboard_simple_prompt") or ""),
            "final_prompt": str(task_row.get("storyboard_final_prompt") or ""),
            "source": "openclip_tasks.storyboard_final_prompt",
        }
        variables["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        write_json_atomic(variables_path, variables)
        output_path = workspace / "S1_00_PrepareSessionVariables" / "Output" / "Variables.json"
        if output_path.parent.exists():
            write_json_atomic(output_path, variables)
        return True

    def sync_analysis_v1_run_context(
        *,
        task_id: int,
        session_id: int,
        attempt_id: int,
        workspace: Path,
        model: dict[str, str] | None = None,
        step_id: str = "",
    ) -> bool:
        variables_path = workspace / "SessionContext" / "Variables.json"
        try:
            variables = json.loads(variables_path.read_text(encoding="utf-8")) if variables_path.exists() else {}
        except Exception:
            variables = {}
        if not isinstance(variables, dict):
            variables = {}
        variables.update(
            {
                "task_id": int(task_id),
                "opencrew_session_id": int(session_id),
                "current_attempt_id": int(attempt_id),
                "latest_attempt_id": int(attempt_id),
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        )
        if step_id:
            variables["current_step_id"] = str(step_id)
        if model:
            variables["run_model_provider"] = str(model.get("providerID") or model.get("provider") or variables.get("run_model_provider") or "")
            variables["run_model_id"] = str(model.get("modelID") or model.get("model") or variables.get("run_model_id") or "")
        try:
            task_row = repo.get_task(task_id) or {}
            raw_config_text = str(task_row.get("storyboard_quick_config_json") or "").strip()
            raw_config = json.loads(raw_config_text) if raw_config_text else {}
            raw_config = raw_config if isinstance(raw_config, dict) else {}
        except Exception:
            raw_config = {}
        task_meta_path = workspace / "SessionOutput" / "task_list" / "task_meta.json"
        try:
            task_meta = json.loads(task_meta_path.read_text(encoding="utf-8")) if task_meta_path.exists() else {}
        except Exception:
            task_meta = {}
        task_meta = task_meta if isinstance(task_meta, dict) else {}
        raw_profile = raw_config.get("workflow_profile") if isinstance(raw_config.get("workflow_profile"), dict) else {}
        is_talking_head = (
            str(raw_profile.get("profile_id") or raw_profile.get("workflow_id") or "").strip() == "person_talking_head_v1"
            or str(task_meta.get("profile_id") or task_meta.get("workflow_id") or "").strip() == "person_talking_head_v1"
            or str(task_meta.get("create_mode") or "").strip() == "person_talking_head"
        )
        if is_talking_head:
            db_talking_head = raw_config.get("talking_head") if isinstance(raw_config.get("talking_head"), dict) else {}
            meta_talking_head = task_meta.get("talking_head") if isinstance(task_meta.get("talking_head"), dict) else {}
            talking_head = db_talking_head or meta_talking_head
            if raw_config:
                variables["storyboard_quick_config"] = raw_config
                task_meta["storyboard_quick_config"] = raw_config
            if talking_head:
                variables["talking_head"] = talking_head
                task_meta["talking_head"] = talking_head
            variables["workflow_id"] = "person_talking_head_v1"
            variables["profile_id"] = "person_talking_head_v1"
            variables["create_mode"] = "person_talking_head"
            variables["workflow_profile"] = {
                **(variables.get("workflow_profile") if isinstance(variables.get("workflow_profile"), dict) else {}),
                "profile_id": "person_talking_head_v1",
                "workflow_id": "person_talking_head_v1",
                "create_mode": "person_talking_head",
            }
            task_meta.update({
                "workflow_id": "person_talking_head_v1",
                "profile_id": "person_talking_head_v1",
                "create_mode": "person_talking_head",
                "updated_at": now_ms(),
            })
            write_json_atomic(task_meta_path, task_meta)
        write_json_atomic(variables_path, variables)
        output_path = workspace / "S1_00_PrepareSessionVariables" / "Output" / "Variables.json"
        if output_path.parent.exists():
            write_json_atomic(output_path, variables)
        return True

    def save_analysis_v1_tts_selection_to_variables(task_row: dict[str, Any], payload: OpenClipTTSSelectionPayload) -> dict[str, Any]:
        workspace = analysis_v1_workspace(task_row)
        variables_path = workspace / "SessionContext" / "Variables.json"
        try:
            variables = json.loads(variables_path.read_text(encoding="utf-8")) if variables_path.exists() else {}
        except Exception:
            variables = {}
        if not isinstance(variables, dict):
            variables = {}
        voice_id = str(payload.voice_id or payload.voice or payload.candidate.get("voice_id") or payload.candidate.get("voice") or "").strip()
        provider, model = resolve_tts_public_alias(ctx, str(payload.provider or payload.candidate.get("provider") or ""), str(payload.model or payload.candidate.get("model") or ""))
        selected_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        selected = {
            "candidate_id": str(payload.candidate_id or payload.candidate.get("candidate_id") or "").strip(),
            "provider": provider,
            "model": model,
            "voice": voice_id,
            "voice_id": voice_id,
            "voice_label": str(payload.voice_label or payload.candidate.get("voice_label") or payload.candidate.get("label") or voice_id or "").strip(),
            "voice_source": str(payload.voice_source or payload.candidate.get("voice_source") or "").strip(),
            "sample_audio_path": str(payload.sample_audio_path or payload.candidate.get("sample_audio_path") or payload.candidate.get("preview_audio_path") or "").strip(),
            "prompt": str(payload.prompt or payload.candidate.get("prompt") or payload.candidate.get("generation_prompt") or "").strip(),
            "prompt_template": str(payload.prompt_template or payload.prompt or payload.candidate.get("prompt_template") or payload.candidate.get("tts_builder_prompt") or "").strip(),
            "score": payload.score if payload.score is not None else payload.candidate.get("score"),
            "match_score": payload.match_score if payload.match_score is not None else payload.candidate.get("match_score"),
            "candidate": payload.candidate,
            "source": "analysis_v1_tts_builder_selection",
            "selected_at": selected_at,
        }
        clone_config = load_config(ctx, "voice-clone")
        active_clone_provider = str(clone_config.get("active_provider") or "").strip().lower()
        selected_provider = str(selected.get("provider") or "").strip().lower()
        selected_model = str(selected.get("model") or "").strip().lower()
        selected_is_cloud_clone = (
            str(selected.get("voice_source") or "").strip().lower() == "cloud_clone"
            or str(selected.get("candidate_id") or "").startswith("clone_")
            or "voice-clone" in selected_model
            or str(selected.get("voice_id") or "").strip().lower().startswith("cosyvoice-")
        )
        if selected_is_cloud_clone and active_clone_provider and selected_provider and selected_provider != active_clone_provider:
            raise HTTPException(status_code=400, detail="This cloud voice belongs to an inactive provider. Select the active cloud voice and try again.")
        variables["tts_builder_selection"] = selected
        variables["selected_tts_candidate"] = selected
        variables["selected_tts_candidate_id"] = selected["candidate_id"]
        variables["selected_tts_provider"] = selected["provider"]
        variables["selected_tts_model"] = selected["model"]
        variables["selected_tts_voice"] = selected["voice"]
        variables["selected_tts_voice_id"] = selected["voice_id"]
        variables["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        write_json_atomic(variables_path, variables)
        output_path = workspace / "S1_00_PrepareSessionVariables" / "Output" / "Variables.json"
        if output_path.parent.exists():
            write_json_atomic(output_path, variables)

        candidates_path = workspace / "SessionOutput" / "tts" / "tts_builder_candidates.json"
        candidates_rel = ""
        candidate_payload = payload.candidate if isinstance(payload.candidate, dict) else {}
        if candidates_path.exists() or candidate_payload:
            try:
                candidate_result = json.loads(candidates_path.read_text(encoding="utf-8")) if candidates_path.exists() else {}
            except Exception:
                candidate_result = {}
            if not isinstance(candidate_result, dict):
                candidate_result = {}
            rows = candidate_result.get("candidates")
            if not isinstance(rows, list):
                rows = []

            filtered_rows: list[Any] = []
            seen_cloud_voices: set[str] = set()
            for row in rows:
                if not isinstance(row, dict):
                    filtered_rows.append(row)
                    continue
                row_provider = str(row.get("provider") or row.get("source_clone_provider") or "").strip().lower()
                row_model = str(row.get("model") or row.get("target_model") or "").strip().lower()
                row_voice = str(row.get("voice_id") or row.get("voice") or "").strip().lower()
                is_cloud_clone = (
                    str(row.get("voice_source") or "").strip().lower() == "cloud_clone"
                    or bool(str(row.get("source_clone_provider") or "").strip())
                    or str(row.get("candidate_id") or "").startswith("clone_")
                    or "voice-clone" in row_model
                    or row_voice.startswith("cosyvoice-")
                )
                if is_cloud_clone and active_clone_provider and row_provider and row_provider != active_clone_provider:
                    continue
                if is_cloud_clone and row_voice:
                    if row_voice in seen_cloud_voices:
                        continue
                    seen_cloud_voices.add(row_voice)
                filtered_rows.append(row)
            rows = filtered_rows

            def clean_text(value: Any) -> str:
                return str(value or "").strip()

            selected_id = clean_text(selected.get("candidate_id"))
            selected_voice = clean_text(selected.get("voice_id") or selected.get("voice"))
            selected_provider = clean_text(selected.get("provider"))
            selected_model = clean_text(selected.get("model"))

            def row_matches(row: dict[str, Any]) -> bool:
                row_id = clean_text(row.get("candidate_id"))
                row_voice = clean_text(row.get("voice_id") or row.get("voice") or row.get("voice_label"))
                row_provider = clean_text(row.get("provider"))
                row_model = clean_text(row.get("model") or row.get("target_model"))
                if selected_id and selected_id in {row_id, row_voice}:
                    return True
                if selected_voice and selected_voice == row_voice:
                    return not selected_model or not row_model or selected_model == row_model
                if selected_provider and selected_model and selected_id:
                    return row_provider == selected_provider and row_model == selected_model and row_id == selected_id
                return False

            next_rows: list[Any] = []
            matched = False
            for row in rows:
                if not isinstance(row, dict):
                    next_rows.append(row)
                    continue
                if row_matches(row):
                    merged = {**row, **candidate_payload}
                    merged.update(
                        {
                            "candidate_id": selected_id or clean_text(merged.get("candidate_id")) or selected_voice,
                            "provider": selected_provider or clean_text(merged.get("provider")),
                            "model": selected_model or clean_text(merged.get("model") or merged.get("target_model")),
                            "voice": selected_voice or clean_text(merged.get("voice")),
                            "voice_id": selected_voice or clean_text(merged.get("voice_id") or merged.get("voice")),
                            "voice_label": selected["voice_label"] or clean_text(merged.get("voice_label") or merged.get("label")),
                            "selected": True,
                            "is_selected": True,
                        }
                    )
                    next_rows.append(merged)
                    matched = True
                    continue
                next_rows.append({**row, "selected": False, "is_selected": False})

            if not matched:
                new_row = {**candidate_payload}
                new_row.update(
                    {
                        "candidate_id": selected_id or clean_text(new_row.get("candidate_id")) or selected_voice,
                        "provider": selected_provider or clean_text(new_row.get("provider")),
                        "model": selected_model or clean_text(new_row.get("model") or new_row.get("target_model")),
                        "voice": selected_voice or clean_text(new_row.get("voice")),
                        "voice_id": selected_voice or clean_text(new_row.get("voice_id") or new_row.get("voice")),
                        "voice_label": selected["voice_label"] or clean_text(new_row.get("voice_label") or new_row.get("label")),
                        "voice_source": selected["voice_source"] or clean_text(new_row.get("voice_source")),
                        "sample_audio_path": selected["sample_audio_path"] or clean_text(new_row.get("sample_audio_path") or new_row.get("preview_audio_path")),
                        "prompt": selected["prompt"] or clean_text(new_row.get("prompt") or new_row.get("generation_prompt")),
                        "selected": True,
                        "is_selected": True,
                    }
                )
                next_rows.insert(0, new_row)

            candidate_result["candidates"] = next_rows
            candidate_result["selected_candidate_id"] = selected_id or selected_voice
            candidate_result["selected_tts_candidate"] = selected
            candidate_result["updated_at"] = selected_at
            write_json_atomic(candidates_path, candidate_result)
            candidates_rel = "SessionOutput/tts/tts_builder_candidates.json"

        return {"variables_path": "SessionContext/Variables.json", "candidates_path": candidates_rel, "selection": selected}

    def wav_data_from_pcm(pcm_data: bytes, sample_rate: int = 24000, channels: int = 1, bits_per_sample: int = 16) -> bytes:
        byte_rate = sample_rate * channels * bits_per_sample // 8
        block_align = channels * bits_per_sample // 8
        out = io.BytesIO()
        out.write(b"RIFF")
        out.write(struct.pack("<I", 36 + len(pcm_data)))
        out.write(b"WAVEfmt ")
        out.write(struct.pack("<IHHIIHH", 16, 1, channels, sample_rate, byte_rate, block_align, bits_per_sample))
        out.write(b"data")
        out.write(struct.pack("<I", len(pcm_data)))
        out.write(pcm_data)
        return out.getvalue()

    def analysis_v1_tts_config(provider: str, model: str) -> dict[str, Any]:
        ensure_table(ctx)
        with ctx.engine.begin() as conn:
            row = conn.execute(text(f"""
SELECT provider, model, api_key_ref, api_key_ciphertext, extra_json FROM {CONFIG_TABLE}
WHERE kind = 'tts' AND provider = :provider AND enabled = TRUE
LIMIT 1
"""), {"provider": provider}).first()
        if not row:
            raise HTTPException(status_code=400, detail=f"TTS provider is not configured or enabled: {provider}")
        mapping = row._mapping
        api_key = str(load_stored_key(ctx, "tts", provider) or "").strip()
        if not api_key:
            raise HTTPException(status_code=400, detail=f"TTS provider API key is missing: {provider}")
        try:
            extra = json.loads(str(mapping.get("extra_json") or "{}"))
        except Exception:
            extra = {}
        return {"provider": str(mapping.get("provider") or provider), "model": model.strip() or str(mapping.get("model") or "").strip(), "api_key": api_key, "extra": extra if isinstance(extra, dict) else {}}

    def analysis_v1_voice_clone_config(requested_provider: str = "", requested_model: str = "") -> dict[str, Any]:
        config = load_config(ctx, "voice-clone")
        providers = config.get("providers") if isinstance(config.get("providers"), list) else []
        provider_override = normalize_analysis_v1_clone_provider(requested_provider)
        active_provider = provider_override or str(config.get("active_provider") or "").strip()
        active = next((item for item in providers if isinstance(item, dict) and str(item.get("provider") or "").strip().lower() == active_provider.lower()), None)
        if not active:
            active = next((item for item in providers if isinstance(item, dict) and item.get("active")), None)
        if not active:
            raise HTTPException(status_code=400, detail="Voice Clone provider is not configured. Open Connection > Voice Clone Settings and set an active provider.")
        provider = str(active.get("provider") or "").strip().lower()
        model = str(requested_model or active.get("model") or "").strip()
        if provider not in {"cosyvoice", "heygen", "minimax"}:
            raise HTTPException(status_code=400, detail=f"Unsupported Voice Clone provider: {provider}")
        api_key = str(load_stored_key(ctx, "voice-clone", provider) or "").strip()
        if not api_key:
            raise HTTPException(status_code=400, detail=f"Voice Clone API key is missing for active provider: {provider}")
        env_name = "OPENCREW_ANALYSIS_V1_VOICE_CLONE_API_KEY"
        if provider == "cosyvoice":
            env_name = "OPENCREW_ANALYSIS_V1_COSYVOICE_CLONE_API_KEY"
        elif provider == "heygen":
            env_name = "OPENCREW_ANALYSIS_V1_HEYGEN_CLONE_API_KEY"
        elif provider == "minimax":
            env_name = "OPENCREW_ANALYSIS_V1_MINIMAX_CLONE_API_KEY"
        extra = active.get("extra_json") if isinstance(active.get("extra_json"), dict) else {}
        return {
            "provider": provider,
            "model": model,
            "api_key": api_key,
            "api_key_env": env_name,
            "provider_label": str(active.get("provider_label") or provider),
            "extra": extra if isinstance(extra, dict) else {},
        }

    def analysis_v1_voice_clone_tts_config(provider: str, model: str) -> dict[str, Any]:
        normalized_provider = str(provider or "").strip().lower()
        if normalized_provider not in {"cosyvoice", "heygen", "minimax"}:
            raise HTTPException(status_code=400, detail=f"Unsupported Voice Clone TTS provider: {provider}")
        config = load_config(ctx, "voice-clone")
        providers = config.get("providers") if isinstance(config.get("providers"), list) else []
        item = next((row for row in providers if isinstance(row, dict) and str(row.get("provider") or "").strip().lower() == normalized_provider), None)
        default_model = ""
        if item:
            default_model = str(item.get("model") or "").strip()
        api_key = str(load_stored_key(ctx, "voice-clone", normalized_provider) or "").strip()
        if not api_key and normalized_provider == "cosyvoice":
            api_key = str(load_stored_key(ctx, "tts", "cosyvoice") or "").strip()
        if not api_key:
            raise HTTPException(status_code=400, detail=f"Voice Clone API key is missing for provider: {normalized_provider}")
        extra = item.get("extra_json") if isinstance(item, dict) and isinstance(item.get("extra_json"), dict) else {}
        return {
            "provider": normalized_provider,
            "model": model.strip() or default_model,
            "api_key": api_key,
            "extra": extra if isinstance(extra, dict) else {},
        }

    def normalize_analysis_v1_clone_provider(value: str) -> str:
        return normalize_analysis_v1_clone_provider_value(value)

    def analysis_v1_clone_model_from_provider(provider: str, model: str) -> str:
        return analysis_v1_clone_model_from_provider_value(provider, model)

    def analysis_v1_clone_payload_model(payload: OpenClipTTSPreviewPayload) -> str:
        return analysis_v1_clone_payload_model_value(payload)

    def analysis_v1_cloud_clone_preview_defaults(workspace: Path, payload: OpenClipTTSPreviewPayload) -> dict[str, str]:
        return analysis_v1_cloud_clone_preview_defaults_from_workspace(workspace, payload, read_json_file)

    def heygen_tts_preview_url(api_key: str, voice_id: str, text: str, language: str, tempo: float) -> str:
        body = json.dumps({
            "text": text,
            "voice_id": voice_id,
            "input_type": "text",
            "speed": max(0.5, min(2.0, float(tempo or 1.0))),
            "language": language or None,
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://api.heygen.com/v3/voices/speech",
            data=body,
            headers={"Content-Type": "application/json", "x-api-key": api_key, "Accept": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:2000]
            raise HTTPException(status_code=502, detail=f"HeyGen TTS request failed: HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise HTTPException(status_code=502, detail=f"HeyGen TTS network request failed: {exc.reason}") from exc
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        audio_url = str(data.get("audio_url") or "").strip()
        if not audio_url:
            raise HTTPException(status_code=502, detail="HeyGen TTS response did not include audio_url")
        return audio_url

    def minimax_tts_preview_url(api_key: str, voice_id: str, text: str, language: str, tempo: float, extra: dict[str, Any]) -> str:
        base_url = str((extra or {}).get("base_url") or "https://api.minimaxi.com").rstrip("/")
        group_id = str((extra or {}).get("group_id") or "").strip()
        tts_model = str((extra or {}).get("tts_model") or "speech-02-hd").strip() or "speech-02-hd"
        if not group_id:
            raise HTTPException(status_code=400, detail="MiniMax TTS preview requires a GroupId. Set group_id in the Voice Clone provider extra config.")
        url = f"{base_url}/v1/t2a_v2?{urllib.parse.urlencode({'GroupId': group_id})}"
        body = json.dumps({
            "model": tts_model,
            "text": text,
            "voice_setting": {
                "voice_id": voice_id,
                "speed": max(0.5, min(2.0, float(tempo or 1.0))),
                "vol": 1.0,
                "pitch": 0,
            },
            "audio_setting": {"sample_rate": 32000, "format": "mp3"},
            "output_format": "url",
        }).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:2000]
            raise HTTPException(status_code=502, detail=f"MiniMax TTS request failed: HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise HTTPException(status_code=502, detail=f"MiniMax TTS network request failed: {exc.reason}") from exc
        base_resp = payload.get("base_resp") if isinstance(payload.get("base_resp"), dict) else {}
        status_code = base_resp.get("status_code")
        if status_code not in (None, 0, "0"):
            raise HTTPException(status_code=502, detail=f"MiniMax TTS failed: status_code={status_code}: {base_resp.get('status_msg') or 'unknown error'}")
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        audio_url = str(data.get("audio") or "").strip()
        if not audio_url:
            raise HTTPException(status_code=502, detail="MiniMax TTS response did not include data.audio url")
        return audio_url

    def generate_google_tts_audio(config: dict[str, str], voice_id: str, prompt: str, output_path: Path) -> None:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(config['model'], safe='')}:generateContent?key={urllib.parse.quote(config['api_key'], safe='')}"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def request_google_tts_payload(prompt_text: str, timeout: int) -> dict[str, Any]:
            body = json.dumps({
                "contents": [{"parts": [{"text": prompt_text}]}],
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice_id}}},
                },
            }).encode("utf-8")
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
            try:
                with opener.open(req, timeout=timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:2000]
                raise HTTPException(status_code=502, detail=f"Google TTS request failed: HTTP {exc.code}: {detail}") from exc
            except urllib.error.URLError as exc:
                raise HTTPException(status_code=502, detail=f"Google TTS network request failed: {exc.reason}") from exc

        def write_first_google_audio(payload: dict[str, Any]) -> bool:
            for candidate in payload.get("candidates") or []:
                for part in (((candidate.get("content") or {}).get("parts")) or []):
                    inline_data = part.get("inlineData") or part.get("inline_data") or {}
                    encoded = str(inline_data.get("data") or "") if isinstance(inline_data, dict) else ""
                    mime_type = str(inline_data.get("mimeType") or inline_data.get("mime_type") or "audio/wav") if isinstance(inline_data, dict) else "audio/wav"
                    if not encoded:
                        continue
                    raw = base64.b64decode(encoded)
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    output_path.write_bytes(wav_data_from_pcm(raw) if "pcm" in mime_type or "l16" in mime_type else raw)
                    return True
            return False

        primary_payload = request_google_tts_payload(prompt, 60)
        if write_first_google_audio(primary_payload):
            return
        retry_prompt = analysis_v1_google_tts_retry_prompt(prompt)
        retry_payload = request_google_tts_payload(retry_prompt, 90)
        if write_first_google_audio(retry_payload):
            return
        primary_summary = analysis_v1_google_tts_finish_summary(primary_payload) or "primary_response_without_audio"
        retry_summary = analysis_v1_google_tts_finish_summary(retry_payload) or "retry_response_without_audio"
        raise HTTPException(status_code=502, detail=f"Google TTS response did not include audio data after retry: primary={primary_summary}; retry={retry_summary}")

    def write_analysis_v1_tts_audio_bytes(data: bytes, mime_type: str, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        lowered = str(mime_type or "").lower()
        if "wav" in lowered or "wave" in lowered or "pcm" in lowered or "l16" in lowered:
            output_path.write_bytes(data)
            return
        source_ext = ".mp3" if "mpeg" in lowered or "mp3" in lowered else ".bin"
        source_path = output_path.with_suffix(f".source{source_ext}")
        source_path.write_bytes(data)
        ffmpeg = analysis_v1_ffmpeg_binary()
        cmd = [ffmpeg, "-y", "-i", str(source_path), "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", str(output_path)]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "")[-1200:]
            raise HTTPException(status_code=500, detail=f"Unable to convert TTS preview audio to wav: {detail}") from exc

    def analysis_v1_audio_duration_seconds(path: Path) -> float:
        try:
            with wave.open(str(path), "rb") as handle:
                rate = handle.getframerate()
                return round(handle.getnframes() / rate, 3) if rate else 0.0
        except Exception:
            return 0.0

    def analysis_v1_tts_audio_url_bytes(audio_url: str) -> tuple[bytes, str]:
        value = str(audio_url or "").strip()
        if not value.startswith("http://"):
            return audio_url_bytes(value)
        parsed = urllib.parse.urlparse(value)
        host = (parsed.hostname or "").lower()
        if not (host.startswith("dashscope-result-") and host.endswith(".aliyuncs.com")):
            return audio_url_bytes(value)
        request = urllib.request.Request(value, headers={"User-Agent": "OpenCrew/AnalysisV1-Qwen-TTS"})
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read(25 * 1024 * 1024 + 1)
            mime_type = response.headers.get("Content-Type", "audio/wav")
        if len(data) > 25 * 1024 * 1024:
            raise HTTPException(status_code=502, detail="Qwen TTS preview audio exceeded 25MB limit")
        return data, mime_type

    def analysis_v1_ffmpeg_binary() -> str:
        found = shutil.which("ffmpeg")
        if found:
            return found
        for candidate in (
            os.environ.get("OPENCREW_FFMPEG_PATH", ""),
            str(OPENCREW_REPO_ROOT / "ToolLibrary" / ".bin" / "ffmpeg"),
            str(BACKEND_ROOT / ".venv" / "bin" / "static_ffmpeg"),
        ):
            path = Path(candidate) if candidate else None
            if path and path.exists() and os.access(path, os.X_OK):
                return str(path)
        try:
            import imageio_ffmpeg
            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"ffmpeg is unavailable for TTS tempo processing: {exc}") from exc

    def analysis_v1_atempo_filter_chain(tempo: float) -> str:
        values: list[float] = []
        remaining = max(0.01, float(tempo or 1.0))
        while remaining > 2.0:
            values.append(2.0)
            remaining /= 2.0
        while remaining < 0.5:
            values.append(0.5)
            remaining /= 0.5
        values.append(remaining)
        return ",".join(f"atempo={value:.6f}" for value in values)

    def apply_analysis_v1_tts_tempo(source: Path, target: Path, tempo: float | None) -> dict[str, Any]:
        tempo_value = float(tempo or 1.0)
        if tempo_value <= 0:
            tempo_value = 1.0
        target.parent.mkdir(parents=True, exist_ok=True)
        if abs(tempo_value - 1.0) < 0.0001:
            if source != target:
                shutil.copyfile(source, target)
            return {"tempo": 1.0, "stretched": False}
        filters = [
            "aresample=48000",
            "aformat=channel_layouts=stereo",
            analysis_v1_atempo_filter_chain(tempo_value),
            "loudnorm=I=-17:LRA=11:TP=-1.5",
            "asetpts=N/SR/TB",
        ]
        completed = subprocess.run([analysis_v1_ffmpeg_binary(), "-y", "-i", str(source), "-af", ",".join(filters), "-ar", "48000", "-ac", "2", "-vn", str(target)], capture_output=True, text=True, timeout=180, check=False)
        if completed.returncode != 0:
            raise HTTPException(status_code=500, detail=(completed.stderr or "ffmpeg TTS tempo processing failed")[:2000])
        return {"tempo": round(tempo_value, 4), "stretched": True}

    def run_analysis_v1_builder_g(workspace: Path, payload: OpenClipTTSBuilderPayload) -> dict[str, Any]:
        script_path = ANALYSIS_V1_TTS_BUILDER_G
        if not script_path.exists():
            raise HTTPException(status_code=500, detail=f"Analysis V1 TTS Builder-G script not found: {script_path}")
        duration = max(0.1, float(payload.reference_duration or 16.0))
        python_bin = str(BACKEND_VENV_PYTHON if BACKEND_VENV_PYTHON.exists() else Path(sys.executable or "python3"))
        cmd = [
            python_bin,
            str(script_path),
            "--workspace",
            str(workspace),
            "--reference-start",
            f"{max(0.0, float(payload.reference_start or 0.0)):.3f}",
            "--reference-duration",
            f"{duration:.3f}",
            "--target-duration",
            f"{duration:.3f}",
            "--quick-duration",
            f"{min(8.0, duration):.3f}",
            "--print-json",
        ]
        if payload.force:
            cmd.append("--force")
        result = subprocess.run(cmd, cwd=str(Path(__file__).resolve().parents[3]), check=False, capture_output=True, text=True, timeout=1800)
        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        parsed: dict[str, Any] | None = None
        if stdout:
            try:
                parsed = json.loads(stdout[stdout.find("{"):])
            except Exception:
                parsed = None
        if result.returncode != 0:
            raise HTTPException(status_code=500, detail={"tool_path": str(script_path), "message": stderr or stdout or "Analysis V1 Builder-G failed"})
        result_payload = parsed or {"status": "completed", "raw_stdout": stdout}
        result_payload["tool_path"] = str(script_path)
        return result_payload

    def analysis_v1_tts_public_alias_requested(provider: str, model: str) -> bool:
        return str(provider or "").strip().startswith(PUBLIC_TTS_PROVIDER_PREFIX) or str(model or "").strip().startswith(PUBLIC_TTS_PROVIDER_PREFIX)

    def resolve_analysis_v1_tts_model_option(provider: str, model: str) -> tuple[str, str]:
        provider_value = str(provider or "").strip()
        model_value = str(model or "").strip()
        if not provider_value and model_value.startswith(PUBLIC_TTS_PROVIDER_PREFIX) and PUBLIC_TTS_MODEL_SEGMENT in model_value:
            provider_value = model_value.split(PUBLIC_TTS_MODEL_SEGMENT, 1)[0]
        if analysis_v1_tts_public_alias_requested(provider_value, model_value):
            return resolve_tts_public_alias(ctx, provider_value, model_value)
        return provider_value, model_value

    def run_analysis_v1_quick_adv_command(
        workspace: Path,
        command: str,
        payload: OpenClipTTSQuickAdvPayload,
        *,
        clone_provider_override: str = "",
        clone_model_override: str = "",
    ) -> dict[str, Any]:
        script_path = ANALYSIS_V1_TTS_BUILDER_QUICK_ADV
        if not script_path.exists():
            raise HTTPException(status_code=500, detail=f"Analysis V1 TTS Builder QuickAdv script not found: {script_path}")
        allowed = {"state", "sample-reference", "catalog-list", "rank", "clone-voice", "clone-list", "clone-query", "clone-import", "clone-delete"}
        if command not in allowed:
            raise HTTPException(status_code=400, detail=f"Unsupported TTS QuickAdv command: {command}")
        if command == "clone-voice" and not str(payload.clone_audio_path or "").strip():
            sampled = run_analysis_v1_quick_adv_command(workspace, "sample-reference", payload)
            reference_profile = sampled.get("reference_profile") if isinstance(sampled.get("reference_profile"), dict) else {}
            selected_audio = str(reference_profile.get("audio_path") or "").strip()
            selected_path = (workspace / selected_audio).resolve() if selected_audio else None
            try:
                if selected_path is not None:
                    selected_path.relative_to(workspace.resolve())
            except ValueError:
                selected_path = None
            if sampled.get("ok") is False or not selected_path or not selected_path.is_file() or selected_path.stat().st_size <= 0:
                raise HTTPException(status_code=400, detail={
                    "code": "clone_reference_sample_failed",
                    "message": "A short reference sample could not be prepared for voice cloning.",
                    "sample_result": sampled,
                })
            payload = payload.model_copy(update={"clone_audio_path": selected_audio})
        duration = max(0.0, float(payload.reference_duration or 0.0))
        clone_env_name = "DASHSCOPE_API_KEY"
        clone_api_key = ""
        clone_config: dict[str, Any] = {}
        if command.startswith("clone-"):
            clone_config = analysis_v1_voice_clone_config(clone_provider_override, clone_model_override)
            clone_env_name = str(clone_config.get("api_key_env") or "OPENCREW_ANALYSIS_V1_VOICE_CLONE_API_KEY")
            clone_api_key = str(clone_config.get("api_key") or "")
        selected_providers, selected_model = resolve_analysis_v1_tts_model_option(
            str(payload.providers or "google"),
            str(payload.model or "gemini-3.1-flash-tts-preview"),
        )
        cmd = [
            analysis_v1_python_bin(),
            str(script_path),
            command,
            "--workspace",
            str(workspace),
            "--model",
            selected_model,
            "--providers",
            selected_providers,
            "--reference-start",
            f"{max(0.0, float(payload.reference_start or 0.0)):.3f}",
            "--reference-duration",
            f"{duration:.3f}",
            "--stage1-count",
            str(max(1, int(payload.stage1_count or 24))),
            "--stage2-count",
            str(max(1, int(payload.stage2_count or 6))),
            "--final-count",
            str(max(1, int(payload.final_count or 3))),
            "--print-json",
        ]
        catalog_dir = str(payload.voice_catalog_dir or "").strip()
        if catalog_dir:
            cmd.extend(["--voice-catalog-dir", catalog_dir])
        voices = str(payload.voices or "").strip()
        if voices:
            cmd.extend(["--voices", voices])
        if not payload.enable_speechbrain:
            cmd.append("--disable-speechbrain")
        if payload.force:
            cmd.append("--force")
        if payload.resume:
            cmd.append("--resume")
        if command.startswith("clone-"):
            cmd.extend([
                "--clone-provider",
                str(clone_config.get("provider") or payload.clone_provider or "cosyvoice"),
                "--clone-target-model",
                str(clone_config.get("model") or payload.clone_target_model or "cosyvoice-v3.5-flash"),
                "--clone-prefix",
                str(payload.clone_prefix or "ocadv"),
                "--clone-language-hints",
                str(payload.clone_language_hints or "zh"),
                "--clone-api-key-env",
                clone_env_name,
                "--clone-page-index",
                str(max(0, int(payload.clone_page_index or 0))),
                "--clone-page-size",
                str(max(1, min(int(payload.clone_page_size or 100), 100))),
                "--clone-consent-actor",
                str(payload.clone_consent_actor or "ui"),
            ])
            if payload.clone_audio_path:
                cmd.extend(["--clone-audio-path", str(payload.clone_audio_path)])
            if payload.clone_audio_url:
                cmd.extend(["--clone-audio-url", str(payload.clone_audio_url)])
            if payload.clone_voice_id:
                cmd.extend(["--clone-voice-id", str(payload.clone_voice_id)])
            if payload.clone_consent_note:
                cmd.extend(["--clone-consent-note", str(payload.clone_consent_note)])
            if payload.clone_consent_confirmed:
                cmd.append("--clone-consent-confirmed")
        env = analysis_v1_run_env(step_id="03_03")
        if payload.enable_speechbrain:
            env["ANALYSIS_V1_ENABLE_SPEECHBRAIN"] = "1"
        if command.startswith("clone-") and clone_env_name and clone_api_key:
            env[clone_env_name] = clone_api_key
        if command.startswith("clone-") and str(clone_config.get("provider") or "").strip().lower() == "minimax":
            clone_extra = clone_config.get("extra") if isinstance(clone_config.get("extra"), dict) else {}
            minimax_group_id = str(clone_extra.get("group_id") or "").strip()
            if minimax_group_id:
                env["OPENCREW_ANALYSIS_V1_MINIMAX_GROUP_ID"] = minimax_group_id
            minimax_base_url = str(clone_extra.get("base_url") or "").strip()
            if minimax_base_url:
                env["OPENCREW_ANALYSIS_V1_MINIMAX_BASE_URL"] = minimax_base_url
            minimax_tts_model = str(clone_extra.get("tts_model") or "").strip()
            if minimax_tts_model:
                env["OPENCREW_ANALYSIS_V1_MINIMAX_TTS_MODEL"] = minimax_tts_model
        timeout = 300 if command in {"rank", "clone-voice", "clone-list", "clone-query", "clone-import", "clone-delete"} else 120
        result = subprocess.run(cmd, cwd=str(OPENCREW_REPO_ROOT), check=False, capture_output=True, text=True, timeout=timeout, env=env)
        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        parsed: dict[str, Any] | None = None
        if stdout:
            try:
                parsed = json.loads(stdout[stdout.find("{"):])
            except Exception:
                parsed = None
        if parsed is None:
            raise HTTPException(status_code=500, detail={
                "tool_path": str(script_path),
                "command": command,
                "returncode": result.returncode,
                "message": stderr or stdout or "Analysis V1 TTS QuickAdv did not return JSON",
            })
        parsed.setdefault("ok", result.returncode == 0 and str(parsed.get("status") or "completed") != "failed")
        parsed["tool_path"] = str(script_path)
        parsed["command"] = command
        parsed["returncode"] = result.returncode
        if stderr:
            parsed["stderr_tail"] = stderr[-2000:]
        return parsed

    def system_voice_clone_workspace() -> Path:
        workspace = ctx.workspace_store.sessions_root() / "_system" / "voice_clone_list"
        workspace.mkdir(parents=True, exist_ok=True)
        return workspace

    def analysis_v1_clone_api_key(payload: OpenClipTTSQuickAdvPayload) -> tuple[str, str]:
        def safe_env_name(value: str, fallback: str = "DASHSCOPE_API_KEY") -> str:
            name = str(value or "").strip()
            return name if re.fullmatch(r"[A-Z_][A-Z0-9_]*", name) else fallback

        explicit_env = safe_env_name(payload.clone_api_key_env or "DASHSCOPE_API_KEY")
        for env_name in (explicit_env, "DASHSCOPE_API_KEY", "QWEN_API_KEY", "OPENCREW_TTS_API_KEY"):
            if env_name and os.environ.get(env_name):
                return env_name, str(os.environ[env_name]).strip()

        provider_candidates: list[str] = []
        for provider in str(payload.providers or "").split(","):
            normalized = provider.strip().lower()
            if normalized in {"qwen", "dashscope", "aliyun", "aliyun_dashscope", "cosyvoice"}:
                provider_candidates.append("qwen" if normalized in {"dashscope", "aliyun", "aliyun_dashscope"} else normalized)
        provider_candidates.extend(["qwen", "cosyvoice", "dashscope"])
        for provider in dict.fromkeys(provider_candidates):
            try:
                config = analysis_v1_tts_config(provider, "")
            except HTTPException:
                continue
            api_key = str(config.get("api_key") or "").strip()
            if api_key:
                return "OPENCREW_TTS_QUICK_ADV_CLONE_API_KEY", api_key
        return explicit_env, ""

    def analysis_v1_python_bin() -> str:
        override = str(os.environ.get("OPENCREW_ANALYSIS_V1_PYTHON") or "").strip()
        if override:
            return override
        data_dir = Path(os.environ.get("OPENCREW_DATA_DIR") or getattr(ctx, "data_dir", "") or (Path.home() / ".opencrew")).expanduser()
        managed = data_dir / "runtimes" / "analysis_v1_py312" / "bin" / "python"
        if managed.exists():
            return str(managed)
        return str(BACKEND_VENV_PYTHON if BACKEND_VENV_PYTHON.exists() else Path(sys.executable or "python3"))

    def analysis_v1_run_env(
        *,
        task_id: int | None = None,
        session_id: int | None = None,
        attempt_id: int | None = None,
        step_id: str = "",
    ) -> dict[str, str]:
        env = dict(os.environ)
        secret_name = re.compile(r"(api|key|token|secret|auth|password|cookie|credential|openai|google|qwen|xai|anthropic|gemini|dashscope)", re.IGNORECASE)
        keep_secret_like = {"PATH", "HOME", "USER", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE", "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "VIRTUAL_ENV"}
        for name in list(env.keys()):
            if name not in keep_secret_like and secret_name.search(name):
                env.pop(name, None)
        db_url = str(ctx.config.database_url)
        env["OPENCREW_DATABASE_URL"] = db_url
        env["DATABASE_URL"] = db_url
        env["OPENCREW_DATA_DIR"] = str(ctx.data_dir)
        if task_id is not None:
            env["OPENCREW_TASK_ID"] = str(task_id)
        if session_id is not None:
            env["OPENCREW_SESSION_ID"] = str(session_id)
        if attempt_id is not None:
            env["OPENCREW_ATTEMPT_ID"] = str(attempt_id)
        if step_id:
            env["OPENCREW_STEP_ID"] = str(step_id)
        opencode_base_url = str(ctx.get_setting("opencode.base_url") or "").strip()
        opencode_username = str(ctx.get_setting("opencode.username") or "").strip()
        opencode_password = str(ctx.get_setting("opencode.password") or "").strip()
        if opencode_base_url:
            env["OPENCREW_OPENCODE_BASE_URL"] = opencode_base_url
        if opencode_username:
            env["OPENCREW_OPENCODE_USERNAME"] = opencode_username
        if opencode_password:
            env["OPENCREW_OPENCODE_PASSWORD"] = opencode_password
        env.setdefault("OPENCREW_ANALYSIS_V1_PYTHON", analysis_v1_python_bin())
        env.setdefault("OPENCREW_FFMPEG_PATH", str(OPENCREW_REPO_ROOT / "ToolLibrary" / ".bin" / "ffmpeg"))
        env.setdefault("OPENCREW_FFPROBE_PATH", str(OPENCREW_REPO_ROOT / "ToolLibrary" / ".bin" / "ffprobe"))
        env["PYTHONUNBUFFERED"] = "1"
        python_paths = [
            str(OPENCREW_REPO_ROOT / "backend"),
            str(OPENCREW_REPO_ROOT),
            str(OPENCREW_REPO_ROOT.parent),
        ]
        existing_python_path = env.get("PYTHONPATH")
        if existing_python_path:
            python_paths.append(existing_python_path)
        env["PYTHONPATH"] = os.pathsep.join(python_paths)
        return env

    def normalize_analysis_v1_tts_builder_mode(payload: OpenClipAnalysisV1RunPayload) -> str:
        if not payload.include_tts_builder:
            return "skip"
        mode = str(payload.tts_builder_mode or "quick").strip().lower()
        aliases = {
            "03_01": "builder_g",
            "03-01": "builder_g",
            "builder-g": "builder_g",
            "builder_g": "builder_g",
            "g": "builder_g",
            "03_02": "quick",
            "03-02": "quick",
            "quick": "quick",
            "tts_builder_quick": "quick",
            "03_03": "quick_adv",
            "03-03": "quick_adv",
            "quick_adv": "quick_adv",
            "quick-adv": "quick_adv",
            "adv": "quick_adv",
            "tts_builder_quick_adv": "quick_adv",
            "skip": "skip",
            "none": "skip",
            "false": "skip",
        }
        normalized = aliases.get(mode)
        if not normalized:
            raise HTTPException(status_code=400, detail="tts_builder_mode must be quick, quick_adv, builder_g, or skip")
        return normalized

    def normalize_analysis_v1_rewrite_mode(payload: OpenClipAnalysisV1RunPayload) -> str:
        mode = str(payload.rewrite_mode or "strict").strip().lower()
        aliases = {
            "strict": "strict",
            "locked": "strict",
            "preserve": "strict",
            "04_01": "strict",
            "04-01": "strict",
            "free": "free",
            "rewrite_free": "free",
            "srt_rewrite_free": "free",
            "04_01_free": "free",
            "04-01-free": "free",
        }
        normalized = aliases.get(mode)
        if not normalized:
            raise HTTPException(status_code=400, detail="rewrite_mode must be strict or free")
        return normalized

    def normalize_analysis_v1_storyboard_mode(payload: OpenClipAnalysisV1RunPayload) -> str:
        mode = str(payload.storyboard_mode or "quick").strip().lower()
        aliases = {
            "model": "model",
            "llm": "model",
            "04_02": "model",
            "04-02": "model",
            "quick": "quick",
            "deterministic": "quick",
            "04_03": "quick",
            "04-03": "quick",
        }
        normalized = aliases.get(mode)
        if not normalized:
            raise HTTPException(status_code=400, detail="storyboard_mode must be model or quick")
        return normalized

    def analysis_v1_tts_builder_spec(tts_builder_mode: str) -> dict[str, Any] | None:
        if tts_builder_mode == "skip":
            return None
        if tts_builder_mode == "builder_g":
            return {"id": "03_01", "name": "03_01_TTSBuilderG", "script": ANALYSIS_V1_TTS_BUILDER_G, "timeout": 10800}
        if tts_builder_mode == "quick_adv":
            return {"id": "03_03", "name": "03_03_TTSBuilderQuickAdv", "script": ANALYSIS_V1_TTS_BUILDER_QUICK_ADV, "timeout": 10800}
        return {"id": "03_02", "name": "03_02_TTSBuilderQuick", "script": ANALYSIS_V1_TTS_BUILDER_QUICK, "timeout": 10800}

    def analysis_v1_payload_workflow_profile(payload: OpenClipAnalysisV1RunPayload | OpenClipAnalysisV1OneClickMoviePayload) -> str:
        options = payload.options if isinstance(payload.options, dict) else {}
        return str(options.get("workflow_profile") or options.get("profile_id") or "").strip()

    def analysis_v1_task_workflow_profile(task_row: dict[str, Any], workspace: Path | None = None) -> str:
        raw_config: dict[str, Any] = {}
        try:
            raw_text = str(task_row.get("storyboard_quick_config_json") or "").strip()
            raw = json.loads(raw_text) if raw_text else {}
            raw_config = raw if isinstance(raw, dict) else {}
        except Exception:
            raw_config = {}
        raw_profile = raw_config.get("workflow_profile") if isinstance(raw_config.get("workflow_profile"), dict) else {}
        profile = str(raw_profile.get("profile_id") or raw_profile.get("workflow_id") or raw_config.get("profile_id") or raw_config.get("workflow_id") or "").strip()
        if profile:
            return profile
        if isinstance(raw_config.get("talking_head"), dict):
            return "person_talking_head_v1"
        task_meta: dict[str, Any] = {}
        if workspace is not None:
            try:
                meta_raw = json.loads((workspace / "SessionOutput" / "task_list" / "task_meta.json").read_text(encoding="utf-8"))
                task_meta = meta_raw if isinstance(meta_raw, dict) else {}
            except Exception:
                task_meta = {}
        profile = str(task_meta.get("profile_id") or task_meta.get("workflow_id") or "").strip()
        if profile:
            return profile
        if str(task_meta.get("create_mode") or "").strip() == "person_talking_head" or isinstance(task_meta.get("talking_head"), dict):
            return "person_talking_head_v1"
        return ""

    def analysis_v1_effective_workflow_profile(
        payload: OpenClipAnalysisV1RunPayload | OpenClipAnalysisV1OneClickMoviePayload,
        task_row: dict[str, Any] | None = None,
        workspace: Path | None = None,
    ) -> str:
        return analysis_v1_payload_workflow_profile(payload) or (analysis_v1_task_workflow_profile(task_row, workspace) if task_row else "")

    def session_variables_prepare_workflow(task_row: dict[str, Any], workspace: Path) -> str:
        profile = analysis_v1_task_workflow_profile(task_row, workspace)
        if profile == "person_talking_head_v1":
            return "person_talking_head_v1"
        mode = infer_openclip_workflow_mode(task_row, workspace=workspace)
        if mode == WORKFLOW_DANCE_MIMIC_V1:
            return WORKFLOW_DANCE_MIMIC_V1
        return "analysis_v1"

    def session_variables_prepare_command(
        *,
        workflow: str,
        task_row: dict[str, Any],
        workspace: Path,
        force: bool = True,
    ) -> list[str]:
        task_id = int(task_row["id"])
        session_id = int(task_row["session_id"])
        python_bin = analysis_v1_python_bin()
        if workflow == "person_talking_head_v1":
            command = [
                python_bin,
                str(TALKING_HEAD_V1_ROOT / "00_PrepareSessionVariables.py"),
                "--workspace",
                str(workspace),
                "--print-json",
            ]
            if force:
                command.append("--force")
            return command
        if workflow == WORKFLOW_DANCE_MIMIC_V1:
            task_meta = read_json_file(workspace / "SessionOutput" / "task_list" / "task_meta.json")
            dance_meta = task_meta.get("dance_mimic") if isinstance(task_meta.get("dance_mimic"), dict) else {}
            source_video = str(task_row.get("reference_video_path") or task_meta.get("reference_video_path") or dance_meta.get("reference_video_path") or "").strip()
            target_identity = str(task_meta.get("target_identity_image_path") or dance_meta.get("target_identity_image_path") or "").strip()
            reference_privacy_mode = str(task_meta.get("reference_privacy_mode") or dance_meta.get("reference_privacy_mode") or "").strip()
            command = [
                python_bin,
                str(DANCE_MIMIC_V1_ROOT / "00_PrepareSessionVariables.py"),
                "--workspace",
                str(workspace),
                "--task-id",
                str(task_id),
                "--session-id",
                str(session_id),
                "--workflow-id",
                WORKFLOW_DANCE_MIMIC_V1,
                "--print-json",
            ]
            if force:
                command.append("--force")
            if source_video:
                command.extend(["--source-video-path", source_video])
            if target_identity:
                command.extend(["--target-identity-image-path", target_identity])
            if reference_privacy_mode:
                command.extend(["--reference-privacy-mode", reference_privacy_mode])
            return command
        command = [
            python_bin,
            str(ANALYSIS_V1_ROOT / "00_PrepareSessionVariables.py"),
            "--task-id",
            str(task_id),
            "--session-id",
            str(session_id),
            "--attempt-mode",
            "latest",
            "--clip-mode",
            "virtual",
            "--selected-scheme",
            "detail",
            "--print-json",
        ]
        if force:
            command.append("--force")
        return command

    def run_session_variables_prepare_00(task_row: dict[str, Any], *, force: bool = True) -> dict[str, Any]:
        workspace = analysis_v1_workspace(task_row)
        workflow = session_variables_prepare_workflow(task_row, workspace)
        command = session_variables_prepare_command(workflow=workflow, task_row=task_row, workspace=workspace, force=force)
        session_id = int(task_row["session_id"])
        task_id = int(task_row["id"])
        started_at = now_ms()
        add_session_event(
            session_id,
            "session_variables.prepare.started",
            {"task_id": task_id, "workflow": workflow, "tool": Path(command[1]).name},
            task_id=task_id,
            step_id="00",
        )
        completed = subprocess.run(
            command,
            cwd=str(OPENCREW_REPO_ROOT),
            env=analysis_v1_run_env(task_id=task_id, session_id=session_id, step_id="00"),
            capture_output=True,
            text=True,
            check=False,
            timeout=900,
        )
        stdout = str(completed.stdout or "")
        stderr = str(completed.stderr or "")
        parsed: dict[str, Any] = {}
        if stdout.strip():
            try:
                parsed = extract_json_object(stdout)
            except Exception:
                parsed = {}
        status = str(parsed.get("status") or "").strip().lower()
        if completed.returncode != 0 or status in {"failed", "blocked"}:
            message = str((parsed.get("error") if isinstance(parsed.get("error"), dict) else {}).get("message") if isinstance(parsed.get("error"), dict) else "")
            message = message or str(parsed.get("message") or "").strip() or stderr.strip() or stdout.strip() or f"{Path(command[1]).name} failed"
            add_session_event(
                session_id,
                "session_variables.prepare.failed",
                {"task_id": task_id, "workflow": workflow, "returncode": completed.returncode, "message": message[:1000]},
                task_id=task_id,
                step_id="00",
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "session_variables_prepare_failed",
                    "workflow": workflow,
                    "tool": Path(command[1]).name,
                    "message": message[:4000],
                    "stdout_tail": stdout[-2000:],
                    "stderr_tail": stderr[-2000:],
                },
            )
        variables_path = workspace / "SessionContext" / "Variables.json"
        variables = read_json_file(variables_path)
        output_path = workspace / "S1_00_PrepareSessionVariables" / "Output" / "Variables.json"
        if output_path.parent.exists() and variables:
            write_json_atomic(output_path, variables)
        finished_at = now_ms()
        add_session_event(
            session_id,
            "session_variables.prepare.completed",
            {
                "task_id": task_id,
                "workflow": workflow,
                "tool": Path(command[1]).name,
                "duration_seconds": round((finished_at - started_at) / 1000, 3),
            },
            task_id=task_id,
            step_id="00",
        )
        return {
            "ok": True,
            "task_id": task_id,
            "session_id": session_id,
            "workflow": workflow,
            "tool": Path(command[1]).name,
            "variables_path": "SessionContext/Variables.json",
            "variables": variables,
            "result": parsed,
            "updated_at": time.strftime("%Y/%m/%d %H:%M:%S", time.localtime()),
        }

    def talking_head_session_video_config_ready(workspace: Path, selected_video_model: dict[str, Any]) -> bool:
        variables = read_json_file(workspace / "SessionContext" / "Variables.json")
        default_video = variables.get("default_video_config") if isinstance(variables.get("default_video_config"), dict) else {}
        default_provider = str(default_video.get("provider") or "").strip()
        default_model = str(default_video.get("model") or "").strip()
        selected_provider = str(selected_video_model.get("provider") or "").strip()
        selected_model = str(selected_video_model.get("model") or "").strip()
        model_matches = (
            selected_model == default_model
            or {selected_model, default_model}.issubset({"wan2.7-r2v", "wan2.7-r2v-2026-06-12"})
        )
        return bool(default_provider and default_model and selected_provider == default_provider and model_matches)

    def analysis_v1_payload_with_workflow_profile(
        payload: OpenClipAnalysisV1RunPayload | OpenClipAnalysisV1OneClickMoviePayload,
        workflow_profile: str,
    ) -> OpenClipAnalysisV1RunPayload | OpenClipAnalysisV1OneClickMoviePayload:
        if not workflow_profile or analysis_v1_payload_workflow_profile(payload):
            return payload
        options = dict(payload.options or {})
        options.update({"workflow_profile": workflow_profile, "profile_id": workflow_profile})
        return payload.model_copy(update={"options": options})

    def analysis_v1_run_step_specs(tts_builder_mode: str = "quick", storyboard_mode: str = "quick", rewrite_mode: str = "strict", workflow_profile: str = "") -> list[dict[str, Any]]:
        tts_spec = analysis_v1_tts_builder_spec(tts_builder_mode)
        prepare_spec = (
            {"id": "00", "name": "00_PrepareSessionVariables", "script": TALKING_HEAD_V1_ROOT / "00_PrepareSessionVariables.py", "timeout": 600, "command_mode": "workspace", "workflow_profile": "person_talking_head_v1"}
            if workflow_profile == "person_talking_head_v1"
            else {"id": "00", "name": "00_PrepareSessionVariables", "script": ANALYSIS_V1_ROOT / "00_PrepareSessionVariables.py", "timeout": 600}
        )
        talking_head_storyboard_generate_spec = {"id": "01", "name": "01_StoryBoardGenerate", "display_name_zh": "故事版生成", "display_name_en": "Generate TalkingHead StoryBoard", "script": TALKING_HEAD_V1_ROOT / "01_StoryBoardGenerate.py", "timeout": 900, "command_mode": "workspace", "workflow_profile": "person_talking_head_v1"}
        talking_head_storyboard_structure_spec = {"id": "02", "name": "02_StoryBoardStructure", "display_name_zh": "故事版分镜生成", "display_name_en": "Build TalkingHead StoryBoard structure", "script": TALKING_HEAD_V1_ROOT / "02_StoryBoardStructure.py", "timeout": 900, "command_mode": "workspace", "workflow_profile": "person_talking_head_v1"}
        talking_head_storyboard_config_spec = {"id": "03", "name": "03_StoryBoardConfig", "display_name_zh": "故事版配置", "display_name_en": "Configure TalkingHead StoryBoard", "script": TALKING_HEAD_V1_ROOT / "03_StoryBoardConfig.py", "timeout": 7200, "command_mode": "workspace", "workflow_profile": "person_talking_head_v1"}
        if workflow_profile == "person_talking_head_v1":
            rewrite_spec = {
                "id": "04_01",
                "name": "04_01_TalkingHeadSRTRewrite",
                "display_name_zh": "口播脚本改写",
                "display_name_en": "TalkingHead SRT Rewrite",
                "script": TALKING_HEAD_V1_ROOT / "04_01_SRTRewrite.py",
                "timeout": 7200,
                "artifact_billable": True,
                "billable_outputs": ["SessionOutput/subtitle/rewritten_srt_items.json"],
                "requires_database": True,
                "rewrite_mode": "talking_head",
                "workflow_profile": "person_talking_head_v1",
            }
        else:
            rewrite_spec = (
            {
                "id": "04_01",
                "name": "04_01_SRTRewriteFree",
                "display_name_zh": "04_01 SRT 自由改写",
                "display_name_en": "04_01 SRT Rewrite Free",
                "script": ANALYSIS_V1_SRT_REWRITE_FREE,
                "timeout": 7200,
                "artifact_billable": True,
                "billable_outputs": ["SessionOutput/subtitle/rewritten_srt_items.json"],
                "requires_database": False,
                "rewrite_mode": "free",
            }
            if rewrite_mode == "free"
            else {
                "id": "04_01",
                "name": "04_01_SRTRewrite",
                "script": ANALYSIS_V1_ROOT / "04_01_SRTRewrite.py",
                "timeout": 7200,
                "artifact_billable": True,
                "billable_outputs": ["SessionOutput/subtitle/rewritten_srt_items.json"],
                "requires_database": True,
                "rewrite_mode": "strict",
            }
            )
        storyboard_spec = (
            {
                "id": "04_03",
                "name": "04_03_StoryBoardQuick",
                "script": ANALYSIS_V1_ROOT / "04_03_StoryBoardQuick.py",
                "timeout": 900,
                "artifact_billable": True,
                "billable_outputs": ["SessionOutput/storyboard/srt_storyboard.json"],
            }
            if storyboard_mode == "quick"
            else {
                "id": "04_02",
                "name": "04_02_StoryBoard",
                "script": ANALYSIS_V1_ROOT / "04_02_StoryBoard.py",
                "timeout": 7200,
                "artifact_billable": True,
                "billable_outputs": ["SessionOutput/storyboard/srt_storyboard.json"],
            }
        )
        if workflow_profile == "person_talking_head_v1":
            return [prepare_spec, rewrite_spec, talking_head_storyboard_generate_spec, talking_head_storyboard_structure_spec, talking_head_storyboard_config_spec]
        specs = [
            prepare_spec,
            {
                "id": "01",
                "name": "01_VideoProbeMetadata",
                "script": ANALYSIS_V1_ROOT / "01_VideoProbeMetadata.py",
                "timeout": 900,
                "artifact_billable": True,
                "billable_outputs": ["SessionContext/Video_Metadata.json"],
            },
            {
                "id": "02_01",
                "name": "02_01_AudioASR",
                "script": ANALYSIS_V1_ROOT / "02_01_AudioASR.py",
                "timeout": 7200,
                "artifact_billable": True,
                "billable_outputs": ["SessionOutput/Audio_Reference.wav"],
                "artifact_provider_allowlist": ["local_whisper"],
            },
            {
                "id": "02_02",
                "name": "02_02_VideoSRTFrame",
                "script": ANALYSIS_V1_ROOT / "02_02_VideoSRTFrame.py",
                "timeout": 7200,
                "artifact_billable": True,
                "billable_outputs": [
                    "SessionOutput/visual/srt_frame_map.json",
                    "SessionOutput/visual/srt_frames/*.jpg",
                    "SessionOutput/subtitle/final_srt_frame_items.json",
                ],
            },
            rewrite_spec,
            storyboard_spec,
        ]
        if tts_spec:
            specs.insert(4, tts_spec)
        return specs

    def analysis_v1_tool_display_index() -> dict[str, dict[str, Any]]:
        path = ANALYSIS_V1_ROOT / "tool_registry.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        tools = payload.get("tools") if isinstance(payload, dict) else []
        index: dict[str, dict[str, Any]] = {}
        for tool in (tools if isinstance(tools, list) else []):
            if not isinstance(tool, dict):
                continue
            tool_id = str(tool.get("id") or "").strip()
            if tool_id:
                index[tool_id] = tool
        return index

    def redact_analysis_v1_text(value: str, limit: int = 4000) -> str:
        text_value = str(value or "")
        if len(text_value) > limit:
            text_value = text_value[-limit:]
        replacements = [
            (r"(?i)(authorization\s*[:=]\s*)(bearer\s+)?[^\s,\"']+", r"\1[redacted]"),
            (r"(?i)(api[_-]?key\s*[:=]\s*)[^\s,\"']+", r"\1[redacted]"),
            (r"(?i)(password\s*[:=]\s*)[^\s,\"']+", r"\1[redacted]"),
            (r"postgresql(?:\+psycopg2?|\+psycopg)?://[^\s,\"']+", "postgresql://[redacted]"),
            (r"sk-[A-Za-z0-9_\-]{8,}", "sk-[redacted]"),
            (r"AIza[0-9A-Za-z_\-]{8,}", "AIza[redacted]"),
        ]
        for pattern, repl in replacements:
            text_value = re.sub(pattern, repl, text_value)
        return text_value

    def analysis_v1_tail_text(value: str, limit: int | None = None) -> str:
        text_value = str(value or "")
        max_len = limit or ANALYSIS_V1_LOG_TAIL_LIMIT
        if len(text_value) <= max_len:
            return text_value
        return text_value[-max_len:]

    def redact_analysis_v1_tail(value: str, limit: int | None = None) -> str:
        return redact_analysis_v1_text(analysis_v1_tail_text(value, limit=limit), limit=limit or ANALYSIS_V1_LOG_TAIL_LIMIT)

    def parse_analysis_v1_stdout(stdout: str) -> dict[str, Any]:
        text_value = str(stdout or "").strip()
        if not text_value:
            return {}
        try:
            return json.loads(text_value)
        except Exception:
            pass
        start = text_value.find("{")
        end = text_value.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(text_value[start:end + 1])
                return parsed if isinstance(parsed, dict) else {"value": parsed}
            except Exception:
                return {}
        return {}

    def analysis_v1_result_message(parsed: dict[str, Any]) -> str:
        error = parsed.get("error")
        if isinstance(error, dict):
            error_message = str(error.get("message") or error.get("detail") or error.get("code") or "").strip()
        else:
            error_message = str(error or "").strip()
        message = str(parsed.get("message") or error_message or parsed.get("code") or "").strip()
        if message:
            return message
        segments_payload = parsed.get("segments") or []
        if isinstance(segments_payload, list):
            for item in segments_payload:
                if not isinstance(item, dict):
                    continue
                segment_error = item.get("error")
                if isinstance(segment_error, dict):
                    segment_message = str(segment_error.get("message") or segment_error.get("detail") or segment_error.get("code") or "").strip()
                else:
                    segment_message = str(segment_error or "").strip()
                if segment_message:
                    return segment_message
        warnings_payload = parsed.get("warnings") or []
        if isinstance(warnings_payload, list):
            parts = []
            for item in warnings_payload:
                if not isinstance(item, dict):
                    continue
                code = str(item.get("code") or item.get("kind") or "").strip()
                if code in {"session_tts_outputs_restored", "restored_previous_tts_outputs_after_failed_force_rerun"}:
                    continue
                text_message = str(item.get("message") or item.get("suggested_action") or "").strip()
                if code and text_message:
                    parts.append(f"{code}: {text_message}")
                elif text_message:
                    parts.append(text_message)
                elif code:
                    parts.append(code)
            if parts:
                return "; ".join(parts[:2])
        reasons = parsed.get("blocked_reasons") or parsed.get("missing_dependencies") or []
        if isinstance(reasons, list):
            parts = []
            for item in reasons:
                if not isinstance(item, dict):
                    continue
                code = str(item.get("code") or item.get("kind") or "").strip()
                text_message = str(item.get("message") or item.get("suggested_action") or "").strip()
                if code and text_message:
                    parts.append(f"{code}: {text_message}")
                elif text_message:
                    parts.append(text_message)
                elif code:
                    parts.append(code)
            return "; ".join(parts)
        return ""

    def analysis_v1_public_message(value: Any) -> str:
        message = str(value or "").strip()
        if not message:
            return ""
        if message.startswith("{") or message.startswith("["):
            try:
                parsed = json.loads(message)
            except Exception:
                return ""
            if isinstance(parsed, dict):
                message = analysis_v1_result_message(parsed)
            else:
                message = ""
        lowered = message.lower()
        voice_credit_error = (
            "insufficient_credit" in lowered
            or "insufficient sub-credit" in lowered
            or ("insufficient credit" in lowered and any(marker in lowered for marker in ("heygen", "tts", "voice", "audio")))
        )
        if voice_credit_error:
            return "语音模型余额不足，请充值"
        if "insufficient credit" in lowered or "plan_credit" in lowered or "api' credits" in lowered:
            if "lipsync" in lowered or "lip-sync" in lowered:
                return "音频匹配服务额度不足，请联系管理员充值后重试。"
            return "媒体处理服务额度不足，请联系管理员充值后重试。"
        if "requires selected model" in lowered or "requires a selected model" in lowered:
            return "当前模型配置已失效，请重新选择后重试。"
        return talking_head_one_click_public_error_message(message)

    def validate_analysis_v1_run_prerequisites(task_row: dict[str, Any], storyboard_mode: str = "quick") -> None:
        rewrite_final = str(task_row.get("rewrite_final_prompt") or task_row.get("final_prompt") or "").strip()
        storyboard_final = str(task_row.get("storyboard_final_prompt") or "").strip()
        missing = []
        if not rewrite_final:
            missing.append("rewrite_final_prompt")
        if storyboard_mode == "model" and not storyboard_final:
            missing.append("storyboard_final_prompt")
        if missing:
            message = "请先在 Prompt Builder 中生成并保存 SRT 改写最终提示词，然后再运行 StoryBoard。" if storyboard_mode == "quick" else "请先在 Prompt Builder 中分别生成并保存 SRT 改写最终提示词和 StoryBoard 最终提示词，然后再运行至 04_02。"
            action = "打开 Prompt Builder，切换 SRT 改写标签，选择模型生成最终提示词并保存。" if storyboard_mode == "quick" else "打开 Prompt Builder，分别切换 SRT 改写 / StoryBoard 标签，选择模型生成最终提示词并保存。"
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "analysis_v1_final_prompt_missing",
                    "message": message,
                    "missing": missing,
                    "suggested_action": action,
                },
            )

    def analysis_v1_default_asr_provider() -> str:
        try:
            with ctx.engine.connect() as conn:
                row = conn.execute(
                    text(
                        """
SELECT provider
FROM tool_asr_provider_configs
WHERE enabled = true
ORDER BY (name = 'default_asr_provider') DESC, priority ASC, id ASC
LIMIT 1
"""
                    )
                ).mappings().first()
        except Exception:
            return "aliyun_bailian_fun_asr"
        return str((row or {}).get("provider") or "aliyun_bailian_fun_asr").strip() or "aliyun_bailian_fun_asr"

    def validate_analysis_v1_asr_authorization(payload: OpenClipAnalysisV1RunPayload) -> None:
        asr_mode = (payload.asr_mode or "default").strip().lower()
        if asr_mode == "local":
            return
        provider = analysis_v1_default_asr_provider()
        if asr_mode == "cloud" and provider == "local_whisper":
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "cloud_asr_config_not_cloud",
                    "message": "ASR=cloud requires the database default ASR provider to be a cloud provider, but the active config is local_whisper.",
                    "suggested_action": "请在 ASR Model Settings 中把 Active ASR 切回 Aliyun Bailian Fun-ASR，或用管理员运行设置改选 ASR=local。",
                    "asr_mode": asr_mode,
                    "provider": provider,
                },
            )
        if provider == "local_whisper":
            return
        if payload.allow_cloud_asr_data_transfer:
            return
        raise HTTPException(
            status_code=400,
            detail={
                "code": "cloud_asr_data_transfer_not_authorized",
                "message": "当前 ASR 模式会把任务音频发送到数据库配置的云端 ASR provider，需要先明确授权。",
                "missing": ["allow_cloud_asr_data_transfer"],
                "suggested_action": "请在运行弹窗中勾选“云端 ASR 允许传输音频”，或改选 ASR=local 后重新运行。",
                "asr_mode": asr_mode,
                "provider": provider,
            },
        )

    def analysis_v1_step_command(
        spec: dict[str, Any],
        *,
        task_id: int,
        session_id: int,
        attempt_id: int,
        workspace: Path,
        model: dict[str, str],
        payload: OpenClipAnalysisV1RunPayload,
    ) -> list[str]:
        script = Path(spec["script"])
        cmd = [analysis_v1_python_bin(), str(script)]
        force_or_resume = ["--force"] if payload.force else ["--resume"]
        if spec["id"] == "00":
            if spec.get("command_mode") == "workspace":
                cmd.extend(["--workspace", str(workspace), "--print-json"])
                if payload.force:
                    cmd.append("--force")
                return cmd
            cmd.extend([
                "--task-id",
                str(task_id),
                "--session-id",
                str(session_id),
                "--attempt-id",
                str(attempt_id),
                "--attempt-mode",
                "latest",
                "--clip-mode",
                "virtual",
                "--selected-scheme",
                "detail",
                "--print-json",
            ])
            if payload.force:
                cmd.append("--force")
            if payload.allow_cloud_asr_data_transfer:
                cmd.append("--allow-cloud-asr-data-transfer")
            return cmd
        cmd.extend(["--workspace", str(workspace), "--print-json"])
        if spec["id"] == "02_01":
            cmd.extend(["--asr-mode", payload.asr_mode or "default"])
        if spec["id"] in {"03_01", "03_02", "03_03", "04_01", "04_02"} and spec.get("requires_database", True):
            cmd.extend(["--database-url-env", "OPENCREW_DATABASE_URL"])
        if spec["id"] in {"03_02", "03_03"}:
            options = payload.options or {}
            _providers, tts_model = resolve_analysis_v1_tts_model_option(
                str(options.get("providers") or ""),
                str(options.get("model") or "gemini-3.1-flash-tts-preview"),
            )
            catalog_dir = str(options.get("voice_catalog_dir") or payload.tts_voice_catalog_dir or "").strip()
            if not catalog_dir:
                catalog_dir = str(ANALYSIS_V1_ROOT / "VoiceCatalog" / tts_model)
            cmd.extend(["--voice-catalog-dir", catalog_dir])
        if spec["id"] in {"03_01", "03_02", "03_03"}:
            options = payload.options or {}
            try:
                reference_start = max(0.0, float(options.get("reference_start") or 0.0))
                reference_duration = max(0.0, float(options.get("reference_duration") or 0.0))
            except (TypeError, ValueError):
                reference_start = 0.0
                reference_duration = 0.0
            if reference_duration > 0:
                cmd.extend([
                    "--reference-start",
                    f"{reference_start:.3f}",
                    "--reference-duration",
                    f"{reference_duration:.3f}",
                ])
            if spec["id"] == "03_03":
                providers, tts_model = resolve_analysis_v1_tts_model_option(
                    str(options.get("providers") or ""),
                    str(options.get("model") or "gemini-3.1-flash-tts-preview"),
                )
                if not providers:
                    providers = "qwen" if "qwen" in tts_model.lower() else "google"
                cmd.extend(["--model", tts_model, "--providers", providers])
                for option_key, cli_key, fallback in (
                    ("stage1_count", "--stage1-count", 24),
                    ("stage2_count", "--stage2-count", 6),
                    ("final_count", "--final-count", 3),
                ):
                    try:
                        count_value = max(1, int(options.get(option_key) or fallback))
                    except (TypeError, ValueError):
                        count_value = fallback
                    cmd.extend([cli_key, str(count_value)])
                enable_speechbrain_raw = options.get("enable_speechbrain", True)
                enable_speechbrain = enable_speechbrain_raw if isinstance(enable_speechbrain_raw, bool) else str(enable_speechbrain_raw or "").strip().lower() in {"1", "true", "yes", "on"}
                if not enable_speechbrain:
                    cmd.append("--disable-speechbrain")
        if spec["id"] in {"04_01", "04_02"}:
            cmd.extend(["--model-provider", model["providerID"], "--model-id", model["modelID"]])
        cmd.extend(force_or_resume)
        return cmd

    def analysis_v1_step_capabilities() -> dict[str, bool]:
        return {
            "supports_run_only": True,
            "supports_graceful_stop": True,
            "supports_terminate": False,
            "safe_to_discard_partial_outputs": False,
        }

    def analysis_v1_step_payload(spec: dict[str, Any], status: str = "pending") -> dict[str, Any]:
        script = Path(spec["script"])
        registry_tool = analysis_v1_tool_display_index().get(str(spec["id"]), {})
        name = str(spec["name"])
        return {
            "id": str(spec["id"]),
            "name": name,
            "display_name_zh": str(spec.get("display_name_zh") or registry_tool.get("display_name_zh") or name),
            "display_name_en": str(spec.get("display_name_en") or registry_tool.get("display_name_en") or name),
            "entrypoint": script.name,
            "script": script.name,
            "script_path": str(script),
            "timeout": int(spec.get("timeout") or 0),
            "status": status,
            "started_at": None,
            "finished_at": None,
            "duration_seconds": None,
            "exit_code": None,
            "message": "",
            "stdout_tail": "",
            "stderr_tail": "",
            "result": {},
            "capabilities": analysis_v1_step_capabilities(),
        }

    def analysis_v1_option_snapshot(payload: OpenClipAnalysisV1RunPayload, model: dict[str, str] | None = None) -> dict[str, Any]:
        options = dict(payload.options or {})
        options.update({
            "asr_mode": payload.asr_mode,
            "allow_cloud_asr_data_transfer": bool(payload.allow_cloud_asr_data_transfer),
            "tts_builder_mode": payload.tts_builder_mode,
            "rewrite_mode": payload.rewrite_mode,
            "storyboard_mode": payload.storyboard_mode,
            "run_model_provider": (model or {}).get("providerID") or payload.run_model_provider,
            "run_model_id": (model or {}).get("modelID") or payload.run_model_id,
            "force": bool(payload.force),
            "selected_step_ids": [str(item) for item in (payload.selected_step_ids or [])],
            "billing_scope": "diagnostic" if analysis_v1_normalize_mode(payload.mode) == "run_only_step" else "production",
        })
        return options

    def analysis_v1_run_state_path(workspace: Path, attempt_id: int) -> Path:
        return workspace / "SessionReport" / "tool_runs" / f"attempt_{attempt_id}" / "run_state.json"

    def analysis_v1_step_log_path(workspace: Path, attempt_id: int, step_id: str, stream_name: str) -> Path:
        return workspace / "SessionReport" / "tool_runs" / f"attempt_{attempt_id}" / "logs" / f"{step_id}.{stream_name}.log"

    def analysis_v1_load_state_file(workspace: Path, attempt_id: int) -> dict[str, Any] | None:
        path = analysis_v1_run_state_path(workspace, attempt_id)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if isinstance(payload, dict):
            payload.setdefault("attempt_id", attempt_id)
            payload.setdefault("workspace", str(workspace))
            return payload
        return None

    def analysis_v1_persist_state(state: dict[str, Any]) -> None:
        attempt_id = int(state.get("attempt_id") or 0)
        workspace_raw = str(state.get("workspace") or "")
        if not attempt_id or not workspace_raw:
            return
        write_json(analysis_v1_run_state_path(Path(workspace_raw), attempt_id), state)

    def analysis_v1_run_state(attempt_id: int, workspace: Path | None = None) -> dict[str, Any] | None:
        with analysis_v1_run_lock:
            state = analysis_v1_run_states.get(attempt_id)
        if state is None and workspace is not None:
            state = analysis_v1_load_state_file(workspace, attempt_id)
            if state is not None:
                with analysis_v1_run_lock:
                    analysis_v1_run_states[attempt_id] = state
        if state is None:
            return None
        return json.loads(json.dumps(state, ensure_ascii=False))

    def analysis_v1_state_for_attempt(attempt: dict[str, Any]) -> dict[str, Any] | None:
        try:
            session_row = safe_session(int(attempt.get("session_id") or 0))
        except Exception:
            return None
        return analysis_v1_run_state(int(attempt.get("id") or 0), Path(str(session_row.get("workspace_dir") or "")))

    def analysis_v1_set_run_state(run_attempt_id: int, workspace: Path | None = None, **fields: Any) -> dict[str, Any]:
        timestamp = now_ms()
        with analysis_v1_run_lock:
            state = analysis_v1_run_states.get(run_attempt_id)
            if state is None and workspace is not None:
                state = analysis_v1_load_state_file(workspace, run_attempt_id)
            if state is None:
                state = {"attempt_id": run_attempt_id}
            state.update(fields)
            if workspace is not None:
                state["workspace"] = str(workspace)
            state["updated_at"] = timestamp
            if str(state.get("status") or "").lower() in ANALYSIS_V1_ACTIVE_STATUSES:
                state["heartbeat_at"] = timestamp
            analysis_v1_run_states[run_attempt_id] = state
            snapshot = json.loads(json.dumps(state, ensure_ascii=False))
        analysis_v1_persist_state(snapshot)
        return snapshot

    def analysis_v1_update_step(attempt_id: int, step_id: str, workspace: Path | None = None, **fields: Any) -> None:
        timestamp = now_ms()
        with analysis_v1_run_lock:
            state = analysis_v1_run_states.get(attempt_id)
            if state is None and workspace is not None:
                state = analysis_v1_load_state_file(workspace, attempt_id)
            if not state:
                return
            for step in state.get("steps") or []:
                if str(step.get("id") or "") == str(step_id):
                    step.update(fields)
                    break
            state["updated_at"] = timestamp
            if str(state.get("status") or "").lower() in ANALYSIS_V1_ACTIVE_STATUSES:
                state["heartbeat_at"] = timestamp
            analysis_v1_run_states[attempt_id] = state
            snapshot = json.loads(json.dumps(state, ensure_ascii=False))
        analysis_v1_persist_state(snapshot)

    def analysis_v1_step_from_state(state: dict[str, Any] | None, step_id: str) -> dict[str, Any] | None:
        for step in (state or {}).get("steps") or []:
            if str(step.get("id") or "") == str(step_id):
                return step
        return None

    def analysis_v1_event(session_id: int, kind: str, payload: dict[str, Any], *, task_id: int, attempt_id: int, step_id: str = "") -> None:
        add_session_event(
            session_id,
            f"analysis_v1.run_to_storyboard.{kind}",
            payload,
            family=ANALYSIS_V1_ATTEMPT_FAMILY,
            task_id=task_id,
            attempt_id=attempt_id,
            tool_id="analysis_v1_run_to_storyboard",
            step_id=step_id or None,
        )

    def analysis_v1_asset_type_for_rel(rel_path: str) -> str:
        suffix = Path(str(rel_path or "")).suffix.lower()
        if suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
            return "Image"
        if suffix in {".mp4", ".mov", ".webm", ".m4v"}:
            return "Video"
        if suffix in {".wav", ".m4a", ".mp3", ".aac", ".ogg"}:
            return "Audio"
        if suffix == ".json":
            return "JSON"
        return "File"

    def analysis_v1_slot_for_rel(rel_path: str) -> str:
        stem = Path(str(rel_path or "")).stem
        for slot in ("Audio_Final", "Image_Source", "Image_New", "Image_02", "Video_Final", "Video_Raw", "TailFrame", "ImagePrompt", "VideoPrompt"):
            if slot in stem:
                return slot
        return analysis_v1_asset_type_for_rel(rel_path)

    def analysis_v1_history_item_for_rel(original_rel: str, history_rel: str, reason: str) -> dict[str, Any]:
        asset_key = Path(original_rel).stem
        for marker in ("_Audio_", "_Image_", "_Video_"):
            if marker in asset_key:
                asset_key = asset_key.split(marker, 1)[0]
                break
        return {
            "original_path": original_rel,
            "history_path": history_rel,
            "asset_type": analysis_v1_asset_type_for_rel(original_rel),
            "slot": analysis_v1_slot_for_rel(original_rel),
            "asset_key": asset_key,
            "reason": reason,
        }

    def analysis_v1_archive_storyboard_working_for_full_grouping(workspace: Path, step_id: str) -> dict[str, Any]:
        if str(step_id) != "04_02":
            return {}
        working_dir = workspace / ANALYSIS_V1_STORYBOARD_WORKING_REL
        if not working_dir.exists():
            return {}
        working_dir.mkdir(parents=True, exist_ok=True)
        reason = "04_02_full_grouping_reset_working"
        batch = f"batch_{now_ms()}_{reason}"
        batch_rel = f"{ANALYSIS_V1_STORYBOARD_HISTORY_REL}/{batch}"
        backup_working_rel = f"{batch_rel}/Working"
        backup_working_dir = workspace / backup_working_rel
        items: list[dict[str, Any]] = []
        for child in sorted(working_dir.iterdir(), key=lambda item: item.name):
            if child.name == ".DS_Store":
                try:
                    child.unlink()
                except FileNotFoundError:
                    pass
                continue
            backup_working_dir.mkdir(parents=True, exist_ok=True)
            target = backup_working_dir / child.name
            if target.exists():
                target = backup_working_dir / f"{target.stem}_{now_ms()}{target.suffix}"
            shutil.move(str(child), str(target))
            original_rel = f"{ANALYSIS_V1_STORYBOARD_WORKING_REL}/{child.name}"
            history_rel = target.relative_to(workspace).as_posix()
            items.append(analysis_v1_history_item_for_rel(original_rel, history_rel, reason))
        working_dir.mkdir(parents=True, exist_ok=True)
        if not items:
            return {}
        manifest = {
            "schema_version": "storyboard_asset_history_0.1",
            "batch_id": batch,
            "reason": reason,
            "created_at": now_ms(),
            "source_working_path": ANALYSIS_V1_STORYBOARD_WORKING_REL,
            "backup_path": backup_working_rel,
            "source_storyboard_path": ANALYSIS_V1_STORYBOARD_REL,
            "items": items,
            "updated_at": now_ms(),
        }
        manifest_path = workspace / batch_rel / "manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"backup_path": batch_rel, "working_path": backup_working_rel, "moved_count": len(items), "items": items}

    def analysis_v1_reset_storyboard_generated_plan_state(workspace: Path, step_id: str) -> dict[str, Any]:
        if str(step_id) != "04_02":
            return {}
        removed: list[dict[str, str]] = []
        for rel in ANALYSIS_V1_STORYBOARD_PLAN_RESET_RELS:
            path = workspace / rel
            if not path.exists():
                continue
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
                removed.append({"path": rel, "type": "directory"})
            else:
                path.unlink()
                removed.append({"path": rel, "type": "file"})
        return {"reason": "04_02_full_grouping_reset_generated_plan_state", "removed": removed, "removed_count": len(removed)} if removed else {}

    def analysis_v1_archive_storyboard_edit_after_source_refresh(workspace: Path, step_id: str) -> dict[str, Any]:
        if str(step_id) not in {"04_02", "04_03"}:
            return {}
        working_archive = analysis_v1_archive_storyboard_working_for_full_grouping(workspace, step_id)
        plan_reset = analysis_v1_reset_storyboard_generated_plan_state(workspace, step_id)
        edit_path = workspace / "SessionOutput/storyboard/koubo_storyboard_edit.json"
        edit_archive: dict[str, Any] = {}
        if edit_path.is_file():
            batch = f"batch_{now_ms()}_analysis_v1_storyboard_source_refreshed"
            target = workspace / "SessionOutput/storyboard/assets/history" / batch / "koubo_storyboard_edit.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                target = target.parent / f"koubo_storyboard_edit_{now_ms()}.json"
            shutil.move(str(edit_path), str(target))
            edit_archive = {
                "archived_edit_path": target.relative_to(workspace).as_posix(),
                "reason": "analysis_v1_storyboard_source_refreshed",
            }
        if not edit_archive and not working_archive and not plan_reset:
            return {}
        return {
            **edit_archive,
            "working_archive": working_archive,
            "generated_plan_state_reset": plan_reset,
        }

    def analysis_v1_script_digest(path: Path) -> str:
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except Exception:
            return ""

    def analysis_v1_source_video_signature(task_row: dict[str, Any]) -> dict[str, Any]:
        value = str(task_row.get("reference_video_path") or "").strip()
        path = Path(value).expanduser() if value else None
        if path and path.is_file():
            try:
                stat = path.stat()
                return {"path": value, "size": int(stat.st_size), "mtime_ms": int(stat.st_mtime * 1000)}
            except Exception:
                return {"path": value}
        return {"path": value}

    def analysis_v1_plan_hash(task_row: dict[str, Any], specs: list[dict[str, Any]], options: dict[str, Any]) -> str:
        payload = {
            "target": ANALYSIS_V1_TARGET,
            "steps": [
                {
                    "id": str(spec["id"]),
                    "entrypoint": Path(spec["script"]).name,
                    "script_sha256": analysis_v1_script_digest(Path(spec["script"])),
                    "timeout": int(spec.get("timeout") or 0),
                    "capabilities": analysis_v1_step_capabilities(),
                }
                for spec in specs
            ],
            "options": options,
            "source_video": analysis_v1_source_video_signature(task_row),
            "task_context": {
                "industry": str(task_row.get("industry") or ""),
                "persona": str(task_row.get("persona") or ""),
                "target_audience": str(task_row.get("target_audience") or ""),
                "analysis_goal": str(task_row.get("analysis_goal") or ""),
                "video_formula": str(task_row.get("video_formula") or ""),
                "rewrite_final_prompt_sha256": hashlib.sha256(str(task_row.get("rewrite_final_prompt") or task_row.get("final_prompt") or "").encode("utf-8")).hexdigest(),
                "storyboard_final_prompt_sha256": hashlib.sha256(str(task_row.get("storyboard_final_prompt") or "").encode("utf-8")).hexdigest(),
                "storyboard_quick_config": storyboard_quick_config_from_row(task_row),
            },
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        return f"sha256:{digest}"

    def analysis_v1_step_output_rels(step_id: str) -> list[str]:
        return {
            "00": ["SessionContext/Variables.json", "S1_00_PrepareSessionVariables/Report/Result.json"],
            "01": ["S2_01_VideoProbeMetadata/Output/Video_Metadata.json", "S2_01_VideoProbeMetadata/Report/Result.json"],
            "02_01": ["SessionOutput/Audio_Reference.wav", "S3_02_01_AudioASR/Output/ASR_Segments.json", "S3_02_01_AudioASR/Report/Result.json"],
            "02_02": ["SessionOutput/subtitle/final_srt_frame_items.json", "SessionOutput/visual/srt_frame_map.json", "S4_02_02_VideoSRTFrame/Report/Result.json"],
            "03_01": ["SessionOutput/tts/tts_builder_candidates.json", "S5_03_01_TTSBuilderG/Report/Result.json"],
            "03_02": ["SessionOutput/tts/tts_builder_candidates.json", "S5_03_02_TTSBuilderQuick/Report/Result.json"],
            "03_03": ["SessionOutput/tts/tts_builder_candidates.json", "S5_03_03_TTSBuilderQuickAdv/Report/Result.json"],
            "04_01": ["SessionOutput/subtitle/rewritten_srt_items.json", "S6_04_01_SRTRewrite/Report/Result.json"],
            "04_02": ["SessionOutput/storyboard/srt_storyboard.json", "S7_04_02_StoryBoard/Report/Result.json"],
            "04_03": ["SessionOutput/storyboard/srt_storyboard.json", "S7_04_03_StoryBoardQuick/Report/Result.json"],
        }.get(str(step_id), [])

    def analysis_v1_step_output_rels_for_profile(step_id: str, workflow_profile: str = "") -> list[str]:
        if workflow_profile == "person_talking_head_v1":
            return {
                "00": ["SessionContext/Variables.json", "S1_00_PrepareSessionVariables/Report/Result.json"],
                "04_01": ["SessionOutput/subtitle/rewritten_srt_items.json", "S6_04_01_TalkingHeadSRTRewrite/Report/Result.json"],
                "01": ["SessionOutput/storyboard/srt_storyboard.json", "SessionOutput/storyboard/koubo_storyboard_edit.json", "S2_01_StoryBoardGenerate/Report/Result.json"],
                "02": ["SessionOutput/storyboard/srt_storyboard.json", "SessionOutput/storyboard/koubo_storyboard_edit.json", "S3_02_StoryBoardStructure/Report/Result.json"],
                "03": ["SessionOutput/storyboard/srt_storyboard.json", "SessionOutput/storyboard/koubo_storyboard_edit.json", "S4_03_StoryBoardConfig/Report/Result.json"],
            }.get(str(step_id), [])
        return analysis_v1_step_output_rels(step_id)

    def analysis_v1_required_input_rels(step_id: str) -> list[str]:
        return {
            "00": [],
            "01": ["SessionContext/Variables.json"],
            "02_01": ["SessionContext/Variables.json", "S2_01_VideoProbeMetadata/Output/Video_Metadata.json"],
            "02_02": ["SessionContext/Variables.json", "S2_01_VideoProbeMetadata/Output/Video_Metadata.json", "S3_02_01_AudioASR/Output/ASR_Segments.json"],
            "03_01": ["SessionContext/Variables.json", "SessionOutput/subtitle/final_srt_frame_items.json", "SessionOutput/Audio_Reference.wav"],
            "03_02": ["SessionContext/Variables.json", "SessionOutput/subtitle/final_srt_frame_items.json", "SessionOutput/Audio_Reference.wav"],
            "03_03": ["SessionContext/Variables.json", "SessionOutput/subtitle/final_srt_frame_items.json", "SessionOutput/Audio_Reference.wav"],
            "04_01": ["SessionOutput/subtitle/final_srt_frame_items.json"],
            "04_02": ["SessionOutput/subtitle/rewritten_srt_items.json"],
            "04_03": ["SessionOutput/subtitle/rewritten_srt_items.json"],
        }.get(str(step_id), [])

    def analysis_v1_required_input_rels_for_profile(step_id: str, workflow_profile: str = "") -> list[str]:
        if workflow_profile == "person_talking_head_v1":
            return {
                "00": [],
                "04_01": ["SessionContext/Variables.json"],
                "01": ["SessionContext/Variables.json", "SessionOutput/subtitle/rewritten_srt_items.json"],
                "02": ["SessionContext/Variables.json", "SessionOutput/storyboard/srt_storyboard.json"],
                "03": ["SessionContext/Variables.json", "SessionOutput/storyboard/srt_storyboard.json"],
            }.get(str(step_id), [])
        return analysis_v1_required_input_rels(step_id)

    def analysis_v1_plan_dependency_block(workspace: Path, plan: dict[str, Any]) -> dict[str, Any] | None:
        produced: set[str] = set()
        execute_ids = [str(item) for item in plan.get("execute_step_ids") or []]
        plan_options = plan.get("options") if isinstance(plan.get("options"), dict) else {}
        workflow_profile = str(plan_options.get("workflow_profile") or plan_options.get("profile_id") or "").strip()
        for step_id in execute_ids:
            required_rels = analysis_v1_required_input_rels_for_profile(step_id, workflow_profile)
            missing = [
                rel
                for rel in required_rels
                if rel not in produced and not (workspace / rel).is_file()
            ]
            if missing:
                if step_id == "04_01" and "SessionOutput/subtitle/final_srt_frame_items.json" in missing:
                    message = "04_01 的输入依赖缺失：缺少 SessionOutput/subtitle/final_srt_frame_items.json。请先运行到 02_02 字幕帧对齐，或执行一次全量任务后再运行自由改写。"
                    suggested_action = "请先运行全部任务，或从 02_02 字幕帧对齐开始运行，生成字幕帧产物后再重试 04_01。"
                else:
                    message = f"{step_id} 的输入依赖缺失，无法从该步骤开始运行。"
                    suggested_action = "请从更早步骤开始运行，或先生成缺失产物后再重试。"
                return {
                    "code": "analysis_v1_dependency_missing",
                    "step_id": step_id,
                    "message": message,
                    "missing": missing,
                    "suggested_action": suggested_action,
                }
            produced.update(analysis_v1_step_output_rels_for_profile(step_id, workflow_profile))
        return None

    def analysis_v1_normalize_mode(mode: str) -> str:
        value = str(mode or "run_all").strip().lower()
        aliases = {
            "all": "run_all",
            "range": "run_range",
            "from_step": "run_from_step",
            "only_step": "run_only_step",
            "selected": "run_selected_steps",
            "selected_steps": "run_selected_steps",
            "custom_steps": "run_selected_steps",
            "rerun": "rerun_all",
        }
        value = aliases.get(value, value)
        if value not in ANALYSIS_V1_RUN_MODES:
            raise HTTPException(status_code=400, detail=f"Unsupported Analysis_V1 run mode: {value}")
        return value

    def analysis_v1_previous_failed_step(previous_state: dict[str, Any] | None, valid_step_ids: set[str]) -> str:
        for step in (previous_state or {}).get("steps") or []:
            step_id = str(step.get("id") or "")
            if step_id in valid_step_ids and str(step.get("status") or "").lower() in {"failed", "blocked", "cancelled", "stale_running"}:
                return step_id
        return ""

    def analysis_v1_compile_plan(task_row: dict[str, Any], payload: OpenClipAnalysisV1RunPayload, model: dict[str, str] | None = None) -> dict[str, Any]:
        mode = analysis_v1_normalize_mode(payload.mode)
        tts_builder_mode = normalize_analysis_v1_tts_builder_mode(payload)
        rewrite_mode = normalize_analysis_v1_rewrite_mode(payload)
        storyboard_mode = normalize_analysis_v1_storyboard_mode(payload)
        specs = analysis_v1_run_step_specs(tts_builder_mode, storyboard_mode, rewrite_mode, analysis_v1_effective_workflow_profile(payload, task_row))
        step_ids = [str(spec["id"]) for spec in specs]
        step_id_set = set(step_ids)
        previous_attempt_id = int(payload.previous_attempt_id or 0) or None
        if mode in {"rerun_all", "rerun_failed", "rerun_from_step"} and not previous_attempt_id:
            previous_attempt_id = int(task_row.get("latest_attempt_id") or 0) or None
        previous_state = None
        if previous_attempt_id:
            previous_attempt = repo.get_attempt(previous_attempt_id)
            if previous_attempt and int(previous_attempt.get("task_id") or 0) == int(task_row["id"]):
                previous_state = analysis_v1_state_for_attempt(previous_attempt)
        start_step_id = str(payload.start_step_id or "").strip()
        end_step_id = str(payload.end_step_id or "").strip()
        run_only_step_id = str(payload.run_only_step_id or "").strip()
        selected_step_ids = [str(item or "").strip() for item in (payload.selected_step_ids or []) if str(item or "").strip()]
        if mode in {"run_all", "rerun_all"}:
            start_step_id = step_ids[0]
            end_step_id = step_ids[-1]
        elif mode == "run_range":
            start_step_id = start_step_id or step_ids[0]
            end_step_id = end_step_id or step_ids[-1]
        elif mode in {"run_from_step", "rerun_from_step"}:
            start_step_id = start_step_id or step_ids[0]
            end_step_id = end_step_id or step_ids[-1]
        elif mode == "run_only_step":
            run_only_step_id = run_only_step_id or start_step_id
            if not run_only_step_id:
                raise HTTPException(status_code=400, detail="run_only_step_id is required for run_only_step")
            start_step_id = run_only_step_id
            end_step_id = run_only_step_id
        elif mode == "run_selected_steps":
            if not selected_step_ids:
                raise HTTPException(status_code=400, detail="selected_step_ids is required for run_selected_steps")
            for value in selected_step_ids:
                if value not in step_id_set:
                    raise HTTPException(status_code=400, detail=f"selected_step_ids contains a step that is not in the Analysis_V1 plan: {value}")
            ordered_selected = [step_id for step_id in step_ids if step_id in set(selected_step_ids)]
            selected_step_ids = ordered_selected
            start_step_id = selected_step_ids[0]
            end_step_id = selected_step_ids[-1]
        elif mode == "rerun_failed":
            failed_step_id = analysis_v1_previous_failed_step(previous_state, step_id_set)
            if not failed_step_id:
                raise HTTPException(status_code=409, detail={"code": "no_failed_step", "message": "没有可从失败处重跑的步骤。"})
            start_step_id = failed_step_id
            end_step_id = step_ids[-1]
        for value, label in ((start_step_id, "start_step_id"), (end_step_id, "end_step_id")):
            if value and value not in step_id_set:
                raise HTTPException(status_code=400, detail=f"{label} is not in the Analysis_V1 plan: {value}")
        if run_only_step_id and run_only_step_id not in step_id_set:
            raise HTTPException(status_code=400, detail=f"run_only_step_id is not in the Analysis_V1 plan: {run_only_step_id}")
        start_index = step_ids.index(start_step_id)
        end_index = step_ids.index(end_step_id)
        if start_index > end_index:
            raise HTTPException(status_code=400, detail="start_step_id must not be after end_step_id")
        execute_step_ids = selected_step_ids if mode == "run_selected_steps" else [run_only_step_id] if mode == "run_only_step" else step_ids[start_index:end_index + 1]
        options = analysis_v1_option_snapshot(payload, model)
        plan_hash = analysis_v1_plan_hash(task_row, specs, options)
        steps = []
        execute_set = set(execute_step_ids)
        for index, spec in enumerate(specs):
            step = analysis_v1_step_payload(spec, "pending")
            step["will_execute"] = step["id"] in execute_set
            step["disabled_reason"] = ""
            if mode != "run_only_step" and index < start_index:
                step["status"] = "reused"
                step["reuse_reason"] = "start_step_before_boundary"
                step["previous_attempt_id"] = previous_attempt_id
                step["reused_output_paths"] = analysis_v1_step_output_rels(step["id"])
                step["plan_hash_match"] = bool(previous_state and (previous_state.get("plan") or {}).get("plan_hash") == plan_hash)
            if step["id"] not in execute_set and step.get("status") != "reused":
                step["status"] = "pending"
            steps.append(step)
        return {
            "target": ANALYSIS_V1_TARGET,
            "attempt_family": ANALYSIS_V1_ATTEMPT_FAMILY,
            "mode": mode,
            "start_step_id": start_step_id,
            "end_step_id": end_step_id,
            "run_only_step_id": run_only_step_id if mode == "run_only_step" else "",
            "selected_step_ids": selected_step_ids if mode == "run_selected_steps" else [],
            "previous_attempt_id": previous_attempt_id,
            "pause_before_step_id": str(payload.pause_before_step_id or "").strip(),
            "tts_builder_mode": tts_builder_mode,
            "rewrite_mode": rewrite_mode,
            "storyboard_mode": storyboard_mode,
            "storyboard_step_id": "03" if analysis_v1_effective_workflow_profile(payload, task_row) == "person_talking_head_v1" else ("04_03" if storyboard_mode == "quick" else "04_02"),
            "execute_step_ids": execute_step_ids,
            "plan_hash": plan_hash,
            "options": options,
            "steps": steps,
        }

    def analysis_v1_result_manifest(workspace: Path, task_id: int, attempt_id: int, steps: list[dict[str, Any]]) -> dict[str, Any]:
        output_candidates = [
            ("SessionContext/Variables.json", "session_context"),
            ("SessionOutput/subtitle/final_srt_frame_items.json", "subtitle"),
            ("SessionOutput/subtitle/rewritten_srt_items.json", "subtitle"),
            ("SessionOutput/storyboard/srt_storyboard.json", "storyboard"),
            ("SessionOutput/visual/srt_frame_map.json", "visual"),
            ("SessionOutput/tts/tts_builder_candidates.json", "tts"),
            ("SessionReport/run_to_storyboard_summary.json", "report"),
        ]
        files = []
        for rel, kind in output_candidates:
            path = workspace / rel
            if not path.is_file():
                continue
            stat = path.stat()
            files.append({"path": rel, "kind": kind, "size": int(stat.st_size), "downloadable": True})
        return {
            "schema_version": "analysis_v1_result_manifest_0.1",
            "task_id": task_id,
            "attempt_id": attempt_id,
            "tool_chain": "run_to_storyboard",
            "target": ANALYSIS_V1_TARGET,
            "attempt_family": ANALYSIS_V1_ATTEMPT_FAMILY,
            "finished_at": now_ms(),
            "steps": steps,
            "tool_outputs": [{"tool_id": "analysis_v1_run_to_storyboard", "files": files}],
        }

    def analysis_v1_script_only_input_ready(workspace: Path) -> bool:
        return (
            (workspace / "SessionOutput/subtitle/source_script.txt").is_file()
            and (workspace / "SessionOutput/subtitle/final_srt_frame_items.json").is_file()
        )

    def analysis_v1_execute_uses_uploaded_reference_audio(execute_ids: set[str]) -> bool:
        return bool(execute_ids) and execute_ids <= {"03_01", "03_02", "03_03"}

    def analysis_v1_execute_requires_reference_video(execute_ids: set[str], workflow_profile: str = "") -> bool:
        if workflow_profile == "person_talking_head_v1":
            return False
        return bool(execute_ids & {"01", "02_01", "02_02"})

    def analysis_v1_file_snapshot(workspace: Path) -> dict[str, list[dict[str, Any]]]:
        groups = {
            "inputs": ["SessionContext/Variables.json", "SessionContext/Video_Source.mp4"],
            "outputs": [
                "SessionOutput/subtitle/final_srt_frame_items.json",
                "SessionOutput/subtitle/rewritten_srt_items.json",
                "SessionOutput/storyboard/srt_storyboard.json",
                "SessionOutput/visual/srt_frame_map.json",
                "SessionOutput/tts/tts_builder_candidates.json",
            ],
            "prompts": [
                "SessionContext/Variables.json",
            ],
            "results": [
                "SessionReport/run_to_storyboard_summary.json",
            ],
        }
        snapshot: dict[str, list[dict[str, Any]]] = {}
        for key, rels in groups.items():
            rows = []
            for rel in rels:
                path = workspace / rel
                if path.is_file():
                    stat = path.stat()
                    rows.append({"path": rel, "exists": True, "size": int(stat.st_size), "updated_at": int(stat.st_mtime * 1000)})
                else:
                    rows.append({"path": rel, "exists": False})
            snapshot[key] = rows
        return snapshot

    def analysis_v1_quick_watch_snapshot(
        *,
        step: dict[str, Any],
        command: list[str],
        workspace: Path,
        payload: OpenClipAnalysisV1RunPayload,
        model: dict[str, str],
        task_id: int | None = None,
        session_id: int | None = None,
        attempt_id: int | None = None,
    ) -> dict[str, Any]:
        env = analysis_v1_run_env(task_id=task_id, session_id=session_id, attempt_id=attempt_id, step_id=str(step.get("id") or ""))
        env_summary = {
            "OPENCREW_DATA_DIR": redact_analysis_v1_text(env.get("OPENCREW_DATA_DIR", "")),
            "OPENCREW_DATABASE_URL": redact_analysis_v1_text(env.get("OPENCREW_DATABASE_URL", "")),
            "OPENCREW_TASK_ID": env.get("OPENCREW_TASK_ID", ""),
            "OPENCREW_SESSION_ID": env.get("OPENCREW_SESSION_ID", ""),
            "OPENCREW_ATTEMPT_ID": env.get("OPENCREW_ATTEMPT_ID", ""),
            "OPENCREW_STEP_ID": env.get("OPENCREW_STEP_ID", ""),
            "DATABASE_URL": redact_analysis_v1_text(env.get("DATABASE_URL", "")),
            "OPENCREW_ANALYSIS_V1_PYTHON": redact_analysis_v1_text(env.get("OPENCREW_ANALYSIS_V1_PYTHON", "")),
            "OPENCREW_FFMPEG_PATH": redact_analysis_v1_text(env.get("OPENCREW_FFMPEG_PATH", "")),
            "OPENCREW_FFPROBE_PATH": redact_analysis_v1_text(env.get("OPENCREW_FFPROBE_PATH", "")),
            "PYTHONPATH": redact_analysis_v1_text(env.get("PYTHONPATH", ""), limit=1200),
            "PYTHONUNBUFFERED": env.get("PYTHONUNBUFFERED", ""),
        }
        return {
            "overview": {
                "step_id": step.get("id"),
                "name": step.get("name"),
                "entrypoint": step.get("entrypoint"),
                "timeout": step.get("timeout"),
                "capabilities": step.get("capabilities") or analysis_v1_step_capabilities(),
            },
            "parameters": {
                "mode": payload.mode,
                "options": analysis_v1_option_snapshot(payload, model),
                "previous_attempt_id": payload.previous_attempt_id,
                "pause_before_step_id": payload.pause_before_step_id,
            },
            "command": {
                "argv": [redact_analysis_v1_text(part, limit=1000) for part in command],
                "cwd": str(OPENCREW_REPO_ROOT),
                "env": env_summary,
                "python": command[0] if command else analysis_v1_python_bin(),
            },
            "files": analysis_v1_file_snapshot(workspace),
            "logs": {
                "stdout_tail": step.get("stdout_tail") or "",
                "stderr_tail": step.get("stderr_tail") or "",
            },
            "result": step.get("result") or {},
        }

    def analysis_v1_is_stale(state: dict[str, Any] | None, attempt: dict[str, Any] | None = None) -> bool:
        status = str((state or {}).get("status") or (attempt or {}).get("status") or "").lower()
        if status not in ANALYSIS_V1_ACTIVE_STATUSES:
            return False
        heartbeat = int((state or {}).get("heartbeat_at") or (state or {}).get("updated_at") or (attempt or {}).get("started_at") or (attempt or {}).get("created_at") or 0)
        return bool(heartbeat and now_ms() - heartbeat > ANALYSIS_V1_HEARTBEAT_STALE_MS)

    def analysis_v1_mark_stale_attempt(attempt: dict[str, Any], state: dict[str, Any] | None) -> dict[str, Any] | None:
        if not state or not analysis_v1_is_stale(state, attempt):
            return state
        attempt_id = int(attempt.get("id") or state.get("attempt_id") or 0)
        task_id = int(attempt.get("task_id") or state.get("task_id") or 0)
        session_id = int(attempt.get("session_id") or state.get("session_id") or 0)
        workspace = Path(str(state.get("workspace") or ""))
        timestamp = now_ms()
        for step in state.get("steps") or []:
            if str(step.get("status") or "").lower() in {"queued", "pending", "running"}:
                step["status"] = "stale_running" if str(step.get("status") or "").lower() == "running" else "cancelled"
                step["message"] = step.get("message") or "运行心跳已超时"
        repo.update_attempt(attempt_id, status="stale_running", summary="Analysis_V1 runner heartbeat timed out", finished_at=timestamp)
        if task_id:
            repo.update_task(task_id, status="stale_running", latest_attempt_id=attempt_id, updated_at=timestamp)
        if session_id:
            ctx.session_repo.update(session_id, status="stale_running", finished_at=timestamp, updated_at=timestamp)
        state.update({"status": "stale_running", "finished_at": timestamp, "current_step_id": None, "summary": "Analysis_V1 runner heartbeat timed out"})
        state_fields = dict(state)
        state_fields.pop("workspace", None)
        analysis_v1_set_run_state(attempt_id, workspace if str(workspace) else None, **state_fields)
        try:
            analysis_v1_event(session_id, "attempt.stale_running", {"task_id": task_id, "attempt_id": attempt_id, "heartbeat_at": state.get("heartbeat_at")}, task_id=task_id, attempt_id=attempt_id)
        except Exception:
            pass
        return analysis_v1_run_state(attempt_id, workspace) or state

    def analysis_v1_usage_row_by_id(usage_id: int) -> dict[str, Any] | None:
        with ctx.engine.connect() as conn:
            row = conn.execute(
                text(
                    """
SELECT id, request_id, provider, model_id, modality, provider_mode, billing_mode,
       task_id, attempt_id, step_id, idempotency_key,
       proxy_policy, status, units_json, est_cost_micros,
       actual_cost_micros, actual_cost_currency, actual_cost_source, actual_cost_raw_json,
       pricebook_version, billing_reconciled_at, error_code,
       started_at, finished_at, created_at
FROM local_usage_log
WHERE id = :id
"""
                ),
                {"id": usage_id},
            ).mappings().first()
        return dict(row) if row else None

    def analysis_v1_usage_rows_by_attempt_id(attempt_id: int) -> list[dict[str, Any]]:
        with ctx.engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
SELECT id, request_id, provider, model_id, modality, provider_mode, billing_mode,
       task_id, attempt_id, step_id, idempotency_key,
       proxy_policy, status, units_json, est_cost_micros,
       actual_cost_micros, actual_cost_currency, actual_cost_source, actual_cost_raw_json,
       pricebook_version, billing_reconciled_at, error_code,
       started_at, finished_at, created_at
FROM local_usage_log
WHERE attempt_id = :attempt_id
ORDER BY id ASC
"""
                ),
                {"attempt_id": str(attempt_id)},
            ).mappings().fetchall()
        return [dict(row) for row in rows]

    def analysis_v1_empty_metering_totals() -> dict[str, Any]:
        return {
            "request_count": 0,
            "provider_cost_micros": 0,
            "cost_micros": 0,
            "estimated_cost_micros": 0,
            "estimated_only_cost_micros": 0,
            "charge_micros": 0,
            "customer_charge_micros": 0,
            "sell_micros": 0,
            "profit_micros": 0,
            "actual_cost_micros": 0,
            "actual_cost_count": 0,
            "estimated_cost_count": 0,
            "unpriced_count": 0,
            "units": {},
            "cost_basis_counts": {},
        }

    def analysis_v1_finalize_metering_totals(totals: dict[str, Any]) -> dict[str, Any]:
        finalize_price_lines(totals)
        totals["customer_charge_micros"] = int(totals.get("charge_micros") or 0)
        return totals

    def analysis_v1_metering_item_view(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": item.get("id"),
            "request_id": item.get("request_id"),
            "provider": item.get("provider") or "",
            "model_id": item.get("model_id") or "",
            "modality": item.get("modality") or "",
            "status": item.get("status") or "",
            "units": item.get("units") or {},
            "unit_lines": item.get("unit_lines") or [],
            "has_actual_cost": bool(item.get("has_actual_cost")),
            "actual_cost_micros": int(item.get("actual_cost_micros") or 0),
            "estimated_cost_micros": int(item.get("estimated_cost_micros") or 0),
            "provider_cost_micros": int(item.get("provider_cost_micros") or item.get("cost_micros") or 0),
            "charge_micros": int(item.get("charge_micros") or item.get("sell_micros") or 0),
            "sell_micros": int(item.get("sell_micros") or item.get("charge_micros") or 0),
            "profit_micros": int(item.get("profit_micros") or 0),
            "cost_basis": item.get("cost_basis") or "none",
            "price_source": item.get("price_source") or "",
            "started_at": item.get("started_at"),
            "finished_at": item.get("finished_at"),
            "created_at": item.get("created_at"),
        }

    def analysis_v1_empty_step_metering(step_id: str) -> dict[str, Any]:
        return {
            "step_id": step_id,
            "item_count": 0,
            "totals": analysis_v1_finalize_metering_totals(analysis_v1_empty_metering_totals()),
            "api": analysis_v1_finalize_metering_totals(analysis_v1_empty_metering_totals()),
            "local_artifacts": analysis_v1_finalize_metering_totals(analysis_v1_empty_metering_totals()),
            "items": [],
        }

    def analysis_v1_metering_channel(item: dict[str, Any]) -> str:
        return "local_artifacts" if str(item.get("modality") or "").lower() == "local_artifact" else "api"

    def analysis_v1_metering_summary(attempt_id: int, workspace: Path | None = None) -> dict[str, Any]:
        rows_by_id: dict[int, dict[str, Any]] = {}
        audit_count = 0
        if workspace is not None and workspace.exists():
            for path in workspace.rglob("ModelCallAudit_*.json"):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if int(payload.get("attempt_id") or 0) != attempt_id:
                    continue
                audit_count += 1
                usage_id = analysis_v1_safe_int(payload.get("local_usage_id"))
                if usage_id and usage_id not in rows_by_id:
                    row = analysis_v1_usage_row_by_id(usage_id)
                    if row:
                        rows_by_id[usage_id] = row
        for row in analysis_v1_usage_rows_by_attempt_id(attempt_id):
            rows_by_id[int(row.get("id") or 0)] = row
        totals: dict[str, Any] = analysis_v1_empty_metering_totals()
        by_step: dict[str, dict[str, Any]] = {}
        items = []
        for row in rows_by_id.values():
            item = enrich_usage_row(row)
            add_amount(totals, item)
            step_id = str(item.get("step_id") or "").strip()
            if step_id:
                step_summary = by_step.setdefault(step_id, {
                    "step_id": step_id,
                    "item_count": 0,
                    "totals": analysis_v1_empty_metering_totals(),
                    "api": analysis_v1_empty_metering_totals(),
                    "local_artifacts": analysis_v1_empty_metering_totals(),
                    "items": [],
                })
                step_summary["item_count"] = int(step_summary.get("item_count") or 0) + 1
                add_amount(step_summary["totals"], item)
                add_amount(step_summary[analysis_v1_metering_channel(item)], item)
                if len(step_summary["items"]) < 12:
                    step_summary["items"].append(analysis_v1_metering_item_view(item))
            items.append(item)
        analysis_v1_finalize_metering_totals(totals)
        totals["attribution"] = "attempt_id_column"
        for step_summary in by_step.values():
            analysis_v1_finalize_metering_totals(step_summary["totals"])
            analysis_v1_finalize_metering_totals(step_summary["api"])
            analysis_v1_finalize_metering_totals(step_summary["local_artifacts"])
        return {
            "attempt_id": attempt_id,
            "audit_count": audit_count,
            "item_count": len(items),
            "totals": totals,
            "items": items[:20],
            "by_step": by_step,
        }

    def analysis_v1_indicator_payload(task_id: int, attempt: dict[str, Any], state: dict[str, Any] | None) -> dict[str, Any]:
        attempt_id = int(attempt.get("id") or attempt.get("attempt_id") or 0)
        state = analysis_v1_mark_stale_attempt(attempt, state)
        status = str((state or {}).get("status") or attempt.get("status") or "unknown")
        steps = (state or {}).get("steps") or []
        if state is None:
            steps = [{
                "id": "unavailable",
                "name": "run_state_missing",
                "entrypoint": "",
                "script": "",
                "status": "unavailable",
                "message": "run state missing",
                "capabilities": analysis_v1_step_capabilities(),
            }]
        completed = len([step for step in steps if str(step.get("status") or "").lower() in {"completed", "reused", "skipped"}])
        current_step_id = str((state or {}).get("current_step_id") or "")
        plan = (state or {}).get("plan") or {}
        pause_before_step_id = str((state or {}).get("pause_before_step_id") or plan.get("pause_before_step_id") or "")
        terminal = status.lower() in ANALYSIS_V1_TERMINAL_STATUSES
        capabilities = {
            "can_stop": status.lower() in ANALYSIS_V1_ACTIVE_STATUSES,
            "can_set_pause_point": status.lower() in {"queued", "running", "paused"},
            "can_cancel_pause_point": bool(pause_before_step_id) and status.lower() in {"queued", "running", "paused"},
            "can_resume": status.lower() == "paused",
            "can_rerun_all": terminal,
            "can_rerun_from_step": terminal,
            "can_run_only_step": True,
        }
        attempt_payload = {
            "attempt_id": attempt_id,
            "attempt_no": int(attempt.get("attempt_no") or (state or {}).get("attempt_no") or 0),
            "attempt_family": str((state or {}).get("attempt_family") or "unknown"),
            "task_id": task_id,
            "session_id": int(attempt.get("session_id") or (state or {}).get("session_id") or 0),
            "target": str((state or {}).get("target") or "unknown"),
            "status": status,
            "display_status": status,
            "started_at": (state or {}).get("started_at") or attempt.get("started_at"),
            "finished_at": (state or {}).get("finished_at") or attempt.get("finished_at"),
            "updated_at": (state or {}).get("updated_at") or now_ms(),
            "heartbeat_at": (state or {}).get("heartbeat_at"),
        }
        workspace_for_metering = Path(str((state or {}).get("workspace") or "")) if (state or {}).get("workspace") else None
        metering = analysis_v1_metering_summary(attempt_id, workspace_for_metering)
        metering_by_step = metering.get("by_step") if isinstance(metering.get("by_step"), dict) else {}
        steps_payload = []
        for step in steps:
            step_payload = dict(step)
            step_id = str(step_payload.get("id") or "")
            combined_step_error = "\n".join(str(step_payload.get(key) or "") for key in ("message", "stderr_tail", "stdout_tail"))
            public_step_error = analysis_v1_public_message(combined_step_error)
            if public_step_error == "语音模型余额不足，请充值":
                step_payload["message"] = public_step_error
                step_payload["stderr_tail"] = public_step_error
                step_payload["stdout_tail"] = ""
            step_payload["metering"] = metering_by_step.get(step_id) or analysis_v1_empty_step_metering(step_id)
            steps_payload.append(step_payload)
        return {
            "ok": True,
            "attempt": attempt_payload,
            "plan": {
                "plan_hash": plan.get("plan_hash") or (state or {}).get("plan_hash") or "",
                "mode": plan.get("mode") or (state or {}).get("mode") or "",
                "start_step_id": plan.get("start_step_id") or "",
                "end_step_id": plan.get("end_step_id") or "",
                "run_only_step_id": plan.get("run_only_step_id") or "",
                "previous_attempt_id": plan.get("previous_attempt_id"),
                "pause_before_step_id": pause_before_step_id,
                "execute_step_ids": plan.get("execute_step_ids") or [],
                "options": plan.get("options") or {},
            },
            "progress": {
                "completed": completed,
                "total": len(steps),
                "current_step_id": current_step_id,
            },
            "steps": steps_payload,
            "capabilities": capabilities,
            "task_id": task_id,
            "session_id": attempt_payload["session_id"],
            "attempt_id": attempt_id,
            "attempt_no": attempt_payload["attempt_no"],
            "attempt_family": attempt_payload["attempt_family"],
            "target": attempt_payload["target"],
            "status": status,
            "attempt_status": status,
            "current_step_id": current_step_id,
            "pause_before_step_id": pause_before_step_id,
            "heartbeat_at": attempt_payload.get("heartbeat_at"),
            "updated_at": attempt_payload.get("updated_at"),
            "summary": analysis_v1_public_message((state or {}).get("summary") or attempt.get("summary") or ""),
            "error": (state or {}).get("error") or "",
            "sync_error": (state or {}).get("sync_error") or "",
            "metering": metering,
        }

    def analysis_v1_active_attempt_summary() -> dict[str, Any] | None:
        with analysis_v1_run_lock:
            with ctx.engine.connect() as conn:
                rows = conn.execute(
                    text(
                        """
SELECT id, task_id, session_id, attempt_no, status, started_at, finished_at, created_at
FROM openclip_attempts
WHERE status IN ('queued', 'running', 'paused', 'stopping')
ORDER BY id DESC
"""
                    )
                ).mappings().fetchall()
            for row in rows:
                attempt = dict(row)
                state = analysis_v1_state_for_attempt(attempt)
                if not state or str(state.get("attempt_family") or "") != ANALYSIS_V1_ATTEMPT_FAMILY:
                    continue
                state = analysis_v1_mark_stale_attempt(attempt, state)
                if str((state or {}).get("status") or "").lower() not in ANALYSIS_V1_ACTIVE_STATUSES:
                    continue
                return {
                    "attempt_id": int(attempt["id"]),
                    "task_id": int(attempt["task_id"]),
                    "session_id": int(attempt["session_id"]),
                    "attempt_no": int(attempt["attempt_no"]),
                    "status": str(state.get("status") or attempt.get("status") or ""),
                    "current_step_id": str(state.get("current_step_id") or ""),
                    "updated_at": state.get("updated_at"),
                    "heartbeat_at": state.get("heartbeat_at"),
                }
        return None

    def reject_if_analysis_v1_active(message: str) -> None:
        active = analysis_v1_active_attempt_summary()
        if active:
            raise HTTPException(status_code=409, detail={"code": "active_run_exists", "message": message, "active_attempt": active})

    def analysis_v1_mark_remaining_cancelled(attempt_id: int, workspace: Path) -> None:
        state = analysis_v1_run_state(attempt_id, workspace)
        for step in (state or {}).get("steps") or []:
            if str(step.get("status") or "").lower() == "pending":
                analysis_v1_update_step(attempt_id, str(step.get("id")), workspace, status="cancelled", message="用户请求当前步骤结束后停止")

    def analysis_v1_wait_for_resume_or_stop(task_id: int, session_id: int, attempt_id: int, workspace: Path, step_id: str) -> bool:
        timestamp = now_ms()
        repo.update_attempt(attempt_id, status="paused")
        repo.update_task(task_id, status="paused", latest_attempt_id=attempt_id, updated_at=timestamp)
        ctx.session_repo.update(session_id, status="paused", updated_at=timestamp)
        analysis_v1_set_run_state(attempt_id, workspace, status="paused", current_step_id=None, paused_at=timestamp)
        analysis_v1_event(session_id, "attempt.paused", {"task_id": task_id, "attempt_id": attempt_id, "step_id": step_id}, task_id=task_id, attempt_id=attempt_id, step_id=step_id)
        while True:
            state = analysis_v1_run_state(attempt_id, workspace) or {}
            if state.get("cancel_requested"):
                analysis_v1_set_run_state(attempt_id, workspace, status="stopping")
                return False
            if str(state.get("status") or "").lower() != "paused":
                return True
            analysis_v1_set_run_state(attempt_id, workspace)
            time.sleep(1)

    def analysis_v1_run_process_step(
        *,
        spec: dict[str, Any],
        step: dict[str, Any],
        task_id: int,
        session_id: int,
        attempt_id: int,
        workspace: Path,
        model: dict[str, str],
        payload: OpenClipAnalysisV1RunPayload,
    ) -> dict[str, Any]:
        script = Path(spec["script"])
        if not script.is_file():
            raise RuntimeError(f"Analysis_V1 script not found: {script}")
        sync_analysis_v1_run_context(task_id=task_id, session_id=session_id, attempt_id=attempt_id, workspace=workspace, model=model, step_id=str(step["id"]))
        cmd = analysis_v1_step_command(spec, task_id=task_id, session_id=session_id, attempt_id=attempt_id, workspace=workspace, model=model, payload=payload)
        step_started_at = now_ms()
        stdout_tail: deque[str] = deque(maxlen=200)
        stderr_tail: deque[str] = deque(maxlen=200)
        stdout_log = analysis_v1_step_log_path(workspace, attempt_id, str(step["id"]), "stdout")
        stderr_log = analysis_v1_step_log_path(workspace, attempt_id, str(step["id"]), "stderr")
        stdout_log.parent.mkdir(parents=True, exist_ok=True)
        quick_watch = analysis_v1_quick_watch_snapshot(step=step, command=cmd, workspace=workspace, payload=payload, model=model, task_id=task_id, session_id=session_id, attempt_id=attempt_id)
        analysis_v1_update_step(
            attempt_id,
            str(step["id"]),
            workspace,
            status="running",
            started_at=step_started_at,
            finished_at=None,
            duration_seconds=None,
            exit_code=None,
            message="",
            quick_watch=quick_watch,
        )
        analysis_v1_set_run_state(attempt_id, workspace, status="running", current_step_id=str(step["id"]))
        analysis_v1_event(session_id, "step.started", {"task_id": task_id, "attempt_id": attempt_id, "step_id": step["id"], "script": script.name, "command": [redact_analysis_v1_text(part, limit=1000) for part in cmd]}, task_id=task_id, attempt_id=attempt_id, step_id=str(step["id"]))

        def read_stream(stream: Any, log_path: Path, tail: deque[str]) -> None:
            try:
                with log_path.open("a", encoding="utf-8", errors="replace") as handle:
                    while True:
                        chunk = stream.readline()
                        if chunk == "":
                            break
                        handle.write(chunk)
                        handle.flush()
                        tail.append(chunk)
            finally:
                try:
                    stream.close()
                except Exception:
                    pass

        env = analysis_v1_run_env(task_id=task_id, session_id=session_id, attempt_id=attempt_id, step_id=str(step["id"]))
        if str(step.get("id") or "") == "03_03":
            options = payload.options or {}
            enable_speechbrain_raw = options.get("enable_speechbrain", True)
            enable_speechbrain = enable_speechbrain_raw if isinstance(enable_speechbrain_raw, bool) else str(enable_speechbrain_raw or "").strip().lower() in {"1", "true", "yes", "on"}
            if enable_speechbrain:
                env["ANALYSIS_V1_ENABLE_SPEECHBRAIN"] = "1"
        proc = subprocess.Popen(cmd, cwd=str(OPENCREW_REPO_ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", bufsize=1)
        with analysis_v1_run_lock:
            analysis_v1_processes[attempt_id] = proc
        stdout_thread = threading.Thread(target=read_stream, args=(proc.stdout, stdout_log, stdout_tail), daemon=True)
        stderr_thread = threading.Thread(target=read_stream, args=(proc.stderr, stderr_log, stderr_tail), daemon=True)
        stdout_thread.start()
        stderr_thread.start()
        timed_out = False
        timeout_seconds = int(spec.get("timeout") or 0)
        while proc.poll() is None:
            duration = (now_ms() - step_started_at) / 1000
            if timeout_seconds and duration > timeout_seconds:
                timed_out = True
                proc.terminate()
                break
            analysis_v1_update_step(
                attempt_id,
                str(step["id"]),
                workspace,
                stdout_tail=redact_analysis_v1_tail("".join(stdout_tail)),
                stderr_tail=redact_analysis_v1_tail("".join(stderr_tail)),
            )
            analysis_v1_set_run_state(attempt_id, workspace)
            time.sleep(1)
        if timed_out:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        return_code = proc.wait()
        stdout_thread.join(timeout=2)
        stderr_thread.join(timeout=2)
        with analysis_v1_run_lock:
            analysis_v1_processes.pop(attempt_id, None)
        stdout_text = stdout_log.read_text(encoding="utf-8", errors="replace") if stdout_log.is_file() else ""
        stderr_text = stderr_log.read_text(encoding="utf-8", errors="replace") if stderr_log.is_file() else ""
        step_finished_at = now_ms()
        duration_seconds = round((step_finished_at - step_started_at) / 1000, 3)
        if timed_out:
            parsed: dict[str, Any] = {}
            step_status = "failed"
            message = f"{script.name} timed out after {timeout_seconds} seconds"
        else:
            parsed = parse_analysis_v1_stdout(stdout_text)
            parsed_status = str(parsed.get("status") or ("completed" if return_code == 0 else "failed")).strip().lower()
            step_status = "completed" if return_code == 0 and parsed_status == "completed" else ("blocked" if parsed_status == "blocked" or return_code == 2 else "failed")
            message = analysis_v1_result_message(parsed)
            if return_code != 0 and not message:
                message = (stderr_text or stdout_text or f"{script.name} failed").strip()[:1000]
        message = redact_analysis_v1_text(message, limit=1000)
        step_payload = {
            "status": step_status,
            "finished_at": step_finished_at,
            "duration_seconds": duration_seconds,
            "exit_code": None if timed_out else return_code,
            "message": message,
            "stdout_tail": redact_analysis_v1_tail(stdout_text),
            "stderr_tail": redact_analysis_v1_tail(stderr_text),
            "result": parsed,
        }
        if step_status == "completed":
            archived_storyboard_edit = analysis_v1_archive_storyboard_edit_after_source_refresh(workspace, str(step["id"]))
            if archived_storyboard_edit:
                step_payload["storyboard_edit_archive"] = archived_storyboard_edit
                analysis_v1_event(
                    session_id,
                    "storyboard_edit.archived",
                    {"task_id": task_id, "attempt_id": attempt_id, "step_id": step["id"], **archived_storyboard_edit},
                    task_id=task_id,
                    attempt_id=attempt_id,
                    step_id=str(step["id"]),
                )
            try:
                artifact_metering = analysis_v1_record_local_artifacts(
                    engine=ctx.engine,
                    recorder=ctx.local_usage,
                    spec=spec,
                    task_id=task_id,
                    attempt_id=attempt_id,
                    workspace=workspace,
                    parsed=parsed,
                    started_at=step_started_at,
                    finished_at=step_finished_at,
                )
            except Exception as exc:
                artifact_metering = {"status": "error", "message": redact_analysis_v1_text(str(exc), limit=500)}
                analysis_v1_event(
                    session_id,
                    "step.local_artifact_metering_failed",
                    {"task_id": task_id, "attempt_id": attempt_id, "step_id": step["id"], "message": artifact_metering["message"]},
                    task_id=task_id,
                    attempt_id=attempt_id,
                    step_id=str(step["id"]),
                )
            if artifact_metering:
                step_payload["artifact_metering"] = artifact_metering
                if artifact_metering.get("status") == "recorded":
                    analysis_v1_event(session_id, "step.local_artifact_metered", {"task_id": task_id, "attempt_id": attempt_id, "step_id": step["id"], **artifact_metering}, task_id=task_id, attempt_id=attempt_id, step_id=str(step["id"]))
                elif artifact_metering.get("status") == "deduped":
                    analysis_v1_event(session_id, "step.local_artifact_metering_deduped", {"task_id": task_id, "attempt_id": attempt_id, "step_id": step["id"], **artifact_metering}, task_id=task_id, attempt_id=attempt_id, step_id=str(step["id"]))
        quick_watch["logs"] = {"stdout_tail": step_payload["stdout_tail"], "stderr_tail": step_payload["stderr_tail"]}
        quick_watch["result"] = parsed
        if step_payload.get("artifact_metering"):
            quick_watch["artifact_metering"] = step_payload["artifact_metering"]
        step_payload["quick_watch"] = quick_watch
        analysis_v1_update_step(attempt_id, str(step["id"]), workspace, **step_payload)
        analysis_v1_event(session_id, f"step.{step_status}", {"task_id": task_id, "attempt_id": attempt_id, "step_id": step["id"], "script": script.name, "duration_seconds": duration_seconds, "exit_code": step_payload["exit_code"], "message": message}, task_id=task_id, attempt_id=attempt_id, step_id=str(step["id"]))
        return step_payload

    def analysis_v1_finish_attempt(
        *,
        task_id: int,
        session_id: int,
        attempt_id: int,
        workspace: Path,
        started_at: int,
        status: str,
        summary: str,
        sync_files: bool = False,
    ) -> None:
        finished_at = now_ms()
        final_steps = (analysis_v1_run_state(attempt_id, workspace) or {}).get("steps") or []
        summary_path = workspace / "SessionReport" / "run_to_storyboard_summary.json"
        summary_payload = {
            "schema_version": "analysis_v1_run_to_storyboard_summary_0.2",
            "task_id": task_id,
            "attempt_id": attempt_id,
            "attempt_family": ANALYSIS_V1_ATTEMPT_FAMILY,
            "target": ANALYSIS_V1_TARGET,
            "status": status,
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_seconds": round((finished_at - started_at) / 1000, 3),
            "sync_error": "",
            "steps": final_steps,
        }
        write_json(summary_path, summary_payload)
        sync_error = ""
        if sync_files:
            try:
                sync_session_files(safe_session(session_id))
            except Exception as exc:
                sync_error = str(exc)
        final_status = "completed_with_sync_error" if status == "completed" and sync_error else status
        summary_payload.update({"status": final_status, "sync_error": sync_error})
        write_json(summary_path, summary_payload)
        manifest = analysis_v1_result_manifest(workspace, task_id, attempt_id, final_steps)
        repo.update_attempt(attempt_id, status=final_status, summary=(summary if not sync_error else f"{summary}; file sync failed: {sync_error}")[:4000], result_manifest_json=json.dumps(manifest, ensure_ascii=False), finished_at=finished_at)
        repo.update_task(task_id, status=final_status, latest_attempt_id=attempt_id, updated_at=finished_at)
        ctx.session_repo.update(session_id, status="waiting_input" if final_status == "completed" else final_status, finished_at=finished_at, updated_at=finished_at)
        analysis_v1_set_run_state(attempt_id, workspace, status=final_status, finished_at=finished_at, current_step_id=None, summary=summary, sync_error=sync_error)
        event_kind = "attempt.completed" if final_status == "completed" else f"attempt.{final_status}"
        analysis_v1_event(session_id, event_kind, {"task_id": task_id, "attempt_id": attempt_id, "duration_seconds": round((finished_at - started_at) / 1000, 3), "summary": summary, "sync_error": sync_error}, task_id=task_id, attempt_id=attempt_id)

    def analysis_v1_run_to_storyboard(
        *,
        task_id: int,
        session_id: int,
        attempt_id: int,
        workspace: Path,
        model: dict[str, str],
        payload: OpenClipAnalysisV1RunPayload,
    ) -> None:
        started_at = now_ms()
        final_status = "completed"
        final_summary = "Analysis_V1 run_to_storyboard completed"
        try:
            repo.update_attempt(attempt_id, status="running", started_at=started_at)
            repo.update_task(task_id, status="running", latest_attempt_id=attempt_id, run_model_provider=model["providerID"], run_model_id=model["modelID"], updated_at=now_ms())
            ctx.session_repo.update(session_id, status="running", started_at=started_at, finished_at=None, updated_at=now_ms())
            sync_analysis_v1_run_context(task_id=task_id, session_id=session_id, attempt_id=attempt_id, workspace=workspace, model=model)
            state = analysis_v1_set_run_state(attempt_id, workspace, status="running", started_at=started_at, current_step_id=None)
            plan = state.get("plan") or {}
            specs = {
                str(spec["id"]): spec
                for spec in analysis_v1_run_step_specs(
                    str(plan.get("tts_builder_mode") or payload.tts_builder_mode),
                    str(plan.get("storyboard_mode") or payload.storyboard_mode),
                    str(plan.get("rewrite_mode") or payload.rewrite_mode),
                    analysis_v1_payload_workflow_profile(payload),
                )
            }
            analysis_v1_event(session_id, "attempt.started", {"task_id": task_id, "attempt_id": attempt_id, "model": model, "mode": plan.get("mode"), "plan_hash": plan.get("plan_hash")}, task_id=task_id, attempt_id=attempt_id)
            for step_id in plan.get("execute_step_ids") or []:
                state = analysis_v1_run_state(attempt_id, workspace) or {}
                if state.get("cancel_requested"):
                    final_status = "cancelled"
                    final_summary = "用户请求当前步骤结束后停止"
                    analysis_v1_mark_remaining_cancelled(attempt_id, workspace)
                    break
                if str(state.get("pause_before_step_id") or "") == str(step_id):
                    if not analysis_v1_wait_for_resume_or_stop(task_id, session_id, attempt_id, workspace, str(step_id)):
                        final_status = "cancelled"
                        final_summary = "用户在暂停期间请求停止"
                        analysis_v1_mark_remaining_cancelled(attempt_id, workspace)
                        break
                step = analysis_v1_step_from_state(analysis_v1_run_state(attempt_id, workspace), str(step_id))
                spec = specs.get(str(step_id))
                if not step or not spec:
                    final_status = "failed"
                    final_summary = f"Step not found in compiled plan: {step_id}"
                    break
                result = analysis_v1_run_process_step(spec=spec, step=step, task_id=task_id, session_id=session_id, attempt_id=attempt_id, workspace=workspace, model=model, payload=payload)
                if str(result.get("status")) == "blocked":
                    final_status = "blocked"
                    final_summary = str(result.get("message") or f"{step_id} blocked")
                    break
                if str(result.get("status")) != "completed":
                    final_status = "failed"
                    final_summary = str(result.get("message") or f"{step_id} failed")
                    break
                state = analysis_v1_run_state(attempt_id, workspace) or {}
                if state.get("cancel_requested"):
                    final_status = "cancelled"
                    final_summary = "用户请求当前步骤结束后停止"
                    analysis_v1_mark_remaining_cancelled(attempt_id, workspace)
                    break
            if final_status == "completed":
                final_summary = "Analysis_V1 run_to_storyboard completed"
            analysis_v1_finish_attempt(task_id=task_id, session_id=session_id, attempt_id=attempt_id, workspace=workspace, started_at=started_at, status=final_status, summary=final_summary, sync_files=final_status == "completed")
        except Exception as exc:
            with analysis_v1_run_lock:
                analysis_v1_processes.pop(attempt_id, None)
            message = redact_analysis_v1_text(str(exc), limit=4000)
            analysis_v1_finish_attempt(task_id=task_id, session_id=session_id, attempt_id=attempt_id, workspace=workspace, started_at=started_at, status="failed", summary=message, sync_files=False)

    def analysis_v1_one_click_state_rel() -> str:
        return "SessionReport/analysis_v1/one_click_movie_state.json"

    def analysis_v1_one_click_run_state_path(workspace: Path, run_id: str) -> Path:
        return workspace / "SessionReport" / "analysis_v1" / "one_click_movie" / f"{run_id}.json"

    def analysis_v1_one_click_latest_state_path(workspace: Path) -> Path:
        return workspace / analysis_v1_one_click_state_rel()

    def analysis_v1_one_click_write_state(workspace: Path, state: dict[str, Any]) -> dict[str, Any]:
        state["updated_at"] = now_ms()
        run_id = str(state.get("run_id") or "").strip()
        if run_id:
            write_json_atomic(analysis_v1_one_click_run_state_path(workspace, run_id), state)
        write_json_atomic(analysis_v1_one_click_latest_state_path(workspace), state)
        return state

    def analysis_v1_one_click_load_state(workspace: Path, run_id: str = "") -> dict[str, Any]:
        target = analysis_v1_one_click_run_state_path(workspace, str(run_id).strip()) if str(run_id or "").strip() else analysis_v1_one_click_latest_state_path(workspace)
        return read_json_file(target) if target.is_file() else {}

    def analysis_v1_one_click_step_payload(step_id: str, name: str, script: Path | str = "", status: str = "pending") -> dict[str, Any]:
        script_path = Path(script) if script else Path("")
        display_name_zh = {
            "01_StoryBoardGenerate": "故事版生成",
            "02_StoryBoardStructure": "故事版分镜生成",
            "03_StoryBoardConfig": "故事版配置",
        }.get(name) or {
            "00": "运行变量准备",
            "01": "读取视频元数据",
            "02_01": "音频识别",
            "02_02": "字幕帧对齐",
            "03_02": "快速声音匹配",
            "04_01": "口播脚本改写" if name == "04_01_TalkingHeadSRTRewrite" else "SRT 改写",
            "04_03": "快速分组",
            "05_01": "生成视频计划",
            "05_02": "逐句生成视频",
            "06_01": "合并成片",
        }.get(step_id, name)
        return {
            "id": step_id,
            "name": name,
            "display_name_zh": display_name_zh,
            "entrypoint": script_path.name if str(script_path) != "." else "",
            "script": script_path.name if str(script_path) != "." else "",
            "script_path": str(script_path) if str(script_path) != "." else "",
            "status": status,
            "started_at": None,
            "finished_at": None,
            "duration_seconds": None,
            "returncode": None,
            "exit_code": None,
            "tool_status": "",
            "message": "",
            "result_path": "",
            "stdout_tail": "",
            "stderr_tail": "",
            "argv": [],
            "capabilities": {"supports_run_only": step_id in {"05_01", "05_02", "06_01"}, "supports_run_from": step_id in {"05_01", "05_02", "06_01"}},
        }

    def analysis_v1_one_click_analysis_payload(payload: OpenClipAnalysisV1OneClickMoviePayload, task_id: int) -> OpenClipAnalysisV1RunPayload:
        selected_step_ids = ["00", "04_01", "01", "02", "03"] if analysis_v1_payload_workflow_profile(payload) == "person_talking_head_v1" else ["00", "01", "02_01", "02_02", "03_02", "04_01", "04_03"]
        return OpenClipAnalysisV1RunPayload(
            task_id=task_id,
            run_model_provider=payload.run_model_provider,
            run_model_id=payload.run_model_id,
            asr_mode=payload.asr_mode or "default",
            allow_cloud_asr_data_transfer=bool(payload.allow_cloud_asr_data_transfer),
            force=bool(payload.force),
            include_tts_builder=True,
            tts_builder_mode=payload.tts_builder_mode or "quick",
            rewrite_mode=payload.rewrite_mode or "strict",
            storyboard_mode=payload.storyboard_mode or "quick",
            mode="run_selected_steps",
            selected_step_ids=selected_step_ids,
            options=payload.options or {},
        )

    def analysis_v1_one_click_tool_specs(payload: OpenClipAnalysisV1OneClickMoviePayload, task_id: int) -> list[dict[str, Any]]:
        analysis_payload = analysis_v1_one_click_analysis_payload(payload, task_id)
        analysis_specs = {
            str(spec["id"]): spec
            for spec in analysis_v1_run_step_specs(
                normalize_analysis_v1_tts_builder_mode(analysis_payload),
                normalize_analysis_v1_storyboard_mode(analysis_payload),
                normalize_analysis_v1_rewrite_mode(analysis_payload),
                analysis_v1_payload_workflow_profile(analysis_payload),
            )
        }
        if analysis_v1_payload_workflow_profile(analysis_payload) == "person_talking_head_v1":
            ordered = [
                analysis_specs["00"],
                analysis_specs["04_01"],
                analysis_specs["01"],
                analysis_specs["02"],
                analysis_specs["03"],
                {"id": "05_01", "name": "05_01_VideoPlanGenerator", "script": ANALYSIS_V1_ROOT / "05_01_VideoPlanGenerator.py", "timeout": 900},
                {"id": "05_02", "name": "05_02_TalkingHeadVideoPlanExecutor", "script": TALKING_HEAD_V1_ROOT / "05_02_VideoPlanExecutor.py", "timeout": 14400},
                {"id": "06_01", "name": "06_01_TalkingHeadVideoPlanComposer", "script": TALKING_HEAD_V1_ROOT / "06_01_VideoPlanComposer.py", "timeout": 7200},
            ]
        else:
            ordered = [
                analysis_specs["00"],
                analysis_specs["01"],
                analysis_specs["02_01"],
                analysis_specs["02_02"],
                analysis_specs["03_02"],
                analysis_specs["04_01"],
                analysis_specs["04_03"],
                {"id": "05_01", "name": "05_01_VideoPlanGenerator", "script": ANALYSIS_V1_ROOT / "05_01_VideoPlanGenerator.py", "timeout": 900},
                {"id": "05_02", "name": "05_02_VideoPlanExecutor", "script": ANALYSIS_V1_ROOT / "05_02_VideoPlanExecutor.py", "timeout": 14400},
                {"id": "06_01", "name": "06_01_VideoPlanComposer", "script": ANALYSIS_V1_ROOT / "06_01_VideoPlanComposer.py", "timeout": 7200},
            ]
        return ordered

    def analysis_v1_one_click_compile_plan(payload: OpenClipAnalysisV1OneClickMoviePayload, task_id: int) -> dict[str, Any]:
        specs = analysis_v1_one_click_tool_specs(payload, task_id)
        return {
            "target": ANALYSIS_V1_ONE_CLICK_TARGET,
            "steps": [analysis_v1_one_click_step_payload(str(spec["id"]), str(spec["name"]), spec.get("script", "")) for spec in specs],
            "options": {
                "force": bool(payload.force),
                "resume": bool(payload.resume),
                "run_only_step_id": str(payload.run_only_step_id or "").strip(),
                "run_from_step_id": str(payload.run_from_step_id or "").strip(),
                "tts_builder_mode": payload.tts_builder_mode or "quick",
                "rewrite_mode": payload.rewrite_mode or "strict",
                "storyboard_mode": payload.storyboard_mode or "quick",
                "video_plan_settings": payload.video_plan_settings or {},
                "composer_settings": payload.composer_settings or {},
            },
        }

    def analysis_v1_one_click_selected_specs(payload: OpenClipAnalysisV1OneClickMoviePayload, specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        run_only_step_id = str(payload.run_only_step_id or "").strip()
        run_from_step_id = str(payload.run_from_step_id or "").strip()
        if run_only_step_id:
            selected = [spec for spec in specs if str(spec.get("id")) == run_only_step_id]
        elif run_from_step_id:
            selected = []
            for index, spec in enumerate(specs):
                if str(spec.get("id")) == run_from_step_id:
                    selected = specs[index:]
                    break
        else:
            selected = specs

        # A TalkingHead StoryBoard may already exist while its selected voice,
        # reference video, or privacy-grid settings have changed afterwards.
        # Starting directly at 05_01/05_02 used to skip 03_StoryBoardConfig,
        # leaving every dialogue audio path empty.  TalkingHead 05_02 correctly
        # refuses to fall back to arbitrary/default TTS, so all segments then
        # failed immediately with "Audio generation is required but disabled".
        #
        # Re-run 03 as a cache-friendly server-side prerequisite and rebuild
        # 05_01 when resuming directly at 05_02.  This keeps voice/provider
        # runtime fields sourced from the trusted Session Variables snapshot;
        # no client-supplied TTS runtime fields reach the executor.
        if analysis_v1_payload_workflow_profile(payload) == WORKFLOW_PERSON_TALKING_HEAD_V1:
            selected_ids = {str(spec.get("id") or "") for spec in selected}
            prerequisite_ids = talking_head_one_click_prerequisite_step_ids(selected_ids)
            if prerequisite_ids:
                prerequisites = [
                    {**spec, "talking_head_server_preflight": True}
                    for spec in specs
                    if str(spec.get("id") or "") in prerequisite_ids
                ]
                selected = prerequisites + selected
        return selected

    def analysis_v1_one_click_file_status(workspace: Path, rel_path: str) -> dict[str, Any]:
        rel = str(rel_path or "").strip()
        if not rel:
            return {"exists": False, "size": 0}
        path = workspace / rel
        exists = path.is_file()
        return {"exists": exists, "size": int(path.stat().st_size) if exists else 0}

    def analysis_v1_one_click_flatten_segments(plan: dict[str, Any]) -> list[dict[str, Any]]:
        segments: list[dict[str, Any]] = []
        for shot in plan.get("shots") or []:
            if not isinstance(shot, dict):
                continue
            for scene in shot.get("scenes") or []:
                if not isinstance(scene, dict):
                    continue
                for segment in scene.get("segments") or []:
                    if isinstance(segment, dict):
                        segments.append(segment)
        return segments

    def analysis_v1_one_click_segments(workspace: Path) -> list[dict[str, Any]]:
        plan = read_json_file(workspace / "SessionOutput/storyboard/video_generation_plan.json")
        execution_state = read_json_file(workspace / "SessionOutput/storyboard/video_plan_execution_state.json")
        execution_segments = execution_state.get("segments") if isinstance(execution_state.get("segments"), dict) else {}
        items: list[dict[str, Any]] = []
        for index, segment in enumerate(analysis_v1_one_click_flatten_segments(plan), start=1):
            segment_id = str(segment.get("segment_id") or "").strip()
            asset_key = str(segment.get("asset_key") or segment_id or f"segment_{index:03d}").strip()
            state = execution_segments.get(segment_id) if isinstance(execution_segments.get(segment_id), dict) else {}
            steps = state.get("steps") if isinstance(state.get("steps"), dict) else {}
            public_steps = {
                str(key): {
                    "status": str(value.get("status") or "").strip(),
                    "updated_at": value.get("updated_at"),
                    **({"error": analysis_v1_public_message(value.get("error"))} if analysis_v1_public_message(value.get("error")) else {}),
                }
                for key, value in steps.items()
                if isinstance(value, dict)
            }
            outputs = segment.get("planned_outputs") if isinstance(segment.get("planned_outputs"), dict) else {}
            first_frame = segment.get("first_frame") if isinstance(segment.get("first_frame"), dict) else {}
            materialize = first_frame.get("materialize_first_frame") if isinstance(first_frame.get("materialize_first_frame"), dict) else {}
            tail_frame = segment.get("tail_frame") if isinstance(segment.get("tail_frame"), dict) else {}
            tasks = segment.get("tasks") if isinstance(segment.get("tasks"), dict) else {}
            first_frame_path = str(materialize.get("copy_to_path") or first_frame.get("planned_generated_image_path") or segment.get("first_frame_image_path") or "").strip()
            final_video_path = str(outputs.get("final_video_path") or outputs.get("video_path") or "").strip()
            raw_video_path = str(outputs.get("raw_video_path") or "").strip()
            audio_path = str(outputs.get("segment_audio_path") or segment.get("segment_audio_path") or "").strip()
            segment_status = str(state.get("status") or "").strip()
            if not segment_status:
                segment_status = "completed" if final_video_path and (workspace / final_video_path).is_file() else "pending"
            items.append({
                "index": index,
                "segment_id": segment_id,
                "asset_key": asset_key,
                "dialogue_ids": segment.get("dialogue_ids") or segment.get("dialogue_asset_keys") or [],
                "duration_seconds": segment.get("duration_seconds") or segment.get("duration") or 0,
                "status": segment_status,
                "first_frame_source_type": str(materialize.get("source_type") or first_frame.get("source_type") or "").strip(),
                "image_step_label": "尾帧" if str(materialize.get("source_type") or first_frame.get("source_type") or "").strip() in {"previous_segment_tail_frame", "previous_scene_tail_frame"} else "新图",
                "steps": public_steps,
                "files": {
                    "audio": analysis_v1_one_click_file_status(workspace, audio_path),
                    "first_frame": analysis_v1_one_click_file_status(workspace, first_frame_path),
                    "raw_video": analysis_v1_one_click_file_status(workspace, raw_video_path),
                    "final_video": analysis_v1_one_click_file_status(workspace, final_video_path),
                    "tail_frame": analysis_v1_one_click_file_status(workspace, str(tail_frame.get("planned_path") or "").strip()),
                },
                "lipsync": {
                    "need_lipsync": bool(tasks.get("need_lipsync", True)),
                    "sync_mode": str(tasks.get("sync_mode") or "").strip(),
                    "reason": str(tasks.get("lipsync_reason") or "").strip(),
                },
                "error": analysis_v1_public_message(state.get("error")),
            })
        return items

    def analysis_v1_one_click_public_steps(steps: Any, fallback_error: str = "") -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for step in steps if isinstance(steps, list) else []:
            if not isinstance(step, dict):
                continue
            message = analysis_v1_public_message(step.get("message"))
            if not message and fallback_error and str(step.get("status") or "").lower() in {"failed", "blocked"}:
                message = fallback_error
            items.append({
                "id": str(step.get("id") or ""),
                "name": str(step.get("name") or ""),
                "display_name_zh": str(step.get("display_name_zh") or ""),
                "status": str(step.get("status") or "pending"),
                "started_at": step.get("started_at"),
                "finished_at": step.get("finished_at"),
                "duration_seconds": step.get("duration_seconds"),
                "message": message,
                "capabilities": step.get("capabilities") if isinstance(step.get("capabilities"), dict) else {},
            })
        return items

    def analysis_v1_one_click_compose_summary(workspace: Path) -> dict[str, Any]:
        result = read_json_file(workspace / "SessionOutput/storyboard/video_plan_compose_result.json")
        shot_plan = result.get("shot_plan") if isinstance(result.get("shot_plan"), dict) else {}
        outputs = shot_plan.get("outputs") if isinstance(shot_plan.get("outputs"), dict) else {}
        summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
        output_video = str(shot_plan.get("output_video") or summary.get("output_video") or outputs.get("shot_plan_subtitled_video_path") or outputs.get("shot_plan_video_path") or "").strip()
        return {"exists": bool(result), "status": str(result.get("status") or "").strip(), "output_video": output_video, "summary": summary}

    def analysis_v1_one_click_reconcile_stale(workspace: Path, state: dict[str, Any], task_id: int, session_id: int) -> dict[str, Any]:
        run_id = str(state.get("run_id") or "").strip()
        if not run_id or str(state.get("status") or "").lower() not in {"queued", "running"}:
            return state
        with analysis_v1_one_click_lock:
            if run_id in analysis_v1_active_one_click_runs:
                return state
        finished_at = now_ms()
        started_at = int(state.get("started_at") or state.get("created_at") or finished_at)
        message = "后台服务已重启，原一键成片运行进程已中断；请从失败步骤继续运行。"
        current_step_id = str(state.get("current_step_id") or "")
        for step in state.get("steps") or []:
            if not isinstance(step, dict):
                continue
            if str(step.get("id") or "") == current_step_id or str(step.get("status") or "").lower() in {"queued", "running"}:
                step_started = int(step.get("started_at") or started_at)
                step.update({"status": "failed", "finished_at": finished_at, "duration_seconds": round((finished_at - step_started) / 1000, 3), "tool_status": "abandoned", "message": message, "stderr_tail": message})
        state.update({"status": "failed", "current_step_id": None, "finished_at": finished_at, "duration_seconds": round((finished_at - started_at) / 1000, 3), "summary": message, "segments": analysis_v1_one_click_segments(workspace), "compose": analysis_v1_one_click_compose_summary(workspace)})
        analysis_v1_one_click_write_state(workspace, state)
        add_session_event(session_id, "analysis_v1.one_click_movie.failed", {"task_id": task_id, "run_id": run_id, "summary": message}, family="analysis_v1_one_click_movie", task_id=task_id, tool_id=ANALYSIS_V1_ONE_CLICK_TARGET)
        return state

    def analysis_v1_one_click_status(task_id: int, run_id: str = "", workflow_profile: str = "") -> dict[str, Any]:
        task = get_task(task_id)
        if workflow_profile == WORKFLOW_PERSON_TALKING_HEAD_V1:
            ensure_talking_head_v1_task(task)
        else:
            ensure_analysis_v1_compatible_task(task)
        workspace = analysis_v1_workspace(task)
        state = analysis_v1_one_click_load_state(workspace, run_id)
        if not state:
            state = {
                "schema_version": "analysis_v1_koubo_one_click_movie_state_0.1",
                "task_id": task_id,
                "session_id": int(task["session_id"]),
                "run_id": "",
                "target": ANALYSIS_V1_ONE_CLICK_TARGET,
                "status": "idle",
                "steps": analysis_v1_one_click_compile_plan(OpenClipAnalysisV1OneClickMoviePayload(options={"workflow_profile": workflow_profile} if workflow_profile else {}), task_id)["steps"],
                "summary": "",
            }
        else:
            state = analysis_v1_one_click_reconcile_stale(workspace, state, task_id, int(task["session_id"]))
        segments = analysis_v1_one_click_segments(workspace)
        fallback_error = next((str(item.get("error") or "") for item in segments if str(item.get("error") or "")), "")
        summary = analysis_v1_public_message(state.get("summary")) or fallback_error
        return {
            "ok": True,
            "task_id": task_id,
            "session_id": int(task["session_id"]),
            "run_id": str(state.get("run_id") or ""),
            "target": ANALYSIS_V1_ONE_CLICK_TARGET,
            "status": str(state.get("status") or "idle"),
            "current_step_id": state.get("current_step_id"),
            "steps": analysis_v1_one_click_public_steps(state.get("steps"), fallback_error),
            "segments": segments,
            "compose": analysis_v1_one_click_compose_summary(workspace),
            "summary": summary,
            "started_at": state.get("started_at"),
            "finished_at": state.get("finished_at"),
            "duration_seconds": state.get("duration_seconds"),
            "updated_at": state.get("updated_at") or now_ms(),
        }

    def analysis_v1_one_click_video_plan_settings(payload: OpenClipAnalysisV1OneClickMoviePayload) -> dict[str, float]:
        settings = payload.video_plan_settings or {}
        def positive(value: Any, fallback: float) -> float:
            try:
                parsed = float(value)
                return parsed if parsed > 0 else fallback
            except Exception:
                return fallback
        max_seconds = positive(settings.get("max_video_seconds"), 4.0)
        min_seconds = min(max(2.0, positive(settings.get("min_video_seconds"), 2.0)), max_seconds)
        tolerance = max(0.0, positive(settings.get("split_tolerance_seconds"), 2.0))
        if int(max_seconds) == 10:
            tolerance = 0.0
        return {"max_video_seconds": max_seconds, "min_video_seconds": min_seconds, "split_tolerance_seconds": tolerance}

    def analysis_v1_one_click_composer_settings(payload: OpenClipAnalysisV1OneClickMoviePayload) -> dict[str, Any]:
        settings = payload.composer_settings or {}
        default_subtitle_mode = "none" if analysis_v1_payload_workflow_profile(payload) == WORKFLOW_PERSON_TALKING_HEAD_V1 else "hyperframe"
        subtitle_mode = str(settings.get("subtitle_mode") or default_subtitle_mode).strip()
        watermark_mode = str(settings.get("watermark_mode") or "never").strip()
        if subtitle_mode not in {"hyperframe", "none"}:
            subtitle_mode = default_subtitle_mode
        if watermark_mode not in {"always", "auto", "never"}:
            watermark_mode = "never"
        return {"subtitle_mode": subtitle_mode, "watermark_mode": watermark_mode, "force": bool(payload.force)}

    def analysis_v1_one_click_command_for_step(spec: dict[str, Any], task: dict[str, Any], attempt_id: int, run_id: str, payload: OpenClipAnalysisV1OneClickMoviePayload, model: dict[str, str]) -> list[str]:
        task_id = int(task["id"])
        session_id = int(task["session_id"])
        workspace = analysis_v1_workspace(task)
        step_id = str(spec.get("id"))
        if analysis_v1_payload_workflow_profile(payload) == "person_talking_head_v1" and step_id in {"00", "04_01", "01", "02", "03"}:
            analysis_payload = analysis_v1_one_click_analysis_payload(payload, task_id)
            if step_id == "03" and spec.get("talking_head_server_preflight"):
                # Cache hits remain free and a changed voice/reference is
                # refreshed from the server-authored Variables snapshot.
                analysis_payload = analysis_payload.model_copy(update={"force": False, "resume": True})
            return analysis_v1_step_command(spec, task_id=task_id, session_id=session_id, attempt_id=attempt_id, workspace=workspace, model=model, payload=analysis_payload)
        if step_id in {"00", "01", "02_01", "02_02", "03_02", "04_01", "04_03"}:
            analysis_payload = analysis_v1_one_click_analysis_payload(payload, task_id)
            return analysis_v1_step_command(spec, task_id=task_id, session_id=session_id, attempt_id=attempt_id, workspace=workspace, model=model, payload=analysis_payload)
        if step_id == "05_01":
            settings = analysis_v1_one_click_video_plan_settings(payload)
            command = [analysis_v1_python_bin(), str(ANALYSIS_V1_ROOT / "05_01_VideoPlanGenerator.py"), "--workspace", str(workspace), "--target-type", "task", "--max-video-seconds", str(settings["max_video_seconds"]), "--min-video-seconds", str(settings["min_video_seconds"]), "--split-tolerance-seconds", str(settings["split_tolerance_seconds"]), "--print-json"]
            command.append("--force" if payload.force or spec.get("talking_head_server_preflight") else "--resume")
            return command
        if step_id == "05_02":
            plan = read_json_file(workspace / "SessionOutput/storyboard/video_generation_plan.json")
            plan_hash_value = str(plan.get("plan_hash") or "").strip()
            command = [analysis_v1_python_bin(), str(spec["script"]), "--workspace", str(workspace), "--execution-job-id", run_id, "--print-json"]
            if analysis_v1_payload_workflow_profile(payload) == WORKFLOW_PERSON_TALKING_HEAD_V1:
                # TalkingHead plans bind the selected TTS audio to generated
                # portrait video via the configured lip-sync provider. The
                # dedicated executor defaults this capability to disabled, so
                # the workflow command must opt in explicitly.
                command.append("--execute-lipsync")
            video_settings = payload.video_plan_settings or {}
            video_provider = str(video_settings.get("video_provider") or "").strip()
            video_model = str(video_settings.get("video_model") or "").strip()
            if video_provider:
                command.extend(["--video-provider", video_provider])
            if video_model:
                command.extend(["--video-model", video_model])
            if plan_hash_value:
                command.extend(["--source-plan-hash", plan_hash_value])
            if payload.force:
                command.append("--force")
            return command
        if step_id == "06_01":
            settings = analysis_v1_one_click_composer_settings(payload)
            command = [analysis_v1_python_bin(), str(spec["script"]), "--workspace", str(workspace), "--target-type", "task", "--subtitle-mode", settings["subtitle_mode"], "--watermark-mode", settings["watermark_mode"], "--print-json"]
            if settings.get("force", True):
                command.append("--force")
            return command
        raise RuntimeError(f"Unsupported one-click step: {step_id}")

    def analysis_v1_one_click_run_step(task: dict[str, Any], attempt_id: int, run_id: str, payload: OpenClipAnalysisV1OneClickMoviePayload, spec: dict[str, Any], state: dict[str, Any], model: dict[str, str]) -> tuple[str, str]:
        workspace = analysis_v1_workspace(task)
        task_id = int(task["id"])
        session_id = int(task["session_id"])
        step_id = str(spec.get("id"))
        step = next(item for item in state["steps"] if str(item.get("id")) == step_id)
        command = analysis_v1_one_click_command_for_step(spec, task, attempt_id, run_id, payload, model)
        started_at = now_ms()
        sync_analysis_v1_run_context(task_id=task_id, session_id=session_id, attempt_id=attempt_id, workspace=workspace, model=model, step_id=step_id)
        step.update({"status": "running", "started_at": started_at, "finished_at": None, "duration_seconds": None, "argv": [redact_analysis_v1_text(part, limit=1000) for part in command], "message": ""})
        state.update({"status": "running", "current_step_id": step_id})
        analysis_v1_one_click_write_state(workspace, state)
        add_session_event(session_id, "analysis_v1.one_click_movie.step.started", {"task_id": task_id, "run_id": run_id, "step_id": step_id}, family="analysis_v1_one_click_movie", task_id=task_id, attempt_id=attempt_id, tool_id=ANALYSIS_V1_ONE_CLICK_TARGET, step_id=step_id)
        completed = subprocess.run(command, cwd=str(OPENCREW_REPO_ROOT), env=analysis_v1_run_env(task_id=task_id, session_id=session_id, attempt_id=attempt_id, step_id=step_id), capture_output=True, text=True, check=False, timeout=int(spec.get("timeout") or 7200))
        parsed = parse_analysis_v1_stdout(completed.stdout or "")
        parsed_status = str(parsed.get("status") or ("completed" if completed.returncode == 0 else "failed")).strip().lower()
        if analysis_v1_one_click_step_succeeded(step_id, completed.returncode, parsed_status):
            step_status = "completed"
        elif completed.returncode == 2 or parsed_status == "blocked":
            step_status = "blocked"
        else:
            step_status = "failed"
        message = analysis_v1_result_message(parsed)
        if step_status != "completed" and not message:
            message = (completed.stderr or completed.stdout or f"{step_id} failed").strip()[:1000]
        message = redact_analysis_v1_text(message, limit=1000)
        finished_at = now_ms()
        step.update({"status": step_status, "finished_at": finished_at, "duration_seconds": round((finished_at - started_at) / 1000, 3), "returncode": completed.returncode, "exit_code": completed.returncode, "tool_status": parsed_status, "message": message, "result": parsed, "stdout_tail": redact_analysis_v1_tail(completed.stdout or ""), "stderr_tail": redact_analysis_v1_tail(completed.stderr or "")})
        state["segments"] = analysis_v1_one_click_segments(workspace)
        state["compose"] = analysis_v1_one_click_compose_summary(workspace)
        analysis_v1_one_click_write_state(workspace, state)
        add_session_event(session_id, f"analysis_v1.one_click_movie.step.{step_status}", {"task_id": task_id, "run_id": run_id, "step_id": step_id, "message": message}, family="analysis_v1_one_click_movie", task_id=task_id, attempt_id=attempt_id, tool_id=ANALYSIS_V1_ONE_CLICK_TARGET, step_id=step_id)
        return step_status, message or f"{step_id} {step_status}"

    def analysis_v1_one_click_mark_unselected(state: dict[str, Any], selected_ids: set[str]) -> None:
        if not selected_ids:
            return
        for step in state.get("steps") or []:
            if str(step.get("id") or "") not in selected_ids:
                step.update({"status": "skipped", "message": "本次未重新执行。"})

    def analysis_v1_one_click_background(task_id: int, run_id: str, attempt_id: int, payload: OpenClipAnalysisV1OneClickMoviePayload, model: dict[str, str]) -> None:
        task = get_task(task_id)
        workspace = analysis_v1_workspace(task)
        session_id = int(task["session_id"])
        started_at = now_ms()
        final_status = "completed"
        final_summary = "口播一键成片完成"
        try:
            repo.update_attempt(attempt_id, status="running", started_at=started_at)
            state = analysis_v1_one_click_load_state(workspace, run_id)
            state.update({"status": "running", "started_at": started_at, "current_step_id": None})
            specs = analysis_v1_one_click_tool_specs(payload, task_id)
            selected = analysis_v1_one_click_selected_specs(payload, specs)
            if (payload.run_only_step_id or payload.run_from_step_id) and not selected:
                raise RuntimeError(f"不支持的一键成片步骤: {payload.run_only_step_id or payload.run_from_step_id}")
            selected_ids = {str(spec.get("id")) for spec in selected}
            analysis_v1_one_click_mark_unselected(state, selected_ids)
            analysis_v1_one_click_write_state(workspace, state)
            add_session_event(session_id, "analysis_v1.one_click_movie.started", {"task_id": task_id, "run_id": run_id}, family="analysis_v1_one_click_movie", task_id=task_id, attempt_id=attempt_id, tool_id=ANALYSIS_V1_ONE_CLICK_TARGET)
            for spec in selected:
                step_status, message = analysis_v1_one_click_run_step(task, attempt_id, run_id, payload, spec, state, model)
                if step_status == "blocked":
                    final_status = "blocked"
                    final_summary = message
                    break
                if step_status != "completed":
                    final_status = "failed"
                    final_summary = message
                    break
            finished_at = now_ms()
            state.update({"status": final_status, "current_step_id": None, "finished_at": finished_at, "duration_seconds": round((finished_at - started_at) / 1000, 3), "summary": final_summary, "segments": analysis_v1_one_click_segments(workspace), "compose": analysis_v1_one_click_compose_summary(workspace)})
            analysis_v1_one_click_write_state(workspace, state)
            repo.update_attempt(attempt_id, status=final_status, summary=final_summary[:4000], finished_at=finished_at)
            if final_status == "completed":
                try:
                    sync_session_files(safe_session(session_id))
                except Exception:
                    pass
            add_session_event(session_id, f"analysis_v1.one_click_movie.{final_status}", {"task_id": task_id, "run_id": run_id, "summary": final_summary}, family="analysis_v1_one_click_movie", task_id=task_id, attempt_id=attempt_id, tool_id=ANALYSIS_V1_ONE_CLICK_TARGET)
        except Exception as exc:
            finished_at = now_ms()
            message = redact_analysis_v1_text(str(exc), limit=4000)
            state = analysis_v1_one_click_load_state(workspace, run_id)
            state.update({"status": "blocked", "current_step_id": None, "finished_at": finished_at, "duration_seconds": round((finished_at - int(state.get("started_at") or started_at)) / 1000, 3), "summary": message, "segments": analysis_v1_one_click_segments(workspace), "compose": analysis_v1_one_click_compose_summary(workspace)})
            analysis_v1_one_click_write_state(workspace, state)
            repo.update_attempt(attempt_id, status="blocked", summary=message[:4000], finished_at=finished_at)
            add_session_event(session_id, "analysis_v1.one_click_movie.blocked", {"task_id": task_id, "run_id": run_id, "summary": message}, family="analysis_v1_one_click_movie", task_id=task_id, attempt_id=attempt_id, tool_id=ANALYSIS_V1_ONE_CLICK_TARGET)
        finally:
            with analysis_v1_one_click_lock:
                analysis_v1_active_one_click_runs.pop(run_id, None)

    def analysis_v1_one_click_start(task_id: int, payload: OpenClipAnalysisV1OneClickMoviePayload, role: str, request: Request) -> dict[str, Any]:
        task = get_task(task_id)
        selected_talking_head_video_model: dict[str, Any] = {}
        if analysis_v1_payload_workflow_profile(payload) == WORKFLOW_PERSON_TALKING_HEAD_V1:
            ensure_talking_head_v1_task(task)
            config_row = repo.get_talking_head_config(task_id) or {}
            try:
                task_config = json.loads(str(config_row.get("config_json") or "{}"))
            except Exception:
                task_config = {}
            if not isinstance(task_config, dict):
                task_config = {}
            talking_head = task_config.get("talking_head") if isinstance(task_config.get("talking_head"), dict) else {}
            video_model = talking_head.get("video_model") if isinstance(talking_head.get("video_model"), dict) else {}
            selected_video_model = resolve_talking_head_video_model(
                model_key=video_model.get("model_key"),
                provider=video_model.get("provider"),
                model=video_model.get("model"),
                model_alias=video_model.get("model_alias"),
            )
            if not selected_video_model:
                raise HTTPException(status_code=400, detail={"code": "talking_head_video_model_invalid", "message": "人物口播任务没有有效的视频模型选择。"})
            selected_talking_head_video_model = selected_video_model
            video_plan_settings = dict(payload.video_plan_settings or {})
            video_plan_settings.update({
                "video_provider": selected_video_model["provider"],
                "video_model": selected_video_model["model"],
            })
            payload = payload.model_copy(update={"video_plan_settings": video_plan_settings})
        else:
            ensure_analysis_v1_compatible_task(task)
        session_id = int(task["session_id"])
        session_row = safe_session(session_id)
        workspace = analysis_v1_workspace(task)
        workflow_profile = analysis_v1_effective_workflow_profile(payload, task, workspace)
        payload = analysis_v1_payload_with_workflow_profile(payload, workflow_profile)
        if analysis_v1_active_attempt_summary():
            raise HTTPException(status_code=409, detail={"code": "active_analysis_v1_run_exists", "message": "已有 Analysis_V1 工具链运行中。"})
        existing_state = normalize_existing_video_plan_execution_state(workspace)
        if str(existing_state.get("status") or "").strip() in {"queued", "running"}:
            raise HTTPException(status_code=409, detail={"code": "active_video_plan_execution_exists", "message": "已有 VideoPlan 执行中。"})
        composer_state = read_json_file(workspace / "SessionOutput/storyboard/video_plan_compose_state.json")
        if str(composer_state.get("status") or "").strip() in {"queued", "running"}:
            raise HTTPException(status_code=409, detail={"code": "active_composer_execution_exists", "message": "已有合并成片任务运行中。"})
        if workflow_profile == WORKFLOW_PERSON_TALKING_HEAD_V1:
            # Always refresh the server-owned snapshot before deciding which
            # one-click prerequisites to run.  A task edit can change voice,
            # reference video, grid settings, or video model after StoryBoard
            # generation, even when the old video model still appears ready.
            run_session_variables_prepare_00(task, force=True)
            variables = read_json_file(workspace / "SessionContext" / "Variables.json")
            talking_head_variables = variables.get("talking_head") if isinstance(variables.get("talking_head"), dict) else {}
            voice_timing = talking_head_variables.get("voice_timing") if isinstance(talking_head_variables.get("voice_timing"), dict) else {}
            selected_voice_id = str(voice_timing.get("voice_id") or variables.get("voice_id") or "").strip()
            if not selected_voice_id:
                raise HTTPException(status_code=409, detail={
                    "code": "talking_head_voice_required",
                    "message": "当前人物口播任务尚未选择可用音色，请选择音色并保存后再执行一键成片。",
                })
        run_provider = payload.run_model_provider or (str(task.get("run_model_provider") or "") if role == "admin" else "")
        run_model_id = payload.run_model_id or (str(task.get("run_model_id") or "") if role == "admin" else "")
        model, _ = resolve_model(session_row, run_provider, run_model_id, "Analysis V1 one-click movie", role, SURFACE_ANALYSIS_V1_RUN)
        analysis_payload = analysis_v1_one_click_analysis_payload(payload, task_id)
        specs = analysis_v1_one_click_tool_specs(payload, task_id)
        selected = analysis_v1_one_click_selected_specs(payload, specs)
        selected_ids = {str(spec.get("id") or "") for spec in selected}
        if "02_01" in selected_ids:
            validate_analysis_v1_asr_authorization(analysis_payload)
            if {"04_01", "04_02", "04_03"} & selected_ids and workflow_profile != "person_talking_head_v1":
                validate_analysis_v1_run_prerequisites(task, normalize_analysis_v1_storyboard_mode(analysis_payload))
        with analysis_v1_one_click_lock:
            active_task_run_ids = sorted(
                run_id
                for run_id, owner_task_id in analysis_v1_active_one_click_runs.items()
                if owner_task_id == task_id
            )
            if active_task_run_ids:
                raise HTTPException(status_code=409, detail={
                    "code": "active_one_click_movie_exists",
                    "message": f"Task #{task_id} 已有一键成片任务运行中。",
                    "active_run_ids": active_task_run_ids,
                })
            run_id = str(now_ms())
            analysis_v1_active_one_click_runs[run_id] = task_id
            plan = analysis_v1_one_click_compile_plan(payload, task_id)
            attempt = repo.create_attempt(task_id=task_id, session_id=session_id, status="queued", prompt_version_id=int(task.get("current_prompt_version_id") or 0) or None, skill_version_id=None, run_model_provider=model["providerID"], run_model_id=model["modelID"], summary="口播一键成片排队中", created_at=now_ms())
            state = {
                "schema_version": "analysis_v1_koubo_one_click_movie_state_0.1",
                "task_id": task_id,
                "session_id": session_id,
                "run_id": run_id,
                "attempt_id": int(attempt["id"]),
                "target": ANALYSIS_V1_ONE_CLICK_TARGET,
                "status": "queued",
                "current_step_id": None,
                "steps": plan["steps"],
                "plan": {key: value for key, value in plan.items() if key != "steps"},
                "summary": "",
                "created_at": now_ms(),
                "task_url": frontend_task_url(request, task_id),
            }
            analysis_v1_one_click_write_state(workspace, state)
        add_session_event(session_id, "analysis_v1.one_click_movie.created", {"task_id": task_id, "run_id": run_id}, family="analysis_v1_one_click_movie", task_id=task_id, attempt_id=int(attempt["id"]), tool_id=ANALYSIS_V1_ONE_CLICK_TARGET)
        thread = threading.Thread(target=analysis_v1_one_click_background, kwargs={"task_id": task_id, "run_id": run_id, "attempt_id": int(attempt["id"]), "payload": payload, "model": model}, daemon=True)
        thread.start()
        return analysis_v1_one_click_status(task_id, run_id, workflow_profile=workflow_profile)

    @router.get("/api/openclip/tasks")
    async def list_openclip_tasks(request: Request) -> dict[str, Any]:
        role = request_role(request)
        return mask_for_role(role, SURFACE_ANALYSIS_V1_RUN, {"items": repo.list_task_summaries()})

    @router.get("/api/openclip/prompt-models")
    async def openclip_prompt_models(request: Request) -> dict[str, Any]:
        role = request_role(request)
        tasks = repo.list_task_summaries()
        if not tasks:
            return {"items": [], "default_model": {"providerID": "", "modelID": ""}}
        return mask_prompt_models(role, SURFACE_ANALYSIS_V1_PROMPT, serialize_prompt_models(safe_session(int(tasks[0]["session_id"]))))

    @router.post("/api/openclip/tasks")
    async def create_openclip_task(request: Request) -> dict[str, Any]:
        created = now_ms()
        session_id = ctx.session_repo.create(
            source=OPENCLIP_SOURCE,
            group_id=OPENCLIP_GROUP_ID,
            sender_name="OpenClip",
            title=ctx.next_session_title(),
            command_text="",
            status="queued",
            workspace_dir=str(ctx.workspace_store.sessions_root() / "pending" / str(created) / "workspace"),
            share_token=ctx.new_share_token(),
            created_at=created,
            updated_at=created,
        )
        workspace_dir = ctx.workspace_store.create_session_workspace(session_id)
        ctx.session_repo.update(session_id, workspace_dir=str(workspace_dir), updated_at=created)
        session_row = safe_session(session_id)
        client = opencode_client_for(session_row)
        op_session = client.create_session(str(session_row["title"]))
        ctx.session_repo.update(session_id, opencode_session_id=str(op_session["id"]), status="draft", updated_at=now_ms())
        default_task_fields = {
            "industry": "医美",
            "persona": "强判断老板型",
            "target_audience": "老板",
            "product_info": "",
            "constraints": "",
            "analysis_goal": "提取整体公式",
            "video_formula": "Hook/Trust/CTA",
        }
        task_id = repo.create_task(
            session_id=session_id,
            status="draft",
            workflow_mode="analysis_v1",
            reference_video_path="",
            industry=default_task_fields["industry"],
            persona=default_task_fields["persona"],
            target_audience=default_task_fields["target_audience"],
            product_info=default_task_fields["product_info"],
            constraints=default_task_fields["constraints"],
            analysis_goal=default_task_fields["analysis_goal"],
            video_formula=default_task_fields["video_formula"],
            simple_prompt=build_simple_prompt(default_task_fields),
            final_prompt="",
            rewrite_simple_prompt=build_simple_prompt(default_task_fields),
            rewrite_final_prompt="",
            storyboard_simple_prompt="",
            storyboard_final_prompt="",
            storyboard_quick_config_json=storyboard_quick_config_json({}),
            prompt_model_provider="",
            prompt_model_id="",
            run_model_provider="",
            run_model_id="",
            created_at=created,
            updated_at=created,
        )
        add_session_event(session_id, "openclip.task.created", {"task_id": task_id, "opencode_session_id": str(op_session["id"])})
        detail = serialize_task_detail(get_task(task_id))
        detail["task_url"] = frontend_task_url(request, task_id)
        role = request_role(request)
        detail["prompt_models"] = mask_prompt_models(role, SURFACE_ANALYSIS_V1_PROMPT, serialize_prompt_models(session_row))
        return mask_for_role(role, SURFACE_ANALYSIS_V1_PROMPT, detail)

    @router.get("/api/openclip/tasks/{task_id}")
    async def openclip_task_detail(request: Request, task_id: int) -> dict[str, Any]:
        role = request_role(request)
        task_row = get_task(task_id)
        detail = serialize_task_detail(task_row)
        try:
            detail["prompt_models"] = mask_prompt_models(role, SURFACE_ANALYSIS_V1_PROMPT, serialize_prompt_models(safe_session(int(task_row["session_id"]))))
        except Exception as exc:
            detail["prompt_models"] = {"items": [], "default_model": {"providerID": "", "modelID": ""}, "error": str(exc)}
        return mask_for_role(role, SURFACE_ANALYSIS_V1_PROMPT, detail)

    @router.delete("/api/openclip/tasks/{task_id}")
    async def delete_openclip_task(task_id: int) -> dict[str, Any]:
        task_row = get_task(task_id)
        session_id = int(task_row["session_id"])
        session_row = safe_session(session_id)
        ctx.workflow_deletion_service.delete_session_db_first(session_row)
        try:
            ctx.workflow_deletion_service.cleanup_workspace(session_row)
        except Exception as exc:
            ctx.event("warning", "cleanup", "Workspace cleanup failed after OpenClip DB deletion", {"session_id": session_id, "task_id": task_id, "error": str(exc)})
        return {"ok": True, "deleted_id": task_id, "deleted_session_id": session_id}

    @router.put("/api/openclip/tasks/{task_id}/config")
    async def save_openclip_task_config(request: Request, task_id: int, payload: OpenClipTaskUpdatePayload) -> dict[str, Any]:
        role = request_role(request)
        task_row = get_task(task_id)
        values = normalize_task_payload(task_id, payload)
        if role != "admin":
            session_row = safe_session(int(task_row["session_id"]))
            prompt_model, _ = resolve_model(session_row, values["prompt_model_provider"], values["prompt_model_id"], "Prompt", role, SURFACE_ANALYSIS_V1_PROMPT)
            run_model, _ = resolve_model(session_row, values["run_model_provider"], values["run_model_id"], "Analysis V1 run", role, SURFACE_ANALYSIS_V1_RUN)
            values["prompt_model_provider"] = prompt_model["providerID"]
            values["prompt_model_id"] = prompt_model["modelID"]
            values["run_model_provider"] = run_model["providerID"]
            values["run_model_id"] = run_model["modelID"]
        with analysis_v1_run_lock:
            reject_if_analysis_v1_active("已有 Analysis_V1 工具链运行中，暂不能保存提示配置。")
            repo.update_task(task_id, reference_video_path=values["reference_video_path"], industry=values["industry"], persona=values["persona"], target_audience=values["target_audience"], product_info=values["product_info"], constraints=values["constraints"], analysis_goal=values["analysis_goal"], video_formula=values["video_formula"], simple_prompt=values["simple_prompt"], final_prompt=values["final_prompt"], rewrite_simple_prompt=values["rewrite_simple_prompt"], rewrite_final_prompt=values["rewrite_final_prompt"], storyboard_simple_prompt=values["storyboard_simple_prompt"], storyboard_final_prompt=values["storyboard_final_prompt"], storyboard_quick_config_json=values["storyboard_quick_config_json"], prompt_model_provider=values["prompt_model_provider"], prompt_model_id=values["prompt_model_id"], run_model_provider=values["run_model_provider"], run_model_id=values["run_model_id"], updated_at=now_ms())
            variables_synced = sync_analysis_v1_variables_prompt_snapshot(get_task(task_id))
        add_session_event(int(task_row["session_id"]), "openclip.config.saved", {"task_id": task_id, "analysis_v1_variables_synced": variables_synced})
        return mask_for_role(role, SURFACE_ANALYSIS_V1_PROMPT, serialize_task_detail(get_task(task_id)))

    @router.post("/api/openclip/tasks/{task_id}/simple-prompt/rebuild")
    async def rebuild_openclip_simple_prompt(request: Request, task_id: int, payload: OpenClipTaskUpdatePayload) -> dict[str, Any]:
        role = request_role(request)
        task_row = get_task(task_id)
        simple_prompt = build_simple_prompt(task_row)
        repo.update_task(task_id, simple_prompt=simple_prompt, updated_at=now_ms())
        add_session_event(int(task_row["session_id"]), "openclip.simple_prompt.generated", {"task_id": task_id})
        return mask_for_role(role, SURFACE_ANALYSIS_V1_PROMPT, serialize_task_detail(get_task(task_id)))

    @router.post("/api/openclip/tasks/{task_id}/generate-prompt")
    async def generate_openclip_prompt(request: Request, task_id: int, payload: OpenClipPromptGeneratePayload) -> dict[str, Any]:
        role = request_role(request)
        task_row = get_task(task_id)
        reject_if_analysis_v1_active("已有 Analysis_V1 工具链运行中，暂不能生成或写入提示配置。")
        session_row = safe_session(int(task_row["session_id"]))
        spec = prompt_kind_spec(payload.prompt_kind)
        simple_prompt = str(task_row.get(spec["simple_field"]) or task_row.get("simple_prompt") or "").strip()
        if not simple_prompt:
            raise HTTPException(status_code=400, detail="Simple Prompt is required before generating Final Prompt")
        if spec["kind"] == "rewrite" and "任务模式：完整脚本创作" in simple_prompt:
            spec = {
                **spec,
                "system_prompt": FULL_SCRIPT_PROMPT_BUILDER_SYSTEM_PROMPT,
                "purpose": "analysis_v1.prompt_builder.generate_full_script_final_prompt",
            }
        model, prompt_models = resolve_model(session_row, payload.prompt_model_provider, payload.prompt_model_id, "Prompt", role, SURFACE_ANALYSIS_V1_PROMPT)
        client = opencode_client_for(session_row)
        auth_refreshed = False
        add_session_event(int(task_row["session_id"]), "user.message", {
            "text": simple_prompt,
            "model": model,
            "purpose": spec["purpose"],
            "task_id": task_id,
        })
        started_at = now_ms()
        try:
            client.prompt_async(str(session_row["opencode_session_id"]), simple_prompt, model=model, system=spec["system_prompt"])
        except OpenCodeAuthError as exc:
            auth_refreshed = True
            add_session_event(int(task_row["session_id"]), "openclip.opencode_auth_refreshed", {"task_id": task_id, "stage": "prompt_async", "detail": str(exc)})
            client = refresh_opencode_client_for(session_row, "openclip.generate_prompt.prompt_async_401")
            started_at = now_ms()
            try:
                client.prompt_async(str(session_row["opencode_session_id"]), simple_prompt, model=model, system=spec["system_prompt"])
            except OpenCodeAuthError as retry_exc:
                raise HTTPException(status_code=400, detail=str(retry_exc)) from retry_exc
        deadline = time.time() + 240
        assistant_text: str | None = None
        while time.time() < deadline:
            try:
                messages = client.messages(str(session_row["opencode_session_id"]), limit=120)
            except OpenCodeAuthError as exc:
                if auth_refreshed:
                    raise HTTPException(status_code=400, detail=str(exc)) from exc
                auth_refreshed = True
                add_session_event(int(task_row["session_id"]), "openclip.opencode_auth_refreshed", {"task_id": task_id, "stage": "messages", "detail": str(exc)})
                client = refresh_opencode_client_for(session_row, "openclip.generate_prompt.messages_401")
                try:
                    messages = client.messages(str(session_row["opencode_session_id"]), limit=120)
                except OpenCodeAuthError as retry_exc:
                    raise HTTPException(status_code=400, detail=str(retry_exc)) from retry_exc
            assistant_text = last_completed_assistant(messages, started_at)
            if assistant_text:
                break
            await asyncio.sleep(1)
        if not assistant_text:
            raise HTTPException(status_code=400, detail="OpenCode timed out before returning the generated final prompt")
        update_values = {
            spec["final_field"]: assistant_text.strip(),
            "prompt_model_provider": model["providerID"],
            "prompt_model_id": model["modelID"],
            "updated_at": now_ms(),
        }
        if spec["kind"] == "rewrite":
            update_values["final_prompt"] = assistant_text.strip()
        with analysis_v1_run_lock:
            reject_if_analysis_v1_active("已有 Analysis_V1 工具链运行中，暂不能写入生成的提示配置。")
            if str(task_row.get("workflow_mode") or "").strip() == "person_talking_head_v1":
                config_row = repo.get_talking_head_config(task_id) or {}
                try:
                    talking_head_config = json.loads(str(config_row.get("config_json") or "{}"))
                except Exception:
                    talking_head_config = {}
                talking_head_config = talking_head_config if isinstance(talking_head_config, dict) else {}
                script_prompt = talking_head_config.get("script_prompt") if isinstance(talking_head_config.get("script_prompt"), dict) else {}
                if spec["kind"] == "rewrite":
                    script_prompt["final_prompt"] = assistant_text.strip()
                script_prompt["model_provider"] = model["providerID"]
                script_prompt["model_id"] = model["modelID"]
                talking_head_config["script_prompt"] = script_prompt
                repo.update_talking_head_task(
                    task_id,
                    config_schema_version=str(config_row.get("schema_version") or "talking_head_task_config_1.0"),
                    script_creation_mode=str(config_row.get("script_creation_mode") or "user_provided"),
                    config_json=json.dumps(talking_head_config, ensure_ascii=False, sort_keys=True),
                    config_created_at=int(config_row.get("created_at") or now_ms()),
                    config_updated_at=now_ms(),
                    **update_values,
                )
                variables_synced = False
            else:
                repo.update_task(task_id, **update_values)
                latest_task = get_task(task_id)
                variables_synced = sync_analysis_v1_variables_prompt_snapshot(latest_task)
        add_session_event(int(task_row["session_id"]), "assistant.final", {
            "text": assistant_text.strip(),
            "model": model,
            "purpose": spec["purpose"],
            "task_id": task_id,
        })
        local_usage = record_storyboard_usage(
            ctx,
            task_row,
            request_id=stable_usage_request_id("openclip_prompt", task_id, spec["kind"], started_at, model["providerID"], model["modelID"]),
            provider=model["providerID"],
            model_id=model["modelID"],
            modality="chat",
            step_id=f"openclip.prompt.{spec['kind']}",
            units=chat_usage_units(input_text=simple_prompt, output_text=assistant_text, system_text=spec["system_prompt"]),
            started_at=started_at,
            finished_at=now_ms(),
        )
        add_session_event(int(task_row["session_id"]), "openclip.prompt.generated", {"task_id": task_id, "provider": model["providerID"], "model": model["modelID"], "prompt_kind": spec["kind"], "analysis_v1_variables_synced": variables_synced, "local_usage": local_usage, "local_usage_id": local_usage.get("local_usage_id", "")})
        detail = serialize_task_detail(get_task(task_id))
        detail["prompt_models"] = prompt_models
        detail["local_usage"] = local_usage
        detail["local_usage_id"] = local_usage.get("local_usage_id", "")
        return mask_for_role(role, SURFACE_ANALYSIS_V1_PROMPT, detail)

    def analysis_v1_reference_audio_upload_dir(workspace: Path) -> Path:
        upload_dir = workspace / "S5_03_01_TTSBuilderG" / "Working" / "manual_reference"
        upload_dir.mkdir(parents=True, exist_ok=True)
        return upload_dir

    def analysis_v1_reference_audio_suffix(filename: str, content_type: str = "") -> str:
        audio_suffixes = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".webm"}
        video_suffixes = {".mp4", ".mov", ".m4v"}
        allowed_suffixes = audio_suffixes | video_suffixes
        suffix = Path(filename or "").suffix.lower()
        if suffix not in allowed_suffixes:
            mime = str(content_type or "").split(";", 1)[0].strip().lower()
            guessed = (mimetypes.guess_extension(mime) or "").lower()
            suffix = {
                ".wave": ".wav",
                ".x-wav": ".wav",
                ".mpga": ".mp3",
                ".oga": ".ogg",
                ".qt": ".mov",
            }.get(guessed, guessed)
        if suffix not in allowed_suffixes:
            raise HTTPException(status_code=400, detail="参考声音支持 WAV/MP3/M4A/AAC/FLAC/OGG/Opus，也支持 MP4/MOV/WebM 视频自动提取音频。")
        return suffix

    async def write_analysis_v1_reference_upload(file: UploadFile, source_path: Path) -> int:
        bytes_written = 0
        try:
            with source_path.open("wb") as fh:
                while True:
                    chunk = await file.read(1024 * 1024)
                    if not chunk:
                        break
                    bytes_written += len(chunk)
                    fh.write(chunk)
            if bytes_written <= 0:
                raise HTTPException(status_code=400, detail="Uploaded reference audio is empty")
            return bytes_written
        except Exception:
            source_path.unlink(missing_ok=True)
            raise

    def merge_analysis_v1_reference_audio_chunks(chunk_dir: Path, total_chunks: int, source_path: Path, total_size: int) -> int:
        merged_bytes = 0
        with source_path.open("wb") as output:
            for index in range(total_chunks):
                part = chunk_dir / f"chunk_{index:06d}.part"
                if not part.is_file():
                    raise HTTPException(status_code=400, detail=f"Reference audio upload chunk {index + 1}/{total_chunks} is missing")
                with part.open("rb") as input_file:
                    while True:
                        data = input_file.read(1024 * 1024)
                        if not data:
                            break
                        merged_bytes += len(data)
                        output.write(data)
        if merged_bytes != total_size:
            raise HTTPException(status_code=400, detail=f"Reference audio upload size mismatch: expected {total_size}, got {merged_bytes}")
        return merged_bytes

    async def finalize_analysis_v1_reference_audio(task_id: int, session_id: int, workspace: Path, source_path: Path, suffix: str, filename: str, bytes_written: int) -> dict[str, Any]:
        output_path = workspace / "SessionOutput" / "Audio_Reference.wav"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temp_output: Path | None = None
        try:
            if bytes_written <= 0:
                raise HTTPException(status_code=400, detail="Uploaded reference audio is empty")
            if suffix != ".wav":
                ffmpeg = analysis_v1_ffmpeg_binary()
                temp_output = output_path.with_name(f".{output_path.name}.{uuid.uuid4().hex[:8]}.tmp.wav")
                try:
                    result = await asyncio.to_thread(subprocess.run, [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(source_path), "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", str(temp_output)], check=False, capture_output=True, text=True, timeout=180)
                except subprocess.TimeoutExpired as exc:
                    raise HTTPException(status_code=504, detail="参考声音上传成功，但抽取音频超时。请先把视频导出为 WAV/MP3 后上传。") from exc
                if result.returncode != 0:
                    detail = (result.stderr or result.stdout or "Reference audio conversion failed")[-1200:]
                    raise HTTPException(status_code=400, detail=f"无法从上传文件提取参考声音。请确认文件包含可播放音频轨道，或先导出 WAV/MP3 后上传。{detail}")
                temp_output.replace(output_path)
            else:
                source_path.replace(output_path)
            add_session_event(session_id, "analysis_v1.tts.reference_audio.uploaded", {"task_id": task_id, "session_id": session_id, "path": "SessionOutput/Audio_Reference.wav", "filename": filename or output_path.name, "bytes": bytes_written})
            return {"ok": True, "task_id": task_id, "session_id": session_id, "path": "SessionOutput/Audio_Reference.wav", "abs_path": str(output_path), "filename": filename or output_path.name, "bytes": bytes_written}
        except Exception:
            source_path.unlink(missing_ok=True)
            if temp_output is not None:
                temp_output.unlink(missing_ok=True)
            raise
        finally:
            if suffix != ".wav":
                source_path.unlink(missing_ok=True)
            if temp_output is not None and temp_output.exists():
                temp_output.unlink(missing_ok=True)

    @router.post("/api/openclip/tasks/{task_id}/analysis-v1/tts/reference-audio")
    async def upload_analysis_v1_tts_reference_audio(task_id: int, file: UploadFile = File(...)) -> dict[str, Any]:
        task_row = get_task(task_id)
        session_id = int(task_row["session_id"])
        workspace = analysis_v1_workspace(task_row)
        upload_dir = analysis_v1_reference_audio_upload_dir(workspace)
        suffix = analysis_v1_reference_audio_suffix(file.filename or "", file.content_type or "")
        source_path = upload_dir / f"upload_{now_ms()}_{uuid.uuid4().hex[:8]}{suffix}"
        bytes_written = await write_analysis_v1_reference_upload(file, source_path)
        return await finalize_analysis_v1_reference_audio(task_id, session_id, workspace, source_path, suffix, file.filename or source_path.name, bytes_written)

    @router.post("/api/openclip/tasks/{task_id}/analysis-v1/tts/reference-audio/chunk")
    async def upload_analysis_v1_tts_reference_audio_chunk(
        task_id: int,
        upload_id: str = Form(...),
        chunk_index: int = Form(...),
        total_chunks: int = Form(...),
        filename: str = Form(...),
        total_size: int = Form(...),
        content_type: str = Form(default=""),
        file: UploadFile = File(...),
    ) -> dict[str, Any]:
        task_row = get_task(task_id)
        session_id = int(task_row["session_id"])
        workspace = analysis_v1_workspace(task_row)
        upload_id_value = str(upload_id or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,96}", upload_id_value):
            raise HTTPException(status_code=400, detail="Invalid reference audio upload id")
        if total_chunks < 1 or total_chunks > 2048:
            raise HTTPException(status_code=400, detail="Invalid reference audio chunk count")
        if chunk_index < 0 or chunk_index >= total_chunks:
            raise HTTPException(status_code=400, detail="Invalid reference audio chunk index")
        if total_size < 1:
            raise HTTPException(status_code=400, detail="Invalid reference audio upload size")
        suffix = analysis_v1_reference_audio_suffix(filename, content_type or file.content_type or "")
        upload_dir = analysis_v1_reference_audio_upload_dir(workspace)
        chunk_dir = upload_dir / "chunks" / upload_id_value
        chunk_dir.mkdir(parents=True, exist_ok=True)
        chunk_path = chunk_dir / f"chunk_{chunk_index:06d}.part"
        bytes_written = await write_analysis_v1_reference_upload(file, chunk_path)
        received_bytes = sum(path.stat().st_size for path in chunk_dir.glob("chunk_*.part") if path.is_file())
        if chunk_index != total_chunks - 1:
            return {"ok": True, "task_id": task_id, "session_id": session_id, "upload_id": upload_id_value, "chunk_index": chunk_index, "total_chunks": total_chunks, "bytes": bytes_written, "received_bytes": received_bytes, "complete": False}
        source_path = upload_dir / f"upload_{now_ms()}_{uuid.uuid4().hex[:8]}{suffix}"
        try:
            merged_bytes = await asyncio.to_thread(merge_analysis_v1_reference_audio_chunks, chunk_dir, total_chunks, source_path, total_size)
            return await finalize_analysis_v1_reference_audio(task_id, session_id, workspace, source_path, suffix, filename, merged_bytes)
        except Exception:
            source_path.unlink(missing_ok=True)
            raise
        finally:
            shutil.rmtree(chunk_dir, ignore_errors=True)

    @router.get("/api/openclip/tasks/{task_id}/analysis-v1/voice-catalog/{model}/audio/{audio_path:path}")
    async def analysis_v1_voice_catalog_audio(task_id: int, model: str, audio_path: str) -> FileResponse:
        get_task(task_id)
        model_name = str(model or "").strip()
        raw_audio_path = str(audio_path or "").strip().replace("\\", "/")
        if not model_name or "/" in model_name or "\\" in model_name or model_name in {".", ".."}:
            raise HTTPException(status_code=400, detail="Invalid voice catalog model")
        if not raw_audio_path:
            raise HTTPException(status_code=400, detail="Voice catalog audio path is required")
        requested = Path(raw_audio_path)
        if requested.is_absolute() or any(part in {"", ".", ".."} for part in requested.parts):
            raise HTTPException(status_code=400, detail="Invalid voice catalog audio path")
        catalog_root = (ANALYSIS_V1_ROOT / "VoiceCatalog" / model_name).resolve()
        target = (catalog_root / requested).resolve()
        try:
            target.relative_to(catalog_root)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Voice catalog audio path escapes catalog root") from exc
        if not target.is_file():
            raise HTTPException(status_code=404, detail="Voice catalog audio not found")
        media_type = mimetypes.guess_type(str(target))[0] or "audio/wav"
        return FileResponse(
            target,
            media_type=media_type,
            filename=target.name,
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )

    @router.post("/api/openclip/tasks/{task_id}/analysis-v1/tts/builder-g")
    async def run_analysis_v1_tts_builder_g(task_id: int, payload: OpenClipTTSBuilderPayload) -> dict[str, Any]:
        task_row = get_task(task_id)
        session_id = int(task_row["session_id"])
        workspace = analysis_v1_workspace(task_row)
        add_session_event(session_id, "analysis_v1.tts.builder_g.started", {"task_id": task_id, "session_id": session_id, "tool_path": str(ANALYSIS_V1_TTS_BUILDER_G), "reference_start": payload.reference_start, "reference_duration": payload.reference_duration, "force": payload.force})
        result = await asyncio.to_thread(run_analysis_v1_builder_g, workspace, payload)
        add_session_event(session_id, "analysis_v1.tts.builder_g.completed", {"task_id": task_id, "session_id": session_id, "tool_path": result.get("tool_path"), "status": result.get("status"), "outputs": result.get("outputs")})
        return {"ok": result.get("status") != "failed", "task_id": task_id, "session_id": session_id, "result": result}

    @router.post("/api/openclip/tasks/{task_id}/analysis-v1/tts/quick-adv/state")
    async def get_analysis_v1_tts_quick_adv_state(task_id: int, payload: OpenClipTTSQuickAdvPayload | None = None) -> dict[str, Any]:
        task_row = get_task(task_id)
        session_id = int(task_row["session_id"])
        workspace = analysis_v1_workspace(task_row)
        request_payload = payload or OpenClipTTSQuickAdvPayload(task_id=task_id)
        result = await asyncio.to_thread(run_analysis_v1_quick_adv_command, workspace, "state", request_payload)
        clone_config = load_config(ctx, "voice-clone")
        filter_analysis_v1_quick_adv_clones(result, str(clone_config.get("active_provider") or ""))
        return {"ok": result.get("ok") is not False, "task_id": task_id, "session_id": session_id, "result": result}

    @router.post("/api/openclip/tasks/{task_id}/analysis-v1/tts/quick-adv/catalog-list")
    async def list_analysis_v1_tts_quick_adv_catalog(task_id: int, payload: OpenClipTTSQuickAdvPayload) -> dict[str, Any]:
        task_row = get_task(task_id)
        session_id = int(task_row["session_id"])
        workspace = analysis_v1_workspace(task_row)
        result = await asyncio.to_thread(run_analysis_v1_quick_adv_command, workspace, "catalog-list", payload)
        add_session_event(session_id, "analysis_v1.tts.quick_adv.catalog_list", {"task_id": task_id, "session_id": session_id, "status": result.get("status"), "count": result.get("count")})
        return {"ok": result.get("ok") is not False, "task_id": task_id, "session_id": session_id, "result": result}

    @router.post("/api/openclip/tasks/{task_id}/analysis-v1/tts/quick-adv/sample-reference")
    async def sample_analysis_v1_tts_quick_adv_reference(task_id: int, payload: OpenClipTTSQuickAdvPayload) -> dict[str, Any]:
        task_row = get_task(task_id)
        session_id = int(task_row["session_id"])
        workspace = analysis_v1_workspace(task_row)
        result = await asyncio.to_thread(run_analysis_v1_quick_adv_command, workspace, "sample-reference", payload)
        add_session_event(session_id, "analysis_v1.tts.quick_adv.sample_reference", {"task_id": task_id, "session_id": session_id, "status": result.get("status"), "ok": result.get("ok"), "reference_start": payload.reference_start, "reference_duration": payload.reference_duration})
        return {"ok": result.get("ok") is not False, "task_id": task_id, "session_id": session_id, "result": result}

    @router.post("/api/openclip/tasks/{task_id}/analysis-v1/tts/quick-adv/rank")
    async def rank_analysis_v1_tts_quick_adv(task_id: int, payload: OpenClipTTSQuickAdvPayload) -> dict[str, Any]:
        task_row = get_task(task_id)
        session_id = int(task_row["session_id"])
        workspace = analysis_v1_workspace(task_row)
        result = await asyncio.to_thread(run_analysis_v1_quick_adv_command, workspace, "rank", payload)
        add_session_event(session_id, "analysis_v1.tts.quick_adv.rank", {"task_id": task_id, "session_id": session_id, "status": result.get("status"), "ok": result.get("ok"), "scoring_mode": result.get("scoring_mode"), "recommended_count": len(result.get("recommended") or [])})
        return {"ok": result.get("ok") is not False, "task_id": task_id, "session_id": session_id, "result": result}

    @router.post("/api/openclip/tasks/{task_id}/analysis-v1/tts/quick-adv/clone-voice")
    async def clone_analysis_v1_tts_quick_adv_voice(task_id: int, payload: OpenClipTTSQuickAdvPayload) -> dict[str, Any]:
        task_row = get_task(task_id)
        session_id = int(task_row["session_id"])
        workspace = analysis_v1_workspace(task_row)
        result = await asyncio.to_thread(run_analysis_v1_quick_adv_command, workspace, "clone-voice", payload)
        add_session_event(session_id, "analysis_v1.tts.quick_adv.clone_voice", {"task_id": task_id, "session_id": session_id, "status": result.get("status"), "ok": result.get("ok"), "provider": result.get("provider"), "target_model": result.get("target_model"), "voice_id": result.get("voice_id"), "reused_existing": result.get("reused_existing")})
        return {"ok": result.get("ok") is not False, "task_id": task_id, "session_id": session_id, "result": result}

    @router.post("/api/openclip/tasks/{task_id}/analysis-v1/tts/quick-adv/clone-list")
    async def list_analysis_v1_tts_quick_adv_clones(task_id: int, payload: OpenClipTTSQuickAdvPayload) -> dict[str, Any]:
        task_row = get_task(task_id)
        session_id = int(task_row["session_id"])
        workspace = analysis_v1_workspace(task_row)
        result = await asyncio.to_thread(run_analysis_v1_quick_adv_command, workspace, "clone-list", payload)
        mark_analysis_v1_cloud_clone_task_membership(workspace, result)
        add_session_event(session_id, "analysis_v1.tts.quick_adv.clone_list", {"task_id": task_id, "session_id": session_id, "status": result.get("status"), "ok": result.get("ok"), "provider": result.get("provider"), "count": result.get("count")})
        return {"ok": result.get("ok") is not False, "task_id": task_id, "session_id": session_id, "result": result}

    @router.post("/api/openclip/analysis-v1/tts/quick-adv/clone-list")
    async def list_analysis_v1_tts_quick_adv_clones_without_task(payload: OpenClipTTSQuickAdvPayload | None = None) -> dict[str, Any]:
        request_payload = payload or OpenClipTTSQuickAdvPayload()
        result = await asyncio.to_thread(run_analysis_v1_quick_adv_command, system_voice_clone_workspace(), "clone-list", request_payload)
        return {"ok": result.get("ok") is not False, "result": result}

    @router.post("/api/openclip/analysis-v1/tts/quick-adv/clone-delete")
    async def delete_analysis_v1_tts_quick_adv_clone_without_task(payload: OpenClipTTSQuickAdvPayload) -> dict[str, Any]:
        voice_target = resolve_tts_voice_alias(ctx, payload.clone_voice_id)
        if str(payload.clone_voice_id or "").strip().startswith(PUBLIC_TTS_VOICE_PREFIX) and not voice_target:
            raise HTTPException(status_code=400, detail="Select a valid cloud voice before deleting.")
        command_payload, target_provider, target_model = resolve_analysis_v1_clone_delete_payload(payload, voice_target)
        result = await asyncio.to_thread(
            run_analysis_v1_quick_adv_command,
            system_voice_clone_workspace(),
            "clone-delete",
            command_payload,
            clone_provider_override=target_provider,
            clone_model_override=target_model,
        )
        return {"ok": result.get("ok") is not False, "result": result}

    @router.post("/api/openclip/analysis-v1/tts/clone-preview")
    async def preview_analysis_v1_clone_tts_without_task(payload: OpenClipTTSPreviewPayload) -> dict[str, Any]:
        voice_target = resolve_tts_voice_alias(ctx, payload.voice_id)
        if str(payload.voice_id or "").strip().startswith(PUBLIC_TTS_VOICE_PREFIX) and not voice_target:
            raise HTTPException(status_code=400, detail="Select a valid cloud voice before previewing.")
        clone_preview_providers = {"heygen", "cosyvoice", "minimax"}
        provider = normalize_analysis_v1_clone_provider((voice_target or {}).get("provider") or payload.source_clone_provider)
        payload_provider = normalize_analysis_v1_clone_provider(payload.provider)
        fallback_clone_config: dict[str, Any] = {}
        if not provider and payload_provider in clone_preview_providers:
            provider = payload_provider
        if not provider:
            fallback_clone_config = analysis_v1_voice_clone_config()
            provider = normalize_analysis_v1_clone_provider(fallback_clone_config.get("provider"))
        model = analysis_v1_clone_model_from_provider(
            provider,
            (voice_target or {}).get("model")
            or payload.target_model
            or (analysis_v1_clone_payload_model(payload) if payload_provider in clone_preview_providers else "")
            or fallback_clone_config.get("model"),
        )
        voice_id = str((voice_target or {}).get("voice_id") or payload.voice_id or "").strip()
        prompt = (payload.prompt or payload.text or "").strip()
        language = (payload.language or "zh").strip() or "zh"
        tempo = float(payload.tempo or 1.0)
        if tempo <= 0:
            raise HTTPException(status_code=400, detail="tempo must be greater than 0")
        if provider not in clone_preview_providers:
            raise HTTPException(status_code=400, detail=f"Clone preview supports HeyGen, CosyVoice, and MiniMax: {provider}")
        if not voice_id:
            raise HTTPException(status_code=400, detail="voice_id is required")
        if not prompt:
            raise HTTPException(status_code=400, detail="prompt is required")
        config = analysis_v1_voice_clone_tts_config(provider, model)
        sample_text = extract_analysis_v1_tts_preview_text(payload.text, prompt) or prompt
        safe_candidate = re.sub(r"[^a-zA-Z0-9_-]+", "_", payload.candidate_id or voice_id)[:40] or "preview"
        cache_signature = hashlib.sha256(json.dumps({
            "provider": provider,
            "model": config["model"],
            "voice_id": voice_id,
            "text": sample_text,
            "language": language,
            "tempo": round(tempo, 4),
        }, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        preview_dir = system_voice_clone_workspace() / "previews"
        raw_output_path = preview_dir / f"{safe_candidate}_{cache_signature[:16]}.raw.wav"
        output_path = preview_dir / f"{safe_candidate}_{cache_signature[:16]}.wav"
        preview_dir.mkdir(parents=True, exist_ok=True)
        if not output_path.exists() or output_path.stat().st_size <= 0:
            if not raw_output_path.exists() or raw_output_path.stat().st_size <= 0:
                if provider == "heygen":
                    audio_url = await asyncio.to_thread(heygen_tts_preview_url, config["api_key"], voice_id, sample_text, language, 1.0)
                elif provider == "minimax":
                    audio_url = await asyncio.to_thread(minimax_tts_preview_url, config["api_key"], voice_id, sample_text, language, 1.0, config.get("extra") or {})
                else:
                    complex_prompt = strip_analysis_v1_tts_preview_instruction(prompt)
                    audio_url = await asyncio.to_thread(dashscope_tts_preview_url, config["api_key"], provider, config["model"], voice_id, sample_text, complex_prompt, language, "direct")
                audio_data, mime_type = await asyncio.to_thread(analysis_v1_tts_audio_url_bytes, audio_url)
                await asyncio.to_thread(write_analysis_v1_tts_audio_bytes, audio_data, mime_type, raw_output_path)
            await asyncio.to_thread(apply_analysis_v1_tts_tempo, raw_output_path, output_path, tempo)
        data = await asyncio.to_thread(output_path.read_bytes)
        duration_seconds = analysis_v1_audio_duration_seconds(output_path)
        return {
            "ok": True,
            "provider": provider,
            "model": config["model"],
            "voice_id": voice_id,
            "tempo": round(tempo, 4),
            "duration_seconds": duration_seconds,
            "audio_url": f"data:audio/wav;base64,{base64.b64encode(data).decode('ascii')}",
        }

    @router.post("/api/openclip/tasks/{task_id}/analysis-v1/tts/quick-adv/clone-import")
    async def import_analysis_v1_tts_quick_adv_clone(task_id: int, payload: OpenClipTTSQuickAdvPayload) -> dict[str, Any]:
        task_row = get_task(task_id)
        session_id = int(task_row["session_id"])
        workspace = analysis_v1_workspace(task_row)
        voice_target = resolve_tts_voice_alias(ctx, payload.clone_voice_id)
        if str(payload.clone_voice_id or "").strip().startswith(PUBLIC_TTS_VOICE_PREFIX) and not voice_target:
            raise HTTPException(status_code=400, detail="Select a valid cloud voice before importing.")
        command_payload = payload.model_copy(update={"clone_voice_id": (voice_target or {}).get("voice_id") or payload.clone_voice_id})
        result = await asyncio.to_thread(run_analysis_v1_quick_adv_command, workspace, "clone-import", command_payload)
        add_session_event(session_id, "analysis_v1.tts.quick_adv.clone_import", {"task_id": task_id, "session_id": session_id, "status": result.get("status"), "ok": result.get("ok"), "provider": result.get("provider"), "voice_id": result.get("voice_id") or str(payload.clone_voice_id or ""), "reused_existing": result.get("reused_existing")})
        return {"ok": result.get("ok") is not False, "task_id": task_id, "session_id": session_id, "result": result}

    @router.post("/api/openclip/tasks/{task_id}/analysis-v1/tts/quick-adv/clone-delete")
    async def delete_analysis_v1_tts_quick_adv_clone(task_id: int, payload: OpenClipTTSQuickAdvPayload) -> dict[str, Any]:
        task_row = get_task(task_id)
        session_id = int(task_row["session_id"])
        workspace = analysis_v1_workspace(task_row)
        voice_target = resolve_tts_voice_alias(ctx, payload.clone_voice_id)
        if str(payload.clone_voice_id or "").strip().startswith(PUBLIC_TTS_VOICE_PREFIX) and not voice_target:
            raise HTTPException(status_code=400, detail="Select a valid cloud voice before deleting.")
        command_payload, target_provider, target_model = resolve_analysis_v1_clone_delete_payload(payload, voice_target)
        result = await asyncio.to_thread(
            run_analysis_v1_quick_adv_command,
            workspace,
            "clone-delete",
            command_payload,
            clone_provider_override=target_provider,
            clone_model_override=target_model,
        )
        add_session_event(session_id, "analysis_v1.tts.quick_adv.clone_delete", {"task_id": task_id, "session_id": session_id, "status": result.get("status"), "ok": result.get("ok"), "provider": result.get("provider"), "voice_id": result.get("voice_id") or str(payload.clone_voice_id or ""), "already_deleted": result.get("already_deleted"), "blocked_reasons": result.get("blocked_reasons")})
        return {"ok": result.get("ok") is not False, "task_id": task_id, "session_id": session_id, "result": result}

    @router.post("/api/openclip/tasks/{task_id}/analysis-v1/tts/selection")
    async def save_analysis_v1_tts_selection(task_id: int, payload: OpenClipTTSSelectionPayload) -> dict[str, Any]:
        task_row = get_task(task_id)
        session_id = int(task_row["session_id"])
        alias_voice_id = str(payload.voice_id or payload.voice or payload.candidate.get("voice_id") or payload.candidate.get("voice") or "").strip()
        voice_target = resolve_tts_voice_alias(ctx, alias_voice_id)
        if alias_voice_id.startswith(PUBLIC_TTS_VOICE_PREFIX) and not voice_target:
            raise HTTPException(status_code=400, detail="Select a valid cloud voice before saving.")
        payload_candidate = dict(payload.candidate) if isinstance(payload.candidate, dict) else {}
        updates: dict[str, Any] = {"task_id": task_id}
        if voice_target:
            real_voice_id = voice_target["voice_id"]
            for key in ("voice", "voice_id", "voice_clone_id"):
                if str(payload_candidate.get(key) or "").strip():
                    payload_candidate[key] = real_voice_id
            if voice_target.get("candidate_id"):
                payload_candidate["candidate_id"] = voice_target["candidate_id"]
            if voice_target.get("provider"):
                payload_candidate["provider"] = voice_target["provider"]
            if voice_target.get("model"):
                payload_candidate["model"] = voice_target["model"]
                payload_candidate["target_model"] = voice_target["model"]
            updates.update({
                "voice": real_voice_id,
                "voice_id": real_voice_id,
                "provider": voice_target.get("provider") or payload.provider,
                "model": voice_target.get("model") or payload.model,
                "candidate_id": voice_target.get("candidate_id") or payload.candidate_id,
                "candidate": payload_candidate,
            })
        request_payload = payload.model_copy(update=updates)
        result = await asyncio.to_thread(save_analysis_v1_tts_selection_to_variables, task_row, request_payload)
        add_session_event(session_id, "analysis_v1.tts.selection.saved", {"task_id": task_id, "session_id": session_id, "candidate_id": result["selection"].get("candidate_id"), "voice_id": result["selection"].get("voice_id"), "provider": result["selection"].get("provider"), "model": result["selection"].get("model"), "variables_path": result.get("variables_path")})
        return {"ok": True, "task_id": task_id, "session_id": session_id, **result}

    @router.post("/api/openclip/tasks/{task_id}/analysis-v1/tts/preview")
    async def preview_analysis_v1_tts(task_id: int, payload: OpenClipTTSPreviewPayload) -> dict[str, Any]:
        task_row = get_task(task_id)
        session_id = int(task_row["session_id"])
        workspace = analysis_v1_workspace(task_row)
        voice_target = resolve_tts_voice_alias(ctx, payload.voice_id)
        if str(payload.voice_id or "").strip().startswith(PUBLIC_TTS_VOICE_PREFIX) and not voice_target:
            raise HTTPException(status_code=400, detail="Select a valid cloud voice before previewing.")
        if voice_target:
            payload = payload.model_copy(update={
                "voice_id": voice_target["voice_id"],
                "provider": voice_target.get("provider") or payload.provider,
                "model": voice_target.get("model") or payload.model,
                "target_model": voice_target.get("model") or payload.target_model,
                "candidate_id": voice_target.get("candidate_id") or payload.candidate_id,
                "source_clone_provider": voice_target.get("provider") or payload.source_clone_provider,
                "voice_source": "cloud_clone",
            })
        clone_defaults = analysis_v1_cloud_clone_preview_defaults(workspace, payload)
        if clone_defaults:
            provider = clone_defaults.get("provider") or ""
            model = clone_defaults.get("model") or ""
        else:
            provider, model = resolve_tts_public_alias(ctx, payload.provider or "", payload.model or "")
        provider = normalize_analysis_v1_clone_provider(provider) or provider
        if provider in {"cosyvoice", "heygen", "minimax"}:
            model = analysis_v1_clone_model_from_provider(provider, model)
        voice_id = (payload.voice_id or "").strip()
        prompt = (payload.prompt or payload.text or "").strip()
        language = (payload.language or "zh").strip() or "zh"
        tempo = float(payload.tempo or 1.0)
        if tempo <= 0:
            raise HTTPException(status_code=400, detail="tempo must be greater than 0")
        if provider == "gemini":
            provider = "google"
        if provider not in {"google", "qwen", "cosyvoice", "bytedance", "heygen", "minimax"}:
            raise HTTPException(status_code=400, detail=f"Analysis V1 TTS preview currently supports Google/Gemini, Qwen, CosyVoice, ByteDance, HeyGen, and MiniMax: {provider}")
        if not voice_id:
            raise HTTPException(status_code=400, detail="voice_id is required")
        if not prompt:
            raise HTTPException(status_code=400, detail="prompt is required")
        config = analysis_v1_voice_clone_tts_config(provider, model) if provider in {"cosyvoice", "heygen", "minimax"} else analysis_v1_tts_config(provider, model)
        safe_candidate = re.sub(r"[^a-zA-Z0-9_-]+", "_", payload.candidate_id or voice_id)[:40] or "preview"
        cache_signature = hashlib.sha256(json.dumps({
            "provider": provider,
            "model": config["model"],
            "voice_id": voice_id,
            "text": payload.text or "",
            "prompt": prompt,
            "candidate_id": payload.candidate_id or "",
            "language": language,
            "tempo": round(tempo, 4),
        }, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        preview_id = f"{safe_candidate}_{cache_signature[:16]}"
        output_rel = f"SessionOutput/tts/previews/{preview_id}.wav"
        raw_output_rel = f"SessionOutput/tts/previews/{preview_id}.raw.wav" if abs(tempo - 1.0) >= 0.0001 else output_rel
        output_path = workspace / output_rel
        raw_output_path = workspace / raw_output_rel
        if output_path.exists() and output_path.stat().st_size > 0:
            add_session_event(session_id, "analysis_v1.tts.preview.cached", {"task_id": task_id, "session_id": session_id, "provider": provider, "model": config["model"], "voice_id": voice_id, "tempo": tempo, "output": output_rel, "raw_output": raw_output_rel})
            return {"ok": True, "cached": True, "task_id": task_id, "session_id": session_id, "provider": provider, "model": config["model"], "voice_id": voice_id, "tempo": round(tempo, 4), "stretched": abs(tempo - 1.0) >= 0.0001, "output": output_rel, "raw_output": raw_output_rel}
        if raw_output_path.exists() and raw_output_path.stat().st_size > 0:
            tempo_meta = await asyncio.to_thread(apply_analysis_v1_tts_tempo, raw_output_path, output_path, tempo)
            add_session_event(session_id, "analysis_v1.tts.preview.cached", {"task_id": task_id, "session_id": session_id, "provider": provider, "model": config["model"], "voice_id": voice_id, "tempo": tempo_meta.get("tempo"), "output": output_rel, "raw_output": raw_output_rel})
            return {"ok": True, "cached": True, "task_id": task_id, "session_id": session_id, "provider": provider, "model": config["model"], "voice_id": voice_id, "tempo": tempo_meta.get("tempo"), "stretched": tempo_meta.get("stretched"), "output": output_rel, "raw_output": raw_output_rel}
        if payload.cache_only:
            raise HTTPException(status_code=404, detail="TTS preview cache is not ready")
        add_session_event(session_id, "analysis_v1.tts.preview.started", {"task_id": task_id, "session_id": session_id, "provider": provider, "model": config["model"], "voice_id": voice_id, "candidate_id": payload.candidate_id or voice_id, "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(), "prompt_excerpt": prompt[:120], "tempo": tempo, "output": output_rel, "raw_output": raw_output_rel})
        sample_text_for_usage = extract_analysis_v1_tts_preview_text(payload.text, prompt) or prompt
        if provider in {"qwen", "cosyvoice"}:
            sample_text = extract_analysis_v1_tts_preview_text(payload.text, prompt)
            sample_text_for_usage = sample_text or sample_text_for_usage
            complex_prompt = strip_analysis_v1_tts_preview_instruction(prompt) if "instruct" in config["model"] else ""
            if provider == "cosyvoice":
                complex_prompt = strip_analysis_v1_tts_preview_instruction(prompt)
            audio_url = await asyncio.to_thread(dashscope_tts_preview_url, config["api_key"], provider, config["model"], voice_id, sample_text, complex_prompt, language, "direct")
            audio_data, mime_type = await asyncio.to_thread(analysis_v1_tts_audio_url_bytes, audio_url)
            await asyncio.to_thread(write_analysis_v1_tts_audio_bytes, audio_data, mime_type, raw_output_path)
        elif provider == "heygen":
            sample_text = extract_analysis_v1_tts_preview_text(payload.text, prompt) or prompt
            sample_text_for_usage = sample_text
            audio_url = await asyncio.to_thread(heygen_tts_preview_url, config["api_key"], voice_id, sample_text, language, 1.0)
            audio_data, mime_type = await asyncio.to_thread(analysis_v1_tts_audio_url_bytes, audio_url)
            await asyncio.to_thread(write_analysis_v1_tts_audio_bytes, audio_data, mime_type, raw_output_path)
        elif provider == "minimax":
            sample_text = extract_analysis_v1_tts_preview_text(payload.text, prompt) or prompt
            sample_text_for_usage = sample_text
            audio_url = await asyncio.to_thread(minimax_tts_preview_url, config["api_key"], voice_id, sample_text, language, 1.0, config.get("extra") or {})
            audio_data, mime_type = await asyncio.to_thread(analysis_v1_tts_audio_url_bytes, audio_url)
            await asyncio.to_thread(write_analysis_v1_tts_audio_bytes, audio_data, mime_type, raw_output_path)
        elif provider == "bytedance":
            sample_text = (payload.text or prompt).strip()
            sample_text_for_usage = sample_text
            audio_url = await asyncio.to_thread(bytedance_tts_preview_url, config["api_key"], config["model"], voice_id, sample_text, config.get("extra") or {}, "direct")
            audio_data, mime_type = await asyncio.to_thread(analysis_v1_tts_audio_url_bytes, audio_url)
            await asyncio.to_thread(write_analysis_v1_tts_audio_bytes, audio_data, mime_type, raw_output_path)
        else:
            await asyncio.to_thread(generate_google_tts_audio, config, voice_id, prompt, raw_output_path)
        tempo_meta = await asyncio.to_thread(apply_analysis_v1_tts_tempo, raw_output_path, output_path, tempo)
        duration_seconds = analysis_v1_audio_duration_seconds(output_path)
        local_usage = record_storyboard_usage(
            ctx,
            task_row,
            request_id=stable_usage_request_id("analysis_v1_tts_preview", task_id, payload.candidate_id or voice_id, output_rel, provider, config["model"]),
            provider=provider,
            model_id=config["model"],
            modality="tts",
            step_id="analysis_v1.tts.preview",
            units=tts_usage_units(sample_text_for_usage, prompt=prompt, audio_seconds=duration_seconds, output_bytes=output_path.stat().st_size if output_path.exists() else 0),
        )
        completed_payload = {"task_id": task_id, "session_id": session_id, "provider": provider, "model": config["model"], "voice_id": voice_id, "tempo": tempo_meta.get("tempo"), "stretched": tempo_meta.get("stretched"), "output": output_rel, "raw_output": raw_output_rel, "duration_seconds": duration_seconds, "local_usage": local_usage, "local_usage_id": local_usage.get("local_usage_id", "")}
        add_session_event(session_id, "analysis_v1.tts.preview.completed", completed_payload)
        return {"ok": True, **completed_payload}

    @router.get("/api/openclip/tasks/{task_id}/analysis-v1/run-to-storyboard/plan")
    async def get_analysis_v1_run_to_storyboard_plan(request: Request, task_id: int) -> dict[str, Any]:
        role = request_role(request)
        task_row = get_task(task_id)
        ensure_analysis_v1_compatible_task(task_row)
        payload = OpenClipAnalysisV1RunPayload(
            task_id=task_id,
            run_model_provider=str(task_row.get("run_model_provider") or ""),
            run_model_id=str(task_row.get("run_model_id") or ""),
        )
        plan = analysis_v1_compile_plan(task_row, payload)
        active = analysis_v1_active_attempt_summary()
        return mask_for_role(role, SURFACE_ANALYSIS_V1_RUN, {
            "ok": True,
            "task_id": task_id,
            "attempt_family": ANALYSIS_V1_ATTEMPT_FAMILY,
            "target": ANALYSIS_V1_TARGET,
            "default_mode": "run_all",
            "modes": sorted(ANALYSIS_V1_RUN_MODES),
            "plan": {key: value for key, value in plan.items() if key != "steps"},
            "steps": plan["steps"],
            "capabilities": {
                "can_run": active is None,
                "can_stop": active is not None,
                "can_set_pause_point": active is not None,
                "can_run_only_step": True,
                "can_rerun_all": True,
                "can_rerun_from_step": True,
            },
            "active_attempt": active,
        })

    @router.post("/api/koubo-storyboard/tasks/{task_id}/session-variables/refresh")
    async def refresh_koubo_storyboard_session_variables(request: Request, task_id: int) -> dict[str, Any]:
        role = request_role(request)
        task_row = get_task(task_id)
        payload = run_session_variables_prepare_00(task_row, force=True)
        return mask_for_role(role, SURFACE_ANALYSIS_V1_RUN, payload)

    @router.post("/api/openclip/tasks/{task_id}/analysis-v1/run-to-storyboard")
    async def start_analysis_v1_run_to_storyboard(request: Request, task_id: int, payload: OpenClipAnalysisV1RunPayload) -> dict[str, Any]:
        role = request_role(request)
        if payload.task_id is not None and payload.task_id != task_id:
            raise HTTPException(status_code=400, detail="Payload task_id does not match URL task_id")
        fixed_update = fixed_fields_update_for_role(
            ctx,
            role,
            SURFACE_ANALYSIS_V1_RUN,
            payload.model_dump(),
            set(payload.model_fields_set),
        )
        if fixed_update:
            payload = payload.model_copy(update=fixed_update)
        asr_mode = (payload.asr_mode or "default").strip().lower()
        if asr_mode not in {"default", "cloud", "local"}:
            raise HTTPException(status_code=400, detail="asr_mode must be default, cloud, or local")
        tts_builder_mode = normalize_analysis_v1_tts_builder_mode(payload)
        rewrite_mode = normalize_analysis_v1_rewrite_mode(payload)
        storyboard_mode = normalize_analysis_v1_storyboard_mode(payload)
        mode = analysis_v1_normalize_mode(payload.mode)
        force = bool(payload.force or mode in {"rerun_all", "rerun_failed", "rerun_from_step"})
        payload = payload.model_copy(update={"task_id": task_id, "mode": mode, "asr_mode": asr_mode, "include_tts_builder": tts_builder_mode != "skip", "tts_builder_mode": tts_builder_mode, "rewrite_mode": rewrite_mode, "storyboard_mode": storyboard_mode, "force": force})
        with analysis_v1_run_lock:
            active = analysis_v1_active_attempt_summary()
            if active:
                raise HTTPException(status_code=409, detail={"code": "active_run_exists", "message": "已有 Analysis_V1 工具链运行中。", "active_attempt": active})
            task_row = get_task(task_id)
            if analysis_v1_payload_workflow_profile(payload) == WORKFLOW_PERSON_TALKING_HEAD_V1:
                ensure_talking_head_v1_task(task_row)
            else:
                ensure_analysis_v1_compatible_task(task_row)
            session_id = int(task_row["session_id"])
            session_row = safe_session(session_id)
            workspace = analysis_v1_workspace(task_row)
            workflow_profile = analysis_v1_effective_workflow_profile(payload, task_row, workspace)
            payload = analysis_v1_payload_with_workflow_profile(payload, workflow_profile)
            existing_video_plan_state = normalize_existing_video_plan_execution_state(workspace)
            if str(existing_video_plan_state.get("status") or "").strip() in {"queued", "running"}:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "active_video_plan_execution_exists",
                        "message": "已有生成计划正在执行，请等待完成后再重新运行故事板。",
                        "job_id": str(existing_video_plan_state.get("job_id") or "").strip(),
                    },
                )
            run_provider = payload.run_model_provider
            run_model_id = payload.run_model_id
            if role == "admin":
                run_provider = run_provider or str(task_row.get("run_model_provider") or "")
                run_model_id = run_model_id or str(task_row.get("run_model_id") or "")
            model, _ = resolve_model(session_row, run_provider, run_model_id, "Analysis V1 run", role, SURFACE_ANALYSIS_V1_RUN)
            plan = analysis_v1_compile_plan(task_row, payload, model)
            execute_ids = set(plan.get("execute_step_ids") or [])
            has_reference_video = bool(str(task_row.get("reference_video_path") or "").strip())
            if not has_reference_video:
                if analysis_v1_execute_requires_reference_video(execute_ids, workflow_profile):
                    raise HTTPException(status_code=400, detail="Task-level analysis reference video path is required before running video-dependent Analysis_V1 steps")
                if workflow_profile != "person_talking_head_v1" and not analysis_v1_execute_uses_uploaded_reference_audio(execute_ids) and not analysis_v1_script_only_input_ready(workspace):
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "code": "script_input_missing",
                            "message": "脚本创建任务缺少已保存的脚本或 SRT JSON，不能在无参考视频时运行。",
                            "missing": ["SessionOutput/subtitle/source_script.txt", "SessionOutput/subtitle/final_srt_frame_items.json"],
                            "suggested_action": "回到任务列表，选中该 Task，打开脚本生成并先保存脚本。",
                        },
                    )
            if "02_01" in execute_ids:
                validate_analysis_v1_asr_authorization(payload)
            if {"04_01", "04_02", "04_03"} & execute_ids and workflow_profile != "person_talking_head_v1":
                validate_analysis_v1_run_prerequisites(task_row, storyboard_mode)
            pause_before_step_id = str(plan.get("pause_before_step_id") or "")
            if pause_before_step_id and pause_before_step_id not in {str(step.get("id")) for step in plan["steps"]}:
                raise HTTPException(status_code=400, detail=f"pause_before_step_id is not in the Analysis_V1 plan: {pause_before_step_id}")
            dependency_block = analysis_v1_plan_dependency_block(workspace, plan)
            initial_status = "blocked" if dependency_block else "queued"
            if dependency_block:
                for step in plan["steps"]:
                    if str(step.get("id") or "") == str(dependency_block["step_id"]):
                        step.update({"status": "blocked", "message": dependency_block["message"], "blocked_reasons": [dependency_block]})
                        break
            attempt = repo.create_attempt(
                task_id=task_id,
                session_id=session_id,
                status=initial_status,
                prompt_version_id=int(task_row.get("current_prompt_version_id") or 0) or None,
                skill_version_id=None,
                run_model_provider=model["providerID"],
                run_model_id=model["modelID"],
                summary=(dependency_block or {}).get("message", ""),
                created_at=now_ms(),
            )
            attempt_id = int(attempt["id"])
            attempt_no = int(attempt["attempt_no"])
            state = analysis_v1_set_run_state(
                attempt_id,
                workspace,
                task_id=task_id,
                session_id=session_id,
                attempt_id=attempt_id,
                attempt_no=attempt_no,
                attempt_family=ANALYSIS_V1_ATTEMPT_FAMILY,
                target=ANALYSIS_V1_TARGET,
                status=initial_status,
                model=model,
                steps=plan["steps"],
                plan={key: value for key, value in plan.items() if key != "steps"},
                current_step_id=None,
                pause_before_step_id=pause_before_step_id,
                pause_reason="user_requested" if pause_before_step_id else "",
                pause_requested_at=now_ms() if pause_before_step_id else None,
                paused_at=None,
                resume_requested_at=None,
                cancel_requested=False,
                cancel_requested_at=None,
                stop_mode="",
                dependency_block=dependency_block,
                task_url=frontend_task_url(request, task_id),
            )
            repo.update_task(task_id, status=initial_status, latest_attempt_id=attempt_id, run_model_provider=model["providerID"], run_model_id=model["modelID"], updated_at=now_ms())
            sync_analysis_v1_run_context(task_id=task_id, session_id=session_id, attempt_id=attempt_id, workspace=workspace, model=model)
        analysis_v1_event(session_id, "attempt.created", {"task_id": task_id, "attempt_id": attempt_id, "attempt_no": attempt_no, "model": model, "mode": mode, "billing_scope": plan.get("options", {}).get("billing_scope"), "plan_hash": plan["plan_hash"], "pause_before_step_id": pause_before_step_id}, task_id=task_id, attempt_id=attempt_id)
        if dependency_block:
            analysis_v1_event(session_id, "step.blocked", {"task_id": task_id, "attempt_id": attempt_id, **dependency_block}, task_id=task_id, attempt_id=attempt_id, step_id=str(dependency_block["step_id"]))
            analysis_v1_event(session_id, "attempt.blocked", {"task_id": task_id, "attempt_id": attempt_id, **dependency_block}, task_id=task_id, attempt_id=attempt_id)
            return mask_for_role(role, SURFACE_ANALYSIS_V1_RUN, analysis_v1_indicator_payload(task_id, attempt, state))
        if pause_before_step_id:
            analysis_v1_event(session_id, "pause.requested", {"task_id": task_id, "attempt_id": attempt_id, "step_id": pause_before_step_id, "reason": "user_requested"}, task_id=task_id, attempt_id=attempt_id, step_id=pause_before_step_id)
        thread = threading.Thread(
            target=analysis_v1_run_to_storyboard,
            kwargs={
                "task_id": task_id,
                "session_id": session_id,
                "attempt_id": attempt_id,
                "workspace": workspace,
                "model": model,
                "payload": payload,
            },
            daemon=True,
        )
        thread.start()
        return mask_for_role(role, SURFACE_ANALYSIS_V1_RUN, analysis_v1_indicator_payload(task_id, attempt, state))

    @router.post("/api/talking-head-v1/tasks/{task_id}/run-storyboard")
    async def start_talking_head_v1_run_storyboard(request: Request, task_id: int, payload: OpenClipAnalysisV1RunPayload) -> dict[str, Any]:
        options = dict(payload.options or {})
        options.update({"workflow_profile": "person_talking_head_v1", "profile_id": "person_talking_head_v1"})
        payload = payload.model_copy(update={
            "options": options,
            "mode": "run_selected_steps",
            "selected_step_ids": ["00", "04_01", "01", "02", "03"],
        })
        return await start_analysis_v1_run_to_storyboard(request, task_id, payload)

    @router.post("/api/openclip/tasks/{task_id}/analysis-v1/one-click-movie")
    async def start_analysis_v1_one_click_movie(request: Request, task_id: int, payload: OpenClipAnalysisV1OneClickMoviePayload) -> dict[str, Any]:
        role = request_role(request)
        return mask_for_role(role, SURFACE_ANALYSIS_V1_RUN, analysis_v1_one_click_start(task_id, payload, role, request))

    @router.post("/api/talking-head-v1/tasks/{task_id}/one-click-movie")
    async def start_talking_head_v1_one_click_movie(request: Request, task_id: int, payload: OpenClipAnalysisV1OneClickMoviePayload) -> dict[str, Any]:
        options = dict(payload.options or {})
        options.update({"workflow_profile": "person_talking_head_v1", "profile_id": "person_talking_head_v1"})
        payload = payload.model_copy(update={"options": options})
        return await start_analysis_v1_one_click_movie(request, task_id, payload)

    @router.get("/api/openclip/tasks/{task_id}/analysis-v1/one-click-movie")
    async def get_analysis_v1_one_click_movie_latest(request: Request, task_id: int) -> dict[str, Any]:
        role = request_role(request)
        return mask_for_role(role, SURFACE_ANALYSIS_V1_RUN, analysis_v1_one_click_status(task_id))

    @router.get("/api/openclip/tasks/{task_id}/analysis-v1/one-click-movie/{run_id}")
    async def get_analysis_v1_one_click_movie_status(request: Request, task_id: int, run_id: str) -> dict[str, Any]:
        role = request_role(request)
        return mask_for_role(role, SURFACE_ANALYSIS_V1_RUN, analysis_v1_one_click_status(task_id, run_id))

    @router.get("/api/talking-head-v1/tasks/{task_id}/one-click-movie")
    async def get_talking_head_v1_one_click_movie_latest(request: Request, task_id: int) -> dict[str, Any]:
        role = request_role(request)
        return mask_for_role(role, SURFACE_ANALYSIS_V1_RUN, analysis_v1_one_click_status(task_id, workflow_profile=WORKFLOW_PERSON_TALKING_HEAD_V1))

    @router.get("/api/talking-head-v1/tasks/{task_id}/one-click-movie/{run_id}")
    async def get_talking_head_v1_one_click_movie_status(request: Request, task_id: int, run_id: str) -> dict[str, Any]:
        role = request_role(request)
        return mask_for_role(role, SURFACE_ANALYSIS_V1_RUN, analysis_v1_one_click_status(task_id, run_id, workflow_profile=WORKFLOW_PERSON_TALKING_HEAD_V1))

    @router.get("/api/openclip/tasks/{task_id}/analysis-v1/run-to-storyboard/{attempt_id}")
    async def get_analysis_v1_run_to_storyboard_status(request: Request, task_id: int, attempt_id: int) -> dict[str, Any]:
        role = request_role(request)
        get_task(task_id)
        attempt = repo.get_attempt(attempt_id)
        if not attempt or int(attempt.get("task_id") or 0) != task_id:
            raise HTTPException(status_code=404, detail="Attempt not found")
        state = analysis_v1_state_for_attempt(attempt)
        return mask_for_role(role, SURFACE_ANALYSIS_V1_RUN, analysis_v1_indicator_payload(task_id, attempt, state))

    @router.get("/api/talking-head-v1/tasks/{task_id}/run-storyboard/{attempt_id}")
    async def get_talking_head_v1_run_storyboard_status(request: Request, task_id: int, attempt_id: int) -> dict[str, Any]:
        return await get_analysis_v1_run_to_storyboard_status(request, task_id, attempt_id)

    @router.post("/api/openclip/tasks/{task_id}/analysis-v1/run-to-storyboard/{attempt_id}/stop")
    async def stop_analysis_v1_run_to_storyboard(request: Request, task_id: int, attempt_id: int, payload: OpenClipAnalysisV1StopPayload) -> dict[str, Any]:
        role = request_role(request)
        get_task(task_id)
        attempt = repo.get_attempt(attempt_id)
        if not attempt or int(attempt.get("task_id") or 0) != task_id:
            raise HTTPException(status_code=404, detail="Attempt not found")
        state = analysis_v1_state_for_attempt(attempt)
        if not state:
            raise HTTPException(status_code=404, detail="Run state not found")
        status = str(state.get("status") or attempt.get("status") or "").lower()
        if status not in ANALYSIS_V1_ACTIVE_STATUSES:
            raise HTTPException(status_code=409, detail={"code": "attempt_not_active", "status": status})
        if str(payload.mode or "graceful") != "graceful":
            raise HTTPException(status_code=400, detail="terminate_current is reserved and not enabled in the MVP")
        workspace = Path(str(state.get("workspace") or safe_session(int(attempt["session_id"]))["workspace_dir"]))
        timestamp = now_ms()
        repo.update_attempt(attempt_id, status="stopping")
        repo.update_task(task_id, status="stopping", latest_attempt_id=attempt_id, updated_at=timestamp)
        analysis_v1_set_run_state(attempt_id, workspace, status="stopping", cancel_requested=True, cancel_requested_at=timestamp, stop_mode="graceful", stop_reason=payload.reason or "user_requested")
        analysis_v1_event(int(attempt["session_id"]), "attempt.cancel_requested", {"task_id": task_id, "attempt_id": attempt_id, "mode": "graceful", "reason": payload.reason}, task_id=task_id, attempt_id=attempt_id)
        return mask_for_role(role, SURFACE_ANALYSIS_V1_RUN, analysis_v1_indicator_payload(task_id, attempt, analysis_v1_run_state(attempt_id, workspace)))

    @router.post("/api/openclip/tasks/{task_id}/analysis-v1/run-to-storyboard/{attempt_id}/pause-before")
    async def pause_before_analysis_v1_run_to_storyboard(request: Request, task_id: int, attempt_id: int, payload: OpenClipAnalysisV1PauseBeforePayload) -> dict[str, Any]:
        role = request_role(request)
        get_task(task_id)
        attempt = repo.get_attempt(attempt_id)
        if not attempt or int(attempt.get("task_id") or 0) != task_id:
            raise HTTPException(status_code=404, detail="Attempt not found")
        state = analysis_v1_state_for_attempt(attempt)
        if not state:
            raise HTTPException(status_code=404, detail="Run state not found")
        status = str(state.get("status") or attempt.get("status") or "").lower()
        if status not in {"queued", "running", "paused"}:
            raise HTTPException(status_code=409, detail={"code": "attempt_not_pauseable", "status": status})
        step_id = str(payload.step_id or "").strip()
        step = analysis_v1_step_from_state(state, step_id)
        if not step:
            raise HTTPException(status_code=404, detail="Step not found")
        if str(step.get("status") or "").lower() != "pending":
            raise HTTPException(status_code=409, detail={"code": "step_already_started", "message": "该步骤已通过或已开始，无法在本次运行中设置暂停点", "step_id": step_id, "status": step.get("status")})
        workspace = Path(str(state.get("workspace") or safe_session(int(attempt["session_id"]))["workspace_dir"]))
        timestamp = now_ms()
        analysis_v1_set_run_state(attempt_id, workspace, pause_before_step_id=step_id, pause_reason=payload.reason or "user_requested", pause_requested_at=timestamp)
        analysis_v1_event(int(attempt["session_id"]), "pause.requested", {"task_id": task_id, "attempt_id": attempt_id, "step_id": step_id, "reason": payload.reason}, task_id=task_id, attempt_id=attempt_id, step_id=step_id)
        return mask_for_role(role, SURFACE_ANALYSIS_V1_RUN, analysis_v1_indicator_payload(task_id, attempt, analysis_v1_run_state(attempt_id, workspace)))

    @router.delete("/api/openclip/tasks/{task_id}/analysis-v1/run-to-storyboard/{attempt_id}/pause-before")
    async def cancel_pause_before_analysis_v1_run_to_storyboard(request: Request, task_id: int, attempt_id: int) -> dict[str, Any]:
        role = request_role(request)
        get_task(task_id)
        attempt = repo.get_attempt(attempt_id)
        if not attempt or int(attempt.get("task_id") or 0) != task_id:
            raise HTTPException(status_code=404, detail="Attempt not found")
        state = analysis_v1_state_for_attempt(attempt)
        if not state:
            raise HTTPException(status_code=404, detail="Run state not found")
        status = str(state.get("status") or attempt.get("status") or "").lower()
        if status not in {"queued", "running", "paused"}:
            raise HTTPException(status_code=409, detail={"code": "attempt_not_pauseable", "status": status})
        workspace = Path(str(state.get("workspace") or safe_session(int(attempt["session_id"]))["workspace_dir"]))
        old_step_id = str(state.get("pause_before_step_id") or "")
        analysis_v1_set_run_state(attempt_id, workspace, pause_before_step_id="", pause_reason="", pause_requested_at=None)
        analysis_v1_event(int(attempt["session_id"]), "pause.cancelled", {"task_id": task_id, "attempt_id": attempt_id, "step_id": old_step_id}, task_id=task_id, attempt_id=attempt_id, step_id=old_step_id)
        return mask_for_role(role, SURFACE_ANALYSIS_V1_RUN, analysis_v1_indicator_payload(task_id, attempt, analysis_v1_run_state(attempt_id, workspace)))

    @router.post("/api/openclip/tasks/{task_id}/analysis-v1/run-to-storyboard/{attempt_id}/resume")
    async def resume_analysis_v1_run_to_storyboard(request: Request, task_id: int, attempt_id: int, payload: OpenClipAnalysisV1ResumePayload) -> dict[str, Any]:
        role = request_role(request)
        get_task(task_id)
        attempt = repo.get_attempt(attempt_id)
        if not attempt or int(attempt.get("task_id") or 0) != task_id:
            raise HTTPException(status_code=404, detail="Attempt not found")
        state = analysis_v1_state_for_attempt(attempt)
        if not state:
            raise HTTPException(status_code=404, detail="Run state not found")
        status = str(state.get("status") or attempt.get("status") or "").lower()
        if status != "paused":
            raise HTTPException(status_code=409, detail={"code": "attempt_not_paused", "status": status})
        workspace = Path(str(state.get("workspace") or safe_session(int(attempt["session_id"]))["workspace_dir"]))
        timestamp = now_ms()
        repo.update_attempt(attempt_id, status="running")
        repo.update_task(task_id, status="running", latest_attempt_id=attempt_id, updated_at=timestamp)
        ctx.session_repo.update(int(attempt["session_id"]), status="running", updated_at=timestamp)
        analysis_v1_set_run_state(attempt_id, workspace, status="running", pause_before_step_id="", resume_requested_at=timestamp)
        analysis_v1_event(int(attempt["session_id"]), "attempt.resumed", {"task_id": task_id, "attempt_id": attempt_id, "reason": payload.reason}, task_id=task_id, attempt_id=attempt_id)
        return mask_for_role(role, SURFACE_ANALYSIS_V1_RUN, analysis_v1_indicator_payload(task_id, attempt, analysis_v1_run_state(attempt_id, workspace)))

    @router.get("/api/openclip/tasks/{task_id}/analysis-v1/run-to-storyboard/{attempt_id}/steps/{step_id}/quick-watch")
    async def get_analysis_v1_step_quick_watch(request: Request, task_id: int, attempt_id: int, step_id: str) -> dict[str, Any]:
        role = request_role(request)
        get_task(task_id)
        attempt = repo.get_attempt(attempt_id)
        if not attempt or int(attempt.get("task_id") or 0) != task_id:
            raise HTTPException(status_code=404, detail="Attempt not found")
        state = analysis_v1_state_for_attempt(attempt)
        step = analysis_v1_step_from_state(state, step_id)
        if not step:
            raise HTTPException(status_code=404, detail="Step not found")
        quick_watch = step.get("quick_watch") or {}
        return mask_for_role(role, SURFACE_ANALYSIS_V1_RUN, {"ok": True, "task_id": task_id, "attempt_id": attempt_id, "step_id": step_id, "step": step, "quick_watch": quick_watch})

    @router.get("/api/openclip/tasks/{task_id}/analysis-v1/run-to-storyboard/{attempt_id}/steps/{step_id}/logs")
    async def get_analysis_v1_step_logs(request: Request, task_id: int, attempt_id: int, step_id: str, cursor: str = "") -> dict[str, Any]:
        role = request_role(request)
        get_task(task_id)
        attempt = repo.get_attempt(attempt_id)
        if not attempt or int(attempt.get("task_id") or 0) != task_id:
            raise HTTPException(status_code=404, detail="Attempt not found")
        state = analysis_v1_state_for_attempt(attempt)
        step = analysis_v1_step_from_state(state, step_id)
        if not step:
            raise HTTPException(status_code=404, detail="Step not found")
        return mask_for_role(role, SURFACE_ANALYSIS_V1_RUN, {
            "ok": True,
            "task_id": task_id,
            "attempt_id": attempt_id,
            "step_id": step_id,
            "cursor": cursor,
            "stdout_tail": step.get("stdout_tail") or "",
            "stderr_tail": step.get("stderr_tail") or "",
            "status": step.get("status") or "",
        })

    def save_analysis_v1_rewritten_srt_payload(request: Request, task_id: int, payload: OpenClipAnalysisV1SrtRewriteSavePayload) -> dict[str, Any]:
        role = request_role(request)
        if payload.task_id is not None and int(payload.task_id) != task_id:
            raise HTTPException(status_code=400, detail="Task id mismatch")
        task_row = get_task(task_id)
        workspace = analysis_v1_workspace(task_row)
        target_path = workspace / ANALYSIS_V1_REWRITTEN_SRT_ITEMS_REL
        if not target_path.exists():
            raise HTTPException(status_code=404, detail=f"JSON not found: {ANALYSIS_V1_REWRITTEN_SRT_ITEMS_REL}")
        try:
            current = json.loads(target_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid JSON: {ANALYSIS_V1_REWRITTEN_SRT_ITEMS_REL}") from exc
        items = current.get("items") if isinstance(current, dict) else None
        if not isinstance(items, list):
            raise HTTPException(status_code=400, detail=f"{ANALYSIS_V1_REWRITTEN_SRT_ITEMS_REL} must contain an items array")
        edits = {str(item.srt_id): str(item.dialogue) for item in payload.items}
        if not edits:
            raise HTTPException(status_code=400, detail="No rewritten SRT edits were provided")
        known_ids = {str(item.get("srt_id") or "") for item in items if isinstance(item, dict)}
        missing_ids = [srt_id for srt_id in edits if srt_id not in known_ids]
        if missing_ids:
            raise HTTPException(status_code=409, detail={"code": "srt_id_not_found", "missing_srt_ids": missing_ids[:20]})
        updated = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            srt_id = str(item.get("srt_id") or "")
            if srt_id in edits and str(item.get("dialogue") or "") != edits[srt_id]:
                item["dialogue"] = edits[srt_id]
                updated += 1
        write_json_atomic(target_path, current)
        storyboard_sync = sync_analysis_v1_storyboard_dialogues(workspace, edits)
        repo.update_task(task_id, updated_at=now_ms())
        sync_session_files(safe_session(int(task_row["session_id"])))
        return mask_for_role(role, SURFACE_ANALYSIS_V1_RUN, {
            "ok": True,
            "task_id": task_id,
            "path": ANALYSIS_V1_REWRITTEN_SRT_ITEMS_REL,
            "updated": updated,
            "storyboard_sync": storyboard_sync,
            "payload": current,
        })

    @router.post("/api/openclip/tasks/{task_id}/analysis-v1/rewritten-srt")
    async def save_analysis_v1_rewritten_srt(request: Request, task_id: int, payload: OpenClipAnalysisV1SrtRewriteSavePayload) -> dict[str, Any]:
        return save_analysis_v1_rewritten_srt_payload(request, task_id, payload)

    @router.put("/api/openclip/tasks/{task_id}/analysis-v1/rewritten-srt")
    async def put_analysis_v1_rewritten_srt(request: Request, task_id: int, payload: OpenClipAnalysisV1SrtRewriteSavePayload) -> dict[str, Any]:
        return save_analysis_v1_rewritten_srt_payload(request, task_id, payload)

    @router.post("/api/openclip/tasks/{task_id}/prompt-versions")
    async def save_openclip_prompt_version(request: Request, task_id: int, payload: OpenClipPromptVersionSavePayload) -> dict[str, Any]:
        role = request_role(request)
        task_row = get_task(task_id)
        version = repo.create_prompt_version(task_id=task_id, name=payload.version_name.strip() or f"Version {now_ms()}", notes=payload.version_notes.strip(), reference_video_path=str(task_row.get("reference_video_path") or ""), industry=str(task_row.get("industry") or ""), persona=str(task_row.get("persona") or ""), target_audience=str(task_row.get("target_audience") or ""), product_info=str(task_row.get("product_info") or ""), constraints=str(task_row.get("constraints") or ""), analysis_goal=str(task_row.get("analysis_goal") or ""), video_formula=str(task_row.get("video_formula") or ""), simple_prompt=str(task_row.get("simple_prompt") or ""), rewrite_simple_prompt=str(task_row.get("rewrite_simple_prompt") or task_row.get("simple_prompt") or ""), rewrite_final_prompt=str(task_row.get("rewrite_final_prompt") or task_row.get("final_prompt") or ""), storyboard_simple_prompt=str(task_row.get("storyboard_simple_prompt") or ""), storyboard_final_prompt=str(task_row.get("storyboard_final_prompt") or ""), storyboard_quick_config_json=storyboard_quick_config_json(task_row.get("storyboard_quick_config_json")), prompt_model_provider=str(task_row.get("prompt_model_provider") or ""), prompt_model_id=str(task_row.get("prompt_model_id") or ""), final_prompt=str(task_row.get("final_prompt") or ""), created_at=now_ms())
        repo.update_task(task_id, current_prompt_version_id=int(version["id"]), updated_at=now_ms())
        return mask_for_role(role, SURFACE_ANALYSIS_V1_PROMPT, serialize_task_detail(get_task(task_id)))

    @router.post("/api/openclip/tasks/{task_id}/prompt-versions/load")
    async def load_openclip_prompt_version(request: Request, task_id: int, payload: OpenClipVersionLoadPayload) -> dict[str, Any]:
        role = request_role(request)
        task_row = get_task(task_id)
        version = repo.get_prompt_version(payload.version_id)
        if not version or int(version["task_id"]) != task_id:
            raise HTTPException(status_code=404, detail="Prompt version not found")
        hydrated = hydrate_prompt_version(task_row, version) or version
        repo.update_task(task_id, current_prompt_version_id=int(version["id"]), reference_video_path=str(hydrated.get("reference_video_path") or ""), industry=str(hydrated.get("industry") or ""), persona=str(hydrated.get("persona") or ""), target_audience=str(hydrated.get("target_audience") or ""), product_info=str(hydrated.get("product_info") or ""), constraints=str(hydrated.get("constraints") or ""), analysis_goal=str(hydrated.get("analysis_goal") or ""), video_formula=str(hydrated.get("video_formula") or ""), simple_prompt=str(hydrated.get("simple_prompt") or ""), final_prompt=str(hydrated.get("final_prompt") or ""), rewrite_simple_prompt=str(hydrated.get("rewrite_simple_prompt") or hydrated.get("simple_prompt") or ""), rewrite_final_prompt=str(hydrated.get("rewrite_final_prompt") or hydrated.get("final_prompt") or ""), storyboard_simple_prompt=str(hydrated.get("storyboard_simple_prompt") or ""), storyboard_final_prompt=str(hydrated.get("storyboard_final_prompt") or ""), storyboard_quick_config_json=storyboard_quick_config_json(hydrated.get("storyboard_quick_config_json")), prompt_model_provider=str(hydrated.get("prompt_model_provider") or ""), prompt_model_id=str(hydrated.get("prompt_model_id") or ""), updated_at=now_ms())
        return mask_for_role(role, SURFACE_ANALYSIS_V1_PROMPT, serialize_task_detail(get_task(task_id)))

    @router.put("/api/openclip/tasks/{task_id}/prompt-versions/{version_id}")
    async def update_openclip_prompt_version(request: Request, task_id: int, version_id: int) -> dict[str, Any]:
        role = request_role(request)
        task_row = get_task(task_id)
        version = repo.get_prompt_version(version_id)
        if not version or int(version["task_id"]) != task_id:
            raise HTTPException(status_code=404, detail="Prompt version not found")
        repo.update_prompt_version(version_id, reference_video_path=str(task_row.get("reference_video_path") or ""), industry=str(task_row.get("industry") or ""), persona=str(task_row.get("persona") or ""), target_audience=str(task_row.get("target_audience") or ""), product_info=str(task_row.get("product_info") or ""), constraints=str(task_row.get("constraints") or ""), analysis_goal=str(task_row.get("analysis_goal") or ""), video_formula=str(task_row.get("video_formula") or ""), simple_prompt=str(task_row.get("simple_prompt") or ""), rewrite_simple_prompt=str(task_row.get("rewrite_simple_prompt") or task_row.get("simple_prompt") or ""), rewrite_final_prompt=str(task_row.get("rewrite_final_prompt") or task_row.get("final_prompt") or ""), storyboard_simple_prompt=str(task_row.get("storyboard_simple_prompt") or ""), storyboard_final_prompt=str(task_row.get("storyboard_final_prompt") or ""), storyboard_quick_config_json=storyboard_quick_config_json(task_row.get("storyboard_quick_config_json")), prompt_model_provider=str(task_row.get("prompt_model_provider") or ""), prompt_model_id=str(task_row.get("prompt_model_id") or ""), final_prompt=str(task_row.get("final_prompt") or ""))
        repo.update_task(task_id, current_prompt_version_id=version_id, updated_at=now_ms())
        return mask_for_role(role, SURFACE_ANALYSIS_V1_PROMPT, serialize_task_detail(get_task(task_id)))

    @router.delete("/api/openclip/tasks/{task_id}/prompt-versions/{version_id}")
    async def delete_openclip_prompt_version(request: Request, task_id: int, version_id: int) -> dict[str, Any]:
        role = request_role(request)
        task_row = get_task(task_id)
        repo.delete_prompt_version(task_id, version_id)
        if int(task_row.get("current_prompt_version_id") or 0) == version_id:
            repo.update_task(task_id, current_prompt_version_id=None, simple_prompt="", final_prompt="", rewrite_simple_prompt="", rewrite_final_prompt="", storyboard_simple_prompt="", storyboard_final_prompt="", storyboard_quick_config_json=storyboard_quick_config_json({}), prompt_model_provider="", prompt_model_id="", updated_at=now_ms())
        return mask_for_role(role, SURFACE_ANALYSIS_V1_PROMPT, serialize_task_detail(get_task(task_id)))

    @router.post("/api/openclip/tasks/{task_id}/generate-skill")
    async def generate_openclip_skill(request: Request, task_id: int, payload: OpenClipSkillGeneratePayload) -> dict[str, Any]:
        role = request_role(request)
        task_row = get_task(task_id)
        if not payload.prompt_version_id:
            raise HTTPException(status_code=400, detail="Save a Final Prompt version before generating Skill")
        prompt_version = active_prompt_version(task_row, int(payload.prompt_version_id or 0) or None)
        if not prompt_version:
            raise HTTPException(status_code=400, detail="Final prompt is required before generating skill")
        skill_content = build_skill_content(task_row, str(prompt_version.get("final_prompt") or ""))
        prompt_version_id = int(prompt_version.get("id") or 0) or None
        repo.update_task(task_id, generated_skill_content=skill_content, skill_version_name=f"Skill {now_ms()}", skill_version_notes="auto generated from current Final Prompt", updated_at=now_ms())
        add_session_event(int(task_row["session_id"]), "openclip.skill.generated", {"task_id": task_id, "prompt_version_id": prompt_version_id, "mode": "template"})
        detail = serialize_task_detail(get_task(task_id))
        return mask_for_role(role, SURFACE_ANALYSIS_V1_PROMPT, detail)

    @router.put("/api/openclip/tasks/{task_id}/skill-draft")
    async def save_openclip_skill_draft(request: Request, task_id: int, payload: OpenClipSkillDraftSavePayload) -> dict[str, Any]:
        role = request_role(request)
        get_task(task_id)
        repo.update_task(task_id, generated_skill_content=payload.skill_content.strip(), skill_version_name=payload.version_name.strip(), skill_version_notes=payload.version_notes.strip(), updated_at=now_ms())
        return mask_for_role(role, SURFACE_ANALYSIS_V1_PROMPT, serialize_task_detail(get_task(task_id)))

    @router.post("/api/openclip/tasks/{task_id}/skill-versions")
    async def save_openclip_skill_version(request: Request, task_id: int, payload: OpenClipSkillVersionSavePayload) -> dict[str, Any]:
        role = request_role(request)
        version = repo.create_skill_version(task_id=task_id, prompt_version_id=payload.prompt_version_id, name=payload.version_name.strip() or f"Skill {now_ms()}", notes=payload.version_notes.strip(), skill_model_provider="template", skill_model_id="openclip-skill-template", skill_content=payload.skill_content.strip(), created_at=now_ms())
        repo.update_task(task_id, current_skill_version_id=int(version["id"]), generated_skill_content=payload.skill_content.strip(), skill_version_name=str(version.get("name") or ""), skill_version_notes=payload.version_notes.strip(), updated_at=now_ms())
        return mask_for_role(role, SURFACE_ANALYSIS_V1_PROMPT, serialize_task_detail(get_task(task_id)))

    @router.post("/api/openclip/tasks/{task_id}/skill-versions/load")
    async def load_openclip_skill_version(request: Request, task_id: int, payload: OpenClipVersionLoadPayload) -> dict[str, Any]:
        role = request_role(request)
        version = repo.get_skill_version(payload.version_id)
        if not version or int(version["task_id"]) != task_id:
            raise HTTPException(status_code=404, detail="Skill version not found")
        repo.update_task(task_id, current_skill_version_id=int(version["id"]), generated_skill_content=str(version.get("skill_content") or ""), skill_version_name=str(version.get("name") or ""), skill_version_notes=str(version.get("notes") or ""), updated_at=now_ms())
        return mask_for_role(role, SURFACE_ANALYSIS_V1_PROMPT, serialize_task_detail(get_task(task_id)))

    @router.delete("/api/openclip/tasks/{task_id}/skill-versions/{version_id}")
    async def delete_openclip_skill_version(request: Request, task_id: int, version_id: int) -> dict[str, Any]:
        role = request_role(request)
        task_row = get_task(task_id)
        repo.delete_skill_version(task_id, version_id)
        if int(task_row.get("current_skill_version_id") or 0) == version_id:
            remaining_versions = repo.list_skill_versions(task_id)
            latest = remaining_versions[0] if remaining_versions else None
            if latest:
                repo.update_task(task_id, current_skill_version_id=int(latest["id"]), generated_skill_content=str(latest.get("skill_content") or ""), skill_version_name=str(latest.get("name") or ""), skill_version_notes=str(latest.get("notes") or ""), updated_at=now_ms())
            else:
                repo.update_task(task_id, current_skill_version_id=None, generated_skill_content="", skill_version_name="", skill_version_notes="", updated_at=now_ms())
        return mask_for_role(role, SURFACE_ANALYSIS_V1_PROMPT, serialize_task_detail(get_task(task_id)))

    @router.post("/api/openclip/tasks/{task_id}/run")
    async def run_openclip_task(request: Request, task_id: int, payload: OpenClipRunPayload) -> dict[str, Any]:
        task_row = get_task(task_id)
        session_row = safe_session(int(task_row["session_id"]))
        skill_version = repo.get_skill_version(int(payload.skill_version_id or task_row.get("current_skill_version_id") or 0)) or current_skill_version(task_row)
        if not task_row.get("reference_video_path"):
            raise HTTPException(status_code=400, detail="Reference video path is required before running")
        if not skill_version:
            raise HTTPException(status_code=400, detail="Skill version is required before running")
        current_skill = str(skill_version.get("skill_content") or "").strip()
        if not current_skill:
            raise HTTPException(status_code=400, detail="Current Skill content is required before running")
        model, _ = resolve_model(session_row, payload.run_model_provider or str(task_row.get("run_model_provider") or ""), payload.run_model_id or str(task_row.get("run_model_id") or ""), "Run")
        prompt_version_id = int(skill_version.get("prompt_version_id") or 0) or None
        attempt = repo.create_attempt(task_id=task_id, session_id=int(task_row["session_id"]), status="queued", prompt_version_id=prompt_version_id, skill_version_id=int(skill_version["id"]), run_model_provider=model["providerID"], run_model_id=model["modelID"], created_at=now_ms())
        attempt_no = int(attempt["attempt_no"])
        archive_current_outputs(session_row, attempt_no)
        package_spec = stage_run_inputs(session_row, task_row, skill_version, attempt)
        repo.update_task(task_id, status="running", current_prompt_version_id=prompt_version_id, current_skill_version_id=int(skill_version["id"]), latest_attempt_id=int(attempt["id"]), run_model_provider=model["providerID"], run_model_id=model["modelID"], updated_at=now_ms())
        add_session_event(int(task_row["session_id"]), "system.message", {
            "kind": "current_skill",
            "input_policy": "current_skill_only",
            "attempt_id": int(attempt["id"]),
            "skill_version_id": int(skill_version["id"]),
            "text": current_skill,
            "preview": preview_text(current_skill),
        })
        add_session_event(int(task_row["session_id"]), "openclip.analysis.started", {"task_id": task_id, "attempt_id": int(attempt["id"]), "attempt_no": attempt_no, "prompt_version_id": prompt_version_id, "skill_version_id": int(skill_version["id"]), "model": model})
        start_prompt_thread(int(task_row["session_id"]), current_skill, "", model, int(attempt["id"]), False)
        return {"ok": True, "task_id": task_id, "session_id": int(task_row["session_id"]), "attempt_id": int(attempt["id"]), "task_url": frontend_task_url(request, task_id), "package_spec": package_spec}

    @router.post("/api/openclip/tasks/{task_id}/rerun")
    async def rerun_openclip_task(request: Request, task_id: int, payload: OpenClipRunPayload) -> dict[str, Any]:
        task_row = get_task(task_id)
        return await run_openclip_task(request, task_id, payload)

    @router.get("/api/openclip/tasks/{task_id}/attempts")
    async def list_openclip_attempts(request: Request, task_id: int) -> dict[str, Any]:
        role = request_role(request)
        get_task(task_id)
        return mask_for_role(role, SURFACE_ANALYSIS_V1_RUN, {"items": repo.list_attempts(task_id)})

    @router.get("/api/openclip/tasks/{task_id}/attempts/{attempt_id}")
    async def openclip_attempt_detail(request: Request, task_id: int, attempt_id: int) -> dict[str, Any]:
        role = request_role(request)
        get_task(task_id)
        row = repo.get_attempt(attempt_id)
        if not row or int(row["task_id"]) != task_id:
            raise HTTPException(status_code=404, detail="Attempt not found")
        return mask_for_role(role, SURFACE_ANALYSIS_V1_RUN, row)

    @router.get("/api/openclip/tasks/{task_id}/session-events")
    async def openclip_task_events(request: Request, task_id: int, since: int = Query(default=0)) -> dict[str, Any]:
        role = request_role(request)
        task_row = get_task(task_id)
        rows = ctx.session_repo.list_events(int(task_row["session_id"]), since, 500)
        items = []
        for row in rows:
            payload = row.get("payload") or "{}"
            try:
                parsed = json.loads(payload)
            except Exception:
                parsed = {}
            items.append({**row, "payload": parsed})
        return mask_for_role(role, SURFACE_ANALYSIS_V1_RUN, {"items": items})

    return router
