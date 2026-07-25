from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _data_dir() -> Path:
    return Path(os.environ.get("OPENCREW_DATA_DIR") or (Path.home() / ".opencrew"))


def _secret_store() -> Any | None:
    backend = _repo_root() / "backend"
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    try:
        from opcrew_backend.services.local_secrets import LocalSecretStore
        return LocalSecretStore(_data_dir())
    except Exception:
        return None


def resolve_secret_value(api_key_ref: str, legacy_value: str = "") -> str:
    ref = str(api_key_ref or "").strip()
    if not ref:
        return str(legacy_value or "").strip()
    store = _secret_store()
    try:
        stored = str(store.get(ref, "") or "").strip() if store is not None else ""
    except Exception:
        stored = ""
    return stored or str(os.environ.get(ref) or "").strip() or str(legacy_value or "").strip()
