from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import queue
import re
import shutil
import threading
import time
import urllib.parse
import uuid
from typing import Any
from urllib.error import HTTPError

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse

from opcrew_backend.context import now_ms
from opcrew_backend.model_policy import (
    SURFACE_KOUBO_ASSET_AGENT_CHAT,
    mask_model_fields_for_role,
    mask_prompt_models_for_role,
    request_role,
)
from opcrew_backend.routes.media_model_config import customer_media_public_alias_target, customer_media_public_config, load_agent_model_aliases, load_config, load_configured_active_provider, option_by_provider
from opcrew_backend.routes.auth import AUTH_ROLE_USER
from opcrew_backend.services.media_sanitize import write_sanitized_image_bytes
from opcrew_backend.services.tts_voice_aliases import normalize_storyboard_tts_selection
from opcrew_backend.workflow_modes import infer_openclip_workflow_mode, storyboard_meta_for_workflow

from .constants import *
from .agent_chat_common import (
    KOUBO_AGENT_CHAT_DISABLED_TOOLS,
    KOUBO_AGENT_CHAT_MESSAGE_LIMIT,
    opencode_event_has_tool_use,
    safe_opencode_message,
    sanitize_opencode_event,
)
from .usage_metering import image_usage_units, record_storyboard_usage
from .tts_public_aliases import customer_tts_public_config, tts_public_alias_state


LEGACY_AGENT_SETTINGS_REL = "SessionContext/AgentSettings.json"
IMAGE_API_SETTINGS_REL = "SessionContext/ImageAPISettings.json"
IMAGES_AGENT_SETTINGS_REL = "SessionContext/ImagesAgentSettings.json"
VIDEO_API_SETTINGS_REL = "SessionContext/VideoAPISettings.json"
VIDEOS_AGENT_SETTINGS_REL = "SessionContext/VideosAgentSettings.json"
IMAGE_API_WORKSPACE_HISTORY_REL = "SessionContext/ImageAPIWorkspaceHistory.json"
VIDEO_API_WORKSPACE_HISTORY_REL = "SessionContext/VideoAPIWorkspaceHistory.json"
TTS_AGENT_MESSAGES_REL = "SessionContext/TTSAgentMessages.json"
IMAGE_API_WORKSPACE_HISTORY_LIMIT = 500
VIDEO_API_WORKSPACE_HISTORY_LIMIT = 500
TTS_AGENT_MESSAGES_LIMIT = 200
IMAGE_API_SETTINGS_SCHEMA = "upload_asset_library_image_api_settings_0.1"
IMAGES_AGENT_SETTINGS_SCHEMA = "upload_asset_library_images_agent_settings_0.1"
VIDEO_API_SETTINGS_SCHEMA = "upload_asset_library_video_api_settings_0.1"
VIDEOS_AGENT_SETTINGS_SCHEMA = "upload_asset_library_videos_agent_settings_0.1"
IMAGE_API_WORKSPACE_HISTORY_SCHEMA = "upload_asset_library_image_api_workspace_history_0.1"
VIDEO_API_WORKSPACE_HISTORY_SCHEMA = "upload_asset_library_video_api_workspace_history_0.1"
TTS_AGENT_MESSAGES_SCHEMA = "upload_asset_library_tts_agent_messages_0.1"
AGENT_CHAT_SETTINGS_FIELDS = (
    "chat_opencode_session_id",
    "chat_session_created_at",
    "chat_last_message_at",
    "chat_last_reference_images",
    "chat_last_model_provider",
    "chat_last_model_id",
    "chat_last_model_image_generation",
    "chat_agent_image_generation_keys",
)
AGENT_SETTING_ASPECTS = {"16:9", "4:3", "1:1", "3:4", "9:16"}
ASSET_AGENT_CHAT_DISABLED_TOOLS = KOUBO_AGENT_CHAT_DISABLED_TOOLS
ASSET_AGENT_CHAT_MESSAGE_LIMIT = KOUBO_AGENT_CHAT_MESSAGE_LIMIT
ASSET_AGENT_CHAT_MODEL_ROLE = AUTH_ROLE_USER
ASSET_AGENT_CONTEXT_CHAR_LIMIT = 16000
ASSET_AGENT_PLAN_SHOT_LIMIT = 24
ASSET_AGENT_ASSET_LIMIT = 60
ASSET_AGENT_IMAGE_GENERATION_TAG = "IMAGE_GENERATION_REQUEST"
VIDEO_SETTING_ASPECTS = {"9:16", "16:9"}
PROMPT_BUILDER_REL = "SessionContext/PromptBuilder"
PROMPT_BUILDER_SCHEMA = "asset_library_prompt_builder_grok_image_0.1"
VIDEO_PROMPT_BUILDER_SCHEMA = "asset_library_prompt_builder_video_0.1"
PROMPT_BUILDER_TEMPLATE_DIR = OPENCREW_ROOT / "ToolLibrary" / "Analysis_V1" / "Reference" / "05_02"
PROMPT_BUILDER_ALLOWED_REFERENCE_PREFIXES = (
    f"{ASSET_IMAGES_REL}/",
    f"{WORKING_REL}/",
    "SessionContext/Consistency/",
)
ASSET_AGENT_REFERENCE_ROLES = {"TARGET_FRAME", "HOST_REFERENCE", "PRODUCT_REFERENCE", "REFERENCE_IMAGE"}


def register_asset_routes(router: APIRouter, deps: Any) -> None:
    asset_agent_generation_claim_lock = threading.Lock()

    def uploaded_asset_payload(rel_path: str, source: str, label: str = "", extra: dict[str, Any] | None = None) -> dict[str, Any]:
        filename = Path(rel_path).name
        asset = {
            "id": rel_path,
            "path": rel_path,
            "label": label or filename,
            "filename": filename,
            "asset_type": deps.asset_type_for_path(rel_path),
            "kind": deps.asset_type_for_path(rel_path).lower(),
            "source": source,
            "created_at": now_ms(),
        }
        if extra:
            asset.update(extra)
        return asset

    def uploaded_asset_roots() -> tuple[str, ...]:
        return (ASSET_IMAGES_REL, ASSET_AUDIOS_REL, ASSET_VIDEOS_REL, LEGACY_UPLOAD_ROOT_REL)

    def is_uploaded_asset_path(path: str) -> bool:
        return any(path.startswith(f"{root}/") for root in uploaded_asset_roots())

    def display_filename(value: str, fallback_stem: str, suffix: str) -> str:
        raw = deps.text(value).strip().replace("\x00", "")
        raw = raw.replace("/", "_").replace("\\", "_").replace(":", "_")
        raw = raw.strip(" .") or fallback_stem
        candidate = Path(raw)
        if candidate.suffix.lower() in IMAGE_EXTS | VIDEO_EXTS | AUDIO_EXTS:
            stem = candidate.stem.strip(" .") or fallback_stem
            ext = candidate.suffix.lower()
        else:
            stem = raw
            ext = suffix
        return f"{stem}{ext}"

    def unique_sibling_path(path: Path) -> Path:
        if not path.exists():
            return path
        stem = path.stem
        suffix = path.suffix
        for index in range(2, 1000):
            candidate = path.with_name(f"{stem}_{index}{suffix}")
            if not candidate.exists():
                return candidate
        raise HTTPException(status_code=409, detail="Could not choose a unique asset filename")

    def load_task_payload(task: dict[str, Any]) -> dict[str, Any]:
        plan, meta = deps.load_plan(task, sc=deps)
        return {"task": task, "meta": meta, "plan": plan}

    def load_asset_library_payload(task: dict[str, Any], workspace: Path) -> dict[str, Any]:
        if (workspace / SOURCE_REL).exists():
            return load_task_payload(task)
        payload = deps.empty_asset_library_payload(task, workspace, sc=deps)
        return {"task": payload["task"], "meta": payload["meta"], "plan": payload["plan"]}

    def default_image_api_settings() -> dict[str, Any]:
        return {
            "confirmBeforeGenerate": True,
            "aspect": "16:9",
            "count": 1,
            "agentImageAlias": "",
            "provider": "",
            "model": "",
        }

    def default_images_agent_settings() -> dict[str, Any]:
        return {
            **default_image_api_settings(),
            "chatProvider": "",
            "chatModel": "",
        }

    def default_video_api_settings() -> dict[str, Any]:
        return {
            "confirmBeforeGenerate": True,
            "aspect": "9:16",
            "duration": 4,
            "count": 1,
            "referenceMode": "selected_images",
            "agentVideoAlias": "",
            "provider": "",
            "model": "",
        }

    def default_videos_agent_settings() -> dict[str, Any]:
        return {
            **default_video_api_settings(),
            "chatProvider": "",
            "chatModel": "",
        }

    def normalize_image_api_settings(value: Any) -> dict[str, Any]:
        source = value if isinstance(value, dict) else {}
        settings = default_image_api_settings()
        settings["confirmBeforeGenerate"] = bool(source.get("confirmBeforeGenerate", settings["confirmBeforeGenerate"]))
        aspect = deps.text(source.get("aspect"), settings["aspect"])
        settings["aspect"] = aspect if aspect in AGENT_SETTING_ASPECTS else settings["aspect"]
        try:
            count = int(source.get("count") or settings["count"])
        except Exception:
            count = settings["count"]
        settings["count"] = max(1, min(count, 4))
        settings["agentImageAlias"] = deps.text(source.get("agentImageAlias") or source.get("agent_image_alias"))
        settings["provider"] = deps.text(source.get("provider"))
        settings["model"] = deps.text(source.get("model"))
        return settings

    def normalize_images_agent_settings(value: Any) -> dict[str, Any]:
        source = value if isinstance(value, dict) else {}
        settings = {**default_images_agent_settings(), **normalize_image_api_settings(source)}
        settings["chatProvider"] = deps.text(source.get("chatProvider") or source.get("chat_provider"))
        settings["chatModel"] = deps.text(source.get("chatModel") or source.get("chat_model"))
        return settings

    def normalize_video_api_settings(value: Any) -> dict[str, Any]:
        source = value if isinstance(value, dict) else {}
        settings = default_video_api_settings()
        settings["confirmBeforeGenerate"] = bool(source.get("confirmBeforeGenerate", settings["confirmBeforeGenerate"]))
        aspect = deps.text(source.get("aspect"), settings["aspect"])
        settings["aspect"] = aspect if aspect in VIDEO_SETTING_ASPECTS else settings["aspect"]
        try:
            duration = int(source.get("duration") or settings["duration"])
        except Exception:
            duration = settings["duration"]
        settings["duration"] = max(1, min(duration, 120))
        try:
            count = int(source.get("count") or settings["count"])
        except Exception:
            count = settings["count"]
        settings["count"] = max(1, min(count, 2))
        reference_mode = deps.text(source.get("referenceMode") or source.get("reference_mode"), settings["referenceMode"])
        settings["referenceMode"] = reference_mode if reference_mode in {"selected_images", "none"} else settings["referenceMode"]
        settings["agentVideoAlias"] = deps.text(source.get("agentVideoAlias") or source.get("agent_video_alias"))
        settings["provider"] = deps.text(source.get("provider"))
        settings["model"] = deps.text(source.get("model"))
        return settings

    def normalize_videos_agent_settings(value: Any) -> dict[str, Any]:
        source = value if isinstance(value, dict) else {}
        settings = {**default_videos_agent_settings(), **normalize_video_api_settings(source)}
        settings["chatProvider"] = deps.text(source.get("chatProvider") or source.get("chat_provider"))
        settings["chatModel"] = deps.text(source.get("chatModel") or source.get("chat_model"))
        return settings

    def int_value(value: Any) -> int:
        try:
            return int(value or 0)
        except Exception:
            return 0

    def normalize_reference_role(value: Any) -> str:
        role = deps.text(value).upper().replace("-", "_").replace(" ", "_")
        aliases = {
            "TARGET": "TARGET_FRAME",
            "BASE": "TARGET_FRAME",
            "BASE_FRAME": "TARGET_FRAME",
            "FRAME": "TARGET_FRAME",
            "HOST": "HOST_REFERENCE",
            "PERSON": "HOST_REFERENCE",
            "CHARACTER": "HOST_REFERENCE",
            "PRODUCT": "PRODUCT_REFERENCE",
        }
        return aliases.get(role, role) if aliases.get(role, role) in ASSET_AGENT_REFERENCE_ROLES else ""

    def infer_reference_role(path: str, label: str = "", kind: str = "", source: str = "") -> str:
        haystack = f"{path} {label} {kind} {source}".lower()
        if "host" in haystack or "person" in haystack or "character" in haystack or "人物" in haystack:
            return "HOST_REFERENCE"
        if "product" in haystack or "prodcut" in haystack or "产品" in haystack:
            return "PRODUCT_REFERENCE"
        if "target" in haystack or "frame" in haystack:
            return "TARGET_FRAME"
        return ""

    def normalize_reference_item_list(values: Any, infer_first_target: bool = False) -> list[dict[str, str]]:
        refs: list[dict[str, str]] = []
        seen: set[str] = set()
        target_seen = False
        for item in values if isinstance(values, list) else []:
            raw_item = item.strip() if isinstance(item, str) else ""
            if raw_item and len(raw_item) <= 4096 and raw_item.startswith("{") and raw_item.endswith("}"):
                try:
                    parsed = json.loads(raw_item)
                except Exception:
                    try:
                        parsed = ast.literal_eval(raw_item)
                    except Exception:
                        parsed = None
                if isinstance(parsed, dict):
                    item = parsed
            if isinstance(item, dict):
                rel_path = deps.text(item.get("path") or item.get("working_path") or item.get("source_path"))
                label = deps.text(item.get("label") or item.get("filename") or Path(rel_path).name)
                kind = deps.text(item.get("key") or item.get("kind") or item.get("source"))
                source = deps.text(item.get("source"))
                role = normalize_reference_role(item.get("role") or item.get("reference_role"))
            else:
                rel_path = deps.text(item)
                label = Path(rel_path).name
                kind = ""
                source = ""
                role = ""
            if not rel_path or rel_path in seen:
                continue
            if infer_first_target and not role and not target_seen and source != "session_consistency_reference":
                role = "TARGET_FRAME"
            if not role:
                role = infer_reference_role(rel_path, label, kind, source)
            if role == "TARGET_FRAME":
                target_seen = True
            seen.add(rel_path)
            refs.append({
                "path": rel_path,
                "role": role or "REFERENCE_IMAGE",
                "label": label or Path(rel_path).name,
            })
        return refs[:8]

    def normalize_reference_path_list(values: Any) -> list[str]:
        refs: list[str] = []
        for item in normalize_reference_item_list(values):
            rel_path = deps.text(item.get("path"))
            if rel_path and rel_path not in refs:
                refs.append(rel_path)
        return refs[:8]

    def normalize_generation_key_list(values: Any) -> list[str]:
        keys: list[str] = []
        for item in values if isinstance(values, list) else []:
            key = deps.text(item)
            if key and key not in keys:
                keys.append(key)
        return keys[-200:]

    def asset_agent_model_supports_image_generation(model: dict[str, Any]) -> bool:
        provider = deps.text(model.get("providerID") or model.get("provider")).lower()
        model_id = deps.text(model.get("modelID") or model.get("model")).lower()
        return provider == "openai" and (model_id == "gpt-5.5" or model_id.startswith("gpt-5.5-"))

    def image_size_for_aspect(aspect: str) -> str:
        aspect = deps.text(aspect)
        if aspect == "9:16":
            return "1024x1536"
        if aspect == "16:9":
            return "1536x1024"
        if aspect == "4:3":
            return "1024x768"
        if aspect == "3:4":
            return "768x1024"
        return "1024x1024"

    def requested_image_count(value: Any) -> int:
        try:
            count = int(value or 1)
        except Exception:
            count = 1
        return max(1, min(count, 4))

    def image_aspect_for_request(prompt: str, size: str, explicit_aspect: str = "") -> str:
        aspect = deps.text(explicit_aspect)
        if aspect in AGENT_SETTING_ASPECTS:
            return aspect
        prompt_text = deps.text(prompt).lower()
        if re.search(r"(^|[^0-9])9\s*[:：x]\s*16([^0-9]|$)|竖屏|竖图|portrait", prompt_text):
            return "9:16"
        if re.search(r"(^|[^0-9])16\s*[:：x]\s*9([^0-9]|$)|横屏|横图|landscape", prompt_text):
            return "16:9"
        if re.search(r"(^|[^0-9])1\s*[:：x]\s*1([^0-9]|$)|方图|square", prompt_text):
            return "1:1"
        match = re.fullmatch(r"\s*(\d{2,5})\s*x\s*(\d{2,5})\s*", deps.text(size))
        if not match:
            return ""
        width = int(match.group(1))
        height = int(match.group(2))
        if width <= 0 or height <= 0:
            return ""
        ratio = width / height
        if abs(ratio - (9 / 16)) < 0.04:
            return "9:16"
        if abs(ratio - (16 / 9)) < 0.04:
            return "16:9"
        if abs(ratio - 1) < 0.04:
            return "1:1"
        if ratio < 1:
            return "3:4"
        return "4:3"

    def image_api_settings_payload(task: dict[str, Any], settings: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
        created_at = previous.get("created_at") if isinstance(previous, dict) else None
        timestamp = now_ms()
        return {
            "schema_version": IMAGE_API_SETTINGS_SCHEMA,
            "task_id": int(task["id"]),
            "session_id": int(task["session_id"]),
            "settings": normalize_image_api_settings(settings),
            "created_at": created_at or timestamp,
            "updated_at": timestamp,
        }

    def agent_settings_payload(task: dict[str, Any], settings: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
        created_at = previous.get("created_at") if isinstance(previous, dict) else None
        timestamp = now_ms()
        payload = {
            "schema_version": IMAGES_AGENT_SETTINGS_SCHEMA,
            "task_id": int(task["id"]),
            "session_id": int(task["session_id"]),
            "settings": normalize_images_agent_settings(settings),
            "created_at": created_at or timestamp,
            "updated_at": timestamp,
        }
        for field in AGENT_CHAT_SETTINGS_FIELDS:
            payload[field] = previous.get(field) if isinstance(previous, dict) else ""
        payload["chat_opencode_session_id"] = deps.text(payload.get("chat_opencode_session_id"))
        payload["chat_session_created_at"] = int_value(payload.get("chat_session_created_at"))
        payload["chat_last_message_at"] = int_value(payload.get("chat_last_message_at"))
        payload["chat_last_reference_images"] = normalize_reference_item_list(payload.get("chat_last_reference_images"))
        payload["chat_last_model_provider"] = deps.text(payload.get("chat_last_model_provider"))
        payload["chat_last_model_id"] = deps.text(payload.get("chat_last_model_id"))
        payload["chat_last_model_image_generation"] = bool(payload.get("chat_last_model_image_generation"))
        payload["chat_agent_image_generation_keys"] = normalize_generation_key_list(payload.get("chat_agent_image_generation_keys"))
        return payload

    def video_api_settings_payload(task: dict[str, Any], settings: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
        created_at = previous.get("created_at") if isinstance(previous, dict) else None
        timestamp = now_ms()
        return {
            "schema_version": VIDEO_API_SETTINGS_SCHEMA,
            "task_id": int(task["id"]),
            "session_id": int(task["session_id"]),
            "settings": normalize_video_api_settings(settings),
            "created_at": created_at or timestamp,
            "updated_at": timestamp,
        }

    def videos_agent_settings_payload(task: dict[str, Any], settings: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
        created_at = previous.get("created_at") if isinstance(previous, dict) else None
        timestamp = now_ms()
        return {
            "schema_version": VIDEOS_AGENT_SETTINGS_SCHEMA,
            "task_id": int(task["id"]),
            "session_id": int(task["session_id"]),
            "settings": normalize_videos_agent_settings(settings),
            "created_at": created_at or timestamp,
            "updated_at": timestamp,
        }

    def normalize_direct_image_history_message(value: Any) -> dict[str, Any] | None:
        source = value if isinstance(value, dict) else {}
        role = deps.text(source.get("role"))
        if role not in {"user", "assistant"}:
            return None
        message_id = deps.text(source.get("id"))
        if not message_id:
            message_id = f"image-api-history-{now_ms()}"
        try:
            created_at = int(source.get("created_at") or source.get("createdAt") or now_ms())
        except Exception:
            created_at = now_ms()
        message: dict[str, Any] = {
            "id": message_id[:160],
            "role": role,
            "text": deps.text(source.get("text"))[:20000],
            "created_at": created_at,
        }
        path = deps.text(source.get("path"))
        if path:
            message["path"] = path
            message["filename"] = deps.text(source.get("filename"), Path(path).name)[:240]
        else:
            filename = deps.text(source.get("filename"))
            if filename:
                message["filename"] = filename[:240]
        aspect = deps.text(source.get("aspect") or source.get("aspect_ratio") or source.get("aspectRatio"))
        if aspect in AGENT_SETTING_ASPECTS:
            message["aspect"] = aspect
        if bool(source.get("imagePlaceholder")):
            message["imagePlaceholder"] = True
            message["progressLabel"] = deps.text(source.get("progressLabel"), "0%")[:64]
        if bool(source.get("failed")):
            message["failed"] = True
            message["progressLabel"] = deps.text(source.get("progressLabel"), "Failed")[:64]
        if not message.get("text") and not message.get("path") and not message.get("imagePlaceholder") and not message.get("failed"):
            return None
        return message

    def direct_image_history_payload(task: dict[str, Any], value: Any, previous: dict[str, Any] | None = None) -> dict[str, Any]:
        source = value if isinstance(value, dict) else {}
        source_messages = source.get("messages") if isinstance(source.get("messages"), list) else []
        messages = [
            item
            for item in (normalize_direct_image_history_message(item) for item in source_messages)
            if item
        ][-IMAGE_API_WORKSPACE_HISTORY_LIMIT:]
        created_at = previous.get("created_at") if isinstance(previous, dict) else None
        timestamp = now_ms()
        return {
            "schema_version": IMAGE_API_WORKSPACE_HISTORY_SCHEMA,
            "task_id": int(task["id"]),
            "session_id": int(task["session_id"]),
            "messages": messages,
            "created_at": created_at or timestamp,
            "updated_at": timestamp,
        }

    def read_or_create_image_api_workspace_history(task: dict[str, Any]) -> dict[str, Any]:
        workspace = deps.workspace_for(task)
        history_path = workspace / IMAGE_API_WORKSPACE_HISTORY_REL
        existing = deps.read_json(history_path)
        payload = direct_image_history_payload(task, existing, existing if existing else None)
        should_write = (
            not history_path.exists()
            or existing.get("schema_version") != IMAGE_API_WORKSPACE_HISTORY_SCHEMA
            or int_value(existing.get("task_id")) != int(task["id"])
            or int_value(existing.get("session_id")) != int(task["session_id"])
        )
        if should_write:
            deps.write_json(history_path, payload)
            deps.add_event(int(task["session_id"]), "koubo_storyboard.asset_library.image_api_workspace_history.created", {"task_id": int(task["id"]), "path": IMAGE_API_WORKSPACE_HISTORY_REL})
            return {"ok": True, "path": IMAGE_API_WORKSPACE_HISTORY_REL, **payload}
        return {"ok": True, "path": IMAGE_API_WORKSPACE_HISTORY_REL, **existing, "messages": payload["messages"]}

    def save_image_api_workspace_history_payload(task: dict[str, Any], value: Any) -> dict[str, Any]:
        workspace = deps.workspace_for(task)
        history_path = workspace / IMAGE_API_WORKSPACE_HISTORY_REL
        existing = deps.read_json(history_path)
        payload = direct_image_history_payload(task, value, existing if existing else None)
        deps.write_json(history_path, payload)
        deps.add_event(int(task["session_id"]), "koubo_storyboard.asset_library.image_api_workspace_history.saved", {"task_id": int(task["id"]), "path": IMAGE_API_WORKSPACE_HISTORY_REL, "message_count": len(payload["messages"])})
        return {"ok": True, "path": IMAGE_API_WORKSPACE_HISTORY_REL, **payload}

    def normalize_direct_video_history_message(value: Any) -> dict[str, Any] | None:
        source = value if isinstance(value, dict) else {}
        role = deps.text(source.get("role"))
        if role not in {"user", "assistant"}:
            return None
        message_id = deps.text(source.get("id"))
        if not message_id:
            message_id = f"video-api-history-{now_ms()}"
        try:
            created_at = int(source.get("created_at") or source.get("createdAt") or now_ms())
        except Exception:
            created_at = now_ms()
        message: dict[str, Any] = {
            "id": message_id[:160],
            "role": role,
            "text": deps.text(source.get("text"))[:20000],
            "created_at": created_at,
        }
        references = normalize_reference_item_list(source.get("referenceAttachments") or source.get("reference_attachments"))
        if references:
            message["referenceAttachments"] = references
        path = deps.text(source.get("path"))
        if path:
            message["path"] = path
            message["filename"] = deps.text(source.get("filename"), Path(path).name)[:240]
        else:
            filename = deps.text(source.get("filename"))
            if filename:
                message["filename"] = filename[:240]
        aspect = deps.text(source.get("aspect") or source.get("aspect_ratio") or source.get("aspectRatio"))
        if aspect in VIDEO_SETTING_ASPECTS:
            message["aspect"] = aspect
        if bool(source.get("videoPlaceholder")):
            message["videoPlaceholder"] = True
            message["progressLabel"] = deps.text(source.get("progressLabel"), "0%")[:64]
        if bool(source.get("failed")):
            message["failed"] = True
            message["progressLabel"] = deps.text(source.get("progressLabel"), "Failed")[:64]
        if not message.get("text") and not message.get("path") and not message.get("videoPlaceholder") and not message.get("failed") and not references:
            return None
        return message

    def direct_video_history_payload(task: dict[str, Any], value: Any, previous: dict[str, Any] | None = None) -> dict[str, Any]:
        source = value if isinstance(value, dict) else {}
        source_messages = source.get("messages") if isinstance(source.get("messages"), list) else []
        messages = [
            item
            for item in (normalize_direct_video_history_message(item) for item in source_messages)
            if item
        ][-VIDEO_API_WORKSPACE_HISTORY_LIMIT:]
        created_at = previous.get("created_at") if isinstance(previous, dict) else None
        timestamp = now_ms()
        return {
            "schema_version": VIDEO_API_WORKSPACE_HISTORY_SCHEMA,
            "task_id": int(task["id"]),
            "session_id": int(task["session_id"]),
            "messages": messages,
            "created_at": created_at or timestamp,
            "updated_at": timestamp,
        }

    def read_or_create_video_api_workspace_history(task: dict[str, Any]) -> dict[str, Any]:
        workspace = deps.workspace_for(task)
        history_path = workspace / VIDEO_API_WORKSPACE_HISTORY_REL
        existing = deps.read_json(history_path)
        payload = direct_video_history_payload(task, existing, existing if existing else None)
        should_write = (
            not history_path.exists()
            or existing.get("schema_version") != VIDEO_API_WORKSPACE_HISTORY_SCHEMA
            or int_value(existing.get("task_id")) != int(task["id"])
            or int_value(existing.get("session_id")) != int(task["session_id"])
        )
        if should_write:
            deps.write_json(history_path, payload)
            deps.add_event(int(task["session_id"]), "koubo_storyboard.asset_library.video_api_workspace_history.created", {"task_id": int(task["id"]), "path": VIDEO_API_WORKSPACE_HISTORY_REL})
            return {"ok": True, "path": VIDEO_API_WORKSPACE_HISTORY_REL, **payload}
        return {"ok": True, "path": VIDEO_API_WORKSPACE_HISTORY_REL, **existing, "messages": payload["messages"]}

    def save_video_api_workspace_history_payload(task: dict[str, Any], value: Any) -> dict[str, Any]:
        workspace = deps.workspace_for(task)
        history_path = workspace / VIDEO_API_WORKSPACE_HISTORY_REL
        existing = deps.read_json(history_path)
        payload = direct_video_history_payload(task, value, existing if existing else None)
        deps.write_json(history_path, payload)
        deps.add_event(int(task["session_id"]), "koubo_storyboard.asset_library.video_api_workspace_history.saved", {"task_id": int(task["id"]), "path": VIDEO_API_WORKSPACE_HISTORY_REL, "message_count": len(payload["messages"])})
        return {"ok": True, "path": VIDEO_API_WORKSPACE_HISTORY_REL, **payload}

    def normalize_tts_agent_message(value: Any) -> dict[str, Any] | None:
        source = value if isinstance(value, dict) else {}
        role = deps.text(source.get("role"))
        if role not in {"user", "assistant"}:
            return None
        message_text = deps.text(source.get("text"))[:20000]
        if not message_text:
            return None
        message_id = deps.text(source.get("id"))
        if not message_id:
            message_id = f"tts-agent-{uuid.uuid4().hex[:12]}"
        try:
            created_at = int(source.get("created_at") or source.get("createdAt") or now_ms())
        except Exception:
            created_at = now_ms()
        return {
            "id": message_id[:160],
            "role": role,
            "text": message_text,
            "created_at": created_at,
        }

    def tts_agent_messages_payload(task: dict[str, Any], value: Any, previous: dict[str, Any] | None = None) -> dict[str, Any]:
        source = value if isinstance(value, dict) else {}
        source_messages = source.get("messages") if isinstance(source.get("messages"), list) else []
        messages = [
            item
            for item in (normalize_tts_agent_message(item) for item in source_messages)
            if item
        ][-TTS_AGENT_MESSAGES_LIMIT:]
        created_at = previous.get("created_at") if isinstance(previous, dict) else None
        timestamp = now_ms()
        return {
            "schema_version": TTS_AGENT_MESSAGES_SCHEMA,
            "task_id": int(task["id"]),
            "session_id": int(task["session_id"]),
            "messages": messages,
            "created_at": created_at or timestamp,
            "updated_at": timestamp,
        }

    def read_or_create_tts_agent_messages(task: dict[str, Any]) -> dict[str, Any]:
        workspace = deps.workspace_for(task)
        messages_path = workspace / TTS_AGENT_MESSAGES_REL
        existing = deps.read_json(messages_path)
        payload = tts_agent_messages_payload(task, existing, existing if existing else None)
        should_write = (
            not messages_path.exists()
            or existing.get("schema_version") != TTS_AGENT_MESSAGES_SCHEMA
            or int_value(existing.get("task_id")) != int(task["id"])
            or int_value(existing.get("session_id")) != int(task["session_id"])
        )
        if should_write:
            deps.write_json(messages_path, payload)
            deps.add_event(int(task["session_id"]), "koubo_storyboard.asset_library.tts_agent.messages.created", {"task_id": int(task["id"]), "path": TTS_AGENT_MESSAGES_REL})
            return {"ok": True, "path": TTS_AGENT_MESSAGES_REL, **payload}
        return {"ok": True, "path": TTS_AGENT_MESSAGES_REL, **existing, "messages": payload["messages"]}

    def save_tts_agent_messages_payload(task: dict[str, Any], value: Any) -> dict[str, Any]:
        workspace = deps.workspace_for(task)
        messages_path = workspace / TTS_AGENT_MESSAGES_REL
        existing = deps.read_json(messages_path)
        payload = tts_agent_messages_payload(task, value, existing if existing else None)
        deps.write_json(messages_path, payload)
        deps.add_event(int(task["session_id"]), "koubo_storyboard.asset_library.tts_agent.messages.saved", {"task_id": int(task["id"]), "path": TTS_AGENT_MESSAGES_REL, "message_count": len(payload["messages"])})
        return {"ok": True, "path": TTS_AGENT_MESSAGES_REL, **payload}

    def save_tts_agent_session_artifact(task: dict[str, Any], session_id: str, value: Any) -> dict[str, Any]:
        workspace = deps.workspace_for(task)
        source = value.get("session") if isinstance(value, dict) and isinstance(value.get("session"), dict) else (value if isinstance(value, dict) else {})
        clean_id = deps.safe_name(deps.text(session_id) or deps.text(source.get("id")) or f"tts_agent_{now_ms()}", f"tts_agent_{now_ms()}")
        target_dir = workspace / ASSET_AUDIOS_REL
        target_dir.mkdir(parents=True, exist_ok=True)
        json_path = target_dir / f"{clean_id}.json"
        existing = deps.read_json(json_path)
        audio = source.get("audio") if isinstance(source.get("audio"), dict) else {}
        audio_path = deps.text(audio.get("path"))
        if not audio_path or not audio_path.startswith(f"{ASSET_AUDIOS_REL}/") or Path(audio_path).suffix.lower() not in AUDIO_EXTS:
            audio_path = f"{ASSET_AUDIOS_REL}/{clean_id}.wav"
        audio_exists = (workspace / audio_path).exists()
        timestamp = now_ms()
        payload = {
            "schema_version": "upload_asset_library_tts_agent_session_0.1",
            "id": clean_id,
            "title": deps.text(source.get("title")) or f"Agent Session {clean_id[-6:]}",
            "task_id": int(task["id"]),
            "session_id": int(task["session_id"]),
            "request_text": deps.text(source.get("request_text")),
            "roles": source.get("roles") if isinstance(source.get("roles"), list) else [],
            "dialogues": source.get("dialogues") if isinstance(source.get("dialogues"), list) else [],
            "messages": [
                item
                for item in (normalize_tts_agent_message(item) for item in (source.get("messages") if isinstance(source.get("messages"), list) else []))
                if item
            ][-TTS_AGENT_MESSAGES_LIMIT:],
            "audio": {**audio, "path": audio_path, "filename": Path(audio_path).name},
            "audio_state": "ready" if audio_exists else deps.text(source.get("audio_state")) or "empty",
            "progress_text": deps.text(source.get("progress_text")),
            "json_path": json_path.relative_to(workspace).as_posix(),
            "created_at": existing.get("created_at") if isinstance(existing, dict) and existing.get("created_at") else timestamp,
            "updated_at": timestamp,
        }
        deps.write_json(json_path, payload)
        asset = uploaded_asset_payload(audio_path, "agent", payload["title"], {
            "audio_exists": audio_exists,
            "missing_audio": not audio_exists,
            "agent_session_path": payload["json_path"],
            "tts_agent_session": payload,
            "origin": {
                "tool": "tts_agent_session",
                "json_path": payload["json_path"],
            },
        })
        deps.upsert_asset_manifest_item(workspace, asset, sc=deps)
        loaded, meta = deps.load_plan(task, sc=deps)
        deps.add_event(int(task["session_id"]), "koubo_storyboard.asset_library.tts_agent.session.saved", {"task_id": int(task["id"]), "path": payload["json_path"], "audio_path": audio_path, "audio_exists": audio_exists})
        return {"ok": True, "task": task, "meta": meta, "plan": loaded, "asset": asset, "session": payload}

    def settings_source_for(existing: dict[str, Any]) -> dict[str, Any]:
        return existing.get("settings") if isinstance(existing.get("settings"), dict) else existing

    def read_legacy_agent_settings(task: dict[str, Any]) -> dict[str, Any]:
        return deps.read_json(deps.workspace_for(task) / LEGACY_AGENT_SETTINGS_REL)

    def read_or_create_image_api_settings(task: dict[str, Any]) -> dict[str, Any]:
        workspace = deps.workspace_for(task)
        settings_path = workspace / IMAGE_API_SETTINGS_REL
        existing = deps.read_json(settings_path)
        legacy = read_legacy_agent_settings(task) if not existing else {}
        previous = existing if existing else legacy
        source = settings_source_for(existing) if existing else settings_source_for(legacy)
        payload = image_api_settings_payload(task, normalize_image_api_settings(source), previous if previous else None)
        should_write = (
            not settings_path.exists()
            or existing.get("schema_version") != IMAGE_API_SETTINGS_SCHEMA
            or existing.get("settings") != payload.get("settings")
            or int_value(existing.get("task_id")) != int(task["id"])
            or int_value(existing.get("session_id")) != int(task["session_id"])
        )
        if should_write:
            deps.write_json(settings_path, payload)
            deps.add_event(int(task["session_id"]), "koubo_storyboard.asset_library.image_api_settings.created", {"task_id": int(task["id"]), "path": IMAGE_API_SETTINGS_REL})
            return {"ok": True, "path": IMAGE_API_SETTINGS_REL, **payload}
        return {"ok": True, "path": IMAGE_API_SETTINGS_REL, **existing, "settings": payload["settings"]}

    def save_image_api_settings_payload(task: dict[str, Any], value: Any) -> dict[str, Any]:
        workspace = deps.workspace_for(task)
        settings_path = workspace / IMAGE_API_SETTINGS_REL
        existing = deps.read_json(settings_path)
        source = value.get("settings") if isinstance(value, dict) and isinstance(value.get("settings"), dict) else value
        payload = image_api_settings_payload(task, normalize_image_api_settings(source), existing if existing else None)
        deps.write_json(settings_path, payload)
        deps.add_event(int(task["session_id"]), "koubo_storyboard.asset_library.image_api_settings.saved", {"task_id": int(task["id"]), "path": IMAGE_API_SETTINGS_REL, "settings": payload["settings"]})
        return {"ok": True, "path": IMAGE_API_SETTINGS_REL, **payload}

    def read_or_create_agent_settings(task: dict[str, Any]) -> dict[str, Any]:
        workspace = deps.workspace_for(task)
        settings_path = workspace / IMAGES_AGENT_SETTINGS_REL
        existing = deps.read_json(settings_path)
        legacy = read_legacy_agent_settings(task) if not existing else {}
        previous = existing if existing else legacy
        source = settings_source_for(existing) if existing else settings_source_for(legacy)
        payload = agent_settings_payload(task, normalize_images_agent_settings(source), previous if previous else None)
        should_write = (
            not settings_path.exists()
            or existing.get("schema_version") != IMAGES_AGENT_SETTINGS_SCHEMA
            or existing.get("settings") != payload.get("settings")
            or any(existing.get(field) != payload.get(field) for field in AGENT_CHAT_SETTINGS_FIELDS)
            or int_value(existing.get("task_id")) != int(task["id"])
            or int_value(existing.get("session_id")) != int(task["session_id"])
        )
        if should_write:
            deps.write_json(settings_path, payload)
            deps.add_event(int(task["session_id"]), "koubo_storyboard.asset_library_agent.settings.created", {"task_id": int(task["id"]), "path": IMAGES_AGENT_SETTINGS_REL})
            return {"ok": True, "path": IMAGES_AGENT_SETTINGS_REL, **payload}
        return {"ok": True, "path": IMAGES_AGENT_SETTINGS_REL, **existing, "settings": payload["settings"]}

    def save_agent_settings_payload(task: dict[str, Any], value: Any) -> dict[str, Any]:
        workspace = deps.workspace_for(task)
        settings_path = workspace / IMAGES_AGENT_SETTINGS_REL
        existing = deps.read_json(settings_path)
        source = value.get("settings") if isinstance(value, dict) and isinstance(value.get("settings"), dict) else value
        payload = agent_settings_payload(task, normalize_images_agent_settings(source), existing if existing else None)
        deps.write_json(settings_path, payload)
        deps.add_event(int(task["session_id"]), "koubo_storyboard.asset_library_agent.settings.saved", {"task_id": int(task["id"]), "path": IMAGES_AGENT_SETTINGS_REL, "settings": payload["settings"]})
        return {"ok": True, "path": IMAGES_AGENT_SETTINGS_REL, **payload}

    def read_or_create_video_api_settings(task: dict[str, Any]) -> dict[str, Any]:
        workspace = deps.workspace_for(task)
        settings_path = workspace / VIDEO_API_SETTINGS_REL
        existing = deps.read_json(settings_path)
        source = settings_source_for(existing)
        payload = video_api_settings_payload(task, normalize_video_api_settings(source), existing if existing else None)
        should_write = (
            not settings_path.exists()
            or existing.get("schema_version") != VIDEO_API_SETTINGS_SCHEMA
            or existing.get("settings") != payload.get("settings")
            or int_value(existing.get("task_id")) != int(task["id"])
            or int_value(existing.get("session_id")) != int(task["session_id"])
        )
        if should_write:
            deps.write_json(settings_path, payload)
            deps.add_event(int(task["session_id"]), "koubo_storyboard.asset_library.video_api_settings.created", {"task_id": int(task["id"]), "path": VIDEO_API_SETTINGS_REL})
            return {"ok": True, "path": VIDEO_API_SETTINGS_REL, **payload}
        return {"ok": True, "path": VIDEO_API_SETTINGS_REL, **existing, "settings": payload["settings"]}

    def save_video_api_settings_payload(task: dict[str, Any], value: Any) -> dict[str, Any]:
        workspace = deps.workspace_for(task)
        settings_path = workspace / VIDEO_API_SETTINGS_REL
        existing = deps.read_json(settings_path)
        source = value.get("settings") if isinstance(value, dict) and isinstance(value.get("settings"), dict) else value
        payload = video_api_settings_payload(task, normalize_video_api_settings(source), existing if existing else None)
        deps.write_json(settings_path, payload)
        template_snapshot = copy_video_prompt_template_snapshot_for_settings(task, payload["settings"])
        deps.add_event(int(task["session_id"]), "koubo_storyboard.asset_library.video_api_settings.saved", {"task_id": int(task["id"]), "path": VIDEO_API_SETTINGS_REL, "settings": payload["settings"], "prompt_template_snapshot": template_snapshot})
        if template_snapshot:
            payload["prompt_template_snapshot"] = template_snapshot
        return {"ok": True, "path": VIDEO_API_SETTINGS_REL, **payload}

    def read_or_create_videos_agent_settings(task: dict[str, Any]) -> dict[str, Any]:
        workspace = deps.workspace_for(task)
        settings_path = workspace / VIDEOS_AGENT_SETTINGS_REL
        existing = deps.read_json(settings_path)
        source = settings_source_for(existing)
        payload = videos_agent_settings_payload(task, normalize_videos_agent_settings(source), existing if existing else None)
        should_write = (
            not settings_path.exists()
            or existing.get("schema_version") != VIDEOS_AGENT_SETTINGS_SCHEMA
            or existing.get("settings") != payload.get("settings")
            or int_value(existing.get("task_id")) != int(task["id"])
            or int_value(existing.get("session_id")) != int(task["session_id"])
        )
        if should_write:
            deps.write_json(settings_path, payload)
            deps.add_event(int(task["session_id"]), "koubo_storyboard.asset_library.videos_agent_settings.created", {"task_id": int(task["id"]), "path": VIDEOS_AGENT_SETTINGS_REL})
            return {"ok": True, "path": VIDEOS_AGENT_SETTINGS_REL, **payload}
        return {"ok": True, "path": VIDEOS_AGENT_SETTINGS_REL, **existing, "settings": payload["settings"]}

    def save_videos_agent_settings_payload(task: dict[str, Any], value: Any) -> dict[str, Any]:
        workspace = deps.workspace_for(task)
        settings_path = workspace / VIDEOS_AGENT_SETTINGS_REL
        existing = deps.read_json(settings_path)
        source = value.get("settings") if isinstance(value, dict) and isinstance(value.get("settings"), dict) else value
        payload = videos_agent_settings_payload(task, normalize_videos_agent_settings(source), existing if existing else None)
        deps.write_json(settings_path, payload)
        template_snapshot = copy_video_prompt_template_snapshot_for_settings(task, payload["settings"])
        deps.add_event(int(task["session_id"]), "koubo_storyboard.asset_library.videos_agent_settings.saved", {"task_id": int(task["id"]), "path": VIDEOS_AGENT_SETTINGS_REL, "settings": payload["settings"], "prompt_template_snapshot": template_snapshot})
        if template_snapshot:
            payload["prompt_template_snapshot"] = template_snapshot
        return {"ok": True, "path": VIDEOS_AGENT_SETTINGS_REL, **payload}

    def save_agent_chat_fields(task: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any]:
        workspace = deps.workspace_for(task)
        settings_path = workspace / IMAGES_AGENT_SETTINGS_REL
        existing = deps.read_json(settings_path)
        payload = agent_settings_payload(task, normalize_images_agent_settings(settings_source_for(existing)), existing if existing else None)
        timestamp = now_ms()
        for field in AGENT_CHAT_SETTINGS_FIELDS:
            if field in fields:
                payload[field] = fields[field]
        payload["chat_opencode_session_id"] = deps.text(payload.get("chat_opencode_session_id"))
        payload["chat_session_created_at"] = int_value(payload.get("chat_session_created_at"))
        payload["chat_last_message_at"] = int_value(payload.get("chat_last_message_at"))
        payload["chat_last_reference_images"] = normalize_reference_item_list(payload.get("chat_last_reference_images"))
        payload["chat_last_model_provider"] = deps.text(payload.get("chat_last_model_provider"))
        payload["chat_last_model_id"] = deps.text(payload.get("chat_last_model_id"))
        payload["chat_last_model_image_generation"] = bool(payload.get("chat_last_model_image_generation"))
        payload["chat_agent_image_generation_keys"] = normalize_generation_key_list(payload.get("chat_agent_image_generation_keys"))
        payload["updated_at"] = timestamp
        deps.write_json(settings_path, payload)
        return {"ok": True, "path": IMAGES_AGENT_SETTINGS_REL, **payload}

    def opencode_session_not_found(exc: Exception) -> bool:
        return isinstance(exc, HTTPError) and exc.code == 404

    def clear_stale_asset_agent_chat_session(task: dict[str, Any], chat_session_id: str, source: str) -> dict[str, Any]:
        saved = save_agent_chat_fields(task, {
            "chat_opencode_session_id": "",
            "chat_session_created_at": 0,
            "chat_last_message_at": 0,
        })
        deps.add_event(int(task["session_id"]), "koubo_storyboard.asset_library_agent.chat.session.stale_cleared", {
            "task_id": int(task["id"]),
            "chat_opencode_session_id": chat_session_id,
            "source": source,
            "path": saved.get("path"),
        })
        return saved

    def claim_agent_image_generation_key(task: dict[str, Any], generation_key: str) -> bool:
        with asset_agent_generation_claim_lock:
            settings_payload = read_or_create_agent_settings(task)
            keys = normalize_generation_key_list(settings_payload.get("chat_agent_image_generation_keys"))
            if generation_key in keys:
                return False
            keys.append(generation_key)
            save_agent_chat_fields(task, {"chat_agent_image_generation_keys": keys})
            return True

    def asset_agent_session_row(task: dict[str, Any]) -> dict[str, Any]:
        session = deps.safe_session(int(task["session_id"]))
        return {**session, "workspace_dir": str(deps.workspace_for(task))}

    def asset_agent_prompt_models(task: dict[str, Any], _role: str) -> dict[str, Any]:
        session_row = asset_agent_session_row(task)
        payload = mask_prompt_models_for_role(deps.ctx, ASSET_AGENT_CHAT_MODEL_ROLE, SURFACE_KOUBO_ASSET_AGENT_CHAT, deps.safe_prompt_models(session_row, sc=deps))
        items = payload.get("items") if isinstance(payload.get("items"), list) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                real_model, _ = deps.resolve_model(
                    session_row,
                    deps.text(item.get("providerID")),
                    deps.text(item.get("modelID")),
                    ASSET_AGENT_CHAT_MODEL_ROLE,
                    SURFACE_KOUBO_ASSET_AGENT_CHAT,
                sc=deps)
                item["asset_agent_image_generation"] = asset_agent_model_supports_image_generation(real_model)
            except Exception:
                item["asset_agent_image_generation"] = False
        return payload

    def ensure_asset_agent_chat_session(task: dict[str, Any], role: str) -> dict[str, Any]:
        settings_payload = read_or_create_agent_settings(task)
        chat_session_id = deps.text(settings_payload.get("chat_opencode_session_id"))
        if chat_session_id:
            try:
                deps.opencode_client_for(asset_agent_session_row(task), sc=deps).messages(chat_session_id, limit=1)
                return {
                    **settings_payload,
                    "chat_opencode_session_id": chat_session_id,
                    "prompt_models": asset_agent_prompt_models(task, role),
                }
            except Exception as exc:
                if not opencode_session_not_found(exc):
                    raise
                clear_stale_asset_agent_chat_session(task, chat_session_id, "ensure_session")
        session_row = asset_agent_session_row(task)
        created = deps.opencode_client_for(session_row, sc=deps).create_session(f"Koubo Asset Library Agent Chat - Task {int(task['id'])}")
        chat_session_id = deps.text(created.get("id"))
        if not chat_session_id:
            raise HTTPException(status_code=500, detail="OpenCode did not return a chat session id")
        saved = save_agent_chat_fields(task, {
            "chat_opencode_session_id": chat_session_id,
            "chat_session_created_at": now_ms(),
            "chat_last_message_at": 0,
        })
        deps.add_event(int(task["session_id"]), "koubo_storyboard.asset_library_agent.chat.session.created", {
            "task_id": int(task["id"]),
            "chat_opencode_session_id": chat_session_id,
        })
        return {**saved, "prompt_models": asset_agent_prompt_models(task, role)}

    def compact_json(value: Any, limit: int = ASSET_AGENT_CONTEXT_CHAR_LIMIT) -> str:
        try:
            raw = json.dumps(deps.redact_payload(value), ensure_ascii=False, default=str, indent=2)
        except Exception:
            raw = str(value)
        if len(raw) <= limit:
            return raw
        return f"{raw[:limit]}\n... truncated ..."

    def asset_agent_plan_summary(task: dict[str, Any]) -> dict[str, Any]:
        try:
            plan, meta = deps.load_plan(task, sc=deps)
        except Exception as exc:
            return {"error": str(exc)}
        shots = plan.get("shots") if isinstance(plan.get("shots"), list) else []
        compact_shots = []
        for index, shot in enumerate(shots[:ASSET_AGENT_PLAN_SHOT_LIMIT], start=1):
            compact_shots.append({
                "index": index,
                "summary": compact_json(shot, 1200),
            })
        return {
            "meta": meta,
            "title": plan.get("title"),
            "shot_count": len(shots),
            "shots": compact_shots,
        }

    def asset_agent_asset_summary(workspace: Path) -> dict[str, Any]:
        store = deps.read_json(workspace / ASSETS_REL)
        assets = store.get("assets") if isinstance(store.get("assets"), list) else []
        items: list[dict[str, Any]] = []
        for asset in assets[:ASSET_AGENT_ASSET_LIMIT]:
            if not isinstance(asset, dict):
                continue
            items.append({
                "path": deps.text(asset.get("path") or asset.get("id")),
                "label": deps.text(asset.get("label") or asset.get("filename")),
                "kind": deps.text(asset.get("kind") or asset.get("asset_type")),
                "source": deps.text(asset.get("source")),
            })
        return {"count": len(assets), "items": items}

    def asset_agent_consistency_summary(workspace: Path) -> dict[str, Any]:
        sections: dict[str, Any] = {}
        for kind in ("host", "product"):
            try:
                section = deps.read_builder_section(workspace, kind, sc=deps)
            except Exception as exc:
                sections[kind] = {"error": str(exc)}
                continue
            manifest = section.get("manifest") if isinstance(section.get("manifest"), dict) else {}
            sections[kind] = {
                "output": deps.text(section.get("output")),
                "manifest": manifest,
            }
        try:
            guide = deps.read_consistency_guide()
        except Exception:
            guide = ""
        return {"sections": sections, "guide": deps.text(guide)[:6000]}

    def asset_agent_context(task: dict[str, Any]) -> dict[str, Any]:
        workspace = deps.workspace_for(task)
        return {
            "task": {
                "id": int(task["id"]),
                "session_id": int(task["session_id"]),
                "title": deps.text(task.get("title") or task.get("name")),
                "status": deps.text(task.get("status")),
            },
            "storyboard": asset_agent_plan_summary(task),
            "assets": asset_agent_asset_summary(workspace),
            "consistency": asset_agent_consistency_summary(workspace),
        }

    def asset_agent_system_prompt(task: dict[str, Any], model: dict[str, Any] | None = None) -> str:
        can_generate = asset_agent_model_supports_image_generation(model or {})
        generation_policy = f"""当前选中的 Agent 模型具备受控生图能力。
- 当用户明确要求生成图片时，你可以发起一次受控生图请求。
- 受控生图请求必须只输出一个机器可解析块，不要声称图片已完成。后端会接管生成、落盘和 Asset Library 登记。
- 生图请求格式：
<{ASSET_AGENT_IMAGE_GENERATION_TAG}>{{"title":"简短标题","prompt":"完整正向提示词","negative_prompt":"可选负向提示词","aspect":"16:9","reference_images":[{{"path":"SessionOutput/storyboard/assets/images/example.png","role":"TARGET_FRAME","label":"目标帧"}}],"notes":"简短说明"}}</{ASSET_AGENT_IMAGE_GENERATION_TAG}>
- reference_images 只能使用用户消息中列出的 Selected reference images；保留其中的 path/role/label。没有参考图时传空数组。
- role 只能是 TARGET_FRAME、HOST_REFERENCE、PRODUCT_REFERENCE、REFERENCE_IMAGE。不要把用户口播文案、字幕文案或说明文字写进画面。
""" if can_generate else f"""当前选中的 Agent 模型不具备生图能力。
- 不要输出 {ASSET_AGENT_IMAGE_GENERATION_TAG}。
- 如果用户要求生成图片，请给出可用的 PROMPT_CANDIDATE，并提示用户切换到 Max 后再生成。
"""
        return f"""你是 Koubo Upload Asset Library 的图像素材 agent，目标是帮助用户把创意整理成高质量图像，并在模型支持时通过受控流程生成图片。

工作边界：
- 只用对话和下方上下文工作；不要尝试读取文件、写文件、执行命令、访问网络或调用任何工具。
- 可以结合当前 StoryBoard、Upload 素材、一致性人物/产品参考，帮助用户补全主体、构图、光线、镜头、材质、风格、负面约束和宽高比。
- 用户想生成或你认为提示词已经可用时，给出一个或多个候选。每个候选必须包含一个机器可解析块：
<PROMPT_CANDIDATE>{{"title":"简短标题","positive_prompt":"完整正向提示词","negative_prompt":"负向提示词","aspect":"16:9","notes":"简短说明"}}</PROMPT_CANDIDATE>
- aspect 只能是 16:9、4:3、1:1、3:4、9:16 之一。positive_prompt 应该独立完整，不能依赖“如上文”。

生图能力：
{generation_policy}

当前 Koubo 上下文：
{compact_json(asset_agent_context(task))}
"""

    def selected_reference_context(values: Any) -> str:
        refs = normalize_reference_item_list(values)
        if not refs:
            return ""
        return "\n\nSelected reference images:\n" + "\n".join(
            f"- {item.get('role')}: {item.get('path')} ({item.get('label')})"
            for item in refs[:8]
        )

    def asset_agent_generation_instruction(payload: dict[str, Any], refs: list[dict[str, str]]) -> str:
        if deps.text(payload.get("intent")) != "generate_image" and not payload.get("generation_intent"):
            return ""
        aspect = deps.text(payload.get("aspect"), "16:9")
        if aspect not in AGENT_SETTING_ASPECTS:
            aspect = "16:9"
        return f"""

The user explicitly requested Images-Agent image generation.
Produce exactly one <{ASSET_AGENT_IMAGE_GENERATION_TAG}> JSON block if the selected Agent model supports image generation.
Use aspect {aspect}.
Use these selected reference_images exactly if relevant, preserving role/path objects: {json.dumps(refs, ensure_ascii=False)}.
If TARGET_FRAME, HOST_REFERENCE, and PRODUCT_REFERENCE are present, this is a role-bound replacement task: TARGET_FRAME is the editable base scene, HOST_REFERENCE controls the person identity/styling, and PRODUCT_REFERENCE controls the product/package identity. User/spoken words are context only and must not appear as subtitles, captions, labels, UI text, or overlay text.
Do not call tools, do not write files, and do not say the image is complete.
"""

    def asset_agent_message_text(message: dict[str, Any]) -> str:
        parts = message.get("parts") if isinstance(message.get("parts"), list) else []
        return "\n".join(str(part.get("text") or "") for part in parts if isinstance(part, dict) and deps.text(part.get("type")) == "text").strip()

    def completed_assistant_message_from_event(event: dict[str, Any]) -> dict[str, Any] | None:
        if deps.text(event.get("type")) != "message.updated":
            return None
        properties = event.get("properties") if isinstance(event.get("properties"), dict) else {}
        if isinstance(properties.get("message"), dict):
            message = properties["message"]
        else:
            message = {
                "info": properties.get("info") if isinstance(properties.get("info"), dict) else {},
                "parts": properties.get("parts") if isinstance(properties.get("parts"), list) else [],
            }
        info = message.get("info") if isinstance(message.get("info"), dict) else {}
        if deps.text(info.get("role")) != "assistant":
            return None
        time_info = info.get("time") if isinstance(info.get("time"), dict) else {}
        if not time_info.get("completed"):
            return None
        if not asset_agent_message_text(message):
            return None
        return message

    def extract_agent_image_generation_requests(message_text: str) -> list[dict[str, Any]]:
        pattern = re.compile(rf"<{ASSET_AGENT_IMAGE_GENERATION_TAG}>([\s\S]*?)</{ASSET_AGENT_IMAGE_GENERATION_TAG}>")
        requests: list[dict[str, Any]] = []
        for match in pattern.finditer(message_text or ""):
            try:
                payload = json.loads(match.group(1).strip())
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            prompt = deps.text(payload.get("prompt") or payload.get("positive_prompt"))
            if not prompt:
                continue
            negative = deps.text(payload.get("negative_prompt"))
            if negative and "negative prompt:" not in prompt.lower():
                prompt = f"{prompt}\n\nNegative prompt:\n{negative}"
            aspect = deps.text(payload.get("aspect"), "16:9")
            if aspect not in AGENT_SETTING_ASPECTS:
                aspect = "16:9"
            requests.append({
                "title": deps.text(payload.get("title"), "Agent generated image")[:200],
                "prompt": prompt,
                "aspect": aspect,
                "reference_images": normalize_reference_item_list(payload.get("reference_images"), infer_first_target=True),
                "notes": deps.text(payload.get("notes"))[:500],
            })
        return requests[:1]

    def normalize_image_provider_id(provider: str, model: str = "") -> str:
        provider_value = deps.text(provider).lower()
        model_value = deps.text(model).lower()
        if provider_value in {"xai", "grok"} or "grok" in model_value:
            return "xai"
        if provider_value in {"gemini", "google"} or "gemini" in model_value:
            return "gemini"
        if provider_value in {"openai", "gpt"} or model_value.startswith("gpt-image"):
            return "openai"
        return provider_value

    def normalize_video_provider_id(provider: str, model: str = "") -> str:
        provider_value = deps.text(provider).lower()
        model_value = deps.text(model).lower()
        combined = f"{provider_value} {model_value}"
        if provider_value in {"xai", "grok"} or "grok" in model_value:
            return "xai"
        if provider_value in {"gemini", "google"} or "gemini" in model_value or "veo" in model_value:
            return "gemini"
        if provider_value in {"openai", "gpt", "sora"} or "sora" in model_value or model_value.startswith("gpt"):
            return "openai"
        if provider_value in {"wan", "dashscope", "alibaba"} or "wan" in model_value or "dashscope" in combined:
            return "wan"
        if provider_value in {"seedance", "bytedance", "volcengine", "doubao"} or "seedance" in model_value:
            return "openrouter"
        if provider_value in {"openrouter"} or "openrouter" in combined:
            return "openrouter"
        return provider_value

    def prompt_builder_model_looks_video(provider: str, model: str = "") -> bool:
        provider_value = deps.text(provider).lower()
        model_value = deps.text(model).lower()
        combined = f"{provider_value} {model_value}"
        markers = (
            "veo",
            "video",
            "sora",
            "wan",
            "seedance",
            "kling",
            "runway",
            "luma",
            "pixverse",
            "hailuo",
            "vidu",
            "pika",
        )
        return any(marker in combined for marker in markers)

    def asset_library_session_variables(task: dict[str, Any]) -> dict[str, Any]:
        path = deps.workspace_for(task) / "SessionContext" / "Variables.json"
        payload = deps.read_json(path)
        return payload if isinstance(payload, dict) else {}

    def asset_library_default_image_config(task: dict[str, Any]) -> dict[str, Any]:
        variables = asset_library_session_variables(task)
        config = variables.get("default_image_config") if isinstance(variables.get("default_image_config"), dict) else {}
        provider = normalize_image_provider_id(deps.text(config.get("provider")), deps.text(config.get("model")))
        model = deps.text(config.get("model"))
        if not provider:
            active = deps.active_image_model_public(sc=deps)
            provider = normalize_image_provider_id(deps.text(active.get("provider")), deps.text(active.get("model")))
            model = model or deps.text(active.get("model"))
        return {
            "provider": provider or "openai",
            "model": model,
            "has_api_key": bool(config.get("has_api_key")) if isinstance(config, dict) else False,
            "api_key_ref": deps.text(config.get("api_key_ref")) if isinstance(config, dict) else "",
            "source": "SessionContext/Variables.json:default_image_config" if provider else "Connection active image model",
        }

    def asset_library_image_model_config(_task: dict[str, Any]) -> dict[str, Any]:
        return customer_media_public_config(load_config(deps.ctx, "image"), "image")

    def agent_image_alias_from_payload(payload: dict[str, Any]) -> str:
        return deps.text(
            payload.get("agentImageAlias")
            or payload.get("agent_image_alias")
            or payload.get("agent_model_alias")
            or payload.get("model_alias")
            or payload.get("alias")
        )

    def resolve_agent_image_model_payload(payload: dict[str, Any]) -> tuple[str, str]:
        provider = deps.text(payload.get("provider"))
        model = deps.text(payload.get("model"))
        alias = agent_image_alias_from_payload(payload)
        if alias:
            for item in load_agent_model_aliases(deps.ctx):
                if deps.text(item.get("alias")) == alias:
                    alias_provider = deps.text(item.get("provider"))
                    alias_model = deps.text(item.get("model"))
                    if alias_provider and alias_model:
                        return alias_provider, alias_model
            alias_provider, alias_model = customer_media_public_alias_target(load_config(deps.ctx, "image"), "image", alias)
            if alias_provider and alias_model:
                return alias_provider, alias_model
            raise HTTPException(status_code=400, detail="Select a valid Agent image model before generating.")
        return provider, model

    def agent_video_alias_from_payload(payload: dict[str, Any]) -> str:
        return deps.text(
            payload.get("agentVideoAlias")
            or payload.get("agent_video_alias")
            or payload.get("agent_model_alias")
            or payload.get("model_alias")
            or payload.get("alias")
        )

    def resolve_agent_video_model_payload(payload: dict[str, Any], *, strict_alias: bool = True) -> tuple[str, str]:
        provider = deps.text(payload.get("provider"))
        model = deps.text(payload.get("model"))
        alias = agent_video_alias_from_payload(payload)
        if alias:
            for item in load_agent_model_aliases(deps.ctx, "video"):
                if deps.text(item.get("alias")) == alias:
                    alias_provider = deps.text(item.get("provider"))
                    alias_model = deps.text(item.get("model"))
                    if alias_provider and alias_model:
                        return alias_provider, alias_model
            alias_provider, alias_model = customer_media_public_alias_target(load_config(deps.ctx, "video"), "video", alias)
            if alias_provider and alias_model:
                return alias_provider, alias_model
            if strict_alias:
                raise HTTPException(status_code=400, detail="Select a valid Agent video model before generating.")
        return provider, model

    def video_api_generation_source(task: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        source = dict(payload) if isinstance(payload, dict) else {}
        if agent_video_alias_from_payload(source) or (deps.text(source.get("provider")) and deps.text(source.get("model"))):
            return source
        scope = deps.text(source.get("settingsScope") or source.get("settings_scope") or source.get("video_settings_scope"))
        workspace = deps.workspace_for(task)
        settings_paths = [VIDEO_API_SETTINGS_REL, VIDEOS_AGENT_SETTINGS_REL]
        if scope in {"videos_agent", "agent", "video_agent"}:
            settings_paths = [VIDEOS_AGENT_SETTINGS_REL, VIDEO_API_SETTINGS_REL]
        settings: dict[str, Any] = {}
        for rel_path in settings_paths:
            saved = deps.read_json(workspace / rel_path)
            candidate = normalize_videos_agent_settings(settings_source_for(saved)) if rel_path == VIDEOS_AGENT_SETTINGS_REL else normalize_video_api_settings(settings_source_for(saved))
            if candidate.get("agentVideoAlias") or (candidate.get("provider") and candidate.get("model")):
                settings = candidate
                break
        if settings.get("agentVideoAlias"):
            alias_provider, alias_model = resolve_agent_video_model_payload(settings, strict_alias=False)
            if alias_provider and alias_model:
                source["agentVideoAlias"] = settings["agentVideoAlias"]
                source["provider"] = ""
                source["model"] = ""
        elif settings.get("provider") and settings.get("model"):
            source["provider"] = settings["provider"]
            source["model"] = settings["model"]
        for key in ("aspect", "duration", "count", "referenceMode"):
            if not source.get(key) and settings.get(key):
                source[key] = settings[key]
        return source

    def asset_library_video_model_config(_task: dict[str, Any]) -> dict[str, Any]:
        return customer_media_public_config(load_config(deps.ctx, "video"), "video")

    def asset_library_tts_model_config(_task: dict[str, Any]) -> dict[str, Any]:
        config = load_config(deps.ctx, "tts")
        alias_state = tts_public_alias_state(deps.ctx, config)
        return customer_tts_public_config(config, alias_secret=deps.text(alias_state.get("secret")))

    def prompt_builder_request_id(value: str = "") -> str:
        raw = deps.text(value)
        if re.fullmatch(r"asset_prompt_builder_[0-9A-Za-z_:-]{8,96}", raw):
            return raw
        return f"asset_prompt_builder_{now_ms()}_{uuid.uuid4().hex[:8]}"

    def prompt_builder_template_for(provider: str, model: str, mode: str = "image") -> dict[str, str]:
        if deps.text(mode).lower() == "video":
            normalized = normalize_video_provider_id(provider, model)
            if normalized == "openai":
                return {"provider": "openai", "prefix": "VIDEO_GPT", "filename": "Video_GPT.md", "snapshot": "Ref_05_02_Video_GPT.md", "mode": "video"}
            if normalized == "gemini":
                return {"provider": "gemini", "prefix": "VIDEO_GEMINI", "filename": "Video_Gemini.md", "snapshot": "Ref_05_02_Video_Gemini.md", "mode": "video"}
            if normalized == "xai":
                return {"provider": "xai", "prefix": "VIDEO_GROK", "filename": "Video_Grok.md", "snapshot": "Ref_05_02_Video_Grok.md", "mode": "video"}
            if normalized == "wan":
                return {"provider": "wan", "prefix": "VIDEO_WAN", "filename": "Video_Wan.md", "snapshot": "Ref_05_02_Video_Wan.md", "mode": "video"}
            if normalized == "openrouter":
                return {"provider": "openrouter", "prefix": "VIDEO_OPENROUTER", "filename": "Video_OpenRouter.md", "snapshot": "Ref_05_02_Video_OpenRouter.md", "mode": "video"}
            if normalized == "seedance":
                return {"provider": "seedance", "prefix": "VIDEO_SEEDANCE", "filename": "Video_Seedance.md", "snapshot": "Ref_05_02_Video_Seedance.md", "mode": "video"}
            return {"provider": "", "prefix": "", "filename": "", "snapshot": "", "mode": "video"}
        normalized = normalize_image_provider_id(provider, model)
        if normalized == "openai":
            return {"provider": "openai", "prefix": "IMAGE_GPT", "filename": "Image_GPT.md", "snapshot": "Ref_05_02_Image_GPT.md", "mode": "image"}
        if normalized == "gemini":
            return {"provider": "gemini", "prefix": "IMAGE_GEMINI", "filename": "Image_Gemini.md", "snapshot": "Ref_05_02_Image_Gemini.md", "mode": "image"}
        if normalized == "xai":
            return {"provider": "xai", "prefix": "GROK", "filename": "Image_Grok.md", "snapshot": "Ref_05_02_Image_Grok.md", "mode": "image"}
        return {"provider": "", "prefix": "", "filename": "", "snapshot": "", "mode": "image"}

    def prompt_builder_block(template_text: str, name: str) -> str:
        start = f"<!-- OPENCREW:{name}_START -->"
        end = f"<!-- OPENCREW:{name}_END -->"
        if start not in template_text or end not in template_text:
            raise HTTPException(status_code=500, detail=f"Prompt template is missing block marker: {name}")
        return template_text.split(start, 1)[1].split(end, 1)[0].strip()

    def prompt_builder_render(value: str, variables: dict[str, str]) -> str:
        def replace(match: re.Match[str]) -> str:
            return variables.get(match.group(1).strip(), "")
        return re.sub(r"\{\{\s*([^}]+?)\s*\}\}", replace, value).strip()

    def prompt_builder_join(parts: list[str]) -> str:
        return "\n\n".join(deps.text(item) for item in parts if deps.text(item))

    def prompt_builder_reference_item(value: Any) -> dict[str, str]:
        if isinstance(value, dict):
            path = deps.text(value.get("path") or value.get("working_path") or value.get("source_path"))
            label = deps.text(value.get("label") or value.get("filename") or Path(path).name)
            kind = deps.text(value.get("key") or value.get("kind") or value.get("role") or value.get("source"))
            source = deps.text(value.get("source"))
        else:
            path = deps.text(value)
            label = Path(path).name
            kind = ""
            source = ""
        haystack = f"{path} {label} {kind} {source}".lower()
        role = normalize_reference_role(value.get("role") or value.get("reference_role")) if isinstance(value, dict) else ""
        if not role:
            if "host" in haystack or "person" in haystack or "人物" in haystack:
                role = "HOST_REFERENCE"
            elif "product" in haystack or "prodcut" in haystack or "产品" in haystack:
                role = "PRODUCT_REFERENCE"
            elif "target" in haystack or "frame" in haystack or "working" in haystack:
                role = "TARGET_FRAME"
            else:
                role = "REFERENCE_IMAGE"
        return {"path": path, "label": label or Path(path).name, "kind": kind, "source": source, "role": role}

    def prompt_builder_reference_paths(workspace: Path, values: Any) -> tuple[list[dict[str, str]], list[str]]:
        references: list[dict[str, str]] = []
        warnings: list[str] = []
        seen: set[str] = set()
        for value in values if isinstance(values, list) else []:
            item = prompt_builder_reference_item(value)
            rel_path = deps.text(item.get("path"))
            if not rel_path or rel_path in seen:
                continue
            seen.add(rel_path)
            if not any(rel_path.startswith(prefix) for prefix in PROMPT_BUILDER_ALLOWED_REFERENCE_PREFIXES):
                raise HTTPException(status_code=400, detail={"message": "Reference image path is outside the Prompt Builder whitelist", "path": rel_path})
            _, abs_path = deps.safe_workspace_rel(workspace, rel_path)
            if not abs_path.exists() or not abs_path.is_file() or abs_path.suffix.lower() not in IMAGE_EXTS:
                raise HTTPException(status_code=400, detail={"message": "Reference image was not found in the current Session workspace", "path": rel_path})
            item["path"] = rel_path
            references.append(item)
        if not references:
            warnings.append("No reference images were selected for this Prompt Builder draft.")
        return references[:8], warnings

    def prompt_builder_current_model(payload: dict[str, Any]) -> tuple[str, str]:
        if deps.text(payload.get("mode")).lower() == "video" or agent_video_alias_from_payload(payload):
            return resolve_agent_video_model_payload(payload)
        return resolve_agent_image_model_payload(payload)

    def prompt_builder_copy_template(workspace: Path, template: dict[str, str], *, overwrite: bool = True) -> tuple[str, str, str]:
        source_template = PROMPT_BUILDER_TEMPLATE_DIR / template["filename"]
        if not source_template.exists():
            raise HTTPException(status_code=500, detail=f"Prompt template not found: {source_template}")
        target_dir = workspace / PROMPT_BUILDER_REL
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / template["snapshot"]
        if overwrite or not target_path.exists():
            shutil.copy2(source_template, target_path)
        template_text = target_path.read_text(encoding="utf-8")
        template_sha = hashlib.sha256(template_text.encode("utf-8")).hexdigest()
        return target_path.relative_to(workspace).as_posix(), template_text, template_sha

    def prompt_builder_optional_block(template_text: str, name: str) -> str:
        start = f"<!-- OPENCREW:{name}_START -->"
        end = f"<!-- OPENCREW:{name}_END -->"
        if start not in template_text or end not in template_text:
            return ""
        return template_text.split(start, 1)[1].split(end, 1)[0].strip()

    def prompt_builder_upsert_block(template_text: str, name: str, value: str) -> str:
        start = f"<!-- OPENCREW:{name}_START -->"
        end = f"<!-- OPENCREW:{name}_END -->"
        block = f"{start}\n{deps.text(value)}\n{end}"
        if start in template_text and end in template_text:
            before = template_text.split(start, 1)[0].rstrip()
            after = template_text.split(end, 1)[1].lstrip()
            return f"{before}\n\n{block}\n\n{after}".rstrip() + "\n"
        return f"{template_text.rstrip()}\n\n{block}\n"

    def prompt_builder_video_request_id(template: dict[str, str]) -> str:
        return f"asset_prompt_builder_video_{deps.text(template.get('provider'))}_{now_ms()}"

    def prompt_builder_video_template_from_request(request_id: str) -> dict[str, str]:
        raw = deps.text(request_id).lower()
        for provider in ("gemini", "xai", "openai", "wan", "openrouter", "seedance"):
            if f"_video_{provider}_" in raw:
                return prompt_builder_template_for(provider, "", "video")
        return {"provider": "", "prefix": "", "filename": "", "snapshot": "", "mode": "video"}

    def copy_video_prompt_template_snapshot_for_settings(task: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
        provider = deps.text(settings.get("provider"))
        model = deps.text(settings.get("model"))
        if not provider or not model:
            return {}
        template = prompt_builder_template_for(provider, model, "video")
        if not template["provider"]:
            return {}
        workspace = deps.workspace_for(task)
        template_path, _template_text, template_sha = prompt_builder_copy_template(workspace, template)
        snapshot = {
            "provider": provider,
            "model": model,
            "template_source": template["snapshot"],
            "template_path": template_path,
            "template_snapshot_sha256": template_sha,
            "copied_at": now_ms(),
        }
        deps.add_event(int(task["session_id"]), "koubo_storyboard.asset_library.video_prompt_builder.template_snapshotted", {
            "task_id": int(task["id"]),
            **snapshot,
        })
        return snapshot

    def prompt_builder_reference_order(references: list[dict[str, str]]) -> str:
        return "\n".join(
            f"{index}. {item['role']}: {item.get('label') or Path(item['path']).name} ({item['path']})"
            for index, item in enumerate(references, start=1)
        ) or "none"

    def prompt_builder_video_reference_guidance(references: list[dict[str, str]], aspect: str = "") -> str:
        if not references:
            return ""
        roles = {deps.text(item.get("role")) for item in references}
        lines = [
            "Role-bound visual references:",
            "Use the attached reference images exactly according to the role list below. Treat roles as binding instructions, not captions.",
            prompt_builder_reference_order(references),
        ]
        if "TARGET_FRAME" in roles:
            lines.append("TARGET_FRAME controls the editable base scene: composition, camera angle, background, pose category, hand/product positions, scale, perspective, occlusion, lighting, shadows, and phone-video texture.")
        if "HOST_REFERENCE" in roles:
            lines.append("HOST_REFERENCE controls the complete visible presenter identity and styling: face, hair, clothing, microphone/accessories, skin tone, and human continuity.")
        if "PRODUCT_REFERENCE" in roles:
            lines.append("PRODUCT_REFERENCE controls the complete product/package identity: package shape, color hierarchy, label direction, visible text-block structure, material, cap/seal, box/sachet structure, and graphic layout.")
        if {"TARGET_FRAME", "HOST_REFERENCE", "PRODUCT_REFERENCE"}.issubset(roles):
            lines.append("This is a strict role-bound replacement/continuation task: keep the target scene while replacing/maintaining the host and product identity from their role references.")
        lines.append("User/spoken words are semantic guidance only. Do not render subtitles, speech captions, title cards, labels, UI text, QR codes, watermarks, or overlay text from the request.")
        if aspect == "9:16":
            lines.append("Output aspect is 9:16 portrait. Recompose or crop naturally; never stretch, squeeze, or warp the person, product, or reference image.")
        elif aspect == "16:9":
            lines.append("Output aspect is 16:9 landscape. Recompose or crop naturally; preserve human and product geometry.")
        return "\n".join(lines)

    def build_video_prompt_builder_package(task: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        workspace = deps.workspace_for(task)
        provider, model = prompt_builder_current_model(payload)
        template = prompt_builder_template_for(provider, model, "video")
        if not template["provider"]:
            return {
                "ok": True,
                "supported": False,
                "provider": provider,
                "model": model,
                "mode": "video",
                "reason": "Prompt Builder supports configured OpenAI, Gemini, xAI, Wan, OpenRouter, and Seedance video models.",
                "warnings": ["Select a supported Video Config model in Settings to use this Builder."],
            }
        references, warnings = prompt_builder_reference_paths(workspace, payload.get("reference_images"))
        template_path, template_text, template_sha = prompt_builder_copy_template(workspace, template, overwrite=bool(payload.get("snapshot_only")))
        if bool(payload.get("snapshot_only")):
            snapshot = {
                "ok": True,
                "supported": True,
                "mode": "video",
                "provider": provider,
                "model": model,
                "template_source": template["snapshot"],
                "template_path": template_path,
                "template_snapshot_sha256": template_sha,
                "warnings": warnings,
                "snapshot_only": True,
            }
            deps.add_event(int(task["session_id"]), "koubo_storyboard.asset_library.video_prompt_builder.template_snapshotted", {
                "task_id": int(task["id"]),
                **snapshot,
            })
            return snapshot
        request_id = prompt_builder_video_request_id(template)
        draft = deps.text(payload.get("draft"))
        aspect = deps.text(payload.get("aspect"), "9:16")
        duration_seconds = deps.text(payload.get("duration") or payload.get("duration_seconds") or "4")
        cutaway = bool(payload.get("cutaway")) or bool(re.search(r"(cutaway|product[- ]?only|产品特写|空镜|纯产品)", draft, re.IGNORECASE))
        reference_order = prompt_builder_reference_order(references)
        reference_guidance = prompt_builder_video_reference_guidance(references, aspect)
        variables = {
            "shot_summary": deps.text(payload.get("shot_summary")) or "Asset Library free-form video generation.",
            "scene_summary": deps.text(payload.get("scene_summary")) or f"Aspect ratio: {aspect}. Duration: {duration_seconds}s.",
            "dialogue_text": draft or "No user draft was provided. Build a realistic short-video continuation.",
            "duration_seconds": duration_seconds,
            "cutaway_mode": "product_only_cutaway" if cutaway else "talking_head_or_standard_video",
            "reference_summary": ", ".join(item["role"] for item in references) or "none",
            "reference_order": reference_order,
        }
        prefix = template["prefix"]
        if prefix == "VIDEO_GROK":
            positive_blocks = [f"{prefix}_POSITIVE_CUTAWAY" if cutaway else f"{prefix}_POSITIVE_TALKING_HEAD", f"{prefix}_CONTEXT"]
        else:
            positive_blocks = [f"{prefix}_POSITIVE_BASE", f"{prefix}_DIALOGUE_CUTAWAY" if cutaway else f"{prefix}_DIALOGUE_STANDARD"]
        negative_blocks = [f"{prefix}_NEGATIVE_BASE", f"{prefix}_NEGATIVE_CUTAWAY" if cutaway else "", f"{prefix}_PITFALLS_APPEND_ONLY"]
        positive_override = prompt_builder_optional_block(template_text, "VIDEO_PROMPT_BUILDER_POSITIVE_OVERRIDE")
        negative_override = prompt_builder_optional_block(template_text, "VIDEO_PROMPT_BUILDER_NEGATIVE_OVERRIDE")
        prompt_override = prompt_builder_optional_block(template_text, "VIDEO_PROMPT_BUILDER_PROMPT_OVERRIDE")
        positive = positive_override or prompt_builder_join([prompt_builder_render(prompt_builder_block(template_text, name), variables) for name in positive_blocks if name])
        if not positive_override:
            positive = prompt_builder_join([positive, reference_guidance])
            if draft:
                positive = prompt_builder_join([positive, f"User creative request:\n{draft}"])
        negative = negative_override or prompt_builder_join([prompt_builder_render(prompt_builder_block(template_text, name), variables) for name in negative_blocks if name])
        prompt = prompt_override or prompt_builder_render(prompt_builder_block(template_text, f"{prefix}_PROMPT"), {**variables, "positive_prompt": positive, "negative_prompt": negative})
        package = {
            "schema_version": VIDEO_PROMPT_BUILDER_SCHEMA,
            "request_id": request_id,
            "task_id": int(task["id"]),
            "session_id": int(task["session_id"]),
            "mode": "video",
            "provider": provider,
            "model": model,
            "template_source": template["snapshot"],
            "template_path": template_path,
            "template_snapshot_sha256": template_sha,
            "template_blocks": [name for name in positive_blocks + negative_blocks + [f"{prefix}_PROMPT"] if deps.text(name)],
            "source": {
                "composer_draft": draft,
                "reference_images": references,
                "storyboard_context": {
                    "shot_id": deps.text(payload.get("shot_id")),
                    "scene_id": deps.text(payload.get("scene_id")),
                    "dialogue_id": deps.text(payload.get("dialogue_id")),
                    "asset_key": deps.text(payload.get("asset_key")),
                    "aspect": aspect,
                    "duration_seconds": duration_seconds,
                    "cutaway": cutaway,
                },
            },
            "positive_prompt": positive,
            "negative_prompt": negative,
            "prompt": prompt,
            "user_edited": False,
            "created_at": now_ms(),
            "updated_at": now_ms(),
        }
        deps.add_event(int(task["session_id"]), "koubo_storyboard.asset_library.video_prompt_builder.saved", {
            "task_id": int(task["id"]),
            "request_id": request_id,
            "provider": provider,
            "model": model,
            "prompt_path": template_path,
            "reference_count": len(references),
        })
        return {"ok": True, "supported": True, "prompt_path": template_path, "draft_path": template_path, "warnings": warnings, **package}

    def build_prompt_builder_package(task: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        provider, model = prompt_builder_current_model(payload)
        requested_mode = deps.text(payload.get("mode")).lower()
        if requested_mode == "video" or (requested_mode != "image" and prompt_builder_model_looks_video(provider, model)):
            return build_video_prompt_builder_package(task, {**payload, "mode": "video"})
        workspace = deps.workspace_for(task)
        template = prompt_builder_template_for(provider, model, "image")
        if not template["provider"]:
            return {
                "ok": True,
                "supported": False,
                "provider": provider,
                "model": model,
                "reason": "Prompt Builder supports OpenAI, Gemini, and xAI image models.",
                "warnings": ["Select an OpenAI, Gemini, or xAI image model in Settings to use this Builder."],
            }
        references, warnings = prompt_builder_reference_paths(workspace, payload.get("reference_images"))
        template_path, template_text, template_sha = prompt_builder_copy_template(workspace, template)
        request_id = prompt_builder_request_id()
        draft = deps.text(payload.get("draft"))
        aspect = deps.text(payload.get("aspect"), "9:16")
        cutaway = bool(payload.get("cutaway")) or bool(re.search(r"(cutaway|product[- ]?only|产品特写|空镜|纯产品)", draft, re.IGNORECASE))
        has_host = any(item.get("role") == "HOST_REFERENCE" for item in references)
        has_product = any(item.get("role") == "PRODUCT_REFERENCE" for item in references)
        reference_order = prompt_builder_reference_order(references)
        variables = {
            "shot_summary": deps.text(payload.get("shot_summary")) or "Asset Library free-form image generation.",
            "scene_summary": deps.text(payload.get("scene_summary")) or f"Aspect ratio: {aspect}.",
            "dialogue_text": draft or "No user draft was provided. Build a realistic short-video first frame.",
            "cutaway_mode": "product_only_cutaway" if cutaway else "talking_head_or_standard_frame",
            "reference_summary": ", ".join(item["role"] for item in references) or "none",
            "reference_order": reference_order,
        }
        prefix = template["prefix"]
        if prefix == "IMAGE_GEMINI":
            positive_blocks = [
                f"{prefix}_POSITIVE_BASE",
                f"{prefix}_HOST_CUTAWAY" if cutaway else f"{prefix}_HOST_STANDARD",
                f"{prefix}_PRODUCT",
                f"{prefix}_CONTEXT",
            ]
            negative_blocks = [f"{prefix}_NEGATIVE_BASE", f"{prefix}_NEGATIVE_CUTAWAY" if cutaway else "", f"{prefix}_PITFALLS_APPEND_ONLY"]
        else:
            positive_blocks = [
                f"{prefix}_POSITIVE_BASE",
                f"{prefix}_HOST_CUTAWAY" if cutaway else (f"{prefix}_HOST_PRESENT" if has_host else f"{prefix}_HOST_MISSING"),
                f"{prefix}_PRODUCT_PRESENT" if has_product else f"{prefix}_PRODUCT_MISSING",
                f"{prefix}_POSITIVE_CUTAWAY" if cutaway else "",
                f"{prefix}_CONTEXT",
            ]
            negative_blocks = [f"{prefix}_NEGATIVE_BASE", f"{prefix}_NEGATIVE_CUTAWAY" if cutaway else "", f"{prefix}_PITFALLS_APPEND_ONLY"]
        positive = prompt_builder_join([prompt_builder_render(prompt_builder_block(template_text, name), variables) for name in positive_blocks if name])
        if draft:
            positive = prompt_builder_join([positive, f"User creative request:\n{draft}"])
        negative = prompt_builder_join([prompt_builder_render(prompt_builder_block(template_text, name), variables) for name in negative_blocks if name])
        prompt = prompt_builder_render(prompt_builder_block(template_text, f"{prefix}_PROMPT"), {**variables, "positive_prompt": positive, "negative_prompt": negative})
        package = {
            "schema_version": PROMPT_BUILDER_SCHEMA,
            "request_id": request_id,
            "task_id": int(task["id"]),
            "session_id": int(task["session_id"]),
            "provider": provider,
            "model": model,
            "template_source": template["snapshot"],
            "template_path": template_path,
            "template_snapshot_sha256": template_sha,
            "template_blocks": [name for name in positive_blocks + negative_blocks + [f"{prefix}_PROMPT"] if deps.text(name)],
            "source": {
                "composer_draft": draft,
                "reference_images": references,
                "storyboard_context": {
                    "shot_id": deps.text(payload.get("shot_id")),
                    "scene_id": deps.text(payload.get("scene_id")),
                    "dialogue_id": deps.text(payload.get("dialogue_id")),
                    "asset_key": deps.text(payload.get("asset_key")),
                    "aspect": aspect,
                    "cutaway": cutaway,
                },
            },
            "positive_prompt": positive,
            "negative_prompt": negative,
            "prompt": prompt,
            "user_edited": False,
            "created_at": now_ms(),
            "updated_at": now_ms(),
        }
        draft_path = f"{PROMPT_BUILDER_REL}/Draft_{request_id}_ImagePrompt.json"
        deps.write_json(workspace / draft_path, package)
        deps.add_event(int(task["session_id"]), "koubo_storyboard.asset_library_agent.prompt_builder.draft_created", {
            "task_id": int(task["id"]),
            "request_id": request_id,
            "provider": provider,
            "model": model,
            "draft_path": draft_path,
            "reference_count": len(references),
        })
        return {"ok": True, "supported": True, "draft_path": draft_path, "warnings": warnings, **package}

    def save_prompt_builder_applied(task: dict[str, Any], request_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        workspace = deps.workspace_for(task)
        safe_request_id = prompt_builder_request_id(request_id)
        if safe_request_id != request_id:
            raise HTTPException(status_code=400, detail="Invalid Prompt Builder request id")
        mode = deps.text(payload.get("mode")).lower()
        template_path = deps.text(payload.get("template_path"))
        if mode == "video" or template_path.startswith(f"{PROMPT_BUILDER_REL}/Ref_05_02_Video_") or request_id.startswith("asset_prompt_builder_video_"):
            if not template_path:
                provider = deps.text(payload.get("provider"))
                model = deps.text(payload.get("model"))
                template = prompt_builder_template_for(provider, model, "video")
                if not template["provider"]:
                    template = prompt_builder_video_template_from_request(request_id)
                if not template["provider"]:
                    raise HTTPException(status_code=400, detail="Video Prompt Builder template path is required")
                template_path = f"{PROMPT_BUILDER_REL}/{template['snapshot']}"
            if not template_path.startswith(f"{PROMPT_BUILDER_REL}/Ref_05_02_Video_") or not template_path.endswith(".md"):
                raise HTTPException(status_code=400, detail="Invalid Video Prompt Builder template path")
            _, template_abs = deps.safe_workspace_rel(workspace, template_path)
            if not template_abs.exists() or not template_abs.is_file():
                raise HTTPException(status_code=404, detail="Video Prompt Builder template was not found")
            positive = deps.text(payload.get("positive_prompt"))
            negative = deps.text(payload.get("negative_prompt"))
            prompt = deps.text(payload.get("prompt"))
            if not prompt:
                prompt = f"{positive}\n\nNegative prompt:\n{negative}".strip() if negative else positive
            current_text = template_abs.read_text(encoding="utf-8")
            updated_text = prompt_builder_upsert_block(current_text, "VIDEO_PROMPT_BUILDER_POSITIVE_OVERRIDE", positive)
            updated_text = prompt_builder_upsert_block(updated_text, "VIDEO_PROMPT_BUILDER_NEGATIVE_OVERRIDE", negative)
            updated_text = prompt_builder_upsert_block(updated_text, "VIDEO_PROMPT_BUILDER_PROMPT_OVERRIDE", prompt)
            template_abs.write_text(updated_text, encoding="utf-8")
            deps.add_event(int(task["session_id"]), "koubo_storyboard.asset_library.video_prompt_builder.saved", {
                "task_id": int(task["id"]),
                "request_id": request_id,
                "applied_path": template_path,
                "apply_mode": deps.text(payload.get("apply_mode"), "full"),
                "prompt_length": len(prompt),
            })
            return {"ok": True, "request_id": request_id, "applied_path": template_path, "prompt": prompt, "positive_prompt": positive, "negative_prompt": negative}
        image_draft_path = workspace / PROMPT_BUILDER_REL / f"Draft_{request_id}_ImagePrompt.json"
        draft_path = image_draft_path
        prompt_kind = "ImagePrompt"
        current = deps.read_json(draft_path)
        if current.get("schema_version") not in {PROMPT_BUILDER_SCHEMA, VIDEO_PROMPT_BUILDER_SCHEMA}:
            raise HTTPException(status_code=404, detail="Prompt Builder state was not found")
        positive = deps.text(payload.get("positive_prompt")) or deps.text(current.get("positive_prompt"))
        negative = deps.text(payload.get("negative_prompt"))
        if "negative_prompt" not in payload:
            negative = deps.text(current.get("negative_prompt"))
        prompt = deps.text(payload.get("prompt"))
        if not prompt:
            prompt = f"{positive}\n\nNegative prompt:\n{negative}".strip() if negative else positive
        applied = {
            **current,
            "positive_prompt": positive,
            "negative_prompt": negative,
            "prompt": prompt,
            "apply_mode": deps.text(payload.get("apply_mode"), "full"),
            "user_edited": True,
            "updated_at": now_ms(),
        }
        applied_path = f"{PROMPT_BUILDER_REL}/Applied_{request_id}_{prompt_kind}.json"
        deps.write_json(workspace / applied_path, applied)
        event_kind = "koubo_storyboard.asset_library_agent.prompt_builder.applied"
        deps.add_event(int(task["session_id"]), event_kind, {
            "task_id": int(task["id"]),
            "request_id": request_id,
            "applied_path": applied_path,
            "apply_mode": applied["apply_mode"],
            "prompt_length": len(prompt),
        })
        return {"ok": True, "request_id": request_id, "applied_path": applied_path, "prompt": prompt, "positive_prompt": positive, "negative_prompt": negative}

    def move_uploaded_asset_to_history(task: dict[str, Any], asset_id: str, reason: str = "asset_library_move_to_history") -> dict[str, Any]:
        workspace = deps.workspace_for(task)
        asset_id = urllib.parse.unquote(deps.text(asset_id))
        store = deps.read_json(workspace / ASSETS_REL)
        assets = store.get("assets") if isinstance(store.get("assets"), list) else []
        target = next((asset for asset in assets if deps.text(asset.get("id")) == asset_id or deps.text(asset.get("path")) == asset_id), None)
        path = deps.text(target.get("path")) if isinstance(target, dict) else asset_id
        if not (
            path.startswith(f"{ASSET_IMAGES_REL}/")
            or path.startswith(f"{ASSET_AUDIOS_REL}/")
            or path.startswith(f"{ASSET_VIDEOS_REL}/")
            or path.startswith(f"{LEGACY_UPLOAD_ROOT_REL}/")
        ):
            raise HTTPException(status_code=404, detail="Uploaded asset not found")
        _, file_path = deps.safe_workspace_rel(workspace, path)
        sidecar = file_path.with_suffix(".json")
        moving_sidecar_only = (
            path.startswith(f"{ASSET_AUDIOS_REL}/")
            and (not file_path.exists() or not file_path.is_file())
            and sidecar.exists()
            and sidecar.is_file()
        )
        if not moving_sidecar_only and (not file_path.exists() or not file_path.is_file()):
            raise HTTPException(status_code=404, detail="Uploaded asset not found")
        batch = f"batch_{now_ms()}_{reason}"
        batch_rel = f"{ASSET_HISTORY_REL}/{batch}"
        history_source_path = sidecar if moving_sidecar_only else file_path
        target_rel = f"{batch_rel}/{history_source_path.name}"
        _, history_path = deps.safe_workspace_rel(workspace, target_rel)
        history_path.parent.mkdir(parents=True, exist_ok=True)
        if history_path.exists():
            history_path = history_path.parent / f"{history_path.stem}_{now_ms()}{history_path.suffix}"
            target_rel = history_path.relative_to(workspace).as_posix()
        shutil.move(str(history_source_path), str(history_path))

        if not moving_sidecar_only and sidecar.exists() and sidecar.is_file():
            sidecar_target = history_path.with_suffix(".json")
            if sidecar_target.exists():
                sidecar_target = sidecar_target.parent / f"{sidecar_target.stem}_{now_ms()}{sidecar_target.suffix}"
            shutil.move(str(sidecar), str(sidecar_target))

        item = deps.history_item_for(path, target_rel, reason, sc=deps)
        if moving_sidecar_only:
            item["asset_type"] = "Audio"
            item["json_only"] = True
        manifest_path = workspace / batch_rel / "manifest.json"
        manifest = {
            "schema_version": "storyboard_asset_history_0.1",
            "batch_id": batch,
            "reason": reason,
            "created_at": now_ms(),
            "items": [item],
        }
        deps.write_json(manifest_path, manifest)

        json_rel = sidecar.relative_to(workspace).as_posix()
        assets = [
            asset for asset in assets
            if deps.text(asset.get("id")) not in {asset_id, path, json_rel}
            and deps.text(asset.get("path")) not in {path, json_rel}
            and deps.text(asset.get("agent_session_path")) != json_rel
        ]
        deps.write_json(workspace / ASSETS_REL, {"assets": assets, "updated_at": now_ms()})

        plan = deps.read_json(workspace / EDIT_REL)
        cleared_refs = 0
        if (workspace / SOURCE_REL).exists() and plan.get("schema_version") == "koubo_storyboard_edit_0.1":
            cleared_refs = deps.clear_path_references(plan, path, sc=deps)
            deps.save_edit_and_source_storyboard(task, workspace, deps.recalculate(plan, sc=deps), sc=deps)
        deps.ctx.session_repo.add_event(int(task["session_id"]), "koubo_storyboard.assets.moved_to_history", json.dumps({"task_id": int(task["id"]), "asset_id": asset_id, "path": path, "history_path": target_rel, "cleared_refs": cleared_refs}, ensure_ascii=True), now_ms())
        return {"ok": True, **load_asset_library_payload(task, workspace), "history_asset": item}

    def asset_agent_reference_prompt_prefix(reference_items: list[dict[str, str]], aspect: str = "") -> str:
        if not reference_items:
            lines: list[str] = []
        else:
            roles = {deps.text(item.get("role")) for item in reference_items}
            lines = [
                "Reference image role binding:",
                "The attached reference images are ordered exactly as listed below. Treat each role as a binding instruction, not a caption.",
            ]
            for index, item in enumerate(reference_items, start=1):
                lines.append(f"{index}. {deps.text(item.get('role'), 'REFERENCE_IMAGE')}: {deps.text(item.get('label')) or Path(deps.text(item.get('path'))).name} ({deps.text(item.get('path'))})")
            if {"TARGET_FRAME", "HOST_REFERENCE", "PRODUCT_REFERENCE"}.issubset(roles):
                lines.extend([
                    "This is a strict replacement/edit task.",
                    "TARGET_FRAME is the editable base scene and controls composition, camera angle, background, pose category, hand positions, product positions, scale, perspective, occlusion, lighting, shadows, and phone-video texture.",
                    "HOST_REFERENCE controls the complete visible person identity and styling: face, hair, clothing, microphone/accessories, skin tone, and presenter appearance.",
                    "PRODUCT_REFERENCE controls the complete product/package identity: package shape, color hierarchy, brand/logo area, label direction, visible text-block structure, material, cap/seal, box/sachet structure, and graphic layout.",
                    "Do not merely remove text while preserving the original target-frame person or old product. Replace the person and product when their role references are present.",
                ])
            lines.extend([
                "If a reference image is a collage, contact sheet, or consistency board, use the visible person/product identity details from its panels; do not reproduce the board layout and do not stretch the reference canvas into the output.",
                "User/spoken words are semantic guidance only. Do not render subtitles, speech captions, title cards, labels, UI text, QR codes, watermarks, or overlay text from user instructions or dialogue.",
            ])
        if aspect == "9:16":
            lines.extend([
                "Output aspect is 9:16 portrait. Recompose, crop, or extend natural background space to fit the portrait canvas; never vertically scale, squeeze, or warp the person, product, or any reference image to fill the frame.",
                "Preserve natural human proportions: realistic face width/height, head size, neck length, shoulder width, torso length, arm/hand scale, and camera perspective.",
                "Preserve product/package geometry and real aspect ratio; do not elongate, narrow, or bend packaging to fit the portrait frame.",
                "Negative geometry constraints: vertically stretched face, narrowed face, elongated neck, elongated torso, squeezed shoulders, stretched body, stretched product, warped reference image.",
            ])
        elif aspect in {"16:9", "4:3", "3:4", "1:1"}:
            lines.append("Preserve natural human and product geometry. Fit the requested canvas by recomposing or cropping, not by stretching, squeezing, or warping subjects.")
        if not lines:
            return ""
        return "\n".join(lines)

    def asset_agent_effective_image_prompt(prompt: str, reference_items: list[dict[str, str]], aspect: str = "") -> str:
        prefix = asset_agent_reference_prompt_prefix(reference_items, aspect)
        return f"{prefix}\n\n{prompt}".strip() if prefix else prompt

    def generate_asset_library_image(task_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        workspace = deps.workspace_for(task)
        prompt = deps.text(payload.get("prompt"))
        if len(prompt) < 4:
            raise HTTPException(status_code=400, detail="prompt is required")
        requested_provider, requested_model = resolve_agent_image_model_payload(payload)
        if not requested_provider or not requested_model:
            raise HTTPException(status_code=400, detail="Select an Agent image model before generating.")
        requested_reference_items = normalize_reference_item_list(payload.get("reference_images"), infer_first_target=True)
        refs: list[str] = []
        reference_items: list[dict[str, str]] = []
        missing_refs: list[str] = []
        reference_paths: list[Path] = []
        for item in requested_reference_items[:8]:
            rel_path = deps.text(item.get("path"))
            if not any(rel_path.startswith(prefix) for prefix in PROMPT_BUILDER_ALLOWED_REFERENCE_PREFIXES):
                missing_refs.append(rel_path)
                continue
            _, path = deps.safe_workspace_rel(workspace, rel_path)
            if path.exists() and path.is_file() and path.suffix.lower() in IMAGE_EXTS:
                refs.append(rel_path)
                reference_items.append({**item, "path": rel_path})
                reference_paths.append(path)
            else:
                missing_refs.append(rel_path)
        if missing_refs:
            raise HTTPException(status_code=400, detail={"message": "Selected reference images were not found in uploaded assets", "missing_reference_images": missing_refs})
        if reference_paths:
            config, reference_image_provider_fallback_from = deps.load_reference_image_config(requested_provider, requested_model, sc=deps)
        else:
            config = deps.load_image_config(requested_provider, requested_model, sc=deps)
            reference_image_provider_fallback_from = ""
        batch = str(now_ms())
        output_dir = workspace / ASSET_IMAGES_REL
        output_dir.mkdir(parents=True, exist_ok=True)
        requested_aspect = image_aspect_for_request(prompt, deps.text(payload.get("size")), deps.text(payload.get("aspect"))) or "1:1"
        requested_size = image_size_for_aspect(requested_aspect)
        requested_count = requested_image_count(payload.get("count"))
        effective_prompt = asset_agent_effective_image_prompt(prompt, reference_items, requested_aspect)
        request_base = {
            "task_id": int(task["id"]),
            "session_id": int(task["session_id"]),
            "provider": config["provider"],
            "model": config["model"],
            "requested_aspect": requested_aspect,
            "requested_size": requested_size,
            "requested_count": requested_count,
            "reference_images": refs,
            "reference_image_roles": reference_items,
            "reference_count": len(reference_paths),
            "prompt_preview": prompt[:1000],
            "prompt_length": len(prompt),
            "effective_prompt_preview": effective_prompt[:1000],
            "effective_prompt_length": len(effective_prompt),
        }
        prompt_builder_request_id = deps.text(payload.get("prompt_builder_request_id"))
        prompt_builder_applied_path = deps.text(payload.get("prompt_builder_applied_path"))
        chat_session_id = deps.text(payload.get("chat_opencode_session_id") or payload.get("chat_session_id"))
        prompt_candidate_id = deps.text(payload.get("prompt_candidate_id"))
        prompt_candidate_title = deps.text(payload.get("prompt_candidate_title"))
        agent_message_id = deps.text(payload.get("agent_message_id"))
        agent_generation_id = deps.text(payload.get("agent_generation_id"))
        if prompt_builder_request_id:
            request_base["prompt_builder_request_id"] = prompt_builder_request_id
        if prompt_builder_applied_path:
            request_base["prompt_builder_applied_path"] = prompt_builder_applied_path
        if chat_session_id:
            request_base["chat_opencode_session_id"] = chat_session_id
        if prompt_candidate_id:
            request_base["prompt_candidate_id"] = prompt_candidate_id
        if prompt_candidate_title:
            request_base["prompt_candidate_title"] = prompt_candidate_title[:200]
        if agent_message_id:
            request_base["agent_message_id"] = agent_message_id
        if agent_generation_id:
            request_base["agent_generation_id"] = agent_generation_id
        if reference_image_provider_fallback_from:
            request_base["reference_image_provider_fallback_from"] = reference_image_provider_fallback_from

        assets: list[dict[str, Any]] = []
        outputs: list[str] = []
        request_details: list[dict[str, Any]] = []
        for generation_index in range(1, requested_count + 1):
            output_name = f"{batch}_agent_generated_{uuid.uuid4().hex[:8]}.png"
            output_path = output_dir / output_name
            output_rel = f"{ASSET_IMAGES_REL}/{output_name}"
            request_id = f"koubo_asset_library_agent_{batch}_{uuid.uuid4().hex[:8]}"
            request_detail = {
                "request_id": request_id,
                **request_base,
                "generation_index": generation_index,
                "output": output_rel,
            }
            deps.add_event(int(task["session_id"]), "koubo_storyboard.asset_library_agent.image.started", request_detail)
            image_bytes = deps.generate_image_bytes(config, effective_prompt, reference_paths or None, requested_size, requested_aspect, sc=deps)
            write_sanitized_image_bytes(output_path, image_bytes)
            local_usage = record_storyboard_usage(
                deps.ctx,
                task,
                request_id=request_id,
                provider=config["provider"],
                model_id=config["model"],
                modality="image",
                step_id="koubo_storyboard.asset_library_agent.image",
                units=image_usage_units(count=1, prompt=effective_prompt, reference_count=len(reference_paths)),
            )
            request_detail["local_usage"] = local_usage
            request_detail["local_usage_id"] = local_usage.get("local_usage_id", "")
            request_path = workspace / ASSET_IMAGES_REL / f"{Path(output_name).stem}.json"
            deps.write_json(request_path, {**request_detail, "prompt": prompt, "effective_prompt": effective_prompt, "generated_at": now_ms(), "local_usage": local_usage, "local_usage_id": local_usage.get("local_usage_id", "")})
            asset = uploaded_asset_payload(output_rel, "agent_generated", "Agent generated image", {
                "origin": {
                    "tool": "upload_asset_library_agent",
                    "request_id": request_id,
                    "local_usage_id": local_usage.get("local_usage_id", ""),
                    "prompt": prompt,
                    "effective_prompt": effective_prompt,
                    "provider": config["provider"],
                    "model": config["model"],
                    "requested_aspect": requested_aspect,
                    "requested_size": requested_size,
                    "requested_count": requested_count,
                    "generation_index": generation_index,
                    "reference_images": refs,
                    "reference_image_roles": reference_items,
                    "request_path": request_path.relative_to(workspace).as_posix(),
                    "chat_opencode_session_id": chat_session_id,
                    "prompt_candidate_id": prompt_candidate_id,
                    "agent_message_id": agent_message_id,
                    "agent_generation_id": agent_generation_id,
                },
            })
            deps.upsert_asset_manifest_item(workspace, asset, sc=deps)
            assets.append(asset)
            outputs.append(output_rel)
            request_details.append(request_detail)

        result = {"ok": True, **request_base, "request_id": request_details[0]["request_id"], "output": outputs[0], "outputs": outputs, "asset": assets[0], "assets": assets, "generated_count": len(assets), "elapsed_ms": 0}
        deps.add_event(int(task["session_id"]), "koubo_storyboard.asset_library_agent.image.generated", result)
        return {**result, **load_task_payload(task)}

    def sync_talking_head_tts_selection(workspace: Any, plan: dict[str, Any]) -> bool:
        selection = plan.get("storyboard_tts_selection") if isinstance(plan.get("storyboard_tts_selection"), dict) else {}
        if not selection:
            return False
        variables_path = workspace / "SessionContext" / "Variables.json"
        task_meta_path = workspace / "SessionOutput" / "task_list" / "task_meta.json"
        variables = deps.read_json(variables_path)
        task_meta = deps.read_json(task_meta_path)
        quick_config = variables.get("storyboard_quick_config") if isinstance(variables.get("storyboard_quick_config"), dict) else {}
        meta_quick_config = task_meta.get("storyboard_quick_config") if isinstance(task_meta.get("storyboard_quick_config"), dict) else {}
        profile = variables.get("workflow_profile") if isinstance(variables.get("workflow_profile"), dict) else {}
        meta_profile = task_meta.get("workflow_profile") if isinstance(task_meta.get("workflow_profile"), dict) else {}
        is_talking_head = (
            str(variables.get("profile_id") or variables.get("workflow_id") or variables.get("create_mode") or "").strip() in {"person_talking_head_v1", "person_talking_head"}
            or str(task_meta.get("profile_id") or task_meta.get("workflow_id") or task_meta.get("create_mode") or "").strip() in {"person_talking_head_v1", "person_talking_head"}
            or str(profile.get("profile_id") or profile.get("workflow_id") or profile.get("create_mode") or "").strip() in {"person_talking_head_v1", "person_talking_head"}
            or str(meta_profile.get("profile_id") or meta_profile.get("workflow_id") or meta_profile.get("create_mode") or "").strip() in {"person_talking_head_v1", "person_talking_head"}
            or isinstance(quick_config.get("talking_head"), dict)
            or isinstance(meta_quick_config.get("talking_head"), dict)
        )
        if not is_talking_head:
            return False

        voice_id = deps.text(selection.get("voice_id") or selection.get("voice"))
        voice_label = deps.text(selection.get("voice_label") or selection.get("label") or voice_id)
        provider = deps.text(selection.get("provider") or "heygen")
        model = deps.text(selection.get("model") or "heygen-voice-clone-v3")
        tempo = deps.number(selection.get("tempo") or 1, 1)
        if not voice_id:
            return False
        voice_timing = {
            "provider": provider,
            "model": model,
            "voice_id": voice_id,
            "voice_label": voice_label,
            "tempo": tempo,
            "duration_estimation": "voice_tempo",
        }

        def apply_to_container(container: dict[str, Any]) -> None:
            talking_head = container.get("talking_head") if isinstance(container.get("talking_head"), dict) else {}
            talking_head["voice_timing"] = {**(talking_head.get("voice_timing") if isinstance(talking_head.get("voice_timing"), dict) else {}), **voice_timing}
            container["talking_head"] = talking_head
            container["voice_provider"] = provider
            container["voice_id"] = voice_id
            container["voice_label"] = voice_label
            container["tempo"] = tempo
            quick = container.get("storyboard_quick_config") if isinstance(container.get("storyboard_quick_config"), dict) else {}
            quick_talking_head = quick.get("talking_head") if isinstance(quick.get("talking_head"), dict) else {}
            quick_talking_head["voice_timing"] = {**(quick_talking_head.get("voice_timing") if isinstance(quick_talking_head.get("voice_timing"), dict) else {}), **voice_timing}
            quick["talking_head"] = quick_talking_head
            container["storyboard_quick_config"] = quick

        apply_to_container(variables)
        apply_to_container(task_meta)

        clone_config = variables.get("default_voice_clone_config") if isinstance(variables.get("default_voice_clone_config"), dict) else {}
        clone_config.update({"provider": provider, "model": model, "selected_voice_id": voice_id, "selected_voice_label": voice_label, "tempo": tempo})
        variables["default_voice_clone_config"] = clone_config
        talking_head_voice = variables.get("talking_head_voice") if isinstance(variables.get("talking_head_voice"), dict) else {}
        talking_head_voice.update({"provider": provider, "voice_id": voice_id, "voice_label": voice_label, "tempo": tempo})
        if isinstance(talking_head_voice.get("clone_config"), dict):
            talking_head_voice["clone_config"].update({"provider": provider, "model": model, "selected_voice_id": voice_id, "selected_voice_label": voice_label, "tempo": tempo})
        variables["talking_head_voice"] = talking_head_voice
        if isinstance(variables.get("talking_head_voice_calibration"), dict):
            variables["talking_head_voice_calibration"].update({"status": "stale", "provider": provider, "voice_id": voice_id, "voice_label": voice_label, "tempo": tempo, "reason": "storyboard_tts_selection_changed"})
        variables["updated_at"] = now_ms()
        task_meta["updated_at"] = now_ms()
        deps.write_json(variables_path, variables)
        deps.write_json(task_meta_path, task_meta)
        return True

    @router.put("/api/koubo-storyboard/tasks/{task_id}")
    async def save(task_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        plan = payload.get("plan") if isinstance(payload.get("plan"), dict) else payload
        try:
            plan = normalize_storyboard_tts_selection(
                deps.ctx,
                plan,
                active_clone_provider=load_configured_active_provider(deps.ctx, "voice-clone"),
                strict=True,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Select a valid cloud voice before saving.") from exc
        if not isinstance(plan.get("shots"), list):
            raise HTTPException(status_code=400, detail="plan.shots is required")
        workspace = deps.workspace_for(task)
        source_path = workspace / SOURCE_REL
        if not source_path.exists():
            raise HTTPException(status_code=404, detail=f"Analysis V1 StoryBoard output not found: {SOURCE_REL}")
        previous_plan = deps.read_json(workspace / EDIT_REL)
        if previous_plan.get("schema_version") != "koubo_storyboard_edit_0.1":
            previous_plan = deps.normalize_source_plan(task, deps.read_json(source_path), sc=deps)
        workflow_meta = storyboard_meta_for_workflow(infer_openclip_workflow_mode(task, workspace=workspace))
        plan = deps.recalculate({
            **plan,
            "schema_version": "koubo_storyboard_edit_0.1",
            "title": workflow_meta["title"],
            "source_type": workflow_meta["source_type"],
            "workflow_mode": workflow_meta["workflow_mode"],
            "analysis_task_id": int(task["id"]),
            "analysis_session_id": int(task["session_id"]),
            "source_path": SOURCE_REL,
        }, sc=deps)
        deps.materialize_plan_assets(workspace, plan, sc=deps)
        archived_assets = deps.archive_removed_generated_assets(workspace, previous_plan, plan, "storyboard_save_removed_generated_asset", sc=deps)
        deps.save_edit_and_source_storyboard(task, workspace, plan, sc=deps)
        tts_selection_synced = sync_talking_head_tts_selection(workspace, plan)
        deps.ctx.session_repo.add_event(int(task["session_id"]), "koubo_storyboard.saved", json.dumps({"task_id": task_id, "shot_count": len(plan.get("shots") or []), "archived_generated_assets": archived_assets, "tts_selection_synced": tts_selection_synced}, ensure_ascii=True), now_ms())
        return {"ok": True, **load_asset_library_payload(task, workspace)}

    @router.post("/api/koubo-storyboard/tasks/{task_id}/asset-bind")
    async def bind_asset(task_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        workspace = deps.workspace_for(task)
        if not (workspace / SOURCE_REL).exists():
            raise HTTPException(status_code=404, detail=f"Analysis V1 StoryBoard output not found: {SOURCE_REL}")
        dialogue_id = deps.text(payload.get("dialogue_id"))
        source_rel = deps.text(payload.get("asset_path"))
        target_kind = deps.text(payload.get("target_kind"))
        if not dialogue_id or not source_rel:
            raise HTTPException(status_code=400, detail="dialogue_id and asset_path are required")
        plan, regroup_backup = deps.coerce_edit_plan(task, workspace, payload.get("plan"), bool(payload.get("regroup_working_assets") or payload.get("clear_working_on_regroup")), sc=deps)
        plan = deps.bind_asset_to_plan(workspace, plan, dialogue_id, source_rel, target_kind, sc=deps)
        deps.save_edit_and_source_storyboard(task, workspace, deps.recalculate(plan, sc=deps), sc=deps)
        deps.ctx.session_repo.add_event(int(task["session_id"]), "koubo_storyboard.asset.bound", json.dumps({"task_id": task_id, "dialogue_id": dialogue_id, "asset_path": source_rel, "target_kind": target_kind, "regroup_backup": regroup_backup}, ensure_ascii=True), now_ms())
        return {"ok": True, **load_asset_library_payload(task, workspace)}

    @router.post("/api/koubo-storyboard/tasks/{task_id}/asset-clear")
    async def clear_asset(task_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        workspace = deps.workspace_for(task)
        dialogue_id = deps.text(payload.get("dialogue_id"))
        target_kind = deps.text(payload.get("target_kind"))
        if not dialogue_id or not target_kind:
            raise HTTPException(status_code=400, detail="dialogue_id and target_kind are required")
        plan, regroup_backup = deps.coerce_edit_plan(task, workspace, payload.get("plan"), bool(payload.get("regroup_working_assets") or payload.get("clear_working_on_regroup")), sc=deps)
        plan, old_path, deleted = deps.clear_asset_from_plan(workspace, plan, dialogue_id, target_kind, sc=deps)
        deps.save_edit_and_source_storyboard(task, workspace, deps.recalculate(plan, sc=deps), sc=deps)
        deps.ctx.session_repo.add_event(int(task["session_id"]), "koubo_storyboard.asset.cleared", json.dumps({"task_id": task_id, "dialogue_id": dialogue_id, "target_kind": target_kind, "old_path": old_path, "deleted_working_file": deleted, "regroup_backup": regroup_backup}, ensure_ascii=True), now_ms())
        loaded, meta = deps.load_plan(task, sc=deps)
        return {"ok": True, "task": task, "meta": meta, "plan": loaded, "deleted_working_file": deleted, "old_path": old_path}

    @router.post("/api/koubo-storyboard/tasks/{task_id}/assets")
    async def upload_assets(task_id: int, files: list[UploadFile] = File(default=[])) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        workspace = deps.workspace_for(task)
        store = deps.read_json(workspace / ASSETS_REL)
        assets = store.get("assets") if isinstance(store.get("assets"), list) else []
        batch = str(now_ms())
        added: list[dict[str, Any]] = []
        for index, upload in enumerate(files, start=1):
            filename = deps.safe_name(upload.filename or "", f"asset_{index:03d}.png")
            suffix = Path(filename).suffix.lower()
            content_type = deps.text(upload.content_type).lower()
            if content_type.startswith("audio/"):
                target_root_rel = ASSET_AUDIOS_REL
                asset_type = "Audio"
            elif content_type.startswith("video/"):
                target_root_rel = ASSET_VIDEOS_REL
                asset_type = "Video"
            elif suffix in VIDEO_EXTS:
                target_root_rel = ASSET_VIDEOS_REL
                asset_type = "Video"
            elif suffix in AUDIO_EXTS:
                target_root_rel = ASSET_AUDIOS_REL
                asset_type = "Audio"
            else:
                target_root_rel = ASSET_IMAGES_REL
                asset_type = "Image"
            target_dir = workspace / target_root_rel
            target_dir.mkdir(parents=True, exist_ok=True)
            filename = f"{batch}_{index:03d}_{filename}"
            target = unique_sibling_path(target_dir / filename)
            filename = target.name
            total = 0
            try:
                with target.open("wb") as handle:
                    while True:
                        chunk = await upload.read(1024 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        handle.write(chunk)
            except Exception:
                target.unlink(missing_ok=True)
                raise
            if total <= 0:
                target.unlink(missing_ok=True)
                continue
            rel_path = f"{target_root_rel}/{filename}"
            asset = uploaded_asset_payload(rel_path, "upload", filename, {
                "asset_type": asset_type,
                "kind": asset_type.lower(),
                "content_type": content_type,
                "size": total,
                "size_bytes": total,
            })
            assets.append(asset)
            added.append(asset)
        deps.write_json(workspace / ASSETS_REL, {"assets": assets, "updated_at": now_ms()})
        deps.ctx.session_repo.add_event(int(task["session_id"]), "koubo_storyboard.assets.uploaded", json.dumps({"task_id": task_id, "count": len(added)}, ensure_ascii=True), now_ms())
        return {"ok": True, **load_asset_library_payload(task, workspace), "added": added}

    @router.post("/api/koubo-storyboard/tasks/{task_id}/assets/source-copy")
    async def copy_source_assets(task_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        workspace = deps.workspace_for(task)
        source_paths = [deps.text(item) for item in payload.get("source_paths") or [] if deps.text(item)]
        limit = max(1, min(int(payload.get("limit") or 4), 12))
        if not source_paths:
            source = deps.read_json(workspace / SOURCE_REL)
            for group in deps.source_asset_groups_from_source(workspace, source, sc=deps):
                for item in group.get("scenes") or []:
                    rel_path = deps.text(item.get("path"))
                    if rel_path:
                        source_paths.append(rel_path)
                    if len(source_paths) >= limit:
                        break
                if len(source_paths) >= limit:
                    break
        batch = str(now_ms())
        target_dir = workspace / ASSET_IMAGES_REL
        target_dir.mkdir(parents=True, exist_ok=True)
        added: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, source_rel in enumerate(source_paths[:limit], start=1):
            if source_rel in seen:
                continue
            seen.add(source_rel)
            _, source_path = deps.safe_workspace_rel(workspace, source_rel)
            if not source_path.exists() or not source_path.is_file() or source_path.suffix.lower() not in IMAGE_EXTS:
                continue
            filename = deps.safe_name(source_path.name, f"source_{index:03d}{source_path.suffix.lower() or '.png'}")
            target_name = f"{batch}_{index:03d}_{filename}"
            target_path = target_dir / target_name
            shutil.copyfile(source_path, target_path)
            rel_path = f"{ASSET_IMAGES_REL}/{target_name}"
            asset = uploaded_asset_payload(rel_path, "source_copy", filename, {"origin": {"tool": "source_copy", "source_path": source_rel}})
            deps.upsert_asset_manifest_item(workspace, asset, sc=deps)
            added.append(asset)
        deps.add_event(int(task["session_id"]), "koubo_storyboard.assets.source_copied", {"task_id": task_id, "count": len(added), "source_count": len(source_paths)})
        plan, meta = deps.load_plan(task, sc=deps)
        return {"ok": True, "task": task, "meta": meta, "plan": plan, "added": added}

    @router.post("/api/koubo-storyboard/tasks/{task_id}/asset-library-agent/chat/ensure-session")
    async def ensure_asset_library_agent_chat_session(task_id: int, request: Request) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        return ensure_asset_agent_chat_session(task, request_role(request))

    @router.get("/api/koubo-storyboard/tasks/{task_id}/asset-library-agent/chat/messages")
    async def get_asset_library_agent_chat_messages(task_id: int, request: Request) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        role = request_role(request)
        settings_payload = read_or_create_agent_settings(task)
        chat_session_id = deps.text(settings_payload.get("chat_opencode_session_id"))
        if not chat_session_id:
            return {
                "ok": True,
                "chat_opencode_session_id": "",
                "items": [],
                "prompt_models": asset_agent_prompt_models(task, role),
            }
        try:
            messages = deps.opencode_client_for(asset_agent_session_row(task), sc=deps).messages(chat_session_id, limit=ASSET_AGENT_CHAT_MESSAGE_LIMIT)
        except Exception as exc:
            if not opencode_session_not_found(exc):
                raise
            clear_stale_asset_agent_chat_session(task, chat_session_id, "messages")
            return {
                "ok": True,
                "chat_opencode_session_id": "",
                "items": [],
                "prompt_models": asset_agent_prompt_models(task, role),
                "recovered": True,
            }
        return {
            "ok": True,
            "chat_opencode_session_id": chat_session_id,
            "items": [safe_opencode_message(message) for message in messages],
            "prompt_models": asset_agent_prompt_models(task, role),
        }

    @router.post("/api/koubo-storyboard/tasks/{task_id}/asset-library-agent/chat/message")
    async def send_asset_library_agent_chat_message(task_id: int, request: Request, payload: dict[str, Any]) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        role = request_role(request)
        message = deps.text(payload.get("message") or payload.get("text"))
        if not message:
            raise HTTPException(status_code=400, detail="message is required")
        if len(message) > 12000:
            raise HTTPException(status_code=400, detail="message is too long")
        state = ensure_asset_agent_chat_session(task, role)
        session_row = asset_agent_session_row(task)
        provider = deps.text(payload.get("provider") or payload.get("providerID"))
        model_id = deps.text(payload.get("model") or payload.get("modelID"))
        model, _prompt_models = deps.resolve_model(session_row, provider, model_id, ASSET_AGENT_CHAT_MODEL_ROLE, SURFACE_KOUBO_ASSET_AGENT_CHAT, sc=deps)
        model_can_generate = asset_agent_model_supports_image_generation(model)
        generation_intent = deps.text(payload.get("intent")) == "generate_image" or bool(payload.get("generation_intent"))
        if generation_intent and not model_can_generate:
            raise HTTPException(status_code=400, detail="Selected Agent model does not support image generation. Switch to Max to generate images.")
        chat_session_id = deps.text(state.get("chat_opencode_session_id"))
        selected_refs = normalize_reference_item_list(payload.get("reference_images"), infer_first_target=True)
        prompt = f"{message}{selected_reference_context(selected_refs)}{asset_agent_generation_instruction(payload, selected_refs)}"
        client = deps.opencode_client_for(session_row, sc=deps)
        try:
            client.prompt_async(
                chat_session_id,
                prompt,
                model=model,
                system=asset_agent_system_prompt(task, model),
                tools=ASSET_AGENT_CHAT_DISABLED_TOOLS,
            )
        except Exception as exc:
            if not opencode_session_not_found(exc):
                raise
            clear_stale_asset_agent_chat_session(task, chat_session_id, "message")
            state = ensure_asset_agent_chat_session(task, role)
            chat_session_id = deps.text(state.get("chat_opencode_session_id"))
            client.prompt_async(
                chat_session_id,
                prompt,
                model=model,
                system=asset_agent_system_prompt(task, model),
                tools=ASSET_AGENT_CHAT_DISABLED_TOOLS,
            )
        save_agent_chat_fields(task, {
            "chat_last_message_at": now_ms(),
            "chat_last_reference_images": selected_refs,
            "chat_last_model_provider": model["providerID"],
            "chat_last_model_id": model["modelID"],
            "chat_last_model_image_generation": model_can_generate,
        })
        deps.add_event(int(task["session_id"]), "koubo_storyboard.asset_library_agent.chat.message.sent", {
            "task_id": int(task["id"]),
            "chat_opencode_session_id": chat_session_id,
            "message_length": len(message),
            "reference_count": len(selected_refs),
            "generation_intent": generation_intent,
            "agent_image_generation": model_can_generate,
        })
        masked_model = mask_model_fields_for_role(deps.ctx, ASSET_AGENT_CHAT_MODEL_ROLE, SURFACE_KOUBO_ASSET_AGENT_CHAT, model)
        masked_model["asset_agent_image_generation"] = model_can_generate
        return {
            "ok": True,
            "chat_opencode_session_id": chat_session_id,
            "model": masked_model,
            "prompt_models": asset_agent_prompt_models(task, role),
        }

    @router.get("/api/koubo-storyboard/tasks/{task_id}/asset-library-agent/chat/events")
    async def stream_asset_library_agent_chat_events(task_id: int, request: Request) -> StreamingResponse:
        task = deps.task_or_404(task_id)
        role = request_role(request)
        state = ensure_asset_agent_chat_session(task, role)
        chat_session_id = deps.text(state.get("chat_opencode_session_id"))
        session_row = asset_agent_session_row(task)
        client = deps.opencode_client_for(session_row, sc=deps)
        stop_event = threading.Event()
        events: queue.Queue[dict[str, Any]] = queue.Queue()
        triggered_generation_keys: set[str] = set()

        def enqueue_agent_image_generation(message: dict[str, Any]) -> None:
            info = message.get("info") if isinstance(message.get("info"), dict) else {}
            message_id = deps.text(info.get("id")) or f"assistant_{now_ms()}"
            message_text = asset_agent_message_text(message)
            for request_payload in extract_agent_image_generation_requests(message_text):
                request_hash = hashlib.sha256(json.dumps(request_payload, sort_keys=True, ensure_ascii=True).encode("utf-8")).hexdigest()[:12]
                generation_key = f"{message_id}:{request_hash}"
                if generation_key in triggered_generation_keys:
                    continue
                triggered_generation_keys.add(generation_key)
                settings_payload = read_or_create_agent_settings(task)
                if not settings_payload.get("chat_last_model_image_generation"):
                    events.put({
                        "type": "asset_agent.image_generation.failed",
                        "properties": {
                            "agent_generation_id": generation_key,
                            "message": "Selected Agent model does not support image generation. Switch to Max to generate images.",
                        },
                    })
                    continue
                if not claim_agent_image_generation_key(task, generation_key):
                    continue
                agent_settings = settings_payload.get("settings") if isinstance(settings_payload.get("settings"), dict) else {}
                requested_count = requested_image_count(agent_settings.get("count"))
                refs = request_payload["reference_images"] or normalize_reference_item_list(settings_payload.get("chat_last_reference_images"), infer_first_target=True)
                generation_event_base = {
                    "agent_generation_id": generation_key,
                    "chat_opencode_session_id": chat_session_id,
                    "agent_message_id": message_id,
                    "title": request_payload["title"],
                    "aspect": request_payload["aspect"],
                    "count": requested_count,
                    "reference_count": len(refs),
                    "prompt_length": len(request_payload["prompt"]),
                }
                events.put({"type": "asset_agent.image_generation.started", "properties": generation_event_base})
                deps.add_event(int(task["session_id"]), "koubo_storyboard.asset_library_agent.image.agent_requested", {
                    "task_id": int(task["id"]),
                    **generation_event_base,
                })

                def worker() -> None:
                    started = time.time()
                    try:
                        result = generate_asset_library_image(task_id, {
                            "prompt": request_payload["prompt"],
                            "reference_images": refs,
                            "aspect": request_payload["aspect"],
                            "size": image_size_for_aspect(request_payload["aspect"]),
                            "count": requested_count,
                            "agentImageAlias": agent_settings.get("agentImageAlias") or agent_settings.get("agent_image_alias"),
                            "provider": agent_settings.get("provider"),
                            "model": agent_settings.get("model"),
                            "chat_opencode_session_id": chat_session_id,
                            "prompt_candidate_title": request_payload["title"],
                            "agent_message_id": message_id,
                            "agent_generation_id": generation_key,
                        })
                    except HTTPException as exc:
                        failed = {**generation_event_base, "status_code": exc.status_code, "detail": exc.detail, "elapsed_seconds": round(time.time() - started, 1)}
                        deps.add_event(int(task["session_id"]), "koubo_storyboard.asset_library_agent.image.agent_failed", failed)
                        events.put({"type": "asset_agent.image_generation.failed", "properties": failed})
                        return
                    except Exception as exc:
                        failed = {**generation_event_base, "detail": str(exc), "elapsed_seconds": round(time.time() - started, 1)}
                        deps.add_event(int(task["session_id"]), "koubo_storyboard.asset_library_agent.image.agent_failed", failed)
                        events.put({"type": "asset_agent.image_generation.failed", "properties": failed})
                        return
                    completed = {**generation_event_base, **result, "elapsed_seconds": round(time.time() - started, 1)}
                    events.put({"type": "asset_agent.image_generation.completed", "properties": completed})

                threading.Thread(target=worker, daemon=True).start()

        def enqueue_existing_agent_image_generations() -> None:
            try:
                messages = client.messages(chat_session_id, limit=ASSET_AGENT_CHAT_MESSAGE_LIMIT)
            except Exception as exc:
                deps.add_event(int(task["session_id"]), "koubo_storyboard.asset_library_agent.image.catchup_failed", {
                    "task_id": int(task["id"]),
                    "chat_opencode_session_id": chat_session_id,
                    "detail": str(exc)[:1000],
                })
                return
            for message in messages if isinstance(messages, list) else []:
                completed_message = completed_assistant_message_from_event({
                    "type": "message.updated",
                    "properties": {"message": safe_opencode_message(message)},
                })
                if completed_message:
                    enqueue_agent_image_generation(completed_message)

        def on_event(payload: dict[str, Any]) -> None:
            if opencode_event_has_tool_use(payload):
                try:
                    client.abort(chat_session_id)
                except Exception:
                    pass
                deps.add_event(int(task["session_id"]), "koubo_storyboard.asset_library_agent.chat.tool_blocked", {
                    "task_id": int(task["id"]),
                    "chat_opencode_session_id": chat_session_id,
                    "event_type": deps.text(payload.get("type")),
                })
                events.put({"type": "asset_agent.chat.tool_blocked", "properties": {"message": "OpenCode tool use was blocked for Asset Library Agent chat."}})
                return
            sanitized = sanitize_opencode_event(payload)
            if sanitized:
                events.put(sanitized)
                completed_message = completed_assistant_message_from_event(sanitized)
                if completed_message:
                    enqueue_agent_image_generation(completed_message)

        thread = threading.Thread(target=client.collect_events, args=(chat_session_id, stop_event, on_event), daemon=True)
        thread.start()
        enqueue_existing_agent_image_generations()

        async def event_generator() -> Any:
            yield f"data: {json.dumps({'type': 'ready', 'chat_opencode_session_id': chat_session_id}, ensure_ascii=True)}\n\n"
            heartbeat = 0
            last_catchup_at = time.time()
            try:
                while not await request.is_disconnected():
                    try:
                        item = events.get_nowait()
                    except queue.Empty:
                        if time.time() - last_catchup_at >= 8:
                            last_catchup_at = time.time()
                            enqueue_existing_agent_image_generations()
                        heartbeat += 1
                        if heartbeat % 15 == 0:
                            yield f"data: {json.dumps({'type': 'heartbeat', 'chat_opencode_session_id': chat_session_id}, ensure_ascii=True)}\n\n"
                        await asyncio.sleep(1)
                        continue
                    yield f"data: {json.dumps(item, ensure_ascii=True)}\n\n"
            finally:
                stop_event.set()

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    @router.post("/api/koubo-storyboard/tasks/{task_id}/asset-library-agent/chat/abort")
    async def abort_asset_library_agent_chat(task_id: int) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        settings_payload = read_or_create_agent_settings(task)
        chat_session_id = deps.text(settings_payload.get("chat_opencode_session_id"))
        if not chat_session_id:
            return {"ok": True, "chat_opencode_session_id": ""}
        try:
            deps.opencode_client_for(asset_agent_session_row(task), sc=deps).abort(chat_session_id)
        except Exception as exc:
            if not opencode_session_not_found(exc):
                raise
            clear_stale_asset_agent_chat_session(task, chat_session_id, "abort")
            return {"ok": True, "chat_opencode_session_id": "", "recovered": True}
        deps.add_event(int(task["session_id"]), "koubo_storyboard.asset_library_agent.chat.aborted", {
            "task_id": int(task["id"]),
            "chat_opencode_session_id": chat_session_id,
        })
        return {"ok": True, "chat_opencode_session_id": chat_session_id}

    @router.get("/api/koubo-storyboard/tasks/{task_id}/asset-library/image-api/settings")
    async def get_asset_library_image_api_settings(task_id: int) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        return read_or_create_image_api_settings(task)

    @router.put("/api/koubo-storyboard/tasks/{task_id}/asset-library/image-api/settings")
    async def save_asset_library_image_api_settings(task_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        return save_image_api_settings_payload(task, payload)

    @router.get("/api/koubo-storyboard/tasks/{task_id}/asset-library/image-api/history")
    async def get_asset_library_image_api_history(task_id: int) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        return read_or_create_image_api_workspace_history(task)

    @router.put("/api/koubo-storyboard/tasks/{task_id}/asset-library/image-api/history")
    async def save_asset_library_image_api_history(task_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        return save_image_api_workspace_history_payload(task, payload)

    @router.get("/api/koubo-storyboard/tasks/{task_id}/asset-library/images-agent/settings")
    async def get_asset_library_images_agent_settings(task_id: int) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        return read_or_create_agent_settings(task)

    @router.put("/api/koubo-storyboard/tasks/{task_id}/asset-library/images-agent/settings")
    async def save_asset_library_images_agent_settings(task_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        return save_agent_settings_payload(task, payload)

    @router.get("/api/koubo-storyboard/tasks/{task_id}/asset-library/video-api/settings")
    async def get_asset_library_video_api_settings(task_id: int) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        return read_or_create_video_api_settings(task)

    @router.put("/api/koubo-storyboard/tasks/{task_id}/asset-library/video-api/settings")
    async def save_asset_library_video_api_settings(task_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        return save_video_api_settings_payload(task, payload)

    @router.get("/api/koubo-storyboard/tasks/{task_id}/asset-library/video-api/history")
    async def get_asset_library_video_api_history(task_id: int) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        return read_or_create_video_api_workspace_history(task)

    @router.put("/api/koubo-storyboard/tasks/{task_id}/asset-library/video-api/history")
    async def save_asset_library_video_api_history(task_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        return save_video_api_workspace_history_payload(task, payload)

    @router.get("/api/koubo-storyboard/tasks/{task_id}/asset-library/video-interactions/current")
    async def get_current_video_interaction(task_id: int, chat_session_id: str = "") -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        return deps.video_interaction_current_thread(task, chat_session_id, sc=deps)

    @router.get("/api/koubo-storyboard/tasks/{task_id}/asset-library/video-interactions/{thread_id}")
    async def get_video_interaction_thread(task_id: int, thread_id: str) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        return deps.video_interaction_thread(task, thread_id, sc=deps)

    @router.post("/api/koubo-storyboard/tasks/{task_id}/asset-library/video-interactions/{thread_id}/cloud-context/delete")
    async def delete_video_interaction_context(task_id: int, thread_id: str) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        return deps.delete_video_interaction_cloud_context(task, thread_id, sc=deps)

    @router.post("/api/koubo-storyboard/tasks/{task_id}/asset-library/video-api/generate/events")
    async def asset_library_video_api_generate_events(task_id: int, payload: dict[str, Any]) -> StreamingResponse:
        task = deps.task_or_404(task_id)
        session_id = int(task["session_id"])
        source = payload if isinstance(payload, dict) else {}
        generation_source = video_api_generation_source(task, source)
        try:
            requested_count = int(generation_source.get("count") or 1)
        except Exception:
            requested_count = 1
        count = max(1, min(requested_count, 2))
        reference_image_count = len(generation_source.get("reference_images") or [])
        reference_audio_count = len(generation_source.get("reference_audios") or [])
        reference_video_count = len(generation_source.get("reference_videos") or [])
        public_agent_video_alias = agent_video_alias_from_payload(generation_source)
        public_provider = "" if public_agent_video_alias else deps.text(source.get("provider"))
        public_model = "" if public_agent_video_alias else deps.text(source.get("model"))
        if public_agent_video_alias:
            resolve_agent_video_model_payload(generation_source)
        request_payload = {
            "task_id": task_id,
            "session_id": session_id,
            "reference_count": reference_image_count + reference_audio_count + reference_video_count,
            "reference_image_count": reference_image_count,
            "reference_audio_count": reference_audio_count,
            "reference_video_count": reference_video_count,
            "prompt_length": len(deps.text(source.get("prompt"))),
            "count": count,
            "agentVideoAlias": public_agent_video_alias,
            "provider": public_provider,
            "model": public_model,
            "duration": generation_source.get("duration"),
            "aspect": deps.text(generation_source.get("aspect"), "9:16"),
            "operation": deps.text(generation_source.get("operation")),
            "stateful": bool(generation_source.get("stateful", False)),
            "video_thread_id": deps.text(generation_source.get("video_thread_id") or generation_source.get("thread_id")),
            "parent_turn_id": deps.text(generation_source.get("parent_turn_id") or generation_source.get("video_turn_id")),
            "client_action_id": deps.text(generation_source.get("client_action_id")),
        }
        deps.add_event(session_id, "koubo_storyboard.asset_library.video_api.requested", {**request_payload, "prompt_preview": deps.text(source.get("prompt"))[:1000]})

        async def event_generator() -> Any:
            started = time.time()
            yield f"data: {json.dumps({'type': 'started', **request_payload}, ensure_ascii=True)}\n\n"

            def run_batch() -> dict[str, Any]:
                assets: list[dict[str, Any]] = []
                outputs: list[str] = []
                last_result: dict[str, Any] = {}
                for index in range(count):
                    item_payload = {
                        **generation_source,
                        "title": deps.text(generation_source.get("title"), "Direct video generation") if count == 1 else f"{deps.text(generation_source.get('title'), 'Direct video generation')} {index + 1}",
                    }
                    result = deps.generate_asset_library_video(task_id, item_payload, sc=deps)
                    if result.get("pending") and result.get("video_turn_id"):
                        replay_deadline = time.time() + 900
                        while result.get("pending") and time.time() < replay_deadline:
                            time.sleep(2)
                            result = deps.generate_asset_library_video(task_id, item_payload, sc=deps)
                        if result.get("pending"):
                            raise HTTPException(status_code=504, detail={
                                "code": "gemini_omni_request_failed",
                                "message": "The existing Gemini Omni request is still pending and can be resumed with the same client action",
                            })
                    last_result = result
                    asset = result.get("asset") if isinstance(result.get("asset"), dict) else {}
                    if asset:
                        assets.append(asset)
                    if deps.text(result.get("output")):
                        outputs.append(deps.text(result.get("output")))
                first_asset = assets[0] if assets else {}
                return {
                    **last_result,
                    "ok": True,
                    "asset": first_asset,
                    "assets": assets,
                    "output": outputs[0] if outputs else deps.text(last_result.get("output")),
                    "outputs": outputs,
                    "generated_count": len(assets),
                }

            worker = asyncio.create_task(asyncio.to_thread(run_batch))
            heartbeat_no = 0
            while not worker.done():
                await asyncio.sleep(2)
                if worker.done():
                    break
                heartbeat_no += 1
                yield f"data: {json.dumps({'type': 'heartbeat', **request_payload, 'heartbeat': heartbeat_no, 'elapsed_seconds': round(time.time() - started, 1)}, ensure_ascii=True)}\n\n"
            try:
                result = await worker
            except HTTPException as exc:
                failed = {**request_payload, "status_code": exc.status_code, "detail": exc.detail, "elapsed_seconds": round(time.time() - started, 1)}
                deps.add_event(session_id, "koubo_storyboard.asset_library.video_api.failed", failed)
                yield f"data: {json.dumps({'type': 'failed', **failed}, ensure_ascii=True)}\n\n"
                return
            except Exception as exc:
                failed = {**request_payload, "detail": str(exc), "elapsed_seconds": round(time.time() - started, 1)}
                deps.add_event(session_id, "koubo_storyboard.asset_library.video_api.failed", failed)
                yield f"data: {json.dumps({'type': 'failed', **failed}, ensure_ascii=True)}\n\n"
                return
            deps.add_event(session_id, "koubo_storyboard.asset_library.video_api.completed", {"task_id": task_id, "session_id": session_id, "generated_count": result.get("generated_count", 0), "outputs": result.get("outputs", [])})
            yield f"data: {json.dumps({'type': 'completed', **result, 'elapsed_seconds': round(time.time() - started, 1)}, ensure_ascii=True)}\n\n"

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    @router.get("/api/koubo-storyboard/tasks/{task_id}/asset-library/videos-agent/settings")
    async def get_asset_library_videos_agent_settings(task_id: int) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        return read_or_create_videos_agent_settings(task)

    @router.put("/api/koubo-storyboard/tasks/{task_id}/asset-library/videos-agent/settings")
    async def save_asset_library_videos_agent_settings(task_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        return save_videos_agent_settings_payload(task, payload)

    @router.get("/api/koubo-storyboard/tasks/{task_id}/asset-library/image-model-config")
    async def get_asset_library_image_model_config(task_id: int) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        return asset_library_image_model_config(task)

    @router.get("/api/koubo-storyboard/tasks/{task_id}/asset-library/video-model-config")
    async def get_asset_library_video_model_config(task_id: int) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        return asset_library_video_model_config(task)

    @router.get("/api/koubo-storyboard/tasks/{task_id}/asset-library/tts-model-config")
    async def get_asset_library_tts_model_config(task_id: int) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        return asset_library_tts_model_config(task)

    @router.get("/api/koubo-storyboard/tasks/{task_id}/asset-library/tts-agent/messages")
    async def get_asset_library_tts_agent_messages(task_id: int) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        return read_or_create_tts_agent_messages(task)

    @router.put("/api/koubo-storyboard/tasks/{task_id}/asset-library/tts-agent/messages")
    async def save_asset_library_tts_agent_messages(task_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        return save_tts_agent_messages_payload(task, payload)

    @router.put("/api/koubo-storyboard/tasks/{task_id}/asset-library/tts-agent/sessions/{session_id}")
    async def save_asset_library_tts_agent_session(task_id: int, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        return save_tts_agent_session_artifact(task, session_id, payload)

    @router.get("/api/koubo-storyboard/tasks/{task_id}/asset-library-agent/settings")
    async def get_asset_library_agent_settings(task_id: int) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        return read_or_create_agent_settings(task)

    @router.put("/api/koubo-storyboard/tasks/{task_id}/asset-library-agent/settings")
    async def save_asset_library_agent_settings(task_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        return save_agent_settings_payload(task, payload)

    @router.post("/api/koubo-storyboard/tasks/{task_id}/asset-library-agent/prompt-builder")
    async def asset_library_agent_prompt_builder(task_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        return deps.redact_payload(build_prompt_builder_package(task, payload))

    @router.put("/api/koubo-storyboard/tasks/{task_id}/asset-library-agent/prompt-builder/{request_id}")
    async def save_asset_library_agent_prompt_builder(task_id: int, request_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        return deps.redact_payload(save_prompt_builder_applied(task, request_id, payload))

    @router.post("/api/koubo-storyboard/tasks/{task_id}/asset-library-agent/generate/events")
    async def asset_library_agent_generate_events(task_id: int, payload: dict[str, Any]) -> StreamingResponse:
        task = deps.task_or_404(task_id)
        session_id = int(task["session_id"])
        request_payload = {
            "task_id": task_id,
            "session_id": session_id,
            "reference_count": len(payload.get("reference_images") or []),
            "prompt_length": len(deps.text(payload.get("prompt"))),
        }
        deps.add_event(session_id, "koubo_storyboard.asset_library_agent.requested", {**request_payload, "prompt_preview": deps.text(payload.get("prompt"))[:1000]})

        async def event_generator() -> Any:
            started = time.time()
            yield f"data: {json.dumps({'type': 'started', **request_payload}, ensure_ascii=True)}\n\n"
            worker = asyncio.create_task(asyncio.to_thread(generate_asset_library_image, task_id, payload))
            heartbeat_no = 0
            while not worker.done():
                await asyncio.sleep(2)
                if worker.done():
                    break
                heartbeat_no += 1
                yield f"data: {json.dumps({'type': 'heartbeat', **request_payload, 'heartbeat': heartbeat_no, 'elapsed_seconds': round(time.time() - started, 1)}, ensure_ascii=True)}\n\n"
            try:
                result = await worker
            except HTTPException as exc:
                failed = {**request_payload, "status_code": exc.status_code, "detail": exc.detail, "elapsed_seconds": round(time.time() - started, 1)}
                deps.add_event(session_id, "koubo_storyboard.asset_library_agent.failed", failed)
                yield f"data: {json.dumps({'type': 'failed', **failed}, ensure_ascii=True)}\n\n"
                return
            except Exception as exc:
                failed = {**request_payload, "detail": str(exc), "elapsed_seconds": round(time.time() - started, 1)}
                deps.add_event(session_id, "koubo_storyboard.asset_library_agent.failed", failed)
                yield f"data: {json.dumps({'type': 'failed', **failed}, ensure_ascii=True)}\n\n"
                return
            yield f"data: {json.dumps({'type': 'completed', **result, 'elapsed_seconds': round(time.time() - started, 1)}, ensure_ascii=True)}\n\n"

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    @router.delete("/api/koubo-storyboard/tasks/{task_id}/assets/{asset_id:path}")
    async def delete_asset(task_id: int, asset_id: str) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        workspace = deps.workspace_for(task)
        asset_id = urllib.parse.unquote(deps.text(asset_id))
        store = deps.read_json(workspace / ASSETS_REL)
        assets = store.get("assets") if isinstance(store.get("assets"), list) else []
        target = next((asset for asset in assets if deps.text(asset.get("id")) == asset_id or deps.text(asset.get("path")) == asset_id), None)
        path = deps.text(target.get("path")) if isinstance(target, dict) else asset_id
        if not (
            path.startswith(f"{ASSET_IMAGES_REL}/")
            or path.startswith(f"{ASSET_AUDIOS_REL}/")
            or path.startswith(f"{ASSET_VIDEOS_REL}/")
            or path.startswith(f"{LEGACY_UPLOAD_ROOT_REL}/")
        ):
            raise HTTPException(status_code=404, detail="Uploaded asset not found")
        _, file_path = deps.safe_workspace_rel(workspace, path)
        if not file_path.exists() and not target:
            raise HTTPException(status_code=404, detail="Uploaded asset not found")
        if file_path.exists() and file_path.is_file():
            file_path.unlink()
        assets = [asset for asset in assets if deps.text(asset.get("id")) != asset_id and deps.text(asset.get("path")) != path]
        deps.write_json(workspace / ASSETS_REL, {"assets": assets, "updated_at": now_ms()})
        plan = deps.read_json(workspace / EDIT_REL)
        cleared_refs = 0
        if (workspace / SOURCE_REL).exists() and plan.get("schema_version") == "koubo_storyboard_edit_0.1":
            cleared_refs = deps.clear_path_references(plan, path, sc=deps)
            deps.save_edit_and_source_storyboard(task, workspace, deps.recalculate(plan, sc=deps), sc=deps)
        deps.ctx.session_repo.add_event(int(task["session_id"]), "koubo_storyboard.assets.deleted", json.dumps({"task_id": task_id, "asset_id": asset_id, "path": path, "cleared_refs": cleared_refs}, ensure_ascii=True), now_ms())
        return {"ok": True, **load_asset_library_payload(task, workspace)}

    @router.post("/api/koubo-storyboard/tasks/{task_id}/assets/{asset_id:path}/rename")
    async def rename_asset(task_id: int, asset_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        workspace = deps.workspace_for(task)
        asset_id = urllib.parse.unquote(deps.text(asset_id))
        store = deps.read_json(workspace / ASSETS_REL)
        assets = store.get("assets") if isinstance(store.get("assets"), list) else []
        target = next((asset for asset in assets if isinstance(asset, dict) and (deps.text(asset.get("id")) == asset_id or deps.text(asset.get("path")) == asset_id)), None)
        path = deps.text(target.get("path")) if isinstance(target, dict) else asset_id
        if not is_uploaded_asset_path(path):
            raise HTTPException(status_code=404, detail="Uploaded asset not found")
        rel_path, file_path = deps.safe_workspace_rel(workspace, path)
        if not file_path.exists() or not file_path.is_file():
            raise HTTPException(status_code=404, detail="Uploaded asset not found")
        filename = display_filename(deps.text(payload.get("filename") or payload.get("label")), file_path.stem, file_path.suffix.lower() or ".png")
        target_path = unique_sibling_path(file_path.with_name(filename))
        target_rel = target_path.relative_to(workspace).as_posix()
        if target_rel != rel_path:
            file_path.rename(target_path)
            sidecar = file_path.with_suffix(".json")
            if sidecar.exists() and sidecar.is_file():
                sidecar_target = target_path.with_suffix(".json")
                if sidecar_target.exists():
                    sidecar_target = unique_sibling_path(sidecar_target)
                sidecar.rename(sidecar_target)
        renamed_asset = {
            **(target if isinstance(target, dict) else deps.file_asset(target_rel, "upload", filename)),
            "id": target_rel,
            "path": target_rel,
            "label": filename,
            "filename": filename,
            "asset_type": deps.asset_type_for_path(target_rel),
            "kind": deps.asset_type_for_path(target_rel).lower(),
            "renamed_at": now_ms(),
            "renamed_from": rel_path,
        }
        next_assets: list[dict[str, Any]] = []
        found = False
        for item in assets:
            if not isinstance(item, dict):
                continue
            if deps.text(item.get("id")) == asset_id or deps.text(item.get("path")) == rel_path:
                next_assets.append(renamed_asset)
                found = True
            else:
                next_assets.append(item)
        if not found:
            next_assets.append(renamed_asset)
        deps.write_json(workspace / ASSETS_REL, {"assets": next_assets, "updated_at": now_ms()})
        replaced_refs = 0
        plan = deps.read_json(workspace / EDIT_REL)
        if (workspace / SOURCE_REL).exists() and plan.get("schema_version") == "koubo_storyboard_edit_0.1":
            replaced_refs = deps.replace_path_references(plan, rel_path, target_rel, sc=deps)
            if replaced_refs:
                deps.save_edit_and_source_storyboard(task, workspace, deps.recalculate(plan, sc=deps), sc=deps)
        deps.add_event(int(task["session_id"]), "koubo_storyboard.assets.renamed", {"task_id": task_id, "old_path": rel_path, "new_path": target_rel, "filename": filename, "replaced_refs": replaced_refs})
        return {"ok": True, **load_asset_library_payload(task, workspace), "asset": renamed_asset, "old_path": rel_path, "new_path": target_rel, "replaced_refs": replaced_refs}

    @router.post("/api/koubo-storyboard/tasks/{task_id}/assets/{asset_id:path}/move-to-history")
    async def move_asset_to_history(task_id: int, asset_id: str) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        return move_uploaded_asset_to_history(task, asset_id)

    @router.post("/api/koubo-storyboard/tasks/{task_id}/asset-history/restore")
    async def restore_history_asset(task_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        workspace = deps.workspace_for(task)
        rel_path = urllib.parse.unquote(deps.text(payload.get("asset_id") or payload.get("history_path") or payload.get("path")))
        if not rel_path.startswith(f"{ASSET_HISTORY_REL}/"):
            raise HTTPException(status_code=404, detail="History asset not found")
        _, file_path = deps.safe_workspace_rel(workspace, rel_path)
        suffix = file_path.suffix.lower()
        if not file_path.exists() or not file_path.is_file() or file_path.name == "manifest.json":
            raise HTTPException(status_code=404, detail="History asset not found")
        manifest_path = file_path.parent / "manifest.json"
        manifest = deps.read_json(manifest_path)
        items = manifest.get("items") if isinstance(manifest.get("items"), list) else []
        restored_items = [item for item in items if isinstance(item, dict) and deps.text(item.get("history_path")) == rel_path]
        restored_item = restored_items[0] if restored_items else {}
        json_only_audio = suffix == ".json" and bool(restored_item.get("json_only")) and deps.text(restored_item.get("asset_type")) == "Audio"
        if suffix not in IMAGE_EXTS | VIDEO_EXTS | AUDIO_EXTS and not json_only_audio:
            raise HTTPException(status_code=404, detail="History asset not found")
        original_path = deps.text(restored_items[0].get("original_path")) if restored_items else ""
        if json_only_audio:
            target_dir = workspace / ASSET_AUDIOS_REL
            target_dir.mkdir(parents=True, exist_ok=True)
            sidecar_target = unique_sibling_path(target_dir / deps.safe_name(file_path.name, file_path.name))
            shutil.copy2(str(file_path), str(sidecar_target))
            sidecar_rel = sidecar_target.relative_to(workspace).as_posix()
            sidecar_payload = deps.read_json(sidecar_target)
            if not isinstance(sidecar_payload, dict):
                sidecar_payload = {}
            audio = sidecar_payload.get("audio") if isinstance(sidecar_payload.get("audio"), dict) else {}
            audio_path = deps.text(audio.get("path")) or original_path
            if not audio_path.startswith(f"{ASSET_AUDIOS_REL}/") or Path(audio_path).suffix.lower() not in AUDIO_EXTS:
                audio_path = f"{ASSET_AUDIOS_REL}/{sidecar_target.stem}.wav"
            audio_exists = (workspace / audio_path).exists()
            sidecar_payload = {
                **sidecar_payload,
                "audio": {**audio, "path": audio_path, "filename": Path(audio_path).name},
                "audio_state": "ready" if audio_exists else deps.text(sidecar_payload.get("audio_state")) or "empty",
                "json_path": sidecar_rel,
                "updated_at": now_ms(),
            }
            deps.write_json(sidecar_target, sidecar_payload)
            extra = {
                "audio_exists": audio_exists,
                "missing_audio": not audio_exists,
                "agent_session_path": sidecar_rel,
                "tts_agent_session": sidecar_payload,
                "restored_from": rel_path,
                "source": "agent",
                "origin": {
                    "tool": "asset_history_restore",
                    "history_path": rel_path,
                    "original_path": original_path,
                    "reason": deps.text(restored_item.get("reason")) or deps.text(manifest.get("reason")),
                    "json_only": True,
                },
            }
            asset = uploaded_asset_payload(audio_path, "history_restore", deps.text(sidecar_payload.get("title")) or Path(audio_path).name, extra)
            deps.upsert_asset_manifest_item(workspace, asset, sc=deps)
            deps.ctx.session_repo.add_event(int(task["session_id"]), "koubo_storyboard.asset_history.restored", json.dumps({"task_id": task_id, "asset_id": rel_path, "path": audio_path, "json_path": sidecar_rel, "copied_items": len(restored_items)}, ensure_ascii=True), now_ms())
            return {"ok": True, **load_asset_library_payload(task, workspace), "asset": asset, "copied_items": len(restored_items)}
        original_name = Path(original_path).name if original_path and Path(original_path).suffix.lower() in IMAGE_EXTS | VIDEO_EXTS | AUDIO_EXTS else file_path.name
        filename = deps.safe_name(original_name, file_path.name)
        if suffix in VIDEO_EXTS:
            target_dir = workspace / ASSET_VIDEOS_REL
        elif suffix in AUDIO_EXTS:
            target_dir = workspace / ASSET_AUDIOS_REL
        else:
            target_dir = workspace / ASSET_IMAGES_REL
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = unique_sibling_path(target_dir / filename)
        shutil.copy2(str(file_path), str(target_path))
        sidecar = file_path.with_suffix(".json")
        sidecar_rel = ""
        sidecar_payload: dict[str, Any] | None = None
        if sidecar.exists() and sidecar.is_file():
            sidecar_target = target_path.with_suffix(".json")
            if sidecar_target.exists():
                sidecar_target = unique_sibling_path(sidecar_target)
            shutil.copy2(str(sidecar), str(sidecar_target))
            sidecar_rel = sidecar_target.relative_to(workspace).as_posix()
            payload = deps.read_json(sidecar_target)
            if isinstance(payload, dict):
                sidecar_payload = payload
        target_rel = target_path.relative_to(workspace).as_posix()
        extra = {
            "restored_from": rel_path,
            "origin": {
                "tool": "asset_history_restore",
                "history_path": rel_path,
                "original_path": original_path,
                "reason": deps.text(restored_items[0].get("reason")) if restored_items else deps.text(manifest.get("reason")),
            },
        }
        if suffix in AUDIO_EXTS and sidecar_rel:
            extra["agent_session_path"] = sidecar_rel
            extra["source"] = "agent"
            if isinstance(sidecar_payload, dict):
                extra["tts_agent_session"] = sidecar_payload
        asset = uploaded_asset_payload(target_rel, "history_restore", target_path.name, extra)
        deps.upsert_asset_manifest_item(workspace, asset, sc=deps)
        deps.ctx.session_repo.add_event(int(task["session_id"]), "koubo_storyboard.asset_history.restored", json.dumps({"task_id": task_id, "asset_id": rel_path, "path": target_rel, "copied_items": len(restored_items)}, ensure_ascii=True), now_ms())
        return {"ok": True, **load_asset_library_payload(task, workspace), "asset": asset, "copied_items": len(restored_items)}

    @router.delete("/api/koubo-storyboard/tasks/{task_id}/asset-history/{asset_id:path}")
    async def delete_history_asset(task_id: int, asset_id: str) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        workspace = deps.workspace_for(task)
        rel_path = urllib.parse.unquote(deps.text(asset_id))
        if not rel_path.startswith(f"{ASSET_HISTORY_REL}/"):
            raise HTTPException(status_code=404, detail="History asset not found")
        _, file_path = deps.safe_workspace_rel(workspace, rel_path)
        if not file_path.exists() or not file_path.is_file() or file_path.name == "manifest.json":
            raise HTTPException(status_code=404, detail="History asset not found")
        manifest_path = file_path.parent / "manifest.json"
        manifest = deps.read_json(manifest_path)
        items = manifest.get("items") if isinstance(manifest.get("items"), list) else []
        removed_items = [item for item in items if isinstance(item, dict) and deps.text(item.get("history_path")) == rel_path]
        file_path.unlink()
        manifest["items"] = [item for item in items if not isinstance(item, dict) or deps.text(item.get("history_path")) != rel_path]
        manifest["updated_at"] = now_ms()
        deps.write_json(manifest_path, manifest)
        deps.ctx.session_repo.add_event(int(task["session_id"]), "koubo_storyboard.asset_history.deleted", json.dumps({"task_id": task_id, "asset_id": rel_path, "removed_items": len(removed_items)}, ensure_ascii=True), now_ms())
        return {"ok": True, **load_asset_library_payload(task, workspace), "removed_items": len(removed_items)}
