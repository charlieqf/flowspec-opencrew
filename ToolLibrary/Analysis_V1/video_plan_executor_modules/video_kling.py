from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import mimetypes
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

TEMPLATE_NAME = "Ref_05_02_Video_Kling_Omni.md"
SOURCE_TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "Reference" / "05_02" / "Video_Kling_Omni.md"
DEFAULT_BASE_URL = "https://api-beijing.klingai.com"
DEFAULT_TMPFILES_UPLOAD_URL = "https://tmpfiles.org/api/v1/upload"
DEFAULT_TMPFILES_EXPIRE_SECONDS = 21600
DEFAULT_PUBLIC_ASSET_PREFIX = "tmp/kling-reference-videos"
DEFAULT_PUBLIC_ASSET_TTL_SECONDS = 3600
DEFAULT_REFERENCE_VIDEO_MAX_SECONDS = 10
TURBO_MODEL = "kling-3.0-turbo"
OMNI_MODEL = "kling-v3-omni"
VIDEO_CONTENT_TYPES = ("video/", "application/octet-stream")
VIDEO_MAX_BYTES = 1024 * 1024 * 1024
TMPFILES_MAX_BYTES = 100 * 1024 * 1024

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


class ToolError(RuntimeError):
    pass


class ProviderTimeout(ToolError):
    pass


try:
    from opcrew_backend.services.safe_download import safe_download_to_path
except Exception:
    safe_download_to_path = None  # type: ignore[assignment]

try:
    from opencrew_runtime_secrets import resolve_secret_value
except Exception:
    def resolve_secret_value(api_key_ref: str, legacy_value: str = "") -> str:
        return str(legacy_value or "").strip()

try:
    from OpenCrew.ToolLibrary.Analysis_V1.public_asset_publisher import PublisherError, publish_file as publish_public_asset
except Exception:
    try:
        from ToolLibrary.Analysis_V1.public_asset_publisher import PublisherError, publish_file as publish_public_asset
    except Exception:
        PublisherError = RuntimeError  # type: ignore[assignment]
        publish_public_asset = None  # type: ignore[assignment]


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
    text = str(value or "").replace("\\/", "/")
    import re

    text = re.sub(r"([?&](?:key|token)=)[^&\s\"'}]+", r"\1***", text, flags=re.I)
    text = re.sub(r"(api[_-]?key[\"']?\s*[:=]\s*[\"']?)[^\"',}\s]+", r"\1***", text, flags=re.I)
    text = re.sub(r"(Authorization[\"']?\s*[:=]\s*[\"']?\s*Bearer\s+)[^\"',}\s]+", r"\1***", text, flags=re.I)
    text = re.sub(r"(Bearer\s+)[A-Za-z0-9._~+/=-]{12,}", r"\1***", text, flags=re.I)
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


def post_multipart_request(url: str, fields: dict[str, str], files: list[tuple[str, Path]], headers: dict[str, str] | None = None, timeout: int = 180) -> dict[str, Any]:
    boundary = f"----OpenCrewKlingBoundary{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
            str(value).encode("utf-8"),
            b"\r\n",
        ])
    for name, path in files:
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{name}"; filename="{path.name}"\r\n'.encode(),
            f"Content-Type: {mime}\r\n\r\n".encode(),
            path.read_bytes(),
            b"\r\n",
        ])
    chunks.append(f"--{boundary}--\r\n".encode())
    request = urllib.request.Request(
        url,
        data=b"".join(chunks),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "Accept": "application/json", **(headers or {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2000]
        raise ToolError(f"HTTP {exc.code} from {redact_secret_text(url)}: {redact_secret_text(detail)}") from exc
    except urllib.error.URLError as exc:
        raise ToolError(f"Request failed for {redact_secret_text(url)}: {exc.reason}") from exc


def download_video(video_url: str, output_path: Path) -> None:
    if safe_download_to_path is None:
        raise ToolError("Safe provider artifact downloader is unavailable; refusing to download Kling output.")
    safe_download_to_path(
        video_url,
        output_path,
        allowed_content_types=VIDEO_CONTENT_TYPES,
        max_bytes=VIDEO_MAX_BYTES,
        timeout=600,
        headers={"User-Agent": "OpenCrew/kling-video-download"},
    )


def first_url(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("url", "video_url", "download_url", "resource_url", "uri", "outputUrl", "output_url"):
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


def operation_status(payload: dict[str, Any]) -> str:
    data = dict_value(payload.get("data"))
    return text_value(
        payload.get("status")
        or payload.get("task_status")
        or payload.get("state")
        or data.get("status")
        or data.get("task_status")
        or data.get("state")
    ).lower()


def operation_done(payload: dict[str, Any]) -> bool:
    return operation_status(payload) in {"succeed", "succeeded", "success", "completed", "done", "finish", "finished"}


def operation_failed(payload: dict[str, Any]) -> str:
    if operation_status(payload) in {"failed", "error", "cancelled", "canceled", "rejected"}:
        return json.dumps(json_safe(payload), ensure_ascii=False)[:1200]
    return ""


def provider_video_seconds(duration: float, model: str = "") -> int:
    seconds = max(3, int(round(duration or 5)))
    return min(15, seconds)


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


def template_snapshot_text(context: dict[str, Any]) -> str:
    prompt_dir = Path(context.get("prompt_dir") or "")
    candidate = prompt_dir / TEMPLATE_NAME
    if candidate.exists():
        return candidate.read_text(encoding="utf-8")
    if SOURCE_TEMPLATE_PATH.exists():
        return SOURCE_TEMPLATE_PATH.read_text(encoding="utf-8")
    return ""


def _block(template_text: str, name: str) -> str:
    start = f"<!-- OPENCREW:{name}_START -->"
    end = f"<!-- OPENCREW:{name}_END -->"
    if start not in template_text or end not in template_text:
        raise ToolError(f"Video Kling template is missing block marker: {name}")
    return template_text.split(start, 1)[1].split(end, 1)[0].strip()


def _render(text: str, variables: dict[str, str]) -> str:
    rendered = text
    for key, value in variables.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", value)
    return rendered.strip()


def _join(template_text: str, blocks: list[str], variables: dict[str, str]) -> str:
    return "\n\n".join(_render(_block(template_text, name), variables) for name in blocks if text_value(name))


def build_prompt_package(context: dict[str, Any]) -> dict[str, Any]:
    segment = dict_value(context.get("segment"))
    dialogue_index = dict_value(context.get("dialogue_index"))
    duration = safe_float(segment.get("planned_video_duration"), 5.0)
    text = prompt_text_from_dialogues(segment, dialogue_index)
    cutaway = segment_is_cutaway(segment)
    template_text = template_snapshot_text(context)
    variables = {"dialogue_text": text, "duration_seconds": f"{duration:.1f}"}
    positive_blocks = [
        "VIDEO_KLING_OMNI_POSITIVE_BASE",
        "VIDEO_KLING_OMNI_DIALOGUE_OR_MOUTHING",
        "VIDEO_KLING_OMNI_CAMERA_LOCK",
        "VIDEO_KLING_OMNI_PERFORMANCE",
        "VIDEO_KLING_OMNI_STATIC_OBJECTS",
        "VIDEO_KLING_OMNI_AUDIO_CONTROL",
    ]
    negative_blocks = [
        "VIDEO_KLING_OMNI_NEGATIVE_BASE",
        "VIDEO_KLING_OMNI_NEGATIVE_CAMERA",
        "VIDEO_KLING_OMNI_NEGATIVE_EXPRESSION" if not cutaway else "",
        "VIDEO_KLING_OMNI_NEGATIVE_OBJECTS",
        "VIDEO_KLING_OMNI_PITFALLS_APPEND_ONLY",
    ]
    positive = _join(template_text, positive_blocks, variables)
    negative = _join(template_text, negative_blocks, variables)
    prompt = _render(_block(template_text, "VIDEO_KLING_OMNI_PROMPT"), {**variables, "positive_prompt": positive, "negative_prompt": negative})
    return {
        "schema_version": "analysis_v1_05_02_video_prompt_kling_0.1",
        "prompt_type": "video_generation",
        "provider_profile": "video_kling",
        "segment_id": text_value(segment.get("segment_id")),
        "dialogue_asset_keys": list_value(segment.get("dialogue_asset_keys") or segment.get("dialogue_ids")),
        "dialogue_ids": list_value(segment.get("dialogue_asset_keys") or segment.get("dialogue_ids")),
        "template_source": TEMPLATE_NAME,
        "template_snapshot_chars": len(template_text),
        "template_blocks": [name for name in positive_blocks + negative_blocks + ["VIDEO_KLING_OMNI_PROMPT"] if text_value(name)],
        "positive_prompt": positive,
        "negative_prompt": negative,
        "prompt": prompt,
        "reference_video": "Video_Kling_Omni.mp4",
        "extracted_fields": {"dialogue_text": text, "duration": duration, "cutaway": cutaway, "reference_video": "Video_Kling_Omni.mp4"},
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


def image_base64(path: Path) -> str:
    suffix = path.suffix.lower()
    mime = mimetypes.guess_type(path.name)[0] or ""
    if suffix not in {".jpg", ".jpeg", ".png"} and mime not in {"image/jpeg", "image/png"}:
        raise ToolError(f"Kling image-to-video accepts jpg/jpeg/png first frames; got {path.name}.")
    return base64.b64encode(path.read_bytes()).decode("ascii")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_https_url(value: str) -> bool:
    parsed = urllib.parse.urlsplit(text_value(value))
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def tmpfiles_direct_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(text_value(url))
    if parsed.scheme not in {"http", "https"} or parsed.netloc != "tmpfiles.org":
        return text_value(url)
    path = parsed.path.lstrip("/")
    if path.startswith("dl/"):
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "/" + path, "", ""))
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "/dl/" + path, "", ""))


def tmpfiles_upload_video(path: Path, config: dict[str, Any]) -> str:
    if not path.exists() or not path.is_file():
        raise ToolError(f"Kling Omni reference video is missing: {path}")
    size = path.stat().st_size
    if size > TMPFILES_MAX_BYTES:
        raise ToolError(f"tmpfiles.org upload limit is 100 MB; reference video is {size} bytes: {path}")
    upload_url = text_value(config.get("tmpfiles_upload_url") or DEFAULT_TMPFILES_UPLOAD_URL)
    timeout = int(safe_float(config.get("public_asset_upload_timeout_seconds"), 180))
    attempts = max(1, int(safe_float(config.get("public_asset_upload_attempts"), 2)))
    last_error = ""
    for attempt in range(1, attempts + 1):
        try:
            response = post_multipart_request(
                upload_url,
                {},
                [("file", path)],
                headers={"User-Agent": "OpenCrew/kling-tmpfiles-upload"},
                timeout=timeout,
            )
        except ToolError as exc:
            last_error = str(exc)
            if attempt < attempts and any(token in last_error.lower() for token in ("broken pipe", "connection reset", "timed out", "temporarily unavailable")):
                time.sleep(min(5, attempt * 2))
                continue
            raise
        data = dict_value(response.get("data"))
        raw_url = text_value(data.get("url") or response.get("url"))
        if text_value(response.get("status")).lower() in {"", "success"} and raw_url:
            return tmpfiles_direct_url(raw_url)
        last_error = f"tmpfiles.org upload did not return a public URL: {json.dumps(json_safe(response), ensure_ascii=False)[:1200]}"
        break
    raise ToolError(last_error or f"tmpfiles.org upload failed for {path.name}")


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


def video_duration_seconds(path: Path) -> float:
    exe = bundled_binary("ffprobe")
    command = [exe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        return 0.0
    try:
        payload = json.loads(completed.stdout or "{}")
        return safe_float(dict_value(payload.get("format")).get("duration"), 0.0)
    except Exception:
        return 0.0


def trim_reference_video(path: Path, max_seconds: int) -> Path:
    output = path.with_name(f"{path.stem}_public_ref_{max_seconds}s.mp4")
    current_duration = video_duration_seconds(output) if output.exists() else 0.0
    if output.exists() and output.stat().st_size > 0 and 0 < current_duration <= max_seconds + 0.05:
        return output
    exe = bundled_binary("ffmpeg")
    command = [
        exe,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(path),
        "-t",
        str(max_seconds),
        "-map",
        "0:v:0",
        "-an",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(output),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0 or not output.exists() or output.stat().st_size <= 0:
        raise ToolError(f"Kling reference video trim failed: {completed.stderr[:1200]}")
    trimmed_duration = video_duration_seconds(output)
    if trimmed_duration > max_seconds + 0.2:
        raise ToolError(f"Kling reference video trim exceeded max seconds: {trimmed_duration:.3f}s > {max_seconds}s")
    return output


def prepare_reference_video_for_public_url(path: Path, config: dict[str, Any]) -> Path:
    max_seconds = int(safe_float(config.get("reference_video_max_seconds"), DEFAULT_REFERENCE_VIDEO_MAX_SECONDS))
    max_seconds = min(15, max(1, max_seconds))
    duration = video_duration_seconds(path)
    if duration and duration > max_seconds + 0.05:
        return trim_reference_video(path, max_seconds)
    return path


def aws_quote(value: str) -> str:
    return urllib.parse.quote(str(value), safe="-_.~")


def aws_sign(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def r2_canonical_uri(bucket: str, object_key: str) -> str:
    parts = [bucket.strip("/"), *[part for part in object_key.strip("/").split("/") if part]]
    return "/" + "/".join(aws_quote(part) for part in parts)


def r2_signing_key(secret_key: str, date_stamp: str, region: str, service: str = "s3") -> bytes:
    k_date = aws_sign(("AWS4" + secret_key).encode("utf-8"), date_stamp)
    k_region = aws_sign(k_date, region)
    k_service = aws_sign(k_region, service)
    return aws_sign(k_service, "aws4_request")


def put_binary_request(url: str, body: bytes, headers: dict[str, str], timeout: int = 180) -> int:
    request = urllib.request.Request(url, data=body, headers=headers, method="PUT")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2000]
        raise ToolError(f"R2 upload failed with HTTP {exc.code}: {redact_secret_text(detail)}") from exc
    except urllib.error.URLError as exc:
        raise ToolError(f"R2 upload failed: {exc.reason}") from exc


def r2_put_object(endpoint: str, bucket: str, object_key: str, body: bytes, content_type: str, access_key: str, secret_key: str, region: str = "auto") -> str:
    parsed = urllib.parse.urlsplit(endpoint.rstrip("/"))
    if parsed.scheme != "https" or not parsed.netloc:
        raise ToolError("R2 endpoint must be an HTTPS URL.")
    if not bucket:
        raise ToolError("R2 bucket is required.")
    if not access_key or not secret_key:
        raise ToolError("R2 access key and secret key are required.")
    now = dt.datetime.now(dt.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(body).hexdigest()
    uri = r2_canonical_uri(bucket, object_key)
    host = parsed.netloc
    canonical_headers = f"content-type:{content_type}\nhost:{host}\nx-amz-content-sha256:{payload_hash}\nx-amz-date:{amz_date}\n"
    signed_headers = "content-type;host;x-amz-content-sha256;x-amz-date"
    canonical_request = "\n".join(["PUT", uri, "", canonical_headers, signed_headers, payload_hash])
    scope = f"{date_stamp}/{region}/s3/aws4_request"
    string_to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()])
    signature = hmac.new(r2_signing_key(secret_key, date_stamp, region), string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    authorization = f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={signed_headers}, Signature={signature}"
    url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, uri, "", ""))
    put_binary_request(url, body, {
        "Content-Type": content_type,
        "Content-Length": str(len(body)),
        "Host": host,
        "X-Amz-Content-Sha256": payload_hash,
        "X-Amz-Date": amz_date,
        "Authorization": authorization,
    })
    return url


def r2_presigned_get_url(endpoint: str, bucket: str, object_key: str, access_key: str, secret_key: str, region: str = "auto", expires: int = DEFAULT_PUBLIC_ASSET_TTL_SECONDS) -> str:
    parsed = urllib.parse.urlsplit(endpoint.rstrip("/"))
    if parsed.scheme != "https" or not parsed.netloc:
        raise ToolError("R2 endpoint must be an HTTPS URL.")
    ttl = min(604800, max(1, int(expires or DEFAULT_PUBLIC_ASSET_TTL_SECONDS)))
    now = dt.datetime.now(dt.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    uri = r2_canonical_uri(bucket, object_key)
    scope = f"{date_stamp}/{region}/s3/aws4_request"
    params = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": f"{access_key}/{scope}",
        "X-Amz-Date": amz_date,
        "X-Amz-Expires": str(ttl),
        "X-Amz-SignedHeaders": "host",
    }
    canonical_query = "&".join(f"{aws_quote(key)}={aws_quote(value)}" for key, value in sorted(params.items()))
    canonical_headers = f"host:{parsed.netloc}\n"
    canonical_request = "\n".join(["GET", uri, canonical_query, canonical_headers, "host", "UNSIGNED-PAYLOAD"])
    string_to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()])
    signature = hmac.new(r2_signing_key(secret_key, date_stamp, region), string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    query = f"{canonical_query}&X-Amz-Signature={signature}"
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, uri, query, ""))


def safe_object_key(prefix: str, path: Path) -> str:
    digest = file_sha256(path)[:16]
    suffix = "".join(ch if ch.isalnum() or ch in {".", "-", "_"} else "_" for ch in path.name) or "reference.mp4"
    timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    return "/".join(part.strip("/") for part in [prefix or DEFAULT_PUBLIC_ASSET_PREFIX, timestamp, f"{digest}_{suffix}"] if part.strip("/"))


def r2_secret(config: dict[str, Any], direct_key: str, ref_key: str, default_ref: str) -> str:
    direct = text_value(config.get(direct_key))
    if direct:
        return direct
    return resolve_secret_value(text_value(config.get(ref_key) or default_ref))


def r2_publish_video(path: Path, config: dict[str, Any]) -> str:
    endpoint = text_value(config.get("r2_endpoint") or config.get("public_asset_endpoint"))
    bucket = text_value(config.get("r2_bucket") or config.get("public_asset_bucket"))
    region = text_value(config.get("r2_region") or config.get("public_asset_region") or "auto")
    access_key = r2_secret(config, "r2_access_key_id", "r2_access_key_ref", "public_assets_r2_access_key_id")
    secret_key = r2_secret(config, "r2_secret_access_key", "r2_secret_access_key_ref", "public_assets_r2_secret_access_key")
    ttl = int(safe_float(config.get("public_asset_ttl_seconds") or config.get("r2_presigned_ttl_seconds"), DEFAULT_PUBLIC_ASSET_TTL_SECONDS))
    prefix = text_value(config.get("public_asset_prefix") or DEFAULT_PUBLIC_ASSET_PREFIX)
    object_key = safe_object_key(prefix, path)
    content_type = mimetypes.guess_type(path.name)[0] or "video/mp4"
    r2_put_object(endpoint, bucket, object_key, path.read_bytes(), content_type, access_key, secret_key, region)
    return r2_presigned_get_url(endpoint, bucket, object_key, access_key, secret_key, region, ttl)


def reference_video_public_url(path: Path, config: dict[str, Any]) -> str:
    explicit = text_value(config.get("reference_video_public_url") or config.get("kling_reference_video_url"))
    if explicit:
        if not is_https_url(explicit):
            raise ToolError("Kling reference_video_public_url must be an http(s) URL.")
        return explicit
    raw = str(path)
    if is_https_url(raw):
        return raw
    provider = text_value(config.get("public_asset_provider") or "tmpfiles").lower()
    if provider in {"tmpfiles", "tmpfiles.org", "tmp"}:
        return tmpfiles_upload_video(path, config)
    if provider == "r2" or (text_value(config.get("r2_endpoint")) and text_value(config.get("r2_bucket"))):
        return r2_publish_video(path, config)
    raise ToolError("Kling Omni reference video must be published to a public URL; set public_asset_provider=tmpfiles/r2 or reference_video_public_url.")


def base_url_from_config(config: dict[str, Any]) -> str:
    return text_value(config.get("base_url") or DEFAULT_BASE_URL).rstrip("/")


def resolution_from_config(config: dict[str, Any]) -> str:
    value = text_value(config.get("resolution") or config.get("default_resolution") or "1080p").lower()
    return value if value in {"720p", "1080p"} else "1080p"


def omni_mode_from_config(config: dict[str, Any]) -> str:
    value = text_value(config.get("mode") or config.get("default_mode") or "pro").lower()
    return value if value in {"std", "pro", "4k"} else "pro"


def aspect_ratio_from_config(config: dict[str, Any]) -> str:
    value = text_value(config.get("aspect_ratio") or config.get("default_aspect_ratio") or "9:16")
    return value or "9:16"


def sound_from_config(config: dict[str, Any]) -> str:
    value = text_value(config.get("sound") or "on").lower()
    return value if value in {"on", "off"} else "on"


def watermark_enabled(config: dict[str, Any]) -> bool:
    raw = config.get("watermark_enabled")
    if isinstance(raw, bool):
        return raw
    return text_value(raw).lower() in {"1", "true", "yes", "on"}


def task_id_from_response(payload: dict[str, Any]) -> str:
    data = dict_value(payload.get("data"))
    return text_value(data.get("id") or data.get("task_id") or payload.get("id") or payload.get("task_id"))


def request_fingerprint(prompt: str, model: str, reference_images: list[Path], reference_videos: list[Path], seconds: int, payload: dict[str, Any]) -> str:
    payload_for_hash = json.loads(json.dumps(json_safe(payload), ensure_ascii=False))
    if isinstance(payload_for_hash, dict):
        payload_for_hash.pop("external_task_id", None)
        options = payload_for_hash.get("options")
        if isinstance(options, dict):
            options.pop("external_task_id", None)
        for item in list_value(payload_for_hash.get("video_list")):
            if isinstance(item, dict) and item.get("video_url"):
                item["video_url"] = "__public_reference_video__"
    hasher = hashlib.sha256()
    hasher.update(model.encode("utf-8"))
    hasher.update(str(seconds).encode("ascii"))
    hasher.update(prompt.encode("utf-8"))
    hasher.update(json.dumps(payload_for_hash, sort_keys=True, ensure_ascii=False).encode("utf-8"))
    for path in reference_images:
        hasher.update(path.name.encode("utf-8"))
        try:
            hasher.update(str(path.stat().st_size).encode("ascii"))
            hasher.update(bytes.fromhex(file_sha256(path)))
        except OSError:
            pass
    for path in reference_videos:
        hasher.update(path.name.encode("utf-8"))
        try:
            hasher.update(str(path.stat().st_size).encode("ascii"))
            hasher.update(bytes.fromhex(file_sha256(path)))
        except OSError:
            pass
    return hasher.hexdigest()


def provider_task_state_path(context: dict[str, Any]) -> Path | None:
    value = text_value(context.get("provider_task_state_path"))
    return Path(value) if value else None


def read_json_or_empty(path: Path) -> dict[str, Any]:
    try:
        payload = read_json(path)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def matching_task_state(path: Path | None, fingerprint: str, model: str) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    state = read_json_or_empty(path)
    if text_value(state.get("fingerprint")) != fingerprint:
        return {}
    if text_value(state.get("model")) != model:
        return {}
    if text_value(state.get("provider")) not in {"", "kling"}:
        return {}
    return state


def write_task_state(path: Path | None, payload: dict[str, Any]) -> None:
    if path is None:
        return
    current = read_json_or_empty(path)
    write_json(path, {**current, **json_safe(payload), "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})


def turbo_request_payload(prompt: str, reference_images: list[Path], seconds: int, config: dict[str, Any], external_task_id: str) -> dict[str, Any]:
    contents: list[dict[str, Any]] = [{"type": "prompt", "text": prompt[:2500]}]
    if reference_images:
        contents.append({"type": "first_frame", "url": image_base64(reference_images[0])})
    return {
        "contents": contents,
        "settings": {"resolution": resolution_from_config(config), "duration": seconds},
        "options": {"external_task_id": external_task_id, "watermark_info": {"enabled": watermark_enabled(config)}},
    }


def omni_request_payload(prompt: str, reference_images: list[Path], reference_video_urls: list[str], seconds: int, config: dict[str, Any], external_task_id: str) -> dict[str, Any]:
    if not reference_images:
        raise ToolError("Kling 3.0 Omni image-to-video requires a first-frame image.")
    payload: dict[str, Any] = {
        "model_name": OMNI_MODEL,
        "prompt": prompt[:2500],
        "image_list": [{"image_url": image_base64(reference_images[0]), "type": "first_frame"}],
        "sound": sound_from_config(config),
        "mode": omni_mode_from_config(config),
        "aspect_ratio": aspect_ratio_from_config(config),
        "duration": str(seconds),
        "watermark_info": {"enabled": watermark_enabled(config)},
        "external_task_id": external_task_id,
    }
    if reference_video_urls:
        payload["sound"] = "off"
        payload["video_list"] = [{"video_url": reference_video_urls[0], "refer_type": "feature", "keep_original_sound": "no"}]
    return payload


def public_reference_video_urls(reference_videos: list[Path], config: dict[str, Any]) -> tuple[list[str], list[dict[str, Any]]]:
    urls: list[str] = []
    published_assets: list[dict[str, Any]] = []
    for path in reference_videos[:1]:
        if str(path).startswith(("http://", "https://")):
            urls.append(str(path))
            continue
        upload_path = prepare_reference_video_for_public_url(path, config)
        url = reference_video_public_url(upload_path, config)
        if not url:
            raise ToolError(f"public asset publisher did not return public_url for Kling reference video: {path}")
        urls.append(url)
        published_assets.append({
            "provider": text_value(config.get("public_asset_provider") or "tmpfiles"),
            "purpose": "kling_omni_reference_video",
            "path": str(upload_path),
            "source_path": str(path),
            "filename": upload_path.name,
            "size_bytes": upload_path.stat().st_size,
            "sha256": file_sha256(upload_path),
            "duration_seconds": video_duration_seconds(upload_path),
            "public_url": url,
        })
    return urls, published_assets


def poll_urls(base_url: str, model: str, task_id: str, external_task_id: str) -> list[str]:
    urls: list[str] = []
    if model == TURBO_MODEL and external_task_id:
        urls.append(f"{base_url}/tasks?external_task_ids={urllib.parse.quote(external_task_id, safe='')}")
    if task_id:
        quoted = urllib.parse.quote(task_id, safe="")
        if model == OMNI_MODEL:
            urls.extend([f"{base_url}/v1/videos/omni-video/{quoted}", f"{base_url}/v1/videos/{quoted}", f"{base_url}/tasks/{quoted}"])
        else:
            urls.extend([f"{base_url}/tasks/{quoted}", f"{base_url}/tasks?ids={quoted}"])
    return urls


def poll_task(base_url: str, model: str, task_id: str, external_task_id: str, headers: dict[str, str], deadline: float) -> tuple[str, dict[str, Any]]:
    urls = poll_urls(base_url, model, task_id, external_task_id)
    if not urls:
        raise ToolError("Kling response did not include a task id or external task id.")
    last_payload: dict[str, Any] = {}
    last_error = ""
    while time.time() < deadline:
        for url in urls:
            try:
                payload = get_json_request(url, headers, timeout=120)
            except ToolError as exc:
                last_error = str(exc)
                continue
            last_payload = payload
            failure = operation_failed(payload)
            if failure:
                raise ToolError(f"Kling video generation failed: {failure}")
            if operation_done(payload):
                return first_url(payload), payload
        time.sleep(5)
    if last_payload:
        raise ProviderTimeout(f"Kling video generation timed out: {json.dumps(json_safe(last_payload), ensure_ascii=False)[:1200]}")
    raise ProviderTimeout(f"Kling video generation timed out; last polling error: {redact_secret_text(last_error)}")


def generate(context: dict[str, Any], prompt_path: Path, output_path: Path) -> dict[str, Any]:
    config = dict_value(context.get("config"))
    api_key = text_value(config.get("api_key"))
    model = text_value(config.get("model") or TURBO_MODEL)
    duration = safe_float(context.get("duration_seconds"), 5.0)
    if not api_key:
        raise ToolError(f"Missing video API key for kling/{model}.")
    if model not in {TURBO_MODEL, OMNI_MODEL}:
        raise ToolError(f"Unsupported Kling video model: {model}")
    prompt = read_prompt_text(prompt_path)
    reference_images = [Path(path) for path in list_value(context.get("reference_images")) if Path(path).exists()]
    reference_videos = [Path(path) for path in list_value(context.get("reference_videos")) if Path(path).exists()]
    reference_video_urls = [text_value(url) for url in list_value(context.get("reference_video_urls")) if text_value(url).startswith(("http://", "https://"))]
    published_assets: list[dict[str, Any]] = []
    seconds = provider_video_seconds(duration, model)
    base_url = base_url_from_config(config)
    external_task_id = f"opencrew-{uuid.uuid4().hex}"
    if model == OMNI_MODEL and reference_videos and not reference_video_urls:
        reference_video_urls, published_assets = public_reference_video_urls(reference_videos, config)
    request_payload = (
        turbo_request_payload(prompt, reference_images, seconds, config, external_task_id)
        if model == TURBO_MODEL
        else omni_request_payload(prompt, reference_images, reference_video_urls, seconds, config, external_task_id)
    )
    fingerprint = request_fingerprint(prompt, model, reference_images, reference_videos, seconds, request_payload)
    state_path = provider_task_state_path(context)
    prior_state = matching_task_state(state_path, fingerprint, model)
    prior_task_id = text_value(prior_state.get("provider_task_id") or prior_state.get("task_id"))
    if prior_task_id and text_value(prior_state.get("status")) in {"succeeded", "completed"} and output_path.exists() and output_path.stat().st_size > 0:
        return {
            "provider": "kling",
            "model": model,
            "provider_profile": "video_kling",
            "provider_task_id": prior_task_id,
            "task_id": prior_task_id,
            "requested_duration": duration,
            "duration": seconds,
            "usage": {"video_second": seconds, "request": 1},
            "output_path": str(output_path),
            "video_url": text_value(prior_state.get("video_url_summary")),
            "elapsed_seconds": 0,
            "cached": True,
        }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    started_at = time.time()
    deadline = time.time() + max(int(context.get("timeout_seconds") or 120), 60)
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    if prior_task_id:
        task_id = prior_task_id
    else:
        create_url = f"{base_url}/image-to-video/{TURBO_MODEL}" if model == TURBO_MODEL else f"{base_url}/v1/videos/omni-video"
        started = post_json_request(create_url, request_payload, headers, timeout=120)
        task_id = task_id_from_response(started)
        if not task_id:
            raise ToolError(f"Kling response did not include task id: {json_safe(started)}")
        write_task_state(state_path, {
            "schema_version": "analysis_v1_kling_provider_task_0.1",
            "provider": "kling",
            "provider_profile": "video_kling",
            "model": model,
            "provider_task_id": task_id,
            "task_id": task_id,
            "external_task_id": external_task_id,
            "fingerprint": fingerprint,
            "status": operation_status(started) or "submitted",
            "base_url": base_url,
            "duration": seconds,
            "published_assets": published_assets,
        })
    external_for_poll = text_value(prior_state.get("external_task_id") or external_task_id)
    video_url, final_payload = poll_task(base_url, model, task_id, external_for_poll, headers, deadline)
    if not video_url:
        raise ToolError(f"Kling completed without video URL: {json_safe(final_payload)}")
    download_video(video_url, output_path)
    write_task_state(state_path, {
        "status": "succeeded",
        "provider_task_id": task_id,
        "task_id": task_id,
        "external_task_id": external_for_poll,
        "video_url_summary": video_url,
        "published_assets": published_assets,
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })
    return {
        "provider": "kling",
        "model": model,
        "provider_profile": "video_kling",
        "provider_task_id": task_id,
        "task_id": task_id,
        "requested_duration": duration,
        "duration": seconds,
        "usage": {"video_second": seconds, "request": 1},
        "output_path": str(output_path),
        "video_url": video_url,
        "published_assets": published_assets,
        "elapsed_seconds": round(time.time() - started_at, 3),
    }
