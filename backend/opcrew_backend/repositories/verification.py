from __future__ import annotations

from typing import Any

from sqlalchemy import desc, select

from ..db.schema import message_logs, verification_runs
from .base import Repository, row_to_dict


class VerificationRepository(Repository):
    def add_message_log(
        self,
        source: str,
        external_id: str,
        sender: str,
        content: str,
        status: str,
        result: str,
        created_at: int,
    ) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                message_logs.insert().values(
                    source=source,
                    external_id=external_id,
                    sender=sender,
                    content=content,
                    status=status,
                    result=result,
                    created_at=created_at,
                )
            )

    def add_run(self, status: str, message: str, detail: str | None, created_at: int) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                verification_runs.insert().values(
                    status=status,
                    message=message,
                    detail=detail,
                    created_at=created_at,
                )
            )

    def latest(self) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            return row_to_dict(conn.execute(select(verification_runs).order_by(desc(verification_runs.c.id)).limit(1)).first())
