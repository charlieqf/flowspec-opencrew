from __future__ import annotations

import base64
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
for path in (REPO_ROOT / "backend", REPO_ROOT / "ModelConfig" / "backend"):
    value = str(path)
    if value not in sys.path:
        sys.path.insert(0, value)

from opcrew_backend.routes.media_model_config import audio_url_bytes  # noqa: E402
from opcrew_backend.services import safe_download  # noqa: E402


class FakeResponse:
    def __init__(self, status: int, body: bytes = b"", headers: dict[str, str] | None = None) -> None:
        self.status = status
        self._body = body
        self._offset = 0
        self.headers = headers or {}
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        if self._offset >= len(self._body):
            return b""
        if size is None or size < 0:
            size = len(self._body) - self._offset
        end = min(len(self._body), self._offset + size)
        chunk = self._body[self._offset:end]
        self._offset = end
        return chunk

    def close(self) -> None:
        self.closed = True


class FakeOpener:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def open(self, request: Any, timeout: int | float) -> FakeResponse:
        del timeout
        self.calls.append(str(request.full_url))
        if not self.responses:
            raise AssertionError(f"Unexpected download request: {request.full_url}")
        return self.responses.pop(0)


class Phase0SafeDownloadContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.old_getaddrinfo = safe_download.socket.getaddrinfo
        self.old_build_opener = safe_download._build_opener

    def tearDown(self) -> None:
        safe_download.socket.getaddrinfo = self.old_getaddrinfo
        safe_download._build_opener = self.old_build_opener

    def install_fake_dns(self, mapping: dict[str, str]) -> None:
        def fake_getaddrinfo(host: str, port: int, *args: Any, **kwargs: Any) -> list[Any]:
            del args, kwargs
            ip = mapping.get(host, "8.8.8.8")
            family = socket.AF_INET6 if ":" in ip else socket.AF_INET
            return [(family, socket.SOCK_STREAM, 6, "", (ip, port))]

        safe_download.socket.getaddrinfo = fake_getaddrinfo

    def install_fake_opener(self, opener: FakeOpener) -> None:
        safe_download._build_opener = lambda _proxy_policy="direct": opener

    def test_rejects_http_and_redacts_query(self) -> None:
        with self.assertRaises(safe_download.SafeDownloadError) as raised:
            safe_download.safe_download_bytes(
                "http://127.0.0.1/audio.wav?sig=secret-token",
                allowed_content_types=("audio/*",),
                max_bytes=1024,
                timeout=1,
            )

        message = str(raised.exception)
        self.assertIn("Only https", message)
        self.assertNotIn("secret-token", message)

    def test_rejects_private_resolved_ip(self) -> None:
        self.install_fake_dns({"artifact.example": "10.0.0.8"})

        with self.assertRaisesRegex(safe_download.SafeDownloadError, "disallowed private IP"):
            safe_download.safe_download_bytes(
                "https://artifact.example/audio.wav",
                allowed_content_types=("audio/*",),
                max_bytes=1024,
                timeout=1,
            )

    def test_allows_tun_fake_ip_for_domain_hosts(self) -> None:
        self.install_fake_dns({"resource2.heygen.ai": "198.18.0.127"})
        opener = FakeOpener([FakeResponse(200, b"heygen-audio", {"Content-Type": "audio/mpeg"})])
        self.install_fake_opener(opener)

        result = safe_download.safe_download_bytes(
            "https://resource2.heygen.ai/audio.mp3",
            allowed_content_types=("audio/*",),
            max_bytes=1024,
            timeout=1,
        )

        self.assertEqual(result.data, b"heygen-audio")
        self.assertEqual(opener.calls, ["https://resource2.heygen.ai/audio.mp3"])

    def test_rejects_tun_fake_ip_literal_urls(self) -> None:
        self.install_fake_dns({"198.18.0.127": "198.18.0.127"})

        with self.assertRaisesRegex(safe_download.SafeDownloadError, "disallowed private IP"):
            safe_download.safe_download_bytes(
                "https://198.18.0.127/audio.mp3",
                allowed_content_types=("audio/*",),
                max_bytes=1024,
                timeout=1,
            )

    def test_rejects_redirect_to_private_resolved_ip(self) -> None:
        self.install_fake_dns({"artifact.example": "8.8.8.8", "internal.example": "169.254.169.254"})
        opener = FakeOpener([FakeResponse(302, headers={"Location": "https://internal.example/audio.wav"})])
        self.install_fake_opener(opener)

        with self.assertRaisesRegex(safe_download.SafeDownloadError, "disallowed link_local IP"):
            safe_download.safe_download_bytes(
                "https://artifact.example/audio.wav",
                allowed_content_types=("audio/*",),
                max_bytes=1024,
                timeout=1,
            )
        self.assertEqual(opener.calls, ["https://artifact.example/audio.wav"])

    def test_rejects_unexpected_content_type(self) -> None:
        self.install_fake_dns({"artifact.example": "8.8.8.8"})
        opener = FakeOpener([FakeResponse(200, b"<html></html>", {"Content-Type": "text/html"})])
        self.install_fake_opener(opener)

        with self.assertRaisesRegex(safe_download.SafeDownloadError, "Content-Type"):
            safe_download.safe_download_bytes(
                "https://artifact.example/audio.wav",
                allowed_content_types=("audio/*",),
                max_bytes=1024,
                timeout=1,
            )

    def test_rejects_download_larger_than_max_bytes(self) -> None:
        self.install_fake_dns({"artifact.example": "8.8.8.8"})
        opener = FakeOpener([FakeResponse(200, b"abcdef", {"Content-Type": "audio/wav"})])
        self.install_fake_opener(opener)

        with self.assertRaisesRegex(safe_download.SafeDownloadError, "exceeded max download size"):
            safe_download.safe_download_bytes(
                "https://artifact.example/audio.wav",
                allowed_content_types=("audio/*",),
                max_bytes=5,
                timeout=1,
            )

    def test_rejects_content_length_larger_than_max_bytes(self) -> None:
        self.install_fake_dns({"artifact.example": "8.8.8.8"})
        opener = FakeOpener([FakeResponse(200, b"", {"Content-Type": "audio/wav", "Content-Length": "6"})])
        self.install_fake_opener(opener)

        with self.assertRaisesRegex(safe_download.SafeDownloadError, "exceeded max download size"):
            safe_download.safe_download_bytes(
                "https://artifact.example/audio.wav",
                allowed_content_types=("audio/*",),
                max_bytes=5,
                timeout=1,
            )

    def test_audio_url_bytes_keeps_data_url_support(self) -> None:
        encoded = base64.b64encode(b"wav-bytes").decode("ascii")

        data, mime = audio_url_bytes(f"data:audio/wav;base64,{encoded}")

        self.assertEqual(data, b"wav-bytes")
        self.assertEqual(mime, "audio/wav")

    def test_audio_url_bytes_uses_safe_download_for_remote_urls(self) -> None:
        self.install_fake_dns({"artifact.example": "8.8.8.8"})
        opener = FakeOpener([FakeResponse(200, b"mp3-bytes", {"Content-Type": "audio/mpeg"})])
        self.install_fake_opener(opener)

        data, mime = audio_url_bytes("https://artifact.example/audio.mp3?sig=secret")

        self.assertEqual(data, b"mp3-bytes")
        self.assertEqual(mime, "audio/mpeg")
        self.assertEqual(opener.calls, ["https://artifact.example/audio.mp3?sig=secret"])

    def test_safe_download_to_path_streams_to_file(self) -> None:
        self.install_fake_dns({"artifact.example": "8.8.8.8"})
        opener = FakeOpener([FakeResponse(200, b"video-bytes", {"Content-Type": "video/mp4"})])
        self.install_fake_opener(opener)

        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "out.mp4"
            result = safe_download.safe_download_to_path(
                "https://artifact.example/video.mp4",
                output_path,
                allowed_content_types=("video/*",),
                max_bytes=1024,
                timeout=1,
            )

            self.assertEqual(output_path.read_bytes(), b"video-bytes")
            self.assertEqual(result.bytes_read, len(b"video-bytes"))
            self.assertIsNone(result.data)


if __name__ == "__main__":
    unittest.main()
