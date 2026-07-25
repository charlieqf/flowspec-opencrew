from __future__ import annotations

import os
from pathlib import Path


def opencrew_data_dir() -> Path:
    return Path(os.environ.get("OPENCREW_DATA_DIR", str(Path.home() / ".opencrew"))).expanduser()


def opencrew_session_workspace(session_id: int | str) -> Path:
    return (opencrew_data_dir() / "sessions" / str(int(session_id)) / "workspace").resolve()
