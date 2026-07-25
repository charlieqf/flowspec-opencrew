from __future__ import annotations

import base64
import json
import mimetypes
import urllib.error
import urllib.parse
import urllib.request
import uuid

import time
from pathlib import Path
from typing import Any

TEMPLATE_NAME = "Ref_05_02_Lipsync_SyncSo.md"
SOURCE_TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "Reference" / "05_02" / "Lipsync_SyncSo.md"
SYNC_API_BASE_URL = "https://api.sync.so/v2"
SYNC_DIRECT_UPLOAD_MAX_BYTES = 20 * 1024 * 1024


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
    text = str(value or "")
    text = text.replace("\\/", "/")
    import re

    text = re.sub(r"([?&]key=)[^&\s\"'}]+", r"\1***", text, flags=re.I)
    text = re.sub(r"(api[_-]?key[\"']?\s*[:=]\s*[\"']?)[^\"',}\s]+", r"\1***", text, flags=re.I)
    text = re.sub(r"(Authorization[\"']?\s*[:=]\s*[\"']?\s*Bearer\s+)[^\"',}\s]+", r"\1***", text, flags=re.I)
    text = re.sub(r"(Bearer\s+)[A-Za-z0-9._~+/=-]{12,}", r"\1***", text, flags=re.I)
    text = re.sub(r"(x-api-key[\"']?\s*[:=]\s*[\"']?)[^\"',}\s]+", r"\1***", text, flags=re.I)
    return text


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, str):
        return redact_secret_text(value)
    return value


def first_url(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("url", "video_url", "audio_url", "download_url", "uri", "outputUrl", "output_url"):
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
        raise ToolError(f"Lipsync SyncSo template is missing block marker: {name}")
    return template_text.split(start, 1)[1].split(end, 1)[0].strip()


def build_prompt_package(context: dict[str, Any]) -> dict[str, Any]:
    template_text = _template_text(context)
    segment = dict_value(context.get("segment"))
    prompt = f"{_block(template_text, 'LIPSYNC_SYNCSO_PROMPT')}\n\n{_block(template_text, 'LIPSYNC_SYNCSO_PITFALLS_APPEND_ONLY')}"
    return {
        "schema_version": "analysis_v1_05_02_lipsync_prompt_syncso_0.1",
        "prompt_type": "lipsync_request",
        "provider_profile": "lipsync_syncso",
        "segment_id": text_value(segment.get("segment_id")),
        "dialogue_asset_keys": list_value(segment.get("dialogue_asset_keys") or segment.get("dialogue_ids")),
        "dialogue_ids": list_value(segment.get("dialogue_asset_keys") or segment.get("dialogue_ids")),
        "template_source": TEMPLATE_NAME,
        "template_snapshot_chars": len(template_text),
        "template_blocks": ["LIPSYNC_SYNCSO_PROMPT", "LIPSYNC_SYNCSO_PITFALLS_APPEND_ONLY"],
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


def sync_output_url(payload: dict[str, Any]) -> str:
    url = text_value(payload.get("outputUrl") or payload.get("output_url"))
    if url:
        return url
    for segment in list_value(payload.get("segments")):
        if isinstance(segment, dict):
            candidate = text_value(segment.get("segmentOutputUrl") or segment.get("segment_output_url"))
            if candidate:
                return candidate
    return first_url(payload)


def retry_after_seconds(response: Any, attempt: int) -> float:
    raw = ""
    try:
        raw = text_value(response.headers.get("Retry-After"))
    except Exception:
        raw = ""
    try:
        seconds = float(raw)
        if seconds > 0:
            return max(1.0, min(seconds, 90.0))
    except (TypeError, ValueError):
        pass
    return min(15.0 * attempt, 60.0)


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


def _response_payload(response: Any) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {"raw_text": response.text}
    return payload if isinstance(payload, dict) else {"body": payload}


def _sync_asset_id(payload: dict[str, Any]) -> str:
    direct = text_value(payload.get("id") or payload.get("assetId") or payload.get("asset_id"))
    if direct:
        return direct
    data = dict_value(payload.get("data"))
    return text_value(data.get("id") or data.get("assetId") or data.get("asset_id"))


def _sync_asset_content_type(path: Path, asset_type: str) -> str:
    if asset_type == "audio" and path.suffix.lower() == ".wav":
        return "audio/wav"
    return mimetypes.guess_type(path.name)[0] or ("video/mp4" if asset_type == "video" else "audio/wav")


def _upload_sync_asset(
    requests_module: Any,
    *,
    api_key: str,
    path: Path,
    asset_type: str,
    content_type: str,
) -> dict[str, Any]:
    normalized_asset_type = text_value(asset_type).lower()
    if normalized_asset_type not in {"audio", "video", "image"}:
        raise ToolError(f"Unsupported Sync.so asset type: {asset_type}")
    upload_init = _request_with_direct_retry(
        requests_module,
        "POST",
        f"{SYNC_API_BASE_URL}/assets/upload",
        headers={"x-api-key": api_key},
        json={"fileName": path.name, "contentType": content_type, "size": path.stat().st_size},
        timeout=60,
    )
    try:
        upload_payload = _response_payload(upload_init)
        if upload_init.status_code >= 400:
            raise ToolError(f"Sync.so asset upload initialization failed for {path.name}: HTTP {upload_init.status_code}: {json_safe(upload_payload)}")
    finally:
        _close_response(upload_init)
    upload_url = text_value(upload_payload.get("uploadUrl") or upload_payload.get("upload_url"))
    asset_url = text_value(upload_payload.get("url") or upload_payload.get("assetUrl") or upload_payload.get("asset_url"))
    if not upload_url or not asset_url:
        raise ToolError(f"Sync.so asset upload initialization did not include uploadUrl and url for {path.name}")

    with path.open("rb") as source:
        upload_response = _request_with_direct_retry(
            requests_module,
            "PUT",
            upload_url,
            headers={"Content-Type": content_type},
            data=source,
            timeout=300,
            on_retry=lambda: source.seek(0),
        )
    try:
        if upload_response.status_code >= 400:
            upload_error = _response_payload(upload_response)
            raise ToolError(f"Sync.so asset byte upload failed for {path.name}: HTTP {upload_response.status_code}: {json_safe(upload_error)}")
    finally:
        _close_response(upload_response)

    register_response = _request_with_direct_retry(
        requests_module,
        "POST",
        f"{SYNC_API_BASE_URL}/assets",
        headers={"x-api-key": api_key},
        json={"url": asset_url, "type": normalized_asset_type.upper(), "name": path.name},
        timeout=60,
    )
    try:
        registered = _response_payload(register_response)
        if register_response.status_code >= 400:
            raise ToolError(f"Sync.so asset registration failed for {path.name}: HTTP {register_response.status_code}: {json_safe(registered)}")
    finally:
        _close_response(register_response)
    asset_id = _sync_asset_id(registered)
    if not asset_id:
        raise ToolError(f"Sync.so asset registration did not include id for {path.name}: {json_safe(registered)}")
    return {
        "id": asset_id,
        "type": normalized_asset_type,
        "name": path.name,
        "content_type": content_type,
        "size_bytes": path.stat().st_size,
    }


def _delete_sync_assets(requests_module: Any, *, api_key: str, assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleanup: list[dict[str, Any]] = []
    for asset in reversed(assets):
        asset_id = text_value(asset.get("id"))
        if not asset_id:
            continue
        record = {
            "id": asset_id,
            "type": text_value(asset.get("type")),
            "name": text_value(asset.get("name")),
            "checked_at": now_ms(),
        }
        try:
            response = _request_with_direct_retry(
                requests_module,
                "DELETE",
                f"{SYNC_API_BASE_URL}/assets/{urllib.parse.quote(asset_id, safe='')}",
                headers={"x-api-key": api_key},
                timeout=60,
            )
            try:
                payload = _response_payload(response)
                status_code = int(response.status_code)
            finally:
                _close_response(response)
            cleanup.append({
                **record,
                "status": "deleted" if status_code < 400 or status_code == 404 else "failed",
                "status_code": status_code,
                "body": payload,
            })
        except Exception as exc:
            cleanup.append({**record, "status": "failed", "detail": redact_secret_text(str(exc))[:1000]})
    return cleanup


def _run_sync_generation(
    requests_module: Any,
    *,
    context: dict[str, Any],
    provider: str,
    model: str,
    api_key: str,
    video_path: Path,
    audio_path: Path,
    asset_inputs: list[dict[str, str]],
    status_path: Path,
    create_response_path: Path,
    output_path: Path,
    started_at: float,
) -> dict[str, Any]:
    create_attempts: list[dict[str, Any]] = []
    response = None
    created: dict[str, Any] = {}
    create_deadline = started_at + max(int(context.get("timeout_seconds") or 60), 60)
    for attempt in range(1, 5):
        if asset_inputs:
            response = _request_with_direct_retry(
                requests_module,
                "POST",
                f"{SYNC_API_BASE_URL}/generate",
                headers={"x-api-key": api_key},
                json={"model": model, "input": asset_inputs},
                timeout=120,
            )
        else:
            with video_path.open("rb") as video_file, audio_path.open("rb") as audio_file:
                response = _request_with_direct_retry(
                    requests_module,
                    "POST",
                    f"{SYNC_API_BASE_URL}/generate",
                    headers={"x-api-key": api_key},
                    data={"model": model},
                    files={"video": (video_path.name, video_file, "video/mp4"), "audio": (audio_path.name, audio_file, "audio/wav")},
                    timeout=120,
                    on_retry=lambda: (video_file.seek(0), audio_file.seek(0)),
                )
        created = _response_payload(response)
        status_code = int(response.status_code)
        retry_after = retry_after_seconds(response, attempt)
        create_attempts.append({
            "attempt": attempt,
            "checked_at": now_ms(),
            "status_code": status_code,
            "body": created,
            "retry_after_seconds": retry_after if status_code == 429 else 0,
        })
        write_json(create_response_path, {"status_code": status_code, "body": created, "attempts": create_attempts})
        _close_response(response)
        if status_code < 400:
            break
        if status_code == 429 and attempt < 4 and time.time() + retry_after < create_deadline:
            time.sleep(retry_after)
            continue
        break
    if response is None:
        raise ToolError("Sync.so create failed before receiving a response")
    if response.status_code >= 400:
        raise ToolError(f"Sync.so create failed: HTTP {response.status_code}: {json_safe(created)}")
    generation_id = text_value(created.get("id"))
    if not generation_id:
        raise ToolError(f"Sync.so create response did not include id: {created}")
    history: list[dict[str, Any]] = []
    final_payload: dict[str, Any] = {}
    deadline = time.time() + max(int(context.get("timeout_seconds") or 60), 60)
    while time.time() < deadline:
        response = _request_with_direct_retry(requests_module, "GET", f"{SYNC_API_BASE_URL}/generate/{generation_id}", headers={"x-api-key": api_key}, timeout=60)
        try:
            payload = response.json()
        except ValueError:
            payload = {"raw_text": response.text}
        finally:
            _close_response(response)
        status = text_value(payload.get("status")).upper()
        final_payload = payload
        history.append({"checked_at": now_ms(), "status_code": response.status_code, "status": status, "body": payload})
        write_json(status_path, {"generation_id": generation_id, "history": history, "latest": {"status_code": response.status_code, "body": payload}})
        if response.status_code >= 400:
            raise ToolError(f"Sync.so poll failed: HTTP {response.status_code}: {json_safe(payload)}")
        if status in {"COMPLETED", "FAILED", "REJECTED"}:
            break
        time.sleep(15)
    status = text_value(final_payload.get("status")).upper()
    if status != "COMPLETED":
        raise ProviderTimeout(f"Sync.so generation ended with {status or 'UNKNOWN'} or timed out.")
    output_url = sync_output_url(final_payload)
    if not output_url:
        raise ToolError(f"Completed Sync.so generation did not include outputUrl: {json_safe(final_payload)}")
    download = _request_with_direct_retry(requests_module, "GET", output_url, headers={"x-api-key": api_key}, stream=True, timeout=300)
    try:
        if download.status_code in {401, 403}:
            _close_response(download)
            download = _request_with_direct_retry(requests_module, "GET", output_url, stream=True, timeout=300)
        download.raise_for_status()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("wb") as out:
            for chunk in download.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    out.write(chunk)
    finally:
        _close_response(download)
    return {"provider": provider, "model": model, "generation_id": generation_id, "output_url": output_url, "output_path": str(output_path), "elapsed_seconds": round(time.time() - started_at, 3)}


def generate(context: dict[str, Any], prompt_path: Path, output_path: Path) -> dict[str, Any]:
    del prompt_path
    try:
        import requests  # type: ignore
    except Exception as exc:
        raise ToolError("requests is required for Sync.so lip-sync upload.") from exc
    config = dict_value(context.get("config"))
    provider = text_value(config.get("provider")).lower()
    if provider not in {"sync", "sync.so", "sync_so"}:
        raise ToolError(f"Unsupported lipsync provider: {provider}/{config.get('model')}")
    api_key = text_value(config.get("api_key"))
    model = text_value(config.get("model") or "lipsync-2")
    if not api_key:
        raise ToolError(f"Missing lipsync API key for {provider}/{model}.")
    video_path = Path(text_value(context.get("video_path")))
    audio_path = Path(text_value(context.get("audio_path")))
    request_path = Path(text_value(context.get("request_path")))
    status_path = Path(text_value(context.get("status_path")))
    create_response_path = Path(text_value(context.get("create_response_path")))
    video_size = video_path.stat().st_size
    audio_size = audio_path.stat().st_size
    use_asset_api = video_size >= SYNC_DIRECT_UPLOAD_MAX_BYTES or audio_size >= SYNC_DIRECT_UPLOAD_MAX_BYTES
    request_record = {
        "created_at": now_ms(),
        "endpoint": f"{SYNC_API_BASE_URL}/generate",
        "provider": provider,
        "model": model,
        "video_path": str(video_path),
        "audio_path": str(audio_path),
        "video_size_bytes": video_size,
        "audio_size_bytes": audio_size,
        "upload_mode": "asset_api" if use_asset_api else "multipart",
    }
    write_json(request_path, request_record)
    started_at = time.time()
    uploaded_assets: list[dict[str, Any]] = []
    result: dict[str, Any] | None = None
    try:
        if use_asset_api:
            for path, asset_type in ((video_path, "video"), (audio_path, "audio")):
                uploaded_assets.append(_upload_sync_asset(
                    requests,
                    api_key=api_key,
                    path=path,
                    asset_type=asset_type,
                    content_type=_sync_asset_content_type(path, asset_type),
                ))
                request_record["assets"] = uploaded_assets
                write_json(request_path, request_record)
        asset_inputs = [{"type": item["type"], "assetId": item["id"]} for item in uploaded_assets]
        result = _run_sync_generation(
            requests,
            context=context,
            provider=provider,
            model=model,
            api_key=api_key,
            video_path=video_path,
            audio_path=audio_path,
            asset_inputs=asset_inputs,
            status_path=status_path,
            create_response_path=create_response_path,
            output_path=output_path,
            started_at=started_at,
        )
        return result
    finally:
        if uploaded_assets:
            cleanup = _delete_sync_assets(requests, api_key=api_key, assets=uploaded_assets)
            request_record["asset_cleanup"] = {"finished_at": now_ms(), "items": cleanup}
            try:
                write_json(request_path, request_record)
            except Exception:
                pass
            if result is not None:
                result["asset_cleanup"] = cleanup
