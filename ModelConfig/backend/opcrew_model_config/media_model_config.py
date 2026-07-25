from __future__ import annotations

import json
import base64
import hashlib
import io
import math
import os
import re
import struct
import subprocess
import threading
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import wave
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from opcrew_backend.context import AppContext
from opcrew_backend.services.provider_resolver import resolve_endpoint, urlopen as provider_urlopen
from opcrew_backend.services.safe_download import decode_data_url_bytes, safe_download_bytes


CONFIG_TABLE = "tool_media_provider_configs"
AGENT_MODEL_ALIASES_PROVIDER = "__agent_model_aliases__"
MediaKind = Literal["image", "video", "tts", "lipsync", "digital-human", "voice-clone"]
TTS_PREVIEW_AUDIO_CONTENT_TYPES = ("audio/*", "application/octet-stream")
TTS_PREVIEW_MAX_BYTES = 25 * 1024 * 1024
BYTEDANCE_TTS_V1_ENDPOINT = "https://openspeech.bytedance.com/api/v1/tts"
BYTEPLUS_TTS_V3_ENDPOINT = "https://voice.ap-southeast-1.bytepluses.com/api/v3/tts/unidirectional"
BYTEPLUS_TTS_APP_KEY = "aGjiRDfUWi"
BYTEPLUS_TTS_RESOURCE_ID = "seed-tts-2.0"
_DASHSCOPE_TTS_V2_LOCK = threading.Lock()
DASHSCOPE_TTS_SHARED_PROVIDERS = {"qwen", "cosyvoice"}
DASHSCOPE_TTS_SHARED_KEY_REFS = (
    "tts_qwen_key",
    "tts_cosyvoice_key",
    "DASHSCOPE_API_KEY",
    "QWEN_API_KEY",
    "OPENCREW_TTS_API_KEY",
)
COSYVOICE_VOICE_CLONE_SHARED_KEY_REFS = (
    "voice_clone_cosyvoice_key",
    *DASHSCOPE_TTS_SHARED_KEY_REFS,
    "OPENCREW_DASHSCOPE_API_KEY",
)
WAN_VIDEO_SHARED_KEY_REFS = (
    "video_wan_key",
    "tts_qwen_key",
    "tts_cosyvoice_key",
    "DASHSCOPE_API_KEY",
    "QWEN_API_KEY",
    "OPENCREW_DASHSCOPE_API_KEY",
    "OPENCREW_VIDEO_API_KEY",
    "WAN_API_KEY",
)


MEDIA_MODEL_PRICE_SUMMARIES: dict[str, str] = {
    "gpt-image-2": "Text in $5/1M, image in $8/1M, out $30/1M",
    "gpt-image-1.5": "Text/image in $5/$8 per 1M, image out $32/1M",
    "gpt-image-1": "Price not listed on current OpenAI pricing page",
    "gpt-image-1-mini": "Text in $2/1M, image in $2.50/1M, out $8/1M",
    "grok-imagine-image-quality": "$0.05/image 1K, $0.07/image 2K; image input $0.01",
    "grok-imagine-image": "$0.02/image output; image input $0.002",
    "gemini-3.1-flash-image": "$0.50/1M input; images $0.045-$0.151 each",
    "gemini-3-pro-image": "$2/1M input; images $0.134-$0.24 each",
    "gemini-2.5-flash-image": "$0.30/1M input; $0.039/image",
    "sora-2": "$0.10/s 720p; batch $0.05/s",
    "sora-2-pro": "$0.30/s 720p, $0.50/s 1024p, $0.70/s 1080p",
    "grok-imagine-video-1.5-preview": "$0.08/s 480p, $0.14/s 720p, $0.25/s 1080p; image input $0.01",
    "grok-imagine-video": "$0.05/s 480p, $0.07/s 720p; image input $0.002",
    "veo-3.1-generate-preview": "$0.40/s 720p/1080p; $0.60/s 4K",
    "veo-3.1-fast-generate-preview": "$0.10/s 720p, $0.12/s 1080p, $0.30/s 4K",
    "veo-3.1-lite-generate-preview": "$0.05/s 720p, $0.08/s 1080p; no 4K",
    "veo-3.0-generate-001": "$0.40/s video with audio",
    "veo-3.0-fast-generate-001": "$0.10/s 720p, $0.12/s 1080p, $0.30/s 4K",
    "gemini-omni-flash-preview": "Preview paid tier; approximately $0.10/s for 720p output at the 2026-07-22 snapshot",
    "kling-3.0-turbo": "CN ¥0.8/s 720P, ¥1.0/s 1080P; official table lists Turbo as audio-priced",
    "kling-v3-omni": "CN ¥0.6/s 720P, ¥0.8/s 1080P, ¥3.0/s 4K no-ref/no-audio; refs/audio raise 720P/1080P to ¥0.8-1.2/s",
    "wan2.7-t2v-2026-04-25": "CN ¥0.6/s 720P, ¥1/s 1080P",
    "happyhorse-1.0-i2v": "CN ¥0.9/s 720P, ¥1.6/s 1080P",
    "wan2.7-i2v-2026-04-25": "CN ¥0.6/s 720P, ¥1/s 1080P",
    "happyhorse-1.0-r2v": "CN ¥0.9/s 720P, ¥1.6/s 1080P",
    "wan2.7-r2v": "Input+output: CN ¥0.6/s 720P, ¥1/s 1080P",
    "doubao-seedance-2-0-fast-260128": "Volcano Ark Seedance 2.0 Fast; billed by video duration/resolution",
    "bytedance/seedance-2.0-fast": "OpenRouter Seedance 2.0 Fast; from about $0.0538/s, check current OpenRouter pricing",
    "bytedance/seedance-2.0": "OpenRouter Seedance 2.0; from about $0.06726/s, check current OpenRouter pricing",
    "bytedance/seedance-1-5-pro": "OpenRouter Seedance 1.5 Pro; from about $0.02306/s, check current OpenRouter pricing",
    "Doubao-Seedance-1.0-lite-i2v": "蝉镜 OpenAPI 视频模型；按蝉豆扣费，实际消耗以蝉镜控制台使用明细为准。",
    "Doubao-Seedance-1.0-pro": "蝉镜 OpenAPI 视频模型；按蝉豆扣费，实际消耗以蝉镜控制台使用明细为准。",
    "kling1.6": "蝉镜 OpenAPI 可灵视频模型；按蝉豆扣费，实际消耗以蝉镜控制台使用明细为准。",
    "kling-v2-1-master": "蝉镜 OpenAPI 可灵视频模型；按蝉豆扣费，实际消耗以蝉镜控制台使用明细为准。",
    "kling2.5": "蝉镜 OpenAPI 可灵 2.5 视频模型；按蝉豆扣费，支持 1080 与 5/6/10 秒任务。",
    "MiniMax-Hailuo-02": "蝉镜 OpenAPI MiniMax Hailuo 02 视频模型；按蝉豆扣费，实际消耗以蝉镜控制台使用明细为准。",
    "viduq1": "蝉镜 OpenAPI 视频模型；按蝉豆扣费，实际消耗以蝉镜控制台使用明细为准。",
    "happyhorse-1.0-t2v": "蝉镜 OpenAPI 快乐马文生视频模型；按蝉豆扣费，实际消耗以蝉镜控制台使用明细为准。",
    "happyhorse-1.0-i2v": "蝉镜 OpenAPI 快乐马图生视频模型；按蝉豆扣费，实际消耗以蝉镜控制台使用明细为准。",
    "happyhorse-1.0-r2v": "蝉镜 OpenAPI 快乐马参考生视频模型；按蝉豆扣费，实际消耗以蝉镜控制台使用明细为准。",
    "happyhorse-1.0-video-edit": "蝉镜 OpenAPI 快乐马视频编辑模型；按蝉豆扣费，实际消耗以蝉镜控制台使用明细为准。",
    "gemini-3.1-flash-tts-preview": "Preview TTS model; billed by Gemini audio generation pricing",
    "gemini-2.5-flash-preview-tts": "Preview TTS model; lower latency Gemini TTS",
    "gemini-2.5-pro-preview-tts": "Preview TTS model; higher quality Gemini TTS",
    "xai-tts": "xAI TTS endpoint; voice_id based",
    "cosyvoice-v3.5-plus": "Bailian CosyVoice v3.5 Plus; high quality/custom voice scenarios",
    "cosyvoice-v3.5-flash": "Bailian CosyVoice v3.5 Flash; lower latency scenarios",
    "qwen3-tts-flash": "Bailian Qwen3 TTS Flash; standard narration",
    "qwen3-tts-flash-2025-11-27": "Bailian Qwen3 TTS Flash pinned version",
    "qwen3-tts-instruct-flash": "Bailian Qwen3 TTS Instruct Flash; prompt-controlled style testing",
    "qwen3-tts-instruct-flash-2026-01-26": "Bailian Qwen3 TTS Instruct Flash pinned version",
    "seed-tts-1.1": "Volcano Doubao Seed TTS 1.1 via HTTP non-streaming; billed by characters",
    "MiniMax/speech-2.8-hd": "MiniMax 2.8 HD; high-quality narration",
    "MiniMax/speech-02-hd": "MiniMax 02 HD; stable high-quality narration",
    "MiniMax/speech-2.8-turbo": "MiniMax 2.8 Turbo; lower latency",
    "MiniMax/speech-02-turbo": "MiniMax 02 Turbo; stable low-latency mode",
    "lipsync-1.9.0": "Sync.so legacy low-cost model; $0.025/s on Hobbyist/Creator, lower with paid-tier discounts.",
    "lipsync-2": "Sync.so standard model; $0.05/s on Hobbyist/Creator, $0.0475/s Growth, $0.04/s Scale.",
    "lipsync-2-pro": "Sync.so higher quality model; $0.08325/s on Hobbyist/Creator, $0.07925/s Growth, $0.06675/s Scale.",
    "sync-3": "Sync.so latest lip-sync family; $0.13340/s on Hobbyist/Creator, $0.12660/s Growth, $0.10660/s Scale.",
    "heygen-lipsync-speed": "HeyGen Lipsync Speed mode; $0.0333/s API wallet pricing.",
    "heygen-lipsync-precision": "HeyGen Lipsync Precision mode; $0.0667/s API wallet pricing.",
    "kling-lipsync-advanced": "Kling AI advanced lip-sync; ¥0.5 per 5 seconds, also charges ¥0.05 per face-identify request.",
    "chanjing-lipsync-basic": "蝉镜口型驱动基础版；API 使用 model=0，按蝉豆扣费，公开文档未列固定每秒单价。",
    "chanjing-lipsync-quality": "蝉镜口型驱动高质量版；API 使用 model=1，按蝉豆扣费，推荐最终出片。",
    "heygen-video-agent-v3": "HeyGen Video Agent v3; prompt to AI presenter video through HeyGen.",
    "heygen-voice-clone-v3": "HeyGen Voice Clone v3; creates reusable cloned voices from reference audio.",
}


class MediaProviderSavePayload(BaseModel):
    provider: str
    model: str
    api_key: str = ""
    enabled: bool = True
    selected_voice_by_model: dict[str, str] = {}
    extra: dict[str, Any] = {}


class MediaAgentModelAliasPayload(BaseModel):
    alias: str
    provider: str
    model: str
    created_at: int | None = None
    updated_at: int | None = None


class MediaConfigSavePayload(BaseModel):
    active_provider: str
    providers: list[MediaProviderSavePayload]
    agent_model_aliases: list[MediaAgentModelAliasPayload] = []


class MediaConnectionTestPayload(BaseModel):
    provider: str
    model: str


class TTSVoicePreviewPayload(BaseModel):
    provider: str
    model: str
    config_kind: str = "tts"
    voice_id: str
    second_voice_id: str = ""
    speaker_1: str = "Speaker1"
    speaker_2: str = "Speaker2"
    sample_text: str = ""
    simple_prompt: str = ""
    complex_prompt: str = ""
    multi_speaker: bool = False
    language: str = "zh"


class TTSVoiceMatchPayload(BaseModel):
    reference_audio_path: str
    reference_text: str = ""
    target_gender: str = ""
    sample_text: str = ""
    language: str = "zh"
    top_k: int = 3
    regenerate: bool = False
    provider_models: dict[str, str] = Field(default_factory=dict)


def connection_result(ok: bool, message: str, detail: str = "") -> dict[str, Any]:
    return {"ok": ok, "status": "success" if ok else "failed", "message": message, "detail": detail}


def model_option(model: str, label: str | None = None, **extra: Any) -> dict[str, Any]:
    return {"model": model, "label": label or model, "price_summary": MEDIA_MODEL_PRICE_SUMMARIES.get(model, ""), **extra}


def default_api_key_ref(kind: str, provider: str) -> str:
    return f"{kind.replace('-', '_')}_{provider}_key"


def shared_api_key_refs(kind: str, provider: str) -> tuple[str, ...]:
    if kind == "tts" and provider in DASHSCOPE_TTS_SHARED_PROVIDERS:
        return DASHSCOPE_TTS_SHARED_KEY_REFS
    if kind == "voice-clone" and provider == "cosyvoice":
        return COSYVOICE_VOICE_CLONE_SHARED_KEY_REFS
    if kind == "video" and provider == "wan":
        return WAN_VIDEO_SHARED_KEY_REFS
    return ()


def agent_alias_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def gemini_omni_feature_enabled() -> bool:
    return str(os.environ.get("OPENCREW_GEMINI_OMNI_ENABLED") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def canonical_agent_model_alias_target(kind: str, alias: str, provider: str, model: str) -> tuple[str, str]:
    if kind != "video":
        return provider, model
    alias_key = agent_alias_key(alias)
    if alias_key == "maxwr27":
        return "wan", "wan2.7-r2v"
    if alias_key in {"maxhr10", "happyhorse10", "happyhorse1"}:
        return "wan", "happyhorse-1.0-r2v"
    if alias_key in {"geminiomniflash", "omniflash", "omnivideo", "geminiomni"}:
        return "gemini", "gemini-omni-flash-preview"
    return provider, model


def voice_option(
    voice_id: str,
    label: str | None = None,
    *,
    language: str = "zh",
    gender: str = "",
    style: str = "",
    mode: str = "preset",
    sample_text: str = "欢迎使用 OpenCrew。下面这段声音将用于测试语气、节奏、清晰度和商业短视频旁白的自然程度。",
) -> dict[str, Any]:
    return {
        "voice_id": voice_id,
        "label": label or voice_id,
        "language": language,
        "gender": gender,
        "style": style,
        "mode": mode,
        "sample_text": sample_text,
    }


GOOGLE_TTS_VOICES = [
    voice_option("Zephyr", "Zephyr - 明亮", language="en", gender="female", style="明亮"),
    voice_option("Puck", "Puck - 欢快", language="en", gender="male", style="欢快"),
    voice_option("Charon", "Charon - 信息丰富", language="en", gender="male", style="信息丰富"),
    voice_option("Kore", "Kore - 坚定", language="en", gender="female", style="坚定"),
    voice_option("Fenrir", "Fenrir - 易兴奋", language="en", gender="male", style="易兴奋"),
    voice_option("Leda", "Leda - 年轻", language="en", gender="female", style="年轻"),
    voice_option("Orus", "Orus - 坚定", language="en", gender="male", style="坚定"),
    voice_option("Aoede", "Aoede - 轻快", language="en", gender="female", style="轻快"),
    voice_option("Callirrhoe", "Callirrhoe - 放松", language="en", gender="female", style="放松"),
    voice_option("Autonoe", "Autonoe - 明亮", language="en", gender="female", style="明亮"),
    voice_option("Enceladus", "Enceladus - 气声", language="en", gender="male", style="气声"),
    voice_option("Iapetus", "Iapetus - 清晰", language="en", gender="male", style="清晰"),
    voice_option("Umbriel", "Umbriel - 随和", language="en", gender="male", style="随和"),
    voice_option("Algieba", "Algieba - 平滑", language="en", gender="male", style="平滑"),
    voice_option("Despina", "Despina - 平滑", language="en", gender="female", style="平滑"),
    voice_option("Erinome", "Erinome - 清晰", language="en", gender="female", style="清晰"),
    voice_option("Algenib", "Algenib - 沙哑", language="en", gender="male", style="沙哑"),
    voice_option("Rasalgethi", "Rasalgethi - 信息丰富", language="en", gender="male", style="信息丰富"),
    voice_option("Laomedeia", "Laomedeia - 欢快", language="en", gender="female", style="欢快"),
    voice_option("Achernar", "Achernar - 柔和", language="en", gender="female", style="柔和"),
    voice_option("Alnilam", "Alnilam - 坚定", language="en", gender="male", style="坚定"),
    voice_option("Schedar", "Schedar - 平稳", language="en", gender="male", style="平稳"),
    voice_option("Gacrux", "Gacrux - 成熟", language="en", gender="female", style="成熟"),
    voice_option("Pulcherrima", "Pulcherrima - 直接", language="en", gender="female", style="直接"),
    voice_option("Achird", "Achird - 友好", language="en", gender="male", style="友好"),
    voice_option("Zubenelgenubi", "Zubenelgenubi - 随意", language="en", gender="male", style="随意"),
    voice_option("Vindemiatrix", "Vindemiatrix - 温柔", language="en", gender="female", style="温柔"),
    voice_option("Sadachbia", "Sadachbia - 活泼", language="en", gender="male", style="活泼"),
    voice_option("Sadaltager", "Sadaltager - 知识渊博", language="en", gender="male", style="知识渊博"),
    voice_option("Sulafat", "Sulafat - 温暖", language="en", gender="female", style="温暖"),
]

XAI_TTS_VOICES = [
    voice_option("ara", "Ara - 多语言温暖女声", language="multilingual", gender="female", style="多语言；温暖友好，适合客服、对话和旁白"),
    voice_option("eve", "Eve - 多语言活力女声", language="multilingual", gender="female", style="多语言；活力清晰，适合演示、公告和轻快内容"),
    voice_option("leo", "Leo - 多语言权威男声", language="multilingual", gender="male", style="多语言；权威有力，适合说明、教学和指令"),
    voice_option("rex", "Rex - 多语言商务男声", language="multilingual", gender="male", style="多语言；自信清晰，适合商务演示和企业沟通"),
    voice_option("sal", "Sal - 多语言均衡男声", language="multilingual", gender="male", style="多语言；平滑均衡，适合通用内容"),
    voice_option("e521cc67", "Hui - 中文年轻女声", language="zh", gender="female", style="中文；年轻女声，适合亲和型介绍和轻快旁白"),
    voice_option("9ab26871", "Wei - 中文年轻男声", language="zh", gender="male", style="中文；年轻男声，适合清爽讲解和短视频旁白"),
    voice_option("6997b0ec", "Yang - 中文成熟男声", language="zh", gender="male", style="中文；成熟男声，适合稳重说明和商业内容"),
    voice_option("09b02491", "Mei - 中文年轻女声", language="zh", gender="female", style="中文；年轻女声，适合温和介绍和生活化表达"),
    voice_option("d11249e6", "Emma - 美式英语资深女声", language="en-US", gender="female", style="美式英语；资深女声，适合叙事和可信说明"),
    voice_option("6a41d324", "Liam - 美式英语成熟男声", language="en-US", gender="male", style="美式英语；成熟男声，适合产品说明和企业内容"),
    voice_option("f15c6a6a", "Henry - 英式英语成熟男声", language="en-GB", gender="male", style="英式英语；成熟男声，适合正式叙述和品牌内容"),
    voice_option("bedd6226", "Olivia - 英式英语年轻女声", language="en-GB", gender="female", style="英式英语；年轻女声，适合轻快介绍和清晰播报"),
    voice_option("a7b78b05", "Sean - 爱尔兰英语成熟男声", language="en-IE", gender="male", style="爱尔兰英语；成熟男声，适合自然对话和地区化表达"),
    voice_option("355dca53", "Niamh - 爱尔兰英语成熟女声", language="en-IE", gender="female", style="爱尔兰英语；成熟女声，适合温和叙事和本地化内容"),
    voice_option("5d695b41", "Marc - 南非英语成熟男声", language="en-ZA", gender="male", style="南非英语；成熟男声，适合地区化说明和访谈感内容"),
    voice_option("135ff7ec", "Thandi - 南非英语成熟女声", language="en-ZA", gender="female", style="南非英语；成熟女声，适合自然叙事和本地化表达"),
    voice_option("custom_voice_id", "Custom voice id", language="multilingual", mode="custom_voice_id", style="自定义克隆音色；从 xAI Voice Library 复制 voice_id"),
]

QWEN_TTS_SHARED_VOICES = [
    voice_option("Cherry", "Cherry - 芊悦：阳光积极女声", gender="female", style="阳光积极、亲切自然小姐姐；支持中文普通话和多语种"),
    voice_option("Serena", "Serena - 苏瑶：温柔女声", gender="female", style="温柔小姐姐；支持中文普通话和多语种"),
    voice_option("Ethan", "Ethan - 晨煦：温暖男声", gender="male", style="标准普通话，带部分北方口音；阳光、温暖、活力"),
    voice_option("Chelsie", "Chelsie - 千雪：二次元女声", gender="female", style="二次元虚拟女友风格；适合角色化表达"),
    voice_option("Momo", "Momo - 茉兔：撒娇搞怪女声", gender="female", style="撒娇搞怪，逗趣活泼；适合轻松内容"),
    voice_option("Vivian", "Vivian - 十三：可爱小暴躁女声", gender="female", style="拽拽的、可爱的小暴躁；适合鲜明角色"),
    voice_option("Moon", "Moon - 月白：率性帅气男声", gender="male", style="率性帅气；适合清爽叙事和角色对白"),
    voice_option("Maia", "Maia - 四月：知性温柔女声", gender="female", style="知性与温柔结合；适合旁白、课程和温和表达"),
    voice_option("Kai", "Kai - 凯：舒缓男声", gender="male", style="耳朵的一场 SPA；适合轻松陪伴和舒缓内容"),
    voice_option("Nofish", "Nofish - 不吃鱼：轻口音男声", gender="male", style="不会翘舌音的设计师；适合生活化表达"),
    voice_option("Bella", "Bella - 萌宝：萝莉女声", gender="female", style="可爱萝莉感；适合动画和轻萌内容"),
    voice_option("Eldric Sage", "Eldric Sage - 沧明子：沉稳老者男声", gender="male", style="沉稳睿智、沧桑如松；适合古风、长辈和旁白"),
    voice_option("Mia", "Mia - 乖小妹：乖巧女声", gender="female", style="温顺如春水，乖巧如初雪；适合温柔叙事"),
    voice_option("Mochi", "Mochi - 沙小弥：聪明少年男声", gender="male", style="聪明伶俐的小大人；适合儿童和少年角色"),
    voice_option("Bellona", "Bellona - 燕铮莺：洪亮女声", gender="female", style="声音洪亮、吐字清晰、人物鲜活；适合热血表达"),
    voice_option("Vincent", "Vincent - 田叔：沙哑男声", gender="male", style="独特沙哑烟嗓；适合江湖感、故事感旁白"),
    voice_option("Bunny", "Bunny - 萌小姬：超萌女声", gender="female", style="萌属性爆棚的小萝莉；适合可爱角色"),
    voice_option("Neil", "Neil - 阿闻：新闻男声", gender="male", style="平直基线语调、字正腔圆；适合新闻主持"),
    voice_option("Elias", "Elias - 墨讲师：知识讲解女声", gender="female", style="严谨中带叙事技巧；适合教育和复杂知识讲解"),
    voice_option("Arthur", "Arthur - 徐大爷：质朴男声", gender="male", style="质朴沧桑、不疾不徐；适合乡土故事和长辈叙述"),
    voice_option("Nini", "Nini - 邻家妹妹：软甜女声", gender="female", style="软糯甜美、邻家感；适合亲密陪伴和轻甜内容"),
    voice_option("Seren", "Seren - 小婉：舒缓助眠女声", gender="female", style="温和舒缓，适合助眠、晚安和疗愈内容"),
    voice_option("Pip", "Pip - 顽屁小孩：童真男声", gender="male", style="调皮捣蛋又童真；适合儿童角色"),
    voice_option("Stella", "Stella - 少女阿月：甜美少女女声", gender="female", style="甜美迷糊少女音，可表现正义感和戏剧张力"),
]

QWEN_TTS_FLASH_ONLY_VOICES = [
    voice_option("Jennifer", "Jennifer - 詹妮弗：电影质感美语女声", gender="female", style="品牌级、电影质感般美语女声"),
    voice_option("Ryan", "Ryan - 甜茶：戏感张力男声", gender="male", style="节奏拉满，戏感炸裂，真实与张力共舞"),
    voice_option("Katerina", "Katerina - 卡捷琳娜：御姐女声", gender="female", style="御姐音色，韵律回味十足"),
    voice_option("Aiden", "Aiden - 艾登：美语大男孩男声", gender="male", style="精通厨艺的美语大男孩；适合轻快英语内容"),
    voice_option("Bodega", "Bodega - 博德加：西班牙男声", gender="male", style="热情的西班牙大叔"),
    voice_option("Sonrisa", "Sonrisa - 索尼莎：拉美女声", gender="female", style="热情开朗的拉美大姐"),
    voice_option("Alek", "Alek - 阿列克：俄语男声", gender="male", style="战斗民族的冷与温暖；适合俄语风格内容"),
    voice_option("Dolce", "Dolce - 多尔切：意大利男声", gender="male", style="慵懒的意大利大叔"),
    voice_option("Sohee", "Sohee - 素熙：韩语女声", gender="female", style="温柔开朗、情绪丰富的韩国欧尼"),
    voice_option("Ono Anna", "Ono Anna - 小野杏：日语女声", gender="female", style="鬼灵精怪的青梅竹马"),
    voice_option("Lenn", "Lenn - 莱恩：德语男声", gender="male", style="理性带叛逆细节的德国青年"),
    voice_option("Emilien", "Emilien - 埃米尔安：法语男声", gender="male", style="浪漫的法国大哥哥"),
    voice_option("Andre", "Andre - 安德雷：磁性男声", gender="male", style="声音磁性，自然舒服、沉稳男生"),
    voice_option("Radio Gol", "Radio Gol - 拉迪奥·戈尔：足球解说男声", gender="male", style="足球诗人式解说，适合体育内容"),
    voice_option("Jada", "Jada - 上海-阿珍：上海话女声", gender="female", style="风风火火的沪上阿姐；支持上海话"),
    voice_option("Dylan", "Dylan - 北京-晓东：北京话男声", gender="male", style="北京胡同少年；支持北京话"),
    voice_option("Li", "Li - 南京-老李：南京话男声", gender="male", style="耐心的瑜伽老师；支持南京话"),
    voice_option("Marcus", "Marcus - 陕西-秦川：陕西话男声", gender="male", style="面宽话短、心实声沉；支持陕西话"),
    voice_option("Roy", "Roy - 闽南-阿杰：闽南语男声", gender="male", style="诙谐直爽、市井活泼；支持闽南语"),
    voice_option("Peter", "Peter - 天津-李彼得：天津话男声", gender="male", style="天津相声、专业捧哏；支持天津话"),
    voice_option("Sunny", "Sunny - 四川-晴儿：四川话女声", gender="female", style="甜到心里的川妹子；支持四川话"),
    voice_option("Eric", "Eric - 四川-程川：四川话男声", gender="male", style="跳脱市井的成都男子；支持四川话"),
    voice_option("Rocky", "Rocky - 粤语-阿强：粤语男声", gender="male", style="幽默风趣，在线陪聊；支持粤语"),
    voice_option("Kiki", "Kiki - 粤语-阿清：粤语女声", gender="female", style="甜美港妹闺蜜；支持粤语"),
]

QWEN_TTS_INSTRUCT_VOICES = QWEN_TTS_SHARED_VOICES
QWEN_TTS_FLASH_VOICES = QWEN_TTS_SHARED_VOICES + QWEN_TTS_FLASH_ONLY_VOICES

# Seed TTS public presets shown in setup dropdowns; account-specific voices still use custom_voice_id/ListSpeakers.
BYTEDANCE_TTS_VOICE_ROWS = [
    ("zh_female_tianmeitaozi_mars_bigtts", "甜美桃子", "female", "中文；火山大模型普通话女声"),
    ("zh_male_M392_conversation_wvae_bigtts", "M392 Conversation Male", "male", "中文；火山官方 HTTP 示例音色"),
    ("multi_female_maomao_conversation_wvae_bigtts", "Maomao / Diana / つき", "female", "多语种；火山大模型会话女声"),
    ("zh_female_popo_mars_bigtts", "婆婆", "female", "中文；角色扮演老年女声"),
    ("zh_female_kefunvsheng_mars_bigtts", "暖阳女声", "female", "中文；客服场景"),
    ("zh_female_qingxinnvsheng_mars_bigtts", "清新女声", "female", "中文；清新自然女声"),
    ("zh_female_shuangkuaisisi_moon_bigtts", "爽快思思 / Skye", "female", "中文；爽快明亮女声"),
    ("zh_male_wennuanahu_moon_bigtts", "温暖阿虎 / Alvin", "male", "中文；温暖男声"),
    ("zh_male_shaonianzixin_moon_bigtts", "少年梓辛 / Brayan", "male", "中文；少年男声"),
    ("zh_female_zhixingnvsheng_mars_bigtts", "知性女声", "female", "中文；知性旁白"),
    ("zh_male_qingshuangnanda_mars_bigtts", "清爽男大", "male", "中文；清爽青年男声"),
    ("zh_female_linjianvhai_moon_bigtts", "邻家女孩", "female", "中文；邻家女声"),
    ("zh_male_yuanboxiaoshu_moon_bigtts", "渊博小叔", "male", "中文；成熟讲解男声"),
    ("zh_male_yangguangqingnian_moon_bigtts", "阳光青年", "male", "中文；阳光青年男声"),
    ("zh_female_tianmeixiaoyuan_moon_bigtts", "甜美小源", "female", "中文；甜美女声"),
    ("zh_female_qingchezizi_moon_bigtts", "清澈梓梓", "female", "中文；清澈女声"),
    ("zh_male_jieshuoxiaoming_moon_bigtts", "解说小明", "male", "中文；解说男声"),
    ("zh_female_kailangjiejie_moon_bigtts", "开朗姐姐", "female", "中文；开朗女声"),
    ("zh_male_linjiananhai_moon_bigtts", "邻家男孩", "male", "中文；邻家男声"),
    ("zh_female_tianmeiyueyue_moon_bigtts", "甜美悦悦", "female", "中文；甜美年轻女声"),
    ("zh_female_xinlingjitang_moon_bigtts", "心灵鸡汤", "female", "中文；情感旁白女声"),
    ("zh_male_wenrouxiaoge_mars_bigtts", "温柔小哥", "male", "中文；温柔青年男声"),
    ("zh_male_jingqiangkanye_moon_bigtts", "京腔侃爷 / Harmony", "male", "中文；北京口音男声"),
    ("zh_female_wanwanxiaohe_moon_bigtts", "湾湾小何", "female", "中文；台湾口音女声"),
    ("zh_female_wanqudashu_moon_bigtts", "湾区大叔", "female", "中文；湾区口音"),
    ("zh_female_daimengchuanmei_moon_bigtts", "呆萌川妹", "female", "中文；四川口音女声"),
    ("zh_male_guozhoudege_moon_bigtts", "广州德哥", "male", "中文；粤语/广州口音男声"),
    ("zh_male_beijingxiaoye_moon_bigtts", "北京小爷", "male", "中文；北京口音男声"),
    ("zh_male_haoyuxiaoge_moon_bigtts", "浩宇小哥", "male", "中文；青年男声"),
    ("zh_male_guangxiyuanzhou_moon_bigtts", "广西远舟", "male", "中文；广西口音男声"),
    ("zh_female_meituojieer_moon_bigtts", "妹坨洁儿", "female", "中文；方言女声"),
    ("zh_male_yuzhouzixuan_moon_bigtts", "豫州子轩", "male", "中文；河南口音男声"),
    ("zh_male_naiqimengwa_mars_bigtts", "奶气萌娃", "male", "中文；儿童角色"),
    ("zh_female_gaolengyujie_moon_bigtts", "高冷御姐", "female", "中文；高冷御姐"),
    ("zh_male_aojiaobazong_moon_bigtts", "傲娇霸总", "male", "中文；角色男声"),
    ("zh_female_meilinvyou_moon_bigtts", "魅力女友", "female", "中文；魅力女声"),
    ("zh_male_shenyeboke_moon_bigtts", "深夜播客", "male", "中文；低沉播客男声"),
    ("zh_female_sajiaonvyou_moon_bigtts", "柔美女友", "female", "中文；柔美女声"),
    ("zh_female_yuanqinvyou_moon_bigtts", "撒娇学妹", "female", "中文；元气女声"),
    ("zh_male_dongfanghaoran_moon_bigtts", "东方浩然", "male", "中文；古风男声"),
    ("zh_female_wenrouxiaoya_moon_bigtts", "温柔小雅", "female", "中文；温柔女声"),
    ("zh_male_tiancaitongsheng_mars_bigtts", "天才童声", "male", "中文；儿童声"),
    ("zh_male_sunwukong_mars_bigtts", "猴哥", "male", "中文；角色声"),
    ("zh_male_xionger_mars_bigtts", "熊二", "male", "中文；角色声"),
    ("zh_female_peiqi_mars_bigtts", "佩奇猪", "female", "中文；角色声"),
    ("zh_female_wuzetian_mars_bigtts", "武则天", "female", "中文；历史角色女声"),
    ("zh_female_gujie_mars_bigtts", "顾姐", "female", "中文；成熟女声"),
    ("zh_female_yingtaowanzi_mars_bigtts", "樱桃丸子", "female", "中文；动画角色女声"),
    ("zh_male_chunhui_mars_bigtts", "春晖", "male", "中文；男声"),
    ("zh_female_shaoergushi_mars_bigtts", "少儿故事", "female", "中文；少儿故事女声"),
    ("zh_male_silang_mars_bigtts", "四郎", "male", "中文；角色男声"),
    ("zh_male_jieshuonansheng_mars_bigtts", "磁性解说男声 / Morgan", "male", "中文；磁性解说男声"),
    ("zh_female_jitangmeimei_mars_bigtts", "鸡汤妹妹 / Hope", "female", "中文；情感女声"),
    ("zh_female_tiexinnvsheng_mars_bigtts", "贴心女声 / Candy", "female", "中文；贴心女声"),
    ("zh_female_qiaopinvsheng_mars_bigtts", "俏皮女声", "female", "中文；俏皮女声"),
    ("zh_female_mengyatou_mars_bigtts", "萌丫头 / Cutey", "female", "中文；萌系女声"),
    ("zh_male_lanxiaoyang_mars_bigtts", "懒音绵宝", "male", "中文；慵懒男声"),
    ("zh_male_dongmanhaimian_mars_bigtts", "亮嗓萌仔", "male", "中文；动画男声"),
    ("zh_male_changtianyi_mars_bigtts", "悬疑解说", "male", "中文；悬疑解说男声"),
    ("zh_male_ruyaqingnian_mars_bigtts", "儒雅青年", "male", "中文；儒雅青年男声"),
    ("zh_male_baqiqingshu_mars_bigtts", "霸气青叔", "male", "中文；霸气成熟男声"),
    ("zh_male_qingcang_mars_bigtts", "擎苍", "male", "中文；有声阅读男声"),
    ("zh_male_yangguangqingnian_mars_bigtts", "活力小哥", "male", "中文；活力青年男声"),
    ("zh_female_gufengshaoyu_mars_bigtts", "古风少御", "female", "中文；古风女声"),
    ("zh_female_wenroushunv_mars_bigtts", "温柔淑女", "female", "中文；温柔女声"),
    ("zh_male_fanjuanqingnian_mars_bigtts", "反卷青年", "male", "中文；青年男声"),
    ("zh_male_beijingxiaoye_emo_v2_mars_bigtts", "北京小爷（多情感）", "male", "中文；多情感男声"),
    ("zh_female_roumeinvyou_emo_v2_mars_bigtts", "柔美女友（多情感）", "female", "中文；多情感女声"),
    ("zh_male_yangguangqingnian_emo_v2_mars_bigtts", "阳光青年（多情感）", "male", "中文；多情感男声"),
    ("zh_female_meilinvyou_emo_v2_mars_bigtts", "魅力女友（多情感）", "female", "中文；多情感女声"),
    ("zh_female_shuangkuaisisi_emo_v2_mars_bigtts", "爽快思思（多情感）", "female", "中文；多情感女声"),
    ("zh_female_cancan_mars_bigtts", "灿灿 / Shiny", "female", "中文；明亮女声"),
    ("ICL_zh_female_zhixingwenwan_tob", "知性温婉", "female", "中文；角色音色"),
    ("ICL_zh_male_nuanxintitie_tob", "暖心体贴", "male", "中文；角色音色"),
    ("ICL_zh_female_wenrouwenya_tob", "温柔文雅", "female", "中文；角色音色"),
    ("ICL_zh_male_kailangqingkuai_tob", "开朗轻快", "male", "中文；角色音色"),
    ("ICL_zh_male_huoposhuanglang_tob", "活泼爽朗", "male", "中文；角色音色"),
    ("ICL_zh_male_shuaizhenxiaohuo_tob", "率真小伙", "male", "中文；角色音色"),
    ("ICL_zh_female_bingruoshaonv_tob", "病弱少女", "female", "中文；角色音色"),
    ("ICL_zh_female_huoponvhai_tob", "活泼女孩", "female", "中文；角色音色"),
    ("ICL_zh_male_lvchaxiaoge_tob", "绿茶小哥", "male", "中文；角色音色"),
    ("ICL_zh_female_jiaoruoluoli_tob", "娇弱萝莉", "female", "中文；角色音色"),
    ("ICL_zh_male_lengdanshuli_tob", "冷淡疏离", "male", "中文；角色音色"),
    ("ICL_zh_male_hanhoudunshi_tob", "憨厚敦实", "male", "中文；角色音色"),
    ("ICL_zh_male_aiqilingren_tob", "傲气凌人", "male", "中文；角色音色"),
    ("ICL_zh_female_huopodiaoman_tob", "活泼刁蛮", "female", "中文；角色音色"),
    ("ICL_zh_male_guzhibingjiao_tob", "固执病娇", "male", "中文；角色音色"),
    ("ICL_zh_male_sajiaonianren_tob", "撒娇粘人", "male", "中文；角色音色"),
    ("ICL_zh_female_aomanjiaosheng_tob", "傲慢娇声", "female", "中文；角色音色"),
    ("ICL_zh_male_xiaosasuixing_tob", "潇洒随性", "male", "中文；角色音色"),
    ("ICL_zh_male_fuheigongzi_tob", "腹黑公子", "male", "中文；角色音色"),
    ("ICL_zh_male_guiyishenmi_tob", "诡异神秘", "male", "中文；角色音色"),
    ("ICL_zh_male_ruyacaijun_tob", "儒雅才俊", "male", "中文；角色音色"),
    ("ICL_zh_male_bingjiaobailian_tob", "病娇白莲", "male", "中文；角色音色"),
    ("ICL_zh_male_zhengzhiqingnian_tob", "正直青年", "male", "中文；角色音色"),
    ("ICL_zh_female_jiaohannvwang_tob", "娇憨女王", "female", "中文；角色音色"),
    ("ICL_zh_female_bingjiaomengmei_tob", "病娇萌妹", "female", "中文；角色音色"),
    ("ICL_zh_male_qingsenaigou_tob", "青涩小生", "male", "中文；角色音色"),
    ("chunzhen_xuedi", "纯真学弟", "male", "中文；角色音色"),
    ("ICL_zh_female_nuanxinxuejie_tob", "暖心学姐", "female", "中文；角色音色"),
    ("ICL_zh_female_keainvsheng_tob", "可爱女生", "female", "中文；角色音色"),
    ("ICL_zh_female_chengshujiejie_tob", "成熟姐姐", "female", "中文；角色音色"),
    ("ICL_zh_female_bingjiaojiejie_tob", "病娇姐姐", "female", "中文；角色音色"),
    ("ICL_zh_male_youroubangzhu_tob", "优柔帮主", "male", "中文；角色音色"),
    ("ICL_zh_male_yourougongzi_tob", "优柔公子", "male", "中文；角色音色"),
    ("wumei_yujie", "妩媚御姐", "female", "中文；角色音色"),
    ("ICL_zh_female_tiaopigongzhu_tob", "调皮公主", "female", "中文；角色音色"),
    ("ICL_zh_female_aojiaonvyou_tob", "傲娇女友", "female", "中文；角色音色"),
    ("ICL_zh_male_tiexinnanyou_tob", "贴心男友", "male", "中文；角色音色"),
    ("ICL_zh_male_shaonianjiangjun_tob", "少年将军", "male", "中文；角色音色"),
    ("ICL_zh_female_tiexinnvyou_tob", "贴心女友", "female", "中文；角色音色"),
    ("ICL_zh_male_bingjiaogege_tob", "病娇哥哥", "male", "中文；角色音色"),
    ("ICL_zh_male_xuebanantongzhuo_tob", "学霸男同桌", "male", "中文；角色音色"),
    ("ICL_zh_male_youmoshushu_tob", "幽默叔叔", "male", "中文；角色音色"),
    ("ICL_zh_female_xingganyujie_tob", "性感御姐", "female", "中文；角色音色"),
    ("ICL_zh_female_jiaxiaozi_tob", "假小子", "female", "中文；角色音色"),
    ("ICL_zh_male_lengjunshangsi_tob", "冷峻上司", "male", "中文；角色音色"),
    ("ICL_zh_male_wenrounantongzhuo_tob", "温柔男同桌", "male", "中文；角色音色"),
    ("bingjiao_didi", "病娇弟弟", "male", "中文；角色音色"),
    ("ICL_zh_male_youmodaye_tob", "幽默大爷", "male", "中文；角色音色"),
    ("ICL_zh_male_aomanshaoye_tob", "傲慢少爷", "male", "中文；角色音色"),
    ("ICL_zh_male_shenmifashi_tob", "神秘法师", "male", "中文；角色音色"),
    ("ICL_zh_female_heainainai_tob", "和蔼奶奶", "female", "中文；角色音色"),
    ("ICL_zh_female_linjuayi_tob", "邻居阿姨", "female", "中文；角色音色"),
    ("multi_male_jingqiangkanye_moon_bigtts", "かずね（和音）/ Javier", "male", "日语/多语种；角色男声"),
    ("multi_female_shuangkuaisisi_moon_bigtts", "はるこ（晴子）/ Esmeralda", "female", "日语/多语种；女声"),
    ("multi_male_wanqudashu_moon_bigtts", "ひろし（広志）/ Roberto", "male", "日语/多语种；男声"),
    ("multi_female_gaolengyujie_moon_bigtts", "あけみ（朱美）", "female", "日语/多语种；女声"),
    ("multi_zh_male_youyoujunzi_moon_bigtts", "ひかる（光）", "male", "日语/多语种；男声"),
    ("multi_female_sophie_conversation_wvae_bigtts", "さとみ（智美）/ Sophie", "female", "日语/多语种；会话女声"),
    ("multi_male_xudong_conversation_wvae_bigtts", "まさお（正男）", "male", "日语/多语种；会话男声"),
]

BYTEDANCE_TTS_VOICES = [
    voice_option(voice_id, label, gender=gender, style=style)
    for voice_id, label, gender, style in BYTEDANCE_TTS_VOICE_ROWS
] + [
    voice_option("custom_voice_id", "Custom voice_type", mode="custom_voice_id", style="Paste a Volcano voice_type from your account or ListSpeakers"),
]


def request_json(url: str, api_key: str, timeout: int = 12, proxy_policy: str = "direct") -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"})
    try:
        with provider_urlopen(req, timeout=timeout, proxy_policy=proxy_policy) as res:
            body = res.read().decode("utf-8", errors="replace")
            try:
                return {"status": int(res.status), "body": json.loads(body) if body else {}}
            except json.JSONDecodeError:
                return {"status": int(res.status), "body": body[:1000]}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2000]
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(str(exc.reason)) from exc


def post_json(url: str, api_key: str, payload: dict[str, Any], extra_headers: dict[str, str] | None = None, timeout: int = 12, proxy_policy: str = "direct") -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        **(extra_headers or {}),
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with provider_urlopen(req, timeout=timeout, proxy_policy=proxy_policy) as res:
            body = res.read().decode("utf-8", errors="replace")
            try:
                parsed: Any = json.loads(body) if body else {}
            except json.JSONDecodeError:
                parsed = body[:1000]
            return {"status": int(res.status), "body": parsed}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2000]
        try:
            body: Any = json.loads(detail) if detail else {}
        except json.JSONDecodeError:
            body = detail
        return {"status": int(exc.code), "body": body, "error": detail}
    except urllib.error.URLError as exc:
        raise RuntimeError(str(exc.reason)) from exc


def post_binary(url: str, api_key: str, payload: dict[str, Any], extra_headers: dict[str, str] | None = None, timeout: int = 30, proxy_policy: str = "direct") -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "*/*",
        **(extra_headers or {}),
    }
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with provider_urlopen(req, timeout=timeout, proxy_policy=proxy_policy) as res:
            return {
                "status": int(res.status),
                "content_type": res.headers.get("Content-Type") or "application/octet-stream",
                "body": res.read(),
            }
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2000]
        return {"status": int(exc.code), "content_type": exc.headers.get("Content-Type") or "", "body": detail.encode("utf-8"), "error": detail}
    except urllib.error.URLError as exc:
        raise RuntimeError(str(exc.reason)) from exc


def body_text(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False) if isinstance(payload, (dict, list)) else str(payload)


def parse_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    text_value = str(value or "").strip()
    if not text_value:
        return {}
    try:
        payload = json.loads(text_value)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def normalize_video_audio_extra(kind: str, provider: str, model: str, extra: dict[str, Any]) -> dict[str, Any]:
    if str(kind or "").strip().lower() != "video":
        return extra
    normalized = dict(extra)
    provider_value = str(provider or "").strip().lower()
    model_value = str(model or "").strip().lower()
    if provider_value in {"kling", "klingai", "kling-ai"} or "kling" in model_value:
        normalized["sound"] = "on"
    if provider_value in {"bytedance", "seedance", "volcengine", "ark"} or ("seedance" in model_value and provider_value != "openrouter"):
        normalized["generate_audio"] = True
    return normalized


def media_options(kind: str) -> list[dict[str, Any]]:
    options: dict[str, list[dict[str, Any]]] = {
        "image": [
            {
                "provider": "openai",
                "provider_label": "OpenAI",
                "description": "GPT Image models for generation and editing workflows.",
                "docs_url": "https://developers.openai.com/cookbook/examples/multimodal/image-gen-models-prompting-guide#1-introduction",
                "models": [
                    model_option("gpt-image-2"),
                    model_option("gpt-image-1.5"),
                ],
            },
            {
                "provider": "xai",
                "provider_label": "xAI",
                "description": "Grok Imagine image models for text/image-to-image workflows.",
                "docs_url": "https://docs.x.ai/developers/models/grok-imagine-image-quality",
                "models": [
                    model_option("grok-imagine-image-quality", description="Recommended xAI image quality model for new generation and editing requests."),
                    model_option("grok-imagine-image", description="Standard Grok Imagine Image model; lower-cost 1K/2K image output."),
                ],
            },
            {
                "provider": "gemini",
                "provider_label": "Gemini",
                "description": "Gemini and Imagen models for image generation workflows.",
                "docs_url": "https://ai.google.dev/gemini-api/docs/image-generation?hl=zh-cn",
                "models": [
                    model_option("gemini-3.1-flash-image"),
                    model_option("gemini-3-pro-image"),
                    model_option("gemini-2.5-flash-image"),
                ],
            },
        ],
        "video": [
            {
                "provider": "openai",
                "provider_label": "OpenAI",
                "description": "Sora video generation models.",
                "docs_url": "https://developers.openai.com/cookbook/topic/multimodal",
                "models": [
                    model_option("sora-2", input_modes=["text", "first_frame"], duration={"adjustable": True, "min": 4, "max": 20, "allowed": [], "note": "支持时长调整；建议 4-20 秒。"}),
                    model_option("sora-2-pro", input_modes=["text", "first_frame"], duration={"adjustable": True, "min": 4, "max": 20, "allowed": [], "note": "支持时长调整；建议 4-20 秒。"}),
                ],
            },
            {
                "provider": "xai",
                "provider_label": "xAI",
                "description": "Grok Imagine video models for text/image-to-video workflows.",
                "docs_url": "https://docs.x.ai/developers/models/grok-imagine-video-1.5-preview",
                "models": [
                    model_option(
                        "grok-imagine-video-1.5-preview",
                        description="Latest xAI video preview model; supports image-to-video only.",
                        input_modes=["first_frame"],
                        duration={"adjustable": True, "min": 1, "max": 15, "allowed": [], "note": "仅支持首帧图生视频；Quality X 1.5 默认使用 1080p。"},
                        reference_images={"supported": True, "min": 1, "max": 1, "mode": "first_frame", "note": "xAI 模型页标注 grok-imagine-video-1.5 currently does not support text-to-video；必须选择 1 张图片作为首帧。"},
                    ),
                    model_option(
                        "grok-imagine-video",
                        description="Current stable Grok Imagine video model; supports text, image, and video inputs.",
                        input_modes=["text", "first_frame", "multi_reference"],
                        duration={"adjustable": True, "min": 1, "max": 15, "allowed": [], "note": "支持文生视频/首帧生视频；多图参考模式最长 10 秒。"},
                        reference_images={"supported": True, "min": 0, "max": 7, "mode": "reference_images", "note": "支持纯文生视频；也可选 1-7 张参考图，不是严格首尾帧约束。"},
                    )
                ],
            },
            {
                "provider": "gemini",
                "provider_label": "Gemini",
                "description": "Veo models exposed through the Gemini API.",
                "docs_url": "https://ai.google.dev/gemini-api/docs/video?hl=zh-cn&example=dialogue",
                "models": [
                    model_option("veo-3.1-generate-preview", input_modes=["text", "first_frame", "first_last"], duration={"adjustable": True, "min": 4, "max": 8, "allowed": [4, 6, 8], "note": "支持 4/6/8 秒等短视频时长。"}),
                    model_option("veo-3.1-fast-generate-preview", input_modes=["text", "first_frame", "first_last"], duration={"adjustable": True, "min": 4, "max": 8, "allowed": [4, 6, 8], "note": "支持 4/6/8 秒等短视频时长。"}),
                    model_option("veo-3.1-lite-generate-preview", input_modes=["text", "first_frame"], duration={"adjustable": True, "min": 4, "max": 8, "allowed": [4, 6, 8], "note": "Veo 3.1 Lite Preview；支持 4/6/8 秒，1080p 需 8 秒。"}),
                    model_option("veo-3.0-generate-001", input_modes=["text", "first_frame"], duration={"adjustable": True, "min": 4, "max": 8, "allowed": [4, 6, 8], "note": "Veo 3 正式模型；支持 4/6/8 秒，1080p/4k 需 8 秒。"}),
                    model_option("veo-3.0-fast-generate-001", input_modes=["text", "first_frame"], duration={"adjustable": True, "min": 4, "max": 8, "allowed": [4, 6, 8], "note": "Veo 3 Fast 正式模型；更适合快速生成和批量创意测试。"}),
                    *(
                        [
                            model_option(
                                "gemini-omni-flash-preview",
                                label="Gemini Omni Flash Preview",
                                description="Preview Interactions API model for paid text/image/video generation and stateful video editing.",
                                input_modes=["text", "first_frame", "multi_reference", "video_reference"],
                                tasks=["text_to_video", "image_to_video", "reference_to_video", "edit"],
                                stateful_edit=True,
                                provider_state="interaction",
                                supports_video_input=True,
                                supports_audio_input=False,
                                output_modalities=["video"],
                                aspect_ratios=["16:9", "9:16"],
                                duration={"adjustable": False, "min": 3, "max": 3, "allowed": [3], "note": "当前 Preview 最短输出为 3 秒；OpenCrew 首期固定最短时长以控制付费和回归范围。"},
                                reference_images={"supported": True, "min": 0, "max": 8, "mode": "file_or_inline"},
                                reference_videos={"supported": True, "min": 0, "max": 1, "mode": "files_api"},
                                audio_input={"supported": False, "recommended": False, "mode": "unsupported"},
                            )
                        ]
                        if gemini_omni_feature_enabled()
                        else []
                    ),
                ],
            },
            {
                "provider": "bytedance",
                "provider_label": "ByteDance Volcano Ark",
                "description": "Volcano Ark Seedance video generation models for CN deployment.",
                "docs_url": "https://www.volcengine.com/docs/82379/1520757?lang=zh",
                "default_extra_json": {
                    "base_url": "https://ark.cn-beijing.volces.com/api/v3",
                    "region": "cn-beijing",
                    "default_ratio": "9:16",
                    "default_resolution": "720p",
                    "generate_audio": True,
                    "docs_url": "https://www.volcengine.com/docs/82379/1520757?lang=zh",
                },
                "models": [
                    model_option(
                        "doubao-seedance-2-0-fast-260128",
                        label="Doubao Seedance 2.0 Fast",
                        description="Seedance 2.0 Fast on Volcano Ark; MVP uses text-to-video or first-frame image-to-video and requests provider audio when supported.",
                        input_modes=["text", "first_frame"],
                        duration={"adjustable": True, "min": 4, "max": 15, "allowed": [], "note": "国内火山默认接入；MVP 建议 5 秒、720p、generate_audio=true。"},
                        audio_input={"supported": True, "recommended": True, "mode": "provider_audio", "note": "默认请求模型输出有声视频；普通视频生成不会继承旧的静音默认。"},
                        reference_images={"supported": True, "min": 0, "max": 1, "mode": "first_frame", "note": "可纯文生视频；有参考图时 MVP 使用 base64 data URL 传首帧，真人参考素材需遵守 Seedance 合规限制。"},
                    ),
                ],
            },
            {
                "provider": "kling",
                "provider_label": "Kling",
                "description": "KlingAI Open Platform video models for first-frame and omni reference workflows.",
                "docs_url": "https://klingai.com/document-api/guides/capability-map/video",
                "default_extra_json": {
                    "base_url": "https://api-beijing.klingai.com",
                    "default_aspect_ratio": "9:16",
                    "default_resolution": "1080p",
                    "default_mode": "pro",
                    "sound": "on",
                    "watermark_enabled": False,
                    "docs_url": "https://klingai.com/document-api/guides/capability-map/video",
                    "public_asset_provider": "tmpfiles",
                    "tmpfiles_upload_url": "https://tmpfiles.org/api/v1/upload",
                    "tmpfiles_expire_seconds": 21600,
                    "public_asset_ttl_seconds": 21600,
                    "public_asset_prefix": "tmp/kling-reference-videos",
                    "reference_video_public_url": "",
                    "reference_video_max_seconds": 10,
                    "r2_access_key_ref": "public_assets_r2_access_key_id",
                    "r2_secret_access_key_ref": "public_assets_r2_secret_access_key",
                    "public_asset_note": "Kling Omni video_list.video_url 不能传本地路径；当前默认 tmpfiles 仅适合测试，生产建议接 OSS/S3/CDN。",
                },
                "models": [
                    model_option(
                        "kling-3.0-turbo",
                        label="Kling 3.0 Turbo",
                        description="Kling 3.0 Turbo image-to-video endpoint; fast 3-15s clips with prompt plus optional first frame.",
                        input_modes=["text", "first_frame"],
                        capabilities=["Text to Video", "Image to Video", "3-15s", "720p/1080p"],
                        duration={"adjustable": True, "min": 3, "max": 15, "allowed": [], "note": "支持 3-15 秒；OpenCrew 默认 1080p；官方文档标注仅首帧，暂不支持首尾帧或仅尾帧。"},
                        reference_images={"supported": True, "min": 0, "max": 1, "mode": "first_frame", "note": "contents[].type=first_frame；支持 URL 或 base64，jpg/jpeg/png，<=50MB，比例 1:2.5 到 2.5:1。"},
                        audio_input={"supported": True, "recommended": True, "mode": "provider_audio", "note": "默认请求模型输出有声视频；仅 Kling Omni 带 video_list 视频参考时才按官方限制强制 sound=off。"},
                    ),
                    model_option(
                        "kling-v3-omni",
                        label="Kling 3.0 Omni",
                        description="Kling 3.0 Omni omni-video route; supports first-frame identity lock, multi-modal references, and up to 4K modes.",
                        input_modes=["text", "first_frame", "first_last", "multi_reference", "video_reference"],
                        capabilities=["Omni", "Video Reference", "Multi-shot", "3-15s", "720p/1080p/4K"],
                        duration={"adjustable": True, "min": 3, "max": 15, "allowed": [], "note": "支持 3-15 秒；OpenCrew 默认 pro/1080p；带 video_list 视频参考时可灵要求 sound=off。"},
                        reference_images={"supported": True, "min": 0, "max": 1, "mode": "first_frame", "note": "可纯文生视频；有参考图时 Omni 调用使用 image_list[].type=first_frame 锁定首帧，URL 或 base64。"},
                        reference_videos={"supported": True, "min": 0, "max": 1, "mode": "feature", "note": "可用 video_list[].refer_type=feature 做特征参考；有视频参考时可灵不支持 sound=on，必须 sound=off。"},
                        audio_input={"supported": True, "recommended": False, "mode": "provider_voice", "note": "支持 voice_list 与 <<<voice_1>>>；当前 OpenCrew 的 Omni 视频参考链路使用 video_list，因此仅该链路按可灵限制关闭 sound。"},
                    ),
                ],
            },
            {
                "provider": "chanjing",
                "provider_label": "蝉镜",
                "description": "蝉镜 OpenAPI 视频生成模型，使用 APP Key / API Key 鉴权，支持上传首帧后提交异步创作任务。",
                "docs_url": "https://doc.chanjing.cc/api/open-api-document.html",
                "default_extra_json": {
                    "base_url": "https://open-api.chanjing.cc",
                    "access_token_path": "/open/v1/access_token",
                    "upload_path": "/open/v1/file/upload",
                    "submit_path": "/open/v1/ai_creation/task/submit",
                    "query_path": "/open/v1/ai_creation/task",
                    "creation_type": 4,
                    "default_aspect_ratio": "9:16",
                    "default_clarity": 1080,
                    "default_quality_mode": "pro",
                    "default_duration_seconds": 10,
                    "allowed_duration_seconds": [5, 6, 10],
                    "credential_format": "JSON with app_key and api_key",
                    "docs_url": "https://doc.chanjing.cc/api/open-api-document.html",
                },
                "models": [
                    model_option(
                        "kling2.5",
                        label="Kling 2.5",
                        description="蝉镜 OpenAPI model_code=kling2.5；适合首帧图生视频，支持 1080、9:16、5/6/10 秒。",
                        input_modes=["text", "first_frame"],
                        capabilities=["Image to Video", "Text to Video", "5/6/10s", "1080"],
                        duration={"adjustable": True, "min": 5, "max": 10, "allowed": [5, 6, 10], "note": "蝉镜 OpenAPI 枚举支持 5/6/10 秒；我们实测 10 秒链路稳定，6 秒任务可能因模型执行失败需裁切兜底。"},
                        audio_input={"supported": True, "recommended": True, "mode": "provider_audio", "note": "蝉镜 Kling 视频默认不发送静音参数，按模型默认生成有声视频。"},
                        reference_images={"supported": True, "min": 0, "max": 1, "mode": "start_frame", "note": "通过 start_frame 传首帧文件路径；文件需先上传到蝉镜文件管理。"},
                    ),
                    model_option(
                        "kling-v2-1-master",
                        label="Kling v2.1 Master",
                        description="蝉镜 OpenAPI model_code=kling-v2-1-master；可灵 2.1 Master 视频模型。",
                        input_modes=["text", "first_frame"],
                        capabilities=["Image to Video", "Text to Video", "5/6/10s"],
                        duration={"adjustable": True, "min": 5, "max": 10, "allowed": [5, 6, 10], "note": "蝉镜 OpenAPI 视频任务时长枚举为 5/6/10 秒。"},
                        audio_input={"supported": True, "recommended": True, "mode": "provider_audio", "note": "蝉镜 Kling 视频默认不发送静音参数，按模型默认生成有声视频。"},
                        reference_images={"supported": True, "min": 0, "max": 1, "mode": "start_frame", "note": "使用 start_frame 作为首帧。"},
                    ),
                    model_option(
                        "kling1.6",
                        label="Kling 1.6",
                        description="蝉镜 OpenAPI model_code=kling1.6；可灵 1.6 视频模型。",
                        input_modes=["text", "first_frame"],
                        capabilities=["Image to Video", "Text to Video", "5/6/10s"],
                        duration={"adjustable": True, "min": 5, "max": 10, "allowed": [5, 6, 10], "note": "蝉镜 OpenAPI 视频任务时长枚举为 5/6/10 秒。"},
                        audio_input={"supported": True, "recommended": True, "mode": "provider_audio", "note": "蝉镜 Kling 视频默认不发送静音参数，按模型默认生成有声视频。"},
                        reference_images={"supported": True, "min": 0, "max": 1, "mode": "start_frame", "note": "使用 start_frame 作为首帧。"},
                    ),
                    model_option(
                        "MiniMax-Hailuo-02",
                        label="MiniMax Hailuo 02",
                        description="蝉镜 OpenAPI model_code=MiniMax-Hailuo-02；海螺 02 视频模型。",
                        input_modes=["text", "first_frame"],
                        capabilities=["Image to Video", "Text to Video", "5/6/10s"],
                        duration={"adjustable": True, "min": 5, "max": 10, "allowed": [5, 6, 10], "note": "蝉镜 OpenAPI 视频任务时长枚举为 5/6/10 秒。"},
                        reference_images={"supported": True, "min": 0, "max": 1, "mode": "start_frame", "note": "使用 start_frame 作为首帧。"},
                    ),
                    model_option(
                        "Doubao-Seedance-1.0-pro",
                        label="Doubao Seedance 1.0 Pro",
                        description="蝉镜 OpenAPI model_code=Doubao-Seedance-1.0-pro；豆包 Seedance 1.0 Pro 视频模型。",
                        input_modes=["text", "first_frame"],
                        capabilities=["Image to Video", "Text to Video", "5/6/10s"],
                        duration={"adjustable": True, "min": 5, "max": 10, "allowed": [5, 6, 10], "note": "蝉镜 OpenAPI 视频任务时长枚举为 5/6/10 秒。"},
                        reference_images={"supported": True, "min": 0, "max": 1, "mode": "start_frame", "note": "使用 start_frame 作为首帧。"},
                    ),
                    model_option(
                        "Doubao-Seedance-1.0-lite-i2v",
                        label="Doubao Seedance 1.0 Lite I2V",
                        description="蝉镜 OpenAPI model_code=Doubao-Seedance-1.0-lite-i2v；豆包轻量图生视频模型。",
                        input_modes=["first_frame"],
                        capabilities=["Image to Video", "5/6/10s"],
                        duration={"adjustable": True, "min": 5, "max": 10, "allowed": [5, 6, 10], "note": "蝉镜 OpenAPI 视频任务时长枚举为 5/6/10 秒。"},
                        reference_images={"supported": True, "min": 1, "max": 1, "mode": "start_frame", "note": "使用 start_frame 作为首帧。"},
                    ),
                    model_option(
                        "happyhorse-1.0-t2v",
                        label="HappyHorse 1.0 T2V",
                        description="蝉镜 OpenAPI model_code=happyhorse-1.0-t2v；快乐马文生视频模型。",
                        input_modes=["text"],
                        capabilities=["Text to Video", "5/6/10s"],
                        duration={"adjustable": True, "min": 5, "max": 10, "allowed": [5, 6, 10], "note": "蝉镜 OpenAPI 视频任务时长枚举为 5/6/10 秒。"},
                    ),
                    model_option(
                        "happyhorse-1.0-i2v",
                        label="HappyHorse 1.0 I2V",
                        description="蝉镜 OpenAPI model_code=happyhorse-1.0-i2v；快乐马图生视频模型。",
                        input_modes=["first_frame"],
                        capabilities=["Image to Video", "5/6/10s"],
                        duration={"adjustable": True, "min": 5, "max": 10, "allowed": [5, 6, 10], "note": "蝉镜 OpenAPI 视频任务时长枚举为 5/6/10 秒。"},
                        reference_images={"supported": True, "min": 1, "max": 1, "mode": "start_frame", "note": "使用 start_frame 作为首帧。"},
                    ),
                    model_option(
                        "happyhorse-1.0-r2v",
                        label="HappyHorse 1.0 R2V",
                        description="蝉镜 OpenAPI model_code=happyhorse-1.0-r2v；快乐马参考生视频模型。",
                        input_modes=["first_frame", "multi_reference"],
                        capabilities=["Reference to Video", "Image to Video", "5/6/10s"],
                        duration={"adjustable": True, "min": 5, "max": 10, "allowed": [5, 6, 10], "note": "蝉镜 OpenAPI 视频任务时长枚举为 5/6/10 秒。"},
                        reference_images={"supported": True, "min": 1, "max": 3, "mode": "ref_img_url/start_frame", "note": "可结合首帧和参考图，具体以蝉镜模型页能力为准。"},
                    ),
                    model_option(
                        "happyhorse-1.0-video-edit",
                        label="HappyHorse 1.0 Video Edit",
                        description="蝉镜 OpenAPI model_code=happyhorse-1.0-video-edit；快乐马视频编辑模型。",
                        input_modes=["video_reference", "text"],
                        capabilities=["Video Edit", "Reference Video", "5/6/10s"],
                        duration={"adjustable": True, "min": 5, "max": 10, "allowed": [5, 6, 10], "note": "蝉镜 OpenAPI 视频任务时长枚举为 5/6/10 秒。"},
                    ),
                    model_option(
                        "viduq1",
                        label="Vidu Q1",
                        description="蝉镜 OpenAPI model_code=viduq1；视频生成模型。",
                        input_modes=["text", "first_frame"],
                        capabilities=["Image to Video", "Text to Video", "5/6/10s"],
                        duration={"adjustable": True, "min": 5, "max": 10, "allowed": [5, 6, 10], "note": "蝉镜 OpenAPI 视频任务时长枚举为 5/6/10 秒。"},
                        reference_images={"supported": True, "min": 0, "max": 1, "mode": "start_frame", "note": "使用 start_frame 作为首帧。"},
                    ),
                ],
            },
            {
                "provider": "openrouter",
                "provider_label": "OpenRouter",
                "description": "OpenRouter normalized video API, including ByteDance Seedance models. Uses OpenRouter credits and API keys.",
                "docs_url": "https://openrouter.ai/docs/cookbook/video-generation/choose-video-model",
                "default_extra_json": {
                    "base_url": "https://openrouter.ai/api/v1",
                    "default_aspect_ratio": "9:16",
                    "default_resolution": "720p",
                    "send_frame_images": True,
                    "public_asset_provider": "",
                    "public_asset_ttl_seconds": 3600,
                    "r2_access_key_ref": "public_assets_r2_access_key_id",
                    "r2_secret_access_key_ref": "public_assets_r2_secret_access_key",
                    "note": "OpenCrew sends legacy image-to-video as OpenRouter frame_images[first_frame]. Max SR2 / reference_mode=input_references sends image/audio/video URLs through OpenRouter input_references. If R2 endpoint/bucket are configured, it sends presigned HTTPS URLs; otherwise it falls back to data URLs for small files.",
                },
                "models": [
                    model_option(
                        "bytedance/seedance-2.0-fast",
                        label="ByteDance Seedance 2.0 Fast",
                        description="Fast and lower-cost Seedance 2.0 route via OpenRouter; supports text-to-video and first-frame image-to-video.",
                        input_modes=["text", "first_frame"],
                        duration={"adjustable": True, "min": 4, "max": 15, "allowed": [], "note": "OpenRouter video generation is paid; default 5s, 720p, 9:16."},
                        reference_images={"supported": True, "min": 0, "max": 1, "mode": "first_frame", "note": "OpenCrew supports text-to-video with no references; when an image is selected, only the first image is sent as first_frame."},
                    ),
                    model_option(
                        "bytedance/seedance-2.0",
                        label="ByteDance Seedance 2.0",
                        description="Higher-quality Seedance 2.0 route via OpenRouter; supports text-to-video, first-frame image-to-video, and SR2 multimodal reference-to-video.",
                        input_modes=["text", "first_frame", "multi_reference", "audio_reference", "video_reference"],
                        duration={"adjustable": True, "min": 4, "max": 15, "allowed": [], "note": "OpenRouter video generation is paid; check current model capabilities before production routing."},
                        reference_images={"supported": True, "min": 0, "max": 8, "mode": "input_references", "note": "Max SR2 sends selected images through OpenRouter input_references for reference-to-video; non-SR2 legacy calls may still use first_frame."},
                        reference_audios={"supported": True, "min": 0, "max": 4, "mode": "input_references", "note": "OpenRouter documents audio/video input references as currently honored by BytePlus Seedance 2.0."},
                        reference_videos={"supported": True, "min": 0, "max": 4, "mode": "input_references", "note": "Use for motion, pacing, gesture, or temporal style references through OpenRouter input_references."},
                        audio_input={"supported": True, "recommended": True, "mode": "provider_audio_or_reference_audio", "note": "OpenCrew requests provider audio for SR2 when input references are used; reference audio can guide cadence or sound style."},
                    ),
                    model_option(
                        "bytedance/seedance-1-5-pro",
                        label="ByteDance Seedance 1.5 Pro",
                        description="Seedance 1.5 Pro route via OpenRouter; supports clips up to 1080p and first-frame image-to-video on OpenRouter.",
                        input_modes=["text", "first_frame"],
                        duration={"adjustable": True, "min": 4, "max": 12, "allowed": [], "note": "OpenRouter video generation is paid; check current model capabilities before production routing."},
                        reference_images={"supported": True, "min": 0, "max": 1, "mode": "first_frame", "note": "OpenCrew supports text-to-video with no references; when an image is selected, only the first image is sent as first_frame."},
                    ),
                ],
            },
            {
                "provider": "wan",
                "provider_label": "Wan",
                "description": "Alibaba Cloud Bailian Tongyi Wanxiang video models.",
                "docs_url": "https://bailian.console.aliyun.com/cn-beijing/?tab=doc#/doc/?type=model&url=3023501",
                "models": [
                    {
                        **model_option("wan2.7-t2v-2026-04-25"),
                        "description": "万相 2.7 文生视频模型；适合仅用文本提示词生成短视频，支持 9:16 竖屏商业短视频、故事片段和产品场景动态画面。",
                        "input_modes": ["text"],
                        "audio_input": {"supported": True, "recommended": True, "mode": "audio_url", "media_type": "driving_audio", "note": "支持上传自定义音频文件；适合文生视频旁白、配乐或声画同步测试。"},
                        "duration": {"adjustable": True, "min": 3, "max": 30, "allowed": [], "note": "支持纯文生视频；建议 3-30 秒，测试默认 4 秒。"},
                    },
                    {
                        **model_option("happyhorse-1.0-i2v"),
                        "description": "首帧生视频推荐；适合单张图片转动态视频，支持音频、1080P、3-15 秒。",
                        "input_modes": ["first_frame"],
                        "audio_input": {"supported": True, "recommended": False, "mode": "provider_audio", "note": "支持有声视频；若需要上传自定义音频文件，优先使用 wan2.7-i2v-2026-04-25。"},
                        "duration": {"adjustable": True, "min": 3, "max": 15, "allowed": [], "note": "支持 3-15 秒时长调整。"},
                    },
                    {
                        **model_option("wan2.7-i2v-2026-04-25"),
                        "description": "首尾帧生视频和长视频构建推荐；适合自定义音频、串联片段、叙事和产品演示。",
                        "input_modes": ["first_frame", "first_last"],
                        "audio_input": {"supported": True, "recommended": True, "mode": "driving_audio", "media_type": "driving_audio", "combinations": ["first_frame+driving_audio"], "note": "支持在 media 中同时传 first_frame 与 driving_audio；用于上传音频驱动画面、口型同步或动作卡点。首尾帧+音频组合上线前需做一次 provider 验证。"},
                        "duration": {"adjustable": True, "min": 3, "max": 30, "allowed": [], "note": "支持首尾帧和较长片段；建议 3-30 秒。"},
                    },
                    {
                        **model_option("happyhorse-1.0-r2v"),
                        "description": "参考生视频推荐；适合参考图保持角色一致性。",
                        "input_modes": ["first_frame"],
                        "duration": {"adjustable": True, "min": 3, "max": 15, "allowed": [], "note": "支持 3-15 秒参考生视频。"},
                    },
                    {
                        **model_option("wan2.7-r2v"),
                        "description": "万相 2.7 参考生视频；支持参考图片和参考视频，适合多主体一致性、动作风格或视频主体参考。",
                        "input_modes": ["reference_image", "reference_video"],
                        "reference_images": {"supported": True, "min": 0, "max": 5, "mode": "reference_image", "note": "OpenCrew 发送 media[].type=reference_image；reference_image + reference_video 合计最多 5 个。"},
                        "reference_videos": {"supported": True, "min": 0, "max": 5, "mode": "reference_video", "note": "OpenCrew 发送 media[].type=reference_video；reference_image + reference_video 合计最多 5 个。"},
                        "audio_input": {"supported": True, "recommended": True, "mode": "reference_voice", "field": "media[].reference_voice", "note": "支持为 reference_image/reference_video 指定音频 URL 作为主体音色；适合多角色音色一致性和参考主体表演。"},
                        "duration": {"adjustable": True, "min": 3, "max": 30, "allowed": [], "note": "支持参考生视频；建议 3-30 秒。"},
                    },
                ],
            },
        ],
        "tts": [
            {
                "provider": "google",
                "provider_label": "Google",
                "description": "Gemini speech generation models for text-to-speech.",
                "docs_url": "https://ai.google.dev/gemini-api/docs/speech-generation?hl=zh-cn",
                "voice_guide_url": "voice-test-tool",
                "models": [
                    model_option("gemini-3.1-flash-tts-preview", voices=GOOGLE_TTS_VOICES, voice_modes=["preset"], capabilities=["Preset Voice", "Multi Speaker", "Audio Output"]),
                    model_option("gemini-2.5-flash-preview-tts", voices=GOOGLE_TTS_VOICES, voice_modes=["preset"], capabilities=["Preset Voice", "Multi Speaker", "Audio Output"]),
                    model_option("gemini-2.5-pro-preview-tts", voices=GOOGLE_TTS_VOICES, voice_modes=["preset"], capabilities=["Preset Voice", "Multi Speaker", "Audio Output"]),
                ],
            },
            {
                "provider": "xai",
                "provider_label": "xAI",
                "description": "xAI text-to-speech endpoint, normalized as a model selector.",
                "docs_url": "https://docs.x.ai/developers/model-capabilities/audio/text-to-speech",
                "voice_guide_url": "voice-test-tool",
                "models": [
                    model_option("xai-tts", voices=XAI_TTS_VOICES, voice_modes=["preset", "custom_voice_id"], capabilities=["Preset Voice", "Voice ID", "Audio Output"]),
                ],
            },
            {
                "provider": "qwen",
                "provider_label": "Qwen",
                "description": "Bailian Qwen3 TTS models for standard and instruct-controlled narration.",
                "docs_url": "https://help.aliyun.com/zh/model-studio/qwen-tts",
                "voice_guide_url": "voice-test-tool",
                "models": [
                    model_option("qwen3-tts-flash", voices=QWEN_TTS_FLASH_VOICES, voice_modes=["preset"], capabilities=["Preset Voice", "Streaming Output", "Multi Language", "Dialect", "Audio Output"]),
                    model_option("qwen3-tts-flash-2025-11-27", voices=QWEN_TTS_FLASH_VOICES, voice_modes=["preset"], capabilities=["Preset Voice", "Pinned Version", "Multi Language", "Dialect", "Audio Output"]),
                    model_option("qwen3-tts-instruct-flash", voices=QWEN_TTS_INSTRUCT_VOICES, voice_modes=["preset", "instruct_prompt"], capabilities=["Preset Voice", "Instruct Prompt", "Style Prompt", "Audio Output"], supports_prompt_builder=True),
                    model_option("qwen3-tts-instruct-flash-2026-01-26", voices=QWEN_TTS_INSTRUCT_VOICES, voice_modes=["preset", "instruct_prompt"], capabilities=["Preset Voice", "Instruct Prompt", "Pinned Version", "Audio Output"], supports_prompt_builder=True),
                ],
            },
            {
                "provider": "bytedance",
                "provider_label": "ByteDance Volcano TTS",
                "description": "Volcano/BytePlus Doubao speech synthesis. Paste a BytePlus API Key from the new console, or legacy appid:access_token credentials.",
                "docs_url": "https://docs.byteplus.com/en/docs/byteplusvoice/unidirectional_tts_http",
                "voice_guide_url": "https://www.volcengine.com/docs/6561/2160690",
                "default_extra_json": {
                    "endpoint": BYTEDANCE_TTS_V1_ENDPOINT,
                    "byteplus_endpoint": BYTEPLUS_TTS_V3_ENDPOINT,
                    "byteplus_resource_id": BYTEPLUS_TTS_RESOURCE_ID,
                    "byteplus_app_key": BYTEPLUS_TTS_APP_KEY,
                    "byteplus_format": "pcm",
                    "cluster": "volcano_tts",
                    "encoding": "wav",
                    "sample_rate": 24000,
                    "speed_ratio": 1.0,
                    "uid": "opencrew",
                    "credential_format": "BytePlus API Key, appid:access_token, or JSON credentials",
                },
                "models": [
                    model_option(
                        "seed-tts-1.1",
                        label="Doubao Seed TTS 1.1",
                        description="Volcano big-model TTS over /api/v1/tts with operation=query; output audio is returned as base64.",
                        voices=BYTEDANCE_TTS_VOICES,
                        voice_modes=["preset", "custom_voice_id"],
                        capabilities=["Preset Voice", "Custom Voice Type", "WAV Output", "Mandarin", "Audio Output"],
                    ),
                ],
            },
            {
                "provider": "minimax",
                "provider_label": "MiniMax",
                "description": "MiniMax TTS models exposed through Bailian for HD and Turbo voice generation.",
                "docs_url": "https://bailian.console.aliyun.com/cn-beijing/?tab=doc#/doc/?type=model&url=3026935",
                "voice_guide_url": "OpenCrew/ToolLibrary/Rebuild/Voice/minimax/usage_reference.md",
                "models": [
                    model_option("MiniMax/speech-2.8-hd", voices=[
                        voice_option("male-qn-qingse", "Mandarin Male", gender="male", style="HD commercial narration"),
                        voice_option("female-shaonv", "Mandarin Female", gender="female", style="HD warm narration"),
                    ], voice_modes=["preset"], capabilities=["Preset Voice", "Emotion Control", "HD", "Audio Output"]),
                    model_option("MiniMax/speech-02-hd", voices=[
                        voice_option("male-qn-qingse", "Mandarin Male", gender="male", style="Stable HD narration"),
                        voice_option("female-shaonv", "Mandarin Female", gender="female", style="Stable HD warm narration"),
                    ], voice_modes=["preset"], capabilities=["Preset Voice", "Emotion Control", "Stable", "Audio Output"]),
                    model_option("MiniMax/speech-2.8-turbo", voices=[
                        voice_option("male-qn-qingse", "Mandarin Male", gender="male", style="Low-latency narration"),
                        voice_option("female-shaonv", "Mandarin Female", gender="female", style="Low-latency warm narration"),
                    ], voice_modes=["preset"], capabilities=["Preset Voice", "Low Latency", "Emotion Control", "Audio Output"]),
                    model_option("MiniMax/speech-02-turbo", voices=[
                        voice_option("male-qn-qingse", "Mandarin Male", gender="male", style="Stable low-latency narration"),
                        voice_option("female-shaonv", "Mandarin Female", gender="female", style="Stable low-latency warm narration"),
                    ], voice_modes=["preset"], capabilities=["Preset Voice", "Low Latency", "Stable", "Audio Output"]),
                ],
            },
        ],
        "voice-clone": [
            {
                "provider": "cosyvoice",
                "provider_label": "CozyVoice",
                "description": "Bailian CosyVoice models for high-quality cloned or custom voice scenarios.",
                "docs_url": "https://bailian.console.aliyun.com/cn-beijing/?tab=doc#/doc/?type=model&url=3026935",
                "voice_guide_url": "OpenCrew/ToolLibrary/Rebuild/Voice/cosyvoice/usage_reference.md",
                "models": [
                    model_option("cosyvoice-v3.5-plus", voices=[
                        voice_option("custom_voice_id", "Custom voice id", mode="custom_voice_id", style="Saved, cloned, or voice-design id"),
                    ], voice_modes=["custom_voice_id", "instruct_prompt"], capabilities=["Custom Voice ID", "Voice Clone", "Voice Design", "Instruct Prompt", "Audio Output"], supports_prompt_builder=True),
                    model_option("cosyvoice-v3.5-flash", voices=[
                        voice_option("custom_voice_id", "Custom voice id", mode="custom_voice_id", style="Saved, cloned, or voice-design id"),
                    ], voice_modes=["custom_voice_id", "instruct_prompt"], capabilities=["Custom Voice ID", "Voice Clone", "Voice Design", "Low Latency", "Audio Output"], supports_prompt_builder=True),
                ],
            },
            {
                "provider": "heygen",
                "provider_label": "HeyGen",
                "description": "HeyGen voice clone API for creating reusable voice_clone_id values from reference audio.",
                "docs_url": "https://developers.heygen.com/reference/clone-a-voice",
                "default_extra_json": {
                    "base_url": "https://api.heygen.com",
                    "auth_header": "x-api-key",
                    "clone_voice_path": "/v3/voices/clone",
                    "connection_test_path": "/v3/users/me",
                },
                "models": [
                    model_option("heygen-voice-clone-v3", label="Voice Clone v3", voices=[
                        voice_option("custom_voice_id", "Custom voice id", mode="custom_voice_id", style="HeyGen voice_clone_id"),
                    ], voice_modes=["custom_voice_id"], capabilities=["Voice Clone", "Voice Clone ID", "Speech", "Video Voice"]),
                ],
            },
            {
                "provider": "minimax",
                "provider_label": "MiniMax",
                "description": "MiniMax (Hailuo) voice clone API: uploads reference audio, creates a reusable cloned voice_id, and synthesizes with it via T2A. Requires a GroupId.",
                "docs_url": "https://platform.minimaxi.com/document/voice_clone",
                "default_extra_json": {
                    "base_url": "https://api.minimaxi.com",
                    "auth_header": "Authorization",
                    "group_id": "",
                    "tts_model": "speech-02-hd",
                    "files_upload_path": "/v1/files/upload",
                    "clone_voice_path": "/v1/voice_clone",
                    "t2a_path": "/v1/t2a_v2",
                    "list_voice_path": "/v1/get_voice",
                    "delete_voice_path": "/v1/delete_voice",
                },
                "models": [
                    model_option("minimax-voice-clone-v1", label="Voice Clone v1", voices=[
                        voice_option("custom_voice_id", "Custom voice id", mode="custom_voice_id", style="MiniMax cloned voice_id"),
                    ], voice_modes=["custom_voice_id"], capabilities=["Voice Clone", "Voice Clone ID", "Speech"]),
                ],
            },
        ],
        "lipsync": [
            {
                "provider": "sync",
                "provider_label": "Sync.so",
                "description": "Sync.so lip-sync models for replacing or repairing speech while preserving the source video.",
                "docs_url": "https://sync.so/docs/api-reference/api-overview",
                "models": [
                    model_option("lipsync-1.9.0", description="Legacy lower-cost Sync.so lip-sync model for rough drafts or budget-sensitive comparisons.", capabilities=["Video Input", "Audio Input", "Low Cost"]),
                    model_option("lipsync-2", description="Use video + audio inputs to generate a lip-synced result. Recommended first pass for口播修复验证。", capabilities=["Video Input", "Audio Input", "Lip Sync"]),
                    model_option("lipsync-2-pro", description="Higher quality lip-sync option for final candidates when lipsync-2 is not accurate enough.", capabilities=["Video Input", "Audio Input", "Higher Quality"]),
                    model_option("sync-3", description="Sync.so newer generation family; use for comparison after lipsync-2/pro baseline.", capabilities=["Video Input", "Audio Input", "Lip Sync"]),
                ],
            },
            {
                "provider": "heygen",
                "provider_label": "HeyGen",
                "description": "HeyGen Lipsync API for dubbing or replacing audio while syncing mouth movement to the source video.",
                "docs_url": "https://developers.heygen.com/lipsync-speed",
                "default_extra_json": {
                    "base_url": "https://api.heygen.com",
                    "auth_header": "X-Api-Key",
                    "lipsync_path": "/v3/lipsyncs",
                    "connection_test_path": "/v3/users/me",
                },
                "models": [
                    model_option(
                        "heygen-lipsync-speed",
                        label="speed",
                        description="Fast HeyGen lip-sync mode for previews, batches, and quick iteration.",
                        capabilities=["Video Input", "Audio Input", "Fast Draft", "Lip Sync"],
                    ),
                    model_option(
                        "heygen-lipsync-precision",
                        label="precision",
                        description="Higher fidelity HeyGen lip-sync mode for frame-accurate mouth movement and final renders.",
                        capabilities=["Video Input", "Audio Input", "Higher Fidelity", "Lip Sync"],
                    ),
                ],
            },
            {
                "provider": "kling",
                "provider_label": "Kling AI",
                "description": "Kling AI advanced lip-sync API. It first identifies a face from a source video URL or Kling video_id, then creates an advanced lip-sync task.",
                "docs_url": "https://klingai.com/document-api/api/video/lip-sync",
                "default_extra_json": {
                    "base_url": "https://api-beijing.klingai.com",
                    "auth_header": "Authorization",
                    "identify_face_path": "/v1/videos/identify-face",
                    "lipsync_path": "/v1/videos/advanced-lip-sync",
                    "query_lipsync_path": "/v1/videos/advanced-lip-sync/{task_id}",
                    "pricing_note": "对口型 ¥0.5/5秒；人脸识别 ¥0.05/次。",
                    "public_asset_provider": "tmpfiles",
                    "tmpfiles_upload_url": "https://tmpfiles.org/api/v1/upload",
                    "tmpfiles_expire_seconds": 21600,
                    "public_asset_note": "本地视频 fallback 会默认用 tmpfiles 生成临时公网 URL；生产建议接 OSS/S3/CDN。",
                },
                "models": [
                    model_option(
                        "kling-lipsync-advanced",
                        label="advanced",
                        description="Kling AI advanced lip-sync. Requires source video_url or Kling video_id; local source video files must be published before use.",
                        capabilities=["Video URL/Input ID", "Audio Input", "Face Identify", "Lip Sync"],
                    ),
                ],
            },
            {
                "provider": "chanjing",
                "provider_label": "蝉镜",
                "description": "蝉镜口型驱动 API，支持上传视频和音频后生成对嘴型视频。",
                "docs_url": "https://doc.chanjing.cc/api/lip-syncing/lip-sync-drive.html",
                "default_extra_json": {
                    "base_url": "https://open-api.chanjing.cc",
                    "auth_header": "access_token",
                    "token_path": "/open/v1/access_token",
                    "lipsync_path": "/open/v1/video_lip_sync/create",
                    "detail_path": "/open/v1/video_lip_sync/detail",
                },
                "models": [
                    model_option(
                        "chanjing-lipsync-basic",
                        label="basic",
                        description="蝉镜 model=0 基础版，适合低成本验证和快速草稿。",
                        capabilities=["Video Input", "Audio Input", "Lip Sync", "Basic"],
                    ),
                    model_option(
                        "chanjing-lipsync-quality",
                        label="quality",
                        description="蝉镜 model=1 高质量版，适合最终出片；我们前面实测使用这个版本。",
                        capabilities=["Video Input", "Audio Input", "Lip Sync", "High Quality"],
                    ),
                ],
            },
        ],
        "digital-human": [
            {
                "provider": "heygen",
                "provider_label": "HeyGen",
                "description": "HeyGen digital human and video agent APIs for avatar presenter video generation.",
                "docs_url": "https://developers.heygen.com/docs/quick-start",
                "default_extra_json": {
                    "base_url": "https://api.heygen.com",
                    "auth_header": "X-Api-Key",
                    "connection_test_path": "/v3/users/me",
                    "create_video_agent_path": "/v3/video-agents",
                },
                "models": [
                    model_option(
                        "heygen-video-agent-v3",
                        label="Video Agent v3",
                        description="Prompt-to-video agent endpoint using HeyGen digital humans, avatars, and voices.",
                        capabilities=["Digital Human", "Avatar Video", "Prompt to Video"],
                    ),
                ],
            },
        ],
    }
    if kind not in options:
        raise HTTPException(status_code=404, detail="Unsupported media model kind")
    return options[kind]


def default_provider(kind: str) -> str:
    if kind == "tts":
        return "qwen"
    if kind == "lipsync":
        return "sync"
    if kind == "digital-human":
        return "heygen"
    if kind == "voice-clone":
        return "cosyvoice"
    return "openai" if kind in {"image", "video"} else ""


def default_voice_by_model(option: dict[str, Any]) -> dict[str, str]:
    defaults: dict[str, str] = {}
    for model in option.get("models", []):
        voices = model.get("voices") or []
        if voices:
            defaults[str(model["model"])] = str(voices[0].get("voice_id") or "")
    return defaults


def normalize_voice_by_model(option: dict[str, Any], selected: dict[str, Any]) -> dict[str, str]:
    normalized = default_voice_by_model(option)
    for model in option.get("models", []):
        model_id = str(model.get("model") or "")
        if not model_id:
            continue
        candidate = str(selected.get(model_id) or "").strip()
        if not candidate:
            continue
        voices = model.get("voices") or []
        valid_voices = {str(item.get("voice_id") or "") for item in voices}
        if candidate in valid_voices:
            normalized[model_id] = candidate
    return normalized


def option_by_provider(kind: str) -> dict[str, dict[str, Any]]:
    return {str(item["provider"]): item for item in media_options(kind)}


def ensure_table(ctx: AppContext) -> None:
    with ctx.engine.begin() as conn:
        conn.execute(text(f"""
CREATE TABLE IF NOT EXISTS {CONFIG_TABLE} (
  id BIGSERIAL PRIMARY KEY,
  kind TEXT NOT NULL,
  provider TEXT NOT NULL,
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  active BOOLEAN NOT NULL DEFAULT FALSE,
  model TEXT NOT NULL,
  api_key_ciphertext TEXT,
  api_key_ref TEXT,
  extra_json TEXT NOT NULL DEFAULT '{{}}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_tool_media_provider_kind_provider UNIQUE (kind, provider)
)
"""))


def updated_at_ms(value: Any) -> int | None:
    if isinstance(value, datetime):
        return int(value.timestamp() * 1000)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def provider_credential_fields(kind: str, provider: str) -> list[dict[str, Any]]:
    if provider == "chanjing" and kind in {"video", "lipsync"}:
        return [
            {"key": "app_key", "label": "APP Key", "type": "password", "required_group": "provider_credentials"},
            {"key": "api_key", "label": "API Key", "type": "password", "required_group": "provider_credentials"},
        ]
    return [{"key": "api_key", "label": "API Key", "type": "password", "required_group": ""}]


def row_public(ctx: AppContext, row: Any | None, option: dict[str, Any], kind: str) -> dict[str, Any]:
    provider = str(option["provider"])
    default_model = str(option["models"][0]["model"])
    option_extra = option.get("default_extra_json") if isinstance(option.get("default_extra_json"), dict) else {}
    default_voice_map = default_voice_by_model(option) if kind == "tts" else {}
    if row is None:
        option_extra = normalize_video_audio_extra(kind, provider, default_model, option_extra)
        return {
            **option,
            "kind": kind,
            "model": default_model,
            "enabled": True,
            "active": False,
            "has_api_key": any(secret_ref_available(ctx, ref) for ref in shared_api_key_refs(kind, provider)),
            "api_key_ref": default_api_key_ref(kind, provider),
            "credential_fields": provider_credential_fields(kind, provider),
            "updated_at": None,
            "extra_json": option_extra,
            "selected_voice_by_model": default_voice_map,
        }
    mapping = row._mapping
    valid_models = {str(item["model"]) for item in option["models"]}
    stored_model = str(mapping.get("model") or default_model)
    preserve_disabled_omni_model = (
        kind == "video"
        and provider == "gemini"
        and stored_model == "gemini-omni-flash-preview"
        and not gemini_omni_feature_enabled()
    )
    extra: dict[str, Any] = {**option_extra, **parse_json_object(mapping.get("extra_json"))}
    extra = normalize_video_audio_extra(kind, provider, stored_model, extra)
    selected_voice_by_model = normalize_voice_by_model(
        option,
        {
            **default_voice_map,
            **(extra.get("selected_voice_by_model") if isinstance(extra.get("selected_voice_by_model"), dict) else {}),
        },
    )
    api_key_ref = mapping.get("api_key_ref") or default_api_key_ref(kind, provider)
    has_api_key = bool(str(mapping.get("api_key_ciphertext") or "").strip()) or secret_ref_available(ctx, str(api_key_ref))
    if not has_api_key:
        has_api_key = any(secret_ref_available(ctx, fallback_ref) for fallback_ref in shared_api_key_refs(kind, provider))
    return {
        **option,
        "kind": kind,
        "model": stored_model if stored_model in valid_models or preserve_disabled_omni_model else default_model,
        "enabled": bool(mapping.get("enabled")),
        "active": bool(mapping.get("active")),
        "has_api_key": has_api_key,
        "api_key_ref": api_key_ref,
        "credential_fields": provider_credential_fields(kind, provider),
        "updated_at": updated_at_ms(mapping.get("updated_at")),
        "extra_json": extra,
        "selected_voice_by_model": selected_voice_by_model,
    }


def normalize_agent_model_aliases(kind: str, values: Any, previous: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    if kind not in {"image", "video"}:
        return []
    options = option_by_provider(kind)
    previous_by_key = {
        f"{str(item.get('provider') or '').strip()}::{str(item.get('model') or '').strip()}::{str(item.get('alias') or '').strip()}": item
        for item in previous or []
        if isinstance(item, dict)
    }
    timestamp = int(time.time() * 1000)
    aliases: list[dict[str, Any]] = []
    seen_aliases: set[str] = set()
    for item in values if isinstance(values, list) else []:
        if isinstance(item, BaseModel):
            source = item.model_dump() if hasattr(item, "model_dump") else item.dict()
        elif isinstance(item, dict):
            source = item
        else:
            continue
        alias = str(source.get("alias") or "").strip()
        provider = str(source.get("provider") or "").strip()
        model = str(source.get("model") or "").strip()
        if alias:
            provider, model = canonical_agent_model_alias_target(kind, alias, provider, model)
        if not alias or not provider or not model:
            continue
        alias_key = alias.lower()
        if alias_key in seen_aliases:
            raise HTTPException(status_code=400, detail=f"Duplicate Agent model alias: {alias}")
        option = options.get(provider)
        if not option:
            raise HTTPException(status_code=400, detail=f"Unknown Agent model provider: {provider}")
        valid_models = {str(model_item.get("model") or "") for model_item in option.get("models", [])}
        disabled_omni_alias = (
            kind == "video"
            and provider == "gemini"
            and model == "gemini-omni-flash-preview"
            and not gemini_omni_feature_enabled()
        )
        if model not in valid_models and not disabled_omni_alias:
            raise HTTPException(status_code=400, detail=f"Unknown Agent model for {provider}: {model}")
        previous_item = previous_by_key.get(f"{provider}::{model}::{alias}")
        previous_created_at = previous_item.get("created_at") if isinstance(previous_item, dict) else None
        created_at = int(source.get("created_at") or previous_created_at or timestamp)
        aliases.append({
            "alias": alias[:80],
            "provider": provider,
            "model": model,
            "created_at": created_at,
            "updated_at": int(source.get("updated_at") or timestamp),
        })
        seen_aliases.add(alias_key)
    return aliases


def load_agent_model_aliases(ctx: AppContext, kind: str = "image") -> list[dict[str, Any]]:
    if kind not in {"image", "video"}:
        return []
    ensure_table(ctx)
    with ctx.engine.begin() as conn:
        row = conn.execute(
            text(f"SELECT extra_json FROM {CONFIG_TABLE} WHERE kind = :kind AND provider = :provider LIMIT 1"),
            {"kind": kind, "provider": AGENT_MODEL_ALIASES_PROVIDER},
        ).first()
    extra = parse_json_object(row._mapping.get("extra_json")) if row else {}
    return normalize_agent_model_aliases(kind, extra.get("agent_model_aliases"))


def save_agent_model_aliases(ctx: AppContext, aliases: list[dict[str, Any]], kind: str = "image") -> None:
    if kind not in {"image", "video"}:
        return
    ensure_table(ctx)
    extra_json = json.dumps({"agent_model_aliases": aliases}, ensure_ascii=True)
    with ctx.engine.begin() as conn:
        conn.execute(
            text(f"""
INSERT INTO {CONFIG_TABLE} (kind, provider, enabled, active, model, api_key_ciphertext, api_key_ref, extra_json, created_at, updated_at)
VALUES (:kind, :provider, TRUE, FALSE, :model, NULL, NULL, :extra_json, now(), now())
ON CONFLICT (kind, provider) DO UPDATE SET
  enabled = EXCLUDED.enabled,
  active = FALSE,
  model = EXCLUDED.model,
  api_key_ciphertext = NULL,
  api_key_ref = NULL,
  extra_json = EXCLUDED.extra_json,
  updated_at = EXCLUDED.updated_at
"""),
            {"kind": kind, "provider": AGENT_MODEL_ALIASES_PROVIDER, "model": AGENT_MODEL_ALIASES_PROVIDER, "extra_json": extra_json},
        )


def load_config(ctx: AppContext, kind: str) -> dict[str, Any]:
    ensure_table(ctx)
    options = option_by_provider(kind)
    with ctx.engine.begin() as conn:
        rows = conn.execute(text(f"SELECT * FROM {CONFIG_TABLE} WHERE kind = :kind"), {"kind": kind}).all()
    row_map = {str(row._mapping.get("provider")): row for row in rows}
    providers = [row_public(ctx, row_map.get(provider), option, kind) for provider, option in options.items()]
    active = next((item for item in providers if item["active"]), None) or next(
        (item for item in providers if item["provider"] == default_provider(kind)), providers[0]
    )
    for item in providers:
        item["active"] = item["provider"] == active["provider"]
    payload = {"kind": kind, "active_provider": active["provider"], "providers": providers}
    if kind in {"image", "video"}:
        payload["agent_model_aliases"] = load_agent_model_aliases(ctx, kind)
    return payload


def load_configured_active_provider(ctx: AppContext, kind: str) -> str:
    """Read the active provider without creating config tables on read paths."""

    engine = getattr(ctx, "engine", None)
    if engine is None:
        return ""
    try:
        with engine.begin() as conn:
            row = conn.execute(
                text(f"SELECT provider FROM {CONFIG_TABLE} WHERE kind = :kind AND active = TRUE LIMIT 1"),
                {"kind": kind},
            ).first()
    except (AttributeError, SQLAlchemyError):
        return ""
    if not row:
        return default_provider(kind)
    provider = str(row._mapping.get("provider") or "").strip()
    return provider if provider in option_by_provider(kind) else ""


def public_video_model_capability(model_item: dict[str, Any]) -> dict[str, Any]:
    capability: dict[str, Any] = {}
    for key in ("input_modes", "capabilities", "tasks", "output_modalities", "aspect_ratios"):
        values = model_item.get(key)
        if isinstance(values, list):
            public_values = [str(item or "").strip() for item in values if str(item or "").strip()]
            if public_values:
                capability[key] = public_values
    for key in ("stateful_edit", "supports_video_input", "supports_audio_input"):
        if isinstance(model_item.get(key), bool):
            capability[key] = model_item[key]
    if str(model_item.get("provider_state") or "").strip() in {"interaction"}:
        capability["provider_state"] = str(model_item["provider_state"])
    allowed_keys = {
        "supported",
        "recommended",
        "adjustable",
        "min",
        "max",
        "minimum",
        "maximum",
        "allowed",
        "values",
        "options",
        "presets",
        "enum",
        "mode",
        "field",
        "media_type",
    }
    for key in ("duration", "reference_images", "reference_audios", "reference_videos", "audio_input"):
        value = model_item.get(key)
        if not isinstance(value, dict):
            continue
        public_value = {name: item for name, item in value.items() if name in allowed_keys}
        if public_value:
            capability[key] = public_value
    return capability


def _public_media_model_selected_model(provider_item: dict[str, Any]) -> str:
    models = [item for item in provider_item.get("models") or [] if isinstance(item, dict)]
    model_ids = [str(item.get("model") or item.get("id") or "").strip() for item in models]
    selected = str(provider_item.get("model") or "").strip()
    if selected and selected in model_ids:
        return selected
    return next((model_id for model_id in model_ids if model_id), selected)


def customer_media_public_alias_targets(config: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    if kind not in {"image", "video"}:
        return []
    providers = [item for item in config.get("providers") or [] if isinstance(item, dict)]
    provider_by_id = {str(item.get("provider") or "").strip(): item for item in providers}
    label_prefix = "图像模型" if kind == "image" else "视频模型"

    targets: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    def add_target(alias: str, provider_id: str, model_id: str) -> None:
        alias = str(alias or "").strip()
        provider_id = str(provider_id or "").strip()
        model_id = str(model_id or "").strip()
        key = (alias.lower(), provider_id, model_id)
        if not alias or not provider_id or not model_id or key in seen:
            return
        provider_item = provider_by_id.get(provider_id) or {}
        if (
            kind == "video"
            and provider_id == "gemini"
            and model_id == "gemini-omni-flash-preview"
            and (not gemini_omni_feature_enabled() or not bool(provider_item.get("enabled", True)))
        ):
            return
        targets.append({
            "alias": alias,
            "provider": provider_id,
            "model": model_id,
            "label": f"{label_prefix} {len(targets) + 1:02d}",
            "has_api_key": bool(provider_item.get("has_api_key")),
        })
        seen.add(key)

    configured_aliases = [item for item in config.get("agent_model_aliases") or [] if isinstance(item, dict)]
    for item in configured_aliases:
        add_target(item.get("alias"), item.get("provider"), item.get("model"))
    if targets:
        return targets

    for provider_item in providers:
        if not bool(provider_item.get("enabled", True)):
            continue
        provider_id = str(provider_item.get("provider") or "").strip()
        model_id = _public_media_model_selected_model(provider_item)
        add_target(f"{label_prefix} {len(targets) + 1:02d}", provider_id, model_id)
    return targets


def customer_media_public_alias_target(config: dict[str, Any], kind: str, alias: str) -> tuple[str, str]:
    alias_value = str(alias or "").strip()
    if not alias_value:
        return "", ""
    for item in customer_media_public_alias_targets(config, kind):
        if str(item.get("alias") or "").strip() == alias_value:
            return str(item.get("provider") or "").strip(), str(item.get("model") or "").strip()
    return "", ""


def customer_media_public_config(config: dict[str, Any], kind: str) -> dict[str, Any]:
    if kind not in {"image", "video"}:
        return config
    providers = [item for item in config.get("providers") or [] if isinstance(item, dict)]
    provider_by_id = {str(item.get("provider") or "").strip(): item for item in providers}

    def model_for_alias(alias_item: dict[str, Any]) -> dict[str, Any]:
        provider_id = str(alias_item.get("provider") or "").strip()
        model_id = str(alias_item.get("model") or "").strip()
        provider_item = provider_by_id.get(provider_id) or {}
        for model_item in provider_item.get("models") or []:
            if isinstance(model_item, dict) and str(model_item.get("model") or model_item.get("id") or "").strip() == model_id:
                return model_item
        return {}

    public_aliases: list[dict[str, Any]] = []
    for item in customer_media_public_alias_targets(config, kind):
        if not isinstance(item, dict):
            continue
        alias = str(item.get("alias") or "").strip()
        if not alias:
            continue
        public_item: dict[str, Any] = {
            "alias": alias,
            "label": str(item.get("label") or "").strip() or alias,
            "has_api_key": bool(item.get("has_api_key")),
        }
        if kind == "image":
            public_item["agentImageAlias"] = alias
        else:
            public_item["agentVideoAlias"] = alias
            capability = public_video_model_capability(model_for_alias(item))
            if capability:
                public_item["capability"] = capability
        public_aliases.append(public_item)
    return {
        "kind": kind,
        "active_provider": "",
        "providers": [],
        "agent_model_aliases": public_aliases,
        "source": "customer_public_aliases",
    }


def provider_extra_json(ctx: AppContext, kind: str, provider: str) -> dict[str, Any]:
    option = option_by_provider(kind).get(provider) or {}
    option_extra = option.get("default_extra_json") if isinstance(option.get("default_extra_json"), dict) else {}
    ensure_table(ctx)
    with ctx.engine.begin() as conn:
        row = conn.execute(
            text(f"SELECT extra_json FROM {CONFIG_TABLE} WHERE kind = :kind AND provider = :provider LIMIT 1"),
            {"kind": kind, "provider": provider},
        ).first()
    stored = parse_json_object(row._mapping.get("extra_json")) if row else {}
    return normalize_video_audio_extra(kind, provider, "", {**option_extra, **stored})


def stored_secret_or_env(ctx: AppContext, ref: str) -> str:
    secret_store = getattr(ctx, "secret_store", None)
    getter = getattr(secret_store, "get", None)
    stored = getter(ref) if callable(getter) else ""
    return str(stored or os.environ.get(ref) or "").strip()


def secret_ref_available(ctx: AppContext, ref: str) -> bool:
    secret_store = getattr(ctx, "secret_store", None)
    has = getattr(secret_store, "has", None)
    return bool((callable(has) and has(ref)) or stored_secret_or_env(ctx, ref))


def load_stored_key(ctx: AppContext, kind: str, provider: str) -> str:
    ensure_table(ctx)
    with ctx.engine.begin() as conn:
        row = conn.execute(
            text(f"SELECT api_key_ref, api_key_ciphertext FROM {CONFIG_TABLE} WHERE kind = :kind AND provider = :provider LIMIT 1"),
            {"kind": kind, "provider": provider},
        ).first()
    mapping = row._mapping if row else {}
    ref = str(mapping.get("api_key_ref") or default_api_key_ref(kind, provider)).strip()
    stored = stored_secret_or_env(ctx, ref)
    if stored:
        return stored
    legacy = str(mapping.get("api_key_ciphertext") or "").strip()
    if legacy:
        ctx.secret_store.set(ref, legacy)
        if row:
            with ctx.engine.begin() as conn:
                conn.execute(
                    text(f"UPDATE {CONFIG_TABLE} SET api_key_ref = :ref, api_key_ciphertext = NULL, updated_at = now() WHERE kind = :kind AND provider = :provider"),
                    {"ref": ref, "kind": kind, "provider": provider},
                )
        return legacy
    for fallback_ref in shared_api_key_refs(kind, provider):
        if fallback_ref == ref:
            continue
        fallback = stored_secret_or_env(ctx, fallback_ref)
        if fallback:
            return fallback
    if kind == "voice-clone" and provider == "cosyvoice":
        fallback = load_stored_key(ctx, "tts", "cosyvoice")
        if fallback:
            return fallback
    if kind == "tts" and provider in DASHSCOPE_TTS_SHARED_PROVIDERS:
        sibling_provider = "qwen" if provider == "cosyvoice" else "cosyvoice"
        with ctx.engine.begin() as conn:
            sibling = conn.execute(
                text(f"SELECT api_key_ref, api_key_ciphertext FROM {CONFIG_TABLE} WHERE kind = 'tts' AND provider = :provider LIMIT 1"),
                {"provider": sibling_provider},
            ).first()
        if sibling:
            sibling_mapping = sibling._mapping
            sibling_ref = str(sibling_mapping.get("api_key_ref") or f"tts_{sibling_provider}_key").strip()
            sibling_stored = stored_secret_or_env(ctx, sibling_ref)
            if sibling_stored:
                return sibling_stored
            sibling_legacy = str(sibling_mapping.get("api_key_ciphertext") or "").strip()
            if sibling_legacy:
                ctx.secret_store.set(sibling_ref, sibling_legacy)
                with ctx.engine.begin() as conn:
                    conn.execute(
                        text(f"UPDATE {CONFIG_TABLE} SET api_key_ref = :ref, api_key_ciphertext = NULL, updated_at = now() WHERE kind = 'tts' AND provider = :provider"),
                        {"ref": sibling_ref, "provider": sibling_provider},
                    )
                return sibling_legacy
    return ""


def provider_has_submitted_or_stored_key(ctx: AppContext, conn: Any, kind: str, provider: str, submitted_api_key: str) -> bool:
    if str(submitted_api_key or "").strip():
        return True
    row = conn.execute(
        text(f"SELECT api_key_ref, api_key_ciphertext FROM {CONFIG_TABLE} WHERE kind = :kind AND provider = :provider LIMIT 1"),
        {"kind": kind, "provider": provider},
    ).first()
    ref = default_api_key_ref(kind, provider)
    mapping = row._mapping if row else {}
    ref = str(mapping.get("api_key_ref") or ref)
    available = (
        bool(str(mapping.get("api_key_ciphertext") or "").strip())
        or secret_ref_available(ctx, ref)
        or any(secret_ref_available(ctx, fallback_ref) for fallback_ref in shared_api_key_refs(kind, provider))
    )
    if available:
        return True
    return bool(load_stored_key(ctx, "tts", "cosyvoice")) if kind == "voice-clone" and provider == "cosyvoice" else False


def test_media_connection(kind: str, provider: str, model: str, api_key: str, proxy_policy: str = "direct", extra: dict[str, Any] | None = None) -> dict[str, Any]:
    if not api_key:
        if provider == "bytedance" and kind == "tts":
            return connection_result(False, "Credentials missing", "Save the ByteDance Volcano App ID and Access Token before testing the connection.")
        if provider == "chanjing" and kind in {"video", "lipsync"}:
            return connection_result(False, "Credentials missing", "Save the Chanjing APP Key and API Key before testing the connection.")
        return connection_result(False, "API Key missing", "Save this provider config before testing the connection.")
    try:
        if provider == "openai":
            request_json(f"https://api.openai.com/v1/models/{urllib.parse.quote(model, safe='')}", api_key, proxy_policy=proxy_policy)
        elif provider == "xai":
            request_json(f"https://api.x.ai/v1/models/{urllib.parse.quote(model, safe='')}", api_key, proxy_policy=proxy_policy)
        elif provider in {"gemini", "google"}:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(model, safe='')}?key={urllib.parse.quote(api_key, safe='')}"
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with provider_urlopen(req, timeout=12, proxy_policy=proxy_policy) as res:
                res.read()
        elif provider in {"wan", "qwen", "cosyvoice", "minimax"}:
            if kind in {"tts", "voice-clone"}:
                result = post_json(
                    "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation",
                    api_key,
                    {"model": model, "input": {"text": "OpenCrew connection test"}},
                    proxy_policy=proxy_policy,
                )
                status = int(result.get("status") or 0)
                text = body_text(result.get("body"))
                lowered = text.lower()
                if status in {401, 403} or "invalid api-key" in lowered or "invalidapikey" in lowered or "unauthorized" in lowered:
                    raise RuntimeError(f"DashScope authentication failed: HTTP {status}: {text}")
                if status == 404 or "not found" in lowered:
                    raise RuntimeError(f"DashScope TTS endpoint/model was not found: HTTP {status}: {text}")
                if status >= 500:
                    raise RuntimeError(f"DashScope service error: HTTP {status}: {text}")
                return connection_result(True, "Connection verified", f"{kind}/{provider}/{model} accepted the saved database key.")
            result = post_json(
                "https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis",
                api_key,
                {"model": model, "input": {}},
                {"X-DashScope-Async": "enable"},
                proxy_policy=proxy_policy,
            )
            status = int(result.get("status") or 0)
            text = body_text(result.get("body"))
            lowered = text.lower()
            if status in {401, 403} or "invalid api-key" in lowered or "invalidapikey" in lowered or "unauthorized" in lowered:
                raise RuntimeError(f"DashScope authentication failed: HTTP {status}: {text}")
            if status == 404 or "not found" in lowered:
                raise RuntimeError(f"DashScope video endpoint/model was not found: HTTP {status}: {text}")
            if "model" in lowered and any(token in lowered for token in ["not exist", "not found", "invalid", "unsupported", "unavailable"]):
                raise RuntimeError(f"DashScope model validation failed: HTTP {status}: {text}")
            if status >= 500:
                raise RuntimeError(f"DashScope service error: HTTP {status}: {text}")
        elif provider in {"bytedance", "seedance", "volcengine", "ark"} and kind == "video":
            return connection_result(True, "API Key saved", "Volcano Ark Seedance video generation is a paid async task; the saved key will be verified during generation.")
        elif provider == "kling" and kind == "video":
            return connection_result(True, "API Key saved", "Kling video generation is a paid async task; the saved key will be verified during generation.")
        elif provider == "bytedance" and kind == "tts":
            credentials = bytedance_tts_credentials(api_key, extra)
            if credentials.get("auth_mode") == "byteplus_api_key":
                return connection_result(True, "Credentials saved", "BytePlus TTS preview is a paid synthesis request; the saved X-Api-Key will be verified during preview or generation.")
            return connection_result(True, "Credentials saved", "ByteDance Volcano TTS preview is a paid synthesis request; the saved App ID and Access Token will be verified during preview or generation.")
        elif provider == "openrouter" and kind == "video":
            return connection_result(True, "API Key saved", "OpenRouter video generation is a paid async task; the saved key will be verified during generation.")
        elif provider == "chanjing" and kind == "video":
            return connection_result(True, "Credentials saved", "蝉镜视频生成需要 APP Key / API Key；真实校验会在生成任务时完成，避免连接测试消耗蝉豆。")
        elif provider == "sync":
            return connection_result(True, "API Key saved", "Sync.so requires video and audio files for a real generation test; the key is stored and ready for lip-sync jobs.")
        elif provider == "kling" and kind == "lipsync":
            return connection_result(True, "API Key saved", "Kling AI advanced lip-sync requires a source video_url or Kling video_id plus audio; the saved credential will be verified during generation.")
        elif provider == "chanjing" and kind == "lipsync":
            return connection_result(True, "Credentials saved", "蝉镜口型驱动需要 APP Key / API Key；真实校验会在生成任务时完成。")
        elif provider == "heygen" and kind in {"digital-human", "lipsync"}:
            req = urllib.request.Request(
                "https://api.heygen.com/v3/users/me",
                headers={"X-Api-Key": api_key, "Accept": "application/json"},
            )
            with provider_urlopen(req, timeout=12, proxy_policy=proxy_policy) as res:
                res.read()
            return connection_result(True, "Connection verified", "HeyGen API key accepted by /v3/users/me.")
        elif provider == "heygen" and kind == "voice-clone":
            req = urllib.request.Request(
                "https://api.heygen.com/v3/users/me",
                headers={"x-api-key": api_key, "Accept": "application/json"},
            )
            with provider_urlopen(req, timeout=12, proxy_policy=proxy_policy) as res:
                res.read()
            return connection_result(True, "Connection verified", "HeyGen API key accepted by /v3/users/me.")
        else:
            return connection_result(False, "Unsupported provider", f"No connection test is configured for {provider}.")
    except Exception as exc:
        return connection_result(False, "Connection failed", str(exc))
    return connection_result(True, "Connection verified", f"{kind}/{provider}/{model} is reachable with the saved database key.")


def preview_wav_data_url(seed: str, seconds: float = 0.45, sample_rate: int = 16000) -> str:
    """Return a tiny generated WAV data URL so UI playback can be verified without paid TTS calls."""
    frames = int(seconds * sample_rate)
    freq = 360 + (sum(ord(ch) for ch in seed) % 360)
    pcm = io.BytesIO()
    for index in range(frames):
        fade = min(index / max(1, frames * 0.12), (frames - index) / max(1, frames * 0.18), 1.0)
        sample = int(12000 * fade * math.sin(2 * math.pi * freq * index / sample_rate))
        pcm.write(struct.pack("<h", sample))
    data = pcm.getvalue()
    wav = io.BytesIO()
    wav.write(b"RIFF")
    wav.write(struct.pack("<I", 36 + len(data)))
    wav.write(b"WAVEfmt ")
    wav.write(struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16))
    wav.write(b"data")
    wav.write(struct.pack("<I", len(data)))
    wav.write(data)
    encoded = base64.b64encode(wav.getvalue()).decode("ascii")
    return f"data:audio/wav;base64,{encoded}"


def wav_bytes_from_pcm(pcm_data: bytes, sample_rate: int = 24000, channels: int = 1, bits_per_sample: int = 16) -> bytes:
    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8
    wav = io.BytesIO()
    wav.write(b"RIFF")
    wav.write(struct.pack("<I", 36 + len(pcm_data)))
    wav.write(b"WAVEfmt ")
    wav.write(struct.pack("<IHHIIHH", 16, 1, channels, sample_rate, byte_rate, block_align, bits_per_sample))
    wav.write(b"data")
    wav.write(struct.pack("<I", len(pcm_data)))
    wav.write(pcm_data)
    return wav.getvalue()


def wav_data_url_from_pcm(pcm_data: bytes, sample_rate: int = 24000, channels: int = 1, bits_per_sample: int = 16) -> str:
    encoded = base64.b64encode(wav_bytes_from_pcm(pcm_data, sample_rate, channels, bits_per_sample)).decode("ascii")
    return f"data:audio/wav;base64,{encoded}"


def google_tts_preview_url(
    api_key: str,
    model: str,
    voice_id: str,
    sample_text: str,
    *,
    multi_speaker: bool = False,
    second_voice_id: str = "",
    speaker_1: str = "Speaker1",
    speaker_2: str = "Speaker2",
    proxy_policy: str = "direct",
) -> str:
    speech_config: dict[str, Any]
    if multi_speaker and second_voice_id.strip():
        speech_config = {
            "multiSpeakerVoiceConfig": {
                "speakerVoiceConfigs": [
                    {
                        "speaker": speaker_1.strip() or "Speaker1",
                        "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice_id}},
                    },
                    {
                        "speaker": speaker_2.strip() or "Speaker2",
                        "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": second_voice_id.strip()}},
                    },
                ],
            },
        }
    else:
        speech_config = {
            "voiceConfig": {
                "prebuiltVoiceConfig": {"voiceName": voice_id},
            },
        }
    result = post_json(
        f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(model, safe='')}:generateContent?key={urllib.parse.quote(api_key, safe='')}",
        "",
        {
            "contents": [{"parts": [{"text": sample_text}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": speech_config,
            },
        },
        timeout=30,
        proxy_policy=proxy_policy,
    )
    status = int(result.get("status") or 0)
    body = result.get("body")
    if status >= 400:
        raise RuntimeError(f"Google TTS preview failed: HTTP {status}: {body_text(body)}")
    if not isinstance(body, dict):
        raise RuntimeError(f"Google TTS preview returned non-JSON response: {body_text(body)}")
    for candidate in body.get("candidates", []):
        content = candidate.get("content") if isinstance(candidate, dict) else {}
        for part in content.get("parts", []) if isinstance(content, dict) else []:
            inline_data = part.get("inlineData") or part.get("inline_data") if isinstance(part, dict) else None
            if not isinstance(inline_data, dict):
                continue
            encoded = str(inline_data.get("data") or "").strip()
            mime_type = str(inline_data.get("mimeType") or inline_data.get("mime_type") or "audio/wav").strip()
            if not encoded:
                continue
            if "pcm" in mime_type or "l16" in mime_type:
                return wav_data_url_from_pcm(base64.b64decode(encoded), sample_rate=24000)
            return f"data:{mime_type};base64,{encoded}"
    raise RuntimeError(f"Google TTS preview did not return inline audio data: {body_text(body)}")


def xai_tts_preview_url(api_key: str, voice_id: str, sample_text: str, language: str = "zh", proxy_policy: str = "direct") -> str:
    result = post_binary(
        "https://api.x.ai/v1/tts",
        api_key,
        {"text": sample_text, "voice_id": voice_id, "language": language or "auto", "format": "mp3"},
        timeout=30,
        proxy_policy=proxy_policy,
    )
    status = int(result.get("status") or 0)
    body = result.get("body") if isinstance(result.get("body"), bytes) else b""
    if status >= 400:
        raise RuntimeError(f"xAI TTS preview failed: HTTP {status}: {body.decode('utf-8', errors='replace')[:2000]}")
    if not body:
        raise RuntimeError("xAI TTS preview returned empty audio.")
    content_type = str(result.get("content_type") or "audio/mpeg").split(";")[0]
    encoded = base64.b64encode(body).decode("ascii")
    return f"data:{content_type};base64,{encoded}"


def xai_tts_match_preview_url(api_key: str, voice_id: str, sample_text: str, language: str = "zh", proxy_policy: str = "direct") -> str:
    """Prefer WAV for voice matching so the local matcher can read samples without MP3 codecs."""
    result = post_binary(
        "https://api.x.ai/v1/tts",
        api_key,
        {"text": sample_text, "voice_id": voice_id, "language": language or "auto", "format": "wav"},
        timeout=30,
        proxy_policy=proxy_policy,
    )
    status = int(result.get("status") or 0)
    body = result.get("body") if isinstance(result.get("body"), bytes) else b""
    if status >= 400:
        raise RuntimeError(f"xAI TTS match preview failed: HTTP {status}: {body.decode('utf-8', errors='replace')[:2000]}")
    if not body:
        raise RuntimeError("xAI TTS match preview returned empty audio.")
    content_type = str(result.get("content_type") or "audio/wav").split(";")[0]
    encoded = base64.b64encode(body).decode("ascii")
    return f"data:{content_type};base64,{encoded}"


def dashscope_language_type(language: str) -> str:
    mapping = {
        "zh": "Chinese",
        "en": "English",
        "ja": "Japanese",
        "ko": "Korean",
        "de": "German",
        "fr": "French",
        "ru": "Russian",
        "pt": "Portuguese",
        "es": "Spanish",
        "it": "Italian",
        "shanghainese": "Chinese",
        "beijingese": "Chinese",
        "nanjingese": "Chinese",
        "shaanxi": "Chinese",
        "minnan": "Chinese",
        "tianjin": "Chinese",
        "sichuanese": "Chinese",
        "cantonese": "Chinese",
    }
    value = str(language or "").strip()
    normalized = value.lower()
    return mapping.get(value) or mapping.get(normalized) or value or "Chinese"


def looks_like_byteplus_tts_api_key(value: str) -> bool:
    raw = str(value or "").strip()
    return bool(re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", raw))


def bytedance_tts_credentials(secret: str, extra: dict[str, Any] | None = None) -> dict[str, str]:
    extra_payload = extra or {}
    app_id = str(extra_payload.get("app_id") or extra_payload.get("appid") or "").strip()
    access_token = ""
    byteplus_api_key = str(extra_payload.get("byteplus_api_key") or extra_payload.get("x_api_key") or "").strip()
    auth_mode = str(extra_payload.get("auth_mode") or "").strip().lower()
    raw = str(secret or "").strip()
    if raw.startswith("{"):
        payload = parse_json_object(raw)
        app_id = str(payload.get("app_id") or payload.get("appid") or app_id).strip()
        access_token = str(payload.get("access_token") or payload.get("token") or "").strip()
        byteplus_api_key = str(payload.get("byteplus_api_key") or payload.get("x_api_key") or byteplus_api_key).strip()
        if not app_id and not access_token:
            byteplus_api_key = str(payload.get("api_key") or byteplus_api_key).strip()
        elif not access_token:
            access_token = str(payload.get("api_key") or "").strip()
    elif raw:
        for delimiter in ("|", ":", ","):
            if delimiter in raw:
                left, right = raw.split(delimiter, 1)
                app_id = app_id or left.strip()
                access_token = right.strip()
                break
        else:
            if auth_mode in {"byteplus", "byteplus_api_key", "x-api-key", "x_api_key"} or looks_like_byteplus_tts_api_key(raw):
                byteplus_api_key = raw
            else:
                access_token = raw
    if byteplus_api_key and not app_id:
        return {"auth_mode": "byteplus_api_key", "api_key": byteplus_api_key}
    if not app_id:
        raise RuntimeError("ByteDance TTS requires app_id for legacy credentials. Paste a BytePlus API Key, appid:access_token, or JSON credentials.")
    if not access_token:
        raise RuntimeError("ByteDance TTS requires access_token. Paste credentials as appid:access_token or JSON with app_id/access_token.")
    return {"app_id": app_id, "access_token": access_token}


def bytedance_tts_request_payload(model: str, voice_id: str, sample_text: str, credentials: dict[str, str], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    extra_payload = extra or {}
    audio: dict[str, Any] = {
        "voice_type": voice_id,
        "encoding": str(extra_payload.get("encoding") or "wav"),
        "speed_ratio": float(extra_payload.get("speed_ratio") or 1.0),
        "rate": int(extra_payload.get("sample_rate") or extra_payload.get("rate") or 24000),
    }
    emotion = str(extra_payload.get("emotion") or "").strip()
    if emotion:
        audio["enable_emotion"] = bool(extra_payload.get("enable_emotion", True))
        audio["emotion"] = emotion
        audio["emotion_scale"] = int(extra_payload.get("emotion_scale") or 4)
    request: dict[str, Any] = {
        "reqid": uuid.uuid4().hex,
        "text": sample_text,
        "operation": "query",
    }
    if model:
        request["model"] = model
    return {
        "app": {
            "appid": credentials["app_id"],
            "token": credentials["access_token"],
            "cluster": str(extra_payload.get("cluster") or "volcano_tts"),
        },
        "user": {"uid": str(extra_payload.get("uid") or "opencrew")},
        "audio": audio,
        "request": request,
    }


def byteplus_tts_format(extra: dict[str, Any] | None = None) -> str:
    extra_payload = extra or {}
    value = str(extra_payload.get("byteplus_format") or extra_payload.get("format") or "").strip().lower()
    return value if value in {"mp3", "ogg_opus", "pcm"} else "mp3"


def byteplus_tts_endpoint(extra: dict[str, Any] | None = None) -> str:
    extra_payload = extra or {}
    endpoint = str(extra_payload.get("byteplus_endpoint") or "").strip()
    if endpoint:
        return endpoint
    endpoint = str(extra_payload.get("endpoint") or "").strip()
    return endpoint if "/api/v3/" in endpoint else BYTEPLUS_TTS_V3_ENDPOINT


def byteplus_tts_headers(credentials: dict[str, str], extra: dict[str, Any] | None = None) -> dict[str, str]:
    extra_payload = extra or {}
    return {
        "X-Api-Key": credentials["api_key"],
        "X-Api-Resource-Id": str(extra_payload.get("byteplus_resource_id") or extra_payload.get("resource_id") or BYTEPLUS_TTS_RESOURCE_ID),
        "X-Api-App-Key": str(extra_payload.get("byteplus_app_key") or extra_payload.get("app_key") or BYTEPLUS_TTS_APP_KEY),
        "X-Api-Request-Id": uuid.uuid4().hex,
        "Connection": "keep-alive",
    }


def byteplus_tts_request_payload(model: str, voice_id: str, sample_text: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    extra_payload = extra or {}
    additions: dict[str, Any] = {
        "disable_markdown_filter": True,
        "enable_language_detector": True,
        "enable_latex_tn": True,
        "disable_default_bit_rate": True,
        "cache_config": {"text_type": 1, "use_cache": True},
    }
    configured_additions = extra_payload.get("byteplus_additions", extra_payload.get("additions"))
    if isinstance(configured_additions, dict):
        additions.update(configured_additions)
    elif isinstance(configured_additions, str) and configured_additions.strip():
        additions.update(parse_json_object(configured_additions))
    audio_params: dict[str, Any] = {
        "format": byteplus_tts_format(extra_payload),
        "sample_rate": int(extra_payload.get("sample_rate") or extra_payload.get("rate") or 24000),
    }
    speed_ratio = extra_payload.get("speed_ratio")
    if speed_ratio is not None:
        audio_params["speed_ratio"] = float(speed_ratio)
    emotion = str(extra_payload.get("emotion") or "").strip()
    if emotion:
        audio_params["enable_emotion"] = bool(extra_payload.get("enable_emotion", True))
        audio_params["emotion"] = emotion
        audio_params["emotion_scale"] = int(extra_payload.get("emotion_scale") or 4)
    return {
        "user": {"uid": str(extra_payload.get("uid") or "opencrew")},
        "req_params": {
            "text": sample_text,
            "speaker": voice_id,
            "audio_params": audio_params,
            "additions": json.dumps(additions, ensure_ascii=False),
        },
    }


def byteplus_tts_stream_audio(
    endpoint: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout: int = 60,
    proxy_policy: str = "direct",
) -> bytes:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json", **headers},
        method="POST",
    )
    audio = bytearray()
    last_payload: Any = {}
    try:
        with provider_urlopen(req, timeout=timeout, proxy_policy=proxy_policy) as res:
            for raw_line in res:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise RuntimeError(f"BytePlus TTS stream returned non-JSON line: {line[:500]}") from exc
                last_payload = event
                code = int(event.get("code") or 0)
                if code == 0 and event.get("data"):
                    audio.extend(base64.b64decode(str(event.get("data") or "")))
                    continue
                if code == 20000000:
                    break
                if code > 0:
                    message = str(event.get("message") or event.get("error") or "unknown error")
                    raise RuntimeError(f"BytePlus TTS failed: code={code} message={message}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2000]
        raise RuntimeError(f"BytePlus TTS failed: HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(str(exc.reason)) from exc
    if not audio:
        raise RuntimeError(f"BytePlus TTS did not return audio data: {body_text(last_payload)}")
    return bytes(audio)


def bytedance_tts_audio_bytes(
    api_key: str,
    model: str,
    voice_id: str,
    sample_text: str,
    extra: dict[str, Any] | None = None,
    proxy_policy: str = "direct",
) -> tuple[bytes, str]:
    extra_payload = extra or {}
    credentials = bytedance_tts_credentials(api_key, extra_payload)
    if credentials.get("auth_mode") == "byteplus_api_key":
        raw = byteplus_tts_stream_audio(
            byteplus_tts_endpoint(extra_payload),
            byteplus_tts_request_payload(model, voice_id, sample_text, extra_payload),
            byteplus_tts_headers(credentials, extra_payload),
            timeout=60,
            proxy_policy=proxy_policy,
        )
        output_format = byteplus_tts_format(extra_payload)
        if output_format == "pcm":
            raw = wav_bytes_from_pcm(raw, sample_rate=int(extra_payload.get("sample_rate") or extra_payload.get("rate") or 24000))
            return raw, "audio/wav"
        return raw, "audio/mpeg" if output_format == "mp3" else "audio/ogg"
    endpoint = str(extra_payload.get("endpoint") or BYTEDANCE_TTS_V1_ENDPOINT).strip()
    payload = bytedance_tts_request_payload(model, voice_id, sample_text, credentials, extra_payload)
    result = post_json(
        endpoint,
        "",
        payload,
        extra_headers={"Authorization": f"Bearer; {credentials['access_token']}"},
        timeout=60,
        proxy_policy=proxy_policy,
    )
    status = int(result.get("status") or 0)
    body = result.get("body")
    if status >= 400:
        raise RuntimeError(f"ByteDance TTS preview failed: HTTP {status}: {body_text(body)}")
    if not isinstance(body, dict):
        raise RuntimeError(f"ByteDance TTS preview returned non-JSON response: {body_text(body)}")
    code = int(body.get("code") or 0)
    if code != 3000:
        message = str(body.get("message") or "unknown error")
        raise RuntimeError(f"ByteDance TTS preview failed: code={code} message={message}")
    audio_data = str(body.get("data") or "").strip()
    if not audio_data:
        raise RuntimeError(f"ByteDance TTS preview did not return base64 audio data: {body_text(body)}")
    encoding = str(extra_payload.get("encoding") or "wav").lower()
    mime = "audio/mpeg" if encoding == "mp3" else "audio/wav" if encoding == "wav" else "application/octet-stream"
    return base64.b64decode(audio_data), mime


def bytedance_tts_preview_url(api_key: str, model: str, voice_id: str, sample_text: str, extra: dict[str, Any] | None = None, proxy_policy: str = "direct") -> str:
    raw, mime = bytedance_tts_audio_bytes(api_key, model, voice_id, sample_text, extra, proxy_policy)
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def dashscope_tts_preview_url(api_key: str, provider: str, model: str, voice_id: str, sample_text: str, complex_prompt: str = "", language: str = "Chinese", proxy_policy: str = "direct") -> str:
    prompt = complex_prompt.strip()
    if provider == "cosyvoice":
        audio = dashscope_cosyvoice_tts_audio_bytes(
            api_key,
            model,
            voice_id,
            sample_text,
            prompt,
            language_hints=dashscope_cosyvoice_language_hints(language),
        )
        return f"data:audio/wav;base64,{base64.b64encode(audio).decode('ascii')}"
    elif provider == "minimax":
        payload = {
            "model": model,
            "input": {
                "text": sample_text,
                "output_format": "url",
                "voice_setting": {"voice_id": voice_id, "speed": 1, "vol": 1, "pitch": 0},
                "audio_setting": {"sample_rate": 32000, "bitrate": 128000, "format": "mp3", "channel": 1},
            },
        }
        url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
    else:
        input_payload = {
            "text": sample_text,
            "voice": voice_id,
            "language_type": dashscope_language_type(language),
        }
        if "instruct" in model and prompt:
            input_payload["instructions"] = prompt
            input_payload["optimize_instructions"] = True
        url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
        payload = {"model": model, "input": input_payload}
    result = post_json(url, api_key, payload, timeout=30, proxy_policy=proxy_policy)
    status = int(result.get("status") or 0)
    body = result.get("body")
    if status >= 400:
        raise RuntimeError(f"DashScope TTS preview failed: HTTP {status}: {body_text(body)}")
    if not isinstance(body, dict):
        raise RuntimeError(f"DashScope TTS preview returned non-JSON response: {body_text(body)}")
    output = body.get("output") if isinstance(body.get("output"), dict) else {}
    audio = output.get("audio") if isinstance(output.get("audio"), dict) else {}
    audio_url = str(audio.get("url") or "").strip()
    audio_data = str(audio.get("data") or "").strip()
    if audio_url:
        return audio_url
    if audio_data:
        return f"data:audio/wav;base64,{audio_data}"
    data = output.get("data") if isinstance(output.get("data"), dict) else {}
    minimax_audio = str(data.get("audio") or "").strip()
    if minimax_audio:
        if minimax_audio.startswith(("http://", "https://")):
            return minimax_audio
        try:
            encoded = base64.b64encode(bytes.fromhex(minimax_audio)).decode("ascii")
            return f"data:audio/mp3;base64,{encoded}"
        except ValueError:
            return f"data:audio/mp3;base64,{minimax_audio}"
    raise RuntimeError(f"DashScope TTS preview did not return audio url/data: {body_text(body)}")


def dashscope_cosyvoice_language_hints(language: str) -> list[str]:
    value = str(language or "").strip().lower()
    tokens = {item for item in re.split(r"[\s,;/|_-]+", value) if item}
    hints: list[str] = []
    if {"zh", "zho", "cn", "chinese", "mandarin"} & tokens or "中文" in value or "普通话" in value:
        hints.append("zh")
    if {"en", "eng", "english"} & tokens or "英语" in value:
        hints.append("en")
    return hints or ["zh"]


def dashscope_cosyvoice_response_error_detail(response: Any) -> str:
    if not isinstance(response, dict):
        return ""
    header = response.get("header") if isinstance(response.get("header"), dict) else {}
    detail_parts: list[str] = []
    task_id = str(header.get("task_id") or "").strip()
    event = str(header.get("event") or "").strip()
    error_code = str(header.get("error_code") or "").strip()
    error_message = str(header.get("error_message") or "").strip()
    if event:
        detail_parts.append(f"event={event}")
    if error_code:
        detail_parts.append(f"error_code={error_code}")
    if error_message:
        detail_parts.append(f"error_message={error_message}")
    if task_id:
        detail_parts.append(f"task_id={task_id}")
    if detail_parts:
        return "DashScope " + ", ".join(detail_parts)
    try:
        return json.dumps(response, ensure_ascii=False)[:1200]
    except Exception:
        return str(response)[:1200]


def dashscope_cosyvoice_tts_audio_bytes(
    api_key: str,
    model: str,
    voice_id: str,
    sample_text: str,
    complex_prompt: str = "",
    *,
    workspace: str = "",
    timeout_millis: int = 90000,
    max_attempts: int = 2,
    language_hints: list[str] | None = None,
) -> bytes:
    try:
        import dashscope  # type: ignore
        from dashscope.audio.tts_v2 import SpeechSynthesizer  # type: ignore
        from dashscope.audio.tts_v2.speech_synthesizer import AudioFormat  # type: ignore
    except Exception as exc:
        raise RuntimeError("Python package dashscope with audio.tts_v2 is required for CosyVoice speech synthesis.") from exc
    text_value = str(sample_text or "").strip()
    if not text_value:
        raise RuntimeError("CosyVoice TTS text is empty.")
    instruction = str(complex_prompt or "").strip()
    if len(instruction) > 128:
        instruction = instruction[:128]
    hints = [str(item).strip() for item in (language_hints or ["zh"]) if str(item).strip()]
    audio: bytes | bytearray | None = None
    failure_details: list[str] = []
    safe_attempts = max(1, min(int(max_attempts or 1), 3))
    for attempt in range(1, safe_attempts + 1):
        # dashscope.audio.tts_v2 reads the API key from a module global during synthesis.
        # Keep the whole call serialized so concurrent requests cannot leak keys across providers.
        synthesizer: Any = None
        with _DASHSCOPE_TTS_V2_LOCK:
            previous_api_key = getattr(dashscope, "api_key", None)
            try:
                dashscope.api_key = api_key
                synthesizer = SpeechSynthesizer(
                    model=model,
                    voice=voice_id,
                    format=AudioFormat.WAV_24000HZ_MONO_16BIT,
                    instruction=instruction or None,
                    language_hints=hints or None,
                    workspace=workspace or None,
                )
                audio = synthesizer.call(text_value, timeout_millis=timeout_millis)
            except Exception as exc:
                audio = None
                failure_details.append(f"attempt {attempt}: {exc}")
            finally:
                if synthesizer is not None and hasattr(synthesizer, "get_response"):
                    try:
                        response_detail = dashscope_cosyvoice_response_error_detail(synthesizer.get_response())
                    except Exception as response_exc:
                        response_detail = f"could not read provider response: {response_exc}"
                    if response_detail:
                        failure_details.append(f"attempt {attempt}: {response_detail}")
                dashscope.api_key = previous_api_key
        if isinstance(audio, (bytes, bytearray)) and audio:
            break
        if attempt < safe_attempts:
            time.sleep(min(1.0, 0.25 * attempt))
    if not isinstance(audio, (bytes, bytearray)) or not audio:
        detail = "; ".join(dict.fromkeys(item for item in failure_details if item))[:1800]
        if detail:
            raise RuntimeError(f"CosyVoice speech synthesis returned empty audio. Provider detail: {detail}")
        raise RuntimeError("CosyVoice speech synthesis returned empty audio.")
    return normalize_dashscope_cosyvoice_wav_bytes(bytes(audio))


def normalize_dashscope_cosyvoice_wav_bytes(data: bytes) -> bytes:
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return data
    normalized = bytearray(data)
    struct.pack_into("<I", normalized, 4, max(0, len(normalized) - 8))
    pos = 12
    while pos + 8 <= len(normalized):
        chunk_id = bytes(normalized[pos : pos + 4])
        declared_size = int.from_bytes(normalized[pos + 4 : pos + 8], "little")
        chunk_data_start = pos + 8
        if chunk_id == b"data":
            actual_size = max(0, len(normalized) - chunk_data_start)
            if declared_size > actual_size:
                struct.pack_into("<I", normalized, pos + 4, actual_size)
            break
        next_pos = chunk_data_start + declared_size + (declared_size % 2)
        if next_pos <= pos or next_pos > len(normalized):
            break
        pos = next_pos
    return bytes(normalized)


def safe_voice_token(value: str) -> str:
    token = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value.strip())
    return token[:96] or "voice"


def default_tts_match_sample_text(language: str) -> str:
    if str(language or "").lower().startswith("en"):
        return "Welcome to OpenCrew. This voice sample tests tone, pacing, clarity, and how natural it feels for a concise commercial narration."
    return "欢迎使用 OpenCrew。下面这段声音将用于测试语气、节奏、清晰度和商业短视频旁白的自然程度。"


def audio_url_bytes(audio_url: str) -> tuple[bytes, str]:
    value = audio_url.strip()
    if value.startswith("data:"):
        result = decode_data_url_bytes(value, allowed_content_types=TTS_PREVIEW_AUDIO_CONTENT_TYPES, max_bytes=TTS_PREVIEW_MAX_BYTES)
        return result.data or b"", result.content_type
    result = safe_download_bytes(
        value,
        allowed_content_types=TTS_PREVIEW_AUDIO_CONTENT_TYPES,
        max_bytes=TTS_PREVIEW_MAX_BYTES,
        timeout=30,
        headers={"User-Agent": "OpenCrew/tts-voice-match"},
    )
    return result.data or b"", result.content_type


def audio_extension(mime: str, fallback: str = ".wav") -> str:
    lowered = mime.lower()
    if "mpeg" in lowered or "mp3" in lowered:
        return ".mp3"
    if "wav" in lowered or "wave" in lowered or "pcm" in lowered or "l16" in lowered:
        return ".wav"
    return fallback


def tts_match_cache_dir(ctx: AppContext | None = None) -> Path:
    root = next((parent for parent in Path(__file__).resolve().parents if parent.name == "OpenCrew"), Path(__file__).resolve().parents[2])
    path = root / "ModelConfig" / "tts_voice_previews"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cached_match_preview_path(ctx: AppContext, provider: str, model: str, voice_id: str, sample_text: str, language: str) -> Path:
    digest = hashlib.sha1(f"{provider}\n{model}\n{voice_id}\n{language}\n{sample_text}".encode("utf-8")).hexdigest()[:16]
    return tts_match_cache_dir(ctx) / safe_voice_token(provider) / safe_voice_token(model) / f"{safe_voice_token(voice_id)}_{digest}.wav"


def cached_match_profile_path(preview_path: Path) -> Path:
    return preview_path.with_suffix(".json")


def write_preview_cache(path: Path, audio_url: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data, mime = audio_url_bytes(audio_url)
    source_ext = audio_extension(mime)
    if source_ext == ".wav":
        path.write_bytes(data)
        return
    source_path = path.with_suffix(source_ext)
    source_path.write_bytes(data)
    converted = convert_audio_to_wav(source_path)
    path.write_bytes(converted.read_bytes())


def convert_audio_to_wav(path: Path) -> Path:
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb"):
                return path
        except wave.Error:
            pass
    output = Path(tempfile.mkstemp(suffix=".wav")[1])
    try:
        subprocess.run(["afconvert", str(path), str(output), "-f", "WAVE", "-d", "LEI16"], check=True, capture_output=True)
        return output
    except Exception as exc:
        try:
            output.unlink(missing_ok=True)
        except OSError:
            pass
        raise RuntimeError(f"Unable to convert audio to WAV for matching: {exc}") from exc


def read_wav_float(path: Path) -> tuple[list[float], int]:
    wav_path = convert_audio_to_wav(path)
    with wave.open(str(wav_path), "rb") as reader:
        channels = reader.getnchannels()
        sample_width = reader.getsampwidth()
        rate = reader.getframerate()
        frames = reader.readframes(reader.getnframes())
    if sample_width != 2:
        raise RuntimeError(f"Unsupported WAV sample width for voice matching: {sample_width}")
    values = struct.unpack("<" + "h" * (len(frames) // 2), frames)
    if channels > 1:
        mono = []
        for index in range(0, len(values), channels):
            mono.append(sum(values[index:index + channels]) / channels)
        values = mono
    peak = max((abs(float(item)) for item in values), default=1.0) or 1.0
    return [float(item) / peak for item in values], rate


def resample_audio(values: list[float], source_rate: int, target_rate: int = 16000) -> list[float]:
    if source_rate == target_rate or not values:
        return values
    duration = len(values) / float(source_rate)
    target_len = max(1, int(duration * target_rate))
    result: list[float] = []
    for index in range(target_len):
        source_pos = index * source_rate / target_rate
        left = int(source_pos)
        right = min(left + 1, len(values) - 1)
        frac = source_pos - left
        result.append(values[left] * (1 - frac) + values[right] * frac)
    return result


def trim_voice(values: list[float], threshold: float = 0.025, sample_rate: int = 16000) -> list[float]:
    frame = max(1, int(0.025 * sample_rate))
    hop = max(1, int(0.010 * sample_rate))
    if len(values) <= frame:
        return values
    rms_values = []
    for start in range(0, len(values) - frame + 1, hop):
        segment = values[start:start + frame]
        rms_values.append(math.sqrt(sum(item * item for item in segment) / len(segment)))
    active = [idx for idx, rms in enumerate(rms_values) if rms > threshold]
    if not active:
        return values
    start = max(0, active[0] * hop)
    end = min(len(values), active[-1] * hop + frame)
    return values[start:end]


def spectral_features(values: list[float], sample_rate: int = 16000) -> dict[str, Any]:
    import numpy as np

    y = np.array(values, dtype=np.float64)
    if y.size < 64:
        y = np.pad(y, (0, 64 - y.size))
    y = y - np.mean(y)
    peak = np.max(np.abs(y)) or 1.0
    y = y / peak
    y = np.array(trim_voice(y.tolist(), sample_rate=sample_rate), dtype=np.float64)
    duration = len(values) / sample_rate
    active_duration = len(y) / sample_rate
    rms = float(np.sqrt(np.mean(y * y))) if y.size else 0.0
    zcr = float(np.mean(np.abs(np.diff(np.signbit(y))))) if y.size > 1 else 0.0
    spectrum = np.abs(np.fft.rfft(y * np.hamming(y.size)))
    freqs = np.fft.rfftfreq(y.size, 1 / sample_rate)
    centroid = float((freqs * spectrum).sum() / (spectrum.sum() + 1e-9))
    cumulative = np.cumsum(spectrum)
    rolloff = float(freqs[min(len(freqs) - 1, int(np.searchsorted(cumulative, 0.85 * cumulative[-1])))]) if cumulative.size and cumulative[-1] > 0 else 0.0
    coeffs = compact_mfcc_like(y, sample_rate)
    f0 = pitch_estimate(y, sample_rate)
    active_ratio = active_duration / max(0.1, duration)
    signal_confidence = max(0.0, min(1.0, 0.45 * min(1.0, active_ratio) + 0.35 * float(f0["voiced_ratio"]) + 0.20 * min(1.0, rms / 0.12)))
    return {
        "duration": duration,
        "active_duration": active_duration,
        "active_ratio": active_ratio,
        "signal_confidence": signal_confidence,
        "rms": rms,
        "zcr": zcr,
        "centroid": centroid,
        "rolloff85": rolloff,
        "f0_median": f0["median"],
        "f0_std": f0["std"],
        "f0_variation_semitones": f0["variation_semitones"],
        "voiced_ratio": f0["voiced_ratio"],
        "mfcc": coeffs,
    }


def compact_mfcc_like(y: Any, sample_rate: int) -> list[float]:
    import numpy as np

    frame = int(0.025 * sample_rate)
    hop = int(0.010 * sample_rate)
    nfft = 512
    if y.size < frame:
        y = np.pad(y, (0, frame - y.size))
    frames = []
    for start in range(0, y.size - frame + 1, hop):
        frames.append(y[start:start + frame] * np.hamming(frame))
    if not frames:
        frames = [np.pad(y, (0, max(0, frame - y.size)))[:frame] * np.hamming(frame)]
    power = np.abs(np.fft.rfft(np.vstack(frames), n=nfft)) ** 2
    nfilt = 24
    mel_points = np.linspace(hz_to_mel(80), hz_to_mel(sample_rate / 2), nfilt + 2)
    hz_points = mel_to_hz(mel_points)
    bins = np.floor((nfft + 1) * hz_points / sample_rate).astype(int)
    filters = np.zeros((nfilt, nfft // 2 + 1))
    for j in range(nfilt):
        left, center, right = bins[j], bins[j + 1], bins[j + 2]
        for i in range(left, center):
            if 0 <= i < filters.shape[1]:
                filters[j, i] = (i - left) / max(1, center - left)
        for i in range(center, right):
            if 0 <= i < filters.shape[1]:
                filters[j, i] = (right - i) / max(1, right - center)
    energies = np.dot(power, filters.T)
    energies = np.where(energies <= 0, 1e-10, energies)
    logs = np.log(energies)
    coeff_count = 13
    basis = np.zeros((coeff_count, nfilt))
    for k in range(coeff_count):
        basis[k, :] = np.cos(math.pi * k * (np.arange(nfilt) + 0.5) / nfilt)
    coeffs = np.dot(logs, basis.T)
    stats = np.concatenate([coeffs.mean(axis=0), coeffs.std(axis=0)])
    return [float(item) for item in stats]


def hz_to_mel(value: float) -> float:
    return 2595 * math.log10(1 + value / 700)


def mel_to_hz(value: Any) -> Any:
    import numpy as np

    return 700 * (np.power(10, value / 2595) - 1)


def pitch_estimate(y: Any, sample_rate: int) -> dict[str, float]:
    import numpy as np

    frame = int(0.04 * sample_rate)
    hop = int(0.01 * sample_rate)
    lo = max(1, int(sample_rate / 500))
    hi = max(lo + 1, int(sample_rate / 60))
    values = []
    frame_count = 0
    for start in range(0, max(1, y.size - frame), hop):
        frame_count += 1
        segment = y[start:start + frame]
        if segment.size < frame or float(np.sqrt(np.mean(segment * segment))) < 0.025:
            continue
        segment = segment * np.hamming(segment.size)
        corr = np.correlate(segment, segment, mode="full")[segment.size - 1:]
        if corr[0] <= 0:
            continue
        search = corr[lo:hi]
        if search.size == 0:
            continue
        lag = int(np.argmax(search)) + lo
        confidence = float(corr[lag] / corr[0])
        hz = sample_rate / lag
        if confidence > 0.25 and 60 < hz < 500:
            values.append(hz)
    if not values:
        return {"median": 0.0, "std": 0.0, "variation_semitones": 0.0, "voiced_ratio": 0.0}
    arr = np.array(values)
    median = float(np.median(arr))
    semitones = 12 * np.log2(arr / max(1e-6, median))
    return {
        "median": median,
        "std": float(np.std(arr)),
        "variation_semitones": float(np.std(semitones)),
        "voiced_ratio": float(len(values) / max(1, frame_count)),
    }


def cosine_similarity(left: list[float], right: list[float]) -> float:
    import numpy as np

    a = np.array(left, dtype=np.float64)
    b = np.array(right, dtype=np.float64)
    return float(np.dot(a, b) / ((np.linalg.norm(a) * np.linalg.norm(b)) + 1e-9))


def exp_similarity(left: float, right: float, scale: float) -> float:
    return math.exp(-abs(left - right) / scale)


def voice_match_score(reference: dict[str, Any], candidate: dict[str, Any]) -> tuple[float, dict[str, float]]:
    timbre = (cosine_similarity(reference["mfcc"], candidate["mfcc"]) + 1) / 2
    pitch = pitch_similarity(reference, candidate)
    brightness = exp_similarity(float(reference["centroid"]), float(candidate["centroid"]), 500)
    energy = exp_similarity(float(reference["rms"]), float(candidate["rms"]), 0.10)
    duration = exp_similarity(float(reference["duration"]), float(candidate["duration"]), 2.0)
    score = 0.45 * timbre + 0.20 * pitch + 0.15 * brightness + 0.10 * energy + 0.10 * duration
    return score, {"timbre": timbre, "pitch": pitch, "brightness": brightness, "energy": energy, "duration": duration}


def normalize_gender(value: str) -> str:
    lowered = str(value or "").strip().lower()
    if lowered in {"m", "male", "man", "男", "男声"}:
        return "male"
    if lowered in {"f", "female", "woman", "女", "女声"}:
        return "female"
    return ""


def infer_gender_from_features(features: dict[str, Any]) -> str:
    f0 = float(features.get("f0_median") or 0)
    if f0 <= 0:
        return "unknown"
    if f0 < 205:
        return "male"
    if f0 > 220:
        return "female"
    return "unknown"


def estimated_syllable_count(text_value: str) -> int:
    value = str(text_value or "")
    cjk_count = sum(1 for ch in value if "\u4e00" <= ch <= "\u9fff")
    latin_syllables = 0
    for word in re.findall(r"[A-Za-z]+", value):
        groups = re.findall(r"[aeiouyAEIOUY]+", word)
        count = max(1, len(groups))
        if word.lower().endswith("e") and count > 1:
            count -= 1
        latin_syllables += count
    digit_count = len(re.findall(r"\d+", value))
    other_count = sum(1 for ch in value if ch.strip() and not ("\u4e00" <= ch <= "\u9fff") and not ch.isascii())
    return max(1, cjk_count + latin_syllables + digit_count + other_count)


def speaking_rate_profile(text_value: str, features: dict[str, Any]) -> float:
    duration = max(0.1, float(features.get("active_duration") or features.get("duration") or 0.1))
    return estimated_syllable_count(text_value) / duration


def rate_similarity(reference_rate: float, candidate_rate: float) -> float:
    if reference_rate <= 0 or candidate_rate <= 0:
        return 0.5
    return math.exp(-abs(reference_rate - candidate_rate) / max(3.0, reference_rate * 0.45))


def pitch_similarity(reference: dict[str, Any], candidate: dict[str, Any]) -> float:
    f0 = exp_similarity(float(reference["f0_median"]), float(candidate["f0_median"]), 75)
    variation = exp_similarity(float(reference.get("f0_variation_semitones") or 0), float(candidate.get("f0_variation_semitones") or 0), 2.5)
    return 0.75 * f0 + 0.25 * variation


def expressiveness_profile(features: dict[str, Any]) -> float:
    return min(1.0, float(features.get("f0_variation_semitones") or 0) / 6.0)


def expressiveness_similarity(reference_value: float, candidate_value: float) -> float:
    return math.exp(-abs(reference_value - candidate_value) / 0.35)


def style_tags_from_text(value: str) -> set[str]:
    text_value = str(value or "").lower()
    tags: set[str] = set()
    mapping = {
        "warm": ["warm", "温暖", "温柔", "亲和", "友好"],
        "bright": ["bright", "明亮", "阳光", "积极", "活力", "欢快"],
        "calm": ["calm", "平稳", "舒缓", "放松", "克制", "稳定"],
        "mature": ["mature", "成熟", "资深", "沉稳", "权威"],
        "young": ["young", "年轻", "少女", "少年", "小姐姐"],
        "child": ["child", "kid", "童声", "儿童", "小孩", "正太", "萝莉"],
        "raspy": ["raspy", "沙哑", "烟嗓", "气声"],
        "clear": ["clear", "清晰", "吐字", "新闻", "讲解"],
        "expressive": ["expressive", "情绪", "戏感", "张力", "表现", "鲜活"],
        "commercial": ["commercial", "商务", "商业", "短视频", "广告", "产品"],
        "dialect": ["上海", "北京", "南京", "陕西", "闽南", "天津", "四川", "粤语", "方言"],
    }
    for tag, needles in mapping.items():
        if any(needle in text_value for needle in needles):
            tags.add(tag)
    return tags


def accent_profile_from_voice(voice: dict[str, Any]) -> dict[str, Any]:
    language = str(voice.get("language") or "").strip().lower()
    text_value = f"{voice.get('label') or ''} {voice.get('style') or ''}".lower()
    dialect_needles = ["上海", "北京", "南京", "陕西", "闽南", "天津", "四川", "粤语", "方言", "口音", "话男声", "话女声"]
    is_dialect = any(needle in text_value for needle in dialect_needles)
    is_chinese = language in {"zh", "zh-cn", "chinese", "mandarin", "multilingual"} or "中文" in text_value or "普通话" in text_value
    is_mandarin = is_chinese and not is_dialect
    if "标准普通话" in text_value or "普通话" in text_value:
        is_mandarin = True
        is_dialect = False
    foreign_needles = ["英语", "法语", "意大利", "德语", "俄语", "西班牙", "葡萄牙", "韩语", "日语", "泰语", "越南", "阿拉伯"]
    is_foreign_label = any(needle in text_value for needle in foreign_needles)
    if language.startswith("en") or is_foreign_label:
        is_chinese = False
        is_mandarin = False
    return {
        "language": language,
        "is_chinese": is_chinese,
        "is_mandarin": is_mandarin,
        "is_dialect": is_dialect,
        "is_foreign_label": is_foreign_label,
    }


def accent_similarity(reference_language: str, candidate_profile: dict[str, Any]) -> float:
    language = str(reference_language or "").strip().lower()
    wants_chinese = language.startswith("zh") or language in {"chinese", "mandarin"}
    if not wants_chinese:
        return 1.0
    if bool(candidate_profile.get("is_mandarin")):
        return 1.0
    if bool(candidate_profile.get("is_chinese")) and not bool(candidate_profile.get("is_dialect")):
        return 0.85
    if bool(candidate_profile.get("is_dialect")):
        return 0.35
    return 0.2


def style_similarity(reference_tags: set[str], candidate_tags: set[str]) -> float:
    if not reference_tags:
        return 0.5
    union = reference_tags | candidate_tags
    if not union:
        return 0.5
    return len(reference_tags & candidate_tags) / len(union)


def age_profile_from_tags(tags: set[str], features: dict[str, Any]) -> str:
    if "child" in tags:
        return "child"
    if "young" in tags:
        return "young"
    if "mature" in tags:
        return "mature"
    f0 = float(features.get("f0_median") or 0)
    rate = float(features.get("speaking_rate") or 0)
    if f0 >= 210 or rate >= 6.5:
        return "young"
    if 0 < f0 < 150 and rate and rate < 4.8:
        return "mature"
    return "neutral"


def age_similarity(reference_age: str, candidate_age: str) -> float:
    ref = reference_age if reference_age in {"child", "young", "neutral", "mature"} else "neutral"
    cand = candidate_age if candidate_age in {"child", "young", "neutral", "mature"} else "neutral"
    if ref == cand:
        return 1.0
    adjacent = {("young", "neutral"), ("neutral", "young"), ("neutral", "mature"), ("mature", "neutral")}
    if (ref, cand) in adjacent:
        return 0.7
    if "child" in {ref, cand}:
        return 0.25
    return 0.45


def reference_profile_from_payload(payload: TTSVoiceMatchPayload, features: dict[str, Any]) -> dict[str, Any]:
    explicit_gender = normalize_gender(payload.target_gender)
    inferred_gender = infer_gender_from_features(features)
    gender = explicit_gender or inferred_gender
    text_value = payload.reference_text.strip() or payload.sample_text.strip()
    tags = style_tags_from_text(text_value)
    if not tags:
        tags = {"commercial", "clear"}
    speaking_rate = speaking_rate_profile(text_value, features) if text_value else 0.0
    age_features = {**features, "speaking_rate": speaking_rate}
    return {
        "gender": gender or "unknown",
        "gender_source": "user" if explicit_gender else "audio_f0",
        "inferred_gender": inferred_gender,
        "language": str(payload.language or "zh").strip().lower() or "zh",
        "speaking_rate": speaking_rate,
        "expressiveness": expressiveness_profile(features),
        "age": age_profile_from_tags(tags, age_features),
        "style_tags": sorted(tags),
    }


def candidate_profile_from_voice(voice: dict[str, Any], sample_text: str, features: dict[str, Any]) -> dict[str, Any]:
    declared_gender = normalize_gender(str(voice.get("gender") or ""))
    style = str(voice.get("style") or "")
    label = str(voice.get("label") or voice.get("voice_id") or "")
    audio_gender = infer_gender_from_features(features)
    tags = style_tags_from_text(f"{label} {style}")
    speaking_rate = speaking_rate_profile(sample_text, features)
    age_features = {**features, "speaking_rate": speaking_rate}
    return {
        "gender": declared_gender or audio_gender,
        "declared_gender": declared_gender,
        "audio_gender": audio_gender,
        "accent": accent_profile_from_voice(voice),
        "speaking_rate": speaking_rate,
        "expressiveness": expressiveness_profile(features),
        "age": age_profile_from_tags(tags, age_features),
        "style_tags": sorted(tags),
    }


def profile_match_score(reference: dict[str, Any], candidate: dict[str, Any], reference_profile: dict[str, Any], candidate_profile: dict[str, Any]) -> tuple[float, dict[str, float], bool]:
    timbre = (cosine_similarity(reference["mfcc"], candidate["mfcc"]) + 1) / 2
    pitch = pitch_similarity(reference, candidate)
    brightness = exp_similarity(float(reference["centroid"]), float(candidate["centroid"]), 500)
    energy = exp_similarity(float(reference["rms"]), float(candidate["rms"]), 0.10)
    rate = rate_similarity(float(reference_profile.get("speaking_rate") or 0), float(candidate_profile.get("speaking_rate") or 0))
    accent = accent_similarity(str(reference_profile.get("language") or "zh"), dict(candidate_profile.get("accent") or {}))
    expressiveness = expressiveness_similarity(float(reference_profile.get("expressiveness") or 0), float(candidate_profile.get("expressiveness") or 0))
    style = style_similarity(set(reference_profile.get("style_tags") or []), set(candidate_profile.get("style_tags") or []))
    age = age_similarity(str(reference_profile.get("age") or "neutral"), str(candidate_profile.get("age") or "neutral"))
    ref_gender = normalize_gender(str(reference_profile.get("gender") or ""))
    cand_gender = normalize_gender(str(candidate_profile.get("gender") or ""))
    gender = 1.0
    gender_mismatch = False
    if ref_gender and cand_gender and ref_gender != cand_gender:
        gender = 0.0
        gender_mismatch = True
    elif ref_gender and not cand_gender:
        gender = 0.45
    tone = 0.55 * style + 0.45 * expressiveness
    confidence = min(float(reference.get("signal_confidence") or 0.5), float(candidate.get("signal_confidence") or 0.5))
    timbre_weight = 0.20 + 0.13 * confidence
    pitch_weight = 0.25 + 0.04 * (1 - confidence)
    weights = {
        "accent": 0.12,
        "timbre": timbre_weight,
        "pitch": pitch_weight,
        "brightness": 0.06,
        "energy": 0.03,
        "age": 0.06,
        "tone": 0.08,
        "speaking_rate": 0.04,
    }
    score = (
        weights["accent"] * accent
        + weights["timbre"] * timbre
        + weights["pitch"] * pitch
        + weights["brightness"] * brightness
        + weights["energy"] * energy
        + weights["age"] * age
        + weights["tone"] * tone
        + weights["speaking_rate"] * rate
    ) / sum(weights.values())
    if gender_mismatch:
        score *= 0.02
    if accent < 0.5:
        score *= 0.75
    return score, {"gender": gender, "accent": accent, "timbre": timbre, "pitch": pitch, "brightness": brightness, "energy": energy, "age": age, "tone": tone, "speaking_rate": rate, "expressiveness": expressiveness, "style": style, "signal_confidence": confidence, "timbre_weight": weights["timbre"], "pitch_weight": weights["pitch"]}, gender_mismatch


def voice_prefilter_reason(reference_profile: dict[str, Any], voice: dict[str, Any]) -> str:
    ref_gender = normalize_gender(str(reference_profile.get("gender") or ""))
    declared_gender = normalize_gender(str(voice.get("gender") or ""))
    if ref_gender and declared_gender and ref_gender != declared_gender:
        return f"gender_mismatch:{declared_gender}"
    accent_profile = accent_profile_from_voice(voice)
    if bool(accent_profile.get("is_foreign_label")) and accent_similarity(str(reference_profile.get("language") or "zh"), accent_profile) <= 0.2:
        return "foreign_language_voice"
    return ""


def load_audio_features(path: Path) -> dict[str, Any]:
    values, rate = read_wav_float(path)
    values = resample_audio(values, rate, 16000)
    return spectral_features(values, 16000)


def public_feature_summary(features: dict[str, Any]) -> dict[str, float]:
    return {
        "duration": round(float(features["duration"]), 4),
        "active_duration": round(float(features["active_duration"]), 4),
        "active_ratio": round(float(features["active_ratio"]), 4),
        "signal_confidence": round(float(features["signal_confidence"]), 4),
        "f0_median": round(float(features["f0_median"]), 4),
        "f0_std": round(float(features["f0_std"]), 4),
        "f0_variation_semitones": round(float(features["f0_variation_semitones"]), 4),
        "voiced_ratio": round(float(features["voiced_ratio"]), 4),
        "centroid": round(float(features["centroid"]), 4),
        "rms": round(float(features["rms"]), 4),
    }


def build_media_model_config_router(ctx: AppContext) -> APIRouter:
    router = APIRouter(prefix="/api/setup/media-models", tags=["media-model-config"])

    @router.get("/{kind}/config")
    def get_config(kind: MediaKind) -> dict[str, Any]:
        return load_config(ctx, kind)

    @router.put("/{kind}/config")
    def save_config(kind: MediaKind, payload: MediaConfigSavePayload) -> dict[str, Any]:
        ensure_table(ctx)
        options = option_by_provider(kind)
        active_provider = payload.active_provider.strip()
        if active_provider not in options:
            raise HTTPException(status_code=400, detail=f"Unknown active provider: {active_provider}")
        submitted = {item.provider.strip(): item for item in payload.providers if item.provider.strip()}
        unknown = sorted(set(submitted) - set(options))
        if unknown:
            raise HTTPException(status_code=400, detail=f"Unknown provider: {', '.join(unknown)}")
        agent_model_aliases = normalize_agent_model_aliases(kind, payload.agent_model_aliases, load_agent_model_aliases(ctx, kind) if kind in {"image", "video"} else [])

        with ctx.engine.begin() as conn:
            active_incoming = submitted.get(active_provider)
            if active_incoming is not None and not active_incoming.enabled:
                raise HTTPException(status_code=400, detail=f"Active provider must be enabled: {active_provider}")
            active_api_key = active_incoming.api_key.strip() if active_incoming else ""
            if not provider_has_submitted_or_stored_key(ctx, conn, kind, active_provider, active_api_key):
                credential_label = "Credentials" if kind == "tts" and active_provider == "bytedance" else "APP Key / API Key" if active_provider == "chanjing" and kind in {"video", "lipsync"} else "API Key"
                raise HTTPException(status_code=400, detail=f"{credential_label} is required before setting {kind} provider active: {active_provider}")

            for provider, option in options.items():
                incoming = submitted.get(provider)
                default_model = str(option["models"][0]["model"])
                valid_models = {str(item["model"]) for item in option["models"]}
                model = (incoming.model.strip() if incoming else "") or default_model
                if model not in valid_models:
                    raise HTTPException(status_code=400, detail=f"Unknown model for {provider}: {model}")
                api_key_ref = default_api_key_ref(kind, provider)
                existing = conn.execute(
                    text(f"SELECT api_key_ref, api_key_ciphertext, extra_json FROM {CONFIG_TABLE} WHERE kind = :kind AND provider = :provider LIMIT 1"),
                    {"kind": kind, "provider": provider},
                ).first()
                api_key = (incoming.api_key.strip() if incoming else "")
                legacy_key = str(existing._mapping.get("api_key_ciphertext") or "").strip() if existing else ""
                if api_key:
                    ctx.secret_store.set(api_key_ref, api_key)
                elif legacy_key and not ctx.secret_store.has(api_key_ref):
                    ctx.secret_store.set(api_key_ref, legacy_key)
                enabled = incoming.enabled if incoming else True
                option_extra = option.get("default_extra_json") if isinstance(option.get("default_extra_json"), dict) else {}
                existing_extra = parse_json_object(existing._mapping.get("extra_json")) if existing else {}
                # Accept user-supplied extra values only for keys the provider
                # actually declares in default_extra_json (e.g. MiniMax group_id,
                # base_url, tts_model). This lets the UI configure provider-specific
                # connection fields without allowing arbitrary key injection.
                submitted_extra = (incoming.extra if incoming and isinstance(incoming.extra, dict) else {})
                incoming_extra = {key: value for key, value in submitted_extra.items() if key in option_extra}
                extra_json = {**option_extra, **existing_extra, **incoming_extra, "docs_url": option.get("docs_url", "")}
                extra_json = normalize_video_audio_extra(kind, provider, model, extra_json)
                if kind == "tts":
                    extra_json["selected_voice_by_model"] = normalize_voice_by_model(option, incoming.selected_voice_by_model if incoming else {})
                conn.execute(
                    text(f"""
INSERT INTO {CONFIG_TABLE} (kind, provider, enabled, active, model, api_key_ciphertext, api_key_ref, extra_json, created_at, updated_at)
VALUES (:kind, :provider, :enabled, :active, :model, :api_key, :api_key_ref, :extra_json, now(), now())
ON CONFLICT (kind, provider) DO UPDATE SET
  enabled = EXCLUDED.enabled,
  active = EXCLUDED.active,
  model = EXCLUDED.model,
  api_key_ciphertext = NULL,
  api_key_ref = EXCLUDED.api_key_ref,
  extra_json = EXCLUDED.extra_json,
  updated_at = EXCLUDED.updated_at
"""),
                    {
                        "kind": kind,
                        "provider": provider,
                        "enabled": enabled,
                        "active": provider == active_provider,
                        "model": model,
                        "api_key": None,
                        "api_key_ref": api_key_ref,
                        "extra_json": json.dumps(extra_json, ensure_ascii=True),
                    },
                )
        if kind in {"image", "video"}:
            save_agent_model_aliases(ctx, agent_model_aliases, kind)
        ctx.event("info", "media-model", "Media model config saved", {"kind": kind, "active_provider": active_provider})
        return {"ok": True, **load_config(ctx, kind)}

    @router.post("/{kind}/test")
    def test_config(kind: MediaKind, payload: MediaConnectionTestPayload) -> dict[str, Any]:
        options = option_by_provider(kind)
        provider = payload.provider.strip()
        model = payload.model.strip()
        if provider not in options:
            return connection_result(False, "Unknown provider", provider)
        valid_models = {str(item["model"]) for item in options[provider]["models"]}
        if model not in valid_models:
            return connection_result(False, "Unknown model", f"{provider}/{model}")
        api_key = load_stored_key(ctx, kind, provider)
        resolution = resolve_endpoint(provider, model, kind, f"{kind}_{provider}_key")
        started_at = int(time.time() * 1000)
        extra = provider_extra_json(ctx, kind, provider) if kind == "tts" and provider == "bytedance" else None
        result = test_media_connection(kind, provider, model, api_key, resolution.proxy_policy, extra)
        finished_at = int(time.time() * 1000)
        ctx.local_usage.record(
            provider=provider,
            model_id=model,
            modality=kind,
            proxy_policy=resolution.proxy_policy,
            status="ok" if result["ok"] else "failed",
            error_code="" if result["ok"] else str(result.get("message") or "connection_failed"),
            started_at=started_at,
            finished_at=finished_at,
        )
        ctx.event("info" if result["ok"] else "warn", "media-model", "Media model connection test", {"kind": kind, "provider": provider, "model": model, "status": result["status"]})
        return result

    @router.post("/tts/voices/preview")
    def preview_tts_voice(payload: TTSVoicePreviewPayload) -> dict[str, Any]:
        config_kind = payload.config_kind.strip() or "tts"
        if config_kind not in {"tts", "voice-clone"}:
            raise HTTPException(status_code=400, detail=f"Unsupported voice preview kind: {config_kind}")
        options = option_by_provider(config_kind)
        provider = payload.provider.strip()
        model = payload.model.strip()
        voice_id = payload.voice_id.strip()
        if provider not in options:
            raise HTTPException(status_code=400, detail=f"Unknown voice provider: {provider}")
        valid_models = {str(item["model"]): item for item in options[provider]["models"]}
        if model not in valid_models:
            raise HTTPException(status_code=400, detail=f"Unknown voice model for {provider}: {model}")
        valid_voices = {str(item.get("voice_id")) for item in valid_models[model].get("voices", [])}
        allows_custom_voice = any(str(item.get("mode")) == "custom_voice_id" for item in valid_models[model].get("voices", []))
        if voice_id not in valid_voices and not allows_custom_voice:
            raise HTTPException(status_code=400, detail=f"Unknown TTS voice for {provider}/{model}: {voice_id}")
        second_voice_id = payload.second_voice_id.strip()
        if payload.multi_speaker and provider != "google":
            raise HTTPException(status_code=400, detail="Multi-speaker preview is only configured for Google TTS.")
        if payload.multi_speaker and (not second_voice_id or second_voice_id not in valid_voices):
            raise HTTPException(status_code=400, detail=f"Unknown second TTS voice for {provider}/{model}: {second_voice_id}")
        preview_id = f"{provider}_{model.replace('/', '_')}_{voice_id}"
        sample_text = payload.sample_text.strip() or "欢迎使用 OpenCrew。下面这段声音将用于测试语气、节奏、清晰度和商业短视频旁白的自然程度。"
        real_provider_audio = False
        api_key = load_stored_key(ctx, config_kind, provider)
        if not api_key:
            raise HTTPException(status_code=400, detail=f"Missing saved API key for {provider}.")
        resolution = resolve_endpoint(provider, model, config_kind, default_api_key_ref(config_kind, provider))
        started_at = int(time.time() * 1000)
        try:
            if provider == "google":
                audio_url = google_tts_preview_url(
                    api_key,
                    model,
                    voice_id,
                    sample_text,
                    multi_speaker=payload.multi_speaker,
                    second_voice_id=second_voice_id,
                    speaker_1=payload.speaker_1,
                    speaker_2=payload.speaker_2,
                    proxy_policy=resolution.proxy_policy,
                )
                real_provider_audio = True
            elif provider == "xai":
                audio_url = xai_tts_preview_url(api_key, voice_id, sample_text, payload.language, resolution.proxy_policy)
                real_provider_audio = True
            elif provider == "bytedance":
                audio_url = bytedance_tts_preview_url(api_key, model, voice_id, sample_text, provider_extra_json(ctx, "tts", provider), resolution.proxy_policy)
                real_provider_audio = True
            elif provider in {"qwen", "cosyvoice", "minimax"}:
                audio_url = dashscope_tts_preview_url(api_key, provider, model, voice_id, sample_text, payload.complex_prompt, payload.language, resolution.proxy_policy)
                real_provider_audio = True
            else:
                raise RuntimeError(f"No real TTS preview adapter is configured for {provider}.")
        except Exception as exc:
            ctx.local_usage.record(
                provider=provider,
                model_id=model,
                modality="tts",
                proxy_policy=resolution.proxy_policy,
                status="failed",
                units={"character": len(sample_text)},
                error_code=type(exc).__name__,
                started_at=started_at,
                finished_at=int(time.time() * 1000),
            )
            ctx.event("warn", "media-model", "TTS voice preview failed", {"provider": provider, "model": model, "voice_id": voice_id, "error": str(exc)})
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        ctx.local_usage.record(
            provider=provider,
            model_id=model,
            modality="tts",
            proxy_policy=resolution.proxy_policy,
            status="ok",
            units={"character": len(sample_text)},
            started_at=started_at,
            finished_at=int(time.time() * 1000),
        )
        ctx.event("info", "media-model", "TTS voice preview generated", {"provider": provider, "model": model, "voice_id": voice_id, "real_provider_audio": real_provider_audio})
        return {
            "ok": True,
            "preview_id": preview_id,
            "provider": provider,
            "model": model,
            "voice_id": voice_id,
            "audio_url": audio_url,
            "duration_seconds": 0.45,
            "real_provider_audio": real_provider_audio,
        }

    @router.post("/tts/voices/match")
    def match_tts_voices(payload: TTSVoiceMatchPayload) -> dict[str, Any]:
        reference_path = Path(payload.reference_audio_path).expanduser()
        if not reference_path.exists() or not reference_path.is_file():
            raise HTTPException(status_code=400, detail=f"Reference audio file was not found: {reference_path}")
        sample_text = payload.sample_text.strip() or default_tts_match_sample_text(payload.language)
        top_k = max(1, min(int(payload.top_k or 3), 20))
        try:
            reference_features = load_audio_features(reference_path)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Unable to analyze reference audio: {exc}") from exc
        reference_profile = reference_profile_from_payload(payload, reference_features)

        config = load_config(ctx, "tts")
        options = option_by_provider("tts")
        results: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        skipped: list[dict[str, str]] = []

        for provider_config in config.get("providers", []):
            provider = str(provider_config.get("provider") or "").strip()
            if not provider or provider not in options:
                continue
            if not bool(provider_config.get("enabled", True)):
                continue
            model = str(payload.provider_models.get(provider) or provider_config.get("model") or "").strip()
            models = {str(item.get("model") or ""): item for item in options[provider].get("models", [])}
            model_option_payload = models.get(model) or next(iter(models.values()), None)
            if not model_option_payload:
                continue
            model = str(model_option_payload.get("model") or model)
            api_key = load_stored_key(ctx, "tts", provider)
            if not api_key:
                errors.append({"provider": provider, "model": model, "voice_id": "", "detail": "Missing saved API key"})
                continue
            resolution = resolve_endpoint(provider, model, "tts", f"tts_{provider}_key")
            for voice in model_option_payload.get("voices", []) or []:
                voice_id = str(voice.get("voice_id") or "").strip()
                if not voice_id or str(voice.get("mode") or "") == "custom_voice_id":
                    continue
                prefilter_reason = voice_prefilter_reason(reference_profile, voice)
                if prefilter_reason:
                    skipped.append({"provider": provider, "model": model, "voice_id": voice_id, "reason": prefilter_reason})
                    continue
                cache_path = cached_match_preview_path(ctx, provider, model, voice_id, sample_text, payload.language)
                try:
                    if payload.regenerate or not cache_path.exists():
                        if provider == "google":
                            audio_url = google_tts_preview_url(api_key, model, voice_id, sample_text, proxy_policy=resolution.proxy_policy)
                        elif provider == "xai":
                            audio_url = xai_tts_match_preview_url(api_key, voice_id, sample_text, payload.language, resolution.proxy_policy)
                        elif provider == "bytedance":
                            audio_url = bytedance_tts_preview_url(api_key, model, voice_id, sample_text, provider_extra_json(ctx, "tts", provider), resolution.proxy_policy)
                        elif provider in {"qwen", "cosyvoice", "minimax"}:
                            audio_url = dashscope_tts_preview_url(api_key, provider, model, voice_id, sample_text, "", payload.language, resolution.proxy_policy)
                        else:
                            raise RuntimeError(f"No TTS adapter is configured for {provider}.")
                        write_preview_cache(cache_path, audio_url)
                    candidate_features = load_audio_features(cache_path)
                    candidate_profile = candidate_profile_from_voice(voice, sample_text, candidate_features)
                    ref_gender = normalize_gender(str(reference_profile.get("gender") or ""))
                    cand_gender = normalize_gender(str(candidate_profile.get("gender") or ""))
                    if ref_gender and cand_gender and ref_gender != cand_gender:
                        skipped.append({"provider": provider, "model": model, "voice_id": voice_id, "reason": f"audio_gender_mismatch:{cand_gender}"})
                        continue
                    score, parts, gender_mismatch = profile_match_score(reference_features, candidate_features, reference_profile, candidate_profile)
                    profile_path = cached_match_profile_path(cache_path)
                    profile_path.write_text(
                        json.dumps(
                            {
                                "provider": provider,
                                "provider_label": str(provider_config.get("provider_label") or provider),
                                "model": model,
                                "voice_id": voice_id,
                                "label": str(voice.get("label") or voice_id),
                                "language": str(voice.get("language") or ""),
                                "gender": str(voice.get("gender") or ""),
                                "style": str(voice.get("style") or ""),
                                "sample_text": sample_text,
                                "preview_audio_path": str(cache_path),
                                "features": public_feature_summary(candidate_features),
                                "profile": candidate_profile,
                                "updated_at": datetime.utcnow().isoformat() + "Z",
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                        encoding="utf-8",
                    )
                    audio_bytes = cache_path.read_bytes()
                    results.append({
                        "provider": provider,
                        "provider_label": str(provider_config.get("provider_label") or provider),
                        "model": model,
                        "voice_id": voice_id,
                        "label": str(voice.get("label") or voice_id),
                        "language": str(voice.get("language") or ""),
                        "gender": str(voice.get("gender") or ""),
                        "style": str(voice.get("style") or ""),
                        "score": round(score, 4),
                        "score_parts": {key: round(value, 4) for key, value in parts.items()},
                        "gender_mismatch": gender_mismatch,
                        "sample_text": sample_text,
                        "preview_audio_url": f"data:audio/wav;base64,{base64.b64encode(audio_bytes).decode('ascii')}",
                        "preview_audio_path": str(cache_path),
                        "profile_json_path": str(profile_path),
                        "candidate_profile": candidate_profile,
                        "features": public_feature_summary(candidate_features),
                    })
                except Exception as exc:
                    detail = str(exc)
                    errors.append({"provider": provider, "model": model, "voice_id": voice_id, "detail": detail[:500]})
                    ctx.event("warn", "media-model", "TTS voice match sample failed", {"provider": provider, "model": model, "voice_id": voice_id, "error": detail[:500]})

        ranked = sorted(results, key=lambda item: float(item.get("score") or 0), reverse=True)
        response = {
            "ok": True,
            "reference_audio_path": str(reference_path),
            "sample_text": sample_text,
            "top_k": top_k,
            "recommendations": ranked[:top_k],
            "candidate_count": len(results),
            "error_count": len(errors),
            "skipped_count": len(skipped),
            "errors": errors[:20],
            "skipped": skipped[:50],
            "reference_profile": reference_profile,
            "reference_features": public_feature_summary(reference_features),
        }
        ctx.event("info", "media-model", "TTS voice match completed", {"reference_audio_path": str(reference_path), "candidate_count": len(results), "error_count": len(errors), "top_k": top_k})
        return response

    return router
