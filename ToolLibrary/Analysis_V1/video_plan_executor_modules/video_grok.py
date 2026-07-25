from __future__ import annotations

import base64
import mimetypes

import json
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

TEMPLATE_NAME = "Ref_05_02_Video_Grok.md"
SOURCE_TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "Reference" / "05_02" / "Video_Grok.md"


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


def operation_done(payload: dict[str, Any]) -> bool:
    status = text_value(payload.get("status") or payload.get("task_status") or payload.get("state")).lower()
    return status in {"succeeded", "success", "completed", "done", "finish", "finished"}


def operation_failed(payload: dict[str, Any]) -> str:
    status = text_value(payload.get("status") or payload.get("task_status") or payload.get("state")).lower()
    if status in {"failed", "error", "cancelled", "canceled", "rejected"}:
        return json.dumps(payload, ensure_ascii=False)[:1200]
    return ""


def image_inline_payload(path: Path | None) -> dict[str, str] | None:
    if not path or not path.exists():
        return None
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return {"mimeType": mime, "bytesBase64Encoded": base64.b64encode(path.read_bytes()).decode("ascii")}


def provider_video_seconds(config: dict[str, str], duration: float) -> int:
    provider = text_value(config.get("provider")).lower()
    seconds = max(1, int(round(duration or 4)))
    if provider == "xai":
        return min(15, seconds)
    if provider == "wan":
        return min(10, max(3, seconds))
    return min(8, max(4, seconds))


def normalize_video_aspect(value: Any) -> str:
    aspect = text_value(value or "9:16")
    return aspect if aspect in {"9:16", "16:9"} else "9:16"


def normalize_video_resolution(value: Any, model: str = "") -> str:
    resolution = text_value(value).lower()
    high_quality_model = text_value(model).lower().startswith("grok-imagine-video-1.5")
    if resolution in {"480p", "720p"}:
        return resolution
    if resolution == "1080p" and high_quality_model:
        return resolution
    return "1080p" if high_quality_model else "720p"


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


def base_video_fields(context: dict[str, Any]) -> dict[str, Any]:
    segment = dict_value(context.get("segment"))
    shot = dict_value(context.get("shot"))
    scene = dict_value(context.get("scene"))
    dialogue_index = dict_value(context.get("dialogue_index"))
    duration = safe_float(segment.get("planned_video_duration"), 4.0)
    return {
        "segment": segment,
        "shot": shot,
        "scene": scene,
        "dialogue_text": prompt_text_from_dialogues(segment, dialogue_index),
        "duration": duration,
        "cutaway": segment_is_cutaway(segment),
    }


def _write_prompt_package_file(prompt_dir: Path, asset_key: str, kind: str, package: dict[str, Any]) -> Path:
    rendered_path = prompt_dir / f"PromptRendered_{asset_key}_{kind}Prompt.json"
    write_json(rendered_path, package)
    return rendered_path


def read_prompt_text(prompt_path: Path) -> str:
    payload = read_json(prompt_path)
    if not isinstance(payload, dict):
        raise ToolError(f"Prompt file must contain a JSON object: {prompt_path}")
    prompt = text_value(payload.get("prompt"))
    if not prompt:
        raise ToolError(f"Prompt file does not contain prompt text: {prompt_path}")
    return prompt


def _proxy_tunnel_error(exc: BaseException) -> bool:
    reason = getattr(exc, "reason", exc)
    message = str(reason or exc).lower()
    return (
        "tunnel connection failed" in message
        or "connection refused" in message
        or ("proxy" in message and ("403" in message or "forbidden" in message or "connection refused" in message))
        or "127.0.0.1:7890" in message
        or "localhost:7890" in message
    )


def _open_xai(url: str, headers: dict[str, str], timeout: int, *, method: str = "GET", data: bytes | None = None) -> Any:
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        return urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.URLError as exc:
        if not _proxy_tunnel_error(exc):
            raise
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        direct_request = urllib.request.Request(url, data=data, headers=headers, method=method)
        return opener.open(direct_request, timeout=timeout)


def _post_json_xai(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    try:
        with _open_xai(url, {"Content-Type": "application/json", **headers}, timeout, method="POST", data=body) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2000]
        raise ToolError(f"HTTP {exc.code} from {redact_secret_text(url)}: {redact_secret_text(detail)}") from exc
    except urllib.error.URLError as exc:
        raise ToolError(f"Request failed for {redact_secret_text(url)}: {exc.reason}") from exc


def _get_json_xai(url: str, headers: dict[str, str], timeout: int) -> dict[str, Any]:
    try:
        with _open_xai(url, headers, timeout, method="GET") as response:
            raw = response.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2000]
        raise ToolError(f"HTTP {exc.code} from {redact_secret_text(url)}: {redact_secret_text(detail)}") from exc
    except urllib.error.URLError as exc:
        raise ToolError(f"Request failed for {redact_secret_text(url)}: {exc.reason}") from exc


def _download_xai(url: str, output_path: Path, headers: dict[str, str], timeout: int = 600) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with _open_xai(url, headers, timeout, method="GET") as response:
            with output_path.open("wb") as handle:
                shutil.copyfileobj(response, handle)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2000]
        raise ToolError(f"Download failed: HTTP {exc.code}: {redact_secret_text(detail)}") from exc
    except urllib.error.URLError as exc:
        output_path.unlink(missing_ok=True)
        raise ToolError(f"Download failed for {redact_secret_text(url)}: {exc.reason}") from exc


def _template_text(context: dict[str, Any]) -> str:
    return template_snapshot_text({**context, "template_name": TEMPLATE_NAME, "template_source_path": str(SOURCE_TEMPLATE_PATH)}, TEMPLATE_NAME)


def _block(template_text: str, name: str) -> str:
    start = f"<!-- OPENCREW:{name}_START -->"
    end = f"<!-- OPENCREW:{name}_END -->"
    if start not in template_text or end not in template_text:
        raise ToolError(f"Video Grok template is missing block marker: {name}")
    return template_text.split(start, 1)[1].split(end, 1)[0].strip()


def _has_block(template_text: str, name: str) -> bool:
    start = f"<!-- OPENCREW:{name}_START -->"
    end = f"<!-- OPENCREW:{name}_END -->"
    return start in template_text and end in template_text


def _render(text: str, variables: dict[str, str]) -> str:
    rendered = text
    for key, value in variables.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", value)
    return rendered.strip()


def _join(template_text: str, blocks: list[str], variables: dict[str, str]) -> str:
    return "\n\n".join(_render(_block(template_text, name), variables) for name in blocks if text_value(name))


def _join_present(template_text: str, blocks: list[str], variables: dict[str, str]) -> str:
    return "\n\n".join(_render(_block(template_text, name), variables) for name in blocks if text_value(name) and _has_block(template_text, name))


def build_prompt_package(context: dict[str, Any]) -> dict[str, Any]:
    fields = base_video_fields({**context, "template_name": TEMPLATE_NAME})
    segment = fields["segment"]
    shot = fields["shot"]
    scene = fields["scene"]
    cutaway = bool(fields["cutaway"])
    duration = float(fields["duration"])
    text = text_value(fields["dialogue_text"])
    template_text = _template_text(context)
    variables = {
        "shot_summary": text_value(shot.get("summary")),
        "scene_summary": text_value(scene.get("summary") or scene.get("title")),
        "dialogue_text": text,
        "duration_seconds": f"{duration:.1f}",
        "cutaway_mode": "product_only_cutaway" if cutaway else "talking_head_or_standard_frame",
    }
    speech_block = "VIDEO_GROK_SPEECH_CUTAWAY" if cutaway else "VIDEO_GROK_SPEECH_TALKING_HEAD"
    storyboard_block = "VIDEO_GROK_STORYBOARD_CUTAWAY" if cutaway else "VIDEO_GROK_STORYBOARD_TALKING_HEAD"
    legacy_positive_block = "VIDEO_GROK_POSITIVE_CUTAWAY" if cutaway else "VIDEO_GROK_POSITIVE_TALKING_HEAD"
    split_prompt = _has_block(template_text, speech_block) and _has_block(template_text, storyboard_block)
    speech_blocks = [speech_block] if split_prompt else []
    storyboard_blocks = [storyboard_block] if split_prompt else [legacy_positive_block]
    context_blocks = ["VIDEO_GROK_CONTEXT"]
    negative_blocks = ["VIDEO_GROK_NEGATIVE_BASE"]
    mode_negative_block = "VIDEO_GROK_NEGATIVE_CUTAWAY" if cutaway else "VIDEO_GROK_NEGATIVE_TALKING_HEAD"
    if _has_block(template_text, mode_negative_block):
        negative_blocks.append(mode_negative_block)
    speech = _join_present(template_text, speech_blocks, variables)
    storyboard = _join(template_text, storyboard_blocks, variables)
    context_prompt = _join(template_text, context_blocks, variables)
    positive = "\n\n".join(part for part in [speech, storyboard, context_prompt] if part)
    negative = _join(template_text, negative_blocks, variables)
    prompt = _render(
        _block(template_text, "VIDEO_GROK_PROMPT"),
        {
            **variables,
            "speech_prompt": speech,
            "storyboard_prompt": storyboard,
            "context_prompt": context_prompt,
            "positive_prompt": positive,
            "negative_prompt": negative,
        },
    )
    positive_blocks = [*speech_blocks, *storyboard_blocks, *context_blocks]
    return {
        "schema_version": "analysis_v1_05_02_video_prompt_grok_0.2",
        "prompt_type": "video_generation",
        "provider_profile": "video_grok",
        "segment_id": text_value(segment.get("segment_id")),
        "dialogue_asset_keys": list_value(segment.get("dialogue_asset_keys") or segment.get("dialogue_ids")),
        "dialogue_ids": list_value(segment.get("dialogue_asset_keys") or segment.get("dialogue_ids")),
        "template_source": TEMPLATE_NAME,
        "template_snapshot_chars": len(template_text),
        "template_blocks": [name for name in [
            "Video_Grok.FIRST_FRAME_ONLY",
            *positive_blocks,
            *negative_blocks,
            "VIDEO_GROK_PROMPT",
        ] if text_value(name)],
        "speech_prompt": speech,
        "storyboard_prompt": storyboard,
        "context_prompt": context_prompt,
        "positive_prompt": positive,
        "negative_prompt": negative,
        "prompt": prompt,
        "extracted_fields": {"shot_id": text_value(shot.get("shot_id")), "scene_id": text_value(scene.get("scene_id")), "dialogue_text": text, "duration": duration, "cutaway": cutaway},
    }


def write_prompt_package(prompt_dir: Path, asset_key: str, package: dict[str, Any]) -> Path:
    return _write_prompt_package_file(prompt_dir, asset_key, "Video", package)


def dry_run_prompt(context: dict[str, Any], prompt_dir: Path, asset_key: str) -> dict[str, Any]:
    package = build_prompt_package(context)
    return {"prompt_path": str(write_prompt_package(prompt_dir, asset_key, package)), "package": package}


def generate(context: dict[str, Any], prompt_path: Path, output_path: Path) -> dict[str, Any]:
    config = dict_value(context.get("config"))
    api_key = text_value(config.get("api_key"))
    model = text_value(config.get("model"))
    duration = safe_float(context.get("duration_seconds"), 4.0)
    if not api_key:
        raise ToolError(f"Missing video API key for xai/{model}.")
    prompt = read_prompt_text(prompt_path)
    reference_images = [Path(path) for path in list_value(context.get("reference_images")) if Path(path).exists()]
    seconds = provider_video_seconds(config, duration)
    aspect = normalize_video_aspect(context.get("aspect") or context.get("aspect_ratio") or context.get("requested_aspect"))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    started_at = time.time()
    deadline = time.time() + max(int(context.get("timeout_seconds") or 60), 60)
    resolution = normalize_video_resolution(context.get("resolution") or config.get("resolution"), model)
    payload: dict[str, Any] = {"model": model, "prompt": prompt, "duration": seconds, "aspect_ratio": aspect, "resolution": resolution}
    inline = image_inline_payload(reference_images[0] if reference_images else None)
    if inline:
        payload["image"] = {"url": f"data:{inline['mimeType']};base64,{inline['bytesBase64Encoded']}"}
    started = _post_json_xai("https://api.x.ai/v1/videos/generations", payload, {"Authorization": f"Bearer {api_key}"}, timeout=120)
    final_payload = started
    video_id = text_value(started.get("request_id") or started.get("id") or dict_value(started.get("data")).get("id"))
    video_url = first_url(started)
    while not video_url and video_id and time.time() < deadline:
        polled = _get_json_xai(f"https://api.x.ai/v1/videos/{urllib.parse.quote(video_id, safe='')}", {"Authorization": f"Bearer {api_key}"}, timeout=120)
        final_payload = polled
        failure = operation_failed(polled)
        if failure:
            raise ToolError(f"xAI video generation failed: {failure}")
        if operation_done(polled):
            video_url = first_url(polled)
            break
        time.sleep(5)
    if not video_url:
        raise ProviderTimeout("xAI video generation timed out or completed without URL.")
    _download_xai(video_url, output_path, {"Authorization": f"Bearer {api_key}"})
    usage = dict_value(final_payload.get("usage") or started.get("usage"))
    return {"provider": "xai", "model": model, "requested_duration": duration, "duration": seconds, "aspect": aspect, "resolution": resolution, "usage": usage, "output_path": str(output_path), "video_url": video_url, "elapsed_seconds": round(time.time() - started_at, 3)}
