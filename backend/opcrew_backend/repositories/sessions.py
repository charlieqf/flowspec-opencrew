from __future__ import annotations

import json
from typing import Any

from sqlalchemy import case, delete, desc, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..db.schema import session_events, session_files, session_shares, sessions
from ..services.session_events import infer_event_defaults, serialize_payload
from .base import Repository, row_to_dict, rows_to_dicts


class SessionRepository(Repository):
    def get(self, session_id: int) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            return row_to_dict(conn.execute(select(sessions).where(sessions.c.id == session_id)).first())

    def exists(self, session_id: int) -> bool:
        return self.get(session_id) is not None

    def create(self, **fields: Any) -> int:
        with self.engine.begin() as conn:
            result = conn.execute(sessions.insert().values(**fields).returning(sessions.c.id))
            return int(result.scalar_one())

    def update(self, session_id: int, **fields: Any) -> None:
        if not fields:
            return
        with self.engine.begin() as conn:
            conn.execute(update(sessions).where(sessions.c.id == session_id).values(**fields))

    def claim_for_rerun(
        self,
        session_id: int,
        *,
        busy_statuses: tuple[str, ...],
        claim_status: str,
        updated_at: int,
    ) -> bool:
        # M4: atomic compare-and-set. Flip status to a claim sentinel only when the
        # session is not already busy, so two concurrent rerun requests cannot both
        # pass the guard and clobber each other's workspace. The conditional UPDATE
        # takes a row lock; exactly one racer gets rowcount == 1 and wins. The
        # claim_status MUST be in busy_statuses so the loser sees a busy row.
        with self.engine.begin() as conn:
            result = conn.execute(
                update(sessions)
                .where(sessions.c.id == session_id, sessions.c.status.notin_(busy_statuses))
                .values(status=claim_status, updated_at=updated_at)
            )
            return int(result.rowcount or 0) == 1

    def delete(self, session_id: int) -> None:
        with self.engine.begin() as conn:
            conn.execute(delete(session_shares).where(session_shares.c.session_id == session_id))
            conn.execute(delete(session_files).where(session_files.c.session_id == session_id))
            conn.execute(delete(session_events).where(session_events.c.session_id == session_id))
            conn.execute(delete(sessions).where(sessions.c.id == session_id))

    def delete_events_for_session(self, session_id: int) -> None:
        with self.engine.begin() as conn:
            conn.execute(delete(session_events).where(session_events.c.session_id == session_id))

    def delete_files_for_session(self, session_id: int) -> None:
        with self.engine.begin() as conn:
            conn.execute(delete(session_files).where(session_files.c.session_id == session_id))

    def list(self, group_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        statement = select(sessions)
        if group_id:
            statement = statement.where(sessions.c.group_id == group_id)
        statement = statement.order_by(desc(sessions.c.updated_at), desc(sessions.c.id)).limit(limit)
        with self.engine.connect() as conn:
            return rows_to_dicts(conn.execute(statement).fetchall())

    def list_recent_group(self, group_id: str, limit: int = 50) -> list[dict[str, Any]]:
        return self.list(group_id=group_id, limit=limit)

    def sessions_summary(self) -> dict[str, Any]:
        statement = select(
            func.count().label("count"),
            func.coalesce(func.sum(case((sessions.c.status == "running", 1), else_=0)), 0).label("running"),
        )
        with self.engine.connect() as conn:
            row = conn.execute(statement).first()
        return row_to_dict(row) or {"count": 0, "running": 0}

    def add_event(
        self,
        session_id: int,
        kind: str,
        payload: Any,
        created_at: int,
        *,
        visibility: str | None = None,
        event_scope: str | None = None,
        severity: str | None = None,
        family: str | None = None,
        workflow_id: str | None = None,
        task_id: int | None = None,
        attempt_id: int | None = None,
        tool_id: str | None = None,
        step_id: str | None = None,
    ) -> int:
        defaults = infer_event_defaults(kind)
        if isinstance(payload, str):
            try:
                payload_value = serialize_payload(json.loads(payload or "{}"))
            except Exception:
                payload_value = serialize_payload({"value": payload})
        else:
            payload_value = serialize_payload(payload or {})
        with self.engine.begin() as conn:
            result = conn.execute(
                session_events.insert()
                .values(
                    session_id=session_id,
                    kind=kind,
                    payload=payload_value,
                    visibility=visibility or defaults["visibility"],
                    event_scope=event_scope or defaults["event_scope"],
                    severity=severity or defaults["severity"],
                    family=family or defaults["family"],
                    workflow_id=workflow_id,
                    task_id=task_id,
                    attempt_id=attempt_id,
                    tool_id=tool_id,
                    step_id=step_id,
                    created_at=created_at,
                )
                .returning(session_events.c.id)
            )
            return int(result.scalar_one())

    def list_events(self, session_id: int, since: int = 0, limit: int = 500) -> list[dict[str, Any]]:
        statement = (
            select(session_events)
            .where(session_events.c.session_id == session_id, session_events.c.id > since)
            .order_by(session_events.c.id.desc())
            .limit(limit)
        )
        with self.engine.connect() as conn:
            rows = rows_to_dicts(conn.execute(statement).fetchall())
        rows.reverse()
        return rows

    def stream_events(self, since: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        statement = select(session_events).where(session_events.c.id > since).order_by(session_events.c.id.asc()).limit(limit)
        with self.engine.connect() as conn:
            return rows_to_dicts(conn.execute(statement).fetchall())

    def stream_session_events(self, session_id: int, since: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        statement = (
            select(session_events)
            .where(session_events.c.session_id == session_id, session_events.c.id > since)
            .order_by(session_events.c.id.asc())
            .limit(limit)
        )
        with self.engine.connect() as conn:
            return rows_to_dicts(conn.execute(statement).fetchall())

    def count_events(self, session_id: int) -> int:
        with self.engine.connect() as conn:
            return int(
                conn.execute(select(func.count()).select_from(session_events).where(session_events.c.session_id == session_id)).scalar_one()
            )

    def count_events_for_sessions(self, session_ids: list[int]) -> dict[int, int]:
        if not session_ids:
            return {}
        statement = (
            select(session_events.c.session_id, func.count().label("count"))
            .where(session_events.c.session_id.in_(session_ids))
            .group_by(session_events.c.session_id)
        )
        with self.engine.connect() as conn:
            rows = conn.execute(statement).fetchall()
        return {int(row._mapping["session_id"]): int(row._mapping["count"]) for row in rows}

    def get_share(self, token: str) -> dict[str, Any] | None:
        statement = (
            select(
                session_shares,
                sessions.c.title,
                sessions.c.workspace_dir,
            )
            .join(sessions, sessions.c.id == session_shares.c.session_id)
            .where(session_shares.c.token == token)
        )
        with self.engine.connect() as conn:
            return row_to_dict(conn.execute(statement).first())

    def upsert_share(self, session_id: int, token: str, scope: str, expires_at: int, created_at: int) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                pg_insert(session_shares)
                .values(session_id=session_id, token=token, scope=scope, expires_at=expires_at, created_at=created_at)
                .on_conflict_do_update(
                    index_elements=[session_shares.c.token],
                    set_={"scope": scope, "expires_at": expires_at},
                )
            )

    def upsert_file(
        self,
        session_id: int,
        path: str,
        kind: str,
        size: int,
        origin: str,
        downloadable: int,
        updated_at: int,
        *,
        visibility: str | None = None,
        sensitivity: str | None = None,
        attempt_id: int | None = None,
        tool_use_session_id: str | None = None,
        stale: int = 0,
    ) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                pg_insert(session_files)
                .values(
                    session_id=session_id,
                    path=path,
                    kind=kind,
                    size=size,
                    origin=origin,
                    downloadable=downloadable,
                    visibility=visibility,
                    sensitivity=sensitivity,
                    attempt_id=attempt_id,
                    tool_use_session_id=tool_use_session_id,
                    stale=stale,
                    updated_at=updated_at,
                )
                .on_conflict_do_update(
                    index_elements=[session_files.c.session_id, session_files.c.path],
                    set_={
                        "kind": kind,
                        "size": size,
                        "origin": origin,
                        "downloadable": downloadable,
                        "visibility": visibility,
                        "sensitivity": sensitivity,
                        "attempt_id": attempt_id,
                        "tool_use_session_id": tool_use_session_id,
                        "stale": stale,
                        "updated_at": updated_at,
                    },
                )
            )

    def list_files(self, session_id: int) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(session_files)
                .where(session_files.c.session_id == session_id)
                .order_by(session_files.c.path.asc())
            ).fetchall()
        return rows_to_dicts(rows)

    def get_file(self, session_id: int, path: str) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            return row_to_dict(
                conn.execute(
                    select(session_files).where(
                        session_files.c.session_id == session_id,
                        session_files.c.path == path,
                    )
                ).first()
            )
