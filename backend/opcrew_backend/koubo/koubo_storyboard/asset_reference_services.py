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
    "plan_references_path",
    "clear_path_references",
    "replace_path_references",
    "history_item_for",
    "move_working_file_to_history",
    "delete_working_file_if_unreferenced",
    "normalize_video_target_kind",
    "bind_video_asset_to_standard_slot",
    "bind_file_asset_to_standard_slot",
    "materialize_standard_slot_path",
    "generated_asset_refs",
    "archive_removed_generated_assets",
    "find_dialogue",
    "coerce_edit_plan",
    "bind_asset_to_plan",
    "clear_asset_from_plan",
    "materialize_plan_assets",
)


def plan_references_path(plan: dict[str, Any], rel_path: str, *, sc: Any) -> bool:
    if not rel_path:
        return False
    for shot in plan.get("shots") or []:
        for scene in shot.get("scenes") or []:
            working_assets = sc.ensure_working_assets(scene, sc=sc)
            if sc.text(working_assets.get("audio", {}).get("path")) == rel_path:
                return True
            if sc.text(working_assets.get("video", {}).get("path")) == rel_path:
                return True
            for image in working_assets.get("images") or []:
                if isinstance(image, dict) and sc.text(image.get("path")) == rel_path:
                    return True
            for dialogue in scene.get("dialogues") or []:
                dialogue_assets = sc.ensure_dialogue_working_assets(dialogue, sc=sc)
                if sc.text(dialogue_assets.get("audio", {}).get("path")) == rel_path:
                    return True
                if sc.text(dialogue_assets.get("video", {}).get("path")) == rel_path:
                    return True
                for image in dialogue_assets.get("images") or []:
                    if isinstance(image, dict) and sc.text(image.get("path")) == rel_path:
                        return True
                if sc.text(dialogue.get("bound_image_path")) == rel_path or sc.text(dialogue.get("image_path")) == rel_path:
                    return True
                if rel_path in [sc.text(path) for path in dialogue.get("source_image_paths") or []]:
                    return True
    return False


def clear_path_references(plan: dict[str, Any], rel_path: str, *, sc: Any) -> int:
    rel_path = sc.text(rel_path)
    if not rel_path:
        return 0
    cleared = 0
    for shot in plan.get("shots") or []:
        for scene in shot.get("scenes") or []:
            working_assets = sc.ensure_working_assets(scene, sc=sc)
            for key, slot_name in (("audio", "Audio_Final"), ("video", "Video_Final")):
                if sc.text(working_assets.get(key, {}).get("path")) == rel_path:
                    working_assets[key] = {"slot": slot_name, "source_type": "", "path": ""}
                    cleared += 1
            for image in working_assets.get("images") or []:
                if isinstance(image, dict) and sc.text(image.get("path")) == rel_path:
                    image["path"] = ""
                    image["source_type"] = ""
                    cleared += 1
            for dialogue in scene.get("dialogues") or []:
                dialogue_assets = sc.ensure_dialogue_working_assets(dialogue, sc=sc)
                for key, slot_name in (("audio", "Audio_Final"), ("video", "Video_Final")):
                    if sc.text(dialogue_assets.get(key, {}).get("path")) == rel_path:
                        dialogue_assets[key] = {"slot": slot_name, "source_type": "", "path": ""}
                        cleared += 1
                for image in dialogue_assets.get("images") or []:
                    if isinstance(image, dict) and sc.text(image.get("path")) == rel_path:
                        image["path"] = ""
                        image["source_type"] = ""
                        cleared += 1
                if sc.text(dialogue.get("bound_image_path")) == rel_path:
                    dialogue["bound_image_path"] = ""
                    cleared += 1
                if sc.text(dialogue.get("image_path")) == rel_path:
                    dialogue["image_path"] = ""
                    cleared += 1
                source_paths = [sc.text(item) for item in dialogue.get("source_image_paths") or [] if sc.text(item)]
                if rel_path in source_paths:
                    dialogue["source_image_paths"] = [item for item in source_paths if item != rel_path]
                    cleared += 1
    return cleared


def replace_path_references(plan: dict[str, Any], old_rel_path: str, new_rel_path: str, *, sc: Any) -> int:
    old_rel_path = sc.text(old_rel_path)
    new_rel_path = sc.text(new_rel_path)
    if not old_rel_path or not new_rel_path or old_rel_path == new_rel_path:
        return 0
    replaced = 0
    for shot in plan.get("shots") or []:
        for scene in shot.get("scenes") or []:
            working_assets = sc.ensure_working_assets(scene, sc=sc)
            for key in ("audio", "video"):
                if sc.text(working_assets.get(key, {}).get("path")) == old_rel_path:
                    working_assets[key]["path"] = new_rel_path
                    working_assets[key]["source_type"] = sc.asset_source_type(new_rel_path, working_assets[key].get("source_type"), sc=sc)
                    replaced += 1
            for image in working_assets.get("images") or []:
                if isinstance(image, dict) and sc.text(image.get("path")) == old_rel_path:
                    image["path"] = new_rel_path
                    image["source_type"] = sc.asset_source_type(new_rel_path, image.get("source_type"), sc=sc)
                    replaced += 1
            for dialogue in scene.get("dialogues") or []:
                dialogue_assets = sc.ensure_dialogue_working_assets(dialogue, sc=sc)
                for key in ("audio", "video"):
                    if sc.text(dialogue_assets.get(key, {}).get("path")) == old_rel_path:
                        dialogue_assets[key]["path"] = new_rel_path
                        dialogue_assets[key]["source_type"] = sc.asset_source_type(new_rel_path, dialogue_assets[key].get("source_type"), sc=sc)
                        replaced += 1
                for image in dialogue_assets.get("images") or []:
                    if isinstance(image, dict) and sc.text(image.get("path")) == old_rel_path:
                        image["path"] = new_rel_path
                        image["source_type"] = sc.asset_source_type(new_rel_path, image.get("source_type"), sc=sc)
                        replaced += 1
                if sc.text(dialogue.get("bound_image_path")) == old_rel_path:
                    dialogue["bound_image_path"] = new_rel_path
                    replaced += 1
                if sc.text(dialogue.get("image_path")) == old_rel_path:
                    dialogue["image_path"] = new_rel_path
                    replaced += 1
                source_paths = [sc.text(item) for item in dialogue.get("source_image_paths") or [] if sc.text(item)]
                if old_rel_path in source_paths:
                    dialogue["source_image_paths"] = [new_rel_path if item == old_rel_path else item for item in source_paths]
                    replaced += 1
    return replaced


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
    if not rel_path.startswith(f"{WORKING_REL}/") or plan_references_path(plan, rel_path, sc=sc):
        return False
    moved, _history_rel = move_working_file_to_history(workspace, rel_path, reason, dialogue, scene, sc=sc)
    return moved


def normalize_video_target_kind(target_kind: str, *, sc: Any) -> str:
    value = sc.text(target_kind)
    return value


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


def archive_removed_generated_assets(workspace: Path, previous_plan: dict[str, Any], next_plan: dict[str, Any], reason: str, *, sc: Any) -> list[dict[str, str]]:
    previous_refs = generated_asset_refs(previous_plan, sc=sc) if isinstance(previous_plan, dict) else {}
    next_paths = set(generated_asset_refs(next_plan, sc=sc).keys()) if isinstance(next_plan, dict) else set()
    archived: list[dict[str, str]] = []
    for rel_path, info in previous_refs.items():
        if rel_path in next_paths:
            continue
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
        old_path = sc.text((images[0] or {}).get("path")) if images else sc.text(dialogue.get("bound_image_path"))
        if images:
            images[0] = {"slot": "Image_New", "source_type": "", "path": ""}
        dialogue["bound_image_path"] = ""
    if target_kind in {"raw_video", "final_video"}:
        deleted, _history_rel = move_working_file_to_history(workspace, old_path, "remove_slot_asset", dialogue, scene, sc=sc)
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


def register_asset_reference_services(ns: Any) -> None:
    for name in SERVICE_EXPORTS:
        setattr(ns, name, globals()[name])
