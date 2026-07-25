from __future__ import annotations

from typing import Any

from sqlalchemy import desc, select

from ..db.schema import event_logs
from .base import Repository, rows_to_dicts


class EventLogRepository(Repository):
    def add(self, level: str, category: str, message: str, payload: str | None, created_at: int) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                event_logs.insert().values(
                    level=level,
                    category=category,
                    message=message,
                    payload=payload,
                    created_at=created_at,
                )
            )

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(event_logs).order_by(desc(event_logs.c.id)).limit(limit)).fetchall()
        return rows_to_dicts(rows)
