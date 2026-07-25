from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..db.schema import app_settings
from .base import Repository, row_to_dict


class SettingsRepository(Repository):
    def get_entry(self, key: str) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            return row_to_dict(conn.execute(select(app_settings).where(app_settings.c.key == key)).first())

    def get_json(self, key: str, default: Any = None) -> Any:
        row = self.get_entry(key)
        if not row:
            return default
        try:
            return json.loads(str(row["value"]))
        except Exception:
            return default

    def set_json(self, key: str, value: Any, updated_at: int) -> None:
        payload = json.dumps(value, ensure_ascii=True)
        with self.engine.begin() as conn:
            conn.execute(
                pg_insert(app_settings)
                .values(key=key, value=payload, updated_at=updated_at)
                .on_conflict_do_update(
                    index_elements=[app_settings.c.key],
                    set_={"value": payload, "updated_at": updated_at},
                )
            )

    def next_counter_title(self, key: str, updated_at: int) -> str:
        with self.engine.begin() as conn:
            row = conn.execute(select(app_settings).where(app_settings.c.key == key).with_for_update()).first()
            current = 1
            if row is not None:
                try:
                    current = int(json.loads(str(row._mapping["value"])))
                except Exception:
                    current = 1
                conn.execute(
                    update(app_settings)
                    .where(app_settings.c.key == key)
                    .values(value=json.dumps(current + 1, ensure_ascii=True), updated_at=updated_at)
                )
            else:
                conn.execute(
                    app_settings.insert().values(key=key, value=json.dumps(2, ensure_ascii=True), updated_at=updated_at)
                )
        return f"{current:02d}" if current < 100 else str(current)
