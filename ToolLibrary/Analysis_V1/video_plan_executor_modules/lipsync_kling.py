from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any

TEMPLATE_NAME = "Ref_05_02_Lipsync_Kling.md"
SOURCE_TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "Reference" / "05_02" / "Lipsync_Kling.md"
VIDEO_CONTENT_TYPES = ("video/", "application/octet-stream")
VIDEO_MAX_BYTES = 1024 * 1024 * 1024
AUDIO_MAX_BYTES = 5 * 1024 * 1024
AUDIO_MIN_DURATION_SECONDS = 2.0
AUDIO_MAX_DURATION_SECONDS = 60.0
TIME_BOUNDARY_GUARD_MS = 50
KLING_LIPSYNC_MIN_TIMEOUT_SECONDS = 7200
REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

try:
    from opcrew_backend.services.safe_download import safe_download_to_path
except Exception:
    safe_download_to_path = None  # type: ignore[assignment]

try:
    from OpenCrew.ToolLibrary.Analysis_V1.video_plan_executor_modules.video_kling import file_sha256, reference_video_public_url, video_duration_seconds
except Exception:
    try:
        from ToolLibrary.Analysis_V1.video_plan_executor_modules.video_kling import file_sha256, reference_video_public_url, video_duration_seconds
    except Exception:
        file_sha256 = None  # type: ignore[assignment]
        reference_video_public_url = None  # type: ignore[assignment]
        video_duration_seconds = None  # type: ignore[assignment]


class ToolError(RuntimeError):
    pass


class ProviderTimeout(ToolError):
    pass


def now_ms() -> int:
    return int(time.time() * 1000)


def text_value(value: Any) -> str:
    return str(value or "").strip()


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, str):
        return value.replace("\\/", "/")
    return value


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_safe(payload), ensure_ascii=False, indent=2), encoding="utf-8")


def _write_prompt_package_file(prompt_dir: Path, asset_key: str, kind: str, package: dict[str, Any]) -> Path:
    rendered_path = prompt_dir / f"PromptRendered_{asset_key}_{kind}Prompt.json"
    write_json(rendered_path, package)
    return rendered_path


def template_snapshot_text(context: dict[str, Any], default_name: str) -> str:
    prompt_dir = Path(context.get("prompt_dir") or "")
    template_name = text_value(context.get("template_name") or default_name)
    candidate = prompt_dir / template_name
    if candidate.exists():
        return candidate.read_text(encoding="utf-8")
    source_value = text_value(context.get("template_source_path"))
    source = Path(source_value) if source_value else None
    if source and source.exists() and source.is_file():
        return source.read_text(encoding="utf-8")
    return ""


def _template_text(context: dict[str, Any]) -> str:
    return template_snapshot_text({**context, "template_name": TEMPLATE_NAME, "template_source_path": str(SOURCE_TEMPLATE_PATH)}, TEMPLATE_NAME)


def _block(template_text: str, name: str) -> str:
    start = f"<!-- OPENCREW:{name}_START -->"
    end = f"<!-- OPENCREW:{name}_END -->"
    if start not in template_text or end not in template_text:
        raise ToolError(f"Lipsync Kling template is missing block marker: {name}")
    return template_text.split(start, 1)[1].split(end, 1)[0].strip()


def build_prompt_package(context: dict[str, Any]) -> dict[str, Any]:
    template_text = _template_text(context)
    segment = dict_value(context.get("segment"))
    prompt = f"{_block(template_text, 'LIPSYNC_KLING_PROMPT')}\n\n{_block(template_text, 'LIPSYNC_KLING_PITFALLS_APPEND_ONLY')}"
    return {
        "schema_version": "analysis_v1_05_02_lipsync_prompt_kling_0.1",
        "prompt_type": "lipsync_request",
        "provider_profile": "lipsync_kling",
        "segment_id": text_value(segment.get("segment_id")),
        "dialogue_asset_keys": list_value(segment.get("dialogue_asset_keys") or segment.get("dialogue_ids")),
        "dialogue_ids": list_value(segment.get("dialogue_asset_keys") or segment.get("dialogue_ids")),
        "template_source": TEMPLATE_NAME,
        "template_snapshot_chars": len(template_text),
        "template_blocks": ["LIPSYNC_KLING_PROMPT", "LIPSYNC_KLING_PITFALLS_APPEND_ONLY"],
        "prompt": prompt,
        "extracted_fields": {
            "video_path": text_value(context.get("video_path")),
            "source_video_url": text_value(context.get("source_video_url")),
            "source_video_id": text_value(context.get("source_video_id")),
            "audio_path": text_value(context.get("audio_path")),
            "output_path": text_value(context.get("output_path")),
        },
    }


def write_prompt_package(prompt_dir: Path, asset_key: str, package: dict[str, Any]) -> Path:
    return _write_prompt_package_file(prompt_dir, asset_key, "LipSync", package)


def dry_run_prompt(context: dict[str, Any], prompt_dir: Path, asset_key: str) -> dict[str, Any]:
    package = build_prompt_package(context)
    return {"prompt_path": str(write_prompt_package(prompt_dir, asset_key, package)), "package": package}


def b64url(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def kling_bearer_token(api_key: str) -> str:
    raw = text_value(api_key)
    if raw.lower().startswith("bearer "):
        return raw.split(None, 1)[1].strip()
    credentials: dict[str, Any] = {}
    if raw.startswith("{"):
        try:
            parsed = json.loads(raw)
            credentials = parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            credentials = {}
    elif ":" in raw and raw.count(":") == 1:
        access_key, secret_key = raw.split(":", 1)
        credentials = {"access_key": access_key, "secret_key": secret_key}
    access_key = text_value(credentials.get("access_key") or credentials.get("ak") or credentials.get("accessKey"))
    secret_key = text_value(credentials.get("secret_key") or credentials.get("sk") or credentials.get("secretKey"))
    if not access_key or not secret_key:
        return raw
    header = {"alg": "HS256", "typ": "JWT"}
    now = int(time.time())
    payload = {"iss": access_key, "exp": now + 1800, "nbf": now - 5}
    signing_input = f"{b64url(json.dumps(header, separators=(',', ':')).encode('utf-8'))}.{b64url(json.dumps(payload, separators=(',', ':')).encode('utf-8'))}"
    signature = hmac.new(secret_key.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest()
    return f"{signing_input}.{b64url(signature)}"


def first_url(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("url", "video_url", "download_url", "outputUrl", "output_url", "watermark_url"):
            value = payload.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value
        for value in payload.values():
            found = first_url(value)
            if found:
                return found
    if isinstance(payload, list):
        for item in payload:
            found = first_url(item)
            if found:
                return found
    return ""


def _request_with_direct_retry(requests_module: Any, method: str, url: str, **kwargs: Any) -> Any:
    try:
        return requests_module.request(method, url, **kwargs)
    except Exception as exc:
        message = str(exc).lower()
        proxy_error = "proxy" in message or "ruleset" in message or "127.0.0.1:7890" in message
        if not proxy_error:
            raise
        session = requests_module.Session()
        session.trust_env = False
        response = session.request(method, url, **kwargs)
        setattr(response, "_opencrew_direct_session", session)
        return response


def _close_response(response: Any) -> None:
    try:
        response.close()
    finally:
        session = getattr(response, "_opencrew_direct_session", None)
        if session:
            session.close()


def api_post(requests_module: Any, url: str, token: str, payload: dict[str, Any], timeout: int = 120) -> dict[str, Any]:
    response = _request_with_direct_retry(
        requests_module,
        "POST",
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/json"},
        json=payload,
        timeout=timeout,
    )
    try:
        try:
            body = response.json()
        except ValueError:
            body = {"raw_text": response.text}
        if int(response.status_code) >= 400:
            raise ToolError(f"Kling lip-sync HTTP {response.status_code}: {json.dumps(json_safe(body), ensure_ascii=False)[:1200]}")
        return body
    finally:
        _close_response(response)


def api_get(requests_module: Any, url: str, token: str, timeout: int = 60) -> dict[str, Any]:
    response = _request_with_direct_retry(
        requests_module,
        "GET",
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=timeout,
    )
    try:
        try:
            body = response.json()
        except ValueError:
            body = {"raw_text": response.text}
        if int(response.status_code) >= 400:
            raise ToolError(f"Kling lip-sync poll HTTP {response.status_code}: {json.dumps(json_safe(body), ensure_ascii=False)[:1200]}")
        return body
    finally:
        _close_response(response)


def response_data(payload: dict[str, Any], action: str) -> dict[str, Any]:
    code = payload.get("code")
    if code not in (0, "0", None):
        raise ToolError(f"Kling {action} failed: {json.dumps(json_safe(payload), ensure_ascii=False)[:1200]}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ToolError(f"Kling {action} response missing data: {json.dumps(json_safe(payload), ensure_ascii=False)[:1200]}")
    return data


def publish_source_video(video_path: Path, config: dict[str, Any]) -> dict[str, Any]:
    if reference_video_public_url is None:
        raise ToolError("Kling lip-sync local video needs a public URL, but Kling public URL publisher is unavailable.")
    publish_config = dict(config)
    if not text_value(publish_config.get("public_asset_provider")):
        publish_config["public_asset_provider"] = "tmpfiles"
    public_url = reference_video_public_url(video_path, publish_config)
    if not public_url:
        raise ToolError(f"Kling public URL publisher returned empty URL for {video_path}")
    return {
        "provider": text_value(publish_config.get("public_asset_provider") or "tmpfiles"),
        "purpose": "kling_lipsync_source_video",
        "path": str(video_path),
        "filename": video_path.name,
        "size_bytes": video_path.stat().st_size,
        "sha256": file_sha256(video_path) if callable(file_sha256) else "",
        "duration_seconds": video_duration_seconds(video_path) if callable(video_duration_seconds) else 0.0,
        "public_url": public_url,
    }


def source_video_reference(context: dict[str, Any], config: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    video_url = text_value(context.get("video_url") or config.get("video_url") or config.get("source_video_url"))
    video_id = text_value(context.get("video_id") or config.get("video_id") or config.get("source_video_id"))
    video_path_text = text_value(context.get("video_path"))
    if not video_url and video_path_text.startswith(("http://", "https://")):
        video_url = video_path_text
    published_asset: dict[str, Any] = {}
    if not video_url and not video_id and video_path_text:
        video_path = Path(video_path_text)
        if video_path.exists() and video_path.is_file():
            published_asset = publish_source_video(video_path, config)
            video_url = text_value(published_asset.get("public_url"))
    if bool(video_url) == bool(video_id):
        raise ToolError("Kling lip-sync requires exactly one source video reference: video_url or Kling video_id. Local video files must be published to a public URL before using Kling.")
    return video_url, video_id, published_asset


def audio_sound_file(audio_path: Path) -> str:
    return base64.b64encode(audio_path.read_bytes()).decode("ascii")


def media_duration_seconds(path: Path, label: str) -> float:
    if not callable(video_duration_seconds):
        raise ToolError(f"Kling lip-sync needs {label} duration, but ffprobe duration helper is unavailable.")
    duration = safe_float(video_duration_seconds(path), 0.0)
    if duration <= 0:
        raise ToolError(f"Kling lip-sync cannot determine a valid {label} duration for {path.name}.")
    return duration


def optional_local_media_duration_seconds(path: Path) -> float:
    if not path.exists() or not path.is_file() or not callable(video_duration_seconds):
        return 0.0
    return safe_float(video_duration_seconds(path), 0.0)


def config_float(config: dict[str, Any], camel_key: str, snake_key: str, default: float) -> float:
    if camel_key in config:
        return safe_float(config.get(camel_key), default)
    if snake_key in config:
        return safe_float(config.get(snake_key), default)
    return default


def config_int_ms(config: dict[str, Any], camel_key: str, snake_key: str, default: int) -> int:
    if camel_key in config:
        return int(round(safe_float(config.get(camel_key), default)))
    if snake_key in config:
        return int(round(safe_float(config.get(snake_key), default)))
    return default


def guarded_time_limit_ms(limit_ms: int, start_ms: int) -> int:
    limit = max(0, int(limit_ms))
    if limit - start_ms > int(AUDIO_MIN_DURATION_SECONDS * 1000) + TIME_BOUNDARY_GUARD_MS:
        return limit - TIME_BOUNDARY_GUARD_MS
    return limit


def kling_poll_timeout_seconds(context: dict[str, Any], config: dict[str, Any]) -> int:
    configured = int(safe_float(context.get("timeout_seconds"), 60))
    provider_override = int(safe_float(config.get("lipsync_poll_timeout_seconds") or config.get("kling_lipsync_timeout_seconds"), 0))
    if provider_override > 0:
        return max(provider_override, 60)
    return max(configured, KLING_LIPSYNC_MIN_TIMEOUT_SECONDS)


def download_video(requests_module: Any, video_url: str, output_path: Path, token: str) -> None:
    del requests_module
    if safe_download_to_path is None:
        raise ToolError("Safe provider artifact downloader is unavailable; refusing to download Kling lip-sync output.")
    first_error: Exception | None = None
    header_attempts = [
        {"Authorization": f"Bearer {token}", "User-Agent": "OpenCrew/kling-lipsync-download"},
        {"User-Agent": "OpenCrew/kling-lipsync-download"},
    ]
    for index, headers in enumerate(header_attempts):
        try:
            safe_download_to_path(
                video_url,
                output_path,
                allowed_content_types=VIDEO_CONTENT_TYPES,
                max_bytes=VIDEO_MAX_BYTES,
                timeout=600,
                headers=headers,
            )
            return
        except Exception as exc:
            if index == 0 and any(code in str(exc) for code in ("401", "403")):
                first_error = exc
                continue
            if first_error is not None:
                raise ToolError(f"Trusted Kling lip-sync download failed after auth and public retries: {first_error}; {exc}") from exc
            raise ToolError(f"Trusted Kling lip-sync download failed: {exc}") from exc


def generate(context: dict[str, Any], prompt_path: Path, output_path: Path) -> dict[str, Any]:
    del prompt_path
    try:
        import requests  # type: ignore
    except Exception as exc:
        raise ToolError("requests is required for Kling lip-sync.") from exc
    config = dict_value(context.get("config"))
    provider = text_value(config.get("provider")).lower()
    if provider not in {"kling", "klingai", "kling-ai"}:
        raise ToolError(f"Unsupported lipsync provider: {provider}/{config.get('model')}")
    api_key = text_value(config.get("api_key"))
    if not api_key:
        raise ToolError(f"Missing Kling lip-sync API credential for {provider}/{config.get('model')}.")
    token = kling_bearer_token(api_key)
    base_url = text_value(config.get("base_url") or "https://api-beijing.klingai.com").rstrip("/")
    video_url, video_id, published_asset = source_video_reference(context, config)
    video_path = Path(text_value(context.get("video_path")))
    audio_path = Path(text_value(context.get("audio_path")))
    request_path = Path(text_value(context.get("request_path")))
    status_path = Path(text_value(context.get("status_path")))
    create_response_path = Path(text_value(context.get("create_response_path")))
    started_at = time.time()
    source_video_duration = optional_local_media_duration_seconds(video_path)
    source_video_duration_ms = int(source_video_duration * 1000) if source_video_duration > 0 else 0
    audio_duration = media_duration_seconds(audio_path, "audio")
    if audio_duration < AUDIO_MIN_DURATION_SECONDS or audio_duration > AUDIO_MAX_DURATION_SECONDS:
        raise ToolError(f"Kling lip-sync audio duration must be between 2 and 60 seconds: {audio_duration:.3f}s")
    audio_size_bytes = audio_path.stat().st_size
    if audio_size_bytes > AUDIO_MAX_BYTES:
        raise ToolError(f"Kling lip-sync sound_file must be 5MB or smaller: {audio_size_bytes} bytes")
    audio_duration_ms = int(audio_duration * 1000)
    sound_start_time_ms = config_int_ms(config, "soundStartTime", "sound_start_time", 0)
    requested_sound_end_time_ms = config_int_ms(config, "soundEndTime", "sound_end_time", audio_duration_ms)
    sound_end_time_ms = min(requested_sound_end_time_ms, guarded_time_limit_ms(audio_duration_ms, sound_start_time_ms))
    if sound_end_time_ms <= sound_start_time_ms:
        raise ToolError(f"Kling lip-sync sound_end_time must be greater than sound_start_time: {sound_end_time_ms} <= {sound_start_time_ms}")
    identify_payload = {key: value for key, value in {"video_url": video_url, "video_id": video_id}.items() if value}
    identified = api_post(requests, f"{base_url}/v1/videos/identify-face", token, identify_payload)
    identify_data = response_data(identified, "identify-face")
    session_id = text_value(identify_data.get("session_id") or identify_data.get("sessionId"))
    faces = list_value(identify_data.get("face_data") or identify_data.get("faceData"))
    face = faces[0] if faces and isinstance(faces[0], dict) else {}
    face_id = text_value(face.get("face_id") or face.get("faceId"))
    if not session_id or not face_id:
        raise ToolError(f"Kling identify-face response missing session_id or face_id: {json.dumps(json_safe(identified), ensure_ascii=False)[:1200]}")
    face_start_ms = int(round(safe_float(face.get("start_time") or face.get("startTime"), 0.0)))
    face_end_ms = int(round(safe_float(face.get("end_time") or face.get("endTime"), 0.0)))
    sound_insert_time_ms = config_int_ms(config, "soundInsertTime", "sound_insert_time", max(face_start_ms, 0))
    if face_end_ms > 0:
        max_sound_end_for_face_ms = sound_start_time_ms + max(0, face_end_ms - sound_insert_time_ms)
        sound_end_time_ms = min(sound_end_time_ms, guarded_time_limit_ms(max_sound_end_for_face_ms, sound_start_time_ms))
    if source_video_duration_ms > 0:
        max_sound_end_for_video_ms = sound_start_time_ms + max(0, source_video_duration_ms - sound_insert_time_ms)
        sound_end_time_ms = min(sound_end_time_ms, guarded_time_limit_ms(max_sound_end_for_video_ms, sound_start_time_ms))
    cropped_sound_ms = sound_end_time_ms - sound_start_time_ms
    if cropped_sound_ms < int(AUDIO_MIN_DURATION_SECONDS * 1000):
        raise ToolError(f"Kling lip-sync cropped sound must be at least 2 seconds: {cropped_sound_ms}ms")
    if face_end_ms > 0:
        overlap_ms = max(0, min(sound_insert_time_ms + cropped_sound_ms, face_end_ms) - max(sound_insert_time_ms, face_start_ms))
        if overlap_ms < int(AUDIO_MIN_DURATION_SECONDS * 1000):
            raise ToolError(f"Kling lip-sync sound insertion must overlap the selected face interval by at least 2 seconds: overlap={overlap_ms}ms")
    write_json(request_path, {
        "created_at": now_ms(),
        "endpoint": f"{base_url}/v1/videos/identify-face",
        "create_endpoint": f"{base_url}/v1/videos/advanced-lip-sync",
        "provider": "kling",
        "model": text_value(config.get("model") or "kling-lipsync-advanced"),
        "video_url": video_url,
        "video_id": video_id,
        "audio_path": str(audio_path),
        "audio_size_bytes": audio_size_bytes,
        "audio_duration_seconds": round(audio_duration, 3),
        "audio_duration_ms_floor": audio_duration_ms,
        "source_video_duration_seconds": round(source_video_duration, 3) if source_video_duration > 0 else 0,
        "source_video_duration_ms_floor": source_video_duration_ms,
        "face_start_time_ms": face_start_ms,
        "face_end_time_ms": face_end_ms,
        "sound_insert_time_ms": sound_insert_time_ms,
        "sound_start_time_ms": sound_start_time_ms,
        "requested_sound_end_time_ms": requested_sound_end_time_ms,
        "sound_end_time_ms": sound_end_time_ms,
        "time_boundary_guard_ms": TIME_BOUNDARY_GUARD_MS,
        "published_asset": published_asset,
    })
    create_payload = {
        "session_id": session_id,
        "face_choose": [{
            "face_id": face_id,
            "sound_file": audio_sound_file(audio_path),
            "sound_insert_time": sound_insert_time_ms,
            "sound_start_time": sound_start_time_ms,
            "sound_end_time": sound_end_time_ms,
            "sound_volume": config_float(config, "soundVolume", "sound_volume", 2.0),
            "original_audio_volume": config_float(config, "originalAudioVolume", "original_audio_volume", 2.0),
        }],
        "external_task_id": text_value(config.get("externalTaskId") or config.get("external_task_id") or f"opencrew_{now_ms()}"),
    }
    callback_url = text_value(config.get("callbackUrl") or config.get("callback_url"))
    if callback_url:
        create_payload["callback_url"] = callback_url
    created = api_post(requests, f"{base_url}/v1/videos/advanced-lip-sync", token, create_payload)
    write_json(create_response_path, {
        "identify": identified,
        "create": created,
        "create_payload_summary": {
            "session_id": session_id,
            "face_choose": [{
                "face_id": face_id,
                "sound_insert_time": sound_insert_time_ms,
                "sound_start_time": sound_start_time_ms,
                "sound_end_time": sound_end_time_ms,
                "sound_volume": config_float(config, "soundVolume", "sound_volume", 2.0),
                "original_audio_volume": config_float(config, "originalAudioVolume", "original_audio_volume", 2.0),
            }],
            "external_task_id": create_payload["external_task_id"],
            "callback_url": callback_url,
        },
    })
    create_data = response_data(created, "advanced-lip-sync")
    task_id = text_value(create_data.get("task_id") or create_data.get("taskId"))
    if not task_id:
        raise ToolError(f"Kling advanced-lip-sync response missing task_id: {json.dumps(json_safe(created), ensure_ascii=False)[:1200]}")
    history: list[dict[str, Any]] = []
    latest: dict[str, Any] = {}
    deadline = time.time() + kling_poll_timeout_seconds(context, config)
    while time.time() < deadline:
        polled = api_get(requests, f"{base_url}/v1/videos/advanced-lip-sync/{urllib.parse.quote(task_id, safe='')}", token)
        data = response_data(polled, "query advanced-lip-sync")
        status = text_value(data.get("task_status") or data.get("taskStatus")).lower()
        latest = data
        history.append({"checked_at": now_ms(), "status": status, "body": polled})
        write_json(status_path, {"task_id": task_id, "history": history, "latest": polled})
        if status in {"succeed", "failed"}:
            break
        time.sleep(15)
    status = text_value(latest.get("task_status") or latest.get("taskStatus")).lower()
    if status != "succeed":
        raise ProviderTimeout(f"Kling lip-sync ended with {status or 'UNKNOWN'}: {json.dumps(json_safe(latest), ensure_ascii=False)[:1200]}")
    output_url = first_url(latest.get("task_result"))
    if not output_url:
        output_url = first_url(latest)
    if not output_url:
        raise ToolError(f"Completed Kling lip-sync result did not include video URL: {json.dumps(json_safe(latest), ensure_ascii=False)[:1200]}")
    download_video(requests, output_url, output_path, token)
    return {
        "provider": "kling",
        "model": text_value(config.get("model") or "kling-lipsync-advanced"),
        "session_id": session_id,
        "face_id": face_id,
        "task_id": task_id,
        "output_url": output_url,
        "output_path": str(output_path),
        "published_asset": published_asset,
        "audio_duration_seconds": round(audio_duration, 3),
        "sound_insert_time_ms": sound_insert_time_ms,
        "sound_start_time_ms": sound_start_time_ms,
        "sound_end_time_ms": sound_end_time_ms,
        "final_unit_deduction": text_value(latest.get("final_unit_deduction")),
        "elapsed_seconds": round(time.time() - started_at, 3),
    }
