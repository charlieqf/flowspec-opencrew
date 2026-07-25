from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from starlette.responses import JSONResponse
from starlette.types import Message, Scope


REPO_ROOT = Path(__file__).resolve().parents[3]
backend_path = str(REPO_ROOT / "backend")
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

from fastapi import FastAPI  # noqa: E402

from opcrew_backend.routes.auth import (  # noqa: E402
    AUTH_BODY_LIMIT_BYTES,
    AUTH_ROLE_ADMIN,
    AUTH_ROLE_USER,
    AuthBodyLimitMiddleware,
    auth_capabilities,
    auth_status,
    build_auth_middleware,
    build_auth_router,
    hash_password,
    make_token,
    setup_request_allowed,
    token_role,
    valid_token,
)


class FakeRequest:
    def __init__(self, host: str, headers: dict[str, str] | None = None, cookies: dict[str, str] | None = None) -> None:
        self.client = SimpleNamespace(host=host)
        self.headers = headers or {}
        self.cookies = cookies or {}


class FakeContext:
    def __init__(self) -> None:
        self.settings: dict[str, object] = {}

    def get_setting(self, key: str, default: object = None) -> object:
        return self.settings.get(key, default)

    def set_setting(self, key: str, value: object) -> None:
        self.settings[key] = value


class AsgiResponse:
    def __init__(self, status_code: int, body: bytes, headers: dict[str, str]) -> None:
        self.status_code = status_code
        self.body = body
        self.headers = headers

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8") or "{}")


def asgi_json_request(app: Any, method: str, path: str, body: dict[str, Any] | None = None, cookies: dict[str, str] | None = None) -> AsgiResponse:
    body_bytes = json.dumps(body or {}).encode("utf-8") if body is not None else b""
    headers: list[tuple[bytes, bytes]] = [(b"host", b"testserver")]
    if body is not None:
        headers.extend(
            [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body_bytes)).encode("ascii")),
            ]
        )
    if cookies:
        cookie_header = "; ".join(f"{key}={value}" for key, value in cookies.items())
        headers.append((b"cookie", cookie_header.encode("latin1")))
    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
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
        return {"type": "http.request", "body": body_bytes, "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    status = next(message for message in sent if message["type"] == "http.response.start")
    chunks = [message.get("body", b"") for message in sent if message["type"] == "http.response.body"]
    response_headers = {key.decode("latin1").lower(): value.decode("latin1") for key, value in status.get("headers", [])}
    return AsgiResponse(int(status["status"]), b"".join(chunks), response_headers)


class Phase0AuthHardeningContractTest(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("OPENCREW_SETUP_TOKEN", None)
        os.environ.pop("OPENCREW_AUTH_REQUIRED", None)
        os.environ.pop("OPENCREW_APP_PASSWORD", None)
        os.environ.pop("OPENCREW_APP_PASSWORD_HASH", None)
        os.environ.pop("OPENCREW_USER_PASSWORD", None)
        os.environ.pop("OPENCREW_USER_PASSWORD_HASH", None)

    def test_setup_allows_direct_loopback_without_forwarded_headers(self) -> None:
        self.assertTrue(setup_request_allowed(FakeRequest("127.0.0.1")))
        self.assertTrue(setup_request_allowed(FakeRequest("::1")))
        self.assertTrue(setup_request_allowed(FakeRequest("::ffff:127.0.0.1")))

    def test_setup_rejects_spoofed_forwarded_for_loopback(self) -> None:
        request = FakeRequest("192.168.0.55", {"x-forwarded-for": "127.0.0.1"})

        self.assertFalse(setup_request_allowed(request))

    def test_setup_rejects_reverse_proxy_loopback_without_token(self) -> None:
        request = FakeRequest("127.0.0.1", {"x-forwarded-for": "192.168.0.55"})

        self.assertFalse(setup_request_allowed(request))

    def test_setup_token_allows_non_loopback_request(self) -> None:
        os.environ["OPENCREW_SETUP_TOKEN"] = "install-token"
        request = FakeRequest("192.168.0.55", {"x-opencrew-setup-token": "install-token", "x-forwarded-for": "127.0.0.1"})

        self.assertTrue(setup_request_allowed(request))

    def test_auth_body_limit_rejects_chunked_body_without_content_length(self) -> None:
        async def app(scope: Scope, receive: Any, send: Any) -> None:
            while True:
                message = await receive()
                if not message.get("more_body"):
                    break
            await JSONResponse({"ok": True})(scope, receive, send)

        middleware = AuthBodyLimitMiddleware(app, max_bytes=AUTH_BODY_LIMIT_BYTES)
        chunks: list[Message] = [
            {"type": "http.request", "body": b"x" * 2048, "more_body": True},
            {"type": "http.request", "body": b"x" * 2048, "more_body": True},
            {"type": "http.request", "body": b"x", "more_body": False},
        ]
        sent: list[Message] = []

        async def receive() -> Message:
            return chunks.pop(0)

        async def send(message: Message) -> None:
            sent.append(message)

        scope: Scope = {"type": "http", "method": "POST", "path": "/api/auth/login", "headers": []}
        asyncio.run(middleware(scope, receive, send))

        status = next(message for message in sent if message["type"] == "http.response.start")
        self.assertEqual(status["status"], 413)

    def test_role_tokens_round_trip_and_capabilities_are_minimal(self) -> None:
        ctx = FakeContext()

        admin_token = make_token(ctx, AUTH_ROLE_ADMIN)
        user_token = make_token(ctx, AUTH_ROLE_USER)

        self.assertTrue(valid_token(ctx, admin_token))
        self.assertTrue(valid_token(ctx, user_token))
        self.assertEqual(token_role(ctx, admin_token), AUTH_ROLE_ADMIN)
        self.assertEqual(token_role(ctx, user_token), AUTH_ROLE_USER)
        self.assertEqual(auth_capabilities(AUTH_ROLE_ADMIN), {"can_manage_connection": True, "can_view_metering": True})
        self.assertEqual(auth_capabilities(AUTH_ROLE_USER), {"can_manage_connection": False, "can_view_metering": False})

    def test_auth_status_exposes_user_role_and_capabilities(self) -> None:
        ctx = FakeContext()
        ctx.set_setting("auth.password_hash", hash_password("admin-password"))
        token = make_token(ctx, AUTH_ROLE_USER)

        status = auth_status(ctx, FakeRequest("127.0.0.1", cookies={"opencrew_session": token}))  # type: ignore[arg-type]

        self.assertTrue(status["authenticated"])
        self.assertEqual(status["role"], AUTH_ROLE_USER)
        self.assertEqual(status["capabilities"], {"can_manage_connection": False, "can_view_metering": False})

    def test_login_issues_role_specific_session(self) -> None:
        ctx = FakeContext()
        ctx.set_setting("auth.password_hash", hash_password("admin-password"))
        ctx.set_setting("auth.user_password_hash", hash_password("user-password"))
        app = FastAPI()
        app.include_router(build_auth_router(ctx))  # type: ignore[arg-type]

        user_response = asgi_json_request(app, "POST", "/api/auth/login", {"password": "user-password"})
        self.assertEqual(user_response.status_code, 200)
        self.assertEqual(user_response.json()["role"], AUTH_ROLE_USER)
        self.assertEqual(user_response.json()["capabilities"], {"can_manage_connection": False, "can_view_metering": False})

        admin_response = asgi_json_request(app, "POST", "/api/auth/login", {"password": "admin-password"})
        self.assertEqual(admin_response.status_code, 200)
        self.assertEqual(admin_response.json()["role"], AUTH_ROLE_ADMIN)
        self.assertEqual(admin_response.json()["capabilities"], {"can_manage_connection": True, "can_view_metering": True})

    def test_configured_setup_reconfiguration_requires_admin_role_token(self) -> None:
        ctx = FakeContext()
        original_hash = hash_password("admin-password")
        ctx.set_setting("auth.password_hash", original_hash)
        app = FastAPI()
        app.include_router(build_auth_router(ctx))  # type: ignore[arg-type]

        no_token_response = asgi_json_request(app, "POST", "/api/auth/setup", {"password": "new-admin-password"})
        self.assertEqual(no_token_response.status_code, 403)
        self.assertEqual(no_token_response.json()["detail"], "Admin role required.")
        self.assertEqual(ctx.get_setting("auth.password_hash"), original_hash)

        user_response = asgi_json_request(app, "POST", "/api/auth/setup", {"password": "new-admin-password"}, {"opencrew_session": make_token(ctx, AUTH_ROLE_USER)})
        self.assertEqual(user_response.status_code, 403)
        self.assertEqual(user_response.json()["detail"], "Admin role required.")
        self.assertEqual(ctx.get_setting("auth.password_hash"), original_hash)

        admin_response = asgi_json_request(app, "POST", "/api/auth/setup", {"password": "new-admin-password"}, {"opencrew_session": make_token(ctx, AUTH_ROLE_ADMIN)})
        self.assertEqual(admin_response.status_code, 200)
        self.assertNotEqual(ctx.get_setting("auth.password_hash"), original_hash)

    def test_user_role_cannot_access_admin_only_api_prefixes(self) -> None:
        ctx = FakeContext()
        ctx.set_setting("auth.password_hash", hash_password("admin-password"))
        app = FastAPI()
        app.middleware("http")(build_auth_middleware(ctx))  # type: ignore[arg-type]

        @app.get("/api/setup/summary")
        def setup_summary() -> dict[str, bool]:
            return {"ok": True}

        @app.get("/api/model-config/providers")
        def model_config() -> dict[str, bool]:
            return {"ok": True}

        @app.get("/api/local-metering/summary")
        def local_metering() -> dict[str, bool]:
            return {"ok": True}

        user_cookies = {"opencrew_session": make_token(ctx, AUTH_ROLE_USER)}
        self.assertEqual(asgi_json_request(app, "GET", "/api/setup/summary", cookies=user_cookies).status_code, 403)
        self.assertEqual(asgi_json_request(app, "GET", "/api/model-config/providers", cookies=user_cookies).status_code, 403)
        self.assertEqual(asgi_json_request(app, "GET", "/api/local-metering/summary", cookies=user_cookies).status_code, 403)

        admin_cookies = {"opencrew_session": make_token(ctx, AUTH_ROLE_ADMIN)}
        self.assertEqual(asgi_json_request(app, "GET", "/api/setup/summary", cookies=admin_cookies).status_code, 200)
        self.assertEqual(asgi_json_request(app, "GET", "/api/model-config/providers", cookies=admin_cookies).status_code, 200)
        self.assertEqual(asgi_json_request(app, "GET", "/api/local-metering/summary", cookies=admin_cookies).status_code, 200)

    def test_auth_401_does_not_emit_browser_basic_auth_challenge(self) -> None:
        ctx = FakeContext()
        ctx.set_setting("auth.password_hash", hash_password("admin-password"))
        app = FastAPI()
        app.middleware("http")(build_auth_middleware(ctx))  # type: ignore[arg-type]

        @app.get("/api/setup/summary")
        def setup_summary() -> dict[str, bool]:
            return {"ok": True}

        response = asgi_json_request(app, "GET", "/api/setup/summary")

        self.assertEqual(response.status_code, 401)
        self.assertNotIn("www-authenticate", response.headers)


if __name__ == "__main__":
    unittest.main()
