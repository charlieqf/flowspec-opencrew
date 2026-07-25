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
from opcrew_backend.model_policy import SURFACE_KOUBO_HOST_PRODUCT_PROMPT, mask_model_fields_for_role, mask_prompt_models_for_role
from opcrew_backend.routes.media_model_config import CONFIG_TABLE, ensure_table
from opcrew_backend.services.media_sanitize import write_sanitized_image_bytes

from .constants import *
from .io_utils import read_json, safe_workspace_rel, write_json
from .runtime import analysis_tool_env
from .text_utils import redact_payload, redact_secret_text
from .usage_metering import chat_usage_units, image_usage_units, record_storyboard_usage, stable_usage_request_id


SERVICE_EXPORTS = (
    "serialize_host_product_builder",
    "parse_builder_prompt_response",
    "generate_host_product_final_prompt",
    "generate_host_product_image",
)


def serialize_host_product_builder(task: dict[str, Any], role: str = "admin", *, sc: Any) -> dict[str, Any]:
    workspace = sc.workspace_for(task)
    session_row = sc.safe_session(int(task["session_id"]))
    prompt_models = sc.prompt_models_with_task_run_model(sc.safe_prompt_models(session_row, sc=sc), task)
    payload = {
        "ok": True,
        "task_id": int(task["id"]),
        "session_id": int(task["session_id"]),
        "workspace_dir": str(workspace),
        "guide_path": str(CONSISTENCY_REFERENCE_GUIDE_PATH),
        "image_model": sc.active_image_model_public(sc=sc),
        "run_model": sc.task_run_model(task),
        "prompt_models": mask_prompt_models_for_role(sc.ctx, role, SURFACE_KOUBO_HOST_PRODUCT_PROMPT, prompt_models),
        "config": sc.read_builder_config(workspace, sc=sc),
        "host": sc.read_builder_section(workspace, "host", sc=sc),
        "product": sc.read_builder_section(workspace, "product", sc=sc),
    }
    return mask_model_fields_for_role(sc.ctx, role, SURFACE_KOUBO_HOST_PRODUCT_PROMPT, payload)


def parse_builder_prompt_response(value: str, request_id: str, *, sc: Any) -> str:
    raw = str(value or "").strip()
    provider_error = sc.provider_error_page_detail(raw)
    if provider_error:
        raise HTTPException(status_code=502, detail=provider_error)
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I).strip()
        raw = re.sub(r"\s*```$", "", raw).strip()
    match = re.search(r"\{[\s\S]*\}", raw)
    if match:
        raw = match.group(0)
    try:
        parsed = json.loads(raw)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Run Model returned non-JSON Host & Product Builder output") from exc
    final_prompt = str((parsed or {}).get("prompt") or "").strip() if isinstance(parsed, dict) else ""
    returned_request_id = str((parsed or {}).get("request_id") or "").strip() if isinstance(parsed, dict) else ""
    provider_error = sc.provider_error_page_detail(final_prompt)
    if provider_error:
        raise HTTPException(status_code=502, detail=provider_error)
    if returned_request_id != request_id or len(final_prompt) < 120:
        raise HTTPException(status_code=400, detail="Run Model Host & Product Builder output failed request_id or prompt validation")
    return final_prompt


def generate_host_product_final_prompt(task_id: int, payload: dict[str, Any], role: str = "admin", *, sc: Any) -> dict[str, Any]:
    task = sc.task_or_404(task_id)
    workspace = sc.workspace_for(task)
    session_id = int(task["session_id"])
    session_row = sc.safe_session(session_id)
    kind = sc.builder_kind_dir(str(payload.get("kind") or ""))
    simple_prompt = sc.text(payload.get("simple_prompt"))
    refs = [str(item).strip() for item in payload.get("reference_images") or [] if str(item).strip()]
    provider = sc.text(payload.get("provider"))
    model_id = sc.text(payload.get("model"))
    request_id = f"koubo_host_product_{kind}_{now_ms()}_{uuid.uuid4().hex[:8]}"
    requested = {"request_id": request_id, "task_id": task_id, "session_id": session_id, "kind": kind, "reference_count": len(refs), "writes_workspace": True}
    sc.add_event(session_id, "koubo_storyboard.host_product_builder.prompt.requested", {**requested, "simple_prompt_preview": simple_prompt[:1000]})
    model, _prompt_models = sc.resolve_model(session_row, provider, model_id, role, sc=sc)
    try:
        plan, _meta = sc.load_plan(task, sc=sc)
        plan_text = "\n".join([str(dialogue.get("text") or "") for shot in plan.get("shots") or [] for scene in shot.get("scenes") or [] for dialogue in scene.get("dialogues") or [] if isinstance(dialogue, dict)])[:2000]
    except Exception:
        plan_text = ""
    user_content = f"""
Request ID:
{request_id}

Builder kind:
{kind}

Task context:
- Task #{task_id}
- Session #{session_id}
- Current storyboard spoken text preview: {plan_text}

Uploaded reference images in this Session workspace:
{sc.builder_reference_summary(workspace, refs, sc=sc)}

User simple prompt:
{simple_prompt}

Consistency guide:
{sc.read_consistency_guide()}

Return strict JSON only with this exact request_id and the final image generation prompt:
{{"request_id":"{request_id}","prompt":"..."}}
""".strip()
    client = sc.opencode_client_for(session_row, sc=sc)
    started_at = now_ms()
    client.prompt_async(str(session_row["opencode_session_id"]), user_content, model=model, system=HOST_PRODUCT_BUILDER_SYSTEM_PROMPT)
    timeout_seconds = HOST_PRODUCT_PROMPT_TIMEOUT_SECONDS
    deadline = time.time() + timeout_seconds
    assistant_text = None
    while time.time() < deadline:
        assistant_text = sc.last_completed_assistant(client.messages(str(session_row["opencode_session_id"]), limit=120), started_at)
        if assistant_text:
            break
        time.sleep(1)
    if not assistant_text:
        failed = {**requested, "run_model_provider": model["providerID"], "run_model_id": model["modelID"], "timeout_seconds": timeout_seconds, "detail": f"Run Model timed out after {timeout_seconds} seconds before returning Host & Product Builder prompt"}
        sc.add_event(session_id, "koubo_storyboard.host_product_builder.prompt.failed", failed)
        raise HTTPException(status_code=400, detail=failed["detail"])
    try:
        final_prompt = parse_builder_prompt_response(assistant_text, request_id, sc=sc)
    except HTTPException as exc:
        sc.add_event(session_id, "koubo_storyboard.host_product_builder.prompt.failed", {**requested, "run_model_provider": model["providerID"], "run_model_id": model["modelID"], "detail": exc.detail})
        raise
    simple_path = sc.builder_prompt_path(workspace, kind, "simple_prompt.txt")
    final_path = sc.builder_prompt_path(workspace, kind, "final_prompt.txt")
    simple_path.parent.mkdir(parents=True, exist_ok=True)
    simple_path.write_text(simple_prompt, encoding="utf-8")
    final_path.write_text(final_prompt, encoding="utf-8")
    section = sc.write_builder_section(workspace, kind, {
        "simple_prompt": simple_prompt,
        "final_prompt": final_prompt,
        "reference_images": refs,
        "simple_prompt_path": sc.builder_rel(workspace, simple_path),
        "final_prompt_path": sc.builder_rel(workspace, final_path),
        "run_model_provider": model["providerID"],
        "run_model_id": model["modelID"],
    }, sc=sc)
    local_usage = record_storyboard_usage(
        sc.ctx,
        task,
        request_id=request_id,
        provider=model["providerID"],
        model_id=model["modelID"],
        modality="chat",
        step_id="koubo_storyboard.host_product_builder.prompt",
        units=chat_usage_units(input_text=user_content, output_text=assistant_text, system_text=HOST_PRODUCT_BUILDER_SYSTEM_PROMPT),
        started_at=started_at,
        finished_at=now_ms(),
    )
    config = sc.read_builder_config(workspace, sc=sc)
    config["task_id"] = task_id
    config["session_id"] = session_id
    config["updated_at"] = now_ms()
    sc.write_json(sc.builder_config_path(workspace), config)
    result = {**requested, "ok": True, "run_model_provider": model["providerID"], "run_model_id": model["modelID"], "prompt": final_prompt, "section": section, "state": serialize_host_product_builder(task, role, sc=sc), "local_usage": local_usage, "local_usage_id": local_usage.get("local_usage_id", "")}
    sc.add_event(session_id, "koubo_storyboard.host_product_builder.prompt.completed", {**result, "prompt_preview": final_prompt[:1000], "prompt_length": len(final_prompt)})
    return mask_model_fields_for_role(sc.ctx, role, SURFACE_KOUBO_HOST_PRODUCT_PROMPT, result)


def generate_host_product_image(task_id: int, payload: dict[str, Any], *, sc: Any) -> dict[str, Any]:
    task = sc.task_or_404(task_id)
    workspace = sc.workspace_for(task)
    session_id = int(task["session_id"])
    kind = sc.builder_kind_dir(str(payload.get("kind") or ""))
    prompt = sc.text(payload.get("prompt"))
    if len(prompt) < 20:
        raise HTTPException(status_code=400, detail="prompt is required")
    refs = [str(item).strip() for item in payload.get("reference_images") or [] if str(item).strip()]
    reference_paths = [sc.safe_workspace_rel(workspace, rel)[1] for rel in refs]
    reference_paths = [path for path in reference_paths if path.exists() and path.is_file()]
    if reference_paths:
        config, reference_image_provider_fallback_from = sc.load_reference_image_config(sc.text(payload.get("provider")), sc.text(payload.get("model")), sc=sc)
    else:
        config = sc.load_image_config(sc.text(payload.get("provider")), sc.text(payload.get("model")), sc=sc)
        reference_image_provider_fallback_from = ""
    output_path = sc.final_output_path_for_write(workspace, kind, sc=sc)
    request_params_path = sc.builder_request_params_path(workspace, kind)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    call_started = time.time()
    api_call_id = f"koubo_host_product_{kind}_image_{now_ms()}_{uuid.uuid4().hex[:8]}"
    call_detail = {
        "api_call_id": api_call_id,
        "task_id": task_id,
        "session_id": session_id,
        "kind": kind,
        "provider": config["provider"],
        "model": config["model"],
        "endpoint": sc.image_provider_endpoint(config["provider"], config["model"], reference_paths[0] if reference_paths else None),
        "reference_images": refs,
        "reference_count": len(reference_paths),
        "workspace_dir": str(workspace),
        "output": sc.builder_rel(workspace, output_path),
        "output_path": str(output_path),
        "prompt_preview": prompt[:1000],
        "prompt_length": len(prompt),
    }
    if reference_image_provider_fallback_from:
        call_detail["reference_image_provider_fallback_from"] = reference_image_provider_fallback_from
    sc.write_json(request_params_path, {**call_detail, "prompt": prompt})
    sc.add_event(session_id, "koubo_storyboard.host_product_builder.image.provider_call.started", call_detail)
    image_bytes = sc.generate_image_bytes(config, prompt, reference_paths or None, sc=sc)
    write_sanitized_image_bytes(output_path, image_bytes)
    usage_request_id = stable_usage_request_id("koubo_host_product_image", task_id, kind, output_path.name, config["provider"], config["model"])
    local_usage = record_storyboard_usage(
        sc.ctx,
        task,
        request_id=usage_request_id,
        provider=config["provider"],
        model_id=config["model"],
        modality="image",
        step_id="koubo_storyboard.host_product_builder.image",
        units=image_usage_units(count=1, prompt=prompt, reference_count=len(reference_paths)),
        started_at=call_started,
        finished_at=time.time(),
    )
    output_rel = sc.builder_rel(workspace, output_path)
    final_prompt_path = sc.builder_prompt_path(workspace, kind, "final_prompt.txt")
    if not final_prompt_path.exists():
        final_prompt_path.parent.mkdir(parents=True, exist_ok=True)
        final_prompt_path.write_text(prompt, encoding="utf-8")
    section = sc.write_builder_section(workspace, kind, {
        "final_prompt": prompt,
        "final_prompt_path": sc.builder_rel(workspace, final_prompt_path),
        "reference_images": refs,
        "output": output_rel,
        "output_path": str(output_path),
        "provider": config["provider"],
        "model": config["model"],
        "generated_at": now_ms(),
        "last_request_params_path": sc.builder_rel(workspace, request_params_path),
        "local_usage": local_usage,
        "local_usage_id": local_usage.get("local_usage_id", ""),
    }, sc=sc)
    current_config = sc.read_builder_config(workspace, sc=sc)
    active = current_config.get("active") if isinstance(current_config.get("active"), dict) else {}
    active["host_reference" if kind == "host" else "product_reference"] = output_rel
    current_config.update({"task_id": task_id, "session_id": session_id, "active": active, "image_model": {"provider": config["provider"], "model": config["model"]}, "updated_at": now_ms()})
    sc.write_json(sc.builder_config_path(workspace), current_config)
    result = {**call_detail, "ok": True, "elapsed_seconds": round(time.time() - call_started, 3), "output": output_rel, "section": section, "state": serialize_host_product_builder(task, sc=sc), "local_usage": local_usage, "local_usage_id": local_usage.get("local_usage_id", "")}
    sc.add_event(session_id, "koubo_storyboard.host_product_builder.image.generated", result)
    return result


def register_host_product_services(ns: Any) -> None:
    for name in SERVICE_EXPORTS:
        setattr(ns, name, globals()[name])
