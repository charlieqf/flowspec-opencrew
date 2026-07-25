from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

TMPFILES_UPLOAD_URL = "https://tmpfiles.org/api/v1/upload"
TMPFILES_USER_AGENT = "OpenCrew/kling-tmpfiles-upload"


class PublisherError(RuntimeError):
    pass


def text_value(value: Any) -> str:
    return str(value or "").strip()


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def configured_provider(config: dict[str, Any]) -> str:
    return text_value(
        config.get("public_asset_provider")
        or config.get("public_url_provider")
        or os.environ.get("OPENCREW_PUBLIC_ASSET_PROVIDER")
    ).lower()


def tmpfiles_direct_url(url: str) -> str:
    value = text_value(url).replace("\\/", "/")
    parsed = urllib.parse.urlparse(value)
    if not parsed.netloc.endswith("tmpfiles.org"):
        return value
    path = parsed.path or ""
    if path.startswith("/dl/"):
        return value
    return urllib.parse.urlunparse((parsed.scheme or "https", parsed.netloc, f"/dl{path}", "", "", ""))


def first_url(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("url", "download_url", "public_url", "link"):
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


def post_multipart_file(url: str, path: Path, *, field_name: str = "file", timeout: int = 180) -> dict[str, Any]:
    boundary = f"----OpenCrewPublicAsset{uuid.uuid4().hex}"
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    body = b"".join([
        f"--{boundary}\r\n".encode("utf-8"),
        f'Content-Disposition: form-data; name="{field_name}"; filename="{path.name}"\r\n'.encode("utf-8"),
        f"Content-Type: {mime}\r\n\r\n".encode("utf-8"),
        path.read_bytes(),
        b"\r\n",
        f"--{boundary}--\r\n".encode("utf-8"),
    ])
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
            "User-Agent": TMPFILES_USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw.strip() else {}
    except TimeoutError as exc:
        raise PublisherError(f"tmpfiles upload timed out for {path.name}") from exc
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1200]
        raise PublisherError(f"tmpfiles upload failed HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise PublisherError(f"tmpfiles upload failed: {exc.reason}") from exc
    except OSError as exc:
        raise PublisherError(f"tmpfiles upload failed: {exc}") from exc


def should_retry_tmpfiles_error(error: str) -> bool:
    text = error.lower()
    return any(token in text for token in ("broken pipe", "connection reset", "timed out", "temporarily unavailable"))


def verify_public_url(url: str, *, timeout: int = 30) -> None:
    request = urllib.request.Request(url, headers={"Range": "bytes=0-0", "User-Agent": "OpenCrew/public-asset-check"}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if int(response.status) >= 400:
                raise PublisherError(f"public URL verification failed HTTP {response.status}: {url}")
    except urllib.error.HTTPError as exc:
        if exc.code not in {200, 206}:
            raise PublisherError(f"public URL verification failed HTTP {exc.code}: {url}") from exc
    except urllib.error.URLError as exc:
        raise PublisherError(f"public URL verification failed: {exc.reason}") from exc


def publish_tmpfiles(path: Path, config: dict[str, Any], purpose: str) -> dict[str, Any]:
    upload_url = text_value(config.get("tmpfiles_upload_url") or TMPFILES_UPLOAD_URL)
    timeout = int(safe_float(config.get("public_asset_upload_timeout_seconds"), 180))
    attempts = max(1, int(safe_float(config.get("public_asset_upload_attempts"), 2)))
    payload: dict[str, Any] = {}
    last_error = ""
    for attempt in range(1, attempts + 1):
        try:
            payload = post_multipart_file(upload_url, path, timeout=timeout)
            break
        except PublisherError as exc:
            last_error = str(exc)
            if attempt < attempts and should_retry_tmpfiles_error(last_error):
                time.sleep(min(5, attempt * 2))
                continue
            raise
    if not payload:
        raise PublisherError(last_error or f"tmpfiles upload failed for {path.name}")
    hosted_url = first_url(payload)
    public_url = tmpfiles_direct_url(hosted_url)
    if not public_url:
        raise PublisherError(f"tmpfiles upload response did not include URL: {json.dumps(payload, ensure_ascii=False)[:1200]}")
    if text_value(config.get("public_asset_verify_url")).lower() in {"1", "true", "yes", "on"}:
        verify_public_url(public_url)
    return {
        "provider": "tmpfiles",
        "purpose": purpose,
        "path": str(path),
        "filename": path.name,
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "hosted_url": hosted_url,
        "public_url": public_url,
        "created_at": int(time.time()),
        "ttl_note": "tmpfiles.org is temporary hosting; use only for tests or short-lived tasks.",
        "cleanup_supported": False,
    }


def publish_file(path: Path | str, config: dict[str, Any] | None = None, *, purpose: str = "provider_input") -> dict[str, Any]:
    target = Path(path)
    if not target.exists() or not target.is_file():
        raise PublisherError(f"public asset source file missing: {target}")
    resolved_config = config or {}
    provider = configured_provider(resolved_config)
    if provider in {"", "none", "off", "disabled"}:
        raise PublisherError(
            "Local file needs a public URL, but public asset publishing is not configured. "
            "Set public_asset_provider=tmpfiles for testing, or configure an OSS/S3/CDN publisher."
        )
    if provider in {"tmpfiles", "tmpfiles.org"}:
        return publish_tmpfiles(target, resolved_config, purpose)
    raise PublisherError(f"Unsupported public asset provider: {provider}")
