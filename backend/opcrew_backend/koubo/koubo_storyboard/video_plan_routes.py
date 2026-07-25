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
from opcrew_backend.routes.media_model_config import customer_media_public_alias_target, load_agent_model_aliases, load_config

from .constants import *
from .dance_mimic_stale import clear_dance_mimic_stale_items, dance_mimic_stale_item_is_active, dance_mimic_stale_summary, is_dance_mimic_storyboard

IMAGE_API_SETTINGS_REL = "SessionContext/ImageAPISettings.json"
IMAGES_AGENT_SETTINGS_REL = "SessionContext/ImagesAgentSettings.json"
VIDEO_API_SETTINGS_REL = "SessionContext/VideoAPISettings.json"
VIDEOS_AGENT_SETTINGS_REL = "SessionContext/VideosAgentSettings.json"


def register_video_plan_routes(router: APIRouter, deps: Any) -> None:

    def settings_source_for(existing: dict[str, Any]) -> dict[str, Any]:
        if isinstance(existing, dict) and isinstance(existing.get("settings"), dict):
            return existing["settings"]
        return existing if isinstance(existing, dict) else {}

    def clean_model_selection_value(value: Any) -> str:
        text = deps.text(value)
        return "" if text in {"[model]", "[provider]"} else text

    def agent_media_alias_from_payload(kind: str, payload: dict[str, Any]) -> str:
        if kind == "image":
            return deps.text(
                payload.get("agentImageAlias")
                or payload.get("agent_image_alias")
                or payload.get("agent_model_alias")
                or payload.get("model_alias")
                or payload.get("alias")
            )
        return deps.text(
            payload.get("agentVideoAlias")
            or payload.get("agent_video_alias")
            or payload.get("agent_model_alias")
            or payload.get("model_alias")
            or payload.get("alias")
        )

    def resolve_agent_media_alias(kind: str, alias: str, *, strict: bool = True) -> tuple[str, str]:
        alias_value = deps.text(alias)
        if not alias_value:
            return "", ""
        for item in load_agent_model_aliases(deps.ctx, kind):
            if deps.text(item.get("alias")) == alias_value:
                provider = deps.text(item.get("provider"))
                model = deps.text(item.get("model"))
                if provider and model:
                    return provider, model
        provider, model = customer_media_public_alias_target(load_config(deps.ctx, kind), kind, alias_value)
        if provider and model:
            return provider, model
        if strict:
            label = "image" if kind == "image" else "video"
            raise HTTPException(status_code=400, detail=f"Select a valid Agent {label} model before generating.")
        return "", ""

    def media_model_selection_from_source(kind: str, source: dict[str, Any], source_name: str, *, strict_alias: bool) -> dict[str, Any]:
        if not isinstance(source, dict):
            return {}
        alias = agent_media_alias_from_payload(kind, source)
        if alias:
            provider, model = resolve_agent_media_alias(kind, alias, strict=strict_alias)
            if provider and model:
                return {
                    f"agent{kind.title()}Alias": alias,
                    f"{kind}_provider": provider,
                    f"{kind}_model": model,
                    "source": source_name,
                }
            return {}
        provider = clean_model_selection_value(source.get(f"{kind}_provider") or source.get("provider"))
        model = clean_model_selection_value(source.get(f"{kind}_model") or source.get("model"))
        if provider and model:
            return {
                f"agent{kind.title()}Alias": "",
                f"{kind}_provider": provider,
                f"{kind}_model": model,
                "source": source_name,
            }
        return {}

    def video_plan_execution_model_selection(task: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        request_payload = payload if isinstance(payload, dict) else {}
        selection: dict[str, Any] = {}
        for kind in ("image", "video"):
            request_selection = media_model_selection_from_source(kind, request_payload, "request", strict_alias=True)
            if request_selection:
                selection.update(request_selection)
        workspace = deps.workspace_for(task)
        saved_sources = (
            ("image", IMAGE_API_SETTINGS_REL),
            ("image", IMAGES_AGENT_SETTINGS_REL),
            ("video", VIDEO_API_SETTINGS_REL),
            ("video", VIDEOS_AGENT_SETTINGS_REL),
        )
        for kind, rel_path in saved_sources:
            if selection.get(f"{kind}_provider") and selection.get(f"{kind}_model"):
                continue
            saved = deps.read_json(workspace / rel_path)
            saved_selection = media_model_selection_from_source(kind, settings_source_for(saved), rel_path, strict_alias=False)
            if saved_selection:
                selection.update(saved_selection)
        return selection

    def public_video_plan_model_selection(selection: dict[str, Any]) -> dict[str, str]:
        if not isinstance(selection, dict):
            return {}
        payload: dict[str, str] = {}
        image_alias = deps.text(selection.get("agentImageAlias"))
        video_alias = deps.text(selection.get("agentVideoAlias"))
        if image_alias:
            payload["agentImageAlias"] = image_alias
        if video_alias:
            payload["agentVideoAlias"] = video_alias
        return payload

    def event_plan_target(plan: dict[str, Any]) -> dict[str, str]:
        target = plan.get("target") if isinstance(plan.get("target"), dict) else {}
        target_type = deps.text(target.get("target_type") or target.get("scope") or "")
        if target_type == "all":
            target_type = "task"
        if target_type not in {"scene", "shot", "task"}:
            return {}
        if target_type == "task":
            return {"target_type": "task", "shot_id": "", "scene_id": ""}
        if target_type == "shot":
            return {"target_type": "shot", "shot_id": deps.text(target.get("shot_id")), "scene_id": ""}
        return {
            "target_type": "scene",
            "shot_id": deps.text(target.get("shot_id")),
            "scene_id": deps.text(target.get("scene_id")),
        }

    def initial_video_plan_execution_marker(workspace, plan: dict[str, Any], now_value: str) -> dict[str, Any]:
        artifacts = deps.video_plan_artifact_status(workspace, plan, sc=deps).get("segments") or {}
        slot_to_step = [("audio", "audio"), ("image", "image"), ("raw_video", "video"), ("final_video", "sync")]
        for shot in plan.get("shots") or []:
            for scene in shot.get("scenes") or []:
                if deps.text(scene.get("status")) != "planned":
                    continue
                for segment in scene.get("segments") or []:
                    segment_id = deps.text(segment.get("segment_id"))
                    if not segment_id or deps.text(segment.get("status")) in {"blocked", "skipped"}:
                        continue
                    slot_states = (artifacts.get(segment_id) or {}).get("slot_states") or {}
                    for slot_key, step in slot_to_step:
                        slot = slot_states.get(slot_key) if isinstance(slot_states, dict) else {}
                        if deps.text(slot.get("ui_tone") or slot.get("tone")) != "pending":
                            continue
                        return {
                            "current_segment_id": segment_id,
                            "current_step": step,
                            "current_step_status": "running_generate",
                            "segments": {
                                segment_id: {
                                    "segment_id": segment_id,
                                    "steps": {
                                        step: {
                                            "status": "running_generate",
                                            "updated_at": now_value,
                                        }
                                    },
                                }
                            },
                        }
        return {"current_segment_id": "", "current_step": "", "current_step_status": "", "segments": {}}

    def bind_existing_final_videos(task: dict[str, Any], workspace, plan: dict[str, Any]) -> list[dict[str, str]]:
        edit_plan = deps.read_json(workspace / EDIT_REL)
        if not isinstance(edit_plan, dict) or edit_plan.get("schema_version") != "koubo_storyboard_edit_0.1":
            return []
        actions: list[dict[str, str]] = []
        for shot in plan.get("shots") or []:
            if not isinstance(shot, dict):
                continue
            for scene in shot.get("scenes") or []:
                if not isinstance(scene, dict):
                    continue
                for segment in scene.get("segments") or []:
                    if not isinstance(segment, dict):
                        continue
                    outputs = segment.get("planned_outputs") if isinstance(segment.get("planned_outputs"), dict) else {}
                    final_rel = deps.text(outputs.get("final_video_path") or outputs.get("video_path"))
                    if not final_rel:
                        continue
                    try:
                        _rel, final_path = deps.safe_workspace_rel(workspace, final_rel)
                    except HTTPException:
                        continue
                    if not final_path.exists() or not final_path.is_file() or final_path.stat().st_size <= 0:
                        continue
                    asset_key = deps.video_plan_segment_asset_key(segment, outputs, sc=deps)
                    dialogue = deps.storyboard_dialogue_for_asset(edit_plan, asset_key, sc=deps)
                    if not dialogue:
                        continue
                    assets = deps.ensure_dialogue_working_assets(dialogue, sc=deps)
                    current_video = deps.text((assets.get("video") or {}).get("path"))
                    if current_video == final_rel:
                        continue
                    if current_video:
                        continue
                    assets["video"] = {"slot": "Video_Final", "source_type": "generated", "path": final_rel}
                    actions.append({
                        "segment_id": deps.text(segment.get("segment_id")),
                        "asset_key": asset_key,
                        "path": final_rel,
                    })
        if actions:
            deps.save_edit_and_source_storyboard(task, workspace, edit_plan, sc=deps)
        return actions

    @router.post("/api/koubo-storyboard/tasks/{task_id}/video-plan")
    async def video_plan(task_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        if deps.video_plan_lock.locked():
            raise HTTPException(status_code=409, detail="VideoPlan is already checking or generating. Please wait for the current run to finish.")
        async with deps.video_plan_lock:
            task = deps.task_or_404(task_id)
            workspace = deps.workspace_for(task)
            running_state = deps.read_json(workspace / VIDEO_PLAN_EXECUTION_STATE_REL)
            if deps.video_plan_execution_is_running(running_state, sc=deps):
                running_plan = deps.video_plan_with_hash(deps.read_json(workspace / VIDEO_PLAN_REL), sc=deps)
                if not running_plan:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "video_plan_execution_running",
                            "message": "当前生成计划仍在执行，请等待完成后再重新生成。",
                            "job_id": deps.text(running_state.get("job_id")),
                        },
                    )
                running_target = event_plan_target(running_plan) or {"target_type": "task", "shot_id": "", "scene_id": ""}
                running_settings = deps.video_plan_settings({
                    "settings": running_plan.get("settings") if isinstance(running_plan.get("settings"), dict) else {}
                })
                return {
                    **deps.video_plan_execution_payload(workspace, running_plan, sc=deps),
                    "ok": True,
                    "already_running": True,
                    "cache_status": "execution_running",
                    "reason": "video_plan_execution_running",
                    "task": task,
                    "target": running_target,
                    "settings": running_settings,
                    "plan": running_plan,
                    "summary": running_plan.get("summary") or {},
                    "status": deps.text(running_state.get("status"), "running"),
                    "cleanup_actions": [],
                }
            current_plan, _meta = deps.load_plan(task, sc=deps)
            deps.save_edit_and_source_storyboard(task, workspace, current_plan, sc=deps)
            target = deps.video_plan_target(payload, current_plan, sc=deps)
            settings = deps.video_plan_settings(payload)
            signature = deps.video_plan_signature(workspace, current_plan, target, settings, sc=deps)
            is_dance_mimic = is_dance_mimic_storyboard(task, workspace, plan=current_plan)
            stale = dance_mimic_stale_summary(workspace) if is_dance_mimic else {}
            video_plan_stale = is_dance_mimic and dance_mimic_stale_item_is_active(stale, "video_generation_plan")
            force_requested = bool(payload.get("force"))
            plan_path = workspace / VIDEO_PLAN_REL
            cache_path = workspace / VIDEO_PLAN_UI_CACHE_REL
            existing_plan = deps.read_json(plan_path)
            existing_cache = deps.read_json(cache_path)
            previous_plan = deps.video_plan_with_hash(existing_plan, sc=deps)
            previous_target = event_plan_target(previous_plan)
            previous_plan_hash = deps.text(previous_plan.get("plan_hash"))
            cache_match, cache_reason = deps.video_plan_cache_matches(existing_plan, existing_cache, target, settings, signature, sc=deps)
            if force_requested and cache_match:
                cache_match = False
                cache_reason = "force_requested"
            if video_plan_stale and cache_match:
                cache_match = False
                cache_reason = "dance_mimic_video_plan_stale"
            action_source = deps.text(payload.get("action_source") or payload.get("actionSource"))
            base_payload = {
                "task_id": task_id,
                "session_id": int(task["session_id"]),
                "workspace_dir": str(workspace),
                "target": target,
                "new_target": target,
                "previous_target": previous_target,
                "previous_plan_hash": previous_plan_hash,
                "settings": settings,
                "signature": signature,
                "force": force_requested,
                "action_source": action_source,
            }
            if cache_match:
                existing_plan = deps.video_plan_with_hash(existing_plan, sc=deps)
                deps.add_event(int(task["session_id"]), "koubo_storyboard.video_plan.cache_hit", {**base_payload, "reason": cache_reason})
                return {
                    "ok": True,
                    "cache_status": "cache_hit",
                    "reason": cache_reason,
                    "task": task,
                    "target": target,
                    "settings": settings,
                    "signature": signature,
                    "ui_cache": existing_cache,
                    "plan": existing_plan,
                    "summary": existing_plan.get("summary") or {},
                    **deps.video_plan_execution_payload(workspace, existing_plan, sc=deps),
                    "artifact_status": deps.video_plan_artifact_status(workspace, existing_plan, sc=deps),
                    "status": "completed",
                    "cleanup_actions": [],
                    **({"stale": stale} if is_dance_mimic else {}),
                }

            cleanup_actions = deps.reset_video_plan_outputs(workspace)
            deps.add_event(int(task["session_id"]), "koubo_storyboard.video_plan.rerun_started", {**base_payload, "reason": cache_reason, "cleanup_actions": cleanup_actions})
            started = time.time()
            result, stdout, stderr, returncode = await asyncio.to_thread(deps.run_video_plan_tool, workspace, target, settings)
            generated_plan = deps.read_json(plan_path)
            status = deps.text(result.get("status"), "failed")
            if (returncode != 0 or status in {"failed", "blocked"}) and not generated_plan:
                detail = result.get("blocked_reasons") or stderr or stdout or "VideoPlan generation failed."
                deps.add_event(int(task["session_id"]), "koubo_storyboard.video_plan.failed", {**base_payload, "reason": cache_reason, "status": status, "returncode": returncode, "detail": detail})
                raise HTTPException(status_code=500, detail=detail)
            if not generated_plan:
                raise HTTPException(status_code=500, detail="VideoPlan generation completed without video_generation_plan.json")
            generated_plan = deps.video_plan_with_hash(generated_plan, sc=deps)

            ui_cache = {
                **signature,
                "cache_status": "regenerated",
                "reason": cache_reason,
                "created_from_task_id": task_id,
                "created_from_session_id": int(task["session_id"]),
                "target": target,
                "settings": settings,
                "tool_status": status,
                "tool_returncode": returncode,
                "updated_at": now_ms(),
            }
            deps.write_json(cache_path, ui_cache)
            cleared_stale_items = (
                clear_dance_mimic_stale_items(workspace, ["video_generation_plan"], source_step="koubo_storyboard.video_plan")
                if is_dance_mimic and returncode == 0 and status not in {"failed", "blocked"}
                else []
            )
            stale = dance_mimic_stale_summary(workspace) if is_dance_mimic else {}
            completed_payload = {
                **base_payload,
                "status": status,
                "reason": cache_reason,
                "new_plan_hash": deps.text(generated_plan.get("plan_hash")),
                "elapsed_seconds": round(time.time() - started, 3),
                "summary": generated_plan.get("summary") or {},
                "shot_count": int(deps.number((generated_plan.get("summary") or {}).get("shot_count"))),
                "scene_count": int(deps.number((generated_plan.get("summary") or {}).get("scene_count"))),
                "segment_count": int(deps.number((generated_plan.get("summary") or {}).get("segment_count"))),
                "cleanup_actions": cleanup_actions,
                "cleared_stale_items": cleared_stale_items,
            }
            deps.add_event(int(task["session_id"]), "koubo_storyboard.video_plan.generated", completed_payload)
            return {
                "ok": True,
                "cache_status": "regenerated",
                "reason": cache_reason,
                "task": task,
                "target": target,
                "settings": settings,
                "signature": signature,
                "ui_cache": ui_cache,
                "plan": generated_plan,
                "summary": generated_plan.get("summary") or {},
                **deps.video_plan_execution_payload(workspace, generated_plan, sc=deps),
                "artifact_status": deps.video_plan_artifact_status(workspace, generated_plan, sc=deps),
                "status": status,
                "cleanup_actions": cleanup_actions,
                "cleared_stale_items": cleared_stale_items,
                **({"stale": stale} if is_dance_mimic else {}),
                "tool_result": result,
            }

    @router.post("/api/koubo-storyboard/tasks/{task_id}/video-plan/execute")
    async def execute_video_plan(task_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        async with deps.video_plan_lock, deps.video_plan_execution_lock:
            task = deps.task_or_404(task_id)
            workspace = deps.workspace_for(task)
            plan = deps.video_plan_with_hash(deps.read_json(workspace / VIDEO_PLAN_REL), sc=deps)
            if not plan:
                raise HTTPException(status_code=404, detail="VideoPlan does not exist. Generate the plan before executing.")
            current_hash = deps.text(plan.get("plan_hash"))
            requested_hash = deps.text(payload.get("plan_hash"))
            state = deps.read_json(workspace / VIDEO_PLAN_EXECUTION_STATE_REL)
            if deps.video_plan_execution_is_running(state, sc=deps):
                state_hash = deps.text(state.get("source_plan_hash"))
                if not requested_hash or requested_hash == state_hash:
                    return {
                        **deps.video_plan_execution_payload(workspace, plan, sc=deps),
                        "ok": True,
                        "already_running": True,
                        "job_id": deps.text(state.get("job_id")),
                    }
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "video_plan_execution_running",
                        "message": "另一个生成计划仍在执行，请等待完成后刷新当前计划。",
                        "job_id": deps.text(state.get("job_id")),
                        "running_plan_hash": state_hash,
                        "requested_plan_hash": requested_hash,
                    },
                )
            if requested_hash and requested_hash != current_hash:
                raise HTTPException(status_code=409, detail="Current VideoPlan changed. Refresh the plan before executing.")
            if is_dance_mimic_storyboard(task, workspace, plan=plan):
                stale = dance_mimic_stale_summary(workspace)
                if dance_mimic_stale_item_is_active(stale, "video_generation_plan"):
                    detail = {
                        "code": "dance_mimic_video_plan_stale",
                        "message": "DanceMimic VideoPlan is stale. Regenerate VideoPlan before executing.",
                        "stale": stale,
                        "plan_hash": current_hash,
                    }
                    deps.add_event(int(task["session_id"]), "koubo_storyboard.video_plan.execution_blocked", {
                        "task_id": task_id,
                        "workspace_dir": str(workspace),
                        "reason": "dance_mimic_video_plan_stale",
                        "source_plan_hash": current_hash,
                        "stale": stale,
                    })
                    raise HTTPException(status_code=409, detail=detail)
            current_storyboard_plan, _meta = deps.load_plan(task, sc=deps)
            target = event_plan_target(plan) or {"target_type": "task", "shot_id": "", "scene_id": ""}
            settings = deps.video_plan_settings({"settings": plan.get("settings") if isinstance(plan.get("settings"), dict) else {}})
            signature = deps.video_plan_signature(workspace, current_storyboard_plan, target, settings, sc=deps)
            cache_match, cache_reason = deps.video_plan_cache_matches(
                plan,
                deps.read_json(workspace / VIDEO_PLAN_UI_CACHE_REL),
                target,
                settings,
                signature,
                sc=deps,
            )
            if not cache_match:
                detail = {
                    "code": "video_plan_stale",
                    "message": "生成计划已过期，请重新生成计划后再执行。",
                    "reason": cache_reason,
                    "plan_hash": current_hash,
                    "target": target,
                    "settings": settings,
                }
                deps.add_event(int(task["session_id"]), "koubo_storyboard.video_plan.execution_blocked", {
                    "task_id": task_id,
                    "workspace_dir": str(workspace),
                    "reason": "video_plan_stale",
                    "cache_reason": cache_reason,
                    "source_plan_hash": current_hash,
                    "target": target,
                    "settings": settings,
                })
                raise HTTPException(status_code=409, detail=detail)
            try:
                audio_preflight = deps.video_plan_tts_audio_preflight(workspace, plan, current_storyboard_plan, sc=deps)
            except HTTPException as exc:
                detail_payload = exc.detail if isinstance(exc.detail, dict) else {
                    "code": "selected_tts_audio_missing",
                    "message": deps.text(exc.detail) or "视频计划的对白音频尚未准备完成。",
                }
                deps.add_event(int(task["session_id"]), "koubo_storyboard.video_plan.execution_blocked", {
                    "task_id": task_id,
                    "workspace_dir": str(workspace),
                    "reason": deps.text(detail_payload.get("code"), "selected_tts_audio_missing"),
                    "source_plan_hash": current_hash,
                    "dialogue_count": len(detail_payload.get("dialogue_asset_keys") or []),
                })
                raise
            binding_repairs = bind_existing_final_videos(task, workspace, plan)
            if binding_repairs:
                deps.add_event(int(task["session_id"]), "koubo_storyboard.video_plan.final_video_bindings_repaired", {
                    "task_id": task_id,
                    "workspace_dir": str(workspace),
                    "source_plan_hash": current_hash,
                    "repairs": binding_repairs,
                })
            state = deps.read_json(workspace / VIDEO_PLAN_EXECUTION_STATE_REL)
            if deps.video_plan_execution_is_running(state, sc=deps):
                return {
                    **deps.video_plan_execution_payload(workspace, plan, sc=deps),
                    "ok": True,
                    "already_running": True,
                    "job_id": deps.text(state.get("job_id")),
                }
            model_selection = video_plan_execution_model_selection(task, payload)
            public_model_selection = public_video_plan_model_selection(model_selection)
            job_id = f"vp_exec_{now_ms()}_{uuid.uuid4().hex[:8]}"
            now_value = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            initial_marker = initial_video_plan_execution_marker(workspace, plan, now_value)
            initial_state = {
                "schema_version": "koubo_video_plan_execution_state_0.1",
                "job_id": job_id,
                "source_plan_hash": current_hash,
                "source_plan_path": VIDEO_PLAN_REL,
                "status": "queued",
                "started_at": now_value,
                "updated_at": now_value,
                "tts_audio_preflight": audio_preflight,
                **public_model_selection,
                **initial_marker,
                "summary": {},
                "error": "",
            }
            deps.write_video_plan_execution_state(workspace, initial_state, sc=deps)
            job = asyncio.create_task(deps.run_video_plan_execution_background(task_id, int(task["session_id"]), workspace, job_id, current_hash, model_selection, sc=deps))
            deps.video_plan_execution_jobs[job_id] = job
            deps.add_event(int(task["session_id"]), "koubo_storyboard.video_plan.execution_started", {
                "task_id": task_id,
                "workspace_dir": str(workspace),
                "job_id": job_id,
                "source_plan_hash": current_hash,
                "tts_audio_preflight": audio_preflight,
                **public_model_selection,
            })
            return {
                **deps.video_plan_execution_payload(workspace, plan, sc=deps),
                "ok": True,
                "job_id": job_id,
                **public_model_selection,
            }

    @router.get("/api/koubo-storyboard/tasks/{task_id}/video-plan/execution")
    async def video_plan_execution(task_id: int) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        workspace = deps.workspace_for(task)
        plan = deps.video_plan_with_hash(deps.read_json(workspace / VIDEO_PLAN_REL), sc=deps)
        if not plan:
            return {
                "ok": True,
                "plan_hash": "",
                "artifact_status": {"segments": {}},
                "execution_state": deps.redact_payload(deps.read_json(workspace / VIDEO_PLAN_EXECUTION_STATE_REL)),
                "execution_result": deps.redact_payload(deps.read_json(workspace / VIDEO_PLAN_EXECUTION_RESULT_REL)),
                "binding_status": {
                    "state_matches_current_plan": False,
                    "result_matches_current_plan": False,
                    "current_plan_hash": "",
                    "state_plan_hash": "",
                    "result_plan_hash": "",
                },
            }
        return deps.video_plan_execution_payload(workspace, plan, sc=deps)
