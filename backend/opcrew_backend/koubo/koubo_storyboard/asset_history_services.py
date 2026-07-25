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
from .runtime import analysis_tool_env, resolve_media_binary
from .text_utils import redact_payload, redact_secret_text


SERVICE_EXPORTS = (
    "history_item_for",
    "move_working_file_to_history",
    "delete_working_file_if_unreferenced",
    "generated_asset_refs",
    "dialogue_identity",
    "dialogue_video_path",
    "dialogue_map",
    "removed_video_slot_refs",
    "video_asset_key_from_path",
    "asset_key_from_working_path",
    "related_video_working_paths",
    "related_image_working_paths",
    "normalize_video_target_kind",
    "archive_current_standard_slot",
    "bind_video_asset_to_standard_slot",
    "bind_file_asset_to_standard_slot",
    "materialize_standard_slot_path",
    "clear_video_only_execution_steps",
    "archive_related_video_outputs",
    "archive_related_image_outputs",
    "archive_removed_generated_assets",
    "find_dialogue",
    "coerce_edit_plan",
    "bind_asset_to_plan",
    "clear_asset_from_plan",
    "materialize_plan_assets",
    "history_versions",
)


def history_item_for(original_rel: str, history_rel: str, reason: str = "remove_slot_asset", dialogue: Optional[dict[str, Any]] = None, scene: Optional[dict[str, Any]] = None, *, sc: Any) -> dict[str, Any]:
    asset_key = Path(original_rel).stem
    for marker in ("_Audio_", "_Image_", "_Video_"):
        if marker in asset_key:
            asset_key = asset_key.split(marker, 1)[0]
            break
    shot_id, scene_id = sc.asset_key_parts(asset_key)
    source_scene_id = sc.text(scene.get("scene_id")) if isinstance(scene, dict) else scene_id
    source_dialogue_id = sc.text(dialogue.get("dialogue_id")) if isinstance(dialogue, dict) else ""
    source_srt_id = sc.text(dialogue.get("srt_id")) if isinstance(dialogue, dict) else ""
    return {
        "original_path": original_rel,
        "history_path": history_rel,
        "asset_type": sc.asset_type_for_path(original_rel),
        "slot": sc.slot_for_path(original_rel),
        "asset_key": asset_key,
        "shot_id": shot_id,
        "scene_id": source_scene_id or scene_id,
        "source_dialogue_id": source_dialogue_id,
        "source_srt_id": source_srt_id,
        "source_scene_id": source_scene_id,
        "reason": reason,
    }


def move_working_file_to_history(workspace: Path, rel_path: str, reason: str, dialogue: Optional[dict[str, Any]] = None, scene: Optional[dict[str, Any]] = None, *, sc: Any) -> tuple[bool, str]:
    rel_path = sc.text(rel_path)
    if not rel_path.startswith(f"{WORKING_REL}/"):
        return False, ""
    _, path = sc.safe_workspace_rel(workspace, rel_path)
    if not path.exists() or not path.is_file():
        return False, ""
    batch = f"batch_{now_ms()}_{sc.safe_name(reason, 'remove_slot_asset')}"
    batch_rel = f"{ASSET_HISTORY_REL}/{batch}"
    target_rel = f"{batch_rel}/{Path(rel_path).name}"
    _, target = sc.safe_workspace_rel(workspace, target_rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target = target.parent / f"{target.stem}_{now_ms()}{target.suffix}"
        target_rel = target.relative_to(workspace).as_posix()
    shutil.move(str(path), str(target))
    item = history_item_for(rel_path, target_rel, reason, dialogue, scene, sc=sc)
    manifest_path = workspace / batch_rel / "manifest.json"
    manifest = sc.read_json(manifest_path)
    items = manifest.get("items") if isinstance(manifest.get("items"), list) else []
    if not manifest:
        manifest = {
            "schema_version": "storyboard_asset_history_0.1",
            "batch_id": batch,
            "reason": reason,
            "created_at": now_ms(),
            "items": [],
        }
    if not any(isinstance(existing, dict) and sc.text(existing.get("history_path")) == target_rel for existing in items):
        items.append(item)
    manifest["items"] = items
    manifest["updated_at"] = now_ms()
    sc.write_json(manifest_path, manifest)
    return True, target_rel


def delete_working_file_if_unreferenced(workspace: Path, rel_path: str, plan: dict[str, Any], reason: str = "remove_slot_asset", dialogue: Optional[dict[str, Any]] = None, scene: Optional[dict[str, Any]] = None, *, sc: Any) -> bool:
    rel_path = sc.text(rel_path)
    if not rel_path.startswith(f"{WORKING_REL}/") or sc.plan_references_path(plan, rel_path, sc=sc):
        return False
    moved, _history_rel = move_working_file_to_history(workspace, rel_path, reason, dialogue, scene, sc=sc)
    return moved


def generated_asset_refs(plan: dict[str, Any], *, sc: Any) -> dict[str, dict[str, Any]]:
    refs: dict[str, dict[str, Any]] = {}
    for shot in plan.get("shots") or []:
        for scene in shot.get("scenes") or []:
            for dialogue in scene.get("dialogues") or []:
                dialogue_assets = sc.ensure_dialogue_working_assets(dialogue, sc=sc)
                for slot in [dialogue_assets.get("audio"), dialogue_assets.get("video"), *(dialogue_assets.get("images") or [])]:
                    if not isinstance(slot, dict):
                        continue
                    rel_path = sc.text(slot.get("path"))
                    if rel_path.startswith(f"{WORKING_REL}/") and sc.asset_source_type(rel_path, slot.get("source_type"), sc=sc) == "generated":
                        refs[rel_path] = {"dialogue": dialogue, "scene": scene}
    return refs


def dialogue_identity(dialogue: dict[str, Any], *, sc: Any) -> str:
    if not isinstance(dialogue, dict):
        return ""
    for key in ("dialogue_id", "dialogue_asset_key", "srt_id"):
        value = sc.text(dialogue.get(key))
        if value:
            return value
    srt_ids = dialogue.get("srt_ids") if isinstance(dialogue.get("srt_ids"), list) else []
    return sc.text(srt_ids[0]) if srt_ids else ""


def dialogue_video_path(dialogue: dict[str, Any], *, sc: Any) -> str:
    if not isinstance(dialogue, dict):
        return ""
    dialogue_assets = sc.ensure_dialogue_working_assets(dialogue, sc=sc)
    video = dialogue_assets.get("video") if isinstance(dialogue_assets.get("video"), dict) else {}
    return sc.current_slot_path(video, sc=sc)


def dialogue_map(plan: dict[str, Any], *, sc: Any) -> dict[str, dict[str, Any]]:
    items: dict[str, dict[str, Any]] = {}
    for shot in plan.get("shots") or []:
        for scene in shot.get("scenes") or []:
            for dialogue in scene.get("dialogues") or []:
                identity = dialogue_identity(dialogue, sc=sc)
                if identity:
                    items[identity] = dialogue
    return items


def removed_video_slot_refs(previous_plan: dict[str, Any], next_plan: dict[str, Any], *, sc: Any) -> list[dict[str, Any]]:
    next_dialogues = dialogue_map(next_plan, sc=sc) if isinstance(next_plan, dict) else {}
    refs: list[dict[str, Any]] = []
    for shot in previous_plan.get("shots") or []:
        for scene in shot.get("scenes") or []:
            for dialogue in scene.get("dialogues") or []:
                previous_path = dialogue_video_path(dialogue, sc=sc)
                if not previous_path:
                    continue
                next_dialogue = next_dialogues.get(dialogue_identity(dialogue, sc=sc))
                next_path = dialogue_video_path(next_dialogue, sc=sc) if isinstance(next_dialogue, dict) else ""
                if previous_path != next_path:
                    refs.append({"path": previous_path, "asset_key": sc.dialogue_asset_key(dialogue, sc=sc), "dialogue": dialogue, "scene": scene})
    return refs


def video_asset_key_from_path(rel_path: str, *, sc: Any) -> str:
    stem = Path(sc.text(rel_path)).stem
    if "_Video_" not in stem:
        return ""
    return stem.split("_Video_", 1)[0]


def asset_key_from_working_path(rel_path: str, *, sc: Any) -> str:
    stem = Path(sc.text(rel_path)).stem
    for marker in ("_Audio_", "_Image_", "_Video_"):
        if marker in stem:
            return stem.split(marker, 1)[0]
    return ""


def related_video_working_paths(asset_key: str, original_rel: str = "", *, sc: Any) -> list[str]:
    asset_key = sc.text(asset_key)
    if not asset_key:
        return []
    paths = [sc.text(original_rel)] if sc.text(original_rel) else []
    for suffix in sorted(VIDEO_EXTS):
        paths.append(f"{WORKING_REL}/{asset_key}_Video_Final{suffix}")
        paths.append(f"{WORKING_REL}/{asset_key}_Video_Raw{suffix}")
    paths.append(f"{WORKING_REL}/{asset_key}_TailFrame.jpg")
    paths.append(f"{WORKING_REL}/{asset_key}_TailFrame.png")
    seen: set[str] = set()
    return [item for item in paths if item and not (item in seen or seen.add(item))]


def related_image_working_paths(asset_key: str, slot: str = "Image_New", original_rel: str = "", *, sc: Any) -> list[str]:
    asset_key = sc.text(asset_key)
    if not asset_key:
        return []
    paths = [sc.text(original_rel)] if sc.text(original_rel) else []
    for suffix in sorted(IMAGE_EXTS):
        paths.append(sc.working_slot_path(asset_key, slot, suffix, sc=sc))
    seen: set[str] = set()
    return [item for item in paths if item and not (item in seen or seen.add(item))]


def normalize_video_target_kind(target_kind: str, *, sc: Any) -> str:
    value = sc.text(target_kind)
    if value == "video":
        return "final_video"
    return value


def media_video_dimensions(path: Path) -> tuple[int, int]:
    if not path.exists():
        return 0, 0
    command = [
        resolve_media_binary("ffprobe"),
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "json",
        str(path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        return 0, 0
    try:
        payload = json.loads(completed.stdout or "{}")
        stream = (payload.get("streams") or [{}])[0]
        return int(stream.get("width") or 0), int(stream.get("height") or 0)
    except Exception:
        return 0, 0


def media_image_dimensions(path: Path) -> tuple[int, int]:
    try:
        from PIL import Image
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Pillow is required for raw video aspect validation") from exc
    try:
        with Image.open(path) as image:
            return int(image.width), int(image.height)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Unable to inspect first-frame image dimensions: {path.name}") from exc


def first_frame_image_rel_for_dialogue(dialogue: dict[str, Any], dialogue_assets: dict[str, Any], *, sc: Any) -> str:
    images = dialogue_assets.get("images") if isinstance(dialogue_assets.get("images"), list) else []
    if images and isinstance(images[0], dict):
        rel = sc.text(images[0].get("path"))
        if rel:
            return rel
    return sc.text(dialogue.get("bound_image_path"))


def validate_raw_video_matches_first_frame(workspace: Path, video_rel: str, first_frame_rel: str, *, sc: Any) -> None:
    video_rel = sc.text(video_rel)
    first_frame_rel = sc.text(first_frame_rel)
    if not video_rel or not first_frame_rel:
        return
    video_path = workspace / video_rel
    first_frame_path = workspace / first_frame_rel
    if not video_path.exists() or not first_frame_path.exists():
        return
    video_width, video_height = media_video_dimensions(video_path)
    image_width, image_height = media_image_dimensions(first_frame_path)
    if video_width <= 0 or video_height <= 0 or image_width <= 0 or image_height <= 0:
        raise HTTPException(status_code=400, detail={
            "message": "Unable to inspect raw video or first-frame dimensions before binding.",
            "video": video_rel,
            "first_frame": first_frame_rel,
        })
    video_ratio = video_width / video_height
    image_ratio = image_width / image_height
    if abs(video_ratio - image_ratio) > 0.04:
        raise HTTPException(status_code=400, detail={
            "message": "Raw video aspect does not match the selected first-frame image.",
            "video": video_rel,
            "video_width": video_width,
            "video_height": video_height,
            "first_frame": first_frame_rel,
            "first_frame_width": image_width,
            "first_frame_height": image_height,
        })


def archive_current_standard_slot(workspace: Path, asset_key: str, slot: str, reason: str, dialogue: Optional[dict[str, Any]] = None, scene: Optional[dict[str, Any]] = None, *, sc: Any) -> tuple[bool, str]:
    current_rel = sc.existing_working_slot_path(workspace, asset_key, slot, VIDEO_EXTS, sc=sc)
    if not current_rel:
        return False, ""
    return move_working_file_to_history(workspace, current_rel, reason, dialogue, scene, sc=sc)


def bind_video_asset_to_standard_slot(workspace: Path, source_rel: str, asset_key: str, slot: str, reason: str, dialogue: Optional[dict[str, Any]] = None, scene: Optional[dict[str, Any]] = None, *, sc: Any) -> str:
    source_rel = sc.text(source_rel)
    current_rel = sc.existing_working_slot_path(workspace, asset_key, slot, VIDEO_EXTS, sc=sc)
    if current_rel and current_rel != source_rel:
        move_working_file_to_history(workspace, current_rel, reason, dialogue, scene, sc=sc)
    return sc.copy_to_standard_working_slot(workspace, source_rel, asset_key, slot, VIDEO_EXTS, sc=sc)


def bind_file_asset_to_standard_slot(workspace: Path, source_rel: str, asset_key: str, slot: str, allowed_exts: set[str], reason: str, dialogue: Optional[dict[str, Any]] = None, scene: Optional[dict[str, Any]] = None, *, sc: Any) -> str:
    source_rel = sc.text(source_rel)
    current_rel = sc.existing_working_slot_path(workspace, asset_key, slot, allowed_exts, sc=sc)
    if current_rel and current_rel != source_rel:
        move_working_file_to_history(workspace, current_rel, reason, dialogue, scene, sc=sc)
    return sc.copy_to_standard_working_slot(workspace, source_rel, asset_key, slot, allowed_exts, sc=sc)


def materialize_standard_slot_path(workspace: Path, rel_path: str, asset_key: str, slot: str, allowed_exts: set[str], source_type_hint: Any = "", reason: str = "replace_slot_asset", dialogue: Optional[dict[str, Any]] = None, scene: Optional[dict[str, Any]] = None, *, sc: Any) -> tuple[str, str]:
    rel_path = sc.text(rel_path)
    if not rel_path:
        return "", ""
    source_type = sc.asset_source_type(rel_path, source_type_hint, sc=sc)
    suffix = Path(rel_path).suffix.lower() or ".mp4"
    expected_rel = sc.working_slot_path(asset_key, slot, suffix, sc=sc)
    if rel_path == expected_rel:
        if source_type == "generated":
            recovered = sc.recover_missing_working_asset(workspace, rel_path, asset_key, slot, sc=sc)
            return recovered or rel_path, "generated"
        return rel_path, source_type
    current_rel = sc.existing_working_slot_path(workspace, asset_key, slot, allowed_exts, sc=sc)
    if current_rel and current_rel != rel_path:
        move_working_file_to_history(workspace, current_rel, reason, dialogue, scene, sc=sc)
    copied = sc.copy_to_standard_working_slot(workspace, rel_path, asset_key, slot, allowed_exts, sc=sc)
    if copied:
        return copied, "generated"
    if source_type == "generated":
        recovered = sc.recover_missing_working_asset(workspace, rel_path, asset_key, slot, sc=sc)
        if recovered:
            return recovered, "generated"
    return rel_path, source_type


def clear_video_only_execution_steps(workspace: Path, asset_key: str, *, sc: Any) -> bool:
    asset_key = sc.text(asset_key)
    if not asset_key:
        return False
    state_path = workspace / VIDEO_ONLY_PLAN_EXECUTION_STATE_REL
    state = sc.read_json(state_path)
    if not isinstance(state, dict) or not state:
        return False
    if sc.text(state.get("status")) in {"queued", "running"}:
        return False
    segments = state.get("segments") if isinstance(state.get("segments"), dict) else {}
    segment = segments.get(asset_key) if isinstance(segments.get(asset_key), dict) else None
    if not segment:
        return False
    steps = segment.get("steps") if isinstance(segment.get("steps"), dict) else {}
    changed = False
    for step in ("video", "confirm_final"):
        if step in steps:
            steps.pop(step, None)
            changed = True
    if sc.text(state.get("current_asset_key")) == asset_key and sc.text(state.get("current_step")) in {"video", "confirm_final"}:
        state["current_step"] = ""
        state["current_step_status"] = ""
        changed = True
    if changed:
        state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        sc.write_json(state_path, state)
    return changed


def archive_related_video_outputs(workspace: Path, rel_path: str, plan: dict[str, Any], reason: str, dialogue: Optional[dict[str, Any]] = None, scene: Optional[dict[str, Any]] = None, asset_key_override: str = "", *, sc: Any) -> list[dict[str, str]]:
    asset_key = sc.text(asset_key_override) or video_asset_key_from_path(rel_path, sc=sc)
    if not asset_key:
        return []
    archived: list[dict[str, str]] = []
    for candidate in related_video_working_paths(asset_key, rel_path, sc=sc):
        if not candidate.startswith(f"{WORKING_REL}/") or sc.plan_references_path(plan, candidate, sc=sc):
            continue
        moved, history_rel = move_working_file_to_history(workspace, candidate, reason, dialogue, scene, sc=sc)
        if moved:
            archived.append({"original_path": candidate, "history_path": history_rel})
    clear_video_only_execution_steps(workspace, asset_key, sc=sc)
    return archived


def archive_related_image_outputs(workspace: Path, asset_key: str, rel_path: str, plan: dict[str, Any], reason: str, dialogue: Optional[dict[str, Any]] = None, scene: Optional[dict[str, Any]] = None, slot: str = "Image_New", *, sc: Any) -> list[dict[str, str]]:
    asset_key = sc.text(asset_key)
    if not asset_key:
        return []
    archived: list[dict[str, str]] = []
    for candidate in related_image_working_paths(asset_key, slot, rel_path, sc=sc):
        if not candidate.startswith(f"{WORKING_REL}/") or sc.plan_references_path(plan, candidate, sc=sc):
            continue
        moved, history_rel = move_working_file_to_history(workspace, candidate, reason, dialogue, scene, sc=sc)
        if moved:
            archived.append({"original_path": candidate, "history_path": history_rel})
    return archived


def archive_removed_generated_assets(workspace: Path, previous_plan: dict[str, Any], next_plan: dict[str, Any], reason: str, *, sc: Any) -> list[dict[str, str]]:
    previous_refs = generated_asset_refs(previous_plan, sc=sc) if isinstance(previous_plan, dict) else {}
    next_paths = set(generated_asset_refs(next_plan, sc=sc).keys()) if isinstance(next_plan, dict) else set()
    archived: list[dict[str, str]] = []
    cleaned_video_asset_keys: set[str] = set()
    for info in removed_video_slot_refs(previous_plan, next_plan, sc=sc) if isinstance(previous_plan, dict) else []:
        asset_key = sc.text(info.get("asset_key"))
        if not asset_key or asset_key in cleaned_video_asset_keys:
            continue
        archived.extend(archive_related_video_outputs(workspace, sc.text(info.get("path")), next_plan, reason, info.get("dialogue"), info.get("scene"), asset_key, sc=sc))
        cleaned_video_asset_keys.add(asset_key)
    for rel_path, info in previous_refs.items():
        if rel_path in next_paths:
            continue
        asset_key = video_asset_key_from_path(rel_path, sc=sc) or asset_key_from_working_path(rel_path, sc=sc) or sc.dialogue_asset_key(info.get("dialogue") or {}, sc=sc)
        if asset_key and asset_key in cleaned_video_asset_keys:
            continue
        if sc.slot_for_path(rel_path) == "Video_Final":
            archived.extend(archive_related_video_outputs(workspace, rel_path, next_plan, reason, info.get("dialogue"), info.get("scene"), sc=sc))
        elif sc.slot_for_path(rel_path) == "Image_New":
            archived.extend(archive_related_image_outputs(workspace, asset_key, rel_path, next_plan, reason, info.get("dialogue"), info.get("scene"), sc=sc))
        else:
            moved, history_rel = move_working_file_to_history(workspace, rel_path, reason, info.get("dialogue"), info.get("scene"), sc=sc)
            if moved:
                archived.append({"original_path": rel_path, "history_path": history_rel})
    return archived


def find_dialogue(plan: dict[str, Any], dialogue_id: str, *, sc: Any) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    for shot in plan.get("shots") or []:
        for scene in shot.get("scenes") or []:
            for dialogue in scene.get("dialogues") or []:
                if sc.text(dialogue.get("dialogue_id")) == dialogue_id:
                    return shot, scene, dialogue
    raise HTTPException(status_code=404, detail="Dialogue not found in StoryBoard plan")


def coerce_edit_plan(task: dict[str, Any], workspace: Path, raw_plan: Any, regroup: bool = False, *, sc: Any) -> tuple[dict[str, Any], Optional[dict[str, Any]]]:
    if isinstance(raw_plan, dict) and isinstance(raw_plan.get("shots"), list):
        plan = raw_plan
    else:
        source = sc.read_json(workspace / SOURCE_REL)
        edit = sc.read_json(workspace / EDIT_REL)
        plan = edit if edit.get("schema_version") == "koubo_storyboard_edit_0.1" else sc.normalize_source_plan(task, source, sc=sc)
    regroup_backup = None
    plan = sc.recalculate({
        **plan,
        "schema_version": "koubo_storyboard_edit_0.1",
        "title": "故事版（口播）",
        "source_type": "analysis_v1_storyboard",
        "analysis_task_id": int(task["id"]),
        "analysis_session_id": int(task["session_id"]),
        "source_path": SOURCE_REL,
    }, sc=sc)
    if regroup_backup:
        plan["last_regroup_working_backup"] = regroup_backup
    return plan, regroup_backup


def bind_asset_to_plan(workspace: Path, plan: dict[str, Any], dialogue_id: str, source_rel: str, target_kind: str, *, sc: Any) -> dict[str, Any]:
    target_kind = normalize_video_target_kind(target_kind, sc=sc)
    if target_kind == "source":
        required = "Image"
    elif target_kind == "image":
        required = "Image"
    elif target_kind == "audio":
        required = "Audio"
    elif target_kind in {"raw_video", "final_video"}:
        required = "Video"
    else:
        required = sc.asset_type_for_path(source_rel)
        if required == "Video":
            raise HTTPException(status_code=400, detail="Video asset target must be raw_video or final_video.")
        target_kind = required.lower()
    if sc.asset_type_for_path(source_rel) != required:
        raise HTTPException(status_code=400, detail=f"Asset type does not match target slot: {required}")
    _shot, scene, dialogue = find_dialogue(plan, dialogue_id, sc=sc)
    dialogue_assets = sc.ensure_dialogue_working_assets(dialogue, sc=sc)
    asset_key = sc.dialogue_asset_key(dialogue, sc=sc)
    if not asset_key:
        raise HTTPException(status_code=400, detail="Dialogue asset key is missing")
    if target_kind == "raw_video":
        validate_raw_video_matches_first_frame(
            workspace,
            source_rel,
            first_frame_image_rel_for_dialogue(dialogue, dialogue_assets, sc=sc),
            sc=sc,
        )
        bound_path = bind_video_asset_to_standard_slot(workspace, source_rel, asset_key, "Video_Raw", "replace_raw_video_slot_asset", dialogue, scene, sc=sc)
        if not bound_path:
            raise HTTPException(status_code=400, detail="Could not copy Raw video to Working.")
        return plan
    if target_kind == "final_video":
        bound_path = bind_video_asset_to_standard_slot(workspace, source_rel, asset_key, "Video_Final", "replace_final_video_slot_asset", dialogue, scene, sc=sc)
        if not bound_path:
            raise HTTPException(status_code=400, detail="Could not copy Final video to Working.")
        old_path = sc.current_slot_path(dialogue_assets["video"], sc=sc)
        dialogue_assets["video"] = {"slot": "Video_Final", "source_type": "generated", "path": bound_path}
        if old_path and old_path != bound_path:
            delete_working_file_if_unreferenced(workspace, old_path, plan, "replace_slot_asset", dialogue, scene, sc=sc)
        return plan
    old_path = ""
    if target_kind == "audio":
        bound_path = bind_file_asset_to_standard_slot(workspace, source_rel, asset_key, "Audio_Final", AUDIO_EXTS, "replace_audio_slot_asset", dialogue, scene, sc=sc)
        if not bound_path:
            raise HTTPException(status_code=400, detail="Could not copy Audio to Working.")
        old_path = sc.current_slot_path(dialogue_assets["audio"], sc=sc)
        dialogue_assets["audio"] = {"slot": "Audio_Final", "source_type": sc.asset_source_type(source_rel, sc=sc), "path": bound_path}
    elif target_kind == "source":
        bound_path = bind_file_asset_to_standard_slot(workspace, source_rel, asset_key, "Image_Source", IMAGE_EXTS, "replace_source_image_slot_asset", dialogue, scene, sc=sc)
        if not bound_path:
            raise HTTPException(status_code=400, detail="Could not copy Source image to Working.")
        old_path = sc.text((dialogue.get("source_image_paths") or [""])[0]) or sc.text(dialogue.get("image_path"))
        dialogue["source_image_paths"] = [bound_path] if bound_path else []
        dialogue["image_path"] = bound_path
    else:
        bound_path = bind_file_asset_to_standard_slot(workspace, source_rel, asset_key, "Image_New", IMAGE_EXTS, "replace_new_image_slot_asset", dialogue, scene, sc=sc)
        if not bound_path:
            raise HTTPException(status_code=400, detail="Could not copy New image to Working.")
        images = dialogue_assets.get("images") if isinstance(dialogue_assets.get("images"), list) else []
        old_path = sc.text((images[0] or {}).get("path")) if images else sc.text(dialogue.get("bound_image_path"))
        if not images:
            images = [sc.normalize_asset_slot({}, "Image_New", sc=sc), sc.normalize_asset_slot({}, "Image_02", sc=sc)]
            dialogue_assets["images"] = images
        images[0] = {"slot": "Image_New", "source_type": "generated", "path": bound_path}
        dialogue["bound_image_path"] = bound_path
    if old_path and old_path != bound_path:
        delete_working_file_if_unreferenced(workspace, old_path, plan, "replace_slot_asset", dialogue, scene, sc=sc)
    return plan


def clear_asset_from_plan(workspace: Path, plan: dict[str, Any], dialogue_id: str, target_kind: str, *, sc: Any) -> tuple[dict[str, Any], str, bool]:
    _shot, scene, dialogue = find_dialogue(plan, dialogue_id, sc=sc)
    dialogue_assets = sc.ensure_dialogue_working_assets(dialogue, sc=sc)
    old_path = ""
    target_kind = normalize_video_target_kind(target_kind, sc=sc)
    if target_kind == "audio":
        old_path = sc.current_slot_path(dialogue_assets["audio"], sc=sc)
        dialogue_assets["audio"] = {"slot": "Audio_Final", "source_type": "", "path": ""}
    elif target_kind == "raw_video":
        old_path = sc.existing_working_slot_path(workspace, sc.dialogue_asset_key(dialogue, sc=sc), "Video_Raw", VIDEO_EXTS, sc=sc)
    elif target_kind == "final_video":
        old_path = sc.current_slot_path(dialogue_assets["video"], sc=sc)
        if not old_path:
            old_path = sc.existing_working_slot_path(workspace, sc.dialogue_asset_key(dialogue, sc=sc), "Video_Final", VIDEO_EXTS, sc=sc)
        dialogue_assets["video"] = {"slot": "Video_Final", "source_type": "", "path": ""}
    elif target_kind == "source":
        old_path = sc.text((dialogue.get("source_image_paths") or [""])[0]) or sc.text(dialogue.get("image_path"))
        dialogue["source_image_paths"] = []
        dialogue["image_path"] = ""
    else:
        images = dialogue_assets.get("images") if isinstance(dialogue_assets.get("images"), list) else []
        asset_key = sc.dialogue_asset_key(dialogue, sc=sc)
        old_path = sc.text((images[0] or {}).get("path")) if images else sc.text(dialogue.get("bound_image_path"))
        if images:
            images[0] = {"slot": "Image_New", "source_type": "", "path": ""}
        dialogue["bound_image_path"] = ""
    if target_kind in {"raw_video", "final_video"}:
        moved, _history_rel = move_working_file_to_history(workspace, old_path, "remove_slot_asset", dialogue, scene, sc=sc)
        deleted = moved
    elif target_kind not in {"audio", "source"}:
        deleted = bool(archive_related_image_outputs(workspace, asset_key, old_path, plan, "remove_slot_asset", dialogue, scene, sc=sc))
    else:
        deleted = delete_working_file_if_unreferenced(workspace, old_path, plan, "remove_slot_asset", dialogue, scene, sc=sc)
    return plan, old_path, deleted


def materialize_plan_assets(workspace: Path, plan: dict[str, Any], *, sc: Any) -> None:
    for shot in plan.get("shots") or []:
        for scene in shot.get("scenes") or []:
            sc.ensure_working_assets(scene, sc=sc)
            for dialogue in scene.get("dialogues") or []:
                dialogue_assets = sc.ensure_dialogue_working_assets(dialogue, sc=sc)
                asset_key = sc.dialogue_asset_key(dialogue, sc=sc)
                for slot_name, default_slot in (("audio", "Audio_Final"), ("video", "Video_Final")):
                    slot = dialogue_assets.get(slot_name)
                    slot_path = sc.current_slot_path(slot, sc=sc)
                    if not slot_path:
                        continue
                    source_type = sc.asset_source_type(slot_path, slot.get("source_type") if isinstance(slot, dict) else "", sc=sc)
                    allowed_exts = AUDIO_EXTS if slot_name == "audio" else VIDEO_EXTS
                    next_path, next_source_type = materialize_standard_slot_path(workspace, slot_path, asset_key, default_slot, allowed_exts, slot.get("source_type") if isinstance(slot, dict) else "", "materialize_slot_asset", dialogue, scene, sc=sc)
                    slot["path"] = next_path
                    slot["source_type"] = next_source_type
                for image in dialogue_assets.get("images") or []:
                    if not isinstance(image, dict):
                        continue
                    image_path = sc.text(image.get("path"))
                    if not image_path:
                        continue
                    next_path, next_source_type = materialize_standard_slot_path(workspace, image_path, asset_key, image.get("slot") or "Image_New", IMAGE_EXTS, image.get("source_type"), "materialize_image_slot_asset", dialogue, scene, sc=sc)
                    image["path"] = next_path
                    image["source_type"] = next_source_type
                bound_image_path = sc.text((dialogue_assets.get("images") or [{}])[0].get("path") if dialogue_assets.get("images") else "")
                if bound_image_path:
                    dialogue["bound_image_path"] = bound_image_path
                source_image_path = sc.text((dialogue.get("source_image_paths") or [""])[0]) or sc.text(dialogue.get("image_path"))
                if source_image_path:
                    next_source_path, _next_source_type = materialize_standard_slot_path(workspace, source_image_path, asset_key, "Image_Source", IMAGE_EXTS, "", "materialize_source_image_slot_asset", dialogue, scene, sc=sc)
                    dialogue["source_image_paths"] = [next_source_path] if next_source_path else []
                    dialogue["image_path"] = next_source_path


def history_versions(workspace: Path, *, sc: Any) -> list[dict[str, Any]]:
    root = workspace / ASSET_HISTORY_REL
    if not root.exists():
        return []
    versions: list[dict[str, Any]] = []
    for version_dir in sorted([item for item in root.iterdir() if item.is_dir()], key=lambda item: item.name, reverse=True):
        manifest = sc.read_json(version_dir / "manifest.json")
        items = manifest.get("items") if isinstance(manifest.get("items"), list) else []
        normalized_items = []
        seen_history_paths: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            history_path = sc.text(item.get("history_path"))
            if not history_path:
                continue
            seen_history_paths.add(history_path)
            normalized_items.append({
                **item,
                "id": history_path,
                "path": history_path,
                "label": Path(history_path).name,
                "filename": Path(history_path).name,
                "kind": sc.text(item.get("asset_type")).lower() or sc.asset_type_for_path(history_path).lower(),
                "source": "history",
            })
        for file_path in sorted([item for item in version_dir.iterdir() if item.is_file() and item.name != "manifest.json"], key=lambda item: item.name):
            history_path = file_path.relative_to(workspace).as_posix()
            if history_path in seen_history_paths:
                continue
            original_path = f"{WORKING_REL}/{file_path.name}"
            normalized_items.append({
                **history_item_for(original_path, history_path, sc.text(manifest.get("reason")) or "history_orphan_file", sc=sc),
                "id": history_path,
                "path": history_path,
                "label": file_path.name,
                "filename": file_path.name,
                "kind": sc.asset_type_for_path(history_path).lower(),
                "source": "history",
                "recovered_from_file_scan": True,
            })
        versions.append({
            "id": version_dir.name,
            "version": version_dir.name,
            "path": version_dir.relative_to(workspace).as_posix(),
            "created_at": manifest.get("created_at"),
            "reason": manifest.get("reason", "regroup"),
            "items": normalized_items,
        })
    return versions


def register_asset_history_services(ns: Any) -> None:
    for name in SERVICE_EXPORTS:
        setattr(ns, name, globals()[name])
