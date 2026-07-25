from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

TEMPLATE_NAME = "Ref_05_02_Video_Seedance.md"
SOURCE_TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "Reference" / "05_02" / "Video_Seedance.md"
DEFAULT_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
DEFAULT_RATIO = "9:16"
DEFAULT_RESOLUTION = "720p"
DEFAULT_GENERATE_AUDIO = True
DEFAULT_MODEL = "doubao-seedance-2-0-fast-260128"
VIDEO_MAX_BYTES = 600 * 1024 * 1024
VIDEO_CONTENT_TYPES = ("video/*", "application/octet-stream")

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
    text = str(value or "")
    text = text.replace("\\/", "/")
    text = re.sub(r"([?&]key=)[^&\s\"'}]+", r"\1***", text, flags=re.I)
    text = re.sub(r"(api[_-]?key[\"']?\s*[:=]\s*[\"']?)[^\"',}\s]+", r"\1***", text, flags=re.I)
    text = re.sub(r"(Authorization[\"']?\s*[:=]\s*[\"']?\s*Bearer\s+)[^\"',}\s]+", r"\1***", text, flags=re.I)
    text = re.sub(r"(Bearer\s+)[A-Za-z0-9._~+/=-]{12,}", r"\1***", text, flags=re.I)
    text = re.sub(r"(x-api-access-key[\"']?\s*[:=]\s*[\"']?)[^\"',}\s]+", r"\1***", text, flags=re.I)
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


def url_summary(value: str) -> str:
    parsed = urllib.parse.urlsplit(str(value or ""))
    if not parsed.scheme or not parsed.netloc:
        return ""
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", "", ""))


def post_json_request(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int = 120) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", **headers}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2000]
        raise ToolError(f"HTTP {exc.code} from {redact_secret_text(url)}: {redact_secret_text(detail)}") from exc
    except urllib.error.URLError as exc:
        raise ToolError(f"Request failed for {redact_secret_text(url)}: {exc.reason}") from exc


def get_json_request(url: str, headers: dict[str, str], timeout: int = 120) -> dict[str, Any]:
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2000]
        raise ToolError(f"HTTP {exc.code} from {redact_secret_text(url)}: {redact_secret_text(detail)}") from exc
    except urllib.error.URLError as exc:
        raise ToolError(f"Request failed for {redact_secret_text(url)}: {exc.reason}") from exc


def first_url(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("video_url", "url", "download_url", "uri", "outputUrl", "output_url"):
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


def task_id_from_response(payload: dict[str, Any]) -> str:
    for container in (payload, dict_value(payload.get("output")), dict_value(payload.get("data"))):
        for key in ("id", "task_id", "taskId"):
            value = text_value(container.get(key))
            if value:
                return value
    return ""


def operation_status(payload: dict[str, Any]) -> str:
    output = dict_value(payload.get("output"))
    data = dict_value(payload.get("data"))
    return text_value(payload.get("status") or payload.get("task_status") or payload.get("state") or output.get("status") or data.get("status")).lower()


def operation_done(payload: dict[str, Any]) -> bool:
    return operation_status(payload) in {"succeeded", "success", "completed", "done", "finish", "finished"}


def operation_failed(payload: dict[str, Any]) -> str:
    if operation_status(payload) in {"failed", "error", "expired", "cancelled", "canceled", "rejected"}:
        return json.dumps(json_safe(payload), ensure_ascii=False)[:1200]
    return ""


def clamp_duration(duration: float) -> int:
    return min(15, max(4, int(round(duration or 5))))


def bool_value(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    text = text_value(value).lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def image_data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def request_fingerprint(prompt: str, model: str, reference_images: list[Path], duration_seconds: int, payload: dict[str, Any]) -> str:
    public_payload = {
        "model": model,
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "reference_image_sha256": [file_sha256(path) for path in reference_images[:1]],
        "ratio": payload.get("ratio"),
        "resolution": payload.get("resolution"),
        "duration": duration_seconds,
        "generate_audio": payload.get("generate_audio"),
    }
    encoded = json.dumps(public_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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


def prompt_text_from_dialogues(segment: dict[str, Any], dialogue_index: dict[str, dict[str, Any]]) -> str:
    lines = []
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


def base_video_fields(context: dict[str, Any]) -> dict[str, Any]:
    segment = dict_value(context.get("segment"))
    shot = dict_value(context.get("shot"))
    scene = dict_value(context.get("scene"))
    dialogue_index = dict_value(context.get("dialogue_index"))
    duration = safe_float(segment.get("planned_video_duration"), 5.0)
    return {
        "segment": segment,
        "shot": shot,
        "scene": scene,
        "dialogue_text": prompt_text_from_dialogues(segment, dialogue_index),
        "duration": duration,
        "cutaway": segment_is_cutaway(segment),
    }


def _template_text(context: dict[str, Any]) -> str:
    return template_snapshot_text({**context, "template_name": TEMPLATE_NAME, "template_source_path": str(SOURCE_TEMPLATE_PATH)}, TEMPLATE_NAME)


def _block(template_text: str, name: str) -> str:
    start = f"<!-- OPENCREW:{name}_START -->"
    end = f"<!-- OPENCREW:{name}_END -->"
    if start not in template_text or end not in template_text:
        raise ToolError(f"Video Seedance template is missing block marker: {name}")
    return template_text.split(start, 1)[1].split(end, 1)[0].strip()


def _render(text: str, variables: dict[str, str]) -> str:
    rendered = text
    for key, value in variables.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", value)
    return rendered.strip()


def _join(template_text: str, blocks: list[str], variables: dict[str, str]) -> str:
    return "\n\n".join(_render(_block(template_text, name), variables) for name in blocks if text_value(name))


def build_prompt_package(context: dict[str, Any]) -> dict[str, Any]:
    fields = base_video_fields({**context, "template_name": TEMPLATE_NAME})
    segment = fields["segment"]
    cutaway = bool(fields["cutaway"])
    duration = float(fields["duration"])
    text = text_value(fields["dialogue_text"])
    template_text = _template_text(context)
    variables = {"dialogue_text": text, "duration_seconds": f"{duration:.1f}"}
    positive_blocks = ["VIDEO_SEEDANCE_POSITIVE_BASE", "VIDEO_SEEDANCE_DIALOGUE_CUTAWAY" if cutaway else "VIDEO_SEEDANCE_DIALOGUE_STANDARD"]
    negative_blocks = ["VIDEO_SEEDANCE_NEGATIVE_BASE", "VIDEO_SEEDANCE_NEGATIVE_CUTAWAY" if cutaway else "", "VIDEO_SEEDANCE_PITFALLS_APPEND_ONLY"]
    positive = _join(template_text, positive_blocks, variables)
    negative = _join(template_text, negative_blocks, variables)
    prompt = _render(_block(template_text, "VIDEO_SEEDANCE_PROMPT"), {**variables, "positive_prompt": positive, "negative_prompt": negative})
    return {
        "schema_version": "analysis_v1_05_02_video_prompt_seedance_0.1",
        "prompt_type": "video_generation",
        "provider_profile": "video_seedance",
        "segment_id": text_value(segment.get("segment_id")),
        "dialogue_asset_keys": list_value(segment.get("dialogue_asset_keys") or segment.get("dialogue_ids")),
        "dialogue_ids": list_value(segment.get("dialogue_asset_keys") or segment.get("dialogue_ids")),
        "template_source": TEMPLATE_NAME,
        "template_snapshot_chars": len(template_text),
        "template_blocks": [name for name in positive_blocks + negative_blocks + ["VIDEO_SEEDANCE_PROMPT"] if text_value(name)],
        "positive_prompt": positive,
        "negative_prompt": negative,
        "prompt": prompt,
        "extracted_fields": {"dialogue_text": text, "duration": duration, "cutaway": cutaway},
    }


def write_prompt_package(prompt_dir: Path, asset_key: str, package: dict[str, Any]) -> Path:
    rendered_path = prompt_dir / f"PromptRendered_{asset_key}_Video.json"
    rendered_path.parent.mkdir(parents=True, exist_ok=True)
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


def seedance_request_payload(prompt: str, model: str, reference_images: list[Path], duration_seconds: int, config: dict[str, Any]) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    if reference_images:
        content.append({"type": "image_url", "image_url": {"url": image_data_url(reference_images[0]), "role": "first_frame"}})
    return {
        "model": model,
        "content": content,
        "ratio": text_value(config.get("ratio") or config.get("default_ratio") or DEFAULT_RATIO),
        "resolution": text_value(config.get("resolution") or config.get("default_resolution") or DEFAULT_RESOLUTION),
        "duration": duration_seconds,
        "generate_audio": bool_value(config.get("generate_audio"), DEFAULT_GENERATE_AUDIO),
    }


def base_url_from_config(config: dict[str, Any]) -> str:
    return text_value(config.get("base_url") or DEFAULT_BASE_URL).rstrip("/")


def download_video(video_url: str, output_path: Path) -> None:
    if safe_download_to_path is None:
        raise ToolError("Safe provider artifact downloader is unavailable; refusing to download Seedance output.")
    safe_download_to_path(
        video_url,
        output_path,
        allowed_content_types=VIDEO_CONTENT_TYPES,
        max_bytes=VIDEO_MAX_BYTES,
        timeout=600,
        headers={"User-Agent": "OpenCrew/seedance-video-download"},
    )


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
    if text_value(state.get("provider")) not in {"", "bytedance"}:
        return {}
    return state


def write_task_state(path: Path | None, payload: dict[str, Any]) -> None:
    if path is None:
        return
    current = read_json_or_empty(path)
    write_json(path, {**current, **json_safe(payload), "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})


def generate(context: dict[str, Any], prompt_path: Path, output_path: Path) -> dict[str, Any]:
    config = dict_value(context.get("config"))
    api_key = text_value(config.get("api_key"))
    model = text_value(config.get("model") or DEFAULT_MODEL)
    duration = safe_float(context.get("duration_seconds"), 5.0)
    if not api_key:
        raise ToolError(f"Missing video API key for bytedance/{model}.")
    prompt = read_prompt_text(prompt_path)
    reference_images = [Path(path) for path in list_value(context.get("reference_images")) if Path(path).exists()]
    seconds = clamp_duration(duration)
    base_url = base_url_from_config(config)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    started_at = time.time()
    deadline = time.time() + max(int(context.get("timeout_seconds") or 120), 60)
    request_payload = seedance_request_payload(prompt, model, reference_images, seconds, config)
    fingerprint = request_fingerprint(prompt, model, reference_images, seconds, request_payload)
    state_path = provider_task_state_path(context)
    prior_state = matching_task_state(state_path, fingerprint, model)
    prior_task_id = text_value(prior_state.get("provider_task_id") or prior_state.get("task_id"))
    if prior_task_id and text_value(prior_state.get("status")) == "succeeded" and output_path.exists() and output_path.stat().st_size > 0:
        return {
            "provider": "bytedance",
            "model": model,
            "provider_profile": "video_seedance",
            "provider_task_id": prior_task_id,
            "task_id": prior_task_id,
            "requested_duration": duration,
            "duration": seconds,
            "ratio": request_payload["ratio"],
            "resolution": request_payload["resolution"],
            "generate_audio": request_payload["generate_audio"],
            "output_path": str(output_path),
            "video_url": text_value(prior_state.get("video_url_summary")),
            "elapsed_seconds": round(time.time() - started_at, 3),
            "cached": True,
        }
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    if prior_task_id:
        task_id = prior_task_id
    else:
        started = post_json_request(f"{base_url}/contents/generations/tasks", request_payload, headers, timeout=120)
        task_id = task_id_from_response(started)
        if not task_id:
            raise ToolError(f"Seedance response did not include task id: {json_safe(started)}")
        write_task_state(state_path, {
            "schema_version": "analysis_v1_seedance_provider_task_0.1",
            "provider": "bytedance",
            "provider_profile": "video_seedance",
            "model": model,
            "provider_task_id": task_id,
            "task_id": task_id,
            "fingerprint": fingerprint,
            "status": operation_status(started) or "submitted",
            "base_url": url_summary(base_url),
            "ratio": request_payload["ratio"],
            "resolution": request_payload["resolution"],
            "duration": seconds,
            "generate_audio": request_payload["generate_audio"],
        })
    poll_url = f"{base_url}/contents/generations/tasks/{urllib.parse.quote(task_id, safe='')}"
    video_url = ""
    last_status = ""
    while time.time() < deadline:
        polled = get_json_request(poll_url, headers, timeout=120)
        last_status = operation_status(polled)
        write_task_state(state_path, {"provider_task_id": task_id, "task_id": task_id, "fingerprint": fingerprint, "model": model, "status": last_status or "polling"})
        failure = operation_failed(polled)
        if failure:
            write_task_state(state_path, {"status": last_status or "failed", "error": failure})
            raise ToolError(f"Seedance video generation failed: {failure}")
        if operation_done(polled):
            video_url = first_url(polled)
            break
        time.sleep(5)
    if not video_url:
        write_task_state(state_path, {"status": last_status or "timeout", "error": "timeout_or_completed_without_url"})
        raise ProviderTimeout(f"Seedance video generation timed out or completed without URL. task_id={task_id} status={last_status or 'unknown'}")
    download_video(video_url, output_path)
    write_task_state(state_path, {
        "provider_task_id": task_id,
        "task_id": task_id,
        "fingerprint": fingerprint,
        "model": model,
        "status": "succeeded",
        "video_url_summary": url_summary(video_url),
        "output_path": str(output_path),
        "bytes": output_path.stat().st_size if output_path.exists() else 0,
    })
    return {
        "provider": "bytedance",
        "model": model,
        "provider_profile": "video_seedance",
        "provider_task_id": task_id,
        "task_id": task_id,
        "requested_duration": duration,
        "duration": seconds,
        "ratio": request_payload["ratio"],
        "resolution": request_payload["resolution"],
        "generate_audio": request_payload["generate_audio"],
        "output_path": str(output_path),
        "video_url": video_url,
        "elapsed_seconds": round(time.time() - started_at, 3),
    }
