from __future__ import annotations

import asyncio
import json
import time
import urllib.parse
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse

from opcrew_backend.context import now_ms
from opcrew_backend.model_policy import SURFACE_KOUBO_HOST_PRODUCT_PROMPT, mask_model_fields_for_role, request_role

from .constants import *


def register_host_product_routes(router: APIRouter, deps: Any) -> None:

    @router.get("/api/koubo-storyboard/tasks/{task_id}/host-product-builder")
    async def get_host_product_builder(request: Request, task_id: int) -> dict[str, Any]:
        return deps.serialize_host_product_builder(deps.task_or_404(task_id), request_role(request), sc=deps)

    @router.post("/api/koubo-storyboard/tasks/{task_id}/host-product-builder/uploads")
    async def upload_host_product_builder_refs(request: Request, task_id: int, kind: str = Form(...), files: list[UploadFile] = File(...)) -> dict[str, Any]:
        role = request_role(request)
        task = deps.task_or_404(task_id)
        workspace = deps.workspace_for(task)
        normalized_kind = deps.builder_kind_dir(kind)
        upload_dir = deps.builder_root(workspace)
        upload_dir.mkdir(parents=True, exist_ok=True)
        saved: list[dict[str, str]] = []
        for index, upload in enumerate(files, start=1):
            content = await upload.read()
            if not content:
                continue
            target = upload_dir / deps.builder_upload_name(normalized_kind, upload.filename or "", "reference", index)
            target.write_bytes(content)
            saved.append({"path": deps.builder_rel(workspace, target), "filename": upload.filename or target.name, "content_type": upload.content_type or ""})
        current = deps.read_builder_section(workspace, normalized_kind, sc=deps)
        existing = [str(item) for item in current.get("reference_images") or [] if str(item).strip()]
        refs = [*existing, *[item["path"] for item in saved]]
        section = deps.write_builder_section(workspace, normalized_kind, {"reference_images": refs}, sc=deps)
        deps.add_event(int(task["session_id"]), "koubo_storyboard.host_product_builder.uploaded", {"task_id": task_id, "session_id": int(task["session_id"]), "kind": normalized_kind, "count": len(saved), "reference_images": refs})
        return mask_model_fields_for_role(deps.ctx, role, SURFACE_KOUBO_HOST_PRODUCT_PROMPT, {"ok": True, "kind": normalized_kind, "items": saved, "section": section, "state": deps.serialize_host_product_builder(task, role, sc=deps)})

    @router.delete("/api/koubo-storyboard/tasks/{task_id}/host-product-builder/reference")
    async def delete_host_product_builder_ref(request: Request, task_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        role = request_role(request)
        task = deps.task_or_404(task_id)
        workspace = deps.workspace_for(task)
        kind = deps.builder_kind_dir(deps.text(payload.get("kind")))
        target_rel = deps.text(payload.get("path"))
        current = deps.read_builder_section(workspace, kind, sc=deps)
        existing = [str(item) for item in current.get("reference_images") or [] if str(item).strip()]
        refs = [item for item in existing if item != target_rel]
        if target_rel:
            target = deps.resolve_workspace_rel_for_write(workspace, target_rel, sc=deps)
            is_new_upload = target.parent.resolve() == deps.builder_root(workspace).resolve() and target.name.startswith(f"{kind}_upload_")
            is_legacy_upload = "consistency_references" in target.parts and "uploads" in target.parts
            if target.exists() and target.is_file() and (is_new_upload or is_legacy_upload):
                target.unlink()
        section = deps.write_builder_section(workspace, kind, {"reference_images": refs}, sc=deps)
        deps.add_event(int(task["session_id"]), "koubo_storyboard.host_product_builder.reference.deleted", {"task_id": task_id, "session_id": int(task["session_id"]), "kind": kind, "path": target_rel, "reference_images": refs})
        return mask_model_fields_for_role(deps.ctx, role, SURFACE_KOUBO_HOST_PRODUCT_PROMPT, {"ok": True, "kind": kind, "section": section, "state": deps.serialize_host_product_builder(task, role, sc=deps)})

    @router.post("/api/koubo-storyboard/tasks/{task_id}/host-product-builder/output")
    async def upload_host_product_builder_output(request: Request, task_id: int, kind: str = Form(...), file: UploadFile = File(...)) -> dict[str, Any]:
        role = request_role(request)
        task = deps.task_or_404(task_id)
        workspace = deps.workspace_for(task)
        normalized_kind = deps.builder_kind_dir(kind)
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Output image is empty")
        output_path = deps.final_output_path_for_write(workspace, normalized_kind, deps.builder_image_suffix(file.filename or ""), sc=deps)
        output_path.write_bytes(content)
        output_rel = deps.builder_rel(workspace, output_path)
        section = deps.write_builder_section(workspace, normalized_kind, {
            "output": output_rel,
            "output_path": str(output_path),
            "uploaded_output_filename": file.filename or output_path.name,
            "uploaded_output_content_type": file.content_type or "",
            "uploaded_at": now_ms(),
        }, sc=deps)
        config = deps.read_builder_config(workspace, sc=deps)
        active = config.get("active") if isinstance(config.get("active"), dict) else {}
        active["host_reference" if normalized_kind == "host" else "product_reference"] = output_rel
        config.update({"task_id": task_id, "session_id": int(task["session_id"]), "active": active, "updated_at": now_ms()})
        deps.write_json(deps.builder_config_path(workspace), config)
        deps.add_event(int(task["session_id"]), "koubo_storyboard.host_product_builder.output.uploaded", {"task_id": task_id, "session_id": int(task["session_id"]), "kind": normalized_kind, "output": output_rel})
        return mask_model_fields_for_role(deps.ctx, role, SURFACE_KOUBO_HOST_PRODUCT_PROMPT, {"ok": True, "kind": normalized_kind, "output": output_rel, "section": section, "state": deps.serialize_host_product_builder(task, role, sc=deps)})

    @router.delete("/api/koubo-storyboard/tasks/{task_id}/host-product-builder/output")
    async def delete_host_product_builder_output(request: Request, task_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        role = request_role(request)
        task = deps.task_or_404(task_id)
        workspace = deps.workspace_for(task)
        kind = deps.builder_kind_dir(deps.text(payload.get("kind")))
        output_rel = deps.text(payload.get("path"))
        if output_rel:
            target = deps.resolve_workspace_rel_for_write(workspace, output_rel, sc=deps)
        else:
            target = deps.latest_final_output_path(workspace, kind, sc=deps) or (deps.builder_root(workspace) / deps.builder_output_name(kind))
            output_rel = deps.builder_rel(workspace, target)
        if target.exists() and target.is_file():
            target.unlink()
        section = deps.write_builder_section(workspace, kind, {"output": "", "output_path": ""}, sc=deps)
        config = deps.read_builder_config(workspace, sc=deps)
        active = config.get("active") if isinstance(config.get("active"), dict) else {}
        active.pop("host_reference" if kind == "host" else "product_reference", None)
        config.update({"active": active, "updated_at": now_ms()})
        deps.write_json(deps.builder_config_path(workspace), config)
        deps.add_event(int(task["session_id"]), "koubo_storyboard.host_product_builder.output.deleted", {"task_id": task_id, "session_id": int(task["session_id"]), "kind": kind, "output": output_rel})
        return mask_model_fields_for_role(deps.ctx, role, SURFACE_KOUBO_HOST_PRODUCT_PROMPT, {"ok": True, "kind": kind, "section": section, "state": deps.serialize_host_product_builder(task, role, sc=deps)})

    @router.post("/api/koubo-storyboard/tasks/{task_id}/host-product-builder/prompt")
    async def host_product_builder_prompt(request: Request, task_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        return deps.generate_host_product_final_prompt(task_id, payload, request_role(request), sc=deps)

    @router.post("/api/koubo-storyboard/tasks/{task_id}/host-product-builder/prompt/events")
    async def host_product_builder_prompt_events(request: Request, task_id: int, payload: dict[str, Any]) -> StreamingResponse:
        role = request_role(request)
        task = deps.task_or_404(task_id)
        session_id = int(task["session_id"])
        workspace = deps.workspace_for(task)
        kind = deps.builder_kind_dir(deps.text(payload.get("kind")))
        request_payload = {
            "task_id": task_id,
            "session_id": session_id,
            "kind": kind,
            "reference_count": len(payload.get("reference_images") or []),
            "workspace_dir": str(workspace),
        }
        deps.add_event(session_id, "koubo_storyboard.host_product_builder.prompt.stream_requested", {**request_payload, "simple_prompt_preview": deps.text(payload.get("simple_prompt"))[:1000]})

        async def event_generator() -> Any:
            started = time.time()
            yield f"data: {json.dumps({'type': 'started', **request_payload}, ensure_ascii=True)}\n\n"
            worker = asyncio.create_task(asyncio.to_thread(deps.generate_host_product_final_prompt, task_id, payload, role, sc=deps))
            heartbeat_no = 0
            while not worker.done():
                await asyncio.sleep(2)
                if worker.done():
                    break
                heartbeat_no += 1
                heartbeat_payload = {**request_payload, "heartbeat": heartbeat_no, "elapsed_seconds": round(time.time() - started, 1)}
                deps.add_event(session_id, "koubo_storyboard.host_product_builder.prompt.heartbeat", heartbeat_payload)
                yield f"data: {json.dumps({'type': 'heartbeat', **heartbeat_payload}, ensure_ascii=True)}\n\n"
            try:
                result = await worker
            except HTTPException as exc:
                failed = {**request_payload, "status_code": exc.status_code, "detail": exc.detail, "elapsed_seconds": round(time.time() - started, 1)}
                deps.add_event(session_id, "koubo_storyboard.host_product_builder.prompt.stream_failed", failed)
                yield f"data: {json.dumps({'type': 'failed', **failed}, ensure_ascii=True)}\n\n"
                return
            except Exception as exc:
                failed = {**request_payload, "detail": str(exc), "elapsed_seconds": round(time.time() - started, 1)}
                deps.add_event(session_id, "koubo_storyboard.host_product_builder.prompt.stream_failed", failed)
                yield f"data: {json.dumps({'type': 'failed', **failed}, ensure_ascii=True)}\n\n"
                return
            yield f"data: {json.dumps({'type': 'completed', **result, 'elapsed_seconds': round(time.time() - started, 1)}, ensure_ascii=True)}\n\n"

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    @router.post("/api/koubo-storyboard/tasks/{task_id}/host-product-builder/generate/events")
    async def host_product_builder_generate_events(request: Request, task_id: int, payload: dict[str, Any]) -> StreamingResponse:
        role = request_role(request)
        task = deps.task_or_404(task_id)
        session_id = int(task["session_id"])
        workspace = deps.workspace_for(task)
        request_payload = {"task_id": task_id, "session_id": session_id, "kind": payload.get("kind"), "reference_count": len(payload.get("reference_images") or []), "workspace_dir": str(workspace)}
        deps.add_event(session_id, "koubo_storyboard.host_product_builder.image.requested", {**request_payload, "prompt_preview": deps.text(payload.get("prompt"))[:1000], "prompt_length": len(deps.text(payload.get("prompt")))})

        async def event_generator() -> Any:
            started = time.time()
            yield f"data: {json.dumps({'type': 'started', **request_payload}, ensure_ascii=True)}\n\n"
            worker = asyncio.create_task(asyncio.to_thread(deps.generate_host_product_image, task_id, payload, sc=deps))
            heartbeat_no = 0
            while not worker.done():
                await asyncio.sleep(2)
                if worker.done():
                    break
                heartbeat_no += 1
                heartbeat_payload = {**request_payload, "heartbeat": heartbeat_no, "elapsed_seconds": round(time.time() - started, 1)}
                deps.add_event(session_id, "koubo_storyboard.host_product_builder.image.heartbeat", heartbeat_payload)
                yield f"data: {json.dumps({'type': 'heartbeat', **heartbeat_payload}, ensure_ascii=True)}\n\n"
            try:
                result = mask_model_fields_for_role(deps.ctx, role, SURFACE_KOUBO_HOST_PRODUCT_PROMPT, await worker)
            except HTTPException as exc:
                failed = {**request_payload, "status_code": exc.status_code, "detail": exc.detail, "elapsed_seconds": round(time.time() - started, 1)}
                deps.add_event(session_id, "koubo_storyboard.host_product_builder.image.failed", failed)
                yield f"data: {json.dumps({'type': 'failed', **failed}, ensure_ascii=True)}\n\n"
                return
            except Exception as exc:
                failed = {**request_payload, "detail": str(exc), "elapsed_seconds": round(time.time() - started, 1)}
                deps.add_event(session_id, "koubo_storyboard.host_product_builder.image.failed", failed)
                yield f"data: {json.dumps({'type': 'failed', **failed}, ensure_ascii=True)}\n\n"
                return
            yield f"data: {json.dumps({'type': 'completed', **result, 'elapsed_seconds': round(time.time() - started, 1)}, ensure_ascii=True)}\n\n"

        return StreamingResponse(event_generator(), media_type="text/event-stream")
