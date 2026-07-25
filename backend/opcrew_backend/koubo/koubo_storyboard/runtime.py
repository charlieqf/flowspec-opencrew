from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from opcrew_backend.context import AppContext, now_ms

from .constants import OPENCREW_ROOT


def media_binary_candidates(name: str) -> list[Path]:
    configured = os.environ.get(f"OPENCREW_{name.upper()}_PATH", "").strip()
    candidates = [Path(configured)] if configured else []
    candidates.extend([
        OPENCREW_ROOT / "ToolLibrary" / ".bin" / name,
        OPENCREW_ROOT / ".bin" / name,
        OPENCREW_ROOT / "ToolLibrary" / "vendor" / "static_ffmpeg" / "darwin_arm64" / name,
        OPENCREW_ROOT / "vendor" / "static_ffmpeg" / "darwin_arm64" / name,
    ])
    return candidates


def resolve_media_binary(name: str) -> str:
    for candidate in (*media_binary_candidates(name), shutil.which(name)):
        if not candidate:
            continue
        path = Path(candidate)
        if path.is_file():
            return str(path)
    return name


def analysis_tool_env() -> dict[str, str]:
    env = dict(os.environ)
    pythonpath = env.get("PYTHONPATH", "")
    paths = [str(OPENCREW_ROOT), str(OPENCREW_ROOT.parent)]
    if pythonpath:
        paths.append(pythonpath)
    env["PYTHONPATH"] = os.pathsep.join(paths)
    return env


class KouboStoryboardRuntime:
    def __init__(self, ctx: AppContext, repo: Any) -> None:
        self.ctx = ctx
        self.repo = repo

    def task_or_404(self, task_id: int) -> dict[str, Any]:
        task = self.repo.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Analysis V1 task not found")
        return task

    def workspace_for(self, task: dict[str, Any]) -> Path:
        value = str(task.get("workspace_dir") or "").strip()
        if value:
            return Path(value)
        session = self.ctx.session_repo.get(int(task["session_id"]))
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        return Path(str(session["workspace_dir"]))

    def safe_session(self, session_id: int) -> dict[str, Any]:
        row = self.ctx.session_repo.get(session_id)
        if not row:
            raise HTTPException(status_code=404, detail="Session not found")
        return row

    def add_event(self, session_id: int, kind: str, payload: dict[str, Any]) -> None:
        self.ctx.session_repo.add_event(session_id, kind, json.dumps(payload, ensure_ascii=True), now_ms())
