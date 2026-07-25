from __future__ import annotations

import json
import mimetypes
import re
import time
import urllib.parse
from pathlib import Path
from typing import Any

TEMPLATE_NAME = "Ref_05_02_Lipsync_Chanjing.md"
SOURCE_TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "Reference" / "05_02" / "Lipsync_Chanjing.md"
API_BASE_URL = "https://open-api.chanjing.cc/open/v1"


class ToolError(RuntimeError):
    pass


class ProviderTimeout(ToolError):
    pass


def now_ms() -> int:
    return int(time.time() * 1000)


def text_value(value: Any) -> str:
    return str(value or "").strip()


def dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_safe(payload), ensure_ascii=False, indent=2), encoding="utf-8")


def redact_secret_text(value: str) -> str:
    text = str(value or "").replace("\\/", "/")
    text = re.sub(r"(access_token[\"']?\s*[:=]\s*[\"']?)[^\"',}\s]+", r"\1***", text, flags=re.I)
    text = re.sub(r"(secret[_-]?key[\"']?\s*[:=]\s*[\"']?)[^\"',}\s]+", r"\1***", text, flags=re.I)
    text = re.sub(r"(app[_-]?key[\"']?\s*[:=]\s*[\"']?)[^\"',}\s]+", r"\1***", text, flags=re.I)
    text = re.sub(r"(api[_-]?key[\"']?\s*[:=]\s*[\"']?)[^\"',}\s]+", r"\1***", text, flags=re.I)
    text = re.sub(r"(Authorization[\"']?\s*[:=]\s*[\"']?\s*Bearer\s+)[^\"',}\s]+", r"\1***", text, flags=re.I)
    text = re.sub(r"([?&](?:OSSAccessKeyId|Signature|Expires|security-token|x-oss-security-token)=)[^&\s\"'}]+", r"\1***", text, flags=re.I)
    return text


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text.lower() in {"app_key", "api_key", "secret_key", "access_token", "sign_url"}:
                safe[key_text] = "***" if item else ""
            else:
                safe[key_text] = json_safe(item)
        return safe
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, str):
        return redact_secret_text(value)
    return value


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


def _write_prompt_package_file(prompt_dir: Path, asset_key: str, kind: str, package: dict[str, Any]) -> Path:
    rendered_path = prompt_dir / f"PromptRendered_{asset_key}_{kind}Prompt.json"
    write_json(rendered_path, package)
    return rendered_path


def _template_text(context: dict[str, Any]) -> str:
    return template_snapshot_text({**context, "template_name": TEMPLATE_NAME, "template_source_path": str(SOURCE_TEMPLATE_PATH)}, TEMPLATE_NAME)


def _block(template_text: str, name: str) -> str:
    start = f"<!-- OPENCREW:{name}_START -->"
    end = f"<!-- OPENCREW:{name}_END -->"
    if start not in template_text or end not in template_text:
        raise ToolError(f"Lipsync Chanjing template is missing block marker: {name}")
    return template_text.split(start, 1)[1].split(end, 1)[0].strip()


def normalize_model(model: str) -> int:
    value = text_value(model).lower() or "quality"
    aliases = {
        "0": 0,
        "basic": 0,
        "model-0": 0,
        "model_0": 0,
        "chanjing-lipsync-basic": 0,
        "1": 1,
        "quality": 1,
        "high": 1,
        "high-quality": 1,
        "model-1": 1,
        "model_1": 1,
        "chanjing-lipsync-quality": 1,
    }
    if value not in aliases:
        raise ToolError(f"Unsupported Chanjing lipsync model: {model}. Expected basic/quality or model 0/1.")
    return aliases[value]


def build_prompt_package(context: dict[str, Any]) -> dict[str, Any]:
    template_text = _template_text(context)
    segment = dict_value(context.get("segment"))
    prompt = f"{_block(template_text, 'LIPSYNC_CHANJING_PROMPT')}\n\n{_block(template_text, 'LIPSYNC_CHANJING_PITFALLS_APPEND_ONLY')}"
    return {
        "schema_version": "analysis_v1_05_02_lipsync_prompt_chanjing_0.1",
        "prompt_type": "lipsync_request",
        "provider_profile": "lipsync_chanjing",
        "segment_id": text_value(segment.get("segment_id")),
        "dialogue_asset_keys": list_value(segment.get("dialogue_asset_keys") or segment.get("dialogue_ids")),
        "dialogue_ids": list_value(segment.get("dialogue_asset_keys") or segment.get("dialogue_ids")),
        "template_source": TEMPLATE_NAME,
        "template_snapshot_chars": len(template_text),
        "template_blocks": ["LIPSYNC_CHANJING_PROMPT", "LIPSYNC_CHANJING_PITFALLS_APPEND_ONLY"],
        "prompt": prompt,
        "extracted_fields": {
            "video_path": text_value(context.get("video_path")),
            "audio_path": text_value(context.get("audio_path")),
            "output_path": text_value(context.get("output_path")),
        },
    }


def write_prompt_package(prompt_dir: Path, asset_key: str, package: dict[str, Any]) -> Path:
    return _write_prompt_package_file(prompt_dir, asset_key, "LipSync", package)


def dry_run_prompt(context: dict[str, Any], prompt_dir: Path, asset_key: str) -> dict[str, Any]:
    package = build_prompt_package(context)
    return {"prompt_path": str(write_prompt_package(prompt_dir, asset_key, package)), "package": package}


def _requests_proxy_tunnel_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return (
        "proxyerror" in message
        or "unable to connect to proxy" in message
        or "connection not allowed by ruleset" in message
        or "sockshttpsconnectionpool" in message
        or "sockshttpconnectionpool" in message
        or "tunnel connection failed" in message
        or ("proxy" in message and ("403" in message or "forbidden" in message or "connection refused" in message))
        or "127.0.0.1:7890" in message
        or "localhost:7890" in message
    )


def _request_with_direct_retry(requests_module: Any, method: str, url: str, *, on_retry: Any = None, **kwargs: Any) -> Any:
    try:
        return requests_module.request(method, url, **kwargs)
    except Exception as exc:
        if not _requests_proxy_tunnel_error(exc):
            raise
        if on_retry:
            on_retry()
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


def response_json(response: Any) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        payload = {"raw_text": response.text}
    return payload if isinstance(payload, dict) else {"data": payload}


def require_success(payload: dict[str, Any], label: str) -> dict[str, Any]:
    if int(payload.get("code") or 0) != 0:
        raise ToolError(f"Chanjing {label} failed: {json_safe(payload)}")
    data = payload.get("data")
    return data if isinstance(data, dict) else {"value": data}


def credential_payload(raw_api_key: str, config: dict[str, Any]) -> dict[str, str]:
    raw = text_value(raw_api_key)
    parsed: dict[str, Any] = {}
    if raw.startswith("{"):
        try:
            value = json.loads(raw)
            parsed = value if isinstance(value, dict) else {}
        except json.JSONDecodeError as exc:
            raise ToolError("Chanjing credentials must be JSON with app_key and api_key when using combined storage.") from exc
    app_key = text_value(parsed.get("app_key") or parsed.get("app_id") or parsed.get("ak") or config.get("app_key") or config.get("app_id") or config.get("ak"))
    secret_key = text_value(parsed.get("api_key") or parsed.get("secret_key") or parsed.get("sk") or config.get("secret_key") or config.get("sk"))
    if not secret_key and raw and not raw.startswith("{"):
        secret_key = raw
    if not app_key or not secret_key:
        raise ToolError("Missing Chanjing APP Key/API Key credentials.")
    return {"app_id": app_key, "secret_key": secret_key}


def get_access_token(requests: Any, credentials: dict[str, str]) -> dict[str, Any]:
    response = _request_with_direct_retry(
        requests,
        "POST",
        f"{API_BASE_URL}/access_token",
        headers={"Content-Type": "application/json; charset=utf-8"},
        json=credentials,
        timeout=30,
    )
    try:
        payload = response_json(response)
        if int(response.status_code) >= 400:
            raise ToolError(f"Chanjing access_token HTTP {response.status_code}: {json_safe(payload)}")
        data = require_success(payload, "access_token")
        token = text_value(data.get("access_token"))
        if not token:
            raise ToolError(f"Chanjing access_token response missing token: {json_safe(payload)}")
        return {"access_token": token, "expire_in": data.get("expire_in"), "trace_id": payload.get("trace_id")}
    finally:
        _close_response(response)


def api_headers(access_token: str, *, json_content: bool = True) -> dict[str, str]:
    headers = {"access_token": access_token, "Accept": "*/*"}
    if json_content:
        headers["Content-Type"] = "application/json"
    return headers


def upload_asset(requests: Any, access_token: str, path: Path, service: str, *, timeout: int) -> dict[str, Any]:
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    url = f"{API_BASE_URL}/common/create_upload_url?{urllib.parse.urlencode({'service': service, 'name': path.name})}"
    response = _request_with_direct_retry(requests, "GET", url, headers=api_headers(access_token, json_content=False), timeout=30)
    try:
        upload_payload = response_json(response)
        if int(response.status_code) >= 400:
            raise ToolError(f"Chanjing create_upload_url HTTP {response.status_code}: {json_safe(upload_payload)}")
        upload_data = require_success(upload_payload, "create_upload_url")
    finally:
        _close_response(response)

    sign_url = text_value(upload_data.get("sign_url"))
    if not sign_url:
        raise ToolError(f"Chanjing create_upload_url response missing sign_url: {json_safe(upload_payload)}")
    with path.open("rb") as file_obj:
        put_response = _request_with_direct_retry(
            requests,
            "PUT",
            sign_url,
            headers={"Content-Type": text_value(upload_data.get("mime_type")) or mime},
            data=file_obj,
            timeout=timeout,
            on_retry=lambda: file_obj.seek(0),
        )
    try:
        if int(put_response.status_code) >= 400:
            raise ToolError(f"Chanjing file PUT failed: HTTP {put_response.status_code}: {put_response.text[:500]}")
    finally:
        _close_response(put_response)

    file_id = text_value(upload_data.get("file_id"))
    detail = poll_file_ready(requests, access_token, file_id, timeout_seconds=75)
    return {
        "service": service,
        "file_id": file_id,
        "full_path": text_value(upload_data.get("full_path")),
        "mime_type": text_value(upload_data.get("mime_type")) or mime,
        "name": path.name,
        "size_bytes": path.stat().st_size,
        "detail": detail,
    }


def poll_file_ready(requests: Any, access_token: str, file_id: str, *, timeout_seconds: int) -> dict[str, Any]:
    if not file_id:
        raise ToolError("Chanjing upload response missing file_id.")
    deadline = time.time() + timeout_seconds
    latest: dict[str, Any] = {}
    while time.time() < deadline:
        url = f"{API_BASE_URL}/common/file_detail?{urllib.parse.urlencode({'id': file_id})}"
        response = _request_with_direct_retry(requests, "GET", url, headers=api_headers(access_token, json_content=False), timeout=30)
        try:
            payload = response_json(response)
            if int(response.status_code) >= 400:
                raise ToolError(f"Chanjing file_detail HTTP {response.status_code}: {json_safe(payload)}")
            data = require_success(payload, "file_detail")
            latest = data
            status = int(data.get("status") or 0)
            if status == 1:
                return data
            if status in {98, 99, 100}:
                raise ToolError(f"Chanjing uploaded file is not usable: {json_safe(data)}")
        finally:
            _close_response(response)
        time.sleep(3)
    raise ProviderTimeout(f"Chanjing uploaded file did not become ready in time: {json_safe(latest)}")


def create_payload(config: dict[str, Any], video_asset: dict[str, Any], audio_asset: dict[str, Any], model_value: int) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "video_file_id": text_value(video_asset.get("file_id")),
        "screen_width": int(config.get("screen_width") or 1080),
        "screen_height": int(config.get("screen_height") or 1920),
        "model": model_value,
        "audio_type": "audio",
        "audio_file_id": text_value(audio_asset.get("file_id")),
        "volume": int(config.get("volume") or 100),
    }
    for key in ("callback", "drive_mode"):
        value = text_value(config.get(key))
        if value:
            payload[key] = value
    if config.get("backway") not in (None, ""):
        payload["backway"] = int(config.get("backway") or 1)
    return payload


def create_lipsync(requests: Any, access_token: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = _request_with_direct_retry(
        requests,
        "POST",
        f"{API_BASE_URL}/video_lip_sync/create",
        headers=api_headers(access_token),
        json=payload,
        timeout=60,
    )
    try:
        created = response_json(response)
        if int(response.status_code) >= 400:
            raise ToolError(f"Chanjing lipsync create HTTP {response.status_code}: {json_safe(created)}")
        data = require_success(created, "video_lip_sync/create")
        task_id = text_value(data.get("value"))
        if not task_id:
            raise ToolError(f"Chanjing lipsync create response missing task id: {json_safe(created)}")
        return {"task_id": task_id, "body": created}
    finally:
        _close_response(response)


def poll_lipsync(requests: Any, access_token: str, task_id: str, status_path: Path, *, timeout_seconds: int) -> dict[str, Any]:
    history: list[dict[str, Any]] = []
    deadline = time.time() + timeout_seconds
    latest: dict[str, Any] = {}
    while time.time() < deadline:
        url = f"{API_BASE_URL}/video_lip_sync/detail?{urllib.parse.urlencode({'id': task_id})}"
        response = _request_with_direct_retry(requests, "GET", url, headers=api_headers(access_token, json_content=False), timeout=60)
        try:
            payload = response_json(response)
            if int(response.status_code) >= 400:
                raise ToolError(f"Chanjing lipsync detail HTTP {response.status_code}: {json_safe(payload)}")
            data = require_success(payload, "video_lip_sync/detail")
            latest = data
            status = int(data.get("status") or 0)
            history.append({"checked_at": now_ms(), "status": status, "progress": data.get("progress"), "body": data})
            write_json(status_path, {"task_id": task_id, "history": history, "latest": latest})
            if status == 20 and text_value(data.get("video_url")):
                return data
            if status == 30:
                raise ToolError(f"Chanjing lipsync failed: {json_safe(data)}")
        finally:
            _close_response(response)
        time.sleep(10)
    raise ProviderTimeout(f"Chanjing lipsync timed out before returning video_url: {json_safe(latest)}")


def download_output(requests: Any, url: str, output_path: Path) -> int:
    response = _request_with_direct_retry(requests, "GET", url, stream=True, timeout=300)
    try:
        response.raise_for_status()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        byte_count = 0
        with output_path.open("wb") as out:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    byte_count += len(chunk)
                    out.write(chunk)
        return byte_count
    finally:
        _close_response(response)


def generate(context: dict[str, Any], prompt_path: Path, output_path: Path) -> dict[str, Any]:
    del prompt_path
    try:
        import requests  # type: ignore
    except Exception as exc:
        raise ToolError("requests is required for Chanjing lipsync upload and polling.") from exc
    config = dict_value(context.get("config"))
    provider = text_value(config.get("provider")).lower()
    if provider not in {"chanjing", "chanjing.cc", "cj"}:
        raise ToolError(f"Unsupported lipsync provider: {provider}/{config.get('model')}")
    credentials = credential_payload(text_value(config.get("api_key")), config)
    model_value = normalize_model(text_value(config.get("model") or "quality"))
    video_path = Path(text_value(context.get("video_path")))
    audio_path = Path(text_value(context.get("audio_path")))
    request_path = Path(text_value(context.get("request_path")))
    status_path = Path(text_value(context.get("status_path")))
    create_response_path = Path(text_value(context.get("create_response_path")))
    timeout_seconds = max(int(context.get("timeout_seconds") or 60), 120)
    started_at = time.time()

    token_info = get_access_token(requests, credentials)
    access_token = text_value(token_info.get("access_token"))
    video_asset = upload_asset(requests, access_token, video_path, "lip_sync_video", timeout=180)
    audio_asset = upload_asset(requests, access_token, audio_path, "lip_sync_audio", timeout=120)
    payload = create_payload(config, video_asset, audio_asset, model_value)
    request_record = {
        "created_at": now_ms(),
        "endpoint": f"{API_BASE_URL}/video_lip_sync/create",
        "provider": "chanjing",
        "model": model_value,
        "requested_model": text_value(config.get("model")),
        "video_path": str(video_path),
        "audio_path": str(audio_path),
        "video_size_bytes": video_path.stat().st_size,
        "audio_size_bytes": audio_path.stat().st_size,
        "token_expire_in": token_info.get("expire_in"),
        "video_asset": video_asset,
        "audio_asset": audio_asset,
        "payload": payload,
    }
    write_json(request_path, request_record)

    created = create_lipsync(requests, access_token, payload)
    task_id = text_value(created.get("task_id"))
    write_json(create_response_path, {"task_id": task_id, "body": created.get("body")})
    detail = poll_lipsync(requests, access_token, task_id, status_path, timeout_seconds=timeout_seconds)
    output_url = text_value(detail.get("video_url"))
    if not output_url:
        raise ProviderTimeout("Chanjing lipsync completed without returning video_url.")
    bytes_written = download_output(requests, output_url, output_path)
    return {
        "provider": "chanjing",
        "model": model_value,
        "requested_model": text_value(config.get("model")),
        "lipsync_id": task_id,
        "output_url": output_url,
        "preview_url": text_value(detail.get("preview_url")),
        "duration_ms": detail.get("duration"),
        "output_path": str(output_path),
        "bytes": bytes_written,
        "video_asset": video_asset,
        "audio_asset": audio_asset,
        "elapsed_seconds": round(time.time() - started_at, 3),
    }
