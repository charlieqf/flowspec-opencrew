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
from .slot_state_services import SlotInputs, derive_all_plan_slot_states
from .text_utils import redact_payload, redact_secret_text


SERVICE_EXPORTS = (
    "file_exists",
    "file_status",
    "storyboard_video_slot_states",
    "load_plan",
)


def file_exists(workspace: Path, rel_path: str, *, sc: Any) -> bool:
    rel_path = sc.text(rel_path)
    if not rel_path:
        return False
    try:
        _, path = sc.safe_workspace_rel(workspace, rel_path)
    except HTTPException:
        return False
    return path.exists() and path.is_file() and path.stat().st_size > 0


def file_status(workspace: Path, rel_path: str, *, sc: Any) -> dict[str, Any]:
    rel_path = sc.text(rel_path)
    if not rel_path:
        return {"path": "", "exists": False, "size": 0, "mtime_ms": 0}
    try:
        _, path = sc.safe_workspace_rel(workspace, rel_path)
    except HTTPException:
        return {"path": rel_path, "exists": False, "size": 0, "mtime_ms": 0}
    if not path.exists() or not path.is_file() or path.stat().st_size <= 0:
        return {"path": rel_path, "exists": False, "size": 0, "mtime_ms": 0}
    stat = path.stat()
    return {"path": rel_path, "exists": True, "size": int(stat.st_size), "mtime_ms": int(stat.st_mtime * 1000)}


def storyboard_video_slot_states(workspace: Path, plan: dict[str, Any], *, sc: Any) -> dict[str, Any]:
    def standard_slot_file_exists(path: str, asset_key: str, slot: str) -> bool:
        rel_path = sc.text(path)
        name = Path(rel_path).name
        expected_prefix = f"{asset_key}_{slot}."
        return bool(rel_path and asset_key and name.startswith(expected_prefix) and file_exists(workspace, rel_path, sc=sc))

    def existing_working_slot_for_keys(keys: list[str], slot: str, exts: set[str] | None = None, preferred_suffix: str = ".mp4") -> str:
        for key in keys:
            rel_path = sc.existing_working_slot_path(workspace, key, slot, exts, preferred_suffix, sc=sc)
            if rel_path:
                return rel_path
        return ""

    def standard_slot_file_exists_for_keys(path: str, keys: list[str], slot: str) -> bool:
        return any(standard_slot_file_exists(path, key, slot) for key in keys)

    def dialogue_asset_lookup_keys(dialogue: dict[str, Any], asset_key: str) -> list[str]:
        keys: list[str] = []

        def add(value: str) -> None:
            item = sc.text(value)
            if item and item not in keys:
                keys.append(item)

        add(asset_key)
        add(dialogue.get("dialogue_id"))
        add(dialogue.get("srt_id"))
        for item in dialogue.get("srt_ids") if isinstance(dialogue.get("srt_ids"), list) else []:
            add(item)
        return keys

    by_dialogue_id: dict[str, Any] = {}
    by_asset_key: dict[str, Any] = {}
    for shot in plan.get("shots") or []:
        for scene in shot.get("scenes") or []:
            for dialogue in scene.get("dialogues") or []:
                if not isinstance(dialogue, dict):
                    continue
                dialogue_assets = sc.ensure_dialogue_working_assets(dialogue, sc=sc)
                asset_key = sc.dialogue_asset_key(dialogue, sc=sc)
                asset_keys = dialogue_asset_lookup_keys(dialogue, asset_key)
                raw_path = existing_working_slot_for_keys(asset_keys, "Video_Raw", VIDEO_EXTS)
                final_bound_path = sc.current_slot_path(dialogue_assets.get("video"), sc=sc)
                final_path = final_bound_path or existing_working_slot_for_keys(asset_keys, "Video_Final", VIDEO_EXTS)
                audio_path = sc.current_slot_path(dialogue_assets.get("audio"), sc=sc)
                audio_status = file_status(workspace, audio_path, sc=sc)
                raw_status = file_status(workspace, raw_path, sc=sc)
                final_status = file_status(workspace, final_path, sc=sc)
                raw_exists = bool(raw_status.get("exists"))
                final_exists = bool(final_status.get("exists"))
                final_bound = bool(final_bound_path and final_exists)
                audio_exists = bool(audio_status.get("exists"))
                final_stale_after_audio = bool(
                    audio_exists
                    and final_exists
                    and int(audio_status.get("mtime_ms") or 0) > int(final_status.get("mtime_ms") or 0)
                )
                images = dialogue_assets.get("images") if isinstance(dialogue_assets.get("images"), list) else []
                new_image_path = sc.text((images[0] or {}).get("path")) if images else sc.text(dialogue.get("bound_image_path"))
                new_image_exists = standard_slot_file_exists_for_keys(new_image_path, asset_keys, "Image_New")
                source_path = sc.text((dialogue.get("source_image_paths") or [""])[0]) or sc.text(dialogue.get("image_path"))
                source_exists = standard_slot_file_exists_for_keys(source_path, asset_keys, "Image_Source")
                image_prompt_path = existing_working_slot_for_keys(asset_keys, "ImagePrompt", {".json"}, ".json")
                video_prompt_path = existing_working_slot_for_keys(asset_keys, "VideoPrompt", {".json"}, ".json")
                image_prompt_exists = file_exists(workspace, image_prompt_path, sc=sc)
                video_prompt_exists = file_exists(workspace, video_prompt_path, sc=sc)
                if final_bound and not final_stale_after_audio:
                    next_action = "none"
                elif raw_exists:
                    next_action = "final_from_raw" if audio_exists else "audio_then_final"
                elif new_image_exists:
                    next_action = "raw_from_image"
                elif source_exists:
                    next_action = "image_from_source"
                else:
                    next_action = "bind_source"
                payload = {
                    "dialogue_id": sc.text(dialogue.get("dialogue_id")),
                    "asset_key": asset_key,
                    "audio_path": audio_path,
                    "raw_video_path": raw_path,
                    "raw_video_exists": raw_exists,
                    "final_video_path": final_path,
                    "final_video_exists": final_exists,
                    "final_video_bound": final_bound,
                    "final_video_bound_path": final_bound_path,
                    "final_video_stale_after_audio": final_stale_after_audio,
                    "audio_exists": audio_exists,
                    "audio_mtime_ms": int(audio_status.get("mtime_ms") or 0),
                    "raw_video_mtime_ms": int(raw_status.get("mtime_ms") or 0),
                    "final_video_mtime_ms": int(final_status.get("mtime_ms") or 0),
                    "audio_size": int(audio_status.get("size") or 0),
                    "raw_video_size": int(raw_status.get("size") or 0),
                    "final_video_size": int(final_status.get("size") or 0),
                    "new_image_exists": new_image_exists,
                    "source_image_exists": source_exists,
                    "image_prompt_path": image_prompt_path,
                    "image_prompt_exists": image_prompt_exists,
                    "video_prompt_path": video_prompt_path,
                    "video_prompt_exists": video_prompt_exists,
                    "next_action": next_action,
                    "slot_states": derive_all_plan_slot_states(SlotInputs(
                        audio_exists=audio_exists,
                        source_exists=source_exists,
                        image_exists=new_image_exists,
                        raw_exists=raw_exists,
                        final_exists=final_exists,
                        image_prompt_exists=image_prompt_exists,
                        video_prompt_exists=video_prompt_exists,
                        final_bound=final_bound,
                        binding_missing=final_exists and not final_bound,
                        binding_stale=bool(final_bound_path and not final_exists),
                    )),
                }
                if payload["dialogue_id"]:
                    by_dialogue_id[payload["dialogue_id"]] = payload
                if asset_key:
                    by_asset_key[asset_key] = payload
    return {"by_dialogue_id": by_dialogue_id, "by_asset_key": by_asset_key}


def apply_tts_manifest_durations(workspace: Path, plan: dict[str, Any], *, sc: Any) -> dict[str, Any]:
    for shot in plan.get("shots") or []:
        for scene in shot.get("scenes") or []:
            for dialogue in scene.get("dialogues") or []:
                if not isinstance(dialogue, dict):
                    continue
                asset_key = sc.dialogue_asset_key(dialogue, sc=sc)
                if not asset_key:
                    continue
                manifest = sc.read_json(workspace / "SessionOutput" / "storyboard" / "tts_manifests" / f"{asset_key}_Audio_Final.json")
                duration = sc.number(manifest.get("duration_seconds") or manifest.get("duration"))
                output_rel = sc.text(manifest.get("output"))
                if duration <= 0 or not output_rel or not file_exists(workspace, output_rel, sc=sc):
                    continue
                dialogue["duration"] = round(duration, 3)
                sc.ensure_dialogue_working_assets(dialogue, sc=sc)["audio"] = {
                    "slot": "Audio_Final",
                    "source_type": sc.asset_source_type(output_rel, sc=sc),
                    "path": output_rel,
                }
    return plan


def load_plan(task: dict[str, Any], *, sc: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    workspace = sc.workspace_for(task)
    source = sc.read_json(workspace / SOURCE_REL)
    if not source:
        raise HTTPException(status_code=404, detail=f"Analysis V1 StoryBoard output not found: {SOURCE_REL}")
    physical_source_revision = sc.stable_storyboard_sha256(source)
    source, source_signature, source_refreshed_from_tool_output = sc.current_storyboard_source(workspace, source)
    edit = sc.read_json(workspace / EDIT_REL)
    current_edit_revision = sc.storyboard_edit_revision_sha256(edit)
    stored_edit_revision = str(edit.get("edit_revision_sha256") or "").strip() if isinstance(edit, dict) else ""
    stored_source_revision = str(edit.get("source_revision_sha256") or "").strip() if isinstance(edit, dict) else ""
    source_changed_without_edit = bool(
        stored_source_revision
        and stored_source_revision != physical_source_revision
        and (not stored_edit_revision or stored_edit_revision == current_edit_revision)
    )
    stale_edit_ignored = bool(edit) and (
        not sc.storyboard_edit_matches_source(edit, source_signature)
        or source_changed_without_edit
    )
    plan = edit if edit.get("schema_version") == "koubo_storyboard_edit_0.1" and not stale_edit_ignored else sc.normalize_source_plan(task, source, sc=sc)
    plan = sc.apply_storyboard_source_signature(plan, source_signature)
    plan["edit_revision_sha256"] = current_edit_revision if not stale_edit_ignored else ""
    plan["source_revision_sha256"] = physical_source_revision
    plan = apply_tts_manifest_durations(workspace, plan, sc=sc)
    plan = sc.recalculate(plan, sc=sc)
    pools = sc.asset_pool_meta(workspace, sc=sc)
    saved_video_plan_settings = sc.video_plan_settings({"settings": sc.read_json(workspace / VIDEO_PLAN_SETTINGS_REL)})
    workflow_meta = storyboard_meta_for_workflow(infer_openclip_workflow_mode(task, workspace=workspace))
    variables = sc.read_json(workspace / "SessionContext" / "Variables.json")
    talking_head = variables.get("talking_head") if isinstance(variables.get("talking_head"), dict) else {}
    storyboard_quick_config = variables.get("storyboard_quick_config") if isinstance(variables.get("storyboard_quick_config"), dict) else {}
    meta = {
        "title": workflow_meta["title"],
        "source_type": workflow_meta["source_type"],
        "workflow_mode": workflow_meta["workflow_mode"],
        "analysis_task_id": int(task["id"]),
        "task_id": int(task["id"]),
        "analysis_session_id": int(task["session_id"]),
        "source_path": SOURCE_REL,
        "edit_path": EDIT_REL,
        "source_exists": True,
        "has_saved_edit": bool(edit) and not stale_edit_ignored,
        "stale_edit_ignored": stale_edit_ignored,
        "source_refreshed_from_tool_output": source_refreshed_from_tool_output,
        "active_source_path": source_signature.get("path", SOURCE_REL),
        "source_storyboard_sha256": source_signature.get("sha256", ""),
        "source_schema_version": source.get("schema_version", ""),
        "source_tool": source.get("tool", ""),
        "source_asset_groups": sc.source_asset_groups_from_source(workspace, source, sc=sc),
        "manual_assets": pools["uploaded_images"] + pools["uploaded_audios"] + pools["uploaded_videos"],
        "uploaded_images": pools["uploaded_images"],
        "uploaded_audios": pools["uploaded_audios"],
        "uploaded_videos": pools["uploaded_videos"],
        "history_versions": pools["history_versions"],
        "video_plan_settings": saved_video_plan_settings,
        "video_plan_settings_path": VIDEO_PLAN_SETTINGS_REL,
        "storyboard_video_slots": storyboard_video_slot_states(workspace, plan, sc=sc),
        "talking_head": talking_head,
        "storyboard_quick_config": storyboard_quick_config,
    }
    return plan, meta


def register_video_plan_load_services(ns: Any) -> None:
    for name in SERVICE_EXPORTS:
        setattr(ns, name, globals()[name])
