from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ..context import AppContext, now_ms


def build_system_router(ctx: AppContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "service": "opcrew-backend", "time": now_ms()}

    @router.get("/api/setup/summary")
    async def setup_summary() -> dict[str, Any]:
        events = ctx.event_repo.recent(20)
        return {"summary": ctx.summary(), "events": events}

    return router
