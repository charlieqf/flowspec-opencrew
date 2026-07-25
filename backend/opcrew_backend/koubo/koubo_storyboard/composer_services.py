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
    "composer_settings",
    "composer_now_iso",
    "composer_execution_is_running",
    "write_composer_execution_state",
    "read_composer_execution_state",
    "composer_execution_payload",
    "composer_video_ready",
    "composer_scene_readiness",
    "composer_storyboard_scene_segment_summary",
    "composer_asset_status",
    "composer_result_status",
    "composer_candidates_from_result",
    "composer_plan_target",
    "composer_storyboard_shot_ids",
    "composer_storyboard_scene_ids",
    "composer_plan_scene_ids",
    "composer_plan_shot",
    "composer_scope_label",
    "composer_incomplete_status",
    "composer_unique",
    "composer_requested_target",
    "composer_target_matches",
    "composer_target_present",
    "composer_target_order",
    "composer_unready_requested_candidate",
    "composer_shot_missing_scene_ids",
    "composer_task_missing_scope",
    "composer_target",
    "composer_candidates_payload",
)


def composer_settings(payload: dict[str, Any], *, sc: Any) -> dict[str, Any]:
    settings = payload.get("settings") if isinstance(payload.get("settings"), dict) else {}
    subtitle_mode = sc.text(settings.get("subtitle_mode"), "hyperframe")
    watermark_mode = sc.text(settings.get("watermark_mode"), "never")
    if subtitle_mode not in {"hyperframe", "none"}:
        raise HTTPException(status_code=400, detail="settings.subtitle_mode must be hyperframe or none")
    if watermark_mode not in {"always", "auto", "never"}:
        raise HTTPException(status_code=400, detail="settings.watermark_mode must be always, auto, or never")
    return {
        "subtitle_mode": subtitle_mode,
        "watermark_mode": watermark_mode,
        "force": bool(settings.get("force", True)),
    }


def composer_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def composer_execution_is_running(state: dict[str, Any], *, sc: Any) -> bool:
    return sc.text(state.get("status")) in {"queued", "running"}


def write_composer_execution_state(workspace: Path, state: dict[str, Any], *, sc: Any) -> None:
    sc.write_json(workspace / COMPOSER_EXECUTION_STATE_REL, state)


def read_composer_execution_state(workspace: Path, *, sc: Any) -> dict[str, Any]:
    state = sc.read_json(workspace / COMPOSER_EXECUTION_STATE_REL)
    if composer_execution_is_running(state, sc=sc):
        job_id = sc.text(state.get("job_id"))
        if not job_id or job_id not in sc.composer_execution_jobs:
            state = {
                **state,
                "status": "failed",
                "error": "Composer job is no longer active. Please start the merge again.",
                "updated_at": composer_now_iso(),
            }
            write_composer_execution_state(workspace, state, sc=sc)
    return state


def composer_execution_payload(task: dict[str, Any], *, sc: Any) -> dict[str, Any]:
    workspace = sc.workspace_for(task)
    state = read_composer_execution_state(workspace, sc=sc)
    result = sc.read_json(workspace / COMPOSER_RESULT_REL)
    target = state.get("target") if isinstance(state.get("target"), dict) else {}
    if not target:
        target = result.get("target") if isinstance(result.get("target"), dict) else {}
    candidates_payload = sc.composer_candidates_payload(task, {"target": target} if target else {}, sc=sc)
    refreshed_plan, meta = sc.load_plan(task, sc=sc)
    return {
        "ok": True,
        "task": task,
        "meta": meta,
        "plan": refreshed_plan,
        "target": target,
        "settings": state.get("settings") if isinstance(state.get("settings"), dict) else {},
        "status": sc.text(state.get("status") or result.get("status") or ""),
        "job_id": sc.text(state.get("job_id")),
        "execution_state": sc.redact_payload(state),
        "tool_result": sc.redact_payload(result),
        "summary": result.get("summary") if isinstance(result.get("summary"), dict) else {},
        "candidates": candidates_payload.get("candidates") or [],
        "candidate_summary": candidates_payload.get("summary") or {},
        "compose_result": candidates_payload.get("compose_result") or {},
    }


def composer_video_ready(workspace: Path, rel_path: str, *, sc: Any) -> bool:
    if not sc.text(rel_path):
        return False
    try:
        _rel, path = sc.safe_workspace_rel(workspace, rel_path)
    except HTTPException:
        return False
    return path.exists() and path.is_file() and path.stat().st_size > 0


def composer_scene_readiness(workspace: Path, scene: dict[str, Any], *, sc: Any) -> dict[str, Any]:
    segments = [segment for segment in scene.get("segments") or [] if isinstance(segment, dict)]
    missing: list[str] = []
    ready_count = 0
    segment_items: list[dict[str, Any]] = []
    for segment in segments:
        outputs = segment.get("planned_outputs") if isinstance(segment.get("planned_outputs"), dict) else {}
        video_path = sc.text(outputs.get("video_path"))
        segment_id = sc.text(segment.get("segment_id")) or f"segment_{len(segment_items) + 1:03d}"
        exists = composer_video_ready(workspace, video_path, sc=sc)
        if exists:
            ready_count += 1
        else:
            missing.append(segment_id or video_path or "segment")
        segment_items.append({
            "segment_id": segment_id,
            "label": segment_id,
            "video_path": video_path,
            "exists": exists,
        })
    return {
        "ready": bool(segments) and not missing,
        "segment_count": len(segments),
        "ready_segment_count": ready_count,
        "missing": missing,
        "segments": segment_items,
    }


def composer_storyboard_scene_segment_summary(workspace: Path, storyboard: dict[str, Any], shot_id: str, scene_id: str, *, sc: Any) -> dict[str, Any]:
    for shot in storyboard.get("shots") or []:
        if not isinstance(shot, dict) or sc.text(shot.get("shot_id")) != shot_id:
            continue
        for scene in shot.get("scenes") or []:
            if not isinstance(scene, dict) or sc.text(scene.get("scene_id")) != scene_id:
                continue
            dialogues = [dialogue for dialogue in scene.get("dialogues") or [] if isinstance(dialogue, dict)]
            ready_count = 0
            for dialogue in dialogues:
                assets = dialogue.get("working_assets") if isinstance(dialogue.get("working_assets"), dict) else {}
                video = assets.get("video") if isinstance(assets.get("video"), dict) else {}
                if composer_video_ready(workspace, sc.text(video.get("path") or dialogue.get("video_path")), sc=sc):
                    ready_count += 1
            return {
                "storyboard_segment_count": len(dialogues),
                "ready_storyboard_segment_count": ready_count,
            }
    return {"storyboard_segment_count": 0, "ready_storyboard_segment_count": 0}


def composer_asset_status(workspace: Path, storyboard: dict[str, Any], shot_id: str, scene_id: str = "", scope: str = "scene", *, sc: Any) -> dict[str, Any]:
    assets: dict[str, Any] = {}
    if scope == "shot_plan":
        assets = storyboard.get("compose_assets") if isinstance(storyboard.get("compose_assets"), dict) else {}
        assets = assets.get("shot_plan") if isinstance(assets.get("shot_plan"), dict) else {}
    else:
        for shot in storyboard.get("shots") or []:
            if not isinstance(shot, dict) or sc.text(shot.get("shot_id")) != shot_id:
                continue
            if scope == "shot":
                shot_assets = shot.get("compose_assets") if isinstance(shot.get("compose_assets"), dict) else {}
                assets = shot_assets.get("shot") if isinstance(shot_assets.get("shot"), dict) else {}
                break
            for scene in shot.get("scenes") or []:
                if isinstance(scene, dict) and sc.text(scene.get("scene_id")) == scene_id:
                    scene_assets = scene.get("compose_assets") if isinstance(scene.get("compose_assets"), dict) else {}
                    assets = scene_assets.get("scene") if isinstance(scene_assets.get("scene"), dict) else {}
                    break
    video_path = sc.text(assets.get("subtitled_video_path") or assets.get("video_path"))
    return {
        "status": sc.text(assets.get("status")),
        "video_path": video_path,
        "exists": composer_video_ready(workspace, video_path, sc=sc),
        "updated_at": assets.get("updated_at") or "",
    }


def composer_result_status(workspace: Path, status: str, outputs: dict[str, Any], path_keys: list[str], updated_at: Any = "", *, sc: Any) -> dict[str, Any]:
    video_path = ""
    for key in path_keys:
        video_path = sc.text(outputs.get(key))
        if video_path:
            break
    return {
        "status": sc.text(status),
        "video_path": video_path,
        "exists": composer_video_ready(workspace, video_path, sc=sc),
        "updated_at": updated_at or "",
    }


def composer_candidates_from_result(workspace: Path, result: dict[str, Any], *, sc: Any) -> list[dict[str, Any]]:
    if not isinstance(result, dict):
        return []
    updated_at = result.get("updated_at") or ""
    candidates: list[dict[str, Any]] = []
    scene_counts_by_shot: dict[str, int] = {}
    for scene_index, scene in enumerate(result.get("scenes") or [], start=1):
        if not isinstance(scene, dict):
            continue
        shot_id = sc.text(scene.get("shot_id"))
        scene_id = sc.text(scene.get("scene_id"))
        outputs = scene.get("outputs") if isinstance(scene.get("outputs"), dict) else {}
        status = composer_result_status(workspace, sc.text(scene.get("status")), outputs, ["scene_subtitled_video_path", "scene_video_path"], updated_at, sc=sc)
        if not shot_id or not scene_id or not status["exists"]:
            continue
        segment_items: list[dict[str, Any]] = []
        for segment_index, segment in enumerate(scene.get("input_segments") or [], start=1):
            if not isinstance(segment, dict):
                continue
            segment_id = sc.text(segment.get("segment_id")) or f"segment_{segment_index:03d}"
            original_path = sc.text(segment.get("video_path"))
            processed_path = sc.text(segment.get("processed_video_path"))
            video_path = processed_path if composer_video_ready(workspace, processed_path, sc=sc) else original_path
            segment_items.append({
                "segment_id": segment_id,
                "label": segment_id,
                "video_path": video_path,
                "exists": composer_video_ready(workspace, video_path, sc=sc),
            })
        scene_counts_by_shot[shot_id] = scene_counts_by_shot.get(shot_id, 0) + 1
        candidates.append({
            "id": f"{shot_id}:{scene_id}",
            "kind": "scene",
            "label": f"{shot_id} / {scene_id}",
            "target": {"target_type": "scene", "shot_id": shot_id, "scene_id": scene_id},
            "ready": True,
            "segment_count": len(segment_items),
            "ready_segment_count": sum(1 for item in segment_items if item.get("exists")),
            "segments": segment_items,
            "missing": [],
            "status": status,
            "order": [scene_index, scene_index, 0],
        })
    shot_items: list[dict[str, Any]] = []
    for shot_index, shot in enumerate(result.get("shots") or [], start=1):
        if not isinstance(shot, dict):
            continue
        shot_id = sc.text(shot.get("shot_id"))
        outputs = shot.get("outputs") if isinstance(shot.get("outputs"), dict) else {}
        status = composer_result_status(workspace, sc.text(shot.get("status")), outputs, ["shot_subtitled_video_path", "shot_video_path"], updated_at, sc=sc)
        if not shot_id or not status["exists"]:
            continue
        scene_count = scene_counts_by_shot.get(shot_id) or len([item for item in shot.get("input_scenes") or [] if isinstance(item, dict)])
        item = {
            "id": shot_id,
            "kind": "shot",
            "label": shot_id,
            "target": {"target_type": "shot", "shot_id": shot_id, "scene_id": ""},
            "ready": True,
            "scene_count": scene_count,
            "ready_scene_count": scene_count,
            "missing": [],
            "status": status,
            "order": [shot_index, 999, 0],
        }
        shot_items.append(item)
        candidates.append(item)
    shot_plan = result.get("shot_plan") if isinstance(result.get("shot_plan"), dict) else {}
    shot_plan_outputs = shot_plan.get("outputs") if isinstance(shot_plan.get("outputs"), dict) else {}
    shot_plan_status = composer_result_status(workspace, sc.text(shot_plan.get("status")), shot_plan_outputs, ["shot_plan_subtitled_video_path", "shot_plan_video_path"], updated_at, sc=sc)
    if shot_plan_status["exists"]:
        candidates.append({
            "id": "shot_plan",
            "kind": "task",
            "label": "ShotPlan",
            "target": {"target_type": "task", "shot_id": "", "scene_id": ""},
            "ready": True,
            "shot_count": len(shot_items) or len([item for item in shot_plan.get("input_shots") or [] if isinstance(item, dict)]),
            "ready_shot_count": len(shot_items) or len([item for item in shot_plan.get("input_shots") or [] if isinstance(item, dict)]),
            "missing": [],
            "status": shot_plan_status,
            "order": [999, 999, 0],
        })
    return sorted(candidates, key=lambda item: item.get("order") or [0, 0, 0])


def composer_plan_target(plan: dict[str, Any], *, sc: Any) -> dict[str, str]:
    target = plan.get("target") if isinstance(plan.get("target"), dict) else {}
    target_type = sc.text(target.get("target_type") or target.get("scope") or "task")
    if target_type == "all":
        target_type = "task"
    if target_type not in {"scene", "shot", "task"}:
        target_type = "task"
    return {
        "target_type": target_type,
        "shot_id": sc.text(target.get("shot_id")),
        "scene_id": sc.text(target.get("scene_id")),
    }


def composer_storyboard_shot_ids(storyboard: dict[str, Any], *, sc: Any) -> list[str]:
    return [
        sc.text(shot.get("shot_id"))
        for shot in storyboard.get("shots") or []
        if isinstance(shot, dict) and sc.text(shot.get("shot_id"))
    ]


def composer_storyboard_scene_ids(storyboard: dict[str, Any], shot_id: str, *, sc: Any) -> list[str]:
    for shot in storyboard.get("shots") or []:
        if not isinstance(shot, dict) or sc.text(shot.get("shot_id")) != shot_id:
            continue
        return [
            sc.text(scene.get("scene_id"))
            for scene in shot.get("scenes") or []
            if isinstance(scene, dict) and sc.text(scene.get("scene_id"))
        ]
    return []


def composer_plan_scene_ids(shot: dict[str, Any], *, sc: Any) -> list[str]:
    return [
        sc.text(scene.get("scene_id"))
        for scene in shot.get("scenes") or []
        if isinstance(scene, dict) and sc.text(scene.get("scene_id"))
    ]


def composer_plan_shot(plan: dict[str, Any], shot_id: str, *, sc: Any) -> dict[str, Any]:
    for shot in plan.get("shots") or []:
        if isinstance(shot, dict) and sc.text(shot.get("shot_id")) == shot_id:
            return shot
    return {}


def composer_scope_label(target: dict[str, str], *, sc: Any) -> str:
    target_type = sc.text(target.get("target_type"))
    if target_type == "task":
        return "整片"
    if target_type == "shot":
        return sc.text(target.get("shot_id")) or "镜头"
    return f"{sc.text(target.get('shot_id'))} / {sc.text(target.get('scene_id'))}".strip(" /") or "场景"


def composer_incomplete_status(status: dict[str, Any]) -> dict[str, Any]:
    return {
        **(status if isinstance(status, dict) else {}),
        "status": "incomplete_plan",
        "exists": False,
        "video_path": "",
    }


def composer_unique(values: list[str], *, sc: Any) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        item = sc.text(value)
        if not item or item in seen:
            continue
        seen.add(item)
        unique.append(item)
    return unique


def composer_requested_target(payload: dict[str, Any], storyboard: dict[str, Any], *, sc: Any) -> dict[str, str]:
    target = payload.get("target") if isinstance(payload.get("target"), dict) else {}
    if not any(sc.text(target.get(key)) for key in ("target_type", "scope", "shot_id", "scene_id")):
        return {}
    return sc.video_plan_target({"target": target}, storyboard, sc=sc)


def composer_target_matches(candidate: dict[str, Any], target: dict[str, str], *, sc: Any) -> bool:
    candidate_target = candidate.get("target") if isinstance(candidate.get("target"), dict) else {}
    return (
        sc.text(candidate_target.get("target_type")) == sc.text(target.get("target_type"))
        and sc.text(candidate_target.get("shot_id")) == sc.text(target.get("shot_id"))
        and sc.text(candidate_target.get("scene_id")) == sc.text(target.get("scene_id"))
    )


def composer_target_present(candidates: list[dict[str, Any]], target: dict[str, str], *, sc: Any) -> bool:
    return any(composer_target_matches(candidate, target, sc=sc) for candidate in candidates)


def composer_target_order(storyboard: dict[str, Any], target: dict[str, str], *, sc: Any) -> list[int]:
    target_type = sc.text(target.get("target_type"))
    if target_type == "task":
        return [999, 999, -1]
    shot_id = sc.text(target.get("shot_id"))
    scene_id = sc.text(target.get("scene_id"))
    for shot_index, shot in enumerate(storyboard.get("shots") or [], start=1):
        if not isinstance(shot, dict) or sc.text(shot.get("shot_id")) != shot_id:
            continue
        if target_type == "shot":
            return [shot_index, 998, -1]
        for scene_index, scene in enumerate(shot.get("scenes") or [], start=1):
            if isinstance(scene, dict) and sc.text(scene.get("scene_id")) == scene_id:
                return [shot_index, scene_index, -1]
        return [shot_index, 0, -1]
    return [998, 998, -1]


def composer_unready_requested_candidate(
    workspace: Path,
    storyboard: dict[str, Any],
    plan: dict[str, Any],
    candidates: list[dict[str, Any]],
    target: dict[str, str], *, sc: Any,
) -> dict[str, Any]:
    target_type = sc.text(target.get("target_type"))
    order = composer_target_order(storyboard, target, sc=sc)
    if target_type == "task":
        shot_items = [item for item in candidates if item.get("kind") == "shot"]
        missing = sc.composer_task_missing_scope(plan, storyboard, sc=sc)
        missing.extend(item["id"] for item in shot_items if not item.get("ready"))
        status = composer_incomplete_status(composer_asset_status(workspace, storyboard, "", "", "shot_plan", sc=sc))
        return {
            "id": "shot_plan",
            "kind": "task",
            "label": "ShotPlan",
            "target": {"target_type": "task", "shot_id": "", "scene_id": ""},
            "ready": False,
            "shot_count": len(composer_storyboard_shot_ids(storyboard, sc=sc)) or len(shot_items),
            "ready_shot_count": sum(1 for item in shot_items if item.get("ready")),
            "missing": composer_unique(missing, sc=sc),
            "status": status,
            "order": order,
        }
    if target_type == "shot":
        shot_id = sc.text(target.get("shot_id"))
        expected_scene_ids = composer_storyboard_scene_ids(storyboard, shot_id, sc=sc)
        scene_items = [
            item
            for item in candidates
            if item.get("kind") == "scene"
            and sc.text((item.get("target") if isinstance(item.get("target"), dict) else {}).get("shot_id")) == shot_id
        ]
        planned_or_ready = {
            sc.text((item.get("target") if isinstance(item.get("target"), dict) else {}).get("scene_id"))
            for item in scene_items
            if item.get("ready")
        }
        missing = [f"{shot_id}:{scene_id}" for scene_id in expected_scene_ids if scene_id not in planned_or_ready]
        missing.extend(item["id"] for item in scene_items if not item.get("ready"))
        status = composer_incomplete_status(composer_asset_status(workspace, storyboard, shot_id, "", "shot", sc=sc))
        return {
            "id": shot_id,
            "kind": "shot",
            "label": shot_id,
            "target": {"target_type": "shot", "shot_id": shot_id, "scene_id": ""},
            "ready": False,
            "scene_count": len(expected_scene_ids) or len(scene_items),
            "ready_scene_count": sum(1 for item in scene_items if item.get("ready")),
            "missing": composer_unique(missing, sc=sc),
            "status": status,
            "order": order,
        }
    shot_id = sc.text(target.get("shot_id"))
    scene_id = sc.text(target.get("scene_id"))
    status = composer_incomplete_status(composer_asset_status(workspace, storyboard, shot_id, scene_id, "scene", sc=sc))
    storyboard_segment_summary = composer_storyboard_scene_segment_summary(workspace, storyboard, shot_id, scene_id, sc=sc)
    return {
        "id": f"{shot_id}:{scene_id}",
        "kind": "scene",
        "label": f"{shot_id} / {scene_id}",
        "target": {"target_type": "scene", "shot_id": shot_id, "scene_id": scene_id},
        "ready": False,
        "segment_count": 0,
        "ready_segment_count": 0,
        **storyboard_segment_summary,
        "segments": [],
        "missing": [f"{shot_id}:{scene_id}"],
        "status": status,
        "order": order,
    }


def composer_shot_missing_scene_ids(plan: dict[str, Any], storyboard: dict[str, Any], shot_id: str, *, sc: Any) -> list[str]:
    planned = composer_plan_scene_ids(composer_plan_shot(plan, shot_id, sc=sc), sc=sc)
    expected = composer_storyboard_scene_ids(storyboard, shot_id, sc=sc) or planned
    return [scene_id for scene_id in expected if scene_id not in planned]


def composer_task_missing_scope(plan: dict[str, Any], storyboard: dict[str, Any], *, sc: Any) -> list[str]:
    planned_shots = {
        sc.text(shot.get("shot_id")): shot
        for shot in plan.get("shots") or []
        if isinstance(shot, dict) and sc.text(shot.get("shot_id"))
    }
    missing: list[str] = []
    for shot_id in composer_storyboard_shot_ids(storyboard, sc=sc):
        shot = planned_shots.get(shot_id)
        if not shot:
            missing.append(shot_id)
            continue
        for scene_id in composer_shot_missing_scene_ids(plan, storyboard, shot_id, sc=sc):
            missing.append(f"{shot_id}:{scene_id}")
    return missing


def composer_target(payload: dict[str, Any], plan: dict[str, Any], storyboard: dict[str, Any], *, sc: Any) -> dict[str, str]:
    if not plan:
        raise HTTPException(status_code=404, detail="VideoPlan does not exist. Generate and execute a VideoPlan before composing.")
    target = sc.video_plan_target(payload, storyboard, sc=sc)
    plan_target = composer_plan_target(plan, sc=sc)
    plan_target_type = plan_target["target_type"]
    requested_type = target["target_type"]

    if requested_type == "task":
        missing = composer_task_missing_scope(plan, storyboard, sc=sc)
        if plan_target_type != "task" or missing:
            raise HTTPException(
                status_code=409,
                detail=f"当前 VideoPlan 只覆盖 {composer_scope_label(plan_target, sc=sc)}；要合成整片，请先切换到全部范围，重新生成并执行 VideoPlan。",
            )
    elif requested_type == "shot":
        missing = composer_shot_missing_scene_ids(plan, storyboard, target["shot_id"], sc=sc)
        if plan_target_type == "scene" or missing:
            raise HTTPException(
                status_code=409,
                detail=f"当前 VideoPlan 只覆盖 {composer_scope_label(plan_target, sc=sc)}；要合成完整镜头 {target['shot_id']}，请先切换到镜头范围，重新生成并执行 VideoPlan。",
            )
    else:
        plan_scene_ids = composer_plan_scene_ids(composer_plan_shot(plan, target["shot_id"], sc=sc), sc=sc)
        if target["scene_id"] not in plan_scene_ids:
            raise HTTPException(
                status_code=409,
                detail=f"当前 VideoPlan 只覆盖 {composer_scope_label(plan_target, sc=sc)}；请先重新生成覆盖目标场景的 VideoPlan。",
            )
    return target


def composer_candidates_payload(task: dict[str, Any], payload: dict[str, Any] | None = None, *, sc: Any) -> dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {}
    workspace = sc.workspace_for(task)
    plan = sc.video_plan_with_hash(sc.read_json(workspace / VIDEO_PLAN_REL), sc=sc)
    storyboard, _meta = sc.load_plan(task, sc=sc)
    requested_target = composer_requested_target(payload, storyboard, sc=sc)
    candidates: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if not plan:
        return {
            "ok": True,
            "task": task,
            "plan_hash": "",
            "summary": {"scene_count": 0, "shot_count": 0, "ready_count": 0},
            "candidates": [],
            "requested_target": requested_target,
            "plan_target": {},
            "warnings": [{"code": "video_plan_missing", "message": "Generate VideoPlan before composing videos."}],
            "compose_result": sc.redact_payload(sc.read_json(workspace / COMPOSER_RESULT_REL)),
        }
    plan_target = composer_plan_target(plan, sc=sc)
    if plan_target["target_type"] == "scene":
        warnings.append({
            "code": "composer_video_plan_scope_scene",
            "message": f"当前 VideoPlan 只覆盖 {composer_scope_label(plan_target, sc=sc)}；要合成完整镜头或整片，请先切换范围，重新生成并执行 VideoPlan。",
        })
    elif plan_target["target_type"] == "shot" and len(composer_storyboard_shot_ids(storyboard, sc=sc)) > 1:
        warnings.append({
            "code": "composer_video_plan_scope_shot",
            "message": f"当前 VideoPlan 只覆盖 {composer_scope_label(plan_target, sc=sc)}；要合成整片，请先切换到全部范围，重新生成并执行 VideoPlan。",
        })
    for shot_index, shot in enumerate(plan.get("shots") or [], start=1):
        if not isinstance(shot, dict):
            continue
        shot_id = sc.text(shot.get("shot_id"))
        scene_candidates: list[dict[str, Any]] = []
        planned_scene_ids = composer_plan_scene_ids(shot, sc=sc)
        expected_scene_ids = composer_storyboard_scene_ids(storyboard, shot_id, sc=sc) or planned_scene_ids
        for scene_index, scene in enumerate(shot.get("scenes") or [], start=1):
            if not isinstance(scene, dict):
                continue
            scene_id = sc.text(scene.get("scene_id"))
            readiness = composer_scene_readiness(workspace, scene, sc=sc)
            storyboard_segment_summary = composer_storyboard_scene_segment_summary(workspace, storyboard, shot_id, scene_id, sc=sc)
            status = composer_asset_status(workspace, storyboard, shot_id, scene_id, "scene", sc=sc)
            target = {"target_type": "scene", "shot_id": shot_id, "scene_id": scene_id}
            item = {
                "id": f"{shot_id}:{scene_id}",
                "kind": "scene",
                "label": f"{shot_id} / {scene_id}",
                "target": target,
                "ready": readiness["ready"],
                "segment_count": readiness["segment_count"],
                "ready_segment_count": readiness["ready_segment_count"],
                **storyboard_segment_summary,
                "segments": readiness["segments"],
                "missing": readiness["missing"],
                "status": status,
                "order": [shot_index, scene_index, 0],
            }
            scene_candidates.append(item)
            candidates.append(item)
        missing_scene_ids = [scene_id for scene_id in expected_scene_ids if scene_id not in planned_scene_ids]
        missing = [f"{shot_id}:{scene_id}" for scene_id in missing_scene_ids]
        missing.extend(item["id"] for item in scene_candidates if not item["ready"])
        scope_allows_shot = plan_target["target_type"] in {"shot", "task"}
        shot_complete = scope_allows_shot and bool(expected_scene_ids) and not missing_scene_ids
        shot_ready = shot_complete and bool(scene_candidates) and all(item["ready"] for item in scene_candidates)
        shot_status = composer_asset_status(workspace, storyboard, shot_id, "", "shot", sc=sc)
        if not shot_complete:
            shot_status = composer_incomplete_status(shot_status)
        candidates.append({
            "id": shot_id,
            "kind": "shot",
            "label": shot_id,
            "target": {"target_type": "shot", "shot_id": shot_id, "scene_id": ""},
            "ready": shot_ready,
            "scene_count": len(expected_scene_ids) or len(scene_candidates),
            "ready_scene_count": sum(1 for item in scene_candidates if item["ready"]),
            "missing": missing,
            "status": shot_status,
            "order": [shot_index, 999, 0],
        })
    shot_items = [item for item in candidates if item.get("kind") == "shot"]
    missing_task_scope = composer_task_missing_scope(plan, storyboard, sc=sc)
    task_scope_complete = plan_target["target_type"] == "task" and not missing_task_scope
    task_ready = task_scope_complete and bool(shot_items) and all(item.get("ready") for item in shot_items)
    if plan_target["target_type"] == "task":
        task_status = composer_asset_status(workspace, storyboard, "", "", "shot_plan", sc=sc)
        if not task_scope_complete:
            task_status = composer_incomplete_status(task_status)
        candidates.append({
            "id": "shot_plan",
            "kind": "task",
            "label": "ShotPlan",
            "target": {"target_type": "task", "shot_id": "", "scene_id": ""},
            "ready": task_ready,
            "shot_count": len(composer_storyboard_shot_ids(storyboard, sc=sc)) or len(shot_items),
            "ready_shot_count": sum(1 for item in shot_items if item.get("ready")),
            "missing": missing_task_scope + [item["id"] for item in shot_items if not item.get("ready")],
            "status": task_status,
            "order": [999, 999, 0],
        })
    compose_result = sc.redact_payload(sc.read_json(workspace / COMPOSER_RESULT_REL))
    if not candidates:
        candidates = composer_candidates_from_result(workspace, compose_result, sc=sc)
    if requested_target and not composer_target_present(candidates, requested_target, sc=sc):
        candidates.append(composer_unready_requested_candidate(workspace, storyboard, plan, candidates, requested_target, sc=sc))
    requested_item = next((item for item in candidates if requested_target and composer_target_matches(item, requested_target, sc=sc)), None)
    if requested_target and (not requested_item or not requested_item.get("ready")):
        warnings.append({
            "code": "composer_requested_target_not_ready",
            "message": f"当前选择是 {composer_scope_label(requested_target, sc=sc)}，但当前 VideoPlan 只覆盖 {composer_scope_label(plan_target, sc=sc)}；请先重新生成并执行当前选择范围的 VideoPlan。",
        })
    return {
        "ok": True,
        "task": task,
        "plan_hash": sc.text(plan.get("plan_hash")),
        "requested_target": requested_target,
        "plan_target": plan_target,
        "summary": {
            "scene_count": sum(1 for item in candidates if item.get("kind") == "scene"),
            "shot_count": sum(1 for item in candidates if item.get("kind") == "shot"),
            "ready_count": sum(1 for item in candidates if item.get("ready")),
        },
        "candidates": sorted(candidates, key=lambda item: item.get("order") or [0, 0, 0]),
        "warnings": warnings,
        "compose_result": compose_result,
    }


def register_composer_services(ns: Any) -> None:
    for name in SERVICE_EXPORTS:
        setattr(ns, name, globals()[name])
