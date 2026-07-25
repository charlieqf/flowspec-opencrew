from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import urllib.parse
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse


def register_clean_image_routes(router: APIRouter, deps: Any) -> None:

    def generation_image_url(task_id: int, generation_id: str) -> str:
        return f"/api/koubo-storyboard/tasks/{task_id}/clean-image/{generation_id}/image"

    def reference_image_url(task_id: int, reference_id: str) -> str:
        return f"/api/koubo-storyboard/tasks/{task_id}/clean-image/references/{urllib.parse.quote(reference_id, safe='')}/image"

    def with_image_url(task_id: int, generation: dict[str, Any]) -> dict[str, Any]:
        generation_id = deps.text(generation.get("generation_id"))
        return {**generation, "image_url": generation_image_url(task_id, generation_id)}

    def with_reference_image_url(task_id: int, item: dict[str, Any]) -> dict[str, Any]:
        filename = Path(deps.text(item.get("path"))).name
        return {**item, "image_url": reference_image_url(task_id, filename)}

    def clean_image_event_payload(task: dict[str, Any], generation: dict[str, Any]) -> dict[str, Any]:
        effective_prompt = deps.clean_image_effective_prompt(deps.text(generation.get("prompt")), deps.text(generation.get("negative_prompt")), sc=deps)
        prompt_hash = hashlib.sha256(effective_prompt.encode("utf-8")).hexdigest()
        return {
            "task_id": int(task["id"]),
            "session_id": int(task["session_id"]),
            "generation_id": deps.text(generation.get("generation_id")),
            "provider": deps.text(generation.get("provider")),
            "model": deps.text(generation.get("model")),
            "effective_size": deps.text(generation.get("effective_size")),
            "prompt_hash": f"sha256:{prompt_hash}",
            "reference_count": len(generation.get("reference_paths") or []),
            "output_path": deps.text(generation.get("output_path")),
        }

    @router.post("/api/koubo-storyboard/tasks/{task_id}/clean-image/generate")
    async def generate_clean_image_route(task_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        generation = await asyncio.to_thread(deps.generate_clean_image, task, payload, sc=deps)
        deps.add_event(int(task["session_id"]), "koubo_storyboard.clean_image.generated", clean_image_event_payload(task, generation))
        return {"ok": True, "generation": with_image_url(task_id, generation)}

    @router.get("/api/koubo-storyboard/tasks/{task_id}/clean-image/generations")
    async def list_clean_image_generations_route(task_id: int) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        workspace = deps.workspace_for(task)
        items = [with_image_url(task_id, item) for item in deps.list_clean_image_generations(workspace, sc=deps)]
        return {"ok": True, "items": items}

    @router.post("/api/koubo-storyboard/tasks/{task_id}/clean-image/references")
    async def upload_clean_image_references_route(task_id: int, files: list[UploadFile] = File(default=[])) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        if len(files or []) > 8:
            raise HTTPException(status_code=400, detail={"message": "Clean image supports at most 8 reference images", "limit": 8})
        workspace = deps.workspace_for(task)
        items: list[dict[str, Any]] = []
        for index, upload in enumerate(files or [], start=1):
            content = await upload.read()
            if not content:
                continue
            item = deps.save_clean_image_reference_bytes(workspace, upload.filename or "", content, index, upload.content_type or "", sc=deps)
            items.append(with_reference_image_url(task_id, item))
        deps.add_event(int(task["session_id"]), "koubo_storyboard.clean_image.references.uploaded", {
            "task_id": task_id,
            "session_id": int(task["session_id"]),
            "count": len(items),
            "reference_paths": [deps.text(item.get("path")) for item in items],
        })
        return {"ok": True, "items": items}

    @router.get("/api/koubo-storyboard/tasks/{task_id}/clean-image/{generation_id}/image")
    async def clean_image_preview_route(task_id: int, generation_id: str) -> FileResponse:
        task = deps.task_or_404(task_id)
        path = deps.clean_image_output_path(deps.workspace_for(task), generation_id, sc=deps)
        media_type = mimetypes.guess_type(path.name)[0] or "image/png"
        return FileResponse(path, media_type=media_type, filename=path.name)

    @router.get("/api/koubo-storyboard/tasks/{task_id}/clean-image/references/{reference_id}/image")
    async def clean_image_reference_preview_route(task_id: int, reference_id: str) -> FileResponse:
        task = deps.task_or_404(task_id)
        path = deps.clean_image_reference_path(deps.workspace_for(task), reference_id, sc=deps)
        media_type = mimetypes.guess_type(path.name)[0] or "image/png"
        return FileResponse(path, media_type=media_type, filename=path.name)

    @router.post("/api/koubo-storyboard/tasks/{task_id}/clean-image/{generation_id}/promote/asset-library")
    async def promote_clean_image_asset_library_route(task_id: int, generation_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        result = deps.promote_clean_image_to_asset_library(task, generation_id, payload or {}, sc=deps)
        generation = with_image_url(task_id, result["generation"])
        deps.add_event(int(task["session_id"]), "koubo_storyboard.clean_image.promoted", {
            "task_id": task_id,
            "session_id": int(task["session_id"]),
            "generation_id": generation_id,
            "target": "asset_library",
            "target_path": deps.text(result.get("asset", {}).get("path")),
        })
        return {"ok": True, **result, "generation": generation}

    @router.post("/api/koubo-storyboard/tasks/{task_id}/clean-image/{generation_id}/promote/dialogue-image")
    async def promote_clean_image_dialogue_route(task_id: int, generation_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        result = deps.promote_clean_image_to_dialogue(task, generation_id, payload or {}, sc=deps)
        generation = with_image_url(task_id, result["generation"])
        deps.add_event(int(task["session_id"]), "koubo_storyboard.clean_image.promoted", {
            "task_id": task_id,
            "session_id": int(task["session_id"]),
            "generation_id": generation_id,
            "target": "dialogue_image",
            "dialogue_id": deps.text(result.get("dialogue_id")),
            "target_path": deps.text(result.get("working_path")),
        })
        return {"ok": True, **result, "generation": generation}

    @router.post("/api/koubo-storyboard/tasks/{task_id}/clean-image/{generation_id}/promote/consistency")
    async def promote_clean_image_consistency_route(task_id: int, generation_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        task = deps.task_or_404(task_id)
        result = deps.promote_clean_image_to_consistency(task, generation_id, payload or {}, sc=deps)
        generation = with_image_url(task_id, result["generation"])
        deps.add_event(int(task["session_id"]), "koubo_storyboard.clean_image.promoted", {
            "task_id": task_id,
            "session_id": int(task["session_id"]),
            "generation_id": generation_id,
            "target": "consistency",
            "kind": deps.text(result.get("kind")),
            "target_path": deps.text((result.get("section") or {}).get("output")),
        })
        return {"ok": True, **result, "generation": generation}
