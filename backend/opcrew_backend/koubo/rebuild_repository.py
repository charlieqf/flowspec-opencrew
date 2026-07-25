from __future__ import annotations

from typing import Any

from sqlalchemy import delete, desc, func, select, update
from sqlalchemy.exc import IntegrityError

from opcrew_backend.db.schema import oc_rebuild_attempts, oc_rebuild_prompt_versions, oc_rebuild_tasks, sessions
from opcrew_backend.repositories.base import Repository, row_to_dict, rows_to_dicts


class OCRebuildRepository(Repository):
    def create_task(self, **fields: Any) -> int:
        with self.engine.begin() as conn:
            result = conn.execute(oc_rebuild_tasks.insert().values(**fields).returning(oc_rebuild_tasks.c.id))
            return int(result.scalar_one())

    def get_task(self, task_id: int) -> dict[str, Any] | None:
        statement = select(oc_rebuild_tasks, sessions.c.title, sessions.c.opencode_session_id, sessions.c.workspace_dir, sessions.c.status.label("session_status")).join(
            sessions, sessions.c.id == oc_rebuild_tasks.c.session_id
        ).where(oc_rebuild_tasks.c.id == task_id)
        with self.engine.connect() as conn:
            return row_to_dict(conn.execute(statement).first())

    def list_tasks(self) -> list[dict[str, Any]]:
        statement = select(oc_rebuild_tasks, sessions.c.title, sessions.c.opencode_session_id, sessions.c.workspace_dir).join(
            sessions, sessions.c.id == oc_rebuild_tasks.c.session_id
        ).order_by(desc(oc_rebuild_tasks.c.updated_at), desc(oc_rebuild_tasks.c.id))
        with self.engine.connect() as conn:
            return rows_to_dicts(conn.execute(statement).fetchall())

    def update_task(self, task_id: int, **fields: Any) -> None:
        allowed = set(oc_rebuild_tasks.c.keys())
        fields = {key: value for key, value in fields.items() if key in allowed and key != "id"}
        if not fields:
            return
        with self.engine.begin() as conn:
            conn.execute(update(oc_rebuild_tasks).where(oc_rebuild_tasks.c.id == task_id).values(**fields))

    def delete_task(self, task_id: int) -> None:
        with self.engine.begin() as conn:
            conn.execute(delete(oc_rebuild_attempts).where(oc_rebuild_attempts.c.task_id == task_id))
            conn.execute(delete(oc_rebuild_prompt_versions).where(oc_rebuild_prompt_versions.c.task_id == task_id))
            conn.execute(delete(oc_rebuild_tasks).where(oc_rebuild_tasks.c.id == task_id))

    def create_version(self, **fields: Any) -> dict[str, Any]:
        with self.engine.begin() as conn:
            result = conn.execute(oc_rebuild_prompt_versions.insert().values(**fields).returning(oc_rebuild_prompt_versions.c.id))
            version_id = int(result.scalar_one())
        return self.get_version(version_id) or {}

    def get_version(self, version_id: int) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            return row_to_dict(conn.execute(select(oc_rebuild_prompt_versions).where(oc_rebuild_prompt_versions.c.id == version_id)).first())

    def update_version(self, version_id: int, **fields: Any) -> dict[str, Any]:
        allowed = set(oc_rebuild_prompt_versions.c.keys())
        fields = {key: value for key, value in fields.items() if key in allowed and key != "id"}
        if fields:
            with self.engine.begin() as conn:
                conn.execute(update(oc_rebuild_prompt_versions).where(oc_rebuild_prompt_versions.c.id == version_id).values(**fields))
        return self.get_version(version_id) or {}

    def list_versions(self, task_id: int) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            return rows_to_dicts(conn.execute(select(oc_rebuild_prompt_versions).where(oc_rebuild_prompt_versions.c.task_id == task_id).order_by(desc(oc_rebuild_prompt_versions.c.id))).fetchall())

    def delete_version(self, task_id: int, version_id: int) -> None:
        with self.engine.begin() as conn:
            conn.execute(delete(oc_rebuild_prompt_versions).where(oc_rebuild_prompt_versions.c.task_id == task_id, oc_rebuild_prompt_versions.c.id == version_id))

    def create_attempt(self, *, task_id: int, **fields: Any) -> dict[str, Any]:
        # M4: see OpenClipRepository.create_attempt — atomic attempt_no allocation
        # with retry against the (task_id, attempt_no) unique index.
        last_error: IntegrityError | None = None
        for _ in range(10):
            try:
                with self.engine.begin() as conn:
                    current = conn.execute(
                        select(func.coalesce(func.max(oc_rebuild_attempts.c.attempt_no), 0)).where(
                            oc_rebuild_attempts.c.task_id == task_id
                        )
                    ).scalar_one()
                    attempt_no = int(current) + 1
                    result = conn.execute(
                        oc_rebuild_attempts.insert()
                        .values(task_id=task_id, attempt_no=attempt_no, **fields)
                        .returning(oc_rebuild_attempts.c.id)
                    )
                    attempt_id = int(result.scalar_one())
                return self.get_attempt(attempt_id) or {}
            except IntegrityError as exc:
                last_error = exc
        raise last_error if last_error is not None else RuntimeError("create_attempt failed")

    def get_attempt(self, attempt_id: int) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            return row_to_dict(conn.execute(select(oc_rebuild_attempts).where(oc_rebuild_attempts.c.id == attempt_id)).first())

    def list_attempts(self, task_id: int) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            return rows_to_dicts(conn.execute(select(oc_rebuild_attempts).where(oc_rebuild_attempts.c.task_id == task_id).order_by(desc(oc_rebuild_attempts.c.id))).fetchall())
