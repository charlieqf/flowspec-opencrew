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
    "copy_to_working",
    "working_slot_path",
    "existing_working_slot_path",
    "copy_to_standard_working_slot",
    "history_source_for_working_path",
    "recover_missing_working_asset",
    "current_slot_path",
    "dialogue_image_slot",
)


def copy_to_working(workspace: Path, source_rel: str, asset_key: str, slot: str, *, sc: Any) -> str:
    source_rel, source_path = sc.safe_workspace_rel(workspace, source_rel)
    if not source_path.exists() or not source_path.is_file():
        return ""
    if source_rel.startswith(f"{WORKING_REL}/"):
        return source_rel
    ext = source_path.suffix.lower() or ".bin"
    target_rel = f"{WORKING_REL}/{asset_key}_{slot}{ext}"
    _, target_path = sc.safe_workspace_rel(workspace, target_rel)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_path, target_path)
    return target_rel


def working_slot_path(asset_key: str, slot: str, suffix: str = ".mp4", *, sc: Any) -> str:
    ext = suffix if str(suffix or "").startswith(".") else f".{suffix or 'mp4'}"
    return f"{WORKING_REL}/{sc.text(asset_key)}_{slot}{ext}"


def existing_working_slot_path(workspace: Path, asset_key: str, slot: str, allowed_exts: set[str] | None = None, preferred_suffix: str = ".mp4", *, sc: Any) -> str:
    asset_key = sc.text(asset_key)
    if not asset_key:
        return ""
    suffixes = [preferred_suffix]
    for ext in sorted(allowed_exts or set()):
        if ext not in suffixes:
            suffixes.append(ext)
    for suffix in suffixes:
        rel_path = working_slot_path(asset_key, slot, suffix, sc=sc)
        try:
            _, path = sc.safe_workspace_rel(workspace, rel_path)
        except HTTPException:
            continue
        if path.exists() and path.is_file() and path.stat().st_size > 0:
            return rel_path
    return ""


def copy_to_standard_working_slot(workspace: Path, source_rel: str, asset_key: str, slot: str, allowed_exts: set[str] | None = None, *, sc: Any) -> str:
    source_rel, source_path = sc.safe_workspace_rel(workspace, source_rel)
    if not source_path.exists() or not source_path.is_file():
        return ""
    suffix = source_path.suffix.lower() or ".mp4"
    if allowed_exts and suffix not in allowed_exts:
        suffix = ".mp4"
    target_rel = working_slot_path(asset_key, slot, suffix, sc=sc)
    _, target_path = sc.safe_workspace_rel(workspace, target_rel)
    if source_rel == target_rel:
        return target_rel
    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_path, target_path)
    return target_rel


def history_source_for_working_path(workspace: Path, original_rel: str, *, sc: Any) -> str:
    original_rel = sc.text(original_rel)
    if not original_rel.startswith(f"{WORKING_REL}/"):
        return ""
    root = workspace / ASSET_HISTORY_REL
    if not root.exists():
        return ""
    for version_dir in sorted([item for item in root.iterdir() if item.is_dir()], key=lambda item: item.name, reverse=True):
        manifest = sc.read_json(version_dir / "manifest.json")
        for item in manifest.get("items") or []:
            if not isinstance(item, dict) or sc.text(item.get("original_path")) != original_rel:
                continue
            history_rel = sc.text(item.get("history_path"))
            if not history_rel:
                continue
            try:
                _rel, history_path = sc.safe_workspace_rel(workspace, history_rel)
            except HTTPException:
                continue
            if history_path.exists() and history_path.is_file():
                return history_rel
        orphan = version_dir / Path(original_rel).name
        if orphan.exists() and orphan.is_file():
            return orphan.relative_to(workspace).as_posix()
    return ""


def recover_missing_working_asset(workspace: Path, slot_path: str, asset_key: str, slot: str, *, sc: Any) -> str:
    slot_path = sc.text(slot_path)
    if not slot_path.startswith(f"{WORKING_REL}/"):
        return ""
    try:
        _rel, working_path = sc.safe_workspace_rel(workspace, slot_path)
    except HTTPException:
        return ""
    if working_path.exists() and working_path.is_file():
        return slot_path
    history_rel = history_source_for_working_path(workspace, slot_path, sc=sc)
    if not history_rel:
        return ""
    return copy_to_working(workspace, history_rel, asset_key, slot, sc=sc)


def current_slot_path(slot: Any, *, sc: Any) -> str:
    return sc.text(slot.get("path")) if isinstance(slot, dict) else ""


def dialogue_image_slot(dialogue: dict[str, Any], prefix: str = "Dialogue", *, sc: Any) -> str:
    index = int(sc.number(dialogue.get("dialogue_index"), 1) or 1)
    return f"Image_{prefix}_{index:03d}"


def register_working_asset_services(ns: Any) -> None:
    for name in SERVICE_EXPORTS:
        setattr(ns, name, globals()[name])
