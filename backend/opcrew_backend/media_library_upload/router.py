from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, File, Form, UploadFile

from .schemas import MediaLibraryUploadComplete, MediaLibraryUploadCreate
from .service import MediaLibraryUploadService


def _asset_payload(row: dict[str, Any]) -> dict[str, Any]:
    session_id = row.get("session_id")
    source_path = str(row.get("source_video_path") or "")
    encoded_path = "/".join(quote(part, safe="") for part in source_path.split("/")) if source_path else ""
    source_version = str(row.get("content_sha256") or "").strip()
    original_preview_url = f"/api/session-tasks/{session_id}/raw/{encoded_path}" if session_id and encoded_path else None
    if original_preview_url and source_version:
        original_preview_url = f"{original_preview_url}?v={quote(source_version[:32], safe='')}"
    preview_url = row.get("preview_url") or original_preview_url
    thumbnail_url = row.get("thumbnail_url") or (f"/api/session-tasks/{session_id}/thumbnail/{encoded_path}" if session_id and encoded_path else None)
    return {
        "asset_id": str(row.get("asset_id") or ""),
        "session_id": session_id,
        "display_name": str(row.get("display_name") or row.get("original_filename") or ""),
        "original_filename": str(row.get("original_filename") or ""),
        "source_video_path": source_path,
        "content_sha256": row.get("content_sha256"),
        "source_version": row.get("content_sha256"),
        "media_type": str(row.get("media_type") or "video"),
        "thumbnail_url": thumbnail_url,
        "preview_url": preview_url,
        "duration_ms": row.get("duration_ms"),
        "width": row.get("width"),
        "height": row.get("height"),
        "format": row.get("format"),
        "size_bytes": row.get("size_bytes"),
        "language": row.get("language"),
        "dialogue_summary": row.get("dialogue_summary"),
        "upload_status": str(row.get("upload_status") or "ready"),
        "analysis_status": str(row.get("analysis_status") or "not_analyzed"),
        "subtitle_mode": str(row.get("subtitle_mode") or "ocr_pending"),
        "analysis_summary": row.get("analysis_summary_json") or {},
        "tags": row.get("tags_json") or [],
        "archived": bool(row.get("archived")),
        "referenced_by_count": max(0, int(row.get("referenced_by_count") or 0)),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def build_media_library_upload_router(ctx: Any) -> APIRouter:
    router = APIRouter(prefix="/api/media-library/uploads", tags=["media-library-upload"])
    service = MediaLibraryUploadService(ctx)

    @router.post("")
    async def create_upload(payload: MediaLibraryUploadCreate) -> dict[str, Any]:
        return service.create_upload(payload)

    @router.get("/{upload_id}")
    async def upload_status(upload_id: str) -> dict[str, Any]:
        return service.status(upload_id)

    @router.post("/{upload_id}/chunks")
    async def upload_chunk(
        upload_id: str,
        chunk_index: int = Form(...),
        total_chunks: int = Form(...),
        file: UploadFile = File(...),
    ) -> dict[str, Any]:
        return await service.save_chunk(upload_id, chunk_index, total_chunks, file)

    @router.post("/{upload_id}/complete")
    async def complete_upload(upload_id: str, payload: MediaLibraryUploadComplete) -> dict[str, Any]:
        # Merging a video of up to 50 GB and running ffprobe are blocking I/O.
        # Keep them off the ASGI event loop while retaining durable DB state.
        result = await asyncio.to_thread(service.complete, upload_id, payload.size_bytes)
        item = result.get("item") if isinstance(result.get("item"), dict) else {}
        return {**result, "item": _asset_payload(item) if item else {}}

    @router.delete("/{upload_id}")
    async def cancel_upload(upload_id: str) -> dict[str, Any]:
        return service.cancel(upload_id)

    return router
