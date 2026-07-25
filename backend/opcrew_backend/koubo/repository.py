from __future__ import annotations

from typing import Any

from sqlalchemy import delete, desc, func, inspect, select, text, update
from sqlalchemy.exc import IntegrityError

from opcrew_backend.db.schema import (
    openclip_attempts,
    openclip_prompt_versions,
    openclip_skill_versions,
    openclip_tasks,
    session_events,
    session_files,
    sessions,
    talking_head_task_configs,
)
from opcrew_backend.repositories.base import Repository, row_to_dict, rows_to_dicts


class OpenClipRepository(Repository):
    def create_task(self, **fields: Any) -> int:
        with self.engine.begin() as conn:
            result = conn.execute(openclip_tasks.insert().values(**fields).returning(openclip_tasks.c.id))
            return int(result.scalar_one())

    def create_talking_head_task(
        self,
        *,
        config_schema_version: str,
        script_creation_mode: str,
        config_json: str,
        **task_fields: Any,
    ) -> int:
        with self.engine.begin() as conn:
            result = conn.execute(openclip_tasks.insert().values(**task_fields).returning(openclip_tasks.c.id))
            task_id = int(result.scalar_one())
            created_at = int(task_fields.get("created_at") or 0)
            updated_at = int(task_fields.get("updated_at") or created_at)
            conn.execute(talking_head_task_configs.insert().values(
                task_id=task_id,
                schema_version=config_schema_version,
                script_creation_mode=script_creation_mode,
                config_json=config_json,
                created_at=created_at,
                updated_at=updated_at,
            ))
            return task_id

    def get_task(self, task_id: int) -> dict[str, Any] | None:
        statement = select(openclip_tasks, sessions.c.title, sessions.c.sender_name, sessions.c.opencode_session_id, sessions.c.workspace_dir, sessions.c.status.label("session_status")).join(
            sessions, sessions.c.id == openclip_tasks.c.session_id
        ).where(openclip_tasks.c.id == task_id)
        with self.engine.connect() as conn:
            return row_to_dict(conn.execute(statement).first())

    def get_task_by_session(self, session_id: int) -> dict[str, Any] | None:
        statement = select(openclip_tasks).where(openclip_tasks.c.session_id == session_id)
        with self.engine.connect() as conn:
            return row_to_dict(conn.execute(statement).first())

    def list_tasks(self) -> list[dict[str, Any]]:
        statement = select(openclip_tasks, sessions.c.title, sessions.c.sender_name, sessions.c.opencode_session_id, sessions.c.workspace_dir).join(
            sessions, sessions.c.id == openclip_tasks.c.session_id
        ).order_by(desc(openclip_tasks.c.updated_at), desc(openclip_tasks.c.id))
        with self.engine.connect() as conn:
            return rows_to_dicts(conn.execute(statement).fetchall())

    def list_task_summaries(self) -> list[dict[str, Any]]:
        statement = select(
            openclip_tasks.c.id,
            openclip_tasks.c.session_id,
            openclip_tasks.c.status,
            openclip_tasks.c.workflow_mode,
            openclip_tasks.c.reference_video_path,
            openclip_tasks.c.industry,
            openclip_tasks.c.persona,
            openclip_tasks.c.target_audience,
            openclip_tasks.c.analysis_goal,
            openclip_tasks.c.video_formula,
            openclip_tasks.c.latest_attempt_id,
            openclip_tasks.c.run_model_provider,
            openclip_tasks.c.run_model_id,
            openclip_tasks.c.created_at,
            openclip_tasks.c.updated_at,
            sessions.c.title,
            sessions.c.sender_name,
            sessions.c.opencode_session_id,
            sessions.c.workspace_dir,
        ).join(
            sessions, sessions.c.id == openclip_tasks.c.session_id
        ).order_by(desc(openclip_tasks.c.updated_at), desc(openclip_tasks.c.id))
        with self.engine.connect() as conn:
            return rows_to_dicts(conn.execute(statement).fetchall())

    def update_task(self, task_id: int, **fields: Any) -> None:
        if not fields:
            return
        with self.engine.begin() as conn:
            conn.execute(update(openclip_tasks).where(openclip_tasks.c.id == task_id).values(**fields))

    def get_talking_head_config(self, task_id: int) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            return row_to_dict(conn.execute(select(talking_head_task_configs).where(talking_head_task_configs.c.task_id == task_id)).first())

    def upsert_talking_head_config(
        self,
        task_id: int,
        *,
        schema_version: str,
        script_creation_mode: str,
        config_json: str,
        created_at: int,
        updated_at: int,
    ) -> None:
        with self.engine.begin() as conn:
            current = conn.execute(select(talking_head_task_configs.c.task_id).where(talking_head_task_configs.c.task_id == task_id)).first()
            values = {
                "schema_version": schema_version,
                "script_creation_mode": script_creation_mode,
                "config_json": config_json,
                "updated_at": updated_at,
            }
            if current:
                conn.execute(update(talking_head_task_configs).where(talking_head_task_configs.c.task_id == task_id).values(**values))
            else:
                conn.execute(talking_head_task_configs.insert().values(task_id=task_id, created_at=created_at, **values))

    def update_talking_head_task(
        self,
        task_id: int,
        *,
        config_schema_version: str,
        script_creation_mode: str,
        config_json: str,
        config_created_at: int,
        config_updated_at: int,
        **task_fields: Any,
    ) -> None:
        with self.engine.begin() as conn:
            if task_fields:
                conn.execute(update(openclip_tasks).where(openclip_tasks.c.id == task_id).values(**task_fields))
            current = conn.execute(select(talking_head_task_configs.c.task_id).where(talking_head_task_configs.c.task_id == task_id)).first()
            values = {
                "schema_version": config_schema_version,
                "script_creation_mode": script_creation_mode,
                "config_json": config_json,
                "updated_at": config_updated_at,
            }
            if current:
                conn.execute(update(talking_head_task_configs).where(talking_head_task_configs.c.task_id == task_id).values(**values))
            else:
                conn.execute(talking_head_task_configs.insert().values(task_id=task_id, created_at=config_created_at, **values))

    def delete_task(self, task_id: int) -> None:
        with self.engine.begin() as conn:
            if inspect(conn).has_table("talking_head_task_configs"):
                conn.execute(delete(talking_head_task_configs).where(talking_head_task_configs.c.task_id == task_id))
            if inspect(conn).has_table("openclip_param_versions"):
                conn.execute(text("DELETE FROM openclip_param_versions WHERE task_id = :task_id"), {"task_id": task_id})
            conn.execute(delete(openclip_attempts).where(openclip_attempts.c.task_id == task_id))
            conn.execute(delete(openclip_skill_versions).where(openclip_skill_versions.c.task_id == task_id))
            conn.execute(delete(openclip_prompt_versions).where(openclip_prompt_versions.c.task_id == task_id))
            conn.execute(delete(openclip_tasks).where(openclip_tasks.c.id == task_id))

    def create_prompt_version(self, **fields: Any) -> dict[str, Any]:
        with self.engine.begin() as conn:
            result = conn.execute(openclip_prompt_versions.insert().values(**fields).returning(openclip_prompt_versions.c.id))
            version_id = int(result.scalar_one())
        return self.get_prompt_version(version_id) or {}

    def get_prompt_version(self, version_id: int) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            return row_to_dict(conn.execute(select(openclip_prompt_versions).where(openclip_prompt_versions.c.id == version_id)).first())

    def update_prompt_version(self, version_id: int, **fields: Any) -> dict[str, Any]:
        with self.engine.begin() as conn:
            conn.execute(update(openclip_prompt_versions).where(openclip_prompt_versions.c.id == version_id).values(**fields))
        return self.get_prompt_version(version_id) or {}

    def list_prompt_versions(self, task_id: int) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            return rows_to_dicts(conn.execute(select(openclip_prompt_versions).where(openclip_prompt_versions.c.task_id == task_id).order_by(desc(openclip_prompt_versions.c.id))).fetchall())

    def delete_prompt_version(self, task_id: int, version_id: int) -> None:
        with self.engine.begin() as conn:
            conn.execute(update(openclip_attempts).where(openclip_attempts.c.task_id == task_id, openclip_attempts.c.prompt_version_id == version_id).values(prompt_version_id=None))
            conn.execute(update(openclip_skill_versions).where(openclip_skill_versions.c.task_id == task_id, openclip_skill_versions.c.prompt_version_id == version_id).values(prompt_version_id=None))
            conn.execute(delete(openclip_prompt_versions).where(openclip_prompt_versions.c.task_id == task_id, openclip_prompt_versions.c.id == version_id))

    def create_skill_version(self, **fields: Any) -> dict[str, Any]:
        with self.engine.begin() as conn:
            result = conn.execute(openclip_skill_versions.insert().values(**fields).returning(openclip_skill_versions.c.id))
            version_id = int(result.scalar_one())
        return self.get_skill_version(version_id) or {}

    def get_skill_version(self, version_id: int) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            return row_to_dict(conn.execute(select(openclip_skill_versions).where(openclip_skill_versions.c.id == version_id)).first())

    def list_skill_versions(self, task_id: int) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            return rows_to_dicts(conn.execute(select(openclip_skill_versions).where(openclip_skill_versions.c.task_id == task_id).order_by(desc(openclip_skill_versions.c.id))).fetchall())

    def delete_skill_version(self, task_id: int, version_id: int) -> None:
        with self.engine.begin() as conn:
            conn.execute(update(openclip_attempts).where(openclip_attempts.c.task_id == task_id, openclip_attempts.c.skill_version_id == version_id).values(skill_version_id=None))
            conn.execute(delete(openclip_skill_versions).where(openclip_skill_versions.c.task_id == task_id, openclip_skill_versions.c.id == version_id))

    def create_attempt(self, *, task_id: int, **fields: Any) -> dict[str, Any]:
        # M4: allocate attempt_no and insert in one transaction, retrying on the
        # (task_id, attempt_no) unique violation so concurrent reruns can never
        # produce duplicate attempt numbers. The unique index guarantees only one
        # racer wins; the loser recomputes max()+1 and retries.
        last_error: IntegrityError | None = None
        for _ in range(10):
            try:
                with self.engine.begin() as conn:
                    current = conn.execute(
                        select(func.coalesce(func.max(openclip_attempts.c.attempt_no), 0)).where(
                            openclip_attempts.c.task_id == task_id
                        )
                    ).scalar_one()
                    attempt_no = int(current) + 1
                    result = conn.execute(
                        openclip_attempts.insert()
                        .values(task_id=task_id, attempt_no=attempt_no, **fields)
                        .returning(openclip_attempts.c.id)
                    )
                    attempt_id = int(result.scalar_one())
                return self.get_attempt(attempt_id) or {}
            except IntegrityError as exc:
                last_error = exc
        raise last_error if last_error is not None else RuntimeError("create_attempt failed")

    def get_attempt(self, attempt_id: int) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            return row_to_dict(conn.execute(select(openclip_attempts).where(openclip_attempts.c.id == attempt_id)).first())

    def list_attempts(self, task_id: int) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            return rows_to_dicts(conn.execute(select(openclip_attempts).where(openclip_attempts.c.task_id == task_id).order_by(desc(openclip_attempts.c.id))).fetchall())

    def update_attempt(self, attempt_id: int, **fields: Any) -> None:
        if not fields:
            return
        with self.engine.begin() as conn:
            conn.execute(update(openclip_attempts).where(openclip_attempts.c.id == attempt_id).values(**fields))

    def delete_session_data(self, session_id: int) -> None:
        with self.engine.begin() as conn:
            conn.execute(delete(session_files).where(session_files.c.session_id == session_id))
            conn.execute(delete(session_events).where(session_events.c.session_id == session_id))
