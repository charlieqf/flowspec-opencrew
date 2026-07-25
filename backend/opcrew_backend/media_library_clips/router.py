from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..media_library_features import require_media_library_feature
from ..repositories.media_library import MediaLibraryRepository
from .errors import MediaClipError
from .manager import ClipJobManager


class ClipJobCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_version: str = Field(min_length=64, max_length=64)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    display_name: str = Field(min_length=1, max_length=256)
    source_scheme: str | None = Field(default=None, max_length=64)
    source_fragment_id: str | None = Field(default=None, max_length=256)
    source_analysis_run_id: str | None = Field(
        default=None, max_length=256
    )
    source_search_id: str | None = Field(default=None, max_length=256)
    source_dialogue_asset_key: str | None = Field(
        default=None, max_length=256
    )
    manual_override: bool = False
    idempotency_key: str = Field(min_length=16, max_length=128)


class ClipSearchMetadataPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = None
    tags: list[str] | None = None
    search_eligible: bool | None = None

    @model_validator(mode="after")
    def require_update(self) -> "ClipSearchMetadataPatchRequest":
        if not self.model_fields_set:
            raise ValueError("media_clip_metadata_update_required")
        if any(
            field in self.model_fields_set
            and getattr(self, field) is None
            for field in ("display_name", "tags", "search_eligible")
        ):
            raise ValueError("media_clip_metadata_null_forbidden")
        return self


def _context_event_sink(
    ctx: Any,
) -> Callable[[str, dict[str, Any]], None] | None:
    for attribute in (
        "media_library_clip_event_sink",
        "media_clip_event_sink",
    ):
        candidate = getattr(ctx, attribute, None)
        if callable(candidate):
            return candidate
    event = getattr(ctx, "event", None)
    session_events = getattr(ctx, "session_event_service", None)
    add_event = getattr(session_events, "add_event", None)
    if callable(add_event) or callable(event):
        def emit(kind: str, payload: dict[str, Any]) -> None:
            session_id = int(payload.get("source_session_id") or 0)
            if session_id > 0 and callable(add_event):
                add_event(
                    session_id,
                    kind,
                    payload,
                    workflow_id="media_library",
                )
            elif callable(event):
                event("info", "media_library_clip", kind, payload)

        return emit
    return None


def ensure_clip_job_manager(
    ctx: Any,
    *,
    event_sink: Callable[[str, dict[str, Any]], None] | None = None,
) -> ClipJobManager:
    manager = getattr(ctx, "media_clip_job_manager", None)
    if isinstance(manager, ClipJobManager):
        return manager
    engine = getattr(ctx, "engine", None)
    if engine is None:
        raise RuntimeError(
            "media library clip manager requires an available database engine"
        )
    manager = ClipJobManager(
        engine,
        event_sink=event_sink or _context_event_sink(ctx),
        metric_sink=(
            getattr(ctx, "media_library_metric_sink", None)
            or getattr(ctx, "media_library_metric", None)
        ),
    )
    setattr(ctx, "media_clip_job_manager", manager)
    manager.startup_cleanup()
    return manager


def _raise_http(exc: MediaClipError) -> None:
    raise HTTPException(
        status_code=exc.status_code,
        detail=exc.payload(),
    ) from exc


def build_media_library_clip_router(
    ctx: Any,
    *,
    manager: ClipJobManager | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/media-library", tags=["media-library"])
    clip_manager = manager
    engine = getattr(ctx, "engine", None)
    asset_repo = getattr(ctx, "media_library_repo", None)
    if asset_repo is None and engine is not None:
        asset_repo = MediaLibraryRepository(engine)

    def require_clip_manager() -> Any:
        nonlocal clip_manager
        if clip_manager is None:
            try:
                clip_manager = ensure_clip_job_manager(ctx)
            except RuntimeError as exc:
                raise HTTPException(
                    status_code=503,
                    detail={
                        "code": "media_clip_service_unavailable",
                        "user_message": "剪辑服务当前不可用。",
                        "suggested_action": "请稍后重试。",
                    },
                ) from exc
        return clip_manager

    def asset_and_session(asset_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        if asset_repo is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "media_clip_service_unavailable",
                    "user_message": "剪辑服务当前不可用。",
                    "suggested_action": "请稍后重试。",
                },
            )
        asset = asset_repo.get(asset_id)
        if asset is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "media_asset_not_found",
                    "user_message": "素材不存在或已删除。",
                },
            )
        session_id = int(asset.get("session_id") or 0)
        session = (
            ctx.session_repo.get(session_id)
            if session_id > 0 and getattr(ctx, "session_repo", None) is not None
            else None
        )
        if session is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "media_session_missing",
                    "user_message": "素材 Session 不存在。",
                },
            )
        return asset, session

    @router.post("/{asset_id}/clip-jobs", status_code=202)
    async def create_clip_job(
        asset_id: str, payload: ClipJobCreateRequest
    ) -> dict[str, Any]:
        require_media_library_feature("editor")
        asset, session = asset_and_session(asset_id)
        try:
            return require_clip_manager().submit(
                asset=asset,
                session=session,
                payload=payload.model_dump(),
            )
        except MediaClipError as exc:
            _raise_http(exc)

    @router.get("/{asset_id}/clip-jobs/{clip_job_id}")
    async def get_clip_job(
        asset_id: str, clip_job_id: str
    ) -> dict[str, Any]:
        asset_and_session(asset_id)
        try:
            return require_clip_manager().get_job(asset_id, clip_job_id)
        except MediaClipError as exc:
            _raise_http(exc)

    @router.post("/{asset_id}/clip-jobs/{clip_job_id}/cancel")
    async def cancel_clip_job(
        asset_id: str, clip_job_id: str
    ) -> dict[str, Any]:
        asset_and_session(asset_id)
        try:
            return require_clip_manager().cancel_job(asset_id, clip_job_id)
        except MediaClipError as exc:
            _raise_http(exc)

    @router.get("/{asset_id}/clips")
    async def list_clips(asset_id: str) -> dict[str, Any]:
        asset_and_session(asset_id)
        return {"items": require_clip_manager().list_clips(asset_id)}

    @router.get("/{asset_id}/clips/{clip_id}")
    async def get_clip(asset_id: str, clip_id: str) -> dict[str, Any]:
        asset_and_session(asset_id)
        try:
            return {
                "clip": require_clip_manager().get_clip(asset_id, clip_id)
            }
        except MediaClipError as exc:
            _raise_http(exc)

    @router.patch("/{asset_id}/clips/{clip_id}")
    async def update_clip_search_metadata(
        asset_id: str,
        clip_id: str,
        payload: ClipSearchMetadataPatchRequest,
    ) -> dict[str, Any]:
        require_media_library_feature("clip_search_v1")
        asset_and_session(asset_id)
        try:
            return {
                "clip": require_clip_manager().update_clip_search_metadata(
                    asset_id=asset_id,
                    clip_id=clip_id,
                    display_name=payload.display_name,
                    tags=payload.tags,
                    search_eligible=payload.search_eligible,
                    update_display_name="display_name"
                    in payload.model_fields_set,
                    update_tags="tags" in payload.model_fields_set,
                )
            }
        except MediaClipError as exc:
            _raise_http(exc)

    @router.delete("/{asset_id}/clips/{clip_id}")
    async def delete_clip(asset_id: str, clip_id: str) -> dict[str, Any]:
        require_media_library_feature("editor")
        _asset, session = asset_and_session(asset_id)
        try:
            deleted = require_clip_manager().delete_clip(
                asset_id=asset_id,
                clip_id=clip_id,
                workspace=Path(str(session["workspace_dir"])).resolve(),
            )
            return {"deleted": True, "clip": deleted}
        except MediaClipError as exc:
            _raise_http(exc)

    return router


__all__ = [
    "ClipJobCreateRequest",
    "ClipSearchMetadataPatchRequest",
    "build_media_library_clip_router",
    "ensure_clip_job_manager",
]
