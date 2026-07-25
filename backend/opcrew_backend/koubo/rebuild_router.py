from __future__ import annotations

import json
import asyncio
import base64
import http.client
import mimetypes
import re
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import wave
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import text

from opcrew_backend.adapters.opencode import OpenCodeSessionClient
from opcrew_backend.context import AppContext, now_ms
try:
    from opcrew_model_config.media_model_config import (
        CONFIG_TABLE,
        TTSVoiceMatchPayload,
        build_media_model_config_router,
        dashscope_cosyvoice_tts_audio_bytes,
        ensure_table,
        load_stored_key,
    )
except ModuleNotFoundError:  # pragma: no cover - standalone contract-test import path
    from opcrew_backend.routes.media_model_config import (
        CONFIG_TABLE,
        TTSVoiceMatchPayload,
        build_media_model_config_router,
        dashscope_cosyvoice_tts_audio_bytes,
        ensure_table,
        load_stored_key,
    )
from opcrew_backend.services.provider_resolver import resolve_endpoint, urlopen as provider_urlopen

from .rebuild_repository import OCRebuildRepository
from .repository import OpenClipRepository
from .rebuild_schemas import OCRebuildAssetCompareFinalizePayload, OCRebuildAssetComparePayload, OCRebuildAssetImageGeneratePayload, OCRebuildAssetPromptRefinePayload, OCRebuildAssetTTSCompareFinalizePayload, OCRebuildAssetTTSComparePayload, OCRebuildAssetTTSPromptRefinePayload, OCRebuildAssetVideoCompareFinalizePayload, OCRebuildAssetVideoComparePayload, OCRebuildAssetVideoPromptRefinePayload, OCRebuildAssetWorkflowSavePayload, OCRebuildHostProductDeletePayload, OCRebuildHostProductGeneratePayload, OCRebuildHostProductPromptPayload, OCRebuildPromptGeneratePayload, OCRebuildRunPayload, OCRebuildSRTRewriteGeneratePayload, OCRebuildSRTRewriteSavePayload, OCRebuildShotKeyframesPayload, OCRebuildShotMultiReferenceFinalizePayload, OCRebuildShotMultiReferencePayload, OCRebuildShotMultiReferencePromptPayload, OCRebuildShotSceneMarksPayload, OCRebuildShotTTSBuilderPayload, OCRebuildShotTTSComparePayload, OCRebuildShotTTSFinalizePayload, OCRebuildShotTTSRecommendPayload, OCRebuildShotTTSVoiceSelectionPayload, OCRebuildShotTTSPromptRefinePayload, OCRebuildTaskUpdatePayload, OCRebuildVersionLoadPayload, OCRebuildVersionSavePayload


OC_REBUILD_SOURCE = "oc-rebuild"
OC_REBUILD_GROUP_ID = "oc-rebuild"

STORYBOARD_COPY_WORKSPACE_DIRS = {
    "Assets",
    "asset_image_workflows",
    "asset_tts_workflows",
    "asset_video_workflows",
    "consistency_references",
    "final_prompt_packages",
    "keyframes",
    "plan_c",
    "renders",
    "reports",
    "schemes",
    "tts",
    "uploads",
}

STORYBOARD_COPY_FILE_DENYLIST = {
    ".DS_Store",
    "storyboard_meta.json",
    "storyboard_snapshot.json",
}

PRESERVE_OPTIONS = {
    "duration_pattern": "保留分镜时长模式",
    "subtitle_timing": "保留字幕节奏",
    "semantic_roles": "保留语义公式槽位",
    "title_layout": "保留标题布局",
    "transition_rhythm": "保留转场节奏",
    "camera_style": "保留镜头/景别风格",
    "emotion_arc": "保留情绪推进",
}
REPLACE_OPTIONS = {
    "topic": "替换主题",
    "visuals": "替换画面内容",
    "voiceover": "替换口播文案",
    "subtitles": "替换字幕文案",
    "title_copy": "替换标题文案",
    "product": "替换产品/服务",
    "persona": "替换人物身份",
    "bgm": "替换背景音乐",
}


def provider_video_seconds(provider: str, model: str, duration: float | int | str | None) -> int:
    try:
        requested = int(round(float(duration if duration is not None else 4)))
    except (TypeError, ValueError):
        requested = 4
    provider_id = provider.strip().lower()
    model_id = model.strip().lower()
    if provider_id == "gemini":
        for allowed in (4, 6, 8):
            if requested <= allowed:
                return allowed
        return 8
    if provider_id == "openai":
        return max(4, min(requested, 20))
    if provider_id == "xai":
        return max(1, min(requested, 15))
    if provider_id == "wan":
        max_seconds = 15 if "happyhorse" in model_id else 30
        return max(3, min(requested, max_seconds))
    return max(1, requested)

FINAL_INTENT_SYSTEM_PROMPT = """
你是 OpenClip Rebuild Final Prompt Builder。

你的职责是把用户的 Rebuild 页面参数和 Simple Prompt，整理成一份清晰、完整、可结构化的重建意图说明。

严格要求：
1. 只能描述重建意图、保留策略、替换策略、风格策略、批量变量和限制条件。
2. 不得生成 shot-level 分镜计划。
3. 不得生成素材生成任务。
4. 不得生成图片 prompt、视频 prompt、ComfyUI prompt、渲染计划或执行步骤。
5. 不得出现代码、命令、工具路径、文件路径、JSON 字段解释或技术实现细节。
6. 输出必须能被后续程序稳定转换成 rebuild_intent.json。
7. 语言要明确、可执行、无歧义。

输出要求：
直接输出最终 Final Prompt，不要解释，不要代码块。
""".strip()

SRT_REWRITE_SYSTEM_PROMPT = """
你是 OC-Rebuild 的 SRT 改写器。

你的职责是把已经按 Scene 对齐的原始字幕改写成新产品/新主题字幕。

严格要求：
1. 逐行对应输入 rows，不得增删、合并、重排 Scene。
2. 只改写字幕文案，不输出解释、Markdown 或代码块。
3. 尽量保持每句长度、口播节奏和生活化语气接近原句。
4. 不要加入医疗化、药品化、绝对化或疗效承诺表达。
5. 输出必须是严格 JSON，形如：
{"rows":[{"shot_id":"...","scene_mark_id":"...","new_srt_text":"..."}]}
6. 每一行必须原样返回 shot_id 和 scene_mark_id。
""".strip()

IMAGE_PROMPT_DOC_URLS = {
    "openai": "https://developers.openai.com/cookbook/examples/multimodal/image-gen-models-prompting-guide#1-introduction",
    "xai": "https://docs.x.ai/developers/model-capabilities/images/generation",
    "gemini": "https://ai.google.dev/gemini-api/docs/image-generation?hl=zh-cn",
}
IMAGE_PROMPT_LOCAL_DOC_DIR = Path(__file__).resolve().parents[3] / "ToolLibrary" / "Rebuild" / "image_prompt_docs"
VIDEO_PROMPT_DOC_URLS = {
    "openai": "https://developers.openai.com/cookbook/topic/multimodal",
    "xai": "https://docs.x.ai/developers/model-capabilities/video/generation",
    "gemini": "https://ai.google.dev/gemini-api/docs/video?hl=zh-cn&example=dialogue",
    "wan": "https://bailian.console.aliyun.com/cn-beijing/?tab=doc#/doc/?type=model&url=3023501",
}
VIDEO_PROMPT_LOCAL_DOC_DIR = Path(__file__).resolve().parents[3] / "ToolLibrary" / "Rebuild" / "video_prompt_docs"
CONSISTENCY_REFERENCE_GUIDE_PATH = Path(__file__).resolve().parents[3] / "ToolLibrary" / "Rebuild_V1" / "consistency_reference_prompt_guide.md"

IMAGE_PROMPT_REFINE_SYSTEM_PROMPT = """
You are an image generation prompt adapter.

Your job is to rewrite the current image prompt for a specific target image provider/model.

Strict rules:
1. Use the provider documentation text included by the user as guidance.
2. Merge the user's short edit instruction into the prompt.
3. Preserve the original core visual intent and subject.
4. If generation mode uses a reference image, clearly state what should be preserved from the reference image and what should change.
5. If the target provider does not support reference image editing, convert reference-image intent into precise text-to-image description.
6. Return strict JSON only: {"request_id":"...","prompt":"..."}.
7. The request_id must exactly match the user's request_id.
8. The prompt value must be only the final prompt that should be sent to the image API.
9. Do not include explanations, markdown, titles, or quotes outside the JSON.
""".strip()

VIDEO_PROMPT_REFINE_SYSTEM_PROMPT = """
You are a video generation prompt adapter.

Your job is to rewrite the current video prompt for a specific target provider/model.

Strict rules:
1. Use the provider documentation text included by the user as guidance.
2. Merge the user's short edit instruction into the prompt.
3. Preserve the Asset plan's story intent, scene continuity, and SRT alignment.
4. Respect the input mode: text, first_frame, or first_last.
5. Respect the scene duration and avoid describing too many action beats for short clips.
6. Do not request subtitles, captions, watermarks, logos, UI text, or title cards in the base video.
7. Return strict JSON only: {"request_id":"...","prompt":"..."}.
8. The request_id must exactly match the user's request_id.
9. The prompt value must be only the final prompt that should be sent to the video API.
10. Do not include explanations, markdown, titles, or quotes outside the JSON.
""".strip()

HOST_PRODUCT_BUILDER_SYSTEM_PROMPT = """
你是 OpenCrew Rebuild V1 的 Host & Product Builder。

你的职责是把用户输入的简单提示词，结合 Tool Library 中的人物/产品一致性参考生成指南，扩写成可直接提交给图像模型的复杂提示词。

严格要求：
1. 必须优先遵守用户的简单提示词。
2. 必须吸收指南中的踩坑经验、硬约束和参考板结构。
3. kind=host 时，输出人物一致性参考板生成提示词，不要输出真实视频帧替换提示词。
4. kind=product 时，输出产品一致性参考板生成提示词，不要输出真实视频帧替换提示词。
5. 输出必须包含 reference image roles，明确多张参考图只作为身份/包装/场景角色锚点，不复制参考板以外的错误布局。
6. 必须包含负面约束，避免旧人物、旧产品、字幕、水印、泛化包装、广告海报感和合规敏感文案。
7. 返回严格 JSON：{"request_id":"...","prompt":"..."}。
8. request_id 必须与用户提供的完全一致。
9. prompt 必须是最终图像生成提示词本身。
10. 不要输出 Markdown、解释、标题、代码块或 JSON 以外的任何内容。
""".strip()


def provider_error_page_detail(value: str) -> str:
    text_value = str(value or "").strip()
    lowered = text_value[:8000].lower()
    if "<!doctype html" not in lowered and "<html" not in lowered:
        return ""
    if "error code 524" in lowered or "a timeout occurred" in lowered:
        return "Run Model returned a Cloudflare 524 timeout page. Retry after the OpenCode/provider tunnel recovers."
    if "cloudflare" in lowered or "5xx-error-landing" in lowered:
        return "Run Model returned a Cloudflare error page. Retry after the OpenCode/provider tunnel recovers."
    return "Run Model returned an HTML error page instead of model output."


def build_oc_rebuild_router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    repo = OCRebuildRepository(ctx.engine)
    analysis_repo = OpenClipRepository(ctx.engine)

    def safe_session(session_id: int) -> dict[str, Any]:
        row = ctx.session_repo.get(session_id)
        if not row:
            raise HTTPException(status_code=404, detail="Session not found")
        return row

    def get_task(task_id: int) -> dict[str, Any]:
        row = repo.get_task(task_id)
        if not row:
            raise HTTPException(status_code=404, detail="OC-Rebuild task not found")
        return row

    def get_analysis_task(task_id: int | None) -> dict[str, Any] | None:
        if not task_id:
            return None
        row = analysis_repo.get_task(int(task_id))
        if not row:
            raise HTTPException(status_code=404, detail="OC-Analysis task not found")
        return row

    def opencode_client_for(session_row: dict[str, Any]) -> OpenCodeSessionClient:
        base_url = str(ctx.get_setting("opencode.base_url") or "").strip()
        username = str(ctx.get_setting("opencode.username") or "").strip()
        password = str(ctx.get_setting("opencode.password") or "").strip()
        if not base_url or not username or not password:
            raise HTTPException(status_code=400, detail="OpenCode connection is incomplete. Finish Step 1 before using OC-Rebuild.")
        return OpenCodeSessionClient(base_url=base_url, username=username, password=password, directory=str(session_row["workspace_dir"]))

    def serialize_prompt_models(session_row: dict[str, Any]) -> dict[str, Any]:
        provider_payload = opencode_client_for(session_row).providers()
        connected = {str(item) for item in (provider_payload.get("connected") or []) if item}
        default_map = provider_payload.get("default") or {}
        items: list[dict[str, Any]] = []
        default_model = {"providerID": "", "modelID": ""}
        for provider in provider_payload.get("all") or []:
            provider_id = str(provider.get("id") or "").strip()
            if not provider_id or provider_id not in connected:
                continue
            for model in (provider.get("models") or {}).values():
                model_id = str((model or {}).get("id") or "").strip()
                if not model_id:
                    continue
                items.append({
                    "providerID": provider_id,
                    "providerName": str(provider.get("name") or provider_id),
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
        preferred = next((item for item in items if item["providerID"] == "openai" and item["modelID"] == "gpt-5.5"), None)
        if preferred:
            default_model = {"providerID": preferred["providerID"], "modelID": preferred["modelID"]}
        elif not default_model["providerID"] and items:
            default_model = {"providerID": str(items[0]["providerID"]), "modelID": str(items[0]["modelID"])}
        return {"items": items, "default_model": default_model}

    def resolve_model(session_row: dict[str, Any], provider: str, model_id: str, purpose: str) -> tuple[dict[str, str], dict[str, Any]]:
        payload = serialize_prompt_models(session_row)
        available = {(str(item["providerID"]), str(item["modelID"])) for item in payload["items"]}
        provider = provider.strip()
        model_id = model_id.strip()
        if provider and model_id:
            if (provider, model_id) not in available:
                raise HTTPException(status_code=400, detail=f"{purpose} model not found: {provider}/{model_id}")
            return {"providerID": provider, "modelID": model_id}, payload
        default_model = payload.get("default_model") or {}
        provider = str(default_model.get("providerID") or "")
        model_id = str(default_model.get("modelID") or "")
        if provider and model_id:
            return {"providerID": provider, "modelID": model_id}, payload
        raise HTTPException(status_code=400, detail=f"No OpenCode {purpose.lower()} models are available")

    def strategy_labels(values: dict[str, bool], labels: dict[str, str]) -> str:
        selected = [label for key, label in labels.items() if values.get(key)]
        return "、".join(selected) if selected else "未明确"

    def parse_strategy(value: Any) -> dict[str, bool]:
        if isinstance(value, dict):
            return {str(k): bool(v) for k, v in value.items()}
        if isinstance(value, str) and value.strip():
            try:
                parsed = json.loads(value)
                if isinstance(parsed, dict):
                    return {str(k): bool(v) for k, v in parsed.items()}
            except Exception:
                pass
        return {}

    def task_snapshot(task_row: dict[str, Any]) -> dict[str, Any]:
        return {
            "analysis_task_id": task_row.get("analysis_task_id"),
            "source_package_path": str(task_row.get("source_package_path") or "source_package.json"),
            "source_scheme": str(task_row.get("source_scheme") or "detail"),
            "target_topic": str(task_row.get("target_topic") or ""),
            "target_platform": str(task_row.get("target_platform") or ""),
            "aspect_ratio": str(task_row.get("aspect_ratio") or "9:16"),
            "target_count": int(task_row.get("target_count") or 1),
            "target_audience": str(task_row.get("target_audience") or ""),
            "product_info": str(task_row.get("product_info") or ""),
            "rebuild_goal": str(task_row.get("rebuild_goal") or ""),
            "preserve_strategy": parse_strategy(task_row.get("preserve_strategy_json")),
            "replace_strategy": parse_strategy(task_row.get("replace_strategy_json")),
            "visual_style": str(task_row.get("visual_style") or ""),
            "subtitle_style": str(task_row.get("subtitle_style") or ""),
            "title_style": str(task_row.get("title_style") or ""),
            "voice_style": str(task_row.get("voice_style") or ""),
            "batch_variables": str(task_row.get("batch_variables") or ""),
            "constraints": str(task_row.get("constraints") or ""),
            "simple_prompt": str(task_row.get("simple_prompt") or ""),
            "final_prompt": str(task_row.get("final_prompt") or ""),
        }

    def build_simple_prompt(task_row: dict[str, Any]) -> str:
        snap = task_snapshot(task_row)
        return f"""请根据下面的 Rebuild 需求，生成一份更清晰、结构化的重建意图说明，用于把参考视频拆解结果转成可复用的新视频生成意图。

参考视频使用“{snap['source_scheme']}”分镜方案。

目标主题：
{snap['target_topic']}

目标平台：
{snap['target_platform']}

目标比例：
{snap['aspect_ratio']}

生成数量：
{snap['target_count']}

目标受众：
{snap['target_audience']}

产品/服务：
{snap['product_info']}

重建目标：
{snap['rebuild_goal']}

保留策略：
{strategy_labels(snap['preserve_strategy'], PRESERVE_OPTIONS)}

替换策略：
{strategy_labels(snap['replace_strategy'], REPLACE_OPTIONS)}

视觉风格：
{snap['visual_style']}

字幕风格：
{snap['subtitle_style']}

标题风格：
{snap['title_style']}

声音风格：
{snap['voice_style']}

批量变量：
{snap['batch_variables']}

限制条件：
{snap['constraints']}

请只描述重建意图，不要生成分镜计划、素材任务、执行命令、工具名称、代码或文件路径。""".strip()

    def final_prompt_user_content(task_row: dict[str, Any]) -> str:
        snap = task_snapshot(task_row)
        return f"""Source Scheme: {snap['source_scheme']}
Target Topic: {snap['target_topic']}
Target Platform: {snap['target_platform']}
Aspect Ratio: {snap['aspect_ratio']}
Target Count: {snap['target_count']}
Target Audience: {snap['target_audience']}
Product Info: {snap['product_info']}
Rebuild Goal: {snap['rebuild_goal']}

Preserve Strategy:
{strategy_labels(snap['preserve_strategy'], PRESERVE_OPTIONS)}

Replace Strategy:
{strategy_labels(snap['replace_strategy'], REPLACE_OPTIONS)}

Visual Style:
{snap['visual_style']}

Subtitle Style:
{snap['subtitle_style']}

Title Style:
{snap['title_style']}

Voice Style:
{snap['voice_style']}

Batch Variables:
{snap['batch_variables']}

Constraints:
{snap['constraints']}

Simple Prompt:
{snap['simple_prompt']}""".strip()

    def serialize_task_detail(task_row: dict[str, Any]) -> dict[str, Any]:
        backfill_storyboard_copy_workspace(task_row)
        workspace = workspace_path(task_row)
        analysis_task = get_analysis_task(int(task_row.get("analysis_task_id") or 0) or None)
        return {
            "task": {**task_row, "preserve_strategy": parse_strategy(task_row.get("preserve_strategy_json")), "replace_strategy": parse_strategy(task_row.get("replace_strategy_json"))},
            "storyboard_phase2": storyboard_phase2_state(workspace),
            "analysis_task": analysis_task,
            "analysis_tasks": analysis_repo.list_task_summaries(),
            "versions": repo.list_versions(int(task_row["id"])),
            "attempts": repo.list_attempts(int(task_row["id"])),
            "options": {
                "source_scheme": ["detail", "balanced", "summary"],
                "target_platform": ["抖音", "视频号", "小红书", "TikTok", "YouTube Shorts"],
                "aspect_ratio": ["9:16", "1:1", "16:9"],
                "target_audience": ["中小企业老板", "运营负责人", "潜在客户", "知识付费从业者"],
                "rebuild_goal": ["复刻参考视频结构", "换主题批量生成", "生成可投放短视频素材", "生成复拍执行方案", "生成多行业变体"],
                "visual_style": ["科技感", "真实口播", "高级商业", "生活化", "电影感"],
                "subtitle_style": ["底部大字", "关键词高亮", "标题卡式", "原片同款"],
                "title_style": ["顶部强钩子", "信息流标题", "不保留标题"],
                "voice_style": ["年轻中文旁白", "专家口吻", "老板口吻", "不生成旁白"],
                "preserve_strategy": PRESERVE_OPTIONS,
                "replace_strategy": REPLACE_OPTIONS,
            },
        }

    def normalize_payload(payload: OCRebuildTaskUpdatePayload) -> dict[str, Any]:
        return {
            "analysis_task_id": int(payload.analysis_task_id or 0) or None,
            "source_package_path": payload.source_package_path.strip() or "source_package.json",
            "source_scheme": payload.source_scheme.strip() or "detail",
            "target_topic": payload.target_topic.strip(),
            "target_platform": payload.target_platform.strip(),
            "aspect_ratio": payload.aspect_ratio.strip() or "9:16",
            "target_count": max(1, int(payload.target_count or 1)),
            "target_audience": payload.target_audience.strip(),
            "product_info": payload.product_info.strip(),
            "rebuild_goal": payload.rebuild_goal.strip(),
            "preserve_strategy_json": json.dumps(payload.preserve_strategy or {}, ensure_ascii=False),
            "replace_strategy_json": json.dumps(payload.replace_strategy or {}, ensure_ascii=False),
            "visual_style": payload.visual_style.strip(),
            "subtitle_style": payload.subtitle_style.strip(),
            "title_style": payload.title_style.strip(),
            "voice_style": payload.voice_style.strip(),
            "batch_variables": payload.batch_variables.strip(),
            "constraints": payload.constraints.strip(),
            "simple_prompt": payload.simple_prompt.strip(),
            "final_prompt": payload.final_prompt.strip(),
            "prompt_model_provider": payload.prompt_model_provider.strip(),
            "prompt_model_id": payload.prompt_model_id.strip(),
            "run_model_provider": payload.run_model_provider.strip(),
            "run_model_id": payload.run_model_id.strip(),
        }

    def add_event(session_id: int, kind: str, payload: dict[str, Any]) -> None:
        ctx.session_event_service.add_event(session_id, kind, payload, workflow_id="oc_rebuild")

    def workspace_path(task_row: dict[str, Any]) -> Path:
        value = str(task_row.get("workspace_dir") or "").strip()
        if value:
            return Path(value)
        session_row = safe_session(int(task_row["session_id"]))
        return Path(str(session_row["workspace_dir"]))

    def copy_storyboard_tree_missing(source: Path, target: Path) -> None:
        if not source.exists() or not source.is_dir():
            return
        target.mkdir(parents=True, exist_ok=True)
        for child in source.iterdir():
            if child.name == ".DS_Store":
                continue
            dst = target / child.name
            if child.is_dir():
                copy_storyboard_tree_missing(child, dst)
            elif child.is_file() and not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(child, dst)

    def backfill_storyboard_copy_workspace(task_row: dict[str, Any]) -> None:
        workspace = workspace_path(task_row)
        meta_path = workspace / "storyboard_meta.json"
        if not meta_path.exists():
            return
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(meta, dict) or meta.get("source_type") != "rebuild_copy":
            return
        phase2 = meta.get("phase2_refresh") if isinstance(meta.get("phase2_refresh"), dict) else {}
        if phase2.get("suppress_plan_d_prompt_autobuild") or phase2.get("boundary") == "storyboard_to_reference_ready_phase2":
            return
        source_session_id = int(meta.get("copied_from_rebuild_session_id") or 0)
        if not source_session_id:
            return
        source_workspace = Path(str(safe_session(source_session_id).get("workspace_dir") or ""))
        if not source_workspace.exists():
            return
        for child in source_workspace.iterdir():
            if child.name in STORYBOARD_COPY_FILE_DENYLIST:
                continue
            dst = workspace / child.name
            if child.is_dir():
                if child.name in STORYBOARD_COPY_WORKSPACE_DIRS:
                    copy_storyboard_tree_missing(child, dst)
            elif child.is_file() and not dst.exists():
                shutil.copy2(child, dst)

    def storyboard_phase2_state(workspace: Path) -> dict[str, Any]:
        meta = read_json_file(workspace / "storyboard_meta.json")
        phase2 = meta.get("phase2_refresh") if isinstance(meta.get("phase2_refresh"), dict) else {}
        if not phase2:
            return {}
        return {
            "status": phase2.get("status") or "",
            "boundary": phase2.get("boundary") or "",
            "storyboard_images_role": phase2.get("storyboard_images_role") or "",
            "final_images": phase2.get("final_images") or "",
            "suppress_plan_d_prompt_autobuild": bool(phase2.get("suppress_plan_d_prompt_autobuild")),
            "no_source_video_required": phase2.get("boundary") == "storyboard_to_reference_ready_phase2",
        }

    def provider_key_from_row(kind: str, mapping: Any, provider: str) -> str:
        return load_stored_key(ctx, kind, provider)

    def record_local_usage(provider: str, model: str, modality: str, started: float, status: str = "ok", units: dict[str, Any] | None = None, error_code: str = "") -> None:
        ctx.local_usage.record(
            provider=provider,
            model_id=model,
            modality=modality,
            proxy_policy=resolve_endpoint(provider, model, modality, "").proxy_policy,
            status=status,
            units=units or {},
            error_code=error_code,
            started_at=int(started * 1000),
            finished_at=now_ms(),
        )

    def load_active_image_selection() -> dict[str, str]:
        ensure_table(ctx)
        with ctx.engine.begin() as conn:
            row = conn.execute(text(f"""
SELECT provider, model, api_key_ref, api_key_ciphertext FROM {CONFIG_TABLE}
WHERE kind = 'image' AND active = TRUE AND enabled = TRUE
LIMIT 1
""")).first()
        if not row:
            raise HTTPException(status_code=400, detail="No active image model is configured in Connection")
        mapping = row._mapping
        provider = str(mapping.get("provider") or "").strip()
        model = str(mapping.get("model") or "").strip()
        return {"provider": provider, "model": model}

    def load_active_image_config() -> dict[str, str]:
        selection = load_active_image_selection()
        provider = selection["provider"]
        model = selection["model"]
        api_key = provider_key_from_row("image", {}, provider)
        if not api_key:
            raise HTTPException(status_code=400, detail=f"Active image model API key is missing in Connection: {provider}/{model}")
        return {"provider": provider, "model": model, "api_key": api_key}

    def active_image_model_public() -> dict[str, str]:
        try:
            config = load_active_image_config()
            return {"provider": config["provider"], "model": config["model"]}
        except HTTPException as exc:
            return {"provider": "", "model": "", "error": str(exc.detail)}

    def builder_kind_dir(kind: str) -> str:
        return "host" if kind == "host" else "product"

    def builder_output_name(kind: str) -> str:
        return "HOST.png" if kind == "host" else "PRODUCT.png"

    def builder_root(workspace: Path) -> Path:
        return workspace / "consistency_references"

    def builder_config_path(workspace: Path) -> Path:
        return builder_root(workspace) / "config.json"

    def builder_section_dir(workspace: Path, kind: str) -> Path:
        return builder_root(workspace) / builder_kind_dir(kind)

    def builder_state_path(workspace: Path, kind: str) -> Path:
        return builder_section_dir(workspace, kind) / f"{builder_kind_dir(kind)}_reference_manifest.json"

    def builder_prompt_path(workspace: Path, kind: str, name: str) -> Path:
        return builder_section_dir(workspace, kind) / "prompts" / name

    def read_json_file(path: Path) -> dict[str, Any]:
        if not path.exists() or not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def write_json_file(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(path)
        return payload

    def builder_rel(workspace: Path, path: Path) -> str:
        return str(path.resolve().relative_to(workspace.resolve()))

    def resolve_workspace_rel(workspace: Path, rel_path: str) -> Path:
        value = str(rel_path or "").strip()
        if not value:
            raise HTTPException(status_code=400, detail="Workspace path is required")
        path = workspace / value
        try:
            resolved = path.resolve()
            if not str(resolved).startswith(str(workspace.resolve())):
                raise HTTPException(status_code=400, detail="Path must stay inside the task workspace")
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"Workspace file not found: {value}")
        if not resolved.exists() or not resolved.is_file():
            raise HTTPException(status_code=404, detail=f"Workspace file not found: {value}")
        return resolved

    def resolve_workspace_rel_for_write(workspace: Path, rel_path: str) -> Path:
        value = str(rel_path or "").strip()
        if not value:
            raise HTTPException(status_code=400, detail="Workspace path is required")
        resolved = (workspace / value).resolve()
        if not str(resolved).startswith(str(workspace.resolve())):
            raise HTTPException(status_code=400, detail="Path must stay inside the task workspace")
        return resolved

    def safe_upload_name(filename: str, fallback: str) -> str:
        suffix = Path(filename or "").suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
            suffix = ".png"
        stem = re.sub(r"[^a-zA-Z0-9_-]+", "_", Path(filename or fallback).stem).strip("_") or fallback
        return f"{stem[:60]}_{now_ms()}_{uuid.uuid4().hex[:6]}{suffix}"

    def safe_audio_upload_name(filename: str, fallback: str, content: bytes, content_type: str = "") -> str:
        suffix = Path(filename or "").suffix.lower()
        allowed = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".webm"}
        if suffix not in allowed:
            mime = str(content_type or "").split(";", 1)[0].strip().lower()
            guessed = (mimetypes.guess_extension(mime) or "").lower()
            suffix = {
                ".wave": ".wav",
                ".x-wav": ".wav",
                ".mpga": ".mp3",
                ".oga": ".ogg",
            }.get(guessed, guessed)
        if suffix not in allowed:
            if content.startswith(b"RIFF") and content[8:12] == b"WAVE":
                suffix = ".wav"
            elif content.startswith(b"ID3"):
                suffix = ".mp3"
            elif content.startswith(b"fLaC"):
                suffix = ".flac"
        if suffix not in allowed:
            raise HTTPException(status_code=400, detail="Reference audio must be an audio file")
        stem = re.sub(r"[^a-zA-Z0-9_-]+", "_", Path(filename or fallback).stem).strip("_") or fallback
        return f"{stem[:60]}_{now_ms()}_{uuid.uuid4().hex[:6]}{suffix}"

    def read_builder_section(workspace: Path, kind: str) -> dict[str, Any]:
        manifest = read_json_file(builder_state_path(workspace, kind))
        output_rel = str(manifest.get("output") or f"consistency_references/{builder_kind_dir(kind)}/{builder_output_name(kind)}")
        return {
            "kind": kind,
            "simple_prompt": str(manifest.get("simple_prompt") or ""),
            "final_prompt": str(manifest.get("final_prompt") or ""),
            "reference_images": manifest.get("reference_images") if isinstance(manifest.get("reference_images"), list) else [],
            "output": output_rel if (workspace / output_rel).exists() else "",
            "manifest": manifest,
        }

    def write_builder_section(workspace: Path, kind: str, patch: dict[str, Any]) -> dict[str, Any]:
        current = read_builder_section(workspace, kind).get("manifest") or {}
        next_manifest = {**current, **patch, "kind": kind, "updated_at": now_ms()}
        if "reference_images" not in next_manifest or not isinstance(next_manifest.get("reference_images"), list):
            next_manifest["reference_images"] = []
        write_json_file(builder_state_path(workspace, kind), next_manifest)
        return read_builder_section(workspace, kind)

    def read_consistency_guide() -> str:
        if not CONSISTENCY_REFERENCE_GUIDE_PATH.exists():
            raise HTTPException(status_code=500, detail=f"Consistency guide not found: {CONSISTENCY_REFERENCE_GUIDE_PATH}")
        return CONSISTENCY_REFERENCE_GUIDE_PATH.read_text(encoding="utf-8")[:50000]

    def builder_reference_summary(workspace: Path, refs: list[str]) -> str:
        lines = []
        for index, rel in enumerate(refs, start=1):
            try:
                path = resolve_workspace_rel(workspace, rel)
                lines.append(f"{index}. {rel} ({mimetypes.guess_type(path.name)[0] or 'image'})")
            except HTTPException:
                lines.append(f"{index}. {rel} (missing)")
        return "\n".join(lines) if lines else "(No uploaded reference images.)"

    def serialize_host_product_builder(task_row: dict[str, Any]) -> dict[str, Any]:
        backfill_storyboard_copy_workspace(task_row)
        workspace = workspace_path(task_row)
        config = read_json_file(builder_config_path(workspace))
        return {
            "ok": True,
            "task_id": int(task_row["id"]),
            "session_id": int(task_row["session_id"]),
            "workspace_dir": str(workspace),
            "guide_path": str(CONSISTENCY_REFERENCE_GUIDE_PATH),
            "image_model": active_image_model_public(),
            "config": config,
            "host": read_builder_section(workspace, "host"),
            "product": read_builder_section(workspace, "product"),
        }

    def load_image_config(provider: str, model: str) -> dict[str, str]:
        provider = str(provider or "").strip()
        model = str(model or "").strip()
        if not provider:
            return load_active_image_config()
        ensure_table(ctx)
        with ctx.engine.begin() as conn:
            row = conn.execute(text(f"""
SELECT provider, model, api_key_ref, api_key_ciphertext FROM {CONFIG_TABLE}
WHERE kind = 'image' AND provider = :provider AND enabled = TRUE
LIMIT 1
"""), {"provider": provider}).first()
        if not row:
            raise HTTPException(status_code=400, detail=f"Image provider is not configured or enabled: {provider}")
        mapping = row._mapping
        stored_model = str(mapping.get("model") or "").strip()
        selected_model = model or stored_model
        api_key = provider_key_from_row("image", mapping, provider)
        if not api_key:
            raise HTTPException(status_code=400, detail=f"Image provider API key is missing in Connection: {provider}/{selected_model}")
        return {"provider": str(mapping.get("provider") or provider), "model": selected_model, "api_key": api_key}

    def image_provider_supports_references(provider: str) -> bool:
        return str(provider or "").strip().lower() in {"openai", "gemini"}

    def load_reference_image_config(provider: str, model: str) -> tuple[dict[str, str], str]:
        selected_provider = str(provider or "").strip()
        selected_model = str(model or "").strip()
        fallback_from = ""
        if not selected_provider:
            try:
                selection = load_active_image_selection()
                selected_provider = selection["provider"]
                selected_model = selection["model"]
            except HTTPException:
                selected_provider = ""
                selected_model = ""
        if selected_provider:
            fallback_from = f"{selected_provider}/{selected_model}".rstrip("/")
        candidates: list[tuple[str, str]] = []
        if selected_provider and image_provider_supports_references(selected_provider):
            candidates.append((selected_provider, selected_model))
        for fallback_provider in ("openai", "gemini"):
            if not any(candidate_provider.lower() == fallback_provider for candidate_provider, _ in candidates):
                candidates.append((fallback_provider, ""))
        errors: list[str] = []
        for candidate_provider, candidate_model in candidates:
            try:
                config = load_image_config(candidate_provider, candidate_model)
            except HTTPException as exc:
                errors.append(f"{candidate_provider}: {exc.detail}")
                continue
            if image_provider_supports_references(config["provider"]):
                actual = f"{config['provider']}/{config['model']}".rstrip("/")
                return config, "" if not fallback_from or actual == fallback_from else fallback_from
        error_suffix = f" Tried: {'; '.join(errors)}" if errors else ""
        selected_label = fallback_from or "none"
        raise HTTPException(status_code=400, detail=f"Reference image generation requires an OpenAI or Gemini image provider with API key in Connection. Selected image model is {selected_label}.{error_suffix}")

    def load_video_config(provider: str, model: str) -> dict[str, str]:
        ensure_table(ctx)
        with ctx.engine.begin() as conn:
            row = conn.execute(text(f"""
SELECT provider, model, api_key_ref, api_key_ciphertext FROM {CONFIG_TABLE}
WHERE kind = 'video' AND provider = :provider AND enabled = TRUE
LIMIT 1
"""), {"provider": provider}).first()
        if not row:
            raise HTTPException(status_code=400, detail=f"Video provider is not configured or enabled: {provider}")
        mapping = row._mapping
        stored_model = str(mapping.get("model") or "").strip()
        return {"provider": str(mapping.get("provider") or provider), "model": model.strip() or stored_model, "api_key": provider_key_from_row("video", mapping, provider)}

    def load_local_prompt_docs(provider: str) -> dict[str, str]:
        docs_url = IMAGE_PROMPT_DOC_URLS.get(provider, "")
        if not docs_url:
            raise HTTPException(status_code=400, detail=f"No prompt docs URL is configured for {provider}")
        path = IMAGE_PROMPT_LOCAL_DOC_DIR / f"{provider}.md"
        docs_text = path.read_text(encoding="utf-8")[:30000] if path.exists() and path.is_file() else ""
        return {"docs_url": docs_url, "docs_text": docs_text, "docs_source": "local_summary", "docs_path": str(path), "docs_fetched_realtime": False}

    def load_local_video_prompt_docs(provider: str) -> dict[str, str]:
        docs_url = VIDEO_PROMPT_DOC_URLS.get(provider, "")
        if not docs_url:
            raise HTTPException(status_code=400, detail=f"No video prompt docs URL is configured for {provider}")
        path = VIDEO_PROMPT_LOCAL_DOC_DIR / f"{provider}.md"
        docs_text = path.read_text(encoding="utf-8")[:30000] if path.exists() and path.is_file() else ""
        return {"docs_url": docs_url, "docs_text": docs_text, "docs_source": "local_summary", "docs_path": str(path), "docs_fetched_realtime": False}

    def asset_image_target(task_row: dict[str, Any], shot_id: str, scene_mark_id: str, role: str) -> tuple[dict[str, Any], dict[str, Any], Path, Path | None]:
        workspace = workspace_path(task_row)
        asset_tasks_path = workspace / "asset_tasks.json"
        if not asset_tasks_path.exists() or not asset_tasks_path.is_file():
            raise HTTPException(status_code=404, detail="asset_tasks.json not found. Run 04_1 first.")
        try:
            asset_tasks = json.loads(asset_tasks_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Unable to read asset_tasks.json: {exc}") from exc
        image_types = {"single": ["image_regenerate_single"], "first": ["image_regenerate_first", "image_regenerate_single"], "last": ["image_regenerate_last"]}.get(role, ["image_regenerate_single"])
        tasks = asset_tasks.get("tasks") if isinstance(asset_tasks.get("tasks"), list) else []
        target = next((item for item in tasks if isinstance(item, dict) and str(item.get("shot_id") or "") == shot_id and str(item.get("scene_mark_id") or "") == scene_mark_id and str(item.get("type") or "") in image_types), None)
        if not target:
            raise HTTPException(status_code=404, detail="Matching image asset task not found")
        inputs = target.get("input") if isinstance(target.get("input"), dict) else {}
        output_rel = str(target.get("output") or "").strip()
        if not output_rel:
            raise HTTPException(status_code=400, detail="Image asset task has no output path")
        reference_path = None
        reference_rel = str(inputs.get("reference_frame") or "").strip()
        if reference_rel:
            analysis_task = get_analysis_task(int(task_row.get("analysis_task_id") or 0) or None)
            analysis_workspace = Path(str((analysis_task or {}).get("workspace_dir") or workspace))
            candidate = analysis_workspace / reference_rel
            if not candidate.exists():
                candidate = workspace / reference_rel
            if candidate.exists() and candidate.is_file():
                reference_path = candidate
        return asset_tasks, target, asset_tasks_path, reference_path

    def asset_video_target(task_row: dict[str, Any], shot_id: str, scene_mark_id: str) -> tuple[dict[str, Any], dict[str, Any], Path]:
        workspace = workspace_path(task_row)
        asset_tasks_path = workspace / "asset_tasks.json"
        if not asset_tasks_path.exists() or not asset_tasks_path.is_file():
            raise HTTPException(status_code=404, detail="asset_tasks.json not found. Run 04_1 first.")
        try:
            asset_tasks = json.loads(asset_tasks_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Unable to read asset_tasks.json: {exc}") from exc
        tasks = asset_tasks.get("tasks") if isinstance(asset_tasks.get("tasks"), list) else []
        target = next((item for item in tasks if isinstance(item, dict) and str(item.get("shot_id") or "") == shot_id and str(item.get("scene_mark_id") or "") == scene_mark_id and str(item.get("type") or "") in {"first_last_image_to_video", "single_image_to_video"}), None)
        if not target:
            raise HTTPException(status_code=404, detail="Matching video asset task not found")
        return asset_tasks, target, asset_tasks_path

    def workspace_file(workspace: Path, value: str) -> Path | None:
        rel = value.strip()
        if not rel:
            return None
        candidate = workspace / rel
        try:
            resolved = candidate.resolve()
            if not str(resolved).startswith(str(workspace.resolve())):
                return None
        except FileNotFoundError:
            return None
        return candidate if candidate.exists() and candidate.is_file() else None

    def task_reference_file(task_row: dict[str, Any], value: str) -> Path | None:
        workspace = workspace_path(task_row)
        found = workspace_file(workspace, value)
        if found:
            return found
        rel = value.strip()
        if not rel:
            return None
        analysis_task = get_analysis_task(int(task_row.get("analysis_task_id") or 0) or None)
        analysis_workspace = Path(str((analysis_task or {}).get("workspace_dir") or workspace))
        candidate = analysis_workspace / rel
        try:
            resolved = candidate.resolve()
            if not str(resolved).startswith(str(analysis_workspace.resolve())):
                return None
        except FileNotFoundError:
            return None
        return candidate if candidate.exists() and candidate.is_file() else None

    def render_local_video_preview(workspace: Path, output_rel: str, first_image_rel: str, duration: float | None) -> dict[str, Any]:
        output_path = workspace / output_rel
        output_path.parent.mkdir(parents=True, exist_ok=True)
        seconds = max(0.5, min(float(duration or 3.0), 30.0))
        try:
            import imageio_ffmpeg
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"ffmpeg is unavailable for video preview rendering: {exc}") from exc
        source = workspace_file(workspace, first_image_rel)
        if source:
            cmd = [ffmpeg, "-y", "-loop", "1", "-i", str(source), "-t", str(seconds), "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,format=yuv420p", "-r", "24", "-movflags", "+faststart", str(output_path)]
        else:
            cmd = [ffmpeg, "-y", "-f", "lavfi", "-i", "color=c=black:s=1080x1920:r=24", "-t", str(seconds), "-movflags", "+faststart", str(output_path)]
        completed = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if completed.returncode != 0:
            raise HTTPException(status_code=500, detail=(completed.stderr or "ffmpeg failed")[:2000])
        return {"output_path": str(output_path), "duration": seconds, "local_preview": True}

    def image_inline_payload(path: Path | None) -> dict[str, str] | None:
        if not path:
            return None
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        try:
            from PIL import Image
            with Image.open(path) as image:
                detected_format = (image.format or "").lower()
            if detected_format in {"jpeg", "jpg"}:
                mime = "image/jpeg"
            elif detected_format == "png":
                mime = "image/png"
            elif detected_format == "webp":
                mime = "image/webp"
        except Exception:
            pass
        return {"mimeType": mime, "bytesBase64Encoded": base64.b64encode(path.read_bytes()).decode("ascii")}

    def gemini_image_payload(path: Path | None) -> dict[str, Any] | None:
        inline = image_inline_payload(path)
        if not inline:
            return None
        return {"mimeType": inline["mimeType"], "bytesBase64Encoded": inline["bytesBase64Encoded"]}

    def normalized_image_reference(path: Path | None, output_path: Path, width: int, height: int) -> Path | None:
        if not path:
            return None
        from PIL import Image, ImageOps
        target = output_path.with_name(f"{output_path.stem}_{width}x{height}_reference.jpg")
        with Image.open(path) as image:
            normalized = ImageOps.fit(image.convert("RGB"), (width, height), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
            normalized.save(target, "JPEG", quality=95)
        return target

    def first_video_url(payload: Any) -> str:
        if isinstance(payload, dict):
            for key in ("url", "video_url", "download_url", "uri"):
                value = payload.get(key)
                if isinstance(value, str) and value.startswith("http"):
                    return value
            output = payload.get("output")
            if isinstance(output, dict):
                found = first_video_url(output)
                if found:
                    return found
            response = payload.get("response")
            if isinstance(response, dict):
                found = first_video_url(response)
                if found:
                    return found
            for value in payload.values():
                found = first_video_url(value)
                if found:
                    return found
        if isinstance(payload, list):
            for value in payload:
                found = first_video_url(value)
                if found:
                    return found
        return ""

    def operation_done(payload: dict[str, Any]) -> bool:
        status = str(payload.get("status") or payload.get("task_status") or "").upper()
        return bool(payload.get("done")) or status in {"SUCCEEDED", "SUCCESS", "COMPLETED", "SUCCESSFUL"}

    def operation_failed(payload: dict[str, Any]) -> str:
        status = str(payload.get("status") or payload.get("task_status") or "").upper()
        if status in {"FAILED", "FAILURE", "CANCELED", "CANCELLED"}:
            return json.dumps(payload, ensure_ascii=False)[:1200]
        error = payload.get("error")
        return json.dumps(error, ensure_ascii=False)[:1200] if error else ""

    def run_video_candidate(task_id: int, request_payload: dict[str, Any], prompt: str, config: dict[str, str], output_rel: str, first_image_rel: str, last_image_rel: str, input_mode: str, duration: float | None) -> dict[str, Any]:
        task_row = get_task(task_id)
        workspace = workspace_path(task_row)
        session_id = int(task_row["session_id"])
        output_path = workspace / output_rel
        output_path.parent.mkdir(parents=True, exist_ok=True)
        provider = config["provider"]
        model = config["model"]
        api_key = config["api_key"]
        if not api_key:
            raise HTTPException(status_code=400, detail=f"Video provider API key is missing: {provider}")
        first_image = workspace_file(workspace, first_image_rel)
        last_image = workspace_file(workspace, last_image_rel)
        call_started = time.time()
        seconds = provider_video_seconds(provider, model, duration)
        call_detail = {**request_payload, "provider": provider, "model": model, "method": "POST", "input_mode": input_mode, "duration": duration, "effective_duration_seconds": seconds, "first_image_path": str(first_image) if first_image else "", "last_image_path": str(last_image) if last_image else "", "workspace_dir": str(workspace), "output": output_rel, "output_path": str(output_path), "prompt_preview": prompt[:1000], "prompt_length": len(prompt), "temporary": True, "writes_asset_json": False}
        add_event(session_id, "ocrebuild.asset_video.provider_call.started", call_detail)
        video_url = ""
        if provider == "gemini":
            endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(model, safe='')}:predictLongRunning?key={urllib.parse.quote(api_key, safe='')}"
            instance: dict[str, Any] = {"prompt": prompt}
            inline = gemini_image_payload(first_image)
            if inline:
                instance["image"] = inline
            last_inline = gemini_image_payload(last_image)
            if last_inline:
                instance["lastFrame"] = last_inline
            operation = post_json_request(endpoint, {"instances": [instance], "parameters": {"sampleCount": 1, "durationSeconds": seconds, "aspectRatio": "9:16"}}, {})
            op_name = str(operation.get("name") or "")
            if not op_name:
                raise HTTPException(status_code=502, detail=f"Gemini video response did not include operation name: {json.dumps(operation, ensure_ascii=False)[:1000]}")
            poll_url = f"https://generativelanguage.googleapis.com/v1beta/{op_name}?key={urllib.parse.quote(api_key, safe='')}" if not op_name.startswith("http") else op_name
            deadline = time.time() + 900
            while time.time() < deadline:
                polled = get_json_request(poll_url, {})
                failure = operation_failed(polled)
                if failure:
                    raise HTTPException(status_code=502, detail=f"Gemini video generation failed: {failure}")
                if operation_done(polled):
                    video_url = first_video_url(polled)
                    break
                time.sleep(5)
            if not video_url:
                raise HTTPException(status_code=502, detail="Gemini video generation completed without a downloadable video URL")
            download_binary(video_url, output_path, {"x-goog-api-key": api_key})
        elif provider == "wan":
            input_payload: dict[str, Any] = {"prompt": prompt}
            if first_image:
                media_type = "reference_image" if "r2v" in model else "first_frame"
                input_payload["media"] = [{"type": media_type, "url": dashscope_upload_file(api_key, model, first_image)}]
            if last_image:
                input_payload["last_frame_url"] = dashscope_upload_file(api_key, model, last_image)
            started = post_json_request("https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis", {"model": model, "input": input_payload, "parameters": {"duration": seconds}}, {"Authorization": f"Bearer {api_key}", "X-DashScope-Async": "enable", "X-DashScope-OssResourceResolve": "enable"})
            task_id_value = str(((started.get("output") or {}).get("task_id") or started.get("task_id") or ""))
            if not task_id_value:
                raise HTTPException(status_code=502, detail=f"Wan response did not include task_id: {json.dumps(started, ensure_ascii=False)[:1000]}")
            poll_url = f"https://dashscope.aliyuncs.com/api/v1/tasks/{urllib.parse.quote(task_id_value, safe='')}"
            deadline = time.time() + 900
            while time.time() < deadline:
                polled = get_json_request(poll_url, {"Authorization": f"Bearer {api_key}"})
                failure = operation_failed(polled.get("output") if isinstance(polled.get("output"), dict) else polled)
                if failure:
                    raise HTTPException(status_code=502, detail=f"Wan video generation failed: {failure}")
                if operation_done(polled.get("output") if isinstance(polled.get("output"), dict) else polled):
                    video_url = first_video_url(polled)
                    break
                time.sleep(5)
            if not video_url:
                raise HTTPException(status_code=502, detail="Wan video generation completed without a downloadable video URL")
            download_binary(video_url, output_path)
        elif provider == "openai":
            normalized_first_image = normalized_image_reference(first_image, output_path, 720, 1280)
            payload: dict[str, Any] = {"model": model, "prompt": prompt, "seconds": str(seconds), "size": "720x1280"}
            inline = image_inline_payload(normalized_first_image)
            if inline:
                payload["input_reference"] = {"image_url": f"data:{inline['mimeType']};base64,{inline['bytesBase64Encoded']}"}
            started = post_json_request("https://api.openai.com/v1/videos", payload, {"Authorization": f"Bearer {api_key}"}, timeout=180)
            video_id = str(started.get("id") or "")
            if not video_id:
                raise HTTPException(status_code=502, detail=f"OpenAI video response did not include id: {json.dumps(started, ensure_ascii=False)[:1000]}")
            deadline = time.time() + 900
            while time.time() < deadline:
                polled = get_json_request(f"https://api.openai.com/v1/videos/{urllib.parse.quote(video_id, safe='')}", {"Authorization": f"Bearer {api_key}"})
                status = str(polled.get("status") or "").lower()
                if status in {"failed", "cancelled", "canceled"}:
                    raise HTTPException(status_code=502, detail=f"OpenAI video generation failed: {json.dumps(polled, ensure_ascii=False)[:1200]}")
                if status in {"completed", "succeeded", "success"}:
                    video_url = first_video_url(polled)
                    break
                time.sleep(5)
            if video_url:
                download_binary(video_url, output_path, {"Authorization": f"Bearer {api_key}"})
            else:
                download_binary(f"https://api.openai.com/v1/videos/{urllib.parse.quote(video_id, safe='')}/content", output_path, {"Authorization": f"Bearer {api_key}"})
        elif provider == "xai":
            payload: dict[str, Any] = {"model": model, "prompt": prompt, "duration": seconds, "aspect_ratio": "9:16", "resolution": "720p"}
            inline = image_inline_payload(first_image)
            if inline:
                payload["image"] = {"url": f"data:{inline['mimeType']};base64,{inline['bytesBase64Encoded']}"}
            started = post_json_request("https://api.x.ai/v1/videos/generations", payload, {"Authorization": f"Bearer {api_key}"})
            video_id = str(started.get("request_id") or started.get("id") or ((started.get("data") or {}).get("id") if isinstance(started.get("data"), dict) else "") or "")
            if not video_id:
                video_url = first_video_url(started)
            deadline = time.time() + 900
            while not video_url and time.time() < deadline:
                polled = get_json_request(f"https://api.x.ai/v1/videos/{urllib.parse.quote(video_id, safe='')}", {"Authorization": f"Bearer {api_key}"})
                failure = operation_failed(polled)
                if failure:
                    raise HTTPException(status_code=502, detail=f"xAI video generation failed: {failure}")
                if str(polled.get("status") or "").lower() == "done" or operation_done(polled):
                    video_url = first_video_url(polled)
                    break
                time.sleep(5)
            if not video_url:
                raise HTTPException(status_code=502, detail="xAI video generation completed without a downloadable video URL")
            download_binary(video_url, output_path, {"Authorization": f"Bearer {api_key}"})
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported video provider: {provider}")
        elapsed_seconds = round(time.time() - call_started, 3)
        result = {**call_detail, "ok": True, "output": output_rel, "output_path": str(output_path), "elapsed_seconds": elapsed_seconds, "video_url": video_url, "local_preview": False}
        record_local_usage(provider, model, "video", call_started, units={"second": int(seconds or 0)})
        add_event(session_id, "ocrebuild.asset_video.generated", result)
        return result

    def run_shot_multi_reference_video_candidate(task_id: int, request_payload: dict[str, Any], prompt: str, config: dict[str, str], output_rel: str, reference_image_rels: list[str], reference_video_rels: list[str], duration: float | None) -> dict[str, Any]:
        task_row = get_task(task_id)
        workspace = workspace_path(task_row)
        session_id = int(task_row["session_id"])
        output_path = workspace / output_rel
        output_path.parent.mkdir(parents=True, exist_ok=True)
        provider = config["provider"]
        model = config["model"]
        api_key = config["api_key"]
        if not api_key:
            raise HTTPException(status_code=400, detail=f"Video provider API key is missing: {provider}")
        references = [path for path in (task_reference_file(task_row, item) for item in reference_image_rels) if path]
        reference_videos = [path for path in (task_reference_file(task_row, item) for item in reference_video_rels) if path]
        if reference_videos and provider != "wan":
            raise HTTPException(status_code=400, detail=f"Reference video input is currently supported only for Wan R2V, not {provider}/{model}")
        if not references and not reference_videos:
            raise HTTPException(status_code=400, detail="At least one reference image or reference video is required")
        call_started = time.time()
        seconds = 8 if provider == "gemini" and model in {"veo-3.1-generate-preview", "veo-3.1-fast-generate-preview"} else provider_video_seconds(provider, model, duration)
        call_detail = {**request_payload, "provider": provider, "model": model, "method": "POST", "input_mode": "multi_reference", "duration": duration, "effective_duration_seconds": seconds, "reference_image_paths": [str(item) for item in references], "reference_image_count": len(references), "reference_video_paths": [str(item) for item in reference_videos], "reference_video_count": len(reference_videos), "workspace_dir": str(workspace), "output": output_rel, "output_path": str(output_path), "prompt_preview": prompt[:1000], "prompt_length": len(prompt), "temporary": True, "writes_asset_json": False}
        add_event(session_id, "ocrebuild.shot_video.provider_call.started", call_detail)
        video_url = ""
        if provider == "wan":
            media = [{"type": "reference_image", "url": dashscope_upload_file(api_key, model, image_path)} for image_path in references]
            media.extend({"type": "reference_video", "url": dashscope_upload_file(api_key, model, video_path)} for video_path in reference_videos)
            input_payload = {"prompt": prompt, "media": media}
            started = post_json_request("https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis", {"model": model, "input": input_payload, "parameters": {"duration": seconds}}, {"Authorization": f"Bearer {api_key}", "X-DashScope-Async": "enable", "X-DashScope-OssResourceResolve": "enable"})
            task_id_value = str(((started.get("output") or {}).get("task_id") or started.get("task_id") or ""))
            if not task_id_value:
                raise HTTPException(status_code=502, detail=f"Wan response did not include task_id: {json.dumps(started, ensure_ascii=False)[:1000]}")
            poll_url = f"https://dashscope.aliyuncs.com/api/v1/tasks/{urllib.parse.quote(task_id_value, safe='')}"
            deadline = time.time() + 900
            while time.time() < deadline:
                polled = get_json_request(poll_url, {"Authorization": f"Bearer {api_key}"})
                failure = operation_failed(polled.get("output") if isinstance(polled.get("output"), dict) else polled)
                if failure:
                    raise HTTPException(status_code=502, detail=f"Wan video generation failed: {failure}")
                if operation_done(polled.get("output") if isinstance(polled.get("output"), dict) else polled):
                    video_url = first_video_url(polled)
                    break
                time.sleep(5)
            if not video_url:
                raise HTTPException(status_code=502, detail="Wan video generation completed without a downloadable video URL")
            download_binary(video_url, output_path)
        elif provider == "xai":
            payload = {"model": model, "prompt": prompt, "duration": seconds, "aspect_ratio": "9:16", "resolution": "720p", "reference_images": []}
            for image_path in references:
                inline = image_inline_payload(image_path)
                if inline:
                    payload["reference_images"].append({"url": f"data:{inline['mimeType']};base64,{inline['bytesBase64Encoded']}"})
            started = post_json_request("https://api.x.ai/v1/videos/generations", payload, {"Authorization": f"Bearer {api_key}"})
            video_id = str(started.get("request_id") or started.get("id") or ((started.get("data") or {}).get("id") if isinstance(started.get("data"), dict) else "") or "")
            if not video_id:
                video_url = first_video_url(started)
            deadline = time.time() + 900
            while not video_url and time.time() < deadline:
                polled = get_json_request(f"https://api.x.ai/v1/videos/{urllib.parse.quote(video_id, safe='')}", {"Authorization": f"Bearer {api_key}"})
                failure = operation_failed(polled)
                if failure:
                    raise HTTPException(status_code=502, detail=f"xAI video generation failed: {failure}")
                if str(polled.get("status") or "").lower() == "done" or operation_done(polled):
                    video_url = first_video_url(polled)
                    break
                time.sleep(5)
            if not video_url:
                raise HTTPException(status_code=502, detail="xAI video generation completed without a downloadable video URL")
            download_binary(video_url, output_path, {"Authorization": f"Bearer {api_key}"})
        elif provider == "gemini" and model in {"veo-3.1-generate-preview", "veo-3.1-fast-generate-preview"}:
            try:
                from google import genai  # type: ignore
                from google.genai import types  # type: ignore
            except Exception as exc:
                raise HTTPException(status_code=500, detail="google-genai package is required for Veo multi-reference generation") from exc
            refs = [types.VideoGenerationReferenceImage(image=types.Image.from_file(location=str(image_path)), referenceType="asset") for image_path in references[:3]]
            client = genai.Client(api_key=api_key)
            operation = client.models.generate_videos(model=model, prompt=prompt, config=types.GenerateVideosConfig(durationSeconds=8, aspectRatio="9:16", resolution="720p", personGeneration="allow_adult", referenceImages=refs))
            deadline = time.time() + 900
            while not getattr(operation, "done", False) and time.time() < deadline:
                time.sleep(5)
                operation = client.operations.get(operation)
            response = getattr(operation, "response", None)
            videos = getattr(response, "generated_videos", None) or getattr(response, "generatedVideos", None) or []
            video = getattr(videos[0], "video", None) if videos else None
            video_url = str(getattr(video, "uri", "") or "") if video else ""
            if not video_url:
                raise HTTPException(status_code=502, detail="Gemini video generation completed without a downloadable video URI")
            download_binary(video_url, output_path, {"x-goog-api-key": api_key})
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported multi-reference video model: {provider}/{model}")
        elapsed_seconds = round(time.time() - call_started, 3)
        result = {**call_detail, "ok": True, "output": output_rel, "output_path": str(output_path), "elapsed_seconds": elapsed_seconds, "video_url": video_url, "local_preview": False}
        add_event(session_id, "ocrebuild.shot_video.generated", result)
        return result

    def normalize_workflow_id(value: str) -> str:
        text_value = re.sub(r"[^a-zA-Z0-9_-]+", "_", value.strip())[:80]
        return text_value or f"imgwf_{now_ms()}"

    def workflow_output_rel(workflow_id: str, round_no: int, provider: str, variant: int) -> str:
        provider_id = re.sub(r"[^a-zA-Z0-9_-]+", "_", provider.strip() or "provider")[:40]
        suffix = f"variant_{variant}" if variant else provider_id
        return f"asset_image_workflows/{normalize_workflow_id(workflow_id)}/round_{round_no}/{suffix}.png"

    def video_workflow_output_rel(workflow_id: str, provider: str, index: int) -> str:
        provider_id = re.sub(r"[^a-zA-Z0-9_-]+", "_", provider.strip() or "provider")[:40]
        suffix = provider_id if index <= 1 else f"{provider_id}_{index}"
        return f"asset_video_workflows/{normalize_workflow_id(workflow_id)}/{suffix}.mp4"

    def tts_workflow_output_rel(workflow_id: str, scene_mark_id: str, provider: str, index: int) -> str:
        provider_id = re.sub(r"[^a-zA-Z0-9_-]+", "_", provider.strip() or "provider")[:40]
        scene_id = re.sub(r"[^a-zA-Z0-9_-]+", "_", scene_mark_id.strip() or "scene")[:80]
        ext = "mp3" if provider.strip() == "xai" else "wav"
        suffix = provider_id if index <= 1 else f"{provider_id}_{index}"
        return f"asset_tts_workflows/{normalize_workflow_id(workflow_id)}/{scene_id}/{suffix}.{ext}"

    def shot_tts_output_rel(workflow_id: str, provider: str, index: int) -> str:
        provider_id = re.sub(r"[^a-zA-Z0-9_-]+", "_", provider.strip() or "provider")[:40]
        ext = "mp3" if provider.strip() == "xai" else "wav"
        suffix = provider_id if index <= 1 else f"{provider_id}_{index}"
        return f"asset_tts_workflows/{normalize_workflow_id(workflow_id)}/shot_full/{suffix}.{ext}"

    def shot_tts_locked_output_rel(workflow_id: str, selected_output: str) -> str:
        source_suffix = Path(selected_output).suffix.lower() or ".wav"
        return f"asset_tts_workflows/{normalize_workflow_id(workflow_id)}/shot_full/locked{source_suffix}"

    def shot_tts_timeline_output_rel(workflow_id: str) -> str:
        return f"asset_tts_workflows/{normalize_workflow_id(workflow_id)}/shot_full/shot_tts_timeline.locked.json"

    def shot_tts_srt_output_rel(workflow_id: str) -> str:
        return f"asset_tts_workflows/{normalize_workflow_id(workflow_id)}/shot_full/shot.srt"

    def safe_workspace_rel(workspace: Path, value: str) -> tuple[str, Path]:
        rel_value = str(value or "").strip().lstrip("/")
        if not rel_value or Path(rel_value).is_absolute() or ".." in Path(rel_value).parts:
            raise HTTPException(status_code=400, detail="Locked TTS path must stay inside the task workspace")
        target = workspace / rel_value
        try:
            target.resolve().relative_to(workspace.resolve())
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Locked TTS path must stay inside the task workspace") from exc
        return rel_value, target

    def read_json_path(path: Path) -> dict[str, Any]:
        if not path.exists() or not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def locked_tts_cache_hit(workspace: Path, payload: OCRebuildAssetTTSComparePayload, prompt_item: dict[str, Any], workflow_id: str) -> dict[str, Any] | None:
        if not payload.use_locked_cache or not payload.locked_output.strip() or not payload.locked_manifest.strip() or not payload.locked_config_key.strip():
            return None
        output_rel, output_path = safe_workspace_rel(workspace, payload.locked_output)
        _, manifest_path = safe_workspace_rel(workspace, payload.locked_manifest)
        manifest = read_json_path(manifest_path)
        if not manifest or str(manifest.get("config_key") or "") != payload.locked_config_key:
            return None
        manifest_output = str(manifest.get("output") or output_rel).strip()
        if manifest_output != output_rel:
            return None
        if not output_path.exists() or not output_path.is_file() or output_path.stat().st_size <= 0:
            return None
        provider = str(manifest.get("provider") or prompt_item.get("provider") or "").strip()
        model_id = str(manifest.get("model") or prompt_item.get("model") or "").strip()
        voice_id = str(manifest.get("voice_id") or prompt_item.get("voice_id") or "").strip()
        duration = float(manifest.get("duration_seconds") or manifest.get("duration") or audio_duration_seconds(output_path) or 0)
        return {
            "workflow_id": workflow_id,
            "shot_id": payload.shot_id,
            "scene_mark_id": payload.scene_mark_id,
            "input_mode": "tts",
            "srt_text": payload.srt_text,
            "api_call_id": f"{workflow_id}-locked-cache-{now_ms()}",
            "candidate_id": str(manifest.get("candidate_id") or f"{provider or 'provider'}_tts_locked"),
            "provider": provider,
            "model": model_id,
            "voice_id": voice_id,
            "output": output_rel,
            "output_path": str(output_path),
            "raw_output": str(manifest.get("raw_output") or ""),
            "raw_output_path": str(manifest.get("raw_output_path") or ""),
            "duration_seconds": round(duration, 3) if duration else 0,
            "raw_duration": manifest.get("raw_duration"),
            "fit_duration": round(duration, 3) if duration else 0,
            "speed_factor": manifest.get("speed_factor"),
            "tempo": manifest.get("tempo") or prompt_item.get("tempo"),
            "stretched": manifest.get("stretched"),
            "elapsed_seconds": 0,
            "audio_url": "",
            "local_preview": False,
            "ok": True,
            "status": "completed",
            "cache_hit": True,
            "locked_manifest": payload.locked_manifest.strip(),
            "locked": True,
        }

    def write_locked_tts_manifest(workspace: Path, payload: OCRebuildAssetTTSComparePayload, prompt_item: dict[str, Any], result: dict[str, Any]) -> None:
        if not payload.use_locked_cache or not payload.locked_manifest.strip() or not payload.locked_config_key.strip():
            return
        _, manifest_path = safe_workspace_rel(workspace, payload.locked_manifest)
        output_rel = str(result.get("output") or payload.locked_output or "").strip()
        raw_output_rel = str(result.get("raw_output") or "").strip()
        manifest = {
            "status": "locked",
            "scope": "storyboard_scene_tts",
            "config_key": payload.locked_config_key.strip(),
            "workflow_id": str(result.get("workflow_id") or payload.workflow_id or ""),
            "task_id": int(result.get("task_id") or 0) or None,
            "session_id": int(result.get("session_id") or 0) or None,
            "shot_id": payload.shot_id,
            "scene_mark_id": payload.scene_mark_id,
            "text": payload.srt_text,
            "prompt": str(prompt_item.get("prompt") or ""),
            "provider": str(result.get("provider") or prompt_item.get("provider") or ""),
            "model": str(result.get("model") or prompt_item.get("model") or ""),
            "voice_id": str(result.get("voice_id") or prompt_item.get("voice_id") or ""),
            "tempo": result.get("tempo") or prompt_item.get("tempo"),
            "speed_factor": result.get("speed_factor"),
            "stretched": result.get("stretched"),
            "duration_seconds": result.get("duration_seconds") or result.get("duration"),
            "raw_duration": result.get("raw_duration"),
            "output": output_rel,
            "output_path": str(workspace / output_rel) if output_rel else "",
            "raw_output": raw_output_rel,
            "raw_output_path": str(workspace / raw_output_rel) if raw_output_rel else "",
            "candidate_id": str(result.get("candidate_id") or ""),
            "updated_at": now_ms(),
        }
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(manifest_path)

    def workflow_dir(workspace: Path, workflow_id: str) -> Path:
        return workspace / "asset_image_workflows" / normalize_workflow_id(workflow_id)

    def video_workflow_dir(workspace: Path, workflow_id: str) -> Path:
        return workspace / "asset_video_workflows" / normalize_workflow_id(workflow_id)

    def tts_workflow_dir(workspace: Path, workflow_id: str) -> Path:
        return workspace / "asset_tts_workflows" / normalize_workflow_id(workflow_id)

    def workflow_json_path(workspace: Path, workflow_id: str) -> Path:
        return workflow_dir(workspace, workflow_id) / "workflow.json"

    def video_workflow_json_path(workspace: Path, workflow_id: str) -> Path:
        return video_workflow_dir(workspace, workflow_id) / "workflow.json"

    def tts_workflow_json_path(workspace: Path, workflow_id: str) -> Path:
        return tts_workflow_dir(workspace, workflow_id) / "workflow.json"

    def read_asset_workflow(workspace: Path, workflow_id: str) -> dict[str, Any]:
        path = workflow_json_path(workspace, workflow_id)
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Unable to read workflow.json: {exc}") from exc
        return data if isinstance(data, dict) else {}

    def read_asset_video_workflow(workspace: Path, workflow_id: str) -> dict[str, Any]:
        path = video_workflow_json_path(workspace, workflow_id)
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Unable to read video workflow.json: {exc}") from exc
        return data if isinstance(data, dict) else {}

    def read_asset_tts_workflow(workspace: Path, workflow_id: str) -> dict[str, Any]:
        path = tts_workflow_json_path(workspace, workflow_id)
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Unable to read TTS workflow.json: {exc}") from exc
        return data if isinstance(data, dict) else {}

    def write_asset_workflow(workspace: Path, workflow_id: str, data: dict[str, Any]) -> dict[str, Any]:
        normalized = normalize_workflow_id(workflow_id)
        path = workflow_json_path(workspace, normalized)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {**data, "workflow_id": normalized, "updated_at": now_ms()}
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(path)
        return payload

    def write_asset_video_workflow(workspace: Path, workflow_id: str, data: dict[str, Any]) -> dict[str, Any]:
        normalized = normalize_workflow_id(workflow_id)
        path = video_workflow_json_path(workspace, normalized)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {**data, "workflow_id": normalized, "updated_at": now_ms()}
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(path)
        return payload

    def write_asset_tts_workflow(workspace: Path, workflow_id: str, data: dict[str, Any]) -> dict[str, Any]:
        normalized = normalize_workflow_id(workflow_id)
        path = tts_workflow_json_path(workspace, normalized)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {**data, "workflow_id": normalized, "updated_at": now_ms()}
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(path)
        return payload

    def update_asset_workflow(workspace: Path, workflow_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        current = read_asset_workflow(workspace, workflow_id)
        merged = {**current, **patch}
        return write_asset_workflow(workspace, workflow_id, merged)

    def update_asset_video_workflow(workspace: Path, workflow_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        current = read_asset_video_workflow(workspace, workflow_id)
        merged = {**current, **patch}
        return write_asset_video_workflow(workspace, workflow_id, merged)

    def update_asset_tts_workflow(workspace: Path, workflow_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        current = read_asset_tts_workflow(workspace, workflow_id)
        merged = {**current, **patch}
        return write_asset_tts_workflow(workspace, workflow_id, merged)

    def upsert_shot_tts_candidate(workspace: Path, workflow_id: str, candidate: dict[str, Any]) -> dict[str, Any]:
        current = read_asset_tts_workflow(workspace, workflow_id)
        shot_state = current.get("shot") if isinstance(current.get("shot"), dict) else {}
        candidates = [item for item in (shot_state.get("candidates") or []) if isinstance(item, dict) and str(item.get("candidate_id") or "") != str(candidate.get("candidate_id") or "")]
        shot_state["candidates"] = [*candidates, candidate]
        current["shot"] = shot_state
        return write_asset_tts_workflow(workspace, workflow_id, current)

    def upsert_video_workflow_candidate(workspace: Path, workflow_id: str, candidate: dict[str, Any]) -> dict[str, Any]:
        current = read_asset_video_workflow(workspace, workflow_id)
        candidates = [item for item in (current.get("candidates") or []) if isinstance(item, dict) and str(item.get("candidate_id") or "") != str(candidate.get("candidate_id") or "")]
        current["candidates"] = [*candidates, candidate]
        return write_asset_video_workflow(workspace, workflow_id, current)

    def upsert_tts_workflow_candidate(workspace: Path, workflow_id: str, scene_mark_id: str, candidate: dict[str, Any]) -> dict[str, Any]:
        current = read_asset_tts_workflow(workspace, workflow_id)
        scenes = current.get("scenes") if isinstance(current.get("scenes"), dict) else {}
        scene = scenes.get(scene_mark_id) if isinstance(scenes.get(scene_mark_id), dict) else {}
        candidates = [item for item in (scene.get("candidates") or []) if isinstance(item, dict) and str(item.get("candidate_id") or "") != str(candidate.get("candidate_id") or "")]
        scene["candidates"] = [*candidates, candidate]
        scenes[scene_mark_id] = scene
        current["scenes"] = scenes
        return write_asset_tts_workflow(workspace, workflow_id, current)

    def upsert_workflow_candidate(workspace: Path, workflow_id: str, round_no: int, candidate: dict[str, Any]) -> dict[str, Any]:
        current = read_asset_workflow(workspace, workflow_id)
        round_key = f"round_{round_no}"
        round_state = current.get(round_key) if isinstance(current.get(round_key), dict) else {}
        candidate_id = str(candidate.get("candidate_id") or candidate.get("candidateId") or "")
        existing = [item for item in (round_state.get("candidates") or []) if isinstance(item, dict) and str(item.get("candidate_id") or item.get("candidateId") or "") != candidate_id]
        round_state["candidates"] = [*existing, candidate]
        current[round_key] = round_state
        return write_asset_workflow(workspace, workflow_id, current)

    CN_DIRECT_HOST_SUFFIXES = (
        ".aliyuncs.com",
        ".aliyun.com",
        ".aliyuncs.com.cn",
        ".myqcloud.com",
        ".myqcloud.com.cn",
        ".qcloud.com",
        ".minimaxi.com",
    )

    def host_matches_suffix(host: str, suffix: str) -> bool:
        return host == suffix.lstrip(".") or host.endswith(suffix)

    def proxy_policy_for_url(url: str) -> str:
        host = urllib.parse.urlparse(url).hostname or ""
        host = host.lower().strip(".")
        if not host or host in {"127.0.0.1", "localhost"} or host.endswith(".local"):
            return "direct"
        if any(host_matches_suffix(host, suffix) for suffix in CN_DIRECT_HOST_SUFFIXES):
            return "direct"
        return "mihomo"

    def open_provider_request(req: urllib.request.Request, timeout: int | float):
        return provider_urlopen(req, timeout=timeout, proxy_policy=proxy_policy_for_url(req.full_url))

    def post_json_request(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int = 120) -> dict[str, Any]:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", "Accept": "application/json", **headers}, method="POST")
        try:
            with open_provider_request(req, timeout=timeout) as res:
                body = res.read().decode("utf-8", errors="replace")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:2000]
            raise HTTPException(status_code=502, detail=f"Provider request failed: HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise HTTPException(status_code=502, detail=f"Provider request failed: {exc.reason}") from exc

    def post_multipart_request(url: str, fields: dict[str, str], files: list[tuple[str, Path]], headers: dict[str, str], timeout: int = 120) -> dict[str, Any]:
        boundary = f"----OpenCrew{uuid.uuid4().hex}"
        chunks: list[bytes] = []
        for name, value in fields.items():
            chunks.extend([f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(), str(value).encode("utf-8"), b"\r\n"])
        for name, path in files:
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            chunks.extend([f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="{name}"; filename="{path.name}"\r\n'.encode(), f"Content-Type: {mime}\r\n\r\n".encode(), path.read_bytes(), b"\r\n"])
        chunks.append(f"--{boundary}--\r\n".encode())
        req = urllib.request.Request(url, data=b"".join(chunks), headers={"Accept": "application/json", "Content-Type": f"multipart/form-data; boundary={boundary}", **headers}, method="POST")
        try:
            with open_provider_request(req, timeout=timeout) as res:
                body = res.read().decode("utf-8", errors="replace")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:2000]
            raise HTTPException(status_code=502, detail=f"Image provider request failed: HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise HTTPException(status_code=502, detail=f"Image provider request failed: {exc.reason}") from exc

    def dashscope_upload_file(api_key: str, model: str, path: Path) -> str:
        query = urllib.parse.urlencode({"action": "getPolicy", "model": model})
        policy = get_json_request(f"https://dashscope.aliyuncs.com/api/v1/uploads?{query}", {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
        policy_data = policy.get("data") if isinstance(policy.get("data"), dict) else {}
        upload_host = str(policy_data.get("upload_host") or "")
        upload_dir = str(policy_data.get("upload_dir") or "")
        if not upload_host or not upload_dir:
            raise HTTPException(status_code=502, detail=f"DashScope upload policy is missing upload_host/upload_dir: {json.dumps(policy, ensure_ascii=False)[:1000]}")
        key = f"{upload_dir.rstrip('/')}/{path.name}"
        boundary = f"----OpenCrewDashScope{uuid.uuid4().hex}"
        chunks: list[bytes] = []
        fields = {
            "OSSAccessKeyId": str(policy_data.get("oss_access_key_id") or ""),
            "Signature": str(policy_data.get("signature") or ""),
            "policy": str(policy_data.get("policy") or ""),
            "x-oss-object-acl": str(policy_data.get("x_oss_object_acl") or "private"),
            "x-oss-forbid-overwrite": str(policy_data.get("x_oss_forbid_overwrite") or "true"),
            "key": key,
            "success_action_status": "200",
        }
        for name, value in fields.items():
            chunks.extend([f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(), value.encode("utf-8"), b"\r\n"])
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        chunks.extend([f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'.encode(), f"Content-Type: {mime}\r\n\r\n".encode(), path.read_bytes(), b"\r\n", f"--{boundary}--\r\n".encode()])
        req = urllib.request.Request(upload_host, data=b"".join(chunks), headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, method="POST")
        try:
            with open_provider_request(req, timeout=180) as res:
                if res.status != 200:
                    raise HTTPException(status_code=502, detail=f"DashScope upload failed: HTTP {res.status}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:2000]
            raise HTTPException(status_code=502, detail=f"DashScope upload failed: HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise HTTPException(status_code=502, detail=f"DashScope upload failed: {exc.reason}") from exc
        return f"oss://{key}"

    def get_json_request(url: str, headers: dict[str, str], timeout: int = 120) -> dict[str, Any]:
        req = urllib.request.Request(url, headers={"Accept": "application/json", **headers})
        try:
            with open_provider_request(req, timeout=timeout) as res:
                body = res.read().decode("utf-8", errors="replace")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:2000]
            raise HTTPException(status_code=502, detail=f"Video provider request failed: HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise HTTPException(status_code=502, detail=f"Video provider request failed: {exc.reason}") from exc

    def download_binary(url: str, output_path: Path, headers: dict[str, str] | None = None, timeout: int = 600) -> None:
        last_error: Exception | None = None
        for attempt in range(1, 4):
            req = urllib.request.Request(url, headers=headers or {})
            try:
                with open_provider_request(req, timeout=timeout) as res:
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    with output_path.open("wb") as handle:
                        shutil.copyfileobj(res, handle)
                return
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:2000]
                raise HTTPException(status_code=502, detail=f"Video download failed: HTTP {exc.code}: {detail}") from exc
            except (urllib.error.URLError, ssl.SSLError, http.client.IncompleteRead, ConnectionError, TimeoutError) as exc:
                last_error = exc
                output_path.unlink(missing_ok=True)
                if attempt < 3:
                    time.sleep(2 * attempt)
                    continue
                reason = getattr(exc, "reason", None) or str(exc)
                raise HTTPException(status_code=502, detail=f"Video download failed after {attempt} attempts: {reason}") from exc
        if last_error:
            raise HTTPException(status_code=502, detail=f"Video download failed: {last_error}")

    def post_binary_request(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int = 120) -> tuple[bytes, str]:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", "Accept": "*/*", **headers}, method="POST")
        try:
            with open_provider_request(req, timeout=timeout) as res:
                return res.read(), str(res.headers.get("Content-Type") or "application/octet-stream")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:2000]
            raise HTTPException(status_code=502, detail=f"TTS provider request failed: HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise HTTPException(status_code=502, detail=f"TTS provider request failed: {exc.reason}") from exc

    def write_data_url(data_url: str, output_path: Path) -> None:
        if not data_url.startswith("data:") or ";base64," not in data_url:
            raise HTTPException(status_code=502, detail="TTS provider returned invalid inline audio data")
        encoded = data_url.split(";base64,", 1)[1]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(base64.b64decode(encoded))

    def wav_data_from_pcm(pcm_data: bytes, sample_rate: int = 24000, channels: int = 1, bits_per_sample: int = 16) -> bytes:
        import io
        byte_rate = sample_rate * channels * bits_per_sample // 8
        block_align = channels * bits_per_sample // 8
        wav = io.BytesIO()
        wav.write(b"RIFF")
        wav.write((36 + len(pcm_data)).to_bytes(4, "little"))
        wav.write(b"WAVEfmt ")
        wav.write((16).to_bytes(4, "little"))
        wav.write((1).to_bytes(2, "little"))
        wav.write((channels).to_bytes(2, "little"))
        wav.write((sample_rate).to_bytes(4, "little"))
        wav.write((byte_rate).to_bytes(4, "little"))
        wav.write((block_align).to_bytes(2, "little"))
        wav.write((bits_per_sample).to_bytes(2, "little"))
        wav.write(b"data")
        wav.write((len(pcm_data)).to_bytes(4, "little"))
        wav.write(pcm_data)
        return wav.getvalue()

    def audio_duration_seconds(path: Path) -> float:
        ffprobe = shutil.which("ffprobe") or str(Path(__file__).resolve().parents[3] / ".bin" / "ffprobe")
        if ffprobe and Path(ffprobe).exists():
            try:
                result = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)], capture_output=True, text=True, timeout=20, check=False)
                value = float((result.stdout or "").strip())
                if value > 0:
                    return round(value, 3)
            except Exception:
                pass
        try:
            with wave.open(str(path), "rb") as handle:
                frames = handle.getnframes()
                rate = handle.getframerate()
                return round(frames / rate, 3) if rate else 0.0
        except Exception:
            return 0.0

    def ffmpeg_binary() -> str:
        found = shutil.which("ffmpeg")
        if found:
            return found
        try:
            import imageio_ffmpeg
            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"ffmpeg is unavailable: {exc}") from exc

    def atempo_filter_chain(tempo: float) -> str:
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

    def time_stretch_audio(source: Path, target: Path, target_duration: float | None) -> dict[str, Any]:
        raw_duration = audio_duration_seconds(source)
        if not target_duration or target_duration <= 0 or raw_duration <= 0:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            return {"raw_duration": raw_duration, "locked_duration": audio_duration_seconds(target), "speed_factor": 1.0, "tempo": 1.0, "stretched": False, "warnings": []}
        speed_factor = raw_duration / float(target_duration)
        warnings: list[str] = []
        if speed_factor < 0.85 or speed_factor > 1.18:
            warnings.append("speed_factor_outside_recommended_range")
        target.parent.mkdir(parents=True, exist_ok=True)
        filters = [
            "aresample=48000",
            "aformat=channel_layouts=stereo",
            atempo_filter_chain(speed_factor),
            "loudnorm=I=-17:LRA=11:TP=-1.5",
            f"apad=pad_dur={float(target_duration):.6f}",
            f"atrim=duration={float(target_duration):.6f}",
            "asetpts=N/SR/TB",
        ]
        cmd = [ffmpeg_binary(), "-y", "-i", str(source), "-af", ",".join(filters), "-ar", "48000", "-ac", "2", "-vn", str(target)]
        completed = subprocess.run(cmd, capture_output=True, text=True, timeout=180, check=False)
        if completed.returncode != 0:
            raise HTTPException(status_code=500, detail=(completed.stderr or "ffmpeg audio stretch failed")[:2000])
        return {"raw_duration": raw_duration, "locked_duration": audio_duration_seconds(target), "speed_factor": round(speed_factor, 4), "tempo": round(speed_factor, 4), "stretched": True, "warnings": warnings}

    def tempo_stretch_audio(source: Path, target: Path, tempo: float | None) -> dict[str, Any]:
        raw_duration = audio_duration_seconds(source)
        tempo_value = float(tempo or 1.0)
        if tempo_value <= 0 or raw_duration <= 0:
            tempo_value = 1.0
        if abs(tempo_value - 1.0) < 0.0001:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            return {"raw_duration": raw_duration, "locked_duration": audio_duration_seconds(target), "speed_factor": 1.0, "tempo": 1.0, "stretched": False, "warnings": []}
        warnings: list[str] = []
        target.parent.mkdir(parents=True, exist_ok=True)
        filters = [
            "aresample=48000",
            "aformat=channel_layouts=stereo",
            atempo_filter_chain(tempo_value),
            "loudnorm=I=-17:LRA=11:TP=-1.5",
            "asetpts=N/SR/TB",
        ]
        cmd = [ffmpeg_binary(), "-y", "-i", str(source), "-af", ",".join(filters), "-ar", "48000", "-ac", "2", "-vn", str(target)]
        completed = subprocess.run(cmd, capture_output=True, text=True, timeout=180, check=False)
        if completed.returncode != 0:
            raise HTTPException(status_code=500, detail=(completed.stderr or "ffmpeg tempo stretch failed")[:2000])
        return {"raw_duration": raw_duration, "locked_duration": audio_duration_seconds(target), "speed_factor": round(tempo_value, 4), "tempo": round(tempo_value, 4), "stretched": True, "warnings": warnings}

    def plain_srt_text(value: str) -> str:
        lines = []
        for line in str(value or "").splitlines():
            item = line.strip()
            if not item or re.fullmatch(r"\d+", item) or "-->" in item:
                continue
            lines.append(item)
        return " ".join(lines).strip()

    def read_workspace_json(path: Path, label: str) -> dict[str, Any]:
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"{label} not found")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Unable to read {label}: {exc}") from exc
        return data if isinstance(data, dict) else {}

    def validate_shot_plan_context(task_row: dict[str, Any], plan: dict[str, Any]) -> None:
        task_info = plan.get("task") if isinstance(plan.get("task"), dict) else {}
        plan_task_id = int(task_info.get("task_id") or 0)
        plan_session_id = int(task_info.get("session_id") or 0)
        if plan_task_id and plan_task_id != int(task_row["id"]):
            raise HTTPException(status_code=409, detail=f"rebuild_shot_plan.json task_id={plan_task_id} does not match Task #{task_row['id']}")
        if plan_session_id and plan_session_id != int(task_row["session_id"]):
            raise HTTPException(status_code=409, detail=f"rebuild_shot_plan.json session_id={plan_session_id} does not match Session #{task_row['session_id']}")

    def shot_plan_path(workspace: Path) -> Path:
        return workspace / "rebuild_shot_plan.json"

    def find_shot_in_plan(plan: dict[str, Any], shot_id: str) -> dict[str, Any]:
        shot = next((item for item in plan.get("shots") or [] if isinstance(item, dict) and str(item.get("shot_id") or "") == shot_id), None)
        if not shot:
            raise HTTPException(status_code=404, detail=f"Shot not found in rebuild_shot_plan.json: {shot_id}")
        return shot

    def write_shot_plan_atomic(path: Path, plan: dict[str, Any]) -> None:
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(path)

    def final_prompt_package_rel(shot: dict[str, Any]) -> str:
        package_ref = shot.get("final_prompt_package") if isinstance(shot.get("final_prompt_package"), dict) else {}
        rel_path = str(package_ref.get("path") or "").strip()
        return rel_path or f"Assets/variant_001/{shot.get('shot_id') or 'shot'}/final_prompt_package.json"

    VIDEO_PROMPT_FIELDS = (
        ("positive", {"zh": "正向", "en": "Positive"}),
        ("character_action", {"zh": "人物动作", "en": "Character Action"}),
        ("speech_speed", {"zh": "语言速度", "en": "Speech Speed"}),
        ("voice_description", {"zh": "语音描述", "en": "Voice Description"}),
        ("camera_motion", {"zh": "镜头运动", "en": "Camera Motion"}),
        ("scene_consistency", {"zh": "场景一致性", "en": "Scene Consistency"}),
        ("product_consistency", {"zh": "产品一致性", "en": "Product Consistency"}),
        ("negative", {"zh": "负向", "en": "Negative"}),
        ("model_notes", {"zh": "模型备注", "en": "Model Notes"}),
    )

    def scene_prompt_language(scene: dict[str, Any]) -> str:
        return "zh" if str(scene.get("active_language") or "").strip().lower() == "zh" else "en"

    def compile_structured_video_prompt(scene: dict[str, Any]) -> str:
        structured_all = scene.get("video_prompt_structured") if isinstance(scene.get("video_prompt_structured"), dict) else {}
        language = scene_prompt_language(scene)
        structured = structured_all.get(language) if isinstance(structured_all.get(language), dict) else {}
        parts = []
        for key, labels in VIDEO_PROMPT_FIELDS:
            value = str(structured.get(key) or "").strip()
            if value:
                parts.append(f"{labels[language]}:\n{value}")
        return "\n\n".join(parts).strip()

    def final_prompt_package_for_shot(workspace: Path, shot: dict[str, Any]) -> tuple[dict[str, Any], str]:
        rel_path = final_prompt_package_rel(shot)
        package_path = resolve_workspace_rel_for_write(workspace, rel_path)
        package = read_json_file(package_path)
        if package:
            return package, rel_path
        package_ref = shot.get("final_prompt_package") if isinstance(shot.get("final_prompt_package"), dict) else {}
        reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
        def source_frame_for_mark(mark: dict[str, Any]) -> str:
            keyframes = mark.get("keyframes") if isinstance(mark.get("keyframes"), dict) else {}
            for key in ("first", "single"):
                value = str(keyframes.get(key) or "").strip()
                if value:
                    return value
            paths = keyframes.get("paths") if isinstance(keyframes.get("paths"), list) else []
            for value in paths:
                if str(value or "").strip():
                    return str(value).strip()
            scene_id = str(mark.get("scene_mark_id") or mark.get("scene_id") or "").strip()
            for frame in reference.get("keyframes") or []:
                if not isinstance(frame, dict):
                    continue
                frame_mark = frame.get("scene_mark") if isinstance(frame.get("scene_mark"), dict) else {}
                if str(frame_mark.get("scene_mark_id") or "") == scene_id and str(frame_mark.get("role") or "") in {"first", "single"}:
                    value = str(frame.get("path") or "").strip()
                    if value:
                        return value
            return ""
        scenes: list[dict[str, Any]] = []
        for mark in reference.get("scene_marks") or []:
            if not isinstance(mark, dict):
                continue
            final_prompts = mark.get("final_prompts") if isinstance(mark.get("final_prompts"), dict) else {}
            scene_id = mark.get("scene_mark_id") or mark.get("scene_id") or ""
            scenes.append({
                "scene_mark_id": scene_id,
                "image_prompt": final_prompts.get("image_prompt") or "",
                "video_prompt": final_prompts.get("video_prompt") or final_prompts.get("grok_video_prompt") or "",
                "reference_image": source_frame_for_mark(mark) or f"Assets/variant_001/{shot.get('shot_id') or 'shot'}/{scene_id or 'scene'}/first.png",
            })
        if not scenes:
            scenes.append({
                "scene_mark_id": f"{shot.get('shot_id') or 'shot'}_scene_001",
                "reference_image": f"Assets/variant_001/{shot.get('shot_id') or 'shot'}/{shot.get('shot_id') or 'shot'}_scene_001/first.png",
                "image_prompt": "",
                "video_prompt": "",
            })
        tts = package_ref.get("tts") if isinstance(package_ref.get("tts"), dict) else {}
        package = {
            "shot_id": shot.get("shot_id") or "",
            "updated_at": int(package_ref.get("updated_at") or 0),
            "prompt_package_version": "final_v1",
            "references": {
                "host_image": read_builder_section(workspace, "host").get("output") or "",
                "product_image": read_builder_section(workspace, "product").get("output") or "",
            },
            "tts_prompt": str(tts.get("execution_prompt") or tts.get("prompt") or package_ref.get("tts_prompt") or ""),
            "tts_speed_notes": tts.get("speed_notes") if isinstance(tts.get("speed_notes"), list) else [],
            "scenes": scenes,
        }
        return package, rel_path

    def normalize_final_prompt_package_payload(package_payload: dict[str, Any], shot_id: str, existing_package: dict[str, Any] | None = None) -> dict[str, Any]:
        existing = existing_package if isinstance(existing_package, dict) else {}
        references = package_payload.get("references") if isinstance(package_payload.get("references"), dict) else {}
        existing_refs = existing.get("references") if isinstance(existing.get("references"), dict) else {}
        speed_notes = package_payload.get("tts_speed_notes")
        if isinstance(speed_notes, str):
            normalized_speed_notes = [item.strip() for item in re.split(r"[,，、\n]+", speed_notes) if item.strip()]
        elif isinstance(speed_notes, list):
            normalized_speed_notes = [str(item).strip() for item in speed_notes if str(item).strip()]
        else:
            normalized_speed_notes = []
        scenes = []
        for scene in [item for item in (package_payload.get("scenes") or []) if isinstance(item, dict)]:
            scene_id = str(scene.get("scene_mark_id") or "").strip()
            if not scene_id:
                raise HTTPException(status_code=400, detail="scene_mark_id is required for every scene prompt")
            scenes.append({
                "scene_mark_id": scene_id,
                "reference_image": str(scene.get("reference_image") or "").strip(),
                "image_prompt": str(scene.get("image_prompt") or "").strip(),
                "video_prompt": str(scene.get("video_prompt") or "").strip(),
            })
        if not scenes:
            raise HTTPException(status_code=400, detail="At least one scene prompt is required")
        return {
            "shot_id": shot_id,
            "updated_at": now_ms(),
            "prompt_package_version": "final_v1",
            "references": {
                "host_image": str(references.get("host_image") or existing_refs.get("host_image") or "").strip(),
                "product_image": str(references.get("product_image") or existing_refs.get("product_image") or "").strip(),
            },
            "tts_prompt": str(package_payload.get("tts_prompt") or existing.get("tts_prompt") or "").strip(),
            "tts_speed_notes": normalized_speed_notes,
            "scenes": scenes,
        }

    def sync_final_prompt_package_to_plan(shot: dict[str, Any], package: dict[str, Any], rel_path: str) -> None:
        timestamp = now_ms()
        scenes = [item for item in (package.get("scenes") or []) if isinstance(item, dict)]
        existing_ref = shot.get("final_prompt_package") if isinstance(shot.get("final_prompt_package"), dict) else {}
        shot["final_prompt_package"] = {
            **existing_ref,
            "path": rel_path,
            "updated_at": timestamp,
            "updated_by": "frontend_final_prompt_editor",
            "scene_count": len(scenes),
            "prompt_package_version": "final_v1",
        }
        scene_by_id = {str(item.get("scene_mark_id") or ""): item for item in scenes}
        reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
        next_marks = []
        for mark in reference.get("scene_marks") or []:
            if not isinstance(mark, dict):
                continue
            scene = scene_by_id.get(str(mark.get("scene_mark_id") or ""))
            if scene:
                existing_prompts = mark.get("final_prompts") if isinstance(mark.get("final_prompts"), dict) else {}
                mark = {
                    **mark,
                    "final_prompts": {
                        **existing_prompts,
                        "image_prompt": scene.get("image_prompt") or existing_prompts.get("image_prompt") or "",
                        "video_prompt": scene.get("video_prompt") or existing_prompts.get("video_prompt") or "",
                        "updated_at": timestamp,
                        "updated_by": "frontend_final_prompt_editor",
                    },
                }
            next_marks.append(mark)
        if next_marks:
            reference["scene_marks"] = next_marks
            shot["reference"] = reference

    def final_prompt_package_needs_plan_d_image_prompts(package: dict[str, Any]) -> bool:
        if package.get("prompt_package_version") != "final_v1":
            return True
        if not str(package.get("tts_prompt") or "").strip():
            return True
        scenes = [item for item in (package.get("scenes") or []) if isinstance(item, dict)]
        if not scenes:
            return True
        return any(not str(scene.get("image_prompt") or "").strip() or not str(scene.get("video_prompt") or "").strip() for scene in scenes)

    def suppress_plan_d_prompt_autobuild(workspace: Path) -> bool:
        phase2 = storyboard_phase2_state(workspace)
        return bool(
            phase2.get("suppress_plan_d_prompt_autobuild")
            or phase2.get("boundary") == "storyboard_to_reference_ready_phase2"
        )

    async def run_plan_d_image_prompt_builder(task_row: dict[str, Any], shot_id: str, force: bool = False) -> dict[str, Any]:
        workspace = workspace_path(task_row)
        session_id = int(task_row["session_id"])
        task_id = int(task_row["id"])
        script_path = Path(__file__).resolve().parents[3] / "ToolLibrary" / "Rebuild_V1" / "12_00_Shot_PlanD_ReplacementImagePromptBuild.py"
        if not script_path.exists():
            return {"status": "failed", "tool": "12_00_Shot_PlanD_ReplacementImagePromptBuild", "error": f"Plan D image prompt builder script not found: {script_path}"}
        cmd = [
            sys.executable or "python3",
            str(script_path),
            "--workspace",
            str(workspace),
            "--task-id",
            str(task_id),
            "--session-id",
            str(session_id),
            "--shot-id",
            shot_id,
            "--print-json",
        ]
        if force:
            cmd.append("--force")
        event_payload = {"task_id": task_id, "session_id": session_id, "shot_id": shot_id, "script": str(script_path)}
        add_event(session_id, "ocrebuild.plan_d.image_prompt_build.started", event_payload)

        def run_builder() -> subprocess.CompletedProcess[str]:
            return subprocess.run(cmd, cwd=str(script_path.parent), check=False, capture_output=True, text=True, timeout=180)

        completed = await asyncio.to_thread(run_builder)
        stdout_text = (completed.stdout or "").strip()
        parsed: dict[str, Any] = {}
        if stdout_text:
            try:
                data = json.loads(stdout_text)
                parsed = data if isinstance(data, dict) else {}
            except Exception:
                parsed = {}
        if completed.returncode == 0:
            payload = parsed or {"status": "completed", "tool": "12_00_Shot_PlanD_ReplacementImagePromptBuild"}
            add_event(session_id, "ocrebuild.plan_d.image_prompt_build.completed", {**event_payload, "status": payload.get("status")})
            return payload
        if completed.returncode == 2:
            payload = parsed or {"status": "blocked", "tool": "12_00_Shot_PlanD_ReplacementImagePromptBuild", "blocking_errors": [(completed.stderr or completed.stdout or "Plan D image prompt build blocked").strip()]}
            add_event(session_id, "ocrebuild.plan_d.image_prompt_build.blocked", {**event_payload, "blocking_errors": payload.get("blocking_errors") or []})
            return payload
        detail = (completed.stderr or completed.stdout or "Plan D image prompt build failed").strip()[-4000:]
        payload = parsed or {"status": "failed", "tool": "12_00_Shot_PlanD_ReplacementImagePromptBuild", "error": detail}
        add_event(session_id, "ocrebuild.plan_d.image_prompt_build.failed", {**event_payload, "error": payload.get("error") or detail})
        return payload

    def looks_like_srt(text_value: Any) -> bool:
        return "-->" in str(text_value or "")

    def rows_to_srt_text(rows: list[dict[str, Any]], field: str) -> str:
        if any(str(row.get("time_range") or "").strip() for row in rows):
            blocks: list[str] = []
            for index, row in enumerate(rows, start=1):
                text_value = str(row.get(field) or "").strip()
                time_range = str(row.get("time_range") or "").strip()
                if not text_value:
                    continue
                if time_range:
                    cue_index = str(row.get("cue_index") or index).strip() or str(index)
                    blocks.append(f"{cue_index}\n{time_range}\n{text_value}")
                else:
                    blocks.append(text_value)
            return "\n\n".join(blocks).strip()
        return "\n\n".join([str(row.get(field) or "").strip() for row in rows if str(row.get(field) or "").strip()]).strip()

    def source_package_subtitles(workspace: Path, task_row: dict[str, Any]) -> dict[str, str]:
        source_path = workspace / str(task_row.get("source_package_path") or "source_package.json")
        try:
            source_package = read_workspace_json(source_path, "source_package.json")
        except HTTPException:
            return {}
        source_info = source_package.get("source") if isinstance(source_package.get("source"), dict) else {}
        analysis_workspace_value = str(source_info.get("analysis_workspace") or "").strip()
        try:
            analysis_task = get_analysis_task(int(task_row.get("analysis_task_id") or 0) or None)
            analysis_workspace_value = str((analysis_task or {}).get("workspace_dir") or analysis_workspace_value).strip()
        except HTTPException:
            pass
        package_workspace_value = str(source_package.get("workspace") or "").strip()
        base_dirs = [workspace]
        if package_workspace_value:
            base_dirs.append(Path(package_workspace_value))
        if analysis_workspace_value:
            base_dirs.append(Path(analysis_workspace_value))
        subtitles: dict[str, str] = {}
        for segment in source_package.get("segments") or []:
            if not isinstance(segment, dict):
                continue
            segment_id = str(segment.get("segment_id") or "").strip()
            subtitle_path = str(segment.get("subtitle_path") or "").strip()
            if not segment_id or not subtitle_path:
                continue
            candidates = [Path(subtitle_path)] if Path(subtitle_path).is_absolute() else [base / subtitle_path for base in base_dirs]
            for candidate in candidates:
                if candidate.exists() and candidate.is_file():
                    subtitles[segment_id] = candidate.read_text(encoding="utf-8").strip()
                    break
        return subtitles

    def hydrate_shot_plan_srt_sources(workspace: Path, task_row: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
        subtitles = source_package_subtitles(workspace, task_row)
        if not subtitles:
            return plan
        for shot in plan.get("shots") or []:
            if not isinstance(shot, dict):
                continue
            reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
            segment_id = str(shot.get("source_segment_id") or shot.get("segment_id") or "").strip()
            source_srt = subtitles.get(segment_id, "").strip()
            if not source_srt:
                continue
            if not looks_like_srt(reference.get("source_srt_text")):
                reference["source_srt_text"] = source_srt
            if not looks_like_srt(reference.get("original_srt_text")):
                reference["original_srt_text"] = source_srt
            shot["reference"] = reference
        return plan

    def normalize_srt_rewrite_rows(rows: list[Any]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            data = row.model_dump() if hasattr(row, "model_dump") else dict(row or {})
            shot_id = str(data.get("shot_id") or "").strip()
            scene_mark_id = str(data.get("scene_mark_id") or "").strip()
            cue_index = str(data.get("cue_index") or "").strip()
            row_id = str(data.get("row_id") or "").strip() or "::".join([shot_id, scene_mark_id, cue_index])
            if not shot_id or not scene_mark_id or row_id in seen:
                continue
            normalized.append({
                "row_id": row_id,
                "shot_id": shot_id,
                "scene_mark_id": scene_mark_id,
                "cue_index": cue_index,
                "time_range": str(data.get("time_range") or "").strip(),
                "virtual_scene": bool(data.get("virtual_scene") or False),
                "original_srt_text": str(data.get("original_srt_text") or "").strip(),
                "original_dialogue_text": str(data.get("original_dialogue_text") or "").strip(),
                "new_srt_text": str(data.get("new_srt_text") or "").strip(),
            })
            seen.add(row_id)
        return normalized

    def scene_mark_lookup(plan: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
        lookup: dict[tuple[str, str], dict[str, Any]] = {}
        for shot in plan.get("shots") or []:
            if not isinstance(shot, dict):
                continue
            shot_id = str(shot.get("shot_id") or "").strip()
            reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
            shot_srt = str(reference.get("original_srt_text") or reference.get("source_srt_text") or reference.get("srt_text") or "").strip()
            for mark in reference.get("scene_marks") or []:
                if not isinstance(mark, dict):
                    continue
                scene_mark_id = str(mark.get("scene_mark_id") or "").strip()
                if shot_id and scene_mark_id:
                    lookup[(shot_id, scene_mark_id)] = {**mark, "_shot_srt_text": shot_srt}
        return lookup

    def enrich_srt_rewrite_rows_from_plan(plan: dict[str, Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        lookup = scene_mark_lookup(plan)
        shot_ids = {str(item.get("shot_id") or "").strip() for item in plan.get("shots") or [] if isinstance(item, dict)}
        enriched: list[dict[str, Any]] = []
        for row in rows:
            mark = lookup.get((row["shot_id"], row["scene_mark_id"]))
            if not mark:
                if row["shot_id"] not in shot_ids:
                    raise HTTPException(status_code=404, detail=f"Scene not found: {row['shot_id']} / {row['scene_mark_id']}")
                original = row["original_srt_text"] or row.get("original_dialogue_text", "")
                current = row["new_srt_text"] or original
                enriched.append({**row, "virtual_scene": True, "original_srt_text": original, "new_srt_text": current})
                continue
            original = row["original_srt_text"] or str(mark.get("original_srt_text") or mark.get("source_srt_text") or mark.get("srt_text") or mark.get("_shot_srt_text") or "").strip()
            current = row["new_srt_text"] or str(mark.get("srt_text") or original).strip()
            enriched.append({**row, "original_srt_text": original, "new_srt_text": current})
        return enriched

    def srt_rewrite_user_content(task_row: dict[str, Any], prompt: str, rows: list[dict[str, Any]]) -> str:
        snap = task_snapshot(task_row)
        return f"""Task context:
Target topic: {snap['target_topic']}
Product/service: {snap['product_info']}
Target platform: {snap['target_platform']}
Voice style: {snap['voice_style']}
Constraints: {snap['constraints']}

User editable rewrite prompt:
{prompt.strip()}

Rows to rewrite:
{json.dumps([{"row_id": row.get("row_id", ""), "shot_id": row["shot_id"], "scene_mark_id": row["scene_mark_id"], "cue_index": row.get("cue_index", ""), "time_range": row.get("time_range", ""), "original_srt_text": row["original_srt_text"]} for row in rows], ensure_ascii=False, indent=2)}

Return strict JSON only.
""".strip()

    def parse_srt_rewrite_response(text_value: str, expected_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        raw = text_value.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw).strip()
        try:
            parsed = json.loads(raw)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Run Model returned non-JSON SRT rewrite output") from exc
        items = parsed.get("rows") if isinstance(parsed, dict) else parsed
        if not isinstance(items, list):
            raise HTTPException(status_code=400, detail="Run Model SRT rewrite output must contain rows[]")
        by_row_id: dict[str, str] = {}
        by_key: dict[tuple[str, str, str], str] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            row_id = str(item.get("row_id") or "").strip()
            shot_id = str(item.get("shot_id") or "").strip()
            scene_mark_id = str(item.get("scene_mark_id") or "").strip()
            cue_index = str(item.get("cue_index") or "").strip()
            text_out = str(item.get("new_srt_text") or item.get("srt_text") or "").strip()
            if row_id and text_out:
                by_row_id[row_id] = text_out
            if shot_id and scene_mark_id and text_out:
                by_key[(shot_id, scene_mark_id, cue_index)] = text_out
        results: list[dict[str, Any]] = []
        missing: list[str] = []
        for row in expected_rows:
            key = (row["shot_id"], row["scene_mark_id"], str(row.get("cue_index") or ""))
            rewritten = by_row_id.get(str(row.get("row_id") or ""), "").strip() or by_key.get(key, "").strip()
            if not rewritten:
                missing.append(f"{row['shot_id']}/{row['scene_mark_id']}/{row.get('cue_index') or '-'}")
                continue
            results.append({**row, "new_srt_text": rewritten})
        if missing:
            raise HTTPException(status_code=400, detail=f"Run Model omitted SRT rows: {', '.join(missing[:8])}")
        return results

    def save_srt_rewrite_rows_to_plan(plan_path: Path, plan: dict[str, Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        virtual_by_shot: dict[str, list[dict[str, Any]]] = {}
        existing_scene_keys: set[tuple[str, str]] = set()
        shot_ids: set[str] = set()
        for shot in plan.get("shots") or []:
            if not isinstance(shot, dict):
                continue
            shot_id = str(shot.get("shot_id") or "").strip()
            if not shot_id:
                continue
            shot_ids.add(shot_id)
            reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
            for mark in reference.get("scene_marks") or []:
                if isinstance(mark, dict) and str(mark.get("scene_mark_id") or "").strip():
                    existing_scene_keys.add((shot_id, str(mark.get("scene_mark_id") or "").strip()))
        for row in rows:
            key = (row["shot_id"], row["scene_mark_id"])
            if row["shot_id"] in shot_ids and (row.get("virtual_scene") or key not in existing_scene_keys):
                virtual_by_shot.setdefault(row["shot_id"], []).append(row)
            else:
                grouped.setdefault(key, []).append(row)
        saved: list[dict[str, Any]] = []
        touched = False
        for shot in plan.get("shots") or []:
            if not isinstance(shot, dict):
                continue
            shot_id = str(shot.get("shot_id") or "").strip()
            reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
            marks = reference.get("scene_marks") if isinstance(reference.get("scene_marks"), list) else []
            for mark in marks:
                if not isinstance(mark, dict):
                    continue
                scene_mark_id = str(mark.get("scene_mark_id") or "").strip()
                mark_rows = grouped.get((shot_id, scene_mark_id)) or []
                if not mark_rows:
                    continue
                original = rows_to_srt_text(mark_rows, "original_srt_text") or str(mark.get("original_srt_text") or mark.get("source_srt_text") or mark.get("srt_text") or "").strip()
                final_text = rows_to_srt_text(mark_rows, "new_srt_text")
                mark["original_srt_text"] = original
                mark["source_srt_text"] = mark.get("source_srt_text") or original
                mark["srt_text"] = final_text
                mark["srt_rewrite"] = {"updated_at": now_ms(), "source": "srt_compare_editor"}
                for row in mark_rows:
                    saved.append({**row, "original_srt_text": row["original_srt_text"] or original, "new_srt_text": row["new_srt_text"].strip()})
                touched = True
            virtual_rows = virtual_by_shot.get(shot_id) or []
            if virtual_rows:
                shot_final = rows_to_srt_text(virtual_rows, "new_srt_text")
                if shot_final:
                    shot_original = rows_to_srt_text(virtual_rows, "original_srt_text")
                    if not looks_like_srt(reference.get("original_srt_text")):
                        reference["original_srt_text"] = shot_original
                    if not looks_like_srt(reference.get("source_srt_text")):
                        reference["source_srt_text"] = shot_original
                    reference["srt_text"] = shot_final
                    reference["srt_rewrite"] = {"updated_at": now_ms(), "source": "srt_compare_editor", "scope": "shot_srt_fallback"}
                    shot["reference"] = reference
                    saved.extend(virtual_rows)
                    touched = True
        saved_ids = {str(item.get("row_id") or "") for item in saved}
        missing = [f"{row['shot_id']}/{row['scene_mark_id']}" for row in rows if str(row.get("row_id") or "") not in saved_ids]
        if missing:
            raise HTTPException(status_code=404, detail=f"Scene not found: {', '.join(missing[:8])}")
        if touched:
            write_shot_plan_atomic(plan_path, plan)
        return saved

    def keyframe_map(frames: Any) -> dict[str, dict[str, Any]]:
        return {str(frame.get("path") or ""): dict(frame) for frame in (frames if isinstance(frames, list) else []) if isinstance(frame, dict) and str(frame.get("path") or "")}

    def original_keyframes_for_reference(reference: dict[str, Any], current_keyframes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        original = reference.get("original_keyframes") if isinstance(reference.get("original_keyframes"), list) else None
        return [dict(frame) for frame in (original if original is not None else current_keyframes) if isinstance(frame, dict)]

    def update_keyframe_audit(reference: dict[str, Any], original_keyframes: list[dict[str, Any]], next_keyframes: list[dict[str, Any]]) -> None:
        next_paths = {str(frame.get("path") or "") for frame in next_keyframes if isinstance(frame, dict)}
        deleted = [dict(frame) for frame in original_keyframes if str(frame.get("path") or "") and str(frame.get("path") or "") not in next_paths]
        reference["original_keyframes"] = sort_keyframes(original_keyframes)
        reference["deleted_keyframes"] = sort_keyframes(deleted)

    def source_reference_audio(task_row: dict[str, Any], workspace: Path) -> Path:
        source_path = workspace / str(task_row.get("source_package_path") or "source_package.json")
        source_package = read_workspace_json(source_path, "source_package.json")
        source_info = source_package.get("source") if isinstance(source_package.get("source"), dict) else {}
        analysis_task = get_analysis_task(int(task_row.get("analysis_task_id") or 0) or None)
        analysis_workspace_value = str((analysis_task or {}).get("workspace_dir") or source_info.get("analysis_workspace") or "").strip()
        analysis_workspace = Path(analysis_workspace_value) if analysis_workspace_value else None
        candidates = [
            analysis_workspace / "audio" / "reference_audio.wav" if analysis_workspace else None,
            workspace / "audio" / "reference_audio.wav",
        ]
        for candidate in candidates:
            if candidate and candidate.exists() and candidate.is_file():
                return candidate
        checked = [str(candidate) for candidate in candidates if candidate]
        raise HTTPException(status_code=404, detail=f"reference_audio.wav was not found. Checked: {', '.join(checked)}")

    def cut_reference_audio_for_shot(task_row: dict[str, Any], shot: dict[str, Any], workflow_id: str) -> Path:
        workspace = workspace_path(task_row)
        source = source_reference_audio(task_row, workspace)
        start = max(0.0, float(shot.get("start") or 0))
        duration = max(0.1, float(shot.get("duration") or (float(shot.get("end") or 0) - start) or 0.1))
        output = tts_workflow_dir(workspace, workflow_id) / "reference" / f"{shot.get('shot_id') or 'shot'}_reference.wav"
        output.parent.mkdir(parents=True, exist_ok=True)
        cmd = [ffmpeg_binary(), "-y", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(source), "-ac", "1", "-ar", "16000", str(output)]
        completed = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
        if completed.returncode != 0:
            raise HTTPException(status_code=500, detail=(completed.stderr or "ffmpeg reference audio cut failed")[:2000])
        return output

    def tts_match_endpoint():
        media_router = build_media_model_config_router(ctx)
        endpoint = next((route.endpoint for route in media_router.routes if getattr(route, "path", "").endswith("/tts/voices/match")), None)
        if not endpoint:
            raise HTTPException(status_code=500, detail="TTS voice match endpoint is unavailable")
        return endpoint

    def scene_text_weight(scene: dict[str, Any]) -> int:
        text_value = str(scene.get("srt_text") or scene.get("text") or "").strip()
        weight = len(re.sub(r"\s+", "", text_value))
        return max(1, weight)

    def build_locked_timeline(shot_id: str, scene_plan: list[dict[str, Any]], locked_audio_rel: str, locked_duration: float) -> dict[str, Any]:
        scenes = [item for item in scene_plan if isinstance(item, dict) and str(item.get("scene_mark_id") or item.get("scene_id") or "").strip()]
        if not scenes:
            scenes = [{"scene_mark_id": f"{shot_id}_scene_001", "srt_text": "", "image": ""}]
        weights = [scene_text_weight(scene) for scene in scenes]
        total_weight = sum(weights) or len(scenes)
        cursor = 0.0
        timeline_scenes: list[dict[str, Any]] = []
        for index, scene in enumerate(scenes):
            if index == len(scenes) - 1:
                end = float(locked_duration)
            else:
                end = cursor + float(locked_duration) * (weights[index] / total_weight)
            start = cursor
            scene_id = str(scene.get("scene_mark_id") or scene.get("scene_id") or f"{shot_id}_scene_{index + 1:03d}").strip()
            timeline_scenes.append({
                "scene_mark_id": scene_id,
                "srt_text": str(scene.get("srt_text") or scene.get("text") or ""),
                "start": round(start, 3),
                "end": round(end, 3),
                "duration": round(max(0.0, end - start), 3),
                "image": str(scene.get("image") or scene.get("reference_image") or ""),
                "alignment_method": "duration_proportional_by_srt_weight",
            })
            cursor = end
        return {"shot_id": shot_id, "locked_audio": locked_audio_rel, "duration": round(float(locked_duration), 3), "alignment_method": "duration_proportional_by_srt_weight", "scenes": timeline_scenes, "created_at": now_ms()}

    def srt_timestamp(seconds: float) -> str:
        ms_total = max(0, int(round(seconds * 1000)))
        hours = ms_total // 3600000
        ms_total %= 3600000
        minutes = ms_total // 60000
        ms_total %= 60000
        secs = ms_total // 1000
        ms = ms_total % 1000
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"

    def write_timeline_files(workspace: Path, timeline_rel: str, srt_rel: str, timeline: dict[str, Any]) -> None:
        timeline_path = workspace / timeline_rel
        timeline_path.parent.mkdir(parents=True, exist_ok=True)
        timeline_path.write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")
        lines: list[str] = []
        for index, scene in enumerate(timeline.get("scenes") or [], start=1):
            text_value = str(scene.get("srt_text") or "").strip()
            if not text_value:
                continue
            lines.extend([str(index), f"{srt_timestamp(float(scene.get('start') or 0))} --> {srt_timestamp(float(scene.get('end') or 0))}", text_value, ""])
        srt_path = workspace / srt_rel
        srt_path.parent.mkdir(parents=True, exist_ok=True)
        srt_path.write_text("\n".join(lines), encoding="utf-8")

    def load_tts_config(provider: str, model: str) -> dict[str, str]:
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
        api_key = provider_key_from_row("tts", mapping, provider)
        if not api_key:
            raise HTTPException(status_code=400, detail=f"TTS provider API key is missing: {provider}")
        stored_model = str(mapping.get("model") or "").strip()
        return {"provider": str(mapping.get("provider") or provider), "model": model.strip() or stored_model, "api_key": api_key}

    def dashscope_language_type(language: str) -> str:
        mapping = {"zh": "Chinese", "en": "English", "ja": "Japanese", "ko": "Korean", "de": "German", "fr": "French", "ru": "Russian", "pt": "Portuguese", "es": "Spanish", "it": "Italian"}
        return mapping.get(str(language or "").strip().lower(), language or "Chinese")

    def first_audio_url(payload: Any) -> str:
        if isinstance(payload, dict):
            for key in ("url", "audio_url", "download_url", "uri"):
                value = payload.get(key)
                if isinstance(value, str) and value.startswith("http"):
                    return value
            for key in ("audio", "output", "data", "response"):
                found = first_audio_url(payload.get(key))
                if found:
                    return found
            for value in payload.values():
                found = first_audio_url(value)
                if found:
                    return found
        if isinstance(payload, list):
            for value in payload:
                found = first_audio_url(value)
                if found:
                    return found
        return ""

    def first_audio_data(payload: Any) -> str:
        if isinstance(payload, dict):
            audio = payload.get("audio") if isinstance(payload.get("audio"), dict) else {}
            if isinstance(audio, dict) and isinstance(audio.get("data"), str) and audio.get("data"):
                return str(audio.get("data"))
            if isinstance(payload.get("data"), str) and payload.get("data"):
                return str(payload.get("data"))
            for value in payload.values():
                found = first_audio_data(value)
                if found:
                    return found
        if isinstance(payload, list):
            for value in payload:
                found = first_audio_data(value)
                if found:
                    return found
        return ""

    def tts_sound_event_instructions(text_value: str) -> list[str]:
        instructions: list[str] = []
        for raw_event in re.findall(r"[【\[]([^】\]]+)[】\]]", str(text_value or "")):
            event = re.sub(r"\s+", " ", str(raw_event or "")).strip()
            if not event:
                continue
            lower = event.lower()
            if "咳" in event or "cough" in lower:
                if "三" in event or "3" in event or "three" in lower:
                    instructions.append(f"遇到【{event}】时，不要朗读括号文字；请在该位置真实地轻微模拟咳嗽三声：“咳、咳、咳”，每声短促，三声之间有很短停顿，然后继续后面的正文。")
                else:
                    instructions.append(f"遇到【{event}】时，不要朗读括号文字；请在该位置自然模拟短促咳嗽声，然后继续后面的正文。")
            else:
                instructions.append(f"遇到【{event}】时，不要朗读括号文字；把它作为声音/表演动作提示自然执行，然后继续后面的正文。")
        return instructions

    def apply_tts_sound_event_instructions(prompt: str, text_value: str) -> str:
        instructions = tts_sound_event_instructions(text_value)
        if not instructions:
            return str(prompt or "").strip()
        guidance = "声音动作规则：正文里的【】或[]内容是声音表演指令，不是要念出来的文字。\n" + "\n".join(f"- {item}" for item in instructions)
        prompt_text = str(prompt or "").strip()
        marker = re.search(r"(?m)^正文\s*[:：]", prompt_text)
        if marker:
            return (prompt_text[: marker.start()].rstrip() + "\n" + guidance + "\n" + prompt_text[marker.start():]).strip()
        return (prompt_text + "\n" + guidance).strip()

    def strip_embedded_tts_text(value: str) -> str:
        return re.sub(r"(?:朗读文本|正文|Text)\s*[:：]\s*[\s\S]*$", "", str(value or ""), flags=re.IGNORECASE).strip()

    def cosyvoice_instruction_from_prompt(value: str) -> str:
        instruction = strip_embedded_tts_text(value)
        instruction = re.sub(r"(?im)^\s*严格朗读当前\s+Scene\s+文本.*$", "", instruction)
        instruction = re.sub(r"(?im)^\s*不要朗读\s+prompt\s+中的示例文本或历史文本.*$", "", instruction)
        instruction = re.sub(r"\n{3,}", "\n\n", instruction).strip()
        if not instruction:
            return ""
        if "自然短视频口播" in instruction:
            return "普通话自然短视频口播；声音自然清晰，像自拍视频；中速平稳，重点词轻微强调；只朗读正文。"
        if "情绪" in instruction and ("轻重音" in instruction or "括号提示" in instruction):
            return "自然朗读正文，并执行正文中的情绪、停顿、轻重音或括号提示；说明性标签不读出。"
        if len(instruction) <= 96:
            return instruction
        compact = "；".join(line.strip("；。 ") for line in instruction.splitlines() if line.strip())
        if len(compact) > 96:
            compact = compact[:95].rstrip("，、；:： \n") + "。"
        return compact

    def should_retry_cosyvoice_without_instruction(error_detail: str) -> bool:
        value = str(error_detail or "").lower()
        return any(token in value for token in ("empty audio", "taskfailed", "task-failed", "invalidparameter", "428", "instruction"))

    def generate_tts_audio(config: dict[str, str], text_value: str, voice_id: str, prompt: str, output_path: Path) -> str:
        provider = config["provider"]
        model = config["model"]
        api_key = config["api_key"]
        call_started = time.time()
        if provider == "google":
            prompt_text = apply_tts_sound_event_instructions(prompt, text_value)
            google_text = prompt_text or text_value
            result = post_json_request(
                f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(model, safe='')}:generateContent?key={urllib.parse.quote(api_key, safe='')}",
                {"contents": [{"parts": [{"text": google_text}]}], "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice_id}}}}},
                {},
                timeout=60,
            )
            for candidate in result.get("candidates") or []:
                for part in (((candidate.get("content") or {}).get("parts")) or []):
                    inline_data = part.get("inlineData") or part.get("inline_data") or {}
                    encoded = str(inline_data.get("data") or "") if isinstance(inline_data, dict) else ""
                    mime_type = str(inline_data.get("mimeType") or inline_data.get("mime_type") or "audio/wav") if isinstance(inline_data, dict) else "audio/wav"
                    if not encoded:
                        continue
                    raw = base64.b64decode(encoded)
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    output_path.write_bytes(wav_data_from_pcm(raw, sample_rate=24000) if "pcm" in mime_type or "l16" in mime_type else raw)
                    record_local_usage(provider, model, "tts", call_started, units={"character": len(text_value)})
                    return ""
            raise HTTPException(status_code=502, detail="Google TTS response did not include audio data")
        if provider == "xai":
            body, _content_type = post_binary_request("https://api.x.ai/v1/tts", {"text": text_value, "voice_id": voice_id, "language": "zh", "format": "mp3"}, {"Authorization": f"Bearer {api_key}"}, timeout=60)
            if not body:
                raise HTTPException(status_code=502, detail="xAI TTS returned empty audio")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(body)
            record_local_usage(provider, model, "tts", call_started, units={"character": len(text_value)})
            return ""
        if provider == "cosyvoice":
            instruction = cosyvoice_instruction_from_prompt(prompt)
            try:
                raw = dashscope_cosyvoice_tts_audio_bytes(api_key, model, voice_id, text_value, instruction)
            except Exception as exc:
                first_error = str(exc)
                if instruction and should_retry_cosyvoice_without_instruction(first_error):
                    try:
                        raw = dashscope_cosyvoice_tts_audio_bytes(api_key, model, voice_id, text_value, "", max_attempts=1)
                    except Exception as retry_exc:
                        raise HTTPException(
                            status_code=502,
                            detail=f"CosyVoice TTS failed after retry without instruction. First error: {first_error}; Retry error: {retry_exc}",
                        ) from retry_exc
                else:
                    raise HTTPException(status_code=502, detail=f"CosyVoice TTS failed: {first_error}") from exc
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(raw)
            record_local_usage(provider, model, "tts", call_started, units={"character": len(text_value)})
            return ""
        if provider == "qwen":
            input_payload = {"text": text_value, "voice": voice_id, "language_type": dashscope_language_type("zh")}
            if "instruct" in model and prompt.strip():
                input_payload["instructions"] = prompt.strip()
                input_payload["optimize_instructions"] = True
            result = post_json_request(
                "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation",
                {"model": model, "input": input_payload},
                {"Authorization": f"Bearer {api_key}"},
                timeout=60,
            )
            audio_url = first_audio_url(result)
            if audio_url:
                download_binary(audio_url, output_path)
                record_local_usage(provider, model, "tts", call_started, units={"character": len(text_value)})
                return audio_url
            audio_data = first_audio_data(result)
            if audio_data:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(base64.b64decode(audio_data))
                record_local_usage(provider, model, "tts", call_started, units={"character": len(text_value)})
                return ""
            raise HTTPException(status_code=502, detail=f"Qwen TTS response did not include audio url/data: {json.dumps(result, ensure_ascii=False)[:1000]}")
        raise HTTPException(status_code=400, detail=f"Unsupported Rebuild TTS provider: {provider}/{model}")

    def image_b64_from_response(provider: str, payload: dict[str, Any]) -> str:
        if provider in {"openai", "xai"}:
            item = next((entry for entry in payload.get("data") or [] if entry.get("b64_json")), None)
            if item:
                return str(item["b64_json"])
        if provider == "gemini":
            for candidate in payload.get("candidates") or []:
                for part in (((candidate.get("content") or {}).get("parts")) or []):
                    inline = part.get("inlineData") or part.get("inline_data") or {}
                    if inline.get("data"):
                        return str(inline["data"])
        raise HTTPException(status_code=502, detail="Image provider response did not include image data")

    def image_provider_endpoint(provider: str, model: str, reference_path: Path | None) -> str:
        if provider == "openai":
            return "https://api.openai.com/v1/images/edits" if reference_path else "https://api.openai.com/v1/images/generations"
        if provider == "xai":
            return "https://api.x.ai/v1/images/generations"
        if provider == "gemini":
            return f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(model, safe='')}:generateContent"
        return ""

    def generate_image_bytes(config: dict[str, str], prompt: str, output_path: Path, reference_path: Path | list[Path] | None, size: str = "1024x1536") -> bytes:
        provider = config["provider"]
        model = config["model"]
        api_key = config["api_key"]
        call_started = time.time()
        reference_paths = [path for path in (reference_path if isinstance(reference_path, list) else ([reference_path] if reference_path else [])) if path]
        if provider == "openai":
            headers = {"Authorization": f"Bearer {api_key}"}
            if reference_paths:
                image_field = "image[]" if len(reference_paths) > 1 else "image"
                payload = post_multipart_request("https://api.openai.com/v1/images/edits", {"model": model, "prompt": prompt, "size": size}, [(image_field, path) for path in reference_paths], headers)
            else:
                payload = post_json_request("https://api.openai.com/v1/images/generations", {"model": model, "prompt": prompt, "size": size}, headers)
            data = base64.b64decode(image_b64_from_response(provider, payload))
            record_local_usage(provider, model, "image", call_started, units={"image": 1})
            return data
        if provider == "xai":
            if reference_paths:
                raise HTTPException(status_code=400, detail="The active xAI image model is configured for prompt-only generation here")
            payload = post_json_request("https://api.x.ai/v1/images/generations", {"model": model, "prompt": prompt, "response_format": "b64_json"}, {"Authorization": f"Bearer {api_key}"})
            data = base64.b64decode(image_b64_from_response(provider, payload))
            record_local_usage(provider, model, "image", call_started, units={"image": 1})
            return data
        if provider == "gemini":
            parts: list[dict[str, Any]] = [{"text": prompt}]
            for path in reference_paths:
                parts.append({"inline_data": {"mime_type": mimetypes.guess_type(path.name)[0] or "image/png", "data": base64.b64encode(path.read_bytes()).decode("ascii")}})
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(model, safe='')}:generateContent?key={urllib.parse.quote(api_key, safe='')}"
            payload = post_json_request(url, {"contents": [{"role": "user", "parts": parts}], "generationConfig": {"responseModalities": ["IMAGE"]}}, {})
            data = base64.b64decode(image_b64_from_response(provider, payload))
            record_local_usage(provider, model, "image", call_started, units={"image": 1})
            return data
        raise HTTPException(status_code=400, detail=f"Unsupported active image provider: {provider}")

    def run_asset_image_generation(task_id: int, payload: OCRebuildAssetImageGeneratePayload) -> dict[str, Any]:
        task_row = get_task(task_id)
        workspace = workspace_path(task_row)
        asset_tasks_path = workspace / "asset_tasks.json"
        if not asset_tasks_path.exists() or not asset_tasks_path.is_file():
            raise HTTPException(status_code=404, detail="asset_tasks.json not found. Run 04_1 first.")
        try:
            asset_tasks = json.loads(asset_tasks_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Unable to read asset_tasks.json: {exc}") from exc
        role = payload.role.strip() or "single"
        scene_mark_id = payload.scene_mark_id.strip()
        image_types = {"single": ["image_regenerate_single"], "first": ["image_regenerate_first", "image_regenerate_single"], "last": ["image_regenerate_last"]}.get(role, ["image_regenerate_single"])
        tasks = asset_tasks.get("tasks") if isinstance(asset_tasks.get("tasks"), list) else []
        target = next((item for item in tasks if isinstance(item, dict) and str(item.get("shot_id") or "") == payload.shot_id and str(item.get("scene_mark_id") or "") == scene_mark_id and str(item.get("type") or "") in image_types), None)
        if not target:
            raise HTTPException(status_code=404, detail="Matching image asset task not found")
        inputs = target.get("input") if isinstance(target.get("input"), dict) else {}
        prompt = str(inputs.get("prompt") or "").strip()
        if not prompt:
            raise HTTPException(status_code=400, detail="Image asset task has no prompt")
        output_rel = str(target.get("output") or "").strip()
        if not output_rel:
            raise HTTPException(status_code=400, detail="Image asset task has no output path")
        output_path = workspace / output_rel
        reference_path = None
        if payload.use_reference_image:
            reference_rel = str(inputs.get("reference_frame") or "").strip()
            if not reference_rel:
                raise HTTPException(status_code=400, detail="Image asset task has no reference frame")
            analysis_task = get_analysis_task(int(task_row.get("analysis_task_id") or 0) or None)
            analysis_workspace = Path(str((analysis_task or {}).get("workspace_dir") or workspace))
            candidate = analysis_workspace / reference_rel
            if not candidate.exists():
                candidate = workspace / reference_rel
            if not candidate.exists() or not candidate.is_file():
                raise HTTPException(status_code=404, detail=f"Reference image not found: {reference_rel}")
            reference_path = candidate
        if reference_path:
            config, reference_image_provider_fallback_from = load_reference_image_config("", "")
        else:
            config = load_active_image_config()
            reference_image_provider_fallback_from = ""
        negative = str((target.get("params") or {}).get("negative_prompt") or "").strip() if isinstance(target.get("params"), dict) else ""
        full_prompt = f"{prompt}\n\nNegative prompt: {negative}" if negative else prompt
        output_path.parent.mkdir(parents=True, exist_ok=True)
        call_started = time.time()
        api_call_id = payload.api_call_id.strip() or f"ocrebuild-image-{now_ms()}"
        endpoint = image_provider_endpoint(config["provider"], config["model"], reference_path)
        call_detail = {
            "api_call_id": api_call_id,
            "task_id": task_id,
            "session_id": int(task_row["session_id"]),
            "shot_id": payload.shot_id,
            "scene_mark_id": scene_mark_id,
            "role": role,
            "provider": config["provider"],
            "model": config["model"],
            "endpoint": endpoint,
            "method": "POST",
            "use_reference_image": bool(reference_path),
            "reference_path": str(reference_path) if reference_path else "",
            "workspace_dir": str(workspace),
            "output": output_rel,
            "output_path": str(output_path),
            "prompt_preview": full_prompt[:1000],
            "prompt_length": len(full_prompt),
        }
        if reference_image_provider_fallback_from:
            call_detail["reference_image_provider_fallback_from"] = reference_image_provider_fallback_from
        add_event(int(task_row["session_id"]), "ocrebuild.asset_image.provider_call.started", call_detail)
        image_bytes = generate_image_bytes(config, full_prompt, output_path, reference_path)
        elapsed_seconds = round(time.time() - call_started, 3)
        output_path.write_bytes(image_bytes)
        target.update({"status": "completed", "generated_at": now_ms(), "provider": config["provider"], "model": config["model"], "used_reference_image": bool(reference_path)})
        tmp_path = asset_tasks_path.with_suffix(asset_tasks_path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(asset_tasks, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(asset_tasks_path)
        result = {"ok": True, "api_call_id": api_call_id, "task_id": task_id, "session_id": int(task_row["session_id"]), "shot_id": payload.shot_id, "scene_mark_id": scene_mark_id, "role": role, "output": output_rel, "output_path": str(output_path), "provider": config["provider"], "model": config["model"], "endpoint": endpoint, "used_reference_image": bool(reference_path), "elapsed_seconds": elapsed_seconds}
        add_event(int(task_row["session_id"]), "ocrebuild.asset_image.generated", result)
        return result

    def run_compare_candidate(task_id: int, request_payload: dict[str, Any], prompt: str, config: dict[str, str], output_rel: str, reference_path: Path | None) -> dict[str, Any]:
        task_row = get_task(task_id)
        workspace = workspace_path(task_row)
        session_id = int(task_row["session_id"])
        output_path = workspace / output_rel
        output_path.parent.mkdir(parents=True, exist_ok=True)
        provider = config["provider"]
        effective_reference = reference_path
        reference_unsupported_fallback = False
        if provider == "xai" and effective_reference:
            effective_reference = None
            reference_unsupported_fallback = True
        endpoint = image_provider_endpoint(provider, config["model"], effective_reference)
        call_started = time.time()
        call_detail = {
            **request_payload,
            "provider": provider,
            "model": config["model"],
            "endpoint": endpoint,
            "method": "POST",
            "use_reference_image": bool(effective_reference),
            "reference_unsupported_fallback": reference_unsupported_fallback,
            "reference_path": str(effective_reference) if effective_reference else "",
            "workspace_dir": str(workspace),
            "output": output_rel,
            "output_path": str(output_path),
            "prompt_preview": prompt[:1000],
            "prompt_length": len(prompt),
            "temporary": True,
            "writes_asset_json": False,
        }
        add_event(session_id, "ocrebuild.asset_image.provider_call.started", call_detail)
        image_bytes = generate_image_bytes(config, prompt, output_path, effective_reference)
        output_path.write_bytes(image_bytes)
        elapsed_seconds = round(time.time() - call_started, 3)
        result = {
            **call_detail,
            "ok": True,
            "output": output_rel,
            "output_path": str(output_path),
            "elapsed_seconds": elapsed_seconds,
            "used_reference_image": bool(effective_reference),
        }
        add_event(session_id, "ocrebuild.asset_image.generated", result)
        return result

    async def refine_asset_prompt(task_id: int, payload: OCRebuildAssetPromptRefinePayload) -> dict[str, Any]:
        task_row = get_task(task_id)
        session_id = int(task_row["session_id"])
        session_row = safe_session(session_id)
        workflow_id = normalize_workflow_id(payload.workflow_id)
        request_id = payload.request_id.strip() or f"prompt_refine_{now_ms()}_{uuid.uuid4().hex[:8]}"
        docs = load_local_prompt_docs(payload.provider.strip())
        requested = {
            "request_id": request_id,
            "workflow_id": workflow_id,
            "task_id": task_id,
            "session_id": session_id,
            "provider": payload.provider.strip(),
            "image_model": payload.image_model.strip(),
            "mode": payload.mode.strip() or "prompt_only",
            "round": payload.round,
            "docs_url": docs["docs_url"],
            "docs_source": docs.get("docs_source", "local_summary"),
            "docs_path": docs.get("docs_path", ""),
            "docs_fetched_realtime": False,
            "temporary": True,
            "writes_asset_json": False,
            "writes_database": False,
        }
        add_event(session_id, "ocrebuild.asset_image.prompt_refine.requested", {**requested, "user_instruction": payload.user_instruction[:500], "current_prompt_preview": payload.current_prompt[:1000]})
        run_provider = payload.provider.strip() or str(task_row.get("run_model_provider") or "").strip()
        run_model_id = payload.model.strip() or str(task_row.get("run_model_id") or "").strip()
        model, _prompt_models = await asyncio.to_thread(resolve_model, session_row, run_provider, run_model_id, "Run")
        add_event(session_id, "ocrebuild.asset_image.prompt_refine.run_model.started", {**requested, "run_model_provider": model["providerID"], "run_model_id": model["modelID"]})
        started_at = now_ms()
        user_content = f"""
Request ID:
{request_id}

Target provider: {payload.provider.strip()}
Target image model: {payload.image_model.strip()}
Generation mode: {payload.mode.strip() or 'prompt_only'}

Official provider documentation URL:
{docs['docs_url']}

Official provider documentation text fetched in realtime:
{docs['docs_text'] or '(No local summary text available. Proceed using only the current prompt and user edit instruction.)'}

Current image prompt:
{payload.current_prompt.strip()}

User edit instruction:
{payload.user_instruction.strip()}

Return strict JSON only with this exact request_id and the final image prompt:
{{"request_id":"{request_id}","prompt":"..."}}
""".strip()
        client = opencode_client_for(session_row)
        await asyncio.to_thread(
            client.prompt_async,
            str(session_row["opencode_session_id"]),
            user_content,
            model=model,
            system=IMAGE_PROMPT_REFINE_SYSTEM_PROMPT,
        )
        deadline = time.time() + 240
        assistant_text = None
        while time.time() < deadline:
            messages = await asyncio.to_thread(client.messages, str(session_row["opencode_session_id"]), limit=120)
            assistant_text = last_completed_assistant(messages, started_at)
            if assistant_text:
                break
            await asyncio.sleep(1)
        if not assistant_text:
            failed = {**requested, "run_model_provider": model["providerID"], "run_model_id": model["modelID"], "detail": "Run Model timed out before returning optimized prompt"}
            add_event(session_id, "ocrebuild.asset_image.prompt_refine.failed", failed)
            raise HTTPException(status_code=400, detail=failed["detail"])
        try:
            parsed = json.loads(assistant_text.strip())
        except Exception as exc:
            failed = {**requested, "run_model_provider": model["providerID"], "run_model_id": model["modelID"], "detail": "Run Model returned non-JSON prompt refine output"}
            add_event(session_id, "ocrebuild.asset_image.prompt_refine.failed", failed)
            raise HTTPException(status_code=400, detail=failed["detail"]) from exc
        prompt_text = str((parsed or {}).get("prompt") or "").strip() if isinstance(parsed, dict) else ""
        returned_request_id = str((parsed or {}).get("request_id") or "").strip() if isinstance(parsed, dict) else ""
        forbidden = ["## Goal", "shot-level plan", "Prompt Refresher"]
        if returned_request_id != request_id or not prompt_text or any(token.lower() in prompt_text.lower() for token in forbidden) or re.search(r"(?m)^#{1,6}\s+", prompt_text):
            failed = {**requested, "run_model_provider": model["providerID"], "run_model_id": model["modelID"], "detail": "Run Model prompt refine output failed request_id or content validation"}
            add_event(session_id, "ocrebuild.asset_image.prompt_refine.failed", failed)
            raise HTTPException(status_code=400, detail=failed["detail"])
        result = {**requested, "ok": True, "run_model_provider": model["providerID"], "run_model_id": model["modelID"], "prompt": prompt_text}
        add_event(session_id, "ocrebuild.asset_image.prompt_refine.completed", {**result, "prompt_preview": result["prompt"][:1000], "prompt_length": len(result["prompt"])})
        return result

    async def generate_host_product_final_prompt(task_id: int, payload: OCRebuildHostProductPromptPayload) -> dict[str, Any]:
        task_row = get_task(task_id)
        workspace = workspace_path(task_row)
        session_id = int(task_row["session_id"])
        session_row = safe_session(session_id)
        kind = payload.kind.strip()
        request_id = f"host_product_{kind}_{now_ms()}_{uuid.uuid4().hex[:8]}"
        refs = [str(item).strip() for item in payload.reference_images if str(item).strip()]
        requested = {
            "request_id": request_id,
            "task_id": task_id,
            "session_id": session_id,
            "kind": kind,
            "reference_count": len(refs),
            "guide_path": str(CONSISTENCY_REFERENCE_GUIDE_PATH),
            "temporary": False,
            "writes_workspace": True,
        }
        add_event(session_id, "ocrebuild.host_product_builder.prompt.requested", {**requested, "simple_prompt_preview": payload.simple_prompt[:1000]})
        run_provider = str(task_row.get("run_model_provider") or "").strip()
        run_model_id = str(task_row.get("run_model_id") or "").strip()
        model, _prompt_models = await asyncio.to_thread(resolve_model, session_row, run_provider, run_model_id, "Run")
        add_event(session_id, "ocrebuild.host_product_builder.prompt.run_model.started", {**requested, "run_model_provider": model["providerID"], "run_model_id": model["modelID"]})
        guide_text = read_consistency_guide()
        user_content = f"""
Request ID:
{request_id}

Builder kind:
{kind}

Task context:
- Task #{task_id}
- Session #{session_id}
- Product info: {task_row.get('product_info') or ''}
- Visual style: {task_row.get('visual_style') or ''}
- Constraints: {task_row.get('constraints') or ''}

Uploaded reference images in this Session workspace:
{builder_reference_summary(workspace, refs)}

User simple prompt:
{payload.simple_prompt.strip()}

Tool Library consistency guide:
{guide_text}

Return strict JSON only with this exact request_id and the final image generation prompt:
{{"request_id":"{request_id}","prompt":"..."}}
""".strip()
        client = opencode_client_for(session_row)
        started_at = now_ms()
        await asyncio.to_thread(
            client.prompt_async,
            str(session_row["opencode_session_id"]),
            user_content,
            model=model,
            system=HOST_PRODUCT_BUILDER_SYSTEM_PROMPT,
        )
        deadline = time.time() + 240
        assistant_text = None
        while time.time() < deadline:
            messages = await asyncio.to_thread(client.messages, str(session_row["opencode_session_id"]), limit=120)
            assistant_text = last_completed_assistant(messages, started_at)
            if assistant_text:
                break
            await asyncio.sleep(1)
        if not assistant_text:
            failed = {**requested, "run_model_provider": model["providerID"], "run_model_id": model["modelID"], "detail": "Run Model timed out before returning Host & Product Builder prompt"}
            add_event(session_id, "ocrebuild.host_product_builder.prompt.failed", failed)
            raise HTTPException(status_code=400, detail=failed["detail"])
        provider_error = provider_error_page_detail(assistant_text)
        if provider_error:
            failed = {**requested, "run_model_provider": model["providerID"], "run_model_id": model["modelID"], "detail": provider_error}
            add_event(session_id, "ocrebuild.host_product_builder.prompt.failed", failed)
            raise HTTPException(status_code=502, detail=provider_error)
        try:
            parsed = json.loads(assistant_text.strip())
        except Exception as exc:
            failed = {**requested, "run_model_provider": model["providerID"], "run_model_id": model["modelID"], "detail": "Run Model returned non-JSON Host & Product Builder output"}
            add_event(session_id, "ocrebuild.host_product_builder.prompt.failed", failed)
            raise HTTPException(status_code=400, detail=failed["detail"]) from exc
        final_prompt = str((parsed or {}).get("prompt") or "").strip() if isinstance(parsed, dict) else ""
        returned_request_id = str((parsed or {}).get("request_id") or "").strip() if isinstance(parsed, dict) else ""
        provider_error = provider_error_page_detail(final_prompt)
        if provider_error:
            failed = {**requested, "run_model_provider": model["providerID"], "run_model_id": model["modelID"], "detail": provider_error}
            add_event(session_id, "ocrebuild.host_product_builder.prompt.failed", failed)
            raise HTTPException(status_code=502, detail=provider_error)
        if returned_request_id != request_id or len(final_prompt) < 200:
            failed = {**requested, "run_model_provider": model["providerID"], "run_model_id": model["modelID"], "detail": "Run Model Host & Product Builder output failed request_id or prompt validation"}
            add_event(session_id, "ocrebuild.host_product_builder.prompt.failed", failed)
            raise HTTPException(status_code=400, detail=failed["detail"])
        simple_path = builder_prompt_path(workspace, kind, "simple_prompt.txt")
        final_path = builder_prompt_path(workspace, kind, "final_prompt.txt")
        simple_path.parent.mkdir(parents=True, exist_ok=True)
        simple_path.write_text(payload.simple_prompt.strip(), encoding="utf-8")
        final_path.write_text(final_prompt, encoding="utf-8")
        section = write_builder_section(workspace, kind, {
            "simple_prompt": payload.simple_prompt.strip(),
            "final_prompt": final_prompt,
            "reference_images": refs,
            "simple_prompt_path": builder_rel(workspace, simple_path),
            "final_prompt_path": builder_rel(workspace, final_path),
            "run_model_provider": model["providerID"],
            "run_model_id": model["modelID"],
        })
        config = read_json_file(builder_config_path(workspace))
        config["task_id"] = task_id
        config["session_id"] = session_id
        config["updated_at"] = now_ms()
        write_json_file(builder_config_path(workspace), config)
        result = {**requested, "ok": True, "run_model_provider": model["providerID"], "run_model_id": model["modelID"], "prompt": final_prompt, "section": section}
        add_event(session_id, "ocrebuild.host_product_builder.prompt.completed", {**result, "prompt_preview": final_prompt[:1000], "prompt_length": len(final_prompt)})
        return result

    def generate_host_product_image(task_id: int, payload: OCRebuildHostProductGeneratePayload) -> dict[str, Any]:
        task_row = get_task(task_id)
        workspace = workspace_path(task_row)
        session_id = int(task_row["session_id"])
        kind = payload.kind.strip()
        refs = [str(item).strip() for item in payload.reference_images if str(item).strip()]
        reference_paths = [resolve_workspace_rel(workspace, rel) for rel in refs]
        if reference_paths:
            config, reference_image_provider_fallback_from = load_reference_image_config(payload.provider.strip(), payload.model.strip())
        else:
            config = load_image_config(payload.provider.strip(), payload.model.strip()) if payload.provider.strip() else load_active_image_config()
            reference_image_provider_fallback_from = ""
        output_path = builder_section_dir(workspace, kind) / builder_output_name(kind)
        request_params_path = builder_section_dir(workspace, kind) / "runs" / f"request_params_{now_ms()}.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        call_started = time.time()
        api_call_id = f"host_product_{kind}_image_{now_ms()}_{uuid.uuid4().hex[:8]}"
        endpoint = image_provider_endpoint(config["provider"], config["model"], reference_paths[0] if reference_paths else None)
        call_detail = {
            "api_call_id": api_call_id,
            "task_id": task_id,
            "session_id": session_id,
            "kind": kind,
            "provider": config["provider"],
            "model": config["model"],
            "endpoint": endpoint,
            "method": "POST",
            "reference_images": refs,
            "reference_count": len(reference_paths),
            "workspace_dir": str(workspace),
            "output": builder_rel(workspace, output_path),
            "output_path": str(output_path),
            "prompt_preview": payload.prompt[:1000],
            "prompt_length": len(payload.prompt),
        }
        if reference_image_provider_fallback_from:
            call_detail["reference_image_provider_fallback_from"] = reference_image_provider_fallback_from
        write_json_file(request_params_path, {**call_detail, "prompt": payload.prompt})
        add_event(session_id, "ocrebuild.host_product_builder.image.provider_call.started", call_detail)
        image_bytes = generate_image_bytes(config, payload.prompt, output_path, reference_paths or None, "1536x1024")
        output_path.write_bytes(image_bytes)
        elapsed_seconds = round(time.time() - call_started, 3)
        final_prompt_path = builder_prompt_path(workspace, kind, "final_prompt.txt")
        if not final_prompt_path.exists():
            final_prompt_path.parent.mkdir(parents=True, exist_ok=True)
            final_prompt_path.write_text(payload.prompt, encoding="utf-8")
        output_rel = builder_rel(workspace, output_path)
        section = write_builder_section(workspace, kind, {
            "final_prompt": payload.prompt,
            "final_prompt_path": builder_rel(workspace, final_prompt_path),
            "reference_images": refs,
            "output": output_rel,
            "output_path": str(output_path),
            "provider": config["provider"],
            "model": config["model"],
            "generated_at": now_ms(),
            "last_request_params_path": builder_rel(workspace, request_params_path),
        })
        current_config = read_json_file(builder_config_path(workspace))
        active = current_config.get("active") if isinstance(current_config.get("active"), dict) else {}
        active["host_reference" if kind == "host" else "product_reference"] = output_rel
        current_config.update({"task_id": task_id, "session_id": session_id, "active": active, "image_model": {"provider": config["provider"], "model": config["model"]}, "updated_at": now_ms()})
        write_json_file(builder_config_path(workspace), current_config)
        result = {**call_detail, "ok": True, "elapsed_seconds": elapsed_seconds, "output": output_rel, "output_path": str(output_path), "section": section}
        add_event(session_id, "ocrebuild.host_product_builder.image.generated", result)
        return result

    async def refine_asset_video_prompt(task_id: int, payload: OCRebuildAssetVideoPromptRefinePayload) -> dict[str, Any]:
        task_row = get_task(task_id)
        session_id = int(task_row["session_id"])
        session_row = safe_session(session_id)
        workflow_id = normalize_workflow_id(payload.workflow_id)
        request_id = payload.request_id.strip() or f"video_prompt_refine_{now_ms()}_{uuid.uuid4().hex[:8]}"
        docs = load_local_video_prompt_docs(payload.provider.strip())
        requested = {
            "request_id": request_id,
            "workflow_id": workflow_id,
            "task_id": task_id,
            "session_id": session_id,
            "provider": payload.provider.strip(),
            "video_model": payload.video_model.strip(),
            "input_mode": payload.input_mode.strip() or "first_frame",
            "duration": payload.duration,
            "docs_url": docs["docs_url"],
            "docs_source": docs.get("docs_source", "local_summary"),
            "docs_path": docs.get("docs_path", ""),
            "docs_fetched_realtime": False,
            "temporary": True,
            "writes_asset_json": False,
            "writes_database": False,
        }
        add_event(session_id, "ocrebuild.asset_video.prompt_refine.requested", {**requested, "user_instruction": payload.user_instruction[:500], "current_prompt_preview": payload.current_prompt[:1000]})
        run_provider = str(task_row.get("run_model_provider") or "").strip()
        run_model_id = str(task_row.get("run_model_id") or "").strip()
        model, _prompt_models = await asyncio.to_thread(resolve_model, session_row, run_provider, run_model_id, "Run")
        add_event(session_id, "ocrebuild.asset_video.prompt_refine.run_model.started", {**requested, "run_model_provider": model["providerID"], "run_model_id": model["modelID"]})
        started_at = now_ms()
        user_content = f"""
Request ID:
{request_id}

Target provider: {payload.provider.strip()}
Target video model: {payload.video_model.strip()}
Input mode: {payload.input_mode.strip() or 'first_frame'}
Scene duration seconds: {payload.duration if payload.duration is not None else 'unknown'}

Official provider documentation URL:
{docs['docs_url']}

Local provider documentation summary:
{docs['docs_text'] or '(No local summary text available. Proceed using only the current prompt and user edit instruction.)'}

Current video prompt from Asset plan:
{payload.current_prompt.strip()}

User edit instruction:
{payload.user_instruction.strip()}

Return strict JSON only with this exact request_id and the final video prompt:
{{"request_id":"{request_id}","prompt":"..."}}
""".strip()
        client = opencode_client_for(session_row)
        await asyncio.to_thread(
            client.prompt_async,
            str(session_row["opencode_session_id"]),
            user_content,
            model=model,
            system=VIDEO_PROMPT_REFINE_SYSTEM_PROMPT,
        )
        deadline = time.time() + 240
        assistant_text = None
        while time.time() < deadline:
            messages = await asyncio.to_thread(client.messages, str(session_row["opencode_session_id"]), limit=120)
            assistant_text = last_completed_assistant(messages, started_at)
            if assistant_text:
                break
            await asyncio.sleep(1)
        if not assistant_text:
            failed = {**requested, "run_model_provider": model["providerID"], "run_model_id": model["modelID"], "detail": "Run Model timed out before returning optimized video prompt"}
            add_event(session_id, "ocrebuild.asset_video.prompt_refine.failed", failed)
            raise HTTPException(status_code=400, detail=failed["detail"])
        try:
            parsed = json.loads(assistant_text.strip())
        except Exception as exc:
            failed = {**requested, "run_model_provider": model["providerID"], "run_model_id": model["modelID"], "detail": "Run Model returned non-JSON video prompt refine output"}
            add_event(session_id, "ocrebuild.asset_video.prompt_refine.failed", failed)
            raise HTTPException(status_code=400, detail=failed["detail"]) from exc
        prompt_text = str((parsed or {}).get("prompt") or "").strip() if isinstance(parsed, dict) else ""
        returned_request_id = str((parsed or {}).get("request_id") or "").strip() if isinstance(parsed, dict) else ""
        forbidden = ["## Goal", "shot-level plan", "Prompt Refresher"]
        if returned_request_id != request_id or not prompt_text or any(token.lower() in prompt_text.lower() for token in forbidden) or re.search(r"(?m)^#{1,6}\s+", prompt_text):
            failed = {**requested, "run_model_provider": model["providerID"], "run_model_id": model["modelID"], "detail": "Run Model video prompt refine output failed request_id or content validation"}
            add_event(session_id, "ocrebuild.asset_video.prompt_refine.failed", failed)
            raise HTTPException(status_code=400, detail=failed["detail"])
        result = {**requested, "ok": True, "run_model_provider": model["providerID"], "run_model_id": model["modelID"], "prompt": prompt_text}
        add_event(session_id, "ocrebuild.asset_video.prompt_refine.completed", {**result, "prompt_preview": result["prompt"][:1000], "prompt_length": len(result["prompt"])})
        return result

    def last_completed_assistant(messages: list[dict[str, Any]], started_after: int) -> str | None:
        for message in reversed(messages):
            info = message.get("info") or {}
            if info.get("role") != "assistant":
                continue
            completed = int(((info.get("time") or {}).get("completed") or 0) or 0)
            if completed < started_after:
                continue
            text = "\n".join([str(part.get("text") or "").strip() for part in (message.get("parts") or []) if part.get("type") == "text"]).strip()
            if text:
                return text
        return None

    def sort_keyframes(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
        def frame_time(frame: dict[str, Any]) -> float:
            try:
                return float(frame.get("time"))
            except (TypeError, ValueError):
                return 1_000_000.0
        return sorted(frames, key=lambda frame: (frame_time(frame), str(frame.get("path") or "")))

    def canonicalize_shot_scene_marks(shot_id: str, keyframes: list[dict[str, Any]], scene_marks: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
        frame_order = {str(frame.get("path") or ""): index for index, frame in enumerate(sort_keyframes(keyframes)) if isinstance(frame, dict)}

        def mark_sort_key(mark: dict[str, Any]) -> tuple[int, float, str]:
            keyframe_info = mark.get("keyframes") if isinstance(mark.get("keyframes"), dict) else {}
            paths = [str(keyframe_info.get(key) or "") for key in ("single", "first", "last")]
            first_order = min([frame_order[path] for path in paths if path in frame_order] or [1_000_000])
            try:
                start = float(mark.get("start"))
            except (TypeError, ValueError):
                start = 1_000_000.0
            return (first_order, start, str(mark.get("scene_mark_id") or ""))

        ordered_marks = [dict(mark) for mark in scene_marks if isinstance(mark, dict)]
        ordered_marks.sort(key=mark_sort_key)
        id_map: dict[str, str] = {}
        next_marks: list[dict[str, Any]] = []
        for index, mark in enumerate(ordered_marks, start=1):
            old_id = str(mark.get("scene_mark_id") or "").strip()
            canonical_id = f"{shot_id}_scene_{index:03d}"
            if old_id:
                id_map[old_id] = canonical_id
            next_mark = dict(mark)
            next_mark["scene_mark_id"] = canonical_id
            next_mark["shot_id"] = shot_id
            next_mark["scene_index"] = index
            generation_mode = "first_last" if str(next_mark.get("generation_mode") or next_mark.get("asset_generation_mode") or "first_frame") == "first_last" else "first_frame"
            next_mark["generation_mode"] = generation_mode
            plan_a = next_mark.get("plan_a") if isinstance(next_mark.get("plan_a"), dict) else {}
            scene_asset = plan_a.get("scene_asset") if isinstance(plan_a.get("scene_asset"), dict) else {}
            scene_asset["uses_only_first_frame"] = generation_mode != "first_last"
            plan_a["scene_asset"] = scene_asset
            keyframe_info = next_mark.get("keyframes") if isinstance(next_mark.get("keyframes"), dict) else {}
            first_path = str(keyframe_info.get("first") or keyframe_info.get("single") or "").strip()
            last_path = str(keyframe_info.get("last") or first_path).strip()
            if first_path and last_path:
                plan_a["first_last_marked"] = True
                plan_a["first_last_confirmed"] = True
                plan_a["ui_saved_confirmed"] = True
                plan_a["scene_confirmed"] = True
                plan_a["confirmed_at"] = now_ms()
                plan_a["confirmed_by"] = "frontend_scene_marks_save"
            next_mark["plan_a"] = plan_a
            next_marks.append(next_mark)
        next_keyframes: list[dict[str, Any]] = []
        valid_ids = {mark["scene_mark_id"] for mark in next_marks}
        for frame in keyframes:
            next_frame = dict(frame)
            scene_mark = next_frame.get("scene_mark") if isinstance(next_frame.get("scene_mark"), dict) else None
            if scene_mark:
                old_id = str(scene_mark.get("scene_mark_id") or "")
                canonical_id = id_map.get(old_id, old_id)
                if canonical_id in valid_ids:
                    next_frame["scene_mark"] = {**scene_mark, "scene_mark_id": canonical_id, "scene_index": next((mark["scene_index"] for mark in next_marks if mark["scene_mark_id"] == canonical_id), scene_mark.get("scene_index"))}
                else:
                    next_frame.pop("scene_mark", None)
            next_keyframes.append(next_frame)
        return sort_keyframes(next_keyframes), next_marks, id_map

    def canonical_scene_mark_summary(reference: dict[str, Any]) -> dict[str, Any]:
        summary = reference.get("scene_mark_summary") if isinstance(reference.get("scene_mark_summary"), dict) else {}
        next_summary = {**summary, "manual_boundary_edits": True, "updated_by": "frontend", "updated_at": now_ms(), "scene_id_mode": "canonical"}
        next_summary.pop("scene_id_map", None)
        return next_summary

    @router.get("/api/ocrebuild/tasks")
    async def list_tasks() -> dict[str, Any]:
        return {"items": repo.list_tasks()}

    @router.post("/api/ocrebuild/tasks")
    async def create_task(request: Request) -> dict[str, Any]:
        created = now_ms()
        analysis_items = analysis_repo.list_task_summaries()
        analysis_task_id = int(analysis_items[0]["id"]) if analysis_items else None
        session_id = ctx.session_repo.create(source=OC_REBUILD_SOURCE, group_id=OC_REBUILD_GROUP_ID, sender_name="OC-Rebuild", title=ctx.next_session_title(), command_text="", status="queued", workspace_dir=str(ctx.workspace_store.sessions_root() / "pending" / str(created) / "workspace"), share_token=ctx.new_share_token(), created_at=created, updated_at=created)
        workspace_dir = ctx.workspace_store.create_session_workspace(session_id)
        ctx.session_repo.update(session_id, workspace_dir=str(workspace_dir), updated_at=created)
        session_row = safe_session(session_id)
        op_session = opencode_client_for(session_row).create_session(str(session_row["title"]))
        ctx.session_repo.update(session_id, opencode_session_id=str(op_session["id"]), status="draft", updated_at=now_ms())
        task_id = repo.create_task(session_id=session_id, analysis_task_id=analysis_task_id, status="draft", source_package_path="source_package.json", source_scheme="detail", target_topic="", target_platform="抖音", aspect_ratio="9:16", target_count=3, target_audience="", product_info="", rebuild_goal="复刻参考视频结构", preserve_strategy_json=json.dumps({"duration_pattern": True, "subtitle_timing": True, "semantic_roles": True, "title_layout": True, "transition_rhythm": True, "camera_style": True, "emotion_arc": True}, ensure_ascii=False), replace_strategy_json=json.dumps({"topic": True, "visuals": True, "voiceover": True, "subtitles": True, "title_copy": True, "product": True, "persona": True, "bgm": False}, ensure_ascii=False), visual_style="真实口播", subtitle_style="底部大字", title_style="顶部强钩子", voice_style="年轻中文旁白", batch_variables="", constraints="", simple_prompt="", final_prompt="", prompt_model_provider="", prompt_model_id="", run_model_provider="", run_model_id="", workflow_mode="rebuild", created_at=created, updated_at=created)
        add_event(session_id, "ocrebuild.task.created", {"task_id": task_id, "analysis_task_id": analysis_task_id})
        detail = serialize_task_detail(get_task(task_id))
        detail["prompt_models"] = serialize_prompt_models(safe_session(session_id))
        return detail

    @router.get("/api/ocrebuild/tasks/{task_id}")
    async def task_detail(task_id: int) -> dict[str, Any]:
        task_row = get_task(task_id)
        detail = serialize_task_detail(task_row)
        try:
            detail["prompt_models"] = serialize_prompt_models(safe_session(int(task_row["session_id"])))
        except Exception as exc:
            detail["prompt_models"] = {"items": [], "default_model": {"providerID": "", "modelID": ""}, "error": str(exc)}
        return detail

    @router.get("/api/ocrebuild/tasks/{task_id}/host-product-builder")
    async def get_host_product_builder(task_id: int) -> dict[str, Any]:
        return serialize_host_product_builder(get_task(task_id))

    @router.post("/api/ocrebuild/tasks/{task_id}/host-product-builder/uploads")
    async def upload_host_product_builder_refs(task_id: int, kind: str = Form(...), files: list[UploadFile] = File(...)) -> dict[str, Any]:
        task_row = get_task(task_id)
        workspace = workspace_path(task_row)
        normalized_kind = kind.strip()
        if normalized_kind not in {"host", "product"}:
            raise HTTPException(status_code=400, detail="kind must be host or product")
        upload_dir = builder_section_dir(workspace, normalized_kind) / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        saved: list[dict[str, str]] = []
        for index, upload in enumerate(files, start=1):
            content = await upload.read()
            if not content:
                continue
            target = upload_dir / safe_upload_name(upload.filename or "", f"reference_{index}")
            target.write_bytes(content)
            saved.append({"path": builder_rel(workspace, target), "filename": upload.filename or target.name, "content_type": upload.content_type or ""})
        current = read_builder_section(workspace, normalized_kind)
        existing = [str(item) for item in current.get("reference_images") or [] if str(item).strip()]
        refs = [*existing, *[item["path"] for item in saved]]
        section = write_builder_section(workspace, normalized_kind, {"reference_images": refs})
        add_event(int(task_row["session_id"]), "ocrebuild.host_product_builder.uploaded", {"task_id": task_id, "session_id": int(task_row["session_id"]), "kind": normalized_kind, "count": len(saved), "reference_images": refs})
        return {"ok": True, "kind": normalized_kind, "items": saved, "section": section, "state": serialize_host_product_builder(task_row)}

    @router.delete("/api/ocrebuild/tasks/{task_id}/host-product-builder/reference")
    async def delete_host_product_builder_ref(task_id: int, payload: OCRebuildHostProductDeletePayload) -> dict[str, Any]:
        task_row = get_task(task_id)
        workspace = workspace_path(task_row)
        kind = payload.kind.strip()
        target_rel = payload.path.strip()
        current = read_builder_section(workspace, kind)
        existing = [str(item) for item in current.get("reference_images") or [] if str(item).strip()]
        refs = [item for item in existing if item != target_rel]
        if target_rel:
            target = resolve_workspace_rel_for_write(workspace, target_rel)
            if target.exists() and target.is_file() and "consistency_references" in target.parts and "uploads" in target.parts:
                target.unlink()
        section = write_builder_section(workspace, kind, {"reference_images": refs})
        add_event(int(task_row["session_id"]), "ocrebuild.host_product_builder.reference.deleted", {"task_id": task_id, "session_id": int(task_row["session_id"]), "kind": kind, "path": target_rel, "reference_images": refs})
        return {"ok": True, "kind": kind, "section": section, "state": serialize_host_product_builder(task_row)}

    @router.post("/api/ocrebuild/tasks/{task_id}/host-product-builder/output")
    async def upload_host_product_builder_output(task_id: int, kind: str = Form(...), file: UploadFile = File(...)) -> dict[str, Any]:
        task_row = get_task(task_id)
        workspace = workspace_path(task_row)
        normalized_kind = kind.strip()
        if normalized_kind not in {"host", "product"}:
            raise HTTPException(status_code=400, detail="kind must be host or product")
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Output image is empty")
        output_path = builder_section_dir(workspace, normalized_kind) / builder_output_name(normalized_kind)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(content)
        output_rel = builder_rel(workspace, output_path)
        section = write_builder_section(workspace, normalized_kind, {
            "output": output_rel,
            "output_path": str(output_path),
            "uploaded_output_filename": file.filename or output_path.name,
            "uploaded_output_content_type": file.content_type or "",
            "uploaded_at": now_ms(),
        })
        config = read_json_file(builder_config_path(workspace))
        active = config.get("active") if isinstance(config.get("active"), dict) else {}
        active[f"{normalized_kind}_reference"] = output_rel
        config["active"] = active
        config["updated_at"] = now_ms()
        write_json_file(builder_config_path(workspace), config)
        add_event(int(task_row["session_id"]), "ocrebuild.host_product_builder.output.uploaded", {"task_id": task_id, "session_id": int(task_row["session_id"]), "kind": normalized_kind, "output": output_rel})
        return {"ok": True, "kind": normalized_kind, "output": output_rel, "section": section, "state": serialize_host_product_builder(task_row)}

    @router.delete("/api/ocrebuild/tasks/{task_id}/host-product-builder/output")
    async def delete_host_product_builder_output(task_id: int, payload: OCRebuildHostProductDeletePayload) -> dict[str, Any]:
        task_row = get_task(task_id)
        workspace = workspace_path(task_row)
        kind = payload.kind.strip()
        output_rel = payload.path.strip() or f"consistency_references/{builder_kind_dir(kind)}/{builder_output_name(kind)}"
        target = resolve_workspace_rel_for_write(workspace, output_rel)
        if target.exists() and target.is_file():
            target.unlink()
        section = write_builder_section(workspace, kind, {"output": "", "output_path": ""})
        config = read_json_file(builder_config_path(workspace))
        active = config.get("active") if isinstance(config.get("active"), dict) else {}
        active.pop(f"{kind}_reference", None)
        config["active"] = active
        config["updated_at"] = now_ms()
        write_json_file(builder_config_path(workspace), config)
        add_event(int(task_row["session_id"]), "ocrebuild.host_product_builder.output.deleted", {"task_id": task_id, "session_id": int(task_row["session_id"]), "kind": kind, "output": output_rel})
        return {"ok": True, "kind": kind, "section": section, "state": serialize_host_product_builder(task_row)}

    @router.post("/api/ocrebuild/tasks/{task_id}/host-product-builder/prompt")
    async def host_product_builder_prompt(task_id: int, payload: OCRebuildHostProductPromptPayload) -> dict[str, Any]:
        return await generate_host_product_final_prompt(task_id, payload)

    @router.post("/api/ocrebuild/tasks/{task_id}/host-product-builder/generate/events")
    async def host_product_builder_generate_events(task_id: int, payload: OCRebuildHostProductGeneratePayload) -> StreamingResponse:
        task_row = get_task(task_id)
        session_id = int(task_row["session_id"])
        workspace = workspace_path(task_row)
        request_payload = {"task_id": task_id, "session_id": session_id, "kind": payload.kind, "reference_count": len(payload.reference_images), "workspace_dir": str(workspace)}
        add_event(session_id, "ocrebuild.host_product_builder.image.requested", {**request_payload, "prompt_preview": payload.prompt[:1000], "prompt_length": len(payload.prompt)})

        async def event_generator() -> Any:
            started = time.time()
            yield f"data: {json.dumps({'type': 'started', **request_payload}, ensure_ascii=True)}\n\n"
            task = asyncio.create_task(asyncio.to_thread(generate_host_product_image, task_id, payload))
            heartbeat_no = 0
            while not task.done():
                await asyncio.sleep(2)
                if task.done():
                    break
                heartbeat_no += 1
                heartbeat_payload = {**request_payload, "heartbeat": heartbeat_no, "elapsed_seconds": round(time.time() - started, 1)}
                add_event(session_id, "ocrebuild.host_product_builder.image.heartbeat", heartbeat_payload)
                yield f"data: {json.dumps({'type': 'heartbeat', **heartbeat_payload}, ensure_ascii=True)}\n\n"
            try:
                result = await task
            except HTTPException as exc:
                failed = {**request_payload, "status_code": exc.status_code, "detail": exc.detail, "elapsed_seconds": round(time.time() - started, 1)}
                add_event(session_id, "ocrebuild.host_product_builder.image.failed", failed)
                yield f"data: {json.dumps({'type': 'failed', **failed}, ensure_ascii=True)}\n\n"
                return
            except Exception as exc:
                failed = {**request_payload, "detail": str(exc), "elapsed_seconds": round(time.time() - started, 1)}
                add_event(session_id, "ocrebuild.host_product_builder.image.failed", failed)
                yield f"data: {json.dumps({'type': 'failed', **failed}, ensure_ascii=True)}\n\n"
                return
            yield f"data: {json.dumps({'type': 'completed', **result, 'elapsed_seconds': round(time.time() - started, 1)}, ensure_ascii=True)}\n\n"

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    @router.get("/api/ocrebuild/tasks/{task_id}/shot-plan")
    async def get_shot_plan(task_id: int) -> dict[str, Any]:
        task_row = get_task(task_id)
        backfill_storyboard_copy_workspace(task_row)
        workspace = workspace_path(task_row)
        plan = read_workspace_json(shot_plan_path(workspace), "rebuild_shot_plan.json")
        validate_shot_plan_context(task_row, plan)
        hydrated = hydrate_shot_plan_srt_sources(workspace, task_row, plan)
        hydrated["storyboard_phase2"] = storyboard_phase2_state(workspace)
        return hydrated

    @router.get("/api/ocrebuild/tasks/{task_id}/shot-plan/final-prompts/{shot_id}")
    async def get_shot_final_prompts(task_id: int, shot_id: str) -> dict[str, Any]:
        task_row = get_task(task_id)
        workspace = workspace_path(task_row)
        plan = read_workspace_json(shot_plan_path(workspace), "rebuild_shot_plan.json")
        validate_shot_plan_context(task_row, plan)
        shot = find_shot_in_plan(plan, shot_id)
        package, rel_path = final_prompt_package_for_shot(workspace, shot)
        image_prompt_build: dict[str, Any] | None = None
        if final_prompt_package_needs_plan_d_image_prompts(package) and not suppress_plan_d_prompt_autobuild(workspace):
            image_prompt_build = await run_plan_d_image_prompt_builder(task_row, shot_id)
            if image_prompt_build.get("status") == "completed":
                plan = read_workspace_json(shot_plan_path(workspace), "rebuild_shot_plan.json")
                validate_shot_plan_context(task_row, plan)
                shot = find_shot_in_plan(plan, shot_id)
                package, rel_path = final_prompt_package_for_shot(workspace, shot)
        return {"ok": True, "task_id": task_id, "shot_id": shot_id, "path": rel_path, "package": package, "image_prompt_build": image_prompt_build}

    @router.put("/api/ocrebuild/tasks/{task_id}/shot-plan/final-prompts/{shot_id}")
    async def save_shot_final_prompts(task_id: int, shot_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        task_row = get_task(task_id)
        workspace = workspace_path(task_row)
        plan_path = shot_plan_path(workspace)
        plan = read_workspace_json(plan_path, "rebuild_shot_plan.json")
        validate_shot_plan_context(task_row, plan)
        shot = find_shot_in_plan(plan, shot_id)
        existing_package, rel_path = final_prompt_package_for_shot(workspace, shot)
        package_payload = payload.get("package") if isinstance(payload.get("package"), dict) else payload
        if not isinstance(package_payload, dict):
            raise HTTPException(status_code=400, detail="Final prompt package payload is required")
        package = normalize_final_prompt_package_payload(package_payload, shot_id, existing_package)
        sync_final_prompt_package_to_plan(shot, package, rel_path)
        write_json_file(resolve_workspace_rel_for_write(workspace, rel_path), package)
        write_shot_plan_atomic(plan_path, plan)
        add_event(int(task_row["session_id"]), "ocrebuild.shot_final_prompts.saved", {"task_id": task_id, "shot_id": shot_id, "path": rel_path, "scene_count": len(package.get("scenes") or [])})
        return {"ok": True, "task_id": task_id, "shot_id": shot_id, "path": rel_path, "package": package, "shot_plan": plan}

    @router.post("/api/ocrebuild/tasks/{task_id}/srt-rewrite/generate")
    async def generate_srt_rewrite(task_id: int, payload: OCRebuildSRTRewriteGeneratePayload) -> dict[str, Any]:
        task_row = get_task(task_id)
        workspace = workspace_path(task_row)
        plan = read_workspace_json(shot_plan_path(workspace), "rebuild_shot_plan.json")
        validate_shot_plan_context(task_row, plan)
        plan = hydrate_shot_plan_srt_sources(workspace, task_row, plan)
        rows = enrich_srt_rewrite_rows_from_plan(plan, normalize_srt_rewrite_rows(payload.rows))
        if not rows:
            raise HTTPException(status_code=400, detail="At least one Scene SRT row is required")
        session_row = safe_session(int(task_row["session_id"]))
        provider = payload.run_model_provider.strip() or str(task_row.get("run_model_provider") or "")
        model_id = payload.run_model_id.strip() or str(task_row.get("run_model_id") or "")
        model, prompt_models = resolve_model(session_row, provider, model_id, "Run")
        started_at = now_ms()
        client = opencode_client_for(session_row)
        client.prompt_async(
            str(session_row["opencode_session_id"]),
            srt_rewrite_user_content(task_row, payload.prompt, rows),
            model=model,
            system=SRT_REWRITE_SYSTEM_PROMPT,
        )
        deadline = time.time() + 240
        assistant_text = None
        while time.time() < deadline:
            assistant_text = last_completed_assistant(client.messages(str(session_row["opencode_session_id"]), limit=120), started_at)
            if assistant_text:
                break
            await asyncio.sleep(1)
        if not assistant_text:
            raise HTTPException(status_code=400, detail="Run Model timed out before returning SRT rewrite output")
        rewritten = parse_srt_rewrite_response(assistant_text, rows)
        add_event(int(task_row["session_id"]), "ocrebuild.srt_rewrite.generated", {"task_id": task_id, "row_count": len(rewritten), "provider": model["providerID"], "model": model["modelID"]})
        return {"ok": True, "rows": rewritten, "prompt_models": prompt_models, "used_run_model_provider": model["providerID"], "used_run_model_id": model["modelID"]}

    @router.put("/api/ocrebuild/tasks/{task_id}/srt-rewrite")
    async def save_srt_rewrite(task_id: int, payload: OCRebuildSRTRewriteSavePayload) -> dict[str, Any]:
        task_row = get_task(task_id)
        workspace = workspace_path(task_row)
        plan_path = shot_plan_path(workspace)
        plan = read_workspace_json(plan_path, "rebuild_shot_plan.json")
        validate_shot_plan_context(task_row, plan)
        plan = hydrate_shot_plan_srt_sources(workspace, task_row, plan)
        rows = enrich_srt_rewrite_rows_from_plan(plan, normalize_srt_rewrite_rows(payload.rows))
        if not rows:
            raise HTTPException(status_code=400, detail="At least one Scene SRT row is required")
        saved = save_srt_rewrite_rows_to_plan(plan_path, plan, rows)
        add_event(int(task_row["session_id"]), "ocrebuild.srt_rewrite.saved", {"task_id": task_id, "row_count": len(saved)})
        return {"ok": True, "rows": saved, "shot_plan": plan}

    @router.delete("/api/ocrebuild/tasks/{task_id}")
    async def delete_task(task_id: int) -> dict[str, Any]:
        task_row = get_task(task_id)
        session_id = int(task_row["session_id"])
        session_row = safe_session(session_id)
        ctx.workflow_deletion_service.delete_session_db_first(session_row)
        try:
            ctx.workflow_deletion_service.cleanup_workspace(session_row)
        except Exception as exc:
            ctx.event("warning", "cleanup", "Workspace cleanup failed after OC-Rebuild DB deletion", {"session_id": session_id, "task_id": task_id, "error": str(exc)})
        return {"ok": True}

    @router.put("/api/ocrebuild/tasks/{task_id}/config")
    async def save_config(task_id: int, payload: OCRebuildTaskUpdatePayload) -> dict[str, Any]:
        task_row = get_task(task_id)
        values = normalize_payload(payload)
        if values["analysis_task_id"]:
            get_analysis_task(values["analysis_task_id"])
        repo.update_task(task_id, **values, updated_at=now_ms())
        add_event(int(task_row["session_id"]), "ocrebuild.config.saved", {"task_id": task_id})
        return serialize_task_detail(get_task(task_id))

    @router.post("/api/ocrebuild/tasks/{task_id}/simple-prompt/rebuild")
    async def rebuild_simple_prompt(task_id: int, payload: OCRebuildTaskUpdatePayload) -> dict[str, Any]:
        await save_config(task_id, payload)
        task_row = get_task(task_id)
        simple_prompt = build_simple_prompt(task_row)
        repo.update_task(task_id, simple_prompt=simple_prompt, updated_at=now_ms())
        return serialize_task_detail(get_task(task_id))

    @router.post("/api/ocrebuild/tasks/{task_id}/generate-prompt")
    async def generate_prompt(task_id: int, payload: OCRebuildPromptGeneratePayload) -> dict[str, Any]:
        task_row = get_task(task_id)
        session_row = safe_session(int(task_row["session_id"]))
        if not str(task_row.get("simple_prompt") or "").strip():
            raise HTTPException(status_code=400, detail="Simple Prompt is required before generating Final Prompt")
        model, prompt_models = resolve_model(session_row, payload.prompt_model_provider, payload.prompt_model_id, "Prompt")
        started_at = now_ms()
        client = opencode_client_for(session_row)
        client.prompt_async(str(session_row["opencode_session_id"]), final_prompt_user_content(task_row), model=model, system=FINAL_INTENT_SYSTEM_PROMPT)
        deadline = time.time() + 240
        assistant_text = None
        while time.time() < deadline:
            assistant_text = last_completed_assistant(client.messages(str(session_row["opencode_session_id"]), limit=120), started_at)
            if assistant_text:
                break
            await asyncio.sleep(1)
        if not assistant_text:
            raise HTTPException(status_code=400, detail="OpenCode timed out before returning Final Prompt")
        repo.update_task(task_id, final_prompt=assistant_text.strip(), prompt_model_provider=model["providerID"], prompt_model_id=model["modelID"], updated_at=now_ms())
        detail = serialize_task_detail(get_task(task_id))
        detail["prompt_models"] = prompt_models
        return detail

    @router.post("/api/ocrebuild/tasks/{task_id}/versions")
    async def save_version(task_id: int, payload: OCRebuildVersionSavePayload) -> dict[str, Any]:
        task_row = get_task(task_id)
        version = repo.create_version(task_id=task_id, name=payload.version_name.strip() or f"Intent {now_ms()}", notes=payload.version_notes.strip(), snapshot_json=json.dumps(task_snapshot(task_row), ensure_ascii=False), final_prompt=str(task_row.get("final_prompt") or ""), created_at=now_ms())
        repo.update_task(task_id, current_version_id=int(version["id"]), updated_at=now_ms())
        return serialize_task_detail(get_task(task_id))

    @router.put("/api/ocrebuild/tasks/{task_id}/shot-plan/keyframes")
    async def update_shot_keyframes(task_id: int, payload: OCRebuildShotKeyframesPayload) -> dict[str, Any]:
        task_row = get_task(task_id)
        plan_path = Path(str(task_row["workspace_dir"])) / "rebuild_shot_plan.json"
        if not plan_path.exists() or not plan_path.is_file():
            raise HTTPException(status_code=404, detail="rebuild_shot_plan.json not found")
        try:
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Unable to read rebuild_shot_plan.json: {exc}") from exc
        shots = plan.get("shots") if isinstance(plan.get("shots"), list) else []
        target_shot = next((shot for shot in shots if isinstance(shot, dict) and str(shot.get("shot_id") or "") == payload.shot_id), None)
        if not target_shot:
            raise HTTPException(status_code=404, detail="Shot not found")
        reference = target_shot.get("reference") if isinstance(target_shot.get("reference"), dict) else {}
        existing_keyframes = reference.get("keyframes") if isinstance(reference.get("keyframes"), list) else []
        original_keyframes = original_keyframes_for_reference(reference, existing_keyframes)
        allowed_by_path = {**keyframe_map(original_keyframes), **keyframe_map(existing_keyframes)}
        next_keyframes: list[dict[str, Any]] = []
        seen_paths: set[str] = set()
        for frame in payload.keyframes:
            frame_path = str(frame.get("path") or "").strip() if isinstance(frame, dict) else ""
            if not frame_path or frame_path in seen_paths:
                continue
            if frame_path not in allowed_by_path:
                raise HTTPException(status_code=400, detail=f"Keyframe is not bound to this shot: {frame_path}")
            frame_payload = dict(frame)
            next_frame = {**allowed_by_path[frame_path], **frame_payload}
            if "scene_mark" not in frame_payload:
                next_frame.pop("scene_mark", None)
            next_keyframes.append(next_frame)
            seen_paths.add(frame_path)
        if payload.scene_marks is not None:
            next_keyframes, next_scene_marks, scene_id_map = canonicalize_shot_scene_marks(payload.shot_id, next_keyframes, [dict(item) for item in payload.scene_marks if isinstance(item, dict)])
            reference["scene_marks"] = next_scene_marks
            reference["scene_mark_summary"] = canonical_scene_mark_summary(reference)
        reference["keyframes"] = sort_keyframes(next_keyframes)
        update_keyframe_audit(reference, original_keyframes, reference["keyframes"])
        target_shot["reference"] = reference
        tmp_path = plan_path.with_suffix(plan_path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(plan_path)
        add_event(int(task_row["session_id"]), "ocrebuild.shot_keyframes.updated", {"task_id": task_id, "shot_id": payload.shot_id, "keyframe_count": len(next_keyframes)})
        return {"ok": True, "task_id": task_id, "shot_id": payload.shot_id, "keyframes": reference["keyframes"], "original_keyframes": reference.get("original_keyframes") or [], "deleted_keyframes": reference.get("deleted_keyframes") or [], "scene_marks": reference.get("scene_marks") or [], "scene_mark_summary": reference.get("scene_mark_summary") or {}}

    @router.put("/api/ocrebuild/tasks/{task_id}/shot-plan/scene-marks")
    async def update_shot_scene_marks(task_id: int, payload: OCRebuildShotSceneMarksPayload) -> dict[str, Any]:
        task_row = get_task(task_id)
        plan_path = Path(str(task_row["workspace_dir"])) / "rebuild_shot_plan.json"
        if not plan_path.exists() or not plan_path.is_file():
            raise HTTPException(status_code=404, detail="rebuild_shot_plan.json not found")
        try:
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Unable to read rebuild_shot_plan.json: {exc}") from exc
        shots = plan.get("shots") if isinstance(plan.get("shots"), list) else []
        target_shot = next((shot for shot in shots if isinstance(shot, dict) and str(shot.get("shot_id") or "") == payload.shot_id), None)
        if not target_shot:
            raise HTTPException(status_code=404, detail="Shot not found")
        reference = target_shot.get("reference") if isinstance(target_shot.get("reference"), dict) else {}
        existing_keyframes = reference.get("keyframes") if isinstance(reference.get("keyframes"), list) else []
        original_keyframes = original_keyframes_for_reference(reference, existing_keyframes)
        allowed_paths = set(keyframe_map(original_keyframes)) | set(keyframe_map(existing_keyframes))
        next_keyframes: list[dict[str, Any]] = []
        seen_paths: set[str] = set()
        for frame in payload.keyframes:
            frame_path = str(frame.get("path") or "").strip() if isinstance(frame, dict) else ""
            if not frame_path or frame_path in seen_paths:
                continue
            if frame_path not in allowed_paths:
                raise HTTPException(status_code=400, detail=f"Keyframe is not bound to this shot: {frame_path}")
            next_keyframes.append(dict(frame))
            seen_paths.add(frame_path)
        next_keyframes, next_scene_marks, scene_id_map = canonicalize_shot_scene_marks(payload.shot_id, next_keyframes, [dict(item) for item in payload.scene_marks if isinstance(item, dict)])
        reference["keyframes"] = sort_keyframes(next_keyframes)
        update_keyframe_audit(reference, original_keyframes, reference["keyframes"])
        reference["scene_marks"] = next_scene_marks
        reference["scene_mark_summary"] = canonical_scene_mark_summary(reference)
        target_shot["reference"] = reference
        tmp_path = plan_path.with_suffix(plan_path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(plan_path)
        add_event(int(task_row["session_id"]), "ocrebuild.scene_marks.updated", {"task_id": task_id, "shot_id": payload.shot_id, "keyframe_count": len(next_keyframes), "scene_mark_count": len(reference["scene_marks"])})
        return {"ok": True, "task_id": task_id, "shot_id": payload.shot_id, "keyframes": reference["keyframes"], "original_keyframes": reference.get("original_keyframes") or [], "deleted_keyframes": reference.get("deleted_keyframes") or [], "scene_marks": reference["scene_marks"], "scene_mark_summary": reference["scene_mark_summary"]}

    @router.post("/api/ocrebuild/tasks/{task_id}/asset-image/generate")
    async def generate_asset_image(task_id: int, payload: OCRebuildAssetImageGeneratePayload) -> dict[str, Any]:
        return run_asset_image_generation(task_id, payload)

    @router.post("/api/ocrebuild/tasks/{task_id}/asset-image/prompt/refine")
    async def refine_asset_image_prompt(task_id: int, payload: OCRebuildAssetPromptRefinePayload) -> dict[str, Any]:
        return await refine_asset_prompt(task_id, payload)

    @router.post("/api/ocrebuild/tasks/{task_id}/asset-video/prompt/refine")
    async def refine_asset_video_prompt_route(task_id: int, payload: OCRebuildAssetVideoPromptRefinePayload) -> dict[str, Any]:
        return await refine_asset_video_prompt(task_id, payload)

    @router.get("/api/ocrebuild/tasks/{task_id}/asset-video/workflows/{workflow_id}")
    async def get_asset_video_workflow(task_id: int, workflow_id: str) -> dict[str, Any]:
        task_row = get_task(task_id)
        workspace = workspace_path(task_row)
        normalized = normalize_workflow_id(workflow_id)
        path = video_workflow_json_path(workspace, normalized)
        workflow = read_asset_video_workflow(workspace, normalized)
        return {"ok": True, "exists": path.exists(), "workflow_id": normalized, "workflow": workflow}

    @router.put("/api/ocrebuild/tasks/{task_id}/asset-video/workflows/{workflow_id}")
    async def save_asset_video_workflow(task_id: int, workflow_id: str, payload: OCRebuildAssetWorkflowSavePayload) -> dict[str, Any]:
        task_row = get_task(task_id)
        workspace = workspace_path(task_row)
        normalized = normalize_workflow_id(workflow_id)
        current = read_asset_video_workflow(workspace, normalized)
        next_workflow = {**current, **(payload.workflow or {}), "workflow_id": normalized, "task_id": task_id, "session_id": int(task_row["session_id"])}
        saved = write_asset_video_workflow(workspace, normalized, next_workflow)
        return {"ok": True, "workflow_id": normalized, "workflow": saved}

    @router.post("/api/ocrebuild/tasks/{task_id}/asset-video/compare/events")
    async def compare_asset_videos(task_id: int, payload: OCRebuildAssetVideoComparePayload) -> StreamingResponse:
        task_row = get_task(task_id)
        session_id = int(task_row["session_id"])
        workspace = workspace_path(task_row)
        workflow_id = normalize_workflow_id(payload.workflow_id)
        if not payload.prompts:
            raise HTTPException(status_code=400, detail="At least one provider prompt is required")
        _asset_tasks, target, _asset_tasks_path = asset_video_target(task_row, payload.shot_id, payload.scene_mark_id.strip())
        input_payload = target.get("input") if isinstance(target.get("input"), dict) else {}
        input_mode_value = payload.input_mode.strip() or "first_frame"
        first_image = "" if input_mode_value == "text" else payload.first_image.strip() or str(input_payload.get("first_image") or "")
        last_image = "" if input_mode_value != "first_last" else payload.last_image.strip() or str(input_payload.get("last_image") or "")
        duration = payload.duration if payload.duration is not None else (target.get("params") or {}).get("duration") if isinstance(target.get("params"), dict) else None
        started_payload = {"workflow_id": workflow_id, "task_id": task_id, "session_id": session_id, "shot_id": payload.shot_id, "scene_mark_id": payload.scene_mark_id.strip(), "input_mode": input_mode_value, "duration": duration, "temporary": True, "writes_asset_json": False}
        prompt_records = [{"provider": item.provider.strip(), "model": item.model.strip(), "prompt": item.prompt, "current_prompt": item.prompt, "user_instruction": item.user_instruction, "duration": item.duration if item.duration is not None else duration, "confirmed_at": now_ms()} for item in payload.prompts]
        write_asset_video_workflow(workspace, workflow_id, {**read_asset_video_workflow(workspace, workflow_id), **started_payload, "phase": "generating", "prompts": prompt_records, "candidates": [], "started_at": now_ms(), "first_image": first_image, "last_image": last_image})
        add_event(session_id, "ocrebuild.asset_video.workflow.started", started_payload)

        async def event_generator() -> Any:
            started = time.time()
            yield f"data: {json.dumps({'type': 'workflow_started', **started_payload}, ensure_ascii=True)}\n\n"
            running: dict[asyncio.Task, dict[str, Any]] = {}
            heartbeat_counts: dict[str, int] = {}
            for index, prompt_item in enumerate(payload.prompts, start=1):
                provider = prompt_item.provider.strip()
                model_id = prompt_item.model.strip()
                candidate_id = f"{provider}_video_{index}"
                output_rel = video_workflow_output_rel(workflow_id, provider, index)
                request_payload = {**started_payload, "api_call_id": f"{workflow_id}-{candidate_id}-{now_ms()}", "candidate_id": candidate_id, "provider": provider, "model": model_id, "output": output_rel, "output_path": str(workspace / output_rel), "prompt_preview": prompt_item.prompt[:1000], "prompt_length": len(prompt_item.prompt), "duration": prompt_item.duration if prompt_item.duration is not None else duration}
                add_event(session_id, "ocrebuild.asset_video.requested", request_payload)
                yield f"data: {json.dumps({'type': 'requested', **request_payload}, ensure_ascii=True)}\n\n"
                try:
                    config = load_video_config(provider, model_id)
                except HTTPException as exc:
                    failed = {**request_payload, "status_code": exc.status_code, "detail": exc.detail, "elapsed_seconds": round(time.time() - started, 1)}
                    upsert_video_workflow_candidate(workspace, workflow_id, {**failed, "status": "failed"})
                    add_event(session_id, "ocrebuild.asset_video.failed", failed)
                    yield f"data: {json.dumps({'type': 'failed', **failed}, ensure_ascii=True)}\n\n"
                    continue
                async_task = asyncio.create_task(asyncio.to_thread(run_video_candidate, task_id, request_payload, prompt_item.prompt, config, output_rel, first_image, last_image, started_payload["input_mode"], request_payload.get("duration")))
                running[async_task] = {**request_payload, "model": model_id or config["model"], "provider": provider, "local_preview": False}
                heartbeat_counts[request_payload["api_call_id"]] = 0
            pending = set(running.keys())
            while pending:
                done, pending = await asyncio.wait(pending, timeout=2)
                for done_task in done:
                    request_payload = running[done_task]
                    try:
                        render_result = await done_task
                    except HTTPException as exc:
                        failed = {**request_payload, "status_code": exc.status_code, "detail": exc.detail, "elapsed_seconds": round(time.time() - started, 1)}
                        upsert_video_workflow_candidate(workspace, workflow_id, {**failed, "status": "failed"})
                        add_event(session_id, "ocrebuild.asset_video.failed", failed)
                        yield f"data: {json.dumps({'type': 'failed', **failed}, ensure_ascii=True)}\n\n"
                    except Exception as exc:
                        failed = {**request_payload, "detail": str(exc), "elapsed_seconds": round(time.time() - started, 1)}
                        upsert_video_workflow_candidate(workspace, workflow_id, {**failed, "status": "failed"})
                        add_event(session_id, "ocrebuild.asset_video.failed", failed)
                        yield f"data: {json.dumps({'type': 'failed', **failed}, ensure_ascii=True)}\n\n"
                    else:
                        result = {**request_payload, **render_result, "ok": True, "status": "completed", "elapsed_seconds": render_result.get("elapsed_seconds") or round(time.time() - started, 1)}
                        upsert_video_workflow_candidate(workspace, workflow_id, result)
                        add_event(session_id, "ocrebuild.asset_video.generated", result)
                        yield f"data: {json.dumps({'type': 'completed', **result}, ensure_ascii=True)}\n\n"
                if pending:
                    for pending_task in list(pending):
                        request_payload = running[pending_task]
                        api_call_id = str(request_payload.get("api_call_id") or "")
                        heartbeat_counts[api_call_id] = heartbeat_counts.get(api_call_id, 0) + 1
                        heartbeat_payload = {**request_payload, "heartbeat": heartbeat_counts[api_call_id], "elapsed_seconds": round(time.time() - started, 1)}
                        add_event(session_id, "ocrebuild.asset_video.heartbeat", heartbeat_payload)
                        yield f"data: {json.dumps({'type': 'heartbeat', **heartbeat_payload}, ensure_ascii=True)}\n\n"
            completed_payload = {**started_payload, "elapsed_seconds": round(time.time() - started, 1)}
            update_asset_video_workflow(workspace, workflow_id, {"phase": "select", "completed_at": now_ms(), "elapsed_seconds": completed_payload["elapsed_seconds"]})
            add_event(session_id, "ocrebuild.asset_video.workflow.completed", completed_payload)
            yield f"data: {json.dumps({'type': 'round_completed', **completed_payload}, ensure_ascii=True)}\n\n"

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    @router.post("/api/ocrebuild/tasks/{task_id}/asset-video/compare/finalize")
    async def finalize_compare_asset_video(task_id: int, payload: OCRebuildAssetVideoCompareFinalizePayload) -> dict[str, Any]:
        task_row = get_task(task_id)
        session_id = int(task_row["session_id"])
        workspace = workspace_path(task_row)
        selected = workspace / payload.selected_output.strip()
        try:
            selected_resolved = selected.resolve()
            if not str(selected_resolved).startswith(str(workspace.resolve())):
                raise HTTPException(status_code=400, detail="Selected output must stay inside the task workspace")
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"Selected output not found: {payload.selected_output}")
        if not selected.exists() or not selected.is_file():
            raise HTTPException(status_code=404, detail=f"Selected output not found: {payload.selected_output}")
        asset_tasks, target, asset_tasks_path = asset_video_target(task_row, payload.shot_id, payload.scene_mark_id.strip())
        output_rel = str(target.get("output") or "").strip()
        output_path = workspace / output_rel
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(selected, output_path)
        target.update({"status": "completed", "generated_at": now_ms(), "provider": payload.provider.strip(), "model": payload.model.strip(), "duration": payload.duration, "compare_selected_output": payload.selected_output.strip()})
        tmp_path = asset_tasks_path.with_suffix(asset_tasks_path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(asset_tasks, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(asset_tasks_path)
        result = {"ok": True, "task_id": task_id, "session_id": session_id, "shot_id": payload.shot_id, "scene_mark_id": payload.scene_mark_id.strip(), "selected_output": payload.selected_output.strip(), "output": output_rel, "output_path": str(output_path), "provider": payload.provider.strip(), "model": payload.model.strip(), "duration": payload.duration}
        workflow_id = normalize_workflow_id(payload.workflow_id) if payload.workflow_id.strip() else ""
        if workflow_id:
            update_asset_video_workflow(workspace, workflow_id, {"phase": "finalized", "final": {"selected_output": payload.selected_output.strip(), "asset_output": output_rel, "provider": payload.provider.strip(), "model": payload.model.strip(), "duration": payload.duration, "finalized_at": now_ms()}})
        add_event(session_id, "ocrebuild.asset_video.workflow.finalized", result)
        return result

    @router.get("/api/ocrebuild/tasks/{task_id}/shot-video/r2v/workflows/{workflow_id}")
    async def get_shot_multi_reference_workflow(task_id: int, workflow_id: str) -> dict[str, Any]:
        task_row = get_task(task_id)
        workspace = workspace_path(task_row)
        normalized = normalize_workflow_id(workflow_id)
        path = video_workflow_json_path(workspace, normalized)
        workflow = read_asset_video_workflow(workspace, normalized)
        return {"ok": True, "exists": path.exists(), "workflow_id": normalized, "workflow": workflow}

    @router.post("/api/ocrebuild/tasks/{task_id}/shot-video/r2v/prompt/refine")
    async def refine_shot_multi_reference_prompt(task_id: int, payload: OCRebuildShotMultiReferencePromptPayload) -> dict[str, Any]:
        task_row = get_task(task_id)
        session_id = int(task_row["session_id"])
        session_row = safe_session(session_id)
        workflow_id = normalize_workflow_id(payload.workflow_id or f"{payload.shot_id}_multi_r2v_video")
        request_id = payload.request_id.strip() or f"shot_r2v_refine_{now_ms()}_{uuid.uuid4().hex[:8]}"
        docs = load_local_video_prompt_docs(payload.provider.strip())
        requested = {"request_id": request_id, "workflow_id": workflow_id, "task_id": task_id, "session_id": session_id, "shot_id": payload.shot_id, "provider": payload.provider.strip(), "video_model": payload.video_model.strip(), "input_mode": "multi_reference", "duration": payload.duration, "reference_image_count": len(payload.reference_images), "reference_video_count": len(payload.reference_videos), "temporary": True, "writes_asset_json": False, "writes_database": False}
        add_event(session_id, "ocrebuild.shot_video.prompt_refine.requested", {**requested, "user_instruction": payload.user_instruction[:500], "current_prompt_preview": payload.current_prompt[:1000]})
        run_provider = str(task_row.get("run_model_provider") or "").strip()
        run_model_id = str(task_row.get("run_model_id") or "").strip()
        model, _prompt_models = resolve_model(session_row, run_provider, run_model_id, "Run")
        add_event(session_id, "ocrebuild.shot_video.prompt_refine.run_model.started", {**requested, "run_model_provider": model["providerID"], "run_model_id": model["modelID"]})
        started_at = now_ms()
        scene_plan_text = json.dumps(payload.scene_plan, ensure_ascii=False, indent=2)[:12000]
        user_content = f"""
Request ID:
{request_id}

Task: Shot-level multi-image reference-to-video prompt refinement.
Shot ID: {payload.shot_id}
Target provider: {payload.provider.strip()}
Target video model: {payload.video_model.strip()}
Input mode: multi_reference
Requested duration seconds: {payload.duration if payload.duration is not None else 'unknown'}
Reference image count: {len(payload.reference_images)}
Reference video count: {len(payload.reference_videos)}

Provider documentation URL:
{docs['docs_url']}

Local provider documentation summary:
{docs['docs_text'] or '(No local summary text available.)'}

Scene plan with image order, SRT/dialogue, and timing notes:
{scene_plan_text or '[]'}

Current prompt:
{payload.current_prompt.strip() or '(empty)'}

User simple instruction:
{payload.user_instruction.strip()}

Important requirements:
- Generate one complete shot-level video from the ordered reference images, not per-scene clips.
- The visual rhythm should follow the dialogue/SRT beats.
- Exact voiceover and SRT synchronization will be handled later by TTS/SRT compositing; do not over-force native model speech.
- Ensure every reference image appears in sequence, and keep the final reference visible through the ending.
- Return a provider-ready video prompt only, without markdown headings.

Return strict JSON only with this exact request_id and final prompt:
{{"request_id":"{request_id}","prompt":"..."}}
""".strip()
        client = opencode_client_for(session_row)
        client.prompt_async(str(session_row["opencode_session_id"]), user_content, model=model, system=VIDEO_PROMPT_REFINE_SYSTEM_PROMPT)
        deadline = time.time() + 240
        assistant_text = None
        while time.time() < deadline:
            assistant_text = last_completed_assistant(client.messages(str(session_row["opencode_session_id"]), limit=120), started_at)
            if assistant_text:
                break
            await asyncio.sleep(1)
        if not assistant_text:
            failed = {**requested, "run_model_provider": model["providerID"], "run_model_id": model["modelID"], "detail": "Run Model timed out before returning shot multi-reference prompt"}
            add_event(session_id, "ocrebuild.shot_video.prompt_refine.failed", failed)
            raise HTTPException(status_code=400, detail=failed["detail"])
        try:
            parsed = json.loads(assistant_text.strip())
        except Exception as exc:
            failed = {**requested, "run_model_provider": model["providerID"], "run_model_id": model["modelID"], "detail": "Run Model returned non-JSON shot multi-reference prompt output"}
            add_event(session_id, "ocrebuild.shot_video.prompt_refine.failed", failed)
            raise HTTPException(status_code=400, detail=failed["detail"]) from exc
        prompt_text = str((parsed or {}).get("prompt") or "").strip() if isinstance(parsed, dict) else ""
        returned_request_id = str((parsed or {}).get("request_id") or "").strip() if isinstance(parsed, dict) else ""
        if returned_request_id != request_id or not prompt_text or re.search(r"(?m)^#{1,6}\s+", prompt_text):
            failed = {**requested, "run_model_provider": model["providerID"], "run_model_id": model["modelID"], "detail": "Run Model shot multi-reference prompt failed request_id or content validation"}
            add_event(session_id, "ocrebuild.shot_video.prompt_refine.failed", failed)
            raise HTTPException(status_code=400, detail=failed["detail"])
        result = {**requested, "ok": True, "run_model_provider": model["providerID"], "run_model_id": model["modelID"], "prompt": prompt_text}
        add_event(session_id, "ocrebuild.shot_video.prompt_refine.completed", {**result, "prompt_preview": prompt_text[:1000], "prompt_length": len(prompt_text)})
        return result

    @router.post("/api/ocrebuild/tasks/{task_id}/shot-video/r2v/events")
    async def generate_shot_multi_reference_video(task_id: int, payload: OCRebuildShotMultiReferencePayload) -> StreamingResponse:
        task_row = get_task(task_id)
        session_id = int(task_row["session_id"])
        workspace = workspace_path(task_row)
        workflow_id = normalize_workflow_id(payload.workflow_id or f"{payload.shot_id}_multi_r2v_video")
        provider = payload.provider.strip()
        model_id = payload.model.strip()
        supported = {("wan", "wan2.7-r2v"), ("wan", "happyhorse-1.0-r2v"), ("xai", "grok-imagine-video"), ("gemini", "veo-3.1-generate-preview"), ("gemini", "veo-3.1-fast-generate-preview")}
        if (provider, model_id) not in supported:
            raise HTTPException(status_code=400, detail=f"Unsupported multi-reference model: {provider}/{model_id}")
        if payload.reference_videos and provider != "wan":
            raise HTTPException(status_code=400, detail=f"Reference video input is currently supported only for Wan R2V, not {provider}/{model_id}")
        if not payload.reference_images and not payload.reference_videos:
            raise HTTPException(status_code=400, detail="At least one reference image or reference video is required")
        variant_count = max(1, min(int(payload.variant_count or 1), 3))
        effective_references = payload.reference_images[:3] if provider == "gemini" else payload.reference_images
        effective_reference_videos = payload.reference_videos[:1] if provider == "wan" else []
        duration = 8 if provider == "gemini" else payload.duration
        started_payload = {"workflow_id": workflow_id, "task_id": task_id, "session_id": session_id, "shot_id": payload.shot_id, "provider": provider, "model": model_id, "input_mode": "multi_reference", "duration": duration, "variant_count": variant_count, "reference_images": effective_references, "reference_image_count": len(effective_references), "reference_videos": effective_reference_videos, "reference_video_count": len(effective_reference_videos), "scene_plan": payload.scene_plan, "temporary": True, "writes_asset_json": False}
        write_asset_video_workflow(workspace, workflow_id, {**read_asset_video_workflow(workspace, workflow_id), **started_payload, "phase": "generating", "prompt": payload.prompt, "candidates": [], "started_at": now_ms()})
        add_event(session_id, "ocrebuild.shot_video.workflow.started", started_payload)

        async def event_generator() -> Any:
            started = time.time()
            yield f"data: {json.dumps({'type': 'workflow_started', **started_payload}, ensure_ascii=True)}\n\n"
            running: dict[asyncio.Task, dict[str, Any]] = {}
            heartbeat_counts: dict[str, int] = {}
            try:
                config = load_video_config(provider, model_id)
            except HTTPException as exc:
                failed = {**started_payload, "status_code": exc.status_code, "detail": exc.detail, "elapsed_seconds": round(time.time() - started, 1)}
                add_event(session_id, "ocrebuild.shot_video.failed", failed)
                yield f"data: {json.dumps({'type': 'failed', **failed}, ensure_ascii=True)}\n\n"
                return
            for index in range(1, variant_count + 1):
                candidate_id = f"{provider}_shot_r2v_{index}"
                output_rel = video_workflow_output_rel(workflow_id, provider, index)
                request_payload = {**started_payload, "api_call_id": f"{workflow_id}-{candidate_id}-{now_ms()}", "candidate_id": candidate_id, "output": output_rel, "output_path": str(workspace / output_rel), "prompt_preview": payload.prompt[:1000], "prompt_length": len(payload.prompt), "duration": duration}
                add_event(session_id, "ocrebuild.shot_video.requested", request_payload)
                yield f"data: {json.dumps({'type': 'requested', **request_payload}, ensure_ascii=True)}\n\n"
                async_task = asyncio.create_task(asyncio.to_thread(run_shot_multi_reference_video_candidate, task_id, request_payload, payload.prompt, config, output_rel, effective_references, effective_reference_videos, duration))
                running[async_task] = request_payload
                heartbeat_counts[request_payload["api_call_id"]] = 0
            pending = set(running.keys())
            while pending:
                done, pending = await asyncio.wait(pending, timeout=2)
                for done_task in done:
                    request_payload = running[done_task]
                    try:
                        render_result = await done_task
                    except HTTPException as exc:
                        failed = {**request_payload, "status_code": exc.status_code, "detail": exc.detail, "elapsed_seconds": round(time.time() - started, 1), "status": "failed"}
                        upsert_video_workflow_candidate(workspace, workflow_id, failed)
                        add_event(session_id, "ocrebuild.shot_video.failed", failed)
                        yield f"data: {json.dumps({'type': 'failed', **failed}, ensure_ascii=True)}\n\n"
                    except Exception as exc:
                        failed = {**request_payload, "detail": str(exc), "elapsed_seconds": round(time.time() - started, 1), "status": "failed"}
                        upsert_video_workflow_candidate(workspace, workflow_id, failed)
                        add_event(session_id, "ocrebuild.shot_video.failed", failed)
                        yield f"data: {json.dumps({'type': 'failed', **failed}, ensure_ascii=True)}\n\n"
                    else:
                        result = {**request_payload, **render_result, "ok": True, "status": "completed", "elapsed_seconds": render_result.get("elapsed_seconds") or round(time.time() - started, 1)}
                        upsert_video_workflow_candidate(workspace, workflow_id, result)
                        add_event(session_id, "ocrebuild.shot_video.generated", result)
                        yield f"data: {json.dumps({'type': 'completed', **result}, ensure_ascii=True)}\n\n"
                if pending:
                    for pending_task in list(pending):
                        request_payload = running[pending_task]
                        api_call_id = str(request_payload.get("api_call_id") or "")
                        heartbeat_counts[api_call_id] = heartbeat_counts.get(api_call_id, 0) + 1
                        heartbeat_payload = {**request_payload, "heartbeat": heartbeat_counts[api_call_id], "elapsed_seconds": round(time.time() - started, 1)}
                        add_event(session_id, "ocrebuild.shot_video.heartbeat", heartbeat_payload)
                        yield f"data: {json.dumps({'type': 'heartbeat', **heartbeat_payload}, ensure_ascii=True)}\n\n"
            completed_payload = {**started_payload, "elapsed_seconds": round(time.time() - started, 1)}
            update_asset_video_workflow(workspace, workflow_id, {"phase": "select", "completed_at": now_ms(), "elapsed_seconds": completed_payload["elapsed_seconds"]})
            add_event(session_id, "ocrebuild.shot_video.workflow.completed", completed_payload)
            yield f"data: {json.dumps({'type': 'round_completed', **completed_payload}, ensure_ascii=True)}\n\n"

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    @router.post("/api/ocrebuild/tasks/{task_id}/shot-video/r2v/finalize")
    async def finalize_shot_multi_reference_video(task_id: int, payload: OCRebuildShotMultiReferenceFinalizePayload) -> dict[str, Any]:
        task_row = get_task(task_id)
        session_id = int(task_row["session_id"])
        workspace = workspace_path(task_row)
        selected = workspace / payload.selected_output.strip()
        try:
            selected_resolved = selected.resolve()
            if not str(selected_resolved).startswith(str(workspace.resolve())):
                raise HTTPException(status_code=400, detail="Selected output must stay inside the task workspace")
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"Selected output not found: {payload.selected_output}")
        if not selected.exists() or not selected.is_file():
            raise HTTPException(status_code=404, detail=f"Selected output not found: {payload.selected_output}")
        workflow_id = normalize_workflow_id(payload.workflow_id or f"{payload.shot_id}_multi_r2v_video")
        final = {"selected_output": payload.selected_output.strip(), "provider": payload.provider.strip(), "model": payload.model.strip(), "duration": payload.duration, "finalized_at": now_ms()}
        update_asset_video_workflow(workspace, workflow_id, {"phase": "finalized", "final": final})
        result = {"ok": True, "task_id": task_id, "session_id": session_id, "shot_id": payload.shot_id, "workflow_id": workflow_id, **final}
        add_event(session_id, "ocrebuild.shot_video.workflow.finalized", result)
        return result

    @router.get("/api/ocrebuild/tasks/{task_id}/asset-tts/workflows/{workflow_id}")
    async def get_asset_tts_workflow(task_id: int, workflow_id: str) -> dict[str, Any]:
        task_row = get_task(task_id)
        workspace = workspace_path(task_row)
        normalized = normalize_workflow_id(workflow_id)
        path = tts_workflow_json_path(workspace, normalized)
        workflow = read_asset_tts_workflow(workspace, normalized)
        return {"ok": True, "exists": path.exists(), "workflow_id": normalized, "workflow": workflow}

    @router.get("/api/ocrebuild/tasks/{task_id}/shot-tts/workflows/{workflow_id}")
    async def get_shot_tts_workflow(task_id: int, workflow_id: str) -> dict[str, Any]:
        task_row = get_task(task_id)
        workspace = workspace_path(task_row)
        normalized = normalize_workflow_id(workflow_id)
        path = tts_workflow_json_path(workspace, normalized)
        workflow = read_asset_tts_workflow(workspace, normalized)
        return {"ok": True, "exists": path.exists(), "workflow_id": normalized, "workflow": workflow}

    @router.post("/api/ocrebuild/tasks/{task_id}/shot-tts/reference-audio")
    async def upload_shot_tts_reference_audio(task_id: int, workflow_id: str = Form(default="shot_plan_tts_voice"), file: UploadFile = File(...)) -> dict[str, Any]:
        task_row = get_task(task_id)
        session_id = int(task_row["session_id"])
        workspace = workspace_path(task_row)
        normalized = normalize_workflow_id(workflow_id or "shot_plan_tts_voice")
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Uploaded reference audio is empty")
        target_dir = tts_workflow_dir(workspace, normalized) / "manual_reference"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / safe_audio_upload_name(file.filename or "", "reference_audio", content, file.content_type or "")
        target.write_bytes(content)
        reference_rel = str(target.resolve().relative_to(workspace.resolve()))
        current = read_asset_tts_workflow(workspace, normalized)
        shot_state = current.get("shot") if isinstance(current.get("shot"), dict) else {}
        shot_state.update({
            "shot_id": "shot_plan",
            "scope": "shot_plan",
            "voice_manual_reference_audio": str(target.resolve()),
            "voice_manual_reference_audio_rel": reference_rel,
        })
        saved = write_asset_tts_workflow(workspace, normalized, {**current, "workflow_id": normalized, "task_id": task_id, "session_id": session_id, "scope": "shot_plan", "shot": shot_state})
        add_event(session_id, "ocrebuild.shot_tts.reference_audio.uploaded", {"workflow_id": normalized, "task_id": task_id, "session_id": session_id, "path": reference_rel, "filename": file.filename or target.name, "bytes": len(content)})
        return {"ok": True, "workflow_id": normalized, "path": reference_rel, "abs_path": str(target.resolve()), "filename": file.filename or target.name, "bytes": len(content), "workflow": saved}

    @router.post("/api/ocrebuild/tasks/{task_id}/shot-tts/recommend")
    async def recommend_shot_tts_voice(task_id: int, payload: OCRebuildShotTTSRecommendPayload) -> dict[str, Any]:
        task_row = get_task(task_id)
        session_id = int(task_row["session_id"])
        workspace = workspace_path(task_row)
        scope = payload.scope.strip() or "shot_plan"
        workflow_id = normalize_workflow_id(payload.workflow_id or ("shot_plan_tts_voice" if scope == "shot_plan" else f"{payload.shot_id}_tts_lab"))
        plan_path = shot_plan_path(workspace)
        plan = read_workspace_json(plan_path, "rebuild_shot_plan.json")
        if scope == "shot_plan" or not payload.shot_id.strip():
            reference_path = source_reference_audio(task_row, workspace)
            reference_text = payload.reference_text.strip() or " ".join(plain_srt_text(str(((shot.get("reference") or {}).get("srt_text") or ""))) for shot in (plan.get("shots") or []) if isinstance(shot, dict)).strip()
            shot_id_value = "shot_plan"
        else:
            shot = find_shot_in_plan(plan, payload.shot_id)
            reference_path = cut_reference_audio_for_shot(task_row, shot, workflow_id)
            reference_text = payload.reference_text.strip() or plain_srt_text(str((shot.get("reference") or {}).get("srt_text") or ""))
            shot_id_value = payload.shot_id
        match_payload = TTSVoiceMatchPayload(
            reference_audio_path=str(reference_path),
            reference_text=reference_text,
            target_gender=payload.target_gender.strip(),
            sample_text="欢迎使用 OpenCrew。下面这段声音将用于测试语气、节奏、清晰度和商业短视频旁白的自然程度。",
            language=payload.language.strip() or "zh",
            top_k=max(1, min(int(payload.top_k or 5), 10)),
            regenerate=payload.regenerate,
        )
        result = tts_match_endpoint()(match_payload)
        recommendations = result.get("recommendations") if isinstance(result.get("recommendations"), list) else []
        current = read_asset_tts_workflow(workspace, workflow_id)
        shot_state = current.get("shot") if isinstance(current.get("shot"), dict) else {}
        shot_state.update({
            "shot_id": shot_id_value,
            "scope": scope,
            "voice_reference_audio": str(reference_path),
            "voice_reference_text": reference_text,
            "voice_recommendations": recommendations,
            "voice_recommendation_result": {
                "candidate_count": result.get("candidate_count"),
                "skipped_count": result.get("skipped_count"),
                "error_count": result.get("error_count"),
                "reference_profile": result.get("reference_profile"),
                "reference_features": result.get("reference_features"),
            },
        })
        saved = write_asset_tts_workflow(workspace, workflow_id, {**current, "workflow_id": workflow_id, "task_id": task_id, "session_id": session_id, "phase": "voice_recommended", "scope": scope, "shot": shot_state})
        event_payload = {"workflow_id": workflow_id, "task_id": task_id, "session_id": session_id, "scope": scope, "shot_id": shot_id_value, "recommendation_count": len(recommendations), "reference_audio": str(reference_path)}
        add_event(session_id, "ocrebuild.shot_tts.voice_recommend.completed", event_payload)
        return {"ok": True, **event_payload, "reference_text": reference_text, "recommendations": recommendations, "match_result": result, "workflow": saved}

    @router.post("/api/ocrebuild/tasks/{task_id}/shot-tts/builder")
    async def build_shot_tts_voice(task_id: int, payload: OCRebuildShotTTSBuilderPayload) -> dict[str, Any]:
        if not payload.confirm:
            raise HTTPException(status_code=400, detail="Builder run requires explicit confirmation")
        task_row = get_task(task_id)
        session_id = int(task_row["session_id"])
        workspace = workspace_path(task_row)
        workflow_id = normalize_workflow_id(payload.workflow_id or "shot_plan_tts_voice")
        builder_value = payload.builder.strip().lower()
        if builder_value in {"g", "gemini", "builder-g", "builder_g"}:
            builder_key = "g"
            builder_label = "Builder-G"
            script_name = "03_02_ShotPlan_GTTSVoiceBuilder.py"
            manifest_rel_path = "tts/gtts_voice_builder/gtts_voice_builder_manifest.json"
            provider_label = "Gemini"
            candidate_keys = ("top_candidates",)
        elif builder_value in {"q", "qwen", "builder-q", "builder_q"}:
            builder_key = "q"
            builder_label = "Builder-Q"
            script_name = "03_02_ShotPlan_QTTSVoiceBuilder.py"
            manifest_rel_path = "tts/qtts_voice_builder/qtts_voice_builder_manifest.json"
            provider_label = "Qwen"
            candidate_keys = ("final_candidates", "selection.top_candidates")
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported TTS builder: {payload.builder}")

        script_path = Path(__file__).resolve().parents[3] / "ToolLibrary" / "Rebuild_V1" / script_name
        if not script_path.exists():
            raise HTTPException(status_code=500, detail=f"TTS builder script not found: {script_path}")

        cmd = [
            sys.executable or "python3",
            str(script_path),
            "--workspace",
            str(workspace),
            "--task-id",
            str(task_id),
            "--session-id",
            str(session_id),
            "--print-json",
        ]
        manual_reference_audio = str(payload.reference_audio or "").strip()
        manual_reference_path = Path(manual_reference_audio).expanduser() if manual_reference_audio else None
        if manual_reference_path and not manual_reference_path.is_absolute():
            manual_reference_path = workspace / manual_reference_path
        if manual_reference_path:
            if not manual_reference_path.exists() or not manual_reference_path.is_file():
                raise HTTPException(status_code=404, detail=f"Manual reference audio was not found: {manual_reference_path}")
            manual_reference_path = manual_reference_path.resolve()
            cmd.extend(["--reference-audio", str(manual_reference_path)])
        manual_reference_rel = ""
        if manual_reference_path:
            try:
                manual_reference_rel = str(manual_reference_path.relative_to(workspace.resolve()))
            except Exception:
                manual_reference_rel = str(manual_reference_path)
        reference_start = max(0.0, float(payload.reference_start or 0.0))
        reference_duration = float(payload.reference_duration or 0.0)
        if reference_duration > 0:
            cmd.extend(["--reference-start", f"{reference_start:.3f}", "--reference-duration", f"{reference_duration:.3f}"])
        if payload.force:
            cmd.append("--force")
        if not payload.generate_html:
            cmd.append("--no-generate-html")

        started_at = now_ms()
        event_payload = {"workflow_id": workflow_id, "task_id": task_id, "session_id": session_id, "builder": builder_key, "builder_label": builder_label, "force": payload.force}
        current = read_asset_tts_workflow(workspace, workflow_id)
        shot_state = current.get("shot") if isinstance(current.get("shot"), dict) else {}
        shot_state.update({
            "shot_id": "shot_plan",
            "scope": "shot_plan",
            "voice_builder": builder_key,
            "voice_manual_reference_audio": str(manual_reference_path) if manual_reference_path else shot_state.get("voice_manual_reference_audio", ""),
            "voice_manual_reference_audio_rel": manual_reference_rel if manual_reference_path else shot_state.get("voice_manual_reference_audio_rel", ""),
            "voice_reference_start": reference_start,
            "voice_reference_duration": reference_duration if reference_duration > 0 else shot_state.get("voice_reference_duration", 16),
            "voice_builder_manifest": manifest_rel_path,
            "voice_builder_html": "",
            "voice_recommendations": [],
            "voice_reference_text": "",
            "voice_reference_srt": "",
            "voice_recommendation_result": {
                "builder": builder_key,
                "builder_label": builder_label,
                "status": "running",
                "manifest": manifest_rel_path,
                "started_at": started_at,
            },
        })
        write_asset_tts_workflow(
            workspace,
            workflow_id,
            {
                **current,
                "workflow_id": workflow_id,
                "task_id": task_id,
                "session_id": session_id,
                "phase": f"voice_builder_{builder_key}_running",
                "scope": "shot_plan",
                "shot": shot_state,
                "started_at": started_at,
            },
        )
        add_event(session_id, "ocrebuild.shot_tts.voice_builder.started", event_payload)

        def save_builder_failure(detail: str) -> None:
            latest = read_asset_tts_workflow(workspace, workflow_id)
            latest_shot = latest.get("shot") if isinstance(latest.get("shot"), dict) else {}
            latest_shot.update({
                "shot_id": "shot_plan",
                "scope": "shot_plan",
                "voice_builder": builder_key,
                "voice_builder_manifest": manifest_rel_path,
                "voice_builder_html": "",
                "voice_recommendation_result": {
                    "builder": builder_key,
                    "builder_label": builder_label,
                    "status": "failed",
                    "manifest": manifest_rel_path,
                    "error": detail[-2000:],
                    "elapsed_seconds": round((now_ms() - started_at) / 1000, 3),
                },
            })
            write_asset_tts_workflow(
                workspace,
                workflow_id,
                {
                    **latest,
                    "workflow_id": workflow_id,
                    "task_id": task_id,
                    "session_id": session_id,
                    "phase": "voice_builder_failed",
                    "scope": "shot_plan",
                    "shot": latest_shot,
                    "error": detail[-2000:],
                    "completed_at": now_ms(),
                },
            )

        def run_builder() -> subprocess.CompletedProcess[str]:
            return subprocess.run(cmd, cwd=str(script_path.parent), check=False, capture_output=True, text=True, timeout=1800)

        result = await asyncio.to_thread(run_builder)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "TTS builder failed").strip()[-4000:]
            save_builder_failure(detail)
            add_event(session_id, "ocrebuild.shot_tts.voice_builder.failed", {**event_payload, "detail": detail})
            raise HTTPException(status_code=500, detail=detail)

        manifest_path = workspace / manifest_rel_path
        manifest = read_json_file(manifest_path)
        if not manifest:
            detail = f"TTS builder completed but manifest is missing: {manifest_rel_path}"
            save_builder_failure(detail)
            raise HTTPException(status_code=500, detail=detail)

        def manifest_rel_value(value: Any) -> str:
            text_value = str(value or "").strip()
            if not text_value:
                return ""
            path_value = Path(text_value)
            if not path_value.is_absolute():
                return text_value
            try:
                return str(path_value.resolve().relative_to(workspace.resolve()))
            except Exception:
                return text_value

        def manifest_abs_value(value: Any) -> str:
            text_value = str(value or "").strip()
            if not text_value:
                return ""
            path_value = Path(text_value).expanduser()
            if not path_value.is_absolute():
                path_value = workspace / path_value
            try:
                return str(path_value.resolve())
            except Exception:
                return str(path_value)

        def candidates_from_manifest() -> list[dict[str, Any]]:
            for key in candidate_keys:
                if key == "selection.top_candidates":
                    values = ((manifest.get("selection") or {}).get("top_candidates") if isinstance(manifest.get("selection"), dict) else None)
                else:
                    values = manifest.get(key)
                if isinstance(values, list) and values:
                    return [item for item in values if isinstance(item, dict)]
            return []

        raw_candidates = candidates_from_manifest()
        recommendations: list[dict[str, Any]] = []
        for candidate in raw_candidates:
            voice = str(candidate.get("voice") or candidate.get("voice_id") or "").strip()
            audio = manifest_rel_value(candidate.get("audio") or candidate.get("fit_audio") or candidate.get("raw_audio"))
            fit_audio = manifest_rel_value(candidate.get("fit_audio") or candidate.get("audio"))
            raw_audio = manifest_rel_value(candidate.get("raw_audio"))
            recommendation = {
                "provider": candidate.get("provider") or manifest.get("provider") or builder_key,
                "provider_label": provider_label,
                "model": candidate.get("model") or manifest.get("model") or manifest.get("base_model") or "",
                "voice_id": voice,
                "voice": voice,
                "label": voice,
                "prompt": candidate.get("prompt") or "",
                "prompt_template": candidate.get("prompt_template") or "",
                "instructions": candidate.get("instructions") or "",
                "style": candidate.get("note") or candidate.get("stage") or "",
                "stage": candidate.get("stage") or "",
                "candidate_id": candidate.get("candidate_id") or "",
                "parent_id": candidate.get("parent_id") or "",
                "round": candidate.get("round") or candidate.get("round_index"),
                "score": candidate.get("score"),
                "score_parts": candidate.get("score_parts") if isinstance(candidate.get("score_parts"), dict) else {},
                "features": candidate.get("features") if isinstance(candidate.get("features"), dict) else {},
                "audio": audio,
                "fit_audio": fit_audio,
                "raw_audio": raw_audio,
                "output": audio,
                "src": "",
                "fit_meta": candidate.get("fit_meta") if isinstance(candidate.get("fit_meta"), dict) else {},
                "raw_duration": candidate.get("raw_duration"),
                "fit_duration": candidate.get("fit_duration"),
                "builder": builder_key,
                "builder_label": builder_label,
            }
            recommendations.append(recommendation)

        reference_clip = manifest.get("reference_clip") if isinstance(manifest.get("reference_clip"), dict) else {}
        reference_fit_audio_source = reference_clip.get("clip_audio") or manifest.get("reference_audio")
        reference_source_audio_source = manifest.get("reference_audio") or reference_clip.get("source_audio")
        reference_features = manifest.get("reference_features") if isinstance(manifest.get("reference_features"), dict) else {}
        reference_profile = reference_features.get("profile") if isinstance(reference_features.get("profile"), dict) else {}
        if not reference_profile:
            inferred_gender = str(manifest.get("inferred_reference_gender") or manifest.get("target_gender") or "").strip()
            if not inferred_gender:
                try:
                    f0_median = float(reference_features.get("f0_median") or 0)
                    inferred_gender = "female" if f0_median >= 165 else "male" if f0_median > 0 else ""
                except Exception:
                    inferred_gender = ""
            reference_profile = {"gender": inferred_gender or "unknown", "gender_source": "builder_reference_audio", "language": "zh"}
        match_result = {
            "builder": builder_key,
            "builder_label": builder_label,
            "candidate_count": len(recommendations),
            "reference_audio": manifest_rel_value(manifest.get("reference_audio")),
            "reference_audio_abs": manifest_abs_value(reference_source_audio_source),
            "reference_fit_audio": manifest_abs_value(reference_fit_audio_source),
            "reference_fit_audio_rel": manifest_rel_value(reference_fit_audio_source),
            "reference_clip": reference_clip,
            "reference_features": reference_features,
            "reference_profile": reference_profile,
            "sample_text": manifest.get("sample_text") or "",
            "sample_srt": manifest.get("sample_srt") or "",
            "sample_text_sources": manifest.get("sample_text_sources") if isinstance(manifest.get("sample_text_sources"), list) else [],
            "html_review": manifest_rel_value(manifest.get("html_review")),
            "manifest": manifest_rel_path,
            "elapsed_seconds": round((now_ms() - started_at) / 1000, 3),
        }
        current = read_asset_tts_workflow(workspace, workflow_id)
        shot_state = current.get("shot") if isinstance(current.get("shot"), dict) else {}
        shot_state.update({
            "shot_id": "shot_plan",
            "scope": "shot_plan",
            "voice_recommendations": recommendations,
            "voice_recommendation_result": match_result,
            "voice_reference_audio": match_result["reference_audio"],
            "voice_reference_fit_audio": match_result["reference_fit_audio"],
            "voice_reference_fit_audio_rel": match_result["reference_fit_audio_rel"],
            "voice_manual_reference_audio": str(manual_reference_path) if manual_reference_path else shot_state.get("voice_manual_reference_audio", ""),
            "voice_manual_reference_audio_rel": manual_reference_rel if manual_reference_path else shot_state.get("voice_manual_reference_audio_rel", ""),
            "voice_reference_start": reference_start,
            "voice_reference_duration": reference_duration if reference_duration > 0 else shot_state.get("voice_reference_duration", 16),
            "voice_reference_text": manifest.get("sample_text") or "",
            "voice_reference_srt": manifest.get("sample_srt") or "",
            "voice_builder": builder_key,
            "voice_builder_manifest": manifest_rel_path,
            "voice_builder_html": match_result["html_review"],
        })
        saved = write_asset_tts_workflow(workspace, workflow_id, {**current, "workflow_id": workflow_id, "task_id": task_id, "session_id": session_id, "phase": "voice_builder_completed", "scope": "shot_plan", "shot": shot_state})
        completed = {**event_payload, "recommendation_count": len(recommendations), "manifest": manifest_rel_path, "html_review": match_result["html_review"], "elapsed_seconds": match_result["elapsed_seconds"]}
        add_event(session_id, "ocrebuild.shot_tts.voice_builder.completed", completed)
        return {"ok": True, **completed, "reference_audio": match_result["reference_audio"], "reference_text": manifest.get("sample_text") or "", "recommendations": recommendations, "match_result": match_result, "workflow": saved}

    @router.put("/api/ocrebuild/tasks/{task_id}/shot-tts/voice-selection")
    async def save_shot_tts_voice_selection(task_id: int, payload: OCRebuildShotTTSVoiceSelectionPayload) -> dict[str, Any]:
        task_row = get_task(task_id)
        session_id = int(task_row["session_id"])
        workspace = workspace_path(task_row)
        scope = payload.scope.strip() or "shot_plan"
        workflow_id = normalize_workflow_id(payload.workflow_id or ("shot_plan_tts_voice" if scope == "shot_plan" else f"{payload.shot_id}_tts_lab"))
        plan_path = shot_plan_path(workspace)
        plan = read_workspace_json(plan_path, "rebuild_shot_plan.json")
        selection = {
            "provider": payload.provider.strip(),
            "model": payload.model.strip(),
            "voice_id": payload.voice_id.strip(),
            "voice": payload.voice_id.strip(),
            "label": payload.label.strip(),
            "prompt": payload.prompt.strip(),
            "score": payload.score,
            "selection_source": "oc_rebuild_voice_recommendation_ui",
            "scope": scope,
            "selected_at": now_ms(),
        }
        if payload.tempo is not None and payload.tempo > 0:
            selection["tempo"] = payload.tempo
        if payload.fit_meta:
            selection["fit_meta"] = payload.fit_meta
        for key, value in {
            "prompt_template": payload.prompt_template,
            "instructions": payload.instructions,
            "stage": payload.stage,
            "candidate_id": payload.candidate_id,
            "audio": payload.audio,
            "fit_audio": payload.fit_audio,
            "raw_audio": payload.raw_audio,
        }.items():
            cleaned = str(value or "").strip()
            if cleaned:
                selection[key] = cleaned
        if payload.top_candidates:
            selection["top_candidates"] = payload.top_candidates
        if scope == "shot_plan" or not payload.shot_id.strip():
            for shot in plan.get("shots") or []:
                if isinstance(shot, dict):
                    shot["tts_selection"] = {**selection, "selection_source": "plan_level_default"}
            plan["plan_a_tts_selection"] = {**selection, "shot_id": "shot_plan", "shot_count": len(plan.get("shots") or []), "written_at": now_ms()}
            shot_id_value = "shot_plan"
        else:
            shot = find_shot_in_plan(plan, payload.shot_id)
            shot["tts_selection"] = selection
            plan["plan_a_tts_selection"] = {**selection, "shot_id": payload.shot_id, "written_at": now_ms()}
            shot_id_value = payload.shot_id
        plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        current = read_asset_tts_workflow(workspace, workflow_id)
        shot_state = current.get("shot") if isinstance(current.get("shot"), dict) else {}
        shot_state.update({"shot_id": shot_id_value, "scope": scope, "voice_selection": selection})
        saved = write_asset_tts_workflow(workspace, workflow_id, {**current, "workflow_id": workflow_id, "task_id": task_id, "session_id": session_id, "phase": "voice_selected", "scope": scope, "shot": shot_state})
        result = {"ok": True, "task_id": task_id, "session_id": session_id, "workflow_id": workflow_id, "scope": scope, "shot_id": shot_id_value, "selection": selection, "workflow": saved}
        add_event(session_id, "ocrebuild.shot_tts.voice_selection.saved", result)
        return result

    @router.post("/api/ocrebuild/tasks/{task_id}/shot-tts/prompt/refine")
    async def refine_shot_tts_prompt(task_id: int, payload: OCRebuildShotTTSPromptRefinePayload) -> dict[str, Any]:
        task_row = get_task(task_id)
        session_id = int(task_row["session_id"])
        session_row = safe_session(session_id)
        workflow_id = normalize_workflow_id(payload.workflow_id or f"{payload.shot_id}_tts_locked")
        request_id = payload.request_id.strip() or f"shot_tts_refine_{now_ms()}_{uuid.uuid4().hex[:8]}"
        requested = {"request_id": request_id, "workflow_id": workflow_id, "task_id": task_id, "session_id": session_id, "shot_id": payload.shot_id, "provider": payload.provider.strip(), "tts_model": payload.tts_model.strip(), "voice_id": payload.voice_id.strip(), "target_duration": payload.target_duration, "temporary": True, "writes_asset_json": False, "writes_database": False}
        add_event(session_id, "ocrebuild.shot_tts.prompt_refine.requested", {**requested, "user_instruction": payload.user_instruction[:500], "current_prompt_preview": payload.current_prompt[:1000]})
        run_provider = str(task_row.get("run_model_provider") or "").strip()
        run_model_id = str(task_row.get("run_model_id") or "").strip()
        model, _prompt_models = resolve_model(session_row, run_provider, run_model_id, "Run")
        add_event(session_id, "ocrebuild.shot_tts.prompt_refine.run_model.started", {**requested, "run_model_provider": model["providerID"], "run_model_id": model["modelID"]})
        started_at = now_ms()
        scene_plan_text = json.dumps(payload.scene_plan, ensure_ascii=False, indent=2)[:12000]
        user_content = f"""
Request ID:
{request_id}

Task: Shot-level continuous TTS instruct prompt refinement for OpenClip Rebuild.
Shot ID: {payload.shot_id}
Target provider: {payload.provider.strip()}
Target TTS model: {payload.tts_model.strip()}
Voice ID: {payload.voice_id.strip()}
Target total duration seconds: {payload.target_duration if payload.target_duration is not None else 'unknown'}

Scene plan with ordered SRT lines:
{scene_plan_text or '[]'}

Complete SRT text to speak exactly:
{payload.srt_text.strip()}

Current final prompt:
{payload.current_prompt.strip() or '(empty)'}

User simple instruction:
{payload.user_instruction.strip()}

Important requirements:
- Return a concise provider-ready voice/instruct prompt only.
- Do not rewrite, add, omit, or translate the spoken SRT text.
- The TTS should be generated as one continuous shot-level audio, not separate sentence clips.
- Mention the target total duration when it is provided.
- Prefer natural Chinese short-video narration: clear pronunciation, controlled rhythm, conversational tone, natural continuity across all lines.
- If the provider/model has limited instruction support, still return a useful style prompt.
- Return strict JSON only with this exact request_id and final prompt:
{{"request_id":"{request_id}","prompt":"..."}}
""".strip()
        client = opencode_client_for(session_row)
        client.prompt_async(str(session_row["opencode_session_id"]), user_content, model=model, system="You refine shot-level continuous TTS voice style prompts. Return strict JSON only.")
        deadline = time.time() + 180
        assistant_text = None
        while time.time() < deadline:
            assistant_text = last_completed_assistant(client.messages(str(session_row["opencode_session_id"]), limit=120), started_at)
            if assistant_text:
                break
            await asyncio.sleep(1)
        if not assistant_text:
            failed = {**requested, "run_model_provider": model["providerID"], "run_model_id": model["modelID"], "detail": "Run Model timed out before returning shot TTS prompt"}
            add_event(session_id, "ocrebuild.shot_tts.prompt_refine.failed", failed)
            raise HTTPException(status_code=400, detail=failed["detail"])
        try:
            parsed = json.loads(assistant_text.strip())
        except Exception as exc:
            failed = {**requested, "run_model_provider": model["providerID"], "run_model_id": model["modelID"], "detail": "Run Model returned non-JSON shot TTS prompt output"}
            add_event(session_id, "ocrebuild.shot_tts.prompt_refine.failed", failed)
            raise HTTPException(status_code=400, detail=failed["detail"]) from exc
        prompt_text = str((parsed or {}).get("prompt") or "").strip() if isinstance(parsed, dict) else ""
        returned_request_id = str((parsed or {}).get("request_id") or "").strip() if isinstance(parsed, dict) else ""
        if returned_request_id != request_id or not prompt_text or re.search(r"(?m)^#{1,6}\s+", prompt_text):
            failed = {**requested, "run_model_provider": model["providerID"], "run_model_id": model["modelID"], "detail": "Run Model shot TTS prompt failed request_id or content validation"}
            add_event(session_id, "ocrebuild.shot_tts.prompt_refine.failed", failed)
            raise HTTPException(status_code=400, detail=failed["detail"])
        result = {**requested, "ok": True, "run_model_provider": model["providerID"], "run_model_id": model["modelID"], "prompt": prompt_text}
        add_event(session_id, "ocrebuild.shot_tts.prompt_refine.completed", {**result, "prompt_preview": prompt_text[:1000], "prompt_length": len(prompt_text)})
        return result

    @router.post("/api/ocrebuild/tasks/{task_id}/asset-tts/prompt/refine")
    async def refine_asset_tts_prompt(task_id: int, payload: OCRebuildAssetTTSPromptRefinePayload) -> dict[str, Any]:
        task_row = get_task(task_id)
        session_id = int(task_row["session_id"])
        session_row = safe_session(session_id)
        workflow_id = normalize_workflow_id(payload.workflow_id or f"{payload.shot_id}_tts_lab")
        request_id = payload.request_id.strip() or f"tts_refine_{now_ms()}_{uuid.uuid4().hex[:8]}"
        requested = {"request_id": request_id, "workflow_id": workflow_id, "task_id": task_id, "session_id": session_id, "shot_id": payload.shot_id, "scene_mark_id": payload.scene_mark_id, "provider": payload.provider.strip(), "tts_model": payload.tts_model.strip(), "voice_id": payload.voice_id.strip(), "temporary": True, "writes_asset_json": False, "writes_database": False}
        add_event(session_id, "ocrebuild.asset_tts.prompt_refine.requested", {**requested, "user_instruction": payload.user_instruction[:500], "current_prompt_preview": payload.current_prompt[:1000]})
        run_provider = str(task_row.get("run_model_provider") or "").strip()
        run_model_id = str(task_row.get("run_model_id") or "").strip()
        model, _prompt_models = resolve_model(session_row, run_provider, run_model_id, "Run")
        add_event(session_id, "ocrebuild.asset_tts.prompt_refine.run_model.started", {**requested, "run_model_provider": model["providerID"], "run_model_id": model["modelID"]})
        started_at = now_ms()
        user_content = f"""
Request ID:
{request_id}

Task: Scene-level TTS instruct prompt refinement for OpenClip Rebuild.
Shot ID: {payload.shot_id}
Scene Mark ID: {payload.scene_mark_id}
Target provider: {payload.provider.strip()}
Target TTS model: {payload.tts_model.strip()}
Voice ID: {payload.voice_id.strip()}

Scene SRT text to speak exactly:
{payload.srt_text.strip()}

Current final prompt:
{payload.current_prompt.strip() or '(empty)'}

User simple instruction:
{payload.user_instruction.strip()}

Important requirements:
- Return a concise provider-ready voice/instruct prompt only.
- Do not rewrite the spoken SRT text.
- Prefer natural Chinese short-video narration: clear pronunciation, controlled rhythm, conversational tone.
- If the provider/model has limited instruction support, still return a useful style prompt.
- Return strict JSON only with this exact request_id and final prompt:
{{"request_id":"{request_id}","prompt":"..."}}
""".strip()
        client = opencode_client_for(session_row)
        client.prompt_async(str(session_row["opencode_session_id"]), user_content, model=model, system="You refine TTS voice style prompts. Return strict JSON only.")
        deadline = time.time() + 180
        assistant_text = None
        while time.time() < deadline:
            assistant_text = last_completed_assistant(client.messages(str(session_row["opencode_session_id"]), limit=120), started_at)
            if assistant_text:
                break
            await asyncio.sleep(1)
        if not assistant_text:
            failed = {**requested, "run_model_provider": model["providerID"], "run_model_id": model["modelID"], "detail": "Run Model timed out before returning TTS prompt"}
            add_event(session_id, "ocrebuild.asset_tts.prompt_refine.failed", failed)
            raise HTTPException(status_code=400, detail=failed["detail"])
        try:
            parsed = json.loads(assistant_text.strip())
        except Exception as exc:
            failed = {**requested, "run_model_provider": model["providerID"], "run_model_id": model["modelID"], "detail": "Run Model returned non-JSON TTS prompt output"}
            add_event(session_id, "ocrebuild.asset_tts.prompt_refine.failed", failed)
            raise HTTPException(status_code=400, detail=failed["detail"]) from exc
        prompt_text = str((parsed or {}).get("prompt") or "").strip() if isinstance(parsed, dict) else ""
        returned_request_id = str((parsed or {}).get("request_id") or "").strip() if isinstance(parsed, dict) else ""
        if returned_request_id != request_id or not prompt_text or re.search(r"(?m)^#{1,6}\s+", prompt_text):
            failed = {**requested, "run_model_provider": model["providerID"], "run_model_id": model["modelID"], "detail": "Run Model TTS prompt failed request_id or content validation"}
            add_event(session_id, "ocrebuild.asset_tts.prompt_refine.failed", failed)
            raise HTTPException(status_code=400, detail=failed["detail"])
        result = {**requested, "ok": True, "run_model_provider": model["providerID"], "run_model_id": model["modelID"], "prompt": prompt_text}
        add_event(session_id, "ocrebuild.asset_tts.prompt_refine.completed", {**result, "prompt_preview": prompt_text[:1000], "prompt_length": len(prompt_text)})
        return result

    def run_tts_candidate(task_id: int, request_payload: dict[str, Any], prompt_item: dict[str, Any], output_rel: str) -> dict[str, Any]:
        task_row = get_task(task_id)
        workspace = workspace_path(task_row)
        session_id = int(task_row["session_id"])
        provider = str(prompt_item.get("provider") or "").strip()
        model_id = str(prompt_item.get("model") or "").strip()
        voice_id = str(prompt_item.get("voice_id") or "").strip()
        prompt = str(prompt_item.get("prompt") or "").strip()
        text_value = str(prompt_item.get("text") or "").strip()
        target_duration = float(prompt_item.get("target_duration") or 0) or None
        tempo = float(prompt_item.get("tempo") or 0) or None
        fit_to_tempo = bool(tempo and tempo > 0)
        fit_to_duration = bool(prompt_item.get("fit_to_duration")) and bool(target_duration and target_duration > 0) and not fit_to_tempo
        if not text_value:
            raise HTTPException(status_code=400, detail="TTS text is required")
        if (provider, model_id) not in {("google", "gemini-3.1-flash-tts-preview"), ("google", "gemini-2.5-flash-preview-tts"), ("google", "gemini-2.5-pro-preview-tts"), ("xai", "xai-tts"), ("qwen", "qwen3-tts-flash"), ("qwen", "qwen3-tts-flash-2025-11-27"), ("qwen", "qwen3-tts-instruct-flash"), ("qwen", "qwen3-tts-instruct-flash-2026-01-26")}:
            raise HTTPException(status_code=400, detail=f"Unsupported Rebuild TTS model: {provider}/{model_id}")
        config = load_tts_config(provider, model_id)
        output_path = workspace / output_rel
        raw_output_rel = output_rel
        raw_output_path = output_path
        if fit_to_duration or fit_to_tempo:
            output_rel_path = Path(output_rel)
            raw_output_rel = str(output_rel_path.with_name(f"{output_rel_path.stem}_raw{output_rel_path.suffix}"))
            raw_output_path = workspace / raw_output_rel
        started = time.time()
        call_detail = {**request_payload, "provider": provider, "model": model_id, "voice_id": voice_id, "method": "POST", "input_mode": "tts", "text_preview": text_value[:500], "prompt_preview": prompt[:1000], "prompt_length": len(prompt), "workspace_dir": str(workspace), "output": output_rel, "output_path": str(output_path), "raw_output": raw_output_rel if (fit_to_duration or fit_to_tempo) else "", "raw_output_path": str(raw_output_path) if (fit_to_duration or fit_to_tempo) else "", "target_duration": target_duration, "fit_to_duration": fit_to_duration, "tempo": tempo, "fit_to_tempo": fit_to_tempo, "temporary": True, "writes_asset_json": False}
        add_event(session_id, "ocrebuild.asset_tts.provider_call.started", call_detail)
        audio_url = generate_tts_audio(config, text_value, voice_id, prompt, raw_output_path)
        stretch: dict[str, Any] = {"raw_duration": audio_duration_seconds(raw_output_path), "locked_duration": audio_duration_seconds(raw_output_path), "speed_factor": 1.0, "tempo": tempo or 1.0, "stretched": False, "warnings": []}
        if fit_to_tempo:
            stretch = tempo_stretch_audio(raw_output_path, output_path, tempo)
            audio_url = ""
        elif fit_to_duration:
            stretch = time_stretch_audio(raw_output_path, output_path, target_duration)
            audio_url = ""
        duration_seconds = audio_duration_seconds(output_path)
        elapsed_seconds = round(time.time() - started, 3)
        result = {**call_detail, "ok": True, "output": output_rel, "output_path": str(output_path), "duration_seconds": duration_seconds, "raw_duration": stretch.get("raw_duration"), "fit_duration": duration_seconds, "speed_factor": stretch.get("speed_factor"), "tempo": stretch.get("tempo") or tempo, "stretched": stretch.get("stretched"), "fit_warnings": stretch.get("warnings") or [], "elapsed_seconds": elapsed_seconds, "audio_url": audio_url, "local_preview": False}
        add_event(session_id, "ocrebuild.asset_tts.generated", result)
        return result

    @router.post("/api/ocrebuild/tasks/{task_id}/shot-tts/events")
    async def compare_shot_tts(task_id: int, payload: OCRebuildShotTTSComparePayload) -> StreamingResponse:
        task_row = get_task(task_id)
        session_id = int(task_row["session_id"])
        workspace = workspace_path(task_row)
        workflow_id = normalize_workflow_id(payload.workflow_id or f"{payload.shot_id}_tts_locked")
        prompts = [item.model_dump() for item in payload.prompts if item.provider.strip() and item.model.strip() and item.voice_id.strip() and item.text.strip()]
        if not prompts:
            raise HTTPException(status_code=400, detail="At least one shot TTS prompt is required")
        started_payload = {"workflow_id": workflow_id, "task_id": task_id, "session_id": session_id, "shot_id": payload.shot_id, "input_mode": "shot_tts", "target_duration": payload.target_duration, "scene_plan": payload.scene_plan, "temporary": True, "writes_asset_json": False}
        current = read_asset_tts_workflow(workspace, workflow_id)
        shot_state = current.get("shot") if isinstance(current.get("shot"), dict) else {}
        shot_state.update({"shot_id": payload.shot_id, "phase": "generating", "target_duration": payload.target_duration, "scene_plan": payload.scene_plan, "prompts": prompts, "candidates": []})
        write_asset_tts_workflow(workspace, workflow_id, {**current, **started_payload, "shot": shot_state, "phase": "shot_generating", "started_at": now_ms()})
        add_event(session_id, "ocrebuild.shot_tts.workflow.started", {**started_payload, "candidate_count": len(prompts)})

        async def event_generator() -> Any:
            started = time.time()
            yield f"data: {json.dumps({'type': 'workflow_started', **started_payload}, ensure_ascii=True)}\n\n"
            running: dict[asyncio.Task, dict[str, Any]] = {}
            heartbeat_counts: dict[str, int] = {}
            for index, prompt_item in enumerate(prompts, start=1):
                provider = str(prompt_item.get("provider") or "provider")
                candidate_id = f"{provider}_shot_tts_{index}"
                output_rel = shot_tts_output_rel(workflow_id, provider, index)
                request_payload = {**started_payload, "api_call_id": f"{workflow_id}-{candidate_id}-{now_ms()}", "candidate_id": candidate_id, "provider": provider, "model": prompt_item.get("model"), "voice_id": prompt_item.get("voice_id"), "output": output_rel, "output_path": str(workspace / output_rel), "prompt_preview": str(prompt_item.get("prompt") or "")[:1000], "prompt_length": len(str(prompt_item.get("prompt") or ""))}
                add_event(session_id, "ocrebuild.shot_tts.requested", request_payload)
                yield f"data: {json.dumps({'type': 'requested', **request_payload}, ensure_ascii=True)}\n\n"
                async_task = asyncio.create_task(asyncio.to_thread(run_tts_candidate, task_id, request_payload, prompt_item, output_rel))
                running[async_task] = request_payload
                heartbeat_counts[request_payload["api_call_id"]] = 0
            pending = set(running.keys())
            while pending:
                done, pending = await asyncio.wait(pending, timeout=2)
                for done_task in done:
                    request_payload = running[done_task]
                    try:
                        render_result = await done_task
                    except HTTPException as exc:
                        failed = {**request_payload, "status_code": exc.status_code, "detail": exc.detail, "elapsed_seconds": round(time.time() - started, 1), "status": "failed"}
                        upsert_shot_tts_candidate(workspace, workflow_id, failed)
                        add_event(session_id, "ocrebuild.shot_tts.failed", failed)
                        yield f"data: {json.dumps({'type': 'failed', **failed}, ensure_ascii=True)}\n\n"
                    except Exception as exc:
                        failed = {**request_payload, "detail": str(exc), "elapsed_seconds": round(time.time() - started, 1), "status": "failed"}
                        upsert_shot_tts_candidate(workspace, workflow_id, failed)
                        add_event(session_id, "ocrebuild.shot_tts.failed", failed)
                        yield f"data: {json.dumps({'type': 'failed', **failed}, ensure_ascii=True)}\n\n"
                    else:
                        result = {**request_payload, **render_result, "ok": True, "status": "completed", "elapsed_seconds": render_result.get("elapsed_seconds") or round(time.time() - started, 1)}
                        upsert_shot_tts_candidate(workspace, workflow_id, result)
                        add_event(session_id, "ocrebuild.shot_tts.generated", result)
                        yield f"data: {json.dumps({'type': 'completed', **result}, ensure_ascii=True)}\n\n"
                if pending:
                    for pending_task in list(pending):
                        request_payload = running[pending_task]
                        api_call_id = str(request_payload.get("api_call_id") or "")
                        heartbeat_counts[api_call_id] = heartbeat_counts.get(api_call_id, 0) + 1
                        heartbeat_payload = {**request_payload, "heartbeat": heartbeat_counts[api_call_id], "elapsed_seconds": round(time.time() - started, 1)}
                        add_event(session_id, "ocrebuild.shot_tts.heartbeat", heartbeat_payload)
                        yield f"data: {json.dumps({'type': 'heartbeat', **heartbeat_payload}, ensure_ascii=True)}\n\n"
            completed_payload = {**started_payload, "elapsed_seconds": round(time.time() - started, 1)}
            current = read_asset_tts_workflow(workspace, workflow_id)
            shot_state = current.get("shot") if isinstance(current.get("shot"), dict) else {}
            shot_state["phase"] = "select"
            update_asset_tts_workflow(workspace, workflow_id, {"phase": "shot_select", "shot": shot_state, "completed_at": now_ms(), "elapsed_seconds": completed_payload["elapsed_seconds"]})
            add_event(session_id, "ocrebuild.shot_tts.workflow.completed", completed_payload)
            yield f"data: {json.dumps({'type': 'round_completed', **completed_payload}, ensure_ascii=True)}\n\n"

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    @router.post("/api/ocrebuild/tasks/{task_id}/shot-tts/finalize")
    async def finalize_shot_tts(task_id: int, payload: OCRebuildShotTTSFinalizePayload) -> dict[str, Any]:
        task_row = get_task(task_id)
        session_id = int(task_row["session_id"])
        workspace = workspace_path(task_row)
        selected = workspace / payload.selected_output.strip()
        try:
            selected_resolved = selected.resolve()
            if not str(selected_resolved).startswith(str(workspace.resolve())):
                raise HTTPException(status_code=400, detail="Selected output must stay inside the task workspace")
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"Selected output not found: {payload.selected_output}")
        if not selected.exists() or not selected.is_file():
            raise HTTPException(status_code=404, detail=f"Selected output not found: {payload.selected_output}")
        workflow_id = normalize_workflow_id(payload.workflow_id or f"{payload.shot_id}_tts_locked")
        target_duration = float(payload.target_duration or payload.duration or 0) or None
        locked_rel = shot_tts_locked_output_rel(workflow_id, payload.selected_output.strip())
        stretch = time_stretch_audio(selected, workspace / locked_rel, target_duration)
        locked_duration = stretch["locked_duration"] or audio_duration_seconds(workspace / locked_rel)
        timeline_rel = shot_tts_timeline_output_rel(workflow_id)
        srt_rel = shot_tts_srt_output_rel(workflow_id)
        timeline = build_locked_timeline(payload.shot_id, payload.scene_plan, locked_rel, locked_duration)
        write_timeline_files(workspace, timeline_rel, srt_rel, timeline)
        final = {
            "selected_output": payload.selected_output.strip(),
            "locked_audio": locked_rel,
            "timeline": timeline_rel,
            "srt": srt_rel,
            "provider": payload.provider.strip(),
            "model": payload.model.strip(),
            "voice_id": payload.voice_id.strip(),
            "target_duration": target_duration,
            "duration": locked_duration,
            "raw_duration": stretch["raw_duration"],
            "speed_factor": stretch["speed_factor"],
            "stretched": stretch["stretched"],
            "alignment_method": timeline.get("alignment_method"),
            "warnings": stretch.get("warnings") or [],
            "finalized_at": now_ms(),
        }
        current = read_asset_tts_workflow(workspace, workflow_id)
        shot_state = current.get("shot") if isinstance(current.get("shot"), dict) else {}
        shot_state.update({"phase": "finalized", "final": final, "locked_timeline": timeline, "scene_plan": payload.scene_plan})
        update_asset_tts_workflow(workspace, workflow_id, {"phase": "shot_finalized", "shot": shot_state})
        result = {"ok": True, "task_id": task_id, "session_id": session_id, "shot_id": payload.shot_id, "workflow_id": workflow_id, **final, "locked_timeline": timeline}
        add_event(session_id, "ocrebuild.shot_tts.workflow.finalized", result)
        return result

    @router.post("/api/ocrebuild/tasks/{task_id}/asset-tts/compare/events")
    async def compare_asset_tts(task_id: int, payload: OCRebuildAssetTTSComparePayload) -> StreamingResponse:
        task_row = get_task(task_id)
        session_id = int(task_row["session_id"])
        workspace = workspace_path(task_row)
        workflow_id = normalize_workflow_id(payload.workflow_id or f"{payload.shot_id}_tts_lab")
        prompts = [item.model_dump() for item in payload.prompts if item.prompt.strip() or item.provider.strip()]
        if not prompts:
            raise HTTPException(status_code=400, detail="At least one TTS prompt is required")
        started_payload = {"workflow_id": workflow_id, "task_id": task_id, "session_id": session_id, "shot_id": payload.shot_id, "scene_mark_id": payload.scene_mark_id, "input_mode": "tts", "srt_text": payload.srt_text, "temporary": True, "writes_asset_json": False}
        cached_result = locked_tts_cache_hit(workspace, payload, prompts[0], workflow_id) if len(prompts) == 1 else None
        if cached_result:
            cached_payload = {**started_payload, **cached_result, "task_id": task_id, "session_id": session_id}
            add_event(session_id, "ocrebuild.asset_tts.locked_cache.hit", cached_payload)

            async def cached_event_generator() -> Any:
                yield f"data: {json.dumps({'type': 'workflow_started', **started_payload, 'cache_hit': True}, ensure_ascii=True)}\n\n"
                yield f"data: {json.dumps({'type': 'completed', **cached_payload}, ensure_ascii=True)}\n\n"
                yield f"data: {json.dumps({'type': 'round_completed', **started_payload, 'elapsed_seconds': 0, 'cache_hit': True}, ensure_ascii=True)}\n\n"

            return StreamingResponse(cached_event_generator(), media_type="text/event-stream")
        current = read_asset_tts_workflow(workspace, workflow_id)
        scenes = current.get("scenes") if isinstance(current.get("scenes"), dict) else {}
        scene = scenes.get(payload.scene_mark_id) if isinstance(scenes.get(payload.scene_mark_id), dict) else {}
        scene.update({"scene_mark_id": payload.scene_mark_id, "srt_text": payload.srt_text, "phase": "generating", "prompts": prompts, "candidates": []})
        scenes[payload.scene_mark_id] = scene
        write_asset_tts_workflow(workspace, workflow_id, {**current, **started_payload, "scenes": scenes, "phase": "generating", "started_at": now_ms()})
        add_event(session_id, "ocrebuild.asset_tts.workflow.started", {**started_payload, "candidate_count": len(prompts)})

        async def event_generator() -> Any:
            started = time.time()
            yield f"data: {json.dumps({'type': 'workflow_started', **started_payload}, ensure_ascii=True)}\n\n"
            running: dict[asyncio.Task, dict[str, Any]] = {}
            heartbeat_counts: dict[str, int] = {}
            for index, prompt_item in enumerate(prompts, start=1):
                provider = str(prompt_item.get("provider") or "provider")
                candidate_id = f"{provider}_tts_{index}"
                output_rel = tts_workflow_output_rel(workflow_id, payload.scene_mark_id, provider, index)
                if payload.use_locked_cache and len(prompts) == 1 and payload.locked_output.strip():
                    output_rel = safe_workspace_rel(workspace, payload.locked_output)[0]
                request_payload = {**started_payload, "api_call_id": f"{workflow_id}-{candidate_id}-{now_ms()}", "candidate_id": candidate_id, "provider": provider, "model": prompt_item.get("model"), "voice_id": prompt_item.get("voice_id"), "output": output_rel, "output_path": str(workspace / output_rel), "prompt_preview": str(prompt_item.get("prompt") or "")[:1000], "prompt_length": len(str(prompt_item.get("prompt") or ""))}
                add_event(session_id, "ocrebuild.asset_tts.requested", request_payload)
                yield f"data: {json.dumps({'type': 'requested', **request_payload}, ensure_ascii=True)}\n\n"
                async_task = asyncio.create_task(asyncio.to_thread(run_tts_candidate, task_id, request_payload, prompt_item, output_rel))
                running[async_task] = request_payload
                heartbeat_counts[request_payload["api_call_id"]] = 0
            pending = set(running.keys())
            while pending:
                done, pending = await asyncio.wait(pending, timeout=2)
                for done_task in done:
                    request_payload = running[done_task]
                    try:
                        render_result = await done_task
                    except HTTPException as exc:
                        failed = {**request_payload, "status_code": exc.status_code, "detail": exc.detail, "elapsed_seconds": round(time.time() - started, 1), "status": "failed"}
                        upsert_tts_workflow_candidate(workspace, workflow_id, payload.scene_mark_id, failed)
                        add_event(session_id, "ocrebuild.asset_tts.failed", failed)
                        yield f"data: {json.dumps({'type': 'failed', **failed}, ensure_ascii=True)}\n\n"
                    except Exception as exc:
                        failed = {**request_payload, "detail": str(exc), "elapsed_seconds": round(time.time() - started, 1), "status": "failed"}
                        upsert_tts_workflow_candidate(workspace, workflow_id, payload.scene_mark_id, failed)
                        add_event(session_id, "ocrebuild.asset_tts.failed", failed)
                        yield f"data: {json.dumps({'type': 'failed', **failed}, ensure_ascii=True)}\n\n"
                    else:
                        result = {**request_payload, **render_result, "ok": True, "status": "completed", "elapsed_seconds": render_result.get("elapsed_seconds") or round(time.time() - started, 1)}
                        write_locked_tts_manifest(workspace, payload, prompts[0], result)
                        upsert_tts_workflow_candidate(workspace, workflow_id, payload.scene_mark_id, result)
                        add_event(session_id, "ocrebuild.asset_tts.generated", result)
                        yield f"data: {json.dumps({'type': 'completed', **result}, ensure_ascii=True)}\n\n"
                if pending:
                    for pending_task in list(pending):
                        request_payload = running[pending_task]
                        api_call_id = str(request_payload.get("api_call_id") or "")
                        heartbeat_counts[api_call_id] = heartbeat_counts.get(api_call_id, 0) + 1
                        heartbeat_payload = {**request_payload, "heartbeat": heartbeat_counts[api_call_id], "elapsed_seconds": round(time.time() - started, 1)}
                        add_event(session_id, "ocrebuild.asset_tts.heartbeat", heartbeat_payload)
                        yield f"data: {json.dumps({'type': 'heartbeat', **heartbeat_payload}, ensure_ascii=True)}\n\n"
            completed_payload = {**started_payload, "elapsed_seconds": round(time.time() - started, 1)}
            scenes = (read_asset_tts_workflow(workspace, workflow_id).get("scenes") or {})
            scene = scenes.get(payload.scene_mark_id) if isinstance(scenes.get(payload.scene_mark_id), dict) else {}
            scene["phase"] = "select"
            scenes[payload.scene_mark_id] = scene
            update_asset_tts_workflow(workspace, workflow_id, {"phase": "select", "scenes": scenes, "completed_at": now_ms(), "elapsed_seconds": completed_payload["elapsed_seconds"]})
            add_event(session_id, "ocrebuild.asset_tts.workflow.completed", completed_payload)
            yield f"data: {json.dumps({'type': 'round_completed', **completed_payload}, ensure_ascii=True)}\n\n"

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    @router.post("/api/ocrebuild/tasks/{task_id}/asset-tts/compare/finalize")
    async def finalize_asset_tts(task_id: int, payload: OCRebuildAssetTTSCompareFinalizePayload) -> dict[str, Any]:
        task_row = get_task(task_id)
        session_id = int(task_row["session_id"])
        workspace = workspace_path(task_row)
        selected = workspace / payload.selected_output.strip()
        try:
            selected_resolved = selected.resolve()
            if not str(selected_resolved).startswith(str(workspace.resolve())):
                raise HTTPException(status_code=400, detail="Selected output must stay inside the task workspace")
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"Selected output not found: {payload.selected_output}")
        if not selected.exists() or not selected.is_file():
            raise HTTPException(status_code=404, detail=f"Selected output not found: {payload.selected_output}")
        workflow_id = normalize_workflow_id(payload.workflow_id or f"{payload.shot_id}_tts_lab")
        final = {"selected_output": payload.selected_output.strip(), "provider": payload.provider.strip(), "model": payload.model.strip(), "voice_id": payload.voice_id.strip(), "duration": payload.duration if payload.duration is not None else audio_duration_seconds(selected), "finalized_at": now_ms()}
        current = read_asset_tts_workflow(workspace, workflow_id)
        scenes = current.get("scenes") if isinstance(current.get("scenes"), dict) else {}
        scene = scenes.get(payload.scene_mark_id) if isinstance(scenes.get(payload.scene_mark_id), dict) else {}
        scene.update({"phase": "finalized", "final": final, "tts_duration": final["duration"]})
        scenes[payload.scene_mark_id] = scene
        update_asset_tts_workflow(workspace, workflow_id, {"phase": "finalized", "scenes": scenes})
        result = {"ok": True, "task_id": task_id, "session_id": session_id, "shot_id": payload.shot_id, "scene_mark_id": payload.scene_mark_id, "workflow_id": workflow_id, **final}
        add_event(session_id, "ocrebuild.asset_tts.workflow.finalized", result)
        return result

    @router.get("/api/ocrebuild/tasks/{task_id}/asset-image/workflows/{workflow_id}")
    async def get_asset_image_workflow(task_id: int, workflow_id: str) -> dict[str, Any]:
        task_row = get_task(task_id)
        workspace = workspace_path(task_row)
        normalized = normalize_workflow_id(workflow_id)
        path = workflow_json_path(workspace, normalized)
        workflow = read_asset_workflow(workspace, normalized)
        return {"ok": True, "exists": path.exists(), "workflow_id": normalized, "workflow": workflow}

    @router.put("/api/ocrebuild/tasks/{task_id}/asset-image/workflows/{workflow_id}")
    async def save_asset_image_workflow(task_id: int, workflow_id: str, payload: OCRebuildAssetWorkflowSavePayload) -> dict[str, Any]:
        task_row = get_task(task_id)
        workspace = workspace_path(task_row)
        normalized = normalize_workflow_id(workflow_id)
        current = read_asset_workflow(workspace, normalized)
        next_workflow = {**current, **(payload.workflow or {}), "workflow_id": normalized, "task_id": task_id, "session_id": int(task_row["session_id"])}
        saved = write_asset_workflow(workspace, normalized, next_workflow)
        return {"ok": True, "workflow_id": normalized, "workflow": saved}

    @router.post("/api/ocrebuild/tasks/{task_id}/asset-image/compare/events")
    async def compare_asset_images(task_id: int, payload: OCRebuildAssetComparePayload) -> StreamingResponse:
        task_row = get_task(task_id)
        session_id = int(task_row["session_id"])
        workspace = workspace_path(task_row)
        workflow_id = normalize_workflow_id(payload.workflow_id)
        round_no = int(payload.round or 1)
        mode = payload.mode.strip() or "prompt_only"
        if not payload.prompts:
            raise HTTPException(status_code=400, detail="At least one provider prompt is required")
        _asset_tasks, _target, _asset_tasks_path, source_reference_path = asset_image_target(task_row, payload.shot_id, payload.scene_mark_id.strip(), payload.role.strip() or "single")
        reference_path = source_reference_path if mode == "with_reference" else None
        if payload.reference_output.strip():
            candidate = workspace / payload.reference_output.strip()
            try:
                candidate_resolved = candidate.resolve()
                workspace_resolved = workspace.resolve()
                if not str(candidate_resolved).startswith(str(workspace_resolved)):
                    raise HTTPException(status_code=400, detail="Reference output must stay inside the task workspace")
            except FileNotFoundError:
                raise HTTPException(status_code=404, detail=f"Reference output not found: {payload.reference_output}")
            if not candidate.exists() or not candidate.is_file():
                raise HTTPException(status_code=404, detail=f"Reference output not found: {payload.reference_output}")
            reference_path = candidate
        started_payload = {"workflow_id": workflow_id, "task_id": task_id, "session_id": session_id, "shot_id": payload.shot_id, "scene_mark_id": payload.scene_mark_id.strip(), "role": payload.role.strip() or "single", "mode": mode, "round": round_no, "temporary": True, "writes_asset_json": False}
        prompt_records = [{"provider": item.provider.strip(), "model": item.model.strip(), "prompt": item.prompt, "current_prompt": item.prompt, "user_instruction": item.user_instruction, "variant": int(item.variant or 0), "confirmed_at": now_ms()} for item in payload.prompts]
        current_workflow = read_asset_workflow(workspace, workflow_id)
        round_key = f"round_{round_no}"
        round_state = current_workflow.get(round_key) if isinstance(current_workflow.get(round_key), dict) else {}
        current_workflow.update({"workflow_id": workflow_id, "task_id": task_id, "session_id": session_id, "shot_id": payload.shot_id, "scene_mark_id": payload.scene_mark_id.strip(), "role": payload.role.strip() or "single", "mode": mode, "phase": "generating" if round_no == 1 else "refine_generating", "temporary": True, "writes_asset_json": False})
        if round_no == 1:
            current_workflow.pop("selected_candidate", None)
            current_workflow.pop("round_2", None)
            current_workflow.pop("final", None)
            current_workflow["round"] = 1
        else:
            current_workflow.pop("final", None)
            current_workflow["round"] = 2
        current_workflow[round_key] = {**round_state, "prompts": prompt_records, "started_at": now_ms(), "candidates": []}
        write_asset_workflow(workspace, workflow_id, current_workflow)
        add_event(session_id, "ocrebuild.asset_image.workflow.started", started_payload)

        async def event_generator() -> Any:
            started = time.time()
            yield f"data: {json.dumps({'type': 'workflow_started', **started_payload}, ensure_ascii=True)}\n\n"
            running: dict[asyncio.Task, dict[str, Any]] = {}
            heartbeat_counts: dict[str, int] = {}
            for index, prompt_item in enumerate(payload.prompts, start=1):
                provider = prompt_item.provider.strip()
                model_id = prompt_item.model.strip()
                variant = int(prompt_item.variant or 0)
                candidate_id = f"{provider}_round_{round_no}" if not variant else f"{provider}_round_{round_no}_variant_{variant}"
                api_call_id = f"{workflow_id}-{candidate_id}-{now_ms()}"
                output_rel = workflow_output_rel(workflow_id, round_no, provider, variant or index if round_no > 1 else 0)
                request_payload = {**started_payload, "api_call_id": api_call_id, "candidate_id": candidate_id, "provider": provider, "model": model_id, "variant": variant, "output": output_rel, "output_path": str(workspace / output_rel)}
                add_event(session_id, "ocrebuild.asset_image.requested", {**request_payload, "prompt_preview": prompt_item.prompt[:1000], "prompt_length": len(prompt_item.prompt)})
                yield f"data: {json.dumps({'type': 'requested', **request_payload}, ensure_ascii=True)}\n\n"
                try:
                    config = load_image_config(provider, model_id)
                except HTTPException as exc:
                    failed = {**request_payload, "status_code": exc.status_code, "detail": exc.detail, "elapsed_seconds": round(time.time() - started, 1)}
                    upsert_workflow_candidate(workspace, workflow_id, round_no, {**failed, "status": "failed"})
                    add_event(session_id, "ocrebuild.asset_image.failed", failed)
                    yield f"data: {json.dumps({'type': 'failed', **failed}, ensure_ascii=True)}\n\n"
                    continue
                task = asyncio.create_task(asyncio.to_thread(run_compare_candidate, task_id, request_payload, prompt_item.prompt, config, output_rel, reference_path))
                running[task] = request_payload
                heartbeat_counts[api_call_id] = 0
            pending = set(running.keys())
            while pending:
                done, pending = await asyncio.wait(pending, timeout=2)
                for task in done:
                    request_payload = running[task]
                    try:
                        result = await task
                    except HTTPException as exc:
                        failed = {**request_payload, "status_code": exc.status_code, "detail": exc.detail, "elapsed_seconds": round(time.time() - started, 1)}
                        upsert_workflow_candidate(workspace, workflow_id, round_no, {**failed, "status": "failed"})
                        add_event(session_id, "ocrebuild.asset_image.failed", failed)
                        yield f"data: {json.dumps({'type': 'failed', **failed}, ensure_ascii=True)}\n\n"
                    except Exception as exc:
                        failed = {**request_payload, "detail": str(exc), "elapsed_seconds": round(time.time() - started, 1)}
                        upsert_workflow_candidate(workspace, workflow_id, round_no, {**failed, "status": "failed"})
                        add_event(session_id, "ocrebuild.asset_image.failed", failed)
                        yield f"data: {json.dumps({'type': 'failed', **failed}, ensure_ascii=True)}\n\n"
                    else:
                        upsert_workflow_candidate(workspace, workflow_id, round_no, {**result, "status": "completed"})
                        yield f"data: {json.dumps({'type': 'completed', **result, 'elapsed_seconds': result.get('elapsed_seconds') or round(time.time() - started, 1)}, ensure_ascii=True)}\n\n"
                if pending:
                    for task in list(pending):
                        request_payload = running[task]
                        api_call_id = str(request_payload.get("api_call_id") or "")
                        heartbeat_counts[api_call_id] = heartbeat_counts.get(api_call_id, 0) + 1
                        heartbeat_payload = {**request_payload, "heartbeat": heartbeat_counts[api_call_id], "elapsed_seconds": round(time.time() - started, 1)}
                        add_event(session_id, "ocrebuild.asset_image.heartbeat", heartbeat_payload)
                        yield f"data: {json.dumps({'type': 'heartbeat', **heartbeat_payload}, ensure_ascii=True)}\n\n"
            completed_payload = {**started_payload, "elapsed_seconds": round(time.time() - started, 1)}
            workflow_snapshot = read_asset_workflow(workspace, workflow_id)
            completed_round_state = workflow_snapshot.get(round_key) if isinstance(workflow_snapshot.get(round_key), dict) else {}
            update_asset_workflow(workspace, workflow_id, {"phase": "select" if round_no == 1 else "final_select", round_key: {**completed_round_state, "completed_at": now_ms(), "elapsed_seconds": completed_payload["elapsed_seconds"]}})
            add_event(session_id, "ocrebuild.asset_image.workflow.round_completed", completed_payload)
            yield f"data: {json.dumps({'type': 'round_completed', **completed_payload}, ensure_ascii=True)}\n\n"

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    @router.post("/api/ocrebuild/tasks/{task_id}/asset-image/compare/finalize")
    async def finalize_compare_asset_image(task_id: int, payload: OCRebuildAssetCompareFinalizePayload) -> dict[str, Any]:
        task_row = get_task(task_id)
        session_id = int(task_row["session_id"])
        workspace = workspace_path(task_row)
        selected = workspace / payload.selected_output.strip()
        try:
            selected_resolved = selected.resolve()
            workspace_resolved = workspace.resolve()
            if not str(selected_resolved).startswith(str(workspace_resolved)):
                raise HTTPException(status_code=400, detail="Selected output must stay inside the task workspace")
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"Selected output not found: {payload.selected_output}")
        if not selected.exists() or not selected.is_file():
            raise HTTPException(status_code=404, detail=f"Selected output not found: {payload.selected_output}")
        asset_tasks, target, asset_tasks_path, _reference_path = asset_image_target(task_row, payload.shot_id, payload.scene_mark_id.strip(), payload.role.strip() or "single")
        output_rel = str(target.get("output") or "").strip()
        output_path = workspace / output_rel
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(selected, output_path)
        target.update({"status": "completed", "generated_at": now_ms(), "provider": payload.provider.strip(), "model": payload.model.strip(), "used_reference_image": bool(payload.used_reference_image), "compare_selected_output": payload.selected_output.strip()})
        tmp_path = asset_tasks_path.with_suffix(asset_tasks_path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(asset_tasks, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(asset_tasks_path)
        result = {"ok": True, "task_id": task_id, "session_id": session_id, "shot_id": payload.shot_id, "scene_mark_id": payload.scene_mark_id.strip(), "role": payload.role.strip() or "single", "selected_output": payload.selected_output.strip(), "output": output_rel, "output_path": str(output_path), "provider": payload.provider.strip(), "model": payload.model.strip(), "used_reference_image": bool(payload.used_reference_image)}
        workflow_id = normalize_workflow_id(payload.workflow_id) if payload.workflow_id.strip() else ""
        if workflow_id:
            update_asset_workflow(workspace, workflow_id, {"phase": "finalized", "final": {"selected_output": payload.selected_output.strip(), "asset_output": output_rel, "provider": payload.provider.strip(), "model": payload.model.strip(), "used_reference_image": bool(payload.used_reference_image), "finalized_at": now_ms()}})
        add_event(session_id, "ocrebuild.asset_image.workflow.finalized", result)
        return result

    @router.post("/api/ocrebuild/tasks/{task_id}/asset-image/copy-reference")
    async def copy_reference_asset_image(task_id: int, payload: OCRebuildAssetImageGeneratePayload) -> dict[str, Any]:
        task_row = get_task(task_id)
        session_id = int(task_row["session_id"])
        workspace = workspace_path(task_row)
        role = payload.role.strip() or "single"
        scene_id = payload.scene_mark_id.strip()
        plan_path = workspace / "rebuild_shot_plan.json"
        plan = read_json_file(plan_path)
        shot = next((item for item in plan.get("shots") or [] if isinstance(item, dict) and str(item.get("shot_id") or "") == payload.shot_id), None)
        reference = shot.get("reference") if isinstance(shot, dict) and isinstance(shot.get("reference"), dict) else {}
        mark = next((item for item in reference.get("scene_marks") or [] if isinstance(item, dict) and str(item.get("scene_mark_id") or "") == scene_id), None)
        if not shot or not mark:
            raise HTTPException(status_code=404, detail="Matching scene mark not found in rebuild_shot_plan.json")
        keyframes = mark.get("keyframes") if isinstance(mark.get("keyframes"), dict) else {}
        paths = keyframes.get("paths") if isinstance(keyframes.get("paths"), list) else []
        if role == "last":
            reference_rel = str(keyframes.get("last") or (paths[-1] if paths else "") or keyframes.get("first") or keyframes.get("single") or "").strip()
            output_rel = f"Assets/variant_001/{payload.shot_id}/{scene_id}/last.png"
        else:
            reference_rel = str(keyframes.get("first") or keyframes.get("single") or (paths[0] if paths else "") or "").strip()
            output_rel = f"Assets/variant_001/{payload.shot_id}/{scene_id}/first.png"
        if not reference_rel:
            raise HTTPException(status_code=404, detail="Reference image not found for this asset slot")
        reference_path = task_reference_file(task_row, reference_rel)
        if not reference_path or not reference_path.exists() or not reference_path.is_file():
            raise HTTPException(status_code=404, detail="Reference image not found for this asset slot")
        output_path = workspace / output_rel
        try:
            output_resolved = output_path.resolve(strict=False)
            workspace_resolved = workspace.resolve()
            if not str(output_resolved).startswith(str(workspace_resolved)):
                raise HTTPException(status_code=400, detail="Asset output must stay inside the task workspace")
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"Asset output path not found: {output_rel}")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if reference_path.resolve() != output_path.resolve(strict=False):
            shutil.copyfile(reference_path, output_path)
        copied_at = now_ms()
        manifest_rel = f"Assets/variant_001/{payload.shot_id}/{scene_id}/asset_manifest.json"
        manifest_path = workspace / manifest_rel
        manifest = {
            "status": "completed",
            "tool": "ocrebuild.copy_reference_image",
            "tool_version": "1.0",
            "shot_id": payload.shot_id,
            "scene_mark_id": scene_id,
            "source": "copy_reference_image",
            "selected_image": output_rel,
            "reference_image": reference_rel,
            "reference_used": True,
            "generated_at": copied_at,
            "provider": "reference",
            "model": "copy-original",
            "copied_reference_frame": str(reference_path),
            "bytes": output_path.stat().st_size,
        }
        write_json_file(manifest_path, manifest)
        for shot in plan.get("shots") or []:
            if not isinstance(shot, dict) or str(shot.get("shot_id") or "") != payload.shot_id:
                continue
            reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
            for mark in reference.get("scene_marks") or []:
                if not isinstance(mark, dict) or str(mark.get("scene_mark_id") or "") != scene_id:
                    continue
                plan_a = mark.get("plan_a") if isinstance(mark.get("plan_a"), dict) else {}
                scene_asset = plan_a.get("scene_asset") if isinstance(plan_a.get("scene_asset"), dict) else {}
                scene_asset.update({
                    "source": "copy_reference_image",
                    "generated_at": copied_at,
                    "provider": "reference",
                    "model": "copy-original",
                    "copied_reference_frame": str(reference_path),
                    "uses_only_first_frame": role != "last",
                })
                if role == "last":
                    scene_asset["selected_last_image"] = output_rel
                    scene_asset["last_manifest"] = manifest_rel
                else:
                    scene_asset["selected_image"] = output_rel
                    scene_asset["manifest"] = manifest_rel
                plan_a["scene_asset"] = scene_asset
                mark["plan_a"] = plan_a
        if plan:
            write_json_file(plan_path, plan)
        result = {
            "ok": True,
            "task_id": task_id,
            "session_id": session_id,
            "shot_id": payload.shot_id,
            "scene_mark_id": payload.scene_mark_id.strip(),
            "role": role,
            "source": str(reference_path),
            "output": output_rel,
            "output_path": str(output_path),
            "provider": "reference",
            "model": "copy-original",
            "generated_at": copied_at,
            "requires_asset_tasks": False,
        }
        add_event(session_id, "ocrebuild.asset_image.reference_copied", result)
        return result

    @router.post("/api/ocrebuild/tasks/{task_id}/asset-image/delete")
    async def delete_asset_image(task_id: int, payload: OCRebuildAssetImageGeneratePayload) -> dict[str, Any]:
        task_row = get_task(task_id)
        session_id = int(task_row["session_id"])
        workspace = workspace_path(task_row)
        role = payload.role.strip() or "single"
        scene_id = payload.scene_mark_id.strip()
        plan_path = workspace / "rebuild_shot_plan.json"
        plan = read_json_file(plan_path)
        output_rel = ""
        manifest_rel = ""
        for shot in plan.get("shots") or []:
            if not isinstance(shot, dict) or str(shot.get("shot_id") or "") != payload.shot_id:
                continue
            reference = shot.get("reference") if isinstance(shot.get("reference"), dict) else {}
            for mark in reference.get("scene_marks") or []:
                if not isinstance(mark, dict) or str(mark.get("scene_mark_id") or "") != scene_id:
                    continue
                plan_a = mark.get("plan_a") if isinstance(mark.get("plan_a"), dict) else {}
                scene_asset = plan_a.get("scene_asset") if isinstance(plan_a.get("scene_asset"), dict) else {}
                if role == "last":
                    output_rel = str(scene_asset.pop("selected_last_image", "") or scene_asset.pop("last_image", "") or "").strip()
                    manifest_rel = str(scene_asset.pop("last_manifest", "") or "").strip()
                else:
                    output_rel = str(scene_asset.pop("selected_image", "") or "").strip()
                    manifest_rel = str(scene_asset.pop("manifest", "") or "").strip()
                if not scene_asset.get("selected_image") and not scene_asset.get("selected_last_image") and not scene_asset.get("last_image"):
                    for key in ("generated_at", "provider", "model", "copied_reference_frame", "source"):
                        scene_asset.pop(key, None)
                if scene_asset:
                    plan_a["scene_asset"] = scene_asset
                else:
                    plan_a.pop("scene_asset", None)
                if plan_a:
                    mark["plan_a"] = plan_a
                else:
                    mark.pop("plan_a", None)
        deleted: list[str] = []

        if not output_rel:
            try:
                _asset_tasks, target, _asset_tasks_path, _reference_path = asset_image_target(task_row, payload.shot_id, scene_id, role)
                output_rel = str(target.get("output") or "").strip()
            except HTTPException:
                output_rel = ""

        for rel_value in [output_rel, manifest_rel]:
            rel_value = str(rel_value or "").strip()
            if not rel_value:
                continue
            candidate = workspace / rel_value
            try:
                resolved = candidate.resolve(strict=False)
                workspace_resolved = workspace.resolve()
                if not str(resolved).startswith(str(workspace_resolved)):
                    raise HTTPException(status_code=400, detail="Asset delete path must stay inside the task workspace")
            except FileNotFoundError:
                continue
            if candidate.exists() and candidate.is_file():
                candidate.unlink()
                deleted.append(rel_value)

        asset_tasks_path = workspace / "asset_tasks.json"
        if asset_tasks_path.exists() and asset_tasks_path.is_file():
            asset_tasks = read_json_file(asset_tasks_path)
            tasks = asset_tasks.get("tasks") if isinstance(asset_tasks.get("tasks"), list) else []
            image_types = {"single": ["image_regenerate_single"], "first": ["image_regenerate_first", "image_regenerate_single"], "last": ["image_regenerate_last"]}.get(role, ["image_regenerate_single"])
            changed_asset_tasks = False
            for item in tasks:
                if not isinstance(item, dict):
                    continue
                if str(item.get("shot_id") or "") != payload.shot_id or str(item.get("scene_mark_id") or "") != scene_id:
                    continue
                if str(item.get("type") or "") not in image_types:
                    continue
                if output_rel and str(item.get("output") or "") != output_rel:
                    continue
                for key in ("generated_at", "updated_at", "provider", "model", "used_reference_image", "compare_selected_output", "copied_reference_frame"):
                    item.pop(key, None)
                item["status"] = "deleted"
                changed_asset_tasks = True
            if changed_asset_tasks:
                write_json_file(asset_tasks_path, asset_tasks)

        if plan:
            write_json_file(plan_path, plan)
        result = {
            "ok": True,
            "task_id": task_id,
            "session_id": session_id,
            "shot_id": payload.shot_id,
            "scene_mark_id": scene_id,
            "role": role,
            "output": output_rel,
            "deleted": deleted,
        }
        add_event(session_id, "ocrebuild.asset_image.deleted", result)
        return result

    @router.get("/api/ocrebuild/tasks/{task_id}/asset-image/generate/events")
    async def generate_asset_image_events(task_id: int, shot_id: str, scene_mark_id: str = "", role: str = "single", use_reference_image: bool = False, api_call_id: str = "") -> StreamingResponse:
        payload = OCRebuildAssetImageGeneratePayload(shot_id=shot_id, scene_mark_id=scene_mark_id, role=role, use_reference_image=use_reference_image, api_call_id=api_call_id)
        task_row = get_task(task_id)
        session_id = int(task_row["session_id"])
        workspace = workspace_path(task_row)
        request_payload = {"api_call_id": payload.api_call_id.strip() or f"ocrebuild-image-{now_ms()}", "task_id": task_id, "session_id": session_id, "shot_id": shot_id, "scene_mark_id": scene_mark_id, "role": role, "use_reference_image": use_reference_image, "workspace_dir": str(workspace)}
        payload.api_call_id = request_payload["api_call_id"]
        add_event(session_id, "ocrebuild.asset_image.requested", request_payload)

        async def event_generator() -> Any:
            started = time.time()
            yield f"data: {json.dumps({'type': 'started', **request_payload}, ensure_ascii=True)}\n\n"
            task = asyncio.create_task(asyncio.to_thread(run_asset_image_generation, task_id, payload))
            heartbeat_no = 0
            while not task.done():
                await asyncio.sleep(2)
                if task.done():
                    break
                heartbeat_no += 1
                heartbeat_payload = {**request_payload, "heartbeat": heartbeat_no, "elapsed_seconds": round(time.time() - started, 1)}
                add_event(session_id, "ocrebuild.asset_image.heartbeat", heartbeat_payload)
                yield f"data: {json.dumps({'type': 'heartbeat', **heartbeat_payload}, ensure_ascii=True)}\n\n"
            try:
                result = await task
            except HTTPException as exc:
                failed = {**request_payload, "status_code": exc.status_code, "detail": exc.detail, "elapsed_seconds": round(time.time() - started, 1)}
                add_event(session_id, "ocrebuild.asset_image.failed", failed)
                yield f"data: {json.dumps({'type': 'failed', **failed}, ensure_ascii=True)}\n\n"
                return
            except Exception as exc:
                failed = {**request_payload, "detail": str(exc), "elapsed_seconds": round(time.time() - started, 1)}
                add_event(session_id, "ocrebuild.asset_image.failed", failed)
                yield f"data: {json.dumps({'type': 'failed', **failed}, ensure_ascii=True)}\n\n"
                return
            yield f"data: {json.dumps({'type': 'completed', **result, 'elapsed_seconds': round(time.time() - started, 1)}, ensure_ascii=True)}\n\n"

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    @router.put("/api/ocrebuild/tasks/{task_id}/versions/{version_id}")
    async def update_version(task_id: int, version_id: int) -> dict[str, Any]:
        task_row = get_task(task_id)
        version = repo.get_version(version_id)
        if not version or int(version["task_id"]) != task_id:
            raise HTTPException(status_code=404, detail="Version not found")
        repo.update_version(version_id, snapshot_json=json.dumps(task_snapshot(task_row), ensure_ascii=False), final_prompt=str(task_row.get("final_prompt") or ""))
        repo.update_task(task_id, current_version_id=version_id, updated_at=now_ms())
        return serialize_task_detail(get_task(task_id))

    @router.post("/api/ocrebuild/tasks/{task_id}/versions/load")
    async def load_version(task_id: int, payload: OCRebuildVersionLoadPayload) -> dict[str, Any]:
        version = repo.get_version(payload.version_id)
        if not version or int(version["task_id"]) != task_id:
            raise HTTPException(status_code=404, detail="Version not found")
        snap = json.loads(str(version.get("snapshot_json") or "{}"))
        values = {
            "analysis_task_id": int(snap.get("analysis_task_id") or 0) or None,
            "source_package_path": str(snap.get("source_package_path") or "source_package.json"),
            "source_scheme": str(snap.get("source_scheme") or "detail"),
            "target_topic": str(snap.get("target_topic") or ""),
            "target_platform": str(snap.get("target_platform") or ""),
            "aspect_ratio": str(snap.get("aspect_ratio") or "9:16"),
            "target_count": int(snap.get("target_count") or 1),
            "target_audience": str(snap.get("target_audience") or ""),
            "product_info": str(snap.get("product_info") or ""),
            "rebuild_goal": str(snap.get("rebuild_goal") or ""),
            "preserve_strategy_json": json.dumps(snap.get("preserve_strategy") or {}, ensure_ascii=False),
            "replace_strategy_json": json.dumps(snap.get("replace_strategy") or {}, ensure_ascii=False),
            "visual_style": str(snap.get("visual_style") or ""),
            "subtitle_style": str(snap.get("subtitle_style") or ""),
            "title_style": str(snap.get("title_style") or ""),
            "voice_style": str(snap.get("voice_style") or ""),
            "batch_variables": str(snap.get("batch_variables") or ""),
            "constraints": str(snap.get("constraints") or ""),
            "simple_prompt": str(snap.get("simple_prompt") or ""),
            "final_prompt": str(snap.get("final_prompt") or ""),
        }
        repo.update_task(task_id, current_version_id=int(version["id"]), **values, updated_at=now_ms())
        return serialize_task_detail(get_task(task_id))

    @router.delete("/api/ocrebuild/tasks/{task_id}/versions/{version_id}")
    async def delete_version(task_id: int, version_id: int) -> dict[str, Any]:
        repo.delete_version(task_id, version_id)
        return serialize_task_detail(get_task(task_id))

    @router.post("/api/ocrebuild/tasks/{task_id}/run")
    async def run_task(request: Request, task_id: int, payload: OCRebuildRunPayload) -> dict[str, Any]:
        task_row = get_task(task_id)
        session_row = safe_session(int(task_row["session_id"]))
        model, _ = resolve_model(session_row, payload.run_model_provider or str(task_row.get("run_model_provider") or ""), payload.run_model_id or str(task_row.get("run_model_id") or ""), "Run")
        attempt = repo.create_attempt(task_id=task_id, session_id=int(task_row["session_id"]), status="queued", run_model_provider=model["providerID"], run_model_id=model["modelID"], created_at=now_ms())
        prompt = str(task_row.get("final_prompt") or task_row.get("simple_prompt") or "").strip()
        if not prompt:
            raise HTTPException(status_code=400, detail="Final Prompt or Simple Prompt is required before running")
        opencode_client_for(session_row).prompt_async(str(session_row["opencode_session_id"]), prompt, model=model, system="You are OC-Rebuild. Plan next rebuild workflow steps using the Rebuild Tool Library and current rebuild intent. Do not execute high-cost work without confirmation.")
        repo.update_task(task_id, status="running", latest_attempt_id=int(attempt["id"]), run_model_provider=model["providerID"], run_model_id=model["modelID"], updated_at=now_ms())
        add_event(int(task_row["session_id"]), "ocrebuild.run.started", {"task_id": task_id, "attempt_id": int(attempt["id"]), "model": model})
        return {"ok": True, "task_id": task_id, "session_id": int(task_row["session_id"]), "attempt_id": int(attempt["id"])}

    return router
