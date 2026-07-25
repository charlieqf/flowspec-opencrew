from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any

TEMPLATE_NAME = "Ref_05_02_Video_ChanJing_ViduQ1.md"
SOURCE_TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "Reference" / "05_02" / "Video_ChanJing_ViduQ1.md"
API_BASE_URL = "https://open-api.chanjing.cc/open/v1"
DEFAULT_MODEL = "viduq1"
CHANJING_VIDUQ1_MODELS = {"viduq1"}
VIDEO_CONTENT_TYPES = ("video/", "application/octet-stream")
VIDEO_MAX_BYTES = 1024 * 1024 * 1024

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

try:
    from opcrew_backend.services.safe_download import safe_download_to_path
except Exception:
    safe_download_to_path = None  # type: ignore[assignment]


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


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_safe(payload), ensure_ascii=False, indent=2), encoding="utf-8")


def read_json_or_empty(path: Path) -> dict[str, Any]:
    try:
        payload = read_json(path)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


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


def _template_text(context: dict[str, Any]) -> str:
    return template_snapshot_text({**context, "template_name": TEMPLATE_NAME, "template_source_path": str(SOURCE_TEMPLATE_PATH)}, TEMPLATE_NAME)


def _block(template_text: str, name: str) -> str:
    start = f"<!-- OPENCREW:{name}_START -->"
    end = f"<!-- OPENCREW:{name}_END -->"
    if start not in template_text or end not in template_text:
        raise ToolError(f"Video Chanjing Vidu Q1 template is missing block marker: {name}")
    return template_text.split(start, 1)[1].split(end, 1)[0].strip()


def _render(text: str, variables: dict[str, str]) -> str:
    rendered = text
    for key, value in variables.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", value)
    return rendered.strip()


def _join(template_text: str, blocks: list[str], variables: dict[str, str]) -> str:
    return "\n\n".join(_render(_block(template_text, name), variables) for name in blocks if text_value(name))


def prompt_text_from_dialogues(segment: dict[str, Any], dialogue_index: dict[str, dict[str, Any]]) -> str:
    lines: list[str] = []
    for dialogue_id in list_value(segment.get("dialogue_asset_keys") or segment.get("dialogue_ids")):
        item = dialogue_index.get(text_value(dialogue_id), {})
        dialogue = dict_value(item.get("dialogue"))
        text = text_value(dialogue.get("dialogue") or dialogue.get("text"))
        if text:
            lines.append(text)
    return "\n".join(lines)


def segment_is_cutaway(segment: dict[str, Any]) -> bool:
    tasks = dict_value(segment.get("tasks"))
    reason = text_value(tasks.get("lipsync_reason")).lower()
    source = text_value(tasks.get("lipsync_decision_source")).lower()
    return reason in {"user_marked_cutaway", "cutaway", "product_closeup", "no_visible_face", "no_face"} or source in {"user_marked_cutaway", "product_closeup"}


def build_prompt_package(context: dict[str, Any]) -> dict[str, Any]:
    segment = dict_value(context.get("segment"))
    dialogue_index = dict_value(context.get("dialogue_index"))
    duration = safe_float(segment.get("planned_video_duration"), 5.0)
    text = prompt_text_from_dialogues(segment, dialogue_index)
    cutaway = segment_is_cutaway(segment)
    template_text = _template_text(context)
    variables = {"dialogue_text": text, "duration_seconds": f"{duration:.1f}"}
    positive_blocks = [
        "VIDEO_CHANJING_VIDUQ1_POSITIVE_BASE",
        "VIDEO_CHANJING_VIDUQ1_DIALOGUE_CUTAWAY" if cutaway else "VIDEO_CHANJING_VIDUQ1_DIALOGUE_STANDARD",
        "VIDEO_CHANJING_VIDUQ1_CAMERA_LOCK",
        "VIDEO_CHANJING_VIDUQ1_STATIC_OBJECTS" if cutaway else "VIDEO_CHANJING_VIDUQ1_PERFORMANCE",
        "VIDEO_CHANJING_VIDUQ1_STATIC_OBJECTS" if not cutaway else "",
    ]
    negative_blocks = [
        "VIDEO_CHANJING_VIDUQ1_NEGATIVE_BASE",
        "VIDEO_CHANJING_VIDUQ1_NEGATIVE_CAMERA",
        "VIDEO_CHANJING_VIDUQ1_NEGATIVE_EXPRESSION" if not cutaway else "",
        "VIDEO_CHANJING_VIDUQ1_NEGATIVE_OBJECTS",
        "VIDEO_CHANJING_VIDUQ1_PITFALLS_APPEND_ONLY",
    ]
    positive = _join(template_text, positive_blocks, variables)
    negative = _join(template_text, negative_blocks, variables)
    prompt = _render(_block(template_text, "VIDEO_CHANJING_VIDUQ1_PROMPT"), {**variables, "positive_prompt": positive, "negative_prompt": negative})
    return {
        "schema_version": "analysis_v1_05_02_video_prompt_chanjing_viduq1_0.1",
        "prompt_type": "video_generation",
        "provider_profile": "video_chanjing_viduq1",
        "segment_id": text_value(segment.get("segment_id")),
        "dialogue_asset_keys": list_value(segment.get("dialogue_asset_keys") or segment.get("dialogue_ids")),
        "dialogue_ids": list_value(segment.get("dialogue_asset_keys") or segment.get("dialogue_ids")),
        "template_source": TEMPLATE_NAME,
        "template_snapshot_chars": len(template_text),
        "template_blocks": [name for name in positive_blocks + negative_blocks + ["VIDEO_CHANJING_VIDUQ1_PROMPT"] if text_value(name)],
        "positive_prompt": positive,
        "negative_prompt": negative,
        "prompt": prompt,
        "extracted_fields": {"dialogue_text": text, "duration": duration, "cutaway": cutaway},
    }


def write_prompt_package(prompt_dir: Path, asset_key: str, package: dict[str, Any]) -> Path:
    rendered_path = prompt_dir / f"PromptRendered_{asset_key}_VideoPrompt.json"
    write_json(rendered_path, package)
    return rendered_path


def dry_run_prompt(context: dict[str, Any], prompt_dir: Path, asset_key: str) -> dict[str, Any]:
    package = build_prompt_package(context)
    return {"prompt_path": str(write_prompt_package(prompt_dir, asset_key, package)), "package": package}


def read_prompt_text(prompt_path: Path) -> str:
    payload = read_json(prompt_path)
    if not isinstance(payload, dict):
        raise ToolError(f"Prompt file must contain a JSON object: {prompt_path}")
    prompt = text_value(payload.get("prompt"))
    if not prompt:
        raise ToolError(f"Prompt file does not contain prompt text: {prompt_path}")
    return prompt


def normalize_model(model: str) -> str:
    value = text_value(model) or DEFAULT_MODEL
    aliases = {
        "viduq1": "viduq1",
        "vidu-q1": "viduq1",
        "vidu_q1": "viduq1",
    }
    normalized = aliases.get(value.lower(), value)
    if normalized not in CHANJING_VIDUQ1_MODELS:
        raise ToolError(f"Unsupported Chanjing Vidu Q1 model: {model}. Expected one of {sorted(CHANJING_VIDUQ1_MODELS)}.")
    return normalized


def provider_video_seconds(config: dict[str, Any], duration: float) -> int:
    allowed_raw = config.get("allowed_duration_seconds") or config.get("video_duration_allowed")
    allowed = [int(item) for item in allowed_raw if isinstance(item, (int, float, str)) and str(item).strip().isdigit()] if isinstance(allowed_raw, list) else [5, 6, 10]
    allowed = sorted({item for item in allowed if item > 0}) or [5, 6, 10]
    requested = max(1, int(round(duration or safe_float(config.get("default_duration_seconds"), 5.0))))
    for item in allowed:
        if requested <= item:
            return item
    return allowed[-1]


def clarity_from_config(config: dict[str, Any]) -> int:
    value = int(safe_float(config.get("clarity") or config.get("default_clarity"), 1080))
    allowed = {720, 768, 1024, 1080, 2048, 4096}
    return value if value in allowed else 1080


def aspect_ratio_from_config(config: dict[str, Any]) -> str:
    value = text_value(config.get("aspect_ratio") or config.get("default_aspect_ratio") or "9:16")
    return value if value in {"1:1", "3:4", "4:3", "9:16", "16:9"} else "9:16"


def quality_mode_from_config(config: dict[str, Any]) -> str:
    value = text_value(config.get("quality_mode") or config.get("default_quality_mode") or "pro").lower()
    return value if value in {"std", "pro"} else "pro"


def api_base_url(config: dict[str, Any]) -> str:
    return text_value(config.get("base_url") or API_BASE_URL).rstrip("/")


def endpoint_url(config: dict[str, Any], key: str, default_path: str) -> str:
    base = api_base_url(config)
    path = text_value(config.get(key) or default_path)
    if path.startswith("http://") or path.startswith("https://"):
        return path
    if path.startswith("/open/v1/"):
        return f"{base.rsplit('/open/v1', 1)[0]}{path}"
    return f"{base}/{path.lstrip('/')}"


def query_task_url(config: dict[str, Any], unique_id: str) -> str:
    path = text_value(config.get("query_path") or "/open/v1/ai_creation/task")
    if path.endswith("/task/info"):
        path = path[:-5]
    base = api_base_url({**config, "query_path": path})
    url = endpoint_url({**config, "query_path": path}, "query_path", "/open/v1/ai_creation/task")
    return f"{url}?{urllib.parse.urlencode({'unique_id': unique_id})}"


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


def require_success(payload: dict[str, Any], label: str) -> Any:
    if int(payload.get("code") or 0) != 0:
        raise ToolError(f"Chanjing {label} failed: {json_safe(payload)}")
    return payload.get("data")


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


def api_headers(access_token: str, *, json_content: bool = True) -> dict[str, str]:
    headers = {"access_token": access_token, "Accept": "*/*"}
    if json_content:
        headers["Content-Type"] = "application/json"
    return headers


def get_access_token(requests: Any, config: dict[str, Any], credentials: dict[str, str]) -> dict[str, Any]:
    response = _request_with_direct_retry(
        requests,
        "POST",
        endpoint_url(config, "access_token_path", "/open/v1/access_token"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        json=credentials,
        timeout=30,
    )
    try:
        payload = response_json(response)
        if int(response.status_code) >= 400:
            raise ToolError(f"Chanjing access_token HTTP {response.status_code}: {json_safe(payload)}")
        data = require_success(payload, "access_token")
        data = data if isinstance(data, dict) else {}
        token = text_value(data.get("access_token"))
        if not token:
            raise ToolError(f"Chanjing access_token response missing token: {json_safe(payload)}")
        return {"access_token": token, "expire_in": data.get("expire_in"), "trace_id": payload.get("trace_id")}
    finally:
        _close_response(response)


def poll_file_ready(requests: Any, config: dict[str, Any], access_token: str, file_id: str, *, timeout_seconds: int) -> dict[str, Any]:
    if not file_id:
        raise ToolError("Chanjing upload response missing file_id.")
    deadline = time.time() + timeout_seconds
    latest: dict[str, Any] = {}
    while time.time() < deadline:
        url = f"{endpoint_url(config, 'file_detail_path', '/open/v1/common/file_detail')}?{urllib.parse.urlencode({'id': file_id})}"
        response = _request_with_direct_retry(requests, "GET", url, headers=api_headers(access_token, json_content=False), timeout=30)
        try:
            payload = response_json(response)
            if int(response.status_code) >= 400:
                raise ToolError(f"Chanjing file_detail HTTP {response.status_code}: {json_safe(payload)}")
            data = require_success(payload, "file_detail")
            latest = data if isinstance(data, dict) else {"value": data}
            status = int(latest.get("status") or 0)
            if status == 1:
                return latest
            if status in {98, 99, 100}:
                raise ToolError(f"Chanjing uploaded file is not usable: {json_safe(latest)}")
        finally:
            _close_response(response)
        time.sleep(3)
    raise ProviderTimeout(f"Chanjing uploaded file did not become ready in time: {json_safe(latest)}")


def upload_asset(requests: Any, config: dict[str, Any], access_token: str, path: Path, service: str, *, timeout: int) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        raise ToolError(f"Chanjing upload file is missing: {path}")
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    url = f"{endpoint_url(config, 'upload_url_path', '/open/v1/common/create_upload_url')}?{urllib.parse.urlencode({'service': service, 'name': path.name})}"
    response = _request_with_direct_retry(requests, "GET", url, headers=api_headers(access_token, json_content=False), timeout=30)
    try:
        upload_payload = response_json(response)
        if int(response.status_code) >= 400:
            raise ToolError(f"Chanjing create_upload_url HTTP {response.status_code}: {json_safe(upload_payload)}")
        upload_data = require_success(upload_payload, "create_upload_url")
        upload_data = upload_data if isinstance(upload_data, dict) else {}
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
    detail = poll_file_ready(requests, config, access_token, file_id, timeout_seconds=75)
    return {
        "service": service,
        "file_id": file_id,
        "full_path": text_value(upload_data.get("full_path")),
        "mime_type": text_value(upload_data.get("mime_type")) or mime,
        "name": path.name,
        "size_bytes": path.stat().st_size,
        "sha256": file_sha256(path),
        "detail": detail,
    }


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def request_payload(prompt: str, model: str, image_asset: dict[str, Any], seconds: int, config: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "start_frame": text_value(image_asset.get("full_path")),
        "ref_prompt": prompt,
        "creation_type": int(safe_float(config.get("creation_type"), 4)),
        "model_code": model,
        "aspect_ratio": aspect_ratio_from_config(config),
        "clarity": clarity_from_config(config),
        "quality_mode": quality_mode_from_config(config),
        "video_duration": seconds,
    }
    for key in ("end_frame", "preset_style", "unique_id"):
        value = text_value(config.get(key))
        if value:
            payload[key] = value
    ref_img_url = config.get("ref_img_url")
    if isinstance(ref_img_url, list) and ref_img_url:
        payload["ref_img_url"] = [text_value(item) for item in ref_img_url if text_value(item)]
    return payload


def request_fingerprint(prompt: str, model: str, reference_images: list[Path], seconds: int, payload: dict[str, Any]) -> str:
    public_payload = {
        "provider": "chanjing",
        "profile": "video_chanjing_viduq1",
        "model": model,
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "reference_image_sha256": [file_sha256(path) for path in reference_images[:1]],
        "aspect_ratio": payload.get("aspect_ratio"),
        "clarity": payload.get("clarity"),
        "quality_mode": payload.get("quality_mode"),
        "video_duration": seconds,
    }
    encoded = json.dumps(public_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def provider_task_state_path(context: dict[str, Any]) -> Path | None:
    value = text_value(context.get("provider_task_state_path"))
    return Path(value) if value else None


def matching_task_state(path: Path | None, fingerprint: str, model: str) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    state = read_json_or_empty(path)
    if text_value(state.get("fingerprint")) != fingerprint:
        return {}
    if text_value(state.get("model")) != model:
        return {}
    if text_value(state.get("provider")) not in {"", "chanjing"}:
        return {}
    if text_value(state.get("provider_profile")) not in {"", "video_chanjing_viduq1"}:
        return {}
    return state


def write_task_state(path: Path | None, payload: dict[str, Any]) -> None:
    if path is None:
        return
    current = read_json_or_empty(path)
    write_json(path, {**current, **json_safe(payload), "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})


def submit_task(requests: Any, config: dict[str, Any], access_token: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = _request_with_direct_retry(
        requests,
        "POST",
        endpoint_url(config, "submit_path", "/open/v1/ai_creation/task/submit"),
        headers=api_headers(access_token),
        json=payload,
        timeout=60,
    )
    try:
        created = response_json(response)
        if int(response.status_code) >= 400:
            raise ToolError(f"Chanjing ai_creation submit HTTP {response.status_code}: {json_safe(created)}")
        data = require_success(created, "ai_creation/task/submit")
        unique_id = text_value(data)
        if not unique_id:
            raise ToolError(f"Chanjing ai_creation submit response missing unique_id: {json_safe(created)}")
        return {"unique_id": unique_id, "body": created}
    finally:
        _close_response(response)


def first_output_url(payload: dict[str, Any]) -> str:
    for value in list_value(payload.get("output_url")):
        if text_value(value).startswith(("http://", "https://")):
            return text_value(value)
    for key in ("video_url", "url", "download_url", "outputUrl", "output_url"):
        value = payload.get(key)
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
    return ""


def poll_task(requests: Any, config: dict[str, Any], access_token: str, unique_id: str, state_path: Path | None, *, timeout_seconds: int) -> dict[str, Any]:
    history: list[dict[str, Any]] = []
    existing = read_json_or_empty(state_path) if state_path else {}
    if isinstance(existing.get("history"), list):
        history = existing["history"]
    deadline = time.time() + timeout_seconds
    latest: dict[str, Any] = {}
    while time.time() < deadline:
        response = _request_with_direct_retry(requests, "GET", query_task_url(config, unique_id), headers=api_headers(access_token, json_content=False), timeout=60)
        try:
            payload = response_json(response)
            if int(response.status_code) >= 400:
                raise ToolError(f"Chanjing ai_creation task HTTP {response.status_code}: {json_safe(payload)}")
            data = require_success(payload, "ai_creation/task")
            latest = data if isinstance(data, dict) else {"value": data}
            progress = text_value(latest.get("progress_desc"))
            err_msg = text_value(latest.get("err_msg"))
            video_url = first_output_url(latest)
            history.append({"checked_at": now_ms(), "progress_desc": progress, "err_msg": err_msg, "video_url": video_url, "body": latest})
            write_task_state(state_path, {"unique_id": unique_id, "status": progress or "polling", "history": history, "latest": latest})
            if progress.lower() == "success" and video_url:
                return latest
            if progress.lower() == "error" or err_msg:
                raise ToolError(f"Chanjing ai_creation failed: {json_safe(latest)}")
        finally:
            _close_response(response)
        time.sleep(10)
    raise ProviderTimeout(f"Chanjing ai_creation timed out before returning output_url: {json_safe(latest)}")


def download_video(requests: Any, video_url: str, output_path: Path) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if safe_download_to_path is not None:
        safe_download_to_path(
            video_url,
            output_path,
            allowed_content_types=VIDEO_CONTENT_TYPES,
            max_bytes=VIDEO_MAX_BYTES,
            timeout=600,
            headers={"User-Agent": "OpenCrew/chanjing-viduq1-video-download"},
        )
        return output_path.stat().st_size if output_path.exists() else 0
    response = _request_with_direct_retry(requests, "GET", video_url, stream=True, timeout=600)
    try:
        response.raise_for_status()
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
    try:
        import requests  # type: ignore
    except Exception as exc:
        raise ToolError("requests is required for Chanjing video upload and polling.") from exc

    config = dict_value(context.get("config"))
    provider = text_value(config.get("provider")).lower()
    if provider not in {"chanjing", "chanjing.cc", "cj"}:
        raise ToolError(f"Unsupported Chanjing Vidu Q1 provider: {provider}/{config.get('model')}")
    model = normalize_model(text_value(config.get("model") or DEFAULT_MODEL))
    credentials = credential_payload(text_value(config.get("api_key")), config)
    prompt = read_prompt_text(prompt_path)
    reference_images = [Path(path) for path in list_value(context.get("reference_images")) if Path(path).exists()]
    if not reference_images:
        raise ToolError("Chanjing Vidu Q1 video generation requires one first-frame image.")
    duration = safe_float(context.get("duration_seconds"), safe_float(config.get("default_duration_seconds"), 5.0))
    seconds = provider_video_seconds(config, duration)
    timeout_seconds = max(int(context.get("timeout_seconds") or 120), 120)
    state_path = provider_task_state_path(context)
    started_at = time.time()

    token_info = get_access_token(requests, config, credentials)
    access_token = text_value(token_info.get("access_token"))
    image_asset = upload_asset(requests, config, access_token, reference_images[0], "ai_creation", timeout=180)
    payload = request_payload(prompt, model, image_asset, seconds, config)
    fingerprint = request_fingerprint(prompt, model, reference_images, seconds, payload)
    prior_state = matching_task_state(state_path, fingerprint, model)
    prior_unique_id = text_value(prior_state.get("unique_id") or prior_state.get("provider_task_id") or prior_state.get("task_id"))
    if prior_unique_id and text_value(prior_state.get("status")).lower() in {"success", "succeeded", "completed"} and output_path.exists() and output_path.stat().st_size > 0:
        return {
            "provider": "chanjing",
            "model": model,
            "provider_profile": "video_chanjing_viduq1",
            "provider_task_id": prior_unique_id,
            "task_id": prior_unique_id,
            "unique_id": prior_unique_id,
            "requested_duration": duration,
            "duration": seconds,
            "aspect_ratio": payload.get("aspect_ratio"),
            "clarity": payload.get("clarity"),
            "quality_mode": payload.get("quality_mode"),
            "output_path": str(output_path),
            "video_url": text_value(prior_state.get("video_url_summary")),
            "elapsed_seconds": 0,
            "cached": True,
        }

    if prior_unique_id:
        unique_id = prior_unique_id
    else:
        submitted = submit_task(requests, config, access_token, payload)
        unique_id = text_value(submitted.get("unique_id"))
        write_task_state(state_path, {
            "schema_version": "analysis_v1_chanjing_viduq1_provider_task_0.1",
            "provider": "chanjing",
            "provider_profile": "video_chanjing_viduq1",
            "model": model,
            "provider_task_id": unique_id,
            "task_id": unique_id,
            "unique_id": unique_id,
            "fingerprint": fingerprint,
            "status": "submitted",
            "base_url": api_base_url(config),
            "duration": seconds,
            "requested_duration": duration,
            "image_asset": image_asset,
            "payload": payload,
            "submit_response": submitted.get("body"),
            "token_expire_in": token_info.get("expire_in"),
        })

    detail = poll_task(requests, config, access_token, unique_id, state_path, timeout_seconds=timeout_seconds)
    video_url = first_output_url(detail)
    if not video_url:
        raise ProviderTimeout("Chanjing ai_creation completed without returning output_url.")
    bytes_written = download_video(requests, video_url, output_path)
    write_task_state(state_path, {
        "status": "success",
        "provider_task_id": unique_id,
        "task_id": unique_id,
        "unique_id": unique_id,
        "fingerprint": fingerprint,
        "model": model,
        "video_url_summary": video_url,
        "output_path": str(output_path),
        "bytes": bytes_written,
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })
    return {
        "provider": "chanjing",
        "model": model,
        "provider_profile": "video_chanjing_viduq1",
        "provider_task_id": unique_id,
        "task_id": unique_id,
        "unique_id": unique_id,
        "requested_duration": duration,
        "duration": seconds,
        "aspect_ratio": payload.get("aspect_ratio"),
        "clarity": payload.get("clarity"),
        "quality_mode": payload.get("quality_mode"),
        "usage": {"video_second": seconds, "request": 1},
        "output_path": str(output_path),
        "video_url": video_url,
        "image_asset": image_asset,
        "bytes": bytes_written,
        "elapsed_seconds": round(time.time() - started_at, 3),
    }
