from __future__ import annotations

import asyncio
import hashlib
import html
import ipaddress
import json
import mimetypes
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


PEXELS_IMAGE_SEARCH_ENDPOINT = "https://api.pexels.com/v1/search"
PEXELS_VIDEO_SEARCH_ENDPOINT = "https://api.pexels.com/v1/videos/search"
PEXELS_IMAGE_DETAIL_ENDPOINT = "https://api.pexels.com/v1/photos/{asset_id}"
PEXELS_VIDEO_DETAIL_ENDPOINT = "https://api.pexels.com/v1/videos/videos/{asset_id}"
PIXABAY_IMAGE_SEARCH_ENDPOINT = "https://pixabay.com/api/"
PIXABAY_VIDEO_SEARCH_ENDPOINT = "https://pixabay.com/api/videos/"
UNSPLASH_SEARCH_ENDPOINT = "https://api.unsplash.com/search/photos"
UNSPLASH_PHOTO_ENDPOINT = "https://api.unsplash.com/photos/{asset_id}"
WIKIMEDIA_SEARCH_ENDPOINT = "https://commons.wikimedia.org/w/api.php"
ASSET_SEARCH_USER_AGENT = "OpenCrew-Koubo-AssetSearch/1.0"

PROVIDER_HOST_ALLOWLISTS: dict[str, tuple[str, ...]] = {
    "pexels": ("pexels.com", "*.pexels.com", "images.pexels.com", "videos.pexels.com", "player.vimeo.com"),
    "pixabay": ("pixabay.com", "*.pixabay.com", "cdn.pixabay.com"),
    "unsplash": ("image.unsplash.com", "images.unsplash.com", "plus.unsplash.com", "hd.unsplash.com"),
    "wikimedia": ("upload.wikimedia.org", "commons.wikimedia.org"),
}


class AssetSearchProviderError(RuntimeError):
    pass


class AssetSearchRateLimit(AssetSearchProviderError):
    pass


class AssetSearchDownloadError(AssetSearchProviderError):
    pass


class AssetSearchSecurityError(AssetSearchProviderError):
    pass


def text(value: Any, fallback: str = "") -> str:
    if value is None or value == "":
        value = fallback
    return str(value or "").strip()


def number(value: Any, fallback: float = 0.0) -> float:
    try:
        parsed = float(value)
        return parsed if parsed == parsed else fallback
    except Exception:
        return fallback


def int_value(value: Any, fallback: int = 0) -> int:
    try:
        return int(value or fallback)
    except Exception:
        return fallback


def strip_html(value: Any) -> str:
    raw = re.sub(r"<[^>]+>", " ", text(value))
    return re.sub(r"\s+", " ", html.unescape(raw)).strip()


def normalized_host(value: str) -> str:
    host = text(value).rstrip(".").lower()
    if not host:
        return ""
    try:
        return host.encode("idna").decode("ascii").lower()
    except Exception:
        return host


def _host_is_public_name(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return bool(host and host not in {"localhost", "localhost.localdomain"})
    return not (address.is_private or address.is_loopback or address.is_link_local or address.is_reserved or address.is_unspecified)


def provider_host_allowed(provider_id: str, host: str) -> bool:
    normalized = normalized_host(host)
    if not _host_is_public_name(normalized):
        return False
    for pattern in PROVIDER_HOST_ALLOWLISTS.get(provider_id, ()):
        allowed = normalized_host(pattern)
        if allowed.startswith("*.") and normalized.endswith(allowed[1:]) and normalized != allowed[2:]:
            return True
        if normalized == allowed:
            return True
    return False


def validate_provider_url(provider_id: str, url: str) -> str:
    parsed = urllib.parse.urlparse(text(url))
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise AssetSearchSecurityError("download_url must be an https provider URL")
    if parsed.username or parsed.password:
        raise AssetSearchSecurityError("download_url must not include userinfo")
    if not provider_host_allowed(provider_id, parsed.hostname):
        raise AssetSearchSecurityError(f"download_url host is not allowed for {provider_id}: {parsed.hostname}")
    return urllib.parse.urlunparse(parsed)


def orientation_for(width: Any, height: Any) -> str:
    w = number(width)
    h = number(height)
    if not w or not h:
        return "unknown"
    ratio = w / h
    if 0.9 <= ratio <= 1.1:
        return "square"
    return "landscape" if ratio > 1.1 else "portrait"


def aspect_orientation(aspect: str) -> str:
    value = text(aspect).lower()
    if value in {"16:9", "4:3", "landscape", "horizontal"}:
        return "landscape"
    if value in {"9:16", "3:4", "portrait", "vertical"}:
        return "portrait"
    if value in {"1:1", "square"}:
        return "square"
    return ""


def _safe_tags(value: Any) -> list[str]:
    if isinstance(value, list):
        return [text(item) for item in value if text(item)][:16]
    return [part.strip() for part in re.split(r"[,;]", text(value)) if part.strip()][:16]


def _mime_from_url(url: str, fallback: str) -> str:
    guessed = mimetypes.guess_type(urllib.parse.urlparse(text(url)).path)[0]
    return text(guessed, fallback)


def _candidate(
    *,
    provider: str,
    provider_asset_id: str,
    media_type: str,
    title: str,
    description: str = "",
    preview_url: str = "",
    thumbnail_url: str = "",
    download_url: str = "",
    source_url: str = "",
    creator: dict[str, Any] | None = None,
    license_payload: dict[str, Any] | None = None,
    width: Any = 0,
    height: Any = 0,
    duration_seconds: Any = None,
    mime_type: str = "",
    tags: list[str] | None = None,
    provider_rank: int = 1,
    import_supported: bool = True,
    import_unsupported_reason: str = "",
    raw: dict[str, Any] | None = None,
) -> dict[str, Any]:
    asset_id = text(provider_asset_id)
    kind = "image" if media_type == "photo" else text(media_type, "image").lower()
    w = int_value(width)
    h = int_value(height)
    license_info = license_payload or {
        "name": f"{provider.title()} License",
        "url": source_url,
        "requires_attribution": False,
        "attribution_text": "",
        "license_status": "confirmed",
    }
    score_base = max(0.15, 1.0 - max(0, provider_rank - 1) * 0.035)
    license_bonus = 0.03 if license_info.get("license_status") == "confirmed" else -0.08
    quality_bonus = 0.05 if w >= 1280 or h >= 720 else 0.0
    score = max(0.0, min(1.0, score_base + license_bonus + quality_bonus))
    reasons = ["provider relevance rank"]
    if w and h:
        reasons.append(f"{orientation_for(w, h)} ratio")
    if license_info.get("license_status") == "confirmed":
        reasons.append("license metadata confirmed")
    return {
        "schema_version": "koubo_asset_search_candidate_0.1",
        "candidate_id": f"{provider}_{kind}_{asset_id}",
        "provider": provider,
        "provider_asset_id": asset_id,
        "media_type": kind,
        "title": title or f"{provider.title()} {kind} {asset_id}",
        "description": description,
        "preview_url": preview_url,
        "thumbnail_url": thumbnail_url or preview_url,
        "download_url": download_url,
        "source_url": source_url,
        "creator": creator or {"name": "", "url": ""},
        "license": license_info,
        "width": w,
        "height": h,
        "duration_seconds": None if duration_seconds is None else number(duration_seconds),
        "mime_type": mime_type or _mime_from_url(download_url or preview_url, "video/mp4" if kind == "video" else "image/jpeg"),
        "orientation": orientation_for(w, h),
        "tags": tags or [],
        "provider_rank": provider_rank,
        "import_supported": bool(import_supported),
        "import_unsupported_reason": import_unsupported_reason,
        "allowed_actions": ["preview", "import_whole"],
        "score": round(score, 4),
        "score_reasons": reasons,
        "raw": raw or {},
    }


def _json_request_sync(url: str, headers: dict[str, str] | None = None, timeout: int = 30) -> dict[str, Any]:
    request_headers = {"Accept": "application/json", "User-Agent": ASSET_SEARCH_USER_AGENT, **(headers or {})}
    request = urllib.request.Request(url, headers=request_headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise AssetSearchRateLimit("provider rate limited the request") from exc
        detail = exc.read().decode("utf-8", errors="ignore")[:1000]
        raise AssetSearchProviderError(f"provider HTTP {exc.code}: {detail or exc.reason}") from exc
    except Exception as exc:
        raise AssetSearchProviderError(str(exc)) from exc
    try:
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else {}
    except Exception as exc:
        raise AssetSearchProviderError("provider returned non-JSON response") from exc


async def json_request(url: str, headers: dict[str, str] | None = None, timeout: int = 30) -> dict[str, Any]:
    return await asyncio.to_thread(_json_request_sync, url, headers, timeout)


class AssetSearchProvider:
    provider_id = ""
    download_host_allowlist: tuple[str, ...] = ()

    def __init__(self, api_key: str = "") -> None:
        self.api_key = text(api_key)

    async def search_raw(
        self,
        *,
        query: str,
        media_type: str,
        aspect: str = "auto",
        limit: int = 12,
        page: int = 1,
        language: str = "en",
        safe_search: bool = True,
    ) -> dict[str, Any]:
        raise NotImplementedError

    async def refresh_candidate(self, candidate: dict[str, Any]) -> dict[str, Any] | None:
        return None

    async def prepare_import_candidate(self, candidate: dict[str, Any]) -> dict[str, Any]:
        return candidate

    def response_items(self, response: dict[str, Any], media_type: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    def normalize(self, raw: dict[str, Any], media_type: str, provider_rank: int = 1) -> dict[str, Any]:
        raise NotImplementedError


class PexelsProvider(AssetSearchProvider):
    provider_id = "pexels"
    download_host_allowlist = PROVIDER_HOST_ALLOWLISTS["pexels"]

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise AssetSearchProviderError("Pexels API key is not configured")
        return {"Accept": "application/json", "Authorization": self.api_key}

    async def search_raw(
        self,
        *,
        query: str,
        media_type: str,
        aspect: str = "auto",
        limit: int = 12,
        page: int = 1,
        language: str = "en",
        safe_search: bool = True,
    ) -> dict[str, Any]:
        if media_type not in {"image", "video"}:
            return {}
        del language, safe_search
        endpoint = PEXELS_VIDEO_SEARCH_ENDPOINT if media_type == "video" else PEXELS_IMAGE_SEARCH_ENDPOINT
        params = {
            "query": query,
            "per_page": str(max(1, min(int(limit or 12), 80))),
            "page": str(max(1, int(page or 1))),
        }
        orientation = aspect_orientation(aspect)
        if orientation:
            params["orientation"] = orientation
        return await json_request(f"{endpoint}?{urllib.parse.urlencode(params)}", self._headers())

    async def refresh_candidate(self, candidate: dict[str, Any]) -> dict[str, Any] | None:
        asset_id = urllib.parse.quote(text(candidate.get("provider_asset_id")), safe="")
        if not asset_id:
            return None
        endpoint = PEXELS_VIDEO_DETAIL_ENDPOINT if candidate.get("media_type") == "video" else PEXELS_IMAGE_DETAIL_ENDPOINT
        payload = await json_request(endpoint.format(asset_id=asset_id), self._headers())
        if not payload:
            return None
        return self.normalize(payload, text(candidate.get("media_type"), "image"), int_value(candidate.get("provider_rank"), 1))

    def response_items(self, response: dict[str, Any], media_type: str) -> list[dict[str, Any]]:
        key = "videos" if media_type == "video" else "photos"
        return [item for item in response.get(key) or [] if isinstance(item, dict)]

    def normalize(self, raw: dict[str, Any], media_type: str, provider_rank: int = 1) -> dict[str, Any]:
        if media_type == "video":
            files = [item for item in raw.get("video_files") or [] if isinstance(item, dict) and text(item.get("link"))]
            files.sort(key=lambda item: (int_value(item.get("width")) * int_value(item.get("height")), int_value(item.get("fps"))), reverse=True)
            download_file = files[0] if files else {}
            preview_choices = sorted(
                files,
                key=lambda item: (
                    int_value(item.get("width")) * int_value(item.get("height")) or 10**12,
                    int_value(item.get("fps")) or 0,
                ),
            )
            preview_file = next(
                (
                    item
                    for item in preview_choices
                    if int_value(item.get("width")) and int_value(item.get("height")) and int_value(item.get("width")) <= 1280 and int_value(item.get("height")) <= 720
                ),
                preview_choices[0] if preview_choices else download_file,
            )
            width = download_file.get("width") or raw.get("width")
            height = download_file.get("height") or raw.get("height")
            user = raw.get("user") if isinstance(raw.get("user"), dict) else {}
            creator_name = text(user.get("name") or raw.get("user", {}).get("name") if isinstance(raw.get("user"), dict) else "")
            return _candidate(
                provider=self.provider_id,
                provider_asset_id=text(raw.get("id")),
                media_type="video",
                title=text(raw.get("alt") or raw.get("url") or raw.get("id")),
                description=text(raw.get("alt")),
                preview_url=text(preview_file.get("link")),
                thumbnail_url=text(raw.get("image")),
                download_url=text(download_file.get("link")),
                source_url=text(raw.get("url")),
                creator={"name": creator_name, "url": text(user.get("url"))},
                license_payload={
                    "name": "Pexels License",
                    "url": "https://www.pexels.com/license/",
                    "requires_attribution": False,
                    "attribution_text": f"Video by {creator_name} on Pexels".strip(),
                    "license_status": "confirmed",
                },
                width=width,
                height=height,
                duration_seconds=raw.get("duration"),
                mime_type=text(download_file.get("file_type"), "video/mp4"),
                tags=[],
                provider_rank=provider_rank,
                raw=raw,
            )
        src = raw.get("src") if isinstance(raw.get("src"), dict) else {}
        preview = text(src.get("large2x") or src.get("large") or src.get("medium") or src.get("original"))
        download = text(src.get("original") or preview)
        creator_name = text(raw.get("photographer"))
        return _candidate(
            provider=self.provider_id,
            provider_asset_id=text(raw.get("id")),
            media_type="image",
            title=text(raw.get("alt") or raw.get("url") or raw.get("id")),
            description=text(raw.get("alt")),
            preview_url=preview,
            thumbnail_url=text(src.get("medium") or src.get("small") or preview),
            download_url=download,
            source_url=text(raw.get("url")),
            creator={"name": creator_name, "url": text(raw.get("photographer_url"))},
            license_payload={
                "name": "Pexels License",
                "url": "https://www.pexels.com/license/",
                "requires_attribution": False,
                "attribution_text": f"Photo by {creator_name} on Pexels".strip(),
                "license_status": "confirmed",
            },
            width=raw.get("width"),
            height=raw.get("height"),
            mime_type=_mime_from_url(download, "image/jpeg"),
            provider_rank=provider_rank,
            raw=raw,
        )


class PixabayProvider(AssetSearchProvider):
    provider_id = "pixabay"
    download_host_allowlist = PROVIDER_HOST_ALLOWLISTS["pixabay"]

    async def search_raw(
        self,
        *,
        query: str,
        media_type: str,
        aspect: str = "auto",
        limit: int = 12,
        page: int = 1,
        language: str = "en",
        safe_search: bool = True,
    ) -> dict[str, Any]:
        if media_type not in {"image", "video"}:
            return {}
        if not self.api_key:
            raise AssetSearchProviderError("Pixabay API key is not configured")
        endpoint = PIXABAY_VIDEO_SEARCH_ENDPOINT if media_type == "video" else PIXABAY_IMAGE_SEARCH_ENDPOINT
        params = {
            "key": self.api_key,
            "q": query,
            "per_page": str(max(3, min(int(limit or 12), 200))),
            "page": str(max(1, int(page or 1))),
            "safesearch": "true" if safe_search else "false",
            "lang": language if re.fullmatch(r"[a-z]{2}", text(language).lower()) else "en",
        }
        orientation = aspect_orientation(aspect)
        if orientation in {"horizontal", "landscape"}:
            params["orientation"] = "horizontal"
        elif orientation == "portrait":
            params["orientation"] = "vertical"
        if media_type != "video":
            params["image_type"] = "photo"
        return await json_request(f"{endpoint}?{urllib.parse.urlencode(params)}", {"Accept": "application/json"})

    async def refresh_candidate(self, candidate: dict[str, Any]) -> dict[str, Any] | None:
        asset_id = text(candidate.get("provider_asset_id"))
        if not asset_id:
            return None
        media_type = text(candidate.get("media_type"), "image")
        endpoint = PIXABAY_VIDEO_SEARCH_ENDPOINT if media_type == "video" else PIXABAY_IMAGE_SEARCH_ENDPOINT
        params = {
            "key": self.api_key,
            "id": asset_id,
            "per_page": "3",
            "safesearch": "true",
        }
        response = await json_request(f"{endpoint}?{urllib.parse.urlencode(params)}", {"Accept": "application/json"})
        for item in self.response_items(response, text(candidate.get("media_type"), "image")):
            if text(item.get("id")) == asset_id:
                return self.normalize(item, text(candidate.get("media_type"), "image"), int_value(candidate.get("provider_rank"), 1))
        return None

    def response_items(self, response: dict[str, Any], media_type: str) -> list[dict[str, Any]]:
        del media_type
        return [item for item in response.get("hits") or [] if isinstance(item, dict)]

    def normalize(self, raw: dict[str, Any], media_type: str, provider_rank: int = 1) -> dict[str, Any]:
        creator_name = text(raw.get("user"))
        if media_type == "video":
            variants = raw.get("videos") if isinstance(raw.get("videos"), dict) else {}
            download_ordered = [variants.get(name) for name in ("large", "medium", "small", "tiny")]
            preview_ordered = [variants.get(name) for name in ("tiny", "small", "medium", "large")]
            download_choices = [item for item in download_ordered if isinstance(item, dict) and text(item.get("url"))]
            preview_choices = [item for item in preview_ordered if isinstance(item, dict) and text(item.get("url"))]
            chosen = download_choices[0] if download_choices else {}
            preview = preview_choices[0] if preview_choices else chosen
            thumb = text(raw.get("picture_id"))
            if thumb and not thumb.startswith("http"):
                thumb = f"https://i.vimeocdn.com/video/{urllib.parse.quote(thumb, safe='')}_640x360.jpg"
            return _candidate(
                provider=self.provider_id,
                provider_asset_id=text(raw.get("id")),
                media_type="video",
                title=text(raw.get("tags") or raw.get("pageURL") or raw.get("id")),
                description=text(raw.get("tags")),
                preview_url=text(preview.get("url")),
                thumbnail_url=thumb,
                download_url=text(chosen.get("url")),
                source_url=text(raw.get("pageURL")),
                creator={"name": creator_name, "url": text(raw.get("userImageURL"))},
                license_payload={
                    "name": "Pixabay Content License",
                    "url": "https://pixabay.com/service/license-summary/",
                    "requires_attribution": False,
                    "attribution_text": f"Video by {creator_name} on Pixabay".strip(),
                    "license_status": "confirmed",
                },
                width=chosen.get("width"),
                height=chosen.get("height"),
                duration_seconds=raw.get("duration"),
                mime_type=_mime_from_url(text(chosen.get("url")), "video/mp4"),
                tags=_safe_tags(raw.get("tags")),
                provider_rank=provider_rank,
                raw=raw,
            )
        download = text(raw.get("largeImageURL") or raw.get("fullHDURL") or raw.get("webformatURL") or raw.get("previewURL"))
        return _candidate(
            provider=self.provider_id,
            provider_asset_id=text(raw.get("id")),
            media_type="image",
            title=text(raw.get("tags") or raw.get("pageURL") or raw.get("id")),
            description=text(raw.get("tags")),
            preview_url=text(raw.get("webformatURL") or raw.get("previewURL") or download),
            thumbnail_url=text(raw.get("previewURL") or raw.get("webformatURL") or download),
            download_url=download,
            source_url=text(raw.get("pageURL")),
            creator={"name": creator_name, "url": text(raw.get("userImageURL"))},
            license_payload={
                "name": "Pixabay Content License",
                "url": "https://pixabay.com/service/license-summary/",
                "requires_attribution": False,
                "attribution_text": f"Image by {creator_name} on Pixabay".strip(),
                "license_status": "confirmed",
            },
            width=raw.get("imageWidth") or raw.get("webformatWidth"),
            height=raw.get("imageHeight") or raw.get("webformatHeight"),
            mime_type=_mime_from_url(download, "image/jpeg"),
            tags=_safe_tags(raw.get("tags")),
            provider_rank=provider_rank,
            raw=raw,
        )


class UnsplashProvider(AssetSearchProvider):
    provider_id = "unsplash"
    download_host_allowlist = PROVIDER_HOST_ALLOWLISTS["unsplash"]

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise AssetSearchProviderError("Unsplash access key is not configured")
        return {
            "Accept": "application/json",
            "Accept-Version": "v1",
            "Authorization": f"Client-ID {self.api_key}",
        }

    async def search_raw(
        self,
        *,
        query: str,
        media_type: str,
        aspect: str = "auto",
        limit: int = 12,
        page: int = 1,
        language: str = "en",
        safe_search: bool = True,
    ) -> dict[str, Any]:
        if media_type != "image":
            return {"results": []}
        params = {
            "query": query,
            "per_page": str(max(1, min(int(limit or 12), 30))),
            "page": str(max(1, int(page or 1))),
            "order_by": "relevant",
            "content_filter": "high" if safe_search else "low",
        }
        orientation = aspect_orientation(aspect)
        if orientation == "landscape":
            params["orientation"] = "landscape"
        elif orientation == "portrait":
            params["orientation"] = "portrait"
        elif orientation == "square":
            params["orientation"] = "squarish"
        if re.fullmatch(r"[a-z]{2}", text(language).lower()):
            params["lang"] = text(language).lower()
        return await json_request(f"{UNSPLASH_SEARCH_ENDPOINT}?{urllib.parse.urlencode(params)}", self._headers())

    async def refresh_candidate(self, candidate: dict[str, Any]) -> dict[str, Any] | None:
        asset_id = urllib.parse.quote(text(candidate.get("provider_asset_id")), safe="")
        if not asset_id:
            return None
        payload = await json_request(UNSPLASH_PHOTO_ENDPOINT.format(asset_id=asset_id), self._headers())
        if not payload:
            return None
        return self.normalize(payload, "image", int_value(candidate.get("provider_rank"), 1))

    async def prepare_import_candidate(self, candidate: dict[str, Any]) -> dict[str, Any]:
        raw = candidate.get("raw") if isinstance(candidate.get("raw"), dict) else {}
        raw_links = raw.get("links") if isinstance(raw.get("links"), dict) else {}
        tracking_url = text(candidate.get("download_tracking_url") or raw_links.get("download_location"))
        if not tracking_url:
            tracking_url = f"{UNSPLASH_PHOTO_ENDPOINT.format(asset_id=urllib.parse.quote(text(candidate.get('provider_asset_id')), safe=''))}/download"
        parsed = urllib.parse.urlparse(tracking_url)
        if parsed.scheme.lower() != "https" or parsed.hostname != "api.unsplash.com":
            raise AssetSearchSecurityError("Unsplash download tracking URL must be an api.unsplash.com HTTPS URL")
        payload = await json_request(tracking_url, self._headers())
        tracked_url = text(payload.get("url"))
        if not tracked_url:
            raise AssetSearchDownloadError("Unsplash download tracking did not return a URL")
        return {**candidate, "download_url": tracked_url, "download_tracking_url": tracking_url}

    def response_items(self, response: dict[str, Any], media_type: str) -> list[dict[str, Any]]:
        del media_type
        return [item for item in response.get("results") or [] if isinstance(item, dict)]

    def normalize(self, raw: dict[str, Any], media_type: str, provider_rank: int = 1) -> dict[str, Any]:
        del media_type
        urls = raw.get("urls") if isinstance(raw.get("urls"), dict) else {}
        links = raw.get("links") if isinstance(raw.get("links"), dict) else {}
        user = raw.get("user") if isinstance(raw.get("user"), dict) else {}
        creator_name = text(user.get("name") or user.get("username"))
        source_url = text(links.get("html"))
        download_url = text(urls.get("full") or urls.get("regular") or urls.get("raw"))
        return _candidate(
            provider=self.provider_id,
            provider_asset_id=text(raw.get("id")),
            media_type="image",
            title=text(raw.get("alt_description") or raw.get("description") or source_url or raw.get("id")),
            description=text(raw.get("description") or raw.get("alt_description")),
            preview_url=text(urls.get("regular") or urls.get("small") or download_url),
            thumbnail_url=text(urls.get("small") or urls.get("thumb") or download_url),
            download_url=download_url,
            source_url=source_url,
            creator={"name": creator_name, "url": text((user.get("links") or {}).get("html") if isinstance(user.get("links"), dict) else "")},
            license_payload={
                "name": "Unsplash License",
                "url": "https://unsplash.com/license",
                "requires_attribution": True,
                "attribution_text": f"Photo by {creator_name} on Unsplash".strip(),
                "license_status": "confirmed" if creator_name and source_url else "unconfirmed",
            },
            width=raw.get("width"),
            height=raw.get("height"),
            mime_type=_mime_from_url(download_url, "image/jpeg"),
            tags=[text(tag.get("title")) for tag in raw.get("tags") or [] if isinstance(tag, dict) and text(tag.get("title"))],
            provider_rank=provider_rank,
            raw=raw,
        ) | {"download_tracking_url": text(links.get("download_location"))}


class WikimediaProvider(AssetSearchProvider):
    provider_id = "wikimedia"
    download_host_allowlist = PROVIDER_HOST_ALLOWLISTS["wikimedia"]

    async def search_raw(
        self,
        *,
        query: str,
        media_type: str,
        aspect: str = "auto",
        limit: int = 12,
        page: int = 1,
        language: str = "en",
        safe_search: bool = True,
    ) -> dict[str, Any]:
        del aspect, page, language, safe_search
        search = text(query)
        if media_type == "image":
            search = f"{search} filetype:bitmap|drawing"
        elif media_type == "video":
            search = f"{search} filetype:video"
        elif media_type == "audio":
            search = f"{search} filetype:audio"
        params = {
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrnamespace": "6",
            "gsrsearch": search,
            "gsrlimit": str(max(1, min(int(limit or 12), 50))),
            "prop": "imageinfo",
            "iiprop": "url|mime|size|mediatype|extmetadata",
            "iiurlwidth": "640",
            "origin": "*",
        }
        return await json_request(f"{WIKIMEDIA_SEARCH_ENDPOINT}?{urllib.parse.urlencode(params)}", {"Accept": "application/json"})

    async def refresh_candidate(self, candidate: dict[str, Any]) -> dict[str, Any] | None:
        title = text((candidate.get("raw") or {}).get("title") if isinstance(candidate.get("raw"), dict) else "")
        if not title:
            return None
        params = {
            "action": "query",
            "format": "json",
            "titles": title,
            "prop": "imageinfo",
            "iiprop": "url|mime|size|mediatype|extmetadata",
            "iiurlwidth": "640",
            "origin": "*",
        }
        payload = await json_request(f"{WIKIMEDIA_SEARCH_ENDPOINT}?{urllib.parse.urlencode(params)}", {"Accept": "application/json"})
        items = self.response_items(payload, text(candidate.get("media_type"), "image"))
        if not items:
            return None
        return self.normalize(items[0], text(candidate.get("media_type"), "image"), int_value(candidate.get("provider_rank"), 1))

    def response_items(self, response: dict[str, Any], media_type: str) -> list[dict[str, Any]]:
        del media_type
        pages = ((response.get("query") or {}).get("pages") or {})
        items = [item for item in pages.values() if isinstance(item, dict)]
        items.sort(key=lambda item: int_value(item.get("index"), 9999))
        return items

    def _meta(self, imageinfo: dict[str, Any], key: str) -> str:
        ext = imageinfo.get("extmetadata") if isinstance(imageinfo.get("extmetadata"), dict) else {}
        item = ext.get(key) if isinstance(ext.get(key), dict) else {}
        return strip_html(item.get("value"))

    def _license(self, imageinfo: dict[str, Any]) -> dict[str, Any]:
        license_name = self._meta(imageinfo, "LicenseShortName") or self._meta(imageinfo, "License") or self._meta(imageinfo, "UsageTerms")
        license_url = self._meta(imageinfo, "LicenseUrl")
        artist = self._meta(imageinfo, "Artist")
        credit = self._meta(imageinfo, "Credit")
        attribution = self._meta(imageinfo, "Attribution") or credit or artist
        attribution_required_raw = self._meta(imageinfo, "AttributionRequired").lower()
        requires_attribution = attribution_required_raw in {"true", "required", "yes"} or "cc-by" in license_name.lower()
        confirmed = bool(license_name or license_url or attribution)
        return {
            "name": license_name or "Wikimedia Commons license",
            "url": license_url or "https://commons.wikimedia.org/wiki/Commons:Licensing",
            "requires_attribution": requires_attribution,
            "attribution_text": attribution,
            "license_status": "confirmed" if confirmed else "unconfirmed",
        }

    def normalize(self, raw: dict[str, Any], media_type: str, provider_rank: int = 1) -> dict[str, Any]:
        infos = raw.get("imageinfo") if isinstance(raw.get("imageinfo"), list) else []
        imageinfo = infos[0] if infos and isinstance(infos[0], dict) else {}
        mime_type = text(imageinfo.get("mime"))
        mediatype = text(imageinfo.get("mediatype")).upper()
        detected_media = "video" if mediatype == "VIDEO" or mime_type.startswith("video/") else "audio" if mediatype == "AUDIO" or mime_type.startswith("audio/") else "image"
        title = text(raw.get("title")).replace("File:", "", 1)
        width = imageinfo.get("width")
        height = imageinfo.get("height")
        source_url = text(imageinfo.get("descriptionurl") or imageinfo.get("descriptionshorturl"))
        download_url = text(imageinfo.get("url"))
        thumb_url = text(imageinfo.get("thumburl") or download_url)
        license_payload = self._license(imageinfo)
        import_supported = detected_media in {"image", "audio"}
        reason = "" if import_supported else "Wikimedia video import is not supported in P0"
        creator = self._meta(imageinfo, "Artist") or self._meta(imageinfo, "Credit")
        return _candidate(
            provider=self.provider_id,
            provider_asset_id=text(raw.get("pageid") or title),
            media_type=detected_media or media_type,
            title=title,
            description=self._meta(imageinfo, "ImageDescription"),
            preview_url=download_url if detected_media in {"video", "audio"} else thumb_url,
            thumbnail_url=thumb_url,
            download_url=download_url,
            source_url=source_url,
            creator={"name": creator, "url": source_url},
            license_payload=license_payload,
            width=width,
            height=height,
            duration_seconds=None,
            mime_type=mime_type or _mime_from_url(download_url, "audio/mpeg" if detected_media == "audio" else "image/jpeg" if detected_media == "image" else "video/mp4"),
            tags=[],
            provider_rank=provider_rank,
            import_supported=import_supported,
            import_unsupported_reason=reason,
            raw=raw,
        )


PROVIDER_CLASSES: dict[str, type[AssetSearchProvider]] = {
    "pexels": PexelsProvider,
    "pixabay": PixabayProvider,
    "unsplash": UnsplashProvider,
    "wikimedia": WikimediaProvider,
}


def provider_for(provider_id: str, api_key: str = "") -> AssetSearchProvider:
    cls = PROVIDER_CLASSES.get(text(provider_id).lower())
    if not cls:
        raise AssetSearchProviderError(f"Unsupported asset search provider: {provider_id}")
    return cls(api_key=api_key)


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        return None


def _download_candidate_file_sync(
    provider_id: str,
    url: str,
    target_path: Path,
    *,
    max_bytes: int,
    timeout: int = 60,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    current_url = validate_provider_url(provider_id, url)
    opener = urllib.request.build_opener(_NoRedirectHandler)
    redirect_chain: list[str] = []
    for _redirect in range(6):
        request = urllib.request.Request(current_url, headers=headers or {"User-Agent": "OpenCrew-Koubo-AssetSearch/1.0"})
        try:
            response = opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if exc.code not in {301, 302, 303, 307, 308}:
                raise AssetSearchDownloadError(f"download failed with HTTP {exc.code}") from exc
            location = exc.headers.get("Location") if exc.headers else ""
            if not location:
                raise AssetSearchDownloadError("download redirect did not include Location") from exc
            current_url = validate_provider_url(provider_id, urllib.parse.urljoin(current_url, location))
            redirect_chain.append(current_url)
            continue
        with response:
            final_url = validate_provider_url(provider_id, response.geturl() or current_url)
            length = int_value(response.headers.get("Content-Length"))
            if length and length > max_bytes:
                raise AssetSearchDownloadError("download exceeds size limit")
            target_path.parent.mkdir(parents=True, exist_ok=True)
            hasher = hashlib.sha256()
            written = 0
            content_type = text(response.headers.get("Content-Type")).split(";", 1)[0].lower()
            with tempfile.NamedTemporaryFile("wb", delete=False, dir=str(target_path.parent), prefix=f".{target_path.name}.", suffix=".part") as handle:
                temp_path = Path(handle.name)
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > max_bytes:
                        handle.close()
                        temp_path.unlink(missing_ok=True)
                        raise AssetSearchDownloadError("download exceeds size limit")
                    hasher.update(chunk)
                    handle.write(chunk)
            temp_path.replace(target_path)
            return {
                "ok": True,
                "content_sha256": hasher.hexdigest(),
                "bytes": written,
                "content_type": content_type,
                "final_url": final_url,
                "redirect_chain": redirect_chain,
            }
    raise AssetSearchDownloadError("download redirected too many times")


async def download_candidate_file(
    provider_id: str,
    url: str,
    target_path: Path,
    *,
    max_bytes: int,
    timeout: int = 60,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    return await asyncio.to_thread(
        _download_candidate_file_sync,
        provider_id,
        url,
        target_path,
        max_bytes=max_bytes,
        timeout=timeout,
        headers=headers,
    )
