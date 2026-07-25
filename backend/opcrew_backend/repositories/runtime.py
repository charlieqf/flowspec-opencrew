from __future__ import annotations

from typing import Any

from sqlalchemy import select, update

from ..db.schema import npc_runtime, opencode_runtime, publish_runtime, tunnel_runtime, wecom_config, wecom_runtime
from .base import Repository, row_to_dict


class RuntimeRepository(Repository):
    def __init__(self, engine: Any) -> None:
        super().__init__(engine)
        self.tables = {
            "opencode": opencode_runtime,
            "tunnel": tunnel_runtime,
            "npc": npc_runtime,
            "publish": publish_runtime,
            "wecom": wecom_runtime,
        }

    def get_runtime(self, kind: str) -> dict[str, Any] | None:
        table = self.tables[kind]
        with self.engine.connect() as conn:
            return row_to_dict(conn.execute(select(table).where(table.c.id == 1)).first())

    def update_runtime(self, kind: str, **fields: Any) -> None:
        if not fields:
            return
        table = self.tables[kind]
        with self.engine.begin() as conn:
            conn.execute(update(table).where(table.c.id == 1).values(**fields))

    def get_wecom_config(self, include_secret: bool = True) -> dict[str, Any] | None:
        statement = select(wecom_config)
        if not include_secret:
            statement = select(
                wecom_config.c.id,
                wecom_config.c.corp_id,
                wecom_config.c.agent_id,
                wecom_config.c.token,
                wecom_config.c.enabled,
                wecom_config.c.updated_at,
            )
        with self.engine.connect() as conn:
            return row_to_dict(conn.execute(statement.where(wecom_config.c.id == 1)).first())

    def update_wecom_config(self, **fields: Any) -> None:
        with self.engine.begin() as conn:
            conn.execute(update(wecom_config).where(wecom_config.c.id == 1).values(**fields))
