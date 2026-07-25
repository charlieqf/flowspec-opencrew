from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, HTTPException

from opcrew_backend.context import now_ms
from opcrew_backend.routes.media_model_config import customer_media_public_alias_target, load_agent_model_aliases, load_config

from .constants import *
from .slot_state_services import SlotInputs, derive_image_plan_slot_states

IMAGE_API_SETTINGS_REL = "SessionContext/ImageAPISettings.json"
IMAGES_AGENT_SETTINGS_REL = "SessionContext/ImagesAgentSettings.json"


def register_image_plan_routes(router: APIRouter, deps: Any) -> None:

    def settings_source_for(existing: dict[str, Any]) -> dict[str, Any]:
        if isinstance(existing, dict) and isinstance(existing.get("settings"), dict):
            return existing["settings"]
        return existing if isinstance(existing, dict) else {}

    def clean_model_selection_value(value: Any) -> str:
        text = deps.text(value)
        return "" if text in {"[model]", "[provider]"} else text

    def agent_image_alias_from_payload(payload: dict[str, Any]) -> str:
        return deps.text(
            payload.get("agentImageAlias")
            or payload.get("agent_image_alias")
            or payload.get("agent_model_alias")
            or payload.get("model_alias")
            or payload.get("alias")
        )

    def resolve_agent_image_alias(alias: str, *, strict: bool = True) -> tuple[str, str]:
        alias_value = deps.text(alias)
        if not alias_value:
            return "", ""
        for item in load_agent_model_aliases(deps.ctx, "image"):
            if deps.text(item.get("alias")) == alias_value:
                provider = deps.text(item.get("provider"))
                model = deps.text(item.get("model"))
                if provider and model:
                    return provider, model
        provider, model = customer_media_public_alias_target(load_config(deps.ctx, "image"), "image", alias_value)
        if provider and model:
            return provider, model
        if strict:
            raise HTTPException(status_code=400, detail="Select a valid Agent image model before generating.")
        return "", ""

    def image_model_selection_from_source(source: dict[str, Any], source_name: str, *, strict_alias: bool) -> dict[str, Any]:
        if not isinstance(source, dict):
            return {}
        alias = agent_image_alias_from_payload(source)
        if alias:
            provider, model = resolve_agent_image_alias(alias, strict=strict_alias)
            if provider and model:
                return {
                    "agentImageAlias": alias,
                    "source": source_name,
                    "image_provider": provider,
                    "image_model": model,
                }
            return {}
        provider = clean_model_selection_value(source.get("image_provider") or source.get("provider"))
        model = clean_model_selection_value(source.get("image_model") or source.get("model"))
        if provider and model:
            return {
                "agentImageAlias": "",
                "source": source_name,
                "image_provider": provider,
                "image_model": model,
            }
        return {}

    def image_execution_model_selection(task: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        request_selection = image_model_selection_from_source(payload if isinstance(payload, dict) else {}, "request", strict_alias=True)
        if request_selection:
            return request_selection
        workspace = deps.workspace_for(task)
        for rel_path in (IMAGE_API_SETTINGS_REL, IMAGES_AGENT_SETTINGS_REL):
            saved = deps.read_json(workspace / rel_path)
            selection = image_model_selection_from_source(settings_source_for(saved), rel_path, strict_alias=False)
            if selection:
                return selection
        return {}

    def public_image_model_selection(selection: dict[str, Any]) -> dict[str, str]:
        alias = deps.text(selection.get("agentImageAlias")) if isinstance(selection, dict) else ""
        return {"agentImageAlias": alias} if alias else {}

    def image_prompt_task(plan: dict[str, Any], asset_key: str) -> dict[str, Any]:
        target_asset_key = deps.text(asset_key)
        for item in plan.get("image_tasks") if isinstance(plan.get("image_tasks"), list) else []:
            if isinstance(item, dict) and deps.text(item.get("asset_key")) == target_asset_key:
                return item
        raise HTTPException(status_code=404, detail="Image prompt task not found in current ImagePlan.")

    def image_prompt_path(workspace, plan: dict[str, Any], asset_key: str):
        task = image_prompt_task(plan, asset_key)
        outputs = task.get("planned_outputs") if isinstance(task.get("planned_outputs"), dict) else {}
        rel_path = deps.text(outputs.get("image_prompt_path")) or f"SessionOutput/storyboard/Working/{deps.text(task.get('asset_key'))}_ImagePrompt.json"
        if not rel_path.startswith("SessionOutput/storyboard/Working/") or not rel_path.endswith("_ImagePrompt.json"):
            raise HTTPException(status_code=400, detail="Image prompt path must stay inside StoryBoard Working.")
        return deps.safe_workspace_rel(workspace, rel_path)

    def planned_working_path(workspace, task: dict[str, Any], output_key: str, fallback_suffix: str):
        outputs = task.get("planned_outputs") if isinstance(task.get("planned_outputs"), dict) else {}
        asset_key = deps.text(task.get("asset_key"))
        rel_path = deps.text(outputs.get(output_key)) or f"SessionOutput/storyboard/Working/{asset_key}{fallback_suffix}"
        if not rel_path.startswith("SessionOutput/storyboard/Working/"):
            return rel_path, None
        return deps.safe_workspace_rel(workspace, rel_path)

    def image_task_executable(task: dict[str, Any]) -> bool:
        outputs = task.get("planned_outputs") if isinstance(task.get("planned_outputs"), dict) else {}
        status = deps.text(task.get("status"))
        return bool(
            deps.text(task.get("asset_key"))
            and deps.text(outputs.get("image_path"))
            and status != "skipped"
            and not status.startswith("blocked")
        )

    def file_status(workspace, rel_path: str) -> dict[str, Any]:
        rel_value = deps.text(rel_path)
        if not rel_value:
            return {"path": "", "exists": False}
        try:
            _, path = deps.safe_workspace_rel(workspace, rel_value)
        except HTTPException:
            return {"path": rel_value, "exists": False}
        return {"path": rel_value, "exists": path.exists() and path.is_file() and path.stat().st_size > 0}

    def scene_dialogues(scene: dict[str, Any]) -> list[dict[str, Any]]:
        dialogues: list[dict[str, Any]] = []
        for key in ("dialogue_items", "dialogues"):
            values = scene.get(key)
            if isinstance(values, list):
                dialogues.extend([item for item in values if isinstance(item, dict)])
        return dialogues

    def dialogue_match_keys(dialogue: dict[str, Any], index: int = 0) -> set[str]:
        keys = {
            deps.text(dialogue.get("dialogue_asset_key")),
            deps.text(dialogue.get("dialogue_id")),
            deps.text(dialogue.get("srt_id")),
        }
        for item in dialogue.get("srt_ids") if isinstance(dialogue.get("srt_ids"), list) else []:
            keys.add(deps.text(item))
        if index > 0:
            for item in list(keys):
                if item and not item.endswith(f"_{index:02d}"):
                    keys.add(f"{item}_{index:02d}")
        return {item for item in keys if item}

    def storyboard_dialogue_for_asset(storyboard: dict[str, Any], asset_key: str) -> dict[str, Any]:
        target_asset_key = deps.text(asset_key)
        if not target_asset_key:
            return {}
        for shot in storyboard.get("shots") if isinstance(storyboard.get("shots"), list) else []:
            for scene in shot.get("scenes") if isinstance(shot.get("scenes"), list) else []:
                for dialogue in scene_dialogues(scene):
                    if target_asset_key == deps.text(dialogue.get("dialogue_asset_key")):
                        return dialogue
        for shot in storyboard.get("shots") if isinstance(storyboard.get("shots"), list) else []:
            for scene in shot.get("scenes") if isinstance(shot.get("scenes"), list) else []:
                for index, dialogue in enumerate(scene_dialogues(scene), start=1):
                    if target_asset_key in dialogue_match_keys(dialogue, index):
                        return dialogue
        return {}

    def dialogue_new_image_path(dialogue: dict[str, Any]) -> str:
        working_assets = dialogue.get("working_assets") if isinstance(dialogue.get("working_assets"), dict) else {}
        images = working_assets.get("images") if isinstance(working_assets.get("images"), list) else []
        if images:
            return deps.text((images[0] or {}).get("path"))
        return deps.text(dialogue.get("bound_image_path"))

    def dialogue_source_image_path(dialogue: dict[str, Any]) -> str:
        source_paths = dialogue.get("source_image_paths") if isinstance(dialogue.get("source_image_paths"), list) else []
        if source_paths:
            return deps.text(source_paths[0])
        return deps.text(dialogue.get("image_path"))

    def first_existing_working_slot_status(workspace, asset_key: str, slot: str, exts: set[str]) -> dict[str, Any]:
        key = deps.text(asset_key)
        if not key:
            return {"path": "", "exists": False}
        working = workspace / WORKING_REL
        for ext in sorted(exts):
            rel_path = f"{WORKING_REL}/{key}_{slot}{ext}"
            status = file_status(workspace, rel_path)
            if status.get("exists"):
                return status
        matches = sorted(working.glob(f"{key}_{slot}.*")) if working.exists() else []
        for path in matches:
            if path.suffix.lower() not in exts:
                continue
            rel_path = f"{WORKING_REL}/{path.name}"
            status = file_status(workspace, rel_path)
            if status.get("exists"):
                return status
        return {"path": "", "exists": False}

    def first_existing_working_slot_for_keys(workspace, asset_keys: list[str], slot: str, exts: set[str]) -> dict[str, Any]:
        for key in asset_keys:
            status = first_existing_working_slot_status(workspace, key, slot, exts)
            if status.get("exists"):
                return status
        return {"path": "", "exists": False}

    def selected_initial_task(plan: dict[str, Any], target_task_id: str = "", target_asset_key: str = "") -> dict[str, Any]:
        for item in plan.get("image_tasks") if isinstance(plan.get("image_tasks"), list) else []:
            if not isinstance(item, dict) or not image_task_executable(item):
                continue
            if target_task_id and deps.text(item.get("image_task_id")) != target_task_id:
                continue
            if target_asset_key and deps.text(item.get("asset_key")) != target_asset_key:
                continue
            return item
        return {}

    def initial_execution_marker(plan: dict[str, Any], mode: str, now_value: str, target_task_id: str = "", target_asset_key: str = "") -> dict[str, Any]:
        task = selected_initial_task(plan, target_task_id, target_asset_key)
        step = ""
        if task:
            step = "image" if mode == "image-only" else "prompt"
        task_key = deps.text(task.get("asset_key")) or deps.text(task.get("image_task_id"))
        return {
            "current_task_id": deps.text(task.get("image_task_id")),
            "current_asset_key": deps.text(task.get("asset_key")),
            "current_step": step,
            "current_step_status": "running_generate" if step else "",
            "tasks": {
                task_key: {
                    "image_task_id": deps.text(task.get("image_task_id")),
                    "asset_key": deps.text(task.get("asset_key")),
                    "steps": {
                        step: {
                            "status": "running_generate",
                            "updated_at": now_value,
                        }
                    },
                }
            } if task_key and step else {},
        }

    def image_plan_artifact_status(workspace, plan: dict[str, Any]) -> dict[str, Any]:
        edit_storyboard = deps.read_json(workspace / EDIT_REL)
        storyboard = edit_storyboard if edit_storyboard.get("schema_version") == "koubo_storyboard_edit_0.1" else deps.read_json(workspace / SOURCE_REL)
        tasks = []
        prompt_completed = 0
        image_completed = 0
        for item in plan.get("image_tasks") if isinstance(plan.get("image_tasks"), list) else []:
            if not isinstance(item, dict):
                continue
            prompt_rel, prompt_path = planned_working_path(workspace, item, "image_prompt_path", "_ImagePrompt.json")
            image_rel, image_path = planned_working_path(workspace, item, "image_path", "_Image_New.png")
            planned_image_exists = bool(image_path and image_path.exists() and image_path.is_file() and image_path.stat().st_size > 0)
            dialogue = storyboard_dialogue_for_asset(storyboard if isinstance(storyboard, dict) else {}, deps.text(item.get("asset_key")))
            actual_asset_key = deps.text(dialogue.get("dialogue_asset_key")) or deps.text(item.get("asset_key"))
            asset_keys = [actual_asset_key]
            if deps.text(item.get("asset_key")) not in asset_keys:
                asset_keys.append(deps.text(item.get("asset_key")))
            prompt_status = first_existing_working_slot_for_keys(workspace, asset_keys, "ImagePrompt", {".json"})
            prompt_exists = bool(prompt_status.get("exists") or (prompt_path and prompt_path.exists()))
            source_image = file_status(workspace, dialogue_source_image_path(dialogue))
            storyboard_image = file_status(workspace, dialogue_new_image_path(dialogue))
            image_status = storyboard_image if storyboard_image.get("exists") else {"path": image_rel if planned_image_exists else "", "exists": planned_image_exists}
            image_exists = bool(storyboard_image.get("exists") or planned_image_exists)
            raw_video = first_existing_working_slot_for_keys(workspace, asset_keys, "Video_Raw", VIDEO_EXTS)
            final_video = first_existing_working_slot_for_keys(workspace, asset_keys, "Video_Final", VIDEO_EXTS)
            slot_states = derive_image_plan_slot_states(SlotInputs(
                source_exists=bool(source_image.get("exists")),
                image_exists=image_exists,
                raw_exists=bool(raw_video.get("exists")),
                final_exists=bool(final_video.get("exists")),
                image_prompt_exists=prompt_exists,
            ))
            executable = image_task_executable(item)
            if executable and prompt_exists:
                prompt_completed += 1
            if executable and image_exists:
                image_completed += 1
            tasks.append({
                "image_task_id": deps.text(item.get("image_task_id")),
                "asset_key": deps.text(item.get("asset_key")),
                "executable_task": executable,
                "prompt_in_working": prompt_exists,
                "source_image": source_image,
                "image_in_working": image_exists,
                "image_in_storyboard": bool(storyboard_image.get("exists")),
                "storyboard_image_path": deps.text(storyboard_image.get("path")) if storyboard_image.get("exists") else "",
                "raw_video": raw_video,
                "final_video": final_video,
                "planned_image_exists": planned_image_exists,
                "prompt_path": deps.text(prompt_status.get("path")) if prompt_status.get("exists") else (prompt_rel if prompt_exists else ""),
                "image_path": deps.text(image_status.get("path")) if image_exists else "",
                "planned_image_path": image_rel if planned_image_exists else "",
                "slot_states": slot_states,
            })
        return {
            "tasks": tasks,
            "summary": {
                "prompt_completed_count": prompt_completed,
                "image_completed_count": image_completed,
            },
        }

    def image_plan_payload(workspace, plan_payload: dict[str, Any]) -> dict[str, Any]:
        plan = deps.video_plan_with_hash(plan_payload, sc=deps)
        current_hash = deps.text(plan.get("plan_hash"))
        execution_state = deps.read_json(workspace / IMAGE_PLAN_EXECUTION_STATE_REL)
        execution_result = deps.read_json(workspace / IMAGE_PLAN_EXECUTION_RESULT_REL)
        state_hash = deps.text(execution_state.get("source_plan_hash"))
        result_hash = deps.text(execution_result.get("source_plan_hash") or execution_result.get("image_plan_hash"))
        return {
            "ok": True,
            "plan_hash": current_hash,
            "consistency_references": deps.redact_payload(deps.video_plan_consistency_reference_snapshot(workspace, sc=deps)),
            "artifact_status": deps.redact_payload(image_plan_artifact_status(workspace, plan)),
            "execution_state": deps.redact_payload(execution_state),
            "execution_result": deps.redact_payload(execution_result),
            "binding_status": {
                "state_matches_current_plan": bool(current_hash and state_hash and current_hash == state_hash),
                "result_matches_current_plan": bool(current_hash and result_hash and current_hash == result_hash),
                "current_plan_hash": current_hash,
                "state_plan_hash": state_hash,
                "result_plan_hash": result_hash,
            },
        }

    @router.get("/api/koubo-storyboard/tasks/{task_id}/image-plan/prompts/{asset_key}")
    async def get_image_prompt(task_id: int, asset_key: str) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        workspace = deps.workspace_for(task)
        plan = deps.video_plan_with_hash(deps.read_json(workspace / IMAGE_PLAN_REL), sc=deps)
        if not plan:
            raise HTTPException(status_code=404, detail="ImagePlan does not exist. Generate the ImagePlan first.")
        rel_path, path = image_prompt_path(workspace, plan, asset_key)
        if not path.exists():
            raise HTTPException(status_code=404, detail="Image prompt does not exist. Run Prompt first.")
        payload = deps.read_json(path)
        return {
            "ok": True,
            "asset_key": asset_key,
            "path": rel_path,
            "prompt": deps.redact_payload(payload),
            "plan_hash": deps.text(plan.get("plan_hash")),
        }

    @router.put("/api/koubo-storyboard/tasks/{task_id}/image-plan/prompts/{asset_key}")
    async def save_image_prompt(task_id: int, asset_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        workspace = deps.workspace_for(task)
        plan = deps.video_plan_with_hash(deps.read_json(workspace / IMAGE_PLAN_REL), sc=deps)
        if not plan:
            raise HTTPException(status_code=404, detail="ImagePlan does not exist. Generate the ImagePlan first.")
        current_hash = deps.text(plan.get("plan_hash"))
        prompt_payload = payload.get("prompt")
        if not isinstance(prompt_payload, dict):
            raise HTTPException(status_code=400, detail="prompt must be a JSON object")
        rel_path, path = image_prompt_path(workspace, plan, asset_key)
        current = deps.read_json(path) if path.exists() else {}
        prompt_payload = {
            **current,
            **prompt_payload,
            "asset_key": asset_key,
            "prompt_origin": "manual_or_system",
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        deps.write_json(path, prompt_payload)
        deps.add_event(int(task["session_id"]), "koubo_storyboard.image_plan.prompt_saved", {
            "task_id": task_id,
            "workspace_dir": str(workspace),
            "asset_key": asset_key,
            "path": rel_path,
        })
        return {
            "ok": True,
            "asset_key": asset_key,
            "path": rel_path,
            "prompt": deps.redact_payload(prompt_payload),
            "plan_hash": current_hash,
        }

    @router.post("/api/koubo-storyboard/tasks/{task_id}/image-plan")
    async def image_plan(task_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        if deps.image_plan_lock.locked() or deps.video_plan_lock.locked():
            raise HTTPException(status_code=409, detail="Plan generation is already running. Please wait for the current run to finish.")
        if deps.image_plan_execution_lock.locked() or deps.video_plan_execution_lock.locked():
            raise HTTPException(status_code=409, detail="Plan execution is running. Please wait for it to finish before regenerating ImagePlan.")
        async with deps.image_plan_lock:
            task = deps.task_or_404(task_id)
            workspace = deps.workspace_for(task)
            current_plan, _meta = deps.load_plan(task, sc=deps)
            deps.save_edit_and_source_storyboard(task, workspace, current_plan, sc=deps)
            target = deps.video_plan_target(payload, current_plan, sc=deps)
            settings = deps.video_plan_settings(payload)
            cleanup_actions = deps.reset_image_plan_outputs(workspace)
            action_source = deps.text(payload.get("action_source") or payload.get("actionSource"))
            deps.add_event(int(task["session_id"]), "koubo_storyboard.image_plan.rerun_started", {
                "task_id": task_id,
                "session_id": int(task["session_id"]),
                "workspace_dir": str(workspace),
                "target": target,
                "settings": settings,
                "action_source": action_source,
                "cleanup_actions": cleanup_actions,
            })
            started = time.time()
            result, stdout, stderr, returncode = await asyncio.to_thread(deps.run_image_plan_tool, workspace, target, settings)
            generated_plan = deps.read_json(workspace / IMAGE_PLAN_REL)
            status = deps.text(result.get("status"), "failed")
            if (returncode != 0 or status in {"failed", "blocked"}) and not generated_plan:
                detail = result.get("blocked_reasons") or stderr or stdout or "ImagePlan generation failed."
                deps.add_event(int(task["session_id"]), "koubo_storyboard.image_plan.failed", {
                    "task_id": task_id,
                    "workspace_dir": str(workspace),
                    "target": target,
                    "settings": settings,
                    "returncode": returncode,
                    "status": status,
                    "detail": deps.redact_payload(detail),
                })
                raise HTTPException(status_code=500, detail=detail)
            if not generated_plan:
                raise HTTPException(status_code=500, detail="ImagePlan generation completed without image_generation_plan.json")
            generated_plan = deps.video_plan_with_hash(generated_plan, sc=deps)
            deps.add_event(int(task["session_id"]), "koubo_storyboard.image_plan.generated", {
                "task_id": task_id,
                "workspace_dir": str(workspace),
                "target": target,
                "settings": settings,
                "returncode": returncode,
                "status": status,
                "new_plan_hash": deps.text(generated_plan.get("plan_hash")),
                "elapsed_seconds": round(time.time() - started, 3),
                "summary": generated_plan.get("summary") or {},
                "cleanup_actions": cleanup_actions,
            })
            return {
                "ok": True,
                "cache_status": "regenerated",
                "task": task,
                "target": target,
                "settings": settings,
                "plan": generated_plan,
                "summary": generated_plan.get("summary") or {},
                **image_plan_payload(workspace, generated_plan),
                "status": status,
                "cleanup_actions": cleanup_actions,
                "tool_result": result,
            }

    @router.post("/api/koubo-storyboard/tasks/{task_id}/image-plan/execute")
    async def execute_image_plan(task_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        if deps.image_plan_lock.locked():
            raise HTTPException(status_code=409, detail="ImagePlan generation is running. Wait for it to finish before running ImagePlanExecutor.")
        if deps.video_plan_execution_lock.locked():
            raise HTTPException(status_code=409, detail="VideoPlan execution is running. Wait for it to finish before running ImagePlanExecutor.")
        async with deps.image_plan_execution_lock:
            task = deps.task_or_404(task_id)
            workspace = deps.workspace_for(task)
            plan = deps.video_plan_with_hash(deps.read_json(workspace / IMAGE_PLAN_REL), sc=deps)
            if not plan:
                raise HTTPException(status_code=404, detail="ImagePlan does not exist. Generate the ImagePlan before executing.")
            current_hash = deps.text(plan.get("plan_hash"))
            mode = deps.text(payload.get("mode"), "prompt-only")
            if mode not in {"prompt-only", "image-only", "prompt-and-image"}:
                raise HTTPException(status_code=400, detail="mode must be prompt-only, image-only, or prompt-and-image")
            target_task_id = deps.text(payload.get("target_task_id"))
            target_asset_key = deps.text(payload.get("target_asset_key"))
            state = deps.read_json(workspace / IMAGE_PLAN_EXECUTION_STATE_REL)
            state_hash = deps.text(state.get("source_plan_hash"))
            state_job_id = deps.text(state.get("job_id"))
            current_state_is_running = bool(
                deps.text(state.get("status")) in {"queued", "running"}
                and current_hash
                and state_hash == current_hash
                and state_job_id in deps.image_plan_execution_jobs
            )
            if current_state_is_running:
                return {
                    **image_plan_payload(workspace, plan),
                    "ok": True,
                    "already_running": True,
                    "job_id": deps.text(state.get("job_id")),
                    "mode": deps.text(state.get("mode")),
                    "target_task_id": deps.text(state.get("target_task_id")),
                    "target_asset_key": deps.text(state.get("target_asset_key")),
                }
            model_selection = image_execution_model_selection(task, payload)
            public_model_selection = public_image_model_selection(model_selection)
            job_id = f"ip_exec_{now_ms()}"
            now_value = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            initial_marker = initial_execution_marker(plan, mode, now_value, target_task_id, target_asset_key)
            deps.write_json(workspace / IMAGE_PLAN_EXECUTION_STATE_REL, {
                "schema_version": "analysis_v1_image_plan_execution_state_0.1",
                "job_id": job_id,
                "status": "queued",
                "mode": mode,
                "target_task_id": target_task_id,
                "target_asset_key": target_asset_key,
                **public_model_selection,
                **initial_marker,
                "summary": {},
                "error": "",
                "source_plan_hash": current_hash,
                "started_at": now_value,
                "updated_at": now_value,
            })
            deps.add_event(int(task["session_id"]), "koubo_storyboard.image_plan.execution_started", {
                "task_id": task_id,
                "workspace_dir": str(workspace),
                "mode": mode,
                "target_task_id": target_task_id,
                "target_asset_key": target_asset_key,
                "source_plan_hash": current_hash,
                **public_model_selection,
            })
            job = asyncio.create_task(deps.run_image_plan_execution_background(task_id, int(task["session_id"]), workspace, job_id, current_hash, mode, target_task_id, target_asset_key, model_selection, sc=deps))
            deps.image_plan_execution_jobs[job_id] = job
            return {
                "ok": True,
                "task": task,
                "plan": plan,
                "summary": plan.get("summary") or {},
                "mode": mode,
                "target_task_id": target_task_id,
                "target_asset_key": target_asset_key,
                **image_plan_payload(workspace, plan),
                "job_id": job_id,
                **public_model_selection,
            }

    @router.get("/api/koubo-storyboard/tasks/{task_id}/image-plan/execution")
    async def image_plan_execution(task_id: int) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        workspace = deps.workspace_for(task)
        plan = deps.video_plan_with_hash(deps.read_json(workspace / IMAGE_PLAN_REL), sc=deps)
        if not plan:
            return {
                "ok": True,
                "plan_hash": "",
                "execution_state": deps.redact_payload(deps.read_json(workspace / IMAGE_PLAN_EXECUTION_STATE_REL)),
                "execution_result": deps.redact_payload(deps.read_json(workspace / IMAGE_PLAN_EXECUTION_RESULT_REL)),
                "binding_status": {
                    "state_matches_current_plan": False,
                    "result_matches_current_plan": False,
                    "current_plan_hash": "",
                    "state_plan_hash": "",
                    "result_plan_hash": "",
                },
            }
        return image_plan_payload(workspace, plan)
