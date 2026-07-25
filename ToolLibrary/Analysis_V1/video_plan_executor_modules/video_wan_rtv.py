from __future__ import annotations

import json
import math
import mimetypes
import re
import shutil
import subprocess
import urllib.error
import urllib.request
import uuid

import time
import urllib.parse
from pathlib import Path
from typing import Any

try:
    from PIL import Image
except Exception:
    Image = None

TEMPLATE_NAME = "Ref_05_02_Video_Wan_R2V.md"
SOURCE_TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "Reference" / "05_02" / "Video_Wan_R2V.md"
REFERENCE_VIDEO_NAME = "Video_Wan_R2V.mp4"
SOURCE_REFERENCE_VIDEO_PATH = Path(__file__).resolve().parents[1] / "Reference" / "05_02" / REFERENCE_VIDEO_NAME
WAN_RTV_MODEL_ALIASES = {"wan2.7-r2v": "wan2.7-r2v-2026-06-12"}
WAN_RTV_MAX_VIDEO_SECONDS = 10
WAN_RTV_AUDIO_DURATION_TOLERANCE_SECONDS = 13.0
WAN_RTV_REFERENCE_TOTAL_LIMIT = 5
DEFAULT_VIDEO_SIZE = "1080*1920"
VIDEO_ASPECT_RATIO_TOLERANCE = 0.04


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


def provider_video_seconds(config: dict[str, str], duration: float, audio_duration: float | None = None) -> int:
    provider = text_value(config.get("provider")).lower()
    model = normalize_wan_rtv_model(text_value(config.get("model"))).lower()
    duration_value = safe_float(audio_duration, 0.0) or safe_float(duration, 4.0)
    if provider in {"wan", "dashscope"} and model in set(WAN_RTV_MODEL_ALIASES.values()) and duration_value < WAN_RTV_MAX_VIDEO_SECONDS:
        return min(WAN_RTV_MAX_VIDEO_SECONDS, max(3, int(math.ceil(duration_value))))
    seconds = max(1, int(round(duration_value or 4)))
    if provider == "xai":
        return min(15, seconds)
    if provider == "wan":
        return min(10, max(3, seconds))
    return min(8, max(4, seconds))


def video_size(config: dict[str, Any]) -> str:
    value = text_value(config.get("size") or config.get("video_size") or config.get("default_size") or DEFAULT_VIDEO_SIZE)
    normalized = value.lower().replace("x", "*").replace("×", "*")
    return normalized if "*" in normalized else DEFAULT_VIDEO_SIZE


def normalize_wan_rtv_model(model: str) -> str:
    value = text_value(model)
    return WAN_RTV_MODEL_ALIASES.get(value.lower(), value)


def video_size_for_reference_image(reference_images: list[Path], config: dict[str, Any]) -> str:
    image_path = reference_images[0] if reference_images else None
    if image_path and image_path.exists() and image_path.is_file() and Image is not None:
        try:
            with Image.open(image_path) as image:
                width, height = image.size
            if width > height:
                return "1920*1080"
            if height > width:
                return "1080*1920"
        except Exception:
            pass
    return video_size(config)


def bundled_binary(name: str) -> str:
    for candidate in (
        shutil.which(name),
        Path(__file__).resolve().parents[3] / ".bin" / name,
        Path(__file__).resolve().parents[2] / ".bin" / name,
        Path(__file__).resolve().parents[2] / "vendor" / "static_ffmpeg" / "darwin_arm64" / name,
        Path(__file__).resolve().parents[3] / "vendor" / "static_ffmpeg" / "darwin_arm64" / name,
    ):
        if not candidate:
            continue
        path = Path(candidate)
        if path.exists() and path.is_file():
            return str(path)
    return name


def video_dimensions(path: Path) -> tuple[int, int]:
    command = [
        bundled_binary("ffprobe"),
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "json",
        str(path),
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
    except (OSError, subprocess.SubprocessError):
        return 0, 0
    if completed.returncode != 0:
        return 0, 0
    try:
        payload = json.loads(completed.stdout or "{}")
        stream = (payload.get("streams") or [{}])[0]
        return int(stream.get("width") or 0), int(stream.get("height") or 0)
    except Exception:
        return 0, 0


def aspect_for_size(size: str) -> str:
    normalized = text_value(size).lower().replace("x", "*").replace("×", "*")
    try:
        width_text, height_text = normalized.split("*", 1)
        width, height = int(float(width_text)), int(float(height_text))
    except Exception:
        width, height = 720, 1280
    ratio = width / height if height else 0
    if abs(ratio - 1.0) <= VIDEO_ASPECT_RATIO_TOLERANCE:
        return "1:1"
    return "16:9" if width > height else "9:16"


def target_ratio_for_size(size: str) -> float:
    normalized = text_value(size).lower().replace("x", "*").replace("×", "*")
    try:
        width_text, height_text = normalized.split("*", 1)
        width, height = float(width_text), float(height_text)
        return width / height if height else 720 / 1280
    except Exception:
        return 720 / 1280


def aspect_ratio_for_size(size: str) -> str:
    return aspect_for_size(size)


def resolution_for_size(size: str) -> str:
    normalized = text_value(size).lower().replace("x", "*").replace("×", "*")
    try:
        width_text, height_text = normalized.split("*", 1)
        width, height = int(float(width_text)), int(float(height_text))
    except Exception:
        width, height = 1080, 1920
    return "1080P" if max(width, height) >= 1920 else "720P"


def prompt_video_aspects(prompt: str) -> set[str]:
    markers: set[str] = set()
    for match in re.findall(r"(?<!\d)(9\s*:\s*16|16\s*:\s*9|1\s*:\s*1)(?!\d)", text_value(prompt)):
        markers.add(re.sub(r"\s+", "", match))
    return markers & {"9:16", "16:9", "1:1"}


def validate_prompt_matches_size(prompt: str, size: str) -> None:
    target_aspect = aspect_for_size(size)
    markers = prompt_video_aspects(prompt)
    conflicts = sorted(marker for marker in markers if marker != target_aspect)
    if conflicts:
        raise ToolError(f"Wan RTV prompt aspect mismatch: target size {size} implies {target_aspect}, prompt contains {', '.join(conflicts)}")


def validate_output_matches_size(output_path: Path, size: str) -> dict[str, Any]:
    width, height = video_dimensions(output_path)
    target_aspect = aspect_for_size(size)
    target_ratio = target_ratio_for_size(size)
    if width <= 0 or height <= 0:
        return {
            "output_width": width,
            "output_height": height,
            "output_aspect": "",
            "output_aspect_matches_request": False,
            "target_aspect": target_aspect,
            "target_ratio": round(target_ratio, 4),
            "aspect_audit_status": "uninspectable",
            "aspect_audit_message": f"Wan RTV output dimensions could not be inspected: {output_path}",
        }
    output_ratio = width / height
    matches = abs(output_ratio - target_ratio) <= VIDEO_ASPECT_RATIO_TOLERANCE
    output_aspect = aspect_for_size(f"{width}*{height}")
    return {
        "output_width": width,
        "output_height": height,
        "output_aspect": output_aspect,
        "output_ratio": round(output_ratio, 4),
        "output_aspect_matches_request": matches,
        "target_aspect": target_aspect,
        "target_ratio": round(target_ratio, 4),
        "aspect_audit_status": "passed" if matches else "mismatch",
        "aspect_audit_message": "" if matches else (
            "Wan RTV output aspect mismatch: "
            f"requested size {size} ratio {target_ratio:.4f}, got {width}x{height} ratio {output_ratio:.4f}"
        ),
    }


def write_provider_state(context: dict[str, Any], payload: dict[str, Any]) -> None:
    state_path = text_value(context.get("provider_task_state_path"))
    if not state_path:
        return
    try:
        write_json(Path(state_path), payload)
    except Exception:
        pass


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


def dashscope_upload_file(api_key: str, model: str, path: Path) -> str:
    query = urllib.parse.urlencode({"action": "getPolicy", "model": model})
    policy = get_json_request(f"https://dashscope.aliyuncs.com/api/v1/uploads?{query}", {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
    policy_data = dict_value(policy.get("data"))
    upload_host = text_value(policy_data.get("upload_host"))
    upload_dir = text_value(policy_data.get("upload_dir"))
    if not upload_host or not upload_dir:
        raise ToolError(f"DashScope upload policy is missing upload_host/upload_dir: {json.dumps(policy, ensure_ascii=False)[:1000]}")
    key = f"{upload_dir.rstrip('/')}/{path.name}"
    boundary = f"----OpenCrewDashScope{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    fields = {
        "OSSAccessKeyId": text_value(policy_data.get("oss_access_key_id")),
        "Signature": text_value(policy_data.get("signature")),
        "policy": text_value(policy_data.get("policy")),
        "x-oss-object-acl": text_value(policy_data.get("x_oss_object_acl") or "private"),
        "x-oss-forbid-overwrite": text_value(policy_data.get("x_oss_forbid_overwrite") or "true"),
        "key": key,
        "success_action_status": "200",
    }
    for name, value in fields.items():
        chunks.extend([f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(), value.encode("utf-8"), b"\r\n"])
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    chunks.extend([f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'.encode(), f"Content-Type: {mime}\r\n\r\n".encode(), path.read_bytes(), b"\r\n", f"--{boundary}--\r\n".encode()])
    req = urllib.request.Request(upload_host, data=b"".join(chunks), headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=180) as res:
            if res.status != 200:
                raise ToolError(f"DashScope upload failed: HTTP {res.status}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2000]
        raise ToolError(f"DashScope upload failed: HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ToolError(f"DashScope upload failed: {exc.reason}") from exc
    return f"oss://{key}"


def _template_text(context: dict[str, Any]) -> str:
    return template_snapshot_text({**context, "template_name": TEMPLATE_NAME, "template_source_path": str(SOURCE_TEMPLATE_PATH)}, TEMPLATE_NAME)


def _block(template_text: str, name: str) -> str:
    start = f"<!-- OPENCREW:{name}_START -->"
    end = f"<!-- OPENCREW:{name}_END -->"
    if start not in template_text or end not in template_text:
        raise ToolError(f"Video Wan RTV template is missing block marker: {name}")
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
    duration = float(fields["duration"])
    text = text_value(fields["dialogue_text"])
    template_text = _template_text(context)
    variables = {"dialogue_text": text, "duration_seconds": f"{duration:.1f}"}
    positive_blocks = [
        "VIDEO_WAN_R2V_POSITIVE_BASE",
        "VIDEO_WAN_R2V_DIALOGUE_TALKING_HEAD",
        "VIDEO_WAN_R2V_CAMERA_LOCK",
        "VIDEO_WAN_R2V_PERFORMANCE",
        "VIDEO_WAN_R2V_AUDIO_CONTROL",
    ]
    negative_blocks = [
        "VIDEO_WAN_R2V_NEGATIVE_BASE",
        "VIDEO_WAN_R2V_NEGATIVE_EXPRESSION",
        "VIDEO_WAN_R2V_PITFALLS_APPEND_ONLY",
    ]
    positive = _join(template_text, positive_blocks, variables)
    negative = _join(template_text, negative_blocks, variables)
    prompt = _render(_block(template_text, "VIDEO_WAN_R2V_PROMPT"), {**variables, "positive_prompt": positive, "negative_prompt": negative})
    return {
        "schema_version": "analysis_v1_05_02_video_prompt_wan_rtv_0.1",
        "prompt_type": "video_generation",
        "provider_profile": "video_wan_rtv",
        "segment_id": text_value(segment.get("segment_id")),
        "dialogue_asset_keys": list_value(segment.get("dialogue_asset_keys") or segment.get("dialogue_ids")),
        "dialogue_ids": list_value(segment.get("dialogue_asset_keys") or segment.get("dialogue_ids")),
        "template_source": TEMPLATE_NAME,
        "reference_video": REFERENCE_VIDEO_NAME,
        "template_snapshot_chars": len(template_text),
        "template_blocks": [name for name in positive_blocks + negative_blocks + ["VIDEO_WAN_R2V_PROMPT"] if text_value(name)],
        "positive_prompt": positive,
        "negative_prompt": negative,
        "prompt": prompt,
        "extracted_fields": {"dialogue_text": text, "duration": duration, "reference_video": REFERENCE_VIDEO_NAME},
    }


def write_prompt_package(prompt_dir: Path, asset_key: str, package: dict[str, Any]) -> Path:
    return _write_prompt_package_file(prompt_dir, asset_key, "Video", package)


def dry_run_prompt(context: dict[str, Any], prompt_dir: Path, asset_key: str) -> dict[str, Any]:
    package = build_prompt_package(context)
    return {"prompt_path": str(write_prompt_package(prompt_dir, asset_key, package)), "package": package}


def resolve_reference_video(context: dict[str, Any], prompt_path: Path) -> Path:
    videos = resolve_reference_videos(context, prompt_path)
    if videos:
        return videos[0]
    raise ToolError(f"Missing Wan RTV reference video: {REFERENCE_VIDEO_NAME}")


def resolve_reference_videos(context: dict[str, Any], prompt_path: Path) -> list[Path]:
    videos: list[Path] = []
    for raw_path in list_value(context.get("reference_videos")):
        candidate = Path(text_value(raw_path))
        if candidate.exists() and candidate.is_file():
            videos.append(candidate)
    if videos:
        return videos
    if "reference_videos" in context:
        return []
    working_candidate = prompt_path.parent.parent / "Working" / REFERENCE_VIDEO_NAME
    if working_candidate.exists() and working_candidate.is_file():
        return [working_candidate]
    if SOURCE_REFERENCE_VIDEO_PATH.exists() and SOURCE_REFERENCE_VIDEO_PATH.is_file():
        return [SOURCE_REFERENCE_VIDEO_PATH]
    return []


def generate(context: dict[str, Any], prompt_path: Path, output_path: Path) -> dict[str, Any]:
    config = dict_value(context.get("config"))
    api_key = text_value(config.get("api_key"))
    configured_model = text_value(config.get("model"))
    model = normalize_wan_rtv_model(configured_model)
    duration = safe_float(context.get("duration_seconds"), 4.0)
    if not api_key:
        raise ToolError(f"Missing video API key for wan/{model}.")
    prompt = read_prompt_text(prompt_path)
    reference_images = [Path(path) for path in list_value(context.get("reference_images")) if Path(path).exists()]
    reference_videos = resolve_reference_videos(context, prompt_path)
    reference_total = len(reference_images) + len(reference_videos)
    if reference_total < 1:
        raise ToolError("Wan RTV requires at least one reference image or reference video.")
    if reference_total > WAN_RTV_REFERENCE_TOTAL_LIMIT:
        raise ToolError(f"Wan RTV supports at most {WAN_RTV_REFERENCE_TOTAL_LIMIT} total reference images/videos.")
    audio_duration = safe_float(context.get("audio_duration_seconds"), 0.0)
    seconds = provider_video_seconds(config, duration, audio_duration)
    size = video_size_for_reference_image(reference_images, config)
    validate_prompt_matches_size(prompt, size)
    input_payload: dict[str, Any] = {"prompt": prompt}
    media: list[dict[str, str]] = []
    for image_path in reference_images:
        media.append({"type": "reference_image", "url": dashscope_upload_file(api_key, model, image_path)})
    media.extend({"type": "reference_video", "url": dashscope_upload_file(api_key, model, video_path)} for video_path in reference_videos)
    input_payload["media"] = media
    parameters = {
        "duration": seconds,
        "resolution": resolution_for_size(size),
        "ratio": aspect_ratio_for_size(size),
        "prompt_extend": False,
        "watermark": False,
    }
    request_payload = {"model": model, "input": input_payload, "parameters": parameters}
    write_provider_state(context, {
        "provider": "wan",
        "provider_profile": "video_wan_rtv",
        "model": model,
        "configured_model": configured_model,
        "request_payload": json_safe(request_payload),
        "reference_image_paths": [str(path) for path in reference_images],
        "reference_video_paths": [str(path) for path in reference_videos],
        "created_at": time.time(),
    })
    output_path.parent.mkdir(parents=True, exist_ok=True)
    started_at = time.time()
    deadline = time.time() + max(int(context.get("timeout_seconds") or 60), 60)
    started = post_json_request("https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis", request_payload, {"Authorization": f"Bearer {api_key}", "X-DashScope-Async": "enable", "X-DashScope-OssResourceResolve": "enable"}, timeout=120)
    task_id = text_value(dict_value(started.get("output")).get("task_id") or started.get("task_id"))
    if not task_id:
        raise ToolError(f"Wan response did not include task_id: {json_safe(started)}")
    write_provider_state(context, {
        "provider": "wan",
        "provider_profile": "video_wan_rtv",
        "model": model,
        "configured_model": configured_model,
        "task_id": task_id,
        "request_payload": json_safe(request_payload),
        "started_response": json_safe(started),
        "reference_image_paths": [str(path) for path in reference_images],
        "reference_video_paths": [str(path) for path in reference_videos],
        "created_at": started_at,
    })
    poll_url = f"https://dashscope.aliyuncs.com/api/v1/tasks/{urllib.parse.quote(task_id, safe='')}"
    video_url = ""
    while time.time() < deadline:
        polled = get_json_request(poll_url, {"Authorization": f"Bearer {api_key}"}, timeout=120)
        payload = dict_value(polled.get("output")) or polled
        failure = operation_failed(payload)
        if failure:
            raise ToolError(f"Wan video generation failed: {failure}")
        if operation_done(payload):
            video_url = first_url(polled)
            break
        time.sleep(5)
    if not video_url:
        raise ProviderTimeout("Wan video generation timed out or completed without URL.")
    download_binary(video_url, output_path)
    output_meta = validate_output_matches_size(output_path, size)
    write_provider_state(context, {
        "provider": "wan",
        "provider_profile": "video_wan_rtv",
        "model": model,
        "configured_model": configured_model,
        "task_id": task_id,
        "request_payload": json_safe(request_payload),
        "started_response": json_safe(started),
        "reference_image_paths": [str(path) for path in reference_images],
        "reference_video_paths": [str(path) for path in reference_videos],
        "output_path": str(output_path),
        "video_url": video_url,
        "aspect_audit": output_meta,
        "created_at": started_at,
        "updated_at": time.time(),
    })
    return {
        "provider": "wan",
        "provider_profile": "video_wan_rtv",
        "model": model,
        "configured_model": configured_model,
        "requested_duration": duration,
        "duration": seconds,
        "size": size,
        **output_meta,
        "reference_video_path": str(reference_videos[0]) if reference_videos else "",
        "reference_video_paths": [str(path) for path in reference_videos],
        "reference_image_paths": [str(path) for path in reference_images],
        "output_path": str(output_path),
        "video_url": video_url,
        "elapsed_seconds": round(time.time() - started_at, 3),
    }
