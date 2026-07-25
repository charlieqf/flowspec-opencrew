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
    "asset_type_for_path",
    "slot_for_path",
    "asset_key_parts",
    "file_asset",
    "list_dir_assets",
    "asset_source_type",
    "normalize_asset_slot",
    "ensure_working_assets",
    "new_dialogue_asset_key",
    "derive_dialogue_asset_key",
    "dialogue_asset_key",
    "ensure_dialogue_working_assets",
)


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


def asset_key_parts(asset_key: str) -> tuple[str, str]:
    value = str(asset_key or "").strip()
    if "_scene_" not in value:
        return "", ""
    shot_part, scene_part = value.split("_scene_", 1)
    return shot_part, f"scene_{scene_part}"


def file_asset(rel_path: str, source: str, label: str = "") -> dict[str, Any]:
    asset_type = asset_type_for_path(rel_path)
    return {
        "id": rel_path,
        "path": rel_path,
        "label": label or Path(rel_path).name,
        "filename": Path(rel_path).name,
        "asset_type": asset_type,
        "kind": asset_type.lower(),
        "source": source,
    }


def list_dir_assets(workspace: Path, rel_dir: str, source: str, allowed_exts: set[str], *, sc: Any) -> list[dict[str, Any]]:
    root = workspace / rel_dir
    if not root.exists():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == ".DS_Store" or path.suffix.lower() not in allowed_exts:
            continue
        rel_path = path.relative_to(workspace).as_posix()
        asset = file_asset(rel_path, source)
        sidecar = path.with_suffix(".json")
        if asset.get("asset_type") == "Audio" and sidecar.exists() and sidecar.is_file():
            asset["agent_session_path"] = sidecar.relative_to(workspace).as_posix()
            asset["source"] = "agent"
            sidecar_payload = sc.read_json(sidecar)
            if isinstance(sidecar_payload, dict):
                asset["tts_agent_session"] = sidecar_payload
        items.append(asset)
    return items


def asset_source_type(rel_path: str, explicit: str = "", *, sc: Any) -> str:
    explicit = sc.text(explicit)
    if explicit in {"original", "upload", "generated", "history", "dance_mimic_target_identity", "dance_mimic_reference_audio", "tail_frame_materialized"}:
        return explicit
    rel_path = sc.text(rel_path)
    if rel_path.startswith(f"{ASSET_HISTORY_REL}/"):
        return "history"
    if rel_path.startswith(f"{ASSET_IMAGES_REL}/") or rel_path.startswith(f"{ASSET_AUDIOS_REL}/") or rel_path.startswith(f"{ASSET_VIDEOS_REL}/") or rel_path.startswith(f"{LEGACY_UPLOAD_ROOT_REL}/"):
        return "upload"
    if rel_path.startswith(f"{CLEAN_IMAGE_REL}/"):
        return "generated"
    if rel_path.startswith(f"{WORKING_REL}/"):
        return "generated"
    if rel_path:
        return "original"
    return ""


def normalize_asset_slot(current: Any, slot: str, *, sc: Any) -> dict[str, str]:
    item = current if isinstance(current, dict) else {}
    path = sc.text(item.get("path"))
    return {"slot": slot, "source_type": asset_source_type(path, item.get("source_type"), sc=sc), "path": path}


def ensure_working_assets(scene: dict[str, Any], *, sc: Any) -> dict[str, Any]:
    working_assets = scene.get("working_assets") if isinstance(scene.get("working_assets"), dict) else {}
    audio = working_assets.get("audio") if isinstance(working_assets.get("audio"), dict) else {}
    video = working_assets.get("video") if isinstance(working_assets.get("video"), dict) else {}
    images = working_assets.get("images") if isinstance(working_assets.get("images"), list) else []
    normalized = {
        "audio": normalize_asset_slot(audio, "Audio_Final", sc=sc),
        "images": [normalize_asset_slot(images[index] if index < len(images) else {}, ["Image_New", "Image_02"][index], sc=sc) for index in range(2)],
        "video": normalize_asset_slot(video, "Video_Final", sc=sc),
    }
    scene["working_assets"] = normalized
    return normalized


def new_dialogue_asset_key(used_keys: set[str] | None = None) -> str:
    used = used_keys if isinstance(used_keys, set) else set()
    candidate = f"dak_{uuid.uuid4().hex[:12]}"
    while candidate in used:
        candidate = f"dak_{uuid.uuid4().hex[:12]}"
    return candidate


def derive_dialogue_asset_key(dialogue: dict[str, Any], used_keys: set[str] | None = None, *, sc: Any) -> str:
    used = used_keys if isinstance(used_keys, set) else set()
    explicit = sc.text(dialogue.get("dialogue_asset_key"))
    if explicit and explicit not in used:
        return explicit
    legacy_srt_id = sc.text(dialogue.get("srt_id"))
    if (
        legacy_srt_id
        and legacy_srt_id not in used
        and re.fullmatch(r"[A-Za-z0-9._:-]{1,255}", legacy_srt_id)
    ):
        return legacy_srt_id
    return new_dialogue_asset_key(used)


def dialogue_asset_key(dialogue: dict[str, Any], *, sc: Any) -> str:
    explicit = sc.text(dialogue.get("dialogue_asset_key"))
    if not explicit:
        raise HTTPException(status_code=400, detail="Dialogue asset key is missing")
    return explicit


def ensure_dialogue_working_assets(dialogue: dict[str, Any], *, sc: Any) -> dict[str, Any]:
    working_assets = dialogue.get("working_assets") if isinstance(dialogue.get("working_assets"), dict) else {}
    audio = working_assets.get("audio") if isinstance(working_assets.get("audio"), dict) else {}
    video = working_assets.get("video") if isinstance(working_assets.get("video"), dict) else {}
    images = working_assets.get("images") if isinstance(working_assets.get("images"), list) else []
    if not sc.text(audio.get("path")):
        legacy_audio = dialogue.get("audio_path")
        if legacy_audio:
            audio = {"slot": "Audio_Final", "path": sc.text(legacy_audio), "source_type": asset_source_type(sc.text(legacy_audio), sc=sc)}
    if not sc.text(video.get("path")):
        legacy_video = dialogue.get("video_path")
        if legacy_video:
            video = {"slot": "Video_Final", "path": sc.text(legacy_video), "source_type": asset_source_type(sc.text(legacy_video), sc=sc)}
    bound_image_path = sc.text(dialogue.get("bound_image_path"))
    source_paths = [sc.text(item) for item in dialogue.get("source_image_paths") or [] if sc.text(item)]
    source_image_path = source_paths[0] if source_paths else sc.text(dialogue.get("image_path"))
    if not images and bound_image_path and bound_image_path != source_image_path:
        images = [{"slot": "Image_New", "path": bound_image_path, "source_type": asset_source_type(bound_image_path, sc=sc)}]
    elif bound_image_path and bound_image_path == source_image_path:
        dialogue["bound_image_path"] = ""
    normalized = {
        "audio": normalize_asset_slot(audio, "Audio_Final", sc=sc),
        "images": [normalize_asset_slot(images[index] if index < len(images) else {}, ["Image_New", "Image_02"][index], sc=sc) for index in range(2)],
        "video": normalize_asset_slot(video, "Video_Final", sc=sc),
    }
    dialogue["working_assets"] = normalized
    if not sc.text(dialogue.get("dialogue_asset_key")):
        dialogue["dialogue_asset_key"] = derive_dialogue_asset_key(dialogue, sc=sc)
    return normalized


def register_asset_core_services(ns: Any) -> None:
    for name in SERVICE_EXPORTS:
        setattr(ns, name, globals()[name])
