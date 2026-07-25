from __future__ import annotations

import json
import time
import urllib.parse
import urllib.error
import urllib.request
import uuid
from typing import Any


PROVIDER_ID = "heygen"
DEFAULT_TARGET_MODEL = "heygen-voice-clone-v3"
DEFAULT_BASE_URL = "https://api.heygen.com"
DELETE_CONFIRMATION_DELAYS = (0.0, 0.8, 1.6, 3.0)


def post_json(url: str, api_key: str, payload: dict[str, Any], timeout: int = 120) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            body = res.read().decode("utf-8", errors="replace")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HeyGen voice clone failed: HTTP {exc.code}: {detail}") from exc


def get_json(url: str, api_key: str, timeout: int = 30) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "x-api-key": api_key,
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            body = res.read().decode("utf-8", errors="replace")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HeyGen voice request failed: HTTP {exc.code}: {detail}") from exc


def delete_json(url: str, api_key: str, timeout: int = 30) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "x-api-key": api_key,
            "Accept": "application/json",
        },
        method="DELETE",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            body = res.read().decode("utf-8", errors="replace")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        if exc.code == 404 and "voice_not_found" in detail:
            return {"data": {}, "already_deleted": True}
        raise RuntimeError(f"HeyGen voice delete failed: HTTP {exc.code}: {detail}") from exc


def _voice_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data") if isinstance(payload, dict) else []
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict) and isinstance(data.get("voices"), list):
        return [item for item in data.get("voices") if isinstance(item, dict)]
    return []


def _normalize_voice(item: dict[str, Any]) -> dict[str, Any]:
    voice_id = str(item.get("voice_id") or item.get("voice_clone_id") or item.get("id") or "").strip()
    return {
        **item,
        "voice_id": voice_id,
        "voice": voice_id,
        "voice_name": item.get("name") or item.get("voice_name") or voice_id,
        "label": item.get("name") or item.get("voice_name") or voice_id,
        "provider": PROVIDER_ID,
        "target_model": DEFAULT_TARGET_MODEL,
    }


def _voice_id(item: dict[str, Any]) -> str:
    return str(item.get("voice_id") or item.get("voice_clone_id") or item.get("voice") or item.get("id") or "").strip()


def voice_exists(
    *,
    api_key: str,
    voice_id: str,
    base_url: str = DEFAULT_BASE_URL,
    max_pages: int = 20,
    **_: Any,
) -> dict[str, Any]:
    target = str(voice_id or "").strip()
    if not target:
        raise RuntimeError("HeyGen voice_id is required.")
    token = ""
    checked = 0
    pages = 0
    while pages < max(1, int(max_pages or 20)):
        params = {"type": "private", "limit": 100}
        if token:
            params["token"] = token
        response = get_json(f"{base_url.rstrip('/')}/v3/voices?{urllib.parse.urlencode(params)}", api_key)
        page = _voice_items(response)
        checked += len(page)
        if any(_voice_id(item) == target for item in page):
            return {"exists": True, "checked": checked, "pages": pages + 1, "truncated": False}
        has_more = bool(response.get("has_more"))
        next_token = str(response.get("next_token") or "")
        if not has_more or not next_token:
            return {"exists": False, "checked": checked, "pages": pages + 1, "truncated": False}
        token = next_token
        pages += 1
    return {"exists": False, "checked": checked, "pages": pages, "truncated": True}


def create_voice(
    *,
    api_key: str,
    target_model: str,
    prefix: str,
    audio_url: str,
    language_hints: list[str] | None = None,
    remove_background_noise: bool = True,
    base_url: str = DEFAULT_BASE_URL,
    **_: Any,
) -> dict[str, Any]:
    language = (language_hints or [""])[0] or None
    voice_name = f"{prefix or 'ocadv'}_{uuid.uuid4().hex[:10]}"[:100]
    response = post_json(
        f"{base_url.rstrip('/')}/v3/voices/clone",
        api_key,
        {
            "audio": {"type": "url", "url": audio_url},
            "voice_name": voice_name,
            "language": language,
            "remove_background_noise": bool(remove_background_noise),
        },
    )
    data = response.get("data") if isinstance(response.get("data"), dict) else {}
    voice_id = str(data.get("voice_clone_id") or "").strip()
    if not voice_id:
        raise RuntimeError("HeyGen response did not include voice_clone_id.")
    return {
        "provider": PROVIDER_ID,
        "target_model": target_model or DEFAULT_TARGET_MODEL,
        "voice_id": voice_id,
        "voice": voice_id,
        "voice_name": voice_name,
        "request_id": data.get("request_id") or response.get("request_id"),
    }


def list_voices(
    *,
    api_key: str,
    prefix: str = "",
    page_index: int = 0,
    page_size: int = 100,
    base_url: str = DEFAULT_BASE_URL,
    **_: Any,
) -> dict[str, Any]:
    limit = max(1, min(int(page_size or 100), 100))
    token = ""
    voices: list[dict[str, Any]] = []
    page_count = 0
    target_page = max(0, int(page_index or 0))
    has_more = False
    next_token = ""
    while True:
        params = {"type": "private", "limit": limit}
        if token:
            params["token"] = token
        response = get_json(f"{base_url.rstrip('/')}/v3/voices?{urllib.parse.urlencode(params)}", api_key)
        page = [_normalize_voice(item) for item in _voice_items(response)]
        has_more = bool(response.get("has_more"))
        next_token = str(response.get("next_token") or "")
        if page_count >= target_page:
            voices.extend(page)
        if not has_more or not next_token or len(voices) >= limit:
            break
        token = next_token
        page_count += 1
        if page_count > target_page + 10:
            break
    return {
        "provider": PROVIDER_ID,
        "target_model": DEFAULT_TARGET_MODEL,
        "voices": voices[:limit],
        "count": len(voices[:limit]),
        "has_more": has_more,
        "next_token": next_token,
    }


def query_voice(*, api_key: str, voice_id: str, base_url: str = DEFAULT_BASE_URL, **_: Any) -> dict[str, Any]:
    voice_id = str(voice_id or "").strip()
    if not voice_id:
        raise RuntimeError("HeyGen voice_id is required.")
    response = get_json(f"{base_url.rstrip('/')}/v3/voices/{urllib.parse.quote(voice_id, safe='')}", api_key)
    data = response.get("data") if isinstance(response.get("data"), dict) else {}
    return {
        "provider": PROVIDER_ID,
        "target_model": DEFAULT_TARGET_MODEL,
        "voice": _normalize_voice(data) if data else {},
    }


def delete_voice(*, api_key: str, voice_id: str, base_url: str = DEFAULT_BASE_URL, **_: Any) -> dict[str, Any]:
    voice_id = str(voice_id or "").strip()
    if not voice_id:
        raise RuntimeError("HeyGen voice_id is required.")
    response = delete_json(f"{base_url.rstrip('/')}/v3/voices/{urllib.parse.quote(voice_id, safe='')}", api_key)
    data = response.get("data") if isinstance(response.get("data"), dict) else {}
    already_deleted = bool(response.get("already_deleted"))
    confirmation: dict[str, Any] = {"exists": False, "checked": 0, "pages": 0, "truncated": False}
    confirmation_error = ""
    confirmed_deleted = already_deleted
    if not already_deleted:
        for delay in DELETE_CONFIRMATION_DELAYS:
            if delay:
                time.sleep(delay)
            try:
                confirmation = voice_exists(api_key=api_key, voice_id=voice_id, base_url=base_url)
            except Exception as exc:
                confirmation_error = str(exc)
                break
            if not confirmation.get("exists"):
                confirmed_deleted = True
                break
    return {
        "provider": PROVIDER_ID,
        "target_model": DEFAULT_TARGET_MODEL,
        "voice_id": data.get("voice_id") or voice_id,
        "delete_requested": True,
        "deleted": confirmed_deleted,
        "delete_confirmed": confirmed_deleted,
        "already_deleted": already_deleted,
        "confirmation": confirmation,
        "confirmation_error": confirmation_error,
        "message": "" if confirmed_deleted else f"HeyGen did not confirm deletion for voice_id {voice_id}. The voice is still returned by /v3/voices.",
    }
