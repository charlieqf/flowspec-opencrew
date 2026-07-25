from __future__ import annotations

import argparse
import base64
import hashlib
import importlib
import json
import math
import mimetypes
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from PIL import Image
except Exception:
    Image = None

try:
    from OpenCrew.ToolLibrary.Analysis_V1 import DEFAULT_DATABASE_URL_ENV, DEFAULT_OPENCREW_DATABASE_URL
except Exception:
    DEFAULT_DATABASE_URL_ENV = "OPENCREW_DATABASE_URL"
    DEFAULT_OPENCREW_DATABASE_URL = "postgresql+psycopg://opencrew:opencrew@127.0.0.1:5433/opencrew"

TOOLLIB_ROOT = Path(__file__).resolve().parents[1]
if str(TOOLLIB_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLLIB_ROOT))
try:
    from opencrew_runtime_secrets import apply_provider_proxy, resolve_secret_value
except Exception:
    def resolve_secret_value(api_key_ref: str, legacy_value: str = "") -> str:
        return str(legacy_value or "").strip()

    def apply_provider_proxy(provider: str) -> str:
        return "default"

try:
    from ToolLibrary.Analysis_V1.provider_audit import record_model_call_from_prompt_dir
except ModuleNotFoundError:
    from OpenCrew.ToolLibrary.Analysis_V1.provider_audit import record_model_call_from_prompt_dir

try:
    from ToolLibrary.Analysis_V1.video_plan_executor_modules.max_sd_2_privacy_policy import (
        should_apply_max_sd_2_oral_privacy_grid as _should_apply_max_sd_2_oral_privacy_grid,
    )
except ModuleNotFoundError:
    from OpenCrew.ToolLibrary.Analysis_V1.video_plan_executor_modules.max_sd_2_privacy_policy import (
        should_apply_max_sd_2_oral_privacy_grid as _should_apply_max_sd_2_oral_privacy_grid,
    )

try:
    from ToolLibrary.Analysis_V1.video_aspect import (
        image_dimensions_and_aspect as inspect_video_first_frame,
        normalize_video_aspect as normalize_storyboard_video_aspect,
        normalize_video_first_frame,
        prompt_package_for_video_aspect,
        provider_config_for_video_aspect,
        rewrite_video_prompt_file,
    )
except ModuleNotFoundError:
    from OpenCrew.ToolLibrary.Analysis_V1.video_aspect import (
        image_dimensions_and_aspect as inspect_video_first_frame,
        normalize_video_aspect as normalize_storyboard_video_aspect,
        normalize_video_first_frame,
        prompt_package_for_video_aspect,
        provider_config_for_video_aspect,
        rewrite_video_prompt_file,
    )


TOOL_NAME = "05_02_VideoPlanExecutor"
TOOL_VERSION = "0.1.0"
TOOL_DIR_NAME = "S9_05_02_VideoPlanExecutor"
MODULE_REFERENCE_TEMPLATE_RELS = {
    "Image_GPT": "OpenCrew/ToolLibrary/Analysis_V1/Reference/05_02/Image_GPT.md",
    "Image_Gemini": "OpenCrew/ToolLibrary/Analysis_V1/Reference/05_02/Image_Gemini.md",
    "Image_Grok": "OpenCrew/ToolLibrary/Analysis_V1/Reference/05_02/Image_Grok.md",
    "Video_GPT": "OpenCrew/ToolLibrary/Analysis_V1/Reference/05_02/Video_GPT.md",
    "Video_Gemini": "OpenCrew/ToolLibrary/Analysis_V1/Reference/05_02/Video_Gemini.md",
    "Video_Grok": "OpenCrew/ToolLibrary/Analysis_V1/Reference/05_02/Video_Grok.md",
    "Video_OpenRouter": "OpenCrew/ToolLibrary/Analysis_V1/Reference/05_02/Video_OpenRouter.md",
    "Video_SDR2V": "OpenCrew/ToolLibrary/Analysis_V1/Reference/05_02/Video_SDR2V.md",
    "Video_SDR2V_DanceMimic": "OpenCrew/ToolLibrary/Analysis_V1/Reference/05_02/Video_SDR2V_DanceMimic.md",
    "Video_Seedance": "OpenCrew/ToolLibrary/Analysis_V1/Reference/05_02/Video_Seedance.md",
    "Video_ChanJing_Kling": "OpenCrew/ToolLibrary/Analysis_V1/Reference/05_02/Video_ChanJing_Kling.md",
    "Video_ChanJing_ViduQ1": "OpenCrew/ToolLibrary/Analysis_V1/Reference/05_02/Video_ChanJing_ViduQ1.md",
    "Video_ChanJing_Hailuo02": "OpenCrew/ToolLibrary/Analysis_V1/Reference/05_02/Video_ChanJing_Hailuo02.md",
    "Video_ChanJing_Doubao": "OpenCrew/ToolLibrary/Analysis_V1/Reference/05_02/Video_ChanJing_Doubao.md",
    "Video_ChanJing_HappyHorse": "OpenCrew/ToolLibrary/Analysis_V1/Reference/05_02/Video_ChanJing_HappyHorse.md",
    "Video_Kling_Omni": "OpenCrew/ToolLibrary/Analysis_V1/Reference/05_02/Video_Kling_Omni.md",
    "Video_Wan": "OpenCrew/ToolLibrary/Analysis_V1/Reference/05_02/Video_Wan.md",
    "Video_Wan_R2V": "OpenCrew/ToolLibrary/Analysis_V1/Reference/05_02/Video_Wan_R2V.md",
    "Lipsync_SyncSo": "OpenCrew/ToolLibrary/Analysis_V1/Reference/05_02/Lipsync_SyncSo.md",
    "Lipsync_HeyGen": "OpenCrew/ToolLibrary/Analysis_V1/Reference/05_02/Lipsync_HeyGen.md",
    "Lipsync_Chanjing": "OpenCrew/ToolLibrary/Analysis_V1/Reference/05_02/Lipsync_Chanjing.md",
}
WAN_RTV_MODEL_IDS = {"wan2.7-r2v", "wan2.7-r2v-2026-06-12"}
WAN_RTV_REFERENCE_VIDEO_REL = "OpenCrew/ToolLibrary/Analysis_V1/Reference/05_02/Video_Wan_R2V.mp4"
WAN_RTV_REFERENCE_VIDEO_NAME = "Video_Wan_R2V.mp4"
WAN_RTV_MAX_VIDEO_SECONDS = 10
WAN_RTV_AUDIO_DURATION_TOLERANCE_SECONDS = 13.0
WAN_RTV_DEFAULT_SIZE = "720*1280"
MAX_SD_2_PROVIDER = "openrouter"
MAX_SD_2_MODEL = "bytedance/seedance-2.0"
MAX_SD_2_REFERENCE_MODE = "input_references"
MAX_SD_2_PROMPT_TEMPLATE = "Video_SDR2V.md"
MAX_SD_2_VIDEO_GENERATION_MODE = "openrouter_sdr2v_talking_head"
MAX_SD_2_REFERENCE_VIDEO_REL = "OpenCrew/ToolLibrary/Analysis_V1/Reference/05_02/Video_SDR2V.mp4"
MAX_SD_2_REFERENCE_VIDEO_NAME = "Video_SDR2V.mp4"
MAX_SD_2_REFERENCE_VIDEO_ROLE = "talking_head_motion_expression_reference"
MAX_SD_2_REFERENCE_VIDEO_MAX_SECONDS = 15.0
DANCE_MIMIC_WORKFLOW_ID = "dance_mimic_v1"
DANCE_MIMIC_VIDEO_GENERATION_MODE = "dance_mimic_reference_video"
DANCE_MIMIC_VIDEO_PROVIDER = "openrouter"
DANCE_MIMIC_VIDEO_MODEL = "bytedance/seedance-2.0"
DANCE_MIMIC_VIDEO_MODEL_ALIAS = "MaxSR2"
DANCE_MIMIC_REFERENCE_MODE = "input_references"
DANCE_MIMIC_PROMPT_TEMPLATE = "Video_SDR2V_DanceMimic.md"
DANCE_MIMIC_REFERENCE_VIDEO_ROLE = "dance_mimic_segment_motion_reference"
DANCE_MIMIC_TAIL_CONTINUITY_SOURCE_TYPES = {"previous_segment_tail_frame", "previous_scene_tail_frame"}
KLING_OMNI_MODEL_IDS = {"kling-v3-omni"}
CHANJING_KLING_MODEL_IDS = {"kling2.5", "kling-v2-1-master", "kling1.6"}
CHANJING_VIDUQ1_MODEL_IDS = {"viduq1"}
CHANJING_HAILUO02_MODEL_IDS = {"minimax-hailuo-02"}
CHANJING_DOUBAO_MODEL_IDS = {"doubao-seedance-1.0-pro", "doubao-seedance-1.0-lite-i2v"}
CHANJING_HAPPYHORSE_MODEL_IDS = {"happyhorse-1.0-t2v", "happyhorse-1.0-i2v", "happyhorse-1.0-r2v", "happyhorse-1.0-video-edit"}
CHANJING_VIDEO_MODEL_MODULES = {
    **{model: "video_chanjing_kling" for model in CHANJING_KLING_MODEL_IDS},
    **{model: "video_chanjing_viduq1" for model in CHANJING_VIDUQ1_MODEL_IDS},
    **{model: "video_chanjing_hailuo02" for model in CHANJING_HAILUO02_MODEL_IDS},
    **{model: "video_chanjing_doubao" for model in CHANJING_DOUBAO_MODEL_IDS},
    **{model: "video_chanjing_happyhorse" for model in CHANJING_HAPPYHORSE_MODEL_IDS},
}
KLING_OMNI_REFERENCE_VIDEO_REL = "OpenCrew/ToolLibrary/Analysis_V1/Reference/05_02/Video_Kling_Omni.mp4"
KLING_OMNI_REFERENCE_VIDEO_NAME = "Video_Kling_Omni.mp4"
VARIABLES_REL = "SessionContext/Variables.json"
STORYBOARD_REL = "SessionOutput/storyboard/srt_storyboard.json"
EDIT_STORYBOARD_REL = "SessionOutput/storyboard/koubo_storyboard_edit.json"
PLAN_REL = "SessionOutput/storyboard/video_generation_plan.json"
SCENE_PROFILE_REL = "S5_03_01_TTSBuilderG/Output/scene_profile_response.json"
STORYBOARD_WORKING_REL = "SessionOutput/storyboard/Working"
ASSET_HISTORY_REL = "SessionOutput/storyboard/assets/history"
RESULT_REL = f"{TOOL_DIR_NAME}/Report/Result.json"
EXECUTION_RESULT_REL = f"{TOOL_DIR_NAME}/Output/video_plan_execution_result.json"
SESSION_EXECUTION_RESULT_REL = "SessionOutput/storyboard/video_plan_execution_result.json"
EXECUTION_STATE_REL = f"{TOOL_DIR_NAME}/Output/video_plan_execution_state.json"
SESSION_EXECUTION_STATE_REL = "SessionOutput/storyboard/video_plan_execution_state.json"
CONFIG_TABLE = "tool_media_provider_configs"
BYTEDANCE_TTS_V1_ENDPOINT = "https://openspeech.bytedance.com/api/v1/tts"
BYTEPLUS_TTS_V3_ENDPOINT = "https://voice.ap-southeast-1.bytepluses.com/api/v3/tts/unidirectional"
BYTEPLUS_TTS_APP_KEY = "aGjiRDfUWi"
BYTEPLUS_TTS_RESOURCE_ID = "seed-tts-2.0"
STRICT_REFERENCE_ROLE_KINDS = {"host", "product"}
SECRET_VALUE_PATTERNS = (
    "postgresql://",
    "postgresql+psycopg://",
    "access_token",
    "refresh_token",
    "authorization",
    "bearer ",
    "cookie",
)
SECRET_VALUE_REGEXES = (
    re.compile(r"\bapi[_-]?key\s*[:=]", re.I),
    re.compile(r"\bpassword\s*[:=]", re.I),
)
SENSITIVE_OUTPUT_KEYS = {
    "api_key",
    "apikey",
    "password",
    "access_token",
    "refresh_token",
    "authorization",
    "cookie",
    "api_key_ciphertext",
}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".m4v"}
AUDIO_EXTS = {".wav", ".m4a", ".mp3", ".aac", ".ogg"}
TARGET_IMAGE_ASPECT = 9 / 16
DEFAULT_TARGET_IMAGE_SIZE = (720, 1280)
FFMPEG_HIGH_QUALITY_VIDEO_ARGS = ("-crf", "10", "-tune", "film")
FFMPEG_HIGH_QUALITY_VIDEO_PRESET = "slow"
GEMINI_IMAGE_MODEL_ALIASES = {
    "gemini-3.1-flash-image-preview": "gemini-3.1-flash-image",
    "gemini-3-pro-image-preview": "gemini-3-pro-image",
    "nano-banana": "gemini-2.5-flash-image",
    "nano-banana-2": "gemini-3.1-flash-image",
    "nano-banana-pro": "gemini-3-pro-image",
}


class ToolError(RuntimeError):
    pass


class ProviderTimeout(ToolError):
    pass


@dataclass(frozen=True)
class Args:
    workspace: str
    database_url: str
    max_segments: int
    force: bool
    execute_audio: bool
    execute_image: bool
    execute_video: bool
    execute_lipsync: bool
    image_provider: str
    image_model: str
    video_provider: str
    video_model: str
    lipsync_provider: str
    lipsync_model: str
    tts_provider: str
    tts_model: str
    provider_timeout_seconds: int
    execution_job_id: str = ""
    source_plan_hash: str = ""
    print_json: bool = False
    execute_audio_video_sync: bool = True


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def now_ms() -> int:
    return int(time.time() * 1000)


def text_value(value: Any) -> str:
    return str(value or "").strip()


def dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def redact_secret_text(value: str) -> str:
    text = str(value or "")
    text = re.sub(r"(postgresql(?:\+\w+)?://[^:\s/@]+:)[^@\s]+(@)", r"\1***\2", text, flags=re.I)
    text = re.sub(r"([A-Za-z][A-Za-z0-9+.-]*://[^:\s/@]+:)[^@\s]+(@)", r"\1***\2", text, flags=re.I)
    text = re.sub(r"(password\s*[:=]\s*[\"']?)[^\"',}\s]+", r"\1***", text, flags=re.I)
    text = re.sub(r"([?&]key=)[^&\s\"'}]+", r"\1***", text, flags=re.I)
    text = re.sub(r"(api[_-]?key[\"']?\s*[:=]\s*[\"']?)[^\"',}\s]+", r"\1***", text, flags=re.I)
    text = re.sub(r"(Authorization[\"']?\s*[:=]\s*[\"']?\s*Bearer\s+)[^\"',}\s]+", r"\1***", text, flags=re.I)
    text = re.sub(r"(Bearer\s+)[A-Za-z0-9._~+/=-]{12,}", r"\1***", text, flags=re.I)
    return text


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, str):
        return redact_secret_text(value)
    return value


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_safe(payload), ensure_ascii=False, indent=2), encoding="utf-8")


def plan_hash(plan: dict[str, Any]) -> str:
    payload = json_safe(plan)
    if isinstance(payload, dict):
        payload = {key: value for key, value in payload.items() if key not in {"plan_hash", "plan_run_id", "created_at"}}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ExecutionTracker:
    def __init__(self, workspace: Path, job_id: str, source_plan_hash: str) -> None:
        self.workspace = workspace
        self.state: dict[str, Any] = {
            "schema_version": "koubo_video_plan_execution_state_0.1",
            "job_id": job_id,
            "source_plan_hash": source_plan_hash,
            "source_plan_path": PLAN_REL,
            "status": "queued",
            "started_at": now_iso(),
            "updated_at": now_iso(),
            "current_segment_id": "",
            "current_step": "",
            "segments": {},
            "summary": {},
            "error": "",
        }
        self.write()

    def write(self) -> None:
        self.state["updated_at"] = now_iso()
        write_json(self.workspace / EXECUTION_STATE_REL, self.state)
        write_json(self.workspace / SESSION_EXECUTION_STATE_REL, self.state)

    def set_status(self, status: str, error: str = "") -> None:
        self.state["status"] = status
        if error:
            self.state["error"] = error
        self.write()

    def start_segment(self, segment: dict[str, Any]) -> str:
        segment_id = text_value(segment.get("segment_id"))
        self.state["status"] = "running"
        self.state["current_segment_id"] = segment_id
        self.state["current_step"] = "audio"
        self.state.setdefault("segments", {})[segment_id] = {
            "segment_id": segment_id,
            "asset_key": segment_asset_key(segment),
            "dialogue_asset_keys": segment_dialogue_asset_keys(segment),
            "dialogue_ids": segment_dialogue_asset_keys(segment),
            "status": "running",
            "steps": {},
            "outputs": {},
            "error": "",
        }
        self.write()
        return segment_id

    def step(self, segment_id: str, step: str, status: str, reason: str = "", outputs: dict[str, Any] | None = None, error: str = "") -> None:
        self.state["status"] = "running" if self.state.get("status") in {"queued", "running"} else self.state.get("status")
        self.state["current_segment_id"] = segment_id
        self.state["current_step"] = step
        segment_state = self.state.setdefault("segments", {}).setdefault(segment_id, {"segment_id": segment_id, "status": "running", "steps": {}, "outputs": {}, "error": ""})
        segment_state["status"] = "failed" if status == "failed" else "running"
        step_payload = {"status": status, "updated_at": now_iso()}
        if reason:
            step_payload["reason"] = reason
        if outputs:
            step_payload["outputs"] = outputs
            segment_state.setdefault("outputs", {}).update(outputs)
        if error:
            step_payload["error"] = error
            segment_state["error"] = error
        segment_state.setdefault("steps", {})[step] = step_payload
        self.write()

    def finish_segment(self, segment_id: str, segment_result: dict[str, Any]) -> None:
        segment_state = self.state.setdefault("segments", {}).setdefault(segment_id, {"segment_id": segment_id, "steps": {}, "outputs": {}})
        segment_state["status"] = text_value(segment_result.get("status")) or "completed"
        segment_state["outputs"] = {**dict_value(segment_state.get("outputs")), **dict_value(segment_result.get("outputs"))}
        if segment_result.get("error"):
            segment_state["error"] = text_value(segment_result.get("error"))
        self.write()

    def finish(self, status: str, summary: dict[str, Any]) -> None:
        self.state["status"] = status
        self.state["summary"] = summary
        self.state["current_segment_id"] = ""
        self.state["current_step"] = ""
        self.write()


def write_text(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")


def remove_path(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def resolve_workspace(raw_workspace: str) -> Path:
    workspace = Path(raw_workspace).expanduser() if raw_workspace else Path.cwd()
    try:
        return workspace.resolve()
    except Exception:
        return workspace.absolute()


def workspace_path(workspace: Path, path_value: str) -> Path:
    path = Path(text_value(path_value)).expanduser()
    return path if path.is_absolute() else workspace / path


def rel(workspace: Path, path: Path | str | None) -> str:
    if not path:
        return ""
    candidate = Path(path)
    try:
        return str(candidate.resolve().relative_to(workspace.resolve()))
    except Exception:
        return str(path)


def safe_name(value: str, fallback: str = "item") -> str:
    text = "".join(ch if ch.isalnum() or ch in ("_", "-", ".") else "_" for ch in text_value(value))
    text = "_".join(part for part in text.split("_") if part)
    return text or fallback


def tool_runtime_asset_path(path_value: str) -> bool:
    value = text_value(path_value).lstrip("./")
    return bool(re.match(r"^S\d+_[^/]+/(Working|Output|Prompt|Report)/", value))


def standard_dialogue_audio_path(audio_task: dict[str, Any], srt_id: str, asset_key: str) -> str:
    for candidate in (
        text_value(audio_task.get("planned_audio_path")),
        text_value(audio_task.get("existing_audio_path")),
    ):
        if candidate and not tool_runtime_asset_path(candidate) and "_SegmentAudio_Final" not in candidate and "_DialogueAudio" not in candidate:
            return candidate
    dialogue_asset_key = text_value(audio_task.get("dialogue_asset_key")) or asset_key
    return f"{STORYBOARD_WORKING_REL}/{safe_name(dialogue_asset_key, asset_key)}_Audio_Final.wav"


def dialogue_audio_task_asset_key(audio_task: dict[str, Any], segment: dict[str, Any]) -> str:
    for value in (
        audio_task.get("dialogue_asset_key"),
        audio_task.get("srt_id"),
    ):
        key = text_value(value)
        if key:
            return key
    segment_keys = segment_dialogue_asset_keys(segment)
    return segment_keys[0] if len(segment_keys) == 1 else ""


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def normalize_database_url(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1).replace("postgresql+psycopg2://", "postgresql://", 1)


def resolve_database_url(args: Args) -> str:
    return text_value(args.database_url) or os.environ.get(DEFAULT_DATABASE_URL_ENV, "").strip() or os.environ.get("DATABASE_URL", "").strip() or DEFAULT_OPENCREW_DATABASE_URL


def postgres_connect(database_url: str) -> Any:
    normalized = normalize_database_url(database_url)
    try:
        import psycopg  # type: ignore

        conn = psycopg.connect(normalized, connect_timeout=8)
        conn.execute("SET client_encoding TO 'UTF8'")
        return conn
    except ImportError:
        try:
            import psycopg2  # type: ignore
        except ImportError as exc:
            raise ToolError("PostgreSQL driver is not available. Install psycopg or psycopg2.") from exc
        conn = psycopg2.connect(normalized, connect_timeout=8)
        conn.set_client_encoding("UTF8")
        return conn


def decode_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    return str(value or "")


def parse_extra_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    text = decode_text(value).strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def default_config_from_variables(variables: dict[str, Any], kind: str) -> dict[str, Any]:
    return dict_value(variables.get(f"default_{kind}_config"))


def config_extra_from_variables(defaults: dict[str, Any]) -> dict[str, Any]:
    extra = dict_value(defaults.get("extra"))
    if extra:
        return extra
    return dict_value(defaults.get("extra_json"))


def normalize_video_audio_defaults(kind: str, provider: str, model: str, extra: dict[str, Any]) -> dict[str, Any]:
    if text_value(kind).lower() != "video":
        return extra
    normalized = dict(extra)
    provider_value = text_value(provider).lower()
    model_value = text_value(model).lower()
    if provider_value in {"kling", "klingai", "kling-ai"} or "kling" in model_value:
        normalized["sound"] = "on"
    if provider_value in {"bytedance", "seedance", "volcengine", "ark"} or ("seedance" in model_value and provider_value != "openrouter"):
        normalized["generate_audio"] = True
    return normalized


def provider_request_matches_cached_default(
    defaults: dict[str, Any],
    provider: str,
    model: str,
    provider_override: str = "",
    model_override: str = "",
) -> bool:
    default_provider = text_value(defaults.get("provider"))
    default_model = text_value(defaults.get("model"))
    if not default_provider or not default_model:
        return False
    if provider != default_provider or model != default_model:
        return False
    if provider_override and text_value(provider_override) != default_provider:
        return False
    if model_override and text_value(model_override) != default_model:
        return False
    return True


def fetch_active_provider_config(
    conn: Any,
    kind: str,
    provider: str,
    api_key_ref: str = "",
) -> tuple[Any, ...] | None:
    query = f"""
SELECT provider, model, api_key_ref, api_key_ciphertext, extra_json
FROM {CONFIG_TABLE}
WHERE kind = %s AND provider = %s AND enabled = TRUE
"""
    params: list[Any] = [kind, provider]
    if api_key_ref:
        query += " ORDER BY (api_key_ref = %s) DESC, active DESC, id ASC LIMIT 1"
        params.append(api_key_ref)
    else:
        query += " ORDER BY active DESC, id ASC LIMIT 1"
    with conn.cursor() as cursor:
        cursor.execute(query, tuple(params))
        row = cursor.fetchone()
    if not row:
        return None
    return tuple(row)


def load_provider_config(
    args: Args,
    variables: dict[str, Any],
    kind: str,
    provider_override: str = "",
    model_override: str = "",
    *,
    allow_provider_model_fallback: bool = False,
) -> dict[str, Any]:
    defaults = default_config_from_variables(variables, kind)
    provider = text_value(provider_override or defaults.get("provider"))
    model = text_value(model_override or defaults.get("model"))
    explicit_media_model_override = bool(
        kind in {"image", "video"}
        and text_value(provider_override)
        and text_value(model_override)
    )
    api_key_ref = text_value(defaults.get("api_key_ref"))
    defaults_extra = config_extra_from_variables(defaults)
    defaults_extra = normalize_video_audio_defaults(kind, provider, model, defaults_extra)
    env_provider = os.environ.get(f"OPENCREW_{kind.upper()}_PROVIDER", "").strip()
    env_model = os.environ.get(f"OPENCREW_{kind.upper()}_MODEL", "").strip()
    env_key = os.environ.get(f"OPENCREW_{kind.upper()}_API_KEY", "").strip()
    fallback_key = ""
    if kind == "tts":
        fallback_key = os.environ.get("GEMINI_API_KEY", "").strip() or os.environ.get("GOOGLE_API_KEY", "").strip()
    if env_key and (not provider or env_provider == provider) and (not model or env_model == model):
        return {"kind": kind, "provider": env_provider or provider, "model": env_model or model, "api_key": env_key, "source": "env", "extra": defaults_extra, "extra_json": defaults_extra, **defaults_extra}
    if fallback_key and provider in {"", "google", "gemini"}:
        return {"kind": kind, "provider": provider or "google", "model": model, "api_key": fallback_key, "source": "env", "extra": defaults_extra, "extra_json": defaults_extra, **defaults_extra}
    if not provider or not model:
        raise ToolError(f"Default {kind} model is not configured in {VARIABLES_REL}.")
    conn = postgres_connect(resolve_database_url(args))
    try:
        query = f"""
SELECT provider, model, api_key_ref, api_key_ciphertext, extra_json
FROM {CONFIG_TABLE}
WHERE kind = %s AND provider = %s AND enabled = TRUE
"""
        params: list[Any] = [kind, provider]
        if model:
            query += " AND model = %s"
            params.append(model)
        if api_key_ref:
            query += " ORDER BY (api_key_ref = %s) DESC, active DESC, id ASC LIMIT 1"
            params.append(api_key_ref)
        else:
            query += " ORDER BY active DESC, id ASC LIMIT 1"
        with conn.cursor() as cursor:
            cursor.execute(query, tuple(params))
            row = cursor.fetchone()
        used_provider_credential_fallback = False
        if not row:
            if (
                allow_provider_model_fallback
                or explicit_media_model_override
                or provider_request_matches_cached_default(defaults, provider, model, provider_override, model_override)
            ):
                row = fetch_active_provider_config(conn, kind, provider, api_key_ref)
                used_provider_credential_fallback = bool(row)
            if not row:
                raise ToolError(f"No enabled {kind} provider config found for {provider}/{model}.")
        row_items = list(row)
        while len(row_items) < 5:
            row_items.append("")
        row_provider, row_model, row_api_key_ref, legacy_key = [decode_text(item).strip() for item in row_items[:4]]
        use_session_config = kind == "lipsync" and provider_request_matches_cached_default(defaults, provider, model, provider_override, model_override)
        use_requested_media_model = explicit_media_model_override and used_provider_credential_fallback
        extra = defaults_extra if use_session_config else {**defaults_extra, **parse_extra_json(row_items[4])}
        row_provider_for_audio = provider if use_session_config or use_requested_media_model else decode_text(row_items[0]).strip()
        row_model_for_audio = model if use_session_config or use_requested_media_model else decode_text(row_items[1]).strip()
        extra = normalize_video_audio_defaults(kind, row_provider_for_audio, row_model_for_audio, extra)
        api_key = resolve_secret_value(row_api_key_ref, legacy_key)
        selected_provider = provider if use_session_config or use_requested_media_model else row_provider
        selected_model = model if use_session_config or use_requested_media_model else row_model
        if not selected_provider or not selected_model:
            raise ToolError(f"{kind} provider config is incomplete for {selected_provider}/{selected_model}.")
        if not api_key:
            raise ToolError(f"No API key found for {kind} provider config {row_provider}/{row_model} using api_key_ref={row_api_key_ref or '<empty>'}.")
        config = {
            "kind": kind,
            "provider": selected_provider,
            "model": selected_model,
            "api_key": api_key,
            "api_key_ref": row_api_key_ref,
            "has_api_key": True,
            "source": (
                "database_api_key_for_session_default"
                if use_session_config
                else "database_api_key_for_requested_model"
                if use_requested_media_model
                else "database"
            ),
            "extra": extra,
            "extra_json": extra,
            **extra,
        }
        if not use_session_config and not use_requested_media_model and model and row_model != model:
            config["source"] = "database_active_model_fallback"
            config["requested_model"] = model
        return config
    finally:
        conn.close()


def provider_selection(variables: dict[str, Any], kind: str, provider_override: str = "", model_override: str = "") -> dict[str, str]:
    defaults = default_config_from_variables(variables, kind)
    provider = text_value(defaults.get("provider"))
    model = text_value(defaults.get("model"))
    if not provider or not model:
        raise ToolError(f"Default {kind} model is not configured in {VARIABLES_REL}.")

    requested_provider = text_value(provider_override)
    requested_model = text_value(model_override)
    if (requested_provider and requested_provider != provider) or (requested_model and requested_model != model):
        raise ToolError(
            f"Runtime {kind} model overrides are not allowed: "
            f"use {VARIABLES_REL} default_{kind}_config ({provider}/{model})."
        )
    return {"kind": kind, "provider": provider, "model": model}


def is_dance_mimic_reference_video_segment(segment: dict[str, Any]) -> bool:
    nested = dict_value(segment.get("dance_mimic"))
    mode = text_value(segment.get("video_generation_mode") or nested.get("video_generation_mode"))
    reference_video_path = text_value(segment.get("reference_video_path") or nested.get("reference_video_path"))
    reference_mode = text_value(segment.get("reference_mode") or nested.get("reference_mode")).lower()
    prompt_template = Path(text_value(segment.get("prompt_template") or nested.get("prompt_template"))).name
    reference_role = text_value(segment.get("reference_video_role") or nested.get("reference_video_role"))
    workflow_id = text_value(segment.get("workflow_id") or nested.get("workflow_id"))
    return bool(
        workflow_id == DANCE_MIMIC_WORKFLOW_ID
        or mode.startswith("dance_mimic")
        or prompt_template == DANCE_MIMIC_PROMPT_TEMPLATE
        or reference_role == DANCE_MIMIC_REFERENCE_VIDEO_ROLE
        or (reference_video_path and reference_mode == DANCE_MIMIC_REFERENCE_MODE)
    )


def dance_mimic_video_selection(selection: dict[str, str], segment: dict[str, Any]) -> dict[str, str]:
    if not is_dance_mimic_reference_video_segment(segment):
        return selection
    return {
        **selection,
        "kind": "video",
        "provider": DANCE_MIMIC_VIDEO_PROVIDER,
        "model": DANCE_MIMIC_VIDEO_MODEL,
        "model_alias": DANCE_MIMIC_VIDEO_MODEL_ALIAS,
        "reference_mode": DANCE_MIMIC_REFERENCE_MODE,
        "prompt_template": DANCE_MIMIC_PROMPT_TEMPLATE,
        "video_generation_mode": DANCE_MIMIC_VIDEO_GENERATION_MODE,
    }


def video_selection_for_segment(variables: dict[str, Any], args: Args, segment: dict[str, Any]) -> dict[str, str]:
    selected = provider_selection(variables, "video", getattr(args, "video_provider", ""), getattr(args, "video_model", ""))
    selected = dance_mimic_video_selection(selected, segment)
    if is_dance_mimic_reference_video_segment(segment):
        return selected
    if is_openrouter_max_sd_2_model(selected.get("provider", ""), selected.get("model", "")) and not segment_is_cutaway(segment):
        return {
            **selected,
            "reference_mode": MAX_SD_2_REFERENCE_MODE,
            "prompt_template": MAX_SD_2_PROMPT_TEMPLATE,
            "video_generation_mode": MAX_SD_2_VIDEO_GENERATION_MODE,
            "reference_video_role": MAX_SD_2_REFERENCE_VIDEO_ROLE,
        }
    return selected


def apply_dance_mimic_video_config(config: dict[str, Any], segment: dict[str, Any]) -> dict[str, Any]:
    if not is_dance_mimic_reference_video_segment(segment):
        return config
    dance_mimic = dict_value(segment.get("dance_mimic"))
    privacy_grid_mode = bool(segment.get("privacy_grid_mode") or dance_mimic.get("privacy_grid_mode"))
    return {
        **config,
        "provider": DANCE_MIMIC_VIDEO_PROVIDER,
        "model": DANCE_MIMIC_VIDEO_MODEL,
        "model_alias": DANCE_MIMIC_VIDEO_MODEL_ALIAS,
        "reference_mode": DANCE_MIMIC_REFERENCE_MODE,
        "prompt_template": DANCE_MIMIC_PROMPT_TEMPLATE,
        "video_generation_mode": DANCE_MIMIC_VIDEO_GENERATION_MODE,
        "dance_mimic_reference_video": True,
        "privacy_grid_mode": privacy_grid_mode,
        "strict_input_references": privacy_grid_mode,
        "require_r2_public_assets": privacy_grid_mode,
        "generate_audio": False,
    }


def load_video_provider_config_for_segment(args: Args, variables: dict[str, Any], segment: dict[str, Any]) -> dict[str, Any]:
    selected = video_selection_for_segment(variables, args, segment)
    config = load_provider_config(
        args,
        variables,
        "video",
        selected.get("provider", ""),
        selected.get("model", ""),
        allow_provider_model_fallback=is_dance_mimic_reference_video_segment(segment),
    )
    config = apply_dance_mimic_video_config(config, segment)
    if (
        not is_dance_mimic_reference_video_segment(segment)
        and is_openrouter_max_sd_2_model(selected.get("provider", ""), selected.get("model", ""))
        and not segment_is_cutaway(segment)
    ):
        config = {
            **config,
            "reference_mode": MAX_SD_2_REFERENCE_MODE,
            "prompt_template": MAX_SD_2_PROMPT_TEMPLATE,
            "video_generation_mode": MAX_SD_2_VIDEO_GENERATION_MODE,
            "reference_video_role": MAX_SD_2_REFERENCE_VIDEO_ROLE,
        }
    if text_value(config.get("provider")).lower() in {"openrouter", "openrouter-video"}:
        module = video_module_for(text_value(config.get("provider")), text_value(config.get("model")))
        apply_runtime_config = getattr(module, "apply_public_asset_runtime_config", None)
        if callable(apply_runtime_config):
            config = apply_runtime_config(config)
    return config


def image_references_need_strict_role_binding(image_references: list[dict[str, str]]) -> bool:
    kinds = {
        text_value(item.get("kind"))
        for item in image_references
        if isinstance(item, dict) and text_value(item.get("kind"))
    }
    return bool(kinds & STRICT_REFERENCE_ROLE_KINDS) and ("target_frame" in kinds or len(kinds) > 1)


def image_provider_selection_for_references(args: Args, variables: dict[str, Any], image_references: list[dict[str, str]]) -> tuple[dict[str, str], dict[str, Any]]:
    selected = provider_selection(variables, "image", getattr(args, "image_provider", ""), getattr(args, "image_model", ""))
    if not image_references_need_strict_role_binding(image_references):
        return selected, {}
    return selected, {
        "provider": f"{selected['provider']}/{selected['model']}".rstrip("/"),
        "task_type": "strict_multi_reference_replacement",
        "reason": "selected_image_provider_used_without_automatic_fallback",
        "requires_manual_quality_review": True,
        "reference_roles": [
            text_value(item.get("role") or item.get("kind"))
            for item in image_references
            if isinstance(item, dict) and text_value(item.get("role") or item.get("kind"))
        ],
    }


def import_executor_module(module_name: str) -> Any:
    try:
        return importlib.import_module(f"OpenCrew.ToolLibrary.Analysis_V1.video_plan_executor_modules.{module_name}")
    except ModuleNotFoundError:
        return importlib.import_module(f"ToolLibrary.Analysis_V1.video_plan_executor_modules.{module_name}")


def image_module_for(provider: str, model: str = "") -> Any:
    provider_value = text_value(provider).lower()
    model_value = text_value(model).lower()
    if provider_value in {"gemini", "google"} or "gemini" in model_value or "banana" in model_value:
        return import_executor_module("image_gemini")
    if provider_value in {"xai", "grok"} or "grok" in model_value:
        return import_executor_module("image_grok")
    if provider_value in {"openai", "gpt"} or "gpt" in model_value:
        return import_executor_module("image_gpt")
    raise ToolError(f"Unsupported image provider/module: {provider}/{model}")


def video_module_for(provider: str, model: str = "") -> Any:
    provider_value = text_value(provider).lower()
    model_value = text_value(model).lower()
    if is_chanjing_video_model(provider_value, model_value):
        return import_executor_module(CHANJING_VIDEO_MODEL_MODULES[model_value])
    if is_wan_rtv_model(provider_value, model_value):
        return import_executor_module("video_wan_rtv")
    if provider_value in {"xai", "grok"} or "grok" in model_value:
        return import_executor_module("video_grok")
    if provider_value in {"gemini", "google"} or "veo" in model_value or "gemini" in model_value:
        return import_executor_module("video_gemini")
    if provider_value in {"openrouter", "openrouter-video"}:
        return import_executor_module("video_openrouter")
    if provider_value in {"bytedance", "seedance", "volcengine", "ark"} or "seedance" in model_value:
        return import_executor_module("video_seedance")
    if provider_value in {"kling", "klingai"} or "kling" in model_value:
        return import_executor_module("video_kling")
    if provider_value in {"wan", "dashscope"} or "wan" in model_value:
        return import_executor_module("video_wan")
    if provider_value in {"openai", "gpt"} or "gpt" in model_value:
        return import_executor_module("video_gpt")
    raise ToolError(f"Unsupported video provider/module: {provider}/{model}")


def video_module_basename(module: Any) -> str:
    return text_value(getattr(module, "__name__", "")).rsplit(".", 1)[-1]


def ensure_dance_mimic_openrouter_route(config: dict[str, Any], module: Any) -> None:
    if not config.get("dance_mimic_reference_video"):
        return
    if text_value(config.get("provider")).lower() != DANCE_MIMIC_VIDEO_PROVIDER or video_module_basename(module) != "video_openrouter":
        raise ToolError(
            "dance_mimic_video_provider_mismatch: "
            f"DanceMimic reference-video segments must use video_openrouter, got "
            f"{text_value(config.get('provider'))}/{text_value(config.get('model'))} -> {video_module_basename(module) or '<unknown>'}."
        )
    config["generate_audio"] = False


def is_wan_rtv_model(provider: str, model: str = "") -> bool:
    provider_value = text_value(provider).lower()
    model_value = text_value(model).lower()
    return model_value in WAN_RTV_MODEL_IDS and (provider_value in {"", "wan", "dashscope"} or "wan" in provider_value)


def is_kling_omni_model(provider: str, model: str = "") -> bool:
    provider_value = text_value(provider).lower()
    model_value = text_value(model).lower()
    return model_value in KLING_OMNI_MODEL_IDS and (provider_value in {"", "kling", "klingai", "kling-ai"} or "kling" in provider_value)


def is_openrouter_max_sd_2_model(provider: str, model: str = "") -> bool:
    return text_value(provider).lower() == MAX_SD_2_PROVIDER and text_value(model).lower() == MAX_SD_2_MODEL


def is_chanjing_kling_model(provider: str, model: str = "") -> bool:
    provider_value = text_value(provider).lower()
    model_value = text_value(model).lower()
    return provider_value in {"chanjing", "chanjing.cc", "cj"} and model_value in CHANJING_KLING_MODEL_IDS


def is_chanjing_video_model(provider: str, model: str = "") -> bool:
    provider_value = text_value(provider).lower()
    model_value = text_value(model).lower()
    return provider_value in {"chanjing", "chanjing.cc", "cj"} and model_value in CHANJING_VIDEO_MODEL_MODULES


def selected_video_is_wan_rtv(variables: dict[str, Any], provider_override: str = "", model_override: str = "") -> bool:
    try:
        selected = provider_selection(variables, "video", provider_override, model_override)
    except Exception:
        return False
    return is_wan_rtv_model(selected.get("provider", ""), selected.get("model", ""))


def selected_video_is_openrouter_max_sd_2(variables: dict[str, Any], provider_override: str = "", model_override: str = "") -> bool:
    try:
        selected = provider_selection(variables, "video", provider_override, model_override)
    except Exception:
        return False
    return is_openrouter_max_sd_2_model(selected.get("provider", ""), selected.get("model", ""))


def should_apply_max_sd_2_oral_privacy_grid(
    variables: dict[str, Any],
    storyboard: dict[str, Any],
    segment: dict[str, Any],
    provider_override: str = "",
    model_override: str = "",
) -> bool:
    return _should_apply_max_sd_2_oral_privacy_grid(
        variables,
        storyboard,
        segment,
        provider_override,
        model_override,
    )


def selected_video_is_kling_omni(variables: dict[str, Any], provider_override: str = "", model_override: str = "") -> bool:
    try:
        selected = provider_selection(variables, "video", provider_override, model_override)
    except Exception:
        return False
    return is_kling_omni_model(selected.get("provider", ""), selected.get("model", ""))


def is_r2v_video_model(provider: str, model: str = "") -> bool:
    provider_value = text_value(provider).lower()
    model_value = text_value(model).lower()
    return is_wan_rtv_model(provider_value, model_value) or "r2v" in model_value


def wan_rtv_video_size(config: dict[str, Any]) -> str:
    value = text_value(config.get("size") or config.get("video_size") or config.get("default_size") or WAN_RTV_DEFAULT_SIZE)
    normalized = value.lower().replace("x", "*").replace("×", "*")
    return normalized if "*" in normalized else WAN_RTV_DEFAULT_SIZE


def wan_rtv_video_size_for_image(image_path: Path, config: dict[str, Any]) -> str:
    if image_path.exists() and image_path.is_file() and Image is not None:
        try:
            with Image.open(image_path) as image:
                width, height = image.size
            if width > height:
                return "1920*1080"
            if height > width:
                return "1080*1920"
        except Exception:
            pass
    return wan_rtv_video_size(config)


def lipsync_module_for(provider: str, model: str = "") -> Any:
    provider_value = text_value(provider).lower()
    if provider_value in {"sync", "sync.so", "sync_so", "syncso"}:
        return import_executor_module("lipsync_syncso")
    if provider_value in {"heygen", "hey-gen"}:
        return import_executor_module("lipsync_heygen")
    if provider_value in {"kling", "klingai", "kling-ai"}:
        return import_executor_module("lipsync_kling")
    if provider_value in {"chanjing", "chanjing.cc", "cj"}:
        return import_executor_module("lipsync_chanjing")
    raise ToolError(f"Unsupported lipsync provider/module: {provider}/{model}")


def is_chanjing_lipsync_config(config: dict[str, Any]) -> bool:
    return text_value(config.get("provider")).lower() in {"chanjing", "chanjing.cc", "cj"}


def is_kling_lipsync_config(config: dict[str, Any]) -> bool:
    return text_value(config.get("provider")).lower() in {"kling", "klingai", "kling-ai"}


def is_heygen_lipsync_config(config: dict[str, Any]) -> bool:
    return text_value(config.get("provider")).lower() in {"heygen", "hey-gen"}


HEYGEN_LIPSYNC_MAX_DURATION_DIFFERENCE_RATIO = 0.15
HEYGEN_LIPSYNC_FIT_TRIGGER_RATIO = 0.145


def lipsync_audio_fit_mode(lipsync_config: dict[str, Any], video_config: dict[str, Any]) -> str:
    if is_kling_lipsync_config(lipsync_config) or is_chanjing_lipsync_config(lipsync_config):
        return "video_locked" if is_r2v_video_model(video_config.get("provider", ""), video_config.get("model", "")) else ""
    if is_heygen_lipsync_config(lipsync_config):
        return "heygen_provider_limit"
    return ""


def should_fit_lipsync_audio_to_video(lipsync_config: dict[str, Any], video_config: dict[str, Any]) -> bool:
    return bool(lipsync_audio_fit_mode(lipsync_config, video_config))


def normalize_module_error(exc: Exception) -> Exception:
    if exc.__class__.__name__ == "ProviderTimeout":
        return ProviderTimeout(str(exc))
    if exc.__class__.__name__ == "ToolError":
        return ToolError(str(exc))
    return exc


def redact_config(config: dict[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key, value in config.items():
        if key == "api_key":
            redacted["secret_length"] = len(text_value(value))
        else:
            redacted[key] = value
    return redacted


def ensure_provider_proxy_available(provider: str, policy: str) -> None:
    if policy != "mihomo":
        return
    proxy_url = os.environ.get("OPENCREW_MIHOMO_PROXY_URL", "http://127.0.0.1:7890").strip()
    if not proxy_url:
        return
    parsed = urllib.parse.urlparse(proxy_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=2):
            return
    except OSError as exc:
        raise ToolError(
            f"{provider} requires mihomo proxy {proxy_url}, but the proxy is not listening: {exc}. "
            "Start mihomo in Connection > mihomo, or set OPENCREW_MIHOMO_PROXY_URL to a reachable proxy."
        ) from exc


def post_json_request(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int = 120) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", "Accept": "application/json", **headers}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            body = res.read().decode("utf-8", errors="replace")
            return json.loads(body) if body else {}
    except TimeoutError as exc:
        raise ProviderTimeout(f"POST {redact_secret_text(url)} timed out") from exc
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:3000]
        raise ToolError(f"POST {redact_secret_text(url)} failed: HTTP {exc.code}: {redact_secret_text(detail)}") from exc
    except urllib.error.URLError as exc:
        raise ToolError(f"POST {redact_secret_text(url)} failed: {exc.reason}") from exc


def get_json_request(url: str, headers: dict[str, str], timeout: int = 120) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"Accept": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return json.loads(res.read().decode("utf-8", errors="replace"))
    except TimeoutError as exc:
        raise ProviderTimeout(f"GET {redact_secret_text(url)} timed out") from exc
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:3000]
        raise ToolError(f"GET {redact_secret_text(url)} failed: HTTP {exc.code}: {redact_secret_text(detail)}") from exc
    except urllib.error.URLError as exc:
        raise ToolError(f"GET {redact_secret_text(url)} failed: {exc.reason}") from exc


def post_multipart_request(url: str, fields: dict[str, str], files: list[tuple[str, Path]], headers: dict[str, str], timeout: int = 120) -> dict[str, Any]:
    boundary = f"----OpenCrewBoundary{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend([f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(), str(value).encode("utf-8"), b"\r\n"])
    for name, path in files:
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        chunks.extend([f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="{name}"; filename="{path.name}"\r\n'.encode(), f"Content-Type: {mime}\r\n\r\n".encode(), path.read_bytes(), b"\r\n"])
    chunks.append(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(url, data=b"".join(chunks), headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "Accept": "application/json", **headers}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            body = res.read().decode("utf-8", errors="replace")
            return json.loads(body) if body else {}
    except TimeoutError as exc:
        raise ProviderTimeout(f"POST {redact_secret_text(url)} timed out") from exc
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:3000]
        raise ToolError(f"POST {redact_secret_text(url)} failed: HTTP {exc.code}: {redact_secret_text(detail)}") from exc
    except urllib.error.URLError as exc:
        raise ToolError(f"POST {redact_secret_text(url)} failed: {exc.reason}") from exc


def download_binary(url: str, output_path: Path, headers: dict[str, str] | None = None, timeout: int = 600) -> None:
    last_error: Exception | None = None
    for attempt in range(1, 4):
        req = urllib.request.Request(url, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                with output_path.open("wb") as handle:
                    shutil.copyfileobj(res, handle)
            return
        except TimeoutError as exc:
            raise ProviderTimeout(f"Download timed out: {redact_secret_text(url)}") from exc
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:3000]
            raise ToolError(f"Download failed: HTTP {exc.code}: {redact_secret_text(detail)}") from exc
        except (urllib.error.URLError, ssl.SSLError, ConnectionError) as exc:
            last_error = exc
            output_path.unlink(missing_ok=True)
            if attempt < 3:
                time.sleep(2 * attempt)
                continue
    raise ToolError(f"Download failed after 3 attempts: {redact_secret_text(str(last_error))}")


def first_url(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("url", "video_url", "audio_url", "download_url", "uri", "outputUrl", "output_url"):
            value = payload.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value
        for value in payload.values():
            found = first_url(value)
            if found:
                return found
    if isinstance(payload, list):
        for item in payload:
            found = first_url(item)
            if found:
                return found
    return ""


def operation_done(payload: dict[str, Any]) -> bool:
    status = text_value(payload.get("status") or payload.get("task_status") or payload.get("state")).lower()
    return status in {"succeeded", "success", "completed", "done", "finish", "finished"}


def operation_failed(payload: dict[str, Any]) -> str:
    status = text_value(payload.get("status") or payload.get("task_status") or payload.get("state")).lower()
    if status in {"failed", "error", "cancelled", "canceled", "rejected"}:
        return json.dumps(payload, ensure_ascii=False)[:1200]
    return ""


def image_inline_payload(path: Path | None) -> dict[str, str] | None:
    if not path or not path.exists():
        return None
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return {"mimeType": mime, "bytesBase64Encoded": base64.b64encode(path.read_bytes()).decode("ascii")}


def normalize_gemini_image_model(model: str) -> str:
    value = text_value(model)
    return GEMINI_IMAGE_MODEL_ALIASES.get(value.lower(), value)


def gemini_image_generate_payload(prompt: str, reference_paths: list[Path]) -> dict[str, Any]:
    parts: list[dict[str, Any]] = [{"text": prompt}]
    for path in reference_paths:
        inline = image_inline_payload(path)
        if inline:
            parts.append({"inline_data": {"mime_type": inline["mimeType"], "data": inline["bytesBase64Encoded"]}})
    return {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"responseModalities": ["IMAGE"]},
    }


def image_b64_from_response(provider: str, payload: dict[str, Any]) -> str:
    if provider in {"openai", "xai"}:
        for item in payload.get("data") or []:
            if isinstance(item, dict) and item.get("b64_json"):
                return str(item["b64_json"])
    if provider in {"gemini", "google"}:
        for candidate in payload.get("candidates") or []:
            content = dict_value(candidate.get("content")) if isinstance(candidate, dict) else {}
            for part in list_value(content.get("parts")):
                inline = dict_value(part.get("inlineData") or part.get("inline_data")) if isinstance(part, dict) else {}
                if inline.get("data"):
                    return str(inline["data"])
    raise ToolError(f"Image provider response did not include image data ({image_response_summary(payload)})")


def image_response_summary(payload: dict[str, Any]) -> str:
    prompt_feedback = dict_value(payload.get("promptFeedback") or payload.get("prompt_feedback"))
    details: list[str] = []
    block_reason = text_value(prompt_feedback.get("blockReason") or prompt_feedback.get("block_reason"))
    if block_reason:
        details.append(f"prompt_block_reason={block_reason}")
    finish_reasons: list[str] = []
    text_parts: list[str] = []
    thought_parts = 0
    inline_parts = 0
    for candidate in list_value(payload.get("candidates")):
        if not isinstance(candidate, dict):
            continue
        finish_reason = text_value(candidate.get("finishReason") or candidate.get("finish_reason"))
        if finish_reason:
            finish_reasons.append(finish_reason)
        content = dict_value(candidate.get("content"))
        for part in list_value(content.get("parts")):
            if not isinstance(part, dict):
                continue
            if part.get("thoughtSignature") or part.get("thought_signature"):
                thought_parts += 1
            inline = dict_value(part.get("inlineData") or part.get("inline_data"))
            if inline:
                inline_parts += 1
            text = text_value(part.get("text"))
            if text:
                text_parts.append(text[:160])
    if finish_reasons:
        details.append("finish_reasons=" + ",".join(finish_reasons[:3]))
    if inline_parts:
        details.append(f"inline_parts_without_data={inline_parts}")
    if thought_parts:
        details.append(f"thought_parts={thought_parts}")
    if text_parts:
        details.append("text=" + json.dumps(text_parts[:2], ensure_ascii=False))
    return "; ".join(details) or "provider returned no inlineData image parts"


def image_dimensions(path: Path) -> tuple[int, int]:
    if Image is None:
        raise ToolError("Pillow is required to validate generated image dimensions.")
    try:
        with Image.open(path) as image:
            return int(image.width), int(image.height)
    except Exception as exc:
        raise ToolError(f"invalid_image_file: {path}: {exc}") from exc


def target_image_size_from_frame(target_frame_path: Path | None) -> tuple[int, int]:
    if target_frame_path and target_frame_path.exists():
        width, height = image_dimensions(target_frame_path)
        if width > 0 and height > 0:
            return width, height
    return DEFAULT_TARGET_IMAGE_SIZE


def normalize_image_to_target_aspect(image_path: Path, target_frame_path: Path | None = None) -> dict[str, Any]:
    if Image is None:
        raise ToolError("Pillow is required to normalize generated image dimensions.")
    target_width, target_height = target_image_size_from_frame(target_frame_path)
    target_ratio = target_width / target_height if target_width and target_height else TARGET_IMAGE_ASPECT
    with Image.open(image_path) as opened:
        original_mode = opened.mode
        image = opened.convert("RGBA" if "A" in opened.getbands() else "RGB")
        original_width, original_height = image.size
        if original_width <= 0 or original_height <= 0:
            raise ToolError(f"invalid_image_file: generated image has invalid dimensions: {image_path}")
        original_ratio = original_width / original_height
        crop_box = (0, 0, original_width, original_height)
        if abs(original_ratio - target_ratio) > 0.002:
            if original_ratio > target_ratio:
                crop_width = max(1, int(round(original_height * target_ratio)))
                left = max(0, (original_width - crop_width) // 2)
                crop_box = (left, 0, left + crop_width, original_height)
            else:
                crop_height = max(1, int(round(original_width / target_ratio)))
                top = max(0, (original_height - crop_height) // 2)
                crop_box = (0, top, original_width, top + crop_height)
            image = image.crop(crop_box)
        resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
        if image.size != (target_width, target_height):
            image = image.resize((target_width, target_height), resampling)
        image.save(image_path)
    final_width, final_height = image_dimensions(image_path)
    final_ratio = final_width / final_height if final_height else 0
    if abs(final_ratio - target_ratio) > 0.002:
        raise ToolError(f"generated_image_aspect_invalid: expected {target_width}x{target_height}, got {final_width}x{final_height}: {image_path}")
    return {
        "target_aspect": "16:9" if target_width > target_height else "9:16",
        "target_width": target_width,
        "target_height": target_height,
        "source_target_frame": str(target_frame_path) if target_frame_path else "",
        "original_width": original_width,
        "original_height": original_height,
        "original_aspect": round(original_ratio, 6),
        "original_mode": original_mode,
        "crop_box": list(crop_box),
        "final_width": final_width,
        "final_height": final_height,
        "final_aspect": round(final_ratio, 6),
        "normalized": (original_width, original_height) != (final_width, final_height) or crop_box != (0, 0, original_width, original_height),
    }


def generate_image_with_provider(config: dict[str, Any], prompt_path: Path, output_path: Path, reference_paths: list[Path], timeout_seconds: int) -> dict[str, Any]:
    module = image_module_for(text_value(config.get("provider")), text_value(config.get("model")))
    try:
        return module.generate({
            "config": config,
            "reference_paths": [str(path) for path in reference_paths],
            "timeout_seconds": timeout_seconds,
        }, prompt_path, output_path)
    except Exception as exc:
        normalized = normalize_module_error(exc)
        if normalized is not exc:
            raise normalized from exc
        raise


def provider_video_seconds(config: dict[str, Any], duration: float, audio_duration: float | None = None) -> int:
    provider = text_value(config.get("provider")).lower()
    model = text_value(config.get("model")).lower()
    duration_value = safe_float(audio_duration, 0.0) or safe_float(duration, 4.0)
    if is_wan_rtv_model(provider, model) and duration_value < WAN_RTV_MAX_VIDEO_SECONDS:
        return min(WAN_RTV_MAX_VIDEO_SECONDS, max(3, int(math.ceil(duration_value))))
    seconds = max(1, int(round(duration_value or 4)))
    if provider == "xai":
        return min(15, seconds)
    if provider == "wan":
        return min(10, max(3, seconds))
    if is_chanjing_video_model(provider, model):
        for allowed in (5, 6, 10):
            if seconds <= allowed:
                return allowed
        return 10
    if provider in {"kling", "klingai"} or "kling" in model:
        return min(15, max(3, seconds))
    return min(8, max(4, seconds))


def dashscope_upload_file(api_key: str, model: str, path: Path) -> str:
    query = urllib.parse.urlencode({"action": "getPolicy", "model": model})
    policy = get_json_request(f"https://dashscope.aliyuncs.com/api/v1/uploads?{query}", {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
    policy_data = dict_value(policy.get("data"))
    upload_host = text_value(policy_data.get("upload_host"))
    upload_dir = text_value(policy_data.get("upload_dir"))
    if not upload_host or not upload_dir:
        raise ToolError(f"DashScope upload policy is missing upload_host/upload_dir: {json.dumps(policy, ensure_ascii=False)[:1000]}")
    key = f"{upload_dir.rstrip('/')}/{path.name}"
    boundary = f"----OpenCrewDashScope{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    fields = {
        "OSSAccessKeyId": text_value(policy_data.get("oss_access_key_id")),
        "Signature": text_value(policy_data.get("signature")),
        "policy": text_value(policy_data.get("policy")),
        "x-oss-object-acl": text_value(policy_data.get("x_oss_object_acl") or "private"),
        "x-oss-forbid-overwrite": text_value(policy_data.get("x_oss_forbid_overwrite") or "true"),
        "key": key,
        "success_action_status": "200",
    }
    for name, value in fields.items():
        chunks.extend([f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(), value.encode("utf-8"), b"\r\n"])
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    chunks.extend([f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'.encode(), f"Content-Type: {mime}\r\n\r\n".encode(), path.read_bytes(), b"\r\n", f"--{boundary}--\r\n".encode()])
    req = urllib.request.Request(upload_host, data=b"".join(chunks), headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=180) as res:
            if res.status != 200:
                raise ToolError(f"DashScope upload failed: HTTP {res.status}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2000]
        raise ToolError(f"DashScope upload failed: HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ToolError(f"DashScope upload failed: {exc.reason}") from exc
    return f"oss://{key}"


def generate_video_with_provider(
    config: dict[str, Any],
    prompt_path: Path,
    output_path: Path,
    reference_images: list[Path],
    duration: float,
    timeout_seconds: int,
    provider_task_state_path: Path | None = None,
    audio_duration: float | None = None,
    reference_videos: list[Path] | None = None,
    reference_audios: list[Path] | None = None,
    requested_aspect: str = "",
) -> dict[str, Any]:
    aspect = normalize_storyboard_video_aspect(requested_aspect or config.get("requested_aspect") or config.get("aspect_ratio"))
    provider_config = provider_config_for_video_aspect(config, aspect)
    module = video_module_for(text_value(provider_config.get("provider")), text_value(provider_config.get("model")))
    ensure_dance_mimic_openrouter_route(provider_config, module)
    context: dict[str, Any] = {
        "config": provider_config,
        "reference_images": [str(path) for path in reference_images],
        "duration_seconds": duration,
        "timeout_seconds": timeout_seconds,
        "aspect": aspect,
        "aspect_ratio": aspect,
        "requested_aspect": aspect,
    }
    if reference_videos:
        context["reference_videos"] = [str(path) for path in reference_videos]
    if reference_audios:
        context["reference_audios"] = [str(path) for path in reference_audios]
    if audio_duration is not None:
        context["audio_duration_seconds"] = audio_duration
    if not reference_videos and is_wan_rtv_model(text_value(provider_config.get("provider")), text_value(provider_config.get("model"))):
        context["reference_videos"] = [str(output_path.parent / WAN_RTV_REFERENCE_VIDEO_NAME)]
    if not reference_videos and is_kling_omni_model(text_value(provider_config.get("provider")), text_value(provider_config.get("model"))):
        context["reference_videos"] = [str(output_path.parent / KLING_OMNI_REFERENCE_VIDEO_NAME)]
    if provider_task_state_path is not None:
        context["provider_task_state_path"] = str(provider_task_state_path)
    try:
        return module.generate(context, prompt_path, output_path)
    except Exception as exc:
        normalized = normalize_module_error(exc)
        if normalized is not exc:
            raise normalized from exc
        raise


def sync_output_url(payload: dict[str, Any]) -> str:
    url = text_value(payload.get("outputUrl") or payload.get("output_url"))
    if url:
        return url
    for segment in list_value(payload.get("segments")):
        if isinstance(segment, dict):
            candidate = text_value(segment.get("segmentOutputUrl") or segment.get("segment_output_url"))
            if candidate:
                return candidate
    return first_url(payload)


def run_lipsync_with_provider(config: dict[str, Any], video_path: Path, audio_path: Path, output_path: Path, request_path: Path, status_path: Path, create_response_path: Path, timeout_seconds: int, prompt_path: Path | None = None, segment: dict[str, Any] | None = None) -> dict[str, Any]:
    module = lipsync_module_for(text_value(config.get("provider")), text_value(config.get("model")))
    if prompt_path is None:
        prompt_path = request_path
    try:
        return module.generate({
            "config": config,
            "segment": segment or {},
            "video_path": str(video_path),
            "audio_path": str(audio_path),
            "output_path": str(output_path),
            "request_path": str(request_path),
            "status_path": str(status_path),
            "create_response_path": str(create_response_path),
            "timeout_seconds": timeout_seconds,
        }, prompt_path, output_path)
    except Exception as exc:
        normalized = normalize_module_error(exc)
        if normalized is not exc:
            raise normalized from exc
        raise


def wav_from_pcm(raw: bytes, sample_rate: int = 24000, channels: int = 1, sample_width: int = 2) -> bytes:
    import io

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(sample_width)
        writer.setframerate(sample_rate)
        writer.writeframes(raw)
    return buffer.getvalue()


def looks_like_byteplus_tts_api_key(value: str) -> bool:
    raw = text_value(value)
    return bool(re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", raw))


def bytedance_tts_credentials(secret: str, config: dict[str, Any]) -> dict[str, str]:
    app_id = text_value(config.get("app_id") or config.get("appid"))
    access_token = ""
    byteplus_api_key = text_value(config.get("byteplus_api_key") or config.get("x_api_key"))
    auth_mode = text_value(config.get("auth_mode")).lower()
    raw = text_value(secret)
    if raw.startswith("{"):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, dict):
            app_id = text_value(payload.get("app_id") or payload.get("appid") or app_id)
            access_token = text_value(payload.get("access_token") or payload.get("token"))
            byteplus_api_key = text_value(payload.get("byteplus_api_key") or payload.get("x_api_key") or byteplus_api_key)
            if not app_id and not access_token:
                byteplus_api_key = text_value(payload.get("api_key") or byteplus_api_key)
            elif not access_token:
                access_token = text_value(payload.get("api_key"))
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
        raise ToolError("ByteDance TTS requires app_id for legacy credentials. Save a BytePlus API Key, appid:access_token, or JSON credentials.")
    if not access_token:
        raise ToolError("ByteDance TTS requires access_token. Save credentials as appid:access_token or JSON with app_id/access_token.")
    return {"app_id": app_id, "access_token": access_token}


def bytedance_tts_request_payload(config: dict[str, Any], prompt_text: str, voice: str, credentials: dict[str, str]) -> dict[str, Any]:
    audio: dict[str, Any] = {
        "voice_type": voice,
        "encoding": text_value(config.get("encoding") or "wav"),
        "speed_ratio": safe_float(config.get("speed_ratio"), 1.0),
        "rate": int(safe_float(config.get("sample_rate") or config.get("rate"), 24000)),
    }
    request: dict[str, Any] = {"reqid": uuid.uuid4().hex, "text": prompt_text, "operation": "query"}
    model = text_value(config.get("model"))
    if model:
        request["model"] = model
    return {
        "app": {"appid": credentials["app_id"], "token": credentials["access_token"], "cluster": text_value(config.get("cluster") or "volcano_tts")},
        "user": {"uid": text_value(config.get("uid") or "opencrew")},
        "audio": audio,
        "request": request,
    }


def byteplus_tts_format(config: dict[str, Any]) -> str:
    value = text_value(config.get("byteplus_format") or config.get("format")).lower()
    return value if value in {"mp3", "ogg_opus", "pcm"} else "mp3"


def byteplus_tts_endpoint(config: dict[str, Any]) -> str:
    endpoint = text_value(config.get("byteplus_endpoint"))
    if endpoint:
        return endpoint
    endpoint = text_value(config.get("endpoint"))
    return endpoint if "/api/v3/" in endpoint else BYTEPLUS_TTS_V3_ENDPOINT


def byteplus_tts_headers(credentials: dict[str, str], config: dict[str, Any]) -> dict[str, str]:
    return {
        "X-Api-Key": credentials["api_key"],
        "X-Api-Resource-Id": text_value(config.get("byteplus_resource_id") or config.get("resource_id") or BYTEPLUS_TTS_RESOURCE_ID),
        "X-Api-App-Key": text_value(config.get("byteplus_app_key") or config.get("app_key") or BYTEPLUS_TTS_APP_KEY),
        "X-Api-Request-Id": uuid.uuid4().hex,
        "Connection": "keep-alive",
    }


def byteplus_tts_request_payload(config: dict[str, Any], prompt_text: str, voice: str) -> dict[str, Any]:
    additions: dict[str, Any] = {
        "disable_markdown_filter": True,
        "enable_language_detector": True,
        "enable_latex_tn": True,
        "disable_default_bit_rate": True,
        "cache_config": {"text_type": 1, "use_cache": True},
    }
    configured_additions = config.get("byteplus_additions", config.get("additions"))
    if isinstance(configured_additions, dict):
        additions.update(configured_additions)
    elif isinstance(configured_additions, str) and configured_additions.strip():
        try:
            parsed = json.loads(configured_additions)
        except json.JSONDecodeError:
            parsed = {}
        if isinstance(parsed, dict):
            additions.update(parsed)
    return {
        "user": {"uid": text_value(config.get("uid") or "opencrew")},
        "req_params": {
            "text": prompt_text,
            "speaker": voice,
            "audio_params": {
                "format": byteplus_tts_format(config),
                "sample_rate": int(safe_float(config.get("sample_rate") or config.get("rate"), 24000)),
            },
            "additions": json.dumps(additions, ensure_ascii=False),
        },
    }


def post_byteplus_tts_stream(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int = 120) -> bytes:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", "Accept": "application/json", **headers}, method="POST")
    audio = bytearray()
    last_payload: Any = {}
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            for raw_line in res:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ToolError(f"BytePlus TTS stream returned non-JSON line: {line[:500]}") from exc
                last_payload = event
                code = int(event.get("code") or 0)
                if code == 0 and event.get("data"):
                    audio.extend(base64.b64decode(text_value(event.get("data"))))
                    continue
                if code == 20000000:
                    break
                if code > 0:
                    message = text_value(event.get("message") or event.get("error") or "unknown error")
                    raise ToolError(f"BytePlus TTS failed: code={code} message={message}")
    except TimeoutError as exc:
        raise ProviderTimeout(f"POST {redact_secret_text(url)} timed out") from exc
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:3000]
        raise ToolError(f"POST {redact_secret_text(url)} failed: HTTP {exc.code}: {redact_secret_text(detail)}") from exc
    except urllib.error.URLError as exc:
        raise ToolError(f"POST {redact_secret_text(url)} failed: {exc.reason}") from exc
    if not audio:
        raise ToolError(f"BytePlus TTS did not return audio data: {json.dumps(last_payload, ensure_ascii=False)[:1500]}")
    return bytes(audio)


def generate_bytedance_tts(config: dict[str, Any], prompt_text: str, output_path: Path, timeout_seconds: int, voice: str) -> dict[str, Any]:
    api_key = text_value(config.get("api_key"))
    if not api_key:
        raise ToolError(f"Missing TTS API key for bytedance/{text_value(config.get('model'))}.")
    credentials = bytedance_tts_credentials(api_key, config)
    if credentials.get("auth_mode") == "byteplus_api_key":
        raw = post_byteplus_tts_stream(byteplus_tts_endpoint(config), byteplus_tts_request_payload(config, prompt_text, voice), byteplus_tts_headers(credentials, config), timeout=timeout_seconds)
        output_format = byteplus_tts_format(config)
        if output_format == "pcm":
            raw = wav_from_pcm(raw, sample_rate=int(safe_float(config.get("sample_rate") or config.get("rate"), 24000)))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(raw)
        return {
            "provider": "bytedance",
            "model": text_value(config.get("model")),
            "voice": voice,
            "mime_type": "audio/wav" if output_format == "pcm" else "audio/mpeg" if output_format == "mp3" else "audio/ogg",
            "output_path": str(output_path),
            "bytes": len(raw),
        }
    endpoint = text_value(config.get("endpoint") or BYTEDANCE_TTS_V1_ENDPOINT)
    payload = bytedance_tts_request_payload(config, prompt_text, voice, credentials)
    response = post_json_request(endpoint, payload, {"Authorization": f"Bearer; {credentials['access_token']}"}, timeout=timeout_seconds)
    code = int(response.get("code") or 0)
    if code != 3000:
        message = text_value(response.get("message") or "unknown error")
        raise ToolError(f"ByteDance TTS failed: code={code} message={message}")
    encoded = text_value(response.get("data"))
    if not encoded:
        raise ToolError(f"ByteDance TTS did not return audio data: {json.dumps(response, ensure_ascii=False)[:1500]}")
    raw = base64.b64decode(encoded)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(raw)
    encoding = text_value(config.get("encoding") or "wav").lower()
    return {
        "provider": "bytedance",
        "model": text_value(config.get("model")),
        "voice": voice,
        "mime_type": "audio/mpeg" if encoding == "mp3" else "audio/wav" if encoding == "wav" else "application/octet-stream",
        "output_path": str(output_path),
        "bytes": len(raw),
    }


def tts_voice_for_config(config: dict[str, Any], variables: dict[str, Any]) -> str:
    provider = text_value(config.get("provider")).lower()
    model = text_value(config.get("model"))
    selected = dict_value(config.get("selected_voice_by_model"))
    configured_voice = text_value(config.get("voice") or selected.get(model))
    if configured_voice:
        return configured_voice
    if provider in {"google", "gemini"}:
        return text_value(dict_value(variables.get("gemini_builder_g_config")).get("voice") or "Aoede")
    if provider == "bytedance":
        return "zh_male_M392_conversation_wvae_bigtts"
    return configured_voice


def generate_tts_with_provider(config: dict[str, Any], prompt_text: str, output_path: Path, timeout_seconds: int) -> dict[str, Any]:
    provider = text_value(config.get("provider")).lower()
    model = text_value(config.get("model"))
    api_key = text_value(config.get("api_key"))
    voice = text_value(config.get("voice") or "Aoede")
    if provider == "bytedance":
        return generate_bytedance_tts(config, prompt_text, output_path, timeout_seconds, voice)
    if provider not in {"google", "gemini"}:
        raise ToolError(f"Unsupported TTS provider for 05_02: {provider}/{model}.")
    if not api_key:
        raise ToolError(f"Missing TTS API key for {provider}/{model}.")
    payload = {
        "contents": [{"parts": [{"text": prompt_text}]}],
        "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}},
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(model, safe='')}:generateContent?key={urllib.parse.quote(api_key, safe='')}"
    response = post_json_request(url, payload, {}, timeout=timeout_seconds)
    for candidate in response.get("candidates") or []:
        content = dict_value(candidate.get("content")) if isinstance(candidate, dict) else {}
        for part in list_value(content.get("parts")):
            inline = dict_value(part.get("inlineData") or part.get("inline_data")) if isinstance(part, dict) else {}
            encoded = text_value(inline.get("data"))
            if not encoded:
                continue
            mime_type = text_value(inline.get("mimeType") or inline.get("mime_type") or "audio/wav")
            raw = base64.b64decode(encoded)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(wav_from_pcm(raw) if "pcm" in mime_type or "l16" in mime_type else raw)
            return {"provider": provider, "model": model, "voice": voice, "mime_type": mime_type, "output_path": str(output_path)}
    raise ToolError(f"Gemini TTS did not return audio: {json.dumps(response, ensure_ascii=False)[:1500]}")


def ensure_tool_dirs(workspace: Path) -> None:
    for rel_path in (
        f"{TOOL_DIR_NAME}/Working",
        f"{TOOL_DIR_NAME}/Output",
        f"{TOOL_DIR_NAME}/Prompt",
        f"{TOOL_DIR_NAME}/Report",
        STORYBOARD_WORKING_REL,
        ASSET_HISTORY_REL,
    ):
        (workspace / rel_path).mkdir(parents=True, exist_ok=True)


def force_reset(workspace: Path, result: dict[str, Any]) -> None:
    tool_dir = workspace / TOOL_DIR_NAME
    if tool_dir.exists():
        preserved_files: list[tuple[Path, bytes]] = []
        prompt_dir = tool_dir / "Prompt"
        if prompt_dir.exists():
            for path in prompt_dir.glob("ModelCall_*_LipSync_*.json"):
                if not path.is_file():
                    continue
                try:
                    preserved_files.append((path.relative_to(tool_dir), path.read_bytes()))
                except Exception:
                    continue
        remove_path(tool_dir)
        for rel_path, content in preserved_files:
            target = tool_dir / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        action = {"path": TOOL_DIR_NAME, "action": "removed_for_force_rerun"}
        if preserved_files:
            action["preserved_lipsync_state_files"] = len(preserved_files)
        result.setdefault("cleanup_actions", []).append(action)


def load_required_json(workspace: Path, rel_path: str, code: str) -> dict[str, Any]:
    path = workspace / rel_path
    if not path.exists():
        raise ToolError(f"{code}: missing required file {rel_path}")
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise ToolError(f"{code}: {rel_path} must contain a JSON object")
    return payload


def bundled_reference_path(workspace: Path, rel_path: str) -> Path | None:
    rel_candidate = Path(rel_path)
    candidates: list[Path] = []
    try:
        candidates.append(REPO_ROOT / rel_candidate.relative_to("OpenCrew"))
    except ValueError:
        pass
    candidates.extend([REPO_ROOT.parent / rel_candidate, workspace / rel_candidate])
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def copy_wan_rtv_reference_video_to_working(workspace: Path, working: Path, result: dict[str, Any]) -> Path:
    source = bundled_reference_path(workspace, WAN_RTV_REFERENCE_VIDEO_REL)
    if not source or not source.exists():
        raise ToolError(f"wan_rtv_reference_video_missing: {WAN_RTV_REFERENCE_VIDEO_REL}")
    target = working / WAN_RTV_REFERENCE_VIDEO_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    append_created_file(result, rel(workspace, target))
    return target


def copy_max_sd_2_reference_video_to_working(workspace: Path, working: Path, result: dict[str, Any]) -> Path:
    source = bundled_reference_path(workspace, MAX_SD_2_REFERENCE_VIDEO_REL)
    if not source or not source.exists() or not source.is_file() or source.stat().st_size <= 0:
        raise ToolError(f"max_sd_2_reference_video_missing: {MAX_SD_2_REFERENCE_VIDEO_REL}")
    target = working / MAX_SD_2_REFERENCE_VIDEO_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    append_created_file(result, rel(workspace, target))
    return target


def copy_kling_omni_reference_video_to_working(workspace: Path, working: Path, result: dict[str, Any]) -> Path:
    source = bundled_reference_path(workspace, KLING_OMNI_REFERENCE_VIDEO_REL)
    if not source or not source.exists():
        raise ToolError(f"kling_omni_reference_video_missing: {KLING_OMNI_REFERENCE_VIDEO_REL}")
    target = working / KLING_OMNI_REFERENCE_VIDEO_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    append_created_file(result, rel(workspace, target))
    return target


def copy_inputs_to_working(workspace: Path, variables: dict[str, Any], storyboard: dict[str, Any], plan: dict[str, Any], result: dict[str, Any], args: Args | None = None) -> None:
    working = workspace / TOOL_DIR_NAME / "Working"
    prompt_dir = workspace / TOOL_DIR_NAME / "Prompt"
    write_json(working / "InputFrom_00_Variables.json", variables)
    write_json(working / "InputFrom_StoryBoard_srt_storyboard.json", storyboard)
    write_json(working / "InputFrom_05_01_video_generation_plan.json", plan)
    for name, rel_path in MODULE_REFERENCE_TEMPLATE_RELS.items():
        module_template = bundled_reference_path(workspace, rel_path)
        if module_template and module_template.exists():
            shutil.copy2(module_template, prompt_dir / f"Ref_05_02_{name}.md")
    if args is not None and selected_video_is_wan_rtv(variables, args.video_provider, args.video_model):
        copy_wan_rtv_reference_video_to_working(workspace, working, result)
    if args is not None and selected_video_is_openrouter_max_sd_2(variables, args.video_provider, args.video_model):
        copy_max_sd_2_reference_video_to_working(workspace, working, result)
    if args is not None and selected_video_is_kling_omni(variables, args.video_provider, args.video_model):
        copy_kling_omni_reference_video_to_working(workspace, working, result)
    scene_profile = workspace / SCENE_PROFILE_REL
    if scene_profile.exists():
        shutil.copy2(scene_profile, working / "InputFrom_SceneProfile_scene_profile_response.json")
    result.setdefault("created_files", []).extend([
        rel(workspace, working / "InputFrom_00_Variables.json"),
        rel(workspace, working / "InputFrom_StoryBoard_srt_storyboard.json"),
        rel(workspace, working / "InputFrom_05_01_video_generation_plan.json"),
    ])


def flatten_dialogues(storyboard: dict[str, Any]) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    for shot in list_value(storyboard.get("shots")):
        if not isinstance(shot, dict):
            continue
        for scene in list_value(shot.get("scenes")):
            if not isinstance(scene, dict):
                continue
            for dialogue in list_value(scene.get("dialogue_items")):
                if not isinstance(dialogue, dict):
                    continue
                for key in dialogue_match_keys(dialogue):
                    mapping.setdefault(key, {"shot": shot, "scene": scene, "dialogue": dialogue})
    return mapping


def iter_source_dialogues(storyboard: dict[str, Any]) -> list[dict[str, Any]]:
    dialogues: list[dict[str, Any]] = []
    for shot in list_value(storyboard.get("shots")):
        if not isinstance(shot, dict):
            continue
        for scene in list_value(shot.get("scenes")):
            if not isinstance(scene, dict):
                continue
            for dialogue in list_value(scene.get("dialogue_items")):
                if isinstance(dialogue, dict):
                    dialogues.append(dialogue)
    return dialogues


def iter_edit_dialogues(edit_plan: dict[str, Any]) -> list[dict[str, Any]]:
    dialogues: list[dict[str, Any]] = []
    for shot in list_value(edit_plan.get("shots")):
        if not isinstance(shot, dict):
            continue
        for scene in list_value(shot.get("scenes")):
            if not isinstance(scene, dict):
                continue
            for dialogue in list_value(scene.get("dialogues")):
                if isinstance(dialogue, dict):
                    dialogues.append(dialogue)
    return dialogues


def dialogue_match_keys(dialogue: dict[str, Any]) -> set[str]:
    return {
        key
        for key in (
            text_value(dialogue.get("dialogue_asset_key")),
            text_value(dialogue.get("srt_id")),
            text_value(dialogue.get("dialogue_id")),
        )
        if key
    }


def segment_dialogue_asset_keys(segment: dict[str, Any]) -> list[str]:
    values = list_value(segment.get("dialogue_asset_keys"))
    if not values:
        values = list_value(segment.get("dialogue_ids"))
    return [text_value(item) for item in values if text_value(item)]


def sync_generated_outputs_to_edit(workspace: Path, storyboard: dict[str, Any], result: dict[str, Any], *, backup_once_flag: str = "") -> bool:
    edit_path = workspace / EDIT_STORYBOARD_REL
    if not edit_path.exists():
        return False
    edit_plan = read_json(edit_path)
    if not isinstance(edit_plan, dict) or edit_plan.get("schema_version") != "koubo_storyboard_edit_0.1":
        return False
    edit_index: dict[str, dict[str, Any]] = {}
    for dialogue in iter_edit_dialogues(edit_plan):
        for key in dialogue_match_keys(dialogue):
            edit_index.setdefault(key, dialogue)

    changed = False
    for source_dialogue in iter_source_dialogues(storyboard):
        source_assets = ensure_dialogue_assets(source_dialogue)
        source_audio = dict_value(source_assets.get("audio"))
        source_image = dict_value(source_assets.get("images")[0] if source_assets.get("images") else {})
        source_video = dict_value(source_assets.get("video"))
        if not text_value(source_audio.get("path")) and not text_value(source_image.get("path")) and not text_value(source_video.get("path")):
            continue
        target_dialogue = next((edit_index[key] for key in dialogue_match_keys(source_dialogue) if key in edit_index), None)
        if not target_dialogue:
            continue
        target_assets = ensure_dialogue_assets(target_dialogue)
        if text_value(source_audio.get("path")) and not tool_runtime_asset_path(source_audio.get("path")) and target_assets["audio"] != source_audio:
            target_assets["audio"] = {
                "slot": text_value(source_audio.get("slot")) or "Audio_Final",
                "source_type": text_value(source_audio.get("source_type")) or "generated",
                "path": text_value(source_audio.get("path")),
            }
            changed = True
        if text_value(source_image.get("path")) and target_assets["images"][0] != source_image:
            target_assets["images"][0] = {
                "slot": text_value(source_image.get("slot")) or "Image_New",
                "source_type": text_value(source_image.get("source_type")) or "generated",
                "path": text_value(source_image.get("path")),
            }
            target_dialogue["bound_image_path"] = text_value(source_dialogue.get("bound_image_path") or source_image.get("path"))
            changed = True
        if text_value(source_video.get("path")) and target_assets["video"] != source_video:
            target_assets["video"] = {
                "slot": text_value(source_video.get("slot")) or "Video_Final",
                "source_type": text_value(source_video.get("source_type")) or "generated",
                "path": text_value(source_video.get("path")),
            }
            changed = True
    if changed:
        if backup_once_flag:
            backup_before_overwrite_once(workspace, edit_path, result, backup_once_flag)
        else:
            backup_before_overwrite(workspace, edit_path, result)
        write_json(edit_path, edit_plan)
        append_created_file(result, EDIT_STORYBOARD_REL)
    return changed


def persist_storyboard_asset_bindings(workspace: Path, storyboard: dict[str, Any], result: dict[str, Any]) -> None:
    backup_before_overwrite_once(workspace, workspace / STORYBOARD_REL, result, "storyboard_json_incremental_backup")
    write_json(workspace / STORYBOARD_REL, storyboard)
    append_created_file(result, STORYBOARD_REL)
    if sync_generated_outputs_to_edit(workspace, storyboard, result, backup_once_flag="edit_storyboard_json_incremental_backup"):
        actions = result.setdefault("sync_actions", [])
        code = "edit_storyboard_incremental_synced"
        if not any(dict_value(item).get("code") == code for item in actions):
            actions.append({
                "code": code,
                "message": "Generated image/video outputs were incrementally synced to koubo_storyboard_edit.json for manual page refresh.",
            })


def ensure_dialogue_assets(dialogue: dict[str, Any]) -> dict[str, Any]:
    assets = dialogue.get("working_assets") if isinstance(dialogue.get("working_assets"), dict) else {}
    images = assets.get("images") if isinstance(assets.get("images"), list) else []
    image_slots = ("Image_New", "Image_02")
    normalized = {
        "audio": assets.get("audio") if isinstance(assets.get("audio"), dict) else {"slot": "Audio_Final", "source_type": "", "path": ""},
        "images": [
            images[index] if index < len(images) and isinstance(images[index], dict) else {"slot": image_slots[index], "source_type": "", "path": ""}
            for index in range(2)
        ],
        "video": assets.get("video") if isinstance(assets.get("video"), dict) else {"slot": "Video_Final", "source_type": "", "path": ""},
    }
    for index, image in enumerate(normalized["images"], start=1):
        image["slot"] = text_value(image.get("slot")) or image_slots[index - 1]
        image["source_type"] = text_value(image.get("source_type"))
        image["path"] = text_value(image.get("path"))
    for key, slot_name in (("audio", "Audio_Final"), ("video", "Video_Final")):
        normalized[key]["slot"] = text_value(normalized[key].get("slot")) or slot_name
        normalized[key]["source_type"] = text_value(normalized[key].get("source_type"))
        normalized[key]["path"] = text_value(normalized[key].get("path"))
    dialogue["working_assets"] = normalized
    return normalized


def bind_segment_output_to_storyboard(segment: dict[str, Any], dialogue_index: dict[str, dict[str, Any]], kind: str, rel_path: str, source_type: str = "generated") -> bool:
    rel_path = text_value(rel_path)
    if not rel_path or tool_runtime_asset_path(rel_path):
        return False
    dialogue_asset_key = next(iter(segment_dialogue_asset_keys(segment)), "")
    dialogue = dict_value(dialogue_index.get(dialogue_asset_key, {}).get("dialogue"))
    if not dialogue:
        return False
    assets = ensure_dialogue_assets(dialogue)
    if kind == "image":
        assets["images"][0] = {"slot": "Image_New", "source_type": text_value(source_type) or "generated", "path": rel_path}
        dialogue["bound_image_path"] = rel_path
        return True
    if kind == "audio":
        assets["audio"] = {"slot": "Audio_Final", "source_type": text_value(source_type) or "generated", "path": rel_path}
        return True
    if kind == "video":
        assets["video"] = {"slot": "Video_Final", "source_type": "generated", "path": rel_path}
        return True
    return False


def segment_asset_key(segment: dict[str, Any]) -> str:
    dialogue_asset_keys = segment_dialogue_asset_keys(segment)
    return safe_name(dialogue_asset_keys[0] if dialogue_asset_keys else text_value(segment.get("asset_key")), "segment")


def iter_segments(plan: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]:
    segments: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
    for shot in list_value(plan.get("shots")):
        if not isinstance(shot, dict):
            continue
        for scene in list_value(shot.get("scenes")):
            if not isinstance(scene, dict):
                continue
            if scene.get("status") != "planned":
                continue
            for segment in list_value(scene.get("segments")):
                if isinstance(segment, dict) and segment.get("status") != "blocked":
                    segments.append((shot, scene, segment))
    return segments


def plan_no_executable_segments_message(plan: dict[str, Any]) -> str:
    reasons: list[str] = []
    seen_reason_keys: set[tuple[str, str]] = set()
    actionable_messages = {
        "first_scene_missing_visual_source": "缺少首句视觉源，人物口播需要先上传或绑定人物形象图作为首帧，然后重新生成视频计划。",
        "scene_first_dialogue_missing_first_frame_and_previous_tail_missing": "当前场景首句缺少首帧，且没有可复用的上一段尾帧；请先绑定首帧图片或重新生成视频计划。",
        "scene_segments_empty": "视频计划没有生成任何逐句视频段；请检查故事版对白和人物形象/素材绑定后重新生成视频计划。",
    }

    def append_reason(scope: str, payload: Any) -> None:
        if not isinstance(payload, dict):
            return
        code = text_value(payload.get("code") or payload.get("kind"))
        message = text_value(payload.get("message") or payload.get("detail") or payload.get("reason"))
        message = actionable_messages.get(code, message)
        if not code and not message:
            return
        reason_key = (code, message)
        if reason_key in seen_reason_keys:
            return
        seen_reason_keys.add(reason_key)
        reason = f"{scope}: "
        if code and message:
            reason += f"{code} - {message}"
        else:
            reason += code or message
        if reason not in reasons:
            reasons.append(reason)

    for shot in list_value(plan.get("shots")):
        if not isinstance(shot, dict):
            continue
        shot_id = text_value(shot.get("shot_id")) or "shot"
        append_reason(shot_id, shot.get("blocked_reason"))
        append_reason(shot_id, shot.get("skipped_reason"))
        for scene in list_value(shot.get("scenes")):
            if not isinstance(scene, dict):
                continue
            scene_id = text_value(scene.get("scene_id")) or "scene"
            scope = f"{shot_id}/{scene_id}"
            append_reason(scope, scene.get("blocked_reason"))
            append_reason(scope, scene.get("skipped_reason"))
            for segment in list_value(scene.get("segments")):
                if not isinstance(segment, dict):
                    continue
                segment_id = text_value(segment.get("segment_id")) or text_value(segment.get("asset_key")) or "segment"
                append_reason(f"{scope}/{segment_id}", segment.get("blocked_reason"))
                append_reason(f"{scope}/{segment_id}", segment.get("skipped_reason"))

    summary = dict_value(plan.get("summary"))
    summary_bits = []
    for key in ("segment_count", "need_video_count", "need_image_count", "need_audio_count", "skipped_scene_count", "blocked_scene_count"):
        if key in summary:
            summary_bits.append(f"{key}={summary.get(key)}")
    detail = "; ".join(reasons[:3])
    if detail:
        return f"plan_has_no_executable_segments: {detail}"
    if summary_bits:
        return f"plan_has_no_executable_segments: {', '.join(summary_bits)}"
    return "plan_has_no_executable_segments"


def asset_type_for_path(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix in IMAGE_EXTS:
        return "Image"
    if suffix in VIDEO_EXTS:
        return "Video"
    if suffix in AUDIO_EXTS:
        return "Audio"
    return "File"


def slot_for_path(path: str) -> str:
    name = Path(path).stem
    for asset_type in ("Audio", "Image", "Video"):
        marker = f"_{asset_type}_"
        if marker in name:
            return f"{asset_type}_{name.split(marker, 1)[1]}"
    return asset_type_for_path(path)


def history_item_for(original_rel: str, history_rel: str, reason: str) -> dict[str, Any]:
    asset_key = Path(original_rel).stem
    for marker in ("_Audio_", "_Image_", "_Video_"):
        if marker in asset_key:
            asset_key = asset_key.split(marker, 1)[0]
            break
    return {
        "original_path": original_rel,
        "history_path": history_rel,
        "asset_type": asset_type_for_path(original_rel),
        "slot": slot_for_path(original_rel),
        "asset_key": asset_key,
        "reason": reason,
        "source": "05_02",
    }


def backup_before_overwrite(workspace: Path, target: Path, result: dict[str, Any]) -> None:
    if not target.exists():
        return
    batch = f"batch_{now_ms()}_05_02_overwrite_backup"
    history = workspace / ASSET_HISTORY_REL / batch
    history.mkdir(parents=True, exist_ok=True)
    backup = history / target.name
    counter = 1
    while backup.exists():
        backup = history / f"{backup.stem}_{counter}{backup.suffix}"
        counter += 1
    shutil.copy2(target, backup)
    original_rel = rel(workspace, target)
    history_rel = rel(workspace, backup)
    item = history_item_for(original_rel, history_rel, "05_02_overwrite_backup")
    manifest_path = history / "manifest.json"
    manifest = read_json(manifest_path) if manifest_path.exists() else {}
    items = manifest.get("items") if isinstance(manifest.get("items"), list) else []
    if not manifest:
        manifest = {
            "schema_version": "storyboard_asset_history_0.1",
            "batch_id": batch,
            "reason": "05_02_overwrite_backup",
            "created_at": now_ms(),
            "items": [],
        }
    items.append(item)
    manifest["items"] = items
    manifest["updated_at"] = now_ms()
    write_json(manifest_path, manifest)
    result.setdefault("backups", []).append({"from": original_rel, "to": history_rel, "history_path": history_rel})


def backup_before_overwrite_once(workspace: Path, target: Path, result: dict[str, Any], flag: str) -> None:
    flags = result.setdefault("_runtime_flags", {})
    if not isinstance(flags, dict):
        flags = {}
        result["_runtime_flags"] = flags
    if flags.get(flag):
        return
    backup_before_overwrite(workspace, target, result)
    flags[flag] = True


def append_created_file(result: dict[str, Any], rel_path: str) -> None:
    created = result.setdefault("created_files", [])
    if rel_path not in created:
        created.append(rel_path)


def publish_file(workspace: Path, source: Path, planned_rel: str, result: dict[str, Any]) -> str:
    if not source.exists() or source.stat().st_size <= 0:
        raise ToolError(f"Cannot publish missing or empty file: {source}")
    output_target = workspace / TOOL_DIR_NAME / "Output" / Path(planned_rel).name
    output_target.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() != output_target.resolve():
        shutil.copy2(source, output_target)
    story_target = workspace_path(workspace, planned_rel)
    story_target.parent.mkdir(parents=True, exist_ok=True)
    backup_before_overwrite(workspace, story_target, result)
    shutil.copy2(output_target, story_target)
    result.setdefault("created_files", []).extend([rel(workspace, output_target), rel(workspace, story_target)])
    return rel(workspace, story_target)


def copy_to_working(workspace: Path, source_path: str, target_name: str) -> Path:
    source = workspace_path(workspace, source_path)
    if not source.exists():
        raise ToolError(f"Source file does not exist: {source_path}")
    target = workspace / TOOL_DIR_NAME / "Working" / target_name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target


def existing_workspace_file(workspace: Path, rel_path: str) -> bool:
    if not rel_path:
        return False
    path = workspace_path(workspace, rel_path)
    return path.exists() and path.is_file() and path.stat().st_size > 0


def same_workspace_path(workspace: Path, left: str, right: str) -> bool:
    left = text_value(left)
    right = text_value(right)
    if not left or not right:
        return False
    try:
        return workspace_path(workspace, left).resolve() == workspace_path(workspace, right).resolve()
    except Exception:
        return str(workspace_path(workspace, left)) == str(workspace_path(workspace, right))


def bound_video_source_for_sync(workspace: Path, asset_key: str, outputs: dict[str, Any], requested_source_rel: str) -> tuple[str, str]:
    requested_source_rel = text_value(requested_source_rel)
    default_final_rel = f"{STORYBOARD_WORKING_REL}/{asset_key}_Video_Final.mp4"
    final_candidates = [
        text_value(outputs.get("video_path")),
        text_value(outputs.get("final_video_path")),
        default_final_rel,
    ]
    if not any(same_workspace_path(workspace, requested_source_rel, candidate) for candidate in final_candidates):
        return requested_source_rel, "requested_bound_video"
    raw_candidates = [
        text_value(outputs.get("raw_video_path")),
        f"{STORYBOARD_WORKING_REL}/{asset_key}_Video_Raw.mp4",
    ]
    seen: set[str] = set()
    for candidate in raw_candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        if existing_workspace_file(workspace, candidate):
            return candidate, "raw_video"
    return requested_source_rel, "requested_bound_video"


def dance_mimic_reference_video_path(item: dict[str, Any]) -> str:
    sources = [
        item,
        dict_value(item.get("dance_mimic")),
        dict_value(item.get("source_segment")),
        dict_value(dict_value(item.get("source_segment")).get("dance_mimic")),
    ]
    for source in sources:
        value = text_value(source.get("reference_video_path"))
        if value:
            return value
    return ""


def max_sd_2_reference_video_path(item: dict[str, Any]) -> str:
    source_segment = dict_value(item.get("source_segment"))
    sources = [
        item,
        dict_value(item.get("max_sd_2")),
        source_segment,
        dict_value(source_segment.get("max_sd_2")),
    ]
    for source in sources:
        for key in ("provider_reference_video_path", "source_face_masked_reference_video_path", "reference_video_path"):
            value = text_value(source.get(key))
            if value:
                return value
    return ""


def first_dialogue_for_segment(segment: dict[str, Any], dialogue_index: dict[str, dict[str, Any]]) -> dict[str, Any]:
    for dialogue_asset_key in segment_dialogue_asset_keys(segment):
        dialogue = dict_value(dict_value(dialogue_index.get(dialogue_asset_key)).get("dialogue"))
        if dialogue:
            return dialogue
    return {}


def dance_mimic_target_identity_path(
    workspace: Path,
    item: dict[str, Any],
    dialogue_index: dict[str, dict[str, Any]] | None = None,
) -> str:
    dialogue = first_dialogue_for_segment(item, dialogue_index or {}) if dialogue_index is not None else {}
    sources = [
        item,
        dict_value(item.get("dance_mimic")),
        dict_value(item.get("source_segment")),
        dict_value(dict_value(item.get("source_segment")).get("dance_mimic")),
        dialogue,
        dict_value(dialogue.get("dance_mimic")),
    ]
    candidates: list[str] = []
    for source in sources:
        for key in ("target_identity_image_path", "source_target_identity_image_path", "image_path"):
            value = text_value(source.get(key))
            if value and value not in candidates:
                candidates.append(value)
        for value in list_value(source.get("source_image_paths")):
            path = text_value(value)
            if path and path not in candidates:
                candidates.append(path)
    for candidate in candidates:
        path = workspace_path(workspace, candidate)
        if path.exists() and path.is_file() and path.stat().st_size > 0:
            return rel(workspace, path)
    return candidates[0] if candidates else ""


def paths_have_same_content(left: Path, right: Path) -> bool:
    try:
        if left.resolve() == right.resolve():
            return True
    except Exception:
        pass
    if not left.exists() or not right.exists() or not left.is_file() or not right.is_file():
        return False
    try:
        left_stat = left.stat()
        right_stat = right.stat()
        if left_stat.st_size != right_stat.st_size:
            return False
        return path_sha256(left) == path_sha256(right)
    except Exception:
        return False


def path_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_dance_mimic_privacy_grid_tool() -> Any:
    try:
        return importlib.import_module("ToolLibrary.DanceMimic_V1._tool_impl")
    except ModuleNotFoundError:
        return importlib.import_module("OpenCrew.ToolLibrary.DanceMimic_V1._tool_impl")


def dance_mimic_privacy_grid_fields(segment: dict[str, Any]) -> dict[str, Any]:
    nested = dict_value(segment.get("dance_mimic"))
    return {
        "enabled": bool(segment.get("privacy_grid_mode") or nested.get("privacy_grid_mode")),
        "reference_video_grid_applied": bool(segment.get("reference_video_grid_applied") or nested.get("reference_video_grid_applied")),
        "target_identity_grid_applied": bool(segment.get("target_identity_grid_applied") or nested.get("target_identity_grid_applied")),
        "effective_grid_scope": text_value(segment.get("effective_grid_scope") or nested.get("effective_grid_scope")),
        "manifest_path": text_value(segment.get("privacy_grid_manifest_path") or nested.get("privacy_grid_manifest_path")),
        "prompt_contract": text_value(segment.get("prompt_contract") or nested.get("prompt_contract")),
        "segment_id": text_value(
            segment.get("source_segment_id")
            or nested.get("source_segment_id")
            or segment.get("storyboard_seed_segment_id")
            or nested.get("storyboard_seed_segment_id")
            or segment.get("segment_id")
        ),
    }


def requires_privacy_grid_continuity_frame(segment: dict[str, Any]) -> bool:
    fields = dance_mimic_privacy_grid_fields(segment)
    first_frame = dict_value(segment.get("first_frame"))
    materialize = dict_value(first_frame.get("materialize_first_frame"))
    source_type = text_value(first_frame.get("source_type") or materialize.get("source_type"))
    return bool(fields["enabled"] and fields["target_identity_grid_applied"] and source_type in DANCE_MIMIC_TAIL_CONTINUITY_SOURCE_TYPES)


def prepare_privacy_grid_continuity_frame(
    workspace: Path,
    segment: dict[str, Any],
    first_frame_path: Path,
    working_dir: Path,
    asset_key: str,
    result: dict[str, Any],
    *,
    privacy_tool: Any | None = None,
) -> tuple[Path, dict[str, Any]]:
    if not requires_privacy_grid_continuity_frame(segment):
        return first_frame_path, {}
    fields = dance_mimic_privacy_grid_fields(segment)
    manifest_rel = text_value(fields["manifest_path"])
    manifest_path = workspace_path(workspace, manifest_rel) if manifest_rel else None
    if manifest_path is None or not manifest_path.exists() or not manifest_path.is_file():
        raise ToolError(f"privacy_grid_continuity_preflight_failed: privacy grid manifest is missing: {manifest_rel or '(empty)' }.")
    manifest = dict_value(read_json(manifest_path))
    if text_value(manifest.get("mode")) != "red_grid_guide" or not bool(manifest.get("apply_to_target_identity_image")):
        raise ToolError("privacy_grid_continuity_preflight_failed: target identity privacy grid is not enabled in the manifest.")
    tool = privacy_tool or load_dance_mimic_privacy_grid_tool()
    cv2, np = tool.import_cv2_np()
    image = cv2.imread(str(first_frame_path), cv2.IMREAD_COLOR)
    if image is None or image.size == 0:
        raise ToolError(f"privacy_grid_continuity_frame_decode_failed: could not decode {rel(workspace, first_frame_path)}.")
    defaults = dict_value(dict_value(tool.default_variables()).get("reference_face_masked_video_build"))
    variables = dict_value(read_json(workspace / VARIABLES_REL)) if (workspace / VARIABLES_REL).is_file() else {}
    runtime = dict_value(variables.get("reference_face_masked_video_build"))
    config = {**defaults, **runtime}
    config["privacy_grid"] = {
        **dict_value(defaults.get("privacy_grid")),
        **dict_value(runtime.get("privacy_grid")),
        **dict_value(manifest.get("render")),
    }
    try:
        faces, engine = tool.detect_faces_in_image(image, config)
    except Exception as exc:
        raise ToolError(f"privacy_grid_continuity_face_detection_failed: {redact_secret_text(str(exc))[:500]}") from exc
    if not faces:
        raise ToolError("privacy_grid_continuity_face_not_detected: continuation tail frame cannot be sent without a privacy grid.")
    height, width = image.shape[:2]
    rendered = image.copy()
    face_results: list[dict[str, Any]] = []
    for face in faces:
        bbox = list_value(dict_value(face).get("bbox"))
        if len(bbox) != 4:
            continue
        x, y, w, h = [int(round(float(value))) for value in bbox]
        expanded = tool.clamp_bbox(
            [int(round(x - 0.15 * w)), int(round(y - 0.25 * h)), int(round(w * 1.30)), int(round(h * 1.45))],
            width,
            height,
        )
        rendered = tool.render_privacy_grid(rendered, expanded, config, cv2)
        face_results.append({
            "bbox": [x, y, w, h],
            "expanded_bbox": expanded,
            "confidence": round(safe_float(dict_value(face).get("confidence"), 0.0), 4),
        })
    if not face_results:
        raise ToolError("privacy_grid_continuity_face_not_detected: continuation tail frame has no valid detected-face region.")
    line_presence = [tool.privacy_grid_line_presence(rendered, item["expanded_bbox"], config, np) for item in face_results]
    min_presence = min(line_presence, default=0.0)
    if min_presence < 0.95:
        raise ToolError(f"privacy_grid_continuity_render_failed: red grid line QA failed: {min_presence:.3f}.")
    output = working_dir / f"{safe_name(asset_key)}_ContinuityFirstFrame_PrivacyGrid.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output), rendered) or not output.exists() or output.stat().st_size <= 0:
        raise ToolError(f"privacy_grid_continuity_render_failed: could not write {rel(workspace, output)}.")
    append_created_file(result, rel(workspace, output))
    return output, {
        "grid_applied": True,
        "source_type": text_value(dict_value(segment.get("first_frame")).get("source_type")),
        "source_path": rel(workspace, first_frame_path),
        "source_sha256": path_sha256(first_frame_path),
        "provider_path": rel(workspace, output),
        "provider_sha256": path_sha256(output),
        "face_count": len(face_results),
        "faces": face_results,
        "detection_engine": text_value(engine),
        "line_presence_ratio_min": round(min_presence, 4),
    }


def load_talking_head_privacy_grid_tool() -> Any:
    try:
        return importlib.import_module("ToolLibrary.TalkingHead_V1.reference_privacy_grid")
    except ModuleNotFoundError:
        return importlib.import_module("OpenCrew.ToolLibrary.TalkingHead_V1.reference_privacy_grid")


def talking_head_privacy_segment(storyboard: dict[str, Any], segment: dict[str, Any]) -> dict[str, Any]:
    reference = dict_value(segment.get("talking_head_reference"))
    if not reference:
        reference = dict_value(dict_value(storyboard.get("talking_head_config")).get("max_sd_2_reference"))
    return {**segment, "talking_head_reference": reference} if reference else segment


def prepare_max_sd_2_oral_privacy_frame(
    workspace: Path,
    variables: dict[str, Any],
    storyboard: dict[str, Any],
    segment: dict[str, Any],
    first_frame_path: Path,
    working_dir: Path,
    asset_key: str,
    *,
    privacy_tool: Any | None = None,
    provider_override: str = "",
    model_override: str = "",
) -> tuple[Path, dict[str, Any]]:
    if not should_apply_max_sd_2_oral_privacy_grid(
        variables,
        storyboard,
        segment,
        provider_override,
        model_override,
    ):
        return first_frame_path, {}
    privacy_segment = talking_head_privacy_segment(storyboard, segment)
    try:
        output, metadata = (privacy_tool or load_talking_head_privacy_grid_tool()).prepare_continuity_frame(
            workspace,
            variables,
            privacy_segment,
            first_frame_path,
            working_dir,
            asset_key,
        )
    except Exception as exc:
        raise ToolError(f"max_sd_2_oral_privacy_grid_failed: {exc}") from exc
    return output, dict_value(metadata)


def privacy_grid_expected_scope(reference_enabled: bool, target_enabled: bool) -> str:
    if reference_enabled and target_enabled:
        return "both"
    if reference_enabled:
        return "reference_video"
    if target_enabled:
        return "target_identity"
    return "none"


def validate_privacy_grid_provider_inputs(
    workspace: Path,
    segment: dict[str, Any],
    identity_path: Path,
    reference_video_path: Path,
) -> None:
    fields = dance_mimic_privacy_grid_fields(segment)
    if not fields["enabled"]:
        return
    manifest_rel = text_value(fields["manifest_path"])
    manifest_path = workspace_path(workspace, manifest_rel) if manifest_rel else None
    if manifest_path is None or not manifest_path.exists() or not manifest_path.is_file():
        raise ToolError(f"privacy_grid_provider_preflight_failed: privacy grid manifest is missing: {manifest_rel or '(empty)' }.")
    try:
        manifest = dict_value(read_json(manifest_path))
    except Exception as exc:
        raise ToolError(f"privacy_grid_provider_preflight_failed: privacy grid manifest is unreadable: {manifest_rel}.") from exc
    if text_value(manifest.get("mode")) != "red_grid_guide":
        raise ToolError("privacy_grid_provider_preflight_failed: manifest mode is not red_grid_guide.")

    reference_enabled = bool(fields["reference_video_grid_applied"])
    target_enabled = bool(fields["target_identity_grid_applied"])
    expected_scope = privacy_grid_expected_scope(reference_enabled, target_enabled)
    manifest_scope = text_value(manifest.get("effective_grid_scope"))
    checks = (
        (bool(manifest.get("apply_to_reference_video")) == reference_enabled, "reference video switch does not match manifest"),
        (bool(manifest.get("apply_to_target_identity_image")) == target_enabled, "target identity switch does not match manifest"),
        (text_value(fields["effective_grid_scope"]) == expected_scope, "segment effective grid scope is invalid"),
        (manifest_scope == expected_scope, "manifest effective grid scope is invalid"),
        (expected_scope == "none" or bool(fields["prompt_contract"]), "privacy grid prompt contract is missing"),
    )
    for valid, message in checks:
        if not valid:
            raise ToolError(f"privacy_grid_provider_preflight_failed: {message}.")

    target_manifest = dict_value(manifest.get("target_identity"))
    target_hash = text_value(target_manifest.get("provider_sha256"))
    if not target_hash or not identity_path.exists() or not identity_path.is_file() or path_sha256(identity_path) != target_hash:
        raise ToolError("privacy_grid_provider_preflight_failed: target identity input does not match the manifest provider asset.")
    if bool(target_manifest.get("grid_applied")) != target_enabled:
        raise ToolError("privacy_grid_provider_preflight_failed: target identity grid state does not match the manifest.")

    reference_manifest = dict_value(manifest.get("reference_video"))
    segment_id = text_value(fields["segment_id"])
    provider_segment = next(
        (
            item for item in list_value(reference_manifest.get("provider_segments"))
            if isinstance(item, dict) and text_value(item.get("segment_id")) == segment_id
        ),
        None,
    )
    expected_video_hash = text_value(dict_value(provider_segment).get("provider_sha256"))
    if not provider_segment or not expected_video_hash:
        raise ToolError(f"privacy_grid_provider_preflight_failed: manifest has no provider video for segment {segment_id or '(empty)'}.")
    if not reference_video_path.exists() or not reference_video_path.is_file() or path_sha256(reference_video_path) != expected_video_hash:
        raise ToolError("privacy_grid_provider_preflight_failed: motion reference video does not match the manifest provider asset.")
    if bool(reference_manifest.get("grid_applied")) != reference_enabled:
        raise ToolError("privacy_grid_provider_preflight_failed: reference video grid state does not match the manifest.")


def dance_mimic_video_reference_images(
    workspace: Path,
    segment: dict[str, Any],
    first_frame_path: Path,
    dialogue_index: dict[str, dict[str, Any]],
) -> tuple[list[Path], list[dict[str, str]]]:
    references = [first_frame_path]
    roles = [{
        "role": "continuity_first_frame",
        "path": rel(workspace, first_frame_path),
    }]
    if not is_dance_mimic_reference_video_segment(segment):
        return references, roles
    identity_rel = dance_mimic_target_identity_path(workspace, segment, dialogue_index)
    identity_path = workspace_path(workspace, identity_rel) if identity_rel else None
    privacy_grid_mode = bool(dance_mimic_privacy_grid_fields(segment)["enabled"])
    if privacy_grid_mode and (identity_path is None or not identity_path.exists() or not identity_path.is_file() or identity_path.stat().st_size <= 0):
        raise ToolError(f"privacy_grid_provider_preflight_failed: target identity image is missing: {identity_rel or '(empty)'}.")
    if identity_path and identity_path.exists() and identity_path.is_file() and not paths_have_same_content(first_frame_path, identity_path):
        references.append(identity_path)
        roles.append({
            "role": "target_identity",
            "path": rel(workspace, identity_path),
        })
    else:
        roles[0]["role"] = "continuity_first_frame,target_identity"
    return references, roles


def prepare_dance_mimic_reference_videos(
    workspace: Path,
    item: dict[str, Any],
    working_dir: Path,
    asset_key: str,
    result: dict[str, Any] | None = None,
) -> list[Path]:
    if not is_dance_mimic_reference_video_segment(item) and not is_dance_mimic_reference_video_segment(dict_value(item.get("source_segment"))):
        return []
    source_rel = dance_mimic_reference_video_path(item)
    if not source_rel:
        raise ToolError(f"dance_mimic_reference_video_missing: {asset_key} has no reference_video_path.")
    source = workspace_path(workspace, source_rel)
    if not source.exists() or not source.is_file() or source.stat().st_size <= 0:
        raise ToolError(f"dance_mimic_reference_video_missing: {source_rel}")
    suffix = source.suffix or ".mp4"
    target = working_dir / f"{safe_name(asset_key)}_DanceMimicReference{suffix}"
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() != target.resolve():
        shutil.copy2(source, target)
    if result is not None:
        append_created_file(result, rel(workspace, target))
    return [target]


def prepare_max_sd_2_reference_videos(
    workspace: Path,
    item: dict[str, Any],
    video_selection: dict[str, Any],
    working_dir: Path,
    asset_key: str,
    result: dict[str, Any] | None = None,
) -> list[Path]:
    source_segment = dict_value(item.get("source_segment"))
    segment = source_segment or item
    if (
        is_dance_mimic_reference_video_segment(item)
        or is_dance_mimic_reference_video_segment(source_segment)
        or segment_is_cutaway(segment)
        or not is_openrouter_max_sd_2_model(
            text_value(video_selection.get("provider")),
            text_value(video_selection.get("model")),
        )
    ):
        return []

    source_rel = max_sd_2_reference_video_path(item)
    if source_rel:
        source = workspace_path(workspace, source_rel)
        if not source.exists() or not source.is_file() or source.stat().st_size <= 0:
            raise ToolError(f"max_sd_2_reference_video_missing: {source_rel}")
        suffix = source.suffix or ".mp4"
        target = working_dir / f"{safe_name(asset_key)}_MaxSD2Reference{suffix}"
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
        if result is not None:
            append_created_file(result, rel(workspace, target))
        return [target]

    target = working_dir / MAX_SD_2_REFERENCE_VIDEO_NAME
    if not target.exists() or not target.is_file() or target.stat().st_size <= 0:
        copy_max_sd_2_reference_video_to_working(workspace, working_dir, result if result is not None else {})
    return [target]


def completed_segment_result_by_id(result: dict[str, Any], segment_id: str) -> dict[str, Any]:
    if not segment_id:
        return {}
    for segment_result in list_value(result.get("segments")):
        if not isinstance(segment_result, dict):
            continue
        if text_value(segment_result.get("segment_id")) == segment_id:
            return segment_result
    return {}


def validate_dance_mimic_segment_dependencies(workspace: Path, segment: dict[str, Any], result: dict[str, Any]) -> None:
    if not is_dance_mimic_reference_video_segment(segment):
        return
    dependencies = dict_value(segment.get("dependencies"))
    depends_on_segment_id = text_value(dependencies.get("depends_on_segment_id"))
    depends_on_video_path = text_value(dependencies.get("depends_on_video_path"))
    depends_on_tail_frame_path = text_value(dependencies.get("depends_on_tail_frame_path"))
    if not depends_on_segment_id and not depends_on_video_path and not depends_on_tail_frame_path:
        return
    upstream = completed_segment_result_by_id(result, depends_on_segment_id)
    if depends_on_segment_id:
        if not upstream:
            raise ToolError(f"dancemimic_dependency_not_ready: {segment_asset_key(segment)} waits for {depends_on_segment_id}.")
        if text_value(upstream.get("status")) != "completed":
            raise ToolError(
                f"dancemimic_dependency_failed: {segment_asset_key(segment)} waits for "
                f"{depends_on_segment_id}, status={text_value(upstream.get('status')) or 'unknown'}."
            )
    upstream_outputs = dict_value(upstream.get("outputs"))
    video_rel = depends_on_video_path or text_value(upstream_outputs.get("video_path"))
    tail_rel = depends_on_tail_frame_path or text_value(upstream_outputs.get("tail_frame_path"))
    for label, rel_path in (("final video", video_rel), ("tail frame", tail_rel)):
        if not rel_path:
            raise ToolError(f"dancemimic_dependency_missing_{label.replace(' ', '_')}: {segment_asset_key(segment)} has no upstream {label} path.")
        path = workspace_path(workspace, rel_path)
        if not path.exists() or not path.is_file() or path.stat().st_size <= 0:
            raise ToolError(f"dancemimic_dependency_not_ready: {segment_asset_key(segment)} upstream {label} missing: {rel_path}")


def load_optional_json(workspace: Path, rel_path: str) -> dict[str, Any]:
    path = workspace / rel_path
    if not path.exists():
        return {}
    try:
        payload = read_json(path)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def consistency_reference_paths(workspace: Path, plan: dict[str, Any]) -> dict[str, str]:
    paths: dict[str, str] = {}
    references = list_value(dict_value(plan.get("consistency_references")).get("references"))
    for item in references:
        if not isinstance(item, dict):
            continue
        kind = text_value(item.get("kind"))
        output_path = text_value(item.get("output_path"))
        if kind and output_path and workspace_path(workspace, output_path).exists():
            paths[kind] = output_path
    fallback = {
        "host": ["SessionContext/Consistency/HOST.png", "SessionContext/Consistency/HOST.jpg", "SessionContext/Consistency/HOST.jpeg", "SessionContext/Consistency/HOST.webp"],
        "product": ["SessionContext/Consistency/Product.png", "SessionContext/Consistency/Product.jpg", "SessionContext/Consistency/Product.jpeg", "SessionContext/Consistency/Product.webp"],
    }
    for kind, candidates in fallback.items():
        if paths.get(kind):
            continue
        for candidate in candidates:
            if workspace_path(workspace, candidate).exists():
                paths[kind] = candidate
                break
    return paths


def prepare_image_references(workspace: Path, segment: dict[str, Any], plan: dict[str, Any]) -> list[dict[str, str]]:
    first_frame = dict_value(segment.get("first_frame"))
    source_path = text_value(first_frame.get("source_path"))
    refs: list[dict[str, str]] = []
    if source_path:
        source_suffix = Path(source_path).suffix or ".png"
        copied = copy_to_working(workspace, source_path, f"{segment_asset_key(segment)}_TargetFrame{source_suffix}")
        refs.append({"role": "TARGET_FRAME", "kind": "target_frame", "source_path": source_path, "working_path": rel(workspace, copied)})
    consistency = consistency_reference_paths(workspace, plan)
    for kind, role, label in (("host", "HOST_REFERENCE", "人物一致性"), ("product", "PRODUCT_REFERENCE", "产品一致性")):
        if kind == "host" and segment_is_cutaway(segment):
            continue
        source = text_value(consistency.get(kind))
        if not source:
            continue
        suffix = Path(source).suffix or ".png"
        copied = copy_to_working(workspace, source, f"{segment_asset_key(segment)}_{role}{suffix}")
        refs.append({"role": role, "kind": kind, "label": label, "source_path": source, "working_path": rel(workspace, copied)})
    return refs


def reference_by_kind(references: list[dict[str, str]], kind: str) -> dict[str, str]:
    for reference in references:
        if reference.get("kind") == kind:
            return reference
    return {}


def segment_is_cutaway(segment: dict[str, Any]) -> bool:
    tasks = dict_value(segment.get("tasks"))
    reason = text_value(tasks.get("lipsync_reason")).lower()
    source = text_value(tasks.get("lipsync_decision_source")).lower()
    return reason in {"user_marked_cutaway", "cutaway", "product_closeup", "no_visible_face", "no_face"} or source in {"user_marked_cutaway", "product_closeup"}


def tts_prompt_for_dialogue(dialogue: dict[str, Any]) -> str:
    text = text_value(dialogue.get("dialogue") or dialogue.get("text"))
    return f"请用自然、清晰、适合口播的中文朗读下面这句话。只输出语音，不要读出括号说明之外的系统文字。\n\n正文：{text}"


def compose_segment_audio(workspace: Path, audio_paths: list[Path], output_path: Path) -> dict[str, Any]:
    if not audio_paths:
        raise ToolError("Segment has no dialogue audio to compose.")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if len(audio_paths) == 1:
        shutil.copy2(audio_paths[0], output_path)
        return {"source": "single_dialogue_audio_copy", "inputs": [rel(workspace, audio_paths[0])], "output_path": rel(workspace, output_path)}
    try:
        params = None
        frames: list[bytes] = []
        for path in audio_paths:
            with wave.open(str(path), "rb") as reader:
                current = reader.getparams()
                comparable = (current.nchannels, current.sampwidth, current.framerate, current.comptype, current.compname)
                if params is None:
                    params = comparable
                elif comparable != params:
                    raise ToolError("WAV audio parameters do not match.")
                frames.append(reader.readframes(reader.getnframes()))
        assert params is not None
        with wave.open(str(output_path), "wb") as writer:
            writer.setnchannels(params[0])
            writer.setsampwidth(params[1])
            writer.setframerate(params[2])
            writer.writeframes(b"".join(frames))
        return {"source": "wav_concat", "inputs": [rel(workspace, path) for path in audio_paths], "output_path": rel(workspace, output_path)}
    except Exception:
        list_file = output_path.with_suffix(".ffconcat.txt")
        list_file.write_text("".join(f"file '{path.as_posix()}'\n" for path in audio_paths), encoding="utf-8")
        command = [ffmpeg_executable(), "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(output_path)]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            raise ToolError(f"Segment audio concat failed: {completed.stderr[:1200]}")
        return {"source": "ffmpeg_concat", "inputs": [rel(workspace, path) for path in audio_paths], "output_path": rel(workspace, output_path)}


def generate_silent_segment_audio(workspace: Path, output_path: Path, duration_seconds: float) -> dict[str, Any]:
    duration = max(0.1, float(duration_seconds or 0.0))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg_executable(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-t",
        f"{duration:.6f}",
        "-c:a",
        "pcm_s16le",
        str(output_path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0 or not output_path.exists() or output_path.stat().st_size <= 0:
        raise ToolError(f"Silent segment audio generation failed: {completed.stderr[:1200]}")
    return {"source": "dance_mimic_silent_segment_audio", "duration_seconds": round(duration, 3), "output_path": rel(workspace, output_path)}


def extract_tail_frame(video_path: Path, output_path: Path) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg_executable(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-sseof",
        "-1",
        "-i",
        str(video_path),
        "-map",
        "0:v:0",
        "-an",
        "-vf",
        "reverse",
        "-frames:v",
        "1",
        str(output_path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0 or not output_path.exists() or output_path.stat().st_size <= 0:
        raise ToolError(f"Tail frame extraction failed: {completed.stderr[:1200]}")
    return {
        "source": "ffmpeg_tail_frame_exact_reverse",
        "video_path": str(video_path),
        "output_path": str(output_path),
        "seek_window_seconds": 1.0,
    }


def publish_segment_tail_frame(
    workspace: Path,
    result: dict[str, Any],
    tracker: ExecutionTracker | None,
    segment_id: str,
    segment: dict[str, Any],
    asset_key: str,
    working_dir: Path,
    video_path: Path,
    reason: str = "",
) -> tuple[str, dict[str, Any]]:
    tail_rel = (
        text_value(dict_value(segment.get("tail_frame")).get("planned_path"))
        or f"{STORYBOARD_WORKING_REL}/{asset_key}_TailFrame.png"
    )
    tail_suffix = Path(tail_rel).suffix or ".png"
    tail_working = working_dir / f"{asset_key}_TailFrame{tail_suffix}"
    tracker.step(segment_id, "tail", "running_generate", reason=reason) if tracker else None
    tail_result = extract_tail_frame(video_path, tail_working)
    publish_file(workspace, tail_working, tail_rel, result)
    tracker.step(segment_id, "tail", "completed_working", outputs={"tail_frame_path": tail_rel}) if tracker else None
    return tail_rel, tail_result


def media_duration_seconds(path: Path) -> float:
    command = [ffprobe_executable(), "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise ToolError(f"Media duration probe failed for {path.name}: {completed.stderr[:1200]}")
    duration = safe_float(completed.stdout.strip(), 0.0)
    if duration <= 0:
        raise ToolError(f"Media duration probe returned invalid duration for {path.name}: {completed.stdout.strip()!r}")
    return duration


def media_frame_rate(path: Path) -> float:
    command = [
        ffprobe_executable(),
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=avg_frame_rate,r_frame_rate",
        "-of",
        "json",
        str(path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        return 0.0
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        return 0.0
    streams = payload.get("streams") if isinstance(payload.get("streams"), list) else []
    stream = streams[0] if streams and isinstance(streams[0], dict) else {}
    for key in ("avg_frame_rate", "r_frame_rate"):
        value = text_value(stream.get(key))
        if not value:
            continue
        if "/" in value:
            numerator, denominator = value.split("/", 1)
            den = safe_float(denominator, 0.0)
            rate = safe_float(numerator, 0.0) / den if den else 0.0
        else:
            rate = safe_float(value, 0.0)
        if 1.0 <= rate <= 120.0:
            return rate
    return 0.0


def frame_rate_arg(rate: float) -> str:
    value = round(float(rate or 24.0), 3)
    return str(int(value)) if abs(value - int(value)) < 0.001 else f"{value:.3f}".rstrip("0").rstrip(".")


def media_pixel_format(path: Path) -> str:
    command = [
        ffprobe_executable(), "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=pix_fmt", "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    value = text_value(completed.stdout).lower() if completed.returncode == 0 else ""
    return value if value in {"yuv420p", "yuv422p", "yuv444p", "yuv420p10le", "yuv422p10le", "yuv444p10le"} else "yuv420p"


def atempo_filter_chain(tempo: float) -> str:
    value = max(0.05, min(20.0, float(tempo or 1.0)))
    filters: list[float] = []
    while value > 2.0:
        filters.append(2.0)
        value /= 2.0
    while value < 0.5:
        filters.append(0.5)
        value /= 0.5
    filters.append(value)
    return ",".join(f"atempo={item:.8f}" for item in filters)


def fit_audio_to_video_duration(workspace: Path, audio_path: Path, video_path: Path, output_path: Path, *, mode: str) -> tuple[Path, dict[str, Any]]:
    if not video_path.exists() or video_path.stat().st_size <= 0:
        raise ToolError(f"Cannot fit audio against missing video: {video_path}")
    if not audio_path.exists() or audio_path.stat().st_size <= 0:
        raise ToolError(f"Cannot fit missing audio: {audio_path}")
    video_duration = media_duration_seconds(video_path)
    audio_duration = media_duration_seconds(audio_path)
    difference_ratio = abs(video_duration - audio_duration) / max(video_duration, audio_duration)
    should_fit = mode == "video_locked"
    source = "r2v_lipsync_audio_duration_fit"
    if mode == "heygen_provider_limit":
        source = "heygen_lipsync_audio_duration_fit"
        should_fit = difference_ratio >= HEYGEN_LIPSYNC_FIT_TRIGGER_RATIO
    meta = {
        "source": source,
        "mode": mode,
        "input_audio_path": rel(workspace, audio_path),
        "video_path": rel(workspace, video_path),
        "video_duration_seconds": round(video_duration, 3),
        "audio_duration_seconds": round(audio_duration, 3),
        "target_duration_seconds": round(video_duration, 3),
        "difference_ratio": round(difference_ratio, 6),
        "applied": should_fit,
    }
    if mode == "heygen_provider_limit":
        meta["max_difference_ratio"] = HEYGEN_LIPSYNC_MAX_DURATION_DIFFERENCE_RATIO
        meta["fit_trigger_ratio"] = HEYGEN_LIPSYNC_FIT_TRIGGER_RATIO
    if not should_fit:
        return audio_path, meta
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tempo = max(0.05, min(20.0, audio_duration / video_duration))
    filters = ",".join([
        "aresample=48000",
        "aformat=channel_layouts=stereo",
        atempo_filter_chain(tempo),
        f"apad=pad_dur={video_duration:.6f}",
        f"atrim=duration={video_duration:.6f}",
        "asetpts=N/SR/TB",
    ])
    command = [
        ffmpeg_executable(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(audio_path),
        "-vn",
        "-af",
        filters,
        "-c:a",
        "pcm_s16le",
        str(output_path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0 or not output_path.exists() or output_path.stat().st_size <= 0:
        label = "HeyGen lipsync" if mode == "heygen_provider_limit" else "R2V lipsync"
        raise ToolError(f"{label} audio duration fit failed: {completed.stderr[:1200]}")
    fit_duration = media_duration_seconds(output_path)
    return output_path, {
        **meta,
        "output_audio_path": rel(workspace, output_path),
        "tempo": round(tempo, 6),
        "fit_duration_seconds": round(fit_duration, 3),
    }


def pad_trim_audio_to_video_duration(workspace: Path, audio_path: Path, video_path: Path, output_path: Path) -> tuple[Path, dict[str, Any]]:
    if not video_path.exists() or video_path.stat().st_size <= 0:
        raise ToolError(f"Cannot align audio against missing video: {video_path}")
    if not audio_path.exists() or audio_path.stat().st_size <= 0:
        raise ToolError(f"Cannot align missing audio: {audio_path}")
    video_duration = media_duration_seconds(video_path)
    audio_duration = media_duration_seconds(audio_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    filters = ",".join([
        "aresample=48000",
        "aformat=channel_layouts=stereo",
        f"apad=pad_dur={video_duration:.6f}",
        f"atrim=duration={video_duration:.6f}",
        "asetpts=N/SR/TB",
    ])
    command = [
        ffmpeg_executable(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(audio_path),
        "-vn",
        "-af",
        filters,
        "-c:a",
        "pcm_s16le",
        str(output_path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0 or not output_path.exists() or output_path.stat().st_size <= 0:
        raise ToolError(f"DanceMimic audio duration align failed: {completed.stderr[:1200]}")
    fit_duration = media_duration_seconds(output_path)
    return output_path, {
        "source": "dance_mimic_audio_pad_trim",
        "mode": "video_locked_pad_trim",
        "input_audio_path": rel(workspace, audio_path),
        "video_path": rel(workspace, video_path),
        "output_audio_path": rel(workspace, output_path),
        "video_duration_seconds": round(video_duration, 3),
        "audio_duration_seconds": round(audio_duration, 3),
        "target_duration_seconds": round(video_duration, 3),
        "fit_duration_seconds": round(fit_duration, 3),
        "difference_ratio": round(abs(video_duration - audio_duration) / max(video_duration, audio_duration), 6),
    }


def pad_trim_audio_to_target_duration(workspace: Path, audio_path: Path, target_duration: float, output_path: Path, *, source: str) -> tuple[Path, dict[str, Any]]:
    if not audio_path.exists() or audio_path.stat().st_size <= 0:
        raise ToolError(f"Cannot align missing audio: {audio_path}")
    duration = max(0.1, float(target_duration or 0.0))
    audio_duration = media_duration_seconds(audio_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    filters = ",".join([
        "aresample=48000",
        "aformat=channel_layouts=stereo",
        f"apad=pad_dur={duration:.6f}",
        f"atrim=duration={duration:.6f}",
        "asetpts=N/SR/TB",
    ])
    command = [
        ffmpeg_executable(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(audio_path),
        "-vn",
        "-af",
        filters,
        "-c:a",
        "pcm_s16le",
        str(output_path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0 or not output_path.exists() or output_path.stat().st_size <= 0:
        raise ToolError(f"Audio duration align failed: {completed.stderr[:1200]}")
    fit_duration = media_duration_seconds(output_path)
    return output_path, {
        "source": source,
        "mode": "target_locked_pad_trim",
        "input_audio_path": rel(workspace, audio_path),
        "output_audio_path": rel(workspace, output_path),
        "audio_duration_seconds": round(audio_duration, 3),
        "target_duration_seconds": round(duration, 3),
        "fit_duration_seconds": round(fit_duration, 3),
        "difference_ratio": round(abs(duration - audio_duration) / max(duration, audio_duration), 6),
    }


def replace_video_audio_preserve_video_duration(workspace: Path, video_path: Path, audio_path: Path, output_path: Path) -> dict[str, Any]:
    if not video_path.exists() or video_path.stat().st_size <= 0:
        raise ToolError(f"Cannot sync missing video: {video_path}")
    if not audio_path.exists() or audio_path.stat().st_size <= 0:
        raise ToolError(f"Cannot sync missing audio: {audio_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    video_duration = media_duration_seconds(video_path)
    audio_duration = media_duration_seconds(audio_path)
    pixel_format = media_pixel_format(video_path)
    copy_command = [
        ffmpeg_executable(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    completed = subprocess.run(copy_command, capture_output=True, text=True, check=False)
    video_copy = True
    if completed.returncode != 0 or not output_path.exists() or output_path.stat().st_size <= 0:
        video_copy = False
        reencode_command = [
            ffmpeg_executable(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(audio_path),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-preset",
            FFMPEG_HIGH_QUALITY_VIDEO_PRESET,
            *FFMPEG_HIGH_QUALITY_VIDEO_ARGS,
            "-pix_fmt",
            pixel_format,
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        completed = subprocess.run(reencode_command, capture_output=True, text=True, check=False)
    if completed.returncode != 0 or not output_path.exists() or output_path.stat().st_size <= 0:
        raise ToolError(f"DanceMimic audio/video sync failed: {completed.stderr[:1200]}")
    return {
        "source": "ffmpeg_audio_replace_preserve_video",
        "video_copy": video_copy,
        "video_reencoded": not video_copy,
        "quality_mode": "stream_copy" if video_copy else "high_quality_crf10",
        "pixel_format": pixel_format,
        "video_path": rel(workspace, video_path),
        "audio_path": rel(workspace, audio_path),
        "output_path": rel(workspace, output_path),
        "video_duration_seconds": round(video_duration, 3),
        "audio_duration_seconds": round(audio_duration, 3),
        "output_duration_seconds": round(media_duration_seconds(output_path), 3),
    }


def replace_video_audio_to_target_duration(workspace: Path, video_path: Path, audio_path: Path, target_duration: float, output_path: Path) -> dict[str, Any]:
    if not video_path.exists() or video_path.stat().st_size <= 0:
        raise ToolError(f"Cannot sync missing video: {video_path}")
    if not audio_path.exists() or audio_path.stat().st_size <= 0:
        raise ToolError(f"Cannot sync missing audio: {audio_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    video_duration = media_duration_seconds(video_path)
    audio_duration = media_duration_seconds(audio_path)
    duration = max(0.1, float(target_duration or audio_duration or video_duration))
    fps_value = media_frame_rate(video_path)
    pixel_format = media_pixel_format(video_path)
    stream_copy_tolerance = max(0.05, 1.0 / max(fps_value, 1.0))
    if abs(video_duration - duration) <= stream_copy_tolerance:
        copied = replace_video_audio_preserve_video_duration(workspace, video_path, audio_path, output_path)
        copied.update({
            "source": "ffmpeg_audio_replace_target_duration_stream_copy",
            "target_duration_seconds": round(duration, 3),
            "duration_delta_seconds": round(abs(video_duration - duration), 6),
            "stream_copy_tolerance_seconds": round(stream_copy_tolerance, 6),
        })
        return copied
    pts_multiplier = max(0.05, min(20.0, duration / video_duration))
    speed_factor = video_duration / duration
    fps = frame_rate_arg(fps_value)
    command = [
        ffmpeg_executable(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-filter:v",
        f"setpts={pts_multiplier:.8f}*PTS",
        "-fps_mode",
        "cfr",
        "-r",
        fps,
        "-t",
        f"{duration:.6f}",
        "-c:v",
        "libx264",
        "-preset",
        FFMPEG_HIGH_QUALITY_VIDEO_PRESET,
        *FFMPEG_HIGH_QUALITY_VIDEO_ARGS,
        "-pix_fmt",
        pixel_format,
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0 or not output_path.exists() or output_path.stat().st_size <= 0:
        raise ToolError(f"Audio/video target-duration sync failed: {completed.stderr[:1200]}")
    return {
        "source": "ffmpeg_audio_replace_retime_to_target_duration",
        "video_copy": False,
        "video_reencoded": True,
        "quality_mode": "high_quality_crf10",
        "pixel_format": pixel_format,
        "video_path": rel(workspace, video_path),
        "audio_path": rel(workspace, audio_path),
        "output_path": rel(workspace, output_path),
        "video_duration_seconds": round(video_duration, 3),
        "audio_duration_seconds": round(audio_duration, 3),
        "target_duration_seconds": round(duration, 3),
        "output_duration_seconds": round(media_duration_seconds(output_path), 3),
        "fps": safe_float(fps, 0.0),
        "setpts_multiplier": round(pts_multiplier, 6),
        "speed_factor": round(speed_factor, 6),
    }


def replace_video_audio_to_match_duration(workspace: Path, video_path: Path, audio_path: Path, output_path: Path) -> dict[str, Any]:
    if not video_path.exists() or video_path.stat().st_size <= 0:
        raise ToolError(f"Cannot sync missing video: {video_path}")
    if not audio_path.exists() or audio_path.stat().st_size <= 0:
        raise ToolError(f"Cannot sync missing audio: {audio_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    video_duration = media_duration_seconds(video_path)
    audio_duration = media_duration_seconds(audio_path)
    fps_value = media_frame_rate(video_path)
    pixel_format = media_pixel_format(video_path)
    stream_copy_tolerance = max(0.05, 1.0 / max(fps_value, 1.0))
    if abs(video_duration - audio_duration) <= stream_copy_tolerance:
        copied = replace_video_audio_preserve_video_duration(workspace, video_path, audio_path, output_path)
        copied.update({
            "source": "ffmpeg_audio_replace_stream_copy",
            "duration_delta_seconds": round(abs(video_duration - audio_duration), 6),
            "stream_copy_tolerance_seconds": round(stream_copy_tolerance, 6),
        })
        return copied
    pts_multiplier = max(0.05, min(20.0, audio_duration / video_duration))
    speed_factor = video_duration / audio_duration
    fps = frame_rate_arg(fps_value)
    command = [
        ffmpeg_executable(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-filter:v",
        f"setpts={pts_multiplier:.8f}*PTS",
        "-fps_mode",
        "cfr",
        "-r",
        fps,
        "-c:v",
        "libx264",
        "-preset",
        FFMPEG_HIGH_QUALITY_VIDEO_PRESET,
        *FFMPEG_HIGH_QUALITY_VIDEO_ARGS,
        "-pix_fmt",
        pixel_format,
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0 or not output_path.exists() or output_path.stat().st_size <= 0:
        raise ToolError(f"Audio/video sync failed: {completed.stderr[:1200]}")
    return {
        "source": "ffmpeg_audio_replace_retime",
        "video_copy": False,
        "video_reencoded": True,
        "quality_mode": "high_quality_crf10",
        "pixel_format": pixel_format,
        "video_path": rel(workspace, video_path),
        "audio_path": rel(workspace, audio_path),
        "output_path": rel(workspace, output_path),
        "video_duration_seconds": round(video_duration, 3),
        "audio_duration_seconds": round(audio_duration, 3),
        "fps": safe_float(fps, 0.0),
        "setpts_multiplier": round(pts_multiplier, 6),
        "speed_factor": round(speed_factor, 6),
    }


def sync_segment_audio_to_video(
    workspace: Path,
    segment: dict[str, Any],
    segment_result: dict[str, Any],
    working_dir: Path,
    asset_key: str,
    video_path: Path,
    audio_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    if is_dance_mimic_reference_video_segment(segment):
        outputs = dict_value(segment.get("planned_outputs"))
        target_duration = safe_float(outputs.get("video_duration_seconds"), safe_float(segment.get("planned_video_duration"), 0.0))
        if target_duration <= 0:
            target_duration = media_duration_seconds(audio_path)
        fitted_audio, fit_meta = pad_trim_audio_to_target_duration(
            workspace,
            audio_path,
            target_duration,
            working_dir / f"{asset_key}_DanceMimicAudio_TargetPadTrim.wav",
            source="dance_mimic_audio_pad_trim_to_planned_duration",
        )
        fit_meta["video_path"] = rel(workspace, video_path)
        fit_meta["video_duration_seconds"] = round(media_duration_seconds(video_path), 3)
        segment_result["audio_duration_fit"] = fit_meta
        sync_result = replace_video_audio_to_target_duration(workspace, video_path, fitted_audio, target_duration, output_path)
        sync_result["source"] = "ffmpeg_audio_replace_retime_to_planned_duration"
        return sync_result
    return replace_video_audio_to_match_duration(workspace, video_path, audio_path, output_path)


def ffmpeg_executable() -> str:
    configured = text_value(os.environ.get("OPENCREW_FFMPEG_PATH"))
    if configured and Path(configured).exists():
        return configured
    found = shutil.which("ffmpeg")
    if found:
        return found
    root = Path(__file__).resolve().parents[2]
    candidates = [
        root / ".bin" / "ffmpeg",
        root / "ToolLibrary" / ".bin" / "ffmpeg",
        root / "ToolLibrary" / "vendor" / "static_ffmpeg" / "darwin_arm64" / "ffmpeg",
        root / "vendor" / "static_ffmpeg" / "darwin_arm64" / "ffmpeg",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    raise ToolError("ffmpeg executable is missing; cannot extract video tail frame.")


def ffprobe_executable() -> str:
    configured = text_value(os.environ.get("OPENCREW_FFPROBE_PATH"))
    if configured and Path(configured).exists():
        return configured
    found = shutil.which("ffprobe")
    if found:
        return found
    ffmpeg_path = Path(ffmpeg_executable())
    sibling = ffmpeg_path.with_name("ffprobe")
    if sibling.exists():
        return str(sibling)
    root = Path(__file__).resolve().parents[2]
    candidates = [
        root / ".bin" / "ffprobe",
        root / "ToolLibrary" / ".bin" / "ffprobe",
        root / "ToolLibrary" / "vendor" / "static_ffmpeg" / "darwin_arm64" / "ffprobe",
        root / "vendor" / "static_ffmpeg" / "darwin_arm64" / "ffprobe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    raise ToolError("ffprobe executable is missing; cannot probe media duration.")


def first_frame_for_segment(workspace: Path, segment: dict[str, Any], image_output_path: Path | None) -> Path:
    first_frame = dict_value(segment.get("first_frame"))
    if image_output_path and image_output_path.exists():
        return image_output_path
    materialize = dict_value(first_frame.get("materialize_first_frame"))
    source_path = text_value(materialize.get("copy_from_path") if materialize.get("required") else first_frame.get("source_path"))
    source = workspace_path(workspace, source_path) if source_path else None
    if is_dance_mimic_reference_video_segment(segment) and (
        not source_path
        or source is None
        or not source.exists()
        or not source.is_file()
        or source.stat().st_size <= 0
    ):
        raise ToolError(f"dancemimic_first_frame_missing: {segment_asset_key(segment)} has no usable first-frame source.")
    if is_dance_mimic_reference_video_segment(segment) and source is not None and source.suffix.lower() in VIDEO_EXTS:
        raise ToolError(
            "dancemimic_first_frame_from_reference_video_forbidden: "
            f"{segment_asset_key(segment)} must use a target identity image, not a video frame source."
        )
    return copy_to_working(workspace, source_path, f"{segment_asset_key(segment)}_FirstFrame.png")


def record_model_call(prompt_dir: Path, asset_key: str, kind: str, request: dict[str, Any], response: dict[str, Any] | None = None) -> None:
    write_json(prompt_dir / f"ModelCall_{asset_key}_{kind}_request.json", request)
    if response is not None:
        write_json(prompt_dir / f"ModelCall_{asset_key}_{kind}_response.json", response)
        try:
            record_model_call_from_prompt_dir(
                prompt_dir=prompt_dir,
                tool_dir_name=TOOL_DIR_NAME,
                tool_name=TOOL_NAME,
                step_index=9,
                asset_key=asset_key,
                kind=kind,
                request=request,
                response=response,
            )
        except Exception as exc:
            detail = redact_secret_text(str(exc))[:1000]
            sys.stderr.write(f"[{TOOL_NAME}] provider audit failed for {asset_key}/{kind}: {detail}\n")


def execute_segment(
    workspace: Path,
    args: Args,
    variables: dict[str, Any],
    plan: dict[str, Any],
    storyboard: dict[str, Any],
    reference_manifests: dict[str, Any],
    shot: dict[str, Any],
    scene: dict[str, Any],
    segment: dict[str, Any],
    dialogue_index: dict[str, dict[str, Any]],
    result: dict[str, Any],
    tracker: ExecutionTracker | None = None,
) -> dict[str, Any]:
    asset_key = segment_asset_key(segment)
    segment_id = text_value(segment.get("segment_id"))
    prompt_dir = workspace / TOOL_DIR_NAME / "Prompt"
    working_dir = workspace / TOOL_DIR_NAME / "Working"
    outputs = dict_value(segment.get("planned_outputs"))
    tasks = dict_value(segment.get("tasks"))
    segment_result: dict[str, Any] = {
        "segment_id": segment_id,
        "asset_key": asset_key,
        "dialogue_asset_keys": segment_dialogue_asset_keys(segment),
        "dialogue_ids": segment_dialogue_asset_keys(segment),
        "status": "completed",
        "outputs": {},
        "model_calls": {},
        "warnings": [],
    }
    validate_dance_mimic_segment_dependencies(workspace, segment, result)

    dialogue_audio_files: list[Path] = []
    tracker.step(segment_id, "audio", "running_generate" if tasks.get("need_audio") else "running_copy") if tracker else None
    for audio_task in list_value(segment.get("dialogue_audio_tasks")):
        if not isinstance(audio_task, dict):
            continue
        srt_id = text_value(audio_task.get("srt_id"))
        dialogue_asset_key = dialogue_audio_task_asset_key(audio_task, segment)
        if not dialogue_asset_key:
            raise ToolError(f"Dialogue audio task is missing dialogue_asset_key: {srt_id or asset_key}.")
        existing_audio = text_value(audio_task.get("existing_audio_path"))
        audio_task_with_key = {**audio_task, "dialogue_asset_key": dialogue_asset_key}
        bound_audio_rel = standard_dialogue_audio_path(audio_task_with_key, srt_id, asset_key)
        source_audio_rel = existing_audio if existing_audio and workspace_path(workspace, existing_audio).exists() else ""
        if not source_audio_rel and bound_audio_rel and workspace_path(workspace, bound_audio_rel).exists():
            source_audio_rel = bound_audio_rel
        if source_audio_rel:
            audio_file = copy_to_working(workspace, source_audio_rel, f"{safe_name(dialogue_asset_key, asset_key)}_DialogueAudio.wav")
            if tool_runtime_asset_path(source_audio_rel) or (
                bound_audio_rel.startswith(f"{STORYBOARD_WORKING_REL}/")
                and not workspace_path(workspace, bound_audio_rel).exists()
            ):
                bound_audio_rel = publish_file(workspace, audio_file, bound_audio_rel, result)
        elif audio_task.get("need_audio"):
            if not args.execute_audio:
                raise ToolError(f"Audio generation is required but disabled for {srt_id}.")
            dialogue = dict_value(dialogue_index.get(dialogue_asset_key, {}).get("dialogue"))
            tts_config = load_provider_config(args, variables, "tts", args.tts_provider, args.tts_model)
            tts_config = {**tts_config, "voice": tts_voice_for_config(tts_config, variables)}
            audio_file = working_dir / f"{safe_name(dialogue_asset_key, asset_key)}_Audio_Generated.wav"
            prompt_text = tts_prompt_for_dialogue(dialogue)
            record_model_call(prompt_dir, safe_name(dialogue_asset_key, asset_key), "TTS", {"provider_config": redact_config(tts_config), "prompt": prompt_text})
            tts_response = generate_tts_with_provider(tts_config, prompt_text, audio_file, args.provider_timeout_seconds)
            record_model_call(prompt_dir, safe_name(dialogue_asset_key, asset_key), "TTS", {"provider_config": redact_config(tts_config), "prompt": prompt_text}, tts_response)
            bound_audio_rel = publish_file(workspace, audio_file, bound_audio_rel, result)
        else:
            raise ToolError(f"Dialogue audio is missing and not planned for generation: {srt_id}.")
        audio_source_type = (
            "dance_mimic_reference_audio"
            if is_dance_mimic_reference_video_segment(segment) and source_audio_rel
            else text_value(audio_task.get("source_type") or "generated")
        )
        if bound_audio_rel and bind_segment_output_to_storyboard({"dialogue_asset_keys": [dialogue_asset_key]}, dialogue_index, "audio", bound_audio_rel, audio_source_type):
            segment_result.setdefault("outputs", {}).setdefault("storyboard_audio_bound_to_dialogues", []).append(dialogue_asset_key)
            persist_storyboard_asset_bindings(workspace, storyboard, result)
        dialogue_audio_files.append(audio_file)

    segment_audio_rel = text_value(outputs.get("segment_audio_path")) or f"{STORYBOARD_WORKING_REL}/{asset_key}_SegmentAudio_Final.wav"
    segment_audio_working = working_dir / f"{asset_key}_SegmentAudio_Final.wav"
    if not dialogue_audio_files and is_dance_mimic_reference_video_segment(segment):
        silence_duration = safe_float(outputs.get("video_duration_seconds"), safe_float(segment.get("planned_video_duration"), safe_float(segment.get("duration"), 4.0)))
        audio_result = generate_silent_segment_audio(workspace, segment_audio_working, silence_duration)
    else:
        audio_result = compose_segment_audio(workspace, dialogue_audio_files, segment_audio_working)
    publish_file(workspace, segment_audio_working, segment_audio_rel, result)
    segment_result["outputs"]["segment_audio_path"] = segment_audio_rel
    segment_result["audio"] = audio_result
    tracker.step(segment_id, "audio", "completed_working", outputs={"segment_audio_path": segment_audio_rel}) if tracker else None

    image_output_working: Path | None = None
    continuity_privacy_grid: dict[str, Any] = {}
    if tasks.get("need_image_prompt") or tasks.get("need_image"):
        tracker.step(segment_id, "image", "running_generate") if tracker else None
        image_references = prepare_image_references(workspace, segment, plan)
        image_reference_paths = [workspace_path(workspace, item["working_path"]) for item in image_references if text_value(item.get("working_path"))]
        target_frame_reference = reference_by_kind(image_references, "target_frame")
        target_frame_path = workspace_path(workspace, text_value(target_frame_reference.get("working_path"))) if text_value(target_frame_reference.get("working_path")) else None
        image_selection, image_provider_reference_assessment = image_provider_selection_for_references(args, variables, image_references)
        image_prompt_rel = text_value(outputs.get("image_prompt_path")) or f"{STORYBOARD_WORKING_REL}/{asset_key}_ImagePrompt.json"
        image_prompt_working: Path | None = None
        if tasks.get("need_image_prompt"):
            image_module = image_module_for(image_selection.get("provider", ""), image_selection.get("model", ""))
            image_prompt_context = {
                "workspace": str(workspace),
                "prompt_dir": str(prompt_dir),
                "segment": segment,
                "shot": shot,
                "scene": scene,
                "dialogue_index": dialogue_index,
                "references": image_references,
                "reference_manifests": reference_manifests,
            }
            image_prompt = image_module.build_prompt_package(image_prompt_context)
            if image_provider_reference_assessment:
                image_prompt["provider_reference_assessment"] = image_provider_reference_assessment
            image_prompt_working = image_module.write_prompt_package(prompt_dir, asset_key, image_prompt)
            publish_file(workspace, image_prompt_working, image_prompt_rel, result)
            segment_result["outputs"]["image_prompt_path"] = image_prompt_rel
        else:
            existing_prompt = workspace_path(workspace, image_prompt_rel)
            if existing_prompt.exists() and existing_prompt.is_file() and existing_prompt.stat().st_size > 0:
                image_prompt_working = prompt_dir / f"PromptRendered_{asset_key}_ImagePrompt.json"
                image_prompt_working.parent.mkdir(parents=True, exist_ok=True)
                if existing_prompt.resolve() != image_prompt_working.resolve():
                    shutil.copy2(existing_prompt, image_prompt_working)
                append_created_file(result, rel(workspace, image_prompt_working))
                segment_result["outputs"]["image_prompt_path"] = image_prompt_rel
        if tasks.get("need_image"):
            if image_prompt_working is None:
                raise ToolError(f"Image prompt is required but missing for {asset_key}: {image_prompt_rel}")
            if not args.execute_image:
                raise ToolError(f"Image generation is required but disabled for {asset_key}.")
            image_config = load_provider_config(args, variables, "image", image_selection["provider"], image_selection["model"])
            image_output_working = working_dir / f"{asset_key}_Image_New.png"
            image_request = {
                "provider_config": redact_config(image_config),
                "prompt_path": rel(workspace, image_prompt_working),
                "reference_count": len(image_reference_paths),
                "reference_paths": [rel(workspace, path) for path in image_reference_paths],
                "reference_roles": image_references,
                "target_aspect": "9:16",
                "target_frame_path": rel(workspace, target_frame_path) if target_frame_path else "",
            }
            if image_provider_reference_assessment:
                image_request["provider_reference_assessment"] = image_provider_reference_assessment
                segment_result["image_provider_reference_assessment"] = image_provider_reference_assessment
            record_model_call(prompt_dir, asset_key, "Image", image_request)
            image_response = generate_image_with_provider(image_config, image_prompt_working, image_output_working, image_reference_paths, args.provider_timeout_seconds)
            image_response["dimension_normalization"] = normalize_image_to_target_aspect(image_output_working, target_frame_path)
            image_output_working, max_sd_2_privacy_grid = prepare_max_sd_2_oral_privacy_frame(
                workspace,
                variables,
                storyboard,
                segment,
                image_output_working,
                working_dir,
                asset_key,
                provider_override=args.video_provider,
                model_override=args.video_model,
            )
            if max_sd_2_privacy_grid:
                continuity_privacy_grid = max_sd_2_privacy_grid
                image_response["continuity_privacy_grid"] = max_sd_2_privacy_grid
                segment_result["continuity_privacy_grid"] = max_sd_2_privacy_grid
            record_model_call(prompt_dir, asset_key, "Image", image_request, image_response)
            image_rel = text_value(outputs.get("image_path"))
            if image_rel:
                publish_file(workspace, image_output_working, image_rel, result)
                segment_result["outputs"]["image_path"] = image_rel
                if bind_segment_output_to_storyboard(segment, dialogue_index, "image", image_rel):
                    segment_result["outputs"]["storyboard_image_bound_to_dialogue"] = segment_dialogue_asset_keys(segment)[0] if segment_dialogue_asset_keys(segment) else ""
                    persist_storyboard_asset_bindings(workspace, storyboard, result)
                tracker.step(segment_id, "image", "completed_working", outputs={"image_path": image_rel}) if tracker else None
    else:
        materialize = dict_value(dict_value(segment.get("first_frame")).get("materialize_first_frame"))
        copy_to_path = text_value(materialize.get("copy_to_path"))
        if materialize.get("required") and copy_to_path:
            tracker.step(segment_id, "image", "running_copy") if tracker else None
            copied = copy_to_working(workspace, text_value(materialize.get("copy_from_path")), f"{asset_key}_MaterializedFirstFrame{Path(text_value(materialize.get('copy_from_path'))).suffix or '.png'}")
            image_output_working = copied
            image_output_working, continuity_privacy_grid = prepare_privacy_grid_continuity_frame(
                workspace,
                segment,
                image_output_working,
                working_dir,
                asset_key,
                result,
            )
            if not continuity_privacy_grid:
                image_output_working, continuity_privacy_grid = prepare_max_sd_2_oral_privacy_frame(
                    workspace,
                    variables,
                    storyboard,
                    segment,
                    image_output_working,
                    working_dir,
                    asset_key,
                    provider_override=args.video_provider,
                    model_override=args.video_model,
                )
            if continuity_privacy_grid:
                segment_result["continuity_privacy_grid"] = continuity_privacy_grid
            publish_file(workspace, image_output_working, copy_to_path, result)
            segment_result["outputs"]["image_path"] = copy_to_path
            materialized_source_type = "tail_frame_materialized" if text_value(materialize.get("source_type")) in {"previous_segment_tail_frame", "previous_scene_tail_frame"} else "generated"
            if bind_segment_output_to_storyboard(segment, dialogue_index, "image", copy_to_path, materialized_source_type):
                segment_result["outputs"]["storyboard_image_bound_to_dialogue"] = segment_dialogue_asset_keys(segment)[0] if segment_dialogue_asset_keys(segment) else ""
                persist_storyboard_asset_bindings(workspace, storyboard, result)
            tracker.step(segment_id, "image", "completed_working", outputs={"image_path": copy_to_path}) if tracker else None

    first_frame = dict_value(segment.get("first_frame"))
    existing_video = dict_value(segment.get("existing_video"))
    materialize_video = dict_value(existing_video.get("materialize_video"))
    is_bound_video = first_frame.get("source_type") == "bound_video" or bool(text_value(existing_video.get("path")))
    if is_bound_video and not tasks.get("need_video", True):
        tracker.step(segment_id, "image", "skipped", reason="bound_video") if tracker else None
        tracker.step(segment_id, "video", "running_copy") if tracker else None
        requested_source_video_rel = text_value(materialize_video.get("copy_from_path") or existing_video.get("path") or first_frame.get("source_path"))
        if not requested_source_video_rel:
            raise ToolError(f"Bound video segment is missing source video path: {asset_key}")
        video_rel = text_value(outputs.get("video_path") or materialize_video.get("copy_to_path")) or f"{STORYBOARD_WORKING_REL}/{asset_key}_Video_Final.mp4"
        source_video_rel, source_preference = bound_video_source_for_sync(workspace, asset_key, outputs, requested_source_video_rel)
        source_suffix = Path(source_video_rel).suffix or ".mp4"
        bound_working = copy_to_working(workspace, source_video_rel, f"{asset_key}_BoundVideo{source_suffix}")
        tracker.step(segment_id, "sync", "running_generate") if tracker else None
        if not args.execute_audio_video_sync:
            message = f"Audio/video sync is required but disabled for bound video {asset_key}."
            tracker.step(segment_id, "sync", "failed", error=message) if tracker else None
            raise ToolError(message)
        synced_video_working = working_dir / f"{asset_key}_Video_AudioSynced.mp4"
        sync_result = sync_segment_audio_to_video(workspace, segment, segment_result, working_dir, asset_key, bound_working, segment_audio_working, synced_video_working)
        publish_file(workspace, synced_video_working, video_rel, result)
        segment_result["outputs"]["video_path"] = video_rel
        if bind_segment_output_to_storyboard(segment, dialogue_index, "video", video_rel):
            segment_result["outputs"]["storyboard_video_bound_to_dialogue"] = segment_dialogue_asset_keys(segment)[0] if segment_dialogue_asset_keys(segment) else ""
            persist_storyboard_asset_bindings(workspace, storyboard, result)
        segment_result["completed_by_bound_video"] = True
        segment_result["source_video_path_requested"] = requested_source_video_rel
        segment_result["source_video_path"] = source_video_rel
        segment_result["source_video_preference"] = source_preference
        segment_result["working_video_path"] = video_rel
        segment_result["sync"] = sync_result
        tracker.step(segment_id, "video", "completed_working", outputs={"video_path": video_rel}) if tracker else None
        tracker.step(segment_id, "sync", "completed_working", outputs={"video_path": video_rel}) if tracker else None
        tail_rel, tail_result = publish_segment_tail_frame(
            workspace,
            result,
            tracker,
            segment_id,
            segment,
            asset_key,
            working_dir,
            synced_video_working,
        )
        segment_result["outputs"]["tail_frame_path"] = tail_rel
        segment_result["tail_frame"] = tail_result
        return segment_result

    default_storyboard_raw_rel = f"{STORYBOARD_WORKING_REL}/{asset_key}_Video_Raw.mp4"
    planned_raw_rel = text_value(outputs.get("raw_video_path")) or default_storyboard_raw_rel
    raw_video = working_dir / f"{asset_key}_Video_Raw{Path(planned_raw_rel).suffix or '.mp4'}"
    video_rel = text_value(outputs.get("video_path")) or f"{STORYBOARD_WORKING_REL}/{asset_key}_Video_Final.mp4"
    video_response: dict[str, Any] = {}
    raw_candidates = [planned_raw_rel, default_storyboard_raw_rel]
    if first_frame.get("source_type") == "existing_raw_video":
        raw_candidates.insert(0, text_value(first_frame.get("source_path")))
    raw_source_rel = next(
        (
            candidate
            for candidate in raw_candidates
            if candidate and workspace_path(workspace, candidate).exists() and workspace_path(workspace, candidate).is_file() and workspace_path(workspace, candidate).stat().st_size > 0
        ),
        "",
    )
    if raw_source_rel:
        tracker.step(segment_id, "video", "running_copy", reason="storyboard_raw_video_reuse") if tracker else None
        copied_raw = copy_to_working(workspace, raw_source_rel, f"{asset_key}_Video_Raw{Path(raw_source_rel).suffix or '.mp4'}")
        raw_video = copied_raw
        segment_result["outputs"]["raw_video_path"] = raw_source_rel
        segment_result["video_source"] = "storyboard_raw_reused"
        tracker.step(segment_id, "video", "completed_working", outputs={"raw_video_path": raw_source_rel}) if tracker else None
    else:
        if not tasks.get("need_video", True):
            raise ToolError(f"Existing Raw video is required but missing for {asset_key}: {planned_raw_rel}")
        first_frame_path = first_frame_for_segment(workspace, segment, image_output_working)
        if not continuity_privacy_grid:
            first_frame_path, continuity_privacy_grid = prepare_privacy_grid_continuity_frame(
                workspace,
                segment,
                first_frame_path,
                working_dir,
                asset_key,
                result,
            )
            if not continuity_privacy_grid:
                first_frame_path, continuity_privacy_grid = prepare_max_sd_2_oral_privacy_frame(
                    workspace,
                    variables,
                    storyboard,
                    segment,
                    first_frame_path,
                    working_dir,
                    asset_key,
                    provider_override=args.video_provider,
                    model_override=args.video_model,
                )
            if continuity_privacy_grid:
                segment_result["continuity_privacy_grid"] = continuity_privacy_grid
        first_frame_normalization = normalize_video_first_frame(first_frame_path)
        target_video_aspect = text_value(first_frame_normalization.get("aspect")) or "9:16"
        segment_result["video_first_frame_normalization"] = first_frame_normalization
        video_selection = video_selection_for_segment(variables, args, segment)
        video_module = video_module_for(video_selection.get("provider", ""), video_selection.get("model", ""))
        video_config = load_video_provider_config_for_segment(args, variables, segment)
        requested_video_duration = safe_float(outputs.get("video_duration_seconds"), safe_float(segment.get("planned_video_duration"), 4.0))
        audio_duration_seconds = media_duration_seconds(segment_audio_working) if segment_audio_working.exists() else 0.0
        provider_duration = provider_video_seconds(video_config, requested_video_duration, audio_duration_seconds)
        provider_task_state_path = working_dir / f"{asset_key}_Video_ProviderTask.json"
        reference_images, reference_image_roles = dance_mimic_video_reference_images(workspace, segment, first_frame_path, dialogue_index)
        reference_videos = prepare_dance_mimic_reference_videos(workspace, segment, working_dir, asset_key, result)
        if not reference_videos:
            reference_videos = prepare_max_sd_2_reference_videos(
                workspace,
                segment,
                video_selection,
                working_dir,
                asset_key,
                result,
            )
        if dance_mimic_privacy_grid_fields(segment)["enabled"]:
            identity_role = next((item for item in reference_image_roles if "target_identity" in text_value(item.get("role"))), None)
            identity_path = workspace_path(workspace, text_value(dict_value(identity_role).get("path"))) if identity_role else None
            if identity_path is None or not reference_videos:
                raise ToolError("privacy_grid_provider_preflight_failed: required provider references were not resolved.")
            validate_privacy_grid_provider_inputs(workspace, segment, identity_path, reference_videos[0])
        video_prompt = video_module.build_prompt_package({
            "workspace": str(workspace),
            "prompt_dir": str(prompt_dir),
            "reference_images": [str(path) for path in reference_images],
            "segment": segment,
            "shot": shot,
            "scene": scene,
            "dialogue_index": dialogue_index,
            "reference_image_roles": reference_image_roles,
            "prompt_template": text_value(video_selection.get("prompt_template")),
            "config": video_config,
            "aspect": target_video_aspect,
            "aspect_ratio": target_video_aspect,
            "requested_aspect": target_video_aspect,
        })
        video_prompt = prompt_package_for_video_aspect(video_prompt, target_video_aspect)
        video_prompt_working = video_module.write_prompt_package(prompt_dir, asset_key, video_prompt)
        video_prompt_rel = text_value(outputs.get("video_prompt_path"))
        if video_prompt_rel:
            publish_file(workspace, video_prompt_working, video_prompt_rel, result)
            segment_result["outputs"]["video_prompt_path"] = video_prompt_rel
        if not args.execute_video:
            message = f"Video generation is required but disabled for {asset_key}."
            tracker.step(segment_id, "video", "failed", error=message) if tracker else None
            raise ToolError(message)
        tracker.step(segment_id, "video", "running_generate") if tracker else None
        video_request = {
            "provider_config": redact_config(video_config),
            "prompt_path": rel(workspace, video_prompt_working),
            "first_frame": rel(workspace, first_frame_path),
            "reference_images": [rel(workspace, path) for path in reference_images],
            "reference_image_roles": reference_image_roles,
            "requested_duration_seconds": requested_video_duration,
            "audio_duration_seconds": round(audio_duration_seconds, 3) if audio_duration_seconds else None,
            "provider_duration_seconds": provider_duration,
            "provider_task_state_path": rel(workspace, provider_task_state_path),
            "aspect_ratio": target_video_aspect,
            "first_frame_normalization": first_frame_normalization,
        }
        if continuity_privacy_grid:
            video_request["continuity_privacy_grid"] = continuity_privacy_grid
        if is_wan_rtv_model(text_value(video_config.get("provider")), text_value(video_config.get("model"))):
            video_request["reference_video"] = rel(workspace, working_dir / WAN_RTV_REFERENCE_VIDEO_NAME)
            video_request["provider_size"] = wan_rtv_video_size_for_image(first_frame_path, video_config)
        if is_kling_omni_model(text_value(video_config.get("provider")), text_value(video_config.get("model"))):
            video_request["reference_video"] = rel(workspace, working_dir / KLING_OMNI_REFERENCE_VIDEO_NAME)
        if reference_videos:
            video_request["reference_video"] = rel(workspace, reference_videos[0])
            video_request["reference_videos"] = [rel(workspace, path) for path in reference_videos]
            video_request["reference_video_count"] = len(reference_videos)
            video_request["reference_mode"] = text_value(video_selection.get("reference_mode") or video_config.get("reference_mode"))
            video_request["video_generation_mode"] = text_value(video_selection.get("video_generation_mode") or video_config.get("video_generation_mode"))
            video_request["reference_video_role"] = text_value(
                video_selection.get("reference_video_role")
                or video_config.get("reference_video_role")
                or (DANCE_MIMIC_REFERENCE_VIDEO_ROLE if is_dance_mimic_reference_video_segment(segment) else "")
            )
        record_model_call(prompt_dir, asset_key, "Video", video_request)
        video_response = generate_video_with_provider(
            video_config,
            video_prompt_working,
            raw_video,
            reference_images,
            requested_video_duration,
            args.provider_timeout_seconds,
            provider_task_state_path,
            audio_duration_seconds,
            reference_videos=reference_videos,
            requested_aspect=target_video_aspect,
        )
        record_model_call(prompt_dir, asset_key, "Video", video_request, video_response)
        segment_result["model_calls"]["video"] = {"config": redact_config(video_config), "response": video_response}
        publish_file(workspace, raw_video, planned_raw_rel, result)
        segment_result["outputs"]["raw_video_path"] = planned_raw_rel
        tracker.step(segment_id, "video", "completed_working", outputs={"raw_video_path": planned_raw_rel}) if tracker else None
    segment_result["outputs"]["video_path"] = video_rel

    need_lipsync = bool(tasks.get("need_lipsync", True))
    final_video_working = raw_video
    sync_result: dict[str, Any] | None = None
    if need_lipsync:
        if not args.execute_lipsync:
            message = f"Lip-sync is required but disabled for {asset_key}."
            tracker.step(segment_id, "sync", "failed", error=message) if tracker else None
            raise ToolError(message)
        tracker.step(segment_id, "sync", "running_generate") if tracker else None
        lipsync_config = load_provider_config(args, variables, "lipsync", args.lipsync_provider, args.lipsync_model)
        lipsync_source_video_url = first_url(video_response)
        if lipsync_source_video_url and not text_value(lipsync_config.get("video_url") or lipsync_config.get("source_video_url")):
            lipsync_config = {**lipsync_config, "source_video_url": lipsync_source_video_url}
        lipsync_output = working_dir / f"{asset_key}_Video_LipSync.mp4"
        lipsync_module = lipsync_module_for(text_value(lipsync_config.get("provider")), text_value(lipsync_config.get("model")))
        lipsync_audio_working = segment_audio_working
        lipsync_audio_fit: dict[str, Any] = {}
        video_selection_for_duration_policy = video_selection_for_segment(variables, args, segment)
        audio_fit_mode = lipsync_audio_fit_mode(lipsync_config, video_selection_for_duration_policy)
        if audio_fit_mode:
            lipsync_audio_working, lipsync_audio_fit = fit_audio_to_video_duration(
                workspace,
                segment_audio_working,
                raw_video,
                working_dir / f"{asset_key}_LipSyncAudio_Fit.wav",
                mode=audio_fit_mode,
            )
            lipsync_audio_fit = {
                **lipsync_audio_fit,
                "lipsync_provider": text_value(lipsync_config.get("provider")),
                "video_provider": text_value(video_selection_for_duration_policy.get("provider")),
                "video_model": text_value(video_selection_for_duration_policy.get("model")),
            }
            segment_result["lipsync_audio_fit"] = lipsync_audio_fit
        lipsync_prompt = lipsync_module.build_prompt_package({
            "workspace": str(workspace),
            "prompt_dir": str(prompt_dir),
            "segment": segment,
            "video_path": str(raw_video),
            "source_video_url": lipsync_source_video_url,
            "source_video_id": text_value(lipsync_config.get("video_id") or lipsync_config.get("source_video_id")),
            "audio_path": str(lipsync_audio_working),
            "output_path": str(lipsync_output),
        })
        lipsync_prompt_path = lipsync_module.write_prompt_package(prompt_dir, asset_key, lipsync_prompt)
        request_path = prompt_dir / f"ModelCall_{asset_key}_LipSync_request.json"
        status_path = prompt_dir / f"ModelCall_{asset_key}_LipSync_status.json"
        response_path = prompt_dir / f"ModelCall_{asset_key}_LipSync_response.json"
        try:
            lipsync_response = run_lipsync_with_provider(
                lipsync_config,
                raw_video,
                lipsync_audio_working,
                lipsync_output,
                request_path,
                status_path,
                response_path,
                args.provider_timeout_seconds,
                lipsync_prompt_path,
                segment,
            )
        except Exception as exc:
            try:
                tail_rel, tail_result = publish_segment_tail_frame(
                    workspace,
                    result,
                    tracker,
                    segment_id,
                    segment,
                    asset_key,
                    working_dir,
                    raw_video,
                    reason="raw_video_tail_after_lipsync_failure",
                )
                segment_result["outputs"]["tail_frame_path"] = tail_rel
                segment_result["tail_frame"] = {
                    **tail_result,
                    "fallback_reason": "lipsync_failed_after_raw_video_generated",
                }
                segment_result.setdefault("warnings", []).append({
                    "code": "tail_frame_extracted_from_raw_video_after_lipsync_failure",
                    "message": "Lip-sync failed after raw video generation; extracted raw tail frame for diagnostics only. Dependent segments remain blocked until Final is produced.",
                    "tail_frame_path": tail_rel,
                })
            except Exception as tail_exc:
                segment_result.setdefault("warnings", []).append({
                    "code": "tail_frame_fallback_failed_after_lipsync_failure",
                    "message": str(tail_exc),
                })
            tracker.step(segment_id, "sync", "failed", error=str(exc)) if tracker else None
            raise
        record_model_call(
            prompt_dir,
            asset_key,
            "LipSync",
            {
                "provider_config": redact_config(lipsync_config),
                "prompt_path": rel(workspace, lipsync_prompt_path),
                "video_path": rel(workspace, raw_video),
                "audio_path": rel(workspace, lipsync_audio_working),
                "upload_video_path": lipsync_response.get("upload_video_path"),
                "upload_audio_path": lipsync_response.get("upload_audio_path"),
                "preparation": lipsync_response.get("preparation"),
                "audio_fit": lipsync_audio_fit,
            },
            lipsync_response,
        )
        final_video_working = lipsync_output
        segment_result["model_calls"]["lipsync"] = {"config": redact_config(lipsync_config), "response": lipsync_response}
    else:
        if not args.execute_audio_video_sync:
            message = f"Audio/video sync is required but disabled for {asset_key}."
            tracker.step(segment_id, "sync", "failed", error=message) if tracker else None
            raise ToolError(message)
        tracker.step(segment_id, "sync", "running_generate") if tracker else None
        synced_video = working_dir / f"{asset_key}_Video_AudioSynced.mp4"
        sync_result = sync_segment_audio_to_video(workspace, segment, segment_result, working_dir, asset_key, raw_video, segment_audio_working, synced_video)
        final_video_working = synced_video
        segment_result["sync"] = sync_result

    if final_video_working != raw_video:
        publish_file(workspace, final_video_working, video_rel, result)
        if bind_segment_output_to_storyboard(segment, dialogue_index, "video", video_rel):
            segment_result["outputs"]["storyboard_video_bound_to_dialogue"] = segment_dialogue_asset_keys(segment)[0] if segment_dialogue_asset_keys(segment) else ""
            persist_storyboard_asset_bindings(workspace, storyboard, result)
    tracker.step(segment_id, "sync", "completed_working", outputs={"video_path": video_rel}) if tracker and (need_lipsync or sync_result) else None
    tail_rel, tail_result = publish_segment_tail_frame(
        workspace,
        result,
        tracker,
        segment_id,
        segment,
        asset_key,
        working_dir,
        final_video_working,
    )
    segment_result["outputs"]["tail_frame_path"] = tail_rel
    segment_result["tail_frame"] = tail_result
    return segment_result


def normalized_output_key(key: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(key or "").lower()).strip("_")


def has_sensitive_field_value(value: Any) -> bool:
    if value is None or isinstance(value, (bool, int, float)):
        return False
    if isinstance(value, str):
        text = value.strip()
        if not text or text.lower() in {"redacted", "<redacted>", "[redacted]", "***"}:
            return False
        if set(text) <= {"*"}:
            return False
    return True


def append_sensitive_warning(warnings: list[dict[str, str]], seen: set[str], pattern: str) -> None:
    if pattern in seen:
        return
    seen.add(pattern)
    warnings.append({"code": "sensitive_output_pattern_detected", "message": f"Output contains sensitive-looking pattern: {pattern}"})


def scan_for_sensitive_output(payload: Any) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    seen: set[str] = set()

    def scan_value(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                normalized_key = normalized_output_key(key)
                if normalized_key in SENSITIVE_OUTPUT_KEYS and has_sensitive_field_value(item):
                    append_sensitive_warning(warnings, seen, normalized_key)
                scan_value(item)
            return
        if isinstance(value, (list, tuple)):
            for item in value:
                scan_value(item)
            return
        if isinstance(value, str):
            text = redact_secret_text(value).lower()
            for pattern in SECRET_VALUE_PATTERNS:
                if pattern in text:
                    append_sensitive_warning(warnings, seen, pattern)
            for regex in SECRET_VALUE_REGEXES:
                if regex.search(text):
                    append_sensitive_warning(warnings, seen, regex.pattern)

    scan_value(json_safe(payload))
    return warnings


def base_result(workspace: Path, args: Args) -> dict[str, Any]:
    return {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "status": "completed",
        "workspace_dir": str(workspace),
        "requires_database": True,
        "requires_model_calls": True,
        "reads_session_context": [VARIABLES_REL],
        "writes_session_context": [],
        "settings": {
            "max_segments": int(args.max_segments),
            "execute_audio": bool(args.execute_audio),
            "execute_image": bool(args.execute_image),
            "execute_video": bool(args.execute_video),
            "execute_lipsync": bool(args.execute_lipsync),
            "execute_audio_video_sync": bool(args.execute_audio_video_sync),
        },
        "created_files": [],
        "cleanup_actions": [],
        "backups": [],
        "segments": [],
        "summary": {},
        "warnings": [],
        "blocked_reasons": [],
        "updated_at": now_iso(),
    }


def summarize_segments(segments: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "segment_count": len(segments),
        "completed_count": sum(1 for item in segments if item.get("status") == "completed"),
        "failed_count": sum(1 for item in segments if str(item.get("status", "")).startswith("failed")),
        "failed_timeout_count": sum(1 for item in segments if item.get("status") == "failed_timeout"),
        "lipsync_completed_count": sum(1 for item in segments if dict_value(item.get("model_calls")).get("lipsync")),
        "audio_video_sync_completed_count": sum(1 for item in segments if text_value(dict_value(item.get("sync")).get("source")).startswith("ffmpeg_audio_replace")),
        "bound_video_completed_count": sum(1 for item in segments if item.get("completed_by_bound_video")),
        "segment_audio_count": sum(1 for item in segments if dict_value(item.get("outputs")).get("segment_audio_path")),
    }


def run(args: Args) -> dict[str, Any]:
    workspace = resolve_workspace(args.workspace)
    result = base_result(workspace, args)
    tracker: ExecutionTracker | None = None
    try:
        if not workspace.exists() or not workspace.is_dir():
            raise ToolError(f"workspace_missing: {workspace}")
        if args.force:
            force_reset(workspace, result)
        ensure_tool_dirs(workspace)
        variables = load_required_json(workspace, VARIABLES_REL, "variables_missing")
        storyboard = load_required_json(workspace, STORYBOARD_REL, "storyboard_missing")
        plan = load_required_json(workspace, PLAN_REL, "plan_missing")
        source_plan_hash = text_value(args.source_plan_hash or plan.get("plan_hash") or plan_hash(plan))
        result["source_plan_hash"] = source_plan_hash
        result["execution_job_id"] = text_value(args.execution_job_id) or f"vp_exec_{now_ms()}_{uuid.uuid4().hex[:8]}"
        tracker = ExecutionTracker(workspace, result["execution_job_id"], source_plan_hash)
        tracker.set_status("running")
        copy_inputs_to_working(workspace, variables, storyboard, plan, result, args)
        reference_manifests = {
            "host": load_optional_json(workspace, "SessionContext/Consistency/host_manifest.json"),
            "product": load_optional_json(workspace, "SessionContext/Consistency/product_manifest.json"),
        }
        dialogue_index = flatten_dialogues(storyboard)
        planned_segments = iter_segments(plan)
        if args.max_segments > 0:
            planned_segments = planned_segments[: args.max_segments]
        if not planned_segments:
            raise ToolError(plan_no_executable_segments_message(plan))
        for shot, scene, segment in planned_segments:
            segment_id = tracker.start_segment(segment) if tracker else text_value(segment.get("segment_id"))
            try:
                segment_result = execute_segment(workspace, args, variables, plan, storyboard, reference_manifests, shot, scene, segment, dialogue_index, result, tracker)
            except ProviderTimeout as exc:
                segment_result = {
                    "segment_id": text_value(segment.get("segment_id")),
                    "asset_key": segment_asset_key(segment),
                    "dialogue_asset_keys": segment_dialogue_asset_keys(segment),
                    "dialogue_ids": segment_dialogue_asset_keys(segment),
                    "status": "failed_timeout",
                    "error": str(exc),
                }
                result["status"] = "completed_with_failed_items"
                tracker.step(segment_id, text_value(tracker.state.get("current_step")) or "segment", "failed", error=str(exc)) if tracker else None
            except Exception as exc:
                segment_result = {
                    "segment_id": text_value(segment.get("segment_id")),
                    "asset_key": segment_asset_key(segment),
                    "dialogue_asset_keys": segment_dialogue_asset_keys(segment),
                    "dialogue_ids": segment_dialogue_asset_keys(segment),
                    "status": "failed",
                    "error": str(exc),
                }
                result["status"] = "completed_with_failed_items"
                tracker.step(segment_id, text_value(tracker.state.get("current_step")) or "segment", "failed", error=str(exc)) if tracker else None
            result.setdefault("segments", []).append(segment_result)
            tracker.finish_segment(segment_id, segment_result) if tracker else None
        result["summary"] = summarize_segments(result["segments"])
        if result["summary"]["completed_count"] == 0:
            result["status"] = "failed"
        tracker.finish(text_value(result.get("status")) or "completed", result["summary"]) if tracker else None
        backup_before_overwrite_once(workspace, workspace / STORYBOARD_REL, result, "storyboard_json_incremental_backup")
        write_json(workspace / STORYBOARD_REL, storyboard)
        append_created_file(result, STORYBOARD_REL)
        if sync_generated_outputs_to_edit(workspace, storyboard, result, backup_once_flag="edit_storyboard_json_incremental_backup"):
            result.setdefault("sync_actions", []).append({
                "code": "edit_storyboard_synced",
                "message": "Generated image/video outputs were also synced to koubo_storyboard_edit.json for the StoryBoard UI.",
            })
        result.pop("_runtime_flags", None)
        write_json(workspace / EXECUTION_RESULT_REL, result)
        write_json(workspace / SESSION_EXECUTION_RESULT_REL, result)
        result["created_files"].extend([EXECUTION_STATE_REL, SESSION_EXECUTION_STATE_REL, EXECUTION_RESULT_REL, SESSION_EXECUTION_RESULT_REL, RESULT_REL])
        warnings = scan_for_sensitive_output(result)
        if warnings:
            result["warnings"].extend(warnings)
            result["status"] = "failed"
            result["blocked_reasons"].append({"code": "sensitive_output_detected", "message": "Sensitive-looking content detected in output files."})
    except Exception as exc:
        ensure_tool_dirs(workspace) if workspace.exists() and workspace.is_dir() else None
        result["status"] = "blocked" if isinstance(exc, ToolError) else "failed"
        result["blocked_reasons"].append({"code": "execution_blocked", "message": str(exc)})
        tracker.set_status(result["status"], str(exc)) if tracker else None
    result["updated_at"] = now_iso()
    result.pop("_runtime_flags", None)
    if workspace.exists() and workspace.is_dir():
        write_json(workspace / RESULT_REL, result)
    if args.print_json:
        print(json.dumps(json_safe(result), ensure_ascii=False, indent=2))
    return result


def parse_args(argv: list[str]) -> Args:
    parser = argparse.ArgumentParser(description="Execute Analysis_V1 StoryBoard video generation plan with real model providers.")
    parser.add_argument("--workspace", default=str(Path.cwd()))
    parser.add_argument("--database-url", default="")
    parser.add_argument("--max-segments", type=int, default=0)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--execute-audio", dest="execute_audio", action="store_true", default=True)
    parser.add_argument("--no-execute-audio", dest="execute_audio", action="store_false")
    parser.add_argument("--execute-image", dest="execute_image", action="store_true", default=True)
    parser.add_argument("--no-execute-image", dest="execute_image", action="store_false")
    parser.add_argument("--execute-video", dest="execute_video", action="store_true", default=True)
    parser.add_argument("--no-execute-video", dest="execute_video", action="store_false")
    parser.add_argument("--execute-lipsync", dest="execute_lipsync", action="store_true", default=True)
    parser.add_argument("--no-execute-lipsync", dest="execute_lipsync", action="store_false")
    parser.add_argument("--execute-audio-video-sync", dest="execute_audio_video_sync", action="store_true", default=True)
    parser.add_argument("--no-execute-audio-video-sync", dest="execute_audio_video_sync", action="store_false")
    parser.add_argument("--image-provider", default="")
    parser.add_argument("--image-model", default="")
    parser.add_argument("--video-provider", default="")
    parser.add_argument("--video-model", default="")
    parser.add_argument("--lipsync-provider", default="")
    parser.add_argument("--lipsync-model", default="")
    parser.add_argument("--tts-provider", default="")
    parser.add_argument("--tts-model", default="")
    parser.add_argument("--provider-timeout-seconds", type=int, default=1800)
    parser.add_argument("--execution-job-id", default="")
    parser.add_argument("--source-plan-hash", default="")
    parser.add_argument("--print-json", action="store_true")
    parsed = parser.parse_args(argv)
    return Args(**vars(parsed))


def main(argv: list[str]) -> int:
    if "--tool-session-root" in argv:
        try:
            from ToolLibrary.Analysis_V1.framework_bridge import maybe_run_framework_bridge
        except ModuleNotFoundError:
            repo_root = str(Path(__file__).resolve().parents[2])
            if repo_root not in sys.path:
                sys.path.insert(0, repo_root)
            from ToolLibrary.Analysis_V1.framework_bridge import maybe_run_framework_bridge

        framework_exit = maybe_run_framework_bridge(argv, script_path=Path(__file__), tool_name=TOOL_NAME)
        if framework_exit is not None:
            return framework_exit

    result = run(parse_args(argv))
    return 0 if result.get("status") in {"completed", "completed_with_failed_items"} else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
