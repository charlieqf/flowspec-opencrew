from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import http.client
import json
import mimetypes
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

TEMPLATE_NAME = "Ref_05_02_Video_OpenRouter.md"
SOURCE_TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "Reference" / "05_02" / "Video_OpenRouter.md"
SDR2V_TEMPLATE_FILENAME = "Video_SDR2V.md"
SDR2V_TEMPLATE_NAME = "Ref_05_02_Video_SDR2V.md"
SDR2V_TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "Reference" / "05_02" / SDR2V_TEMPLATE_FILENAME
DANCE_MIMIC_TEMPLATE_FILENAME = "Video_SDR2V_DanceMimic.md"
DANCE_MIMIC_TEMPLATE_NAME = "Ref_05_02_Video_SDR2V_DanceMimic.md"
DANCE_MIMIC_TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "Reference" / "05_02" / DANCE_MIMIC_TEMPLATE_FILENAME
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_ASPECT_RATIO = "9:16"
DEFAULT_RESOLUTION = "720p"
DEFAULT_SEND_FRAME_IMAGES = True
DEFAULT_MODEL = "bytedance/seedance-2.0-fast"
DEFAULT_PUBLIC_ASSET_PREFIX = "tmp/openrouter-frames"
DEFAULT_PUBLIC_ASSET_TTL_SECONDS = 3600
DEFAULT_INPUT_REFERENCE_LIMIT = 12
SDR2V_PROMPT_MAX_CHARS = 1000
SDR2V_FIXED_PROMPT_MAX_CHARS = 700
SDR2V_DIALOGUE_MIN_RESERVED_CHARS = 300
REFERENCE_DATA_URL_MAX_BYTES = 25 * 1024 * 1024
VIDEO_MAX_BYTES = 600 * 1024 * 1024
VIDEO_CONTENT_TYPES = ("video/*", "application/octet-stream")

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"
TOOLLIBRARY_ROOT = REPO_ROOT / "ToolLibrary"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(TOOLLIBRARY_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLLIBRARY_ROOT))

try:
    from opcrew_backend.services.safe_download import safe_download_to_path
except Exception:
    safe_download_to_path = None  # type: ignore[assignment]

try:
    from opencrew_runtime_secrets import public_asset_r2_runtime_config, resolve_secret_value
except Exception:
    def public_asset_r2_runtime_config() -> dict[str, str]:
        return {}

    def resolve_secret_value(api_key_ref: str, legacy_value: str = "") -> str:
        return str(legacy_value or "").strip()

try:
    from OpenCrew.ToolLibrary.Analysis_V1.public_asset_publisher import PublisherError, publish_tmpfiles as publish_tmpfiles_asset
except Exception:
    try:
        from ToolLibrary.Analysis_V1.public_asset_publisher import PublisherError, publish_tmpfiles as publish_tmpfiles_asset
    except Exception:
        try:
            from Analysis_V1.public_asset_publisher import PublisherError, publish_tmpfiles as publish_tmpfiles_asset
        except Exception:
            PublisherError = RuntimeError  # type: ignore[assignment]
            publish_tmpfiles_asset = None  # type: ignore[assignment]


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


def bool_value(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    text = text_value(value).lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def apply_public_asset_runtime_config(config: dict[str, Any]) -> dict[str, Any]:
    resolved = dict(config)
    configured_provider = text_value(resolved.get("public_asset_provider")).lower()
    if configured_provider not in {"", "r2"}:
        return resolved

    runtime_r2 = public_asset_r2_runtime_config()
    endpoint = text_value(resolved.get("r2_endpoint") or resolved.get("public_asset_endpoint") or runtime_r2.get("OPENCREW_PUBLIC_ASSET_R2_ENDPOINT"))
    bucket = text_value(resolved.get("r2_bucket") or resolved.get("public_asset_bucket") or runtime_r2.get("OPENCREW_PUBLIC_ASSET_R2_BUCKET"))
    if not endpoint or not bucket:
        return resolved

    runtime_fields: dict[str, Any] = {
        "public_asset_provider": "r2",
        "r2_endpoint": endpoint,
        "r2_bucket": bucket,
        "r2_region": text_value(resolved.get("r2_region") or resolved.get("public_asset_region") or runtime_r2.get("OPENCREW_PUBLIC_ASSET_R2_REGION") or "auto"),
        "public_asset_prefix": text_value(resolved.get("public_asset_prefix") or runtime_r2.get("OPENCREW_PUBLIC_ASSET_R2_PREFIX") or DEFAULT_PUBLIC_ASSET_PREFIX),
        "public_asset_ttl_seconds": int(safe_float(resolved.get("public_asset_ttl_seconds") or runtime_r2.get("OPENCREW_PUBLIC_ASSET_R2_TTL_SECONDS"), DEFAULT_PUBLIC_ASSET_TTL_SECONDS)),
        "public_asset_config_source": "runtime_environment" if runtime_r2.get("OPENCREW_PUBLIC_ASSET_R2_ENDPOINT") else "provider_config",
    }
    resolved.update(runtime_fields)
    for nested_key in ("extra", "extra_json"):
        nested = dict_value(resolved.get(nested_key))
        resolved[nested_key] = {**nested, **runtime_fields}
    return resolved


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_json_or_empty(path: Path) -> dict[str, Any]:
    try:
        payload = read_json(path)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_safe(payload), ensure_ascii=False, indent=2), encoding="utf-8")


def redact_secret_text(value: str) -> str:
    text = str(value or "")
    text = text.replace("\\/", "/")
    text = re.sub(r"([?&]key=)[^&\s\"'}]+", r"\1***", text, flags=re.I)
    text = re.sub(r"([?&]X-Amz-Credential=)[^&\s\"'}]+", r"\1***", text, flags=re.I)
    text = re.sub(r"([?&]X-Amz-Signature=)[^&\s\"'}]+", r"\1***", text, flags=re.I)
    text = re.sub(r"([?&]X-Amz-Security-Token=)[^&\s\"'}]+", r"\1***", text, flags=re.I)
    text = re.sub(r"(api[_-]?key[\"']?\s*[:=]\s*[\"']?)[^\"',}\s]+", r"\1***", text, flags=re.I)
    text = re.sub(r"(Authorization[\"']?\s*[:=]\s*[\"']?\s*Bearer\s+)[^\"',}\s]+", r"\1***", text, flags=re.I)
    text = re.sub(r"(Bearer\s+)[A-Za-z0-9._~+/=-]{12,}", r"\1***", text, flags=re.I)
    text = re.sub(r"(openrouter[\"']?\s*[:=]\s*[\"']?)[^\"',}\s]+", r"\1***", text, flags=re.I)
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


def json_from_detail(detail: str) -> dict[str, Any]:
    try:
        parsed = json.loads(str(detail or ""))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def retry_after_seconds(exc: urllib.error.HTTPError | None, detail: str, attempt: int) -> float:
    values: list[float] = []
    if exc is not None:
        try:
            raw = str(exc.headers.get("Retry-After") or "")
        except Exception:
            raw = ""
        if raw:
            try:
                values.append(float(raw))
            except ValueError:
                pass
    payload = json_from_detail(detail)
    try:
        if payload.get("retry_after") is not None:
            values.append(float(payload.get("retry_after")))
    except (TypeError, ValueError):
        pass
    if values:
        return max(0.0, min(min(values), 60.0))
    return min(30.0, float(10 * attempt))


def retryable_http_error(exc: urllib.error.HTTPError, detail: str) -> bool:
    if int(getattr(exc, "code", 0) or 0) in {408, 429, 500, 502, 503, 504, 520, 522, 523, 524}:
        return True
    payload = json_from_detail(detail)
    error_payload = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    status = text_value(payload.get("status") or error_payload.get("status")).upper()
    return payload.get("retryable") is True or payload.get("cloudflare_error") is True or status in {"UNAVAILABLE", "RESOURCE_EXHAUSTED", "INTERNAL"}


def retryable_url_error(exc: urllib.error.URLError) -> bool:
    reason = getattr(exc, "reason", exc)
    lowered = str(reason or "").lower()
    return isinstance(reason, TimeoutError) or "timed out" in lowered or "temporarily unavailable" in lowered or "connection reset" in lowered


def json_request_with_retries(request_factory: Any, url: str, timeout: int, attempts: int = 2) -> dict[str, Any]:
    for attempt in range(1, max(1, attempts) + 1):
        try:
            with urllib.request.urlopen(request_factory(), timeout=timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:2000]
            if attempt < attempts and retryable_http_error(exc, detail):
                time.sleep(retry_after_seconds(exc, detail, attempt))
                continue
            suffix = f" (after {attempt} attempts)" if attempt > 1 else ""
            raise ToolError(f"HTTP {exc.code} from {redact_secret_text(url)}: {redact_secret_text(detail)}{suffix}") from exc
        except urllib.error.URLError as exc:
            if attempt < attempts and retryable_url_error(exc):
                time.sleep(retry_after_seconds(None, "", attempt))
                continue
            suffix = f" (after {attempt} attempts)" if attempt > 1 else ""
            raise ToolError(f"Request failed for {redact_secret_text(url)}: {exc.reason}{suffix}") from exc
        except (ssl.SSLError, http.client.IncompleteRead, ConnectionError, TimeoutError) as exc:
            if attempt < attempts:
                time.sleep(retry_after_seconds(None, "", attempt))
                continue
            suffix = f" (after {attempt} attempts)" if attempt > 1 else ""
            raise ToolError(f"Request failed for {redact_secret_text(url)}: {exc}{suffix}") from exc
    raise ToolError(f"Request failed for {redact_secret_text(url)}")


def post_json_request(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int = 120) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return json_request_with_retries(
        lambda: urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", **headers}, method="POST"),
        url,
        timeout,
        attempts=3,
    )


def get_json_request(url: str, headers: dict[str, str], timeout: int = 120) -> dict[str, Any]:
    return json_request_with_retries(
        lambda: urllib.request.Request(url, headers=headers, method="GET"),
        url,
        timeout,
    )


def first_url(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("video_url", "url", "download_url", "outputUrl", "output_url"):
            value = payload.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value
        for key in ("videos", "outputs", "data", "content"):
            found = first_url(payload.get(key))
            if found:
                return found
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
    for container in (payload, dict_value(payload.get("data"))):
        for key in ("id", "task_id", "generation_id"):
            value = text_value(container.get(key))
            if value:
                return value
    return ""


def operation_status(payload: dict[str, Any]) -> str:
    data = dict_value(payload.get("data"))
    return text_value(payload.get("status") or payload.get("state") or data.get("status") or data.get("state")).lower()


def operation_done(payload: dict[str, Any]) -> bool:
    return operation_status(payload) in {"completed", "complete", "succeeded", "success", "done", "finished"}


def operation_failed(payload: dict[str, Any]) -> str:
    if operation_status(payload) in {"failed", "error", "cancelled", "canceled", "rejected", "expired"}:
        return json.dumps(json_safe(payload), ensure_ascii=False)[:1200]
    return ""


def clamp_duration(duration: float) -> int:
    return min(15, max(4, int(round(duration or 5))))


def file_data_url(path: Path, default_mime: str = "application/octet-stream") -> str:
    mime = mimetypes.guess_type(path.name)[0] or default_mime
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def image_data_url(path: Path) -> str:
    return file_data_url(path, "image/png")


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


def put_binary_request(url: str, body: bytes, headers: dict[str, str], timeout: int = 120) -> int:
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
    put_binary_request(
        url,
        body,
        {
            "Content-Type": content_type,
            "Content-Length": str(len(body)),
            "Host": host,
            "X-Amz-Content-Sha256": payload_hash,
            "X-Amz-Date": amz_date,
            "Authorization": authorization,
        },
        timeout=120,
    )
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
    suffix = "".join(ch if ch.isalnum() or ch in {".", "-", "_"} else "_" for ch in path.name) or "frame.png"
    timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    return "/".join(part.strip("/") for part in [prefix or DEFAULT_PUBLIC_ASSET_PREFIX, timestamp, f"{digest}_{suffix}"] if part.strip("/"))


def r2_secret(config: dict[str, Any], direct_key: str, ref_key: str, default_ref: str) -> str:
    direct = text_value(config.get(direct_key))
    if direct:
        return direct
    env_key = {
        "r2_access_key_id": "OPENCREW_PUBLIC_ASSET_R2_ACCESS_KEY_ID",
        "r2_secret_access_key": "OPENCREW_PUBLIC_ASSET_R2_SECRET_ACCESS_KEY",
    }.get(direct_key, "")
    runtime_secret = text_value(os.environ.get(env_key)) if env_key else ""
    if runtime_secret:
        return runtime_secret
    ref = text_value(config.get(ref_key) or default_ref)
    return resolve_secret_value(ref)


def r2_publish_file(path: Path, config: dict[str, Any]) -> str:
    endpoint = text_value(config.get("r2_endpoint") or config.get("public_asset_endpoint"))
    bucket = text_value(config.get("r2_bucket") or config.get("public_asset_bucket"))
    region = text_value(config.get("r2_region") or config.get("public_asset_region") or "auto")
    access_key = r2_secret(config, "r2_access_key_id", "r2_access_key_ref", "public_assets_r2_access_key_id")
    secret_key = r2_secret(config, "r2_secret_access_key", "r2_secret_access_key_ref", "public_assets_r2_secret_access_key")
    ttl = int(safe_float(config.get("public_asset_ttl_seconds") or config.get("r2_presigned_ttl_seconds"), DEFAULT_PUBLIC_ASSET_TTL_SECONDS))
    prefix = text_value(config.get("public_asset_prefix") or DEFAULT_PUBLIC_ASSET_PREFIX)
    object_key = safe_object_key(prefix, path)
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    r2_put_object(endpoint, bucket, object_key, path.read_bytes(), content_type, access_key, secret_key, region)
    return r2_presigned_get_url(endpoint, bucket, object_key, access_key, secret_key, region, ttl)


def r2_publish_image(path: Path, config: dict[str, Any]) -> str:
    return r2_publish_file(path, config)


def tmpfiles_publish_file(path: Path, config: dict[str, Any], kind: str) -> str:
    if publish_tmpfiles_asset is None:
        raise ToolError("OpenRouter video reference needs tmpfiles fallback, but public asset publisher is unavailable.")
    try:
        published = publish_tmpfiles_asset(path, dict(config), purpose=f"openrouter_{kind}_reference")
    except PublisherError as exc:
        raise ToolError(f"OpenRouter tmpfiles fallback failed for {kind} reference: {exc}") from exc
    public_url = text_value(dict_value(published).get("public_url"))
    if not public_url:
        raise ToolError(f"OpenRouter tmpfiles fallback did not return a public URL for {path.name}.")
    return public_url


def frame_image_url(path: Path, config: dict[str, Any]) -> str:
    provider = text_value(config.get("public_asset_provider")).lower()
    if provider == "r2" or (text_value(config.get("r2_endpoint")) and text_value(config.get("r2_bucket"))):
        return r2_publish_file(path, config)
    return image_data_url(path)


def reference_asset_url(path: Path, config: dict[str, Any], kind: str) -> str:
    provider = text_value(config.get("public_asset_provider")).lower()
    if provider == "r2" or (text_value(config.get("r2_endpoint")) and text_value(config.get("r2_bucket"))):
        return r2_publish_file(path, config)
    if kind == "video" and bool_value(config.get("require_r2_public_assets")):
        raise ToolError(
            "privacy_grid_public_asset_transport_invalid: privacy grid motion references require configured R2 public asset transport."
        )
    if kind == "video":
        return tmpfiles_publish_file(path, config, kind)
    if path.stat().st_size > REFERENCE_DATA_URL_MAX_BYTES:
        raise ToolError(
            "OpenRouter input_references need a public asset URL for large audio/video references. "
            "Configure R2 public asset settings or use a smaller reference file."
        )
    defaults = {
        "image": "image/png",
        "audio": "audio/mpeg",
        "video": "video/mp4",
    }
    return file_data_url(path, defaults.get(kind, "application/octet-stream"))


def input_reference_item(path: Path, kind: str, config: dict[str, Any]) -> dict[str, Any]:
    if kind == "audio":
        return {"type": "audio_url", "audio_url": {"url": reference_asset_url(path, config, kind)}}
    if kind == "video":
        return {"type": "video_url", "video_url": {"url": reference_asset_url(path, config, kind)}}
    return {"type": "image_url", "image_url": {"url": reference_asset_url(path, config, "image")}}


def input_reference_mode(config: dict[str, Any]) -> bool:
    mode = text_value(config.get("reference_mode") or config.get("referenceMode")).lower()
    return mode in {"input_references", "input-reference", "reference_to_video", "reference-to-video", "sr2", "max_sr2"}


def should_send_input_references(config: dict[str, Any], reference_audios: list[Path], reference_videos: list[Path]) -> bool:
    return input_reference_mode(config) or bool(reference_audios or reference_videos)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def request_fingerprint(
    prompt: str,
    model: str,
    reference_images: list[Path],
    duration_seconds: int,
    payload: dict[str, Any],
    reference_audios: list[Path] | None = None,
    reference_videos: list[Path] | None = None,
) -> str:
    reference_audios = reference_audios or []
    reference_videos = reference_videos or []
    public_payload = {
        "model": model,
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "reference_image_sha256": [file_sha256(path) for path in reference_images[:1]] if payload.get("frame_images") else [],
        "input_reference_image_sha256": [file_sha256(path) for path in reference_images] if payload.get("input_references") else [],
        "input_reference_audio_sha256": [file_sha256(path) for path in reference_audios] if payload.get("input_references") else [],
        "input_reference_video_sha256": [file_sha256(path) for path in reference_videos] if payload.get("input_references") else [],
        "aspect_ratio": payload.get("aspect_ratio"),
        "resolution": payload.get("resolution"),
        "duration": duration_seconds,
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


def template_spec(context: dict[str, Any]) -> tuple[str, Path]:
    segment = dict_value(context.get("segment"))
    config = dict_value(context.get("config"))
    requested = text_value(context.get("prompt_template") or segment.get("prompt_template") or config.get("prompt_template"))
    if Path(requested).name == DANCE_MIMIC_TEMPLATE_FILENAME:
        return DANCE_MIMIC_TEMPLATE_NAME, DANCE_MIMIC_TEMPLATE_PATH
    if Path(requested).name == SDR2V_TEMPLATE_FILENAME:
        return SDR2V_TEMPLATE_NAME, SDR2V_TEMPLATE_PATH
    return TEMPLATE_NAME, SOURCE_TEMPLATE_PATH


def _template_text(context: dict[str, Any]) -> str:
    template_name, source_path = template_spec(context)
    return template_snapshot_text({**context, "template_name": template_name, "template_source_path": str(source_path)}, template_name)


def _block(template_text: str, name: str) -> str:
    start = f"<!-- OPENCREW:{name}_START -->"
    end = f"<!-- OPENCREW:{name}_END -->"
    if start not in template_text or end not in template_text:
        raise ToolError(f"Video OpenRouter template is missing block marker: {name}")
    return template_text.split(start, 1)[1].split(end, 1)[0].strip()


def _render(text: str, variables: dict[str, str]) -> str:
    rendered = text
    for key, value in variables.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", value)
    return rendered.strip()


def _join(template_text: str, blocks: list[str], variables: dict[str, str]) -> str:
    return "\n\n".join(_render(_block(template_text, name), variables) for name in blocks if text_value(name))


def build_prompt_package(context: dict[str, Any]) -> dict[str, Any]:
    template_name, _source_path = template_spec(context)
    fields = base_video_fields(context)
    segment = fields["segment"]
    cutaway = bool(fields["cutaway"])
    duration = float(fields["duration"])
    text = text_value(fields["dialogue_text"])
    template_text = _template_text(context)
    dance_mimic = dict_value(segment.get("dance_mimic"))
    reference_grid = bool(dance_mimic.get("reference_video_grid_applied"))
    target_grid = bool(dance_mimic.get("target_identity_grid_applied"))
    grid_scope = "目标身份图和表情参考视频" if reference_grid and target_grid else ("表情参考视频" if reference_grid else ("目标身份图" if target_grid else ""))
    reference_image_roles = [
        {"position": index, "role": text_value(dict_value(item).get("role")), "path": text_value(dict_value(item).get("path"))}
        for index, item in enumerate(list_value(context.get("reference_image_roles")), start=1)
        if text_value(dict_value(item).get("role"))
    ]
    role_labels = {"continuity_first_frame": "连续首帧", "target_identity": "目标身份"}
    reference_role_contract = "；".join(
        f"图{item['position']}=" + "、".join(
            role_labels.get(role, role)
            for role in item["role"].split(",")
            if role
        )
        for item in reference_image_roles
    )
    explicit_reference_role_lines = "\n".join(
        f"Input image reference {item['position']} has role {role}."
        for item in reference_image_roles
        for role in item["role"].split(",")
        if role
    )
    if explicit_reference_role_lines:
        reference_role_contract = "\n".join(
            part
            for part in (
                explicit_reference_role_lines,
                reference_role_contract,
            )
            if part
        )
    variables = {
        "dialogue_text": text,
        "duration_seconds": f"{duration:.1f}",
        "gridded_input_scope": grid_scope,
        "reference_role_contract": reference_role_contract,
    }
    positive_blocks = ["VIDEO_OPENROUTER_POSITIVE_BASE", "VIDEO_OPENROUTER_DIALOGUE_CUTAWAY" if cutaway else "VIDEO_OPENROUTER_DIALOGUE_STANDARD"]
    if reference_role_contract:
        positive_blocks.append("VIDEO_OPENROUTER_REFERENCE_ROLES")
    negative_blocks = ["VIDEO_OPENROUTER_NEGATIVE_BASE", "VIDEO_OPENROUTER_NEGATIVE_CUTAWAY" if cutaway else "", "VIDEO_OPENROUTER_PITFALLS_APPEND_ONLY"]
    if grid_scope:
        positive_blocks.append("VIDEO_OPENROUTER_PRIVACY_GRID_POSITIVE")
        negative_blocks.insert(-1, "VIDEO_OPENROUTER_PRIVACY_GRID_NEGATIVE")
    positive = _join(template_text, positive_blocks, variables)
    negative = _join(template_text, negative_blocks, variables)
    prompt = _render(_block(template_text, "VIDEO_OPENROUTER_PROMPT"), {**variables, "positive_prompt": positive, "negative_prompt": negative})
    prompt_budget: dict[str, int] = {}
    if template_name == SDR2V_TEMPLATE_NAME:
        fixed_prompt_chars = len(prompt) - len(text)
        dialogue_budget_chars = SDR2V_PROMPT_MAX_CHARS - fixed_prompt_chars
        if fixed_prompt_chars > SDR2V_FIXED_PROMPT_MAX_CHARS or dialogue_budget_chars < SDR2V_DIALOGUE_MIN_RESERVED_CHARS:
            raise ToolError(
                "SDR2V 固定提示词超出预算："
                f"固定部分 {fixed_prompt_chars} 字，要求不超过 {SDR2V_FIXED_PROMPT_MAX_CHARS} 字，"
                f"且至少为台词预留 {SDR2V_DIALOGUE_MIN_RESERVED_CHARS} 字。"
            )
        if len(text) > dialogue_budget_chars:
            raise ToolError(
                "SDR2V 台词超过提示词预算："
                f"台词 {len(text)} 字，可用 {dialogue_budget_chars} 字，"
                f"最终提示词不得超过 {SDR2V_PROMPT_MAX_CHARS} 字；为避免改变台词，系统不会自动截断。"
            )
        if len(prompt) > SDR2V_PROMPT_MAX_CHARS:
            raise ToolError(
                f"SDR2V 最终提示词共 {len(prompt)} 字，超过 {SDR2V_PROMPT_MAX_CHARS} 字限制。"
            )
        prompt_budget = {
            "max_chars": SDR2V_PROMPT_MAX_CHARS,
            "fixed_prompt_chars": fixed_prompt_chars,
            "dialogue_chars": len(text),
            "dialogue_budget_chars": dialogue_budget_chars,
            "remaining_chars": SDR2V_PROMPT_MAX_CHARS - len(prompt),
        }
    return {
        "schema_version": "analysis_v1_05_02_video_prompt_openrouter_0.1",
        "prompt_type": "video_generation",
        "provider_profile": "video_openrouter",
        "segment_id": text_value(segment.get("segment_id")),
        "dialogue_asset_keys": list_value(segment.get("dialogue_asset_keys") or segment.get("dialogue_ids")),
        "dialogue_ids": list_value(segment.get("dialogue_asset_keys") or segment.get("dialogue_ids")),
        "template_source": template_name,
        "template_snapshot_chars": len(template_text),
        "template_blocks": [name for name in positive_blocks + negative_blocks + ["VIDEO_OPENROUTER_PROMPT"] if text_value(name)],
        "positive_prompt": positive,
        "negative_prompt": negative,
        "prompt": prompt,
        "extracted_fields": {"dialogue_text": text, "duration": duration, "cutaway": cutaway, "gridded_input_scope": grid_scope, "reference_image_roles": reference_image_roles, "prompt_budget": prompt_budget},
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


def openrouter_request_payload(
    prompt: str,
    model: str,
    reference_images: list[Path],
    duration_seconds: int,
    config: dict[str, Any],
    reference_audios: list[Path] | None = None,
    reference_videos: list[Path] | None = None,
) -> dict[str, Any]:
    config = apply_public_asset_runtime_config(config)
    reference_audios = reference_audios or []
    reference_videos = reference_videos or []
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "duration": duration_seconds,
        "resolution": text_value(config.get("resolution") or config.get("default_resolution") or DEFAULT_RESOLUTION),
        "aspect_ratio": text_value(config.get("aspect_ratio") or config.get("default_aspect_ratio") or config.get("ratio") or config.get("default_ratio") or DEFAULT_ASPECT_RATIO),
    }
    if "generate_audio" in config:
        payload["generate_audio"] = bool_value(config.get("generate_audio"), True)
    if should_send_input_references(config, reference_audios, reference_videos):
        input_references = [
            *[input_reference_item(path, "image", config) for path in reference_images],
            *[input_reference_item(path, "audio", config) for path in reference_audios],
            *[input_reference_item(path, "video", config) for path in reference_videos],
        ][:DEFAULT_INPUT_REFERENCE_LIMIT]
        if input_references:
            payload["input_references"] = input_references
            payload.setdefault("generate_audio", True)
    elif bool_value(config.get("send_frame_images"), DEFAULT_SEND_FRAME_IMAGES) and reference_images:
        payload["frame_images"] = [{
            "type": "image_url",
            "image_url": {"url": frame_image_url(reference_images[0], config)},
            "frame_type": "first_frame",
        }]
    return payload


def base_url_from_config(config: dict[str, Any]) -> str:
    return text_value(config.get("base_url") or DEFAULT_BASE_URL).rstrip("/")


def polling_url_from_response(payload: dict[str, Any], base_url: str, task_id: str) -> str:
    raw = text_value(payload.get("polling_url") or dict_value(payload.get("data")).get("polling_url"))
    if not raw and task_id:
        raw = f"videos/{urllib.parse.quote(task_id, safe='')}"
    if not raw:
        return ""
    candidate = urllib.parse.urljoin(base_url.rstrip("/") + "/", raw)
    base_host = urllib.parse.urlsplit(base_url).hostname or ""
    candidate_host = urllib.parse.urlsplit(candidate).hostname or ""
    if candidate_host != base_host:
        raise ToolError(f"OpenRouter polling URL host is not allowed: {url_summary(candidate)}")
    return candidate


def content_url_from_task(base_url: str, task_id: str) -> str:
    return f"{base_url.rstrip('/')}/videos/{urllib.parse.quote(task_id, safe='')}/content"


def download_video(video_url: str, output_path: Path, headers: dict[str, str] | None = None) -> None:
    if safe_download_to_path is None:
        raise ToolError("Safe provider artifact downloader is unavailable; refusing to download OpenRouter output.")
    request_headers = {"User-Agent": "OpenCrew/openrouter-video-download"}
    if headers:
        request_headers.update(headers)
    safe_download_to_path(
        video_url,
        output_path,
        allowed_content_types=VIDEO_CONTENT_TYPES,
        max_bytes=VIDEO_MAX_BYTES,
        timeout=600,
        headers=request_headers,
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
    if text_value(state.get("provider")) not in {"", "openrouter"}:
        return {}
    return state


PRIVATE_TASK_STATE_KEYS = {"polling_url_full"}
PUBLIC_URL_SUMMARY_KEYS = {"base_url", "polling_url", "content_url", "video_url_summary"}


def private_task_state_path(path: Path | None) -> Path | None:
    if path is None:
        return None
    normalized_path = path.expanduser().resolve(strict=False)
    digest = hashlib.sha256(str(normalized_path).encode("utf-8")).hexdigest()[:12]
    name = f"{path.stem}-{digest}.json"
    for parent in normalized_path.parents:
        if parent.name == "workspace":
            return parent.parent / "meta" / "provider_private" / name
    return normalized_path.with_suffix(normalized_path.suffix + ".private")


def read_private_task_state(path: Path | None) -> dict[str, Any]:
    private_path = private_task_state_path(path)
    if private_path is None:
        return {}
    return read_json_or_empty(private_path)


def write_private_task_state(path: Path | None, payload: dict[str, Any]) -> None:
    private_path = private_task_state_path(path)
    if private_path is None:
        return
    private_payload = {key: text_value(payload.get(key)) for key in PRIVATE_TASK_STATE_KEYS if text_value(payload.get(key))}
    if not private_payload:
        return
    current = read_json_or_empty(private_path)
    next_payload = {**current, **private_payload, "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    private_path.parent.mkdir(parents=True, exist_ok=True)
    private_path.write_text(json.dumps(next_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        private_path.chmod(0o600)
    except OSError:
        pass


def public_task_state_payload(payload: dict[str, Any]) -> dict[str, Any]:
    public_payload: dict[str, Any] = {}
    for key, value in payload.items():
        if key in PRIVATE_TASK_STATE_KEYS:
            continue
        if key in PUBLIC_URL_SUMMARY_KEYS:
            summary = url_summary(text_value(value))
            public_payload[key] = summary if summary else json_safe(value)
        else:
            public_payload[key] = json_safe(value)
    return public_payload


def polling_url_from_state(path: Path | None, prior_state: dict[str, Any], base_url: str, task_id: str) -> str:
    private_state = read_private_task_state(path)
    stored_url = text_value(private_state.get("polling_url_full") or prior_state.get("polling_url_full") or prior_state.get("polling_url"))
    if stored_url:
        return polling_url_from_response({"polling_url": stored_url}, base_url, task_id)
    return polling_url_from_response({}, base_url, task_id)


def write_task_state(path: Path | None, payload: dict[str, Any]) -> None:
    if path is None:
        return
    write_private_task_state(path, payload)
    current = read_json_or_empty(path)
    next_payload = {
        **public_task_state_payload(current),
        **public_task_state_payload(payload),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(next_payload, ensure_ascii=False, indent=2), encoding="utf-8")


def generate(context: dict[str, Any], prompt_path: Path, output_path: Path) -> dict[str, Any]:
    config = apply_public_asset_runtime_config(dict_value(context.get("config")))
    api_key = text_value(config.get("api_key"))
    model = text_value(config.get("model") or DEFAULT_MODEL)
    duration = safe_float(context.get("duration_seconds"), 5.0)
    if not api_key:
        raise ToolError(f"Missing video API key for openrouter/{model}.")
    prompt = read_prompt_text(prompt_path)
    requested_reference_images = [Path(path) for path in list_value(context.get("reference_images"))]
    requested_reference_audios = [Path(path) for path in list_value(context.get("reference_audios"))]
    requested_reference_videos = [Path(path) for path in list_value(context.get("reference_videos"))]
    if bool_value(config.get("strict_input_references")):
        missing = [str(path) for path in [*requested_reference_images, *requested_reference_audios, *requested_reference_videos] if not path.exists() or not path.is_file() or path.stat().st_size <= 0]
        if missing:
            raise ToolError(f"privacy_grid_provider_preflight_failed: required input references are missing: {missing}")
    reference_images = [path for path in requested_reference_images if path.exists() and path.is_file()]
    reference_audios = [path for path in requested_reference_audios if path.exists() and path.is_file()]
    reference_videos = [path for path in requested_reference_videos if path.exists() and path.is_file()]
    seconds = clamp_duration(duration)
    base_url = base_url_from_config(config)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    started_at = time.time()
    deadline = time.time() + max(int(context.get("timeout_seconds") or 120), 60)
    request_payload = openrouter_request_payload(prompt, model, reference_images, seconds, config, reference_audios, reference_videos)
    fingerprint = request_fingerprint(prompt, model, reference_images, seconds, request_payload, reference_audios, reference_videos)
    state_path = provider_task_state_path(context)
    prior_state = matching_task_state(state_path, fingerprint, model)
    prior_task_id = text_value(prior_state.get("provider_task_id") or prior_state.get("task_id"))
    if prior_task_id and text_value(prior_state.get("status")) == "completed" and output_path.exists() and output_path.stat().st_size > 0:
        return {
            "provider": "openrouter",
            "model": model,
            "provider_profile": "video_openrouter",
            "provider_task_id": prior_task_id,
            "task_id": prior_task_id,
            "requested_duration": duration,
            "duration": seconds,
            "aspect_ratio": request_payload["aspect_ratio"],
            "resolution": request_payload["resolution"],
            "send_frame_images": bool(prior_state.get("send_frame_images")),
            "send_input_references": bool(prior_state.get("send_input_references")),
            "input_reference_count": int(safe_float(prior_state.get("input_reference_count"), 0)),
            "output_path": str(output_path),
            "video_url": text_value(prior_state.get("video_url_summary")),
            "elapsed_seconds": round(time.time() - started_at, 3),
            "cached": True,
        }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "HTTP-Referer": "https://opencrew.local",
        "X-Title": "OpenCrew",
    }
    if prior_task_id:
        task_id = prior_task_id
        poll_url = polling_url_from_state(state_path, prior_state, base_url, task_id)
    else:
        started = post_json_request(f"{base_url}/videos", request_payload, headers, timeout=120)
        task_id = task_id_from_response(started)
        if not task_id:
            raise ToolError(f"OpenRouter video response did not include task id: {json_safe(started)}")
        poll_url = polling_url_from_response(started, base_url, task_id)
        if not poll_url:
            raise ToolError(f"OpenRouter video response did not include polling URL: {json_safe(started)}")
        write_task_state(state_path, {
            "schema_version": "analysis_v1_openrouter_provider_task_0.1",
            "provider": "openrouter",
            "provider_profile": "video_openrouter",
            "model": model,
            "provider_task_id": task_id,
            "task_id": task_id,
            "fingerprint": fingerprint,
            "status": operation_status(started) or "submitted",
            "base_url": url_summary(base_url),
            "polling_url": url_summary(poll_url),
            "polling_url_full": poll_url,
            "aspect_ratio": request_payload["aspect_ratio"],
            "resolution": request_payload["resolution"],
            "duration": seconds,
            "send_frame_images": bool("frame_images" in request_payload),
            "send_input_references": bool("input_references" in request_payload),
            "input_reference_count": len(request_payload.get("input_references") or []),
        })
    video_url = ""
    downloaded = False
    last_status = ""
    while time.time() < deadline:
        polled = get_json_request(poll_url, headers, timeout=120)
        last_status = operation_status(polled)
        write_task_state(state_path, {"provider_task_id": task_id, "task_id": task_id, "fingerprint": fingerprint, "model": model, "status": last_status or "polling", "polling_url": url_summary(poll_url), "polling_url_full": poll_url})
        failure = operation_failed(polled)
        if failure:
            write_task_state(state_path, {"status": last_status or "failed", "error": failure})
            raise ToolError(f"OpenRouter video generation failed: {failure}")
        if operation_done(polled):
            video_url = first_url(polled)
            if video_url:
                break
            content_url = content_url_from_task(base_url, task_id)
            try:
                download_video(content_url, output_path, headers={"Authorization": f"Bearer {api_key}", "Accept": "video/*, application/octet-stream"})
                video_url = content_url
                downloaded = True
                break
            except Exception as exc:
                content_error = redact_secret_text(str(exc))[:600]
            write_task_state(state_path, {
                "provider_task_id": task_id,
                "task_id": task_id,
                "fingerprint": fingerprint,
                "model": model,
                "status": "completed_without_url",
                "polling_url": url_summary(poll_url),
                "polling_url_full": poll_url,
                "content_url": url_summary(content_url),
                "content_download_error": content_error,
            })
        time.sleep(5)
    if not video_url:
        write_task_state(state_path, {"status": last_status or "timeout", "error": "timeout_or_completed_without_url"})
        raise ProviderTimeout(f"OpenRouter video generation timed out or completed without URL. task_id={task_id} status={last_status or 'unknown'}")
    if not downloaded:
        download_video(video_url, output_path)
    write_task_state(state_path, {
        "provider_task_id": task_id,
        "task_id": task_id,
        "fingerprint": fingerprint,
        "model": model,
        "status": "completed",
        "error": "",
        "content_download_error": "",
        "video_url_summary": url_summary(video_url),
        "output_path": str(output_path),
        "bytes": output_path.stat().st_size if output_path.exists() else 0,
    })
    return {
        "provider": "openrouter",
        "model": model,
        "provider_profile": "video_openrouter",
        "provider_task_id": task_id,
        "task_id": task_id,
        "requested_duration": duration,
        "duration": seconds,
        "aspect_ratio": request_payload["aspect_ratio"],
        "resolution": request_payload["resolution"],
        "send_frame_images": bool("frame_images" in request_payload),
        "send_input_references": bool("input_references" in request_payload),
        "input_reference_count": len(request_payload.get("input_references") or []),
        "output_path": str(output_path),
        "video_url": video_url,
        "elapsed_seconds": round(time.time() - started_at, 3),
    }
