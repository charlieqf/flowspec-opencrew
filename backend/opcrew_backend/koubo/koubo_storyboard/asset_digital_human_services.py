from __future__ import annotations

import json
import mimetypes
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from opcrew_backend.context import now_ms
from opcrew_backend.services.media_sanitize import MediaSanitizeError, sanitize_video_file_metadata
try:
    from opcrew_model_config.media_model_config import load_stored_key
except ModuleNotFoundError:  # pragma: no cover - standalone contract-test import path
    from opcrew_backend.routes.media_model_config import load_stored_key

from .constants import ASSET_AUDIOS_REL, ASSET_IMAGES_REL, ASSET_VIDEOS_REL, ASSETS_REL
from .io_utils import safe_workspace_rel
from .usage_metering import record_storyboard_usage, stable_usage_request_id, video_usage_units


DIGITAL_HUMAN_REL = "SessionOutput/storyboard/assets/digital_human"
DIGITAL_HUMAN_AVATARS_REL = f"{DIGITAL_HUMAN_REL}/avatars"
DIGITAL_HUMAN_VOICES_REL = f"{DIGITAL_HUMAN_REL}/voices"
DIGITAL_HUMAN_AUDIO_INPUTS_REL = f"{DIGITAL_HUMAN_REL}/audio_inputs"
DIGITAL_HUMAN_SETTINGS_REL = f"{DIGITAL_HUMAN_REL}/settings.json"
HEYGEN_BASE_URL = "https://api.heygen.com"
DEFAULT_AVATAR_ENGINE_TYPE = "avatar_iv"
DEFAULT_AVATAR_MODEL_NAME = "Avatar IV"
AVATAR_V_MODEL_NAME = "Avatar V"
VIDEO_AGENT_MODEL_NAME = "HeyGen Video Agent"
VIDEO_AGENT_REVISION_PREFIX = "不要生成视频，只修改计划"


def _text(value: Any, default: str = "") -> str:
    if value is None or value == "":
        value = default
    return str(value or "").strip()


def _video_agent_revision_message(value: Any) -> str:
    message = _text(value)
    if not message:
        return ""
    return message if message.startswith(VIDEO_AGENT_REVISION_PREFIX) else f"{VIDEO_AGENT_REVISION_PREFIX}\n{message}"


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, str):
        text = value.replace("\\/", "/")
        text = urllib.parse.unquote(text)
        for marker in ("X-Api-Key", "x-api-key", "Authorization", "Bearer "):
            if marker in text:
                return "***"
        if "signature=" in text or "token=" in text or "X-Amz-Signature=" in text:
            return text.split("?", 1)[0]
    return value


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_json_safe(payload), ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _asset_payload(rel_path: str, source: str, label: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    filename = Path(rel_path).name
    suffix = Path(rel_path).suffix.lower()
    if rel_path.startswith(f"{ASSET_AUDIOS_REL}/") or suffix in {".wav", ".m4a", ".mp3", ".aac", ".ogg", ".oga", ".flac", ".opus", ".aiff", ".aif", ".caf", ".weba", ".wma"}:
        asset_type = "Audio"
    elif rel_path.startswith(f"{ASSET_IMAGES_REL}/") or suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        asset_type = "Image"
    else:
        asset_type = "Video"
    asset = {
        "id": rel_path,
        "path": rel_path,
        "label": label or filename,
        "filename": filename,
        "asset_type": asset_type,
        "kind": asset_type.lower(),
        "source": source,
        "created_at": now_ms(),
    }
    if extra:
        asset.update(extra)
    return asset


def _upsert_asset_manifest(workspace: Path, asset: dict[str, Any]) -> None:
    store = _read_json(workspace / ASSETS_REL)
    items = store.get("assets") if isinstance(store.get("assets"), list) else []
    path = _text(asset.get("path"))
    asset_id = _text(asset.get("id"), path)
    items = [
        item for item in items
        if not isinstance(item, dict) or (_text(item.get("path")) != path and _text(item.get("id")) != asset_id)
    ]
    items.append(asset)
    _write_json(workspace / ASSETS_REL, {"assets": items, "updated_at": now_ms()})


def _heygen_key(ctx: Any) -> str:
    return _text(load_stored_key(ctx, "digital-human", "heygen"))


def _json_request(api_key: str, method: str, path: str, payload: dict[str, Any] | None = None, params: dict[str, Any] | None = None, timeout: int = 60) -> dict[str, Any]:
    query = ""
    clean_params = {key: value for key, value in (params or {}).items() if value not in (None, "")}
    if clean_params:
        query = "?" + urllib.parse.urlencode(clean_params)
    body = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8") if method.upper() not in {"GET", "DELETE"} else None
    req = urllib.request.Request(
        f"{HEYGEN_BASE_URL}{path}{query}",
        data=body,
        method=method.upper(),
        headers={
            "x-api-key": api_key,
            "X-Api-Key": api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as res:
            raw = res.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2000]
        raise HTTPException(status_code=502, detail=f"HeyGen {method.upper()} {path} failed: HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise HTTPException(status_code=502, detail=f"HeyGen network request failed: {exc.reason}") from exc
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        data = {"raw_text": raw}
    return data if isinstance(data, dict) else {"data": data}


def _is_auto_proceed_schema_error(exc: HTTPException) -> bool:
    detail = _text(getattr(exc, "detail", ""))
    return "auto_proceed" in detail and ("Extra inputs are not permitted" in detail or "invalid_parameter" in detail)


def _multipart_upload(api_key: str, path: Path, timeout: int = 120) -> dict[str, Any]:
    boundary = f"----OpenCrewHeyGen{uuid.uuid4().hex}"
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    file_bytes = path.read_bytes()
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
        f"Content-Type: {mime}\r\n\r\n"
    ).encode("utf-8")
    tail = f"\r\n--{boundary}--\r\n".encode("utf-8")
    body = head + file_bytes + tail
    req = urllib.request.Request(
        f"{HEYGEN_BASE_URL}/v3/assets",
        data=body,
        method="POST",
        headers={
            "x-api-key": api_key,
            "X-Api-Key": api_key,
            "Accept": "application/json",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Idempotency-Key": f"opencrew-{uuid.uuid4().hex}",
        },
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as res:
            raw = res.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2000]
        raise HTTPException(status_code=502, detail=f"HeyGen asset upload failed: HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise HTTPException(status_code=502, detail=f"HeyGen asset upload failed: {exc.reason}") from exc
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError:
        payload = {"raw_text": raw}
    return payload if isinstance(payload, dict) else {"data": payload}


def _data(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


def _asset_id(payload: dict[str, Any]) -> str:
    data = _data(payload)
    for key in ("asset_id", "id"):
        value = _text(data.get(key))
        if value:
            return value
    nested = data.get("asset") if isinstance(data.get("asset"), dict) else {}
    return _text(nested.get("asset_id") or nested.get("id"))


def _avatar_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data")
    containers = [data, payload]
    for container in containers:
        if isinstance(container, list):
            return [item for item in container if isinstance(item, dict)]
        if isinstance(container, dict):
            for key in ("avatar_items", "items", "avatars", "looks"):
                value = container.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
    return []


def _avatar_item_id(item: dict[str, Any]) -> str:
    nested = item.get("avatar_item") if isinstance(item.get("avatar_item"), dict) else {}
    return _text(item.get("id") or item.get("avatar_id") or nested.get("id"))


def _int_value(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _avatar_dimensions(item: dict[str, Any]) -> tuple[int, int]:
    nested = item.get("avatar_item") if isinstance(item.get("avatar_item"), dict) else {}
    width = _int_value(item.get("image_width") or item.get("width") or nested.get("image_width") or nested.get("width"))
    height = _int_value(item.get("image_height") or item.get("height") or nested.get("image_height") or nested.get("height"))
    return width, height


def _avatar_status(item: dict[str, Any]) -> str:
    nested = item.get("avatar_item") if isinstance(item.get("avatar_item"), dict) else {}
    return _text(item.get("status") or nested.get("status")).lower()


def _avatar_group_id(item: dict[str, Any]) -> str:
    nested = item.get("avatar_item") if isinstance(item.get("avatar_item"), dict) else {}
    return _text(item.get("group_id") or nested.get("group_id"))


def _avatar_type(item: dict[str, Any]) -> str:
    nested = item.get("avatar_item") if isinstance(item.get("avatar_item"), dict) else {}
    return _text(item.get("avatar_type") or nested.get("avatar_type")).lower()


def _avatar_supported_api_engines(item: dict[str, Any]) -> list[str]:
    nested = item.get("avatar_item") if isinstance(item.get("avatar_item"), dict) else {}
    raw = item.get("supported_api_engines")
    if not isinstance(raw, list):
        raw = nested.get("supported_api_engines")
    return [_text(value).lower() for value in raw if _text(value)] if isinstance(raw, list) else []


def _enrich_avatar_item(item: dict[str, Any], source_path: str = "", record_path: str = "") -> dict[str, Any]:
    target = dict(item)
    if source_path and not _text(target.get("source_path")):
        target["source_path"] = source_path
    if record_path and not _text(target.get("record_path")):
        target["record_path"] = record_path
    return target


def _local_avatar_items(workspace: Path) -> list[dict[str, Any]]:
    record_dir = workspace / DIGITAL_HUMAN_AVATARS_REL
    items: list[dict[str, Any]] = []
    for path in sorted(record_dir.glob("*.json"), reverse=True):
        record = _read_json(path)
        if record.get("deleted_at"):
            continue
        data = _data(record.get("result") if isinstance(record.get("result"), dict) else {})
        item = data.get("avatar_item") if isinstance(data.get("avatar_item"), dict) else {}
        if not item:
            continue
        items.append(_enrich_avatar_item(
            item,
            _text(record.get("source_path")),
            path.relative_to(workspace).as_posix(),
        ))
    return items


def _local_avatar_item_by_id(workspace: Path, avatar_id: str) -> dict[str, Any]:
    target_id = _text(avatar_id)
    if not target_id:
        return {}
    for item in _local_avatar_items(workspace):
        if _avatar_item_id(item) == target_id:
            return item
    return {}


def _mark_local_avatar_deleted(workspace: Path, avatar_id: str, delete_result: dict[str, Any]) -> int:
    target_id = _text(avatar_id)
    if not target_id:
        return 0
    deleted_at = now_ms()
    marked = 0
    record_dir = workspace / DIGITAL_HUMAN_AVATARS_REL
    for path in sorted(record_dir.glob("*.json"), reverse=True):
        record = _read_json(path)
        data = _data(record.get("result") if isinstance(record.get("result"), dict) else {})
        item = data.get("avatar_item") if isinstance(data.get("avatar_item"), dict) else {}
        if _avatar_item_id(item) != target_id:
            continue
        record["deleted_at"] = deleted_at
        record["delete_result"] = delete_result
        _write_json(path, record)
        marked += 1
    return marked


def _find_avatar_item(payload: dict[str, Any], avatar_id: str) -> dict[str, Any]:
    target_id = _text(avatar_id)
    if not target_id:
        return {}
    for item in _avatar_items(payload):
        if _avatar_item_id(item) == target_id:
            return item
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    item = data.get("avatar_item") if isinstance(data.get("avatar_item"), dict) else {}
    return item if _avatar_item_id(item) == target_id else {}


def _wait_for_avatar_ready(api_key: str, workspace: Path, avatar_id: str, group_id: str = "", avatar_type: str = "", timeout_seconds: int = 180) -> dict[str, Any]:
    target_id = _text(avatar_id)
    if not target_id:
        return {}
    local_item = _local_avatar_item_by_id(workspace, target_id)
    item_type = _text(avatar_type).lower() or _avatar_type(local_item)
    if item_type != "photo_avatar":
        return local_item
    group = _text(group_id) or _avatar_group_id(local_item)
    ready_statuses = {"completed", "complete", "ready", "active"}
    failed_statuses = {"failed", "error", "deleted"}
    deadline = time.time() + max(5, timeout_seconds)
    last_item = local_item

    while time.time() < deadline:
        params: dict[str, Any] = {"avatar_type": item_type or "photo_avatar", "limit": 50}
        if group:
            params["group_id"] = group
        payload = _json_request(api_key, "GET", "/v3/avatars/looks", params=params, timeout=30)
        item = _find_avatar_item(payload, target_id)
        if item:
            last_item = item
            if not group:
                group = _avatar_group_id(item)
            status = _avatar_status(item)
            width, height = _avatar_dimensions(item)
            if status in failed_statuses:
                raise HTTPException(status_code=502, detail=f"HeyGen Photo Avatar 处理失败：{target_id}，status={status}")
            if width > 0 and height > 0 and (not status or status in ready_statuses):
                return item
        time.sleep(5)

    status = _avatar_status(last_item)
    width, height = _avatar_dimensions(last_item)
    raise HTTPException(
        status_code=504,
        detail=(
            "HeyGen Photo Avatar 仍在处理，暂时没有可用于生成视频的图片尺寸。"
            f"请稍后重试；avatar_id={target_id}, status={status or '-'}, image_width={width}, image_height={height}"
        ),
    )


def _merge_avatar_looks_payload(payload: dict[str, Any], workspace: Path) -> dict[str, Any]:
    merged: list[dict[str, Any]] = []
    by_id: dict[str, int] = {}
    local_by_id = {_avatar_item_id(item): item for item in _local_avatar_items(workspace) if _avatar_item_id(item)}
    for item in _avatar_items(payload):
        item_id = _avatar_item_id(item)
        local = local_by_id.get(item_id, {})
        enriched = _enrich_avatar_item(item, _text(local.get("source_path")), _text(local.get("record_path")))
        if item_id:
            by_id[item_id] = len(merged)
        merged.append(enriched)
    for item in local_by_id.values():
        item_id = _avatar_item_id(item)
        if item_id and item_id not in by_id:
            by_id[item_id] = len(merged)
            merged.append(item)
    next_payload = dict(payload)
    next_payload["data"] = merged
    next_payload["local_pending_count"] = sum(1 for item in merged if _text(item.get("status")).lower() not in {"completed", "complete", "ready"})
    return next_payload


def _download_to_path(url: str, target: Path, timeout: int = 600) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "OpenCrew/1.0"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as res:
            content = res.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2000]
        raise HTTPException(status_code=502, detail=f"HeyGen video download failed: HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise HTTPException(status_code=502, detail=f"HeyGen video download failed: {exc.reason}") from exc
    if not content:
        raise HTTPException(status_code=502, detail="HeyGen video download returned an empty file")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    try:
        sanitize_video_file_metadata(target)
    except MediaSanitizeError as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=502, detail=f"Digital human video metadata sanitization failed: {exc}") from exc


def list_heygen_avatar_looks(ctx: Any, params: dict[str, Any], workspace: Path | None = None) -> dict[str, Any]:
    api_key = _heygen_key(ctx)
    if not api_key:
        raise HTTPException(status_code=400, detail="HeyGen 数字人设置未配置 API key")
    allowed = {key: params.get(key) for key in ("ownership", "avatar_type", "group_id", "limit", "token")}
    payload = _json_request(api_key, "GET", "/v3/avatars/looks", params=allowed, timeout=30)
    return _merge_avatar_looks_payload(payload, workspace) if workspace else payload


def list_heygen_voices(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    api_key = _heygen_key(ctx)
    if not api_key:
        raise HTTPException(status_code=400, detail="HeyGen 数字人设置未配置 API key")
    allowed = {key: params.get(key) for key in ("type", "engine", "language", "gender", "limit", "token")}
    return _json_request(api_key, "GET", "/v3/voices", params=allowed, timeout=30)


def create_photo_avatar(ctx: Any, workspace: Path, name: str, image_path: Path, rel_source: str = "", description: str = "") -> dict[str, Any]:
    api_key = _heygen_key(ctx)
    if not api_key:
        raise HTTPException(status_code=400, detail="HeyGen 数字人设置未配置 API key")
    upload_payload = _multipart_upload(api_key, image_path)
    asset_id = _asset_id(upload_payload)
    if not asset_id:
        raise HTTPException(status_code=502, detail="HeyGen asset upload response did not include asset id")
    payload = {
        "type": "photo",
        "name": name or image_path.stem,
        "description": _text(description, "Photo avatar generated from Asset Library image."),
        "file": {"type": "asset_id", "asset_id": asset_id},
    }
    record_path = workspace / DIGITAL_HUMAN_AVATARS_REL / f"{int(time.time())}_{uuid.uuid4().hex[:8]}.json"
    record_rel = record_path.relative_to(workspace).as_posix()
    result = _json_request(api_key, "POST", "/v3/avatars", payload, timeout=120)
    data = _data(result)
    if isinstance(data.get("avatar_item"), dict):
        data["avatar_item"] = _enrich_avatar_item(data["avatar_item"], rel_source or str(image_path), record_rel)
    _write_json(record_path, {"request": payload, "source_path": rel_source or str(image_path), "heygen_asset_id": asset_id, "result": result, "created_at": now_ms()})
    return {"ok": True, "result": result, "heygen_asset_id": asset_id, "record_path": record_rel}


def delete_heygen_avatar_look(ctx: Any, workspace: Path, avatar_id: str) -> dict[str, Any]:
    api_key = _heygen_key(ctx)
    if not api_key:
        raise HTTPException(status_code=400, detail="HeyGen 数字人设置未配置 API key")
    target_id = _text(avatar_id)
    if not target_id:
        raise HTTPException(status_code=400, detail="Avatar id is required")
    encoded_id = urllib.parse.quote(target_id, safe="")
    result = _json_request(api_key, "DELETE", f"/v3/avatars/looks/{encoded_id}", timeout=60)
    marked = _mark_local_avatar_deleted(workspace, target_id, result)
    return {"ok": True, "avatar_id": target_id, "result": result, "deleted_local_records": marked}


def clone_heygen_voice(ctx: Any, workspace: Path, voice_name: str, audio_path: Path, rel_source: str = "", language: str = "", remove_background_noise: bool = True) -> dict[str, Any]:
    api_key = _heygen_key(ctx)
    if not api_key:
        raise HTTPException(status_code=400, detail="HeyGen 数字人设置未配置 API key")
    upload_payload = _multipart_upload(api_key, audio_path)
    asset_id = _asset_id(upload_payload)
    if not asset_id:
        raise HTTPException(status_code=502, detail="HeyGen asset upload response did not include asset id")
    payload: dict[str, Any] = {
        "audio": {"type": "asset_id", "asset_id": asset_id},
        "voice_name": voice_name or audio_path.stem,
        "remove_background_noise": bool(remove_background_noise),
    }
    if language:
        payload["language"] = language
    result = _json_request(api_key, "POST", "/v3/voices/clone", payload, timeout=120)
    record_path = workspace / DIGITAL_HUMAN_VOICES_REL / f"{int(time.time())}_{uuid.uuid4().hex[:8]}.json"
    _write_json(record_path, {"request": payload, "source_path": rel_source or str(audio_path), "heygen_asset_id": asset_id, "result": result, "created_at": now_ms()})
    return {"ok": True, "result": result, "heygen_asset_id": asset_id, "record_path": record_path.relative_to(workspace).as_posix()}


def default_digital_human_settings() -> dict[str, Any]:
    return {
        "confirm_before_generating": "always",
        "aspect": "9:16",
        "count": 1,
        "generation_model": DEFAULT_AVATAR_ENGINE_TYPE,
        "model_name": DEFAULT_AVATAR_MODEL_NAME,
        "engine_type": DEFAULT_AVATAR_ENGINE_TYPE,
        "selected_avatar_id": "",
        "selected_voice_id": "",
        "selected_audio_asset_path": "",
        "generation_mode": "voice_script",
        "motion_prompt_enabled": False,
        "motion_prompt": "",
        "expressiveness": "low",
    }


def read_digital_human_settings(workspace: Path) -> dict[str, Any]:
    return {**default_digital_human_settings(), **_read_json(workspace / DIGITAL_HUMAN_SETTINGS_REL).get("settings", {})}


def save_digital_human_settings(workspace: Path, settings: dict[str, Any]) -> dict[str, Any]:
    current = read_digital_human_settings(workspace)
    next_settings = {**current, **(settings or {})}
    if next_settings.get("aspect") not in {"9:16", "16:9"}:
        next_settings["aspect"] = "9:16"
    next_settings["count"] = max(1, min(int(next_settings.get("count") or 1), 2))
    next_settings["confirm_before_generating"] = "never" if next_settings.get("confirm_before_generating") == "never" else "always"
    next_settings["generation_mode"] = "audio_file" if next_settings.get("generation_mode") == "audio_file" else "voice_script"
    generation_model = _text(next_settings.get("generation_model") or next_settings.get("engine_type"), DEFAULT_AVATAR_ENGINE_TYPE).lower()
    if generation_model not in {"avatar_v", "avatar_iv", "video_agent"}:
        generation_model = DEFAULT_AVATAR_ENGINE_TYPE
    next_settings["generation_model"] = generation_model
    next_settings["engine_type"] = generation_model
    next_settings["model_name"] = VIDEO_AGENT_MODEL_NAME if generation_model == "video_agent" else AVATAR_V_MODEL_NAME if generation_model == "avatar_v" else DEFAULT_AVATAR_MODEL_NAME
    _write_json(workspace / DIGITAL_HUMAN_SETTINGS_REL, {"settings": next_settings, "updated_at": now_ms()})
    return next_settings


def _poll_video(api_key: str, video_id: str, timeout_seconds: int = 1800) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    last: dict[str, Any] = {}
    while time.time() < deadline:
        payload = _json_request(api_key, "GET", f"/v3/videos/{urllib.parse.quote(video_id, safe='')}", timeout=60)
        data = _data(payload)
        last = data
        status = _text(data.get("status")).lower()
        if status == "completed":
            return data
        if status == "failed":
            raise HTTPException(status_code=502, detail=f"HeyGen video failed: {_text(data.get('failure_code'))} {_text(data.get('failure_message'))}".strip())
        time.sleep(10)
    raise HTTPException(status_code=504, detail=f"HeyGen video generation timed out: {video_id}. Last status: {_text(last.get('status'))}")


def _poll_video_agent(api_key: str, session_id: str, timeout_seconds: int = 1800) -> tuple[str, dict[str, Any]]:
    deadline = time.time() + min(timeout_seconds, 600)
    last: dict[str, Any] = {}
    while time.time() < deadline:
        payload = _json_request(api_key, "GET", f"/v3/video-agents/{urllib.parse.quote(session_id, safe='')}", timeout=60)
        data = _data(payload)
        last = data
        video_id = _text(data.get("video_id"))
        status = _text(data.get("status")).lower()
        if video_id:
            return video_id, data
        if status == "failed":
            raise HTTPException(status_code=502, detail=f"HeyGen video agent failed: {_text(data.get('failure_code'))} {_text(data.get('failure_message'))}".strip())
        time.sleep(5)
    raise HTTPException(status_code=504, detail=f"HeyGen video agent did not produce a video id: {session_id}. Last status: {_text(last.get('status'))}")


def _poll_video_agent_review(api_key: str, session_id: str, timeout_seconds: int = 300) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    last: dict[str, Any] = {}
    while time.time() < deadline:
        payload = _json_request(api_key, "GET", f"/v3/video-agents/{urllib.parse.quote(session_id, safe='')}", timeout=60)
        data = _data(payload)
        last = data
        status = _text(data.get("status")).lower()
        if status in {"reviewing", "waiting_for_input", "completed", "generating"}:
            return data
        if status == "failed":
            raise HTTPException(status_code=502, detail=f"HeyGen video agent failed: {_text(data.get('failure_code'))} {_text(data.get('failure_message'))}".strip())
        time.sleep(5)
    return last


def _local_audio_path(workspace: Path, rel_path: str) -> Path:
    raw = _text(rel_path)
    if not raw:
        raise HTTPException(status_code=400, detail="audio_asset_path is required for audio file mode")
    rel, path = safe_workspace_rel(workspace, raw)
    if not rel.startswith(f"{ASSET_AUDIOS_REL}/"):
        raise HTTPException(status_code=400, detail=f"Unsupported audio asset path: {raw}")
    if not path.is_file() or workspace.resolve() not in path.parents:
        raise HTTPException(status_code=400, detail=f"Audio file was not found: {rel}")
    return path


def _avatar_supports_expressiveness(payload: dict[str, Any]) -> bool:
    avatar_type = _text(payload.get("avatar_type")).lower()
    if not avatar_type:
        return bool(_text(payload.get("avatar_photo_path")))
    return avatar_type in {"photo_avatar", "image", "prompt"}


def _requested_avatar_engine_type(payload: dict[str, Any]) -> str:
    raw = _text(payload.get("generation_model") or payload.get("engine_type"), DEFAULT_AVATAR_ENGINE_TYPE).lower()
    return "avatar_v" if raw == "avatar_v" else DEFAULT_AVATAR_ENGINE_TYPE


def _payload_supported_api_engines(payload: dict[str, Any]) -> list[str]:
    raw = payload.get("supported_api_engines")
    return [_text(value).lower() for value in raw if _text(value)] if isinstance(raw, list) else []


def _avatar_engine_type(payload: dict[str, Any], avatar_item: dict[str, Any] | None = None) -> str:
    requested = _requested_avatar_engine_type(payload)
    if requested != "avatar_v":
        return DEFAULT_AVATAR_ENGINE_TYPE
    item = avatar_item or {}
    avatar_type = _text(payload.get("avatar_type")).lower() or _avatar_type(item)
    supported = _payload_supported_api_engines(payload) or _avatar_supported_api_engines(item)
    if supported and "avatar_v" not in supported:
        return DEFAULT_AVATAR_ENGINE_TYPE
    if avatar_type and avatar_type != "digital_twin":
        return DEFAULT_AVATAR_ENGINE_TYPE
    return "avatar_v"


def _avatar_model_name(engine_type: str) -> str:
    return AVATAR_V_MODEL_NAME if engine_type == "avatar_v" else DEFAULT_AVATAR_MODEL_NAME


def _apply_avatar_motion_options(request_body: dict[str, Any], payload: dict[str, Any], engine_type: str | None = None) -> None:
    engine_type = engine_type or _avatar_engine_type(payload)
    if engine_type == "avatar_v":
        request_body["engine"] = {"type": "avatar_v"}
    motion_prompt = _text(payload.get("motion_prompt"))
    if not motion_prompt and _text(payload.get("generation_mode")) == "audio_file":
        motion_prompt = _text(payload.get("prompt"))
    if motion_prompt:
        request_body["motion_prompt"] = motion_prompt[:1000]
    if engine_type == "avatar_v":
        return
    expressiveness = _text(payload.get("expressiveness"), "low").lower()
    if expressiveness in {"low", "medium", "high"} and _avatar_supports_expressiveness(payload):
        request_body["expressiveness"] = expressiveness


def _agent_orientation(aspect: str) -> str:
    return "landscape" if aspect == "16:9" else "portrait"


def _message_text(message: Any) -> str:
    if isinstance(message, str):
        return message
    if not isinstance(message, dict):
        return ""
    for key in ("content", "text", "message"):
        value = message.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            chunks = []
            for item in value:
                if isinstance(item, str):
                    chunks.append(item)
                elif isinstance(item, dict):
                    chunks.append(_text(item.get("text") or item.get("content")))
            return "\n".join(chunk for chunk in chunks if chunk)
    return ""


def _message_resource_ids(message: Any) -> list[str]:
    if not isinstance(message, dict):
        return []
    raw = message.get("resource_ids")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raw = [raw]
    seen: set[str] = set()
    result: list[str] = []
    for item in raw:
        resource_id = _text(item)
        if resource_id and resource_id not in seen:
            seen.add(resource_id)
            result.append(resource_id)
    return result


def _collect_video_agent_resources(api_key: str, session_id: str, messages: list[Any]) -> list[dict[str, Any]]:
    resource_ids: list[str] = []
    seen: set[str] = set()
    for message in messages:
        for resource_id in _message_resource_ids(message):
            if resource_id not in seen:
                seen.add(resource_id)
                resource_ids.append(resource_id)
    resources: list[dict[str, Any]] = []
    for resource_id in resource_ids:
        try:
            payload = _json_request(api_key, "GET", f"/v3/video-agents/{urllib.parse.quote(session_id, safe='')}/resources/{urllib.parse.quote(resource_id, safe='')}", timeout=60)
            resource = _data(payload)
            if isinstance(resource, dict):
                resource.setdefault("request_resource_id", resource_id)
                resources.append(resource)
        except HTTPException as exc:
            resources.append({"resource_id": resource_id, "error": exc.detail, "status_code": exc.status_code})
    return resources


def _message_time(message: Any) -> int:
    if not isinstance(message, dict):
        return 0
    try:
        return int(message.get("created_at") or message.get("createdAt") or 0)
    except Exception:
        return 0


def _dedupe_video_agent_messages(messages: list[Any]) -> list[Any]:
    seen: set[str] = set()
    result: list[Any] = []
    for message in sorted(messages, key=_message_time):
        if not isinstance(message, dict):
            continue
        key = "|".join([
            _text(message.get("role")).lower(),
            " ".join(_message_text(message).split()),
            ",".join(_message_resource_ids(message)),
        ])
        if key in seen:
            continue
        seen.add(key)
        result.append(message)
    return result


def _video_agent_plan_text(messages: list[Any]) -> str:
    model_messages = [
        message
        for message in messages
        if isinstance(message, dict) and _text(message.get("role")).lower() in {"model", "assistant", "agent"} and _message_text(message)
    ]
    if model_messages:
        latest = max(model_messages, key=_message_time)
        return _message_text(latest).strip()
    return "\n\n".join(_message_text(message) for message in messages if _message_text(message)).strip()


def _video_agent_snapshot(api_key: str, session_id: str, session: dict[str, Any] | None = None) -> dict[str, Any]:
    current = session
    if current is None:
        payload = _json_request(api_key, "GET", f"/v3/video-agents/{urllib.parse.quote(session_id, safe='')}", timeout=60)
        current = _data(payload)
    if not isinstance(current, dict):
        current = {}
    messages = _dedupe_video_agent_messages(current.get("messages") if isinstance(current.get("messages"), list) else [])
    resources = _collect_video_agent_resources(api_key, session_id, messages)
    plan_text = _video_agent_plan_text(messages)
    return {
        "provider_session_id": _text(current.get("session_id"), session_id),
        "agent_status": _text(current.get("status")),
        "agent_progress": current.get("progress"),
        "agent_title": _text(current.get("title")),
        "provider_video_id": _text(current.get("video_id")),
        "provider_result": {key: value for key, value in current.items() if key not in {"messages"}},
        "agent_messages": messages,
        "agent_resources": resources,
        "plan_text": plan_text,
        "agent_snapshot": {
            "session": {key: value for key, value in current.items() if key not in {"messages"}},
            "messages": messages,
            "resources": resources,
            "plan_text": plan_text,
            "updated_at": now_ms(),
        },
    }


def _write_video_agent_record(workspace: Path, task: dict[str, Any], request_id: str, request_body: dict[str, Any], snapshot: dict[str, Any], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    record_path = workspace / DIGITAL_HUMAN_REL / "agents" / f"{request_id}.json"
    request_record = {
        "request_id": request_id,
        "task_id": int(task.get("id") or 0),
        "session_id": int(task.get("session_id") or 0),
        "provider": "heygen",
        "model": VIDEO_AGENT_MODEL_NAME,
        "generation_model": "video_agent",
        "agent_mode": "chat",
        "request": request_body,
        **snapshot,
        "created_at": now_ms(),
    }
    if extra:
        request_record.update(extra)
    _write_json(record_path, request_record)
    return {"ok": True, **request_record, "record_path": record_path.relative_to(workspace).as_posix(), "asset": {}, "assets": [], "outputs": [], "generated_count": 0}


def _existing_video_agent_asset(workspace: Path, provider_video_id: str) -> dict[str, Any]:
    video_id = _text(provider_video_id)
    if not video_id:
        return {}
    store = _read_json(workspace / ASSETS_REL)
    for item in store.get("assets") if isinstance(store.get("assets"), list) else []:
        if not isinstance(item, dict):
            continue
        origin = item.get("origin") if isinstance(item.get("origin"), dict) else {}
        if _text(origin.get("provider_video_id")) != video_id:
            continue
        rel = _text(item.get("path") or item.get("id"))
        if rel and (workspace / rel).is_file():
            return item
    return {}


def _materialize_video_agent_video(
    api_key: str,
    workspace: Path,
    task: dict[str, Any],
    request_id: str,
    request_body: dict[str, Any],
    snapshot: dict[str, Any],
    provider_session_id: str,
    provider_video_id: str,
    title: str = "video_agent",
    extra: dict[str, Any] | None = None,
    metering_ctx: Any | None = None,
) -> dict[str, Any]:
    existing_asset = _existing_video_agent_asset(workspace, provider_video_id)
    if existing_asset:
        record = _write_video_agent_record(workspace, task, request_id, request_body, snapshot, extra)
        return {
            **record,
            "asset": existing_asset,
            "assets": [existing_asset],
            "outputs": [_text(existing_asset.get("path"))],
            "generated_count": 1,
        }
    video = _poll_video(api_key, provider_video_id)
    video_url = _text(video.get("video_url"))
    if not video_url:
        raise HTTPException(status_code=502, detail="HeyGen completed video response did not include video_url")
    batch = str(now_ms())
    safe_title = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in _text(title, "video_agent").lower())[:48].strip("_") or "video_agent"
    output_name = f"{batch}_digital_human_agent_{safe_title}_{uuid.uuid4().hex[:8]}.mp4"
    output_rel = f"{ASSET_VIDEOS_REL}/{output_name}"
    output_path = workspace / output_rel
    _download_to_path(video_url, output_path)
    local_usage = record_storyboard_usage(
        metering_ctx,
        task,
        request_id=request_id,
        provider="heygen",
        model_id=VIDEO_AGENT_MODEL_NAME,
        modality="digital_human",
        step_id="koubo_storyboard.asset_library.digital_human.video_agent",
        units=video_usage_units(seconds=video.get("duration"), prompt=snapshot.get("plan_text") or request_body.get("prompt") or request_body.get("message") or "", reference_count=len(snapshot.get("agent_resources") or [])),
    )
    request_path = workspace / ASSET_VIDEOS_REL / f"{Path(output_name).stem}.json"
    request_record = {
        "request_id": request_id,
        "task_id": int(task.get("id") or 0),
        "session_id": int(task.get("session_id") or 0),
        "provider": "heygen",
        "model": VIDEO_AGENT_MODEL_NAME,
        "generation_model": "video_agent",
        "agent_mode": "chat",
        "provider_session_id": provider_session_id,
        "provider_video_id": provider_video_id,
        "request": request_body,
        "agent_status": snapshot.get("agent_status"),
        "agent_progress": snapshot.get("agent_progress"),
        "agent_title": snapshot.get("agent_title"),
        "agent_messages": snapshot.get("agent_messages", []),
        "agent_resources": snapshot.get("agent_resources", []),
        "agent_snapshot": snapshot.get("agent_snapshot", {}),
        "plan_text": snapshot.get("plan_text", ""),
        "provider_result": dict(video),
        "output": output_rel,
        "local_usage": local_usage,
        "local_usage_id": local_usage.get("local_usage_id", ""),
        "created_at": now_ms(),
    }
    if extra:
        request_record.update(extra)
    _write_json(request_path, request_record)
    asset = _asset_payload(output_rel, "digital_human_agent", "Digital human agent video", {
        "duration": video.get("duration"),
        "duration_seconds": video.get("duration"),
        "origin": {
            "tool": "asset_library_digital_human_agent",
            "provider": "heygen",
            "model": VIDEO_AGENT_MODEL_NAME,
            "generation_model": "video_agent",
            "agent_mode": "chat",
            "request_id": request_id,
            "local_usage_id": local_usage.get("local_usage_id", ""),
            "provider_session_id": provider_session_id,
            "provider_video_id": provider_video_id,
            "request_path": request_path.relative_to(workspace).as_posix(),
        },
    })
    _upsert_asset_manifest(workspace, asset)
    return {"ok": True, **request_record, "asset": asset, "assets": [asset], "outputs": [output_rel], "generated_count": 1}


def sync_video_agent_chat_plan(ctx: Any, workspace: Path, task: dict[str, Any], provider_session_id: str, materialize_completed: bool = True) -> dict[str, Any]:
    api_key = _heygen_key(ctx)
    if not api_key:
        raise HTTPException(status_code=400, detail="HeyGen 数字人设置未配置 API key")
    session_id = _text(provider_session_id)
    if not session_id:
        raise HTTPException(status_code=400, detail="provider_session_id is required for Video Agent session sync")
    request_id = f"koubo_asset_library_video_agent_sync_{now_ms()}_{uuid.uuid4().hex[:8]}"
    snapshot = _video_agent_snapshot(api_key, session_id)
    provider_video_id = _text(snapshot.get("provider_video_id"))
    if materialize_completed and _text(snapshot.get("agent_status")).lower() == "completed" and provider_video_id:
        return _materialize_video_agent_video(api_key, workspace, task, request_id, {"sync": True}, snapshot, session_id, provider_video_id, snapshot.get("agent_title") or "video_agent", metering_ctx=ctx)
    return _write_video_agent_record(workspace, task, request_id, {"sync": True, "materialize_completed": materialize_completed}, snapshot)


def stop_video_agent_chat_plan(ctx: Any, workspace: Path, task: dict[str, Any], provider_session_id: str) -> dict[str, Any]:
    api_key = _heygen_key(ctx)
    if not api_key:
        raise HTTPException(status_code=400, detail="HeyGen 数字人设置未配置 API key")
    session_id = _text(provider_session_id)
    if not session_id:
        raise HTTPException(status_code=400, detail="provider_session_id is required for Video Agent stop")
    request_id = f"koubo_asset_library_video_agent_stop_{now_ms()}_{uuid.uuid4().hex[:8]}"
    stop_result = _json_request(api_key, "POST", f"/v3/video-agents/{urllib.parse.quote(session_id, safe='')}/stop", {}, timeout=120)
    snapshot = _video_agent_snapshot(api_key, session_id)
    return _write_video_agent_record(workspace, task, request_id, {"stop": True}, snapshot, {"stop_result": stop_result})


def start_video_agent_chat_plan(ctx: Any, workspace: Path, task: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    api_key = _heygen_key(ctx)
    if not api_key:
        raise HTTPException(status_code=400, detail="HeyGen 数字人设置未配置 API key")
    prompt = _video_agent_revision_message(payload.get("prompt"))
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt is required for Video Agent chat mode")
    aspect = _text(payload.get("aspect"), "9:16")
    if aspect not in {"9:16", "16:9"}:
        aspect = "9:16"
    request_id = f"koubo_asset_library_video_agent_{now_ms()}_{uuid.uuid4().hex[:8]}"
    request_body: dict[str, Any] = {
        "prompt": prompt,
        "mode": "chat",
        "orientation": _agent_orientation(aspect),
        "callback_id": request_id,
    }
    avatar_id = _text(payload.get("avatar_id"))
    voice_id = _text(payload.get("voice_id"))
    if avatar_id:
        request_body["avatar_id"] = avatar_id
    if voice_id:
        request_body["voice_id"] = voice_id
    created = _json_request(api_key, "POST", "/v3/video-agents", request_body, timeout=120)
    data = _data(created)
    provider_session_id = _text(data.get("session_id") or data.get("id") or created.get("session_id"))
    if not provider_session_id:
        raise HTTPException(status_code=502, detail="HeyGen video agent response did not include session_id")
    session = _poll_video_agent_review(api_key, provider_session_id)
    snapshot = _video_agent_snapshot(api_key, provider_session_id, session)
    return _write_video_agent_record(workspace, task, request_id, request_body, snapshot, {"avatar_id": avatar_id, "voice_id": voice_id, "aspect": aspect})


def continue_video_agent_chat(ctx: Any, workspace: Path, task: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    api_key = _heygen_key(ctx)
    if not api_key:
        raise HTTPException(status_code=400, detail="HeyGen 数字人设置未配置 API key")
    provider_session_id = _text(payload.get("provider_session_id"))
    if not provider_session_id:
        raise HTTPException(status_code=400, detail="provider_session_id is required for Video Agent chat continuation")
    confirm_generate = bool(payload.get("agent_confirm_generate"))
    message = _text(payload.get("prompt"), "Approve" if confirm_generate else "")
    if not confirm_generate:
        message = _video_agent_revision_message(message)
    if not message:
        raise HTTPException(status_code=400, detail="Message is required for Video Agent chat continuation")
    request_id = f"koubo_asset_library_video_agent_continue_{now_ms()}_{uuid.uuid4().hex[:8]}"
    request_body: dict[str, Any] = {"message": message, "auto_proceed": False}
    try:
        sent = _json_request(api_key, "POST", f"/v3/video-agents/{urllib.parse.quote(provider_session_id, safe='')}", request_body, timeout=120)
    except HTTPException as exc:
        if not _is_auto_proceed_schema_error(exc):
            raise
        request_body = {"message": message}
        sent = _json_request(api_key, "POST", f"/v3/video-agents/{urllib.parse.quote(provider_session_id, safe='')}", request_body, timeout=120)
    if confirm_generate:
        provider_video_id, session = _poll_video_agent(api_key, provider_session_id)
        snapshot = _video_agent_snapshot(api_key, provider_session_id, session)
        return _materialize_video_agent_video(api_key, workspace, task, request_id, request_body, snapshot, provider_session_id, provider_video_id, payload.get("title") or "video_agent", {"send_result": sent}, metering_ctx=ctx)
    session = _poll_video_agent_review(api_key, provider_session_id)
    snapshot = _video_agent_snapshot(api_key, provider_session_id, session)
    return _write_video_agent_record(workspace, task, request_id, request_body, snapshot, {"send_result": sent})


def generate_digital_human_video(ctx: Any, workspace: Path, task: dict[str, Any], payload: dict[str, Any], index: int = 1) -> dict[str, Any]:
    api_key = _heygen_key(ctx)
    if not api_key:
        raise HTTPException(status_code=400, detail="HeyGen 数字人设置未配置 API key")
    avatar_id = _text(payload.get("avatar_id"))
    if not avatar_id:
        raise HTTPException(status_code=400, detail="Avatar is required")
    avatar_item = _wait_for_avatar_ready(
        api_key,
        workspace,
        avatar_id,
        _text(payload.get("avatar_group_id") or payload.get("group_id")),
        _text(payload.get("avatar_type")),
    )
    mode = "audio_file" if _text(payload.get("generation_mode")) == "audio_file" or _text(payload.get("audio_asset_path")) else "voice_script"
    requested_engine_type = _requested_avatar_engine_type(payload)
    engine_type = _avatar_engine_type(payload, avatar_item)
    model_name = _avatar_model_name(engine_type)
    aspect = _text(payload.get("aspect"), "9:16")
    if aspect not in {"9:16", "16:9"}:
        aspect = "9:16"
    request_id = f"koubo_asset_library_digital_human_{now_ms()}_{uuid.uuid4().hex[:8]}"
    batch = str(now_ms())
    safe_title = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in _text(payload.get("title"), "digital_human").lower())[:48].strip("_") or "digital_human"
    output_name = f"{batch}_digital_human_{index}_{safe_title}_{uuid.uuid4().hex[:8]}.mp4"
    output_rel = f"{ASSET_VIDEOS_REL}/{output_name}"
    output_path = workspace / output_rel
    provider_session_id = ""
    provider_video_id = ""
    heygen_audio_asset_id = ""
    request_body: dict[str, Any]
    if mode == "audio_file":
        audio_path = _local_audio_path(workspace, _text(payload.get("audio_asset_path")))
        upload_payload = _multipart_upload(api_key, audio_path)
        heygen_audio_asset_id = _asset_id(upload_payload)
        if not heygen_audio_asset_id:
            raise HTTPException(status_code=502, detail="HeyGen audio upload response did not include asset id")
        request_body = {
            "type": "avatar",
            "avatar_id": avatar_id,
            "title": _text(payload.get("title"), "OpenCrew Digital Human"),
            "aspect_ratio": aspect,
            "audio_asset_id": heygen_audio_asset_id,
            "output_format": "mp4",
        }
        _apply_avatar_motion_options(request_body, payload, engine_type)
        created = _json_request(api_key, "POST", "/v3/videos", request_body, timeout=120)
        provider_video_id = _text(_data(created).get("video_id") or _data(created).get("id"))
        if not provider_video_id:
            raise HTTPException(status_code=502, detail="HeyGen create video response did not include video_id")
        video = _poll_video(api_key, provider_video_id)
    else:
        voice_id = _text(payload.get("voice_id"))
        prompt = _text(payload.get("prompt"))
        if not voice_id:
            raise HTTPException(status_code=400, detail="Voice is required for text mode")
        if not prompt:
            raise HTTPException(status_code=400, detail="Prompt/script is required for text mode")
        request_body = {
            "type": "avatar",
            "avatar_id": avatar_id,
            "title": _text(payload.get("title"), "OpenCrew Digital Human"),
            "aspect_ratio": aspect,
            "script": prompt,
            "voice_id": voice_id,
            "output_format": "mp4",
            "callback_id": request_id,
        }
        _apply_avatar_motion_options(request_body, payload, engine_type)
        created = _json_request(api_key, "POST", "/v3/videos", request_body, timeout=120)
        provider_video_id = _text(_data(created).get("video_id") or _data(created).get("id"))
        if not provider_video_id:
            raise HTTPException(status_code=502, detail="HeyGen create video response did not include video_id")
        video = _poll_video(api_key, provider_video_id)
    video_url = _text(video.get("video_url"))
    if not video_url:
        raise HTTPException(status_code=502, detail="HeyGen completed video response did not include video_url")
    _download_to_path(video_url, output_path)
    local_usage = record_storyboard_usage(
        ctx,
        task,
        request_id=request_id,
        provider="heygen",
        model_id=model_name,
        modality="digital_human",
        step_id="koubo_storyboard.asset_library.digital_human.video",
        units=video_usage_units(seconds=video.get("duration"), prompt=payload.get("prompt") or "", reference_count=1),
    )
    request_path = workspace / ASSET_VIDEOS_REL / f"{Path(output_name).stem}.json"
    request_record = {
        "request_id": request_id,
        "task_id": int(task.get("id") or 0),
        "session_id": int(task.get("session_id") or 0),
        "provider": "heygen",
        "model": model_name,
        "engine_type": engine_type,
        "requested_engine_type": requested_engine_type,
        "engine_fallback_reason": "selected avatar does not support Avatar V" if requested_engine_type != engine_type else "",
        "generation_mode": mode,
        "avatar_id": avatar_id,
        "voice_id": _text(payload.get("voice_id")),
        "audio_input_path": _text(payload.get("audio_asset_path")),
        "heygen_audio_asset_id": heygen_audio_asset_id,
        "provider_session_id": provider_session_id,
        "provider_video_id": provider_video_id,
        "aspect": aspect,
        "motion_prompt": _text(payload.get("motion_prompt")),
        "expressiveness": "" if engine_type == "avatar_v" else _text(payload.get("expressiveness")),
        "request": request_body,
        "provider_result": {key: value for key, value in video.items() if key not in {"video_url", "thumbnail_url", "gif_url", "subtitle_url", "captioned_video_url"}},
        "output": output_rel,
        "local_usage": local_usage,
        "local_usage_id": local_usage.get("local_usage_id", ""),
        "created_at": now_ms(),
    }
    _write_json(request_path, request_record)
    asset = _asset_payload(output_rel, "digital_human", "Digital human video", {
        "duration": video.get("duration"),
        "duration_seconds": video.get("duration"),
        "aspect": aspect,
        "aspect_ratio": aspect,
        "origin": {
            "tool": "asset_library_digital_human_agent",
            "provider": "heygen",
            "model": request_record["model"],
            "engine_type": engine_type,
            "requested_engine_type": requested_engine_type,
            "generation_mode": mode,
            "request_id": request_id,
            "local_usage_id": local_usage.get("local_usage_id", ""),
            "avatar_id": avatar_id,
            "voice_id": _text(payload.get("voice_id")),
            "audio_input_path": _text(payload.get("audio_asset_path")),
            "heygen_audio_asset_id": heygen_audio_asset_id,
            "provider_session_id": provider_session_id,
            "provider_video_id": provider_video_id,
            "aspect": aspect,
            "request_path": request_path.relative_to(workspace).as_posix(),
        },
    })
    _upsert_asset_manifest(workspace, asset)
    return {"ok": True, **request_record, "asset": asset}
