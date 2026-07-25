from __future__ import annotations

import asyncio
import importlib.util
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from opcrew_backend.context import now_ms
from opcrew_backend.routes.media_model_config import customer_media_public_alias_target, load_agent_model_aliases, load_config

from .constants import *
from .slot_state_services import SlotInputs, derive_video_only_plan_slot_states

VIDEO_API_SETTINGS_REL = "SessionContext/VideoAPISettings.json"
VIDEOS_AGENT_SETTINGS_REL = "SessionContext/VideosAgentSettings.json"
_TALKING_HEAD_PRIVACY_GRID: Any | None = None
_MAX_SD_2_PRIVACY_POLICY: Any | None = None


def max_sd_2_privacy_policy_module() -> Any:
    global _MAX_SD_2_PRIVACY_POLICY
    if _MAX_SD_2_PRIVACY_POLICY is not None:
        return _MAX_SD_2_PRIVACY_POLICY
    path = OPENCREW_ROOT / "ToolLibrary" / "Analysis_V1" / "video_plan_executor_modules" / "max_sd_2_privacy_policy.py"
    spec = importlib.util.spec_from_file_location("analysis_v1_max_sd_2_privacy_policy_for_tail_materialize", path)
    if not spec or not spec.loader:
        raise RuntimeError(f"Cannot load Analysis_V1 Max SD 2 privacy policy: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _MAX_SD_2_PRIVACY_POLICY = module
    return module


def talking_head_privacy_grid_module() -> Any:
    global _TALKING_HEAD_PRIVACY_GRID
    if _TALKING_HEAD_PRIVACY_GRID is not None:
        return _TALKING_HEAD_PRIVACY_GRID
    path = OPENCREW_ROOT / "ToolLibrary" / "TalkingHead_V1" / "reference_privacy_grid.py"
    spec = importlib.util.spec_from_file_location("talking_head_reference_privacy_grid_for_tail_materialize", path)
    if not spec or not spec.loader:
        raise RuntimeError(f"Cannot load TalkingHead privacy grid module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _TALKING_HEAD_PRIVACY_GRID = module
    return module


def apply_talking_head_tail_frame_privacy(
    workspace: Path,
    variables: dict[str, Any],
    storyboard: dict[str, Any],
    item: dict[str, Any],
    source_path: Path,
    working_dir: Path,
    asset_key: str,
) -> tuple[Path, dict[str, Any]]:
    if not max_sd_2_privacy_policy_module().should_apply_max_sd_2_oral_privacy_grid(variables, storyboard, item):
        return source_path, {}
    talking_head_config = storyboard.get("talking_head_config") if isinstance(storyboard.get("talking_head_config"), dict) else {}
    reference = talking_head_config.get("max_sd_2_reference") if isinstance(talking_head_config.get("max_sd_2_reference"), dict) else {}
    if not reference.get("privacy_grid_mode") or not reference.get("target_identity_grid_applied"):
        return source_path, {}
    segment = {
        "first_frame": {"source_type": "previous_segment_tail_frame"},
        "talking_head_reference": reference,
    }
    output, metadata = talking_head_privacy_grid_module().prepare_continuity_frame(
        workspace,
        variables,
        segment,
        source_path,
        working_dir,
        asset_key,
    )
    return output, metadata if isinstance(metadata, dict) else {}


def register_video_only_plan_routes(router: APIRouter, deps: Any) -> None:

    def task_items(plan: dict[str, Any]) -> list[dict[str, Any]]:
        items = plan.get("video_only_tasks")
        return [item for item in (items if isinstance(items, list) else []) if isinstance(item, dict)]

    def video_only_task(plan: dict[str, Any], asset_key: str) -> dict[str, Any]:
        target_asset_key = deps.text(asset_key)
        for item in task_items(plan):
            if deps.text(item.get("asset_key")) == target_asset_key:
                return item
        raise HTTPException(status_code=404, detail="Video-only task not found in current plan.")

    previous_tail_frame_types = {"previous_segment_tail_frame", "previous_scene_tail_frame"}

    def selected_initial_task_artifact(workspace, plan: dict[str, Any], mode: str, target_task_id: str = "", target_asset_key: str = "") -> tuple[dict[str, Any], dict[str, Any], str]:
        artifacts = artifact_status(workspace, plan).get("tasks") or []
        artifact_by_task_id = {deps.text(item.get("video_only_task_id")): item for item in artifacts if isinstance(item, dict)}
        artifact_by_asset_key = {deps.text(item.get("asset_key")): item for item in artifacts if isinstance(item, dict)}
        step_slots = {
            "prompt-only": [("prompt", "video_prompt")],
            "video-only": [("video", "raw_video")],
            "prompt-and-video": [("prompt", "video_prompt"), ("video", "raw_video")],
        }.get(mode, [("prompt", "video_prompt")])
        for task in task_items(plan):
            task_id = deps.text(task.get("video_only_task_id"))
            asset_key = deps.text(task.get("asset_key"))
            if target_task_id and task_id != target_task_id:
                continue
            if target_asset_key and asset_key != target_asset_key:
                continue
            artifact = artifact_by_task_id.get(task_id) or artifact_by_asset_key.get(asset_key) or {}
            slot_states = artifact.get("slot_states") if isinstance(artifact.get("slot_states"), dict) else {}
            for step, slot_key in step_slots:
                slot = slot_states.get(slot_key) if isinstance(slot_states, dict) else {}
                if deps.text(slot.get("ui_tone") or slot.get("tone")) == "pending":
                    return task, artifact, step
        return {}, {}, ""

    def initial_execution_marker(workspace, plan: dict[str, Any], mode: str, now_value: str, target_task_id: str = "", target_asset_key: str = "") -> dict[str, Any]:
        task, _artifact, step = selected_initial_task_artifact(workspace, plan, mode, target_task_id, target_asset_key)
        task_key = deps.text(task.get("asset_key")) or deps.text(task.get("video_only_task_id"))
        return {
            "current_task_id": deps.text(task.get("video_only_task_id")),
            "current_asset_key": deps.text(task.get("asset_key")),
            "current_step": step if task_key else "",
            "current_step_status": "running_generate" if task_key and step else "",
            "segments": {
                task_key: {
                    "video_only_task_id": deps.text(task.get("video_only_task_id")),
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

    def settings_source_for(existing: dict[str, Any]) -> dict[str, Any]:
        if isinstance(existing, dict) and isinstance(existing.get("settings"), dict):
            return existing["settings"]
        return existing if isinstance(existing, dict) else {}

    def clean_model_selection_value(value: Any) -> str:
        text = deps.text(value)
        return "" if text in {"[model]", "[provider]"} else text

    def agent_video_alias_from_payload(payload: dict[str, Any]) -> str:
        return deps.text(
            payload.get("agentVideoAlias")
            or payload.get("agent_video_alias")
            or payload.get("agent_model_alias")
            or payload.get("model_alias")
            or payload.get("alias")
        )

    def resolve_agent_video_alias(alias: str, *, strict: bool = True) -> tuple[str, str]:
        alias_value = deps.text(alias)
        if not alias_value:
            return "", ""
        for item in load_agent_model_aliases(deps.ctx, "video"):
            if deps.text(item.get("alias")) == alias_value:
                provider = deps.text(item.get("provider"))
                model = deps.text(item.get("model"))
                if provider and model:
                    return provider, model
        provider, model = customer_media_public_alias_target(load_config(deps.ctx, "video"), "video", alias_value)
        if provider and model:
            return provider, model
        if strict:
            raise HTTPException(status_code=400, detail="Select a valid Agent video model before generating.")
        return "", ""

    def video_only_model_selection_from_source(source: dict[str, Any], source_name: str, *, strict_alias: bool) -> dict[str, Any]:
        if not isinstance(source, dict):
            return {}
        alias = agent_video_alias_from_payload(source)
        if alias:
            provider, model = resolve_agent_video_alias(alias, strict=strict_alias)
            if provider and model:
                return {
                    "agentVideoAlias": alias,
                    "source": source_name,
                    "video_provider": provider,
                    "video_model": model,
                }
            return {}
        provider = clean_model_selection_value(source.get("video_provider") or source.get("provider"))
        model = clean_model_selection_value(source.get("video_model") or source.get("model"))
        if provider and model:
            return {
                "agentVideoAlias": "",
                "source": source_name,
                "video_provider": provider,
                "video_model": model,
            }
        return {}

    def video_only_execution_model_selection(task: dict[str, Any], request_payload: dict[str, Any]) -> dict[str, Any]:
        request_selection = video_only_model_selection_from_source(request_payload if isinstance(request_payload, dict) else {}, "request", strict_alias=True)
        if request_selection:
            return request_selection
        workspace = deps.workspace_for(task)
        for rel_path in (VIDEO_API_SETTINGS_REL, VIDEOS_AGENT_SETTINGS_REL):
            saved = deps.read_json(workspace / rel_path)
            selection = video_only_model_selection_from_source(settings_source_for(saved), rel_path, strict_alias=False)
            if selection:
                return selection
        return {}

    def public_video_only_model_selection(selection: dict[str, Any]) -> dict[str, str]:
        alias = deps.text(selection.get("agentVideoAlias")) if isinstance(selection, dict) else ""
        return {"agentVideoAlias": alias} if alias else {}

    def public_video_alias_from_saved_settings(workspace) -> dict[str, str]:
        for rel_path in (VIDEO_API_SETTINGS_REL, VIDEOS_AGENT_SETTINGS_REL):
            saved = deps.read_json(workspace / rel_path)
            selection = video_only_model_selection_from_source(settings_source_for(saved), rel_path, strict_alias=False)
            alias = deps.text(selection.get("agentVideoAlias")) if isinstance(selection, dict) else ""
            if alias:
                return {"agentVideoAlias": alias}
        return {}

    def planned_path(task: dict[str, Any], key: str, suffix: str) -> str:
        outputs = task.get("planned_outputs") if isinstance(task.get("planned_outputs"), dict) else {}
        asset_key = deps.text(task.get("asset_key"))
        return deps.text(outputs.get(key)) or f"{WORKING_REL}/{asset_key}{suffix}"

    def prompt_path(workspace, plan: dict[str, Any], asset_key: str):
        task = video_only_task(plan, asset_key)
        rel_path = planned_path(task, "video_prompt_path", "_VideoPrompt.json")
        if not rel_path.startswith(f"{WORKING_REL}/") or not rel_path.endswith("_VideoPrompt.json"):
            raise HTTPException(status_code=400, detail="Video prompt path must stay inside StoryBoard Working.")
        return deps.safe_workspace_rel(workspace, rel_path)

    def prompt_text(positive_prompt: str, negative_prompt: str) -> str:
        positive = deps.text(positive_prompt)
        negative = deps.text(negative_prompt)
        if positive and negative:
            return f"{positive}\n\nNegative prompt:\n{negative}"
        return positive

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

    def dialogue_video_slot(dialogue: dict[str, Any]) -> dict[str, Any]:
        working_assets = dialogue.get("working_assets") if isinstance(dialogue.get("working_assets"), dict) else {}
        video = working_assets.get("video") if isinstance(working_assets.get("video"), dict) else {}
        if deps.text(video.get("path")):
            return video
        legacy_assets = dialogue.get("assets") if isinstance(dialogue.get("assets"), dict) else {}
        legacy_video = legacy_assets.get("video") if isinstance(legacy_assets.get("video"), dict) else {}
        return legacy_video

    def bind_video_slot(dialogue: dict[str, Any], final_rel: str) -> None:
        working_assets = dialogue.get("working_assets") if isinstance(dialogue.get("working_assets"), dict) else {}
        working_assets["video"] = {"slot": "Video_Final", "source_type": "generated", "path": final_rel}
        dialogue["working_assets"] = working_assets

    def video_bound(storyboard: dict[str, Any], final_rel: str, asset_key: str = "") -> bool:
        for shot in storyboard.get("shots") if isinstance(storyboard.get("shots"), list) else []:
            for scene in shot.get("scenes") if isinstance(shot.get("scenes"), list) else []:
                for index, dialogue in enumerate(scene_dialogues(scene), start=1):
                    video = dialogue_video_slot(dialogue)
                    if deps.text(video.get("path")) == final_rel:
                        return True
                    if asset_key and asset_key in dialogue_match_keys(dialogue, index) and deps.text(video.get("path")) == final_rel:
                        return True
        return False

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

    def dialogue_audio_path(dialogue: dict[str, Any]) -> str:
        working_assets = dialogue.get("working_assets") if isinstance(dialogue.get("working_assets"), dict) else {}
        audio = working_assets.get("audio") if isinstance(working_assets.get("audio"), dict) else {}
        return deps.text(audio.get("path"))

    def is_new_image_path(path: str) -> bool:
        return bool(path and re.search(r"_Image_New\.[^./]+$", path))

    def is_standard_asset_slot_path(path: str, asset_key: str, slot: str) -> bool:
        value = deps.text(path)
        key = deps.text(asset_key)
        return bool(value and key and Path(value).name.startswith(f"{key}_{slot}."))

    def storyboard_new_image_status(workspace, dialogue: dict[str, Any], asset_key: str) -> dict[str, Any]:
        rel_path = dialogue_new_image_path(dialogue)
        if not is_standard_asset_slot_path(rel_path, asset_key, "Image_New"):
            return {"path": rel_path, "exists": False}
        return file_status(workspace, rel_path)

    def first_existing_status(workspace, paths: list[str], *, require_new_image: bool = False) -> dict[str, Any]:
        first_status: dict[str, Any] = {"path": "", "exists": False}
        for path in paths:
            if require_new_image and not is_new_image_path(path):
                continue
            status = file_status(workspace, path)
            if not first_status["path"] and status["path"]:
                first_status = status
            if status["exists"]:
                return status
        return first_status

    def existing_working_slot_for_keys(workspace, asset_keys: list[str], slot: str, exts: set[str], preferred_suffix: str = ".mp4") -> str:
        for key in asset_keys:
            rel_path = deps.existing_working_slot_path(workspace, key, slot, exts, preferred_suffix, sc=deps)
            if rel_path:
                return rel_path
        return ""

    def plan_new_image_paths(task: dict[str, Any]) -> list[str]:
        first_frame = task.get("first_frame") if isinstance(task.get("first_frame"), dict) else {}
        outputs = task.get("planned_outputs") if isinstance(task.get("planned_outputs"), dict) else {}
        source_type = deps.text(first_frame.get("source_type"))
        source_path = "" if source_type in previous_tail_frame_types else deps.text(first_frame.get("source_path"))
        return [
            source_path,
            deps.text(first_frame.get("planned_image_path")),
            deps.text(outputs.get("first_frame_path")),
        ]

    def tail_frame_input_path(task: dict[str, Any]) -> str:
        first_frame = task.get("first_frame") if isinstance(task.get("first_frame"), dict) else {}
        source_type = deps.text(first_frame.get("source_type"))
        if source_type in previous_tail_frame_types:
            return deps.text(first_frame.get("source_path"))
        source_segment = task.get("source_segment") if isinstance(task.get("source_segment"), dict) else {}
        segment_first_frame = source_segment.get("first_frame") if isinstance(source_segment.get("first_frame"), dict) else {}
        segment_source_type = deps.text(segment_first_frame.get("source_type"))
        if segment_source_type in previous_tail_frame_types:
            return deps.text(segment_first_frame.get("source_path"))
        return ""

    def frame_input_source_type(task: dict[str, Any], bound_source_type: str = "") -> str:
        bound_value = deps.text(bound_source_type)
        if bound_value:
            return bound_value
        first_frame = task.get("first_frame") if isinstance(task.get("first_frame"), dict) else {}
        source_type = deps.text(first_frame.get("source_type"))
        if source_type:
            return source_type
        source_segment = task.get("source_segment") if isinstance(task.get("source_segment"), dict) else {}
        segment_first_frame = source_segment.get("first_frame") if isinstance(source_segment.get("first_frame"), dict) else {}
        return deps.text(segment_first_frame.get("source_type"))

    def pending_slot(reason: str, title: str) -> dict[str, Any]:
        return {
            "color": "white",
            "tone": "pending",
            "ui_tone": "pending",
            "color_zh": "白",
            "reason": reason,
            "file_exists": False,
            "binding_consistency": "not_applicable",
            "title": title,
        }

    def dialogue_new_image_source_type(dialogue: dict[str, Any]) -> str:
        working_assets = dialogue.get("working_assets") if isinstance(dialogue.get("working_assets"), dict) else {}
        images = working_assets.get("images") if isinstance(working_assets.get("images"), list) else []
        if images and isinstance(images[0], dict):
            return deps.text(images[0].get("source_type"))
        return ""

    def audio_task_statuses(workspace, task: dict[str, Any]) -> list[dict[str, Any]]:
        source_segment = task.get("source_segment") if isinstance(task.get("source_segment"), dict) else {}
        audio_tasks = source_segment.get("dialogue_audio_tasks") if isinstance(source_segment.get("dialogue_audio_tasks"), list) else []
        statuses = []
        for audio_task in audio_tasks:
            if not isinstance(audio_task, dict):
                continue
            rel_path = deps.text(audio_task.get("existing_audio_path")) or deps.text(audio_task.get("planned_audio_path"))
            status = file_status(workspace, rel_path)
            status.update({
                "srt_id": deps.text(audio_task.get("srt_id")),
                "need_audio": bool(audio_task.get("need_audio")),
                "audio_source": deps.text(audio_task.get("audio_source")),
            })
            statuses.append(status)
        return statuses

    def artifact_status(workspace, plan: dict[str, Any]) -> dict[str, Any]:
        edit_storyboard = deps.read_json(workspace / EDIT_REL)
        storyboard = edit_storyboard if edit_storyboard.get("schema_version") == "koubo_storyboard_edit_0.1" else deps.read_json(workspace / SOURCE_REL)
        tasks = []
        summary = {"prompt_completed_count": 0, "video_completed_count": 0, "final_completed_count": 0, "pending_final_count": 0}
        for item in task_items(plan):
            asset_key = deps.text(item.get("asset_key"))
            dialogue = storyboard_dialogue_for_asset(storyboard if isinstance(storyboard, dict) else {}, asset_key)
            actual_asset_key = deps.text(dialogue.get("dialogue_asset_key")) or asset_key
            asset_keys = [actual_asset_key]
            if asset_key not in asset_keys:
                asset_keys.append(asset_key)
            new_image = storyboard_new_image_status(workspace, dialogue, actual_asset_key)
            if not new_image.get("exists") and actual_asset_key != asset_key:
                new_image = storyboard_new_image_status(workspace, dialogue, asset_key)
            new_image_source_type = dialogue_new_image_source_type(dialogue)
            frame_source_type = frame_input_source_type(item, new_image_source_type)
            tail_frame_input = file_status(workspace, tail_frame_input_path(item))
            tail_frame_copy_pending = bool(tail_frame_input["exists"] and not new_image["exists"])
            tail_frame_required = frame_source_type in previous_tail_frame_types
            tail_frame_missing = bool(tail_frame_required and not new_image["exists"] and not tail_frame_input["exists"])
            first_frame = new_image
            if new_image["exists"] and frame_source_type in {"tail_frame_materialized", *previous_tail_frame_types}:
                frame_input_kind = "tail_frame_materialized"
            else:
                frame_input_kind = "new_image" if new_image["exists"] else ("tail_frame_pending_copy" if tail_frame_copy_pending else ("tail_frame_missing" if tail_frame_missing else ""))
            prompt = first_existing_status(workspace, [
                existing_working_slot_for_keys(workspace, asset_keys, "VideoPrompt", {".json"}, ".json"),
                planned_path(item, "video_prompt_path", "_VideoPrompt.json"),
            ])
            segment_audio = first_existing_status(workspace, [
                dialogue_audio_path(dialogue),
                planned_path(item, "segment_audio_path", "_SegmentAudio_Final.wav"),
            ])
            audio_files = audio_task_statuses(workspace, item)
            audio_in_working = bool(segment_audio["exists"] or (audio_files and all(status["exists"] for status in audio_files)))
            raw = first_existing_status(workspace, [
                existing_working_slot_for_keys(workspace, asset_keys, "Video_Raw", VIDEO_EXTS, ".mp4"),
                planned_path(item, "raw_video_path", "_Video_Raw.mp4"),
            ])
            final = first_existing_status(workspace, [
                deps.text(dialogue_video_slot(dialogue).get("path")),
                existing_working_slot_for_keys(workspace, asset_keys, "Video_Final", VIDEO_EXTS, ".mp4"),
                planned_path(item, "final_video_path", "_Video_Final.mp4"),
            ])
            tail = file_status(workspace, planned_path(item, "tail_frame_path", "_TailFrame.png"))
            bound = bool(final.get("exists") and video_bound(storyboard if isinstance(storyboard, dict) else {}, deps.text(final.get("path")), asset_key))
            source_image = file_status(workspace, dialogue_source_image_path(dialogue))
            slot_states = derive_video_only_plan_slot_states(SlotInputs(
                audio_exists=audio_in_working,
                source_exists=bool(source_image.get("exists")),
                image_exists=bool(new_image.get("exists")),
                raw_exists=bool(raw.get("exists")),
                final_exists=bool(final.get("exists")),
                video_prompt_exists=bool(prompt.get("exists")),
                final_bound=bound,
                binding_missing=bool(final.get("exists")) and not bound,
            ))
            downstream_exists = bool(raw.get("exists") or final.get("exists"))
            if tail_frame_copy_pending and not downstream_exists:
                slot_states["image"] = pending_slot("tail_frame_ready_to_materialize", "尾帧已存在，可物化为新图")
            elif tail_frame_missing and not downstream_exists:
                slot_states["image"] = pending_slot("waiting_previous_tail_frame", "等待上一段/上一 Shot 尾帧")
            if prompt["exists"]:
                summary["prompt_completed_count"] += 1
            if raw["exists"] or final["exists"]:
                summary["video_completed_count"] += 1
            if bound:
                summary["final_completed_count"] += 1
            if raw["exists"] and not bound:
                summary["pending_final_count"] += 1
            tasks.append({
                "video_only_task_id": deps.text(item.get("video_only_task_id")),
                "asset_key": deps.text(item.get("asset_key")),
                "first_frame": first_frame,
                "new_image": new_image,
                "source_image": source_image,
                "tail_frame_input": tail_frame_input,
                "frame_input_kind": frame_input_kind,
                "frame_input_source_type": frame_source_type,
                "frame_input_exists": new_image["exists"],
                "frame_input_copy_pending": tail_frame_copy_pending,
                "tail_frame_required": tail_frame_required,
                "tail_frame_missing": tail_frame_missing,
                "tail_frame_materializable": bool(tail_frame_copy_pending),
                "frame_input_ready_for_execution": bool(new_image["exists"] or tail_frame_copy_pending),
                "audio_files": audio_files,
                "segment_audio": segment_audio,
                "audio_in_working": audio_in_working,
                "audio_copy_pending": bool(audio_files and any(status["exists"] for status in audio_files) and not audio_in_working),
                "audio_generate_pending": bool(not audio_in_working and any(status["need_audio"] for status in audio_files)),
                "prompt": prompt,
                "raw": raw,
                "final": final,
                "tail": tail,
                "first_frame_in_storyboard": new_image["exists"],
                "final_bound": bound,
                "prompt_in_working": prompt["exists"],
                "raw_in_working": raw["exists"],
                "final_in_working": final["exists"],
                "slot_states": slot_states,
            })
        return {"tasks": tasks, "summary": summary}

    def contains_sensitive_output_false_positive(payload: Any) -> bool:
        if isinstance(payload, list):
            return any(contains_sensitive_output_false_positive(item) for item in payload)
        if isinstance(payload, dict):
            if deps.text(payload.get("code")) == "sensitive_output_detected":
                return True
            return any(contains_sensitive_output_false_positive(item) for item in payload.values())
        return "sensitive_output_detected" in deps.text(payload)

    def normalize_legacy_execution_state(execution_state: dict[str, Any], execution_result: dict[str, Any], current_hash: str) -> dict[str, Any]:
        if not isinstance(execution_state, dict) or not isinstance(execution_result, dict):
            return execution_state
        if deps.text(execution_state.get("status")) != "failed":
            return execution_state
        result_status = deps.text(execution_result.get("status"))
        if result_status != "completed":
            return execution_state
        result_hash = deps.text(execution_result.get("source_plan_hash") or execution_result.get("video_only_plan_hash"))
        state_hash = deps.text(execution_state.get("source_plan_hash"))
        if current_hash and (result_hash != current_hash or state_hash != current_hash):
            return execution_state
        if not contains_sensitive_output_false_positive(execution_state.get("error")):
            return execution_state
        summary = execution_result.get("summary") if isinstance(execution_result.get("summary"), dict) else {}
        if int(summary.get("failed_count") or 0) or int(summary.get("blocked_count") or 0):
            return execution_state
        return {
            **execution_state,
            "status": result_status,
            "error": "",
            "returncode": 0,
            "tool_status": result_status,
            "recovered_from": "legacy_sensitive_output_false_positive",
        }

    def payload_for(workspace, plan_payload: dict[str, Any]) -> dict[str, Any]:
        plan = deps.video_plan_with_hash(plan_payload, sc=deps)
        current_hash = deps.text(plan.get("plan_hash"))
        execution_state = deps.read_json(workspace / VIDEO_ONLY_PLAN_EXECUTION_STATE_REL)
        execution_result = deps.read_json(workspace / VIDEO_ONLY_PLAN_EXECUTION_RESULT_REL)
        execution_state = normalize_legacy_execution_state(execution_state, execution_result, current_hash)
        if isinstance(execution_state, dict) and not deps.text(execution_state.get("agentVideoAlias")):
            execution_state = {**execution_state, **public_video_alias_from_saved_settings(workspace)}
        state_hash = deps.text(execution_state.get("source_plan_hash"))
        result_hash = deps.text(execution_result.get("source_plan_hash") or execution_result.get("video_only_plan_hash"))
        return {
            "ok": True,
            "plan": plan,
            "plan_hash": current_hash,
            "artifact_status": deps.redact_payload(artifact_status(workspace, plan)),
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

    def bind_video_in_storyboard_payload(storyboard: dict[str, Any], asset_key: str, final_rel: str) -> bool:
        changed = False
        for shot in storyboard.get("shots") if isinstance(storyboard.get("shots"), list) else []:
            for scene in shot.get("scenes") if isinstance(shot.get("scenes"), list) else []:
                for index, dialogue in enumerate(scene_dialogues(scene), start=1):
                    if asset_key not in dialogue_match_keys(dialogue, index):
                        continue
                    bind_video_slot(dialogue, final_rel)
                    changed = True
        return changed

    def bind_new_image_in_storyboard_payload(storyboard: dict[str, Any], asset_key: str, image_rel: str, source_type: str) -> bool:
        changed = False
        for shot in storyboard.get("shots") if isinstance(storyboard.get("shots"), list) else []:
            for scene in shot.get("scenes") if isinstance(shot.get("scenes"), list) else []:
                for index, dialogue in enumerate(scene_dialogues(scene), start=1):
                    if asset_key not in dialogue_match_keys(dialogue, index):
                        continue
                    working_assets = dialogue.get("working_assets") if isinstance(dialogue.get("working_assets"), dict) else {}
                    images = working_assets.get("images") if isinstance(working_assets.get("images"), list) else []
                    if len(images) < 2:
                        images = [
                            images[0] if len(images) > 0 and isinstance(images[0], dict) else {"slot": "Image_New", "source_type": "", "path": ""},
                            images[1] if len(images) > 1 and isinstance(images[1], dict) else {"slot": "Image_02", "source_type": "", "path": ""},
                        ]
                    images[0] = {"slot": "Image_New", "source_type": source_type, "path": image_rel}
                    working_assets["images"] = images
                    dialogue["working_assets"] = working_assets
                    dialogue["bound_image_path"] = image_rel
                    changed = True
        return changed

    def backup_file(workspace, target: Path, reason: str, slot: str = "Video_Final", source: str = "video_only_confirm_final") -> dict[str, str]:
        if not target.exists():
            return {}
        batch = f"batch_{now_ms()}_{source}"
        history = workspace / ASSET_HISTORY_REL / batch
        history.mkdir(parents=True, exist_ok=True)
        backup = history / target.name
        counter = 1
        while backup.exists():
            backup = history / f"{backup.stem}_{counter}{backup.suffix}"
            counter += 1
        shutil.copy2(target, backup)
        item = {
            "schema_version": "storyboard_asset_history_0.1",
            "batch_id": batch,
            "reason": reason,
            "created_at": now_ms(),
            "items": [{"original_path": str(target.relative_to(workspace)), "history_path": str(backup.relative_to(workspace)), "slot": slot, "reason": reason, "source": source}],
        }
        deps.write_json(history / "manifest.json", item)
        return {"from": str(target.relative_to(workspace)), "to": str(backup.relative_to(workspace))}

    def ffmpeg_binary() -> str:
        bundled = OPENCREW_ROOT / "ToolLibrary" / ".bin" / "ffmpeg"
        if bundled.exists():
            return str(bundled)
        found = shutil.which("ffmpeg")
        if found:
            return found
        raise HTTPException(status_code=500, detail="ffmpeg is unavailable; cannot extract TailFrame.")

    def extract_tail_frame_for_confirm(workspace: Path, video_path: Path, output_path: Path) -> dict[str, Any]:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        command = [
            ffmpeg_binary(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-sseof",
            "-1",
            "-i",
            str(video_path),
            "-map",
            "0:v:0",
            "-an",
            "-vf",
            "reverse",
            "-frames:v",
            "1",
            str(output_path),
        ]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed.returncode != 0 or not output_path.exists() or output_path.stat().st_size <= 0:
            raise HTTPException(status_code=500, detail=f"TailFrame extraction failed: {(completed.stderr or '').strip()[:1200]}")
        return {
            "source": "ffmpeg_tail_frame_exact_reverse",
            "video_path": str(video_path.relative_to(workspace)) if video_path.is_relative_to(workspace) else str(video_path),
            "output_path": str(output_path.relative_to(workspace)) if output_path.is_relative_to(workspace) else str(output_path),
            "seek_window_seconds": 1.0,
        }

    def copy_png_tail_frame(source_path: Path, target_path: Path) -> None:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        source_suffix = source_path.suffix.lower()
        target_suffix = target_path.suffix.lower()
        if source_suffix != ".png" or target_suffix != ".png":
            raise HTTPException(status_code=409, detail="TailFrame must be regenerated as PNG before materializing it as the first frame.")
        if source_path.resolve() == target_path.resolve():
            return
        shutil.copy2(source_path, target_path)

    @router.post("/api/koubo-storyboard/tasks/{task_id}/video-only-plan")
    async def video_only_plan(task_id: int, request_payload: dict[str, Any]) -> dict[str, Any]:
        if deps.video_only_plan_lock.locked() or deps.image_plan_lock.locked() or deps.video_plan_lock.locked():
            raise HTTPException(status_code=409, detail="Plan generation is already running.")
        if deps.video_only_plan_execution_lock.locked() or deps.image_plan_execution_lock.locked() or deps.video_plan_execution_lock.locked():
            raise HTTPException(status_code=409, detail="Plan execution is running.")
        async with deps.video_only_plan_lock:
            task = deps.task_or_404(task_id)
            workspace = deps.workspace_for(task)
            current_plan, _meta = deps.load_plan(task, sc=deps)
            deps.save_edit_and_source_storyboard(task, workspace, current_plan, sc=deps)
            target = deps.video_plan_target(request_payload, current_plan, sc=deps)
            settings = deps.video_plan_settings(request_payload)
            cleanup_actions = deps.reset_video_only_plan_outputs(workspace)
            started = time.time()
            result, stdout, stderr, returncode = await asyncio.to_thread(deps.run_video_only_plan_tool, workspace, target, settings)
            generated_plan = deps.read_json(workspace / VIDEO_ONLY_PLAN_REL)
            status = deps.text(result.get("status"), "failed")
            if (returncode != 0 or status in {"failed", "blocked"}) and not generated_plan:
                raise HTTPException(status_code=500, detail=result.get("blocked_reasons") or stderr or stdout or "VideoOnlyPlan generation failed.")
            if not generated_plan:
                raise HTTPException(status_code=500, detail="VideoOnlyPlan generation completed without video_only_generation_plan.json")
            generated_plan = deps.video_plan_with_hash(generated_plan, sc=deps)
            deps.add_event(int(task["session_id"]), "koubo_storyboard.video_only_plan.generated", {
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
            return {"ok": True, "task": task, "target": target, "settings": settings, "summary": generated_plan.get("summary") or {}, "status": status, "cleanup_actions": cleanup_actions, **payload_for(workspace, generated_plan), "tool_result": result}

    @router.post("/api/koubo-storyboard/tasks/{task_id}/video-only-plan/execute")
    async def execute_video_only_plan(task_id: int, request_payload: dict[str, Any]) -> dict[str, Any]:
        if deps.video_only_plan_lock.locked():
            raise HTTPException(status_code=409, detail="VideoOnlyPlan generation is running.")
        async with deps.video_only_plan_execution_lock:
            task = deps.task_or_404(task_id)
            workspace = deps.workspace_for(task)
            plan = deps.video_plan_with_hash(deps.read_json(workspace / VIDEO_ONLY_PLAN_REL), sc=deps)
            if not plan:
                raise HTTPException(status_code=404, detail="VideoOnlyPlan does not exist. Generate it before executing.")
            current_hash = deps.text(plan.get("plan_hash"))
            mode = deps.text(request_payload.get("mode"), "prompt-only")
            if mode not in {"prompt-only", "video-only", "prompt-and-video"}:
                raise HTTPException(status_code=400, detail="mode must be prompt-only, video-only, or prompt-and-video")
            target_task_id = deps.text(request_payload.get("target_task_id"))
            target_asset_key = deps.text(request_payload.get("target_asset_key"))
            state = deps.read_json(workspace / VIDEO_ONLY_PLAN_EXECUTION_STATE_REL)
            state_hash = deps.text(state.get("source_plan_hash"))
            state_job_id = deps.text(state.get("job_id"))
            if deps.text(state.get("status")) in {"queued", "running"} and current_hash and state_hash == current_hash and state_job_id in deps.video_only_plan_execution_jobs:
                return {**payload_for(workspace, plan), "ok": True, "already_running": True, "job_id": state_job_id}
            model_selection = video_only_execution_model_selection(task, request_payload)
            public_model_selection = public_video_only_model_selection(model_selection)
            job_id = f"vop_exec_{now_ms()}"
            now_value = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            initial_marker = initial_execution_marker(workspace, plan, mode, now_value, target_task_id, target_asset_key)
            deps.write_json(workspace / VIDEO_ONLY_PLAN_EXECUTION_STATE_REL, {
                "schema_version": "analysis_v1_video_only_plan_execution_state_0.1",
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
            job = asyncio.create_task(deps.run_video_only_plan_execution_background(task_id, int(task["session_id"]), workspace, job_id, current_hash, mode, target_task_id, target_asset_key, model_selection, sc=deps))
            deps.video_only_plan_execution_jobs[job_id] = job
            deps.add_event(int(task["session_id"]), "koubo_storyboard.video_only_plan.execution_started", {"task_id": task_id, "workspace_dir": str(workspace), "job_id": job_id, "mode": mode, "source_plan_hash": current_hash, **public_model_selection})
            return {**payload_for(workspace, plan), "ok": True, "job_id": job_id, "mode": mode, "target_task_id": target_task_id, "target_asset_key": target_asset_key, **public_model_selection}

    @router.get("/api/koubo-storyboard/tasks/{task_id}/video-only-plan/execution")
    async def video_only_plan_execution(task_id: int) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        workspace = deps.workspace_for(task)
        plan = deps.video_plan_with_hash(deps.read_json(workspace / VIDEO_ONLY_PLAN_REL), sc=deps)
        if not plan:
            return {"ok": True, "plan_hash": "", "execution_state": deps.redact_payload(deps.read_json(workspace / VIDEO_ONLY_PLAN_EXECUTION_STATE_REL)), "execution_result": deps.redact_payload(deps.read_json(workspace / VIDEO_ONLY_PLAN_EXECUTION_RESULT_REL)), "artifact_status": {"tasks": []}, "binding_status": {"state_matches_current_plan": False, "result_matches_current_plan": False, "current_plan_hash": "", "state_plan_hash": "", "result_plan_hash": ""}}
        return payload_for(workspace, plan)

    @router.get("/api/koubo-storyboard/tasks/{task_id}/video-only-plan/prompts/{asset_key}")
    async def get_video_only_prompt(task_id: int, asset_key: str) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        workspace = deps.workspace_for(task)
        plan = deps.video_plan_with_hash(deps.read_json(workspace / VIDEO_ONLY_PLAN_REL), sc=deps)
        if not plan:
            raise HTTPException(status_code=404, detail="VideoOnlyPlan does not exist. Generate the plan first.")
        rel_path, path = prompt_path(workspace, plan, asset_key)
        if not path.exists():
            raise HTTPException(status_code=404, detail="Video prompt does not exist. Run Prompt first.")
        return {"ok": True, "asset_key": asset_key, "path": rel_path, "prompt": deps.redact_payload(deps.read_json(path)), "plan_hash": deps.text(plan.get("plan_hash"))}

    @router.put("/api/koubo-storyboard/tasks/{task_id}/video-only-plan/prompts/{asset_key}")
    async def save_video_only_prompt(task_id: int, asset_key: str, request_payload: dict[str, Any]) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        workspace = deps.workspace_for(task)
        plan = deps.video_plan_with_hash(deps.read_json(workspace / VIDEO_ONLY_PLAN_REL), sc=deps)
        if not plan:
            raise HTTPException(status_code=404, detail="VideoOnlyPlan does not exist. Generate the plan first.")
        prompt_payload = request_payload.get("prompt")
        if not isinstance(prompt_payload, dict):
            raise HTTPException(status_code=400, detail="prompt must be a JSON object")
        positive_prompt = deps.text(prompt_payload.get("positive_prompt"))
        negative_prompt = deps.text(prompt_payload.get("negative_prompt"))
        if not positive_prompt:
            raise HTTPException(status_code=400, detail="positive_prompt is required")
        rel_path, path = prompt_path(workspace, plan, asset_key)
        current = deps.read_json(path) if path.exists() else {}
        saved = {
            **current,
            "positive_prompt": positive_prompt,
            "negative_prompt": negative_prompt,
            "prompt": prompt_text(positive_prompt, negative_prompt),
            "asset_key": asset_key,
            "prompt_origin": "manual_or_system",
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        deps.write_json(path, saved)
        deps.add_event(int(task["session_id"]), "koubo_storyboard.video_only_plan.prompt_saved", {"task_id": task_id, "workspace_dir": str(workspace), "asset_key": asset_key, "path": rel_path})
        return {"ok": True, "asset_key": asset_key, "path": rel_path, "prompt": deps.redact_payload(saved), "plan_hash": deps.text(plan.get("plan_hash"))}

    @router.post("/api/koubo-storyboard/tasks/{task_id}/video-only-plan/prompts/{asset_key}/reload")
    async def reload_video_only_prompt(task_id: int, asset_key: str) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        workspace = deps.workspace_for(task)
        plan = deps.video_plan_with_hash(deps.read_json(workspace / VIDEO_ONLY_PLAN_REL), sc=deps)
        if not plan:
            raise HTTPException(status_code=404, detail="VideoOnlyPlan does not exist. Generate the plan first.")
        plan_task = video_only_task(plan, asset_key)
        result = await asyncio.to_thread(deps.reload_video_only_plan_prompt, workspace, deps.text(plan.get("plan_hash")), asset_key)
        rel_path, path = prompt_path(workspace, plan, asset_key)
        if not path.exists():
            raise HTTPException(status_code=500, detail="Video prompt reload did not produce a prompt file.")
        deps.add_event(int(task["session_id"]), "koubo_storyboard.video_only_plan.prompt_reloaded", {"task_id": task_id, "workspace_dir": str(workspace), "asset_key": asset_key, "path": rel_path})
        return {
            "ok": True,
            "asset_key": asset_key,
            "path": rel_path,
            "prompt": deps.redact_payload(deps.read_json(path)),
            "plan_hash": deps.text(plan.get("plan_hash")),
            "video_only_task_id": deps.text(plan_task.get("video_only_task_id")),
            "reload_result": deps.redact_payload(result),
        }

    @router.post("/api/koubo-storyboard/tasks/{task_id}/video-only-plan/segments/{asset_key}/materialize-tail-frame")
    async def materialize_video_only_tail_frame(task_id: int, asset_key: str) -> dict[str, Any]:
        if deps.video_only_plan_execution_lock.locked() or deps.video_plan_execution_lock.locked() or deps.image_plan_execution_lock.locked():
            raise HTTPException(status_code=409, detail="Execution is running. Wait before materializing TailFrame.")
        task = deps.task_or_404(task_id)
        workspace = deps.workspace_for(task)
        plan = deps.video_plan_with_hash(deps.read_json(workspace / VIDEO_ONLY_PLAN_REL), sc=deps)
        if not plan:
            raise HTTPException(status_code=404, detail="VideoOnlyPlan does not exist. Generate the plan first.")
        item = video_only_task(plan, asset_key)
        source_type = frame_input_source_type(item)
        if source_type not in previous_tail_frame_types:
            raise HTTPException(status_code=400, detail="This VideoOnly task does not use a previous TailFrame as first frame.")
        tail_rel = tail_frame_input_path(item)
        tail_status = file_status(workspace, tail_rel)
        if not tail_status.get("exists"):
            raise HTTPException(status_code=404, detail="TailFrame does not exist yet. Generate or extract the upstream TailFrame first.")
        source_rel, source_path = deps.safe_workspace_rel(workspace, deps.text(tail_status.get("path")))
        suffix = source_path.suffix.lower()
        if suffix not in IMAGE_EXTS:
            raise HTTPException(status_code=400, detail="TailFrame source must be an image file.")
        target_rel = planned_path(item, "first_frame_path", "_Image_New.png")
        if not target_rel.startswith(f"{WORKING_REL}/") or not is_standard_asset_slot_path(target_rel, asset_key, "Image_New"):
            target_rel = f"{WORKING_REL}/{deps.text(asset_key)}_Image_New{suffix}"
        _, target_path = deps.safe_workspace_rel(workspace, target_rel)
        storyboard = deps.read_json(workspace / SOURCE_REL)
        if not isinstance(storyboard, dict):
            raise HTTPException(status_code=500, detail="srt_storyboard.json is not a JSON object")
        variables = deps.read_json(workspace / "SessionContext/Variables.json")
        try:
            provider_source, privacy_grid = apply_talking_head_tail_frame_privacy(
                workspace,
                variables if isinstance(variables, dict) else {},
                storyboard,
                item,
                source_path,
                target_path.parent,
                deps.text(asset_key),
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"TalkingHead tail-frame privacy grid failed: {exc}") from exc
        backup = backup_file(workspace, target_path, "video_only_materialize_tail_frame_overwrite", "Image_New", "video_only_materialize_tail_frame")
        copy_png_tail_frame(provider_source, target_path)
        if not bind_new_image_in_storyboard_payload(storyboard, asset_key, target_rel, "tail_frame_materialized"):
            raise HTTPException(status_code=500, detail="Could not bind materialized TailFrame to srt_storyboard.json")
        edit_path = workspace / EDIT_REL
        edit = deps.read_json(edit_path) if edit_path.exists() else {}
        edit_changed = False
        if edit_path.exists():
            edit_changed = isinstance(edit, dict) and bind_new_image_in_storyboard_payload(edit, asset_key, target_rel, "tail_frame_materialized")
            if not edit_changed:
                raise HTTPException(status_code=500, detail="Could not bind materialized TailFrame to koubo_storyboard_edit.json")
        deps.write_json(workspace / SOURCE_REL, storyboard)
        if edit_changed:
            deps.write_json(edit_path, edit)
        deps.add_event(int(task["session_id"]), "koubo_storyboard.video_only_plan.tail_frame_materialized", {"task_id": task_id, "workspace_dir": str(workspace), "asset_key": asset_key, "tail_frame_path": source_rel, "image_path": target_rel, "backup": backup, "privacy_grid": privacy_grid})
        return {**payload_for(workspace, plan), "ok": True, "asset_key": asset_key, "tail_frame_path": source_rel, "image_path": target_rel, "backup": backup, "privacy_grid": privacy_grid}

    @router.post("/api/koubo-storyboard/tasks/{task_id}/video-only-plan/segments/{asset_key}/confirm-final")
    async def confirm_video_only_final(task_id: int, asset_key: str) -> dict[str, Any]:
        if deps.video_only_plan_execution_lock.locked() or deps.video_plan_execution_lock.locked() or deps.image_plan_execution_lock.locked():
            raise HTTPException(status_code=409, detail="Execution is running. Wait before confirming Final.")
        task = deps.task_or_404(task_id)
        workspace = deps.workspace_for(task)
        plan = deps.video_plan_with_hash(deps.read_json(workspace / VIDEO_ONLY_PLAN_REL), sc=deps)
        if not plan:
            raise HTTPException(status_code=404, detail="VideoOnlyPlan does not exist. Generate the plan first.")
        item = video_only_task(plan, asset_key)
        planned_raw_rel = planned_path(item, "raw_video_path", "_Video_Raw.mp4")
        planned_final_rel = planned_path(item, "final_video_path", "_Video_Final.mp4")
        tail_rel = planned_path(item, "tail_frame_path", "_TailFrame.png")
        storyboard = deps.read_json(workspace / SOURCE_REL)
        if not isinstance(storyboard, dict):
            raise HTTPException(status_code=500, detail="Could not read srt_storyboard.json")
        dialogue = storyboard_dialogue_for_asset(storyboard, asset_key)
        actual_asset_key = deps.text(dialogue.get("dialogue_asset_key")) or deps.text(asset_key)
        asset_keys = [actual_asset_key]
        if deps.text(asset_key) not in asset_keys:
            asset_keys.append(deps.text(asset_key))
        raw_status = first_existing_status(workspace, [
            existing_working_slot_for_keys(workspace, asset_keys, "Video_Raw", VIDEO_EXTS, ".mp4"),
            planned_raw_rel,
        ])
        final_status = first_existing_status(workspace, [
            deps.text(dialogue_video_slot(dialogue).get("path")),
            existing_working_slot_for_keys(workspace, asset_keys, "Video_Final", VIDEO_EXTS, ".mp4"),
            planned_final_rel,
        ])
        raw_rel = deps.text(raw_status.get("path")) or planned_raw_rel
        final_rel = deps.text(final_status.get("path")) or planned_final_rel
        if not final_status.get("exists"):
            final_rel = planned_final_rel
        _, raw_path = deps.safe_workspace_rel(workspace, raw_rel)
        _, final_path = deps.safe_workspace_rel(workspace, final_rel)
        raw_exists = bool(raw_status.get("exists"))
        final_exists = bool(final_status.get("exists"))
        if not final_exists and not raw_exists:
            raise HTTPException(status_code=404, detail="Final video does not exist and Raw video does not exist. Generate Video first.")
        _, tail_path = deps.safe_workspace_rel(workspace, tail_rel)
        if not isinstance(storyboard, dict) or not bind_video_in_storyboard_payload(storyboard, asset_key, final_rel):
            raise HTTPException(status_code=500, detail="Could not bind Final video to srt_storyboard.json")
        edit_path = workspace / EDIT_REL
        edit = deps.read_json(edit_path) if edit_path.exists() else {}
        edit_changed = isinstance(edit, dict) and bind_video_in_storyboard_payload(edit, asset_key, final_rel)
        backup = {}
        tail_backup = {}
        tail_frame = {}
        action = "bound_existing_final"
        if not final_exists:
            backup = backup_file(workspace, final_path, "video_only_confirm_final_overwrite")
            final_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(raw_path, final_path)
            action = "copied_raw_to_final"
        if raw_exists:
            tail_backup = backup_file(workspace, tail_path, "video_only_confirm_final_tail_frame_overwrite", "TailFrame")
            tail_frame = extract_tail_frame_for_confirm(workspace, final_path, tail_path)
        deps.write_json(workspace / SOURCE_REL, storyboard)
        if edit_changed:
            deps.write_json(edit_path, edit)
        deps.add_event(int(task["session_id"]), "koubo_storyboard.video_only_plan.confirm_final", {"task_id": task_id, "workspace_dir": str(workspace), "asset_key": asset_key, "raw_path": raw_rel, "final_path": final_rel, "tail_frame_path": tail_rel, "backup": backup, "tail_backup": tail_backup, "tail_frame": tail_frame, "action": action})
        return {**payload_for(workspace, plan), "ok": True, "asset_key": asset_key, "raw_path": raw_rel, "final_path": final_rel, "tail_frame_path": tail_rel, "backup": backup, "tail_backup": tail_backup, "tail_frame": tail_frame, "action": action}
