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
import threading
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
from .tts_selection_recovery import rehydrate_storyboard_tts_selection
from .usage_metering import record_storyboard_usage, stable_usage_request_id, tts_usage_units


SERVICE_EXPORTS = (
    "read_locked_tts_cache",
    "write_locked_tts_manifest",
    "update_scene_audio_path",
    "update_dialogue_audio_path",
    "reserve_tts_output_generation",
    "video_plan_tts_audio_preflight",
    "prepare_video_plan_selected_tts_audio",
    "run_scene_tts_candidate",
)


def tts_output_generation_key(workspace: Path, output_rel: str) -> str:
    return str((workspace / output_rel).resolve())


def tts_output_lock(workspace: Path, output_rel: str, *, sc: Any) -> Any:
    key = tts_output_generation_key(workspace, output_rel)
    guard = getattr(sc, "tts_output_lock_guard", None)
    if guard is None:
        guard = threading.Lock()
        sc.tts_output_lock_guard = guard
    with guard:
        locks = getattr(sc, "tts_output_locks", None)
        if not isinstance(locks, dict):
            locks = {}
            sc.tts_output_locks = locks
        return locks.setdefault(key, threading.Lock())


def reserve_tts_output_generation(workspace: Path, output_rel: str, config_key: str, *, sc: Any) -> str:
    key = tts_output_generation_key(workspace, output_rel)
    normalized_config_key = sc.text(config_key)
    guard = getattr(sc, "tts_output_lock_guard", None)
    if guard is None:
        guard = threading.Lock()
        sc.tts_output_lock_guard = guard
    with guard:
        generations = getattr(sc, "tts_output_generations", None)
        if not isinstance(generations, dict):
            generations = {}
            sc.tts_output_generations = generations
        current = generations.get(key) if isinstance(generations.get(key), dict) else {}
        if normalized_config_key and current.get("config_key") == normalized_config_key and current.get("token"):
            return current["token"]
        token = uuid.uuid4().hex
        generations[key] = {"token": token, "config_key": normalized_config_key}
        return token


def tts_output_generation_is_current(workspace: Path, output_rel: str, token: str, *, sc: Any) -> bool:
    if not token:
        return True
    key = tts_output_generation_key(workspace, output_rel)
    guard = getattr(sc, "tts_output_lock_guard", None)
    if guard is None:
        return True
    with guard:
        generations = getattr(sc, "tts_output_generations", None)
        current = generations.get(key) if isinstance(generations, dict) and isinstance(generations.get(key), dict) else {}
        return current.get("token") == token


def clean_tts_identifier(value: Any, *, sc: Any) -> str:
    normalized = sc.text(value)
    return "" if normalized.lower() in {"[model]", "[provider]", "model", "provider"} else normalized


def canonical_tts_text(value: Any, *, sc: Any) -> str:
    return re.sub(r"\s+", " ", sc.text(value)).strip()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def storyboard_tts_selection_config(storyboard: dict[str, Any], *, sc: Any, workspace: Path | None = None) -> dict[str, Any]:
    raw_selection = storyboard.get("storyboard_tts_selection") if isinstance(storyboard.get("storyboard_tts_selection"), dict) else {}
    public_voice_id = sc.text(raw_selection.get("voice_id") or raw_selection.get("voice"))
    if not raw_selection or not public_voice_id:
        return {}
    try:
        normalized_storyboard = rehydrate_storyboard_tts_selection(
            sc.ctx,
            workspace or Path("."),
            storyboard,
            strict=True,
            read_json=sc.read_json,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={
            "code": "selected_tts_voice_unavailable",
            "message": "所选音色已失效，请重新选择音色后再执行视频计划。",
        }) from exc
    selection = normalized_storyboard.get("storyboard_tts_selection") if isinstance(normalized_storyboard.get("storyboard_tts_selection"), dict) else {}
    provider = clean_tts_identifier(selection.get("provider") or selection.get("source_clone_provider"), sc=sc)
    model = clean_tts_identifier(selection.get("model") or selection.get("target_model"), sc=sc)
    voice_id = clean_tts_identifier(selection.get("voice_id") or selection.get("voice"), sc=sc)
    if not provider or not model or not voice_id:
        raise HTTPException(status_code=409, detail={
            "code": "selected_tts_voice_unavailable",
            "message": "当前音色缺少可用的生成配置，请重新选择音色后再执行视频计划。",
        })
    tempo = sc.number(raw_selection.get("tempo") or selection.get("tempo")) or 1.0
    return {
        "provider": provider,
        "model": model,
        "voice_id": voice_id,
        "public_provider": clean_tts_identifier(raw_selection.get("provider"), sc=sc),
        "public_model": clean_tts_identifier(raw_selection.get("model"), sc=sc),
        "public_voice_id": public_voice_id,
        "candidate_id": sc.text(selection.get("candidate_id")),
        "voice_source": sc.text(selection.get("voice_source") or raw_selection.get("voice_source")),
        "voice_label": sc.text(raw_selection.get("voice_label") or raw_selection.get("label") or public_voice_id),
        "prompt": sc.text(raw_selection.get("prompt") or raw_selection.get("prompt_template") or selection.get("prompt") or selection.get("prompt_template")),
        "tempo": tempo,
    }


def video_plan_dialogue_index(storyboard: dict[str, Any], *, sc: Any) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for shot in storyboard.get("shots") or []:
        if not isinstance(shot, dict):
            continue
        for scene in shot.get("scenes") or []:
            if not isinstance(scene, dict):
                continue
            for dialogue in scene.get("dialogues") or []:
                if not isinstance(dialogue, dict):
                    continue
                asset_key = sc.text(dialogue.get("dialogue_asset_key"))
                if asset_key:
                    result[asset_key] = {"shot": shot, "scene": scene, "dialogue": dialogue}
    return result


def selected_tts_manifest_matches(workspace: Path, manifest: dict[str, Any], selection: dict[str, Any], dialogue: dict[str, Any], output_rel: str, *, sc: Any) -> bool:
    if not manifest:
        return False
    if sc.text(manifest.get("provider")) != selection["provider"]:
        return False
    if sc.text(manifest.get("model")) != selection["model"]:
        return False
    if sc.text(manifest.get("voice_id")) != selection["voice_id"]:
        return False
    if sc.text(manifest.get("output")) != output_rel:
        return False
    output_path = workspace / output_rel
    expected_sha256 = sc.text(manifest.get("output_sha256"))
    if not expected_sha256 or not output_path.is_file() or file_sha256(output_path) != expected_sha256:
        return False
    if canonical_tts_text(manifest.get("text"), sc=sc) != canonical_tts_text(dialogue.get("text"), sc=sc):
        return False
    manifest_tempo = sc.number(manifest.get("tempo")) or 1.0
    return abs(manifest_tempo - float(selection["tempo"])) <= 0.0001


def video_plan_tts_audio_tasks(workspace: Path, video_plan: dict[str, Any], storyboard: dict[str, Any], selection: dict[str, Any], *, sc: Any) -> list[dict[str, Any]]:
    dialogue_index = video_plan_dialogue_index(storyboard, sc=sc)
    pending: list[dict[str, Any]] = []
    seen: set[str] = set()
    for shot in video_plan.get("shots") or []:
        for scene in shot.get("scenes") or []:
            for segment in scene.get("segments") or []:
                for audio_task in segment.get("dialogue_audio_tasks") or []:
                    if not isinstance(audio_task, dict):
                        continue
                    asset_key = sc.text(audio_task.get("dialogue_asset_key"))
                    if not asset_key or asset_key in seen:
                        continue
                    info = dialogue_index.get(asset_key) or {}
                    dialogue = info.get("dialogue") if isinstance(info.get("dialogue"), dict) else {}
                    existing_rel = sc.text(audio_task.get("existing_audio_path"))
                    planned_rel = sc.text(audio_task.get("planned_audio_path")) or f"SessionOutput/storyboard/Working/{asset_key}_Audio_Final.wav"
                    existing_path = sc.safe_workspace_rel(workspace, existing_rel)[1] if existing_rel else None
                    planned_rel, planned_path = sc.safe_workspace_rel(workspace, planned_rel)
                    existing_file = bool(existing_path and existing_path.is_file() and existing_path.stat().st_size > 0)
                    planned_file = bool(planned_path.is_file() and planned_path.stat().st_size > 0)
                    output_rel = existing_rel if existing_file else planned_rel
                    reason = "missing_audio" if not (existing_file or planned_file) else ""
                    if not reason and selection and dialogue:
                        assets = dialogue.get("working_assets") if isinstance(dialogue.get("working_assets"), dict) else {}
                        audio_asset = assets.get("audio") if isinstance(assets.get("audio"), dict) else {}
                        source_type = sc.asset_source_type(output_rel, audio_asset.get("source_type"), sc=sc)
                        managed_output = output_rel.startswith("SessionOutput/storyboard/Working/") and source_type == "generated"
                        if managed_output:
                            manifest = sc.read_json(workspace / "SessionOutput" / "storyboard" / "tts_manifests" / f"{asset_key}_Audio_Final.json")
                            if not selected_tts_manifest_matches(workspace, manifest, selection, dialogue, output_rel, sc=sc):
                                reason = "selected_voice_mismatch"
                    if not reason:
                        continue
                    seen.add(asset_key)
                    pending.append({
                        "asset_key": asset_key,
                        "srt_id": sc.text(audio_task.get("srt_id")),
                        "output": output_rel,
                        "reason": reason,
                        "shot": info.get("shot") or {},
                        "scene": info.get("scene") or {},
                        "dialogue": dialogue,
                    })
    return pending


def video_plan_tts_audio_preflight(workspace: Path, video_plan: dict[str, Any], storyboard: dict[str, Any], *, sc: Any) -> dict[str, Any]:
    raw_selection = storyboard.get("storyboard_tts_selection") if isinstance(storyboard.get("storyboard_tts_selection"), dict) else {}
    provisional_tasks = video_plan_tts_audio_tasks(workspace, video_plan, storyboard, {}, sc=sc)
    selection = storyboard_tts_selection_config(storyboard, sc=sc, workspace=workspace) if raw_selection else {}
    tasks = video_plan_tts_audio_tasks(workspace, video_plan, storyboard, selection, sc=sc) if selection else provisional_tasks
    if tasks and not selection:
        raise HTTPException(status_code=409, detail={
            "code": "selected_tts_audio_missing",
            "message": "视频计划缺少对白音频，请先选择并生成音色后再执行。",
            "dialogue_asset_keys": [item["asset_key"] for item in tasks],
        })
    return {
        "requires_generation": bool(tasks),
        "generation_count": len(tasks),
        "dialogue_asset_keys": [item["asset_key"] for item in tasks],
        "reasons": sorted({item["reason"] for item in tasks}),
        "voice_label": selection.get("voice_label", "") if selection else "",
    }


def apply_selected_tts_prompt(prompt: str, provider: str, text_value: str) -> str:
    prompt_value = str(prompt or "").strip()
    text_value = str(text_value or "").strip()
    strip_pattern = r"(?:朗读文本|正文|Text)\s*[:：]\s*[\s\S]*$"
    if "{text}" in prompt_value:
        return prompt_value.replace("{text}", text_value)
    instruction = re.sub(strip_pattern, "", prompt_value, flags=re.IGNORECASE).strip()
    if provider == "google":
        return text_value if not instruction else f"{instruction}\n\n正文：{text_value}"
    return f"{instruction}\n\n严格朗读当前 Scene 文本，不要朗读 prompt 中的示例文本或历史文本。".strip()


def prepare_video_plan_selected_tts_audio(task: dict[str, Any], workspace: Path, *, sc: Any) -> dict[str, Any]:
    video_plan = sc.read_json(workspace / VIDEO_PLAN_REL)
    storyboard, _meta = sc.load_plan(task, sc=sc)
    selection = storyboard_tts_selection_config(storyboard, sc=sc, workspace=workspace)
    tasks = video_plan_tts_audio_tasks(workspace, video_plan, storyboard, selection, sc=sc)
    if tasks and not selection:
        raise HTTPException(status_code=409, detail="A selected TTS voice is required before VideoPlan execution.")
    generated: list[dict[str, Any]] = []
    for item in tasks:
        dialogue = item["dialogue"]
        text_value = sc.text(dialogue.get("text"))
        if not text_value:
            raise HTTPException(status_code=400, detail=f"Dialogue text is missing: {item['asset_key']}")
        prompt = apply_selected_tts_prompt(selection["prompt"], selection["provider"], text_value)
        tempo_value = float(selection["tempo"])
        public_tempo: int | float = int(tempo_value) if tempo_value.is_integer() else tempo_value
        config_key = json.dumps({
            "dialogueId": sc.text(dialogue.get("dialogue_id")),
            "assetKey": item["asset_key"],
            "provider": selection["public_provider"],
            "model": selection["public_model"],
            "voiceId": selection["public_voice_id"],
            "prompt": prompt,
            "text": text_value,
            "tempo": public_tempo,
        }, ensure_ascii=False, separators=(",", ":"))
        manifest_rel = f"SessionOutput/storyboard/tts_manifests/{item['asset_key']}_Audio_Final.json"
        request_payload = {
            "workflow_id": f"video_plan_dialogue_tts_{item['asset_key']}",
            "task_id": int(task.get("id") or 0),
            "session_id": int(task.get("session_id") or 0),
            "shot_id": sc.text(item["shot"].get("shot_id")),
            "scene_mark_id": sc.text(item["scene"].get("scene_id")),
            "dialogue_id": sc.text(dialogue.get("dialogue_id")),
            "dialogue_asset_key": item["asset_key"],
            "srt_text": text_value,
            "use_locked_cache": True,
            "locked_output": item["output"],
            "locked_manifest": manifest_rel,
            "locked_config_key": config_key,
            "output": item["output"],
        }
        token = reserve_tts_output_generation(workspace, item["output"], config_key, sc=sc)
        request_payload["output_generation_token"] = token
        prompt_item = {
            "provider": selection["provider"],
            "model": selection["model"],
            "voice_id": selection["voice_id"],
            "voice_source": selection["voice_source"],
            "candidate_id": selection["candidate_id"],
            "prompt": prompt,
            "text": text_value,
            "tempo": selection["tempo"],
        }
        result = sc.run_scene_tts_candidate(task, workspace, request_payload, prompt_item, item["output"], sc=sc)
        generated.append({
            "dialogue_asset_key": item["asset_key"],
            "output": sc.text(result.get("output")),
            "duration_seconds": result.get("duration_seconds") or 0,
            "cache_hit": bool(result.get("cache_hit")),
            "reason": item["reason"],
        })
    return {"generated_count": len(generated), "items": generated}


def read_locked_tts_cache(workspace: Path, payload: dict[str, Any], prompt_item: dict[str, Any], workflow_id: str, *, sc: Any) -> dict[str, Any] | None:
    if not payload.get("use_locked_cache") or not sc.text(payload.get("locked_output")) or not sc.text(payload.get("locked_manifest")) or not sc.text(payload.get("locked_config_key")):
        return None
    output_rel, output_path = sc.safe_workspace_rel(workspace, sc.text(payload.get("locked_output")))
    _, manifest_path = sc.safe_workspace_rel(workspace, sc.text(payload.get("locked_manifest")))
    manifest = sc.read_json(manifest_path)
    if not manifest or sc.text(manifest.get("config_key")) != sc.text(payload.get("locked_config_key")):
        return None
    if not sc.locked_tts_cache_text_matches(payload, manifest):
        return None
    if sc.text(manifest.get("output"), output_rel) != output_rel:
        return None
    if not output_path.exists() or not output_path.is_file() or output_path.stat().st_size <= 0:
        return None
    expected_sha256 = sc.text(manifest.get("output_sha256"))
    if expected_sha256 and file_sha256(output_path) != expected_sha256:
        return None
    duration = sc.number(manifest.get("duration_seconds") or manifest.get("duration") or sc.audio_duration_seconds(output_path))
    if sc.tts_duration_is_suspicious(sc.text(payload.get("srt_text")), duration):
        return None
    return {
        "workflow_id": workflow_id,
        "shot_id": sc.text(payload.get("shot_id")),
        "scene_mark_id": sc.text(payload.get("scene_mark_id")),
        "input_mode": "tts",
        "srt_text": sc.text(payload.get("srt_text")),
        "api_call_id": f"{workflow_id}-locked-cache-{now_ms()}",
        "candidate_id": sc.text(manifest.get("candidate_id"), f"{manifest.get('provider') or prompt_item.get('provider') or 'provider'}_tts_locked"),
        "provider": sc.text(manifest.get("provider"), sc.text(prompt_item.get("provider"))),
        "model": sc.text(manifest.get("model"), sc.text(prompt_item.get("model"))),
        "voice_id": sc.text(manifest.get("voice_id"), sc.text(prompt_item.get("voice_id"))),
        "output": output_rel,
        "output_path": str(output_path),
        "duration_seconds": round(duration, 3) if duration else 0,
        "raw_duration": manifest.get("raw_duration"),
        "fit_duration": round(duration, 3) if duration else 0,
        "speed_factor": manifest.get("speed_factor"),
        "tempo": manifest.get("tempo") or prompt_item.get("tempo"),
        "stretched": manifest.get("stretched"),
        "elapsed_seconds": 0,
        "audio_url": "",
        "local_preview": False,
        "ok": True,
        "status": "completed",
        "cache_hit": True,
        "locked_manifest": sc.text(payload.get("locked_manifest")),
        "locked": True,
    }


def write_locked_tts_manifest(workspace: Path, task: dict[str, Any], payload: dict[str, Any], prompt_item: dict[str, Any], result: dict[str, Any], *, sc: Any) -> None:
    if not sc.text(payload.get("locked_manifest")) or not sc.text(payload.get("locked_config_key")):
        return
    _, manifest_path = sc.safe_workspace_rel(workspace, sc.text(payload.get("locked_manifest")))
    output_rel = sc.text(result.get("output"), sc.text(payload.get("locked_output")))
    output_path = workspace / output_rel if output_rel else None
    output_sha256 = file_sha256(output_path) if output_path and output_path.is_file() else ""
    output_size = output_path.stat().st_size if output_path and output_path.is_file() else 0
    manifest = {
        "status": "locked",
        "scope": "koubo_storyboard_scene_tts",
        "config_key": sc.text(payload.get("locked_config_key")),
        "workflow_id": sc.text(result.get("workflow_id"), sc.text(payload.get("workflow_id"))),
        "task_id": int(task.get("id") or 0) or None,
        "session_id": int(task.get("session_id") or 0) or None,
        "shot_id": sc.text(payload.get("shot_id")),
        "scene_mark_id": sc.text(payload.get("scene_mark_id")),
        "text": sc.text(payload.get("srt_text")),
        "prompt": sc.text(prompt_item.get("prompt")),
        "provider": sc.text(result.get("provider"), sc.text(prompt_item.get("provider"))),
        "model": sc.text(result.get("model"), sc.text(prompt_item.get("model"))),
        "voice_id": sc.text(result.get("voice_id"), sc.text(prompt_item.get("voice_id"))),
        "tempo": result.get("tempo") or prompt_item.get("tempo"),
        "speed_factor": result.get("speed_factor"),
        "stretched": result.get("stretched"),
        "duration_seconds": result.get("duration_seconds") or result.get("duration"),
        "raw_duration": result.get("raw_duration"),
        "output": output_rel,
        "output_path": str(workspace / output_rel) if output_rel else "",
        "output_sha256": output_sha256,
        "output_size": output_size,
        "candidate_id": sc.text(result.get("candidate_id")),
        "updated_at": now_ms(),
    }
    sc.write_json(manifest_path, manifest)


def update_scene_audio_path(workspace: Path, task: dict[str, Any], scene_id: str, output_rel: str, duration_seconds: Any = None, *, sc: Any) -> None:
    if not scene_id or not output_rel:
        return
    source = sc.read_json(workspace / SOURCE_REL)
    if not source:
        return
    edit = sc.read_json(workspace / EDIT_REL)
    plan = edit if edit.get("schema_version") == "koubo_storyboard_edit_0.1" else sc.normalize_source_plan(task, source, sc=sc)
    plan = sc.recalculate(plan, sc=sc)
    for shot in plan.get("shots") or []:
        for scene in shot.get("scenes") or []:
            if sc.text(scene.get("scene_id")) != scene_id:
                continue
            sc.ensure_working_assets(scene, sc=sc)["audio"]["path"] = output_rel
            duration_value = sc.number(duration_seconds)
            if duration_value > 0:
                scene["duration"] = round(duration_value, 3)
                scene["end"] = round(sc.number(scene.get("start")) + duration_value, 3)
            next_plan = sc.recalculate({
                **plan,
                "schema_version": "koubo_storyboard_edit_0.1",
                "title": "故事版（口播）",
                "source_type": "analysis_v1_storyboard",
                "analysis_task_id": int(task["id"]),
                "analysis_session_id": int(task["session_id"]),
                "source_path": SOURCE_REL,
            }, sc=sc)
            _current_source, source_signature, _refreshed = sc.current_storyboard_source(workspace, source)
            sc.apply_storyboard_source_signature(next_plan, source_signature)
            sc.save_edit_and_source_storyboard(task, workspace, next_plan, sc=sc)
            return


def update_dialogue_audio_path(workspace: Path, task: dict[str, Any], dialogue_id: str, output_rel: str, dialogue_asset_key: str = "", duration_seconds: Any = None, *, sc: Any) -> None:
    dialogue_id = sc.text(dialogue_id)
    dialogue_asset_key = sc.text(dialogue_asset_key)
    if not (dialogue_id or dialogue_asset_key) or not output_rel:
        return
    source = sc.read_json(workspace / SOURCE_REL)
    if not source:
        return
    edit = sc.read_json(workspace / EDIT_REL)
    plan = edit if edit.get("schema_version") == "koubo_storyboard_edit_0.1" else sc.normalize_source_plan(task, source, sc=sc)
    plan = sc.recalculate(plan, sc=sc)
    for shot in plan.get("shots") or []:
        for scene in shot.get("scenes") or []:
            for dialogue in scene.get("dialogues") or []:
                if dialogue_asset_key:
                    if sc.text(dialogue.get("dialogue_asset_key")) != dialogue_asset_key:
                        continue
                elif sc.text(dialogue.get("dialogue_id")) != dialogue_id:
                    continue
                sc.ensure_dialogue_working_assets(dialogue, sc=sc)["audio"] = {"slot": "Audio_Final", "source_type": sc.asset_source_type(output_rel, sc=sc), "path": output_rel}
                duration_value = sc.number(duration_seconds)
                if duration_value > 0:
                    dialogue["duration"] = round(duration_value, 3)
                    dialogue["end"] = round(sc.number(dialogue.get("start")) + duration_value, 3)
                next_plan = sc.recalculate({
                    **plan,
                    "schema_version": "koubo_storyboard_edit_0.1",
                    "title": "故事版（口播）",
                    "source_type": "analysis_v1_storyboard",
                    "analysis_task_id": int(task["id"]),
                    "analysis_session_id": int(task["session_id"]),
                    "source_path": SOURCE_REL,
                }, sc=sc)
                _current_source, source_signature, _refreshed = sc.current_storyboard_source(workspace, source)
                sc.apply_storyboard_source_signature(next_plan, source_signature)
                sc.save_edit_and_source_storyboard(task, workspace, next_plan, sc=sc)
                return


def run_scene_tts_candidate(task: dict[str, Any], workspace: Path, request_payload: dict[str, Any], prompt_item: dict[str, Any], output_rel: str, *, sc: Any) -> dict[str, Any]:
    provider = sc.text(prompt_item.get("provider"))
    model_id = sc.text(prompt_item.get("model"))
    voice_id = sc.text(prompt_item.get("voice_id"))
    prompt = sc.text(prompt_item.get("prompt"))
    text_value = sc.text(prompt_item.get("text"))
    tempo = sc.number(prompt_item.get("tempo")) or None
    if not text_value:
        raise HTTPException(status_code=400, detail="TTS text is required")
    output_rel, output_path = sc.safe_workspace_rel(workspace, output_rel)
    config_key = sc.text(request_payload.get("locked_config_key")) or json.dumps({
        "provider": provider,
        "model": model_id,
        "voice_id": voice_id,
        "prompt": prompt,
        "text": text_value,
        "tempo": tempo or 1.0,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    generation_token = sc.text(request_payload.get("output_generation_token")) or reserve_tts_output_generation(workspace, output_rel, config_key, sc=sc)
    request_payload = {**request_payload, "output_generation_token": generation_token}
    with tts_output_lock(workspace, output_rel, sc=sc):
        if not tts_output_generation_is_current(workspace, output_rel, generation_token, sc=sc):
            raise HTTPException(status_code=409, detail="A newer TTS request replaced this generation.")
        cached = read_locked_tts_cache(
            workspace,
            request_payload,
            prompt_item,
            sc.text(request_payload.get("workflow_id"), "koubo_storyboard_scene_tts"),
            sc=sc,
        )
        if cached:
            return cached

        config = sc.load_tts_config(provider, model_id, sc=sc)
        raw_output_rel = output_rel
        raw_output_path = output_path
        if tempo and abs(tempo - 1.0) > 0.0001:
            output_rel_path = Path(output_rel)
            raw_output_rel = output_rel_path.with_name(f"{output_rel_path.stem}_raw{output_rel_path.suffix}").as_posix()
            _, raw_output_path = sc.safe_workspace_rel(workspace, raw_output_rel)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temp_output_path = output_path.with_name(f".{output_path.stem}.{generation_token}.tmp{output_path.suffix}")
        temp_raw_path = temp_output_path
        if raw_output_rel != output_rel:
            temp_raw_path = raw_output_path.with_name(f".{raw_output_path.stem}.{generation_token}.tmp{raw_output_path.suffix}")
        temporary_paths = {temp_output_path, temp_raw_path}
        started = time.time()
        call_detail = {**request_payload, "provider": provider, "model": model_id, "voice_id": voice_id, "method": "POST", "input_mode": "tts", "text_preview": text_value[:500], "prompt_preview": prompt[:1000], "prompt_length": len(prompt), "workspace_dir": str(workspace), "output": output_rel, "output_path": str(output_path), "raw_output": raw_output_rel if raw_output_rel != output_rel else "", "raw_output_path": str(raw_output_path) if raw_output_rel != output_rel else "", "tempo": tempo, "temporary": False, "writes_asset_json": True}
        sc.ctx.session_repo.add_event(int(task["session_id"]), "koubo_storyboard.scene_tts.provider_call.started", json.dumps(call_detail, ensure_ascii=True), now_ms())
        try:
            audio_url = sc.generate_tts_audio(config, text_value, voice_id, prompt, temp_raw_path, sc=sc)
            raw_duration = sc.audio_duration_seconds(temp_raw_path)
            retry_warnings: list[str] = []
            if provider == "google" and sc.tts_duration_is_suspicious(text_value, raw_duration):
                retry_warnings.append("google_tts_duration_suspicious_retried_text_only")
                minimal_prompt = f"请只朗读以下正文，不要朗读任何说明、标题或提示词。\\n\\n正文：{text_value}"
                audio_url = sc.generate_tts_audio(config, text_value, voice_id, minimal_prompt, temp_raw_path, sc=sc)
                raw_duration = sc.audio_duration_seconds(temp_raw_path)
            if sc.tts_duration_is_suspicious(text_value, raw_duration):
                raise HTTPException(status_code=502, detail=f"TTS duration looks wrong for this dialogue: text_chars={sc.spoken_char_count(text_value)}, duration={raw_duration}s")
            stretch: dict[str, Any] = {"raw_duration": raw_duration, "speed_factor": 1.0, "tempo": tempo or 1.0, "stretched": False, "warnings": retry_warnings}
            if raw_output_rel != output_rel:
                stretch = sc.tempo_stretch_audio(temp_raw_path, temp_output_path, tempo)
                stretch["warnings"] = [*(stretch.get("warnings") or []), *retry_warnings]
                audio_url = ""
            duration_seconds = sc.audio_duration_seconds(temp_output_path)
            if not tts_output_generation_is_current(workspace, output_rel, generation_token, sc=sc):
                raise HTTPException(status_code=409, detail="A newer TTS request replaced this generation.")
            if raw_output_rel != output_rel:
                os.replace(temp_raw_path, raw_output_path)
            os.replace(temp_output_path, output_path)
            usage_request_id = stable_usage_request_id("koubo_scene_tts", task["id"], task.get("latest_attempt_id"), output_rel, provider, config["model"])
            local_usage = record_storyboard_usage(
                sc.ctx,
                task,
                request_id=usage_request_id,
                provider=config["provider"],
                model_id=config["model"],
                modality="tts",
                step_id="koubo_storyboard.scene_tts",
                units=tts_usage_units(text_value, prompt=prompt, audio_seconds=duration_seconds, output_bytes=output_path.stat().st_size if output_path.exists() else 0),
                started_at=started,
                finished_at=time.time(),
            )
            result = {**call_detail, "ok": True, "output": output_rel, "output_path": str(output_path), "raw_output": raw_output_rel if raw_output_rel != output_rel else "", "raw_output_path": str(raw_output_path) if raw_output_rel != output_rel else "", "duration_seconds": duration_seconds, "raw_duration": stretch.get("raw_duration"), "fit_duration": duration_seconds, "speed_factor": stretch.get("speed_factor"), "tempo": stretch.get("tempo") or tempo, "stretched": stretch.get("stretched"), "fit_warnings": stretch.get("warnings") or [], "elapsed_seconds": round(time.time() - started, 3), "audio_url": audio_url, "local_preview": False, "local_usage": local_usage, "local_usage_id": local_usage.get("local_usage_id", "")}
            write_locked_tts_manifest(workspace, task, request_payload, prompt_item, result, sc=sc)
            sc.ctx.session_repo.add_event(int(task["session_id"]), "koubo_storyboard.scene_tts.generated", json.dumps(result, ensure_ascii=True), now_ms())
            return result
        finally:
            for temporary_path in temporary_paths:
                if temporary_path.exists():
                    temporary_path.unlink()


def register_tts_workflow_services(ns: Any) -> None:
    for name in SERVICE_EXPORTS:
        setattr(ns, name, globals()[name])
