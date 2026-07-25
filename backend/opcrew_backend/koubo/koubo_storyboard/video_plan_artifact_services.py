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
from .slot_state_services import SlotInputs, derive_video_plan_slot_states
from .text_utils import redact_payload, redact_secret_text


SERVICE_EXPORTS = (
    "video_plan_file_status",
    "scene_dialogues",
    "dialogue_match_keys",
    "dialogue_video_slot",
    "video_plan_final_bound",
    "storyboard_dialogue_for_asset",
    "dialogue_slot_path",
    "dialogue_source_image_path",
    "existing_working_status_for_keys",
    "first_done_status",
    "video_plan_asset_key_from_path",
    "video_plan_segment_asset_key",
    "video_plan_artifact_status",
)


def video_plan_file_status(workspace: Path, rel_path: str, *, sc: Any) -> dict[str, Any]:
    rel_value = sc.text(rel_path)
    if not rel_value:
        return {"path": "", "exists": False, "in_working": False, "in_working_exists": False}
    try:
        _, path = sc.safe_workspace_rel(workspace, rel_value)
    except HTTPException:
        return {"path": rel_value, "exists": False, "in_working": False, "in_working_exists": False}
    exists = path.exists() and path.is_file() and path.stat().st_size > 0
    in_working = rel_value.startswith(f"{WORKING_REL}/")
    return {
        "path": rel_value,
        "exists": exists,
        "in_working": in_working,
        "in_working_exists": exists and in_working,
    }


def scene_dialogues(scene: dict[str, Any]) -> list[dict[str, Any]]:
    dialogues: list[dict[str, Any]] = []
    for key in ("dialogue_items", "dialogues"):
        values = scene.get(key)
        if isinstance(values, list):
            dialogues.extend([item for item in values if isinstance(item, dict)])
    return dialogues


def dialogue_match_keys(dialogue: dict[str, Any], index: int = 0, *, sc: Any) -> set[str]:
    keys = {
        sc.text(dialogue.get("dialogue_asset_key")),
        sc.text(dialogue.get("dialogue_id")),
        sc.text(dialogue.get("srt_id")),
    }
    for item in dialogue.get("srt_ids") if isinstance(dialogue.get("srt_ids"), list) else []:
        keys.add(sc.text(item))
    if index > 0:
        for item in list(keys):
            if item and not item.endswith(f"_{index:02d}"):
                keys.add(f"{item}_{index:02d}")
    return {item for item in keys if item}


def dialogue_video_slot(dialogue: dict[str, Any], *, sc: Any) -> dict[str, Any]:
    working_assets = dialogue.get("working_assets") if isinstance(dialogue.get("working_assets"), dict) else {}
    video = working_assets.get("video") if isinstance(working_assets.get("video"), dict) else {}
    if sc.text(video.get("path")):
        return video
    legacy_assets = dialogue.get("assets") if isinstance(dialogue.get("assets"), dict) else {}
    legacy_video = legacy_assets.get("video") if isinstance(legacy_assets.get("video"), dict) else {}
    return legacy_video


def video_plan_final_bound(storyboard: dict[str, Any], final_rel: str, asset_key: str = "", *, sc: Any) -> bool:
    target_rel = sc.text(final_rel)
    if not target_rel:
        return False
    for shot in storyboard.get("shots") if isinstance(storyboard.get("shots"), list) else []:
        for scene in shot.get("scenes") if isinstance(shot.get("scenes"), list) else []:
            for index, dialogue in enumerate(scene_dialogues(scene), start=1):
                video = dialogue_video_slot(dialogue, sc=sc)
                if sc.text(video.get("path")) == target_rel:
                    return True
                if asset_key and asset_key in dialogue_match_keys(dialogue, index, sc=sc) and sc.text(video.get("path")) == target_rel:
                    return True
    return False


def storyboard_dialogue_for_asset(storyboard: dict[str, Any], asset_key: str, *, sc: Any) -> dict[str, Any]:
    target_asset_key = sc.text(asset_key)
    if not target_asset_key:
        return {}
    for shot in storyboard.get("shots") if isinstance(storyboard.get("shots"), list) else []:
        for scene in shot.get("scenes") if isinstance(shot.get("scenes"), list) else []:
            for dialogue in scene_dialogues(scene):
                if target_asset_key == sc.text(dialogue.get("dialogue_asset_key")):
                    return dialogue
    for shot in storyboard.get("shots") if isinstance(storyboard.get("shots"), list) else []:
        for scene in shot.get("scenes") if isinstance(shot.get("scenes"), list) else []:
            for index, dialogue in enumerate(scene_dialogues(scene), start=1):
                if target_asset_key in dialogue_match_keys(dialogue, index, sc=sc):
                    return dialogue
    return {}


def dialogue_slot_path(dialogue: dict[str, Any], group: str, slot: str, *, sc: Any) -> str:
    working_assets = dialogue.get("working_assets") if isinstance(dialogue.get("working_assets"), dict) else {}
    if group == "audio":
        audio = working_assets.get("audio") if isinstance(working_assets.get("audio"), dict) else {}
        return sc.text(audio.get("path"))
    if group == "video":
        video = working_assets.get("video") if isinstance(working_assets.get("video"), dict) else {}
        return sc.text(video.get("path"))
    if group == "image":
        images = working_assets.get("images") if isinstance(working_assets.get("images"), list) else []
        for image in images:
            if isinstance(image, dict) and sc.text(image.get("slot")) == slot:
                return sc.text(image.get("path"))
        return sc.text(dialogue.get("bound_image_path")) if slot == "Image_New" else ""
    return ""


def dialogue_source_image_path(dialogue: dict[str, Any], *, sc: Any) -> str:
    source_paths = dialogue.get("source_image_paths") if isinstance(dialogue.get("source_image_paths"), list) else []
    if source_paths:
        return sc.text(source_paths[0])
    return sc.text(dialogue.get("image_path"))


def existing_working_status_for_keys(workspace: Path, keys: list[str], slot: str, exts: set[str], preferred_suffix: str, *, sc: Any) -> dict[str, Any]:
    for key in keys:
        rel_path = sc.existing_working_slot_path(workspace, key, slot, exts, preferred_suffix, sc=sc)
        status = video_plan_file_status(workspace, rel_path, sc=sc)
        if status.get("in_working_exists"):
            return status
    return {"path": "", "exists": False, "in_working": False, "in_working_exists": False}


def first_done_status(workspace: Path, paths: list[str], *, sc: Any) -> dict[str, Any]:
    first_status = {"path": "", "exists": False, "in_working": False, "in_working_exists": False}
    for path in paths:
        status = video_plan_file_status(workspace, path, sc=sc)
        if not first_status["path"] and status.get("path"):
            first_status = status
        if status.get("in_working_exists"):
            return status
    return first_status


def video_plan_asset_key_from_path(path_value: str, *, sc: Any) -> str:
    stem = Path(sc.text(path_value)).stem
    for suffix in (
        "_Video_Final",
        "_Video_Raw",
        "_Video_AudioSynced",
        "_Video_LipSync",
        "_SegmentAudio_Final",
        "_Audio_Final",
        "_Image_New",
        "_TailFrame",
        "_VideoPrompt",
        "_ImagePrompt",
    ):
        if stem.endswith(suffix):
            return stem[: -len(suffix)]
    return ""


def video_plan_segment_asset_key(segment: dict[str, Any], outputs: dict[str, Any], *, sc: Any) -> str:
    explicit = sc.text(segment.get("asset_key"))
    if explicit:
        return explicit
    dialogue_asset_keys = segment.get("dialogue_asset_keys") if isinstance(segment.get("dialogue_asset_keys"), list) else segment.get("dialogue_ids")
    for item in dialogue_asset_keys if isinstance(dialogue_asset_keys, list) else []:
        value = sc.text(item)
        if value:
            return value
    for key in ("raw_video_path", "final_video_path", "video_path", "segment_audio_path", "image_path", "video_prompt_path", "image_prompt_path"):
        value = video_plan_asset_key_from_path(sc.text(outputs.get(key)), sc=sc)
        if value:
            return value
    return ""


def video_plan_artifact_status(workspace: Path, plan_payload: dict[str, Any], *, sc: Any) -> dict[str, Any]:
    def done(status: dict[str, Any]) -> bool:
        return bool(status.get("in_working_exists"))

    def new_image_done(status: dict[str, Any]) -> bool:
        path = sc.text(status.get("path"))
        return done(status) and bool(re.search(r"_Image_New\.[^.\/]+$", path))

    def copy_pending(source: dict[str, Any], target: dict[str, Any], required: bool) -> bool:
        return bool(required and not done(target) and bool(source.get("exists")))

    edit_storyboard = sc.read_json(workspace / EDIT_REL)
    storyboard = edit_storyboard if edit_storyboard.get("schema_version") == "koubo_storyboard_edit_0.1" else sc.read_json(workspace / SOURCE_REL)
    execution_result = sc.read_json(workspace / VIDEO_PLAN_EXECUTION_RESULT_REL)
    segments: dict[str, Any] = {}
    for shot in plan_payload.get("shots") or []:
        if not isinstance(shot, dict):
            continue
        for scene in shot.get("scenes") or []:
            if not isinstance(scene, dict):
                continue
            for segment in scene.get("segments") or []:
                if not isinstance(segment, dict):
                    continue
                segment_id = sc.text(segment.get("segment_id"))
                if not segment_id:
                    continue
                outputs = segment.get("planned_outputs") if isinstance(segment.get("planned_outputs"), dict) else {}
                first_frame = segment.get("first_frame") if isinstance(segment.get("first_frame"), dict) else {}
                tail_frame = segment.get("tail_frame") if isinstance(segment.get("tail_frame"), dict) else {}
                existing_video = segment.get("existing_video") if isinstance(segment.get("existing_video"), dict) else {}
                materialize_video = existing_video.get("materialize_video") if isinstance(existing_video.get("materialize_video"), dict) else {}
                materialize_frame = first_frame.get("materialize_first_frame") if isinstance(first_frame.get("materialize_first_frame"), dict) else {}
                audio_files = []
                for audio_task in segment.get("dialogue_audio_tasks") or []:
                    if isinstance(audio_task, dict):
                        audio_files.append(video_plan_file_status(workspace, sc.text(audio_task.get("existing_audio_path") or audio_task.get("planned_audio_path")), sc=sc))
                tasks = segment.get("tasks") if isinstance(segment.get("tasks"), dict) else {}
                asset_key = video_plan_segment_asset_key(segment, outputs, sc=sc)
                storyboard_dict = storyboard if isinstance(storyboard, dict) else {}
                dialogue = storyboard_dialogue_for_asset(storyboard_dict, asset_key, sc=sc)
                actual_asset_key = sc.text(dialogue.get("dialogue_asset_key")) or asset_key
                asset_keys = [actual_asset_key]
                if asset_key not in asset_keys:
                    asset_keys.append(asset_key)
                segment_audio = first_done_status(workspace, [
                    dialogue_slot_path(dialogue, "audio", "Audio_Final", sc=sc),
                    sc.text(outputs.get("segment_audio_path")),
                ], sc=sc)
                image_prompt = video_plan_file_status(workspace, sc.text(outputs.get("image_prompt_path")) or f"{WORKING_REL}/{asset_key}_ImagePrompt.json", sc=sc)
                image = first_done_status(workspace, [
                    dialogue_slot_path(dialogue, "image", "Image_New", sc=sc),
                    sc.text(outputs.get("image_path") or first_frame.get("planned_generated_image_path")),
                ], sc=sc)
                image_source = first_done_status(workspace, [
                    dialogue_source_image_path(dialogue, sc=sc),
                    sc.text(first_frame.get("source_path")),
                ], sc=sc)
                image_copy_target = video_plan_file_status(workspace, sc.text(materialize_frame.get("copy_to_path")), sc=sc)
                image_source_available = bool(image_source.get("exists")) or sc.text(first_frame.get("source_type")) == "existing_image_prompt"
                standard_image = existing_working_status_for_keys(workspace, asset_keys, "Image_New", IMAGE_EXTS, ".png", sc=sc)
                if not new_image_done(image) and new_image_done(standard_image):
                    image = standard_image
                raw_video_path = sc.text(outputs.get("raw_video_path")) or f"{WORKING_REL}/{asset_key}_Video_Raw.mp4"
                final_video_path = sc.text(outputs.get("final_video_path") or outputs.get("video_path")) or f"{WORKING_REL}/{asset_key}_Video_Final.mp4"
                video_prompt = video_plan_file_status(workspace, sc.text(outputs.get("video_prompt_path")) or f"{WORKING_REL}/{asset_key}_VideoPrompt.json", sc=sc)
                raw_slot = existing_working_status_for_keys(workspace, asset_keys, "Video_Raw", VIDEO_EXTS, ".mp4", sc=sc)
                video = raw_slot if raw_slot.get("in_working_exists") else video_plan_file_status(workspace, raw_video_path, sc=sc)
                final_slot = existing_working_status_for_keys(workspace, asset_keys, "Video_Final", VIDEO_EXTS, ".mp4", sc=sc)
                final_video = first_done_status(workspace, [
                    dialogue_slot_path(dialogue, "video", "Video_Final", sc=sc),
                    sc.text(final_slot.get("path")),
                    final_video_path,
                ], sc=sc)
                video_source = first_done_status(workspace, [
                    sc.text(existing_video.get("path") or materialize_video.get("copy_from_path") or first_frame.get("source_path")),
                    dialogue_slot_path(dialogue, "video", "Video_Final", sc=sc),
                ], sc=sc)
                video_copy_target = video_plan_file_status(workspace, sc.text(materialize_video.get("copy_to_path")), sc=sc)
                tail = video_plan_file_status(workspace, sc.text(tail_frame.get("planned_path")), sc=sc)
                audio_in_working = all(done(item) for item in audio_files) if audio_files else done(segment_audio)
                image_in_working = new_image_done(image) or new_image_done(image_copy_target) or new_image_done(standard_image)
                video_in_working = done(video)
                image_copy_pending = copy_pending(image_source, image_copy_target, bool(materialize_frame.get("required")))
                image_source_type = sc.text(materialize_frame.get("source_type") or first_frame.get("source_type"))
                image_input_kind = "tail_frame_materialized" if image_in_working and image_source_type in {"tail_frame_materialized", "previous_segment_tail_frame", "previous_scene_tail_frame"} else (
                    "tail_frame_pending_copy" if image_copy_pending and image_source_type in {"previous_segment_tail_frame", "previous_scene_tail_frame"} else "new_image"
                )
                video_copy_required = bool(tasks.get("need_video") and materialize_video.get("required") and sc.text(materialize_video.get("copy_to_path")) == raw_video_path)
                video_copy_pending = copy_pending(video_source, video_copy_target, video_copy_required)
                audio_copy_pending = not audio_in_working and any(bool(item.get("exists")) and not done(item) for item in audio_files)
                image_generate_pending = not image_in_working and not image_copy_pending and bool(
                    tasks.get("need_image")
                    or tasks.get("need_image_prompt")
                    or first_frame.get("source_type") in {"previous_segment_tail_frame", "previous_scene_tail_frame"}
                )
                video_generate_pending = not video_in_working and not video_copy_pending and bool(tasks.get("need_video") or tasks.get("need_video_prompt"))
                sync_required = bool(tasks.get("need_sync") or tasks.get("need_lipsync") or tasks.get("need_audio_video_sync"))
                final_bound = bool(done(final_video) and video_plan_final_bound(storyboard_dict, sc.text(final_video.get("path")), asset_key, sc=sc))
                final_file_exists = done(final_video)
                sync_in_working = final_file_exists
                slot_states = derive_video_plan_slot_states(SlotInputs(
                    audio_exists=audio_in_working,
                    source_exists=image_source_available,
                    image_exists=image_in_working,
                    raw_exists=video_in_working,
                    final_exists=final_file_exists,
                    image_prompt_exists=done(image_prompt),
                    video_prompt_exists=done(video_prompt),
                    final_bound=final_bound,
                    binding_missing=final_file_exists and not final_bound,
                ))
                segments[segment_id] = {
                    "audio_files": audio_files,
                    "segment_audio": segment_audio,
                    "image_prompt": image_prompt,
                    "image": image,
                    "image_source": image_source,
                    "image_copy_target": image_copy_target,
                    "video_prompt": video_prompt,
                    "video": video,
                    "final_video": final_video,
                    "video_source": video_source,
                    "video_copy_target": video_copy_target,
                    "tail": tail,
                    "audio_in_working": audio_in_working,
                    "audio_copy_pending": audio_copy_pending,
                    "audio_generate_pending": not audio_in_working and bool(tasks.get("need_audio")),
                    "image_in_working": image_in_working,
                    "image_copy_pending": image_copy_pending,
                    "image_source_type": image_source_type,
                    "image_input_kind": image_input_kind,
                    "image_generate_pending": image_generate_pending,
                    "video_in_working": video_in_working,
                    "video_copy_pending": video_copy_pending,
                    "video_generate_pending": video_generate_pending,
                    "final_bound": final_bound,
                    "sync_in_working": sync_in_working,
                    "sync_generate_pending": not sync_in_working and sync_required,
                    "slot_states": slot_states,
                }
    return {
        "segments": segments,
        "execution_result": sc.redact_payload(execution_result),
        "execution_result_path": VIDEO_PLAN_EXECUTION_RESULT_REL if execution_result else "",
    }


def register_video_plan_artifact_services(ns: Any) -> None:
    for name in SERVICE_EXPORTS:
        setattr(ns, name, globals()[name])
