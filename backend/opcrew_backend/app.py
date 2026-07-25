from __future__ import annotations

import gzip
import os
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ._paths import ensure_model_config_backend_path
from .config import load_config
from .context import AppContext
from .koubo import (
    build_dance_mimic_router,
    build_koubo_task_list_router,
    build_koubo_storyboard_router,
    build_oc_rebuild_router,
    build_oc_storyboard_router,
    build_openclip_router,
)
from .model_leakage_guard import (
    CustomerEgressSanitizerMiddleware,
    customer_egress_role_from_request,
    sanitize_customer_payload,
    should_filter_customer_egress_path,
)
from .routes.auth import AuthBodyLimitMiddleware, build_auth_middleware, build_auth_router
from .routes.mihomo import build_mihomo_router
from .routes.local_metering import build_local_metering_router
from .routes.media_library import (
    MediaLibraryPatchBodyLimitMiddleware,
    build_media_library_router,
)
from .media_library_clips import build_media_library_clip_router
from .media_library_imports import build_media_library_import_router
from .media_library_search.router import build_media_library_search_router
from .media_library_upload import build_media_library_upload_router
from .routes.step1_opencode import build_step1_router
from .routes.step2_tunnel import build_step2_router
from .routes.step3_publish import build_step3_router
from .routes.step3_wecom import build_step3_router as build_step3_wecom_router
from .routes.step4_verify import build_step4_router
from .routes.sessions import build_session_router
from .routes.system_routes import build_system_router
from .routes.workflow_assistant_bridge import build_workflow_assistant_router

ensure_model_config_backend_path()
from opcrew_model_config import build_model_config_router


class JsonGZipMiddleware:
    def __init__(self, app: ASGIApp, minimum_size: int = 1024, compresslevel: int = 6) -> None:
        self.app = app
        self.minimum_size = minimum_size
        self.compresslevel = compresslevel

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or "gzip" not in Headers(scope=scope).get("accept-encoding", "").lower():
            await self.app(scope, receive, send)
            return

        initial_message: Message | None = None
        should_compress = False
        body_parts: list[bytes] = []

        async def send_with_gzip(message: Message) -> None:
            nonlocal initial_message, should_compress
            if message["type"] == "http.response.start":
                initial_message = message
                headers = Headers(raw=message.get("headers", []))
                content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
                should_compress = (
                    message.get("status", 200) >= 200
                    and "content-encoding" not in headers
                    and (content_type == "application/json" or content_type.endswith("+json"))
                )
                if not should_compress:
                    await send(message)
                return

            if message["type"] != "http.response.body" or not should_compress:
                await send(message)
                return

            body_parts.append(message.get("body", b""))
            if message.get("more_body", False):
                return

            assert initial_message is not None
            body = b"".join(body_parts)
            if len(body) < self.minimum_size:
                await send(initial_message)
                await send({**message, "body": body})
                return

            compressed = gzip.compress(body, compresslevel=self.compresslevel)
            headers = MutableHeaders(raw=initial_message["headers"])
            headers["Content-Encoding"] = "gzip"
            headers["Content-Length"] = str(len(compressed))
            headers.add_vary_header("Accept-Encoding")
            await send(initial_message)
            await send({**message, "body": compressed})

        await self.app(scope, receive, send_with_gzip)


def cors_origins(ctx: AppContext) -> list[str]:
    configured = [item.strip().rstrip("/") for item in os.environ.get("OPENCREW_CORS_ORIGINS", "").split(",") if item.strip()]
    frontend = ctx.config.frontend_url.rstrip("/")
    defaults = [
        frontend,
        "http://127.0.0.1:18080",
        "http://localhost:18080",
        "http://127.0.0.1.nip.io:18081",
    ]
    return sorted({*configured, *defaults})


def _sanitize_exception_content(ctx: AppContext, request: Request, content: dict[str, Any]) -> dict[str, Any]:
    if not should_filter_customer_egress_path(request.url.path):
        return content
    role = customer_egress_role_from_request(request)
    sanitized = sanitize_customer_payload(ctx, role, content)
    return sanitized if isinstance(sanitized, dict) else {"detail": sanitized}


def include_app_routers(app: FastAPI, ctx: AppContext) -> None:
    app.include_router(build_auth_router(ctx))
    app.include_router(build_mihomo_router(ctx))
    app.include_router(build_system_router(ctx))
    app.include_router(build_model_config_router(ctx))
    app.include_router(build_workflow_assistant_router(ctx))
    app.include_router(build_openclip_router(ctx))
    app.include_router(build_dance_mimic_router(ctx))
    app.include_router(build_koubo_task_list_router(ctx))
    app.include_router(build_oc_rebuild_router(ctx))
    app.include_router(build_oc_storyboard_router(ctx))
    app.include_router(build_koubo_storyboard_router(ctx))
    app.include_router(build_local_metering_router(ctx))
    app.include_router(build_media_library_upload_router(ctx))
    app.include_router(build_media_library_search_router(ctx))
    app.include_router(build_media_library_clip_router(ctx))
    app.include_router(build_media_library_import_router(ctx))
    app.include_router(build_media_library_router(ctx))
    app.include_router(build_session_router(ctx))
    app.include_router(build_step1_router(ctx))
    app.include_router(build_step2_router(ctx))
    app.include_router(build_step3_router(ctx))
    app.include_router(build_step3_wecom_router(ctx))
    app.include_router(build_step4_router(ctx))


def create_app(data_dir=None) -> FastAPI:
    ctx = AppContext(load_config(data_dir))

    app = FastAPI(title="OpenCrew Server Bridge", version="0.2.0")
    app.middleware("http")(build_auth_middleware(ctx))
    include_app_routers(app, ctx)

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        content = _sanitize_exception_content(ctx, request, {"detail": exc.detail})
        return JSONResponse(status_code=exc.status_code, content=content, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        content = _sanitize_exception_content(ctx, request, {"detail": jsonable_encoder(exc.errors())})
        return JSONResponse(status_code=422, content=content)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        ctx.event("error", "system", "Unhandled backend exception", {"error": str(exc)})
        content = _sanitize_exception_content(ctx, request, {"detail": str(exc)})
        return JSONResponse(status_code=500, content=content)

    @app.on_event("shutdown")
    def _shutdown() -> None:
        ctx.shutdown()

    app.add_middleware(AuthBodyLimitMiddleware)
    app.add_middleware(MediaLibraryPatchBodyLimitMiddleware)
    app.add_middleware(CustomerEgressSanitizerMiddleware, ctx=ctx)
    app.add_middleware(JsonGZipMiddleware, minimum_size=1024)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins(ctx),
        allow_origin_regex=r"https?://([a-zA-Z0-9.-]+[.]nip[.]io|localhost|127[.]0[.]0[.]1)(:\d+)?",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    return app
