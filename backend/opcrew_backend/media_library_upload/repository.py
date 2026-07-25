from __future__ import annotations

import json
from typing import Any

from sqlalchemy import delete, or_, select, update
from sqlalchemy.engine import Engine

from ..db.schema import media_library_assets, media_library_tasks, media_library_uploads, session_files
from ..repositories.base import row_to_dict


def _chunk_indexes(value: Any) -> list[int]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            value = []
    if not isinstance(value, list):
        return []
    return sorted({int(item) for item in value if isinstance(item, int) or str(item).isdigit()})


class MediaLibraryUploadRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def create_pending(self, *, asset: dict[str, Any], upload: dict[str, Any]) -> None:
        with self.engine.begin() as conn:
            conn.execute(media_library_assets.insert().values(**asset))
            conn.execute(media_library_uploads.insert().values(**upload))

    def get_upload(self, upload_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            row = row_to_dict(conn.execute(select(media_library_uploads).where(media_library_uploads.c.upload_id == upload_id)).first())
        if row is not None:
            row["received_chunks_json"] = _chunk_indexes(row.get("received_chunks_json"))
        return row

    def get_asset(self, asset_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            return row_to_dict(conn.execute(select(media_library_assets).where(media_library_assets.c.asset_id == asset_id)).first())

    def get_upload_by_asset(self, asset_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            row = row_to_dict(conn.execute(select(media_library_uploads).where(media_library_uploads.c.asset_id == asset_id)).first())
        if row is not None:
            row["received_chunks_json"] = _chunk_indexes(row.get("received_chunks_json"))
        return row

    def record_chunk(self, upload_id: str, chunk_index: int, *, chunk_bytes: int, updated_at: int, expires_at: int) -> dict[str, Any] | None:
        with self.engine.begin() as conn:
            # Browser uploads use a small concurrent worker pool. Serialize the
            # JSON progress merge so two completed chunks cannot overwrite each
            # other's indexes and leave finalization permanently incomplete.
            row = row_to_dict(
                conn.execute(
                    select(media_library_uploads)
                    .where(media_library_uploads.c.upload_id == upload_id)
                    .with_for_update()
                ).first()
            )
            if row is None:
                return None
            indexes = _chunk_indexes(row.get("received_chunks_json"))
            if chunk_index not in indexes:
                indexes.append(chunk_index)
                indexes.sort()
            received_bytes = 0
            for index in indexes:
                if index < int(row["total_chunks"]) - 1:
                    received_bytes += int(row["chunk_size"])
                elif index == int(row["total_chunks"]) - 1:
                    received_bytes += int(row["size_bytes"]) - (int(row["total_chunks"]) - 1) * int(row["chunk_size"])
            conn.execute(
                update(media_library_uploads)
                .where(
                    media_library_uploads.c.upload_id == upload_id,
                    media_library_uploads.c.status == "uploading",
                )
                .values(
                    received_chunks_json=indexes,
                    received_bytes=min(int(row["size_bytes"]), received_bytes),
                    updated_at=updated_at,
                    expires_at=expires_at,
                    error=None,
                )
            )
        return self.get_upload(upload_id)

    def claim_finalization(
        self,
        upload_id: str,
        *,
        token: str,
        updated_at: int,
        stale_before: int,
    ) -> dict[str, Any] | None:
        """Atomically grant exactly one worker ownership of finalization.

        A stale claim can be recovered after a process restart. The token also
        prevents a superseded worker from publishing ready/failed state.
        """

        with self.engine.begin() as conn:
            result = conn.execute(
                update(media_library_uploads)
                .where(
                    media_library_uploads.c.upload_id == upload_id,
                    or_(
                        media_library_uploads.c.status == "uploading",
                        (
                            (media_library_uploads.c.status == "finalizing")
                            & or_(
                                media_library_uploads.c.finalization_started_at.is_(None),
                                media_library_uploads.c.finalization_started_at < stale_before,
                            )
                        ),
                    ),
                )
                .values(
                    status="finalizing",
                    error=None,
                    finalization_token=token,
                    finalization_started_at=updated_at,
                    updated_at=updated_at,
                )
            )
            if result.rowcount != 1:
                return None
        return self.get_upload(upload_id)

    def mark_ready(
        self,
        upload_id: str,
        *,
        source_video_path: str,
        content_sha256: str,
        duration_ms: int | None,
        width: int | None,
        height: int | None,
        media_format: str,
        preview_url: str | None,
        thumbnail_url: str | None,
        updated_at: int,
        finalization_token: str,
    ) -> dict[str, Any] | None:
        with self.engine.begin() as conn:
            upload_row = row_to_dict(conn.execute(select(media_library_uploads).where(media_library_uploads.c.upload_id == upload_id)).first())
            if upload_row is None:
                return None
            result = conn.execute(
                update(media_library_uploads)
                .where(
                    media_library_uploads.c.upload_id == upload_id,
                    media_library_uploads.c.status == "finalizing",
                    media_library_uploads.c.finalization_token == finalization_token,
                )
                .values(
                    status="ready",
                    received_bytes=media_library_uploads.c.size_bytes,
                    error=None,
                    finalization_token=None,
                    finalization_started_at=None,
                    updated_at=updated_at,
                )
            )
            if result.rowcount != 1:
                return None
            conn.execute(
                update(media_library_assets)
                .where(media_library_assets.c.asset_id == upload_row["asset_id"])
                .values(
                    source_video_path=source_video_path,
                    content_sha256=content_sha256,
                    content_hashed_at=updated_at,
                    upload_status="ready",
                    duration_ms=duration_ms,
                    width=width,
                    height=height,
                    format=media_format,
                    preview_url=preview_url,
                    thumbnail_url=thumbnail_url,
                    size_bytes=upload_row["size_bytes"],
                    updated_at=updated_at,
                )
            )
        return self.get_asset(str(upload_row["asset_id"]))

    def mark_failed(self, upload_id: str, *, message: str, updated_at: int, finalization_token: str) -> bool:
        with self.engine.begin() as conn:
            upload_row = row_to_dict(conn.execute(select(media_library_uploads).where(media_library_uploads.c.upload_id == upload_id)).first())
            if upload_row is None:
                return False
            result = conn.execute(
                update(media_library_uploads)
                .where(
                    media_library_uploads.c.upload_id == upload_id,
                    media_library_uploads.c.status == "finalizing",
                    media_library_uploads.c.finalization_token == finalization_token,
                )
                .values(
                    status="failed",
                    error=message,
                    finalization_token=None,
                    finalization_started_at=None,
                    updated_at=updated_at,
                )
            )
            if result.rowcount != 1:
                return False
            conn.execute(update(media_library_assets).where(media_library_assets.c.asset_id == upload_row["asset_id"]).values(upload_status="failed", updated_at=updated_at))
            return True

    def record_session_file(
        self,
        *,
        session_id: int,
        path: str,
        size: int,
        updated_at: int,
        origin: str = "upload",
        downloadable: bool = True,
    ) -> None:
        with self.engine.begin() as conn:
            existing = conn.execute(select(session_files.c.id).where(session_files.c.session_id == session_id, session_files.c.path == path)).first()
            values = {
                "kind": "video",
                "size": size,
                "origin": origin,
                "downloadable": 1 if downloadable else 0,
                "visibility": "internal",
                "sensitivity": "normal",
                "attempt_id": None,
                "tool_use_session_id": None,
                "stale": 0,
                "updated_at": updated_at,
            }
            if existing is None:
                conn.execute(session_files.insert().values(session_id=session_id, path=path, **values))
            else:
                conn.execute(update(session_files).where(session_files.c.id == existing[0]).values(**values))

    def delete_upload_and_asset(self, upload_id: str) -> dict[str, Any] | None:
        with self.engine.begin() as conn:
            upload_row = row_to_dict(conn.execute(select(media_library_uploads).where(media_library_uploads.c.upload_id == upload_id)).first())
            if upload_row is None:
                return None
            conn.execute(delete(media_library_tasks).where(media_library_tasks.c.asset_id == upload_row["asset_id"]))
            conn.execute(delete(media_library_uploads).where(media_library_uploads.c.upload_id == upload_id))
            conn.execute(delete(media_library_assets).where(media_library_assets.c.asset_id == upload_row["asset_id"]))
            return upload_row

    def delete_asset(self, asset_id: str) -> dict[str, Any] | None:
        with self.engine.begin() as conn:
            asset_row = row_to_dict(conn.execute(select(media_library_assets).where(media_library_assets.c.asset_id == asset_id)).first())
            if asset_row is None:
                return None
            conn.execute(delete(media_library_tasks).where(media_library_tasks.c.asset_id == asset_id))
            conn.execute(delete(media_library_uploads).where(media_library_uploads.c.asset_id == asset_id))
            conn.execute(delete(media_library_assets).where(media_library_assets.c.asset_id == asset_id))
            return asset_row
