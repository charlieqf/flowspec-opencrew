from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from opcrew_backend.context import now_ms

REPO_ROOT = Path(__file__).resolve().parents[4]
TEMPLATES_DIR = REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "hyperframe_templates"
APPLY_SCRIPT = TEMPLATES_DIR / "apply_template.py"


def _template_ids() -> set[str]:
    try:
        data = json.loads((TEMPLATES_DIR / "templates.json").read_text(encoding="utf-8"))
        return {str(tpl.get("id")) for tpl in data.get("templates", []) if tpl.get("id")}
    except Exception:
        return set()


def _run_render(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def register_hyperframe_template_routes(router: APIRouter, deps: Any) -> None:

    @router.post("/api/koubo-storyboard/tasks/{task_id}/hyperframe-template/apply")
    async def apply_hyperframe_template(task_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        workspace = deps.workspace_for(task)

        template_id = deps.text(payload.get("template_id"))
        if template_id not in _template_ids():
            raise HTTPException(status_code=400, detail="unknown template_id")

        video_path = deps.text(payload.get("video_path"))
        if not video_path:
            raise HTTPException(status_code=400, detail="missing video_path")
        try:
            _, source_abs = deps.safe_workspace_rel(workspace, video_path)
        except Exception:
            raise HTTPException(status_code=400, detail="invalid video_path")
        if not Path(source_abs).exists():
            raise HTTPException(status_code=404, detail="composed video not found")

        params = payload.get("params") if isinstance(payload.get("params"), dict) else {}

        stem = Path(source_abs).stem
        out_rel, output_abs = deps.safe_workspace_rel(workspace, f"SessionOutput/templated/{stem}_{template_id}_{now_ms()}.mp4")
        Path(output_abs).parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            sys.executable, str(APPLY_SCRIPT),
            "--template", template_id,
            "--video", str(source_abs),
            "--output", str(output_abs),
            "--params", json.dumps(params, ensure_ascii=False),
        ]
        completed = await asyncio.to_thread(_run_render, cmd)
        if completed.returncode != 0 or not Path(output_abs).exists() or Path(output_abs).stat().st_size <= 0:
            detail = (completed.stderr or completed.stdout or "render failed")[-800:]
            raise HTTPException(status_code=500, detail=f"template render failed: {detail}")

        deps.add_event(int(task["session_id"]), "koubo_storyboard.hyperframe_template.applied", {
            "task_id": task_id,
            "template_id": template_id,
            "source_video_path": video_path,
            "output_path": out_rel,
        })
        return {"status": "completed", "template_id": template_id, "video_path": out_rel}
