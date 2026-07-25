from __future__ import annotations

import asyncio
import gzip
import json
import sys
import unittest
from pathlib import Path
from typing import Any

from starlette.types import Message, Scope


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_PATH = REPO_ROOT / "backend"
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))

from opcrew_backend.app import JsonGZipMiddleware  # noqa: E402


class AsgiResponse:
    def __init__(self, status_code: int, body: bytes, headers: dict[str, str]) -> None:
        self.status_code = status_code
        self.body = body
        self.headers = headers


def asgi_request(app: Any, accept_encoding: str = "gzip") -> AsgiResponse:
    headers = [(b"accept-encoding", accept_encoding.encode("latin1"))] if accept_encoding else []
    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/test",
        "raw_path": b"/test",
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "root_path": "",
    }
    sent: list[Message] = []
    received = False

    async def receive() -> Message:
        nonlocal received
        if received:
            return {"type": "http.disconnect"}
        received = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    start = next(message for message in sent if message["type"] == "http.response.start")
    body = b"".join(message.get("body", b"") for message in sent if message["type"] == "http.response.body")
    response_headers = {key.decode("latin1").lower(): value.decode("latin1") for key, value in start.get("headers", [])}
    return AsgiResponse(int(start["status"]), body, response_headers)


def response_app(content_type: str, body: bytes) -> Any:
    async def app(_scope: Scope, _receive: Any, send: Any) -> None:
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", content_type.encode("latin1")),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        })
        await send({"type": "http.response.body", "body": body, "more_body": False})

    return app


class JsonGZipMiddlewareContractTest(unittest.TestCase):
    def test_compresses_large_json_response(self) -> None:
        body = json.dumps({"items": ["x" * 128 for _ in range(12)]}).encode("utf-8")
        response = asgi_request(JsonGZipMiddleware(response_app("application/json", body), minimum_size=64))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("content-encoding"), "gzip")
        self.assertEqual(gzip.decompress(response.body), body)

    def test_does_not_compress_event_stream(self) -> None:
        body = b"data: hello\n\n"
        response = asgi_request(JsonGZipMiddleware(response_app("text/event-stream", body), minimum_size=1))

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("content-encoding", response.headers)
        self.assertEqual(response.body, body)

    def test_does_not_compress_zip_response(self) -> None:
        body = b"PK\x03\x04" + (b"x" * 512)
        response = asgi_request(JsonGZipMiddleware(response_app("application/zip", body), minimum_size=1))

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("content-encoding", response.headers)
        self.assertEqual(response.body, body)


if __name__ == "__main__":
    unittest.main()
