from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ..adapters.opencode import probe_server
from ..context import AppContext, now_ms
from ..schemas import OpenCodeServerPayload
from ..services.opencode_runtime import discover_and_save_opencode_runtime, opencode_runtime_status


def build_step1_router(ctx: AppContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/setup/opencode/status")
    async def opencode_status() -> dict[str, Any]:
        return ctx.runtime_repo.get_runtime("opencode") or {}

    @router.post("/api/setup/opencode/discover")
    async def opencode_discover() -> dict[str, Any]:
        result = discover_and_save_opencode_runtime(ctx, reason="manual")
        return result

    @router.post("/api/setup/opencode/save")
    async def opencode_save(payload: OpenCodeServerPayload) -> dict[str, Any]:
        base_url = payload.base_url.strip()
        ctx.set_setting("opencode.base_url", base_url)
        ctx.set_setting("opencode.username", payload.username.strip())
        ctx.set_setting("opencode.password", payload.password.strip())
        ctx.runtime_repo.update_runtime(
            "opencode",
            base_url=base_url,
            auth_username=payload.username.strip() or None,
            auth_password=payload.password.strip() or None,
            checked_at=now_ms(),
        )
        ctx.event("info", "opencode", "OpenCode base URL saved", {"base_url": base_url})
        return {"ok": True}

    @router.post("/api/setup/opencode/check")
    async def opencode_check(payload: OpenCodeServerPayload) -> dict[str, Any]:
        base_url = payload.base_url.strip()
        username = payload.username.strip() or None
        password = payload.password.strip() or None
        ctx.set_setting("opencode.base_url", base_url)
        ctx.set_setting("opencode.username", username or "")
        ctx.set_setting("opencode.password", password or "")
        result = probe_server(base_url, username=username, password=password)
        ctx.runtime_repo.update_runtime(
            "opencode",
            status=opencode_runtime_status(result.probe_status, result.ok),
            base_url=result.base_url,
            health_url=result.health_url,
            auth_username=username,
            auth_password=password,
            auth_source="manual" if username and password else None,
            version=result.version,
            error=result.error,
            checked_at=now_ms(),
        )
        ctx.event(
            "info" if result.ok else "error",
            "opencode",
            "OpenCode check completed",
            {
                "status": "ready" if result.ok else "failed",
                "base_url": result.base_url,
                "health_url": result.health_url,
                "version": result.version,
                "probe_status": result.probe_status,
                "http_status": result.http_status,
                "auth_used": bool(username and password),
                "error": result.error,
            },
        )
        return ctx.runtime_repo.get_runtime("opencode") or {}

    return router
