from __future__ import annotations

from typing import Any

from sqlalchemy import select, update

from ..db.schema import task_logs, task_runs
from .base import Repository, row_to_dict, rows_to_dicts


class TaskRepository(Repository):
    def create(self, kind: str, status: str, skill_snapshot: str, created_at: int, started_at: int | None = None) -> int:
        with self.engine.begin() as conn:
            result = conn.execute(
                task_runs.insert()
                .values(
                    kind=kind,
                    status=status,
                    skill_snapshot=skill_snapshot,
                    created_at=created_at,
                    started_at=started_at,
                )
                .returning(task_runs.c.id)
            )
            return int(result.scalar_one())

    def update(self, task_id: int, **fields: Any) -> None:
        if not fields:
            return
        with self.engine.begin() as conn:
            conn.execute(update(task_runs).where(task_runs.c.id == task_id).values(**fields))

    def get(self, task_id: int, kind: str | None = None) -> dict[str, Any] | None:
        statement = select(task_runs).where(task_runs.c.id == task_id)
        if kind is not None:
            statement = statement.where(task_runs.c.kind == kind)
        with self.engine.connect() as conn:
            return row_to_dict(conn.execute(statement).first())

    def add_log(self, task_id: int, phase: str, level: str, message: str, created_at: int) -> int:
        with self.engine.begin() as conn:
            result = conn.execute(
                task_logs.insert()
                .values(task_id=task_id, phase=phase, level=level, message=message, created_at=created_at)
                .returning(task_logs.c.id)
            )
            return int(result.scalar_one())

    def list_logs(self, task_id: int) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(task_logs).where(task_logs.c.task_id == task_id).order_by(task_logs.c.id.asc())).fetchall()
        return rows_to_dicts(rows)
