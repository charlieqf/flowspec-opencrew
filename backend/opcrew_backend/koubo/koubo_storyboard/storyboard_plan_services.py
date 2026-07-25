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
from .text_utils import redact_payload, redact_secret_text, simplify_storyboard_text
from .tts_selection_recovery import rehydrate_storyboard_tts_selection


ANALYSIS_STORYBOARD_SOURCE_RELS = (
    "S7_04_02_StoryBoard/Output/srt_storyboard.json",
    "S7_04_03_StoryBoardQuick/Output/srt_storyboard.json",
)
_STORYBOARD_SIGNATURE_CACHE: dict[str, tuple[int, int, dict[str, Any]]] = {}


def _plain_text(value: Any) -> str:
    return str(value or "").strip()


def stable_storyboard_sha256(payload: Any) -> str:
    try:
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        raw = json.dumps({}, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def storyboard_edit_revision_sha256(payload: Any) -> str:
    if not isinstance(payload, dict) or payload.get("schema_version") != "koubo_storyboard_edit_0.1":
        return ""
    revision_payload = clone_json(payload)
    revision_payload.pop("edit_revision_sha256", None)
    revision_payload.pop("source_revision_sha256", None)
    return stable_storyboard_sha256(revision_payload)


def _read_storyboard_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _mtime_ms(path: Path) -> int:
    try:
        return int(path.stat().st_mtime * 1000)
    except Exception:
        return 0


def _fingerprint(path: Path) -> tuple[int, int]:
    try:
        stat = path.stat()
        return int(stat.st_mtime_ns), int(stat.st_size)
    except Exception:
        return 0, 0


def storyboard_source_signature(path: Path, rel: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict) or not payload:
        return {}
    mtime_ns, size = _fingerprint(path)
    cache_key = str(path)
    cached = _STORYBOARD_SIGNATURE_CACHE.get(cache_key)
    if cached and cached[0] == mtime_ns and cached[1] == size:
        return dict(cached[2])
    signature = {
        "path": rel,
        "sha256": stable_storyboard_sha256(payload),
        "tool": _plain_text(payload.get("tool")),
        "mtime_ms": int(mtime_ns / 1_000_000) if mtime_ns else _mtime_ms(path),
    }
    if mtime_ns and size:
        _STORYBOARD_SIGNATURE_CACHE[cache_key] = (mtime_ns, size, dict(signature))
    return signature


def latest_analysis_storyboard_source(workspace: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    candidates: list[tuple[int, str, Path, dict[str, Any]]] = []
    for rel in ANALYSIS_STORYBOARD_SOURCE_RELS:
        path = workspace / rel
        payload = _read_storyboard_json(path)
        if not payload:
            continue
        candidates.append((_mtime_ms(path), rel, path, payload))
    if not candidates:
        return {}, {}
    _mtime, rel, path, payload = sorted(candidates, key=lambda item: item[0], reverse=True)[0]
    return payload, storyboard_source_signature(path, rel, payload)


def current_storyboard_source(workspace: Path, source: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], bool]:
    source_signature = storyboard_source_signature(workspace / SOURCE_REL, SOURCE_REL, source) if source else {}
    latest_source, latest_signature = latest_analysis_storyboard_source(workspace)
    source_updated_from_edit = _plain_text(source.get("updated_from")) == EDIT_REL if isinstance(source, dict) else False
    if latest_source:
        source_hash = _plain_text(source_signature.get("sha256"))
        latest_hash = _plain_text(latest_signature.get("sha256"))
        if not source or source_updated_from_edit or source_hash != latest_hash:
            return latest_source, latest_signature, True
    if source_updated_from_edit:
        # Saving the editable StoryBoard also projects it into SOURCE_REL for
        # downstream Analysis V1 tools.  That projection necessarily has a
        # different physical hash, while its embedded source signature still
        # identifies the authoritative tool output the edit was based on.
        # Comparing a later edit with the projection's own hash would make the
        # server reject its own immediately preceding save as stale.
        projected_source_hash = _plain_text(source.get("source_storyboard_sha256"))
        if re.fullmatch(r"[0-9a-fA-F]{64}", projected_source_hash):
            try:
                projected_source_mtime_ms = int(source.get("source_storyboard_mtime_ms") or 0)
            except (TypeError, ValueError):
                projected_source_mtime_ms = 0
            return source, {
                "path": _plain_text(source.get("source_storyboard_path")) or SOURCE_REL,
                "sha256": projected_source_hash.lower(),
                "tool": _plain_text(source.get("source_storyboard_tool")) or _plain_text(source.get("tool")),
                "mtime_ms": projected_source_mtime_ms,
            }, False
    return source, source_signature, False


def apply_storyboard_source_signature(plan: dict[str, Any], signature: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(plan, dict) or not signature:
        return plan
    plan["source_storyboard_path"] = _plain_text(signature.get("path"))
    plan["source_storyboard_sha256"] = _plain_text(signature.get("sha256"))
    plan["source_storyboard_tool"] = _plain_text(signature.get("tool"))
    plan["source_storyboard_mtime_ms"] = int(signature.get("mtime_ms") or 0)
    return plan


def storyboard_edit_matches_source(edit: dict[str, Any], signature: dict[str, Any]) -> bool:
    if not isinstance(edit, dict) or edit.get("schema_version") != "koubo_storyboard_edit_0.1":
        return False
    if not signature:
        return True
    expected_hash = _plain_text(signature.get("sha256"))
    edit_hash = _plain_text(edit.get("source_storyboard_sha256"))
    if edit_hash:
        return bool(expected_hash and edit_hash == expected_hash)
    edit_tool = _plain_text(edit.get("source_storyboard_tool") or edit.get("created_from_tool"))
    source_tool = _plain_text(signature.get("tool"))
    if edit_tool and source_tool and edit_tool != source_tool:
        return False
    return True


SERVICE_EXPORTS = (
    "stable_storyboard_sha256",
    "storyboard_edit_revision_sha256",
    "current_storyboard_source",
    "apply_storyboard_source_signature",
    "storyboard_edit_matches_source",
    "latest_analysis_storyboard_source",
    "scene_id_for",
    "empty_dialogue_working_assets",
    "working_assets_for_source",
    "copy_dialogue_generation_metadata",
    "copy_compose_assets",
    "normalize_source_plan",
    "recalculate",
    "clone_json",
    "projected_dialogue_image_path",
    "projected_source_dialogue",
    "project_edit_plan_to_source_storyboard",
    "save_edit_and_source_storyboard",
)


def scene_id_for(index: int) -> str:
    return f"scene_{index:03d}"


def empty_dialogue_working_assets() -> dict[str, Any]:
    return {
        "audio": {"slot": "Audio_Final", "source_type": "", "path": ""},
        "images": [
            {"slot": "Image_New", "source_type": "", "path": ""},
            {"slot": "Image_02", "source_type": "", "path": ""},
        ],
        "video": {"slot": "Video_Final", "source_type": "", "path": ""},
    }


def working_assets_for_source(source_scene: dict[str, Any], *, sc: Any) -> dict[str, Any]:
    source_assets = source_scene.get("working_assets") if isinstance(source_scene.get("working_assets"), dict) else {}
    return {
        "audio": {"slot": "Audio_Final", "path": sc.text((source_assets.get("audio") or {}).get("path") if isinstance(source_assets.get("audio"), dict) else "")},
        "images": [
            {"slot": "Image_New", "path": sc.text(((source_assets.get("images") or [{}])[0] or {}).get("path")) if isinstance(source_assets.get("images"), list) and source_assets.get("images") else ""},
            {"slot": "Image_02", "path": sc.text(((source_assets.get("images") or [{}, {}])[1] or {}).get("path")) if isinstance(source_assets.get("images"), list) and len(source_assets.get("images") or []) > 1 else ""},
        ],
        "video": {"slot": "Video_Final", "path": sc.text((source_assets.get("video") or {}).get("path") if isinstance(source_assets.get("video"), dict) else "")},
    }


def copy_dialogue_generation_metadata(source: dict[str, Any], target: dict[str, Any], *, sc: Any) -> dict[str, Any]:
    if isinstance(source.get("dance_mimic"), dict):
        target["dance_mimic"] = json.loads(json.dumps(source["dance_mimic"], ensure_ascii=False))
    for key in (
        "video_generation_mode",
        "provider",
        "model",
        "model_alias",
        "reference_mode",
        "prompt_template",
        "reference_video_path",
        "reference_video_role",
    ):
        value = sc.text(source.get(key))
        if value:
            target[key] = value
    return target


def copy_compose_assets(source: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    assets = source.get("compose_assets") if isinstance(source.get("compose_assets"), dict) else {}
    if assets:
        target["compose_assets"] = json.loads(json.dumps(assets, ensure_ascii=False))
    return target


def first_working_image_path(source: dict[str, Any], *, sc: Any) -> str:
    assets = source.get("working_assets") if isinstance(source.get("working_assets"), dict) else {}
    images = assets.get("images") if isinstance(assets.get("images"), list) else []
    return sc.text((images[0] or {}).get("path")) if images else ""


def is_standard_new_image_path(path: str, *, sc: Any) -> bool:
    return bool(re.search(r"_Image_New\.[^./\\]+$", sc.text(path)))


def source_image_path_is_polluted_by_new_image(item_image: str, bound_image_path: str, working_image_path: str, *, sc: Any) -> bool:
    item_image = sc.text(item_image)
    if not item_image:
        return False
    new_image_path = sc.text(working_image_path) or sc.text(bound_image_path)
    return bool(new_image_path and item_image == new_image_path and is_standard_new_image_path(item_image, sc=sc))


def clean_dialogue_source_image_paths(dialogue: dict[str, Any], *, sc: Any) -> list[str]:
    bound_image_path = sc.text(dialogue.get("bound_image_path"))
    working_image_path = first_working_image_path(dialogue, sc=sc)
    return [
        path
        for path in [sc.text(path) for path in dialogue.get("source_image_paths") or [] if sc.text(path)]
        if not source_image_path_is_polluted_by_new_image(path, bound_image_path, working_image_path, sc=sc)
    ]


def sanitize_dialogue_source_image_fields(dialogue: dict[str, Any], *, sc: Any) -> None:
    source_image_paths = clean_dialogue_source_image_paths(dialogue, sc=sc)
    image_path = sc.text(dialogue.get("image_path"))
    bound_image_path = sc.text(dialogue.get("bound_image_path"))
    working_image_path = first_working_image_path(dialogue, sc=sc)
    if source_image_path_is_polluted_by_new_image(image_path, bound_image_path, working_image_path, sc=sc):
        image_path = ""
    dialogue["source_image_paths"] = source_image_paths
    dialogue["image_path"] = source_image_paths[0] if source_image_paths else image_path


def normalize_source_plan(task: dict[str, Any], source: dict[str, Any], *, sc: Any) -> dict[str, Any]:
    workflow_meta = storyboard_meta_for_workflow(infer_openclip_workflow_mode(task))
    shots: list[dict[str, Any]] = []
    global_scene_index = 0
    used_dialogue_asset_keys: set[str] = set()
    for shot_index, source_shot in enumerate(source.get("shots") or [], start=1):
        if not isinstance(source_shot, dict):
            continue
        source_shot_id = sc.text(source_shot.get("shot_id"), f"shot_{shot_index:03d}") or f"shot_{shot_index:03d}"
        shot_id = f"shot_{shot_index:03d}"
        scenes: list[dict[str, Any]] = []
        for scene_index, source_scene in enumerate(source_shot.get("scenes") or [], start=1):
            if not isinstance(source_scene, dict):
                continue
            global_scene_index += 1
            scene_id = sc.text(source_scene.get("scene_id")) if sc.text(source_scene.get("scene_id")).startswith("scene_") else scene_id_for(global_scene_index)
            asset_key = f"{shot_id}_{scene_id}"
            image_paths = [sc.text(path) for path in source_scene.get("key_frame_paths") or [] if sc.text(path)]
            source_dialogues = source_scene.get("dialogue_items") or []
            dialogues: list[dict[str, Any]] = []
            if isinstance(source_dialogues, list):
                for dialogue_index, source_dialogue in enumerate(source_dialogues, start=1):
                    if not isinstance(source_dialogue, dict):
                        continue
                    source_image_paths = [sc.text(path) for path in source_dialogue.get("source_image_paths") or [] if sc.text(path)]
                    item_image = source_image_paths[0] if source_image_paths else sc.text(source_dialogue.get("image_path"))
                    raw_bound_image_path = sc.text(source_dialogue.get("bound_image_path"))
                    raw_working_image_path = first_working_image_path(source_dialogue, sc=sc)
                    polluted_new_image = source_image_path_is_polluted_by_new_image(item_image, raw_bound_image_path, raw_working_image_path, sc=sc)
                    if polluted_new_image:
                        item_image = ""
                    dialogue_assets = json.loads(json.dumps(sc.ensure_dialogue_working_assets(source_dialogue, sc=sc), ensure_ascii=False))
                    if polluted_new_image:
                        bound_image_path = raw_working_image_path or raw_bound_image_path
                        images = dialogue_assets.get("images") if isinstance(dialogue_assets.get("images"), list) else []
                        if images:
                            images[0] = {"slot": "Image_New", "source_type": images[0].get("source_type") or "generated", "path": bound_image_path}
                        else:
                            dialogue_assets["images"] = [{"slot": "Image_New", "source_type": "generated", "path": bound_image_path}]
                    else:
                        bound_image_path = raw_bound_image_path
                    srt_id = sc.text(source_dialogue.get("srt_id"))
                    dialogue_asset_key_value = sc.derive_dialogue_asset_key(source_dialogue, used_dialogue_asset_keys, sc=sc)
                    used_dialogue_asset_keys.add(dialogue_asset_key_value)
                    dialogue_payload = {
                        "dialogue_id": f"{scene_id}_dialogue_{dialogue_index:03d}",
                        "scene_id": scene_id,
                        "dialogue_index": dialogue_index,
                        "srt_id": srt_id,
                        "srt_ids": [srt_id] if srt_id else [],
                        "dialogue_asset_key": dialogue_asset_key_value,
                        "text": simplify_storyboard_text(source_dialogue.get("dialogue")),
                        "start": round(sc.number(source_dialogue.get("start")), 3),
                        "end": round(sc.number(source_dialogue.get("end")), 3),
                        "duration": round(sc.number(source_dialogue.get("duration"), max(0.2, sc.number(source_dialogue.get("end")) - sc.number(source_dialogue.get("start")))), 3),
                        "source_image_paths": [item_image] if item_image else [],
                        "image_path": item_image,
                        "bound_image_path": bound_image_path,
                        "working_assets": dialogue_assets,
                    }
                    dialogues.append(copy_dialogue_generation_metadata(source_dialogue, dialogue_payload, sc=sc))
            if not dialogues:
                dialogue_id = f"{scene_id}_dialogue_001"
                srt_ids = [sc.text(item) for item in source_scene.get("srt_ids") or [] if sc.text(item)]
                srt_id = srt_ids[0] if srt_ids else ""
                dialogue_asset_key_value = sc.new_dialogue_asset_key(used_dialogue_asset_keys)
                used_dialogue_asset_keys.add(dialogue_asset_key_value)
                dialogues = [{
                    "dialogue_id": dialogue_id,
                    "scene_id": scene_id,
                    "dialogue_index": 1,
                    "srt_id": srt_id,
                    "srt_ids": srt_ids,
                    "dialogue_asset_key": dialogue_asset_key_value,
                    "text": simplify_storyboard_text(source_scene.get("dialogue")),
                    "start": round(sc.number(source_scene.get("start")), 3),
                    "end": round(sc.number(source_scene.get("end")), 3),
                    "duration": round(sc.number(source_scene.get("duration"), 0.2), 3),
                    "source_image_paths": image_paths,
                    "image_path": image_paths[0] if image_paths else "",
                    "bound_image_path": "",
                    "working_assets": {
                        "audio": {"slot": "Audio_Final", "source_type": "", "path": ""},
                        "images": [
                            {"slot": "Image_New", "source_type": "", "path": ""},
                            {"slot": "Image_02", "source_type": "", "path": ""},
                        ],
                        "video": {"slot": "Video_Final", "source_type": "", "path": ""},
                    },
                }]
            scene_payload = {
                "scene_id": scene_id,
                "source_scene_id": sc.text(source_scene.get("scene_id"), f"scene_{scene_index:03d}"),
                "asset_key": asset_key,
                "working_assets": working_assets_for_source(source_scene, sc=sc),
                "scene_name": simplify_storyboard_text(source_scene.get("title"), scene_id),
                "scene_index": scene_index,
                "start": round(sc.number(source_scene.get("start")), 3),
                "end": round(sc.number(source_scene.get("end")), 3),
                "duration": round(sc.number(source_scene.get("duration"), max(0.2, sc.number(source_scene.get("end")) - sc.number(source_scene.get("start")))), 3),
                "summary": simplify_storyboard_text(source_scene.get("summary")),
                "dialogues": dialogues,
            }
            scenes.append(copy_compose_assets(source_scene, scene_payload))
        if not scenes:
            duration = round(sc.number(source_shot.get("duration"), 4), 3)
            global_scene_index += 1
            scene_id = scene_id_for(global_scene_index)
            asset_key = f"{shot_id}_{scene_id}"
            dialogue_asset_key_value = sc.new_dialogue_asset_key(used_dialogue_asset_keys)
            used_dialogue_asset_keys.add(dialogue_asset_key_value)
            scene_payload = {
                "scene_id": scene_id,
                "source_scene_id": "scene_001",
                "asset_key": asset_key,
                "working_assets": working_assets_for_source({}, sc=sc),
                "scene_name": simplify_storyboard_text(source_shot.get("title"), scene_id),
                "scene_index": 1,
                "start": round(sc.number(source_shot.get("start")), 3),
                "end": round(sc.number(source_shot.get("end"), duration), 3),
                "duration": duration,
                "summary": simplify_storyboard_text(source_shot.get("summary")),
                "dialogues": [{
                    "dialogue_id": f"{scene_id}_dialogue_001",
                    "scene_id": scene_id,
                    "dialogue_index": 1,
                    "srt_id": sc.text((source_shot.get("srt_ids") or [""])[0]) if isinstance(source_shot.get("srt_ids"), list) and source_shot.get("srt_ids") else "",
                    "srt_ids": [sc.text(item) for item in source_shot.get("srt_ids") or [] if sc.text(item)],
                    "dialogue_asset_key": dialogue_asset_key_value,
                    "text": simplify_storyboard_text(source_shot.get("dialogue")),
                    "start": round(sc.number(source_shot.get("start")), 3),
                    "end": round(sc.number(source_shot.get("end"), duration), 3),
                    "duration": duration,
                    "source_image_paths": [sc.text(path) for path in source_shot.get("key_frame_paths") or [] if sc.text(path)],
                    "image_path": "",
                    "bound_image_path": "",
                    "working_assets": {
                        "audio": {"slot": "Audio_Final", "source_type": "", "path": ""},
                        "images": [
                            {"slot": "Image_New", "source_type": "", "path": ""},
                            {"slot": "Image_02", "source_type": "", "path": ""},
                        ],
                        "video": {"slot": "Video_Final", "source_type": "", "path": ""},
                    },
                }],
            }
            scenes.append(copy_compose_assets(source_shot, scene_payload))
        shot_payload = {
            "shot_id": shot_id,
            "source_shot_id": source_shot_id,
            "shot_name": simplify_storyboard_text(source_shot.get("title"), shot_id),
            "formula_stage": simplify_storyboard_text(source_shot.get("formula_stage")),
            "summary": simplify_storyboard_text(source_shot.get("summary")),
            "start": round(sc.number(source_shot.get("start")), 3),
            "end": round(sc.number(source_shot.get("end")), 3),
            "duration": round(sum(sc.number(scene.get("duration")) for scene in scenes), 3),
            "scenes": scenes,
        }
        shots.append(copy_compose_assets(source_shot, shot_payload))
    normalized = {
        "schema_version": "koubo_storyboard_edit_0.1",
        "title": workflow_meta["title"],
        "source_type": workflow_meta["source_type"],
        "workflow_mode": workflow_meta["workflow_mode"],
        "analysis_task_id": int(task["id"]),
        "analysis_session_id": int(task["session_id"]),
        "source_path": SOURCE_REL,
        "created_from_tool": sc.text(source.get("tool")),
        "video_formula": simplify_storyboard_text(source.get("video_formula")),
        "updated_at": now_ms(),
        "shots": shots,
    }
    return copy_compose_assets(source, normalized)


def recalculate(plan: dict[str, Any], *, sc: Any) -> dict[str, Any]:
    plan["video_formula"] = simplify_storyboard_text(plan.get("video_formula"))
    global_scene_index = 0
    used_dialogue_ids: set[str] = set()
    used_dialogue_asset_keys: set[str] = set()
    for shot_index, shot in enumerate(plan.get("shots") or [], start=1):
        shot_id = f"shot_{shot_index:03d}"
        shot["shot_id"] = shot_id
        shot["shot_name"] = simplify_storyboard_text(shot.get("shot_name"), shot_id) or shot_id
        shot["formula_stage"] = simplify_storyboard_text(shot.get("formula_stage"))
        shot["summary"] = simplify_storyboard_text(shot.get("summary"))
        cursor = 0.0
        shot_duration = 0.0
        for scene_index, scene in enumerate(shot.get("scenes") or [], start=1):
            global_scene_index += 1
            scene_id = scene_id_for(global_scene_index)
            scene["scene_id"] = scene_id
            scene["scene_index"] = scene_index
            scene["asset_key"] = f"{shot_id}_{scene_id}"
            scene["scene_name"] = simplify_storyboard_text(scene.get("scene_name"), scene_id) or scene_id
            scene["summary"] = simplify_storyboard_text(scene.get("summary"))
            sc.ensure_working_assets(scene, sc=sc)
            scene_duration = 0.0
            for dialogue_index, dialogue in enumerate(scene.get("dialogues") or [], start=1):
                duration = round(max(0, sc.number(dialogue.get("duration"))), 3)
                srt_ids = dialogue.get("srt_ids") if isinstance(dialogue.get("srt_ids"), list) else []
                srt_id = sc.text(dialogue.get("srt_id")) or (sc.text(srt_ids[0]) if srt_ids else "")
                current_dialogue_id = sc.text(dialogue.get("dialogue_id"))
                if not current_dialogue_id or current_dialogue_id in used_dialogue_ids:
                    current_dialogue_id = f"dlg_{uuid.uuid4().hex[:12]}"
                dialogue["dialogue_id"] = current_dialogue_id
                used_dialogue_ids.add(current_dialogue_id)
                dialogue["scene_id"] = scene_id
                dialogue["dialogue_index"] = dialogue_index
                dialogue["srt_id"] = srt_id
                dialogue["srt_ids"] = [srt_id] if srt_id else [sc.text(item) for item in srt_ids if sc.text(item)]
                previous_asset_key = sc.text(dialogue.get("dialogue_asset_key"))
                duplicate_asset_key = bool(previous_asset_key and previous_asset_key in used_dialogue_asset_keys)
                asset_key = sc.derive_dialogue_asset_key(dialogue, used_dialogue_asset_keys, sc=sc)
                if duplicate_asset_key:
                    dialogue["srt_id"] = ""
                    dialogue["srt_ids"] = []
                    dialogue["source_image_paths"] = []
                    dialogue["image_path"] = ""
                    dialogue["bound_image_path"] = ""
                    dialogue["working_assets"] = empty_dialogue_working_assets()
                dialogue["dialogue_asset_key"] = asset_key
                used_dialogue_asset_keys.add(asset_key)
                dialogue["text"] = simplify_storyboard_text(dialogue.get("text"))
                dialogue["duration"] = duration
                dialogue["start"] = round(cursor + scene_duration, 3)
                sanitize_dialogue_source_image_fields(dialogue, sc=sc)
                dialogue["end"] = round(cursor + scene_duration + duration, 3)
                sc.ensure_dialogue_working_assets(dialogue, sc=sc)
                sanitize_dialogue_source_image_fields(dialogue, sc=sc)
                scene_duration += duration
            scene["start"] = round(cursor, 3)
            scene["duration"] = round(scene_duration, 3)
            scene["end"] = round(cursor + scene_duration, 3)
            shot_duration += scene_duration
            cursor += scene_duration
        shot["start"] = 0
        shot["duration"] = round(shot_duration, 3)
        shot["end"] = round(shot_duration, 3)
    plan["updated_at"] = now_ms()
    return plan


def clone_json(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def projected_dialogue_image_path(dialogue: dict[str, Any], *, sc: Any) -> str:
    source_image_paths = clean_dialogue_source_image_paths(dialogue, sc=sc)
    image_path = sc.text(dialogue.get("image_path"))
    bound_image_path = sc.text(dialogue.get("bound_image_path"))
    working_image_path = first_working_image_path(dialogue, sc=sc)
    if source_image_path_is_polluted_by_new_image(image_path, bound_image_path, working_image_path, sc=sc):
        image_path = ""
    return (source_image_paths[0] if source_image_paths else image_path)


def projected_source_dialogue(dialogue: dict[str, Any], *, sc: Any) -> dict[str, Any]:
    sanitize_dialogue_source_image_fields(dialogue, sc=sc)
    srt_id = sc.text(dialogue.get("srt_id")) or sc.text((dialogue.get("srt_ids") or [""])[0])
    item: dict[str, Any] = {
        "srt_id": srt_id,
        "dialogue_id": sc.text(dialogue.get("dialogue_id")),
        "dialogue_asset_key": sc.text(dialogue.get("dialogue_asset_key")),
        "dialogue": simplify_storyboard_text(dialogue.get("text") or dialogue.get("dialogue")),
        "start": round(sc.number(dialogue.get("start")), 3),
        "end": round(sc.number(dialogue.get("end")), 3),
        "duration": round(sc.number(dialogue.get("duration")), 3),
        "image_path": projected_dialogue_image_path(dialogue, sc=sc),
        "key_frame_paths": [path for path in [projected_dialogue_image_path(dialogue, sc=sc)] if path],
        "working_assets": clone_json(sc.ensure_dialogue_working_assets(dialogue, sc=sc)),
    }
    source_image_paths = clean_dialogue_source_image_paths(dialogue, sc=sc)
    if source_image_paths:
        item["source_image_paths"] = source_image_paths
    bound_image_path = sc.text(dialogue.get("bound_image_path"))
    if bound_image_path:
        item["bound_image_path"] = bound_image_path
    if isinstance(dialogue.get("video_plan"), dict):
        item["video_plan"] = clone_json(dialogue["video_plan"])
    copy_dialogue_generation_metadata(dialogue, item, sc=sc)
    return item


def project_edit_plan_to_source_storyboard(task: dict[str, Any], workspace: Path, plan: dict[str, Any], *, sc: Any) -> dict[str, Any]:
    previous_source, source_signature, _source_refreshed = current_storyboard_source(workspace, read_json(workspace / SOURCE_REL))
    projected = {
        **previous_source,
        "schema_version": sc.text(previous_source.get("schema_version"), "analysis_v1_srt_storyboard_0.2"),
        "tool": sc.text(previous_source.get("tool")),
        "updated_from": EDIT_REL,
        "updated_at": now_ms(),
        "shots": [],
    }
    if not projected["tool"]:
        projected.pop("tool", None)
    if "video_formula" in projected:
        projected["video_formula"] = simplify_storyboard_text(projected.get("video_formula"))
    for shot in plan.get("shots") or []:
        if not isinstance(shot, dict):
            continue
        scenes: list[dict[str, Any]] = []
        for scene in shot.get("scenes") or []:
            if not isinstance(scene, dict):
                continue
            dialogue_items = [
                projected_source_dialogue(dialogue, sc=sc)
                for dialogue in scene.get("dialogues") or []
                if isinstance(dialogue, dict)
            ]
            key_frame_paths = [sc.text(item.get("image_path")) for item in dialogue_items if sc.text(item.get("image_path"))]
            scenes.append({
                "scene_id": sc.text(scene.get("scene_id")),
                "source_scene_id": sc.text(scene.get("source_scene_id")),
                "title": simplify_storyboard_text(scene.get("scene_name")),
                "summary": simplify_storyboard_text(scene.get("summary")),
                "start": round(sc.number(scene.get("start")), 3),
                "end": round(sc.number(scene.get("end")), 3),
                "duration": round(sc.number(scene.get("duration")), 3),
                "key_frame_paths": key_frame_paths,
                "working_assets": clone_json(sc.ensure_working_assets(scene, sc=sc)),
                "dialogue_items": dialogue_items,
            })
        projected["shots"].append({
            "shot_id": sc.text(shot.get("shot_id")),
            "source_shot_id": sc.text(shot.get("source_shot_id")),
            "title": simplify_storyboard_text(shot.get("shot_name")),
            "formula_stage": simplify_storyboard_text(shot.get("formula_stage")),
            "summary": simplify_storyboard_text(shot.get("summary")),
            "start": round(sc.number(shot.get("start")), 3),
            "end": round(sc.number(shot.get("end")), 3),
            "duration": round(sc.number(shot.get("duration")), 3),
            "scenes": scenes,
        })
    projected["analysis_task_id"] = int(task["id"])
    projected["analysis_session_id"] = int(task["session_id"])
    projected.pop("edit_revision_sha256", None)
    projected.pop("source_revision_sha256", None)
    apply_storyboard_source_signature(projected, source_signature)
    return projected


def save_edit_and_source_storyboard(task: dict[str, Any], workspace: Path, plan: dict[str, Any], *, sc: Any) -> None:
    physical_source = read_json(workspace / SOURCE_REL)
    physical_source_revision = stable_storyboard_sha256(physical_source) if physical_source else ""
    current_edit = read_json(workspace / EDIT_REL)
    current_edit_revision = storyboard_edit_revision_sha256(current_edit)
    expected_edit_revision = _plain_text(plan.get("edit_revision_sha256"))
    expected_source_revision = _plain_text(plan.get("source_revision_sha256"))
    stored_edit_revision = _plain_text(current_edit.get("edit_revision_sha256"))
    stored_source_revision = _plain_text(current_edit.get("source_revision_sha256"))
    edit_revision_mismatch = bool(current_edit_revision) and expected_edit_revision != current_edit_revision
    source_revision_mismatch = bool(physical_source_revision) and expected_source_revision != physical_source_revision
    if (edit_revision_mismatch and (expected_edit_revision or stored_edit_revision)) or (
        source_revision_mismatch and (expected_source_revision or stored_source_revision)
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "storyboard_edit_changed",
                "message": "StoryBoard 已被其他页面或后台任务更新，请刷新后再保存。",
            },
        )
    _source, source_signature, _source_refreshed = current_storyboard_source(workspace, physical_source)
    if not storyboard_edit_matches_source(plan, source_signature):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "storyboard_source_changed",
                "message": "Analysis V1 StoryBoard source changed. Reload StoryBoard before saving edits.",
                "active_source_path": source_signature.get("path", SOURCE_REL),
            },
        )
    try:
        rehydrated_plan = rehydrate_storyboard_tts_selection(
            getattr(sc, "ctx", sc),
            workspace,
            plan,
            strict=True,
            read_json=sc.read_json,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={
            "code": "selected_tts_voice_unavailable",
            "message": "当前音色缺少可验证的服务端生成配置，请重新选择音色后再保存。",
        }) from exc
    plan.clear()
    plan.update(rehydrated_plan)
    apply_storyboard_source_signature(plan, source_signature)
    projected_source = project_edit_plan_to_source_storyboard(task, workspace, plan, sc=sc)
    plan["source_revision_sha256"] = stable_storyboard_sha256(projected_source)
    plan["edit_revision_sha256"] = storyboard_edit_revision_sha256(plan)
    write_json(workspace / EDIT_REL, plan)
    write_json(workspace / SOURCE_REL, projected_source)


def register_storyboard_plan_services(ns: Any) -> None:
    for name in SERVICE_EXPORTS:
        setattr(ns, name, globals()[name])
