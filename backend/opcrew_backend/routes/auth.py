from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import time
from typing import Any, Callable

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from opcrew_backend.context import AppContext


SESSION_COOKIE = "opencrew_session"
SESSION_TTL_SECONDS = 7 * 24 * 60 * 60
AUTH_BODY_LIMIT_BYTES = 4096
AUTH_ROLE_ADMIN = "admin"
AUTH_ROLE_USER = "user"
AUTH_ROLES = {AUTH_ROLE_ADMIN, AUTH_ROLE_USER}
ADMIN_ONLY_PATH_PREFIXES = ("/api/setup/", "/api/model-config/", "/api/local-metering/")


class AuthBodyLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int = AUTH_BODY_LIMIT_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") not in {"/api/auth/login", "/api/auth/setup"}:
            await self.app(scope, receive, send)
            return
        headers = {key.decode("latin1").lower(): value.decode("latin1") for key, value in scope.get("headers", [])}
        try:
            content_length = int(headers.get("content-length") or "0")
        except ValueError:
            content_length = 0
        if content_length > self.max_bytes:
            await JSONResponse(status_code=413, content={"detail": "Authentication request body is too large."})(scope, receive, send)
            return
        body = bytearray()
        more_body = True
        while more_body:
            message = await receive()
            if message["type"] == "http.request":
                body.extend(message.get("body", b""))
                if len(body) > self.max_bytes:
                    await JSONResponse(status_code=413, content={"detail": "Authentication request body is too large."})(scope, receive, send)
                    return
                more_body = bool(message.get("more_body"))
        replayed = False

        async def replay_receive() -> Message:
            nonlocal replayed
            if replayed:
                return {"type": "http.request", "body": b"", "more_body": False}
            replayed = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay_receive, send)


class PasswordPayload(BaseModel):
    password: str = Field(..., min_length=1, max_length=256)


def auth_required() -> bool:
    return os.environ.get("OPENCREW_AUTH_REQUIRED", "1").strip().lower() not in {"0", "false", "no", "off"}


def debug_console_enabled() -> bool:
    return os.environ.get("OPENCREW_DEBUG_CONSOLE", "0").strip().lower() in {"1", "true", "yes", "on"}


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), 240_000)
    return f"pbkdf2_sha256${salt}${base64.urlsafe_b64encode(digest).decode('ascii')}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, salt, digest_b64 = encoded.split("$", 2)
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    expected = base64.urlsafe_b64decode(digest_b64.encode("ascii"))
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), 240_000)
    return hmac.compare_digest(actual, expected)


def configured_admin_password_hash(ctx: AppContext) -> str:
    env_hash = os.environ.get("OPENCREW_APP_PASSWORD_HASH", "").strip()
    if env_hash:
        return env_hash
    env_password = os.environ.get("OPENCREW_APP_PASSWORD", "").strip()
    if env_password:
        cached = str(ctx.get_setting("auth.env_password_hash", "") or "")
        marker = hashlib.sha256(env_password.encode("utf-8")).hexdigest()
        cached_marker = str(ctx.get_setting("auth.env_password_marker", "") or "")
        if cached and cached_marker == marker:
            return cached
        encoded = hash_password(env_password)
        ctx.set_setting("auth.env_password_hash", encoded)
        ctx.set_setting("auth.env_password_marker", marker)
        return encoded
    return str(ctx.get_setting("auth.password_hash", "") or "")


def configured_password_hash(ctx: AppContext) -> str:
    return configured_admin_password_hash(ctx)


def configured_user_password_hash(ctx: AppContext) -> str:
    env_hash = os.environ.get("OPENCREW_USER_PASSWORD_HASH", "").strip()
    if env_hash:
        return env_hash
    env_password = os.environ.get("OPENCREW_USER_PASSWORD", "").strip()
    if env_password:
        cached = str(ctx.get_setting("auth.user_env_password_hash", "") or "")
        marker = hashlib.sha256(env_password.encode("utf-8")).hexdigest()
        cached_marker = str(ctx.get_setting("auth.user_env_password_marker", "") or "")
        if cached and cached_marker == marker:
            return cached
        encoded = hash_password(env_password)
        ctx.set_setting("auth.user_env_password_hash", encoded)
        ctx.set_setting("auth.user_env_password_marker", marker)
        return encoded
    return str(ctx.get_setting("auth.user_password_hash", "") or "")


def normalized_password(value: str) -> str:
    return str(value or "").strip()


def auth_configured(ctx: AppContext) -> bool:
    return bool(configured_password_hash(ctx))


def session_secret(ctx: AppContext) -> str:
    current = str(ctx.get_setting("auth.session_secret", "") or "")
    if current:
        return current
    current = secrets.token_urlsafe(32)
    ctx.set_setting("auth.session_secret", current)
    return current


def make_token(ctx: AppContext, role: str = AUTH_ROLE_ADMIN) -> str:
    if role not in AUTH_ROLES:
        role = AUTH_ROLE_ADMIN
    now = int(time.time())
    payload = {"iat": now, "exp": now + SESSION_TTL_SECONDS, "role": role}
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")).decode("ascii").rstrip("=")
    sig = hmac.new(session_secret(ctx).encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    signature = base64.urlsafe_b64encode(sig).decode("ascii").rstrip("=")
    return f"{body}.{signature}"


def parse_token(ctx: AppContext, token: str) -> dict[str, Any]:
    if "." not in token:
        return {"valid": False, "role": "", "payload": {}}
    body, signature = token.split(".", 1)
    expected = hmac.new(session_secret(ctx).encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    try:
        actual = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
    except Exception:
        return {"valid": False, "role": "", "payload": {}}
    if not hmac.compare_digest(actual, expected):
        return {"valid": False, "role": "", "payload": {}}
    try:
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode("utf-8"))
    except Exception:
        return {"valid": False, "role": "", "payload": {}}
    if int(payload.get("exp") or 0) < int(time.time()):
        return {"valid": False, "role": "", "payload": {}}
    role = str(payload.get("role") or AUTH_ROLE_ADMIN).strip() or AUTH_ROLE_ADMIN
    if role not in AUTH_ROLES:
        return {"valid": False, "role": "", "payload": {}}
    return {"valid": True, "role": role, "payload": payload}


def valid_token(ctx: AppContext, token: str) -> bool:
    return bool(parse_token(ctx, token)["valid"])


def token_role(ctx: AppContext, token: str) -> str:
    parsed = parse_token(ctx, token)
    return str(parsed["role"]) if parsed["valid"] else ""


def auth_capabilities(role: str) -> dict[str, bool]:
    is_admin = role == AUTH_ROLE_ADMIN
    return {
        "can_manage_connection": is_admin,
        "can_view_metering": is_admin,
    }


def auth_status(ctx: AppContext, request: Request) -> dict[str, Any]:
    enabled = auth_required()
    configured = auth_configured(ctx) if enabled else False
    parsed = parse_token(ctx, request.cookies.get(SESSION_COOKIE, "")) if enabled and configured else {"valid": False, "role": "", "payload": {}}
    authenticated = not enabled or (configured and bool(parsed["valid"]))
    role = AUTH_ROLE_ADMIN if not enabled else (str(parsed["role"]) if authenticated else "")
    return {
        "enabled": enabled,
        "configured": configured,
        "authenticated": authenticated,
        "role": role,
        "capabilities": auth_capabilities(role),
        "debug_console_enabled": debug_console_enabled(),
    }


def set_session_cookie(ctx: AppContext, response: Response, role: str = AUTH_ROLE_ADMIN) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        make_token(ctx, role),
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=SESSION_TTL_SECONDS,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


def request_direct_host(request: Request) -> str:
    return request.client.host if request.client else ""


def is_loopback_host(host: str) -> bool:
    value = str(host or "").strip()
    if value == "localhost":
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def has_forwarded_headers(request: Request) -> bool:
    return any(name in request.headers for name in ("x-forwarded-for", "x-real-ip", "forwarded"))


def setup_token_valid(request: Request) -> bool:
    expected = os.environ.get("OPENCREW_SETUP_TOKEN", "").strip()
    if not expected:
        return False
    supplied = str(request.headers.get("x-opencrew-setup-token") or "").strip()
    return hmac.compare_digest(supplied, expected)


def setup_request_allowed(request: Request) -> bool:
    if setup_token_valid(request):
        return True
    return is_loopback_host(request_direct_host(request)) and not has_forwarded_headers(request)


def build_auth_router(ctx: AppContext) -> APIRouter:
    router = APIRouter(prefix="/api/auth", tags=["auth"])

    @router.get("/status")
    def get_status(request: Request) -> dict[str, Any]:
        return auth_status(ctx, request)

    @router.post("/setup")
    def setup(payload: PasswordPayload, request: Request) -> JSONResponse:
        password = normalized_password(payload.password)
        if len(password) < 8:
            return JSONResponse(status_code=400, content={"detail": "Password must be at least 8 characters."})
        if auth_configured(ctx):
            parsed = parse_token(ctx, request.cookies.get(SESSION_COOKIE, ""))
            if not parsed["valid"]:
                return JSONResponse(status_code=403, content={"detail": "Admin role required."})
            if parsed["role"] != AUTH_ROLE_ADMIN:
                return JSONResponse(status_code=403, content={"detail": "Admin role required."})
        if not auth_configured(ctx) and not setup_request_allowed(request):
            return JSONResponse(status_code=403, content={"detail": "Initial setup requires local access or setup token."})
        ctx.set_setting("auth.password_hash", hash_password(password))
        response = JSONResponse(content={"ok": True, "status": "configured"})
        set_session_cookie(ctx, response, AUTH_ROLE_ADMIN)
        return response

    @router.post("/login")
    def login(payload: PasswordPayload) -> JSONResponse:
        password = normalized_password(payload.password)
        admin_encoded = configured_admin_password_hash(ctx)
        user_encoded = configured_user_password_hash(ctx)
        role = ""
        if admin_encoded and verify_password(password, admin_encoded):
            role = AUTH_ROLE_ADMIN
        elif user_encoded and verify_password(password, user_encoded):
            role = AUTH_ROLE_USER
        if not role:
            return JSONResponse(status_code=401, content={"detail": "Invalid password."})
        response = JSONResponse(content={"ok": True, "role": role, "capabilities": auth_capabilities(role)})
        set_session_cookie(ctx, response, role)
        return response

    @router.post("/logout")
    def logout() -> JSONResponse:
        response = JSONResponse(content={"ok": True})
        clear_session_cookie(response)
        return response

    return router


def build_auth_middleware(ctx: AppContext) -> Callable[[Request, Callable[[Request], Any]], Any]:
    async def middleware(request: Request, call_next: Callable[[Request], Any]) -> Any:
        path = request.url.path
        if not auth_required() or not path.startswith("/api/"):
            request.state.opencrew_auth_role = AUTH_ROLE_ADMIN
            return await call_next(request)
        if path in {"/api/auth/login", "/api/auth/setup"}:
            try:
                content_length = int(request.headers.get("content-length") or "0")
            except ValueError:
                content_length = 0
            if content_length > 4096:
                return JSONResponse(status_code=413, content={"detail": "Authentication request body is too large."})
        if path in {"/api/health", "/api/auth/status", "/api/auth/login", "/api/auth/setup", "/api/auth/logout"}:
            return await call_next(request)
        if not auth_configured(ctx):
            return JSONResponse(status_code=401, content={"detail": "Application password is not configured."})
        if path.startswith("/api/session-share/") or path.startswith("/api/session-download/") or path == "/api/sessions/im/send":
            return await call_next(request)
        parsed = parse_token(ctx, request.cookies.get(SESSION_COOKIE, ""))
        if parsed["valid"]:
            request.state.opencrew_auth_role = parsed["role"]
            if parsed["role"] != AUTH_ROLE_ADMIN and any(path.startswith(prefix) for prefix in ADMIN_ONLY_PATH_PREFIXES):
                return JSONResponse(status_code=403, content={"detail": "Admin role is required."})
            return await call_next(request)
        return JSONResponse(status_code=401, content={"detail": "Authentication required."})

    return middleware
