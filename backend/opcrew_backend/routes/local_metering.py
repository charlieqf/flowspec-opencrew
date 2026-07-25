from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from ..context import AppContext
from ..services.local_metering import LocalMeteringService


def build_local_metering_router(ctx: AppContext) -> APIRouter:
    router = APIRouter(prefix="/api/local-metering", tags=["local-metering"])
    service = LocalMeteringService(ctx.engine)

    @router.get("/report")
    def report(days: int = Query(30, ge=1, le=3650), limit: int = Query(200, ge=1, le=1000)) -> dict[str, Any]:
        return service.report(days=days, limit=limit)

    @router.get("/tasks/{task_id}")
    def task_report(
        task_id: int,
        attempt: str = Query("all"),
        limit: int = Query(500, ge=1, le=2000),
        include_items: bool = Query(True),
    ) -> dict[str, Any]:
        return service.task_report(task_id=task_id, attempt=attempt, limit=limit, include_items=include_items)

    return router
