from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy.engine import Engine, Row


def row_to_dict(row: Row[Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row._mapping)


def rows_to_dicts(rows: Sequence[Row[Any]]) -> list[dict[str, Any]]:
    return [dict(row._mapping) for row in rows]


class Repository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
