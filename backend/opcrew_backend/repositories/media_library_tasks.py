from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select, update

from ..db.schema import media_library_tasks
from .base import Repository, row_to_dict


class MediaLibraryTaskRepository(Repository):
    def get(self, task_id: int) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            return row_to_dict(conn.execute(select(media_library_tasks).where(media_library_tasks.c.id == task_id)).first())

    def get_by_asset(self, asset_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            return row_to_dict(conn.execute(select(media_library_tasks).where(media_library_tasks.c.asset_id == asset_id)).first())

    def create_for_asset(self, *, asset_id: str, session_id: int, title: str, created_at: int) -> int:
        with self.engine.begin() as conn:
            existing = conn.execute(select(media_library_tasks.c.id).where(media_library_tasks.c.asset_id == asset_id)).first()
            if existing is not None:
                return int(existing[0])
            result = conn.execute(
                media_library_tasks.insert()
                .values(
                    asset_id=asset_id,
                    session_id=session_id,
                    title=title,
                    status="draft",
                    dialogue_status="not_analyzed",
                    visual_status="not_analyzed",
                    composite_status="not_analyzed",
                    created_at=created_at,
                    updated_at=created_at,
                )
                .returning(media_library_tasks.c.id)
            )
            return int(result.scalar_one())

    def delete_by_asset(self, asset_id: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(delete(media_library_tasks).where(media_library_tasks.c.asset_id == asset_id))

    def update_dialogue_run(
        self,
        task_id: int,
        *,
        status: str,
        updated_at: int,
        tool_use_session_id: str | None = None,
        error: str | None = None,
        progress: dict[str, Any] | None = None,
        task_status: str | None = None,
    ) -> dict[str, Any] | None:
        values: dict[str, Any] = {
            "dialogue_status": status,
            "dialogue_error": error,
            "dialogue_progress_json": progress or {},
            "updated_at": updated_at,
        }
        if tool_use_session_id is not None:
            values["dialogue_tool_use_session_id"] = tool_use_session_id
        if task_status is not None:
            values["status"] = task_status
        with self.engine.begin() as conn:
            conn.execute(update(media_library_tasks).where(media_library_tasks.c.id == task_id).values(**values))
        return self.get(task_id)

    def update_visual_run(
        self,
        task_id: int,
        *,
        status: str,
        updated_at: int,
        tool_use_session_id: str | None = None,
        error: str | None = None,
        progress: dict[str, Any] | None = None,
        task_status: str | None = None,
    ) -> dict[str, Any] | None:
        values: dict[str, Any] = {
            "visual_status": status,
            "visual_error": error,
            "visual_progress_json": progress or {},
            "updated_at": updated_at,
        }
        if tool_use_session_id is not None:
            values["visual_tool_use_session_id"] = tool_use_session_id
        if task_status is not None:
            values["status"] = task_status
        with self.engine.begin() as conn:
            conn.execute(update(media_library_tasks).where(media_library_tasks.c.id == task_id).values(**values))
        return self.get(task_id)
