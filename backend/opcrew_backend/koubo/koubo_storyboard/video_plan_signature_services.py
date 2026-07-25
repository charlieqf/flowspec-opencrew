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
    "stable_video_plan_hash",
    "video_plan_stable_hash",
    "video_plan_with_hash",
    "video_plan_number",
    "video_plan_nonnegative_number",
    "video_plan_settings",
    "video_plan_target",
    "video_plan_scope_shots",
    "video_plan_asset_signature",
    "video_plan_existing_file_rel",
    "video_plan_consistency_manifest_output",
    "video_plan_consistency_final_image",
    "video_plan_consistency_reference_snapshot",
    "video_plan_consistency_refs_by_kind",
    "video_plan_consistency_payload_matches",
    "video_plan_input_snapshot",
    "video_plan_signature",
    "video_plan_cache_matches",
)


def stable_video_plan_hash(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def video_plan_stable_hash(plan_payload: dict[str, Any]) -> str:
    payload = json.loads(json.dumps(plan_payload or {}, ensure_ascii=False))
    if isinstance(payload, dict):
        payload = {key: value for key, value in payload.items() if key not in {"plan_hash", "plan_run_id", "created_at"}}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def video_plan_with_hash(plan_payload: dict[str, Any], *, sc: Any) -> dict[str, Any]:
    if not plan_payload:
        return {}
    plan = json.loads(json.dumps(plan_payload, ensure_ascii=False))
    plan["plan_hash"] = sc.text(plan.get("plan_hash")) or video_plan_stable_hash(plan)
    return plan


def video_plan_number(value: Any, fallback: float) -> float:
    try:
        parsed = float(value)
        return parsed if parsed > 0 else fallback
    except (TypeError, ValueError):
        return fallback


def video_plan_nonnegative_number(value: Any, fallback: float) -> float:
    try:
        parsed = float(value)
        return parsed if parsed >= 0 else fallback
    except (TypeError, ValueError):
        return fallback


def video_plan_settings(payload: dict[str, Any]) -> dict[str, float]:
    settings = payload.get("settings") if isinstance(payload.get("settings"), dict) else {}
    requested_max_video = video_plan_number(settings.get("max_video_seconds"), 4.0)
    max_video = min((4.0, 8.0, 10.0, 15.0), key=lambda option: abs(option - requested_max_video))
    min_video = max(2.0, video_plan_number(settings.get("min_video_seconds"), 2.0))
    tolerance = max(0.0, video_plan_nonnegative_number(settings.get("split_tolerance_seconds"), 2.0))
    return {
        "max_video_seconds": max_video,
        "min_video_seconds": min(min_video, max_video),
        "split_tolerance_seconds": 0.0 if max_video == 10.0 else tolerance,
    }


def video_plan_target(payload: dict[str, Any], plan: dict[str, Any], *, sc: Any) -> dict[str, str]:
    target = payload.get("target") if isinstance(payload.get("target"), dict) else {}
    target_type = sc.text(target.get("target_type") or target.get("scope") or "task")
    if target_type == "all":
        target_type = "task"
    if target_type not in {"scene", "shot", "task"}:
        raise HTTPException(status_code=400, detail="target.target_type must be scene, shot, or task")
    shot_id = sc.text(target.get("shot_id"))
    scene_id = sc.text(target.get("scene_id"))
    if target_type == "task":
        return {"target_type": "task", "shot_id": "", "scene_id": ""}
    if target_type == "shot":
        if not shot_id:
            raise HTTPException(status_code=400, detail="target.shot_id is required for shot VideoPlan")
        if not any(sc.text(shot.get("shot_id")) == shot_id for shot in plan.get("shots") or [] if isinstance(shot, dict)):
            raise HTTPException(status_code=404, detail=f"Shot not found: {shot_id}")
        return {"target_type": "shot", "shot_id": shot_id, "scene_id": ""}
    if not shot_id or not scene_id:
        raise HTTPException(status_code=400, detail="target.shot_id and target.scene_id are required for scene VideoPlan")
    for shot in plan.get("shots") or []:
        if not isinstance(shot, dict) or sc.text(shot.get("shot_id")) != shot_id:
            continue
        if any(sc.text(scene.get("scene_id")) == scene_id for scene in shot.get("scenes") or [] if isinstance(scene, dict)):
            return {"target_type": "scene", "shot_id": shot_id, "scene_id": scene_id}
    raise HTTPException(status_code=404, detail=f"Scene not found: {shot_id}/{scene_id}")


def video_plan_scope_shots(plan: dict[str, Any], target: dict[str, str], *, sc: Any) -> list[dict[str, Any]]:
    target_type = target["target_type"]
    if target_type == "task":
        return [shot for shot in plan.get("shots") or [] if isinstance(shot, dict)]
    scoped: list[dict[str, Any]] = []
    for shot in plan.get("shots") or []:
        if not isinstance(shot, dict) or sc.text(shot.get("shot_id")) != target["shot_id"]:
            continue
        if target_type == "shot":
            scoped.append(shot)
        else:
            scenes = [scene for scene in shot.get("scenes") or [] if isinstance(scene, dict) and sc.text(scene.get("scene_id")) == target["scene_id"]]
            scoped.append({**shot, "scenes": scenes})
    return scoped


def video_plan_asset_signature(asset: Any, *, sc: Any) -> dict[str, str]:
    if not isinstance(asset, dict):
        return {"slot": "", "source_type": "", "path": ""}
    return {
        "slot": sc.text(asset.get("slot")),
        "source_type": sc.text(asset.get("source_type") or asset.get("source")),
        "path": sc.text(asset.get("path")),
    }


def video_plan_dialogue_plan_signature(value: Any, *, sc: Any) -> Any:
    if isinstance(value, dict):
        return {
            sc.text(key): video_plan_dialogue_plan_signature(item, sc=sc)
            for key, item in value.items()
            if sc.text(key)
        }
    if isinstance(value, list):
        return [video_plan_dialogue_plan_signature(item, sc=sc) for item in value]
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return sc.text(value)


def video_plan_existing_file_rel(workspace: Path, rel_path: str, *, sc: Any) -> str:
    value = sc.text(rel_path)
    if not value:
        return ""
    try:
        rel_value, target = sc.safe_workspace_rel(workspace, value)
    except HTTPException:
        return ""
    return rel_value if target.exists() and target.is_file() and target.stat().st_size > 0 else ""


def video_plan_consistency_manifest_output(workspace: Path, kind: str, *, sc: Any) -> str:
    manifest = sc.read_json(sc.builder_state_path(workspace, kind))
    if not manifest:
        manifest = sc.read_json(sc.legacy_builder_state_path(workspace, kind))
    if not isinstance(manifest, dict):
        return ""
    return video_plan_existing_file_rel(workspace, sc.text(manifest.get("output")), sc=sc)


def video_plan_consistency_final_image(workspace: Path, item: dict[str, str], *, sc: Any) -> str:
    for suffix in sorted(IMAGE_EXTS):
        candidate = sc.builder_root(workspace) / f"{item['stem']}{suffix}"
        if candidate.exists() and candidate.is_file() and candidate.stat().st_size > 0:
            return sc.builder_rel(workspace, candidate)
    return video_plan_consistency_manifest_output(workspace, item["kind"], sc=sc)


def video_plan_consistency_reference_snapshot(workspace: Path, *, sc: Any) -> dict[str, Any]:
    references: list[dict[str, Any]] = []
    missing: list[str] = []
    for item in CONSISTENCY_FINAL_REFERENCES:
        output_path = video_plan_consistency_final_image(workspace, item, sc=sc)
        available = bool(output_path)
        references.append({
            "kind": item["kind"],
            "label": item["label"],
            "available": available,
            "output_path": output_path,
        })
        if not available:
            missing.append(item["kind"])
    return {
        "status": "ready" if not missing else "missing_reference_images",
        "missing": missing,
        "references": references,
    }


def video_plan_consistency_refs_by_kind(payload: Any, *, sc: Any) -> dict[str, dict[str, Any]]:
    refs = payload.get("references") if isinstance(payload, dict) else []
    return {
        sc.text(item.get("kind")): item
        for item in refs
        if isinstance(item, dict) and sc.text(item.get("kind"))
    }


def video_plan_consistency_payload_matches(plan_payload: dict[str, Any], signature: dict[str, Any], *, sc: Any) -> tuple[bool, str]:
    current = signature.get("consistency_references") if isinstance(signature.get("consistency_references"), dict) else {}
    planned = plan_payload.get("consistency_references") if isinstance(plan_payload.get("consistency_references"), dict) else {}
    current_refs = video_plan_consistency_refs_by_kind(current, sc=sc)
    planned_refs = video_plan_consistency_refs_by_kind(planned, sc=sc)
    for item in CONSISTENCY_FINAL_REFERENCES:
        kind = item["kind"]
        current_ref = current_refs.get(kind) or {}
        planned_ref = planned_refs.get(kind) or {}
        if bool(planned_ref.get("available")) != bool(current_ref.get("available")):
            return False, f"consistency_reference_state_mismatch:{kind}"
        if sc.text(planned_ref.get("output_path")) != sc.text(current_ref.get("output_path")):
            return False, f"consistency_reference_path_mismatch:{kind}"
    return True, "consistency_reference_payload_matched"


def video_plan_input_snapshot(plan: dict[str, Any], target: dict[str, str], *, sc: Any) -> dict[str, Any]:
    shots_payload: list[dict[str, Any]] = []
    for shot_index, shot in enumerate(video_plan_scope_shots(plan, target, sc=sc), start=1):
        scenes_payload: list[dict[str, Any]] = []
        for scene_index, scene in enumerate(shot.get("scenes") or [], start=1):
            if not isinstance(scene, dict):
                continue
            scene_assets = scene.get("working_assets") if isinstance(scene.get("working_assets"), dict) else {}
            dialogues_payload: list[dict[str, Any]] = []
            for dialogue_index, dialogue in enumerate(scene.get("dialogues") or [], start=1):
                if not isinstance(dialogue, dict):
                    continue
                dialogue_assets = dialogue.get("working_assets") if isinstance(dialogue.get("working_assets"), dict) else {}
                dialogues_payload.append({
                    "index": dialogue_index,
                    "dialogue_id": sc.text(dialogue.get("dialogue_id")),
                    "srt_id": sc.text(dialogue.get("srt_id")),
                    "text": sc.text(dialogue.get("text")),
                    "start": sc.number(dialogue.get("start")),
                    "end": sc.number(dialogue.get("end")),
                    "duration": sc.number(dialogue.get("duration")),
                    "dialogue_asset_key": sc.text(dialogue.get("dialogue_asset_key")),
                    "source_image_paths": [sc.text(path) for path in dialogue.get("source_image_paths") or [] if sc.text(path)],
                    "image_path": sc.text(dialogue.get("image_path")),
                    "bound_image_path": sc.text(dialogue.get("bound_image_path")),
                    "video_plan": video_plan_dialogue_plan_signature(dialogue.get("video_plan"), sc=sc),
                    "working_assets": {
                        "audio": video_plan_asset_signature(dialogue_assets.get("audio"), sc=sc),
                        "images": [video_plan_asset_signature(item, sc=sc) for item in dialogue_assets.get("images") or []],
                        "video": video_plan_asset_signature(dialogue_assets.get("video"), sc=sc),
                    },
                })
            scenes_payload.append({
                "index": scene_index,
                "scene_id": sc.text(scene.get("scene_id")),
                "asset_key": sc.text(scene.get("asset_key")),
                "start": sc.number(scene.get("start")),
                "end": sc.number(scene.get("end")),
                "duration": sc.number(scene.get("duration")),
                "working_assets": {
                    "audio": video_plan_asset_signature(scene_assets.get("audio"), sc=sc),
                    "images": [video_plan_asset_signature(item, sc=sc) for item in scene_assets.get("images") or []],
                    "video": video_plan_asset_signature(scene_assets.get("video"), sc=sc),
                },
                "dialogues": dialogues_payload,
            })
        shots_payload.append({
            "index": shot_index,
            "shot_id": sc.text(shot.get("shot_id")),
            "start": sc.number(shot.get("start")),
            "end": sc.number(shot.get("end")),
            "duration": sc.number(shot.get("duration")),
            "scenes": scenes_payload,
        })
    selection = plan.get("storyboard_tts_selection") if isinstance(plan.get("storyboard_tts_selection"), dict) else {}
    tts_selection = {
        "candidate_id": sc.text(selection.get("candidate_id")),
        "voice_id": sc.text(selection.get("voice_id") or selection.get("voice")),
        "voice_source": sc.text(selection.get("voice_source")),
        "prompt": sc.text(selection.get("prompt") or selection.get("prompt_template")),
        "tempo": sc.number(selection.get("tempo")) or 1.0,
    } if selection else {}
    return {"target": target, "tts_selection": tts_selection, "shots": shots_payload}


def video_plan_signature(workspace: Path, plan: dict[str, Any], target: dict[str, str], settings: dict[str, float], *, sc: Any) -> dict[str, Any]:
    input_snapshot = video_plan_input_snapshot(plan, target, sc=sc)
    consistency_snapshot = video_plan_consistency_reference_snapshot(workspace, sc=sc)
    structure_signature = stable_video_plan_hash(input_snapshot)
    media_binding_signature = stable_video_plan_hash({
        "target": target,
        "media": [
            {
                "shot_id": shot.get("shot_id"),
                "scenes": [
                    {
                        "scene_id": scene.get("scene_id"),
                        "working_assets": scene.get("working_assets"),
                        "dialogues": [
                            {
                                "dialogue_asset_key": dialogue.get("dialogue_asset_key"),
                                "source_image_paths": dialogue.get("source_image_paths"),
                                "image_path": dialogue.get("image_path"),
                                "bound_image_path": dialogue.get("bound_image_path"),
                                "video_plan": dialogue.get("video_plan"),
                                "working_assets": dialogue.get("working_assets"),
                            }
                            for dialogue in scene.get("dialogues", [])
                        ],
                    }
                    for scene in shot.get("scenes", [])
                ],
            }
            for shot in input_snapshot["shots"]
        ],
    })
    return {
        "scope_signature": stable_video_plan_hash(target),
        "parameter_signature": stable_video_plan_hash(settings),
        "storyboard_structure_signature": structure_signature,
        "media_binding_signature": media_binding_signature,
        "consistency_reference_signature": stable_video_plan_hash(consistency_snapshot),
        "input_signature": stable_video_plan_hash({"target": target, "settings": settings, "input": input_snapshot, "consistency_references": consistency_snapshot}),
        "consistency_references": consistency_snapshot,
        "source": "koubo_storyboard_saved_edit",
    }


def video_plan_cache_matches(plan_payload: dict[str, Any], cache: dict[str, Any], target: dict[str, str], settings: dict[str, float], signature: dict[str, Any], *, sc: Any) -> tuple[bool, str]:
    if not plan_payload:
        return False, "plan_missing"
    if not cache:
        return False, "ui_cache_missing"
    if "_Image_01" in json.dumps(plan_payload, ensure_ascii=False):
        return False, "legacy_image_01_plan"
    if plan_payload.get("target") != target:
        return False, "target_mismatch"
    plan_settings = plan_payload.get("settings") if isinstance(plan_payload.get("settings"), dict) else {}
    for key, value in settings.items():
        if abs(video_plan_number(plan_settings.get(key), -1) - value) > 0.0001:
            return False, f"settings_mismatch:{key}"
    consistency_match, consistency_reason = video_plan_consistency_payload_matches(plan_payload, signature, sc=sc)
    if not consistency_match:
        return False, consistency_reason
    for key in ("scope_signature", "parameter_signature", "storyboard_structure_signature", "media_binding_signature", "consistency_reference_signature", "input_signature"):
        if sc.text(cache.get(key)) != sc.text(signature.get(key)):
            return False, f"{key}_mismatch"
    return True, "cache_signature_matched"


def register_video_plan_signature_services(ns: Any) -> None:
    for name in SERVICE_EXPORTS:
        setattr(ns, name, globals()[name])
