from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import HTTPException


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def safe_workspace_rel(workspace: Path, value: str) -> tuple[str, Path]:
    rel_value = str(value or "").strip().lstrip("/")
    if not rel_value or Path(rel_value).is_absolute() or ".." in Path(rel_value).parts:
        raise HTTPException(status_code=400, detail="StoryBoard asset path must stay inside the task workspace")
    target = workspace / rel_value
    try:
        target.resolve().relative_to(workspace.resolve())
    except Exception as exc:
        raise HTTPException(status_code=400, detail="StoryBoard asset path must stay inside the task workspace") from exc
    return rel_value, target
