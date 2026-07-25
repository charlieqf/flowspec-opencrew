from __future__ import annotations

import base64
import json
import mimetypes
import urllib.error
import urllib.request
import uuid

import time
import urllib.parse
from pathlib import Path
from typing import Any

TEMPLATE_NAME = "Ref_05_02_Video_GPT.md"
SOURCE_TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "Reference" / "05_02" / "Video_GPT.md"


class ToolError(RuntimeError):
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


def download_binary(url: str, output_path: Path, headers: dict[str, str] | None = None, timeout: int = 600) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers=headers or {}, method="GET")
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                output_path.write_bytes(response.read())
            if output_path.exists() and output_path.stat().st_size > 0:
                return
        except Exception as exc:
            last_error = exc
            output_path.unlink(missing_ok=True)
            if attempt < 3:
                time.sleep(2 * attempt)
                continue
    raise ToolError(f"Download failed after 3 attempts: {redact_secret_text(str(last_error))}")


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


def _template_text(context: dict[str, Any]) -> str:
    return template_snapshot_text({**context, "template_name": TEMPLATE_NAME, "template_source_path": str(SOURCE_TEMPLATE_PATH)}, TEMPLATE_NAME)


def _block(template_text: str, name: str) -> str:
    start = f"<!-- OPENCREW:{name}_START -->"
    end = f"<!-- OPENCREW:{name}_END -->"
    if start not in template_text or end not in template_text:
        raise ToolError(f"Video GPT template is missing block marker: {name}")
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
    scene = fields["scene"]
    cutaway = bool(fields["cutaway"])
    duration = float(fields["duration"])
    text = text_value(fields["dialogue_text"])
    template_text = _template_text(context)
    variables = {
        "scene_summary": text_value(scene.get("summary") or scene.get("title")),
        "dialogue_text": text,
        "duration_seconds": f"{duration:.1f}",
    }
    positive_blocks = ["VIDEO_GPT_POSITIVE_BASE", "VIDEO_GPT_DIALOGUE_CUTAWAY" if cutaway else "VIDEO_GPT_DIALOGUE_STANDARD"]
    negative_blocks = ["VIDEO_GPT_NEGATIVE_BASE", "VIDEO_GPT_NEGATIVE_CUTAWAY" if cutaway else "", "VIDEO_GPT_PITFALLS_APPEND_ONLY"]
    positive = _join(template_text, positive_blocks, variables)
    negative = _join(template_text, negative_blocks, variables)
    prompt = _render(_block(template_text, "VIDEO_GPT_PROMPT"), {**variables, "positive_prompt": positive, "negative_prompt": negative})
    return {
        "schema_version": "analysis_v1_05_02_video_prompt_gpt_0.1",
        "prompt_type": "video_generation",
        "provider_profile": "video_gpt",
        "segment_id": text_value(segment.get("segment_id")),
        "dialogue_asset_keys": list_value(segment.get("dialogue_asset_keys") or segment.get("dialogue_ids")),
        "dialogue_ids": list_value(segment.get("dialogue_asset_keys") or segment.get("dialogue_ids")),
        "template_source": TEMPLATE_NAME,
        "template_snapshot_chars": len(template_text),
        "template_blocks": [name for name in positive_blocks + negative_blocks + ["VIDEO_GPT_PROMPT"] if text_value(name)],
        "positive_prompt": positive,
        "negative_prompt": negative,
        "prompt": prompt,
        "extracted_fields": {"dialogue_text": text, "duration": duration, "cutaway": cutaway},
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
        raise ToolError(f"Missing video API key for openai/{model}.")
    prompt = read_prompt_text(prompt_path)
    reference_images = [Path(path) for path in list_value(context.get("reference_images")) if Path(path).exists()]
    seconds = provider_video_seconds(config, duration)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    started_at = time.time()
    deadline = time.time() + max(int(context.get("timeout_seconds") or 60), 60)
    payload: dict[str, Any] = {"model": model, "prompt": prompt, "seconds": str(seconds), "size": "720x1280"}
    inline = image_inline_payload(reference_images[0] if reference_images else None)
    if inline:
        payload["input_reference"] = {"image_url": f"data:{inline['mimeType']};base64,{inline['bytesBase64Encoded']}"}
    started = post_json_request("https://api.openai.com/v1/videos", payload, {"Authorization": f"Bearer {api_key}"}, timeout=180)
    video_id = text_value(started.get("id"))
    if not video_id:
        raise ToolError(f"OpenAI video response did not include id: {json_safe(started)}")
    video_url = ""
    while time.time() < deadline:
        polled = get_json_request(f"https://api.openai.com/v1/videos/{urllib.parse.quote(video_id, safe='')}", {"Authorization": f"Bearer {api_key}"}, timeout=120)
        failure = operation_failed(polled)
        if failure:
            raise ToolError(f"OpenAI video generation failed: {failure}")
        if operation_done(polled):
            video_url = first_url(polled)
            break
        time.sleep(5)
    if video_url:
        download_binary(video_url, output_path, {"Authorization": f"Bearer {api_key}"})
    else:
        download_binary(f"https://api.openai.com/v1/videos/{urllib.parse.quote(video_id, safe='')}/content", output_path, {"Authorization": f"Bearer {api_key}"})
    return {"provider": "openai", "model": model, "requested_duration": duration, "duration": seconds, "output_path": str(output_path), "video_url": video_url, "elapsed_seconds": round(time.time() - started_at, 3)}
