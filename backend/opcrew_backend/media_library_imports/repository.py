from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError

from ..db.schema import (
    media_library_assets,
    media_library_clip_derivatives,
    media_library_search_actions,
    media_library_search_runs,
    media_library_storyboard_imports,
    openclip_tasks,
    session_files,
    sessions,
)
from ..repositories.base import row_to_dict, rows_to_dicts


def _json_object(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except Exception:
        return None


class MediaLibraryImportRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def get_source_asset(self, asset_id: str) -> dict[str, Any] | None:
        statement = (
            select(
                media_library_assets,
                sessions.c.workspace_dir.label("source_workspace_dir"),
                sessions.c.status.label("source_session_status"),
            )
            .join(sessions, sessions.c.id == media_library_assets.c.session_id)
            .where(media_library_assets.c.asset_id == asset_id)
        )
        with self.engine.connect() as conn:
            return row_to_dict(conn.execute(statement).first())

    def get_source_clip(self, clip_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            return row_to_dict(conn.execute(select(media_library_clip_derivatives).where(media_library_clip_derivatives.c.clip_id == clip_id)).first())

    def get_registered_file(self, session_id: int, path: str) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            return row_to_dict(
                conn.execute(
                    select(session_files).where(
                        session_files.c.session_id == session_id,
                        session_files.c.path == path,
                    )
                ).first()
            )

    def get_target_task(self, task_id: int) -> dict[str, Any] | None:
        statement = (
            select(
                openclip_tasks,
                sessions.c.title.label("session_title"),
                sessions.c.workspace_dir,
                sessions.c.status.label("session_status"),
                sessions.c.updated_at.label("session_updated_at"),
            )
            .join(sessions, sessions.c.id == openclip_tasks.c.session_id)
            .where(openclip_tasks.c.id == task_id)
        )
        with self.engine.connect() as conn:
            return row_to_dict(conn.execute(statement).first())

    def get_session(self, session_id: int) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            return row_to_dict(conn.execute(select(sessions).where(sessions.c.id == session_id)).first())

    def list_target_tasks(self) -> list[dict[str, Any]]:
        statement = (
            select(
                openclip_tasks.c.id,
                openclip_tasks.c.session_id,
                openclip_tasks.c.status,
                openclip_tasks.c.workflow_mode,
                openclip_tasks.c.updated_at,
                sessions.c.title,
                sessions.c.workspace_dir,
                sessions.c.status.label("session_status"),
            )
            .join(sessions, sessions.c.id == openclip_tasks.c.session_id)
            .order_by(openclip_tasks.c.updated_at.desc(), openclip_tasks.c.id.desc())
        )
        with self.engine.connect() as conn:
            return rows_to_dicts(conn.execute(statement).fetchall())

    def get_import(self, import_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            return row_to_dict(conn.execute(select(media_library_storyboard_imports).where(media_library_storyboard_imports.c.import_id == import_id)).first())

    def get_import_by_key(self, idempotency_key: str) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            return row_to_dict(
                conn.execute(select(media_library_storyboard_imports).where(media_library_storyboard_imports.c.idempotency_key == idempotency_key)).first()
            )

    def list_preparing(self) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            return rows_to_dicts(
                conn.execute(
                    select(media_library_storyboard_imports)
                    .where(media_library_storyboard_imports.c.status == "preparing")
                    .order_by(media_library_storyboard_imports.c.created_at.asc())
                ).fetchall()
            )

    def has_preparing_for_asset(self, asset_id: str) -> bool:
        with self.engine.connect() as conn:
            count = conn.execute(
                select(func.count())
                .select_from(media_library_storyboard_imports)
                .where(
                    media_library_storyboard_imports.c.source_asset_id == asset_id,
                    media_library_storyboard_imports.c.status == "preparing",
                )
            ).scalar_one()
        return int(count) > 0

    def count_clip_references(self, clip_id: str) -> int:
        """Return all durable StoryBoard audit references for delete guards.

        Failed/preparing rows intentionally count too: the audit table has a
        non-cascading foreign key to the derivative and remains authoritative
        for interrupted-import cleanup and idempotency.
        """
        with self.engine.connect() as conn:
            count = conn.execute(
                select(func.count())
                .select_from(media_library_storyboard_imports)
                .where(
                    media_library_storyboard_imports.c.source_kind == "media_library_clip",
                    media_library_storyboard_imports.c.source_clip_id == clip_id,
                )
            ).scalar_one()
        return int(count)

    def has_clip_reference(self, clip_id: str) -> bool:
        return self.count_clip_references(clip_id) > 0

    def claim_import(self, values: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        try:
            with self.engine.begin() as conn:
                conn.execute(media_library_storyboard_imports.insert().values(**values))
            return dict(values), True
        except IntegrityError:
            existing = self.get_import_by_key(str(values["idempotency_key"]))
            if existing is None:
                raise
            return existing, False

    def get_search_candidate(
        self,
        search_id: str,
        source_asset_id: str,
        *,
        candidate_kind: str = "original_video",
        candidate_id: str | None = None,
    ) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            run = row_to_dict(conn.execute(select(media_library_search_runs).where(media_library_search_runs.c.search_id == search_id)).first())
        if run is None:
            return None
        candidates = _json_object(run.get("top_candidates_json"))
        if not isinstance(candidates, list):
            candidates = []
        for index, item in enumerate(candidates, start=1):
            if not isinstance(item, dict):
                continue
            item_kind = str(item.get("candidate_kind") or "")
            if not item_kind:
                item_kind = "original_video"
            if item_kind != candidate_kind:
                continue
            candidate_asset_id = str(item.get("source_asset_id") or item.get("asset_id") or item.get("provider_asset_id") or "")
            if candidate_asset_id != source_asset_id:
                continue
            item_candidate_id = str(
                item.get("candidate_id")
                or item.get("provider_asset_id")
                or source_asset_id
            )
            if candidate_id is not None and item_candidate_id != candidate_id:
                continue
            item_clip_id = str(item.get("source_clip_id") or "")
            if candidate_kind == "derived_clip" and item_clip_id != item_candidate_id:
                continue
            return {
                "run": run,
                "candidate_kind": item_kind,
                "candidate_id": item_candidate_id,
                "source_clip_id": (item_clip_id or None),
                "candidate_rank": int(item.get("rank") or index),
                "candidate_source_version": str(item.get("source_version") or ""),
                "candidate_content_sha256": str(
                    item.get("content_sha256") or ""
                ),
                "candidate_found": True,
            }
        return {
            "run": run,
            "candidate_kind": candidate_kind,
            "candidate_id": candidate_id or source_asset_id,
            "source_clip_id": None,
            "candidate_rank": None,
            "candidate_source_version": "",
            "candidate_content_sha256": "",
            "candidate_found": False,
        }

    def _insert_search_action(
        self,
        conn: Connection,
        *,
        search_id: str,
        source_asset_id: str,
        candidate_id: str,
        candidate_rank: int | None,
        target_task_id: int,
        import_id: str,
        content_sha256: str,
        source_kind: str,
        source_clip_id: str | None,
        created_at: int,
    ) -> None:
        metadata = {
            "import_id": import_id,
            "source_kind": source_kind,
            "content_sha256": content_sha256,
        }
        if source_clip_id is not None:
            metadata["source_clip_id"] = source_clip_id
        conn.execute(
            media_library_search_actions.insert().values(
                search_id=search_id,
                action_kind="import",
                source="media_library",
                candidate_id=candidate_id,
                source_asset_id=source_asset_id,
                candidate_rank=candidate_rank,
                target_task_id=target_task_id,
                metadata_json=metadata,
                created_at=created_at,
            )
        )

    def finalize_completed(
        self,
        import_id: str,
        *,
        session_id: int,
        target_path: str,
        size_bytes: int,
        source_asset_id: str,
        target_task_id: int,
        content_sha256: str,
        updated_at: int,
        search_candidate: dict[str, Any] | None,
        source_kind: str = "media_library_original",
        source_clip_id: str | None = None,
    ) -> tuple[bool, bool]:
        telemetry_written = False
        with self.engine.begin() as conn:
            claimed = conn.execute(
                update(media_library_storyboard_imports)
                .where(
                    media_library_storyboard_imports.c.import_id == import_id,
                    media_library_storyboard_imports.c.status == "preparing",
                )
                .values(
                    status="completed",
                    error_code=None,
                    size_bytes=size_bytes,
                    content_sha256=content_sha256,
                    updated_at=updated_at,
                )
            )
            if int(claimed.rowcount or 0) != 1:
                return False, False
            conn.execute(
                pg_insert(session_files)
                .values(
                    session_id=session_id,
                    path=target_path,
                    kind="video",
                    size=size_bytes,
                    origin="media_library_import",
                    downloadable=1,
                    visibility="public",
                    sensitivity="normal",
                    attempt_id=None,
                    tool_use_session_id=None,
                    stale=0,
                    updated_at=updated_at,
                )
                .on_conflict_do_update(
                    index_elements=[session_files.c.session_id, session_files.c.path],
                    set_={
                        "kind": "video",
                        "size": size_bytes,
                        "origin": "media_library_import",
                        "downloadable": 1,
                        "visibility": "public",
                        "sensitivity": "normal",
                        "attempt_id": None,
                        "tool_use_session_id": None,
                        "stale": 0,
                        "updated_at": updated_at,
                    },
                )
            )
            conn.execute(
                update(media_library_assets)
                .where(media_library_assets.c.asset_id == source_asset_id)
                .values(
                    referenced_by_count=media_library_assets.c.referenced_by_count + 1,
                    updated_at=updated_at,
                )
            )
            conn.execute(update(openclip_tasks).where(openclip_tasks.c.id == target_task_id).values(updated_at=updated_at))
            conn.execute(update(sessions).where(sessions.c.id == session_id).values(updated_at=updated_at))
            if search_candidate is not None:
                try:
                    with conn.begin_nested():
                        run = search_candidate["run"]
                        self._insert_search_action(
                            conn,
                            search_id=str(run["search_id"]),
                            source_asset_id=source_asset_id,
                            candidate_id=str(search_candidate.get("candidate_id") or source_asset_id),
                            candidate_rank=search_candidate.get("candidate_rank"),
                            target_task_id=target_task_id,
                            import_id=import_id,
                            content_sha256=content_sha256,
                            source_kind=source_kind,
                            source_clip_id=source_clip_id,
                            created_at=updated_at,
                        )
                    telemetry_written = True
                except Exception:
                    # Search telemetry is deliberately best effort. The savepoint
                    # keeps an observability failure from aborting the imported
                    # file, manifest, session_files row and import audit record.
                    telemetry_written = False
        return True, telemetry_written

    def mark_failed(self, import_id: str, *, error_code: str, updated_at: int) -> bool:
        with self.engine.begin() as conn:
            result = conn.execute(
                update(media_library_storyboard_imports)
                .where(
                    media_library_storyboard_imports.c.import_id == import_id,
                    media_library_storyboard_imports.c.status == "preparing",
                )
                .values(
                    status="failed",
                    error_code=error_code,
                    updated_at=updated_at,
                )
            )
            return int(result.rowcount or 0) == 1
