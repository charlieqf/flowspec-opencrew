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
    "backup_and_clear_working",
    "clear_working_asset_paths",
)


def backup_and_clear_working(workspace: Path, *, sc: Any) -> Optional[dict[str, Any]]:
    working_dir = workspace / WORKING_REL
    version = f"regroup_{now_ms()}"
    backup_rel = f"{ASSET_HISTORY_REL}/{version}"
    backup_working_rel = f"{backup_rel}/Working"
    backup_dir = workspace / backup_working_rel
    working_dir.mkdir(parents=True, exist_ok=True)
    items: list[dict[str, Any]] = []
    for child in sorted(working_dir.iterdir(), key=lambda item: item.name):
        if child.name == ".DS_Store":
            try:
                child.unlink()
            except FileNotFoundError:
                pass
            continue
        backup_dir.mkdir(parents=True, exist_ok=True)
        target = backup_dir / child.name
        if target.exists():
            target = backup_dir / f"{child.stem}_{now_ms()}{child.suffix}"
        shutil.move(str(child), str(target))
        original_rel = f"{WORKING_REL}/{child.name}"
        history_rel = target.relative_to(workspace).as_posix()
        items.append(sc.history_item_for(original_rel, history_rel, sc=sc))
    if not items:
        return None
    manifest = {
        "schema_version": "storyboard_asset_history_0.1",
        "reason": "regroup",
        "created_at": now_ms(),
        "source_working_path": WORKING_REL,
        "backup_path": backup_working_rel,
        "source_storyboard_path": SOURCE_REL,
        "items": items,
    }
    sc.write_json(workspace / backup_rel / "manifest.json", manifest)
    return {"backup_path": backup_rel, "working_path": backup_working_rel, "moved_count": len(items), "items": items}


def clear_working_asset_paths(plan: dict[str, Any], *, sc: Any) -> None:
    for shot in plan.get("shots") or []:
        for scene in shot.get("scenes") or []:
            working_assets = sc.ensure_working_assets(scene, sc=sc)
            audio = working_assets.get("audio")
            if isinstance(audio, dict):
                audio["path"] = ""
            video = working_assets.get("video")
            if isinstance(video, dict):
                video["path"] = ""
            images = working_assets.get("images")
            if isinstance(images, list):
                for image in images:
                    if isinstance(image, dict):
                        image["path"] = ""
            for dialogue in scene.get("dialogues") or []:
                dialogue_assets = sc.ensure_dialogue_working_assets(dialogue, sc=sc)
                dialogue_assets["audio"] = {"slot": "Audio_Final", "source_type": "", "path": ""}
                dialogue_assets["video"] = {"slot": "Video_Final", "source_type": "", "path": ""}
                dialogue_assets["images"] = [
                    {"slot": "Image_New", "source_type": "", "path": ""},
                    {"slot": "Image_02", "source_type": "", "path": ""},
                ]
                dialogue["bound_image_path"] = ""


def register_working_reset_services(ns: Any) -> None:
    for name in SERVICE_EXPORTS:
        setattr(ns, name, globals()[name])
