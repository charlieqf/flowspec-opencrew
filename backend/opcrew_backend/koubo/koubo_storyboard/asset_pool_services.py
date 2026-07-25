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
from opcrew_backend.workflow_modes import infer_openclip_workflow_mode, storyboard_meta_for_workflow

from .constants import *
from .io_utils import read_json, safe_workspace_rel, write_json
from .runtime import analysis_tool_env
from .text_utils import redact_payload, redact_secret_text


SERVICE_EXPORTS = (
    "upsert_asset_manifest_item",
    "asset_pool_meta",
    "empty_asset_library_payload",
    "source_asset_groups_from_source",
)


def upsert_asset_manifest_item(workspace: Path, asset: dict[str, Any], *, sc: Any) -> None:
    store = sc.read_json(workspace / ASSETS_REL)
    assets = store.get("assets") if isinstance(store.get("assets"), list) else []
    path = sc.text(asset.get("path"))
    asset_id = sc.text(asset.get("id"), path)
    assets = [
        item
        for item in assets
        if not isinstance(item, dict) or (sc.text(item.get("path")) != path and sc.text(item.get("id")) != asset_id)
    ]
    assets.append(asset)
    sc.write_json(workspace / ASSETS_REL, {"assets": assets, "updated_at": now_ms()})


def asset_pool_meta(workspace: Path, *, sc: Any) -> dict[str, Any]:
    legacy = sc.read_json(workspace / ASSETS_REL).get("assets", [])
    legacy_items = legacy if isinstance(legacy, list) else []
    legacy_by_path = {sc.text(item.get("path")): item for item in legacy_items if isinstance(item, dict) and sc.text(item.get("path"))}
    uploaded_images = sc.list_dir_assets(workspace, ASSET_IMAGES_REL, "upload", IMAGE_EXTS, sc=sc)
    uploaded_audios = sc.list_dir_assets(workspace, ASSET_AUDIOS_REL, "upload", AUDIO_EXTS, sc=sc)
    known_audio_paths = {sc.text(item.get("path")) for item in uploaded_audios if isinstance(item, dict)}
    audio_root = workspace / ASSET_AUDIOS_REL
    if audio_root.exists():
        for sidecar in sorted(audio_root.glob("*.json"), key=lambda item: item.name):
            payload = sc.read_json(sidecar)
            if not isinstance(payload, dict):
                continue
            audio = payload.get("audio") if isinstance(payload.get("audio"), dict) else {}
            audio_path = sc.text(audio.get("path"))
            if not audio_path or not audio_path.startswith(f"{ASSET_AUDIOS_REL}/") or Path(audio_path).suffix.lower() not in AUDIO_EXTS:
                audio_path = f"{ASSET_AUDIOS_REL}/{sidecar.stem}.wav"
            if audio_path in known_audio_paths:
                continue
            asset = sc.file_asset(audio_path, "agent", sc.text(payload.get("title")) or sidecar.stem)
            asset.update({
                "audio_exists": (workspace / audio_path).exists(),
                "missing_audio": not (workspace / audio_path).exists(),
                "agent_session_path": sidecar.relative_to(workspace).as_posix(),
                "tts_agent_session": payload,
                "source": "agent",
            })
            uploaded_audios.append(asset)
            known_audio_paths.add(audio_path)
    uploaded_videos = sc.list_dir_assets(workspace, ASSET_VIDEOS_REL, "upload", VIDEO_EXTS, sc=sc)
    for collection in (uploaded_images, uploaded_audios, uploaded_videos):
        for index, item in enumerate(collection):
            stored = legacy_by_path.get(item["path"])
            if isinstance(stored, dict):
                collection[index] = {**item, **stored, "path": item["path"], "id": sc.text(stored.get("id"), item["path"])}
    known = {item["path"] for item in uploaded_images + uploaded_audios + uploaded_videos}
    for item in legacy_items:
        if not isinstance(item, dict):
            continue
        path = sc.text(item.get("path"))
        if not path or path in known:
            continue
        normalized = sc.file_asset(path, "upload", sc.text(item.get("label")) or sc.text(item.get("filename")))
        normalized["id"] = sc.text(item.get("id"), path)
        if normalized["asset_type"] == "Video":
            uploaded_videos.append(normalized)
        elif normalized["asset_type"] == "Audio":
            uploaded_audios.append(normalized)
        else:
            uploaded_images.append(normalized)
    return {
        "uploaded_images": uploaded_images,
        "uploaded_audios": uploaded_audios,
        "uploaded_videos": uploaded_videos,
        "history_versions": sc.history_versions(workspace, sc=sc),
    }


def empty_asset_library_payload(task: dict[str, Any], workspace: Path, *, sc: Any) -> dict[str, Any]:
    pools = sc.asset_pool_meta(workspace, sc=sc)
    workflow_meta = storyboard_meta_for_workflow(infer_openclip_workflow_mode(task, workspace=workspace))
    uploaded_images = pools["uploaded_images"]
    uploaded_audios = pools["uploaded_audios"]
    uploaded_videos = pools["uploaded_videos"]
    meta = {
        "title": workflow_meta["title"],
        "source_type": workflow_meta["source_type"],
        "workflow_mode": workflow_meta["workflow_mode"],
        "analysis_task_id": int(task["id"]),
        "task_id": int(task["id"]),
        "analysis_session_id": int(task["session_id"]),
        "source_path": SOURCE_REL,
        "edit_path": EDIT_REL,
        "source_exists": False,
        "storyboard_ready": False,
        "has_saved_edit": False,
        "manual_assets": [*uploaded_images, *uploaded_audios, *uploaded_videos],
        "uploaded_images": uploaded_images,
        "uploaded_audios": uploaded_audios,
        "uploaded_videos": uploaded_videos,
        "history_versions": pools["history_versions"],
    }
    plan = {
        "schema_version": "koubo_storyboard_edit_0.1",
        "title": workflow_meta["title"],
        "source_type": workflow_meta["source_type"],
        "workflow_mode": workflow_meta["workflow_mode"],
        "analysis_task_id": int(task["id"]),
        "analysis_session_id": int(task["session_id"]),
        "source_path": SOURCE_REL,
        "shots": [],
    }
    return {"ok": True, "task": task, "meta": meta, "plan": plan}


def source_asset_groups_from_source(workspace: Path, source: dict[str, Any], *, sc: Any) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for shot_index, source_shot in enumerate(source.get("shots") or [], start=1):
        if not isinstance(source_shot, dict):
            continue
        seen: set[str] = set()
        scenes: list[dict[str, Any]] = []
        shot_id = sc.text(source_shot.get("shot_id"), f"shot_{shot_index:03d}") or f"shot_{shot_index:03d}"
        for source_scene in source_shot.get("scenes") or []:
            if not isinstance(source_scene, dict):
                continue
            scene_id = sc.text(source_scene.get("scene_id"))
            for dialogue in source_scene.get("dialogue_items") or []:
                if not isinstance(dialogue, dict):
                    continue
                srt_id = sc.text(dialogue.get("srt_id"))
                rel_path = sc.text(dialogue.get("image_path"))
                if not rel_path and srt_id:
                    candidate = f"SessionOutput/visual/srt_frames/{srt_id}.jpg"
                    if (workspace / candidate).exists():
                        rel_path = candidate
                if not rel_path:
                    continue
                if rel_path in seen:
                    continue
                seen.add(rel_path)
                asset = sc.file_asset(rel_path, "source", srt_id or Path(rel_path).name)
                asset.update({
                    "shot_id": shot_id,
                    "scene_id": scene_id,
                    "srt_id": srt_id,
                    "duration": sc.number(dialogue.get("duration")),
                    "text": sc.text(dialogue.get("dialogue")),
                })
                scenes.append(asset)
        if scenes:
            groups.append({"shot_id": shot_id, "duration": sc.number(source_shot.get("duration")), "scenes": scenes})
    return groups


def register_asset_pool_services(ns: Any) -> None:
    for name in SERVICE_EXPORTS:
        setattr(ns, name, globals()[name])
