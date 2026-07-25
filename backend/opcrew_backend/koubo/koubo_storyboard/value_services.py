from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import wave
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy import text as sql_text

from opcrew_backend.adapters.opencode import OpenCodeSessionClient
from opcrew_backend.context import now_ms
from opcrew_backend.routes.media_model_config import CONFIG_TABLE, ensure_table

from .constants import *
from .io_utils import read_json, safe_workspace_rel, write_json
from .runtime import analysis_tool_env
from .text_utils import redact_payload, redact_secret_text

SERVICE_EXPORTS = (
    "safe_name",
    "number",
    "text",
    "spoken_char_count",
    "tts_duration_is_suspicious",
    "canonical_tts_text",
    "tts_prompt_body_text",
    "locked_tts_cache_text_matches",
)


def safe_name(value: str, fallback: str) -> str:
    name = Path(str(value or "")).name.strip().replace("/", "_").replace("\\", "_")
    return name or fallback


def number(value: Any, fallback: float = 0.0) -> float:
    try:
        parsed = float(value)
        return parsed if parsed == parsed else fallback
    except (TypeError, ValueError):
        return fallback


def text(value: Any, fallback: str = "") -> str:
    return str(value if value is not None else fallback).strip()


def spoken_char_count(value: str) -> int:
    return len(re.findall(r"[\w\u4e00-\u9fff]", str(value or ""), flags=re.UNICODE))


def tts_duration_is_suspicious(text_value: str, duration_seconds: float) -> bool:
    duration = number(duration_seconds)
    chars = spoken_char_count(text_value)
    if duration <= 0 or chars <= 0:
        return False
    expected_max = max(4.0, chars * 0.8)
    return duration > expected_max and duration > chars * 1.0


def canonical_tts_text(value: Any) -> str:
    return re.sub(r"\s+", " ", text(value)).strip()


def tts_prompt_body_text(prompt: Any) -> str:
    value = str(prompt or "")
    matches = list(re.finditer(r"(?:朗读文本|正文|Text)\s*[:：]", value, flags=re.IGNORECASE))
    if not matches:
        return ""
    return canonical_tts_text(value[matches[-1].end():])


def locked_tts_cache_text_matches(payload: dict[str, Any], manifest: dict[str, Any]) -> bool:
    expected = canonical_tts_text(payload.get("srt_text"))
    if not expected:
        return False
    if canonical_tts_text(manifest.get("text")) != expected:
        return False
    try:
        config = json.loads(text(manifest.get("config_key")))
    except Exception:
        config = {}
    if not isinstance(config, dict) or canonical_tts_text(config.get("text")) != expected:
        return False
    prompt_body = tts_prompt_body_text(manifest.get("prompt") or config.get("prompt"))
    return bool(prompt_body) and prompt_body == expected


def register_value_services(ns: Any) -> None:
    for name in SERVICE_EXPORTS:
        setattr(ns, name, globals()[name])
