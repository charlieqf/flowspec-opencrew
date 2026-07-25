from __future__ import annotations

import base64
import http.client
import importlib.util
import json
import mimetypes
import os
import re
import shutil
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text as sql_text

from opcrew_backend.context import now_ms
from opcrew_backend.services.media_sanitize import MediaSanitizeError, sanitize_video_file_metadata, write_sanitized_image_bytes
try:
    from opcrew_model_config.media_model_config import CONFIG_TABLE, customer_media_public_alias_target, ensure_table, load_agent_model_aliases, load_config, load_stored_key
except ModuleNotFoundError:  # pragma: no cover - standalone contract-test import path
    from opcrew_backend.routes.media_model_config import CONFIG_TABLE, customer_media_public_alias_target, ensure_table, load_agent_model_aliases, load_config, load_stored_key

from .constants import *
from .gemini_omni_video_services import (
    GEMINI_OMNI_720P_USD_PER_SECOND,
    GEMINI_OMNI_MODEL,
    GeminiOmniClient,
    GeminiOmniError,
    build_interaction_request,
    file_input as gemini_omni_file_input,
    materialize_video_output as materialize_gemini_omni_video_output,
    omni_task_for,
    require_gemini_omni_enabled,
)
from .runtime import resolve_media_binary
from .usage_metering import image_usage_units, record_storyboard_usage, stable_usage_request_id, video_usage_units
from .video_interaction_repository import (
    VideoInteractionError,
    VideoInteractionRepository,
    public_turn as public_video_interaction_turn,
)


VIDEO_REFERENCE_PREFIXES = (
    f"{ASSET_IMAGES_REL}/",
    f"{WORKING_REL}/",
    "SessionContext/Consistency/",
)
VIDEO_MEDIA_REFERENCE_PREFIXES = (
    f"{ASSET_IMAGES_REL}/",
    f"{ASSET_VIDEOS_REL}/",
    f"{ASSET_AUDIOS_REL}/",
    f"{WORKING_REL}/",
    "SessionContext/Consistency/",
)
VIDEO_ASPECTS = {"9:16", "16:9", "1:1"}
VIDEO_IMAGE_REFRAME_SOURCE_RATIO_TOLERANCE = 0.04
VIDEO_OUTPUT_ASPECT_RATIO_TOLERANCE = 0.04
OPENROUTER_SEEDANCE_MODEL = "bytedance/seedance-2.0-fast"
SEEDANCE_VIDEO_PROVIDER_IDS = {"bytedance", "seedance", "volcengine", "doubao", "ark"}
OPENROUTER_SR2_MODEL = "bytedance/seedance-2.0"
WAN_R2V_MODEL = "wan2.7-r2v"
WAN_R2V_MODELS = {WAN_R2V_MODEL, "wan2.7-r2v-2026-06-12"}
HAPPYHORSE_R2V_MODEL = "happyhorse-1.0-r2v"
WAN_R2V_REFERENCE_TOTAL_LIMIT = 5


def _text(value: Any, default: str = "") -> str:
    if value is None or value == "":
        value = default
    return str(value or "").strip()


def _alias_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", _text(value).lower())


def xai_video_model_requires_image(model: str) -> bool:
    return _text(model).lower().startswith("grok-imagine-video-1.5")


def xai_video_resolution(config: dict[str, Any], model: str) -> str:
    requested = _text(config.get("resolution") or config.get("default_resolution")).lower()
    if requested in {"480p", "720p"}:
        return requested
    if requested == "1080p" and xai_video_model_requires_image(model):
        return requested
    return "1080p" if xai_video_model_requires_image(model) else "720p"


def xai_usage_cost_micros(usage: dict[str, Any]) -> int | None:
    try:
        ticks = int(usage.get("cost_in_usd_ticks"))
    except (TypeError, ValueError):
        return None
    return round(ticks * 1_000_000 / 10_000_000_000) if ticks >= 0 else None


SERVICE_EXPORTS = (
    "video_provider_seconds",
    "parse_extra_json",
    "bool_config",
    "normalize_video_aspect",
    "video_size_for_aspect",
    "video_resolution_for_aspect",
    "video_provider_api_key",
    "video_config_extra_json",
    "openrouter_seedance_model",
    "route_seedance_video_provider",
    "load_active_video_config",
    "load_video_config",
    "load_video_config_for_generation",
    "first_video_url",
    "operation_status",
    "operation_done",
    "operation_failed",
    "video_task_id",
    "video_proxy_tunnel_error",
    "video_provider_urlopen",
    "get_json_request",
    "post_video_json_request",
    "download_video_binary",
    "image_inline_payload",
    "analysis_v1_video_module",
    "analysis_v1_openrouter_video_module",
    "analysis_v1_wan_rtv_video_module",
    "analysis_v1_happyhorse_video_module",
    "run_openrouter_asset_video",
    "run_wan_rtv_asset_video",
    "run_chanjing_happyhorse_asset_video",
    "openrouter_video_error_detail",
    "normalized_image_reference",
    "image_size_for_video_reframe",
    "image_dimensions",
    "video_aspect_ratio_value",
    "image_reference_matches_video_aspect",
    "video_reframe_orientation_label",
    "video_reference_reframe_prompt",
    "video_portrait_reframe_asset_payload",
    "prepare_xai_image_to_video_reference",
    "dashscope_upload_file",
    "normalize_video_reference_role",
    "infer_video_reference_role",
    "validate_video_reference_images",
    "infer_video_reference_kind",
    "reference_values_for_kind",
    "unique_reference_value_count",
    "validate_video_reference_media",
    "video_reference_prompt_prefix",
    "effective_video_prompt",
    "run_asset_library_video_provider",
    "uploaded_video_asset_payload",
    "generate_asset_library_video",
    "video_interaction_repository",
    "video_interaction_actor_id",
    "video_interaction_current_thread",
    "video_interaction_thread",
    "delete_video_interaction_cloud_context",
    "recover_gemini_omni_pending_turns",
    "retry_gemini_omni_cloud_deletions",
    "start_gemini_omni_recovery_worker",
)


def video_provider_seconds(provider: str, model: str, duration: float | int | str | None) -> int:
    try:
        requested = int(round(float(duration if duration is not None else 4)))
    except (TypeError, ValueError):
        requested = 4
    provider_id = _text(provider).lower()
    model_id = _text(model).lower()
    if provider_id == "gemini" and model_id == GEMINI_OMNI_MODEL:
        return 3
    if provider_id == "gemini":
        return 4 if requested <= 4 else 8
    if provider_id == "openai":
        return max(4, min(requested, 20))
    if provider_id == "xai":
        return max(1, min(requested, 15))
    if provider_id == "wan":
        max_seconds = 15 if "happyhorse" in model_id else 30
        return max(3, min(requested, max_seconds))
    if provider_id in {"chanjing", "chanjing.cc", "cj"}:
        for allowed in (5, 6, 10):
            if requested <= allowed:
                return allowed
        return 10
    if provider_id == "openrouter" or provider_id in {"bytedance", "seedance", "volcengine", "ark"} or model_id.startswith("bytedance/seedance"):
        return max(4, min(requested, 15))
    return max(1, min(requested, 30))


def parse_extra_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def bool_config(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    lowered = _text(value).lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    return default


def normalize_video_aspect(value: Any) -> str:
    aspect = _text(value, "9:16")
    return aspect if aspect in VIDEO_ASPECTS else "9:16"


def video_size_for_aspect(aspect: str) -> str:
    aspect = normalize_video_aspect(aspect)
    if aspect == "16:9":
        return "1280x720"
    if aspect == "1:1":
        return "1024x1024"
    return "720x1280"


def video_resolution_for_aspect(aspect: str) -> tuple[int, int]:
    size = video_size_for_aspect(aspect)
    width, height = size.split("x", 1)
    return int(width), int(height)


def dashscope_video_resolution(config: dict[str, Any]) -> str:
    value = _text(config.get("resolution") or config.get("default_resolution"), "720P")
    normalized = value.upper()
    if normalized in {"720", "1080"}:
        return f"{normalized}P"
    return normalized or "720P"


def dashscope_video_parameters(config: dict[str, Any], seconds: int, aspect: str) -> dict[str, Any]:
    watermark_value = config.get("watermark")
    if watermark_value is None:
        watermark_value = config.get("watermark_enabled")
    return {
        "duration": seconds,
        "ratio": normalize_video_aspect(aspect),
        "resolution": dashscope_video_resolution(config),
        "prompt_extend": bool_config(config.get("prompt_extend"), False),
        "watermark": bool_config(watermark_value, False),
    }


def video_aspect_for_dimensions(width: int, height: int) -> str:
    if width <= 0 or height <= 0:
        return "9:16"
    ratio = width / height
    if abs(ratio - 1.0) <= VIDEO_OUTPUT_ASPECT_RATIO_TOLERANCE:
        return "1:1"
    return "16:9" if width > height else "9:16"


def prompt_video_aspects(prompt: str) -> set[str]:
    markers: set[str] = set()
    for match in re.findall(r"(?<!\d)(9\s*:\s*16|16\s*:\s*9|1\s*:\s*1)(?!\d)", _text(prompt)):
        markers.add(re.sub(r"\s+", "", match))
    return markers & VIDEO_ASPECTS


def assert_prompt_aspect_matches(prompt: str, aspect: str) -> None:
    aspect = normalize_video_aspect(aspect)
    markers = prompt_video_aspects(prompt)
    conflicts = sorted(marker for marker in markers if marker != aspect)
    if conflicts:
        raise HTTPException(status_code=400, detail={
            "message": "Video prompt aspect conflicts with the first-frame image aspect.",
            "target_aspect": aspect,
            "prompt_aspects": sorted(markers),
            "conflicting_prompt_aspects": conflicts,
        })


def video_aspect_for_reference_image(path: Path | None, *, sc: Any) -> tuple[str, dict[str, Any]]:
    if not path:
        return "", {}
    width, height = sc.image_dimensions(path)
    aspect = video_aspect_for_dimensions(width, height)
    return aspect, {"first_frame_width": width, "first_frame_height": height, "first_frame_aspect": aspect}


def resolve_reference_frame_video_aspect(reference_paths: list[Path], requested_aspect: Any, prompt: str, *, sc: Any) -> tuple[str, dict[str, Any]]:
    requested = normalize_video_aspect(requested_aspect)
    if not reference_paths:
        assert_prompt_aspect_matches(prompt, requested)
        return requested, {"aspect_source": "request"}
    target_aspect, meta = video_aspect_for_reference_image(reference_paths[0], sc=sc)
    if not target_aspect:
        assert_prompt_aspect_matches(prompt, requested)
        return requested, {"aspect_source": "request"}
    requested_raw = _text(requested_aspect)
    if requested_raw and requested != target_aspect:
        raise HTTPException(status_code=400, detail={
            "message": "Requested video aspect conflicts with the first-frame image aspect.",
            "requested_aspect": requested,
            "first_frame_aspect": target_aspect,
            **meta,
        })
    assert_prompt_aspect_matches(prompt, target_aspect)
    return target_aspect, {**meta, "aspect_source": "first_frame_image"}


def video_provider_api_key(provider: str, mapping: Any, *, sc: Any) -> str:
    provider_id = _text(provider)
    try:
        key = _text(load_stored_key(sc.ctx, "video", provider_id))
    except Exception:
        key = ""
    if key:
        return key
    api_key_ref = _text(mapping.get("api_key_ref") if hasattr(mapping, "get") else "", f"video_{provider_id}_key")
    if api_key_ref:
        try:
            key = _text(sc.ctx.secret_store.get(api_key_ref))
        except Exception:
            key = ""
    return key or _text(mapping.get("api_key_ciphertext") if hasattr(mapping, "get") else "")


def video_config_extra_json(mapping: Any) -> dict[str, Any]:
    raw = mapping.get("extra_json") if hasattr(mapping, "get") else None
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(str(raw or ""))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def openrouter_seedance_model(model: str) -> str:
    model_id = _text(model)
    lowered = model_id.lower()
    if lowered.startswith("bytedance/seedance"):
        return model_id
    if "1-5" in lowered or "1.5" in lowered:
        return "bytedance/seedance-1-5-pro"
    return OPENROUTER_SEEDANCE_MODEL


def route_seedance_video_provider(provider: str, model: str) -> tuple[str, str]:
    provider_id = _text(provider)
    model_id = _text(model)
    lowered_provider = provider_id.lower()
    lowered_model = model_id.lower()
    if lowered_provider in SEEDANCE_VIDEO_PROVIDER_IDS or (not lowered_provider and "seedance" in lowered_model):
        return "openrouter", openrouter_seedance_model(model_id)
    if lowered_provider in {"chanjing", "chanjing.cc", "cj"} and lowered_model.startswith("happyhorse-1.0"):
        return "wan", model_id
    return provider_id, model_id


def load_active_video_config(*, sc: Any) -> dict[str, Any]:
    ensure_table(sc.ctx)
    with sc.ctx.engine.begin() as conn:
        row = conn.execute(sql_text(f"""
SELECT provider, model, api_key_ref, api_key_ciphertext, extra_json FROM {CONFIG_TABLE}
WHERE kind = 'video' AND enabled = TRUE
ORDER BY active DESC, updated_at DESC
LIMIT 1
""")).first()
    if not row:
        raise HTTPException(status_code=400, detail="No enabled video model is configured in Connection")
    mapping = row._mapping
    provider = _text(mapping.get("provider"))
    model = _text(mapping.get("model"))
    routed_provider, routed_model = route_seedance_video_provider(provider, model)
    if routed_provider != provider:
        return sc.load_video_config(routed_provider, routed_model, sc=sc)
    api_key = video_provider_api_key(provider, mapping, sc=sc)
    if not api_key:
        raise HTTPException(status_code=400, detail=f"Video provider API key is missing in Connection: {provider}/{model}")
    return {**video_config_extra_json(mapping), "provider": provider, "model": model, "api_key": api_key}


def load_video_config(provider: str, model: str, *, sc: Any) -> dict[str, Any]:
    selected_provider = _text(provider)
    selected_model = _text(model)
    selected_provider, selected_model = route_seedance_video_provider(selected_provider, selected_model)
    if not selected_provider:
        config = load_active_video_config(sc=sc)
        if selected_model:
            config["model"] = selected_model
        return config
    ensure_table(sc.ctx)
    with sc.ctx.engine.begin() as conn:
        row = conn.execute(sql_text(f"""
SELECT provider, model, api_key_ref, api_key_ciphertext, extra_json FROM {CONFIG_TABLE}
WHERE kind = 'video' AND provider = :provider AND enabled = TRUE
LIMIT 1
"""), {"provider": selected_provider}).first()
    if not row:
        raise HTTPException(status_code=400, detail=f"Video provider is not configured or enabled: {selected_provider}")
    mapping = row._mapping
    provider_id = _text(mapping.get("provider"), selected_provider)
    stored_model = _text(mapping.get("model"))
    selected_model = selected_model or stored_model
    api_key = video_provider_api_key(provider_id, mapping, sc=sc)
    if not api_key:
        raise HTTPException(status_code=400, detail=f"Video provider API key is missing in Connection: {provider_id}/{selected_model}")
    return {**video_config_extra_json(mapping), "provider": provider_id, "model": selected_model, "api_key": api_key}


def agent_video_alias_from_payload(payload: dict[str, Any]) -> str:
    return _text(
        payload.get("agentVideoAlias")
        or payload.get("agent_video_alias")
        or payload.get("agent_model_alias")
        or payload.get("model_alias")
        or payload.get("alias")
    )


def resolve_agent_video_alias(alias: str, *, strict: bool = True, sc: Any) -> tuple[str, str]:
    alias_value = _text(alias)
    if not alias_value:
        return "", ""
    for item in load_agent_model_aliases(sc.ctx, "video"):
        if _text(item.get("alias")) == alias_value:
            provider = _text(item.get("provider"))
            model = _text(item.get("model"))
            if provider and model:
                return provider, model
    public_provider, public_model = customer_media_public_alias_target(load_config(sc.ctx, "video"), "video", alias_value)
    if public_provider and public_model:
        return public_provider, public_model
    if strict:
        raise HTTPException(status_code=400, detail="Select a valid Agent video model before generating.")
    return "", ""


def load_video_config_for_generation(task: dict[str, Any], payload: dict[str, Any], *, sc: Any) -> dict[str, Any]:
    provider = _text(payload.get("provider"))
    model = _text(payload.get("model"))
    request_alias = agent_video_alias_from_payload(payload)
    settings_alias = request_alias
    is_agent_request = bool(
        _text(payload.get("agent_generation_id"))
        or _text(payload.get("agent_message_id"))
        or _text(payload.get("chat_opencode_session_id"))
        or _text(payload.get("chat_session_id"))
    )
    if is_agent_request:
        settings_provider = ""
        settings_model = ""
        settings_reader = getattr(sc, "read_or_create_videos_agent_settings", None)
        if callable(settings_reader):
            try:
                settings_payload = settings_reader(task)
                settings = settings_payload.get("settings") if isinstance(settings_payload, dict) else {}
                if isinstance(settings, dict):
                    settings_provider = _text(settings.get("provider"))
                    settings_model = _text(settings.get("model"))
                    settings_alias = settings_alias or _text(settings.get("agentVideoAlias") or settings.get("agent_video_alias"))
            except Exception:
                pass
        if not settings_provider or not settings_model:
            try:
                settings_path = sc.workspace_for(task) / "SessionContext/VideosAgentSettings.json"
                settings_payload = json.loads(settings_path.read_text()) if settings_path.exists() else {}
                settings = settings_payload.get("settings") if isinstance(settings_payload, dict) else {}
                if isinstance(settings, dict):
                    settings_provider = settings_provider or _text(settings.get("provider"))
                    settings_model = settings_model or _text(settings.get("model"))
                    settings_alias = settings_alias or _text(settings.get("agentVideoAlias") or settings.get("agent_video_alias"))
            except Exception:
                pass
        provider = settings_provider or provider
        model = settings_model or model
    if settings_alias:
        alias_provider, alias_model = resolve_agent_video_alias(settings_alias, strict=bool(request_alias), sc=sc)
        if alias_provider and alias_model:
            provider = alias_provider
            model = alias_model
        else:
            provider = ""
            model = ""
    config = load_video_config(provider, model, sc=sc)
    if settings_alias and alias_provider and alias_model:
        config["agent_video_alias"] = settings_alias
    return config


def video_interaction_repository(*, sc: Any) -> VideoInteractionRepository:
    repository = getattr(sc, "_video_interaction_repository", None)
    if not isinstance(repository, VideoInteractionRepository):
        repository = VideoInteractionRepository(sc.ctx.engine)
        setattr(sc, "_video_interaction_repository", repository)
    return repository


def video_interaction_actor_id(task: dict[str, Any]) -> str:
    # Koubo tasks are already resolved within a session-owned workspace. Keep
    # that stable local owner scope instead of accepting an actor from clients.
    return f"session:{int(task['session_id'])}"


def video_interaction_current_thread(task: dict[str, Any], chat_session_id: str = "", *, sc: Any) -> dict[str, Any]:
    repository = video_interaction_repository(sc=sc)
    return repository.current_thread(
        task_id=int(task["id"]),
        actor_id=video_interaction_actor_id(task),
        chat_session_id=_text(chat_session_id),
    ) or {"video_thread_id": "", "head_turn_id": None, "status": "empty", "turns": []}


def video_interaction_thread(task: dict[str, Any], thread_id: str, *, sc: Any) -> dict[str, Any]:
    try:
        return video_interaction_repository(sc=sc).list_thread(
            task_id=int(task["id"]),
            actor_id=video_interaction_actor_id(task),
            thread_id=thread_id,
        )
    except VideoInteractionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.as_detail()) from exc


def delete_video_interaction_cloud_context(task: dict[str, Any], thread_id: str, *, sc: Any) -> dict[str, Any]:
    repository = video_interaction_repository(sc=sc)
    actor_id = video_interaction_actor_id(task)
    try:
        thread = repository.internal_thread(task_id=int(task["id"]), actor_id=actor_id, thread_id=thread_id)
    except VideoInteractionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.as_detail()) from exc
    if _text(thread.get("internal_provider")) != "gemini" or _text(thread.get("internal_model")) != GEMINI_OMNI_MODEL:
        raise HTTPException(status_code=400, detail={"code": "gemini_omni_previous_interaction_invalid", "message": "This thread has no Gemini Omni cloud context"})
    config = load_video_config("gemini", GEMINI_OMNI_MODEL, sc=sc)
    client = GeminiOmniClient(_text(config.get("api_key")))
    pending = repository.begin_cloud_delete(task_id=int(task["id"]), actor_id=actor_id, thread_id=thread_id)
    failures = 0
    for turn_id, interaction_id in pending:
        try:
            client.delete_interaction(interaction_id)
        except GeminiOmniError as exc:
            failures += 1
            repository.finish_cloud_delete(turn_id, error=exc.code)
        else:
            repository.finish_cloud_delete(turn_id)
    state = repository.list_thread(task_id=int(task["id"]), actor_id=actor_id, thread_id=thread_id)
    return {
        "ok": failures == 0,
        "video_thread_id": state["video_thread_id"],
        "status": state["status"],
        "deleted_count": len(pending) - failures,
        "failed_count": failures,
        "turns": state["turns"],
    }


def _recovery_workspace_path(workspace: Path, relative_path: str) -> Path:
    candidate = (workspace / _text(relative_path)).resolve()
    root = workspace.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise GeminiOmniError("gemini_omni_file_processing_failed", "Saved recovery path is outside the task workspace", status_code=400) from exc
    return candidate


def _recover_gemini_omni_turn(turn_id: str, *, sc: Any) -> bool:
    repository = video_interaction_repository(sc=sc)
    claim = repository.claim_recovery(turn_id)
    if not claim:
        return False
    turn = claim.turn
    task = sc.task_or_404(int(turn["task_id"]))
    actor_id = video_interaction_actor_id(task)
    repository.internal_thread(task_id=int(task["id"]), actor_id=actor_id, thread_id=turn["thread_id"])
    request_config = turn.get("request_config_json") if isinstance(turn.get("request_config_json"), dict) else {}
    workspace = sc.workspace_for(task)
    output_rel = _text(turn.get("output_path"))
    if not output_rel.startswith(f"{ASSET_VIDEOS_REL}/"):
        repository.fail_turn(turn_id, lease_token=claim.lease_token)
        return False
    output_path = _recovery_workspace_path(workspace, output_rel)
    config = load_video_config("gemini", GEMINI_OMNI_MODEL, sc=sc)
    client = GeminiOmniClient(_text(config.get("api_key")))
    prompt = _text(request_config.get("effective_prompt") or turn.get("prompt"))
    aspect = normalize_video_aspect(request_config.get("aspect"))
    if aspect not in {"16:9", "9:16"}:
        aspect = "9:16"
    requested_duration = video_provider_seconds(
        "gemini",
        GEMINI_OMNI_MODEL,
        request_config.get("duration") or request_config.get("duration_seconds"),
    )

    def persist_interaction(interaction_id: str, expires_at: int | None, expiry_source: str) -> None:
        repository.mark_provider_request_sent(
            turn_id,
            interaction_id=interaction_id,
            provider_state_expires_at=expires_at,
            provider_expiry_source=expiry_source,
        )

    def renew() -> None:
        if not repository.renew_lease(turn_id, claim.lease_token):
            raise GeminiOmniError("video_stateful_edit_in_progress", "Recovery lease was lost", status_code=409)

    try:
        interaction_id = _text(turn.get("interaction_id"))
        if interaction_id:
            current = client.get_interaction(interaction_id)
            if _text(current.get("status")).lower() in {"completed", "succeeded", "success"}:
                completed = current
            else:
                completed = client.poll_interaction(interaction_id, lease_callback=renew)
        else:
            provider_inputs: list[dict[str, str]] = []
            image_rels = request_config.get("reference_images") if isinstance(request_config.get("reference_images"), list) else []
            video_rels = request_config.get("reference_videos") if isinstance(request_config.get("reference_videos"), list) else []
            for relative_path, media_type in [
                *((value, "image") for value in image_rels),
                *((value, "video") for value in video_rels),
            ]:
                source_path = _recovery_workspace_path(workspace, _text(relative_path))
                if not source_path.is_file():
                    raise GeminiOmniError("gemini_omni_file_processing_failed", "A saved recovery input is unavailable", status_code=409)
                provider_inputs.append(gemini_omni_file_input(client.upload_file(source_path), media_type=media_type))
            previous_interaction_id = ""
            if turn.get("parent_turn_id"):
                parent = repository.get_turn(task_id=int(task["id"]), actor_id=actor_id, turn_id=turn["parent_turn_id"])
                previous_interaction_id = _text(parent.get("interaction_id"))
                if not previous_interaction_id:
                    raise GeminiOmniError("gemini_omni_interaction_expired", "Parent provider context is unavailable", status_code=409)
                client.get_interaction(previous_interaction_id)
            request_payload = build_interaction_request(
                prompt=prompt,
                task=omni_task_for(turn["operation"], image_count=len(image_rels), video_count=len(video_rels)),
                aspect_ratio=aspect,
                delivery="uri",
                store=True,
                background=True,
                previous_interaction_id=previous_interaction_id,
                file_inputs=provider_inputs,
                duration_seconds=requested_duration,
            )
            completed = client.run_interaction(
                request_payload,
                interaction_callback=persist_interaction,
                lease_callback=renew,
            )
        output_meta = materialize_gemini_omni_video_output(
            completed,
            output_path,
            api_key=_text(config.get("api_key")),
            download_video_binary=sc.download_video_binary,
            sanitize_video_output=sanitize_video_output,
        )
        usage = record_storyboard_usage(
            sc.ctx,
            task,
            request_id=_text(turn.get("usage_request_id")),
            provider="gemini",
            model_id=GEMINI_OMNI_MODEL,
            modality="video",
            step_id="koubo_storyboard.asset_library_agent.video",
            units=video_usage_units(
                seconds=output_meta.get("duration_seconds") or requested_duration,
                prompt=prompt,
                reference_count=len(request_config.get("reference_images") or []) + len(request_config.get("reference_videos") or []),
                resolution="720p",
            ),
            estimated_cost_micros=round(
                float(output_meta.get("duration_seconds") or requested_duration)
                * GEMINI_OMNI_720P_USD_PER_SECOND
                * 1_000_000
            ),
        )
        asset = sc.uploaded_video_asset_payload(output_rel, "agent_generated", "Recovered Gemini Omni video", {
            "duration": output_meta.get("duration_seconds"),
            "duration_seconds": output_meta.get("duration_seconds"),
            "aspect": aspect,
            "width": output_meta.get("width"),
            "height": output_meta.get("height"),
            "operation": turn.get("operation"),
            "video_thread_id": turn.get("thread_id"),
            "video_turn_id": turn.get("turn_id"),
            "parent_turn_id": turn.get("parent_turn_id"),
            "source_asset_id": turn.get("input_asset_id"),
            "stateful": True,
            "origin": {
                "tool": "upload_asset_library_video_agent",
                "operation": turn.get("operation"),
                "video_thread_id": turn.get("thread_id"),
                "video_turn_id": turn.get("turn_id"),
                "parent_turn_id": turn.get("parent_turn_id"),
                "source_asset_id": turn.get("input_asset_id"),
                "stateful": True,
                "recovered": True,
            },
        })
        sc.upsert_asset_manifest_item(workspace, asset, sc=sc)
        sidecar_path = output_path.with_suffix(".json")
        sc.write_json(sidecar_path, {
            "operation": turn.get("operation"),
            "video_thread_id": turn.get("thread_id"),
            "video_turn_id": turn.get("turn_id"),
            "parent_turn_id": turn.get("parent_turn_id"),
            "stateful": True,
            "recovered": True,
            "generated_at": now_ms(),
        })
        repository.complete_turn(
            turn_id,
            lease_token=claim.lease_token,
            output_asset_id=_text(asset.get("id")),
            output_path=output_rel,
            local_usage_id=_text(usage.get("local_usage_id")),
        )
        return True
    except GeminiOmniError as exc:
        current_turn = repository.get_turn(task_id=int(task["id"]), actor_id=actor_id, turn_id=turn_id)
        if exc.code == "gemini_omni_interaction_expired":
            repository.mark_provider_expired(turn_id)
            repository.fail_turn(turn_id, lease_token=claim.lease_token)
        elif current_turn.get("interaction_id"):
            repository.release_lease(turn_id, lease_token=claim.lease_token)
        elif exc.status_code >= 500:
            repository.mark_provider_result_unknown(turn_id)
        else:
            repository.fail_turn(turn_id, lease_token=claim.lease_token)
        output_path.unlink(missing_ok=True)
        return False
    except Exception:
        repository.fail_turn(turn_id, lease_token=claim.lease_token)
        output_path.unlink(missing_ok=True)
        return False


def recover_gemini_omni_pending_turns(*, sc: Any, limit: int = 100) -> dict[str, int]:
    repository = video_interaction_repository(sc=sc)
    turn_ids = repository.recoverable_turn_ids(limit=limit)
    recovered = sum(1 for turn_id in turn_ids if _recover_gemini_omni_turn(turn_id, sc=sc))
    return {"scanned": len(turn_ids), "recovered": recovered, "remaining": len(turn_ids) - recovered}


def retry_gemini_omni_cloud_deletions(*, sc: Any, limit: int = 100) -> dict[str, int]:
    repository = video_interaction_repository(sc=sc)
    rows = repository.cloud_deletion_rows(limit=limit)
    deleted = 0
    for turn in rows:
        try:
            config = load_video_config("gemini", GEMINI_OMNI_MODEL, sc=sc)
            GeminiOmniClient(_text(config.get("api_key"))).delete_interaction(_text(turn.get("interaction_id")))
        except Exception as exc:
            repository.finish_cloud_delete(_text(turn.get("turn_id")), error=getattr(exc, "code", "cloud_delete_retry_failed"))
        else:
            repository.finish_cloud_delete(_text(turn.get("turn_id")))
            deleted += 1
    return {"scanned": len(rows), "deleted": deleted, "remaining": len(rows) - deleted}


def start_gemini_omni_recovery_worker(*, sc: Any) -> bool:
    if getattr(sc.ctx, "engine", None) is None:
        return False
    enabled = str(os.environ.get("OPENCREW_GEMINI_OMNI_ENABLED") or "").strip().lower() in {"1", "true", "yes", "on"}
    has_deletions = bool(video_interaction_repository(sc=sc).cloud_deletion_rows(limit=1))
    if not enabled and not has_deletions:
        return False
    if getattr(sc.ctx, "gemini_omni_recovery_started", False):
        return False
    setattr(sc.ctx, "gemini_omni_recovery_started", True)

    def worker(worker_sc: Any) -> None:
        try:
            retry_gemini_omni_cloud_deletions(sc=worker_sc)
            if enabled:
                recover_gemini_omni_pending_turns(sc=worker_sc)
        except Exception:
            # Startup recovery is best-effort; durable pending rows remain for
            # the next scan and no paid request is blindly recreated.
            return

    threading.Thread(
        target=worker,
        args=(sc,),
        name="opencrew-gemini-omni-recovery",
        daemon=True,
    ).start()
    return True


def first_video_url(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("url", "video_url", "audio_url", "download_url", "uri", "outputUrl", "output_url"):
            value = payload.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value
        for key in ("output", "response", "data"):
            found = first_video_url(payload.get(key))
            if found:
                return found
        for value in payload.values():
            found = first_video_url(value)
            if found:
                return found
    if isinstance(payload, list):
        for value in payload:
            found = first_video_url(value)
            if found:
                return found
    return ""


def operation_status(payload: dict[str, Any]) -> str:
    output = payload.get("output") if isinstance(payload.get("output"), dict) else {}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    return _text(payload.get("status") or payload.get("task_status") or payload.get("state") or output.get("status") or data.get("status")).lower()


def operation_done(payload: dict[str, Any]) -> bool:
    status = operation_status(payload)
    return bool(payload.get("done")) or status in {"succeeded", "success", "completed", "done", "finish", "finished"}


def operation_failed(payload: dict[str, Any]) -> str:
    status = operation_status(payload)
    if status in {"failed", "failure", "error", "canceled", "cancelled", "rejected"}:
        return json.dumps(payload, ensure_ascii=False)[:1200]
    error = payload.get("error")
    return json.dumps(error, ensure_ascii=False)[:1200] if error else ""


def video_task_id(payload: dict[str, Any]) -> str:
    for container in (payload, payload.get("output"), payload.get("data")):
        if not isinstance(container, dict):
            continue
        for key in ("id", "task_id", "taskId"):
            value = _text(container.get(key))
            if value:
                return value
    return ""


def video_proxy_tunnel_error(exc: BaseException) -> bool:
    reason = getattr(exc, "reason", exc)
    message = str(reason or exc).lower()
    return (
        "tunnel connection failed" in message
        or "connection refused" in message
        or ("proxy" in message and ("403" in message or "forbidden" in message or "connection refused" in message))
        or "broken pipe" in message
        or "127.0.0.1:7890" in message
        or "127.0.0.1:61988" in message
        or "localhost:7890" in message
        or "localhost:61988" in message
    )


def video_provider_urlopen(req: urllib.request.Request, provider: str, timeout: int = 120) -> Any:
    # Match Analysis_V1 video tools: direct urllib call, leaving TUN/system proxy
    # handling to the process environment instead of forcing a local HTTP proxy.
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except (urllib.error.URLError, BrokenPipeError, ConnectionError, OSError) as exc:
        # Match Video_Grok.py: xAI first uses the normal environment opener, then
        # falls back to a no-proxy opener only for proxy tunnel failures.
        if _text(provider).lower() not in {"xai", "grok", "bytedance", "seedance", "volcengine", "ark"} or not video_proxy_tunnel_error(exc):
            raise
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        return opener.open(req, timeout=timeout)


def _json_from_detail(detail: str) -> dict[str, Any]:
    try:
        parsed = json.loads(str(detail or ""))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _video_retry_after_seconds(exc: urllib.error.HTTPError | None, detail: str, attempt: int) -> float:
    values: list[float] = []
    if exc is not None:
        retry_after = ""
        try:
            retry_after = str(exc.headers.get("Retry-After") or "")
        except Exception:
            retry_after = ""
        if retry_after:
            try:
                values.append(float(retry_after))
            except ValueError:
                pass
    payload = _json_from_detail(detail)
    try:
        if payload.get("retry_after") is not None:
            values.append(float(payload.get("retry_after")))
    except (TypeError, ValueError):
        pass
    if values:
        return max(0.0, min(min(values), 60.0))
    return min(30.0, float(10 * attempt))


def _retryable_video_http_error(exc: urllib.error.HTTPError, detail: str) -> bool:
    if int(getattr(exc, "code", 0) or 0) in {429, 500, 502, 503, 504, 520, 522, 523, 524}:
        return True
    payload = _json_from_detail(detail)
    error_payload = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    status = _text(payload.get("status") or error_payload.get("status")).upper()
    return payload.get("retryable") is True or payload.get("cloudflare_error") is True or status in {"UNAVAILABLE", "RESOURCE_EXHAUSTED", "INTERNAL"}


def _retryable_video_url_error(exc: urllib.error.URLError) -> bool:
    reason = getattr(exc, "reason", exc)
    lowered = str(reason or "").lower()
    return isinstance(reason, TimeoutError) or "timed out" in lowered or "temporarily unavailable" in lowered or "connection reset" in lowered


def _video_json_request_with_retries(request_factory: Any, provider: str, timeout: int, attempts: int = 2) -> dict[str, Any]:
    for attempt in range(1, max(1, attempts) + 1):
        try:
            with video_provider_urlopen(request_factory(), provider, timeout=timeout) as res:
                body = res.read().decode("utf-8", errors="replace")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:2000]
            if attempt < attempts and _retryable_video_http_error(exc, detail):
                time.sleep(_video_retry_after_seconds(exc, detail, attempt))
                continue
            suffix = f" (after {attempt} attempts)" if attempt > 1 else ""
            raise HTTPException(status_code=502, detail=f"Video provider request failed: HTTP {exc.code}: {detail}{suffix}") from exc
        except urllib.error.URLError as exc:
            if attempt < attempts and _retryable_video_url_error(exc):
                time.sleep(_video_retry_after_seconds(None, "", attempt))
                continue
            suffix = f" (after {attempt} attempts)" if attempt > 1 else ""
            raise HTTPException(status_code=502, detail=f"Video provider request failed: {exc.reason}{suffix}") from exc
        except (ssl.SSLError, http.client.IncompleteRead, ConnectionError, TimeoutError) as exc:
            if attempt < attempts:
                time.sleep(_video_retry_after_seconds(None, "", attempt))
                continue
            suffix = f" (after {attempt} attempts)" if attempt > 1 else ""
            raise HTTPException(status_code=502, detail=f"Video provider request failed: {exc}{suffix}") from exc
    raise HTTPException(status_code=502, detail="Video provider request failed")


def get_json_request(url: str, headers: dict[str, str], timeout: int = 120, provider: str = "") -> dict[str, Any]:
    def request_factory() -> urllib.request.Request:
        return urllib.request.Request(url, headers={"Accept": "application/json", **headers})

    return _video_json_request_with_retries(request_factory, provider, timeout)


def post_video_json_request(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int = 120, provider: str = "") -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def request_factory() -> urllib.request.Request:
        return urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", "Accept": "application/json", **headers}, method="POST")

    return _video_json_request_with_retries(request_factory, provider, timeout)


def download_video_binary(url: str, output_path: Path, headers: dict[str, str] | None = None, timeout: int = 600, provider: str = "") -> None:
    last_error: Exception | None = None
    for attempt in range(1, 4):
        req = urllib.request.Request(url, headers=headers or {})
        try:
            with video_provider_urlopen(req, provider, timeout=timeout) as res:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                with output_path.open("wb") as handle:
                    shutil.copyfileobj(res, handle)
            sanitize_video_output(output_path)
            return
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:2000]
            raise HTTPException(status_code=502, detail=f"Video download failed: HTTP {exc.code}: {detail}") from exc
        except MediaSanitizeError as exc:
            output_path.unlink(missing_ok=True)
            raise HTTPException(status_code=502, detail=f"Video metadata sanitization failed: {exc}") from exc
        except (urllib.error.URLError, ssl.SSLError, http.client.IncompleteRead, ConnectionError, TimeoutError) as exc:
            last_error = exc
            output_path.unlink(missing_ok=True)
            if attempt < 3:
                time.sleep(2 * attempt)
                continue
            reason = getattr(exc, "reason", None) or str(exc)
            raise HTTPException(status_code=502, detail=f"Video download failed after {attempt} attempts: {reason}") from exc
    if last_error:
        raise HTTPException(status_code=502, detail=f"Video download failed: {last_error}")


def sanitize_video_output(output_path: Path) -> None:
    sanitize_video_file_metadata(output_path)


def image_inline_payload(path: Path | None) -> dict[str, str] | None:
    if not path:
        return None
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return {"mimeType": mime, "bytesBase64Encoded": base64.b64encode(path.read_bytes()).decode("ascii")}


def analysis_v1_video_module(module_name: str) -> Any:
    attr = f"_module_{module_name}"
    cached = getattr(analysis_v1_video_module, attr, None)
    if cached is not None:
        return cached
    module_path = Path(__file__).resolve().parents[4] / "ToolLibrary" / "Analysis_V1" / "video_plan_executor_modules" / "video_openrouter.py"
    module_path = module_path.with_name(f"{module_name}.py")
    spec = importlib.util.spec_from_file_location(f"opencrew_analysis_v1_{module_name}_asset_library", module_path)
    if spec is None or spec.loader is None:
        raise HTTPException(status_code=500, detail=f"Analysis_V1 video module could not be loaded: {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    setattr(analysis_v1_video_module, attr, module)
    return module


def analysis_v1_openrouter_video_module() -> Any:
    return analysis_v1_video_module("video_openrouter")


def analysis_v1_wan_rtv_video_module() -> Any:
    return analysis_v1_video_module("video_wan_rtv")


def analysis_v1_happyhorse_video_module() -> Any:
    return analysis_v1_video_module("video_chanjing_happyhorse")


def run_openrouter_asset_video(
    prompt: str,
    config: dict[str, Any],
    output_path: Path,
    reference_paths: list[Path],
    reference_audio_paths: list[Path],
    reference_video_paths: list[Path],
    seconds: int,
    aspect: str, *, sc: Any,
) -> dict[str, Any]:
    module = analysis_v1_openrouter_video_module()
    prompt_path = output_path.with_name(f"{output_path.stem}_openrouter_prompt.json")
    state_path = output_path.with_name(f"{output_path.stem}_openrouter_task_state.json")
    sc.write_json(prompt_path, {"prompt": prompt})
    openrouter_config = {
        **config,
        "provider": "openrouter",
        "model": _text(config.get("model"), OPENROUTER_SEEDANCE_MODEL),
        "aspect_ratio": aspect,
        "default_aspect_ratio": aspect,
    }
    try:
        result = module.generate(
            {
                "config": openrouter_config,
                "reference_images": [str(path) for path in reference_paths],
                "reference_audios": [str(path) for path in reference_audio_paths],
                "reference_videos": [str(path) for path in reference_video_paths],
                "duration_seconds": seconds,
                "timeout_seconds": 900,
                "provider_task_state_path": str(state_path),
            },
            prompt_path,
            output_path,
        )
    except Exception as exc:
        detail = sc.openrouter_video_error_detail(exc, reference_paths, reference_audio_paths, reference_video_paths)
        status_code = 504 if exc.__class__.__name__ == "ProviderTimeout" else 502
        if isinstance(detail, dict):
            status_code = int(detail.get("status_code") or status_code)
        raise HTTPException(status_code=status_code, detail=detail) from exc
    payload = result if isinstance(result, dict) else {}
    return {**payload, "provider_state_path": str(state_path), "prompt_path": str(prompt_path)}


def run_wan_rtv_asset_video(
    prompt: str,
    config: dict[str, Any],
    output_path: Path,
    reference_paths: list[Path],
    reference_video_paths: list[Path],
    seconds: int,
    aspect: str, *, sc: Any,
) -> dict[str, Any]:
    module = analysis_v1_wan_rtv_video_module()
    prompt_path = output_path.with_name(f"{output_path.stem}_wan_r2v_prompt.json")
    sc.write_json(prompt_path, {"prompt": prompt})
    wan_config = {
        **config,
        "provider": "wan",
        "model": _text(config.get("model"), WAN_R2V_MODEL),
        "video_size": video_size_for_aspect(aspect),
        "default_size": video_size_for_aspect(aspect),
    }
    try:
        result = module.generate(
            {
                "config": wan_config,
                "reference_images": [str(path) for path in reference_paths],
                "reference_videos": [str(path) for path in reference_video_paths],
                "duration_seconds": seconds,
                "timeout_seconds": 900,
            },
            prompt_path,
            output_path,
        )
    except Exception as exc:
        status_code = 504 if exc.__class__.__name__ == "ProviderTimeout" else 502
        raise HTTPException(status_code=status_code, detail=f"Video generation failed: {exc}") from exc
    payload = result if isinstance(result, dict) else {}
    return {**payload, "prompt_path": str(prompt_path)}


def run_chanjing_happyhorse_asset_video(
    prompt: str,
    config: dict[str, Any],
    output_path: Path,
    reference_paths: list[Path],
    seconds: int,
    aspect: str, *, sc: Any,
) -> dict[str, Any]:
    module = analysis_v1_happyhorse_video_module()
    prompt_path = output_path.with_name(f"{output_path.stem}_happyhorse_prompt.json")
    state_path = output_path.with_name(f"{output_path.stem}_happyhorse_task_state.json")
    sc.write_json(prompt_path, {"prompt": prompt})
    chanjing_config = {
        **config,
        "provider": "chanjing",
        "model": _text(config.get("model"), HAPPYHORSE_R2V_MODEL),
        "aspect_ratio": aspect,
        "default_aspect_ratio": aspect,
    }
    try:
        result = module.generate(
            {
                "config": chanjing_config,
                "reference_images": [str(path) for path in reference_paths[:3]],
                "duration_seconds": seconds,
                "timeout_seconds": 900,
                "provider_task_state_path": str(state_path),
            },
            prompt_path,
            output_path,
        )
    except Exception as exc:
        status_code = 504 if exc.__class__.__name__ == "ProviderTimeout" else 502
        raise HTTPException(status_code=status_code, detail=f"Video generation failed: {exc}") from exc
    payload = result if isinstance(result, dict) else {}
    return {**payload, "provider_state_path": str(state_path), "prompt_path": str(prompt_path)}


def openrouter_video_error_detail(exc: BaseException, reference_paths: list[Path], reference_audio_paths: list[Path] | None = None, reference_video_paths: list[Path] | None = None) -> str | dict[str, Any]:
    message = str(exc)
    lowered = message.lower()
    sensitive_reference = (
        "inputimagesensitivecontentdetected.privacyinformation" in lowered
        or "input image may contain real person" in lowered
    )
    if sensitive_reference and reference_paths:
        return {
            "message": "The selected video model rejected the selected reference image because it may contain a real person or privacy-sensitive content.",
            "provider": "",
            "provider_error_code": "InputImageSensitiveContentDetected.PrivacyInformation",
            "status_code": 400,
            "reference_image_count": len(reference_paths),
            "reference_audio_count": len(reference_audio_paths or []),
            "reference_video_count": len(reference_video_paths or []),
            "suggestion": "Remove the selected person reference image and generate text-to-video, or switch to a provider/model that accepts real-person reference images such as Gemini/Veo for this workflow.",
            "raw_provider_error": message[:1200],
        }
    return f"Video generation failed: {message}"


def normalized_image_reference(path: Path | None, output_path: Path, aspect: str) -> Path | None:
    if not path:
        return None
    try:
        from PIL import Image, ImageOps
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Pillow is required for video reference normalization") from exc
    width, height = video_resolution_for_aspect(aspect)
    target = output_path.with_name(f"{output_path.stem}_{width}x{height}_reference.jpg")
    with Image.open(path) as image:
        normalized = ImageOps.fit(image.convert("RGB"), (width, height), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
        normalized.save(target, "JPEG", quality=95)
    return target


def image_size_for_video_reframe(aspect: str) -> str:
    aspect = normalize_video_aspect(aspect)
    if aspect == "16:9":
        return "1536x1024"
    if aspect == "1:1":
        return "1024x1024"
    return "1024x1536"


def image_dimensions(path: Path | None) -> tuple[int, int]:
    if not path:
        return 0, 0
    try:
        from PIL import Image
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Pillow is required for video reference aspect checks") from exc
    try:
        with Image.open(path) as image:
            return int(image.width), int(image.height)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Unable to inspect reference image dimensions: {path.name}") from exc


def video_dimensions(path: Path | None) -> tuple[int, int]:
    if not path or not path.exists():
        return 0, 0
    command = [
        resolve_media_binary("ffprobe"),
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "json",
        str(path),
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
    except (OSError, subprocess.SubprocessError):
        return 0, 0
    if completed.returncode != 0:
        return 0, 0
    try:
        payload = json.loads(completed.stdout or "{}")
        stream = (payload.get("streams") or [{}])[0]
        return int(stream.get("width") or 0), int(stream.get("height") or 0)
    except Exception:
        return 0, 0


def validate_video_output_aspect(path: Path, aspect: str, *, provider: str, model: str, sc: Any) -> dict[str, Any]:
    width, height = video_dimensions(path)
    target_ratio = sc.video_aspect_ratio_value(aspect)
    target_aspect = normalize_video_aspect(aspect)
    if width <= 0 or height <= 0:
        return {
            "output_width": width,
            "output_height": height,
            "output_aspect": "",
            "output_aspect_matches_request": False,
            "target_aspect": target_aspect,
            "target_ratio": round(target_ratio, 4),
            "aspect_audit_status": "uninspectable",
            "aspect_audit_message": "Generated video dimensions could not be inspected.",
            "provider": provider,
            "model": model,
            "output_path": str(path),
        }
    output_ratio = width / height
    matches = abs(output_ratio - target_ratio) <= VIDEO_OUTPUT_ASPECT_RATIO_TOLERANCE
    return {
        "output_width": width,
        "output_height": height,
        "output_aspect": video_aspect_for_dimensions(width, height),
        "output_ratio": round(output_ratio, 4),
        "output_aspect_matches_request": matches,
        "target_aspect": target_aspect,
        "target_ratio": round(target_ratio, 4),
        "aspect_audit_status": "passed" if matches else "mismatch",
        "aspect_audit_message": "" if matches else "Generated video aspect does not match the required first-frame/request aspect.",
        "provider": provider,
        "model": model,
    }


def video_aspect_ratio_value(aspect: str) -> float:
    width, height = video_resolution_for_aspect(aspect)
    return width / height


def image_reference_matches_video_aspect(path: Path | None, aspect: str) -> bool:
    width, height = image_dimensions(path)
    if width <= 0 or height <= 0:
        return True
    source_ratio = width / height
    target_ratio = video_aspect_ratio_value(aspect)
    return abs(source_ratio - target_ratio) <= VIDEO_IMAGE_REFRAME_SOURCE_RATIO_TOLERANCE


def video_reframe_orientation_label(aspect: str) -> str:
    if aspect == "16:9":
        return "16:9 landscape"
    if aspect == "1:1":
        return "1:1 square"
    return "9:16 portrait"


def video_reference_reframe_prompt(source_path: Path, aspect: str, video_prompt: str) -> str:
    width, height = image_dimensions(source_path)
    orientation = video_reframe_orientation_label(aspect)
    scene_context = _text(video_prompt)[:1400]
    return "\n".join([
        f"Create a new {orientation} keyframe for image-to-video from the attached source image.",
        f"The source image is {width}x{height}; do not stretch, squeeze, or warp it to fit the new canvas.",
        "Recompose the scene naturally for the target canvas by outpainting, extending background, changing camera framing, or cropping only when safe.",
        "Preserve the same people, clothing, product/package, table/props, environment, lighting direction, and left/right relationship as much as possible.",
        "Preserve natural human proportions: face width/height, head size, neck length, shoulder width, torso length, arms, hands, and legs must remain realistic.",
        "Preserve product/package geometry and readable package proportions; do not elongate, narrow, bend, or mirror the product.",
        "For a portrait canvas, build a mobile-ad vertical composition that keeps the important people and product visible without compressing the original landscape image.",
        "Do not add subtitles, speech captions, title cards, QR codes, logos, watermarks, UI text, or decorative borders.",
        "Negative: vertically stretched face, narrowed face, elongated neck, squeezed shoulders, stretched body, stretched product, warped perspective, flattened scene.",
        "",
        "Video scene intent for this keyframe:",
        scene_context,
    ]).strip()


def video_portrait_reframe_asset_payload(rel_path: str, origin: dict[str, Any]) -> dict[str, Any]:
    filename = Path(rel_path).name
    return {
        "id": rel_path,
        "path": rel_path,
        "label": "Video portrait reframe",
        "filename": filename,
        "asset_type": "Image",
        "kind": "image",
        "source": "agent_generated",
        "created_at": now_ms(),
        "origin": origin,
    }


def prepare_xai_image_to_video_reference(
    task: dict[str, Any],
    workspace: Path,
    request_payload: dict[str, Any],
    video_prompt: str,
    source_path: Path | None,
    source_rel: str,
    output_path: Path,
    aspect: str, *, sc: Any,
) -> tuple[Path | None, dict[str, Any]]:
    if not source_path or image_reference_matches_video_aspect(source_path, aspect):
        return source_path, {}
    image_config, fallback_from = sc.load_reference_image_config("", "", sc=sc)
    target_size = image_size_for_video_reframe(aspect)
    reframe_prompt = video_reference_reframe_prompt(source_path, aspect, video_prompt)
    reframe_request_id = f"{_text(request_payload.get('request_id'), 'koubo_asset_video')}_portrait_reframe"
    output_dir = workspace / ASSET_IMAGES_REL
    output_dir.mkdir(parents=True, exist_ok=True)
    output_name = f"{output_path.stem}_portrait_reframe_{uuid.uuid4().hex[:8]}.png"
    reframe_path = output_dir / output_name
    reframe_rel = f"{ASSET_IMAGES_REL}/{output_name}"
    source_width, source_height = image_dimensions(source_path)
    started_at = time.time()
    detail = {
        "request_id": reframe_request_id,
        "task_id": int(task["id"]),
        "session_id": int(task["session_id"]),
        "provider": image_config["provider"],
        "model": image_config["model"],
        "source_image": source_rel,
        "source_width": source_width,
        "source_height": source_height,
        "target_aspect": aspect,
        "target_size": target_size,
        "output": reframe_rel,
        "prompt_preview": reframe_prompt[:1000],
        "prompt_length": len(reframe_prompt),
    }
    if fallback_from:
        detail["reference_image_provider_fallback_from"] = fallback_from
    sc.add_event(int(task["session_id"]), "koubo_storyboard.asset_library_agent.video.portrait_reframe.started", detail)
    image_bytes = sc.generate_image_bytes(image_config, reframe_prompt, [source_path], target_size, aspect, sc=sc)
    write_sanitized_image_bytes(reframe_path, image_bytes)
    local_usage = record_storyboard_usage(
        sc.ctx,
        task,
        request_id=reframe_request_id,
        provider=image_config["provider"],
        model_id=image_config["model"],
        modality="image",
        step_id="koubo_storyboard.asset_library_agent.video.portrait_reframe",
        units=image_usage_units(count=1, prompt=reframe_prompt, reference_count=1),
        started_at=started_at,
        finished_at=time.time(),
    )
    sidecar = {
        **detail,
        "prompt": reframe_prompt,
        "generated_at": now_ms(),
        "local_usage": local_usage,
        "local_usage_id": local_usage.get("local_usage_id", ""),
    }
    sidecar_path = workspace / ASSET_IMAGES_REL / f"{Path(output_name).stem}.json"
    sc.write_json(sidecar_path, sidecar)
    asset = video_portrait_reframe_asset_payload(reframe_rel, {
        "tool": "upload_asset_library_video_portrait_reframe",
        "request_id": reframe_request_id,
        "source_image": source_rel,
        "target_aspect": aspect,
        "target_size": target_size,
        "provider": image_config["provider"],
        "model": image_config["model"],
        "request_path": sidecar_path.relative_to(workspace).as_posix(),
        "local_usage_id": local_usage.get("local_usage_id", ""),
    })
    sc.upsert_asset_manifest_item(workspace, asset, sc=sc)
    result = {
        **sidecar,
        "asset": asset,
        "request_path": sidecar_path.relative_to(workspace).as_posix(),
        "elapsed_seconds": round(time.time() - started_at, 3),
    }
    sc.add_event(int(task["session_id"]), "koubo_storyboard.asset_library_agent.video.portrait_reframe.completed", result)
    return reframe_path, result


def dashscope_upload_file(api_key: str, model: str, path: Path) -> str:
    query = urllib.parse.urlencode({"action": "getPolicy", "model": model})
    policy = get_json_request(f"https://dashscope.aliyuncs.com/api/v1/uploads?{query}", {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, provider="wan")
    policy_data = policy.get("data") if isinstance(policy.get("data"), dict) else {}
    upload_host = _text(policy_data.get("upload_host"))
    upload_dir = _text(policy_data.get("upload_dir"))
    if not upload_host or not upload_dir:
        raise HTTPException(status_code=502, detail=f"DashScope upload policy is missing upload_host/upload_dir: {json.dumps(policy, ensure_ascii=False)[:1000]}")
    key = f"{upload_dir.rstrip('/')}/{path.name}"
    boundary = f"----OpenCrewDashScope{uuid.uuid4().hex}"
    fields = {
        "OSSAccessKeyId": _text(policy_data.get("oss_access_key_id")),
        "Signature": _text(policy_data.get("signature")),
        "policy": _text(policy_data.get("policy")),
        "x-oss-object-acl": _text(policy_data.get("x_oss_object_acl"), "private"),
        "x-oss-forbid-overwrite": _text(policy_data.get("x_oss_forbid_overwrite"), "true"),
        "key": key,
        "success_action_status": "200",
    }
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend([f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(), value.encode("utf-8"), b"\r\n"])
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    chunks.extend([f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'.encode(), f"Content-Type: {mime}\r\n\r\n".encode(), path.read_bytes(), b"\r\n", f"--{boundary}--\r\n".encode()])
    req = urllib.request.Request(upload_host, data=b"".join(chunks), headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, method="POST")
    try:
        with video_provider_urlopen(req, "wan", timeout=180) as res:
            if getattr(res, "status", 200) != 200:
                raise HTTPException(status_code=502, detail=f"DashScope upload failed: HTTP {res.status}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2000]
        raise HTTPException(status_code=502, detail=f"DashScope upload failed: HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise HTTPException(status_code=502, detail=f"DashScope upload failed: {exc.reason}") from exc
    return f"oss://{key}"


def normalize_video_reference_role(value: Any) -> str:
    role = _text(value).upper().replace("-", "_").replace(" ", "_")
    aliases = {
        "TARGET": "TARGET_FRAME",
        "BASE": "TARGET_FRAME",
        "FRAME": "TARGET_FRAME",
        "HOST": "HOST_REFERENCE",
        "PERSON": "HOST_REFERENCE",
        "CHARACTER": "HOST_REFERENCE",
        "PRODUCT": "PRODUCT_REFERENCE",
    }
    normalized = aliases.get(role, role)
    return normalized if normalized in {"TARGET_FRAME", "HOST_REFERENCE", "PRODUCT_REFERENCE", "REFERENCE_IMAGE"} else ""


def infer_video_reference_role(path: str, label: str = "", source: str = "") -> str:
    haystack = f"{path} {label} {source}".lower()
    if "host" in haystack or "person" in haystack or "character" in haystack or "人物" in haystack:
        return "HOST_REFERENCE"
    if "product" in haystack or "prodcut" in haystack or "产品" in haystack:
        return "PRODUCT_REFERENCE"
    if "target" in haystack or "frame" in haystack or "working" in haystack:
        return "TARGET_FRAME"
    return "REFERENCE_IMAGE"


def validate_video_reference_images(workspace: Path, values: Any, *, sc: Any) -> tuple[list[str], list[Path], list[dict[str, str]], list[str]]:
    refs: list[str] = []
    paths: list[Path] = []
    items: list[dict[str, str]] = []
    missing: list[str] = []
    seen: set[str] = set()
    target_seen = False
    for item in values if isinstance(values, list) else []:
        rel_path = _text(item.get("path") if isinstance(item, dict) else item)
        if not rel_path or rel_path in seen:
            continue
        seen.add(rel_path)
        label = _text(item.get("label") or item.get("filename") if isinstance(item, dict) else "", Path(rel_path).name)
        source = _text(item.get("source") if isinstance(item, dict) else "")
        role = normalize_video_reference_role((item.get("role") or item.get("reference_role")) if isinstance(item, dict) else "")
        if not role and not target_seen and source != "session_consistency_reference":
            role = "TARGET_FRAME"
            target_seen = True
        if not role:
            role = infer_video_reference_role(rel_path, label, source)
        if role == "TARGET_FRAME":
            target_seen = True
        if not any(rel_path.startswith(prefix) for prefix in VIDEO_REFERENCE_PREFIXES):
            missing.append(rel_path)
            continue
        _, path = sc.safe_workspace_rel(workspace, rel_path)
        if path.exists() and path.is_file() and path.suffix.lower() in IMAGE_EXTS:
            refs.append(rel_path)
            paths.append(path)
            items.append({"path": rel_path, "role": role or "REFERENCE_IMAGE", "label": label})
        else:
            missing.append(rel_path)
    # SR2 can use up to 8 image references; alias-specific validation below keeps SI/HR/WR tighter.
    return refs[:8], paths[:8], items[:8], missing


def infer_video_reference_kind(item: Any, rel_path: str) -> str:
    explicit = _text(
        (item.get("kind") or item.get("asset_type") or item.get("type")) if isinstance(item, dict) else ""
    ).lower()
    if "audio" in explicit:
        return "audio"
    if "video" in explicit:
        return "video"
    if "image" in explicit:
        return "image"
    suffix = Path(rel_path).suffix.lower()
    if suffix in AUDIO_EXTS:
        return "audio"
    if suffix in VIDEO_EXTS:
        return "video"
    if suffix in IMAGE_EXTS:
        return "image"
    return ""


def reference_values_for_kind(payload: dict[str, Any], kind: str) -> list[Any]:
    values: list[Any] = []
    direct_key = {
        "image": "reference_images",
        "video": "reference_videos",
        "audio": "reference_audios",
    }.get(kind, "")
    if direct_key and isinstance(payload.get(direct_key), list):
        values.extend(payload.get(direct_key) or [])
    for item in payload.get("reference_assets") if isinstance(payload.get("reference_assets"), list) else []:
        rel_path = _text(item.get("path") if isinstance(item, dict) else item)
        if rel_path and infer_video_reference_kind(item, rel_path) == kind:
            values.append(item)
    return values


def unique_reference_value_count(values: Any) -> int:
    seen: set[str] = set()
    for item in values if isinstance(values, list) else []:
        rel_path = _text(item.get("path") if isinstance(item, dict) else item)
        if rel_path:
            seen.add(rel_path)
    return len(seen)


def validate_video_reference_media(workspace: Path, values: Any, suffixes: set[str], kind: str, limit: int = 4, *, sc: Any) -> tuple[list[str], list[Path], list[dict[str, str]], list[str]]:
    refs: list[str] = []
    paths: list[Path] = []
    items: list[dict[str, str]] = []
    missing: list[str] = []
    seen: set[str] = set()
    for item in values if isinstance(values, list) else []:
        rel_path = _text(item.get("path") if isinstance(item, dict) else item)
        if not rel_path or rel_path in seen:
            continue
        seen.add(rel_path)
        label = _text(item.get("label") or item.get("filename") if isinstance(item, dict) else "", Path(rel_path).name)
        if not any(rel_path.startswith(prefix) for prefix in VIDEO_MEDIA_REFERENCE_PREFIXES):
            missing.append(rel_path)
            continue
        _, path = sc.safe_workspace_rel(workspace, rel_path)
        if path.exists() and path.is_file() and path.suffix.lower() in suffixes:
            refs.append(rel_path)
            paths.append(path)
            items.append({"path": rel_path, "role": f"REFERENCE_{kind.upper()}", "label": label, "kind": kind})
        else:
            missing.append(rel_path)
    return refs[:limit], paths[:limit], items[:limit], missing


def video_reference_prompt_prefix(reference_items: list[dict[str, str]], aspect: str = "") -> str:
    if not reference_items:
        return ""
    roles = {_text(item.get("role")) for item in reference_items}
    lines = [
        "Role-bound generation references:",
        "The attached reference images/audio/videos are ordered exactly as listed below. Treat each role as a binding instruction, not a caption.",
    ]
    for index, item in enumerate(reference_items, start=1):
        kind_note = _text(item.get("kind"))
        kind_prefix = f"{kind_note} " if kind_note else ""
        lines.append(f"{index}. {kind_prefix}{_text(item.get('role'), 'REFERENCE_IMAGE')}: {_text(item.get('label')) or Path(_text(item.get('path'))).name} ({_text(item.get('path'))})")
    if "TARGET_FRAME" in roles:
        lines.append("TARGET_FRAME controls the editable base scene: composition, camera angle, background, pose category, hand/product positions, scale, perspective, occlusion, lighting, shadows, and phone-video texture.")
    if "HOST_REFERENCE" in roles:
        lines.append("HOST_REFERENCE controls the complete visible presenter identity and styling: face, hair, clothing, microphone/accessories, skin tone, and human continuity.")
    if "PRODUCT_REFERENCE" in roles:
        lines.append("PRODUCT_REFERENCE controls the complete product/package identity: package shape, color hierarchy, label direction, visible text-block structure, material, cap/seal, box/sachet structure, and graphic layout.")
    if {"TARGET_FRAME", "HOST_REFERENCE", "PRODUCT_REFERENCE"}.issubset(roles):
        lines.append("This is a strict role-bound replacement/continuation task. Preserve the target scene while binding host and product identity to their role references.")
    if "REFERENCE_AUDIO" in roles:
        lines.append("REFERENCE_AUDIO controls voice, cadence, rhythm, and sound-style guidance when supported by the selected model; do not add subtitles.")
    if "REFERENCE_VIDEO" in roles:
        lines.append("REFERENCE_VIDEO controls motion, pacing, gesture style, and temporal continuity when supported by the selected model.")
    lines.append("User/spoken words are semantic guidance only. Do not render subtitles, speech captions, title cards, labels, UI text, QR codes, watermarks, or overlay text from user instructions or dialogue.")
    if aspect == "9:16":
        lines.append("Output aspect is 9:16 portrait. Recompose, crop, or extend natural background space to fit; never vertically stretch, squeeze, or warp the person, product, or any reference image.")
    elif aspect == "16:9":
        lines.append("Output aspect is 16:9 landscape. Recompose or crop naturally; preserve human and product geometry.")
    return "\n".join(lines)


def effective_video_prompt(prompt: str, reference_items: list[dict[str, str]], aspect: str = "") -> str:
    prefix = video_reference_prompt_prefix(reference_items, aspect)
    return f"{prefix}\n\n{prompt}".strip() if prefix else prompt


def run_asset_library_video_provider(
    task: dict[str, Any],
    request_payload: dict[str, Any],
    prompt: str,
    config: dict[str, Any],
    output_rel: str,
    reference_paths: list[Path],
    reference_audio_paths: list[Path],
    reference_video_paths: list[Path],
    duration: float | int | str | None,
    aspect: str, *, sc: Any,
) -> dict[str, Any]:
    workspace = sc.workspace_for(task)
    output_path = workspace / output_rel
    output_path.parent.mkdir(parents=True, exist_ok=True)
    provider = _text(config.get("provider")).lower()
    model = _text(config.get("model"))
    is_gemini_omni = provider == "gemini" and model == GEMINI_OMNI_MODEL
    if is_gemini_omni:
        require_gemini_omni_enabled()
    if provider in {"chanjing", "chanjing.cc", "cj"} and model.lower().startswith("happyhorse-1.0"):
        provider = "wan"
        config = {**config, "provider": provider}
    api_key = _text(config.get("api_key"))
    if not api_key:
        raise HTTPException(status_code=400, detail=f"Video provider API key is missing: {provider}/{model}")
    call_started = time.time()
    seconds = video_provider_seconds(provider, model, duration)
    aspect = normalize_video_aspect(aspect)
    assert_prompt_aspect_matches(prompt, aspect)
    video_url = ""
    provider_meta: dict[str, Any] = {}
    provider_usage: dict[str, Any] = {}
    requested_resolution = ""
    provider_task_id = ""
    provider_profile = ""
    if reference_audio_paths and provider != "openrouter":
        raise HTTPException(status_code=400, detail={
            "message": "Audio references are currently supported only through OpenRouter Seedance SR2.",
            "provider": provider,
            "model": model,
            "reference_audio_count": len(reference_audio_paths),
        })
    if reference_video_paths and provider not in {"openrouter", "wan"} and not is_gemini_omni:
        raise HTTPException(status_code=400, detail={
            "message": "Video references are currently supported through OpenRouter Seedance SR2 or Wan WR2.7.",
            "provider": provider,
            "model": model,
            "reference_video_count": len(reference_video_paths),
        })
    if reference_video_paths and provider == "wan" and model.lower() not in WAN_R2V_MODELS:
        raise HTTPException(status_code=400, detail={
            "message": "Wan video references require Max WR2.7 / wan2.7-r2v.",
            "provider": provider,
            "model": model,
            "reference_video_count": len(reference_video_paths),
        })
    if model.lower() == HAPPYHORSE_R2V_MODEL and (not reference_paths or len(reference_paths) > 3):
        raise HTTPException(status_code=400, detail={
            "message": "Max HR1.0 / HappyHorse R2V requires 1 to 3 reference images.",
            "provider": provider,
            "model": model,
            "reference_image_count": len(reference_paths),
        })
    if model.lower() in WAN_R2V_MODELS:
        total_reference_count = len(reference_paths) + len(reference_video_paths)
        if total_reference_count < 1 or total_reference_count > WAN_R2V_REFERENCE_TOTAL_LIMIT:
            raise HTTPException(status_code=400, detail={
                "message": "Max WR2.7 / Wan R2V accepts 1 to 5 total image/video references.",
                "provider": provider,
                "model": model,
                "reference_image_count": len(reference_paths),
                "reference_video_count": len(reference_video_paths),
                "limits": {"total_image_video_references": WAN_R2V_REFERENCE_TOTAL_LIMIT},
            })
    first_image = reference_paths[0] if reference_paths else None
    last_image = reference_paths[1] if len(reference_paths) > 1 else None
    if provider == "xai" and xai_video_model_requires_image(model) and not first_image:
        raise HTTPException(status_code=400, detail={
            "message": "grok-imagine-video-1.5 requires one selected image reference. It does not support text-to-video.",
            "provider": provider,
            "model": model,
            "reference_image_count": len(reference_paths),
            "suggestion": "Select one image in the video workspace, or switch to grok-imagine-video / another text-to-video capable model.",
        })
    call_detail = {
        **request_payload,
        "provider": provider,
        "model": model,
        "method": "POST",
        "duration": duration,
        "effective_duration_seconds": seconds,
        "aspect": aspect,
        "reference_image_count": len(reference_paths),
        "reference_audio_count": len(reference_audio_paths),
        "reference_video_count": len(reference_video_paths),
        "network_mode": "environment_urlopen",
        "workspace_dir": str(workspace),
        "output": output_rel,
        "output_path": str(output_path),
        "prompt_preview": prompt[:1000],
        "prompt_length": len(prompt),
    }
    event_call_detail = call_detail
    if is_gemini_omni:
        event_call_detail = {
            key: value
            for key, value in call_detail.items()
            if key not in {"provider", "model", "output_path", "workspace_dir", "prompt_preview"}
        }
        event_call_detail["stateful"] = True
    sc.add_event(int(task["session_id"]), "koubo_storyboard.asset_library_agent.video.provider_call.started", event_call_detail)
    first_image_rel = _text((call_detail.get("reference_images") or [""])[0] if isinstance(call_detail.get("reference_images"), list) and call_detail.get("reference_images") else "")
    if is_gemini_omni:
        repository = video_interaction_repository(sc=sc)
        turn_id = _text(config.get("_omni_turn_id"))
        lease_token = _text(config.get("_omni_lease_token"))
        previous_interaction_id = _text(config.get("_omni_previous_interaction_id"))
        client = GeminiOmniClient(api_key)
        if previous_interaction_id:
            try:
                client.get_interaction(previous_interaction_id)
            except GeminiOmniError as exc:
                if exc.code in {"gemini_omni_interaction_expired", "gemini_omni_previous_interaction_invalid"}:
                    repository.mark_provider_expired(_text(config.get("_omni_parent_turn_id")))
                raise
        provider_inputs: list[dict[str, str]] = []
        for reference_path in [*reference_paths, *reference_video_paths]:
            uploaded = client.upload_file(reference_path)
            provider_inputs.append(
                gemini_omni_file_input(
                    uploaded,
                    media_type="video" if reference_path in reference_video_paths else "image",
                )
            )
        omni_task = omni_task_for(
            _text(config.get("_omni_operation"), "generate"),
            image_count=len(reference_paths),
            video_count=len(reference_video_paths),
        )
        interaction_request = build_interaction_request(
            prompt=prompt,
            task=omni_task,
            aspect_ratio=aspect,
            delivery="uri",
            store=True,
            background=True,
            previous_interaction_id=previous_interaction_id,
            file_inputs=provider_inputs,
            duration_seconds=seconds,
        )

        def persist_interaction(interaction_id: str, expires_at: int | None, expiry_source: str) -> None:
            repository.mark_provider_request_sent(
                turn_id,
                interaction_id=interaction_id,
                provider_state_expires_at=expires_at,
                provider_expiry_source=expiry_source,
            )

        def renew_interaction_lease() -> None:
            if not repository.renew_lease(turn_id, lease_token):
                raise GeminiOmniError(
                    "video_stateful_edit_in_progress",
                    "The stateful video edit lease was lost",
                    status_code=409,
                )

        completed_interaction = client.run_interaction(
            interaction_request,
            interaction_callback=persist_interaction,
            lease_callback=renew_interaction_lease,
        )
        provider_meta.update(
            materialize_gemini_omni_video_output(
                completed_interaction,
                output_path,
                api_key=api_key,
                download_video_binary=sc.download_video_binary,
                sanitize_video_output=sanitize_video_output,
            )
        )
        provider_meta.update({"stateful": True, "operation": _text(config.get("_omni_operation")), "omni_task": omni_task})
    elif provider == "gemini":
        normalized_first = normalized_image_reference(first_image, output_path, aspect)
        endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(model, safe='')}:predictLongRunning?key={urllib.parse.quote(api_key, safe='')}"
        instance: dict[str, Any] = {"prompt": prompt}
        inline = image_inline_payload(normalized_first)
        if inline:
            instance["image"] = {"mimeType": inline["mimeType"], "bytesBase64Encoded": inline["bytesBase64Encoded"]}
        operation = post_video_json_request(endpoint, {"instances": [instance], "parameters": {"sampleCount": 1, "durationSeconds": seconds, "aspectRatio": aspect}}, {}, provider=provider)
        op_name = _text(operation.get("name"))
        if not op_name:
            raise HTTPException(status_code=502, detail=f"Gemini video response did not include operation name: {json.dumps(operation, ensure_ascii=False)[:1000]}")
        poll_url = f"https://generativelanguage.googleapis.com/v1beta/{op_name}?key={urllib.parse.quote(api_key, safe='')}" if not op_name.startswith("http") else op_name
        deadline = time.time() + 900
        while time.time() < deadline:
            polled = get_json_request(poll_url, {}, provider=provider)
            failure = operation_failed(polled)
            if failure:
                raise HTTPException(status_code=502, detail=f"Video generation failed: {failure}")
            if operation_done(polled):
                video_url = first_video_url(polled)
                break
            time.sleep(5)
        if not video_url:
            raise HTTPException(status_code=502, detail="Gemini video generation completed without a downloadable video URL")
        download_video_binary(video_url, output_path, {"x-goog-api-key": api_key}, provider=provider)
    elif provider == "wan":
        if model.lower() in WAN_R2V_MODELS and (reference_paths or reference_video_paths):
            provider_meta.update(run_wan_rtv_asset_video(prompt, config, output_path, reference_paths, reference_video_paths, seconds, aspect, sc=sc))
            video_url = _text(provider_meta.get("video_url"))
            try:
                sanitize_video_output(output_path)
            except MediaSanitizeError as exc:
                output_path.unlink(missing_ok=True)
                raise HTTPException(status_code=502, detail=f"Video metadata sanitization failed: {exc}") from exc
        else:
            input_payload: dict[str, Any] = {"prompt": prompt}
            if first_image:
                media_type = "reference_image" if "r2v" in model else "first_frame"
                image_limit = 3 if model.lower() == HAPPYHORSE_R2V_MODEL else 1
                input_payload["media"] = [{"type": media_type, "url": dashscope_upload_file(api_key, model, image_path)} for image_path in reference_paths[:image_limit]]
            if last_image and model.lower() != HAPPYHORSE_R2V_MODEL:
                input_payload["last_frame_url"] = dashscope_upload_file(api_key, model, last_image)
            parameters = dashscope_video_parameters(config, seconds, aspect)
            started = post_video_json_request(
                "https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis",
                {"model": model, "input": input_payload, "parameters": parameters},
                {"Authorization": f"Bearer {api_key}", "X-DashScope-Async": "enable", "X-DashScope-OssResourceResolve": "enable"},
                provider=provider,
            )
            task_id_value = _text(((started.get("output") or {}).get("task_id") if isinstance(started.get("output"), dict) else "") or started.get("task_id"))
            if not task_id_value:
                raise HTTPException(status_code=502, detail=f"Wan response did not include task_id: {json.dumps(started, ensure_ascii=False)[:1000]}")
            poll_url = f"https://dashscope.aliyuncs.com/api/v1/tasks/{urllib.parse.quote(task_id_value, safe='')}"
            deadline = time.time() + 900
            while time.time() < deadline:
                polled = get_json_request(poll_url, {"Authorization": f"Bearer {api_key}"}, provider=provider)
                status_payload = polled.get("output") if isinstance(polled.get("output"), dict) else polled
                failure = operation_failed(status_payload)
                if failure:
                    raise HTTPException(status_code=502, detail=f"Video generation failed: {failure}")
                if operation_done(status_payload):
                    video_url = first_video_url(polled)
                    break
                time.sleep(5)
            if not video_url:
                raise HTTPException(status_code=502, detail="Wan video generation completed without a downloadable video URL")
            download_video_binary(video_url, output_path, provider=provider)
    elif provider in {"bytedance", "seedance", "volcengine", "ark"}:
        provider_profile = "video_seedance"
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        normalized_first = normalized_image_reference(first_image, output_path, aspect)
        inline = image_inline_payload(normalized_first)
        if inline:
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:{inline['mimeType']};base64,{inline['bytesBase64Encoded']}",
                    "role": "first_frame",
                },
            })
        ratio = _text(config.get("ratio") or config.get("default_ratio"), aspect)
        resolution = _text(config.get("resolution") or config.get("default_resolution"), "720p")
        generate_audio = bool_config(config.get("generate_audio"), False)
        seedance_payload = {
            "model": model,
            "content": content,
            "ratio": ratio,
            "resolution": resolution,
            "duration": seconds,
            "generate_audio": generate_audio,
        }
        base_url = _text(config.get("base_url"), "https://ark.cn-beijing.volces.com/api/v3").rstrip("/")
        headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
        started = post_video_json_request(f"{base_url}/contents/generations/tasks", seedance_payload, headers, provider=provider)
        provider_task_id = video_task_id(started)
        if not provider_task_id:
            raise HTTPException(status_code=502, detail=f"Seedance response did not include task id: {json.dumps(started, ensure_ascii=False)[:1000]}")
        poll_url = f"{base_url}/contents/generations/tasks/{urllib.parse.quote(provider_task_id, safe='')}"
        deadline = time.time() + 900
        last_status = ""
        while time.time() < deadline:
            polled = get_json_request(poll_url, headers, provider=provider)
            last_status = operation_status(polled)
            failure = operation_failed(polled)
            if failure:
                raise HTTPException(status_code=502, detail=f"Video generation failed: {failure}")
            if operation_done(polled):
                video_url = first_video_url(polled)
                break
            time.sleep(5)
        if not video_url:
            raise HTTPException(status_code=502, detail=f"Seedance video generation timed out or completed without URL. task_id={provider_task_id} status={last_status or 'unknown'}")
        download_video_binary(video_url, output_path, {"User-Agent": "OpenCrew/seedance-video-download"}, provider=provider)
    elif provider == "openai":
        normalized_first = normalized_image_reference(first_image, output_path, aspect)
        payload: dict[str, Any] = {"model": model, "prompt": prompt, "seconds": str(seconds), "size": video_size_for_aspect(aspect)}
        inline = image_inline_payload(normalized_first)
        if inline:
            payload["input_reference"] = {"image_url": f"data:{inline['mimeType']};base64,{inline['bytesBase64Encoded']}"}
        started = post_video_json_request("https://api.openai.com/v1/videos", payload, {"Authorization": f"Bearer {api_key}"}, timeout=180, provider=provider)
        video_id = _text(started.get("id"))
        if not video_id:
            raise HTTPException(status_code=502, detail=f"OpenAI video response did not include id: {json.dumps(started, ensure_ascii=False)[:1000]}")
        deadline = time.time() + 900
        while time.time() < deadline:
            polled = get_json_request(f"https://api.openai.com/v1/videos/{urllib.parse.quote(video_id, safe='')}", {"Authorization": f"Bearer {api_key}"}, provider=provider)
            status = _text(polled.get("status")).lower()
            if status in {"failed", "cancelled", "canceled"}:
                raise HTTPException(status_code=502, detail=f"Video generation failed: {json.dumps(polled, ensure_ascii=False)[:1200]}")
            if status in {"completed", "succeeded", "success"}:
                video_url = first_video_url(polled)
                break
            time.sleep(5)
        if video_url:
            download_video_binary(video_url, output_path, {"Authorization": f"Bearer {api_key}"}, provider=provider)
        else:
            download_video_binary(f"https://api.openai.com/v1/videos/{urllib.parse.quote(video_id, safe='')}/content", output_path, {"Authorization": f"Bearer {api_key}"}, provider=provider)
    elif provider == "xai":
        xai_image, portrait_reframe = prepare_xai_image_to_video_reference(task, workspace, request_payload, prompt, first_image, first_image_rel, output_path, aspect, sc=sc)
        requested_resolution = xai_video_resolution(config, model)
        payload: dict[str, Any] = {"model": model, "prompt": prompt, "duration": seconds, "aspect_ratio": aspect, "resolution": requested_resolution}
        if portrait_reframe:
            provider_meta["portrait_reframe"] = portrait_reframe
        inline = image_inline_payload(xai_image)
        if inline:
            payload["image"] = {"url": f"data:{inline['mimeType']};base64,{inline['bytesBase64Encoded']}"}
        started = post_video_json_request("https://api.x.ai/v1/videos/generations", payload, {"Authorization": f"Bearer {api_key}"}, provider=provider)
        final_provider_payload = started
        video_id = _text(started.get("request_id") or started.get("id") or ((started.get("data") or {}).get("id") if isinstance(started.get("data"), dict) else ""))
        video_url = first_video_url(started)
        deadline = time.time() + 900
        while not video_url and video_id and time.time() < deadline:
            polled = get_json_request(f"https://api.x.ai/v1/videos/{urllib.parse.quote(video_id, safe='')}", {"Authorization": f"Bearer {api_key}"}, provider=provider)
            final_provider_payload = polled
            failure = operation_failed(polled)
            if failure:
                raise HTTPException(status_code=502, detail=f"Video generation failed: {failure}")
            if operation_done(polled):
                video_url = first_video_url(polled)
                break
            time.sleep(5)
        if not video_url:
            raise HTTPException(status_code=502, detail="xAI video generation completed without a downloadable video URL")
        download_video_binary(video_url, output_path, {"Authorization": f"Bearer {api_key}"}, provider=provider)
        provider_usage = final_provider_payload.get("usage") if isinstance(final_provider_payload.get("usage"), dict) else {}
        if not provider_usage and isinstance(started.get("usage"), dict):
            provider_usage = started["usage"]
        provider_meta["resolution"] = requested_resolution
    elif provider == "openrouter":
        provider_meta.update(run_openrouter_asset_video(prompt, config, output_path, reference_paths, reference_audio_paths, reference_video_paths, seconds, aspect, sc=sc))
        video_url = _text(provider_meta.get("video_url"))
        try:
            sanitize_video_output(output_path)
        except MediaSanitizeError as exc:
            output_path.unlink(missing_ok=True)
            raise HTTPException(status_code=502, detail=f"Video metadata sanitization failed: {exc}") from exc
    elif provider in {"chanjing", "chanjing.cc", "cj"}:
        if not model.lower().startswith("happyhorse-1.0"):
            raise HTTPException(status_code=400, detail=f"Unsupported Chanjing asset-library video model: {model}")
        provider_meta.update(run_chanjing_happyhorse_asset_video(prompt, config, output_path, reference_paths, seconds, aspect, sc=sc))
        video_url = _text(provider_meta.get("video_url"))
        try:
            sanitize_video_output(output_path)
        except MediaSanitizeError as exc:
            output_path.unlink(missing_ok=True)
            raise HTTPException(status_code=502, detail=f"Video metadata sanitization failed: {exc}") from exc
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported video provider: {provider}")
    provider_meta.update(validate_video_output_aspect(output_path, aspect, provider=provider, model=model, sc=sc))
    elapsed_seconds = round(time.time() - call_started, 3)
    usage_task_id = task.get("id") or task.get("task_id") or task.get("session_id") or ""
    usage_request_id = _text(config.get("_omni_usage_request_id")) if is_gemini_omni else ""
    if not usage_request_id:
        usage_request_id = stable_usage_request_id("koubo_asset_video", usage_task_id, task.get("latest_attempt_id"), output_rel, provider, model)
    actual_cost_micros = xai_usage_cost_micros(provider_usage) if provider == "xai" else None
    reference_count = int(first_image is not None) if provider == "xai" else len(reference_paths) + len(reference_audio_paths) + len(reference_video_paths)
    metered_seconds = (
        float(provider_meta.get("duration_seconds") or seconds)
        if is_gemini_omni
        else seconds
    )
    local_usage = record_storyboard_usage(
        sc.ctx,
        task,
        request_id=usage_request_id,
        provider=provider,
        model_id=model,
        modality="video",
        step_id="koubo_storyboard.asset_library_agent.video",
        units=video_usage_units(
            seconds=metered_seconds,
            prompt=prompt,
            reference_count=reference_count,
            resolution="720p" if is_gemini_omni else requested_resolution,
        ),
        estimated_cost_micros=(
            round(metered_seconds * GEMINI_OMNI_720P_USD_PER_SECOND * 1_000_000)
            if is_gemini_omni
            else None
        ),
        started_at=call_started,
        finished_at=time.time(),
        actual_cost_micros=actual_cost_micros,
        actual_cost_source="response.usage.cost_in_usd_ticks" if actual_cost_micros is not None else "",
        actual_cost_raw={"usage": provider_usage} if actual_cost_micros is not None else {},
    )
    public_call_detail = event_call_detail if is_gemini_omni else call_detail
    result = {**public_call_detail, **provider_meta, "ok": True, "output": output_rel, "output_path": output_rel if is_gemini_omni else str(output_path), "elapsed_seconds": elapsed_seconds, "video_url": "" if is_gemini_omni else video_url}
    result.update({"local_usage": local_usage, "local_usage_id": local_usage.get("local_usage_id", "")})
    if provider_task_id:
        result.update({"provider_profile": provider_profile, "provider_task_id": provider_task_id, "task_id": provider_task_id})
    sc.add_event(int(task["session_id"]), "koubo_storyboard.asset_library_agent.video.provider_call.completed", result)
    return result


def uploaded_video_asset_payload(rel_path: str, source: str, label: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    filename = Path(rel_path).name
    asset = {
        "id": rel_path,
        "path": rel_path,
        "label": label or filename,
        "filename": filename,
        "asset_type": "Video",
        "kind": "video",
        "source": source,
        "created_at": now_ms(),
    }
    if extra:
        asset.update(extra)
    return asset


def generate_asset_library_video(task_id: int, payload: dict[str, Any], *, sc: Any) -> dict[str, Any]:
    task = sc.task_or_404(task_id)
    workspace = sc.workspace_for(task)
    prompt = _text(payload.get("prompt"))
    if len(prompt) < 4:
        raise HTTPException(status_code=400, detail="prompt is required")
    image_reference_values = reference_values_for_kind(payload, "image")
    audio_reference_values = reference_values_for_kind(payload, "audio")
    video_reference_values = reference_values_for_kind(payload, "video")
    reference_image_input_count = unique_reference_value_count(image_reference_values)
    reference_audio_input_count = unique_reference_value_count(audio_reference_values)
    reference_video_input_count = unique_reference_value_count(video_reference_values)
    reference_rels, reference_paths, reference_items, missing_refs = validate_video_reference_images(workspace, image_reference_values, sc=sc)
    reference_audio_rels, reference_audio_paths, reference_audio_items, missing_audio_refs = validate_video_reference_media(workspace, audio_reference_values, AUDIO_EXTS, "audio", 4, sc=sc)
    reference_video_rels, reference_video_paths, reference_video_items, missing_video_refs = validate_video_reference_media(workspace, video_reference_values, VIDEO_EXTS, "video", 4, sc=sc)
    if missing_refs or missing_audio_refs or missing_video_refs:
        raise HTTPException(status_code=400, detail={
            "message": "Selected video generation references were not found in uploaded assets",
            "missing_reference_images": missing_refs,
            "missing_reference_audios": missing_audio_refs,
            "missing_reference_videos": missing_video_refs,
        })
    config = load_video_config_for_generation(task, payload, sc=sc)
    is_gemini_omni = (
        _text(config.get("provider")).lower() == "gemini"
        and _text(config.get("model")) == GEMINI_OMNI_MODEL
    )
    if is_gemini_omni:
        try:
            require_gemini_omni_enabled()
        except GeminiOmniError as exc:
            raise exc.as_http_exception() from exc
        if reference_audio_paths:
            raise HTTPException(status_code=400, detail={
                "code": "video_stateful_invalid_request",
                "message": "Gemini Omni does not accept uploaded audio references",
            })
        if len(reference_video_paths) > 1:
            raise HTTPException(status_code=400, detail={
                "code": "video_stateful_invalid_request",
                "message": "Gemini Omni accepts at most one uploaded video for editing",
            })
    reference_mode = _text(payload.get("reference_mode") or payload.get("referenceMode"))
    agent_video_alias = _text(payload.get("agentVideoAlias") or payload.get("agent_video_alias") or config.get("agentVideoAlias") or config.get("agent_video_alias"))
    alias_key = _alias_key(agent_video_alias)
    is_max_sr2 = alias_key == "maxsr2" or (
        _text(config.get("provider")).lower() == "openrouter"
        and _text(config.get("model")).lower() == OPENROUTER_SR2_MODEL
    )
    if is_max_sr2 and (reference_image_input_count > 8 or reference_audio_input_count > 4 or reference_video_input_count > 4):
        raise HTTPException(status_code=400, detail={
            "message": "Max SR2 supports at most 8 image references, 4 audio references, and 4 video references.",
            "reference_image_count": reference_image_input_count,
            "reference_audio_count": reference_audio_input_count,
            "reference_video_count": reference_video_input_count,
            "limits": {
                "reference_images": 8,
                "reference_audios": 4,
                "reference_videos": 4,
            },
        })
    if alias_key == "maxsi2" and (len(reference_paths) > 1 or reference_audio_paths or reference_video_paths):
        raise HTTPException(status_code=400, detail={
            "message": "Max SI2 supports text-to-video with no references, or at most one image reference. It does not accept audio/video references.",
            "reference_image_count": len(reference_paths),
            "reference_audio_count": len(reference_audio_paths),
            "reference_video_count": len(reference_video_paths),
        })
    if alias_key == "maxwr27":
        total_reference_count = reference_image_input_count + reference_video_input_count
        if total_reference_count < 1 or total_reference_count > WAN_R2V_REFERENCE_TOTAL_LIMIT or reference_audio_input_count:
            raise HTTPException(status_code=400, detail={
                "message": "Max WR2.7 accepts 1 to 5 total image/video references. Standalone audio references are not wired in this chain yet.",
                "reference_image_count": reference_image_input_count,
                "reference_audio_count": reference_audio_input_count,
                "reference_video_count": reference_video_input_count,
                "limits": {
                    "total_image_video_references": WAN_R2V_REFERENCE_TOTAL_LIMIT,
                    "reference_audios": 0,
                },
            })
    if alias_key == "maxhr10" and (not reference_paths or len(reference_paths) > 3 or reference_audio_paths or reference_video_paths):
        raise HTTPException(status_code=400, detail={
            "message": "Max HR1.0 requires 1 to 3 image references and does not accept audio/video references.",
            "reference_image_count": len(reference_paths),
            "reference_audio_count": len(reference_audio_paths),
            "reference_video_count": len(reference_video_paths),
        })
    if reference_mode:
        config = {**config, "reference_mode": reference_mode}
    if alias_key == "maxsi2":
        config = {**config, "reference_mode": "first_frame", "send_frame_images": True}
    if alias_key == "maxsr2":
        config = {**config, "reference_mode": "input_references", "generate_audio": True}
    if _text(config.get("provider")).lower() == "openrouter" and (reference_audio_paths or reference_video_paths):
        config = {**config, "reference_mode": "input_references", "generate_audio": True}
    aspect, aspect_meta = resolve_reference_frame_video_aspect(reference_paths, payload.get("aspect"), prompt, sc=sc)
    if is_gemini_omni and aspect not in {"16:9", "9:16"}:
        raise HTTPException(status_code=400, detail={
            "code": "video_stateful_invalid_request",
            "message": "Gemini Omni supports only 16:9 and 9:16 video",
        })
    all_reference_items = [*reference_items, *reference_audio_items, *reference_video_items]
    effective_prompt = effective_video_prompt(prompt, all_reference_items, aspect)
    duration = payload.get("duration") if payload.get("duration") is not None else payload.get("duration_seconds")
    if is_gemini_omni:
        # The live Preview API now rejects the historical 1-second smoke-test
        # value. Normalize old clients and pending turns to the supported
        # minimum before persisting the request scope or calling Google.
        duration = video_provider_seconds("gemini", GEMINI_OMNI_MODEL, duration)
    batch = str(now_ms())
    output_dir = workspace / ASSET_VIDEOS_REL
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_title = re.sub(r"[^a-zA-Z0-9_-]+", "_", _text(payload.get("title"), "agent_video").lower())[:48].strip("_") or "agent_video"
    output_name = f"{batch}_agent_generated_{safe_title}_{uuid.uuid4().hex[:8]}.mp4"
    output_rel = f"{ASSET_VIDEOS_REL}/{output_name}"
    request_id = f"koubo_asset_library_video_agent_{batch}_{uuid.uuid4().hex[:8]}"
    chat_session_id = _text(payload.get("chat_opencode_session_id") or payload.get("chat_session_id"))
    prompt_builder_request_id = _text(payload.get("prompt_builder_request_id"))
    prompt_builder_applied_path = _text(payload.get("prompt_builder_applied_path"))
    agent_message_id = _text(payload.get("agent_message_id"))
    agent_generation_id = _text(payload.get("agent_generation_id"))
    interaction_repository: VideoInteractionRepository | None = None
    interaction_claim = None
    interaction_public: dict[str, Any] = {}
    if is_gemini_omni:
        interaction_repository = video_interaction_repository(sc=sc)
        actor_id = video_interaction_actor_id(task)
        operation = _text(payload.get("operation")).lower()
        thread_id = _text(payload.get("video_thread_id") or payload.get("thread_id"))
        parent_turn_id = _text(payload.get("parent_turn_id") or payload.get("video_turn_id"))
        if not operation:
            operation = "continue" if thread_id or parent_turn_id else ("edit" if reference_video_paths else "generate")
        if operation == "continue" and not thread_id:
            current = interaction_repository.current_thread(
                task_id=int(task["id"]),
                actor_id=actor_id,
                chat_session_id=chat_session_id,
            )
            if current:
                thread_id = _text(current.get("video_thread_id"))
                parent_turn_id = parent_turn_id or _text(current.get("head_turn_id"))
        client_action_id = _text(payload.get("client_action_id"))
        if not client_action_id and agent_generation_id:
            client_action_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"opencrew:gemini-omni:{task['id']}:{chat_session_id}:{agent_generation_id}",
                )
            )
        if not client_action_id:
            raise HTTPException(status_code=400, detail={
                "code": "video_stateful_invalid_request",
                "message": "client_action_id is required for each paid Gemini Omni action",
            })
        if payload.get("stateful") is False:
            raise HTTPException(status_code=400, detail={
                "code": "gemini_omni_store_required",
                "message": "Gemini Omni video generation requires stored provider state",
            })
        try:
            interaction_claim = interaction_repository.create_or_replay_turn(
                task_id=int(task["id"]),
                session_id=int(task["session_id"]),
                actor_id=actor_id,
                operation=operation,
                client_action_id=client_action_id,
                model_alias=agent_video_alias or "Omni Flash",
                internal_provider="gemini",
                internal_model=GEMINI_OMNI_MODEL,
                prompt=prompt,
                thread_id=thread_id,
                parent_turn_id=parent_turn_id,
                input_asset_id=_text(payload.get("source_video_asset_id")) or (reference_video_rels[0] if reference_video_rels else ""),
                chat_session_id=chat_session_id,
                input_scope={
                    "aspect": aspect,
                    "duration": duration,
                    "effective_prompt": effective_prompt,
                    "reference_images": reference_rels,
                    "reference_videos": reference_video_rels,
                },
            )
        except VideoInteractionError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.as_detail()) from exc
        interaction_public = interaction_claim.public
        if interaction_claim.replayed:
            replay_asset: dict[str, Any] = {}
            replay_path = _text(interaction_claim.turn.get("output_path"))
            if interaction_claim.turn.get("status") == "completed" and replay_path:
                replay_asset = sc.uploaded_video_asset_payload(
                    replay_path,
                    "agent_generated",
                    "Gemini Omni video version",
                    {
                        "operation": interaction_claim.turn.get("operation"),
                        "video_thread_id": interaction_claim.turn.get("thread_id"),
                        "video_turn_id": interaction_claim.turn.get("turn_id"),
                        "parent_turn_id": interaction_claim.turn.get("parent_turn_id"),
                        "stateful": True,
                    },
                )
            return {
                "ok": interaction_claim.turn.get("status") not in {"failed", "cancelled"},
                "replayed": True,
                "pending": interaction_claim.turn.get("status") == "pending",
                "asset": replay_asset,
                "output": replay_path,
                **interaction_public,
            }
        interaction_repository.set_planned_output(interaction_claim.turn["turn_id"], output_rel)
        previous_interaction_id = ""
        if interaction_claim.turn.get("parent_turn_id"):
            try:
                parent_turn = interaction_repository.get_turn(
                    task_id=int(task["id"]),
                    actor_id=actor_id,
                    turn_id=interaction_claim.turn["parent_turn_id"],
                )
            except VideoInteractionError as exc:
                interaction_repository.fail_turn(interaction_claim.turn["turn_id"], lease_token=interaction_claim.lease_token)
                raise HTTPException(status_code=exc.status_code, detail=exc.as_detail()) from exc
            previous_interaction_id = _text(parent_turn.get("interaction_id"))
            if not previous_interaction_id or parent_turn.get("provider_state_status") != "available":
                interaction_repository.fail_turn(interaction_claim.turn["turn_id"], lease_token=interaction_claim.lease_token)
                raise HTTPException(status_code=409, detail={
                    "code": "gemini_omni_interaction_expired",
                    "message": "The selected provider context is unavailable; restart from the saved local video",
                })
        config = {
            **config,
            "_omni_turn_id": interaction_claim.turn["turn_id"],
            "_omni_parent_turn_id": interaction_claim.turn.get("parent_turn_id") or "",
            "_omni_previous_interaction_id": previous_interaction_id,
            "_omni_lease_token": interaction_claim.lease_token,
            "_omni_usage_request_id": interaction_claim.turn["usage_request_id"],
            "_omni_operation": interaction_claim.turn["operation"],
        }
    request_detail = {
        "request_id": request_id,
        "task_id": int(task["id"]),
        "session_id": int(task["session_id"]),
        "provider": config["provider"],
        "model": config["model"],
        "agent_video_alias": agent_video_alias,
        "duration": duration,
        "aspect": aspect,
        **aspect_meta,
        "reference_mode": _text(config.get("reference_mode") or payload.get("referenceMode")),
        "reference_images": reference_rels,
        "reference_audios": reference_audio_rels,
        "reference_videos": reference_video_rels,
        "reference_image_roles": reference_items,
        "reference_audio_roles": reference_audio_items,
        "reference_video_roles": reference_video_items,
        "reference_count": len(reference_paths) + len(reference_audio_paths) + len(reference_video_paths),
        "reference_image_count": len(reference_paths),
        "reference_audio_count": len(reference_audio_paths),
        "reference_video_count": len(reference_video_paths),
        "output": output_rel,
        "prompt_preview": prompt[:1000],
        "prompt_length": len(prompt),
        "effective_prompt_preview": effective_prompt[:1000],
        "effective_prompt_length": len(effective_prompt),
        "chat_opencode_session_id": chat_session_id,
        "prompt_builder_request_id": prompt_builder_request_id,
        "prompt_builder_applied_path": prompt_builder_applied_path,
        "agent_message_id": agent_message_id,
        "agent_generation_id": agent_generation_id,
    }
    if is_gemini_omni:
        request_detail = {
            key: value
            for key, value in request_detail.items()
            if key not in {"provider", "model", "prompt_preview", "effective_prompt_preview"}
        }
        request_detail.update({
            "stateful": True,
            "operation": interaction_claim.turn["operation"],
            **interaction_public,
        })
    sc.add_event(int(task["session_id"]), "koubo_storyboard.asset_library_agent.video.started", request_detail)
    try:
        provider_result = run_asset_library_video_provider(task, request_detail, effective_prompt, config, output_rel, reference_paths, reference_audio_paths, reference_video_paths, duration, aspect, sc=sc)
    except GeminiOmniError as exc:
        if interaction_repository and interaction_claim:
            current_turn = interaction_repository.get_turn(
                task_id=int(task["id"]),
                actor_id=video_interaction_actor_id(task),
                turn_id=interaction_claim.turn["turn_id"],
            )
            if current_turn.get("interaction_id"):
                interaction_repository.release_lease(interaction_claim.turn["turn_id"], lease_token=interaction_claim.lease_token)
            elif exc.status_code >= 500:
                interaction_repository.mark_provider_result_unknown(interaction_claim.turn["turn_id"])
            else:
                interaction_repository.fail_turn(interaction_claim.turn["turn_id"], lease_token=interaction_claim.lease_token)
        (workspace / output_rel).unlink(missing_ok=True)
        raise exc.as_http_exception() from exc
    except Exception:
        if interaction_repository and interaction_claim:
            interaction_repository.fail_turn(interaction_claim.turn["turn_id"], lease_token=interaction_claim.lease_token)
        (workspace / output_rel).unlink(missing_ok=True)
        raise
    request_path = workspace / ASSET_VIDEOS_REL / f"{Path(output_name).stem}.json"
    sc.write_json(request_path, {**request_detail, "prompt": prompt, "effective_prompt": effective_prompt, "provider_result": provider_result, "generated_at": now_ms()})
    public_origin_provider = {} if is_gemini_omni else {"provider": config["provider"], "model": config["model"]}
    asset = uploaded_video_asset_payload(output_rel, "agent_generated", "Agent generated video", {
        "duration": provider_result.get("effective_duration_seconds"),
        "duration_seconds": provider_result.get("effective_duration_seconds"),
        "aspect": aspect,
        "width": provider_result.get("output_width"),
        "height": provider_result.get("output_height"),
        "origin": {
            "tool": "upload_asset_library_video_agent",
            "request_id": request_id,
            "prompt": prompt,
            "effective_prompt": effective_prompt,
            **public_origin_provider,
            "duration": provider_result.get("effective_duration_seconds"),
            "aspect": aspect,
            **aspect_meta,
            "reference_images": reference_rels,
            "reference_audios": reference_audio_rels,
            "reference_videos": reference_video_rels,
            "reference_image_roles": reference_items,
            "reference_audio_roles": reference_audio_items,
            "reference_video_roles": reference_video_items,
            "reference_mode": request_detail.get("reference_mode"),
            "portrait_reframe": provider_result.get("portrait_reframe") if isinstance(provider_result.get("portrait_reframe"), dict) else {},
            "request_path": request_path.relative_to(workspace).as_posix(),
            "chat_opencode_session_id": chat_session_id,
            "prompt_builder_request_id": prompt_builder_request_id,
            "prompt_builder_applied_path": prompt_builder_applied_path,
            "agent_message_id": agent_message_id,
            "agent_generation_id": agent_generation_id,
            **({
                "operation": interaction_claim.turn["operation"],
                "video_thread_id": interaction_claim.turn["thread_id"],
                "video_turn_id": interaction_claim.turn["turn_id"],
                "parent_turn_id": interaction_claim.turn.get("parent_turn_id"),
                "source_asset_id": interaction_claim.turn.get("input_asset_id"),
                "stateful": True,
            } if is_gemini_omni else {}),
        },
        **({
            "operation": interaction_claim.turn["operation"],
            "video_thread_id": interaction_claim.turn["thread_id"],
            "video_turn_id": interaction_claim.turn["turn_id"],
            "parent_turn_id": interaction_claim.turn.get("parent_turn_id"),
            "source_asset_id": interaction_claim.turn.get("input_asset_id"),
            "stateful": True,
        } if is_gemini_omni else {}),
    })
    sc.upsert_asset_manifest_item(workspace, asset, sc=sc)
    if interaction_repository and interaction_claim:
        try:
            completed_turn = interaction_repository.complete_turn(
                interaction_claim.turn["turn_id"],
                lease_token=interaction_claim.lease_token,
                output_asset_id=_text(asset.get("id")),
                output_path=output_rel,
                local_usage_id=_text(provider_result.get("local_usage_id")),
            )
        except VideoInteractionError as exc:
            interaction_repository.fail_turn(interaction_claim.turn["turn_id"], lease_token=interaction_claim.lease_token)
            raise HTTPException(status_code=exc.status_code, detail=exc.as_detail()) from exc
        interaction_public = public_video_interaction_turn(completed_turn)
    result = {"ok": True, **request_detail, "asset": asset, "provider_result": provider_result}
    if interaction_public:
        result.update(interaction_public)
    try:
        plan, meta = sc.load_plan(task, sc=sc)
        result.update({"task": task, "meta": meta, "plan": plan})
    except Exception:
        result["task"] = task
    sc.add_event(int(task["session_id"]), "koubo_storyboard.asset_library_agent.video.generated", result)
    return result


def register_asset_video_generation_services(ns: Any) -> None:
    for name in SERVICE_EXPORTS:
        setattr(ns, name, globals()[name])
