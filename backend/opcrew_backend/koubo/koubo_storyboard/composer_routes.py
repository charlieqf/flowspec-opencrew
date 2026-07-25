from __future__ import annotations

import asyncio
import json
import time
import urllib.parse
import uuid
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from opcrew_backend.context import now_ms

from .constants import *


def register_composer_routes(router: APIRouter, deps: Any) -> None:

    @router.get("/api/koubo-storyboard/tasks/{task_id}/composer/candidates")
    async def composer_candidates(task_id: int, target_type: str = "", shot_id: str = "", scene_id: str = "", action_source: str = "") -> dict[str, Any]:
        payload = {}
        if target_type or shot_id or scene_id:
            payload = {"target": {"target_type": target_type, "shot_id": shot_id, "scene_id": scene_id}}
        if action_source:
            payload["action_source"] = action_source
        task = deps.task_or_404(task_id)
        result = deps.composer_candidates_payload(task, payload, sc=deps)
        warnings = [
            {"code": deps.text(item.get("code")), "message": deps.text(item.get("message"))}
            for item in result.get("warnings") or []
            if isinstance(item, dict)
        ]
        event_payload = {
            "task_id": task_id,
            "session_id": int(task["session_id"]),
            "workspace_dir": str(deps.workspace_for(task)),
            "action_source": deps.text(action_source),
            "requested_target": result.get("requested_target") if isinstance(result.get("requested_target"), dict) else {},
            "current_plan_target": result.get("plan_target") if isinstance(result.get("plan_target"), dict) else {},
            "plan_hash": deps.text(result.get("plan_hash")),
            "candidate_count": len(result.get("candidates") or []),
            "ready_count": int(deps.number((result.get("summary") or {}).get("ready_count"))),
            "warnings": warnings,
        }
        deps.add_event(int(task["session_id"]), "koubo_storyboard.composer.candidates_checked", event_payload)
        requested_type = deps.text(event_payload["requested_target"].get("target_type"))
        current_type = deps.text(event_payload["current_plan_target"].get("target_type"))
        if requested_type == "task" and current_type in {"scene", "shot"}:
            deps.add_event(int(task["session_id"]), "koubo_storyboard.composer.scope_mismatch_warning", event_payload)
        return result

    @router.post("/api/koubo-storyboard/tasks/{task_id}/composer/execute")
    async def execute_composer(task_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        async with deps.composer_lock:
            task = deps.task_or_404(task_id)
            workspace = deps.workspace_for(task)
            storyboard_plan, _meta = deps.load_plan(task, sc=deps)
            generated_plan = deps.video_plan_with_hash(deps.read_json(workspace / VIDEO_PLAN_REL), sc=deps)
            target = deps.composer_target(payload, generated_plan, storyboard_plan, sc=deps)
            settings = deps.composer_settings(payload, sc=deps)
            state = deps.read_composer_execution_state(workspace, sc=deps)
            if deps.composer_execution_is_running(state, sc=deps):
                return {
                    **deps.composer_execution_payload(task, sc=deps),
                    "already_running": True,
                }
            job_id = f"composer_{now_ms()}_{uuid.uuid4().hex[:8]}"
            now_value = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            initial_state = {
                "schema_version": "koubo_video_plan_composer_state_0.1",
                "job_id": job_id,
                "status": "queued",
                "target": target,
                "settings": settings,
                "started_at": now_value,
                "updated_at": now_value,
                "returncode": None,
                "tool_status": "",
                "summary": {},
                "error": "",
            }
            deps.write_composer_execution_state(workspace, initial_state, sc=deps)
            job = asyncio.create_task(deps.run_composer_background(task_id, int(task["session_id"]), workspace, job_id, target, settings, sc=deps))
            deps.composer_execution_jobs[job_id] = job
            deps.add_event(int(task["session_id"]), "koubo_storyboard.composer.started", {
                "task_id": task_id,
                "workspace_dir": str(workspace),
                "job_id": job_id,
                "target": target,
                "settings": settings,
            })
            return {
                **deps.composer_execution_payload(task, sc=deps),
                "job_id": job_id,
            }

    @router.get("/api/koubo-storyboard/tasks/{task_id}/composer/execution")
    async def composer_execution(task_id: int) -> dict[str, Any]:
        return deps.composer_execution_payload(deps.task_or_404(task_id), sc=deps)
