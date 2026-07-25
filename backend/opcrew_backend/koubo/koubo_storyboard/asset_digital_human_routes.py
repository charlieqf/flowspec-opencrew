from __future__ import annotations

import asyncio
import json
import urllib.parse
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from opcrew_backend.context import now_ms

from .constants import ASSET_AUDIOS_REL, ASSET_IMAGES_REL
from .io_utils import safe_workspace_rel
from .text_utils import redact_payload
from .usage_metering import record_storyboard_usage, stable_usage_request_id, voice_clone_usage_units
from .asset_digital_human_services import (
    DIGITAL_HUMAN_AUDIO_INPUTS_REL,
    _asset_payload,
    _upsert_asset_manifest,
    continue_video_agent_chat,
    create_photo_avatar,
    clone_heygen_voice,
    delete_heygen_avatar_look,
    generate_digital_human_video,
    list_heygen_avatar_looks,
    list_heygen_voices,
    read_digital_human_settings,
    save_digital_human_settings,
    start_video_agent_chat_plan,
    stop_video_agent_chat_plan,
    sync_video_agent_chat_plan,
)


def _text(value: Any, default: str = "") -> str:
    if value is None or value == "":
        value = default
    return str(value or "").strip()


async def _save_upload(workspace: Path, upload: UploadFile, target_rel_dir: str, fallback_name: str) -> tuple[Path, str]:
    filename = Path(_text(upload.filename, fallback_name)).name or fallback_name
    target_dir = workspace / target_rel_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{now_ms()}_{filename}"
    content = await upload.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    target.write_bytes(content)
    return target, target.relative_to(workspace).as_posix()


def _resolve_workspace_file(workspace: Path, rel_path: str, allowed_prefixes: tuple[str, ...]) -> Path:
    rel, target = safe_workspace_rel(workspace, rel_path)
    if not rel or not any(rel.startswith(f"{prefix}/") or rel == prefix for prefix in allowed_prefixes):
        raise HTTPException(status_code=400, detail=f"Unsupported asset path: {rel_path}")
    if not target.is_file() or workspace.resolve() not in target.parents:
        raise HTTPException(status_code=404, detail=f"Asset file was not found: {rel}")
    return target


def register_asset_digital_human_routes(router: APIRouter, deps: Any) -> None:
    task_or_404 = deps.task_or_404
    workspace_for = deps.workspace_for
    ctx = deps.ctx
    add_event = deps.add_event

    @router.get("/api/koubo-storyboard/tasks/{task_id}/asset-library/digital-human/avatars")
    async def digital_human_avatars(task_id: int, ownership: str = "", avatar_type: str = "", group_id: str = "", limit: int = 20, token: str = "") -> dict[str, Any]:
        task = task_or_404(task_id)
        return list_heygen_avatar_looks(ctx, {
            "ownership": ownership,
            "avatar_type": avatar_type,
            "group_id": group_id,
            "limit": max(1, min(int(limit or 20), 50)),
            "token": token,
        }, workspace_for(task))

    @router.post("/api/koubo-storyboard/tasks/{task_id}/asset-library/digital-human/avatars/photo")
    async def digital_human_photo_avatar(task_id: int, name: str = Form(""), description: str = Form(""), asset_path: str = Form(""), file: UploadFile | None = File(default=None)) -> dict[str, Any]:
        task = task_or_404(task_id)
        workspace = workspace_for(task)
        if file is not None and _text(file.filename):
            image_path, rel_source = await _save_upload(workspace, file, ASSET_IMAGES_REL, "avatar.png")
        else:
            image_path = _resolve_workspace_file(workspace, asset_path, (ASSET_IMAGES_REL,))
            rel_source, _ = safe_workspace_rel(workspace, asset_path)
        result = create_photo_avatar(ctx, workspace, _text(name, image_path.stem), image_path, rel_source, _text(description, "Photo avatar generated from Asset Library image."))
        add_event(int(task["session_id"]), "koubo_storyboard.asset_library.digital_human.avatar.created", redact_payload({"task_id": task_id, "record_path": result.get("record_path")}))
        return result

    @router.delete("/api/koubo-storyboard/tasks/{task_id}/asset-library/digital-human/avatars/{avatar_id:path}")
    async def digital_human_delete_avatar(task_id: int, avatar_id: str) -> dict[str, Any]:
        task = task_or_404(task_id)
        workspace = workspace_for(task)
        decoded_avatar_id = urllib.parse.unquote(_text(avatar_id))
        result = delete_heygen_avatar_look(ctx, workspace, decoded_avatar_id)
        add_event(int(task["session_id"]), "koubo_storyboard.asset_library.digital_human.avatar.deleted", redact_payload({"task_id": task_id, "avatar_id": decoded_avatar_id, "deleted_local_records": result.get("deleted_local_records")}))
        return result

    @router.get("/api/koubo-storyboard/tasks/{task_id}/asset-library/digital-human/voices")
    async def digital_human_voices(task_id: int, type: str = "private", engine: str = "", language: str = "Chinese", gender: str = "", limit: int = 20, token: str = "") -> dict[str, Any]:
        task_or_404(task_id)
        return list_heygen_voices(ctx, {
            "type": type or "private",
            "engine": engine,
            "language": language,
            "gender": gender,
            "limit": max(1, min(int(limit or 20), 100)),
            "token": token,
        })

    @router.post("/api/koubo-storyboard/tasks/{task_id}/asset-library/digital-human/voices/clone")
    async def digital_human_clone_voice(
        task_id: int,
        voice_name: str = Form(""),
        asset_path: str = Form(""),
        language: str = Form(""),
        remove_background_noise: bool = Form(True),
        file: UploadFile | None = File(default=None),
    ) -> dict[str, Any]:
        task = task_or_404(task_id)
        workspace = workspace_for(task)
        uploaded_asset: dict[str, Any] | None = None
        if file is not None and _text(file.filename):
            audio_path, rel_source = await _save_upload(workspace, file, ASSET_AUDIOS_REL, "voice.wav")
            uploaded_asset = _asset_payload(rel_source, "digital_human_voice_clone_upload", Path(rel_source).name)
            _upsert_asset_manifest(workspace, uploaded_asset)
        else:
            audio_path = _resolve_workspace_file(workspace, asset_path, (ASSET_AUDIOS_REL,))
            rel_source, _ = safe_workspace_rel(workspace, asset_path)
        result = clone_heygen_voice(ctx, workspace, _text(voice_name, audio_path.stem), audio_path, rel_source, language, bool(remove_background_noise))
        local_usage = record_storyboard_usage(
            ctx,
            task,
            request_id=stable_usage_request_id("heygen_voice_clone", task_id, rel_source, result.get("heygen_asset_id")),
            provider="heygen",
            model_id="heygen-voice-clone-v3",
            modality="voice_clone",
            step_id="koubo_storyboard.asset_library.digital_human.voice_clone",
            units=voice_clone_usage_units(audio_path),
        )
        result = {**result, "local_usage": local_usage, "local_usage_id": local_usage.get("local_usage_id", "")}
        add_event(int(task["session_id"]), "koubo_storyboard.asset_library.digital_human.voice.clone_created", redact_payload({"task_id": task_id, "record_path": result.get("record_path"), "asset": uploaded_asset, "local_usage": local_usage, "local_usage_id": local_usage.get("local_usage_id", "")}))
        return result

    @router.post("/api/koubo-storyboard/tasks/{task_id}/asset-library/digital-human/audio-assets")
    async def digital_human_audio_asset(task_id: int, asset_path: str = Form(""), file: UploadFile | None = File(default=None)) -> dict[str, Any]:
        task = task_or_404(task_id)
        workspace = workspace_for(task)
        uploaded_asset: dict[str, Any] | None = None
        if file is not None and _text(file.filename):
            _path, rel_source = await _save_upload(workspace, file, ASSET_AUDIOS_REL, "digital-human-audio.wav")
            uploaded_asset = _asset_payload(rel_source, "digital_human_audio_input", Path(rel_source).name)
            _upsert_asset_manifest(workspace, uploaded_asset)
        else:
            _resolve_workspace_file(workspace, asset_path, (ASSET_AUDIOS_REL,))
            rel_source, _ = safe_workspace_rel(workspace, asset_path)
        record_dir = workspace / DIGITAL_HUMAN_AUDIO_INPUTS_REL
        record_dir.mkdir(parents=True, exist_ok=True)
        asset = uploaded_asset or {"id": rel_source, "path": rel_source, "filename": Path(rel_source).name, "label": Path(rel_source).name, "kind": "audio", "asset_type": "Audio", "source": "digital_human_audio_input"}
        return {"ok": True, "asset": asset}

    @router.get("/api/koubo-storyboard/tasks/{task_id}/asset-library/digital-human/settings")
    async def get_digital_human_settings(task_id: int) -> dict[str, Any]:
        task = task_or_404(task_id)
        return {"ok": True, "settings": read_digital_human_settings(workspace_for(task))}

    @router.put("/api/koubo-storyboard/tasks/{task_id}/asset-library/digital-human/settings")
    async def put_digital_human_settings(task_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        task = task_or_404(task_id)
        settings = save_digital_human_settings(workspace_for(task), payload.get("settings") if isinstance(payload.get("settings"), dict) else payload)
        add_event(int(task["session_id"]), "koubo_storyboard.asset_library.digital_human.settings.saved", redact_payload({"task_id": task_id, "settings": settings}))
        return {"ok": True, "settings": settings}

    @router.get("/api/koubo-storyboard/tasks/{task_id}/asset-library/digital-human/agents/{provider_session_id}")
    async def digital_human_agent_session(task_id: int, provider_session_id: str, materialize: bool = True) -> dict[str, Any]:
        task = task_or_404(task_id)
        result = sync_video_agent_chat_plan(ctx, workspace_for(task), task, provider_session_id, materialize_completed=materialize)
        add_event(int(task["session_id"]), "koubo_storyboard.asset_library.digital_human.agent.synced", redact_payload({
            "task_id": task_id,
            "provider_session_id": provider_session_id,
            "agent_status": result.get("agent_status"),
            "resource_count": len(result.get("agent_resources") or []),
        }))
        return result

    @router.post("/api/koubo-storyboard/tasks/{task_id}/asset-library/digital-human/agents/{provider_session_id}/stop")
    async def digital_human_agent_stop(task_id: int, provider_session_id: str) -> dict[str, Any]:
        task = task_or_404(task_id)
        result = stop_video_agent_chat_plan(ctx, workspace_for(task), task, provider_session_id)
        add_event(int(task["session_id"]), "koubo_storyboard.asset_library.digital_human.agent.stopped", redact_payload({
            "task_id": task_id,
            "provider_session_id": provider_session_id,
            "agent_status": result.get("agent_status"),
            "resource_count": len(result.get("agent_resources") or []),
        }))
        return result

    @router.post("/api/koubo-storyboard/tasks/{task_id}/asset-library/digital-human/generate/events")
    async def digital_human_generate_events(task_id: int, payload: dict[str, Any]) -> StreamingResponse:
        task = task_or_404(task_id)
        workspace = workspace_for(task)
        source = payload if isinstance(payload, dict) else {}
        count = max(1, min(int(source.get("count") or 1), 2))
        request_payload = {
            "task_id": task_id,
            "session_id": int(task["session_id"]),
            "count": count,
            "model_name": _text(source.get("model_name"), "Avatar IV"),
            "engine_type": _text(source.get("engine_type"), "avatar_iv"),
            "generation_model": _text(source.get("generation_model") or source.get("engine_type"), "avatar_iv"),
            "provider_session_id": _text(source.get("provider_session_id")),
            "agent_confirm_generate": bool(source.get("agent_confirm_generate")),
            "generation_mode": _text(source.get("generation_mode"), "voice_script"),
            "aspect": _text(source.get("aspect"), "9:16"),
            "avatar_id": _text(source.get("avatar_id")),
            "avatar_type": _text(source.get("avatar_type")),
            "voice_id": _text(source.get("voice_id")),
            "audio_asset_path": _text(source.get("audio_asset_path")),
            "motion_prompt_length": len(_text(source.get("motion_prompt"))),
            "expressiveness": _text(source.get("expressiveness")),
            "prompt_length": len(_text(source.get("prompt"))),
        }
        add_event(int(task["session_id"]), "koubo_storyboard.asset_library.digital_human.requested", redact_payload({**request_payload, "prompt_preview": _text(source.get("prompt"))[:1000]}))

        async def event_generator() -> Any:
            started = now_ms()
            yield f"data: {json.dumps({'type': 'started', **request_payload}, ensure_ascii=True)}\n\n"

            def run_batch() -> dict[str, Any]:
                if _text(source.get("generation_model") or source.get("engine_type")) == "video_agent":
                    if _text(source.get("provider_session_id")):
                        return continue_video_agent_chat(ctx, workspace, task, {**source, "count": 1})
                    return start_video_agent_chat_plan(ctx, workspace, task, {**source, "count": 1})
                assets: list[dict[str, Any]] = []
                outputs: list[str] = []
                last: dict[str, Any] = {}
                for index in range(1, count + 1):
                    result = generate_digital_human_video(ctx, workspace, task, {**source, "count": count}, index=index)
                    last = result
                    asset = result.get("asset") if isinstance(result.get("asset"), dict) else {}
                    if asset:
                        assets.append(asset)
                    if _text(result.get("output")):
                        outputs.append(_text(result.get("output")))
                return {**last, "ok": True, "assets": assets, "asset": assets[0] if assets else {}, "outputs": outputs, "generated_count": len(assets)}

            worker = asyncio.create_task(asyncio.to_thread(run_batch))
            heartbeat = 0
            while not worker.done():
                await asyncio.sleep(2)
                if worker.done():
                    break
                heartbeat += 1
                elapsed = round((now_ms() - started) / 1000, 1)
                yield f"data: {json.dumps({'type': 'heartbeat', **request_payload, 'heartbeat': heartbeat, 'elapsed_seconds': elapsed}, ensure_ascii=True)}\n\n"
            try:
                result = await worker
            except HTTPException as exc:
                failed = {**request_payload, "status_code": exc.status_code, "detail": exc.detail, "elapsed_seconds": round((now_ms() - started) / 1000, 1)}
                add_event(int(task["session_id"]), "koubo_storyboard.asset_library.digital_human.failed", redact_payload(failed))
                yield f"data: {json.dumps({'type': 'failed', **failed}, ensure_ascii=True)}\n\n"
                return
            except Exception as exc:
                failed = {**request_payload, "detail": str(exc), "elapsed_seconds": round((now_ms() - started) / 1000, 1)}
                add_event(int(task["session_id"]), "koubo_storyboard.asset_library.digital_human.failed", redact_payload(failed))
                yield f"data: {json.dumps({'type': 'failed', **failed}, ensure_ascii=True)}\n\n"
                return
            add_event(int(task["session_id"]), "koubo_storyboard.asset_library.digital_human.completed", redact_payload({"task_id": task_id, "generated_count": result.get("generated_count", 0), "outputs": result.get("outputs", [])}))
            yield f"data: {json.dumps({'type': 'completed', **result, 'elapsed_seconds': round((now_ms() - started) / 1000, 1)}, ensure_ascii=True)}\n\n"

        return StreamingResponse(event_generator(), media_type="text/event-stream")
