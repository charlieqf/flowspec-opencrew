from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException

from ..media_library_features import require_media_library_feature
from .schemas import StoryBoardImportRequest, StoryBoardSearchImportRequest
from .service import MediaLibraryStoryBoardImportService


def build_media_library_import_router(ctx: Any) -> APIRouter:
    router = APIRouter(tags=["media-library-import"])
    service = MediaLibraryStoryBoardImportService(ctx)

    @router.get("/api/media-library/import-targets/storyboards")
    async def list_storyboard_import_targets() -> dict[str, Any]:
        return await asyncio.to_thread(service.list_targets)

    @router.post("/api/media-library/{asset_id}/import-to-storyboard")
    async def import_original_to_storyboard(
        asset_id: str, payload: StoryBoardImportRequest
    ) -> dict[str, Any]:
        return await asyncio.to_thread(service.import_original, asset_id, payload)

    @router.post(
        "/api/media-library/{asset_id}/clips/{clip_id}/import-to-storyboard"
    )
    async def import_clip_to_storyboard(
        asset_id: str,
        clip_id: str,
        payload: StoryBoardImportRequest,
    ) -> dict[str, Any]:
        require_media_library_feature("editor")
        return await asyncio.to_thread(
            service.import_clip, asset_id, clip_id, payload
        )

    @router.post(
        "/api/media-library/{asset_id}/search/runs/{search_id}/import-to-storyboard"
    )
    async def import_search_result_to_storyboard(
        asset_id: str, search_id: str, payload: StoryBoardImportRequest
    ) -> dict[str, Any]:
        require_media_library_feature("library_search")
        if payload.search_id and payload.search_id != search_id:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "search_run_mismatch",
                    "message": "路径和请求体中的检索运行不一致。",
                },
            )
        normalized = payload.model_copy(update={"search_id": search_id})
        return await asyncio.to_thread(
            service.import_original, asset_id, normalized
        )

    @router.post(
        "/api/koubo-storyboard/tasks/{task_id}/media-library-search/import"
    )
    async def import_storyboard_search_result(
        task_id: int, payload: StoryBoardSearchImportRequest
    ) -> dict[str, Any]:
        require_media_library_feature("library_search")
        if payload.target_task_id and payload.target_task_id != task_id:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "storyboard_target_mismatch",
                    "message": "路径和请求体中的目标 Task 不一致。",
                },
            )
        normalized = StoryBoardImportRequest(
            target_task_id=task_id,
            requested_name=payload.requested_name,
            search_id=payload.search_id,
            dialogue_asset_key=payload.dialogue_asset_key,
            idempotency_key=payload.idempotency_key,
        )
        if payload.source_kind == "media_library_clip":
            require_media_library_feature("clip_search_v1")
            clip = await asyncio.to_thread(
                service.repo.get_source_clip, payload.source_id
            )
            if clip is None:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "code": "media_clip_not_found",
                        "message": "派生片段不存在或已删除。",
                    },
                )
            return await asyncio.to_thread(
                service.import_clip,
                str(clip["source_asset_id"]),
                payload.source_id,
                normalized,
                search_candidate_kind="derived_clip",
            )
        return await asyncio.to_thread(
            service.import_original, payload.source_id, normalized
        )

    return router
