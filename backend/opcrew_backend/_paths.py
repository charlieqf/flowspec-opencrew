from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_CONFIG_BACKEND = REPO_ROOT / "ModelConfig" / "backend"


def ensure_sys_path(path: Path) -> Path:
    value = str(path)
    if value not in sys.path:
        sys.path.insert(0, value)
    return path


def ensure_model_config_backend_path() -> Path:
    return ensure_sys_path(MODEL_CONFIG_BACKEND)


def ensure_repo_root_path() -> Path:
    return ensure_sys_path(REPO_ROOT)
