from __future__ import annotations

import json
import logging
import os
import re
import uuid
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..context import AppContext, now_ms
from ..db.schema import media_library_clip_derivatives
from ..media_library_analysis import (
    CompositeAnalysisService,
    OpenCutDialogueService,
    OpenCutVisualService,
    VisualSemanticService,
    enrich_dialogue_progress_timing,
    enrich_visual_progress_timing,
    load_dialogue_result,
    load_visual_result,
)
from ..media_library_features import (
    MediaLibraryCapabilities,
    media_library_capabilities,
    media_library_feature_state,
    require_media_library_feature,
)
from ..media_library_upload.service import MediaLibraryUploadService, OPEN_CUT_SOURCE


LOGGER = logging.getLogger(__name__)
OPAQUE_CONTEXT_RE = re.compile(r"^[A-Za-z0-9._:-]{1,255}$")


class MediaLibraryPatch(BaseModel):
    display_name: str | None = None
    tags: list[str] | None = Field(default=None, max_length=1000)


class AnalysisRunRequest(BaseModel):
    force: bool = False
    allow_cloud_asr_data_transfer: bool = False


class VisualAnalysisRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    force: bool = False
    force_structure: bool = False
    force_semantic: bool = False
    allow_cloud_visual_data_transfer: bool = False


class CompositeAnalysisRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    force: bool = False


PUBLIC_ANALYSIS_ERRORS = {
    "analysis_upstream_changed": (
        "上游分析已更新，本结果需要重新运行。",
        "基于当前上游结果重新运行分析。",
    ),
    "analysis_worker_lost": (
        "后端服务重启，本次未完成的分析已中断。",
        "请重新创建分析运行。",
    ),
    "cloud_asr_data_transfer_not_authorized": (
        "尚未获得音频数据外发授权。",
        "明确授权本次运行后重试。",
    ),
    "cloud_visual_data_transfer_not_authorized": (
        "尚未获得每片段四张采样图的外发授权；不会上传整段源视频。",
        "明确授权本次运行或配置本地视觉模型后重试。",
    ),
    "video_has_no_audio": (
        "源视频没有音轨，无法进行对白识别。画面分析和视频剪辑仍可正常使用。",
        "无需调整 ASR 授权；请运行画面分析。若需对白检索，请上传含音轨版本。",
    ),
    "composite_model_configuration_unavailable": (
        "综合分析模型服务尚未配置或不可用。",
        "请联系管理员配置已批准的文本模型。",
    ),
    "composite_model_policy_invalid": (
        "综合分析模型策略尚未正确配置。",
        "请联系管理员补齐只读模型别名策略。",
    ),
    "model_input_capability_missing": (
        "当前模型别名不支持所需的图像输入。",
        "请联系管理员配置兼容模型。",
    ),
    "quota_exceeded": (
        "本次分析超过已配置的模型配额。",
        "稍后重试或联系管理员调整配额。",
    ),
    "visual_model_configuration_unavailable": (
        "视觉语义模型服务尚未配置或不可用。",
        "请联系管理员配置已批准的图像模型。",
    ),
    "visual_model_policy_invalid": (
        "视觉语义模型策略尚未正确配置。",
        "请联系管理员补齐只读别名与图像能力策略。",
    ),
}

PUBLIC_MEDIA_LIBRARY_PATCH_ERRORS = {
    "media_asset_name_empty": "素材名称不能为空。",
    "media_library_tags_too_many": "每个素材最多可保存 20 个标签。",
    "media_library_tag_too_long": "单个标签最多可包含 32 个字符。",
    "media_library_tag_empty": "标签不能为空或只包含空白。",
}

MEDIA_LIBRARY_PATCH_BODY_LIMIT_BYTES = 64 * 1024
MEDIA_LIBRARY_TAG_LIMIT = 20
MEDIA_LIBRARY_TAG_LENGTH_LIMIT = 32
MEDIA_LIBRARY_PATCH_PATH_RE = re.compile(r"^/api/media-library/[^/]+$")


class MediaLibraryPatchBodyLimitMiddleware:
    """Reject oversized metadata patches before JSON deserialization.

    The receive loop deliberately measures bytes instead of trusting
    Content-Length so chunked requests receive the same protection.
    """

    def __init__(
        self,
        app: ASGIApp,
        max_bytes: int = MEDIA_LIBRARY_PATCH_BODY_LIMIT_BYTES,
    ) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if (
            scope["type"] != "http"
            or str(scope.get("method") or "").upper() != "PATCH"
            or not MEDIA_LIBRARY_PATCH_PATH_RE.fullmatch(
                str(scope.get("path") or "")
            )
        ):
            await self.app(scope, receive, send)
            return

        headers = {
            key.decode("latin1").lower(): value.decode("latin1")
            for key, value in scope.get("headers", [])
        }
        try:
            content_length = int(headers.get("content-length") or "0")
        except ValueError:
            content_length = 0
        if content_length > self.max_bytes:
            await self._reject(scope, receive, send)
            return

        body = bytearray()
        more_body = True
        while more_body:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                continue
            body.extend(message.get("body", b""))
            if len(body) > self.max_bytes:
                await self._reject(scope, receive, send)
                return
            more_body = bool(message.get("more_body"))

        replayed = False

        async def replay_receive() -> Message:
            nonlocal replayed
            if replayed:
                return {
                    "type": "http.request",
                    "body": b"",
                    "more_body": False,
                }
            replayed = True
            return {
                "type": "http.request",
                "body": bytes(body),
                "more_body": False,
            }

        await self.app(scope, replay_receive, send)

    async def _reject(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        del receive

        async def disconnected_receive() -> Message:
            return {"type": "http.disconnect"}

        await JSONResponse(
            status_code=413,
            content={
                "detail": {
                    "code": "media_library_patch_body_too_large",
                    "message": "素材元数据请求体不能超过 64 KiB。",
                }
            },
        )(scope, disconnected_receive, send)


def _media_library_patch_error(code: str) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={"code": code, "message": PUBLIC_MEDIA_LIBRARY_PATCH_ERRORS[code]},
    )


def normalize_media_library_patch_tags(
    incoming_tags: list[str],
    existing_tags: list[Any],
) -> list[str]:
    """Normalize tags while allowing unchanged invalid legacy values once."""

    normalized_existing = [str(tag or "").strip() for tag in existing_tags]
    legacy_instances = Counter(normalized_existing)
    normalized_incoming = [tag.strip() for tag in incoming_tags]

    for tag in normalized_incoming:
        unchanged_legacy = legacy_instances[tag] > 0
        if unchanged_legacy:
            legacy_instances[tag] -= 1
        if not tag and not unchanged_legacy:
            raise _media_library_patch_error("media_library_tag_empty")
        if len(tag) > MEDIA_LIBRARY_TAG_LENGTH_LIMIT and not unchanged_legacy:
            raise _media_library_patch_error("media_library_tag_too_long")

    unique_tags = list(dict.fromkeys(normalized_incoming))
    if len(unique_tags) > MEDIA_LIBRARY_TAG_LIMIT and (
        len(normalized_existing) <= MEDIA_LIBRARY_TAG_LIMIT
        or len(unique_tags) > len(normalized_existing)
    ):
        raise _media_library_patch_error("media_library_tags_too_many")
    return unique_tags


def _dialogue_projection_error_code(error: Any) -> str | None:
    message = str(error or "").strip()
    lowered = message.lower()
    if "video_has_no_audio" in lowered or "source video has no audio track" in lowered or "源视频没有音轨" in message:
        return "video_has_no_audio"
    if "cloud_asr_data_transfer_not_authorized" in lowered or "云端 asr" in lowered and "授权" in message:
        return "cloud_asr_data_transfer_not_authorized"
    return None


def _dialogue_projection_error(error: Any) -> tuple[str | None, str | None]:
    code = _dialogue_projection_error_code(error)
    if code in PUBLIC_ANALYSIS_ERRORS:
        return code, PUBLIC_ANALYSIS_ERRORS[code][0]
    message = str(error or "").strip()
    return code, message or None


def _public_analysis_error(
    error: Any, *, scheme: str
) -> dict[str, Any] | None:
    if not isinstance(error, dict):
        return None
    raw_code = str(error.get("code") or "").strip()
    if raw_code in PUBLIC_ANALYSIS_ERRORS:
        code = raw_code
        user_message, suggested_action = PUBLIC_ANALYSIS_ERRORS[code]
    elif raw_code == "analysis_upstream_missing":
        code = raw_code
        user_message = "综合分析需要先完成全部上游分析。"
        suggested_action = "先完成对白、画面结构和画面语义分析。"
    else:
        code = (
            "visual_semantic_execution_failed"
            if scheme == "visual_semantic"
            else "composite_execution_failed"
            if scheme == "composite"
            else "analysis_execution_failed"
        )
        user_message = "分析执行失败，未发布本次结果。"
        suggested_action = "检查配置后重新运行；详细原因仅保留在内部审计。"
    payload: dict[str, Any] = {
        "code": code,
        "user_message": user_message,
        "suggested_action": suggested_action,
    }
    failed_step = str(error.get("failed_step") or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", failed_step):
        payload["failed_step"] = failed_step
    return payload


def _serialize(row: dict[str, Any]) -> dict[str, Any]:
    session_id = row.get("session_id")
    source_path = str(row.get("source_video_path") or "")
    encoded_path = "/".join(quote(part, safe="") for part in source_path.split("/")) if source_path else ""
    source_version = str(row.get("content_sha256") or "").strip()
    original_preview_url = f"/api/session-tasks/{session_id}/raw/{encoded_path}" if session_id and encoded_path else None
    if original_preview_url and source_version:
        original_preview_url = f"{original_preview_url}?v={quote(source_version[:32], safe='')}"
    preview_url = row.get("preview_url") or original_preview_url
    thumbnail_url = row.get("thumbnail_url") or (f"/api/session-tasks/{session_id}/thumbnail/{encoded_path}" if session_id and encoded_path else None)
    dialogue_error_code, _dialogue_error = _dialogue_projection_error(
        row.get("dialogue_error")
    )
    return {
        "asset_id": str(row.get("asset_id") or ""),
        "session_id": session_id,
        "display_name": str(row.get("display_name") or row.get("original_filename") or ""),
        "original_filename": str(row.get("original_filename") or ""),
        "source_video_path": source_path,
        "content_sha256": row.get("content_sha256"),
        "source_version": row.get("content_sha256"),
        "media_type": str(row.get("media_type") or "video"),
        "thumbnail_url": thumbnail_url,
        "preview_url": preview_url,
        "duration_ms": row.get("duration_ms"),
        "width": row.get("width"),
        "height": row.get("height"),
        "format": row.get("format"),
        "size_bytes": row.get("size_bytes"),
        "language": row.get("language"),
        "dialogue_summary": row.get("dialogue_summary"),
        "upload_status": str(row.get("upload_status") or "ready"),
        "analysis_status": str(row.get("analysis_status") or "not_analyzed"),
        "analysis_status_reason": dialogue_error_code,
        "visual_search_ready": bool(row.get("visual_search_ready")),
        "visual_search_reanalysis_required": bool(
            row.get("visual_search_reanalysis_required")
        ),
        "visual_search_state": str(
            row.get("visual_search_state") or "unavailable"
        ),
        "visual_search_fragment_count": max(
            0, int(row.get("visual_search_fragment_count") or 0)
        ),
        "visual_search_schema_version": row.get(
            "visual_search_schema_version"
        ),
        "subtitle_mode": str(row.get("subtitle_mode") or "unknown"),
        "analysis_summary": row.get("analysis_summary_json") or {},
        "tags": row.get("tags_json") or [],
        "archived": bool(row.get("archived")),
        "referenced_by_count": max(0, int(row.get("referenced_by_count") or 0)),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _detail_open_cut_payload(row: dict[str, Any], task: dict[str, Any] | None) -> dict[str, Any]:
    summary = row.get("analysis_summary_json") or {}
    task = task or {}
    def count(*keys: str) -> Any:
        for key in keys:
            if summary.get(key) is not None:
                return summary.get(key)
        return None
    dialogue_error_code, dialogue_error = _dialogue_projection_error(
        task.get("dialogue_error")
    )
    return {
        "task_id": task.get("id"),
        "session_id": row.get("session_id"),
        # The header is a business-analysis summary.  media_library_tasks.status
        # is only the OpenCut execution lifecycle (draft/running) and must not
        # overwrite ready/blocked/partial/stale asset state in the UI.
        "status": str(row.get("analysis_status") or "not_analyzed"),
        "dialogue_status": str(task.get("dialogue_status") or "not_analyzed"),
        "dialogue_current_run_id": task.get("dialogue_current_run_id"),
        "visual_status": str(task.get("visual_status") or "not_analyzed"),
        "visual_structure_status": str(task.get("visual_structure_status") or "not_analyzed"),
        "visual_structure_current_run_id": task.get("visual_structure_current_run_id"),
        "visual_semantic_status": str(task.get("visual_semantic_status") or "not_analyzed"),
        "visual_semantic_current_run_id": task.get("visual_semantic_current_run_id"),
        "composite_status": str(task.get("composite_status") or "not_analyzed"),
        "composite_current_run_id": task.get("composite_current_run_id"),
        "dialogue_tool_use_session_id": task.get("dialogue_tool_use_session_id"),
        "dialogue_error": dialogue_error,
        "dialogue_error_code": dialogue_error_code,
        "dialogue_progress": task.get("dialogue_progress_json") or {},
        "visual_tool_use_session_id": task.get("visual_tool_use_session_id"),
        "visual_error": task.get("visual_error"),
        "visual_progress": task.get("visual_progress_json") or {},
        "visual_semantic_tool_use_session_id": task.get("visual_semantic_tool_use_session_id"),
        "visual_semantic_error": task.get("visual_semantic_error"),
        "visual_semantic_progress": task.get("visual_semantic_progress_json") or {},
        "composite_tool_use_session_id": task.get("composite_tool_use_session_id"),
        "composite_error": task.get("composite_error"),
        "composite_progress": task.get("composite_progress_json") or {},
        "counts": {
            "dialogue": count("dialogue_fragment_count", "srt_unit_count"),
            "visual": count("visual_fragment_count", "visual_unit_count"),
            "composite": count("composite_fragment_count", "candidate_clip_count"),
        },
    }


def _serialize_analysis_run(
    run: dict[str, Any],
    published: dict[str, Any] | None = None,
) -> dict[str, Any]:
    started_at = run.get("started_at")
    finished_at = run.get("finished_at")
    published = published or {}
    return {
        "analysis_run_id": str(run.get("analysis_run_id") or ""),
        "scheme": str(run.get("scheme") or ""),
        "source_version": str(run.get("source_version") or ""),
        "status": str(run.get("status") or ""),
        "schema_version": run.get("schema_version"),
        "prompt_version": run.get("prompt_version"),
        "model_config_label": "server-default"
        if run.get("model_config_id")
        else None,
        "model_alias": "server-default"
        if run.get("model_config_id")
        else None,
        "model_version": run.get("model_config_id"),
        "sampling_strategy": published.get("sampling_strategy"),
        "result_hash": run.get("result_hash"),
        "progress": run.get("progress_json") or {},
        "error": _public_analysis_error(
            run.get("error_json"),
            scheme=str(run.get("scheme") or ""),
        ),
        "started_at": started_at,
        "finished_at": finished_at,
        "elapsed_ms": (
            max(0, int(finished_at) - int(started_at))
            if started_at is not None and finished_at is not None
            else None
        ),
    }


def _load_published_payload(
    *,
    run: dict[str, Any],
    session: dict[str, Any] | None,
) -> dict[str, Any]:
    if session is None:
        return {}
    relative = Path(str(run.get("result_index_path") or ""))
    if not relative.as_posix():
        return {}
    workspace = Path(str(session.get("workspace_dir") or "")).resolve()
    path = (workspace / relative).resolve()
    if not path.is_relative_to(workspace) or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_published_items(
    *,
    run: dict[str, Any],
    session: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    payload = _load_published_payload(run=run, session=session)
    if not isinstance(payload.get("items"), list):
        return []
    return [
        item for item in payload["items"] if isinstance(item, dict)
    ]


def _serialize_clip(row: dict[str, Any]) -> dict[str, Any]:
    session_id = int(row.get("source_session_id") or 0)
    output_path = str(row.get("output_path") or "")
    encoded_path = "/".join(
        quote(part, safe="") for part in output_path.split("/")
    )
    media_url = (
        f"/api/session-tasks/{session_id}/raw/{encoded_path}"
        if session_id and encoded_path
        else None
    )
    return {
        "clip_id": str(row.get("clip_id") or ""),
        "source_asset_id": str(row.get("source_asset_id") or ""),
        "source_version": str(row.get("source_version") or ""),
        "start_ms": int(row.get("source_start_ms") or 0),
        "end_ms": int(row.get("source_end_ms") or 0),
        "duration_ms": int(row.get("duration_ms") or 0),
        "source_scheme": row.get("source_scheme"),
        "source_fragment_id": row.get("source_fragment_id"),
        "source_analysis_run_id": row.get("source_analysis_run_id"),
        "source_search_id": row.get("source_search_id"),
        "source_dialogue_asset_key": row.get(
            "source_dialogue_asset_key"
        ),
        "display_name": str(row.get("display_name") or ""),
        "content_sha256": str(row.get("content_sha256") or ""),
        "size_bytes": int(row.get("size_bytes") or 0),
        "operation": str(
            row.get("operation") or "precise_reencode_v1"
        ),
        "search_eligible": False,
        "preview_url": media_url,
        "download_url": media_url,
        "created_at": int(row.get("created_at") or 0),
    }


def _context_token(value: str | None) -> str | None:
    normalized = str(value or "").strip()
    return (
        normalized
        if normalized and OPAQUE_CONTEXT_RE.fullmatch(normalized)
        else None
    )


def _storyboard_dialogue_match_count(
    plan: dict[str, Any], expected: str
) -> int:
    matches = 0
    shots = plan.get("shots")
    for shot in shots if isinstance(shots, list) else []:
        if not isinstance(shot, dict):
            continue
        scenes = shot.get("scenes")
        for scene in scenes if isinstance(scenes, list) else []:
            if not isinstance(scene, dict):
                continue
            dialogues = scene.get("dialogues")
            for dialogue in (
                dialogues if isinstance(dialogues, list) else []
            ):
                if (
                    isinstance(dialogue, dict)
                    and str(
                        dialogue.get("dialogue_asset_key") or ""
                    )
                    == expected
                ):
                    matches += 1
    return matches


def _storyboard_dialogue_valid(
    ctx: AppContext, task_id: int | None, dialogue_asset_key: str | None
) -> bool:
    if task_id is None or dialogue_asset_key is None:
        return False
    service = getattr(ctx, "media_library_import_service", None)
    repository = getattr(service, "repo", None)
    target = (
        repository.get_target_task(task_id)
        if repository is not None
        else None
    )
    if target is None:
        return False
    services = getattr(ctx, "koubo_storyboard_services", None)
    if services is None:
        return False
    target_workspace_raw = str(target.get("workspace_dir") or "")
    if not target_workspace_raw:
        return False
    try:
        task = services.task_or_404(task_id)
        target_workspace = Path(target_workspace_raw).resolve()
        task_workspace = Path(
            services.workspace_for(task)
        ).resolve()
        if task_workspace != target_workspace:
            return False
        plan, _meta = services.load_plan(task, sc=services)
    except Exception:
        LOGGER.warning(
            "media_library_storyboard_dialogue_validation_failed "
            "task_id=%s",
            task_id,
            exc_info=True,
        )
        return False
    return (
        isinstance(plan, dict)
        and _storyboard_dialogue_match_count(
            plan, dialogue_asset_key
        )
        == 1
    )


def _search_context_valid(
    ctx: AppContext,
    *,
    search_id: str | None,
    asset_id: str,
    target_task_id: int | None,
    dialogue_asset_key: str | None,
) -> bool:
    if search_id is None:
        return False
    service = getattr(ctx, "media_library_search_service", None)
    if service is None:
        return False
    try:
        run = service.get_run(search_id)
    except HTTPException:
        return False
    if str(run.get("status") or "") != "completed":
        return False
    run_task_id = run.get("target_task_id")
    if (
        target_task_id is not None
        and int(run_task_id or 0) != target_task_id
    ):
        return False
    run_dialogue = str(run.get("dialogue_asset_key") or "")
    if dialogue_asset_key is not None and run_dialogue != dialogue_asset_key:
        return False
    candidates = run.get("top_candidates_json")
    if not isinstance(candidates, list):
        return False
    return any(
        isinstance(item, dict)
        and str(
            item.get("source_asset_id")
            or item.get("asset_id")
            or item.get("provider_asset_id")
            or ""
        )
        == asset_id
        for item in candidates
    )


def build_media_library_router(ctx: AppContext) -> APIRouter:
    router = APIRouter(prefix="/api/media-library", tags=["media-library"])
    repo = ctx.media_library_repo

    def require_asset(asset_id: str) -> dict[str, Any]:
        row = repo.get(asset_id)
        if row is None:
            raise HTTPException(status_code=404, detail={"code": "media_asset_not_found", "message": "素材不存在或已删除。"})
        return row

    @router.get("")
    async def list_assets(
        q: str = "",
        analysis_status: str = "",
        subtitle_mode: str = "",
        duration_range: str = "",
        tag: str = "",
        updated_range: str = "",
        orientation: str = "",
        include_archived: bool = False,
        sort: str = "updated_desc",
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
    ) -> dict[str, Any]:
        ranges = {"today": 86_400_000, "7d": 7 * 86_400_000, "30d": 30 * 86_400_000}
        updated_from = now_ms() - ranges[updated_range] if updated_range in ranges else None
        rows, total, tags = repo.list(
            q=q.strip(),
            analysis_status=analysis_status.strip(),
            subtitle_mode=subtitle_mode.strip(),
            duration_range=duration_range.strip(),
            tag=tag.strip(),
            updated_from=updated_from,
            orientation=orientation.strip(),
            include_archived=include_archived,
            sort=sort.strip(),
            page=page,
            page_size=page_size,
        )
        return {"items": [_serialize(row) for row in rows], "total": total, "page": page, "page_size": page_size, "facets": {"tags": tags}}

    @router.get(
        "/capabilities",
        response_model=MediaLibraryCapabilities,
    )
    async def media_library_capability_status() -> MediaLibraryCapabilities:
        return media_library_capabilities()

    @router.get("/{asset_id}/editor")
    async def asset_editor(
        asset_id: str,
        start_ms: int | None = Query(default=None, ge=0),
        end_ms: int | None = Query(default=None, ge=1),
        target_task_id: int | None = Query(default=None, ge=1),
        dialogue_asset_key: str | None = Query(
            default=None, max_length=255
        ),
        search_id: str | None = Query(default=None, max_length=128),
        matched_fragment_id: str | None = Query(
            default=None, max_length=255
        ),
        return_to: str | None = Query(default=None, max_length=64),
    ) -> dict[str, Any]:
        require_media_library_feature("editor")
        row = require_asset(asset_id)
        source_version = str(row.get("content_sha256") or "")
        if (
            str(row.get("upload_status") or "") != "ready"
            or bool(row.get("archived"))
            or not re.fullmatch(r"[0-9a-f]{64}", source_version)
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "media_source_not_eligible",
                    "user_message": "原始素材当前不可打开剪辑器。",
                },
            )
        duration_ms = max(0, int(row.get("duration_ms") or 0))
        if duration_ms <= 0:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "media_source_duration_missing",
                    "user_message": "原始素材缺少可信时长，不能打开剪辑器。",
                },
            )
        session = (
            ctx.session_repo.get(int(row.get("session_id") or 0))
            if getattr(ctx, "session_repo", None) is not None
            else None
        )
        run_repo = getattr(ctx, "media_analysis_run_repo", None)
        runs: dict[str, Any] = {}
        payloads: dict[str, dict[str, Any]] = {}
        for scheme in (
            "dialogue",
            "visual_structure",
            "visual_semantic",
            "composite",
        ):
            run = (
                run_repo.current(asset_id, scheme)
                if run_repo is not None
                else None
            )
            if run is None:
                runs[scheme] = None
                payloads[scheme] = {}
                continue
            published = (
                _load_published_payload(run=run, session=session)
                if str(run.get("status") or "") in {"ready", "stale"}
                else {}
            )
            runs[scheme] = _serialize_analysis_run(run, published)
            payloads[scheme] = published
        visual_payload = (
            payloads["visual_semantic"]
            if isinstance(payloads["visual_semantic"].get("items"), list)
            else payloads["visual_structure"]
        )
        fragments = {
            "dialogue": [
                item
                for item in (payloads["dialogue"].get("items") or [])
                if isinstance(item, dict)
            ],
            "visual": [
                item
                for item in (visual_payload.get("items") or [])
                if isinstance(item, dict)
            ],
            "composite": [
                item
                for item in (payloads["composite"].get("items") or [])
                if isinstance(item, dict)
            ],
        }
        with ctx.engine.connect() as conn:
            clip_rows = [
                dict(item)
                for item in conn.execute(
                    select(media_library_clip_derivatives)
                    .where(
                        media_library_clip_derivatives.c.source_asset_id
                        == asset_id
                    )
                    .order_by(
                        media_library_clip_derivatives.c.created_at.desc(),
                        media_library_clip_derivatives.c.clip_id.asc(),
                    )
                )
                .mappings()
                .fetchall()
            ]
        import_service = getattr(
            ctx, "media_library_import_service", None
        )
        import_targets = (
            list(import_service.list_targets().get("items") or [])
            if import_service is not None
            else []
        )
        valid_target_ids = {
            int(item["task_id"])
            for item in import_targets
            if isinstance(item, dict) and item.get("task_id")
        }
        safe_dialogue_key = _context_token(dialogue_asset_key)
        safe_search_id = _context_token(search_id)
        safe_fragment_id = _context_token(matched_fragment_id)
        safe_return_to = (
            return_to
            if return_to
            in {"storyboard_dialogue", "media_library_detail"}
            else None
        )
        target_valid = (
            target_task_id is not None
            and target_task_id in valid_target_ids
        )
        dialogue_valid = (
            target_valid
            and _storyboard_dialogue_valid(
                ctx, target_task_id, safe_dialogue_key
            )
        )
        search_valid = _search_context_valid(
            ctx,
            search_id=safe_search_id,
            asset_id=asset_id,
            target_task_id=target_task_id if target_valid else None,
            dialogue_asset_key=(
                safe_dialogue_key if dialogue_valid else None
            ),
        )
        known_fragment_ids = {
            str(item.get("fragment_id") or "")
            for track in fragments.values()
            for item in track
            if item.get("fragment_id")
        }
        selection_start = min(max(0, int(start_ms or 0)), duration_ms)
        selection_end = min(
            duration_ms,
            max(
                selection_start,
                int(end_ms if end_ms is not None else duration_ms),
            ),
        )
        if selection_end <= selection_start:
            selection_start = 0
            selection_end = duration_ms
        response = {
            "item": _serialize(row),
            "source_version": source_version,
            "fragments": fragments,
            "runs": runs,
            "clips": [_serialize_clip(item) for item in clip_rows],
            "import_targets": import_targets,
            "navigation_context": {
                "start_ms": selection_start,
                "end_ms": selection_end,
                "target_task_id": target_task_id,
                "dialogue_asset_key": safe_dialogue_key,
                "search_id": safe_search_id if search_valid else None,
                "matched_fragment_id": (
                    safe_fragment_id
                    if safe_fragment_id in known_fragment_ids
                    else None
                ),
                "return_to": safe_return_to,
                "target_valid": target_valid,
                "dialogue_valid": bool(dialogue_valid),
                "search_valid": search_valid,
            },
        }
        fragment_count = sum(len(items) for items in fragments.values())
        payload_bytes = len(
            json.dumps(
                response,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        )
        LOGGER.info(
            "media_library_editor_capacity asset_id=%s "
            "media_library_editor_fragment_count=%d "
            "media_library_editor_payload_bytes=%d",
            asset_id,
            fragment_count,
            payload_bytes,
        )
        metric_sink = (
            getattr(ctx, "media_library_metric_sink", None)
            or getattr(ctx, "media_library_metric", None)
        )
        if callable(metric_sink):
            try:
                metric_sink(
                    "media_library_editor_fragment_count",
                    fragment_count,
                )
                metric_sink(
                    "media_library_editor_payload_bytes",
                    payload_bytes,
                )
            except Exception:
                LOGGER.warning(
                    "media_library_editor_metric_sink_failed "
                    "asset_id=%s fragment_count=%d payload_bytes=%d",
                    asset_id,
                    fragment_count,
                    payload_bytes,
                )
        try:
            warn_bytes = max(
                1,
                int(
                    os.getenv(
                        "OPENCREW_MEDIA_EDITOR_PAYLOAD_WARN_BYTES",
                        "2097152",
                    )
                ),
            )
        except ValueError:
            warn_bytes = 2_097_152
        if payload_bytes > warn_bytes:
            LOGGER.warning(
                "media_library_editor_payload_capacity_warning "
                "asset_id=%s fragment_count=%d payload_bytes=%d "
                "warn_bytes=%d",
                asset_id,
                fragment_count,
                payload_bytes,
                warn_bytes,
            )
        return response

    @router.get("/{asset_id}")
    async def asset_detail(asset_id: str) -> dict[str, Any]:
        row = require_asset(asset_id)
        task_repo = getattr(ctx, "media_library_task_repo", None)
        task = task_repo.get_by_asset(asset_id) if task_repo is not None else None
        item = _serialize(row)
        item["open_cut"] = _detail_open_cut_payload(row, task)
        dialogue_result: dict[str, Any] = {"items": []}
        visual_result: dict[str, Any] = {"items": []}
        dialogue_tool_use_session_id = str((task or {}).get("dialogue_tool_use_session_id") or "")
        visual_tool_use_session_id = str((task or {}).get("visual_tool_use_session_id") or "")
        session_id = int(row.get("session_id") or 0)
        session_repo = getattr(ctx, "session_repo", None)
        session = session_repo.get(session_id) if session_repo is not None and session_id else None
        if dialogue_tool_use_session_id and session is not None:
            item["open_cut"]["dialogue_progress"] = enrich_dialogue_progress_timing(
                workspace=Path(str(session.get("workspace_dir") or "")),
                tool_use_session_id=dialogue_tool_use_session_id,
                progress=item["open_cut"].get("dialogue_progress"),
            )
        if visual_tool_use_session_id and session is not None:
            item["open_cut"]["visual_progress"] = enrich_visual_progress_timing(
                workspace=Path(str(session.get("workspace_dir") or "")),
                tool_use_session_id=visual_tool_use_session_id,
                progress=item["open_cut"].get("visual_progress"),
            )
        if dialogue_tool_use_session_id and session is not None and str((task or {}).get("dialogue_status") or "") == "ready":
            dialogue_result = load_dialogue_result(
                workspace=Path(str(session.get("workspace_dir") or "")),
                session_id=session_id,
                tool_use_session_id=dialogue_tool_use_session_id,
                preview_url=str(item.get("preview_url") or ""),
            )
        if (
            visual_tool_use_session_id
            and session is not None
            and (
                str((task or {}).get("visual_structure_status") or "") == "ready"
                or str((task or {}).get("visual_status") or "") == "ready"
            )
        ):
            visual_result = load_visual_result(
                workspace=Path(str(session.get("workspace_dir") or "")),
                session_id=session_id,
                tool_use_session_id=visual_tool_use_session_id,
                preview_url=str(item.get("preview_url") or ""),
            )
        item["analysis_results"] = {
            "dialogue": dialogue_result,
            "visual": visual_result,
            "composite": {"items": []},
        }
        return {"item": item}

    @router.post("/{asset_id}/analyses/dialogue/run")
    async def run_dialogue_analysis(asset_id: str, payload: AnalysisRunRequest) -> dict[str, Any]:
        require_media_library_feature("analysis_runs")
        return OpenCutDialogueService(ctx).start(
            asset_id,
            force=payload.force,
            allow_cloud_asr_data_transfer=payload.allow_cloud_asr_data_transfer,
        )

    @router.post("/{asset_id}/analyses/visual/run")
    async def run_visual_analysis(
        asset_id: str, payload: VisualAnalysisRunRequest
    ) -> dict[str, Any]:
        require_media_library_feature("analysis_runs")
        require_asset(asset_id)
        run_repo = getattr(ctx, "media_analysis_run_repo", None)
        if run_repo is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "analysis_run_repository_unavailable",
                    "user_message": "分析运行服务尚未就绪。",
                },
            )
        operation_id = f"mlvo_{now_ms()}_{uuid.uuid4().hex[:10]}"
        force_structure = payload.force_structure or payload.force
        structure = run_repo.current(asset_id, "visual_structure")
        if (
            force_structure
            or structure is None
            or str(structure.get("status") or "") != "ready"
        ):
            semantic_state = media_library_feature_state(
                "visual_semantic"
            )
            return OpenCutVisualService(ctx).start(
                asset_id,
                force=force_structure,
                continue_semantic=(
                    semantic_state.configuration_valid
                    and semantic_state.enabled
                ),
                allow_cloud_visual_data_transfer=(
                    payload.allow_cloud_visual_data_transfer
                ),
                operation_id=operation_id,
            )
        require_media_library_feature("visual_semantic")
        return VisualSemanticService(ctx).start(
            asset_id,
            force=payload.force_semantic,
            allow_cloud_visual_data_transfer=(
                payload.allow_cloud_visual_data_transfer
            ),
            operation_id=operation_id,
        )

    @router.post("/{asset_id}/analyses/composite/run")
    async def run_composite_analysis(
        asset_id: str, payload: CompositeAnalysisRunRequest
    ) -> dict[str, Any]:
        require_media_library_feature("analysis_runs")
        require_media_library_feature("composite")
        return CompositeAnalysisService(ctx).start(
            asset_id, force=payload.force
        )

    @router.get("/{asset_id}/analyses/{scheme}/current")
    async def current_analysis(asset_id: str, scheme: str) -> dict[str, Any]:
        asset = require_asset(asset_id)
        if scheme not in {"dialogue", "visual", "composite"}:
            raise HTTPException(
                status_code=404,
                detail={"code": "analysis_scheme_not_found", "user_message": "分析类型不存在。"},
            )
        run_repo = getattr(ctx, "media_analysis_run_repo", None)
        if run_repo is None:
            return {"run": None, "items": []}
        candidates = (
            ("visual_semantic", "visual_structure")
            if scheme == "visual"
            else (scheme,)
        )
        run = next(
            (
                current
                for candidate in candidates
                if (current := run_repo.current(asset_id, candidate)) is not None
            ),
            None,
        )
        if run is None:
            return {"run": None, "items": []}
        session = (
            ctx.session_repo.get(int(asset.get("session_id") or 0))
            if getattr(ctx, "session_repo", None) is not None
            and asset.get("session_id")
            else None
        )
        published = _load_published_payload(run=run, session=session)
        return {
            "run": _serialize_analysis_run(run, published),
            "items": [
                item
                for item in (published.get("items") or [])
                if isinstance(item, dict)
            ],
        }

    @router.get("/{asset_id}/analyses/{scheme}/runs/{run_id}")
    async def known_analysis_run(
        asset_id: str, scheme: str, run_id: str
    ) -> dict[str, Any]:
        asset = require_asset(asset_id)
        run_repo = getattr(ctx, "media_analysis_run_repo", None)
        run = run_repo.get(run_id) if run_repo is not None else None
        allowed = (
            {"visual_structure", "visual_semantic"}
            if scheme == "visual"
            else {scheme}
        )
        if (
            run is None
            or str(run.get("asset_id") or "") != asset_id
            or str(run.get("scheme") or "") not in allowed
        ):
            raise HTTPException(
                status_code=404,
                detail={"code": "analysis_run_not_found", "user_message": "分析运行不存在。"},
            )
        session = (
            ctx.session_repo.get(int(asset.get("session_id") or 0))
            if getattr(ctx, "session_repo", None) is not None
            and asset.get("session_id")
            else None
        )
        published = (
            _load_published_payload(run=run, session=session)
            if str(run.get("status") or "") in {"ready", "stale"}
            else {}
        )
        return {
            "run": _serialize_analysis_run(run, published),
            "items": [
                item
                for item in (published.get("items") or [])
                if isinstance(item, dict)
            ],
        }

    @router.patch("/{asset_id}")
    async def update_asset(asset_id: str, payload: MediaLibraryPatch) -> dict[str, Any]:
        asset = require_asset(asset_id)
        display_name = None
        if payload.display_name is not None:
            display_name = payload.display_name.strip()
            if not display_name:
                raise _media_library_patch_error("media_asset_name_empty")
        tags = None
        if payload.tags is not None:
            tags = normalize_media_library_patch_tags(
                payload.tags,
                list(asset.get("tags_json") or []),
            )
        row = repo.update_metadata(asset_id, display_name=display_name, tags=tags, updated_at=now_ms())
        return {"item": _serialize(row or require_asset(asset_id))}

    @router.post("/{asset_id}/archive")
    async def archive_asset(asset_id: str) -> dict[str, Any]:
        require_asset(asset_id)
        import_service = getattr(ctx, "media_library_import_service", None)
        if import_service is not None and import_service.has_active_import(asset_id):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "media_asset_import_active",
                    "message": "素材正在导入 StoryBoard，暂时不能归档。",
                },
            )
        row = repo.set_archived(asset_id, True, now_ms())
        return {"item": _serialize(row or require_asset(asset_id))}

    @router.post("/{asset_id}/restore")
    async def restore_asset(asset_id: str) -> dict[str, Any]:
        require_asset(asset_id)
        row = repo.set_archived(asset_id, False, now_ms())
        return {"item": _serialize(row or require_asset(asset_id))}

    @router.delete("/{asset_id}")
    async def delete_asset(asset_id: str) -> dict[str, Any]:
        row = require_asset(asset_id)
        run_repo = getattr(ctx, "media_analysis_run_repo", None)
        active_runs = (
            [
                run
                for run in run_repo.active_runs()
                if str(run.get("asset_id") or "") == asset_id
            ]
            if run_repo is not None
            else []
        )
        if active_runs:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "analysis_run_active",
                    "message": "素材仍有运行中的分析，暂时不能删除。",
                },
            )
        clip_manager = getattr(ctx, "media_clip_job_manager", None)
        if (
            clip_manager is not None
            and callable(getattr(clip_manager, "has_active_job", None))
            and clip_manager.has_active_job(asset_id)
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "media_clip_job_active",
                    "message": "素材仍有运行中的剪切任务，暂时不能删除。",
                },
            )
        with ctx.engine.connect() as conn:
            derivative = conn.execute(
                select(media_library_clip_derivatives.c.clip_id)
                .where(
                    media_library_clip_derivatives.c.source_asset_id
                    == asset_id
                )
                .limit(1)
            ).first()
        if derivative is not None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "media_asset_has_derivatives",
                    "message": "素材仍有派生片段，请先处理派生片段。",
                },
            )
        import_service = getattr(ctx, "media_library_import_service", None)
        if import_service is not None and import_service.has_active_import(asset_id):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "media_asset_import_active",
                    "message": "素材正在导入 StoryBoard，暂时不能删除。",
                },
            )
        references = max(0, int(row.get("referenced_by_count") or 0))
        if references:
            raise HTTPException(status_code=409, detail={"code": "media_asset_in_use", "message": f"素材已被 {references} 个任务或工程引用，不能直接删除。", "referenced_by_count": references})
        session_id = int(row.get("session_id") or 0)
        session_row = ctx.session_repo.get(session_id) if session_id else None
        if session_row is not None and str(session_row.get("source") or "") == OPEN_CUT_SOURCE:
            return MediaLibraryUploadService(ctx).delete_ready_asset(asset_id)
        repo.delete(asset_id)
        return {"ok": True, "asset_id": asset_id}

    return router
