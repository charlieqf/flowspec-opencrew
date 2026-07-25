from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..db.schema import npc_skills, openflow_skills, publish_skills
from .base import Repository, row_to_dict


class SkillRepository(Repository):
    def __init__(self, engine: Any) -> None:
        super().__init__(engine)
        self.tables = {"npc": npc_skills, "publish": publish_skills, "openflow": openflow_skills}

    def get(self, domain: str, kind: str) -> dict[str, Any] | None:
        table = self.tables[domain]
        with self.engine.connect() as conn:
            return row_to_dict(conn.execute(select(table).where(table.c.kind == kind)).first())

    def upsert(self, domain: str, kind: str, title: str, content: str, updated_at: int) -> None:
        table = self.tables[domain]
        with self.engine.begin() as conn:
            conn.execute(
                pg_insert(table)
                .values(kind=kind, title=title, content=content, updated_at=updated_at)
                .on_conflict_do_update(
                    index_elements=[table.c.kind],
                    set_={"title": title, "content": content, "updated_at": updated_at},
                )
            )
