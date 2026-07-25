from __future__ import annotations

import asyncio
import json
import time
import urllib.parse
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse

from opcrew_backend.context import now_ms
from opcrew_backend.workflow_modes import infer_openclip_workflow_mode, storyboard_meta_for_workflow
from opcrew_backend.model_policy import SURFACE_KOUBO_HOST_PRODUCT_PROMPT, SURFACE_KOUBO_TTS_TIMING, mask_model_fields_for_role, mask_model_fields_under_keys_for_role, request_role
from opcrew_backend.routes.media_model_config import load_configured_active_provider
from opcrew_backend.services.tts_voice_aliases import (
    normalize_storyboard_tts_selection,
    storyboard_tts_candidate_is_cloud_clone,
    storyboard_tts_candidate_is_inactive_cloud_clone,
)

from .constants import *
from .dance_mimic_stale import dance_mimic_stale_summary, dance_mimic_stale_warnings, is_dance_mimic_storyboard


TTS_TIMING_PAYLOAD_KEYS = {"storyboard_tts_selection", "tts_selection", "timing_model", "selection", "top_candidates", "recommendations"}


def storyboard_task_list_summary(task: dict[str, Any]) -> dict[str, int]:
    return {
        "id": int(task["id"]),
        "session_id": int(task["session_id"]),
    }


def register_task_routes(router: APIRouter, deps: Any) -> None:

    def mask_task_payload(role: str, payload: dict[str, Any]) -> dict[str, Any]:
        payload = mask_model_fields_for_role(deps.ctx, role, SURFACE_KOUBO_HOST_PRODUCT_PROMPT, payload)
        return mask_model_fields_under_keys_for_role(deps.ctx, role, SURFACE_KOUBO_TTS_TIMING, payload, TTS_TIMING_PAYLOAD_KEYS)

    @router.get("/api/koubo-storyboard/tasks")
    def list_tasks(request: Request) -> dict[str, Any]:
        role = request_role(request)
        items = []
        for task in deps.repo.list_tasks():
            workspace = deps.workspace_for(task)
            if (workspace / SOURCE_REL).exists():
                source = deps.read_json(workspace / SOURCE_REL)
                _current_source, source_signature, _source_refreshed = deps.current_storyboard_source(workspace, source)
                edit = deps.read_json(workspace / EDIT_REL)
                edit_exists = bool(edit) and deps.storyboard_edit_matches_source(edit, source_signature)
                workflow_meta = storyboard_meta_for_workflow(infer_openclip_workflow_mode(task, workspace=workspace))
                items.append({
                    "task": storyboard_task_list_summary(task),
                    "meta": {
                        "title": workflow_meta["title"],
                        "source_type": workflow_meta["source_type"],
                        "workflow_mode": workflow_meta["workflow_mode"],
                        "analysis_task_id": int(task["id"]),
                        "task_id": int(task["id"]),
                        "analysis_session_id": int(task["session_id"]),
                        "source_path": SOURCE_REL,
                        "edit_path": EDIT_REL,
                        "has_saved_edit": edit_exists,
                    },
                })
        return mask_task_payload(role, {"items": items})

    @router.get("/api/koubo-storyboard/tasks/{task_id}")
    async def detail(request: Request, task_id: int) -> dict[str, Any]:
        role = request_role(request)
        task = deps.task_or_404(task_id)
        workspace = deps.workspace_for(task)
        if not (workspace / SOURCE_REL).exists():
            return mask_task_payload(role, deps.empty_asset_library_payload(task, workspace, sc=deps))
        plan, meta = deps.load_plan(task, sc=deps)
        plan = normalize_storyboard_tts_selection(
            deps.ctx,
            plan,
            active_clone_provider=load_configured_active_provider(deps.ctx, "voice-clone"),
        )
        payload = {"ok": True, "task": task, "meta": meta, "plan": plan}
        if is_dance_mimic_storyboard(task, workspace, meta=meta, plan=plan):
            stale = dance_mimic_stale_summary(workspace)
            warnings = dance_mimic_stale_warnings(stale)
            meta = {**meta, "stale": stale}
            if warnings:
                meta["warnings"] = [*(meta.get("warnings") if isinstance(meta.get("warnings"), list) else []), *warnings]
            payload = {**payload, "meta": meta, "stale": stale, "warnings": warnings}
        return mask_task_payload(role, payload)

    @router.get("/api/koubo-storyboard/tasks/{task_id}/tts-builder-candidates")
    async def tts_builder_candidates(request: Request, task_id: int) -> dict[str, Any]:
        role = request_role(request)
        task = deps.task_or_404(task_id)
        workspace = deps.workspace_for(task)
        candidate_payload = deps.read_json(workspace / "SessionOutput/tts/tts_builder_candidates.json")
        rows = candidate_payload.get("candidates") if isinstance(candidate_payload.get("candidates"), list) else []
        active_clone_provider = load_configured_active_provider(deps.ctx, "voice-clone")
        selected = candidate_payload.get("selected_tts_candidate") if isinstance(candidate_payload.get("selected_tts_candidate"), dict) else {}
        if not selected:
            selected = next((item for item in rows if isinstance(item, dict) and (item.get("selected") or item.get("is_selected"))), {})
        if not selected and rows:
            selected = rows[0] if isinstance(rows[0], dict) else {}
        normalized = normalize_storyboard_tts_selection(
            deps.ctx,
            {
                "storyboard_tts_selection": {
                    **selected,
                    "top_candidates": rows,
                    "recommendations": rows,
                }
            },
            active_clone_provider=active_clone_provider,
        )
        normalized_selection = normalized.get("storyboard_tts_selection") if isinstance(normalized.get("storyboard_tts_selection"), dict) else {}
        candidates = normalized_selection.get("top_candidates") if isinstance(normalized_selection.get("top_candidates"), list) else []
        inactive_cloud_candidate_count = sum(
            1
            for item in rows
            if isinstance(item, dict) and storyboard_tts_candidate_is_inactive_cloud_clone(item, active_clone_provider)
        )
        has_active_cloud_candidate = any(
            storyboard_tts_candidate_is_cloud_clone(item)
            for item in candidates
            if isinstance(item, dict)
        )
        result = {
            **candidate_payload,
            "candidates": candidates,
            "top_candidates": candidates,
            "selected_candidate": normalized_selection,
            "selected_tts_candidate": normalized_selection,
            "selected_candidate_id": normalized_selection.get("candidate_id") or "",
            "requires_cloud_clone_refresh": bool(inactive_cloud_candidate_count and not has_active_cloud_candidate),
            "inactive_cloud_candidate_count": inactive_cloud_candidate_count,
        }
        return mask_task_payload(role, result)

    @router.put("/api/koubo-storyboard/tasks/{task_id}/video-plan/settings")
    async def save_video_plan_settings(task_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        workspace = deps.workspace_for(task)
        settings = deps.video_plan_settings(payload)
        saved = {
            **settings,
            "task_id": task_id,
            "session_id": int(task["session_id"]),
            "updated_at": now_ms(),
        }
        deps.write_json(workspace / VIDEO_PLAN_SETTINGS_REL, saved)
        deps.add_event(int(task["session_id"]), "koubo_storyboard.video_plan.settings_saved", {
            "task_id": task_id,
            "session_id": int(task["session_id"]),
            "workspace_dir": str(workspace),
            "settings": settings,
            "settings_path": VIDEO_PLAN_SETTINGS_REL,
        })
        return {"ok": True, "settings": settings, "settings_path": VIDEO_PLAN_SETTINGS_REL}
