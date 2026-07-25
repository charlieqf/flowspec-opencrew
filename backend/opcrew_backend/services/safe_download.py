from __future__ import annotations

import ipaddress
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from .provider_resolver import mihomo_proxy_url


class SafeDownloadError(RuntimeError):
    pass


TUN_FAKE_IP_V4 = ipaddress.ip_network("198.18.0.0/15")


@dataclass(frozen=True)
class SafeDownloadResult:
    final_url: str
    content_type: str
    bytes_read: int
    data: bytes | None = None


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _redacted_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(str(value or ""))
    if not parsed.scheme or not parsed.netloc:
        return "<invalid-url>"
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", "", ""))


def _content_type_base(value: str) -> str:
    return str(value or "application/octet-stream").split(";", 1)[0].strip().lower() or "application/octet-stream"


def _content_type_allowed(value: str, allowed_content_types: Iterable[str]) -> bool:
    mime = _content_type_base(value)
    allowed = [str(item or "").strip().lower() for item in allowed_content_types if str(item or "").strip()]
    if not allowed:
        return True
    for item in allowed:
        if item.endswith("/*") and mime.startswith(item[:-1]):
            return True
        if item.endswith("/") and mime.startswith(item):
            return True
        if mime == item:
            return True
    return False


def _unsafe_ip_reason(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    if ip.is_loopback:
        return "loopback"
    if ip.is_link_local:
        return "link_local"
    if ip.is_private:
        return "private"
    if ip.is_unspecified:
        return "unspecified"
    if ip.is_multicast:
        return "multicast"
    if ip.is_reserved:
        return "reserved"
    return None


def _host_is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _is_tun_fake_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return isinstance(ip, ipaddress.IPv4Address) and ip in TUN_FAKE_IP_V4


def _validate_https_public_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(str(url or "").strip())
    if parsed.scheme.lower() != "https":
        raise SafeDownloadError(f"Only https provider artifact URLs are allowed: {_redacted_url(url)}")
    if not parsed.hostname:
        raise SafeDownloadError(f"Provider artifact URL is missing a host: {_redacted_url(url)}")
    if parsed.username or parsed.password:
        raise SafeDownloadError(f"Provider artifact URL credentials are not allowed: {_redacted_url(url)}")
    host = parsed.hostname.strip()
    try:
        port = parsed.port or 443
    except ValueError as exc:
        raise SafeDownloadError(f"Provider artifact URL has an invalid port: {_redacted_url(url)}") from exc
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise SafeDownloadError(f"Cannot resolve provider artifact host {host}: {exc}") from exc
    if not addresses:
        raise SafeDownloadError(f"Cannot resolve provider artifact host {host}")
    checked: set[str] = set()
    for item in addresses:
        raw_ip = item[4][0]
        if raw_ip in checked:
            continue
        checked.add(raw_ip)
        try:
            ip = ipaddress.ip_address(raw_ip)
        except ValueError as exc:
            raise SafeDownloadError(f"Resolved invalid provider artifact IP for {host}: {raw_ip}") from exc
        reason = _unsafe_ip_reason(ip)
        if reason is not None:
            if _is_tun_fake_ip(ip) and not _host_is_ip_literal(host):
                continue
            raise SafeDownloadError(f"Provider artifact host {host} resolved to disallowed {reason} IP {ip}")
    return urllib.parse.urlunsplit(parsed)


def _build_opener(proxy_policy: str = "direct") -> urllib.request.OpenerDirector:
    handlers: list[urllib.request.BaseHandler] = [_NoRedirectHandler()]
    if proxy_policy == "mihomo":
        proxy_url = mihomo_proxy_url()
        if proxy_url:
            handlers.append(urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}))
        else:
            handlers.append(urllib.request.ProxyHandler({}))
    elif proxy_policy == "direct":
        handlers.append(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(*handlers)


def _response_status(response: object) -> int:
    value = getattr(response, "status", None)
    if value is None and hasattr(response, "getcode"):
        value = response.getcode()  # type: ignore[attr-defined]
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _header_value(response: object, name: str, default: str = "") -> str:
    headers = getattr(response, "headers", None)
    if headers is None:
        return default
    getter = getattr(headers, "get", None)
    if callable(getter):
        return str(getter(name, default) or default)
    return default


def _read_limited(response: object, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    read = getattr(response, "read", None)
    if not callable(read):
        raise SafeDownloadError("Provider artifact response is not readable")
    while True:
        chunk = read(min(64 * 1024, max_bytes + 1 - total))
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise SafeDownloadError(f"Provider artifact exceeded max download size of {max_bytes} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


def _write_limited(response: object, output_path: Path, max_bytes: int) -> int:
    total = 0
    read = getattr(response, "read", None)
    if not callable(read):
        raise SafeDownloadError("Provider artifact response is not readable")
    with output_path.open("wb") as handle:
        while True:
            chunk = read(min(64 * 1024, max_bytes + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise SafeDownloadError(f"Provider artifact exceeded max download size of {max_bytes} bytes")
            handle.write(chunk)
    return total


def _validate_response_metadata(response: object, allowed_content_types: Iterable[str], max_bytes: int) -> str:
    content_type = _content_type_base(_header_value(response, "Content-Type", "application/octet-stream"))
    if not _content_type_allowed(content_type, allowed_content_types):
        raise SafeDownloadError(f"Provider artifact Content-Type is not allowed: {content_type}")
    content_length = _header_value(response, "Content-Length", "")
    if content_length:
        try:
            if int(content_length) > max_bytes:
                raise SafeDownloadError(f"Provider artifact exceeded max download size of {max_bytes} bytes")
        except ValueError:
            pass
    return content_type


def _open_checked(url: str, *, headers: Mapping[str, str] | None, timeout: int | float, proxy_policy: str, max_redirects: int) -> tuple[object, str]:
    current_url = str(url or "").strip()
    opener = _build_opener(proxy_policy)
    for _redirect_index in range(max_redirects + 1):
        current_url = _validate_https_public_url(current_url)
        request = urllib.request.Request(current_url, headers=dict(headers or {}), method="GET")
        try:
            response = opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            if status in {301, 302, 303, 307, 308}:
                location = str(exc.headers.get("Location") or "").strip()
                if not location:
                    raise SafeDownloadError(f"Provider artifact redirect missing Location: {_redacted_url(current_url)}") from exc
                current_url = urllib.parse.urljoin(current_url, location)
                continue
            raise SafeDownloadError(f"Provider artifact download failed with HTTP {status}: {_redacted_url(current_url)}") from exc
        except urllib.error.URLError as exc:
            raise SafeDownloadError(f"Provider artifact download failed: {_redacted_url(current_url)}") from exc
        status = _response_status(response)
        if status in {301, 302, 303, 307, 308}:
            location = _header_value(response, "Location", "").strip()
            close = getattr(response, "close", None)
            if callable(close):
                close()
            if not location:
                raise SafeDownloadError(f"Provider artifact redirect missing Location: {_redacted_url(current_url)}")
            current_url = urllib.parse.urljoin(current_url, location)
            continue
        if status and status >= 400:
            raise SafeDownloadError(f"Provider artifact download failed with HTTP {status}: {_redacted_url(current_url)}")
        return response, current_url
    raise SafeDownloadError(f"Provider artifact exceeded redirect limit of {max_redirects}: {_redacted_url(current_url)}")


def safe_download_bytes(
    url: str,
    *,
    allowed_content_types: Iterable[str],
    max_bytes: int,
    timeout: int | float,
    proxy_policy: str = "direct",
    headers: Mapping[str, str] | None = None,
    max_redirects: int = 3,
) -> SafeDownloadResult:
    if max_bytes <= 0:
        raise SafeDownloadError("max_bytes must be positive")
    response, final_url = _open_checked(url, headers=headers, timeout=timeout, proxy_policy=proxy_policy, max_redirects=max_redirects)
    try:
        content_type = _validate_response_metadata(response, allowed_content_types, max_bytes)
        data = _read_limited(response, max_bytes)
        return SafeDownloadResult(final_url=final_url, content_type=content_type, bytes_read=len(data), data=data)
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            close()


def safe_download_to_path(
    url: str,
    output_path: Path,
    *,
    allowed_content_types: Iterable[str],
    max_bytes: int,
    timeout: int | float,
    proxy_policy: str = "direct",
    headers: Mapping[str, str] | None = None,
    max_redirects: int = 3,
) -> SafeDownloadResult:
    if max_bytes <= 0:
        raise SafeDownloadError("max_bytes must be positive")
    response, final_url = _open_checked(url, headers=headers, timeout=timeout, proxy_policy=proxy_policy, max_redirects=max_redirects)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_name(f"{output_path.name}.part")
    try:
        content_type = _validate_response_metadata(response, allowed_content_types, max_bytes)
        bytes_read = _write_limited(response, temp_path, max_bytes)
        temp_path.replace(output_path)
        return SafeDownloadResult(final_url=final_url, content_type=content_type, bytes_read=bytes_read)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            close()


def decode_data_url_bytes(url: str, *, allowed_content_types: Iterable[str], max_bytes: int) -> SafeDownloadResult:
    value = str(url or "").strip()
    if not value.startswith("data:"):
        raise SafeDownloadError("Not a data URL")
    try:
        header, encoded = value.split(",", 1)
    except ValueError as exc:
        raise SafeDownloadError("Invalid data URL") from exc
    content_type = _content_type_base(header[5:].split(";", 1)[0] or "application/octet-stream")
    if not _content_type_allowed(content_type, allowed_content_types):
        raise SafeDownloadError(f"Provider artifact Content-Type is not allowed: {content_type}")
    import base64

    data = base64.b64decode(encoded)
    if len(data) > max_bytes:
        raise SafeDownloadError(f"Provider artifact exceeded max download size of {max_bytes} bytes")
    return SafeDownloadResult(final_url="data:", content_type=content_type, bytes_read=len(data), data=data)
