from __future__ import annotations

import math
import os
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import HTTPException, UploadFile

from ..context import now_ms
from ..services.opencode_runtime import opencode_client_for_context
from ..repositories.media_library_tasks import MediaLibraryTaskRepository
from .repository import MediaLibraryUploadRepository
from .schemas import MediaLibraryUploadCreate
from .storage import (
    create_proxy_preview,
    display_name_from_filename,
    merge_chunks,
    probe_video,
    remove_upload_files,
    safe_video_filename,
    write_chunk,
)


OPEN_CUT_SOURCE = "open-cut-v1"
OPEN_CUT_GROUP_ID = "open-cut-v1"
DEFAULT_CHUNK_SIZE = 16 * 1024 * 1024
UPLOAD_TTL_MS = 24 * 60 * 60 * 1000
FINALIZATION_STALE_MS = 6 * 60 * 60 * 1000
DEFAULT_MAX_UPLOAD_BYTES = 50 * 1024 * 1024 * 1024


def configured_max_upload_bytes() -> int:
    try:
        return max(DEFAULT_CHUNK_SIZE, int(os.environ.get("OPENCREW_MEDIA_LIBRARY_MAX_UPLOAD_BYTES") or DEFAULT_MAX_UPLOAD_BYTES))
    except ValueError:
        return DEFAULT_MAX_UPLOAD_BYTES


class MediaLibraryUploadService:
    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self.repo = MediaLibraryUploadRepository(ctx.engine)
        self.task_repo = getattr(ctx, "media_library_task_repo", None) or MediaLibraryTaskRepository(ctx.engine)

    def _opencode_client(self, session_row: dict[str, Any]) -> Any:
        factory = getattr(self.ctx, "media_library_opencode_client_factory", None)
        if callable(factory):
            return factory(session_row)
        try:
            return opencode_client_for_context(self.ctx, session_row, "OpenCode 连接未完成，请先在 Connection 中完成连接。")
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail={"code": "media_upload_opencode_unavailable", "message": str(exc)}) from exc

    def create_upload(self, payload: MediaLibraryUploadCreate) -> dict[str, Any]:
        safe_filename = safe_video_filename(payload.filename)
        if payload.size_bytes > configured_max_upload_bytes():
            raise HTTPException(status_code=413, detail={"code": "media_upload_too_large", "message": "视频超过素材库允许的最大上传大小。"})
        created = now_ms()
        upload_id = f"mlu_{created}_{uuid.uuid4().hex[:12]}"
        asset_id = f"mla_{created}_{uuid.uuid4().hex[:12]}"
        title = display_name_from_filename(payload.filename)
        total_chunks = max(1, math.ceil(payload.size_bytes / DEFAULT_CHUNK_SIZE))
        session_id: int | None = None
        session_row: dict[str, Any] | None = None
        opencode_session_id = ""
        try:
            session_id = self.ctx.session_repo.create(
                source=OPEN_CUT_SOURCE,
                group_id=OPEN_CUT_GROUP_ID,
                sender_name="OpenCut V1",
                title=title,
                command_text="",
                status="queued",
                workspace_dir=str(self.ctx.workspace_store.sessions_root() / "pending" / str(created) / "workspace"),
                share_token=self.ctx.new_share_token(),
                created_at=created,
                updated_at=created,
            )
            workspace = self.ctx.workspace_store.create_session_workspace(session_id)
            self.ctx.session_repo.update(session_id, workspace_dir=str(workspace), updated_at=created)
            session_row = self.ctx.session_repo.get(session_id)
            if session_row is None:
                raise RuntimeError("创建素材 Session 后无法读取 Session。")
            opencode_session = self._opencode_client(session_row).create_session(title)
            opencode_session_id = str(opencode_session["id"])
            self.ctx.session_repo.update(session_id, opencode_session_id=opencode_session_id, status="draft", updated_at=now_ms())
            self.repo.create_pending(
                asset={
                    "asset_id": asset_id,
                    "session_id": session_id,
                    "display_name": title,
                    "original_filename": payload.filename,
                    "source_video_path": None,
                    "content_sha256": None,
                    "content_hashed_at": None,
                    "media_type": "video",
                    "thumbnail_url": None,
                    "preview_url": None,
                    "duration_ms": None,
                    "width": None,
                    "height": None,
                    "format": Path(safe_filename).suffix.lstrip(".").lower(),
                    "size_bytes": payload.size_bytes,
                    "language": None,
                    "dialogue_summary": None,
                    "upload_status": "uploading",
                    "analysis_status": "not_analyzed",
                    "subtitle_mode": "ocr_pending",
                    "analysis_summary_json": None,
                    "tags_json": [],
                    "archived": False,
                    "referenced_by_count": 0,
                    "created_at": created,
                    "updated_at": created,
                },
                upload={
                    "upload_id": upload_id,
                    "asset_id": asset_id,
                    "session_id": session_id,
                    "filename": payload.filename,
                    "safe_filename": safe_filename,
                    "content_type": payload.content_type,
                    "size_bytes": payload.size_bytes,
                    "chunk_size": DEFAULT_CHUNK_SIZE,
                    "total_chunks": total_chunks,
                    "received_chunks_json": [],
                    "received_bytes": 0,
                    "status": "uploading",
                    "error": None,
                    "created_at": created,
                    "updated_at": created,
                    "expires_at": created + UPLOAD_TTL_MS,
                },
            )
            self.task_repo.create_for_asset(asset_id=asset_id, session_id=session_id, title=title, created_at=created)
            self.ctx.session_event_service.add_event(session_id, "media_library.upload.created", {"asset_id": asset_id, "upload_id": upload_id, "filename": payload.filename, "bytes": payload.size_bytes}, workflow_id=OPEN_CUT_SOURCE)
            return self.status(upload_id)
        except Exception:
            try:
                self.repo.delete_upload_and_asset(upload_id)
            except Exception:
                pass
            if session_row is not None and opencode_session_id:
                try:
                    self._opencode_client(session_row).delete_session(opencode_session_id)
                except Exception:
                    pass
            if session_id is not None:
                current = self.ctx.session_repo.get(session_id)
                if current is not None:
                    try:
                        self.ctx.session_repo.delete(session_id)
                    finally:
                        try:
                            self.ctx.workflow_deletion_service.cleanup_workspace(current)
                        except Exception:
                            pass
            raise

    def require_upload(self, upload_id: str) -> dict[str, Any]:
        row = self.repo.get_upload(upload_id)
        if row is None:
            raise HTTPException(status_code=404, detail={"code": "media_upload_not_found", "message": "上传任务不存在或已被清理。"})
        return row

    def status(self, upload_id: str) -> dict[str, Any]:
        row = self.require_upload(upload_id)
        return {
            "upload_id": row["upload_id"],
            "asset_id": row["asset_id"],
            "session_id": row["session_id"],
            "filename": row["filename"],
            "size_bytes": row["size_bytes"],
            "chunk_size": row["chunk_size"],
            "total_chunks": row["total_chunks"],
            "received_chunks": row.get("received_chunks_json") or [],
            "received_bytes": row.get("received_bytes") or 0,
            "status": row["status"],
            "error": row.get("error"),
        }

    async def save_chunk(self, upload_id: str, chunk_index: int, total_chunks: int, file: UploadFile) -> dict[str, Any]:
        row = self.require_upload(upload_id)
        if row["status"] != "uploading":
            raise HTTPException(status_code=409, detail={"code": "media_upload_not_writable", "message": "当前上传任务不能继续写入分片。"})
        if total_chunks != int(row["total_chunks"]):
            raise HTTPException(status_code=422, detail={"code": "media_upload_chunk_count_mismatch", "message": "分片总数与上传任务不一致。"})
        if chunk_index < 0 or chunk_index >= total_chunks:
            raise HTTPException(status_code=422, detail={"code": "media_upload_chunk_index_invalid", "message": "分片编号无效。"})
        expected = int(row["chunk_size"]) if chunk_index < total_chunks - 1 else int(row["size_bytes"]) - (total_chunks - 1) * int(row["chunk_size"])
        session_row = self.ctx.session_repo.get(int(row["session_id"]))
        if session_row is None:
            raise HTTPException(status_code=409, detail={"code": "media_upload_session_missing", "message": "素材 Session 不存在。"})
        await write_chunk(Path(session_row["workspace_dir"]), upload_id, chunk_index, file, expected_bytes=expected)
        updated = self.repo.record_chunk(upload_id, chunk_index, chunk_bytes=expected, updated_at=now_ms(), expires_at=now_ms() + UPLOAD_TTL_MS)
        return self.status(upload_id) if updated is not None else self.require_upload(upload_id)

    def complete(self, upload_id: str, declared_size: int) -> dict[str, Any]:
        row = self.require_upload(upload_id)
        if row["status"] == "ready":
            asset = self.repo.get_asset(str(row["asset_id"]))
            return {"upload": self.status(upload_id), "item": asset or {}}
        if row["status"] == "finalizing" and int(row.get("finalization_started_at") or 0) >= now_ms() - FINALIZATION_STALE_MS:
            return {"upload": self.status(upload_id), "item": {}, "finalizing": True}
        if row["status"] not in {"uploading", "finalizing"}:
            raise HTTPException(status_code=409, detail={"code": "media_upload_not_completable", "message": "当前上传任务不能完成。"})
        if declared_size != int(row["size_bytes"]):
            raise HTTPException(status_code=422, detail={"code": "media_upload_size_mismatch", "message": "完成请求中的文件大小与上传任务不一致。"})
        received = set(row.get("received_chunks_json") or [])
        expected = set(range(int(row["total_chunks"])))
        if received != expected:
            raise HTTPException(status_code=409, detail={"code": "media_upload_chunks_missing", "message": "仍有视频分片未上传完成。", "missing_chunks": sorted(expected - received)[:50]})
        session_row = self.ctx.session_repo.get(int(row["session_id"]))
        if session_row is None:
            raise HTTPException(status_code=409, detail={"code": "media_upload_session_missing", "message": "素材 Session 不存在。"})
        workspace = Path(session_row["workspace_dir"])
        finalization_token = uuid.uuid4().hex
        claimed = self.repo.claim_finalization(
            upload_id,
            token=finalization_token,
            updated_at=now_ms(),
            stale_before=now_ms() - FINALIZATION_STALE_MS,
        )
        if claimed is None:
            current = self.require_upload(upload_id)
            if current["status"] == "ready":
                asset = self.repo.get_asset(str(current["asset_id"]))
                return {"upload": self.status(upload_id), "item": asset or {}}
            if current["status"] == "finalizing":
                return {"upload": self.status(upload_id), "item": {}, "finalizing": True}
            raise HTTPException(status_code=409, detail={"code": "media_upload_not_completable", "message": "当前上传任务不能完成。"})
        preview_path: Path | None = None
        ready_published = False
        try:
            source_rel, final_path, content_sha256 = merge_chunks(
                workspace,
                upload_id,
                str(row["safe_filename"]),
                total_chunks=int(row["total_chunks"]),
                expected_size=int(row["size_bytes"]),
                finalization_token=finalization_token,
            )
            metadata = probe_video(final_path)
            preview = create_proxy_preview(workspace, str(row["asset_id"]), final_path, metadata)
            preview_url: str | None = None
            thumbnail_url: str | None = None
            preview_rel: str | None = None
            if preview is not None:
                preview_rel, preview_path = preview
                encoded_preview = "/".join(quote(part, safe="") for part in preview_rel.split("/"))
                preview_url = (
                    f"/api/session-tasks/{int(row['session_id'])}/raw/{encoded_preview}"
                    f"?v={content_sha256[:16]}"
                )
                thumbnail_url = (
                    f"/api/session-tasks/{int(row['session_id'])}/thumbnail/{encoded_preview}"
                    f"?v={content_sha256[:16]}"
                )
            asset = self.repo.mark_ready(
                upload_id,
                source_video_path=source_rel,
                content_sha256=content_sha256,
                duration_ms=metadata.get("duration_ms"),
                width=metadata.get("width"),
                height=metadata.get("height"),
                media_format=final_path.suffix.lstrip(".").lower(),
                preview_url=preview_url,
                thumbnail_url=thumbnail_url,
                updated_at=now_ms(),
                finalization_token=finalization_token,
            )
            if asset is None:
                raise HTTPException(status_code=409, detail={"code": "media_upload_finalization_superseded", "message": "上传完成任务已由另一个进程接管，请稍后查询状态。"})
            ready_published = True
            self.repo.record_session_file(session_id=int(row["session_id"]), path=source_rel, size=int(row["size_bytes"]), updated_at=now_ms())
            if preview_rel is not None and preview_path is not None:
                self.repo.record_session_file(
                    session_id=int(row["session_id"]),
                    path=preview_rel,
                    size=preview_path.stat().st_size,
                    updated_at=now_ms(),
                    origin="generated_preview",
                    downloadable=True,
                )
                self.ctx.session_event_service.add_event(
                    int(row["session_id"]),
                    "media_library.preview.generated",
                    {
                        "asset_id": str(row["asset_id"]),
                        "upload_id": upload_id,
                        "path": preview_rel,
                        "source_bytes": int(row["size_bytes"]),
                        "preview_bytes": preview_path.stat().st_size,
                        "source_bit_rate": metadata.get("bit_rate") or metadata.get("video_bit_rate"),
                    },
                    workflow_id=OPEN_CUT_SOURCE,
                )
            self.ctx.session_event_service.add_event(int(row["session_id"]), "file.uploaded", {"asset_id": row["asset_id"], "upload_id": upload_id, "path": source_rel, "name": row["filename"], "bytes": row["size_bytes"]}, workflow_id=OPEN_CUT_SOURCE)
            self.ctx.session_event_service.add_event(
                int(row["session_id"]),
                "media_library.source_hash.completed",
                {
                    "asset_id": str(row["asset_id"]),
                    "upload_id": upload_id,
                    "source_version": content_sha256,
                    "size_bytes": int(row["size_bytes"]),
                },
                workflow_id=OPEN_CUT_SOURCE,
            )
            return {"upload": self.status(upload_id), "item": asset or {}}
        except Exception as exc:
            if preview_path is not None and not ready_published:
                preview_path.unlink(missing_ok=True)
            self.repo.mark_failed(
                upload_id,
                message=str(exc),
                updated_at=now_ms(),
                finalization_token=finalization_token,
            )
            raise

    def cancel(self, upload_id: str) -> dict[str, Any]:
        row = self.require_upload(upload_id)
        if row["status"] == "ready":
            raise HTTPException(status_code=409, detail={"code": "media_upload_already_completed", "message": "已完成的素材不能通过取消上传删除。"})
        if row["status"] == "finalizing":
            raise HTTPException(status_code=409, detail={"code": "media_upload_finalizing", "message": "视频正在完成保存，暂时不能取消。"})
        session_row = self.ctx.session_repo.get(int(row["session_id"]))
        if session_row is not None:
            remove_upload_files(Path(session_row["workspace_dir"]), upload_id, str(row.get("safe_filename") or ""))
            opencode_session_id = str(session_row.get("opencode_session_id") or "")
            if opencode_session_id:
                try:
                    self._opencode_client(session_row).delete_session(opencode_session_id)
                except Exception:
                    pass
        self.repo.delete_upload_and_asset(upload_id)
        if session_row is not None:
            self.ctx.session_repo.delete(int(row["session_id"]))
            try:
                self.ctx.workflow_deletion_service.cleanup_workspace(session_row)
            except FileNotFoundError:
                pass
        return {"ok": True, "upload_id": upload_id, "session_id": row["session_id"], "asset_id": row["asset_id"]}

    def delete_ready_asset(self, asset_id: str) -> dict[str, Any]:
        asset = self.repo.get_asset(asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail={"code": "media_asset_not_found", "message": "素材不存在或已删除。"})
        session_id = int(asset.get("session_id") or 0)
        session_row = self.ctx.session_repo.get(session_id) if session_id else None
        upload = self.repo.get_upload_by_asset(asset_id)

        if session_row is not None:
            opencode_session_id = str(session_row.get("opencode_session_id") or "")
            if opencode_session_id:
                try:
                    self._opencode_client(session_row).delete_session(opencode_session_id)
                except Exception:
                    pass

        self.repo.delete_asset(asset_id)
        if session_row is not None:
            self.ctx.session_repo.delete(session_id)
            try:
                self.ctx.workflow_deletion_service.cleanup_workspace(session_row)
            except FileNotFoundError:
                pass
        return {
            "ok": True,
            "asset_id": asset_id,
            "upload_id": upload.get("upload_id") if upload else None,
            "session_id": session_id or None,
        }
