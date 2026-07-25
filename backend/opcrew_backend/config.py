from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_DATABASE_URL = "postgresql+psycopg://opencrew:opencrew@127.0.0.1:5433/opencrew"
DEFAULT_FRONTEND_URL = "http://127.0.0.1:18080/"
DEFAULT_BACKEND_URL = "http://127.0.0.1:8011"


@dataclass(frozen=True)
class AppConfig:
    data_dir: Path
    database_url: str
    frontend_url: str
    backend_url: str


def load_config(data_dir: Path | None = None) -> AppConfig:
    resolved_data_dir = Path(os.environ.get("OPENCREW_DATA_DIR") or data_dir or (Path.home() / ".opencrew"))
    resolved_data_dir.mkdir(parents=True, exist_ok=True)
    return AppConfig(
        data_dir=resolved_data_dir,
        database_url=os.environ.get("DATABASE_URL") or DEFAULT_DATABASE_URL,
        frontend_url=(os.environ.get("OPENCREW_FRONTEND_URL") or DEFAULT_FRONTEND_URL).rstrip("/") + "/",
        backend_url=(os.environ.get("OPENCREW_BACKEND_URL") or DEFAULT_BACKEND_URL).rstrip("/"),
    )
