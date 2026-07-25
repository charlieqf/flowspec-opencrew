from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable

from fastapi import HTTPException

from .runtime import resolve_media_binary


GEMINI_OMNI_MODEL = "gemini-omni-flash-preview"
GEMINI_OMNI_API_ROOT = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_OMNI_UPLOAD_ROOT = "https://generativelanguage.googleapis.com/upload/v1beta"
GEMINI_OMNI_TASKS = {
    "text_to_video",
    "image_to_video",
    "reference_to_video",
    "edit",
}
GEMINI_OMNI_ASPECTS = {"16:9", "9:16"}
GEMINI_OMNI_MIN_DURATION_SECONDS = 3
GEMINI_OMNI_MAX_DURATION_SECONDS = 10
GEMINI_OMNI_TERMINAL_FAILURES = {"failed", "cancelled", "canceled", "rejected"}
GEMINI_OMNI_MAX_OUTPUT_BYTES = 1024 * 1024 * 1024
GEMINI_OMNI_720P_USD_PER_SECOND = 0.10


class GeminiOmniError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 502, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable

    def as_detail(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "retryable": self.retryable}

    def as_http_exception(self) -> HTTPException:
        return HTTPException(status_code=self.status_code, detail=self.as_detail())


def _text(value: Any) -> str:
    return str(value or "").strip()


def _bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    normalized = _text(value).lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def gemini_omni_enabled() -> bool:
    return _bool(os.environ.get("OPENCREW_GEMINI_OMNI_ENABLED"), False)


def require_gemini_omni_enabled() -> None:
    if not gemini_omni_enabled():
        raise GeminiOmniError(
            "gemini_omni_disabled",
            "Gemini Omni stateful video editing is disabled on this server",
            status_code=404,
        )


def _sanitized_provider_message(value: Any) -> str:
    message = _text(value)[:1000]
    message = re.sub(r"https?://\S+", "[provider URL]", message)
    message = re.sub(r"\b(?:interactions?|files?)/[A-Za-z0-9._~-]+", "[provider state]", message)
    message = re.sub(r"AIza[0-9A-Za-z_-]{20,}", "[credential]", message)
    return message or "Gemini Omni request failed"


def map_provider_error(status: int, payload: dict[str, Any] | None) -> GeminiOmniError:
    error = payload.get("error") if isinstance(payload, dict) and isinstance(payload.get("error"), dict) else {}
    provider_status = _text(error.get("status")).upper()
    message = _sanitized_provider_message(error.get("message") or (payload or {}).get("message"))
    lowered = message.lower()
    if status == 404 and ("interaction" in lowered or "previous" in lowered or "not found" in lowered):
        return GeminiOmniError("gemini_omni_interaction_expired", "The saved provider editing context is no longer available", status_code=409)
    if "store=true" in lowered or ("store" in lowered and "required" in lowered):
        return GeminiOmniError("gemini_omni_store_required", "URI delivery and stateful continuation require store=true", status_code=400)
    if status in {401, 403} and ("paid" in lowered or "billing" in lowered or "tier" in lowered):
        return GeminiOmniError("gemini_omni_paid_tier_required", "Gemini Omni requires an enabled paid-tier test or production key", status_code=403)
    if status in {401, 403} and ("region" in lowered or "location" in lowered or "country" in lowered):
        return GeminiOmniError("gemini_omni_region_unsupported", "Gemini Omni video editing is unavailable in this region", status_code=403)
    if status in {400, 422} and ("previous" in lowered or "interaction" in lowered):
        return GeminiOmniError("gemini_omni_previous_interaction_invalid", "The selected parent video context is invalid", status_code=409)
    if "cannot be set" in lowered and "edit task" in lowered and any(
        field in lowered for field in ("duration", "aspect ratio")
    ):
        return GeminiOmniError(
            "video_stateful_invalid_request",
            "Uploaded video edits inherit the input video's dimensions and duration",
            status_code=400,
        )
    if "exactly one input video" in lowered and "edit task" in lowered:
        return GeminiOmniError(
            "video_stateful_invalid_request",
            "Uploaded video editing requires exactly one video input",
            status_code=400,
        )
    if provider_status in {"SAFETY", "BLOCKED", "CONTENT_FILTERED"} or any(token in lowered for token in ("safety", "content filter", "blocked prompt")):
        return GeminiOmniError("gemini_omni_content_filtered", "The request was blocked by the provider content policy", status_code=422)
    if status == 404:
        return GeminiOmniError("gemini_omni_model_unavailable", "Gemini Omni is unavailable for this connection", status_code=404)
    retryable = status == 429 or status >= 500
    return GeminiOmniError(
        "gemini_omni_model_unavailable" if status in {401, 403} else "gemini_omni_request_failed",
        message,
        status_code=503 if retryable else 502,
        retryable=retryable,
    )


def omni_task_for(
    operation: str,
    *,
    image_count: int = 0,
    video_count: int = 0,
) -> str:
    operation = _text(operation).lower()
    if operation in {"edit", "continue"} or video_count:
        return "edit"
    if image_count > 1:
        return "reference_to_video"
    if image_count == 1:
        return "image_to_video"
    return "text_to_video"


def build_interaction_request(
    *,
    prompt: str,
    task: str,
    aspect_ratio: str,
    delivery: str = "uri",
    store: bool = True,
    background: bool = True,
    previous_interaction_id: str = "",
    file_inputs: list[dict[str, str]] | None = None,
    duration_seconds: int | None = None,
) -> dict[str, Any]:
    task = _text(task)
    if task not in GEMINI_OMNI_TASKS:
        raise GeminiOmniError("video_stateful_invalid_request", f"Unsupported Omni video task: {task}", status_code=400)
    aspect_ratio = _text(aspect_ratio)
    if aspect_ratio not in GEMINI_OMNI_ASPECTS:
        raise GeminiOmniError("video_stateful_invalid_request", "Gemini Omni supports only 16:9 and 9:16", status_code=400)
    delivery = _text(delivery).lower() or "uri"
    if delivery == "uri" and not store:
        raise GeminiOmniError("gemini_omni_store_required", "URI delivery requires store=true", status_code=400)
    if _text(previous_interaction_id) and not store:
        raise GeminiOmniError("gemini_omni_store_required", "Stateful continuation requires store=true", status_code=400)

    inputs = [item for item in (file_inputs or []) if isinstance(item, dict)]
    input_value: Any = _text(prompt)
    if inputs:
        input_value = [*inputs, {"type": "text", "text": _text(prompt)}]
    # Interactions API revision 2026-05-20 keeps model behavior in
    # generation_config and moves video output controls to response_format.
    video_config: dict[str, Any] = {"task": task}
    has_uploaded_video = any(
        _text(item.get("type")).lower() == "video"
        or (
            _text(item.get("type")).lower() == "document"
            and _text(item.get("mime_type") or item.get("mimeType")).lower().startswith("video/")
        )
        for item in inputs
    )
    uploaded_video_edit = task == "edit" and has_uploaded_video and not _text(previous_interaction_id)
    response_format: dict[str, Any] = {
        "type": "video",
        "delivery": delivery,
    }
    if not uploaded_video_edit:
        response_format["aspect_ratio"] = aspect_ratio
    if duration_seconds is not None and not uploaded_video_edit:
        normalized_duration = int(duration_seconds)
        if not GEMINI_OMNI_MIN_DURATION_SECONDS <= normalized_duration <= GEMINI_OMNI_MAX_DURATION_SECONDS:
            raise GeminiOmniError(
                "video_stateful_invalid_request",
                "Gemini Omni output duration must be between 3 and 10 seconds",
                status_code=400,
            )
        response_format["duration"] = f"{normalized_duration}s"
    payload: dict[str, Any] = {
        "model": GEMINI_OMNI_MODEL,
        "input": input_value,
        "response_format": response_format,
        "store": bool(store),
        "background": bool(background),
    }
    if _text(previous_interaction_id):
        payload["previous_interaction_id"] = _text(previous_interaction_id)
    elif not uploaded_video_edit:
        # The current Preview API infers conversational editing from
        # previous_interaction_id and uploaded Files API document inputs. It
        # rejects video_config.task for both edit forms. Fresh text/image
        # generation still uses the explicit task discriminator.
        payload["generation_config"] = {"video_config": video_config}
    return payload


def extract_video_parts(payload: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def visit(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, dict):
            return
        mime = _text(value.get("mime_type") or value.get("mimeType")).lower()
        value_type = _text(value.get("type")).lower()
        if value_type == "video" or mime.startswith("video/"):
            found.append(value)
            return
        for key, item in value.items():
            if key not in {"error", "request", "input"}:
                visit(item)

    visit(payload)
    return found


def _inline_video_bytes(part: dict[str, Any]) -> bytes | None:
    encoded = part.get("data") or part.get("bytes_base64_encoded") or part.get("bytesBase64Encoded")
    if not isinstance(encoded, str) or not encoded:
        inline = part.get("inline_data") or part.get("inlineData")
        if isinstance(inline, dict):
            encoded = inline.get("data")
    if not isinstance(encoded, str) or not encoded:
        return None
    try:
        return base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise GeminiOmniError("gemini_omni_video_output_missing", "Gemini Omni returned invalid video data", status_code=502) from exc


def _video_uri(part: dict[str, Any]) -> str:
    uri = _text(part.get("uri") or part.get("url"))
    if uri:
        return uri
    file_data = part.get("file_data") or part.get("fileData")
    return _text(file_data.get("file_uri") or file_data.get("fileUri")) if isinstance(file_data, dict) else ""


def validate_provider_video_uri(uri: str) -> str:
    parsed = urllib.parse.urlparse(_text(uri))
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (host == "googleapis.com" or host.endswith(".googleapis.com")):
        raise GeminiOmniError("gemini_omni_video_output_missing", "Gemini Omni returned an untrusted video download location", status_code=502)
    return parsed.geturl()


def validate_video_file(path: Path, *, probe: Callable[[Path], dict[str, Any]] | None = None) -> dict[str, Any]:
    try:
        size = path.stat().st_size
        header = path.read_bytes()[:64]
    except OSError as exc:
        raise GeminiOmniError("gemini_omni_video_output_missing", "Generated video file is unavailable", status_code=502) from exc
    if size <= 16 or size > GEMINI_OMNI_MAX_OUTPUT_BYTES or b"ftyp" not in header:
        raise GeminiOmniError("gemini_omni_video_output_missing", "Gemini Omni output is not a valid MP4 file", status_code=502)
    if probe is None:
        probe = probe_video_file
    info = probe(path)
    if not info.get("has_video"):
        raise GeminiOmniError("gemini_omni_video_output_missing", "Gemini Omni output contains no playable video stream", status_code=502)
    return {"size_bytes": size, **info}


def probe_video_file(path: Path) -> dict[str, Any]:
    try:
        process = subprocess.run(
            [
                resolve_media_binary("ffprobe"),
                "-v",
                "error",
                "-show_entries",
                "stream=codec_type,width,height:format=duration",
                "-of",
                "json",
                str(path),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GeminiOmniError("gemini_omni_video_output_missing", "Generated video could not be inspected", status_code=502) from exc
    if process.returncode != 0:
        raise GeminiOmniError("gemini_omni_video_output_missing", "Generated video failed media validation", status_code=502)
    try:
        payload = json.loads(process.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise GeminiOmniError("gemini_omni_video_output_missing", "Generated video inspection returned invalid data", status_code=502) from exc
    streams = payload.get("streams") if isinstance(payload.get("streams"), list) else []
    video = next((item for item in streams if isinstance(item, dict) and item.get("codec_type") == "video"), {})
    format_info = payload.get("format") if isinstance(payload.get("format"), dict) else {}
    try:
        duration = float(format_info.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    return {
        "has_video": bool(video),
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "duration_seconds": round(duration, 3),
    }


def materialize_video_output(
    payload: dict[str, Any],
    output_path: Path,
    *,
    api_key: str,
    download_video_binary: Callable[..., None],
    sanitize_video_output: Callable[[Path], None],
    probe: Callable[[Path], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    parts = extract_video_parts(payload)
    if not parts:
        raise GeminiOmniError("gemini_omni_video_output_missing", "Gemini Omni completed without a video output", status_code=502)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Preserve the MP4 suffix for the shared ffmpeg metadata sanitizer. It
    # infers the output muxer from the temporary path extension.
    temporary = output_path.with_name(f".{output_path.stem}.{uuid.uuid4().hex}.provider.mp4")
    try:
        for part in parts:
            mime = _text(part.get("mime_type") or part.get("mimeType") or "video/mp4").lower()
            if mime and not mime.startswith("video/"):
                continue
            inline = _inline_video_bytes(part)
            if inline is not None:
                if len(inline) > GEMINI_OMNI_MAX_OUTPUT_BYTES:
                    raise GeminiOmniError("gemini_omni_video_output_missing", "Gemini Omni output exceeds the configured size limit", status_code=502)
                temporary.write_bytes(inline)
                sanitize_video_output(temporary)
                info = validate_video_file(temporary, probe=probe)
                temporary.replace(output_path)
                return {**info, "delivery": "inline"}
            uri = _video_uri(part)
            if uri:
                download_video_binary(
                    validate_provider_video_uri(uri),
                    temporary,
                    {"x-goog-api-key": api_key},
                    provider="gemini",
                )
                # download_video_binary already invokes the shared sanitize sink.
                info = validate_video_file(temporary, probe=probe)
                temporary.replace(output_path)
                return {**info, "delivery": "uri"}
        raise GeminiOmniError("gemini_omni_video_output_missing", "Gemini Omni returned no usable video output", status_code=502)
    except GeminiOmniError:
        raise
    except Exception as exc:
        # Shared download/sanitize failures can contain local absolute paths or
        # ffmpeg command details. Keep those in the chained server exception,
        # never in the public SSE error body or persisted browser history.
        raise GeminiOmniError(
            "gemini_omni_video_output_missing",
            "Gemini Omni video output could not be downloaded, sanitized, or validated",
            status_code=502,
        ) from exc
    finally:
        temporary.unlink(missing_ok=True)


class GeminiOmniClient:
    def __init__(
        self,
        api_key: str,
        *,
        api_root: str = GEMINI_OMNI_API_ROOT,
        upload_root: str = GEMINI_OMNI_UPLOAD_ROOT,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
        urlopen: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        self.api_key = _text(api_key)
        self.api_root = api_root.rstrip("/")
        self.upload_root = upload_root.rstrip("/")
        self.sleep = sleep
        self.clock = clock
        self.urlopen = urlopen
        if not self.api_key:
            raise GeminiOmniError("gemini_omni_model_unavailable", "Gemini API key is unavailable", status_code=400)

    def _request(
        self,
        method: str,
        url: str,
        *,
        payload: dict[str, Any] | None = None,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: int = 120,
        attempts: int = 3,
    ) -> tuple[int, dict[str, Any], dict[str, str]]:
        request_body = body if body is not None else (json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None)
        request_headers = {
            "Accept": "application/json",
            "x-goog-api-key": self.api_key,
            **({"Content-Type": "application/json"} if payload is not None else {}),
            **(headers or {}),
        }
        last_error: GeminiOmniError | None = None
        for attempt in range(1, max(1, attempts) + 1):
            request = urllib.request.Request(url, data=request_body, headers=request_headers, method=method)
            try:
                with self.urlopen(request, timeout=timeout) as response:
                    raw = response.read()
                    parsed = json.loads(raw.decode("utf-8")) if raw else {}
                    response_headers = {str(key).lower(): str(value) for key, value in response.headers.items()}
                    return int(response.status), parsed if isinstance(parsed, dict) else {}, response_headers
            except urllib.error.HTTPError as exc:
                try:
                    parsed = json.loads(exc.read().decode("utf-8", errors="replace"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    parsed = {"error": {"message": "Provider returned a non-JSON error"}}
                last_error = map_provider_error(int(exc.code), parsed if isinstance(parsed, dict) else {})
                if last_error.retryable and attempt < attempts:
                    self.sleep(min(2 ** (attempt - 1), 4))
                    continue
                raise last_error from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = GeminiOmniError("gemini_omni_request_failed", "Gemini Omni network request failed", status_code=503, retryable=True)
                if attempt < attempts:
                    self.sleep(min(2 ** (attempt - 1), 4))
                    continue
                raise last_error from exc
        raise last_error or GeminiOmniError("gemini_omni_request_failed", "Gemini Omni request failed", status_code=503)

    def get_interaction(self, interaction_id: str) -> dict[str, Any]:
        interaction_id = _text(interaction_id)
        if not interaction_id:
            raise GeminiOmniError("gemini_omni_previous_interaction_invalid", "Parent provider state is missing", status_code=409)
        _, payload, _ = self._request("GET", f"{self.api_root}/interactions/{urllib.parse.quote(interaction_id, safe='')}")
        return payload

    def delete_interaction(self, interaction_id: str) -> None:
        interaction_id = _text(interaction_id)
        if interaction_id:
            self._request("DELETE", f"{self.api_root}/interactions/{urllib.parse.quote(interaction_id, safe='')}")

    def create_interaction(
        self,
        request_payload: dict[str, Any],
        *,
        interaction_callback: Callable[[str, int | None, str], None] | None = None,
    ) -> dict[str, Any]:
        # Creating an Interaction is a paid, non-idempotent boundary. A lost
        # response is reconciled from durable state; it is never blindly POSTed again.
        _, payload, _ = self._request(
            "POST",
            f"{self.api_root}/interactions",
            payload=request_payload,
            timeout=180,
            attempts=1,
        )
        interaction_id = _text(payload.get("id") or payload.get("interaction_id"))
        if not interaction_id:
            raise GeminiOmniError("gemini_omni_video_output_missing", "Gemini Omni response did not include provider state", status_code=502)
        expires_at = payload.get("expires_at") or payload.get("expiresAt")
        expiry_ms: int | None = None
        if isinstance(expires_at, (int, float)):
            expiry_ms = int(expires_at if expires_at > 10_000_000_000 else expires_at * 1000)
        if interaction_callback:
            interaction_callback(interaction_id, expiry_ms, "provider" if expiry_ms else "unknown")
        return payload

    def poll_interaction(
        self,
        interaction_id: str,
        *,
        timeout_seconds: int = 900,
        interval_seconds: float = 5,
        lease_callback: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        deadline = self.clock() + timeout_seconds
        while self.clock() < deadline:
            payload = self.get_interaction(interaction_id)
            status = _text(payload.get("status")).lower()
            if status in {"completed", "succeeded", "success"}:
                return payload
            if status in GEMINI_OMNI_TERMINAL_FAILURES:
                error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
                raise map_provider_error(422 if "safety" in _text(error.get("status")).lower() else 502, payload)
            if lease_callback:
                lease_callback()
            self.sleep(interval_seconds)
        raise GeminiOmniError("gemini_omni_request_failed", "Gemini Omni generation timed out; the existing provider request will be reconciled", status_code=504, retryable=False)

    def run_interaction(
        self,
        request_payload: dict[str, Any],
        *,
        interaction_callback: Callable[[str, int | None, str], None] | None = None,
        lease_callback: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        started = self.create_interaction(request_payload, interaction_callback=interaction_callback)
        interaction_id = _text(started.get("id") or started.get("interaction_id"))
        status = _text(started.get("status")).lower()
        if status in {"completed", "succeeded", "success"}:
            return started
        return self.poll_interaction(interaction_id, lease_callback=lease_callback)

    def upload_file(
        self,
        path: Path,
        *,
        timeout_seconds: int = 300,
        interval_seconds: float = 2,
    ) -> dict[str, str]:
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise GeminiOmniError("gemini_omni_file_processing_failed", "Video input file is unavailable", status_code=400) from exc
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        _, _, headers = self._request(
            "POST",
            f"{self.upload_root}/files",
            payload={"file": {"display_name": path.name}},
            headers={
                "X-Goog-Upload-Protocol": "resumable",
                "X-Goog-Upload-Command": "start",
                "X-Goog-Upload-Header-Content-Length": str(size),
                "X-Goog-Upload-Header-Content-Type": mime,
            },
        )
        upload_url = _text(headers.get("x-goog-upload-url"))
        if not upload_url:
            raise GeminiOmniError("gemini_omni_file_processing_failed", "Gemini Files API did not return an upload session", status_code=502)
        _, uploaded, _ = self._request(
            "POST",
            upload_url,
            body=path.read_bytes(),
            headers={
                "Content-Type": mime,
                "X-Goog-Upload-Offset": "0",
                "X-Goog-Upload-Command": "upload, finalize",
            },
            timeout=300,
            attempts=2,
        )
        file_info = uploaded.get("file") if isinstance(uploaded.get("file"), dict) else uploaded
        name = _text(file_info.get("name"))
        uri = _text(file_info.get("uri"))
        if not name:
            raise GeminiOmniError("gemini_omni_file_processing_failed", "Gemini Files API did not return a file name", status_code=502)
        deadline = self.clock() + timeout_seconds
        while self.clock() < deadline:
            _, current, _ = self._request("GET", f"{self.api_root}/{name.lstrip('/')}")
            current_info = current.get("file") if isinstance(current.get("file"), dict) else current
            state_value = current_info.get("state")
            state = _text(state_value.get("name") if isinstance(state_value, dict) else state_value).upper()
            uri = _text(current_info.get("uri")) or uri
            if state == "ACTIVE":
                if not uri:
                    raise GeminiOmniError("gemini_omni_file_processing_failed", "Active Gemini file has no URI", status_code=502)
                return {"name": name, "uri": uri, "mime_type": mime}
            if state in {"FAILED", "ERROR", "CANCELLED", "CANCELED"}:
                raise GeminiOmniError("gemini_omni_file_processing_failed", "Gemini could not process the uploaded video", status_code=422)
            self.sleep(interval_seconds)
        raise GeminiOmniError("gemini_omni_file_processing_failed", "Gemini file processing timed out", status_code=504)


def file_input(file_info: dict[str, str], *, media_type: str = "video") -> dict[str, str]:
    return {
        # The current Files API example represents an uploaded video URI as a
        # document input. Inline/base64 video content continues to use type=video.
        "type": "document" if _text(media_type).lower() == "video" else media_type,
        "uri": _text(file_info.get("uri")),
        "mime_type": _text(file_info.get("mime_type")),
    }
