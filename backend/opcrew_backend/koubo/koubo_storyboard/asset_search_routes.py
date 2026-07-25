from __future__ import annotations

import json
from typing import Any, AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from opcrew_backend.model_policy import request_role


def register_asset_search_routes(router: APIRouter, deps: Any) -> None:

    def sse(event: dict[str, Any]) -> str:
        return f"data: {json.dumps(event, ensure_ascii=True)}\n\n"

    @router.get("/api/koubo-storyboard/tasks/{task_id}/asset-library-search/settings")
    async def get_asset_library_search_settings(task_id: int) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        settings = deps.read_asset_search_settings(task, sc=deps)
        return {"ok": True, "settings": settings, "provider_status": deps.asset_search_provider_status(settings, sc=deps)}

    @router.put("/api/koubo-storyboard/tasks/{task_id}/asset-library-search/settings")
    async def put_asset_library_search_settings(task_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        settings = deps.save_asset_search_settings(task, payload or {}, sc=deps)
        return {"ok": True, "settings": settings, "provider_status": deps.asset_search_provider_status(settings, sc=deps)}

    @router.post("/api/koubo-storyboard/tasks/{task_id}/asset-library-search/plan")
    async def create_asset_library_search_plan(task_id: int, payload: dict[str, Any], request: Request) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        plan = await deps.create_asset_search_plan(task, payload or {}, request_role(request), sc=deps)
        return {"ok": True, "plan": plan}

    @router.post("/api/koubo-storyboard/tasks/{task_id}/asset-library-search/storyboard-plan")
    async def create_asset_library_search_storyboard_plan(task_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        plan = await deps.create_storyboard_asset_search_plan(task, payload or {}, sc=deps)
        return {"ok": True, "plan": plan}

    @router.post("/api/koubo-storyboard/tasks/{task_id}/asset-library-search/search/events")
    async def asset_library_search_events(task_id: int, payload: dict[str, Any], request: Request) -> StreamingResponse:
        task = deps.task_or_404(task_id)

        async def generate() -> AsyncIterator[str]:
            async for event in deps.stream_asset_search_events(task, payload or {}, request_role(request), sc=deps):
                yield sse(event)

        return StreamingResponse(generate(), media_type="text/event-stream")

    @router.get("/api/koubo-storyboard/tasks/{task_id}/asset-library-search/runs")
    async def get_asset_library_search_runs(task_id: int) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        return {"ok": True, "runs": deps.list_asset_search_runs(task, sc=deps)}

    @router.get("/api/koubo-storyboard/tasks/{task_id}/asset-library-search/runs/{search_id}")
    async def get_asset_library_search_run(task_id: int, search_id: str) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        return {"ok": True, "run": deps.load_asset_search_run(task, search_id, sc=deps)}

    @router.post("/api/koubo-storyboard/tasks/{task_id}/asset-library-search/import")
    async def import_asset_library_search_candidates(task_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        return await deps.import_asset_search_candidates(task, payload or {}, sc=deps)

    @router.get("/api/koubo-storyboard/tasks/{task_id}/asset-library-search/source-list")
    async def get_asset_library_search_source_list(task_id: int) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        return {"ok": True, "source_list": deps.asset_search_source_list(task, sc=deps)}

    @router.post("/api/koubo-storyboard/tasks/{task_id}/asset-library-search/source-list/export")
    async def export_asset_library_search_source_list(task_id: int) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        return deps.export_asset_search_source_list(task, sc=deps)
