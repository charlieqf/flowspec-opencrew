from __future__ import annotations

import asyncio
import base64
import copy
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
from opcrew_backend.model_policy import SURFACE_KOUBO_HOST_PRODUCT_PROMPT, resolve_prompt_model_for_role
from opcrew_backend.routes.media_model_config import CONFIG_TABLE, ensure_table, load_stored_key
from opcrew_backend.services.media_sanitize import MediaSanitizeError, sanitize_audio_file_metadata

from .constants import *
from .io_utils import read_json, safe_workspace_rel, write_json
from .runtime import analysis_tool_env
from .text_utils import redact_payload, redact_secret_text


PROMPT_MODELS_CACHE_TTL_SECONDS = 45.0
_prompt_models_cache_lock = threading.Lock()
_prompt_models_cache: dict[tuple[str, str, str], tuple[float, dict[str, Any]]] = {}

SERVICE_EXPORTS = (
    "provider_error_page_detail",
    "opencode_client_for",
    "serialize_prompt_models",
    "safe_prompt_models",
    "task_run_model",
    "prompt_models_with_task_run_model",
    "resolve_model",
    "last_completed_assistant",
    "load_active_image_selection",
    "load_active_image_config",
    "load_image_config",
    "image_provider_supports_references",
    "load_reference_image_config",
    "active_image_model_public",
    "image_b64_from_response",
    "image_url_from_response",
    "image_data_uri",
    "aspect_ratio_from_size",
    "image_bytes_from_xai_response",
    "image_provider_endpoint",
    "provider_urlopen",
    "post_json_request",
    "post_binary_request",
    "post_multipart_request",
    "supported_xai_aspect_ratio",
    "generate_image_bytes",
    "download_binary",
)


def provider_error_page_detail(value: str) -> str:
    text_value = str(value or "").strip()
    lowered = text_value[:8000].lower()
    if "<!doctype html" not in lowered and "<html" not in lowered:
        return ""
    if "error code 524" in lowered or "a timeout occurred" in lowered:
        return "Run Model returned a Cloudflare 524 timeout page. Retry after the OpenCode/provider tunnel recovers."
    if "cloudflare" in lowered or "5xx-error-landing" in lowered:
        return "Run Model returned a Cloudflare error page. Retry after the OpenCode/provider tunnel recovers."
    return "Run Model returned an HTML error page instead of model output."


def opencode_client_for(session_row: dict[str, Any], *, sc: Any) -> OpenCodeSessionClient:
    base_url = str(sc.ctx.get_setting("opencode.base_url") or "").strip()
    username = str(sc.ctx.get_setting("opencode.username") or "").strip()
    password = str(sc.ctx.get_setting("opencode.password") or "").strip()
    if not base_url or not username or not password:
        raise HTTPException(status_code=400, detail="OpenCode connection is incomplete. Finish Connection before using Host & Product Builder.")
    return OpenCodeSessionClient(base_url=base_url, username=username, password=password, directory=str(session_row["workspace_dir"]))


def _prompt_models_cache_key(session_row: dict[str, Any], *, sc: Any) -> tuple[str, str, str]:
    return (
        str(sc.ctx.get_setting("opencode.base_url") or "").strip(),
        str(sc.ctx.get_setting("opencode.username") or "").strip(),
        str(session_row.get("workspace_dir") or "").strip(),
    )


def _cached_prompt_models(cache_key: tuple[str, str, str], *, allow_stale: bool = False) -> dict[str, Any] | None:
    with _prompt_models_cache_lock:
        cached = _prompt_models_cache.get(cache_key)
    if not cached:
        return None
    cached_at, payload = cached
    if allow_stale or time.monotonic() - cached_at <= PROMPT_MODELS_CACHE_TTL_SECONDS:
        return copy.deepcopy(payload)
    return None


def _write_prompt_models_cache(cache_key: tuple[str, str, str], payload: dict[str, Any]) -> None:
    with _prompt_models_cache_lock:
        _prompt_models_cache[cache_key] = (time.monotonic(), copy.deepcopy(payload))


def serialize_prompt_models(session_row: dict[str, Any], *, sc: Any) -> dict[str, Any]:
    cache_key = _prompt_models_cache_key(session_row, sc=sc)
    cached = _cached_prompt_models(cache_key)
    if cached is not None:
        return cached
    try:
        provider_payload = opencode_client_for(session_row, sc=sc).providers(timeout=12)
    except Exception:
        stale = _cached_prompt_models(cache_key, allow_stale=True)
        if stale is not None:
            return stale
        raise
    connected = {str(item) for item in (provider_payload.get("connected") or []) if item}
    default_map = provider_payload.get("default") or {}
    items: list[dict[str, Any]] = []
    default_model = {"providerID": "", "modelID": ""}
    for provider in provider_payload.get("all") or []:
        provider_id = str(provider.get("id") or "").strip()
        if not provider_id or provider_id not in connected:
            continue
        for model in (provider.get("models") or {}).values():
            model_id = str((model or {}).get("id") or "").strip()
            if not model_id:
                continue
            items.append({
                "providerID": provider_id,
                "providerName": str(provider.get("name") or provider_id),
                "modelID": model_id,
                "modelName": str((model or {}).get("name") or model_id),
                "reasoning": bool((model or {}).get("reasoning")),
                "contextLimit": int((((model or {}).get("limit") or {}).get("context") or 0) or 0),
                "inputModalities": list((((model or {}).get("modalities") or {}).get("input") or [])),
            })
        configured_default = str(default_map.get(provider_id) or "").strip()
        if configured_default and not default_model["providerID"]:
            default_model = {"providerID": provider_id, "modelID": configured_default}
    items.sort(key=lambda item: (str(item["providerName"]), str(item["modelName"])))
    preferred = next((item for item in items if item["providerID"] == "openai" and item["modelID"] in {"gpt-5.5", "gpt-5.1"}), None)
    if preferred:
        default_model = {"providerID": preferred["providerID"], "modelID": preferred["modelID"]}
    elif not default_model["providerID"] and items:
        default_model = {"providerID": str(items[0]["providerID"]), "modelID": str(items[0]["modelID"])}
    payload = {"items": items, "default_model": default_model}
    _write_prompt_models_cache(cache_key, payload)
    return payload


def safe_prompt_models(session_row: dict[str, Any], *, sc: Any) -> dict[str, Any]:
    try:
        return serialize_prompt_models(session_row, sc=sc)
    except HTTPException as exc:
        return {"items": [], "default_model": {"providerID": "", "modelID": ""}, "error": str(exc.detail)}
    except Exception as exc:
        return {"items": [], "default_model": {"providerID": "", "modelID": ""}, "error": str(exc)}


def task_run_model(task: dict[str, Any]) -> dict[str, str]:
    return {
        "providerID": str(task.get("run_model_provider") or "").strip(),
        "modelID": str(task.get("run_model_id") or "").strip(),
    }


def prompt_models_with_task_run_model(prompt_models: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
    run_model = task_run_model(task)
    provider_id = run_model["providerID"]
    model_id = run_model["modelID"]
    if not provider_id or not model_id:
        return prompt_models
    items = list(prompt_models.get("items") or [])
    if not any(str(item.get("providerID")) == provider_id and str(item.get("modelID")) == model_id for item in items):
        items.append({
            "providerID": provider_id,
            "providerName": provider_id,
            "modelID": model_id,
            "modelName": model_id,
            "reasoning": False,
            "contextLimit": 0,
            "inputModalities": [],
            "source": "task_run_model",
        })
    return {
        **prompt_models,
        "items": items,
        "default_model": run_model,
        "task_run_model": run_model,
    }


def resolve_model(
    session_row: dict[str, Any],
    provider: str,
    model_id: str,
    role: str = "admin",
    surface: str = SURFACE_KOUBO_HOST_PRODUCT_PROMPT, *, sc: Any,
) -> tuple[dict[str, str], dict[str, Any]]:
    payload = serialize_prompt_models(session_row, sc=sc)
    return resolve_prompt_model_for_role(sc.ctx, role, surface, payload, provider, model_id, "Prompt")


def last_completed_assistant(messages: list[dict[str, Any]], started_after: int) -> str | None:
    for message in reversed(messages or []):
        info = message.get("info") if isinstance(message.get("info"), dict) else {}
        role = str(info.get("role") or message.get("role") or "")
        if role != "assistant":
            continue
        time_info = info.get("time") if isinstance(info.get("time"), dict) else {}
        top_level_time = message.get("time") if isinstance(message.get("time"), dict) else {}
        completed = int((time_info.get("completed") or top_level_time.get("completed") or time_info.get("created") or top_level_time.get("created") or 0) or 0)
        if completed < started_after:
            continue
        parts = message.get("parts") if isinstance(message.get("parts"), list) else []
        text_parts = [str(part.get("text") or "") for part in parts if isinstance(part, dict) and part.get("type") == "text" and str(part.get("text") or "").strip()]
        if text_parts:
            return "\n".join(text_parts).strip()
        content = str(message.get("content") or "").strip()
        if content:
            return content
    return None


def load_active_image_selection(*, sc: Any) -> dict[str, str]:
    ensure_table(sc.ctx)
    with sc.ctx.engine.begin() as conn:
        row = conn.execute(sql_text(f"""
SELECT provider, model FROM {CONFIG_TABLE}
WHERE kind = 'image' AND active = TRUE AND enabled = TRUE
LIMIT 1
""")).first()
    if not row:
        raise HTTPException(status_code=400, detail="No active image model is configured in Connection")
    mapping = row._mapping
    provider = str(mapping.get("provider") or "").strip()
    model = str(mapping.get("model") or "").strip()
    return {"provider": provider, "model": model}


def load_active_image_config(*, sc: Any) -> dict[str, str]:
    selection = load_active_image_selection(sc=sc)
    provider = selection["provider"]
    model = selection["model"]
    api_key = load_stored_key(sc.ctx, "image", provider)
    if not api_key:
        raise HTTPException(status_code=400, detail=f"Active image model API key is missing in Connection: {provider}/{model}")
    return {"provider": provider, "model": model, "api_key": api_key}


def load_image_config(provider: str, model: str, *, sc: Any) -> dict[str, str]:
    provider = str(provider or "").strip()
    if not provider:
        return load_active_image_config(sc=sc)
    ensure_table(sc.ctx)
    with sc.ctx.engine.begin() as conn:
        row = conn.execute(sql_text(f"""
SELECT provider, model FROM {CONFIG_TABLE}
WHERE kind = 'image' AND provider = :provider AND enabled = TRUE
LIMIT 1
"""), {"provider": provider}).first()
    if not row:
        raise HTTPException(status_code=400, detail=f"Image provider is not configured or enabled: {provider}")
    mapping = row._mapping
    row_provider = str(mapping.get("provider") or provider).strip()
    stored_model = str(mapping.get("model") or "").strip()
    selected_model = str(model or "").strip() or stored_model
    api_key = load_stored_key(sc.ctx, "image", row_provider)
    if not api_key:
        raise HTTPException(status_code=400, detail=f"Image provider API key is missing in Connection: {row_provider}/{selected_model}")
    return {"provider": row_provider, "model": selected_model, "api_key": api_key}


def image_provider_supports_references(provider: str) -> bool:
    return str(provider or "").strip().lower() in {"openai", "gemini", "xai"}


def load_reference_image_config(provider: str, model: str, *, sc: Any) -> tuple[dict[str, str], str]:
    selected_provider = str(provider or "").strip()
    selected_model = str(model or "").strip()
    fallback_from = ""
    if not selected_provider:
        try:
            selection = load_active_image_selection(sc=sc)
            selected_provider = selection["provider"]
            selected_model = selection["model"]
        except HTTPException:
            selected_provider = ""
            selected_model = ""
    if selected_provider:
        fallback_from = f"{selected_provider}/{selected_model}".rstrip("/")
    candidates: list[tuple[str, str]] = []
    if selected_provider and image_provider_supports_references(selected_provider):
        candidates.append((selected_provider, selected_model))
    for fallback_provider in ("openai", "gemini"):
        if not any(candidate_provider.lower() == fallback_provider for candidate_provider, _ in candidates):
            candidates.append((fallback_provider, ""))
    errors: list[str] = []
    for candidate_provider, candidate_model in candidates:
        try:
            config = load_image_config(candidate_provider, candidate_model, sc=sc)
        except HTTPException as exc:
            errors.append(f"{candidate_provider}: {exc.detail}")
            continue
        if image_provider_supports_references(config["provider"]):
            actual = f"{config['provider']}/{config['model']}".rstrip("/")
            return config, "" if not fallback_from or actual == fallback_from else fallback_from
    error_suffix = f" Tried: {'; '.join(errors)}" if errors else ""
    selected_label = fallback_from or "none"
    raise HTTPException(status_code=400, detail=f"Reference image generation requires an xAI, OpenAI, or Gemini image provider with API key in Connection. Selected image model is {selected_label}.{error_suffix}")


def active_image_model_public(*, sc: Any) -> dict[str, str]:
    try:
        config = load_active_image_config(sc=sc)
        return {"provider": config["provider"], "model": config["model"]}
    except HTTPException as exc:
        return {"provider": "", "model": "", "error": str(exc.detail)}


def image_b64_from_response(provider: str, payload: dict[str, Any]) -> str:
    if provider in {"openai", "xai"}:
        item = next((entry for entry in payload.get("data") or [] if entry.get("b64_json")), None)
        if item:
            return str(item["b64_json"])
    if provider == "gemini":
        for candidate in payload.get("candidates") or []:
            for part in (((candidate.get("content") or {}).get("parts")) or []):
                inline = part.get("inlineData") or part.get("inline_data") or {}
                if inline.get("data"):
                    return str(inline["data"])
    raise HTTPException(status_code=502, detail="Image provider response did not include image data")


def image_url_from_response(provider: str, payload: dict[str, Any]) -> str:
    if provider in {"openai", "xai"}:
        item = next((entry for entry in payload.get("data") or [] if entry.get("url")), None)
        if item:
            return str(item["url"])
    raise HTTPException(status_code=502, detail="Image provider response did not include image URL")


def image_data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def aspect_ratio_from_size(size: str) -> str:
    normalized = str(size or "").strip().lower()
    if normalized == "1024x1536":
        return "9:16"
    if normalized == "1536x1024":
        return "16:9"
    parts = str(size or "").lower().split("x", 1)
    try:
        width = int(parts[0])
        height = int(parts[1])
    except Exception:
        return ""
    if width <= 0 or height <= 0:
        return ""
    ratio = width / height
    options = {
        "16:9": 16 / 9,
        "9:16": 9 / 16,
        "4:3": 4 / 3,
        "3:4": 3 / 4,
        "1:1": 1,
    }
    return min(options, key=lambda item: abs(options[item] - ratio))


def image_bytes_from_xai_response(payload: dict[str, Any], *, sc: Any) -> bytes:
    try:
        return base64.b64decode(image_b64_from_response("xai", payload))
    except HTTPException:
        url = image_url_from_response("xai", payload)
        try:
            with sc.provider_urlopen(urllib.request.Request(url), timeout=600) as res:
                return res.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:2000]
            raise HTTPException(status_code=502, detail=f"Image provider output download failed: HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise HTTPException(status_code=502, detail=f"Image provider output download failed: {exc.reason}") from exc


def image_provider_endpoint(provider: str, model: str, reference_path: Path | None) -> str:
    if provider == "openai":
        return "https://api.openai.com/v1/images/edits" if reference_path else "https://api.openai.com/v1/images/generations"
    if provider == "xai":
        return "https://api.x.ai/v1/images/generations"
    if provider == "gemini":
        return f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(model, safe='')}:generateContent"
    return ""


def provider_urlopen(req: urllib.request.Request | str, timeout: int = 120) -> Any:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return opener.open(req, timeout=timeout)


def _json_from_detail(detail: str) -> dict[str, Any]:
    try:
        parsed = json.loads(str(detail or ""))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _http_retry_after_seconds(exc: urllib.error.HTTPError | None, detail: str, attempt: int) -> float:
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
    return min(10.0, float(2 ** attempt))


def _retryable_http_error(exc: urllib.error.HTTPError, detail: str) -> bool:
    if int(getattr(exc, "code", 0) or 0) in {429, 500, 502, 503, 504, 520, 522, 523, 524}:
        return True
    payload = _json_from_detail(detail)
    return payload.get("retryable") is True or payload.get("cloudflare_error") is True


def _retryable_url_error(exc: urllib.error.URLError) -> bool:
    reason = getattr(exc, "reason", exc)
    lowered = str(reason or "").lower()
    return isinstance(reason, TimeoutError) or "timed out" in lowered or "temporarily unavailable" in lowered or "connection reset" in lowered


def _read_json_request_with_retries(
    request_factory: Any,
    error_prefix: str,
    timeout: int,
    attempts: int = 2,
) -> dict[str, Any]:
    for attempt in range(1, max(1, attempts) + 1):
        try:
            with provider_urlopen(request_factory(), timeout=timeout) as res:
                body = res.read().decode("utf-8", errors="replace")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:2000]
            if attempt < attempts and _retryable_http_error(exc, detail):
                time.sleep(_http_retry_after_seconds(exc, detail, attempt))
                continue
            suffix = f" (after {attempt} attempts)" if attempt > 1 else ""
            raise HTTPException(status_code=502, detail=f"{error_prefix} request failed: HTTP {exc.code}: {detail}{suffix}") from exc
        except urllib.error.URLError as exc:
            if attempt < attempts and _retryable_url_error(exc):
                time.sleep(_http_retry_after_seconds(None, "", attempt))
                continue
            suffix = f" (after {attempt} attempts)" if attempt > 1 else ""
            raise HTTPException(status_code=502, detail=f"{error_prefix} request failed: {exc.reason}{suffix}") from exc
        except TimeoutError as exc:
            if attempt < attempts:
                time.sleep(_http_retry_after_seconds(None, "", attempt))
                continue
            suffix = f" (after {attempt} attempts)" if attempt > 1 else ""
            raise HTTPException(status_code=502, detail=f"{error_prefix} request failed: {exc}{suffix}") from exc
    raise HTTPException(status_code=502, detail=f"{error_prefix} request failed")


def post_json_request(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int = 120, error_prefix: str = "Image provider") -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def request_factory() -> urllib.request.Request:
        return urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", "Accept": "application/json", **headers}, method="POST")

    return _read_json_request_with_retries(request_factory, error_prefix or "Provider", timeout)


def post_binary_request(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int = 120) -> tuple[bytes, str]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", "Accept": "*/*", **headers}, method="POST")
    try:
        with provider_urlopen(req, timeout=timeout) as res:
            return res.read(), str(res.headers.get("Content-Type") or "application/octet-stream")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2000]
        raise HTTPException(status_code=502, detail=f"TTS provider request failed: HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise HTTPException(status_code=502, detail=f"TTS provider request failed: {exc.reason}") from exc


def post_multipart_request(url: str, fields: dict[str, str], files: list[tuple[str, Path]], headers: dict[str, str], timeout: int = 120) -> dict[str, Any]:
    boundary = f"----OpenCrewKoubo{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend([f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(), str(value).encode("utf-8"), b"\r\n"])
    for name, path in files:
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        chunks.extend([f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="{name}"; filename="{path.name}"\r\n'.encode(), f"Content-Type: {mime}\r\n\r\n".encode(), path.read_bytes(), b"\r\n"])
    chunks.append(f"--{boundary}--\r\n".encode())
    body = b"".join(chunks)

    def request_factory() -> urllib.request.Request:
        return urllib.request.Request(url, data=body, headers={"Accept": "application/json", "Content-Type": f"multipart/form-data; boundary={boundary}", **headers}, method="POST")

    return _read_json_request_with_retries(request_factory, "Image provider", timeout)


def supported_xai_aspect_ratio(aspect: str, size: str) -> str:
    requested = str(aspect or "").strip()
    supported = {"1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "2:1", "1:2", "19.5:9", "9:19.5", "20:9", "9:20", "auto"}
    if requested in supported:
        return requested
    return aspect_ratio_from_size(size)


def generate_image_bytes(config: dict[str, str], prompt: str, reference_paths: list[Path] | None, size: str = "1536x1024", aspect: str = "", *, sc: Any) -> bytes:
    provider = config["provider"]
    model = config["model"]
    api_key = config["api_key"]
    refs = [path for path in (reference_paths or []) if path]
    if provider == "openai":
        headers = {"Authorization": f"Bearer {api_key}"}
        if refs:
            image_field = "image[]" if len(refs) > 1 else "image"
            payload = post_multipart_request("https://api.openai.com/v1/images/edits", {"model": model, "prompt": prompt, "size": size}, [(image_field, path) for path in refs], headers)
        else:
            payload = post_json_request("https://api.openai.com/v1/images/generations", {"model": model, "prompt": prompt, "size": size}, headers)
        return base64.b64decode(image_b64_from_response(provider, payload))
    if provider == "xai":
        headers = {"Authorization": f"Bearer {api_key}"}
        aspect_ratio = supported_xai_aspect_ratio(aspect, size)
        if refs:
            image_payloads = [{"type": "image_url", "url": image_data_uri(path)} for path in refs[:3]]
            body: dict[str, Any] = {"model": model, "prompt": prompt, "response_format": "b64_json"}
            if len(image_payloads) == 1:
                body["image"] = image_payloads[0]
            else:
                body["images"] = image_payloads
            if aspect_ratio:
                body["aspect_ratio"] = aspect_ratio
            payload = post_json_request("https://api.x.ai/v1/images/edits", body, headers)
            return image_bytes_from_xai_response(payload, sc=sc)
        body = {"model": model, "prompt": prompt, "response_format": "b64_json"}
        if aspect_ratio:
            body["aspect_ratio"] = aspect_ratio
        payload = post_json_request("https://api.x.ai/v1/images/generations", body, headers)
        return image_bytes_from_xai_response(payload, sc=sc)
    if provider == "gemini":
        parts: list[dict[str, Any]] = [{"text": prompt}]
        for path in refs:
            parts.append({"inline_data": {"mime_type": mimetypes.guess_type(path.name)[0] or "image/png", "data": base64.b64encode(path.read_bytes()).decode("ascii")}})
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(model, safe='')}:generateContent?key={urllib.parse.quote(api_key, safe='')}"
        payload = post_json_request(url, {"contents": [{"role": "user", "parts": parts}], "generationConfig": {"responseModalities": ["IMAGE"]}}, {})
        return base64.b64decode(image_b64_from_response(provider, payload))
    raise HTTPException(status_code=400, detail=f"Unsupported active image provider: {provider}")


def download_binary(url: str, output_path: Path, timeout: int = 600) -> None:
    try:
        with provider_urlopen(urllib.request.Request(url), timeout=timeout) as res:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with output_path.open("wb") as handle:
                shutil.copyfileobj(res, handle)
            sanitize_audio_file_metadata(output_path)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2000]
        raise HTTPException(status_code=502, detail=f"TTS audio download failed: HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise HTTPException(status_code=502, detail=f"TTS audio download failed: {exc.reason}") from exc
    except MediaSanitizeError as exc:
        output_path.unlink(missing_ok=True)
        raise HTTPException(status_code=502, detail=f"TTS audio metadata sanitization failed: {exc}") from exc


def register_provider_services(ns: Any) -> None:
    for name in SERVICE_EXPORTS:
        setattr(ns, name, globals()[name])
